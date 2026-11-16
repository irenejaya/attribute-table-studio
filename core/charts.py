# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Visualize Statistics: the numbers behind the chart window, built for big
layers (millions of records).

:func:`read` streams the Explorer's scope once, in a background job, and
keeps compact columns only: the feature id and the value of each charted
record (8 bytes each, ``array('q')`` / ``array('d')``) or a small integer
code per record for a text value and for the Split by field.  No Python
object is kept per record, so 10 million records take about 250 MB rather
than several GB, and drawing costs the same for 100 records or 10 million
(it only ever sees the bars).

Everything else (statistics, bins, categories, periods, split counts, the
selected records of each bar, the ids behind a clicked bar) is worked out
from those columns, with numpy when QGIS has it (it nearly always does) and
in plain Python otherwise, so changing the bins never reads the layer
again.  Only :func:`read` touches QGIS.

Rules shared with the Explorer's statistics (:mod:`.stats`): NULL is
counted apart, NaN / infinity and unreadable values are *invalid*, the
standard deviation is the population one, bins are [low, high) except the
last, which holds the maximum.
"""

import bisect
import datetime
import math
import threading
from array import array

from . import fields as F

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:              # pragma: no cover - QGIS ships numpy
    np = None
    HAS_NUMPY = False

USE_NUMPY = HAS_NUMPY            # tests switch this off to check plain Python

CATEGORY_CAP = 100000            # distinct text values tracked one by one
SPLIT_CAP = 1000                 # distinct Split by values tracked
SPLIT_SERIES = 8                 # categories with their own colour
MAX_BINS = 100
MIN_BINS = 4
MAX_PERIODS = 1500               # bars of the over-time chart
CHUNK = 1 << 20                  # numpy works in slices of this many records
NULL_KEY = None
OTHER = -2                       # code of values beyond CATEGORY_CAP
NULL_CODE = -1

HISTOGRAM, BARS, TIME = "histogram", "bars", "time"
_CATEGORIES_LOCK = threading.Lock()   # two quick chart switches never race
INTERVALS = ("day", "week", "month", "quarter", "year")
INTERVAL_LABELS = {"day": "Day", "week": "Week", "month": "Month",
                   "quarter": "Quarter", "year": "Year"}
_EPOCH = 719163                  # date(1970, 1, 1).toordinal()


def chart_kind(kind):
    """How a field kind is read and its default bars: histogram (numbers,
    times), bars (text, yes / no) or over time (dates)."""
    if kind in F.NUMERIC or kind == F.TIME:
        return HISTOGRAM
    if kind in (F.DATE, F.DATETIME):
        return TIME
    return BARS


# the Chart box: every field kind offers a histogram (vertical columns) and
# a bar chart (horizontal bars)
CHART_TYPES = (("histogram", "Histogram"), ("bar", "Bar chart"))


def default_chart_type(kind):
    return "bar" if chart_kind(kind) == BARS else "histogram"


def bars_kind(kind, chart_type):
    """The bars for a field kind and a chart type: dates always by period
    (columns or bars), numbers in bins (histogram) or one bar per value
    (bar chart), text one bar or column per value."""
    base = chart_kind(kind)
    if base == TIME:
        return TIME
    if base == HISTOGRAM and chart_type == "histogram":
        return HISTOGRAM
    return BARS


def _np():
    return np if USE_NUMPY else None


# ============================================================================
# READING
# ============================================================================

class Column:
    """One field over the scope, kept compact (see the module notes).

    Numbers, times and dates keep only their valid records: *fids* and
    *values* (numbers, seconds since midnight, or days on the proleptic
    ordinal scale with the time of day as a fraction).  Text and yes / no
    fields keep every record: *fids* and *codes* (index into *labels*,
    ``NULL_CODE`` for NULL, ``OTHER`` beyond ``CATEGORY_CAP``).  *split*
    holds the Split by code of each kept record (index into
    *split_labels*)."""

    def __init__(self, kind, name):
        self.kind = kind
        self.name = name
        self.chart = chart_kind(kind)
        self.scope = 0               # records in scope
        self.missing = 0             # NULL
        self.invalid = 0
        self.fids = array("q")
        self.values = array("d")
        self.codes = array("i")
        self.labels = []
        self.counts = []
        self.unlisted = 0            # records of values beyond CATEGORY_CAP
        self.sort_keys = None        # numbers as categories: value per code
        self.null_fids = array("q")  # numbers / dates: the NULL records
        self.null_split = array("i")  # ... and their Split by codes
        self._categories = False     # numbers: categories worked out
        self.split_name = None
        self.split = None
        self.split_labels = []
        self.split_counts = []
        self.split_unlisted = 0
        self.stats = None
        self._np_cache = {}

    @property
    def size(self):
        """Records kept (charted)."""
        return len(self.fids)

    @property
    def capped(self):
        return self.unlisted > 0

    def np(self, name):
        """numpy view of one compact column (no copy)."""
        arr = self._np_cache.get(name)
        if arr is None:
            src = getattr(self, name)
            dtype = {"q": np.int64, "d": np.float64, "i": np.int32}[src.typecode]
            arr = np.frombuffer(src, dtype=dtype) if len(src) else np.zeros(0, dtype)
            self._np_cache[name] = arr
        return arr


def _is_condition(exp):
    """True when a QGIS expression is a condition (a comparison, AND / OR /
    NOT, IN, BETWEEN, LIKE, IS ...): QGIS returns 1 / 0 for those."""
    import qgis.core as qc
    node = exp.rootNode()
    if node is None:
        return False
    for name in ("QgsExpressionNodeInOperator", "QgsExpressionNodeBetweenOperator"):
        cls = getattr(qc, name, None)
        if cls is not None and isinstance(node, cls):
            return True
    if isinstance(node, qc.QgsExpressionNodeUnaryOperator):
        return node.op() == getattr(qc.QgsExpressionNodeUnaryOperator, "uoNot", None)
    if isinstance(node, qc.QgsExpressionNodeBinaryOperator):
        B = qc.QgsExpressionNodeBinaryOperator
        arithmetic = [getattr(B, n, None) for n in ("boPlus", "boMinus", "boMul", "boDiv",
                                                    "boIntDiv", "boMod", "boPow",
                                                    "boConcat")]
        return node.op() not in arithmetic
    return False


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


def read(spec, job, field_idx, kind, name, split_idx=None, split_name=None,
         split_expr=None):
    """Read the scope of *spec* (a :class:`.scan.ScanSpec`) into a
    :class:`Column`; runs inside a background job.  Split by is a field
    (*split_idx*) or a QGIS expression (*split_expr*, evaluated for every
    record; an evaluation error counts as NULL)."""
    from .scan import stream

    col = Column(kind, name)
    attrs = [field_idx] + ([split_idx] if split_idx is not None else [])
    geometry = False
    exp = ctx = None
    if split_expr:
        from qgis.core import QgsExpression, QgsExpressionContext, QgsFeatureRequest
        exp = QgsExpression(split_expr)
        ctx = QgsExpressionContext(spec.context)
        ctx.setFields(spec.fields)
        exp.prepare(ctx)
        if QgsFeatureRequest.ALL_ATTRIBUTES in exp.referencedColumns():
            attrs = list(range(spec.fields.count()))
        else:
            attrs += [i for i in exp.referencedAttributeIndexes(spec.fields) if i >= 0]
        geometry = exp.needsGeometry()
        condition = _is_condition(exp)       # 1 / 0 read as true / false
        split_idx = -1                       # a split, from the expression
        split_name = split_name or split_expr
    fids_append = col.fids.append
    split_codes = None
    split_index = {}
    split_counts = []
    if split_idx is not None:
        col.split_name = split_name
        col.split = split_codes = array("i")
    split_append = split_codes.append if split_codes is not None else None
    is_null = F.is_null
    scope = missing = invalid = 0

    def split_code(feat):
        if exp is not None:
            ctx.setFeature(feat)
            sv = exp.evaluate(ctx)
            if exp.hasEvalError():
                sv = None
            elif condition and not (sv is None or is_null(sv)):
                sv = "true" if sv else "false"
        else:
            sv = feat.attribute(split_idx)
        key = NULL_KEY if (sv is None or is_null(sv)) else (
            sv if type(sv) is str else F.display_value(sv))
        code = split_index.get(key)
        if code is None:
            if len(split_index) >= SPLIT_CAP:
                col.split_unlisted += 1
                return OTHER
            code = split_index[key] = len(split_counts)
            split_counts.append(0)
        split_counts[code] += 1
        return code

    chart = col.chart
    if chart == BARS:
        index = {}
        counts = col.counts
        codes_append = col.codes.append
        boolean = kind == F.BOOL
        for feat in stream(spec, job, attrs, geometry=geometry):
            scope += 1
            v = feat.attribute(field_idx)
            if boolean:
                key = _bool_key(v)
                if key == "":
                    invalid += 1
                    continue
            elif type(v) is str:
                key = v
            elif v is None or is_null(v):
                key = None
            else:
                key = F.display_value(v)
            if key is None:
                missing += 1
                code = NULL_CODE
            else:
                code = index.get(key)
                if code is None:
                    if len(index) >= CATEGORY_CAP:
                        col.unlisted += 1
                        code = OTHER
                    else:
                        code = index[key] = len(counts)
                        counts.append(0)
                if code >= 0:
                    counts[code] += 1
            fids_append(feat.id())
            codes_append(code)
            if split_append is not None:
                split_append(split_code(feat))
        col.labels = [None] * len(index)
        for key, code in index.items():
            col.labels[code] = key
    else:
        values_append = col.values.append
        null_append = col.null_fids.append
        null_split_append = col.null_split.append if split_idx is not None else None
        isfinite = math.isfinite
        numeric = chart == HISTOGRAM and kind != F.TIME
        tkey = F.temporal_key
        for feat in stream(spec, job, attrs, geometry=geometry):
            scope += 1
            v = feat.attribute(field_idx)
            if v is None or (type(v) not in (float, int) and is_null(v)):
                missing += 1
                null_append(feat.id())           # a NULL bar can select them
                if null_split_append is not None:
                    null_split_append(split_code(feat))
                continue
            if numeric:
                t = type(v)
                if t is float:
                    if not isfinite(v):
                        invalid += 1
                        continue
                    x = v
                elif t is int:
                    x = float(v)
                else:
                    try:
                        x = float(v)
                    except (TypeError, ValueError):
                        invalid += 1
                        continue
                    if not isfinite(x):
                        invalid += 1
                        continue
            else:
                x = tkey(v)
                if x is None:
                    invalid += 1
                    continue
            values_append(x)
            fids_append(feat.id())
            if split_append is not None:
                split_append(split_code(feat))
    col.scope, col.missing, col.invalid = scope, missing, invalid
    if split_idx is not None:
        col.split_counts = split_counts
        col.split_labels = [None] * len(split_index)
        for key, code in split_index.items():
            col.split_labels[code] = F.NULL_TEXT if key is NULL_KEY else key
    job.check()
    col.stats = statistics(col, job)
    return col


# ============================================================================
# STATISTICS
# ============================================================================

def statistics(col, job=None):
    """Statistics of the kept values: a dict (see :func:`_numeric_stats`
    and :func:`_category_stats`)."""
    base = {"records": col.scope, "count": col.scope - col.missing - col.invalid,
            "null": col.missing, "invalid": col.invalid}
    if col.chart == BARS:
        base.update(_category_stats(col))
    else:
        base.update(_numeric_stats(col.values, job,
                                   moments=col.chart == HISTOGRAM and col.kind != F.TIME,
                                   numpy_values=col.np("values") if _np() else None))
    return base


def _category_stats(col):
    listed = [(n, label) for label, n in zip(col.labels, col.counts)]
    out = {"distinct": len(listed) + (1 if col.unlisted else 0),
           "distinct_capped": col.capped,
           "most": None, "least": None}
    if listed:
        most = min(listed, key=lambda t: (-t[0], t[1]))
        least = min(listed, key=lambda t: (t[0], t[1]))
        out["most"] = (most[1], most[0])
        out["least"] = (least[1], least[0])
    return out


def _numeric_stats(values, job=None, moments=True, numpy_values=None):
    """min, max, sum, mean, median, std (population) and, with *moments*,
    skewness and kurtosis (normal = 3) of an ``array('d')``."""
    n = len(values)
    out = {"valid": n, "min": None, "max": None, "sum": None, "mean": None,
           "median": None, "std": None, "skewness": None, "kurtosis": None}
    if not n:
        return out
    a = numpy_values
    if a is not None:
        lo, hi = float(a.min()), float(a.max())
    else:
        lo, hi = min(values), max(values)
    total = math.fsum(values)
    mean = total / n
    if job is not None:
        job.check()
    out.update({"min": lo, "max": hi, "sum": total, "mean": mean})
    out["median"] = median(values, lo, hi, a)
    if job is not None:
        job.check()
    s2 = s3 = s4 = 0.0
    if a is not None:
        for i in range(0, n, CHUNK):
            d = a[i:i + CHUNK] - mean
            d2 = d * d
            s2 += float(d2.sum())
            if moments:
                s3 += float((d2 * d).sum())
                s4 += float((d2 * d2).sum())
    elif moments:
        for x in values:
            d = x - mean
            d2 = d * d
            s2 += d2
            s3 += d2 * d
            s4 += d2 * d2
    else:
        s2 = math.fsum((x - mean) * (x - mean) for x in values)
    m2 = s2 / n
    out["std"] = math.sqrt(m2)
    if moments and m2 > 0:
        out["skewness"] = (s3 / n) / m2 ** 1.5
        out["kurtosis"] = (s4 / n) / (m2 * m2)
    return out


def median(values, lo=None, hi=None, numpy_values=None):
    """Exact median of an ``array('d')`` without sorting a Python list of
    millions of floats: numpy partitions a copy, plain Python counts the
    values into 65,536 buckets and sorts only the middle bucket."""
    n = len(values)
    if not n:
        return None
    if numpy_values is not None:
        return float(np.median(numpy_values))
    if n <= 200000:
        s = sorted(values)
        mid = n // 2
        return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0
    lo = min(values) if lo is None else lo
    hi = max(values) if hi is None else hi
    if lo == hi:
        return lo
    buckets = 65536
    scale = buckets / (hi - lo)
    counts = [0] * (buckets + 1)
    for x in values:
        counts[int((x - lo) * scale)] += 1
    k1, k2 = (n - 1) // 2, n // 2
    seen = 0
    b1 = b2 = None
    for b, c in enumerate(counts):
        if b1 is None and seen + c > k1:
            b1, before = b, seen
        if seen + c > k2:
            b2 = b
            break
        seen += c
    picked = sorted(x for x in values if b1 <= int((x - lo) * scale) <= b2)
    return (picked[k1 - before] + picked[k2 - before]) / 2.0


def default_bins(n, kind=None, lo=None, hi=None):
    """Bins for *n* values (Rice rule, 8 to 50); whole numbers spanning up
    to 50 values get one bar each."""
    if kind == F.INTEGER and lo is not None and hi is not None \
            and float(lo).is_integer() and float(hi).is_integer() and 0 < hi - lo < 50:
        return int(hi - lo + 1)
    if n < 2:
        return MIN_BINS
    return int(max(8, min(50, round(2.0 * n ** (1.0 / 3.0)))))


# ============================================================================
# BARS
# ============================================================================

class Bars:
    """What the chart draws: one entry per bar, plus the bar of every kept
    record (*codes*, -1 = in no bar) for selection and Split by."""

    def __init__(self, chart):
        self.chart = chart
        self.labels = []             # hover / export text per bar
        self.axis_labels = None      # shorter axis text (weeks), else labels
        self.lows = []               # histogram / time: start of each bar
        self.highs = []              # ... and its end (inclusive for the last)
        self.counts = []
        self.codes = None            # array('i') or numpy int32, per kept record
        self.integer = False         # one bar per whole number
        self.width = None            # histogram bin width
        self.interval = None         # time: day / week / month / quarter / year
        self.other = None            # bars: index of the "Other" bar
        self.null_bar = None         # bars: index of the NULL bar
        self.members = []            # bars: category codes of each bar
        self.series = []             # Split by: names (colour order)
        self.series_codes = None     # raw split code -> series index
        self.split = None            # [[count per series]] per bar
        self.total = 0               # records in the bars
        self.note = ""               # e.g. "Day has too many bars: Month"

    def __len__(self):
        return len(self.counts)


def histogram(col, bins):
    """Equal-width bins between the minimum and the maximum."""
    st = col.stats or {}
    out = Bars(HISTOGRAM)
    lo, hi = st.get("min"), st.get("max")
    n = col.size
    if not n or lo is None:
        out.codes = _empty_codes(0)
        return out
    a = col.np("values") if _np() else None
    bins = int(max(1, min(bins, MAX_BINS)))
    if lo == hi:
        out.labels, out.lows, out.highs, out.counts = [lo], [lo], [hi], [n]
        out.integer = True
        out.codes = _filled_codes(n, 0)
        out.total = n
        return out
    integer = (col.kind == F.INTEGER and float(lo).is_integer() and float(hi).is_integer()
               and hi - lo + 1 <= bins)
    if integer:
        k = int(hi - lo + 1)
        out.integer = True
        out.lows = [lo + i for i in range(k)]
        out.highs = list(out.lows)
        if a is not None:
            codes = (a - lo).astype(np.int32)
        else:
            base = lo
            codes = array("i", (int(x - base) for x in col.values))
        out.width = 1.0
    else:
        width = (hi - lo) / float(bins)
        edges = [lo + i * width for i in range(bins)] + [hi]
        inner = edges[1:-1]
        out.lows, out.highs = edges[:-1], edges[1:]
        out.width = width
        if a is not None:
            codes = np.searchsorted(np.asarray(inner), a, side="right").astype(np.int32)
        else:
            right = bisect.bisect_right
            codes = array("i", (right(inner, x) for x in col.values))
        k = bins
    out.codes = codes
    out.counts = _bincount(codes, k)
    out.labels = list(out.lows)
    out.total = n
    return out


def ensure_categories(col):
    """Numbers (and times) as categories, for their bar chart: one code
    per distinct value, worked out once from the kept values (the most
    common ``CATEGORY_CAP`` values; the rest count as Other)."""
    if col.chart == BARS or col._categories:
        return col
    with _CATEGORIES_LOCK:
        if not col._categories:
            _number_categories(col)
    return col


def _number_categories(col):
    xp = _np()
    if xp is not None and col.size:
        uniq, inverse, counts = np.unique(col.np("values"), return_inverse=True,
                                          return_counts=True)
        keep = len(uniq)
        if keep > CATEGORY_CAP:
            order = np.argsort(-counts, kind="stable")[:CATEGORY_CAP]
            remap = np.full(keep, OTHER, dtype=np.int32)
            remap[order] = np.arange(len(order), dtype=np.int32)
            col.unlisted = int(counts.sum() - counts[order].sum())
            codes = remap[inverse]
            uniq, counts = uniq[order], counts[order]
        else:
            codes = inverse.astype(np.int32)
        values = uniq.tolist()
        col.counts = counts.tolist()
        col.codes = array("i", codes.astype(np.int32).tobytes())
    else:
        index, counts, values = {}, [], []
        codes = array("i")
        app = codes.append
        unlisted = 0
        for x in col.values:
            code = index.get(x)
            if code is None:
                if len(index) >= CATEGORY_CAP:
                    unlisted += 1
                    app(OTHER)
                    continue
                code = index[x] = len(counts)
                counts.append(0)
                values.append(x)
            counts[code] += 1
            app(code)
        col.counts, col.codes, col.unlisted = counts, codes, unlisted
    col.sort_keys = values
    if col.kind == F.TIME:
        col.labels = [F.key_to_text(v, F.TIME) for v in values]
    else:
        col.labels = [F.format_number(int(v)) if float(v).is_integer() and abs(v) < 1e15
                      else F.format_number(v) for v in values]
    col._np_cache.pop("codes", None)
    col._categories = True


SORTS = ("az", "za", "fewest", "count", "custom")


def categories(col, sort="count", top=10, null_bar=False, order=None):
    """One bar per value.  *sort*: "az" (A → Z, smallest number first),
    "za", "fewest" (fewest records first), "count" (most records first) or
    "custom" (the values in *order*, a list of labels; others follow, most
    records first).  *top* keeps the most common that many (in the chosen
    order) and groups the rest as Other (None keeps all, which still stops
    at 1,000 bars); *null_bar* draws NULL as a bar.  Other and NULL always
    come last."""
    ensure_categories(col)
    out = Bars(BARS)
    entries = [(label, n, code) for code, (label, n) in
               enumerate(zip(col.labels, col.counts)) if n]
    keys = col.sort_keys
    if keys is not None:
        alpha = (lambda e: keys[e[2]])
    else:
        alpha = (lambda e: (e[0].lower(), e[0]))
    if sort == "az":
        entries.sort(key=alpha)
    elif sort == "za":
        entries.sort(key=alpha, reverse=True)
    elif sort == "fewest":
        entries.sort(key=lambda e: (e[1], alpha(e)))
    elif sort == "custom" and order:
        pos = {label: i for i, label in enumerate(order)}
        last = len(pos)
        entries.sort(key=lambda e: (pos.get(e[0], last), -e[1], alpha(e)))
    else:
        entries.sort(key=lambda e: (-e[1], alpha(e)))
    limit = 1000 if top is None else int(top)
    shown, rest = entries, []
    if len(entries) > limit:
        if sort == "count":
            shown, rest = entries[:limit], entries[limit:]
        else:                        # keep the most common, in the chosen order
            keep = {e[2] for e in sorted(entries, key=lambda e: (-e[1], alpha(e)))[:limit]}
            shown = [e for e in entries if e[2] in keep]
            rest = [e for e in entries if e[2] not in keep]
    mapping = [-1] * (len(col.labels) + 2)      # code + 2 -> bar
    for i, (label, n, code) in enumerate(shown):
        out.labels.append(label)
        out.counts.append(n)
        out.members.append([code])
        mapping[code + 2] = i
    other_n = sum(e[1] for e in rest) + col.unlisted
    if other_n:
        out.other = len(out.counts)
        out.labels.append("Other")
        out.counts.append(other_n)
        out.members.append([e[2] for e in rest] + [OTHER])
        for e in rest:
            mapping[e[2] + 2] = out.other
        mapping[OTHER + 2] = out.other
    if null_bar and col.missing:
        out.null_bar = len(out.counts)
        out.labels.append(F.NULL_TEXT)
        out.counts.append(col.missing)
        out.members.append([NULL_CODE])
        mapping[NULL_CODE + 2] = out.null_bar
    out.codes = _map_codes(col, mapping)
    out.total = sum(out.counts)
    return out


def auto_interval(lo, hi):
    """The interval giving at most about 120 bars for days *lo* .. *hi*."""
    span = (hi - lo) if lo is not None else 0
    if span <= 120:
        return "day"
    if span <= 120 * 7:
        return "week"
    if span <= 120 * 31:
        return "month"
    if span <= 120 * 92:
        return "quarter"
    return "year"


def periods(col, interval="auto", show_empty=True):
    """Bars of a date field per day, week (from Monday), month, quarter or
    year; too many bars for the chosen interval switch to a longer one."""
    out = Bars(TIME)
    st = col.stats or {}
    lo, hi = st.get("min"), st.get("max")
    if not col.size or lo is None:
        out.codes = _empty_codes(0)
        out.interval = "month" if interval == "auto" else interval
        return out
    d0, d1 = int(math.floor(lo)), int(math.floor(hi))
    chosen = auto_interval(d0, d1) if interval == "auto" else interval
    while _period_count(d0, d1, chosen) > MAX_PERIODS and chosen != "year":
        longer = INTERVALS[INTERVALS.index(chosen) + 1]
        out.note = (f"{INTERVAL_LABELS[chosen]} would give more than {MAX_PERIODS:,} "
                    f"bars: shown by {INTERVAL_LABELS[longer].lower()}")
        chosen = longer
    out.interval = chosen
    first = _period_of(d0, chosen)
    k = _period_of(d1, chosen) - first + 1
    a = col.np("values") if _np() else None
    if a is not None:
        codes = _np_periods(a, chosen) - first
        codes = codes.astype(np.int32)
    else:
        cache = {}
        codes = array("i")
        app = codes.append
        floor = math.floor
        for x in col.values:
            d = int(floor(x))
            p = cache.get(d)
            if p is None:
                p = cache[d] = _period_of(d, chosen) - first
            app(p)
    counts = _bincount(codes, k)
    keep = list(range(k)) if show_empty else [i for i, c in enumerate(counts) if c]
    if len(keep) != k:
        mapping = [-1] * k
        for new, old in enumerate(keep):
            mapping[old] = new
        codes = _remap(codes, mapping)
    out.codes = codes
    for i in keep:
        start, end = _period_bounds(first + i, chosen)
        out.lows.append(start)
        out.highs.append(end)
        out.labels.append(period_label(start, chosen))
        out.counts.append(counts[i])
    if chosen == "week":
        out.axis_labels = [datetime.date.fromordinal(d).isoformat() for d in out.lows]
    out.total = col.size
    return out


def _period_of(day, interval):
    """Period number of a proleptic ordinal *day*."""
    if interval == "day":
        return day
    if interval == "week":
        return (day - 1) // 7            # ordinal 1 (0001-01-01) is a Monday
    d = datetime.date.fromordinal(max(1, day))
    if interval == "month":
        return d.year * 12 + d.month - 1
    if interval == "quarter":
        return d.year * 4 + (d.month - 1) // 3
    return d.year


def _np_periods(a, interval):
    days = np.floor(a).astype(np.int64)
    if interval == "day":
        return days
    if interval == "week":
        return (days - 1) // 7
    dates = (days - _EPOCH).astype("datetime64[D]")
    months = dates.astype("datetime64[M]").astype(np.int64)     # since 1970-01
    if interval == "month":
        return months + 1970 * 12
    if interval == "quarter":
        return (months + 1970 * 12) // 3
    return dates.astype("datetime64[Y]").astype(np.int64) + 1970


def _period_count(d0, d1, interval):
    return _period_of(d1, interval) - _period_of(d0, interval) + 1


def _period_bounds(p, interval):
    """(first day, last day) of period *p* as proleptic ordinals."""
    if interval == "day":
        return p, p
    if interval == "week":
        return p * 7 + 1, p * 7 + 7
    if interval == "month":
        y, m = divmod(p, 12)
        start = datetime.date(y, m + 1, 1)
        nxt = datetime.date(y + (m + 1) // 12, (m + 1) % 12 + 1, 1)
        return start.toordinal(), nxt.toordinal() - 1
    if interval == "quarter":
        y, q = divmod(p, 4)
        start = datetime.date(y, 3 * q + 1, 1)
        nxt = datetime.date(y + 1, 1, 1) if q == 3 else datetime.date(y, 3 * q + 4, 1)
        return start.toordinal(), nxt.toordinal() - 1
    return datetime.date(p, 1, 1).toordinal(), datetime.date(p, 12, 31).toordinal()


def period_label(start, interval):
    d = datetime.date.fromordinal(start)
    if interval == "day":
        return d.isoformat()
    if interval == "week":
        return f"Week of {d.isoformat()}"
    if interval == "month":
        return f"{d.year}-{d.month:02d}"
    if interval == "quarter":
        return f"{d.year} Q{(d.month - 1) // 3 + 1}"
    return str(d.year)


# ============================================================================
# SPLIT BY, SELECTION, CLICKED BARS
# ============================================================================

def apply_split(col, bars):
    """Count each bar per Split by category: the most common
    ``SPLIT_SERIES`` categories get their own series, the rest form
    "Other"."""
    if col.split is None:
        return bars
    order = sorted(range(len(col.split_labels)),
                   key=lambda c: (-col.split_counts[c], str(col.split_labels[c])))
    keep = order[:SPLIT_SERIES]
    rest = len(order) > SPLIT_SERIES or col.split_unlisted
    series_of = [-1] * (len(col.split_labels) + 2)     # code + 2 -> series
    for s, code in enumerate(keep):
        series_of[code + 2] = s
    names = [col.split_labels[c] for c in keep]
    if rest:
        other = len(names)
        names.append("Other")
        for code in order[SPLIT_SERIES:]:
            series_of[code + 2] = other
        series_of[OTHER + 2] = other
    bars.series = names
    bars.series_codes = series_of
    k, s = len(bars.counts), len(names)
    xp = _np()
    if xp is not None:
        sc = np.asarray(series_of, dtype=np.int32)[col.np("split") + 2]
        bc = np.asarray(bars.codes)
        ok = (bc >= 0) & (sc >= 0)
        flat = np.bincount(bc[ok].astype(np.int64) * s + sc[ok], minlength=k * s)
        bars.split = flat.reshape(k, s).tolist()
    else:
        grid = [[0] * s for _ in range(k)]
        for b, code in zip(bars.codes, col.split):
            if b >= 0:
                si = series_of[code + 2]
                if si >= 0:
                    grid[b][si] += 1
        bars.split = grid
    nb = bars.null_bar
    if nb is not None and len(col.null_fids):    # numbers: NULL records kept apart
        row = [0] * s
        for code in col.null_split:
            si = series_of[code + 2]
            if si >= 0:
                row[si] += 1
        bars.split[nb] = row
    return bars


def _null_part(col, bars):
    """True when the NULL bar's records are kept apart (numbers)."""
    return bars.null_bar is not None and len(col.null_fids) > 0


def selected_counts(col, bars, selected):
    """(selected records per bar, [[per series]] or None) for the layer's
    selected feature ids *selected* (a set)."""
    k = len(bars.counts)
    if not selected or not k:
        return [0] * k, None
    s = len(bars.series)
    xp = _np()
    if xp is not None:
        sel = np.fromiter(selected, dtype=np.int64, count=len(selected))
        mask = np.isin(col.np("fids"), sel)
        bc = np.asarray(bars.codes)
        mask &= bc >= 0
        per_bar = np.bincount(bc[mask], minlength=k).tolist()
        per_series = None
        if s and bars.series_codes is not None:
            sc = np.asarray(bars.series_codes, dtype=np.int32)[col.np("split") + 2]
            m2 = mask & (sc >= 0)
            flat = np.bincount(bc[m2].astype(np.int64) * s + sc[m2], minlength=k * s)
            per_series = flat.reshape(k, s).tolist()
        _null_selected(col, bars, selected, per_bar, per_series)
        return per_bar, per_series
    per_bar = [0] * k
    per_series = [[0] * s for _ in range(k)] if s and bars.series_codes is not None else None
    split = col.split
    for i, (fid, b) in enumerate(zip(col.fids, bars.codes)):
        if b >= 0 and fid in selected:
            per_bar[b] += 1
            if per_series is not None:
                si = bars.series_codes[split[i] + 2]
                if si >= 0:
                    per_series[b][si] += 1
    _null_selected(col, bars, selected, per_bar, per_series)
    return per_bar, per_series


def _null_selected(col, bars, selected, per_bar, per_series):
    if not _null_part(col, bars):
        return
    nb = bars.null_bar
    split = col.null_split if len(col.null_split) else None
    for i, fid in enumerate(col.null_fids):
        if fid in selected:
            per_bar[nb] += 1
            if per_series is not None and split is not None:
                si = bars.series_codes[split[i] + 2]
                if si >= 0:
                    per_series[nb][si] += 1


def fids_of(col, bars, bar_indexes, series=None):
    """Feature ids of the records in *bar_indexes* (and, with *series*, of
    that Split by series only), as a list for ``selectByIds``."""
    wanted = set(int(b) for b in bar_indexes)
    if not wanted:
        return []
    extra = []
    if _null_part(col, bars) and bars.null_bar in wanted:
        if series is None or bars.series_codes is None:
            extra = list(col.null_fids)
        else:
            sc = bars.series_codes
            extra = [fid for fid, code in zip(col.null_fids, col.null_split)
                     if sc[code + 2] == series]
    return _fids_of(col, bars, wanted, series) + extra


def _fids_of(col, bars, wanted, series):
    xp = _np()
    if xp is not None:
        bc = np.asarray(bars.codes)
        mask = np.isin(bc, np.fromiter(wanted, dtype=np.int32, count=len(wanted)))
        if series is not None and bars.series_codes is not None:
            sc = np.asarray(bars.series_codes, dtype=np.int32)[col.np("split") + 2]
            mask &= sc == int(series)
        return col.np("fids")[mask].tolist()
    out = []
    if series is not None and bars.series_codes is not None:
        sc = bars.series_codes
        for fid, b, code in zip(col.fids, bars.codes, col.split):
            if b in wanted and sc[code + 2] == series:
                out.append(fid)
        return out
    for fid, b in zip(col.fids, bars.codes):
        if b in wanted:
            out.append(fid)
    return out


def busiest(bars):
    """(label, records) of the bar with the most records."""
    if not bars.counts:
        return None
    i = max(range(len(bars.counts)), key=lambda j: (bars.counts[j], -j))
    return bars.labels[i], bars.counts[i]


def table_rows(bars, selected=None):
    """Rows for the CSV export: bar, (from, to,) records, share, selected
    and one column per Split by series."""
    total = float(bars.total or 1)
    rows = []
    for i, n in enumerate(bars.counts):
        row = [bars.labels[i]]
        if bars.chart != BARS:
            row += [bars.lows[i], bars.highs[i]]
        row += [n, round(100.0 * n / total, 4)]
        if selected is not None:
            row.append(selected[i])
        if bars.split is not None:
            row += list(bars.split[i])
        rows.append(row)
    return rows


# ============================================================================
# HELPERS
# ============================================================================

def _empty_codes(n):
    return np.zeros(n, np.int32) if _np() else array("i", bytes(4 * n))


def _filled_codes(n, value):
    if _np():
        return np.full(n, value, np.int32)
    return array("i", [value]) * n


def _bincount(codes, k):
    if _np() is not None and not isinstance(codes, array):
        c = np.asarray(codes)
        c = c[(c >= 0) & (c < k)]
        return np.bincount(c, minlength=k)[:k].tolist()
    counts = [0] * k
    for c in codes:
        if 0 <= c < k:
            counts[c] += 1
    return counts


def _map_codes(col, mapping):
    """Bar of each record from its category code (*mapping*[code + 2])."""
    if _np():
        return np.asarray(mapping, dtype=np.int32)[col.np("codes") + 2]
    return array("i", (mapping[c + 2] for c in col.codes))


def _remap(codes, mapping):
    if _np() is not None and not isinstance(codes, array):
        m = np.asarray(mapping, dtype=np.int32)
        c = np.asarray(codes)
        return np.where(c >= 0, m[np.clip(c, 0, len(mapping) - 1)], -1).astype(np.int32)
    return array("i", (mapping[c] if c >= 0 else -1 for c in codes))
