# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Edit plot: the look of what is drawn inside a plot's canvas (the area
inside the axes) and the objects added to it.

A plot keeps both in ``plot["canvas"]``:

* ``style``: one entry per part of the canvas ("background", "grid_h",
  "grid_v", "series:<name>", "slices", "line:<what>", "guide:<n>"), each a
  dict of the settings changed (colour, width, dash, ...); a missing setting
  keeps the plot's own look.
* ``objects``: text boxes, horizontal and vertical lines, arrows and
  pictures, placed as shares (0 to 1) of the canvas so they keep their place
  when the window or the axes change.

Settings the plot's card already has (titles, axes, legend, series colours,
point shapes, grid lines on or off, guides' values) are never set here.
"""

import base64
import copy
import math
import uuid

from qgis.PyQt.QtCore import QByteArray, QBuffer, QPointF, QRectF, Qt
from qgis.PyQt.QtGui import (
    QColor, QFont, QFontMetricsF, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap,
    QPolygonF, QTextOption,
)

DASHES = (("solid", "Solid"), ("dash", "Dashed"), ("dot", "Dotted"),
          ("dashdot", "Dash and dot"))
PEN_STYLES = {"solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine,
              "dot": Qt.PenStyle.DotLine, "dashdot": Qt.PenStyle.DashDotLine}
ADD_KINDS = (("text", "Text box"), ("hline", "Horizontal line"),
             ("vline", "Vertical line"), ("arrow", "Arrow"), ("picture", "Picture…"))
KIND_LABELS = {"text": "Text box", "hline": "Horizontal line", "vline": "Vertical line",
               "arrow": "Arrow", "picture": "Picture"}
HEADS = (("end", "At the end"), ("start", "At the start"), ("both", "At both ends"),
         ("none", "None"))
ALIGNS = (("left", "Left"), ("center", "Centre"), ("right", "Right"))
PICTURE_SIDE = 1600              # a picture's longest side kept in the plot (px)
HANDLE = 7                       # a selection handle's side (px)


# ============================================================================
# THE CANVAS OF A PLOT
# ============================================================================

def canvas_of(plot):
    """The plot's canvas settings, made when missing."""
    canvas = plot.setdefault("canvas", {})
    canvas.setdefault("style", {})
    canvas.setdefault("objects", [])
    return canvas


def style_of(plot, key):
    """The settings changed for one part of the canvas ({} when none)."""
    canvas = plot.get("canvas") if plot else None
    if not isinstance(canvas, dict):
        return {}
    return (canvas.get("style") or {}).get(key) or {}


def objects_of(plot):
    canvas = plot.get("canvas") if plot else None
    if not isinstance(canvas, dict):
        return []
    return [o for o in canvas.get("objects") or [] if isinstance(o, dict)]


def with_alpha(colour, alpha):
    """*colour* at *alpha* percent opacity (None: as it is)."""
    c = QColor(colour)
    if alpha is not None:
        c.setAlphaF(max(0.0, min(1.0, float(alpha) / 100.0)) * c.alphaF())
    return c


def styled_pen(style, colour, width, dash="solid", cap=Qt.PenCapStyle.SquareCap):
    """A pen of the plot's own *colour*, *width* and *dash*, with what the
    part's *style* changes."""
    c = QColor(style.get("colour") or colour)
    if style.get("alpha") is not None:
        c = with_alpha(c, style["alpha"])
    pen = QPen(c, float(style.get("width", width)))
    pen.setStyle(PEN_STYLES.get(style.get("dash", dash), Qt.PenStyle.SolidLine))
    pen.setCapStyle(cap)
    return pen


def dash_icon(dash, colour, size=16):
    """An icon of a line dash (the Dash boxes)."""
    icon = QIcon()
    for scale in (1, 2):
        img = QImage(size * 2 * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(scale)
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(colour), 2)
        pen.setStyle(PEN_STYLES.get(dash, Qt.PenStyle.SolidLine))
        q.setPen(pen)
        q.drawLine(QPointF(2, size / 2.0), QPointF(size * 2 - 2, size / 2.0))
        q.end()
        icon.addPixmap(QPixmap.fromImage(img))
    return icon


def kind_icon(kind, colour, size=16):
    """An icon of an object kind (the Add menu)."""
    icon = QIcon()
    for scale in (1, 2):
        img = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(scale)
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(colour)
        q.setPen(QPen(c, 1.6))
        s = float(size)
        if kind == "text":
            q.setBrush(Qt.BrushStyle.NoBrush)
            q.drawRoundedRect(QRectF(1.5, 3.5, s - 3, s - 7), 2, 2)
            f = QFont()
            f.setBold(True)
            f.setPixelSize(int(s * 0.55))
            q.setFont(f)
            q.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "T")
        elif kind == "hline":
            q.drawLine(QPointF(1, s / 2), QPointF(s - 1, s / 2))
            q.setPen(QPen(c, 1))
            q.drawLine(QPointF(1, s * 0.25), QPointF(1, s * 0.75))
            q.drawLine(QPointF(s - 1, s * 0.25), QPointF(s - 1, s * 0.75))
        elif kind == "vline":
            q.drawLine(QPointF(s / 2, 1), QPointF(s / 2, s - 1))
            q.setPen(QPen(c, 1))
            q.drawLine(QPointF(s * 0.25, 1), QPointF(s * 0.75, 1))
            q.drawLine(QPointF(s * 0.25, s - 1), QPointF(s * 0.75, s - 1))
        elif kind == "arrow":
            paint_arrow(q, QPointF(2, s - 2), QPointF(s - 2, 2), c, 1.6, "solid", "end", 6)
        elif kind == "picture":
            q.setBrush(Qt.BrushStyle.NoBrush)
            q.drawRect(QRectF(1.5, 2.5, s - 3, s - 5))
            path = QPainterPath()
            path.moveTo(3, s - 4)
            path.lineTo(s * 0.4, s * 0.45)
            path.lineTo(s * 0.62, s * 0.7)
            path.lineTo(s * 0.75, s * 0.58)
            path.lineTo(s - 3, s - 4)
            q.drawPath(path)
            q.drawEllipse(QPointF(s * 0.7, s * 0.32), 1.6, 1.6)
        q.end()
        icon.addPixmap(QPixmap.fromImage(img))
    return icon


# ============================================================================
# OBJECTS
# ============================================================================

def new_object(kind, theme, x=0.5, y=0.5, x2=None, y2=None, picture=None):
    """A new object at (x, y) (shares of the canvas)."""
    strong = QColor(theme.text_strong).name()
    obj = {"id": uuid.uuid4().hex[:10], "kind": kind}
    if kind == "text":
        obj.update(x=x, y=y, w=0.3, text="Text", font="", size=10.0, colour=strong,
                   bold=False, italic=False, underline=False, align="left",
                   fill=QColor(theme.base).name(), fill_alpha=85, border=QColor(theme.border).name(),
                   border_width=1.0, border_dash="solid", radius=4.0, padding=6.0)
    elif kind == "hline":
        obj.update(y=y, colour=strong, width=1.6, dash="solid", alpha=100)
    elif kind == "vline":
        obj.update(x=x, colour=strong, width=1.6, dash="solid", alpha=100)
    elif kind == "arrow":
        if x2 is None or y2 is None or (abs(x2 - x) < 0.01 and abs(y2 - y) < 0.01):
            x2, y2 = min(1.0, x + 0.15), max(0.0, y - 0.1)
        obj.update(x1=x, y1=y, x2=x2, y2=y2, colour=strong, width=2.0, dash="solid",
                   head="end", head_size=10.0, alpha=100)
    elif kind == "picture":
        obj.update(x=x, y=y, w=0.25, alpha=100, border="", border_width=1.0)
        if picture is not None:
            obj.update(picture)
    clamp(obj)
    return obj


def clamp(obj):
    """Keep an object inside the canvas (shares between 0 and 1)."""
    for key in ("x", "y", "x1", "y1", "x2", "y2"):
        if key in obj:
            obj[key] = max(0.0, min(1.0, float(obj[key])))
    if "w" in obj:
        obj["w"] = max(0.03, min(1.0, float(obj["w"])))
        if "x" in obj:
            obj["x"] = min(obj["x"], 1.0 - obj["w"])


def picture_data(path):
    """A picture file made ready to be kept in the plot: (dict, error).  A
    big picture is made smaller (its longest side PICTURE_SIDE px)."""
    img = QImage(path)
    if img.isNull():
        return None, "The file is not a picture QGIS can read"
    if max(img.width(), img.height()) > PICTURE_SIDE:
        img = img.scaled(PICTURE_SIDE, PICTURE_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return {"data": base64.b64encode(bytes(data)).decode("ascii"),
            "ratio": img.height() / max(1, img.width())}, ""


_IMAGES = {}


def picture_image(obj):
    """The QImage of a picture object (decoded once)."""
    data = obj.get("data") or ""
    key = (obj.get("id"), len(data), data[:64])
    img = _IMAGES.get(key)
    if img is None:
        img = QImage()
        try:
            img.loadFromData(base64.b64decode(data))
        except (ValueError, TypeError):
            pass
        if len(_IMAGES) > 64:
            _IMAGES.clear()
        _IMAGES[key] = img
    return img


def _text_font(obj, base):
    font = QFont(base)
    if obj.get("font"):
        font.setFamily(obj["font"])
    font.setPointSizeF(max(4.0, float(obj.get("size", 10))))
    font.setBold(bool(obj.get("bold")))
    font.setItalic(bool(obj.get("italic")))
    font.setUnderline(bool(obj.get("underline")))
    return font


def object_rect(obj, plot, base_font):
    """The box an object takes in the canvas *plot* (px)."""
    k = obj.get("kind")
    if k == "text":
        w = obj.get("w", 0.3) * plot.width()
        pad = float(obj.get("padding", 6))
        fm = QFontMetricsF(_text_font(obj, base_font))
        inner = max(10.0, w - 2 * pad)
        text = obj.get("text") or " "
        bound = fm.boundingRect(QRectF(0, 0, inner, 1e6),
                                int(Qt.TextFlag.TextWordWrap), text)
        h = bound.height() + 2 * pad
        x = plot.left() + obj.get("x", 0) * plot.width()
        y = plot.top() + obj.get("y", 0) * plot.height()
        y = min(y, plot.bottom() - h)
        return QRectF(x, max(plot.top(), y), w, h)
    if k == "hline":
        y = plot.top() + obj.get("y", 0.5) * plot.height()
        return QRectF(plot.left(), y - 4, plot.width(), 8)
    if k == "vline":
        x = plot.left() + obj.get("x", 0.5) * plot.width()
        return QRectF(x - 4, plot.top(), 8, plot.height())
    if k == "arrow":
        a, b = arrow_points(obj, plot)
        return QRectF(a, b).normalized().adjusted(-6, -6, 6, 6)
    if k == "picture":
        w = obj.get("w", 0.25) * plot.width()
        h = w * float(obj.get("ratio", 1.0) or 1.0)
        h = min(h, plot.height())
        w = h / float(obj.get("ratio", 1.0) or 1.0)
        x = plot.left() + obj.get("x", 0) * plot.width()
        y = plot.top() + obj.get("y", 0) * plot.height()
        y = min(y, plot.bottom() - h)
        return QRectF(x, max(plot.top(), y), w, h)
    return QRectF()


def arrow_points(obj, plot):
    a = QPointF(plot.left() + obj.get("x1", 0.4) * plot.width(),
                plot.top() + obj.get("y1", 0.6) * plot.height())
    b = QPointF(plot.left() + obj.get("x2", 0.6) * plot.width(),
                plot.top() + obj.get("y2", 0.4) * plot.height())
    return a, b


def paint_arrow(p, a, b, colour, width, dash, head, head_size):
    pen = QPen(QColor(colour), width)
    pen.setStyle(PEN_STYLES.get(dash, Qt.PenStyle.SolidLine))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(a, b)
    angle = math.atan2(b.y() - a.y(), b.x() - a.x())
    size = max(4.0, float(head_size))

    def tip(at, ang):
        left = QPointF(at.x() - size * math.cos(ang - 0.42), at.y() - size * math.sin(ang - 0.42))
        right = QPointF(at.x() - size * math.cos(ang + 0.42), at.y() - size * math.sin(ang + 0.42))
        solid = QPen(QColor(colour), 1)
        solid.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(solid)
        p.setBrush(QColor(colour))
        p.drawPolygon(QPolygonF([at, left, right]))

    if head in ("end", "both"):
        tip(b, angle)
    if head in ("start", "both"):
        tip(a, angle + math.pi)


def paint_objects(p, plot, objects, base_font):
    """Draw the objects in the canvas *plot*; returns [(id, rect)] for
    picking, in drawing order."""
    placed = []
    p.save()
    p.setClipRect(plot.adjusted(-1, -1, 1, 1))
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for obj in objects:
        k = obj.get("kind")
        rect = object_rect(obj, plot, base_font)
        if k == "text":
            radius = float(obj.get("radius", 4))
            fill = obj.get("fill")
            p.setBrush(with_alpha(fill, obj.get("fill_alpha", 100)) if fill
                       else Qt.BrushStyle.NoBrush)
            if obj.get("border") and float(obj.get("border_width", 1)) > 0:
                pen = QPen(QColor(obj["border"]), float(obj.get("border_width", 1)))
                pen.setStyle(PEN_STYLES.get(obj.get("border_dash", "solid"),
                                            Qt.PenStyle.SolidLine))
                p.setPen(pen)
            else:
                p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, radius, radius)
            pad = float(obj.get("padding", 6))
            p.setFont(_text_font(obj, base_font))
            p.setPen(QColor(obj.get("colour") or "#000000"))
            option = QTextOption({"left": Qt.AlignmentFlag.AlignLeft,
                                  "center": Qt.AlignmentFlag.AlignHCenter,
                                  "right": Qt.AlignmentFlag.AlignRight}.get(
                                      obj.get("align", "left"), Qt.AlignmentFlag.AlignLeft))
            option.setWrapMode(QTextOption.WrapMode.WordWrap)
            p.drawText(rect.adjusted(pad, pad, -pad, -pad), obj.get("text") or "", option)
            p.setFont(base_font)
        elif k in ("hline", "vline"):
            colour = with_alpha(obj.get("colour") or "#000000", obj.get("alpha", 100))
            pen = QPen(colour, float(obj.get("width", 1.6)))
            pen.setStyle(PEN_STYLES.get(obj.get("dash", "solid"), Qt.PenStyle.SolidLine))
            p.setPen(pen)
            c = rect.center()
            if k == "hline":
                p.drawLine(QPointF(plot.left(), c.y()), QPointF(plot.right(), c.y()))
            else:
                p.drawLine(QPointF(c.x(), plot.top()), QPointF(c.x(), plot.bottom()))
        elif k == "arrow":
            a, b = arrow_points(obj, plot)
            paint_arrow(p, a, b, with_alpha(obj.get("colour") or "#000000",
                                            obj.get("alpha", 100)),
                        float(obj.get("width", 2)), obj.get("dash", "solid"),
                        obj.get("head", "end"), obj.get("head_size", 10))
        elif k == "picture":
            img = picture_image(obj)
            p.setOpacity(max(0.0, min(1.0, float(obj.get("alpha", 100)) / 100.0)))
            if not img.isNull():
                p.drawImage(rect, img)
            p.setOpacity(1.0)
            if obj.get("border") and float(obj.get("border_width", 1)) > 0:
                p.setPen(QPen(QColor(obj["border"]), float(obj.get("border_width", 1))))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rect)
        placed.append((obj.get("id"), rect))
    p.restore()
    return placed


def handles(obj, plot, base_font):
    """The handles of a selected object: [(name, point)]."""
    k = obj.get("kind")
    rect = object_rect(obj, plot, base_font)
    if k == "text":
        return [("w", QPointF(rect.right(), rect.center().y()))]
    if k == "picture":
        return [("size", rect.bottomRight())]
    if k == "arrow":
        a, b = arrow_points(obj, plot)
        return [("start", a), ("end", b)]
    return []


def object_hit(obj, plot, base_font, pos):
    """Whether *pos* is on the object (a line within a few px)."""
    k = obj.get("kind")
    if k == "arrow":
        a, b = arrow_points(obj, plot)
        return _near_segment(pos, a, b) <= max(5.0, float(obj.get("width", 2)) + 3)
    return object_rect(obj, plot, base_font).contains(pos)


def _near_segment(p, a, b):
    dx, dy = b.x() - a.x(), b.y() - a.y()
    length = dx * dx + dy * dy
    if not length:
        return math.hypot(p.x() - a.x(), p.y() - a.y())
    t = max(0.0, min(1.0, ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / length))
    return math.hypot(p.x() - (a.x() + t * dx), p.y() - (a.y() + t * dy))


def move_object(obj, dx, dy):
    """Move an object by shares of the canvas, inside it."""
    k = obj.get("kind")
    if k == "arrow":
        dx = max(-min(obj["x1"], obj["x2"]), min(1 - max(obj["x1"], obj["x2"]), dx))
        dy = max(-min(obj["y1"], obj["y2"]), min(1 - max(obj["y1"], obj["y2"]), dy))
        obj["x1"] += dx
        obj["x2"] += dx
        obj["y1"] += dy
        obj["y2"] += dy
        return
    if "x" in obj:
        obj["x"] = obj["x"] + dx
    if "y" in obj:
        obj["y"] = obj["y"] + dy
    clamp(obj)


def duplicate(obj):
    twin = copy.deepcopy(obj)
    twin["id"] = uuid.uuid4().hex[:10]
    move_object(twin, 0.03, 0.03)
    return twin


def describe(part):
    """The heading of the card for a selected part of the canvas."""
    return part.get("label") or KIND_LABELS.get(part.get("kind"), "")
