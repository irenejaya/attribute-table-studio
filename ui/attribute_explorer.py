# -*- coding: utf-8 -*-
"""
Attribute Explorer - read-only table with attached column statistics.

Population rules (plan, "Column statistics and scope rules"):

    scope = records available through the layer (provider subset included)
            AND every column filter (AND across columns, OR inside a list)
            AND the QGIS selection          (when "Selected only" is on)
            AND records handed over from a pivot cell (drill-through)

Every profile describes exactly the rows in that scope - including rows
outside the visible screen; sorting and hiding columns never change it.
Filtering never touches the map selection.

Work runs in background tasks: one scan finds the scope (and computes the
visible columns' profiles in the same pass when it fits the memory
budget), then the remaining profiles follow in chunks, visible columns
first.  Every edit, filter or selection change starts a new generation, so
a stale result can never overwrite a newer one.
"""

import json
from contextlib import contextmanager

from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QGuiApplication, QKeySequence
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QMenu, QMessageBox,
    QVBoxLayout, QWidget,
)
from qgis.core import (
    QgsFeatureRequest, QgsMessageLog, QgsVectorLayer,
    QgsVectorLayerFeatureSource,
)

from ..core import export as X
from ..core import fields as F
from ..core import filters as FL
from ..core import settings
from ..core import stats as S
from ..core import subset as SUB
from ..core.compat import (
    NO_GEOMETRY, SELECT_ADD, SELECT_REMOVE, SELECT_SET, TASK_VISIBLE,
    VECTOR_LAYER_FILTER,
)
from ..core.jobs import JobRunner
from ..core.scan import ScanSpec, scan
from . import icons
from . import widgets as W
from .explorer_header import ExplorerHeader, ProfileDetails
from .explorer_table import ExplorerTableView, FeatureTableModel
from .theme import Metrics, Theme, stylesheet

LOG_TAG = "Attribute Table Studio"
LAYOUT_PROPERTY = "attribute_table_studio/explorer_layout"
VALUE_BUDGET = 4000000          # raw values held in memory per scan
VALUE_LIST_LIMIT = 5000         # entries offered by the "is one of" picker
QUICK_ROWS_LIMIT = 50000        # above this, records show before profiles


class ExplorerPanel(QWidget):
    """Attribute Explorer for one layer."""

    openPivotRequested = pyqtSignal(object, object)     # layer, scope dict
    layerChangeRequested = pyqtSignal(object, object)   # panel, new layer
    scopeChanged = pyqtSignal()
    titleChanged = pyqtSignal()

    def __init__(self, iface, layer, shortcut_text="", parent=None):
        super().__init__(parent)
        self.setObjectName("gtsPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.iface = iface
        self.theme = Theme()
        self.metrics = Metrics(self.font())
        self.setStyleSheet(stylesheet(self.theme, self.metrics))
        self.shortcut_text = shortcut_text
        self.runner = JobRunner(self)

        self.layer = None
        self._layer_signals = []
        self._revision = 0               # bumps on every data / schema change
        self._selection_version = 0
        self._table_selecting = False       # selection made in this table
        self._scope_key = None           # profiles are valid for this key
        self._expr = None
        self._restrict = None
        self._spec = None
        self.profiles = {}
        self._profile_queue = []
        self._per_scan = 1
        self.scope_version = 0           # bumps when the scope result changes
        self.external = None             # {"fids": set, "label": str}
        self.selected_only = False
        self.sort = None                 # (logical, descending)
        self._anchor = None
        self._drag_behaviour = SELECT_SET
        self._has_result = False
        self._closing = False
        # map filter: the layer's Query Builder filter follows the table
        self._map_filter = False
        self._base_subset = ""           # the layer's own filter before
        self._applied_subset = None      # the filter this explorer set
        self._subset_busy = False        # our own setSubsetString running
        self._available = 0              # records without our map filter

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        self._data_timer = QTimer(self)
        self._data_timer.setSingleShot(True)
        self._data_timer.setInterval(600)
        self._data_timer.timeout.connect(self._on_data_settled)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(800)
        self._layout_timer.timeout.connect(self._save_layout)
        self._profile_timer = QTimer(self)
        self._profile_timer.setSingleShot(True)
        self._profile_timer.setInterval(150)
        self._profile_timer.timeout.connect(self._compute_missing_profiles)

        self._build_ui()
        self.set_layer(layer)

    # ====================================================================
    # UI
    # ====================================================================

    def _build_ui(self):
        m, t = self.metrics, self.theme
        root = QVBoxLayout(self)
        root.setContentsMargins(m.margin, m.margin, m.margin, m.margin)
        root.setSpacing(8)

        # ---- top row: layer, count, actions ----
        top = QHBoxLayout()
        top.setSpacing(m.gap)
        top.addWidget(W.label("Layer", "dim"))
        self.layer_combo = W.LayerCombo(m)
        self.layer_combo.setFilters(VECTOR_LAYER_FILTER)
        self.layer_combo.setAllowEmptyLayer(False)
        self.layer_combo.setMinimumWidth(200)
        self.layer_combo.setMaximumWidth(260)
        self.layer_combo.layerChanged.connect(self._on_combo_layer)
        top.addWidget(self.layer_combo)
        top.addSpacing(8)
        self.count_label = W.label("", "strong")
        top.addWidget(self.count_label)
        top.addSpacing(m.group_gap)

        row = W.ButtonRow(m)
        row.add_stretch()
        self.columns_btn = row.add(W.button(
            m, "Columns", icons.native(icons.COLUMNS),
            "Show, hide and reorder columns"))
        self.zoom_btn = row.add(W.button(
            m, "Zoom to selection", icons.native(icons.ZOOM_SELECTED),
            "Zoom the map to the selected features"))
        self.selected_btn = row.add(W.button(
            m, "Selected only", icons.native(icons.SELECTED_ONLY),
            "Show only the features selected on the map\n"
            "(statistics follow the same records)", checkable=True))
        self.summary_btn = row.add(W.button(
            m, "Summary: On", icons.native(icons.SUMMARY),
            "Show or hide the column statistics strip", checkable=True))
        self.pivot_btn = row.add(W.button(
            m, "Open Pivot", icons.tool(icons.PIVOT),
            "Summarise the filtered records in a Pivot Table"))
        export_menu = QMenu(self)
        self.export_xlsx = export_menu.addAction(
            icons.native(icons.XLSX), "Excel (.xlsx) — filtered records")
        self.export_csv = export_menu.addAction(
            icons.native(icons.CSV), "CSV (.csv) — filtered records")
        export_menu.addSeparator()
        self.copy_action = export_menu.addAction(
            icons.native(icons.COPY), "Copy selected rows")
        self.copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        if not X.HAS_OPENPYXL:
            self.export_xlsx.setEnabled(False)
            self.export_xlsx.setToolTip("Excel export needs the 'openpyxl' package")
        self.export_btn = row.add(W.button(
            m, "Export", icons.native(icons.EXPORT),
            "Export the filtered records (with their scope recorded)",
            menu=export_menu))
        self.clear_btn = row.add(W.button(
            m, "Clear filters", icons.with_badge(icons.FILTER, "x", t),
            "Clear every column filter"))
        top.addWidget(row, 1)
        top.addSpacing(m.gap)
        self.query_btn = W.square_button(m, icons.native_for(icons.QUERY, t), "",
                                         checkable=True)
        top.addWidget(self.query_btn)
        self.dock_btn = W.dock_button(m)
        top.addWidget(self.dock_btn)
        root.addLayout(top)

        # ---- scope line ----
        scope = QHBoxLayout()
        scope.setSpacing(m.gap)
        self.scope_label = W.ElideLabel()
        self.scope_label.setObjectName("gtsDim")
        scope.addWidget(self.scope_label, 1)
        self.external_chip = W.button(m, "", icons.glyph("cross", t.text_dim),
                                      "Remove the records handed over from "
                                      "the Pivot Table")
        self.external_chip.setFixedHeight(m.compact_h)
        self.external_chip.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.external_chip.setVisible(False)
        self.external_chip.clicked.connect(self.clear_external_scope)
        scope.addWidget(self.external_chip)
        root.addLayout(scope)

        # ---- table ----
        self.model = FeatureTableModel(self.theme, self)
        self.header = ExplorerHeader(self.theme, self.metrics)
        self.view = ExplorerTableView(self.theme, self.metrics, self.header, self)
        self.view.setModel(self.model)
        self.header.values_provider = self._values_for
        root.addWidget(self.view, 1)

        # ---- status ----
        self.status = W.StatusStrip(m)
        self.status.cancelRequested.connect(self._cancel_jobs)
        root.addWidget(self.status)

        # ---- signals ----
        self.columns_btn.clicked.connect(self._choose_columns)
        self.zoom_btn.clicked.connect(self.zoom_to_selection)
        self.selected_btn.toggled.connect(self._on_selected_only)
        self.summary_btn.toggled.connect(self._on_summary_toggled)
        self.pivot_btn.clicked.connect(self._open_pivot)
        self.clear_btn.clicked.connect(self.clear_filters)
        self.query_btn.toggled.connect(self._toggle_map_filter)
        self.export_xlsx.triggered.connect(lambda: self.export("xlsx"))
        self.export_csv.triggered.connect(lambda: self.export("csv"))
        self.copy_action.triggered.connect(self.copy_selected_rows)
        self.header.filterChanged.connect(self._on_filter_changed)
        self.header.profileClicked.connect(self._show_profile_details)
        self.header.layoutChanged.connect(self._on_header_layout)
        self.header.sectionClicked.connect(self._on_sort_clicked)
        self.view.horizontalScrollBar().valueChanged.connect(
            lambda _v: self._profile_timer.start())
        self.view.rowPressed.connect(self._on_row_pressed)
        self.view.rowDragged.connect(self._on_row_dragged)
        self.view.rowActivated.connect(self._zoom_to_row)
        self.view.currentMoved.connect(self._on_current_moved)
        self.view.selectAllRequested.connect(self.select_all)
        self.view.copyRequested.connect(self.copy_selected_rows)
        self.view.toggleRequested.connect(
            lambda r: self._on_row_pressed(r, Qt.KeyboardModifier.ControlModifier))
        self.view.contextRequested.connect(self._context_menu)
        self.view.row_header.rowPressed.disconnect()
        self.view.row_header.rowPressed.connect(self._on_tick_pressed)

        visible = settings.value("explorer/summaryVisible")
        self.summary_btn.blockSignals(True)
        self.summary_btn.setChecked(visible)
        W.set_button_text(self.summary_btn, "Summary: On" if visible else "Summary: Off")
        self.summary_btn.blockSignals(False)
        self.header.set_profiles_visible(visible)

    # ====================================================================
    # LAYER
    # ====================================================================

    def window_title(self):
        name = self.layer.name() if self.layer is not None else ""
        return f"Attribute Explorer — {name}" if name else "Attribute Explorer"

    def set_layer(self, layer):
        if layer is self.layer:
            return
        if self.layer is not None:
            self._save_layout()
            self._restore_map_filter()
            self._disconnect_layer()
        self.runner.new_generation()
        self.layer = layer
        self.external = None
        self.external_chip.setVisible(False)
        self.profiles = {}
        self._scope_key = None
        self._spec = None
        self.sort = None
        self._anchor = None
        self._has_result = False
        self.model.set_layer(layer)
        self.header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.header.rebuild(self.model.fields, self.model.kinds)
        for logical, card in self.header.cards.items():
            card.set_state("pending", message="Calculating…")
        self.layer_combo.blockSignals(True)
        self.layer_combo.setLayer(layer)
        self.layer_combo.blockSignals(False)
        if layer is None:
            return
        self._restore_layout()
        self.model.selected = set(layer.selectedFeatureIds())
        self._connect_layer()
        self.zoom_btn.setEnabled(layer.isSpatial())
        self.zoom_btn.setToolTip(
            "Zoom the map to the selected features" if layer.isSpatial()
            else "This table has no geometry")
        self.titleChanged.emit()
        self.refresh()

    def _connect_layer(self):
        lyr = self.layer
        pairs = [
            (lyr.selectionChanged, self._on_layer_selection),
            (lyr.attributeValueChanged, self._on_attribute_changed),
            (lyr.featureAdded, self._on_features_changed),
            (lyr.featuresDeleted, self._on_features_changed),
            (lyr.dataChanged, self._on_features_changed),
            (lyr.afterRollBack, self._on_features_changed),
            (lyr.subsetStringChanged, self._on_subset_changed),
            (lyr.editingStarted, self._update_query_btn),
            (lyr.editingStopped, self._on_editing_stopped),
            (lyr.updatedFields, self._on_schema_changed),
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

    def _on_combo_layer(self, layer):
        if layer is None or layer is self.layer:
            return
        if not isinstance(layer, QgsVectorLayer):
            self.layer_combo.setLayer(self.layer)
            return
        self.layerChangeRequested.emit(self, layer)

    def revert_combo(self):
        self.layer_combo.blockSignals(True)
        self.layer_combo.setLayer(self.layer)
        self.layer_combo.blockSignals(False)

    def shutdown(self):
        self._closing = True
        self._refresh_timer.stop()
        self._data_timer.stop()
        self._profile_timer.stop()
        if self._layout_timer.isActive():
            self._layout_timer.stop()
        try:
            self._save_layout()
        except RuntimeError:
            pass
        self.runner.new_generation()
        self._restore_map_filter()
        self._disconnect_layer()

    # ====================================================================
    # LAYER CHANGES
    # ====================================================================

    def _on_layer_selection(self, *_args):
        previous = self.model.selected
        self.model.selected = set(self.layer.selectedFeatureIds())
        if not self._table_selecting and not self.selected_only:
            # selected on the map (or elsewhere): bring the row into view
            self._scroll_to_features(self.model.selected - previous)
        self._selection_version += 1
        self.view.viewport().update()
        self.view.verticalHeader().viewport().update()
        self._update_status()
        if self.selected_only:
            self._schedule_refresh(250)

    def _on_attribute_changed(self, fid, _idx, _value):
        self.model.invalidate([fid])
        self._data_timer.start()

    def _on_features_changed(self, *_args):
        if self._subset_busy:            # our own map filter, not new data
            return
        self._data_timer.start()

    def _on_subset_changed(self):
        if self._subset_busy:
            return
        if self._map_filter:
            # the layer filter was changed elsewhere (e.g. the Query Builder)
            self._map_filter = False
            self._applied_subset = None
            self._set_query_checked(False)
            if self.layer is not None:
                self.layer.removeCustomProperty(SUB.PROPERTY)
            self.status.set_status("The layer filter was changed outside the "
                                   "explorer — map filter turned off", "warning")
        self._on_features_changed()

    def _on_data_settled(self):
        if self.layer is None or self._closing:
            return
        self._revision += 1
        self.model.invalidate()
        self.refresh()

    def _on_schema_changed(self):
        if self.layer is None or self._closing:
            return
        # keep filters of fields that still exist (by name)
        old = {cell.name: cell.current_filter()
               for cell in self.header.cells.values()}
        self._save_layout()
        layer = self.layer
        self.runner.new_generation()
        self._revision += 1
        self.model.set_layer(layer)
        self.header.rebuild(self.model.fields, self.model.kinds)
        self._restore_layout()
        dropped = []
        for logical, cell in self.header.cells.items():
            flt = old.pop(cell.name, None)
            if flt is not None and flt.kind == cell.kind:
                cell.set_filter(flt)
        dropped = [n for n, f in old.items() if f is not None]
        self.profiles = {}
        self._scope_key = None
        self.refresh()
        if dropped:
            self.status.set_status(
                f"Fields changed — removed the filter on {', '.join(dropped)}",
                "warning")

    # ====================================================================
    # SCOPE + SCANS
    # ====================================================================

    def _active_filters(self):
        out = []
        for cell in self.header.cells.values():
            flt = cell.current_filter()
            if flt is not None:
                out.append(flt)
        return out

    def _schedule_refresh(self, delay=None):
        if delay is None:
            delay = settings.value("explorer/filterDelayMs")
        self._refresh_timer.start(max(0, int(delay)))

    def _on_filter_changed(self, logical, immediate):
        cell = self.header.cells.get(logical)
        if cell is not None:
            cell.set_error("")
        if immediate:
            self._refresh_timer.stop()
            self.refresh()
        else:
            self._schedule_refresh()

    def refresh(self, sort_only=False):
        """Recompute the scope (and the profiles when the scope changed)."""
        if self.layer is None or self._closing:
            return
        self._refresh_timer.stop()
        filters = self._active_filters()
        expr, errors = FL.combine(filters)
        for cell in self.header.cells.values():
            cell.set_error(errors.get(cell.name, ""))
        if errors:
            field, msg = next(iter(errors.items()))
            self.status.set_status(
                f"{field}: {msg} — showing the last valid result", "warning")
            return

        restrict = None
        restrict_key = None
        if self.selected_only:
            restrict = set(self.model.selected)
            restrict_key = ("sel", self._selection_version)
        if self.external is not None:
            ext = self.external["fids"]
            restrict = set(ext) if restrict is None else (restrict & ext)
            restrict_key = (restrict_key, "ext", id(ext))
        self._expr = expr
        self._restrict = restrict
        self._filters_text = [f.describe() for f in filters]

        scope_key = (self._revision, expr, restrict_key)
        new_scope = scope_key != self._scope_key
        if new_scope:
            self._scope_key = scope_key
            self.profiles = {}
        self.runner.new_generation()
        self._profile_queue = []
        with self._unfiltered():
            spec = ScanSpec(self.layer, expr, restrict)
            self._available = max(self.layer.featureCount(), 0)
            # the map filter follows the column filters only, so with a
            # restricted table their records are counted separately
            map_spec = (ScanSpec(self.layer, expr, None)
                        if self._map_filter and restrict is not None else None)
        self._map_filters = filters
        self._spec = spec
        order = self._profile_order() if self.header.profiles_visible else []
        order = [i for i in order if i not in self.profiles]
        self._per_scan = max(1, VALUE_BUDGET // max(spec.estimate, 1))
        if spec.estimate > QUICK_ROWS_LIMIT:
            # large scope: show the records first, profiles follow
            first, self._profile_queue = [], order
        else:
            first = order[:self._per_scan]
            self._profile_queue = order[self._per_scan:]
        for logical in order:
            self.header.cards[logical].set_state("pending", message="Calculating…")
        kinds = list(self.model.kinds)
        sort = self.sort

        def work(job, spec=spec, first=first, kinds=kinds, sort=sort,
                 map_spec=map_spec):
            fids, cols = scan(spec, job, first, True, sort)
            profs = {}
            for idx, col in zip(first, cols):
                job.check()
                profs[idx] = S.compute_profile(kinds[idx], col)
            map_fids = scan(map_spec, job)[0] if map_spec is not None else None
            return fids, profs, map_fids

        self.status.set_status(
            "Sorting…" if sort_only else f"Filtering {self.layer.name()}…", "busy")
        self.status.set_busy(True)
        self.runner.start(f"Attribute Explorer: {self.layer.name()}", work,
                          self._on_scan_done, tag="scan")

    def _on_scan_done(self, job):
        if self._closing:
            return
        if not self.runner.is_current(job):
            return
        if job.error:
            self._job_failed(job, "Filtering failed")
            return
        if job.cancelled:
            self._finish_busy("Cancelled — the previous result is still shown",
                              "warning")
            return
        fids, profs, map_fids = job.result
        if self._map_filter:             # before the rows are read
            self._sync_map_filter(fids if map_fids is None else map_fids)
        self.model.set_fids(fids)
        self._anchor = None
        self.view.verticalHeader().updateGeometry()
        self._has_result = True
        self.scope_version += 1
        self.profiles.update(profs)
        for logical, prof in profs.items():
            self.header.cards[logical].set_state("ready", prof)
        self._update_labels()
        self.scopeChanged.emit()
        self._next_profile_chunk()

    def _profile_order(self):
        """Shown columns, those on screen first."""
        on_screen = self.header.logicals_in_view()
        rest = [i for i in self.header.visible_logicals() if i not in on_screen]
        return on_screen + rest

    def _next_profile_chunk(self):
        queue = [i for i in self._profile_queue if i not in self.profiles]
        if not queue or self._spec is None or not self.header.profiles_visible:
            self._profile_queue = []
            self._finish_busy()
            return
        on_screen = set(self.header.logicals_in_view())
        queue.sort(key=lambda i: 0 if i in on_screen else 1)
        chunk, self._profile_queue = queue[:self._per_scan], queue[self._per_scan:]
        spec = self._spec
        kinds = list(self.model.kinds)
        done = len(self.profiles)
        total = done + len(queue)

        def work(job, spec=spec, chunk=chunk, kinds=kinds):
            _fids, cols = scan(spec, job, chunk, False)
            out = {}
            for idx, col in zip(chunk, cols):
                job.check()
                out[idx] = S.compute_profile(kinds[idx], col)
            return out

        self.status.set_status(
            f"Calculating column summaries… {done:,} of {total:,} columns", "busy")
        self.status.set_busy(True)
        self.runner.start(f"Attribute Explorer: summaries of {self.layer.name()}",
                          work, self._on_profiles_done, tag="profile")

    def _on_profiles_done(self, job):
        if self._closing or not self.runner.is_current(job):
            return
        if job.error:
            self._job_failed(job, "Column summaries failed")
            return
        if job.cancelled:
            for logical, card in self.header.cards.items():
                if logical not in self.profiles:
                    card.set_state("cancelled",
                                   message="Not calculated — click to calculate")
            self._finish_busy("Column summaries cancelled", "warning")
            return
        self.profiles.update(job.result)
        for logical, prof in job.result.items():
            self.header.cards[logical].set_state("ready", prof)
        self._next_profile_chunk()

    def _compute_missing_profiles(self):
        """Profiles for columns that became visible (scroll, unhide)."""
        if not self._has_result or not self.header.profiles_visible \
                or self.runner.busy or self._spec is None:
            return
        missing = [i for i in self._profile_order() if i not in self.profiles]
        if not missing:
            return
        for logical in missing:
            self.header.cards[logical].set_state("pending", message="Calculating…")
        self._profile_queue = missing
        self._next_profile_chunk()

    def _cancel_jobs(self):
        self.runner.cancel_all()

    def _job_failed(self, job, title):
        QgsMessageLog.logMessage(f"{title}:\n{job.error}", LOG_TAG)
        last = job.error.strip().splitlines()[-1] if job.error else ""
        self._finish_busy(f"{title}: {last}", "error")

    def _finish_busy(self, text=None, level="info"):
        if self.runner.busy:
            return
        self.status.set_busy(False)
        if text:
            self.status.set_status(text, level)
        else:
            self._update_status()

    # ====================================================================
    # LABELS
    # ====================================================================

    def _update_labels(self):
        layer = self.layer
        if layer is None:
            return
        self._update_query_btn()
        shown = len(self.model.fids)
        available = self._available_count()
        self.count_label.setText(f"{available:,} records" if available >= 0 else "")
        if self.external is not None:
            scope = "Pivot records"
        elif self.selected_only:
            scope = "Selected records"
        else:
            scope = "Filtered records"
        filters = getattr(self, "_filters_text", [])
        if filters:
            names = ", ".join(sorted({f.split(" ")[0] for f in filters}))
            ftext = (f"{len(filters)} active filter{'s' if len(filters) > 1 else ''}"
                     f": {names}")
        else:
            ftext = "No active filters"
        self.scope_label.setText(
            f"Statistics for: {scope}  •  Exact  •  {shown:,} of "
            f"{max(available, 0):,}  •  {ftext}")
        self.scope_label.setToolTip("<br>".join(
            ["<b>Active filters</b>"] + filters) if filters else "")
        self._update_status()

    def _update_status(self):
        if self.layer is None or self.runner.busy:
            return
        shown = len(self.model.fids)
        available = self._available_count()
        sel = len(self.model.selected)
        self.status.set_status(
            f"{shown:,} shown / {available:,} available  •  {sel:,} selected")
        extra = "Map filter on  •  Read only" if self._map_on_layer() else "Read only"
        if self.shortcut_text:
            extra += f"  •  {self.shortcut_text}"
        self.status.set_extra(extra)

    def scope_description(self):
        return self.scope_label.fullText().replace("Statistics for: ", "")

    # ====================================================================
    # TOGGLES
    # ====================================================================

    def _on_selected_only(self, checked):
        self.selected_only = checked
        self.refresh()

    def _on_summary_toggled(self, checked):
        W.set_button_text(self.summary_btn, "Summary: On" if checked else "Summary: Off")
        settings.set_value("explorer/summaryVisible", checked)
        self.header.set_profiles_visible(checked)
        if checked:
            self._profile_timer.start()
        self._layout_timer.start()

    def clear_filters(self):
        for cell in self.header.cells.values():
            cell.set_filter(None)
            cell.set_error("")
        self.external = None
        self.external_chip.setVisible(False)
        self.refresh()

    # ====================================================================
    # MAP FILTER (the layer's Query Builder filter follows the table)
    # ====================================================================

    def _map_on_layer(self):
        return self._applied_subset not in (None, self._base_subset)

    def _available_count(self):
        if self._map_on_layer():
            return self._available
        return max(self.layer.featureCount(), 0) if self.layer is not None else 0

    def _set_subset(self, text):
        self._subset_busy = True
        try:
            return self.layer.setSubsetString(text)
        finally:
            self._subset_busy = False

    @contextmanager
    def _unfiltered(self):
        """Read the layer without the map filter this explorer set (the
        table and its statistics always see every record)."""
        layer = self.layer
        if layer is None or not self._map_on_layer() or layer.isEditable():
            yield
            return
        self._set_subset(self._base_subset)
        try:
            yield
        finally:
            self._set_subset(self._applied_subset)

    def _set_query_checked(self, checked):
        self.query_btn.blockSignals(True)
        self.query_btn.setChecked(checked)
        self.query_btn.blockSignals(False)
        self._update_query_btn()

    def _update_query_btn(self, *_args):
        layer = self.layer
        editing = layer is not None and layer.isEditable()
        active = bool(self._active_filters())
        self.query_btn.setEnabled(layer is not None and not editing
                                  and (active or self._map_filter))
        if editing:
            tip = ("Map filter (Query Builder)\nNot available while the layer "
                   "is being edited.")
        elif self._map_filter:
            tip = ("Map filter on: the layer's Query Builder filter follows the "
                   "table filters, so the map shows the same records.\n"
                   "Click to turn it off and show every feature again "
                   "(the table keeps its filters).")
        elif not active:
            tip = ("Map filter (Query Builder)\nAdd a column filter first, then "
                   "click to show only the filtered records on the map.")
        else:
            tip = ("Map filter (Query Builder): show only the filtered records "
                   "on the map.\nWhile on, the layer's Query Builder filter "
                   "follows the table filters.")
        self.query_btn.setToolTip(tip)

    def _toggle_map_filter(self, on):
        layer = self.layer
        if not on:
            self._restore_map_filter()
            self.status.set_status(
                "Map filter off — the map shows every feature again "
                "(the table keeps its filters)")
            return
        problem = None
        if layer is None:
            problem = "No layer"
        elif layer.isEditable():
            problem = "The map filter is not available while the layer is edited"
        elif SUB.dialect(layer) is None:
            problem = (f"The map filter is not available for "
                       f"{layer.providerType()} layers")
        elif SUB.with_base(layer.subsetString(), "x") is None:
            problem = ("The layer's own filter is an SQL query, so the map "
                       "filter cannot be added to it")
        if problem:
            self._set_query_checked(False)
            self.status.set_status(problem, "warning")
            return
        self._map_filter = True
        self._base_subset = layer.subsetString()
        self._applied_subset = None
        layer.setCustomProperty(SUB.PROPERTY, self._base_subset)
        self._update_query_btn()
        self.refresh()

    def _sync_map_filter(self, column_fids):
        """Point the layer filter at the records of the column filters:
        readable SQL when the provider matches the table exactly, otherwise
        the list of their feature ids (checked by count either way)."""
        layer = self.layer
        if layer.isEditable():
            self.status.set_status("The map filter stays as it is while the "
                                   "layer is being edited", "warning")
            return
        filters = getattr(self, "_map_filters", [])
        candidates = [SUB.filters_sql(layer, filters),
                      SUB.ids_subset(layer, column_fids)]
        for ours in candidates:
            if ours is None:
                continue
            text = SUB.with_base(self._base_subset, ours)
            if text is None or not self._set_subset(text):
                continue
            count = layer.featureCount()
            if ours == "" or count < 0 or count == len(column_fids):
                self._applied_subset = text
                return
        self._restore_map_filter()
        self.status.set_status(
            "The map filter could not be applied to this layer (too many "
            "records for an id list) — it was turned off", "warning")

    def _restore_map_filter(self):
        """Give the layer its own filter back (map filter off)."""
        layer = self.layer
        if self._map_filter and layer is not None:
            try:
                if not layer.isEditable():
                    if layer.subsetString() != self._base_subset:
                        self._set_subset(self._base_subset)
                    layer.removeCustomProperty(SUB.PROPERTY)
            except RuntimeError:         # layer already deleted
                pass
        self._map_filter = False
        self._applied_subset = None
        try:
            self._set_query_checked(False)
        except RuntimeError:
            pass

    def _on_editing_stopped(self):
        self._update_query_btn()
        if self._map_filter:
            self.refresh()

    def set_external_scope(self, fids, label):
        """Show only *fids* (records handed over from a pivot cell)."""
        self.external = {"fids": set(fids), "label": label}
        self.external_chip.setText(f"  {label}")
        self.external_chip.setVisible(True)
        self.refresh()

    def clear_external_scope(self):
        self.external = None
        self.external_chip.setVisible(False)
        self.refresh()

    def set_column_filter(self, field_name, flt):
        for cell in self.header.cells.values():
            if cell.name == field_name:
                cell.set_filter(flt)
        self.refresh()

    # ====================================================================
    # SORT + LAYOUT
    # ====================================================================

    def _on_sort_clicked(self, logical):
        if self.sort is not None and self.sort[0] == logical:
            self.sort = (logical, True) if not self.sort[1] else None
        else:
            self.sort = (logical, False)
        if self.sort is None:
            self.header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        else:
            self.header.setSortIndicator(
                logical, Qt.SortOrder.DescendingOrder if self.sort[1]
                else Qt.SortOrder.AscendingOrder)
        self._layout_timer.start()
        self.refresh(sort_only=True)

    def _on_header_layout(self):
        self._layout_timer.start()
        self._profile_timer.start()

    def _layout_state(self):
        h = self.header
        cols = []
        for visual in range(h.count()):
            logical = h.logicalIndex(visual)
            cols.append({"name": self.model.fields[logical].name(),
                         "width": h.sectionSize(logical),
                         "hidden": h.isSectionHidden(logical)})
        sort = None
        if self.sort is not None:
            sort = [self.model.fields[self.sort[0]].name(), self.sort[1]]
        return {"version": 1, "columns": cols, "sort": sort}

    def _save_layout(self):
        if self.layer is None or not self.model.fields:
            return
        try:
            text = json.dumps(self._layout_state())
        except (TypeError, ValueError):
            return
        if self.layer.customProperty(LAYOUT_PROPERTY) != text:
            self.layer.setCustomProperty(LAYOUT_PROPERTY, text)

    def _restore_layout(self):
        h = self.header
        text = self.layer.customProperty(LAYOUT_PROPERTY) if self.layer else None
        index = {f.name(): i for i, f in enumerate(self.model.fields)}
        for logical, fld in enumerate(self.model.fields):
            h.resizeSection(logical, self._default_width(logical))
        if not text:
            return
        try:
            state = json.loads(text)
        except (TypeError, ValueError):
            return
        target = 0
        for col in state.get("columns", []):
            logical = index.get(col.get("name"))
            if logical is None:
                continue
            current = h.visualIndex(logical)
            if current != target:
                h.moveSection(current, target)
            target += 1
            width = int(col.get("width") or 0)
            if width >= h.minimumSectionSize():
                h.resizeSection(logical, width)
            h.setSectionHidden(logical, bool(col.get("hidden")))
        sort = state.get("sort")
        if sort and sort[0] in index:
            self.sort = (index[sort[0]], bool(sort[1]))
            self.header.setSortIndicator(
                self.sort[0], Qt.SortOrder.DescendingOrder if self.sort[1]
                else Qt.SortOrder.AscendingOrder)
        h.relayout()

    def _default_width(self, logical):
        fm = self.metrics.bold_fm
        name = self.model.fields[logical].name()
        # wide enough for the profile card's "Distinct 9,999 (100%)" row
        card = self.metrics.fm.horizontalAdvance("Distinct   99,999 (100%)") + 26
        if self.model.kinds[logical] in F.TEMPORAL:
            # the whole YYYY-MM-DD hint beside the operator and calendar buttons
            card = max(card, self.metrics.fm.horizontalAdvance("YYYY-MM-DD") + 96)
        return max(150, card, min(340, fm.horizontalAdvance(name) + 56))

    def _choose_columns(self):
        h = self.header
        entries = []
        for visual in range(h.count()):
            logical = h.logicalIndex(visual)
            fld = self.model.fields[logical]
            entries.append((fld.name(), icons.field(self.model.kinds[logical], self.theme),
                            "", not h.isSectionHidden(logical)))
        dlg = W.FieldChecklistDialog(
            entries, self.metrics, self.theme, "Columns",
            "Tick the columns to show; drag to change their order. "
            "Hidden columns are not filtered or summarised on screen, and "
            "never change the records in scope.", movable=True, parent=self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        shown = set(dlg.checked_names())
        index = {f.name(): i for i, f in enumerate(self.model.fields)}
        for target, name in enumerate(dlg.ordered_names()):
            logical = index[name]
            current = h.visualIndex(logical)
            if current != target:
                h.moveSection(current, target)
            h.setSectionHidden(logical, name not in shown)
        h.relayout()
        self._layout_timer.start()
        self._profile_timer.start()

    # ====================================================================
    # SELECTION
    # ====================================================================

    def _select(self, fids, behaviour):
        if self.layer is not None:
            self._table_selecting = True
            try:
                self.layer.selectByIds(list(fids), behaviour)
            finally:
                self._table_selecting = False

    def _scroll_to_features(self, fids):
        """Scroll (vertically only) so the first of *fids* in table order is
        in the middle of the view."""
        rows = [r for r in map(self.model.row_of, fids) if r is not None]
        if not rows:
            return
        bar = self.view.horizontalScrollBar()
        keep = bar.value()
        column = max(self.view.columnAt(0), 0)
        self.view.scrollTo(self.model.index(min(rows), column),
                           QAbstractItemView.ScrollHint.PositionAtCenter)
        bar.setValue(keep)

    def _on_row_pressed(self, row, mods):
        fid = self.model.fid(row)
        if fid is None:
            return
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        self._drag_behaviour = SELECT_ADD if ctrl else SELECT_SET
        if shift and self._anchor is not None:
            lo, hi = sorted((self._anchor, row))
            self._select(self.model.fids[lo:hi + 1],
                         SELECT_ADD if ctrl else SELECT_SET)
            return
        self._anchor = row
        if ctrl:
            self._select([fid], SELECT_REMOVE if fid in self.model.selected
                         else SELECT_ADD)
        else:
            self._select([fid], SELECT_SET)

    def _on_tick_pressed(self, row, mods):
        """The tick box toggles one feature (Shift extends a range)."""
        if mods & Qt.KeyboardModifier.ShiftModifier:
            self._on_row_pressed(row, mods | Qt.KeyboardModifier.ControlModifier)
        else:
            self._on_row_pressed(row, Qt.KeyboardModifier.ControlModifier)

    def _on_row_dragged(self, row):
        if self._anchor is None:
            return
        lo, hi = sorted((self._anchor, row))
        self._select(self.model.fids[lo:hi + 1], self._drag_behaviour)

    def _on_current_moved(self, row, mods):
        if mods & Qt.KeyboardModifier.ControlModifier:
            return
        if mods & Qt.KeyboardModifier.ShiftModifier and self._anchor is not None:
            lo, hi = sorted((self._anchor, row))
            self._select(self.model.fids[lo:hi + 1], SELECT_SET)
        else:
            self._anchor = row
            self._select([self.model.fids[row]], SELECT_SET)

    def select_all(self):
        self._select(self.model.fids, SELECT_SET)

    def invert_selection(self):
        sel = self.model.selected
        self._select([f for f in self.model.fids if f not in sel], SELECT_SET)

    def deselect_all(self):
        if self.layer is not None:
            self._table_selecting = True
            try:
                self.layer.removeSelection()
            finally:
                self._table_selecting = False

    # ====================================================================
    # MAP
    # ====================================================================

    def zoom_to_selection(self):
        layer = self.layer
        if layer is None or not layer.isSpatial():
            return
        ids = layer.selectedFeatureIds()
        if not ids:
            self.status.set_status("Select features first (click rows in the "
                                   "table)", "warning")
            return
        canvas = self.iface.mapCanvas()
        canvas.zoomToSelected(layer)
        canvas.flashFeatureIds(layer, ids[:500])

    def _zoom_to_row(self, row):
        layer = self.layer
        fid = self.model.fid(row)
        if layer is None or fid is None or not layer.isSpatial():
            return
        canvas = self.iface.mapCanvas()
        canvas.zoomToFeatureIds(layer, [fid])
        canvas.flashFeatureIds(layer, [fid])

    # ====================================================================
    # CONTEXT MENU / COPY
    # ====================================================================

    def _context_menu(self, global_pos, row, col):
        menu = QMenu(self)
        n_sel = len(self.model.selected)
        if row >= 0 and col >= 0:
            value_text = self.model.display_text(row, col)
            act = menu.addAction(icons.native(icons.COPY), "Copy cell value")
            act.triggered.connect(
                lambda: QGuiApplication.clipboard().setText(value_text))
        act = menu.addAction(icons.native(icons.COPY),
                             f"Copy selected rows ({n_sel:,})")
        act.setEnabled(n_sel > 0)
        act.triggered.connect(self.copy_selected_rows)
        menu.addSeparator()
        menu.addAction(icons.native(icons.SELECT_ALL),
                       "Select all shown records").triggered.connect(self.select_all)
        menu.addAction(icons.native(icons.INVERT),
                       "Invert selection (shown records)").triggered.connect(
            self.invert_selection)
        menu.addAction(icons.native(icons.DESELECT),
                       "Deselect all").triggered.connect(self.deselect_all)
        if self.layer is not None and self.layer.isSpatial():
            menu.addSeparator()
            if row >= 0:
                menu.addAction(icons.native(icons.ZOOM_TO),
                               "Zoom to this feature").triggered.connect(
                    lambda: self._zoom_to_row(row))
            act = menu.addAction(icons.native(icons.ZOOM_SELECTED),
                                 "Zoom to selection")
            act.setEnabled(n_sel > 0)
            act.triggered.connect(self.zoom_to_selection)
        if row >= 0 and col >= 0:
            menu.addSeparator()
            name = self.model.fields[col].name()
            raw = self.model.raw_value(row, col)
            kind = self.model.kinds[col]
            act = menu.addAction(icons.native(icons.FILTER),
                                 f"Filter {name} to this value")
            act.triggered.connect(lambda: self._filter_to_value(col, raw))
            act.setEnabled(kind != F.OTHER)
            menu.addAction(f"Hide column {name}").triggered.connect(
                lambda: self._hide_column(col))
        menu.exec(global_pos)

    def _filter_to_value(self, logical, raw):
        cell = self.header.cells[logical]
        kind = cell.kind
        if F.is_null(raw):
            flt = FL.ColumnFilter(cell.name, kind, "null")
        elif kind == F.BOOL:
            flt = FL.ColumnFilter(cell.name, kind, "true" if raw else "false")
        elif kind in (F.DATE, F.DATETIME):
            text = F.display_value(raw)[:10]
            flt = FL.ColumnFilter(cell.name, kind, "eq", text)
        elif kind in F.NUMERIC or kind == F.TEXT:
            flt = FL.ColumnFilter(cell.name, kind, "in", values=[raw])
        else:
            flt = FL.ColumnFilter(cell.name, kind, "eq", F.display_value(raw))
        cell.set_filter(flt)
        self.refresh()

    def _hide_column(self, logical):
        self.header.setSectionHidden(logical, True)
        self.header.relayout()
        self._layout_timer.start()

    def _shown_columns(self):
        return self.header.visible_logicals()

    def copy_selected_rows(self):
        sel = self.model.selected
        if not sel:
            self.status.set_status("Nothing selected to copy", "warning")
            return
        rows = [r for r, fid in enumerate(self.model.fids) if fid in sel]
        if not rows:
            self.status.set_status(
                "The selected features are not among the shown records", "warning")
            return
        if len(rows) > 100000:
            answer = QMessageBox.question(
                self, "Copy selected rows",
                f"Copy {len(rows):,} rows to the clipboard?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        cols = self._shown_columns()
        lines = ["\t".join(self.model.fields[c].name() for c in cols)]
        for r in rows:
            lines.append("\t".join(
                X.export_value(self.model.raw_value(r, c)).replace("\t", " ")
                .replace("\n", " ") for c in cols))
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.status.set_status(f"Copied {len(rows):,} row(s) to the clipboard",
                               "success")

    # ====================================================================
    # EXPORT
    # ====================================================================

    def export(self, fmt):
        layer = self.layer
        if layer is None or not self._has_result:
            return
        n = len(self.model.fids)
        if n == 0:
            QMessageBox.information(self, "Export", "There are no records to export.")
            return
        if fmt == "xlsx" and n > X.XLSX_MAX_ROWS:
            QMessageBox.warning(
                self, "Export", f"{n:,} records exceed the Excel limit of "
                f"{X.XLSX_MAX_ROWS:,} rows. Use CSV instead.")
            return
        ext = ".xlsx" if fmt == "xlsx" else ".csv"
        flt = "Excel workbook (*.xlsx)" if fmt == "xlsx" else "CSV (*.csv)"
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                       for ch in layer.name()) or "records"
        path, _sel = QFileDialog.getSaveFileName(
            self, "Export filtered records", f"{safe}_filtered{ext}", flt)
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext
        cols = self._shown_columns()
        header = [self.model.fields[c].name() for c in cols]
        fids = list(self.model.fids)
        with self._unfiltered():
            source = QgsVectorLayerFeatureSource(layer)
        meta = {
            "Layer": layer.name(),
            "Source": layer.publicSource(),
            "Scope": self.scope_description(),
            "Filter expression": self._expr or "(none)",
            "Records exported": f"{n:,}",
            "Columns": ", ".join(header),
        }

        def rows(job):
            step = 2000
            for start in range(0, len(fids), step):
                job.check()
                job.report(100.0 * start / len(fids))
                chunk = fids[start:start + step]
                req = QgsFeatureRequest().setFilterFids(chunk)
                req.setFlags(NO_GEOMETRY)
                req.setSubsetOfAttributes(cols)
                got = {f.id(): f for f in source.getFeatures(req)}
                for fid in chunk:
                    feat = got.get(fid)
                    if feat is None:
                        continue
                    yield [feat.attribute(c) for c in cols]

        def work(job):
            if fmt == "xlsx":
                return X.write_xlsx(path, layer.name(), header, rows(job), meta)
            return X.write_csv(path, header, rows(job), meta)

        def done(job):
            if self._closing:
                return
            self.status.set_busy(False)
            if job.error:
                QgsMessageLog.logMessage(f"Export failed:\n{job.error}", LOG_TAG)
                last = job.error.strip().splitlines()[-1]
                self.status.set_status(f"Export failed: {last}", "error")
                QMessageBox.critical(self, "Export failed", last)
            elif job.cancelled:
                self.status.set_status("Export cancelled", "warning")
            else:
                extra = " (scope saved alongside)" if fmt == "csv" else ""
                self.status.set_status(
                    f"Exported {job.result:,} records to {path}{extra}", "success")

        self.status.set_status(f"Exporting {n:,} records…", "busy")
        self.status.set_busy(True)
        self.runner.start(f"Attribute Explorer: export {layer.name()}", work,
                          done, tag="export", flags=TASK_VISIBLE)

    # ====================================================================
    # PROFILE DETAILS / VALUE LISTS / PIVOT
    # ====================================================================

    def _show_profile_details(self, logical):
        prof = self.profiles.get(logical)
        if prof is None:
            self._compute_missing_profiles()
            return
        fld = self.model.fields[logical]
        pop = ProfileDetails(logical, fld.name(), self.model.kinds[logical], prof,
                             self.scope_description(), self.theme, self.metrics,
                             self)
        pop.setStyleSheet(pop.styleSheet() + stylesheet(self.theme, self.metrics))
        pop.filterRequested.connect(self._on_details_filter)
        card = self.header.cards[logical]
        pos = card.mapToGlobal(card.rect().bottomLeft())
        pop.adjustSize()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            pos.setX(max(avail.left(), min(pos.x(), avail.right() - pop.width())))
            pos.setY(max(avail.top(), min(pos.y(), avail.bottom() - pop.height())))
        pop.move(pos)
        pop.show()

    def _on_details_filter(self, logical, text):
        cell = self.header.cells[logical]
        cell.set_filter(FL.ColumnFilter(cell.name, cell.kind, "in", values=[text]))
        self.refresh()

    def _values_for(self, logical):
        layer = self.layer
        if layer is None:
            return []
        kind = self.model.kinds[logical]
        prof = self.profiles.get(logical)
        counts = dict(prof.frequencies) if prof is not None and kind in (
            F.TEXT, F.OTHER) else {}
        try:
            with self._unfiltered():
                raw = layer.uniqueValues(logical, VALUE_LIST_LIMIT)
        except Exception:
            raw = set()
        values = sorted((v for v in raw if not F.is_null(v)), key=F.sort_key)
        entries = [(None, F.NULL_TEXT, prof.missing if prof is not None else None)]
        fld = self.model.fields[logical]
        for v in values:
            try:
                text = fld.displayString(v)
            except Exception:
                text = F.display_value(v)
            entries.append((v, text if text != "" else "(empty string)",
                            counts.get(v) if counts else None))
        return entries

    def _open_pivot(self):
        if self.layer is None or not self._has_result:
            return
        self.openPivotRequested.emit(self.layer, self.current_scope())

    def current_scope(self):
        """Scope hand-over for the Pivot Table (a snapshot of the ids)."""
        n = len(self.model.fids)
        return {"kind": "explorer", "fids": list(self.model.fids),
                "label": f"Explorer filtered records ({n:,})",
                "description": self.scope_description(),
                "explorer": self, "version": self.scope_version}
