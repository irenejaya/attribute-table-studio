# -*- coding: utf-8 -*-
"""
Pivot Table engine (pure Python, safe in a background task).

Records are grouped by the row fields (and optional column fields).  Every
measure keeps a small mergeable state per leaf cell; row, column and grand
totals are produced by *merging the states of the contributing records*, so
a total is always calculated from records, never by averaging the
displayed subgroup values (group A {10, 20} + group B {90} -> grand mean 40,
not 52.5).

NULL rules: mean / sum / min / max / median / stdev skip NULL and keep zero;
a group with no valid value gives None (shown as NULL); a missing cross-tab
combination gives None, except "Count records" which gives 0.  NULL group
values form their own group (key None), separate from the text "NULL".
"""

import math

from . import fields as F

AGGREGATES = (
    # id, label, numeric only
    ("count", "Count records", False),
    ("count_non_null", "Count non-NULL", False),
    ("count_distinct", "Count distinct", False),
    ("sum", "Sum", True),
    ("mean", "Mean", True),
    ("median", "Median", True),
    ("min", "Min", True),
    ("max", "Max", True),
    ("stdev", "Std dev (population)", True),
)
AGG_LABELS = {a: label for a, label, _n in AGGREGATES}
NUMERIC_AGGS = {a for a, _l, numeric in AGGREGATES if numeric}
COUNT_AGGS = ("count", "count_non_null", "count_distinct")

TOTAL = object()          # key of a total row / column
DEFAULT_CELL_LIMIT = 100000


class PivotError(Exception):
    """Invalid specification or a result above the cell limit."""


class PivotCancelled(Exception):
    pass


class Measure:
    def __init__(self, field, agg, label=""):
        self.field = field          # may be None for "count"
        self.agg = agg
        self.label = label

    def title(self):
        if self.label:
            return self.label
        if self.agg == "count" or not self.field:
            return "Records" if self.agg == "count" else AGG_LABELS[self.agg]
        short = {"count_non_null": "Count", "count_distinct": "Distinct",
                 "stdev": "Std dev"}.get(self.agg, AGG_LABELS[self.agg])
        return f"{short} {self.field}"

    def to_dict(self):
        return {"field": self.field, "agg": self.agg, "label": self.label}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("field"), d.get("agg", "count"), d.get("label", ""))


class PivotSpec:
    def __init__(self, rows=(), cols=(), measures=(), show_count=True,
                 show_total=True):
        self.rows = list(rows)
        self.cols = list(cols)
        self.measures = list(measures)
        self.show_count = show_count
        self.show_total = show_total

    def fields_needed(self):
        out = []
        for name in self.rows + self.cols + [m.field for m in self.measures]:
            if name and name not in out:
                out.append(name)
        return out

    def to_dict(self):
        return {"rows": self.rows, "cols": self.cols,
                "measures": [m.to_dict() for m in self.measures],
                "show_count": self.show_count, "show_total": self.show_total}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("rows", []), d.get("cols", []),
                   [Measure.from_dict(m) for m in d.get("measures", [])],
                   d.get("show_count", True), d.get("show_total", True))


# ============================================================================
# MEASURE STATE
# ============================================================================

class _State:
    """Mergeable accumulator for one measure in one cell."""
    __slots__ = ("records", "n", "total", "lo", "hi", "values", "distinct")

    def __init__(self, keep_values, keep_distinct):
        self.records = 0
        self.n = 0                 # non-NULL (valid) values
        self.total = 0.0
        self.lo = None
        self.hi = None
        self.values = [] if keep_values else None
        self.distinct = set() if keep_distinct else None

    def merge(self, other):
        self.records += other.records
        self.n += other.n
        self.total += other.total
        if other.lo is not None and (self.lo is None or other.lo < self.lo):
            self.lo = other.lo
        if other.hi is not None and (self.hi is None or other.hi > self.hi):
            self.hi = other.hi
        if self.values is not None:
            self.values.extend(other.values)
        if self.distinct is not None:
            self.distinct |= other.distinct


def _result(state, agg):
    if agg == "count":
        return state.records
    if agg == "count_non_null":
        return state.n
    if agg == "count_distinct":
        return len(state.distinct)
    if state.n == 0:
        return None
    if agg == "sum":
        return math.fsum(state.values) if state.values is not None \
            else state.total
    if agg == "mean":
        total = math.fsum(state.values) if state.values is not None \
            else state.total
        return total / state.n
    if agg == "min":
        return state.lo
    if agg == "max":
        return state.hi
    vals = sorted(state.values)
    n = len(vals)
    if agg == "median":
        mid = n // 2
        return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0
    if agg == "stdev":
        mean = math.fsum(vals) / n
        return math.sqrt(math.fsum((x - mean) ** 2 for x in vals) / n)
    raise PivotError(f"Unknown aggregation '{agg}'")


# ============================================================================
# RESULT
# ============================================================================

class PivotResult:
    """Grouped values; keys are tuples of raw group values (None = NULL)."""

    def __init__(self, spec, n_input):
        self.spec = spec
        self.n_input = n_input
        self.row_keys = []
        self.col_keys = []            # [()] when there are no column fields
        self._leaf = {}               # (rk, ck) -> ([states], [fids])
        self._cache = {}

    @property
    def has_cols(self):
        return bool(self.spec.cols)

    @property
    def group_count(self):
        return len(self.row_keys)

    def _states(self, rk, ck):
        key = (rk, ck)
        if key in self._cache:
            return self._cache[key]
        if rk is not TOTAL and ck is not TOTAL:
            hit = self._leaf.get(key)
            states = hit[0] if hit else None
        else:
            rows = self.row_keys if rk is TOTAL else [rk]
            cols = self.col_keys if ck is TOTAL else [ck]
            states = None
            for r in rows:
                for c in cols:
                    hit = self._leaf.get((r, c))
                    if not hit:
                        continue
                    if states is None:
                        states = [_State(s.values is not None,
                                         s.distinct is not None)
                                  for s in hit[0]]
                    for acc, s in zip(states, hit[0]):
                        acc.merge(s)
        self._cache[key] = states
        return states

    def value(self, rk, ck, measure_index):
        """Measure value for a cell (rk / ck may be TOTAL)."""
        states = self._states(rk, ck)
        agg = self.spec.measures[measure_index].agg
        if states is None:
            return 0 if agg == "count" else None
        return _result(states[measure_index], agg)

    def record_count(self, rk, ck):
        states = self._states(rk, ck)
        return states[0].records if states else 0

    def fids(self, rk, ck):
        """Feature ids that contributed to a cell (rk / ck may be TOTAL)."""
        rows = self.row_keys if rk is TOTAL else [rk]
        cols = self.col_keys if ck is TOTAL else [ck]
        out = []
        for r in rows:
            for c in cols:
                hit = self._leaf.get((r, c))
                if hit:
                    out.extend(hit[1])
        return out

    # ------------------------------------------------------------------
    def table(self):
        """Display layout shared by the result view and the exporters.

        Returns (header, rows, total_row) where header is a list of column
        dicts {"label", "group", "kind", "measure"} and every row is a list
        of cell dicts {"value", "rk", "ck", "measure"}; kind is "key",
        "count" or "value".  total_row is None when totals are off.
        """
        spec = self.spec
        header = [{"label": name, "group": "", "kind": "key", "measure": None}
                  for name in spec.rows]
        if spec.show_count:
            header.append({"label": "Records", "group": "", "kind": "count",
                           "measure": None})
        col_groups = list(self.col_keys)
        if self.has_cols and spec.show_total:
            col_groups.append(TOTAL)
        for ck in col_groups:
            group = ""
            if self.has_cols:
                group = "Total" if ck is TOTAL else key_label(ck)
            for mi, m in enumerate(spec.measures):
                header.append({"label": m.title(), "group": group,
                               "kind": "value", "measure": mi, "ck": ck})

        def make_row(rk):
            cells = []
            if rk is TOTAL:
                for i in range(len(spec.rows)):
                    cells.append({"value": "Grand total" if i == 0 else "",
                                  "rk": TOTAL, "ck": None, "measure": None,
                                  "total": True})
            else:
                for v in rk:
                    cells.append({"value": v, "rk": rk, "ck": None,
                                  "measure": None})
            if spec.show_count:
                cells.append({"value": self.record_count(rk, TOTAL), "rk": rk,
                              "ck": TOTAL, "measure": None})
            for col in header:
                if col["kind"] != "value":
                    continue
                ck = col["ck"]
                cells.append({"value": self.value(rk, ck, col["measure"]),
                              "rk": rk, "ck": ck, "measure": col["measure"]})
            return cells

        rows = [make_row(rk) for rk in self.row_keys]
        total_row = make_row(TOTAL) if spec.show_total else None
        return header, rows, total_row


def key_label(key):
    """Text of a group key tuple, NULL shown as 'NULL'."""
    return " · ".join(F.display_value(v) if v is not None else F.NULL_TEXT
                      for v in key)


# ============================================================================
# COMPUTE
# ============================================================================

def compute(records, field_names, spec, kinds=None,
            cell_limit=DEFAULT_CELL_LIMIT, is_cancelled=None, progress=None,
            total=None):
    """Build a :class:`PivotResult`.

    *records* yields (fid, values) where values align with *field_names*.
    *kinds* maps field name -> field kind (numeric measures check it).
    """
    if not spec.rows:
        raise PivotError("Add at least one row field")
    if not spec.measures:
        raise PivotError("Add at least one value")
    kinds = kinds or {}
    index = {name: i for i, name in enumerate(field_names)}
    for name in spec.fields_needed():
        if name not in index:
            raise PivotError(f"Field '{name}' is not in the input")
    for m in spec.measures:
        if m.agg not in AGG_LABELS:
            raise PivotError(f"Unknown aggregation '{m.agg}'")
        if m.agg != "count" and not m.field:
            raise PivotError(f"Choose a field for '{AGG_LABELS[m.agg]}'")
        if m.agg in NUMERIC_AGGS and kinds.get(m.field) not in (
                None, F.INTEGER, F.REAL):
            raise PivotError(
                f"{AGG_LABELS[m.agg]} needs a numeric field "
                f"('{m.field}' is {F.KIND_LABELS.get(kinds.get(m.field), '?')})")

    row_idx = [index[n] for n in spec.rows]
    col_idx = [index[n] for n in spec.cols]
    meas = []
    for m in spec.measures:
        meas.append((index[m.field] if m.field else None, m.agg,
                     m.agg in ("median", "stdev"),
                     m.agg == "count_distinct"))
    n_meas = len(meas)
    limit_cells = max(1, int(cell_limit))

    leaf = {}
    row_keys, col_keys = set(), set()
    gv = F.group_value
    isfinite = math.isfinite
    n = 0
    for fid, values in records:
        n += 1
        if is_cancelled is not None and n % 2000 == 0:
            if is_cancelled():
                raise PivotCancelled()
            if progress is not None and total:
                progress(min(99.0, 100.0 * n / total))
        rk = tuple(gv(values[i]) for i in row_idx)
        ck = tuple(gv(values[i]) for i in col_idx)
        cell = leaf.get((rk, ck))
        if cell is None:
            if rk not in row_keys:
                row_keys.add(rk)
            if ck not in col_keys:
                col_keys.add(ck)
            cells = len(row_keys) * max(len(col_keys), 1) * n_meas
            if cells > limit_cells:
                raise PivotError(
                    f"The result would exceed {limit_cells:,} cells. Use "
                    f"fewer or lower-cardinality fields, or filter the input.")
            cell = ([_State(keep_vals, keep_dist)
                     for _i, _a, keep_vals, keep_dist in meas], [])
            leaf[(rk, ck)] = cell
        states, fids = cell
        fids.append(fid)
        for k in range(n_meas):
            vi, agg, _kv, _kd = meas[k]
            st = states[k]
            st.records += 1
            if vi is None:
                continue
            v = values[vi]
            if v is None or F.is_null(v):
                continue
            if agg in COUNT_AGGS:
                st.n += 1
                if st.distinct is not None:
                    st.distinct.add(gv(v))
                continue
            if type(v) not in (int, float):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
            if isinstance(v, float) and not isfinite(v):
                continue
            st.n += 1
            st.total += v
            if st.lo is None or v < st.lo:
                st.lo = v
            if st.hi is None or v > st.hi:
                st.hi = v
            if st.values is not None:
                st.values.append(v)

    result = PivotResult(spec, n)
    result.row_keys = sorted(row_keys, key=lambda k: tuple(
        F.sort_key(v) for v in k))
    result.col_keys = sorted(col_keys, key=lambda k: tuple(
        F.sort_key(v) for v in k)) if spec.cols else [()]
    result._leaf = leaf
    return result
