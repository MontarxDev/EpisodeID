"""Light/dark stylesheets for EpisodeID."""

LIGHT = """
QWidget { font-size: 13px; }
QMainWindow, QDialog { background: #f5f6f8; color: #1a1a1a; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
  background: #ffffff; border: 1px solid #c5c7ce; border-radius: 4px; padding: 4px 6px;
}
QPushButton {
  background: #e8eaef; border: 1px solid #c5c7ce; border-radius: 4px; padding: 6px 12px;
}
QPushButton:hover { background: #dce0e8; }
QPushButton#primary {
  background: #2f6fed; color: white; border: 1px solid #2458c4; font-weight: 600;
}
QPushButton#primary:hover { background: #2458c4; }
QPushButton#primary:disabled { background: #9bb6f0; color: #eef; }
QTableWidget {
  background: #ffffff; gridline-color: #dde0e6; border: 1px solid #c5c7ce;
  selection-background-color: #cfe0ff;
}
QHeaderView::section {
  background: #e8eaef; padding: 6px; border: none; border-right: 1px solid #c5c7ce;
  border-bottom: 1px solid #c5c7ce;
}
QProgressBar {
  border: 1px solid #c5c7ce; border-radius: 4px; text-align: center; background: #fff;
}
QProgressBar::chunk { background: #2f6fed; border-radius: 3px; }
QStatusBar { background: #eceef2; }
"""

DARK = """
QWidget { font-size: 13px; color: #e6e6e6; }
QMainWindow, QDialog { background: #1e1f24; color: #e6e6e6; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
  background: #2a2b32; border: 1px solid #3f414a; border-radius: 4px; padding: 4px 6px; color: #e6e6e6;
}
QPushButton {
  background: #33353e; border: 1px solid #4a4d58; border-radius: 4px; padding: 6px 12px; color: #e6e6e6;
}
QPushButton:hover { background: #3d404b; }
QPushButton#primary {
  background: #3b82f6; color: white; border: 1px solid #2563eb; font-weight: 600;
}
QPushButton#primary:hover { background: #2563eb; }
QPushButton#primary:disabled { background: #1e3a5f; color: #89a; }
QTableWidget {
  background: #25262c; gridline-color: #3a3c45; border: 1px solid #3f414a;
  selection-background-color: #1e3a5f; alternate-background-color: #2a2b32;
}
QHeaderView::section {
  background: #2f313a; padding: 6px; border: none; border-right: 1px solid #3f414a;
  border-bottom: 1px solid #3f414a; color: #e6e6e6;
}
QProgressBar {
  border: 1px solid #3f414a; border-radius: 4px; text-align: center; background: #2a2b32; color: #eee;
}
QProgressBar::chunk { background: #3b82f6; border-radius: 3px; }
QStatusBar { background: #17181c; }
QLabel { color: #e6e6e6; }
QCheckBox { color: #e6e6e6; }
QGroupBox { color: #e6e6e6; border: 1px solid #3f414a; margin-top: 8px; padding-top: 8px; }
"""


def stylesheet_for(theme: str, system_dark: bool = False) -> str:
    if theme == "dark":
        return DARK
    if theme == "light":
        return LIGHT
    return DARK if system_dark else LIGHT
