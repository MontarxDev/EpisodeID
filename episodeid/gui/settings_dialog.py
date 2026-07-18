"""Settings dialog for API keys, matching, and dependencies."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from episodeid.config import (
    KEY_GEMINI,
    KEY_GROK,
    KEY_OPENAI,
    KEY_TMDB,
    KEY_WYZIE,
    Settings,
    get_secret,
    set_secret,
)
from episodeid.cache_manager import (
    clear_all,
    clear_refsubs,
    clear_tmdb,
    clear_tvmaze,
    get_cache_stats,
)
from episodeid.deps import install_dependencies, ocr_available, summary_text
from episodeid.metadata import TMDBClient, TMDBError


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EpisodeID Settings")
        self.setMinimumWidth(560)
        self.settings = settings

        tabs = QTabWidget()
        tabs.addTab(self._tmdb_tab(), "TMDB")
        tabs.addTab(self._accuracy_tab(), "Accuracy")
        tabs.addTab(self._cache_tab(), "Cache")
        tabs.addTab(self._matching_tab(), "Matching")
        tabs.addTab(self._rename_tab(), "Rename")
        tabs.addTab(self._llm_tab(), "Optional LLM")
        tabs.addTab(self._system_tab(), "System")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def _tmdb_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.tmdb_key = QLineEdit()
        self.tmdb_key.setEchoMode(QLineEdit.Password)
        self.tmdb_key.setText(get_secret(KEY_TMDB) or "")
        self.tmdb_key.setPlaceholderText("Your free TMDB API key")
        form.addRow("API key", self.tmdb_key)

        row = QHBoxLayout()
        test_btn = QPushButton("Test connection")
        test_btn.clicked.connect(self._test_tmdb)
        help_lbl = QLabel(
            '<a href="https://www.themoviedb.org/settings/api">Get a free TMDB API key</a>'
        )
        help_lbl.setOpenExternalLinks(True)
        row.addWidget(test_btn)
        row.addWidget(help_lbl)
        row.addStretch()
        form.addRow("", row)
        return w

    def _accuracy_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.use_tvmaze = QCheckBox("Enrich plots with TVMaze (free, no key)")
        self.use_tvmaze.setChecked(getattr(self.settings, "use_tvmaze", True))
        self.use_refsubs = QCheckBox("Match against reference subtitles (recommended)")
        self.use_refsubs.setChecked(getattr(self.settings, "use_reference_subs", True))
        self.save_ref_cache = QCheckBox("Save downloaded references to disk cache")
        self.save_ref_cache.setChecked(getattr(self.settings, "save_refsubs_to_cache", True))
        self.save_ref_cache.setToolTip(
            "When on, Wyzie downloads are stored under the cache folder and reused forever "
            "(survives reboot). Turn off only if you never want new downloads written."
        )
        self.ref_policy = QComboBox()
        self.ref_policy.addItem("Use cache, download missing (default)", "download-missing")
        self.ref_policy.addItem("Cache only — never download", "cache-only")
        self.ref_policy.addItem("Force re-download this scan", "force-refresh")
        pol = getattr(self.settings, "refsubs_network_policy", "download-missing")
        idx = self.ref_policy.findData(pol)
        self.ref_policy.setCurrentIndex(max(0, idx))
        self.wyzie_key = QLineEdit(get_secret(KEY_WYZIE) or "")
        self.wyzie_key.setEchoMode(QLineEdit.Password)
        self.wyzie_key.setPlaceholderText("Free Wyzie API key (optional but recommended)")
        help_lbl = QLabel(
            'Free key: <a href="https://store.wyzie.io/redeem">store.wyzie.io/redeem</a><br>'
            "Downloads only happen for <b>missing</b> episodes in the active season list. "
            "Cached files are reused on later scans."
        )
        help_lbl.setOpenExternalLinks(True)
        help_lbl.setWordWrap(True)
        form.addRow(self.use_tvmaze)
        form.addRow(self.use_refsubs)
        form.addRow(self.save_ref_cache)
        form.addRow("Network policy", self.ref_policy)
        form.addRow("Wyzie API key", self.wyzie_key)
        form.addRow(help_lbl)
        return w

    def _cache_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.durable_cache = QCheckBox(
            "Use durable cache location (~/.local/share/episodeid/cache)"
        )
        self.durable_cache.setChecked(getattr(self.settings, "durable_cache", True))
        self.durable_cache.setToolTip(
            "Recommended. Survives reboot and most “clean ~/.cache” tools. "
            "Off uses ~/.cache/episodeid instead."
        )
        layout.addWidget(self.durable_cache)
        self.cache_stats_label = QLabel()
        self.cache_stats_label.setWordWrap(True)
        self._refresh_cache_stats()
        layout.addWidget(self.cache_stats_label)

        row1 = QHBoxLayout()
        open_btn = QPushButton("Open cache folder")
        open_btn.clicked.connect(self._open_cache_folder)
        refresh_btn = QPushButton("Refresh stats")
        refresh_btn.clicked.connect(self._refresh_cache_stats)
        row1.addWidget(open_btn)
        row1.addWidget(refresh_btn)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        for label, handler in (
            ("Clear TMDB", self._clear_tmdb),
            ("Clear TVMaze", self._clear_tvmaze),
            ("Clear ref subs", self._clear_refsubs),
            ("Clear ALL", self._clear_all_cache),
        ):
            b = QPushButton(label)
            b.clicked.connect(handler)
            row2.addWidget(b)
        layout.addLayout(row2)
        note = QLabel(
            "Cache is on disk and is <b>not</b> deleted on reboot. "
            "Only Clear buttons, manual delete, or aggressive system cleaners remove it."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return w

    def _matching_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.low_threshold = QDoubleSpinBox()
        self.low_threshold.setRange(0, 100)
        self.low_threshold.setValue(self.settings.low_threshold)
        self.auto_threshold = QDoubleSpinBox()
        self.auto_threshold.setRange(0, 100)
        self.auto_threshold.setValue(self.settings.auto_threshold)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(0, 120)
        self.offset.setSuffix(" min")
        self.offset.setValue(self.settings.offset_minutes)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(1, 60)
        self.duration.setSuffix(" min")
        self.duration.setValue(self.settings.scan_duration_minutes)
        self.max_lines = QSpinBox()
        self.max_lines.setRange(5, 200)
        self.max_lines.setValue(self.settings.max_lines)
        self.size_filter = QCheckBox("Skip very small files (extras/menus)")
        self.size_filter.setChecked(self.settings.size_filter_enabled)
        self.size_ratio = QDoubleSpinBox()
        self.size_ratio.setRange(0.05, 0.9)
        self.size_ratio.setSingleStep(0.05)
        self.size_ratio.setValue(self.settings.size_filter_ratio)
        self.recursive = QCheckBox("Include subfolders when scanning")
        self.recursive.setChecked(getattr(self.settings, "recursive_scan", True))
        self.skip_samples = QCheckBox("Skip Sample/Samples folders")
        self.skip_samples.setChecked(getattr(self.settings, "skip_sample_folders", True))
        self.auto_resolve = QCheckBox("Auto-resolve duplicates & retry weak matches")
        self.auto_resolve.setChecked(getattr(self.settings, "auto_resolve_problems", True))

        form.addRow("Low confidence threshold", self.low_threshold)
        form.addRow("High confidence threshold", self.auto_threshold)
        form.addRow("Subtitle sample offset", self.offset)
        form.addRow("Subtitle scan duration", self.duration)
        form.addRow("Max dialogue lines", self.max_lines)
        form.addRow(self.size_filter)
        form.addRow("Size filter ratio vs median", self.size_ratio)
        form.addRow(self.recursive)
        form.addRow(self.skip_samples)
        form.addRow(self.auto_resolve)
        return w

    def _rename_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.rename_format = QLineEdit(self.settings.rename_format)
        self.move_season = QCheckBox("Organize into Season XX folders")
        self.move_season.setChecked(self.settings.move_to_season)
        self.skip_named = QCheckBox("Skip files already named SxxExx")
        self.skip_named.setChecked(self.settings.skip_already_named)
        form.addRow("Rename format", self.rename_format)
        form.addRow(self.move_season)
        form.addRow(self.skip_named)
        form.addRow(
            QLabel(
                "Placeholders: {series} {season} {episode} {title} {ext}"
            )
        )
        return w

    def _llm_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.llm_enabled = QCheckBox("Enable optional LLM identification")
        self.llm_enabled.setChecked(self.settings.llm_enabled)
        self.llm_only_low = QCheckBox("Only use LLM when fuzzy confidence is low")
        self.llm_only_low.setChecked(self.settings.llm_only_when_low)
        self.llm_provider = QComboBox()
        self.llm_provider.addItems(["ollama", "gemini", "openai", "grok"])
        idx = self.llm_provider.findText(self.settings.llm_provider)
        self.llm_provider.setCurrentIndex(max(0, idx))
        self.llm_model = QLineEdit(self.settings.llm_model)
        self.ollama_url = QLineEdit(self.settings.ollama_base_url)
        self.gemini_key = QLineEdit(get_secret(KEY_GEMINI) or "")
        self.gemini_key.setEchoMode(QLineEdit.Password)
        self.openai_key = QLineEdit(get_secret(KEY_OPENAI) or "")
        self.openai_key.setEchoMode(QLineEdit.Password)
        self.grok_key = QLineEdit(get_secret(KEY_GROK) or "")
        self.grok_key.setEchoMode(QLineEdit.Password)

        form.addRow(self.llm_enabled)
        form.addRow(self.llm_only_low)
        form.addRow("Provider", self.llm_provider)
        form.addRow("Model", self.llm_model)
        form.addRow("Ollama base URL", self.ollama_url)
        form.addRow("Gemini API key", self.gemini_key)
        form.addRow("OpenAI API key", self.openai_key)
        form.addRow("Grok API key", self.grok_key)
        note = QLabel(
            "Only short subtitle text samples are sent when LLM is enabled. Video files never leave this computer."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return w

    def _system_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.dep_label = QLabel(summary_text() + f"\nOCR available: {ocr_available()}")
        self.dep_label.setWordWrap(True)
        install_btn = QPushButton("Install Missing Dependencies (apt)")
        install_btn.clicked.connect(self._install_deps)
        self.theme = QComboBox()
        self.theme.addItems(["system", "light", "dark"])
        idx = self.theme.findText(self.settings.theme)
        self.theme.setCurrentIndex(max(0, idx))

        form = QFormLayout()
        form.addRow("Theme", self.theme)
        layout.addWidget(self.dep_label)
        layout.addWidget(install_btn)
        layout.addLayout(form)
        layout.addStretch()
        return w

    def _test_tmdb(self) -> None:
        key = self.tmdb_key.text().strip()
        if not key:
            QMessageBox.warning(self, "TMDB", "Enter an API key first.")
            return
        try:
            msg = TMDBClient(key).test_connection()
            QMessageBox.information(self, "TMDB", msg)
        except TMDBError as exc:
            QMessageBox.critical(self, "TMDB", str(exc))

    def _install_deps(self) -> None:
        reply = QMessageBox.question(
            self,
            "Install dependencies",
            "This will run:\npkexec apt-get install -y ffmpeg mkvtoolnix tesseract-ocr\n\nContinue?",
        )
        if reply != QMessageBox.Yes:
            return
        code, out = install_dependencies()
        self.dep_label.setText(summary_text() + f"\nOCR available: {ocr_available()}")
        if code == 0:
            QMessageBox.information(self, "Dependencies", "Install finished successfully.\n\n" + out[-1500:])
        else:
            QMessageBox.warning(self, "Dependencies", f"Exit code {code}\n\n" + out[-1500:])

    def _refresh_cache_stats(self) -> None:
        durable = (
            self.durable_cache.isChecked()
            if hasattr(self, "durable_cache")
            else getattr(self.settings, "durable_cache", True)
        )
        st = get_cache_stats(durable)
        self.cache_stats_label.setText(
            f"<b>Cache root:</b> {st.root}<br>"
            f"TMDB files: {st.tmdb_files} · TVMaze: {st.tvmaze_files} · "
            f"Reference episodes: {st.refsubs_files}<br>"
            f"Approx size: {st.human_size()}"
        )

    def _open_cache_folder(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        durable = self.durable_cache.isChecked()
        st = get_cache_stats(durable)
        st.root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(st.root)))

    def _clear_tmdb(self) -> None:
        n = clear_tmdb(self.durable_cache.isChecked())
        self._refresh_cache_stats()
        QMessageBox.information(self, "Cache", f"Removed {n} TMDB cache file(s).")

    def _clear_tvmaze(self) -> None:
        n = clear_tvmaze(self.durable_cache.isChecked())
        self._refresh_cache_stats()
        QMessageBox.information(self, "Cache", f"Removed {n} TVMaze cache file(s).")

    def _clear_refsubs(self) -> None:
        n = clear_refsubs(self.durable_cache.isChecked())
        self._refresh_cache_stats()
        QMessageBox.information(self, "Cache", f"Removed {n} reference subtitle cache file(s).")

    def _clear_all_cache(self) -> None:
        if (
            QMessageBox.question(self, "Clear all caches", "Delete all TMDB, TVMaze, and reference subtitle caches?")
            != QMessageBox.Yes
        ):
            return
        n = clear_all(self.durable_cache.isChecked())
        self._refresh_cache_stats()
        QMessageBox.information(self, "Cache", f"Removed {n} file(s).")

    def _save(self) -> None:
        set_secret(KEY_TMDB, self.tmdb_key.text().strip() or None)
        set_secret(KEY_GEMINI, self.gemini_key.text().strip() or None)
        set_secret(KEY_OPENAI, self.openai_key.text().strip() or None)
        set_secret(KEY_GROK, self.grok_key.text().strip() or None)
        set_secret(KEY_WYZIE, self.wyzie_key.text().strip() or None)

        self.settings.use_tvmaze = self.use_tvmaze.isChecked()
        self.settings.use_reference_subs = self.use_refsubs.isChecked()
        self.settings.save_refsubs_to_cache = self.save_ref_cache.isChecked()
        self.settings.refsubs_network_policy = self.ref_policy.currentData() or "download-missing"
        self.settings.durable_cache = self.durable_cache.isChecked()
        self.settings.recursive_scan = self.recursive.isChecked()
        self.settings.skip_sample_folders = self.skip_samples.isChecked()
        self.settings.auto_resolve_problems = self.auto_resolve.isChecked()
        self.settings.low_threshold = float(self.low_threshold.value())
        self.settings.auto_threshold = float(self.auto_threshold.value())
        self.settings.offset_minutes = float(self.offset.value())
        self.settings.scan_duration_minutes = float(self.duration.value())
        self.settings.max_lines = int(self.max_lines.value())
        self.settings.size_filter_enabled = self.size_filter.isChecked()
        self.settings.size_filter_ratio = float(self.size_ratio.value())
        self.settings.rename_format = self.rename_format.text().strip() or self.settings.rename_format
        self.settings.move_to_season = self.move_season.isChecked()
        self.settings.skip_already_named = self.skip_named.isChecked()
        self.settings.llm_enabled = self.llm_enabled.isChecked()
        self.settings.llm_only_when_low = self.llm_only_low.isChecked()
        self.settings.llm_provider = self.llm_provider.currentText()
        self.settings.llm_model = self.llm_model.text().strip()
        self.settings.ollama_base_url = self.ollama_url.text().strip() or self.settings.ollama_base_url
        self.settings.theme = self.theme.currentText()
        self.accept()
