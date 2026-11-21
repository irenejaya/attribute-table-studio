# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""The statistics builders (one read, compact values) against
compute_profile (today's statistics): every number must match exactly.

Every test runs twice: with numpy and in plain Python.

Run from the repository root:  python -m unittest discover -s tests
"""

import datetime
import math
import os
import random
import sys
import unittest
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import compact as CP  # noqa: E402
from core import fields as F  # noqa: E402
from core import stats as S  # noqa: E402

FIELDS = ("kind", "count", "missing", "distinct", "invalid", "valid", "minimum",
          "maximum", "total", "mean", "median", "std", "earliest", "latest", "bins",
          "integer_bins", "true_count", "false_count", "frequencies",
          "frequencies_truncated", "blank", "whitespace")


def built(kind, values, **kw):
    b = S.builder(kind)
    for v in values:
        b.add(v)
    return b.profile(**kw)


class Both(unittest.TestCase):
    """Runs each test with numpy and without."""

    def run(self, result=None):
        for use in ([True, False] if CP.HAS_NUMPY else [False]):
            CP.USE_NUMPY = use
            try:
                super().run(result)
            finally:
                CP.USE_NUMPY = CP.HAS_NUMPY

    def same(self, kind, values, **kw):
        ref = S.compute_profile(kind, list(values), **kw)
        new = built(kind, values, **kw)
        for name in FIELDS:
            a, b = getattr(ref, name), getattr(new, name)
            if isinstance(a, float) and isinstance(b, float) and math.isnan(a):
                self.assertTrue(math.isnan(b), name)
                continue
            self.assertEqual(a, b, f"{kind} {name} (numpy={CP.USE_NUMPY})")
        return new


class Numbers(Both):
    def test_random_decimals(self):
        rnd = random.Random(1)
        for trial in range(6):
            values = []
            for _ in range(rnd.choice([1, 2, 3, 57, 1000, 5000])):
                r = rnd.random()
                if r < 0.05:
                    values.append(None)
                elif r < 0.07:
                    values.append(rnd.choice([float("nan"), float("inf"), -float("inf")]))
                elif r < 0.09:
                    values.append(rnd.choice([0.0, -0.0]))
                elif r < 0.11:
                    values.append(rnd.choice(["3.5", "abc", "", "1e3"]))
                else:
                    values.append(rnd.lognormvariate(5, 2) * rnd.choice([1, -1]))
            self.same(F.REAL, values)

    def test_whole_numbers(self):
        rnd = random.Random(2)
        values = [rnd.randint(-50, 50) if rnd.random() > 0.1 else None
                  for _ in range(3000)]
        self.same(F.INTEGER, values)
        self.same(F.INTEGER, [rnd.randint(1, 9) for _ in range(500)])   # a bin per value
        self.same(F.INTEGER, [7] * 40)                                   # constant

    def test_big_integers_stay_exact(self):
        big = 2 ** 62
        values = [big + k for k in (5, 1, 3, 3, 9)] + [None]
        prof = self.same(F.INTEGER, values)
        self.assertEqual(prof.minimum, big + 1)
        self.assertEqual(prof.maximum, big + 9)
        self.assertEqual(prof.median, big + 3)
        self.assertEqual(prof.distinct, 4)

    def test_mixed_and_odd_values(self):
        self.same(F.REAL, [1, 2.5, True, False, None, "x", 3, 3.0])
        self.same(F.INTEGER, [1, 2, 2.5, None, 4])            # a decimal in an integer field
        self.same(F.REAL, [])
        self.same(F.REAL, [None, None])
        self.same(F.REAL, [float("nan")])
        self.same(F.INTEGER, [2 ** 70, 1, 2])                 # beyond 8 bytes


class Dates(Both):
    def test_dates(self):
        rnd = random.Random(3)
        base = datetime.date(2015, 1, 1)
        values = [base + datetime.timedelta(days=rnd.randint(0, 3650))
                  if rnd.random() > 0.05 else None for _ in range(4000)]
        self.same(F.DATE, values)
        self.same(F.DATE, [base + datetime.timedelta(days=k % 7) for k in range(100)])

    def test_datetimes_and_times(self):
        rnd = random.Random(4)
        start = datetime.datetime(2020, 1, 1)
        values = [start + datetime.timedelta(seconds=rnd.randint(0, 10 ** 8))
                  for _ in range(2000)] + [None, "not a date"]
        self.same(F.DATETIME, values)
        times = [datetime.time(rnd.randint(0, 23), rnd.randint(0, 59)) for _ in range(500)]
        self.same(F.TIME, times + [None])


class _QDate:
    """Stands for QGIS's QDate (what temporal_key() and the short path read)."""

    def __init__(self, jd, valid=True, null=False):
        self.jd, self.valid, self.null = jd, valid, null

    def isNull(self):
        return self.null

    def isValid(self):
        return self.valid and not self.null

    def toJulianDay(self):
        return self.jd


class _QDateTime(_QDate):
    def toMSecsSinceEpoch(self):
        return self.jd * 1000


class QtDates(Both):
    def test_qt_dates_take_the_short_path(self):
        rnd = random.Random(6)
        values = [_QDate(2459000 + rnd.randint(0, 900)) for _ in range(800)]
        values += [_QDate(0, null=True), _QDate(5, valid=False), None]
        self.same(F.DATE, values)
        stamps = [_QDateTime(rnd.randint(0, 10 ** 9)) for _ in range(400)]
        stamps += [_QDateTime(0, null=True), _QDateTime(1, valid=False)]
        self.same(F.DATETIME, stamps)


class Booleans(Both):
    def test_yes_no(self):
        values = [True, False, None, 1, 0, "yes", "N", " true ", "maybe", 2] * 30
        self.same(F.BOOL, values)


class Texts(Both):
    def test_text(self):
        rnd = random.Random(5)
        words = ["Residential", "Commercial", "", "  ", "Rural", "Open space"]
        values = [rnd.choice(words) if rnd.random() > 0.08 else None for _ in range(3000)]
        values += [12, 3.5, 2023]                        # other values as text
        self.same(F.TEXT, values)

    def test_many_values_truncated(self):
        values = [f"P-{k % 700:05d}" for k in range(3000)]
        b = S.builder(F.TEXT)
        for v in values:
            b.add(v)
        new = b.profile(freq_limit=50)
        ref = S.compute_profile(F.TEXT, values, freq_limit=50)
        self.assertEqual(ref.frequencies, new.frequencies)
        self.assertTrue(new.frequencies_truncated)
        self.assertEqual(new.distinct, 700)


class Planning(unittest.TestCase):
    def test_numbers_share_a_read_within_the_budget(self):
        kinds = [F.REAL, F.TEXT, F.DATE, F.INTEGER, F.BOOL, F.REAL]
        n = 10_000_000                                  # 80 MB a number column
        chunk, rest = S.take_read(list(range(6)), kinds, n, budget=200 * 2 ** 20)
        self.assertEqual(chunk, [0, 1, 2, 4])            # 2 numbers + text + yes / no
        self.assertEqual(rest, [3, 5])
        chunk, rest = S.take_read([3, 5], kinds, n, budget=200 * 2 ** 20)
        self.assertEqual((chunk, rest), ([3, 5], []))
        chunk, rest = S.take_read([0, 1], kinds, n, budget=1)
        self.assertEqual(chunk, [0, 1])                  # always at least one column
        chunk, rest = S.take_read([1, 0], kinds, n, alone={1})
        self.assertEqual((chunk, rest), ([1], [0]))      # read on its own

    def test_shed_the_biggest_text(self):
        a, b, c = S.builder(F.TEXT), S.builder(F.TEXT), S.builder(F.REAL)
        for k in range(300):
            a.add(f"a{k}")
            b.add(f"b{k % 50}")
            c.add(float(k))
        self.assertFalse(S.shed([a, b, c], limit=400))
        self.assertTrue(S.shed([a, b, c], limit=200))
        self.assertTrue(a.dropped)
        self.assertFalse(b.dropped)
        lone = S.builder(F.TEXT)
        for k in range(300):
            lone.add(str(k))
        self.assertFalse(S.shed([lone], limit=10))       # a lone column finishes


class Compact(Both):
    def test_row_index(self):
        values = CP.ids([5, 9, 11, 40])
        idx = CP.RowIndex(values)
        self.assertEqual(idx.row(11), 2)
        self.assertIsNone(idx.row(12))
        self.assertEqual(sorted(idx.rows({40, 5, 7})), [0, 3])
        shuffled = CP.ids([40, 5, 11, 9])
        idx = CP.RowIndex(shuffled)
        self.assertEqual(idx.row(5), 1)
        self.assertEqual(idx.row(9), 3)
        self.assertIsNone(idx.row(6))
        self.assertEqual(sorted(idx.rows([9, 40, 1000])), [0, 3])
        self.assertEqual(idx.count({5, 9, 99}), 2)
        self.assertEqual(idx.first({11, 9}), 2)
        self.assertIsNone(CP.RowIndex(CP.ids()).row(1))

    def test_helpers(self):
        self.assertIsInstance(CP.ids([3, 1]), array)
        self.assertTrue(CP.ascending(CP.ids([1, 2, 5])))
        self.assertFalse(CP.ascending(CP.ids([1, 5, 2])))
        self.assertEqual(list(CP.sort_ids(CP.ids([3, 1, 2]))), [1, 2, 3])
        self.assertEqual(list(CP.take(CP.ids([10, 20, 30]), [2, 0, 1])), [30, 10, 20])


if __name__ == "__main__":
    unittest.main()
