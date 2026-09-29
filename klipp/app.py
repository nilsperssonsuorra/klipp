import ctypes
import os
import sys
import traceback
from datetime import datetime

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QSystemTrayIcon, QWidget

from . import autostart, icons
from .capture import grab_screen
from .config import CONFIG_DIR, SETTINGS_KEYS, Config
from .editor import EditorWindow, save_image_dialog
from .hotkeys import HotkeyWindow
from .overlay import SelectionOverlay
from .settings import SettingsDialog

MUTEX_NAME = "Local\\Klipp.SingleInstance"
ERROR_ALREADY_EXISTS = 183


def install_error_log():
    """pythonw has no console, so write uncaught exceptions to a file instead of losing them."""
    log = CONFIG_DIR / "error.log"

    def hook(exc_type, exc, tb):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write("".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


class Toast(QWidget):
    """Small "Copied" bubble that fades out, shown after a clipboard capture."""

    def __init__(self, text, anchor):
        flags = (
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.text = text
        width = self.fontMetrics().horizontalAdvance(text) + 28
        self.resize(width, 32)
        # Just below the selection, but kept on screen.
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(anchor.x() - width // 2, area.left() + 8), area.right() - width - 8)
        y = min(anchor.y() + 12, area.bottom() - self.height() - 8)
        self.move(x, y)
        self.setWindowOpacity(0.95)
        QTimer.singleShot(900, self._fade)

    def _fade(self):
        opacity = self.windowOpacity() - 0.12
        if opacity <= 0:
            self.close()
        else:
            self.setWindowOpacity(opacity)
            QTimer.singleShot(25, self._fade)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(28, 30, 34, 235))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        p.setPen(QColor("#5ad17a"))
        p.drawText(self.rect(), Qt.AlignCenter, self.text)


class KlippApp(QObject):
    def __init__(self, qapp, config=None):
        super().__init__()
        self.qapp = qapp
        self.config = config or Config.load()
        self.icon = icons.app_icon()
        self.overlay = None
        self.windows = set()
        self.settings_dialog = None
        self._toast = None
        self._previous_window = None  # window to hand focus back to after a copy capture
        self._paste_into = None  # window to paste into once the frozen screen has gone

        self.hotkeys = HotkeyWindow()
        self.hotkeys.triggered.connect(self.capture)
        self._build_tray()
        self._register_hotkeys()
        # Klipp never adds itself to autostart; that only happens from Settings (see autostart.py).
        self.config["autostart"] = autostart.is_enabled()
        if self.config.first_run:
            self.tray.showMessage(
                "Klipp is running",
                f"{self.config['hotkey_copy']}: capture an area to the clipboard\n"
                f"{self.config['hotkey_edit']}: capture an area and draw on it\n"
                "Right-click the tray icon → Settings to start Klipp with Windows.",
                QSystemTrayIcon.Information,
                8000,
            )

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.icon, self)
        menu = QMenu()
        self.copy_action = menu.addAction("")
        self.copy_action.triggered.connect(lambda: QTimer.singleShot(250, lambda: self.capture("copy")))
        self.edit_action = menu.addAction("")
        self.edit_action.triggered.connect(lambda: QTimer.singleShot(250, lambda: self.capture("edit")))
        menu.addSeparator()
        menu.addAction("Settings…").triggered.connect(self.open_settings)
        menu.addAction("Open captures folder").triggered.connect(self._open_captures_folder)
        menu.addSeparator()
        menu.addAction("Quit Klipp").triggered.connect(self.qapp.quit)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self._update_labels()
        self.tray.show()

    def _update_labels(self):
        copy_key, edit_key = self.config["hotkey_copy"], self.config["hotkey_edit"]
        self.copy_action.setText(f"Capture to clipboard\t{copy_key}")
        self.edit_action.setText(f"Capture and edit\t{edit_key}")
        self.tray.setToolTip(f"Klipp\n{copy_key}: copy area\n{edit_key}: edit area")

    def _register_hotkeys(self):
        """Registers both hotkeys; returns True if everything worked."""
        errors = [self.config.error] if self.config.error else []
        for mode, key in (("copy", "hotkey_copy"), ("edit", "hotkey_edit")):
            error = self.hotkeys.register(mode, self.config[key])
            if error:
                errors.append(error)
        if errors:
            self.tray.showMessage(
                "Klipp: settings problem",
                "\n".join(errors) + "\nRight-click the tray icon → Settings… to fix it.",
                QSystemTrayIcon.Warning,
                8000,
            )
        return not errors

    def _set_autostart(self, enabled):
        try:
            autostart.set_enabled(enabled)
        except OSError:
            pass
        self.config["autostart"] = autostart.is_enabled()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger and self.config["tray_click"] in ("copy", "edit"):
            mode = self.config["tray_click"]
            QTimer.singleShot(150, lambda: self.capture(mode))

    def _open_captures_folder(self):
        folder = self.config.save_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    # settings ------------------------------------------------------------------

    def open_settings(self):
        if self.settings_dialog is not None:
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        # Release our hotkeys so they can be re-recorded, and so the availability
        # check only reports conflicts with other programs.
        self.hotkeys.unregister_all()
        self.config["autostart"] = autostart.is_enabled()  # show the real state
        self.settings_dialog = SettingsDialog(self.config, self.icon)
        self.settings_dialog.finished.connect(self._settings_closed)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()
        force_foreground(self.settings_dialog)

    def _settings_closed(self, result):
        dialog, self.settings_dialog = self.settings_dialog, None
        if result == QDialog.Accepted:
            values = dialog.values()
            if values["autostart"] != autostart.is_enabled():
                self._set_autostart(values["autostart"])  # the user asked for this change
            self.config.data.update(values)
            self.config["autostart"] = autostart.is_enabled()
            self.config.error = None
            self.config.save(SETTINGS_KEYS)
        self._register_hotkeys()
        self._update_labels()
        dialog.deleteLater()

    # capture flow --------------------------------------------------------------

    def capture(self, mode):
        if self.overlay is not None:
            return
        self._previous_window = ctypes.windll.user32.GetForegroundWindow()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        shot = grab_screen(screen)
        dpr = shot.devicePixelRatio()
        draw_here = mode == "edit" and self.config["edit_mode"] != "window"
        self.overlay = SelectionOverlay(screen, shot, self.config["dim"], self.config["crosshair"],
                                        draw_config=self.config if draw_here else None)
        self.overlay.selected.connect(lambda image, rect: self._on_selected(mode, image, rect, dpr))
        self.overlay.annotated.connect(self._on_annotated)
        self.overlay.open_editor.connect(lambda image, strokes, rect: self._open_editor(image, dpr, rect, strokes))
        self.overlay.finished.connect(self._overlay_closed)
        self.overlay.show()
        self.overlay.raise_()
        self.overlay.activateWindow()
        force_foreground(self.overlay)

    def _overlay_closed(self):
        self.overlay = None
        # After a copy or a cancel, return focus to where the user was so Ctrl+V lands there.
        user32 = ctypes.windll.user32
        if self._previous_window and user32.IsWindow(self._previous_window):
            user32.SetForegroundWindow(self._previous_window)
        self._previous_window = None
        if self._paste_into:
            target, self._paste_into = self._paste_into, None
            QTimer.singleShot(120, lambda: send_paste(target))

    def _auto_save(self, image):
        folder = self.config.save_dir()
        path = folder / datetime.now().strftime("Klipp %Y-%m-%d %H%M%S.png")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            saved = image.save(str(path))
        except OSError:
            saved = False
        if not saved:
            self.tray.showMessage("Klipp", f"Could not save the capture to\n{folder}", QSystemTrayIcon.Warning, 5000)

    def _on_selected(self, mode, image, rect, dpr):
        if self.config["auto_save"]:
            self._auto_save(image)
        if mode == "copy":
            QGuiApplication.clipboard().setPixmap(image)
            if self.config["show_toast"]:
                self._toast = Toast("✓ Copied to clipboard", QPoint(rect.center().x(), rect.bottom()))
                self._toast.show()
            return
        self._open_editor(image, dpr, rect)

    def _on_annotated(self, image, rect, action):
        """Finished drawing on the frozen screen."""
        if self.config["auto_save"]:
            self._auto_save(image)
        QGuiApplication.clipboard().setImage(image)
        if action == "paste" and can_paste_into(self._previous_window):
            self._paste_into = self._previous_window  # pasted once focus is back there
            return
        if action == "save":
            # Keep focus with Klipp and open the dialog once the overlay is gone, so it isn't hidden.
            self._previous_window = None
            QTimer.singleShot(0, lambda: save_image_dialog(image, self.config))
        elif self.config["show_toast"]:
            self._toast = Toast("✓ Copied to clipboard", QPoint(rect.center().x(), rect.bottom()))
            self._toast.show()

    def _open_editor(self, image, dpr, rect, strokes=None):
        self._previous_window = None  # the editor takes focus instead
        window = EditorWindow(image, dpr, self.config, self.icon, strokes)
        self.windows.add(window)
        window.destroyed.connect(lambda: self.windows.discard(window))
        window.present(rect)
        force_foreground(window)


NOT_PASTE_TARGETS = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}  # desktop, taskbar


def can_paste_into(hwnd):
    if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
        return False
    name = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(hwnd, name, 64)
    return name.value not in NOT_PASTE_TARGETS


def send_paste(hwnd):
    """Press Ctrl+V for the user, but only if `hwnd` really is the window in front."""
    user32 = ctypes.windll.user32
    if user32.GetForegroundWindow() != hwnd:
        return
    VK_CONTROL, VK_V, KEYUP = 0x11, 0x56, 0x0002
    ctrl_held = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000  # the user may still be holding it
    if not ctrl_held:
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYUP, 0)
    if not ctrl_held:
        user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)


def force_foreground(widget):
    """Windows may refuse focus to a background app; this nudges it so Esc/shortcuts work."""
    user32 = ctypes.windll.user32
    hwnd = int(widget.winId())
    if user32.GetForegroundWindow() == hwnd or user32.SetForegroundWindow(hwnd):
        return
    # Holding a synthetic Alt lifts the foreground lock for this process.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)


def main():
    install_error_log()
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName("Klipp")
    app = KlippApp(qapp)
    qapp.setWindowIcon(app.icon)
    try:
        sys.exit(qapp.exec())
    finally:
        app.hotkeys.unregister_all()
        ctypes.windll.kernel32.CloseHandle(mutex)
