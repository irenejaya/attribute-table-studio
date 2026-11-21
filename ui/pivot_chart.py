# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Pivot Table chart: the built pivot as a chart, in the window of Visualize
Statistics.

Opened from the chart icon in the Pivot Table's top row; one chart per
pivot, closed with it, shown as a window or docked.  One bar per row of the
pivot, its value one of the pivot's values (the first by default); with
column fields the columns become the coloured parts of the bars, stacked
or side by side.  The chart follows the pivot: a new Build Pivot draws it
again, sorting the table reorders it (with Sort "As in the pivot table").

The bars come from the pivot's own result (:mod:`..core.pivot_chart`), so
nothing is read again.  Hovering a bar shows its numbers; a click selects
its records on the layer (Ctrl adds, a drag selects a range), a click on a
column's colour in the legend selects that column, a right-click offers
the drill-through of the pivot table (Inspect contributing records).
"""

from qgis.PyQt.QtCore import QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QMessageBox, QScrollArea, QSizePolicy, QSplitter,
    QVBoxLayout, QWidget,
)
from qgis.core import QgsProject
from qgis.gui import QgsColorButton

from ..core import export as X
from ..core import pivot as P
from ..core import pivot_chart as PC
from ..core import settings
from ..core.compat import SELECT_ADD, SELECT_SET
from ..core.jobs import JobRunner
from . import icons
from . import widgets as W
from .chart_view import ChartModel, ChartView
from .theme import Metrics, Theme, stylesheet

SYNC_LIMIT = 250000              # up to this many records, the selection at once
CHART_TYPES = (("columns", "Column chart"), ("bar", "Bar chart"))
TOP_CHOICES = (("All rows", None), ("First 10", 10), ("First 25", 25), ("First 50", 50),
               ("First 100", 100))
AUTO_TOP = 100                   # a pivot with more rows starts on the first 100


def open_pivot_chart(pivot):
    """Open (or raise) the chart window of the Pivot Table *pivot*."""
    host = getattr(pivot, "chart_host", None)
    if host is not None:
        host.show_and_raise()
        return host.panel
    from .tool_host import ToolHost
    panel = PivotChartPanel(pivot)
    host = ToolHost(pivot.iface, panel, "chart", icons.native(icons.CHART),
                    QSize(1180, 700))
    panel.titleChanged.connect(host.refresh_title)

    def closed(_host, pv=pivot):
        pv.chart_host = None

    host.closed.connect(closed)
    pivot.chart_host = host
    host.open()
    return panel


def close_pivot_chart(pivot):
    host = getattr(pivot, "chart_host", None)
    if host is not None:
        pivot.chart_host = None
        host.close()


class PivotChartPanel(QWidget):
    titleChanged = pyqtSignal()

    def __init__(self, pivot, parent=None):
        super().__init__(parent)
        self.pivot = pivot
        self.iface = pivot.iface
        self.theme = Theme()
        self.metrics = Metrics(self.font())
        self.setObjectName("gtsPanel")
        self.setStyleSheet(stylesheet(self.theme, self.metrics))
        self.selector = JobRunner(self)
        self.layer = None
        self.result = None
        self.bars = None
        self.sel = self.sel_series = None
        self.sel_records = self.sel_records_series = None
        self._custom_title = False
        self._mode_touched = False
        self._closing = False
        self._build_ui()
        self._sel_timer = QTimer(self)
        self._sel_timer.setSingleShot(True)
        self._sel_timer.setInterval(120)
        self._sel_timer.timeout.connect(self._update_selection)
        pivot.model.modelReset.connect(self.follow)
        pivot.model.layoutChanged.connect(self._on_table_order)
        self.follow()

    # ====================================================================
    # UI
    # ====================================================================

    def _build_ui(self):
        t, m = self.theme, self.metrics
        lay = QVBoxLayout(self)
        lay.setContentsMargins(m.margin, m.margin, m.margin, m.margin)
        lay.setSpacing(m.gap + 2)

        top = QHBoxLayout()
        top.setSpacing(m.gap)
        top.addWidget(W.label("Value", "dim"))
        self.value_combo = QComboBox()
        self.value_combo.setFixedHeight(m.control_h)
        self.value_combo.setMinimumWidth(180)
        self.value_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.value_combo.setToolTip("Which value of the pivot the bars show")
        self.value_combo.currentIndexChanged.connect(self._on_value_changed)
        top.addWidget(self.value_combo)
        top.addWidget(W.label("Chart", "dim"))
        self.kind_combo = QComboBox()
        self.kind_combo.setFixedHeight(m.control_h)
        self.kind_combo.setToolTip("Column chart: vertical columns, one per row of the pivot\n"
                                   "Bar chart: horizontal bars, one per row of the pivot")
        for data, text in CHART_TYPES:
            self.kind_combo.addItem(icons.native(icons.CHART), text, data)
        self.kind_combo.currentIndexChanged.connect(self._on_chart_type_changed)
        top.addWidget(self.kind_combo)
        top.addSpacing(m.group_gap)
        self.scope_label = W.ElideLabel()
        self.scope_label.setObjectName("gtsDim")
        self.scope_label.setMinimumWidth(80)
        top.addWidget(self.scope_label, 1)
        self.clear_btn = W.square_button(m, icons.native(icons.DESELECT_LAYER),
                                         "Clear the chart selection (deselect the records)")
        self.clear_btn.clicked.connect(self._clear_selection)
        top.addWidget(self.clear_btn)
        menu = QMenu(self)
        menu.addAction(icons.native(icons.SAVE), "Save as PNG Image…").triggered.connect(
            lambda: self.export_image("png"))
        menu.addAction(icons.native(icons.SAVE), "Save as SVG Image…").triggered.connect(
            lambda: self.export_image("svg"))
        menu.addAction(icons.native(icons.COPY), "Copy Image").triggered.connect(
            self.copy_image)
        menu.addSeparator()
        menu.addAction(icons.native(icons.CSV), "Bars as CSV…").triggered.connect(
            self.export_csv)
        self.export_btn = W.menu_square(m, icons.native(icons.EXPORT), "Export the chart",
                                        menu)
        top.addWidget(self.export_btn)
        help_btn = W.square_button(m, icons.native(icons.HELP), "Help")
        help_btn.clicked.connect(self.show_help)
        top.addWidget(help_btn)
        self.dock_btn = W.dock_button(m)
        top.addWidget(self.dock_btn)
        lay.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.view = ChartView(t, m)
        self.view.barsPicked.connect(self._on_bars_picked)
        self.view.seriesPicked.connect(self._on_series_picked)
        self.view.barMenuRequested.connect(self._bar_menu)
        self.chart_scroll = QScrollArea()
        self.chart_scroll.setWidgetResizable(True)
        self.chart_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chart_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chart_scroll.setWidget(self.view)
        split.addWidget(self.chart_scroll)
        split.addWidget(self._build_settings())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        lay.addWidget(split, 1)

        self.status = W.StatusStrip(m)
        lay.addWidget(self.status)

    def _row(self, layout, text, widget):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(self.metrics.gap)
        row.addWidget(W.label(text, "dim"))
        row.addWidget(widget, 1)
        layout.addWidget(holder)
        return holder

    def _combo(self, items):
        combo = QComboBox()
        combo.setFixedHeight(self.metrics.control_h)
        for text, data in items:
            combo.addItem(text, data)
        return combo

    def _tick(self, layout, text, on):
        box = QCheckBox(text)
        box.setChecked(on)
        layout.addWidget(box)
        return box

    def _build_settings(self):
        t, m = self.theme, self.metrics
        card = QFrame()
        card.setObjectName("gtsCard")
        pl = QVBoxLayout(card)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(6)

        pl.addWidget(W.section_header("Data"))
        self.sort_combo = QComboBox()
        self.sort_combo.setFixedHeight(m.control_h)
        self.sort_combo.setIconSize(QSize(m.icon, m.icon))
        for key in PC.SORTS:
            icon = icons.native(icons.TABLE) if key == "table" else icons.sort_icon(key, t)
            self.sort_combo.addItem(icon, PC.SORT_LABELS[key], key)
        self.sort_combo.currentIndexChanged.connect(self.rebuild)
        self._row(pl, "Sort", self.sort_combo)
        self.top_combo = self._combo(TOP_CHOICES)
        self.top_combo.setToolTip("How many rows of the pivot the chart shows (after sorting)")
        self.top_combo.currentIndexChanged.connect(self.rebuild)
        self._row(pl, "Show", self.top_combo)
        self.scale_combo = self._combo((("Linear", "linear"), ("Logarithmic", "log"),
                                        ("Square root", "sqrt")))
        self.scale_combo.currentIndexChanged.connect(self._redraw)
        self._row(pl, "Scale", self.scale_combo)
        self.split_mode = self._combo((("Stacked", "stacked"), ("Side by side", "side")))
        self.split_mode.activated.connect(self._on_mode_touched)
        self.split_mode.currentIndexChanged.connect(self._redraw)
        self.split_mode_row = self._row(pl, "Bars", self.split_mode)

        pl.addWidget(W.section_header("Summary"))
        self.stats_box = QWidget()
        self.stats_grid = QGridLayout(self.stats_box)
        self.stats_grid.setContentsMargins(0, 0, 0, 0)
        self.stats_grid.setHorizontalSpacing(12)
        self.stats_grid.setVerticalSpacing(3)
        pl.addWidget(self.stats_box)

        pl.addWidget(W.section_header("Appearance"))
        self.title_edit = QLineEdit()
        self.title_edit.setFixedHeight(m.control_h)
        self.title_edit.textEdited.connect(self._on_title_edited)
        self._row(pl, "Title", self.title_edit)
        self.colour_btn = QgsColorButton()
        self.colour_btn.setColor(QColor(t.bar))
        self.colour_btn.setDefaultColor(QColor(t.bar))
        self.colour_btn.setShowNull(False)
        self.colour_btn.setFixedHeight(m.control_h)
        self.colour_btn.setToolTip("Colour of the bars")
        self.colour_btn.colorChanged.connect(self._redraw)
        self._row(pl, "Bar colour", self.colour_btn)
        self.labels_tick = self._tick(pl, "Label bars with their value", False)
        self.grid_tick = self._tick(pl, "Grid lines", True)
        for box in (self.labels_tick, self.grid_tick):
            box.toggled.connect(self._redraw)
        pl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(card)
        scroll.setFixedWidth(312)
        return scroll

    # ====================================================================
    # FOLLOWING THE PIVOT
    # ====================================================================

    def window_title(self):
        layer = self.layer.name() if self.layer is not None else ""
        return f"Pivot Table Chart — {layer}" if layer else "Pivot Table Chart"

    def _set_layer(self, layer):
        if layer is self.layer:
            return
        if self.layer is not None:
            try:
                self.layer.selectionChanged.disconnect(self._on_selection_changed)
            except (TypeError, RuntimeError):
                pass
        self.layer = layer
        if layer is not None:
            layer.selectionChanged.connect(self._on_selection_changed)
        self.titleChanged.emit()

    def follow(self):
        """The pivot's result again (built, rebuilt or cleared)."""
        if self._closing:
            return
        pv = self.pivot
        self._set_layer(pv.layer)
        result = pv.model.result
        new_spec = result is None or self.result is None or \
            result.spec.to_dict() != self.result.spec.to_dict()
        self.result = result
        self._fill_values(new_spec)
        if result is not None and new_spec:
            many = result.group_count > AUTO_TOP
            self.top_combo.blockSignals(True)
            self.top_combo.setCurrentIndex(self.top_combo.findData(AUTO_TOP if many
                                                                   else None))
            self.top_combo.blockSignals(False)
            self._custom_title = False
        self._show_controls()
        self.rebuild()

    def _fill_values(self, new_spec):
        combo = self.value_combo
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        if self.result is not None:
            for key, title, agg in PC.value_choices(self.result.spec):
                combo.addItem(title, key)
                combo.setItemData(combo.count() - 1, agg, Qt.ItemDataRole.UserRole + 1)
            idx = combo.findText(current) if current and not new_spec else -1
            combo.setCurrentIndex(max(idx, 0))
        combo.blockSignals(False)
        combo.setEnabled(combo.count() > 1)

    def _on_table_order(self, *_args):
        if self.sort_combo.currentData() == "table":
            self.rebuild()

    def value_key(self):
        return self.value_combo.currentData()

    def value_title(self):
        return self.value_combo.currentText()

    def agg(self):
        return self.value_combo.currentData(Qt.ItemDataRole.UserRole + 1) or "count"

    def chart_type(self):
        return self.kind_combo.currentData() or "columns"

    def _on_value_changed(self, *_args):
        self._custom_title = False
        self._show_controls()
        self.rebuild()

    def _on_mode_touched(self, *_args):
        self._mode_touched = True

    def _on_chart_type_changed(self, *_args):
        self._sort_icons_follow_chart()
        self._redraw()

    def _sort_icons_follow_chart(self):
        horizontal = self.chart_type() == "bar"
        for i in range(self.sort_combo.count()):
            key = self.sort_combo.itemData(i)
            if key != "table":
                self.sort_combo.setItemIcon(i, icons.sort_icon(key, self.theme, horizontal))
        if horizontal:
            axes = ("Bar chart: the rows of the pivot run down the left (Y axis), the "
                    "value along the bottom (X axis).\n"
                    "A → Z / Z → A: the rows from top to bottom\n"
                    "Smallest / Largest value first: by bar length")
        else:
            axes = ("Column chart: the rows of the pivot run along the bottom (X axis), "
                    "the value up the side (Y axis).\n"
                    "A → Z / Z → A: the rows from left to right\n"
                    "Smallest / Largest value first: by column height")
        self.sort_combo.setToolTip("As in the pivot table: in the table's order (sort the "
                                   "table by a column to reorder the chart)\n" + axes)

    def _show_controls(self):
        """Stacked bars only for values that add up (records, counts and
        sums); a mean, median, minimum, maximum, standard deviation or
        distinct count of the parts would not make the bar's total."""
        cols = self.result is not None and self.result.has_cols
        self.split_mode_row.setVisible(cols)
        additive = self.agg() in PC.ADDITIVE
        model = self.split_mode.model()
        item = model.item(self.split_mode.findData("stacked")) if hasattr(model, "item") \
            else None
        if item is not None:
            item.setEnabled(additive)
        self.split_mode.blockSignals(True)
        if not additive:
            self.split_mode.setCurrentIndex(self.split_mode.findData("side"))
        elif not self._mode_touched:
            self.split_mode.setCurrentIndex(self.split_mode.findData("stacked"))
        self.split_mode.blockSignals(False)
        self.split_mode.setToolTip(
            "Stacked: the parts of a bar add up to the row's total\n"
            "Side by side: one bar per column of the pivot" if additive else
            f"Side by side: a {self.value_title()} of the columns does not add up to the "
            "row's, so the bars are not stacked")
        self.colour_btn.setEnabled(not cols)
        self.colour_btn.setToolTip("Each column of the pivot has its own colour" if cols
                                   else "Colour of the bars")
        self._sort_icons_follow_chart()

    def _on_title_edited(self, _text):
        self._custom_title = True
        self._redraw()

    def _default_title(self):
        if self.result is None:
            return ""
        spec = self.result.spec
        title = f"{self.value_title()} by {', '.join(spec.rows)}"
        if spec.cols:
            title += f" and {', '.join(spec.cols)}"
        return title

    # ====================================================================
    # BARS
    # ====================================================================

    def rebuild(self, *_args):
        if self._closing:
            return
        result = self.result
        if result is None or self.value_key() is None:
            self.bars = None
            self.sel = self.sel_series = self.sel_records = self.sel_records_series = None
            self.view.set_message("Build the pivot: its chart shows here")
            self.status.set_status("Build the pivot in the Pivot Table window first")
            self._update_scope_label()
            self._fill_stats()
            return
        sort = self.sort_combo.currentData()
        rows = [row[0]["rk"] for row in self.pivot.model.rows] if sort == "table" else None
        self.bars = PC.chart_bars(result, self.value_key(), rows, sort,
                                  self.top_combo.currentData())
        self._scale_follows_values()
        self.sel = self.sel_series = self.sel_records = self.sel_records_series = None
        self._fill_stats()
        self._update_scope_label()
        self._update_selection()
        self._redraw()

    def _scale_follows_values(self):
        """A logarithmic or square root axis cannot show negative values."""
        negative = self.bars is not None and self.bars.negative
        model = self.scale_combo.model()
        for key in ("log", "sqrt"):
            item = model.item(self.scale_combo.findData(key)) if hasattr(model, "item") \
                else None
            if item is not None:
                item.setEnabled(not negative)
        if negative and self.scale_combo.currentData() != "linear":
            self.scale_combo.blockSignals(True)
            self.scale_combo.setCurrentIndex(self.scale_combo.findData("linear"))
            self.scale_combo.blockSignals(False)
        self.scale_combo.setToolTip(
            "Negative values: a linear axis through 0" if negative else
            "How the value axis grows: a logarithmic or square root scale shows small "
            "bars next to very tall ones")

    def _fmt(self, v):
        return PC.format_value(v, self.agg(), settings.value("pivot/decimals"))

    def _redraw(self, *_args):
        if self._closing:
            return
        if not self._custom_title:
            self.title_edit.blockSignals(True)
            self.title_edit.setText(self._default_title())
            self.title_edit.setCursorPosition(0)
            self.title_edit.blockSignals(False)
        if self.bars is None:
            return
        if not len(self.bars):
            self.view.set_message("The pivot has no rows to chart")
            self._update_status()
            return
        self.view.set_model(self._model())
        self._update_status()

    def _model(self):
        bars, spec = self.bars, self.result.spec
        m = ChartModel()
        m.bars = bars
        m.title = self.title_edit.text()
        m.horizontal = self.chart_type() == "bar"
        m.x_title = "" if m.horizontal else ", ".join(spec.rows)
        m.y_title = self.value_title()
        m.fmt_value = self._fmt
        m.fmt_bar = lambda i: str(bars.labels[i])
        m.card = self._card
        m.scale = self.scale_combo.currentData() if not bars.negative else "linear"
        m.labels = self.labels_tick.isChecked()
        m.grid = self.grid_tick.isChecked()
        colour = self.colour_btn.color()
        m.colour = colour if colour.isValid() and colour != QColor(self.theme.bar) else None
        m.split_mode = self.split_mode.currentData()
        m.sel = self.sel
        m.sel_series = self.sel_series
        m.sel_records = sum(self.sel_records) if self.sel_records else 0
        project = QgsProject.instance()
        m.sel_colour = QColor(project.selectionColor()) if project.selectionColor().isValid() \
            else QColor("#ffff00")
        return m

    def _card(self, i, series):
        """The hover card: the bar's value (and each column's), its
        records and how many of them are selected."""
        bars = self.bars
        agg = self.agg()
        lines = []
        rec_sel = self.sel_records[i] if self.sel_records else 0
        if bars.split is not None:
            rs = self.sel_records_series[i] if self.sel_records_series else None
            for s, name in enumerate(bars.series):
                n = bars.split_records[i][s]
                if not n:
                    continue
                text = f"{name}: {self._fmt(bars.split_values[i][s])}"
                if agg != "count":
                    text += f"  ({n:,} record{'s' if n != 1 else ''})"
                if rs and rs[s]:
                    text += f"  · {rs[s]:,} selected"
                lines.append((s, text, s == series))
            lines.append((None, f"Total: {self._fmt(bars.values[i])}", False))
        else:
            lines.append((None, f"{self.value_title()}: {self._fmt(bars.values[i])}", False))
        n = bars.records[i]
        if agg != "count":                   # a count is already the records
            lines.append((None, f"{n:,} record{'s' if n != 1 else ''}", False))
        if rec_sel:
            lines.append((None, f"{rec_sel:,} of {n:,} selected", False))
        if self._data_changed():
            foot = "The layer changed: Build Pivot again to select"
        elif bars.split is not None and series is not None:
            foot = "Click to select this column's records · right-click for more"
        else:
            foot = "Click to select its records · Ctrl+click to add · right-click for more"
        return str(bars.labels[i]), lines, foot

    # ====================================================================
    # STATUS, SUMMARY
    # ====================================================================

    def _data_changed(self):
        """The layer changed after the pivot was built: its record ids may
        no longer be right, so selecting waits for a new Build Pivot."""
        pv = self.pivot
        r = pv._result_input
        return r is not None and r.get("built_revision") != pv._revision

    def on_pivot_status(self):
        """The pivot's input or layout changed (or was rebuilt)."""
        if not self._closing and self.bars is not None:
            self._update_status()

    def _update_scope_label(self):
        result = self.result
        if result is None:
            self.scope_label.setText("")
            return
        r = self.pivot._result_input or {}
        parts = [f"Pivot of {result.n_input:,} records",
                 f"{result.group_count:,} row{'s' if result.group_count != 1 else ''}"]
        if r.get("label"):
            parts.append(r["label"])
        if r.get("filters"):
            parts.append("Filters: " + " AND ".join(r["filters"]))
        text = "  •  ".join(parts)
        self.scope_label.setText(text)
        self.scope_label.setToolTip("The chart shows the pivot as it was last built:\n"
                                    + text)

    def _update_status(self):
        bars = self.bars
        if bars is None:
            return
        horizontal = self.chart_type() == "bar"
        unit = "bar" if horizontal else "column"
        parts = [self.kind_combo.currentText(),
                 f"{len(bars):,} {unit}{'s' if len(bars) != 1 else ''}"]
        if bars.note:
            parts.append(bars.note)
        n_sel = sum(self.sel_records) if self.sel_records else 0
        level = "info"
        if self._data_changed():
            parts.append("The layer changed after this pivot was built: Build Pivot again "
                         "(selecting is paused)")
            level = "warning"
        elif self.pivot._stale_reason() == "layout":
            parts.append("The pivot's layout changed: press Build Pivot to chart it")
            level = "warning"
        elif n_sel:
            parts.append(f"{n_sel:,} of {bars.total:,} records selected")
        else:
            parts.append("Click a bar to select its records, drag for a range")
        self.status.set_status("  •  ".join(parts), level)

    def _fill_stats(self):
        grid = self.stats_grid
        while grid.count():
            item = grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        result = self.result
        if result is None or self.value_key() is None:
            return
        key = self.value_key()
        values = []
        for rk in result.row_keys:
            v = PC._value(result, key, rk)
            if v is not None:
                values.append((v, P.key_label(rk)))
        rows = [("Rows", f"{result.group_count:,}")]
        if result.has_cols:
            rows.append(("Columns", f"{len(result.col_keys):,}"))
        rows.append(("Records", f"{result.n_input:,}"))
        rows.append(("Grand total", self._fmt(PC._value(result, key, P.TOTAL))))
        if values:
            hi = max(values, key=lambda e: e[0])
            lo = min(values, key=lambda e: e[0])
            rows.append(("Largest", f"{hi[1]} ({self._fmt(hi[0])})"))
            rows.append(("Smallest", f"{lo[1]} ({self._fmt(lo[0])})"))
        tips = {"Grand total": f"{self.value_title()} of all the pivot's records, as in "
                               "its Grand total row",
                "Largest": "The row with the largest value",
                "Smallest": "The row with the smallest value"}
        for r, (label, value) in enumerate(rows):
            a = QLabel(label)
            a.setObjectName("gtsDim")
            if label in tips:
                a.setToolTip(tips[label])
            b = QLabel(value)
            b.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            b.setToolTip(value)
            grid.addWidget(a, r, 0)
            grid.addWidget(b, r, 1)
        grid.setColumnStretch(1, 1)

    # ====================================================================
    # SELECTION
    # ====================================================================

    def _on_selection_changed(self, *_args):
        if self.bars is not None and not self._closing:
            self._sel_timer.start()

    def _update_selection(self):
        result, bars = self.result, self.bars
        if result is None or bars is None or self._closing or self.layer is None:
            return
        try:
            selected = set(self.layer.selectedFeatureIds())
        except RuntimeError:
            return
        if result.n_input <= SYNC_LIMIT:
            self._apply_selection(PC.selection(result, bars, selected))
            return
        self.selector.new_generation()

        def work(_job, result=result, bars=bars, selected=selected):
            return PC.selection(result, bars, selected)

        def done(job):
            if self._closing or not self.selector.is_current(job) or job.result is None:
                return
            if bars is self.bars:
                self._apply_selection(job.result)

        self.selector.start("Pivot Table chart: selection", work, done, tag="sel")

    def _apply_selection(self, picked):
        self.sel, self.sel_series, self.sel_records, self.sel_records_series = picked
        self._redraw()

    def _select(self, bar_indexes, add, series=None):
        if self.bars is None or self.layer is None:
            return
        if self._data_changed():
            self.status.set_status("The layer changed after this pivot was built: press "
                                   "Build Pivot, then select", "warning")
            return
        fids = PC.bar_fids(self.result, self.bars, bar_indexes, series)
        try:
            self.layer.selectByIds(fids, SELECT_ADD if add else SELECT_SET)
        except RuntimeError:
            pass

    def _on_bars_picked(self, bar_indexes, add, series):
        self._select(bar_indexes, add, series)

    def _on_series_picked(self, series, add):
        if self.bars is not None:
            self._select(list(range(len(self.bars))), add, series)

    def _clear_selection(self):
        try:
            if self.layer is not None:
                self.layer.removeSelection()
        except RuntimeError:
            pass

    def _bar_label(self, i, series):
        """The drill-through's label, as the pivot table's."""
        bars = self.bars
        rk = bars.row_keys[i]
        if series is None:
            return self.pivot._cell_label(rk, P.TOTAL)
        keys = bars.series_keys[series]
        if len(keys) == 1:
            return self.pivot._cell_label(rk, keys[0])
        cols = ", ".join(self.result.spec.cols)
        return f"{self.pivot._cell_label(rk, P.TOTAL)}, {cols} = Other ({len(keys)} values)"

    def _bar_menu(self, i, series, pos):
        if self.bars is None or i >= len(self.bars):
            return
        bars = self.bars
        name = str(bars.labels[i])
        if series is not None:
            name += f" · {bars.series[series]}"
        menu = QMenu(self)
        menu.addAction(icons.native(icons.SELECTED_ONLY), f"Select Records of {name}"
                       ).triggered.connect(lambda: self._select([i], False, series))
        menu.addAction(icons.native(icons.SELECTED_ONLY), "Add to Selection"
                       ).triggered.connect(lambda: self._select([i], True, series))
        menu.addSeparator()
        menu.addAction(icons.tool(icons.EXPLORER), "Inspect Contributing Records"
                       ).triggered.connect(lambda: self.inspect(i, series))
        menu.exec(pos)

    def inspect(self, i, series=None):
        """The pivot table's drill-through: the bar's records in Attribute
        Explorer."""
        pv = self.pivot
        if pv._stale_reason() in ("input", "data", "selection"):
            QMessageBox.information(
                self, "Pivot Table",
                "The source changed after this pivot was built.\n"
                "Refresh input and rebuild before inspecting records.")
            return
        fids = PC.bar_fids(self.result, self.bars, [i], series)
        pv.drillRequested.emit(self.layer, fids, self._bar_label(i, series))

    # ====================================================================
    # EXPORT, HELP
    # ====================================================================

    def _safe_name(self):
        raw = f"{self.layer.name() if self.layer is not None else 'pivot'}_pivot"
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw) or "chart"

    def export_image(self, fmt):
        if self.bars is None:
            return
        ext = ".png" if fmt == "png" else ".svg"
        flt = "PNG image (*.png)" if fmt == "png" else "SVG image (*.svg)"
        path, _sel = QFileDialog.getSaveFileName(
            self, "Save the chart", f"{self._safe_name()}_chart{ext}", flt)
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext
        try:
            if fmt == "png":
                if not self.view.render_image(2.0).save(path):
                    raise OSError(f"Could not write {path}")
            else:
                self.view.render_svg(path, self.title_edit.text())
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            self.status.set_status(f"Chart export failed: {exc}", "error")
            return
        self.status.set_status(f"Chart saved to {path}", "success")

    def copy_image(self):
        if self.bars is None:
            return
        QGuiApplication.clipboard().setImage(self.view.render_image(2.0))
        self.status.set_status("Chart copied as an image", "success")

    def export_csv(self):
        bars = self.bars
        if bars is None:
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, "Export the bars", f"{self._safe_name()}_bars.csv", "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        self.write_csv(path)

    def write_csv(self, path):
        bars, spec = self.bars, self.result.spec
        cols = ", ".join(spec.cols)
        header = [", ".join(spec.rows), self.value_title(), "Records", "Selected"]
        header += [f"{cols}: {s}" for s in bars.series] if bars.split is not None else []
        rows = PC.table_rows(bars, self.sel_records)
        r = self.pivot._result_input or {}
        meta = {"Layer": self.layer.name() if self.layer is not None else "",
                "Pivot": self.pivot.title_label.text(),
                "Value": self.value_title(),
                "Input": r.get("label", ""),
                "Filters": " AND ".join(r.get("filters") or []) or "None",
                "Records in the pivot": self.result.n_input,
                "Sorted": self.sort_combo.currentText()}
        if bars.note:
            meta["Shown"] = bars.note
        try:
            X.write_csv(path, header, rows, meta)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            self.status.set_status(f"Bars export failed: {exc}", "error")
            return False
        self.status.set_status(f"{len(rows):,} bars exported to {path}", "success")
        return True

    def show_help(self):
        from .help_dialog import HelpDialog
        dlg = getattr(self, "_help_dialog", None)
        if dlg is None:
            dlg = self._help_dialog = HelpDialog(self, topic="pivot")
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ====================================================================
    # CLOSING
    # ====================================================================

    def shutdown(self):
        self._closing = True
        self._sel_timer.stop()
        self.selector.new_generation()
        for sig, slot in ((self.pivot.model.modelReset, self.follow),
                          (self.pivot.model.layoutChanged, self._on_table_order)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        if self.layer is not None:
            try:
                self.layer.selectionChanged.disconnect(self._on_selection_changed)
            except (TypeError, RuntimeError):
                pass
        self.result = None
        self.bars = None
