# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Feature scans shared by Attribute Explorer and Pivot Table.

A :class:`ScanSpec` is created on the GUI thread (it snapshots the layer as
a thread-safe ``QgsVectorLayerFeatureSource``); :func:`scan` runs inside a
background job and reads only the attributes it needs, never geometry.

Scope = records available through the layer (provider subset included,
unsaved edits included) AND the filter expression AND, when given, a fixed
set of feature ids (Selected only, Edited and new, Failing constraints, or
records handed over from a pivot cell) AND, when given, a map extent.  A
QGIS request cannot combine an id list with an expression, so when both
apply the ids are fetched and the expression is evaluated here.

*restrict* may be replaced inside the job before :func:`scan` runs (the
"Failing constraints" scope finds its ids in the same job).
"""

from qgis.core import (
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
    QgsFeatureRequest, QgsVectorLayerFeatureSource,
)

from . import compact as CP
from . import fields as F
from .compat import NO_GEOMETRY

# id sets up to this size are fetched by id; larger ones are matched while
# scanning the (provider filtered) layer
FID_FILTER_LIMIT = 200000


class ScanSpec:
    def __init__(self, layer, expression=None, restrict=None, rect=None):
        self.source = QgsVectorLayerFeatureSource(layer)
        self.fields = layer.fields()
        self.expression = expression or None
        self.restrict = set(restrict) if restrict is not None else None
        self.rect = rect                # map extent, in the layer's CRS
        # project / layer variables for expressions evaluated in the job
        self.context = QgsExpressionContext(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        count = layer.featureCount()
        if self.restrict is not None:
            self.estimate = len(self.restrict)
        else:
            self.estimate = count if count and count > 0 else 0
        self.layer_name = layer.name()

    def referenced(self):
        if not self.expression:
            return set()
        exp = QgsExpression(self.expression)
        return {i for i in exp.referencedAttributeIndexes(self.fields) if i >= 0}


def scan(spec, job, attr_idxs=(), want_fids=True, sort=None, sort_expr=None,
         builders=None):
    """Read the scope.

    Returns (fids, columns) where columns[i] holds the raw values of
    attr_idxs[i] in the same record order as fids.  *sort* = (field index,
    descending) orders the records by that field, *sort_expr* =
    (expression, descending) by an expression (NULL always last);
    otherwise records are ordered by feature id.  The fids are a compact
    list (``array('q')``), or None when not wanted (then none are kept).

    *builders* (one per attr_idxs entry, see :mod:`.stats`) take each value
    as it is read instead of keeping it: then *columns* is the builders, and
    a column costs what its builder keeps (8 bytes a number or date, the
    distinct texts), not a Python object per value.
    """
    attr_idxs = list(attr_idxs)
    sort_idx, desc = sort if sort else (None, False)
    need = set(attr_idxs)
    if sort_idx is not None:
        need.add(sort_idx)
    sort_exp = None
    if sort_expr:
        sort_exp = QgsExpression(sort_expr[0])
        desc = bool(sort_expr[1])
        need |= {i for i in sort_exp.referencedAttributeIndexes(spec.fields) if i >= 0}

    geometry = spec.rect is not None or (sort_exp is not None and sort_exp.needsGeometry())
    req, restrict, local_expr, need = _plan(spec, need, geometry)
    if sort_exp is not None and sort_exp.referencedColumns() \
            and QgsFeatureRequest.ALL_ATTRIBUTES in sort_exp.referencedColumns():
        need = set(range(spec.fields.count()))
    req.setSubsetOfAttributes(sorted(need))

    ctx = None
    if local_expr is not None or sort_exp is not None:
        ctx = QgsExpressionContext(spec.context)
        ctx.setFields(spec.fields)
        if local_expr is not None:
            local_expr.prepare(ctx)
        if sort_exp is not None:
            sort_exp.prepare(ctx)

    sorting = sort_idx is not None or sort_exp is not None
    collect = builders is None
    fids = CP.ids() if (want_fids or sorting or (collect and attr_idxs)) else None
    fids_append = fids.append if fids is not None else None
    keys = []
    if collect:
        cols = [[] for _ in attr_idxs]
        pairs = [(c.append, idx) for c, idx in zip(cols, attr_idxs)]
    else:
        from .stats import shed           # text too big to share the read
        cols = builders
        pairs = [(b.add, idx) for b, idx in zip(builders, attr_idxs)]
    total = float(spec.estimate or 1)
    # most fields wanted: one call per record for all its values (twice as
    # fast as one call per value); a few of many: one call per value
    whole = len(pairs) > 1 and len(pairs) * 4 >= spec.fields.count()
    n = 0
    for feat in spec.source.getFeatures(req):
        n += 1
        if n % 2000 == 0:
            job.check()
            job.report(min(99.0, 100.0 * n / total))
            if not collect and n % 50000 == 0 and shed(builders):
                pairs = [(b.add, idx) for b, idx in zip(builders, attr_idxs)
                         if not b.dropped]
        fid = feat.id()
        if restrict is not None and fid not in restrict:
            continue
        if ctx is not None:
            ctx.setFeature(feat)
        if local_expr is not None:
            ok = local_expr.evaluate(ctx)
            if F.is_null(ok) or not ok:
                continue
        if fids_append is not None:
            fids_append(fid)
        if sort_exp is not None:
            keys.append(sort_exp.evaluate(ctx))
        elif sort_idx is not None:
            keys.append(feat.attribute(sort_idx))
        if whole:
            attrs = feat.attributes()
            for add, idx in pairs:
                add(attrs[idx])
        else:
            for add, idx in pairs:
                add(feat.attribute(idx))
    job.check()

    order = None
    if sorting:
        order = _sort_order(keys, desc)
        keys = None
    elif fids is not None and not CP.ascending(fids):
        if collect and attr_idxs:
            order = sorted(range(len(fids)), key=fids.__getitem__)
        else:
            fids = CP.sort_ids(fids)            # feature id order
    if order is not None:
        fids = CP.take(fids, order)
        if collect:
            cols = [[c[i] for i in order] for c in cols]
    return (fids if want_fids else None), cols


def _plan(spec, need, geometry):
    """(request, restrict, local_expr, need) reading the scope of *spec*:
    the id set goes to the provider when it can, otherwise it is matched
    while reading (*restrict*); an expression the provider cannot combine
    with the ids is evaluated while reading (*local_expr*)."""
    need = set(need)
    req = QgsFeatureRequest()
    if not geometry:
        req.setFlags(NO_GEOMETRY)
    if spec.rect is not None:
        req.setFilterRect(spec.rect)
    restrict = spec.restrict
    local_expr = None
    if restrict is not None and (spec.expression is None
                                 or len(restrict) <= FID_FILTER_LIMIT):
        req.setFilterFids(sorted(restrict))
        restrict = None                     # the provider applies it
        if spec.expression:
            local_expr = QgsExpression(spec.expression)
            need |= spec.referenced()
    elif spec.expression:
        req.setFilterExpression(spec.expression)
        need |= spec.referenced()
    return req, restrict, local_expr, need


def stream(spec, job, attr_idxs=(), step=5000, geometry=False):
    """Yield the features of the scope one by one (no geometry unless a map
    extent or *geometry* needs it), reading only *attr_idxs*.  Nothing is
    kept, so it suits millions of records; progress and cancelling go
    through *job*."""
    req, restrict, local_expr, need = _plan(spec, attr_idxs,
                                            spec.rect is not None or geometry)
    req.setSubsetOfAttributes(sorted(need))
    ctx = None
    if local_expr is not None:
        ctx = QgsExpressionContext(spec.context)
        ctx.setFields(spec.fields)
        local_expr.prepare(ctx)
    total = float(spec.estimate or 1)
    n = 0
    for feat in spec.source.getFeatures(req):
        n += 1
        if n % step == 0:
            job.check()
            job.report(min(99.0, 100.0 * n / total))
        if restrict is not None and feat.id() not in restrict:
            continue
        if local_expr is not None:
            ctx.setFeature(feat)
            ok = local_expr.evaluate(ctx)
            if F.is_null(ok) or not ok:
                continue
        yield feat
    job.check()


def _sort_order(keys, desc):
    present, missing = [], []
    for i, v in enumerate(keys):
        (missing if F.is_null(v) else present).append(i)
    gv, sk = F.group_value, F.sort_key
    present.sort(key=lambda i: sk(gv(keys[i])), reverse=desc)
    return present + missing
