"""Background workers for non-blocking GUI operations."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from episodeid.config import Settings
from episodeid.models import Episode, ProgressEvent, RenamePlanRow, SeriesInfo
from episodeid.pipeline import scan_and_identify
from episodeid.renamer import apply_renames


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

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            plan = scan_and_identify(
                folder=self.folder,
                series=self.series,
                episodes=self.episodes,
                settings=self.settings,
                progress=lambda ev: self.progress.emit(ev),
                cancel_check=lambda: self._cancel,
            )
            self.finished.emit(plan)
        except Exception as exc:
            self.failed.emit(str(exc))


class ApplyWorker(QObject):
    finished = Signal(object, object)  # successes, failures
    failed = Signal(str)

    def __init__(self, rows: list[RenamePlanRow], undo_dir: Path, parent=None):
        super().__init__(parent)
        self.rows = rows
        self.undo_dir = undo_dir

    def run(self) -> None:
        try:
            ok, err = apply_renames(self.rows, undo_dir=self.undo_dir)
            self.finished.emit(ok, err)
        except Exception as exc:
            self.failed.emit(str(exc))


def start_worker(worker: QObject, thread: QThread) -> None:
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    if hasattr(worker, "failed"):
        worker.failed.connect(thread.quit)
    thread.start()
