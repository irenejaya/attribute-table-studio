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

    def test_set_intercept_linear_and_polynomial(self):
        rnd = np.random.default_rng(4)
        x = rnd.uniform(1, 20, 400)
        y = 3.0 * x + 7.0 + rnd.normal(0, 2, 400)
        for b in (0.0, 7.0, 12.5):
            tr = P.fit_trend(x, y, "linear", intercept=b)
            slope = float(np.linalg.lstsq(x[:, None], y - b, rcond=None)[0][0])
            self.assertAlmostEqual(tr.coef[0], b)
            self.assertAlmostEqual(tr.coef[1], slope, places=9)
            res = y - (b + slope * x)
            self.assertAlmostEqual(tr.r2, 1 - (res @ res) / ((y - b) @ (y - b)), places=9)
        exact = 1.0 + 2.0 * x - 0.5 * x ** 2
        tr = P.fit_trend(x, exact, "polynomial", order=2, intercept=1.0)
        np.testing.assert_allclose(tr.coef, [1.0, 2.0, -0.5], rtol=1e-9, atol=1e-9)
        self.assertAlmostEqual(tr.r2, 1.0)
        self.assertTrue(tr.equation.startswith("y = −0.5x²"))

    def test_set_intercept_exponential_and_others(self):
        x = np.linspace(0.5, 6, 50)
        tr = P.fit_trend(x, 3.0 * np.exp(0.4 * x), "exponential", intercept=3.0)
        self.assertAlmostEqual(tr.a, 3.0)
        self.assertAlmostEqual(tr.b, 0.4, places=9)
        bad = P.fit_trend(x, np.exp(x), "exponential", intercept=0.0)
        self.assertFalse(bad.ok)
        self.assertIn("above 0", bad.problem)
        # logarithmic and power trends have no set intercept (as in spreadsheets)
        a = P.fit_trend(x, 2 * np.log(x) + 1, "logarithmic", intercept=50.0)
        self.assertIsNone(a.intercept)
        self.assertAlmostEqual(a.a, 1.0, places=9)

    def test_forecast_extends_the_line(self):
        x = np.array([2.0, 4.0, 6.0])
        tr = P.fit_trend(x, 2 * x, "linear")
        self.assertEqual((tr.lo, tr.hi), (2.0, 6.0))
        tr.extend(backward=1.5, forward=4)
        self.assertEqual((tr.lo, tr.hi), (0.5, 10.0))
        self.assertAlmostEqual(float(tr.at([10.0])[0]), 20.0)
        lg = P.fit_trend(x, np.log(x), "logarithmic")
        lg.extend(backward=5)                       # no logarithm at 0 or below
        self.assertGreater(lg.lo, 0)


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

    def test_several_y_fields(self):
        t = make_table(X=(F.REAL, [1, 2, 3, None, 5]),
                       A=(F.REAL, [10, None, 30, 40, 50]),
                       B=(F.REAL, [None, None, 3, 4, None]))
        d = {"x": "X", "y": "A", "fields": ["A", "B"],
             "trends": {"A": {"on": True}, "B": {"on": False}}}
        p = P.scatter(t, d)
        self.assertEqual(p.names, ["A", "B"])
        self.assertEqual(len(p), 4)                     # A: records 1, 3, 5; B: record 3
        self.assertEqual(p.series.tolist(), [0, 0, 0, 1])
        self.assertEqual([t.fids[i] for i in p.rows], [1, 3, 5, 3])
        self.assertEqual(p.y.tolist(), [10, 30, 50, 3])
        self.assertEqual(p.missing, 2)                  # record 2 (no Y) and 4 (no X)
        self.assertTrue(p.trends[0].ok)
        self.assertIsNone(p.trends[1])
        self.assertIsNone(p.trend)                      # several fields: see trends
        self.assertEqual(p.y_name_of(3), "B")
        self.assertEqual(P.fields_used({"type": "scatter", "data": d}), ["A", "X", "B"])
        self.assertEqual(P.default_title({"type": "scatter", "data": d}), "A, B and X")

    def test_earlier_plots_keep_their_trend(self):
        d = {"x": "X", "y": "A", "trend": True, "trend_type": "power", "order": 3}
        self.assertEqual(P.scatter_fields(d), ["A"])
        o = P.trend_options(d, "A", first=True)
        self.assertEqual((o["on"], o["type"], o["order"]), (True, "power", 3))
        self.assertEqual((o["equation"], o["r2"], o["forward"]), (True, True, 0.0))
        d["trends"] = {"A": {"on": False}}
        self.assertFalse(P.trend_options(d, "A", first=True)["on"])

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


class ComboTests(unittest.TestCase):
    def test_bar_and_line_series_on_two_axes(self):
        t = make_table(LU=(F.TEXT, ["b", "a", "b", "c", "a", None]),
                       V=(F.REAL, [10, 20, 30, None, 50, 60]))
        p = P.combo(t, {"x": "LU", "series": [
            {"field": None, "agg": "count", "kind": "bar", "axis": "left"},
            {"field": "V", "agg": "mean", "kind": "line", "axis": "right"}]})
        self.assertEqual(p.labels, ["a", "b", "c"])          # A → Z
        self.assertEqual(p.series, ["Count", "Mean of V"])
        self.assertEqual(p.kinds, ["bar", "line"])
        self.assertEqual(p.axes, ["left", "right"])
        self.assertEqual(p.values[0], [2, 2, 1])
        self.assertEqual(p.values[1][:2], [35.0, 20.0])
        # c has a record, but its value is NULL: no value (not an empty bin)
        self.assertTrue(math.isnan(p.values[1][2]))
        self.assertEqual(p.excluded, 1)

    def test_empty_interval(self):
        t = make_table(D=(F.DATE, [day(2024, 1, 5), day(2024, 3, 5)]),
                       V=(F.REAL, [4, 6]))
        opts = {"x": "D", "unit": "months", "align": "reference",
                "reference": day(2024, 1, 1),
                "series": [{"field": "V", "agg": "sum", "kind": "line", "axis": "left"}]}
        self.assertEqual(P.combo(t, dict(opts, empty="zero")).values[0], [4, 0, 6])
        nulls = P.combo(t, dict(opts, empty="null")).values[0]
        self.assertTrue(math.isnan(nulls[1]))


class PieTests(unittest.TestCase):
    def test_categories_largest_first_and_other(self):
        lu = ["a"] * 50 + ["b"] * 30 + ["c"] * 15 + ["d"] * 3 + ["e"] * 2 + [None]
        t = make_table(LU=(F.TEXT, lu), V=(F.REAL, list(range(len(lu)))))
        p = P.pie(t, {"mode": "category", "category": "LU"})
        self.assertEqual(p.labels, ["a", "b", "c", "d", "e"])
        self.assertEqual(p.values, [50, 30, 15, 3, 2])
        self.assertEqual(p.excluded, 1)
        p = P.pie(t, {"mode": "category", "category": "LU", "threshold": 5})
        self.assertEqual(p.labels, ["a", "b", "c", P.OTHER])
        self.assertEqual(p.values[-1], 5)
        rows = P.pie_rows(p, t, [p.other])
        self.assertEqual(sorted(lu[i] for i in rows), ["d"] * 3 + ["e"] * 2)
        # a mean of Other is the mean of its records
        p = P.pie(t, {"mode": "category", "category": "LU", "field": "V", "agg": "mean",
                      "threshold": 20})
        grouped = {t.columns["LU"].labels[c] for c in p.members[p.other]}
        self.assertEqual(grouped, {"a", "b"})       # the means under 20% of the whole
        other = [i for i, v in enumerate(lu) if v in grouped]
        self.assertAlmostEqual(p.values[p.other], statistics.fmean(other))

    def test_fields_as_slices_and_selection(self):
        t = make_table(A=(F.REAL, [1, 2, None]), B=(F.REAL, [5, None, 5]),
                       C=(F.REAL, [-1, -2, 0]))
        p = P.pie(t, {"mode": "fields", "fields": ["A", "B", "C"], "agg": "sum"})
        self.assertEqual(p.labels, ["B", "A"])
        self.assertEqual(p.values, [10, 3])
        self.assertEqual(p.left_out, ["C"])                 # not above 0
        mask = P.selected_mask(t.np(None), {1})
        per, part = P.pie_selected(p, t, mask)
        self.assertEqual(per, [1, 1])
        self.assertEqual(part, [5.0, 1.0])
        self.assertEqual(sorted(P.pie_rows(p, t, [1]).tolist()), [0, 1])


class HistogramTests(unittest.TestCase):
    def test_bins_counts_and_statistics(self):
        vals = [1, 2, 2, 3, 3, 3, 4, 4, 4, 4, None, float("nan")]
        t = make_table(X=(F.REAL, [None if v is None else v for v in vals]))
        t.columns["X"].values[11] = float("nan")
        p = P.histogram(t, {"x": "X", "bins": 3})
        self.assertEqual(p.counts, [1, 2, 7])               # [1,2) [2,3) [3,4]
        self.assertEqual(p.missing, 2)
        good = [1, 2, 2, 3, 3, 3, 4, 4, 4, 4]
        self.assertAlmostEqual(p.stats["mean"], statistics.fmean(good))
        self.assertAlmostEqual(p.stats["median"], statistics.median(good))
        self.assertAlmostEqual(p.stats["std"], statistics.pstdev(good))
        self.assertEqual(P.default_hist_bins(10000), 64)
        self.assertEqual(P.default_hist_bins(100), 10)
        self.assertEqual(sorted(P.hist_rows(p, [2]).tolist()), [3, 4, 5, 6, 7, 8, 9])

    def test_transformation_and_split(self):
        t = make_table(X=(F.REAL, [-1, 0, 1, math.e, math.e ** 2]),
                       S=(F.TEXT, ["a", "a", "b", "b", "a"]))
        p = P.histogram(t, {"x": "X", "bins": 2, "transform": "log"})
        self.assertEqual(p.left_out, 2)
        self.assertAlmostEqual(p.stats["max"], 2.0)
        p = P.histogram(t, {"x": "X", "bins": 2, "split": "S"})
        self.assertEqual(p.series, ["a", "b"])
        self.assertEqual([sum(c) for c in p.series_counts], [3, 2])
        mask = P.selected_mask(t.np(None), {1, 2, 3})
        self.assertEqual(sum(P.hist_selected(p, mask)), 3)
        self.assertEqual(sum(P.hist_selected(p, mask, 1)), 1)


class BoxTests(unittest.TestCase):
    def test_quartiles_whiskers_and_outliers(self):
        vals = [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]
        t = make_table(V=(F.REAL, vals))
        p = P.box(t, {"fields": ["V"]})
        b = p.boxes[0]
        q1, med, q3 = np.percentile(vals, [25, 50, 75])
        self.assertEqual((b.q1, b.median, b.q3), (q1, med, q3))
        self.assertEqual(b.high, 9)                     # last value inside 1.5 x IQR
        self.assertEqual(b.out_values.tolist(), [100])
        self.assertEqual(int(t.fids[b.out_rows[0]]), 10)
        p = P.box(t, {"fields": ["V"], "outliers": False})
        self.assertEqual((p.boxes[0].low, p.boxes[0].high), (1, 100))

    def test_split_and_standardize(self):
        t = make_table(A=(F.REAL, [1, 2, 3, 4, None, 6]),
                       B=(F.REAL, [10, 20, 30, 40, 50, 60]),
                       Z=(F.TEXT, ["y", "x", "y", "x", "y", None]))
        p = P.box(t, {"fields": ["A", "B"], "split": "Z"})
        self.assertTrue(p.standardized)
        self.assertEqual(p.categories, ["x", "y", "NULL"])
        self.assertEqual([(b.label, b.series) for b in p.boxes[:4]],
                         [("x", 0), ("x", 1), ("y", 0), ("y", 1)])
        # z-scores: both fields on one scale
        a = np.array([1, 2, 3, 4, 6.0])
        z = (a - a.mean()) / a.std()
        self.assertAlmostEqual(p.boxes[0].median, float(np.median(z[[1, 3]])))
        self.assertEqual(p.boxes[2].n, 2)               # y: A has 1, 3 (5th is NULL)
        p = P.box(t, {"fields": ["A"], "split": "Z"})
        self.assertFalse(p.standardized)                # one field: its own values


class ClassBreakTests(unittest.TestCase):
    def test_methods(self):
        v = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.assertEqual(P.class_breaks(v, "equal", 3), [4.0, 7.0, 10.0])
        q = P.class_breaks(v, "quantile", 2)
        self.assertEqual(q[-1], 10.0)
        self.assertAlmostEqual(q[0], float(np.quantile(v, 0.5)))
        g = P.class_breaks([1, 10, 100, 1000], "geometric", 3)
        self.assertEqual(len(g), 3)
        self.assertAlmostEqual(g[0], 10.0)
        self.assertAlmostEqual(g[1], 100.0)
        self.assertEqual(g[-1], 1000)
        n = P.class_breaks([1, 1, 2, 2, 10, 11, 12, 30, 31, 32], "natural", 3)
        self.assertEqual(n, [2, 12, 32])
        self.assertEqual(P.class_of(5, [4.0, 7.0, 10.0]), 1)
        self.assertEqual(P.class_of(float("nan"), [1.0]), -1)


class CellTests(unittest.TestCase):
    def test_matrix_heat_counts_and_mean(self):
        t = make_table(A=(F.TEXT, ["x", "y", "x", "x", None]),
                       B=(F.INTEGER, [1, 1, 2, 1, 2]),
                       V=(F.REAL, [10, 20, 30, 50, 60]))
        p = P.matrix_heat(t, {"x": "A", "y": "B"})
        self.assertEqual((p.columns, p.rows), (["x", "y"], ["1", "2"]))
        self.assertEqual(p.records.tolist(), [2, 1, 1, 0])
        self.assertTrue(math.isnan(p.values[3]))            # no records: no colour
        p = P.matrix_heat(t, {"x": "A", "y": "B", "field": "V", "agg": "mean"})
        self.assertEqual(p.values[0], 30.0)
        self.assertEqual(p.excluded, 1)
        self.assertEqual(sorted(P.cell_rows(p, [0]).tolist()), [0, 3])

    def test_calendar_year_and_week(self):
        dates = [day(2023, 1, 1, 9), day(2023, 1, 1, 18), day(2024, 2, 29, 3),
                 day(2024, 1, 1, 9)]
        t = make_table(D=(F.DATETIME, dates))
        p = P.calendar_heat(t, {"x": "D", "calendar": "year"})
        self.assertEqual(p.records[0 * 31 + 0], 3)          # 1 January, both years
        self.assertEqual(p.records[1 * 31 + 28], 1)         # 29 February
        p = P.calendar_heat(t, {"x": "D", "calendar": "year", "leap": False})
        self.assertEqual(p.records[1 * 31 + 28], 0)
        p = P.calendar_heat(t, {"x": "D", "calendar": "year", "view": "sequential"})
        self.assertEqual(p.panels, ["2023", "2024"])
        self.assertEqual(p.records[0], 2)                   # 2023, 1 January
        p = P.calendar_heat(t, {"x": "D", "calendar": "week"})
        # 1 Jan 2023 is a Sunday (row 6), 9 and 18 o'clock
        self.assertEqual(p.records[6 * 24 + 9], 1)
        self.assertEqual(p.records[6 * 24 + 18], 1)
        # 1 Jan 2024 is a Monday
        self.assertEqual(p.records[0 * 24 + 9], 1)

    def test_data_clock(self):
        dates = [day(2022, 3, 5), day(2023, 3, 9), day(2023, 12, 31), day(2024, 12, 31)]
        t = make_table(D=(F.DATE, dates))
        p = P.data_clock(t, {"x": "D", "rings": "years_months"})
        self.assertEqual(p.rows, ["2022", "2023", "2024"])  # oldest first (innermost)
        self.assertEqual(p.records[1 * 12 + 2], 1)          # 2023, March
        p = P.data_clock(t, {"x": "D", "rings": "years_weeks"})
        self.assertEqual(len(p.columns), 52)
        self.assertEqual(p.records[1 * 52 + 51], 1)         # 31 Dec 2023: the last week
        self.assertEqual(p.records[2 * 52 + 51], 1)         # 31 Dec 2024 (leap year)
        p = P.data_clock(t, {"x": "D", "rings": "years_days"})
        self.assertEqual(p.records[2 * 366 + 365], 1)       # day 366 of 2024


class MatrixQQTests(unittest.TestCase):
    def test_matrix_r2_and_hists(self):
        rnd = random.Random(5)
        a = [rnd.gauss(0, 1) for _ in range(300)]
        b = [x * 2 + rnd.gauss(0, 0.5) for x in a]
        c = [rnd.gauss(0, 1) if i % 10 else None for i in range(300)]
        t = make_table(A=(F.REAL, a), B=(F.REAL, b), C=(F.REAL, c))
        p = P.scatter_matrix(t, {"fields": ["A", "B", "C"], "trend": True})
        r = statistics.correlation(a, b)
        self.assertAlmostEqual(p.r2[0, 1], r * r, places=9)
        self.assertEqual(p.n[0, 2], 270)
        self.assertEqual(sum(p.hists[2][1]), 270)
        self.assertTrue(p.trends[(0, 1)].ok)

    def test_norm_ppf(self):
        for prob, z in ((0.5, 0.0), (0.975, 1.959963985), (0.01, -2.326347874),
                        (0.999, 3.090232306)):
            self.assertAlmostEqual(float(P.norm_ppf(np.array([prob]))[0]), z, places=6)

    def test_without_scipy_too(self):
        """The QQ numbers are the same with SciPy and with the own
        calculation (QGIS before 3.44.16, installs without SciPy)."""
        rnd = random.Random(9)
        probs = np.array([0.001, 0.02, 0.3, 0.5, 0.77, 0.99, 0.9999])
        lognormal = np.exp(np.array([rnd.gauss(0.3, 0.8) for _ in range(3000)]))
        results = []
        for use in (True, False):
            P.USE_SCIPY = use
            try:
                results.append((P.norm_ppf(probs), P.boxcox_lambda(lognormal)))
            finally:
                P.USE_SCIPY = True
        self.assertTrue(np.allclose(results[0][0], results[1][0], atol=1e-8))
        self.assertAlmostEqual(results[0][1], results[1][1], delta=0.06)

    def test_qq_normal_and_two_fields(self):
        rnd = random.Random(2)
        vals = [rnd.gauss(50, 10) for _ in range(500)]
        t = make_table(X=(F.REAL, vals), Y=(F.REAL, [v * 3 for v in vals[:250]] +
                                            [None] * 250))
        p = P.qq(t, {"x": "X"})
        self.assertEqual(len(p), 500)
        self.assertEqual(p.y.tolist(), sorted(vals))
        self.assertAlmostEqual(p.ref[0], 10, delta=1.5)     # slope near the std
        self.assertEqual([vals[i] for i in p.rows[:3]], sorted(vals)[:3])
        p = P.qq(t, {"x": "X", "y": "Y"})
        self.assertEqual(len(p), 250)                       # the field with fewer values
        self.assertEqual(p.missing, 250)
        w, keep, lam = P.transform(np.array([1.0, np.e, -1.0, 0.0]), "log")
        self.assertEqual(keep.tolist(), [True, True, False, False])
        self.assertAlmostEqual(w[1], 1.0)
        lognormal = np.exp(np.array([rnd.gauss(0, 1) for _ in range(2000)]))
        self.assertAlmostEqual(P.boxcox_lambda(lognormal), 0.0, delta=0.15)


if __name__ == "__main__":
    unittest.main()
