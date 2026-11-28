# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Data Plot: plots made from a layer's records (the fourth tool).

One window per plot shown: the layer's plot list on the left, the plot in
the middle, its properties on the right (Data, Axes, Guides, Format,
General, as in ArcGIS Pro's Chart Properties).  The plots of every layer
are kept in the QGIS project (:class:`PlotStore`), so they come back with
the project; one can also be saved to a file and loaded on another layer.

The records come from a choice of All records, the Attribute Explorer's
filtered records, Selected records or the visible map extent, and the plot
follows them live (filters, selection, map extent and edits).  Reading
runs in a background job and keeps only the fields the plot uses
(:mod:`..core.plots`); options that need no new field never read the layer
again.  Selection works both ways: a click or a drag on the plot selects
the records on the layer, and records selected anywhere are shown in the
plot in the project's selection colour.
"""

import copy
import json
import time
import uuid

from qgis.PyQt.QtCore import QObject, QRect, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QGuiApplication, QIcon
from qgis.PyQt.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFontComboBox, QFrame,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QVBoxLayout,
    QWidget,
)
from qgis.core import (
    QgsCoordinateTransform, QgsExpressionContext, QgsMessageLog, QgsProject,
    QgsRenderContext, QgsVectorLayer,
)
from qgis.gui import QgsColorButton

import numpy as np

from ..core import export as X
from ..core import fields as F
from ..core import plots as P
from ..core.compat import SELECT_ADD, SELECT_SET, VECTOR_LAYER_FILTER
from ..core.jobs import JobRunner
from ..core.scan import ScanSpec
from . import icons
from . import plot_edit as PE
from . import widgets as W
from .plot_view import (
    POINT_SHAPES, SHAPE_LABELS, PlotModel, PlotView, fill_type_menu, marker_icon, series_colour,
    value_text,
)
from .theme import Metrics, Theme, stylesheet

LOG_TAG = "Attribute Table Studio"
PROJECT_SCOPE = "AttributeTableStudio"
PROJECT_PLOTS = "dataPlots"
FILE_TAG = "attribute_table_studio_plot"
SYNC_LIMIT = 250000              # up to this many records, plots build at once
SCOPES = (("all", "All records", icons.TABLE),
          ("explorer", "Explorer filtered records", icons.FILTER),
          ("selected", "Selected records", icons.SELECTED_ONLY),
          ("extent", "Visible map extent", icons.TABLE_VISIBLE))
SCOPE_LABELS = {k: t for k, t, _i in SCOPES}
CHOOSE = "Choose a field…"
# types with value axes (Axes and Guides sections)
AXES_TYPES = ("scatter", "line", "bar", "combo", "histogram", "box", "qq")


def _is_number(kind):
    return P.role(kind) == P.NUMBER and kind != F.TIME


def _is_date_or_number(kind):
    return P.role(kind) in (P.DATE, P.NUMBER)


def _is_category(kind):
    return kind in (F.TEXT, F.BOOL, F.INTEGER, F.OTHER) or P.role(kind) == P.DATE


def _is_split(kind):
    return kind in (F.TEXT, F.BOOL, F.INTEGER, F.OTHER)


def self_type(plot):
    return plot.get("type") if plot else None


EDITING_HINT = ("Editing the look of the plot: the plot and its settings are frozen. "
                "Click a part of the plot, or Add an object.")
LAYER_ROLE = Qt.ItemDataRole.UserRole + 1        # the plot's layer, shown after its name


class _PlotItemDelegate(QStyledItemDelegate):
    """A row of the plot list: the type icon, the plot's name, then its
    layer, dimmed."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        where = index.data(LAYER_ROLE)
        if not where:
            return
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget)
        fm = opt.fontMetrics
        x = rect.left() + fm.horizontalAdvance(opt.text) + 8
        room = rect.right() - x - 2
        if room < 24:
            return
        painter.save()
        painter.setPen(QColor(self.theme.text_dim))
        painter.drawText(QRect(int(x), rect.top(), int(room), rect.height()),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                         fm.elidedText(f"· {where}", Qt.TextElideMode.ElideRight, int(room)))
        painter.restore()


class _SectionHeader(QPushButton):
    """The header of a section that opens and closes (an arrow, then its
    text, elided to the room it has)."""

    def __init__(self, metrics):
        super().__init__()
        self._full = ""
        self.setObjectName("gtsDisclosure")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(metrics.control_h)
        self.setIconSize(QSize(14, 14))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QPushButton#gtsDisclosure { text-align: left; border: none; "
                           "padding: 0px; font-weight: bold; background: transparent; }")

    def set_full_text(self, text):
        self._full = text
        self._elide()

    def full_text(self):
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        room = max(20, self.width() - self.iconSize().width() - 14)
        self.setText(" " + self.fontMetrics().elidedText(self._full,
                                                         Qt.TextElideMode.ElideRight, room))


# ============================================================================
# PLOTS IN THE PROJECT
# ============================================================================

class PlotStore(QObject):
    """Every plot of the project, in one list kept in the QGIS project as
    JSON; each plot names its own layer ("layer": the layer's id, and
    "layer_name" to show while the layer is not in the project).  One store
    is shared by all Data Plot windows; *changed* tells them the list or a
    plot changed (layer id and plot id; "" for the whole list).  Projects
    saved before kept a list per layer: it is read into the one list."""

    changed = pyqtSignal(str, str)           # layer id, plot id ("" for the list)
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._data = None
        project = QgsProject.instance()
        project.readProject.connect(self._forget)
        project.cleared.connect(self._forget)

    def _forget(self, *_args):
        self._data = None
        self.changed.emit("", "")

    def _load(self):
        if self._data is None:
            text, _ok = QgsProject.instance().readEntry(PROJECT_SCOPE, PROJECT_PLOTS, "")
            try:
                data = json.loads(text) if text else {}
            except ValueError:
                data = {}
            plots = []
            if isinstance(data, dict) and isinstance(data.get("plots"), list):
                plots = [p for p in data["plots"] if isinstance(p, dict)]
            elif isinstance(data, dict):             # before: a list per layer
                for layer_id, items in data.items():
                    for plot in items if isinstance(items, list) else []:
                        if isinstance(plot, dict):
                            plot.setdefault("layer", layer_id)
                            plots.append(plot)
            for plot in plots:                       # what earlier versions lacked
                P.complete_plot(plot)
            self._data = [p for p in plots if p.get("type") in P.TYPE_LABELS]
        return self._data

    def _save(self, layer_id="", plot_id=""):
        QgsProject.instance().writeEntry(PROJECT_SCOPE, PROJECT_PLOTS,
                                         json.dumps({"version": 2, "plots": self._load()}))
        self.changed.emit(layer_id, plot_id)

    def plots(self, layer_id=None):
        """Every plot, or the plots of one layer."""
        plots = self._load()
        if layer_id is None:
            return plots
        return [p for p in plots if p.get("layer") == layer_id]

    def get(self, plot_id):
        for plot in self._load():
            if plot.get("id") == plot_id:
                return plot
        return None

    def add(self, plot, after=None):
        """Add *plot* (its "layer" set) after the plot *after*; its id."""
        plot = copy.deepcopy(plot)
        plot["id"] = uuid.uuid4().hex[:12]
        plots = self._load()
        ids = [p.get("id") for p in plots]
        pos = ids.index(after) + 1 if after in ids else len(plots)
        plots.insert(pos, plot)
        self._save(plot.get("layer", ""))
        return plot["id"]

    def update(self, plot):
        plots = self._load()
        for i, p in enumerate(plots):
            if p.get("id") == plot.get("id"):
                plots[i] = copy.deepcopy(plot)
                self._save(plot.get("layer", ""), plot["id"])
                return

    def remove(self, plot_id):
        plots = self._load()
        gone = self.get(plot_id)
        plots[:] = [p for p in plots if p.get("id") != plot_id]
        self._save(gone.get("layer", "") if gone else "")

    def move(self, plot_id, delta):
        plots = self._load()
        ids = [p.get("id") for p in plots]
        if plot_id not in ids:
            return
        i = ids.index(plot_id)
        j = max(0, min(len(plots) - 1, i + delta))
        if i != j:
            plots.insert(j, plots.pop(i))
            self._save()


# ============================================================================
# LAYER SYMBOLOGY CLASSES
# ============================================================================

class SymbologyClasses:
    """The legend class of each record, from a copy of the layer's renderer
    (as the Layers panel shows it): a scatter plot then uses the layer's
    colours, as ArcGIS Pro does.  Built on the GUI thread; :meth:`start`,
    :meth:`code` and :meth:`stop` run in the reading job."""

    def __init__(self, layer):
        self.renderer = None
        self.labels, self.colours, self._index = [], [], {}
        self.attrs, self.geometry = [], False
        renderer = layer.renderer() if hasattr(layer, "renderer") else None
        if renderer is None:
            return
        try:
            self.renderer = renderer.clone()
            for item in self.renderer.legendSymbolItems():
                symbol = item.symbol()
                if symbol is None or not item.ruleKey():
                    continue
                self._index[item.ruleKey()] = len(self.labels)
                self.labels.append(item.label())
                self.colours.append(symbol.color().name())
            fields = layer.fields()
            used = self.renderer.usedAttributes(QgsRenderContext())
            self.attrs = [fields.lookupField(n) for n in used if fields.lookupField(n) >= 0]
            self.geometry = bool(self.renderer.filterNeedsGeometry())
            self.fields = fields
        except Exception:                    # an unusual renderer: one colour
            self.renderer = None
            self.labels, self.colours = [], []

    @property
    def usable(self):
        return self.renderer is not None and bool(self.labels)

    def start(self, spec):
        self.ctx = QgsRenderContext()
        self.ctx.setExpressionContext(QgsExpressionContext(spec.context))
        self.renderer.startRender(self.ctx, spec.fields)

    def code(self, feat):
        self.ctx.expressionContext().setFeature(feat)
        try:
            keys = self.renderer.legendKeysForFeature(feat, self.ctx)
        except Exception:
            return -1
        for key in keys:
            i = self._index.get(key)
            if i is not None:
                return i
        return -1

    def stop(self):
        try:
            self.renderer.stopRender(self.ctx)
        except Exception:
            pass


# ============================================================================
# PROPERTIES
# ============================================================================

class PlotProperties(QScrollArea):
    """The plot's settings, in the card Visualize Statistics and the Pivot
    Table chart use: section headers (Data, Time binning, Statistics on the
    chart, Axes, Guides, Summary, Appearance) and "Label [box]" rows.  The
    sections hold ArcGIS Pro's Chart Properties (Data, Axes, Guides, Format,
    General).  Edits change the plot (a dict) in place and emit *edited*
    (True when the plot needs other records or fields read)."""

    edited = pyqtSignal(bool)
    layerChosen = pyqtSignal(object)        # another layer for the plot (Layer in Data)
    WIDTH = 312                              # as the Visualize Statistics card

    def __init__(self, theme, metrics, parent=None):
        super().__init__(parent)
        self.theme, self.metrics = theme, metrics
        self.plot = None
        self.fields = None
        self.series = []                     # series of the last build (colours)
        self.summary_rows = []
        self._trend_open = {}                # (plot id, Y field): its trend section open
        self._layer_pending = False          # the Layer row is still to come (under Data)
        self._rebuild_pending = False        # a rebuild waits for the event loop
        self.layer_combo = None
        self._quiet = False
        self.title_edit = None
        self.colour_lay = None
        self.summary_grid = None
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedWidth(self.WIDTH)
        self._card()

    # ---- helpers (the Visualize Statistics card's) -----------------------
    def _card(self):
        card = QFrame()
        card.setObjectName("gtsCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        self.setWidget(card)
        self.lay = lay
        return lay

    def _label(self, text):
        """A row's label, in the card's one label column (align_labels)."""
        lbl = W.label(text, "dim")
        self._labels.append(lbl)
        return lbl

    def _row(self, text, widget, tip="", stretch=1):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(self.metrics.gap)
        row.addWidget(self._label(text))
        row.addWidget(widget, stretch)
        if tip:
            holder.setToolTip(tip)
        self.lay.addWidget(holder)
        return holder

    def _combo(self, items, current):
        box = QComboBox()
        box.setFixedHeight(self.metrics.control_h)
        box.setIconSize(QSize(self.metrics.icon, self.metrics.icon))
        # long choices never widen the card: the box shrinks, its list does not
        box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(6)
        for item in items:
            key, text = item[0], item[1]
            if len(item) > 2:
                box.addItem(item[2], text, key)
            else:
                box.addItem(text, key)
        i = box.findData(current)
        box.setCurrentIndex(i if i >= 0 else 0)
        return box

    def _field_combo(self, current, accept, none_label=None):
        box = W.FieldCombo(self.metrics, self.theme, none_label=none_label)
        box.setMinimumContentsLength(4)
        box.setMinimumWidth(80)
        box.set_fields(self.fields, keep=False, accept=accept)
        if current:
            box.set_current_name(current)
        elif none_label is not None:
            box.setCurrentIndex(0)
        return box

    def _tick(self, text, on, tip=""):
        box = QCheckBox(text)
        box.setChecked(bool(on))
        if tip:
            box.setToolTip(tip)
        self.lay.addWidget(box)
        return box

    def _edit(self, text, placeholder=""):
        e = QLineEdit(text or "")
        e.setFixedHeight(self.metrics.control_h)
        if placeholder:
            e.setPlaceholderText(placeholder)
        return e

    def _note(self, text=""):
        note = QLabel(text)
        note.setObjectName("gtsDim")
        note.setWordWrap(True)
        self.lay.addWidget(note)
        return note

    def _header(self, text):
        header = W.section_header(text)
        self.lay.addWidget(header)
        if text == "Data" and self._layer_pending:   # the plot's layer heads Data
            self._layer_pending = False
            self._layer_row()
        return header

    def _layer_row(self):
        """Layer: the layer this plot is made from; choosing another moves
        the plot to it (layerChosen), keeping the fields of the same names."""
        layer = QgsProject.instance().mapLayer(self.plot.get("layer") or "")
        layer = layer if isinstance(layer, QgsVectorLayer) else None
        combo = W.LayerCombo(self.metrics)
        combo.setFilters(VECTOR_LAYER_FILTER)
        combo.setAllowEmptyLayer(layer is None)       # its layer left the project
        combo.setLayer(layer)
        combo.setMinimumWidth(80)
        self._row("Layer", combo, "The layer this plot is made from; choosing another "
                  "keeps the fields that have the same names there")

        def chosen(_index):
            new = combo.currentLayer()
            if new is not None and (layer is None or new.id() != layer.id()):
                self.layerChosen.emit(new)

        # only a layer you pick: the box also moves by itself when a layer
        # leaves the project, and the plot must not follow it then
        combo.activated.connect(chosen)
        self.layer_combo = combo

    def _changed(self, read=False):
        if not self._quiet:
            self.edited.emit(read)

    def _rebuild(self):
        """Lay the card out again (a Y field added or removed), keeping
        the scroll position.  It waits for the event loop: the box or button
        that asked for it is deleted with the card, and deleting it while
        its own signal still runs freezes or crashes QGIS 3 (Qt 5)."""
        if not self._rebuild_pending:
            self._rebuild_pending = True
            QTimer.singleShot(0, self._rebuild_now)

    def _rebuild_now(self):
        self._rebuild_pending = False
        if self.plot is None:                    # Edit plot shows its own card
            return
        pos = self.verticalScrollBar().value()
        self.set_plot(self.plot, self.fields)
        self.verticalScrollBar().setValue(pos)

    # ---- building ------------------------------------------------------
    def set_plot(self, plot, fields):
        """Show *plot*'s settings (None: an empty card)."""
        self.plot, self.fields = plot, fields
        self._quiet = True
        self._card()                         # setWidget deletes the previous card
        self._labels = []
        self.title_edit = self.colour_lay = self.summary_grid = self.layer_combo = None
        self._layer_pending = plot is not None
        if plot is not None and fields is None:  # its layer is not in the project
            self._header("Data")
            self._note("This plot's layer is not in the project. Choose a layer: the "
                       "plot keeps the fields that have the same names there.")
        elif plot is not None:
            t = plot["type"]
            if t == "scatter":
                self._scatter_data()
            elif t in ("line", "bar"):
                self._series_data()
            else:
                getattr(self, f"_{t}_data")()
            if t in AXES_TYPES:
                self._axes()
                self._guides()
            self._summary()
            self._appearance()
        self.lay.addStretch(1)
        self._label_width = W.align_labels(self._labels)
        if self.colour_lay is not None:          # series names take the column too
            self._fill_colours()
        self._quiet = False

    # ---- Data: scatter -------------------------------------------------
    def _scatter_data(self):
        d = self.plot["data"]
        names = P.scatter_fields(d)
        if names and not d.get("fields"):
            d["fields"] = list(names)            # a plot made before several Y fields
        trends = d.setdefault("trends", {})
        for k, name in enumerate(names):         # each Y field keeps its own settings
            if name not in trends:
                trends[name] = P.trend_options(d, name, first=(k == 0))
        self._header("Data")
        x = self._field_combo(d.get("x"), _is_number, none_label=CHOOSE)
        self._row("X-axis", x, "The number field along the bottom")
        first, extra, add = self._y_rows(names, tip="The number field up the side; each "
                                         "further Y field is a series of its own colour")
        size = self._field_combo(d.get("size"), _is_number, none_label="None")
        self._row("Size by", size, "A third number field sizes each point (a bubble "
                  "plot); a NULL size draws the smallest point")
        smin = QSpinBox()
        smin.setRange(1, 60)
        smin.setValue(int(d.get("size_min", 4)))
        smin.setFixedHeight(self.metrics.control_h)
        smax = QSpinBox()
        smax.setRange(2, 120)
        smax.setValue(int(d.get("size_max", 20)))
        smax.setFixedHeight(self.metrics.control_h)
        sizes = QWidget()
        sl = QHBoxLayout(sizes)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(self.metrics.gap)
        sl.addWidget(smin)
        sl.addWidget(W.label("to", "dim"))
        sl.addWidget(smax)
        sl.addWidget(W.label("px", "dim"))
        sl.addStretch(1)
        size_row = self._row("Point size", sizes)
        size_row.setVisible(bool(d.get("size")))

        self._header("Statistics on the chart")
        for name in names:
            # a section per Y field; one Y field's is open to start with
            key = (self.plot.get("id"), name)
            opened = self._trend_open.setdefault(key, len(names) == 1)
            self._trend_block(trends[name], name, key, opened)
        if not names:
            self._note("Choose a Y field for its trend line.")

        def field(key, box):
            def changed(*_a):
                d[key] = box.current_name()
                size_row.setVisible(bool(d.get("size")))
                self._changed(True)
            box.currentIndexChanged.connect(changed)

        field("x", x)
        field("size", size)

        def set_fields(new, rename=None):
            """The Y fields changed: a field's trend settings stay with its row."""
            new = [n for n in new if n]
            if rename is not None:
                old_name, new_name = rename
                if old_name in trends and new_name:
                    trends[new_name] = trends.pop(old_name)
            d["fields"] = new
            d["y"] = new[0] if new else None
            self._changed(True)
            self._rebuild()

        def changed_first(*_a):
            names = P.scatter_fields(d)
            name = first.current_name()
            if not name:
                set_fields(names[1:])
            else:
                set_fields([name] + names[1:], (names[0] if names else None, name))

        def changed_extra(k, box):
            def changed(*_a):
                names = P.scatter_fields(d)
                if k < len(names):
                    old = names[k]
                    names[k] = box.current_name()
                    set_fields(names, (old, names[k]))
            return changed

        def remove_extra(k):
            def remove():
                names = P.scatter_fields(d)
                if k < len(names):
                    trends.pop(names[k], None)
                    del names[k]
                    set_fields(names)
            return remove

        def add_field():
            names = P.scatter_fields(d)
            spare = [self.fields.at(i).name() for i in range(self.fields.count())
                     if _is_number(F.field_kind(self.fields.at(i)))
                     and self.fields.at(i).name() not in names
                     and self.fields.at(i).name() != d.get("x")]
            if not names or not spare:
                return
            set_fields(names + [spare[0]])

        first.currentIndexChanged.connect(changed_first)
        for k, box, remove in extra:
            box.currentIndexChanged.connect(changed_extra(k, box))
            remove.clicked.connect(remove_extra(k))
        add.clicked.connect(add_field)

        def other(*_a):
            d["size_min"] = smin.value()
            d["size_max"] = max(smax.value(), smin.value() + 1)
            self._changed(True)

        for w in (smin, smax):
            w.valueChanged.connect(other)

    def _trend_block(self, o, name, key, opened):
        """One Y field's trend line (*o*, its settings), as a spreadsheet
        trend line's: type, order, forecast forward and backward, a set
        intercept, and the equation and R² in the legend; in a section that
        opens and closes from its header (the field and a summary)."""
        m = self.metrics
        header = _SectionHeader(m)
        self.lay.addWidget(header)
        body = QWidget()
        outer = self.lay                         # the section's rows go into its body
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(0, 0, 0, 4)
        self.lay.setSpacing(outer.spacing())
        on = self._tick("Trend line", o.get("on"),
                        "The least squares trend of Y on X")
        kind = self._combo([(k, text, icons.option_icon(k, self.theme))
                            for k, text in P.TRENDS], o.get("type", "linear"))
        kind_row = self._row("Trend", kind)
        order = QSpinBox()
        order.setRange(2, 6)
        order.setValue(int(o.get("order", 2) or 2))
        order.setFixedHeight(m.control_h)
        order_row = self._row("Order", order, "The polynomial's order (2: a parabola)")

        def units(value):
            box = QDoubleSpinBox()
            box.setRange(0, 1e12)
            box.setDecimals(2)
            box.setValue(float(value or 0))
            box.setFixedHeight(m.control_h)
            box.setMinimumWidth(50)              # its range would widen the card
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(m.gap)
            hl.addWidget(box, 1)
            hl.addWidget(W.label("X units", "dim"))
            return box, holder

        forward, fw = units(o.get("forward"))
        forward_row = self._row("Forward", fw, "Forecast: draw the line this far beyond "
                                "the largest X (in X units; a spreadsheet's periods)")
        backward, bw = units(o.get("backward"))
        backward_row = self._row("Backward", bw, "Forecast: draw the line this far before "
                                 "the smallest X (in X units)")
        # Set intercept: the tick in place of a label (it is wider than the
        # label column), its value beside it
        icpt = QCheckBox("Set intercept")
        icpt.setChecked(bool(o.get("set_intercept")))
        value = self._edit(P.nice_number(float(o.get("intercept") or 0.0)))
        value.setToolTip("The trend's value at X = 0")
        icpt_row = QWidget()
        il = QHBoxLayout(icpt_row)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(m.gap)
        il.addWidget(icpt)
        il.addWidget(value, 1)
        self.lay.addWidget(icpt_row)
        equation = QCheckBox("Equation")
        equation.setChecked(bool(o.get("equation", True)))
        r2 = QCheckBox("R²")
        r2.setChecked(bool(o.get("r2", True)))
        shows = QWidget()
        sl = QHBoxLayout(shows)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(12)
        sl.addWidget(equation)
        sl.addWidget(r2)
        sl.addStretch(1)
        show_row = self._row("Show", shows, "Show the trend's equation and R² in the "
                             "legend")
        note = self._note("R² measures the fit of a straight line; for this trend it is "
                          "that of the line on the logarithms, so read it with care.")
        icpt_note = self._note("With a set intercept, R² is worked out about the "
                               "intercept (as spreadsheet trend lines do), not about the "
                               "mean.")
        self.lay = outer
        self.lay.addWidget(body)

        def summary():
            if not on.isChecked():
                text = f"{name} · no trend line"
            else:
                text = f"{name} · {P.TREND_LABELS.get(kind.currentData(), '')} trend"
                extra = []
                if forward.value() or backward.value():
                    extra.append("forecast")
                if icpt.isChecked() and kind.currentData() in P.INTERCEPT_TRENDS:
                    extra.append("intercept")
                if extra:
                    text += f" ({', '.join(extra)})"
            header.setToolTip(text + "\nClick to show or hide its trend line settings")
            header.set_full_text(text)

        def show(state):
            self._trend_open[key] = state
            body.setVisible(state)
            header.setIcon(icons.glyph("chevron" if state else "chevron_right",
                                       self.theme.text_dim))

        header.clicked.connect(lambda: show(not self._trend_open.get(key)))
        show(opened)

        def follow():
            shown = on.isChecked()
            kt = kind.currentData()
            for w in (kind_row, forward_row, backward_row, icpt_row, show_row):
                w.setVisible(shown)
            order_row.setVisible(shown and kt == "polynomial")
            can = kt in P.INTERCEPT_TRENDS
            icpt.setEnabled(can)
            value.setEnabled(can and icpt.isChecked())
            icpt.setToolTip("Fix the trend's value at X = 0" if can else
                            "Logarithmic and power trends have no set intercept (as in "
                            "spreadsheets)")
            note.setVisible(shown and kt in ("exponential", "logarithmic", "power"))
            icpt_note.setVisible(shown and can and icpt.isChecked())

        follow()

        summary()

        def changed(*_a):
            o.update(on=on.isChecked(), type=kind.currentData(), order=order.value(),
                     forward=forward.value(), backward=backward.value(),
                     set_intercept=icpt.isChecked(), equation=equation.isChecked(),
                     r2=r2.isChecked())
            follow()
            summary()
            self._changed(True)

        def changed_value():
            text = value.text().strip().replace(",", "")
            try:
                number = float(text)
            except ValueError:
                number = None
            if number is None or number != number or abs(number) == float("inf"):
                value.setText(P.nice_number(float(o.get("intercept") or 0.0)))
                return
            if number != float(o.get("intercept") or 0.0):
                o["intercept"] = number
                self._changed(True)

        on.toggled.connect(changed)
        kind.currentIndexChanged.connect(changed)
        order.valueChanged.connect(changed)
        forward.valueChanged.connect(changed)
        backward.valueChanged.connect(changed)
        icpt.toggled.connect(changed)
        equation.toggled.connect(changed)
        r2.toggled.connect(changed)
        value.editingFinished.connect(changed_value)

    # ---- Data: line and bar --------------------------------------------
    def _series_data(self):
        d = self.plot["data"]
        t = self.plot["type"]
        m = self.metrics
        self._header("Data")
        if t == "line":
            x = self._field_combo(d.get("x"), _is_date_or_number, none_label=CHOOSE)
            self._row("X-axis", x, "A date field (grouped in time intervals) or a number "
                      "field (one point per value) along the bottom")
        else:
            x = self._field_combo(d.get("x"), _is_category, none_label=CHOOSE)
            self._row("X-axis", x, "The categories along the bottom, one bar each (dates "
                      "are grouped in time intervals)")
        fields = list(d.get("fields") or []) if d.get("agg", "count") != "count" else []
        first = self._field_combo(fields[0] if fields else None, _is_number,
                                  none_label="Count of records")
        self._row("Y-axis", first, "Count of records, or a numeric field summarised by "
                  "the aggregation (each further Y field is a series of its own)")
        extra = []
        for k, name in enumerate(fields[1:], 1):
            box = self._field_combo(name, _is_number)
            remove = W.square_button(m, icons.native(icons.REMOVE), "Remove this Y field")
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(m.gap)
            hl.addWidget(self._label("Y-axis"))
            hl.addWidget(box, 1)
            hl.addWidget(remove)
            self.lay.addWidget(holder)
            extra.append((k, box, remove))
        add = W.button(m, "Add Y field", icons.native(icons.ADD),
                       "Another numeric field: a series of its own")
        add_row = QWidget()
        al = QHBoxLayout(add_row)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(add)
        al.addStretch(1)
        self.lay.addWidget(add_row)
        agg = self._combo([a for a in P.AGGREGATIONS if a[0] != "count"],
                          d.get("agg") if d.get("agg") != "count" else "sum")
        agg_row = self._row("Aggregate", agg, "Aggregation: how the Y field of each "
                            + ("interval or value" if t == "line" else "category")
                            + "'s records is summarised (NULL values left out)")
        split = self._field_combo(d.get("split"), _is_split, none_label="None")
        self._row("Split by", split, "One series per category of this field (with one "
                  "Y field): the 8 with the most records get their own colour, the rest "
                  "form Other")
        bars = sort = None
        if t == "bar":
            bars = self._combo([(k, text, icons.option_icon(k, self.theme))
                                for k, text in P.MULTI_SERIES], d.get("multi", "side"))
            bars_row = self._row("Bars", bars, "How several series share a bar")
            sort = QComboBox()
            sort.setFixedHeight(m.control_h)
            sort.setIconSize(QSize(m.icon, m.icon))
            for key, text in P.SORTS:
                sort.addItem(icons.sort_icon(P.SORT_ICONS[key], self.theme), text, key)
            i = sort.findData(d.get("sort", "category_asc"))
            sort.setCurrentIndex(i if i >= 0 else 0)
            self._row("Sort", sort)
        hint = self._note()

        # time binning (a date along the X axis)
        tb_header = self._header("Time binning")
        unit = self._combo([(None, "Auto")] + list(P.UNITS), d.get("unit"))
        size = QSpinBox()
        size.setRange(1, 100000)
        size.setValue(int(d.get("interval", 1)))
        size.setFixedHeight(m.control_h)
        interval = QWidget()
        il = QHBoxLayout(interval)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(m.gap)
        il.addWidget(size)
        il.addWidget(unit, 1)
        interval_row = self._row("Interval", interval,
                                 "Auto works the interval out from the data's time span")
        align = self._combo(P.ALIGNMENTS, d.get("align", "start"))
        align_row = self._row("Align to", align, "Where the intervals start: the first "
                              "record, the last record (intervals end with it) or a "
                              "reference time")
        ref = self._edit(d.get("reference"), "YYYY-MM-DD HH:MM")
        ref_row = self._row("Reference", ref, "The reference time the intervals start from")
        trim = self._tick("Trim incomplete interval", d.get("trim"),
                          "Leave out an interval at either end that the data only partly "
                          "covers (it would look like a dip)")
        empty = empty_row = None
        if t == "line":
            empty = self._combo(P.EMPTY_BINS, d.get("empty", "zero"))
            empty_row = self._row("Empty bins", empty, "Intervals without records: 0 "
                                  "(right for counts), a line across, or a gap")

        def is_date():
            name = d.get("x")
            return bool(name) and self.fields.lookupField(name) >= 0 and P.role(
                F.field_kind(self.fields.field(name))) == P.DATE

        def follow():
            dated = is_date()
            for w in (tb_header, interval_row, align_row, trim):
                w.setVisible(dated)
            ref_row.setVisible(dated and align.currentData() == "reference")
            if empty_row is not None:
                empty_row.setVisible(dated)
            size.setEnabled(unit.currentData() is not None)
            counting = d.get("agg", "count") == "count"
            agg_row.setVisible(not counting)
            add.setEnabled(not counting and not d.get("split"))
            n_fields = len(d.get("fields") or []) if not counting else 0
            if bars is not None:
                bars_row.setVisible(bool(d.get("split")) or n_fields > 1)
            if d.get("split") and n_fields > 1:
                hint.setText(f"With Split by, one Y field is used: {d['fields'][0]}.")
            elif empty is not None and dated and not counting \
                    and empty.currentData() == "zero":
                hint.setText("Empty bins › Treat as zero draws intervals without records "
                             "at 0, which suits counts; for a "
                             f"{P.AGG_LABELS[d.get('agg', 'sum')].lower()}, Connect line or "
                             "Break line shows them as missing.")
            else:
                hint.setText("")
            hint.setVisible(bool(hint.text()))

        def changed_x(*_a):
            d["x"] = x.current_name()
            follow()
            self._changed(True)

        def changed_first(*_a):
            name = first.current_name()
            names = list(d.get("fields") or []) if d.get("agg", "count") != "count" else []
            if name is None:                         # Count of records
                d["agg"] = "count"
                d["fields"] = []
                self._changed(True)
                self._rebuild()
                return
            if d.get("agg", "count") == "count":
                d["agg"] = agg.currentData() or "sum"
            d["fields"] = [name] + names[1:]
            follow()
            self._changed(True)

        def changed_extra(k, box):
            def changed(*_a):
                names = list(d.get("fields") or [])
                if k < len(names):
                    names[k] = box.current_name()
                    d["fields"] = names
                    self._changed(True)
            return changed

        def remove_extra(k):
            def remove():
                names = list(d.get("fields") or [])
                if k < len(names):
                    del names[k]
                    d["fields"] = names
                    self._changed(True)
                    self._rebuild()
            return remove

        def add_field():
            names = list(d.get("fields") or [])
            spare = [self.fields.at(i).name() for i in range(self.fields.count())
                     if _is_number(F.field_kind(self.fields.at(i)))
                     and self.fields.at(i).name() not in names]
            if not names or not spare:
                return
            d["fields"] = names + [spare[0]]
            self._changed(True)
            self._rebuild()

        def changed_split(*_a):
            d["split"] = split.current_name()
            follow()
            self._changed(True)

        def other(*_a):
            if d.get("agg", "count") != "count":
                d["agg"] = agg.currentData()
            d["unit"] = unit.currentData()
            d["interval"] = size.value()
            d["align"] = align.currentData()
            d["reference"] = ref.text().strip() or None
            d["trim"] = trim.isChecked()
            if empty is not None:
                d["empty"] = empty.currentData()
            if bars is not None:
                d["multi"] = bars.currentData()
                d["sort"] = sort.currentData()
            follow()
            self._changed(True)

        follow()
        x.currentIndexChanged.connect(changed_x)
        first.currentIndexChanged.connect(changed_first)
        for k, box, remove in extra:
            box.currentIndexChanged.connect(changed_extra(k, box))
            remove.clicked.connect(remove_extra(k))
        add.clicked.connect(add_field)
        split.currentIndexChanged.connect(changed_split)
        for w in [agg, unit, align] + [w for w in (empty, bars, sort) if w is not None]:
            w.currentIndexChanged.connect(other)
        size.valueChanged.connect(other)
        ref.editingFinished.connect(other)
        trim.toggled.connect(other)

    # ---- shared rows ---------------------------------------------------
    def _y_rows(self, names, first_none=None, tip="", add_text="Add Y field",
                label="Y-axis"):
        """Rows of number fields (the first with *first_none* as its empty
        choice), each further row with a remove button, then an Add button.
        Returns (first combo, [(index, combo, remove)], add button)."""
        m = self.metrics
        first = self._field_combo(names[0] if names else None, _is_number,
                                  none_label=first_none or CHOOSE)
        self._row(label, first, tip)
        extra = []
        for k, name in enumerate(names[1:], 1):
            box = self._field_combo(name, _is_number)
            remove = W.square_button(m, icons.native(icons.REMOVE), "Remove this field")
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(m.gap)
            hl.addWidget(self._label(label))
            hl.addWidget(box, 1)
            hl.addWidget(remove)
            self.lay.addWidget(holder)
            extra.append((k, box, remove))
        add = W.button(m, add_text, icons.native(icons.ADD),
                       "Another number field: a series of its own")
        holder = QWidget()
        al = QHBoxLayout(holder)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(add)
        al.addStretch(1)
        self.lay.addWidget(holder)
        return first, extra, add

    def _wire_fields(self, d, key, first, extra, add, read=True, keep_first=False):
        """Keep d[key] (a list of field names) in step with _y_rows."""
        def changed_first(*_a):
            names = list(d.get(key) or [])
            name = first.current_name()
            if name is None and not keep_first:
                d[key] = names[1:]
                self._changed(read)
                self._rebuild()
                return
            d[key] = ([name] if name else []) + names[1:]
            self._changed(read)

        def changed_extra(k, box):
            def changed(*_a):
                names = list(d.get(key) or [])
                if k < len(names):
                    names[k] = box.current_name()
                    d[key] = names
                    self._changed(read)
            return changed

        def remove_extra(k):
            def remove():
                names = list(d.get(key) or [])
                if k < len(names):
                    del names[k]
                    d[key] = names
                    self._changed(read)
                    self._rebuild()
            return remove

        def add_field():
            names = list(d.get(key) or [])
            spare = [self.fields.at(i).name() for i in range(self.fields.count())
                     if _is_number(F.field_kind(self.fields.at(i)))
                     and self.fields.at(i).name() not in names]
            if not names or not spare:
                return
            d[key] = names + [spare[0]]
            self._changed(read)
            self._rebuild()

        first.currentIndexChanged.connect(changed_first)
        for k, box, remove in extra:
            box.currentIndexChanged.connect(changed_extra(k, box))
            remove.clicked.connect(remove_extra(k))
        add.clicked.connect(add_field)

    def _binning(self, d, empty_items, prefix="", field_key="x", title="Time binning"):
        """The Time binning section (shown for a date in d[field_key]; its
        settings are d[prefix + "unit"] ...); returns a function that shows
        or hides it."""
        m = self.metrics
        header = self._header(title)
        unit = self._combo([(None, "Auto")] + list(P.UNITS), d.get(prefix + "unit"))
        size = QSpinBox()
        size.setRange(1, 100000)
        size.setValue(int(d.get(prefix + "interval", 1)))
        size.setFixedHeight(m.control_h)
        interval = QWidget()
        il = QHBoxLayout(interval)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(m.gap)
        il.addWidget(size)
        il.addWidget(unit, 1)
        interval_row = self._row("Interval", interval,
                                 "Auto works the interval out from the data's time span")
        align = self._combo(P.ALIGNMENTS, d.get(prefix + "align", "start"))
        align_row = self._row("Align to", align)
        ref = self._edit(d.get(prefix + "reference"), "YYYY-MM-DD HH:MM")
        ref_row = self._row("Reference", ref)
        trim = self._tick("Trim incomplete interval", d.get(prefix + "trim"),
                          "Leave out an interval at either end that the data only partly "
                          "covers (it would look like a dip)")
        empty = empty_row = None
        if empty_items:
            empty = self._combo(empty_items, d.get("empty", "zero"))
            empty_row = self._row("Empty bins", empty, "Intervals without records")

        def is_date():
            name = d.get(field_key)
            return bool(name) and self.fields.lookupField(name) >= 0 and P.role(
                F.field_kind(self.fields.field(name))) == P.DATE

        def follow():
            dated = is_date()
            for w in (header, interval_row, align_row, trim, empty_row):
                if w is not None:
                    w.setVisible(dated)
            ref_row.setVisible(dated and align.currentData() == "reference")
            size.setEnabled(unit.currentData() is not None)

        def changed(*_a):
            d[prefix + "unit"] = unit.currentData()
            d[prefix + "interval"] = size.value()
            d[prefix + "align"] = align.currentData()
            d[prefix + "reference"] = ref.text().strip() or None
            d[prefix + "trim"] = trim.isChecked()
            if empty is not None:
                d["empty"] = empty.currentData()
            follow()
            self._changed(True)

        for w in (unit, align) + ((empty,) if empty is not None else ()):
            w.currentIndexChanged.connect(changed)
        size.valueChanged.connect(changed)
        ref.editingFinished.connect(changed)
        trim.toggled.connect(changed)
        follow()
        return follow

    # ---- Data: combo ---------------------------------------------------
    def _combo_data(self):
        d = self.plot["data"]
        m = self.metrics
        self._header("Data")
        x = self._field_combo(d.get("x"), lambda k: k != F.OTHER, none_label=CHOOSE)
        self._row("X-axis", x, "Categories, numbers or dates (grouped in time intervals) "
                  "along the bottom")
        series = d.setdefault("series", [])
        for k, spec in enumerate(series):
            self.lay.addWidget(W.label(f"Series {k + 1}", "strong"))
            field = self._field_combo(spec.get("field"), _is_number,
                                      none_label="Count of records")
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(m.gap)
            hl.addWidget(self._label("Y-axis"))
            hl.addWidget(field, 1)
            remove = W.square_button(m, icons.native(icons.REMOVE), "Remove this series")
            remove.setEnabled(len(series) > 1)
            hl.addWidget(remove)
            self.lay.addWidget(holder)
            agg = self._combo([a for a in P.AGGREGATIONS if a[0] != "count"],
                              spec.get("agg") if spec.get("agg") != "count" else "sum")
            agg_row = self._row("Aggregate", agg, "Aggregation of the field")
            agg_row.setVisible(bool(spec.get("field")))
            kind = self._combo((("bar", "Bar"), ("line", "Line")), spec.get("kind", "bar"))
            axis = self._combo((("left", "Left y-axis"), ("right", "Right y-axis")),
                               spec.get("axis", "left"))
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(m.gap)
            rl.addWidget(self._label("Show as"))
            rl.addWidget(kind, 1)
            rl.addWidget(axis, 1)
            self.lay.addWidget(row)

            def changed(*_a, spec=spec, field=field, agg=agg, kind=kind, axis=axis,
                        agg_row=agg_row):
                spec["field"] = field.current_name()
                spec["agg"] = agg.currentData() if spec["field"] else "count"
                spec["kind"] = kind.currentData()
                spec["axis"] = axis.currentData()
                agg_row.setVisible(bool(spec["field"]))
                self._changed(True)

            for w in (field, agg, kind, axis):
                w.currentIndexChanged.connect(changed)

            def drop(_c=False, k=k):
                if len(series) > 1:
                    del series[k]
                    self._changed(True)
                    self._rebuild()

            remove.clicked.connect(drop)
        add = W.button(m, "Add series", icons.native(icons.ADD),
                       "Another series: a bar or a line, on the left or the right axis")
        holder = QWidget()
        al = QHBoxLayout(holder)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(add)
        al.addStretch(1)
        self.lay.addWidget(holder)
        note = self._note("An axis that holds a bar series starts at 0.")

        def add_series():
            series.append({"field": None, "agg": "count", "kind": "line", "axis": "right"})
            self._changed(True)
            self._rebuild()

        add.clicked.connect(add_series)
        follow = self._binning(d, (("zero", "Treat as zero"), ("null", "Treat as null")))

        def changed_x(*_a):
            d["x"] = x.current_name()
            follow()
            self._changed(True)

        x.currentIndexChanged.connect(changed_x)
        del note

    # ---- Data: pie -----------------------------------------------------
    def _pie_data(self):
        d = self.plot["data"]
        self._header("Data")
        mode = self._combo((("category", "Categories of a field"),
                            ("fields", "Number fields")), d.get("mode", "category"))
        self._row("Slices", mode, "The categories of one field (records counted, or a "
                  "number field summarised), or one slice per number field")
        if d.get("mode", "category") == "fields":
            names = list(d.get("fields") or [])
            first, extra, add = self._y_rows(names, tip="Each number field is a slice",
                                             add_text="Add field", label="Field")
            self._wire_fields(d, "fields", first, extra, add)
            agg = self._combo([a for a in P.AGGREGATIONS if a[0] != "count"],
                              d.get("agg", "sum"))
            self._row("Aggregate", agg, "Aggregation of each field")
        else:
            cat = self._field_combo(d.get("category"), _is_category, none_label=CHOOSE)
            self._row("Category", cat, "One slice per category")
            value = self._field_combo(d.get("field"), _is_number,
                                      none_label="Count of records")
            self._row("Value", value, "Count of records, or a number field summarised "
                      "by the aggregation")
            agg = self._combo([a for a in P.AGGREGATIONS if a[0] != "count"],
                              d.get("agg", "sum"))
            agg_row = self._row("Aggregate", agg, "Aggregation of the value field")
            agg_row.setVisible(bool(d.get("field")))

            def changed_cat(*_a):
                d["category"] = cat.current_name()
                d["field"] = value.current_name()
                agg_row.setVisible(bool(d["field"]))
                self._changed(True)

            cat.currentIndexChanged.connect(changed_cat)
            value.currentIndexChanged.connect(changed_cat)
        threshold = QSpinBox()
        threshold.setRange(0, 50)
        threshold.setSuffix(" %")
        threshold.setValue(int(d.get("threshold", 0) or 0))
        threshold.setFixedHeight(self.metrics.control_h)
        self._row("Threshold", threshold, "Grouping threshold: slices under this share of "
                  "the whole are put together as Other (0: every slice shown)")
        donut = QSpinBox()
        donut.setRange(0, 90)
        donut.setSuffix(" %")
        donut.setValue(int(d.get("donut", 0) or 0))
        donut.setFixedHeight(self.metrics.control_h)
        self._row("Donut hole", donut, "The hole as a share of the pie (0: a full pie)")
        self._note("A slice must be above 0 to be drawn.")

        def changed_mode(*_a):
            d["mode"] = mode.currentData()
            self._changed(True)
            self._rebuild()

        def other(*_a):
            d["agg"] = agg.currentData()
            d["threshold"] = threshold.value()
            d["donut"] = donut.value()
            self._changed(True)

        mode.currentIndexChanged.connect(changed_mode)
        agg.currentIndexChanged.connect(other)
        threshold.valueChanged.connect(other)
        donut.valueChanged.connect(other)

    # ---- Data: histogram -----------------------------------------------
    def _histogram_data(self):
        d = self.plot["data"]
        m = self.metrics
        self._header("Data")
        x = self._field_combo(d.get("x"), _is_number, none_label=CHOOSE)
        self._row("X-axis", x, "The number field whose distribution is shown")
        bins = QSpinBox()
        bins.setRange(0, P.MAX_HIST_BINS)
        bins.setSpecialValueText("Auto")
        bins.setValue(int(d.get("bins") or 0))
        bins.setFixedHeight(m.control_h)
        self._row("Bins", bins, "Auto: the square root of the number of records (at most "
                  "64), as ArcGIS Pro")
        transform = self._combo([(k, text, icons.option_icon(k, self.theme))
                                 for k, text in P.TRANSFORMS], d.get("transform", "none"))
        self._row("Transform", transform, "Transformation: preview the field made more "
                  "normal: "
                  "Logarithmic (natural logarithm; values of 0 or less left out) or Square "
                  "root (values below 0 left out)")
        split = self._field_combo(d.get("split"), _is_split, none_label="None")
        self._row("Split by", split, "One small histogram per category (on the same bins)")
        per_row = QSpinBox()
        per_row.setRange(1, 6)
        per_row.setValue(int(d.get("per_row", 3) or 3))
        per_row.setFixedHeight(m.control_h)
        per_row_row = self._row("Per row", per_row, "Mini charts per row")
        preview = self._tick("Show preview chart", d.get("preview"),
                             "A larger view of one category's histogram; click a small "
                             "histogram's title to show it")

        self._header("Statistics on the chart")
        fmt = self.plot.setdefault("format", {})
        ticks = {}
        for key, text, colour_key, default in (
                ("mean", "Mean", "mean_colour", True),
                ("median", "Median", "median_colour", True),
                ("std", "± 1 Std. Dev.", "std_colour", False),
                ("normal", "Normal Distribution Curve", None, False)):
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(m.gap)
            box = QCheckBox(text)
            box.setChecked(bool(d.get(key, default)))
            hl.addWidget(box, 1)
            if colour_key:
                from .plot_view import MEAN_COLOUR, MEDIAN_COLOUR, STD_COLOUR
                base = {"mean_colour": MEAN_COLOUR, "median_colour": MEDIAN_COLOUR,
                        "std_colour": STD_COLOUR}[colour_key]
                btn = QgsColorButton()
                btn.setFixedHeight(m.control_h)
                btn.setFixedWidth(64)
                btn.setShowNull(False)
                btn.setDefaultColor(QColor(base))
                btn.setColor(QColor(fmt.get(colour_key) or base))

                def recolour(colour, colour_key=colour_key):
                    fmt[colour_key] = colour.name()
                    self._changed(False)

                btn.colorChanged.connect(recolour)
                hl.addWidget(btn)
            self.lay.addWidget(holder)
            ticks[key] = box

        def follow():
            on = bool(d.get("split"))
            per_row_row.setVisible(on)
            preview.setVisible(on)

        follow()

        def changed(*_a):
            d["x"] = x.current_name()
            d["split"] = split.current_name()
            follow()
            self._changed(True)

        def other(*_a):
            d["bins"] = bins.value() or None
            d["transform"] = transform.currentData()
            d["per_row"] = per_row.value()
            d["preview"] = preview.isChecked()
            for key, box in ticks.items():
                d[key] = box.isChecked()
            self._changed(True)

        x.currentIndexChanged.connect(changed)
        split.currentIndexChanged.connect(changed)
        bins.valueChanged.connect(other)
        per_row.valueChanged.connect(other)
        transform.currentIndexChanged.connect(other)
        preview.toggled.connect(other)
        for box in ticks.values():
            box.toggled.connect(other)

    # ---- Data: box plot ------------------------------------------------
    def _box_data(self):
        d = self.plot["data"]
        self._header("Data")
        split = self._field_combo(d.get("split"), _is_split, none_label="None")
        self._row("X-axis", split, "A category field: one box per category (None: one box "
                  "per field)")
        names = list(d.get("fields") or [])
        first, extra, add = self._y_rows(names, tip="The number field shown as boxes; "
                                         "each further field is a box of its own")
        self._wire_fields(d, "fields", first, extra, add)
        standardize = self._tick("Standardize values (z-score)", d.get("standardize", True),
                                 "With several fields, their values become z-scores so "
                                 "they share one axis")
        standardize.setEnabled(len(names) > 1)
        outliers = self._tick("Show outliers", d.get("outliers", True),
                              "Values more than 1.5 × the interquartile range beyond the "
                              "box as points; off, the whiskers reach the minimum and "
                              "maximum")

        def changed(*_a):
            d["split"] = split.current_name()
            d["standardize"] = standardize.isChecked()
            d["outliers"] = outliers.isChecked()
            self._changed(True)

        split.currentIndexChanged.connect(changed)
        standardize.toggled.connect(changed)
        outliers.toggled.connect(changed)

    # ---- shared: value and classification (heat charts) -----------------
    def _value_rows(self, d):
        value = self._field_combo(d.get("field"), _is_number, none_label="Count of records")
        self._row("Value", value, "Count of records, or a number field summarised by the "
                  "aggregation, colours each cell")
        agg = self._combo([a for a in P.AGGREGATIONS if a[0] != "count"],
                          d.get("agg", "sum"))
        agg_row = self._row("Aggregate", agg, "Aggregation of the value field")
        agg_row.setVisible(bool(d.get("field")))

        def changed(*_a):
            d["field"] = value.current_name()
            d["agg"] = agg.currentData()
            agg_row.setVisible(bool(d["field"]))
            self._changed(True)

        value.currentIndexChanged.connect(changed)
        agg.currentIndexChanged.connect(changed)

    def _classification(self, d):
        from qgis.core import QgsStyle, QgsSymbolLayerUtils
        self._header("Classification")
        method = self._combo(P.CLASSIFICATIONS, d.get("classify", "natural"))
        self._row("Method", method, "How the cell values are cut into colour classes")
        classes = QSpinBox()
        classes.setRange(2, 10)
        classes.setValue(int(d.get("classes", 5) or 5))
        classes.setFixedHeight(self.metrics.control_h)
        self._row("Classes", classes)
        ramp = QComboBox()
        ramp.setFixedHeight(self.metrics.control_h)
        ramp.setIconSize(QSize(64, 12))
        style = QgsStyle.defaultStyle()
        for name in style.colorRampNames():
            r = style.colorRamp(name)
            icon = QgsSymbolLayerUtils.colorRampPreviewIcon(r, QSize(64, 12)) \
                if r is not None else QIcon()
            ramp.addItem(icon, name, name)
        i = ramp.findData(d.get("ramp", "Viridis"))
        ramp.setCurrentIndex(i if i >= 0 else 0)
        self._row("Ramp", ramp, "Colour ramp: a QGIS colour ramp; the first class takes "
                  "its start")

        def changed(*_a):
            d["classify"] = method.currentData()
            d["classes"] = classes.value()
            d["ramp"] = ramp.currentData()
            self._changed(False)

        method.currentIndexChanged.connect(changed)
        classes.valueChanged.connect(changed)
        ramp.currentIndexChanged.connect(changed)

    # ---- Data: scatter plot matrix -------------------------------------
    def _matrix_data(self):
        d = self.plot["data"]
        self._header("Data")
        first, extra, add = self._y_rows(list(d.get("fields") or []),
                                         tip="Every pair of these number fields is plotted",
                                         add_text="Add field", label="Field")
        self._wire_fields(d, "fields", first, extra, add)
        self._header("Matrix layout")
        lower = self._combo(P.MATRIX_LOWER, d.get("lower", "r2"))
        self._row("Lower left", lower, "R² values (shaded by the strength of the "
                  "relationship), scatter plots or nothing")
        upper = self._combo(P.MATRIX_UPPER, d.get("upper", "scatter"))
        self._row("Upper right", upper, "Scatter plots, a preview plot (a larger view of "
                  "the cell clicked) or nothing")
        diag = self._combo(P.MATRIX_DIAGONAL, d.get("diagonal", "histogram"))
        self._row("Diagonal", diag, "Each field's histogram, or its name")
        trend = self._tick("Trend line", d.get("trend"), "A linear trend in each small plot")

        def changed(*_a):
            d["lower"] = lower.currentData()
            d["upper"] = upper.currentData()
            d["diagonal"] = diag.currentData()
            d["trend"] = trend.isChecked()
            self._changed(True)

        for w in (lower, upper, diag):
            w.currentIndexChanged.connect(changed)
        trend.toggled.connect(changed)

    # ---- Data: QQ plot -------------------------------------------------
    def _qq_data(self):
        d = self.plot["data"]
        self._header("Data")
        x = self._field_combo(d.get("x"), _is_number, none_label=CHOOSE)
        self._row("Field", x, "The number field whose distribution is checked")
        y = self._field_combo(d.get("y"), _is_number, none_label="Normal distribution")
        self._row("Compare", y, "Compare with the normal distribution, or a second number "
                  "field "
                  "(the X axis then shows the first field, the Y axis the second)")
        transform = self._combo([(k, text, icons.option_icon(k, self.theme))
                                 for k, text in P.QQ_TRANSFORMS], d.get("transform", "none"))
        self._row("Transform", transform, "Transformation: Logarithmic (natural; above 0), "
                  "Square "
                  "root (0 and above), Inverse (1 / value; not 0) or Box-Cox (above 0, its "
                  "lambda worked out)")
        self._header("Statistics on the chart")
        ref = self._tick("Reference line", d.get("reference", True),
                         "The line through the first and third quartiles: the points "
                         "follow it when the distributions agree")

        def changed(*_a):
            d["x"] = x.current_name()
            d["y"] = y.current_name()
            d["transform"] = transform.currentData()
            d["reference"] = ref.isChecked()
            self._changed(True)

        for w in (x, y, transform):
            w.currentIndexChanged.connect(changed)
        ref.toggled.connect(changed)

    # ---- Data: matrix heat chart ---------------------------------------
    def _heat_data(self):
        d = self.plot["data"]
        self._header("Data")
        accept = lambda k: k in (F.TEXT, F.BOOL, F.INTEGER) or P.role(k) == P.DATE  # noqa
        x = self._field_combo(d.get("x"), accept, none_label=CHOOSE)
        self._row("X-axis", x, "The columns: a text, whole number or date field (dates "
                  "grouped in time intervals)")
        y = self._field_combo(d.get("y"), accept, none_label=CHOOSE)
        self._row("Y-axis", y, "The rows: a text, whole number or date field")
        self._value_rows(d)
        follow_x = self._binning(d, None, prefix="x_", field_key="x",
                                 title="Time binning (X-axis)")
        follow_y = self._binning(d, None, prefix="y_", field_key="y",
                                 title="Time binning (Y-axis)")
        self._classification(d)

        def changed(*_a):
            d["x"] = x.current_name()
            d["y"] = y.current_name()
            follow_x()
            follow_y()
            self._changed(True)

        x.currentIndexChanged.connect(changed)
        y.currentIndexChanged.connect(changed)

    # ---- Data: calendar heat chart -------------------------------------
    def _calendar_data(self):
        d = self.plot["data"]
        self._header("Data")
        x = self._field_combo(d.get("x"), lambda k: P.role(k) == P.DATE, none_label=CHOOSE)
        self._row("Date", x, "A date or date-time field")
        kind = self._combo(P.CALENDAR_TYPES, d.get("calendar", "year"))
        self._row("Calendar", kind, "Calendar type")
        view = self._combo(P.CALENDAR_VIEWS, d.get("view", "single"))
        self._row("View", view, "Single: every year (or week) pooled into one grid; "
                  "Sequential: one grid per year (or week)")
        leap = self._tick("Include leap day", d.get("leap", True),
                          "Off: the records of 29 February are left out")
        leap.setVisible(d.get("calendar", "year") == "year")
        self._value_rows(d)
        self._classification(d)

        def changed(*_a):
            d["x"] = x.current_name()
            d["calendar"] = kind.currentData()
            d["view"] = view.currentData()
            d["leap"] = leap.isChecked()
            leap.setVisible(d["calendar"] == "year")
            self._changed(True)

        for w in (x, kind, view):
            w.currentIndexChanged.connect(changed)
        leap.toggled.connect(changed)

    # ---- Data: data clock ----------------------------------------------
    def _clock_data(self):
        d = self.plot["data"]
        self._header("Data")
        x = self._field_combo(d.get("x"), lambda k: P.role(k) == P.DATE, none_label=CHOOSE)
        self._row("Date", x, "A date or date-time field")
        rings = self._combo(P.CLOCK_RINGS, d.get("rings", "years_months"))
        self._row("Rings", rings, "Rings and wedges: each ring a longer period (oldest "
                  "inside), "
                  "cut into wedges of a shorter one from 12 o'clock")
        self._value_rows(d)
        self._classification(d)

        def changed(*_a):
            d["x"] = x.current_name()
            d["rings"] = rings.currentData()
            self._changed(True)

        x.currentIndexChanged.connect(changed)
        rings.currentIndexChanged.connect(changed)

    # ---- Axes ----------------------------------------------------------
    def _axes(self):
        axes = self.plot["axes"]
        t = self.plot["type"]
        self._header("Axes")
        edits = {}
        keys = (["x_min", "x_max"] if t in ("scatter", "line", "histogram", "qq") else []) + \
            ["y_min", "y_max"]
        if t == "combo" and any(s.get("axis") == "right"
                                for s in self.plot["data"].get("series") or []):
            keys += ["y2_min", "y2_max"]
        labels = {"x_min": "X minimum", "x_max": "X maximum", "y_min": "Y minimum",
                  "y_max": "Y maximum", "y2_min": "Right min", "y2_max": "Right max"}
        for key in keys:
            e = self._edit("" if axes.get(key) is None else str(axes.get(key)), "Automatic")
            self._row(labels[key], e, "Empty: from the data (dates as YYYY-MM-DD)")
            edits[key] = e
        x_log = None
        if t in ("scatter", "line"):
            x_log = self._tick("Logarithmic X axis", axes.get("x_log"),
                               "Values of 0 or less cannot be shown on a log axis")
        y_log = self._tick("Logarithmic Y axis", axes.get("y_log"),
                           "Values of 0 or less cannot be shown on a log axis")
        y_log.setVisible(t != "qq")

        def changed(*_a):
            for key, e in edits.items():
                axes[key] = e.text().strip() or None
            if x_log is not None:
                axes["x_log"] = x_log.isChecked()
            axes["y_log"] = y_log.isChecked()
            self._changed(False)

        for e in edits.values():
            e.editingFinished.connect(changed)
        for box in (x_log, y_log):
            if box is not None:
                box.toggled.connect(changed)

    # ---- Guides --------------------------------------------------------
    def _guides(self):
        guides = self.plot.setdefault("guides", [])
        t = self.plot["type"]
        m = self.metrics
        self._header("Guides")
        rows = QWidget()
        rl = QVBoxLayout(rows)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        self.lay.addWidget(rows)

        def describe(g):
            where = "Vertical" if g.get("axis") == "vertical" else "Horizontal"
            text = f"{where} at {g.get('value')}"
            if g.get("to"):
                text += f" to {g.get('to')}"
            if g.get("label"):
                text += f"  ({g['label']})"
            return text

        def refill():
            while rl.count():
                item = rl.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            if not guides:
                lbl = QLabel("None")
                lbl.setObjectName("gtsDim")
                rl.addWidget(lbl)
            for k, g in enumerate(guides):
                holder = QWidget()
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(m.gap)
                text = W.ElideLabel(describe(g))
                text.setToolTip(describe(g))
                hl.addWidget(text, 1)
                remove = W.square_button(m, icons.native(icons.REMOVE), "Remove this guide")
                remove.clicked.connect(lambda _c=False, k=k: drop(k))
                hl.addWidget(remove)
                rl.addWidget(holder)

        def drop(k):
            if 0 <= k < len(guides):
                del guides[k]
                refill()
                self._changed(False)

        refill()
        orient = self._combo((("horizontal", "Horizontal"), ("vertical", "Vertical")),
                             "horizontal")
        if t in ("bar", "combo", "box"):            # categories along X
            orient.setEnabled(False)
        value = self._edit("", "A number, or a date as YYYY-MM-DD")
        to = self._edit("", "Optional: a second value makes a range")
        label = self._edit("", "Optional text by the guide")
        self._row("Direction", orient)
        self._row("Value", value)
        self._row("To", to)
        self._row("Label", label)
        add = W.button(m, "Add guide", icons.native(icons.ADD),
                       "Add a line (or, with To, a range) at this value")
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(add)
        hl.addStretch(1)
        self.lay.addWidget(holder)

        def do_add():
            if not value.text().strip():
                value.setFocus()
                return
            guides.append({"axis": orient.currentData(), "value": value.text().strip(),
                           "to": to.text().strip() or None, "label": label.text().strip()})
            for e in (value, to, label):
                e.clear()
            refill()
            self._changed(False)

        add.clicked.connect(do_add)

    # ---- Summary -------------------------------------------------------
    def _summary(self):
        self._header("Summary")
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)
        self.lay.addWidget(box)
        self.summary_grid = grid
        self._fill_summary()

    def set_summary(self, rows):
        """(label, value, tip) rows of the Summary section."""
        self.summary_rows = list(rows)
        if self.summary_grid is not None:
            try:
                self._fill_summary()
            except RuntimeError:
                pass

    def _fill_summary(self):
        grid = self.summary_grid
        while grid.count():
            item = grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for r, row in enumerate(self.summary_rows):
            label, value = row[0], row[1]
            tip = row[2] if len(row) > 2 else ""
            a = QLabel(label)
            a.setObjectName("gtsDim")
            if tip:
                a.setToolTip(tip)
            b = QLabel(value)
            b.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            b.setToolTip(value)
            grid.addWidget(a, r, 0)
            grid.addWidget(b, r, 1)
        grid.setColumnStretch(1, 1)

    # ---- Appearance ----------------------------------------------------
    def _appearance(self):
        gen = self.plot.setdefault("general", {})
        fmt = self.plot.setdefault("format", {})
        self._header("Appearance")
        axes = self.plot["type"] in AXES_TYPES + ("heat",)
        title = self._edit(gen.get("title"), P.default_title(self.plot))
        self._row("Title", title)
        x_title = self._edit(gen.get("x_title"), "From the field")
        self._row("X title", x_title, "X axis title").setVisible(axes)
        y_title = self._edit(gen.get("y_title"), "From the field")
        self._row("Y title", y_title, "Y axis title").setVisible(axes)
        desc = self._edit(gen.get("description"), "Shown under the plot")
        self._row("Description", desc)
        if self.plot["type"] == "scatter" and len(P.scatter_fields(self.plot["data"])) < 2:
            mode = self._combo((("layer", "Layer symbology"), ("single", "One colour")),
                               self.plot["data"].get("colour", "layer"))
            self._row("Points", mode, "Layer symbology: each point in the colour of its "
                      "legend class, as on the map (with several Y fields each field has "
                      "its own colour)")

            def changed_mode(*_a):
                self.plot["data"]["colour"] = mode.currentData()
                self._fill_colours()
                self._changed(True)

            mode.currentIndexChanged.connect(changed_mode)
            fmt = self.plot.setdefault("format", {})
            shape = self._combo([(k, text, marker_icon(k, self.theme.text))
                                 for k, text in POINT_SHAPES], fmt.get("shape", "circle"))
            self._row("Shape", shape, "The points' shape")

            def changed_shape(*_a):
                fmt["shape"] = shape.currentData()
                self._changed(False)

            shape.currentIndexChanged.connect(changed_shape)
        colours = QWidget()
        self.colour_lay = QVBoxLayout(colours)
        self.colour_lay.setContentsMargins(0, 0, 0, 0)
        self.colour_lay.setSpacing(6)
        self.lay.addWidget(colours)
        self._fill_colours()
        legend = self._combo((("auto", "Automatic"), ("right", "Right of the plot"),
                              ("bottom", "Under the plot"), ("off", "Hidden")),
                             "off" if not gen.get("legend", True)
                             else gen.get("legend_pos", "auto"))
        self._row("Legend", legend, "Automatic: inside the plot, or beside it when it "
                  "lists series (as in Visualize Statistics)")
        grid = self._tick("Grid lines", fmt.get("grid", True))
        axis = self._tick("Axis lines", fmt.get("axis_lines", True))
        grid.setVisible(axes and self.plot["type"] != "heat")
        axis.setVisible(axes)
        self.title_edit = title

        def changed(*_a):
            gen["title"] = title.text().strip()
            gen["x_title"] = x_title.text().strip()
            gen["y_title"] = y_title.text().strip()
            gen["description"] = desc.text().strip()
            gen["legend"] = legend.currentData() != "off"
            if legend.currentData() != "off":
                gen["legend_pos"] = legend.currentData()
            fmt["grid"] = grid.isChecked()
            fmt["axis_lines"] = axis.isChecked()
            self._changed(False)

        for e in (title, x_title, y_title, desc):
            e.editingFinished.connect(changed)
        legend.currentIndexChanged.connect(changed)
        grid.toggled.connect(changed)
        axis.toggled.connect(changed)

    # ---- Edit plot: the card of the part selected -------------------------
    def show_edit(self, part, plot, on_change):
        """The card while the plot is edited: the settings of the part of
        the canvas selected (*part*, from the plot view), or how to pick
        one.  Changes go into *plot*'s canvas; *on_change(base, tag)* is
        called after each (*base*: the plot is drawn again, not only its
        objects)."""
        m = self.metrics
        self.plot = None                         # the usual settings are frozen
        self._quiet = True
        self._card()
        self._labels = []
        self.title_edit = self.colour_lay = self.summary_grid = self.layer_combo = None
        self._layer_pending = False
        self.edit_part = part
        canvas = PE.canvas_of(plot)
        t = self.theme
        if part is None:
            self._header("Edit plot")
            self._note("Click a part of the plot to change its look: the canvas, the grid "
                       "lines, a series, a trend or reference line, a guide, or an object "
                       "added with Add.")
            self._note("Add puts a text box, a horizontal or vertical line, an arrow or a "
                       "picture on the plot. Drag an object to move it; Delete removes it.")
            self._note("Titles, axes, the legend, colours and point shapes are set outside "
                       "Edit plot: turn it off to change them.")
            self.lay.addStretch(1)
            self._label_width = W.align_labels(self._labels)
            self._quiet = False
            return
        self._header(PE.describe(part))
        kind = part.get("kind")
        if kind == "object":
            target = next((o for o in canvas["objects"] if o.get("id") == part.get("id")), {})
            tag_base, base = part.get("id"), False
        else:
            key = part.get("key")
            tag_base, base = key, True

            def target_style(k=key):
                return canvas["style"].setdefault(k, {})
            target = dict(canvas["style"].get(key) or {})

        def get(name, default=None, store=None):
            if kind == "object":
                return target.get(name, default)
            st = canvas["style"].get(store or part.get("key")) or {}
            return st.get(name, default)

        def put(name, value, store=None):
            if kind == "object":
                target[name] = value
            else:
                canvas["style"].setdefault(store or part.get("key"), {})[name] = value
            if not self._quiet:
                on_change(base, f"{tag_base}:{name}")

        def colour_row(label, name, default, null_text=None, tip="", store=None):
            btn = QgsColorButton()
            btn.setFixedHeight(m.control_h)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setAllowOpacity(False)
            btn.setDefaultColor(QColor(default))
            btn.setShowNull(bool(null_text), null_text or "")
            current = get(name, None, store)
            if current:
                btn.setColor(QColor(current))
            elif null_text and current == "":
                btn.setToNull()
            elif null_text and current is None and not default:
                btn.setToNull()
            else:
                btn.setColor(QColor(default))
            btn.colorChanged.connect(
                lambda c: put(name, c.name() if c.isValid() else "", store))
            self._row(label, btn, tip)
            return btn

        def number_row(label, name, default, lo, hi, step=0.5, decimals=1, suffix="",
                       tip="", store=None):
            box = QDoubleSpinBox()
            box.setRange(lo, hi)
            box.setDecimals(decimals)
            box.setSingleStep(step)
            box.setSuffix(suffix)
            box.setFixedHeight(m.control_h)
            box.setMinimumWidth(50)
            box.setValue(float(get(name, default, store)))
            box.valueChanged.connect(lambda v: put(name, float(v), store))
            self._row(label, box, tip)
            return box

        def transparency_row(name="alpha", label="Transparency", store=None):
            box = QSpinBox()
            box.setRange(0, 100)
            box.setSuffix(" %")
            box.setFixedHeight(m.control_h)
            box.setValue(100 - int(round(float(get(name, 100, store)))))
            box.valueChanged.connect(lambda v: put(name, 100 - int(v), store))
            self._row(label, box, "0 %: solid; 100 %: invisible")
            return box

        def dash_row(name="dash", default="solid", label="Dash", store=None):
            box = self._combo([(k, text, PE.dash_icon(k, t.text)) for k, text in PE.DASHES],
                              get(name, default, store))
            box.setIconSize(QSize(32, 16))
            box.currentIndexChanged.connect(lambda _i: put(name, box.currentData(), store))
            self._row(label, box)
            return box

        def choice_row(label, name, items, default):
            box = self._combo(items, get(name, default))
            box.currentIndexChanged.connect(lambda _i: put(name, box.currentData()))
            self._row(label, box)
            return box

        def tick(text, name, default, store=None):
            box = self._tick(text, get(name, default, store))
            box.toggled.connect(lambda on: put(name, bool(on), store))
            return box

        if kind == "background":
            colour_row("Colour", "colour", "", null_text="No fill",
                       tip="The canvas behind the data")
            transparency_row()
        elif kind in ("grid_h", "grid_v"):
            colour_row("Colour", "colour", t.border_soft)
            number_row("Width", "width", 1.0, 0.2, 10, 0.2, 1, " px")
            dash_row()
            self._note("Grid lines in Appearance shows or hides them.")
        elif kind == "series" and part.get("what") == "line":
            number_row("Width", "width", 2.0, 0.2, 12, 0.2, 1, " px")
            dash_row()
            tick("Point markers", "markers", True)
            self._note("Its colour is set in Appearance.")
        elif kind == "series" and part.get("what") == "bar":
            colour_row("Outline", "outline", "", null_text="No outline")
            number_row("Outline width", "outline_width", 1.0, 0.2, 8, 0.2, 1, " px")
            transparency_row()
            if self_type(plot) in ("bar", "combo"):
                box = QSpinBox()
                box.setRange(10, 100)
                box.setSuffix(" %")
                box.setFixedHeight(m.control_h)
                box.setValue(int(get("bar_width", 82, "bars")))
                box.valueChanged.connect(lambda v: put("bar_width", int(v), "bars"))
                self._row("Bar width", box, "The share of each category's space the bars "
                          "take (every series)")
            self._note("Its colour is set in Appearance.")
        elif kind == "series" and part.get("what") == "points":
            number_row("Size", "point_size", 7.0, 1, 60, 0.5, 1, " px",
                       "The points' size (Size by in Data sizes them instead)")
            transparency_row()
            colour_row("Outline", "outline", "", null_text="Automatic",
                       tip="Automatic: a darker shade of the point")
            box = self._tick("No outline", get("outline") == "")
            box.toggled.connect(lambda on: put("outline", "" if on else None))
            self._note("Its colour and shape are set in Appearance.")
        elif kind == "slices":
            colour_row("Outline", "outline", t.base)
            number_row("Outline width", "outline_width", 1.5, 0, 8, 0.5, 1, " px",
                       "0: no outline")
            transparency_row()
            self._note("The slices' colours are set in Appearance.")
        elif kind in ("line", "guide"):
            key = part.get("key", "")
            fixed_colour = key in ("line:mean", "line:median", "line:std")
            width = {"line:normal": 1.8, "line:reference": 1.8}.get(key, 1.6)
            if key.startswith("line:trend"):
                width = 2.0
            dash = "dot" if key == "line:std" else ("dash" if fixed_colour or kind == "guide"
                                                    else "solid")
            if not fixed_colour:
                default = t.danger if kind == "guide" or key == "line:reference" \
                    else ("#ECEFF1" if t.dark else "#37474F")
                colour_row("Colour", "colour", default)
            number_row("Width", "width", width, 0.2, 12, 0.2, 1, " px")
            dash_row(default=dash)
            if fixed_colour:
                self._note("Its colour is set beside its tick under Statistics on the "
                           "chart.")
            elif kind == "guide":
                self._note("Its value and label are set under Guides.")
        elif kind == "object":
            self._object_rows(target, put, get, colour_row, number_row, transparency_row,
                              dash_row, choice_row, on_change)
        self.lay.addStretch(1)
        self._label_width = W.align_labels(self._labels)
        self._quiet = False

    def _object_rows(self, obj, put, get, colour_row, number_row, transparency_row,
                     dash_row, choice_row, on_change):
        """The settings of an added object (Edit plot)."""
        m, t = self.metrics, self.theme
        k = obj.get("kind")
        if k == "text":
            edit = QPlainTextEdit(obj.get("text", ""))
            edit.setFixedHeight(m.control_h * 3)
            edit.textChanged.connect(lambda: put("text", edit.toPlainText()))
            self._row("Text", edit)
            fonts = QFontComboBox()
            fonts.setFixedHeight(m.control_h)
            # long font names never widen the card: the box shrinks, its list does not
            fonts.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            fonts.setMinimumContentsLength(6)
            fonts.setMinimumWidth(80)
            if obj.get("font"):
                fonts.setCurrentFont(QFont(obj["font"]))
            fonts.currentFontChanged.connect(lambda f: put("font", f.family()))
            self._row("Font", fonts)
            number_row("Size", "size", 10.0, 4, 96, 1, 1, " pt")
            colour_row("Colour", "colour", t.text_strong)
            styles = QWidget()                     # B, I, U toggles, as in a text editor
            sl = QHBoxLayout(styles)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(m.gap)
            for name, letter, tip in (("bold", "B", "Bold"), ("italic", "I", "Italic"),
                                      ("underline", "U", "Underline")):
                btn = QToolButton()
                btn.setText(letter)
                btn.setToolTip(tip)
                btn.setCheckable(True)
                btn.setChecked(bool(obj.get(name)))
                btn.setFixedSize(m.control_h, m.control_h)
                f = btn.font()
                f.setBold(name == "bold")
                f.setItalic(name == "italic")
                f.setUnderline(name == "underline")
                btn.setFont(f)
                btn.toggled.connect(lambda on, n=name: put(n, bool(on)))
                sl.addWidget(btn)
            sl.addStretch(1)
            self._row("Style", styles)
            choice_row("Align", "align", PE.ALIGNS, "left")
            self.lay.addWidget(W.section_header("Box"))
            colour_row("Fill", "fill", t.base, null_text="No fill")
            transparency_row("fill_alpha", "Transparency")
            colour_row("Border", "border", t.border, null_text="No border")
            number_row("Border width", "border_width", 1.0, 0, 10, 0.5, 1, " px")
            dash_row("border_dash", "solid", "Border dash")
            number_row("Corners", "radius", 4.0, 0, 40, 1, 0, " px", "Rounded corners")
            number_row("Padding", "padding", 6.0, 0, 40, 1, 0, " px",
                       "Room between the border and the text")
            self._note("Drag the box to move it, its right edge to set its width.")
        elif k in ("hline", "vline"):
            colour_row("Colour", "colour", t.text_strong)
            number_row("Width", "width", 1.6, 0.2, 12, 0.2, 1, " px")
            dash_row()
            transparency_row()
            self._note("It crosses the whole plot; drag it to move it.")
        elif k == "arrow":
            colour_row("Colour", "colour", t.text_strong)
            number_row("Width", "width", 2.0, 0.2, 12, 0.2, 1, " px")
            dash_row()
            choice_row("Head", "head", PE.HEADS, "end")
            number_row("Head size", "head_size", 10.0, 4, 40, 1, 0, " px")
            transparency_row()
            self._note("Drag the arrow to move it, its ends to point it.")
        elif k == "picture":
            transparency_row()
            colour_row("Border", "border", t.border, null_text="No border")
            number_row("Border width", "border_width", 1.0, 0, 10, 0.5, 1, " px")
            replace = W.button(m, "Replace picture…", icons.native(icons.OPEN),
                               "Choose another picture file")

            def choose():
                path, _f = QFileDialog.getOpenFileName(
                    self, "Picture", "", "Pictures (*.png *.jpg *.jpeg *.svg *.bmp *.gif)")
                if not path:
                    return
                data, error = PE.picture_data(path)
                if data is None:
                    QMessageBox.warning(self, "Picture", error)
                    return
                obj.update(data)
                on_change(False, f"{obj.get('id')}:picture")

            replace.clicked.connect(choose)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(replace)
            hl.addStretch(1)
            self.lay.addWidget(holder)
            self._note("Drag the picture to move it, its corner to size it.")

    def set_series(self, names):
        """The series of the last build, for the colour rows."""
        if names == self.series:
            return
        self.series = list(names)
        if self.plot is not None and self.colour_lay is not None:
            try:
                self._fill_colours()
            except RuntimeError:
                pass

    def _fill_colours(self):
        lay = self.colour_lay
        while lay.count():
            item = lay.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        fmt = self.plot.setdefault("format", {})
        colours = fmt.setdefault("colours", {})
        t = self.plot["type"]
        if t == "scatter" and len(P.scatter_fields(self.plot["data"])) > 1:
            names = self.series or []                # a colour per Y field
        elif t == "scatter":
            if self.plot["data"].get("colour", "layer") == "layer":
                return
            names = [None]
        else:
            names = self.series or []
        for s, name in enumerate(names):
            btn = QgsColorButton()
            btn.setFixedHeight(self.metrics.control_h)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setShowNull(False)
            default = series_colour(self.theme, s, name, len(names)) if name is not None \
                else QColor(self.theme.bar)
            btn.setDefaultColor(default)
            key = fmt.get("colour") if name is None else colours.get(str(name))
            btn.setColor(QColor(key) if key else default)

            def changed(colour, name=name):
                if name is None:
                    fmt["colour"] = colour.name()
                else:
                    colours[str(name)] = colour.name()
                self._changed(False)

            btn.colorChanged.connect(changed)
            if name is None:
                text = "Point colour" if t in ("scatter", "qq", "matrix") else "Bar colour"
            elif len(names) == 1:
                text = "Line colour" if t == "line" else "Bar colour"
            else:
                text = str(name)
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(self.metrics.gap)
            shape_btn = self._shape_button(fmt, name) \
                if t == "scatter" and name is not None else None
            lbl = W.ElideLabel(text)               # a series name: in the column, elided
            lbl.setObjectName("gtsDim")
            lbl.setToolTip(text)
            width = getattr(self, "_label_width", 0) or self.metrics.fm.horizontalAdvance(
                "Description") + 4
            lbl.setFixedWidth(width)
            # an elided label is ignored by the row's layout: fixed, it keeps its column
            lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            row.addWidget(lbl)
            row.addWidget(btn, 1)
            if shape_btn is not None:              # a Y field's point shape, beside its colour
                row.addWidget(shape_btn)
            lay.addWidget(holder)

    def _shape_button(self, fmt, name):
        """A Y field's point shape: a square button showing it, its menu the
        shapes."""
        shapes = fmt.setdefault("shapes", {})
        menu = QMenu(self)
        btn = W.menu_square(self.metrics, marker_icon(shapes.get(str(name), "circle"),
                                                      self.theme.text),
                            f"Point shape of {name}: "
                            f"{SHAPE_LABELS.get(shapes.get(str(name), 'circle'))}", menu)
        for key, text in POINT_SHAPES:
            def choose(_c=False, key=key, text=text):
                shapes[str(name)] = key
                btn.setIcon(marker_icon(key, self.theme.text))
                btn.setToolTip(f"Point shape of {name}: {text}")
                self._changed(False)
            menu.addAction(marker_icon(key, self.theme.text), text).triggered.connect(choose)
        return btn

    def refresh_title_hint(self):
        if self.title_edit is not None and self.plot is not None:
            try:
                self.title_edit.setPlaceholderText(P.default_title(self.plot))
            except RuntimeError:
                pass


# ============================================================================
# THE WINDOW
# ============================================================================

def ramp_colours(name, k):
    """*k* colours along the QGIS colour ramp *name* (the default style's),
    lightest class first."""
    from qgis.core import QgsStyle
    style = QgsStyle.defaultStyle()
    ramp = style.colorRamp(name) if name in style.colorRampNames() else None
    if ramp is None:
        ramp = style.colorRamp("Viridis")
    if ramp is None or k <= 0:
        return [QColor("#2a78d6")] * max(k, 0)
    return [QColor(ramp.color(i / (k - 1) if k > 1 else 1.0)) for i in range(k)]


def compute(table, plot):
    """The plot's numbers from the records read (runs in a job for big
    layers)."""
    t = plot["type"]
    d = dict(plot["data"])
    axes = plot.get("axes", {})
    if t == "scatter":
        return P.scatter(table, d, bool(axes.get("x_log")), bool(axes.get("y_log")))
    if t == "pie":
        return P.pie(table, d)
    if t == "histogram":
        return P.histogram(table, d)
    if t == "box":
        return P.box(table, d)
    if t == "qq":
        return P.qq(table, d)
    if t == "matrix":
        return P.scatter_matrix(table, d)
    if t == "heat":
        for axis in ("x_", "y_"):
            d[axis + "reference"] = P.parse_value(d.get(axis + "reference"), P.DATE) \
                if d.get(axis + "align") == "reference" else None
        return P.matrix_heat(table, d)
    if t == "calendar":
        return P.calendar_heat(table, d)
    if t == "clock":
        return P.data_clock(table, d)
    if d.get("align") == "reference":
        d["reference"] = P.parse_value(d.get("reference"), P.DATE)
    else:
        d["reference"] = None
    if d.get("agg", "count") == "count":
        d["fields"] = []
    elif d.get("split") and d.get("fields"):
        d["fields"] = d["fields"][:1]
    if t == "combo":
        return P.combo(table, d)
    return P.line(table, d) if t == "line" else P.bar(table, d)


class DataPlotPanel(QWidget):
    titleChanged = pyqtSignal()
    drillRequested = pyqtSignal(object, list, str)       # layer, fids, label
    newWindowRequested = pyqtSignal(object, str)         # layer, plot id

    def __init__(self, iface, explorer_lookup=None, parent=None):
        super().__init__(parent)
        self.setObjectName("gtsPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.iface = iface
        self.theme = Theme()
        self.metrics = Metrics(self.font())
        self.setStyleSheet(stylesheet(self.theme, self.metrics))
        self.explorer_lookup = explorer_lookup or (lambda layer: None)
        self.store = PlotStore.instance()
        self.reader = JobRunner(self)
        self.builder = JobRunner(self)
        self.selector = JobRunner(self)
        self._editing = False                # Edit plot is on (the plot frozen)
        self._edit_plot = None               # the plot being edited (saved on Save edits)
        self._stale = False                  # its records changed while frozen
        self._history, self._hist_i, self._saved_i = [], 0, 0
        self._last_tag, self._last_time = None, 0.0
        self.layer = None                    # the layer of the plot shown
        self._new_layer = None               # where New plot starts when no plot is shown
        self.plot_id = None
        self.scope = "all"                   # the records of the plot shown
        self.table = None
        self._table_info = None              # (scope key, fields, classes) of the table
        self.data = None
        self.model = None
        self._revision = 0
        self._sel_version = 0
        self._explorer = None
        self._layer_signals = []
        self._writing = False
        self._closing = False
        self._extent = None
        self._build_ui()
        self.store.changed.connect(self._on_store_changed)
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(450)
        self._reload_timer.timeout.connect(self.load)
        self._sel_timer = QTimer(self)
        self._sel_timer.setSingleShot(True)
        self._sel_timer.setInterval(120)
        self._sel_timer.timeout.connect(self._update_selection)
        self.reader.progress.connect(self._on_progress)
        canvas = iface.mapCanvas() if iface is not None else None
        if canvas is not None:
            canvas.extentsChanged.connect(self._on_extent_changed)

    # ====================================================================
    # UI
    # ====================================================================

    def _build_ui(self):
        m = self.metrics
        root = QVBoxLayout(self)
        root.setContentsMargins(m.margin, m.margin, m.margin, m.margin)
        root.setSpacing(8)

        # top row: Plots | the Edit plot group | Add, then the records and
        # the actions at the right
        top = QHBoxLayout()
        top.setSpacing(m.gap)
        # hides the Plots card for a wider plot; every window opens with it shown
        self.plots_btn = W.button(m, "Plots", icons.native(icons.LIST_VIEW),
                                  "Show or hide the list of plots", checkable=True)
        self.plots_btn.setChecked(True)
        top.addWidget(self.plots_btn)
        top.addWidget(W.vseparator(m))
        # Edit plot, as the editing buttons of Attribute Explorer
        self.edit_btn = W.button(m, "Edit plot", icons.native(icons.TOGGLE_EDIT),
                                 "Edit the look of what is inside the plot and add text, "
                                 "lines, arrows and pictures; the plot and its settings "
                                 "are frozen meanwhile", checkable=True)
        self.edit_btn.toggled.connect(self._on_edit_toggled)
        self.save_edits_btn = W.button(m, "Save edits", icons.native(icons.SAVE_EDITS),
                                       "Keep the changes made in Edit plot")
        self.save_edits_btn.clicked.connect(self.save_edits)
        self.undo_btn = W.square_button(m, icons.native(icons.UNDO), "Undo (Ctrl+Z)")
        self.undo_btn.clicked.connect(self.undo_edit)
        self.redo_btn = W.square_button(m, icons.native(icons.REDO), "Redo (Ctrl+Y)")
        self.redo_btn.clicked.connect(self.redo_edit)
        for b in (self.edit_btn, self.save_edits_btn, self.undo_btn, self.redo_btn):
            top.addWidget(b)
        top.addWidget(W.vseparator(m))
        self.add_menu = QMenu(self)
        for kind, text in PE.ADD_KINDS:
            self.add_menu.addAction(PE.kind_icon(kind, self.theme.text), text).triggered.connect(
                lambda _c=False, k=kind: self.add_object(k))
        self.add_btn = W.button(m, "Add", icons.native(icons.ADD),
                                "Add a text box, a horizontal or vertical line, an arrow "
                                "or a picture to the plot", menu=self.add_menu)
        top.addWidget(self.add_btn)
        row = W.ButtonRow(m)
        row.add_stretch()
        self.scope_menu = QMenu(self)
        self.scope_menu.aboutToShow.connect(self._fill_scope_menu)
        self.scope_btn = row.add(W.button(
            m, "Change scope", icons.native(icons.FILTER),
            "Choose which records the plots show; the plot follows them as they change",
            menu=self.scope_menu))
        top.addWidget(row, 1)
        top.addSpacing(m.gap)
        self.clear_btn = W.square_button(m, icons.native(icons.DESELECT_LAYER),
                                         "Clear the selection (deselect the records)")
        self.clear_btn.clicked.connect(self._clear_selection)
        top.addWidget(self.clear_btn)
        export = QMenu(self)
        export.addAction(icons.native(icons.SAVE), "Save as PNG Image…").triggered.connect(
            lambda: self.export_image("png"))
        export.addAction(icons.native(icons.SAVE), "Save as SVG Image…").triggered.connect(
            lambda: self.export_image("svg"))
        export.addAction(icons.native(icons.COPY), "Copy Image").triggered.connect(
            self.copy_image)
        export.addSeparator()
        export.addAction(icons.native(icons.CSV), "Plotted values as CSV…").triggered.connect(
            self.export_csv)
        self.export_btn = W.menu_square(m, icons.native(icons.EXPORT),
                                        "Export the plot: an image or its values", export)
        top.addWidget(self.export_btn)
        self.help_btn = W.square_button(m, icons.native(icons.HELP),
                                        "Help\nHow to make each kind of plot")
        self.help_btn.clicked.connect(self.show_help)
        top.addWidget(self.help_btn)
        self.dock_btn = W.dock_button(m)
        top.addWidget(self.dock_btn)
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.list_card = self._build_list()
        self.plots_btn.toggled.connect(self.list_card.setVisible)
        split.addWidget(self.list_card)
        self.view = PlotView(self.theme, m)
        self.view.rowsPicked.connect(self._on_rows_picked)
        self.view.rowsMenu.connect(self._on_rows_menu)
        self.view.previewPicked.connect(self._on_preview)
        self.view.partSelected.connect(self._on_part_selected)
        self.view.toolChanged.connect(
            lambda kind: self.status.set_status(EDITING_HINT)
            if kind is None and self._editing else None)
        self.view.canvasEdited.connect(self._on_canvas_edited)
        split.addWidget(self.view)
        self.props = PlotProperties(self.theme, m)
        self.props.edited.connect(self._on_edited)
        # after the signal: the card is rebuilt for the new layer's fields
        self.props.layerChosen.connect(
            lambda layer: QTimer.singleShot(0, lambda: self._change_layer(layer)))
        split.addWidget(self.props)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([230, 800, PlotProperties.WIDTH])
        root.addWidget(split, 1)

        self.status = W.StatusStrip(m)
        self.status.cancelRequested.connect(self._cancel)
        root.addWidget(self.status)
        # Ctrl+Z and Ctrl+Y go to the plot while it is edited (not QGIS's undo)
        self.view.editKey.connect(lambda k: self.undo_edit() if k == "undo"
                                  else self.redo_edit())
        self.view.set_message("Choose a layer")
        self._update_scope_label()

    def _build_list(self):
        """The layer's plots, in a card like the Pivot Table's Build your
        table: the heading with New plot, the list's buttons, then the list."""
        m = self.metrics
        box = QFrame()
        box.setObjectName("gtsCard")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()                  # the heading, New plot at its right
        head.addWidget(W.label("Plots", "heading"))
        head.addStretch(1)
        lay.addLayout(head)
        buttons = QVBoxLayout()               # the list's buttons, above it
        buttons.setSpacing(6)
        lay.addLayout(buttons)
        self.plot_list = QListWidget()
        self.plot_list.setObjectName("gtsFormList")
        self.plot_list.setIconSize(QSize(20, 20))
        self.plot_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.plot_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.plot_list.setItemDelegate(_PlotItemDelegate(self.theme, self.plot_list))
        self.plot_list.currentRowChanged.connect(self._on_row_changed)
        self.plot_list.itemDoubleClicked.connect(lambda _i: self.rename_plot())
        self.plot_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.plot_list.customContextMenuRequested.connect(self._list_menu)
        lay.addWidget(self.plot_list, 1)
        self.new_menu = fill_type_menu(QMenu(self), self.new_plot)
        self.new_btn = W.button(m, "New plot", icons.native(icons.ADD),
                                "Make a new plot of this layer", menu=self.new_menu)
        head.addWidget(self.new_btn)
        row = QHBoxLayout()
        row.setSpacing(m.gap)
        self.dup_btn = W.square_button(m, icons.native(icons.COPY), "Duplicate the plot")
        self.dup_btn.clicked.connect(self.duplicate_plot)
        self.del_btn = W.square_button(m, icons.native(icons.DELETE), "Delete the plot")
        self.del_btn.clicked.connect(self.delete_plot)
        self.up_btn = W.square_button(m, icons.glyph("arrow_up", self.theme.text), "Move up")
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn = W.square_button(m, icons.glyph("arrow_down", self.theme.text),
                                        "Move down")
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.window_btn = W.square_button(m, icons.native(icons.TABLE_VISIBLE),
                                          "Open in new window\nShow this plot in another "
                                          "window, beside this one")
        self.window_btn.clicked.connect(self.open_in_new_window)
        more = QMenu(self)
        more.addAction("Rename…").triggered.connect(self.rename_plot)
        more.addSeparator()
        more.addAction(icons.native(icons.SAVE), "Save plot to file…").triggered.connect(
            self.save_to_file)
        more.addAction(icons.native(icons.OPEN), "Load plot from file…").triggered.connect(
            self.load_from_file)
        self.more_btn = W.menu_square(m, icons.glyph("menu", self.theme.text),
                                      "More: rename, save or load a plot file", more)
        for b in (self.dup_btn, self.del_btn, self.up_btn, self.down_btn,
                  self.window_btn, self.more_btn):
            row.addWidget(b)
        row.addStretch(1)
        buttons.addLayout(row)
        box.setMinimumWidth(190)
        return box

    # ====================================================================
    # LAYER AND PLOTS
    # ====================================================================

    def window_title(self):
        return "Data Plot"

    def current_plot(self):
        if not self.plot_id:
            return None
        return self.store.get(self.plot_id)

    @staticmethod
    def _layer_of(plot):
        """The layer a plot is made from (None while it is not in the project)."""
        layer = QgsProject.instance().mapLayer(plot.get("layer") or "") if plot else None
        return layer if isinstance(layer, QgsVectorLayer) else None

    def _default_layer(self):
        """The layer a new plot starts on when no plot is shown: the active
        layer, else the project's first vector layer."""
        active = self.iface.activeLayer() if self.iface is not None else None
        if isinstance(active, QgsVectorLayer) and active.isValid():
            return active
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.isValid():
                return layer
        return None

    def set_layer(self, layer, plot_id=None):
        """Show *plot_id*, else the first plot of *layer* (a layer without
        plots: the plot shown stays, and New plot starts on *layer*).
        None: the layer shown left the project (or the project closed)."""
        if layer is None:
            self._attach(None)
            self._fill_list()
            return
        plots = self.store.plots(layer.id())
        if plot_id is None and plots:
            current = self.current_plot()
            keep = current is not None and current.get("layer") == layer.id()
            plot_id = current["id"] if keep else plots[0].get("id")
        if plot_id is not None:
            self._fill_list(plot_id)
            return
        self._new_layer = layer
        if self.current_plot() is None:
            self._attach(layer)
        self._fill_list()

    def _attach(self, layer):
        """Follow *layer* (the layer of the plot shown): its signals, its
        Attribute Explorer, a fresh table."""
        if layer is self.layer:
            return
        self._disconnect_layer()
        for runner in (self.reader, self.builder, self.selector):
            runner.new_generation()
        self.layer = layer
        self.table = None
        self._table_info = None
        self.data = None
        self._revision = 0
        if layer is not None:
            self._connect_layer()
        self.titleChanged.emit()

    def layers_removed(self, layer_ids):
        """Layers left the project: their plots stay in the list, marked."""
        shown = self.layer is not None and self.layer.id() in layer_ids
        if shown and self._editing:              # keep the edits made so far
            self.stop_editing(ask=False)
        if shown:
            self._attach(None)
        if self._new_layer is not None and self._new_layer.id() in layer_ids:
            self._new_layer = None
        # the project still holds them while they are being removed; the plot
        # shown is shown again (its layer marked) when it was on one of them
        QTimer.singleShot(0, lambda: None if self._closing else
                          self._fill_list(self.plot_id if shown else None))

    def _connect_layer(self):
        lyr = self.layer
        pairs = [
            (lyr.selectionChanged, self._on_selection_changed),
            (lyr.attributeValueChanged, self._on_data),
            (lyr.featureAdded, self._on_data),
            (lyr.featuresDeleted, self._on_data),
            (lyr.dataChanged, self._on_data),
            (lyr.subsetStringChanged, self._on_data),
            (lyr.updatedFields, self._on_schema),
            (lyr.nameChanged, self._on_layer_renamed),
            (lyr.rendererChanged, self._on_renderer),
        ]
        for sig, slot in pairs:
            try:
                sig.connect(slot)
            except (AttributeError, TypeError):
                continue
            self._layer_signals.append((sig, slot))
        self._watch_explorer()

    def _disconnect_layer(self):
        for sig, slot in self._layer_signals:
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._layer_signals = []
        self._unwatch_explorer()

    def _on_layer_renamed(self):
        self._fill_list(self.plot_id)
        self.titleChanged.emit()

    def _fill_list(self, select=None):
        """Every plot of the project, each with its layer after its name."""
        lst = self.plot_list
        keep = select or self.plot_id
        lst.blockSignals(True)
        lst.clear()
        plots = self.store.plots()
        row = -1
        for i, plot in enumerate(plots):
            layer = self._layer_of(plot)
            name = plot.get("name", "Plot")
            item = QListWidgetItem(icons.plot_type(plot.get("type")), name)
            if layer is not None:
                where = layer.name()
            else:
                where = f"{plot.get('layer_name') or 'its layer'}: not in the project"
                item.setForeground(QColor(self.theme.text_dim))
            item.setData(LAYER_ROLE, where)
            item.setToolTip(f"{name}\n{P.TYPE_LABELS.get(plot.get('type'), '')} of {where}")
            item.setData(Qt.ItemDataRole.UserRole, plot.get("id"))
            lst.addItem(item)
            if plot.get("id") == keep:
                row = i
        if row < 0 and plots:
            row = 0
        lst.setCurrentRow(row)
        lst.blockSignals(False)
        new_id = plots[row].get("id") if row >= 0 else None
        if new_id != self.plot_id or select is not None:
            self._show_plot(new_id)
        elif new_id is None:
            self._show_plot(None)

    def _on_row_changed(self, row):
        item = self.plot_list.item(row) if row >= 0 else None
        self._show_plot(item.data(Qt.ItemDataRole.UserRole) if item is not None else None)

    def _show_plot(self, plot_id):
        """Show a plot on its own layer, with its own records."""
        plot = self.store.get(plot_id) if plot_id else None
        self.plot_id = plot.get("id") if plot else None
        if plot is not None:
            layer = self._layer_of(plot)
            self._attach(layer)
            scope = plot.get("scope", "all")
            if scope == "extent" and layer is not None and not layer.isSpatial():
                scope = "all"
            if scope != self.scope:
                self.scope = scope
                self._watch_explorer()
        else:
            self._attach(self._new_layer or self._default_layer())
        self.data = None
        self._update_scope_button()
        self.props.set_plot(copy.deepcopy(plot) if plot else None,
                            self.layer.fields() if self.layer is not None else None)
        self._update_buttons()
        if plot is None:
            self.view.set_message("No plots yet.\nChoose New plot to make one."
                                  if not self.store.plots() else "Choose a plot")
            self.status.set_status("Choose New plot to make a plot")
            return
        if self.layer is None:
            text = (f"The layer of this plot ({plot.get('layer_name') or 'unknown'}) is not "
                    "in the project: choose a layer in Data")
            self.view.set_message(text)
            self.status.set_status(text, "warning")
            return
        self.load()

    def _update_buttons(self):
        has_plot = self.current_plot() is not None
        has_layer = self.layer is not None
        editing = self._editing
        free = not editing                       # frozen while the plot is edited
        self.new_btn.setEnabled(free and (has_layer or self._default_layer() is not None))
        for b in (self.dup_btn, self.del_btn, self.up_btn, self.down_btn, self.window_btn):
            b.setEnabled(free and has_plot)
        self.export_btn.setEnabled(has_plot)
        self.scope_btn.setEnabled(free and has_plot and has_layer)
        self.clear_btn.setEnabled(free and has_layer)
        self.more_btn.setEnabled(free)
        self.plot_list.setEnabled(free)
        self.edit_btn.setEnabled(editing or (has_plot and has_layer))
        self.save_edits_btn.setEnabled(editing and self._hist_i != self._saved_i)
        self.undo_btn.setEnabled(editing and self._hist_i > 0)
        self.redo_btn.setEnabled(editing and self._hist_i < len(self._history) - 1)
        self.add_btn.setEnabled(editing)

    def _on_store_changed(self, _layer_id, plot_id):
        if self._writing or self._closing or self._editing:
            return
        if plot_id and plot_id == self.plot_id:
            self._fill_list(plot_id)              # edited in another window
        else:
            self._fill_list()

    def _write(self, plot):
        if self.layer is not None and plot.get("layer") == self.layer.id():
            plot["layer_name"] = self.layer.name()
        self._writing = True
        try:
            self.store.update(plot)
        finally:
            self._writing = False

    def _add(self, plot):
        self._writing = True
        try:
            pid = self.store.add(plot, after=self.plot_id)
        finally:
            self._writing = False
        self._fill_list(pid)
        return pid

    def new_plot(self, ptype, scope=None, layer=None):
        """A new plot of type *ptype* on *layer* (by default the layer of
        the plot shown), with the records *scope*."""
        layer = layer or self.layer or self._new_layer or self._default_layer()
        if layer is None:
            return
        fields = layer.fields()
        pairs = [(fields.at(i).name(), F.field_kind(fields.at(i)))
                 for i in range(fields.count())]
        names = {p.get("name") for p in self.store.plots()}
        base = P.TYPE_LABELS[ptype]
        name, n = base, 2
        while name in names:
            name, n = f"{base} {n}", n + 1
        plot = P.new_plot(ptype, pairs, name)
        plot["layer"], plot["layer_name"] = layer.id(), layer.name()
        plot["scope"] = scope or "all"
        self._add(plot)

    def duplicate_plot(self):
        plot = self.current_plot()
        if plot is None:
            return
        twin = copy.deepcopy(plot)
        twin["name"] = f"{plot.get('name', 'Plot')} (copy)"
        self._add(twin)

    def rename_plot(self):
        plot = self.current_plot()
        if plot is None:
            return
        name, ok = QInputDialog.getText(self, "Rename plot", "Name", text=plot.get("name", ""))
        if ok and name.strip():
            plot = copy.deepcopy(plot)
            plot["name"] = name.strip()
            self._write(plot)
            self._fill_list(plot["id"])

    def delete_plot(self):
        plot = self.current_plot()
        if plot is None:
            return
        answer = QMessageBox.question(self, "Delete plot",
                                      f"Delete the plot \"{plot.get('name', '')}\"?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._writing = True
        try:
            self.store.remove(plot["id"])
        finally:
            self._writing = False
        self.plot_id = None
        self._fill_list()

    def _move(self, delta):
        if self.current_plot() is None:
            return
        self._writing = True
        try:
            self.store.move(self.plot_id, delta)
        finally:
            self._writing = False
        self._fill_list(self.plot_id)

    def open_in_new_window(self):
        if self.current_plot() is not None:
            self.newWindowRequested.emit(self.layer, self.plot_id)

    def _list_menu(self, pos):
        menu = QMenu(self)
        menu.addMenu(self.new_menu).setText("New plot")
        if self.current_plot() is not None:
            menu.addAction("Rename…").triggered.connect(self.rename_plot)
            menu.addAction(icons.native(icons.COPY), "Duplicate").triggered.connect(
                self.duplicate_plot)
            menu.addAction(icons.native(icons.DELETE), "Delete").triggered.connect(
                self.delete_plot)
            menu.addSeparator()
            menu.addAction(icons.native(icons.TABLE_VISIBLE),
                           "Open in new window").triggered.connect(self.open_in_new_window)
            menu.addAction(icons.native(icons.SAVE), "Save plot to file…").triggered.connect(
                self.save_to_file)
        menu.addAction(icons.native(icons.OPEN), "Load plot from file…").triggered.connect(
            self.load_from_file)
        menu.exec(self.plot_list.mapToGlobal(pos))

    def save_to_file(self):
        plot = self.current_plot()
        if plot is None:
            return
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                       for ch in plot.get("name", "plot")) or "plot"
        path, _f = QFileDialog.getSaveFileName(self, "Save plot to file", f"{safe}.json",
                                               "Plot (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        # the plot without what ties it to this project (its id and its layer)
        body = {k: v for k, v in plot.items() if k not in ("id", "layer")}
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({FILE_TAG: 1, "plot": body}, fh, indent=2)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.status.set_status(f"Plot saved to {path}", "success")

    def load_from_file(self, path=None):
        """A plot file made again on the layer of the plot shown (the layer
        can then be changed in Data)."""
        layer = self.layer or self._new_layer or self._default_layer()
        if layer is None:
            return
        if not path:
            path, _f = QFileDialog.getOpenFileName(self, "Load plot from file", "",
                                                   "Plot (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            plot = data["plot"] if isinstance(data, dict) and FILE_TAG in data else None
            if not isinstance(plot, dict) or plot.get("type") not in P.TYPE_LABELS:
                raise ValueError("This file holds no Data Plot plot")
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Load plot", f"The plot could not be loaded:\n{exc}")
            return
        fields = layer.fields()
        missing = P.missing_fields(plot, [fields.at(i).name() for i in range(fields.count())])
        plot["layer"], plot["layer_name"] = layer.id(), layer.name()
        self._add(plot)
        if missing:
            self.status.set_status(f"Plot loaded on {layer.name()}, which lacks the field(s) "
                                   + ", ".join(missing) + ": choose others in Data",
                                   "warning")

    def _change_layer(self, layer):
        """The plot shown moves to *layer* (Layer in Data): the fields with
        the same names stay, the others are cleared."""
        plot = self.current_plot()
        if plot is None or layer is None:
            return
        plot = copy.deepcopy(plot)
        fields = layer.fields()
        gone = P.clear_missing_fields(plot, [fields.at(i).name() for i in range(fields.count())])
        plot["layer"], plot["layer_name"] = layer.id(), layer.name()
        if plot.get("scope") == "extent" and not layer.isSpatial():
            plot["scope"] = "all"
        self._write(plot)
        self._fill_list(plot["id"])
        if gone:
            self.status.set_status(f"{layer.name()} has no field " + ", ".join(gone)
                                   + ": choose others in Data", "warning")

    # ====================================================================
    # EDIT PLOT
    # ====================================================================

    def _on_edit_toggled(self, on):
        if on and not self._editing:
            if not self._start_editing():
                self.edit_btn.blockSignals(True)
                self.edit_btn.setChecked(False)
                self.edit_btn.blockSignals(False)
        elif not on and self._editing:
            if not self.stop_editing():
                self.edit_btn.blockSignals(True)
                self.edit_btn.setChecked(True)
                self.edit_btn.blockSignals(False)

    def _start_editing(self):
        """Freeze the plot and its settings; the canvas can be edited."""
        plot = self.current_plot()
        if plot is None or self.layer is None or self.data is None or self.model is None:
            return False
        self._editing = True
        self._stale = False
        self._edit_plot = copy.deepcopy(plot)
        PE.canvas_of(self._edit_plot)
        self._history = [copy.deepcopy(self._edit_plot["canvas"])]
        self._hist_i = self._saved_i = 0
        self._last_tag, self._last_time = None, 0.0
        self.model.plot = self._edit_plot
        self.view.set_model(self.model)
        self.view.set_editing(True)
        self.props.show_edit(None, self._edit_plot, self._on_canvas_card)
        self._update_buttons()
        self.status.set_status(EDITING_HINT)
        return True

    def stop_editing(self, ask=True):
        """Leave Edit plot (asking about unsaved edits); False when the
        user cancels."""
        if not self._editing:
            return True
        if ask and self._hist_i != self._saved_i:
            answer = QMessageBox.question(
                self, "Edit plot", "Save the changes made to the plot?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            if answer == QMessageBox.StandardButton.Save:
                self.save_edits()
        elif not ask and self._hist_i != self._saved_i:
            self.save_edits()
        self._editing = False
        self._edit_plot = None
        self.view.set_editing(False)
        self.edit_btn.blockSignals(True)
        self.edit_btn.setChecked(False)
        self.edit_btn.blockSignals(False)
        stale = self._stale
        self._stale = False
        self._update_buttons()
        if stale:                                # the records changed meanwhile
            self._show_plot(self.plot_id)
        else:
            plot = self.current_plot()
            self.props.set_plot(copy.deepcopy(plot) if plot else None,
                                self.layer.fields() if self.layer is not None else None)
            self._redraw(plot)
        return True

    def confirm_close(self, _reason="close"):
        """The window closes: ask about unsaved edits of Edit plot."""
        return self.stop_editing(ask=True)

    def save_edits(self):
        if not self._editing:
            return
        self._write(copy.deepcopy(self._edit_plot))
        self._saved_i = self._hist_i
        self._update_buttons()
        self.status.set_status("Edits saved: the plot keeps its new look", "success")

    def undo_edit(self):
        if self._editing and self._hist_i > 0:
            self._hist_i -= 1
            self._restore_edit()

    def redo_edit(self):
        if self._editing and self._hist_i < len(self._history) - 1:
            self._hist_i += 1
            self._restore_edit()

    def _restore_edit(self):
        self._edit_plot["canvas"] = copy.deepcopy(self._history[self._hist_i])
        self._last_tag = None
        sel = self.view.selected
        if sel and sel.get("kind") == "object" and \
                not any(o.get("id") == sel.get("id")
                        for o in self._edit_plot["canvas"].get("objects", [])):
            sel = None
        self.view.selected = sel
        self.view.refresh(base=True)
        self.props.show_edit(sel, self._edit_plot, self._on_canvas_card)
        self._update_buttons()

    def _record(self, tag):
        """A change to the canvas: a step to undo (quick changes of one
        setting are one step)."""
        now = time.monotonic()
        snap = copy.deepcopy(self._edit_plot["canvas"])
        same = (tag is not None and tag == self._last_tag and now - self._last_time < 1.5
                and self._hist_i == len(self._history) - 1 and self._hist_i > self._saved_i)
        if same:
            self._history[self._hist_i] = snap
        else:
            del self._history[self._hist_i + 1:]
            self._history.append(snap)
            self._hist_i += 1
        self._last_tag, self._last_time = tag, now
        self._update_buttons()

    def _on_canvas_card(self, base, tag):
        """A setting changed in the Edit plot card."""
        if not self._editing:
            return
        self._record(tag)
        self.view.refresh(base=base)

    def _on_canvas_edited(self, reason):
        """An object added, moved, removed or reordered in the plot."""
        if not self._editing:
            return
        sel = self.view.selected
        tag = f"{reason}:{sel.get('id')}" if reason == "nudge" and sel else None
        self._record(tag)
        if reason in ("add", "delete"):
            self.props.show_edit(self.view.selected, self._edit_plot, self._on_canvas_card)
            self.status.set_status(EDITING_HINT)

    def _on_part_selected(self, part):
        if self._editing:
            self.props.show_edit(part, self._edit_plot, self._on_canvas_card)

    def add_object(self, kind):
        """Add > a kind: the next click (a drag for an arrow) places it."""
        if not self._editing:
            return
        picture = None
        if kind == "picture":
            path, _f = QFileDialog.getOpenFileName(
                self, "Add picture", "", "Pictures (*.png *.jpg *.jpeg *.svg *.bmp *.gif)")
            if not path:
                return
            picture, error = PE.picture_data(path)
            if picture is None:
                QMessageBox.warning(self, "Add picture", error)
                return
        self.view.set_tool(kind, picture)
        self.view.setFocus()
        hint = {"arrow": "Drag in the plot from where the arrow starts to where it points",
                "hline": "Click in the plot where the horizontal line goes",
                "vline": "Click in the plot where the vertical line goes"}.get(
                    kind, f"Click in the plot where the {PE.KIND_LABELS[kind].lower()} goes")
        self.status.set_status(hint + " (Esc: cancel)")

    # ====================================================================
    # RECORDS
    # ====================================================================

    def _fill_scope_menu(self):
        menu = self.scope_menu
        menu.clear()
        layer = self.layer
        if layer is None:
            return
        exp = self.explorer_lookup(layer)
        counts = {"all": f" ({max(layer.featureCount(), 0):,})",
                  "explorer": (f" ({len(exp.model.fids):,})" if exp is not None
                               else " (open the layer in Attribute Explorer first)"),
                  "selected": f" ({layer.selectedFeatureCount():,})",
                  "extent": "" if layer.isSpatial() else " (the layer has no geometry)"}
        enabled = {"all": True, "explorer": exp is not None, "selected": True,
                   "extent": layer.isSpatial()}
        for key, text, icon in SCOPES:
            act = menu.addAction(icons.native(icon), text + counts[key])
            act.setCheckable(True)
            act.setChecked(key == self.scope)
            act.setEnabled(enabled[key])
            act.triggered.connect(lambda _c=False, k=key: self.set_scope(k))

    def set_scope(self, kind):
        """The records of the plot shown (kept with the plot)."""
        plot = self.current_plot()
        if plot is not None and plot.get("scope", "all") != kind:
            plot = copy.deepcopy(plot)
            plot["scope"] = kind
            self._write(plot)
        if kind == self.scope:
            return
        self.scope = kind
        self._watch_explorer()
        self._update_scope_label()
        self.load()

    def _update_scope_label(self):
        """The records of the plot shown, in Change scope's tooltip (its menu
        ticks them too)."""
        text = SCOPE_LABELS[self.scope]
        if self.table is not None and self.layer is not None:
            text += f" ({len(self.table):,})"
        self.scope_btn.setToolTip("Change scope\nThe plot shows " + text.lower()
                                  + " and follows them as they change; the choice is kept "
                                    "with the plot")

    def _update_scope_button(self):
        self._update_scope_label()

    def _clear_selection(self):
        if self.layer is not None:
            try:
                self.layer.removeSelection()
            except RuntimeError:
                pass

    def _watch_explorer(self):
        self._unwatch_explorer()
        if self.scope != "explorer" or self.layer is None:
            return
        exp = self.explorer_lookup(self.layer)
        if exp is None:
            return
        self._explorer = exp
        exp.scopeChanged.connect(self._on_scope_changed)
        exp.destroyed.connect(self._on_explorer_gone)

    def _unwatch_explorer(self):
        exp, self._explorer = self._explorer, None
        if exp is None:
            return
        for sig, slot in ((getattr(exp, "scopeChanged", None), self._on_scope_changed),
                          (getattr(exp, "destroyed", None), self._on_explorer_gone)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError, AttributeError):
                pass

    def _on_explorer_gone(self, *_args):
        self._explorer = None
        if self.scope == "explorer" and not self._closing:
            self.set_scope("all")
            self._update_scope_button()
            self.status.set_status("The Attribute Explorer closed: the plot shows all "
                                   "records", "warning")

    def _on_scope_changed(self):
        if self.scope == "explorer":
            self._reload_timer.start()

    def _on_extent_changed(self):
        if self.scope == "extent" and not self._closing:
            self._reload_timer.start()

    def _extent_rect(self):
        canvas = self.iface.mapCanvas()
        settings = canvas.mapSettings()
        rect = canvas.extent()
        try:
            xform = QgsCoordinateTransform(settings.destinationCrs(), self.layer.crs(),
                                           QgsProject.instance())
            rect = xform.transformBoundingBox(rect)
        except Exception:
            pass
        return rect

    def _scope_spec(self):
        """(ScanSpec, scope key) of the records chosen, or (None, reason)."""
        layer = self.layer
        if self.scope == "explorer":
            exp = self.explorer_lookup(layer)
            if exp is None:                      # no Attribute Explorer of this layer
                self.scope = "all"
                self._update_scope_button()
            else:
                if exp is not self._explorer:
                    self._watch_explorer()
                spec = None
                if getattr(exp, "_has_result", False) and not exp.runner.running("scan"):
                    spec = exp.chart_spec(False)
                if spec is None:
                    return None, "Waiting for the Attribute Explorer…"
                return spec, ("explorer", id(exp), exp.scope_version)
        if self.scope == "selected":
            ids = set(layer.selectedFeatureIds())
            return ScanSpec(layer, None, ids), ("selected", self._sel_version)
        if self.scope == "extent" and layer.isSpatial():
            rect = self._extent_rect()
            self._extent = rect
            key = ("extent", round(rect.xMinimum(), 6), round(rect.yMinimum(), 6),
                   round(rect.xMaximum(), 6), round(rect.yMaximum(), 6))
            return ScanSpec(layer, None, None, rect), key
        return ScanSpec(layer), ("all",)

    # ====================================================================
    # READING AND BUILDING
    # ====================================================================

    def load(self):
        """Read the records the current plot needs (only when the records
        or its fields changed), then build it."""
        if self._closing or self.layer is None:
            return
        if self._editing:                        # frozen: read again after Edit plot
            self._stale = True
            return
        plot = self.current_plot()
        if plot is None:
            return
        self.props.refresh_title_hint()
        reason = P.ready(plot)
        if reason:
            self.view.set_message(reason)
            self.status.set_status(reason, "warning")
            return
        fields = self.layer.fields()
        names = [fields.at(i).name() for i in range(fields.count())]
        missing = P.missing_fields(plot, names)
        if missing:
            text = ("This layer has no field " + ", ".join(missing)
                    + ": choose another in Data")
            self.view.set_message(text)
            self.status.set_status(text, "warning")
            return
        spec, key = self._scope_spec()
        if spec is None:
            self.view.set_message(key)
            self._reload_timer.start()
            return
        wanted = [(n, fields.indexOf(n), F.field_kind(fields.field(n)))
                  for n in P.fields_used(plot)]
        classifier = None
        if plot["type"] == "scatter" and plot["data"].get("colour", "layer") == "layer" \
                and len(P.scatter_fields(plot["data"])) == 1:
            classifier = SymbologyClasses(self.layer)
            if not classifier.usable:
                classifier = None
        info = (key, self._revision, classifier is not None,
                tuple(classifier.colours) if classifier else ())
        have = self._table_info
        if (self.table is not None and have is not None and have[0] == info
                and all(n in have[1] for n, _i, _k in wanted)):
            if self.reader.busy:                 # a read for another plot: not needed now
                self.reader.new_generation()
                self.status.set_busy(False)
            self.rebuild()
            return
        for runner in (self.reader, self.builder, self.selector):
            runner.new_generation()
        names_wanted = {n for n, _i, _k in wanted}

        def work(job, spec=spec, wanted=wanted, classifier=classifier):
            return P.read(spec, job, wanted, classifier)

        def done(job, info=info, names_wanted=names_wanted):
            if self._closing or not self.reader.is_current(job):
                return
            self.status.set_busy(False)
            if job.error:
                QgsMessageLog.logMessage(f"Data Plot failed:\n{job.error}", LOG_TAG)
                last = job.error.strip().splitlines()[-1]
                self.view.set_message(f"The plot could not be made: {last}")
                self.status.set_status(f"Reading failed: {last}", "error")
                return
            if job.cancelled:
                self.view.set_message("Reading cancelled")
                self.status.set_status("Reading cancelled", "warning", ("Read again", self.load))
                return
            self.table = job.result
            self._table_info = (info, names_wanted)
            plot = self.current_plot()
            if plot is not None and any(n not in names_wanted for n in P.fields_used(plot)):
                self.load()                      # the plot shown changed meanwhile
                return
            self.rebuild()

        self.view.set_message(f"Reading {self.layer.name()}…")
        self.status.set_status(f"Reading {self.layer.name()}…", "busy")
        self.status.set_busy(True)
        self.reader.start(f"Data Plot: {self.layer.name()}", work, done, tag="read")

    def _on_progress(self, pct):
        if self._closing or not self.reader.busy or self.layer is None:
            return
        text = f"Reading {self.layer.name()}… {pct:.0f}%"
        self.view.set_message(text)
        self.status.set_status(text, "busy")

    def _cancel(self):
        for runner in (self.reader, self.builder, self.selector):
            runner.cancel_all()

    def rebuild(self):
        """The plot's numbers again from the records read."""
        plot = self.current_plot()
        table = self.table
        if plot is None or table is None or self._closing:
            return
        reason = P.ready(plot)
        if reason:
            self.view.set_message(reason)
            return
        sel_ids = self._selected_ids()
        axes = plot.get("axes", {})
        self._built_logs = (bool(axes.get("x_log")), bool(axes.get("y_log")))
        if len(table) <= SYNC_LIMIT:
            data = compute(table, plot)
            self._apply(plot, data, self._selection(plot, data, table, sel_ids))
            return
        self.builder.new_generation()

        def work(job, table=table, plot=copy.deepcopy(plot), sel_ids=sel_ids):
            data = compute(table, plot)
            job.check()
            return plot, data, self._selection(plot, data, table, sel_ids)

        def done(job):
            if self._closing or not self.builder.is_current(job) or job.result is None:
                if job.error:
                    last = job.error.strip().splitlines()[-1]
                    self.status.set_status(f"The plot could not be made: {last}", "error")
                    QgsMessageLog.logMessage(f"Data Plot failed:\n{job.error}", LOG_TAG)
                return
            if table is self.table:
                self._apply(*job.result)

        self.status.set_status("Updating the plot…", "busy")
        self.builder.start("Data Plot: build", work, done, tag="build")

    @staticmethod
    def _selection(plot, data, table, sel_ids):
        """Selection numbers for the drawing: a mask of the table's rows and,
        for lines and bars, the selected records (and Count / Sum part) of
        every position."""
        mask = P.selected_mask(table.np(None), sel_ids)
        out = {"mask": mask, "per": None, "per_series": None, "part": None, "count": 0}
        t = plot["type"]
        if t in ("scatter", "qq"):
            # a record with several Y fields is several points: count it once
            rows = np.unique(data.rows) if getattr(data, "series", None) is not None \
                else data.rows
            out["count"] = int(mask[rows].sum()) if len(data) else 0
            return out
        if t == "matrix":
            any_value = np.zeros(len(table), dtype=bool)
            for f in data.fields:
                any_value |= ~np.isnan(table.np(f))
            out["count"] = int((mask & any_value).sum())
            return out
        if t in ("heat", "calendar", "clock"):
            out["per"] = P.cell_selected(data, mask).tolist()
            out["count"] = int((mask & (data.cell_of >= 0)).sum())
            return out
        if t == "pie":
            out["per"], out["part"] = P.pie_selected(data, table, mask,
                                                     plot["data"].get("field"))
            if data.fields is None:
                out["count"] = int((mask & (data.group >= 0)).sum())
            else:
                out["count"] = int(mask[P.pie_rows(data, table, range(len(data)))].sum())
            return out
        if t == "histogram":
            out["per"] = P.hist_selected(data, mask)
            if data.series:
                out["per_series"] = [P.hist_selected(data, mask, k)
                                     for k in range(len(data.series))]
            out["count"] = int(mask[data.rows].sum()) if data.rows is not None else 0
            return out
        if t == "box":
            out["per"] = [int(mask[b.rows].sum()) if b.rows is not None else 0
                          for b in data.boxes]
            rows = [b.rows for b in data.boxes if b.rows is not None and len(b.rows)]
            out["count"] = int(mask[np.unique(np.concatenate(rows))].sum()) if rows else 0
            return out
        per, per_series = P.selected_per_position(data, mask)
        out["per"], out["per_series"] = per, per_series
        out["count"] = int((mask & (data.group >= 0)).sum())
        if t == "combo" and out["count"]:
            part = []
            for spec in plot["data"].get("series") or []:
                field = spec.get("field")
                agg = spec.get("agg", "count") if field else "count"
                if spec.get("kind", "bar") != "bar":
                    part.append(None)
                elif agg == "count":
                    part.append(per)
                elif agg == "sum":
                    part.append(P.selected_sums(data, table, mask, field)[0])
                else:
                    part.append(None)
            out["part"] = part
        if t == "bar" and out["count"]:
            agg = data.agg
            fields = [s for s in data.series] if not data.split else []
            if agg == "count":
                out["part"] = per_series if per_series is not None else [per]
            elif agg == "sum":
                d = plot["data"]
                if data.split:
                    out["part"] = P.selected_sums(data, table, mask, d["fields"][0])
                else:
                    out["part"] = [P.selected_sums(data, table, mask, f)[0] for f in fields]
        return out

    def _apply(self, plot, data, sel):
        self.data = data
        self._sel = sel
        self.props.set_series(self._colour_names(plot, data))
        self._redraw(plot)

    @staticmethod
    def _colour_names(plot, data):
        """The names the Appearance colour rows are for (None: one colour)."""
        t = plot["type"]
        if t == "scatter":
            if len(data.names) > 1:
                return list(data.names)              # a colour per Y field
            return [None] if plot["data"].get("colour") == "single" else []
        if t in ("qq", "matrix"):
            return [None]
        if t in ("heat", "calendar", "clock"):
            return []
        if t == "pie":
            return list(data.labels)
        if t == "histogram":
            return list(data.series) if data.series else [None]
        return list(data.series)

    def _redraw(self, plot=None):
        if self._editing:                        # the plot being edited stays as it is
            return
        plot = plot or self.current_plot()
        if plot is None or self.data is None:
            return
        self.model = self._model(plot)
        self.view.set_model(self.model)
        self._update_status()

    def _model(self, plot):
        m = PlotModel()
        m.plot = plot
        m.kind = plot["type"]
        m.data = self.data
        m.table = self.table
        gen = plot.get("general", {})
        m.title = gen.get("title") or P.default_title(plot)
        m.description = gen.get("description") or ""
        d = plot["data"]
        fmt = plot.get("format", {})
        sel = getattr(self, "_sel", None) or {}
        m.sel_mask = sel.get("mask")
        m.sel_per = sel.get("per")
        m.sel_per_series = sel.get("per_series")
        m.sel_part = sel.get("part")
        m.sel_count = sel.get("count", 0)
        project = QgsProject.instance()
        colour = project.selectionColor()
        m.sel_colour = QColor(colour) if colour.isValid() else QColor("#ffff00")
        m.sel_colour.setAlpha(255)
        if m.kind == "scatter":
            names = self.data.names or P.scatter_fields(d)
            m.x_title = gen.get("x_title") or d.get("x") or ""
            m.y_title = gen.get("y_title") or ", ".join(names)
            m.shape = fmt.get("shape", "circle")
            if len(names) > 1:                       # a colour and a shape per Y field
                colours = fmt.get("colours", {})
                m.colours = [series_colour(self.theme, i, name, len(names),
                                           colours.get(str(name)))
                             for i, name in enumerate(names)]
                shapes = fmt.get("shapes", {})
                m.shapes = [shapes.get(str(name), "circle") for name in names]
            elif d.get("colour") == "single" or self.table.classes is None:
                m.single = QColor(fmt.get("colour") or self.theme.bar)
            else:
                m.colours = [QColor(c) for c in self.table.class_colours]
            return m
        data = self.data
        colours = fmt.get("colours", {})
        t = m.kind
        if t == "qq":
            m.x_title = gen.get("x_title") or data.x_name
            m.y_title = gen.get("y_title") or data.y_name
            m.single = QColor(fmt.get("colour") or self.theme.bar)
            return m
        if t == "matrix":
            m.colours = [QColor(fmt.get("colour") or self.theme.bar)]
            return m
        if t in ("heat", "calendar", "clock"):
            k = int(d.get("classes", 5) or 5)
            m.breaks = P.class_breaks(data.values, d.get("classify", "natural"), k)
            m.class_colours = ramp_colours(d.get("ramp", "Viridis"), len(m.breaks))
            if t == "heat":
                m.x_title = gen.get("x_title") or d.get("x") or ""
                m.y_title = gen.get("y_title") or d.get("y") or ""
            return m
        if t == "pie":
            n = len(data.labels)
            m.colours = [series_colour(self.theme, i, name, n, colours.get(str(name)))
                         for i, name in enumerate(data.labels)]
            return m
        if t == "histogram":
            label = {"log": f"ln({data.field})", "sqrt": f"√{data.field}"}.get(
                data.transform, data.field)
            m.x_title = gen.get("x_title") or label
            m.y_title = gen.get("y_title") or "Records"
            if data.series:
                n = len(data.series)
                m.colours = [series_colour(self.theme, i, name, n, colours.get(str(name)))
                             for i, name in enumerate(data.series)]
            else:
                m.colours = [QColor(fmt.get("colour") or self.theme.bar)]
            return m
        if t == "box":
            m.x_title = gen.get("x_title") or (d.get("split") or "")
            one = data.series[0] if len(data.series) == 1 else "Value"
            m.y_title = gen.get("y_title") or (
                "z-score" if data.standardized else one)
            n = len(data.series)
            m.colours = [series_colour(self.theme, i, name, n, colours.get(str(name)))
                         for i, name in enumerate(data.series)]
            return m
        x_title = d.get("x") or ""
        if data.bins is not None:
            x_title += f" (by {data.bins.size} {P.UNIT_LABELS[data.bins.unit].lower()})" \
                if data.bins.size != 1 else \
                f" (by {P.UNIT_LABELS[data.bins.unit].lower()[:-1]})"
        m.x_title = gen.get("x_title") or x_title
        agg = data.agg
        if t == "combo":
            m.y_title = gen.get("y_title") or ""
            n = len(data.series)
            m.colours = [series_colour(self.theme, s, name, n, colours.get(str(name)))
                         for s, name in enumerate(data.series)]
            return m
        if plot["type"] == "bar" and d.get("multi") == "stacked100" and len(data.series) > 1:
            y_title = "Share (%)"
        elif agg == "count":
            y_title = "Count"
        elif len(data.series) == 1 or data.split:
            field = d["fields"][0] if d.get("fields") else ""
            y_title = f"{P.AGG_LABELS[agg]} of {field}"
        else:
            y_title = P.AGG_LABELS[agg]
        m.y_title = gen.get("y_title") or y_title
        m.y_axis_kind = F.INTEGER if agg == "count" else F.REAL
        colours = fmt.get("colours", {})
        n = len(data.series)
        m.colours = [series_colour(self.theme, s, name, n, colours.get(str(name)))
                     for s, name in enumerate(data.series)]
        return m

    def _update_status(self):
        plot, data = self.current_plot(), self.data
        if plot is None or data is None:
            return
        parts = [P.TYPE_LABELS[plot["type"]]]
        if plot["type"] == "scatter":
            several = len(data.names) > 1
            parts.append(f"{len(data):,} points" + (f" of {len(data.names)} Y fields"
                                                    if several else ""))
            if data.missing:
                parts.append(f"{data.missing:,} records without an X or "
                             f"{'any ' if several else ''}Y value left out")
            if data.not_positive:
                parts.append(f"{data.not_positive:,} "
                             f"{'points' if several else 'records'} with values of 0 or "
                             "less left out (log axis)")
            for k, trend in enumerate(data.trends):
                if trend is None:
                    continue
                whose = f"the trend of {data.names[k]}" if several else "the trend"
                if trend.problem:
                    parts.append(f"{whose}: {trend.problem}")
                elif trend.ok and trend.left_out:
                    parts.append(f"{whose} leaves out {trend.left_out:,} values of "
                                 "0 or less")
            from .plot_view import DENSITY_LIMIT
            if len(data) > DENSITY_LIMIT:
                parts.append("shaded by records per spot")
            hint = "Click a point to select its record, drag a box for several"
        elif plot["type"] == "pie":
            parts.append(f"{len(data):,} slices")
            if data.excluded:
                parts.append(f"{data.excluded:,} records without a category left out")
            if data.left_out:
                parts.append("not above 0, so not shown: " + ", ".join(
                    str(x) for x in data.left_out[:5]) + ("…" if len(data.left_out) > 5 else ""))
            hint = "Click a slice to select its records"
        elif plot["type"] == "histogram":
            if len(data):
                width = data.edges[1] - data.edges[0]
                parts.append(f"{len(data):,} bins of {value_text(width)}")
            if data.missing:
                parts.append(f"{data.missing:,} records without a value left out")
            if data.left_out:
                parts.append(f"{data.left_out:,} with no "
                             + ("logarithm (0 or less)" if data.transform == "log"
                                else "square root (below 0)") + " left out")
            hint = "Click a bar to select its records, drag for a range"
        elif plot["type"] == "qq":
            parts.append(f"{len(data):,} points")
            if data.missing:
                parts.append(f"{data.missing:,} records without a value left out")
            if data.left_out:
                parts.append(f"{data.left_out:,} values the transformation cannot take "
                             "left out")
            from .plot_view import DENSITY_LIMIT
            if len(data) > DENSITY_LIMIT:
                parts.append("shaded by records per spot")
            hint = "Click a point to select its record, drag a box for several"
        elif plot["type"] == "matrix":
            parts.append(f"{len(data.fields)} fields, {len(data.fields) * (len(data.fields) - 1) // 2} pairs")
            hint = "Click a cell to show it in the preview; drag in a plot to select"
        elif plot["type"] in ("heat", "calendar", "clock"):
            filled = int((data.records > 0).sum()) if data.records is not None else 0
            parts.append(f"{filled:,} cells with records")
            if data.excluded:
                parts.append(f"{data.excluded:,} records without a value left out")
            parts.extend(data.notes)
            hint = "Click a cell to select its records"
        elif plot["type"] == "box":
            parts.append(f"{sum(1 for b in data.boxes if b.n):,} boxes")
            if data.standardized:
                parts.append("values as z-scores")
            parts.extend(data.notes)
            hint = "Click a box or an outlier to select its records"
        else:
            unit = {"bar": "bars", "line": "points"}.get(
                plot["type"], "intervals" if data.x_role == P.DATE else "categories")
            parts.append(f"{len(data):,} {unit}")
            trimmed = data.bins.trimmed if data.bins is not None else 0
            if data.excluded - trimmed:
                parts.append(f"{data.excluded - trimmed:,} records without a value along "
                             "the axis left out")
            if data.capped:
                parts.append(f"{data.capped:,} records in the categories past the first "
                             f"{P.MAX_BARS:,} left out")
            if data.bins is not None and data.bins.trimmed:
                parts.append(f"{data.bins.trimmed:,} in trimmed intervals")
            parts.extend(data.notes)
            hint = "Click to select records, drag for a range"
        n_sel = self.model.sel_count if self.model is not None else 0
        shown = self._shown(plot, data)
        parts.append(f"{n_sel:,} of {shown:,} records selected" if n_sel else hint)
        self.status.set_status("  •  ".join(parts))
        self._update_scope_label()
        self.props.set_summary(self._summary_rows(plot, data, n_sel))

    def _shown(self, plot, data):
        """Records the plot shows."""
        t = plot["type"]
        if t == "scatter":
            return len(np.unique(data.rows)) if data.series is not None else len(data)
        if t == "pie":
            if data.fields is None:
                return sum(data.records)
            return len(P.pie_rows(data, self.table, range(len(data))))
        if t == "histogram":
            return len(data.rows) if data.rows is not None else 0
        if t == "box":
            rows = [b.rows for b in data.boxes if b.rows is not None and len(b.rows)]
            return len(np.unique(np.concatenate(rows))) if rows else 0
        if t == "qq":
            return len(data)
        if t == "matrix":
            any_value = np.zeros(len(self.table), dtype=bool)
            for f in data.fields:
                any_value |= ~np.isnan(self.table.np(f))
            return int(any_value.sum())
        if t in ("heat", "calendar", "clock"):
            return int((data.cell_of >= 0).sum())
        return sum(data.records)

    def _summary_rows(self, plot, data, n_sel):
        """The Summary section's rows (as the Pivot Table chart's)."""
        n = len(self.table) if self.table is not None else 0
        rows = [("Records", F.format_number(n), "Records read: " + SCOPE_LABELS[self.scope])]
        t = plot["type"]
        if t == "pie":
            rows.append(("Slices", F.format_number(len(data))))
            rows.append(("Whole", value_text(data.total)))
            if len(data):
                rows.append(("Largest", f"{data.labels[0]} "
                             f"({100.0 * data.values[0] / data.total:.1f}%)"))
            rows.append(("Selected", F.format_number(n_sel)))
            return rows
        if t == "histogram":
            st = data.stats
            kind = data.kind if data.transform == "none" else F.REAL
            rows.append(("Values", F.format_number(st.get("n", 0))))
            for label, key in (("Minimum", "min"), ("Maximum", "max"), ("Mean", "mean"),
                               ("Median", "median"), ("Std dev", "std")):
                rows.append((label, value_text(st.get(key), kind)))
            rows.append(("Selected", F.format_number(n_sel)))
            return rows
        if t == "qq":
            rows.append(("Points", F.format_number(len(data))))
            if data.ref is not None:
                rows.append(("Line slope", value_text(data.ref[0]),
                             "The reference line through the first and third quartiles"))
            if data.lam is not None:
                rows.append(("Box-Cox λ", f"{data.lam:.2f}"))
            rows.append(("Selected", F.format_number(n_sel)))
            return rows
        if t == "matrix":
            k = len(data.fields)
            pairs = [(data.r2[i, j], data.fields[i], data.fields[j])
                     for i in range(k) for j in range(i + 1, k) if data.r2[i, j] == data.r2[i, j]]
            if pairs:
                best = max(pairs)
                rows.append(("Strongest", f"{best[1]} · {best[2]} (R² {best[0]:.2f})"))
            rows.append(("Selected", F.format_number(n_sel)))
            return rows
        if t in ("heat", "calendar", "clock"):
            vals = data.values[~np.isnan(data.values)]
            rows.append(("Cells", F.format_number(int((data.records > 0).sum()))))
            if len(vals):
                rows.append(("Largest", value_text(float(vals.max()))))
                rows.append(("Smallest", value_text(float(vals.min()))))
            rows.append(("Selected", F.format_number(n_sel)))
            return rows
        if t == "box":
            rows.append(("Boxes", F.format_number(sum(1 for b in data.boxes if b.n))))
            outliers = sum(len(b.out_values) for b in data.boxes
                           if b.out_values is not None)
            rows.append(("Outliers", F.format_number(outliers),
                         "More than 1.5 × the interquartile range beyond the box"))
            rows.append(("Selected", F.format_number(n_sel)))
            return rows
        if plot["type"] == "scatter":
            rows.append(("Points", F.format_number(len(data))))
            left = data.missing + data.not_positive
            if left:
                rows.append(("Left out", F.format_number(left),
                             "No X or Y value, or 0 or less on a log axis"))
            for k, trend in enumerate(data.trends):
                if trend is not None and trend.ok and trend.r2 is not None:
                    label = "R²" if len(data.names) < 2 else f"R² {data.names[k]}"
                    rows.append((label, f"{trend.r2:.4f}", trend.equation))
        else:
            unit = {"bar": "Bars", "line": "Points"}.get(t, "Categories")
            rows.append((unit, F.format_number(len(data))))
            if data.excluded or data.capped:
                rows.append(("Left out", F.format_number(data.excluded + data.capped),
                             "No value along the X axis, a trimmed interval, or past the "
                             f"first {P.MAX_BARS:,} categories"))
            totals = [(label, v) for label, v in zip(data.labels, data.values[0])
                      if v == v] if len(data.series) == 1 else []
            if totals:
                kind = self.model.y_axis_kind if self.model is not None else F.REAL
                hi = max(totals, key=lambda t: t[1])
                lo = min(totals, key=lambda t: t[1])
                rows.append(("Largest", f"{hi[0]} ({value_text(hi[1], kind)})"))
                rows.append(("Smallest", f"{lo[0]} ({value_text(lo[1], kind)})"))
        rows.append(("Selected", F.format_number(n_sel)))
        return rows

    # ====================================================================
    # EDITS AND LAYER EVENTS
    # ====================================================================

    def _on_edited(self, read):
        plot = self.props.plot
        if plot is None or self.layer is None:
            return
        self._write(plot)
        self.props.refresh_title_hint()
        if read:
            self.load()
        elif self.data is not None and plot["type"] == "scatter" and self._log_changed(plot):
            self.rebuild()
        else:
            self._redraw(self.current_plot())

    def _log_changed(self, plot):
        axes = plot.get("axes", {})
        return (bool(axes.get("x_log")), bool(axes.get("y_log"))) != \
            getattr(self, "_built_logs", (False, False))

    def _on_preview(self, name):
        """A small histogram's title clicked: show it in the preview."""
        plot = self.current_plot()
        if plot is None:
            return
        key, value = "preview_of", name
        if plot["type"] == "matrix":
            if plot["data"].get("upper") != "preview":
                return
            key, value = "preview_pair", [int(v) for v in name.split(",")]
        elif not plot["data"].get("preview"):
            return
        plot = copy.deepcopy(plot)
        plot["data"][key] = value
        if self.props.plot is not None:
            self.props.plot["data"][key] = value
        self._write(plot)
        self._redraw(plot)

    def _on_data(self, *_args):
        self._revision += 1
        if self.current_plot() is not None:
            self._reload_timer.start()

    def _on_schema(self):
        self._revision += 1
        plot = self.current_plot()
        self.props.set_plot(copy.deepcopy(plot) if plot else None, self.layer.fields())
        if plot is not None:
            self._reload_timer.start()

    def _on_renderer(self):
        plot = self.current_plot()
        if plot is not None and plot["type"] == "scatter":
            self._table_info = None
            self._reload_timer.start()

    def _on_selection_changed(self, *_args):
        self._sel_version += 1
        if self.scope == "selected":
            self._reload_timer.start()
        elif self.data is not None:
            self._sel_timer.start()

    def _selected_ids(self):
        try:
            return self.layer.selectedFeatureIds()
        except RuntimeError:
            return []

    def _update_selection(self):
        if self._editing:                        # frozen: drawn again after Edit plot
            self._stale = True
            return
        plot, data, table = self.current_plot(), self.data, self.table
        if plot is None or data is None or table is None or self._closing:
            return
        sel_ids = self._selected_ids()
        if len(table) <= SYNC_LIMIT:
            self._sel = self._selection(plot, data, table, sel_ids)
            self._redraw(plot)
            return
        self.selector.new_generation()

        def work(job, plot=copy.deepcopy(plot), data=data, table=table, sel_ids=sel_ids):
            return self._selection(plot, data, table, sel_ids)

        def done(job):
            if self._closing or not self.selector.is_current(job) or job.result is None:
                return
            if data is self.data:
                self._sel = job.result
                self._redraw()

        self.selector.start("Data Plot: selection", work, done, tag="sel")

    # ====================================================================
    # PICKING
    # ====================================================================

    def _fids(self, rows):
        if self.table is None or rows is None:
            return []
        return [int(v) for v in self.table.np(None)[rows]]

    def _on_rows_picked(self, rows, add):
        fids = self._fids(rows)
        if not fids and add:
            return
        try:
            self.layer.selectByIds(fids, SELECT_ADD if add else SELECT_SET)
        except RuntimeError:
            pass

    def _on_rows_menu(self, rows, global_pos):
        fids = self._fids(rows)
        if not fids:
            return
        menu = QMenu(self)
        n = len(fids)
        menu.addAction(icons.native(icons.SELECT_ALL), f"Select these records ({n:,})"
                       ).triggered.connect(lambda: self._on_rows_picked(rows, False))
        menu.addAction(icons.tool(icons.EXPLORER),
                       f"Inspect in Attribute Explorer ({n:,})").triggered.connect(
            lambda: self.drillRequested.emit(self.layer, fids,
                                             f"Data Plot records ({n:,})"))
        menu.exec(global_pos)

    # ====================================================================
    # EXPORT, HELP
    # ====================================================================

    def _safe_name(self):
        plot = self.current_plot() or {}
        raw = f"{self.layer.name() if self.layer else 'layer'}_{plot.get('name', 'plot')}"
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw) or "plot"

    def export_image(self, fmt, path=None):
        if self.data is None:
            return
        ext = ".png" if fmt == "png" else ".svg"
        if path is None:
            flt = "PNG image (*.png)" if fmt == "png" else "SVG image (*.svg)"
            path, _sel = QFileDialog.getSaveFileName(self, "Save the plot",
                                                     f"{self._safe_name()}{ext}", flt)
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext
        try:
            if fmt == "png":
                if not self.view.render_image(2.0).save(path):
                    raise OSError(f"Could not write {path}")
            else:
                self.view.render_svg(path, self.model.title if self.model else "")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            self.status.set_status(f"Plot export failed: {exc}", "error")
            return
        self.status.set_status(f"Plot saved to {path}", "success")

    def copy_image(self):
        if self.data is None:
            return
        QGuiApplication.clipboard().setImage(self.view.render_image(2.0))
        self.status.set_status("Plot copied as an image", "success")

    def export_csv(self, path=None):
        plot, data = self.current_plot(), self.data
        if plot is None or data is None:
            return
        if path is None:
            path, _sel = QFileDialog.getSaveFileName(self, "Export the plotted values",
                                                     f"{self._safe_name()}.csv", "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        header, rows = self.csv_rows(plot, data)
        meta = {"Layer": self.layer.name(), "Plot": plot.get("name", ""),
                "Type": P.TYPE_LABELS[plot["type"]], "Records": SCOPE_LABELS[self.scope],
                "Records read": len(self.table) if self.table is not None else 0}
        try:
            X.write_csv(path, header, rows, meta)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            self.status.set_status(f"Export failed: {exc}", "error")
            return
        self.status.set_status(f"{len(rows):,} rows exported to {path}", "success")

    def csv_rows(self, plot, data):
        d = plot["data"]
        sel = (getattr(self, "_sel", None) or {}).get("mask")
        per = (getattr(self, "_sel", None) or {}).get("per") or []
        t = plot["type"]
        if t == "pie":
            header = ["Slice", "Records" if data.agg == "count" else
                      P.AGG_LABELS.get(data.agg, "Value"), "Share (%)", "Records",
                      "Selected"]
            rows = [[label, v, round(100.0 * v / data.total, 4) if data.total else None,
                     data.records[i], per[i] if i < len(per) else 0]
                    for i, (label, v) in enumerate(zip(data.labels, data.values))]
            return header, rows
        if t == "histogram":
            header = ["From", "To", "Records", "Share (%)", "Selected"]
            header += [str(s) for s in data.series]
            total = sum(data.counts) or 1
            rows = []
            for i, c in enumerate(data.counts):
                row = [data.edges[i], data.edges[i + 1], c, round(100.0 * c / total, 4),
                       per[i] if i < len(per) else 0]
                row += [cs[i] for cs in (data.series_counts or [])]
                rows.append(row)
            return header, rows
        if t in ("heat", "calendar", "clock"):
            nc = len(data.columns)
            nr = len(data.rows)
            header = (["Panel"] if t == "calendar" else []) + [
                "Row", "Column", "Records" if data.agg == "count" else
                P.AGG_LABELS.get(data.agg, "Value"), "Records", "Selected"]
            rows = []
            for k in range(len(data)):
                if not data.records[k]:
                    continue
                panel, rest = divmod(k, nr * nc)
                r, c = divmod(rest, nc)
                v = float(data.values[k])
                row = ([data.panels[panel]] if t == "calendar" else []) + [
                    data.rows[r], data.columns[c], None if v != v else v,
                    int(data.records[k]), per[k] if k < len(per) else 0]
                rows.append(row)
            return header, rows
        if t == "matrix":
            header = ["Field", "Field", "R²", "r", "Records with both"]
            k = len(data.fields)
            rows = [[data.fields[i], data.fields[j],
                     None if data.r2[i, j] != data.r2[i, j] else float(data.r2[i, j]),
                     None if data.r[i, j] != data.r[i, j] else float(data.r[i, j]),
                     int(data.n[i, j])]
                    for i in range(k) for j in range(i + 1, k)]
            return header, rows
        if t == "box":
            header = ["Category", "Field", "Records", "Lower whisker", "First quartile",
                      "Median", "Third quartile", "Upper whisker", "Mean", "Outliers",
                      "Selected"]
            rows = []
            for k, b in enumerate(data.boxes):
                rows.append([b.label, data.series[b.series], b.n, b.low, b.q1, b.median, b.q3,
                             b.high, b.mean,
                             len(b.out_values) if b.out_values is not None else 0,
                             per[k] if k < len(per) else 0])
            return header, rows
        if t == "qq":
            fids = self.table.np(None)[data.rows]
            header = ["Feature id", data.x_name, data.y_name, "Selected"]
            rows = [[int(fids[k]), float(data.x[k]), float(data.y[k]),
                     "Yes" if sel is not None and sel[data.rows[k]] else ""]
                    for k in range(len(data))]
            return header, rows
        if t == "scatter" and data.series is not None:
            # several Y fields: a row per record, a column per Y field
            order = np.unique(data.rows)
            where = np.full(len(self.table), -1, dtype=np.int64)
            where[order] = np.arange(len(order))
            n = len(data.names)
            xs = [None] * len(order)
            ys = [[None] * n for _ in order]
            for k in range(len(data)):
                i = int(where[data.rows[k]])
                xs[i] = float(data.x[k])
                ys[i][int(data.series[k])] = float(data.y[k])
            sizes = self.table.np(d["size"]) if data.size is not None else None
            fids = self.table.np(None)
            header = ["Feature id", d["x"]] + list(data.names) + \
                ([d["size"]] if sizes is not None else []) + ["Selected"]
            rows = []
            for i, r in enumerate(order.tolist()):
                row = [int(fids[r]), xs[i]] + ys[i]
                if sizes is not None:
                    v = float(sizes[r])
                    row.append(None if v != v else v)
                row.append("Yes" if sel is not None and sel[r] else "")
                rows.append(row)
            kinds = [data.x_kind] + list(data.y_kinds)
            for i, h in enumerate(kinds, 1):
                if P.role(h) == P.DATE:
                    for row in rows:
                        if row[i] is not None:
                            row[i] = value_text(row[i], h)
            return header, rows
        if t == "scatter":
            fids = self.table.np(None)[data.rows]
            header = ["Feature id", d["x"], d["y"]] + ([d["size"]] if data.size is not None
                                                        else [])
            if data.classes is not None:
                header.append("Legend class")
            header.append("Selected")
            rows = []
            labels = self.table.class_labels
            for k in range(len(data)):
                row = [int(fids[k]), float(data.x[k]), float(data.y[k])]
                if data.size is not None:
                    v = float(data.size[k])
                    row.append(None if v != v else v)
                if data.classes is not None:
                    c = int(data.classes[k])
                    row.append(labels[c] if 0 <= c < len(labels) else "")
                row.append("Yes" if sel is not None and sel[data.rows[k]] else "")
                rows.append(row)
            for i, h in ((1, data.x_kind), (2, data.y_kind)):
                if P.role(h) == P.DATE:
                    for row in rows:
                        row[i] = value_text(row[i], h)
            return header, rows
        header = [d["x"]]
        if data.x_role == P.DATE:
            header += ["From", "To"]
        header += [str(s) for s in data.series] + ["Records", "Selected"]
        per = (getattr(self, "_sel", None) or {}).get("per") or [0] * len(data)
        rows = []
        for i, label in enumerate(data.labels):
            row = [label]
            if data.x_role == P.DATE:
                row += [value_text(data.starts[i], F.DATETIME),
                        value_text(data.ends[i], F.DATETIME)]
            for vals in data.values:
                v = vals[i]
                row.append(None if v != v else v)
            row += [data.records[i], per[i] if i < len(per) else 0]
            rows.append(row)
        return header, rows

    def show_help(self):
        from .help_dialog import HelpDialog
        dlg = getattr(self, "_help_dialog", None)
        if dlg is None:
            dlg = self._help_dialog = HelpDialog(self, topic="plot")
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ====================================================================
    # CLOSING
    # ====================================================================

    def shutdown(self):
        self._closing = True
        for timer in (self._reload_timer, self._sel_timer):
            timer.stop()
        for runner in (self.reader, self.builder, self.selector):
            runner.new_generation()
        try:
            self.store.changed.disconnect(self._on_store_changed)
        except (TypeError, RuntimeError):
            pass
        try:
            self.iface.mapCanvas().extentsChanged.disconnect(self._on_extent_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self._disconnect_layer()
        self.table = None
        self.data = None
