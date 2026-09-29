"""Renders docs/demo.gif and docs/settings.png for the README.

The real Klipp widgets are driven with synthetic mouse events on a staged desktop, entirely
off-screen (nothing appears on your monitor and the clipboard is left alone). Needs ffmpeg on PATH.

Run: .venv\\Scripts\\python tools\\make_demo.py
"""

import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication

from klipp import icons
from klipp.app import Toast
from klipp.config import DEFAULTS, Config
from klipp.editor import EditorWindow
from klipp.overlay import SelectionOverlay
from klipp.settings import SettingsDialog

W, H = 1360, 820  # logical size of the staged desktop
FPS = 20
OUT_WIDTH = 1200
DOCS = ROOT / "docs"


def font(size, weight=QFont.Normal):
    f = QFont("Segoe UI", size)
    f.setWeight(weight)
    return f


# --- staged desktop ---------------------------------------------------------------


def paint_desktop(d):
    pm = QPixmap(int(W * d), int(H * d))
    pm.setDevicePixelRatio(d)
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)

    wall = QLinearGradient(0, 0, W, H)
    wall.setColorAt(0, QColor("#18243f"))
    wall.setColorAt(1, QColor("#3a1f4d"))
    p.fillRect(QRectF(0, 0, W, H), wall)
    for cx, cy, r, color in ((1350, 150, 520, "#3d5bd9"), (150, 820, 480, "#b0407a")):
        glow = QRadialGradient(cx, cy, r)
        c = QColor(color)
        c.setAlpha(90)
        glow.setColorAt(0, c)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, W, H), glow)

    # A dashboard window to screenshot.
    win = QRectF(90, 30, 1180, 720)
    p.setPen(Qt.NoPen)
    for i in range(12):
        p.setBrush(QColor(0, 0, 0, 10))
        p.drawRoundedRect(win.adjusted(-i, -i + 6, i, i + 6), 10 + i, 10 + i)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(win, 10, 10)
    title = QRectF(win.left(), win.top(), win.width(), 38)
    path = QPainterPath()
    path.addRoundedRect(title, 10, 10)
    path.addRect(title.adjusted(0, 19, 0, 0))
    p.fillPath(path.simplified(), QColor("#f1f2f4"))
    p.setPen(QColor("#444"))
    p.setFont(font(9))
    p.drawText(title.adjusted(16, 0, 0, 0), Qt.AlignVCenter, "Q3 Report — Sales Dashboard")
    for i, glyph in enumerate(("—", "☐", "✕")):
        p.drawText(QRectF(win.right() - 138 + i * 46, win.top(), 46, 38), Qt.AlignCenter, glyph)

    left, top = win.left() + 40, win.top() + 70
    p.setPen(QColor("#15171c"))
    p.setFont(font(20, QFont.DemiBold))
    p.drawText(QPointF(left, top + 18), "Revenue by region")
    p.setPen(QColor("#6b7280"))
    p.setFont(font(10))
    p.drawText(QPointF(left, top + 44), "July – September, in thousands of USD")

    # Bar chart.
    chart = QRectF(left, top + 80, 640, 380)
    p.setPen(QPen(QColor("#e5e7eb"), 1))
    for i in range(5):
        y = chart.bottom() - i * chart.height() / 4
        p.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
    bars = [("North", 420), ("South", 360), ("East", 510), ("West", 300), ("Online", 880), ("Retail", 470)]
    slot = chart.width() / len(bars)
    for i, (name, value) in enumerate(bars):
        h = chart.height() * value / 1000
        x = chart.left() + i * slot + slot * 0.2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2f7bff" if name != "Online" else "#1f5fd6"))
        p.drawRoundedRect(QRectF(x, chart.bottom() - h, slot * 0.6, h), 4, 4)
        p.setPen(QColor("#374151"))
        p.setFont(font(9, QFont.DemiBold))
        p.drawText(QRectF(x - 10, chart.bottom() - h - 24, slot * 0.6 + 20, 20), Qt.AlignCenter, str(value))
        p.setPen(QColor("#6b7280"))
        p.setFont(font(9))
        p.drawText(QRectF(x - 10, chart.bottom() + 8, slot * 0.6 + 20, 20), Qt.AlignCenter, name)

    # KPI cards.
    cards = [("Total revenue", "$2.94M", "▲ 12% vs Q2", "#16a34a"),
             ("New customers", "1,284", "▲ 8% vs Q2", "#16a34a"),
             ("Churn", "2.1%", "▼ 0.4 pts", "#dc2626")]
    for i, (label, value, delta, color) in enumerate(cards):
        card = QRectF(left + 700, top + 80 + i * 128, 380, 108)
        p.setPen(QPen(QColor("#e5e7eb"), 1))
        p.setBrush(QColor("#fafafa"))
        p.drawRoundedRect(card, 8, 8)
        p.setPen(QColor("#6b7280"))
        p.setFont(font(10))
        p.drawText(QPointF(card.left() + 20, card.top() + 32), label)
        p.setPen(QColor("#111827"))
        p.setFont(font(22, QFont.DemiBold))
        p.drawText(QPointF(card.left() + 20, card.top() + 74), value)
        p.setPen(QColor(color))
        p.setFont(font(10, QFont.DemiBold))
        p.drawText(QPointF(card.left() + 210, card.top() + 74), delta)

    p.setPen(QColor("#4b5563"))
    p.setFont(font(10))
    p.drawText(QPointF(left, top + 520), "Online sales grew 38% after the spring campaign, while West lagged behind")
    p.drawText(QPointF(left, top + 544), "due to the store renovation in August. Retail is back on track for Q4.")
    p.end()
    return pm


# --- frame helpers ----------------------------------------------------------------


class Recorder:
    def __init__(self, d):
        self.d = d
        self.frames = []

    def frame(self, layers, cursor=None, badge=None, caption=None, repeat=1):
        img = QImage(int(W * self.d), int(H * self.d), QImage.Format_RGB32)
        img.setDevicePixelRatio(self.d)
        p = QPainter(img)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        for pm, pos in layers:
            p.drawPixmap(QPointF(*pos), pm)
        if caption:
            draw_caption(p, caption)
        if badge:
            draw_keys(p, badge)
        if cursor:
            draw_cursor(p, *cursor)
        p.end()
        self.frames.extend([img] * repeat)


def draw_caption(p, text):
    p.setFont(font(15, QFont.DemiBold))
    w = QFontMetrics(p.font()).horizontalAdvance(text) + 48
    rect = QRectF((W - w) / 2, 22, w, 50)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(15, 16, 20, 225))
    p.drawRoundedRect(rect, 25, 25)
    p.setPen(QColor("#ffffff"))
    p.drawText(rect, Qt.AlignCenter, text)


def draw_keys(p, keys):
    p.setFont(font(16, QFont.DemiBold))
    metrics = QFontMetrics(p.font())
    caps = [metrics.horizontalAdvance(k) + 30 for k in keys]
    plus = 30
    total = sum(caps) + plus * (len(keys) - 1) + 36
    x = (W - total) / 2
    rect = QRectF(x, H - 140, total, 70)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(15, 16, 20, 225))
    p.drawRoundedRect(rect, 16, 16)
    x += 18
    for i, (key, w) in enumerate(zip(keys, caps)):
        cap = QRectF(x, rect.top() + 13, w, 44)
        p.setPen(QPen(QColor("#5a5d66"), 1.5))
        p.setBrush(QColor("#2b2d33"))
        p.drawRoundedRect(cap, 8, 8)
        p.setPen(QColor("#ffffff"))
        p.drawText(cap, Qt.AlignCenter, key)
        x += w
        if i < len(keys) - 1:
            p.setPen(QColor("#9a9da5"))
            p.drawText(QRectF(x, cap.top(), plus, cap.height()), Qt.AlignCenter, "+")
            x += plus


def draw_cursor(p, kind, pos, size=0.0):
    x, y = pos
    if kind == "arrow":
        shape = QPolygonF([QPointF(x + dx, y + dy) for dx, dy in
                           ((0, 0), (0, 21), (5, 16), (9, 25), (12, 24), (8, 15), (15, 15))])
        p.setPen(QPen(QColor("#000000"), 1.2))
        p.setBrush(QColor("#ffffff"))
        p.drawPolygon(shape)
    elif kind == "cross":
        for color, width in ((QColor(0, 0, 0, 180), 3.0), (QColor("#ffffff"), 1.2)):
            p.setPen(QPen(color, width))
            p.drawLine(QPointF(x - 11, y), QPointF(x + 11, y))
            p.drawLine(QPointF(x, y - 11), QPointF(x, y + 11))
    elif kind == "brush":
        r = max(size, 4) / 2
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 200), 1.6))
        p.drawEllipse(QPointF(x, y), r, r)
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.0))
        p.drawEllipse(QPointF(x, y), r - 1, r - 1)


def send_mouse(widget, kind, pos, button=Qt.NoButton, buttons=Qt.NoButton):
    local = QPointF(*pos)
    event = QMouseEvent(kind, local, widget.mapToGlobal(local), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


def lerp(a, b, t):
    t = 0.5 - math.cos(math.pi * t) / 2  # ease in/out
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def window_chrome(content, title, d):
    """Wrap a grabbed window in a title bar and shadow, like Windows would draw it."""
    cw, ch = content.width() / d, content.height() / d
    margin, bar = 26, 32
    pm = QPixmap(int((cw + 2 * margin) * d), int((ch + bar + 2 * margin) * d))
    pm.setDevicePixelRatio(d)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    body = QRectF(margin, margin, cw, ch + bar)
    p.setPen(Qt.NoPen)
    for i in range(margin, 0, -2):
        p.setBrush(QColor(0, 0, 0, 7))
        p.drawRoundedRect(body.adjusted(-i, -i + 8, i, i + 8), 8 + i, 8 + i)
    p.setBrush(QColor("#202124"))
    p.drawRoundedRect(body, 8, 8)
    p.drawPixmap(QPointF(margin + 10, margin + 8), icons.app_icon().pixmap(16, 16))
    p.setPen(QColor("#d0d0d0"))
    p.setFont(font(9))
    p.drawText(QRectF(margin + 34, margin, cw, bar), Qt.AlignVCenter, title)
    for i, glyph in enumerate(("—", "☐", "✕")):
        p.drawText(QRectF(margin + cw - 138 + i * 46, margin, 46, bar), Qt.AlignCenter, glyph)
    p.drawPixmap(QPointF(margin, margin + bar), content)
    p.end()
    return pm, (margin, margin + bar)


def off_screen(widget):
    widget.setAttribute(Qt.WA_DontShowOnScreen)
    widget.show()
    QApplication.processEvents()


# --- storyboard -------------------------------------------------------------------


def select_area(rec, desktop, keys, start, end, caption=None):
    """Hotkey badge, frozen overlay, drag a selection. Returns (crop, rect)."""
    overlay = SelectionOverlay(QGuiApplication.primaryScreen(), desktop, 45, True)
    overlay.setGeometry(0, 0, W, H)
    off_screen(overlay)
    result = {}
    overlay.selected.connect(lambda crop, rect: result.update(crop=crop, rect=rect))

    cursor = (1150, 700)
    for i in range(8):
        rec.frame([(desktop, (0, 0))], ("arrow", cursor), badge=keys, caption=caption)
    for i in range(14):
        pos = lerp(cursor, start, (i + 1) / 14)
        send_mouse(overlay, QEvent.MouseMove, pos)
        rec.frame([(overlay.grab(), (0, 0))], ("cross", pos), badge=keys if i < 6 else None, caption=caption)
    send_mouse(overlay, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    for i in range(18):
        pos = lerp(start, end, (i + 1) / 18)
        send_mouse(overlay, QEvent.MouseMove, pos, Qt.NoButton, Qt.LeftButton)
        rec.frame([(overlay.grab(), (0, 0))], ("cross", pos), caption=caption)
    rec.frame([(overlay.grab(), (0, 0))], ("cross", end), caption=caption, repeat=6)
    send_mouse(overlay, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
    QApplication.processEvents()
    return result["crop"], result["rect"]


def main():
    app = QApplication(sys.argv)
    d = QGuiApplication.primaryScreen().devicePixelRatio()
    DOCS.mkdir(exist_ok=True)
    desktop = paint_desktop(d)
    rec = Recorder(d)
    app.setApplicationName("Klipp")

    # Keep the demo away from the real clipboard.
    EditorWindow.copy_to_clipboard = lambda self: (self.copied_label.setText("✓ Copied"), self._flash.start())

    # 1) Alt+S: straight to the clipboard.
    caption = "Alt+S  →  drag an area  →  it's on your clipboard"
    _, rect = select_area(rec, desktop, ["Alt", "S"], (815, 166), (1225, 556), caption)
    toast = Toast("✓ Copied to clipboard", QPoint(rect.center().x(), rect.bottom()))
    off_screen(toast)
    toast.move(int(rect.center().x() - toast.width() / 2), rect.bottom() + 12)
    for i in range(26):
        rec.frame([(desktop, (0, 0)), (toast.grab(), (toast.x(), toast.y()))], ("arrow", (1225, 556)), caption=caption)
    for i in range(6):
        rec.frame([(desktop, (0, 0))], ("arrow", (1225, 556)))

    # 2) Alt+Shift+S: capture and draw.
    caption = "Alt+Shift+S  →  drag an area  →  draw on it"
    crop, rect = select_area(rec, desktop, ["Alt", "Shift", "S"], (112, 78), (828, 660), caption)
    config = Config(json.loads(json.dumps(DEFAULTS)))
    config.save = lambda keys: None
    editor = EditorWindow(crop, d, config)
    editor.resize(1120, int(crop.height() / d) + 66)
    off_screen(editor)
    editor.canvas.fit()
    QApplication.processEvents()

    def editor_layers():
        pm, (cx, cy) = window_chrome(editor.grab(), editor.windowTitle(), d)
        ex, ey = (W - pm.width() / d) / 2, (H - pm.height() / d) / 2
        return [(desktop, (0, 0)), (pm, (ex, ey))], (ex + cx, ey + cy)

    layers, origin = editor_layers()
    vp = editor.canvas.viewport()

    def to_frame(widget_pos, widget=vp):
        off = widget.mapTo(editor, QPoint(0, 0))
        return (origin[0] + off.x() + widget_pos[0], origin[1] + off.y() + widget_pos[1])

    def brush():
        return editor.canvas.size_for(editor.canvas.tool) * editor.canvas.zoom

    def image_to_vp(x, y):
        """Position in the captured image (logical px) -> viewport coords."""
        pt = editor.canvas.mapFromScene(QPointF(x * d, y * d))
        return (pt.x(), pt.y())

    def stroke(points, caption, button=Qt.LeftButton, kind="brush", frames_per_point=1):
        send_mouse(vp, QEvent.MouseButtonPress, points[0], button, button)
        for pt in points[1:]:
            send_mouse(vp, QEvent.MouseMove, pt, Qt.NoButton, button)
            layers, _ = editor_layers()
            rec.frame(layers, (kind, to_frame(pt), brush()), caption=caption, repeat=frames_per_point)
        send_mouse(vp, QEvent.MouseButtonRelease, points[-1], button, Qt.NoButton)

    def move_cursor(a, b, caption, frames=10, kind="arrow"):
        for i in range(frames):
            layers, _ = editor_layers()
            rec.frame(layers, (kind, lerp(a, b, (i + 1) / frames), brush()), caption=caption)

    def click(button, caption, at):
        center = button.rect().center()
        target = to_frame((center.x(), center.y()), button)
        move_cursor(at, target, caption)
        button.click()
        layers, _ = editor_layers()
        rec.frame(layers, ("arrow", target), caption=caption, repeat=4)
        return target

    for i in range(10):
        layers, _ = editor_layers()
        rec.frame(layers, ("arrow", (828, 660)), caption=caption)

    # Positions below are in the captured image (logical px from its top-left corner):
    # the Online bar is centred at x=498 with its top at y=148; the title's baseline is y=40.
    caption = "Circle what matters"
    cx, cy, rx, ry = 498, 160, 60, 42
    circle = [image_to_vp(cx + rx * math.cos(a), cy + ry * math.sin(a))
              for a in (math.radians(-100 + t * 12.5) for t in range(32))]
    move_cursor((828, 660), to_frame(circle[0]), caption, frames=10, kind="brush")
    stroke(circle, caption)

    caption = "Pick a tool and a color with one click"
    at = click(editor.tool_buttons["arrow"], caption, to_frame(circle[-1]))
    at = click(editor.swatches[5], caption, at)  # blue
    arrow = [image_to_vp(700 - t * 13.5, 300 - t * 9.4) for t in range(12)]  # ends just outside the circle
    move_cursor(at, to_frame(arrow[0]), caption, kind="cross")
    stroke(arrow, caption, kind="cross", frames_per_point=1)

    at = click(editor.tool_buttons["highlighter"], caption, to_frame(arrow[-1]))
    at = click(editor.swatches[2], caption, at)  # yellow
    mark = [image_to_vp(18 + t * 12, 30) for t in range(20)]  # across the title
    move_cursor(at, to_frame(mark[0]), caption, kind="brush")
    stroke(mark, caption)

    caption = "Hold right-click to erase a whole stroke"
    wipe = [image_to_vp(470 + t * 4, 100 + t * 2.5) for t in range(16)]  # through the top of the circle
    move_cursor(to_frame(mark[-1]), to_frame(wipe[0]), caption, kind="brush")
    editor.canvas.update_cursor("eraser")
    stroke(wipe, caption, button=Qt.RightButton)
    layers, _ = editor_layers()
    rec.frame(layers, ("brush", to_frame(wipe[-1]), brush()), caption=caption, repeat=10)

    caption = "Changed your mind? Ctrl+Z"
    layers, _ = editor_layers()
    rec.frame(layers, ("arrow", to_frame(wipe[-1])), badge=["Ctrl", "Z"], caption=caption, repeat=6)
    editor.canvas.undo_stack.undo()
    editor.copy_to_clipboard()
    layers, _ = editor_layers()
    rec.frame(layers, ("arrow", to_frame(wipe[-1])), badge=["Ctrl", "Z"], caption=caption, repeat=14)

    caption = "Everything you draw is already on your clipboard"
    editor.copy_to_clipboard()
    layers, _ = editor_layers()
    rec.frame(layers, ("arrow", to_frame(wipe[-1])), caption=caption, repeat=40)

    # --- encode ------------------------------------------------------------------
    tmp = Path(tempfile.mkdtemp(prefix="klipp-demo-"))
    for i, img in enumerate(rec.frames):
        img.save(str(tmp / f"f{i:04d}.png"))
    gif = DOCS / "demo.gif"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(tmp / "f%04d.png"),
        "-vf", f"scale={OUT_WIDTH}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
               "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
        "-loop", "0", str(gif),
    ], check=True)
    shutil.rmtree(tmp)
    print(f"{gif}: {len(rec.frames)} frames, {len(rec.frames) / FPS:.1f}s, {gif.stat().st_size / 1e6:.1f} MB")

    # --- settings screenshot -----------------------------------------------------
    dialog = SettingsDialog(config)
    dialog.folder.setPlaceholderText(r"C:\Users\you\Pictures\Klipp")
    off_screen(dialog)
    dialog.grab().save(str(DOCS / "settings.png"))


if __name__ == "__main__":
    main()
