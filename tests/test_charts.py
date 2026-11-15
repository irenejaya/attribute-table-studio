# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Unit tests for the Visualize Statistics engine (no QGIS needed).

Every test runs twice: with numpy and in plain Python.

Run from the repository root:  python -m unittest discover -s tests
"""

import datetime
import os
import random
import sys
import unittest
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import charts as C  # noqa: E402
from core import fields as F  # noqa: E402
from core import stats as S  # noqa: E402


def number_column(values, kind=F.REAL, fids=None, split=None, split_labels=None):
    col = C.Column(kind, "VALUE")
    nums = [v for v in values if v is not None]
    col.scope = len(values)
    col.missing = len(values) - len(nums)
    col.values = array("d", nums)
    col.fids = array("q", fids if fids is not None else range(1, len(nums) + 1))
    if split is not None:
        col.split = array("i", split)
        col.split_labels = split_labels
        col.split_counts = [sum(1 for c in split if c == k) for k in range(len(split_labels))]
    col.stats = C.statistics(col)
    return col


def text_column(values):
    col = C.Column(F.TEXT, "LAND_USE")
    index = {}
    for fid, v in enumerate(values, 1):
        col.scope += 1
        if v is None:
            col.missing += 1
            code = C.NULL_CODE
        else:
            code = index.get(v)
            if code is None:
                code = index[v] = len(col.counts)
                col.counts.append(0)
            col.counts[code] += 1
        col.fids.append(fid)
        col.codes.append(code)
    col.labels = [None] * len(index)
    for k, c in index.items():
        col.labels[c] = k
    col.stats = C.statistics(col)
    return col


def date_column(dates):
    col = C.Column(F.DATE, "SURVEY_DATE")
    col.scope = len(dates)
    for fid, d in enumerate(dates, 1):
        col.values.append(float(d.toordinal()))
        col.fids.append(fid)
    col.stats = C.statistics(col)
    return col


class _Both:
    """Mixin: run each test with numpy on and off."""

    def run(self, result=None):
        for use in ([True, False] if C.HAS_NUMPY else [False]):
            C.USE_NUMPY = use
            try:
                super().run(result)
            finally:
                C.USE_NUMPY = C.HAS_NUMPY


class NumberTests(_Both, unittest.TestCase):
    def setUp(self):
        rnd = random.Random(3)
        self.values = [round(rnd.lognormvariate(0, 0.5) * 600, 2) for _ in range(5000)]
        self.values += [None] * 25

    def test_statistics_match_explorer(self):
        col = number_column(self.values)
        prof = S.compute_profile(F.REAL, self.values)
        st = col.stats
        self.assertEqual(st["records"], prof.count)
        self.assertEqual(st["null"], prof.missing)
        self.assertEqual(st["min"], prof.minimum)
        self.assertEqual(st["max"], prof.maximum)
        self.assertAlmostEqual(st["sum"], prof.total, places=6)
        self.assertAlmostEqual(st["mean"], prof.mean, places=9)
        self.assertEqual(st["median"], prof.median)
        self.assertAlmostEqual(st["std"], prof.std, places=9)

    def test_moments(self):
        vals = [1.0, 2.0, 3.0, 4.0, 100.0]
        col = number_column(vals)
        n = len(vals)
        mean = sum(vals) / n
        m2 = sum((x - mean) ** 2 for x in vals) / n
        m3 = sum((x - mean) ** 3 for x in vals) / n
        m4 = sum((x - mean) ** 4 for x in vals) / n
        self.assertAlmostEqual(col.stats["skewness"], m3 / m2 ** 1.5, places=9)
        self.assertAlmostEqual(col.stats["kurtosis"], m4 / m2 ** 2, places=9)

    def test_constant_has_no_moments(self):
        col = number_column([5.0] * 10)
        self.assertEqual(col.stats["std"], 0.0)
        self.assertIsNone(col.stats["skewness"])

    def test_histogram_matches_explorer_bins(self):
        col = number_column(self.values)
        nums = sorted(v for v in self.values if v is not None)
        expected, _int = S.histogram(nums, 20)
        bars = C.histogram(col, 20)
        self.assertEqual(bars.counts, [c for _lo, _hi, c in expected])
        self.assertEqual(sum(bars.counts), len(nums))
        self.assertAlmostEqual(bars.lows[0], nums[0])
        self.assertAlmostEqual(bars.highs[-1], nums[-1])

    def test_rebinning_keeps_every_record(self):
        col = number_column(self.values)
        for bins in (4, 7, 33, 100):
            self.assertEqual(sum(C.histogram(col, bins).counts), 5000)

    def test_integer_bars(self):
        col = number_column([1, 2, 2, 3, 3, 3, 10], kind=F.INTEGER)
        self.assertEqual(C.default_bins(7, F.INTEGER, 1, 10), 10)
        bars = C.histogram(col, 10)
        self.assertTrue(bars.integer)
        self.assertEqual(bars.counts, [1, 2, 3, 0, 0, 0, 0, 0, 0, 1])

    def test_single_value(self):
        bars = C.histogram(number_column([7.0, 7.0, 7.0]), 20)
        self.assertEqual(bars.counts, [3])

    def test_empty(self):
        col = number_column([None, None])
        self.assertEqual(col.stats["null"], 2)
        self.assertEqual(len(C.histogram(col, 10)), 0)

    def test_large_median(self):
        rnd = random.Random(5)
        vals = [rnd.random() * 1000 for _ in range(250001)]
        self.assertEqual(C.median(array("d", vals)), sorted(vals)[125000])
        vals.append(-1.0)
        s = sorted(vals)
        self.assertEqual(C.median(array("d", vals)), (s[125000] + s[125001]) / 2)

    def test_selection_and_click(self):
        col = number_column([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
                            fids=[10, 11, 12, 13, 14, 15, 16, 17])
        bars = C.histogram(col, 4)
        self.assertEqual(bars.counts, [2, 2, 2, 2])
        per_bar, per_series = C.selected_counts(col, bars, {10, 13, 17, 99})
        self.assertEqual(per_bar, [1, 1, 0, 1])
        self.assertIsNone(per_series)
        self.assertEqual(sorted(C.fids_of(col, bars, [0, 3])), [10, 11, 16, 17])

    def test_split(self):
        split = [0, 1, 0, 1, 0, 2, 2, 2]
        col = number_column([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
                            fids=list(range(8)), split=split,
                            split_labels=["Private", "Council", "State"])
        bars = C.apply_split(col, C.histogram(col, 2))
        # most records first, equal counts by name
        self.assertEqual(bars.series, ["Private", "State", "Council"])
        self.assertEqual(bars.split, [[2, 0, 2], [1, 3, 0]])
        per_bar, per_series = C.selected_counts(col, bars, {0, 5})
        self.assertEqual(per_bar, [1, 1])
        self.assertEqual(per_series, [[1, 0, 0], [0, 1, 0]])
        self.assertEqual(sorted(C.fids_of(col, bars, [1], series=1)), [5, 6, 7])

    def test_split_other(self):
        labels = [f"S{k}" for k in range(10)]
        split = list(range(10)) * 3
        col = number_column([float(i) for i in range(30)], fids=list(range(30)),
                            split=split, split_labels=labels)
        bars = C.apply_split(col, C.histogram(col, 3))
        self.assertEqual(len(bars.series), C.SPLIT_SERIES + 1)
        self.assertEqual(bars.series[-1], "Other")
        self.assertEqual(sum(sum(r) for r in bars.split), 30)


class NumberBarTests(_Both, unittest.TestCase):
    """Numbers as a bar chart: one bar per value, NULL records kept apart."""

    def setUp(self):
        vals = [3, 1, 2, 2, 3, 3, 10, None, None]
        self.col = number_column(vals, kind=F.INTEGER, fids=[11, 12, 13, 14, 15, 16, 17])
        self.col.null_fids = array("q", [18, 19])

    def test_one_bar_per_value(self):
        bars = C.categories(self.col, "count", top=None)
        self.assertEqual(bars.labels, ["3", "2", "1", "10"])
        self.assertEqual(bars.counts, [3, 2, 1, 1])
        bars = C.categories(self.col, "az", top=None)          # by value, not text
        self.assertEqual(bars.labels, ["1", "2", "3", "10"])
        bars = C.categories(self.col, "za", top=2)
        self.assertEqual(bars.labels, ["3", "2", "Other"])
        self.assertEqual(bars.counts, [3, 2, 2])

    def test_null_bar_selects_null_records(self):
        bars = C.categories(self.col, "count", top=None, null_bar=True)
        self.assertEqual(bars.labels[-1], "NULL")
        self.assertEqual(bars.counts[-1], 2)
        self.assertEqual(sorted(C.fids_of(self.col, bars, [bars.null_bar])), [18, 19])
        self.assertEqual(sorted(C.fids_of(self.col, bars, [0])), [11, 15, 16])
        per_bar, _s = C.selected_counts(self.col, bars, {11, 19})
        self.assertEqual(per_bar[0], 1)
        self.assertEqual(per_bar[bars.null_bar], 1)

    def test_null_bar_with_split(self):
        col = number_column([1.0, 1.0, 2.0], fids=[1, 2, 3], split=[0, 1, 0],
                            split_labels=["A", "B"])
        col.null_fids = array("q", [4, 5])
        col.null_split = array("i", [1, 1])
        col.missing = 2
        col.split_counts = [2, 3]
        bars = C.apply_split(col, C.categories(col, "az", top=None, null_bar=True))
        self.assertEqual(bars.series, ["B", "A"])
        self.assertEqual(bars.split[bars.null_bar], [2, 0])
        self.assertEqual([sum(r) for r in bars.split], bars.counts)
        self.assertEqual(sorted(C.fids_of(col, bars, [bars.null_bar], series=0)), [4, 5])
        per_bar, per_series = C.selected_counts(col, bars, {5})
        self.assertEqual(per_series[bars.null_bar], [1, 0])

    def test_reals_label_and_histogram_untouched(self):
        col = number_column([0.5, 0.5, 1.25])
        bars = C.categories(col, "count", top=None)
        self.assertEqual(bars.labels, ["0.5", "1.25"])
        self.assertEqual(sum(C.histogram(col, 4).counts), 3)

    def test_chart_types(self):
        self.assertEqual(C.default_chart_type(F.TEXT), "bar")
        self.assertEqual(C.default_chart_type(F.REAL), "histogram")
        self.assertEqual(C.default_chart_type(F.DATE), "histogram")
        self.assertEqual(C.bars_kind(F.TEXT, "histogram"), C.BARS)
        self.assertEqual(C.bars_kind(F.REAL, "bar"), C.BARS)
        self.assertEqual(C.bars_kind(F.REAL, "histogram"), C.HISTOGRAM)
        self.assertEqual(C.bars_kind(F.DATE, "bar"), C.TIME)


class TextTests(_Both, unittest.TestCase):
    def setUp(self):
        self.values = (["Residential"] * 50 + ["Commercial"] * 20 + ["Industrial"] * 10
                       + ["Rural"] * 5 + ["Park"] * 3 + [None] * 4)
        self.col = text_column(self.values)

    def test_stats(self):
        st = self.col.stats
        self.assertEqual(st["records"], 92)
        self.assertEqual(st["null"], 4)
        self.assertEqual(st["distinct"], 5)
        self.assertEqual(st["most"], ("Residential", 50))
        self.assertEqual(st["least"], ("Park", 3))

    def test_top_and_other(self):
        bars = C.categories(self.col, "count", top=3)
        self.assertEqual(bars.labels, ["Residential", "Commercial", "Industrial", "Other"])
        self.assertEqual(bars.counts, [50, 20, 10, 8])
        self.assertEqual(bars.other, 3)
        self.assertEqual(len(C.fids_of(self.col, bars, [3])), 8)

    def test_null_bar_and_sort(self):
        bars = C.categories(self.col, "az", top=None, null_bar=True)
        self.assertEqual(bars.labels, ["Commercial", "Industrial", "Park", "Residential",
                                       "Rural", "NULL"])
        self.assertEqual(bars.counts[-1], 4)
        self.assertEqual(sorted(C.fids_of(self.col, bars, [5])), [89, 90, 91, 92])
        za = C.categories(self.col, "za", top=None)
        self.assertEqual(za.labels[0], "Rural")

    def test_sorted_top_keeps_most_common(self):
        bars = C.categories(self.col, "az", top=2)
        self.assertEqual(bars.labels, ["Commercial", "Residential", "Other"])

    def test_selected(self):
        bars = C.categories(self.col, "count", top=10)
        per_bar, _s = C.selected_counts(self.col, bars, {1, 2, 51, 92})
        self.assertEqual(per_bar[:2], [2, 1])
        self.assertEqual(sum(per_bar), 3)            # 92 is NULL: in no bar


class DateTests(_Both, unittest.TestCase):
    def setUp(self):
        start = datetime.date(2023, 1, 30)
        self.dates = [start + datetime.timedelta(days=k * 3) for k in range(200)]
        self.col = date_column(self.dates)

    def test_auto_interval(self):
        self.assertEqual(C.auto_interval(0, 90), "day")
        self.assertEqual(C.auto_interval(0, 600), "week")
        self.assertEqual(C.auto_interval(0, 3000), "month")
        self.assertEqual(C.auto_interval(0, 9000), "quarter")
        self.assertEqual(C.auto_interval(0, 20000), "year")

    def test_months(self):
        bars = C.periods(self.col, "month")
        self.assertEqual(bars.labels[0], "2023-01")
        months = {}
        for d in self.dates:
            months[f"{d.year}-{d.month:02d}"] = months.get(f"{d.year}-{d.month:02d}", 0) + 1
        self.assertEqual(dict(zip(bars.labels, bars.counts)), months)
        self.assertEqual(sum(bars.counts), 200)

    def test_weeks_quarters_years(self):
        for interval in ("week", "quarter", "year", "day"):
            bars = C.periods(self.col, interval)
            self.assertEqual(sum(bars.counts), 200, interval)
        weeks = C.periods(self.col, "week")
        self.assertEqual(weeks.labels[0], "Week of 2023-01-30")      # a Monday
        q = C.periods(self.col, "quarter")
        self.assertEqual(q.labels[:2], ["2023 Q1", "2023 Q2"])

    def test_empty_periods(self):
        dates = [datetime.date(2020, 1, 5), datetime.date(2020, 4, 5)]
        col = date_column(dates)
        self.assertEqual(C.periods(col, "month").counts, [1, 0, 0, 1])
        bars = C.periods(col, "month", show_empty=False)
        self.assertEqual(bars.labels, ["2020-01", "2020-04"])
        self.assertEqual(sorted(C.fids_of(col, bars, [1])), [2])

    def test_too_many_days(self):
        dates = [datetime.date(2000, 1, 1), datetime.date(2010, 1, 1)]
        bars = C.periods(date_column(dates), "day")
        self.assertEqual(bars.interval, "week")
        self.assertIn("more than", bars.note)

    def test_busiest(self):
        bars = C.periods(self.col, "month")
        label, n = C.busiest(bars)
        self.assertEqual(n, max(bars.counts))


class ChartKindTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(C.chart_kind(F.REAL), C.HISTOGRAM)
        self.assertEqual(C.chart_kind(F.TIME), C.HISTOGRAM)
        self.assertEqual(C.chart_kind(F.DATE), C.TIME)
        self.assertEqual(C.chart_kind(F.TEXT), C.BARS)
        self.assertEqual(C.chart_kind(F.BOOL), C.BARS)

    def test_bool_keys(self):
        self.assertEqual(C._bool_key(True), "true")
        self.assertEqual(C._bool_key(0), "false")
        self.assertEqual(C._bool_key(" Yes "), "true")
        self.assertIsNone(C._bool_key(None))
        self.assertEqual(C._bool_key("maybe"), "")


if __name__ == "__main__":
    unittest.main()
