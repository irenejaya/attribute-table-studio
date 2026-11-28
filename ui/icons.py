# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Icons for the Attribute Table Studio tools.

Native QGIS theme icons are used wherever one exists (all names below are
present in QGIS 3.40+ and 4.x).  Glyph-only native icons that are drawn in
dark grey (field types) are tinted to the text colour in dark mode so they
stay readable.  A few small glyphs are painted (the white "run" arrow on
primary buttons, the dropdown chevron) so they follow the theme colours.
"""

import os

import unicodedata

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
PLOT = "data_plot"

# native QGIS icon names, by purpose
COLUMNS = "/mActionFilterTableFields.svg"
ZOOM_SELECTED = "/mActionZoomToSelected.svg"
ZOOM_TO = "/mActionZoomTo.svg"
SELECTED_ONLY = "/mActionOpenTableSelected.svg"
SUMMARY = "/mActionSum.svg"                 # column statistics strip (Σ)
CHART = "/histogram.svg"                # Visualize Statistics
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
# the QGIS attribute table's filter menu
TABLE_VISIBLE = "/mActionOpenTableVisible.svg"
TABLE_INVALID = "/mActionOpenTableInvalid.svg"
TABLE_EDITED = "/mActionOpenTableEdited.svg"
ADVANCED_FILTER = "/mActionFilter.svg"
STORED_FILTER = "/mActionHandleStoreFilterExpressionChecked.svg"
STORED_FILTER_OFF = "/mActionHandleStoreFilterExpressionUnchecked.svg"
HISTORY = "/mActionHistory.svg"
QUERY = "/mIndicatorFilter.svg"      # the Layers panel's "filtered" mark
SEARCH = "/search.svg"               # Find and Replace (Ctrl+F)
SEARCH_CASE = "/mIconSearchCaseSensitive.svg"
SEARCH_WHOLE = "/mIconSearchWholeWord.svg"
SEARCH_REGEX = "/mIconSearchRegex.svg"
GEOM_DIFF = "/algorithms/mAlgorithmSymmetricalDifference.svg"
JOIN = "/propertyicons/join.svg"     # Layer Properties > Joins

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
    """One of the tool icons."""
    key = ("tool", name)
    if key not in _cache:
        _cache[key] = QIcon(os.path.join(ICON_DIR, name + ".svg"))
    return _cache[key]


def plot_type(key):
    """Icon of a Data Plot type (icons/plots/<key>.svg), or the tool icon."""
    k = ("plot_type", key)
    if k not in _cache:
        path = os.path.join(ICON_DIR, "plots", key + ".svg")
        _cache[k] = QIcon(path) if os.path.exists(path) else tool(PLOT)
    return _cache[k]


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
    elif kind == "chevron_right":
        path = QPainterPath()
        path.moveTo(pt(.40, .25))
        path.lineTo(pt(.64, .50))
        path.lineTo(pt(.40, .75))
        p.drawPath(path)
    elif kind in ("arrow_up", "arrow_down"):
        up = kind == "arrow_up"
        tip, tail = (.22, .80) if up else (.78, .20)
        wing = .44 if up else .56
        p.drawLine(pt(.50, tip), pt(.50, tail))
        path = QPainterPath()
        path.moveTo(pt(.26, wing))
        path.lineTo(pt(.50, tip))
        path.lineTo(pt(.74, wing))
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


def sort_icon(kind, theme, horizontal=False):
    """Chart sort icons, drawn the way the chart runs.  *horizontal* (a
    bar chart: values down the left, records along the bottom): letters
    stacked with a down arrow, bars getting longer / shorter down the
    icon, an up-down arrow for a custom order.  Columns (a histogram:
    values left to right, records up): letters side by side with a right
    arrow, columns rising / falling, a left-right arrow."""
    key = ("sort", kind, theme.dark, bool(horizontal))
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
        s = float(size)
        accent = QColor(theme.accent_text)
        if kind in ("az", "za"):
            font = QFont()
            font.setBold(True)
            font.setPixelSize(max(6, int(s * 0.44)))
            p.setFont(font)
            p.setPen(QColor(theme.text))
            first, second = ("A", "Z") if kind == "az" else ("Z", "A")
            pen = QPen(accent, max(1.4, s / 11.0))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            if horizontal:                   # values top to bottom
                p.drawText(QRectF(0, 0, s * 0.6, s * 0.5), Qt.AlignmentFlag.AlignCenter,
                           first)
                p.drawText(QRectF(0, s * 0.5, s * 0.6, s * 0.5),
                           Qt.AlignmentFlag.AlignCenter, second)
                p.setPen(pen)
                x = s * 0.78
                p.drawLine(QPointF(x, s * 0.14), QPointF(x, s * 0.84))
                p.drawLine(QPointF(x - s * 0.13, s * 0.66), QPointF(x, s * 0.84))
                p.drawLine(QPointF(x + s * 0.13, s * 0.66), QPointF(x, s * 0.84))
            else:                            # values left to right
                p.drawText(QRectF(0, 0, s * 0.5, s * 0.62), Qt.AlignmentFlag.AlignCenter,
                           first)
                p.drawText(QRectF(s * 0.5, 0, s * 0.5, s * 0.62),
                           Qt.AlignmentFlag.AlignCenter, second)
                p.setPen(pen)
                y = s * 0.80
                p.drawLine(QPointF(s * 0.14, y), QPointF(s * 0.86, y))
                p.drawLine(QPointF(s * 0.68, y - s * 0.13), QPointF(s * 0.86, y))
                p.drawLine(QPointF(s * 0.68, y + s * 0.13), QPointF(s * 0.86, y))
        elif kind in ("fewest", "count"):
            sizes = [0.30, 0.50, 0.70, 0.90]
            if kind == "count":
                sizes.reverse()
            bar = QColor(theme.bar)
            w = s * 0.17
            for i, h in enumerate(sizes):
                if horizontal:               # bars down the icon, from the left
                    p.fillRect(QRectF(s * 0.05, s * 0.10 + i * (w + s * 0.05), s * h, w), bar)
                else:                        # columns along the icon, from the bottom
                    p.fillRect(QRectF(s * 0.10 + i * (w + s * 0.05), s * (0.95 - h), w, s * h),
                               bar)
        else:                                # custom: a two-way arrow along the values
            pen = QPen(accent, max(1.5, s / 10.0))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            if horizontal:
                p.translate(s / 2, s / 2)
                p.rotate(90)
                p.translate(-s / 2, -s / 2)
            y = s * 0.5
            p.drawLine(QPointF(s * 0.14, y), QPointF(s * 0.86, y))
            for tip, back in ((0.14, 0.32), (0.86, 0.68)):
                p.drawLine(QPointF(s * tip, y), QPointF(s * back, y - s * 0.17))
                p.drawLine(QPointF(s * tip, y), QPointF(s * back, y + s * 0.17))
        p.end()
        icon.addPixmap(pm)
    _cache[key] = icon
    return icon


def option_icon(kind, theme):
    """Small pictures for the choices of a box, drawn the way the choice
    shapes the plot: trend lines ("linear", "exponential", "logarithmic",
    "power", "polynomial"), transformations ("none", "log", "sqrt",
    "inverse", "boxcox") and bars ("side", "stacked", "stacked100")."""
    key = ("option", kind, theme.dark)
    if key in _cache:
        return _cache[key]
    import math
    icon = QIcon()
    for size in (16, 24, 32):
        scale = 2
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(scale)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = float(size)
        if kind in ("side", "stacked", "stacked100"):
            blue, orange = QColor("#2a78d6"), QColor("#eb6834")
            if theme.dark:
                blue, orange = QColor("#3987e5"), QColor("#d95926")
            w = s * 0.26
            if kind == "side":
                for x, h, c in ((0.12, 0.62, blue), (0.38, 0.42, orange),
                                (0.64, 0.80, blue)):
                    p.fillRect(QRectF(s * x, s * (0.92 - h), w * 0.92, s * h), c)
            else:
                full = kind == "stacked100"
                for x, lo, hi in ((0.14, 0.34, 0.30), (0.56, 0.48, 0.38)):
                    if full:
                        lo, hi = lo / (lo + hi) * 0.84, hi / (lo + hi) * 0.84
                    p.fillRect(QRectF(s * x, s * (0.92 - lo), w * 1.15, s * lo), blue)
                    p.fillRect(QRectF(s * x, s * (0.92 - lo - hi), w * 1.15, s * hi), orange)
        else:
            dim = QColor(theme.text_dim)
            dim.setAlpha(160)
            pen = QPen(dim, max(1.0, s / 16.0))
            p.setPen(pen)
            p.drawLine(QPointF(s * 0.12, s * 0.10), QPointF(s * 0.12, s * 0.88))
            p.drawLine(QPointF(s * 0.12, s * 0.88), QPointF(s * 0.92, s * 0.88))
            curve = {
                "linear": lambda t: t,
                "none": lambda t: t,
                "exponential": lambda t: (math.exp(3 * t) - 1) / (math.exp(3) - 1),
                "power": lambda t: t ** 1.8,
                "logarithmic": lambda t: math.log(1 + 9 * t) / math.log(10),
                "log": lambda t: math.log(1 + 9 * t) / math.log(10),
                "sqrt": lambda t: math.sqrt(t),
                "polynomial": lambda t: 1 - 3.6 * (t - 0.5) ** 2 * 1.1,
                "inverse": lambda t: min(1.0, 0.12 / (t + 0.08)),
                "boxcox": lambda t: 0.5 + 0.5 * math.tanh(4 * (t - 0.5)),
            }.get(kind, lambda t: t)
            path = QPainterPath()
            for i in range(25):
                t = i / 24.0
                x = s * (0.18 + 0.72 * t)
                y = s * (0.82 - 0.66 * max(0.0, min(1.0, curve(t))))
                if i:
                    path.lineTo(x, y)
                else:
                    path.moveTo(x, y)
            pen = QPen(QColor(theme.accent_text), max(1.5, s / 10.0))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if kind == "none":
                pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.end()
        icon.addPixmap(pm)
    _cache[key] = icon
    return icon


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
        # a mark such as the bar of x̄ does not make the symbol wider
        wide = sum(1 for ch in text if not unicodedata.combining(ch)) > 1
        font.setPixelSize(int(size * (0.58 if wide else 0.80)))
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
