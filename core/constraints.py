# -*- coding: utf-8 -*-
"""
Records that fail their fields' constraints, for the Explorer's "Failing
constraints" scope.

The checks follow QgsVectorLayerUtils.validateAttribute: NOT NULL fails on
NULL, UNIQUE fails when a non-NULL value occurs more than once in the
layer, and an expression constraint fails unless it evaluates to true.
Each check keeps its strength, so hard and soft failures are told apart.

:class:`ConstraintSpec` is built on the GUI thread; :func:`failing` runs in
a background job on a thread-safe feature source (unsaved edits included).
"""

from collections import Counter

from qgis.core import (
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
    QgsFeatureRequest, QgsFieldConstraints,
)

from . import fields as F
from .compat import NO_GEOMETRY

_C = QgsFieldConstraints.Constraint
HARD = QgsFieldConstraints.ConstraintStrength.ConstraintStrengthHard


class ConstraintSpec:
    def __init__(self, layer):
        self.fields = layer.fields()
        self.checks = []     # (index, name, kind, expression, hard)
        for i in range(self.fields.count()):
            fld = self.fields.at(i)
            con = fld.constraints()
            flags = con.constraints()
            for kind, flag in (("notnull", _C.ConstraintNotNull),
                               ("unique", _C.ConstraintUnique),
                               ("expression", _C.ConstraintExpression)):
                if not flags & flag:
                    continue
                expr = con.constraintExpression() if kind == "expression" else ""
                if kind == "expression" and not expr:
                    continue
                hard = con.constraintStrength(flag) == HARD
                self.checks.append((i, fld.name(), kind, expr, hard))
        self.context = QgsExpressionContext(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer))

    def __bool__(self):
        return bool(self.checks)

    def describe(self):
        return [f"{name}: {'NOT NULL' if kind == 'notnull' else kind.upper() if kind == 'unique' else expr}"
                f" ({'hard' if hard else 'soft'})"
                for _i, name, kind, expr, hard in self.checks]


def failing(spec, source, job=None):
    """{fid: "hard" | "soft"} for every record that fails a check."""
    if not spec:
        return {}
    unique_idx = sorted({i for i, _n, kind, _e, _h in spec.checks if kind == "unique"})
    counts = {i: Counter() for i in unique_idx}
    if unique_idx:
        req = QgsFeatureRequest()
        req.setFlags(NO_GEOMETRY)
        req.setSubsetOfAttributes(unique_idx)
        for n, feat in enumerate(source.getFeatures(req)):
            if job is not None and n % 5000 == 0:
                job.check()
            for i in unique_idx:
                value = feat.attribute(i)
                if not F.is_null(value):
                    counts[i][F.group_value(value)] += 1

    exps = []
    ctx = QgsExpressionContext(spec.context)
    ctx.setFields(spec.fields)
    need_geometry = False
    for check in spec.checks:
        if check[2] == "expression":
            exp = QgsExpression(check[3])
            exp.prepare(ctx)
            need_geometry = need_geometry or exp.needsGeometry()
            exps.append((check, exp))
    req = QgsFeatureRequest()
    if not need_geometry:
        req.setFlags(NO_GEOMETRY)
    out = {}
    for n, feat in enumerate(source.getFeatures(req)):
        if job is not None and n % 2000 == 0:
            job.check()
        result = None
        for i, _name, kind, _expr, hard in spec.checks:
            if kind == "expression":
                continue
            value = feat.attribute(i)
            bad = (F.is_null(value) if kind == "notnull"
                   else not F.is_null(value) and counts[i][F.group_value(value)] > 1)
            if bad:
                result = "hard" if hard or result == "hard" else "soft"
        if exps:
            ctx.setFeature(feat)
            for (_i, _name, _kind, _expr, hard), exp in exps:
                value = exp.evaluate(ctx)
                if F.is_null(value) or not value:
                    result = "hard" if hard or result == "hard" else "soft"
        if result:
            out[feat.id()] = result
    return out
