# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Join Table (Attribute Explorer): add a QGIS layer join, as Layer
Properties › Joins does, from one window.

- The join layer is dropped on the window (a layer from the Layers panel,
  a Browser panel item or a file from the file manager) or picked in the
  box.  A file becomes a layer of the project when the join is added.
- The two match fields are guessed (a field name both layers share); the
  window counts in the background how many records will match, with the
  same rules as QGIS (core/joins.py).
- The fields to add, their name prefix and the names already in the layer
  (QGIS leaves those out) are shown before the join is made.
- The joins already on the layer are listed, each with Remove.
"""

import os
import time

from qgis.PyQt.QtCore import QMimeData, QSize, Qt, QTimer, QUrl, QUrlQuery
from qgis.PyQt.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QVBoxLayout, QWidget,
)
from qgis.core import (
    Qgis, QgsFields, QgsMimeDataUtils, QgsProject, QgsProviderRegistry,
    QgsVectorLayer, QgsVectorLayerFeatureSource, QgsVectorLayerJoinInfo,
)

from ..core import fields as F
from ..core import joins as J
from ..core.compat import VECTOR_LAYER_FILTER
from ..core.jobs import JobRunner
from . import icons
from . import widgets as W
from .explorer_dialogs import _Dialog

_VECTOR = Qgis.LayerType.Vector


# ----------------------------------------------------------------------------
# Layers from a drop
# ----------------------------------------------------------------------------

def _same_source(layer, provider, uri):
    return layer.providerType() == provider and layer.source() == uri


def _project_layer(provider, uri):
    for layer in QgsProject.instance().mapLayers().values():
        if layer.type() == _VECTOR and _same_source(layer, provider, uri):
            return layer
    return None


def file_choices(path):
    """[(name, uri, provider)] of the tables in a file (a CSV is read with
    its column types detected)."""
    base = os.path.splitext(os.path.basename(path))[0]
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        url = QUrl.fromLocalFile(path)
        query = QUrlQuery()
        query.addQueryItem("type", "csv")
        if ext == ".tsv":
            query.addQueryItem("delimiter", "\\t")
        query.addQueryItem("detectTypes", "yes")
        query.addQueryItem("geomType", "none")
        url.setQuery(query)
        return [(base, url.toString(), "delimitedtext")]
    out, seen = [], set()
    for d in QgsProviderRegistry.instance().querySublayers(path):
        if d.type() != _VECTOR or (d.providerKey(), d.uri()) in seen:
            continue
        seen.add((d.providerKey(), d.uri()))
        out.append((d.name() or base, d.uri(), d.providerKey()))
    return out


def mime_choices(mime):
    """What a drop offers: [(name, uri, provider, project layer or None)]."""
    out = []
    if QgsMimeDataUtils.isUriList(mime):
        for uri in QgsMimeDataUtils.decodeUriList(mime):
            if uri.layerType != "vector":
                continue
            layer = uri.mapLayer() if uri.layerId else None
            if layer is None:
                layer = _project_layer(uri.providerKey, uri.uri)
            out.append((uri.name, uri.uri, uri.providerKey, layer))
    elif mime.hasUrls():
        for url in mime.urls():
            if url.isLocalFile() and os.path.isfile(url.toLocalFile()):
                for name, uri, provider in file_choices(url.toLocalFile()):
                    out.append((name, uri, provider, _project_layer(provider, uri)))
    return out


# ----------------------------------------------------------------------------
# The window
# ----------------------------------------------------------------------------

class JoinTableDialog(_Dialog):
    """Add a layer join to the panel's layer; list and remove its joins."""

    def __init__(self, panel):
        self.layer = panel.layer
        super().__init__(panel, f"Join Table — {self.layer.name()}", icons.JOIN)
        m, t = self.metrics, self.theme
        self.runner = JobRunner(self)
        self.counts = None               # the last match count (core/joins)
        self._pending = []               # layers from files, not in the project
        self._prefix_edited = False
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(200)
        self._count_timer.timeout.connect(self._count_matches)
        self.setAcceptDrops(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(self.styleSheet() + f"""
            QFrame#gtsDrop {{ background: {t.base};
                border: 2px dashed {t.text_faint}; border-radius: {m.radius}px; }}
            QFrame#gtsDrop[hover="true"] {{ background: {t.accent_soft};
                border: 2px dashed {t.accent}; }}
            QFrame#gtsDrop QLabel {{ background: transparent; border: none; }}
            QLabel#gtsJoinProblem {{ color: {t.danger}; }}
            QLabel#gtsJoinClash {{ color: {t.warning}; }}
        """)

        # ---- join layer: drop zone + box ----
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("gtsDrop")
        dz = QHBoxLayout(self.drop_zone)
        dz.setContentsMargins(12, 10, 12, 10)
        dz.setSpacing(12)
        mark = QLabel()
        mark.setPixmap(icons.native(icons.JOIN).pixmap(QSize(32, 32)))
        dz.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        texts.addWidget(W.label("Drop a layer or a file here", "title"))
        hint = W.label("From the Layers or Browser panel, or a CSV, Excel, "
                       "GeoPackage or shapefile from your files", "dim")
        hint.setWordWrap(True)
        texts.addWidget(hint)
        pick = QHBoxLayout()
        pick.setSpacing(m.gap)
        pick.addWidget(W.label("or choose", "dim"))
        self.layer_combo = W.LayerCombo(m)
        self.layer_combo.setFilters(VECTOR_LAYER_FILTER)
        self.layer_combo.setExceptedLayerList([self.layer])
        self.layer_combo.setAllowEmptyLayer(True, "Choose a layer or table")
        self.layer_combo.setLayer(None)
        pick.addWidget(self.layer_combo, 1)
        texts.addLayout(pick)
        self.drop_problem = QLabel("")
        self.drop_problem.setObjectName("gtsJoinProblem")
        self.drop_problem.setWordWrap(True)
        self.drop_problem.setVisible(False)
        texts.addWidget(self.drop_problem)
        dz.addLayout(texts, 1)
        self.lay.addWidget(self.drop_zone)

        # ---- match ----
        self.lay.addWidget(W.section_header("Match"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(m.gap)
        grid.setVerticalSpacing(4)
        self.target_caption = W.ElideLabel(self.layer.name())
        self.target_caption.setObjectName("gtsDim")
        self.join_caption = W.ElideLabel("Join layer")
        self.join_caption.setObjectName("gtsDim")
        self.target_combo = W.FieldCombo(m, t)
        self.target_combo.set_fields(self.layer.fields())
        self.join_combo = W.FieldCombo(m, t)
        equals = QLabel("=")
        equals.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.target_caption, 0, 0)
        grid.addWidget(self.join_caption, 0, 2)
        grid.addWidget(self.target_combo, 1, 0)
        grid.addWidget(equals, 1, 1)
        grid.addWidget(self.join_combo, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        self.lay.addLayout(grid)
        self.match_box = QFrame()
        self.match_box.setObjectName("gtsStatus")
        mb = QVBoxLayout(self.match_box)
        mb.setContentsMargins(10, 5, 8, 5)
        mb.setSpacing(2)
        self.match_head = QLabel("")
        self.match_head.setObjectName("gtsTitle")
        self.match_detail = QLabel("")
        self.match_detail.setObjectName("gtsDim")
        self.match_detail.setWordWrap(True)
        mb.addWidget(self.match_head)
        mb.addWidget(self.match_detail)
        self.lay.addWidget(self.match_box)

        # ---- fields to add ----
        head = QHBoxLayout()
        head.setSpacing(m.gap)
        head.addWidget(W.section_header("Fields to add"), 1)
        self.all_link = W.flat_link(m, "All", "Tick every field")
        self.none_link = W.flat_link(m, "None", "Untick every field")
        head.addWidget(self.all_link)
        head.addWidget(self.none_link)
        self.lay.addLayout(head)
        self.field_list = QListWidget()
        self.field_list.setIconSize(QSize(m.icon, m.icon))
        self.field_list.setMinimumHeight(120)
        self.lay.addWidget(self.field_list, 1)
        prefix_row = QHBoxLayout()
        prefix_row.setSpacing(m.gap)
        prefix_row.addWidget(W.label("Name prefix", "dim"))
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setFixedHeight(m.control_h)
        self.prefix_edit.setMaximumWidth(220)
        self.prefix_edit.setToolTip("Put before the name of every joined field "
                                    "(empty: the names as they are)")
        prefix_row.addWidget(self.prefix_edit)
        self.names_preview = W.ElideLabel("")
        self.names_preview.setObjectName("gtsDim")
        prefix_row.addWidget(self.names_preview, 1)
        self.lay.addLayout(prefix_row)
        self.clash_label = QLabel("")
        self.clash_label.setObjectName("gtsJoinClash")
        self.clash_label.setWordWrap(True)
        self.clash_label.setVisible(False)
        self.lay.addWidget(self.clash_label)

        # ---- joins already on the layer ----
        self.lay.addWidget(W.section_header("Joins on this layer"))
        self.joins_box = QWidget()
        self.joins_layout = QVBoxLayout(self.joins_box)
        self.joins_layout.setContentsMargins(0, 0, 0, 0)
        self.joins_layout.setSpacing(4)
        self.lay.addWidget(self.joins_box)

        # ---- buttons ----
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = W.button(m, "Cancel")
        self.join_btn = W.primary_button(m, "Join", t, "Add the join to the layer")
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.join_btn)
        self.lay.addLayout(buttons)

        self.layer_combo.layerChanged.connect(self._on_join_layer)
        self.target_combo.currentIndexChanged.connect(self._on_match_fields)
        self.join_combo.currentIndexChanged.connect(self._on_join_field)
        self.field_list.itemChanged.connect(lambda *_: self._update_names())
        self.all_link.clicked.connect(lambda: self._tick_all(True))
        self.none_link.clicked.connect(lambda: self._tick_all(False))
        self.prefix_edit.textEdited.connect(self._on_prefix_edited)
        self.cancel_btn.clicked.connect(self.reject)
        self.join_btn.clicked.connect(self.add_join)

        self._fill_joins()
        self._on_join_layer(None)

    # ------------------------------------------------------------------
    # join layer
    # ------------------------------------------------------------------
    def join_layer(self):
        return self.layer_combo.currentLayer()

    def _on_join_layer(self, _layer=None):
        layer = self.join_layer()
        self.join_caption.setText(layer.name() if layer else "Join layer")
        self.join_combo.set_fields(layer.fields() if layer else QgsFields(),
                                   keep=False)
        if layer is not None:
            target, join = J.default_match(self.layer.fields(), layer.fields())
            if target:
                self.target_combo.set_current_name(target)
            if join:
                self.join_combo.set_current_name(join)
            if not self._prefix_edited:
                self.prefix_edit.setText(J.default_prefix(layer.name()))
        self._fill_fields()
        self._on_match_fields()

    def set_join_layer(self, layer):
        """Use *layer* (a project layer, or one opened from a file)."""
        if QgsProject.instance().mapLayer(layer.id()) is None \
                and layer not in self._pending:
            self._pending.append(layer)
            self.layer_combo.setAdditionalLayers(self._pending)
        self.drop_problem.setVisible(False)
        self.layer_combo.setLayer(layer)

    def _open(self, name, uri, provider):
        layer = QgsVectorLayer(uri, name, provider)
        if not layer.isValid():
            self._problem(f"Could not open {name} as a table")
            return None
        return layer

    def _problem(self, text):
        self.drop_problem.setText(text)
        self.drop_problem.setVisible(True)

    def accept_mime(self, mime, pos=None):
        """Take the join layer from dropped data; with several tables (a
        GeoPackage or a workbook) a menu asks which one."""
        choices = [c for c in mime_choices(mime)
                   if c[3] is None or c[3].id() != self.layer.id()]
        if not choices:
            self._problem("Drop a vector layer, a table or a file such as CSV, "
                          "Excel, GeoPackage or shapefile (this layer itself "
                          "cannot be joined to itself)")
            return None
        choice = choices[0]
        if len(choices) > 1:
            menu = QMenu(self)
            for c in choices:
                menu.addAction(c[0]).setData(c)
            act = menu.exec(self.mapToGlobal(pos) if pos is not None
                            else self.drop_zone.mapToGlobal(self.drop_zone.rect().center()))
            if act is None:
                return None
            choice = act.data()
        name, uri, provider, layer = choice
        if layer is None:
            layer = self._open(name, uri, provider)
            if layer is None:
                return None
        self.set_join_layer(layer)
        return layer

    def _set_hover(self, on):
        self.drop_zone.setProperty("hover", on)
        self.drop_zone.style().unpolish(self.drop_zone)
        self.drop_zone.style().polish(self.drop_zone)

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if QgsMimeDataUtils.isUriList(mime) or mime.hasUrls():
            event.acceptProposedAction()
            self._set_hover(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._set_hover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_hover(False)
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        event.acceptProposedAction()
        # the menu (several tables) must not open inside the drop itself
        mime = event.mimeData()
        data = QMimeData()
        for fmt in mime.formats():
            data.setData(fmt, mime.data(fmt))
        if mime.hasUrls():
            data.setUrls(mime.urls())
        QTimer.singleShot(0, lambda: self.accept_mime(data, pos))

    # ------------------------------------------------------------------
    # match fields and preview
    # ------------------------------------------------------------------
    def _on_join_field(self, *_args):
        self._fill_fields()
        self._on_match_fields()

    def _on_match_fields(self, *_args):
        self.counts = None
        self.runner.new_generation()
        layer = self.join_layer()
        target, join = self.target_combo.current_name(), self.join_combo.current_name()
        if layer is None:
            self._show_match("Choose the layer or table to join", "", "info")
        elif not target or not join:
            self._show_match("Choose the fields to match", "", "warning")
        else:
            self._show_match("Counting the records that match…", "", "busy")
            self._count_timer.start()
        self._update_names()

    def _count_matches(self):
        layer = self.join_layer()
        target, join = self.target_combo.current_name(), self.join_combo.current_name()
        if layer is None or not target or not join:
            return
        t_idx = self.layer.fields().indexFromName(target)
        j_idx = layer.fields().indexFromName(join)
        if t_idx < 0 or j_idx < 0:
            return
        t_src = QgsVectorLayerFeatureSource(self.layer)
        j_src = QgsVectorLayerFeatureSource(layer)
        kinds = (F.field_kind(self.layer.fields().at(t_idx)),
                 F.field_kind(layer.fields().at(j_idx)))

        def run(job):
            return J.match_counts(t_src, t_idx, j_src, j_idx, job)

        def done(job):
            if not self.runner.is_current(job):
                return
            if job.error:
                last = job.error.strip().splitlines()[-1]
                self._show_match("Could not count the matches", last, "error")
                return
            self.counts = job.result
            head, detail, level = J.summary(job.result)
            if kinds[0] != kinds[1]:
                note = (f"{F.KIND_LABELS[kinds[0]]} = {F.KIND_LABELS[kinds[1]]}: "
                        "compared as text, as QGIS does")
                detail = f"{detail} · {note}" if detail else note
            self._show_match(head, detail, level)
            self._update_join_btn()

        self.runner.start("Counting join matches", run, done)

    def _show_match(self, head, detail, level):
        self.match_head.setText(head)
        self.match_detail.setText(detail)
        self.match_detail.setVisible(bool(detail))
        self.match_box.setProperty("level", level)
        self.match_box.style().unpolish(self.match_box)
        self.match_box.style().polish(self.match_box)

    # ------------------------------------------------------------------
    # fields to add
    # ------------------------------------------------------------------
    def _fill_fields(self):
        previous = {it.data(Qt.ItemDataRole.UserRole): it.checkState()
                    for it in self._field_items()}
        self.field_list.blockSignals(True)
        self.field_list.clear()
        layer = self.join_layer()
        join = self.join_combo.current_name()
        if layer is None:
            hint = QListWidgetItem("The fields of the layer to join appear here")
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            self.field_list.addItem(hint)
        else:
            for fld in layer.fields():
                if fld.name() == join:
                    continue             # QGIS never adds the join field
                item = QListWidgetItem(icons.field(F.field_kind(fld), self.theme),
                                       fld.name())
                item.setData(Qt.ItemDataRole.UserRole, fld.name())
                item.setToolTip(W.field_tooltip(fld))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(previous.get(fld.name(), Qt.CheckState.Checked))
                self.field_list.addItem(item)
        self.field_list.blockSignals(False)
        self._update_names()

    def _field_items(self):
        items = (self.field_list.item(i) for i in range(self.field_list.count()))
        return [it for it in items if it.data(Qt.ItemDataRole.UserRole)]

    def chosen_fields(self):
        return [it.data(Qt.ItemDataRole.UserRole) for it in self._field_items()
                if it.checkState() == Qt.CheckState.Checked]

    def _tick_all(self, on):
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self.field_list.blockSignals(True)
        for item in self._field_items():
            item.setCheckState(state)
        self.field_list.blockSignals(False)
        self._update_names()

    def _on_prefix_edited(self, _text):
        self._prefix_edited = True
        self._update_names()

    def prefix(self):
        return self.prefix_edit.text()

    def _update_names(self):
        names = J.joined_names(self.chosen_fields(), self.prefix())
        self.names_preview.setText(("→ " + ", ".join(names)) if names else "")
        clash = J.clashes(self.layer.fields(), self.chosen_fields(), self.prefix())
        if clash:
            self.clash_label.setText(
                f"Already in this layer, so QGIS leaves {'it' if len(clash) == 1 else 'them'} "
                f"out: {', '.join(clash)}. Change the prefix to add "
                f"{'it' if len(clash) == 1 else 'them'}.")
        self.clash_label.setVisible(bool(clash))
        self._update_join_btn()

    def _update_join_btn(self):
        layer = self.join_layer()
        chosen = self.chosen_fields()
        clash = set(J.clashes(self.layer.fields(), chosen, self.prefix()))
        if layer is None:
            why = "Choose the layer or table to join first"
        elif not self.target_combo.current_name() or not self.join_combo.current_name():
            why = "Choose the fields to match"
        elif not chosen:
            why = "Tick at least one field to add"
        elif len(clash) == len(chosen):
            why = "Every field to add already exists in this layer: change the prefix"
        else:
            why = ""
        self.join_btn.setEnabled(not why)
        self.join_btn.setToolTip(why or "Add the join to the layer")

    # ------------------------------------------------------------------
    # adding and removing joins
    # ------------------------------------------------------------------
    def add_join(self):
        layer = self.join_layer()
        if layer is None or not self.join_btn.isEnabled():
            return False
        if layer in self._pending:
            QgsProject.instance().addMapLayer(layer)
            self._pending.remove(layer)
        info = QgsVectorLayerJoinInfo()
        info.setJoinLayer(layer)
        info.setJoinFieldName(self.join_combo.current_name())
        info.setTargetFieldName(self.target_combo.current_name())
        info.setUsingMemoryCache(True)
        info.setPrefix(self.prefix())
        chosen = self.chosen_fields()
        if len(chosen) < len(self._field_items()):
            info.setJoinFieldNamesSubset(chosen)
        before = {f.name() for f in self.layer.fields()}
        if not self.layer.addJoin(info):
            self._problem(f"QGIS could not join {layer.name()} to this layer "
                          f"(a layer cannot be joined in a circle)")
            return False
        self.layer.triggerRepaint()
        added = [f.name() for f in self.layer.fields() if f.name() not in before]
        match = ""
        if self.counts:
            head, _detail, _level = J.summary(self.counts)
            match = f" — {head}"
        self._tell(f"Joined {layer.name()}: {len(added):,} field(s) added{match}")
        self.accept()
        return True

    def _tell(self, text):
        # kept in the status bar through the refresh the new fields cause
        self.panel._status_note = (time.monotonic() + 8, text)
        self.panel.status.set_status(text, "success")

    def _join_text(self, info):
        layer = info.joinLayer()
        name = layer.name() if layer is not None else info.joinLayerId()
        subset = info.joinFieldNamesSubset()
        fields = (f"{len(subset)} field(s)" if subset is not None else "all fields")
        prefix = info.prefix()
        extra = f" · prefix {prefix}" if prefix else ""
        return name, f"{info.targetFieldName()} = {info.joinFieldName()} · {fields}{extra}"

    def _fill_joins(self):
        while self.joins_layout.count():
            item = self.joins_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        joins = list(self.layer.vectorJoins())
        if not joins:
            self.joins_layout.addWidget(W.label("No joins yet", "dim"))
            return
        for info in joins:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(self.metrics.gap)
            mark = QLabel()
            mark.setPixmap(icons.native(icons.JOIN).pixmap(QSize(self.metrics.icon,
                                                                 self.metrics.icon)))
            rl.addWidget(mark)
            name, detail = self._join_text(info)
            rl.addWidget(W.label(name, "title"))
            text = W.ElideLabel(detail)
            text.setObjectName("gtsDim")
            rl.addWidget(text, 1)
            remove = W.square_button(self.metrics, icons.native(icons.DELETE),
                                     f"Remove the join to {name}")
            remove.clicked.connect(lambda _c=False, lid=info.joinLayerId(), n=name:
                                   self.remove_join(lid, n))
            rl.addWidget(remove)
            self.joins_layout.addWidget(row)

    def remove_join(self, join_layer_id, name=""):
        if self.layer.removeJoin(join_layer_id):
            self.layer.triggerRepaint()
            self._tell(f"Removed the join to {name}")
        self._fill_joins()
        self.target_combo.set_fields(self.layer.fields())
        self._update_names()

    # ------------------------------------------------------------------
    def done(self, result):
        self.runner.new_generation()
        self._count_timer.stop()
        self.layer_combo.setAdditionalLayers([])
        self._pending = []               # files not joined stay out of the project
        super().done(result)

