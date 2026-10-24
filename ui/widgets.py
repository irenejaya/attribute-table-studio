# -*- coding: utf-8 -*-
"""
Shared widgets, so every Attribute Table Studio tool looks and behaves the same.

- :func:`button` / :func:`square_button`: QToolButtons of one height
  (``Metrics.control_h``, the same as inputs) with 16 px icons; square
  utility buttons are control_h x control_h.
- :func:`action_button`: blue, bold, label-only main action at the control
  height (Build Pivot).
- :func:`primary_square`: Swap, at ``Metrics.primary_h`` like the Compare
  action buttons.
- :class:`ButtonRow`: a row of buttons that switches to icon-only (all at
  once, so they stay uniform, icons at full size) when there is not enough
  room, e.g. docked.
- :class:`StatusStrip`: the bottom status line (framed, coloured accent)
  with a Cancel button while a job runs.
- :class:`FieldChecklistDialog`: searchable field checklist used by
  "Columns" (Explorer) and "Field(s) to Compare" (Compare).
"""

from qgis.PyQt.QtCore import QPoint, QPointF, QRect, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter, QPolygonF
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit, QListWidget,
    QListWidgetItem, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..core import fields as F
from . import icons


# ============================================================================
# LABELS
# ============================================================================

class ElideLabel(QLabel):
    """Single-line label that elides its text instead of widening a layout."""

    def __init__(self, text="", parent=None, mode=Qt.TextElideMode.ElideRight):
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        self._refresh()

    def fullText(self):
        return self._full

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 4,
                     hint.height())

    def minimumSizeHint(self):
        return QSize(self.minimumWidth(), super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        elided = self.fontMetrics().elidedText(
            self._full, self._mode, max(self.width(), 10))
        super().setText(elided)
        self.setToolTip(self._full if elided != self._full else "")


def label(text="", role=None, parent=None):
    """QLabel with a style role: 'title', 'heading', 'dim', 'strong', 'link'."""
    lbl = QLabel(text, parent)
    if role:
        lbl.setObjectName("gts" + role.capitalize())
    return lbl


def separator_dot():
    return "  •  "


# ============================================================================
# BUTTONS
# ============================================================================

def _base_button(metrics, icon=None, tooltip=""):
    btn = QToolButton()
    btn.setFixedHeight(metrics.control_h)
    btn.setIconSize(QSize(metrics.icon, metrics.icon))
    # never squeeze a button below its label (rows collapse or elide instead)
    btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    btn.setAutoRaise(False)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    if icon is not None:
        btn.setIcon(icon)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def button(metrics, text, icon=None, tooltip="", checkable=False, menu=None):
    """Standard bordered button: 16 px icon + text, fixed control height."""
    btn = _base_button(metrics, icon, tooltip)
    btn.setObjectName("gtsButton")
    # a leading space gives the same icon-to-label gap on every style
    btn.setText(f" {text}" if icon is not None else text)
    btn.setToolButtonStyle(
        Qt.ToolButtonStyle.ToolButtonTextBesideIcon if icon is not None
        else Qt.ToolButtonStyle.ToolButtonTextOnly)
    btn.setCheckable(checkable)
    if menu is not None:
        btn.setMenu(menu)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    return btn


def primary_button(metrics, text, theme, tooltip=""):
    """Blue primary action with a white run arrow (the value list's Apply)."""
    btn = _base_button(metrics, icons.glyph("run", "#FFFFFF"), tooltip)
    btn.setFixedHeight(metrics.primary_h)
    btn.setObjectName("gtsPrimary")
    btn.setText(f" {text}")
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    btn.setProperty("gtsKeepLabel", True)      # the main action keeps its label
    # the label is bold (style sheet), so size it from bold metrics
    btn.setMinimumWidth(metrics.bold_fm.horizontalAdvance(f" {text}")
                        + metrics.icon + 2 * 14 + 12)
    return btn


def action_button(metrics, text, tooltip=""):
    """Blue main action with a bold label and no icon, as tall as the buttons
    beside it (Build Pivot).  It keeps its label when a row turns compact."""
    btn = _base_button(metrics, None, tooltip)
    btn.setObjectName("gtsPrimary")
    btn.setText(text)
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    btn.setProperty("gtsKeepLabel", True)
    btn.setMinimumWidth(metrics.bold_fm.horizontalAdvance(text) + 2 * 14 + 8)
    return btn


def square_button(metrics, icon, tooltip="", checkable=False):
    """Icon-only utility button, control_h x control_h."""
    btn = _base_button(metrics, icon, tooltip)
    btn.setObjectName("gtsSquare")
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    btn.setFixedSize(metrics.control_h, metrics.control_h)
    btn.setCheckable(checkable)
    return btn


def primary_square(metrics, icon, tooltip=""):
    """Icon-only button at the primary height (Swap)."""
    btn = square_button(metrics, icon, tooltip)
    btn.setFixedSize(metrics.primary_h, metrics.primary_h)
    return btn


def flat_link(metrics, text, tooltip=""):
    """Text link button ('[Add filter]')."""
    btn = QToolButton()
    btn.setObjectName("gtsFlat")
    btn.setText(text)
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setAutoRaise(True)
    return btn


def set_button_text(btn, text):
    """Change a button label (keeps ButtonRow's compact mode in sync)."""
    text = f" {text}" if not btn.icon().isNull() else text
    if btn.property("gtsLabel"):
        btn.setProperty("gtsLabel", text)
        btn.setToolTip(btn.toolTip() or text.strip())
    btn.setText(text)


def field_tooltip(field):
    try:
        type_desc = field.displayType(True)
    except Exception:
        type_desc = field.typeName()
    tip = f"<b>{field.name()}</b><br>{type_desc}"
    if field.alias():
        tip += f"<br><i>Alias: {field.alias()}</i>"
    return tip


class FieldCombo(QComboBox):
    """Field picker with native field-type icons (optionally 'None')."""

    def __init__(self, metrics, theme, none_label=None, parent=None):
        super().__init__(parent)
        self._metrics = metrics
        self._theme = theme
        self._none_label = none_label
        self._kinds = {}
        self.setFixedHeight(metrics.control_h)
        self.setIconSize(QSize(metrics.icon, metrics.icon))
        self.setMaxVisibleItems(18)
        self.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(10)

    def set_fields(self, fields, numeric_only=False, keep=True):
        previous = self.current_name() if keep else None
        self.blockSignals(True)
        self.clear()
        self._kinds = {}
        if self._none_label is not None:
            self.addItem(self._none_label, None)
        for i in range(fields.count()):
            fld = fields.at(i)
            kind = F.field_kind(fld)
            if numeric_only and kind not in F.NUMERIC:
                continue
            self._kinds[fld.name()] = kind
            self.addItem(icons.field(kind, self._theme), fld.name(), fld.name())
            self.setItemData(self.count() - 1, field_tooltip(fld),
                             Qt.ItemDataRole.ToolTipRole)
        idx = self.findData(previous) if previous else -1
        self.setCurrentIndex(idx if idx >= 0 else 0)
        self.blockSignals(False)

    def current_name(self):
        return self.currentData()

    def set_current_name(self, name):
        idx = self.findData(name) if name else (0 if self._none_label else -1)
        if idx >= 0:
            self.setCurrentIndex(idx)
        return idx >= 0

    def kind(self, name=None):
        return self._kinds.get(name if name is not None else self.current_name())


def section_header(text):
    """Bold heading followed by a divider line: 'Rows ───────────'."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lay.addWidget(label(text, "title"))
    line = QFrame()
    line.setObjectName("gtsDivider")
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    lay.addWidget(line, 1, Qt.AlignmentFlag.AlignVCenter)
    return box


class Chip(QFrame):
    """Rounded pill showing a filter; click to edit, × to remove."""

    clicked = pyqtSignal()
    removed = pyqtSignal()

    def __init__(self, text, metrics, theme, max_width=320, parent=None):
        super().__init__(parent)
        self.setObjectName("gtsChip")
        self.setFixedHeight(metrics.control_h - 2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 3, 0)
        lay.setSpacing(4)
        self.text_label = ElideLabel(text)
        self.text_label.setMinimumWidth(30)
        self.text_label.setMaximumWidth(max(60, max_width - 40))
        self.text_label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                      QSizePolicy.Policy.Preferred)
        self.text_label.setToolTip(f"{text}\nClick to edit")
        lay.addWidget(self.text_label)
        close = QToolButton()
        close.setIcon(icons.glyph("cross", theme.text_dim))
        close.setIconSize(QSize(12, 12))
        close.setFixedSize(metrics.control_h - 8, metrics.control_h - 8)
        close.setToolTip("Remove this filter")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.removed.emit)
        lay.addWidget(close)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def dock_button(metrics):
    btn = square_button(metrics, icons.native(icons.DOCK),
                        "Dock this window", checkable=True)
    return btn


class ButtonRow(QWidget):
    """Horizontal button group; collapses every button to icon-only when the
    row is narrower than the buttons need with their labels."""

    def __init__(self, metrics, parent=None):
        super().__init__(parent)
        self._metrics = metrics
        self._buttons = []
        self._compact = False
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(metrics.gap)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    def add(self, btn):
        self._buttons.append(btn)
        self._lay.addWidget(btn)
        return btn

    def add_spacing(self, px=None):
        self._lay.addSpacing(self._metrics.group_gap if px is None else px)

    def add_stretch(self):
        self._lay.addStretch(1)

    def _text_width(self, b):
        """Width of *b* showing its label (measured by Qt while the button
        is in text mode, so style padding and menu arrows are included)."""
        if b.toolButtonStyle() != Qt.ToolButtonStyle.ToolButtonIconOnly:
            width = b.sizeHint().width()
            if b.isVisible() or b.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
                b.setProperty("gtsFullWidth", width)
            return width
        stored = b.property("gtsFullWidth")
        if stored:
            return int(stored)
        text = b.property("gtsLabel") or b.text()
        extra = 30 + self._metrics.icon + (16 if b.menu() is not None else 0)
        return b.fontMetrics().horizontalAdvance(text) + extra

    def _compact_width(self, b):
        """Icon-only width: a square, plus room for a menu arrow."""
        return self._metrics.control_h + (14 if b.menu() is not None else 0)

    def _full_width(self):
        total = 0
        for b in self._buttons:
            if b.isHidden():
                continue
            if b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly \
                    and not b.property("gtsLabel"):
                total += self._compact_width(b)
                continue
            total += self._text_width(b)
        visible = [b for b in self._buttons if not b.isHidden()]
        total += self._metrics.gap * max(len(visible) - 1, 0)
        return total

    def _height(self):
        """Tallest button (a primary action is taller than the rest; the
        others are centred on it)."""
        return max([self._metrics.control_h]
                   + [b.maximumHeight() for b in self._buttons
                      if not b.isHidden() and b.maximumHeight() < 1000])

    def sizeHint(self):
        return QSize(self._full_width(), self._height())

    def minimumSizeHint(self):
        width = 0
        for b in self._buttons:
            if b.isHidden():
                continue
            width += (b.sizeHint().width() if b.property("gtsKeepLabel")
                      else self._compact_width(b)) + self._metrics.gap
        return QSize(width, self._height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply)

    def _apply(self):
        compact = self.width() < self._full_width()
        if compact == self._compact:
            return
        self._compact = compact
        for b in self._buttons:
            if b.icon().isNull() or b.property("gtsKeepLabel"):
                continue
            if compact:
                if not b.property("gtsLabel"):
                    b.setProperty("gtsLabel", b.text())
                    if not b.toolTip():
                        b.setToolTip(b.text())
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                # icon-only buttons become exact squares, like square_button
                b.setFixedWidth(self._compact_width(b))
            elif b.property("gtsLabel"):
                b.setMinimumWidth(0)
                b.setMaximumWidth(16777215)
                b.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            # the label padding goes in compact mode, so the style sheet never
            # shrinks the 16 px icon to fit
            b.setProperty("gtsCompact", compact)
            b.style().unpolish(b)
            b.style().polish(b)


# ============================================================================
# TABLE HEADER
# ============================================================================

def paint_sort_arrow(header, painter, rect, logical, colour):
    """Draw ▲ (ascending) or ▼ (descending) in the 22 px the style sheet
    reserves at the right of every header section."""
    if logical != header.sortIndicatorSection() or logical < 0:
        return
    ascending = header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
    cx = rect.right() - 12
    cy = rect.center().y() + 0.5
    w, h = 4.5, 3.0
    if ascending:
        pts = [QPointF(cx - w, cy + h), QPointF(cx + w, cy + h), QPointF(cx, cy - h)]
    else:
        pts = [QPointF(cx - w, cy - h), QPointF(cx + w, cy - h), QPointF(cx, cy + h)]
    painter.save()
    # the style may leave a clip on the label area (Qt5); use the section
    painter.setClipRect(rect)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawPolygon(QPolygonF(pts))
    painter.restore()


class SortHeader(QHeaderView):
    """Horizontal table header that draws its own sort arrow, so the arrow
    looks and points the same on Qt5 and Qt6 and never covers the label."""

    def __init__(self, theme, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._arrow = theme.text_dim
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignVCenter)

    def paintSection(self, painter, rect, logical):
        super().paintSection(painter, rect, logical)
        if self.isSortIndicatorShown():
            paint_sort_arrow(self, painter, rect, logical, self._arrow)


# ============================================================================
# FLOW LAYOUT
# ============================================================================

class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto the next line when the row is
    too narrow (keeps option rows tidy in narrow docks)."""

    def __init__(self, parent=None, h_spacing=10, v_spacing=6):
        super().__init__(parent)
        self._items = []
        self._h = h_spacing
        self._v = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h
            if next_x - self._h > area.right() + 1 and line_h > 0:
                x = area.x()
                y += line_h + self._v
                next_x = x + hint.width() + self._h
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


def labelled(text, widget, spacing=6):
    """'Label [widget]' kept together as one unit (for FlowLayout)."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    lbl = QLabel(text)
    lay.addWidget(lbl)
    lay.addWidget(widget)
    box.label = lbl
    return box


# ============================================================================
# STATUS STRIP
# ============================================================================

class StatusStrip(QFrame):
    """Bottom status line, framed with a coloured left accent (as in
    Geometry and Table Compare v1.1): message on the left, extra info on the
    right and a Cancel button while a job runs.  The panel keeps a margin
    below it, so the whole bar is always inside the window."""

    cancelRequested = pyqtSignal()

    def __init__(self, metrics, parent=None):
        super().__init__(parent)
        self.setObjectName("gtsStatus")
        self.setMinimumHeight(metrics.control_h + 6)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 3, 6, 3)
        lay.setSpacing(metrics.gap)
        self.message = ElideLabel()
        lay.addWidget(self.message, 1)
        self.extra = QLabel("")
        self.extra.setObjectName("gtsDim")
        lay.addWidget(self.extra)
        self.cancel_btn = QToolButton()
        self.cancel_btn.setIcon(icons.native(icons.CANCEL))
        self.cancel_btn.setIconSize(QSize(metrics.icon, metrics.icon))
        self.cancel_btn.setAutoRaise(True)
        self.cancel_btn.setFixedSize(metrics.control_h - 4, metrics.control_h - 4)
        self.cancel_btn.setToolTip("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancelRequested.emit)
        lay.addWidget(self.cancel_btn)
        self.set_status("Ready")

    def set_status(self, text, level="info"):
        self.message.setText(text)
        self.setProperty("level", level)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_extra(self, text):
        self.extra.setText(text)

    def set_busy(self, busy, cancellable=True):
        self.cancel_btn.setVisible(busy and cancellable)


# ============================================================================
# FIELD CHECKLIST
# ============================================================================

class FieldChecklistDialog(QDialog):
    """Searchable list of fields with tick boxes.

    *movable* lets the user drag fields into a new order (Explorer
    columns).  Show All / Hide All / Toggle Selection act on the visible
    (search-matched) entries.
    """

    def __init__(self, entries, metrics, theme, title, info="",
                 movable=False, parent=None):
        """*entries*: [(name, icon, tooltip, checked)] in display order."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(420, 500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(metrics.margin, metrics.margin,
                               metrics.margin, metrics.margin)
        lay.setSpacing(8)
        if info:
            text = QLabel(info)
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(text)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter fields")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(metrics.control_h)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)

        self.listw = QListWidget()
        self.listw.setIconSize(QSize(metrics.icon, metrics.icon))
        self.listw.setUniformItemSizes(True)
        if movable:
            self.listw.setDragDropMode(
                QAbstractItemView.DragDropMode.InternalMove)
            self.listw.setDefaultDropAction(Qt.DropAction.MoveAction)
        for name, icon, tip, checked in entries:
            item = QListWidgetItem(icon, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if checked
                               else Qt.CheckState.Unchecked)
            if tip:
                item.setToolTip(tip)
            self.listw.addItem(item)
        lay.addWidget(self.listw, 1)

        row = QHBoxLayout()
        row.setSpacing(metrics.gap)
        for text, tip, fn in (
                ("Show All", "Tick every listed field", lambda: self._set_all(True)),
                ("Hide All", "Untick every listed field", lambda: self._set_all(False)),
                ("Toggle Selection", "Invert the tick of every listed field",
                 self._toggle_all)):
            b = button(metrics, text, tooltip=tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        if movable:
            hint = QLabel("Drag to reorder")
            hint.setObjectName("gtsDim")
            row.addWidget(hint)
        lay.addLayout(row)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _items(self):
        return [self.listw.item(i) for i in range(self.listw.count())]

    def _filter(self, text):
        text = text.lower()
        for item in self._items():
            item.setHidden(text not in item.text().lower())

    def _set_all(self, state):
        check = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        for item in self._items():
            if not item.isHidden():
                item.setCheckState(check)

    def _toggle_all(self):
        for item in self._items():
            if not item.isHidden():
                item.setCheckState(
                    Qt.CheckState.Unchecked
                    if item.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked)

    def checked_names(self):
        return [i.text() for i in self._items()
                if i.checkState() == Qt.CheckState.Checked]

    def ordered_names(self):
        return [i.text() for i in self._items()]
