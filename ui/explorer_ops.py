# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Attribute Explorer layer operations shared by the edit row, the Quick update
row, the Field calculator and the menus.

Every mutation goes through the layer's edit buffer and is grouped as one
undo command; a cancelled batch destroys its incomplete command, so a
partial result is never left behind or reported as complete.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QApplication, QProgressDialog
from qgis.core import (
    Qgis, QgsDistanceArea, QgsExpression, QgsFeatureRequest, QgsField,
    QgsGeometry, QgsProject, QgsVectorLayer, QgsVectorLayerUtils,
)

from ..core import fields as F
from ..core.compat import NO_GEOMETRY

BATCH_PROGRESS = 2000            # show a cancellable progress dialog above this


class OperationError(Exception):
    """A user-facing problem (bad expression, value that cannot be stored)."""


def convert_like_qgis(field, value):
    """The value QGIS's field calculators write for *field*:
    QgsField.convertCompatible's result, which QGIS writes even when the
    conversion fails (NULL, or text cut to the field length).  Decimals
    round half away from zero to the field precision, integers round the
    same way.  Returns (value, problem or None)."""
    if F.is_null(value):
        return None, None
    try:
        return field.convertCompatible(value), None
    except ValueError as exc:
        if F.field_kind(field) == F.TEXT and field.length() > 0:
            try:            # the text Qt makes of the value, cut like QGIS cuts it
                text = QgsField(field.name(), field.type(), field.typeName()
                                ).convertCompatible(value)
            except ValueError:
                text = None
            if isinstance(text, str) and len(text) > field.length():
                return text[:field.length()], str(exc)
        return None, str(exc)


def calculator_fields(layer):
    """Names QGIS's Field Calculator offers under "Update existing field":
    stored fields, plus joined fields from editable joins that are not
    hidden; never virtual fields."""
    fields = layer.fields()
    names = []
    for idx in range(fields.count()):
        origin = fields.fieldOrigin(idx)
        if origin in (Qgis.FieldOrigin.Provider, Qgis.FieldOrigin.Edit):
            names.append(fields.at(idx).name())
        elif origin == Qgis.FieldOrigin.Join:
            if fields.at(idx).editorWidgetSetup().type() == "Hidden":
                continue
            info = layer.joinBuffer().joinForFieldIndex(idx, fields)
            info = info[0] if isinstance(info, tuple) else info
            if info is not None and info.isEditable():
                names.append(fields.at(idx).name())
    return names


# ----------------------------------------------------------------------------
# the QGIS field calculation (attribute table "Update All / Filtered /
# Selected", src/app/qgsattributetabledialog.cpp runFieldCalculation)
# ----------------------------------------------------------------------------

def geometry_calculator(layer):
    """Measures like the QGIS field calculator: on the project's ellipsoid
    (planar when the project has none, e.g. without a project CRS)."""
    project = QgsProject.instance()
    da = QgsDistanceArea()
    da.setSourceCrs(layer.crs(), project.transformContext())
    da.setEllipsoid(project.ellipsoid())
    return da


def calculator_expression(layer, expression):
    """(expression, context) prepared the way QGIS prepares them: $area /
    $length / $perimeter use the project's ellipsoid and distance / area
    units.  Raises OperationError for a bad expression."""
    exp = QgsExpression(expression)
    if not expression.strip() or exp.hasParserError():
        raise OperationError(exp.parserErrorString() or "Enter an expression")
    project = QgsProject.instance()
    exp.setGeomCalculator(geometry_calculator(layer))
    exp.setDistanceUnits(project.distanceUnits())
    exp.setAreaUnits(project.areaUnits())
    ctx = layer.createExpressionContext()
    # prepared before @row_number exists, so it is not folded into a constant
    if not exp.prepare(ctx):
        raise OperationError(exp.evalErrorString())
    return exp, ctx


def _calculation_request(layer, exp, field):
    """Every feature the layer provides (its Query Builder filter applies),
    in the layer's own order, with only the attributes needed."""
    req = QgsFeatureRequest()
    if not exp.needsGeometry():
        req.setFlags(NO_GEOMETRY)
    columns = set(exp.referencedColumns()) | {field.name()}
    req.setSubsetOfAttributes(list(columns), layer.fields())
    return req


def _targets(layer, req, fids):
    """The layer's features, skipping those not in *fids* (None = all)."""
    for feat in layer.getFeatures(req):
        if fids is None or feat.id() in fids:
            yield feat


def evaluate_preview(layer, idx, expression, fids=None, limit=6):
    """[(fid, old, new)] for the first *limit* target features, as the
    calculation would write them (nothing is written).  *fids*: None for
    every feature.  Raises OperationError."""
    exp, ctx = calculator_expression(layer, expression)
    field = layer.fields().at(idx)
    fids = None if fids is None else set(fids)
    out = []
    for n, feat in enumerate(_targets(layer, _calculation_request(layer, exp, field), fids), 1):
        if n > limit:
            break
        ctx.setFeature(feat)
        ctx.lastScope().setVariable("row_number", n)
        value = exp.evaluate(ctx)
        if exp.hasEvalError():
            raise OperationError(exp.evalErrorString())
        out.append((feat.id(), feat.attribute(idx), convert_like_qgis(field, value)[0]))
    return out


def run_field_calculation(layer, idx, expression, fids, title, parent=None):
    """QGIS's field calculation as one undo command: *fids* None updates
    every feature the layer provides, otherwise only those ids.  Like QGIS,
    @row_number counts the calculated features from 1, every target is
    written (also unchanged values) and an evaluation error writes nothing.
    Returns (calculated, problems) - problems are values QGIS writes as NULL
    or cuts to the field length; None when cancelled.  Raises
    OperationError."""
    exp, ctx = calculator_expression(layer, expression)
    if not layer.isEditable():
        raise OperationError("Start editing first")
    field = layer.fields().at(idx)
    fids = None if fids is None else set(fids)
    count = len(fids) if fids is not None else max(layer.featureCount(), 0)
    progress = None
    if count > BATCH_PROGRESS and parent is not None:
        progress = QProgressDialog(f"{title}…", "Cancel", 0, count, parent)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(400)
    layer.beginEditCommand(title)
    rownum, problems = 1, 0
    try:
        for feat in _targets(layer, _calculation_request(layer, exp, field), fids):
            ctx.setFeature(feat)
            ctx.lastScope().setVariable("row_number", rownum)
            value = exp.evaluate(ctx)
            if exp.hasEvalError():
                raise OperationError(exp.evalErrorString())
            value, problem = convert_like_qgis(field, value)
            problems += problem is not None
            layer.changeAttributeValue(feat.id(), idx, value, feat.attribute(idx))
            if progress is not None and rownum % 200 == 0:
                progress.setValue(rownum)
                QApplication.processEvents()
                if progress.wasCanceled():
                    layer.destroyEditCommand()
                    return None
            rownum += 1
    except OperationError:
        layer.destroyEditCommand()
        raise
    finally:
        if progress is not None:
            progress.close()
    layer.endEditCommand()
    return rownum - 1, problems


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
