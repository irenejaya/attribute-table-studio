# -*- coding: utf-8 -*-
"""
Attribute Explorer Form view (View > Form view).

A list of the records in scope (labelled with a display expression) next
to the layer's configured QGIS attribute form for the current record, with
previous / next and flash / pan / zoom.  The form writes to the layer's
edit buffer when the record changes, when switching back to the table and
before saving - the same edit session as the table and the map.
"""

from qgis.PyQt.QtCore import QAbstractListModel, QModelIndex, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListView, QScrollArea, QSplitter,
    QVBoxLayout, QWidget,
)
from qgis.core import (
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsFeature,
    QgsFeatureRequest,
)
from qgis.gui import QgsAttributeEditorContext, QgsAttributeForm, QgsFieldExpressionWidget

from ..core import fields as F
from ..core.compat import NO_GEOMETRY
from . import icons
from . import widgets as W
from .explorer_dialogs import form_context

_SINGLE = QgsAttributeEditorContext.SingleEditMode


class FeatureListModel(QAbstractListModel):
    """Records in scope, each labelled by the display expression (pages
    of records are read when shown)."""

    PAGE = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layer = None
        self.fids = []
        self._labels = {}
        self._exp = None
        self._ctx = None

    def set_layer(self, layer, expression):
        self.beginResetModel()
        self.layer = layer
        self.fids = []
        self._labels = {}
        self._set_expression(expression)
        self.endResetModel()

    def _set_expression(self, expression):
        layer = self.layer
        self._exp = QgsExpression(expression or "") if layer is not None else None
        self._ctx = None
        if layer is not None:
            self._ctx = QgsExpressionContext(
                QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            self._exp.prepare(self._ctx)

    def set_expression(self, expression):
        self.beginResetModel()
        self._labels = {}
        self._set_expression(expression)
        self.endResetModel()

    def set_fids(self, fids):
        self.beginResetModel()
        self.fids = list(fids)
        self._labels = {}
        self.endResetModel()

    def invalidate(self):
        self._labels = {}
        if self.fids:
            self.dataChanged.emit(self.index(0), self.index(len(self.fids) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.fids)

    def _load(self, row):
        start = max(0, row - self.PAGE // 4)
        chunk = [f for f in self.fids[start:start + self.PAGE] if f not in self._labels]
        if not chunk or self.layer is None:
            return
        req = QgsFeatureRequest().setFilterFids(chunk)
        exp = self._exp
        if exp is None or not exp.needsGeometry():
            req.setFlags(NO_GEOMETRY)
        got = set()
        for feat in self.layer.getFeatures(req):
            label = ""
            if exp is not None and exp.expression():
                self._ctx.setFeature(feat)
                value = exp.evaluate(self._ctx)
                label = F.NULL_TEXT if F.is_null(value) else F.display_value(value)
            self._labels[feat.id()] = label or f"Feature {feat.id()}"
            got.add(feat.id())
        for fid in chunk:
            self._labels.setdefault(fid, f"Feature {fid}")

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        fid = self.fids[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            if fid not in self._labels:
                self._load(index.row())
            return self._labels.get(fid, f"Feature {fid}")
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"Feature id {fid}"
        if role == Qt.ItemDataRole.UserRole:
            return fid
        return None


class FormView(QWidget):
    """Feature list + the layer's attribute form."""

    currentChanged = pyqtSignal(object)        # fid

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        m = panel.metrics
        self.layer = None
        self.current = None
        self.form = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        lay.addWidget(split)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        self.display_expr = QgsFieldExpressionWidget()
        self.display_expr.setFixedHeight(m.control_h)
        self.display_expr.setToolTip("Display expression used to label the records")
        lv.addWidget(self.display_expr)
        self.list_model = FeatureListModel(self)
        self.list = QListView()
        self.list.setObjectName("gtsFormList")
        self.list.setModel(self.list_model)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lv.addWidget(self.list, 1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("gtsDim")
        lv.addWidget(self.count_label)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(m.gap)
        sq = W.square_button
        self.prev_btn = sq(m, icons.native("/mActionArrowUp.svg"), "Previous record")
        self.next_btn = sq(m, icons.native("/mActionArrowDown.svg"), "Next record")
        self.title = QLabel("")
        self.title.setObjectName("gtsTitle")
        self.flash_btn = sq(m, icons.native(icons.FLASH), "Flash this feature on the map")
        self.pan_btn = sq(m, icons.native(icons.PAN_SELECTED), "Pan the map to this feature")
        self.zoom_btn = sq(m, icons.native(icons.ZOOM_TO), "Zoom the map to this feature")
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.next_btn)
        bar.addSpacing(m.gap)
        bar.addWidget(self.title, 1)
        for b in (self.flash_btn, self.pan_btn, self.zoom_btn):
            bar.addWidget(b)
        rv.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        rv.addWidget(self.scroll, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 700])

        self.list.selectionModel().currentChanged.connect(self._on_list_current)
        self.display_expr.fieldChanged.connect(
            lambda *_: self.list_model.set_expression(self.display_expr.expression()))
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn.clicked.connect(lambda: self._step(1))
        self.flash_btn.clicked.connect(lambda: self._map("flash"))
        self.pan_btn.clicked.connect(lambda: self._map("pan"))
        self.zoom_btn.clicked.connect(lambda: self._map("zoom"))

    # ------------------------------------------------------------------
    def set_layer(self, layer):
        self.flush(commit=False)
        self.layer = layer
        self.current = None
        expression = layer.displayExpression() if layer is not None else ""
        self.display_expr.setLayer(layer)
        self.display_expr.setExpression(expression)
        self.list_model.set_layer(layer, expression)
        if self.form is not None:
            self.form.setParent(None)
            self.form.deleteLater()
            self.form = None
        if layer is not None:
            self.form = QgsAttributeForm(layer, QgsFeature(), form_context(self.panel))
            self.form.setMode(_SINGLE)
            if hasattr(self.form, "setShowButtonBox"):
                self.form.setShowButtonBox(False)
            self.scroll.setWidget(self.form)
        spatial = layer is not None and layer.isSpatial()
        for b in (self.flash_btn, self.pan_btn, self.zoom_btn):
            b.setEnabled(spatial)

    def set_fids(self, fids):
        keep = self.current
        self.list_model.set_fids(fids)
        self.count_label.setText(f"{len(fids):,} records in scope")
        if keep is not None and keep in set(fids):
            self.set_current(keep, flush=False)
        elif fids:
            self.set_current(fids[0], flush=False)
        else:
            self.current = None
            self.title.setText("No records in scope")

    def flush(self, commit=True):
        """Write (or drop) the form's changes into the edit buffer."""
        form = self.form
        if form is None or self.current is None or self.layer is None:
            return True
        if not self.layer.isEditable():
            return True
        if commit:
            return bool(form.save())
        form.resetValues()
        return True

    def set_current(self, fid, flush=True):
        if fid is None or self.layer is None:
            return
        if flush and self.current is not None and fid != self.current:
            self.flush()
        self.current = fid
        feat = self.layer.getFeature(fid)
        if self.form is not None and feat.isValid():
            self.form.setFeature(feat)
        try:
            row = self.list_model.fids.index(fid)
        except ValueError:
            row = -1
        if row >= 0:
            idx = self.list_model.index(row)
            self.list.selectionModel().blockSignals(True)
            self.list.setCurrentIndex(idx)
            self.list.selectionModel().blockSignals(False)
            self.list.scrollTo(idx)
        label = self.list_model.data(self.list_model.index(row)) if row >= 0 else ""
        self.title.setText(f"{label}   ·   feature {fid}" if label else f"Feature {fid}")
        self.prev_btn.setEnabled(row > 0)
        self.next_btn.setEnabled(0 <= row < len(self.list_model.fids) - 1)
        self.currentChanged.emit(fid)

    def _on_list_current(self, current, _previous):
        if current.isValid():
            self.set_current(self.list_model.fids[current.row()])

    def _step(self, delta):
        fids = self.list_model.fids
        if self.current not in fids:
            return
        row = fids.index(self.current) + delta
        if 0 <= row < len(fids):
            self.set_current(fids[row])

    def _map(self, what):
        if self.current is None or self.layer is None:
            return
        canvas = self.panel.iface.mapCanvas()
        ids = [self.current]
        if what == "zoom":
            canvas.zoomToFeatureIds(self.layer, ids)
        elif what == "pan":
            canvas.panToFeatureIds(self.layer, ids)
        canvas.flashFeatureIds(self.layer, ids)
