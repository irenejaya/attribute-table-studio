# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Pivot Table - grouped summaries and cross tabs in a separate window.

Input scope (plan, "Pivot Table behaviour and calculation rules"):
All available records, Explorer filtered records or Selected records.  The
feature ids of the scope are captured when the input is set / refreshed;
Build Pivot calculates on that captured input.  Any later change to the
layer, the selection or the Explorer filter marks the input stale ("Source
changed? Refresh input then rebuild") and drill-through is blocked until
the pivot is rebuilt.

Calculation rules live in :mod:`core.pivot` (totals from contributing
records, NULL groups separate from the text "NULL", missing cross-tab
cells NULL / Count 0).
"""

import json

from qgis.PyQt.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QPen
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMenu, QMessageBox, QScrollArea, QSizePolicy,
    QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTableView, QVBoxLayout, QWidget,
)
from qgis.core import (
    QgsFeature, QgsFields, QgsMessageLog, QgsProject,
    QgsVectorFileWriter, QgsVectorLayer,
)

from ..core import export as X
from ..core import fields as F
from ..core import filters as FL
from ..core import pivot as P
from ..core import settings
from ..core.compat import TASK_VISIBLE, VECTOR_LAYER_FILTER, enum_int
from ..core.jobs import JobRunner
from ..core.scan import ScanSpec, scan
from . import icons
from . import widgets as W
from .theme import Metrics, Theme, stylesheet

LOG_TAG = "Attribute Table Studio"
PROJECT_SCOPE = "AttributeTableStudio"
PROJECT_LAYOUTS = "pivotLayouts"
_RIGHT = enum_int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_LEFT = enum_int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

SCOPE_LABELS = {
    "all": "All available records",
    "explorer": "Explorer filtered records",
    "selected": "Selected records",
}


# ============================================================================
# RESULT MODEL
# ============================================================================

class PivotModel(QAbstractTableModel):
    """Displays PivotResult.table(); the total row stays at the bottom."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.result = None
        self.header = []
        self.rows = []
        self.total = None
        self.decimals = 2
        self._value = QColor(theme.accent_text)
        self._null = QColor(theme.data_null)
        self._bold = QFont()
        self._bold.setBold(True)
        self._italic = QFont()
        self._italic.setItalic(True)

    def set_result(self, result, decimals):
        self.beginResetModel()
        self.result = result
        self.decimals = decimals
        if result is None:
            self.header, self.rows, self.total = [], [], None
        else:
            self.header, self.rows, self.total = result.table()
        self.endResetModel()

    def all_rows(self):
        return self.rows + ([self.total] if self.total is not None else [])

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.all_rows())

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.header)

    def cell(self, row, col):
        return self.all_rows()[row][col]

    def is_total(self, row):
        return self.total is not None and row == len(self.rows)

    def text(self, row, col):
        cell = self.cell(row, col)
        kind = self.header[col]["kind"]
        value = cell["value"]
        if kind == "key":
            if cell.get("total"):
                return value
            return F.NULL_TEXT if value is None else F.display_value(value)
        if value is None:
            return F.NULL_TEXT
        if kind == "count":
            return f"{value:,}"
        agg = self.result.spec.measures[self.header[col]["measure"]].agg
        if agg in P.COUNT_AGGS or isinstance(value, int):
            return F.format_number(value)
        if agg in ("min", "max", "sum") and float(value).is_integer():
            return F.format_number(int(value))
        return F.format_number(value, self.decimals)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        kind = self.header[col]["kind"]
        cell = self.cell(row, col)
        if role == Qt.ItemDataRole.DisplayRole:
            return self.text(row, col)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _LEFT if kind == "key" else _RIGHT
        if role == Qt.ItemDataRole.ForegroundRole:
            if cell["value"] is None and not cell.get("total"):
                return self._null
            if kind == "value":
                return self._value
            return None
        if role == Qt.ItemDataRole.FontRole:
            if self.is_total(row):
                return self._bold
            if cell["value"] is None and not cell.get("total"):
                return self._italic
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            return "Double-click to inspect the contributing records"
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal or not self.header:
            return None
        col = self.header[section]
        spec = self.result.spec if self.result is not None else None
        if role == Qt.ItemDataRole.DisplayRole:
            if col["group"]:
                if len(spec.measures) == 1:      # the title names the measure
                    return col["group"]
                return f"{col['group']}\n{col['label']}"
            if col["kind"] == "key" and spec is not None and spec.cols \
                    and col["label"] == spec.rows[-1]:
                return f"{col['label']}  ╲  {', '.join(spec.cols)}"
            return col["label"]
        if role == Qt.ItemDataRole.ToolTipRole and col["group"]:
            return f"{', '.join(spec.cols)} = {col['group']}\n{col['label']}"

        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _LEFT if col["kind"] == "key" else _RIGHT
        return None

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if not self.rows or column >= len(self.header):
            return
        self.layoutAboutToBeChanged.emit()
        desc = order == Qt.SortOrder.DescendingOrder
        present = [r for r in self.rows if r[column]["value"] is not None]
        missing = [r for r in self.rows if r[column]["value"] is None]
        present.sort(key=lambda r: F.sort_key(r[column]["value"]), reverse=desc)
        self.rows = present + missing
        self.layoutChanged.emit()

    def as_table(self, raw=True):
        """(group header row or None, header row, body rows) for export."""
        groups = [c["group"] for c in self.header]
        labels = [c["label"] for c in self.header]
        body = []
        for r, row in enumerate(self.all_rows()):
            body.append([c["value"] if raw else self.text(r, i)
                         for i, c in enumerate(row)])
        return (groups if any(groups) else None), labels, body


class _PivotDelegate(QStyledItemDelegate):
    """Banded rows, padded text, and a divider above the Grand total row."""

    PAD = 10

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self._base = QColor(theme.base)
        self._alt = QColor(theme.base_alt)
        self._total = QColor(theme.header)
        self._line = QColor(theme.text_dim)
        self._grid = QColor(theme.border_soft)
        self._sel_bg = QColor(theme.selected)
        self._sel_fg = QColor(theme.selected_text)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        model = index.model()
        row = index.row()
        total = model.is_total(row)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.save()
        if selected:
            bg = self._sel_bg
        elif total:
            bg = self._total
        else:
            bg = self._alt if row % 2 else self._base
        painter.fillRect(opt.rect, bg)
        painter.setPen(QPen(self._grid, 1))
        painter.drawLine(opt.rect.topRight(), opt.rect.bottomRight())
        if total:
            painter.setPen(QPen(self._line, 2))
            painter.drawLine(opt.rect.left(), opt.rect.top() + 1,
                             opt.rect.right(), opt.rect.top() + 1)
        painter.setPen(self._sel_fg if selected
                       else opt.palette.color(opt.palette.ColorRole.Text))
        painter.setFont(opt.font)
        rect = opt.rect.adjusted(self.PAD, 0, -self.PAD, 0)
        text = opt.fontMetrics.elidedText(opt.text, Qt.TextElideMode.ElideRight,
                                          rect.width())
        painter.drawText(rect, opt.displayAlignment, text)
        painter.restore()


# ============================================================================
# BUILDER WIDGETS
# ============================================================================

class _FieldRow(QWidget):
    """One row / column field: picker + remove button."""

    removed = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(self, metrics, theme, none_label=None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(metrics.gap)
        self.combo = W.FieldCombo(metrics, theme, none_label)
        self.combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
        lay.addWidget(self.combo, 1)
        self.remove_btn = W.square_button(metrics, icons.native(icons.REMOVE),
                                          "Remove")
        self.remove_btn.clicked.connect(lambda: self.removed.emit(self))
        lay.addWidget(self.remove_btn)


class _MeasureRow(QFrame):
    """One value: field, aggregation, optional output label."""

    removed = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(self, metrics, theme, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(metrics.gap)
        self.field = W.FieldCombo(metrics, theme, none_label="(records)")
        top.addWidget(self.field, 1)
        self.remove_btn = W.square_button(metrics, icons.native(icons.REMOVE),
                                          "Remove this value")
        self.remove_btn.clicked.connect(lambda: self.removed.emit(self))
        top.addWidget(self.remove_btn)
        lay.addLayout(top)
        self.agg = QComboBox()
        self.agg.setFixedHeight(metrics.control_h)
        for agg, label, numeric in P.AGGREGATES:
            self.agg.addItem(label, agg)
            if numeric:
                self.agg.setItemData(self.agg.count() - 1,
                                     "Needs a numeric field",
                                     Qt.ItemDataRole.ToolTipRole)
        lay.addWidget(self.agg)
        self.label = QLineEdit()
        self.label.setFixedHeight(metrics.control_h)
        self.label.setClearButtonEnabled(True)
        lay.addWidget(self.label)
        self._fields = None
        self.field.currentIndexChanged.connect(self._on_change)
        self.agg.currentIndexChanged.connect(self._on_agg)
        self.label.textChanged.connect(lambda _t: self.changed.emit())

    def set_fields(self, fields):
        self._fields = fields
        self._refill()

    def _refill(self):
        if self._fields is None:
            return
        numeric = self.agg.currentData() in P.NUMERIC_AGGS
        self.field.set_fields(self._fields, numeric_only=numeric)
        self._on_change()

    def _on_agg(self, _i):
        self._refill()

    def _on_change(self, *_args):
        m = self.measure()
        self.label.setPlaceholderText(f"Label: {P.Measure(m.field, m.agg).title()}")
        self.changed.emit()

    def measure(self):
        agg = self.agg.currentData()
        field = self.field.current_name()
        if agg == "count":
            field = field or None
        return P.Measure(field, agg, self.label.text().strip())

    def set_measure(self, m):
        idx = self.agg.findData(m.agg)
        self.agg.setCurrentIndex(max(idx, 0))
        self._refill()
        self.field.set_current_name(m.field)
        self.label.setText(m.label or "")


class _FilterDialog(QDialog):
    """Add (or edit) one pivot filter: field, operator, value."""

    def __init__(self, fields, metrics, theme, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("Edit filter" if existing is not None else "Add filter")
        self.setWindowIcon(icons.native(icons.FILTER))
        self.setMinimumWidth(400)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        lay = QFormLayout(self)
        lay.setContentsMargins(metrics.margin + 4, metrics.margin + 4,
                               metrics.margin + 4, metrics.margin)
        lay.setSpacing(8)
        self.field = W.FieldCombo(metrics, theme)
        self.field.set_fields(fields)
        self.op = QComboBox()
        self.op.setFixedHeight(metrics.control_h)
        self.value = QLineEdit()
        self.value.setFixedHeight(metrics.control_h)
        self.value2 = QLineEdit()
        self.value2.setFixedHeight(metrics.control_h)
        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {theme.danger};")
        lay.addRow("Field", self.field)
        lay.addRow("Operator", self.op)
        lay.addRow("Value", self.value)
        lay.addRow("", self.value2)
        lay.addRow("", self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addRow(box)
        self.field.currentIndexChanged.connect(self._on_field)
        self.op.currentIndexChanged.connect(self._on_op)
        self._on_field()
        if existing is not None:
            self.field.set_current_name(existing.field)
            self._on_field()
            idx = self.op.findData(existing.op)
            if idx >= 0:
                self.op.setCurrentIndex(idx)
            self.value.setText(str(existing.value))
            self.value2.setText(str(existing.value2))

    def _on_field(self, *_a):
        kind = self.field.kind() or F.TEXT
        self.op.blockSignals(True)
        self.op.clear()
        colour = self.palette().color(self.foregroundRole()).name()
        for op_id, label in FL.operators(kind):
            if op_id == "in":
                continue
            self.op.addItem(icons.text_glyph(FL.OP_SYMBOLS.get(op_id, ""), colour),
                            label, op_id)
        self.op.blockSignals(False)
        self._on_op()

    def _on_op(self, *_a):
        op = self.op.currentData()
        self.value.setEnabled(op not in FL.NO_VALUE_OPS)
        self.value2.setVisible(op == "between")
        hint, hint2 = FL.value_hints(self.field.kind() or F.TEXT, op)
        self.value.setPlaceholderText(hint)
        self.value2.setPlaceholderText(hint2)

    def column_filter(self):
        return FL.ColumnFilter(self.field.current_name(), self.field.kind(),
                               self.op.currentData(), self.value.text(),
                               self.value2.text())

    def _accept(self):
        try:
            if self.column_filter().build() is None:
                raise FL.FilterError("Enter a value")
        except FL.FilterError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


# ============================================================================
# PANEL
# ============================================================================

class PivotPanel(QWidget):
    drillRequested = pyqtSignal(object, list, str)       # layer, fids, label
    titleChanged = pyqtSignal()

    def __init__(self, iface, explorer_lookup=None, parent=None):
        super().__init__(parent)
        self.setObjectName("gtsPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.iface = iface
        self.theme = Theme()
        self.metrics = Metrics(self.font())
        self.setStyleSheet(stylesheet(self.theme, self.metrics))
        self.explorer_lookup = explorer_lookup or (lambda layer: None)
        self.runner = JobRunner(self)
        self.layer = None
        self._layer_signals = []
        self._revision = 0
        self._selection_version = 0
        self.input = None
        self._input_stale = False
        self._result_input = None          # input the shown result was built on
        self.filters = []
        self.row_rows, self.col_rows, self.measure_rows = [], [], []
        self._closing = False
        self._build_ui()

    # ====================================================================
    # UI
    # ====================================================================

    def _build_ui(self):
        m = self.metrics
        root = QVBoxLayout(self)
        root.setContentsMargins(m.margin, m.margin, m.margin, m.margin)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(m.gap)
        top.addWidget(W.label("Layer", "dim"))
        self.layer_combo = W.LayerCombo(m)
        self.layer_combo.setFilters(VECTOR_LAYER_FILTER)
        self.layer_combo.setMinimumWidth(200)
        self.layer_combo.setMaximumWidth(260)
        self.layer_combo.layerChanged.connect(self._on_combo_layer)
        top.addWidget(self.layer_combo)
        top.addSpacing(10)
        self.input_label = W.ElideLabel()
        self.input_label.setMinimumWidth(110)
        self.input_label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                       QSizePolicy.Policy.Preferred)
        top.addWidget(self.input_label)

        row = W.ButtonRow(m)
        row.add_stretch()
        self.scope_menu = QMenu(self)
        self.scope_menu.aboutToShow.connect(self._fill_scope_menu)
        self.scope_btn = row.add(W.button(
            m, "Change scope", icons.native(icons.FILTER),
            "Choose which records feed the pivot", menu=self.scope_menu))
        self.load_menu = QMenu(self)
        self.load_menu.aboutToShow.connect(self._fill_load_menu)
        self.load_btn = row.add(W.button(
            m, "Load layout", icons.native(icons.OPEN),
            "Load a saved pivot recipe", menu=self.load_menu))
        save_menu = QMenu(self)
        save_menu.addAction(icons.native(icons.SAVE), "Save in project…"
                            ).triggered.connect(self._save_layout_project)
        save_menu.addAction(icons.native(icons.EXPORT), "Save to file…"
                            ).triggered.connect(self._save_layout_file)
        self.save_btn = row.add(W.button(
            m, "Save layout", icons.native(icons.SAVE),
            "Save this pivot recipe (fields, values, filters — not results)",
            menu=save_menu))
        self.refresh_btn = row.add(W.button(
            m, "Refresh input", icons.native(icons.REFRESH),
            "Capture the records of the current scope again"))
        self.build_btn = W.action_button(m, "Build Pivot",
                                         "Calculate the pivot from the input")
        row.add(self.build_btn)
        export_menu = QMenu(self)
        self.export_xlsx = export_menu.addAction(icons.native(icons.XLSX),
                                                 "Excel (.xlsx)")
        self.export_csv = export_menu.addAction(icons.native(icons.CSV), "CSV (.csv)")
        self.export_gpkg = export_menu.addAction(
            icons.native(icons.GPKG), "GeoPackage table (.gpkg)")
        export_menu.addSeparator()
        self.copy_action = export_menu.addAction(icons.native(icons.COPY),
                                                 "Copy table")
        if not X.HAS_OPENPYXL:
            self.export_xlsx.setEnabled(False)
        self.export_btn = row.add(W.menu_square(
            m, icons.native(icons.EXPORT), "Export\nThe built pivot (with its layout "
            "and scope) to Excel, CSV or a GeoPackage table, or copy it", export_menu))
        top.addWidget(row, 1)
        top.addSpacing(m.gap)
        self.help_btn = W.square_button(m, icons.native(icons.HELP),
                                        "Help\nHow to build a pivot table, step by step")
        self.help_btn.clicked.connect(self.show_help)
        top.addWidget(self.help_btn)
        self.dock_btn = W.dock_button(m)
        top.addWidget(self.dock_btn)
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.addWidget(self._build_builder())
        split.addWidget(self._build_result())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 900])
        root.addWidget(split, 1)

        self.status = W.StatusStrip(m)
        self.status.cancelRequested.connect(self.runner.cancel_all)
        root.addWidget(self.status)

        self.build_btn.clicked.connect(self.build)
        self.refresh_btn.clicked.connect(self.refresh_input)
        self.export_xlsx.triggered.connect(lambda: self.export("xlsx"))
        self.export_csv.triggered.connect(lambda: self.export("csv"))
        self.export_gpkg.triggered.connect(lambda: self.export("gpkg"))
        self.copy_action.triggered.connect(self.copy_table)
        self._set_result_actions(False)
        self._refresh_filters()

    def _section(self, lay, text, first=False):
        """Section heading with a divider line: 'Rows ───────────'."""
        if not first:
            lay.addSpacing(10)
        lay.addWidget(W.section_header(text))
        lay.addSpacing(2)

    def _build_builder(self):
        m = self.metrics
        card = QFrame()
        card.setObjectName("gtsCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("gtsBuilder")
        scroll.viewport().setObjectName("gtsBuilderViewport")
        scroll.setStyleSheet(
            "QWidget#gtsBuilder, QWidget#gtsBuilderViewport "
            "{ background: transparent; }")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        lay.addWidget(W.label("Build your table", "heading"))
        lay.addSpacing(4)

        self._section(lay, "Rows", first=True)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(6)
        lay.addLayout(self.rows_box)
        self.add_row_btn = W.button(m, "Add row", icons.native(icons.ADD),
                                    "Group by another field")
        self.add_row_btn.clicked.connect(lambda: self._add_field_row(self.row_rows,
                                                                     self.rows_box))
        lay.addWidget(self.add_row_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self._section(lay, "Columns  (optional)")
        self.cols_box = QVBoxLayout()
        self.cols_box.setSpacing(6)
        lay.addLayout(self.cols_box)
        self.add_col_btn = W.button(m, "Add column", icons.native(icons.ADD),
                                    "Spread values across columns (cross tab)")
        self.add_col_btn.clicked.connect(lambda: self._add_field_row(
            self.col_rows, self.cols_box, none_label="None"))
        lay.addWidget(self.add_col_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self._section(lay, "Values")
        self.values_box = QVBoxLayout()
        self.values_box.setSpacing(12)
        lay.addLayout(self.values_box)
        self.add_value_btn = W.button(m, "Add value", icons.native(icons.ADD),
                                      "Add another measure")
        self.add_value_btn.clicked.connect(lambda: self._add_measure())
        lay.addWidget(self.add_value_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self._section(lay, "Filters")
        self.filters_empty = W.label("None — every input record is used", "dim")
        lay.addWidget(self.filters_empty)
        self.chips = QWidget()
        self.chips_flow = W.FlowLayout(self.chips, h_spacing=6, v_spacing=6)
        lay.addWidget(self.chips)
        self.filters_note = W.label("All filters must match (AND)", "dim")
        lay.addWidget(self.filters_note)
        self.add_filter_btn = W.button(m, "Add filter", icons.native(icons.ADD),
                                       "Filter the input before grouping")
        self.add_filter_btn.clicked.connect(self._add_filter)
        lay.addWidget(self.add_filter_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self._section(lay, "Options")
        self.show_count_cb = QCheckBox("Show record count")
        self.show_count_cb.setChecked(True)
        self.show_total_cb = QCheckBox("Show grand total")
        self.show_total_cb.setChecked(True)
        lay.addWidget(self.show_count_cb)
        lay.addWidget(self.show_total_cb)
        lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        card.setMinimumWidth(270)
        card.setMaximumWidth(460)
        return card

    def _build_result(self):
        m = self.metrics
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 0, 0, 0)
        lay.setSpacing(8)
        head = QFrame()
        head.setObjectName("gtsCard")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(16)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = W.label("Pivot Table", "display")
        titles.addWidget(self.title_label)
        self.subtitle = W.ElideLabel("Choose rows and values, then press Build Pivot")
        self.subtitle.setObjectName("gtsDim")
        titles.addWidget(self.subtitle)
        hl.addLayout(titles, 1)
        counts = QVBoxLayout()
        counts.setSpacing(2)
        self.records_label = W.label("—", "display")
        self.records_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
        counts.addWidget(self.records_label)
        self.groups_label = W.label("records used", "dim")
        self.groups_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
        counts.addWidget(self.groups_label)
        hl.addLayout(counts)
        lay.addWidget(head)
        self.stack = QStackedWidget()
        empty = QLabel("Choose row fields and values on the left,\n"
                       "then press  Build Pivot.")
        empty.setObjectName("gtsDim")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(empty)
        self.table = QTableView()
        self.table.setObjectName("gtsTable")
        self.table.setHorizontalHeader(W.SortHeader(self.theme, self.table))
        self.model = PivotModel(self.theme, self)
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSortIndicator(
            -1, Qt.SortOrder.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeader().setMinimumHeight(m.font_h + 14)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(m.font_h + 14)
        self.table.setAlternatingRowColors(False)
        self.table.setItemDelegate(_PivotDelegate(self.theme, self.table))
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.doubleClicked.connect(self._drill)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.stack.addWidget(self.table)
        lay.addWidget(self.stack, 1)
        self.hint1 = W.label("Double click a result to inspect its contributing "
                             "records.", "dim")
        self.hint2 = W.label("Add a column field to turn this grouped summary "
                             "into a cross tab.", "dim")
        lay.addSpacing(4)
        lay.addWidget(self.hint1)
        lay.addWidget(self.hint2)
        lay.addSpacing(4)
        return box

    # ====================================================================
    # BUILDER STATE
    # ====================================================================

    def _fields(self):
        return self.layer.fields() if self.layer is not None else QgsFields()

    def _add_field_row(self, rows, box, name=None, none_label=None):
        r = _FieldRow(self.metrics, self.theme, none_label)
        r.combo.set_fields(self._fields())
        if name is not None:
            r.combo.set_current_name(name)
        r.removed.connect(lambda w, rs=rows: self._remove_row(rs, w))
        r.changed.connect(self._update_title)
        rows.append(r)
        box.addWidget(r)
        self._update_remove_buttons()
        self._update_title()
        return r

    @staticmethod
    def _discard(widget):
        widget.hide()
        widget.setParent(None)
        widget.deleteLater()

    def _remove_row(self, rows, widget):
        if widget in rows:
            rows.remove(widget)
            self._discard(widget)
        self._update_remove_buttons()
        self._update_title()

    def _add_measure(self, measure=None):
        r = _MeasureRow(self.metrics, self.theme)
        r.set_fields(self._fields())
        if measure is not None:
            r.set_measure(measure)
        r.removed.connect(lambda w: self._remove_row(self.measure_rows, w))
        r.changed.connect(self._update_title)
        self.measure_rows.append(r)
        self.values_box.addWidget(r)
        self._update_remove_buttons()
        self._update_title()
        return r

    def _update_remove_buttons(self):
        # a section always keeps one entry; remove buttons appear once there
        # are two or more, so a simple pivot looks as clean as the mockup
        for rows in (self.row_rows, self.col_rows, self.measure_rows):
            for r in rows:
                r.remove_btn.setVisible(len(rows) > 1)

    def spec(self):
        rows = [r.combo.current_name() for r in self.row_rows
                if r.combo.current_name()]
        cols = [r.combo.current_name() for r in self.col_rows
                if r.combo.current_name()]
        measures = [r.measure() for r in self.measure_rows]
        return P.PivotSpec(rows, cols, measures, self.show_count_cb.isChecked(),
                           self.show_total_cb.isChecked())

    def set_spec(self, spec, filters=()):
        for rows in (self.row_rows, self.col_rows, self.measure_rows):
            for w in rows:
                self._discard(w)
            rows.clear()
        for name in spec.rows or [None]:
            self._add_field_row(self.row_rows, self.rows_box, name)
        for name in spec.cols or [None]:
            self._add_field_row(self.col_rows, self.cols_box, name, "None")
        for m in spec.measures or [P.Measure(None, "count")]:
            self._add_measure(m)
        self.show_count_cb.setChecked(spec.show_count)
        self.show_total_cb.setChecked(spec.show_total)
        self.filters = list(filters)
        self._refresh_filters()
        self._update_title()

    def _default_spec(self, scope=None):
        fields = self._fields()
        kinds = {fields.at(i).name(): F.field_kind(fields.at(i))
                 for i in range(fields.count())}
        row = None
        for name in (scope or {}).get("categorical", []):
            row = name
            break
        if row is None:
            for name, kind in kinds.items():
                low = name.lower()
                if kind == F.TEXT and not any(k in low for k in (
                        "id", "pfi", "uuid", "guid", "key", "code", "name")):
                    row = name
                    break
        if row is None:
            row = next((n for n, k in kinds.items() if k == F.TEXT),
                       next(iter(kinds), None))
        real = next((n for n, k in kinds.items() if k == F.REAL), None)
        measure = P.Measure(real, "mean") if real else P.Measure(None, "count")
        return P.PivotSpec([row] if row else [], [], [measure])

    def _update_title(self, *_args):
        spec = self.spec()
        if not spec.rows or not spec.measures:
            self.title_label.setText("Pivot Table")
            return
        parts = [m.title() for m in spec.measures]
        by = ", ".join(spec.rows)
        text = f"{' · '.join(parts)} by {by}"
        if spec.cols:
            text += f" and {', '.join(spec.cols)}"
        self.title_label.setText(text)
        self.hint2.setVisible(not spec.cols)
        if self._result_input is not None:
            self._mark_stale_if_changed()

    # ---- filters ----
    def _add_filter(self):
        if self.layer is None:
            return
        dlg = _FilterDialog(self._fields(), self.metrics, self.theme, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.filters.append(dlg.column_filter())
            self._refresh_filters()

    def _edit_filter(self, flt):
        if self.layer is None or flt not in self.filters:
            return
        dlg = _FilterDialog(self._fields(), self.metrics, self.theme, self, flt)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.filters[self.filters.index(flt)] = dlg.column_filter()
            self._refresh_filters()

    def _refresh_filters(self):
        while self.chips_flow.count():
            item = self.chips_flow.takeAt(0)
            if item.widget():
                self._discard(item.widget())
        width = max(120, self.chips.width() or 240)
        for flt in self.filters:
            chip = W.Chip(flt.describe(), self.metrics, self.theme,
                          max_width=width)
            chip.clicked.connect(lambda f=flt: self._edit_filter(f))
            chip.removed.connect(lambda f=flt: self._remove_filter(f))
            self.chips_flow.addWidget(chip)
        self.filters_empty.setVisible(not self.filters)
        self.chips.setVisible(bool(self.filters))
        self.filters_note.setVisible(len(self.filters) > 1)
        self.chips.updateGeometry()
        self._update_title()

    def _remove_filter(self, flt):
        if flt in self.filters:
            self.filters.remove(flt)
        self._refresh_filters()

    # ====================================================================
    # LAYER + INPUT
    # ====================================================================

    def window_title(self):
        name = self.layer.name() if self.layer is not None else ""
        return f"Pivot Table — {name}" if name else "Pivot Table"

    def set_layer(self, layer, scope=None):
        same = layer is self.layer
        if not same:
            self._disconnect_layer()
            self.runner.new_generation()
            self.layer = layer
            self._revision = 0
            self.layer_combo.blockSignals(True)
            self.layer_combo.setLayer(layer)
            self.layer_combo.blockSignals(False)
            self.model.set_result(None, 2)
            self._result_input = None
            self.stack.setCurrentIndex(0)
            self._set_result_actions(False)
            self._reset_header_card()
            if layer is not None:
                self._connect_layer()
                self.set_spec(self._default_spec(scope))
            self.titleChanged.emit()
        if layer is None:
            self.input = None
            self._update_input_label()
            return
        if scope is not None:
            self._capture_from(scope)
        elif not same or self.input is None:
            self._capture("all")
        self._update_input_label()
        self._update_status()

    def _connect_layer(self):
        lyr = self.layer
        pairs = [
            (lyr.selectionChanged, self._on_selection),
            (lyr.attributeValueChanged, self._on_data),
            (lyr.featureAdded, self._on_data),
            (lyr.featuresDeleted, self._on_data),
            (lyr.dataChanged, self._on_data),
            (lyr.subsetStringChanged, self._on_data),
            (lyr.updatedFields, self._on_schema),
            (lyr.nameChanged, self.titleChanged.emit),
        ]
        for sig, slot in pairs:
            sig.connect(slot)
        self._layer_signals = pairs

    def _disconnect_layer(self):
        for sig, slot in self._layer_signals:
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._layer_signals = []
        exp = (self.input or {}).get("explorer")
        if exp is not None:
            try:
                exp.scopeChanged.disconnect(self._on_explorer_scope)
            except (TypeError, RuntimeError):
                pass

    def _on_combo_layer(self, layer):
        if layer is not None and layer is not self.layer:
            self.set_layer(layer)

    def _on_selection(self, *_a):
        self._selection_version += 1
        if self.input and self.input["kind"] == "selected":
            self._input_stale = True
            self._update_status()

    def _on_data(self, *_a):
        self._revision += 1
        if self.input is not None:
            self._input_stale = True
            self._update_status()

    def _on_schema(self):
        self._revision += 1
        fields = self._fields()
        for r in self.row_rows + self.col_rows:
            r.combo.set_fields(fields)
        for r in self.measure_rows:
            r.set_fields(fields)
        self._input_stale = True
        self._update_status()

    def _on_explorer_scope(self):
        if self.input and self.input["kind"] == "explorer":
            exp = self.input.get("explorer")
            if exp is not None and exp.scope_version != self.input.get("version"):
                self._input_stale = True
                self._update_status()

    def _capture_from(self, scope):
        old = (self.input or {}).get("explorer")
        if old is not None:
            try:
                old.scopeChanged.disconnect(self._on_explorer_scope)
            except (TypeError, RuntimeError):
                pass
        self.input = {
            "kind": scope["kind"], "fids": scope.get("fids"),
            "label": scope.get("label", SCOPE_LABELS.get(scope["kind"], "")),
            "description": scope.get("description", ""),
            "explorer": scope.get("explorer"), "version": scope.get("version"),
            "revision": self._revision,
            "selection_version": self._selection_version,
        }
        exp = self.input["explorer"]
        if exp is not None:
            exp.scopeChanged.connect(self._on_explorer_scope)
        self._input_stale = False
        self._update_input_label()
        self._update_status()

    def _capture(self, kind):
        layer = self.layer
        if layer is None:
            return
        if kind == "explorer":
            exp = self.explorer_lookup(layer)
            if exp is None:
                QMessageBox.information(
                    self, "Pivot Table",
                    "Open this layer in Attribute Explorer first.")
                return
            self._capture_from(exp.current_scope())
            return
        if kind == "selected":
            fids = list(layer.selectedFeatureIds())
            self._capture_from({"kind": "selected", "fids": fids,
                                "label": f"Selected records ({len(fids):,})"})
            return
        n = max(layer.featureCount(), 0)
        self._capture_from({"kind": "all", "fids": None,
                            "label": "All available records",
                            "description": f"{n:,} records available through "
                                           "the layer"})

    def refresh_input(self):
        if self.input is None:
            self._capture("all")
        else:
            self._capture(self.input["kind"])

    def _fill_scope_menu(self):
        menu = self.scope_menu
        menu.clear()
        layer = self.layer
        current = (self.input or {}).get("kind")
        if layer is None:
            return
        n_sel = layer.selectedFeatureCount()
        exp = self.explorer_lookup(layer)
        entries = [("all", f"All available records ({max(layer.featureCount(), 0):,})",
                    icons.TABLE, True),
                   ("explorer", "Explorer filtered records" +
                    (f" ({len(exp.model.fids):,})" if exp else " (open Explorer first)"),
                    icons.FILTER, exp is not None),
                   ("selected", f"Selected records ({n_sel:,})",
                    icons.SELECTED_ONLY, True)]
        for kind, text, icon, enabled in entries:
            act = menu.addAction(icons.native(icon), text)
            act.setCheckable(True)
            act.setChecked(kind == current)
            act.setEnabled(enabled)
            act.triggered.connect(lambda _c=False, k=kind: self._capture(k))

    def _update_input_label(self):
        if self.input is None:
            self.input_label.setText("Input: —")
            return
        self.input_label.setText(f"Input: {self.input['label']}")
        self.input_label.setToolTip(self.input.get("description") or "")

    # ====================================================================
    # BUILD
    # ====================================================================

    def build(self):
        layer = self.layer
        if layer is None:
            return
        spec = self.spec()
        if not spec.rows:
            self.status.set_status("Choose at least one row field", "warning")
            return
        if self.input is None:
            self._capture("all")
        expr, errors = FL.combine(self.filters)
        if errors:
            self.status.set_status("Invalid filter: " + "; ".join(errors.values()),
                                   "error")
            return
        fields = layer.fields()
        kinds = {fields.at(i).name(): F.field_kind(fields.at(i))
                 for i in range(fields.count())}
        needed = spec.fields_needed()
        for name in needed:
            if fields.indexOf(name) < 0:
                self.status.set_status(f"Field '{name}' no longer exists", "error")
                return
        idxs = [fields.indexOf(n) for n in needed]
        fids = self.input.get("fids")
        restrict = set(fids) if fids is not None else None
        spec_scan = ScanSpec(layer, expr, restrict)
        limit = settings.value("pivot/cellLimit")
        captured = dict(self.input)
        captured["filters"] = [f.describe() for f in self.filters]
        captured["expression"] = expr

        def work(job, s=spec_scan, idxs=idxs, needed=needed, spec=spec):
            ids, cols = scan(s, job, idxs, True)
            job.check()
            records = zip(ids, zip(*cols)) if cols else ((i, ()) for i in ids)
            return P.compute(records, needed, spec, kinds, cell_limit=limit,
                             is_cancelled=job.isCanceled,
                             progress=lambda p: None, total=len(ids))

        self.runner.new_generation()
        self.status.set_status("Building pivot…", "busy")
        self.status.set_busy(True)
        self.build_btn.setEnabled(False)
        job = self.runner.start(f"Pivot Table: {layer.name()}", work,
                                self._on_built, flags=TASK_VISIBLE)
        job.captured = captured
        job.revision = self._revision
        job.selection_version = self._selection_version

    def _on_built(self, job):
        if self._closing:
            return
        self.build_btn.setEnabled(True)
        self.status.set_busy(False)
        if not self.runner.is_current(job):
            return
        if job.cancelled:
            self.status.set_status("Cancelled — the last completed result is kept",
                                   "warning")
            return
        if job.error:
            last = job.error.strip().splitlines()[-1]
            if "PivotError" in last or "PivotCancelled" in last:
                msg = last.split(":", 1)[-1].strip()
                self.status.set_status(msg, "warning")
                if "exceed" in msg:
                    QMessageBox.warning(self, "Pivot Table", msg)
            else:
                QgsMessageLog.logMessage(f"Pivot failed:\n{job.error}", LOG_TAG)
                self.status.set_status(f"Pivot failed: {last}", "error")
            return
        result = job.result
        captured = job.captured
        captured["built_revision"] = job.revision
        captured["built_selection"] = job.selection_version
        captured["spec"] = json.dumps(result.spec.to_dict(), sort_keys=True)
        self._result_input = captured
        self.model.set_result(result, settings.value("pivot/decimals"))
        self.table.horizontalHeader().setSortIndicator(
            -1, Qt.SortOrder.AscendingOrder)
        self.stack.setCurrentIndex(1)
        self._resize_columns()
        self._set_result_actions(True)
        n = result.n_input
        extras = [captured.get("label", ""), "NULL numeric values excluded",
                  "Exact"]
        if captured.get("filters"):
            extras.insert(1, "Filters: " + " AND ".join(captured["filters"]))
        self.subtitle.setText("Input: " + "  •  ".join(e for e in extras if e))
        self.records_label.setText(f"{n:,}")
        groups = result.group_count
        self.groups_label.setText(
            f"records  •  {groups:,} group{'s' if groups != 1 else ''}")
        self._update_status()

    def _resize_columns(self):
        """Columns fit their content and header (with room for the sort
        arrow); nothing is stretched across the window."""
        hdr = self.table.horizontalHeader()
        self.table.resizeColumnsToContents()
        fm = self.metrics.bold_fm
        for c in range(self.model.columnCount()):
            text = str(self.model.headerData(c, Qt.Orientation.Horizontal) or "")
            header_w = max(fm.horizontalAdvance(line)
                           for line in text.split("\n")) + 8 + 22 + 8
            cell_w = hdr.sectionSize(c) + 2 * _PivotDelegate.PAD
            hdr.resizeSection(c, max(cell_w, header_w, 80))

    def _reset_header_card(self):
        self.subtitle.setText("Choose rows and values, then press Build Pivot")
        self.records_label.setText("—")
        self.groups_label.setText("records used")

    def _set_result_actions(self, on):
        self.export_btn.setEnabled(on)
        self.hint1.setVisible(on)

    def _stale_reason(self):
        r = self._result_input
        if r is None:
            return None
        if self._input_stale:
            return "input"
        if r.get("built_revision") != self._revision:
            return "data"
        if r.get("kind") == "selected" and \
                r.get("built_selection") != self._selection_version:
            return "selection"
        if r.get("spec") != json.dumps(self.spec().to_dict(), sort_keys=True):
            return "layout"
        return None

    def _mark_stale_if_changed(self):
        self._update_status()

    def _update_status(self):
        if self.runner.busy:
            return
        if self.layer is None:
            self.status.set_status("Choose a layer")
            return
        result = self.model.result
        stale = self._stale_reason()
        if result is None:
            text = "Ready  •  choose rows and values, then Build Pivot"
            if self._input_stale:
                text = "Source changed  •  Refresh input then build"
            self.status.set_status(text, "warning" if self._input_stale else "info")
            return
        groups = result.group_count
        base = f"{groups:,} group{'s' if groups != 1 else ''}"
        if stale in ("input", "data", "selection"):
            self.status.set_status(
                f"{base}  •  Source changed — Refresh input then rebuild "
                "(drill-through is paused)", "warning")
        elif stale == "layout":
            self.status.set_status(
                f"{base}  •  Layout changed — press Build Pivot to update", "warning")
        else:
            self.status.set_status(
                f"Ready  •  {base}  •  Input captured at Build Pivot", "success")

    # ====================================================================
    # DRILL / COPY / CONTEXT
    # ====================================================================

    def _drill(self, index):
        if not index.isValid() or self.model.result is None:
            return
        if self._stale_reason() in ("input", "data", "selection"):
            QMessageBox.information(
                self, "Pivot Table",
                "The source changed after this pivot was built.\n"
                "Refresh input and rebuild before inspecting records.")
            return
        cell = self.model.cell(index.row(), index.column())
        result = self.model.result
        rk = cell["rk"]
        ck = cell.get("ck")
        if ck is None:
            ck = P.TOTAL
        fids = result.fids(rk, ck)
        label = self._cell_label(rk, ck)
        self.drillRequested.emit(self.layer, fids, label)

    def _cell_label(self, rk, ck):
        spec = self.model.result.spec
        parts = []
        if rk is P.TOTAL:
            parts.append("Grand total")
        else:
            parts += [f"{n} = {F.display_value(v) if v is not None else 'NULL'}"
                      for n, v in zip(spec.rows, rk)]
        if ck is not P.TOTAL and ck:
            parts += [f"{n} = {F.display_value(v) if v is not None else 'NULL'}"
                      for n, v in zip(spec.cols, ck)]
        return "Pivot: " + ", ".join(parts)

    def copy_table(self):
        if self.model.result is None:
            return
        idxs = self.table.selectionModel().selectedIndexes()
        groups, header, body = self.model.as_table(raw=False)
        if len(idxs) > 1:
            rows = sorted({i.row() for i in idxs})
            cols = sorted({i.column() for i in idxs})
            lines = ["\t".join(header[c] for c in cols)]
            for r in rows:
                lines.append("\t".join(body[r][c] for c in cols))
        else:
            lines = []
            if groups:
                lines.append("\t".join(groups))
            lines.append("\t".join(header))
            lines += ["\t".join(str(v) for v in row) for row in body]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.status.set_status("Copied to the clipboard", "success")

    def _context_menu(self, pos):
        idx = self.table.indexAt(pos)
        menu = QMenu(self)
        if idx.isValid():
            menu.addAction(icons.tool(icons.EXPLORER), "Inspect contributing records"
                           ).triggered.connect(lambda: self._drill(idx))
            menu.addSeparator()
        act = menu.addAction(icons.native(icons.COPY), "Copy")
        act.setShortcut(QKeySequence.StandardKey.Copy)
        act.triggered.connect(self.copy_table)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_table()
            return
        super().keyPressEvent(event)

    # ====================================================================
    # LAYOUTS (recipes only, never results)
    # ====================================================================

    def _recipe(self):
        return {"version": 1, "layer": self.layer.name() if self.layer else "",
                "spec": self.spec().to_dict(),
                "filters": [f.to_dict() for f in self.filters]}

    def _apply_recipe(self, recipe):
        try:
            spec = P.PivotSpec.from_dict(recipe["spec"])
            filters = [FL.ColumnFilter.from_dict(d) for d in recipe.get("filters", [])]
        except (KeyError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Load layout", f"Not a pivot layout:\n{exc}")
            return
        fields = self._fields()
        missing = [n for n in spec.fields_needed() + [f.field for f in filters]
                   if fields.indexOf(n) < 0]
        self.set_spec(spec, filters)
        if missing:
            self.status.set_status(
                "Layout loaded — missing fields: " + ", ".join(sorted(set(missing))),
                "warning")
        else:
            self.status.set_status("Layout loaded — press Build Pivot", "success")

    def _project_layouts(self):
        text, ok = QgsProject.instance().readEntry(PROJECT_SCOPE, PROJECT_LAYOUTS, "")
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _save_layout_project(self):
        name, ok = QInputDialog.getText(self, "Save layout", "Layout name:",
                                        text=self.title_label.text()[:60])
        if not ok or not name.strip():
            return
        data = self._project_layouts()
        data[name.strip()] = self._recipe()
        QgsProject.instance().writeEntry(PROJECT_SCOPE, PROJECT_LAYOUTS,
                                         json.dumps(data))
        self.status.set_status(f"Layout '{name.strip()}' saved in the project",
                               "success")

    def _save_layout_file(self):
        path, _s = QFileDialog.getSaveFileName(self, "Save layout",
                                               "pivot_layout.json", "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._recipe(), fh, indent=2)
        self.status.set_status(f"Layout saved to {path}", "success")

    def show_help(self):
        """How to build a pivot, in its own window (as the other tools do)."""
        from .help_dialog import HelpDialog
        dlg = getattr(self, "_help_dialog", None)
        if dlg is None:
            dlg = self._help_dialog = HelpDialog(self, topic="pivot")
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _fill_load_menu(self):
        menu = self.load_menu
        menu.clear()
        layouts = self._project_layouts()
        if layouts:
            for name in sorted(layouts):
                act = menu.addAction(icons.tool(icons.PIVOT), name)
                act.triggered.connect(
                    lambda _c=False, n=name: self._apply_recipe(self._project_layouts()[n]))
        else:
            act = menu.addAction("No layouts saved in this project")
            act.setEnabled(False)
        menu.addSeparator()
        menu.addAction(icons.native(icons.OPEN), "Open from file…"
                       ).triggered.connect(self._load_layout_file)

    def _load_layout_file(self):
        path, _s = QFileDialog.getOpenFileName(self, "Load layout", "",
                                               "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                recipe = json.load(fh)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Load layout", str(exc))
            return
        self._apply_recipe(recipe)

    # ====================================================================
    # EXPORT
    # ====================================================================

    def _export_meta(self):
        r = self._result_input or {}
        spec = self.model.result.spec
        return {
            "Layer": self.layer.name(),
            "Source": self.layer.publicSource(),
            "Input": r.get("label", ""),
            "Input detail": r.get("description") or "—",
            "Filters": " AND ".join(r.get("filters") or []) or "(none)",
            "Rows": ", ".join(spec.rows),
            "Columns": ", ".join(spec.cols) or "(none)",
            "Values": "; ".join(f"{m.title()} = {P.AGG_LABELS[m.agg]}"
                                f"({m.field or 'records'})" for m in spec.measures),
            "Records used": f"{self.model.result.n_input:,}",
            "Status": "STALE — source changed after build"
                      if self._stale_reason() else "Current",
            "Rules": "NULL excluded from numeric measures; totals from records",
        }

    def export(self, fmt):
        if self.model.result is None:
            return
        ext = {"xlsx": ".xlsx", "csv": ".csv", "gpkg": ".gpkg"}[fmt]
        flt = {"xlsx": "Excel workbook (*.xlsx)", "csv": "CSV (*.csv)",
               "gpkg": "GeoPackage (*.gpkg)"}[fmt]
        path, _s = QFileDialog.getSaveFileName(self, "Export pivot",
                                               f"pivot{ext}", flt)
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext
        groups, header, body = self.model.as_table(raw=True)
        meta = self._export_meta()
        try:
            if fmt == "csv":
                if groups:
                    header = [f"{g} | {h}" if g else h for g, h in zip(groups, header)]
                X.write_csv(path, header, body, meta)
            elif fmt == "xlsx":
                X.write_xlsx(path, "Pivot", header, body, meta, groups)
            else:
                self._write_gpkg(path, groups, header, body, meta)
        except Exception as exc:
            QgsMessageLog.logMessage(f"Pivot export failed: {exc}", LOG_TAG)
            self.status.set_status(f"Export failed: {exc}", "error")
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.status.set_status(f"Exported the pivot to {path}", "success")

    def _write_gpkg(self, path, groups, header, body, meta):
        names = [f"{g}_{h}" if g else h for g, h in zip(groups or [""] * len(header),
                                                         header)]
        safe = X.safe_field_names(names)
        kinds = [c["kind"] for c in self.model.header]
        layer = QgsVectorLayer("None", "pivot", "memory")
        fields = []
        for i, name in enumerate(safe):
            values = [row[i] for row in body if row[i] is not None]
            if kinds[i] == "count":
                ftype = "integer"
            elif kinds[i] == "value" or (values and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in values)):
                ftype = "double"
            else:
                ftype = "string"
            fields.append((name, ftype))
        uri = "None?" + "&".join(f"field={n}:{t}" for n, t in fields)
        layer = QgsVectorLayer(uri, "pivot", "memory")
        feats = []
        for row in body:
            f = QgsFeature(layer.fields())
            attrs = []
            for (name, ftype), v in zip(fields, row):
                if v is None:
                    attrs.append(None)
                elif ftype == "string":
                    attrs.append(F.display_value(v) if not isinstance(v, str) else v)
                else:
                    attrs.append(v)
            f.setAttributes(attrs)
            feats.append(f)
        layer.dataProvider().addFeatures(feats)
        base = X.safe_field_names([self.layer.name() + "_pivot"])[0].lower()
        self._write_table(layer, path, base)
        info = QgsVectorLayer("None?field=item:string&field=value:string",
                              "summary", "memory")
        rows = []
        for line in X.scope_lines(meta):
            key, _sep, value = line.partition(": ")
            f = QgsFeature(info.fields())
            f.setAttributes([key, value])
            rows.append(f)
        info.dataProvider().addFeatures(rows)
        self._write_table(info, path, base + "_summary")

    @staticmethod
    def _write_table(layer, path, name):
        import os
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = name
        if os.path.exists(path):
            opts.actionOnExistingFile = \
                QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
        res = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, QgsProject.instance().transformContext(), opts)
        if res[0] != QgsVectorFileWriter.WriterError.NoError:
            raise RuntimeError(res[1] or "Could not write the GeoPackage")

    # ====================================================================
    def shutdown(self):
        self._closing = True
        self.runner.new_generation()
        self._disconnect_layer()
