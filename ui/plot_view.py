# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Data Plot: the plot itself, drawn with QPainter.

The view draws a :class:`PlotModel` (the plot's settings, the numbers from
:mod:`..core.plots` and the selection) and reports what the user picks as
record indexes of the plot's table: a click on a point, bar or line point,
Ctrl+click to add, a drag for a box (scatter) or a range (bars, lines), a
click on a legend entry for a Split by category or a legend class.

The body of the plot (points, bars, lines, axes) is drawn once into a
cached image; hovering only paints the card and the guide line on top, so
the plot stays responsive with millions of points.  Past
``DENSITY_LIMIT`` points, a scatter plot shades each spot by the records
on it instead of drawing every point.
"""

import datetime
import math

from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap,
)
from qgis.PyQt.QtWidgets import QSizePolicy, QWidget

from ..core import fields as F
from ..core import plots as P
from .chart_view import MEAN_COLOUR, MEDIAN_COLOUR, OTHER_COLOUR, SERIES, nice_step

try:
    import numpy as np
except ImportError:              # pragma: no cover - QGIS ships numpy
    np = None

DENSITY_LIMIT = 100000           # scatter points drawn one by one up to this (measured: ~0.11 s)
CELL = 3                         # density: size of a spot (px)
PICK_RADIUS = 6                  # px around the mouse that picks a point
TREND_COLOUR = {False: "#37474F", True: "#ECEFF1"}
STD_COLOUR = "#8E24AA"
_MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def fill_type_menu(menu, slot):
    """The plot types, each with its icon, in the order of ArcGIS Pro's
    Create Chart list (Data Plot's New plot and Attribute Explorer's Data
    Plot button); *slot* takes the type.  Types not built yet are listed
    greyed out."""
    from . import icons
    for key, text in P.TYPES:
        act = menu.addAction(icons.plot_type(key), text)
        if key in P.BUILT:
            act.triggered.connect(lambda _c=False, k=key: slot(k))
        else:
            act.setText(f"{text}  (coming in round {P.COMING.get(key, '')})")
            act.setEnabled(False)
    return menu


def series_colour(theme, s, name=None, n=0, override=None):
    """Colour of series *s*: the user's, else the colour-blind checked
    series colours; Other in grey."""
    if override:
        c = QColor(override)
        if c.isValid():
            return c
    if name == P.OTHER and s == n - 1:
        return QColor(OTHER_COLOUR[theme.dark])
    return QColor(SERIES[theme.dark][s % len(SERIES[False])])


# ============================================================================
# AXES
# ============================================================================

class Axis:
    """One axis: numbers (linear or log), dates or categories, mapped onto
    pixels by :meth:`place`."""

    def __init__(self, role, lo, hi, log=False, kind=F.REAL, labels=None):
        self.role = role
        self.kind = kind
        self.log = log and role != P.CATEGORY
        self.labels = labels or []
        self.lo, self.hi = float(lo), float(hi)
        if self.hi <= self.lo:
            pad = abs(self.lo) * 0.1 or 1.0
            self.lo, self.hi = self.lo - pad, self.hi + pad
        self.p0 = self.p1 = 0.0

    @classmethod
    def category(cls, labels):
        return cls(P.CATEGORY, -0.5, len(labels) - 0.5, labels=labels)

    def _t(self, v):
        if self.log:
            return math.log10(v) if v > 0 else float("-inf")
        return v

    def place(self, p0, p1):
        self.p0, self.p1 = float(p0), float(p1)

    def map(self, v):
        a, b = self._t(self.lo), self._t(self.hi)
        tv = self._t(v)
        if not math.isfinite(tv):
            return self.p0 if tv < 0 else self.p1
        return self.p0 + (tv - a) / (b - a) * (self.p1 - self.p0)

    def map_np(self, values):
        a, b = self._t(self.lo), self._t(self.hi)
        vals = np.log10(np.where(values > 0, values, np.nan)) if self.log else values
        return self.p0 + (vals - a) / (b - a) * (self.p1 - self.p0)

    def value_at(self, px):
        a, b = self._t(self.lo), self._t(self.hi)
        if self.p1 == self.p0:
            return self.lo
        tv = a + (px - self.p0) / (self.p1 - self.p0) * (b - a)
        return 10 ** tv if self.log else tv

    # ---- ticks ---------------------------------------------------------
    def ticks(self, room=8):
        if self.role == P.CATEGORY:
            return [(float(i), str(t)) for i, t in enumerate(self.labels)]
        if self.role == P.DATE:
            return date_ticks(self.lo, self.hi, room)
        if self.log:
            return log_ticks(self.lo, self.hi)
        step = nice_step(self.hi - self.lo, max(2, room))
        first = math.ceil(self.lo / step - 1e-9) * step
        out, v = [], first
        while v <= self.hi + step * 1e-6:
            out.append((v, self.number_text(v, step)))
            v += step
        return out

    def number_text(self, v, step=None):
        if self.kind == F.TIME:
            return F.key_to_text(v, F.TIME)
        if step is None:
            return value_text(v, self.kind)
        d = 0
        while d < 8 and abs(step * 10 ** d - round(step * 10 ** d)) > 1e-6:
            d += 1
        text = f"{v:,.{d}f}"
        return "0" if not text.strip("-0.,") else text


def nice_range(lo, hi, include_zero=False, pad=0.0):
    """Axis bounds on round numbers around *lo* .. *hi*; with
    *include_zero* the axis reaches 0 and does not go past it."""
    if include_zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    if hi == lo:
        d = abs(hi) * 0.1 or 1.0
        return lo - d, hi + d
    span = hi - lo
    if not (include_zero and lo == 0):
        lo -= span * pad
    if not (include_zero and hi == 0):
        hi += span * pad
    step = nice_step(hi - lo)
    return math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step


def padded(lo, hi, pad=0.04):
    """*lo* .. *hi* widened a little on each side (scatter axes keep to
    the data rather than to round numbers)."""
    span = hi - lo
    if span <= 0:
        d = abs(hi) * 0.1 or 1.0
        return lo - d, hi + d
    return lo - span * pad, hi + span * pad


def log_ticks(lo, hi):
    out = []
    a = math.floor(math.log10(max(lo, 1e-300)))
    b = math.ceil(math.log10(max(hi, 1e-300)))
    decades = b - a
    mults = (1,) if decades > 4 else ((1, 2, 5) if decades > 1 else (1, 2, 3, 5))
    for e in range(a, b + 1):
        for m in mults:
            v = m * 10.0 ** e
            if lo * 0.999 <= v <= hi * 1.001:
                out.append((v, F.format_number(v) if abs(v) >= 1 else f"{v:g}"))
    return out


def date_ticks(lo, hi, room=8):
    """(day, text) ticks on round dates between proleptic days *lo* and *hi*."""
    span = hi - lo
    out = []
    if span > 365 * 2.5:
        years = span / 365.25
        step = 1
        for s in (1, 2, 5, 10, 20, 50, 100, 200, 500):
            step = s
            if years / s <= room:
                break
        start = datetime.date.fromordinal(max(1, int(lo))).year
        y = int(math.ceil(start / step) * step)
        while True:
            try:
                d = datetime.date(y, 1, 1).toordinal()
            except ValueError:
                break
            if d > hi:
                break
            if d >= lo:
                out.append((float(d), str(y)))
            y += step
        return out
    if span > 75:
        months = span / 30.44
        step = 1
        for s in (1, 2, 3, 6):
            step = s
            if months / s <= room:
                break
        d0 = datetime.date.fromordinal(max(1, int(lo)))
        m = d0.year * 12 + d0.month - 1
        m = int(math.ceil(m / step) * step)
        while True:
            y, mm = divmod(m, 12)
            d = datetime.date(y, mm + 1, 1).toordinal()
            if d > hi:
                break
            if d >= lo:
                out.append((float(d), f"{y}-{mm + 1:02d}"))
            m += step
        return out
    if span > 3:
        step = 1
        for s in (1, 2, 7, 14, 28):
            step = s
            if span / s <= room:
                break
        d = int(math.ceil(lo))
        if step >= 7:                                   # weeks start on Monday
            d += (7 - (d - 1) % 7) % 7
        while d <= hi:
            out.append((float(d), datetime.date.fromordinal(d).isoformat()))
            d += step
        return out
    hours = span * 24
    step = 1
    for s in (1 / 60, 5 / 60, 15 / 60, 0.5, 1, 2, 3, 6, 12, 24):
        step = s
        if hours / s <= room:
            break
    step_days = step / 24
    v = math.ceil(lo / step_days - 1e-9) * step_days
    while v <= hi + 1e-12:
        text = F.key_to_text(v, F.DATETIME)
        out.append((v, text[5:16] if span > 1 else text[11:16]))
        v += step_days
    return out


def value_text(v, kind=F.REAL):
    """A value for a hover card or a legend."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "No value"
    if kind in (F.DATE, F.DATETIME):
        return F.key_to_text(v, kind)
    if kind == F.TIME:
        return F.key_to_text(v, F.TIME)
    if float(v).is_integer() and abs(v) < 1e15:
        return F.format_number(int(v))
    a = abs(v)
    places = 2 if a >= 1 else (4 if a >= 0.001 else 8)
    return F.format_number(round(v, places))


# ============================================================================
# MODEL
# ============================================================================

class PlotModel:
    """Everything one drawing needs (set by the window)."""

    def __init__(self):
        self.plot = None             # the plot's settings (dict)
        self.kind = None             # "scatter", "line", "bar"
        self.data = None             # core.plots result
        self.table = None
        self.title = ""
        self.description = ""
        self.x_title = ""
        self.y_title = ""
        self.x_kind = F.REAL
        self.y_kind = F.REAL
        self.y_axis_kind = F.REAL    # value axis of line / bar
        self.colours = []            # QColor per series / class
        self.single = None           # scatter: one colour
        self.sel_mask = None         # numpy bool per table row
        self.sel_per = None          # line / bar: selected records per position
        self.sel_per_series = None   # ... per [series][position]
        self.sel_part = None         # bar: selected part [series][position] (Count / Sum)
        self.sel_count = 0
        self.sel_colour = QColor("#ffff00")
        self.breaks = []             # heat charts: the classes' upper bounds ...
        self.class_colours = []      # ... and their colours


# ============================================================================
# VIEW
# ============================================================================

class PlotView(QWidget):
    rowsPicked = pyqtSignal(object, bool)          # numpy row indexes, add
    rowsMenu = pyqtSignal(object, object)          # numpy row indexes, global pos
    previewPicked = pyqtSignal(str)                # histogram: category to preview

    def __init__(self, theme, metrics, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.metrics = metrics
        self.model = None
        self.message = ""
        self._cache = None
        self._geo = None
        self._hover = None
        self._press = None
        self._drag = None
        self._sprites = {}
        self.setMouseTracking(True)
        self.setMinimumSize(QSize(360, 260))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self):
        return QSize(720, 480)

    # ------------------------------------------------------------------
    def set_model(self, model):
        self.model = model
        self.message = ""
        self._hover = None
        self._cache = None
        self.update()

    def set_message(self, text):
        self.message = text
        self._hover = None
        self._cache = None
        self.update()

    def set_theme(self, theme):
        self.theme = theme
        self._sprites = {}
        self._cache = None
        self.update()

    def resizeEvent(self, event):
        self._cache = None
        super().resizeEvent(event)

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------
    def render_image(self, scale=2.0):
        img = QImage(int(self.width() * scale), int(self.height() * scale),
                     QImage.Format.Format_ARGB32)
        img.fill(QColor(self.theme.base))
        p = QPainter(img)
        p.scale(scale, scale)
        self.paint_plot(p, QRectF(0, 0, self.width(), self.height()), scale)
        p.end()
        return img

    def render_svg(self, path, title=""):
        from qgis.PyQt.QtSvg import QSvgGenerator
        gen = QSvgGenerator()
        gen.setFileName(path)
        gen.setSize(QSize(self.width(), self.height()))
        gen.setViewBox(QRectF(0, 0, self.width(), self.height()))
        gen.setTitle(title)
        p = QPainter(gen)
        self.paint_plot(p, QRectF(0, 0, self.width(), self.height()), 1.0, vector=True)
        p.end()

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        if self._cache is None or self._cache.size() != self.size() * self._ratio():
            ratio = self._ratio()
            pm = QPixmap(self.size() * ratio)
            pm.setDevicePixelRatio(ratio)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            self.paint_plot(q, QRectF(self.rect()), ratio)
            q.end()
            self._cache = pm
        p.drawPixmap(0, 0, self._cache)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_drag(p)
        self._paint_hover(p)
        p.end()

    def _ratio(self):
        try:
            return max(1.0, float(self.devicePixelRatioF()))
        except AttributeError:
            return 1.0

    def paint_plot(self, p, rect, ratio=1.0, vector=False):
        t = self.theme
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(t.border), 1))
        p.setBrush(QColor(t.base))
        p.drawRoundedRect(r, 4, 4)
        self._geo = {"plot": None, "items": [], "legend": [], "x": None, "y": None,
                     "points": None, "wedges": [], "minis": []}
        base = QFont(self.font())
        p.setFont(base)
        fm = QFontMetrics(base)
        m = self.model
        if self.message or m is None or m.data is None:
            p.setPen(QColor(t.text_dim))
            p.drawText(r.adjusted(20, 20, -20, -20),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self.message or "")
            return
        top = r.top() + 10
        if m.title:
            title = QFont(base)
            title.setPointSizeF(base.pointSizeF() + 3 if base.pointSizeF() > 0 else 12)
            title.setBold(True)
            p.setFont(title)
            p.setPen(QColor(t.text_strong))
            tfm = QFontMetrics(title)
            p.drawText(QRectF(r.left() + 10, top, r.width() - 20, tfm.height() + 4),
                       Qt.AlignmentFlag.AlignCenter,
                       tfm.elidedText(m.title, Qt.TextElideMode.ElideRight, int(r.width() - 20)))
            top += tfm.height() + 10
            p.setFont(base)
        bottom = r.bottom() - 8
        if m.description:
            dh = fm.height() * 2 + 4
            p.setPen(QColor(t.text_dim))
            p.drawText(QRectF(r.left() + 14, bottom - dh, r.width() - 28, dh),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                       | Qt.TextFlag.TextWordWrap, m.description)
            bottom -= dh + 4
        legend = self._legend_entries()
        gen = m.plot.get("general", {})
        show_legend = bool(legend) and gen.get("legend", True)
        place = gen.get("legend_pos", "auto")
        if place not in ("right", "bottom"):
            # as Visualize Statistics: inside the plot, beside it for series
            place = "right" if any(e[0] == "box" for e in legend) else "inside"
        lw = 0
        lh = 0
        inside_w = 0
        if show_legend:
            texts = [e[2] for e in legend]
            widest = int(min(max(fm.horizontalAdvance(s) for s in texts) + 44,
                             max(240, r.width() * 0.3)))
            if place == "bottom":
                lh = self._legend_height_bottom(fm, texts, r.width() - 40)
            elif place == "right":
                lw = widest
            else:
                inside_w = widest
        area = QRectF(r.left() + 10, top, r.width() - 20 - (lw + 12 if lw else 0),
                      bottom - top - (lh + 8 if lh else 0))
        if m.kind == "scatter":
            self._paint_scatter(p, area, fm, ratio, vector)
        elif m.kind == "line":
            self._paint_series(p, area, fm, line=True)
        elif m.kind == "bar":
            self._paint_series(p, area, fm, line=False)
        elif m.kind == "combo":
            self._paint_combo(p, area, fm)
        elif m.kind == "pie":
            self._paint_pie(p, area, fm)
        elif m.kind == "histogram":
            self._paint_histogram(p, area, fm)
        elif m.kind == "box":
            self._paint_box(p, area, fm, ratio, vector)
        elif m.kind == "qq":
            self._paint_scatter(p, area, fm, ratio, vector)
            self._paint_qq_reference(p)
        elif m.kind == "heat":
            self._paint_heat(p, area, fm)
        elif m.kind == "calendar":
            self._paint_calendar(p, area, fm)
        elif m.kind == "clock":
            self._paint_clock(p, area, fm)
        elif m.kind == "matrix":
            self._paint_matrix(p, area, fm, ratio)
        if show_legend:
            plot = self._geo["plot"] or area
            if place == "bottom":
                box = QRectF(r.left() + 20, bottom - lh, r.width() - 40, lh)
            elif place == "right":
                box = QRectF(area.right() + 12, plot.top(), lw, 0)
            else:
                box = self._inside_box(plot, min(inside_w, plot.width() * 0.6),
                                       10 + 20 * len(legend))
            self._paint_legend(p, box, legend, fm, place == "bottom")

    # ---- frame: axes, grid, guides --------------------------------------
    def _frame(self, p, area, fm, xa, ya, x_title, y_title, rotate_x=False, ya2=None,
               y2_title="", grid=None):
        """Lay out *area* around the axes (*ya2*: a second value axis on the
        right) and draw the grid, the axes and their labels; returns the
        plot rectangle."""
        t, m = self.theme, self.model
        yt = ya.ticks(max(2, int(area.height() / 45)))
        ywidth = max([fm.horizontalAdvance(s) for _v, s in yt] + [20])
        left = area.left() + ywidth + 10 + (fm.height() + 6 if y_title else 0)
        y2t, y2width, right_room = [], 0, 8
        if ya2 is not None:
            y2t = ya2.ticks(max(2, int(area.height() / 45)))
            y2width = max([fm.horizontalAdvance(s) for _v, s in y2t] + [20])
            right_room = y2width + 14 + (fm.height() + 6 if y2_title else 0)
        xt = xa.ticks(max(2, int((area.width() - ywidth) / 90)))
        if xa.role == P.CATEGORY:
            slant = self._category_slant(fm, xt, area.width() - ywidth - 30)
        else:
            slant = 0
        label_h = fm.height() + 6 if not slant else min(
            110, max(fm.horizontalAdvance(s) for _v, s in xt) * 0.72 + 12)
        bottom = area.bottom() - label_h - (fm.height() + 6 if x_title else 0)
        plot = QRectF(left, area.top() + 6, area.right() - left - right_room,
                      bottom - area.top() - 6)
        if plot.width() < 40 or plot.height() < 40:
            plot = QRectF(area.left(), area.top(), max(40.0, area.width()),
                          max(40.0, area.height()))
        xa.place(plot.left(), plot.right())
        ya.place(plot.bottom(), plot.top())
        if ya2 is not None:
            ya2.place(plot.bottom(), plot.top())
            self._geo["y2"] = ya2
        self._geo["plot"] = plot
        self._geo["x"], self._geo["y"] = xa, ya
        fmt = m.plot.get("format", {})
        if grid is None:
            grid = fmt.get("grid", True)
        p.setPen(QPen(QColor(t.border_soft), 1))
        if grid:
            for v, _s in yt:
                y = ya.map(v)
                if plot.top() - 0.5 <= y <= plot.bottom() + 0.5:
                    p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            if xa.role != P.CATEGORY:
                for v, _s in xt:
                    x = xa.map(v)
                    if plot.left() - 0.5 <= x <= plot.right() + 0.5:
                        p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        # tick labels
        p.setPen(QColor(t.text_dim))
        for v, s in yt:
            y = ya.map(v)
            if plot.top() - 1 <= y <= plot.bottom() + 1:
                p.drawText(QRectF(plot.left() - ywidth - 8, y - 9, ywidth, 18),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, s)
        for v, s in y2t:
            y = ya2.map(v)
            if plot.top() - 1 <= y <= plot.bottom() + 1:
                p.drawText(QRectF(plot.right() + 8, y - 9, y2width, 18),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, s)
        if xa.role == P.CATEGORY:
            self._category_labels(p, plot, xa, xt, fm, slant)
        else:
            last_right = -1e9
            for v, s in xt:
                x = xa.map(v)
                if not (plot.left() - 1 <= x <= plot.right() + 1):
                    continue
                w = fm.horizontalAdvance(s)
                if x - w / 2 < last_right + 6:
                    continue
                p.drawText(QRectF(x - w / 2 - 2, plot.bottom() + 4, w + 4, fm.height()),
                           Qt.AlignmentFlag.AlignCenter, s)
                last_right = x + w / 2
        # titles
        p.setPen(QColor(t.text))
        if x_title:
            p.drawText(QRectF(plot.left(), area.bottom() - fm.height() - 2, plot.width(),
                              fm.height() + 2), Qt.AlignmentFlag.AlignCenter,
                       fm.elidedText(x_title, Qt.TextElideMode.ElideRight, int(plot.width())))
        if y_title:
            p.save()
            p.translate(area.left() + fm.height() / 2 + 2, plot.center().y())
            p.rotate(-90)
            p.drawText(QRectF(-plot.height() / 2, -fm.height() / 2, plot.height(),
                              fm.height() + 2), Qt.AlignmentFlag.AlignCenter,
                       fm.elidedText(y_title, Qt.TextElideMode.ElideRight, int(plot.height())))
            p.restore()
        if y2_title and ya2 is not None:
            p.save()
            p.translate(area.right() - fm.height() / 2 - 2, plot.center().y())
            p.rotate(90)
            p.drawText(QRectF(-plot.height() / 2, -fm.height() / 2, plot.height(),
                              fm.height() + 2), Qt.AlignmentFlag.AlignCenter,
                       fm.elidedText(y2_title, Qt.TextElideMode.ElideRight, int(plot.height())))
            p.restore()
        if fmt.get("axis_lines", True):
            p.setPen(QPen(QColor(t.text_dim), 1))
            p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
            p.drawLine(QPointF(plot.left(), plot.top()), QPointF(plot.left(), plot.bottom()))
            if ya2 is not None:
                p.drawLine(QPointF(plot.right(), plot.top()),
                           QPointF(plot.right(), plot.bottom()))
        return plot

    def _paint_guides(self, p, plot, fm):
        t, m = self.theme, self.model
        xa, ya = self._geo["x"], self._geo["y"]
        colour = QColor(t.danger)
        for g in m.plot.get("guides", []):
            vertical = g.get("axis") == "vertical"
            axis = xa if vertical else ya
            if axis.role == P.CATEGORY:
                continue
            v = P.parse_value(g.get("value"), axis.role)
            if v is None:
                continue
            to = P.parse_value(g.get("to"), axis.role)
            a = axis.map(v)
            if to is not None:
                b = axis.map(to)
                band = QColor(colour)
                band.setAlpha(40)
                if vertical:
                    rect = QRectF(min(a, b), plot.top(), abs(b - a), plot.height())
                else:
                    rect = QRectF(plot.left(), min(a, b), plot.width(), abs(b - a))
                p.fillRect(rect.intersected(plot), band)
            pen = QPen(colour, 1.6)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            if vertical:
                if plot.left() <= a <= plot.right():
                    p.drawLine(QPointF(a, plot.top()), QPointF(a, plot.bottom()))
            elif plot.top() <= a <= plot.bottom():
                p.drawLine(QPointF(plot.left(), a), QPointF(plot.right(), a))
            label = g.get("label") or ""
            if label:
                p.setPen(colour)
                if vertical:
                    p.drawText(QRectF(a + 4, plot.top() + 2, 220, fm.height()),
                               Qt.AlignmentFlag.AlignLeft, label)
                else:
                    p.drawText(QRectF(plot.right() - 224, a - fm.height() - 1, 220,
                                      fm.height()), Qt.AlignmentFlag.AlignRight, label)

    def _category_slant(self, fm, ticks, width):
        if not ticks:
            return 0
        widest = max(fm.horizontalAdvance(s) for _v, s in ticks)
        return 0 if widest + 8 <= width / max(1, len(ticks)) else 1

    def _category_labels(self, p, plot, xa, ticks, fm, slant):
        n = len(ticks)
        band = plot.width() / max(1, n)
        step = max(1, int(math.ceil((fm.height() + 2) / max(band, 1)))) if slant else 1
        for i, (v, s) in enumerate(ticks):
            if i % step:
                continue
            x = xa.map(v)
            if not slant:
                p.drawText(QRectF(x - band / 2, plot.bottom() + 4, band, fm.height()),
                           Qt.AlignmentFlag.AlignCenter,
                           fm.elidedText(s, Qt.TextElideMode.ElideRight, int(band)))
                continue
            p.save()
            p.translate(x, plot.bottom() + 6)
            p.rotate(-40)
            text = fm.elidedText(s, Qt.TextElideMode.ElideRight, 140)
            w = fm.horizontalAdvance(text)
            p.drawText(QRectF(-w - 2, -fm.height() / 2, w + 2, fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            p.restore()

    # ---- bounds --------------------------------------------------------
    def _bounds(self, which, lo, hi, axis_role):
        axes = self.model.plot.get("axes", {})
        vmin = P.parse_value(axes.get(f"{which}_min"), axis_role)
        vmax = P.parse_value(axes.get(f"{which}_max"), axis_role)
        if vmin is not None:
            lo = vmin
        if vmax is not None:
            hi = vmax
        return lo, hi

    # ---- scatter -------------------------------------------------------
    def _paint_scatter(self, p, area, fm, ratio, vector=False):
        t, m = self.theme, self.model
        d = m.data
        axes = m.plot.get("axes", {})
        x_log, y_log = bool(axes.get("x_log")), bool(axes.get("y_log"))
        xr, yr = P.role(d.x_kind), P.role(d.y_kind)
        if len(d):
            xlo, xhi = float(d.x.min()), float(d.x.max())
            ylo, yhi = float(d.y.min()), float(d.y.max())
        else:
            xlo, xhi, ylo, yhi = 0.0, 1.0, 0.0, 1.0
        if not x_log:
            xlo, xhi = padded(xlo, xhi)
        if not y_log:
            ylo, yhi = padded(ylo, yhi)
        if x_log:
            xlo, xhi = xlo / 1.3, xhi * 1.3
        if y_log:
            ylo, yhi = ylo / 1.3, yhi * 1.3
        xlo, xhi = self._bounds("x", xlo, xhi, xr)
        ylo, yhi = self._bounds("y", ylo, yhi, yr)
        xa = Axis(xr, xlo, xhi, x_log, d.x_kind)
        ya = Axis(yr, ylo, yhi, y_log, d.y_kind)
        plot = self._frame(p, area, fm, xa, ya, m.x_title, m.y_title)
        self._paint_guides(p, plot, fm)
        if not len(d):
            return
        sx = xa.map_np(d.x)
        sy = ya.map_np(d.y)
        inside = ((sx >= plot.left() - 0.5) & (sx <= plot.right() + 0.5)
                  & (sy >= plot.top() - 0.5) & (sy <= plot.bottom() + 0.5))
        self._geo["points"] = (sx, sy, inside)
        p.save()
        p.setClipRect(plot)
        dense = len(d) > DENSITY_LIMIT
        self._geo["dense"] = dense
        if dense:
            self._paint_density(p, plot, sx, sy, inside, ratio)
        else:
            self._paint_points(p, sx, sy, inside, ratio, vector)
        if d.trend is not None and d.trend.ok:
            xs = np.linspace(xa.lo, xa.hi, 240) if not x_log else \
                np.logspace(math.log10(xa.lo), math.log10(xa.hi), 240)
            ys = d.trend.at(xs)
            pen = QPen(QColor(TREND_COLOUR[t.dark]), 2)
            p.setPen(pen)
            path = QPainterPath()
            started = False
            for x, y in zip(xa.map_np(xs), ya.map_np(ys) if len(ys) else []):
                if not (math.isfinite(x) and math.isfinite(y)) or abs(y) > 1e7:
                    started = False
                    continue
                if started:
                    path.lineTo(x, y)
                else:
                    path.moveTo(x, y)
                    started = True
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.restore()

    def _point_colours(self):
        m, d = self.model, self.model.data
        if d.classes is not None and m.single is None:
            return [QColor(c) for c in m.colours]
        return None

    def _sizes(self):
        """Diameter (px) of each point: Size by between the smallest and the
        largest size, else the plain point size."""
        m, d = self.model, self.model.data
        data = m.plot.get("data", {})
        if d.size is None or d.size_range is None:
            return None
        smin = float(data.get("size_min", 4))
        smax = float(data.get("size_max", 20))
        lo, hi = d.size_range
        s = np.where(np.isnan(d.size), lo, d.size)
        if hi > lo:
            return smin + (s - lo) / (hi - lo) * (smax - smin)
        return np.full(len(s), (smin + smax) / 2.0)

    def _sprite(self, colour, dia, ratio):
        """A point (filled circle with a darker outline) drawn once, at the
        painter's scale, then copied to every point of that colour and
        size: about 20 times faster than drawing each circle."""
        key = (colour.rgba(), float(dia), float(ratio))
        sprite = self._sprites.get(key)
        if sprite is None:
            side = int(math.ceil(dia + 3))
            img = QImage(int(math.ceil(side * ratio)), int(math.ceil(side * ratio)),
                         QImage.Format.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(ratio)
            img.fill(Qt.GlobalColor.transparent)
            q = QPainter(img)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.setPen(QPen(QColor(colour).darker(150), 1.2))
            q.setBrush(colour)
            q.drawEllipse(QPointF(side / 2.0, side / 2.0), dia / 2.0, dia / 2.0)
            q.end()
            sprite = (QPixmap.fromImage(img), side / 2.0)
            if len(self._sprites) > 400:
                self._sprites.clear()
            self._sprites[key] = sprite
        return sprite

    def _paint_points(self, p, sx, sy, inside, ratio=1.0, vector=False):
        t, m, d = self.theme, self.model, self.model.data
        sizes = self._sizes()
        colours = self._point_colours()
        default = QColor(m.single) if m.single is not None else QColor(t.bar)
        idx = np.nonzero(inside)[0]
        sel = m.sel_mask[d.rows] if m.sel_mask is not None and len(m.sel_mask) else None
        if sizes is None:
            dia = np.full(len(d), 7.0)
        else:
            dia = np.round(sizes * 2) / 2.0
        self._geo["dia"] = dia

        def draw(ids, colour):
            if not len(ids):
                return
            for size in np.unique(dia[ids]):
                part = ids[dia[ids] == size]
                if vector:                       # SVG: true circles
                    outline = QPen(QColor(colour).darker(150), 1.2)
                    p.setPen(outline)
                    p.setBrush(colour)
                    r = float(size) / 2.0
                    for a, b in zip(sx[part].tolist(), sy[part].tolist()):
                        p.drawEllipse(QPointF(a, b), r, r)
                    continue
                pm, half = self._sprite(colour, float(size), ratio)
                for a, b in zip((sx[part] - half).tolist(), (sy[part] - half).tolist()):
                    p.drawPixmap(QPointF(a, b), pm)

        if sizes is not None:
            idx = idx[np.argsort(-dia[idx], kind="stable")]       # small ones on top
        plain = idx if sel is None else idx[~sel[idx]]
        if colours is None:
            draw(plain, default)
        else:
            cls = d.classes[plain]
            for c in np.unique(cls):
                colour = colours[c] if 0 <= c < len(colours) else QColor(OTHER_COLOUR[t.dark])
                draw(plain[cls == c], colour)
        if sel is not None:
            draw(idx[sel[idx]], QColor(m.sel_colour))

    def _paint_density(self, p, plot, sx, sy, inside, ratio):
        t, m, d = self.theme, self.model, self.model.data
        w = max(1, int(plot.width() / CELL))
        h = max(1, int(plot.height() / CELL))
        idx = np.nonzero(inside)[0]
        cx = np.clip(((sx[idx] - plot.left()) / CELL).astype(np.int64), 0, w - 1)
        cy = np.clip(((sy[idx] - plot.top()) / CELL).astype(np.int64), 0, h - 1)
        colours = self._point_colours()
        classes = d.classes[idx] if colours is not None else None
        counts, dominant = P.density(cx, cy, w, h, classes, len(colours or []))
        self._geo["density"] = (w, h, counts, cx, cy, idx)
        top = max(1, int(counts.max()))
        alpha = np.zeros(counts.shape)
        has = counts > 0
        alpha[has] = 0.28 + 0.72 * np.log1p(counts[has]) / math.log1p(top)
        base = QColor(m.single) if m.single is not None else QColor(t.bar)
        rgb = np.zeros((h, w, 3))
        rgb[:] = (base.red(), base.green(), base.blue())
        if dominant is not None:
            table = np.array([(c.red(), c.green(), c.blue()) for c in colours] or
                             [(base.red(), base.green(), base.blue())], dtype=float)
            ok = dominant >= 0
            rgb[ok] = table[dominant[ok]]
        sel = m.sel_mask[d.rows][idx] if m.sel_mask is not None and len(m.sel_mask) else None
        if sel is not None and sel.any():
            sc = np.bincount((cy[sel] * w + cx[sel]), minlength=w * h)[:w * h].reshape(h, w)
            s_col = QColor(m.sel_colour)
            on = sc > 0
            rgb[on] = (s_col.red(), s_col.green(), s_col.blue())
            alpha[on] = np.maximum(alpha[on], 0.85)
        argb = np.zeros((h, w), dtype=np.uint32)
        argb |= (np.clip(alpha * 255, 0, 255).astype(np.uint32) << 24)
        argb |= (rgb[..., 0].astype(np.uint32) << 16)
        argb |= (rgb[..., 1].astype(np.uint32) << 8)
        argb |= rgb[..., 2].astype(np.uint32)
        buf = np.ascontiguousarray(argb)
        img = QImage(buf.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
        p.drawImage(QRectF(plot.left(), plot.top(), w * CELL, h * CELL), img)
        self._geo["density_top"] = top

    # ---- line and bar --------------------------------------------------
    def _series_axes(self, line):
        m, d = self.model, self.model.data
        axes = m.plot.get("axes", {})
        data = m.plot.get("data", {})
        y_log = bool(axes.get("y_log"))
        multi = data.get("multi", "side")
        values = [np.asarray(v, dtype=np.float64) for v in d.values]
        empty = data.get("empty", "zero")
        if line and empty == "zero" and d.x_role == P.DATE:
            values = [np.where(np.isnan(v), 0.0, v) for v in values]
        if not line and multi == "stacked100" and len(values) > 1:
            stack = np.vstack(values)
            tot = np.nansum(np.abs(stack), axis=0)
            tot[tot == 0] = np.nan
            values = [v / tot * 100.0 for v in stack]
        if not line and multi in ("stacked", "stacked100") and len(values) > 1:
            stack = np.vstack([np.where(np.isnan(v), 0.0, v) for v in values])
            hi = float(np.where(stack > 0, stack, 0).sum(axis=0).max()) if stack.size else 0
            lo = float(np.where(stack < 0, stack, 0).sum(axis=0).min()) if stack.size else 0
        else:
            finite = [v[np.isfinite(v)] for v in values]
            finite = [v for v in finite if len(v)]
            hi = max(float(v.max()) for v in finite) if finite else 1.0
            lo = min(float(v.min()) for v in finite) if finite else 0.0
        if y_log:
            pos = [v[np.isfinite(v) & (v > 0)] for v in values]
            pos = [v for v in pos if len(v)]
            lo = min(float(v.min()) for v in pos) / 1.5 if pos else 1.0
            hi = max(float(v.max()) for v in pos) * 1.5 if pos else 10.0
        elif line:
            if d.agg == "count":
                lo = min(lo, 0.0)
            lo, hi = padded(lo, hi, 0.04)
            if d.agg == "count" and lo < 0 <= min(v.min() for v in values if len(v)):
                lo = 0.0
        else:
            lo, hi = nice_range(lo, hi, include_zero=True)
        lo, hi = self._bounds("y", lo, hi, P.NUMBER)
        ya = Axis(P.NUMBER, lo, hi, y_log, m.y_axis_kind)
        if d.x_role == P.CATEGORY or not line:
            xa = Axis.category(d.labels)
        elif d.x_role == P.DATE:
            xs = d.starts or [0.0]
            lo_x, hi_x = min(xs), max(d.ends or xs)
            lo_x, hi_x = self._bounds("x", lo_x, hi_x, P.DATE)
            xa = Axis(P.DATE, lo_x, hi_x, kind=d.x_kind)
        else:
            xs = d.xs or [0.0]
            lo_x, hi_x = nice_range(min(xs), max(xs), pad=0.02)
            lo_x, hi_x = self._bounds("x", lo_x, hi_x, P.NUMBER)
            xa = Axis(P.NUMBER, lo_x, hi_x, bool(axes.get("x_log")) and min(xs) > 0,
                      d.x_kind)
        return xa, ya, values

    def _paint_series(self, p, area, fm, line):
        m, d = self.model, self.model.data
        xa, ya, values = self._series_axes(line)
        plot = self._frame(p, area, fm, xa, ya, m.x_title, m.y_title)
        self._paint_guides(p, plot, fm)
        if not len(d):
            return
        p.save()
        p.setClipRect(plot.adjusted(-6, -6, 6, 6))
        if line:
            self._paint_lines(p, plot, xa, ya, values)
        else:
            self._paint_bars(p, plot, xa, ya, values)
        p.restore()

    def _x_of(self, xa, i):
        d = self.model.data
        if xa.role == P.CATEGORY:
            return xa.map(i)
        if d.x_role == P.DATE:
            return xa.map(d.starts[i])
        return xa.map(d.xs[i])

    def _paint_lines(self, p, plot, xa, ya, values):
        t, m, d = self.theme, self.model, self.model.data
        empty = m.plot.get("data", {}).get("empty", "zero")
        n = len(d)
        xs = [self._x_of(xa, i) for i in range(n)]
        for s, vals in enumerate(values):
            colour = m.colours[s] if s < len(m.colours) else QColor(t.bar)
            path = QPainterPath()
            started = False
            for i in range(n):
                v = vals[i]
                if not math.isfinite(v) or (ya.log and v <= 0):
                    if empty == "break":
                        started = False
                    continue
                pt = QPointF(xs[i], ya.map(v))
                if started:
                    path.lineTo(pt)
                else:
                    path.moveTo(pt)
                    started = True
                self._geo["items"].append((QRectF(pt.x() - 5, pt.y() - 5, 10, 10), i, s))
            p.setPen(QPen(colour, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            p.setBrush(colour)
            p.setPen(QPen(QColor(t.base), 1))
            dot = 2.6 if n <= 400 else 0
            sel_row = None
            if m.sel_per_series is not None and s < len(m.sel_per_series):
                sel_row = m.sel_per_series[s]
            elif m.sel_per is not None:
                sel_row = m.sel_per
            for i in range(n):
                v = vals[i]
                if not math.isfinite(v) or (ya.log and v <= 0):
                    continue
                y = ya.map(v)
                if dot:
                    p.setBrush(colour)
                    p.setPen(QPen(QColor(t.base), 1))
                    p.drawEllipse(QPointF(xs[i], y), dot, dot)
                if sel_row is not None and i < len(sel_row) and sel_row[i]:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(QColor(m.sel_colour), 2.4))
                    p.drawEllipse(QPointF(xs[i], y), 5.5, 5.5)

    def _paint_bars(self, p, plot, xa, ya, values):
        t, m, d = self.theme, self.model, self.model.data
        data = m.plot.get("data", {})
        multi = data.get("multi", "side") if len(values) > 1 else "side"
        n = len(d)
        k = len(values)
        band = plot.width() / max(1, n)
        gap = band * 0.18 if band > 6 else 0
        base_v = ya.lo if ya.log else 0.0
        base_y = ya.map(max(base_v, ya.lo)) if not ya.log else plot.bottom()
        part = m.sel_part
        for i in range(n):
            x0 = xa.map(i) - band / 2 + gap / 2
            width = band - gap
            pos_acc = neg_acc = 0.0
            for s in range(k):
                v = values[s][i]
                if not math.isfinite(v):
                    continue
                colour = m.colours[s] if s < len(m.colours) else QColor(t.bar)
                if multi == "side":
                    w = width / k
                    x = x0 + s * w
                    lo_v, hi_v = (base_v, v) if v >= 0 else (v, base_v)
                else:
                    w = width
                    x = x0
                    if v >= 0:
                        lo_v, hi_v = pos_acc, pos_acc + v
                        pos_acc += v
                    else:
                        lo_v, hi_v = neg_acc + v, neg_acc
                        neg_acc += v
                if ya.log:
                    if hi_v <= 0:
                        continue
                    y_hi = ya.map(hi_v)
                    y_lo = ya.map(lo_v) if lo_v > 0 else base_y
                else:
                    y_hi, y_lo = ya.map(hi_v), ya.map(lo_v)
                rect = QRectF(x, min(y_hi, y_lo), max(w - (1 if multi == "side" and k > 1
                                                          else 0), 1.0),
                              max(abs(y_lo - y_hi), 0.8))
                p.fillRect(rect, colour)
                self._geo["items"].append((rect, i, s))
                self._paint_bar_selection(p, rect, i, s, v, multi, part)

    def _paint_bar_selection(self, p, rect, i, s, v, multi, part):
        """The selected part of a bar: its selected records (Count) or their
        sum (Sum) from the bar's base; other aggregations: an outline when
        the bar holds selected records."""
        m = self.model
        sel_n = None
        if m.sel_per_series is not None and s < len(m.sel_per_series):
            sel_n = m.sel_per_series[s][i]
        elif m.sel_per is not None and i < len(m.sel_per):
            sel_n = m.sel_per[i]
        if not sel_n:
            return
        colour = QColor(m.sel_colour)
        raw = m.data.values[s][i] if s < len(m.data.values) else v
        if part is not None and s < len(part) and part[s] is not None and raw:
            share = max(0.0, min(1.0, part[s][i] / raw))
            if v >= 0:
                h = rect.height() * share
                sel = QRectF(rect.left(), rect.bottom() - h, rect.width(), h)
            else:
                h = rect.height() * share
                sel = QRectF(rect.left(), rect.top(), rect.width(), h)
            if h > 0:
                p.fillRect(sel, colour)
                p.setPen(QPen(colour.darker(170), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(sel.adjusted(0.5, 0.5, -0.5, -0.5))
            return
        # a mean, median, minimum or maximum has no selected part: a strip
        # at the bar's base marks a bar that holds selected records
        strip = min(5.0, max(2.0, rect.height()))
        base = QRectF(rect.left(), rect.bottom() - strip if v >= 0 else rect.top(),
                      rect.width(), strip)
        p.fillRect(base, colour)
        p.setPen(QPen(colour.darker(170), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(base.adjusted(0.5, 0.5, -0.5, -0.5))

    # ---- combo ---------------------------------------------------------
    def _value_axis(self, values, bars, log=False, which="y"):
        """A value axis for *values* (lists of floats); one holding bars
        starts at 0, as ArcGIS Pro's."""
        finite = [v for vals in values for v in vals if v == v and math.isfinite(v)]
        if log:
            pos = [v for v in finite if v > 0]
            lo = min(pos) / 1.5 if pos else 1.0
            hi = max(pos) * 1.5 if pos else 10.0
        else:
            lo = min(finite) if finite else 0.0
            hi = max(finite) if finite else 1.0
            lo, hi = nice_range(lo, hi, include_zero=bars, pad=0.0 if bars else 0.04)
        lo, hi = self._bounds(which, lo, hi, P.NUMBER)
        return Axis(P.NUMBER, lo, hi, log, F.REAL)

    def _paint_combo(self, p, area, fm):
        t, m, d = self.theme, self.model, self.model.data
        axes = m.plot.get("axes", {})
        log = bool(axes.get("y_log"))
        left = [s for s in range(len(d.series)) if d.axes[s] == "left"]
        right = [s for s in range(len(d.series)) if d.axes[s] == "right"]
        ya = self._value_axis([d.values[s] for s in left] or [[0.0, 1.0]],
                              any(d.kinds[s] == "bar" for s in left), log)
        ya2 = None
        if right:
            ya2 = self._value_axis([d.values[s] for s in right],
                                   any(d.kinds[s] == "bar" for s in right), log, "y2")
        xa = Axis.category(d.labels)
        y_title = m.y_title or ", ".join(d.series[s] for s in left)
        y2_title = ", ".join(d.series[s] for s in right) if right else ""
        plot = self._frame(p, area, fm, xa, ya, m.x_title, y_title, ya2=ya2,
                           y2_title=y2_title)
        self._paint_guides(p, plot, fm)
        n = len(d)
        if not n:
            return
        band = plot.width() / max(1, n)
        gap = band * 0.18 if band > 6 else 0
        bar_series = [s for s in range(len(d.series)) if d.kinds[s] == "bar"]
        line_series = [s for s in range(len(d.series)) if d.kinds[s] == "line"]
        p.save()
        p.setClipRect(plot.adjusted(-6, -6, 6, 6))
        for slot, s in enumerate(bar_series):
            axis = ya2 if d.axes[s] == "right" else ya
            colour = m.colours[s] if s < len(m.colours) else QColor(t.bar)
            w = (band - gap) / max(1, len(bar_series))
            for i in range(n):
                v = d.values[s][i]
                if not math.isfinite(v) or (axis.log and v <= 0):
                    continue
                x = xa.map(i) - band / 2 + gap / 2 + slot * w
                base = axis.map(max(0.0, axis.lo)) if not axis.log else plot.bottom()
                top = axis.map(v)
                rect = QRectF(x, min(top, base), max(w - (1 if len(bar_series) > 1 else 0), 1),
                              max(abs(base - top), 0.8))
                p.fillRect(rect, colour)
                self._geo["items"].append((rect, i, s))
                self._paint_bar_selection(p, rect, i, s, v, "side",
                                          m.sel_part if m.sel_part else None)
        for s in line_series:
            axis = ya2 if d.axes[s] == "right" else ya
            colour = m.colours[s] if s < len(m.colours) else QColor(t.bar)
            path = QPainterPath()
            started = False
            pts = []
            for i in range(n):
                v = d.values[s][i]
                if not math.isfinite(v) or (axis.log and v <= 0):
                    started = False
                    continue
                pt = QPointF(xa.map(i), axis.map(v))
                pts.append((i, pt))
                if started:
                    path.lineTo(pt)
                else:
                    path.moveTo(pt)
                    started = True
                self._geo["items"].append((QRectF(pt.x() - 5, pt.y() - 5, 10, 10), i, s))
            p.setPen(QPen(colour, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            for i, pt in pts:
                p.setBrush(colour)
                p.setPen(QPen(QColor(t.base), 1))
                p.drawEllipse(pt, 2.8, 2.8)
                if m.sel_per is not None and i < len(m.sel_per) and m.sel_per[i]:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(QColor(m.sel_colour), 2.4))
                    p.drawEllipse(pt, 5.5, 5.5)
        p.restore()

    # ---- pie -----------------------------------------------------------
    def _paint_pie(self, p, area, fm):
        t, m, d = self.theme, self.model, self.model.data
        self._geo["plot"] = area
        if not len(d) or d.total <= 0:
            p.setPen(QColor(t.text_dim))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "No values above 0 to show")
            return
        side = min(area.width(), area.height()) - 20
        rect = QRectF(area.center().x() - side / 2, area.center().y() - side / 2, side, side)
        donut = max(0.0, min(0.9, float(m.plot["data"].get("donut", 0) or 0) / 100.0))
        hole = QRectF(rect.center().x() - side * donut / 2, rect.center().y() - side * donut / 2,
                      side * donut, side * donut)
        start = 90.0                                   # 12 o'clock, clockwise
        for i, v in enumerate(d.values):
            span = -360.0 * v / d.total
            colour = m.colours[i] if i < len(m.colours) else QColor(t.bar)
            path = self._wedge(rect, hole, start, span, donut)
            p.setPen(QPen(QColor(t.base), 1.5))
            p.setBrush(colour)
            p.drawPath(path)
            self._geo["wedges"].append((path, i, start, span))
            sel = m.sel_per[i] if m.sel_per is not None and i < len(m.sel_per) else 0
            if sel:
                scol = QColor(m.sel_colour)
                if m.sel_part is not None and v:
                    share = max(0.0, min(1.0, m.sel_part[i] / v))
                    part = self._wedge(rect, hole, start, span * share, donut)
                    p.setPen(QPen(scol.darker(170), 1))
                    p.setBrush(scol)
                    p.drawPath(part)
                else:                                  # a mean, median ...: the rim
                    pen = QPen(scol, 5)
                    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawArc(rect.adjusted(2.5, 2.5, -2.5, -2.5), int(start * 16),
                              int(span * 16))
            start += span
        self._geo["pie"] = (rect, hole)

    @staticmethod
    def _wedge(rect, hole, start, span, donut):
        path = QPainterPath()
        if donut > 0:
            path.arcMoveTo(rect, start)
            path.arcTo(rect, start, span)
            path.arcTo(hole, start + span, -span)
            path.closeSubpath()
        else:
            path.moveTo(rect.center())
            path.arcTo(rect, start, span)
            path.closeSubpath()
        return path

    def _wedge_at(self, pos):
        for path, i, _a, _s in self._geo.get("wedges", []):
            if path.contains(QPointF(pos)):
                return i
        return None

    # ---- histogram -----------------------------------------------------
    def _paint_histogram(self, p, area, fm):
        m, d = self.model, self.model.data
        data = m.plot.get("data", {})
        if not len(d):
            p.setPen(QColor(self.theme.text_dim))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter,
                       f"{d.field} has no values to show in these records")
            self._geo["plot"] = area
            return
        if not d.series:
            self._paint_one_histogram(p, area, fm, d.counts, d.stats, None,
                                      m.sel_per, m.x_title, m.y_title, True)
            return
        # small histograms, one per category, on the same bins and scale
        n = len(d.series)
        per_row = max(1, int(data.get("per_row", 3) or 3))
        grid = area
        preview = None
        if data.get("preview") and n:
            chosen = data.get("preview_of")
            k = d.series.index(chosen) if chosen in d.series else 0
            grid = QRectF(area.left(), area.top(), area.width() * 0.42, area.height())
            preview = (k, QRectF(grid.right() + 12, area.top(),
                                 area.right() - grid.right() - 12, area.height()))
        rows = int(math.ceil(n / per_row))
        cw = grid.width() / per_row
        ch = grid.height() / max(1, rows)
        top = max(max(c) if c else 0 for c in d.series_counts)
        for k in range(n):
            r_, c_ = divmod(k, per_row)
            cell = QRectF(grid.left() + c_ * cw, grid.top() + r_ * ch, cw - 8, ch - 6)
            sel = m.sel_per_series[k] if m.sel_per_series else None
            self._paint_one_histogram(p, cell, fm, d.series_counts[k], d.series_stats[k],
                                      k, sel, "", "", False, top=top,
                                      title=f"{d.series[k]}  ({sum(d.series_counts[k]):,})")
        if preview is not None:
            k, rect = preview
            sel = m.sel_per_series[k] if m.sel_per_series else None
            self._paint_one_histogram(p, rect, fm, d.series_counts[k], d.series_stats[k],
                                      k, sel, m.x_title, m.y_title, True,
                                      title=f"{d.series[k]}  ({sum(d.series_counts[k]):,})")

    def _paint_one_histogram(self, p, area, fm, counts, stats, series, sel, x_title,
                             y_title, main, top=None, title=""):
        """One histogram (the plot, a small one of a category, or its
        preview) in *area*."""
        t, m, d = self.theme, self.model, self.model.data
        data = m.plot.get("data", {})
        if title:
            bold = QFont(self.font())
            bold.setBold(True)
            p.setFont(bold)
            p.setPen(QColor(t.text_strong))
            title_rect = QRectF(area.left(), area.top(), area.width(), fm.height() + 2)
            p.drawText(title_rect, Qt.AlignmentFlag.AlignCenter,
                       QFontMetrics(bold).elidedText(title, Qt.TextElideMode.ElideRight,
                                                     int(area.width())))
            p.setFont(self.font())
            if series is not None:
                self._geo["minis"].append((title_rect, series))
            area = area.adjusted(0, fm.height() + 4, 0, 0)
        edges = d.edges
        lo, hi = edges[0], edges[-1]
        lo, hi = self._bounds("x", lo, hi, P.NUMBER)
        xa = Axis(P.NUMBER, lo, hi, False, d.kind if d.transform == "none" else F.REAL)
        peak = top if top is not None else (max(counts) if counts else 1)
        y_lo, y_hi = nice_range(0.0, float(max(peak, 1)), include_zero=True)
        y_lo, y_hi = self._bounds("y", y_lo, y_hi, P.NUMBER) if main else (y_lo, y_hi)
        ya = Axis(P.NUMBER, y_lo, y_hi, bool(m.plot.get("axes", {}).get("y_log")) and main,
                  F.INTEGER)
        plot = self._frame(p, area, fm, xa, ya, x_title, y_title)
        if main:
            self._paint_guides(p, plot, fm)
        p.save()
        p.setClipRect(plot)
        colour = m.colours[series] if (series is not None and series < len(m.colours)) \
            else (m.colours[0] if m.colours else QColor(t.bar))
        base = ya.map(max(0.0, ya.lo)) if not ya.log else plot.bottom()
        for i, c in enumerate(counts):
            if not c or (ya.log and c <= 0):
                continue
            x0, x1 = xa.map(edges[i]), xa.map(edges[i + 1])
            rect = QRectF(x0 + 0.5, ya.map(c), max(x1 - x0 - 1, 0.8), base - ya.map(c))
            p.fillRect(rect, colour)
            self._geo["items"].append((rect, i, series))
            if sel is not None and i < len(sel) and sel[i]:
                h = rect.height() * sel[i] / c
                srect = QRectF(rect.left(), rect.bottom() - h, rect.width(), h)
                scol = QColor(m.sel_colour)
                p.fillRect(srect, scol)
                if srect.width() >= 4:               # an outline would hide a thin bar
                    p.setPen(QPen(scol.darker(170), 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(srect.adjusted(0.5, 0.5, -0.5, -0.5))
        # statistics on the chart
        if stats.get("n"):
            lines = []
            if data.get("mean", True) and stats.get("mean") is not None:
                lines.append((stats["mean"], QColor(m.plot.get("format", {}).get(
                    "mean_colour") or MEAN_COLOUR), Qt.PenStyle.DashLine))
            if data.get("median", True) and stats.get("median") is not None:
                lines.append((stats["median"], QColor(m.plot.get("format", {}).get(
                    "median_colour") or MEDIAN_COLOUR), Qt.PenStyle.DashLine))
            if data.get("std") and stats.get("std"):
                colour = QColor(m.plot.get("format", {}).get("std_colour") or STD_COLOUR)
                lines.append((stats["mean"] - stats["std"], colour, Qt.PenStyle.DotLine))
                lines.append((stats["mean"] + stats["std"], colour, Qt.PenStyle.DotLine))
            for v, colour, style in lines:
                x = xa.map(v)
                pen = QPen(colour, 1.6)
                pen.setStyle(style)
                p.setPen(pen)
                p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            if data.get("normal") and stats.get("std"):
                width = edges[1] - edges[0] if len(edges) > 1 else 1.0
                xs = np.linspace(xa.lo, xa.hi, 200)
                ys = stats["n"] * width * P.normal_pdf(xs, stats["mean"], stats["std"])
                path = QPainterPath()
                for j, (a, b) in enumerate(zip(xa.map_np(xs), ya.map_np(ys))):
                    if j:
                        path.lineTo(a, b)
                    else:
                        path.moveTo(a, b)
                p.setPen(QPen(QColor(TREND_COLOUR[t.dark]), 1.8))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        p.restore()

    # ---- box plot ------------------------------------------------------
    def _paint_box(self, p, area, fm, ratio, vector):
        t, m, d = self.theme, self.model, self.model.data
        axes = m.plot.get("axes", {})
        log = bool(axes.get("y_log"))
        vals = []
        for b in d.boxes:
            if b.n:
                vals += [b.low, b.high]
                if b.out_values is not None and len(b.out_values):
                    vals += [float(b.out_values.min()), float(b.out_values.max())]
        if log:
            pos = [v for v in vals if v > 0]
            lo, hi = (min(pos) / 1.5, max(pos) * 1.5) if pos else (1.0, 10.0)
        else:
            lo, hi = padded(min(vals), max(vals), 0.06) if vals else (0.0, 1.0)
        lo, hi = self._bounds("y", lo, hi, P.NUMBER)
        ya = Axis(P.NUMBER, lo, hi, log, F.REAL)
        xa = Axis.category(d.categories)
        plot = self._frame(p, area, fm, xa, ya, m.x_title, m.y_title)
        self._paint_guides(p, plot, fm)
        if not d.boxes:
            return
        nc = max(1, len(d.categories))
        ns = max(1, len(d.series))
        band = plot.width() / nc
        slot = band * 0.8 / ns
        p.save()
        p.setClipRect(plot.adjusted(-4, -4, 4, 4))
        for k, b in enumerate(d.boxes):
            if not b.n:
                continue
            ci, s = divmod(k, ns)
            colour = m.colours[s] if s < len(m.colours) else QColor(t.bar)
            cx = xa.map(ci) - band * 0.4 + slot * (s + 0.5)
            w = min(slot * 0.7, 60.0)
            y_q1, y_q3 = ya.map(b.q1), ya.map(b.q3)
            rect = QRectF(cx - w / 2, min(y_q1, y_q3), w, max(abs(y_q1 - y_q3), 1.0))
            fill = QColor(colour)
            fill.setAlpha(150)
            outline = QColor(colour).darker(140)
            p.setPen(QPen(outline, 1.4))
            p.drawLine(QPointF(cx, ya.map(b.high)), QPointF(cx, rect.top()))
            p.drawLine(QPointF(cx, rect.bottom()), QPointF(cx, ya.map(b.low)))
            for y in (ya.map(b.high), ya.map(b.low)):
                p.drawLine(QPointF(cx - w / 4, y), QPointF(cx + w / 4, y))
            p.setBrush(fill)
            p.drawRect(rect)
            p.setPen(QPen(QColor(t.text_strong), 2))
            p.drawLine(QPointF(rect.left(), ya.map(b.median)),
                       QPointF(rect.right(), ya.map(b.median)))
            hit = QRectF(cx - w / 2, ya.map(b.high), w, ya.map(b.low) - ya.map(b.high))
            self._geo["items"].append((hit.normalized(), k, None))
            sel = m.sel_per[k] if m.sel_per is not None and k < len(m.sel_per) else 0
            if sel:
                p.setPen(QPen(QColor(m.sel_colour), 2.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rect.adjusted(-2, -2, 2, 2))
            if b.out_values is not None and len(b.out_values):
                sel_rows = m.sel_mask[b.out_rows] if m.sel_mask is not None and \
                    len(m.sel_mask) else np.zeros(len(b.out_rows), dtype=bool)
                for j, (v, picked) in enumerate(zip(b.out_values.tolist(), sel_rows.tolist())):
                    if ya.log and v <= 0:
                        continue
                    y = ya.map(v)
                    c = QColor(m.sel_colour) if picked else colour
                    p.setPen(QPen(QColor(c).darker(150), 1.1))
                    p.setBrush(c)
                    p.drawEllipse(QPointF(cx, y), 3.2, 3.2)
                    self._geo["items"].append((QRectF(cx - 5, y - 5, 10, 10), k, j))
        p.restore()

    # ---- QQ plot: a scatter plot with a reference line -------------------
    def _paint_qq_reference(self, p):
        m, d = self.model, self.model.data
        if not m.plot.get("data", {}).get("reference", True) or d.ref is None:
            return
        xa, ya, plot = self._geo["x"], self._geo["y"], self._geo["plot"]
        slope, icpt = d.ref
        x0, x1 = xa.lo, xa.hi
        p.save()
        p.setClipRect(plot)
        pen = QPen(QColor(self.theme.danger), 1.8)
        p.setPen(pen)
        p.drawLine(QPointF(xa.map(x0), ya.map(slope * x0 + icpt)),
                   QPointF(xa.map(x1), ya.map(slope * x1 + icpt)))
        p.restore()

    # ---- cell colours (heat charts) -------------------------------------
    def _cell_colour(self, v):
        m = self.model
        if v != v or not m.breaks:
            return None
        c = P.class_of(v, m.breaks)
        return m.class_colours[c] if 0 <= c < len(m.class_colours) else None

    def _cell_card(self, k):
        """Hover card of cell *k* of a heat chart."""
        m, d = self.model, self.model.data
        v = float(d.values[k])
        rec = int(d.records[k])
        what = "Records" if d.agg == "count" else P.AGG_LABELS.get(d.agg, "")
        sel = int(m.sel_per[k]) if m.sel_per is not None and k < len(m.sel_per) else 0
        lines = [(self._cell_colour(v), f"{what}: {value_text(v) if rec else 'none'}")]
        if d.agg != "count":
            lines.append((None, f"{rec:,} records"))
        return lines + self._sel_line(sel)

    # ---- matrix heat chart ---------------------------------------------
    def _paint_heat(self, p, area, fm):
        m, d = self.model, self.model.data
        nr, nc = len(d.rows), len(d.columns)
        xa = Axis.category(d.columns)
        ya = Axis.category(list(reversed(d.rows)))          # the first row on top
        plot = self._frame(p, area, fm, xa, ya, m.x_title, m.y_title, grid=False)
        if not nr or not nc:
            return
        cw, ch = plot.width() / nc, plot.height() / nr
        for r in range(nr):
            for c in range(nc):
                k = r * nc + c
                colour = self._cell_colour(float(d.values[k]))
                rect = QRectF(plot.left() + c * cw, plot.top() + r * ch, cw, ch)
                if colour is None:
                    continue
                p.fillRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), colour)
                self._geo["items"].append((rect, k, None))
                if m.sel_per is not None and m.sel_per[k]:
                    p.setPen(QPen(QColor(m.sel_colour), 2.2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(rect.adjusted(1.5, 1.5, -1.5, -1.5))

    # ---- calendar heat chart -------------------------------------------
    def _paint_calendar(self, p, area, fm):
        t, m, d = self.theme, self.model, self.model.data
        self._geo["plot"] = area
        n = len(d.panels)
        cols = 1 if n <= 4 else (2 if n <= 12 else 3)
        rows_n = int(math.ceil(n / cols))
        pw = area.width() / cols
        ph = area.height() / max(1, rows_n)
        nr, nc = len(d.rows), len(d.columns)
        year = m.plot["data"].get("calendar", "year") == "year"
        label_w = max(fm.horizontalAdvance(s) for s in d.rows) + 8
        for k in range(n):
            gr, gc = divmod(k, cols)
            box = QRectF(area.left() + gc * pw, area.top() + gr * ph, pw - 10, ph - 6)
            top = box.top()
            if d.panels[k]:
                bold = QFont(self.font())
                bold.setBold(True)
                p.setFont(bold)
                p.setPen(QColor(t.text_strong))
                p.drawText(QRectF(box.left(), top, box.width(), fm.height() + 2),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           d.panels[k])
                p.setFont(self.font())
                top += fm.height() + 4
            grid = QRectF(box.left() + label_w, top, box.width() - label_w,
                          box.bottom() - top - fm.height() - 4)
            cw, ch = grid.width() / nc, grid.height() / nr
            p.setPen(QColor(t.text_dim))
            if ch >= fm.height() * 0.7:
                for r, name in enumerate(d.rows):
                    p.drawText(QRectF(box.left(), grid.top() + r * ch, label_w - 6, ch),
                               Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, name)
            step = 1 if cw >= fm.horizontalAdvance("00") + 4 else (
                5 if year else 3)
            for c, name in enumerate(d.columns):
                if c % step and not (year and c == nc - 1):
                    continue
                p.drawText(QRectF(grid.left() + c * cw - 10, grid.bottom() + 2, cw + 20,
                                  fm.height()), Qt.AlignmentFlag.AlignCenter, name)
            base = k * nr * nc
            for r in range(nr):
                for c in range(nc):
                    rect = QRectF(grid.left() + c * cw, grid.top() + r * ch, cw, ch)
                    if year and c >= _MONTH_DAYS[r]:
                        continue                       # no 30 February
                    cell = base + r * nc + c
                    colour = self._cell_colour(float(d.values[cell]))
                    p.setPen(QPen(QColor(t.border_soft), 0.6))
                    p.setBrush(colour if colour is not None else QColor(t.base))
                    p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
                    if colour is not None:
                        self._geo["items"].append((rect, cell, None))
                    if m.sel_per is not None and m.sel_per[cell]:
                        p.setPen(QPen(QColor(m.sel_colour), 2))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRect(rect.adjusted(1.5, 1.5, -1.5, -1.5))

    # ---- data clock ----------------------------------------------------
    def _paint_clock(self, p, area, fm):
        t, m, d = self.theme, self.model, self.model.data
        self._geo["plot"] = area
        nr, nc = len(d.rows), len(d.columns)
        if not nr:
            return
        label_room = fm.height() * 2 + 6
        side = min(area.width(), area.height()) - label_room
        centre = area.center()
        outer = side / 2.0
        inner = outer * 0.18
        width = (outer - inner) / nr
        span = -360.0 / nc
        for r in range(nr):
            r0, r1 = inner + r * width, inner + (r + 1) * width
            rect_o = QRectF(centre.x() - r1, centre.y() - r1, 2 * r1, 2 * r1)
            rect_i = QRectF(centre.x() - r0, centre.y() - r0, 2 * r0, 2 * r0)
            for c in range(nc):
                k = r * nc + c
                colour = self._cell_colour(float(d.values[k]))
                start = 90.0 + c * span
                path = QPainterPath()
                path.arcMoveTo(rect_o, start)
                path.arcTo(rect_o, start, span)
                path.arcTo(rect_i, start + span, -span)
                path.closeSubpath()
                p.setPen(QPen(QColor(t.border_soft), 0.5) if nc * nr < 4000
                         else Qt.PenStyle.NoPen)
                p.setBrush(colour if colour is not None else QColor(t.base))
                p.drawPath(path)
                if colour is not None:
                    self._geo["wedges"].append((path, k, start, span))
                if m.sel_per is not None and m.sel_per[k]:
                    p.setPen(QPen(QColor(m.sel_colour), 2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)
        # the wedges' names around the clock (every few when there are many)
        p.setPen(QColor(t.text_dim))
        step = max(1, int(math.ceil(nc / 24.0)))
        for c in range(0, nc, step):
            angle = math.radians(90.0 + (c + 0.5) * span)
            rr = outer + fm.height() * 0.9
            x = centre.x() + rr * math.cos(angle)
            y = centre.y() - rr * math.sin(angle)
            p.drawText(QRectF(x - 30, y - fm.height() / 2, 60, fm.height()),
                       Qt.AlignmentFlag.AlignCenter, d.columns[c])
        # the first and last ring's names at 12 o'clock, on a small card
        for r in sorted({0, nr - 1}):
            rr = inner + (r + 0.5) * width
            text = d.rows[r]
            w = fm.horizontalAdvance(text) + 8
            box = QRectF(centre.x() - w / 2, centre.y() - rr - fm.height() / 2 - 1, w,
                         fm.height() + 2)
            p.setPen(QPen(QColor(t.border), 1))
            p.setBrush(QColor(t.card))
            p.drawRoundedRect(box, 3, 3)
            p.setPen(QColor(t.text_strong))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    # ---- scatter plot matrix -------------------------------------------
    def _paint_matrix(self, p, area, fm, ratio):
        t, m, d = self.theme, self.model, self.model.data
        data = m.plot.get("data", {})
        k = len(d.fields)
        self._geo["plot"] = area
        self._geo["cells"] = []
        if k < 2:
            return
        lower, upper = data.get("lower", "r2"), data.get("upper", "scatter")
        diag = data.get("diagonal", "histogram")
        label = fm.height() + 6
        side = min(area.width() - label, area.height() - label)
        size = side / k
        left = area.left() + label
        top = area.top()
        pair = data.get("preview_pair") or [1, 0]
        try:
            pi, pj = int(pair[0]), int(pair[1])
        except (TypeError, ValueError, IndexError):
            pi, pj = 1, 0
        if not (0 <= pi < k and 0 <= pj < k and pi != pj):
            pi, pj = 1, 0
        colour = QColor(m.colours[0]) if m.colours else QColor(t.bar)
        for i in range(k):
            # field names: left of each row, under each column
            p.setPen(QColor(t.text))
            p.save()
            p.translate(area.left() + fm.height() / 2 + 2, top + (i + 0.5) * size)
            p.rotate(-90)
            p.drawText(QRectF(-size / 2, -fm.height() / 2, size, fm.height() + 2),
                       Qt.AlignmentFlag.AlignCenter,
                       fm.elidedText(d.fields[i], Qt.TextElideMode.ElideRight, int(size)))
            p.restore()
            p.drawText(QRectF(left + i * size, top + k * size + 2, size, fm.height() + 2),
                       Qt.AlignmentFlag.AlignCenter,
                       fm.elidedText(d.fields[i], Qt.TextElideMode.ElideRight, int(size)))
            for j in range(k):
                cell = QRectF(left + j * size, top + i * size, size, size).adjusted(2, 2, -2, -2)
                p.setPen(QPen(QColor(t.border_soft), 1))
                p.setBrush(QColor(t.base))
                if i == j:
                    p.drawRect(cell)
                    if diag == "histogram":
                        self._mini_hist(p, cell, d.hists[i], colour)
                    else:
                        p.setPen(QColor(t.text_strong))
                        p.drawText(cell, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                   d.fields[i])
                    continue
                show = lower if i > j else upper
                if show == "none" or (show == "preview"):
                    continue
                p.drawRect(cell)
                if show == "r2":
                    r2 = d.r2[i, j]
                    if r2 == r2:
                        fill = QColor(t.accent)
                        fill.setAlphaF(0.12 + 0.75 * float(r2))
                        p.fillRect(cell.adjusted(1, 1, -1, -1), fill)
                    p.setPen(QColor(t.text_strong))
                    p.drawText(cell, Qt.AlignmentFlag.AlignCenter,
                               "—" if r2 != r2 else f"{r2:.2f}")
                    self._geo["cells"].append((cell, i, j, "r2", None))
                else:
                    axes = self._mini_scatter(p, cell, j, i, colour, ratio, small=True)
                    self._geo["cells"].append((cell, i, j, "scatter", axes))
        if upper == "preview":
            # the upper right triangle holds one larger plot of the chosen pair
            box = QRectF(left + size * (k // 2 + (k % 2)) + 6, top + 4,
                         size * (k - (k // 2 + (k % 2))) - 8, size * (k // 2) - 8) \
                if k >= 2 else QRectF()
            if k == 2:
                box = QRectF(left + size + 6, top + 4, size - 8, size - 8)
            if box.width() > 40 and box.height() > 40:
                bold = QFont(self.font())
                bold.setBold(True)
                p.setFont(bold)
                p.setPen(QColor(t.text_strong))
                title = f"{d.fields[pi]} and {d.fields[pj]}"
                p.drawText(QRectF(box.left(), box.top(), box.width(), fm.height() + 2),
                           Qt.AlignmentFlag.AlignCenter,
                           QFontMetrics(bold).elidedText(title, Qt.TextElideMode.ElideRight,
                                                         int(box.width())))
                p.setFont(self.font())
                inner = box.adjusted(0, fm.height() + 4, 0, 0)
                p.setPen(QPen(QColor(t.border_soft), 1))
                p.setBrush(QColor(t.base))
                p.drawRect(inner)
                axes = self._mini_scatter(p, inner, pj, pi, colour, ratio, small=False)
                self._geo["cells"].append((inner, pi, pj, "preview", axes))

    def _mini_hist(self, p, cell, hist, colour):
        edges, counts = hist
        top = max(counts) if counts else 0
        if not top:
            return
        inner = cell.adjusted(4, 6, -4, -4)
        n = len(counts)
        w = inner.width() / max(1, n)
        for b, c in enumerate(counts):
            h = inner.height() * c / top
            p.fillRect(QRectF(inner.left() + b * w + 0.5, inner.bottom() - h,
                              max(w - 1, 0.8), h), colour)

    def _mini_scatter(self, p, cell, xi, yi, colour, ratio, small=True):
        """Points of field *xi* (along) against field *yi* (up) in *cell*;
        returns (x axis, y axis) for picking."""
        m, d = self.model, self.model.data
        table = m.table
        xv, yv = table.np(d.fields[xi]), table.np(d.fields[yi])
        ok = ~np.isnan(xv) & ~np.isnan(yv)
        rows = np.nonzero(ok)[0]
        lo_x, hi_x = padded(*d.ranges[xi], 0.06)
        lo_y, hi_y = padded(*d.ranges[yi], 0.06)
        pad = 4 if small else 30
        inner = cell.adjusted(pad if not small else 3, 3, -3, -(3 if small else 18))
        xa = Axis(P.NUMBER, lo_x, hi_x, False, d.kinds[xi])
        ya = Axis(P.NUMBER, lo_y, hi_y, False, d.kinds[yi])
        ya.place(inner.bottom(), inner.top())
        if not small:
            # room for the widest y label (millions need more than 30 px)
            fm = QFontMetrics(self.font())
            y_ticks = ya.ticks(max(2, int(inner.height() / 40)))
            widest = max((fm.horizontalAdvance(s) for _v, s in y_ticks), default=0)
            pad = int(min(max(30, widest + 10), cell.width() * 0.4))
            inner = cell.adjusted(pad, 3, -3, -18)
        xa.place(inner.left(), inner.right())
        if not small:
            p.setPen(QColor(self.theme.text_dim))
            for v, s in y_ticks:
                y = ya.map(v)
                if inner.top() <= y <= inner.bottom():
                    p.drawText(QRectF(cell.left(), y - 8, pad - 4, 16),
                               Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, s)
            last = -1e9
            for v, s in xa.ticks(max(2, int(inner.width() / 80))):
                x = xa.map(v)
                w = fm.horizontalAdvance(s)
                if inner.left() <= x <= inner.right() and x - w / 2 > last + 6:
                    p.drawText(QRectF(x - w / 2, inner.bottom() + 2, w + 2, 16),
                               Qt.AlignmentFlag.AlignCenter, s)
                    last = x + w / 2
        sx = xa.map_np(xv[rows])
        sy = ya.map_np(yv[rows])
        sel = m.sel_mask[rows] if m.sel_mask is not None and len(m.sel_mask) else None
        p.save()
        p.setClipRect(inner)
        limit = 4000 if small else 20000
        if len(rows) <= limit:
            dia = 3.0 if small else 5.0
            pm, half = self._sprite(colour, dia, ratio)
            plain = np.ones(len(rows), dtype=bool) if sel is None else ~sel
            for a, b in zip((sx[plain] - half).tolist(), (sy[plain] - half).tolist()):
                p.drawPixmap(QPointF(a, b), pm)
            if sel is not None and sel.any():
                pm, half = self._sprite(QColor(m.sel_colour), dia, ratio)
                for a, b in zip((sx[sel] - half).tolist(), (sy[sel] - half).tolist()):
                    p.drawPixmap(QPointF(a, b), pm)
        else:                                          # shaded, as a big scatter plot
            w = max(1, int(inner.width() / 2))
            h = max(1, int(inner.height() / 2))
            cx = np.clip(((sx - inner.left()) / 2).astype(np.int64), 0, w - 1)
            cy = np.clip(((sy - inner.top()) / 2).astype(np.int64), 0, h - 1)
            counts, _dom = P.density(cx, cy, w, h)
            top = max(1, int(counts.max()))
            alpha = np.where(counts > 0, 0.28 + 0.72 * np.log1p(counts) / math.log1p(top), 0)
            argb = (np.clip(alpha * 255, 0, 255).astype(np.uint32) << 24) | \
                np.uint32((colour.red() << 16) | (colour.green() << 8) | colour.blue())
            if sel is not None and sel.any():
                sc = np.bincount(cy[sel] * w + cx[sel], minlength=w * h)[:w * h].reshape(h, w)
                s_col = QColor(m.sel_colour)
                argb = np.where(sc > 0, np.uint32(0xFF000000 | (s_col.red() << 16)
                                                  | (s_col.green() << 8) | s_col.blue()), argb)
            buf = np.ascontiguousarray(argb.astype(np.uint32))
            img = QImage(buf.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
            p.drawImage(QRectF(inner.left(), inner.top(), w * 2, h * 2), img)
        trend = d.trends.get((xi, yi))
        if trend is not None and trend.ok:
            xs = np.linspace(xa.lo, xa.hi, 60)
            ys = trend.at(xs)
            p.setPen(QPen(QColor(TREND_COLOUR[self.theme.dark]), 1.4))
            path = QPainterPath()
            for n_, (a, b) in enumerate(zip(xa.map_np(xs), ya.map_np(ys))):
                if n_:
                    path.lineTo(a, b)
                else:
                    path.moveTo(a, b)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.restore()
        return xa, ya

    def _matrix_cell(self, pos):
        for rect, i, j, kind, axes in (self._geo or {}).get("cells", []):
            if rect.contains(QPointF(pos)):
                return rect, i, j, kind, axes
        return None

    # ---- legend --------------------------------------------------------
    def _legend_entries(self):
        """(kind, colour, text, pick) per legend row; *pick* is a series or
        class number to select, or None."""
        t, m, d = self.theme, self.model, self.model.data
        out = []
        if m.kind in ("heat", "calendar", "clock"):
            lo = None
            vals = d.values[~np.isnan(d.values)] if d.values is not None else []
            if len(vals):
                lo = float(vals.min())
            for c, b in enumerate(m.breaks):        # each class: from - to
                colour = m.class_colours[c] if c < len(m.class_colours) else QColor(t.bar)
                start = m.breaks[c - 1] if c else lo
                text = value_text(b) if start is None else \
                    f"{value_text(start)} – {value_text(b)}"
                out.append(("box", colour, text, None))
        elif m.kind == "qq":
            if d.ref is not None and m.plot.get("data", {}).get("reference", True):
                out.append(("line", QColor(t.danger), "Reference line", None))
            if d.lam is not None:
                out.append(("none", None, f"Box-Cox λ = {d.lam:.2f}", None))
            if len(d) > DENSITY_LIMIT:
                out.append(("density", QColor(m.single or t.bar),
                            "Shade: records per spot", None))
        elif m.kind == "matrix":
            pass
        elif m.kind == "scatter":
            if d.classes is not None and m.single is None and len(m.colours) > 1:
                for c, colour in enumerate(m.colours):
                    label = m.table.class_labels[c] if c < len(m.table.class_labels) else ""
                    out.append(("box", colour, label or "(no label)", c))
            if d.trend is not None and d.trend.ok:
                out.append(("line", QColor(TREND_COLOUR[t.dark]),
                            f"{P.TREND_LABELS.get(d.trend.kind, '')} trend", None))
                out.append(("none", None, d.trend.equation, None))
                if d.trend.r2 is not None:
                    out.append(("none", None, f"R² = {d.trend.r2:.4f}", None))
            if len(d) > DENSITY_LIMIT:
                out.append(("density", QColor(m.single or t.bar),
                            "Shade: records per spot", None))
        elif m.kind == "combo":
            for s, name in enumerate(d.series):
                colour = m.colours[s] if s < len(m.colours) else QColor(t.bar)
                text = str(name) + ("  (right axis)" if d.axes[s] == "right" else "")
                out.append(("box" if d.kinds[s] == "bar" else "line", colour, text, None))
        elif m.kind == "pie":
            for i, label in enumerate(d.labels):
                colour = m.colours[i] if i < len(m.colours) else QColor(t.bar)
                share = 100.0 * d.values[i] / d.total if d.total else 0.0
                out.append(("box", colour, f"{label}  {share:.1f}%", i))
        elif m.kind == "histogram":
            data = m.plot.get("data", {})
            fmt = m.plot.get("format", {})
            st = d.stats
            if not d.series and st.get("n"):
                kind = d.kind if d.transform == "none" else F.REAL
                if data.get("mean", True):
                    out.append(("dash", QColor(fmt.get("mean_colour") or MEAN_COLOUR),
                                f"Mean  {value_text(st['mean'], kind)}", None))
                if data.get("median", True):
                    out.append(("dash", QColor(fmt.get("median_colour") or MEDIAN_COLOUR),
                                f"Median  {value_text(st['median'], kind)}", None))
                if data.get("std"):
                    out.append(("dot", QColor(fmt.get("std_colour") or STD_COLOUR),
                                f"± 1 Std. Dev.  {value_text(st['std'], kind)}", None))
                if data.get("normal"):
                    out.append(("line", QColor(TREND_COLOUR[t.dark]),
                                "Normal Distribution Curve", None))
        elif m.kind == "box":
            if len(d.series) > 1:
                for s, name in enumerate(d.series):
                    colour = m.colours[s] if s < len(m.colours) else QColor(t.bar)
                    out.append(("box", colour, str(name), None))
        else:
            if len(d.series) > 1 or d.split:
                for s, name in enumerate(d.series):
                    colour = m.colours[s] if s < len(m.colours) else QColor(t.bar)
                    out.append(("box", colour, str(name), s if d.split else None))
        if m.sel_count:
            out.append(("sel", QColor(m.sel_colour), f"Selected  {m.sel_count:,}", None))
        return out

    def _inside_box(self, plot, w, h):
        """The top corner of the plot (right or left) that hides the least
        of the drawing, as Visualize Statistics places its legend."""
        boxes = [QRectF(plot.right() - w - 8, plot.top() + 6, w, h),
                 QRectF(plot.left() + 8, plot.top() + 6, w, h)]
        geo = self._geo

        def hidden(box):
            if geo.get("points") is not None:
                sx, sy, inside = geo["points"]
                return int((inside & (sx >= box.left()) & (sx <= box.right())
                            & (sy >= box.top()) & (sy <= box.bottom())).sum())
            area = 0.0
            for rect, _i, _s in geo["items"]:
                inter = rect.intersected(box)
                if not inter.isEmpty():
                    area += inter.width() * inter.height()
            return area

        return min(boxes, key=hidden)

    def _legend_height_bottom(self, fm, texts, width):
        x, rows = 0, 1
        for s in texts:
            w = fm.horizontalAdvance(s) + 40
            if x + w > width and x > 0:
                rows += 1
                x = 0
            x += w
        return rows * 20 + 6

    def _paint_legend(self, p, box, legend, fm, bottom):
        t = self.theme
        rows = []
        if bottom:
            x, y = box.left(), box.top() + 3
            for kind, colour, text, pick in legend:
                w = fm.horizontalAdvance(text) + 40
                if x + w > box.right() and x > box.left():
                    x, y = box.left(), y + 20
                rows.append((QRectF(x, y, w, 18), kind, colour, text, pick))
                x += w
        else:
            box.setHeight(10 + 20 * len(legend))
            p.setPen(QPen(QColor(t.border_soft), 1))
            p.setBrush(QColor(t.base))
            p.drawRoundedRect(box, 3, 3)
            for k, (kind, colour, text, pick) in enumerate(legend):
                rows.append((QRectF(box.left() + 2, box.top() + 5 + 20 * k, box.width() - 4, 18),
                             kind, colour, text, pick))
        self._geo["legend_box"] = box
        for rect, kind, colour, text, pick in rows:
            sw = QRectF(rect.left() + 8, rect.center().y() - 5, 12, 10)
            if kind == "box":
                p.fillRect(sw, colour)
            elif kind == "sel":                 # as the Pivot Table chart's swatch
                self._geo["sel_swatch"] = QRectF(sw)
                p.fillRect(sw, colour)
                p.setPen(QPen(colour.darker(170), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(sw.adjusted(0.5, 0.5, -0.5, -0.5))
            elif kind in ("dash", "dot"):
                pen = QPen(colour, 1.8)
                pen.setStyle(Qt.PenStyle.DashLine if kind == "dash" else Qt.PenStyle.DotLine)
                p.setPen(pen)
                p.drawLine(QPointF(sw.left() - 2, sw.center().y()),
                           QPointF(sw.right() + 2, sw.center().y()))
            elif kind == "line":
                p.setPen(QPen(colour, 2))
                p.drawLine(QPointF(sw.left() - 2, sw.center().y()),
                           QPointF(sw.right() + 2, sw.center().y()))
            elif kind == "density":
                for j in range(4):
                    c = QColor(colour)
                    c.setAlphaF(0.28 + 0.24 * j)
                    p.fillRect(QRectF(sw.left() + j * 3, sw.top(), 3, sw.height()), c)
            p.setPen(QColor(t.text))
            p.drawText(QRectF(rect.left() + 28, rect.top(), rect.width() - 30, rect.height()),
                       Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight,
                                     int(max(10, rect.width() - 30))))
            if pick is not None:
                self._geo["legend"].append((rect, pick))

    # ------------------------------------------------------------------
    # hover and picking
    # ------------------------------------------------------------------
    def _scatter_hit(self, pos):
        """(point indexes, anchor) of the points under *pos*: the nearest
        point and those on the same spot (the whole cell when shaded)."""
        geo = self._geo
        if not geo or geo.get("points") is None:
            return None
        sx, sy, inside = geo["points"]
        if geo.get("dense"):
            w, h, counts, cx, cy, idx = geo["density"]
            plot = geo["plot"]
            gx = int((pos.x() - plot.left()) / CELL)
            gy = int((pos.y() - plot.top()) / CELL)
            if not (0 <= gx < w and 0 <= gy < h) or counts[gy, gx] == 0:
                return None
            pick = idx[(cx == gx) & (cy == gy)]
            return pick, (plot.left() + (gx + 0.5) * CELL, plot.top() + (gy + 0.5) * CELL)
        dia = geo.get("dia")
        dist2 = (sx - pos.x()) ** 2 + (sy - pos.y()) ** 2
        reach = (dia / 2.0 + 3.0) ** 2 if dia is not None else PICK_RADIUS ** 2
        near = np.nonzero(inside & (dist2 <= reach))[0]
        if not len(near):
            return None
        best = near[np.argmin(dist2[near])]
        same = near[(np.abs(sx[near] - sx[best]) < 0.75) & (np.abs(sy[near] - sy[best]) < 0.75)]
        same = np.concatenate(([best], same[same != best]))
        return same, (float(sx[best]), float(sy[best]))

    def _series_hit(self, pos):
        """(position, series) under *pos*."""
        if not self._geo:
            return None
        pt = QPointF(pos)
        best = None
        for rect, i, s in self._geo["items"]:
            if rect.contains(pt):
                best = (i, s)
        if best is None and self.model.kind == "line":
            plot = self._geo.get("plot")
            if plot is not None and plot.contains(pt):
                # nearest position along X
                items = self._geo["items"]
                if items:
                    rect, i, s = min(items, key=lambda it: abs(it[0].center().x() - pt.x())
                                     + abs(it[0].center().y() - pt.y()) * 0.25)
                    if abs(rect.center().x() - pt.x()) < 30:
                        best = (i, None)
        return best

    def _legend_hit(self, pos):
        if not self._geo:
            return None
        pt = QPointF(pos)
        for rect, pick in self._geo["legend"]:
            if rect.contains(pt):
                return pick
        return None

    def mouseMoveEvent(self, event):
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        if self._press is not None:
            self._drag = pos
            self.update()
            return
        hover = None
        if self.model is not None and self.model.data is not None and not self.message:
            if self.model.kind == "scatter":
                hit = self._scatter_hit(pos)
                if hit is not None:
                    hover = ("points", hit[0], hit[1])
            elif self.model.kind in ("pie", "clock"):
                i = self._wedge_at(pos)
                if i is not None:
                    hover = ("wedge", i, None)
            elif self.model.kind == "matrix":
                cell = self._matrix_cell(pos)
                if cell is not None:
                    hover = ("cell", cell[1], cell[2], cell[0])
            elif self.model.kind == "qq":
                hit = self._scatter_hit(pos)
                if hit is not None:
                    hover = ("points", hit[0], hit[1])
            else:
                hit = self._series_hit(pos)
                if hit is not None:
                    hover = ("series", hit[0], hit[1])
        over_legend = self._legend_hit(pos) is not None
        self.setCursor(Qt.CursorShape.PointingHandCursor if (hover or over_legend)
                       else Qt.CursorShape.ArrowCursor)
        self._hover = hover
        self.update()

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.model is None:
            return super().mousePressEvent(event)
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        self._press = pos
        self._drag = None
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return super().mouseReleaseEvent(event)
        add = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        # the drag ends where the button is let go (as in Visualize Statistics)
        press = self._press
        drag = event.position() if hasattr(event, "position") else QPointF(event.pos())
        self._press = self._drag = None
        self.update()
        m = self.model
        if m is None or m.data is None or self.message:
            return
        moved = drag is not None and (abs(drag.x() - press.x()) > 4
                                      or abs(drag.y() - press.y()) > 4)
        if not moved and m.kind == "matrix":
            cell = self._matrix_cell(press)
            if cell is not None and cell[3] in ("scatter", "r2"):
                self.previewPicked.emit(f"{cell[1]},{cell[2]}")
            return
        if not moved:
            for rect, k in (self._geo or {}).get("minis", []):
                if rect.contains(press) and m.kind == "histogram" and k is not None:
                    self.previewPicked.emit(str(m.data.series[k]))
                    return
            pick = self._legend_hit(press)
            if pick is not None:
                self.rowsPicked.emit(self._legend_rows(pick), add)
                return
            rows = self._rows_at(press)
            if rows is not None:
                self.rowsPicked.emit(rows, add)
            return
        rows = self._rows_in(press, drag)
        if rows is not None:
            self.rowsPicked.emit(rows, add)

    def contextMenuEvent(self, event):
        rows = self._rows_at(QPointF(event.pos()))
        if rows is None or not len(rows):
            return super().contextMenuEvent(event)
        self._hover = None
        self.update()
        self.rowsMenu.emit(rows, event.globalPos())

    def _rows_at(self, pos):
        m, d = self.model, self.model.data
        if m.kind == "scatter":
            hit = self._scatter_hit(pos)
            return d.rows[hit[0]] if hit is not None else None
        if m.kind == "pie":
            i = self._wedge_at(pos)
            return P.pie_rows(d, m.table, [i]) if i is not None else None
        if m.kind == "qq":
            hit = self._scatter_hit(pos)
            return d.rows[hit[0]] if hit is not None else None
        if m.kind == "clock":
            k = self._wedge_at(pos)
            return P.cell_rows(d, [k]) if k is not None else None
        if m.kind == "matrix":
            return None                                # a click picks the preview pair
        hit = self._series_hit(pos)
        if hit is None:
            return None
        i, s = hit
        if m.kind in ("heat", "calendar"):
            return P.cell_rows(d, [i])
        if m.kind == "histogram":
            return P.hist_rows(d, [i], s)
        if m.kind == "box":
            b = d.boxes[i]
            return b.rows if s is None else b.out_rows[s:s + 1]
        series = s if (getattr(d, "split", False) and s is not None) else None
        return P.records_of(d, [i], series)

    def _rows_in(self, a, b):
        """Rows of a drag: the points inside the box (scatter) or the
        positions the drag spans (line, bar)."""
        m, geo = self.model, self._geo
        if not geo or geo.get("plot") is None:
            return None
        xa, ya = geo["x"], geo["y"]
        if m.kind == "matrix":
            cell = self._matrix_cell(a)
            if cell is None or cell[4] is None:
                return None
            _rect, i, j, _kind, (mxa, mya) = cell
            x0, x1 = sorted((mxa.value_at(a.x()), mxa.value_at(b.x())))
            y0, y1 = sorted((mya.value_at(a.y()), mya.value_at(b.y())))
            xv, yv = m.table.np(m.data.fields[j]), m.table.np(m.data.fields[i])
            mask = (xv >= x0) & (xv <= x1) & (yv >= y0) & (yv <= y1)
            return np.nonzero(mask)[0]
        if m.kind in ("heat", "calendar"):
            box = QRectF(a, b).normalized()
            cells = [k for rect, k, _s in geo["items"] if rect.intersects(box)]
            return P.cell_rows(m.data, cells) if cells else None
        if m.kind == "clock":
            return None
        if m.kind in ("scatter", "qq"):
            d = m.data
            x0, x1 = sorted((xa.value_at(a.x()), xa.value_at(b.x())))
            y0, y1 = sorted((ya.value_at(a.y()), ya.value_at(b.y())))
            mask = (d.x >= x0) & (d.x <= x1) & (d.y >= y0) & (d.y <= y1)
            return d.rows[mask]
        if m.kind == "scatter":
            d = m.data
            x0, x1 = sorted((xa.value_at(a.x()), xa.value_at(b.x())))
            y0, y1 = sorted((ya.value_at(a.y()), ya.value_at(b.y())))
            mask = (d.x >= x0) & (d.x <= x1) & (d.y >= y0) & (d.y <= y1)
            return d.rows[mask]
        if m.kind == "pie":
            return None
        lo, hi = sorted((a.x(), b.x()))
        items = geo["items"]
        if m.kind == "histogram":                    # within one small histogram
            start = self._series_hit(a)
            series = start[1] if start is not None else (None if not m.data.series else -1)
            picked = sorted({i for rect, i, s in items if s == series
                             and rect.right() >= lo and rect.left() <= hi})
            return P.hist_rows(m.data, picked, series) if picked else None
        if m.kind == "box":
            picked = sorted({i for rect, i, s in items if s is None
                             and rect.right() >= lo and rect.left() <= hi})
            if not picked:
                return None
            return np.unique(np.concatenate([m.data.boxes[i].rows for i in picked]))
        picked = sorted({i for rect, i, _s in items
                         if rect.right() >= lo and rect.left() <= hi})
        if not picked:
            return None
        return P.records_of(m.data, picked)

    def _legend_rows(self, pick):
        m, d = self.model, self.model.data
        if m.kind == "scatter":
            return d.rows[d.classes == pick] if d.classes is not None else d.rows[:0]
        if m.kind == "pie":
            return P.pie_rows(d, m.table, [pick])
        if getattr(d, "series_of", None) is not None and getattr(d, "group", None) is not None:
            return np.nonzero((d.series_of == pick) & (d.group >= 0))[0]
        return np.zeros(0, dtype=np.int64)

    # ---- overlays ------------------------------------------------------
    def _paint_drag(self, p):
        if self._press is None or self._drag is None or not self._geo:
            return
        plot = self._geo.get("plot")
        if plot is None:
            return
        pen = QPen(QColor(self.theme.text_strong), 1.2)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        fill = QColor(self.theme.accent)
        fill.setAlpha(40)
        p.setBrush(fill)
        if self.model.kind == "scatter":
            rect = QRectF(self._press, self._drag).normalized()
        else:
            lo, hi = sorted((self._press.x(), self._drag.x()))
            rect = QRectF(lo, plot.top(), hi - lo, plot.height())
        p.drawRect(rect.intersected(plot))

    def card_lines(self):
        """(title, [(colour or None, text)], footer) of the hover card."""
        m, d = self.model, self.model.data
        kind, a, b = self._hover[:3]
        if kind == "points":
            points = a
            rows = d.rows[points]
            n = len(points)
            data = m.plot.get("data", {})
            first = int(points[0]) if n else 0
            x_name = getattr(d, "x_name", "") or data.get("x")
            y_name = getattr(d, "y_name", "") or data.get("y")
            lines = [(None, f"{x_name}: {value_text(float(d.x[first]), d.x_kind)}"),
                     (None, f"{y_name}: {value_text(float(d.y[first]), d.y_kind)}")]
            if d.size is not None and data.get("size"):
                lines.append((None, f"{data['size']}: {value_text(float(d.size[first]))}"))
            if d.classes is not None and m.single is None and len(m.colours) > 1:
                c = int(d.classes[first])
                if 0 <= c < len(m.table.class_labels):
                    lines.append((m.colours[c], m.table.class_labels[c]))
            title = f"{n:,} records here" if n > 1 else "1 record"
            if self._geo.get("dense"):
                title = f"{n:,} records in this spot"
                lines = lines[:0] + [(None, "Values around "
                                      f"{value_text(float(d.x[first]), d.x_kind)}, "
                                      f"{value_text(float(d.y[first]), d.y_kind)}")]
            if m.sel_mask is not None and len(m.sel_mask):
                n_sel = int(m.sel_mask[rows].sum())
                if n_sel:
                    lines.append((None, f"{n_sel:,} selected"))
            return title, lines, "Click to select · Ctrl+click to add · drag a box"
        if kind == "cell" and m.kind == "matrix":
            i, j = a, b
            r2 = d.r2[i, j]
            lines = [(None, f"R² = {r2:.4f}" if r2 == r2 else "R²: no value"),
                     (None, f"r = {d.r[i, j]:.4f}" if d.r[i, j] == d.r[i, j] else ""),
                     (None, f"{int(d.n[i, j]):,} records with both values")]
            lines = [x for x in lines if x[1]]
            return (f"{d.fields[i]} and {d.fields[j]}", lines,
                    "Click to show it in the preview · drag in a plot to select")
        if m.kind in ("heat", "calendar", "clock"):
            return self._card_cell(a)
        if m.kind in ("pie", "histogram", "box", "combo"):
            return getattr(self, f"_card_{m.kind}")(a, b)
        i = a
        title = d.labels[i]
        if d.x_role == P.DATE and d.ends is not None and d.bins is not None:
            title = f"{d.labels[i]} – {P.bin_label(d.ends[i], d.ends[i], d.bins.unit, d.x_kind)}"
        lines = []
        agg = P.AGG_LABELS.get(d.agg, "")
        for k, name in enumerate(d.series):
            if d.split and d.series_records is not None and not d.series_records[k][i]:
                continue                     # no records of this category here
            v = d.values[k][i]
            text = f"{name}: {value_text(v, m.y_axis_kind)}"
            if len(d.series) == 1 and d.agg != "count":
                text = f"{agg}: {value_text(v, m.y_axis_kind)}"
            if d.split and d.series_records is not None:
                text += f"  ({d.series_records[k][i]:,} records)"
            colour = m.colours[k] if (len(d.series) > 1 or d.split) and k < len(m.colours) \
                else None
            lines.append((colour, text))
        lines.append((None, f"{d.records[i]:,} records"))
        if m.sel_per is not None and i < len(m.sel_per) and m.sel_per[i]:
            lines.append((None, f"{m.sel_per[i]:,} selected"))
        foot = "Click to select · Ctrl+click to add · drag for a range"
        return title, lines, foot

    def _sel_line(self, n):
        return [(None, f"{n:,} selected")] if n else []

    def _card_cell(self, k):
        d = self.model.data
        nc = len(d.columns)
        if d.kind == "calendar":
            nr = len(d.rows)
            panel, rest = divmod(k, nr * nc)
            r, c = divmod(rest, nc)
            name = d.panels[panel] if d.panels and d.panels[panel] else ""
            if self.model.plot["data"].get("calendar", "year") == "year":
                title = f"{c + 1} {d.rows[r]}" + (f" {name}" if name else "")
            else:
                title = f"{d.rows[r]} {d.columns[c]}:00" + (f" · {name}" if name else "")
        else:
            r, c = divmod(k, nc)
            title = f"{d.columns[c]} · {d.rows[r]}"
        return title, self._cell_card(k), "Click to select · Ctrl+click to add"

    def _card_combo(self, i, s):
        m, d = self.model, self.model.data
        title = d.labels[i]
        if d.x_role == P.DATE and d.ends is not None and d.bins is not None:
            title = f"{d.labels[i]} – {P.bin_label(d.ends[i], d.ends[i], d.bins.unit, d.x_kind)}"
        lines = []
        for k, name in enumerate(d.series):
            colour = m.colours[k] if k < len(m.colours) else None
            lines.append((colour, f"{name}: {value_text(d.values[k][i])}"))
        lines.append((None, f"{d.records[i]:,} records"))
        sel = m.sel_per[i] if m.sel_per is not None and i < len(m.sel_per) else 0
        return title, lines + self._sel_line(sel), \
            "Click to select · Ctrl+click to add · drag for a range"

    def _card_pie(self, i, _s):
        m, d = self.model, self.model.data
        v = d.values[i]
        share = 100.0 * v / d.total if d.total else 0.0
        what = "Records" if d.agg == "count" else P.AGG_LABELS.get(d.agg, "")
        lines = [(m.colours[i] if i < len(m.colours) else None,
                  f"{what}: {value_text(v)}  ({share:.1f}%)"),
                 (None, f"{d.records[i]:,} records")]
        if d.fields is None and i == d.other:
            lines.append((None, f"{len(d.members[i]):,} categories under the threshold"))
        sel = m.sel_per[i] if m.sel_per is not None and i < len(m.sel_per) else 0
        return d.labels[i], lines + self._sel_line(sel), "Click to select · Ctrl+click to add"

    def _card_histogram(self, i, s):
        m, d = self.model, self.model.data
        kind = d.kind if d.transform == "none" else F.REAL
        lo, hi = d.edges[i], d.edges[i + 1]
        last = i == len(d) - 1
        title = f"{value_text(lo, kind)} – {value_text(hi, kind)}"
        if last:
            title += "  (maximum included)"
        counts = d.series_counts[s] if s is not None and d.series_counts else d.counts
        total = sum(counts) or 1
        lines = [(None, f"{counts[i]:,} records  ({100.0 * counts[i] / total:.1f}%)")]
        if s is not None and d.series:
            lines.insert(0, (m.colours[s] if s < len(m.colours) else None, str(d.series[s])))
        if d.transform != "none":
            lines.append((None, "Values after the "
                          + ("logarithm" if d.transform == "log" else "square root")))
        sel = m.sel_per_series[s][i] if (s is not None and m.sel_per_series) else \
            (m.sel_per[i] if m.sel_per is not None and i < len(m.sel_per) else 0)
        return title, lines + self._sel_line(sel), \
            "Click to select · Ctrl+click to add · drag for a range"

    def _card_box(self, k, j):
        m, d = self.model, self.model.data
        b = d.boxes[k]
        name = d.series[b.series] if b.series < len(d.series) else ""
        title = " · ".join(x for x in (b.label, name if len(d.series) > 1 or not b.label
                                       else "") if x) or name
        if j is not None:
            v = float(b.out_values[j])
            lines = [(None, f"Outlier: {value_text(v)}"),
                     (None, "More than 1.5 × the interquartile range beyond the box")]
            return title, lines, "Click to select this record"
        z = " (z-score)" if d.standardized else ""
        lines = [(None, f"{b.n:,} records")]
        for label, v in (("Upper whisker", b.high), ("Third quartile", b.q3),
                         ("Median", b.median), ("First quartile", b.q1),
                         ("Lower whisker", b.low), ("Mean", b.mean)):
            lines.append((None, f"{label}{z}: {value_text(v)}"))
        if b.out_values is not None and len(b.out_values):
            lines.append((None, f"{len(b.out_values):,} outliers"))
        sel = m.sel_per[k] if m.sel_per is not None and k < len(m.sel_per) else 0
        return title, lines + self._sel_line(sel), \
            "Click to select · Ctrl+click to add · drag across boxes"

    def _paint_hover(self, p):
        if self._hover is None or self._press is not None or not self._geo:
            return
        t = self.theme
        plot = self._geo.get("plot")
        if plot is None:
            return
        fm = QFontMetrics(self.font())
        bold = QFont(self.font())
        bold.setBold(True)
        bfm = QFontMetrics(bold)
        title, lines, foot = self.card_lines()
        kind = self._hover[0]
        if kind == "points":
            ax, ay = self._hover[2]
            p.setPen(QPen(QColor(t.text_strong), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(ax, ay), 7, 7)
        elif kind == "cell":
            rect = self._hover[3]
            ax, ay = rect.right() - 4, rect.top() + rect.height() / 2
            p.setPen(QPen(QColor(t.text_strong), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect)
        elif kind == "wedge":
            path = next((w[0] for w in self._geo.get("wedges", []) if w[1] == self._hover[1]),
                        None)
            if path is None:
                return
            ax, ay = path.boundingRect().center().x(), path.boundingRect().center().y()
            p.setPen(QPen(QColor(t.text_strong), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        else:
            i, s_hover = self._hover[1], self._hover[2]
            rects = [rect for rect, j, s_ in self._geo["items"] if j == i
                     and (self.model.kind not in ("histogram", "box") or s_ == s_hover)]
            if not rects:
                return
            ax = rects[0].center().x()
            ay = min(r.top() for r in rects)
            if self.model.kind == "line":
                pen = QPen(QColor(t.text_dim), 1)
                pen.setStyle(Qt.PenStyle.DotLine)
                p.setPen(pen)
                p.drawLine(QPointF(ax, plot.top()), QPointF(ax, plot.bottom()))
        widths = [bfm.horizontalAdvance(title)] + [
            fm.horizontalAdvance(s) + (16 if c is not None else 0) for c, s in lines]
        widths.append(fm.horizontalAdvance(foot))
        tw = max(widths) + 22
        th = 10 + 18 * (len(lines) + 2)
        x = ax + 14
        if x + tw > self.width() - 4:
            x = ax - tw - 14
        y = max(4, ay - th / 2)
        card = QRectF(max(4, x), min(y, self.height() - th - 4), tw, th)
        p.setPen(QPen(QColor(t.border), 1))
        p.setBrush(QColor(t.card))
        p.drawRoundedRect(card, 4, 4)
        p.setFont(bold)
        p.setPen(QColor(t.text_strong))
        p.drawText(QRectF(card.left() + 10, card.top() + 5, tw - 14, 18),
                   Qt.AlignmentFlag.AlignVCenter, title)
        p.setFont(self.font())
        for k, (colour, text) in enumerate(lines, 1):
            x0 = card.left() + 10
            if colour is not None:
                p.fillRect(QRectF(x0, card.top() + 10 + 18 * k, 10, 9), colour)
                x0 += 16
            p.setPen(QColor(t.text))
            p.drawText(QRectF(x0, card.top() + 5 + 18 * k, tw, 18),
                       Qt.AlignmentFlag.AlignVCenter, text)
        p.setPen(QColor(t.text_dim))
        p.drawText(QRectF(card.left() + 10, card.top() + 5 + 18 * (len(lines) + 1), tw, 18),
                   Qt.AlignmentFlag.AlignVCenter, foot)

