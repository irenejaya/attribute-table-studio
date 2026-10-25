# -*- coding: utf-8 -*-
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
"""

from . import fields as F
from . import filters as FL

ID_LIMIT = 100000            # longest id list written into a subset string
# the layer's own filter, kept on the layer while the map filter is on, so a
# project saved in that state gets it back when it is opened again
PROPERTY = "attribute_table_studio/map_filter_base"   # before map filters persisted
# A map filter left on when its explorer closes stays on the layer (like a
# Query Builder filter); this records how to resume and undo it.
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
    the layer its own filter back (map filters did not persist then)."""
    base = layer.customProperty(PROPERTY)
    if base is None or layer.isEditable() or layer.customProperty(STATE):
        return False
    layer.setSubsetString(str(base))
    layer.removeCustomProperty(PROPERTY)
    return True
