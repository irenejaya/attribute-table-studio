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
import uuid

from qgis.PyQt.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QScrollArea,
    QSizePolicy, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)
from qgis.core import (
    QgsCoordinateTransform, QgsExpressionContext, QgsMessageLog, QgsProject,
    QgsRenderContext,
)
from qgis.gui import QgsColorButton

from ..core import export as X
from ..core import fields as F
from ..core import plots as P
from ..core.compat import SELECT_ADD, SELECT_SET, VECTOR_LAYER_FILTER
from ..core.jobs import JobRunner
from ..core.scan import ScanSpec
from . import icons
from . import widgets as W
from .plot_view import PlotModel, PlotView, series_colour, value_text
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


def _is_number(kind):
    return P.role(kind) == P.NUMBER and kind != F.TIME


def _is_date_or_number(kind):
    return P.role(kind) in (P.DATE, P.NUMBER)


def _is_category(kind):
    return kind in (F.TEXT, F.BOOL, F.INTEGER, F.OTHER) or P.role(kind) == P.DATE


def _is_split(kind):
    return kind in (F.TEXT, F.BOOL, F.INTEGER, F.OTHER)


# ============================================================================
# PLOTS IN THE PROJECT
# ============================================================================

class PlotStore(QObject):
    """The plots of every layer, kept in the QGIS project as JSON (layer id
    -> list of plots).  One store is shared by all Data Plot windows;
    *changed* tells them a layer's list changed ("" for every layer)."""

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
            self._data = data if isinstance(data, dict) else {}
        return self._data

    def _save(self, layer_id, plot_id=""):
        QgsProject.instance().writeEntry(PROJECT_SCOPE, PROJECT_PLOTS,
                                         json.dumps(self._load()))
        self.changed.emit(layer_id, plot_id)

    def plots(self, layer_id):
        return self._load().get(layer_id, [])

    def get(self, layer_id, plot_id):
        for plot in self.plots(layer_id):
            if plot.get("id") == plot_id:
                return plot
        return None

    def add(self, layer_id, plot, after=None):
        plot = copy.deepcopy(plot)
        plot["id"] = uuid.uuid4().hex[:12]
        plots = self._load().setdefault(layer_id, [])
        ids = [p.get("id") for p in plots]
        pos = ids.index(after) + 1 if after in ids else len(plots)
        plots.insert(pos, plot)
        self._save(layer_id)
        return plot["id"]

    def update(self, layer_id, plot):
        plots = self._load().setdefault(layer_id, [])
        for i, p in enumerate(plots):
            if p.get("id") == plot.get("id"):
                plots[i] = copy.deepcopy(plot)
                self._save(layer_id, plot["id"])
                return

    def remove(self, layer_id, plot_id):
        plots = self._load().get(layer_id, [])
        self._load()[layer_id] = [p for p in plots if p.get("id") != plot_id]
        self._save(layer_id)

    def move(self, layer_id, plot_id, delta):
        plots = self._load().get(layer_id, [])
        ids = [p.get("id") for p in plots]
        if plot_id not in ids:
            return
        i = ids.index(plot_id)
        j = max(0, min(len(plots) - 1, i + delta))
        if i != j:
            plots.insert(j, plots.pop(i))
            self._save(layer_id)


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
    WIDTH = 312                              # as the Visualize Statistics card

    def __init__(self, theme, metrics, parent=None):
        super().__init__(parent)
        self.theme, self.metrics = theme, metrics
        self.plot = None
        self.fields = None
        self.series = []                     # series of the last build (colours)
        self.summary_rows = []
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

    def _row(self, text, widget, tip="", stretch=1):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(self.metrics.gap)
        row.addWidget(W.label(text, "dim"))
        row.addWidget(widget, stretch)
        if tip:
            holder.setToolTip(tip)
        self.lay.addWidget(holder)
        return holder

    def _combo(self, items, current):
        box = QComboBox()
        box.setFixedHeight(self.metrics.control_h)
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
        return header

    def _changed(self, read=False):
        if not self._quiet:
            self.edited.emit(read)

    def _rebuild(self):
        """Lay the card out again (a Y field added or removed), keeping
        the scroll position."""
        pos = self.verticalScrollBar().value()
        self.set_plot(self.plot, self.fields)
        self.verticalScrollBar().setValue(pos)

    # ---- building ------------------------------------------------------
    def set_plot(self, plot, fields):
        """Show *plot*'s settings (None: an empty card)."""
        self.plot, self.fields = plot, fields
        self._quiet = True
        self._card()                         # setWidget deletes the previous card
        self.title_edit = self.colour_lay = self.summary_grid = None
        if plot is not None:
            if plot["type"] == "scatter":
                self._scatter_data()
            else:
                self._series_data()
            self._axes()
            self._guides()
            self._summary()
            self._appearance()
        self.lay.addStretch(1)
        self._quiet = False

    # ---- Data: scatter -------------------------------------------------
    def _scatter_data(self):
        d = self.plot["data"]
        self._header("Data")
        x = self._field_combo(d.get("x"), _is_number, none_label=CHOOSE)
        y = self._field_combo(d.get("y"), _is_number, none_label=CHOOSE)
        self._row("X-axis", x, "The number field along the bottom")
        self._row("Y-axis", y, "The number field up the side")
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
        trend = self._tick("Trend line", d.get("trend"),
                           "The least squares trend of Y on X, with its equation and R²")
        kind = self._combo(P.TRENDS, d.get("trend_type", "linear"))
        kind_row = self._row("Trend", kind)
        order = QSpinBox()
        order.setRange(2, 6)
        order.setValue(int(d.get("order", 2)))
        order.setFixedHeight(self.metrics.control_h)
        order_row = self._row("Order", order, "The polynomial's order (2: a parabola)")
        note = self._note("R² measures the fit of a straight line; for this trend it is "
                          "that of the line on the logarithms, so read it with care.")

        def follow():
            on = trend.isChecked()
            kind_row.setEnabled(on)
            order_row.setVisible(on and kind.currentData() == "polynomial")
            note.setVisible(on and kind.currentData() in ("exponential", "logarithmic",
                                                          "power"))
            size_row.setVisible(bool(d.get("size")))

        follow()

        def field(key, box):
            def changed(*_a):
                d[key] = box.current_name()
                follow()
                self._changed(True)
            box.currentIndexChanged.connect(changed)

        field("x", x)
        field("y", y)
        field("size", size)

        def other(*_a):
            d["size_min"] = smin.value()
            d["size_max"] = max(smax.value(), smin.value() + 1)
            d["trend"] = trend.isChecked()
            d["trend_type"] = kind.currentData()
            d["order"] = order.value()
            follow()
            self._changed(True)

        for w in (smin, smax, order):
            w.valueChanged.connect(other)
        kind.currentIndexChanged.connect(other)
        trend.toggled.connect(other)

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
            hl.addWidget(W.label("Y-axis", "dim"))
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
        agg_row = self._row("Aggregation", agg, "How the Y field of each "
                            + ("interval or value" if t == "line" else "category")
                            + "'s records is summarised (NULL values left out)")
        split = self._field_combo(d.get("split"), _is_split, none_label="None")
        self._row("Split by", split, "One series per category of this field (with one "
                  "Y field): the 8 with the most records get their own colour, the rest "
                  "form Other")
        bars = sort = None
        if t == "bar":
            bars = self._combo(P.MULTI_SERIES, d.get("multi", "side"))
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

    # ---- Axes ----------------------------------------------------------
    def _axes(self):
        axes = self.plot["axes"]
        t = self.plot["type"]
        self._header("Axes")
        edits = {}
        keys = (["x_min", "x_max"] if t != "bar" else []) + ["y_min", "y_max"]
        labels = {"x_min": "X minimum", "x_max": "X maximum", "y_min": "Y minimum",
                  "y_max": "Y maximum"}
        for key in keys:
            e = self._edit("" if axes.get(key) is None else str(axes.get(key)), "Automatic")
            self._row(labels[key], e, "Empty: from the data (dates as YYYY-MM-DD)")
            edits[key] = e
        x_log = None
        if t != "bar":
            x_log = self._tick("Logarithmic X axis", axes.get("x_log"),
                               "Values of 0 or less cannot be shown on a log axis")
        y_log = self._tick("Logarithmic Y axis", axes.get("y_log"),
                           "Values of 0 or less cannot be shown on a log axis")

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
        if t == "bar":
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
        title = self._edit(gen.get("title"), P.default_title(self.plot))
        self._row("Title", title)
        x_title = self._edit(gen.get("x_title"), "From the field")
        self._row("X axis title", x_title)
        y_title = self._edit(gen.get("y_title"), "From the field")
        self._row("Y axis title", y_title)
        desc = self._edit(gen.get("description"), "Shown under the plot")
        self._row("Description", desc)
        if self.plot["type"] == "scatter":
            mode = self._combo((("layer", "Layer symbology"), ("single", "One colour")),
                               self.plot["data"].get("colour", "layer"))
            self._row("Points", mode, "Layer symbology: each point in the colour of its "
                      "legend class, as on the map")

            def changed_mode(*_a):
                self.plot["data"]["colour"] = mode.currentData()
                self._fill_colours()
                self._changed(True)

            mode.currentIndexChanged.connect(changed_mode)
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
        if t == "scatter":
            if self.plot["data"].get("colour", "layer") == "layer":
                return
            names = [None]
        else:
            names = self.series or []
        for s, name in enumerate(names):
            btn = QgsColorButton()
            btn.setFixedHeight(self.metrics.control_h)
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
                text = "Point colour"
            elif len(names) == 1:
                text = "Line colour" if t == "line" else "Bar colour"
            else:
                text = str(name)
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(self.metrics.gap)
            lbl = W.ElideLabel(text)
            lbl.setObjectName("gtsDim")
            lbl.setMinimumWidth(60)
            row.addWidget(lbl, 1)
            row.addWidget(btn, 1)
            lay.addWidget(holder)

    def refresh_title_hint(self):
        if self.title_edit is not None and self.plot is not None:
            try:
                self.title_edit.setPlaceholderText(P.default_title(self.plot))
            except RuntimeError:
                pass


# ============================================================================
# THE WINDOW
# ============================================================================

def compute(table, plot):
    """The plot's numbers from the records read (runs in a job for big
    layers)."""
    t = plot["type"]
    d = dict(plot["data"])
    axes = plot.get("axes", {})
    if t == "scatter":
        return P.scatter(table, d, bool(axes.get("x_log")), bool(axes.get("y_log")))
    if d.get("align") == "reference":
        d["reference"] = P.parse_value(d.get("reference"), P.DATE)
    else:
        d["reference"] = None
    if d.get("agg", "count") == "count":
        d["fields"] = []
    elif d.get("split") and d.get("fields"):
        d["fields"] = d["fields"][:1]
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
        self.layer = None
        self.plot_id = None
        self.scope = "all"
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

        # top row, as the Pivot Table's: Layer, the records, the actions
        top = QHBoxLayout()
        top.setSpacing(m.gap)
        top.addWidget(W.label("Layer", "dim"))
        self.layer_combo = W.LayerCombo(m)
        self.layer_combo.setFilters(VECTOR_LAYER_FILTER)
        self.layer_combo.setMinimumWidth(200)
        self.layer_combo.setMaximumWidth(260)
        self.layer_combo.layerChanged.connect(self._on_combo_layer)
        top.addWidget(self.layer_combo)
        top.addSpacing(10)
        self.scope_label = W.ElideLabel()
        self.scope_label.setMinimumWidth(110)
        self.scope_label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                       QSizePolicy.Policy.Preferred)
        top.addWidget(self.scope_label)
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
        split.addWidget(self._build_list())
        self.view = PlotView(self.theme, m)
        self.view.rowsPicked.connect(self._on_rows_picked)
        self.view.rowsMenu.connect(self._on_rows_menu)
        split.addWidget(self.view)
        self.props = PlotProperties(self.theme, m)
        self.props.edited.connect(self._on_edited)
        split.addWidget(self.props)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([230, 800, PlotProperties.WIDTH])
        root.addWidget(split, 1)

        self.status = W.StatusStrip(m)
        self.status.cancelRequested.connect(self._cancel)
        root.addWidget(self.status)
        self.view.set_message("Choose a layer")
        self._update_scope_label()

    def _build_list(self):
        """The layer's plots, in a card like the Pivot Table's Build your
        table: a heading, the list, New plot and the list's buttons."""
        m = self.metrics
        box = QFrame()
        box.setObjectName("gtsCard")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        lay.addWidget(W.label("Plots", "heading"))
        self.plot_list = QListWidget()
        self.plot_list.setObjectName("gtsFormList")
        self.plot_list.setIconSize(QSize(20, 20))
        self.plot_list.currentRowChanged.connect(self._on_row_changed)
        self.plot_list.itemDoubleClicked.connect(lambda _i: self.rename_plot())
        self.plot_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.plot_list.customContextMenuRequested.connect(self._list_menu)
        lay.addWidget(self.plot_list, 1)
        self.new_menu = QMenu(self)
        for key, text in P.TYPES:
            if key in P.BUILT:
                act = self.new_menu.addAction(icons.plot_type(key), text)
                act.triggered.connect(lambda _c=False, k=key: self.new_plot(k))
        self.new_btn = W.button(m, "New plot", icons.native(icons.ADD),
                                "Make a new plot of this layer", menu=self.new_menu)
        new_row = QHBoxLayout()
        new_row.addWidget(self.new_btn)
        new_row.addStretch(1)
        lay.addLayout(new_row)
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
        lay.addLayout(row)
        box.setMinimumWidth(190)
        return box

    # ====================================================================
    # LAYER AND PLOTS
    # ====================================================================

    def window_title(self):
        name = self.layer.name() if self.layer is not None else ""
        return f"Data Plot — {name}" if name else "Data Plot"

    def current_plot(self):
        if self.layer is None or not self.plot_id:
            return None
        return self.store.get(self.layer.id(), self.plot_id)

    def set_layer(self, layer, plot_id=None):
        if layer is not self.layer:
            self._disconnect_layer()
            for runner in (self.reader, self.builder, self.selector):
                runner.new_generation()
            self.layer = layer
            self.table = None
            self._table_info = None
            self.data = None
            self.plot_id = None
            self._revision = 0
            self.layer_combo.blockSignals(True)
            self.layer_combo.setLayer(layer)
            self.layer_combo.blockSignals(False)
            if self.scope == "extent" and layer is not None and not layer.isSpatial():
                self.scope = "all"
            if layer is not None:
                self._connect_layer()
            self.titleChanged.emit()
        self._fill_list(plot_id)
        self._update_scope_button()
        self._update_buttons()
        if layer is None:
            self.props.set_plot(None, None)
            self.view.set_message("Choose a layer")
            self.status.set_status("Ready")

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
            (lyr.nameChanged, self.titleChanged.emit),
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

    def _fill_list(self, select=None):
        lst = self.plot_list
        keep = select or self.plot_id
        lst.blockSignals(True)
        lst.clear()
        plots = self.store.plots(self.layer.id()) if self.layer is not None else []
        row = -1
        for i, plot in enumerate(plots):
            item = QListWidgetItem(icons.plot_type(plot.get("type")), plot.get("name", "Plot"))
            item.setToolTip(f"{plot.get('name', '')}\n{P.TYPE_LABELS.get(plot.get('type'), '')}")
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
        self.plot_id = plot_id
        plot = self.current_plot()
        self.data = None
        self.props.set_plot(copy.deepcopy(plot) if plot else None,
                            self.layer.fields() if self.layer is not None else None)
        self._update_buttons()
        if plot is None:
            if self.layer is not None:
                self.view.set_message(f"{self.layer.name()} has no plots yet.\n"
                                      "Choose New plot to make one.")
                self.status.set_status("Choose New plot to make a plot of this layer")
            return
        self.load()

    def _update_buttons(self):
        has_layer = self.layer is not None
        has_plot = self.current_plot() is not None
        self.new_btn.setEnabled(has_layer)
        for b in (self.dup_btn, self.del_btn, self.up_btn, self.down_btn, self.window_btn,
                  self.export_btn):
            b.setEnabled(has_plot)
        self.scope_btn.setEnabled(has_layer)
        self.clear_btn.setEnabled(has_layer)
        self.more_btn.setEnabled(has_layer)

    def _on_store_changed(self, layer_id, plot_id):
        if self._writing or self._closing:
            return
        if layer_id == "":
            if self.layer is not None:
                self._fill_list()
            return
        if self.layer is None or layer_id != self.layer.id():
            return
        if plot_id and plot_id == self.plot_id:
            self._show_plot(plot_id)              # edited in another window
        else:
            self._fill_list()

    def _write(self, plot):
        self._writing = True
        try:
            self.store.update(self.layer.id(), plot)
        finally:
            self._writing = False

    def new_plot(self, ptype):
        if self.layer is None:
            return
        fields = self.layer.fields()
        pairs = [(fields.at(i).name(), F.field_kind(fields.at(i)))
                 for i in range(fields.count())]
        names = {p.get("name") for p in self.store.plots(self.layer.id())}
        base = P.TYPE_LABELS[ptype]
        name, n = base, 2
        while name in names:
            name, n = f"{base} {n}", n + 1
        plot = P.new_plot(ptype, pairs, name)
        self._writing = True
        try:
            pid = self.store.add(self.layer.id(), plot, after=self.plot_id)
        finally:
            self._writing = False
        self._fill_list(pid)

    def duplicate_plot(self):
        plot = self.current_plot()
        if plot is None:
            return
        twin = copy.deepcopy(plot)
        twin["name"] = f"{plot.get('name', 'Plot')} (copy)"
        self._writing = True
        try:
            pid = self.store.add(self.layer.id(), twin, after=self.plot_id)
        finally:
            self._writing = False
        self._fill_list(pid)

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
            self.store.remove(self.layer.id(), plot["id"])
        finally:
            self._writing = False
        self.plot_id = None
        self._fill_list()

    def _move(self, delta):
        if self.current_plot() is None:
            return
        self._writing = True
        try:
            self.store.move(self.layer.id(), self.plot_id, delta)
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
        body = {k: v for k, v in plot.items() if k != "id"}
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({FILE_TAG: 1, "plot": body}, fh, indent=2)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.status.set_status(f"Plot saved to {path}", "success")

    def load_from_file(self, path=None):
        if self.layer is None:
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
        fields = self.layer.fields()
        missing = P.missing_fields(plot, [fields.at(i).name() for i in range(fields.count())])
        self._writing = True
        try:
            pid = self.store.add(self.layer.id(), plot, after=self.plot_id)
        finally:
            self._writing = False
        self._fill_list(pid)
        if missing:
            self.status.set_status("Plot loaded; this layer lacks the field(s) "
                                   + ", ".join(missing) + ": choose others in Data",
                                   "warning")

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
        if kind == self.scope:
            return
        self.scope = kind
        self._watch_explorer()
        self._update_scope_label()
        self.load()

    def _update_scope_label(self):
        text = f"Records: {SCOPE_LABELS[self.scope]}"
        if self.table is not None and self.layer is not None:
            text += f" ({len(self.table):,})"
        self.scope_label.setText(text)
        self.scope_label.setToolTip("The plots show these records and follow them as "
                                    "they change; Change scope picks others")

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
            self.scope = "all"
            self._update_scope_button()
            self.load()
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
            if exp is None:
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
        if plot["type"] == "scatter" and plot["data"].get("colour", "layer") == "layer":
            classifier = SymbologyClasses(self.layer)
            if not classifier.usable:
                classifier = None
        info = (key, self._revision, classifier is not None,
                tuple(classifier.colours) if classifier else ())
        have = self._table_info
        if (self.table is not None and have is not None and have[0] == info
                and all(n in have[1] for n, _i, _k in wanted)):
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
        if plot["type"] == "scatter":
            out["count"] = int(mask[data.rows].sum()) if len(data) else 0
            return out
        per, per_series = P.selected_per_position(data, mask)
        out["per"], out["per_series"] = per, per_series
        out["count"] = int((mask & (data.group >= 0)).sum())
        if plot["type"] == "bar" and out["count"]:
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
        if plot["type"] == "scatter":
            self.props.set_series([None] if plot["data"].get("colour") == "single" else [])
        else:
            self.props.set_series(list(data.series))
        self._redraw(plot)

    def _redraw(self, plot=None):
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
            m.x_title = gen.get("x_title") or d.get("x") or ""
            m.y_title = gen.get("y_title") or d.get("y") or ""
            if d.get("colour") == "single" or self.table.classes is None:
                m.single = QColor(fmt.get("colour") or self.theme.bar)
            else:
                m.colours = [QColor(c) for c in self.table.class_colours]
            return m
        data = self.data
        x_title = d.get("x") or ""
        if data.bins is not None:
            x_title += f" (by {data.bins.size} {P.UNIT_LABELS[data.bins.unit].lower()})" \
                if data.bins.size != 1 else \
                f" (by {P.UNIT_LABELS[data.bins.unit].lower()[:-1]})"
        m.x_title = gen.get("x_title") or x_title
        agg = data.agg
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
            parts.append(f"{len(data):,} points")
            if data.missing:
                parts.append(f"{data.missing:,} records without an X or Y value left out")
            if data.not_positive:
                parts.append(f"{data.not_positive:,} with values of 0 or less left out "
                             "(log axis)")
            if data.trend is not None and data.trend.ok and data.trend.left_out:
                parts.append(f"the trend leaves out {data.trend.left_out:,} values of "
                             "0 or less")
            from .plot_view import DENSITY_LIMIT
            if len(data) > DENSITY_LIMIT:
                parts.append("shaded by records per spot")
            hint = "Click a point to select its record, drag a box for several"
        else:
            unit = "bars" if plot["type"] == "bar" else "points"
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
        shown = len(data) if plot["type"] == "scatter" else sum(data.records)
        parts.append(f"{n_sel:,} of {shown:,} records selected" if n_sel else hint)
        self.status.set_status("  •  ".join(parts))
        self._update_scope_label()
        self.props.set_summary(self._summary_rows(plot, data, n_sel))

    def _summary_rows(self, plot, data, n_sel):
        """The Summary section's rows (as the Pivot Table chart's)."""
        n = len(self.table) if self.table is not None else 0
        rows = [("Records", F.format_number(n), "Records read: " + SCOPE_LABELS[self.scope])]
        if plot["type"] == "scatter":
            rows.append(("Points", F.format_number(len(data))))
            left = data.missing + data.not_positive
            if left:
                rows.append(("Left out", F.format_number(left),
                             "No X or Y value, or 0 or less on a log axis"))
            if data.trend is not None and data.trend.ok and data.trend.r2 is not None:
                rows.append(("R²", f"{data.trend.r2:.4f}", data.trend.equation))
        else:
            unit = "Bars" if plot["type"] == "bar" else "Points"
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

    def _on_combo_layer(self, layer):
        if layer is not None and layer is not self.layer:
            self.set_layer(layer)

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
        if plot["type"] == "scatter":
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
