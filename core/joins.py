# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Join Table (Attribute Explorer): the rules of a QGIS layer join, so the
window can say what a join will do before it is added.

QGIS (with the join layer cached in memory, its default) matches the two
fields by their text: 12, 12.0 and "12" are the same value, NULL never
matches, and when a join value repeats, a record gets the last of the
matching join records.  :func:`match_counts` counts what the join will
match, in a background job.
"""

from . import fields as F


def join_key(value):
    """The text QGIS compares for *value* (QVariant::toString), or None
    for NULL."""
    if F.is_null(value):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    if isinstance(value, str):
        return value
    to_string = getattr(value, "toString", None)
    if to_string is not None:
        from qgis.PyQt.QtCore import Qt
        iso = Qt.DateFormat.ISODateWithMs if (
            hasattr(value, "msecsSinceStartOfDay") or hasattr(value, "toMSecsSinceEpoch")) \
            else Qt.DateFormat.ISODate
        try:
            return to_string(iso)
        except TypeError:
            return to_string()
    return str(value)


def default_prefix(layer_name):
    """QGIS's default name prefix: the join layer's name and '_'."""
    return f"{layer_name}_"


def default_match(target_fields, join_fields):
    """(target field, join field) a join most likely uses: the first field
    of this layer also in the join layer (letter case ignored), otherwise
    the first field of each."""
    join_names = {f.name().lower(): f.name() for f in join_fields}
    for f in target_fields:
        hit = join_names.get(f.name().lower())
        if hit is not None:
            return f.name(), hit
    first_t = target_fields.at(0).name() if target_fields.count() else None
    first_j = join_fields.at(0).name() if join_fields.count() else None
    return first_t, first_j


def joined_names(names, prefix):
    return [f"{prefix}{n}" for n in names]


def clashes(target_fields, names, prefix):
    """Joined field names already used by this layer (QGIS leaves those
    fields out)."""
    taken = {f.name().lower() for f in target_fields}
    return [n for n in joined_names(names, prefix) if n.lower() in taken]


def match_counts(target_source, target_idx, join_source, join_idx, job=None,
                 step=5000):
    """What a join of the two fields matches: a dict with ``records``
    (this layer), ``matched``, ``null`` (records with no value),
    ``join_records``, ``repeated`` (join values used by more than one join
    record) and ``repeated_records`` (records of this layer matching one of
    those)."""
    from qgis.core import QgsFeatureRequest
    from .compat import NO_GEOMETRY

    def values(source, idx):
        req = QgsFeatureRequest()
        req.setFlags(NO_GEOMETRY)
        req.setSubsetOfAttributes([idx])
        for n, feat in enumerate(source.getFeatures(req), 1):
            if job is not None and n % step == 0:
                job.check()
            yield feat.attributes()[idx]

    seen = {}
    join_records = 0
    for value in values(join_source, join_idx):
        join_records += 1
        key = join_key(value)
        if key is not None:
            seen[key] = seen.get(key, 0) + 1
    if job is not None:
        job.report(40)
    records = matched = null = repeated_records = 0
    for value in values(target_source, target_idx):
        records += 1
        key = join_key(value)
        if key is None:
            null += 1
            continue
        hits = seen.get(key, 0)
        if hits:
            matched += 1
            if hits > 1:
                repeated_records += 1
    return {
        "records": records,
        "matched": matched,
        "null": null,
        "join_records": join_records,
        "repeated": sum(1 for n in seen.values() if n > 1),
        "repeated_records": repeated_records,
    }


def summary(counts):
    """(headline, detail, level) for the window: level "success" (all
    match), "warning" (some) or "error" (none)."""
    records, matched = counts["records"], counts["matched"]
    pct = 100.0 * matched / records if records else 0.0
    if records == 0:
        return "This layer has no records", "", "warning"
    share = f"{pct:.0f}%" if matched in (0, records) or 1 <= pct <= 99 \
        else f"{pct:.1f}%"
    head = f"{matched:,} of {records:,} records match ({share})"
    notes = []
    if counts["null"]:
        notes.append(f"{counts['null']:,} with no value (NULL)")
    if counts["repeated"]:
        notes.append(
            f"{counts['repeated']:,} join value(s) repeat: "
            f"{counts['repeated_records']:,} record(s) get the last matching row")
    level = "success" if matched == records else ("error" if matched == 0
                                                  else "warning")
    return head, " · ".join(notes), level
