"""Light/dark stylesheets for EpisodeID — high contrast for tables."""

LIGHT = """
QWidget { font-size: 13px; color: #111111; }
QMainWindow, QDialog { background: #f5f6f8; color: #111111; }
QLabel { color: #111111; }
QCheckBox { color: #111111; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
  background: #ffffff; color: #111111;
  border: 1px solid #c5c7ce; border-radius: 4px; padding: 4px 6px;
  selection-background-color: #cfe0ff; selection-color: #111111;
}
QPushButton {
  background: #e8eaef; color: #111111;
  border: 1px solid #c5c7ce; border-radius: 4px; padding: 6px 12px;
}
QPushButton:hover { background: #dce0e8; }
QPushButton#primary {
  background: #2f6fed; color: #ffffff; border: 1px solid #2458c4; font-weight: 600;
}
QPushButton#primary:hover { background: #2458c4; color: #ffffff; }
QPushButton#primary:disabled { background: #9bb6f0; color: #eef; }
QTableWidget {
  background: #ffffff; color: #111111;
  gridline-color: #dde0e6; border: 1px solid #c5c7ce;
  selection-background-color: #cfe0ff; selection-color: #111111;
  alternate-background-color: #f7f8fa;
}
QTableWidget::item { color: #111111; }
QHeaderView::section {
  background: #e8eaef; color: #111111; padding: 6px; border: none;
  border-right: 1px solid #c5c7ce; border-bottom: 1px solid #c5c7ce;
}
QProgressBar {
  border: 1px solid #c5c7ce; border-radius: 4px; text-align: center;
  background: #fff; color: #111111;
}
QProgressBar::chunk { background: #2f6fed; border-radius: 3px; }
QStatusBar { background: #eceef2; color: #111111; }
QListWidget {
  background: #ffffff; color: #111111; border: 1px solid #c5c7ce;
}
"""

DARK = """
QWidget { font-size: 13px; color: #f0f0f0; }
QMainWindow, QDialog { background: #1e1f24; color: #f0f0f0; }
QLabel { color: #f0f0f0; }
QCheckBox { color: #f0f0f0; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
  background: #2a2b32; border: 1px solid #3f414a; border-radius: 4px;
  padding: 4px 6px; color: #f0f0f0;
  selection-background-color: #1e3a5f; selection-color: #f0f0f0;
}
QPushButton {
  background: #33353e; border: 1px solid #4a4d58; border-radius: 4px;
  padding: 6px 12px; color: #f0f0f0;
}
QPushButton:hover { background: #3d404b; }
QPushButton#primary {
  background: #3b82f6; color: #ffffff; border: 1px solid #2563eb; font-weight: 600;
}
QPushButton#primary:hover { background: #2563eb; color: #ffffff; }
QPushButton#primary:disabled { background: #1e3a5f; color: #89a; }
QTableWidget {
  background: #25262c; color: #f0f0f0;
  gridline-color: #3a3c45; border: 1px solid #3f414a;
  selection-background-color: #1e3a5f; selection-color: #f0f0f0;
  alternate-background-color: #2a2b32;
}
QTableWidget::item { color: #f0f0f0; }
QHeaderView::section {
  background: #2f313a; padding: 6px; border: none;
  border-right: 1px solid #3f414a; border-bottom: 1px solid #3f414a; color: #f0f0f0;
}
QProgressBar {
  border: 1px solid #3f414a; border-radius: 4px; text-align: center;
  background: #2a2b32; color: #f0f0f0;
}
QProgressBar::chunk { background: #3b82f6; border-radius: 3px; }
QStatusBar { background: #17181c; color: #f0f0f0; }
QGroupBox { color: #f0f0f0; border: 1px solid #3f414a; margin-top: 8px; padding-top: 8px; }
QListWidget {
  background: #25262c; color: #f0f0f0; border: 1px solid #3f414a;
}
"""


def stylesheet_for(theme: str, system_dark: bool = False) -> str:
    if theme == "dark":
        return DARK
    if theme == "light":
        return LIGHT
    # system: if unknown, prefer light for readability on mixed Mint themes
    return DARK if system_dark else LIGHT


def confidence_colors(theme: str, system_dark: bool = False) -> dict[str, tuple]:
    """Return (bg, fg) QColor-compatible hex pairs for confidence bands."""
    dark = theme == "dark" or (theme == "system" and system_dark)
    if dark:
        return {
            "high": ("#1b4332", "#e8f5e9"),
            "mid": ("#5c4d12", "#fff8e1"),
            "low": ("#5c1a1a", "#ffebee"),
            "error": ("#5c1a1a", "#ffebee"),
        }
    return {
        "high": ("#c8e6c9", "#111111"),
        "mid": ("#fff59d", "#111111"),
        "low": ("#ffcdd2", "#111111"),
        "error": ("#ef9a9a", "#111111"),
    }
