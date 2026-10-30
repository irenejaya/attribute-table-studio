# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Find and Replace bar of the Attribute Explorer (Ctrl+F / Ctrl+H).

A card floating at the top right of the rows, drawn like VS Code's: the
chevron (or Ctrl+H) opens the Replace line under the Find line.  Matches
are searched in the background in the records and columns shown, in the
text the table shows; every match is tinted and the current one is the
framed cell (Ctrl+C copies it), scrolled to the middle.  Enter / Shift+Enter
(or F3 / Shift+F3) move between matches, Escape closes the bar.

Replace works while the layer is being edited, in text fields that can be
edited: Replace changes the framed match and moves on, Replace All changes
every match in the records shown as one undo step (unsaved until Save
edits).
"""

import re
import time
import traceback
from bisect import bisect_left, bisect_right

from qgis.PyQt.QtCore import QEvent, QSize, Qt, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QLineEdit, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)
from qgis.core import QgsField, QgsMessageLog, QgsVectorLayerFeatureSource

from ..core import fields as F
from ..core import find as FIND
from ..core.compat import MSG_WARNING
from ..core.jobs import JobRunner
from . import explorer_ops as OPS
from . import icons

LOG_TAG = "Attribute Table Studio"
BOX_WIDTH = 250                  # the Find / Replace text boxes
BUSY_CELLS = 200000              # above this many cells, say "Searching…"


class _InputBox(QFrame):
    """A text box with its option toggles inside it, as in VS Code."""

    def __init__(self, metrics, placeholder, parent=None):
        super().__init__(parent)
        self.setObjectName("gtsFindInput")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(BOX_WIDTH, metrics.control_h)
        self._metrics = metrics
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(6, 0, 2, 0)
        self._lay.setSpacing(1)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self._lay.addWidget(self.edit, 1)
        self.edit.installEventFilter(self)

    def add_toggle(self, icon, tip):
        side = self._metrics.control_h - 6
        btn = QToolButton(self)
        btn.setObjectName("gtsFindToggle")
        btn.setCheckable(True)
        btn.setIcon(icon)
        btn.setIconSize(QSize(side - 4, side - 4))
        btn.setFixedSize(side, side)
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lay.addWidget(btn)
        return btn

    def set_state(self, name, on):
        if bool(self.property(name)) != on:
            self.setProperty(name, on)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def eventFilter(self, obj, event):
        if obj is self.edit and event.type() in (QEvent.Type.FocusIn,
                                                  QEvent.Type.FocusOut):
            self.set_state("focus", event.type() == QEvent.Type.FocusIn)
        return False


class FindBar(QFrame):
    """The Find and Replace card of an ExplorerPanel (child of its table)."""

    def __init__(self, panel):
        super().__init__(panel.view)
        self.panel = panel
        self.view = panel.view
        self.model = panel.model
        self.setObjectName("gtsFindBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()
        self.matcher = None
        self.hits = ()                   # sorted row * len(cols) + position
        self.cols = []                   # searched columns, left to right
        self.col_pos = {}
        self._jump = False
        self._runner = JobRunner(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._search)
        self._build(panel.theme, panel.metrics)
        self.view.installEventFilter(self)
        self.view.viewport().installEventFilter(self)
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)

    # ------------------------------------------------------------------
    # look
    # ------------------------------------------------------------------
    def _tool(self, icon, tip, side):
        btn = QToolButton(self)
        btn.setObjectName("gtsFindTool")
        btn.setIcon(icon)
        btn.setIconSize(QSize(16, 16))
        btn.setFixedSize(side, side)
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _text_button(self, text, tip, height, icon=None):
        btn = QToolButton(self)
        btn.setObjectName("gtsFindText")
        btn.setText(f" {text}" if icon is not None else text)
        if icon is not None:
            btn.setIcon(icon)
            btn.setIconSize(QSize(16, 16))
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setFixedHeight(height)
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _build(self, t, m):
        h = m.control_h
        outer = QHBoxLayout(self)
        outer.setContentsMargins(3, 5, 6, 5)
        outer.setSpacing(3)
        self.expand_btn = self._tool(icons.glyph("chevron_right", t.text_dim),
                                     "Toggle Replace (Ctrl+H)", h)
        self.expand_btn.setFixedSize(18, h)
        self.expand_btn.setSizePolicy(QSizePolicy.Policy.Fixed,
                                      QSizePolicy.Policy.Expanding)
        self.expand_btn.setMaximumHeight(16777215)
        self.expand_btn.clicked.connect(
            lambda: self.set_replace(not self.replace_row.isVisible(), focus=True))
        outer.addWidget(self.expand_btn)
        rows = QVBoxLayout()
        rows.setSpacing(5)
        outer.addLayout(rows)

        def option(name):              # QGIS's dark artwork, light on a dark theme
            icon = icons.native(name)
            return icons.tinted(icon, t.text) if t.dark else icon

        find_row = QHBoxLayout()
        find_row.setSpacing(3)
        self.find_box = _InputBox(m, "Find", self)
        self.case_btn = self.find_box.add_toggle(option(icons.SEARCH_CASE), "Match case")
        self.whole_btn = self.find_box.add_toggle(option(icons.SEARCH_WHOLE),
                                                  "Match whole cell")
        self.regex_btn = self.find_box.add_toggle(
            option(icons.SEARCH_REGEX),
            "Use regular expression\nIn Replace, $1 … $99 put back a group and $& "
            "the whole match")
        find_row.addWidget(self.find_box)
        self.count_label = QLabel("", self)
        self.count_label.setObjectName("gtsFindCount")
        self.count_label.setMinimumWidth(
            self.count_label.fontMetrics().horizontalAdvance("999,999 of 999,999") + 8)
        find_row.addWidget(self.count_label)
        self.prev_btn = self._tool(icons.glyph("arrow_up", t.text),
                                   "Previous match (Shift+Enter)", h)
        self.next_btn = self._tool(icons.glyph("arrow_down", t.text),
                                   "Next match (Enter)", h)
        self.close_btn = self._tool(icons.glyph("cross", t.text),
                                    "Close (Escape)", h)
        for btn in (self.prev_btn, self.next_btn, self.close_btn):
            find_row.addWidget(btn)
        rows.addLayout(find_row)

        self.replace_row = QWidget(self)
        rep = QHBoxLayout(self.replace_row)
        rep.setContentsMargins(0, 0, 0, 0)
        rep.setSpacing(3)
        self.replace_box = _InputBox(m, "Replace", self.replace_row)
        rep.addWidget(self.replace_box)
        self.replace_btn = self._text_button(
            "Replace", "Replace this match, then go to the next one (Enter)", h)
        self.replace_all_btn = self._text_button(
            "Replace All", "Replace every match in the records shown, as one undo "
            "step (Ctrl+Alt+Enter)", h)
        self.start_btn = self._text_button("Start editing", "", h,
                                           icons.native(icons.TOGGLE_EDIT))
        for btn in (self.replace_btn, self.replace_all_btn, self.start_btn):
            rep.addWidget(btn)
        rep.addStretch(1)
        self.replace_row.setVisible(False)
        rows.addWidget(self.replace_row)

        self.setStyleSheet(f"""
            QFrame#gtsFindBar {{ background: {t.card}; border: 1px solid {t.border};
                border-radius: 6px; }}
            QFrame#gtsFindInput {{ background: {t.base}; border: 1px solid {t.border};
                border-radius: 4px; }}
            QFrame#gtsFindInput[focus="true"] {{ border: 1px solid {t.accent}; }}
            QFrame#gtsFindInput[error="true"] {{ border: 1px solid {t.danger}; }}
            QFrame#gtsFindInput QLineEdit {{ border: none; background: transparent;
                padding: 0px; color: {t.text}; }}
            QToolButton#gtsFindToggle {{ border: 1px solid transparent;
                border-radius: 3px; background: transparent; padding: 0px; }}
            QToolButton#gtsFindToggle:hover {{ background: {t.card_hover}; }}
            QToolButton#gtsFindToggle:checked {{ background: {t.accent_soft};
                border: 1px solid {t.accent}; }}
            QToolButton#gtsFindTool {{ border: none; border-radius: 4px;
                background: transparent; padding: 0px; }}
            QToolButton#gtsFindTool:hover {{ background: {t.card_hover}; }}
            QToolButton#gtsFindTool:pressed {{ background: {t.card_pressed}; }}
            QToolButton#gtsFindText {{ border: 1px solid {t.border}; border-radius: 4px;
                background: {t.base}; color: {t.text}; padding: 0px 8px; }}
            QToolButton#gtsFindText:hover {{ background: {t.card_hover}; }}
            QToolButton#gtsFindText:disabled {{ color: {t.text_faint};
                border-color: {t.border_soft}; }}
            QLabel#gtsFindCount {{ color: {t.text_dim}; padding-left: 4px; }}
            QLabel#gtsFindCount[state="none"] {{ color: {t.danger}; }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 140 if t.dark else 70))
        self.setGraphicsEffect(shadow)

        self.find_box.edit.textChanged.connect(lambda _t: self._schedule(jump=True))
        for btn in (self.case_btn, self.whole_btn, self.regex_btn):
            btn.toggled.connect(lambda _on: self._schedule(jump=True))
        self.find_box.edit.installEventFilter(self)
        self.replace_box.edit.installEventFilter(self)
        self.prev_btn.clicked.connect(self.previous)
        self.next_btn.clicked.connect(self.next)
        self.close_btn.clicked.connect(self.dismiss)
        self.replace_btn.clicked.connect(self.replace_one)
        self.replace_all_btn.clicked.connect(self.replace_all)
        self.start_btn.clicked.connect(self._start_editing)
        self.update_buttons()

    def place(self):
        """Top right of the rows, clear of the scroll bar."""
        if not self.isVisible():
            return
        self.adjustSize()
        vp = self.view.viewport().geometry()
        self.move(max(vp.left() + 4, vp.right() - self.width() - 14), vp.top() + 8)
        self.raise_()

    # ------------------------------------------------------------------
    # open / close
    # ------------------------------------------------------------------
    def open(self, replace=False):
        panel = self.panel
        if panel.is_form_view():
            panel.set_form_view(False)
        was_open = self.isVisible()
        if replace or not was_open:
            self.set_replace(replace)
        self.show()
        self.place()
        self._set_button(True)
        edit = (self.replace_box.edit if replace and self.find_box.edit.text()
                else self.find_box.edit)
        edit.setFocus()
        edit.selectAll()
        if not was_open and self.find_box.edit.text():
            self._schedule(jump=False, delay=0)       # the last search, highlighted again
        self.update_buttons()

    def dismiss(self):
        self._timer.stop()
        self._runner.new_generation()
        self.hide()
        self._set_hits(None)
        self._set_button(False)
        self.view.setFocus()

    def toggle(self):
        if self.isVisible():
            self.dismiss()
        else:
            self.open()

    def shutdown(self):
        self._timer.stop()
        self._runner.new_generation()

    def set_replace(self, on, focus=False):
        self.replace_row.setVisible(on)
        t = self.panel.theme
        self.expand_btn.setIcon(icons.glyph("chevron" if on else "chevron_right",
                                            t.text_dim))
        self.place()
        if focus:
            (self.replace_box.edit if on else self.find_box.edit).setFocus()

    def _set_button(self, on):
        btn = getattr(self.panel, "find_btn", None)
        if btn is not None:
            btn.blockSignals(True)
            btn.setChecked(on)
            btn.blockSignals(False)

    # ------------------------------------------------------------------
    # searching
    # ------------------------------------------------------------------
    def _schedule(self, jump=True, delay=150):
        self._jump = self._jump or jump
        self._timer.start(delay)

    def _matcher(self):
        """The search as typed, or None (empty, or an invalid expression:
        the box is outlined red and says why)."""
        text = self.find_box.edit.text()
        self.find_box.set_state("error", False)
        self.find_box.setToolTip("")
        if not text:
            return None
        try:
            return FIND.Matcher(text, self.case_btn.isChecked(),
                                self.whole_btn.isChecked(), self.regex_btn.isChecked())
        except re.error as exc:
            self.find_box.set_state("error", True)
            self.find_box.setToolTip(f"Invalid regular expression: {exc}")
            return None

    def _search(self):
        jump, self._jump = self._jump, False
        self._runner.new_generation()
        layer = self.panel.layer
        matcher = self._matcher() if self.isVisible() else None
        if matcher is None or layer is None:
            self._set_hits(None)
            self._show_count("Invalid pattern" if self.find_box.property("error")
                             else None)
            return
        cols = self.panel._shown_columns()
        fids = list(self.model.fids)
        fields = [QgsField(f) for f in self.model.fields]
        source = QgsVectorLayerFeatureSource(layer)

        def work(job):
            return FIND.search(source, fids, fields, cols, matcher, job)
        if len(fids) * max(len(cols), 1) > BUSY_CELLS:
            self._show_count("Searching…")
        self._runner.start(f"Attribute Explorer: find in {layer.name()}", work,
                           lambda job: self._on_found(job, matcher, cols, jump),
                           tag="find")

    def _on_found(self, job, matcher, cols, jump):
        if not self._runner.is_current(job) or not self.isVisible():
            return
        if job.error:
            QgsMessageLog.logMessage(job.error, LOG_TAG, MSG_WARNING)
            self._set_hits(None)
            self._show_count("Search failed")
            return
        if job.cancelled:
            return
        self.matcher = matcher
        self._set_hits(job.result, cols)
        if jump and self.hits:
            i = bisect_left(self.hits, max(self._cur_pos(), 0))
            self._go(i if i < len(self.hits) else 0)
        else:
            self._show_count()

    def _set_hits(self, hits, cols=None):
        if hits is None:
            self.hits, self.cols, self.col_pos = (), [], {}
            self.model.highlight = None
        else:
            self.hits = hits
            self.cols = list(cols)
            self.col_pos = {c: k for k, c in enumerate(self.cols)}
            self.model.highlight = self._level
        self.view.viewport().update()
        self.update_buttons()

    def rows_changed(self):
        """The table's rows changed (sort, filter, data): search again,
        leaving the frame where it is."""
        if self.isVisible() and self.find_box.edit.text():
            self.model.highlight = None              # the old positions are stale
            self._schedule(jump=False, delay=60)

    def columns_changed(self, *_args):
        if self.isVisible() and self.cols \
                and self.panel._shown_columns() != self.cols:
            self.rows_changed()

    # ------------------------------------------------------------------
    # matches
    # ------------------------------------------------------------------
    def _level(self, row, col):
        """Delegate hook: 0, 1 (a match) or 2 (the framed match)."""
        k = self.col_pos.get(col)
        if k is None:
            return 0
        pos = row * len(self.cols) + k
        i = bisect_left(self.hits, pos)
        if i >= len(self.hits) or self.hits[i] != pos:
            return 0
        cur = self.view.currentIndex()
        return 2 if cur.row() == row and cur.column() == col else 1

    def _cur_pos(self):
        """The framed cell's position (in a column not searched: just before
        its row's first column), or -1."""
        index = self.view.currentIndex()
        if not index.isValid() or not self.cols:
            return -1
        k = self.col_pos.get(index.column())
        n = len(self.cols)
        return index.row() * n + k if k is not None else index.row() * n - 1

    def _go(self, i):
        row, k = divmod(self.hits[i], len(self.cols))
        index = self.model.index(row, self.cols[k])
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._show_count()

    def next(self):
        if self._timer.isActive():                 # typed, not searched yet
            self._timer.stop()
            self._jump = True
            self._search()
            return
        if self.hits:
            i = bisect_right(self.hits, self._cur_pos())
            self._go(i if i < len(self.hits) else 0)

    def previous(self):
        if self._timer.isActive():
            self._timer.stop()
            self._jump = True
            self._search()
            return
        if self.hits:
            i = bisect_left(self.hits, self._cur_pos()) - 1
            self._go(i if i >= 0 else len(self.hits) - 1)

    def _on_current_changed(self, *_args):
        if self.isVisible() and self.hits:
            self._show_count()

    def _show_count(self, text=None):
        state = ""
        if text is None:
            n = len(self.hits)
            if not self.find_box.edit.text():
                text = ""
            elif not n:
                text, state = "No results", "none"
            else:
                cur = self._cur_pos()
                i = bisect_left(self.hits, cur)
                at = i + 1 if cur >= 0 and i < n and self.hits[i] == cur else None
                text = f"{at:,} of {n:,}" if at else f"? of {n:,}"
        elif text in ("Invalid pattern", "Search failed"):
            state = "none"
        self.count_label.setText(text)
        if self.count_label.property("state") != state:
            self.count_label.setProperty("state", state)
            self.count_label.style().unpolish(self.count_label)
            self.count_label.style().polish(self.count_label)

    # ------------------------------------------------------------------
    # replacing
    # ------------------------------------------------------------------
    def update_buttons(self, *_args):
        panel, model = self.panel, self.model
        has = bool(self.hits)
        self.prev_btn.setEnabled(has)
        self.next_btn.setEnabled(has)
        editing = model.editing
        for btn in (self.replace_btn, self.replace_all_btn):
            btn.setEnabled(editing and has)
        start = not editing and panel.layer is not None
        if self.start_btn.isVisibleTo(self) != start:
            self.start_btn.setVisible(start)
            self.place()
        if start:
            blocker = panel.edit.edit_blocker()
            self.start_btn.setEnabled(not blocker)
            self.start_btn.setToolTip(
                f"Editing is not available: {blocker}" if blocker else
                f"Start editing {panel.layer.name()} to replace (the edits stay "
                "unsaved until Save edits)")
        note = "" if editing else "\nStart editing to replace"
        self.replace_btn.setToolTip(
            "Replace this match, then go to the next one (Enter)" + note)
        self.replace_all_btn.setToolTip(
            "Replace every match in the records shown, as one undo step "
            "(Ctrl+Alt+Enter)" + note)

    def _start_editing(self):
        self.panel.edit.start()
        self.update_buttons()
        self.replace_box.edit.setFocus()

    def _replaceable(self, col):
        m = self.model
        return m.editing and col in m.editable_cols and m.kinds[col] == F.TEXT

    def replace_one(self):
        """Replace the framed match and go to the next one (on a cell that
        is not a match: just go to the next match)."""
        if not self.model.editing or self.matcher is None or self._timer.isActive():
            return
        cur = self._cur_pos()
        i = bisect_left(self.hits, cur) if self.hits else 0
        if cur < 0 or i >= len(self.hits) or self.hits[i] != cur:
            self.next()
            return
        index = self.view.currentIndex()
        row, col = index.row(), index.column()
        if not self._replaceable(col):
            self.panel.status.set_status(
                f"{self.model.fields[col].name()}: Replace changes text fields "
                "you can edit only", "warning")
            self.next()
            return
        old = self.model.raw_value(row, col)
        if isinstance(old, str):
            new = self.matcher.replace(old, self.replace_box.edit.text())
            if new != old and self.model.setData(index, new) \
                    and not self.matcher.matches(new):
                del self.hits[i]
        self.next()

    def replace_all(self):
        panel, model = self.panel, self.model
        matcher = self._matcher()
        if not model.editing or matcher is None or panel.layer is None:
            return
        cols = [c for c in panel._shown_columns() if self._replaceable(c)]
        if not cols:
            panel.status.set_status(
                "Nothing to replace: Replace changes text fields you can edit only",
                "warning")
            return
        panel.flush_editors()
        find, repl = self.find_box.edit.text(), self.replace_box.edit.text()
        try:
            done = OPS.replace_all(panel.layer, model.fids, cols, matcher, repl,
                                   f"Attribute Explorer: replace '{find}' with '{repl}'",
                                   panel)
        except OPS.OperationError as exc:
            panel.status.set_status(str(exc), "error")
            return
        except Exception:
            QgsMessageLog.logMessage(traceback.format_exc(), LOG_TAG, MSG_WARNING)
            panel.status.set_status("Replace All failed: nothing was changed", "error")
            return
        if done is None:
            panel.status.set_status("Replace All cancelled: nothing was changed",
                                    "warning")
            return
        values, features = done
        if values:
            text = (f"Replaced {values:,} value{'s' if values != 1 else ''} in "
                    f"{features:,} feature{'s' if features != 1 else ''} (unsaved; "
                    "Ctrl+Z undoes it)")
            panel._status_note = (time.monotonic() + 8, text)   # outlives the refresh
            panel.status.set_status(text, "success")
            self.rows_changed()                                  # the matches changed
        else:
            panel.status.set_status(
                "No matches in text fields you can edit", "warning")

    # ------------------------------------------------------------------
    # keys and placement
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.Resize and obj in (self.view, self.view.viewport()):
            self.place()
            return False
        if kind != QEvent.Type.KeyPress or obj not in (self.find_box.edit,
                                                       self.replace_box.edit):
            return False
        key, mods = event.key(), event.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        enter = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if key == Qt.Key.Key_Escape:
            self.dismiss()
            return True
        if obj is self.find_box.edit and (enter or key == Qt.Key.Key_F3):
            (self.previous if shift else self.next)()
            return True
        if obj is self.replace_box.edit and enter:
            if mods & Qt.KeyboardModifier.ControlModifier \
                    and mods & Qt.KeyboardModifier.AltModifier:
                self.replace_all()
            else:
                self.replace_one()
            return True
        return False
