# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Unit tests for the pure-Python engines (no QGIS needed).

Run from the repository root:  python -m unittest discover -s tests
"""

import datetime
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import fields as F  # noqa: E402
from core import find as FIND  # noqa: E402
from core import filters as FL  # noqa: E402
from core import pivot as P  # noqa: E402
from core import stats as S  # noqa: E402
from core import subset as SUB  # noqa: E402


class FieldTests(unittest.TestCase):
    def test_null_and_zero(self):
        self.assertTrue(F.is_null(None))
        self.assertFalse(F.is_null(0))
        self.assertFalse(F.is_null(""))
        self.assertFalse(F.is_null(0.0))

    def test_sort_key_nulls_last(self):
        vals = [None, "b", 3, "A", 1.5]
        ordered = sorted(vals, key=F.sort_key)
        self.assertEqual(ordered, [1.5, 3, "A", "b", None])

    def test_temporal_key_round_trip(self):
        d = datetime.date(2024, 2, 29)
        self.assertEqual(F.key_to_text(F.temporal_key(d), F.DATE), "2024-02-29")
        dt = datetime.datetime(2024, 2, 29, 13, 5, 7)
        self.assertEqual(F.key_to_text(F.temporal_key(dt), F.DATETIME),
                         "2024-02-29 13:05:07")

    def test_format_number(self):
        self.assertEqual(F.format_number(7411), "7,411")
        self.assertEqual(F.format_number(88.44), "88.44")
        self.assertEqual(F.format_number(59.07, 2), "59.07")
        self.assertEqual(F.format_number(2.0), "2")


class StatsTests(unittest.TestCase):
    def test_numeric_profile(self):
        p = S.compute_profile(F.REAL, [10.0, 20.0, None, 0.0, 30.0])
        self.assertEqual(p.count, 5)
        self.assertEqual(p.missing, 1)
        self.assertEqual(p.non_null, 4)
        self.assertEqual(p.valid, 4)
        self.assertEqual(p.minimum, 0.0)       # zero is a real value
        self.assertEqual(p.maximum, 30.0)
        self.assertAlmostEqual(p.mean, 15.0)
        self.assertAlmostEqual(p.median, 15.0)
        self.assertAlmostEqual(p.total, 60.0)
        self.assertAlmostEqual(p.std, math.sqrt(125.0))
        self.assertEqual(p.distinct, 4)
        self.assertAlmostEqual(p.pct(p.missing), 20.0)
        self.assertEqual(sum(c for _l, _h, c in p.bins), 4)

    def test_non_finite_reported_separately(self):
        p = S.compute_profile(F.REAL, [1.0, float("nan"), float("inf"), None])
        self.assertEqual(p.invalid, 2)
        self.assertEqual(p.missing, 1)
        self.assertEqual(p.valid, 1)
        self.assertEqual(p.mean, 1.0)

    def test_histogram_max_in_last_bin(self):
        vals = sorted([0.0, 1.0, 2.0, 3.0, 4.0, 10.0])
        bins, integer = S.histogram(vals, bins=5)
        self.assertFalse(integer)
        self.assertEqual(len(bins), 5)
        self.assertEqual(bins[-1][2], 1)              # 10.0 counted
        self.assertEqual(sum(b[2] for b in bins), 6)
        self.assertEqual(bins[-1][1], 10.0)

    def test_integer_bins(self):
        p = S.compute_profile(F.INTEGER, [1, 2, 2, 3, 3, 3])
        self.assertTrue(p.integer_bins)
        self.assertEqual([(lo, c) for lo, _hi, c in p.bins],
                         [(1, 1), (2, 2), (3, 3)])

    def test_constant_numeric(self):
        p = S.compute_profile(F.REAL, [5.5, 5.5, None])
        self.assertTrue(p.constant)
        self.assertEqual(p.bins, [(5.5, 5.5, 2)])

    def test_all_null(self):
        p = S.compute_profile(F.REAL, [None, None])
        self.assertEqual(p.missing, 2)
        self.assertAlmostEqual(p.pct(p.missing), 100.0)
        self.assertEqual(p.bins, [])
        self.assertIsNone(p.mean)

    def test_empty_scope(self):
        p = S.compute_profile(F.TEXT, [])
        self.assertEqual(p.count, 0)
        self.assertEqual(p.pct(0), 0.0)
        self.assertEqual(p.frequencies, [])

    def test_text_categories_use_non_null(self):
        vals = ["RES"] * 6 + ["IND"] * 2 + ["COM"] + [None] * 3 + ["", "  "]
        p = S.compute_profile(F.TEXT, vals)
        self.assertEqual(p.missing, 3)
        self.assertEqual(p.non_null, 11)
        self.assertEqual(p.distinct, 5)                # "" and "  " count
        self.assertEqual(p.blank, 1)
        self.assertEqual(p.whitespace, 1)
        top = p.top_categories(2)
        self.assertEqual(top[0][0], "RES")
        self.assertAlmostEqual(top[0][2], 600 / 11)
        self.assertIsNone(top[-1][0])                  # Other classes
        self.assertEqual(top[-1][1], 3)
        self.assertTrue(p.is_categorical())

    def test_quotes_and_unicode(self):
        p = S.compute_profile(F.TEXT, ["O'Brien", "Zürich", "O'Brien"])
        self.assertEqual(p.frequencies[0], ("O'Brien", 2))

    def test_dates(self):
        vals = [datetime.date(2024, 1, 3), None, datetime.date(2023, 12, 31),
                datetime.date(2024, 1, 3)]
        p = S.compute_profile(F.DATE, vals)
        self.assertEqual(p.earliest, datetime.date(2023, 12, 31))
        self.assertEqual(p.latest, datetime.date(2024, 1, 3))
        self.assertEqual(p.distinct, 2)
        self.assertEqual(p.missing, 1)

    def test_boolean(self):
        p = S.compute_profile(F.BOOL, [True, False, True, None, 1, 0])
        self.assertEqual((p.true_count, p.false_count, p.missing), (3, 2, 1))


class FilterTests(unittest.TestCase):
    def expr(self, kind, op, value="", value2="", values=None, field="F"):
        return FL.ColumnFilter(field, kind, op, value, value2, values).build()

    def test_text(self):
        self.assertEqual(self.expr(F.TEXT, "contains", "res"),
                         "\"F\" ILIKE '%res%'")
        self.assertEqual(self.expr(F.TEXT, "eq", "O'Brien"),
                         "\"F\" = 'O''Brien'")
        self.assertEqual(self.expr(F.TEXT, "starts", "RE"),
                         "\"F\" ILIKE 'RE%'")
        self.assertEqual(self.expr(F.TEXT, "contains", "50%"),
                         "strpos(lower(\"F\"), '50%') > 0")
        self.assertIsNone(self.expr(F.TEXT, "contains", ""))

    def test_quoted_column(self):
        self.assertEqual(self.expr(F.TEXT, "null", field='a"b'),
                         '"a""b" IS NULL')

    def test_numbers(self):
        self.assertEqual(self.expr(F.REAL, "gt", "60"), '"F" > 60')
        self.assertEqual(self.expr(F.REAL, "eq", "0"), '"F" = 0')
        self.assertEqual(self.expr(F.REAL, "ne", "88.44"), '"F" <> 88.44')
        self.assertEqual(self.expr(F.REAL, "between", "20", "10"),
                         '("F" >= 10 AND "F" <= 20)')
        self.assertEqual(self.expr(F.INTEGER, "between", "5", ""), '"F" >= 5')
        with self.assertRaises(FL.FilterError):
            self.expr(F.REAL, "gt", "abc")

    def test_in_list(self):
        self.assertEqual(self.expr(F.TEXT, "in", values=["A", "B"]),
                         "\"F\" IN ('A', 'B')")
        self.assertEqual(self.expr(F.INTEGER, "in", values=[1, None]),
                         '("F" IN (1) OR "F" IS NULL)')
        self.assertEqual(self.expr(F.TEXT, "in", values=[None]),
                         '"F" IS NULL')

    def test_dates(self):
        self.assertEqual(self.expr(F.DATE, "ge", "2024-01-31"),
                         '"F" >= make_date(2024, 1, 31)')
        self.assertEqual(self.expr(F.DATETIME, "eq", "2024-01-31"),
                         'to_date("F") = make_date(2024, 1, 31)')
        self.assertEqual(
            self.expr(F.DATE, "between", "2024-02-01", "2024-01-01"),
            '("F" >= make_date(2024, 1, 1) AND "F" <= make_date(2024, 2, 1))')

    def test_bool(self):
        self.assertEqual(self.expr(F.BOOL, "true"), '"F" = TRUE')

    def test_combine_and_errors(self):
        a = FL.ColumnFilter("LAND_USE", F.TEXT, "eq", "RESIDENTIAL")
        b = FL.ColumnFilter("SCORE_MAX", F.REAL, "gt", "60")
        c = FL.ColumnFilter("X", F.REAL, "gt", "oops")
        expr, errors = FL.combine([a, b, c])
        self.assertEqual(
            expr, "(\"LAND_USE\" = 'RESIDENTIAL') AND (\"SCORE_MAX\" > 60)")
        self.assertIn("X", errors)

    def test_value_hints(self):
        # dates show their format in every value box, 'between' included
        for op in ("eq", "lt", "ge", "between"):
            self.assertEqual(FL.value_hints(F.DATE, op), ("YYYY-MM-DD", "YYYY-MM-DD"))
        self.assertEqual(FL.value_hints(F.DATETIME, "between")[1], "YYYY-MM-DD")
        self.assertEqual(FL.value_hints(F.TIME, "between"), ("HH:MM:SS", "HH:MM:SS"))
        self.assertEqual(FL.value_hints(F.DATE, "null")[0], "is NULL")
        self.assertEqual(FL.value_hints(F.TEXT, "contains")[0], "contains")
        self.assertEqual(FL.value_hints(F.REAL, "between"), ("from", "to"))
        self.assertEqual(FL.value_hints(F.INTEGER, "gt")[0], "value")

    def test_round_trip_dict(self):
        a = FL.ColumnFilter("F", F.TEXT, "in", values=["A", None])
        b = FL.ColumnFilter.from_dict(a.to_dict())
        self.assertEqual(a.build(), b.build())


def _records(rows):
    return [(i, r) for i, r in enumerate(rows)]


class SubsetTests(unittest.TestCase):
    """Table filters written as a layer filter in each provider's SQL."""

    def sql(self, flt, dialect):
        return SUB.filter_sql(flt, dialect)

    def test_numbers_and_lists(self):
        f = FL.ColumnFilter("SCORE", F.REAL, "between", "60", "10")
        for d in ("sqlite", "ogr", "postgres"):
            self.assertEqual(self.sql(f, d), '("SCORE" >= 10 AND "SCORE" <= 60)')
        f = FL.ColumnFilter("CODE", F.INTEGER, "in", values=[3, None, 1])
        self.assertEqual(self.sql(f, "sqlite"), '("CODE" IN (3, 1) OR "CODE" IS NULL)')

    def test_text(self):
        eq = FL.ColumnFilter("NAME", F.TEXT, "eq", "O'Brien")
        self.assertEqual(self.sql(eq, "sqlite"), '"NAME" = \'O\'\'Brien\'')
        has = FL.ColumnFilter("NAME", F.TEXT, "contains", "dent")
        self.assertEqual(self.sql(has, "sqlite"), '"NAME" LIKE \'%dent%\'')
        self.assertEqual(self.sql(has, "postgres"), '"NAME" ILIKE \'%dent%\'')
        self.assertEqual(self.sql(has, "ogr"), '"NAME" ILIKE \'%dent%\'')
        wild = FL.ColumnFilter("NAME", F.TEXT, "contains", "50%")
        self.assertEqual(self.sql(wild, "sqlite"), 'instr(lower("NAME"), \'50%\') > 0')
        self.assertIsNone(self.sql(wild, "ogr"))
        # SQLite folds ASCII letters only: leave non-ASCII to the id list
        self.assertIsNone(self.sql(FL.ColumnFilter("NAME", F.TEXT, "contains", "É"), "sqlite"))

    def test_dates_bools_and_qgis(self):
        d = FL.ColumnFilter("D", F.DATE, "ge", "2024-01-02")
        self.assertEqual(self.sql(d, "sqlite"), '"D" >= \'2024-01-02\'')
        self.assertEqual(self.sql(d, "postgres"), '"D" >= DATE \'2024-01-02\'')
        self.assertIsNone(self.sql(d, "ogr"))
        dt = FL.ColumnFilter("T", F.DATETIME, "lt", "2024-01-02")
        self.assertEqual(self.sql(dt, "sqlite"), 'substr("T", 1, 10) < \'2024-01-02\'')
        b = FL.ColumnFilter("B", F.BOOL, "true")
        self.assertEqual(self.sql(b, "sqlite"), '"B" = 1')
        self.assertEqual(self.sql(b, "postgres"), '"B" = TRUE')
        # memory / delimited text layers use the table's own expression
        self.assertEqual(self.sql(d, "qgis"), d.build())

    def test_base_filter(self):
        self.assertEqual(SUB.with_base("", "a"), "a")
        self.assertEqual(SUB.with_base("x > 1", "a"), "(x > 1) AND (a)")
        self.assertEqual(SUB.with_base("x > 1", ""), "x > 1")
        self.assertIsNone(SUB.with_base("SELECT * FROM t", "a"))


class PivotTests(unittest.TestCase):
    def test_plan_fixture(self):
        # Residential 10, 20, NULL; Industrial 0, 30
        rows = [("RES", 10.0), ("RES", 20.0), ("RES", None),
                ("IND", 0.0), ("IND", 30.0)]
        spec = P.PivotSpec(["LU"], [], [P.Measure("V", "mean"),
                                        P.Measure("V", "count_non_null")])
        r = P.compute(_records(rows), ["LU", "V"], spec,
                      kinds={"LU": F.TEXT, "V": F.REAL})
        self.assertEqual(r.row_keys, [("IND",), ("RES",)])
        self.assertEqual(r.value(("RES",), (), 0), 15.0)
        self.assertEqual(r.value(("RES",), (), 1), 2)
        self.assertEqual(r.value(("IND",), (), 0), 15.0)
        self.assertEqual(r.value(("IND",), (), 1), 2)
        self.assertEqual(r.value(P.TOTAL, (), 0), 15.0)
        self.assertEqual(r.record_count(P.TOTAL, P.TOTAL), 5)

    def test_unequal_groups_grand_mean(self):
        rows = [("A", 10.0), ("A", 20.0), ("B", 90.0)]
        spec = P.PivotSpec(["G"], [], [P.Measure("V", "mean")])
        r = P.compute(_records(rows), ["G", "V"], spec)
        self.assertEqual(r.value(("A",), (), 0), 15.0)
        self.assertEqual(r.value(("B",), (), 0), 90.0)
        self.assertEqual(r.value(P.TOTAL, (), 0), 40.0)   # not 52.5

    def test_null_group_separate_from_text_null(self):
        rows = [(None, 1.0), ("NULL", 2.0), ("x", 3.0)]
        spec = P.PivotSpec(["G"], [], [P.Measure("V", "sum")])
        r = P.compute(_records(rows), ["G", "V"], spec)
        self.assertEqual(r.row_keys, [("NULL",), ("x",), (None,)])
        self.assertEqual(r.value((None,), (), 0), 1.0)
        self.assertEqual(r.value(("NULL",), (), 0), 2.0)

    def test_cross_tab_missing_combination(self):
        rows = [("A", "x", 1.0), ("A", "y", 2.0), ("B", "x", 4.0)]
        spec = P.PivotSpec(["R"], ["C"], [P.Measure(None, "count"),
                                          P.Measure("V", "mean")])
        r = P.compute(_records(rows), ["R", "C", "V"], spec)
        self.assertEqual(r.value(("B",), ("y",), 0), 0)       # count -> 0
        self.assertIsNone(r.value(("B",), ("y",), 1))         # mean -> NULL
        self.assertEqual(r.value(("A",), P.TOTAL, 1), 1.5)
        self.assertAlmostEqual(r.value(P.TOTAL, ("x",), 1), 2.5)
        self.assertAlmostEqual(r.value(P.TOTAL, P.TOTAL, 1), 7.0 / 3)
        header, body, total = r.table()
        self.assertEqual([h["group"] for h in header if h["kind"] == "value"],
                         ["x", "x", "y", "y", "Total", "Total"])
        self.assertEqual(len(body), 2)
        self.assertEqual(total[0]["value"], "Grand total")

    def test_multiple_measures_and_all_null(self):
        rows = [("A", None, 1), ("A", None, 1), ("B", None, 2)]
        spec = P.PivotSpec(["G"], [], [
            P.Measure("V", "mean"), P.Measure("V", "count_non_null"),
            P.Measure("W", "sum"), P.Measure("W", "max"),
            P.Measure("W", "count_distinct"), P.Measure("W", "median"),
            P.Measure("W", "stdev")])
        r = P.compute(_records(rows), ["G", "V", "W"], spec)
        self.assertIsNone(r.value(("A",), (), 0))      # all NULL -> NULL
        self.assertEqual(r.value(("A",), (), 1), 0)
        self.assertEqual(r.value(P.TOTAL, (), 2), 4)
        self.assertEqual(r.value(P.TOTAL, (), 3), 2)
        self.assertEqual(r.value(P.TOTAL, (), 4), 2)
        self.assertEqual(r.value(P.TOTAL, (), 5), 1)
        self.assertAlmostEqual(r.value(P.TOTAL, (), 6), math.sqrt(2 / 9))

    def test_duplicate_labels_and_fids(self):
        rows = [("A", 1.0), ("A", 2.0), ("B", 3.0)]
        spec = P.PivotSpec(["G"], [], [P.Measure("V", "sum")])
        r = P.compute(_records(rows), ["G", "V"], spec)
        self.assertEqual(sorted(r.fids(("A",), ())), [0, 1])
        self.assertEqual(sorted(r.fids(P.TOTAL, P.TOTAL)), [0, 1, 2])

    def test_numeric_measure_requires_numeric_field(self):
        spec = P.PivotSpec(["G"], [], [P.Measure("T", "mean")])
        with self.assertRaises(P.PivotError):
            P.compute([], ["G", "T"], spec, kinds={"T": F.TEXT})

    def test_cell_limit(self):
        rows = [(str(i), 1.0) for i in range(50)]
        spec = P.PivotSpec(["G"], [], [P.Measure("V", "sum")])
        with self.assertRaises(P.PivotError):
            P.compute(_records(rows), ["G", "V"], spec, cell_limit=10)

    def test_spec_round_trip(self):
        spec = P.PivotSpec(["A"], ["B"], [P.Measure("V", "mean", "Avg")],
                           False, True)
        again = P.PivotSpec.from_dict(spec.to_dict())
        self.assertEqual(again.to_dict(), spec.to_dict())



class FindTests(unittest.TestCase):
    def test_contains_ignoring_case(self):
        m = FIND.Matcher("resid")
        self.assertTrue(m.matches("RESIDENTIAL"))
        self.assertFalse(m.matches("Commercial"))
        self.assertEqual(m.replace("Resid resid", "X"), "X X")

    def test_match_case(self):
        m = FIND.Matcher("Lot", case=True)
        self.assertTrue(m.matches("Lot 12"))
        self.assertFalse(m.matches("lot 12"))

    def test_whole_cell(self):
        m = FIND.Matcher("lot 1", whole=True)
        self.assertTrue(m.matches("Lot 1"))
        self.assertFalse(m.matches("Lot 12"))
        self.assertEqual(m.replace("LOT 1", "Lot one"), "Lot one")
        self.assertEqual(m.replace("Lot 12", "x"), "Lot 12")

    def test_plain_text_is_not_a_pattern(self):
        m = FIND.Matcher("a.b (1)")
        self.assertTrue(m.matches("x a.b (1) y"))
        self.assertFalse(m.matches("axb 1"))
        self.assertEqual(m.replace("a.b (1)", "$1 \\1"), "$1 \\1")

    def test_regex_and_groups(self):
        m = FIND.Matcher(r"^P-(\d{3})(\d+)$", regex=True)
        self.assertTrue(m.matches("P-170001"))
        self.assertFalse(m.matches("Q-170001"))
        self.assertEqual(m.replace("P-170001", "$2-$1"), "001-170")
        self.assertEqual(m.replace("P-170001", "[$&] $$ $9"), "[P-170001] $ $9")
        self.assertEqual(m.replace("P-170001", "$12"), "1702")    # $1 then "2"
        whole = FIND.Matcher(r"st", regex=True, whole=True)
        self.assertFalse(whole.matches("Main St"))
        self.assertTrue(whole.matches("ST"))

    def test_invalid_regex(self):
        import re
        with self.assertRaises(re.error):
            FIND.Matcher("(", regex=True)


if __name__ == "__main__":
    unittest.main()
