# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Attribute Explorer table: model, delegate, row header and view.

- :class:`FeatureTableModel` shows one row per feature id in scope.  Only
  rows that are painted are read from the layer (pages of 256 by id), so a
  million-feature layer opens instantly and uses little memory.
- Feature selection is the QGIS selection: rows are painted selected from
  the layer's selected id set (O(1) per row), and clicks are turned into
  ``selectByIds`` calls by the panel.  No QItemSelection is kept, so huge
  map selections never slow the table down.
- Values use ``QgsField.displayString`` (same formatting as the native
  attribute table) with the reference colouring: text orange, numbers
  green and right aligned, NULL grey italic.  Layer conditional styles
  (View > Conditional formatting) paint over the type colours.
- While the layer is being edited, cells of editable fields open the
  field's configured QGIS editor widget (F2, typing or double click).  A
  value is validated against the field's constraints, written to the
  layer's edit buffer as one undo command, and the cell gets an amber
  corner until it is saved.
- A single click only frames the cell, browsing or editing (Ctrl+C copies
  its value); features are selected with the row tick boxes / numbers.
"""

from collections import OrderedDict

from qgis.PyQt.QtCore import (
    QAbstractTableModel, QEvent, QModelIndex, QPoint, QPointF, QRect, QRectF,
    QSize, Qt, pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QPolygonF
from qgis.PyQt import sip
from qgis.PyQt.QtWidgets import (
    QAbstractButton, QAbstractItemView, QHeaderView, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTableView, QToolTip,
)
from qgis.core import (
    QgsApplication, QgsConditionalStyle, QgsExpressionContext,
    QgsExpressionContextUtils, QgsFeature, QgsFeatureRequest,
    QgsFieldConstraints, QgsVectorLayerUtils,
)
from qgis.gui import QgsGui

from ..core import fields as F
from ..core.compat import NO_GEOMETRY, enum_int

_ALIGN_LEFT = enum_int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_RIGHT = enum_int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


COPY_ROWS_KEY = QKeySequence("Ctrl+Shift+C")   # Ctrl+C copies the framed cell
_HARD = QgsFieldConstraints.ConstraintStrength.ConstraintStrengthHard
_SOFT = QgsFieldConstraints.ConstraintStrength.ConstraintStrengthSoft


class FeatureTableModel(QAbstractTableModel):
    FidRole = Qt.ItemDataRole.UserRole + 1
    RawRole = Qt.ItemDataRole.UserRole + 2
    PAGE = 256
    CACHE_LIMIT = 30000

    editRejected = pyqtSignal(str)        # hard constraint / write failure
    editWarned = pyqtSignal(str)          # soft constraint

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.layer = None
        self.fields = []
        self.kinds = []
        self.fids = []
        self._rows = None                   # fid -> row, built when needed
        self.selected = set()
        self._cache = OrderedDict()
        self._null_text = QgsApplication.nullRepresentation() or F.NULL_TEXT
        self._colours = {}
        self._null_colour = QColor(theme.data_null)
        self._italic = QFont()
        self._italic.setItalic(True)
        # editing
        self.editing = False
        self.editable_cols = set()
        self.dirty = {}                    # fid -> set of changed columns
        self.added = set()                 # unsaved new features
        # layer conditional styles (row rules + per field rules)
        self._row_styles = []
        self._field_styles = {}
        self._style_cache = {}
        self._style_context = None

    # ------------------------------------------------------------------
    def set_layer(self, layer):
        self.beginResetModel()
        self.layer = layer
        flds = layer.fields() if layer is not None else None
        self.fields = [flds.at(i) for i in range(flds.count())] if flds else []
        self.kinds = [F.field_kind(f) for f in self.fields]
        t = self.theme
        self._colours = {}
        for col, kind in enumerate(self.kinds):
            if kind in F.NUMERIC:
                c = t.data_number
            elif kind in F.TEMPORAL:
                c = t.data_temporal
            elif kind == F.BOOL:
                c = t.data_bool
            else:
                c = t.data_text
            self._colours[col] = QColor(c)
        self.fids = []
        self._rows = None
        self._cache.clear()
        self.editable_cols = set()
        self.dirty, self.added = {}, set()
        self.load_styles()
        self.endResetModel()

    def set_fids(self, fids):
        self.beginResetModel()
        self.fids = fids
        self._rows = None
        self.endResetModel()

    def row_of(self, fid):
        """Row of a feature in the current order, or None when not shown."""
        if self._rows is None:
            self._rows = {f: i for i, f in enumerate(self.fids)}
        return self._rows.get(fid)

    def invalidate(self, fids=None):
        """Forget cached attributes (all, or of *fids*) and repaint."""
        if fids is None:
            self._cache.clear()
            self._style_cache.clear()
        else:
            for fid in fids:
                self._cache.pop(fid, None)
                for key in [k for k in self._style_cache if k[0] == fid]:
                    del self._style_cache[key]
        if self.fids and self.fields:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self.fids) - 1, len(self.fields) - 1))

    def fid(self, row):
        return self.fids[row] if 0 <= row < len(self.fids) else None

    def attributes(self, row):
        fid = self.fids[row]
        attrs = self._cache.get(fid)
        if attrs is None:
            self._load_around(row)
            attrs = self._cache.get(fid)
        return attrs

    def raw_value(self, row, col):
        attrs = self.attributes(row)
        return attrs[col] if attrs is not None and col < len(attrs) else None

    def display_text(self, row, col):
        value = self.raw_value(row, col)
        if F.is_null(value):
            return self._null_text
        try:
            return self.fields[col].displayString(value)
        except Exception:
            return F.display_value(value)

    def _load_around(self, row):
        if self.layer is None:
            return
        start = max(0, row - self.PAGE // 4)
        chunk = [f for f in self.fids[start:start + self.PAGE]
                 if f not in self._cache]
        if not chunk:
            return
        req = QgsFeatureRequest().setFilterFids(chunk)
        req.setFlags(NO_GEOMETRY)
        got = set()
        for feat in self.layer.getFeatures(req):
            fid = feat.id()
            self._cache[fid] = feat.attributes()
            got.add(fid)
        blank = [None] * len(self.fields)
        for fid in chunk:
            if fid not in got:           # deleted since the scan
                self._cache[fid] = blank
        while len(self._cache) > self.CACHE_LIMIT:
            self._cache.popitem(last=False)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # conditional styles
    # ------------------------------------------------------------------
    def load_styles(self):
        """Read the layer's conditional formatting rules."""
        self._style_cache.clear()
        self._row_styles, self._field_styles = [], {}
        layer = self.layer
        if layer is None:
            return
        styles = layer.conditionalStyles()
        self._row_styles = [s for s in styles.rowStyles() if s.isValid()]
        for fld in self.fields:
            rules = [s for s in styles.fieldStyles(fld.name()) if s.isValid()]
            if rules:
                self._field_styles[fld.name()] = rules
        self._style_context = QgsExpressionContext(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer))

    def has_styles(self):
        return bool(self._row_styles or self._field_styles)

    def cell_style(self, row, col):
        """Merged QgsConditionalStyle for a cell, or None."""
        if not self.has_styles() or not 0 <= row < len(self.fids):
            return None
        fid = self.fids[row]
        key = (fid, col)
        if key in self._style_cache:
            return self._style_cache[key]
        attrs = self.attributes(row)
        style = None
        if attrs is not None:
            feat = QgsFeature(self.layer.fields(), fid)
            feat.setAttributes(list(attrs))
            ctx = self._style_context
            ctx.setFeature(feat)
            found = []
            if self._row_styles:
                found += QgsConditionalStyle.matchingConditionalStyles(
                    self._row_styles, None, ctx)
            rules = self._field_styles.get(self.fields[col].name())
            if rules:
                found += QgsConditionalStyle.matchingConditionalStyles(
                    rules, attrs[col], ctx)
            if found:
                style = QgsConditionalStyle.compressStyles(found)
        self._style_cache[key] = style
        if len(self._style_cache) > self.CACHE_LIMIT:
            self._style_cache.clear()
        return style

    # ------------------------------------------------------------------
    # editing
    # ------------------------------------------------------------------
    def set_editing(self, editing, editable_cols):
        self.editing = editing
        self.editable_cols = set(editable_cols) if editing else set()

    def set_dirty(self, dirty, added):
        self.dirty, self.added = dirty, added

    def is_dirty(self, fid, col):
        return fid in self.added or col in self.dirty.get(fid, ())

    def validate(self, fid, col, value):
        """(hard problems, soft problems) of writing *value* (texts, '' when
        fine), checked with the field's QGIS constraints."""
        layer = self.layer
        feat = layer.getFeature(fid)
        if not feat.isValid():
            feat = QgsFeature(layer.fields(), fid)
        feat.setAttribute(col, value)
        hard_ok, hard = QgsVectorLayerUtils.validateAttribute(layer, feat, col, _HARD)
        soft_ok, soft = QgsVectorLayerUtils.validateAttribute(layer, feat, col, _SOFT)
        return ("; ".join(hard) if not hard_ok else ""), \
            ("; ".join(soft) if not soft_ok else "")

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        if not self.flags(index) & Qt.ItemFlag.ItemIsEditable:
            return False
        row, col = index.row(), index.column()
        fid = self.fids[row]
        layer = self.layer
        name = self.fields[col].name()
        old = self.raw_value(row, col)
        if (F.is_null(old) and F.is_null(value)) or (
                not F.is_null(old) and not F.is_null(value) and old == value
                and type(old) is type(value)):
            return True
        hard, soft = self.validate(fid, col, value)
        if hard:
            self.editRejected.emit(f"{name}: {hard}")
            return False
        layer.beginEditCommand(f"Attribute Explorer: {name} changed")
        try:
            ok = layer.changeAttributeValue(fid, col, value, old)
        except Exception:
            ok = False
        if ok:
            layer.endEditCommand()
        else:
            layer.destroyEditCommand()
            self.editRejected.emit(f"{name}: the value was not accepted")
        self._cache.pop(fid, None)
        for key in [k for k in self._style_cache if k[0] == fid]:
            del self._style_cache[key]
        if ok:
            self.dirty.setdefault(fid, set()).add(col)
            if soft:
                self.editWarned.emit(f"{name}: {soft} (soft constraint, value kept)")
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.fields) - 1))
        return ok

    # ------------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.fids)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.fields)

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.editing and index.isValid() and index.column() in self.editable_cols:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.display_text(row, col)
        if role == Qt.ItemDataRole.EditRole:
            return self.raw_value(row, col)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if self.kinds[col] in F.NUMERIC else _ALIGN_LEFT
        if role == Qt.ItemDataRole.ForegroundRole:
            style = self.cell_style(row, col)
            if style is not None and style.validTextColor():
                return style.textColor()
            if F.is_null(self.raw_value(row, col)):
                return self._null_colour
            return self._colours.get(col)
        if role == Qt.ItemDataRole.BackgroundRole:
            style = self.cell_style(row, col)
            if style is not None and style.validBackgroundColor():
                return style.backgroundColor()
            return None
        if role == Qt.ItemDataRole.FontRole:
            style = self.cell_style(row, col)
            if style is not None and style.font() != QFont():
                return style.font()
            if F.is_null(self.raw_value(row, col)):
                return self._italic
            return None
        if role == self.FidRole:
            return self.fids[row]
        if role == self.RawRole:
            return self.raw_value(row, col)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if not 0 <= section < len(self.fields):
                return None
            fld = self.fields[section]
            if role == Qt.ItemDataRole.DisplayRole:
                return fld.name()
            if role == Qt.ItemDataRole.ToolTipRole:
                try:
                    kind = fld.displayType(True)
                except Exception:
                    kind = fld.typeName()
                tip = f"<b>{fld.name()}</b><br>{kind}"
                if fld.alias():
                    tip += f"<br><i>Alias: {fld.alias()}</i>"
                return tip + "<br><br>Click to sort · drag to move"
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return _ALIGN_LEFT
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return None


# ============================================================================
# DELEGATE
# ============================================================================

class RowDelegate(QStyledItemDelegate):
    """Paints cells with padding (selection comes from the layer) and, while
    editing, opens the field's configured QGIS editor widget."""

    PAD = 7
    CORNER = 7

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._sel_bg = QColor(theme.selected)
        self._sel_fg = QColor(theme.selected_text)
        self._alt = QColor(theme.base_alt)
        self._dirty = QColor("#F5A623")          # unsaved: amber corner
        self._focus = QColor(theme.accent)
        self._wrappers = {}                      # C++ editor address -> (wrapper, index)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        model = index.model()
        row, col = index.row(), index.column()
        fid = model.fids[row] if row < len(model.fids) else None
        selected = fid in model.selected
        painter.save()
        if selected:
            painter.fillRect(opt.rect, self._sel_bg)
            painter.setPen(self._sel_fg)
        else:
            if opt.features & QStyleOptionViewItem.ViewItemFeature.Alternate:
                painter.fillRect(opt.rect, self._alt)
            background = model.data(index, Qt.ItemDataRole.BackgroundRole)
            if background is not None:
                painter.fillRect(opt.rect, QColor(background))
            painter.setPen(opt.palette.color(opt.palette.ColorRole.Text))
        painter.setFont(opt.font)
        rect = opt.rect.adjusted(self.PAD, 0, -self.PAD, 0)
        text = opt.fontMetrics.elidedText(
            opt.text, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, opt.displayAlignment, text)
        if model.editing:
            if model.is_dirty(fid, col):
                r = opt.rect
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._dirty)
                painter.drawPolygon(QPolygonF([
                    QPointF(r.left(), r.top()), QPointF(r.left() + self.CORNER, r.top()),
                    QPointF(r.left(), r.top() + self.CORNER)]))
        if opt.state & QStyle.StateFlag.State_HasFocus:
            # the framed cell (Ctrl+C copies it), browsing or editing; on a
            # selected row in the selection's text colour, to stand out
            painter.setPen(QPen(self._sel_fg if selected else self._focus, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(opt.rect).adjusted(1, 1, -1, -1))
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width() + 2 * self.PAD, size.height())

    # ------------------------------------------------------------------
    # editors: the field's configured QGIS widget
    # ------------------------------------------------------------------
    def createEditor(self, parent, option, index):
        model = index.model()
        layer = model.layer
        col = index.column()
        registry = QgsGui.editorWidgetRegistry()
        # the layer's configured widget, else the one QGIS picks for the field
        setup = registry.findBest(layer, layer.fields().at(col).name())
        wrapper = registry.create(
            setup.type(), layer, col, setup.config(), None, parent)
        if wrapper is None or wrapper.widget() is None:
            return super().createEditor(parent, option, index)
        editor = wrapper.widget()
        editor.setAutoFillBackground(True)
        editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        key = sip.unwrapinstance(editor)
        self._wrappers[key] = (wrapper, index)
        editor.destroyed.connect(lambda *_a, k=key: self._drop_wrapper(k))
        return editor

    def _drop_wrapper(self, key):
        entry = self._wrappers.pop(key, None)
        if entry is not None:
            entry[0].deleteLater()       # owned by the viewport, not the editor

    def setEditorData(self, editor, index):
        entry = self._wrappers.get(sip.unwrapinstance(editor))
        if entry is None:
            return super().setEditorData(editor, index)
        wrapper = entry[0]
        model = index.model()
        fid = model.fid(index.row())
        feat = model.layer.getFeature(fid) if fid is not None else QgsFeature()
        if feat.isValid():
            wrapper.setFeature(feat)
        else:
            wrapper.setValues(model.raw_value(index.row(), index.column()), [])

    def setModelData(self, editor, model, index):
        entry = self._wrappers.get(sip.unwrapinstance(editor))
        if entry is None:
            return super().setModelData(editor, model, index)
        model.setData(index, entry[0].value(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)

    def eventFilter(self, editor, event):
        """Enter / Tab keep a value that breaks a hard constraint in the
        editor (with the reason); Escape cancels only this cell."""
        if event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            entry = self._wrappers.get(sip.unwrapinstance(editor))
            if entry is not None and entry[1].isValid():
                wrapper, index = entry
                model = index.model()
                fid = model.fid(index.row())
                if fid is not None:
                    hard, _soft = model.validate(fid, index.column(), wrapper.value())
                    if hard:
                        QToolTip.showText(editor.mapToGlobal(QPoint(0, editor.height())),
                                          hard, editor)
                        return True
        return super().eventFilter(editor, event)


# ============================================================================
# ROW HEADER (tick box + row number)
# ============================================================================

class RowHeader(QHeaderView):
    """Left gutter: a tick box showing whether the feature is selected,
    then the row number.  Clicking selects that feature (Ctrl+click adds
    or removes it, Shift+click or a drag selects a range)."""

    rowPressed = pyqtSignal(int, object)       # row, keyboard modifiers
    rowDragged = pyqtSignal(int)               # row under the mouse

    BOX = 13

    def __init__(self, theme, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.theme = theme
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._dim = QColor(theme.text_dim)
        self._text = QColor(theme.text)
        self._border = QColor(theme.text_faint)
        self._accent = QColor(theme.accent)
        self._bg = QColor(theme.base)
        self._alt = QColor(theme.base_alt)
        self._line = QColor(theme.border_soft)
        self._drag_row = -1

    def sizeHint(self):
        model = self.model()
        rows = model.rowCount() if model is not None else 0
        digits = max(2, len(str(max(rows, 1))))
        width = 8 + self.BOX + 8 + self.fontMetrics().horizontalAdvance(
            "9" * digits) + 8
        return QSize(width, super().sizeHint().height())

    def paintSection(self, painter, rect, logical):
        if not rect.isValid():
            return
        model = self.model()
        selected = False
        if model is not None and hasattr(model, "fids") \
                and 0 <= logical < len(model.fids):
            selected = model.fids[logical] in model.selected
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, self._alt if logical % 2 else self._bg)
        painter.setPen(QPen(self._line, 1))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        box = QRectF(rect.left() + 8, rect.center().y() - self.BOX / 2 + 0.5,
                     self.BOX, self.BOX)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._accent)
            painter.drawRoundedRect(box, 2.5, 2.5)
            pen = QPen(QColor("white"), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            x, y, s = box.left(), box.top(), self.BOX
            painter.drawPolyline(QPolygonF([
                QPointF(x + s * .22, y + s * .52),
                QPointF(x + s * .43, y + s * .72),
                QPointF(x + s * .78, y + s * .30)]))
        else:
            painter.setPen(QPen(self._border, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 2.5, 2.5)
        painter.setPen(self._text if selected else self._dim)
        num_rect = QRect(int(box.right()) + 6, rect.top(),
                         rect.right() - int(box.right()) - 12, rect.height())
        painter.drawText(num_rect, Qt.AlignmentFlag.AlignRight
                         | Qt.AlignmentFlag.AlignVCenter, str(logical + 1))
        painter.restore()

    def mousePressEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") \
            else event.pos()
        row = self.logicalIndexAt(pos)
        if row >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self._drag_row = row
            self.rowPressed.emit(row, event.modifiers())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_row >= 0 and event.buttons() & Qt.MouseButton.LeftButton:
            pos = event.position().toPoint() if hasattr(event, "position") \
                else event.pos()
            row = self.logicalIndexAt(pos)
            if row >= 0 and row != self._drag_row:
                self._drag_row = row
                self.rowDragged.emit(row)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_row = -1
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()


# ============================================================================
# VIEW
# ============================================================================

class ExplorerTableView(QTableView):
    """Grid that reports keys and double clicks to the panel.  A click only
    frames the cell, browsing or editing (features are selected with the
    row header's tick boxes); Ctrl+C copies the framed cell, Ctrl+Shift+C
    the selected rows.  While editing, F2 / typing / double click open the
    editor, Tab moves between editable columns, and outside an editor the
    editing keys (undo, redo, cut, paste, delete) go to the panel instead
    of QGIS."""

    rowActivated = pyqtSignal(int)              # double click (browsing)
    selectAllRequested = pyqtSignal()
    copyCellRequested = pyqtSignal()            # Ctrl+C
    copyRowsRequested = pyqtSignal()            # Ctrl+Shift+C
    toggleRequested = pyqtSignal(int)
    contextRequested = pyqtSignal(object, int, int)   # global pos, row, col
    editKeyRequested = pyqtSignal(str)          # undo / redo / cut / paste / delete
    editorClosed = pyqtSignal()

    _EDIT_KEYS = (
        (QKeySequence.StandardKey.Undo, "undo"),
        (QKeySequence.StandardKey.Redo, "redo"),
        (QKeySequence.StandardKey.Cut, "cut"),
        (QKeySequence.StandardKey.Paste, "paste"),
        (QKeySequence.StandardKey.Delete, "delete"),
    )

    def __init__(self, theme, metrics, header, parent=None):
        super().__init__(parent)
        self.setObjectName("gtsTable")
        self.setHorizontalHeader(header)
        self.row_header = RowHeader(theme, self)
        self.setVerticalHeader(self.row_header)
        self.setItemDelegate(RowDelegate(theme, self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setShowGrid(True)
        self.setCornerButtonEnabled(False)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context)
        self.verticalHeader().setDefaultSectionSize(metrics.font_h + 9)
        self.verticalHeader().setMinimumSectionSize(metrics.font_h + 4)
        self.editing = False
        self._theme = theme
        self._corner = self.findChild(QAbstractButton)
        if self._corner is not None:
            self._corner.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._corner and event.type() == QEvent.Type.Paint:
            self._paint_corner(obj)
            return True
        return super().eventFilter(obj, event)

    def _paint_corner(self, widget):
        """Top-left corner: blank beside the profiles, '#' beside the
        column names, header colour beside the filters."""
        t = self._theme
        header = self.horizontalHeader()
        rect = widget.rect()
        top = header.name_top() if hasattr(header, "name_top") else 0
        name_h = getattr(header, "name_h", rect.height())
        p = QPainter(widget)
        p.fillRect(rect, QColor(t.panel))
        band = QRect(0, top, rect.width(), rect.height() - top)
        p.fillRect(band, QColor(t.header))
        p.setPen(QPen(QColor(t.border), 1))
        p.drawLine(band.topLeft(), band.topRight())
        p.drawLine(rect.bottomLeft(), rect.bottomRight())
        p.drawLine(rect.topRight(), rect.bottomRight())
        p.setPen(QColor(t.text_dim))
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRect(0, top, rect.width(), name_h),
                   Qt.AlignmentFlag.AlignCenter, "#")
        p.end()

    @staticmethod
    def _pos(event):
        return event.position().toPoint() if hasattr(event, "position") \
            else event.pos()

    def set_editing(self, editing):
        if self.editing == editing:
            return
        if not editing and self.state() == QAbstractItemView.State.EditingState:
            self.commit_editor()
        self.editing = editing
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed if editing
            else QAbstractItemView.EditTrigger.NoEditTriggers)
        self.viewport().update()

    def commit_editor(self, commit=True):
        """Close the open editor (writing its value unless *commit* is
        False) - before saving, switching tools or resetting the model."""
        if self.state() != QAbstractItemView.State.EditingState:
            return
        index = self.currentIndex()
        editor = self.indexWidget(index) if index.isValid() else None
        if editor is None:
            editor = self.focusWidget()
        delegate = self.itemDelegate()
        if editor is not None and editor is not self:
            if commit:
                delegate.commitData.emit(editor)
            delegate.closeEditor.emit(
                editor, QStyledItemDelegate.EndEditHint.NoHint)

    def closeEditor(self, editor, hint):
        super().closeEditor(editor, hint)
        self.editorClosed.emit()

    def mouseDoubleClickEvent(self, event):
        if self.editing:
            super().mouseDoubleClickEvent(event)  # opens the editor
            return
        idx = self.indexAt(self._pos(event))
        if idx.isValid():
            self.rowActivated.emit(idx.row())

    def _edit_key(self, event):
        for seq, name in self._EDIT_KEYS:
            if event.matches(seq):
                return name
        return None

    @staticmethod
    def _is_copy(event):
        """Ctrl+C or Ctrl+Shift+C."""
        return event.key() == Qt.Key.Key_C and bool(
            event.modifiers() & Qt.KeyboardModifier.ControlModifier)

    def event(self, event):
        # outside a cell editor these keys belong to the Explorer, not to
        # QGIS's own commands (Copy Features; while editing undo / paste /
        # delete too)
        if event.type() == QEvent.Type.ShortcutOverride \
                and self.state() != QAbstractItemView.State.EditingState \
                and (self._is_copy(event)
                     or (self.editing and self._edit_key(event))):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if self.editing:
            name = self._edit_key(event)
            if name:
                self.editKeyRequested.emit(name)
                return
        if ctrl and key == Qt.Key.Key_A:
            self.selectAllRequested.emit()
            return
        if self._is_copy(event):
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self.copyRowsRequested.emit()
            else:
                self.copyCellRequested.emit()
            return
        if key == Qt.Key.Key_Space and self.currentIndex().isValid() and not self.editing:
            self.toggleRequested.emit(self.currentIndex().row())
            return
        super().keyPressEvent(event)           # the arrows move the frame

    def moveCursor(self, action, modifiers):
        """Tab / Shift+Tab move to the next editable shown column."""
        if not self.editing or action not in (
                QAbstractItemView.CursorAction.MoveNext,
                QAbstractItemView.CursorAction.MovePrevious):
            return super().moveCursor(action, modifiers)
        current = self.currentIndex()
        model = self.model()
        if not current.isValid() or model is None:
            return super().moveCursor(action, modifiers)
        header = self.horizontalHeader()
        order = [header.logicalIndex(v) for v in range(header.count())
                 if not header.isSectionHidden(header.logicalIndex(v))
                 and header.logicalIndex(v) in model.editable_cols]
        if not order:
            return current
        step = 1 if action == QAbstractItemView.CursorAction.MoveNext else -1
        row = current.row()
        pos = order.index(current.column()) if current.column() in order else -1
        pos += step
        if pos >= len(order):
            pos, row = 0, row + 1
        elif pos < 0:
            pos, row = len(order) - 1, row - 1
        if not 0 <= row < model.rowCount():
            return current
        return model.index(row, order[pos])

    def _context(self, pos):
        idx = self.indexAt(pos)
        self.contextRequested.emit(self.viewport().mapToGlobal(pos),
                                   idx.row() if idx.isValid() else -1,
                                   idx.column() if idx.isValid() else -1)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        if dx:
            self.horizontalHeader().relayout()
