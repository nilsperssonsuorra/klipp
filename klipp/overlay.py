"""Fullscreen overlay showing a frozen screenshot where the user drags out a region,
and then (for capture-and-edit) draws on it right there."""

import ctypes
import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QWidget

from . import icons
from .config import EDITOR_KEYS
from .editor import Canvas, ToolPanel, icon_button, separator
from .theme import stylesheet

ACCENT = QColor("#2f9bff")
CROSSHAIR_GAP = 14  # the guide lines stop this far from the pointer, so only the real pointer forms a "+"
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
        self.done_button = icon_button(icons.tool_icon("done"),
                                       "Copy and close (Space, Enter, or click outside the selection).\n"
                                       "Ctrl+V pastes it straight into the window you came from.")
        self.done_button.setStyleSheet("QToolButton { background: #2f7bff; } QToolButton:hover { background: #4a8cff; }")
        for button in (self.open_button, self.save_button, self.cancel_button, self.done_button):
            row.addWidget(button)
        self.setCursor(Qt.ArrowCursor)
        self.adjustSize()


class SelectionOverlay(QWidget):
    selected = Signal(QPixmap, QRect)  # cropped image, selection in global logical coords
    annotated = Signal(QImage, QRect, str)  # drawn-on capture, selection, and "copy", "save" or "paste"
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
        # Darkening is painted per update rather than baked into a copy of the whole screenshot:
        # copying and filling a 4K image up front delayed the frozen screen by 20-35 ms.
        self._dim = QColor(0, 0, 0, round(255 * dim / 100))

        self._metrics = QFontMetrics(LABEL_FONT)
        self._anchor = None  # drag start, logical coords
        self._cursor = QPointF(self.mapFromGlobal(QCursor.pos()))  # crosshair shows right away
        self._shown_selection = None  # the selection as last painted, see _refresh()
        self._guides_at = None  # where the guide lines were last painted, so they can be erased

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

    def _covering_pixels(self, rect):
        """The screenshot pixels (physical) that cover a logical rectangle."""
        d = self._dpr
        x0, y0 = math.floor(rect.left() * d), math.floor(rect.top() * d)
        x1, y1 = math.ceil(rect.right() * d), math.ceil(rect.bottom() * d)
        return QRect(x0, y0, x1 - x0, y1 - y0).intersected(self._shot.rect())

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

    def _selection_state(self):
        """What the selection looks like now: its outline and size label, in logical px."""
        sel = self._selection()
        if sel is None:
            return None
        rect = self._to_logical(sel)
        return rect, self._label_rect(rect)

    def _refresh(self):
        """Repaint what the selection change touched, as a few separate thin pieces.

        Qt hands Windows the bounding box of one update, so repainting a growing selection in
        one go redraws the whole selection area on every mouse move. Only the edges that moved
        (and the size label) actually change."""
        old, new = self._shown_selection, self._selection_state()
        self._shown_selection = new
        pieces = []
        if old is not None and new is not None:
            (a, label_a), (b, label_b) = old, new
            span = a.united(b).toAlignedRect().adjusted(-3, -3, 3, 3)
            for u, v in ((a.left(), b.left()), (a.right(), b.right())):
                if u != v:
                    pieces.append(QRect(math.floor(min(u, v)) - 3, span.top(), math.ceil(abs(u - v)) + 7, span.height()))
            for u, v in ((a.top(), b.top()), (a.bottom(), b.bottom())):
                if u != v:
                    pieces.append(QRect(span.left(), math.floor(min(u, v)) - 3, span.width(), math.ceil(abs(u - v)) + 7))
            pieces += [label_a.toAlignedRect().adjusted(-2, -2, 2, 2), label_b.toAlignedRect().adjusted(-2, -2, 2, 2)]
        else:
            for state in (old, new):
                if state is not None:
                    rect, label = state
                    pieces += [rect.toAlignedRect().adjusted(-3, -3, 3, 3), label.toAlignedRect().adjusted(-2, -2, 2, 2)]
        for piece in pieces:
            self.repaint(piece)

    # --- events -----------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        # Only copy the part of the screenshot this paint covers (letting the clip discard the
        # rest of a 4K image made every partial repaint cost as much as a full one), snapped to
        # whole screenshot pixels so it's copied 1:1 and never resampled half a pixel off.
        area = self._covering_pixels(QRectF(event.rect()))
        p.drawPixmap(self._to_logical(area), self._shot, QRectF(area))
        p.fillRect(self._to_logical(area), self._dim)
        sel = self._selection()
        if sel is not None and not sel.isEmpty():
            target = self._to_logical(sel)
            bright = sel.intersected(area)
            if not bright.isEmpty():
                p.drawPixmap(self._to_logical(bright), self._shot, QRectF(bright))
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
        elif self._guides_visible():
            self._paint_guides(p)

    # --- crosshair guides -------------------------------------------------

    def _guides_visible(self):
        return self._crosshair and self._anchor is None and self.canvas is None and self._cursor is not None

    def _paint_guides(self, p):
        """Faint guide lines through the pointer while choosing where to start. They stop
        short of the pointer so the real pointer is the only "+"."""
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        x, y, gap = round(self._cursor.x()), round(self._cursor.y()), CROSSHAIR_GAP
        p.drawLine(QPointF(0, y), QPointF(x - gap, y))
        p.drawLine(QPointF(x + gap, y), QPointF(self.width(), y))
        p.drawLine(QPointF(x, 0), QPointF(x, y - gap))
        p.drawLine(QPointF(x, y + gap), QPointF(x, self.height()))
        self._guides_at = (x, y)

    def _move_guides(self):
        """Redraw only the thin strips the guide lines leave and enter, each one on its own.

        Qt hands Windows the bounding box of everything that changed in one update, and two
        full-length lines span the whole screen: that was a full 4K redraw (~8 ms) per mouse
        move. Four separate strip repaints are a fraction of a millisecond each."""
        old, self._guides_at = self._guides_at, None
        new = (round(self._cursor.x()), round(self._cursor.y())) if self._guides_visible() else None
        rows = [pos[1] for pos in (old, new) if pos is not None]
        cols = [pos[0] for pos in (old, new) if pos is not None]
        strips = []
        for values, make in ((rows, lambda lo, hi: QRect(0, lo - 1, self.width(), hi - lo + 3)),
                             (cols, lambda lo, hi: QRect(lo - 1, 0, hi - lo + 3, self.height()))):
            if len(values) == 2 and abs(values[0] - values[1]) <= 24:
                strips.append(make(min(values), max(values)))  # close together: one strip
            else:
                strips += [make(v, v) for v in values]
        for strip in strips:
            self.repaint(strip)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._anchor = event.position()
            self._cursor = event.position()
            self._move_guides()  # erases them: they're hidden while dragging
            self._refresh()
        elif event.button() == Qt.RightButton:
            if self._anchor is not None:
                self._anchor = None
                self._refresh()
                self._move_guides()
            else:
                self.close()

    def mouseMoveEvent(self, event):
        self._cursor = event.position()
        if self._anchor is None:
            self._move_guides()
        else:
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
            self._move_guides()
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
        self.canvas.finishRequested.connect(lambda: self._done("copy"))
        self.toolbar.panel.install_shortcuts(self)
        # Space and Enter finish from the canvas itself, so they can type into a label instead.
        QShortcut(QKeySequence("Ctrl+C"), self, activated=lambda: self._done("copy"))
        QShortcut(QKeySequence("Ctrl+S"), self, activated=lambda: self._done("save"))
        # The paste you were about to do anyway: copy, close, and paste where you came from.
        QShortcut(QKeySequence("Ctrl+V"), self, activated=lambda: self._done("paste"))
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
