# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Geometry and Table Compare: export the compared features to a GeoPackage
(Export ▾ › GeoPackage), one layer per kind of change (added, deleted,
modified, unchanged), every layer with the same fields:

- ``CHANGE``, ``DECISION`` and ``CHANGED_FIELDS`` (the columns whose value
  changed, in table order; ``geometry`` when the shape changed);
- the table's columns, each holding the reviewed value: NEW, or OLD where
  the change was rejected (a deleted feature holds its OLD values);
- ``<column>_OLD`` right after every column that changed in an exported
  modified row: the OLD value where that column changed, NULL elsewhere.

The geometry is NEW, or OLD for a deleted feature and for a modified feature
whose geometry change was rejected, in the NEW layer's CRS.  Numbers, dates
and booleans keep their types; a column whose type differs between OLD and
NEW is written as text.
"""

import os

from qgis.PyQt.QtCore import QMetaType
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsCsException, QgsFeature, QgsFeatureRequest, QgsField, QgsFields,
    QgsGeometry, QgsProviderRegistry, QgsVectorFileWriter, QgsWkbTypes,
)

from . import fields as F
from .compat import GEOM_NULL, NO_GEOMETRY

_MT = getattr(QMetaType, "Type", QMetaType)

KINDS = ("Added", "Deleted", "Modified", "Unchanged")
REVIEWABLE = ("Added", "Modified")

# geometry measures read per feature (the table's "Compare geometry" options)
GEOM_PROPS = ("_geom_length", "_geom_area", "_centroid_x", "_centroid_y",
              "_vertex_count")
# values that belong to the shape: rejecting one keeps the OLD geometry
GEOMETRY_VALUES = frozenset(GEOM_PROPS + ("_parts",))

CHANGE, DECISION, CHANGED_FIELDS = "CHANGE", "DECISION", "CHANGED_FIELDS"
OLD_SUFFIX = "_OLD"
CHUNK = 2000                    # features fetched per request


class Row:
    """One table row: its join *key*, *status* (Added ...), *decision*
    (Accepted / Rejected / Mixed / Pending, None when it cannot be reviewed),
    the *changed* column names (table order) and the *rejected* ones."""

    __slots__ = ("key", "status", "decision", "changed", "rejected")

    def __init__(self, key, status, decision=None, changed=(), rejected=()):
        self.key = key
        self.status = status
        self.decision = decision
        self.changed = list(changed)
        self.rejected = set(rejected)


class _Column:
    __slots__ = ("name", "kind", "field", "old_field", "as_text", "identity",
                 "new_idx", "old_idx")

    def __init__(self, name, kind):
        self.name = name                 # the table's column name
        self.kind = kind                 # "attr", "prop" or "extra"
        self.field = None                # output QgsField (reviewed value)
        self.old_field = None            # output QgsField of <name>_OLD
        self.as_text = False             # OLD / NEW types differ: write text
        self.identity = False            # the GeoPackage fid: never reviewed
        self.new_idx = -1
        self.old_idx = -1


def layer_name(kind):
    return kind.lower()


def layer_file(layer):
    """Normalised path of the file a layer reads, or ''."""
    try:
        parts = QgsProviderRegistry.instance().decodeUri(
            layer.providerType(), layer.source())
    except Exception:
        return ""
    path = parts.get("path") or ""
    return os.path.normcase(os.path.abspath(path)) if path else ""


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------

def _limit(a, b):
    """A length / precision both definitions accept (0 = no limit)."""
    return max(a, b) if a > 0 and b > 0 else 0


def merged_field(name, new_f, old_f):
    """(QgsField for the reviewed values of *name*, as_text): the NEW
    definition, widened so OLD values fit; text when the types differ."""
    if new_f is None or old_f is None:
        f = QgsField(new_f if new_f is not None else old_f)
        f.setName(name)
        return f, False
    same = (F.type_id(new_f.type()) == F.type_id(old_f.type())
            and F.type_id(new_f.subType()) == F.type_id(old_f.subType()))
    new_k, old_k = F.field_kind(new_f), F.field_kind(old_f)
    if same:
        f = QgsField(new_f)
        f.setLength(_limit(new_f.length(), old_f.length()))
        f.setPrecision(_limit(new_f.precision(), old_f.precision()))
        f.setName(name)
        return f, False
    if new_k == old_k == F.INTEGER:
        return QgsField(name, _MT.LongLong, "int8"), False
    if new_k in F.NUMERIC and old_k in F.NUMERIC:
        return QgsField(name, _MT.Double, "double"), False
    return QgsField(name, _MT.QString, "string"), True


def _measure_field(name):
    if name in ("_vertex_count", "_parts", "_parts_changed"):
        return QgsField(name, _MT.Int, "integer")
    if name == "_geom_status":
        return QgsField(name, _MT.QString, "string")
    return QgsField(name, _MT.Double, "double")


def _unique(name, used):
    candidate, n = name, 2
    while candidate.lower() in used:
        candidate = f"{name}_{n}"
        n += 1
    used.add(candidate.lower())
    return candidate


def _is_key(layer, name):
    idx = layer.fields().indexFromName(name)
    return idx >= 0 and idx in layer.primaryKeyAttributes()


class Schema:
    """Output fields shared by every layer, and where each value comes
    from.  *columns* are the table's (kind, name) after Change / Decision."""

    def __init__(self, columns, old_layer, new_layer, changed_any, kinds):
        old_fields, new_fields = old_layer.fields(), new_layer.fields()
        self.columns = []
        used = set()
        for kind, name in columns:
            col = _Column(name, kind)
            if kind == "attr":
                n_i, o_i = new_fields.indexFromName(name), old_fields.indexFromName(name)
                if n_i < 0 and o_i < 0:
                    continue             # gone from both layers since Compare
                col.new_idx, col.old_idx = n_i, o_i
                col.field, col.as_text = merged_field(
                    name, new_fields.at(n_i) if n_i >= 0 else None,
                    old_fields.at(o_i) if o_i >= 0 else None)
            else:
                col.field = _measure_field(name)
            col.field.setName(_unique(name, used))
            self.columns.append(col)
        # the fixed columns keep their names unless a source field has them
        self.fixed = [_unique(n, used) for n in (CHANGE, DECISION, CHANGED_FIELDS)]
        for col in self.columns:
            if col.name in changed_any:
                if col.kind == "attr":
                    o_i = col.old_idx
                    old = QgsField(old_fields.at(o_i)) if o_i >= 0 else QgsField(col.field)
                else:
                    old = _measure_field(col.name)
                old.setName(_unique(col.name + OLD_SUFFIX, used))
                col.old_field = old

        # GeoPackage stores the feature id in a column called fid.  A source
        # fid that is its layer's key (a GeoPackage read by QGIS) stays the
        # feature id and keeps the source feature's value; any other column
        # called fid is written as an ordinary column.
        self.fid_option = None
        fid_cols = [c for c in self.columns if c.field.name().lower() == "fid"]
        if fid_cols:
            col = fid_cols[0]
            from_new = any(k != "Deleted" for k in kinds)
            from_old = "Deleted" in kinds
            keep = (col.kind == "attr" and not col.as_text
                    and F.field_kind(col.field) == F.INTEGER
                    and (not from_new or _is_key(new_layer, col.name))
                    and (not from_old or _is_key(old_layer, col.name)))
            if keep:
                col.identity = True
            else:
                self.fid_option = _unique("ogc_fid", used)

    def fields(self):
        out = QgsFields()
        for name in self.fixed:
            out.append(QgsField(name, _MT.QString, "string"))
        for col in self.columns:
            out.append(col.field)
            if col.old_field is not None:
                out.append(col.old_field)
        return out


# ----------------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------------

def output_geometry(old_layer, new_layer):
    """(wkb type, crs) of the output layers: the NEW layer's, widened to
    multi / Z / M when OLD geometries of the same kind need it."""
    if new_layer.isSpatial():
        wkb, crs = new_layer.wkbType(), new_layer.crs()
        other = old_layer.wkbType() if old_layer.isSpatial() else None
        if other is not None and (QgsWkbTypes.geometryType(other)
                                  == QgsWkbTypes.geometryType(wkb)):
            if QgsWkbTypes.isMultiType(other):
                wkb = QgsWkbTypes.multiType(wkb)
            if QgsWkbTypes.hasZ(other):
                wkb = QgsWkbTypes.addZ(wkb)
            if QgsWkbTypes.hasM(other):
                wkb = QgsWkbTypes.addM(wkb)
        return wkb, crs
    if old_layer.isSpatial():
        return old_layer.wkbType(), old_layer.crs()
    return Qgis.WkbType.NoGeometry, None


def _fit(geom, wkb):
    """*geom* ready for a layer of type *wkb* (None when it does not fit)."""
    if geom is None or geom.isNull():
        return None
    want = QgsWkbTypes.geometryType(wkb)
    if (want != Qgis.GeometryType.Unknown
            and QgsWkbTypes.geometryType(geom.wkbType()) != want):
        return None
    geom = QgsGeometry(geom)
    if QgsWkbTypes.isMultiType(wkb) and not geom.isMultipart():
        geom.convertToMultiType()
    return geom


def _props(geom, names):
    """The table's geometry measures of *geom* (in its own layer's CRS)."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return {}
    out = {}
    if "_geom_length" in names:
        out["_geom_length"] = round(geom.length(), 6)
    if "_geom_area" in names:
        out["_geom_area"] = round(geom.area(), 6)
    if "_centroid_x" in names or "_centroid_y" in names:
        c = geom.centroid().asPoint()
        out["_centroid_x"] = round(c.x(), 6)
        out["_centroid_y"] = round(c.y(), 6)
    if "_vertex_count" in names:
        out["_vertex_count"] = sum(1 for _ in geom.vertices())
    return out


def _round(value):
    return round(value, 6) if isinstance(value, float) else value


def _extra(name, status, gd, side):
    """A geometry-comparison column (``_geom_status``, ``_added_area`` ...)
    of one row; *side* "old" / "new" picks the part count."""
    if gd is None:
        return None
    both = status in ("Modified", "Unchanged")
    if name == "_geom_status":
        return ("Changed" if gd["changed"] else "Same") if both else None
    if name.startswith("_added_"):
        return _round(gd["added_m"])
    if name.startswith("_removed_"):
        return _round(gd["removed_m"])
    if name == "_changed_pct":
        return _round(gd["pct"])
    if name == "_parts":
        if status == "Added":
            return gd["parts_new"]
        if status == "Deleted":
            return gd["parts_old"]
        return gd["parts_old"] if side == "old" else gd["parts_new"]
    if name == "_parts_changed":
        return gd.get("parts_changed", 0) if both else None
    return None


# ----------------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------------

def _fetch(layer, ids, geometry):
    ids = sorted({i for i in ids if i is not None})
    if not ids:
        return {}
    req = QgsFeatureRequest().setFilterFids(ids)
    if not geometry:
        req.setFlags(NO_GEOMETRY)
    return {f.id(): QgsFeature(f) for f in layer.getFeatures(req)}


def _text(value):
    return None if F.is_null(value) else F.display_value(value)


class _RowWriter:
    def __init__(self, schema, wkb, old_layer, new_layer, crs, context,
                 geom_diffs):
        self.schema = schema
        self.wkb = wkb
        self.spatial = (wkb != Qgis.WkbType.NoGeometry
                        and QgsWkbTypes.geometryType(wkb) != GEOM_NULL)
        self.geom_diffs = geom_diffs or {}
        self.prop_names = {c.name for c in schema.columns if c.kind == "prop"}
        self.transform = None
        if (self.spatial and old_layer.isSpatial() and crs is not None
                and old_layer.crs() != crs):
            self.transform = QgsCoordinateTransform(old_layer.crs(), crs, context)
        self.fields = schema.fields()

    def feature(self, row, old_f, new_f):
        status = row.status
        gd = self.geom_diffs.get(row.key)
        props = {}

        def prop(side):
            if side not in props:
                src = old_f if side == "old" else new_f
                props[side] = _props(src.geometry() if src is not None
                                     else None, self.prop_names)
            return props[side]

        def value(col, side):
            if col.kind == "attr":
                src, idx = (old_f, col.old_idx) if side == "old" else (new_f, col.new_idx)
                if src is None or idx < 0:
                    return None
                return src.attribute(idx)
            if col.kind == "prop":
                return prop(side).get(col.name)
            return _extra(col.name, status, gd, side)

        changed = list(row.changed)
        if status == "Modified" and gd is not None and gd["changed"]:
            changed.append("geometry")
        attrs = [status, row.decision if status in REVIEWABLE else None,
                 ", ".join(changed) if status == "Modified" and changed else None]
        for col in self.schema.columns:
            if col.identity:
                side = "old" if status == "Deleted" else "new"
            elif status == "Deleted" or (status == "Modified"
                                         and col.name in row.rejected):
                side = "old"
            else:
                side = "new"
            v = value(col, side)
            attrs.append(_text(v) if col.as_text else v)
            if col.old_field is not None:
                attrs.append(value(col, "old") if status == "Modified"
                             and col.name in row.changed else None)

        feat = QgsFeature(self.fields)
        feat.setAttributes(attrs)
        if self.spatial:
            old_geom = status == "Deleted" or (
                status == "Modified" and (row.decision == "Rejected"
                                          or row.rejected & GEOMETRY_VALUES))
            src = old_f if old_geom else new_f
            geom = None
            if src is not None and src.hasGeometry():
                geom = QgsGeometry(src.geometry())
                if old_geom and self.transform is not None:
                    try:
                        geom.transform(self.transform)
                    except QgsCsException:
                        geom = None
            geom = _fit(geom, self.wkb)
            if geom is not None:
                feat.setGeometry(geom)
        return feat


def export(path, rows, columns, old_layer, new_layer, fid_maps, context,
           geom_diffs=None, progress=None):
    """Write *rows* (list of :class:`Row`, table order) to the GeoPackage
    *path*, replacing the file.  *columns*: the table's (kind, name) after
    Change / Decision; *fid_maps*: {"old"/"new": {join key: feature id}}.

    Returns ``(written, skipped)``: [(kind, layer name, count)] in KINDS
    order, and the number of rows whose feature is no longer in its layer.
    *progress(done, total)* is called now and then."""
    by_kind = {k: [] for k in KINDS}
    for row in rows:
        by_kind.setdefault(row.status, []).append(row)
    kinds = [k for k in KINDS if by_kind.get(k)]
    if not kinds:
        return [], 0
    changed_any = set()
    for row in by_kind["Modified"]:
        changed_any.update(row.changed)

    schema = Schema(columns, old_layer, new_layer, changed_any, kinds)
    wkb, crs = output_geometry(old_layer, new_layer)
    rw = _RowWriter(schema, wkb, old_layer, new_layer, crs, context, geom_diffs)
    need_geom = rw.spatial or bool(rw.prop_names)
    old_map, new_map = fid_maps.get("old", {}), fid_maps.get("new", {})

    total = sum(len(by_kind[k]) for k in kinds)
    done = skipped = 0
    written = []
    first = True
    for kind in kinds:
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.fileEncoding = "UTF-8"
        options.layerName = layer_name(kind)
        if schema.fid_option:
            options.layerOptions = [f"FID={schema.fid_option}"]
        if not first:
            options.actionOnExistingFile = \
                QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
        writer = QgsVectorFileWriter.create(
            path, rw.fields, wkb,
            crs if crs is not None else QgsCoordinateReferenceSystem(),
            context, options)
        if writer is None or writer.hasError():
            message = writer.errorMessage() if writer is not None else ""
            raise RuntimeError(message or f"Could not create {path}")
        first = False
        count = 0
        try:
            todo = by_kind[kind]
            use_new = kind != "Deleted"
            use_old = kind in ("Deleted", "Modified")
            for start in range(0, len(todo), CHUNK):
                chunk = todo[start:start + CHUNK]
                new_f = _fetch(new_layer, [new_map.get(r.key) for r in chunk],
                               need_geom) if use_new else {}
                old_f = _fetch(old_layer, [old_map.get(r.key) for r in chunk],
                               need_geom) if use_old else {}
                for row in chunk:
                    nf = new_f.get(new_map.get(row.key)) if use_new else None
                    of = old_f.get(old_map.get(row.key)) if use_old else None
                    if (use_new and nf is None) or (use_old and of is None):
                        skipped += 1
                        continue
                    if not writer.addFeature(rw.feature(row, of, nf)):
                        raise RuntimeError(writer.errorMessage()
                                           or "Could not write a feature")
                    count += 1
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        finally:
            del writer                   # closes the layer in the file
        written.append((kind, layer_name(kind), count))
    return written, skipped
