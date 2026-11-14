# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Attribute Explorer editing rows.

- :class:`EditBar` is the second permanent toolbar row: the editing
  commands right aligned (ending at the Dock button's right edge), and on
  the left the expression filter bar and chips while they are in use.  The
  scope line and the edit status live in the status bar.  In a narrow
  window the lower-priority groups move into the More menu, so the row
  never wraps.
- :class:`ExpressionBar` is the Advanced Filter (Expression), editable in
  place as in QGIS's attribute table.
- :class:`QuickUpdateBar` is the optional third row, shown only while
  editing AND its separate Quick update toggle is on; it works like the
  native attribute table's field calculation bar.
"""

from qgis.PyQt.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QSizePolicy,
    QVBoxLayout, QWidget,
)
from qgis.core import QgsFieldProxyModel, QgsStoredExpression
from qgis.gui import QgsExpressionLineEdit, QgsFieldComboBox, QgsFieldExpressionWidget

from . import icons
from . import widgets as W
from .explorer_ops import geometry_calculator


class EditBar(QWidget):
    """The editing commands (right) and, on the left, the expression filter
    bar and chips while they are in use (filled by the panel)."""

    LEFT = 40           # kept free on the left before groups move into More
    LEFT_MIN = 40

    def __init__(self, metrics, theme, parent=None):
        super().__init__(parent)
        m = metrics
        self.metrics = m
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(m.gap)
        self.info = QWidget()
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info.setMinimumWidth(120)
        self.info_layout = QHBoxLayout(self.info)
        self.info_layout.setContentsMargins(0, 0, 0, 0)
        self.info_layout.setSpacing(m.gap)
        self._chips = []
        lay.addWidget(self.info, 1)

        sq = W.square_button
        self.edit_btn = W.button(m, "Edit", icons.native(icons.TOGGLE_EDIT),
                                 "Start editing this layer", checkable=True)
        self.save_btn = W.button(m, "Save edits", icons.native(icons.SAVE_EDITS),
                                 "Save all unsaved changes of the layer")
        self.discard_btn = sq(m, icons.native(icons.ROLLBACK),
                              "Discard all unsaved edits of the layer")
        self.undo_btn = sq(m, icons.native(icons.UNDO), "Undo")
        self.redo_btn = sq(m, icons.native(icons.REDO), "Redo")
        self.add_menu = QMenu(self)
        self.add_table_action = self.add_menu.addAction(
            icons.native(icons.NEW_ROW), "Add Record in the Table")
        self.add_form_action = self.add_menu.addAction(
            icons.native(icons.FORM_VIEW), "Add Record with a Form…")
        self.add_btn = W.split_button(m, icons.native(icons.NEW_ROW),
                                      "Add a record", self.add_menu)
        self.delete_btn = sq(m, icons.native(icons.DELETE_SELECTED),
                             "Delete the selected features…")
        self.cut_btn = sq(m, icons.native(icons.CUT), "Cut the selected features")
        self.copy_btn = sq(m, icons.native(icons.COPY), "Copy the selected features")
        self.paste_btn = sq(m, icons.native(icons.PASTE), "Paste features")
        self.calc_btn = sq(m, icons.native(icons.CALCULATE), "Field calculator…")
        self.quick_btn = sq(m, icons.native(icons.QUICK_UPDATE),
                            "Show or hide the Quick update row",
                            checkable=True)
        self.multi_btn = sq(m, icons.native(icons.MULTI_EDIT),
                            "Multi edit the selected features…")
        self.new_field_btn = sq(m, icons.native(icons.NEW_FIELD), "New field…")
        self.delete_field_btn = sq(m, icons.native(icons.DELETE_FIELD), "Delete field…")
        self.join_btn = sq(m, icons.native(icons.JOIN),
                           "Join table…\nAdd the fields of another layer or table, "
                           "matched by a field (a QGIS layer join); no editing needed")
        self.reload_btn = sq(m, icons.native(icons.RELOAD), "Reload the layer")
        self.more_menu = QMenu(self)
        self.more_menu.setToolTipsVisible(True)
        # drawn in the text colour: QGIS's hamburger icon is too faint here
        self.more_btn = sq(m, icons.glyph("menu", theme.text), "More")
        self.more_btn.setMenu(self.more_menu)
        self.more_btn.setPopupMode(W.QToolButton.ToolButtonPopupMode.InstantPopup)
        # no arrow, so no room kept for one: the icon stays centred
        self.more_btn.setStyleSheet("QToolButton { padding-right: 0px; }"
                                    "QToolButton::menu-indicator { image: none; width: 0px; }")

        # groups, left to right; the first is never moved into More
        self.groups = [
            [self.edit_btn, self.save_btn, self.discard_btn],
            [self.undo_btn, self.redo_btn],
            [self.add_btn, self.delete_btn],
            [self.cut_btn, self.copy_btn, self.paste_btn],
            [self.calc_btn, self.quick_btn, self.multi_btn],
            [self.new_field_btn, self.delete_field_btn, self.join_btn],
            [self.reload_btn],
        ]
        # groups that leave first when the row gets narrow
        self.overflow_order = [6, 5, 4, 3, 2, 1]
        self._seps = []
        for i, group in enumerate(self.groups):
            if i:
                sep = W.vseparator(m)
                self._seps.append(sep)
                lay.addWidget(sep)
            for btn in group:
                lay.addWidget(btn)
        sep = W.vseparator(m)
        self._seps.append(sep)
        lay.addWidget(sep)
        lay.addWidget(self.more_btn)
        self.hidden_groups = set()

    def add_info(self, widget, grow=False):
        """Put *widget* on the left of the row: the expression bar (*grow*:
        it takes the free width) or a chip.  While shown, each keeps its
        width there and the commands move into More instead."""
        self.info_layout.addWidget(widget, 1 if grow else 0)
        self._chips.append(widget)
        widget.installEventFilter(self)

    def end_info(self):
        """Keep the chips on the left when nothing grows."""
        self.info_layout.addStretch(0)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            QTimer.singleShot(0, self._apply_overflow)
        return False

    def _left(self):
        gap = self.metrics.gap
        return self.LEFT + sum(w.sizeHint().width() + gap for w in self._chips
                               if w.isVisibleTo(self))

    # ------------------------------------------------------------------
    def _group_width(self, i):
        gap = self.metrics.gap
        return sum(b.sizeHint().width() for b in self.groups[i]) \
            + gap * len(self.groups[i]) + 9 + gap

    def sizeHint(self):
        width = self._left() + sum(self._group_width(i) for i in range(len(self.groups))) \
            + self.metrics.control_h
        return QSize(width, self.metrics.control_h)

    def minimumSizeHint(self):
        return QSize(self.LEFT_MIN + self._group_width(0) + self.metrics.control_h + 20,
                     self.metrics.control_h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply_overflow)

    def _apply_overflow(self):
        """Move the lowest-priority groups into More until the row fits."""
        available = self.width() - self._left() - self.metrics.control_h - 12
        hidden = set()
        needed = sum(self._group_width(i) for i in range(len(self.groups)))
        for i in self.overflow_order:
            if needed <= available:
                break
            hidden.add(i)
            needed -= self._group_width(i)
        if hidden == self.hidden_groups:
            return
        self.hidden_groups = hidden
        for i, group in enumerate(self.groups):
            for btn in group:
                btn.setVisible(i not in hidden)
            if i:
                self._seps[i - 1].setVisible(i not in hidden)

    def overflow_buttons(self):
        """Buttons currently moved into More (in row order)."""
        return [b for i, g in enumerate(self.groups) if i in self.hidden_groups
                for b in g]


class ExpressionBar(QFrame):
    """The Advanced Filter (Expression) in the edit row, editable as in QGIS's
    attribute table: QGIS's expression box (… opens the Expression Builder,
    an invalid expression says why), Apply or Enter, the layer's stored
    filter expressions (☆) and × to remove the filter.  Escape puts the
    applied expression back."""

    applyRequested = pyqtSignal(str)
    closeRequested = pyqtSignal()
    manageRequested = pyqtSignal()

    WIDTH = 440         # room the row keeps for it (commands move into More)
    MIN_WIDTH = 260

    def __init__(self, metrics, theme, parent=None):
        super().__init__(parent)
        m = metrics
        self.metrics = m
        self.layer = None
        self.applied = ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(m.gap)
        icon = QLabel()
        icon.setPixmap(icons.native(icons.ADVANCED_FILTER).pixmap(m.icon, m.icon))
        icon.setToolTip("Advanced Filter (Expression)")
        lay.addWidget(icon)
        self.box = QgsExpressionLineEdit()
        self.box.setExpressionDialogTitle("Advanced Filter (Expression)")
        self.box.setToolTip("Filter expression: edit it and press Enter or Apply "
                            "(Escape puts the applied one back; … opens the "
                            "Expression Builder)")
        lay.addWidget(self.box, 1)
        self.line = self.box.findChild(QLineEdit)
        self.line.setPlaceholderText("Filter expression, e.g. \"AREA_M2\" > 500")
        self.line.returnPressed.connect(self.apply)
        self.line.installEventFilter(self)
        self.apply_btn = W.button(m, "Apply", None, "")
        self.apply_btn.setObjectName("gtsExprApply")
        self.apply_btn.clicked.connect(self.apply)
        lay.addWidget(self.apply_btn)
        self.store_menu = QMenu(self)
        self.store_menu.aboutToShow.connect(self._fill_store_menu)
        self.store_btn = W.menu_square(m, icons.native(icons.STORED_FILTER_OFF),
                                       "Stored filter expressions", self.store_menu)
        lay.addWidget(self.store_btn)
        self.close_btn = W.square_button(m, icons.glyph("cross", theme.text_dim),
                                         "Remove the expression filter")
        self.close_btn.clicked.connect(self.closeRequested.emit)
        lay.addWidget(self.close_btn)
        self.setStyleSheet(
            f"QToolButton#gtsExprApply[pending=\"true\"] {{ background: {theme.accent};"
            f" color: #FFFFFF; border: 1px solid {theme.accent}; }}")
        self.box.expressionChanged.connect(self._update)
        self._update()

    def sizeHint(self):
        return QSize(self.WIDTH, self.metrics.control_h)

    def minimumSizeHint(self):
        return QSize(self.MIN_WIDTH, self.metrics.control_h)

    def set_layer(self, layer):
        self.layer = layer
        self.box.setLayer(layer)

    def set_applied(self, expression):
        """The expression filter in force (shown in the box)."""
        self.applied = expression or ""
        self.box.setExpression(self.applied)
        if not self.line.hasFocus():
            self.line.setCursorPosition(0)        # show it from its start
        self._update()

    def text(self):
        return self.box.expression().strip()

    def focus(self):
        self.line.setFocus()
        self.line.selectAll()

    def _valid(self):
        text = self.text()
        if not text:
            return True, ""
        ok, message = self.box.isValidExpression()
        return ok, message.strip()

    def _update(self, *_args):
        ok, message = self._valid()
        pending = self.text() != self.applied
        self.apply_btn.setEnabled(pending and ok)
        self.apply_btn.setToolTip(
            f"The expression is not valid: {message}" if not ok else
            "Apply the expression (Enter)" if pending else "The expression is applied")
        if bool(self.apply_btn.property("pending")) != (pending and ok):
            self.apply_btn.setProperty("pending", pending and ok)
            self.apply_btn.style().unpolish(self.apply_btn)
            self.apply_btn.style().polish(self.apply_btn)
        self.line.setToolTip(
            f"The expression is not valid: {message}" if not ok else
            f"{self.text()}\n\nEnter applies it, Escape puts the applied one back; "
            "… opens the Expression Builder" if self.text() else "")
        stored = self._stored_name(self.applied) if self.applied else None
        self.store_btn.setIcon(icons.native(icons.STORED_FILTER if stored
                                            else icons.STORED_FILTER_OFF))
        self.store_btn.setToolTip(f"Stored filter expression: {stored}" if stored
                                  else "Stored filter expressions")

    def apply(self):
        if self.apply_btn.isEnabled():
            self.applyRequested.emit(self.text())

    def eventFilter(self, obj, event):
        if obj is not self.line:
            return False
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape \
                and self.text() != self.applied:
            self.set_applied(self.applied)
            return True
        if event.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(0, lambda: self.line.setCursorPosition(0))
        return False

    # stored filter expressions (the layer's own, shared with QGIS) -------
    def _stored(self):
        if self.layer is None:
            return []
        return self.layer.storedExpressionManager().storedExpressions(
            QgsStoredExpression.Category.FilterExpression)

    def _stored_name(self, expression):
        return next((se.name for se in self._stored()
                     if se.expression.strip() == expression.strip()), None)

    def _fill_store_menu(self):
        menu = self.store_menu
        menu.clear()
        text = self.text()
        save = menu.addAction(icons.native(icons.SAVE), "Save Expression As…")
        save.setEnabled(bool(text) and self._valid()[0] and not self._stored_name(text))
        save.triggered.connect(self._save_as)
        stored = self._stored()
        if stored:
            menu.addSeparator()
            for se in stored:
                act = menu.addAction(se.name)
                act.setToolTip(se.expression)
                act.triggered.connect(lambda _c=False, e=se.expression: self._load(e))
        menu.addSeparator()
        menu.addAction(icons.native(icons.STORED_FILTER), "Manage Stored Expressions…"
                       ).triggered.connect(self.manageRequested.emit)

    def _save_as(self):
        text = self.text()
        name, ok = QInputDialog.getText(self, "Save Expression As", "Name",
                                        text=text[:40])
        if ok and name.strip():
            self.layer.storedExpressionManager().addStoredExpression(
                name.strip(), text, QgsStoredExpression.Category.FilterExpression)
            self._update()

    def _load(self, expression):
        self.box.setExpression(expression)
        self._update()
        self.apply()


class QuickUpdateBar(QFrame):
    """Optional row while editing - the native attribute table's field
    calculation bar: a field (read-only fields hidden), an expression, then
    "Update All" ("Update Filtered" while the table is filtered) and
    "Update Selected".  Preview shows the first values; it never writes."""

    previewRequested = pyqtSignal()
    updateRequested = pyqtSignal(str)        # "all", "filtered" or "selected"
    closeRequested = pyqtSignal()

    def __init__(self, metrics, theme, parent=None):
        super().__init__(parent)
        m = metrics
        self.metrics = m
        self.filtered = False
        self.setObjectName("gtsBand")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 6, 6)
        outer.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(m.gap)
        title = QLabel("Quick update")
        title.setObjectName("gtsQuickTitle")
        row.addWidget(title)
        self.field_combo = QgsFieldComboBox()
        self.field_combo.setFixedHeight(m.control_h)
        self.field_combo.setMinimumWidth(170)
        self.field_combo.setToolTip("Field to update")
        proxy = getattr(QgsFieldProxyModel, "Filter", QgsFieldProxyModel)
        self.field_combo.setFilters(proxy.AllTypes | proxy.HideReadOnly)
        row.addWidget(self.field_combo)
        row.addWidget(W.label("Expression", "dim"))
        self.expression = QgsFieldExpressionWidget()
        self.expression.setFixedHeight(m.control_h)
        self.expression.setToolTip("QGIS expression giving the new value")
        row.addWidget(self.expression, 1)
        self.preview_btn = W.button(m, "Preview", icons.native(icons.QUICK_UPDATE),
                                    "Show or hide the first values; nothing is written",
                                    checkable=True)
        row.addWidget(self.preview_btn)
        self.run_btn = W.button(m, "Update All")
        self.selected_btn = W.button(m, "Update Selected")
        for btn in (self.run_btn, self.selected_btn):
            btn.setMinimumWidth(m.fm.horizontalAdvance("Update Filtered") + 28)
            row.addWidget(btn)
        self.close_btn = W.square_button(m, icons.glyph("cross", theme.text_dim),
                                         "Close the Quick update row (the draft is kept)")
        row.addWidget(self.close_btn)
        outer.addLayout(row)

        self.preview = QFrame()
        self.preview.setObjectName("gtsSurface")
        pv = QHBoxLayout(self.preview)
        pv.setContentsMargins(10, 6, 8, 6)
        pv.setSpacing(m.group_gap)
        left = QVBoxLayout()
        left.setSpacing(2)
        self.preview_title = QLabel("")
        self.preview_title.setObjectName("gtsQuickTitle")
        self.preview_lines = QLabel("")
        self.preview_lines.setTextFormat(Qt.TextFormat.RichText)
        left.addWidget(self.preview_title)
        left.addWidget(self.preview_lines)
        pv.addLayout(left, 1)
        self.preview_note = QLabel("")
        self.preview_note.setObjectName("gtsDim")
        pv.addWidget(self.preview_note, 0, Qt.AlignmentFlag.AlignBottom)
        self.preview.setVisible(False)
        outer.addWidget(self.preview)

        self.preview_btn.clicked.connect(self.previewRequested.emit)
        self.run_btn.clicked.connect(
            lambda: self.updateRequested.emit("filtered" if self.filtered else "all"))
        self.selected_btn.clicked.connect(lambda: self.updateRequested.emit("selected"))
        self.close_btn.clicked.connect(self.closeRequested.emit)
        self.expression.fieldChanged[str, bool].connect(lambda *_: self._sync())
        for sig in (self.field_combo.fieldChanged, self.expression.fieldChanged):
            sig.connect(lambda *_: self.hide_preview())
        self._counts = (0, 0)
        self._sync()

    def hide_preview(self):
        self.preview.setVisible(False)
        self.preview_btn.setChecked(False)

    def set_layer(self, layer):
        self.field_combo.setLayer(layer)
        self.expression.setLayer(layer)
        if layer is not None:     # its builder previews measures like QGIS
            self.expression.setGeomCalculator(geometry_calculator(layer))
        self.hide_preview()

    def set_state(self, filtered, n_main, n_selected):
        """*filtered*: the table shows a filtered list (QGIS: any filter
        mode but "Show All"); *n_main*: records Update All / Filtered
        calculates; *n_selected*: selected features of the layer."""
        self.filtered = bool(filtered)
        self._counts = (n_main, n_selected)
        W.set_button_text(self.run_btn, "Update Filtered" if filtered else "Update All")
        self._sync()

    def _sync(self):
        n_main, n_selected = self._counts
        valid = self.expression.isValidExpression()
        what = "filtered record" if self.filtered else "record"
        self.run_btn.setEnabled(valid and n_main > 0)
        self.run_btn.setToolTip(
            f"Calculate the field for the {n_main:,} {what}{'s' if n_main != 1 else ''}"
            + (" shown in the table" if self.filtered else " of the layer")
            + " (one undo step)" + ("" if valid else "\nEnter a valid expression first"))
        self.selected_btn.setEnabled(n_selected > 0)
        self.selected_btn.setToolTip(
            f"Calculate the field for the {n_selected:,} selected feature"
            f"{'s' if n_selected != 1 else ''} (shown or not; one undo step)"
            if n_selected else "Select features first")
