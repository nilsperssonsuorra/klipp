"""Fast screen capture.

Qt's QScreen.grabWindow() takes about 100 ms for a 4K screen. Copying the monitor straight
into a DIB with BitBlt and wrapping that in a QImage takes about half, which is most of the
delay between pressing the hotkey and seeing the frozen screen.
"""

import ctypes
from ctypes import wintypes

from PySide6.QtGui import QImage, QPixmap

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]

SRCCOPY = 0x00CC0020
MONITOR_DEFAULTTONEAREST = 2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _monitor_under_cursor():
    """The physical-pixel rectangle of the monitor the mouse is on."""
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    monitor = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO(ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    r = info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _grab_gdi(x, y, w, h):
    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(screen_dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    try:
        if not bitmap or not bits.value:
            raise OSError("CreateDIBSection failed")
        previous = gdi32.SelectObject(memory_dc, bitmap)
        ok = gdi32.BitBlt(memory_dc, 0, 0, w, h, screen_dc, x, y, SRCCOPY)
        gdi32.SelectObject(memory_dc, previous)
        if not ok:
            raise OSError("BitBlt failed")
        buffer = (ctypes.c_ubyte * (w * h * 4)).from_address(bits.value)
        return QImage(buffer, w, h, w * 4, QImage.Format_RGB32).copy()  # copy: the DIB is freed below
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def grab_screen(screen):
    """Screenshot of `screen` (the one under the mouse) as a QPixmap with its device pixel ratio."""
    dpr = screen.devicePixelRatio()
    expected = (round(screen.geometry().width() * dpr), round(screen.geometry().height() * dpr))
    try:
        x, y, w, h = _monitor_under_cursor()
        if (w, h) == expected:
            pixmap = QPixmap.fromImage(_grab_gdi(x, y, w, h))
            pixmap.setDevicePixelRatio(dpr)
            return pixmap
    except OSError:
        pass
    return screen.grabWindow(0)  # the slower, always-correct way
