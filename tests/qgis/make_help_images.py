# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Make the pictures of the Help windows (help/*.png), from made-up data.

    QT_QPA_PLATFORM=offscreen python tests/qgis/make_help_images.py [OUT_DIR]

Every picture tab of ui/help_pages.py: the tool is prepared (setup), the
tab's parts are cut out of the window (crops) at twice their size, and each
item gets a red numbered badge on its widget (target), with a red outline
around wide ones.  A target that is missing, hidden or outside the picture
stops the script, so the numbers always match the text.  QGIS 4's light
look (one set of light pictures); at the end scripts/shrink_png.py makes
them about four times smaller when a Python with Pillow is found.
"""

import os
import subprocess
import sys

os.environ.setdefault("QT_SCALE_FACTOR", "2")        # crisp on high-DPI screens
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402
import numpy as np  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "help"))
APP = H.start_qgis(None)
H.plugin_package()

from qgis.PyQt.QtCore import QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap  # noqa: E402
from qgis.PyQt.QtWidgets import QAbstractButton, QCheckBox, QFrame, QWidget  # noqa: E402

from attribute_table_studio import classFactory  # noqa: E402
from attribute_table_studio.core import fields as F  # noqa: E402
from attribute_table_studio.core import filters as FL  # noqa: E402
from attribute_table_studio.core import pivot as P  # noqa: E402
from attribute_table_studio.core.compat import SELECT_SET  # noqa: E402
from attribute_table_studio.ui import explorer_edit as E  # noqa: E402
from attribute_table_studio.ui.help_pages import PAGES  # noqa: E402
from sample_data import sample_parcels, sample_update  # noqa: E402

E.ASK = lambda parent, title, text, choices, default=None, icon="question": \
    default or choices[0][0]

RED = QColor("#d32f2f")
PAD = 6                          # white around each part (more where a badge needs it)
EDGE = 80                        # a part this close to the window's edge reaches it
GAP = 14                         # between parts
MAX_WIDTH = 1040                 # wider parts are cut in two rows
BADGE = 18


# ----------------------------------------------------------------------------
# finding targets
# ----------------------------------------------------------------------------

def widget_rect(win, w):
    if w is None or not isinstance(w, QWidget):
        raise ValueError(f"not a widget: {w!r}")
    if not w.isVisibleTo(win):
        raise ValueError(f"hidden: {w.objectName() or type(w).__name__}")
    return QRect(w.mapTo(win, QPoint(0, 0)), w.size())


def viewport_rect(win, viewport, rect):
    return QRect(viewport.mapTo(win, rect.topLeft()), rect.size())


def common_special(panel, win, name):
    """Targets every tool has; None for the tool's own."""
    if name == "status":                 # the status bar, as far as its text goes
        msg = panel.status.message
        r = widget_rect(win, panel.status)
        text_w = msg.fontMetrics().horizontalAdvance(msg.fullText())
        right = min(r.right(), msg.mapTo(win, QPoint(0, 0)).x() + text_w + 24)
        return QRect(r.left(), r.top(), right - r.left(), r.height())
    return None


def resolve(panel, win, target, special):
    rect = QRect()
    for part in target.split("+"):
        if part.startswith("@"):
            r = common_special(panel, win, part[1:]) or special(panel, win, part[1:])
        else:
            r = widget_rect(win, eval("panel." + part, {"panel": panel}))   # noqa: S307
        rect = r if rect.isNull() else rect.united(r)
    return rect


# ----------------------------------------------------------------------------
# drawing
# ----------------------------------------------------------------------------

def white_pixmap(size, scale):
    pix = QPixmap(int(size.width() * scale), int(size.height() * scale))
    pix.setDevicePixelRatio(scale)
    pix.fill(QColor("#ffffff"))
    return pix


def pixels(pix):
    """The pixmap's colours as an (h, w, 3) int16 array."""
    img = pix.toImage().convertToFormat(QImage.Format.Format_RGB32)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    rows = np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine())
    return rows[:, :img.width() * 4].reshape(img.height(), img.width(), 4)[..., :3] \
        .astype(np.int16)


def place_badges(shot, origin, scale, rects):
    """Badge centre of each item (window coordinates).  The first of a few
    places around its target (its top right corner first) where the badge
    covers nothing: no text or icon in *shot* (the part on white, its top
    left at *origin*), no other item and no badge placed before it."""
    radius = BADGE / 2 + 1
    height, width = shot.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]

    def ink(cx, cy, own):
        x0, y0 = (cx - origin.x()) * scale, (cy - origin.y()) * scale
        r = radius * scale
        a, b = int(x0 - r), int(y0 - r)
        c, d = int(x0 + r) + 1, int(y0 + r) + 1
        if a < 0 or b < 0 or c > width or d > height:
            return 1.0
        sx, sy = xx[b:d, a:c], yy[b:d, a:c]
        mask = (sx - x0) ** 2 + (sy - y0) ** 2 <= r * r
        # the target's own edge is under its outline anyway
        o = QRectF(own).adjusted(-3, -3, 3, 3)
        i = QRectF(own).adjusted(3, 3, -3, -3)
        ox = (sx / scale + origin.x(), sy / scale + origin.y())
        in_o = (ox[0] >= o.left()) & (ox[0] <= o.right()) & (ox[1] >= o.top()) & (ox[1] <= o.bottom())
        in_i = (ox[0] >= i.left()) & (ox[0] <= i.right()) & (ox[1] >= i.top()) & (ox[1] <= i.bottom())
        mask &= ~(in_o & ~in_i)
        if not mask.any():
            return 0.0
        block = shot[b:d, a:c]
        ref = np.median(block[mask], axis=0)
        diff = (np.abs(block - ref).sum(axis=2) > 90) & mask
        # a frame or a separator line crossing the badge is no harm: drop the
        # rows and columns that are (nearly) all different
        count = mask.sum(axis=1)
        diff[(diff.sum(axis=1) >= 0.8 * count) & (count > 0), :] = False
        count = mask.sum(axis=0)
        diff[:, (diff.sum(axis=0) >= 0.8 * count) & (count > 0)] = False
        return float(diff.sum() / mask.sum())

    placed = {}
    for n, rect in sorted(rects, key=lambda nr: nr[1].width() * nr[1].height()):
        h = BADGE / 2                      # (small targets choose first)
        L, T, R, B = rect.left(), rect.top(), rect.right(), rect.bottom()
        cx, cy = (L + R) / 2, (T + B) / 2
        corner, outside, right, above = (R - 1, T + 1), (R + h - 2, T - h + 3), \
            (R + h + 2, cy), (cx, T - h - 1)
        small = rect.width() <= 40 and rect.height() <= 40     # an icon: above it
        spots = [above, corner, outside, right] if small else [corner, outside, right, above]
        spots += [(L - h - 2, cy), (L + 1, T + 1), (cx, B + h + 1),
                  (R + h + 2, T - h - 1), (L - h - 2, T - h - 1), (cx, T - h - BADGE - 3)]
        best = None
        for k, (x, y) in enumerate(spots):
            if any((x - px) ** 2 + (y - py) ** 2 < (BADGE + 1) ** 2
                   for px, py in placed.values()):
                continue
            box = QRect(int(x - h), int(y - h), BADGE, BADGE)
            score = ink(x, y, rect) + 0.002 * k
            for m, other in rects:                 # on another item
                if m != n and not other.intersects(rect):
                    over = other.adjusted(3, 3, -3, -3).intersected(box)   # (its margin is free)
                    score += 0.6 * over.width() * over.height() / BADGE ** 2
            if score < 0.04:
                best = (score, x, y)
                break
            if best is None or score < best[0]:
                best = (score, x, y)
        if best is None:
            raise ValueError(f"no room for badge {n}")
        placed[n] = best[1:]
    return placed


def draw(pix, region, rects, badges):
    """Outlines and numbered badges (window coordinates; *region* is the
    picture's)."""
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    white = QColor("#ffffff")
    font = QFont()
    font.setBold(True)
    font.setPixelSize(11)
    p.setFont(font)
    for n, rect in rects:
        r = rect.translated(-region.left(), -region.top())
        if r.width() > 1.8 * r.height() and r.height() >= 14:
            p.setPen(QPen(RED, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(-2, -2, 2, 2), 4, 4)
    for n, _rect in rects:
        x, y = badges[n]
        badge = QRectF(x - region.left() - BADGE / 2, y - region.top() - BADGE / 2, BADGE, BADGE)
        p.setPen(QPen(white, 1.6))
        p.setBrush(RED)
        p.drawEllipse(badge)
        p.setPen(white)
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(n))
    p.end()


def split_point(width, rects):
    """Where to cut a wide part in two: near the middle, through no item."""
    blocked = [(r.left() - BADGE, r.right() + BADGE) for _n, r in rects]
    best = None
    for x in range(int(width * 0.3), int(width * 0.7)):
        if any(a <= x <= b for a, b in blocked):
            continue
        if best is None or abs(x - width / 2) < abs(best - width / 2):
            best = x
    return best


def compose(parts):
    """Parts (logical-size pixmaps) top to bottom, each with a thin frame."""
    scale = parts[0].devicePixelRatio()
    width = max(int(p.width() / scale) for p in parts) + 2
    height = sum(int(p.height() / scale) + 2 for p in parts) + GAP * (len(parts) - 1)
    out = QPixmap(int(width * scale), int(height * scale))
    out.setDevicePixelRatio(scale)
    out.fill(Qt.GlobalColor.transparent)           # only the framed parts show
    p = QPainter(out)
    y = 0
    for part in parts:
        w, h = int(part.width() / scale), int(part.height() / scale)
        p.drawPixmap(1, y + 1, part)
        p.setPen(QPen(QColor("#c4c4c4"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(0.5, y + 0.5, w + 1, h + 1))
        y += h + 2 + GAP
    p.end()
    return out


def render(tab, panel, special):
    win = panel.window()
    H.settle(0.4)
    full = QRect(QPoint(0, 0), win.size())
    rects = [(n, resolve(panel, win, item.target, special))
             for n, item in enumerate(tab.items, 1)]
    parts, placed = [], set()
    for group in tab.crops:
        content = QRect()
        for target in group:
            r = resolve(panel, win, target, special)
            content = r if content.isNull() else content.united(r)
        if content.left() < EDGE:                  # labels at the window's edge stay
            content.setLeft(0)
        content = content.intersected(full)
        inside = [(n, r) for n, r in rects if content.contains(r.center())]
        placed |= {n for n, _r in inside}
        shot = win.grab(content)                   # only the part; white around it
        scale = shot.devicePixelRatio()
        room = content.adjusted(-3 * BADGE, -3 * BADGE, 3 * BADGE, 3 * BADGE)
        canvas = white_pixmap(room.size(), scale)
        p = QPainter(canvas)
        p.drawPixmap(content.topLeft() - room.topLeft(), shot)
        p.end()
        badges = place_badges(pixels(canvas), room.topLeft(), scale, inside)
        region = content.adjusted(-PAD, -PAD, PAD, PAD)
        for x, y in badges.values():               # room for every badge
            region = region.united(QRect(int(x - BADGE / 2) - 3, int(y - BADGE / 2) - 3,
                                         BADGE + 6, BADGE + 6))
        pix = white_pixmap(region.size(), scale)
        p = QPainter(pix)
        p.drawPixmap(content.topLeft() - region.topLeft(), shot)
        p.end()
        draw(pix, region, inside, badges)
        local = [(n, r.translated(-region.left(), -region.top())) for n, r in inside]
        if region.width() > MAX_WIDTH:
            x = split_point(region.width(), local)
            if x is not None:
                scale = pix.devicePixelRatio()
                left = pix.copy(0, 0, int(x * scale), pix.height())
                right = pix.copy(int(x * scale), 0, pix.width() - int(x * scale), pix.height())
                left.setDevicePixelRatio(scale)
                right.setDevicePixelRatio(scale)
                parts += [left, right]
                continue
        parts.append(pix)
    missing = [n for n, _r in rects if n not in placed]
    if missing:
        raise ValueError(f"{tab.key}: items {missing} are outside the picture")
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{tab.key}.png")
    compose(parts).save(path)
    SAVED.append(path)
    print("saved", os.path.relpath(path, ROOT), f"({len(rects)} badges)")


# ----------------------------------------------------------------------------
# Attribute Explorer
# ----------------------------------------------------------------------------

def logical(panel, name):
    return panel.layer.fields().indexOf(name)


def explorer_special(panel, win, name):
    head, _sep, arg = name.partition(":")
    h = panel.header
    if head == "card":
        return widget_rect(win, h.cards[logical(panel, arg)])
    if head == "cell":
        return widget_rect(win, h.cells[logical(panel, arg)])
    if head == "op":
        return widget_rect(win, h.cells[logical(panel, arg)].op_btn)
    if head == "clear":
        edit = h.cells[logical(panel, arg)].edit
        r = widget_rect(win, edit)
        return QRect(r.right() - 20, r.top(), 20, r.height())
    if head == "name":
        lg = logical(panel, arg)
        r = QRect(h.sectionViewportPosition(lg), h.name_top(), h.sectionSize(lg), h.name_h)
        return viewport_rect(win, h.viewport(), r)
    if head == "frozen":
        return widget_rect(win, panel.frozen.header)
    view, model = panel.view, panel.model
    first = [lg for lg in range(model.columnCount()) if not h.isSectionHidden(lg)]
    if head == "tick":
        row = int(arg)
        vh = view.verticalHeader()
        r = QRect(0, vh.sectionViewportPosition(row), vh.width(), vh.sectionSize(row))
        return viewport_rect(win, vh.viewport(), r)
    if head == "selected":
        row = int(arg)
        r = view.visualRect(model.index(row, first[0])).united(
            view.visualRect(model.index(row, first[2])))
        return viewport_rect(win, view.viewport(), r)
    if head == "frame":
        row, col = (int(v) for v in arg.split(","))
        return viewport_rect(win, view.viewport(),
                             view.visualRect(model.index(row, first[col])))
    if head == "rows":                   # the row ticks and the first columns
        a, b = (int(v) for v in arg.split("-"))
        top = view.visualRect(model.index(a, first[0]))
        bottom = view.visualRect(model.index(b, first[min(3, len(first) - 1)]))
        r = viewport_rect(win, view.viewport(), top.united(bottom))
        left = widget_rect(win, view.verticalHeader()).left()
        return QRect(left, r.top(), r.right() - left + 1, r.height())
    if head == "dirty":
        fid, cols = next(iter(panel.model.dirty.items()))
        row = panel.model.row_of(fid)
        return viewport_rect(win, view.viewport(),
                             view.visualRect(model.index(row, sorted(cols)[0])))
    raise ValueError(f"unknown target @{name}")


def explorer_pictures(plugin, layer):
    panel = plugin.open_explorer(layer)
    host = plugin.explorers[layer.id()]
    win = host._window
    win.resize(1440, 860)
    H.wait_for(lambda: idle(panel), 60)
    lu = logical(panel, "LAND_USE")
    panel.header.cells[lu].set_filter(
        FL.ColumnFilter("LAND_USE", F.TEXT, "in", values=["Residential"]))
    panel.refresh()
    H.wait_for(lambda: idle(panel), 60)
    tabs = {t.key: t for t in PAGES["explorer"]}

    render(tabs["explorer_overview"], panel, explorer_special)

    # statistics: a few columns side by side, one frozen
    for name in ("fid", "OWNER_TYPE", "LAND_VALUE", "HERITAGE", "STOREYS", "YEAR_BUILT"):
        if logical(panel, name) >= 0:
            panel._hide_column(logical(panel, name))
    panel.frozen.freeze(logical(panel, "PARCEL_ID"))
    H.wait_for(lambda: idle(panel), 60)
    H.settle(0.5)
    render(tabs["explorer_statistics"], panel, explorer_special)

    # selection: three rows selected, a framed cell
    panel._select(list(panel.model.fids[1:4]), SELECT_SET)
    H.settle(0.3)
    first = [lg for lg in range(panel.model.columnCount())
             if not panel.header.isSectionHidden(lg)]
    panel.view.setCurrentIndex(panel.model.index(6, first[2]))
    panel.view.scrollToTop()
    render(tabs["explorer_selection"], panel, explorer_special)

    # editing: two changed cells, Quick update closed
    panel.layer.removeSelection()
    panel.edit_bar.edit_btn.click()
    H.wait_for(lambda: panel.edit.editing, 10)
    if panel.edit_bar.quick_btn.isChecked():
        panel.edit_bar.quick_btn.click()
    area = logical(panel, "AREA_M2")
    for row in (1, 3):
        fid = panel.model.fid(row)
        panel.layer.changeAttributeValue(fid, area, 1234.5)
    H.wait_for(lambda: idle(panel), 60)
    H.settle(0.6)
    panel.view.scrollToTop()
    render(tabs["explorer_editing"], panel, explorer_special)

    # Quick update and Find (a narrower window: the row is shorter)
    win.resize(1000, 860)
    panel.edit_bar.quick_btn.click()
    q = panel.quick_bar
    q.field_combo.setField("STOREYS")
    q.expression.setExpression('"STOREYS" + 1')
    panel._update_quick_state()
    panel.find_btn.click()
    H.settle(0.3)
    fb = panel.find_bar
    fb.find_box.edit.setText("Resid")
    if not fb.replace_row.isVisible():
        fb.expand_btn.click()
    H.settle(0.6)
    render(tabs["explorer_quick"], panel, explorer_special)
    panel.find_btn.click()
    panel.layer.rollBack()
    H.settle(0.3)
    return panel


def idle(panel):
    return (panel._has_result and not panel.runner.busy
            and not panel._data_timer.isActive() and not panel._refresh_timer.isActive())


# ----------------------------------------------------------------------------
# Visualize Statistics and the Pivot Table chart
# ----------------------------------------------------------------------------

def chart_special(panel, win, name):
    view = panel.view
    if name == "bar":
        bars = [(r, i) for r, i, s in view._geo["bars"] if s is None]
        r, _i = max(bars, key=lambda b: b[0].height() * b[0].width())
        return QRect(view.mapTo(win, r.toRect().topLeft()), r.toRect().size())
    if name == "legend":
        box = view._geo["legend_box"].toRect()
        return QRect(view.mapTo(win, box.topLeft()), box.size())
    if name == "settings":                   # the settings card, down to its last row
        card = widget_rect(win, panel.bins_row.parentWidget())
        last = widget_rect(win, panel.grid_tick)
        return QRect(card.left(), card.top(), card.width(), last.bottom() + 10 - card.top())
    raise ValueError(f"unknown target @{name}")


def chart_pictures(explorer):
    chart = explorer.visualize_statistics(logical(explorer, "AREA_M2"))
    win = explorer.chart_host._window
    win.resize(980, 720)
    H.wait_for(lambda: chart.col is not None and not chart.reader.busy, 60)
    chart.bins_spin.setValue(24)
    chart.std_tick.setChecked(True)
    H.settle(0.6)
    explorer.layer.selectByIds(chart.col.fids[:600].tolist()
                               if hasattr(chart.col.fids, "tolist") else list(chart.col.fids[:600]))
    H.settle(0.6)
    tabs = {t.key: t for t in PAGES["chart"]}
    render(tabs["chart_chart"], chart, chart_special)
    explorer.layer.removeSelection()
    chart.split_combo.set_current_name("OWNER_TYPE")
    H.wait_for(lambda: chart.col is not None and chart.col.split_name == "OWNER_TYPE"
               and not chart.reader.busy, 60)
    win.resize(1180, 1060)
    H.settle(0.6)
    render(tabs["chart_settings"], chart, chart_special)
    explorer.chart_host.close()


# ----------------------------------------------------------------------------
# Pivot Table
# ----------------------------------------------------------------------------

def pivot_special(panel, win, name):
    head, _sep, arg = name.partition(":")
    table = panel.table
    if head == "builder":
        w = panel.add_row_btn
        while w is not None and not (isinstance(w, QFrame) and w.objectName() == "gtsCard"):
            w = w.parentWidget()
        card = widget_rect(win, w)
        last = widget_rect(win, panel.show_total_cb)
        return QRect(card.left(), card.top(), card.width(), last.bottom() + 12 - card.top())
    if head == "heading":
        hh = table.horizontalHeader()
        c = int(arg)
        r = QRect(hh.sectionViewportPosition(c), 0, hh.sectionSize(c), hh.height())
        return viewport_rect(win, hh.viewport(), r)
    if head == "result":
        row, col = (int(v) for v in arg.split(","))
        return viewport_rect(win, table.viewport(),
                             table.visualRect(panel.model.index(row, col)))
    if head == "total":
        row = panel.model.rowCount() - 1
        r = table.visualRect(panel.model.index(row, 0)).united(
            table.visualRect(panel.model.index(row, panel.model.columnCount() - 1)))
        return viewport_rect(win, table.viewport(), r)
    if head == "result_area":
        last = panel.model.rowCount() - 1
        r = table.visualRect(panel.model.index(last, panel.model.columnCount() - 1))
        area = QRect(QPoint(0, 0), r.bottomRight())
        return viewport_rect(win, table.viewport(), area).united(
            widget_rect(win, table.horizontalHeader()))
    raise ValueError(f"unknown target @{name}")


def pivot_pictures(plugin, layer):
    pivot = plugin.open_pivot(layer)
    win = plugin.pivot_host._window
    win.resize(1440, 780)
    pivot._capture("all")
    pivot.set_spec(P.PivotSpec(["LAND_USE"], ["OWNER_TYPE"], [
        P.Measure("LAND_VALUE", "mean", ""), P.Measure(None, "count")]),
        [FL.ColumnFilter("HAS_SOLAR", F.BOOL, "true")])
    pivot.build()
    H.wait_for(lambda: pivot.model.result is not None and not pivot.runner.busy, 60)
    H.settle(0.5)
    tabs = {t.key: t for t in PAGES["pivot"]}
    render(tabs["pivot_overview"], pivot, pivot_special)
    win.resize(1440, 900)
    H.settle(0.4)
    render(tabs["pivot_build"], pivot, pivot_special)
    # the result: one value, no record column, so it fits
    pivot.set_spec(P.PivotSpec(["LAND_USE"], ["HAS_SOLAR"], [
        P.Measure("LAND_VALUE", "mean", "")], show_count=True))
    pivot.filters = []
    pivot._refresh_filters()
    pivot.build()
    H.wait_for(lambda: not pivot.runner.busy, 60)
    H.settle(0.5)
    render(tabs["pivot_result"], pivot, pivot_special)
    # the chart
    pivot.set_spec(P.PivotSpec(["LAND_USE"], ["OWNER_TYPE"], [P.Measure("AREA_M2", "sum")]))
    pivot.build()
    H.wait_for(lambda: not pivot.runner.busy, 60)
    chart = pivot.open_chart()
    cwin = pivot.chart_host._window
    cwin.resize(980, 720)
    chart.sort_combo.setCurrentIndex(chart.sort_combo.findData("count"))
    H.settle(0.5)
    chart.view.barsPicked.emit([1], False, None)
    H.settle(0.6)
    render(tabs["pivot_chart"], chart, chart_special)
    pivot.chart_host.close()
    layer.removeSelection()
    plugin.pivot_host.close()


# ----------------------------------------------------------------------------
# Data Plot
# ----------------------------------------------------------------------------

def plot_special(panel, win, name):
    head, _sep, arg = name.partition(":")
    view, props = panel.view, panel.props
    geo = view._geo

    def on_view(r):
        r = r.toRect() if hasattr(r, "toRect") else r
        return QRect(view.mapTo(win, r.topLeft()), r.size())

    def shown(widgets):
        out = []
        for w in widgets:
            try:
                if w.isVisibleTo(win):
                    out.append(w)
            except RuntimeError:                 # a row of an earlier card
                continue
        return out
    if head in ("row", "tick", "button"):
        text, _h, nth = arg.partition("#")
        nth = int(nth or 1)
        if head == "row":
            found = [lbl.parentWidget() for lbl in shown(props._labels) if lbl.text() == text]
        elif head == "tick":
            found = [w for w in shown(props.widget().findChildren(QCheckBox))
                     if w.text() == text]
        else:
            found = [w for w in shown(props.widget().findChildren(QAbstractButton))
                     if not isinstance(w, QCheckBox)
                     and w.text().replace("&", "").strip() == text]
        if len(found) < nth:
            raise ValueError(f"no {head} {arg!r}")
        return widget_rect(win, found[nth - 1])
    if head == "item":                       # the largest; of equal ones the middle
        items = [r for r, _i, _s in geo["items"]]
        if not items:
            raise ValueError("no plot items")
        big = max(r.width() * r.height() for r in items)
        same = [r for r in items if r.width() * r.height() >= 0.99 * big]
        return on_view(same[len(same) // 2])
    if head == "wedge":                      # a spot inside the largest slice
        path = max(geo["wedges"], key=lambda w: abs(w[3]))[0]
        box = path.boundingRect()
        inside = [QPointF(box.left() + box.width() * i / 10, box.top() + box.height() * j / 10)
                  for i in range(1, 10) for j in range(1, 10)]
        inside = [pt for pt in inside if path.contains(pt)]
        cx = sum(pt.x() for pt in inside) / len(inside)
        cy = sum(pt.y() for pt in inside) / len(inside)
        pt = min(inside, key=lambda q: (q.x() - cx) ** 2 + (q.y() - cy) ** 2)
        return on_view(QRectF(pt.x() - 8, pt.y() - 8, 16, 16))
    if head == "legend":
        return on_view(geo["legend_box"])
    if head == "plot":
        return on_view(geo["plot"])
    if head == "cell":
        cell = next(c for c in geo["cells"] if c[3] == arg)
        return on_view(cell[0])
    raise ValueError(f"unknown target @{name}")


def plot_pictures(plugin, layer):
    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSymbol
    cats = []                                # the points in the layer's colours
    for value, colour in (("Residential", "#4c9be8"), ("Commercial", "#e8833a"),
                          ("Industrial", "#7b5ea7"), ("Rural", "#3aa76d"),
                          ("Open space", "#9aa64b"), ("Community", "#d45d8a")):
        sym = QgsSymbol.defaultSymbol(layer.geometryType())
        sym.setColor(QColor(colour))
        cats.append(QgsRendererCategory(value, sym, value))
    renderer = layer.renderer().clone()
    layer.setRenderer(QgsCategorizedSymbolRenderer("LAND_USE", cats))
    panel = plugin.open_data_plot(layer)
    win = panel.window()

    def ready():
        return (panel.data is not None and not panel.reader.busy and not panel.builder.busy
                and not panel.selector.busy)

    def tidy(height):
        """The window *height* or a little less, so that the settings card
        ends between two rows instead of cutting one."""
        win.resize(win.width(), height)
        H.settle(0.3)
        panel.props.verticalScrollBar().setValue(0)
        room = panel.props.viewport().height()
        lay = panel.props.widget().layout()
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None and w.isVisible() and w.y() < room < w.y() + w.height():
                win.resize(win.width(), height - (room - w.y()) + 2)
                H.settle(0.3)
                break

    def make(kind, name, title=None, **data):
        panel.new_plot(kind)
        plot = panel.current_plot()
        plot["name"] = name
        if title:
            plot.setdefault("general", {})["title"] = title
        plot["data"].update(data)
        panel._write(plot)
        panel._fill_list(plot["id"])
        panel._show_plot(plot["id"])
        H.wait_for(ready, 60)
        H.settle(0.5)
        if not panel.list_card.isVisible():
            tidy(860)

    tabs = {t.key: t for t in PAGES["plot"]}
    win.resize(1000, 700)
    make("scatter", "Area and land value", x="AREA_M2", y="LAND_VALUE")
    make("line", "Mean land value per month", x="SURVEY_DATE", fields=["LAND_VALUE"],
         agg="mean", unit="months")
    make("bar", "Area by land use", title="Area by land use", x="LAND_USE",
         fields=["AREA_M2"], agg="sum", split="OWNER_TYPE", multi="stacked",
         sort="value_desc")
    render(tabs["plot_overview"], panel, plot_special)

    panel.plots_btn.click()                  # the type pictures: the plot and its card
    tidy(860)
    render(tabs["plot_bar"], panel, plot_special)
    make("line", "Mean land value per month", x="SURVEY_DATE", fields=["LAND_VALUE"],
         agg="mean", unit="months")
    render(tabs["plot_line"], panel, plot_special)
    make("combo", "Records and land value per quarter", x="SURVEY_DATE", unit="months",
         interval=3, series=[
             {"field": None, "agg": "count", "kind": "bar", "axis": "left"},
             {"field": "LAND_VALUE", "agg": "mean", "kind": "line", "axis": "right"}])
    render(tabs["plot_combo"], panel, plot_special)
    make("pie", "Parcels by land use", category="LAND_USE", donut=35)
    render(tabs["plot_pie"], panel, plot_special)
    make("histogram", "Parcel areas", x="AREA_M2", transform="log", std=True, normal=True)
    render(tabs["plot_histogram"], panel, plot_special)
    make("box", "Areas by land use", split="LAND_USE", fields=["AREA_M2"])
    render(tabs["plot_box"], panel, plot_special)
    make("scatter", "Area and land value", x="AREA_M2", y="LAND_VALUE",
         trends={"LAND_VALUE": {"on": True, "type": "linear", "forward": 10000}})
    render(tabs["plot_scatter"], panel, plot_special)
    make("matrix", "Number fields", fields=["AREA_M2", "LAND_VALUE", "STOREYS", "YEAR_BUILT"],
         upper="preview")
    render(tabs["plot_matrix"], panel, plot_special)
    make("qq", "Land value against the normal distribution", x="LAND_VALUE")
    render(tabs["plot_qq"], panel, plot_special)
    make("heat", "Land use and owner type", x="LAND_USE", y="OWNER_TYPE", field=None)
    render(tabs["plot_heat"], panel, plot_special)
    make("calendar", "Surveys per day", x="SURVEY_DATE", field=None)
    render(tabs["plot_calendar"], panel, plot_special)
    make("clock", "Surveys per month", x="SURVEY_DATE", field=None)
    render(tabs["plot_clock"], panel, plot_special)
    panel.plots_btn.click()
    layer.setRenderer(renderer)
    for host in list(plugin.plot_hosts):
        host.close()
    H.settle(0.3)


# ----------------------------------------------------------------------------
# Geometry and Table Compare
# ----------------------------------------------------------------------------

def compare_special(panel, win, name):
    head, _sep, arg = name.partition(":")
    tbl = panel.results_table
    if head == "filter":
        r = widget_rect(win, panel.filter_buttons[arg])
        for w in (panel.filter_boxes[arg], panel.filter_counts[arg]):
            r = r.united(widget_rect(win, w))
        return r
    if head == "heading":
        hh = tbl.horizontalHeader()
        col = next(c for c in range(tbl.columnCount())
                   if tbl.horizontalHeaderItem(c).text() == arg)
        r = QRect(hh.sectionViewportPosition(col), 0, hh.sectionSize(col), hh.height())
        return viewport_rect(win, hh.viewport(), r)
    kind_role = Qt.ItemDataRole.UserRole + 1
    decision_role = Qt.ItemDataRole.UserRole + 2

    def on_screen(row):
        y = tbl.rowViewportPosition(row)
        return not tbl.isRowHidden(row) and 0 <= y and y + tbl.rowHeight(row) <= ROWS_SHOWN * \
            tbl.rowHeight(row)

    def first_cell(pred):
        for row in range(tbl.rowCount()):
            if not on_screen(row):
                continue
            for col in range(tbl.columnCount()):
                item = tbl.item(row, col)
                if item is not None and pred(item):
                    rect = tbl.visualItemRect(item)
                    if rect.right() < tbl.viewport().width():
                        return viewport_rect(win, tbl.viewport(), rect)
        raise ValueError(f"no cell for @{name}")
    if head == "changed":
        return first_cell(lambda it: it.data(kind_role) == "changed"
                          and it.data(decision_role) is None)
    if head == "accepted":
        return first_cell(lambda it: it.data(decision_role) == "Accepted")
    if head == "decision":
        return first_cell(lambda it: it.column() == 1 and "Accepted" in it.text())
    if head == "table_top":                  # the header and the first rows shown
        r = QRect(0, 0, tbl.viewport().width(), ROWS_SHOWN * tbl.verticalHeader()
                  .defaultSectionSize())
        return viewport_rect(win, tbl.viewport(), r).united(
            widget_rect(win, tbl.horizontalHeader()))
    raise ValueError(f"unknown target @{name}")


ROWS_SHOWN = 9                   # rows of the Compare table in its pictures


def first_modified_on_top(cp):
    tbl = cp.results_table
    for row in range(tbl.rowCount()):
        if not tbl.isRowHidden(row) and tbl.item(row, 0).text() == "Modified":
            tbl.scrollToItem(tbl.item(row, 0), tbl.ScrollHint.PositionAtTop)
            return row
    return None


def compare_pictures(plugin):
    base = sample_parcels(n=2000, seed=7, name="Sample parcels (baseline)")
    new = sample_update(base)
    cp = plugin.open_compare()
    win = plugin.compare_host._window
    win.resize(1440, 860)
    cp.old_table_combo.setLayer(base)
    cp.new_table_combo.setLayer(new)
    cp.join_field_combo.setCurrentIndex(cp.join_field_combo.findText("PARCEL_ID"))
    cp.geom_compare_cb.setChecked(True)
    cp.validate_data()
    H.settle(0.6)
    tabs = {t.key: t for t in PAGES["compare"]}
    render(tabs["compare_setup"], cp, compare_special)
    cp.compare_tables()
    H.settle(1.2)
    cp.filter_buttons["Unchanged"].setChecked(False)
    H.settle(0.4)
    first_modified_on_top(cp)
    H.settle(0.3)
    render(tabs["compare_results"], cp, compare_special)
    tbl = cp.results_table
    rows = [r for r in range(tbl.rowCount()) if not tbl.isRowHidden(r)
            and tbl.item(r, 0).text() == "Modified"]
    tbl.clearSelection()
    values = cp._row_values(rows[0])
    for item in values:
        item.setSelected(True)
    cp._decide_values("Accepted")
    tbl.clearSelection()
    if len(rows) > 2:
        other = cp._row_values(rows[2])
        if other:
            other[0].setSelected(True)
            cp._decide_values("Rejected")
    tbl.clearSelection()
    tbl.selectRow(rows[1])
    first_modified_on_top(cp)
    H.settle(0.5)
    render(tabs["compare_review"], cp, compare_special)
    plugin.compare_host.close()


# ----------------------------------------------------------------------------

SAVED = []


def shrink(paths):
    """256 colours, about four times smaller (scripts/shrink_png.py, with a
    Python that has Pillow: QGIS's own usually has not)."""
    script = os.path.join(ROOT, "scripts", "shrink_png.py")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    for python in ("python3", "python"):
        try:
            subprocess.run([python, script, *paths], check=True, env=env)
            return
        except (OSError, subprocess.CalledProcessError):
            continue
    print("the pictures keep all their colours: no Python with Pillow found")


def main():
    iface = H.FakeIface()
    layer = sample_parcels(n=3000, seed=42)
    iface.setActiveLayer(layer)
    plugin = classFactory(iface)
    plugin.initGui()
    explorer = explorer_pictures(plugin, layer)
    chart_pictures(explorer)
    pivot_pictures(plugin, layer)
    plot_pictures(plugin, layer)
    compare_pictures(plugin)
    plugin.unload()
    shrink(SAVED)


main()
H.shutdown(APP)
