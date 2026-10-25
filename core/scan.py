# -*- coding: utf-8 -*-
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


def scan(spec, job, attr_idxs=(), want_fids=True, sort=None, sort_expr=None):
    """Read the scope.

    Returns (fids, columns) where columns[i] holds the raw values of
    attr_idxs[i] in the same record order as fids.  *sort* = (field index,
    descending) orders the records by that field, *sort_expr* =
    (expression, descending) by an expression (NULL always last);
    otherwise records are ordered by feature id.
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

    req = QgsFeatureRequest()
    geometry = spec.rect is not None or (sort_exp is not None and sort_exp.needsGeometry())
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

    fids = []
    keys = []
    cols = [[] for _ in attr_idxs]
    pairs = list(zip(cols, attr_idxs))
    total = float(spec.estimate or 1)
    n = 0
    for feat in spec.source.getFeatures(req):
        n += 1
        if n % 2000 == 0:
            job.check()
            job.report(min(99.0, 100.0 * n / total))
        fid = feat.id()
        if restrict is not None and fid not in restrict:
            continue
        if ctx is not None:
            ctx.setFeature(feat)
        if local_expr is not None:
            ok = local_expr.evaluate(ctx)
            if F.is_null(ok) or not ok:
                continue
        fids.append(fid)
        if sort_exp is not None:
            keys.append(sort_exp.evaluate(ctx))
        elif sort_idx is not None:
            keys.append(feat.attribute(sort_idx))
        for col, idx in pairs:
            col.append(feat.attribute(idx))
    job.check()

    if sort_idx is not None or sort_exp is not None:
        order = _sort_order(keys, desc)
    elif any(fids[i] > fids[i + 1] for i in range(len(fids) - 1)):
        order = sorted(range(len(fids)), key=fids.__getitem__)
    else:
        order = None
    if order is not None:
        fids = [fids[i] for i in order]
        cols = [[c[i] for i in order] for c in cols]
    return (fids if want_fids else None), cols


def _sort_order(keys, desc):
    present, missing = [], []
    for i, v in enumerate(keys):
        (missing if F.is_null(v) else present).append(i)
    gv, sk = F.group_value, F.sort_key
    present.sort(key=lambda i: sk(gv(keys[i])), reverse=desc)
    return present + missing
