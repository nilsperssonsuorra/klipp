"""Shared dark theme for Klipp's windows."""

import tempfile
from pathlib import Path

ACCENT = "#2f7bff"

_TEMPLATE = """
QMainWindow, QDialog, QWidget#toolbar {{ background: #1f2023; }}
QWidget {{ color: #e8e8e8; font: 9pt "Segoe UI"; }}
QLabel {{ color: #b8b8b8; }}
QLabel[role="heading"] {{ color: #ffffff; font: 600 10pt "Segoe UI"; padding-top: 6px; }}
QLabel[role="hint"] {{ color: #8a8d94; }}
QLabel[role="ok"] {{ color: #5ad17a; }}
QLabel[role="error"] {{ color: #ff6b6b; }}

QToolButton {{
    background: transparent; border: none; border-radius: 6px; padding: 4px; color: #e8e8e8;
}}
QToolButton:hover {{ background: #33353a; }}
QToolButton:checked {{ background: {ACCENT}; }}

QPushButton {{
    background: #2c2e33; border: 1px solid #3a3c42; border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover {{ background: #35383e; }}
QPushButton:pressed {{ background: #2a2c30; }}
QPushButton[role="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; }}
QPushButton[role="primary"]:hover {{ background: #4a8cff; }}
QPushButton[role="hotkey"] {{
    background: #16171a; font: 600 10pt "Segoe UI"; padding: 7px 12px; min-width: 190px; text-align: left;
}}
QPushButton[role="hotkey"]:checked {{ border: 1px solid {ACCENT}; color: #9ec0ff; }}

QLineEdit, QComboBox {{
    background: #16171a; border: 1px solid #3a3c42; border-radius: 6px; padding: 5px 8px;
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({ARROW}); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: #26282c; border: 1px solid #3a3c42; selection-background-color: {ACCENT};
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px; border: 1px solid #555861; background: #16171a;
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({CHECK}); }}

QSlider::groove:horizontal {{ height: 4px; background: #45474d; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; border-radius: 7px; background: #e8e8e8; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}

QProgressBar {{ background: #16171a; border: 1px solid #3a3c42; border-radius: 5px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

QToolTip {{ background: #26282c; color: #e8e8e8; border: 1px solid #3a3c42; padding: 4px; }}
"""

_style = None


def _glyph(name, points, color):
    """Stylesheets can only load images from files, so draw small glyphs once to temp files."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

    path = Path(tempfile.gettempdir()) / f"klipp-{name}.png"
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))
    p.end()
    pm.save(str(path))
    return path.as_posix()


def stylesheet():
    global _style
    if _style is None:
        check = _glyph("check", [(8, 16.5), (13.5, 22), (24, 10)], "#ffffff")
        arrow = _glyph("arrow", [(7, 12), (16, 21), (25, 12)], "#b8b8b8")
        _style = _TEMPLATE.replace("{ACCENT}", ACCENT).replace("{CHECK}", check).replace("{ARROW}", arrow)
        _style = _style.replace("{{", "{").replace("}}", "}")
    return _style
