"""Readable, modern light/dark stylesheets for EpisodeID.

Light theme is the default and prioritizes contrast: solid backgrounds
(no transparency that inherits a dark system chrome), charcoal text on
soft off-white surfaces, and clear blue accents.
"""

from __future__ import annotations

from pathlib import Path


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent / "assets"


def _asset_url(name: str) -> str:
    p = _assets_dir() / name
    if not p.exists():
        return ""
    return p.as_posix()


def _checkbox_qss(
    *,
    unchecked_bg: str,
    unchecked_border: str,
    checked_bg: str,
    checked_border: str,
    hover_border: str,
) -> str:
    check = _asset_url("check.png") or _asset_url("check.svg")
    image_rule = f"image: url({check});" if check else "image: none;"
    return f"""
QCheckBox {{
  spacing: 8px;
  outline: none;
  background: transparent;
}}
QCheckBox::indicator {{
  width: 18px;
  height: 18px;
  border-radius: 4px;
  border: 2px solid {unchecked_border};
  background: {unchecked_bg};
}}
QCheckBox::indicator:hover {{
  border: 2px solid {hover_border};
}}
QCheckBox::indicator:checked {{
  background: {checked_bg};
  border: 2px solid {checked_border};
  {image_rule}
}}
QCheckBox::indicator:checked:hover {{
  background: {hover_border};
  border: 2px solid {hover_border};
}}
QCheckBox::indicator:disabled {{
  background: #e5e7eb;
  border: 2px solid #d1d5db;
}}
QTableView::indicator, QTableWidget::indicator {{
  width: 18px;
  height: 18px;
  border-radius: 4px;
  border: 2px solid {unchecked_border};
  background: {unchecked_bg};
}}
QTableView::indicator:hover, QTableWidget::indicator:hover {{
  border: 2px solid {hover_border};
}}
QTableView::indicator:checked, QTableWidget::indicator:checked {{
  background: {checked_bg};
  border: 2px solid {checked_border};
  {image_rule}
}}
QTableView::indicator:checked:hover, QTableWidget::indicator:checked:hover {{
  background: {hover_border};
  border: 2px solid {hover_border};
}}
"""


def _light_sheet() -> str:
    """Airy light UI: soft gray page, white panels, near-black text."""
    # Palette — high contrast, modern SaaS-style light
    page = "#f4f6fa"  # soft cool gray page (not pure white, not dark)
    surface = "#ffffff"  # cards / inputs / table
    text = "#1c2333"  # charcoal body text
    muted = "#5b657a"  # secondary labels (still readable)
    border = "#cdd3e0"  # visible but soft borders
    accent = "#2f6fed"  # primary blue
    accent_hover = "#1f5ad9"
    accent_soft = "#e8f0fe"  # light blue chip / selection
    header = "#eef1f7"

    return f"""
* {{
  font-family: "Ubuntu", "Noto Sans", "Segoe UI", "DejaVu Sans", sans-serif;
  font-size: 13px;
}}

/* Solid page — never transparent (avoids dark system bleed) */
QMainWindow, QDialog, QWidget#qt_scrollarea_viewport {{
  background-color: {page};
  color: {text};
}}
QWidget {{
  color: {text};
  background-color: {page};
}}
QMainWindow > QWidget {{
  background-color: {page};
}}

QLabel {{
  color: {text};
  background-color: transparent;
}}
QLabel#appTitle {{
  font-size: 22px;
  font-weight: 700;
  color: {text};
  background-color: transparent;
}}
QLabel#appSubtitle {{
  font-size: 12px;
  color: {muted};
  background-color: transparent;
}}
QLabel#coverageLabel {{
  font-weight: 600;
  font-size: 12px;
  color: #1e4bb8;
  background-color: {accent_soft};
  border: 1px solid #b6d0fc;
  border-radius: 8px;
  padding: 8px 12px;
}}
QLabel#legend, QLabel#progressLabel {{
  color: {muted};
  background-color: transparent;
  font-size: 12px;
}}

{_checkbox_qss(
    unchecked_bg="#ffffff",
    unchecked_border=accent,
    checked_bg=accent,
    checked_border=accent_hover,
    hover_border=accent_hover,
)}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
  background-color: {surface};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  padding: 7px 10px;
  selection-background-color: {accent_soft};
  selection-color: {text};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
  border: 2px solid {accent};
  padding: 6px 9px;
}}
QComboBox::drop-down {{
  border: none;
  width: 22px;
}}
QComboBox QAbstractItemView {{
  background-color: {surface};
  color: {text};
  border: 1px solid {border};
  selection-background-color: {accent_soft};
  selection-color: {text};
  outline: none;
}}

QPushButton {{
  background-color: {surface};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  padding: 8px 14px;
  font-weight: 500;
}}
QPushButton:hover {{
  background-color: #f0f3f9;
  border: 1px solid #b0b8c9;
}}
QPushButton:pressed {{
  background-color: #e4e9f2;
}}
QPushButton#primary {{
  background-color: {accent};
  color: #ffffff;
  border: 1px solid {accent_hover};
  font-weight: 600;
}}
QPushButton#primary:hover {{
  background-color: {accent_hover};
  color: #ffffff;
}}
QPushButton#primary:disabled {{
  background-color: #a8c4f5;
  color: #ffffff;
  border: 1px solid #a8c4f5;
}}
QPushButton:disabled {{
  color: #9aa3b5;
  background-color: #eceff5;
  border: 1px solid #dde1ea;
}}

QTableWidget {{
  background-color: {surface};
  color: {text};
  gridline-color: #e6eaf2;
  border: 1px solid {border};
  border-radius: 10px;
  selection-background-color: {accent_soft};
  selection-color: {text};
  alternate-background-color: #f9fafc;
  outline: none;
}}
QTableWidget::item {{
  color: {text};
  padding: 4px 6px;
}}
QTableWidget::item:selected {{
  background-color: {accent_soft};
  color: {text};
}}
QHeaderView::section {{
  background-color: {header};
  color: {text};
  padding: 9px 8px;
  border: none;
  border-right: 1px solid {border};
  border-bottom: 1px solid {border};
  font-weight: 600;
}}
QTableCornerButton::section {{
  background-color: {header};
  border: none;
}}

QProgressBar {{
  border: 1px solid {border};
  border-radius: 8px;
  text-align: center;
  background-color: {surface};
  color: {text};
  min-height: 20px;
}}
QProgressBar::chunk {{
  background-color: {accent};
  border-radius: 7px;
}}

QStatusBar {{
  background-color: #e8ecf4;
  color: {muted};
  border-top: 1px solid {border};
}}
QStatusBar QLabel {{
  color: {muted};
  background-color: transparent;
}}

QListWidget {{
  background-color: {surface};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  outline: none;
}}
QListWidget::item {{
  color: {text};
  padding: 6px 10px;
}}
QListWidget::item:selected {{
  background-color: {accent_soft};
  color: {text};
}}
QListWidget::item:hover {{
  background-color: #f0f3f9;
}}

QGroupBox {{
  color: {text};
  background-color: {surface};
  border: 1px solid {border};
  border-radius: 8px;
  margin-top: 12px;
  padding-top: 14px;
  font-weight: 600;
}}
QGroupBox::title {{
  subcontrol-origin: margin;
  left: 12px;
  padding: 0 6px;
  color: {muted};
  background-color: {surface};
}}

QTabWidget::pane {{
  border: 1px solid {border};
  border-radius: 8px;
  background-color: {surface};
  top: -1px;
}}
QTabBar::tab {{
  background-color: {page};
  color: {muted};
  padding: 9px 16px;
  margin-right: 2px;
  border-top-left-radius: 8px;
  border-top-right-radius: 8px;
  border: 1px solid transparent;
}}
QTabBar::tab:selected {{
  background-color: {surface};
  color: {text};
  font-weight: 600;
  border: 1px solid {border};
  border-bottom: 1px solid {surface};
}}
QTabBar::tab:hover:!selected {{
  color: {text};
  background-color: #e8ecf4;
}}

QScrollBar:vertical {{
  background: {page};
  width: 12px;
  margin: 0;
}}
QScrollBar::handle:vertical {{
  background: #b8c0d0;
  border-radius: 6px;
  min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
  background: #9aa5b8;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
  height: 0;
}}
QScrollBar:horizontal {{
  background: {page};
  height: 12px;
}}
QScrollBar::handle:horizontal {{
  background: #b8c0d0;
  border-radius: 6px;
}}

QToolTip {{
  background-color: #1c2333;
  color: #ffffff;
  border: none;
  padding: 8px 10px;
  border-radius: 6px;
}}
QMessageBox {{
  background-color: {surface};
  color: {text};
}}
QMessageBox QLabel {{
  color: {text};
  background-color: transparent;
}}
QDialogButtonBox QPushButton {{
  min-width: 88px;
}}
"""


def _dark_sheet() -> str:
    """Readable dark: elevated gray surfaces, bright body text (not pure black)."""
    page = "#2b303b"  # medium slate — not near-black
    surface = "#353b48"  # elevated panels
    text = "#f0f2f5"  # near-white body
    muted = "#c5cbd6"  # secondary still bright enough
    border = "#4a5263"
    accent = "#5b9cff"
    accent_hover = "#7eb0ff"
    accent_soft = "#3a4f73"
    header = "#3a4150"

    return f"""
* {{
  font-family: "Ubuntu", "Noto Sans", "Segoe UI", "DejaVu Sans", sans-serif;
  font-size: 13px;
}}
QMainWindow, QDialog {{
  background-color: {page};
  color: {text};
}}
QWidget {{
  color: {text};
  background-color: {page};
}}
QLabel {{
  color: {text};
  background-color: transparent;
}}
QLabel#appTitle {{
  font-size: 22px;
  font-weight: 700;
  color: {text};
}}
QLabel#appSubtitle {{
  font-size: 12px;
  color: {muted};
}}
QLabel#coverageLabel {{
  font-weight: 600;
  color: #d6e6ff;
  background-color: {accent_soft};
  border: 1px solid {accent};
  border-radius: 8px;
  padding: 8px 12px;
}}
QLabel#legend, QLabel#progressLabel {{
  color: {muted};
}}

{_checkbox_qss(
    unchecked_bg="#2b303b",
    unchecked_border=accent,
    checked_bg=accent,
    checked_border=accent_hover,
    hover_border=accent_hover,
)}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
  background-color: {surface};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  padding: 7px 10px;
  selection-background-color: {accent_soft};
  selection-color: {text};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
  border: 2px solid {accent};
  padding: 6px 9px;
}}
QComboBox QAbstractItemView {{
  background-color: {surface};
  color: {text};
  selection-background-color: {accent_soft};
  selection-color: {text};
}}

QPushButton {{
  background-color: {surface};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  padding: 8px 14px;
  font-weight: 500;
}}
QPushButton:hover {{
  background-color: #404859;
  border: 1px solid #6a7388;
}}
QPushButton#primary {{
  background-color: {accent};
  color: #0f1419;
  border: 1px solid {accent_hover};
  font-weight: 600;
}}
QPushButton#primary:hover {{
  background-color: {accent_hover};
  color: #0f1419;
}}
QPushButton#primary:disabled {{
  background-color: #4a5f82;
  color: #a8b4c8;
}}

QTableWidget {{
  background-color: {surface};
  color: {text};
  gridline-color: #454d5e;
  border: 1px solid {border};
  border-radius: 10px;
  selection-background-color: {accent_soft};
  selection-color: {text};
  alternate-background-color: #3a4150;
}}
QTableWidget::item {{
  color: {text};
}}
QHeaderView::section {{
  background-color: {header};
  color: {text};
  padding: 9px 8px;
  border: none;
  border-right: 1px solid {border};
  border-bottom: 1px solid {border};
  font-weight: 600;
}}

QProgressBar {{
  border: 1px solid {border};
  border-radius: 8px;
  text-align: center;
  background-color: {surface};
  color: {text};
  min-height: 20px;
}}
QProgressBar::chunk {{
  background-color: {accent};
  border-radius: 7px;
}}

QStatusBar {{
  background-color: #242830;
  color: {muted};
  border-top: 1px solid {border};
}}
QListWidget {{
  background-color: {surface};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
}}
QListWidget::item:selected {{
  background-color: {accent_soft};
  color: {text};
}}
QGroupBox {{
  color: {text};
  background-color: {surface};
  border: 1px solid {border};
  border-radius: 8px;
  margin-top: 12px;
  padding-top: 14px;
}}
QTabWidget::pane {{
  border: 1px solid {border};
  background-color: {surface};
  border-radius: 8px;
}}
QTabBar::tab {{
  background-color: {page};
  color: {muted};
  padding: 9px 16px;
}}
QTabBar::tab:selected {{
  background-color: {surface};
  color: {text};
  font-weight: 600;
}}
QScrollBar:vertical {{
  background: {page};
  width: 12px;
}}
QScrollBar::handle:vertical {{
  background: #5a6375;
  border-radius: 6px;
  min-height: 28px;
}}
QToolTip {{
  background-color: #1a1e26;
  color: #ffffff;
  border: 1px solid {border};
  padding: 8px 10px;
}}
QMessageBox {{
  background-color: {surface};
  color: {text};
}}
"""


def stylesheet_for(theme: str, system_dark: bool = False) -> str:
    """Resolve theme. 'system' follows OS, but light is preferred when unknown."""
    t = (theme or "light").strip().lower()
    if t == "dark":
        return _dark_sheet()
    if t == "system":
        return _dark_sheet() if system_dark else _light_sheet()
    return _light_sheet()


def confidence_colors(theme: str, system_dark: bool = False) -> dict[str, tuple]:
    """(bg, fg) for table confidence bands — always high-contrast pairs."""
    t = (theme or "light").strip().lower()
    dark = t == "dark" or (t == "system" and system_dark)
    if dark:
        return {
            # Pastel-on-dark with bright text
            "high": ("#1f4d36", "#e8fff0"),
            "mid": ("#5c4a12", "#fff8d6"),
            "low": ("#5c2222", "#ffe8e8"),
            "error": ("#6b1f1f", "#ffd6d6"),
            "select": ("#353b48", "#f0f2f5"),
        }
    return {
        # Soft pastels with dark charcoal text (readable)
        "high": ("#d1fae5", "#064e3b"),
        "mid": ("#fef3c7", "#78350f"),
        "low": ("#fee2e2", "#7f1d1d"),
        "error": ("#fecaca", "#7f1d1d"),
        "select": ("#ffffff", "#1c2333"),
    }
