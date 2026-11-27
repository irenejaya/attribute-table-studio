# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Unit tests for the Data Plot engine (no QGIS needed; made-up data).

Run from the repository root:  python -m unittest discover -s tests
"""

import datetime
import math
import os
import random
import statistics
import sys
import unittest
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from core import fields as F  # noqa: E402
from core import plots as P  # noqa: E402

NAN = float("nan")


def day(y, m, d, hh=0, mm=0):
    return F.temporal_key(datetime.datetime(y, m, d, hh, mm))


def make_table(**columns):
    """columns: name=(kind, values); text values become codes."""
    t = P.Table()
    n = len(next(iter(columns.values()))[1])
    t.fids = array("q", range(1, n + 1))
    for name, (kind, values) in columns.items():
        col = P.Column(name, kind)
        if col.role == P.CATEGORY:
            index = {}
            for v in values:
                if v is None:
                    col.null += 1
                    col.codes.append(P.NULL_CODE)
                    continue
                code = index.get(v)
                if code is None:
                    code = index[v] = len(col.labels)
                    col.labels.append(v)
                col.codes.append(code)
        else:
            for v in values:
                if v is None:
                    col.null += 1
                    col.values.append(NAN)
                else:
                    col.values.append(float(v))
        t.columns[name] = col
    return t


class AggregateTests(unittest.TestCase):
    def test_every_aggregation_matches_python(self):
        rnd = random.Random(7)
        groups = [rnd.randrange(-1, 5) for _ in range(500)]
        values = [rnd.choice([None, rnd.uniform(-50, 200)]) for _ in range(500)]
        g = np.asarray(groups)
        v = np.asarray([NAN if x is None else x for x in values])
        for how in ("count", "sum", "mean", "median", "min", "max"):
            res, records = P.aggregate(g, 5, v, how)
            for k in range(5):
                rows = [i for i, gg in enumerate(groups) if gg == k]
                self.assertEqual(records[k], len(rows))
                vals = [values[i] for i in rows if values[i] is not None]
                if how == "count":
                    expect = len(rows)
                elif not vals:
                    self.assertTrue(math.isnan(res[k]), how)
                    continue
                else:
                    expect = {"sum": math.fsum(vals), "mean": statistics.fmean(vals),
                              "median": statistics.median(vals), "min": min(vals),
                              "max": max(vals)}[how]
                self.assertAlmostEqual(res[k], expect, places=7, msg=how)

    def test_group_without_values_is_nan_not_zero(self):
        res, rec = P.aggregate(np.array([0, 0, 1]), 2, np.array([NAN, NAN, 4.0]), "sum")
        self.assertTrue(math.isnan(res[0]))
        self.assertEqual(res[1], 4.0)
        self.assertEqual(rec.tolist(), [2, 1])


class TimeBinTests(unittest.TestCase):
    def test_start_alignment_days(self):
        days = np.array([day(2024, 1, 1, 6), day(2024, 1, 1, 18), day(2024, 1, 3, 1)])
        b = P.time_bins(days, "days", 1, "start")
        # bins start at the first record: [1 Jan 06:00, 2 Jan 06:00), ...
        self.assertEqual(len(b), 2)
        self.assertEqual(b.index.tolist(), [0, 0, 1])
        self.assertAlmostEqual(b.starts[0], days[0])

    def test_end_alignment_includes_last_record(self):
        days = np.array([day(2024, 1, 1), day(2024, 1, 2), day(2024, 1, 5)])
        b = P.time_bins(days, "days", 2, "end")
        self.assertEqual(b.closed, "right")
        self.assertAlmostEqual(b.ends[-1], days[-1])
        self.assertEqual(b.index[2], len(b) - 1)
        # (Jan 3, Jan 5] holds Jan 5 only; (Jan 1, Jan 3] holds Jan 2;
        # (Dec 30, Jan 1] holds Jan 1
        self.assertEqual(b.index.tolist(), [0, 1, 2])

    def test_reference_time_months(self):
        days = np.array([day(2024, 1, 20), day(2024, 2, 10), day(2024, 3, 16)])
        ref = day(2023, 6, 15)
        b = P.time_bins(days, "months", 1, "reference", reference=ref)
        labels = [F.key_to_text(s, F.DATE) for s in b.starts]
        self.assertEqual(labels[0], "2024-01-15")
        self.assertEqual(b.index.tolist(), [0, 0, 2])

    def test_months_clamp_to_month_end(self):
        days = np.array([day(2024, 1, 31), day(2024, 2, 29), day(2024, 3, 30)])
        b = P.time_bins(days, "months", 1, "start")
        labels = [F.key_to_text(s, F.DATE) for s in b.starts]
        self.assertEqual(labels[:3], ["2024-01-31", "2024-02-29", "2024-03-29"])
        self.assertEqual(b.index.tolist(), [0, 1, 2])

    def test_trim_incomplete_interval(self):
        # weekly bins from Monday 1 Jan; data to Wednesday 17 Jan: the third
        # week is partly filled and goes
        days = np.array([day(2024, 1, d) for d in range(1, 18)])
        b = P.time_bins(days, "weeks", 1, "start", trim=True, date_only=True)
        self.assertEqual(len(b), 2)
        self.assertEqual(b.trimmed, 3)
        untrimmed = P.time_bins(days, "weeks", 1, "start", date_only=True)
        self.assertEqual(len(untrimmed), 3)

    def test_date_only_last_day_fills_its_bin(self):
        days = np.array([day(2024, 1, d) for d in range(1, 15)])
        b = P.time_bins(days, "weeks", 1, "start", trim=True, date_only=True)
        self.assertEqual(len(b), 2)          # two full weeks, nothing trimmed
        self.assertEqual(b.trimmed, 0)

    def test_auto_interval_and_cap(self):
        b = P.time_bins(np.array([day(2000, 1, 1), day(2024, 1, 1)]))
        self.assertEqual((b.unit, b.size), ("months", 3))      # 96 quarters
        days = np.array([day(1950, 1, 1), day(2024, 1, 1)])
        b = P.time_bins(days)
        self.assertEqual((b.unit, b.size), ("years", 1))
        b = P.time_bins(np.array([day(2024, 1, 1), day(2024, 1, 1, 0, 30)]))
        self.assertEqual(b.unit, "minutes")
        many = P.time_bins(days, "seconds", 1)
        self.assertLessEqual(len(many), P.MAX_TIME_BINS + 1)
        self.assertIn("would give more than", many.note)

    def test_nan_is_in_no_bin(self):
        b = P.time_bins(np.array([NAN, day(2024, 5, 1)]), "days", 1)
        self.assertEqual(b.index.tolist(), [-1, 0])


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.t = make_table(
            LAND_USE=(F.TEXT, ["Residential", "Commercial", "Residential", None,
                               "Industrial", "Residential"]),
            VALUE=(F.REAL, [100, 300, None, 50, 40, 200]),
            AREA=(F.REAL, [10, 20, 30, 40, 50, 60]),
            ZONE=(F.TEXT, ["A", "B", "A", "A", "B", None]),
            SOLD=(F.DATE, [day(2024, 1, 3), day(2024, 1, 20), day(2024, 3, 5),
                           None, day(2024, 3, 9), day(2024, 1, 4)]),
        )

    def test_bar_count_sorted_az_and_null_left_out(self):
        p = P.bar(self.t, {"x": "LAND_USE", "agg": "count"})
        self.assertEqual(p.labels, ["Commercial", "Industrial", "Residential"])
        self.assertEqual(p.values[0], [1, 1, 3])
        self.assertEqual(p.excluded, 1)

    def test_bar_mean_of_two_fields_by_value(self):
        p = P.bar(self.t, {"x": "LAND_USE", "agg": "mean", "fields": ["VALUE", "AREA"],
                           "sort": "value_desc"})
        self.assertEqual(p.series, ["VALUE", "AREA"])
        # Commercial 300+20, Residential 150+33.3, Industrial 40+50
        self.assertEqual(p.labels, ["Commercial", "Residential", "Industrial"])
        self.assertAlmostEqual(p.values[0][1], 150.0)
        self.assertAlmostEqual(p.values[1][1], 100.0 / 3)

    def test_bar_split_is_exact_per_category(self):
        p = P.bar(self.t, {"x": "LAND_USE", "agg": "sum", "fields": ["AREA"],
                           "split": "ZONE"})
        self.assertTrue(p.split)
        self.assertEqual(p.series, ["A", "B", "NULL"])
        res = dict(zip(p.labels, zip(*p.values)))
        self.assertEqual(res["Residential"][0], 40)        # A: 10 + 30
        self.assertEqual(res["Residential"][2], 60)        # NULL zone
        self.assertTrue(math.isnan(res["Residential"][1]))

    def test_split_other_holds_the_rest(self):
        n = 40
        t = make_table(CAT=(F.TEXT, ["x"] * n),
                       S=(F.TEXT, [f"s{i % 12}" for i in range(n)]),
                       V=(F.REAL, list(range(n))))
        p = P.bar(t, {"x": "CAT", "agg": "mean", "fields": ["V"], "split": "S"})
        self.assertEqual(len(p.series), P.SPLIT_SERIES + 1)
        self.assertEqual(p.series[-1], P.OTHER)
        other = [v for i, v in enumerate(range(n)) if p.series_of[i] == P.SPLIT_SERIES]
        self.assertAlmostEqual(p.values[-1][0], statistics.fmean(other))

    def test_line_by_month_and_empty_bins(self):
        p = P.line(self.t, {"x": "SOLD", "agg": "count", "unit": "months", "size": 1,
                            "align": "reference", "reference": day(2024, 1, 1)})
        self.assertEqual(len(p), 3)
        self.assertEqual(p.values[0], [3, 0, 2])
        self.assertEqual(p.excluded, 1)
        p = P.line(self.t, {"x": "SOLD", "agg": "sum", "fields": ["VALUE"], "unit": "months",
                            "align": "reference", "reference": day(2024, 1, 1)})
        self.assertEqual(p.values[0][0], 600)       # 100 + 300 + 200
        self.assertTrue(math.isnan(p.values[0][1]))         # no records: empty bin

    def test_line_number_x_one_point_per_value(self):
        p = P.line(self.t, {"x": "AREA", "agg": "max", "fields": ["VALUE"]})
        self.assertEqual(p.xs, [10, 20, 30, 40, 50, 60])
        self.assertTrue(math.isnan(p.values[0][2]))

    def test_records_and_selection_per_position(self):
        p = P.bar(self.t, {"x": "LAND_USE", "agg": "count"})
        rows = P.records_of(p, [2])
        self.assertEqual(sorted(self.t.fids[i] for i in rows), [1, 3, 6])
        mask = P.selected_mask(self.t.np(None), {1, 2, 4})
        per, _ = P.selected_per_position(p, mask)
        self.assertEqual(per, [1, 0, 1])
        sums = P.selected_sums(p, self.t, mask, "AREA")
        self.assertEqual(sums[0], [20.0, 0.0, 10.0])

    def test_many_categories_capped(self):
        n = P.MAX_BARS + 5
        t = make_table(ID=(F.TEXT, [f"p{i:05d}" for i in range(n)]))
        p = P.bar(t, {"x": "ID", "agg": "count"})
        self.assertEqual(len(p), P.MAX_BARS)
        self.assertTrue(p.notes)


class TrendTests(unittest.TestCase):
    def test_exact_fits_have_r2_one(self):
        x = np.linspace(1, 10, 50)
        cases = {
            "linear": (2 * x + 3, lambda tr: (tr.coef[0], tr.coef[1]), (3, 2)),
            "exponential": (1.5 * np.exp(0.3 * x), lambda tr: (tr.a, tr.b), (1.5, 0.3)),
            "logarithmic": (4 + 2 * np.log(x), lambda tr: (tr.a, tr.b), (4, 2)),
            "power": (3 * x ** 1.7, lambda tr: (tr.a, tr.b), (3, 1.7)),
        }
        for kind, (y, get, want) in cases.items():
            tr = P.fit_trend(x, y, kind)
            self.assertTrue(tr.ok, kind)
            self.assertAlmostEqual(tr.r2, 1.0, places=9, msg=kind)
            for a, b in zip(get(tr), want):
                self.assertAlmostEqual(a, b, places=6, msg=kind)
            self.assertTrue(np.allclose(tr.at(x), y), kind)

    def test_polynomial_and_equation(self):
        x = np.linspace(-5, 5, 41)
        y = 0.5 * x ** 3 - 2 * x + 7
        tr = P.fit_trend(x, y, "polynomial", 3)
        self.assertTrue(np.allclose(tr.coef, [7, -2, 0, 0.5], atol=1e-8))
        self.assertEqual(tr.equation, "y = 0.5x³ − 2x + 7")
        self.assertAlmostEqual(tr.r2, 1.0, places=9)

    def test_r2_matches_textbook_for_noisy_line(self):
        rnd = random.Random(3)
        x = np.array([rnd.uniform(0, 100) for _ in range(400)])
        y = 3 * x + np.array([rnd.gauss(0, 20) for _ in range(400)])
        tr = P.fit_trend(x, y, "linear")
        r = np.corrcoef(x, y)[0, 1]
        self.assertAlmostEqual(tr.r2, r * r, places=9)

    def test_values_without_logarithm_are_left_out(self):
        x = np.array([-1.0, 0.0, 1.0, 2.0, 3.0])
        y = np.array([5.0, 5.0, 2.0, 4.0, 6.0])
        tr = P.fit_trend(x, y, "power")
        self.assertEqual(tr.left_out, 2)
        self.assertEqual(tr.used, 3)

    def test_too_few_points(self):
        self.assertFalse(P.fit_trend(np.array([1.0]), np.array([2.0])).ok)
        self.assertFalse(P.fit_trend(np.array([1.0, 1.0]), np.array([2.0, 3.0])).ok)


class ScatterTests(unittest.TestCase):
    def test_points_size_and_log_axis(self):
        t = make_table(X=(F.REAL, [1, 2, None, -4, 5]), Y=(F.REAL, [10, 20, 30, 40, None]),
                       S=(F.INTEGER, [1, None, 3, 4, 5]))
        p = P.scatter(t, {"x": "X", "y": "Y", "size": "S"})
        self.assertEqual(len(p), 3)
        self.assertEqual(p.missing, 2)
        self.assertEqual(p.size_missing, 1)
        self.assertEqual(p.size_range, (1.0, 4.0))
        p = P.scatter(t, {"x": "X", "y": "Y"}, x_log=True)
        self.assertEqual(len(p), 2)
        self.assertEqual(p.not_positive, 1)
        self.assertEqual([t.fids[i] for i in p.rows], [1, 2])

    def test_density_counts_and_dominant_class(self):
        px = np.array([0, 0, 1, 1, 1])
        py = np.array([0, 0, 0, 0, 0])
        cls = np.array([2, 2, 0, 1, 1])
        counts, dom = P.density(px, py, 2, 1, cls, 3)
        self.assertEqual(counts.tolist(), [[2, 3]])
        self.assertEqual(dom.tolist(), [[2, 1]])


class PlotSpecTests(unittest.TestCase):
    def test_new_plot_and_ready(self):
        fields = [("LAND_USE", F.TEXT), ("VALUE", F.REAL), ("AREA", F.REAL),
                  ("SOLD", F.DATE)]
        sc = P.new_plot("scatter", fields)          # fields chosen in Data, as in ArcGIS Pro
        self.assertEqual((sc["data"]["x"], sc["data"]["y"]), (None, None))
        self.assertIn("Choose", P.ready(sc))
        sc["data"].update(x="VALUE", y="AREA")
        self.assertEqual(P.ready(sc), "")
        br = P.new_plot("bar", fields)
        self.assertIn("category", P.ready(br))
        br["data"]["x"] = "LAND_USE"
        br["data"]["agg"] = "sum"
        self.assertIn("number field", P.ready(br))
        br["data"]["fields"] = ["VALUE"]
        self.assertEqual(P.fields_used(br), ["LAND_USE", "VALUE"])
        self.assertEqual(P.missing_fields(br, ["LAND_USE"]), ["VALUE"])
        self.assertEqual(P.default_title(br), "Sum of VALUE by LAND_USE")

    def test_parse_values(self):
        self.assertEqual(P.parse_value("1,250.5", P.NUMBER), 1250.5)
        self.assertEqual(P.parse_value("2024-03-01", P.DATE), day(2024, 3, 1))
        self.assertAlmostEqual(P.parse_value("2024-03-01 12:00", P.DATE),
                               day(2024, 3, 1, 12))
        self.assertIsNone(P.parse_value("soon", P.DATE))
        self.assertIsNone(P.parse_value("", P.NUMBER))


if __name__ == "__main__":
    unittest.main()
