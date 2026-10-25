# -*- coding: utf-8 -*-
"""
Attribute Explorer layer operations shared by the edit row, the Quick update
row, the Field calculator and the menus.

Every mutation goes through the layer's edit buffer and is grouped as one
undo command; a cancelled batch destroys its incomplete command, so a
partial result is never left behind or reported as complete.
"""

import datetime

from qgis.PyQt.QtCore import QDate, QDateTime, QTime, Qt
from qgis.PyQt.QtWidgets import QApplication, QProgressDialog
from qgis.core import (
    Qgis, QgsDistanceArea, QgsExpression, QgsFeatureRequest, QgsGeometry,
    QgsProject, QgsVectorLayer, QgsVectorLayerUtils,
)

from ..core import fields as F
from ..core.compat import NO_GEOMETRY

BATCH_PROGRESS = 2000            # show a cancellable progress dialog above this
RECENT = "fieldcalc"             # QGIS's own field calculator history (shared)


class OperationError(Exception):
    """A user-facing problem (bad expression, value that cannot be stored)."""


def coerce(field, value):
    """*value* converted for *field* the way QGIS stores calculated values:
    integers round, decimals round to the field precision, text checks the
    field length, dates parse ISO text.  Raises OperationError."""
    if F.is_null(value):
        return None
    kind = F.field_kind(field)
    name = field.name()
    try:
        if kind in (F.INTEGER,):
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, str):
                value = float(value.strip().replace(",", ""))
            return int(round(float(value)))
        if kind == F.REAL:
            if isinstance(value, str):
                value = float(value.strip().replace(",", ""))
            value = float(value)
            prec = field.precision()
            return round(value, prec) if prec > 0 else value
        if kind == F.BOOL:
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "t", "yes", "y")
            return bool(value)
        if kind == F.DATE:
            if isinstance(value, QDateTime):
                return value.date()
            if isinstance(value, (QDate, datetime.date)) and not isinstance(value, datetime.datetime):
                return value
            if isinstance(value, datetime.datetime):
                return value.date()
            date = QDate.fromString(str(value)[:10], Qt.DateFormat.ISODate)
            if not date.isValid():
                raise ValueError(value)
            return date
        if kind == F.DATETIME:
            if isinstance(value, (QDateTime, datetime.datetime)):
                return value
            if isinstance(value, QDate):
                return QDateTime(value, QTime(0, 0))
            dt = QDateTime.fromString(str(value), Qt.DateFormat.ISODate)
            if not dt.isValid():
                raise ValueError(value)
            return dt
        if kind == F.TEXT:
            text = value if isinstance(value, str) else F.display_value(value)
            if field.length() > 0 and len(text) > field.length():
                raise OperationError(f"'{text[:40]}' is longer than the {field.length()} "
                                     f"characters {name} can hold")
            return text
    except OperationError:
        raise
    except (TypeError, ValueError, OverflowError):
        raise OperationError(f"{F.display_value(value)!s} cannot be stored in {name} "
                             f"({field.typeName()})")
    return value


# ----------------------------------------------------------------------------
# the QGIS field calculator's evaluation set-up
# ----------------------------------------------------------------------------

def geometry_calculator(layer):
    """Measures like the QGIS field calculator: on the project's ellipsoid
    (planar when the project has none)."""
    project = QgsProject.instance()
    da = QgsDistanceArea()
    da.setSourceCrs(layer.crs(), project.transformContext())
    da.setEllipsoid(project.ellipsoid())
    return da


def calculator_context(layer):
    """Global, project and layer scopes plus @row_number (as in QGIS), for
    the expression builder."""
    ctx = layer.createExpressionContext()
    ctx.lastScope().setVariable("row_number", 1, True)
    ctx.setHighlightedVariables(["row_number"])
    return ctx


def calculator_expression(layer, expression):
    """(expression, context) prepared the way the QGIS field calculator
    prepares them: $area / $length / $perimeter use the project's ellipsoid
    and distance / area units.  Raises OperationError for a bad expression."""
    exp = QgsExpression(expression)
    if not expression.strip() or exp.hasParserError():
        raise OperationError(exp.parserErrorString() or "Enter an expression")
    project = QgsProject.instance()
    exp.setGeomCalculator(geometry_calculator(layer))
    exp.setDistanceUnits(project.distanceUnits())
    exp.setAreaUnits(project.areaUnits())
    ctx = layer.createExpressionContext()
    # prepared before @row_number exists, so it is not folded into a constant
    exp.prepare(ctx)
    return exp, ctx


def _evaluate(exp, ctx, feat, row_number):
    ctx.setFeature(feat)
    ctx.lastScope().setVariable("row_number", row_number)
    return exp.evaluate(ctx)


def sample_value(layer, expression, fid):
    """The expression's value for one feature (as the first row)."""
    exp, ctx = calculator_expression(layer, expression)
    value = _evaluate(exp, ctx, layer.getFeature(fid), 1)
    if exp.hasEvalError():
        raise OperationError(exp.evalErrorString())
    return value


def evaluate_preview(layer, idx, expression, fids, limit=6):
    """[(fid, old, new)] for the first *limit* target features, without
    writing anything.  Raises OperationError for a bad expression."""
    exp, ctx = calculator_expression(layer, expression)
    field = layer.fields().at(idx)
    out = []
    req = QgsFeatureRequest().setFilterFids(sorted(fids)[:limit])
    if not exp.needsGeometry():
        req.setFlags(NO_GEOMETRY)
    for n, feat in enumerate(layer.getFeatures(req), 1):
        value = _evaluate(exp, ctx, feat, n)
        if exp.hasEvalError():
            raise OperationError(exp.evalErrorString())
        out.append((feat.id(), feat.attribute(idx), coerce(field, value)))
    return out


def apply_expression(layer, idx, expression, fids, title, parent=None,
                     own_command=True):
    """Write *expression* into field *idx* of every feature in *fids* as one
    undo command.  Returns the number of changed values; raises
    OperationError (nothing is written) or returns None when cancelled.
    With *own_command* False the caller holds the undo command (and
    destroys it on an error or a cancel)."""
    exp, ctx = calculator_expression(layer, expression)
    if not layer.isEditable():
        raise OperationError("Start editing first")
    field = layer.fields().at(idx)
    ids = sorted(fids)
    req = QgsFeatureRequest().setFilterFids(ids)
    if not exp.needsGeometry():
        req.setFlags(NO_GEOMETRY)
    progress = None
    if len(ids) > BATCH_PROGRESS and parent is not None:
        progress = QProgressDialog(f"{title}…", "Cancel", 0, len(ids), parent)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(400)
    if own_command:
        layer.beginEditCommand(title)
    changed = 0
    try:
        for n, feat in enumerate(layer.getFeatures(req)):
            value = _evaluate(exp, ctx, feat, n + 1)      # @row_number from 1
            if exp.hasEvalError():
                raise OperationError(f"Feature {feat.id()}: {exp.evalErrorString()}")
            value = coerce(field, value)
            old = feat.attribute(idx)
            if not (F.is_null(old) and value is None) and old != value:
                if not layer.changeAttributeValue(feat.id(), idx, value, old):
                    raise OperationError(f"Feature {feat.id()}: the value was not accepted")
                changed += 1
            if progress is not None and n % 200 == 0:
                progress.setValue(n)
                QApplication.processEvents()
                if progress.wasCanceled():
                    if own_command:
                        layer.destroyEditCommand()
                    return None
    except OperationError:
        if own_command:
            layer.destroyEditCommand()
        raise
    finally:
        if progress is not None:
            progress.close()
    if own_command:
        layer.endEditCommand()
    return changed


def add_feature_in_table(layer):
    """A new record with the layer's defaults and no geometry.  Returns its
    (temporary) feature id, or None."""
    feat = QgsVectorLayerUtils.createFeature(
        layer, QgsGeometry(), {}, layer.createExpressionContext())
    layer.beginEditCommand("Attribute Explorer: add record")
    if layer.addFeature(feat):
        layer.endEditCommand()
        return feat.id()
    layer.destroyEditCommand()
    return None


def delete_impact(layer, fids):
    """(handled layers text, extra features) for a cascade delete, from
    QGIS relations (composition)."""
    try:
        cascades, context = QgsVectorLayerUtils.impactsCascadeFeatures(
            layer, set(fids), QgsProject.instance())
    except Exception:
        return "", 0
    if not cascades:
        return "", 0
    names, extra = [], 0
    for lyr in context.layers():
        if lyr is layer:
            continue
        n = len(context.duplicatedFeatures(lyr))
        if n:
            names.append(f"{lyr.name()} ({n:,})")
            extra += n
    return ", ".join(names), extra


def delete_features(layer, fids):
    """Delete *fids* (with QGIS relation cascades) as one undo command."""
    layer.beginEditCommand(f"Attribute Explorer: delete {len(fids):,} feature(s)")
    try:
        ok = layer.deleteFeatures(list(fids), QgsVectorLayer.DeleteContext(
            True, QgsProject.instance()))
    except TypeError:
        ok = layer.deleteFeatures(list(fids))
    if ok:
        layer.endEditCommand()
    else:
        layer.destroyEditCommand()
    return ok


def edited_fids(layer):
    """Unsaved changed or added features of the layer's edit buffer."""
    buf = layer.editBuffer() if layer is not None and layer.isEditable() else None
    if buf is None:
        return set()
    out = set(buf.changedAttributeValues().keys())
    out |= set(buf.changedGeometries().keys())
    out |= set(buf.addedFeatures().keys())
    return out


def field_dependents(layer, name):
    """Names of virtual fields, defaults and constraints that refer to the
    field *name* (checked before it is deleted)."""
    out = []
    fields = layer.fields()
    for i in range(fields.count()):
        fld = fields.at(i)
        if fld.name() == name:
            continue
        exprs = []
        if fields.fieldOrigin(i) == Qgis.FieldOrigin.Expression:
            exprs.append(("virtual field", layer.expressionField(i)))
        default = fld.defaultValueDefinition().expression()
        if default:
            exprs.append(("default value", default))
        cexp = fld.constraints().constraintExpression()
        if cexp:
            exprs.append(("constraint", cexp))
        for what, text in exprs:
            if name in QgsExpression(text).referencedColumns():
                out.append(f"{fld.name()} ({what})")
    return out
