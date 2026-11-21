# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Kept columns: the statistics of any part of the records, worked out
from what one read kept, must equal compute_profile on that part.

Run from the repository root:  python -m unittest discover -s tests
"""

import datetime
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import compact as CP  # noqa: E402
from core import fields as F  # noqa: E402
from core import keep as K  # noqa: E402
from core import stats as S  # noqa: E402

FIELDS = ("kind", "count", "missing", "distinct", "invalid", "valid", "minimum",
          "maximum", "total", "mean", "median", "std", "earliest", "latest", "bins",
          "integer_bins", "true_count", "false_count", "frequencies",
          "frequencies_truncated", "blank", "whitespace")


def column(kind, rnd, n):
    if kind == F.REAL:
        return [None if rnd.random() < 0.05 else
                float("nan") if rnd.random() < 0.02 else rnd.lognormvariate(4, 1.5)
                for _ in range(n)]
    if kind == F.INTEGER:
        return [None if rnd.random() < 0.05 else rnd.randint(-20, 400) for _ in range(n)]
    if kind == F.DATE:
        base = datetime.date(2010, 1, 1)
        return [None if rnd.random() < 0.05 else
                base + datetime.timedelta(days=rnd.randint(0, 5000)) for _ in range(n)]
    if kind == F.BOOL:
        return [rnd.choice([True, False, None, "yes", "x"]) for _ in range(n)]
    words = ["Residential", "Commercial", "", " ", "Rural", "Open space", "Hills"]
    return [None if rnd.random() < 0.05 else rnd.choice(words) for _ in range(n)]


@unittest.skipUnless(CP.HAS_NUMPY, "kept columns need numpy")
class Keep(unittest.TestCase):
    KINDS = (F.REAL, F.INTEGER, F.DATE, F.BOOL, F.TEXT)

    def check(self, ref, new, what):
        for name in FIELDS:
            a, b = getattr(ref, name), getattr(new, name)
            if isinstance(a, float) and isinstance(b, float) and math.isnan(a):
                self.assertTrue(math.isnan(b), (what, name))
                continue
            self.assertEqual(a, b, f"{what}: {name}")

    def test_parts_equal_a_new_read(self):
        rnd = random.Random(11)
        n = 4000
        fids = rnd.sample(range(1, 50000), n)            # read order, not sorted
        data = {kind: column(kind, rnd, n) for kind in self.KINDS}
        store = K.Store(("state",), fids)
        for logical, kind in enumerate(self.KINDS):
            b = S.builder(kind, keep=True)
            for v in data[kind]:
                b.add(v)
            # the read's own profile, as before
            self.check(S.compute_profile(kind, data[kind]), b.profile(), f"{kind} read")
            store.add(logical, b.kept())
        self.assertEqual(sorted(store.columns), list(range(len(self.KINDS))))
        by_fid = {kind: dict(zip(fids, data[kind])) for kind in self.KINDS}
        for trial in range(5):
            part = rnd.sample(fids, rnd.choice([0, 1, 7, 900, n]))
            scope = CP.ids(part)                          # a filter's records, any order
            for logical, kind in enumerate(self.KINDS):
                values = [by_fid[kind][f] for f in part]

                def fetch(fid, kind=kind):
                    return by_fid[kind][fid]
                new = store.profile(logical, scope, fetch)
                self.check(S.compute_profile(kind, values), new, f"{kind} part {len(part)}")

    def test_sorted_like_a_read(self):
        rnd = random.Random(12)
        n = 3000
        fids = rnd.sample(range(1, 40000), n)
        data = {F.REAL: [None if rnd.random() < 0.05 else rnd.choice([1.5, 2.0, 2.0, 7.25, -3.0])
                         for _ in range(n)],
                F.INTEGER: [None if rnd.random() < 0.05 else rnd.randint(0, 30) for _ in range(n)],
                F.DATE: column(F.DATE, rnd, n),
                F.TEXT: column(F.TEXT, rnd, n)}
        store = K.Store(("s",), fids)
        kinds = list(data)
        for logical, kind in enumerate(kinds):
            b = S.builder(kind, keep=True)
            for v in data[kind]:
                b.add(v)
            store.add(logical, b.kept())
        for trial in range(4):
            part = set(rnd.sample(fids, rnd.choice([0, 5, 1200, n])))
            in_read_order = [f for f in fids if f in part]       # what a read gives
            for logical, kind in enumerate(kinds):
                values = dict(zip(fids, data[kind]))
                for desc in (False, True):
                    present = [f for f in in_read_order if values[f] is not None]
                    missing = [f for f in in_read_order if values[f] is None]
                    present.sort(key=lambda f: F.sort_key(F.group_value(values[f])),
                                 reverse=desc)
                    got = store.sorted_ids(logical, CP.ids(sorted(part)), desc)
                    self.assertEqual(list(got), present + missing, (kind, desc, len(part)))
        nan = K.Store(("n",), [1, 2])
        b = S.builder(F.REAL, keep=True)
        b.add(float("nan"))
        b.add(1.0)
        nan.add(0, b.kept())
        self.assertIsNone(nan.sorted_ids(0, CP.ids([1, 2])))       # read instead

    def test_whole_scope_needs_no_mask(self):
        store = K.Store(("s",), [5, 3, 9])
        self.assertIsNone(store.mask(CP.ids([9, 5, 3])))
        mask = store.mask(CP.ids([9]))
        self.assertEqual(mask.tolist(), [False, False, True])
        self.assertTrue(store.same_records([5, 3, 9]))
        self.assertFalse(store.same_records([3, 5, 9]))

    def test_unique_text_is_not_kept(self):
        old = S.KEEP_TEXT_LIMIT
        S.KEEP_TEXT_LIMIT = 50
        try:
            b = S.builder(F.TEXT, keep=True)
            for k in range(200):
                b.add(f"ID-{k}")
            self.assertIsNone(b.kept())
            self.assertEqual(b.profile().distinct, 200)    # still profiled exactly
        finally:
            S.KEEP_TEXT_LIMIT = old

    def test_budget(self):
        self.assertEqual(K.budget(300), 300 * 2 ** 20)
        auto = K.budget(0)
        self.assertGreaterEqual(auto, K.AUTO_MIN)
        self.assertLessEqual(auto, K.AUTO_MAX)
        self.assertEqual(S.kept_bytes(F.REAL, 1000), 9000)
        self.assertEqual(S.kept_bytes(F.TEXT, 1000), 4000)


if __name__ == "__main__":
    unittest.main()
