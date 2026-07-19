"""Modern light/dark stylesheets for EpisodeID — high-contrast controls."""

from __future__ import annotations

from pathlib import Path


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent / "assets"


def _asset_url(name: str) -> str:
    """Filesystem path for QSS url(...). Empty if missing."""
    p = _assets_dir() / name
    if not p.exists():
        return ""
    # Qt QSS urls prefer forward slashes
    return p.as_posix()


def _checkbox_block(*, checked_bg: str, unchecked_bg: str, border: str, hover_border: str) -> str:
    # Prefer PNG (reliable in Qt QSS); SVG as fallback
    check = _asset_url("check.png") or _asset_url("check.svg")
    image_rule = f"image: url({check});" if check else "image: none;"
    return f"""
QCheckBox {{
  spacing: 8px;
  outline: none;
}}
QCheckBox::indicator {{
  width: 18px;
  height: 18px;
  border-radius: 5px;
  border: 2px solid {border};
  background: {unchecked_bg};
}}
QCheckBox::indicator:hover {{
  border: 2px solid {hover_border};
}}
QCheckBox::indicator:checked {{
  background: {checked_bg};
  border: 2px solid {checked_bg};
  {image_rule}
}}
QCheckBox::indicator:checked:hover {{
  background: {hover_border};
  border: 2px solid {hover_border};
}}
QCheckBox::indicator:disabled {{
  background: #c5c9d2;
  border: 2px solid #a8adb8;
}}
QCheckBox::indicator:checked:disabled {{
  background: #8b93a7;
  border: 2px solid #8b93a7;
}}
/* Table select column — same high-contrast treatment */
QTableView::indicator, QTableWidget::indicator {{
  width: 18px;
  height: 18px;
  border-radius: 5px;
  border: 2px solid {border};
  background: {unchecked_bg};
}}
QTableView::indicator:hover, QTableWidget::indicator:hover {{
  border: 2px solid {hover_border};
}}
QTableView::indicator:checked, QTableWidget::indicator:checked {{
  background: {checked_bg};
  border: 2px solid {checked_bg};
  {image_rule}
}}
QTableView::indicator:checked:hover, QTableWidget::indicator:checked:hover {{
  background: {hover_border};
  border: 2px solid {hover_border};
}}
QTableView::indicator:disabled, QTableWidget::indicator:disabled {{
  background: #c5c9d2;
  border: 2px solid #a8adb8;
}}
"""


def _light_sheet() -> str:
    accent = "#2563eb"
    accent_hover = "#1d4ed8"
    text = "#0f172a"
    muted = "#64748b"
    border = "#d0d5e0"
    surface = "#ffffff"
    page = "#eef1f7"
    input_bg = "#ffffff"
    return f"""
* {{
  font-family: "Inter", "Segoe UI", "Ubuntu", "Noto Sans", sans-serif;
  font-size: 13px;
}}
QMainWindow, QDialog {{
  background: {page};
  color: {text};
}}
QWidget {{
  color: {text};
  background: transparent;
}}
QLabel {{
  color: {text};
  background: transparent;
}}
QLabel#appTitle {{
  font-size: 20px;
  font-weight: 700;
  color: {text};
  letter-spacing: -0.3px;
}}
QLabel#appSubtitle {{
  font-size: 12px;
  color: {muted};
}}
QLabel#coverageLabel {{
  font-weight: 600;
  color: {accent};
  background: #dbeafe;
  border: 1px solid #93c5fd;
  border-radius: 8px;
  padding: 8px 12px;
}}
QLabel#legend {{
  color: {muted};
  font-size: 12px;
}}
QLabel#progressLabel {{
  color: {muted};
}}
{_checkbox_block(checked_bg=accent, unchecked_bg="#ffffff", border=accent, hover_border=accent_hover)}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
  background: {input_bg};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  padding: 7px 10px;
  min-height: 18px;
  selection-background-color: #bfdbfe;
  selection-color: {text};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {{
  border: 1.5px solid {accent};
  background: #ffffff;
}}
QComboBox::drop-down {{
  border: none;
  width: 24px;
}}
QComboBox QAbstractItemView {{
  background: {surface};
  color: {text};
  border: 1px solid {border};
  selection-background-color: #dbeafe;
  selection-color: {text};
  outline: none;
}}
QPushButton {{
  background: {surface};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  padding: 8px 14px;
  font-weight: 500;
}}
QPushButton:hover {{
  background: #f8fafc;
  border: 1px solid #b6bdcc;
}}
QPushButton:pressed {{
  background: #e8ecf4;
}}
QPushButton#primary {{
  background: {accent};
  color: #ffffff;
  border: 1px solid {accent_hover};
  font-weight: 600;
  padding: 8px 16px;
}}
QPushButton#primary:hover {{
  background: {accent_hover};
  color: #ffffff;
  border: 1px solid #1e40af;
}}
QPushButton#primary:disabled {{
  background: #93c5fd;
  color: #eff6ff;
  border: 1px solid #93c5fd;
}}
QPushButton:disabled {{
  color: #94a3b8;
  background: #f1f5f9;
}}
QTableWidget {{
  background: {surface};
  color: {text};
  gridline-color: #e8ebf2;
  border: 1px solid {border};
  border-radius: 10px;
  selection-background-color: #dbeafe;
  selection-color: {text};
  alternate-background-color: #f8fafc;
  outline: none;
}}
QTableWidget::item {{
  color: {text};
  padding: 4px 6px;
}}
QTableWidget::item:selected {{
  background: #dbeafe;
  color: {text};
}}
QHeaderView::section {{
  background: #f1f5f9;
  color: {text};
  padding: 8px 6px;
  border: none;
  border-right: 1px solid {border};
  border-bottom: 1px solid {border};
  font-weight: 600;
}}
QProgressBar {{
  border: 1px solid {border};
  border-radius: 8px;
  text-align: center;
  background: {surface};
  color: {text};
  min-height: 18px;
}}
QProgressBar::chunk {{
  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b82f6, stop:1 #2563eb);
  border-radius: 7px;
}}
QStatusBar {{
  background: #e8ecf4;
  color: {muted};
  border-top: 1px solid {border};
}}
QListWidget {{
  background: {surface};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  outline: none;
}}
QListWidget::item {{
  padding: 6px 8px;
  border-radius: 4px;
}}
QListWidget::item:selected {{
  background: #dbeafe;
  color: {text};
}}
QGroupBox {{
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  margin-top: 10px;
  padding-top: 12px;
  background: {surface};
}}
QGroupBox::title {{
  subcontrol-origin: margin;
  left: 10px;
  padding: 0 4px;
  color: {muted};
  font-weight: 600;
}}
QTabWidget::pane {{
  border: 1px solid {border};
  border-radius: 8px;
  background: {surface};
  top: -1px;
}}
QTabBar::tab {{
  background: transparent;
  color: {muted};
  padding: 8px 14px;
  margin-right: 2px;
  border-top-left-radius: 8px;
  border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{
  background: {surface};
  color: {text};
  font-weight: 600;
  border: 1px solid {border};
  border-bottom: 1px solid {surface};
}}
QTabBar::tab:hover:!selected {{
  color: {text};
  background: #e8ecf4;
}}
QScrollBar:vertical {{
  background: transparent;
  width: 10px;
  margin: 2px;
}}
QScrollBar::handle:vertical {{
  background: #c5cad6;
  border-radius: 5px;
  min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
  background: #a8b0c0;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
  height: 0;
}}
QScrollBar:horizontal {{
  background: transparent;
  height: 10px;
}}
QScrollBar::handle:horizontal {{
  background: #c5cad6;
  border-radius: 5px;
  min-width: 24px;
}}
QToolTip {{
  background: #1e293b;
  color: #f8fafc;
  border: none;
  padding: 6px 8px;
  border-radius: 6px;
}}
QDialogButtonBox QPushButton {{
  min-width: 80px;
}}
"""


def _dark_sheet() -> str:
    accent = "#3b82f6"
    accent_hover = "#60a5fa"
    text = "#f1f5f9"
    muted = "#94a3b8"
    border = "#3f4554"
    surface = "#252830"
    page = "#181a20"
    input_bg = "#2c303a"
    return f"""
* {{
  font-family: "Inter", "Segoe UI", "Ubuntu", "Noto Sans", sans-serif;
  font-size: 13px;
}}
QMainWindow, QDialog {{
  background: {page};
  color: {text};
}}
QWidget {{
  color: {text};
  background: transparent;
}}
QLabel {{
  color: {text};
  background: transparent;
}}
QLabel#appTitle {{
  font-size: 20px;
  font-weight: 700;
  color: {text};
}}
QLabel#appSubtitle {{
  font-size: 12px;
  color: {muted};
}}
QLabel#coverageLabel {{
  font-weight: 600;
  color: #93c5fd;
  background: #1e3a5f;
  border: 1px solid #2563eb;
  border-radius: 8px;
  padding: 8px 12px;
}}
QLabel#legend {{
  color: {muted};
  font-size: 12px;
}}
QLabel#progressLabel {{
  color: {muted};
}}
{_checkbox_block(checked_bg=accent, unchecked_bg="#1e2128", border=accent, hover_border=accent_hover)}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
  background: {input_bg};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  padding: 7px 10px;
  selection-background-color: #1e3a5f;
  selection-color: {text};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
  border: 1.5px solid {accent};
}}
QComboBox QAbstractItemView {{
  background: {surface};
  color: {text};
  border: 1px solid {border};
  selection-background-color: #1e3a5f;
  selection-color: {text};
}}
QPushButton {{
  background: {surface};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  padding: 8px 14px;
  font-weight: 500;
}}
QPushButton:hover {{
  background: #323642;
  border: 1px solid #555b6c;
}}
QPushButton#primary {{
  background: {accent};
  color: #ffffff;
  border: 1px solid #2563eb;
  font-weight: 600;
}}
QPushButton#primary:hover {{
  background: {accent_hover};
  color: #0f172a;
  border: 1px solid {accent_hover};
}}
QPushButton#primary:disabled {{
  background: #1e3a5f;
  color: #64748b;
  border: 1px solid #1e3a5f;
}}
QTableWidget {{
  background: {surface};
  color: {text};
  gridline-color: #343844;
  border: 1px solid {border};
  border-radius: 10px;
  selection-background-color: #1e3a5f;
  selection-color: {text};
  alternate-background-color: #2a2e38;
}}
QTableWidget::item {{
  color: {text};
  padding: 4px 6px;
}}
QHeaderView::section {{
  background: #2a2e38;
  color: {text};
  padding: 8px 6px;
  border: none;
  border-right: 1px solid {border};
  border-bottom: 1px solid {border};
  font-weight: 600;
}}
QProgressBar {{
  border: 1px solid {border};
  border-radius: 8px;
  text-align: center;
  background: {input_bg};
  color: {text};
  min-height: 18px;
}}
QProgressBar::chunk {{
  background: {accent};
  border-radius: 7px;
}}
QStatusBar {{
  background: #12141a;
  color: {muted};
  border-top: 1px solid {border};
}}
QListWidget {{
  background: {surface};
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
}}
QListWidget::item:selected {{
  background: #1e3a5f;
  color: {text};
}}
QGroupBox {{
  color: {text};
  border: 1px solid {border};
  border-radius: 8px;
  margin-top: 10px;
  padding-top: 12px;
  background: {surface};
}}
QTabWidget::pane {{
  border: 1px solid {border};
  border-radius: 8px;
  background: {surface};
}}
QTabBar::tab {{
  background: transparent;
  color: {muted};
  padding: 8px 14px;
}}
QTabBar::tab:selected {{
  background: {surface};
  color: {text};
  font-weight: 600;
}}
QScrollBar:vertical {{
  background: transparent;
  width: 10px;
}}
QScrollBar::handle:vertical {{
  background: #4a5060;
  border-radius: 5px;
  min-height: 24px;
}}
QToolTip {{
  background: #0f172a;
  color: #f8fafc;
  border: 1px solid {border};
  padding: 6px 8px;
  border-radius: 6px;
}}
"""


def stylesheet_for(theme: str, system_dark: bool = False) -> str:
    if theme == "dark":
        return _dark_sheet()
    if theme == "light":
        return _light_sheet()
    return _dark_sheet() if system_dark else _light_sheet()


def confidence_colors(theme: str, system_dark: bool = False) -> dict[str, tuple]:
    """Return (bg, fg) QColor-compatible hex pairs for confidence bands."""
    dark = theme == "dark" or (theme == "system" and system_dark)
    if dark:
        return {
            "high": ("#14532d", "#dcfce7"),
            "mid": ("#713f12", "#fef9c3"),
            "low": ("#7f1d1d", "#fee2e2"),
            "error": ("#7f1d1d", "#fecaca"),
            "select": ("#1e2128", "#f1f5f9"),
        }
    return {
        "high": ("#bbf7d0", "#14532d"),
        "mid": ("#fef08a", "#713f12"),
        "low": ("#fecaca", "#7f1d1d"),
        "error": ("#fca5a5", "#7f1d1d"),
        "select": ("#ffffff", "#0f172a"),
    }
