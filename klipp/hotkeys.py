"""Global hotkeys via the Win32 RegisterHotKey API."""

import ctypes
import re
from ctypes import wintypes

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

user32 = ctypes.windll.user32
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}

_NAMED_KEYS = {
    "printscreen": 0x2C, "prtsc": 0x2C, "prtscn": 0x2C, "print": 0x2C,
    "pause": 0x13, "scrolllock": 0x91,
    "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "backspace": 0x08,
    "`": 0xC0, "§": 0xDC, "-": 0xBD, "=": 0xBB, ",": 0xBC, ".": 0xBE,
}


# Canonical display names, used when recording a hotkey in the settings window.
_VK_NAMES = {
    0x2C: "PrintScreen", 0x13: "Pause", 0x91: "ScrollLock",
    0x2D: "Insert", 0x2E: "Delete", 0x24: "Home", 0x23: "End", 0x21: "PageUp", 0x22: "PageDown",
    0x20: "Space", 0x09: "Tab", 0x0D: "Enter", 0x08: "Backspace",
    0xC0: "`", 0xDC: "§", 0xBD: "-", 0xBB: "=", 0xBC: ",", 0xBE: ".",
}
# Keys that make sense as a hotkey on their own, without Ctrl/Alt/Shift/Win.
STANDALONE_KEYS = {"PrintScreen", "Pause", "ScrollLock"} | {f"F{n}" for n in range(1, 25)}


def key_name(vk):
    """Name for a Windows virtual-key code, in the syntax parse_hotkey accepts, or None."""
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    return _VK_NAMES.get(vk)


def is_available(text):
    """True if no other program has claimed this hotkey."""
    probe = HotkeyWindow()
    try:
        return probe.register("probe", text) is None
    finally:
        probe.unregister_all()
        probe.deleteLater()


def parse_hotkey(text):
    """Turn e.g. "Ctrl+Shift+S" into (modifiers, virtual key code)."""
    mods, vk = 0, None
    for part in (p.strip().lower() for p in text.split("+")):
        if not part:
            continue
        if part in _MODIFIERS:
            mods |= _MODIFIERS[part]
        elif part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part):
            vk = 0x70 + int(part[1:]) - 1
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"Unknown key '{part}' in hotkey '{text}'")
    if vk is None:
        raise ValueError(f"Hotkey '{text}' has no main key")
    return mods, vk


class HotkeyWindow(QWidget):
    """Hidden native window that receives WM_HOTKEY messages."""

    triggered = Signal(str)

    def __init__(self):
        super().__init__()
        self._names = {}
        self._hwnd = int(self.winId())

    def register(self, name, text):
        """Register a hotkey. Returns an error message, or None on success."""
        try:
            mods, vk = parse_hotkey(text)
        except ValueError as exc:
            return str(exc)
        hotkey_id = len(self._names) + 1
        if not user32.RegisterHotKey(self._hwnd, hotkey_id, mods | MOD_NOREPEAT, vk):
            return f"'{text}' is already used by another program"
        self._names[hotkey_id] = name
        return None

    def unregister_all(self):
        for hotkey_id in self._names:
            user32.UnregisterHotKey(self._hwnd, hotkey_id)
        self._names.clear()

    def nativeEvent(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam in self._names:
            self.triggered.emit(self._names[msg.wParam])
            return True, 0
        return super().nativeEvent(event_type, message)
