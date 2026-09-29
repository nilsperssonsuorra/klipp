"""Fullscreen overlay showing a frozen screenshot where the user drags out a region,
and then (for capture-and-edit) draws on it right there."""

import ctypes

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QImage, QKeySequence, QPainter, QPen, QPixmap, QRegion, QShortcut
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QWidget

from . import icons
from .config import EDITOR_KEYS
from .editor import Canvas, ToolPanel, icon_button, separator
from .theme import stylesheet

ACCENT = QColor("#2f9bff")
LABEL_FONT = QFont("Segoe UI", 9)


def disable_window_animation(widget):
    """Stop DWM from fading the window in, so the frozen frame appears instantly."""
    value = ctypes.c_int(1)
    DWMWA_TRANSITIONS_FORCEDISABLED = 3
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        int(widget.winId()), DWMWA_TRANSITIONS_FORCEDISABLED, ctypes.byref(value), ctypes.sizeof(value)
    )


class FloatingToolbar(QFrame):
    """The drawing tools, shown next to the selection on the frozen screen."""

    def __init__(self, canvas, config, parent):
        super().__init__(parent)
        self.setObjectName("floating")
        self.setStyleSheet(stylesheet() + "QFrame#floating { background: #1f2023; border: 1px solid #3a3c42;"
                           " border-radius: 10px; }")
        shadow = QGraphicsDropShadowEffect(self, blurRadius=24, offset=QPointF(0, 4), color=QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(2)
        self.panel = ToolPanel(canvas, config)
        row.addWidget(self.panel)
        row.addSpacing(8)
        row.addWidget(separator())
        row.addSpacing(8)
        self.open_button = icon_button(icons.tool_icon("expand"), "Open in the editor window (zoom, more room)")
        self.save_button = icon_button(icons.tool_icon("save"), "Save as (Ctrl+S)")
        self.cancel_button = icon_button(icons.tool_icon("close"), "Cancel (Esc)")
        self.done_button = icon_button(icons.tool_icon("done"), "Copy and close (Enter)")
        self.done_button.setStyleSheet("QToolButton { background: #2f7bff; } QToolButton:hover { background: #4a8cff; }")
        for button in (self.open_button, self.save_button, self.cancel_button, self.done_button):
            row.addWidget(button)
        self.setCursor(Qt.ArrowCursor)
        self.adjustSize()


class SelectionOverlay(QWidget):
    selected = Signal(QPixmap, QRect)  # cropped image, selection in global logical coords
    annotated = Signal(QImage, QRect, str)  # drawn-on capture, selection, and "copy" or "save"
    open_editor = Signal(QPixmap, list, QRect)  # capture and strokes, to continue in the editor window
    finished = Signal()

    def __init__(self, screen, shot, dim=45, crosshair=True, draw_config=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(screen.geometry())
        disable_window_animation(self)

        self._shot = shot
        self._crosshair = crosshair
        self._dim_percent = dim
        self._draw_config = draw_config  # set: draw on the frozen screen after selecting
        self.canvas = None
        self.toolbar = None
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
        if self._draw_config is not None:
            self._start_drawing(sel)
            return
        crop = self._shot.copy(sel)
        crop.setDevicePixelRatio(1.0)
        self.hide()
        self.selected.emit(crop, self._global_rect(sel))
        self.close()

    def _global_rect(self, sel):
        return self._to_logical(sel).toAlignedRect().translated(self.geometry().topLeft())

    # --- drawing on the frozen screen ----------------------------------------

    def _start_drawing(self, sel):
        """Turn the frozen screen into a canvas, limited to the selection."""
        self._selection_px = QRect(sel)
        full = QPixmap(self._shot)
        full.setDevicePixelRatio(1.0)
        self.canvas = Canvas(full, self._dpr, self, region=QRectF(sel), embedded=True, dim=self._dim_percent)
        self.canvas.setGeometry(self.rect())
        self.toolbar = FloatingToolbar(self.canvas, self._draw_config, self)
        self.toolbar.open_button.clicked.connect(self._open_in_editor)
        self.toolbar.save_button.clicked.connect(lambda: self._done("save"))
        self.toolbar.cancel_button.clicked.connect(self.close)
        self.toolbar.done_button.clicked.connect(lambda: self._done("copy"))
        self.toolbar.panel.install_shortcuts(self)
        for key in ("Return", "Enter", "Ctrl+C"):
            QShortcut(QKeySequence(key), self, activated=lambda: self._done("copy"))
        QShortcut(QKeySequence("Ctrl+S"), self, activated=lambda: self._done("save"))
        self._place_toolbar()
        self.canvas.show()
        self.toolbar.show()
        self.toolbar.raise_()
        self.canvas.setFocus()

    def _place_toolbar(self):
        """Below the selection, or above it, or inside it; always on screen."""
        sel = self._to_logical(self._selection_px)
        w, h, gap = self.toolbar.width(), self.toolbar.height(), 10
        x = min(max(sel.left(), 8), self.width() - w - 8)
        if sel.bottom() + gap + h <= self.height() - 8:
            y = sel.bottom() + gap
        elif sel.top() - gap - h >= 8:
            y = sel.top() - gap - h
        else:
            y = sel.bottom() - h - gap
        self.toolbar.move(QPoint(round(x), round(y)))

    def _done(self, action):
        image = self.canvas.render_image()
        self._draw_config.save(EDITOR_KEYS)
        self.hide()
        self.annotated.emit(image, self._global_rect(self._selection_px), action)
        self.close()

    def _open_in_editor(self):
        crop = self._shot.copy(self._selection_px)
        crop.setDevicePixelRatio(1.0)
        strokes = self.canvas.export_strokes()
        self._draw_config.save(EDITOR_KEYS)
        self.hide()
        self.open_editor.emit(crop, strokes, self._global_rect(self._selection_px))
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def closeEvent(self, event):
        self.finished.emit()
        super().closeEvent(event)
