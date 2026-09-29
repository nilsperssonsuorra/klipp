"""Fullscreen overlay showing a frozen screenshot where the user drags out a region."""

import ctypes

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QWidget

ACCENT = QColor("#2f9bff")
LABEL_FONT = QFont("Segoe UI", 9)


def disable_window_animation(widget):
    """Stop DWM from fading the window in, so the frozen frame appears instantly."""
    value = ctypes.c_int(1)
    DWMWA_TRANSITIONS_FORCEDISABLED = 3
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        int(widget.winId()), DWMWA_TRANSITIONS_FORCEDISABLED, ctypes.byref(value), ctypes.sizeof(value)
    )


class SelectionOverlay(QWidget):
    selected = Signal(QPixmap, QRect)  # cropped image, selection in global logical coords
    finished = Signal()

    def __init__(self, screen, shot, dim=45, crosshair=True):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(screen.geometry())
        disable_window_animation(self)

        self._shot = shot
        self._crosshair = crosshair
        self._dpr = shot.devicePixelRatio()
        self._dimmed = QPixmap(shot)
        painter = QPainter(self._dimmed)
        painter.fillRect(self._dimmed.rect(), QColor(0, 0, 0, round(255 * dim / 100)))
        painter.end()

        self._metrics = QFontMetrics(LABEL_FONT)
        self._anchor = None  # drag start, logical coords
        self._cursor = QPointF(self.mapFromGlobal(QCursor.pos()))  # crosshair shows right away
        self._dirty = QRegion()

    # --- geometry helpers -------------------------------------------------

    def _selection(self):
        """Current selection as a rect in physical (screenshot) pixels, or None."""
        if self._anchor is None or self._cursor is None:
            return None
        d = self._dpr
        x0, x1 = sorted((self._anchor.x(), self._cursor.x()))
        y0, y1 = sorted((self._anchor.y(), self._cursor.y()))
        left, top = round(x0 * d), round(y0 * d)
        right, bottom = round(x1 * d), round(y1 * d)
        return QRect(left, top, max(right - left, 0), max(bottom - top, 0)).intersected(self._shot.rect())

    def _to_logical(self, phys):
        d = self._dpr
        return QRectF(phys.x() / d, phys.y() / d, phys.width() / d, phys.height() / d)

    def _label_rect(self, sel_logical):
        w = self._metrics.horizontalAdvance(self._label_text()) + 14
        h = 22
        x = sel_logical.left()
        y = sel_logical.top() - h - 6
        if y < 0:
            y = sel_logical.top() + 6
            x += 6
        x = min(x, self.width() - w - 4)
        return QRectF(x, y, w, h)

    def _label_text(self):
        sel = self._selection()
        return f"{sel.width()} × {sel.height()}" if sel else ""

    def _current_dirty(self):
        region = QRegion()
        sel = self._selection()
        if sel is not None:
            r = self._to_logical(sel).toAlignedRect().adjusted(-4, -4, 4, 4)
            region += r
            region += self._label_rect(self._to_logical(sel)).toAlignedRect().adjusted(-2, -2, 2, 2)
        elif self._cursor is not None and self._crosshair:
            c = self._cursor.toPoint()
            region += QRect(0, c.y() - 2, self.width(), 5)
            region += QRect(c.x() - 2, 0, 5, self.height())
        return region

    def _refresh(self):
        new_dirty = self._current_dirty()
        self.update(self._dirty.united(new_dirty))
        self._dirty = new_dirty

    # --- events -----------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawPixmap(0, 0, self._dimmed)
        sel = self._selection()
        if sel is not None and not sel.isEmpty():
            target = self._to_logical(sel)
            p.drawPixmap(target, self._shot, QRectF(sel))
            p.setPen(QPen(ACCENT, 1))
            p.drawRect(target.adjusted(-0.5, -0.5, 0.5, 0.5))

            p.setRenderHint(QPainter.Antialiasing)
            p.setFont(LABEL_FONT)
            text = self._label_text()
            label = self._label_rect(target)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 20, 20, 220))
            p.drawRoundedRect(label, 4, 4)
            p.setPen(QColor("#ffffff"))
            p.drawText(label, Qt.AlignCenter, text)
        elif self._anchor is None and self._cursor is not None and self._crosshair:
            # Faint crosshair guides before the drag starts.
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            c = self._cursor
            p.drawLine(QPointF(0, c.y()), QPointF(self.width(), c.y()))
            p.drawLine(QPointF(c.x(), 0), QPointF(c.x(), self.height()))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._anchor = event.position()
            self._cursor = event.position()
            self._refresh()
        elif event.button() == Qt.RightButton:
            if self._anchor is not None:
                self._anchor = None
                self._refresh()
            else:
                self.close()

    def mouseMoveEvent(self, event):
        self._cursor = event.position()
        self._refresh()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._anchor is None:
            return
        self._cursor = event.position()
        sel = self._selection()
        if sel is None or sel.width() < 3 or sel.height() < 3:
            # Treat a plain click as a mis-click; let them drag again.
            self._anchor = None
            self._refresh()
            return
        crop = self._shot.copy(sel)
        crop.setDevicePixelRatio(1.0)
        global_rect = self._to_logical(sel).toAlignedRect().translated(self.geometry().topLeft())
        self.hide()
        self.selected.emit(crop, global_rect)
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def closeEvent(self, event):
        self.finished.emit()
        super().closeEvent(event)
