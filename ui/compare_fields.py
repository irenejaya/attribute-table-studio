# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Geometry and Table Compare: the changed-fields summary.

A click on the **Changed** chip of the Review row opens this popup (in the
look of the Attribute Explorer's statistics popup): one line per field that
changed, most changes first, with the number of modified records it
changed in, their share and a small bar.  Hovering a line shows how many of
its changes are accepted, rejected or still pending; a click on a line
shows only the records where that field changed (and scrolls the table to
its column), **All** shows every change again.
"""

from qgis.PyQt.QtCore import QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from . import widgets as W

NAME_ROLE = Qt.ItemDataRole.UserRole
SHARE_ROLE = Qt.ItemDataRole.UserRole + 1
MAX_ROWS = 14                    # taller lists scroll


def summary_rows(counts, decisions):
    """[(field, changed records, accepted, rejected)], most changes first.

    *counts* maps a field to the records it changed in; *decisions* maps
    (record id, field) to "Accepted" / "Rejected"."""
    accepted, rejected = {}, {}
    for (_fid, name), decision in decisions.items():
        if decision == "Accepted":
            accepted[name] = accepted.get(name, 0) + 1
        elif decision == "Rejected":
            rejected[name] = rejected.get(name, 0) + 1
    rows = [(name, n, accepted.get(name, 0), rejected.get(name, 0))
            for name, n in counts.items() if n]
    rows.sort(key=lambda r: (-r[1], r[0].lower()))
    return rows


class _ShareDelegate(QStyledItemDelegate):
    """The Share column: a small bar and the percentage."""

    def __init__(self, colour, text_colour, parent=None):
        super().__init__(parent)
        self.colour = QColor(colour)
        self.text_colour = QColor(text_colour)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        share = index.data(SHARE_ROLE)
        if share is None:
            return
        r = QRectF(option.rect).adjusted(4, 0, -6, 0)
        text = f"{100.0 * share:.0f}%"
        tw = option.fontMetrics.horizontalAdvance("100%") + 6
        bar_w = max(r.width() - tw, 10.0)
        h = min(8.0, r.height() - 6)
        y = r.center().y() - h / 2
        painter.save()
        track = QColor(self.colour)
        track.setAlpha(40)
        painter.fillRect(QRectF(r.left(), y, bar_w, h), track)
        painter.fillRect(QRectF(r.left(), y, max(bar_w * share, 1.5), h), self.colour)
        painter.setPen(self.text_colour)
        painter.drawText(QRectF(r.left() + bar_w, r.top(), tw, r.height()),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(max(size.width(), 150), size.height())


class ChangedFieldsPopup(QFrame):
    """Changed fields of a comparison; picks one to show only its changes."""

    fieldPicked = pyqtSignal(object)            # field name, or None for All

    def __init__(self, rows, modified, active, theme, metrics, icon_of=None,
                 parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("gtsPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.theme = theme
        self.setMinimumWidth(420)
        self.setStyleSheet(
            f"QFrame#gtsPopup {{ background: {theme.card}; border: 1px solid "
            f"{theme.border}; border-radius: 4px; }}"
            f"QLabel#gtsDim {{ color: {theme.text_dim}; }}"
            f"QTreeWidget#gtsChangedFields {{ background: transparent; border: none; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Changed fields</b>"), 1)
        count = QLabel(f"{modified:,} modified record{'s' if modified != 1 else ''}")
        count.setObjectName("gtsDim")
        head.addWidget(count)
        lay.addLayout(head)

        self.tree = QTreeWidget()
        self.tree.setObjectName("gtsChangedFields")
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels(["Field", "Records", "Share of modified"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setMouseTracking(True)
        self.tree.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tree.setItemDelegateForColumn(
            2, _ShareDelegate(theme.accent_of("ChangedField"), theme.text_dim, self.tree))
        total = float(modified or 1)
        for name, n, acc, rej in rows:
            item = QTreeWidgetItem([name, f"{n:,}", ""])
            item.setData(0, NAME_ROLE, name)
            item.setData(2, SHARE_ROLE, min(n / total, 1.0))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
            if icon_of is not None:
                icon = icon_of(name)
                if icon is not None:
                    item.setIcon(0, icon)
            pending = max(n - acc - rej, 0)
            tip = (f"{name}\nChanged in {n:,} of {modified:,} modified records "
                   f"({100.0 * n / total:.0f}%)\n"
                   f"Accepted {acc:,}  ·  Rejected {rej:,}  ·  Pending {pending:,}\n"
                   "Click to show only the records where it changed")
            for col in range(3):
                item.setToolTip(col, tip)
            self.tree.addTopLevelItem(item)
            if name == active:
                item.setSelected(True)
                self.tree.setCurrentItem(item)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(2, 170)
        row_h = self.tree.sizeHintForRow(0) if rows else 0
        if row_h <= 0:
            row_h = metrics.font_h + 6
        shown = min(max(len(rows), 1), MAX_ROWS)
        self.tree.setFixedHeight(int(hdr.sizeHint().height() + row_h * shown
                                     + 2 * self.tree.frameWidth() + 2))
        self.tree.itemClicked.connect(self._on_item)
        if rows:
            lay.addWidget(self.tree)
        else:
            self.tree.hide()
            empty = QLabel("No field changed: no record is Modified.")
            empty.setObjectName("gtsDim")
            lay.addWidget(empty)

        foot = QHBoxLayout()
        foot.setSpacing(metrics.gap)
        hint = QLabel("Click a field to show only its changes")
        hint.setObjectName("gtsDim")
        foot.addWidget(hint, 1)
        self.all_btn = W.button(metrics, "All", None,
                                "Show every change again (all fields)")
        self.all_btn.setFixedHeight(metrics.compact_h)
        self.all_btn.setEnabled(active is not None)
        self.all_btn.clicked.connect(self._on_all)
        foot.addWidget(self.all_btn)
        lay.addLayout(foot)
        self.adjustSize()

    def _on_item(self, item, _col):
        self.fieldPicked.emit(item.data(0, NAME_ROLE))
        self.close()

    def _on_all(self):
        self.fieldPicked.emit(None)
        self.close()

    def show_below(self, widget):
        """Open under *widget*, its right edges lined up, inside the screen."""
        self.adjustSize()
        below = widget.mapToGlobal(widget.rect().bottomRight())
        x = below.x() - self.width()
        y = below.y() + 4
        screen = widget.screen().availableGeometry() if hasattr(widget, "screen") \
            and widget.screen() is not None else None
        if screen is not None:
            x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
            if y + self.height() > screen.bottom() - 4:
                y = widget.mapToGlobal(widget.rect().topLeft()).y() - self.height() - 4
        self.move(x, y)
        self.show()
