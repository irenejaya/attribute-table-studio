# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Field kinds and value normalisation.

Every tool classifies a QGIS field into one *kind* (text, integer, real,
date, datetime, time, boolean or other) and reads attribute values through
the helpers below, so NULL handling is identical everywhere:

- NULL is missing.  QGIS 3 returns it as a null ``QVariant``, QGIS 4 as
  ``None``; both are recognised.
- Numeric zero and the empty string are real values, never NULL.
- Values are kept raw (no rounding, no alias / formatter text) until they
  are displayed.
"""

import datetime
import math

try:  # QVariant only exists when Qt is available (not in pure unit tests)
    from qgis.PyQt.QtCore import QVariant as _QVariant
except Exception:  # pragma: no cover - depends on the environment
    _QVariant = None

TEXT = "text"
INTEGER = "integer"
REAL = "real"
DATE = "date"
DATETIME = "datetime"
TIME = "time"
BOOL = "bool"
OTHER = "other"

NUMERIC = (INTEGER, REAL)
TEMPORAL = (DATE, DATETIME, TIME)

KIND_LABELS = {
    TEXT: "Text",
    INTEGER: "Integer",
    REAL: "Decimal",
    DATE: "Date",
    DATETIME: "Date and time",
    TIME: "Time",
    BOOL: "Boolean",
    OTHER: "Other",
}

# QMetaType ids (identical to the old QVariant::Type values)
_KIND_BY_TYPE_ID = {
    1: BOOL,
    2: INTEGER, 3: INTEGER, 4: INTEGER, 5: INTEGER,          # int, uint, (u)int64
    32: INTEGER, 33: INTEGER, 35: INTEGER, 36: INTEGER,      # long, short, ulong, ushort
    6: REAL, 38: REAL,                                       # double, float
    10: TEXT,
    14: DATE,
    15: TIME,
    16: DATETIME,
}

# Proleptic Gregorian ordinal of 1970-01-01 (datetime.date(1970, 1, 1).toordinal())
_EPOCH_ORDINAL = 719163
# QDate.toJulianDay() - date.toordinal() for the same calendar day
_JULIAN_OFFSET = 1721425


def type_id(qtype):
    """Integer id of a QVariant::Type / QMetaType::Type (Qt5 or Qt6 enum)."""
    try:
        return int(qtype)
    except (TypeError, ValueError):
        return int(getattr(qtype, "value", -1))


def field_kind(field):
    """Classify a ``QgsField`` (anything with type() / typeName())."""
    kind = _KIND_BY_TYPE_ID.get(type_id(field.type()))
    if kind is not None:
        return kind
    name = (field.typeName() or "").lower()
    if "bool" in name:
        return BOOL
    if any(k in name for k in ("real", "double", "float", "numeric", "decimal")):
        return REAL
    if "int" in name:
        return INTEGER
    if "datetime" in name or "timestamp" in name:
        return DATETIME
    if "date" in name:
        return DATE
    if "time" in name:
        return TIME
    if any(k in name for k in ("char", "text", "string")):
        return TEXT
    return OTHER


def is_null(value):
    """True for NULL in QGIS 3 (null QVariant) and QGIS 4 (None), and for
    a null QDate / QDateTime / QTime."""
    if value is None:
        return True
    if _QVariant is not None and isinstance(value, _QVariant):
        return value.isNull()
    if isinstance(value, (str, int, float)):
        return False
    probe = getattr(value, "isNull", None)
    if probe is not None:
        try:
            return bool(probe())
        except TypeError:
            return False
    return False


# ----------------------------------------------------------------------------
# Temporal values
# ----------------------------------------------------------------------------

def temporal_key(value):
    """Sortable number for a date / datetime / time value, or None.

    Dates and datetimes use days on the proleptic ordinal scale (datetimes
    as fractional days, compared as instants); times use seconds since
    midnight.
    """
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            secs = value.timestamp()
            return _EPOCH_ORDINAL + secs / 86400.0
        delta = value - datetime.datetime(1970, 1, 1)
        return _EPOCH_ORDINAL + delta.total_seconds() / 86400.0
    if isinstance(value, datetime.date):
        return float(value.toordinal())
    if isinstance(value, datetime.time):
        return (value.hour * 3600 + value.minute * 60 + value.second
                + value.microsecond / 1e6)
    if is_null(value):
        return None
    if hasattr(value, "toMSecsSinceEpoch"):        # QDateTime
        if not value.isValid():
            return None
        return _EPOCH_ORDINAL + value.toMSecsSinceEpoch() / 86400000.0
    if hasattr(value, "toJulianDay"):              # QDate
        if not value.isValid():
            return None
        return float(value.toJulianDay() - _JULIAN_OFFSET)
    if hasattr(value, "msecsSinceStartOfDay"):     # QTime
        if not value.isValid():
            return None
        return value.msecsSinceStartOfDay() / 1000.0
    return None


def key_to_text(key, kind):
    """Format a :func:`temporal_key` number back to text (bin labels)."""
    if key is None:
        return ""
    if kind == TIME:
        key = max(0.0, min(key, 86399.0))
        h, rem = divmod(int(round(key)), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    day = int(math.floor(key))
    try:
        d = datetime.date.fromordinal(max(day, 1))
    except (ValueError, OverflowError):
        return ""
    if kind == DATE:
        return d.isoformat()
    secs = int(round((key - day) * 86400))
    secs = min(secs, 86399)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{d.isoformat()} {h:02d}:{m:02d}:{s:02d}"


# ----------------------------------------------------------------------------
# Display
# ----------------------------------------------------------------------------

NULL_TEXT = "NULL"


def format_number(value, decimals=None):
    """Thousands-separated number; *decimals* None keeps up to 6 significant
    decimals and drops trailing zeros."""
    if value is None:
        return NULL_TEXT
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(value):
        return str(value)
    if decimals is not None:
        return f"{value:,.{decimals}f}"
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    text = f"{value:,.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def cell_text(field, value):
    """A non-NULL value as the attribute table shows it (QGIS's own
    formatting for the field); also the text Find matches."""
    try:
        return field.displayString(value)
    except Exception:
        return display_value(value)


def display_value(value):
    """Plain text for a raw attribute value (labels, frequency lists)."""
    if is_null(value):
        return NULL_TEXT
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return format_number(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat(sep=" ") if isinstance(
            value, datetime.datetime) else value.isoformat()
    to_string = getattr(value, "toString", None)
    if to_string is not None:
        if hasattr(value, "toMSecsSinceEpoch"):
            return to_string("yyyy-MM-dd hh:mm:ss")
        if hasattr(value, "toJulianDay"):
            return to_string("yyyy-MM-dd")
        if hasattr(value, "msecsSinceStartOfDay"):
            return to_string("hh:mm:ss")
        try:
            return to_string()
        except TypeError:
            pass
    return str(value)


def group_value(value):
    """Hashable grouping key for a raw value: NULL -> None, Qt temporal
    types -> Python date / datetime / time (raw values are never rounded)."""
    if is_null(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "toMSecsSinceEpoch"):
        try:
            return value.toPyDateTime()
        except Exception:
            return display_value(value)
    if hasattr(value, "toJulianDay"):
        try:
            return value.toPyDate()
        except Exception:
            return display_value(value)
    if hasattr(value, "msecsSinceStartOfDay"):
        try:
            return value.toPyTime()
        except Exception:
            return display_value(value)
    try:
        hash(value)
        return value
    except TypeError:
        return display_value(value)


_RANK = {bool: 0, int: 1, float: 1, str: 3}


def sort_key(value):
    """Total ordering across mixed raw values; NULL (None) sorts last."""
    if value is None:
        return (9, 0)
    rank = _RANK.get(type(value))
    if rank == 1 or rank == 0:
        return (1, float(value))
    if rank == 3:
        return (3, value.lower(), value)
    tkey = temporal_key(value)
    if tkey is not None:
        return (2, tkey)
    return (4, display_value(value))
