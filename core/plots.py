# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Data Plot: the numbers behind the plots (no Qt, unit tested).

:func:`read` streams the chosen records once, in a background job, and
keeps one compact column per field a plot uses: 8 bytes a value for
numbers and dates (NaN for NULL and unreadable values) and a small integer
code per record for text and yes / no values.  Every column has one entry
per record, in the order of *fids*, so the fields of a record line up.
Only :func:`read` touches QGIS; everything else works on those columns with
numpy, so changing a plot's options never reads the layer again.

Rules shared with the Explorer and Visualize Statistics: NULL is counted
apart, NaN / infinity and unreadable values are invalid, the median is the
exact one.  Count counts records; the other aggregations use the records
whose value is not NULL.

The plot types of round 1: :func:`scatter` (with :func:`fit_trend`),
:func:`line` and :func:`bar`.  Dates are grouped by :func:`time_bins` as
ArcGIS Pro does: an interval size and unit, bins aligned to the first or
last record or a reference time, and optionally the incomplete bins at the
ends trimmed.
"""

import datetime
import math
from array import array

from . import fields as F

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:              # pragma: no cover - QGIS ships numpy
    np = None
    HAS_NUMPY = False

NUMBER, DATE, CATEGORY = "number", "date", "category"
NULL_CODE = -1
SPLIT_SERIES = 8                 # Split by categories with their own colour
MAX_BARS = 1000                  # bars drawn; the rest are left out (noted)
MAX_TIME_BINS = 5000             # a smaller interval is enlarged (noted)
CHUNK = 1 << 20                  # numpy works in slices of this many records
OTHER = "Other"

# the plot types, in the order of ArcGIS Pro's Create Chart list; *built*
# types are offered in the New plot menu
TYPES = (
    ("bar", "Bar Chart"), ("line", "Line Chart"), ("combo", "Combo Chart"),
    ("pie", "Pie Chart"), ("histogram", "Histogram"), ("box", "Box Plot"),
    ("scatter", "Scatter Plot"), ("matrix", "Scatter Plot Matrix"),
    ("clock", "Data Clock"), ("calendar", "Calendar Heat Chart"),
    ("heat", "Matrix Heat Chart"), ("qq", "QQ Plot"),
)
TYPE_LABELS = dict(TYPES)
BUILT = ("bar", "line", "combo", "pie", "histogram", "box", "scatter")
# the round of dev-v5 that builds each type not built yet (shown in the menu)
COMING = {"matrix": 3, "qq": 3, "heat": 3, "calendar": 3, "clock": 3}

AGGREGATIONS = (("count", "Count"), ("sum", "Sum"), ("mean", "Mean"),
                ("median", "Median"), ("min", "Minimum"), ("max", "Maximum"))
AGG_LABELS = dict(AGGREGATIONS)
TRENDS = (("linear", "Linear"), ("exponential", "Exponential"),
          ("logarithmic", "Logarithmic"), ("power", "Power"),
          ("polynomial", "Polynomial"))
TREND_LABELS = dict(TRENDS)
UNITS = (("seconds", "Seconds"), ("minutes", "Minutes"), ("hours", "Hours"),
         ("days", "Days"), ("weeks", "Weeks"), ("months", "Months"),
         ("years", "Years"))
UNIT_LABELS = dict(UNITS)
_UNIT_DAYS = {"seconds": 1.0 / 86400, "minutes": 1.0 / 1440, "hours": 1.0 / 24,
              "days": 1.0, "weeks": 7.0}
ALIGNMENTS = (("start", "First record"), ("end", "Last record"),
              ("reference", "Reference time"))
EMPTY_BINS = (("zero", "Treat as zero"), ("connect", "Connect line"),
              ("break", "Break line"))
MULTI_SERIES = (("side", "Side by side"), ("stacked", "Stacked"),
                ("stacked100", "100% stacked"))
# the Sort box, worded as in Visualize Statistics and the Pivot Table chart
SORTS = (("category_asc", "A → Z"), ("category_desc", "Z → A"),
         ("value_asc", "Smallest value first"), ("value_desc", "Largest value first"))
SORT_ICONS = {"category_asc": "az", "category_desc": "za", "value_asc": "fewest",
              "value_desc": "count"}
_EPOCH = 719163                  # date(1970, 1, 1).toordinal()


def role(kind):
    """How a field kind is plotted: a number (numbers and times of day),
    a date (dates and date-times) or a category (text, yes / no, other)."""
    if kind in F.NUMERIC or kind == F.TIME:
        return NUMBER
    if kind in (F.DATE, F.DATETIME):
        return DATE
    return CATEGORY


# ============================================================================
# READING
# ============================================================================

class Column:
    """One field over the records read: *values* (numbers, times as seconds
    since midnight, dates as proleptic ordinal days with the time of day as
    a fraction; NaN for NULL and invalid values) or *codes* (index into
    *labels*, ``NULL_CODE`` for NULL)."""

    def __init__(self, name, kind):
        self.name = name
        self.kind = kind
        self.role = role(kind)
        self.values = array("d")
        self.codes = array("i")
        self.labels = []
        self.null = 0
        self.invalid = 0


class Table:
    """The records read for a plot: *fids* and one :class:`Column` per
    field, every column in the order of *fids*; *classes* holds the legend
    class of each record when a plot is coloured by the layer's symbology
    (index into *class_labels* / *class_colours*, -1 for none)."""

    def __init__(self):
        self.fids = array("q")
        self.columns = {}
        self.classes = None
        self.class_labels = []
        self.class_colours = []
        self._np = {}

    def __len__(self):
        return len(self.fids)

    def column(self, name):
        return self.columns.get(name)

    def np(self, name, part="values"):
        """numpy view (no copy) of a column's values or codes; *name* None
        is the feature ids, "\\x00classes" the legend classes."""
        key = (name, part)
        arr = self._np.get(key)
        if arr is None:
            if name is None:
                src = self.fids
            elif name == "\x00classes":
                src = self.classes
            else:
                src = getattr(self.columns[name], part)
            dtype = {"q": np.int64, "d": np.float64, "i": np.int32}[src.typecode]
            arr = np.frombuffer(src, dtype=dtype) if len(src) else np.zeros(0, dtype)
            self._np[key] = arr
        return arr


def _bool_key(v):
    """'true' / 'false' for a yes / no value, None for NULL, '' if unusable
    (the Explorer's rules)."""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None or F.is_null(v):
        return None
    if isinstance(v, (int, float)) and v in (0, 1):
        return "true" if v else "false"
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "t", "yes", "y", "1"):
            return "true"
        if low in ("false", "f", "no", "n", "0"):
            return "false"
    return ""


def _adder(col):
    """The function that keeps one raw value of *col*."""
    is_null = F.is_null
    nan = float("nan")
    if col.role == CATEGORY:
        index = {}
        labels = col.labels
        codes = col.codes.append
        boolean = col.kind == F.BOOL

        def add(v):
            if boolean:
                key = _bool_key(v)
                if key == "":
                    col.invalid += 1
                    key = None
            elif type(v) is str:
                key = v
            elif v is None or is_null(v):
                key = None
            else:
                key = F.display_value(v)
            if key is None:
                if not boolean or v is None or is_null(v):
                    col.null += 1
                codes(NULL_CODE)
                return
            code = index.get(key)
            if code is None:
                code = index[key] = len(labels)
                labels.append(key)
            codes(code)
        return add

    values = col.values.append
    isfinite = math.isfinite
    if col.role == NUMBER and col.kind != F.TIME:
        def add(v):
            t = type(v)
            if t is float:
                if isfinite(v):
                    values(v)
                else:
                    col.invalid += 1
                    values(nan)
                return
            if t is int:
                values(float(v))
                return
            if v is None or is_null(v):
                col.null += 1
                values(nan)
                return
            try:
                x = float(v)
            except (TypeError, ValueError):
                col.invalid += 1
                values(nan)
                return
            if isfinite(x):
                values(x)
            else:
                col.invalid += 1
                values(nan)
        return add

    tkey = F.temporal_key

    def add(v):
        if v is None or is_null(v):
            col.null += 1
            values(nan)
            return
        x = tkey(v)
        if x is None:
            col.invalid += 1
            values(nan)
        else:
            values(x)
    return add


def read(spec, job, wanted, classifier=None):
    """Read the records of *spec* (a :class:`.scan.ScanSpec`) for the
    fields *wanted* ((name, index, kind) each) into a :class:`Table`;
    runs inside a background job.  *classifier* (optional) gives each
    record its legend class: attributes ``attrs`` (indexes it reads),
    ``geometry``, ``labels``, ``colours`` and methods ``start(spec)``,
    ``code(feature)`` and ``stop()``."""
    from .scan import stream

    table = Table()
    adders = []
    attrs = set()
    for name, idx, kind in wanted:
        if name in table.columns:
            continue
        col = Column(name, kind)
        table.columns[name] = col
        adders.append((idx, _adder(col)))
        attrs.add(idx)
    geometry = False
    classes_append = None
    if classifier is not None:
        attrs |= set(classifier.attrs)
        geometry = classifier.geometry
        table.classes = array("i")
        classes_append = table.classes.append
        table.class_labels = list(classifier.labels)
        table.class_colours = list(classifier.colours)
        classifier.start(spec)
    fids_append = table.fids.append
    try:
        for feat in stream(spec, job, sorted(attrs), geometry=geometry):
            fids_append(feat.id())
            if len(adders) > 2:
                values = feat.attributes()
                for idx, add in adders:
                    add(values[idx])
            else:
                for idx, add in adders:
                    add(feat.attribute(idx))
            if classes_append is not None:
                classes_append(classifier.code(feat))
    finally:
        if classifier is not None:
            classifier.stop()
    job.check()
    return table


# ============================================================================
# HELPERS
# ============================================================================

def _codes_order(col):
    """Rank of each category code in A → Z order (case-insensitive)."""
    order = sorted(range(len(col.labels)),
                   key=lambda c: (str(col.labels[c]).lower(), str(col.labels[c])))
    rank = [0] * len(col.labels)
    for r, c in enumerate(order):
        rank[c] = r
    return rank


def aggregate(groups, k, values=None, how="count"):
    """Aggregate per group: *groups* gives each record its group (0 .. k-1,
    -1 for none); *values* the numbers (NaN for NULL), unused by Count.
    Returns (result, records): *result* a float array (NaN where a group has
    no value), *records* the records of each group."""
    groups = np.asarray(groups)
    ok = groups >= 0
    g = groups[ok].astype(np.int64)
    records = np.bincount(g, minlength=k)[:k] if k else np.zeros(0, np.int64)
    if how == "count" or values is None:
        return records.astype(np.float64), records
    v = np.asarray(values)[ok]
    valid = ~np.isnan(v)
    g, v = g[valid], v[valid]
    n = np.bincount(g, minlength=k)[:k]
    out = np.full(k, np.nan)
    has = n > 0
    if how in ("sum", "mean"):
        total = np.bincount(g, weights=v, minlength=k)[:k]
        out[has] = total[has] if how == "sum" else total[has] / n[has]
        return out, records
    order = np.lexsort((v, g))
    gs, vs = g[order], v[order]
    groups_k = np.arange(k)
    starts = np.searchsorted(gs, groups_k, side="left")
    ends = np.searchsorted(gs, groups_k, side="right")
    if how == "min":
        out[has] = vs[starts[has]]
    elif how == "max":
        out[has] = vs[ends[has] - 1]
    elif how == "median":
        lo = starts[has] + (n[has] - 1) // 2
        hi = starts[has] + n[has] // 2
        out[has] = (vs[lo] + vs[hi]) / 2.0
    else:
        raise ValueError(f"unknown aggregation {how}")
    return out, records


def selected_mask(fids, selected):
    """True for each of *fids* (numpy int64) that is in *selected* (a set
    or list of feature ids)."""
    if not len(fids) or not selected:
        return np.zeros(len(fids), dtype=bool)
    sel = np.fromiter(selected, dtype=np.int64, count=len(selected))
    return np.isin(fids, sel)


def nice_number(v, digits=4):
    """*v* with *digits* significant digits, for equations."""
    if v == 0 or not math.isfinite(v):
        return "0" if v == 0 else str(v)
    mag = int(math.floor(math.log10(abs(v))))
    if mag >= 6 or mag <= -5:
        return f"{v:.{digits - 1}e}"
    places = max(0, digits - 1 - mag)
    text = f"{v:,.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# ============================================================================
# TIME BINNING
# ============================================================================

def _add_months(day, months):
    """Proleptic ordinal *day* (with a time fraction) moved by *months*,
    the day of the month clamped to the month's length."""
    whole = int(math.floor(day))
    frac = day - whole
    d = datetime.date.fromordinal(max(1, whole))
    total = d.year * 12 + d.month - 1 + months
    year, month = divmod(total, 12)
    year = max(1, min(9999, year))
    month += 1
    if month == 12:
        last = 31
    else:
        last = (datetime.date(year, month + 1, 1) - datetime.date(year, month, 1)).days
    return datetime.date(year, month, min(d.day, last)).toordinal() + frac


def _step(edge, unit, size, sign=1):
    if unit in ("months", "years"):
        months = size * (12 if unit == "years" else 1)
        return _add_months(edge, sign * months)
    return edge + sign * size * _UNIT_DAYS[unit]


def auto_interval(lo, hi):
    """(unit, size): the interval giving at most about 120 bins for days
    *lo* .. *hi* (a span of one day or less: hours, minutes or seconds)."""
    span = (hi - lo) if lo is not None and hi is not None else 0.0
    for unit, size, days in (("seconds", 1, 1.0 / 86400), ("minutes", 1, 1.0 / 1440),
                             ("hours", 1, 1.0 / 24), ("days", 1, 1.0),
                             ("weeks", 1, 7.0), ("months", 1, 30.44),
                             ("months", 3, 91.3), ("years", 1, 365.25)):
        if span <= 120 * days:
            return unit, size
    years = max(1, int(math.ceil(span / 365.25 / 120)))
    return "years", years


class TimeBins:
    """Bins of dates: *starts* / *ends* (proleptic days), the bin of every
    value (*index*, -1 when it is in no bin: NULL or trimmed), whether the
    ends are inclusive (*closed* "left": [start, end), "right":
    (start, end]) and the interval used."""

    def __init__(self):
        self.starts = []
        self.ends = []
        self.index = None
        self.closed = "left"
        self.unit = "days"
        self.size = 1
        self.trimmed = 0             # records in trimmed bins
        self.note = ""

    def __len__(self):
        return len(self.starts)


def time_bins(days, unit=None, size=1, align="start", reference=None, trim=False,
              date_only=False):
    """Bin the dates *days* (numpy float, NaN for none).  *unit* None works
    the interval out from the data's time span.  *align*: "start" (bins
    from the first record), "end" (bins ending with the last record) or
    "reference" (bins from the *reference* day).  *trim* leaves out the
    bins at either end that the data's time span does not fill; with
    *date_only* the last day counts as a whole day."""
    out = TimeBins()
    days = np.asarray(days, dtype=np.float64)
    ok = ~np.isnan(days)
    out.index = np.full(len(days), -1, dtype=np.int32)
    if not ok.any():
        out.unit, out.size = unit or "days", size or 1
        return out
    lo, hi = float(days[ok].min()), float(days[ok].max())
    if unit is None:
        unit, size = auto_interval(lo, hi + (1 if date_only else 0))
    size = max(1, int(size or 1))
    # too many bins for the chosen interval: a larger one
    span = hi - lo + (1 if date_only else 0)
    approx = _UNIT_DAYS.get(unit, 30.44 if unit == "months" else 365.25) * size
    if span / approx > MAX_TIME_BINS:
        new = int(math.ceil(span / (approx / size) / MAX_TIME_BINS))
        out.note = (f"{size} {UNIT_LABELS[unit].lower()} would give more than "
                    f"{MAX_TIME_BINS:,} intervals: shown by {new} "
                    f"{UNIT_LABELS[unit].lower()}")
        size = new
    out.unit, out.size = unit, size

    if align == "end":
        edges = [hi]
        while edges[-1] >= lo:
            edges.append(_step(edges[-1], unit, size, -1))
        edges.reverse()
        out.closed = "right"
    else:
        origin = lo
        if align == "reference" and reference is not None:
            origin = float(reference)
            if origin > lo:
                while origin > lo:
                    origin = _step(origin, unit, size, -1)
            elif unit not in ("months", "years"):
                step = size * _UNIT_DAYS[unit]
                origin += math.floor((lo - origin) / step) * step
            else:
                while _step(origin, unit, size) <= lo:
                    origin = _step(origin, unit, size)
        edges = [origin]
        while edges[-1] <= hi:
            edges.append(_step(edges[-1], unit, size))
    edges_np = np.asarray(edges)
    side = "right" if out.closed == "left" else "left"
    idx = np.searchsorted(edges_np, days[ok], side=side) - 1
    k = len(edges) - 1
    keep = list(range(k))
    if trim:
        end = hi + (1.0 if date_only else 0.0)
        keep = [i for i in range(k) if edges[i] >= lo - 1e-9 and edges[i + 1] <= end + 1e-9]
    full = np.full(k, -1, dtype=np.int32)
    for new, old in enumerate(keep):
        full[old] = new
    idx = full[np.clip(idx, 0, k - 1)]
    out.index[ok] = idx
    out.trimmed = int(ok.sum() - (idx >= 0).sum())
    out.starts = [edges[i] for i in keep]
    out.ends = [edges[i + 1] for i in keep]
    return out


def bin_label(start, end, unit, kind=F.DATE):
    """Text of a time bin: its first day (or instant, below a day)."""
    if unit in ("seconds", "minutes", "hours"):
        return F.key_to_text(start, F.DATETIME)
    whole = math.floor(start) == start
    if kind == F.DATETIME and not whole:
        return F.key_to_text(start, F.DATETIME)
    return F.key_to_text(start, F.DATE)


# ============================================================================
# SERIES PLOTS: LINE AND BAR
# ============================================================================

class SeriesPlot:
    """What a line or bar chart draws: one position per bin / value /
    category (*labels*, and *starts* / *ends* for dates, *xs* for numbers),
    one value per series per position (*values*, NaN for none), the
    records of each position (*records*) and, for selecting, the position
    (*group*) and series (*series_of*, None when the series are fields) of
    every record read."""

    def __init__(self, kind):
        self.kind = kind
        self.x_role = CATEGORY
        self.x_kind = F.TEXT
        self.labels = []
        self.starts = None
        self.ends = None
        self.xs = None
        self.series = []
        self.values = []             # [series][position]
        self.records = []            # records per position
        self.series_records = None   # Split by: [series][position]
        self.group = None
        self.series_of = None
        self.split = False
        self.agg = "count"
        self.excluded = 0            # records with no position (NULL, trimmed)
        self.capped = 0              # records of the bars past MAX_BARS
        self.bins = None
        self.notes = []

    def __len__(self):
        return len(self.labels)


def _split_series(table, split, rows):
    """Series of a Split by field over the records *rows* (bool mask):
    the ``SPLIT_SERIES`` categories with the most records, then Other;
    NULL is a category of its own.  Returns (names, series code per
    record (-1 outside *rows*))."""
    col = table.column(split)
    if col.role == CATEGORY:
        codes = table.np(split, "codes").astype(np.int64)
        labels = list(col.labels)
        null_code = len(labels)
        codes = np.where(codes < 0, null_code, codes)
        labels.append(F.NULL_TEXT)
    else:
        vals = table.np(split)
        nan = np.isnan(vals)
        uniq, inv = np.unique(np.where(nan, np.inf, vals), return_inverse=True)
        codes = inv.astype(np.int64)
        labels = [F.NULL_TEXT if not math.isfinite(u) else (
            F.key_to_text(u, col.kind) if col.role == DATE or col.kind == F.TIME
            else F.plain_number(u)) for u in uniq]
    counts = np.bincount(codes[rows], minlength=len(labels))
    ranked = sorted((c for c in range(len(labels)) if counts[c]),
                    key=lambda c: (-counts[c], str(labels[c]).lower()))
    keep = ranked[:SPLIT_SERIES]
    rest = ranked[SPLIT_SERIES:]
    remap = np.full(len(labels), -1, dtype=np.int64)
    names = []
    for s, c in enumerate(keep):
        remap[c] = s
        names.append(str(labels[c]))
    if rest:
        remap[np.asarray(rest)] = len(names)
        names.append(OTHER)
    series = remap[codes]
    series[~rows] = -1
    return names, series


def _positions(table, x, opts, kind):
    """(SeriesPlot with its x positions, group per record) for the field
    *x*: dates in time bins, numbers one position per value, categories
    one per category."""
    out = SeriesPlot(kind)
    col = table.column(x)
    out.x_role, out.x_kind = col.role, col.kind
    n = len(table)
    if col.role == DATE:
        bins = time_bins(table.np(x), opts.get("unit"), opts.get("interval", 1),
                         opts.get("align", "start"), opts.get("reference"),
                         opts.get("trim", False), date_only=col.kind == F.DATE)
        out.bins = bins
        out.starts, out.ends = list(bins.starts), list(bins.ends)
        out.labels = [bin_label(s, e, bins.unit, col.kind)
                      for s, e in zip(bins.starts, bins.ends)]
        if bins.note:
            out.notes.append(bins.note)
        return out, bins.index.astype(np.int64)
    if col.role == NUMBER:
        vals = table.np(x)
        ok = ~np.isnan(vals)
        uniq, inv = np.unique(vals[ok], return_inverse=True)
        group = np.full(n, -1, dtype=np.int64)
        group[ok] = inv
        out.xs = uniq.tolist()
        fmt = (lambda v: F.key_to_text(v, F.TIME)) if col.kind == F.TIME else F.plain_number
        out.labels = [fmt(v) for v in out.xs]
        return out, group
    codes = table.np(x, "codes").astype(np.int64)
    out.labels = [str(v) for v in col.labels]
    group = np.where(codes >= 0, codes, -1)
    return out, group


def series_plot(table, kind, opts):
    """A line or bar chart.  *opts*: "x" (the field along the axis), "fields"
    (number fields, one series each; unused by Count), "agg", "split"
    (a field: one series per category, with the first number field),
    time binning ("unit", "interval", "align", "reference", "trim") and, for
    bars, "sort"."""
    x = opts["x"]
    agg = opts.get("agg", "count")
    out, group = _positions(table, x, opts, kind)
    out.agg = agg
    fields = [f for f in (opts.get("fields") or []) if table.column(f) is not None]
    split = opts.get("split")
    if split is not None and table.column(split) is None:
        split = None
    k = len(out.labels)
    rows = group >= 0

    out.excluded = int(len(table) - rows.sum())
    # bars: the order, and at most MAX_BARS of them
    if kind == "bar":
        order = _bar_order(table, x, out, group, k, fields, agg, opts.get("sort",
                                                                            "category_asc"))
        if len(order) > MAX_BARS:
            out.notes.append(f"{len(order):,} categories: the first {MAX_BARS:,} are "
                             "shown (in the chosen order)")
            order = order[:MAX_BARS]
        remap = np.full(k + 1, -1, dtype=np.int64)
        remap[np.asarray(order, dtype=np.int64)] = np.arange(len(order))
        group = np.where(group >= 0, remap[np.clip(group, 0, k)], -1)
        out.labels = [out.labels[i] for i in order]
        if out.starts is not None:
            out.starts = [out.starts[i] for i in order]
            out.ends = [out.ends[i] for i in order]
        if out.xs is not None:
            out.xs = [out.xs[i] for i in order]
        k = len(order)
        rows = group >= 0

    out.group = group.astype(np.int32)
    out.capped = int(len(table) - rows.sum()) - out.excluded
    _, records = aggregate(group, k)
    out.records = records.tolist()
    if split is not None:
        names, series = _split_series(table, split, rows)
        out.split = True
        out.series = names
        out.series_of = series.astype(np.int32)
        s_n = max(1, len(names))
        combined = np.where(rows & (series >= 0), group * s_n + series, -1)
        vals = table.np(fields[0]) if (fields and agg != "count") else None
        res, rec = aggregate(combined, k * s_n, vals, agg)
        res = res.reshape(k, s_n) if k else np.zeros((0, s_n))
        rec = rec.reshape(k, s_n) if k else np.zeros((0, s_n), np.int64)
        out.values = [res[:, s].tolist() for s in range(len(names))]
        out.series_records = [rec[:, s].tolist() for s in range(len(names))]
    elif agg == "count" or not fields:
        out.series = ["Count" if agg == "count" or not fields else fields[0]]
        out.values = [records.astype(np.float64).tolist()]
    else:
        out.series = list(fields)
        out.values = [aggregate(group, k, table.np(f), agg)[0].tolist() for f in fields]
    return out


def _bar_order(table, x, out, group, k, fields, agg, sort):
    """Positions of the bars in the order *sort* asks for."""
    col = table.column(x)
    if sort in ("value_asc", "value_desc"):
        if agg == "count" or not fields:
            score = aggregate(group, k)[0]
        else:
            parts = [aggregate(group, k, table.np(f), agg)[0] for f in fields]
            score = np.nansum(np.vstack(parts), axis=0) if parts else np.zeros(k)
        score = np.where(np.isnan(score), -np.inf if sort == "value_desc" else np.inf, score)
        if sort == "value_desc":
            return sorted(range(k), key=lambda i: (-score[i], i))
        return sorted(range(k), key=lambda i: (score[i], i))
    if col.role == CATEGORY:
        rank = _codes_order(col)
        order = sorted(range(k), key=rank.__getitem__)
    else:
        order = list(range(k))                       # dates and numbers ascending
    if sort == "category_desc":
        order.reverse()
    # categories without records (another field's NULL) are not bars
    present = np.bincount(group[group >= 0], minlength=k)
    return [i for i in order if present[i]]


def line(table, opts):
    """A line chart (see :func:`series_plot`).  Number positions come in
    ascending order; dates in time order."""
    return series_plot(table, "line", opts)


def bar(table, opts):
    return series_plot(table, "bar", opts)


def records_of(plot, positions, series=None):
    """Indexes (into the table) of the records of *positions* (and of the
    Split by *series*, when given)."""
    group = plot.group
    mask = np.isin(group, np.asarray(list(positions), dtype=np.int32))
    if series is not None and plot.series_of is not None:
        mask &= plot.series_of == series
    return np.nonzero(mask)[0]


def selected_per_position(plot, sel_mask):
    """Selected records of each position (and of each Split by series)."""
    k = len(plot)
    g = plot.group[sel_mask]
    g = g[g >= 0]
    per = np.bincount(g, minlength=k)[:k].tolist() if k else []
    per_series = None
    if plot.series_of is not None and k:
        s_n = max(1, len(plot.series))
        s = plot.series_of[sel_mask]
        gg = plot.group[sel_mask]
        ok = (gg >= 0) & (s >= 0)
        both = np.bincount(gg[ok] * s_n + s[ok], minlength=k * s_n)[:k * s_n]
        per_series = both.reshape(k, s_n).T.tolist()
    return per, per_series


def selected_sums(plot, table, sel_mask, field):
    """Sum of *field* over the selected records of each position (and
    series): the selected part of a Sum bar."""
    k = len(plot)
    vals = table.np(field)
    ok = sel_mask & (plot.group >= 0) & ~np.isnan(vals)
    if plot.series_of is not None:
        s_n = max(1, len(plot.series))
        ok &= plot.series_of >= 0
        idx = plot.group[ok].astype(np.int64) * s_n + plot.series_of[ok]
        sums = np.bincount(idx, weights=vals[ok], minlength=k * s_n)[:k * s_n]
        return sums.reshape(k, s_n).T.tolist()
    return [np.bincount(plot.group[ok], weights=vals[ok], minlength=k)[:k].tolist()]


# ============================================================================
# SCATTER PLOT
# ============================================================================

class ScatterPlot:
    """The points of a scatter plot: *x*, *y* (numpy float), *size* (the
    Size by value, or None), *classes* (legend class, or None) and *rows*
    (index of each point's record in the table); records left out have a
    NULL or invalid X or Y (*missing*) or a value of 0 or less on a log
    axis (*not_positive*)."""

    def __init__(self):
        self.x = self.y = self.size = self.classes = self.rows = None
        self.x_kind = self.y_kind = F.REAL
        self.missing = 0
        self.not_positive = 0
        self.size_missing = 0
        self.size_range = None
        self.trend = None

    def __len__(self):
        return 0 if self.x is None else len(self.x)


def scatter(table, opts, x_log=False, y_log=False):
    """Points of the fields *opts* "x" and "y"; "size" (optional) sizes
    them (a NULL size draws the smallest point); "trend" fits a trend line
    of "trend_type" ("order" for a polynomial)."""
    out = ScatterPlot()
    xc, yc = table.column(opts["x"]), table.column(opts["y"])
    out.x_kind, out.y_kind = xc.kind, yc.kind
    x, y = table.np(opts["x"]), table.np(opts["y"])
    ok = ~np.isnan(x) & ~np.isnan(y)
    out.missing = int(len(x) - ok.sum())
    positive = ok.copy()
    if x_log:
        positive &= x > 0
    if y_log:
        positive &= y > 0
    out.not_positive = int(ok.sum() - positive.sum())
    rows = np.nonzero(positive)[0]
    out.rows = rows
    out.x, out.y = x[rows], y[rows]
    size_field = opts.get("size")
    if size_field and table.column(size_field) is not None:
        s = table.np(size_field)[rows]
        good = ~np.isnan(s)
        out.size_missing = int(len(s) - good.sum())
        if good.any():
            out.size_range = (float(s[good].min()), float(s[good].max()))
        out.size = s
    if table.classes is not None:
        out.classes = table.np("\x00classes")[rows]
    if opts.get("trend") and len(rows) >= 2:
        out.trend = fit_trend(out.x, out.y, opts.get("trend_type", "linear"),
                              int(opts.get("order", 2)))
    return out


class Trend:
    """A fitted trend line: *kind*, *r2*, the records used and left out
    (values of 0 or less where a logarithm is needed), the equation's text
    and :meth:`at` for drawing."""

    def __init__(self, kind):
        self.kind = kind
        self.r2 = None
        self.used = 0
        self.left_out = 0
        self.coef = None             # polynomial coefficients in x (lowest first)
        self.a = self.b = None
        self.equation = ""
        self.ok = False

    def at(self, xs):
        xs = np.asarray(xs, dtype=np.float64)
        with np.errstate(all="ignore"):
            if self.kind in ("linear", "polynomial"):
                return np.polynomial.polynomial.polyval(xs, self.coef)
            if self.kind == "exponential":
                return self.a * np.exp(self.b * xs)
            if self.kind == "logarithmic":
                return np.where(xs > 0, self.a + self.b * np.log(np.where(xs > 0, xs, 1)), np.nan)
            if self.kind == "power":
                return np.where(xs > 0, self.a * np.power(np.where(xs > 0, xs, 1), self.b),
                                np.nan)
        return np.full(len(xs), np.nan)


def _poly_fit(x, y, order):
    """Least squares polynomial (coefficients lowest first, in x) and R²,
    from sums worked out in slices (no matrix of millions of rows)."""
    lo, hi = float(x.min()), float(x.max())
    mid = (lo + hi) / 2.0
    half = (hi - lo) / 2.0 or 1.0
    m = order + 1
    ata = np.zeros((m, m))
    aty = np.zeros(m)
    for i in range(0, len(x), CHUNK):
        t = (x[i:i + CHUNK] - mid) / half
        yy = y[i:i + CHUNK]
        powers = np.vander(t, 2 * order + 1, increasing=True)
        sums = powers.sum(axis=0)
        for r in range(m):
            ata[r] += sums[r:r + m]
        aty += (powers[:, :m] * yy[:, None]).sum(axis=0)
    try:
        coef_t = np.linalg.solve(ata, aty)
    except np.linalg.LinAlgError:
        coef_t = np.linalg.lstsq(ata, aty, rcond=None)[0]
    poly = np.polynomial.Polynomial(coef_t, domain=[lo, hi] if hi > lo else [lo - 1, lo + 1],
                                    window=[-1, 1])
    coef = poly.convert().coef
    mean = float(y.mean())
    ss_res = ss_tot = 0.0
    for i in range(0, len(x), CHUNK):
        yy = y[i:i + CHUNK]
        pred = poly(x[i:i + CHUNK])
        ss_res += float(((yy - pred) ** 2).sum())
        ss_tot += float(((yy - mean) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    return coef, r2


def fit_trend(x, y, kind="linear", order=2):
    """Least squares trend of *y* on *x*.  Exponential, logarithmic and
    power fits are made on the logarithms (as spreadsheet trend lines are),
    so their R² is that of the straight line on the logarithms; records
    whose values have no logarithm (0 or less) are left out of them."""
    out = Trend(kind)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    keep = np.ones(len(x), dtype=bool)
    if kind in ("logarithmic", "power"):
        keep &= x > 0
    if kind in ("exponential", "power"):
        keep &= y > 0
    out.left_out = int(len(x) - keep.sum())
    x, y = x[keep], y[keep]
    out.used = len(x)
    need = (int(order) + 1) if kind == "polynomial" else 2
    if len(x) < need or float(x.max()) == float(x.min()):
        return out
    if kind == "linear":
        out.coef, out.r2 = _poly_fit(x, y, 1)
        out.equation = _poly_text(out.coef)
    elif kind == "polynomial":
        order = max(2, min(6, int(order)))
        out.coef, out.r2 = _poly_fit(x, y, order)
        out.coef = _clean(out.coef, float(np.abs(x).max()))
        out.equation = _poly_text(out.coef)
    elif kind == "exponential":
        coef, out.r2 = _poly_fit(x, np.log(y), 1)
        out.a, out.b = math.exp(coef[0]), coef[1] if len(coef) > 1 else 0.0
        out.equation = f"y = {nice_number(out.a)}e^({nice_number(out.b)}x)"
    elif kind == "logarithmic":
        coef, out.r2 = _poly_fit(np.log(x), y, 1)
        out.a, out.b = coef[0], coef[1] if len(coef) > 1 else 0.0
        sign = "+" if out.a >= 0 else "−"
        out.equation = f"y = {nice_number(out.b)} ln(x) {sign} {nice_number(abs(out.a))}"
    elif kind == "power":
        coef, out.r2 = _poly_fit(np.log(x), np.log(y), 1)
        out.a, out.b = math.exp(coef[0]), coef[1] if len(coef) > 1 else 0.0
        out.equation = f"y = {nice_number(out.a)}x^{nice_number(out.b)}"
    out.ok = True
    return out


def _clean(coef, reach):
    """Coefficients whose term is rounding noise (next to the largest term
    over the data's range) set to 0, so the equation shows no 1e-16 x²."""
    coef = np.array(coef, dtype=np.float64)
    reach = max(reach, 1e-300)
    terms = np.abs(coef) * reach ** np.arange(len(coef))
    top = float(terms.max()) if len(terms) else 0.0
    coef[terms < top * 1e-10] = 0.0
    return coef


_SUPERSCRIPTS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _poly_text(coef):
    terms = []
    for power in range(len(coef) - 1, -1, -1):
        c = float(coef[power])
        if c == 0 and len(coef) > 1:
            continue
        mag = nice_number(abs(c))
        if power == 0:
            body = mag
        elif power == 1:
            body = f"{mag}x"
        else:
            body = f"{mag}x{str(power).translate(_SUPERSCRIPTS)}"
        if not terms:
            terms.append(("−" if c < 0 else "") + body)
        else:
            terms.append(("− " if c < 0 else "+ ") + body)
    return "y = " + (" ".join(terms) if terms else "0")


# ============================================================================
# DENSITY
# ============================================================================

def density(px, py, width, height, classes=None, n_classes=0):
    """Records per cell of a *width* x *height* grid for the cell
    coordinates *px*, *py* (numpy int, inside the grid).  With *classes*,
    also the class with the most records in each cell (-1 for an empty
    cell).  Returns (counts (height x width), dominant or None)."""
    cells = width * height
    idx = py.astype(np.int64) * width + px.astype(np.int64)
    counts = np.bincount(idx, minlength=cells)[:cells].reshape(height, width)
    dominant = None
    if classes is not None and n_classes > 0:
        ok = classes >= 0
        both = idx[ok] * n_classes + classes[ok].astype(np.int64)
        per = np.bincount(both, minlength=cells * n_classes)[:cells * n_classes]
        per = per.reshape(cells, n_classes)
        dominant = np.where(per.sum(axis=1) > 0, per.argmax(axis=1), -1).reshape(height, width)
    return counts, dominant


# ============================================================================
# COMBO CHART
# ============================================================================

def _apply_order(out, group, order):
    """Keep the positions *order* (in that order): their group numbers
    follow; records of the other positions get no position."""
    k = len(out.labels)
    remap = np.full(k + 1, -1, dtype=np.int64)
    if len(order):
        remap[np.asarray(order, dtype=np.int64)] = np.arange(len(order))
    group = np.where(group >= 0, remap[np.clip(group, 0, k)], -1)
    out.labels = [out.labels[i] for i in order]
    if out.starts is not None:
        out.starts = [out.starts[i] for i in order]
        out.ends = [out.ends[i] for i in order]
    if out.xs is not None:
        out.xs = [out.xs[i] for i in order]
    return group


def series_name(field, agg):
    """A series' name: Count, or the aggregation and the field."""
    if agg == "count" or not field:
        return "Count"
    return f"{AGG_LABELS.get(agg, agg)} of {field}"


def combo(table, opts):
    """A combo chart: bar and line series over one X axis.  *opts*: "x",
    "series" (each a dict: "field" (None: Count), "agg", "kind" "bar" or
    "line", "axis" "left" or "right"), time binning as for a line chart and
    "empty" ("zero" or "null").  Categories come A → Z; numbers and dates
    in ascending order."""
    x = opts["x"]
    out, group = _positions(table, x, opts, "combo")
    k = len(out.labels)
    present = np.bincount(group[group >= 0], minlength=k)[:k] if k else np.zeros(0)
    if out.x_role == CATEGORY:
        rank = _codes_order(table.column(x))
        order = sorted((i for i in range(k) if present[i]), key=rank.__getitem__)
        out.excluded = int((group < 0).sum())
        group = _apply_order(out, group, order)
        k = len(order)
    else:
        out.excluded = int((group < 0).sum())
    out.group = group.astype(np.int32)
    _, records = aggregate(group, k)
    out.records = records.tolist()
    out.agg = "combo"
    out.kinds, out.axes = [], []
    zero = opts.get("empty", "zero") == "zero"
    for spec in opts.get("series") or [{"field": None, "agg": "count"}]:
        field = spec.get("field")
        agg = spec.get("agg", "count") if field else "count"
        if field and table.column(field) is None:
            continue
        vals = aggregate(group, k, table.np(field) if field else None, agg)[0]
        if zero:                             # an interval without records is 0
            vals = np.where(records == 0, 0.0, vals)
        out.series.append(series_name(field, agg))
        out.values.append(vals.tolist())
        out.kinds.append(spec.get("kind", "bar"))
        out.axes.append(spec.get("axis", "left"))
    return out


# ============================================================================
# PIE CHART
# ============================================================================

class PiePlot:
    """The slices of a pie: *labels*, *values* (largest first, Other
    last), the records of each slice and, for selecting, the slice of
    every record (*group*; None when the slices are fields, *fields*)."""

    def __init__(self):
        self.labels = []
        self.values = []
        self.records = []
        self.group = None
        self.fields = None           # slices = number fields
        self.members = []            # categories / fields in each slice
        self.other = None            # index of Other
        self.total = 0.0
        self.excluded = 0            # records without a category
        self.left_out = []           # slices of 0 or less, or without a value
        self.agg = "count"
        self.n_codes = 0

    def __len__(self):
        return len(self.labels)


def pie(table, opts):
    """A pie chart.  *opts* "mode": "category" (the slices are the
    categories of "category": records counted, or "field" summarised by
    "agg") or "fields" (one slice per number field of "fields", each
    summarised by "agg").  "threshold": slices under this percentage of the
    whole are put together as Other (exact: worked out from their
    records).  A slice must be above 0 to be drawn."""
    out = PiePlot()
    agg = opts.get("agg", "sum")
    threshold = float(opts.get("threshold", 0) or 0)
    if opts.get("mode") == "fields":
        names = [f for f in (opts.get("fields") or []) if table.column(f) is not None]
        out.fields = names
        out.agg = agg
        n = len(table)
        entries = []
        for f in names:
            v = table.np(f)
            res, rec = aggregate(np.zeros(n, dtype=np.int64), 1, v, agg)
            entries.append((f, float(res[0]), int((~np.isnan(v)).sum()), [f]))
    else:
        cat = opts.get("category")
        col = table.column(cat)
        field = opts.get("field")
        if field and table.column(field) is None:
            field = None
        out.agg = agg if field else "count"
        if col.role == CATEGORY:
            codes = table.np(cat, "codes").astype(np.int64)
            labels = [str(v) for v in col.labels]
            group = np.where(codes >= 0, codes, -1)
        else:
            vals = table.np(cat)
            ok = ~np.isnan(vals)
            uniq, inv = np.unique(vals[ok], return_inverse=True)
            group = np.full(len(vals), -1, dtype=np.int64)
            group[ok] = inv
            fmt = (lambda v: F.key_to_text(v, col.kind)) if col.role == DATE else F.plain_number
            labels = [fmt(v) for v in uniq.tolist()]
        k = len(labels)
        out.n_codes = k
        res, rec = aggregate(group, k, table.np(field) if field else None, out.agg)
        out.excluded = int((group < 0).sum())
        entries = [(labels[i], float(res[i]), int(rec[i]), [i]) for i in range(k) if rec[i]]
        out.group = group
    good = [e for e in entries if e[1] == e[1] and e[1] > 0]
    out.left_out = [e[0] for e in entries if not (e[1] == e[1] and e[1] > 0)]
    good.sort(key=lambda e: (-e[1], str(e[0]).lower()))
    total = sum(e[1] for e in good)
    keep = [e for e in good if total and 100.0 * e[1] / total >= threshold] \
        if threshold > 0 else good
    rest = [e for e in good if e not in keep]
    for label, value, n, members in keep:
        out.labels.append(label)
        out.values.append(value)
        out.records.append(n)
        out.members.append(members)
    if rest:
        members = [m for e in rest for m in e[3]]
        if out.fields is None and out.agg not in ("count", "sum"):
            # an exact Other: its own records summarised, not the slices'
            mask = np.isin(out.group, np.asarray(members, dtype=np.int64))
            field = opts.get("field")
            value = float(aggregate(np.where(mask, 0, -1), 1, table.np(field), out.agg)[0][0])
        else:
            value = sum(e[1] for e in rest)
        out.other = len(out.labels)
        out.labels.append(OTHER)
        out.values.append(value)
        out.records.append(sum(e[2] for e in rest))
        out.members.append(members)
    out.total = float(sum(out.values))
    if out.group is not None:                # category -> slice (-1: none)
        slice_of = np.full(out.n_codes + 1, -1, dtype=np.int64)
        for s, members in enumerate(out.members):
            slice_of[np.asarray(members, dtype=np.int64)] = s
        g = out.group
        out.group = np.where(g >= 0, slice_of[np.clip(g, 0, out.n_codes)], -1)
    return out


def pie_rows(plot, table, slices):
    """Table rows of the slices *slices*: the records of their categories
    (slices of fields: the records whose field has a value)."""
    if plot.fields is None:
        return np.nonzero(np.isin(plot.group, np.asarray(list(slices))))[0]
    mask = np.zeros(len(table), dtype=bool)
    for s in slices:
        for f in plot.members[s]:
            mask |= ~np.isnan(table.np(f))
    return np.nonzero(mask)[0]


def pie_selected(plot, table, sel_mask, field=None):
    """Selected records of each slice and, for Count and Sum slices, the
    selected part of the slice's value."""
    k = len(plot)
    if plot.fields is None:
        g = plot.group[sel_mask]
        g = g[g >= 0]
        per = np.bincount(g, minlength=k)[:k].tolist() if k else []
        part = None
        if plot.agg == "count":
            part = [float(n) for n in per]
        elif plot.agg == "sum" and field:
            v = table.np(field)
            ok = sel_mask & (plot.group >= 0) & ~np.isnan(v)
            part = np.bincount(plot.group[ok], weights=v[ok], minlength=k)[:k].tolist()
        return per, part
    per, part = [], []
    for s in range(k):
        n = 0
        p = 0.0
        for f in plot.members[s]:
            v = table.np(f)
            ok = sel_mask & ~np.isnan(v)
            n += int(ok.sum())
            p += float(v[ok].sum())
        per.append(n)
        part.append(p)
    return per, (part if plot.agg == "sum" else None)


# ============================================================================
# HISTOGRAM
# ============================================================================

TRANSFORMS = (("none", "None"), ("log", "Logarithmic"), ("sqrt", "Square root"))
MAX_HIST_BINS = 200


class HistogramPlot:
    """Equal bins of one number field (after the chosen transformation):
    *edges*, *counts*, the statistics, the value, bin and table row of
    every value kept and, with Split by, the counts and statistics of each
    category (a small histogram each, on the same bins)."""

    def __init__(self):
        self.edges = []
        self.counts = []
        self.values = None
        self.bin_of = None
        self.rows = None
        self.stats = {}
        self.missing = 0             # NULL or invalid
        self.left_out = 0            # no logarithm / square root
        self.transform = "none"
        self.field = ""
        self.kind = F.REAL
        self.series = []             # Split by categories
        self.series_of = None        # category of every value kept
        self.series_counts = None    # [category][bin]
        self.series_stats = []

    def __len__(self):
        return len(self.counts)


def default_hist_bins(n):
    """The square root of the record count, as ArcGIS Pro (at most 64)."""
    return int(max(1, min(64, math.ceil(math.sqrt(max(n, 1))))))


def _stats(v):
    if not len(v):
        return {"n": 0, "mean": None, "median": None, "std": None, "min": None, "max": None}
    return {"n": int(len(v)), "mean": float(v.mean()), "median": float(np.median(v)),
            "std": float(v.std()), "min": float(v.min()), "max": float(v.max())}


def histogram(table, opts):
    """A histogram of "x" with "bins" equal bins (None: the default),
    "transform" ("none", "log": natural logarithm, "sqrt") and "split"
    (one small histogram per category, on the same bins)."""
    out = HistogramPlot()
    x = opts["x"]
    col = table.column(x)
    out.field, out.kind = x, col.kind
    v = table.np(x)
    ok = ~np.isnan(v)
    out.missing = int(len(v) - ok.sum())
    out.transform = opts.get("transform", "none")
    if out.transform == "log":
        good = ok & (v > 0)
    elif out.transform == "sqrt":
        good = ok & (v >= 0)
    else:
        good = ok
    out.left_out = int(ok.sum() - good.sum())
    rows = np.nonzero(good)[0]
    vals = v[rows]
    if out.transform == "log":
        vals = np.log(vals)
    elif out.transform == "sqrt":
        vals = np.sqrt(vals)
    out.rows, out.values = rows, vals
    out.stats = _stats(vals)
    if not len(vals):
        out.bin_of = np.zeros(0, dtype=np.int64)
        return out
    bins = opts.get("bins")
    bins = default_hist_bins(len(vals)) if not bins else int(max(1, min(MAX_HIST_BINS, bins)))
    lo, hi = float(vals.min()), float(vals.max())
    if lo == hi:
        edges = np.array([lo - 0.5, hi + 0.5])
    else:
        edges = np.linspace(lo, hi, bins + 1)
    out.edges = edges.tolist()
    k = len(edges) - 1
    bin_of = np.clip(np.searchsorted(edges, vals, side="right") - 1, 0, k - 1)
    out.bin_of = bin_of
    out.counts = np.bincount(bin_of, minlength=k)[:k].tolist()
    split = opts.get("split")
    if split and table.column(split) is not None:
        mask = np.zeros(len(table), dtype=bool)
        mask[rows] = True
        names, series = _split_series(table, split, mask)
        s = series[rows]
        out.series, out.series_of = names, s
        n_s = len(names)
        both = np.bincount(s * k + bin_of, minlength=n_s * k)[:n_s * k]
        out.series_counts = both.reshape(n_s, k).tolist()
        out.series_stats = [_stats(vals[s == i]) for i in range(n_s)]
    return out


def hist_rows(plot, bins, series=None):
    """Table rows of the values in *bins* (and of the Split by category)."""
    mask = np.isin(plot.bin_of, np.asarray(list(bins)))
    if series is not None and plot.series_of is not None:
        mask &= plot.series_of == series
    return plot.rows[mask]


def hist_selected(plot, sel_mask, series=None):
    """Selected values in each bin (of one Split by category)."""
    k = len(plot)
    if not k:
        return []
    sel = sel_mask[plot.rows]
    if series is not None and plot.series_of is not None:
        sel &= plot.series_of == series
    return np.bincount(plot.bin_of[sel], minlength=k)[:k].tolist()


def normal_pdf(x, mean, std):
    return np.exp(-0.5 * ((x - mean) / std) ** 2) / (std * math.sqrt(2 * math.pi))


# ============================================================================
# BOX PLOT
# ============================================================================

MAX_BOXES = 200


class Box:
    """One box: its category and field, quartiles (*q1*, *median*, *q3*),
    whiskers, outliers (values and table rows) and the table rows of every
    value."""

    def __init__(self, label, series):
        self.label = label
        self.series = series
        self.n = 0
        self.q1 = self.median = self.q3 = None
        self.low = self.high = None
        self.mean = None
        self.out_values = None
        self.out_rows = None
        self.rows = None


class BoxPlot:
    def __init__(self):
        self.categories = []         # along the X axis ("" without a category)
        self.series = []             # the fields
        self.boxes = []              # by category, then field
        self.standardized = False
        self.outliers = True
        self.missing = 0
        self.notes = []

    def __len__(self):
        return len(self.boxes)


def quartiles(v):
    """First quartile, median and third quartile (linear interpolation
    between the closest ranks)."""
    q1, med, q3 = np.percentile(v, [25.0, 50.0, 75.0])
    return float(q1), float(med), float(q3)


def box(table, opts):
    """Box plots of the number fields "fields" (one box each), per category
    of "split" (when given).  "standardize": with several fields their
    values become z-scores, so they share one axis.  "outliers": values
    more than 1.5 x the interquartile range beyond the box are drawn as
    points (the whiskers stop at the last value inside); off, the
    whiskers reach the minimum and maximum."""
    out = BoxPlot()
    fields = [f for f in (opts.get("fields") or []) if table.column(f) is not None]
    out.series = fields
    out.outliers = bool(opts.get("outliers", True))
    out.standardized = bool(opts.get("standardize", True)) and len(fields) > 1
    n = len(table)
    split = opts.get("split")
    if split and table.column(split) is not None:
        col = table.column(split)
        if col.role == CATEGORY:
            codes = table.np(split, "codes").astype(np.int64)
            labels = [str(v) for v in col.labels] + [F.NULL_TEXT]
            codes = np.where(codes < 0, len(labels) - 1, codes)
            rank = _codes_order(col) + [len(col.labels)]
        else:
            vals = table.np(split)
            nan = np.isnan(vals)
            uniq, inv = np.unique(np.where(nan, np.inf, vals), return_inverse=True)
            codes = inv.astype(np.int64)
            labels = [F.NULL_TEXT if not math.isfinite(u) else F.plain_number(u)
                      for u in uniq.tolist()]
            rank = list(range(len(labels)))
        present = np.bincount(codes, minlength=len(labels))
        cats = sorted((c for c in range(len(labels)) if present[c]), key=rank.__getitem__)
        if len(cats) > MAX_BOXES:
            out.notes.append(f"{len(cats):,} categories: the first {MAX_BOXES:,} are shown")
            cats = cats[:MAX_BOXES]
    else:
        codes = np.zeros(n, dtype=np.int64)
        labels = [""]
        cats = [0]
    out.categories = [labels[c] for c in cats]
    for s, f in enumerate(fields):
        v = table.np(f).copy()
        valid = ~np.isnan(v)
        out.missing += int(len(v) - valid.sum())
        if out.standardized and valid.any():
            mean, std = float(v[valid].mean()), float(v[valid].std())
            v = (v - mean) / std if std > 0 else v - mean
        for c in cats:
            b = Box(labels[c], s)
            rows = np.nonzero(valid & (codes == c))[0]
            b.rows = rows
            b.n = len(rows)
            if b.n:
                vals = v[rows]
                b.q1, b.median, b.q3 = quartiles(vals)
                b.mean = float(vals.mean())
                if out.outliers:
                    iqr = b.q3 - b.q1
                    lo_f, hi_f = b.q1 - 1.5 * iqr, b.q3 + 1.5 * iqr
                    inside = (vals >= lo_f) & (vals <= hi_f)
                    b.low = float(vals[inside].min()) if inside.any() else b.q1
                    b.high = float(vals[inside].max()) if inside.any() else b.q3
                    b.out_values = vals[~inside]
                    b.out_rows = rows[~inside]
                else:
                    b.low, b.high = float(vals.min()), float(vals.max())
                    b.out_values = vals[:0]
                    b.out_rows = rows[:0]
            out.boxes.append(b)
    # by category, then field
    nc = len(cats)
    out.boxes = [out.boxes[s * nc + ci] for ci in range(nc) for s in range(len(fields))]
    return out


# ============================================================================
# PLOTS: DEFAULTS AND CHECKS
# ============================================================================

def new_plot(ptype, fields, name=None):
    """A new plot of type *ptype*; its fields are chosen afterwards (Data),
    as in ArcGIS Pro.  *fields* is a list of (name, kind)."""
    numbers = [n for n, k in fields if role(k) == NUMBER]
    dates = [n for n, k in fields if role(k) == DATE]
    cats = [n for n, k in fields if role(k) == CATEGORY]
    data = {}
    del numbers, dates, cats          # the fields are chosen in Data, as in ArcGIS Pro
    if ptype == "scatter":
        data = {"x": None, "y": None, "size": None, "colour": "layer", "trend": False, "trend_type": "linear",
                "order": 2, "size_min": 4, "size_max": 20}
    elif ptype == "line":
        data = {"x": None, "fields": [], "agg": "count", "split": None, "unit": None, "interval": 1,
                "align": "start", "reference": None, "trim": False, "empty": "zero"}
    elif ptype == "bar":
        data = {"x": None, "fields": [], "agg": "count", "split": None, "multi": "side",
                "sort": "category_asc", "unit": None, "interval": 1, "align": "start",
                "reference": None, "trim": False}
    elif ptype == "combo":
        data = {"x": None, "series": [{"field": None, "agg": "count", "kind": "bar",
                                       "axis": "left"}],
                "unit": None, "interval": 1, "align": "start", "reference": None,
                "trim": False, "empty": "zero"}
    elif ptype == "pie":
        data = {"mode": "category", "category": None, "field": None, "agg": "sum",
                "fields": [], "threshold": 0, "donut": 0}
    elif ptype == "histogram":
        data = {"x": None, "bins": None, "transform": "none", "split": None,
                "mean": True, "median": True, "std": False, "normal": False}
    elif ptype == "box":
        data = {"fields": [], "split": None, "standardize": True, "outliers": True}
    return {
        "type": ptype, "name": name or TYPE_LABELS.get(ptype, "Plot"), "data": data,
        "axes": {"x_min": None, "x_max": None, "y_min": None, "y_max": None,
                 "x_log": False, "y_log": False},
        "guides": [],
        "format": {"colours": {}, "grid": True, "axis_lines": True, "colour": None},
        "general": {"title": "", "description": "", "x_title": "", "y_title": "",
                    "legend": True, "legend_pos": "auto"},
    }


def fields_used(plot):
    """The field names a plot reads."""
    d = plot.get("data", {})
    t = plot.get("type")
    names = []
    keys = ("category", "field") if t == "pie" else ("x", "y", "size", "split")
    if t == "pie" and d.get("mode") == "fields":
        keys = ()
    for key in keys:
        if d.get(key):
            names.append(d[key])
    if t == "box" or (t == "pie" and d.get("mode") == "fields"):
        names.extend(d.get("fields") or [])
    elif t in ("line", "bar") and d.get("agg", "count") != "count":
        names.extend(d.get("fields") or [])
    if t == "box" and d.get("split"):
        names.append(d["split"])
    for spec in d.get("series") or []:
        if spec.get("field"):
            names.append(spec["field"])
    return list(dict.fromkeys(names))


def missing_fields(plot, available):
    """Fields a plot needs that are not in *available* (names)."""
    have = set(available)
    return [n for n in fields_used(plot) if n not in have]


def ready(plot):
    """Why the plot cannot be drawn yet ('' when it can)."""
    d = plot.get("data", {})
    t = plot.get("type")
    if t == "scatter":
        if not d.get("x") or not d.get("y"):
            return "Choose a number field for X and for Y"
    elif t in ("line", "bar"):
        if not d.get("x"):
            return ("Choose a date or number field for the X axis" if t == "line"
                    else "Choose a category field")
        if d.get("agg", "count") != "count" and not d.get("fields"):
            return f"Choose a number field to {AGG_LABELS[d['agg']].lower()}"
    elif t == "combo":
        if not d.get("x"):
            return "Choose a field for the X axis"
    elif t == "pie":
        if d.get("mode") == "fields":
            if not d.get("fields"):
                return "Choose the number fields that make the slices"
        elif not d.get("category"):
            return "Choose the category field that makes the slices"
    elif t == "histogram":
        if not d.get("x"):
            return "Choose a number field"
    elif t == "box":
        if not d.get("fields"):
            return "Choose a number field for the Y axis"
    else:
        return f"{TYPE_LABELS.get(t, t)} comes in a later round"
    return ""


def default_title(plot):
    d = plot.get("data", {})
    t = plot.get("type")
    if t == "scatter" and d.get("x") and d.get("y"):
        return f"{d['y']} and {d['x']}"
    if t in ("line", "bar") and d.get("x"):
        agg = d.get("agg", "count")
        if agg == "count" or not d.get("fields"):
            what = "Count"
        else:
            what = f"{AGG_LABELS[agg]} of {', '.join(d['fields'])}"
        title = f"{what} by {d['x']}"
        if d.get("split"):
            title += f" and {d['split']}"
        return title
    if t == "combo" and d.get("x"):
        names = [series_name(s.get("field"), s.get("agg", "count"))
                 for s in d.get("series") or []]
        return f"{', '.join(names) or 'Count'} by {d['x']}"
    if t == "pie":
        if d.get("mode") == "fields" and d.get("fields"):
            return f"{AGG_LABELS.get(d.get('agg', 'sum'))} of {', '.join(d['fields'])}"
        if d.get("category"):
            what = series_name(d.get("field"), d.get("agg", "sum") if d.get("field")
                               else "count")
            return f"{what} by {d['category']}"
    if t == "histogram" and d.get("x"):
        return f"Distribution of {d['x']}"
    if t == "box" and d.get("fields"):
        title = ", ".join(d["fields"])
        return title + (f" by {d['split']}" if d.get("split") else "")
    return TYPE_LABELS.get(t, "Plot")


def parse_value(text, axis_role):
    """A guide's or a bound's value from its text: a number, or a date
    (YYYY-MM-DD, optionally with HH:MM[:SS]) on a date axis.  None when
    empty or unreadable."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    text = str(text).strip()
    if not text:
        return None
    if axis_role == DATE:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue
            return F.temporal_key(dt)
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None
