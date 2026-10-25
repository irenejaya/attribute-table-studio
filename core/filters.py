# -*- coding: utf-8 -*-
"""
Column filters -> QGIS expressions.

Each Attribute Explorer column (and each Pivot Table filter) holds one
:class:`ColumnFilter`.  :meth:`ColumnFilter.build` turns it into a QGIS
expression string; :func:`combine` joins active filters with AND.  A list
of values inside one filter is combined with OR (``IN``).

Expressions are written so the data provider can usually run them in its
own SQL (``=``, ``<``, ``IN``, ``ILIKE``, ``IS NULL``), which keeps
filtering fast on large GeoPackage and PostGIS layers.  Values containing
LIKE wildcards fall back to ``strpos`` so they always match literally.

Rules: NULL is only matched by "is NULL"; a value of 0 is not NULL;
comparisons never match NULL (SQL semantics).
"""

import datetime
import math

from . import fields as F

# operator id -> label, per kind
TEXT_OPS = (
    ("contains", "contains"),
    ("eq", "equals"),
    ("starts", "starts with"),
    ("in", "is one of"),
    ("null", "is NULL"),
    ("notnull", "is not NULL"),
)
NUMBER_OPS = (
    ("eq", "equal to"),
    ("ne", "not equal to"),
    ("lt", "less than"),
    ("le", "less than or equal to"),
    ("gt", "greater than"),
    ("ge", "greater than or equal to"),
    ("between", "between"),
    ("in", "is one of"),
    ("null", "is NULL"),
    ("notnull", "is not NULL"),
)
DATE_OPS = (
    ("eq", "on"),
    ("lt", "before"),
    ("le", "on or before"),
    ("gt", "after"),
    ("ge", "on or after"),
    ("between", "between"),
    ("null", "is NULL"),
    ("notnull", "is not NULL"),
)
TIME_OPS = (
    ("eq", "at"),
    ("lt", "before"),
    ("le", "at or before"),
    ("gt", "after"),
    ("ge", "at or after"),
    ("between", "between"),
    ("null", "is NULL"),
    ("notnull", "is not NULL"),
)
BOOL_OPS = (
    ("true", "is true"),
    ("false", "is false"),
    ("null", "is NULL"),
    ("notnull", "is not NULL"),
)
OTHER_OPS = (
    ("contains", "contains"),
    ("null", "is NULL"),
    ("notnull", "is not NULL"),
)

# short symbol shown on the operator button of a filter cell (and next to
# the words in the operator menu)
OP_SYMBOLS = {
    "contains": "≈", "starts": "a*", "eq": "=", "ne": "≠", "lt": "<",
    "le": "≤", "gt": ">", "ge": "≥", "between": "↔", "in": "∈",
    "null": "∅", "notnull": "≠∅", "true": "✓", "false": "✗",
}

NO_VALUE_OPS = ("null", "notnull", "true", "false")
COMPARISON_OPS = ("eq", "ne", "lt", "le", "gt", "ge")
_SQL_CMP = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}


def operators(kind):
    """((id, label), ...) available for a field kind."""
    if kind in F.NUMERIC:
        return NUMBER_OPS
    if kind in (F.DATE, F.DATETIME):
        return DATE_OPS
    if kind == F.TIME:
        return TIME_OPS
    if kind == F.BOOL:
        return BOOL_OPS
    if kind == F.TEXT:
        return TEXT_OPS
    return OTHER_OPS


def default_operator(kind):
    return operators(kind)[0][0]


def operator_label(kind, op):
    return dict(operators(kind)).get(op, op)


_HINTS = {"contains": "contains", "starts": "starts with", "in": "choose values",
          "null": "is NULL", "notnull": "is not NULL"}


def value_hints(kind, op):
    """Placeholder texts of the first and second value box: the expected
    format for dates and times, otherwise what the operator wants."""
    if op in ("null", "notnull", "in"):
        return _HINTS[op], ""
    if kind in (F.DATE, F.DATETIME):
        return "YYYY-MM-DD", "YYYY-MM-DD"
    if kind == F.TIME:
        return "HH:MM:SS", "HH:MM:SS"
    if op == "between":
        return "from", "to"
    return _HINTS.get(op, "value"), "to"


# ============================================================================
# QUOTING (same rules as QgsExpression.quotedString / quotedColumnRef)
# ============================================================================

def quote_column(name):
    return '"{}"'.format(name.replace('"', '""'))


def quote_string(text):
    text = (text.replace("'", "''").replace("\\", "\\\\")
            .replace("\n", "\\n").replace("\t", "\\t"))
    return "'{}'".format(text)


def number_literal(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    value = float(value)
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


# ============================================================================
# PARSING
# ============================================================================

class FilterError(ValueError):
    """Raised for input that cannot be turned into a filter."""


def parse_number(text):
    text = (text or "").strip().replace(" ", "")
    if not text:
        raise FilterError("Enter a number")
    for candidate in (text, text.replace(",", "")):
        try:
            value = float(candidate)
        except ValueError:
            continue
        if not math.isfinite(value):
            break
        return int(value) if value.is_integer() and "." not in candidate \
            and "e" not in candidate.lower() else value
    raise FilterError(f"'{text}' is not a number")


def parse_date(text):
    text = (text or "").strip()
    if not text:
        raise FilterError("Enter a date (YYYY-MM-DD)")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    raise FilterError(f"'{text}' is not a date (use YYYY-MM-DD)")


def parse_time(text):
    text = (text or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise FilterError(f"'{text}' is not a time (use HH:MM or HH:MM:SS)")


def _date_sql(d):
    return f"make_date({d.year}, {d.month}, {d.day})"


def _time_sql(t):
    return f"make_time({t.hour}, {t.minute}, {t.second})"


# ============================================================================
# FILTER
# ============================================================================

class ColumnFilter:
    """One filter on one field.

    *value* / *value2* hold the text typed by the user (value2 is the upper
    bound of "between"); *values* holds raw typed values for "is one of"
    (None stands for NULL).
    """

    def __init__(self, field, kind, op=None, value="", value2="", values=None):
        self.field = field
        self.kind = kind
        self.op = op or default_operator(kind)
        self.value = value
        self.value2 = value2
        self.values = list(values) if values else []

    def copy(self):
        return ColumnFilter(self.field, self.kind, self.op, self.value,
                            self.value2, self.values)

    def to_dict(self):
        return {"field": self.field, "kind": self.kind, "op": self.op,
                "value": self.value, "value2": self.value2,
                "values": [v for v in self.values
                           if v is None or isinstance(v, (str, int, float, bool))]}

    @classmethod
    def from_dict(cls, data):
        return cls(data["field"], data["kind"], data.get("op"),
                   data.get("value", ""), data.get("value2", ""),
                   data.get("values"))

    def is_active(self):
        if self.op in NO_VALUE_OPS:
            return True
        if self.op == "in":
            return bool(self.values)
        if self.op == "between":
            return bool(str(self.value).strip() or str(self.value2).strip())
        return bool(str(self.value).strip()) if self.kind != F.TEXT \
            else self.value != ""

    def describe(self):
        """Short human text, e.g. 'FL_1_PCT_MAX > 60'."""
        label = operator_label(self.kind, self.op)
        if self.kind in F.NUMERIC and self.op in COMPARISON_OPS:
            label = OP_SYMBOLS[self.op]          # 'FL_1_PCT_MAX > 60'
        if self.op in NO_VALUE_OPS:
            return f"{self.field} {label}"
        if self.op == "in":
            shown = ", ".join(F.display_value(v) for v in self.values[:3])
            more = f" +{len(self.values) - 3}" if len(self.values) > 3 else ""
            return f"{self.field} {label} ({shown}{more})"
        if self.op == "between":
            return f"{self.field} between {self.value} and {self.value2}"
        return f"{self.field} {label} {self.value}"

    # ------------------------------------------------------------------
    def build(self, case_sensitive=False):
        """QGIS expression for this filter, or None when inactive.

        *case_sensitive* makes "contains" / "starts with" match letter case
        exactly (View > Case-sensitive text filters).
        Raises :class:`FilterError` for invalid input.
        """
        if not self.is_active():
            return None
        col = quote_column(self.field)
        op = self.op
        if op == "null":
            return f"{col} IS NULL"
        if op == "notnull":
            return f"{col} IS NOT NULL"
        if op == "in":
            return self._build_in(col)
        if self.kind in F.NUMERIC:
            return self._build_number(col)
        if self.kind in (F.DATE, F.DATETIME):
            return self._build_date(col)
        if self.kind == F.TIME:
            return self._build_time(col)
        if self.kind == F.BOOL:
            return f"{col} = {'TRUE' if op == 'true' else 'FALSE'}"
        return self._build_text(col, case_sensitive)

    def _build_in(self, col):
        has_null = any(v is None for v in self.values)
        literals = []
        for v in self.values:
            if v is None:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                literals.append(number_literal(v))
            elif isinstance(v, bool):
                literals.append("TRUE" if v else "FALSE")
            else:
                literals.append(quote_string(str(v)))
        parts = []
        if literals:
            parts.append(f"{col} IN ({', '.join(literals)})")
        if has_null:
            parts.append(f"{col} IS NULL")
        if len(parts) == 2:
            return f"({parts[0]} OR {parts[1]})"
        return parts[0]

    def _build_number(self, col):
        if self.op == "between":
            lo, hi = self._bounds(parse_number)
            if lo is not None and hi is not None and lo > hi:
                lo, hi = hi, lo
            return self._range(col, lo, hi, number_literal)
        return f"{col} {_SQL_CMP[self.op]} {number_literal(parse_number(self.value))}"

    def _bounds(self, parse):
        lo = parse(self.value) if str(self.value).strip() else None
        hi = parse(self.value2) if str(self.value2).strip() else None
        return lo, hi

    @staticmethod
    def _range(col, lo, hi, literal, lhs=None):
        lhs = lhs or col
        parts = []
        if lo is not None:
            parts.append(f"{lhs} >= {literal(lo)}")
        if hi is not None:
            parts.append(f"{lhs} <= {literal(hi)}")
        return " AND ".join(parts) if len(parts) == 1 else f"({' AND '.join(parts)})"

    def _build_date(self, col):
        # compare whole calendar days in the field's own time zone
        lhs = col if self.kind == F.DATE else f"to_date({col})"
        if self.op == "between":
            lo, hi = self._bounds(parse_date)
            if lo is not None and hi is not None and lo > hi:
                lo, hi = hi, lo
            return self._range(col, lo, hi, _date_sql, lhs)
        return f"{lhs} {_SQL_CMP[self.op]} {_date_sql(parse_date(self.value))}"

    def _build_time(self, col):
        if self.op == "between":
            lo, hi = self._bounds(parse_time)
            if lo is not None and hi is not None and lo > hi:
                lo, hi = hi, lo
            return self._range(col, lo, hi, _time_sql)
        return f"{col} {_SQL_CMP[self.op]} {_time_sql(parse_time(self.value))}"

    def _build_text(self, col, case_sensitive=False):
        text = str(self.value)
        if self.op == "eq":
            return f"{col} = {quote_string(text)}"
        literal_safe = not any(ch in text for ch in "%_\\")
        target = col if self.kind == F.TEXT else f"to_string({col})"
        if case_sensitive:
            if self.op == "starts":
                if literal_safe:
                    return f"{target} LIKE {quote_string(text + '%')}"
                return f"left({target}, {len(text)}) = {quote_string(text)}"
            if literal_safe and self.kind == F.TEXT:
                return f"{target} LIKE {quote_string('%' + text + '%')}"
            return f"strpos({target}, {quote_string(text)}) > 0"
        if self.op == "starts":
            if literal_safe:
                return f"{target} ILIKE {quote_string(text + '%')}"
            return (f"left(lower({target}), {len(text)}) = "
                    f"{quote_string(text.lower())}")
        # contains (default)
        if literal_safe and self.kind == F.TEXT:
            return f"{target} ILIKE {quote_string('%' + text + '%')}"
        return f"strpos(lower({target}), {quote_string(text.lower())}) > 0"


def combine(filters, case_sensitive=False):
    """AND of every active filter.  Returns (expression or None, errors)
    where errors maps field name -> message for invalid filters (those are
    left out so the last valid result can stay on screen)."""
    parts, errors = [], {}
    for flt in filters:
        try:
            expr = flt.build(case_sensitive)
        except FilterError as exc:
            errors[flt.field] = str(exc)
            continue
        if expr:
            parts.append(expr)
    if not parts:
        return None, errors
    if len(parts) == 1:
        return parts[0], errors
    return " AND ".join(f"({p})" for p in parts), errors
