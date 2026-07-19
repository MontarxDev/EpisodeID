"""Background workers for non-blocking GUI operations."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from episodeid.config import Settings
from episodeid.models import Episode, ProgressEvent, RenamePlanRow, SeriesInfo
from episodeid.pipeline import scan_and_identify
from episodeid.renamer import apply_all_selected
from episodeid.session_log import SessionLog


class IdentifyWorker(QObject):
    progress = Signal(object)  # ProgressEvent
    finished = Signal(object)  # list[RenamePlanRow]
    failed = Signal(str)

    def __init__(
        self,
        folder: Path,
        series: SeriesInfo,
        episodes: list[Episode],
        settings: Settings,
        parent=None,
    ):
        super().__init__(parent)
        self.folder = folder
        self.series = series
        self.episodes = episodes
        self.settings = settings
        self._cancel = False
        self.session_log: SessionLog | None = None

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            self.session_log = SessionLog("scan")
            plan = scan_and_identify(
                folder=self.folder,
                series=self.series,
                episodes=self.episodes,
                settings=self.settings,
                progress=lambda ev: self.progress.emit(ev),
                cancel_check=lambda: self._cancel,
                session_log=self.session_log,
            )
            self.finished.emit(plan)
        except Exception as exc:
            if self.session_log:
                self.session_log.log("error", str(exc))
            self.failed.emit(str(exc))


class ApplyWorker(QObject):
    finished = Signal(object, object)  # successes, failures
    failed = Signal(str)

    def __init__(
        self,
        rows: list[RenamePlanRow],
        undo_dir: Path,
        session_log: SessionLog | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.rows = rows
        self.undo_dir = undo_dir
        self.session_log = session_log

    def run(self) -> None:
        try:
            if self.session_log:
                self.session_log.log(
                    "apply_start",
                    f"Applying {sum(1 for r in self.rows if r.selected)} selected row(s)",
                )
            ok, err = apply_all_selected(self.rows, undo_dir=self.undo_dir)
            if self.session_log:
                self.session_log.finalize_apply(successes=ok, failures=err)
            self.finished.emit(ok, err)
        except Exception as exc:
            if self.session_log:
                self.session_log.log("error", f"Apply failed: {exc}")
            self.failed.emit(str(exc))


def start_worker(worker: QObject, thread: QThread) -> None:
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    if hasattr(worker, "failed"):
        worker.failed.connect(thread.quit)
    thread.start()
