"""Dark theme: colour tokens, Qt stylesheet and pyqtgraph defaults."""
from __future__ import annotations

import numpy as np

BG = "#0d1117"
BG_ELEV = "#151b23"
BG_CARD = "#1b2430"
BORDER = "#2a3441"
BORDER_STRONG = "#3a4757"
TEXT = "#e6edf3"
TEXT_DIM = "#93a3b5"
TEXT_FAINT = "#6b7d91"
ACCENT = "#2f9bff"
ACCENT_DIM = "#1b5f9e"
GOOD = "#2ecc71"
WARN = "#f0a020"
BAD = "#ef4444"
TRACE = "#35d6a4"
PEAK = "#f0a020"
NOISE = "#7a8ba0"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {BG}; }}

/* QLabel derives from QFrame, so any card-level "QFrame {{...}}" rule would
   otherwise draw a border and an opaque background behind every label. */
QLabel {{ background: transparent; border: none; }}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    background: {BG_ELEV};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 9px 18px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {TEXT}; background: {BG_CARD}; }}
QTabBar::tab:selected {{
    background: {BG_ELEV};
    color: {ACCENT};
    border: 1px solid {BORDER};
    border-bottom-color: {BG_ELEV};
}}

QGroupBox {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 16px;
    padding: 12px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 2px 6px;
    color: {TEXT_DIM};
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 1px;
}}

QPushButton {{
    background: {BG_CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 7px;
    padding: 8px 16px;
    color: {TEXT};
    font-weight: 600;
}}
QPushButton:hover {{ background: #223044; border-color: {ACCENT_DIM}; }}
QPushButton:pressed {{ background: #1a2634; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; background: #141a22; }}
QPushButton[accent="true"] {{
    background: {ACCENT_DIM};
    border-color: {ACCENT};
    color: #ffffff;
}}
QPushButton[accent="true"]:hover {{ background: {ACCENT}; }}
QPushButton[danger="true"] {{ border-color: {BAD}; color: #ffd7d7; }}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {BG_ELEV};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: {ACCENT_DIM};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{
    border-color: {ACCENT_DIM};
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {BG_ELEV};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {ACCENT_DIM};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {BG_CARD};
    border-left: 1px solid {BORDER};
    width: 16px;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background: {BG_ELEV};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QSlider::groove:horizontal {{
    height: 5px; background: {BORDER}; border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT}; width: 14px; margin: -5px 0; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT_DIM}; border-radius: 3px; }}

QTableWidget {{
    background: {BG_ELEV};
    alternate-background-color: #182029;
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: {ACCENT_DIM};
}}
QHeaderView::section {{
    background: {BG_CARD};
    color: {TEXT_DIM};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 7px 8px;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.5px;
}}
QTableCornerButton::section {{ background: {BG_CARD}; border: none; }}

QTextEdit, QPlainTextEdit {{
    background: #0a0e14;
    border: 1px solid {BORDER};
    border-radius: 8px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
    color: #cfe3f5;
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_DIM}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_STRONG}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {BG_ELEV}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{
    background: {BG_CARD}; color: {TEXT};
    border: 1px solid {BORDER_STRONG}; padding: 6px; border-radius: 6px;
}}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QProgressBar {{
    background: {BG_ELEV}; border: 1px solid {BORDER}; border-radius: 6px;
    text-align: center; color: {TEXT_DIM};
}}
QProgressBar::chunk {{ background: {ACCENT_DIM}; border-radius: 5px; }}
"""


def waterfall_colormap() -> tuple:
    """Positions and RGBA colours for the waterfall gradient."""
    stops = np.array([0.0, 0.22, 0.45, 0.68, 0.86, 1.0])
    colors = np.array([
        [4, 8, 18, 255],
        [18, 40, 92, 255],
        [22, 108, 148, 255],
        [40, 190, 140, 255],
        [244, 200, 60, 255],
        [255, 92, 60, 255],
    ], dtype=np.ubyte)
    return stops, colors


def apply_pyqtgraph_defaults() -> None:
    import pyqtgraph as pg
    pg.setConfigOption("background", BG_ELEV)
    pg.setConfigOption("foreground", TEXT_DIM)
    # Antialiasing costs ~10x per repaint on multi-thousand-point spectrum
    # traces (measured 80 ms vs 8 ms for 4096 points), which is more than a
    # live display can afford. Curves stay legible without it.
    pg.setConfigOptions(antialias=False, useOpenGL=False, imageAxisOrder="row-major")
