"""Scan folder, extract dialogue, match episodes, build rename plan."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from episodeid.config import Settings, get_secret, KEY_GEMINI
from episodeid.extractor import filter_by_size, list_video_files, sample_dialogue
from episodeid.llm import identify_with_llm
from episodeid.matcher import demote_duplicate_claims, match_dialogue
from episodeid.metadata import TMDBClient
from episodeid.models import Episode, MatchResult, ProgressEvent, RenamePlanRow, SeriesInfo
from episodeid.renamer import build_plan


ProgressCb = Callable[[ProgressEvent], None]


def _noop_progress(_: ProgressEvent) -> None:
    return None


def scan_and_identify(
    *,
    folder: Path,
    series: SeriesInfo,
    episodes: list[Episode] | None = None,
    settings: Settings | None = None,
    api_key: str | None = None,
    progress: ProgressCb | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[RenamePlanRow]:
    """Identify all videos in folder. Never renames files."""
    settings = settings or Settings()
    progress = progress or _noop_progress
    cancel_check = cancel_check or (lambda: False)
    folder = Path(folder)

    if episodes is None:
        if not api_key:
            raise ValueError("TMDB API key required when episodes not provided")
        progress(ProgressEvent("metadata", 0, 1, "Fetching episode list from TMDB…"))
        client = TMDBClient(api_key)
        episodes = client.get_all_episodes(series.id)

    progress(ProgressEvent("scan", 0, 1, f"Scanning {folder}"))
    files = list_video_files(folder)
    keep, skipped = filter_by_size(
        files,
        enabled=settings.size_filter_enabled,
        ratio=settings.size_filter_ratio,
    )
    if skipped:
        progress(
            ProgressEvent(
                "scan",
                0,
                len(keep),
                f"Skipping {len(skipped)} small file(s) (extras/menus)",
            )
        )

    results: list[MatchResult] = []
    total = len(keep)
    for idx, path in enumerate(keep, start=1):
        if cancel_check():
            break
        progress(
            ProgressEvent(
                "extract",
                idx,
                total,
                f"Extracting dialogue ({idx}/{total}): {path.name}",
                path=str(path),
            )
        )
        sample = sample_dialogue(
            path,
            offset_minutes=settings.offset_minutes,
            scan_duration_minutes=settings.scan_duration_minutes,
            max_lines=settings.max_lines,
        )
        if sample.is_empty():
            results.append(
                MatchResult(
                    path=path,
                    error=sample.track_info or "No dialogue extracted",
                    dialogue_source=sample.source,
                )
            )
            continue

        progress(
            ProgressEvent(
                "match",
                idx,
                total,
                f"Matching ({idx}/{total}): {path.name}",
                path=str(path),
            )
        )
        match = match_dialogue(
            sample.raw_text,
            episodes,
            path=path,
            dialogue_source=sample.source,
            low_threshold=settings.low_threshold,
            auto_threshold=settings.auto_threshold,
            lines=sample.lines,
        )

        if (
            settings.llm_enabled
            and (
                not settings.llm_only_when_low
                or match.low_confidence
                or match.error
            )
        ):
            api = None
            if settings.llm_provider == "gemini":
                api = get_secret(KEY_GEMINI)
            llm_match = identify_with_llm(
                provider=settings.llm_provider,
                series_name=series.name,
                dialogue=sample.raw_text,
                episodes=episodes,
                api_key=api,
                model=settings.llm_model,
                ollama_base_url=settings.ollama_base_url,
                path=path,
            )
            if not llm_match.error and llm_match.season is not None:
                # Prefer LLM when fuzzy is low
                if match.low_confidence or match.error or llm_match.confidence >= match.confidence:
                    match = llm_match
                    match.dialogue_source = sample.source
                    if "llm" not in match.flags:
                        match.flags.append("llm")

        results.append(match)

    results = demote_duplicate_claims(results)
    progress(ProgressEvent("plan", total, total, "Building rename plan…"))
    plan = build_plan(
        results,
        series_name=series.name,
        scan_root=folder,
        move_to_season=settings.move_to_season,
        fmt=settings.rename_format,
        low_threshold=settings.low_threshold,
        auto_threshold=settings.auto_threshold,
        skip_already_named=settings.skip_already_named,
    )
    progress(ProgressEvent("done", total, total, f"Identified {len(plan)} file(s)"))
    return plan
