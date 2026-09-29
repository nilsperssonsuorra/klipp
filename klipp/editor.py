"""Drawing on screenshots: the canvas and tool panel shared by the editor window and the
frozen-screen overlay, plus the editor window itself."""

import math
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QShortcut,
    QTransform,
    QUndoCommand,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFileDialog,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, shapes
from .config import EDITOR_KEYS
from .theme import stylesheet

PALETTE = [
    ("Red", "#ff3b30"),
    ("Orange", "#ff9500"),
    ("Yellow", "#ffe600"),
    ("Green", "#34c759"),
    ("Cyan", "#00e5ff"),
    ("Blue", "#2f7bff"),
    ("Purple", "#b44dff"),
    ("Pink", "#ff2d95"),
    ("Black", "#000000"),
    ("White", "#ffffff"),
    ("Gray", "#8e8e93"),
]

TOOLS = [
    ("pen", "Pen", "P"),
    ("highlighter", "Highlighter", "H"),
    ("line", "Line", "L"),
    ("arrow", "Arrow", "A"),
    ("rect", "Rectangle", "R"),
    ("ellipse", "Ellipse", "O"),
    ("eraser", "Eraser", "E"),
]
FREEHAND = ("pen", "highlighter")
HIGHLIGHTER_ALPHA = 110
MAX_SIZE = 80
SNAP_DELAY_MS = 450  # how long the pointer must rest at the end of a stroke before it snaps


# --- scene items & undo commands --------------------------------------------


class StrokeItem(QGraphicsPathItem):
    """A drawn mark. Its hit shape is just the visible stroke, so the eraser only
    catches it when actually touching the ink (not the inside of a rectangle)."""

    def __init__(self, pen, filled=False):
        super().__init__()
        self.setPen(pen)
        if filled:
            self.setBrush(pen.color())
        self._shape = None

    def set_path(self, path):
        self.setPath(path)
        self._shape = None

    def shape(self):
        if self._shape is None:
            stroker = QPainterPathStroker()
            stroker.setWidth(max(self.pen().widthF(), 2.0))
            stroker.setCapStyle(Qt.RoundCap)
            stroker.setJoinStyle(Qt.RoundJoin)
            shape = stroker.createStroke(self.path())
            if self.brush().style() != Qt.NoBrush:
                shape.addPath(self.path())
            shape.setFillRule(Qt.WindingFill)
            self._shape = shape
        return self._shape

    def collidesWithPath(self, path, mode=Qt.IntersectsItemShape):
        # Strokes are clipped by the drawing layer, and for clipped items Qt tests against the
        # whole clip area. The eraser must only hit the actual ink.
        return path.intersects(self.shape())


class AddItems(QUndoCommand):
    """Records strokes that were already put on the drawing layer while drawing."""

    def __init__(self, layer, items, text="Draw"):
        super().__init__(text)
        self.layer, self.items = layer, list(items)

    def redo(self):
        for item in self.items:
            if item.scene() is None:
                item.setParentItem(self.layer)

    def undo(self):
        for item in self.items:
            if item.scene() is not None:
                item.scene().removeItem(item)


class RemoveItems(AddItems):
    def __init__(self, layer, items, text="Erase"):
        super().__init__(layer, items, text)

    def redo(self):
        AddItems.undo(self)

    def undo(self):
        AddItems.redo(self)


# --- geometry helpers --------------------------------------------------------


def smooth_path(points):
    path = QPainterPath(points[0])
    if len(points) == 1:
        path.lineTo(points[0] + QPointF(0.01, 0))  # zero-length segment renders as a dot
        return path
    for i in range(1, len(points) - 1):
        path.quadTo(points[i], (points[i] + points[i + 1]) * 0.5)
    path.lineTo(points[-1])
    return path


def snap_angle(start, end):
    """Constrain the segment to the nearest 45° direction."""
    dx, dy = end.x() - start.x(), end.y() - start.y()
    length = math.hypot(dx, dy)
    angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
    return QPointF(start.x() + length * math.cos(angle), start.y() + length * math.sin(angle))


def snap_square(start, end):
    dx, dy = end.x() - start.x(), end.y() - start.y()
    side = max(abs(dx), abs(dy))
    return QPointF(start.x() + math.copysign(side, dx), start.y() + math.copysign(side, dy))


def arrow_path(start, end, width):
    path = QPainterPath(start)
    dx, dy = end.x() - start.x(), end.y() - start.y()
    length = math.hypot(dx, dy)
    if length < 0.5:
        path.lineTo(end)
        return path
    ux, uy = dx / length, dy / length
    head = min(max(width * 3.2, 12.0), length)
    half = head * 0.55
    base = QPointF(end.x() - ux * head, end.y() - uy * head)
    path.lineTo(base + QPointF(ux, uy))
    path.moveTo(end)
    path.lineTo(base + QPointF(-uy * half, ux * half))
    path.lineTo(base + QPointF(uy * half, -ux * half))
    path.closeSubpath()
    return path


def shape_path(shape):
    """A clean path for a result of shapes.recognize()."""
    kind, *values = shape
    path = QPainterPath()
    if kind == "line":
        x1, y1, x2, y2 = values
        path.moveTo(x1, y1)
        path.lineTo(x2, y2)
    elif kind == "rect":
        path.addRect(QRectF(*values))
    else:
        cx, cy, rx, ry, angle = values
        path.addEllipse(QPointF(0, 0), rx, ry)
        path = QTransform().translate(cx, cy).rotate(angle).map(path)
    return path


def circle_cursor(diameter, dpr):
    """Cursor showing the brush footprint. diameter is in logical screen pixels."""
    d = min(max(diameter, 3.0), 240.0 / dpr)
    size = math.ceil(d + 6)
    pm = QPixmap(math.ceil(size * dpr), math.ceil(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QPointF(size / 2, size / 2)
    p.setPen(QPen(QColor(0, 0, 0, 200), 1.6))
    p.drawEllipse(c, d / 2, d / 2)
    p.setPen(QPen(QColor(255, 255, 255, 230), 1.0))
    p.drawEllipse(c, d / 2 - 1.0, d / 2 - 1.0)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(c, 1.2, 1.2)
    p.end()
    return QCursor(pm)


# --- canvas -----------------------------------------------------------------


class Canvas(QGraphicsView):
    """Draw on `pixmap`. Only `region` (default: all of it) is drawn on and exported.

    In the editor window the canvas zooms and scrolls. Embedded in the frozen-screen
    overlay it shows the whole screenshot 1:1 and darkens everything outside `region`."""

    zoomChanged = Signal(float)
    snapped = Signal(str)  # a rough stroke was turned into this shape
    finishRequested = Signal()  # embedded: the user clicked outside the selection

    def __init__(self, pixmap, dpr, parent=None, region=None, embedded=False, dim=45):
        super().__init__(parent)
        self.dpr = dpr
        self.embedded = embedded
        self.tool = "pen"
        self.color = QColor("#ff3b30")
        self.sizes = {}
        self.snap_enabled = True
        self.region = QRectF(region) if region is not None else QRectF(pixmap.rect())
        self._dim = QColor(0, 0, 0, round(255 * dim / 100))

        self.setScene(QGraphicsScene(self))
        self.background = QGraphicsPixmapItem(pixmap)
        self.background.setZValue(-1)
        self.scene().addItem(self.background)
        self.scene().setSceneRect(QRectF(pixmap.rect()))
        # Strokes live on this layer, which clips them to the region being captured.
        self.layer = QGraphicsRectItem(self.region)
        self.layer.setPen(Qt.NoPen)
        self.layer.setFlag(QGraphicsRectItem.ItemClipsChildrenToShape)
        self.scene().addItem(self.layer)
        self.undo_stack = QUndoStack(self)

        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#141517"))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setContextMenuPolicy(Qt.PreventContextMenu)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)  # moves without a button held, for the cursor
        if embedded:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            self.setTransform(QTransform.fromScale(1 / dpr, 1 / dpr))
        else:
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

        self._z = 0
        self._mode = None  # "draw", "erase", "pan"
        self._button = None  # mouse button that started the current mode
        self._item = None
        self._draw_tool = None  # tool the current stroke started with
        self._points = []
        self._start = None
        self._last = None
        self._erased = []
        self._snapped = False
        self._snap_timer = QTimer(self, singleShot=True, interval=SNAP_DELAY_MS, timeout=self._try_snap)
        self._inside = True  # embedded: is the pointer over the selection?
        self._press = None  # embedded: where a left press outside the selection began

    # zoom ------------------------------------------------------------------

    @property
    def zoom(self):
        return self.transform().m11()

    def set_zoom(self, zoom):
        zoom = min(max(zoom, 0.05), 32.0)
        self.scale(zoom / self.zoom, zoom / self.zoom)
        # Nearest-neighbour when magnified (crisp pixels), smooth when shrunk.
        mode = Qt.SmoothTransformation if zoom * self.dpr < 1 else Qt.FastTransformation
        self.background.setTransformationMode(mode)
        self.update_cursor()
        self.zoomChanged.emit(zoom)

    def actual_size(self):
        self.set_zoom(1.0 / self.dpr)

    def fit(self):
        rect = self.region
        view = self.viewport().rect()
        zoom = min((view.width() - 20) / rect.width(), (view.height() - 20) / rect.height(), 1.0 / self.dpr)
        self.set_zoom(zoom)
        self.centerOn(rect.center())

    def drawForeground(self, painter, rect):
        outside = QPainterPath()
        outside.addRect(rect)
        inside = QPainterPath()
        inside.addRect(self.region)
        if self.embedded:
            # Same darkening as while selecting, plus a thin outline round the capture.
            painter.fillPath(outside.subtracted(inside), self._dim)
            painter.setPen(QPen(QColor("#2f9bff"), self.dpr))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.region.adjusted(-self.dpr / 2, -self.dpr / 2, self.dpr / 2, self.dpr / 2))
        else:
            painter.fillPath(outside.subtracted(inside), self.backgroundBrush())

    def wheelEvent(self, event):
        if self.embedded:
            return
        if event.modifiers() & Qt.ControlModifier:
            steps = event.angleDelta().y() / 120
            self.set_zoom(self.zoom * (1.15 ** steps))
        else:
            super().wheelEvent(event)

    # tool state --------------------------------------------------------------

    def size_for(self, tool):
        return self.sizes.get(tool, 6)

    def update_cursor(self, tool=None):
        tool = tool or self.tool
        if self.embedded and not self._inside and self._mode is None:
            self.viewport().setCursor(Qt.ArrowCursor)  # clicking out here finishes
        elif tool in FREEHAND or tool == "eraser":
            self.viewport().setCursor(circle_cursor(self.size_for(tool) * self.zoom, self.dpr))
        else:
            self.viewport().setCursor(Qt.CrossCursor)

    def _make_pen(self):
        color = QColor(self.color)
        if self.tool == "highlighter":
            color.setAlpha(HIGHLIGHTER_ALPHA)
        return QPen(color, self.size_for(self.tool), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    def strokes(self):
        return [item for item in self.layer.childItems() if isinstance(item, StrokeItem)]

    # mouse -------------------------------------------------------------------

    def _scene_pos(self, event):
        # Keep sub-pixel precision; mapToScene(QPoint) would snap to whole logical pixels.
        return self.viewportTransform().inverted()[0].map(event.position())

    def mousePressEvent(self, event):
        if self._mode is not None:
            return
        button = event.button()
        pos = self._scene_pos(event)
        self._button = button
        if button == Qt.LeftButton and self.embedded and not self.region.contains(pos):
            self._mode = "outside"  # a click (not a drag) out here finishes, see _finish()
            self._press = event.position()
        elif button == Qt.MiddleButton and not self.embedded:
            self._mode = "pan"
            self._last = event.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
        elif button == Qt.RightButton or (button == Qt.LeftButton and self.tool == "eraser"):
            self._mode = "erase"
            self._erased = []
            self._last = pos
            self.update_cursor("eraser")
            self._erase_along(pos, pos)
        elif button == Qt.LeftButton:
            self._mode = "draw"
            self._draw_tool = self.tool
            self._start = pos
            self._points = [pos]
            self._snapped = False
            self._item = StrokeItem(self._make_pen(), filled=self.tool == "arrow")
            self._z += 1
            self._item.setZValue(self._z)
            self._item.setParentItem(self.layer)
            self._update_shape(pos, event.modifiers())

    def mouseMoveEvent(self, event):
        if self._mode is not None and not (event.buttons() & self._button):
            # The release went missing (e.g. focus was stolen mid-drag); don't get stuck.
            self._finish()
            return
        if self.embedded and self._mode is None:
            inside = self.region.contains(self._scene_pos(event))
            if inside != self._inside:
                self._inside = inside
                self.update_cursor()
        if self._mode == "outside":
            if (event.position() - self._press).manhattanLength() > 6:
                self._mode = "outside-drag"  # became a drag: not a finishing click
        elif self._mode == "pan":
            delta = event.position() - self._last
            self._last = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - round(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - round(delta.y()))
        elif self._mode == "erase":
            pos = self._scene_pos(event)
            self._erase_along(self._last, pos)
            self._last = pos
        elif self._mode == "draw":
            self._update_shape(self._scene_pos(event), event.modifiers())

    def mouseReleaseEvent(self, event):
        if self._mode is not None and event.button() == self._button:
            self._finish()

    def _finish(self):
        mode, self._mode = self._mode, None
        self._snap_timer.stop()
        if mode == "outside":
            self.finishRequested.emit()
            return
        if mode == "draw":
            bounds = self._item.path().boundingRect()
            if self._draw_tool not in FREEHAND and max(bounds.width(), bounds.height()) < 2:
                self.scene().removeItem(self._item)  # a click with a shape tool; nothing to keep
            else:
                self.undo_stack.push(AddItems(self.layer, [self._item]))
            self._item = None
        elif mode == "erase" and self._erased:
            self.undo_stack.push(RemoveItems(self.layer, self._erased))
            self._erased = []
        self.update_cursor()

    def _update_shape(self, pos, modifiers):
        if self._snapped:
            return  # the stroke became a clean shape; it stays that way until release
        shift = bool(modifiers & Qt.ShiftModifier)
        start, tool = self._start, self._draw_tool
        if tool in FREEHAND:
            if shift:  # Shift: straight line from where the stroke began
                path = QPainterPath(start)
                path.lineTo(snap_angle(start, pos))
            else:
                last = self._points[-1]
                if math.hypot(pos.x() - last.x(), pos.y() - last.y()) * self.zoom >= 1.5:
                    self._points.append(pos)
                    if self.snap_enabled:
                        self._snap_timer.start()  # snap if the pointer rests here
                path = smooth_path(self._points)
        elif tool in ("line", "arrow"):
            end = snap_angle(start, pos) if shift else pos
            if tool == "line":
                path = QPainterPath(start)
                path.lineTo(end if end != start else end + QPointF(0.01, 0))
            else:
                path = arrow_path(start, end, self.size_for(tool))
        else:
            end = snap_square(start, pos) if shift else pos
            rect = QRectF(start, end).normalized()
            path = QPainterPath()
            if tool == "rect":
                path.addRect(rect)
            else:
                path.addEllipse(rect)
        self._item.set_path(path)

    def _try_snap(self):
        """The pointer rested at the end of a freehand stroke: tidy it into a clean shape."""
        if self._mode != "draw" or self._draw_tool not in FREEHAND or self._snapped:
            return
        allow = ("line", "ellipse", "rect") if self._draw_tool == "pen" else ("line",)
        # Thresholds in recognize() are in pixels as seen on screen.
        points = [(p.x() * self.zoom, p.y() * self.zoom) for p in self._points]
        shape = shapes.recognize(points, allow)
        if shape is None:
            return
        kind, *values = shape
        values = [v / self.zoom for v in values[:4]] + values[4:]
        self._item.set_path(shape_path((kind, *values)))
        self._snapped = True
        self.snapped.emit(kind)

    def _erase_along(self, a, b):
        stroker = QPainterPathStroker()
        stroker.setWidth(self.size_for("eraser"))
        stroker.setCapStyle(Qt.RoundCap)
        segment = QPainterPath(a)
        segment.lineTo(b if b != a else b + QPointF(0.01, 0))
        area = stroker.createStroke(segment)
        for item in self.scene().items(area, Qt.IntersectsItemShape):
            if isinstance(item, StrokeItem):
                self.scene().removeItem(item)
                self._erased.append(item)

    # actions -----------------------------------------------------------------

    def clear(self):
        items = self.strokes()
        if items:
            self.undo_stack.push(RemoveItems(self.layer, items, "Clear"))

    def render_image(self):
        rect = self.region
        image = QImage(round(rect.width()), round(rect.height()), QImage.Format_RGB32)
        image.fill(Qt.black)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.scene().render(painter, QRectF(image.rect()), rect)
        painter.end()
        return image

    def export_strokes(self):
        """The drawing, relative to the region's corner, for handing to another canvas."""
        offset = self.region.topLeft()
        return [
            (QPen(item.pen()), item.brush().style() != Qt.NoBrush, item.path().translated(-offset), item.zValue())
            for item in sorted(self.strokes(), key=lambda i: i.zValue())
        ]

    def import_strokes(self, strokes):
        for pen, filled, path, z in strokes:
            item = StrokeItem(pen, filled)
            item.set_path(path.translated(self.region.topLeft()))
            item.setZValue(z)
            item.setParentItem(self.layer)
            self._z = max(self._z, z)


# --- tool panel ---------------------------------------------------------------


class Swatch(QAbstractButton):
    def __init__(self, name, color, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setCheckable(True)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(name)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        if self.isChecked():
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        elif self.underMouse():
            p.setPen(QPen(QColor("#6b6e76"), 2))
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(self.color)
        p.drawEllipse(r.adjusted(5, 5, -5, -5))

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()


def separator():
    line = QWidget()
    line.setFixedSize(1, 26)
    line.setStyleSheet("background: #3a3c42;")
    return line


def icon_button(icon, tip, checkable=False):
    btn = QToolButton()
    btn.setIcon(icon)
    btn.setIconSize(QSize(22, 22))
    btn.setFixedSize(34, 34)
    btn.setToolTip(tip)
    btn.setCheckable(checkable)
    btn.setFocusPolicy(Qt.NoFocus)
    return btn


class ToolPanel(QWidget):
    """Tools, colours, brush size, undo/redo and clear for one canvas."""

    def __init__(self, canvas, config, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.config = config
        canvas.sizes = dict(config["sizes"])
        canvas.color = QColor(config["color"])
        canvas.snap_enabled = bool(config["snap_shapes"])

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        self.tool_group = QButtonGroup(self, exclusive=True)
        self.tool_buttons = {}
        for key, name, shortcut in TOOLS:
            btn = icon_button(icons.tool_icon(key), f"{name} ({shortcut})", checkable=True)
            btn.clicked.connect(lambda _=False, k=key: self.select_tool(k))
            self.tool_group.addButton(btn)
            self.tool_buttons[key] = btn
            row.addWidget(btn)

        row.addSpacing(8)
        row.addWidget(separator())
        row.addSpacing(8)

        self.color_group = QButtonGroup(self, exclusive=True)
        self.swatches = []
        for i, (name, color) in enumerate(PALETTE):
            key_hint = f" ({(i + 1) % 10})" if i < 10 else ""
            swatch = Swatch(name + key_hint, color)
            swatch.clicked.connect(lambda _=False, c=color: self.select_color(c))
            self.color_group.addButton(swatch)
            self.swatches.append(swatch)
            row.addWidget(swatch)

        row.addSpacing(8)
        row.addWidget(separator())
        row.addSpacing(10)

        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(1, MAX_SIZE)
        self.size_slider.setFixedWidth(110)
        self.size_slider.setToolTip("Size  ( -  and  + )")
        self.size_slider.setFocusPolicy(Qt.NoFocus)
        self.size_slider.valueChanged.connect(self._size_changed)
        row.addWidget(self.size_slider)
        self.size_label = QLabel()
        self.size_label.setFixedWidth(42)
        self.size_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.size_label)

        row.addSpacing(8)
        row.addWidget(separator())
        row.addSpacing(8)

        self.undo_button = icon_button(icons.tool_icon("undo"), "Undo (Ctrl+Z)")
        self.undo_button.clicked.connect(canvas.undo_stack.undo)
        self.undo_button.setEnabled(False)
        canvas.undo_stack.canUndoChanged.connect(self.undo_button.setEnabled)
        row.addWidget(self.undo_button)
        self.redo_button = icon_button(icons.tool_icon("redo"), "Redo (Ctrl+Y)")
        self.redo_button.clicked.connect(canvas.undo_stack.redo)
        self.redo_button.setEnabled(False)
        canvas.undo_stack.canRedoChanged.connect(self.redo_button.setEnabled)
        row.addWidget(self.redo_button)
        clear = icon_button(icons.tool_icon("clear"), "Erase all drawings")
        clear.clicked.connect(canvas.clear)
        row.addWidget(clear)

        tool = config["tool"] if config["tool"] in {t[0] for t in TOOLS} else "pen"
        self.select_tool(tool)
        self.select_color(config["color"])

    def install_shortcuts(self, host):
        """Tool, colour, size and undo keys, active while `host`'s window has focus."""

        def bind(seq, fn):
            QShortcut(QKeySequence(seq), host, activated=fn)

        for key, _, shortcut in TOOLS:
            bind(shortcut, lambda k=key: self.select_tool(k))
        for i, (_, color) in enumerate(PALETTE[:10]):
            bind(str((i + 1) % 10), lambda c=color: self.select_color(c))
        bind("Ctrl+Z", self.canvas.undo_stack.undo)
        bind("Ctrl+Y", self.canvas.undo_stack.redo)
        bind("Ctrl+Shift+Z", self.canvas.undo_stack.redo)
        for key in ("[", "-"):
            bind(key, lambda: self.step_size(-1))
        for key in ("]", "+"):
            bind(key, lambda: self.step_size(1))

    def step_size(self, direction):
        value = self.size_slider.value()
        self.size_slider.setValue(value + direction * max(1, value // 6))

    def select_tool(self, tool):
        self.canvas.tool = tool
        self.tool_buttons[tool].setChecked(True)
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(self.canvas.size_for(tool))
        self.size_slider.blockSignals(False)
        self.size_label.setText(f"{self.canvas.size_for(tool)} px")
        self.canvas.update_cursor()
        self.config["tool"] = tool

    def select_color(self, color):
        self.canvas.color = QColor(color)
        for swatch in self.swatches:
            if swatch.color == QColor(color):
                swatch.setChecked(True)
        self.config["color"] = color
        if self.canvas.tool == "eraser":
            self.select_tool("pen")

    def _size_changed(self, value):
        self.canvas.sizes[self.canvas.tool] = value
        self.size_label.setText(f"{value} px")
        self.canvas.update_cursor()
        self.config["sizes"] = dict(self.canvas.sizes)


# --- saving -------------------------------------------------------------------


def save_image_dialog(image, config, parent=None):
    """Ask where to save `image` (PNG or JPG). Returns the path, or None if cancelled."""
    folder = Path(config["last_save_dir"] or config.save_dir())
    name = datetime.now().strftime("Klipp %Y-%m-%d %H%M%S.png")
    path, _ = QFileDialog.getSaveFileName(
        parent, "Save screenshot", str(folder / name), "PNG image (*.png);;JPEG image (*.jpg *.jpeg)"
    )
    if not path:
        return None
    ok = image.save(path, quality=95) if path.lower().endswith((".jpg", ".jpeg")) else image.save(path)
    if not ok:
        QMessageBox.warning(parent, "Klipp", f"Could not save to\n{path}")
        return None
    config["last_save_dir"] = str(Path(path).parent)
    config.save(["last_save_dir"])
    return path


# --- window -----------------------------------------------------------------


class EditorWindow(QMainWindow):
    def __init__(self, pixmap, dpr, config, app_icon=None, strokes=None):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.config = config
        self.setWindowTitle(f"Klipp — {pixmap.width()} × {pixmap.height()}")
        if app_icon is not None:
            self.setWindowIcon(app_icon)
        self.setStyleSheet(stylesheet())

        self.canvas = Canvas(pixmap, dpr, self)
        if strokes:
            self.canvas.import_strokes(strokes)
        self.panel = ToolPanel(self.canvas, config)
        self.tool_buttons, self.swatches = self.panel.tool_buttons, self.panel.swatches
        self.select_tool, self.select_color = self.panel.select_tool, self.panel.select_color

        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_toolbar())
        column.addWidget(self.canvas, 1)
        self.setCentralWidget(central)

        self._copy_timer = QTimer(self, singleShot=True, interval=150, timeout=self.copy_to_clipboard)
        self.canvas.undo_stack.indexChanged.connect(lambda _: self._copy_timer.start())
        self.canvas.zoomChanged.connect(lambda z: self.zoom_label.setText(f"{round(z * dpr * 100)}%"))
        self._flash = QTimer(self, singleShot=True, interval=1400, timeout=lambda: self.copied_label.setText(""))
        self._build_shortcuts()

    def _build_toolbar(self):
        bar = QWidget(objectName="toolbar")
        bar.setAttribute(Qt.WA_StyledBackground)
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(2)
        row.addWidget(self.panel)
        row.addStretch(1)
        self.copied_label = QLabel()
        self.copied_label.setStyleSheet("color: #5ad17a;")
        row.addWidget(self.copied_label)
        row.addSpacing(8)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setToolTip("Ctrl+scroll to zoom, Ctrl+0 fit, Ctrl+1 actual size")
        row.addWidget(self.zoom_label)
        row.addSpacing(6)
        copy = icon_button(icons.tool_icon("copy"), "Copy (Ctrl+C) — happens automatically")
        copy.clicked.connect(self.copy_to_clipboard)
        row.addWidget(copy)
        save = icon_button(icons.tool_icon("save"), "Save as (Ctrl+S)")
        save.clicked.connect(self.save_as)
        row.addWidget(save)
        return bar

    def _build_shortcuts(self):
        self.panel.install_shortcuts(self)

        def bind(seq, fn):
            QShortcut(QKeySequence(seq), self, activated=fn)

        bind("Ctrl+C", self.copy_to_clipboard)
        bind("Ctrl+S", self.save_as)
        bind("Ctrl+W", self.close)
        bind("Ctrl+0", self.canvas.fit)
        bind("Ctrl+1", self.canvas.actual_size)
        bind("Ctrl+=", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25))
        bind("Ctrl++", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25))
        bind("Ctrl+-", lambda: self.canvas.set_zoom(self.canvas.zoom / 1.25))

    def copy_to_clipboard(self):
        QGuiApplication.clipboard().setImage(self.canvas.render_image())
        self.copied_label.setText("✓ Copied")
        self._flash.start()

    def save_as(self):
        save_image_dialog(self.canvas.render_image(), self.config, self)

    def present(self, near_rect=None):
        """Size the window to the image (1:1 if it fits) and show it."""
        screen = QGuiApplication.screenAt(near_rect.center()) if near_rect else None
        screen = screen or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        region = self.canvas.region
        dpr = self.canvas.dpr
        toolbar_w = self.centralWidget().sizeHint().width()
        chrome_h = 60  # toolbar height + margins
        width = min(max(region.width() / dpr + 40, toolbar_w, 640), avail.width() * 0.92)
        height = min(max(region.height() / dpr + chrome_h + 40, 360), avail.height() * 0.92)
        self.resize(int(width), int(height))
        self.move(avail.center() - self.rect().center())
        self.show()
        self.raise_()
        self.activateWindow()
        self.canvas.fit()
        QTimer.singleShot(0, self.canvas.fit)  # again once the layout has settled
        self.copy_to_clipboard()

    def closeEvent(self, event):
        self.config.save(EDITOR_KEYS)
        super().closeEvent(event)
