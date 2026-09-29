import json
import os
import shutil
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Klipp"
CONFIG_PATH = CONFIG_DIR / "config.json"
LEGACY_CONFIG_PATH = CONFIG_DIR.parent / "Screenshotter" / "config.json"  # pre-rename name

DEFAULTS = {
    # Hotkey syntax: modifiers (Ctrl, Alt, Shift, Win) + key, e.g. "Ctrl+Alt+S", "F9", "PrintScreen".
    "hotkey_copy": "Alt+S",
    "hotkey_edit": "Alt+Shift+S",
    "autostart": False,  # mirrors the Run key; only Settings changes it (see autostart.py)
    "tray_click": "edit",  # "edit", "copy" or "none"
    "edit_mode": "inplace",  # capture-and-edit draws on the frozen screen ("inplace") or opens a "window"
    "snap_shapes": True,  # pausing at the end of a pen stroke turns a rough line/circle/box into a clean one
    "show_toast": True,  # "Copied to clipboard" bubble after a clipboard capture
    "crosshair": True,  # guide lines while choosing where to start the selection
    "dim": 45,  # how much the frozen screen is darkened outside the selection, in percent
    "save_dir": "",  # empty means Pictures\Klipp
    "auto_save": False,  # also save every capture as a PNG in save_dir
    "check_updates": True,  # ask GitHub once a day whether a newer release exists
    "notified_version": "",  # newest version the user was already told about
    # Editor state, remembered between sessions.
    "last_save_dir": "",  # where "Save as" was last used
    "tool": "pen",
    "color": "#ff3b30",
    "sizes": {
        "pen": 6,
        "highlighter": 26,
        "line": 6,
        "arrow": 6,
        "rect": 6,
        "ellipse": 6,
        "eraser": 24,
    },
}

EDITOR_KEYS = ("tool", "color", "sizes", "last_save_dir")
SETTINGS_KEYS = (
    "hotkey_copy", "hotkey_edit", "autostart", "tray_click", "show_toast",
    "crosshair", "dim", "save_dir", "auto_save", "edit_mode", "snap_shapes", "check_updates",
)


def default_save_dir():
    from PySide6.QtCore import QStandardPaths

    return Path(QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)) / "Klipp"


def _read_stored():
    """Returns (settings dict, error message). A missing file is not an error."""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as exc:
        return None, f"Could not read {CONFIG_PATH.name}: {exc}"


class Config:
    def __init__(self, data, error=None, first_run=False):
        self.data = data
        self.error = error
        self.first_run = first_run

    @classmethod
    def load(cls):
        first_run = not CONFIG_PATH.exists()
        if first_run and LEGACY_CONFIG_PATH.exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(LEGACY_CONFIG_PATH, CONFIG_PATH)
            first_run = False
        data = json.loads(json.dumps(DEFAULTS))
        stored, error = _read_stored()
        for key, value in (stored or {}).items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key].update(value)
            else:
                data[key] = value
        cfg = cls(data, error, first_run)
        cfg.save([key for key in DEFAULTS if key not in (stored or {})])  # add options new in this version
        return cfg

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def save_dir(self):
        return Path(self.data["save_dir"]) if self.data["save_dir"] else default_save_dir()

    def save(self, keys):
        """Write only the given keys, keeping whatever else is in the file, so edits the
        user made by hand aren't overwritten. A missing file gets everything."""
        stored, error = _read_stored()
        if error:
            return  # never clobber a file the user is in the middle of fixing
        if not stored:
            keys = self.data.keys()
        stored.update({key: self.data[key] for key in keys})
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(stored, indent=2), encoding="utf-8")
