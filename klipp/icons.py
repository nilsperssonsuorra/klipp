"""Icons drawn in code so the app needs no image assets."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

FG = QColor("#e8e8e8")


def _canvas(size=64):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p


def _pen(width=5, color=FG):
    return QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def _arrow_head(p, tip, angle, size):
    left = QPointF(tip.x() - size * math.cos(angle - 0.5), tip.y() - size * math.sin(angle - 0.5))
    right = QPointF(tip.x() - size * math.cos(angle + 0.5), tip.y() - size * math.sin(angle + 0.5))
    p.setBrush(p.pen().color())
    p.drawPolygon(QPolygonF([tip, left, right]))
    p.setBrush(Qt.NoBrush)


def tool_icon(kind):
    pm, p = _canvas()
    p.setPen(_pen())
    if kind == "pen":
        p.translate(32, 32)
        p.rotate(-45)
        p.drawRoundedRect(QRectF(-20, -7, 30, 14), 2, 2)
        p.drawPolygon(QPolygonF([QPointF(10, -7), QPointF(24, 0), QPointF(10, 7)]))
        p.setBrush(FG)
        p.drawEllipse(QPointF(22, 0), 2, 2)
    elif kind == "highlighter":
        path = QPainterPath(QPointF(10, 42))
        path.cubicTo(22, 20, 36, 50, 54, 24)
        p.setPen(QPen(QColor(255, 214, 10, 190), 16, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
    elif kind == "line":
        p.drawLine(QPointF(12, 52), QPointF(52, 12))
    elif kind == "arrow":
        p.drawLine(QPointF(12, 52), QPointF(48, 16))
        _arrow_head(p, QPointF(52, 12), -math.pi / 4, 18)
    elif kind == "rect":
        p.drawRoundedRect(QRectF(10, 16, 44, 32), 3, 3)
    elif kind == "ellipse":
        p.drawEllipse(QRectF(8, 14, 48, 36))
    elif kind == "eraser":
        p.translate(32, 32)
        p.rotate(-45)
        body = QRectF(-22, -11, 44, 22)
        p.setBrush(QColor("#ff6b9a"))
        p.drawRoundedRect(QRectF(-22, -11, 18, 22), 4, 4)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(body, 4, 4)
    elif kind in ("undo", "redo"):
        if kind == "redo":
            p.translate(64, 0)
            p.scale(-1, 1)
        path = QPainterPath(QPointF(16, 26))
        path.cubicTo(28, 14, 52, 16, 52, 36)
        path.cubicTo(52, 46, 44, 52, 34, 52)
        p.drawPath(path)
        _arrow_head(p, QPointF(12, 30), math.pi * 0.85, 16)
    elif kind == "copy":
        p.drawRoundedRect(QRectF(22, 20, 30, 36), 4, 4)
        p.drawPolyline(QPolygonF([QPointF(16, 44), QPointF(12, 44), QPointF(12, 8), QPointF(40, 8), QPointF(40, 14)]))
    elif kind == "save":
        p.drawRoundedRect(QRectF(10, 10, 44, 44), 5, 5)
        p.drawRect(QRectF(20, 10, 22, 14))
        p.drawRoundedRect(QRectF(18, 34, 28, 20), 2, 2)
    elif kind == "clear":
        p.drawLine(QPointF(12, 18), QPointF(52, 18))
        p.drawLine(QPointF(26, 18), QPointF(28, 10))
        p.drawLine(QPointF(28, 10), QPointF(36, 10))
        p.drawLine(QPointF(36, 10), QPointF(38, 18))
        p.drawPolyline(QPolygonF([QPointF(17, 18), QPointF(20, 54), QPointF(44, 54), QPointF(47, 18)]))
        p.drawLine(QPointF(28, 28), QPointF(29, 45))
        p.drawLine(QPointF(36, 28), QPointF(35, 45))
    p.end()
    return QIcon(pm)


def app_icon():
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm, p = _canvas(size)
        s = size / 64
        p.scale(s, s)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2f7bff"))
        p.drawRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
        p.setPen(_pen(6, QColor("#ffffff")))
        for x, y, dx, dy in ((14, 14, 1, 1), (50, 14, -1, 1), (14, 50, 1, -1), (50, 50, -1, -1)):
            p.drawPolyline(QPolygonF([QPointF(x, y + 12 * dy), QPointF(x, y), QPointF(x + 12 * dx, y)]))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffd60a"))
        p.drawEllipse(QPointF(32, 32), 7, 7)
        p.end()
        icon.addPixmap(pm)
    return icon
