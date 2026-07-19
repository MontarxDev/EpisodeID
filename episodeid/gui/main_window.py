"""Main EpisodeID window."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from episodeid import __app_name__, __version__
from episodeid.config import (
    Settings,
    get_tmdb_api_key,
    load_settings,
    save_settings,
    undo_dir,
)
from episodeid.deps import summary_text
from episodeid.gui.settings_dialog import SettingsDialog
from episodeid.gui.styles import confidence_colors, stylesheet_for
from episodeid.gui.workers import ApplyWorker, IdentifyWorker
from episodeid.metadata import TMDBClient, TMDBError
from episodeid.models import ProgressEvent, RenamePlanRow, SeriesInfo
from episodeid.renamer import (
    DEFAULT_FORMAT,
    apply_all_selected,
    export_csv,
    export_json,
    format_new_name,
    season_dir_name,
    undo_last,
)

COL_SEL, COL_ORIG, COL_CODE, COL_TITLE, COL_CONF, COL_NEW, COL_TARGET = range(7)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.resize(1180, 720)
        self.settings = load_settings()
        self.series: SeriesInfo | None = None
        self.episodes = []
        self.plan: list[RenamePlanRow] = []
        self._thread: QThread | None = None
        self._worker = None
        self._session_log = None  # SessionLog from last scan
        self._search_results: list[SeriesInfo] = []

        self._build_ui()
        self._apply_theme()
        self.statusBar().showMessage(summary_text())

        if self.settings.last_folder:
            self.folder_edit.setText(self.settings.last_folder)
        if self.settings.last_series_id and self.settings.last_series_name:
            self.series = SeriesInfo(
                id=self.settings.last_series_id,
                name=self.settings.last_series_name,
            )
            self.series_label.setText(f"Selected: {self.series.display_name()}  [id {self.series.id}]")

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Toolbar row
        top = QHBoxLayout()
        title = QLabel(f"<b>{__app_name__}</b>")
        title.setStyleSheet("font-size: 18px;")
        top.addWidget(title)
        top.addStretch()
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        top.addWidget(self.settings_btn)
        root.addLayout(top)

        # Series search
        series_row = QHBoxLayout()
        series_row.addWidget(QLabel("Series:"))
        self.series_edit = QLineEdit()
        self.series_edit.setPlaceholderText("e.g. Star Wars: The Clone Wars")
        self.series_edit.returnPressed.connect(self.search_series)
        series_row.addWidget(self.series_edit, stretch=1)
        self.search_btn = QPushButton("Search TMDB")
        self.search_btn.clicked.connect(self.search_series)
        series_row.addWidget(self.search_btn)
        root.addLayout(series_row)

        self.series_label = QLabel("Selected: (none)")
        root.addWidget(self.series_label)

        self.results_list = QListWidget()
        self.results_list.setMaximumHeight(100)
        self.results_list.itemDoubleClicked.connect(self._pick_series_item)
        self.results_list.hide()
        root.addWidget(self.results_list)

        # Scan folder
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Scan folder:"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Folder with all discs / nested videos…")
        folder_row.addWidget(self.folder_edit, stretch=1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_folder)
        folder_row.addWidget(browse)
        root.addLayout(folder_row)

        # Output folder
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output folder:"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Where Season folders will be created…")
        out_row.addWidget(self.output_edit, stretch=1)
        out_browse = QPushButton("Browse…")
        out_browse.clicked.connect(self.browse_output)
        out_row.addWidget(out_browse)
        self.same_as_scan = QCheckBox("Same as scan")
        self.same_as_scan.setChecked(getattr(self.settings, "output_same_as_scan", True))
        self.same_as_scan.toggled.connect(self._on_same_as_scan_toggled)
        out_row.addWidget(self.same_as_scan)
        root.addLayout(out_row)

        out_opts = QHBoxLayout()
        self.series_subfolder_check = QCheckBox("Create series subfolder under output")
        self.series_subfolder_check.setChecked(
            getattr(self.settings, "output_create_series_subfolder", True)
        )
        self.series_subfolder_check.setToolTip(
            "e.g. Output/Star Wars The Clone Wars/Season 01/…"
        )
        out_opts.addWidget(self.series_subfolder_check)
        out_opts.addStretch()
        root.addLayout(out_opts)

        if getattr(self.settings, "last_output_folder", ""):
            self.output_edit.setText(self.settings.last_output_folder)
        self._on_same_as_scan_toggled(self.same_as_scan.isChecked())

        # Options
        opt_row = QHBoxLayout()
        self.season_check = QCheckBox("Organize into Season XX folders")
        self.season_check.setChecked(self.settings.move_to_season)
        opt_row.addWidget(self.season_check)
        self.recursive_check = QCheckBox("Include subfolders")
        self.recursive_check.setChecked(getattr(self.settings, "recursive_scan", True))
        self.recursive_check.setToolTip("Scan all nested folders for video files")
        opt_row.addWidget(self.recursive_check)
        opt_row.addWidget(QLabel("Match season:"))
        self.season_filter = QComboBox()
        self.season_filter.addItem("All seasons", 0)
        for n in range(1, 31):
            self.season_filter.addItem(f"Season {n:02d} only", n)
        sf = getattr(self.settings, "season_filter", None) or 0
        idx = self.season_filter.findData(int(sf))
        self.season_filter.setCurrentIndex(max(0, idx))
        self.season_filter.setToolTip(
            "Limit matching to one season (recommended for DVD disc folders). "
            "Also limits which reference subtitles are fetched/cached."
        )
        opt_row.addWidget(self.season_filter)
        opt_row.addWidget(QLabel("Format:"))
        self.format_edit = QLineEdit(self.settings.rename_format or DEFAULT_FORMAT)
        opt_row.addWidget(self.format_edit, stretch=1)
        self.scan_btn = QPushButton("Scan & Identify")
        self.scan_btn.setObjectName("primary")
        self.scan_btn.clicked.connect(self.start_scan)
        opt_row.addWidget(self.scan_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_scan)
        opt_row.addWidget(self.cancel_btn)
        root.addLayout(opt_row)

        # Progress
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.progress_label = QLabel("Ready")
        root.addWidget(self.progress_label)

        # Table
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["✓", "Original", "SxxExx", "Official Title", "Conf %", "Proposed name", "Target folder"]
        )
        self.table.horizontalHeader().setSectionResizeMode(COL_ORIG, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_NEW, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, stretch=1)

        legend = QLabel("Legend: green ≥70 · yellow 55–69 · red &lt;55 / error — edit cells before applying")
        root.addWidget(legend)

        # Footer actions
        foot = QHBoxLayout()
        self.export_btn = QPushButton("Export CSV/JSON")
        self.export_btn.clicked.connect(self.export_report)
        foot.addWidget(self.export_btn)
        self.undo_btn = QPushButton("Undo last apply")
        self.undo_btn.clicked.connect(self.undo_apply)
        foot.addWidget(self.undo_btn)
        self.retry_btn = QPushButton("Retry problem rows")
        self.retry_btn.setToolTip(
            "Re-extract and re-match only failed / low-confidence / duplicate rows"
        )
        self.retry_btn.clicked.connect(self.retry_problems)
        foot.addWidget(self.retry_btn)
        foot.addStretch()
        self.apply_btn = QPushButton("Apply Selected Renames")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply_renames)
        foot.addWidget(self.apply_btn)
        root.addLayout(foot)

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if not app:
            return
        system_dark = False
        try:
            from PySide6.QtCore import Qt as _Qt

            scheme = app.styleHints().colorScheme()
            system_dark = scheme == _Qt.ColorScheme.Dark
        except Exception:
            system_dark = False
        self._system_dark = system_dark
        # Default "system" unknown on Mint often → light for readable tables
        theme = self.settings.theme or "light"
        app.setStyleSheet(stylesheet_for(theme, system_dark=system_dark))

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            save_settings(self.settings)
            self.season_check.setChecked(self.settings.move_to_season)
            self.format_edit.setText(self.settings.rename_format)
            self._apply_theme()
            self.statusBar().showMessage(summary_text() + " · Settings saved")

    def browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select folder to scan (all discs / subfolders)", self.folder_edit.text() or str(Path.home())
        )
        if path:
            self.folder_edit.setText(path)
            if self.same_as_scan.isChecked():
                self.output_edit.setText(path)

    def browse_output(self) -> None:
        start = self.output_edit.text().strip() or self.folder_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "Select output library folder (holds Season folders)", start
        )
        if path:
            self.same_as_scan.setChecked(False)
            self.output_edit.setText(path)
            self.output_edit.setEnabled(True)

    def _on_same_as_scan_toggled(self, checked: bool) -> None:
        self.output_edit.setEnabled(not checked)
        if checked:
            scan = self.folder_edit.text().strip()
            if scan:
                self.output_edit.setText(scan)

    def _require_tmdb(self) -> str | None:
        key = get_tmdb_api_key()
        if not key:
            QMessageBox.warning(
                self,
                "TMDB API key required",
                "Add your free TMDB API key in Settings before searching or scanning.",
            )
            self.open_settings()
            return get_tmdb_api_key()
        return key

    def search_series(self) -> None:
        query = self.series_edit.text().strip()
        if not query:
            return
        key = self._require_tmdb()
        if not key:
            return
        try:
            client = TMDBClient(key)
            results = client.search_series(query)
        except TMDBError as exc:
            QMessageBox.critical(self, "TMDB search failed", str(exc))
            return
        self._search_results = results
        self.results_list.clear()
        if not results:
            self.results_list.hide()
            QMessageBox.information(self, "Search", "No series found.")
            return
        for s in results[:20]:
            item = QListWidgetItem(s.display_name())
            item.setData(Qt.UserRole, s.id)
            self.results_list.addItem(item)
        self.results_list.show()

    def _pick_series_item(self, item: QListWidgetItem) -> None:
        sid = item.data(Qt.UserRole)
        for s in self._search_results:
            if s.id == sid:
                self.series = s
                break
        if not self.series:
            return
        self.series_label.setText(f"Selected: {self.series.display_name()}  [id {self.series.id}]")
        self.results_list.hide()
        self.settings.last_series_id = self.series.id
        self.settings.last_series_name = self.series.name
        save_settings(self.settings)
        # Prefetch episodes
        key = get_tmdb_api_key()
        if key:
            try:
                self.episodes = TMDBClient(key).get_all_episodes(self.series.id)
                self.statusBar().showMessage(f"Loaded {len(self.episodes)} episodes for {self.series.name}")
            except TMDBError as exc:
                QMessageBox.warning(self, "TMDB", str(exc))

    def start_scan(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        folder = Path(self.folder_edit.text().strip())
        if not folder.is_dir():
            QMessageBox.warning(self, "Folder", "Choose a valid folder of video files.")
            return
        if not self.series:
            QMessageBox.warning(self, "Series", "Search and select a series first.")
            return
        key = self._require_tmdb()
        if not key:
            return

        self.settings.move_to_season = self.season_check.isChecked()
        self.settings.recursive_scan = self.recursive_check.isChecked()
        self.settings.rename_format = self.format_edit.text().strip() or DEFAULT_FORMAT
        self.settings.last_folder = str(folder)
        self.settings.output_same_as_scan = self.same_as_scan.isChecked()
        self.settings.output_create_series_subfolder = self.series_subfolder_check.isChecked()
        if self.same_as_scan.isChecked():
            self.settings.output_folder = str(folder)
            self.settings.last_output_folder = str(folder)
        else:
            out = self.output_edit.text().strip() or str(folder)
            self.settings.output_folder = out
            self.settings.last_output_folder = out
            self.output_edit.setText(out)
        sf_data = self.season_filter.currentData()
        self.settings.season_filter = int(sf_data) if sf_data else None
        if self.settings.season_filter == 0:
            self.settings.season_filter = None
        save_settings(self.settings)

        if not self.episodes:
            try:
                self.episodes = TMDBClient(key).get_all_episodes(self.series.id)
            except TMDBError as exc:
                QMessageBox.critical(self, "TMDB", str(exc))
                return

        self.scan_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.plan = []
        self.table.setRowCount(0)

        self._thread = QThread(self)
        self._worker = IdentifyWorker(folder, self.series, self.episodes, self.settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.failed.connect(self._on_scan_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_worker)
        self._thread.start()

    def cancel_scan(self) -> None:
        if self._worker and hasattr(self._worker, "cancel"):
            self._worker.cancel()
            self.progress_label.setText("Cancelling after current file…")

    def _cleanup_worker(self) -> None:
        self.scan_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        if self._thread:
            self._thread.deleteLater()
            self._thread = None

    def _on_progress(self, ev: ProgressEvent) -> None:
        if ev.total > 0:
            self.progress.setMaximum(ev.total)
            self.progress.setValue(ev.current)
        self.progress_label.setText(ev.message)

    def _on_scan_failed(self, message: str) -> None:
        self.progress_label.setText("Scan failed")
        QMessageBox.critical(self, "Scan failed", message)

    def _on_scan_finished(self, plan: list) -> None:
        self.plan = list(plan)
        self._fill_table()
        # Keep session log for apply + user review path
        if self._worker and getattr(self._worker, "session_log", None):
            self._session_log = self._worker.session_log
        log_hint = ""
        if self._session_log:
            log_hint = f" · Log: {self._session_log.dir}"
        self.progress_label.setText(
            f"Done — {len(self.plan)} row(s). Review table, then Apply.{log_hint}"
        )
        self.statusBar().showMessage(
            f"Scan complete: {len(self.plan)} rows{log_hint}"
        )

    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.plan))
        for row_idx, row in enumerate(self.plan):
            sel = QTableWidgetItem()
            sel.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            sel.setCheckState(Qt.Checked if row.selected else Qt.Unchecked)
            self.table.setItem(row_idx, COL_SEL, sel)

            orig = QTableWidgetItem(row.original_name)
            orig.setFlags(orig.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row_idx, COL_ORIG, orig)

            code = ""
            if row.season is not None and row.episode is not None:
                code = f"S{row.season:02d}E{row.episode:02d}"
            code_item = QTableWidgetItem(code)
            self.table.setItem(row_idx, COL_CODE, code_item)

            title_item = QTableWidgetItem(row.official_title)
            self.table.setItem(row_idx, COL_TITLE, title_item)

            conf_item = QTableWidgetItem(f"{row.confidence:.0f}" if not row.error else "—")
            conf_item.setFlags(conf_item.flags() & ~Qt.ItemIsEditable)
            conf_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, COL_CONF, conf_item)

            new_item = QTableWidgetItem(row.proposed_name)
            self.table.setItem(row_idx, COL_NEW, new_item)

            target = str(row.target_dir) if row.target_dir else ""
            tgt_item = QTableWidgetItem(target)
            tgt_item.setFlags(tgt_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row_idx, COL_TARGET, tgt_item)

            tip_parts = []
            kind = getattr(row, "row_kind", "rename")
            tip_parts.append(f"Kind: {kind}")
            if kind == "split" and row.split_start is not None and row.split_end is not None:
                tip_parts.append(
                    f"Split range: {row.split_start/60:.2f}–{row.split_end/60:.2f} min"
                )
            if kind == "inventory_skip":
                tip_parts.append(f"Skip: {row.skip_reason or 'already present'}")
                if row.covered_by:
                    tip_parts.append(f"Already have: {row.covered_by}")
            if row.dialogue_source:
                tip_parts.append(f"Source: {row.dialogue_source}")
            if row.track_info:
                tip_parts.append(f"Track: {row.track_info}")
            tip_parts.append(f"Sample quality: {row.sample_quality:.0f}%")
            if row.flags:
                tip_parts.append("Flags: " + ", ".join(row.flags))
            if row.error:
                tip_parts.append("Error: " + row.error)
            if row.candidates:
                alts = "; ".join(
                    f"S{c.season:02d}E{c.episode:02d} {c.title} ({c.confidence:.0f}%)"
                    for c in row.candidates[:3]
                )
                tip_parts.append("Top: " + alts)
            if row.dialogue_lines:
                tip_parts.append("Dialogue sample:")
                tip_parts.extend(f"  • {ln}" for ln in row.dialogue_lines[:8])
            tip = "\n".join(tip_parts)
            for col in range(7):
                item = self.table.item(row_idx, col)
                if item and tip:
                    item.setToolTip(tip)

            self._color_row(row_idx, row)
        self.table.blockSignals(False)

    def _color_row(self, row_idx: int, row: RenamePlanRow) -> None:
        theme = self.settings.theme or "light"
        system_dark = getattr(self, "_system_dark", False)
        bands = confidence_colors(theme, system_dark=system_dark)
        if row.error:
            bg_hex, fg_hex = bands["error"]
        elif row.confidence >= 70:
            bg_hex, fg_hex = bands["high"]
        elif row.confidence >= self.settings.low_threshold:
            bg_hex, fg_hex = bands["mid"]
        else:
            bg_hex, fg_hex = bands["low"]
        bg = QColor(bg_hex)
        fg = QColor(fg_hex)
        for col in range(7):
            item = self.table.item(row_idx, col)
            if item:
                item.setBackground(bg)
                item.setForeground(fg)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row_idx = item.row()
        if row_idx < 0 or row_idx >= len(self.plan):
            return
        row = self.plan[row_idx]
        col = item.column()
        if col == COL_SEL:
            row.selected = item.checkState() == Qt.Checked
            return
        if col == COL_CODE:
            m = re.fullmatch(r"[Ss](\d{1,2})[Ee](\d{1,2})", item.text().strip())
            if not m:
                return
            row.season = int(m.group(1))
            row.episode = int(m.group(2))
            # Resolve title from episode list
            for ep in self.episodes:
                if ep.season == row.season and ep.episode == row.episode:
                    row.official_title = ep.title
                    break
            self._rebuild_proposed(row_idx)
        elif col == COL_TITLE:
            row.official_title = item.text().strip()
            self._rebuild_proposed(row_idx)
        elif col == COL_NEW:
            row.proposed_name = item.text().strip()

    def _output_root_path(self) -> Path:
        scan = Path(self.folder_edit.text().strip() or ".")
        if self.same_as_scan.isChecked():
            return scan
        out = self.output_edit.text().strip()
        return Path(out) if out else scan

    def _rebuild_proposed(self, row_idx: int) -> None:
        from episodeid.renamer import resolve_target_dir

        row = self.plan[row_idx]
        if row.season is None or row.episode is None or not self.series:
            return
        series = self.series.name
        fmt = self.format_edit.text().strip() or DEFAULT_FORMAT
        path = row.path
        row.proposed_name = format_new_name(
            series=series,
            season=row.season,
            episode=row.episode,
            title=row.official_title or "Unknown",
            ext=path.suffix,
            fmt=fmt,
        )
        scan_root = Path(self.folder_edit.text().strip() or ".")
        row.target_dir = resolve_target_dir(
            season=row.season if self.season_check.isChecked() else None,
            scan_root=scan_root,
            output_root=self._output_root_path(),
            series_name=series,
            move_to_season=self.season_check.isChecked(),
            create_series_subfolder=self.series_subfolder_check.isChecked(),
            source_path=path,
        )
        row.move_to_season = self.season_check.isChecked()
        row.error = None
        row.selected = True
        self.table.blockSignals(True)
        self.table.item(row_idx, COL_TITLE).setText(row.official_title)
        self.table.item(row_idx, COL_NEW).setText(row.proposed_name)
        self.table.item(row_idx, COL_TARGET).setText(str(row.target_dir))
        self.table.item(row_idx, COL_SEL).setCheckState(Qt.Checked)
        self.table.item(row_idx, COL_CODE).setText(f"S{row.season:02d}E{row.episode:02d}")
        self._color_row(row_idx, row)
        self.table.blockSignals(False)

    def apply_renames(self) -> None:
        selected = [r for r in self.plan if r.selected]
        if not selected:
            QMessageBox.information(self, "Apply", "No rows selected.")
            return
        n_rename = sum(1 for r in selected if getattr(r, "row_kind", "rename") == "rename")
        n_split = sum(1 for r in selected if getattr(r, "row_kind", "rename") == "split")
        sample = "\n".join(
            f"{r.original_name} → {r.proposed_name}" for r in selected[:8]
        )
        more = "" if len(selected) <= 8 else f"\n… and {len(selected) - 8} more"
        reply = QMessageBox.question(
            self,
            "Confirm apply",
            f"Apply {n_rename} rename(s) and {n_split} split(s)?\n"
            f"Original multi-episode files are kept.\n\n{sample}{more}\n\n"
            "Undo via Undo last apply (splits remove created files only).",
        )
        if reply != QMessageBox.Yes:
            return

        # sync selection from table
        for i, row in enumerate(self.plan):
            item = self.table.item(i, COL_SEL)
            if item:
                row.selected = item.checkState() == Qt.Checked
            new_item = self.table.item(i, COL_NEW)
            if new_item:
                row.proposed_name = new_item.text().strip()

        self.apply_btn.setEnabled(False)
        self._thread = QThread(self)
        self._worker = ApplyWorker(self.plan, undo_dir(), session_log=self._session_log)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_apply_finished)
        self._worker.failed.connect(self._on_apply_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_worker)
        self._thread.start()

    def _on_apply_finished(self, successes: list, failures: list) -> None:
        self.apply_btn.setEnabled(True)
        n_split = sum(1 for s in successes if (s.get("type") or "rename") == "split")
        n_ren = len(successes) - n_split
        msg = f"Applied {n_ren} rename(s) and {n_split} split(s)."
        if failures:
            msg += f"\n{len(failures)} failed:\n" + "\n".join(
                f"{f.get('path')}: {f.get('error')}" for f in failures[:10]
            )
        if self._session_log:
            msg += f"\n\nFull log for review:\n{self._session_log.dir}"
        QMessageBox.information(self, "Apply complete", msg)
        self.statusBar().showMessage(msg.split("\n")[0])
        # Refresh original names in table for successes
        self._fill_table()

    def _on_apply_failed(self, message: str) -> None:
        self.apply_btn.setEnabled(True)
        QMessageBox.critical(self, "Apply failed", message)

    def export_report(self) -> None:
        if not self.plan:
            QMessageBox.information(self, "Export", "Nothing to export yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export report",
            str(Path.home() / "episodeid-report.csv"),
            "CSV (*.csv);;JSON (*.json)",
        )
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() == ".json":
            export_json(self.plan, p)
        else:
            if p.suffix.lower() != ".csv":
                p = p.with_suffix(".csv")
            export_csv(self.plan, p)
        QMessageBox.information(self, "Export", f"Saved {p}")

    def undo_apply(self) -> None:
        reply = QMessageBox.question(
            self,
            "Undo",
            "Undo the most recent apply operation?",
        )
        if reply != QMessageBox.Yes:
            return
        ok, err = undo_last(undo_dir())
        msg = f"Restored {len(ok)} file(s)."
        if err:
            msg += "\n" + "\n".join(e.get("error", str(e)) for e in err[:8])
        QMessageBox.information(self, "Undo", msg)

    def retry_problems(self) -> None:
        if not self.plan or not self.series:
            QMessageBox.information(self, "Retry", "Run a scan first.")
            return
        if not self.episodes:
            key = get_tmdb_api_key()
            if not key:
                return
            try:
                self.episodes = TMDBClient(key).get_all_episodes(self.series.id)
            except TMDBError as exc:
                QMessageBox.critical(self, "TMDB", str(exc))
                return
        from episodeid.pipeline import retry_problem_rows

        folder = Path(self.folder_edit.text().strip())
        self.retry_btn.setEnabled(False)
        self.progress_label.setText("Retrying problem rows…")
        try:
            self.plan = retry_problem_rows(
                self.plan,
                folder=folder,
                series=self.series,
                episodes=self.episodes,
                settings=self.settings,
                progress=lambda ev: self.progress_label.setText(ev.message),
            )
            self._fill_table()
            self.progress_label.setText("Retry complete — review table")
        except Exception as exc:
            QMessageBox.critical(self, "Retry failed", str(exc))
        finally:
            self.retry_btn.setEnabled(True)
