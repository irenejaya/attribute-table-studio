# -*- coding: utf-8 -*-
"""
Icons for the Attribute Table Studio tools.

Native QGIS theme icons are used wherever one exists (all names below are
present in QGIS 3.40+ and 4.x).  Glyph-only native icons that are drawn in
dark grey (field types) are tinted to the text colour in dark mode so they
stay readable.  A few small glyphs are painted (the white "run" arrow on
primary buttons, the dropdown chevron) so they follow the theme colours.
"""

import os

from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt
from qgis.PyQt.QtGui import (
    QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from qgis.core import QgsApplication

from ..core import fields as F

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons")

# tool icons (custom SVGs; Compare keeps its existing icon)
EXPLORER = "attribute_explorer"
PIVOT = "pivot_table"
COMPARE = "geometry_compare"

# native QGIS icon names, by purpose
COLUMNS = "/mActionFilterTableFields.svg"
ZOOM_SELECTED = "/mActionZoomToSelected.svg"
ZOOM_TO = "/mActionZoomTo.svg"
SELECTED_ONLY = "/mActionOpenTableSelected.svg"
SUMMARY = "/histogram.svg"
EXPORT = "/mActionSharingExport.svg"
FILTER = "/mActionFilter2.svg"
DOCK = "/mDockify.svg"
CANCEL = "/mTaskCancel.svg"
OPEN = "/mActionFileOpen.svg"
SAVE = "/mActionFileSave.svg"
REFRESH = "/mActionRefresh.svg"
ADD = "/symbologyAdd.svg"
REMOVE = "/symbologyRemove.svg"
COPY = "/mActionEditCopy.svg"
SELECT_ALL = "/mActionSelectAll.svg"
INVERT = "/mActionInvertSelection.svg"
DESELECT = "/mActionDeselectAll.svg"                  # all layers
DESELECT_LAYER = "/mActionDeselectActiveLayer.svg"    # the current layer
SETTINGS = "/propertyicons/settings.svg"
HELP = "/mActionHelpContents.svg"
SUCCESS = "/mIconSuccess.svg"
DELETE = "/mIconDelete.svg"
UNDO = "/mActionUndo.svg"
SWAP = "/sync_views.svg"
TABLE = "/mActionOpenTable.svg"
CSV = "/mActionOpenTable.svg"
XLSX = "/mActionSharingExport.svg"
GPKG = "/mActionNewGeoPackageLayer.svg"
WARNING = "/mIconWarning.svg"
CLEAR = "/console/iconClearConsole.svg"
# editing (Attribute Explorer edit row and menus)
TOGGLE_EDIT = "/mActionToggleEditing.svg"
SAVE_EDITS = "/mActionSaveEdits.svg"
ROLLBACK = "/mActionRollbackEdits.svg"
REDO = "/mActionRedo.svg"
NEW_ROW = "/mActionNewTableRow.svg"
DELETE_SELECTED = "/mActionDeleteSelectedFeatures.svg"
CUT = "/mActionEditCut.svg"
PASTE = "/mActionEditPaste.svg"
CALCULATE = "/mActionCalculateField.svg"
QUICK_UPDATE = "/mActionProcessSelected.svg"
MULTI_EDIT = "/mActionMultiEdit.svg"
NEW_FIELD = "/mActionNewAttribute.svg"
DELETE_FIELD = "/mActionDeleteAttribute.svg"
RELOAD = "/mActionReload.svg"
FORM_VIEW = "/mActionFormView.svg"
EXPRESSION = "/mIconExpression.svg"
EXPR_SELECT = "/mIconExpressionSelect.svg"
FORM_SELECT = "/mIconFormSelect.svg"
CONDITIONAL = "/mActionConditionalFormatting.svg"
SELECTED_TOP = "/mActionSelectedToTop.svg"
SORT = "/sort.svg"
SORT_REVERSE = "/sort-reverse.svg"
PAN_SELECTED = "/mActionPanToSelected.svg"
FLASH = "/mActionHighlightFeature.svg"
ACTION = "/mAction.svg"
FIELD_PROPS = "/propertyicons/attributes.svg"
STORED = "/mIconStoredQueries.svg"
HISTORY = "/mActionHistory.svg"
QUERY = "/mIndicatorFilter.svg"      # the Layers panel's "filtered" mark
GEOM_DIFF = "/algorithms/mAlgorithmSymmetricalDifference.svg"

FIELD_ICONS = {
    F.TEXT: "/mIconFieldText.svg",
    F.INTEGER: "/mIconFieldInteger.svg",
    F.REAL: "/mIconFieldFloat.svg",
    F.DATE: "/mIconFieldDate.svg",
    F.DATETIME: "/mIconFieldDateTime.svg",
    F.TIME: "/mIconFieldTime.svg",
    F.BOOL: "/mIconFieldBool.svg",
    F.OTHER: "/mIconFieldJson.svg",
}

_SIZES = (16, 20, 24, 32, 48)
_cache = {}


def tool(name):
    """One of the three tool icons."""
    key = ("tool", name)
    if key not in _cache:
        _cache[key] = QIcon(os.path.join(ICON_DIR, name + ".svg"))
    return _cache[key]


def native(name, fallback=None):
    """Built-in QGIS theme icon (or *fallback* when the theme lacks it)."""
    icon = QgsApplication.getThemeIcon(name)
    if icon.isNull() and fallback is not None:
        return fallback
    return icon


def tinted(icon, color):
    """*icon* recoloured to a single *color* (alpha preserved)."""
    out = QIcon()
    col = QColor(color)
    for size in _SIZES:
        src = icon.pixmap(QSize(size, size))
        if src.isNull():
            continue
        pm = QPixmap(src.size())
        pm.setDevicePixelRatio(src.devicePixelRatio())
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.drawPixmap(0, 0, src)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(pm.rect(), col)
        p.end()
        out.addPixmap(pm)
    return out


def lightened(icon, amount=0.55):
    """*icon* blended toward white (alpha and hues kept), so a dark native
    icon stays readable on a dark theme."""
    out = QIcon()
    veil = QColor(255, 255, 255, int(255 * amount))
    for size in _SIZES:
        src = icon.pixmap(QSize(size, size))
        if src.isNull():
            continue
        pm = QPixmap(src.size())
        pm.setDevicePixelRatio(src.devicePixelRatio())
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.drawPixmap(0, 0, src)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.fillRect(pm.rect(), veil)
        p.end()
        out.addPixmap(pm)
    return out


def native_for(name, theme):
    """Native icon, lightened on a dark theme (for dark artwork such as the
    expression filter icon)."""
    key = ("native_for", name, theme.dark)
    if key not in _cache:
        icon = native(name)
        _cache[key] = lightened(icon) if theme.dark else icon
    return _cache[key]


def field(kind, theme):
    """Native field-type icon; tinted for contrast in dark mode."""
    key = ("field", kind, theme.dark)
    if key not in _cache:
        icon = native(FIELD_ICONS.get(kind, FIELD_ICONS[F.TEXT]))
        _cache[key] = tinted(icon, theme.text) if theme.dark else icon
    return _cache[key]


def field_for(qgs_field, theme):
    return field(F.field_kind(qgs_field), theme)


# ----------------------------------------------------------------------------
# Painted glyphs (crisp at every scale)
# ----------------------------------------------------------------------------

def _paint(kind, color, size):
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(scale)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(color)
    pen = QPen(col)
    pen.setWidthF(max(1.5, size / 10.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    s = float(size)

    def pt(x, y):
        return QPointF(x * s, y * s)

    if kind == "run":
        path = QPainterPath()
        path.moveTo(pt(.30, .20))
        path.lineTo(pt(.80, .50))
        path.lineTo(pt(.30, .80))
        path.closeSubpath()
        p.setBrush(col)
        p.drawPath(path)
    elif kind == "chevron":
        path = QPainterPath()
        path.moveTo(pt(.25, .40))
        path.lineTo(pt(.50, .64))
        path.lineTo(pt(.75, .40))
        p.drawPath(path)
    elif kind == "cross":
        p.drawLine(pt(.30, .30), pt(.70, .70))
        p.drawLine(pt(.70, .30), pt(.30, .70))
    elif kind == "calendar":
        p.drawRoundedRect(QRectF(.18 * s, .24 * s, .64 * s, .58 * s), 1.5, 1.5)
        p.drawLine(pt(.18, .42), pt(.82, .42))
        p.drawLine(pt(.36, .16), pt(.36, .30))
        p.drawLine(pt(.64, .16), pt(.64, .30))
    elif kind == "menu":                 # "More": three bars on whole pixels
        bar, gap, edge = max(2, round(s / 8)), round(s * 3 / 16), size // 8
        top = (size - 3 * bar - 2 * gap) // 2
        for i in range(3):
            p.fillRect(QRectF(edge, top + i * (bar + gap), size - 2 * edge, bar), col)
    p.end()
    return pm


def action_icon(kind, color, size=18):
    """Compare's action button icons, drawn exactly as in v1.1:
    'validate' (circled tick) and 'play' (filled triangle)."""
    key = ("action", kind, QColor(color).name(), size)
    if key in _cache:
        return _cache[key]
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(scale)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(color)
    pen = QPen(col)
    pen.setWidthF(1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = float(size)

    def pt(x, y):
        return QPointF(x * s, y * s)

    path = QPainterPath()
    if kind == "play":
        path.moveTo(pt(.30, .18))
        path.lineTo(pt(.82, .50))
        path.lineTo(pt(.30, .82))
        path.closeSubpath()
        p.setBrush(col)
    elif kind == "validate":
        p.drawEllipse(QRectF(.1 * s, .1 * s, .8 * s, .8 * s))
        path.moveTo(pt(.3, .52))
        path.lineTo(pt(.45, .67))
        path.lineTo(pt(.7, .35))
    p.drawPath(path)
    p.end()
    _cache[key] = QIcon(pm)
    return _cache[key]


def text_glyph(text, color):
    """A short symbol (=, ≠, ≈, ∈ ...) drawn as an icon, so operator menus
    show each symbol once, aligned in the icon column."""
    key = ("text", text, QColor(color).name())
    if key in _cache:
        return _cache[key]
    icon = QIcon()
    for size in (16, 24, 32):
        scale = 2
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(scale)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(size * (0.80 if len(text) == 1 else 0.58)))
        p.setFont(font)
        p.setPen(QColor(color))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, text)
        p.end()
        icon.addPixmap(pm)
    _cache[key] = icon
    return icon


def glyph(kind, color):
    key = ("glyph", kind, QColor(color).name())
    if key not in _cache:
        icon = QIcon()
        for size in (16, 24, 32):
            icon.addPixmap(_paint(kind, color, size))
        _cache[key] = icon
    return _cache[key]


def with_badge(base_name, badge, theme):
    """Native icon with a small painted badge in the lower right corner
    (used for 'Clear filters': funnel + cross)."""
    key = ("badge", base_name, badge, theme.dark)
    if key in _cache:
        return _cache[key]
    base = native(base_name)
    out = QIcon()
    for size in (16, 24, 32):
        src = base.pixmap(QSize(size, size))
        pm = QPixmap(src.size())
        pm.setDevicePixelRatio(src.devicePixelRatio())
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, src)
        w = pm.width() / pm.devicePixelRatio()
        r = w * 0.27
        c = QPointF(w - r - w * 0.02, w - r - w * 0.02)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.danger))
        p.drawEllipse(c, r, r)
        pen = QPen(QColor("white"))
        pen.setWidthF(max(1.2, w / 12.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        d = r * 0.45
        p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))
        p.end()
        out.addPixmap(pm)
    _cache[key] = out
    return out


def clear_cache():
    _cache.clear()
