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
from .chart_view import OTHER_COLOUR, SERIES, nice_step

try:
    import numpy as np
except ImportError:              # pragma: no cover - QGIS ships numpy
    np = None

DENSITY_LIMIT = 100000           # scatter points drawn one by one up to this (measured: ~0.11 s)
CELL = 3                         # density: size of a spot (px)
PICK_RADIUS = 6                  # px around the mouse that picks a point
TREND_COLOUR = {False: "#37474F", True: "#ECEFF1"}


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


# ============================================================================
# VIEW
# ============================================================================

class PlotView(QWidget):
    rowsPicked = pyqtSignal(object, bool)          # numpy row indexes, add
    rowsMenu = pyqtSignal(object, object)          # numpy row indexes, global pos

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
                     "points": None}
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
    def _frame(self, p, area, fm, xa, ya, x_title, y_title, rotate_x=False):
        """Lay out *area* around the two axes and draw the grid, the axes
        and the guides; returns the plot rectangle."""
        t, m = self.theme, self.model
        yt = ya.ticks(max(2, int(area.height() / 45)))
        ywidth = max([fm.horizontalAdvance(s) for _v, s in yt] + [20])
        left = area.left() + ywidth + 10 + (fm.height() + 6 if y_title else 0)
        xt = xa.ticks(max(2, int((area.width() - ywidth) / 90)))
        if xa.role == P.CATEGORY:
            slant = self._category_slant(fm, xt, area.width() - ywidth - 30)
        else:
            slant = 0
        label_h = fm.height() + 6 if not slant else min(
            110, max(fm.horizontalAdvance(s) for _v, s in xt) * 0.72 + 12)
        bottom = area.bottom() - label_h - (fm.height() + 6 if x_title else 0)
        plot = QRectF(left, area.top() + 6, area.right() - left - 8, bottom - area.top() - 6)
        if plot.width() < 40 or plot.height() < 40:
            plot = QRectF(area.left(), area.top(), max(40.0, area.width()),
                          max(40.0, area.height()))
        xa.place(plot.left(), plot.right())
        ya.place(plot.bottom(), plot.top())
        self._geo["plot"] = plot
        self._geo["x"], self._geo["y"] = xa, ya
        fmt = m.plot.get("format", {})
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
        if fmt.get("axis_lines", True):
            p.setPen(QPen(QColor(t.text_dim), 1))
            p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
            p.drawLine(QPointF(plot.left(), plot.top()), QPointF(plot.left(), plot.bottom()))
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
        if part is not None and s < len(part) and raw:
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

    # ---- legend --------------------------------------------------------
    def _legend_entries(self):
        """(kind, colour, text, pick) per legend row; *pick* is a series or
        class number to select, or None."""
        t, m, d = self.theme, self.model, self.model.data
        out = []
        if m.kind == "scatter":
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
            elif kind == "sel":
                p.fillRect(sw, colour)
                p.setPen(QPen(colour.darker(170), 1))
                p.drawRect(sw)
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
        if not moved:
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
        m = self.model
        if m.kind == "scatter":
            hit = self._scatter_hit(pos)
            return m.data.rows[hit[0]] if hit is not None else None
        hit = self._series_hit(pos)
        if hit is None:
            return None
        i, s = hit
        series = s if (m.data.split and s is not None) else None
        return P.records_of(m.data, [i], series)

    def _rows_in(self, a, b):
        """Rows of a drag: the points inside the box (scatter) or the
        positions the drag spans (line, bar)."""
        m, geo = self.model, self._geo
        if not geo or geo.get("plot") is None:
            return None
        xa, ya = geo["x"], geo["y"]
        if m.kind == "scatter":
            d = m.data
            x0, x1 = sorted((xa.value_at(a.x()), xa.value_at(b.x())))
            y0, y1 = sorted((ya.value_at(a.y()), ya.value_at(b.y())))
            mask = (d.x >= x0) & (d.x <= x1) & (d.y >= y0) & (d.y <= y1)
            return d.rows[mask]
        lo, hi = sorted((a.x(), b.x()))
        picked = sorted({i for rect, i, _s in geo["items"]
                         if rect.right() >= lo and rect.left() <= hi})
        if not picked:
            return None
        return P.records_of(m.data, picked)

    def _legend_rows(self, pick):
        m, d = self.model, self.model.data
        if m.kind == "scatter":
            return d.rows[d.classes == pick] if d.classes is not None else d.rows[:0]
        if d.series_of is not None:
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
        kind, a, b = self._hover
        if kind == "points":
            points = a
            rows = d.rows[points]
            n = len(points)
            data = m.plot.get("data", {})
            first = int(points[0]) if n else 0
            lines = [(None, f"{data.get('x')}: {value_text(float(d.x[first]), d.x_kind)}"),
                     (None, f"{data.get('y')}: {value_text(float(d.y[first]), d.y_kind)}")]
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
        else:
            i = self._hover[1]
            rects = [rect for rect, j, _s in self._geo["items"] if j == i]
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

