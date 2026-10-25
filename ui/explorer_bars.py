# -*- coding: utf-8 -*-
"""
Attribute Explorer editing rows.

- :class:`EditBar` is the second permanent toolbar row: the panel's scope
  line ("Statistics for: ...") on the left, the editing commands right
  aligned (ending at the Dock button's right edge).  The edit status lives
  in the status bar.  In a narrow window the lower-priority groups move into
  the More menu, so the row never wraps.
- :class:`QuickUpdateBar` is the optional third row, shown only while
  editing AND its separate Quick update toggle is on.
"""

from qgis.PyQt.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QVBoxLayout,
    QWidget,
)
from qgis.gui import QgsFieldComboBox, QgsFieldExpressionWidget

from . import icons
from . import widgets as W


class EditBar(QWidget):
    """Scope line (left, filled by the panel) and the editing commands
    (right)."""

    LEFT = 220          # kept for the scope line before groups move into More
    LEFT_MIN = 140

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
            icons.native(icons.NEW_ROW), "Add record in the table")
        self.add_form_action = self.add_menu.addAction(
            icons.native(icons.FORM_VIEW), "Add record with a form…")
        self.add_btn = W.split_button(m, icons.native(icons.NEW_ROW),
                                      "Add a record", self.add_menu)
        self.delete_btn = sq(m, icons.native(icons.DELETE_SELECTED),
                             "Delete the selected features")
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
        self.reload_btn = sq(m, icons.native(icons.RELOAD), "Reload the layer")
        self.more_menu = QMenu(self)
        self.more_btn = sq(m, icons.native(icons.MORE), "More")
        self.more_btn.setMenu(self.more_menu)
        self.more_btn.setPopupMode(W.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")

        # groups, left to right; the first is never moved into More
        self.groups = [
            [self.edit_btn, self.save_btn, self.discard_btn],
            [self.undo_btn, self.redo_btn],
            [self.add_btn, self.delete_btn],
            [self.cut_btn, self.copy_btn, self.paste_btn],
            [self.calc_btn, self.quick_btn, self.multi_btn],
            [self.new_field_btn, self.delete_field_btn],
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

    def add_info(self, widget, stretch=0):
        """Put *widget* on the left of the row: the scope line (stretch 1)
        or one of its chips, which take room from the commands when shown."""
        self.info_layout.addWidget(widget, stretch)
        if not stretch:
            self._chips.append(widget)
            widget.installEventFilter(self)

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


class QuickUpdateBar(QFrame):
    """Optional third row: field, expression, target, preview and apply.
    Opening or closing it never changes data."""

    previewRequested = pyqtSignal()
    applyRequested = pyqtSignal()
    closeRequested = pyqtSignal()

    TARGETS = (("layer", "All layer records"), ("filtered", "All filtered records"),
               ("selected_layer", "Selected layer records"),
               ("selected_shown", "Selected shown records"))

    def __init__(self, metrics, theme, parent=None):
        super().__init__(parent)
        m = metrics
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
        self.field_combo.setToolTip("Existing field to update")
        row.addWidget(self.field_combo)
        row.addWidget(W.label("Expression", "dim"))
        self.expression = QgsFieldExpressionWidget()
        self.expression.setFixedHeight(m.control_h)
        self.expression.setToolTip("QGIS expression giving the new value")
        row.addWidget(self.expression, 1)
        row.addWidget(W.label("Target", "dim"))
        self.target_combo = QComboBox()
        self.target_combo.setFixedHeight(m.control_h)
        self.target_combo.setMinimumWidth(210)
        for key, text in self.TARGETS:
            self.target_combo.addItem(text, key)
        self.target_combo.setCurrentIndex(3)
        row.addWidget(self.target_combo)
        self.preview_btn = W.button(m, "Preview update", icons.native(icons.QUICK_UPDATE),
                                    "Show what would change; nothing is written yet")
        row.addWidget(self.preview_btn)
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
        pv.addWidget(self.preview_note)
        self.apply_btn = W.action_button(m, "Apply", "Write the values (one undo step)")
        pv.addWidget(self.apply_btn, 0, Qt.AlignmentFlag.AlignBottom)
        self.preview.setVisible(False)
        outer.addWidget(self.preview)

        self.preview_btn.clicked.connect(self.previewRequested.emit)
        self.apply_btn.clicked.connect(self.applyRequested.emit)
        self.close_btn.clicked.connect(self.closeRequested.emit)
        for sig in (self.field_combo.fieldChanged, self.expression.fieldChanged,
                    self.target_combo.currentIndexChanged):
            sig.connect(lambda *_: self.preview.setVisible(False))

    def set_layer(self, layer):
        self.field_combo.setLayer(layer)
        self.expression.setLayer(layer)
        self.preview.setVisible(False)

    def target(self):
        return self.target_combo.currentData()

    def set_target_counts(self, counts):
        for i, (key, text) in enumerate(self.TARGETS):
            n = counts.get(key)
            self.target_combo.setItemText(i, f"{text} ({n:,})" if n is not None else text)
