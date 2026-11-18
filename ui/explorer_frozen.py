# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Frozen columns of the Attribute Explorer (ArcGIS Pro's Freeze / Unfreeze
Column).

A frozen column moves to the left edge of the table and stays there while
the other columns scroll sideways.  Several can be frozen; they sit left to
right in the order they were frozen.  Unfreezing lets a column scroll
again and leaves it first among the scrolling columns.  Only the view
changes, never the layer's field order; the frozen columns are kept with
the column layout of the layer (in the project).

How: the frozen columns keep their place at the start of the table and a
second table view, the *strip*, lies over its left edge showing only them.
It shares the table's model and selection and scrolls up and down with it.
Each frozen column's profile card and filter box move into the strip's
header (the same widgets, so filters and statistics work as before) and
move back when the column is unfrozen.
"""

from qgis.PyQt.QtCore import QEvent, QObject, Qt
from qgis.PyQt.QtWidgets import QFrame

from .explorer_header import ExplorerHeader
from .explorer_table import ExplorerTableView

EDGE = 2                          # the line between frozen and scrolling columns


class FrozenHeader(ExplorerHeader):
    """The strip's header: the frozen columns' names, profile cards and
    filter boxes (the table header's own widgets, moved here)."""

    def __init__(self, source):
        super().__init__(source.theme, source.metrics)
        self.source = source
        self.logicals = []
        self.setSectionsMovable(False)

    def total_height(self):
        return self.source.total_height()

    def name_top(self):
        return self.source.name_top()

    def logicals_in_view(self):
        return list(self.logicals)

    def paintSection(self, painter, rect, logical):
        self.selected_cols = self.source.selected_cols
        self.profiles_visible = self.source.profiles_visible
        super().paintSection(painter, rect, logical)

    def relayout(self):
        src = getattr(self, "source", None)
        if src is None:                  # still being built
            return
        self._place_flash()
        self.profiles_visible = src.profiles_visible
        filt_y = self.name_top() + self.name_h + 3
        vp = self.viewport()
        for logical in self.logicals:
            card, cell = src.cards.get(logical), src.cells.get(logical)
            if card is None or cell is None:
                continue
            for widget in (card, cell):
                if widget.parent() is not vp:
                    widget.setParent(vp)
            if self.isSectionHidden(logical):
                card.hide()
                cell.hide()
                continue
            x = self.sectionViewportPosition(logical)
            w = self.sectionSize(logical)
            if src.profiles_visible:
                card.setGeometry(x + 3, 3, max(10, w - 6), self.profile_h - 6)
                card.show()
            else:
                card.hide()
            cell.setGeometry(x + 3, filt_y, max(10, w - 6), self.filter_h - 6)
            cell.show()


class FrozenColumns(QObject):
    """Keeps the strip over the table's left edge in step with it."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.names = []                  # frozen field names, left to right
        self._busy = False
        main = panel.view
        self.main = main
        self.header = FrozenHeader(panel.header)
        self.view = ExplorerTableView(panel.theme, panel.metrics, self.header, main)
        self.view.setModel(panel.model)
        self.view.setSelectionModel(main.selectionModel())
        self.view.verticalHeader().hide()
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.strip_of = main
        self.view.hide()
        self.edge = QFrame(main)
        self.edge.setStyleSheet(f"background: {panel.theme.text_faint};")
        self.edge.hide()
        main.frozen = self
        # up and down together; widths, sorting and layout follow the table
        main.verticalScrollBar().valueChanged.connect(self.view.verticalScrollBar().setValue)
        self.view.verticalScrollBar().valueChanged.connect(main.verticalScrollBar().setValue)
        mh = main.horizontalHeader()
        mh.sectionResized.connect(self._on_table_resized)
        mh.sectionMoved.connect(self._on_table_moved)
        mh.sortIndicatorChanged.connect(self.header.setSortIndicator)
        mh.geometriesChanged.connect(self.update_geometry)
        self.header.sectionResized.connect(self._on_strip_resized)
        panel.model.modelReset.connect(self.apply)
        main.selectionModel().currentChanged.connect(self._on_current)
        main.installEventFilter(self)

    # ------------------------------------------------------------------
    def logicals(self):
        """Frozen columns' logical indexes, left to right (fields that no
        longer exist are skipped)."""
        index = {f.name(): i for i, f in enumerate(self.panel.model.fields)}
        return [index[n] for n in self.names if n in index]

    def is_frozen(self, logical):
        return logical in self.logicals()

    def width(self):
        mh = self.main.horizontalHeader()
        return sum(mh.sectionSize(i) for i in self.logicals() if not mh.isSectionHidden(i))

    def freeze(self, logical):
        name = self.panel.model.fields[logical].name()
        if name not in self.names:
            self.names.append(name)
        self.apply()

    def unfreeze(self, logical):
        """Back to scrolling, as the first scrolling column."""
        name = self.panel.model.fields[logical].name()
        if name in self.names:
            self.names.remove(name)
            self.apply()
            mh = self.main.horizontalHeader()
            self._move(mh, logical, len(self.logicals()))
            self.apply()

    def unfreeze_all(self):
        logicals = self.logicals()
        self.names = []
        self.apply()
        mh = self.main.horizontalHeader()
        for target, logical in enumerate(logicals):   # they stay first, in order
            self._move(mh, logical, target)
        self.apply()

    def set_names(self, names):
        """Frozen columns from a saved layout."""
        self.names = [n for n in names if n]
        self.apply()

    # ------------------------------------------------------------------
    def _move(self, header, logical, visual):
        current = header.visualIndex(logical)
        if current != visual and current >= 0:
            self._busy = True
            try:
                header.moveSection(current, visual)
            finally:
                self._busy = False

    def apply(self):
        """Frozen columns first in the table, the strip showing just them."""
        if self._busy:
            return
        logicals = self.logicals()
        mh = self.main.horizontalHeader()
        self.panel.header.frozen = set(logicals)
        for target, logical in enumerate(logicals):
            self._move(mh, logical, target)
        fh = self.header
        fh.logicals = logicals
        self._busy = True
        try:
            for logical in range(fh.count()):
                hidden = logical not in logicals or mh.isSectionHidden(logical)
                if fh.isSectionHidden(logical) != hidden:
                    fh.setSectionHidden(logical, hidden)
            for target, logical in enumerate(logicals):
                current = fh.visualIndex(logical)
                if current != target and current >= 0:
                    fh.moveSection(current, target)
                if fh.sectionSize(logical) != mh.sectionSize(logical) \
                        and not mh.isSectionHidden(logical):
                    fh.resizeSection(logical, mh.sectionSize(logical))
            if mh.sortIndicatorSection() != fh.sortIndicatorSection() \
                    or mh.sortIndicatorOrder() != fh.sortIndicatorOrder():
                fh.setSortIndicator(mh.sortIndicatorSection(), mh.sortIndicatorOrder())
        finally:
            self._busy = False
        mh.relayout()
        fh.relayout()
        self.update_geometry()

    def update_geometry(self, *_args):
        main = self.main
        width = self.width()
        if not width:
            self.view.hide()
            self.edge.hide()
            return
        x = main.verticalHeader().width() + main.frameWidth()
        y = main.frameWidth()
        height = main.horizontalHeader().height() + main.viewport().height()
        self.view.setGeometry(x, y, width, height)
        self.edge.setGeometry(x + width - 1, y, EDGE, height)
        self.view.verticalScrollBar().setValue(main.verticalScrollBar().value())
        self.view.show()
        self.edge.show()
        self.view.raise_()
        self.edge.raise_()
        self.header.updateGeometry()
        self.header.relayout()

    def refresh(self):
        """Repaint (selected columns, profile strip shown or hidden)."""
        self.header.updateGeometry()
        self.view.updateGeometries()
        self.header.viewport().update()
        self.view.viewport().update()
        self.update_geometry()

    # ------------------------------------------------------------------
    def _on_table_resized(self, logical, _old, new):
        if self._busy or logical not in self.header.logicals:
            return
        if new and self.header.sectionSize(logical) != new:
            self._busy = True
            try:
                self.header.resizeSection(logical, new)
            finally:
                self._busy = False
        self.apply()

    def _on_strip_resized(self, logical, _old, new):
        if self._busy or not new:
            return
        mh = self.main.horizontalHeader()
        if mh.sectionSize(logical) != new:
            mh.resizeSection(logical, new)      # the table follows (and saves it)

    def _on_table_moved(self, *_args):
        if not self._busy and self.names:
            self.apply()                 # the frozen columns stay first

    def _on_current(self, current, _previous):
        """The framed cell shows in the view that holds its column."""
        if not current.isValid() or not self.names:
            return
        frozen = current.column() in self.header.logicals
        if frozen and self.main.hasFocus():
            self.view.setFocus()
        elif not frozen and self.view.hasFocus():
            self.main.setFocus()

    def eventFilter(self, obj, event):
        if obj is self.main and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.update_geometry()
        return False
