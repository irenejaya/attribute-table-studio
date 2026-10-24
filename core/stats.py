# -*- coding: utf-8 -*-
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
from collections import Counter

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
