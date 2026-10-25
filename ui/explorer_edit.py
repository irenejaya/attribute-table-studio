# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Attribute Explorer editing: the layer's QGIS edit session is the single
source of truth.

:class:`EditController` never keeps a private buffer.  It starts or joins
the layer's edit session, reports its state (read only, editing, unsaved
changes, saving, save failed) and runs Save / Discard / Stop with the
layer's own API, so edits made on the map, in a form, in the native table
or in another Explorer are the same edits.

Prompts go through :data:`ASK` (a function returning the chosen key), so
tests can answer them without showing a dialog.
"""

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    Qgis, QgsFeature, QgsProject, QgsVectorDataProvider, QgsVectorLayerUtils,
)

try:
    _CAP = Qgis.VectorProviderCapability
except AttributeError:                      # QGIS < 3.38
    _CAP = QgsVectorDataProvider.Capability

CAP_ADD = _CAP.AddFeatures
CAP_DELETE = _CAP.DeleteFeatures
CAP_CHANGE = _CAP.ChangeAttributeValues
CAP_ADD_FIELD = _CAP.AddAttributes
CAP_DELETE_FIELD = _CAP.DeleteAttributes

_ORIGIN = Qgis.FieldOrigin


def _ask_messagebox(parent, title, text, choices, default=None, icon="question"):
    """*choices*: [(key, label, role)] with role 'accept' | 'destructive' |
    'reject'.  Returns the chosen key (the reject key when closed)."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon({"question": QMessageBox.Icon.Question,
                 "warning": QMessageBox.Icon.Warning,
                 "error": QMessageBox.Icon.Critical,
                 "info": QMessageBox.Icon.Information}[icon])
    roles = {"accept": QMessageBox.ButtonRole.AcceptRole,
             "destructive": QMessageBox.ButtonRole.DestructiveRole,
             "reject": QMessageBox.ButtonRole.RejectRole}
    buttons = {}
    reject_key = None
    for key, label, role in choices:
        buttons[box.addButton(label, roles[role])] = key
        if role == "reject":
            reject_key = key
    for btn, key in buttons.items():
        if key == default:
            box.setDefaultButton(btn)
    box.exec()
    return buttons.get(box.clickedButton(), reject_key)


ASK = _ask_messagebox


def ask(*args, **kwargs):
    return ASK(*args, **kwargs)


class EditController(QObject):
    """Edit state of the Explorer's layer (shared with every QGIS view)."""

    changed = pyqtSignal()          # editing, unsaved counts or undo state

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.layer = None
        self._conns = []
        self._note = None           # ("Saving…", level) or a failed save
        self._counts = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._emit)

    # ------------------------------------------------------------------
    def set_layer(self, layer):
        self._disconnect()
        self._timer.stop()               # nothing queued for the old layer
        self.layer = layer
        self._note = None
        self._counts = None
        if layer is not None:
            pairs = [(layer.editingStarted, self._on_session),
                     (layer.editingStopped, self._on_session),
                     (layer.layerModified, self.schedule),
                     (layer.afterCommitChanges, self._on_committed),
                     (layer.afterRollBack, self._on_session),
                     (layer.readOnlyChanged, self.schedule)]
            stack = layer.undoStack()
            if stack is not None:
                pairs.append((stack.indexChanged, self.schedule))
            for sig, slot in pairs:
                sig.connect(slot)
            self._conns = pairs
        self._emit()

    def _disconnect(self):
        for sig, slot in self._conns:
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._conns = []

    def schedule(self, *_args):
        """Coalesce bursts (a calculation changes thousands of values)."""
        self._counts = None
        self._timer.start()

    def _emit(self):
        self._counts = None
        self.changed.emit()

    def _on_session(self, *_args):
        self._note = None
        self._emit()

    def _on_committed(self, *_args):
        self._emit()

    # ------------------------------------------------------------------
    # capabilities
    # ------------------------------------------------------------------
    @property
    def editing(self):
        try:
            return self.layer is not None and self.layer.isEditable()
        except RuntimeError:
            return False

    def capable(self, cap):
        layer = self.layer
        if layer is None or layer.dataProvider() is None:
            return False
        return bool(layer.dataProvider().capabilities() & cap)

    def edit_blocker(self):
        """Why editing cannot start ('' when it can)."""
        layer = self.layer
        if layer is None:
            return "No layer"
        if layer.readOnly():
            return "The layer is set to read-only"
        if not layer.supportsEditing():
            return "The data source does not support editing"
        return ""

    def field_editable(self, idx, editing=None):
        """(editable, reason) for a field while editing."""
        layer = self.layer
        if layer is None:
            return False, "No layer"
        if not (self.editing if editing is None else editing):
            return False, "Start editing first (Edit)"
        fields = layer.fields()
        if not 0 <= idx < fields.count():
            return False, "Unknown field"
        origin = fields.fieldOrigin(idx)
        if origin == _ORIGIN.Expression:
            return False, "Virtual field: calculated by an expression"
        if layer.editFormConfig().readOnly(idx):
            return False, "Read-only in the layer's form settings"
        if origin == _ORIGIN.Join:
            if not QgsVectorLayerUtils.fieldIsEditable(layer, idx, QgsFeature()):
                return False, "Joined field: not editable in its join settings"
            return True, ""
        provider = layer.dataProvider()
        if origin == _ORIGIN.Provider and provider is not None:
            src = fields.fieldOriginIndex(idx)
            if src in provider.pkAttributeIndexes() and provider.defaultValueClause(src):
                return False, "Generated by the data source (key field)"
            if not self.capable(CAP_CHANGE):
                return False, "The data source does not allow changing values"
        if not QgsVectorLayerUtils.fieldIsEditable(layer, idx, QgsFeature()):
            return False, "Not editable"
        return True, ""

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def counts(self):
        """Unsaved totals: cells, added, deleted, geometry, fields (None
        when only a 'modified' flag is known, e.g. transaction groups)."""
        if self._counts is not None:
            return self._counts
        layer = self.layer
        out = {"cells": 0, "added": 0, "deleted": 0, "geometry": 0, "fields": 0}
        buf = layer.editBuffer() if self.editing else None
        if buf is None:
            self._counts = None if self.editing and layer.isModified() else out
            return self._counts
        out["cells"] = sum(len(v) for v in buf.changedAttributeValues().values())
        out["added"] = len(buf.addedFeatures())
        out["deleted"] = len(buf.deletedFeatureIds())
        out["geometry"] = len(buf.changedGeometries())
        renamed = getattr(buf, "renamedAttributes", None)
        out["fields"] = (len(buf.addedAttributes()) + len(buf.deletedAttributeIds())
                         + (len(renamed()) if callable(renamed) else 0))
        self._counts = out
        return out

    def modified(self):
        return self.editing and self.layer.isModified()

    def summary(self):
        counts = self.counts()
        if counts is None:
            return "Unsaved layer edits"
        parts = []
        n = counts["cells"]
        if n:
            parts.append(f"{n:,} unsaved cell{'s' if n != 1 else ''}")
        if counts["added"]:
            parts.append(f"{counts['added']:,} added")
        if counts["deleted"]:
            parts.append(f"{counts['deleted']:,} deleted")
        if counts["geometry"]:
            parts.append(f"{counts['geometry']:,} geometr{'ies' if counts['geometry'] != 1 else 'y'} changed")
        if counts["fields"]:
            parts.append("fields changed")
        if not parts and self.modified():
            return "Unsaved layer edits"
        return "  •  ".join(parts)

    def status(self):
        """(text, level) for the edit status in the status bar."""
        if self._note is not None:
            return self._note
        if self.layer is None:
            return "", "info"
        if not self.editing:
            blocker = self.edit_blocker()
            return (f"Read only  •  {blocker}" if blocker else "Read only"), "info"
        if self.modified():
            return f"Editing layer  •  {self.summary()}", "editing"
        return "Editing layer  •  No unsaved changes", "editing-clean"

    def transaction_layers(self):
        """Names of the layers saved together with this one (empty when the
        project has no transaction groups)."""
        project = QgsProject.instance()
        try:
            mode = project.transactionMode()
            if mode == Qgis.TransactionMode.Disabled:
                return []
        except AttributeError:
            return []
        layer = self.layer
        key = layer.dataProvider().dataSourceUri().split(" table=")[0] \
            if layer.dataProvider() else ""
        names = []
        for lyr in project.mapLayers().values():
            try:
                if lyr.providerType() == layer.providerType() and \
                        lyr.dataProvider().dataSourceUri().split(" table=")[0] == key:
                    names.append(lyr.name())
            except AttributeError:
                continue
        return names

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def start(self):
        layer = self.layer
        if layer is None or self.editing:
            return self.editing
        blocker = self.edit_blocker()
        if blocker:
            self._note = (f"Cannot edit  •  {blocker}", "error")
            self._emit()
            return False
        ok = layer.startEditing()
        if not ok:
            self._note = ("Cannot edit  •  QGIS refused to start editing", "error")
        self._emit()
        return bool(ok)

    def save(self):
        """Save every unsaved change of the layer (not only the shown
        records); stays in edit mode.  Returns True on success."""
        layer = self.layer
        if not self.editing:
            return True
        self.panel.flush_editors()
        if not layer.isModified():
            return True
        self._note = ("Saving…", "busy")
        self._emit()
        ok = layer.commitChanges(False)
        if ok:
            self._note = None
            self.panel.status.set_status(f"Saved all edits of {layer.name()}", "success")
        else:
            errors = list(layer.commitErrors())
            self._note = ("Save failed  •  your edits are kept", "error")
            ask(self.panel, "Save failed",
                f"QGIS could not save all edits of <b>{layer.name()}</b>. "
                "The layer is still being edited and your changes are kept, "
                "so you can correct them and save again. Some changes may "
                "already have been written by the data source.<br><br>"
                + "<br>".join(e.replace("<", "&lt;") for e in errors[:12]),
                [("ok", "OK", "accept")], icon="error")
        self._emit()
        return bool(ok)

    def discard(self, confirm=True):
        """Discard every unsaved edit of the layer and keep editing."""
        layer = self.layer
        if not self.editing:
            return False
        self.panel.flush_editors(commit=False)
        if not layer.isModified():
            return True
        if confirm:
            choice = ask(
                self.panel, "Discard edits",
                f"Discard <b>all unsaved edits</b> of <b>{layer.name()}</b>?<br><br>"
                f"{self.summary()}.<br>This includes records hidden by filters "
                "and edits made in other QGIS views (map, forms, native table). "
                "It cannot be undone.",
                [("discard", "Discard all layer edits", "destructive"),
                 ("cancel", "Cancel", "reject")], default="cancel", icon="warning")
            if choice != "discard":
                return False
        layer.rollBack(False)              # keeps the edit session open
        layer.triggerRepaint()
        self._note = None
        self.panel.status.set_status(f"Discarded the unsaved edits of {layer.name()}",
                                     "warning")
        self._emit()
        return True

    def stop(self):
        """Stop editing; unsaved edits ask Save / Discard / Cancel."""
        layer = self.layer
        if not self.editing:
            return True
        self.panel.flush_editors()
        if layer.isModified():
            choice = ask(
                self.panel, "Stop editing",
                f"<b>{layer.name()}</b> has unsaved edits:<br>{self.summary()}.<br><br>"
                "Save them before you stop editing?",
                [("save", "Save", "accept"),
                 ("discard", "Discard all layer edits", "destructive"),
                 ("cancel", "Cancel", "reject")], default="save")
            if choice == "cancel":
                self._emit()
                return False
            if choice == "save":
                ok = layer.commitChanges(True)
                if not ok:
                    self._note = ("Save failed  •  your edits are kept", "error")
                    ask(self.panel, "Save failed",
                        "QGIS could not save the edits, so editing continues:<br>"
                        + "<br>".join(e.replace("<", "&lt;")
                                      for e in list(layer.commitErrors())[:12]),
                        [("ok", "OK", "accept")], icon="error")
                    self._emit()
                    return False
            else:
                layer.rollBack(True)
                layer.triggerRepaint()
        else:
            layer.rollBack(True)
        self._note = None
        self._emit()
        return True

    def toggle(self, on):
        return self.start() if on else self.stop()

    def undo(self):
        stack = self.layer.undoStack() if self.layer is not None else None
        if stack is not None and stack.canUndo():
            self.panel.flush_editors(commit=False)
            stack.undo()

    def redo(self):
        stack = self.layer.undoStack() if self.layer is not None else None
        if stack is not None and stack.canRedo():
            self.panel.flush_editors(commit=False)
            stack.redo()

    def undo_text(self, redo=False):
        stack = self.layer.undoStack() if self.layer is not None else None
        if stack is None:
            return ""
        return stack.redoText() if redo else stack.undoText()

    def can_undo(self):
        stack = self.layer.undoStack() if self.editing else None
        return bool(stack is not None and stack.canUndo())

    def can_redo(self):
        stack = self.layer.undoStack() if self.editing else None
        return bool(stack is not None and stack.canRedo())

    def confirm_close(self, what="close"):
        """Before closing (or switching) this view: Save and close / Close
        and keep layer edits / Cancel.  True when the view may close."""
        if not self.modified():
            self.panel.flush_editors()
            return True
        self.panel.flush_editors()
        verb = "switch" if what == "switch" else "close"
        choice = ask(
            self.panel, "Unsaved edits",
            f"<b>{self.layer.name()}</b> has unsaved edits:<br>{self.summary()}."
            f"<br><br>Closing this view never saves or discards them on its own.",
            [("save", f"Save and {verb}", "accept"),
             ("keep", f"{verb.capitalize()} and keep layer edits", "destructive"),
             ("cancel", "Cancel", "reject")], default="save")
        if choice == "cancel":
            return False
        if choice == "save":
            return self.save()
        return True
