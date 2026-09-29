"""Settings window, opened from the tray icon."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from . import __version__, hotkeys
from .config import DEFAULTS, default_save_dir
from .theme import stylesheet

TRAY_CLICK_CHOICES = [
    ("edit", "Capture and edit"),
    ("copy", "Capture to clipboard"),
    ("none", "Do nothing"),
]
MODIFIER_KEYS = {Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr}


def _label(text, role=None):
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    return label


def _modifier_names(modifiers):
    names = []
    for flag, name in (
        (Qt.ControlModifier, "Ctrl"),
        (Qt.AltModifier, "Alt"),
        (Qt.ShiftModifier, "Shift"),
        (Qt.MetaModifier, "Win"),
    ):
        if modifiers & flag:
            names.append(name)
    return names


class HotkeyEdit(QPushButton):
    """Click it, then press the key combination you want."""

    changed = Signal(str)

    def __init__(self, value):
        super().__init__()
        self.setProperty("role", "hotkey")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.value = value
        self.problem = ""  # why the last key press was rejected
        self.toggled.connect(self._on_toggled)
        self._refresh()

    def set_value(self, value):
        self.value = value
        self.setChecked(False)
        self._refresh()
        self.changed.emit(value)

    def _refresh(self, pending=None):
        if self.isChecked():
            self.setText(pending or "Press a key combination…")
        else:
            self.setText(self.value.replace("+", " + "))

    def _on_toggled(self, recording):
        self.problem = ""
        self._refresh()
        if recording:
            self.setFocus()

    def keyPressEvent(self, event):
        if not self.isChecked():
            if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                self.setChecked(True)
                return
            super().keyPressEvent(event)
            return
        key, mods = event.key(), _modifier_names(event.modifiers())
        if key in MODIFIER_KEYS:
            self._refresh(" + ".join(mods) + " + …")
            return
        if key == Qt.Key_Escape and not mods:
            self.setChecked(False)
            return
        self._accept(hotkeys.key_name(event.nativeVirtualKey()), mods)

    def keyReleaseEvent(self, event):
        # Windows only delivers PrintScreen as a key release.
        if self.isChecked() and event.key() == Qt.Key_Print:
            self._accept("PrintScreen", _modifier_names(event.modifiers()))
            return
        if self.isChecked() and event.key() in MODIFIER_KEYS:
            mods = _modifier_names(event.modifiers())
            self._refresh(" + ".join(mods) + " + …" if mods else None)
            return
        super().keyReleaseEvent(event)

    def _accept(self, name, mods):
        if name is None:
            problem = "That key can't be used as a hotkey."
        elif not mods and name not in hotkeys.STANDALONE_KEYS:
            problem = "Add Ctrl, Alt, Shift or Win, so normal typing isn't blocked."
        else:
            self.set_value("+".join(mods + [name]))
            return
        self.setChecked(False)  # clears self.problem, so set it afterwards
        self.problem = problem
        self.changed.emit(self.value)

    def focusOutEvent(self, event):
        self.setChecked(False)
        super().focusOutEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, config, icon=None):
        super().__init__(None, Qt.WindowCloseButtonHint | Qt.WindowTitleHint)
        self.config = config
        self.setWindowTitle("Klipp settings")
        if icon is not None:
            self.setWindowIcon(icon)
        self.setStyleSheet(stylesheet())
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 18)
        root.setSpacing(6)

        # Hotkeys --------------------------------------------------------------
        root.addWidget(_label("Hotkeys", "heading"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.copy_key = HotkeyEdit(config["hotkey_copy"])
        self.edit_key = HotkeyEdit(config["hotkey_edit"])
        self.copy_status = _label("")
        self.edit_status = _label("")
        grid.addWidget(QLabel("Capture to clipboard"), 0, 0)
        grid.addWidget(self.copy_key, 0, 1)
        grid.addWidget(self.copy_status, 0, 2)
        grid.addWidget(QLabel("Capture and edit"), 1, 0)
        grid.addWidget(self.edit_key, 1, 1)
        grid.addWidget(self.edit_status, 1, 2)
        grid.setColumnStretch(2, 1)
        root.addLayout(grid)
        root.addWidget(_label("Click a box, then press the keys you want. Esc cancels.", "hint"))
        for edit in (self.copy_key, self.edit_key):
            edit.changed.connect(self._validate_hotkeys)

        # Capturing ------------------------------------------------------------
        root.addSpacing(8)
        root.addWidget(_label("Capturing", "heading"))
        self.show_toast = QCheckBox('Show a "Copied" popup after capturing to the clipboard')
        self.crosshair = QCheckBox("Show crosshair guide lines while selecting")
        root.addWidget(self.show_toast)
        root.addWidget(self.crosshair)
        dim_row = QHBoxLayout()
        dim_row.addWidget(QLabel("Darken the screen outside the selection"))
        self.dim = QSlider(Qt.Horizontal)
        self.dim.setRange(0, 80)
        self.dim.setFixedWidth(150)
        self.dim_label = QLabel()
        self.dim_label.setFixedWidth(36)
        self.dim.valueChanged.connect(lambda v: self.dim_label.setText(f"{v}%"))
        dim_row.addStretch(1)
        dim_row.addWidget(self.dim)
        dim_row.addWidget(self.dim_label)
        root.addLayout(dim_row)

        # Saving ---------------------------------------------------------------
        root.addSpacing(8)
        root.addWidget(_label("Saving", "heading"))
        folder_row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText(str(default_save_dir()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(QLabel("Folder"))
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        root.addLayout(folder_row)
        self.auto_save = QCheckBox("Automatically save every capture to this folder as a PNG")
        root.addWidget(self.auto_save)

        # General --------------------------------------------------------------
        root.addSpacing(8)
        root.addWidget(_label("General", "heading"))
        self.autostart = QCheckBox("Start Klipp when Windows starts")
        root.addWidget(self.autostart)
        tray_row = QHBoxLayout()
        tray_row.addWidget(QLabel("Clicking the tray icon"))
        self.tray_click = QComboBox()
        for key, text in TRAY_CLICK_CHOICES:
            self.tray_click.addItem(text, key)
        tray_row.addWidget(self.tray_click)
        tray_row.addStretch(1)
        root.addLayout(tray_row)

        # Footer ---------------------------------------------------------------
        root.addSpacing(14)
        footer = QHBoxLayout()
        footer.addWidget(_label(f"Klipp {__version__}", "hint"))
        footer.addStretch(1)
        defaults = QPushButton("Restore defaults")
        defaults.clicked.connect(lambda: self._load(DEFAULTS))
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton("Save")
        self.save_button.setProperty("role", "primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)
        for button in (defaults, cancel, self.save_button):
            button.setAutoDefault(False)
            button.setFocusPolicy(Qt.TabFocus)
            footer.addWidget(button)
        root.addLayout(footer)

        self._load(config.data)

    def _load(self, data):
        self.copy_key.set_value(data["hotkey_copy"])
        self.edit_key.set_value(data["hotkey_edit"])
        self.show_toast.setChecked(data["show_toast"])
        self.crosshair.setChecked(data["crosshair"])
        self.dim.setValue(int(data["dim"]))
        self.dim_label.setText(f"{self.dim.value()}%")
        self.folder.setText(data["save_dir"])
        self.auto_save.setChecked(data["auto_save"])
        self.autostart.setChecked(data["autostart"])
        index = self.tray_click.findData(data["tray_click"])
        self.tray_click.setCurrentIndex(max(index, 0))

    def _browse(self):
        start = self.folder.text() or str(default_save_dir())
        folder = QFileDialog.getExistingDirectory(self, "Folder for saved captures", start)
        if folder:
            self.folder.setText(folder.replace("/", "\\"))

    def _validate_hotkeys(self, *_):
        """Show per-hotkey status; returns True if both can be registered."""
        ok = True
        for edit, status in ((self.copy_key, self.copy_status), (self.edit_key, self.edit_status)):
            other = self.edit_key if edit is self.copy_key else self.copy_key
            if edit.problem:
                message, good = edit.problem, False
            elif edit.value.lower() == other.value.lower():
                message, good = "Both hotkeys are the same.", False
            elif not hotkeys.is_available(edit.value):
                message, good = "Already used by another program.", False
            else:
                message, good = "✓", True
            status.setText(message)
            status.setProperty("role", "ok" if good else "error")
            status.style().polish(status)
            ok = ok and good
        self.save_button.setEnabled(ok)
        return ok

    def values(self):
        return {
            "hotkey_copy": self.copy_key.value,
            "hotkey_edit": self.edit_key.value,
            "show_toast": self.show_toast.isChecked(),
            "crosshair": self.crosshair.isChecked(),
            "dim": self.dim.value(),
            "save_dir": self.folder.text().strip(),
            "auto_save": self.auto_save.isChecked(),
            "autostart": self.autostart.isChecked(),
            "tray_click": self.tray_click.currentData(),
        }

    def accept(self):
        if self._validate_hotkeys():
            super().accept()
