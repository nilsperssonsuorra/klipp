"""Annotation editor: draw on a screenshot, erase whole strokes, auto-copy to clipboard."""

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
    QUndoCommand,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFileDialog,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
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

from . import icons
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


class AddItems(QUndoCommand):
    """Records items that were already added to the scene while drawing."""

    def __init__(self, scene, items, text="Draw"):
        super().__init__(text)
        self.scene, self.items = scene, list(items)

    def redo(self):
        for item in self.items:
            if item.scene() is None:
                self.scene.addItem(item)

    def undo(self):
        for item in self.items:
            if item.scene() is not None:
                self.scene.removeItem(item)


class RemoveItems(AddItems):
    def __init__(self, scene, items, text="Erase"):
        super().__init__(scene, items, text)

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
    zoomChanged = Signal(float)

    def __init__(self, pixmap, dpr, parent=None):
        super().__init__(parent)
        self.dpr = dpr
        self.tool = "pen"
        self.color = QColor("#ff3b30")
        self.sizes = {}

        self.setScene(QGraphicsScene(self))
        self.background = QGraphicsPixmapItem(pixmap)
        self.background.setZValue(-1)
        self.scene().addItem(self.background)
        self.scene().setSceneRect(QRectF(pixmap.rect()))
        self.undo_stack = QUndoStack(self)

        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#141517"))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setContextMenuPolicy(Qt.PreventContextMenu)
        self.setMouseTracking(True)

        self._z = 0
        self._mode = None  # "draw", "erase", "pan"
        self._button = None  # mouse button that started the current mode
        self._item = None
        self._draw_tool = None  # tool the current stroke started with
        self._points = []
        self._start = None
        self._last = None
        self._erased = []

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
        rect = self.scene().sceneRect()
        view = self.viewport().rect()
        zoom = min((view.width() - 20) / rect.width(), (view.height() - 20) / rect.height(), 1.0 / self.dpr)
        self.set_zoom(zoom)
        self.centerOn(rect.center())

    def drawForeground(self, painter, rect):
        # Hide ink that strays outside the image, since it's not part of the output.
        outside = QPainterPath()
        outside.addRect(rect)
        inside = QPainterPath()
        inside.addRect(self.scene().sceneRect())
        painter.fillPath(outside.subtracted(inside), self.backgroundBrush())

    def wheelEvent(self, event):
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
        if tool in FREEHAND or tool == "eraser":
            self.viewport().setCursor(circle_cursor(self.size_for(tool) * self.zoom, self.dpr))
        else:
            self.viewport().setCursor(Qt.CrossCursor)

    def _make_pen(self):
        color = QColor(self.color)
        if self.tool == "highlighter":
            color.setAlpha(HIGHLIGHTER_ALPHA)
        return QPen(color, self.size_for(self.tool), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

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
        if button == Qt.MiddleButton:
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
            self._item = StrokeItem(self._make_pen(), filled=self.tool == "arrow")
            self._z += 1
            self._item.setZValue(self._z)
            self.scene().addItem(self._item)
            self._update_shape(pos, event.modifiers())

    def mouseMoveEvent(self, event):
        if self._mode is not None and not (event.buttons() & self._button):
            # The release went missing (e.g. focus was stolen mid-drag); don't get stuck.
            self._finish()
            return
        if self._mode == "pan":
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
        if mode == "draw":
            bounds = self._item.path().boundingRect()
            if self._draw_tool not in FREEHAND and max(bounds.width(), bounds.height()) < 2:
                self.scene().removeItem(self._item)  # a click with a shape tool; nothing to keep
            else:
                self.undo_stack.push(AddItems(self.scene(), [self._item]))
            self._item = None
        elif mode == "erase" and self._erased:
            self.undo_stack.push(RemoveItems(self.scene(), self._erased))
            self._erased = []
        self.update_cursor()

    def _update_shape(self, pos, modifiers):
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
        items = [i for i in self.scene().items() if isinstance(i, StrokeItem)]
        if items:
            self.undo_stack.push(RemoveItems(self.scene(), items, "Clear"))

    def render_image(self):
        rect = self.scene().sceneRect()
        image = QImage(int(rect.width()), int(rect.height()), QImage.Format_RGB32)
        image.fill(Qt.black)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.scene().render(painter, QRectF(image.rect()), rect)
        painter.end()
        return image


# --- toolbar widgets --------------------------------------------------------


class Swatch(QAbstractButton):
    def __init__(self, name, color, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setCheckable(True)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(name)

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


def _separator():
    line = QWidget()
    line.setFixedSize(1, 26)
    line.setStyleSheet("background: #3a3c42;")
    return line


# --- window -----------------------------------------------------------------


class EditorWindow(QMainWindow):
    def __init__(self, pixmap, dpr, config, app_icon=None):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.config = config
        self.setWindowTitle(f"Klipp — {pixmap.width()} × {pixmap.height()}")
        if app_icon is not None:
            self.setWindowIcon(app_icon)
        self.setStyleSheet(stylesheet())

        self.canvas = Canvas(pixmap, dpr, self)
        self.canvas.sizes = dict(config["sizes"])
        self.canvas.color = QColor(config["color"])

        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_toolbar())
        column.addWidget(self.canvas, 1)
        self.setCentralWidget(central)

        self._copy_timer = QTimer(self, singleShot=True, interval=150, timeout=self.copy_to_clipboard)
        self.canvas.undo_stack.indexChanged.connect(lambda _: self._copy_timer.start())
        self.canvas.undo_stack.canUndoChanged.connect(self.undo_button.setEnabled)
        self.canvas.undo_stack.canRedoChanged.connect(self.redo_button.setEnabled)
        self.canvas.zoomChanged.connect(lambda z: self.zoom_label.setText(f"{round(z * dpr * 100)}%"))
        self._build_shortcuts()

        tool = config["tool"] if config["tool"] in dict((t[0], t) for t in TOOLS) else "pen"
        self.select_tool(tool)
        self.select_color(config["color"])
        self._flash = QTimer(self, singleShot=True, interval=1400, timeout=lambda: self.copied_label.setText(""))

    # construction ------------------------------------------------------------

    def _build_toolbar(self):
        bar = QWidget(objectName="toolbar")
        bar.setAttribute(Qt.WA_StyledBackground)
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(2)

        self.tool_group = QButtonGroup(self, exclusive=True)
        self.tool_buttons = {}
        for key, name, shortcut in TOOLS:
            btn = self._icon_button(icons.tool_icon(key), f"{name} ({shortcut})", checkable=True)
            btn.clicked.connect(lambda _=False, k=key: self.select_tool(k))
            self.tool_group.addButton(btn)
            self.tool_buttons[key] = btn
            row.addWidget(btn)

        row.addSpacing(8)
        row.addWidget(_separator())
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
        row.addWidget(_separator())
        row.addSpacing(10)

        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(1, MAX_SIZE)
        self.size_slider.setFixedWidth(120)
        self.size_slider.setToolTip("Size  ( -  and  + )")
        self.size_slider.valueChanged.connect(self._size_changed)
        row.addWidget(self.size_slider)
        self.size_label = QLabel()
        self.size_label.setFixedWidth(42)
        self.size_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.size_label)

        row.addSpacing(8)
        row.addWidget(_separator())
        row.addSpacing(8)

        self.undo_button = self._icon_button(icons.tool_icon("undo"), "Undo (Ctrl+Z)")
        self.undo_button.clicked.connect(self.canvas.undo_stack.undo)
        self.undo_button.setEnabled(False)
        row.addWidget(self.undo_button)
        self.redo_button = self._icon_button(icons.tool_icon("redo"), "Redo (Ctrl+Y)")
        self.redo_button.clicked.connect(self.canvas.undo_stack.redo)
        self.redo_button.setEnabled(False)
        row.addWidget(self.redo_button)
        clear = self._icon_button(icons.tool_icon("clear"), "Erase all drawings")
        clear.clicked.connect(self.canvas.clear)
        row.addWidget(clear)

        row.addStretch(1)
        self.copied_label = QLabel()
        self.copied_label.setStyleSheet("color: #5ad17a;")
        row.addWidget(self.copied_label)
        row.addSpacing(8)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setToolTip("Ctrl+scroll to zoom, Ctrl+0 fit, Ctrl+1 actual size")
        row.addWidget(self.zoom_label)
        row.addSpacing(6)
        copy = self._icon_button(icons.tool_icon("copy"), "Copy (Ctrl+C) — happens automatically")
        copy.clicked.connect(self.copy_to_clipboard)
        row.addWidget(copy)
        save = self._icon_button(icons.tool_icon("save"), "Save as (Ctrl+S)")
        save.clicked.connect(self.save_as)
        row.addWidget(save)
        return bar

    def _icon_button(self, icon, tip, checkable=False):
        btn = QToolButton()
        btn.setIcon(icon)
        btn.setIconSize(QSize(22, 22))
        btn.setFixedSize(34, 34)
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        btn.setFocusPolicy(Qt.NoFocus)
        return btn

    def _build_shortcuts(self):
        def bind(seq, fn):
            QShortcut(QKeySequence(seq), self, activated=fn)

        for key, _, shortcut in TOOLS:
            bind(shortcut, lambda k=key: self.select_tool(k))
        for i, (_, color) in enumerate(PALETTE[:10]):
            bind(str((i + 1) % 10), lambda c=color: self.select_color(c))
        bind("Ctrl+Z", self.canvas.undo_stack.undo)
        bind("Ctrl+Y", self.canvas.undo_stack.redo)
        bind("Ctrl+Shift+Z", self.canvas.undo_stack.redo)
        bind("Ctrl+C", self.copy_to_clipboard)
        bind("Ctrl+S", self.save_as)
        bind("Ctrl+W", self.close)
        bind("Ctrl+0", self.canvas.fit)
        bind("Ctrl+1", self.canvas.actual_size)
        bind("Ctrl+=", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25))
        bind("Ctrl++", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25))
        bind("Ctrl+-", lambda: self.canvas.set_zoom(self.canvas.zoom / 1.25))
        for key in ("[", "-"):
            bind(key, lambda: self._step_size(-1))
        for key in ("]", "+"):
            bind(key, lambda: self._step_size(1))

    def _step_size(self, direction):
        value = self.size_slider.value()
        self.size_slider.setValue(value + direction * max(1, value // 6))

    # state -------------------------------------------------------------------

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

    # output ------------------------------------------------------------------

    def copy_to_clipboard(self):
        QGuiApplication.clipboard().setImage(self.canvas.render_image())
        self.copied_label.setText("✓ Copied")
        self._flash.start()

    def save_as(self):
        folder = Path(self.config["last_save_dir"] or self.config.save_dir())
        name = datetime.now().strftime("Klipp %Y-%m-%d %H%M%S.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save screenshot", str(folder / name), "PNG image (*.png);;JPEG image (*.jpg *.jpeg)"
        )
        if not path:
            return
        image = self.canvas.render_image()
        ok = image.save(path, quality=95) if path.lower().endswith((".jpg", ".jpeg")) else image.save(path)
        if not ok:
            QMessageBox.warning(self, "Klipp", f"Could not save to\n{path}")
            return
        self.config["last_save_dir"] = str(Path(path).parent)
        self.config.save(["last_save_dir"])

    # window ------------------------------------------------------------------

    def present(self, near_rect=None):
        """Size the window to the image (1:1 if it fits) and show it."""
        screen = QGuiApplication.screenAt(near_rect.center()) if near_rect else None
        screen = screen or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        scene = self.canvas.scene().sceneRect()
        dpr = self.canvas.dpr
        toolbar_w = self.centralWidget().sizeHint().width()
        chrome_h = 60  # toolbar height + margins
        width = min(max(scene.width() / dpr + 40, toolbar_w, 640), avail.width() * 0.92)
        height = min(max(scene.height() / dpr + chrome_h + 40, 360), avail.height() * 0.92)
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
