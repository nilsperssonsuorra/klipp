"""Starting Klipp with Windows (the HKCU Run key).

Only ever changed when the user asks for it in Settings. A freshly downloaded, unsigned
program that adds itself to the Run key on first launch is exactly what Windows Defender's
"Behavior:Win32/Persistence" detection looks for, and it blocks the program."""

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Klipp"
LEGACY_VALUE_NAMES = ("Screenshotter",)  # entries written by older versions


def launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    script = Path(__file__).resolve().parent.parent / "run.pyw"
    return f'"{pythonw}" "{script}"'


def is_enabled():
    """True if Windows will start this copy of Klipp at login (not one in another folder)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return False
    return value.strip().lower() == launch_command().lower()


def set_enabled(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        stale = LEGACY_VALUE_NAMES if enabled else (VALUE_NAME, *LEGACY_VALUE_NAMES)
        for name in stale:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
