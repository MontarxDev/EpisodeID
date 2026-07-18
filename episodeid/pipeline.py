"""Scan folder, extract dialogue, match episodes, build rename plan."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from episodeid.config import KEY_GEMINI, Settings, data_dir, get_secret
from episodeid.extractor import filter_by_size, list_video_files, sample_dialogue
from episodeid.llm import identify_with_llm
from episodeid.matcher import (
    demote_duplicate_claims,
    match_dialogue,
    reassign_unique_episodes,
    score_all_episodes,
)
from episodeid.metadata import TMDBClient
from episodeid.models import Episode, MatchResult, ProgressEvent, RenamePlanRow, SeriesInfo
from episodeid.renamer import build_plan

ProgressCb = Callable[[ProgressEvent], None]


def _noop_progress(_: ProgressEvent) -> None:
    return None


def _write_scan_log(plan: list[RenamePlanRow], series: SeriesInfo, folder: Path) -> Path | None:
    try:
        out_dir = data_dir() / "scans"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"{stamp}.json"
        payload = {
            "created": stamp,
            "series": {"id": series.id, "name": series.name},
            "folder": str(folder),
            "rows": [r.to_dict() for r in plan],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except OSError:
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

    season_filter = getattr(settings, "season_filter", None) or None
    if season_filter and int(season_filter) > 0:
        sf = int(season_filter)
        episodes = [e for e in episodes if e.season == sf]
        progress(
            ProgressEvent(
                "metadata",
                0,
                1,
                f"Season filter: S{sf:02d} only ({len(episodes)} episodes)",
            )
        )
        if not episodes:
            raise ValueError(f"No episodes found for season {sf}")

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
                f"Identifying {len(keep)} episode-sized file(s); skipped {len(skipped)} extra/mega file(s)",
            )
        )

    results: list[MatchResult] = []
    score_matrix: list[list[float]] = []
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
            prefer_english=True,
            adaptive=True,
        )
        if sample.is_empty() or sample.error in {
            "no_english_subtitles",
            "no_subtitle_tracks",
            "no_dialogue_extracted",
        }:
            results.append(
                MatchResult(
                    path=path,
                    error=sample.error or sample.track_info or "No dialogue extracted",
                    dialogue_source=sample.source,
                    dialogue_lines=list(sample.lines),
                    sample_quality=sample.quality,
                    track_info=sample.track_info,
                    flags=["no_match"],
                )
            )
            score_matrix.append([0.0] * len(episodes))
            continue

        progress(
            ProgressEvent(
                "match",
                idx,
                total,
                f"Matching ({idx}/{total}): {path.name} (sample quality {sample.quality:.0f}%)",
                path=str(path),
            )
        )
        row_scores = score_all_episodes(
            sample.raw_text,
            episodes,
            lines=sample.lines,
            sample_quality=sample.quality,
        )
        score_matrix.append(row_scores)

        match = match_dialogue(
            sample.raw_text,
            episodes,
            path=path,
            dialogue_source=sample.source,
            low_threshold=settings.low_threshold,
            auto_threshold=settings.auto_threshold,
            lines=sample.lines,
            sample_quality=sample.quality,
            track_info=sample.track_info,
        )

        if (
            settings.llm_enabled
            and sample.quality >= 35
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
                if match.low_confidence or match.error or llm_match.confidence >= match.confidence:
                    llm_match.dialogue_source = sample.source
                    llm_match.dialogue_lines = list(sample.lines)
                    llm_match.sample_quality = sample.quality
                    llm_match.track_info = sample.track_info
                    if "llm" not in llm_match.flags:
                        llm_match.flags.append("llm")
                    match = llm_match

        results.append(match)

    progress(ProgressEvent("plan", total, total, "Resolving unique episode assignments…"))
    results = reassign_unique_episodes(
        results,
        episodes,
        score_matrix=score_matrix,
        low_threshold=settings.low_threshold,
        auto_threshold=settings.auto_threshold,
    )
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
    log_path = _write_scan_log(plan, series, folder)
    msg = f"Identified {len(plan)} file(s)"
    if log_path:
        msg += f" · log {log_path.name}"
    progress(ProgressEvent("done", total, total, msg))
    return plan
