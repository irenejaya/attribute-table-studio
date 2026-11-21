# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Pivot Table chart: the bars of a built pivot (no Qt, unit tested).

One bar per row of the pivot, its value one measure (the first that gives
a number, or the records of each row).  With column fields every column of
the pivot becomes a coloured part of the bars (as Split by does in
Visualize Statistics): the ``SPLIT_SERIES`` columns with the most records
keep their own colour, in the pivot's column order; the others are put
together as **Other**, worked out from their records, so it is exact for
every aggregation (a mean of Other is the mean of those records, not a sum
of means).

Values come from the pivot itself, never from reading the layer again, so
the chart opens at once whatever the size of the layer.
"""

from . import charts as C
from . import fields as F
from . import pivot as P

RECORDS = "records"                  # the value: records of each row
# the parts of a stacked bar add up to its total only for these
ADDITIVE = ("count", "count_non_null", "sum")
SORTS = ("table", "az", "za", "fewest", "count")
SORT_LABELS = {
    "table": "As in the pivot table",
    "az": "A → Z",
    "za": "Z → A",
    "fewest": "Smallest value first",
    "count": "Largest value first",
}


def value_choices(spec):
    """[(key, title, aggregation)] the chart can show: every measure that
    gives a number (not List distinct), then Records unless a measure
    already counts the records."""
    out = []
    for mi, m in enumerate(spec.measures):
        if m.agg in P.TEXT_AGGS:
            continue
        out.append((mi, m.title(), m.agg))
    if not any(agg == "count" for _k, _t, agg in out):
        out.append((RECORDS, "Records", "count"))
    return out


def aggregation(spec, value):
    return "count" if value == RECORDS else spec.measures[value].agg


def format_value(v, agg, decimals=2):
    """A value as the pivot table shows it."""
    if v is None:
        return F.NULL_TEXT
    if agg in P.COUNT_AGGS or isinstance(v, int):
        return F.format_number(v)
    if agg in ("min", "max", "sum") and float(v).is_integer():
        return F.format_number(int(v))
    return F.format_number(v, decimals)


def _value(result, value, rk, col_keys=None):
    if value == RECORDS:
        if col_keys is None:
            return result.record_count(rk, P.TOTAL)
        return result.record_count_over(rk, col_keys)
    if col_keys is None:
        return result.value(rk, P.TOTAL, value)
    return result.value_over(rk, col_keys, value)


def _is_null_key(rk):
    return bool(rk) and all(v is None for v in rk)


def _key_sort(rk):
    return tuple(F.sort_key(v) for v in rk)


def series_groups(result):
    """([names], [[column keys]]) of the coloured parts; ([], []) without
    column fields."""
    if not result.has_cols:
        return [], []
    cks = list(result.col_keys)
    if len(cks) <= C.SPLIT_SERIES:
        return [P.key_label(ck) for ck in cks], [[ck] for ck in cks]
    ranked = sorted(range(len(cks)),
                    key=lambda k: (-result.record_count(P.TOTAL, cks[k]), k))
    keep = set(ranked[:C.SPLIT_SERIES])
    names = [P.key_label(cks[k]) for k in range(len(cks)) if k in keep]
    groups = [[cks[k]] for k in range(len(cks)) if k in keep]
    names.append("Other")
    groups.append([cks[k] for k in range(len(cks)) if k not in keep])
    return names, groups


def chart_bars(result, value, row_keys=None, sort="table", top=None):
    """The bars of *result* for the measure *value* (an index into the
    pivot's measures, or ``RECORDS``).

    *row_keys* is the pivot table's row order (``sort="table"`` keeps it),
    else its rows as built.  *top* keeps the first rows after sorting.
    Besides :class:`.charts.Bars`' fields the result carries ``values``
    (NULL kept as None), ``split_values``, ``records``, ``row_keys``,
    ``series_keys`` (the column keys of each part), ``agg`` and
    ``negative``.
    """
    rows = list(result.row_keys if row_keys is None else row_keys)
    rows = [rk for rk in rows if rk is not P.TOTAL]
    totals = {rk: _value(result, value, rk) for rk in rows}
    present = [rk for rk in rows if not _is_null_key(rk)]
    nulls = [rk for rk in rows if _is_null_key(rk)]
    if sort in ("az", "za"):
        present.sort(key=_key_sort, reverse=sort == "za")
        rows = present + nulls                    # NULL last either way
    elif sort in ("fewest", "count"):
        with_value = [rk for rk in rows if totals[rk] is not None]
        without = [rk for rk in rows if totals[rk] is None]
        with_value.sort(key=lambda rk: totals[rk], reverse=sort == "count")
        rows = with_value + without
    n_rows = len(rows)
    if top and n_rows > top:
        rows = rows[:top]
    out = C.Bars(C.BARS)
    out.agg = aggregation(result.spec, value)
    out.row_keys = rows
    out.labels = [P.key_label(rk) for rk in rows]
    out.values = [totals[rk] for rk in rows]
    out.counts = [v or 0 for v in out.values]
    out.records = [result.record_count(rk, P.TOTAL) for rk in rows]
    out.total = sum(out.records)
    out.members = [[k] for k in range(len(rows))]
    out.null_bar = next((k for k, rk in enumerate(rows) if _is_null_key(rk)), None)
    names, groups = series_groups(result)
    out.series = names
    out.series_keys = groups
    out.split_values = None
    if groups:
        out.split_values = [[_value(result, value, rk, g) for g in groups] for rk in rows]
        out.split = [[v or 0 for v in row] for row in out.split_values]
        out.split_records = [[result.record_count_over(rk, g) for g in groups]
                             for rk in rows]
    else:
        out.split_records = None
    flat = list(out.counts) + [v for row in (out.split or []) for v in row]
    out.negative = any(v < 0 for v in flat)
    if top and n_rows > top:
        out.note = f"The first {top:,} of {n_rows:,} rows"
    return out


def bar_fids(result, bars, indexes, series=None):
    """Feature ids behind bars *indexes* (with *series*, only that part)."""
    out = []
    for i in indexes:
        rk = bars.row_keys[i]
        if series is None:
            out.extend(result.fids(rk, P.TOTAL))
        else:
            out.extend(result.fids_over(rk, bars.series_keys[series]))
    return out


def selection(result, bars, selected):
    """What of the bars the layer's selection (a set of ids) holds.

    Returns ``(sel, sel_series, records, records_series)``: *records* the
    selected records per bar (and per part).  *sel* is what the chart
    paints in the selection colour, in the bars' own units: for a count
    of records the selected records themselves (a bar can be partly
    selected), for any other value the whole bar once all its records
    are selected.
    """
    k = len(bars)
    if not selected or not k:
        return [0] * k, None, [0] * k, None
    exact = bars.agg == "count"
    records, sel = [], []
    for i, rk in enumerate(bars.row_keys):
        fids = result.fids(rk, P.TOTAL)
        n = sum(1 for f in fids if f in selected)
        records.append(n)
        if exact:
            sel.append(n)
        else:
            sel.append(bars.counts[i] if fids and n == len(fids) else 0)
    sel_series = records_series = None
    if bars.split is not None:
        sel_series, records_series = [], []
        for i, rk in enumerate(bars.row_keys):
            srow, rrow = [], []
            for s, group in enumerate(bars.series_keys):
                fids = result.fids_over(rk, group)
                n = sum(1 for f in fids if f in selected)
                rrow.append(n)
                if exact:
                    srow.append(n)
                else:
                    srow.append(bars.split[i][s] if fids and n == len(fids) else 0)
            sel_series.append(srow)
            records_series.append(rrow)
    return sel, sel_series, records, records_series


def table_rows(bars, records_selected=None):
    """Rows for the CSV export: bar, value, records, selected and one value
    per column of the pivot."""
    out = []
    sel = records_selected or [0] * len(bars)
    for i in range(len(bars)):
        row = [bars.labels[i], bars.values[i], bars.records[i], sel[i]]
        if bars.split_values is not None:
            row += list(bars.split_values[i])
        out.append(row)
    return out
