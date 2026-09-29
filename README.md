# Klipp

**Alt+Shift+S, drag, draw, Ctrl+V.** Screenshots on Windows that you draw on right where they are, then paste, without opening an app or saving a file.

![Klipp demo](docs/demo.gif)

**[Download Klipp for Windows](https://github.com/nilsperssonsuorra/klipp/releases/latest)**. Unzip it and run `Klipp.exe`. No installer, no account.

## Why Klipp?

- **You draw where you captured.** After Alt+Shift+S, your selection stays on the frozen screen with a toolbar under it. There's no editor window to open, arrange or close.
- **Ctrl+V is the last step.** It unfreezes the screen and pastes your capture, drawing included, into the window you were in. No Copy button, no saving, no file picker.
- **Mistakes are cheap.** Hold the right mouse button and drag to erase whole strokes, whichever tool you're using. Ctrl+Z works too.
- **Rough is fine.** Pause for a moment at the end of a pen stroke and a wobbly circle, box or line turns into a clean one.
- **Nothing leaves your PC.** Klipp is free and open source (MIT). There's no account, it never connects to the internet, and it collects nothing.

## What it does

- **Alt+S** freezes the screen so you can drag out an area. The area goes straight to the clipboard.
- **Alt+Shift+S** freezes the screen, lets you draw on the area you drag out, then copies or pastes it. The keys are listed under [Using it](#using-it).
- Tools: pen, highlighter, line, arrow, rectangle and ellipse, plus 11 preset colors you pick with one click.
- One click moves the capture, drawing included, into an editor window when you want to zoom or have more room. There, every change is copied to the clipboard right away.
- Captures keep full resolution on high-DPI screens. A 4K screen at 150% scaling gives you 4K pixels.

Klipp runs in the system tray. To have it start with Windows, turn that on in Settings.

## Install

1. Download `Klipp-<version>-windows.zip` from the [latest release](https://github.com/nilsperssonsuorra/klipp/releases/latest).
2. Unzip it wherever you want to keep it, for example `C:\Tools\Klipp`.
3. Run `Klipp.exe`. It adds an icon to the system tray.

Klipp isn't code-signed yet, so Windows SmartScreen may warn you the first time. Click **More info**, then **Run anyway**.

To update, quit Klipp from the tray icon, replace the folder with the new version and start it again. Your settings are kept.

### Build it yourself

With Python 3.12 on Windows 10 or 11:

```powershell
git clone https://github.com/nilsperssonsuorra/klipp.git
cd klipp
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.\build.ps1
```

This creates `dist\Klipp\Klipp.exe` and a release zip in `dist`.

## Using it

While selecting an area, press **Esc** or right-click to cancel.

After **Alt+Shift+S**, a toolbar appears under your selection:

| Input | Action |
|---|---|
| **Ctrl+V** | Paste the area with your drawing into the window you were in |
| Space, Enter, Ctrl+C or a click outside the selection | Copy the area with your drawing and close |
| Ctrl+S | Copy, then save as PNG or JPG |
| Esc | Cancel |
| Window button | Continue in the editor window, drawing included |

Drawing works the same on the frozen screen and in the editor window:

| Input | Action |
|---|---|
| Left-drag | Draw with the current tool |
| Right-drag | Erase the strokes you touch |
| P, H, L, A, R, O, E | Pen, highlighter, line, arrow, rectangle, ellipse, eraser |
| 1 to 9, 0 | Pick one of the first ten colors |
| `-` and `+` (or `[` and `]`) | Smaller or bigger brush |
| Pause before letting go of a pen stroke | Snap a rough line, circle or box into a clean one |
| Shift while drawing | Straight lines in 45° steps, perfect squares and circles |
| Ctrl+Z, Ctrl+Y | Undo, redo |
| Ctrl+S | Save as PNG or JPG |
| Ctrl+scroll, Ctrl+0, Ctrl+1 | Zoom, fit to window, actual size (editor window) |
| Middle-drag | Pan (editor window) |

## Settings

Right-click the tray icon and choose **Settings…**.

![Settings window](docs/settings.png)

To change a hotkey, click its box and press the new key combination. Klipp checks whether another program already uses it. Plain letters need a modifier so they don't block normal typing. F-keys, PrintScreen and Pause work on their own.

The other settings:

- Choose whether capture-and-edit draws on the frozen screen or opens the editor window.
- Turn shape snapping on or off.
- Show the "Copied" popup after a clipboard capture.
- Show crosshair guide lines while selecting.
- Choose how dark the screen gets outside the selection.
- Pick a folder for saved captures, and optionally save every capture there as a PNG. The default folder is `Pictures\Klipp`.
- Start with Windows.
- Choose what a left-click on the tray icon does.

Settings are stored in `%APPDATA%\Klipp\config.json`. You can edit that file by hand while Klipp isn't running.

## Development

```powershell
.venv\Scripts\python -m klipp               # run from source
.venv\Scripts\python tests\smoke_test.py    # automated checks
.venv\Scripts\python tools\make_demo.py     # re-render docs\demo.gif and docs\settings.png
```

The smoke test drives the editor, the settings window and the capture flow with simulated input. It briefly shows the selection overlay on screen. It posts hotkey messages to Klipp directly, so no real key presses reach other programs.

`make_demo.py` renders the README images off-screen using the real widgets on a staged desktop. It needs `ffmpeg` on your PATH.

To publish a release, bump `__version__` in `klipp/__init__.py`, commit, then push a matching tag such as `v1.0.1`. GitHub Actions builds `Klipp.exe` and attaches the zip to a new release.

Code layout:

| File | Contents |
|---|---|
| `klipp/app.py` | Tray icon, hotkey handling, capture flow |
| `klipp/overlay.py` | The frozen-screen selection overlay |
| `klipp/editor.py` | The drawing editor |
| `klipp/settings.py` | The settings window and hotkey recorder |
| `klipp/hotkeys.py` | Global hotkeys through the Win32 `RegisterHotKey` API |
| `klipp/config.py` | Loading and saving settings |

## License

MIT. See [LICENSE](LICENSE).
