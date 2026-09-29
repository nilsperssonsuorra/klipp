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
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


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
