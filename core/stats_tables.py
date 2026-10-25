# -*- coding: utf-8 -*-
"""
The two tables of a column's statistics popup - the summary and the
distribution (histogram bins or value frequencies) - in two forms: the
text the popup shows, and the raw values copies and exports use (full
precision, counts as numbers, shares as fractions 0-1).
"""

from . import fields as F
from . import stats as S
from .export import export_value

SUMMARY_HEADER = ["Statistic", "Value", "Share"]


def fmt_pct(n, base):
    """'0%', '<1%', '93%', '>99%', '100%' (never rounds up to a false 100%)."""
    if not base or not n:
        return "0%"
    p = 100.0 * n / base
    if p < 1:
        return "<1%"
    if n < base and p >= 99.5:
        return ">99%"
    return f"{p:.0f}%"


def fmt_value(value, kind):
    """Short display of a statistic (min / max / earliest ...)."""
    if value is None:
        return F.NULL_TEXT
    if kind in F.NUMERIC:
        return F.format_number(value)
    return F.display_value(value)


def _share(n, base):
    return n / base if base else None


def summary(p, kind):
    """[(statistic, value, share or None, shown text)] in popup order."""
    rows = []

    def add(label, value, shown, share=None):
        rows.append((label, value, share, shown))

    add("Records in scope", p.count, f"{p.count:,}")
    for label, n in (("Non-NULL", p.non_null), ("Missing (NULL)", p.missing),
                     ("Distinct (non-NULL)", p.distinct)):
        add(label, n, f"{n:,}  ({fmt_pct(n, p.count)})", _share(n, p.count))
    if kind in F.NUMERIC:
        add("Valid numbers", p.valid, f"{p.valid:,}")
        if p.valid:
            for label, value in (("Sum", p.total), ("Mean", p.mean), ("Median", p.median),
                                 ("Minimum", p.minimum), ("Maximum", p.maximum),
                                 ("Std dev (population)", p.std)):
                add(label, value, F.format_number(value))
        if p.invalid:
            add("Invalid (NaN / infinite)", p.invalid, f"{p.invalid:,}")
    elif kind in F.TEMPORAL:
        if p.valid:
            add("Earliest", p.earliest, fmt_value(p.earliest, kind))
            add("Latest", p.latest, fmt_value(p.latest, kind))
        if p.invalid:
            add("Invalid", p.invalid, f"{p.invalid:,}")
    elif kind == F.BOOL:
        for label, n in (("true", p.true_count), ("false", p.false_count)):
            add(label, n, f"{n:,}  ({fmt_pct(n, p.count)})", _share(n, p.count))
        add("Denominator", p.count, f"{p.count:,} records in scope")
    else:
        add("Empty strings", p.blank, f"{p.blank:,}")
        add("Whitespace only", p.whitespace, f"{p.whitespace:,}")
    return rows


class Distribution:
    """*header* for copies and exports, *shown_header* for the popup;
    *rows* raw (label, count, share), *shown* the popup text."""

    def __init__(self, header, shown_header, rows, shown, note=""):
        self.header = header
        self.shown_header = shown_header
        self.rows = rows
        self.shown = shown
        self.note = note


def distribution(p, kind):
    """The popup's distribution table, or None when it shows none."""
    if kind in F.NUMERIC or kind in F.TEMPORAL:
        if not p.bins or len(p.bins) < 2:
            return None
        fmt = (lambda v: F.key_to_text(v, kind)) if kind in F.TEMPORAL \
            else F.format_number
        rows, shown = [], []
        for i, (lo, hi, count) in enumerate(p.bins):
            label = S.bin_label(lo, hi, i == len(p.bins) - 1, p.integer_bins, fmt)
            rows.append((label, count, _share(count, p.valid)))
            shown.append((label, f"{count:,}", fmt_pct(count, p.valid)))
        return Distribution(["Bin", "Records", "Share"], ["Bin", "Records", "Share"],
                            rows, shown)
    if kind == F.BOOL or not p.frequencies:
        return None
    rows, shown = [], []
    for text, count in p.frequencies:
        label = text if text != "" else "(empty string)"
        if text and not text.strip():
            label = f"(whitespace ×{len(text)})"
        rows.append((text, count, _share(count, p.non_null)))
        shown.append((label, f"{count:,}", fmt_pct(count, p.non_null)))
    note = (f"The {len(p.frequencies):,} most common of {p.distinct:,} distinct values"
            if p.frequencies_truncated else "")
    return Distribution(["Value", "Records", "Share of non-NULL"],
                        ["Value", "Records", "% of non-NULL"], rows, shown, note)


# ----------------------------------------------------------------------------
# tab separated text (pastes into Excel as a table)
# ----------------------------------------------------------------------------

def tsv_cell(value):
    return export_value(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def tsv(header, rows):
    lines = ["\t".join(header)] if header else []
    lines += ["\t".join(tsv_cell(v) for v in row) for row in rows]
    return "\n".join(lines)


def summary_tsv(rows):
    return tsv(SUMMARY_HEADER, [(label, value, share) for label, value, share, _s in rows])


def distribution_tsv(dist):
    return tsv(dist.header, dist.rows) if dist is not None else ""


def everything_tsv(meta, rows, dist):
    """Scope (layer, field, filters, records), the summary and the
    distribution, separated by blank lines."""
    parts = [tsv([], [(k, v) for k, v in meta.items()]), summary_tsv(rows)]
    if dist is not None:
        if dist.note:
            parts[0] += "\n" + tsv([], [("Distribution", dist.note)])
        parts.append(distribution_tsv(dist))
    return "\n\n".join(parts)
