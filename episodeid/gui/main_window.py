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
    plan_summary_counts,
    season_dir_name,
    source_folder_label,
    undo_last,
)

(
    COL_SEL,
    COL_STATUS,
    COL_SOURCE,
    COL_ORIG,
    COL_CODE,
    COL_TITLE,
    COL_CONF,
    COL_NEW,
    COL_TARGET,
) = range(9)
_N_COLS = 9


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
        self._row_map: list[int] = []  # table row → plan index
        self._show_inventory_skips = False
        self._show_extras = False

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
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        # Toolbar row
        top = QHBoxLayout()
        top.setSpacing(8)
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel(__app_name__)
        title.setObjectName("appTitle")
        subtitle = QLabel(f"v{__version__} · identify & rename from dialogue")
        subtitle.setObjectName("appSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        top.addLayout(title_col)
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
        self.progress.setTextVisible(True)
        root.addWidget(self.progress)
        self.progress_label = QLabel("Ready")
        self.progress_label.setObjectName("progressLabel")
        root.addWidget(self.progress_label)
        self.coverage_label = QLabel("")
        self.coverage_label.setObjectName("coverageLabel")
        self.coverage_label.setWordWrap(True)
        self.coverage_label.hide()
        root.addWidget(self.coverage_label)

        # Result filters (so S7 splits / problems are findable)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Show:"))
        self.view_filter = QComboBox()
        self.view_filter.addItem("All actionable", "actionable")
        self.view_filter.addItem("Selected only", "selected")
        self.view_filter.addItem("Splits only", "splits")
        self.view_filter.addItem("Problems / review", "problems")
        self.view_filter.addItem("Everything", "all")
        self.view_filter.setToolTip(
            "All actionable hides mega inventory skips. Use Splits only to find S7 segments."
        )
        self.view_filter.currentIndexChanged.connect(self._refill_table)
        filter_row.addWidget(self.view_filter)
        filter_row.addWidget(QLabel("Season:"))
        self.season_view_filter = QComboBox()
        self.season_view_filter.addItem("All seasons", 0)
        for n in range(1, 31):
            self.season_view_filter.addItem(f"S{n:02d}", n)
        self.season_view_filter.currentIndexChanged.connect(self._refill_table)
        filter_row.addWidget(self.season_view_filter)
        self.show_skips_check = QCheckBox("Show mega skips")
        self.show_skips_check.setChecked(False)
        self.show_skips_check.setToolTip(
            "Mega multi-episode files skipped because the disc already has singles"
        )
        self.show_skips_check.toggled.connect(self._on_show_skips_toggled)
        filter_row.addWidget(self.show_skips_check)
        self.show_extras_check = QCheckBox("Show extras")
        self.show_extras_check.setChecked(False)
        self.show_extras_check.setToolTip(
            "Show non-episode extras (no English subs, commentary, short bonus tracks)"
        )
        self.show_extras_check.toggled.connect(self._on_show_extras_toggled)
        filter_row.addWidget(self.show_extras_check)
        filter_row.addStretch()
        root.addLayout(filter_row)

        # Table
        self.table = QTableWidget(0, _N_COLS)
        self.table.setHorizontalHeaderLabels(
            [
                "✓",
                "Status",
                "Source disc/folder",
                "Original",
                "SxxExx",
                "Official Title",
                "Conf %",
                "Proposed name",
                "Target folder",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(COL_ORIG, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_NEW, QHeaderView.Stretch)
        self.table.setColumnWidth(COL_SEL, 40)
        self.table.setColumnWidth(COL_STATUS, 78)
        self.table.setColumnWidth(COL_SOURCE, 160)
        self.table.setColumnWidth(COL_CODE, 80)
        self.table.setColumnWidth(COL_CONF, 64)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, stretch=1)

        legend = QLabel(
            "Status: OK · REVIEW · SPLIT · SKIP · EXTRA · ERROR — "
            "green ≥70 · yellow 55–69 · red &lt;55 / error — edit before Apply"
        )
        legend.setObjectName("legend")
        root.addWidget(legend)

        # Footer actions
        foot = QHBoxLayout()
        foot.setSpacing(8)
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
        # Prefer explicit light for readability; migrate blank/unknown to light
        theme = (self.settings.theme or "light").strip().lower()
        if theme not in {"light", "dark", "system"}:
            theme = "light"
        # Fusion + light palette avoids Mint dark-chrome fighting our QSS
        try:
            from PySide6.QtGui import QPalette, QColor
            from PySide6.QtWidgets import QStyleFactory

            app.setStyle(QStyleFactory.create("Fusion") or app.style().objectName())
            if theme == "light" or (theme == "system" and not system_dark):
                pal = QPalette()
                page = QColor("#f4f6fa")
                text = QColor("#1c2333")
                surface = QColor("#ffffff")
                pal.setColor(QPalette.Window, page)
                pal.setColor(QPalette.WindowText, text)
                pal.setColor(QPalette.Base, surface)
                pal.setColor(QPalette.AlternateBase, QColor("#f9fafc"))
                pal.setColor(QPalette.Text, text)
                pal.setColor(QPalette.Button, surface)
                pal.setColor(QPalette.ButtonText, text)
                pal.setColor(QPalette.Highlight, QColor("#e8f0fe"))
                pal.setColor(QPalette.HighlightedText, text)
                pal.setColor(QPalette.ToolTipBase, QColor("#1c2333"))
                pal.setColor(QPalette.ToolTipText, QColor("#ffffff"))
                app.setPalette(pal)
            elif theme == "dark" or (theme == "system" and system_dark):
                pal = QPalette()
                page = QColor("#2b303b")
                text = QColor("#f0f2f5")
                surface = QColor("#353b48")
                pal.setColor(QPalette.Window, page)
                pal.setColor(QPalette.WindowText, text)
                pal.setColor(QPalette.Base, surface)
                pal.setColor(QPalette.AlternateBase, QColor("#3a4150"))
                pal.setColor(QPalette.Text, text)
                pal.setColor(QPalette.Button, surface)
                pal.setColor(QPalette.ButtonText, text)
                pal.setColor(QPalette.Highlight, QColor("#3a4f73"))
                pal.setColor(QPalette.HighlightedText, text)
                app.setPalette(pal)
        except Exception:
            pass
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
        self._populate_season_view_filter()
        self._fill_table()
        # Keep session log for apply + user review path
        if self._worker and getattr(self._worker, "session_log", None):
            self._session_log = self._worker.session_log
        log_hint = ""
        if self._session_log:
            log_hint = f" · Log: {self._session_log.dir}"
        counts = plan_summary_counts(self.plan)
        summary = (
            f"{counts['rename']} rename · {counts['split']} split · "
            f"{counts['inventory_skip']} mega-skipped · {counts['extra']} no-eng extras · "
            f"{counts['review']} need review"
        )
        cov_text = self._coverage_text()
        if cov_text:
            self.coverage_label.setText(f"Season coverage: {cov_text}")
            self.coverage_label.setToolTip(cov_text)
            self.coverage_label.show()
        else:
            self.coverage_label.hide()
        season_badge = self._season_badge_text()
        self.progress_label.setText(f"Done — {summary}.{log_hint}")
        if season_badge:
            self.progress_label.setText(
                self.progress_label.text() + f"  |  {season_badge}"
            )
        bar = f"Scan complete: {summary}"
        if cov_text:
            bar += f"  |  {cov_text}"
        self.statusBar().showMessage(bar + log_hint)

    def _coverage_text(self) -> str:
        """Missing/found episodes per season from plan + full catalog."""
        from episodeid.coverage import format_coverage_summary, season_coverage

        if not self.plan:
            return ""
        catalog = list(self.episodes or [])
        if not catalog:
            # Fall back to seasons only from plan (no totals)
            return self._season_badge_text()
        # Prefer summary stored on session log if present
        if self._session_log:
            try:
                import json

                sj = self._session_log.summary_json
                if sj.exists():
                    data = json.loads(sj.read_text(encoding="utf-8"))
                    extra = data.get("extra") or {}
                    if extra.get("coverage_summary"):
                        return str(extra["coverage_summary"])
                    if extra.get("coverage"):
                        # rebuild short string from stored dicts
                        from episodeid.coverage import SeasonCoverage

                        covs = [
                            SeasonCoverage(
                                season=int(c["season"]),
                                total=int(c["total"]),
                                found=int(c["found"]),
                                found_codes=list(c.get("found_codes") or []),
                                missing_codes=list(c.get("missing_codes") or []),
                            )
                            for c in extra["coverage"]
                        ]
                        return format_coverage_summary(covs)
            except Exception:
                pass
        cov = season_coverage(
            self.plan,
            catalog,
            low_threshold=self.settings.low_threshold,
        )
        return format_coverage_summary(cov)

    def _season_badge_text(self) -> str:
        """Highlight seasons that only appear as splits (e.g. S07)."""
        by_season: dict[int, list[RenamePlanRow]] = {}
        for r in self.plan:
            if r.season is None:
                continue
            if getattr(r, "row_kind", "rename") == "inventory_skip":
                continue
            by_season.setdefault(int(r.season), []).append(r)
        parts = []
        for s in sorted(by_season):
            rows = by_season[s]
            ready = sum(1 for r in rows if r.selected)
            review = sum(
                1
                for r in rows
                if not r.selected and not r.error and r.confidence >= self.settings.low_threshold
            )
            splits = sum(1 for r in rows if getattr(r, "row_kind", "") == "split")
            if splits and ready + review > 0:
                parts.append(f"S{s:02d}: {ready} ready · {review} review")
        return "  ·  ".join(parts[:6])

    def _populate_season_view_filter(self) -> None:
        """Keep All + seasons that appear in the plan."""
        present = sorted(
            {
                int(r.season)
                for r in self.plan
                if r.season is not None
            }
        )
        cur = self.season_view_filter.currentData()
        self.season_view_filter.blockSignals(True)
        self.season_view_filter.clear()
        self.season_view_filter.addItem("All seasons", 0)
        for n in present:
            n_sel = sum(
                1
                for r in self.plan
                if r.season == n and r.selected
            )
            n_all = sum(1 for r in self.plan if r.season == n)
            self.season_view_filter.addItem(f"S{n:02d} ({n_sel}/{n_all})", n)
        idx = self.season_view_filter.findData(cur if cur is not None else 0)
        self.season_view_filter.setCurrentIndex(max(0, idx))
        self.season_view_filter.blockSignals(False)

    def _on_show_skips_toggled(self, checked: bool) -> None:
        self._show_inventory_skips = checked
        self._fill_table()

    def _on_show_extras_toggled(self, checked: bool) -> None:
        self._show_extras = checked
        self._fill_table()

    def _refill_table(self, *_args) -> None:
        if self.plan:
            self._fill_table()

    def _filtered_plan_indices(self) -> list[int]:
        mode = self.view_filter.currentData() or "actionable"
        season_f = int(self.season_view_filter.currentData() or 0)
        out: list[int] = []
        for i, row in enumerate(self.plan):
            kind = getattr(row, "row_kind", "rename")
            err = (row.error or "").lower()
            is_extra = bool(
                "likely_extra" in (row.flags or [])
                or (err and ("no_english" in err or "no_subtitle" in err or err == "likely_extra"))
            )

            # Mega inventory skips hidden unless checkbox / Everything mode
            if kind == "inventory_skip":
                if mode == "actionable" or mode == "selected" or mode == "splits" or mode == "problems":
                    continue
                if not self._show_inventory_skips and mode != "all":
                    continue
                if not self._show_inventory_skips and mode == "all":
                    continue

            if is_extra and not self._show_extras:
                continue

            if season_f and row.season is not None and int(row.season) != season_f:
                continue
            if season_f and row.season is None:
                # Hide unseasoned rows when filtering a specific season
                continue

            if mode == "selected" and not row.selected:
                continue
            if mode == "splits" and kind != "split":
                continue
            if mode == "problems":
                status = row.status_label(
                    low_threshold=self.settings.low_threshold,
                    auto_threshold=self.settings.auto_threshold,
                )
                if status in {"OK", "SPLIT"} and row.selected and "duplicate_global" not in row.flags:
                    continue
            out.append(i)
        return out

    def _fill_table(self) -> None:
        indices = self._filtered_plan_indices()
        self._row_map = indices
        self.table.blockSignals(True)
        self.table.setRowCount(len(indices))
        for row_idx, plan_i in enumerate(indices):
            row = self.plan[plan_i]
            sel = QTableWidgetItem()
            sel.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            sel.setCheckState(Qt.Checked if row.selected else Qt.Unchecked)
            sel.setTextAlignment(Qt.AlignCenter)
            if getattr(row, "row_kind", "rename") == "inventory_skip":
                sel.setFlags(Qt.ItemIsEnabled)  # not checkable
            self.table.setItem(row_idx, COL_SEL, sel)

            status = row.status_label(
                low_threshold=self.settings.low_threshold,
                auto_threshold=self.settings.auto_threshold,
            )
            # Friendlier labels for extras / skips / dups
            err = (row.error or "").lower()
            if "likely_extra" in (row.flags or []) or "no_english" in err or err == "likely_extra":
                status = "EXTRA"
            elif "duplicate_global" in (row.flags or []):
                status = "DUP"
            status_item = QTableWidgetItem(status)
            status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
            status_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, COL_STATUS, status_item)

            # Source disc/folder for manual review (e.g. S1_D4)
            src_label = source_folder_label(
                row.path,
                library_root=Path(self.folder_edit.text().strip() or ".")
                if self.folder_edit.text().strip()
                else None,
            )
            src_item = QTableWidgetItem(src_label)
            src_item.setFlags(src_item.flags() & ~Qt.ItemIsEditable)
            src_item.setToolTip(str(row.path))
            self.table.setItem(row_idx, COL_SOURCE, src_item)

            display_name = row.original_name
            if status == "EXTRA":
                display_name = f"{row.original_name}  (extra / not main episode)"
            elif getattr(row, "row_kind", "") == "inventory_skip":
                display_name = f"{row.original_name}  — skipped (already on disc)"
            elif status == "DUP":
                display_name = f"{row.original_name}  (duplicate claim)"
            orig = QTableWidgetItem(display_name)
            orig.setFlags(orig.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row_idx, COL_ORIG, orig)

            code = ""
            if row.season is not None and row.episode is not None:
                code = f"S{row.season:02d}E{row.episode:02d}"
            elif getattr(row, "row_kind", "") == "inventory_skip":
                code = "—"
            code_item = QTableWidgetItem(code)
            self.table.setItem(row_idx, COL_CODE, code_item)

            title_item = QTableWidgetItem(row.official_title)
            self.table.setItem(row_idx, COL_TITLE, title_item)

            conf_item = QTableWidgetItem(
                f"{row.confidence:.0f}" if row.season is not None and not (
                    row.error and getattr(row, "row_kind", "") != "split"
                ) else ("—" if row.error or getattr(row, "row_kind", "") == "inventory_skip" else f"{row.confidence:.0f}")
            )
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
            tip_parts.append(f"Status: {status}")
            tip_parts.append(f"Kind: {kind}")
            if kind == "split" and row.split_start is not None and row.split_end is not None:
                tip_parts.append(
                    f"Split range: {row.split_start/60:.2f}–{row.split_end/60:.2f} min"
                )
            if kind == "inventory_skip":
                tip_parts.append(f"Skip: {row.skip_reason or 'already present'}")
                if row.covered_by:
                    tip_parts.append(f"Already have: {row.covered_by}")
            if "duplicate_global" in row.flags:
                tip_parts.append("Demoted: another file claims this SxxExx (higher confidence)")
            if "output_collision" in row.flags:
                tip_parts.append("Demoted: would overwrite same output path")
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
            for col in range(_N_COLS):
                item = self.table.item(row_idx, col)
                if item and tip:
                    item.setToolTip(tip)

            self._color_row(row_idx, row, status=status)
        self.table.blockSignals(False)
        visible = len(indices)
        total = len(self.plan)
        if visible != total:
            self.statusBar().showMessage(
                f"Showing {visible} of {total} rows (filters active)"
            )

    def _color_row(self, row_idx: int, row: RenamePlanRow, status: str = "") -> None:
        theme = self.settings.theme or "light"
        system_dark = getattr(self, "_system_dark", False)
        dark = theme == "dark" or (theme == "system" and system_dark)
        bands = confidence_colors(theme, system_dark=system_dark)
        kind = getattr(row, "row_kind", "rename")
        if status == "SKIP" or kind == "inventory_skip":
            bg_hex, fg_hex = ("#e8e8ec", "#555555") if not dark else ("#2a2b32", "#999999")
        elif status == "EXTRA" or (
            row.error and ("no_english" in (row.error or "").lower())
        ):
            bg_hex, fg_hex = bands["low"]
        elif row.error:
            bg_hex, fg_hex = bands["error"]
        elif row.confidence >= 70:
            bg_hex, fg_hex = bands["high"]
        elif row.confidence >= self.settings.low_threshold:
            bg_hex, fg_hex = bands["mid"]
        else:
            bg_hex, fg_hex = bands["low"]
        bg = QColor(bg_hex)
        fg = QColor(fg_hex)
        # Keep select-column neutral so blue checkboxes stay high-contrast
        sel_bg = QColor(bands.get("select", ("#ffffff", "#111111"))[0])
        for col in range(_N_COLS):
            item = self.table.item(row_idx, col)
            if not item:
                continue
            if col == COL_SEL:
                item.setBackground(sel_bg)
                item.setForeground(fg)
            else:
                item.setBackground(bg)
                item.setForeground(fg)

    def _plan_index(self, table_row: int) -> int | None:
        if table_row < 0 or table_row >= len(self._row_map):
            return None
        return self._row_map[table_row]

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row_idx = item.row()
        plan_i = self._plan_index(row_idx)
        if plan_i is None or plan_i >= len(self.plan):
            return
        row = self.plan[plan_i]
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

        plan_i = self._plan_index(row_idx)
        if plan_i is None:
            return
        row = self.plan[plan_i]
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
        status = row.status_label(
            low_threshold=self.settings.low_threshold,
            auto_threshold=self.settings.auto_threshold,
        )
        self.table.blockSignals(True)
        self.table.item(row_idx, COL_TITLE).setText(row.official_title)
        self.table.item(row_idx, COL_NEW).setText(row.proposed_name)
        self.table.item(row_idx, COL_TARGET).setText(str(row.target_dir))
        self.table.item(row_idx, COL_SEL).setCheckState(Qt.Checked)
        self.table.item(row_idx, COL_CODE).setText(f"S{row.season:02d}E{row.episode:02d}")
        if self.table.item(row_idx, COL_STATUS):
            self.table.item(row_idx, COL_STATUS).setText(status)
        self._color_row(row_idx, row, status=status)
        self.table.blockSignals(False)

    def apply_renames(self) -> None:
        # Sync visible table edits into plan first
        for table_i, plan_i in enumerate(self._row_map):
            if plan_i >= len(self.plan):
                continue
            row = self.plan[plan_i]
            item = self.table.item(table_i, COL_SEL)
            if item and (item.flags() & Qt.ItemIsUserCheckable):
                row.selected = item.checkState() == Qt.Checked
            new_item = self.table.item(table_i, COL_NEW)
            if new_item:
                row.proposed_name = new_item.text().strip()

        # Re-run collision check after manual edits
        from episodeid.renamer import apply_global_unique_assignment, detect_output_collisions

        apply_global_unique_assignment(self.plan)
        detect_output_collisions(self.plan)

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
