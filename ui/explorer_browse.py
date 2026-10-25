# -*- coding: utf-8 -*-
"""
Attribute Explorer browsing tools (mixed into :class:`ExplorerPanel`):
View and Select menus, scope modes, the expression filter, stored
expressions, filter / select by form, selected to top, sort by expression,
case-sensitive text filters, conditional formatting, the Form view, and
the header and row context menus.

Scope model:

    records available through the layer
      -> View scope: All / Selected / Visible on Map / Failing Constraints /
                     Edited and New
      AND the column filters
      AND the expression filter
      AND a Pivot drill-through restriction

Selected only and the View scope are one setting shown in two places.
Changing filters never changes the feature selection.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QInputDialog, QMenu
from qgis.core import (
    QgsCoordinateTransform, QgsFeatureRequest, QgsProject, QgsVectorLayer,
)
from qgis.gui import QgsExpressionSelectionDialog

from ..core.compat import NO_GEOMETRY, QActionGroup
from . import explorer_dialogs as D
from . import icons
from . import widgets as W

# named, drawn and ordered as in the QGIS attribute table's filter menu
SCOPES = (("all", "Show All Features", icons.TABLE),
          ("selected", "Show Selected Features", icons.SELECTED_ONLY),
          ("map", "Show Features Visible on Map", icons.TABLE_VISIBLE),
          ("failing", "Show Features with Failing Constraints", icons.TABLE_INVALID),
          ("edited", "Show Edited and New Features", icons.TABLE_EDITED))
SCOPE_LABELS = {"all": "Filtered records", "selected": "Selected records",
                "map": "Records visible on the map", "edited": "Edited and new records",
                "failing": "Records failing constraints"}


class BrowseMixin:
    """View / Select menus and every browsing command."""

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_view_select(self, row):
        """View and Select menu buttons (the panel places them in its row)."""
        m = self.metrics
        self.view_menu = QMenu(self)
        self.view_menu.aboutToShow.connect(self._fill_view_menu)
        self.view_btn = W.button(m, "View", icons.native(icons.TABLE),
                                 "Table or form view, scope, expression tools, sorting "
                                 "and conditional formatting", menu=self.view_menu)
        self.select_menu = QMenu(self)
        self.select_menu.aboutToShow.connect(self._fill_select_menu)
        self.select_btn = W.button(m, "Select", icons.native(icons.SELECT_ALL),
                                   "Select shown or layer records, by expression or by "
                                   "value, and pan / zoom to the selection",
                                   menu=self.select_menu)

    def _browse_layer_changed(self, layer):
        self.expr_filter = None
        self.sort_expr = None
        self.view_scope = "all"
        self._failing_counts = None
        self.expr_chip.setVisible(False)
        if getattr(self, "_styles_signal", None) is not None:
            try:
                self._styles_signal.disconnect(self._on_styles_changed)
            except (TypeError, RuntimeError):
                pass
        self._styles_signal = None
        if layer is not None:
            self._styles_signal = layer.conditionalStyles().changed
            self._styles_signal.connect(self._on_styles_changed)
        if hasattr(self, "form_view"):
            self.form_view.set_layer(layer)

    # ------------------------------------------------------------------
    # View menu
    # ------------------------------------------------------------------
    def _fill_view_menu(self):
        menu = self.view_menu
        menu.clear()
        group = QActionGroup(menu)
        for key, text, icon in (("table", "Table view", icons.TABLE),
                                ("form", "Form view", icons.FORM_VIEW)):
            act = menu.addAction(icons.native(icon), text)
            act.setCheckable(True)
            act.setChecked(self.is_form_view() == (key == "form"))
            group.addAction(act)
            act.triggered.connect(lambda _c=False, k=key: self.set_form_view(k == "form"))
        menu.addSeparator()
        menu.addSection("Scope")
        scopes = QActionGroup(menu)
        for key, text, icon in SCOPES:
            act = menu.addAction(icons.native(icon), text)
            act.setCheckable(True)
            act.setChecked(self.view_scope == key)
            if key == "edited" and not self.edit.editing:
                act.setToolTip("Unsaved changes exist only while editing")
            if key == "map" and not self.layer.isSpatial():
                act.setEnabled(False)
            scopes.addAction(act)
            act.triggered.connect(lambda _c=False, k=key: self.set_view_scope(k))
        menu.addSeparator()
        menu.addAction(icons.native(icons.ADVANCED_FILTER), "Advanced Filter (Expression)"
                       ).triggered.connect(self.edit_expression_filter)
        act = menu.addAction("Clear expression filter")
        act.setEnabled(bool(self.expr_filter))
        act.triggered.connect(lambda: self.set_expression_filter(None))
        menu.addAction(icons.native(icons.STORED_FILTER), "Stored Filter Expressions"
                       ).triggered.connect(self.stored_expressions)
        menu.addAction(icons.native(icons.FORM_SELECT), "Filter by form…").triggered.connect(
            lambda: D.FormSearchDialog(self).exec())
        act = menu.addAction("Case-sensitive text filters")
        act.setCheckable(True)
        act.setChecked(self.case_sensitive)
        act.setToolTip("'contains' and 'starts with' match upper / lower case exactly")
        act.toggled.connect(self.set_case_sensitive)
        menu.addSeparator()
        act = menu.addAction(icons.native(icons.SELECTED_TOP), "Selected to top")
        act.setCheckable(True)
        act.setChecked(self.selected_top)
        act.toggled.connect(self.set_selected_top)
        menu.addAction(icons.native(icons.SORT), "Sort by expression…").triggered.connect(
            self.edit_sort_expression)
        act = menu.addAction("Clear sort")
        act.setEnabled(self.sort is not None or bool(self.sort_expr))
        act.triggered.connect(self.clear_sort)
        menu.addSeparator()
        menu.addAction(icons.native(icons.CONDITIONAL), "Conditional formatting…"
                       ).triggered.connect(lambda: D.ConditionalFormatDialog(self).exec())
        menu.addAction("Autosize all columns").triggered.connect(self.autosize_columns)

    # ------------------------------------------------------------------
    # scope
    # ------------------------------------------------------------------
    @property
    def selected_only(self):
        return self.view_scope == "selected"

    def set_view_scope(self, scope):
        if scope not in SCOPE_LABELS:
            return
        self.view_scope = scope
        self.selected_btn.blockSignals(True)
        self.selected_btn.setChecked(scope == "selected")
        self.selected_btn.blockSignals(False)
        if scope != "failing":
            self._failing_counts = None
        self.refresh()

    def _on_selected_only(self, checked):
        self.set_view_scope("selected" if checked else "all")

    def _map_rect(self):
        """Canvas extent in the layer's CRS (Visible on map scope)."""
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        try:
            xform = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(),
                                           self.layer.crs(), QgsProject.instance())
            return xform.transformBoundingBox(extent)
        except Exception:
            return extent

    def _on_canvas_extent(self):
        if self.view_scope == "map" and not self._closing:
            self._schedule_refresh(400)

    def scope_name(self):
        if self.external is not None:
            return "Pivot records"
        name = SCOPE_LABELS.get(self.view_scope, "Filtered records")
        if self.view_scope == "failing" and self._failing_counts:
            hard, soft = self._failing_counts
            name += f" ({hard:,} hard, {soft:,} soft)"
        return name

    # ------------------------------------------------------------------
    # expression filter
    # ------------------------------------------------------------------
    def set_expression_filter(self, expression, mode="replace"):
        expression = (expression or "").strip() or None
        current = self.expr_filter
        if expression and current and mode in ("and", "or"):
            expression = f"({current}) {mode.upper()} ({expression})"
        self.expr_filter = expression
        if expression:
            short = expression if len(expression) < 60 else expression[:57] + "…"
            self.expr_chip.setText(f"  Expression: {short}")
            self.expr_chip.setToolTip(f"{expression}\nClick to remove the expression filter")
        self.expr_chip.setVisible(bool(expression))
        self.refresh()

    def edit_expression_filter(self):
        dlg = D.ExpressionDialog(self, "Advanced Filter (Expression)", self.expr_filter or "",
                                 "filter", has_current=bool(self.expr_filter))
        if dlg.exec():
            self.set_expression_filter(dlg.expression(), dlg.mode())

    def stored_expressions(self):
        dlg = D.StoredExpressionsDialog(self)
        dlg.exec()
        if dlg.applied:
            self.set_expression_filter(dlg.applied, "replace")

    def set_case_sensitive(self, on):
        self.case_sensitive = bool(on)
        self.refresh()

    # ------------------------------------------------------------------
    # sorting
    # ------------------------------------------------------------------
    def set_selected_top(self, on):
        self.selected_top = bool(on)
        self.model.set_fids(self._ordered(list(self.model.fids)))
        self._layout_timer.start()

    def _ordered(self, fids):
        """Selected records first (filter membership is unchanged)."""
        if not self.selected_top or not self.model.selected:
            return fids
        sel = self.model.selected
        return [f for f in fids if f in sel] + [f for f in fids if f not in sel]

    def edit_sort_expression(self):
        current = self.sort_expr[0] if self.sort_expr else ""
        dlg = D.ExpressionDialog(self, "Sort by expression", current, "sort")
        if self.sort_expr:
            dlg.descending.setChecked(self.sort_expr[1])
        if dlg.exec():
            self.sort_expr = (dlg.expression(), dlg.descending.isChecked())
            self.sort = None
            self.header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            self._layout_timer.start()
            self.refresh(sort_only=True)

    def clear_sort(self):
        self.sort = None
        self.sort_expr = None
        self.header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self._layout_timer.start()
        self.refresh(sort_only=True)

    def sort_by(self, logical, descending):
        self.sort_expr = None
        self.sort = (logical, descending)
        self.header.setSortIndicator(logical, Qt.SortOrder.DescendingOrder if descending
                                     else Qt.SortOrder.AscendingOrder)
        self._layout_timer.start()
        self.refresh(sort_only=True)

    # ------------------------------------------------------------------
    # conditional formatting / columns
    # ------------------------------------------------------------------
    def _on_styles_changed(self, *_args):
        self.model.load_styles()
        self.view.viewport().update()

    def autosize_columns(self, logicals=None):
        for logical in (logicals if logicals is not None else self.header.visible_logicals()):
            self.view.resizeColumnToContents(logical)
            self.header.resizeSection(logical, max(self.header.sectionSize(logical),
                                                   self._default_width(logical) // 2, 90))
        self.header.relayout()
        self._layout_timer.start()

    def set_column_width(self, logical):
        width, ok = QInputDialog.getInt(self, "Column width",
                                        self.model.fields[logical].name(),
                                        self.header.sectionSize(logical), 40, 2000)
        if ok:
            self.header.resizeSection(logical, width)
            self.header.relayout()
            self._layout_timer.start()

    # ------------------------------------------------------------------
    # Select menu
    # ------------------------------------------------------------------
    def _fill_select_menu(self):
        menu = self.select_menu
        menu.clear()
        n, hidden = self._selection_counts()
        info = menu.addAction(f"{n:,} selected" + (f"  •  {hidden:,} outside this view"
                                                    if hidden else ""))
        info.setEnabled(False)
        menu.addSeparator()
        menu.addAction(icons.native(icons.SELECT_ALL), "Select all shown records"
                       ).triggered.connect(self.select_all)
        menu.addAction(icons.native(icons.SELECT_ALL), "Select all available layer records"
                       ).triggered.connect(self.select_layer_all)
        menu.addAction(icons.native(icons.INVERT), "Invert selection"
                       ).triggered.connect(self.invert_selection)
        count = self.layer.featureCount()
        if count < 0 or len(self.model.fids) < count:     # only differs when filtered
            menu.addAction(icons.native(icons.INVERT), "Invert selection (whole layer)"
                           ).triggered.connect(self.invert_layer_selection)
        menu.addAction(icons.native(icons.DESELECT), "Deselect features from all layers"
                       ).triggered.connect(self.deselect_all_layers)
        menu.addSeparator()
        menu.addAction(icons.native(icons.EXPR_SELECT), "Select by expression…"
                       ).triggered.connect(self.select_by_expression)
        menu.addAction(icons.native(icons.FORM_SELECT), "Select by value / form…"
                       ).triggered.connect(lambda: D.FormSearchDialog(self, select=True).exec())
        if self.layer.isSpatial():
            menu.addSeparator()
            act = menu.addAction(icons.native(icons.PAN_SELECTED), "Pan to selection")
            act.setEnabled(n > 0)
            act.triggered.connect(self.pan_to_selection)
            act = menu.addAction(icons.native(icons.ZOOM_SELECTED), "Zoom to selection")
            act.setEnabled(n > 0)
            act.triggered.connect(self.zoom_to_selection)

    def deselect_all_layers(self):
        """QGIS's "Deselect Features from All Layers"."""
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.selectedFeatureCount():
                if layer is self.layer:
                    self.deselect_all()
                else:
                    layer.removeSelection()

    def select_layer_all(self):
        self._table_selecting = True
        try:
            self.layer.selectAll()
        finally:
            self._table_selecting = False

    def invert_layer_selection(self):
        self._table_selecting = True
        try:
            self.layer.invertSelection()
        finally:
            self._table_selecting = False

    def select_by_expression(self):
        dlg = QgsExpressionSelectionDialog(self.layer, "", self)
        if hasattr(dlg, "setMapCanvas"):
            dlg.setMapCanvas(self.iface.mapCanvas())
        dlg.exec()

    def pan_to_selection(self):
        if self.layer.selectedFeatureCount():
            canvas = self.iface.mapCanvas()
            canvas.panToSelected(self.layer)
            canvas.flashFeatureIds(self.layer, self.layer.selectedFeatureIds()[:500])

    def _expression_ids(self, expression):
        req = QgsFeatureRequest().setFilterExpression(expression)
        req.setFlags(NO_GEOMETRY)
        req.setNoAttributes()
        return [f.id() for f in self.layer.getFeatures(req)]

    def flash_expression(self, expression):
        ids = self._expression_ids(expression)
        if ids and self.layer.isSpatial():
            self.iface.mapCanvas().flashFeatureIds(self.layer, ids[:500])

    def zoom_expression(self, expression):
        ids = self._expression_ids(expression)
        if ids and self.layer.isSpatial():
            canvas = self.iface.mapCanvas()
            canvas.zoomToFeatureIds(self.layer, ids)
            canvas.flashFeatureIds(self.layer, ids[:500])

    # ------------------------------------------------------------------
    # Form view
    # ------------------------------------------------------------------
    def is_form_view(self):
        return hasattr(self, "pages") and self.pages.currentIndex() == 1

    def set_form_view(self, form):
        if form == self.is_form_view():
            return
        if form:
            self.view.commit_editor()
            idx = self.view.currentIndex()
            fid = self.model.fid(idx.row()) if idx.isValid() else None
            if fid is None and self.model.selected:
                fid = next((f for f in self.model.fids if f in self.model.selected), None)
            self.form_view.set_fids(self.model.fids)
            if fid is not None:
                self.form_view.set_current(fid, flush=False)
            self.pages.setCurrentIndex(1)
            W.set_button_text(self.view_btn, "View: Form")
        else:
            self.form_view.flush()
            fid = self.form_view.current
            self.pages.setCurrentIndex(0)
            W.set_button_text(self.view_btn, "View")
            row = self.model.row_of(fid) if fid is not None else None
            if row is not None:
                col = max(self.view.columnAt(0), 0)
                self.view.setCurrentIndex(self.model.index(row, col))
                self._scroll_to_features([fid])

    # ------------------------------------------------------------------
    # context menus
    # ------------------------------------------------------------------
    def _header_menu(self, pos):
        logical = self.header.logicalIndexAt(pos.x())
        if logical < 0:
            return
        name = self.model.fields[logical].name()
        menu = QMenu(self)
        menu.addAction(icons.native(icons.SORT), f"Sort {name} ascending").triggered.connect(
            lambda: self.sort_by(logical, False))
        menu.addAction(icons.native(icons.SORT_REVERSE), f"Sort {name} descending"
                       ).triggered.connect(lambda: self.sort_by(logical, True))
        menu.addAction("Sort by expression…").triggered.connect(self.edit_sort_expression)
        act = menu.addAction("Clear sort")
        act.setEnabled(self.sort is not None or bool(self.sort_expr))
        act.triggered.connect(self.clear_sort)
        menu.addSeparator()
        menu.addAction("Autosize this column").triggered.connect(
            lambda: self.autosize_columns([logical]))
        menu.addAction("Autosize all columns").triggered.connect(self.autosize_columns)
        menu.addAction("Set column width…").triggered.connect(
            lambda: self.set_column_width(logical))
        menu.addAction(f"Hide column {name}").triggered.connect(
            lambda: self._hide_column(logical))
        menu.addAction(icons.native(icons.COLUMNS), "Columns…").triggered.connect(
            self._choose_columns)
        menu.addSeparator()
        act = menu.addAction(icons.native(icons.CALCULATE), f"Calculate {name}…")
        act.setEnabled(self.can_calculate(name))
        act.triggered.connect(lambda: self.open_calculator(name))
        act = menu.addAction(icons.native(icons.DELETE_FIELD), f"Delete field {name}…")
        act.setEnabled(self.edit.editing)
        act.triggered.connect(lambda: self.delete_field(name))
        menu.addAction(icons.native(icons.FIELD_PROPS), "Field properties…").triggered.connect(
            self.open_field_properties)
        menu.exec(self.header.mapToGlobal(pos))
