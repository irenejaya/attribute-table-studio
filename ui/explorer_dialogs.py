# -*- coding: utf-8 -*-
"""
Attribute Explorer dialogs, built on native QGIS widgets (expression
builder, attribute forms, conditional format panel) inside the suite's
look.
"""

from qgis.PyQt.QtCore import QMetaType, Qt
from qgis.PyQt.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QRadioButton, QScrollArea,
    QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)
from qgis.core import (
    Qgis, QgsFeature, QgsFeatureRequest, QgsField,
    QgsStoredExpression,
)
from qgis.gui import (
    QgsAttributeEditorContext, QgsAttributeForm, QgsExpressionBuilderWidget,
    QgsFieldComboBox, QgsFieldConditionalFormatWidget, QgsFieldExpressionWidget,
)

from ..core import fields as F
from ..core.compat import NO_GEOMETRY
from . import explorer_edit as E
from . import explorer_ops as OPS
from . import icons
from . import widgets as W
from .theme import stylesheet

_MT = getattr(QMetaType, "Type", QMetaType)
VIRTUAL_TYPES = (("Whole number (integer)", _MT.Int, "integer"),
                 ("Large whole number", _MT.LongLong, "int8"),
                 ("Decimal number (double)", _MT.Double, "double"),
                 ("Text (string)", _MT.QString, "string"),
                 ("Date", _MT.QDate, "date"),
                 ("Date and time", _MT.QDateTime, "datetime"),
                 ("Boolean", _MT.Bool, "boolean"))
_SEARCH = QgsAttributeEditorContext.SearchMode
_MULTI = QgsAttributeEditorContext.MultiEditMode
_SINGLE = QgsAttributeEditorContext.SingleEditMode
_EMBED = QgsAttributeEditorContext.FormMode.Embed
_FILTER_TYPE = QgsAttributeForm.FilterType


def form_context(panel):
    """Attribute form context with the map canvas and vector layer tools."""
    ctx = QgsAttributeEditorContext()
    try:
        ctx.setMapCanvas(panel.iface.mapCanvas())
    except Exception:
        pass
    tools = getattr(panel.iface, "vectorLayerTools", None)
    if callable(tools):
        try:
            t = tools()
            if t is not None:
                ctx.setVectorLayerTools(t)
        except Exception:
            pass
    ctx.setFormMode(_EMBED)
    return ctx


class _Dialog(QDialog):
    """Suite-styled dialog base."""

    def __init__(self, panel, title, icon_name=None):
        super().__init__(panel)
        self.panel = panel
        self.metrics = panel.metrics
        self.theme = panel.theme
        self.setWindowTitle(title)
        if icon_name:
            self.setWindowIcon(icons.native(icon_name))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setStyleSheet(stylesheet(panel.theme, panel.metrics))
        self.lay = QVBoxLayout(self)
        m = panel.metrics.margin + 2
        self.lay.setContentsMargins(m, m, m, m)
        self.lay.setSpacing(8)

    def note(self, text, role="dim"):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setObjectName("gts" + role.capitalize())
        self.lay.addWidget(lbl)
        return lbl


def _expression_builder(layer, text="", calculator=False):
    """*calculator*: set up like the QGIS field calculator's builder
    (@row_number, ellipsoidal measures in the preview, its Recent list)."""
    builder = QgsExpressionBuilderWidget()
    builder.setLayer(layer)
    builder.setExpressionContext(OPS.calculator_context(layer) if calculator
                                 else layer.createExpressionContext())
    builder.loadFieldNames()
    if calculator:
        builder.setGeomCalculator(OPS.geometry_calculator(layer))
        builder.loadRecent(OPS.RECENT)
    if text:
        builder.setExpressionText(text)
    return builder


# ============================================================================
# FIELD CALCULATOR
# ============================================================================

class FieldCalculatorDialog(_Dialog):
    """Update an existing field, or create a new stored or virtual field,
    from an expression, for a chosen target - with a count before Apply."""

    def __init__(self, panel, field_name=None):
        super().__init__(panel, f"Field calculator — {panel.layer.name()}", icons.CALCULATE)
        self.resize(760, 640)
        layer = panel.layer
        m = self.metrics
        modes = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_update = QRadioButton("Update existing field")
        self.mode_new = QRadioButton("Create a new field")
        self.mode_virtual = QRadioButton("Create a virtual field")
        for i, rb in enumerate((self.mode_update, self.mode_new, self.mode_virtual)):
            self.mode_group.addButton(rb, i)
            modes.addWidget(rb)
        modes.addStretch()
        self.lay.addLayout(modes)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.field_combo = QgsFieldComboBox()
        self.field_combo.setLayer(layer)
        self.field_combo.setFixedHeight(m.control_h)
        editable = [layer.fields().at(i).name() for i in range(layer.fields().count())
                    if panel.edit.field_editable(i)[0]]
        self._editable = editable
        grid.addWidget(QLabel("Field"), 0, 0)
        grid.addWidget(self.field_combo, 0, 1, 1, 5)
        self.name_edit = QLineEdit()
        self.name_edit.setFixedHeight(m.control_h)
        self.name_edit.setPlaceholderText("new_field")
        self.type_combo = QComboBox()
        self.type_combo.setFixedHeight(m.control_h)
        self.length_spin = QSpinBox()
        self.length_spin.setRange(0, 100000)
        self.length_spin.setValue(10)
        self.prec_spin = QSpinBox()
        self.prec_spin.setRange(0, 30)
        self.prec_spin.setValue(3)
        for w in (self.length_spin, self.prec_spin):
            w.setFixedHeight(m.control_h)
        grid.addWidget(QLabel("Name"), 1, 0)
        grid.addWidget(self.name_edit, 1, 1)
        grid.addWidget(QLabel("Type"), 1, 2)
        grid.addWidget(self.type_combo, 1, 3)
        lp = QHBoxLayout()
        lp.addWidget(QLabel("Length"))
        lp.addWidget(self.length_spin)
        lp.addWidget(QLabel("Precision"))
        lp.addWidget(self.prec_spin)
        grid.addLayout(lp, 1, 4, 1, 2)
        self.target_combo = QComboBox()
        self.target_combo.setFixedHeight(m.control_h)
        counts = panel.target_counts()
        for key, text in (("layer", "All layer records"),
                          ("filtered", "All filtered records"),
                          ("selected_layer", "Selected layer records"),
                          ("selected_shown", "Selected shown records")):
            self.target_combo.addItem(f"{text} ({counts[key]:,})", key)
        self.target_combo.setCurrentIndex(2 if counts["selected_layer"] else 1)
        grid.addWidget(QLabel("Target"), 2, 0)
        grid.addWidget(self.target_combo, 2, 1, 1, 5)
        self.lay.addLayout(grid)

        self.builder = _expression_builder(layer, calculator=True)
        self.lay.addWidget(self.builder, 1)
        self.preview = QLabel("")
        self.preview.setObjectName("gtsDim")
        self.preview.setWordWrap(True)
        self.lay.addWidget(self.preview)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_btn = box.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        box.rejected.connect(self.reject)
        self.apply_btn.clicked.connect(self._apply)
        self.lay.addWidget(box)

        can_change = panel.edit.editing and bool(editable)
        can_add = panel.edit.editing and panel.edit.capable(E.CAP_ADD_FIELD)
        self.mode_update.setEnabled(can_change)
        self.mode_new.setEnabled(can_add)
        if not can_change:
            self.mode_update.setToolTip("Start editing (and have an editable field) first")
        if not can_add:
            self.mode_new.setToolTip("Start editing a layer whose data source can add fields")
        (self.mode_update if can_change else self.mode_new if can_add
         else self.mode_virtual).setChecked(True)
        if field_name and field_name in editable:
            self.field_combo.setField(field_name)
        elif editable:
            self.field_combo.setField(editable[0])
        self.mode_group.idClicked.connect(self._on_mode) if hasattr(
            self.mode_group, "idClicked") else self.mode_group.buttonClicked.connect(
            lambda *_: self._on_mode())
        self.builder.expressionParsed.connect(lambda *_: self._update_preview())
        self.target_combo.currentIndexChanged.connect(lambda *_: self._update_preview())
        self.field_combo.fieldChanged.connect(lambda *_: self._update_preview())
        self._on_mode()

    def mode(self):
        return ("update", "new", "virtual")[max(self.mode_group.checkedId(), 0)]

    def _on_mode(self, *_args):
        mode = self.mode()
        self.field_combo.setEnabled(mode == "update")
        for w in (self.name_edit, self.type_combo, self.length_spin, self.prec_spin):
            w.setEnabled(mode != "update")
        self.target_combo.setEnabled(mode != "virtual")
        self.type_combo.clear()
        if mode == "virtual":
            for text, mtype, tname in VIRTUAL_TYPES:
                self.type_combo.addItem(text, (mtype, tname, 0, 0))
        else:
            for nt in self.panel.layer.dataProvider().nativeTypes():
                self.type_combo.addItem(nt.mTypeDesc, (nt.mType, nt.mTypeName,
                                                       nt.mMaxLen, nt.mMaxPrec))
        self._update_preview()

    def target_ids(self):
        return self.panel.target_ids(self.target_combo.currentData())

    def _update_preview(self):
        text = self.builder.expressionText()
        mode = self.mode()
        if not text.strip():
            self.preview.setText("Enter an expression.")
            self.apply_btn.setEnabled(False)
            return
        if not self.builder.isExpressionValid():
            self.preview.setText("Expression is invalid.")
            self.apply_btn.setEnabled(False)
            return
        layer = self.panel.layer
        ids = self.target_ids() if mode != "virtual" else list(layer.allFeatureIds())[:1]
        sample = ""
        if ids:
            try:
                value = OPS.sample_value(layer, text, sorted(ids)[0])
                sample = ("Output preview: "
                          f"{F.display_value(value) if not F.is_null(value) else 'NULL'}")
            except OPS.OperationError as exc:
                sample = f"Evaluation error: {exc}"
        if mode == "virtual":
            self.preview.setText(f"{sample}  •  A virtual field is calculated for every "
                                 "record and stored in the project (save the project to keep it).")
        else:
            self.preview.setText(f"{sample}  •  {len(ids):,} feature(s) will be calculated "
                                 "as one undo step.")
        self.apply_btn.setEnabled(mode == "virtual" or bool(ids))

    def _new_field(self):
        name = self.name_edit.text().strip()
        if not name:
            raise OPS.OperationError("Enter a field name")
        if self.panel.layer.fields().indexFromName(name) >= 0:
            raise OPS.OperationError(f"{name} already exists")
        mtype, tname, max_len, max_prec = self.type_combo.currentData()
        length = self.length_spin.value() if max_len else 0
        prec = self.prec_spin.value() if max_prec else 0
        return QgsField(name, mtype, tname, length, prec)

    def _apply(self):
        layer = self.panel.layer
        text = self.builder.expressionText()
        mode = self.mode()
        try:
            if mode == "virtual":
                fld = self._new_field()
                layer.addExpressionField(text, fld)
                self._remember(text)
                self.panel.status.set_status(
                    f"Virtual field {fld.name()} added — it is stored in the project, "
                    "so save the project to keep it", "success")
                self.accept()
                return
            ids = self.target_ids()
            if mode == "update":
                idx = layer.fields().indexFromName(self.field_combo.currentField())
                if idx < 0:
                    raise OPS.OperationError("Choose a field")
                n = OPS.apply_expression(layer, idx, text, ids,
                                         f"Field calculator: {self.field_combo.currentField()}",
                                         self)
                name = self.field_combo.currentField()
            else:
                fld = self._new_field()
                layer.beginEditCommand(f"Field calculator: new field {fld.name()}")
                if not layer.addAttribute(fld):
                    layer.destroyEditCommand()
                    raise OPS.OperationError(f"{fld.name()} could not be added")
                idx = layer.fields().indexFromName(fld.name())
                try:
                    n = OPS.apply_expression(layer, idx, text, ids, "", self,
                                             own_command=False)
                except OPS.OperationError:
                    layer.destroyEditCommand()
                    raise
                if n is None:
                    layer.destroyEditCommand()
                else:
                    layer.endEditCommand()
                name = fld.name()
        except OPS.OperationError as exc:
            E.ask(self, "Field calculator", str(exc), [("ok", "OK", "accept")], icon="warning")
            return
        if n is None:
            self.panel.status.set_status("Calculation cancelled — nothing was changed",
                                         "warning")
        else:
            self._remember(text)
            self.panel.status.set_status(
                f"Calculated {name} for {len(ids):,} feature(s) ({n:,} value(s) changed, "
                "unsaved — Save edits writes them)", "success")
        self.accept()

    def _remember(self, text):
        """Into the Recent list shared with QGIS's own field calculator."""
        self.builder.expressionTree().saveToRecent(text, OPS.RECENT)


# ============================================================================
# NEW / DELETE FIELD
# ============================================================================

class NewFieldDialog(_Dialog):
    """A stored field (provider types, length, precision) or a virtual
    field calculated by an expression."""

    def __init__(self, panel):
        super().__init__(panel, f"New field — {panel.layer.name()}", icons.NEW_FIELD)
        self.resize(520, 360)
        layer = panel.layer
        m = self.metrics
        self.physical = QRadioButton("Stored field (written to the data source on save)")
        self.virtual = QRadioButton("Virtual field (calculated by an expression, kept in the project)")
        self.lay.addWidget(self.physical)
        self.lay.addWidget(self.virtual)
        can_add = panel.edit.editing and panel.edit.capable(E.CAP_ADD_FIELD)
        self.physical.setEnabled(can_add)
        if not can_add:
            self.physical.setToolTip("Start editing a layer whose data source can add fields")
        (self.physical if can_add else self.virtual).setChecked(True)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.comment_edit = QLineEdit()
        self.type_combo = QComboBox()
        self.length_spin = QSpinBox()
        self.length_spin.setRange(0, 100000)
        self.length_spin.setValue(50)
        self.prec_spin = QSpinBox()
        self.prec_spin.setRange(0, 30)
        self.expr = QgsFieldExpressionWidget()
        self.expr.setLayer(layer)
        for w in (self.name_edit, self.comment_edit, self.type_combo,
                  self.length_spin, self.prec_spin, self.expr):
            w.setFixedHeight(m.control_h)
        form.addRow("Name", self.name_edit)
        form.addRow("Comment", self.comment_edit)
        form.addRow("Type", self.type_combo)
        form.addRow("Length", self.length_spin)
        form.addRow("Precision", self.prec_spin)
        form.addRow("Expression", self.expr)
        self.lay.addLayout(form)
        self.info = self.note("")
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._apply)
        box.rejected.connect(self.reject)
        self.lay.addWidget(box)
        self.physical.toggled.connect(self._on_kind)
        self.type_combo.currentIndexChanged.connect(self._on_type)
        self._on_kind()

    def _on_kind(self, *_args):
        physical = self.physical.isChecked()
        self.type_combo.clear()
        if physical:
            for nt in self.panel.layer.dataProvider().nativeTypes():
                self.type_combo.addItem(nt.mTypeDesc, (nt.mType, nt.mTypeName, nt.mMinLen,
                                                       nt.mMaxLen, nt.mMinPrec, nt.mMaxPrec))
        else:
            for text, mtype, tname in VIRTUAL_TYPES:
                self.type_combo.addItem(text, (mtype, tname, 0, 0, 0, 0))
        self.expr.setEnabled(not physical)
        self.info.setText(
            "Added to the edit buffer; Save edits writes it to the data source."
            if physical else
            "A virtual field definition is a layer setting kept in the project, not in "
            "the data source — save the project to keep it.")
        self._on_type()

    def _on_type(self, *_args):
        data = self.type_combo.currentData()
        if not data:
            return
        _t, _n, min_len, max_len, min_prec, max_prec = data
        self.length_spin.setEnabled(bool(max_len) and self.physical.isChecked())
        self.prec_spin.setEnabled(bool(max_prec) and self.physical.isChecked())
        if max_len:
            self.length_spin.setRange(max(min_len, 0), max_len)
        if max_prec:
            self.prec_spin.setRange(max(min_prec, 0), max_prec)

    def _apply(self):
        layer = self.panel.layer
        name = self.name_edit.text().strip()
        problem = ""
        if not name:
            problem = "Enter a field name"
        elif layer.fields().indexFromName(name) >= 0:
            problem = f"{name} already exists"
        data = self.type_combo.currentData()
        if not problem and data is None:
            problem = "Choose a type"
        if not problem and not self.physical.isChecked() and not self.expr.isValidExpression():
            problem = "Enter a valid expression for the virtual field"
        if problem:
            E.ask(self, "New field", problem, [("ok", "OK", "accept")], icon="warning")
            return
        mtype, tname, _a, max_len, _b, max_prec = data
        fld = QgsField(name, mtype, tname,
                       self.length_spin.value() if max_len else 0,
                       self.prec_spin.value() if max_prec else 0,
                       self.comment_edit.text())
        if self.physical.isChecked():
            if not layer.addAttribute(fld):
                E.ask(self, "New field", f"{name} could not be added to the layer.",
                      [("ok", "OK", "accept")], icon="warning")
                return
            self.panel.status.set_status(f"Field {name} added (unsaved — Save edits writes it)",
                                         "success")
        else:
            layer.addExpressionField(self.expr.expression(), fld)
            self.panel.status.set_status(
                f"Virtual field {name} added — save the project to keep it", "success")
        self.accept()


class DeleteFieldDialog(_Dialog):
    """Delete stored fields (the whole layer, on save) or virtual fields;
    hiding a column instead is only a view setting (Columns)."""

    def __init__(self, panel, preselect=None):
        super().__init__(panel, f"Delete field — {panel.layer.name()}", icons.DELETE_FIELD)
        self.resize(480, 460)
        layer = panel.layer
        self.note("Deleting a stored field removes it from <b>every record of the layer</b> "
                  "when the edits are saved. To only hide a column in this view, use "
                  "<b>Columns</b> instead.")
        self.list = QListWidget()
        fields = layer.fields()
        can_delete = panel.edit.editing and panel.edit.capable(E.CAP_DELETE_FIELD)
        for i in range(fields.count()):
            fld = fields.at(i)
            origin = fields.fieldOrigin(i)
            item = QListWidgetItem(icons.field_for(fld, panel.theme), fld.name())
            item.setData(Qt.ItemDataRole.UserRole, i)
            enabled, why = True, ""
            if origin == Qgis.FieldOrigin.Expression:
                item.setText(f"{fld.name()}   (virtual)")
            elif origin == Qgis.FieldOrigin.Join:
                enabled, why = False, "Joined field: remove it in the layer's join settings"
            elif not can_delete:
                enabled, why = False, ("Start editing a layer whose data source can "
                                       "delete fields")
            flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            if not enabled:
                flags &= ~Qt.ItemFlag.ItemIsEnabled
                item.setToolTip(why)
            item.setFlags(flags)
            item.setCheckState(Qt.CheckState.Checked if fld.name() == preselect and enabled
                               else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.lay.addWidget(self.list, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.delete_btn = box.addButton("Delete", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.delete_btn.clicked.connect(self._apply)
        box.rejected.connect(self.reject)
        self.lay.addWidget(box)

    def chosen(self):
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _apply(self):
        layer = self.panel.layer
        idxs = self.chosen()
        if not idxs:
            return
        fields = layer.fields()
        names = [fields.at(i).name() for i in idxs]
        deps = []
        for name in names:
            deps += [d for d in OPS.field_dependents(layer, name)
                     if d.split(" (")[0] not in names]
        text = f"Delete {', '.join(names)} from <b>{layer.name()}</b>?"
        if deps:
            text += ("<br><br>These refer to it and may stop working:<br>"
                     + "<br>".join(deps))
        if E.ask(self, "Delete field", text,
                 [("delete", "Delete", "destructive"), ("cancel", "Cancel", "reject")],
                 default="cancel", icon="warning") != "delete":
            return
        virtual = [i for i in idxs if fields.fieldOrigin(i) == Qgis.FieldOrigin.Expression]
        stored = [i for i in idxs if i not in virtual]
        for i in sorted(virtual, reverse=True):
            layer.removeExpressionField(i)
        if stored:
            stored = [layer.fields().indexFromName(fields.at(i).name()) for i in stored]
            layer.beginEditCommand(f"Attribute Explorer: delete field(s) {', '.join(names)}")
            if layer.deleteAttributes(stored):
                layer.endEditCommand()
            else:
                layer.destroyEditCommand()
                E.ask(self, "Delete field", "The data source refused to delete the field(s).",
                      [("ok", "OK", "accept")], icon="warning")
                return
        self.panel.status.set_status(
            f"Deleted {', '.join(names)}" + (" (unsaved — Save edits writes it)"
                                              if stored else ""), "success")
        self.accept()


# ============================================================================
# EXPRESSIONS
# ============================================================================

class ExpressionDialog(_Dialog):
    """The QGIS expression builder with how to combine the result
    (filters) or the sort direction (sorting)."""

    def __init__(self, panel, title, text="", purpose="filter", has_current=False):
        super().__init__(panel, title, icons.EXPRESSION)
        self.resize(760, 560)
        self.builder = _expression_builder(panel.layer, text)
        self.lay.addWidget(self.builder, 1)
        row = QHBoxLayout()
        self.combine = QButtonGroup(self)
        self.descending = QCheckBox("Descending")
        if purpose == "filter":
            for i, label in enumerate(("Replace the expression filter",
                                       "AND with it", "OR with it")):
                rb = QRadioButton(label)
                rb.setEnabled(has_current or i == 0)
                self.combine.addButton(rb, i)
                row.addWidget(rb)
            self.combine.button(0).setChecked(True)
        else:
            row.addWidget(self.descending)
        row.addStretch()
        self.lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._ok)
        box.rejected.connect(self.reject)
        self.lay.addWidget(box)

    def _ok(self):
        if not self.builder.expressionText().strip() or not self.builder.isExpressionValid():
            E.ask(self, self.windowTitle(), "Enter a valid expression.",
                  [("ok", "OK", "accept")], icon="warning")
            return
        self.accept()

    def expression(self):
        return self.builder.expressionText().strip()

    def mode(self):
        return ("replace", "and", "or")[max(self.combine.checkedId(), 0)]


class StoredExpressionsDialog(_Dialog):
    """The layer's stored filter expressions: apply, save, rename, delete."""

    def __init__(self, panel):
        super().__init__(panel, f"Stored expressions — {panel.layer.name()}", icons.STORED)
        self.resize(620, 420)
        self.manager = panel.layer.storedExpressionManager()
        self.list = QTreeWidget()
        self.list.setRootIsDecorated(False)
        self.list.setHeaderLabels(["Name", "Expression"])
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.apply_btn = W.button(self.metrics, "Apply as filter", icons.native(icons.FILTER))
        self.save_btn = W.button(self.metrics, "Save current filter…", icons.native(icons.SAVE))
        self.rename_btn = W.button(self.metrics, "Rename…")
        self.delete_btn = W.button(self.metrics, "Delete", icons.native(icons.DELETE))
        for b in (self.apply_btn, self.save_btn, self.rename_btn, self.delete_btn):
            row.addWidget(b)
        row.addStretch()
        close = W.button(self.metrics, "Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.lay.addLayout(row)
        self.applied = None
        self.apply_btn.clicked.connect(self._apply)
        self.save_btn.clicked.connect(self._save)
        self.rename_btn.clicked.connect(self._rename)
        self.delete_btn.clicked.connect(self._delete)
        self.list.itemDoubleClicked.connect(lambda *_: self._apply())
        self.save_btn.setEnabled(bool(panel.expr_filter))
        self.save_btn.setToolTip(panel.expr_filter or "Set an expression filter first "
                                 "(View > Advanced expression filter)")
        self._fill()

    def _fill(self):
        self.list.clear()
        for se in self.manager.storedExpressions():
            item = QTreeWidgetItem([se.name, se.expression])
            item.setData(0, Qt.ItemDataRole.UserRole, se.id)
            self.list.addTopLevelItem(item)
        has = self.list.topLevelItemCount() > 0
        for b in (self.apply_btn, self.rename_btn, self.delete_btn):
            b.setEnabled(has)

    def _current(self):
        item = self.list.currentItem() or self.list.topLevelItem(0)
        return item

    def _apply(self):
        item = self._current()
        if item is not None:
            self.applied = item.text(1)
            self.accept()

    def _save(self):
        text = self.panel.expr_filter
        if not text:
            return
        name, ok = QInputDialog.getText(self, "Save expression", "Name", text=text[:40])
        if ok and name.strip():
            self.manager.addStoredExpression(name.strip(), text,
                                             QgsStoredExpression.Category.FilterExpression)
            self._fill()

    def _rename(self):
        item = self._current()
        if item is None:
            return
        name, ok = QInputDialog.getText(self, "Rename expression", "Name", text=item.text(0))
        if ok and name.strip():
            self.manager.updateStoredExpression(item.data(0, Qt.ItemDataRole.UserRole),
                                                name.strip(), item.text(1),
                                                QgsStoredExpression.Category.FilterExpression)
            self._fill()

    def _delete(self):
        item = self._current()
        if item is not None:
            self.manager.removeStoredExpression(item.data(0, Qt.ItemDataRole.UserRole))
            self._fill()


class FormSearchDialog(_Dialog):
    """The layer's configured form in search mode: filter the table or
    select features by value (with flash / zoom)."""

    def __init__(self, panel, select=False):
        title = "Select by value" if select else "Filter by form"
        super().__init__(panel, f"{title} — {panel.layer.name()}", icons.FORM_SELECT)
        self.resize(560, 620)
        self.note("Fill in values (operators next to each field), then use the "
                  "buttons below the form to filter the table or change the map "
                  "selection.")
        self.form = QgsAttributeForm(panel.layer, QgsFeature(), form_context(panel), self)
        self.form.setMode(_SEARCH)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form)
        self.lay.addWidget(scroll, 1)
        self.form.filterExpressionSet.connect(self._on_filter)
        self.form.closed.connect(self.accept)
        if hasattr(self.form, "flashFeatures"):
            self.form.flashFeatures.connect(lambda e: panel.flash_expression(e))
        if hasattr(self.form, "zoomToFeatures"):
            self.form.zoomToFeatures.connect(lambda e: panel.zoom_expression(e))

    def _on_filter(self, expression, filter_type):
        mode = "replace"
        if filter_type == _FILTER_TYPE.FilterAnd:
            mode = "and"
        elif filter_type == _FILTER_TYPE.FilterOr:
            mode = "or"
        self.panel.set_expression_filter(expression, mode)


class MultiEditDialog(_Dialog):
    """The configured form in multi edit mode for a captured set of
    features; only fields you change are applied."""

    def __init__(self, panel, fids):
        super().__init__(panel, f"Multi edit — {len(fids):,} features of "
                                f"{panel.layer.name()}", icons.MULTI_EDIT)
        self.resize(600, 640)
        self.fids = list(fids)
        self.note(f"Change the fields to set on all <b>{len(fids):,}</b> captured features. "
                  "Fields showing mixed values are left as they are unless you change them. "
                  "Apply writes one undo step to the edit buffer (Save edits stores it).")
        self.form = QgsAttributeForm(panel.layer, QgsFeature(), form_context(panel), self)
        self.form.setMode(_MULTI)
        self.form.setMultiEditFeatureIds(self.fids)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form)
        self.lay.addWidget(scroll, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_btn = box.addButton(f"Apply to {len(fids):,} features",
                                       QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_btn.clicked.connect(self._apply)
        box.rejected.connect(self.reject)
        self.lay.addWidget(box)

    def _apply(self):
        if self.form.save():
            self.panel.status.set_status(
                f"Multi edit applied to {len(self.fids):,} features (unsaved)", "success")
            self.accept()
        else:
            E.ask(self, "Multi edit", "The values were not accepted (check the "
                  "constraint messages in the form).", [("ok", "OK", "accept")],
                  icon="warning")


class ConditionalFormatDialog(_Dialog):
    """The layer's conditional formatting rules (QGIS's own panel); the
    table paints them over the type colours."""

    def __init__(self, panel):
        super().__init__(panel, f"Conditional formatting — {panel.layer.name()}",
                         icons.CONDITIONAL)
        self.resize(460, 600)
        self.widget = QgsFieldConditionalFormatWidget(self)
        self.widget.setLayer(panel.layer)
        self.lay.addWidget(self.widget, 1)
        self.note("Rules are a layer setting saved with the project. Selection and "
                  "unsaved markers stay visible on top of them.")
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.accept)
        box.accepted.connect(self.accept)
        self.lay.addWidget(box)


class ReviewChangesDialog(_Dialog):
    """Every unsaved change of the layer, as stored in its edit buffer."""

    LIMIT = 2000

    def __init__(self, panel):
        super().__init__(panel, f"Unsaved changes — {panel.layer.name()}", icons.HISTORY)
        self.resize(720, 520)
        layer = panel.layer
        buf = layer.editBuffer() if layer.isEditable() else None
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Change", "Feature", "Field", "Saved value", "Unsaved value"])
        self.tree.setRootIsDecorated(True)
        self.lay.addWidget(self.tree, 1)
        if buf is None:
            self.note("The layer is not being edited, so there are no unsaved changes.")
        else:
            fields = layer.fields()
            changed = buf.changedAttributeValues()
            saved = {}
            ids = sorted(changed.keys())[:self.LIMIT]
            if ids and layer.dataProvider() is not None:
                req = QgsFeatureRequest().setFilterFids([f for f in ids if f >= 0])
                req.setFlags(NO_GEOMETRY)
                for feat in layer.dataProvider().getFeatures(req):
                    saved[feat.id()] = feat.attributes()
            top = QTreeWidgetItem([f"Changed values ({sum(len(v) for v in changed.values()):,})"])
            for fid in ids:
                for idx, value in changed[fid].items():
                    old = saved.get(fid)
                    old = old[idx] if old is not None and idx < len(old) else None
                    name = fields.at(idx).name() if 0 <= idx < fields.count() else str(idx)
                    top.addChild(QTreeWidgetItem([
                        "Value", str(fid), name,
                        "NULL" if F.is_null(old) else F.display_value(old),
                        "NULL" if F.is_null(value) else F.display_value(value)]))
            self.tree.addTopLevelItem(top)
            added = buf.addedFeatures()
            top = QTreeWidgetItem([f"Added records ({len(added):,})"])
            for fid in sorted(added.keys())[:self.LIMIT]:
                top.addChild(QTreeWidgetItem(["Added", str(fid), "", "", "new record"]))
            self.tree.addTopLevelItem(top)
            deleted = buf.deletedFeatureIds()
            top = QTreeWidgetItem([f"Deleted records ({len(deleted):,})"])
            for fid in sorted(deleted)[:self.LIMIT]:
                top.addChild(QTreeWidgetItem(["Deleted", str(fid), "", "", ""]))
            self.tree.addTopLevelItem(top)
            geoms = buf.changedGeometries()
            top = QTreeWidgetItem([f"Geometry changes ({len(geoms):,})"])
            for fid in sorted(geoms.keys())[:self.LIMIT]:
                top.addChild(QTreeWidgetItem(["Geometry", str(fid), "", "", "changed"]))
            self.tree.addTopLevelItem(top)
            schema = [f"added {f.name()}" for f in buf.addedAttributes()]
            schema += [f"deleted field #{i}" for i in buf.deletedAttributeIds()]
            renamed = getattr(buf, "renamedAttributes", None)
            if callable(renamed):
                schema += [f"renamed field #{i} to {n}" for i, n in renamed().items()]
            top = QTreeWidgetItem([f"Field changes ({len(schema):,})"])
            for text in schema:
                top.addChild(QTreeWidgetItem(["Field", "", text, "", ""]))
            self.tree.addTopLevelItem(top)
            self.tree.expandToDepth(0)
            self.note("Save edits writes all of these (including records hidden by "
                      "filters); Discard reverts all of them.")
        for c in range(5):
            self.tree.resizeColumnToContents(c)
        row = QHBoxLayout()
        self.show_btn = W.button(self.metrics, "Show edited and new",
                                 icons.native(icons.FILTER))
        self.show_btn.clicked.connect(lambda: (panel.set_view_scope("edited"), self.accept()))
        self.show_btn.setEnabled(buf is not None)
        row.addWidget(self.show_btn)
        row.addStretch()
        close = W.button(self.metrics, "Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.lay.addLayout(row)
