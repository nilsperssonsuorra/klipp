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

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
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

from klipp import hotkeys, icons
from klipp.config import DEFAULTS, Config
from klipp.overlay import SelectionOverlay
from klipp.settings import SettingsDialog

W, H = 1280, 720  # logical size of the staged desktop
FPS = 20
OUT_WIDTH = 1280
DOCS = ROOT / "docs"


def font(size, weight=QFont.Normal):
    f = QFont("Segoe UI", size)
    f.setWeight(weight)
    return f


# --- staged desktop ---------------------------------------------------------------

TERMINAL = QRectF(24, 40, 800, 470)
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
    ("ConnectionError: timed out after 30s", "#ff6b6b"),
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


def draw_composer(p, focused=False, attachment=None, text="", d=1.0):
    """The chat's message box. After a paste it grows to show the image as an attachment."""
    box = QRectF(CHAT_INPUT)
    if attachment is not None:
        box.setTop(box.top() - 138)
    p.setPen(QPen(QColor("#2f7bff"), 1.5) if focused else Qt.NoPen)
    p.setBrush(QColor("#383a40"))
    p.drawRoundedRect(box, 8, 8)
    if attachment is not None:
        tile = QRectF(box.left() + 14, box.top() + 14, 190, 118)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2b2d31"))
        p.drawRoundedRect(tile, 6, 6)
        area = tile.adjusted(8, 8, -8, -26)
        scale = min(area.width() / (attachment.width() / d), area.height() / (attachment.height() / d))
        w, h = attachment.width() / d * scale, attachment.height() / d * scale
        p.drawPixmap(QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h),
                     attachment, QRectF(attachment.rect()))
        p.setPen(QColor("#b5bac1"))
        p.setFont(font(8))
        p.drawText(QRectF(tile.left() + 10, tile.bottom() - 24, tile.width() - 20, 20), Qt.AlignVCenter, "image.png")
    line = QRectF(CHAT_INPUT.left() + 16, CHAT_INPUT.top(), CHAT_INPUT.width() - 32, CHAT_INPUT.height())
    p.setFont(font(10))
    if text:
        p.setPen(QColor("#dbdee1"))
        p.drawText(line, Qt.AlignVCenter, text)
    else:
        p.setPen(QColor("#80848e"))
        p.drawText(line, Qt.AlignVCenter, "Message #deploys")
    if focused:
        x = line.left() + (QFontMetrics(font(10)).horizontalAdvance(text) + 1 if text else 0)
        p.setPen(QPen(QColor("#dbdee1"), 1.4))
        p.drawLine(QPointF(x, line.center().y() - 9), QPointF(x, line.center().y() + 9))


def paint_desktop(d, posted=None, composer=None, posted_text="getting this, any idea?"):
    """The staged desktop: a terminal with a failed deploy next to a team chat.
    `posted` is the annotated capture, shown as a new chat message after sending.
    `composer` holds the message box state: focused, attachment, text."""
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
    x, baseline = TERMINAL.left() + 56, TERMINAL.top() + 38 + 34
    for text, color in TERMINAL_LINES:
        p.setPen(QColor(color))
        p.drawText(QPointF(x, baseline), text)
        indent = metrics.horizontalAdvance(text[: len(text) - len(text.lstrip())])
        LINE_RECTS.append(QRectF(x + indent, baseline - metrics.ascent(),
                                 metrics.horizontalAdvance(text.strip()), metrics.height()))
        baseline += 40
    prompt_baseline = LINE_RECTS[-1].top() + metrics.ascent()
    p.fillRect(QRectF(LINE_RECTS[-1].right() + 2, prompt_baseline - metrics.capHeight() - 1, 10,
                      metrics.capHeight() + 3), QColor("#c9ccd3"))

    # Team chat.
    draw_window(p, CHAT, "#deploys  ·  Team chat", "#313338", "#2b2d31", "#dbdee1")
    y = CHAT.top() + 38 + 22
    y = chat_message(p, y, "Sam", "#f0883e", ["is the prod deploy done?", "customers keep asking about the new checkout"])
    if posted is not None:
        chat_message(p, y, "You", "#2f7bff", [posted_text], posted, d)
    global CHAT_INPUT
    CHAT_INPUT = QRectF(CHAT.left() + 16, CHAT.bottom() - 66, CHAT.width() - 32, 48)
    draw_composer(p, d=d, **(composer or {}))
    p.end()
    return pm


# --- frame helpers ----------------------------------------------------------------


class Recorder:
    def __init__(self, d):
        self.d = d
        self.frames = []

    def frame(self, layers, cursor=None, badge=None, caption=None, repeat=1, extra=None):
        img = QImage(int(W * self.d), int(H * self.d), QImage.Format_RGB32)
        img.setDevicePixelRatio(self.d)
        p = QPainter(img)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        for pm, pos in layers:
            p.drawPixmap(QPointF(*pos), pm)
        if extra:
            extra(p)  # e.g. the stopwatch or the end card
        if caption:
            draw_caption(p, caption)
        if badge:
            draw_badge(p, badge)
        if cursor:
            draw_cursor(p, *cursor)
        p.end()
        self.frames.extend([img] * repeat)


def draw_badge(p, badge):
    if isinstance(badge, dict):
        draw_mouse(p, badge["button"], badge["text"])
    else:
        draw_keys(p, badge)


def draw_mouse(p, button, text):
    """Pill at the bottom with a mouse whose `button` side is held down."""
    p.setFont(font(16, QFont.DemiBold))
    tw = QFontMetrics(p.font()).horizontalAdvance(text)
    total = 36 + 30 + 14 + tw + 22
    rect = QRectF((W - total) / 2, H - 140, total, 70)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(15, 16, 20, 225))
    p.drawRoundedRect(rect, 16, 16)
    body = QRectF(rect.left() + 22, rect.top() + 12, 30, 46)
    half = QRectF(body.center().x() if button == "right" else body.left(), body.top(), body.width() / 2, 20)
    shape = QPainterPath()
    shape.addRoundedRect(body, 15, 15)
    p.save()
    p.setClipPath(shape)
    p.fillRect(half, QColor("#2f7bff"))
    p.restore()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor("#e8e8e8"), 2))
    p.drawRoundedRect(body, 15, 15)
    p.drawLine(QPointF(body.center().x(), body.top()), QPointF(body.center().x(), body.top() + 20))
    p.drawLine(QPointF(body.left(), body.top() + 20), QPointF(body.right(), body.top() + 20))
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(body.right() + 14, rect.top(), tw + 10, rect.height()), Qt.AlignVCenter, text)


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
    elif kind == "ibeam":
        for color, width in ((QColor(0, 0, 0, 200), 3.2), (QColor("#ffffff"), 1.4)):
            p.setPen(QPen(color, width))
            p.drawLine(QPointF(x, y - 9), QPointF(x, y + 9))
            p.drawLine(QPointF(x - 4, y - 10), QPointF(x + 4, y - 10))
            p.drawLine(QPointF(x - 4, y + 10), QPointF(x + 4, y + 10))
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


def select_area(rec, overlay, desktop, keys, start, end, cursor):
    """Hotkey badge, frozen overlay, drag a selection."""
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


def phrase_rect(line, phrase):
    """Where `phrase` sits on terminal line `line` (monospace, so it's simple arithmetic)."""
    text = TERMINAL_LINES[line][0].strip()
    metrics = QFontMetrics(QFont("Consolas", 14))
    rect = LINE_RECTS[line]
    left = rect.left() + metrics.horizontalAdvance(text[: text.index(phrase)])
    return QRectF(left, rect.top(), metrics.horizontalAdvance(phrase), rect.height())


def glyphs(line):
    """The ink of terminal line `line`: from cap height to descender, not the full line box."""
    metrics = QFontMetrics(QFont("Consolas", 14))
    rect = LINE_RECTS[line]
    baseline = rect.top() + metrics.ascent()
    return QRectF(rect.left(), baseline - metrics.capHeight(), rect.width(), metrics.capHeight() + metrics.descent())


def hand_circle(rect, points=34):
    """A loop around `rect` that looks drawn by hand: roomy enough that the text's corners
    sit well inside it, slightly tilted and wobbly, and finishing a little past where it began."""
    cx, cy = rect.center().x(), rect.center().y()
    rx, ry = rect.width() / 2 + 34, rect.height() / 2 + 12
    tilt = -math.atan2(3, rx)  # the ends sit ~3 px off level, however wide the loop is
    out = []
    for t in range(points):
        f = t / (points - 1)
        a = math.radians(-115 + 390 * f)  # start near the top, go round once and a bit more
        wobble = math.sin(f * math.pi * 3)
        # Clearly hand-made (so the snap to a clean ellipse is visible), finishing outside its start.
        x = (rx + 14 * f + 7 * wobble) * math.cos(a)
        y = (ry + 3 * f + 3 * wobble) * math.sin(a)
        out.append((cx + x * math.cos(tilt) - y * math.sin(tilt), cy + x * math.sin(tilt) + y * math.cos(tilt)))
    return out


def draw_stopwatch(p, seconds, done=False):
    """Small timer in the top-right corner: runs from the hotkey until the paste."""
    color = QColor("#5ad17a") if done else QColor("#ffffff")
    rect = QRectF(W - 150, 14, 132, 44)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(15, 16, 20, 225))
    p.drawRoundedRect(rect, 22, 22)
    c = QPointF(rect.left() + 26, rect.center().y() + 1)
    p.setPen(QPen(color, 2.2))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(c, 10, 10)
    p.drawLine(QPointF(c.x(), c.y() - 13), QPointF(c.x(), c.y() - 16))
    angle = math.radians(-90 + 360 * (seconds % 1))
    p.drawLine(c, QPointF(c.x() + 7 * math.cos(angle), c.y() + 7 * math.sin(angle)))
    p.setPen(color)
    p.setFont(font(16, QFont.DemiBold))
    p.drawText(QRectF(rect.left() + 44, rect.top(), 80, rect.height()), Qt.AlignVCenter, f"{seconds:.1f} s")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Klipp")
    d = QGuiApplication.primaryScreen().devicePixelRatio()
    DOCS.mkdir(exist_ok=True)
    rec = Recorder(d)
    clock = {"start": None, "stopped": None}

    def elapsed():
        if clock["start"] is None:
            return None
        end = clock["stopped"] if clock["stopped"] is not None else len(rec.frames)
        return (end - clock["start"]) / FPS

    def timer_layer(p):
        seconds = elapsed()
        if seconds is not None:
            draw_stopwatch(p, seconds, done=clock["stopped"] is not None)

    def shoot(layers, cursor=None, badge=None, repeat=1):
        for _ in range(repeat):
            rec.frame(layers, cursor, badge=badge, extra=timer_layer)

    # 0) You're writing in the team chat when the deploy fails.
    message = "deploy failed again, look:"
    typing_at = (CHAT_INPUT.right() - 50, CHAT_INPUT.center().y())
    for n in range(0, len(message) + 1):
        shoot([(paint_desktop(d, composer={"focused": True, "text": message[:n]}), (0, 0))], ("ibeam", typing_at))
    desktop = paint_desktop(d, composer={"focused": True, "text": message})
    shoot([(desktop, (0, 0))], ("ibeam", typing_at), repeat=12)  # notices the error

    # 1) Alt+Shift+S: the stopwatch starts and the screen freezes.
    config = Config(json.loads(json.dumps(DEFAULTS)))
    config.save = lambda keys: None
    config["snap_shapes"] = False  # quick hand-drawn marks, released straight away
    overlay = SelectionOverlay(QGuiApplication.primaryScreen(), desktop, 45, True, draw_config=config)
    overlay.setGeometry(0, 0, W, H)
    off_screen(overlay)
    result = {}
    overlay.annotated.connect(lambda image, rect, action: result.update(image=image))
    keys = ["Alt", "Shift", "S"]
    clock["start"] = len(rec.frames)
    shoot([(overlay.grab(), (0, 0))], ("ibeam", typing_at), badge=keys, repeat=6)

    def frame(cursor, badge=None, repeat=1):
        shoot([(overlay.grab(), (0, 0))], cursor, badge=badge, repeat=repeat)

    start = (TERMINAL.left() + 4, LINE_RECTS[3].top() - 8)
    # Extra room on the right, inside the capture, for the arrow.
    end = (max(r.right() for r in LINE_RECTS[3:9]) + 190, LINE_RECTS[8].bottom() + 21)
    for i in range(10):
        pos = lerp(typing_at, start, (i + 1) / 10)
        send_mouse(overlay, QEvent.MouseMove, pos)
        frame(("cross", pos), badge=keys if i < 5 else None)
    send_mouse(overlay, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    for i in range(14):
        pos = lerp(start, end, (i + 1) / 14)
        send_mouse(overlay, QEvent.MouseMove, pos, Qt.NoButton, Qt.LeftButton)
        frame(("cross", pos))
    send_mouse(overlay, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
    QApplication.processEvents()
    canvas, panel = overlay.canvas, overlay.toolbar.panel
    vp = canvas.viewport()

    def brush():
        return canvas.size_for(canvas.tool) * canvas.zoom

    def cursor_for(pos):
        return ("brush", pos, brush()) if canvas.tool in ("pen", "highlighter") else ("cross", pos)

    def stroke(points, every=1):
        send_mouse(vp, QEvent.MouseButtonPress, points[0], Qt.LeftButton, Qt.LeftButton)
        for i, pt in enumerate(points[1:], 1):
            send_mouse(vp, QEvent.MouseMove, pt, Qt.NoButton, Qt.LeftButton)
            if i % every == 0 or i == len(points) - 1:
                frame(cursor_for(pt))
        send_mouse(vp, QEvent.MouseButtonRelease, points[-1], Qt.LeftButton, Qt.NoButton)
        return points[-1]

    def move(a, b, frames, kind=None):
        for i in range(frames):
            pos = lerp(a, b, (i + 1) / frames)
            frame((kind, pos) if kind else cursor_for(pos))
        return b

    def click(button, at, frames=5):
        center = button.mapTo(overlay, button.rect().center())
        target = (center.x(), center.y())
        move(at, target, frames, kind="arrow")
        button.click()
        frame(("arrow", target), repeat=2)
        return target

    # 2) Circle the call that failed, point at the timeout, and just type a note.
    loop = hand_circle(glyphs(5), points=26)
    frame(("cross", end), repeat=3)
    at = move(end, loop[0], 7)
    at = stroke(loop)
    frame(cursor_for(at), repeat=4)
    at = click(panel.tool_buttons["arrow"], at)
    timeout = phrase_rect(7, "timeout=30")
    tip = (timeout.right() + 8, timeout.center().y())
    tail = (tip[0] + 150, tip[1] - 34)
    at = move(at, tail, 7, kind="cross")
    at = stroke([lerp(tail, tip, t / 14) for t in range(15)])
    frame(("cross", at), repeat=4)
    # Point just left of where the arrow starts and type: the label appears at the pointer.
    note_at = (tail[0] - 118, tail[1] - 30)
    at = move(at, note_at, 6, kind="arrow")
    note = "too low?"
    label = canvas.start_text(QPointF(note_at[0] * d, note_at[1] * d), note[0])  # scene = screenshot px
    frame(("arrow", at), repeat=2)
    for ch in note[1:]:
        cursor = label.textCursor()
        cursor.insertText(ch)
        label.setTextCursor(cursor)
        frame(("arrow", at), repeat=2)
    canvas.commit_text()
    frame(("arrow", at), badge=["Enter"], repeat=4)
    frame(("arrow", at), repeat=4)

    # 3) Ctrl+V: the stopwatch stops, the capture is in the message box.
    frame(("arrow", at), badge=["Ctrl", "V"], repeat=4)
    overlay._done("paste")
    clock["stopped"] = len(rec.frames)
    posted = QPixmap.fromImage(result["image"])
    posted.setDevicePixelRatio(d)

    def chat(repeat=1, badge=None, **composer):
        sent = composer.pop("posted", None)
        layer = paint_desktop(d, sent, composer, posted_text=message)
        shoot([(layer, (0, 0))], ("arrow", at), badge=badge, repeat=repeat)

    chat(10, badge=["Ctrl", "V"], focused=True, attachment=posted, text=message)
    chat(14, focused=True, attachment=posted, text=message)
    chat(8, badge=["Enter"], focused=True, attachment=posted, text=message)
    chat(70, focused=True, posted=posted)  # end on the result, then loop

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
    # The real Settings window releases Klipp's own hotkeys first; a running Klipp would
    # otherwise show them as taken here.
    hotkeys.is_available = lambda text: True
    dialog = SettingsDialog(config)
    dialog.folder.setPlaceholderText(r"C:\Users\you\Pictures\Klipp")
    off_screen(dialog)
    dialog.grab().save(str(DOCS / "settings.png"))


if __name__ == "__main__":
    main()
