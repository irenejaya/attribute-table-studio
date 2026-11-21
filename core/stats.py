# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Exact column profiles for Attribute Explorer.

:func:`compute_profile` takes the raw values of one field for every record
in the current scope and returns a :class:`Profile`.  It is pure Python and
touches no QGIS object, so it is safe inside a background task.

Rules (see the plan, "Column statistics and scope rules"):

- missing = NULL count; missing % and distinct % use the scoped record count
- distinct counts non-NULL raw values (no rounding, no alias text)
- category shares use the non-NULL count
- numeric statistics ignore NULL but keep zero; NaN / infinity are reported
  separately as *invalid* and excluded from the statistics
- histograms state their bin ranges; the maximum falls in the last bin
"""

import bisect
import heapq
import math
from array import array
from collections import Counter
from itertools import chain

from . import compact as CP
from . import fields as F

DEFAULT_BINS = 20
FREQ_LIMIT = 20000          # longest frequency list kept for the expanded view
CATEGORY_LIMIT = 50         # up to this many distinct values -> category view
TOP_SHARE = 0.5             # ... or when the top categories cover >= 50%


class Profile:
    """Statistics of one field over one record scope."""

    def __init__(self, kind, count):
        self.kind = kind
        self.count = count            # records in scope
        self.missing = 0              # NULL
        self.distinct = 0             # distinct non-NULL values
        self.invalid = 0              # non-NULL values that cannot be used
        # numeric
        self.valid = 0
        self.minimum = None
        self.maximum = None
        self.total = None
        self.mean = None
        self.median = None
        self.std = None
        # temporal
        self.earliest = None          # raw value
        self.latest = None
        # numeric + temporal: [(low, high, count)], last bin includes high
        self.bins = []
        self.integer_bins = False     # one bin per integer / day value
        # boolean
        self.true_count = 0
        self.false_count = 0
        # text / other
        self.frequencies = []         # [(text, count)] most common first
        self.frequencies_truncated = False
        self.blank = 0                # empty strings
        self.whitespace = 0           # whitespace-only strings

    # ------------------------------------------------------------------
    @property
    def non_null(self):
        return self.count - self.missing

    @property
    def constant(self):
        return self.distinct == 1

    def pct(self, n, of=None):
        """Percentage of *n* relative to *of* (default: scoped records)."""
        base = self.count if of is None else of
        return (100.0 * n / base) if base else 0.0

    def is_categorical(self):
        """Text profile reads best as a category list (not one big count)."""
        if self.kind not in (F.TEXT, F.OTHER) or not self.frequencies:
            return False
        if self.distinct <= CATEGORY_LIMIT:
            return True
        top = sum(c for _v, c in self.frequencies[:3])
        return self.non_null > 0 and top / self.non_null >= TOP_SHARE

    def top_categories(self, n=3):
        """[(text, count, share of non-NULL %)] plus a trailing
        ("Other classes", count, share) row when more values exist."""
        nn = self.non_null
        rows = [(v, c, self.pct(c, nn)) for v, c in self.frequencies[:n]]
        rest = nn - sum(c for _v, c, _p in rows)
        if rest > 0:
            rows.append((None, rest, self.pct(rest, nn)))
        return rows


# ============================================================================
# ENTRY POINT
# ============================================================================

def compute_profile(kind, values, bins=DEFAULT_BINS, freq_limit=FREQ_LIMIT):
    """Profile of *values* (raw attribute values, NULL included)."""
    profile = Profile(kind, len(values))
    if kind in F.NUMERIC:
        _numeric(profile, values, bins)
    elif kind in F.TEMPORAL:
        _temporal(profile, values, bins)
    elif kind == F.BOOL:
        _boolean(profile, values)
    else:
        _categorical(profile, values, freq_limit)
    return profile


# ============================================================================
# KINDS
# ============================================================================

def _numeric(p, values, bins):
    nums = []
    append = nums.append
    isfinite = math.isfinite
    missing = invalid = 0
    for v in values:
        t = type(v)
        if t is float:
            if isfinite(v):
                append(v)
            else:
                invalid += 1
        elif t is int:
            append(v)
        elif v is None or F.is_null(v):
            missing += 1
        else:
            try:
                x = float(v)
            except (TypeError, ValueError):
                invalid += 1
                continue
            if isfinite(x):
                append(x)
            else:
                invalid += 1
    p.missing = missing
    p.invalid = invalid
    p.valid = n = len(nums)
    if not n:
        return
    nums.sort()
    p.minimum, p.maximum = nums[0], nums[-1]
    p.total = math.fsum(nums)
    p.mean = p.total / n
    mid = n // 2
    p.median = nums[mid] if n % 2 else (nums[mid - 1] + nums[mid]) / 2.0
    mean = p.mean
    p.std = math.sqrt(math.fsum((x - mean) * (x - mean) for x in nums) / n)
    p.distinct = len(set(nums))
    p.bins, p.integer_bins = histogram(
        nums, bins, integer=(p.kind == F.INTEGER))


def _temporal(p, values, bins):
    keys, originals = [], []
    missing = invalid = 0
    for v in values:
        if v is None or F.is_null(v):
            missing += 1
            continue
        k = F.temporal_key(v)
        if k is None:
            invalid += 1
        else:
            keys.append(k)
            originals.append(v)
    p.missing = missing
    p.invalid = invalid
    p.valid = len(keys)
    if not keys:
        return
    lo_i = min(range(len(keys)), key=keys.__getitem__)
    hi_i = max(range(len(keys)), key=keys.__getitem__)
    p.earliest, p.latest = originals[lo_i], originals[hi_i]
    p.distinct = len(set(keys))
    keys.sort()
    p.minimum, p.maximum = keys[0], keys[-1]
    p.bins, p.integer_bins = histogram(keys, bins, integer=(p.kind == F.DATE))


def _boolean(p, values):
    true = false = missing = invalid = 0
    for v in values:
        if v is True:
            true += 1
        elif v is False:
            false += 1
        elif v is None or F.is_null(v):
            missing += 1
        elif isinstance(v, (int, float)) and v in (0, 1):
            if v:
                true += 1
            else:
                false += 1
        elif isinstance(v, str) and v.strip().lower() in (
                "true", "t", "yes", "y", "1", "false", "f", "no", "n", "0"):
            if v.strip().lower() in ("true", "t", "yes", "y", "1"):
                true += 1
            else:
                false += 1
        else:
            invalid += 1
    p.true_count, p.false_count = true, false
    p.missing, p.invalid = missing, invalid
    p.distinct = int(true > 0) + int(false > 0)


def _categorical(p, values, freq_limit):
    texts = []
    append = texts.append
    missing = 0
    for v in values:
        if type(v) is str:
            append(v)
        elif v is None or F.is_null(v):
            missing += 1
        else:
            append(F.display_value(v))
    p.missing = missing
    counter = Counter(texts)
    p.distinct = len(counter)
    p.blank = counter.get("", 0)
    p.whitespace = sum(c for s, c in counter.items() if s and not s.strip())
    order = (lambda kv: (-kv[1], kv[0]))
    if len(counter) <= freq_limit:
        p.frequencies = sorted(counter.items(), key=order)
    else:
        p.frequencies = heapq.nsmallest(freq_limit, counter.items(), key=order)
        p.frequencies_truncated = True


# ============================================================================
# BUILDERS: the same statistics, taken value by value as a scan reads them
# ============================================================================
#
# compute_profile() needs every value of a column as a Python object (40 -
# 100 bytes each), so a big layer could only be profiled one column per
# read.  A builder keeps numbers and dates as 8-byte values and counts text
# and yes / no values as they come, so all the shown columns share one read
# (see plan_reads).  The results are exactly those of compute_profile(),
# which stays as the reference the unit tests compare with.

READ_BUDGET = 512 * 2 ** 20      # bytes of numbers and dates kept by one read
TEXT_LIMIT = 2000000             # distinct texts kept by one read, all columns
_CHUNK = 262144                  # values turned into Python numbers at a time


def builder(kind):
    """A builder for a field of *kind*: ``add(value)`` per record, then
    ``profile()``."""
    if kind in F.NUMERIC:
        return NumberBuilder(kind)
    if kind in F.TEMPORAL:
        return DateBuilder(kind)
    if kind == F.BOOL:
        return BoolBuilder(kind)
    return TextBuilder(kind)


def value_bytes(kind, records):
    """Memory a builder of *kind* keeps for *records* values, as far as it
    can be known before reading (text depends on its distinct values)."""
    return 8 * records if (kind in F.NUMERIC or kind in F.TEMPORAL) else 0


def take_read(order, kinds, records, alone=(), budget=READ_BUDGET):
    """(columns of the next read, the rest): the first columns of *order*
    whose numbers and dates fit *budget* together (at least one column; a
    column in *alone* is read on its own)."""
    if not order:
        return [], []
    first = order[0]
    if first in alone:
        return [first], list(order[1:])
    chunk, rest, used = [], [], 0
    for logical in order:
        if logical in alone:
            rest.append(logical)
            continue
        cost = value_bytes(kinds[logical], records)
        if chunk and cost and used + cost > budget:     # text, yes / no always join
            rest.append(logical)
            continue
        chunk.append(logical)
        used += cost
    return chunk, rest


def shed(builders, limit=TEXT_LIMIT):
    """While the text columns of one read keep more than *limit* distinct
    values together, stop the biggest (it is read again on its own); a
    read never stops its last column.  True when one was stopped."""
    stopped = False
    while True:
        live = [b for b in builders if not b.dropped]
        texts = [b for b in live if isinstance(b, TextBuilder)]
        if len(live) < 2 or not texts or sum(len(b.counts) for b in texts) <= limit:
            return stopped
        biggest = max(texts, key=lambda b: len(b.counts))
        biggest.drop()
        stopped = True


class _Builder:
    dropped = False

    def __init__(self, kind):
        self.kind = kind
        self.missing = 0
        self.invalid = 0

    def drop(self):
        """Stop keeping values (too big to share this read)."""
        self.dropped = True


class NumberBuilder(_Builder):
    """Numbers as 8-byte values: whole numbers of an integer field as
    integers (exact, also beyond 2**53), others as doubles."""

    def __init__(self, kind):
        super().__init__(kind)
        self.values = array("q" if kind == F.INTEGER else "d")

    def _widen(self, typecode):
        """Doubles for a float in an integer field, a Python list for a
        number beyond 8 bytes."""
        if typecode == "d":
            self.values = array("d", self.values)
        else:
            self.values = list(self.values)

    def _put(self, v):
        try:
            self.values.append(v)
        except OverflowError:
            self._widen("list")
            self.values.append(v)
        except TypeError:                # a float in an array of integers
            self._widen("d")
            self._put(v)

    def add(self, v):
        t = type(v)
        if t is float:
            if v - v == 0.0:             # finite (NaN and infinity are not)
                if type(self.values) is array and self.values.typecode == "q":
                    self._widen("d")
                self._put(v)
            else:
                self.invalid += 1
        elif t is int:
            self._put(v)
        elif v is None or F.is_null(v):
            self.missing += 1
        else:
            try:
                x = float(v)
            except (TypeError, ValueError):
                self.invalid += 1
                return
            if math.isfinite(x):
                if type(self.values) is array and self.values.typecode == "q":
                    self._widen("d")
                self._put(x)
            else:
                self.invalid += 1

    def drop(self):
        super().drop()
        self.values = array("d")

    def profile(self, bins=DEFAULT_BINS):
        values = self.values
        n = len(values)
        p = Profile(self.kind, n + self.missing + self.invalid)
        p.missing, p.invalid, p.valid = self.missing, self.invalid, n
        if not n:
            return p
        a = _view(values)
        if a is None:
            nums = sorted(values)
            _numbers_from_sorted(p, nums, bins)
        else:
            a.sort()                     # in place: the builder is done
            _numbers_from_sorted_np(p, a, bins)
        self.values = None
        return p


def _date_reader(sample):
    """A reader for values of the type of *sample*: value -> its
    temporal_key, None for NULL, False when it is not a valid date.  The
    QDate and QDateTime a layer gives get a short path (about 5 times
    faster); anything else goes through is_null() and temporal_key()."""
    if hasattr(sample, "toJulianDay") and hasattr(sample, "isValid") \
            and not hasattr(sample, "toMSecsSinceEpoch"):          # QDate
        offset = F._JULIAN_OFFSET

        def read(v):
            if v.isNull():
                return None
            if not v.isValid():
                return False
            return float(v.toJulianDay() - offset)
        return read
    if hasattr(sample, "toMSecsSinceEpoch") and hasattr(sample, "isValid"):  # QDateTime
        epoch = F._EPOCH_ORDINAL

        def read(v):
            if v.isNull():
                return None
            if not v.isValid():
                return False
            return epoch + v.toMSecsSinceEpoch() / 86400000.0
        return read

    def read(v):
        if v is None or F.is_null(v):
            return None
        k = F.temporal_key(v)
        return False if k is None else k
    return read


class DateBuilder(_Builder):
    """Dates, date-times and times as their sortable numbers (8 bytes),
    plus the original earliest and latest values."""

    def __init__(self, kind):
        super().__init__(kind)
        self.keys = array("d")
        self.lo = self.hi = None
        self.earliest = self.latest = None
        self._readers = {}

    def add(self, v):
        read = self._readers.get(type(v))
        if read is None:
            read = self._readers[type(v)] = _date_reader(v)
        k = read(v)
        if k is None:
            self.missing += 1
            return
        if k is False:
            self.invalid += 1
            return
        self.keys.append(k)
        if self.lo is None or k < self.lo:
            self.lo, self.earliest = k, v
        if self.hi is None or k > self.hi:
            self.hi, self.latest = k, v

    def drop(self):
        super().drop()
        self.keys = array("d")

    def profile(self, bins=DEFAULT_BINS):
        keys = self.keys
        n = len(keys)
        p = Profile(self.kind, n + self.missing + self.invalid)
        p.missing, p.invalid, p.valid = self.missing, self.invalid, n
        if not n:
            return p
        p.earliest, p.latest = self.earliest, self.latest
        integer = self.kind == F.DATE
        a = _view(keys)
        if a is None:
            p.distinct = len(set(keys))
            ordered = sorted(keys)
            p.minimum, p.maximum = ordered[0], ordered[-1]
            p.bins, p.integer_bins = histogram(ordered, bins, integer=integer)
        else:
            a.sort()
            p.distinct = _np_distinct(a)
            p.minimum, p.maximum = a[0].item(), a[-1].item()
            p.bins, p.integer_bins = _np_histogram(a, bins, integer)
        self.keys = None
        return p


_TRUE = ("true", "t", "yes", "y", "1")
_FALSE = ("false", "f", "no", "n", "0")


class BoolBuilder(_Builder):
    def __init__(self, kind):
        super().__init__(kind)
        self.true = 0
        self.false = 0

    def add(self, v):
        if v is True:
            self.true += 1
        elif v is False:
            self.false += 1
        elif v is None or F.is_null(v):
            self.missing += 1
        elif isinstance(v, (int, float)) and v in (0, 1):
            if v:
                self.true += 1
            else:
                self.false += 1
        elif isinstance(v, str) and v.strip().lower() in _TRUE + _FALSE:
            if v.strip().lower() in _TRUE:
                self.true += 1
            else:
                self.false += 1
        else:
            self.invalid += 1

    def profile(self, bins=DEFAULT_BINS):
        p = Profile(self.kind, self.true + self.false + self.missing + self.invalid)
        p.true_count, p.false_count = self.true, self.false
        p.missing, p.invalid = self.missing, self.invalid
        p.distinct = int(self.true > 0) + int(self.false > 0)
        return p


class TextBuilder(_Builder):
    """Text (and any other kind) counted as it is read: only the distinct
    values are kept."""

    def __init__(self, kind):
        super().__init__(kind)
        self.counts = {}

    def add(self, v):
        if type(v) is str:
            key = v
        elif v is None or F.is_null(v):
            self.missing += 1
            return
        else:
            key = F.display_value(v)
        counts = self.counts
        counts[key] = counts.get(key, 0) + 1

    def drop(self):
        super().drop()
        self.counts = {}

    def profile(self, bins=DEFAULT_BINS, freq_limit=FREQ_LIMIT):
        counter = self.counts
        p = Profile(self.kind, sum(counter.values()) + self.missing)
        p.missing = self.missing
        p.distinct = len(counter)
        p.blank = counter.get("", 0)
        p.whitespace = sum(c for s, c in counter.items() if s and not s.strip())
        order = (lambda kv: (-kv[1], kv[0]))
        if len(counter) <= freq_limit:
            p.frequencies = sorted(counter.items(), key=order)
        else:
            p.frequencies = heapq.nsmallest(freq_limit, counter.items(), key=order)
            p.frequencies_truncated = True
        self.counts = None
        return p


# ---- numbers: the statistics of compute_profile on sorted values ------------

def _view(values):
    """numpy array over the builder's array (no copy), or None."""
    xp = CP._np()
    if xp is None or type(values) is not array:
        return None
    return xp.frombuffer(values, dtype=xp.int64 if values.typecode == "q"
                         else xp.float64)


def _numbers_from_sorted(p, nums, bins):
    n = len(nums)
    p.minimum, p.maximum = nums[0], nums[-1]
    p.total = math.fsum(nums)
    p.mean = p.total / n
    mid = n // 2
    p.median = nums[mid] if n % 2 else (nums[mid - 1] + nums[mid]) / 2.0
    mean = p.mean
    p.std = math.sqrt(math.fsum((x - mean) * (x - mean) for x in nums) / n)
    p.distinct = len(set(nums))
    p.bins, p.integer_bins = histogram(nums, bins, integer=(p.kind == F.INTEGER))


def _chunks(a):
    """The values of *a* as Python numbers, a chunk at a time (one exact
    math.fsum over all of them, without a list of every value)."""
    for i in range(0, a.size, _CHUNK):
        yield a[i:i + _CHUNK].tolist()


def _squares(a, mean):
    np = CP.np
    for i in range(0, a.size, _CHUNK):
        d = a[i:i + _CHUNK].astype(np.float64) - mean
        d *= d
        yield d.tolist()


def _numbers_from_sorted_np(p, a, bins):
    n = a.size
    p.minimum, p.maximum = a[0].item(), a[-1].item()
    p.total = math.fsum(chain.from_iterable(_chunks(a)))
    p.mean = p.total / n
    mid = n // 2
    p.median = a[mid].item() if n % 2 else (a[mid - 1].item() + a[mid].item()) / 2.0
    p.std = math.sqrt(math.fsum(chain.from_iterable(_squares(a, p.mean))) / n)
    p.distinct = _np_distinct(a)
    p.bins, p.integer_bins = _np_histogram(a, bins, p.kind == F.INTEGER)


def _np_distinct(a):
    """Distinct values of sorted *a* (as len(set()): 0.0 and -0.0 are one)."""
    if not a.size:
        return 0
    return 1 + int((a[1:] != a[:-1]).sum())


def _np_left(a, x):
    """How many values of sorted *a* are smaller than *x* (bisect_left),
    exact also for whole numbers compared with a decimal edge."""
    np = CP.np
    if a.dtype.kind == "f":
        return int(np.searchsorted(a, x, side="left"))
    t = math.ceil(x)                     # integers: v < x  <=>  v < ceil(x)
    info = np.iinfo(a.dtype)
    if t > info.max:
        return a.size
    if t <= info.min:
        return 0
    return int(np.searchsorted(a, t, side="left"))


def _np_histogram(a, bins=DEFAULT_BINS, integer=False):
    """histogram() over a sorted numpy array (the same bins)."""
    n = a.size
    lo, hi = a[0].item(), a[-1].item()
    if lo == hi:
        return [(lo, hi, n)], True
    if integer and float(lo).is_integer() and float(hi).is_integer() \
            and (hi - lo + 1) <= bins:
        out = []
        k = lo
        while k <= hi:
            out.append((k, k, _np_left(a, k + 1) - _np_left(a, k)))
            k += 1
        return out, True
    width = (hi - lo) / float(bins)
    edges = [lo + i * width for i in range(bins)] + [hi]
    out = []
    start = 0
    for i in range(bins):
        end = n if i == bins - 1 else max(_np_left(a, edges[i + 1]), start)
        out.append((edges[i], edges[i + 1], end - start))
        start = end
    return out, False


# ============================================================================
# HISTOGRAM
# ============================================================================

def histogram(sorted_values, bins=DEFAULT_BINS, integer=False):
    """Equal-width bins over *sorted_values*.

    Returns (bins, integer_bins) where bins is [(low, high, count)].  Bins
    are [low, high) except the last, which is [low, high] so the maximum
    is always counted.  For integer data spanning no more than *bins*
    values, each integer gets its own bin (low == high).
    """
    if not sorted_values:
        return [], False
    lo, hi = sorted_values[0], sorted_values[-1]
    n = len(sorted_values)
    if lo == hi:
        return [(lo, hi, n)], True
    left = bisect.bisect_left
    if integer and float(lo).is_integer() and float(hi).is_integer() \
            and (hi - lo + 1) <= bins:
        out = []
        k = lo
        while k <= hi:
            a = left(sorted_values, k)
            b = left(sorted_values, k + 1)
            out.append((k, k, b - a))
            k += 1
        return out, True
    width = (hi - lo) / float(bins)
    edges = [lo + i * width for i in range(bins)] + [hi]
    out = []
    start = 0
    for i in range(bins):
        end = n if i == bins - 1 else left(sorted_values, edges[i + 1], start)
        out.append((edges[i], edges[i + 1], end - start))
        start = end
    return out, False


def bin_label(low, high, last, integer_bins, fmt):
    """Hover text for one bin, e.g. '[26.43, 29.07)'."""
    if integer_bins or low == high:
        return fmt(low)
    return f"[{fmt(low)}, {fmt(high)}{']' if last else ')'}"
