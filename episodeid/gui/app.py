"""Application bootstrap."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from episodeid.gui.main_window import MainWindow


def run_app(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    app = QApplication(argv)
    app.setApplicationName("EpisodeID")
    app.setOrganizationName("EpisodeID")
    window = MainWindow()
    window.show()
    return app.exec()
