# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Field Properties (Attribute Explorer): every field of the layer in one grid,
as QGIS's Layer Properties › Fields and ArcGIS Pro's Fields view show them.

Changeable: Visible (the Explorer column, as the Columns window), Read only
(the layer's form setting), Field name (renamed as Rename Field does: in the
edit session, written by Save edits), Alias, Allow NULL and Unique (QGIS
constraints) and Default value (an expression, optionally applied on update
too).  Type, length, precision, origin and comment are shown only.  OK
applies every change together; Cancel changes nothing.  Rows with a change
show their field name in bold, as ArcGIS does.
"""

import time

from qgis.PyQt.QtCore import QSize, Qt, QTimer
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QFontMetrics, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QTableWidget,
    QTableWidgetItem,
)
from qgis.core import Qgis, QgsDefaultValue, QgsExpression, QgsFieldConstraints

from ..core import fields as F
from . import explorer_edit as E
from . import icons
from . import widgets as W
from .explorer_dialogs import _Dialog, _name_limit

_ORIGIN = Qgis.FieldOrigin
_NOT_NULL = QgsFieldConstraints.Constraint.ConstraintNotNull
_UNIQUE = QgsFieldConstraints.Constraint.ConstraintUnique
# scoped in QGIS 4 (Qt6), only unscoped in QGIS 3 (Qt5)
_FROM_SOURCE = getattr(QgsFieldConstraints.ConstraintOrigin, "ConstraintOriginProvider",
                       getattr(QgsFieldConstraints, "ConstraintOriginProvider", None))
_HARD = QgsFieldConstraints.ConstraintStrength.ConstraintStrengthHard

# (key, heading, tooltip, check box?)
COLUMNS = (
    ("visible", "Visible", "Show the column in the Explorer (as the Columns window "
     "does)", True),
    ("readonly", "Read only", "Not editable in tables and forms (the layer's form "
     "setting, saved in the project)", True),
    ("name", "Field name", "Double-click to rename. The new name is written by Save "
     "edits and Ctrl+Z undoes it, as Rename Field does", False),
    ("alias", "Alias", "The name QGIS shows in forms (saved in the project)", False),
    ("type", "Type", "The field's data type (it cannot be changed in place)", False),
    ("length", "Length", "Longest text or number of digits (0 or blank: no limit)", False),
    ("precision", "Precision", "Decimal places", False),
    ("allownull", "Allow NULL", "Unticked: QGIS's Not null rule (saved in the project). "
     "Greyed when the data source sets it", True),
    ("unique", "Unique", "QGIS's Unique rule (saved in the project). Greyed when the "
     "data source sets it", True),
    ("default", "Default value", "An expression for new records, e.g. now() or "
     "'Proposed' (saved in the project)", False),
    ("onupdate", "On update", "Also apply the default value when a record is "
     "updated", True),
    ("origin", "Origin", "Where the field comes from", False),
    ("comment", "Comment", "The field's comment in the data source", False),
)
KEYS = [c[0] for c in COLUMNS]
COL = {key: i for i, key in enumerate(KEYS)}
CHECKS = {c[0] for c in COLUMNS if c[3]}
TEXTS = ("name", "alias", "default")

ORIGINS = {
    _ORIGIN.Provider: "Data source",
    _ORIGIN.Join: "Joined",
    _ORIGIN.Expression: "Virtual",
    _ORIGIN.Edit: "New (unsaved)",
}


class FieldPropertiesDialog(_Dialog):
    """The layer's fields, one per row; OK applies the changes."""

    def __init__(self, panel, focus=None):
        self.layer = panel.layer
        super().__init__(panel, f"Field Properties — {self.layer.name()}",
                         icons.FIELD_PROPS)
        m, t = self.metrics, self.theme
        self._danger = QColor(t.danger)
        self.rows = []                   # per field: idx, original values, origin
        self.resize(1120, 540)

        top = QHBoxLayout()
        top.setSpacing(m.gap)
        self.count_label = W.label("", "dim")
        top.addWidget(self.count_label)
        top.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search / Filter fields")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(m.control_h)
        self.search.setFixedWidth(220)
        top.addWidget(self.search)
        self.lay.addLayout(top)

        # the Explorer table's look: its header, rows, grid lines and the blue
        # frame on the current cell (drawn by the delegates, so no theme
        # selection colour shows)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("gtsTable")
        # nothing sorts here, so the headings keep no room for a sort arrow
        self.setStyleSheet(self.styleSheet() + "QTableWidget#gtsTable "
                           "QHeaderView::section:horizontal { padding: 0px 8px; }")
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(m.icon, m.icon))
        self.table.verticalHeader().setDefaultSectionSize(m.font_h + 9)
        self.table.horizontalHeader().setMinimumHeight(m.font_h + 12)
        self.table.setShowGrid(True)
        self.table.setItemDelegate(W.GridTextDelegate(t, self.table))
        checks = W.TickDelegate(t, self.table, grid=True)
        for i, (key, title, tip, is_check) in enumerate(COLUMNS):
            head = QTableWidgetItem(title)
            head.setToolTip(tip)
            self.table.setHorizontalHeaderItem(i, head)
            if is_check:
                self.table.setItemDelegateForColumn(i, checks)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setHighlightSections(False)
        hh.setStretchLastSection(True)
        self.lay.addWidget(self.table, 1)

        self.problem = QLabel("")
        self.problem.setStyleSheet(f"color: {t.danger};")
        self.problem.setWordWrap(True)
        self.problem.setVisible(False)
        self.lay.addWidget(self.problem)

        buttons = QHBoxLayout()
        self.props_btn = W.button(m, "Layer Properties…", None,
                                  "Open QGIS's Layer Properties › Fields for anything "
                                  "else (editor widgets, comments, …)")
        buttons.addWidget(self.props_btn)
        buttons.addStretch(1)
        self.cancel_btn = W.button(m, "Cancel")
        self.ok_btn = W.button(m, "OK", None, "Apply the changes")
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)
        self.lay.addLayout(buttons)
        for btn in (self.cancel_btn, self.ok_btn):
            btn.ensurePolished()
        width = max(self.cancel_btn.sizeHint().width(), self.ok_btn.sizeHint().width())
        for btn in (self.cancel_btn, self.ok_btn):
            btn.setMinimumWidth(width)

        self._fill()
        self._fit_width()
        self.table.itemChanged.connect(self._on_item_changed)
        self.search.textChanged.connect(lambda *_: self._filter())
        self.props_btn.clicked.connect(self.open_layer_properties)
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.apply)
        self._check()
        if focus is not None:
            self.focus_field(focus)

    # ------------------------------------------------------------------
    # reading the layer
    # ------------------------------------------------------------------
    def _values(self, idx):
        layer, panel = self.layer, self.panel
        fields = layer.fields()
        fld = fields.at(idx)
        cons = fld.constraints()
        dv = layer.defaultValueDefinition(idx)
        origin = fields.fieldOrigin(idx)
        readonly = layer.editFormConfig().readOnly(idx) or origin in (
            _ORIGIN.Expression, _ORIGIN.Join)
        return {
            "visible": not panel.header.isSectionHidden(idx)
            if idx < panel.header.count() else True,
            "readonly": readonly,
            "name": fld.name(),
            "alias": fld.alias() or "",
            "allownull": not bool(cons.constraints() & _NOT_NULL),
            "unique": bool(cons.constraints() & _UNIQUE),
            "default": dv.expression() or "",
            "onupdate": bool(dv.applyOnUpdate()),
        }

    def _fill(self):
        layer = self.layer
        fields = layer.fields()
        limit, fmt = _name_limit(layer)
        self.limit, self.format = limit, fmt
        self.table.blockSignals(True)
        self.table.setRowCount(fields.count())
        for idx in range(fields.count()):
            fld = fields.at(idx)
            origin = fields.fieldOrigin(idx)
            values = self._values(idx)
            cons = fld.constraints()
            self.rows.append({"idx": idx, "orig": dict(values), "origin": origin})
            own = origin in (_ORIGIN.Provider, _ORIGIN.Edit)
            renamable, why_name = self.panel.edit.field_renamable(idx)

            def check(key, on, enabled=True, why=""):
                item = QTableWidgetItem()
                flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
                if enabled:
                    flags |= Qt.ItemFlag.ItemIsEnabled
                item.setFlags(flags)
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                if why:
                    item.setToolTip(why)
                self.table.setItem(idx, COL[key], item)

            def text(key, value, editable=False, why=""):
                item = QTableWidgetItem(str(value))
                flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                if editable:
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                if why:
                    item.setToolTip(why)
                elif not editable:
                    item.setForeground(QBrush(QColor(self.theme.text_dim)))
                self.table.setItem(idx, COL[key], item)
                return item

            check("visible", values["visible"])
            if origin == _ORIGIN.Expression:
                check("readonly", True, False, "Virtual field: calculated by its expression")
            elif origin == _ORIGIN.Join:
                check("readonly", True, False,
                      "Joined field: set in the join's settings (Layer Properties › Joins)")
            else:
                check("readonly", values["readonly"])
            name = text("name", values["name"], renamable,
                        "" if renamable else f"Cannot be renamed: {why_name}")
            name.setIcon(icons.field(F.field_kind(fld), self.theme))
            if renamable:
                name.setForeground(QBrush(QColor(self.theme.text)))
            text("alias", values["alias"], True)
            try:
                type_text = fld.friendlyTypeString()
            except AttributeError:
                type_text = fld.typeName()
            text("type", type_text)
            text("length", fld.length() if fld.length() > 0 else "")
            numeric = F.field_kind(fld) == F.REAL
            text("precision", fld.precision() if numeric and fld.precision() > 0 else "")
            for key, flag in (("allownull", _NOT_NULL), ("unique", _UNIQUE)):
                on = values[key]
                if not own:
                    check(key, on, False, "Only for this layer's own fields")
                elif cons.constraints() & flag and cons.constraintOrigin(flag) == _FROM_SOURCE:
                    check(key, on, False, "Set by the data source")
                else:
                    check(key, on)
            text("default", values["default"], own,
                 "" if own else "Only for this layer's own fields")
            check("onupdate", values["onupdate"], own,
                  "" if own else "Only for this layer's own fields")
            text("origin", ORIGINS.get(origin, ""))
            text("comment", fld.comment() or "")
        self.table.blockSignals(False)
        # every heading and value fits (headings are bold, 8 px each side);
        # long free text is capped
        self.table.resizeColumnsToContents()
        hh = self.table.horizontalHeader()
        bold = QFont(self.table.font())
        bold.setBold(True)
        fm = QFontMetrics(bold)
        for i, (key, title, _tip, _check) in enumerate(COLUMNS):
            width = max(hh.sectionSize(i), fm.horizontalAdvance(title) + 2 * 8 + 4)
            if key in ("alias", "default", "comment"):
                width = min(max(width, 110), 240)
            hh.resizeSection(i, width)
        # names turn bold when their row changes: make room for that
        widest = max((fm.horizontalAdvance(fields.at(i).name())
                      for i in range(fields.count())), default=0)
        hh.resizeSection(COL["name"], min(max(widest + self.metrics.icon + 28,
                                              hh.sectionSize(COL["name"])), 260))
        self._filter()

    def _fit_width(self):
        """Open wide enough for every column (within the screen)."""
        hh = self.table.horizontalHeader()
        grid = sum(hh.sectionSize(i) for i in range(hh.count()))
        margins = self.lay.contentsMargins()
        width = (grid + 2 * self.table.frameWidth()
                 + self.table.verticalScrollBar().sizeHint().width()
                 + margins.left() + margins.right() + 4)
        screen = self.screen() if hasattr(self, "screen") else None
        avail = (screen or QGuiApplication.primaryScreen()).availableGeometry()
        self.resize(max(900, min(width, int(avail.width() * 0.92))), self.height())

    # ------------------------------------------------------------------
    # state in the grid
    # ------------------------------------------------------------------
    def current(self, row):
        """The values the grid holds for *row* (a field index)."""
        out = {}
        for key in KEYS:
            item = self.table.item(row, COL[key])
            if key in CHECKS:
                out[key] = item.checkState() == Qt.CheckState.Checked
            elif key in TEXTS:
                out[key] = item.text().strip() if key == "name" else item.text()
        return out

    def changes(self, row):
        rec = self.rows[row]
        cur = self.current(row)
        return {k: v for k, v in cur.items() if v != rec["orig"][k]}

    def changed_rows(self):
        return [r for r in range(len(self.rows)) if self.changes(r)]

    def _on_item_changed(self, item):
        row = item.row()
        name = self.table.item(row, COL["name"])
        font = QFont(name.font())
        font.setBold(bool(self.changes(row)))
        self.table.blockSignals(True)
        name.setFont(font)
        self.table.blockSignals(False)
        self._check()

    def _problems(self):
        """[(row, column key, message)] of the values that cannot be applied."""
        out = []
        names = {}
        for r in range(len(self.rows)):
            names.setdefault(self.current(r)["name"].lower(), []).append(r)
        for r, rec in enumerate(self.rows):
            cur = self.current(r)
            new = cur["name"]
            if new != rec["orig"]["name"]:
                if not new:
                    out.append((r, "name", "Enter a name"))
                elif len(names.get(new.lower(), [])) > 1:
                    out.append((r, "name", f"Another field is already named {new}"))
                elif self.limit and len(new) > self.limit:
                    out.append((r, "name", f"{self.format} keeps at most {self.limit} "
                                           f"characters in a field name"))
            expr = cur["default"].strip()
            if expr and expr != rec["orig"]["default"].strip():
                parsed = QgsExpression(expr)
                if parsed.hasParserError():
                    out.append((r, "default", "Default value: "
                                + parsed.parserErrorString()))
        return out

    def _check(self):
        problems = self._problems()
        bad = {(r, key) for r, key, _m in problems}
        self.table.blockSignals(True)
        for r in range(len(self.rows)):
            for key in ("name", "default"):
                item = self.table.item(r, COL[key])
                if not item.flags() & Qt.ItemFlag.ItemIsEditable:
                    continue
                item.setForeground(QBrush(self._danger if (r, key) in bad
                                          else QColor(self.theme.text)))
        self.table.blockSignals(False)
        if problems:
            r, _key, message = problems[0]
            self.problem.setText(f"{self.rows[r]['orig']['name']}: {message}")
        self.problem.setVisible(bool(problems))
        n = len(self.changed_rows())
        self.ok_btn.setEnabled(not problems)
        total = len(self.rows)
        self.count_label.setText(f"{total:,} fields" + (f"  •  {n:,} changed" if n else ""))

    def _filter(self):
        text = self.search.text().strip().lower()
        stripe = {}
        for r in range(len(self.rows)):
            cur = self.current(r)
            match = (not text or text in cur["name"].lower()
                     or text in cur["alias"].lower())
            self.table.setRowHidden(r, not match)
            if match:
                stripe[r] = len(stripe)     # stripes follow the rows shown
        self.table.gts_stripe = stripe
        self.table.viewport().update()

    def focus_field(self, name):
        """Pick the row of the field *name* (right-click › Field Properties)."""
        for r in range(len(self.rows)):
            if self.rows[r]["orig"]["name"] == name:
                self.table.setCurrentCell(r, COL["name"])
                self.table.scrollToItem(self.table.item(r, COL["name"]),
                                        QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False

    # ------------------------------------------------------------------
    # applying
    # ------------------------------------------------------------------
    def apply(self):
        """OK: apply every change (renames last, in the edit session)."""
        if self._problems():
            return False
        rows = self.changed_rows()
        if not rows:
            self.accept()
            return True
        layer, panel = self.layer, self.panel
        header = panel.header
        cfg = layer.editFormConfig()
        cfg_changed = shown_changed = False
        renames = []
        settings = 0
        # the Explorer columns first: a field change rebuilds the header from
        # the saved layout
        for r in rows:
            change = self.changes(r)
            if "visible" in change:
                header.setSectionHidden(self.rows[r]["idx"], not change["visible"])
                shown_changed = True
        if shown_changed:
            header.relayout()
            panel._save_layout()
            panel._layout_timer.start()
            panel._profile_timer.start()
        for r in rows:
            idx = self.rows[r]["idx"]
            change = self.changes(r)
            cur = self.current(r)
            if "readonly" in change:
                cfg.setReadOnly(idx, change["readonly"])
                cfg_changed = True
            if "alias" in change:
                if change["alias"].strip():
                    layer.setFieldAlias(idx, change["alias"].strip())
                else:
                    layer.removeFieldAlias(idx)
            if "allownull" in change:
                if change["allownull"]:
                    layer.removeFieldConstraint(idx, _NOT_NULL)
                else:
                    layer.setFieldConstraint(idx, _NOT_NULL, _HARD)
            if "unique" in change:
                if change["unique"]:
                    layer.setFieldConstraint(idx, _UNIQUE, _HARD)
                else:
                    layer.removeFieldConstraint(idx, _UNIQUE)
            if "default" in change or "onupdate" in change:
                expr = cur["default"].strip()
                layer.setDefaultValueDefinition(
                    idx, QgsDefaultValue(expr, cur["onupdate"]) if expr
                    else QgsDefaultValue())
            if "name" in change:
                renames.append((idx, self.rows[r]["orig"]["name"], change["name"]))
            settings += len(set(change) - {"name"})
        if cfg_changed:
            layer.setEditFormConfig(cfg)
        renamed = 0
        if renames:
            stored = [x for x in renames if not panel.edit.is_virtual(x[0])]
            if stored and not panel.edit.editing and not panel.edit.start():
                self.problem.setText("The layer cannot be edited, so the fields "
                                     "could not be renamed")
                self.problem.setVisible(True)
                return False
            for idx, old, new in renames:
                if layer.renameAttribute(idx, new):
                    renamed += 1
        parts = []
        if settings:
            parts.append(f"{settings:,} setting(s) changed")
        if renamed:
            parts.append(f"{renamed:,} field(s) renamed (unsaved: Save edits writes "
                         f"the names, Ctrl+Z undoes them)")
        if len(renames) > renamed:
            parts.append(f"{len(renames) - renamed:,} rename(s) failed")
        text = "Field Properties: " + "; ".join(parts) if parts else "Field Properties"
        panel._status_note = (time.monotonic() + 8, text)   # outlives the refresh
        panel.status.set_status(text, "warning" if len(renames) > renamed else "success")
        self.accept()
        return True

    def open_layer_properties(self):
        """QGIS's Layer Properties › Fields, after this window closes (the
        changes made here are applied first, or left out, as chosen)."""
        if self.changed_rows():
            choice = E.ask(self, "Field Properties",
                           "Apply the changes made here before opening Layer "
                           "Properties?",
                           [("apply", "Apply", "accept"), ("discard", "Discard", "destructive"),
                            ("cancel", "Cancel", "reject")], default="apply")
            if choice == "cancel":
                return
            if choice == "apply" and not self.apply():
                return
            if choice == "discard":
                self.reject()
        else:
            self.reject()
        panel = self.panel
        QTimer.singleShot(0, lambda: panel.open_layer_properties(fields=True))
