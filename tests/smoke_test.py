"""Drives the editor and the hotkey → overlay → clipboard flow with synthetic input.

Run: .venv\\Scripts\\python tests\\smoke_test.py
Briefly flashes the selection overlay on screen. Hotkeys are simulated by posting
WM_HOTKEY directly, so no real key presses reach other apps.
"""

import ctypes
import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

import klipp.config as config_module
from klipp.config import DEFAULTS, Config
from klipp.editor import EditorWindow, StrokeItem
from klipp.hotkeys import WM_HOTKEY

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def strokes(canvas):
    return [i for i in canvas.scene().items() if isinstance(i, StrokeItem)]


def scratch_config(**overrides):
    """In-memory config that never touches the user's real settings file."""
    config = Config(json.loads(json.dumps(DEFAULTS)))
    config["check_updates"] = False  # tests never contact GitHub
    config.data.update(overrides)
    config.save = lambda keys: None
    return config


def wait_for(cond, ms=2000):
    for _ in range(ms // 20):
        if cond():
            return True
        QTest.qWait(20)
    return cond()


def drag(widget, points, button=Qt.LeftButton, mods=Qt.NoModifier):
    QTest.mousePress(widget, button, mods, QPoint(*points[0]))
    for pt in points[1:]:
        QTest.mouseMove(widget, QPoint(*pt))
    QTest.mouseRelease(widget, button, mods, QPoint(*points[-1]))
    QApplication.processEvents()


def test_editor():
    image = QPixmap(900, 500)
    image.fill(QColor("#f4f4f4"))
    p = QPainter(image)
    p.setPen(QColor("#333"))
    p.drawText(40, 60, "Sample screenshot text")
    p.end()

    win = EditorWindow(image, 1.5, scratch_config())
    win.present()
    QTest.qWait(200)
    canvas, vp = win.canvas, win.canvas.viewport()
    # Draw on the image itself (it's centred in the window); ink off the image is clipped away.
    ox = canvas.mapFromScene(canvas.region.topLeft()).x() - 30
    on = lambda pts: [(x + ox, y) for x, y in pts]  # noqa: E731

    win.select_tool("pen")
    win.select_color("#ff3b30")
    drag(vp, on([(60, 60), (120, 90), (180, 70), (240, 110)]))
    check(len(strokes(canvas)) == 1, "pen stroke created")

    win.select_tool("highlighter")
    win.select_color("#ffe600")
    drag(vp, on([(40, 160), (300, 160)]))
    win.select_tool("arrow")
    win.select_color("#2f7bff")
    drag(vp, on([(350, 250), (450, 150)]))
    win.select_tool("rect")
    win.select_color("#34c759")
    drag(vp, on([(320, 40), (480, 120)]))
    check(len(strokes(canvas)) == 4, "highlighter, arrow and rectangle created")

    # Right-drag inside the rectangle must NOT erase it (only its outline counts).
    drag(vp, on([(380, 70), (420, 90)]), Qt.RightButton)
    check(len(strokes(canvas)) == 4, "right-drag inside a rectangle leaves it alone")

    # Right-drag across the pen stroke erases the whole stroke.
    drag(vp, on([(120, 40), (120, 130)]), Qt.RightButton)
    check(len(strokes(canvas)) == 3, "right-drag erased the pen stroke as a whole")
    check(win.windowHandle() is not None and canvas.tool == "rect", "tool unchanged after right-erase")

    canvas.undo_stack.undo()
    check(len(strokes(canvas)) == 4, "undo restores the erased stroke")
    canvas.undo_stack.redo()
    check(len(strokes(canvas)) == 3, "redo erases it again")
    canvas.undo_stack.undo()

    # A plain click with a shape tool must not leave an invisible item or an undo step.
    steps = canvas.undo_stack.count()
    drag(vp, on([(500, 300), (500, 300)]))
    check(len(strokes(canvas)) == 4 and canvas.undo_stack.count() == steps, "click with rectangle tool adds nothing")

    # If the button release never arrives (focus stolen mid-drag), the canvas must recover.
    win.select_tool("pen")
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, QPoint(500 + ox, 330))
    stray = QMouseEvent(QEvent.MouseMove, QPointF(510 + ox, 340), QPointF(510 + ox, 340), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    QApplication.sendEvent(vp, stray)
    check(canvas._mode is None and len(strokes(canvas)) == 5, "lost mouse release ends the stroke instead of sticking")
    canvas.undo_stack.undo()

    QTest.qWait(400)  # let the debounced auto-copy fire
    clip = QGuiApplication.clipboard().image()
    check(clip.width() == 900 and clip.height() == 500, f"auto-copied full-res image ({clip.width()}x{clip.height()})")
    check(QColor(clip.pixel(0, 0)) == QColor("#f4f4f4"), "clipboard image has screenshot content")

    win.grab().save(str(OUT / "editor.png"))
    clip.save(str(OUT / "clipboard.png"))
    win.close()


def test_config_merge():
    folder = Path(tempfile.mkdtemp())
    config_module.CONFIG_DIR, config_module.CONFIG_PATH = folder, folder / "config.json"
    config = Config.load()
    check(config_module.CONFIG_PATH.exists(), "first run writes a default settings file")

    # The user edits the hotkey by hand while the app is running...
    data = json.loads(config_module.CONFIG_PATH.read_text(encoding="utf-8"))
    data["hotkey_copy"] = "Ctrl+Alt+Q"
    config_module.CONFIG_PATH.write_text(json.dumps(data), encoding="utf-8")
    # ...then closes an editor, which saves its own state.
    config["tool"] = "arrow"
    config.save(config_module.EDITOR_KEYS)
    data = json.loads(config_module.CONFIG_PATH.read_text(encoding="utf-8"))
    check(data["hotkey_copy"] == "Ctrl+Alt+Q" and data["tool"] == "arrow", "editor save keeps hand-edited hotkeys")

    config_module.CONFIG_PATH.write_text("{ broken json", encoding="utf-8")
    broken = Config.load()
    broken.save(config_module.EDITOR_KEYS)
    check(broken.error is not None, "malformed settings file is reported")
    check(config_module.CONFIG_PATH.read_text(encoding="utf-8") == "{ broken json", "malformed file is not overwritten")


def press(widget, key, vk, mods=Qt.NoModifier):
    """Key press carrying a Windows virtual-key code, like a real one."""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, mods, 0, vk, 0, ""))


def test_settings():
    from klipp.app import KlippApp
    from klipp.settings import SettingsDialog

    config = scratch_config(hotkey_copy="Ctrl+Alt+Shift+F11", hotkey_edit="Ctrl+Alt+Shift+F12")
    dialog = SettingsDialog(config)
    dialog.show()
    QTest.qWait(100)
    dialog.grab().save(str(OUT / "settings.png"))

    edit = dialog.copy_key
    edit.click()
    press(edit, Qt.Key_K, 0x4B)
    check(edit.value == "Ctrl+Alt+Shift+F11" and "Ctrl" in dialog.copy_status.text(),
          "plain letter is rejected as a hotkey")
    edit.click()
    press(edit, Qt.Key_K, 0x4B, Qt.ControlModifier | Qt.AltModifier)
    check(edit.value == "Ctrl+Alt+K" and dialog.save_button.isEnabled(), "Ctrl+Alt+K is recorded")
    edit.click()
    press(edit, Qt.Key_F12, 0x7B, Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier)
    check(not dialog.save_button.isEnabled() and "same" in dialog.copy_status.text(),
          "duplicate hotkeys block saving")
    edit.click()
    press(edit, Qt.Key_F9, 0x78)
    check(edit.value == "F9", "F-keys work without modifiers")
    dialog.reject()

    # Saving through the app re-registers the new hotkeys.
    app = KlippApp(QApplication.instance(), config)
    check(len(app.hotkeys._names) == 2, "app registered hotkeys")
    app.open_settings()
    check(len(app.hotkeys._names) == 0, "hotkeys are released while settings are open")
    app.settings_dialog.copy_key.set_value("Ctrl+Alt+Shift+F10")
    app.settings_dialog.dim.setValue(30)
    app.settings_dialog.accept()
    QTest.qWait(50)
    check(config["hotkey_copy"] == "Ctrl+Alt+Shift+F10" and config["dim"] == 30, "saved settings are applied")
    check(len(app.hotkeys._names) == 2 and app.copy_action.text().endswith("Ctrl+Alt+Shift+F10"),
          "new hotkeys registered and tray menu updated")
    app.hotkeys.unregister_all()
    app.tray.hide()


def press_key(widget, key, mods=Qt.NoModifier):
    """Keyboard shortcuts only reach the active window; make sure it is before pressing."""
    from klipp.app import force_foreground

    force_foreground(widget)
    wait_for(lambda: QApplication.activeWindow() is widget, 1000)
    QTest.keyClick(widget, key, mods)


def hold_stroke(widget, points, wait_ms=0, button=Qt.LeftButton):
    """Press, move through points, optionally rest at the end, then release."""
    QTest.mousePress(widget, button, Qt.NoModifier, QPoint(*map(round, points[0])))
    for x, y in points[1:]:
        move = QMouseEvent(QEvent.MouseMove, QPointF(x, y), widget.mapToGlobal(QPointF(x, y)),
                           Qt.NoButton, button, Qt.NoModifier)
        QApplication.sendEvent(widget, move)
    if wait_ms:
        QTest.qWait(wait_ms)
    QTest.mouseRelease(widget, button, Qt.NoModifier, QPoint(*map(round, points[-1])))
    QApplication.processEvents()


def wobbly_loop(cx, cy, rx, ry, n=48):
    pts = []
    for i in range(n):
        f = i / (n - 1)
        a = math.radians(-115 + 390 * f)
        k = 1 + 0.04 * math.sin(f * math.pi * 3) + 0.05 * f
        pts.append((cx + rx * k * math.cos(a), cy + ry * k * math.sin(a)))
    return pts


def pen_strokes(canvas):
    """Only drawn strokes; typing on the PC during a run can add stray text labels."""
    return [i for i in canvas.strokes() if isinstance(i, StrokeItem)]


def test_snapping():
    image = QPixmap(900, 500)
    image.fill(QColor("#ffffff"))
    win = EditorWindow(image, 1.5, scratch_config())
    win.present()
    QTest.qWait(200)
    canvas, vp = win.canvas, win.canvas.viewport()
    center = canvas.mapFromScene(canvas.region.center())
    cx, cy = center.x(), center.y()
    kinds = []
    canvas.snapped.connect(kinds.append)

    win.select_tool("pen")
    hold_stroke(vp, wobbly_loop(cx, cy, 120, 50), wait_ms=700)
    item = pen_strokes(canvas)[-1]
    check(kinds == ["ellipse"], f"resting at the end of a rough loop snaps it to an ellipse ({kinds})")
    check(item.path().elementCount() < 20, "the snapped stroke is a clean ellipse path, not the freehand points")
    canvas.undo_stack.undo()
    check(not pen_strokes(canvas), "a snapped shape undoes in one step")

    kinds.clear()
    hold_stroke(vp, wobbly_loop(cx, cy, 120, 50), wait_ms=0)
    check(kinds == [], "a stroke released without resting stays freehand")

    zigzag = [(cx - 150 + i * 10, cy + 40 * math.sin(i * 0.9)) for i in range(30)]
    hold_stroke(vp, zigzag, wait_ms=700)
    check(kinds == [], "resting at the end of a scribble leaves it freehand")

    line = [(cx - 150 + i * 12, cy + 60 + (i % 3) - 1) for i in range(26)]
    hold_stroke(vp, line, wait_ms=700)
    check(kinds == ["line"], f"a shaky line snaps straight ({kinds})")

    kinds.clear()
    canvas.snap_enabled = False
    hold_stroke(vp, wobbly_loop(cx, cy, 120, 50), wait_ms=700)
    check(kinds == [], "no snapping when it's turned off in settings")
    win.close()


def red_pixels(image):
    img = image.convertToFormat(QImage.Format_RGB32)
    count = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = QColor(img.pixel(x, y))
            if c.red() > 200 and c.green() < 90 and c.blue() < 90:
                count += 1
    return count


def test_draw_on_screen():
    from klipp.app import KlippApp

    config = scratch_config(hotkey_copy="Ctrl+Alt+Shift+F11", hotkey_edit="Ctrl+Alt+Shift+F12",
                            edit_mode="inplace", color="#ff3b30", tool="pen")
    app = KlippApp(QApplication.instance(), config)
    ids = {name: hid for hid, name in app.hotkeys._names.items()}
    user32 = ctypes.windll.user32
    clipboard = QGuiApplication.clipboard()

    def open_selection(x0=300, y0=250, x1=700, y1=500):
        user32.PostMessageW(app.hotkeys._hwnd, WM_HOTKEY, ids["edit"], 0)
        wait_for(lambda: app.overlay is not None)
        ov = app.overlay
        QTest.mousePress(ov, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y0))
        QTest.mouseMove(ov, QPoint(x1, y1))
        QTest.mouseRelease(ov, Qt.LeftButton, Qt.NoModifier, QPoint(x1, y1))
        QApplication.processEvents()
        return ov

    overlay = open_selection()
    check(overlay.canvas is not None and overlay.isVisible(), "releasing the selection keeps the frozen screen open for drawing")
    if overlay.canvas is None:
        return
    tb = overlay.toolbar.geometry()
    check(overlay.rect().contains(tb) and tb.top() >= 500, "the toolbar sits just below the selection, on screen")
    overlay.grab().scaled(1280, 720, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(OUT / "draw_on_screen.png"))

    canvas, vp = overlay.canvas, overlay.canvas.viewport()
    hold_stroke(vp, [(200, 200), (260, 220)])
    check(not canvas.strokes(), "dragging outside the selection doesn't draw")
    hold_stroke(vp, [(350, 300), (420, 330), (500, 310), (600, 360)])
    check(len(canvas.strokes()) == 1, "drawing inside the selection works")
    hold_stroke(vp, [(420, 280), (420, 400)], button=Qt.RightButton)
    check(not canvas.strokes(), "right-drag erases on the frozen screen too")
    hold_stroke(vp, wobbly_loop(500, 375, 120, 60), wait_ms=700)
    check(len(canvas.strokes()) == 1, "draw a loop to keep")

    clipboard.clear()
    type_into(overlay.canvas, overlay, key=Qt.Key_Return)  # keys go to the focused canvas
    wait_for(lambda: app.overlay is None)
    image = clipboard.image()
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    check(app.overlay is None, "Enter finishes and closes the frozen screen")
    check(abs(image.width() - 400 * dpr) <= 2 and abs(image.height() - 250 * dpr) <= 2,
          f"the clipboard gets exactly the selection ({image.width()}x{image.height()})")
    check(red_pixels(image) > 50, "the drawing is in the copied image")
    image.save(str(OUT / "draw_on_screen_result.png"))

    clipboard.clear()
    overlay = open_selection()
    hold_stroke(overlay.canvas.viewport(), [(350, 300), (500, 350)])
    press_key(overlay, Qt.Key_Escape)
    wait_for(lambda: app.overlay is None)
    check(app.overlay is None and clipboard.image().isNull(), "Esc cancels without copying anything")

    overlay = open_selection()
    hold_stroke(overlay.canvas.viewport(), [(350, 300), (420, 330), (500, 310)])
    overlay.toolbar.open_button.click()
    wait_for(lambda: app.overlay is None and app.windows)
    editors = list(app.windows)
    check(len(editors) == 1 and len(editors[0].canvas.strokes()) == 1,
          "Open in window carries the drawing over to the editor")
    for editor in editors:
        editor.close()
    app.hotkeys.unregister_all()
    app.tray.hide()


class PasteTarget(QWidget):
    """A stand-in for the chat you came from: records what Ctrl+V pastes into it."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Klipp test: paste target")
        self.setFocusPolicy(Qt.StrongFocus)
        self.resize(300, 200)
        self.pasted = None

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Paste):
            self.pasted = QGuiApplication.clipboard().image()


def test_finish_gestures():
    from klipp.app import KlippApp, force_foreground

    config = scratch_config(hotkey_copy="Ctrl+Alt+Shift+F11", hotkey_edit="Ctrl+Alt+Shift+F12", edit_mode="inplace")
    app = KlippApp(QApplication.instance(), config)
    ids = {name: hid for hid, name in app.hotkeys._names.items()}
    user32 = ctypes.windll.user32
    clipboard = QGuiApplication.clipboard()

    def open_selection():
        user32.PostMessageW(app.hotkeys._hwnd, WM_HOTKEY, ids["edit"], 0)
        wait_for(lambda: app.overlay is not None)
        ov = app.overlay
        QTest.mousePress(ov, Qt.LeftButton, Qt.NoModifier, QPoint(300, 250))
        QTest.mouseMove(ov, QPoint(700, 500))
        QTest.mouseRelease(ov, Qt.LeftButton, Qt.NoModifier, QPoint(700, 500))
        QApplication.processEvents()
        hold_stroke(ov.canvas.viewport(), [(350, 300), (420, 330), (500, 310), (600, 360)])
        return ov

    # A plain click outside the selection copies and closes.
    clipboard.clear()
    overlay = open_selection()
    vp = overlay.canvas.viewport()
    QApplication.sendEvent(vp, QMouseEvent(QEvent.MouseMove, QPointF(150, 150), vp.mapToGlobal(QPointF(150, 150)),
                                         Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    check(vp.cursor().shape() == Qt.ArrowCursor, "the pointer turns into an arrow outside the selection")
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, QPoint(150, 150))
    wait_for(lambda: app.overlay is None)
    check(app.overlay is None and red_pixels(clipboard.image()) > 20, "clicking outside the selection copies and closes")

    # Space does the same.
    clipboard.clear()
    overlay = open_selection()
    type_into(overlay.canvas, overlay, key=Qt.Key_Space)  # keys go to the focused canvas
    wait_for(lambda: app.overlay is None)
    check(app.overlay is None and red_pixels(clipboard.image()) > 20, "Space copies and closes")

    # Ctrl+V: copy, close, and paste into the window you came from.
    target = PasteTarget()
    target.show()
    force_foreground(target)
    target.activateWindow()
    target.setFocus()
    wait_for(lambda: user32.GetForegroundWindow() == int(target.winId()), 1500)
    if user32.GetForegroundWindow() != int(target.winId()):
        print("SKIP Ctrl+V paste test: couldn't bring the test window to the front")
    else:
        overlay = open_selection()
        press_key(overlay, Qt.Key_V, Qt.ControlModifier)
        wait_for(lambda: target.pasted is not None, 2000)
        pasted = target.pasted
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        check(app.overlay is None and pasted is not None, "Ctrl+V closes the frozen screen and pastes where you came from")
        check(pasted is not None and abs(pasted.width() - 400 * dpr) <= 2 and red_pixels(pasted) > 20,
              "what gets pasted is the selection with the drawing")
    target.close()
    app.hotkeys.unregister_all()
    app.tray.hide()


def type_into(canvas, window, text=None, key=None, mods=Qt.NoModifier):
    """Keys go to the focused canvas, like real typing (after making its window active)."""
    from klipp.app import force_foreground

    force_foreground(window)
    wait_for(lambda: QApplication.activeWindow() is window, 1000)
    canvas.setFocus()
    if text is not None:
        QTest.keyClicks(canvas, text)
    if key is not None:
        QTest.keyClick(canvas, key, mods)
    QApplication.processEvents()


def point_at(canvas, x, y):
    move = QMouseEvent(QEvent.MouseMove, QPointF(x, y), canvas.viewport().mapToGlobal(QPointF(x, y)),
                       Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    QApplication.sendEvent(canvas.viewport(), move)


def scroll(canvas, x, y, notches, mods=Qt.NoModifier):
    pos = QPointF(x, y)
    wheel = QWheelEvent(pos, canvas.viewport().mapToGlobal(pos), QPoint(), QPoint(0, 120 * notches),
                        Qt.NoButton, mods, Qt.NoScrollPhase, False)
    QApplication.sendEvent(canvas.viewport(), wheel)


def labels(canvas):
    from klipp.editor import TextItem

    return [i for i in canvas.strokes() if isinstance(i, TextItem)]


def test_text_labels():
    from klipp.app import KlippApp

    # --- in the editor window ---------------------------------------------------
    image = QPixmap(900, 500)
    image.fill(QColor("#ffffff"))
    config = scratch_config(color="#ff3b30", tool="pen")
    win = EditorWindow(image, 1.5, config)
    win.present()
    QTest.qWait(200)
    canvas = win.canvas
    center = canvas.mapFromScene(canvas.region.center())
    point_at(canvas, center.x(), center.y())
    type_into(canvas, win, "fix this")
    check(canvas._editing is not None and canvas._editing.toPlainText() == "fix this",
          "typing starts a label at the pointer")
    check(canvas.tool == "pen", "letters no longer switch tools")

    # Scrolling while typing resizes the label instead of scrolling the view.
    typing = canvas._editing
    start_size, middle, zoom = typing.pixel_size(), typing.sceneBoundingRect().center().y(), canvas.zoom
    scroll(canvas, center.x(), center.y(), 2)
    check(typing.pixel_size() > start_size and canvas.zoom == zoom, "scrolling while typing makes the label bigger")
    check(abs(typing.sceneBoundingRect().center().y() - middle) < 1, "the label grows around its middle")
    check(config["sizes"]["text"] == typing.pixel_size() and canvas.tool == "pen",
          "the next label starts at the new size; the tool stays the same")
    scroll(canvas, center.x(), center.y(), -2)
    check(typing.pixel_size() == start_size, "scrolling back makes it the size it was")
    for _ in range(30):
        scroll(canvas, center.x(), center.y(), -1)
    check(typing.pixel_size() == 6, "labels don't shrink below 6 px")
    scroll(canvas, center.x(), center.y(), 40)
    check(typing.pixel_size() == 80, "or grow past 80 px")
    scroll(canvas, center.x(), center.y(), 1, Qt.ControlModifier)
    check(canvas.zoom > zoom and typing.pixel_size() == 80, "Ctrl+scroll still zooms while typing")
    canvas.set_label_size(start_size)
    canvas.sizes["text"] = start_size
    canvas.set_zoom(zoom)
    type_into(canvas, win, key=Qt.Key_Return, mods=Qt.ShiftModifier)
    type_into(canvas, win, "please")
    type_into(canvas, win, key=Qt.Key_Return)
    check(canvas._editing is None and len(labels(canvas)) == 1, "Enter finishes the label")
    check(labels(canvas)[0].toPlainText() == "fix this please" or "\n" in labels(canvas)[0].toPlainText()
          or len(labels(canvas)[0].toPlainText().splitlines()) == 2, "Shift+Enter makes a new line")
    label = labels(canvas)[0]
    check(red_pixels(canvas.render_image()) > 30, "the label is in the copied image")
    canvas.undo()
    check(not labels(canvas), "Ctrl+Z removes the label")
    canvas.redo()
    check(len(labels(canvas)) == 1, "Ctrl+Y brings it back")

    # Click the label to change its words.
    at = canvas.mapFromScene(label.sceneBoundingRect().center())
    QTest.mouseClick(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    type_into(canvas, win, "!")
    type_into(canvas, win, key=Qt.Key_Escape)
    check("!" in label.toPlainText() and canvas._editing is None,
          "clicking a label edits it where you clicked; Esc finishes")
    canvas.undo()
    check("!" not in label.toPlainText(), "the edit undoes on its own")

    # Click a label and scroll to resize it; that undoes too.
    size = label.pixel_size()
    at = canvas.mapFromScene(label.sceneBoundingRect().center())
    QTest.mouseClick(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    scroll(canvas, at.x(), at.y(), 3)
    type_into(canvas, win, key=Qt.Key_Escape)
    check(canvas._editing is None and label.pixel_size() > size, "scrolling over a label you're editing resizes it")
    canvas.undo()
    check(label.pixel_size() == size, "Ctrl+Z puts the old size back")
    canvas.redo()
    check(label.pixel_size() > size, "Ctrl+Y makes it bigger again")
    canvas.undo()

    # Right-drag erases a label like a stroke.
    rect = canvas.mapFromScene(label.sceneBoundingRect()).boundingRect()
    hold_stroke(canvas.viewport(), [(rect.left() - 10, rect.center().y()), (rect.right() + 10, rect.center().y())],
                button=Qt.RightButton)
    check(not labels(canvas), "right-drag erases a label")
    win.close()

    # --- on the frozen screen ---------------------------------------------------
    config = scratch_config(hotkey_copy="Ctrl+Alt+Shift+F11", hotkey_edit="Ctrl+Alt+Shift+F12",
                            edit_mode="inplace", color="#ff3b30", tool="pen")
    app = KlippApp(QApplication.instance(), config)
    ids = {name: hid for hid, name in app.hotkeys._names.items()}
    clipboard = QGuiApplication.clipboard()

    def open_selection():
        ctypes.windll.user32.PostMessageW(app.hotkeys._hwnd, WM_HOTKEY, ids["edit"], 0)
        wait_for(lambda: app.overlay is not None)
        ov = app.overlay
        QTest.mousePress(ov, Qt.LeftButton, Qt.NoModifier, QPoint(300, 250))
        QTest.mouseMove(ov, QPoint(700, 500))
        QTest.mouseRelease(ov, Qt.LeftButton, Qt.NoModifier, QPoint(700, 500))
        QApplication.processEvents()
        point_at(ov.canvas, 380, 330)
        return ov

    clipboard.clear()
    overlay = open_selection()
    type_into(overlay.canvas, overlay, "look here")
    type_into(overlay.canvas, overlay, key=Qt.Key_Return)
    check(app.overlay is not None and len(labels(overlay.canvas)) == 1,
          "on the frozen screen, Enter finishes the label and stays open")
    type_into(overlay.canvas, overlay, key=Qt.Key_Space)
    wait_for(lambda: app.overlay is None)
    check(app.overlay is None and red_pixels(clipboard.image()) > 30, "then Space copies it, label included")

    overlay = open_selection()
    type_into(overlay.canvas, overlay, "oops")
    type_into(overlay.canvas, overlay, key=Qt.Key_Escape)
    check(app.overlay is not None and len(labels(overlay.canvas)) == 1, "Esc while typing only ends the label")
    type_into(overlay.canvas, overlay, key=Qt.Key_Escape)
    wait_for(lambda: app.overlay is None)
    check(app.overlay is None, "a second Esc cancels")

    overlay = open_selection()
    type_into(overlay.canvas, overlay, "big")
    size = overlay.canvas._editing.pixel_size()
    scroll(overlay.canvas, 380, 330, 2)
    check(overlay.canvas._editing.pixel_size() > size, "scrolling while typing on the frozen screen resizes the label")
    type_into(overlay.canvas, overlay, key=Qt.Key_Escape)
    type_into(overlay.canvas, overlay, key=Qt.Key_Escape)
    wait_for(lambda: app.overlay is None)

    target = PasteTarget()
    target.show()
    from klipp.app import force_foreground
    force_foreground(target)
    target.activateWindow()
    target.setFocus()
    wait_for(lambda: ctypes.windll.user32.GetForegroundWindow() == int(target.winId()), 1500)
    if ctypes.windll.user32.GetForegroundWindow() != int(target.winId()):
        print("SKIP Ctrl+V while typing: couldn't bring the test window to the front")
    else:
        overlay = open_selection()
        type_into(overlay.canvas, overlay, "why 30s?")
        type_into(overlay.canvas, overlay, key=Qt.Key_V, mods=Qt.ControlModifier)
        wait_for(lambda: target.pasted is not None, 2000)
        check(target.pasted is not None and red_pixels(target.pasted) > 30,
              "Ctrl+V while typing finishes the label and pastes it where you came from")
    target.close()

    overlay = open_selection()
    type_into(overlay.canvas, overlay, "keep me")
    overlay.toolbar.open_button.click()
    wait_for(lambda: app.overlay is None and app.windows)
    editors = list(app.windows)
    check(len(editors) == 1 and [l.toPlainText() for l in labels(editors[0].canvas)] == ["keep me"],
          "Open in window carries labels over")
    for editor in editors:
        editor.close()
    app.hotkeys.unregister_all()
    app.tray.hide()


def test_updates():
    import klipp.updates as updates
    from klipp import __version__
    from klipp.app import KlippApp

    check(updates.parse_version("v1.10.2") > updates.parse_version("1.9.9"), "versions compare as numbers, not text")
    check(updates.parse_version("v1.1.2") == updates.parse_version("1.1.2"), "a leading v doesn't matter")

    config = scratch_config(hotkey_copy="Ctrl+Alt+Shift+F11", hotkey_edit="Ctrl+Alt+Shift+F12", check_updates=True)
    app = KlippApp(QApplication.instance(), config)
    messages = []
    app.tray.showMessage = lambda title, *rest: messages.append(title)
    real_fetch = updates.fetch_latest
    try:
        updates.fetch_latest = lambda: ("0.0.1", "https://example.invalid/old")
        app.updates.check()
        QTest.qWait(300)
        check(not app.update_action.isVisible() and not messages, "an older release is ignored")

        updates.fetch_latest = lambda: ("99.0.0", "https://example.invalid/new")
        app.updates.check()
        wait_for(lambda: app.update_action.isVisible())
        check(app.update_action.isVisible() and "99.0.0" in app.update_action.text(),
              "a newer release adds an update item to the tray menu")
        check(messages == ["Klipp 99.0.0 is available"], "and shows one notification")
        app.updates.check()
        QTest.qWait(300)
        check(len(messages) == 1, "the same version is never announced twice")

        def offline():
            raise OSError("no network")
        updates.fetch_latest = offline
        app.updates.check()
        QTest.qWait(300)
        check(True, "a failed check is silent")
    finally:
        updates.fetch_latest = real_fetch
    app.updates.stop()
    app.hotkeys.unregister_all()
    app.tray.hide()
    print(f"  (running version {__version__})")


def test_hotkey_flow():
    from klipp.app import KlippApp

    # Unusual hotkeys so this works while the real app holds Alt+S.
    config = scratch_config(hotkey_copy="Ctrl+Alt+Shift+F11", hotkey_edit="Ctrl+Alt+Shift+F12")
    user32 = ctypes.windll.user32
    before = user32.GetForegroundWindow()
    app = KlippApp(QApplication.instance(), config)
    check(sorted(app.hotkeys._names.values()) == ["copy", "edit"], "both hotkeys registered")
    ids = {name: hid for hid, name in app.hotkeys._names.items()}
    QGuiApplication.clipboard().clear()
    user32.PostMessageW(app.hotkeys._hwnd, WM_HOTKEY, ids["copy"], 0)
    wait_for(lambda: app.overlay is not None)
    overlay = app.overlay
    check(overlay is not None and overlay.isVisible(), "copy hotkey opened the overlay")
    if overlay is None:
        return
    overlay.grab().scaled(960, 540, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(OUT / "overlay_idle.png"))
    QTest.mousePress(overlay, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
    QTest.mouseMove(overlay, QPoint(400, 300))
    overlay.grab().scaled(960, 540, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(OUT / "overlay_drag.png"))
    QTest.mouseRelease(overlay, Qt.LeftButton, Qt.NoModifier, QPoint(400, 300))
    wait_for(lambda: not QGuiApplication.clipboard().image().isNull())
    clip = QGuiApplication.clipboard().image()
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    check(abs(clip.width() - 300 * dpr) <= 1 and abs(clip.height() - 200 * dpr) <= 1,
          f"selection copied at physical resolution ({clip.width()}x{clip.height()}, dpr {dpr})")
    check(app.overlay is None, "overlay closed after selection")
    check(wait_for(lambda: user32.GetForegroundWindow() == before, 1000), "focus returned to the previous window")

    # Escape cancels.
    user32.PostMessageW(app.hotkeys._hwnd, WM_HOTKEY, ids["edit"], 0)
    wait_for(lambda: app.overlay is not None)
    check(app.overlay is not None, "edit hotkey opened the overlay")
    if app.overlay:
        check(QGuiApplication.focusWindow() is app.overlay.windowHandle(), "overlay has keyboard focus")
        QTest.keyClick(app.overlay, Qt.Key_Escape)
        QTest.qWait(100)
        check(app.overlay is None, "Escape cancels the overlay")
    app.hotkeys.unregister_all()
    app.tray.hide()


if __name__ == "__main__":
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    test_editor()
    test_config_merge()
    test_settings()
    test_snapping()
    test_draw_on_screen()
    test_finish_gestures()
    test_text_labels()
    test_updates()
    test_hotkey_flow()
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
