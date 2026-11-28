# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Visualize Statistics: a chart of one column of an Attribute Explorer.

Opened from a column name's right-click menu or the statistics popup's
**Chart…** button; one chart per explorer, closed with it, shown as a
window or docked (:class:`.tool_host.ToolHost`).

The chart follows the explorer: its scope (filters, View scope, map
extent, drill-through) and edits; a click on a bar selects its records
through the layer's selection, so the map and the table follow.  Reading
the records runs in a background job (progress and Cancel in the status
bar) and keeps only compact columns (:mod:`..core.charts`), so layers with
millions of records chart without freezing QGIS or running out of memory;
changing bins, sorting or intervals never reads the layer again.
"""

import math

from qgis.PyQt.QtCore import QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QMessageBox, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QSplitter, QVBoxLayout, QWidget,
)
from qgis.core import QgsMessageLog, QgsProject
from qgis.gui import QgsColorButton

from ..core import charts as C
from ..core import export as X
from ..core import fields as F
from ..core.compat import SELECT_ADD, SELECT_SET
from ..core.jobs import JobRunner
from . import icons
from . import widgets as W
from .chart_view import MEAN_COLOUR, MEDIAN_COLOUR, ChartModel, ChartView
from .theme import Metrics, Theme, stylesheet

LOG_TAG = "Attribute Table Studio"
SYNC_LIMIT = 250000              # up to this many records, rebuild at once
# the Sort box, as ArcGIS Pro's: values (X) ascending / descending, records
# (Y) ascending / descending - most records first is the default - and custom
SORT_TEXT_LABELS = (("az", "A → Z"), ("za", "Z → A"), ("fewest", "Fewest records first"),
                    ("count", "Most records first"), ("custom", "Custom order…"))
SORT_NUMBER_LABELS = {"az": "Smallest value first", "za": "Largest value first"}
EXPR_KEY = "\x00expression"      # Split by item that holds an expression
TOP_CHOICES = (("Top 10 + Other", 10), ("Top 25 + Other", 25), ("All values", None))


def open_chart(explorer, logical=None):
    """Open (or raise) the chart window of *explorer* on column *logical*."""
    host = getattr(explorer, "chart_host", None)
    if host is not None:
        if logical is not None:
            host.panel.set_field(logical)
        host.show_and_raise()
        return host.panel
    from .tool_host import ToolHost
    panel = ChartPanel(explorer.iface, explorer, logical)
    host = ToolHost(explorer.iface, panel, "chart", icons.native(icons.CHART),
                    QSize(1180, 700))
    panel.titleChanged.connect(host.refresh_title)

    def closed(_host, exp=explorer):
        exp.chart_host = None

    host.closed.connect(closed)
    explorer.chart_host = host
    host.open()
    return panel


def close_chart(explorer):
    host = getattr(explorer, "chart_host", None)
    if host is not None:
        explorer.chart_host = None
        host.close()


def _decimals(width):
    if not width or width >= 10:
        return 0
    if width >= 1:
        return 1
    return min(8, int(math.ceil(-math.log10(width))) + 1)


def _stat_number(v):
    if v is None:
        return "—"
    if float(v).is_integer() and abs(v) < 1e15:
        return F.format_number(int(v))
    a = abs(v)
    places = 2 if a >= 1 else (4 if a >= 0.001 else 8)
    return F.format_number(round(v, places))


class ChartPanel(QWidget):
    titleChanged = pyqtSignal()

    def __init__(self, iface, explorer, logical=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.explorer = explorer
        self.layer = explorer.layer
        self.theme = Theme()
        self.metrics = Metrics(self.font())
        self.setObjectName("gtsPanel")
        self.setStyleSheet(stylesheet(self.theme, self.metrics))
        self.reader = JobRunner(self)        # reads the records
        self.builder = JobRunner(self)       # bins / categories / periods
        self.selector = JobRunner(self)      # selected records per bar
        self.picker = JobRunner(self)        # ids behind clicked bars
        self.col = None
        self.bars = None
        self.sel = None
        self.sel_series = None
        self._read_key = None
        self._custom_title = False
        self._closing = False
        self._waiting = False
        self._last_options = None
        self._build_ui()
        self._fill_fields()
        self._sel_timer = QTimer(self)
        self._sel_timer.setSingleShot(True)
        self._sel_timer.setInterval(120)
        self._sel_timer.timeout.connect(self._update_selection)
        self._build_timer = QTimer(self)
        self._build_timer.setSingleShot(True)
        self._build_timer.setInterval(140)
        self._build_timer.timeout.connect(self.rebuild)
        self._scope_timer = QTimer(self)
        self._scope_timer.setSingleShot(True)
        self._scope_timer.setInterval(300)
        self._scope_timer.timeout.connect(self._check_scope)
        self.reader.progress.connect(self._on_progress)
        explorer.scopeChanged.connect(self._on_scope_changed)
        self.layer.selectionChanged.connect(self._on_selection_changed)
        self.set_field(logical if logical is not None else 0)

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
        top.addWidget(W.label("Field", "dim"))
        self.field_combo = W.FieldCombo(m, t)
        self.field_combo.setMinimumWidth(180)
        self.field_combo.currentIndexChanged.connect(self._on_field_changed)
        top.addWidget(self.field_combo)
        top.addWidget(W.label("Chart", "dim"))
        self.kind_combo = QComboBox()
        self.kind_combo.setFixedHeight(m.control_h)
        self.kind_combo.setToolTip(
            "Histogram: vertical columns (numbers in bins, dates by period, text one "
            "column per value)\nBar chart: horizontal bars, one per value or period")
        for data, text in C.CHART_TYPES:
            self.kind_combo.addItem(icons.native(icons.CHART), text, data)
        self.kind_combo.currentIndexChanged.connect(self._on_chart_type_changed)
        top.addWidget(self.kind_combo)
        top.addSpacing(m.group_gap)
        self.scope_label = W.ElideLabel()
        self.scope_label.setObjectName("gtsDim")
        self.scope_label.setMinimumWidth(80)
        top.addWidget(self.scope_label, 1)
        self.selected_btn = W.square_button(
            m, icons.native(icons.SELECTED_ONLY),
            "Selected only: chart only the selected records", checkable=True)
        self.selected_btn.toggled.connect(lambda _on: self.load())
        top.addWidget(self.selected_btn)
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
        self.export_menu = menu
        self.export_btn = W.menu_square(m, icons.native(icons.EXPORT), "Export the chart",
                                        menu)
        top.addWidget(self.export_btn)
        self.help_btn = W.square_button(m, icons.native(icons.HELP), "Help")
        self.help_btn.clicked.connect(self.show_help)
        top.addWidget(self.help_btn)
        self.dock_btn = W.dock_button(m)
        top.addWidget(self.dock_btn)
        lay.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.view = ChartView(t, m)
        self.view.barsPicked.connect(self._on_bars_picked)
        self.view.seriesPicked.connect(self._on_series_picked)
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
        self.status.cancelRequested.connect(self._cancel)
        lay.addWidget(self.status)

    def _row(self, layout, text, widget, stretch=1):
        row = QHBoxLayout()
        row.setSpacing(self.metrics.gap)
        lbl = W.label(text, "dim")
        self._card_labels.append(lbl)            # one label column (align_labels)
        row.addWidget(lbl)
        row.addWidget(widget, stretch)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(holder)
        return holder

    def _combo(self, items):
        combo = QComboBox()
        combo.setFixedHeight(self.metrics.control_h)
        for text, data in items:
            combo.addItem(text, data)
        return combo

    def _tick(self, layout, text, on, colour=None, tip=""):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        box = QCheckBox(text)
        box.setChecked(on)
        if tip:
            box.setToolTip(tip)
        row.addWidget(box)
        row.addStretch(1)
        if colour is not None:
            swatch = QLabel("━ ━")
            swatch.setStyleSheet(f"color: {colour}; font-weight: 700;")
            row.addWidget(swatch)
        layout.addWidget(holder)
        return box, holder

    def _build_settings(self):
        t, m = self.theme, self.metrics
        self._card_labels = []
        card = QFrame()
        card.setObjectName("gtsCard")
        pl = QVBoxLayout(card)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(6)

        pl.addWidget(W.section_header("Data"))
        bins_row = QWidget()
        bl = QHBoxLayout(bins_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(m.gap)
        bins_label = W.label("Bins", "dim")
        self._card_labels.append(bins_label)
        bl.addWidget(bins_label)
        self.bins_slider = QSlider(Qt.Orientation.Horizontal)
        self.bins_slider.setRange(C.MIN_BINS, C.MAX_BINS)
        bl.addWidget(self.bins_slider, 1)
        self.bins_spin = QSpinBox()
        self.bins_spin.setRange(C.MIN_BINS, C.MAX_BINS)
        self.bins_spin.setFixedHeight(m.control_h)
        bl.addWidget(self.bins_spin)
        self.bins_slider.valueChanged.connect(self.bins_spin.setValue)
        self.bins_spin.valueChanged.connect(self.bins_slider.setValue)
        self.bins_spin.valueChanged.connect(self._schedule_build)
        pl.addWidget(bins_row)
        self.bins_row = bins_row

        self.sort_combo = QComboBox()
        self.sort_combo.setFixedHeight(m.control_h)
        self.sort_combo.setIconSize(QSize(m.icon, m.icon))
        for key, text in SORT_TEXT_LABELS:
            self.sort_combo.addItem(icons.sort_icon(key, t), text, key)
        self.sort_combo.setCurrentIndex(self.sort_combo.findData("count"))
        self._custom_orders = {}             # field -> [value labels]
        self.sort_combo.activated.connect(self._on_sort_activated)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        self.sort_row = self._row(pl, "Sort", self.sort_combo)
        self.top_combo = self._combo(TOP_CHOICES)
        self.top_combo.currentIndexChanged.connect(self._schedule_build)
        self.top_row = self._row(pl, "Show", self.top_combo)
        self.values_combo = self._combo((("Records", False), ("Share (%)", True)))
        self.values_combo.currentIndexChanged.connect(self._redraw)
        self.values_row = self._row(pl, "Values", self.values_combo)
        self.interval_combo = self._combo([("Auto", "auto")] + [
            (C.INTERVAL_LABELS[k], k) for k in C.INTERVALS])
        self.interval_combo.currentIndexChanged.connect(self._schedule_build)
        self.interval_row = self._row(pl, "Interval", self.interval_combo)
        self.scale_combo = self._combo((("Linear", "linear"), ("Logarithmic", "log"),
                                        ("Square root", "sqrt")))
        self.scale_combo.setToolTip("How the records axis grows: a logarithmic or square "
                                    "root scale shows small bars next to very tall ones")
        self.scale_combo.currentIndexChanged.connect(self._redraw)
        self._row(pl, "Scale", self.scale_combo)

        split_row = QWidget()
        sl = QHBoxLayout(split_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(m.gap)
        split_label = W.label("Split by", "dim")
        self._card_labels.append(split_label)
        sl.addWidget(split_label)
        self.split_combo = W.FieldCombo(m, t, none_label="None")
        self.split_combo.setMinimumContentsLength(4)
        self.split_combo.setMinimumWidth(80)
        self.split_combo.setToolTip(
            "Count each bar per value of a second field or an expression (the 8 most "
            "common values get their own colour, the rest form Other)")
        self.split_combo.currentIndexChanged.connect(self._on_split_changed)
        sl.addWidget(self.split_combo, 1)
        self.split_expr_btn = W.square_button(
            m, icons.native(icons.EXPRESSION),
            "Split by an expression…\nBuild one in the QGIS expression builder, e.g. "
            "\"AREA_M2\" > 1000 or year(\"SURVEY_DATE\")")
        self.split_expr_btn.clicked.connect(self.edit_split_expression)
        sl.addWidget(self.split_expr_btn)
        pl.addWidget(split_row)
        self._split_expr = None
        self.split_mode = self._combo((("Stacked", "stacked"), ("Side by side", "side")))
        self.split_mode.setIconSize(QSize(m.icon, m.icon))
        for i in range(self.split_mode.count()):          # the bars' shape beside each
            self.split_mode.setItemIcon(i, icons.option_icon(self.split_mode.itemData(i), t))
        self.split_mode.currentIndexChanged.connect(self._redraw)
        self.split_mode_row = self._row(pl, "Bars", self.split_mode)

        self.null_tick, self.null_row = self._tick(pl, "Count NULL as a bar", False)
        self.null_tick.toggled.connect(self._schedule_build)
        self.empty_tick, self.empty_row = self._tick(
            pl, "Show empty periods", True, tip="Keep periods without records as gaps")
        self.empty_tick.toggled.connect(self._schedule_build)

        self.lines_header = W.section_header("Statistics on the chart")
        pl.addWidget(self.lines_header)
        self.mean_tick, self.mean_row = self._tick(pl, "Mean", True, MEAN_COLOUR)
        self.median_tick, self.median_row = self._tick(pl, "Median", True, MEDIAN_COLOUR)
        self.std_tick, self.std_row = self._tick(pl, "± 1 Std. Dev.", False)
        self.normal_tick, self.normal_row = self._tick(
            pl, "Normal Distribution Curve", False,
            tip="The bell curve with the same mean and standard deviation")
        for box in (self.mean_tick, self.median_tick, self.std_tick, self.normal_tick):
            box.toggled.connect(self._redraw)

        pl.addWidget(W.section_header("Statistics"))
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
        # as wide as the boxes above it (the label column lines them up)
        self.colour_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.colour_btn.setToolTip("Colour of the bars")
        self.colour_btn.colorChanged.connect(self._redraw)
        self.colour_row = self._row(pl, "Bar colour", self.colour_btn)
        self.labels_tick, _r = self._tick(pl, "Label bars with their count", False)
        self.grid_tick, _r = self._tick(pl, "Grid lines", True)
        for box in (self.labels_tick, self.grid_tick):
            box.toggled.connect(self._redraw)
        pl.addStretch(1)
        W.align_labels(self._card_labels)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(card)
        scroll.setFixedWidth(312)
        return scroll

    # ====================================================================
    # FIELD AND OPTIONS
    # ====================================================================

    def _fields(self):
        return self.layer.fields()

    def _fill_fields(self):
        fields = self._fields()
        self.field_combo.blockSignals(True)
        self.field_combo.set_fields(fields)
        self.field_combo.blockSignals(False)
        self._fill_split()

    def _fill_split(self):
        current = self.split_combo.current_name()
        self.split_combo.blockSignals(True)
        self.split_combo.set_fields(self._fields(), keep=False)
        name = self.field_combo.current_name()
        idx = self.split_combo.findData(name)
        if idx > 0:
            self.split_combo.removeItem(idx)
        if self._split_expr:                 # the expression stays offered
            self._add_expression_item()
        if current and current != name:
            self.split_combo.set_current_name(current)
        self.split_combo.blockSignals(False)
        self.split_mode_row.setVisible(self.split_combo.current_name() is not None)

    def _add_expression_item(self):
        idx = self.split_combo.findData(EXPR_KEY)
        text = self._split_expr if len(self._split_expr) <= 40 \
            else self._split_expr[:39] + "…"
        if idx < 0:
            self.split_combo.addItem(icons.native(icons.EXPRESSION), text, EXPR_KEY)
            idx = self.split_combo.count() - 1
        else:
            self.split_combo.setItemText(idx, text)
        self.split_combo.setItemData(idx, self._split_expr, Qt.ItemDataRole.ToolTipRole)
        return idx

    def edit_split_expression(self):
        """Split by an expression built in the QGIS expression builder."""
        from .explorer_dialogs import ExpressionDialog
        current = self._split_expr or ""
        if not current:
            name = self.split_combo.current_name()
            current = f'"{name}"' if name and name != EXPR_KEY else ""
        dlg = ExpressionDialog(self, "Split by an expression", current, order=False)
        if not dlg.exec():
            return
        self._split_expr = dlg.expression()
        idx = self._add_expression_item()
        if self.split_combo.currentIndex() == idx:
            self._on_split_changed()             # a new expression: read again
        else:
            self.split_combo.setCurrentIndex(idx)

    def split_choice(self):
        """(field name, expression, display name) of Split by; all None
        without a split."""
        data = self.split_combo.current_name()
        if data is None:
            return None, None, None
        if data == EXPR_KEY:
            return None, self._split_expr, self._split_expr
        return data, None, data

    def on_schema_changed(self):
        """Fields renamed, added or removed in the explorer."""
        name = self.field_combo.current_name()
        self._fill_fields()
        if name and self.field_combo.set_current_name(name):
            return
        self._on_field_changed()

    def set_field(self, logical):
        fields = self._fields()
        if logical is None or not 0 <= logical < fields.count():
            logical = 0
        name = fields.at(logical).name() if fields.count() else None
        if name is not None and name == self.field_combo.current_name() \
                and self.col is not None:
            return
        self.field_combo.blockSignals(True)
        self.field_combo.set_current_name(name)
        self.field_combo.blockSignals(False)
        self._on_field_changed()

    def field_name(self):
        return self.field_combo.current_name()

    def field_kind(self):
        return self.field_combo.kind()

    def chart_type(self):
        """The Chart box: "histogram" (vertical) or "bar" (horizontal)."""
        return self.kind_combo.currentData() or "histogram"

    def chart(self):
        """The bars drawn: histogram bins, one per value, or periods."""
        kind = self.field_kind()
        return C.bars_kind(kind, self.chart_type()) if kind is not None else None

    def window_title(self):
        name = self.field_name() or ""
        layer = self.layer.name() if self.layer is not None else ""
        return f"Visualize Statistics — {name} ({layer})"

    def _on_field_changed(self, *_args):
        self._custom_title = False
        kind = self.field_kind()
        if kind is not None:                 # each field starts on its usual chart
            self.kind_combo.blockSignals(True)
            self.kind_combo.setCurrentIndex(
                self.kind_combo.findData(C.default_chart_type(kind)))
            self.kind_combo.blockSignals(False)
        self.colour_btn.blockSignals(True)
        self.colour_btn.setColor(QColor(self.theme.bar))
        self.colour_btn.blockSignals(False)
        self._fill_split()
        self._show_controls()
        self.titleChanged.emit()
        self.col = None
        self.load()

    def _sort_icons_follow_chart(self):
        """Sort icons and tooltip turn with the chart: in a bar chart the
        values run down the left (Y axis) and the records along the bottom
        (X axis); in a histogram the other way round."""
        horizontal = self.chart_type() == "bar"
        for i in range(self.sort_combo.count()):
            key = self.sort_combo.itemData(i)
            self.sort_combo.setItemIcon(i, icons.sort_icon(key, self.theme, horizontal))
        if horizontal:
            axes = ("Bar chart: the values run down the left (Y axis), the records "
                    "along the bottom (X axis).\n"
                    "A → Z / Z → A: the values from top to bottom (Y axis)\n"
                    "Fewest / Most records first: by bar length (X axis ascending / "
                    "descending)")
        else:
            axes = ("Histogram: the values run along the bottom (X axis), the records "
                    "up the side (Y axis).\n"
                    "A → Z / Z → A: the values from left to right (X axis)\n"
                    "Fewest / Most records first: by column height (Y axis ascending / "
                    "descending)")
        self.sort_combo.setToolTip(axes + "\nCustom order…: put the values in your own "
                                   "order")

    def _on_sort_activated(self, _index):
        """Custom order… opens its window (again, to change the order);
        cancelling it the first time goes back to the previous sort."""
        if self.sort_combo.currentData() == "custom":
            if not self.edit_custom_order() and not self._custom_orders.get(self.field_name()):
                self.sort_combo.blockSignals(True)
                self.sort_combo.setCurrentIndex(self.sort_combo.findData(
                    getattr(self, "_last_sort", "count")))
                self.sort_combo.blockSignals(False)
                self._schedule_build()
                return
        self._last_sort = self.sort_combo.currentData()

    def _on_sort_changed(self, *_args):
        if self.sort_combo.currentData() == "custom" \
                and not self._custom_orders.get(self.field_name()):
            return                           # its window comes first (activated)
        self._schedule_build()

    def custom_order_entries(self):
        """[(label, records)] of the field's values in the current custom
        order (else most records first), at most 1,000."""
        col = self.col
        if col is None:
            return []
        C.ensure_categories(col)
        entries = sorted(((label, n) for label, n in zip(col.labels, col.counts) if n),
                         key=lambda e: -e[1])[:1000]
        order = self._custom_orders.get(self.field_name())
        if order:
            pos = {label: i for i, label in enumerate(order)}
            entries.sort(key=lambda e: (pos.get(e[0], len(pos)), -e[1]))
        return entries

    def edit_custom_order(self):
        """The Custom order window; True when a new order was set."""
        from .explorer_dialogs import CustomOrderDialog
        entries = self.custom_order_entries()
        if not entries:
            return False
        dlg = CustomOrderDialog(self, self.field_name(), entries)
        if not dlg.exec():
            return False
        self._custom_orders[self.field_name()] = dlg.order()
        self._last_sort = "custom"
        self._last_options = None            # the order changed: draw again
        self._schedule_build()
        return True

    def _on_chart_type_changed(self, *_args):
        self._show_controls()
        self._schedule_build()
        self._redraw()

    def _on_split_changed(self, *_args):
        self.split_mode_row.setVisible(self.split_combo.current_name() is not None)
        self._show_controls()
        self.load()

    def _show_controls(self):
        chart = self.chart()
        kind = self.field_kind()
        hist = chart == C.HISTOGRAM
        numbers = hist and kind != F.TIME
        as_text = kind not in F.NUMERIC and kind != F.TIME
        for key, text in SORT_TEXT_LABELS[:2]:
            self.sort_combo.setItemText(self.sort_combo.findData(key),
                                        text if as_text else SORT_NUMBER_LABELS[key])
        self._sort_icons_follow_chart()
        self.bins_row.setVisible(hist)
        for w in (self.sort_row, self.top_row, self.values_row, self.null_row):
            w.setVisible(chart == C.BARS)
        for w in (self.interval_row, self.empty_row):
            w.setVisible(chart == C.TIME)
        for w in (self.lines_header, self.mean_row, self.median_row, self.std_row,
                  self.normal_row):
            w.setVisible(numbers)
        split = self.split_combo.current_name() is not None
        self.colour_btn.setEnabled(not split)
        self.colour_btn.setToolTip("Split by: each category has its own colour" if split
                                   else "Colour of the bars")

    def _on_title_edited(self, _text):
        self._custom_title = True
        self._redraw()

    def _default_title(self):
        name = self.field_name() or ""
        split = self.split_choice()[2]
        chart = self.chart()
        if chart == C.HISTOGRAM:
            title = f"Distribution of {name}"
        elif chart == C.TIME:
            title = f"Records over time: {name}"
        else:
            title = f"Records by {name}"
        if split:
            title += f" by {split}" if chart != C.BARS else f" and {split}"
        return title

    def _options(self):
        return {"chart": self.chart_type(),
                "bins": self.bins_spin.value(),
                "sort": self.sort_combo.currentData(),
                "order": tuple(self._custom_orders.get(self.field_name(), ())),
                "top": self.top_combo.currentData(),
                "null": self.null_tick.isChecked(),
                "interval": self.interval_combo.currentData(),
                "empty": self.empty_tick.isChecked()}

    @staticmethod
    def make_bars(col, opts):
        kind = C.bars_kind(col.kind, opts.get("chart") or C.default_chart_type(col.kind))
        if kind == C.HISTOGRAM:
            bars = C.histogram(col, opts["bins"])
        elif kind == C.TIME:
            bars = C.periods(col, opts["interval"], opts["empty"])
        else:
            bars = C.categories(col, opts["sort"], opts["top"], opts["null"],
                                opts.get("order"))
        return C.apply_split(col, bars)

    # ====================================================================
    # READING
    # ====================================================================

    def load(self):
        """Read the explorer's scope for the current field (background)."""
        if self._closing or self.layer is None:
            return
        exp = self.explorer
        name = self.field_name()
        if name is None:
            self.view.set_message("This layer has no fields to chart")
            return
        if not getattr(exp, "_has_result", False) or exp.runner.running("scan"):
            self._waiting = True
            self.view.set_message("Waiting for the Attribute Explorer…")
            return
        self._waiting = False
        fields = self._fields()
        idx = fields.indexOf(name)
        split_field, split_expr, split = self.split_choice()
        split_idx = fields.indexOf(split_field) if split_field else None
        selected_only = self.selected_btn.isChecked()
        spec = exp.chart_spec(selected_only)
        if spec is None:
            return
        self._read_key = self._scope_key()
        kind = self.field_kind()
        for runner in (self.reader, self.builder, self.selector, self.picker):
            runner.new_generation()
        sel_ids = self._selected_ids()
        opts = self._options()
        if C.chart_kind(kind) == C.HISTOGRAM and self.col is None:
            opts["bins"] = None              # the default for the data

        def work(job, spec=spec, idx=idx, kind=kind, name=name, split_idx=split_idx,
                 split=split, split_expr=split_expr, opts=opts, sel_ids=sel_ids):
            col = C.read(spec, job, idx, kind, name, split_idx, split, split_expr)
            if opts["bins"] is None:
                st = col.stats
                opts["bins"] = C.default_bins(col.size, kind, st.get("min"), st.get("max"))
            bars = self.make_bars(col, opts)
            job.check()
            sel, sel_series = C.selected_counts(col, bars, sel_ids)
            return col, bars, sel, sel_series, opts

        self.view.set_message(f"Reading {self.layer.name()}…")
        self.status.set_status(f"Reading {name} of {self.layer.name()}…", "busy")
        self.status.set_busy(True)
        self.reader.start(f"Visualize Statistics: {name}", work, self._on_read_done,
                          tag="read")

    def _scope_key(self):
        exp = self.explorer
        key = (getattr(exp, "_scope_key", None), self.field_name(),
               self.split_choice()[2], self.selected_btn.isChecked())
        if self.selected_btn.isChecked():
            key += (getattr(exp, "_selection_version", 0),)
        return key

    def _on_progress(self, pct):
        if self._closing or not self.reader.busy:
            return
        est = 0
        try:
            est = max(self.layer.featureCount(), 0)
        except RuntimeError:
            return
        text = f"Reading {self.layer.name()}… {pct:.0f}%"
        if est:
            text += f"  ({int(est * pct / 100.0):,} of about {est:,} records)"
        self.view.set_message(text)
        self.status.set_status(text, "busy")

    def _on_read_done(self, job):
        if self._closing or not self.reader.is_current(job):
            return
        self.status.set_busy(False)
        if job.error:
            QgsMessageLog.logMessage(f"Visualize Statistics failed:\n{job.error}", LOG_TAG)
            last = job.error.strip().splitlines()[-1]
            self.view.set_message(f"The chart could not be made: {last}")
            self.status.set_status(f"Reading failed: {last}", "error")
            return
        if job.cancelled:
            self.view.set_message("Reading cancelled")
            self.status.set_status("Reading cancelled", "warning", ("Read again", self.load))
            return
        col, bars, sel, sel_series, opts = job.result
        self.col = col
        if col.chart == C.HISTOGRAM:
            self.bins_spin.blockSignals(True)
            self.bins_slider.blockSignals(True)
            self.bins_spin.setValue(int(opts["bins"]))
            self.bins_slider.setValue(int(opts["bins"]))
            self.bins_spin.blockSignals(False)
            self.bins_slider.blockSignals(False)
        self._last_options = self._options()
        self._apply_bars(bars, sel, sel_series)
        self._fill_stats()

    def _cancel(self):
        for runner in (self.reader, self.builder, self.selector, self.picker):
            runner.cancel_all()

    def _on_scope_changed(self):
        self._scope_timer.start()

    def _check_scope(self):
        if self._closing:
            return
        if self._waiting or self._scope_key() != self._read_key:
            self.load()
        else:
            self._update_scope_label()

    # ====================================================================
    # BARS
    # ====================================================================

    def _schedule_build(self, *_args):
        if self.col is None:
            return
        if self.col.size <= SYNC_LIMIT:
            self.rebuild()
        else:
            self._build_timer.start()

    def rebuild(self):
        """Bins, categories or periods again from the kept columns."""
        col = self.col
        if col is None or self._closing:
            return
        opts = self._options()
        if opts == self._last_options and self.bars is not None:
            return
        self._last_options = opts
        sel_ids = self._selected_ids()
        if col.size <= SYNC_LIMIT:
            bars = self.make_bars(col, opts)
            sel, sel_series = C.selected_counts(col, bars, sel_ids)
            self._apply_bars(bars, sel, sel_series)
            return
        self.builder.new_generation()

        def work(job, col=col, opts=opts, sel_ids=sel_ids):
            bars = self.make_bars(col, opts)
            job.check()
            return bars, *C.selected_counts(col, bars, sel_ids)

        def done(job):
            if self._closing or not self.builder.is_current(job) or job.result is None:
                if job.error:
                    self.status.set_status(job.error.strip().splitlines()[-1], "error")
                return
            if col is self.col:
                self._apply_bars(*job.result)

        self.status.set_status("Updating the chart…", "busy")
        self.builder.start("Visualize Statistics: bars", work, done, tag="build")

    def _apply_bars(self, bars, sel, sel_series):
        self.bars = bars
        self.sel, self.sel_series = sel, sel_series
        if bars.chart == C.TIME:
            self.interval_combo.setItemText(
                0, f"Auto ({C.INTERVAL_LABELS.get(bars.interval, '')})")
            self._fill_stats()                   # the busiest period follows the interval
        self._redraw()
        self._update_scope_label()

    def _redraw(self, *_args):
        if self._closing:
            return
        if not self._custom_title:
            self.title_edit.blockSignals(True)
            self.title_edit.setText(self._default_title())
            self.title_edit.setCursorPosition(0)
            self.title_edit.blockSignals(False)
        if self.col is None or self.bars is None:
            return
        self.view.set_model(self._model())
        self._update_status()

    def _model(self):
        col, bars = self.col, self.bars
        m = ChartModel()
        m.bars = bars
        m.stats = col.stats or {}
        m.title = self.title_edit.text()
        name = col.name
        kind = col.kind
        m.horizontal = self.chart_type() == "bar"
        if bars.chart == C.TIME:
            m.x_title = f"{name} (by {C.INTERVAL_LABELS.get(bars.interval, '').lower()})"
        else:
            m.x_title = "" if m.horizontal else name
        if kind == F.TIME and bars.chart == C.HISTOGRAM:
            m.fmt_axis = lambda v: F.key_to_text(v, F.TIME)
        elif bars.chart == C.HISTOGRAM and bars.integer:
            m.fmt_axis = lambda v: F.plain_number(round(v))   # a column per value
        elif bars.chart == C.HISTOGRAM:
            d = _decimals(bars.width)
            m.fmt_axis = lambda v, d=d: F.format_number(round(v, d) if d else round(v))
        m.fmt_bar = self._bar_title
        shown = set()
        if self.mean_tick.isChecked():
            shown.add("mean")
        if self.median_tick.isChecked():
            shown.add("median")
        if self.std_tick.isChecked():
            shown.add("std")
        if self.normal_tick.isChecked():
            shown.add("normal")
        m.show = shown if (bars.chart == C.HISTOGRAM and kind != F.TIME) else set()
        m.fmt_stat = _stat_number
        m.scale = self.scale_combo.currentData()
        m.labels = self.labels_tick.isChecked()
        m.grid = self.grid_tick.isChecked()
        colour = self.colour_btn.color()
        m.colour = colour if colour.isValid() and colour != QColor(self.theme.bar) else None
        m.split_mode = self.split_mode.currentData()
        m.share = bars.chart == C.BARS and bool(self.values_combo.currentData())
        m.sel = self.sel
        m.sel_series = self.sel_series
        project = QgsProject.instance()
        m.sel_colour = QColor(project.selectionColor()) if project.selectionColor().isValid() \
            else QColor("#ffff00")
        return m

    def _bar_title(self, i):
        bars, col = self.bars, self.col
        if bars.chart == C.BARS:
            return str(bars.labels[i])
        if bars.chart == C.TIME:
            lo, hi = bars.lows[i], bars.highs[i]
            if bars.interval == "day":
                return F.key_to_text(lo, F.DATE)
            return (f"{bars.labels[i]}  ({F.key_to_text(lo, F.DATE)} – "
                    f"{F.key_to_text(hi, F.DATE)})")
        lo, hi = bars.lows[i], bars.highs[i]
        if col.kind == F.TIME:
            fmt = lambda v: F.key_to_text(v, F.TIME)       # noqa: E731
        elif bars.integer:                   # a column per value: the value itself
            fmt = F.plain_number
        else:
            d = max(_decimals(bars.width), 1) if not bars.integer else 0
            fmt = lambda v, d=d: F.format_number(round(v, d))   # noqa: E731
        if bars.integer or lo == hi:
            return fmt(lo)
        last = i == len(bars) - 1
        return f"{fmt(lo)} – {fmt(hi)}" + ("  (maximum included)" if last else "")

    # ====================================================================
    # STATUS AND STATISTICS
    # ====================================================================

    def _update_scope_label(self):
        exp = self.explorer
        try:
            available = max(self.layer.featureCount(), 0)
        except RuntimeError:
            return
        scope = exp.scope_name() if hasattr(exp, "scope_name") else "Records"
        n = self.col.scope if self.col is not None else len(exp.model.fids)
        parts = [scope, f"{n:,} of {available:,}"]
        filters = exp._filters_summary() if hasattr(exp, "_filters_summary") else ""
        if filters:
            parts.append(filters)
        if self.selected_btn.isChecked():
            parts.append("Selected only")
        text = "  •  ".join(parts)
        self.scope_label.setText(text)
        self.scope_label.setToolTip("The chart shows the records of the Attribute "
                                    "Explorer:\n" + text)

    def _update_status(self):
        bars, col = self.bars, self.col
        if bars is None or col is None:
            return
        parts = [self.kind_combo.currentText()]
        if not len(bars):
            self.status.set_status("  •  ".join(parts + ["No values to chart"]))
            self.view.set_message(f"{col.name} has no values to chart in these "
                                  f"{col.scope:,} records")
            return
        if bars.chart == C.HISTOGRAM:
            if bars.integer:
                parts.append(f"{len(bars):,} values")
            else:
                d = max(_decimals(bars.width), 1)
                parts.append(f"{len(bars):,} bins of {F.format_number(round(bars.width, d))}")
        elif bars.chart == C.TIME:
            unit = C.INTERVAL_LABELS.get(bars.interval, "").lower()
            parts.append(f"{len(bars):,} {unit}{'s' if len(bars) != 1 else ''}")
        else:
            unit = "bar" if self.chart_type() == "bar" else "column"
            parts.append(f"{len(bars):,} {unit}{'s' if len(bars) != 1 else ''}")
        n_sel = sum(self.sel) if self.sel else 0
        if n_sel:
            parts.append(f"{n_sel:,} of {bars.total:,} records selected")
        else:
            parts.append("Click a bar to select its records, drag for a range")
        if col.capped:
            parts.append(f"More than {C.CATEGORY_CAP:,} values: values found after that "
                         "are counted in Other")
        if bars.note:
            parts.append(bars.note)
        self.status.set_status("  •  ".join(parts))
        nothing = col.size == 0
        if nothing:
            self.view.set_message(f"{col.name} has no values to chart in these "
                                  f"{col.scope:,} records")

    def _fill_stats(self):
        grid = self.stats_grid
        while grid.count():
            item = grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        col = self.col
        if col is None:
            return
        st = col.stats or {}
        rows = [("Records", F.format_number(st.get("records", 0))),
                ("Count (non-NULL)", F.format_number(st.get("count", 0))),
                ("NULL", F.format_number(st.get("null", 0)))]
        if st.get("invalid"):
            rows.append(("Invalid", F.format_number(st["invalid"])))
        if col.chart == C.BARS:
            distinct = F.format_number(st.get("distinct", 0))
            if st.get("distinct_capped"):
                distinct = f"over {F.format_number(C.CATEGORY_CAP)}"
            rows.append(("Distinct", distinct))
            if st.get("most"):
                rows.append(("Most common", f"{st['most'][0]} ({st['most'][1]:,})"))
                rows.append(("Least common", f"{st['least'][0]} ({st['least'][1]:,})"))
        elif col.chart == C.TIME or col.kind == F.TIME:
            if st.get("min") is not None:
                rows.append(("Earliest", F.key_to_text(st["min"], col.kind)))
                rows.append(("Latest", F.key_to_text(st["max"], col.kind)))
            if col.chart == C.TIME and self.bars is not None:
                busy = C.busiest(self.bars)
                if busy:
                    unit = C.INTERVAL_LABELS.get(self.bars.interval, "Period")
                    rows.append((f"Busiest {unit.lower()}", f"{busy[0]} ({busy[1]:,})"))
        else:
            for label, key in (("Minimum", "min"), ("Maximum", "max"), ("Sum", "sum"),
                               ("Mean", "mean"), ("Median", "median"), ("Std dev", "std")):
                rows.append((label, _stat_number(st.get(key))))
            for label, key in (("Skewness", "skewness"), ("Kurtosis", "kurtosis")):
                v = st.get(key)
                rows.append((label, "—" if v is None else F.format_number(round(v, 2))))
        tips = {"Std dev": "Standard deviation (population), as in the Explorer",
                "Skewness": "0 for a symmetric distribution; above 0, a long tail of "
                            "high values",
                "Kurtosis": "3 for a normal distribution; above 3, more extreme values",
                "Invalid": "Values that cannot be charted (NaN, infinity, unreadable)"}
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

    def _selected_ids(self):
        """The layer's selected feature ids.  The explorer already keeps them
        as a set it replaces (never changes) on every selection, so a big
        selection is not copied out of QGIS again for the chart."""
        exp = self.explorer
        model = getattr(exp, "model", None)
        if model is not None and getattr(exp, "layer", None) is self.layer:
            return model.selected
        return self.layer.selectedFeatureIds()

    def _on_selection_changed(self, *_args):
        if self.col is not None and not self._closing:
            self._sel_timer.start()

    def _update_selection(self):
        col, bars = self.col, self.bars
        if col is None or bars is None or self._closing:
            return
        try:
            sel_ids = self._selected_ids()
        except RuntimeError:
            return
        if col.size <= SYNC_LIMIT:
            self.sel, self.sel_series = C.selected_counts(col, bars, sel_ids)
            self._redraw()
            return
        self.selector.new_generation()

        def work(job, col=col, bars=bars, sel_ids=sel_ids):
            return C.selected_counts(col, bars, sel_ids)

        def done(job):
            if self._closing or not self.selector.is_current(job) or job.result is None:
                return
            if bars is self.bars:
                self.sel, self.sel_series = job.result
                self._redraw()

        self.selector.start("Visualize Statistics: selection", work, done, tag="sel")

    def _select(self, bar_indexes, add, series=None):
        col, bars = self.col, self.bars
        if col is None or bars is None:
            return
        behaviour = SELECT_ADD if add else SELECT_SET

        def apply(fids):
            if self._closing:
                return
            try:
                self.layer.selectByIds(fids, behaviour)
            except RuntimeError:
                pass

        if col.size <= SYNC_LIMIT:
            apply(C.fids_of(col, bars, bar_indexes, series))
            return
        self.picker.new_generation()

        def work(job, col=col, bars=bars):
            return C.fids_of(col, bars, bar_indexes, series)

        def done(job):
            if not self._closing and self.picker.is_current(job) and job.result is not None:
                apply(job.result)
                self._update_status()

        self.status.set_status("Selecting…", "busy")
        self.picker.start("Visualize Statistics: select", work, done, tag="pick")

    def _on_bars_picked(self, bar_indexes, add, series):
        self._select(bar_indexes, add, series)

    def _on_series_picked(self, series, add):
        if self.bars is not None:
            self._select(list(range(len(self.bars))), add, series)

    def _clear_selection(self):
        try:
            self.layer.removeSelection()
        except RuntimeError:
            pass

    # ====================================================================
    # EXPORT, HELP
    # ====================================================================

    def _safe_name(self):
        raw = f"{self.layer.name()}_{self.field_name()}"
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
        bars, col = self.bars, self.col
        if bars is None:
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, "Export the bars", f"{self._safe_name()}_bars.csv", "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        header = ["Bar"]
        if bars.chart != C.BARS:
            header += ["From", "To"]
        header += ["Records", "Share (%)", "Selected"]
        header += [f"{col.split_name}: {s}" for s in bars.series] if bars.split else []
        rows = C.table_rows(bars, self.sel or [0] * len(bars))
        if bars.chart == C.TIME:
            for row in rows:
                row[1] = F.key_to_text(row[1], F.DATE)
                row[2] = F.key_to_text(row[2], F.DATE)
        elif col.kind == F.TIME and bars.chart == C.HISTOGRAM:
            for row in rows:
                row[0] = F.key_to_text(row[0], F.TIME)
                row[1] = F.key_to_text(row[1], F.TIME)
                row[2] = F.key_to_text(row[2], F.TIME)
        exp = self.explorer
        meta = {"Layer": self.layer.name(), "Field": col.name,
                "Chart": self.kind_combo.currentText(),
                "Statistics for": exp.scope_name(),
                "Active filters": "; ".join(getattr(exp, "_filters_text", [])) or "None",
                "Records in scope": col.scope}
        if self.selected_btn.isChecked():
            meta["Selected only"] = "Yes"
        if col.split_name:
            meta["Split by"] = col.split_name
        if bars.chart == C.HISTOGRAM and not bars.integer:
            meta["Bins"] = f"{len(bars)} of {bars.width}"
        if bars.chart == C.TIME:
            meta["Interval"] = C.INTERVAL_LABELS.get(bars.interval, "")
        try:
            X.write_csv(path, header, rows, meta)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            self.status.set_status(f"Bars export failed: {exc}", "error")
            return
        self.status.set_status(f"{len(rows):,} bars exported to {path}", "success")

    def show_help(self):
        from .help_dialog import HelpDialog
        dlg = getattr(self, "_help_dialog", None)
        if dlg is None:
            dlg = self._help_dialog = HelpDialog(self, topic="chart")
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ====================================================================
    # CLOSING
    # ====================================================================

    def shutdown(self):
        self._closing = True
        for timer in (self._sel_timer, self._build_timer, self._scope_timer):
            timer.stop()
        for runner in (self.reader, self.builder, self.selector, self.picker):
            runner.new_generation()
        try:
            self.explorer.scopeChanged.disconnect(self._on_scope_changed)
        except (TypeError, RuntimeError):
            pass
        try:
            self.layer.selectionChanged.disconnect(self._on_selection_changed)
        except (TypeError, RuntimeError):
            pass
        self.col = None
        self.bars = None
