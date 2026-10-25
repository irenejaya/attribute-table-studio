# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Table filters -> the layer's own filter (the Query Builder "subset string"),
so the map shows the same records as Attribute Explorer.

A subset string is written in the data provider's own language:

- memory and delimited text layers take QGIS expressions, so the table's
  expression is used as it is;
- GeoPackage, SQLite and SpatiaLite take SQLite SQL;
- other OGR formats (Shapefile, GeoJSON, ...) take OGR SQL;
- PostgreSQL takes PostgreSQL SQL.

A filter is only written as SQL where the provider answers exactly like the
table (e.g. case-insensitive "contains" with plain ASCII text in SQLite).
Otherwise :func:`ids_subset` lists the matching feature ids, and the panel
checks the resulting count against the table, so the map never differs
from it.

The map filter is temporary: it lives while its explorer is open, and the
layer gets its own filter back when it goes off.  The layers carrying one
are listed here (see :func:`hold`), so a project saved meanwhile is written
with the layer's own filter, and a layer still being edited when its
explorer closes gets its filter back as soon as editing stops (QGIS refuses
filter changes during editing).
"""

import json

from . import fields as F
from . import filters as FL

ID_LIMIT = 100000            # longest id list written into a subset string
# Layer properties written by earlier versions, which saved a map filter with
# the project; restore_forgotten() gives such layers their own filter back.
PROPERTY = "attribute_table_studio/map_filter_base"
STATE = "attribute_table_studio/map_filter"


def dialect(layer):
    """'qgis', 'sqlite', 'ogr', 'postgres' or None (not supported)."""
    provider = layer.providerType()
    if provider in ("memory", "delimitedtext"):
        return "qgis"
    if provider == "spatialite":
        return "sqlite"
    if provider == "postgres":
        return "postgres"
    if provider == "ogr":
        storage = (layer.dataProvider().storageType() or "").lower()
        if storage in ("gpkg", "sqlite") or "geopackage" in storage:
            return "sqlite"
        return "ogr"
    return None


def sql_string(text):
    return "'{}'".format(text.replace("'", "''"))


def _ascii(text):
    return all(ord(ch) < 128 for ch in text)


def _literal(value, dialect_name):
    if isinstance(value, bool):
        if dialect_name == "postgres":
            return "TRUE" if value else "FALSE"
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return FL.number_literal(value)
    return sql_string(str(value))


def filter_sql(flt, dialect_name, case_sensitive=False):
    """Subset text for one active filter, or None when the provider cannot
    give exactly the table's answer.  Raises FilterError for bad input."""
    if dialect_name == "qgis":
        return flt.build(case_sensitive)
    col = FL.quote_column(flt.field)
    op, kind = flt.op, flt.kind
    if op == "null":
        return f"{col} IS NULL"
    if op == "notnull":
        return f"{col} IS NOT NULL"
    if op == "in":
        if kind not in F.NUMERIC and kind not in (F.TEXT, F.BOOL):
            return None
        literals = [_literal(v, dialect_name) for v in flt.values if v is not None]
        parts = [f"{col} IN ({', '.join(literals)})"] if literals else []
        if any(v is None for v in flt.values):
            parts.append(f"{col} IS NULL")
        return parts[0] if len(parts) == 1 else f"({parts[0]} OR {parts[1]})"
    if kind in F.NUMERIC:
        if op == "between":
            lo, hi = _bounds(flt, FL.parse_number)
            return FL.ColumnFilter._range(col, lo, hi, FL.number_literal)
        value = FL.number_literal(FL.parse_number(flt.value))
        return f"{col} {FL._SQL_CMP[op]} {value}"
    if kind == F.BOOL:
        return f"{col} = {_literal(op == 'true', dialect_name)}"
    if kind == F.TEXT:
        if case_sensitive and op in ("contains", "starts"):
            return _exact_case_sql(col, op, str(flt.value), dialect_name)
        return _text_sql(col, op, str(flt.value), dialect_name)
    if kind in (F.DATE, F.DATETIME, F.TIME):
        return _temporal_sql(col, flt, dialect_name)
    return None


def _bounds(flt, parse):
    lo = parse(flt.value) if str(flt.value).strip() else None
    hi = parse(flt.value2) if str(flt.value2).strip() else None
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _text_sql(col, op, text, dialect_name):
    if op == "eq":
        return f"{col} = {sql_string(text)}"
    plain = not any(ch in text for ch in "%_\\")
    if dialect_name == "postgres":
        if op == "starts":
            if plain:
                return f"{col} ILIKE {sql_string(text + '%')}"
            return f"left(lower({col}), {len(text)}) = lower({sql_string(text)})"
        if plain:
            return f"{col} ILIKE {sql_string('%' + text + '%')}"
        return f"strpos(lower({col}), lower({sql_string(text)})) > 0"
    if not _ascii(text):
        return None              # SQLite and OGR SQL fold ASCII letters only
    if dialect_name == "sqlite":
        if op == "starts":
            if plain:
                return f"{col} LIKE {sql_string(text + '%')}"
            return f"substr(lower({col}), 1, {len(text)}) = {sql_string(text.lower())}"
        if plain:
            return f"{col} LIKE {sql_string('%' + text + '%')}"
        return f"instr(lower({col}), {sql_string(text.lower())}) > 0"
    if not plain:
        return None
    pattern = text + "%" if op == "starts" else "%" + text + "%"
    return f"{col} ILIKE {sql_string(pattern)}"


def _exact_case_sql(col, op, text, dialect_name):
    """Case-sensitive "contains" / "starts with" (binary comparison)."""
    if dialect_name == "sqlite":
        if op == "starts":
            return f"substr({col}, 1, {len(text)}) = {sql_string(text)}"
        return f"instr({col}, {sql_string(text)}) > 0"
    if dialect_name == "postgres":
        if op == "starts":
            return f"left({col}, {len(text)}) = {sql_string(text)}"
        return f"strpos({col}, {sql_string(text)}) > 0"
    return None


def _temporal_sql(col, flt, dialect_name):
    if dialect_name not in ("sqlite", "postgres"):
        return None
    op, kind = flt.op, flt.kind
    sqlite = dialect_name == "sqlite"
    if kind == F.TIME:
        parse, lhs = FL.parse_time, col
        text = (lambda t: t.strftime("%H:%M:%S"))
        literal = ((lambda t: sql_string(text(t))) if sqlite
                   else (lambda t: f"TIME {sql_string(text(t))}"))
    else:
        parse = FL.parse_date
        if sqlite:               # GeoPackage stores dates as ISO text
            lhs = col if kind == F.DATE else f"substr({col}, 1, 10)"
            literal = (lambda d: sql_string(d.isoformat()))
        else:
            lhs = col if kind == F.DATE else f"CAST({col} AS DATE)"
            literal = (lambda d: f"DATE {sql_string(d.isoformat())}")
    if op == "between":
        lo, hi = _bounds(flt, parse)
        return FL.ColumnFilter._range(col, lo, hi, literal, lhs)
    return f"{lhs} {FL._SQL_CMP[op]} {literal(parse(flt.value))}"


def filters_sql(layer, filters, case_sensitive=False, expression=None):
    """Subset text for the active *filters* and the table's own expression
    filter (without the layer's own filter): '' when none is active, None
    when they cannot be written exactly for this layer."""
    name = dialect(layer)
    if name is None:
        return None
    parts = []
    for flt in filters:
        if not flt.is_active():
            continue
        try:
            sql = filter_sql(flt, name, case_sensitive)
        except FL.FilterError:
            return None
        if not sql:
            return None
        parts.append(sql)
    if expression:
        if name != "qgis":             # QGIS expressions are not provider SQL
            return None
        parts.append(expression)
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else " AND ".join(f"({p})" for p in parts)


def ids_subset(layer, fids):
    """Subset text that keeps exactly the features *fids*, or None."""
    name = dialect(layer)
    if name is None or len(fids) > ID_LIMIT:
        return None
    if not fids:
        return "1 = 0"
    ids = ", ".join(str(int(f)) for f in sorted(fids))
    if name == "qgis":
        return f"$id IN ({ids})"
    pk = layer.dataProvider().pkAttributeIndexes()
    if len(pk) == 1:
        return f"{FL.quote_column(layer.fields().at(pk[0]).name())} IN ({ids})"
    if name == "ogr":
        return f"FID IN ({ids})"
    return None


def with_base(base, ours):
    """The layer's own filter AND ours (None when the layer's filter is a
    full SQL query that cannot be extended)."""
    base = (base or "").strip()
    if base.upper().startswith("SELECT"):
        return None
    if not ours:
        return base
    if not base:
        return ours
    return f"({base}) AND ({ours})"


def restore_forgotten(layer):
    """Projects saved by earlier versions while a map filter was on: give
    the layer its own filter back (a map filter no longer outlives its
    explorer)."""
    if layer.isEditable():
        return False
    restored = False
    text = layer.customProperty(STATE)
    if text is not None:
        try:
            state = json.loads(text)
        except (TypeError, ValueError):
            state = None
        if isinstance(state, dict) and state.get("applied") == layer.subsetString():
            layer.setSubsetString(str(state.get("base") or ""))
            restored = True
        layer.removeCustomProperty(STATE)
    base = layer.customProperty(PROPERTY)
    if base is not None:
        if not restored:
            layer.setSubsetString(str(base))
            restored = True
        layer.removeCustomProperty(PROPERTY)
    return restored


# ----------------------------------------------------------------------------
# Layers carrying a map filter
# ----------------------------------------------------------------------------

class Held:
    """The map filter one layer carries."""

    def __init__(self, layer, base):
        self.base = base                      # the layer's own filter
        self.base_source = layer.source()     # its data source with that filter
        self.applied = None                   # the filter on the layer now
        self.applied_source = None
        self.state = None                     # the table filters that made it
        self.waiting = None                   # (signal, slot) after its explorer closed


_HELD = {}                   # layer id -> Held


def hold(layer, base):
    """The map filter goes on: *base* is the layer's own filter."""
    release(layer.id())
    entry = _HELD[layer.id()] = Held(layer, base)
    return entry


def note_applied(layer, text, state=None):
    entry = _HELD.get(layer.id())
    if entry is not None:
        entry.applied = text
        entry.applied_source = layer.source()
        entry.state = state


def held(layer):
    return _HELD.get(layer.id()) if layer is not None else None


def _stop_waiting(entry):
    if entry.waiting is not None:
        signal, slot = entry.waiting
        entry.waiting = None
        try:
            signal.disconnect(slot)
        except (TypeError, RuntimeError):
            pass


def release(layer_id):
    """The layer no longer carries a map filter."""
    entry = _HELD.pop(layer_id, None)
    if entry is not None:
        _stop_waiting(entry)
    return entry


def restore_after_editing(layer):
    """The explorer closed while *layer* is being edited, so its map filter
    cannot go yet: give the layer its own filter back once editing stops."""
    entry = _HELD.get(layer.id())
    if entry is None:
        return
    layer_id = layer.id()

    def stopped():
        if _HELD.get(layer_id) is not entry:
            return
        release(layer_id)
        try:
            if layer.subsetString() == entry.applied:
                layer.setSubsetString(entry.base)
        except RuntimeError:          # layer already deleted
            pass

    _stop_waiting(entry)
    layer.editingStopped.connect(stopped)
    entry.waiting = (layer.editingStopped, stopped)


def adopt(layer):
    """An explorer opening on *layer* takes over a map filter still waiting
    for editing to stop.  Returns its entry, or None."""
    entry = _HELD.get(layer.id())
    if entry is None:
        return None
    if entry.waiting is None or layer.subsetString() != entry.applied:
        release(layer.id())
        return None
    _stop_waiting(entry)
    return entry


def _written_sources(only=None):
    """layer id -> (the data source a project would be written with, the
    same with the layer's own filter), for the layers carrying a map filter."""
    from qgis.core import QgsProject, QgsReadWriteContext   # (engines run without QGIS)
    project = QgsProject.instance()
    context = QgsReadWriteContext()
    context.setPathResolver(project.pathResolver())
    sources = {}
    for layer_id, entry in _HELD.items():
        if only is not None and layer_id != only:
            continue
        layer = project.mapLayer(layer_id)
        if layer is None or not entry.applied_source or layer.source() != entry.applied_source:
            continue
        sources[layer_id] = (layer.encodedSource(entry.applied_source, context),
                             layer.encodedSource(entry.base_source, context))
    return sources


def _own_datasource(element, document, pair):
    node = element.firstChildElement("datasource")
    if node.isNull() or node.text() != pair[0]:
        return
    while node.hasChildNodes():
        node.removeChild(node.firstChild())
    node.appendChild(document.createTextNode(pair[1]))


# A saved project never keeps a map filter: layers carrying one are written
# with their own filter, in their <maplayer> and in the layer tree.

def write_layer(layer, element, document):
    """QgsProject.writeMapLayer: the layer's <maplayer> element."""
    if layer.id() in _HELD:
        pair = _written_sources(layer.id()).get(layer.id())
        if pair is not None:
            _own_datasource(element, document, pair)


def write_project(document):
    """QgsProject.writeProject: the layer tree (and any <maplayer> already
    in the document)."""
    sources = _written_sources() if _HELD else {}
    if not sources:
        return
    layers = document.elementsByTagName("maplayer")
    for i in range(layers.count()):
        element = layers.at(i).toElement()
        pair = sources.get(element.firstChildElement("id").text())
        if pair is not None:
            _own_datasource(element, document, pair)
    nodes = document.elementsByTagName("layer-tree-layer")
    for i in range(nodes.count()):
        element = nodes.at(i).toElement()
        pair = sources.get(element.attribute("id"))
        if pair is not None and element.attribute("source") == pair[0]:
            element.setAttribute("source", pair[1])
