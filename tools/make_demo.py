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
from klipp.config import DEFAULTS, Config
from klipp.editor import EditorWindow
from klipp.overlay import SelectionOverlay
from klipp.settings import SettingsDialog

W, H = 1280, 720  # logical size of the staged desktop
FPS = 20
OUT_WIDTH = 1200
DOCS = ROOT / "docs"


def font(size, weight=QFont.Normal):
    f = QFont("Segoe UI", size)
    f.setWeight(weight)
    return f


# --- staged desktop ---------------------------------------------------------------

TERMINAL = QRectF(24, 40, 800, 400)
CHAT = QRectF(848, 40, 408, 640)
TERMINAL_LINES = [
    ("PS C:\\projects\\shop> python deploy.py --env prod", "#8ae28a"),
    ("Building frontend... done (4.2s)", "#c9ccd3"),
    ("Uploading 214 files to api.internal", "#c9ccd3"),
    ("Traceback (most recent call last):", "#c9ccd3"),
    ('  File "deploy.py", line 42, in <module>', "#8f96a3"),
    ("    upload(build_dir)", "#c9ccd3"),
    ('  File "deploy.py", line 17, in upload', "#8f96a3"),
    ("    client.put(path, data, timeout=30)", "#c9ccd3"),
    ("ConnectionError: timed out after 30s (host: api.internal)", "#ff6b6b"),
    ("PS C:\\projects\\shop> ", "#8ae28a"),
]
LINE_RECTS = []  # visible text of each terminal line, filled in by paint_desktop
CHAT_INPUT = QRectF()


def draw_window(p, rect, title, body, bar, text):
    p.setPen(Qt.NoPen)
    for i in range(14, 0, -2):
        p.setBrush(QColor(0, 0, 0, 9))
        p.drawRoundedRect(rect.adjusted(-i, -i + 8, i, i + 8), 10 + i, 10 + i)
    p.setBrush(QColor(body))
    p.drawRoundedRect(rect, 10, 10)
    title_bar = QRectF(rect.left(), rect.top(), rect.width(), 38)
    path = QPainterPath()
    path.addRoundedRect(title_bar, 10, 10)
    path.addRect(title_bar.adjusted(0, 19, 0, 0))
    p.fillPath(path.simplified(), QColor(bar))
    p.setPen(QColor(text))
    p.setFont(font(9))
    p.drawText(title_bar.adjusted(16, 0, 0, 0), Qt.AlignVCenter, title)
    for i, glyph in enumerate(("—", "☐", "✕")):
        p.drawText(QRectF(rect.right() - 138 + i * 46, rect.top(), 46, 38), Qt.AlignCenter, glyph)


def chat_message(p, y, name, color, lines, image=None, d=1.0):
    left = CHAT.left() + 18
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(left, y, 36, 36))
    p.setPen(QColor("#ffffff"))
    p.setFont(font(10, QFont.DemiBold))
    p.drawText(QRectF(left, y, 36, 36), Qt.AlignCenter, name[0])
    p.drawText(QPointF(left + 50, y + 14), name)
    p.setPen(QColor("#949ba4"))
    p.setFont(font(8))
    p.drawText(QPointF(left + 52 + QFontMetrics(font(10, QFont.DemiBold)).horizontalAdvance(name), y + 14), "10:21")
    p.setPen(QColor("#dbdee1"))
    p.setFont(font(10))
    ty = y + 36
    for line in lines:
        p.drawText(QPointF(left + 50, ty), line)
        ty += 22
    if image is not None:
        w = min(CHAT.width() - 86, image.width() / d)
        h = image.height() / d * w / (image.width() / d)
        target = QRectF(left + 50, ty - 12, w, h)
        clip = QPainterPath()
        clip.addRoundedRect(target, 8, 8)
        p.save()
        p.setClipPath(clip)
        p.drawPixmap(target, image, QRectF(image.rect()))
        p.restore()
        ty += h + 4
    return ty + 14


def paint_desktop(d, posted=None):
    """The staged desktop: a terminal with a failed deploy next to a team chat.
    `posted` is the annotated capture, shown as a new chat message after the paste."""
    pm = QPixmap(int(W * d), int(H * d))
    pm.setDevicePixelRatio(d)
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)

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

    # Terminal with a failed deploy.
    draw_window(p, TERMINAL, "Windows PowerShell", "#1b1c21", "#26272d", "#c9ccd3")
    mono = QFont("Consolas", 14)
    metrics = QFontMetrics(mono)
    p.setFont(mono)
    LINE_RECTS.clear()
    x, baseline = TERMINAL.left() + 22, TERMINAL.top() + 38 + 34
    for text, color in TERMINAL_LINES:
        p.setPen(QColor(color))
        p.drawText(QPointF(x, baseline), text)
        indent = metrics.horizontalAdvance(text[: len(text) - len(text.lstrip())])
        LINE_RECTS.append(QRectF(x + indent, baseline - metrics.ascent(),
                                 metrics.horizontalAdvance(text.strip()), metrics.height()))
        baseline += 31
    p.fillRect(QRectF(LINE_RECTS[-1].right() + 2, LINE_RECTS[-1].top() + 2, 10, metrics.height() - 4),
               QColor("#c9ccd3"))

    # Team chat.
    draw_window(p, CHAT, "#deploys  ·  Team chat", "#313338", "#2b2d31", "#dbdee1")
    y = CHAT.top() + 38 + 22
    y = chat_message(p, y, "Sam", "#f0883e", ["is the prod deploy done?", "customers keep asking about the new checkout"])
    if posted is not None:
        chat_message(p, y, "You", "#2f7bff", ["getting this, any idea?"], posted, d)
    global CHAT_INPUT
    CHAT_INPUT = QRectF(CHAT.left() + 16, CHAT.bottom() - 66, CHAT.width() - 32, 48)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#383a40"))
    p.drawRoundedRect(CHAT_INPUT, 8, 8)
    p.setPen(QColor("#80848e"))
    p.setFont(font(10))
    p.drawText(CHAT_INPUT.adjusted(16, 0, 0, 0), Qt.AlignVCenter, "Message #deploys")
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
    rect = QRectF((W - w) / 2, 6, w, 46)
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


def select_area(rec, desktop, keys, start, end, cursor):
    """Hotkey badge, frozen overlay, drag a selection. Returns the cropped capture."""
    overlay = SelectionOverlay(QGuiApplication.primaryScreen(), desktop, 45, True)
    overlay.setGeometry(0, 0, W, H)
    off_screen(overlay)
    result = {}
    overlay.selected.connect(lambda crop, rect: result.update(crop=crop))

    rec.frame([(desktop, (0, 0))], ("arrow", cursor), badge=keys, repeat=6)
    for i in range(8):
        pos = lerp(cursor, start, (i + 1) / 8)
        send_mouse(overlay, QEvent.MouseMove, pos)
        rec.frame([(overlay.grab(), (0, 0))], ("cross", pos), badge=keys if i < 4 else None)
    send_mouse(overlay, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    for i in range(12):
        pos = lerp(start, end, (i + 1) / 12)
        send_mouse(overlay, QEvent.MouseMove, pos, Qt.NoButton, Qt.LeftButton)
        rec.frame([(overlay.grab(), (0, 0))], ("cross", pos))
    rec.frame([(overlay.grab(), (0, 0))], ("cross", end), repeat=3)
    send_mouse(overlay, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
    QApplication.processEvents()
    return result["crop"]


def ellipse_around(rect, pad_x, pad_y, points=26):
    cx, cy = rect.center().x(), rect.center().y()
    rx, ry = rect.width() / 2 + pad_x, rect.height() / 2 + pad_y
    return [(cx + rx * math.cos(a), cy + ry * math.sin(a))
            for a in (math.radians(-110 + t * 375 / (points - 1)) for t in range(points))]


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Klipp")
    d = QGuiApplication.primaryScreen().devicePixelRatio()
    DOCS.mkdir(exist_ok=True)
    desktop = paint_desktop(d)
    rec = Recorder(d)

    # Keep the demo away from the real clipboard.
    EditorWindow.copy_to_clipboard = lambda self: (self.copied_label.setText("✓ Copied"), self._flash.start())

    # 1) Alt+Shift+S and drag over the error.
    start = (TERMINAL.left() + 12, LINE_RECTS[3].top() - 4)
    end = (max(r.right() for r in LINE_RECTS[3:9]) + 20, LINE_RECTS[8].bottom() + 8)
    crop = select_area(rec, desktop, ["Alt", "Shift", "S"], start, end, cursor=(560, 640))

    config = Config(json.loads(json.dumps(DEFAULTS)))
    config.save = lambda keys: None
    editor = EditorWindow(crop, d, config)
    editor.resize(1120, int(crop.height() / d) + 66)
    off_screen(editor)
    editor.canvas.fit()
    QApplication.processEvents()
    vp = editor.canvas.viewport()

    def editor_layers():
        pm, (cx, cy) = window_chrome(editor.grab(), editor.windowTitle(), d)
        ex, ey = (W - pm.width() / d) / 2, (H - pm.height() / d) / 2 - 40
        return [(desktop, (0, 0)), (pm, (ex, ey))], (ex + cx, ey + cy)

    def to_frame(vp_pos):
        _, origin = editor_layers()
        off = vp.mapTo(editor, QPoint(0, 0))
        return (origin[0] + off.x() + vp_pos[0], origin[1] + off.y() + vp_pos[1])

    def to_vp(desktop_pos):
        """A point on the staged desktop -> the same spot in the editor's viewport."""
        x, y = desktop_pos[0] - start[0], desktop_pos[1] - start[1]
        pt = editor.canvas.mapFromScene(QPointF(x * d, y * d))
        return (pt.x(), pt.y())

    def frame(cursor, caption=None, badge=None, repeat=1):
        layers, _ = editor_layers()
        rec.frame(layers, cursor, badge=badge, caption=caption, repeat=repeat)

    def brush(tool=None):
        return editor.canvas.size_for(tool or editor.canvas.tool) * editor.canvas.zoom

    def stroke(points, caption=None, button=Qt.LeftButton, tool=None):
        points = [to_vp(pt) for pt in points]
        send_mouse(vp, QEvent.MouseButtonPress, points[0], button, button)
        for pt in points[1:]:
            send_mouse(vp, QEvent.MouseMove, pt, Qt.NoButton, button)
            frame(("brush", to_frame(pt), brush(tool)), caption)
        send_mouse(vp, QEvent.MouseButtonRelease, points[-1], button, Qt.NoButton)
        return to_frame(points[-1])

    def move(a, b, frames, caption=None, tool=None):
        for i in range(frames):
            frame(("brush", lerp(a, b, (i + 1) / frames), brush(tool)), caption)

    # 2) Circle the wrong line by mistake...
    frame(("arrow", end), repeat=3)
    wrong = ellipse_around(LINE_RECTS[5], 16, 9, points=22)
    move(end, to_frame(to_vp(wrong[0])), 6)
    at = stroke(wrong)

    # ...right-drag wipes the whole stroke...
    caption = "Wrong line? Right-drag erases the whole stroke"
    left_edge = (LINE_RECTS[5].left() - 16, LINE_RECTS[5].center().y())
    wipe = [(left_edge[0] - 6 + t * 2, left_edge[1] - 26 + t * 7) for t in range(9)]
    move(at, to_frame(to_vp(wipe[0])), 6, caption, tool="eraser")
    editor.canvas.update_cursor("eraser")
    at = stroke(wipe, caption, button=Qt.RightButton, tool="eraser")
    frame(("brush", at, brush("eraser")), caption, repeat=6)

    # ...and circle the real error.
    right = ellipse_around(LINE_RECTS[8], 18, 5, points=28)
    move(at, to_frame(to_vp(right[0])), 6)
    at = stroke(right)
    editor.copy_to_clipboard()
    frame(("arrow", at), repeat=10)

    # 3) Paste into the chat. The editor copied it already.
    target = (CHAT_INPUT.left() + 150, CHAT_INPUT.center().y())
    for i in range(10):
        rec.frame([(desktop, (0, 0))], ("arrow", lerp(at, target, (i + 1) / 10)))
    rec.frame([(desktop, (0, 0))], ("arrow", target), badge=["Ctrl", "V"], repeat=8)
    posted = QPixmap.fromImage(editor.canvas.render_image())
    posted.setDevicePixelRatio(d)
    after = paint_desktop(d, posted)
    rec.frame([(after, (0, 0))], ("arrow", target), caption="Pasted. It was already on your clipboard.", repeat=44)

    # --- encode ------------------------------------------------------------------
    tmp = Path(tempfile.mkdtemp(prefix="klipp-demo-"))
    for i, img in enumerate(rec.frames):
        img.save(str(tmp / f"f{i:04d}.png"))
    gif = DOCS / "demo.gif"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(tmp / "f%04d.png"),
        "-vf", f"scale={OUT_WIDTH}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];"
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
