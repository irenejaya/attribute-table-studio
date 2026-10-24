# -*- coding: utf-8 -*-
"""
Attribute Explorer column header: profile strip, column names and filters.

:class:`ExplorerHeader` is the table's horizontal header with three bands:

    ┌───────────────┬───────────────┐
    │ profile card  │ profile card  │   collapsible ("Summary: On / Off")
    ├───────────────┼───────────────┤
    │ NAME        ▲ │ NAME          │   click = sort, drag = move, edge = resize
    ├───────────────┼───────────────┤
    │ ≈ contains  ▾ │ = value     ▾ │   always-visible filter
    └───────────────┴───────────────┘

Cards and filter cells are children of the header viewport positioned from
``sectionViewportPosition`` / ``sectionSize``, so resizing, hiding,
reordering or horizontally scrolling a column moves its profile, heading and
filter together.
"""

import datetime

from qgis.PyQt.QtCore import QDate, QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QCalendarWidget, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QToolButton, QToolTip,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import fields as F
from ..core import filters as FL
from ..core import stats as S
from ..core.compat import QAction, QActionGroup
from . import icons
from . import widgets as W


def fmt_pct(n, base):
    """'0%', '<1%', '93%', '>99%', '100%' (never rounds up to a false 100%)."""
    if not base or not n:
        return "0%"
    p = 100.0 * n / base
    if p < 1:
        return "<1%"
    if n < base and p >= 99.5:
        return ">99%"
    return f"{p:.0f}%"


def fmt_value(value, kind):
    """Short display of a statistic (min / max / earliest ...)."""
    if value is None:
        return F.NULL_TEXT
    if kind in F.NUMERIC:
        return F.format_number(value)
    return F.display_value(value)


# ============================================================================
# PROFILE CARD
# ============================================================================

class ProfileCard(QWidget):
    """Compact statistics of one column, painted for crisp alignment."""

    clicked = pyqtSignal(int)

    PAD = 8

    def __init__(self, logical, name, kind, theme, metrics, parent=None):
        super().__init__(parent)
        self.logical = logical
        self.name = name
        self.kind = kind
        self.theme = theme
        self.metrics = metrics
        self.profile = None
        self.state = "pending"
        self.message = ""
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bars = []            # [(QRectF, bin index)]
        self._hover = -1
        base = QFont(self.font())
        self._title_font = QFont(base)
        self._title_font.setBold(True)
        self._small = QFont(base)
        size = base.pointSizeF() if base.pointSizeF() > 0 else 9.0
        self._small.setPointSizeF(max(7.0, size - 0.5))
        self._big = QFont(base)
        self._big.setPointSizeF(size * 2.1)
        self._big.setBold(False)
        self._caption = QFont(base)
        self._c = {k: QColor(getattr(theme, k)) for k in (
            "card", "border", "text", "text_strong", "text_dim", "text_faint", "bar",
            "bar_hover", "big_number", "warning", "danger", "accent")}

    @staticmethod
    def preferred_height(metrics):
        small = max(12, metrics.fm.height() - 1)
        title = metrics.bold_fm.height()
        return 8 + title + 4 + 2 * small + 6 + 4 * small + 8

    def set_state(self, state, profile=None, message=""):
        self.state = state
        self.profile = profile
        self.message = message
        self._hover = -1
        self.setToolTip(self._tooltip())
        self.update()

    def _tooltip(self):
        p = self.profile
        if self.state != "ready" or p is None:
            return f"<b>{self.name}</b><br>{self.message or 'Calculating…'}"
        tip = (f"<b>{self.name}</b> · {F.KIND_LABELS.get(self.kind, '')}<br>"
               f"Records: {p.count:,}<br>Missing (NULL): {p.missing:,}"
               f"<br>Distinct (non-NULL): {p.distinct:,}")
        if self.kind in F.NUMERIC and p.valid:
            tip += (f"<br>Mean: {F.format_number(p.mean)}"
                    f"<br>Median: {F.format_number(p.median)}")
        if p.invalid:
            tip += f"<br><span style='color:{self.theme.warning}'>"\
                   f"Invalid: {p.invalid:,}</span>"
        return tip + "<br><br><i>Click for full statistics</i>"

    # ------------------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self._c
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(c["border"], 1))
        p.setBrush(c["card"])
        p.drawRoundedRect(rect, 3, 3)

        pad = self.PAD
        w = self.width()
        tfm = QFontMetrics(self._title_font)
        sfm = QFontMetrics(self._small)
        y = pad
        icon = icons.field(self.kind, self.theme)
        p.drawPixmap(pad, y + (tfm.height() - 16) // 2, icon.pixmap(QSize(16, 16)))
        p.setFont(self._title_font)
        p.setPen(c["text_strong"])
        name_rect = QRect(pad + 21, y, w - 2 * pad - 21, tfm.height())
        p.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   tfm.elidedText(self.name, Qt.TextElideMode.ElideRight,
                                  name_rect.width()))
        y += tfm.height() + 4

        prof = self.profile
        ready = self.state == "ready" and prof is not None
        p.setFont(self._small)
        row_h = sfm.height()
        for label, value in (
                ("Missing", f"{prof.missing:,} ({fmt_pct(prof.missing, prof.count)})"
                 if ready else "…"),
                ("Distinct", f"{prof.distinct:,} ({fmt_pct(prof.distinct, prof.count)})"
                 if ready else "…")):
            r = QRect(pad, y, w - 2 * pad, row_h)
            vw = sfm.horizontalAdvance(value)
            p.setPen(c["text_dim"])
            label_w = r.width() - vw - 6
            if label_w > sfm.horizontalAdvance("M…"):
                p.drawText(QRect(r.left(), r.top(), label_w, r.height()),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           sfm.elidedText(label, Qt.TextElideMode.ElideRight, label_w))
            p.setPen(c["text_dim"] if not ready else c["text"])
            p.drawText(r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       sfm.elidedText(value, Qt.TextElideMode.ElideLeft, r.width()))
            y += row_h
        y += 6
        body = QRect(pad, y, w - 2 * pad, self.height() - y - pad)
        self._bars = []
        if not ready:
            self._paint_message(p, body, self.message or "Calculating…")
        elif prof.count == 0:
            self._paint_message(p, body, "No records in scope")
        elif prof.non_null == 0:
            self._paint_big(p, body, "0", "All values missing")
        elif self.kind in F.NUMERIC:
            self._paint_numeric(p, body, prof)
        elif self.kind in F.TEMPORAL:
            self._paint_temporal(p, body, prof)
        elif self.kind == F.BOOL:
            self._paint_bool(p, body, prof)
        elif prof.is_categorical():
            self._paint_categories(p, body, prof)
        else:
            self._paint_big(p, body, f"{prof.distinct:,}", "Distinct values")
        p.end()

    def _paint_message(self, p, rect, text):
        p.setFont(self._small)
        p.setPen(self._c["text_faint"])
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

    def _paint_big(self, p, rect, number, caption):
        sfm = QFontMetrics(self._small)
        bfm = QFontMetrics(self._big)
        total = bfm.height() + sfm.height()
        top = rect.top() + max(0, (rect.height() - total) // 2)
        p.setFont(self._big)
        p.setPen(self._c["big_number"])
        p.drawText(QRect(rect.left(), top, rect.width(), bfm.height()),
                   Qt.AlignmentFlag.AlignCenter,
                   bfm.elidedText(number, Qt.TextElideMode.ElideRight, rect.width()))
        p.setFont(self._small)
        p.setPen(self._c["text_dim"])
        p.drawText(QRect(rect.left(), top + bfm.height(), rect.width(), sfm.height()),
                   Qt.AlignmentFlag.AlignCenter, caption)

    def _paint_categories(self, p, rect, prof):
        sfm = QFontMetrics(self._small)
        row_h = sfm.height()
        rows = max(1, rect.height() // row_h)
        top = prof.top_categories(max(1, rows - 1))
        p.setFont(self._small)
        y = rect.top()
        for value, _count, share in top[:rows]:
            r = QRect(rect.left(), y, rect.width(), row_h)
            text = "Other classes" if value is None else (value if value != "" else "(blank)")
            p.setPen(self._c["text_dim"] if value is None else self._c["text"])
            pct = fmt_pct(_count, prof.non_null)
            pw = sfm.horizontalAdvance(pct) + 6
            p.drawText(r.adjusted(0, 0, -pw, 0),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       sfm.elidedText(text, Qt.TextElideMode.ElideRight, r.width() - pw))
            p.setPen(self._c["text_dim"])
            p.drawText(r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, pct)
            y += row_h

    def _paint_hist(self, p, rect, bins):
        if not bins:
            return
        top_count = max(b[2] for b in bins) or 1
        n = len(bins)
        gap = 2.0 if rect.width() / n > 6 else 1.0
        bw = max(1.0, (rect.width() - gap * (n - 1)) / n)
        p.setPen(Qt.PenStyle.NoPen)
        for i, (_lo, _hi, count) in enumerate(bins):
            h = (rect.height() - 1) * count / top_count
            if count and h < 2:
                h = 2
            x = rect.left() + i * (bw + gap)
            bar = QRectF(x, rect.bottom() - h + 1, bw, h)
            full = QRectF(x, rect.top(), bw + gap, rect.height())
            self._bars.append((full, i))
            p.setBrush(self._c["bar_hover"] if i == self._hover else self._c["bar"])
            p.drawRect(bar)
        p.setPen(QPen(self._c["border"], 1))
        p.drawLine(rect.left(), rect.bottom() + 1, rect.right(), rect.bottom() + 1)

    def _paint_numeric(self, p, rect, prof):
        if prof.valid == 0:
            self._paint_big(p, rect, f"{prof.invalid:,}", "No finite values")
            return
        if prof.constant:
            self._paint_big(p, rect, F.format_number(prof.minimum), "Constant value")
            return
        sfm = QFontMetrics(self._small)
        hist = QRect(rect.left(), rect.top(), rect.width(),
                     rect.height() - sfm.height() - 2)
        self._paint_hist(p, hist, prof.bins)
        self._paint_caption(p, rect, f"Min {F.format_number(prof.minimum)}",
                            f"Max {F.format_number(prof.maximum)}")

    def _paint_temporal(self, p, rect, prof):
        if prof.valid == 0:
            self._paint_message(p, rect, "No valid values")
            return
        if prof.constant:
            self._paint_big(p, rect, "1", fmt_value(prof.earliest, self.kind))
            return
        sfm = QFontMetrics(self._small)
        hist = QRect(rect.left(), rect.top(), rect.width(),
                     rect.height() - sfm.height() - 2)
        self._paint_hist(p, hist, prof.bins)
        lo = fmt_value(prof.earliest, self.kind)[:10 if self.kind != F.TIME else 8]
        hi = fmt_value(prof.latest, self.kind)[:10 if self.kind != F.TIME else 8]
        self._paint_caption(p, rect, lo, hi)

    def _paint_caption(self, p, rect, left, right):
        sfm = QFontMetrics(self._small)
        r = QRect(rect.left(), rect.bottom() - sfm.height() + 1, rect.width(),
                  sfm.height())
        p.setFont(self._small)
        p.setPen(self._c["text_dim"])
        half = r.width() // 2 - 2
        p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   sfm.elidedText(left, Qt.TextElideMode.ElideRight, half))
        p.drawText(r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   sfm.elidedText(right, Qt.TextElideMode.ElideLeft, half))

    def _paint_bool(self, p, rect, prof):
        sfm = QFontMetrics(self._small)
        row_h = sfm.height() + 2
        p.setFont(self._small)
        rows = [("true", prof.true_count), ("false", prof.false_count),
                ("NULL", prof.missing)]
        label_w = sfm.horizontalAdvance("false") + 8
        y = rect.top()
        for text, n in rows:
            pct = fmt_pct(n, prof.count)
            pw = sfm.horizontalAdvance("100%") + 6
            r = QRect(rect.left(), y, rect.width(), row_h)
            p.setPen(self._c["text"] if text != "NULL" else self._c["text_dim"])
            p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            bar_w = max(0, r.width() - label_w - pw)
            frac = n / prof.count if prof.count else 0
            bar = QRectF(r.left() + label_w, y + row_h * 0.25, bar_w * frac, row_h * 0.5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._c["bar"] if text != "NULL" else self._c["text_faint"])
            p.drawRoundedRect(bar, 2, 2)
            p.setPen(self._c["text_dim"])
            p.drawText(r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, pct)
            y += row_h

    # ------------------------------------------------------------------
    def mouseMoveEvent(self, event):
        pos = event.position() if hasattr(event, "position") else event.pos()
        hit = -1
        for rect, i in self._bars:
            if rect.contains(pos):
                hit = i
                break
        if hit != self._hover:
            self._hover = hit
            self.update()
        if hit >= 0 and self.profile is not None:
            lo, hi, count = self.profile.bins[hit]
            last = hit == len(self.profile.bins) - 1
            if self.kind in F.TEMPORAL:
                fmt = (lambda v: F.key_to_text(v, self.kind))
            else:
                fmt = F.format_number
            label = S.bin_label(lo, hi, last, self.profile.integer_bins, fmt)
            share = fmt_pct(count, self.profile.valid)
            gp = event.globalPosition().toPoint() if hasattr(event, "globalPosition") \
                else event.globalPos()
            QToolTip.showText(gp, f"{label}<br><b>{count:,}</b> records ({share})", self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.logical)
        super().mouseReleaseEvent(event)


# ============================================================================
# FILTER CELL
# ============================================================================

PLACEHOLDERS = {
    "contains": "contains", "eq": "value", "starts": "starts with",
    "ne": "value", "lt": "value", "le": "value", "gt": "value",
    "ge": "value", "between": "from", "in": "choose values",
    "null": "is NULL", "notnull": "is not NULL",
}


class FilterCell(QFrame):
    """Operator button + value input (+ value list / calendar button)."""

    changed = pyqtSignal(int, bool)        # logical, apply immediately

    def __init__(self, logical, name, kind, theme, metrics, parent=None):
        super().__init__(parent)
        self.setObjectName("gtsFilterCell")
        self.logical = logical
        self.name = name
        self.kind = kind
        self.theme = theme
        self.metrics = metrics
        self.op = FL.default_operator(kind)
        self.values = []
        self.values_provider = None        # callable(logical) -> [(raw, label, count)]
        self._error = ""
        self._bool_set = False             # boolean cells: a value was chosen

        lay = QHBoxLayout(self)
        lay.setContentsMargins(1, 0, 1, 0)
        lay.setSpacing(0)

        self.op_btn = QToolButton(self)
        self.op_btn.setObjectName("gtsOp")
        self.op_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.op_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.op_menu = QMenu(self.op_btn)
        group = QActionGroup(self.op_menu)
        self._op_actions = {}
        for op_id, op_label in FL.operators(kind):
            act = QAction(icons.text_glyph(FL.OP_SYMBOLS.get(op_id, ""),
                                           theme.accent_text),
                          op_label, self.op_menu)
            act.setIconVisibleInMenu(True)
            act.setCheckable(True)
            act.setData(op_id)
            group.addAction(act)
            self.op_menu.addAction(act)
            self._op_actions[op_id] = act
        self.op_menu.addSeparator()
        clear = self.op_menu.addAction(icons.native(icons.DESELECT), "Clear this filter")
        clear.triggered.connect(lambda: self.clear(emit=True))
        self.op_menu.triggered.connect(self._on_op_action)
        self.op_btn.setMenu(self.op_menu)
        lay.addWidget(self.op_btn)

        self.edit = QLineEdit(self)
        self.edit.setFrame(False)
        self.edit.setClearButtonEnabled(True)
        self.edit2 = QLineEdit(self)
        self.edit2.setFrame(False)
        self.edit2.setClearButtonEnabled(True)
        self.edit2.setPlaceholderText("to")
        self.dash = QLabel("–", self)
        self.dash.setObjectName("gtsDim")
        for e in (self.edit, self.edit2):
            e.textEdited.connect(lambda _t: self._emit(False))
            e.returnPressed.connect(lambda: self._emit(True))
            e.textChanged.connect(self._on_text_changed)
            e.editingFinished.connect(lambda e=e: e.setCursorPosition(0))
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.dash)
        lay.addWidget(self.edit2, 1)

        self.aux_btn = QToolButton(self)
        self.aux_btn.setObjectName("gtsOp")
        self.aux_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if kind in (F.DATE, F.DATETIME):
            self.aux_btn.setIcon(icons.glyph("calendar", theme.text_dim))
            self.aux_btn.setToolTip("Pick a date")
            self.aux_btn.clicked.connect(self._pick_date)
        else:
            self.aux_btn.setIcon(icons.glyph("chevron", theme.text_dim))
            self.aux_btn.setToolTip("Choose from the values in this column")
            self.aux_btn.clicked.connect(self._pick_values)
        self.aux_btn.setIconSize(QSize(12, 12))
        lay.addWidget(self.aux_btn)
        self._apply_op_ui()

    # ------------------------------------------------------------------
    def _on_op_action(self, action):
        op = action.data()
        if op is None:
            return
        self.set_op(op)
        if op == "in" and not self.values:
            self._pick_values()
            return
        self._emit(True)

    def set_op(self, op):
        if op != "in":
            self.values = []
            if self.op == "in":
                self.edit.clear()
        self.op = op
        self._apply_op_ui()

    def _apply_op_ui(self):
        op, kind = self.op, self.kind
        for op_id, act in self._op_actions.items():
            act.setChecked(op_id == op)
        symbol = FL.OP_SYMBOLS.get(op, "?")
        self.op_btn.setText(symbol)
        self.op_btn.setToolTip(f"Filter: {FL.operator_label(kind, op)}"
                               "\nClick to change the operator")
        no_value = op in FL.NO_VALUE_OPS
        between = op == "between"
        self.edit.setVisible(not no_value or kind != F.BOOL)
        self.edit.setEnabled(not no_value)
        self.edit.setReadOnly(op == "in")
        if kind == F.BOOL:
            self.edit.setVisible(False)
            labels = {"true": "true", "false": "false", "null": "NULL",
                      "notnull": "not NULL"}
            self.op_btn.setText(f"{symbol}  {labels.get(op, '')}" if self.is_active()
                                else "any")
            self.op_btn.setSizePolicy(self.edit.sizePolicy())
        if kind in (F.DATE, F.DATETIME):
            ph = "YYYY-MM-DD"
        elif kind == F.TIME:
            ph = "HH:MM:SS"
        else:
            ph = PLACEHOLDERS.get(op, "value")
        if between:
            ph = "from"
        self.edit.setPlaceholderText(PLACEHOLDERS.get(op) if no_value else ph)
        self.dash.setVisible(between)
        self.edit2.setVisible(between)
        if kind in (F.DATE, F.DATETIME):
            self.edit2.setPlaceholderText("to")
        self.aux_btn.setVisible(kind != F.BOOL and not no_value
                                and (op in ("in", "eq", "contains", "starts")
                                     or kind in (F.DATE, F.DATETIME)))
        if op == "in":
            self.edit.setText(self._values_summary())
        self._update_state()

    def _values_summary(self):
        if not self.values:
            return ""
        shown = ", ".join(F.display_value(v) for v in self.values[:3])
        more = f" +{len(self.values) - 3}" if len(self.values) > 3 else ""
        return shown + more

    def _on_text_changed(self, _text):
        self._update_state()

    def _emit(self, immediate):
        self._update_state()
        self.changed.emit(self.logical, immediate)

    def is_active(self):
        if self.kind == F.BOOL:
            return self._bool_set
        return self.column_filter().is_active()

    def column_filter(self):
        return FL.ColumnFilter(self.name, self.kind, self.op, self.edit.text(),
                               self.edit2.text(), self.values)

    def current_filter(self):
        """ColumnFilter when active, else None."""
        flt = self.column_filter()
        if self.kind == F.BOOL and not self._bool_set:
            return None
        return flt if flt.is_active() else None

    def set_filter(self, flt, emit=False):
        """Apply a ColumnFilter (or None to clear)."""
        self.blockSignals(True)
        if flt is None:
            self.op = FL.default_operator(self.kind)
            self.values = []
            self._bool_set = False
            self.edit.clear()
            self.edit2.clear()
        else:
            self.op = flt.op
            self.values = list(flt.values)
            self._bool_set = self.kind == F.BOOL
            if flt.op != "in":
                self.edit.setText(str(flt.value))
            self.edit2.setText(str(flt.value2))
        self._apply_op_ui()
        for e in (self.edit, self.edit2):     # show values from their start
            if not e.hasFocus():
                e.setCursorPosition(0)
        self.blockSignals(False)
        if emit:
            self._emit(True)

    def clear(self, emit=False):
        changed = self.current_filter() is not None
        self.set_filter(None)
        if emit and changed:
            self._emit(True)

    def set_error(self, message):
        self._error = message or ""
        self._update_state()

    def _update_state(self):
        if self._error:
            state = "error"
            self.setToolTip(f"<b>{self.name}</b><br><span style='color:"
                            f"{self.theme.danger}'>{self._error}</span><br>"
                            "The last valid filter is still applied.")
        elif self.current_filter() is not None:
            state = "active"
            self.setToolTip(f"<b>Active filter</b><br>"
                            f"{self.column_filter().describe()}")
        else:
            state = ""
            self.setToolTip(f"Filter {self.name}")
        if self.property("state") != state:
            self.setProperty("state", state)
            self.style().unpolish(self)
            self.style().polish(self)

    # ------------------------------------------------------------------
    def _popup_pos(self, _popup):
        return self.mapToGlobal(QPoint(0, self.height() + 2))

    def _pick_values(self):
        if self.values_provider is None:
            return
        entries = self.values_provider(self.logical)
        picker = ValuePicker(self.name, entries, self.values, self.theme,
                             self.metrics, self)
        picker.applied.connect(self._on_values_applied)
        picker.move(self._popup_pos(picker))
        picker.show()
        picker.search.setFocus()

    def _on_values_applied(self, values):
        if values:
            self.op = "in"
            self.values = values
        elif self.op == "in":
            self.op = FL.default_operator(self.kind)
            self.values = []
            self.edit.clear()
        self._apply_op_ui()
        self._emit(True)

    def _pick_date(self):
        target = self.edit2 if (self.op == "between" and self.edit2.hasFocus()) \
            else self.edit
        popup = CalendarPopup(target.text(), self)
        popup.picked.connect(lambda d, t=target: self._on_date(t, d))
        popup.move(self._popup_pos(popup))
        popup.show()

    def _on_date(self, target, date):
        target.setText(date.isoformat())
        self._emit(True)

    def set_bool(self, op):
        """Boolean cells: 'any' (cleared) or a no-value operator."""
        if op is None:
            self._bool_set = False
            self.op = FL.default_operator(self.kind)
        else:
            self._bool_set = True
            self.op = op
        self._apply_op_ui()
        self._emit(True)


# ============================================================================
# POPUPS
# ============================================================================

class ValuePicker(QFrame):
    """Checklist of the column's values ('is one of'), with record counts."""

    applied = pyqtSignal(list)

    def __init__(self, name, entries, current, theme, metrics, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("gtsPopup")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(300, 340)
        self.setStyleSheet(
            f"QFrame#gtsPopup {{ background: {theme.card}; border: 1px solid "
            f"{theme.border}; border-radius: 4px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        title = QLabel(f"<b>{name}</b> — choose values (matched with OR)")
        lay.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search values")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(metrics.control_h)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels(["Value", "Records"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        current_keys = {F.group_value(v) for v in current}
        null_colour = QColor(theme.data_null)
        for raw, text, count in entries:
            item = QTreeWidgetItem([text, "" if count is None else f"{count:,}"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked
                               if F.group_value(raw) in current_keys
                               else Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, raw)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
            if raw is None:
                f = item.font(0)
                f.setItalic(True)
                item.setFont(0, f)
                item.setForeground(0, null_colour)
            self.tree.addTopLevelItem(item)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemDoubleClicked.connect(lambda *_: self._apply())
        lay.addWidget(self.tree, 1)
        if len(entries) >= 5000:
            note = QLabel("Showing the first 5,000 values — search to narrow.")
            note.setObjectName("gtsDim")
            lay.addWidget(note)
        row = QHBoxLayout()
        row.setSpacing(metrics.gap)
        for text, fn in (("All", lambda: self._set_all(True)),
                         ("None", lambda: self._set_all(False))):
            b = W.button(metrics, text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        ok = W.primary_button(metrics, "Apply", theme)
        ok.setIcon(icons.native(icons.FILTER))
        ok.clicked.connect(self._apply)
        row.addWidget(ok)
        lay.addLayout(row)

    def _items(self):
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def _filter(self, text):
        text = text.lower()
        for item in self._items():
            item.setHidden(text not in item.text(0).lower())

    def _set_all(self, state):
        check = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        for item in self._items():
            if not item.isHidden():
                item.setCheckState(0, check)

    def _apply(self):
        values = [i.data(0, Qt.ItemDataRole.UserRole) for i in self._items()
                  if i.checkState(0) == Qt.CheckState.Checked]
        self.applied.emit(values)
        self.close()


class CalendarPopup(QFrame):
    picked = pyqtSignal(object)            # datetime.date

    def __init__(self, text, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        self.cal = QCalendarWidget()
        self.cal.setGridVisible(False)
        try:
            d = FL.parse_date(text)
            self.cal.setSelectedDate(QDate(d.year, d.month, d.day))
        except FL.FilterError:
            pass
        self.cal.clicked.connect(self._done)
        self.cal.activated.connect(self._done)
        lay.addWidget(self.cal)

    def _done(self, qdate):
        self.picked.emit(datetime.date(qdate.year(), qdate.month(), qdate.day()))
        self.close()


# ============================================================================
# HEADER
# ============================================================================

class ExplorerHeader(QHeaderView):
    """Horizontal header with profile, name and filter bands."""

    filterChanged = pyqtSignal(int, bool)
    profileClicked = pyqtSignal(int)
    layoutChanged = pyqtSignal()

    def __init__(self, theme, metrics, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.theme = theme
        self.metrics = metrics
        self.profile_h = ProfileCard.preferred_height(metrics)
        self.name_h = metrics.font_h + 12
        self.filter_h = metrics.compact_h + 6      # 24 px cells, 3 px margins
        self.profiles_visible = True
        self.cards = {}
        self.cells = {}
        self._name_press = False
        self.values_provider = None
        self.setSectionsClickable(True)
        self.setSectionsMovable(True)
        self.setHighlightSections(False)
        self.setSortIndicatorShown(True)
        self.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.setStretchLastSection(False)
        self.setMinimumSectionSize(96)
        self.setDefaultSectionSize(160)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignVCenter)
        self.sectionResized.connect(self._on_geometry)
        self.sectionMoved.connect(self._on_geometry)
        self.geometriesChanged.connect(self.relayout)
        self._panel = QColor(theme.panel)
        self._band = QColor(theme.header)
        self._line = QColor(theme.border)

    # ------------------------------------------------------------------
    def rebuild(self, fields, kinds):
        for w in list(self.cards.values()) + list(self.cells.values()):
            w.deleteLater()
        self.cards, self.cells = {}, {}
        vp = self.viewport()
        for logical, (fld, kind) in enumerate(zip(fields, kinds)):
            card = ProfileCard(logical, fld.name(), kind, self.theme,
                               self.metrics, vp)
            card.clicked.connect(self.profileClicked.emit)
            cell = FilterCell(logical, fld.name(), kind, self.theme,
                              self.metrics, vp)
            cell.changed.connect(self.filterChanged.emit)
            cell.values_provider = self._provide_values
            if kind == F.BOOL:
                self._make_bool_menu(cell)
            self.cards[logical] = card
            self.cells[logical] = cell
        self.relayout()

    def _provide_values(self, logical):
        return self.values_provider(logical) if self.values_provider else []

    def _make_bool_menu(self, cell):
        menu = QMenu(cell.op_btn)
        colour = self.theme.accent_text
        for symbol, text, op in (("", "any", None), ("✓", "true", "true"),
                                 ("✗", "false", "false"), ("∅", "is NULL", "null"),
                                 ("≠∅", "is not NULL", "notnull")):
            act = menu.addAction(icons.text_glyph(symbol, colour), text)
            act.setIconVisibleInMenu(True)
            act.triggered.connect(lambda _c=False, o=op, c=cell: c.set_bool(o))
        cell.op_btn.setMenu(menu)
        cell.op_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        cell.layout().setStretch(0, 1)

    def set_profiles_visible(self, visible):
        self.profiles_visible = visible
        self.updateGeometry()
        self.relayout()
        view = self.parent()
        if view is not None and hasattr(view, "updateGeometries"):
            view.updateGeometries()
        self.viewport().update()

    def total_height(self):
        return (self.profile_h if self.profiles_visible else 0) \
            + self.name_h + self.filter_h

    def sizeHint(self):
        size = super().sizeHint()
        return QSize(size.width(), self.total_height())

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        return QSize(size.width(), self.total_height())

    def name_top(self):
        return self.profile_h if self.profiles_visible else 0

    def visible_logicals(self):
        """Logical indices of shown columns, left to right."""
        out = []
        for visual in range(self.count()):
            logical = self.logicalIndex(visual)
            if not self.isSectionHidden(logical):
                out.append(logical)
        return out

    def logicals_in_view(self):
        width = self.viewport().width()
        out = []
        for logical in self.visible_logicals():
            x = self.sectionViewportPosition(logical)
            if x + self.sectionSize(logical) > 0 and x < width:
                out.append(logical)
        return out

    # ------------------------------------------------------------------
    def paintSection(self, painter, rect, logical):
        top = self.name_top()
        painter.save()
        if top:
            painter.fillRect(QRect(rect.left(), rect.top(), rect.width(), top),
                             self._panel)
        band = QRect(rect.left(), rect.top() + top + self.name_h, rect.width(),
                     self.filter_h)
        painter.fillRect(band, self._band)
        painter.setPen(QPen(self._line, 1))
        painter.drawLine(band.bottomLeft(), band.bottomRight())
        painter.drawLine(band.topRight(), band.bottomRight())
        painter.restore()
        name_rect = QRect(rect.left(), rect.top() + top, rect.width(), self.name_h)
        super().paintSection(painter, name_rect, logical)
        W.paint_sort_arrow(self, painter, name_rect, logical, self.theme.text_dim)

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), self._panel)
        painter.end()
        super().paintEvent(event)

    # mouse: only the name band sorts / moves / resizes -----------------
    def _pos(self, event):
        return event.position().toPoint() if hasattr(event, "position") \
            else event.pos()

    def _in_name_band(self, event):
        y = self._pos(event).y()
        top = self.name_top()
        return top <= y < top + self.name_h

    def mousePressEvent(self, event):
        if self._in_name_band(event):
            self._name_press = True
            super().mousePressEvent(event)
        else:
            event.accept()

    def mouseMoveEvent(self, event):
        if self._name_press or self._in_name_band(event):
            super().mouseMoveEvent(event)
        else:
            self.unsetCursor()
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._name_press:
            self._name_press = False
            super().mouseReleaseEvent(event)
        else:
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if self._in_name_band(event):
            super().mouseDoubleClickEvent(event)
        else:
            event.accept()

    # child placement ----------------------------------------------------
    def _on_geometry(self, *_args):
        self.relayout()
        self.layoutChanged.emit()

    def relayout(self):
        top = self.name_top()
        filt_y = top + self.name_h + 3
        width = self.viewport().width()
        for logical, card in self.cards.items():
            cell = self.cells[logical]
            if self.isSectionHidden(logical):
                card.hide()
                cell.hide()
                continue
            x = self.sectionViewportPosition(logical)
            w = self.sectionSize(logical)
            on_screen = x + w > 0 and x < width
            if self.profiles_visible and on_screen:
                card.setGeometry(x + 3, 3, max(10, w - 6), self.profile_h - 6)
                card.show()
            else:
                card.hide()
            if on_screen:
                cell.setGeometry(x + 3, filt_y, max(10, w - 6), self.filter_h - 6)
                cell.show()
            else:
                cell.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout()

    def showEvent(self, event):
        super().showEvent(event)
        self.relayout()

    def updateGeometries(self):
        super().updateGeometries()
        self.relayout()


# ============================================================================
# EXPANDED STATISTICS
# ============================================================================

class ProfileDetails(QFrame):
    """Full statistics of one column (opened by clicking its profile)."""

    filterRequested = pyqtSignal(int, object)      # logical, raw value

    def __init__(self, logical, name, kind, profile, scope_text, theme,
                 metrics, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("gtsPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.logical = logical
        self.setMinimumWidth(360)
        self.setStyleSheet(
            f"QFrame#gtsPopup {{ background: {theme.card}; border: 1px solid "
            f"{theme.border}; border-radius: 4px; }}"
            f"QLabel#gtsDim {{ color: {theme.text_dim}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.field(kind, theme).pixmap(QSize(16, 16)))
        head.addWidget(ic)
        title = QLabel(f"<b>{name}</b>")
        head.addWidget(title, 1)
        kind_lbl = QLabel(F.KIND_LABELS.get(kind, ""))
        kind_lbl.setObjectName("gtsDim")
        head.addWidget(kind_lbl)
        lay.addLayout(head)
        scope = QLabel(scope_text)
        scope.setObjectName("gtsDim")
        scope.setWordWrap(True)
        lay.addWidget(scope)

        p = profile
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(3)
        rows = [("Records in scope", f"{p.count:,}"),
                ("Non-NULL", f"{p.non_null:,}  ({fmt_pct(p.non_null, p.count)})"),
                ("Missing (NULL)", f"{p.missing:,}  ({fmt_pct(p.missing, p.count)})"),
                ("Distinct (non-NULL)",
                 f"{p.distinct:,}  ({fmt_pct(p.distinct, p.count)})")]
        if kind in F.NUMERIC:
            rows += [("Valid numbers", f"{p.valid:,}")]
            if p.valid:
                rows += [("Sum", F.format_number(p.total)),
                         ("Mean", F.format_number(p.mean)),
                         ("Median", F.format_number(p.median)),
                         ("Minimum", F.format_number(p.minimum)),
                         ("Maximum", F.format_number(p.maximum)),
                         ("Std dev (population)", F.format_number(p.std))]
            if p.invalid:
                rows += [("Invalid (NaN / infinite)", f"{p.invalid:,}")]
        elif kind in F.TEMPORAL:
            if p.valid:
                rows += [("Earliest", fmt_value(p.earliest, kind)),
                         ("Latest", fmt_value(p.latest, kind))]
            if p.invalid:
                rows += [("Invalid", f"{p.invalid:,}")]
        elif kind == F.BOOL:
            rows += [("true", f"{p.true_count:,}  ({fmt_pct(p.true_count, p.count)})"),
                     ("false", f"{p.false_count:,}  ({fmt_pct(p.false_count, p.count)})"),
                     ("Denominator", f"{p.count:,} records in scope")]
        else:
            rows += [("Empty strings", f"{p.blank:,}"),
                     ("Whitespace only", f"{p.whitespace:,}")]
        for r, (k, v) in enumerate(rows):
            kl = QLabel(k)
            kl.setObjectName("gtsDim")
            vl = QLabel(v)
            vl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(kl, r, 0)
            grid.addWidget(vl, r, 1, Qt.AlignmentFlag.AlignRight)
        lay.addLayout(grid)

        if kind in F.NUMERIC or kind in F.TEMPORAL:
            if p.bins and len(p.bins) > 1:
                lay.addWidget(self._bins_table(p, kind, theme))
        elif kind not in (F.BOOL,) and p.frequencies:
            lay.addWidget(self._freq_table(p, theme, metrics))

    def _bins_table(self, p, kind, theme):
        tree = QTreeWidget()
        tree.setRootIsDecorated(False)
        tree.setHeaderLabels(["Bin", "Records", "Share"])
        tree.setMaximumHeight(220)
        fmt = (lambda v: F.key_to_text(v, kind)) if kind in F.TEMPORAL \
            else F.format_number
        for i, (lo, hi, count) in enumerate(p.bins):
            label = S.bin_label(lo, hi, i == len(p.bins) - 1, p.integer_bins, fmt)
            item = QTreeWidgetItem([label, f"{count:,}", fmt_pct(count, p.valid)])
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight)
            tree.addTopLevelItem(item)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        return tree

    def _freq_table(self, p, theme, metrics):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        search = QLineEdit()
        search.setPlaceholderText(f"Search {len(p.frequencies):,} values")
        search.setClearButtonEnabled(True)
        search.setFixedHeight(metrics.control_h)
        lay.addWidget(search)
        tree = QTreeWidget()
        tree.setRootIsDecorated(False)
        tree.setUniformRowHeights(True)
        tree.setHeaderLabels(["Value", "Records", "% of non-NULL"])
        tree.setMinimumHeight(220)
        dim = QColor(theme.text_dim)
        for text, count in p.frequencies:
            label = text if text != "" else "(empty string)"
            if text and not text.strip():
                label = f"(whitespace ×{len(text)})"
            item = QTreeWidgetItem([label, f"{count:,}", fmt_pct(count, p.non_null)])
            item.setData(0, Qt.ItemDataRole.UserRole, text)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight)
            if label != text:
                item.setForeground(0, dim)
            tree.addTopLevelItem(item)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree.setToolTip("Double-click a value to filter the column to it")
        tree.itemDoubleClicked.connect(self._on_value)

        def apply_search(text, t=tree):
            text = text.lower()
            for i in range(t.topLevelItemCount()):
                it = t.topLevelItem(i)
                it.setHidden(text not in it.text(0).lower())
        search.textChanged.connect(apply_search)
        lay.addWidget(tree)
        if p.frequencies_truncated:
            note = QLabel(f"Showing the {len(p.frequencies):,} most common of "
                          f"{p.distinct:,} distinct values.")
            note.setObjectName("gtsDim")
            lay.addWidget(note)
        hint = QLabel("Double-click a value to filter the column to it.")
        hint.setObjectName("gtsDim")
        lay.addWidget(hint)
        return box

    def _on_value(self, item, _col):
        self.filterRequested.emit(self.logical, item.data(0, Qt.ItemDataRole.UserRole))
        self.close()
