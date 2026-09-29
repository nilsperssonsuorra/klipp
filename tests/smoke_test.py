"""Drives the editor and the hotkey → overlay → clipboard flow with synthetic input.

Run: .venv\\Scripts\\python tests\\smoke_test.py
Briefly flashes the selection overlay on screen. Hotkeys are simulated by posting
WM_HOTKEY directly, so no real key presses reach other apps.
"""

import ctypes
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

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

    win.select_tool("pen")
    win.select_color("#ff3b30")
    drag(vp, [(60, 60), (120, 90), (180, 70), (240, 110)])
    check(len(strokes(canvas)) == 1, "pen stroke created")

    win.select_tool("highlighter")
    win.select_color("#ffe600")
    drag(vp, [(40, 160), (300, 160)])
    win.select_tool("arrow")
    win.select_color("#2f7bff")
    drag(vp, [(350, 250), (450, 150)])
    win.select_tool("rect")
    win.select_color("#34c759")
    drag(vp, [(320, 40), (480, 120)])
    check(len(strokes(canvas)) == 4, "highlighter, arrow and rectangle created")

    # Right-drag inside the rectangle must NOT erase it (only its outline counts).
    drag(vp, [(380, 70), (420, 90)], Qt.RightButton)
    check(len(strokes(canvas)) == 4, "right-drag inside a rectangle leaves it alone")

    # Right-drag across the pen stroke erases the whole stroke.
    drag(vp, [(120, 40), (120, 130)], Qt.RightButton)
    check(len(strokes(canvas)) == 3, "right-drag erased the pen stroke as a whole")
    check(win.windowHandle() is not None and canvas.tool == "rect", "tool unchanged after right-erase")

    canvas.undo_stack.undo()
    check(len(strokes(canvas)) == 4, "undo restores the erased stroke")
    canvas.undo_stack.redo()
    check(len(strokes(canvas)) == 3, "redo erases it again")
    canvas.undo_stack.undo()

    # A plain click with a shape tool must not leave an invisible item or an undo step.
    steps = canvas.undo_stack.count()
    drag(vp, [(600, 300), (600, 300)])
    check(len(strokes(canvas)) == 4 and canvas.undo_stack.count() == steps, "click with rectangle tool adds nothing")

    # If the button release never arrives (focus stolen mid-drag), the canvas must recover.
    win.select_tool("pen")
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, QPoint(600, 350))
    stray = QMouseEvent(QEvent.MouseMove, QPointF(610, 360), QPointF(610, 360), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
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
    test_hotkey_flow()
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
