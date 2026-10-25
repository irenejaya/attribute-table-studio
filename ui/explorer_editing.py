# -*- coding: utf-8 -*-
"""
Attribute Explorer editing commands (mixed into :class:`ExplorerPanel`).

The edit row, the Quick update row, the More menu and the editing keys all
land here.  Every command works on the layer's own edit session (through
:class:`~.explorer_edit.EditController`), targets explicit feature ids, and
explains itself in its tooltip when it is unavailable.
"""

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QGuiApplication
from qgis.PyQt.QtWidgets import QComboBox, QGroupBox
from qgis.core import QgsFeature, QgsVectorLayerUtils
from qgis.gui import (
    QgsActionMenu, QgsAttributeDialog, QgsAttributeEditorContext, QgsFieldCalculator,
)

from ..core import fields as F
from . import explorer_dialogs as D
from . import explorer_edit as E
from . import explorer_ops as OPS
from . import icons
from .explorer_bars import EditBar, QuickUpdateBar

_ADD_MODE = QgsAttributeEditorContext.AddFeatureMode


def _preselect_field(dlg, name):
    """Open QGIS's Field Calculator on "Update existing field" with *name*.
    The dialog has no API for it, so this uses its widget names and quietly
    does nothing if they ever change."""
    box = dlg.findChild(QGroupBox, "mUpdateExistingGroupBox")
    combo = dlg.findChild(QComboBox, "mExistingFieldComboBox")
    if box is None or combo is None or not box.isEnabled():
        return
    i = combo.findText(name)
    if i >= 0:
        box.setChecked(True)
        combo.setCurrentIndex(i)


class EditingMixin:
    """Edit row, Quick update row and editing commands."""

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_edit_rows(self, root):
        m, t = self.metrics, self.theme
        self.edit = E.EditController(self)
        self.edit_bar = EditBar(m, t)
        root.addWidget(self.edit_bar)
        self.quick_bar = QuickUpdateBar(m, t)
        self.quick_bar.setVisible(False)
        root.addWidget(self.quick_bar)
        self._dirty_timer = QTimer(self)
        self._dirty_timer.setSingleShot(True)
        self._dirty_timer.setInterval(80)
        self._dirty_timer.timeout.connect(self._update_dirty)
        self._mutations = set()
        self._mutation_timer = QTimer(self)
        self._mutation_timer.setSingleShot(True)
        self._mutation_timer.setInterval(0)
        self._mutation_timer.timeout.connect(self._flush_mutations)
        self._pending_scan = None

        b = self.edit_bar
        self.edit.changed.connect(self._on_edit_state)
        b.edit_btn.toggled.connect(self._toggle_edit)
        b.save_btn.clicked.connect(self.save_edits)
        b.discard_btn.clicked.connect(lambda: self.edit.discard())
        b.undo_btn.clicked.connect(self.edit.undo)
        b.redo_btn.clicked.connect(self.edit.redo)
        b.add_btn.clicked.connect(self.add_record_in_table)
        b.add_table_action.triggered.connect(self.add_record_in_table)
        b.add_form_action.triggered.connect(self.add_record_with_form)
        b.delete_btn.clicked.connect(self.delete_selected)
        b.cut_btn.clicked.connect(self.cut_features)
        b.copy_btn.clicked.connect(self.copy_features)
        b.paste_btn.clicked.connect(self.paste_features)
        b.calc_btn.clicked.connect(lambda: self.open_calculator())
        b.quick_btn.toggled.connect(self._toggle_quick)
        b.multi_btn.clicked.connect(self.multi_edit)
        b.new_field_btn.clicked.connect(self.new_field)
        b.delete_field_btn.clicked.connect(lambda: self.delete_field())
        b.reload_btn.clicked.connect(self.reload_layer)
        b.more_menu.aboutToShow.connect(self._fill_more_menu)
        q = self.quick_bar
        q.previewRequested.connect(self.quick_preview)
        q.updateRequested.connect(self.quick_update)
        q.closeRequested.connect(lambda: b.quick_btn.setChecked(False))

    def _wire_editing(self):
        """Connections to the table (built after the edit rows)."""
        self.model.editRejected.connect(lambda msg: self.status.set_status(
            f"Value not accepted — {msg}", "error"))
        self.model.editWarned.connect(lambda msg: self.status.set_status(msg, "warning"))
        self.view.editKeyRequested.connect(self._on_edit_key)
        self.view.editorClosed.connect(self._apply_pending_scan)

    def _edit_layer_changed(self, layer):
        self.edit.set_layer(layer)
        self.quick_bar.set_layer(layer)
        self.edit_bar.quick_btn.blockSignals(True)
        self.edit_bar.quick_btn.setChecked(False)
        self.edit_bar.quick_btn.blockSignals(False)

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def _on_edit_state(self):
        layer = self.layer
        if layer is None or self._closing:
            return
        e, b = self.edit, self.edit_bar
        editing = e.editing
        b.edit_btn.blockSignals(True)
        b.edit_btn.setChecked(editing)
        b.edit_btn.blockSignals(False)
        blocker = e.edit_blocker()
        b.edit_btn.setEnabled(not blocker or editing)
        b.edit_btn.setToolTip(
            ("Stop editing (unsaved edits ask Save / Discard / Cancel)" if editing
             else f"Start editing {layer.name()} (shared with the map and every "
                  "other QGIS view)") if not blocker or editing
            else f"Editing is not available: {blocker}")
        modified = e.modified()
        group = e.transaction_layers()
        scope = (f"\nThe project uses transaction groups: this also saves "
                 f"{', '.join(n for n in group if n != layer.name())}"
                 if len(group) > 1 else "")
        self._tip(b.save_btn, editing and modified,
                  f"Save all unsaved changes of {layer.name()} — every record, including "
                  f"those hidden by filters and edits made in other QGIS views. Editing "
                  f"stays on.{scope}",
                  "Nothing to save" if editing else "Start editing first")
        self._tip(b.discard_btn, editing and modified,
                  f"Discard all unsaved edits of {layer.name()} (asks first; includes "
                  "edits made in other views)",
                  "Nothing to discard" if editing else "Start editing first")
        self._tip(b.undo_btn, e.can_undo(), f"Undo: {e.undo_text()}",
                  "Nothing to undo" if editing else "Start editing first")
        self._tip(b.redo_btn, e.can_redo(), f"Redo: {e.undo_text(redo=True)}",
                  "Nothing to redo" if editing else "Start editing first")
        can_add = editing and e.capable(E.CAP_ADD)
        geo = (" — a table record has no geometry (draw it on the map if you need "
               "one)" if layer.isSpatial() else "")
        self._tip(b.add_btn, can_add, f"Add a record in the table{geo}; the arrow "
                  "also offers a form", self._why(editing, E.CAP_ADD, "add records"))
        self._update_selection_commands()
        self._tip(b.paste_btn, can_add,
                  "Paste features from the QGIS clipboard (fields matched by name)",
                  self._why(editing, E.CAP_ADD, "add records"))
        editable_cols = [i for i in range(len(self.model.fields))
                         if e.field_editable(i)[0]]
        self._tip(b.calc_btn, True,
                  "Field calculator (QGIS's own): update a field, or create a new or "
                  "virtual field, for the whole layer or only the selected features. "
                  "It starts editing when needed.\nTo calculate only the filtered "
                  "records use Quick update › Update Filtered.", "")
        self._tip(b.quick_btn, editing, "Show or hide the Quick update row",
                  "Start editing first")
        if not editing and b.quick_btn.isChecked():
            b.quick_btn.blockSignals(True)
            b.quick_btn.setChecked(False)
            b.quick_btn.blockSignals(False)
            self.quick_bar.expression.setExpression("")
        self.quick_bar.setVisible(editing and b.quick_btn.isChecked())
        self._tip(b.new_field_btn, editing,
                  "New field: stored (provider types) or virtual (expression)",
                  "Start editing first")
        self._tip(b.delete_field_btn, editing and (e.capable(E.CAP_DELETE_FIELD)
                                                   or self._has_virtual_fields()),
                  "Delete a stored field (whole layer) or a virtual field",
                  self._why(editing, E.CAP_DELETE_FIELD, "delete fields"))
        self._tip(b.reload_btn, not modified,
                  "Reload the layer from its data source", "Save or discard the "
                  "unsaved edits before reloading")
        self.model.set_editing(editing, editable_cols)
        self.view.set_editing(editing)
        self._dirty_timer.start()
        self._update_query_btn()
        self._update_status()

    @staticmethod
    def _tip(btn, enabled, tip, why):
        btn.setEnabled(bool(enabled))
        btn.setToolTip(tip if enabled else f"{tip.split(chr(10))[0].split(' — ')[0]}"
                       f"\nUnavailable: {why}")

    def _why(self, editing, cap, what):
        if not editing:
            return "start editing first"
        if not self.edit.capable(cap):
            return f"the data source cannot {what}"
        return ""

    def _has_virtual_fields(self):
        from qgis.core import Qgis
        fields = self.layer.fields()
        return any(fields.fieldOrigin(i) == Qgis.FieldOrigin.Expression
                   for i in range(fields.count()))

    def _selection_counts(self):
        sel = self.model.selected
        shown = sum(1 for f in sel if self.model.row_of(f) is not None) if sel else 0
        return len(sel), len(sel) - shown

    def _update_selection_commands(self):
        if self.layer is None:
            return
        self._update_quick_state()
        e, b = self.edit, self.edit_bar
        editing = e.editing
        n, hidden = self._selection_counts()
        sel_text = f"{n:,} selected feature{'s' if n != 1 else ''}" + (
            f" ({hidden:,} not shown)" if hidden else "")
        can_delete = editing and e.capable(E.CAP_DELETE)
        self._tip(b.delete_btn, can_delete and n,
                  f"Delete {sel_text} (asks first, one undo step)",
                  self._why(editing, E.CAP_DELETE, "delete records") or "select features first")
        self._tip(b.cut_btn, can_delete and n, f"Cut {sel_text} to the QGIS clipboard",
                  self._why(editing, E.CAP_DELETE, "delete records") or "select features first")
        self._tip(b.copy_btn, n, f"Copy {sel_text} to the QGIS clipboard (attributes "
                  "and geometry; paste them here, on the map or in another layer)",
                  "select features first")
        self._tip(b.multi_btn, editing and n and e.capable(E.CAP_CHANGE),
                  f"Multi edit {sel_text} with the layer's form",
                  self._why(editing, E.CAP_CHANGE, "change values") or "select features first")

    def _update_dirty(self):
        if self._closing:
            return
        layer = self.layer
        buf = layer.editBuffer() if layer is not None and layer.isEditable() else None
        if buf is None:
            self.model.set_dirty({}, set())
        else:
            dirty = {fid: set(vals.keys())
                     for fid, vals in buf.changedAttributeValues().items()}
            self.model.set_dirty(dirty, set(buf.addedFeatures().keys()))
        self.view.viewport().update()

    def flush_editors(self, commit=True):
        """Close the active cell editor / write the form before saving,
        switching tools or views, and starting a calculation."""
        self.view.commit_editor(commit)
        form = getattr(self, "form_view", None)
        if form is not None and self.is_form_view():
            form.flush(commit)

    # ------------------------------------------------------------------
    # mutations from anywhere (table, map, forms, other views)
    # ------------------------------------------------------------------
    def _note_mutation(self, fids=None):
        """Invalidate at once (no stale statistics), rescan after a pause."""
        if fids is None:
            self._mutations.add(None)
        else:
            self._mutations.update(fids)
        self._mutation_timer.start()

    def _flush_mutations(self):
        if self._closing:
            return
        fids, self._mutations = self._mutations, set()
        self.model.invalidate(None if None in fids else fids)
        self.runner.new_generation()
        for logical, card in self.header.cards.items():
            if logical in self.profiles:
                card.set_state("pending", message="Updating…")
        self._dirty_timer.start()
        self._data_timer.start()
        if hasattr(self, "form_view") and self.is_form_view():
            self.form_view.list_model.invalidate()

    def _apply_pending_scan(self):
        """A scan that finished while a cell editor was open is applied once
        the editor closes (a model reset would lose the typed value)."""
        job, self._pending_scan = self._pending_scan, None
        if job is not None:
            self._on_scan_done(job)

    # ------------------------------------------------------------------
    # edit session commands
    # ------------------------------------------------------------------
    def _toggle_edit(self, on):
        if not self.edit.toggle(on):
            self._on_edit_state()

    def save_edits(self):
        return self.edit.save()

    def _on_edit_key(self, name):
        if name == "undo":
            self.edit.undo()
        elif name == "redo":
            self.edit.redo()
        elif name == "cut":
            self.cut_features()
        elif name == "paste":
            self.paste_features()
        elif name == "delete":
            self.delete_selected()

    def confirm_close(self, what="close"):
        return self.edit.confirm_close(what)

    # ------------------------------------------------------------------
    # records
    # ------------------------------------------------------------------
    def add_record_in_table(self):
        layer = self.layer
        if not (self.edit.editing and self.edit.capable(E.CAP_ADD)):
            return
        self.flush_editors()
        fid = OPS.add_feature_in_table(layer)
        if fid is None:
            self.status.set_status("The record could not be added", "error")
            return
        self._after_add([fid])

    def add_record_with_form(self):
        layer = self.layer
        if not (self.edit.editing and self.edit.capable(E.CAP_ADD)):
            return
        self.flush_editors()
        feat = QgsVectorLayerUtils.createFeature(layer, context=layer.createExpressionContext())
        before = set(OPS.edited_fids(layer))
        dlg = QgsAttributeDialog(layer, feat, False, self, True, D.form_context(self))
        dlg.setMode(_ADD_MODE)
        if dlg.exec():
            new = sorted(set(layer.editBuffer().addedFeatures().keys()) - before) \
                if layer.editBuffer() is not None else []
            self._after_add(new)

    def _after_add(self, fids):
        """Acknowledge new records; offer Show edited and new when the
        active filters hide them (the filters are never changed for you)."""
        self._pending_focus = fids[-1] if fids else None
        n = len(fids)
        self.status.set_status(
            f"Added {n} record{'s' if n != 1 else ''} (unsaved). Records that do not "
            "match the active filters are not shown.", "success",
            action=("Show edited and new", lambda: self.set_view_scope("edited")))

    def delete_selected(self):
        layer = self.layer
        e = self.edit
        if not (e.editing and e.capable(E.CAP_DELETE)):
            return
        ids = sorted(layer.selectedFeatureIds())
        if not ids:
            self.status.set_status("Select the features to delete first (row ticks)",
                                   "warning")
            return
        self.flush_editors()
        n, hidden = len(ids), self._selection_counts()[1]
        cascade, extra = OPS.delete_impact(layer, ids)
        text = (f"Delete <b>{n:,}</b> selected feature{'s' if n != 1 else ''} from "
                f"<b>{layer.name()}</b>?")
        if hidden:
            text += f"<br>{hidden:,} of them are not shown in this view (filters or scope)."
        if cascade:
            text += (f"<br><br>Related records are deleted with them (relations): "
                     f"{cascade}.")
        text += "<br><br>This is one undo step and is not saved until you press Save edits."
        if E.ask(self, "Delete features", text,
                 [("delete", f"Delete {n:,}", "destructive"), ("cancel", "Cancel", "reject")],
                 default="cancel", icon="warning") != "delete":
            return
        if OPS.delete_features(layer, ids):
            self.status.set_status(f"Deleted {n:,} feature(s) (unsaved)", "success")
        else:
            self.status.set_status("The features could not be deleted", "error")

    def copy_features(self):
        if not self.layer.selectedFeatureCount():
            return
        self.iface.copySelectionToClipboard(self.layer)
        n, hidden = self._selection_counts()
        self.status.set_status(
            f"Copied {n:,} feature(s) to the QGIS clipboard"
            + (f" ({hidden:,} not shown)" if hidden else ""), "success")

    def cut_features(self):
        e = self.edit
        if not (e.editing and e.capable(E.CAP_DELETE)) or not self.layer.selectedFeatureCount():
            return
        self.flush_editors()
        n = self.layer.selectedFeatureCount()
        self.iface.cutSelectionToClipboard(self.layer)
        self.status.set_status(f"Cut {n:,} feature(s) to the QGIS clipboard (unsaved)",
                               "success")

    def paste_features(self):
        e = self.edit
        if not (e.editing and e.capable(E.CAP_ADD)):
            return
        self.flush_editors()
        before = set(OPS.edited_fids(self.layer))
        self.iface.pasteFromClipboard(self.layer)
        new = sorted(set(OPS.edited_fids(self.layer)) - before)
        if new:
            self._after_add(new)

    # ------------------------------------------------------------------
    # fields
    # ------------------------------------------------------------------
    def new_field(self):
        self.flush_editors()
        D.NewFieldDialog(self).exec()

    def delete_field(self, name=None):
        self.flush_editors()
        D.DeleteFieldDialog(self, name).exec()

    def reload_layer(self):
        layer = self.layer
        if layer is None or self.edit.modified():
            return
        layer.reload()
        self._note_mutation()
        self.status.set_status(f"Reloaded {layer.name()} from its data source")

    # ------------------------------------------------------------------
    # bulk: calculator, Quick update, multi edit
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Field calculator (QGIS's own dialog) and Quick update (QGIS's field
    # calculation bar)
    # ------------------------------------------------------------------
    def table_filtered(self):
        """The table shows a filtered list - QGIS's attribute table is then
        in any filter mode but "Show All" (column, expression or form
        filters, or a scope such as selected / visible on map / edited)."""
        return bool(self._active_filters() or self.expr_filter or self.external
                    or self.view_scope != "all")

    def can_calculate(self, name):
        """*name* is offered by QGIS's Field Calculator ("Update existing
        field"), which starts editing itself when needed."""
        return self.layer is not None and not self.layer.readOnly() \
            and name in OPS.calculator_fields(self.layer)

    def open_calculator(self, field_name=None):
        """QGIS's own Field Calculator for this layer (as the native table's
        button opens it): whole layer - only the records the Query Builder
        filter lets through - or "Only update N selected features"; it
        starts editing when needed.  *field_name* opens it on "Update
        existing field" with that field."""
        if self.layer is None:
            return
        self.flush_editors()
        dlg = QgsFieldCalculator(self.layer, self)
        if field_name:
            _preselect_field(dlg, field_name)
        dlg.exec()

    def _update_quick_state(self):
        if self.layer is None:
            return
        filtered = self.table_filtered()
        n_main = len(self.model.fids) if filtered else max(self.layer.featureCount(), 0)
        self.quick_bar.set_state(filtered, n_main, self.layer.selectedFeatureCount())

    def _toggle_quick(self, on):
        """Opening or closing the row never changes data."""
        self.quick_bar.setVisible(bool(on) and self.edit.editing)
        if on:
            self._update_quick_state()

    def _quick_target(self, target):
        """Feature ids for *target* as QGIS picks them: Update All = every
        feature (None), Update Filtered = the records the table shows,
        Update Selected = every selected feature of the layer."""
        if target == "selected":
            return list(self.layer.selectedFeatureIds())
        if target == "filtered":
            return list(self.model.fids)
        return None

    def _quick_field(self):
        q = self.quick_bar
        name = q.field_combo.currentField()
        idx = self.layer.fields().indexFromName(name)
        if idx < 0:
            raise OPS.OperationError("Choose a field to update")
        if not q.expression.isValidExpression():
            raise OPS.OperationError("Enter a valid expression")
        return name, idx, q.expression.asExpression()

    def quick_preview(self):
        q = self.quick_bar
        self._update_quick_state()
        target = "filtered" if q.filtered else "all"
        label = "Update Filtered" if q.filtered else "Update All"
        n_main, n_sel = q._counts
        try:
            name, idx, expression = self._quick_field()
            rows = OPS.evaluate_preview(self.layer, idx, expression, self._quick_target(target))
        except OPS.OperationError as exc:
            q.preview_title.setText("Preview not available")
            q.preview_lines.setText(str(exc))
            q.preview_note.setText("")
            q.preview.setVisible(True)
            return
        q.preview_title.setText(f"Preview  •  {label}: {n_main:,} record{'s' if n_main != 1 else ''}"
                                f"  •  Update Selected: {n_sel:,}")
        lines = [f"fid {fid} &nbsp; {'NULL' if F.is_null(old) else F.display_value(old)}"
                 f" &rarr; <b>{'NULL' if F.is_null(new) else F.display_value(new)}</b>"
                 for fid, old, new in rows]
        q.preview_lines.setText("<br>".join(lines) or "No records to update")
        q.preview_note.setText("First values in the layer's order  •  nothing written yet")
        q.preview.setVisible(True)

    def quick_update(self, target):
        """Update All / Filtered / Selected, as QGIS's field calculation bar
        does it (one undo step)."""
        q = self.quick_bar
        try:
            name, idx, expression = self._quick_field()
        except OPS.OperationError as exc:
            self.status.set_status(f"Quick update not applied — {exc}", "error")
            return
        ids = self._quick_target(target)
        if ids is not None and not ids:
            self.status.set_status("Quick update: no records to update", "warning")
            return
        self.flush_editors()
        try:
            result = OPS.run_field_calculation(self.layer, idx, expression, ids,
                                               f"Quick update: {name}", self)
        except OPS.OperationError as exc:
            E.ask(self, "Update Attributes", "An error occurred while evaluating the "
                  f"calculation string:\n{exc}", [("ok", "OK", "accept")], icon="warning")
            self.status.set_status("Quick update not applied — nothing was changed", "error")
            return
        q.preview.setVisible(False)
        if result is None:
            self.status.set_status("Quick update cancelled — nothing was changed", "warning")
            return
        n, problems = result
        note = (f"; {problems:,} value{'s' if problems != 1 else ''} could not be stored in "
                f"{name} and became NULL or were shortened (as QGIS does)" if problems else "")
        self.status.set_status(f"Quick update: {name} calculated for {n:,} feature"
                               f"{'s' if n != 1 else ''} (one undo step, unsaved){note}",
                               "warning" if problems else "success")

    def multi_edit(self):
        ids = sorted(self.layer.selectedFeatureIds())
        if not (self.edit.editing and ids):
            return
        self.flush_editors()
        D.MultiEditDialog(self, ids).exec()

    # ------------------------------------------------------------------
    # More menu
    # ------------------------------------------------------------------
    def _current_cell(self):
        idx = self.view.currentIndex()
        if self.is_form_view() and self.form_view.current is not None:
            row = self.model.row_of(self.form_view.current)
            return (row if row is not None else -1), -1
        return (idx.row(), idx.column()) if idx.isValid() else (-1, -1)

    def _fill_more_menu(self):
        menu = self.edit_bar.more_menu
        menu.clear()
        hidden = self.edit_bar.overflow_buttons()
        if hidden:
            for btn in hidden:
                act = menu.addAction(btn.icon(), btn.toolTip().split("\n")[0])
                act.setEnabled(btn.isEnabled())
                if btn.isCheckable():
                    act.setCheckable(True)
                    act.setChecked(btn.isChecked())
                act.triggered.connect(lambda _c=False, b=btn: b.click())
            menu.addSeparator()
        row, col = self._current_cell()
        fid = self.model.fid(row) if row >= 0 else None
        if fid is None and self.model.selected:
            fid = next(iter(sorted(self.model.selected)))
        act = menu.addAction(icons.native(icons.FORM_VIEW), "Open feature form…")
        act.setEnabled(fid is not None)
        act.triggered.connect(lambda: self.open_feature_form(fid))
        self.add_actions_menu(menu, fid)
        menu.addSeparator()
        menu.addAction(icons.native(icons.COPY), "Copy selected rows as text").triggered.connect(
            self.copy_selected_rows)
        act = menu.addAction(icons.native(icons.COPY), "Copy cell value")
        act.setEnabled(row >= 0 and col >= 0)
        act.triggered.connect(lambda: QGuiApplication.clipboard().setText(
            self.model.display_text(row, col)))
        act = menu.addAction("Set cell to NULL")
        act.setEnabled(row >= 0 and col >= 0 and col in self.model.editable_cols)
        act.triggered.connect(lambda: self.set_cell_null(row, col))
        menu.addSeparator()
        act = menu.addAction(icons.native(icons.HISTORY), "Review unsaved changes…")
        act.setEnabled(self.edit.modified())
        act.triggered.connect(lambda: D.ReviewChangesDialog(self).exec())
        menu.addAction(icons.native(icons.FIELD_PROPS), "Layer field properties…").triggered.connect(
            self.open_field_properties)
        menu.addAction(icons.native(icons.TABLE), "Open native attribute table").triggered.connect(
            lambda: self.iface.showAttributeTable(self.layer))
        menu.addAction(icons.native(icons.HELP), "Help").triggered.connect(self.show_help)

    def add_actions_menu(self, menu, fid):
        """'Layer feature actions' submenu: the layer's QGIS actions for the
        feature (run with its expression context)."""
        sub = menu.addMenu(icons.native(icons.ACTION), "Layer feature actions")
        feat = self.layer.getFeature(fid) if fid is not None else QgsFeature()
        if fid is None or not feat.isValid():
            sub.setEnabled(False)
            return sub
        self._action_menu = QgsActionMenu(self.layer, feat, "Feature", self)
        acts = [a for a in self._action_menu.actions() if not a.isSeparator()]
        for a in self._action_menu.actions():
            sub.addAction(a)
        if not acts:
            sub.addAction("No actions defined for this layer").setEnabled(False)
        return sub

    def open_feature_form(self, fid):
        if fid is None:
            return
        self.flush_editors()
        feat = self.layer.getFeature(fid)
        if feat.isValid():
            self.iface.openFeatureForm(self.layer, feat, False, True)
            self._note_mutation([fid])

    def set_cell_null(self, row, col):
        if row < 0 or col < 0:
            return
        self.model.setData(self.model.index(row, col), None)

    def open_field_properties(self):
        try:
            self.iface.showLayerProperties(self.layer, "mOptsPage_SourceFields")
        except TypeError:
            self.iface.showLayerProperties(self.layer)

    def show_help(self):
        from .help_dialog import HelpDialog
        dlg = getattr(self, "_help_dialog", None)
        if dlg is None:
            dlg = self._help_dialog = HelpDialog(self, topic="explorer")
        dlg.show()
        dlg.raise_()
