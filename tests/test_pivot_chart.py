# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Unit tests for the Pivot Table chart engine (no QGIS needed).

Run from the repository root:  python -m unittest discover -s tests
"""

import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import charts as C  # noqa: E402
from core import pivot as P  # noqa: E402
from core import pivot_chart as PC  # noqa: E402

FIELDS = ["ZONE", "KIND", "VALUE", "CHANGE"]
KINDS = {"ZONE": "text", "KIND": "text", "VALUE": "real", "CHANGE": "real"}


def records():
    """Made-up parcels: zone, kind, value, change (some NULL)."""
    rows = [
        (1, ("North", "House", 100.0, 5.0)),
        (2, ("North", "House", 300.0, -20.0)),
        (3, ("North", "Shop", 50.0, None)),
        (4, ("South", "House", 200.0, -40.0)),
        (5, ("South", "Farm", None, -10.0)),
        (6, ("East", "Shop", 80.0, 2.5)),
        (7, (None, "Farm", 10.0, 1.0)),
    ]
    return rows


def build(rows=("ZONE",), cols=(), measures=None, data=None):
    from core import fields as F
    kinds = {"ZONE": F.TEXT, "KIND": F.TEXT, "VALUE": F.REAL, "CHANGE": F.REAL}
    spec = P.PivotSpec(rows, cols, measures or [P.Measure("VALUE", "sum")])
    return P.compute(data or records(), FIELDS, spec, kinds)


class ValueChoices(unittest.TestCase):
    def test_numbers_only_and_records(self):
        spec = P.PivotSpec(["ZONE"], [], [P.Measure("KIND", "list_distinct"),
                                          P.Measure("VALUE", "mean")])
        choices = PC.value_choices(spec)
        self.assertEqual([c[0] for c in choices], [1, PC.RECORDS])
        self.assertEqual(choices[0][2], "mean")

    def test_no_extra_records_when_counted(self):
        spec = P.PivotSpec(["ZONE"], [], [P.Measure(None, "count")])
        self.assertEqual([c[0] for c in PC.value_choices(spec)], [0])

    def test_format_like_the_table(self):
        self.assertEqual(PC.format_value(None, "sum"), "NULL")
        self.assertEqual(PC.format_value(1200.0, "sum"), "1,200")
        self.assertEqual(PC.format_value(5.0, "mean", 2), "5.00")
        self.assertEqual(PC.format_value(3, "count"), "3")


class Bars(unittest.TestCase):
    def test_one_bar_per_row(self):
        result = build()
        bars = PC.chart_bars(result, 0)
        by_label = dict(zip(bars.labels, bars.values))
        self.assertEqual(by_label["North"], 450.0)
        self.assertEqual(by_label["South"], 200.0)
        self.assertEqual(by_label["NULL"], 10.0)
        self.assertEqual(bars.chart, C.BARS)
        self.assertIsNone(bars.split)
        self.assertEqual(sum(bars.records), 7)
        self.assertEqual(bars.labels[bars.null_bar], "NULL")
        self.assertFalse(bars.negative)

    def test_records_value(self):
        bars = PC.chart_bars(build(), PC.RECORDS, sort="count")
        self.assertEqual(bars.labels[0], "North")
        self.assertEqual(bars.values[0], 3)
        self.assertEqual(bars.agg, "count")

    def test_sorts(self):
        result = build()
        az = PC.chart_bars(result, 0, sort="az").labels
        self.assertEqual(az, ["East", "North", "South", "NULL"])
        za = PC.chart_bars(result, 0, sort="za").labels
        self.assertEqual(za, ["South", "North", "East", "NULL"])     # NULL last both ways
        big = PC.chart_bars(result, 0, sort="count").values
        self.assertEqual(big, sorted(big, reverse=True))
        small = PC.chart_bars(result, 0, sort="fewest").values
        self.assertEqual(small, sorted(small))

    def test_table_order_and_top(self):
        result = build()
        order = [("South",), ("East",), ("North",), (None,)]
        bars = PC.chart_bars(result, 0, row_keys=order + [P.TOTAL], sort="table", top=2)
        self.assertEqual(bars.labels, ["South", "East"])
        self.assertIn("2 of 4", bars.note)

    def test_null_values_stay_none(self):
        bars = PC.chart_bars(build(measures=[P.Measure("VALUE", "mean")]), 0, sort="fewest")
        # every zone has a value here; a mean of nothing would be None
        self.assertTrue(all(v is not None for v in bars.values))
        data = records() + [(8, ("West", "Shop", None, None))]
        bars = PC.chart_bars(build(measures=[P.Measure("VALUE", "mean")], data=data), 0,
                             sort="count")
        self.assertIsNone(bars.values[-1])               # no value: last, drawn as 0
        self.assertEqual(bars.counts[-1], 0)

    def test_negative(self):
        bars = PC.chart_bars(build(measures=[P.Measure("CHANGE", "sum")]), 0)
        self.assertTrue(bars.negative)
        self.assertEqual(dict(zip(bars.labels, bars.values))["South"], -50.0)


class Columns(unittest.TestCase):
    def test_columns_are_the_series(self):
        result = build(cols=("KIND",))
        bars = PC.chart_bars(result, 0, sort="az")
        self.assertEqual(bars.series, ["Farm", "House", "Shop"])
        north = bars.labels.index("North")
        self.assertEqual(bars.split_values[north], [None, 400.0, 50.0])
        self.assertEqual(bars.split[north], [0, 400.0, 50.0])
        self.assertEqual(sum(bars.split[north]), bars.counts[north])     # sums add up
        self.assertEqual(bars.split_records[north], [0, 2, 1])

    def test_many_columns_fold_into_an_exact_other(self):
        rng = random.Random(7)
        data = []
        for fid in range(1, 400):
            data.append((fid, (rng.choice("AB"), f"K{rng.randrange(14):02d}",
                               float(rng.randrange(1, 100)), None)))
        result = build(cols=("KIND",), measures=[P.Measure("VALUE", "mean")], data=data)
        bars = PC.chart_bars(result, 0)
        self.assertEqual(len(bars.series), C.SPLIT_SERIES + 1)
        self.assertEqual(bars.series[-1], "Other")
        other = bars.series_keys[-1]
        self.assertEqual(len(other), 14 - C.SPLIT_SERIES)
        # Other's mean is the mean of its records, not a sum of means
        for i, rk in enumerate(bars.row_keys):
            vals = [r[1][2] for r in data if (r[1][0],) == rk and (r[1][1],) in other]
            self.assertTrue(math.isclose(bars.split_values[i][-1], sum(vals) / len(vals)))
        # the kept columns are the ones with the most records
        counts = {ck: result.record_count(P.TOTAL, ck) for ck in result.col_keys}
        kept = [g[0] for g in bars.series_keys[:-1]]
        self.assertGreaterEqual(min(counts[k] for k in kept),
                                max(counts[k] for k in other))

    def test_fids_and_selection(self):
        result = build(cols=("KIND",), measures=[P.Measure("VALUE", "sum")])
        bars = PC.chart_bars(result, 0, sort="az")
        north = bars.labels.index("North")
        self.assertEqual(sorted(PC.bar_fids(result, bars, [north])), [1, 2, 3])
        house = bars.series.index("House")
        self.assertEqual(sorted(PC.bar_fids(result, bars, [north], house)), [1, 2])
        sel, sel_series, rec, rec_series = PC.selection(result, bars, {1, 2})
        self.assertEqual(rec[north], 2)
        self.assertEqual(sel[north], 0)                  # a sum: only a whole bar
        self.assertEqual(sel_series[north][house], 400.0)
        self.assertEqual(rec_series[north][house], 2)
        sel, _s, _r, _rs = PC.selection(result, bars, {1, 2, 3})
        self.assertEqual(sel[north], 450.0)

    def test_records_selection_is_partial(self):
        result = build(measures=[P.Measure(None, "count")])
        bars = PC.chart_bars(result, 0, sort="az")
        north = bars.labels.index("North")
        sel, _s, rec, _rs = PC.selection(result, bars, {1})
        self.assertEqual(sel[north], 1)
        self.assertEqual(rec[north], 1)

    def test_export_rows(self):
        result = build(cols=("KIND",))
        bars = PC.chart_bars(result, 0, sort="az")
        rows = PC.table_rows(bars)
        self.assertEqual(rows[0][:3], ["East", 80.0, 1])
        self.assertEqual(len(rows[0]), 4 + len(bars.series))


class Labels(unittest.TestCase):
    """Group values name a value (a year, a code): no thousands separator;
    counts and statistics keep it."""

    def test_plain_values(self):
        from core import fields as F
        self.assertEqual(F.display_value(2023), "2023")
        self.assertEqual(F.display_value(2023.0), "2023")
        self.assertEqual(F.display_value(10450.25), "10450.25")
        self.assertEqual(F.display_value(-0.0), "0")
        self.assertEqual(F.display_value(True), "true")
        self.assertEqual(F.format_number(2023), "2,023")          # an amount
        self.assertEqual(P.key_label((2023, None)), "2023 · NULL")

    def test_year_columns_and_rows(self):
        data = [(1, ("North", "House", 1.0, None, 2023)), (2, ("South", "Shop", 2.0, None, 2024)),
                (3, ("North", "Shop", 3.0, None, 2024))]
        from core import fields as F
        spec = P.PivotSpec(["YEAR"], ["ZONE"], [P.Measure("VALUE", "sum")])
        result = P.compute(data, FIELDS + ["YEAR"], spec,
                           {"ZONE": F.TEXT, "VALUE": F.REAL, "YEAR": F.INTEGER})
        bars = PC.chart_bars(result, 0, sort="az")
        self.assertEqual(bars.labels, ["2023", "2024"])
        header, _rows, _total = result.table()
        self.assertIn("North", [h["group"] for h in header])
        spec = P.PivotSpec(["ZONE"], ["YEAR"], [P.Measure("VALUE", "sum")])
        result = P.compute(data, FIELDS + ["YEAR"], spec,
                           {"ZONE": F.TEXT, "VALUE": F.REAL, "YEAR": F.INTEGER})
        self.assertEqual(sorted(PC.chart_bars(result, 0).series), ["2023", "2024"])
        header, _rows, _total = result.table()
        self.assertEqual(sorted(h["group"] for h in header if h["group"] not in ("", "Total")),
                         ["2023", "2024"])


if __name__ == "__main__":
    unittest.main()
