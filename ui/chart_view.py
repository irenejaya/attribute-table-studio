# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Visualize Statistics: the chart itself, drawn with QPainter.

The view only ever sees the bars (:class:`..core.charts.Bars`), never the
records, so it draws as fast for 10 million records as for 100.  Bars are
always solid in the bar colour; the selected records of a bar (with Split
by, of each coloured part) are drawn from its base in QGIS's selection
colour, so a bar can be partly selected.  Hovering a bar shows a card with
its numbers; a click selects its records (Ctrl adds), a drag across bars
selects the range, a click on a Split by legend entry selects that
category.  Text wears the theme's text colours, never a series colour.
"""

import math

from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen,
)
from qgis.PyQt.QtWidgets import QSizePolicy, QWidget

from ..core import charts as C

# categorical colours checked for colour-blind readers against the chart
# background (light #FFFFFF, dark #2B2B2B), always in this order
SERIES = {
    False: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
            "#e87ba4", "#008300", "#6250d6", "#e34948"],
    True: ["#3987e5", "#d95926", "#199e70", "#c98500",
           "#d55181", "#0ca30c", "#9085e9", "#e66767"],
}
OTHER_COLOUR = {False: "#9E9E9E", True: "#7A7A7A"}
MEAN_COLOUR = "#00ACC1"
MEDIAN_COLOUR = "#D81B60"
MIN_ROW = 18                      # bar chart: smallest row height (px)


def nice_step(top, ticks=6):
    raw = top / float(ticks)
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


class ChartModel:
    """Everything one drawing needs (set by the window)."""

    def __init__(self):
        self.bars = None
        self.stats = {}
        self.title = ""
        self.x_title = ""
        self.y_title = "Records"
        self.fmt_axis = str              # value -> axis text
        self.fmt_bar = None              # bar index -> hover title
        self.fmt_stat = None             # statistic -> legend text
        self.show = set()                # "mean", "median", "std", "normal"
        self.scale = "linear"            # "log", "sqrt"
        self.labels = False
        self.grid = True
        self.colour = None               # QColor of the bars
        self.split_mode = "stacked"      # "side"
        self.horizontal = False          # bar chart: horizontal bars
        self.share = False               # bar chart: values as a share
        self.sel = None                  # selected records per bar
        self.sel_series = None           # [[per series]] per bar
        self.sel_colour = QColor("#ffff00")
        # charts of values other than record counts (the Pivot Table's)
        self.fmt_value = None            # value -> text (None: a record count)
        self.card = None                 # (bar, series) -> (title, [(series, text,
        #                                  strong)], footer): the hover card
        self.sel_records = None          # selected records (legend), else sum(sel)

    @property
    def chart(self):
        return self.bars.chart if self.bars is not None else None


class ChartView(QWidget):
    """Draws a :class:`ChartModel`; emits what the user picks."""

    barsPicked = pyqtSignal(list, bool, object)    # bars, add, series or None
    seriesPicked = pyqtSignal(int, bool)           # series, add
    barMenuRequested = pyqtSignal(int, object, object)  # bar, series or None, global pos

    def __init__(self, theme, metrics, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.metrics = metrics
        self.model = None
        self.message = ""
        self._geo = None                 # geometry of the last drawing
        self._hover = None               # (bar, series or None)
        self._press = None               # bar under the mouse press
        self._drag = None                # bar under the mouse while dragging
        self._signed = False             # negative values: a linear axis through 0
        self.setMouseTracking(True)
        self.setMinimumSize(QSize(360, 260))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------------
    def set_model(self, model):
        self.model = model
        self.message = ""
        self._hover = None
        self._fit_height()
        self.update()

    def set_message(self, text):
        """Text in the middle instead of the chart (reading, no values)."""
        self.message = text
        self._hover = None
        self._fit_height()
        self.update()

    def _fit_height(self):
        # a long bar chart grows taller than the window: its scroll area scrolls
        self.setMinimumHeight(max(260, self.needed_height()))
        self.updateGeometry()

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def needed_height(self):
        """Bar chart rows need room: the window scrolls a long list."""
        m = self.model
        if m is None or not m.horizontal or self.message or m.bars is None:
            return 0
        rows = len(m.bars)
        if m.bars.split is not None and m.split_mode == "side":
            rows *= max(1, len(m.bars.series)) * 0.6
        return int(100 + rows * MIN_ROW)

    def sizeHint(self):
        return QSize(640, max(420, self.needed_height()))

    def minimumSizeHint(self):
        return QSize(360, max(260, self.needed_height()))

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------
    def render_image(self, scale=2.0):
        img = QImage(int(self.width() * scale), int(self.height() * scale),
                     QImage.Format.Format_ARGB32)
        img.fill(QColor(self.theme.base))
        p = QPainter(img)
        p.scale(scale, scale)
        self.paint_chart(p, QRectF(0, 0, self.width(), self.height()), export=True)
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
        self.paint_chart(p, QRectF(0, 0, self.width(), self.height()), export=True)
        p.end()

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        self.paint_chart(p, QRectF(self.rect()))
        p.end()

    def paint_chart(self, p, rect, export=False):
        t = self.theme
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(t.border), 1))
        p.setBrush(QColor(t.base))
        p.drawRoundedRect(r, 4, 4)
        self._geo = {"bars": [], "legend": [], "plot": None}
        base = QFont(self.font())
        fm = QFontMetrics(base)
        m = self.model
        if self.message or m is None or m.bars is None or not len(m.bars):
            p.setPen(QColor(t.text_dim))
            text = self.message or (m.title and "No values to chart") or ""
            p.drawText(r, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
            return
        title = QFont(base)
        title.setPointSizeF(base.pointSizeF() + 3 if base.pointSizeF() > 0 else 12)
        title.setBold(True)
        p.setFont(title)
        p.setPen(QColor(t.text_strong))
        p.drawText(QRectF(r.left() + 10, r.top() + 10, r.width() - 20, 26),
                   Qt.AlignmentFlag.AlignCenter,
                   QFontMetrics(title).elidedText(m.title, Qt.TextElideMode.ElideRight,
                                                  int(r.width() - 20)))
        p.setFont(base)
        if m.horizontal:
            self._paint_hbars(p, r, fm)
        else:
            self._paint_columns(p, r, fm)
        if not export:
            self._paint_drag(p)
            self._paint_hover(p, r, fm)

    # ---- helpers -------------------------------------------------------
    def _f(self, v):
        """Value -> drawing scale (linear, square root or logarithmic)."""
        s = self.model.scale
        if self._signed:
            return float(v)
        if s == "sqrt":
            return math.sqrt(max(v, 0))
        if s == "log":
            return math.log10(1 + max(v, 0))
        return float(v)

    def _ticks(self, top, floor=0.0):
        """(ticks, scaled bottom, scaled top) for the value axis; *floor*
        below 0 (negative values) gives a linear axis through 0."""
        s = self.model.scale
        if floor < 0:
            span = (top - floor) * 1.06
            step = nice_step(span)
            top_v = math.ceil(top * 1.06 / step) * step if top > 0 else 0.0
            lo_v = math.floor(floor * 1.06 / step) * step
            n = int(round((top_v - lo_v) / step))
            ticks = [lo_v + k * step for k in range(n + 1)]
            return ticks, float(lo_v), float(top_v)
        if s == "log":
            ticks, v = [0], 1
            while v <= top * 1.0001:
                ticks.append(v)
                v *= 10
            top_v = max(ticks[-1] * (10 if ticks[-1] < top else 1), 10)
            if ticks[-1] != top_v:
                ticks.append(top_v)
            return ticks, 0.0, self._f(top_v)
        step = nice_step(top * 1.06 if top else 1)
        top_v = math.ceil((top * 1.06 if top else 1) / step) * step
        ticks, v = [], 0.0
        while v <= top_v + 1e-9:
            ticks.append(v)
            v += step
        return ticks, 0.0, self._f(top_v)

    def _series_colour(self, s):
        names = self.model.bars.series
        if s < len(names) and names[s] == "Other" and s == len(names) - 1 \
                and len(names) > C.SPLIT_SERIES:
            return QColor(OTHER_COLOUR[self.theme.dark])
        return QColor(SERIES[self.theme.dark][s % len(SERIES[False])])

    def _bar_colour(self):
        m = self.model
        return QColor(m.colour) if m.colour is not None else QColor(self.theme.bar)

    def _paint_sel(self, p, rect):
        colour = QColor(self.model.sel_colour)
        colour.setAlpha(255)
        p.fillRect(rect, colour)
        p.setPen(QPen(colour.darker(170), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

    def _count_text(self, n):
        m = self.model
        if m.share:
            total = float(m.bars.total or 1)
            return f"{100.0 * n / total:.1f}%"
        if m.fmt_value is not None:
            return m.fmt_value(n)
        return f"{n:,}"

    def _tick_text(self, v, ticks):
        """Axis text of tick *v*: records as whole numbers, other values
        with the decimals the tick step needs."""
        m = self.model
        if m.share:
            return f"{100.0 * v / float(m.bars.total or 1):.0f}%"
        if m.fmt_value is None:
            return f"{int(v):,}"
        step = abs(ticks[1] - ticks[0]) if len(ticks) > 1 else 1.0
        d = 0
        while d < 6 and abs(step * 10 ** d - round(step * 10 ** d)) > 1e-6:
            d += 1
        text = f"{v:,.{d}f}"
        return "0" if not text.strip("-0.,") else text

    @staticmethod
    def _extent(bars, side):
        """(highest, lowest) value the bars reach, 0 included."""
        split = bars.split
        if split is None:
            values = bars.counts
            return max(max(values), 0), min(min(values), 0)
        if side:
            hi = max((max(row) if row else 0) for row in split)
            lo = min((min(row) if row else 0) for row in split)
        else:
            hi = max(sum(c for c in row if c > 0) for row in split)
            lo = min(sum(c for c in row if c < 0) for row in split)
        return max(hi, 0), min(lo, 0)

    # ---- vertical columns: histogram and over time ---------------------
    def _paint_columns(self, p, r, fm):
        t, m = self.theme, self.model
        bars = m.bars
        n = len(bars)
        split = bars.split
        side = split is not None and m.split_mode == "side"
        peak, floor = self._extent(bars, side)
        self._signed = floor < 0
        ticks, lo_t, top_t = self._ticks(peak, floor)
        tick_texts = [self._tick_text(v, ticks) for v in ticks]
        ylab_w = max(fm.horizontalAdvance(s) for s in tick_texts) + 10
        plot = QRectF(r.left() + ylab_w + 30, r.top() + 46, 0, 0)
        legend_w = self._outside_legend_width(fm)
        plot.setRight(r.right() - 18 - legend_w)
        # values under their columns: level when they fit, else slanted
        cats = bars.chart == C.BARS
        slant_len = 0
        if cats:
            est_bw = max(plot.width() / n, 1.0)
            widest_label = max(fm.horizontalAdvance(str(v)) for v in bars.labels)
            if widest_label + 8 > est_bw:
                slant_len = min(widest_label, 150)
        bottom = 54 if not slant_len else int((slant_len + fm.height()) * 0.71 + 40)
        plot.setBottom(r.bottom() - bottom)
        self._geo["plot"] = plot

        def yof(v):
            if top_t <= lo_t:
                return plot.bottom()
            return plot.bottom() - plot.height() * (self._f(v) - lo_t) / (top_t - lo_t)

        # count labels (the grid lines are drawn over the bars, below)
        for v, s in zip(ticks, tick_texts):
            y = yof(v)
            p.setPen(QColor(t.text_dim))
            p.drawText(QRectF(plot.left() - ylab_w - 8, y - 9, ylab_w, 18),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, s)
        p.save()
        p.translate(r.left() + 14, plot.center().y())
        p.rotate(-90)
        p.setPen(QColor(t.text))
        p.drawText(QRectF(-plot.height() / 2, -10, plot.height(), 20),
                   Qt.AlignmentFlag.AlignCenter,
                   m.y_title + (" (log scale)" if m.scale == "log" else
                                " (square root scale)" if m.scale == "sqrt" else ""))
        p.restore()

        bw = plot.width() / n
        hist = bars.chart == C.HISTOGRAM
        if hist and not bars.integer:
            lo, hi = bars.lows[0], bars.highs[-1]
        elif hist:
            lo, hi = bars.lows[0] - 0.5, bars.highs[-1] + 0.5
        else:
            lo, hi = 0.0, float(n)

        def xof(value):
            return plot.left() + plot.width() * (value - lo) / (hi - lo) if hi > lo else plot.left()

        # ± 1 standard deviation band (histogram of numbers)
        st = m.stats or {}
        if hist and "std" in m.show and st.get("std") and st.get("mean") is not None:
            band = QColor(t.text_dim)
            band.setAlpha(26)
            x0 = max(plot.left(), xof(st["mean"] - st["std"]))
            x1 = min(plot.right(), xof(st["mean"] + st["std"]))
            if x1 > x0:
                p.fillRect(QRectF(x0, plot.top(), x1 - x0, plot.height()), band)

        colour = self._bar_colour()
        hover_bar = self._hover[0] if self._hover else None
        sel = m.sel or [0] * n
        geo = self._geo["bars"]
        count_labels = []
        gap = 1.5 if bw > 4 else 0.0
        # bins touch (a histogram); one column per value stands apart, thin
        inset, cw = gap / 3, max(bw - gap, 1.0)
        if cats:
            cw = max(min(bw * 0.72, 64.0), 1.0)
            inset = (bw - cw) / 2
        base = yof(0)                        # bars grow up from 0, negative ones down

        def span(y_a, y_b):
            return min(y_a, y_b), abs(y_a - y_b)

        for i in range(n):
            x = plot.left() + i * bw
            if split is None:
                c = bars.counts[i]
                top, h = span(base, yof(c))
                rect = QRectF(x + inset, top, cw, h)
                p.fillRect(rect, QColor(t.bar_hover) if i == hover_bar and m.colour is None
                           else (colour.darker(125) if i == hover_bar else colour))
                if sel[i]:
                    top, h = span(base, yof(sel[i]))
                    self._paint_sel(p, QRectF(rect.left(), top, rect.width(), h))
                if c:
                    geo.append((self._col_hit(rect, base, c < 0), i, None))
                if m.labels and c:
                    count_labels.append((rect, c))
                continue
            row = split[i]
            sel_row = m.sel_series[i] if m.sel_series else None
            if side:
                k = max(1, len(row))
                sw = max(cw / k, 1.0)
                for s, c in enumerate(row):
                    top, h = span(base, yof(c))
                    rect = QRectF(x + inset + s * sw, top, max(sw - 0.5, 0.8), h)
                    if c:
                        p.fillRect(rect, self._series_colour(s))
                        if sel_row and sel_row[s]:
                            top, h = span(base, yof(sel_row[s]))
                            self._paint_sel(p, QRectF(rect.left(), top, rect.width(), h))
                    if c:
                        geo.append((self._col_hit(rect, base, c < 0), i, s))
                    if m.labels and c:
                        count_labels.append((rect, c))
                hi = max(max(row) if row else 0, 0)
                lo = min(min(row) if row else 0, 0)
                if hi or lo:
                    geo.append((self._span_hit(x + inset, cw, yof(hi), yof(lo), base, hi, lo),
                                i, None))
            else:
                pos = neg = 0                    # positive parts up, negative down
                for s, c in enumerate(row):
                    if not c:
                        continue
                    a = pos if c > 0 else neg
                    if c > 0:
                        pos = a + c
                    else:
                        neg = a + c
                    y_a = yof(a)
                    top, h = span(y_a, yof(a + c))
                    rect = QRectF(x + inset, top, cw, h)
                    p.fillRect(rect.adjusted(0, 0.5, 0, -0.5) if rect.height() > 3 else rect,
                               self._series_colour(s))
                    if sel_row and sel_row[s]:       # from the base of this part
                        top, h = span(y_a, yof(a + sel_row[s]))
                        self._paint_sel(p, QRectF(rect.left(), top, rect.width(), h))
                    geo.append((rect, i, s))
                if pos or neg:
                    geo.append((self._span_hit(x + inset, cw, yof(pos), yof(neg), base, pos,
                                               neg), i, None))
                total = bars.counts[i]
                if m.labels and total:
                    top, h = span(base, yof(pos) if total > 0 else yof(neg))
                    count_labels.append((QRectF(x, top, bw, h), total))
        if m.grid:                           # over the bars, to read them off
            self._grid_lines(p, plot, [yof(v) for v in ticks if v != 0], across=True)
        for rect, c in count_labels:
            self._column_label(p, rect, c, fm)
        p.setPen(QPen(QColor(t.border), 1))
        p.drawLine(QPointF(plot.left(), base), QPointF(plot.right(), base))

        # x labels: as many as fit, never rotated
        p.setPen(QColor(t.text_dim))
        if cats:
            self._category_labels(p, plot, bars, bw, fm, slant_len)
            marks = []
        elif hist and not bars.integer:
            marks = [(bars.lows[k], m.fmt_axis(bars.lows[k])) for k in range(n)]
            marks.append((bars.highs[-1], m.fmt_axis(bars.highs[-1])))
            positions = [xof(v) for v, _s in marks]
        else:
            texts = bars.axis_labels or bars.labels
            marks = [(k, m.fmt_axis(texts[k]) if hist else str(texts[k]))
                     for k in range(n)]
            positions = [plot.left() + bw * (k + 0.5) for k in range(n)]
        if marks:
            widest = max(fm.horizontalAdvance(s) for _v, s in marks) + 16
            spacing = plot.width() / max(len(marks) - (0 if not hist or bars.integer else 1), 1)
            every = max(1, math.ceil(widest / max(spacing, 1)))
            for k, ((_v, s), x) in enumerate(zip(marks, positions)):
                if k % every:
                    continue
                p.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() + 4))
                p.drawText(QRectF(x - widest / 2 - 20, plot.bottom() + 6, widest + 40, 18),
                           Qt.AlignmentFlag.AlignCenter, s)
        p.setPen(QColor(t.text))
        title_y = plot.bottom() + 26 if not slant_len else r.bottom() - 26
        p.drawText(QRectF(plot.left(), title_y, plot.width(), 20),
                   Qt.AlignmentFlag.AlignCenter, m.x_title)

        # statistic lines, normal curve and legend
        legend = []
        self._series_legend(legend)
        fmt_stat = m.fmt_stat or m.fmt_axis
        if hist:
            for key, col, name in (("mean", MEAN_COLOUR, "Mean"),
                                   ("median", MEDIAN_COLOUR, "Median")):
                v = st.get(key)
                if key not in m.show or v is None:
                    continue
                x = xof(v)
                if plot.left() <= x <= plot.right():
                    pen = QPen(QColor(col), 1.6)
                    pen.setStyle(Qt.PenStyle.DashLine)
                    p.setPen(pen)
                    p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
                legend.append(("dash", QColor(col), f"{name}  {fmt_stat(v)}", None))
            if "std" in m.show and st.get("std"):
                band = QColor(t.text_dim)
                legend.append(("band", band, f"± 1 Std. Dev.  {fmt_stat(st['std'])}", None))
            if "normal" in m.show and st.get("std") and bars.width:
                self._normal_curve(p, plot, xof, yof, st, bars, lo, hi)
                legend.append(("line", QColor(t.text_strong), "Normal Distribution Curve", None))
        self._paint_legend(p, plot, legend, fm, corner_pref=("tr", "tl"))

    def _grid_lines(self, p, plot, positions, across=True):
        """Grid lines drawn over the bars (*across*: horizontal lines for
        columns, else vertical ones for bars), in the theme's soft line
        colour, so a bar's height can be read where it meets a line."""
        p.setPen(QPen(QColor(self.theme.border_soft), 1))
        for pos in positions:
            if across:
                p.drawLine(QPointF(plot.left(), pos), QPointF(plot.right(), pos))
            else:
                p.drawLine(QPointF(pos, plot.top()), QPointF(pos, plot.bottom()))

    def _category_labels(self, p, plot, bars, bw, fm, slant_len):
        """Values under their columns: level and shortened to the column
        when they fit, else slanted at 45° (every n-th one when even that
        is too tight), shortened with … beyond 150 px."""
        t = self.theme
        n = len(bars)
        fh = fm.height()
        dim = {bars.other, bars.null_bar}
        if not slant_len:
            for k in range(n):
                x = plot.left() + bw * (k + 0.5)
                p.setPen(QColor(t.text_dim if k in dim else t.text))
                text = fm.elidedText(str(bars.labels[k]), Qt.TextElideMode.ElideRight,
                                     int(max(bw - 4, 8)))
                p.drawText(QRectF(x - bw / 2, plot.bottom() + 6, bw, 18),
                           Qt.AlignmentFlag.AlignCenter, text)
            return
        every = max(1, math.ceil(fh * 1.42 / max(bw, 1.0)))
        for k in range(0, n, every):
            x = plot.left() + bw * (k + 0.5)
            p.setPen(QColor(t.text_dim if k in dim else t.text))
            text = fm.elidedText(str(bars.labels[k]), Qt.TextElideMode.ElideRight,
                                 int(slant_len))
            p.save()
            p.translate(x + fh * 0.3, plot.bottom() + 8)
            p.rotate(-45)
            p.drawText(QRectF(-slant_len - 6, -fh / 2, slant_len + 6, fh),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            p.restore()

    @staticmethod
    def _col_hit(rect, base, down=False):
        """Hover and click area of a column: the bar itself (3 px of slack
        past its end, at least 6 px tall), never the empty space beyond
        it.  *base* is the 0 line; *down*, a negative bar below it."""
        if down:
            bottom = max(rect.bottom() + 3, base + 6)
            return QRectF(rect.left(), base, rect.width(), bottom - base)
        top = min(rect.top() - 3, base - 6)
        return QRectF(rect.left(), top, rect.width(), base - top)

    @classmethod
    def _span_hit(cls, x, w, y_hi, y_lo, base, hi, lo):
        """Area of a whole column (all its parts) from *lo* to *hi*."""
        if not lo:
            return cls._col_hit(QRectF(x, y_hi, w, base - y_hi), base)
        if not hi:
            return cls._col_hit(QRectF(x, base, w, y_lo - base), base, True)
        return QRectF(x, y_hi - 3, w, y_lo - y_hi + 6)

    @staticmethod
    def _row_hit(rect, base, left=False):
        """The same for a horizontal bar (slack after its end); *left*, a
        negative bar left of the 0 line."""
        if left:
            start = min(rect.left() - 3, base - 6)
            return QRectF(start, rect.top(), base - start, rect.height())
        right = max(rect.right() + 3, base + 6)
        return QRectF(base, rect.top(), right - base, rect.height())

    @classmethod
    def _row_span_hit(cls, y, h, x_hi, x_lo, base, hi, lo):
        if not lo:
            return cls._row_hit(QRectF(base, y, x_hi - base, h), base)
        if not hi:
            return cls._row_hit(QRectF(x_lo, y, base - x_lo, h), base, True)
        return QRectF(x_lo - 3, y, x_hi - x_lo + 6, h)

    def _column_label(self, p, rect, c, fm):
        p.setPen(QColor(self.theme.text_dim))
        text = self._count_text(c)
        w = fm.horizontalAdvance(text)
        if w > rect.width() + 24:
            return
        y = rect.top() - 17 if c >= 0 else rect.bottom() + 2    # past the bar's end
        p.drawText(QRectF(rect.center().x() - w / 2 - 6, y, w + 12, 15),
                   Qt.AlignmentFlag.AlignCenter, text)

    def _normal_curve(self, p, plot, xof, yof, st, bars, lo, hi):
        mean, std = st["mean"], st["std"]
        total = float(bars.total) * bars.width
        path = QPainterPath()
        for k in range(161):
            xv = lo + (hi - lo) * k / 160.0
            yv = total * math.exp(-0.5 * ((xv - mean) / std) ** 2) / (std * math.sqrt(2 * math.pi))
            pt = QPointF(xof(xv), max(plot.top(), yof(yv)))
            if k:
                path.lineTo(pt)
            else:
                path.moveTo(pt)
        p.setPen(QPen(QColor(self.theme.text_strong), 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    # ---- horizontal bars: text and yes / no ------------------------------
    def _paint_hbars(self, p, r, fm):
        t, m = self.theme, self.model
        bars = m.bars
        n = len(bars)
        split = bars.split
        side = split is not None and m.split_mode == "side"
        total = float(bars.total or 1)
        peak, floor = self._extent(bars, side)
        self._signed = floor < 0
        lab_w = min(max(fm.horizontalAdvance(str(s)) for s in bars.labels) + 16,
                    int(r.width() * 0.35))
        value_texts = [self._value_text(c, total) for c in bars.counts]
        val_w = (max(fm.horizontalAdvance(s) for s in value_texts) + 18) if m.labels else 18
        self._value_col_w = val_w
        plot = QRectF(r.left() + lab_w + 12, r.top() + 50, 0, 0)
        if self._signed and m.labels:        # room for the values of negative bars
            plot.setLeft(plot.left() + val_w)
        plot.setRight(r.right() - val_w - 8 - self._outside_legend_width(fm))
        plot.setBottom(r.bottom() - 46)
        self._geo["plot"] = plot
        ticks, lo_t, top_t = self._ticks(peak, floor)

        def xof(v):
            if top_t <= lo_t:
                return plot.left()
            return plot.left() + plot.width() * (self._f(v) - lo_t) / (top_t - lo_t)

        for v in ticks:
            x = xof(v)
            p.setPen(QColor(t.text_dim))
            p.drawText(QRectF(x - 40, plot.bottom() + 4, 80, 18), Qt.AlignmentFlag.AlignCenter,
                       self._tick_text(v, ticks))
        p.setPen(QColor(t.text))
        p.drawText(QRectF(plot.left(), plot.bottom() + 22, plot.width(), 20),
                   Qt.AlignmentFlag.AlignCenter,
                   ("Share of records" if m.share else m.y_title)
                   + (" (log scale)" if m.scale == "log" else
                      " (square root scale)" if m.scale == "sqrt" else ""))
        bh = plot.height() / n
        value_places = []
        colour = self._bar_colour()
        hover_bar = self._hover[0] if self._hover else None
        sel = m.sel or [0] * n
        geo = self._geo["bars"]
        base = xof(0)                        # bars grow right from 0, negative ones left

        def span(x_a, x_b):
            return min(x_a, x_b), abs(x_a - x_b)

        for i in range(n):
            y = plot.top() + i * bh
            thick = min(bh * 0.68, 34.0)        # thin marks, also for few bars
            inner = QRectF(base, y + (bh - thick) / 2, 0, thick)
            if split is None:
                c = bars.counts[i]
                hi, lo = max(c, 0), min(c, 0)
                left, w = span(base, xof(c))
                rect = QRectF(left, inner.top(), w, inner.height())
                p.fillRect(rect, QColor(t.bar_hover) if i == hover_bar and m.colour is None
                           else (colour.darker(125) if i == hover_bar else colour))
                if sel[i]:
                    left, w = span(base, xof(sel[i]))
                    self._paint_sel(p, QRectF(left, rect.top(), w, rect.height()))
                if c:
                    geo.append((self._row_hit(rect, base, c < 0), i, None))
            elif side:
                row = split[i]
                k = max(1, len(row))
                sh = inner.height() / k
                sel_row = m.sel_series[i] if m.sel_series else None
                for s, c in enumerate(row):
                    left, w = span(base, xof(c))
                    rect = QRectF(left, inner.top() + s * sh, w, max(sh - 0.5, 0.8))
                    if c:
                        p.fillRect(rect, self._series_colour(s))
                        if sel_row and sel_row[s]:
                            left, w = span(base, xof(sel_row[s]))
                            self._paint_sel(p, QRectF(left, rect.top(), w, rect.height()))
                    if c:
                        geo.append((self._row_hit(rect, base, c < 0), i, s))
                hi = max(max(row) if row else 0, 0)
                lo = min(min(row) if row else 0, 0)
                if hi or lo:
                    geo.append((self._row_span_hit(inner.top(), inner.height(), xof(hi),
                                                   xof(lo), base, hi, lo), i, None))
            else:
                hi = lo = 0                      # positive parts right, negative left
                sel_row = m.sel_series[i] if m.sel_series else None
                for s, c in enumerate(split[i]):
                    if not c:
                        continue
                    a = hi if c > 0 else lo
                    if c > 0:
                        hi = a + c
                    else:
                        lo = a + c
                    x_a = xof(a)
                    left, w = span(x_a, xof(a + c))
                    rect = QRectF(left, inner.top(), w, inner.height())
                    p.fillRect(rect.adjusted(0.5, 0, -0.5, 0) if rect.width() > 3 else rect,
                               self._series_colour(s))
                    if sel_row and sel_row[s]:       # from the start of this part
                        left, w = span(x_a, xof(a + sel_row[s]))
                        self._paint_sel(p, QRectF(left, rect.top(), w, rect.height()))
                    geo.append((rect, i, s))
                if hi or lo:
                    geo.append((self._row_span_hit(inner.top(), inner.height(), xof(hi),
                                                   xof(lo), base, hi, lo), i, None))
            label = str(bars.labels[i])
            p.setPen(QColor(t.text_dim if i in (bars.other, bars.null_bar) else t.text))
            p.drawText(QRectF(r.left() + 8, y, lab_w, bh),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(label, Qt.TextElideMode.ElideRight, lab_w - 4))
            if m.labels:                         # "Label bars with their count"
                if bars.counts[i] >= 0:
                    value_places.append((QRectF(xof(hi) + 8, y, val_w + 40, bh),
                                         value_texts[i], Qt.AlignmentFlag.AlignLeft))
                else:                            # a negative bar: left of its end
                    value_places.append((QRectF(xof(lo) - 8 - val_w - 40, y, val_w + 40, bh),
                                         value_texts[i], Qt.AlignmentFlag.AlignRight))
        if m.grid:                           # over the bars, to read them off
            self._grid_lines(p, plot, [xof(v) for v in ticks if v != 0], across=False)
        p.setPen(QColor(t.text_dim))
        for rect, text, align in value_places:
            p.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, text)
        p.setPen(QPen(QColor(t.border), 1))
        p.drawLine(QPointF(base, plot.top()), QPointF(base, plot.bottom()))
        legend = []
        self._series_legend(legend)
        self._paint_legend(p, plot, legend, fm, corner_pref=("br", "tr"))

    def _value_text(self, c, total):
        if self.model.fmt_value is not None:
            return self.model.fmt_value(c)
        if self.model.share:
            return f"{100.0 * c / total:.1f}%  ({c:,})"
        return f"{c:,}  ({100.0 * c / total:.0f}%)"

    # ---- legend --------------------------------------------------------
    def _series_legend(self, legend):
        m = self.model
        bars = m.bars
        if bars.split is not None:
            for s, name in enumerate(bars.series):
                legend.append(("box", self._series_colour(s), str(name), s))
        n_sel = m.sel_records if m.sel_records is not None else (sum(m.sel) if m.sel else 0)
        if n_sel:
            legend.append(("sel", QColor(m.sel_colour), f"Selected  {n_sel:,}", None))

    def _legend_text_width(self, fm, texts):
        return max(fm.horizontalAdvance(s) for s in texts) + 42

    def _outside_legend_width(self, fm):
        """With Split by the legend has a column of its own right of the
        plot (so it never hides a bar); 0 otherwise."""
        m = self.model
        if m.bars.split is None:
            return 0
        texts = [str(s) for s in m.bars.series] + ["Selected  000,000,000",
                                                   "± 1 Std. Dev.  000,000.00",
                                                   "Normal Distribution Curve"]
        return int(min(self._legend_text_width(fm, texts), 220)) + 12

    def _paint_legend(self, p, plot, legend, fm, corner_pref=("tr", "tl")):
        if not legend:
            return
        t = self.theme
        lw = self._legend_text_width(fm, [s for _k, _c, s, _x in legend])
        lh = 10 + 18 * len(legend)
        outside = self.model.bars.split is not None
        if outside:
            lw = min(lw, 220)
        else:
            lw = min(lw, plot.width() * 0.6)

        def box_at(corner):
            x = plot.right() - lw - 8 if corner[1] == "r" else plot.left() + 8
            y = plot.top() + 6 if corner[0] == "t" else plot.bottom() - lh - 6
            return QRectF(x, y, lw, lh)

        def overlap(box):
            area = 0.0
            for rect, _i, s in self._geo["bars"]:
                if s is None and self.model.bars.split is not None:
                    continue
                inter = rect.intersected(box)
                if not inter.isEmpty():
                    area += inter.width() * inter.height()
            return area

        candidates = [box_at(c) for c in corner_pref]
        if outside:
            right = plot.right() + (self._value_col_w if self.model.horizontal else 0)
            box = QRectF(right + 12, plot.top(), lw, lh)
        elif not self.model.horizontal:
            box = min(candidates, key=overlap)
        else:
            box = candidates[0]
        p.setPen(QPen(QColor(t.border_soft), 1))
        p.setBrush(QColor(t.base))
        p.drawRoundedRect(box, 3, 3)
        self._geo["legend_box"] = box
        rows = self._geo["legend"]
        for k, (kind, colour, text, series) in enumerate(legend):
            y = box.top() + 14 + 18 * k
            sw = QRectF(box.left() + 10, y - 5, 12, 10)
            if kind == "box":
                p.fillRect(sw, colour)
            elif kind == "sel":
                self._paint_sel(p, sw)
            elif kind == "band":
                band = QColor(colour)
                band.setAlpha(60)
                p.fillRect(sw, band)
            else:
                pen = QPen(colour, 2 if kind == "line" else 1.6)
                if kind == "dash":
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(box.left() + 8, y), QPointF(box.left() + 24, y))
            p.setPen(QColor(t.text))
            p.drawText(QRectF(box.left() + 30, y - 9, lw - 36, 18), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, int(lw - 36)))
            if series is not None:
                rows.append((QRectF(box.left(), y - 9, lw, 18), series))

    # ---- hover card, drag range ----------------------------------------
    def _paint_drag(self, p):
        if self._press is None or self._drag is None or self._drag == self._press:
            return
        lo, hi = sorted((self._press, self._drag))
        rects = [rect for rect, i, s in self._geo["bars"] if s is None and lo <= i <= hi]
        if not rects:
            return
        area = rects[0]
        for rect in rects[1:]:
            area = area.united(rect)
        pen = QPen(QColor(self.theme.text_strong), 1.2)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(area.adjusted(1, 1, -1, -1))

    def card_lines(self, i, series=None):
        """(title, [(swatch colour or None, text)], footer) for bar *i*."""
        m = self.model
        bars = m.bars
        if m.card is not None:               # the window's own numbers
            title, lines, foot = m.card(i, series)
            return title, [(self._series_colour(s) if s is not None else None, text, strong)
                           for s, text, strong in lines], foot
        total = float(bars.total or 1)
        title = m.fmt_bar(i) if m.fmt_bar else str(bars.labels[i])
        n = bars.counts[i]
        lines = []
        if bars.split is not None:
            sel_row = m.sel_series[i] if m.sel_series else None
            for s, c in enumerate(bars.split[i]):
                if not c:
                    continue
                text = f"{bars.series[s]}: {c:,}"
                if sel_row and sel_row[s]:
                    text += f"  ({sel_row[s]:,} selected)"
                lines.append((self._series_colour(s), text, s == series))
            lines.append((None, f"Total: {n:,} records  ({100.0 * n / total:.1f}%)", False))
        else:
            lines.append((None, f"{n:,} records  ({100.0 * n / total:.1f}%)", False))
            if m.sel and m.sel[i]:
                lines.append((None, f"{m.sel[i]:,} selected", False))
        if bars.split is not None and series is not None:
            foot = "Click to select this category here · Ctrl+click to add"
        else:
            foot = "Click to select · Ctrl+click to add · drag for a range"
        return title, lines, foot

    def _paint_hover(self, p, r, fm):
        if self._hover is None or self._press is not None:
            return
        i, series = self._hover
        if i >= len(self.model.bars):
            return
        t = self.theme
        title, lines, foot = self.card_lines(i, series)
        bold = QFont(self.font())
        bold.setBold(True)
        bfm = QFontMetrics(bold)
        widths = [bfm.horizontalAdvance(title)] + [
            fm.horizontalAdvance(s) + (16 if c is not None else 0) for c, s, _b in lines]
        widths.append(fm.horizontalAdvance(foot))
        tw = max(widths) + 22
        th = 10 + 18 * (len(lines) + 2)
        anchor = None
        for rect, bi, s in self._geo["bars"]:
            if bi == i and s is None:
                anchor = rect
                break
        if anchor is None:
            return
        plot = self._geo["plot"]
        if self.model.horizontal:
            x = plot.left() + 40
            y = anchor.bottom() + 2
            if y + th > r.bottom() - 4:
                y = anchor.top() - th - 2
        else:
            x = anchor.right() + 8
            if x + tw > r.right() - 4:
                x = anchor.left() - tw - 8
            y = plot.top() + 30
        card = QRectF(max(r.left() + 4, min(x, r.right() - tw - 4)),
                      max(r.top() + 4, min(y, r.bottom() - th - 4)), tw, th)
        box = self._geo.get("legend_box")
        if box is not None and card.intersects(box):      # keep the legend readable
            card.moveTop(box.bottom() + 8)
            if card.bottom() > r.bottom() - 4:
                card.moveBottom(box.top() - 8)
        p.setPen(QPen(QColor(t.border), 1))
        p.setBrush(QColor(t.card))
        p.drawRoundedRect(card, 4, 4)
        p.setFont(bold)
        p.setPen(QColor(t.text_strong))
        p.drawText(QRectF(card.left() + 10, card.top() + 5, tw - 14, 18),
                   Qt.AlignmentFlag.AlignVCenter, title)
        for k, (colour, text, strong) in enumerate(lines, 1):
            x0 = card.left() + 10
            if colour is not None:
                p.fillRect(QRectF(x0, card.top() + 10 + 18 * k, 10, 9), colour)
                x0 += 16
            p.setFont(bold if strong else self.font())
            p.setPen(QColor(t.text))
            p.drawText(QRectF(x0, card.top() + 5 + 18 * k, tw, 18),
                       Qt.AlignmentFlag.AlignVCenter, text)
        p.setFont(self.font())
        p.setPen(QColor(t.text_dim))
        p.drawText(QRectF(card.left() + 10, card.top() + 5 + 18 * (len(lines) + 1), tw, 18),
                   Qt.AlignmentFlag.AlignVCenter, foot)

    # ------------------------------------------------------------------
    # mouse
    # ------------------------------------------------------------------
    def hit(self, pos):
        """(bar, series or None) under *pos*, or None."""
        if not self._geo or self.message or self.model is None:
            return None
        pt = QPointF(pos)
        found = None
        for rect, i, s in self._geo["bars"]:
            if rect.contains(pt):
                if s is not None:
                    return i, s
                found = (i, None)
        return found

    def legend_hit(self, pos):
        if not self._geo:
            return None
        pt = QPointF(pos)
        for rect, s in self._geo["legend"]:
            if rect.contains(pt):
                return s
        return None

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        hit = self.hit(pos)
        if self._press is not None:
            if hit is not None and hit[0] != self._drag:
                self._drag = hit[0]
                self.update()
            return
        over_legend = self.legend_hit(pos) is not None
        self.setCursor(Qt.CursorShape.PointingHandCursor if (hit or over_legend)
                       else Qt.CursorShape.ArrowCursor)
        if hit != self._hover:
            self._hover = hit
            self.update()

    def contextMenuEvent(self, event):
        hit = self.hit(event.pos())
        if hit is None:
            return super().contextMenuEvent(event)
        self._hover = None
        self.update()
        self.barMenuRequested.emit(hit[0], hit[1], event.globalPos())

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        hit = self.hit(pos)
        self._press = hit[0] if hit else None
        self._drag = self._press
        self._press_series = hit[1] if hit else None
        self._press_legend = self.legend_hit(pos) if hit is None else None
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        add = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        press, drag = self._press, self._drag
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        hit = self.hit(pos)
        if press is not None and hit is not None:
            drag = hit[0]                    # the range ends where the button is let go
        self._press = self._drag = None
        legend = getattr(self, "_press_legend", None)
        self._press_legend = None
        self.update()
        if press is None:
            if legend is not None:
                self.seriesPicked.emit(legend, add)
            return
        if drag is None or drag == press:
            self.barsPicked.emit([press], add, self._press_series)
        else:
            lo, hi = sorted((press, drag))
            self.barsPicked.emit(list(range(lo, hi + 1)), add, None)
