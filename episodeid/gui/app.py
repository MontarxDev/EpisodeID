"""Application bootstrap."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _log_path() -> Path:
    base = Path.home() / ".local" / "share" / "episodeid"
    base.mkdir(parents=True, exist_ok=True)
    return base / "last-run.log"


def _write_log(text: str) -> Path:
    path = _log_path()
    path.write_text(text, encoding="utf-8")
    return path


def run_app(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        msg = (
            f"PySide6 is not installed ({exc}).\n"
            "Install with: pip install 'episodeid[ocr]'"
        )
        print(msg, file=sys.stderr)
        _write_log(msg)
        return 1

    try:
        app = QApplication(argv)
    except Exception as exc:  # pragma: no cover - platform failures
        detail = traceback.format_exc()
        msg = (
            "EpisodeID could not start the Qt GUI.\n\n"
            f"{exc}\n\n"
            "On Linux X11 this is often a missing libxcb-cursor0 package, "
            "or the AppImage failed to load its bundled Qt platform plugin.\n"
            "Try from a terminal for full errors, or install:\n"
            "  sudo apt install libxcb-cursor0\n"
        )
        print(msg, file=sys.stderr)
        print(detail, file=sys.stderr)
        path = _write_log(msg + "\n" + detail)
        print(f"Log written to {path}", file=sys.stderr)
        return 1

    try:
        from episodeid.gui.main_window import MainWindow

        app.setApplicationName("EpisodeID")
        app.setOrganizationName("EpisodeID")
        window = MainWindow()
        window.show()
        return app.exec()
    except Exception as exc:
        detail = traceback.format_exc()
        msg = f"EpisodeID crashed while opening the main window:\n{exc}\n"
        print(msg, file=sys.stderr)
        print(detail, file=sys.stderr)
        path = _write_log(msg + "\n" + detail)
        print(f"Log written to {path}", file=sys.stderr)
        return 1
