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
from qgis.core import QgsFeature, QgsVectorLayerUtils
from qgis.gui import QgsActionMenu, QgsAttributeDialog, QgsAttributeEditorContext

from ..core import fields as F
from . import explorer_dialogs as D
from . import explorer_edit as E
from . import explorer_ops as OPS
from . import icons
from .explorer_bars import EditBar, QuickUpdateBar

_ADD_MODE = QgsAttributeEditorContext.AddFeatureMode


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
        q.applyRequested.connect(self.quick_apply)
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
        self._tip(b.calc_btn, editing,
                  "Field calculator: update a field, or create a new or virtual field",
                  "Start editing first")
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
    def target_ids(self, key):
        layer = self.layer
        if key == "layer":
            return list(layer.allFeatureIds())
        if key == "filtered":
            return list(self.model.fids)
        if key == "selected_layer":
            return list(layer.selectedFeatureIds())
        sel = self.model.selected
        return [f for f in self.model.fids if f in sel]

    def target_counts(self):
        layer = self.layer
        sel = self.model.selected
        return {"layer": max(layer.featureCount(), 0), "filtered": len(self.model.fids),
                "selected_layer": len(sel),
                "selected_shown": sum(1 for f in sel if self.model.row_of(f) is not None)}

    def open_calculator(self, field_name=None):
        if not self.edit.editing:
            return
        self.flush_editors()
        D.FieldCalculatorDialog(self, field_name).exec()

    def _toggle_quick(self, on):
        """Opening or closing the row never changes data."""
        self.quick_bar.setVisible(bool(on) and self.edit.editing)
        if on:
            self.quick_bar.set_target_counts(self.target_counts())
            editable = [self.model.fields[i].name() for i in sorted(self.model.editable_cols)]
            if editable and self.quick_bar.field_combo.currentField() not in editable:
                self.quick_bar.field_combo.setField(editable[0])

    def _quick_plan(self):
        q = self.quick_bar
        name = q.field_combo.currentField()
        idx = self.layer.fields().indexFromName(name)
        if idx < 0 or idx not in self.model.editable_cols:
            raise OPS.OperationError(f"{name or 'The field'} is not editable "
                                     f"({self.edit.field_editable(idx)[1]})")
        if not q.expression.isValidExpression():
            raise OPS.OperationError("Enter a valid expression")
        ids = self.target_ids(q.target())
        return name, idx, q.expression.expression(), ids

    def quick_preview(self):
        q = self.quick_bar
        q.set_target_counts(self.target_counts())
        try:
            name, idx, expression, ids = self._quick_plan()
            rows = OPS.evaluate_preview(self.layer, idx, expression, ids)
        except OPS.OperationError as exc:
            q.preview_title.setText("Preview not available")
            q.preview_lines.setText(str(exc))
            q.preview_note.setText("")
            q.apply_btn.setEnabled(False)
            q.preview.setVisible(True)
            return
        shown = set(self.model.fids)
        outside = sum(1 for f in ids if f not in shown) if q.target() in (
            "layer", "selected_layer") else 0
        q.preview_title.setText(
            f"Preview  •  {len(ids):,} targeted feature{'s' if len(ids) != 1 else ''}  •  "
            f"{outside:,} outside this view")
        lines = [f"fid {fid} &nbsp; {'NULL' if F.is_null(old) else F.display_value(old)}"
                 f" &rarr; <b>{'NULL' if new is None else F.display_value(new)}</b>"
                 for fid, old, new in rows]
        q.preview_lines.setText("<br>".join(lines) or "No features in the target")
        q.preview_note.setText("Expression valid  •  Target IDs captured  •  One undo step")
        self._quick_ids = (name, idx, expression, list(ids))
        W_text = f"Apply to {len(ids):,} feature{'s' if len(ids) != 1 else ''}"
        q.apply_btn.setText(W_text)
        q.apply_btn.setMinimumWidth(self.metrics.bold_fm.horizontalAdvance(W_text) + 36)
        q.apply_btn.setEnabled(bool(ids))
        q.preview.setVisible(True)

    def quick_apply(self):
        plan = getattr(self, "_quick_ids", None)
        if not plan:
            return
        name, idx, expression, ids = plan
        self.flush_editors()
        try:
            n = OPS.apply_expression(self.layer, idx, expression, ids,
                                     f"Quick update: {name}", self)
        except OPS.OperationError as exc:
            self.status.set_status(f"Quick update not applied — {exc}", "error")
            return
        self.quick_bar.preview.setVisible(False)
        self._quick_ids = None
        if n is None:
            self.status.set_status("Quick update cancelled — nothing was changed", "warning")
        else:
            self.status.set_status(
                f"Quick update: {name} calculated for {len(ids):,} feature(s), "
                f"{n:,} value(s) changed (one undo step, unsaved)", "success")

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
        act = menu.addAction(icons.native(icons.CALCULATE), "Open QGIS field calculator…")
        act.setToolTip("QGIS's own Field Calculator for this layer. It calculates the whole "
                       "layer, or only the selected features when you tick that option.\n"
                       "It ignores the Explorer's filters unless the map filter is on "
                       "(then only the records shown on the map are calculated).\n"
                       "Use the Explorer's Field calculator to target the filtered records.")
        act.setEnabled(self._qgis_calculator_action() is not None)
        act.triggered.connect(self.open_qgis_calculator)
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

    def _qgis_calculator_action(self):
        getter = getattr(self.iface, "actionOpenFieldCalculator", None)
        return getter() if getter is not None else None

    def open_qgis_calculator(self):
        """QGIS's Field Calculator works on the active layer: make it this
        one, write any open cell editor first, then open it."""
        action = self._qgis_calculator_action()
        if action is None:
            return
        self.flush_editors()
        self.iface.setActiveLayer(self.layer)
        action.trigger()

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
