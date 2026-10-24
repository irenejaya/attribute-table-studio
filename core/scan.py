# -*- coding: utf-8 -*-
"""
Feature scans shared by Attribute Explorer and Pivot Table.

A :class:`ScanSpec` is created on the GUI thread (it snapshots the layer as
a thread-safe ``QgsVectorLayerFeatureSource``); :func:`scan` runs inside a
background job and reads only the attributes it needs, never geometry.

Scope = records available through the layer (provider subset included)
AND the filter expression AND, when given, a fixed set of feature ids
(Selected only, or records handed over from a pivot cell).  A QGIS request
cannot combine an id list with an expression, so when both apply the ids
are fetched and the expression is evaluated here.
"""

from qgis.core import (
    QgsExpression, QgsExpressionContext, QgsFeatureRequest,
    QgsVectorLayerFeatureSource,
)

from . import fields as F
from .compat import NO_GEOMETRY

# id sets up to this size are fetched by id; larger ones are matched while
# scanning the (provider filtered) layer
FID_FILTER_LIMIT = 200000


class ScanSpec:
    def __init__(self, layer, expression=None, restrict=None):
        self.source = QgsVectorLayerFeatureSource(layer)
        self.fields = layer.fields()
        self.expression = expression or None
        self.restrict = set(restrict) if restrict is not None else None
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


def scan(spec, job, attr_idxs=(), want_fids=True, sort=None):
    """Read the scope.

    Returns (fids, columns) where columns[i] holds the raw values of
    attr_idxs[i] in the same record order as fids.  *sort* = (field index,
    descending) orders the records by that field (NULL always last);
    otherwise records are ordered by feature id.
    """
    attr_idxs = list(attr_idxs)
    sort_idx, desc = sort if sort else (None, False)
    need = set(attr_idxs)
    if sort_idx is not None:
        need.add(sort_idx)

    req = QgsFeatureRequest()
    req.setFlags(NO_GEOMETRY)
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
    req.setSubsetOfAttributes(sorted(need))

    ctx = None
    if local_expr is not None:
        ctx = QgsExpressionContext()
        ctx.setFields(spec.fields)
        local_expr.prepare(ctx)

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
        if local_expr is not None:
            ctx.setFeature(feat)
            ok = local_expr.evaluate(ctx)
            if F.is_null(ok) or not ok:
                continue
        fids.append(fid)
        if sort_idx is not None:
            keys.append(feat.attribute(sort_idx))
        for col, idx in pairs:
            col.append(feat.attribute(idx))
    job.check()

    if sort_idx is not None:
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
