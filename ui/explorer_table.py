# -*- coding: utf-8 -*-
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
  green and right aligned, NULL grey italic.
"""

from collections import OrderedDict

from qgis.PyQt.QtCore import (
    QAbstractTableModel, QEvent, QModelIndex, QPointF, QRect, QRectF, QSize,
    Qt, pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from qgis.PyQt.QtWidgets import (
    QAbstractButton, QAbstractItemView, QHeaderView, QStyledItemDelegate,
    QStyleOptionViewItem, QTableView,
)
from qgis.core import QgsApplication, QgsFeatureRequest

from ..core import fields as F
from ..core.compat import NO_GEOMETRY, enum_int

_ALIGN_LEFT = enum_int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_RIGHT = enum_int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class FeatureTableModel(QAbstractTableModel):
    FidRole = Qt.ItemDataRole.UserRole + 1
    RawRole = Qt.ItemDataRole.UserRole + 2
    PAGE = 256
    CACHE_LIMIT = 30000

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
        else:
            for fid in fids:
                self._cache.pop(fid, None)
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
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.fids)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.fields)

    def flags(self, index):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.display_text(row, col)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if self.kinds[col] in F.NUMERIC else _ALIGN_LEFT
        if role == Qt.ItemDataRole.ForegroundRole:
            if F.is_null(self.raw_value(row, col)):
                return self._null_colour
            return self._colours.get(col)
        if role == Qt.ItemDataRole.FontRole:
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
    """Paints cells with padding; selection comes from the layer."""

    PAD = 7

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._sel_bg = QColor(theme.selected)
        self._sel_fg = QColor(theme.selected_text)
        self._alt = QColor(theme.base_alt)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        model = index.model()
        fid = model.fids[index.row()] if index.row() < len(model.fids) else None
        selected = fid in model.selected
        painter.save()
        if selected:
            painter.fillRect(opt.rect, self._sel_bg)
            painter.setPen(self._sel_fg)
        else:
            if opt.features & QStyleOptionViewItem.ViewItemFeature.Alternate:
                painter.fillRect(opt.rect, self._alt)
            painter.setPen(opt.palette.color(opt.palette.ColorRole.Text))
        painter.setFont(opt.font)
        rect = opt.rect.adjusted(self.PAD, 0, -self.PAD, 0)
        text = opt.fontMetrics.elidedText(
            opt.text, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, opt.displayAlignment, text)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width() + 2 * self.PAD, size.height())


# ============================================================================
# ROW HEADER (tick box + row number)
# ============================================================================

class RowHeader(QHeaderView):
    """Left gutter: a tick box showing whether the feature is selected,
    then the row number.  Clicking toggles that feature."""

    rowPressed = pyqtSignal(int, object)       # row, keyboard modifiers

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
            self.rowPressed.emit(row, event.modifiers())
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()


# ============================================================================
# VIEW
# ============================================================================

class ExplorerTableView(QTableView):
    """Read-only grid; reports clicks / drags / keys so the panel can
    update the QGIS selection."""

    rowPressed = pyqtSignal(int, object)        # row, modifiers
    rowDragged = pyqtSignal(int)                # row under the mouse
    rowActivated = pyqtSignal(int)              # double click
    currentMoved = pyqtSignal(int, object)      # keyboard navigation
    selectAllRequested = pyqtSignal()
    copyRequested = pyqtSignal()
    toggleRequested = pyqtSignal(int)
    contextRequested = pyqtSignal(object, int, int)   # global pos, row, col

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
        self.row_header.rowPressed.connect(self.rowPressed.emit)
        self._dragging = False
        self._last_drag_row = -1
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

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        idx = self.indexAt(self._pos(event))
        if idx.isValid():
            self._dragging = True
            self._last_drag_row = idx.row()
            self.rowPressed.emit(idx.row(), event.modifiers())

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            idx = self.indexAt(self._pos(event))
            if idx.isValid() and idx.row() != self._last_drag_row:
                self._last_drag_row = idx.row()
                self.rowDragged.emit(idx.row())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        idx = self.indexAt(self._pos(event))
        if idx.isValid():
            self.rowActivated.emit(idx.row())

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key == Qt.Key.Key_A:
            self.selectAllRequested.emit()
            return
        if ctrl and key == Qt.Key.Key_C:
            self.copyRequested.emit()
            return
        if key == Qt.Key.Key_Space and self.currentIndex().isValid():
            self.toggleRequested.emit(self.currentIndex().row())
            return
        before = self.currentIndex().row()
        super().keyPressEvent(event)
        after = self.currentIndex().row()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp,
                   Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End) \
                and after >= 0 and after != before:
            self.currentMoved.emit(after, mods)

    def _context(self, pos):
        idx = self.indexAt(pos)
        self.contextRequested.emit(self.viewport().mapToGlobal(pos),
                                   idx.row() if idx.isValid() else -1,
                                   idx.column() if idx.isValid() else -1)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        if dx:
            self.horizontalHeader().relayout()
