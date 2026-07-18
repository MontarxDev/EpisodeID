"""Scan folder (recursive), extract dialogue, match episodes, build rename plan."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from episodeid.config import KEY_GEMINI, KEY_WYZIE, Settings, data_dir, get_secret
from episodeid.edge_cases import (
    apply_file_flags,
    is_problem_result,
    mark_content_duplicates,
    season_path_boost,
)
from episodeid.extractor import (
    filter_by_size,
    list_video_files,
    probe_duration_seconds,
    sample_dialogue,
)
from episodeid.llm import identify_with_llm
from episodeid.matcher import (
    demote_duplicate_claims,
    match_dialogue,
    reassign_unique_episodes,
    score_all_episodes,
)
from episodeid.metadata import TMDBClient
from episodeid.models import Episode, MatchResult, ProgressEvent, RenamePlanRow, SeriesInfo
from episodeid.refsubs import attach_reference_subs
from episodeid.renamer import build_plan, is_already_named
from episodeid.tvmaze import enrich_episodes_with_tvmaze

ProgressCb = Callable[[ProgressEvent], None]


def _noop_progress(_: ProgressEvent) -> None:
    return None


def _resolve_output_root(scan_folder: Path, settings: Settings) -> Path:
    """Library root for Season folders (or flat renames)."""
    if getattr(settings, "rename_in_place", False):
        return scan_folder
    if getattr(settings, "output_same_as_scan", True):
        return scan_folder
    out = (getattr(settings, "output_folder", None) or "").strip()
    if not out:
        out = (getattr(settings, "last_output_folder", None) or "").strip()
    if out:
        return Path(out)
    return scan_folder


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


def _expected_runtime(episodes: list[Episode], season: int | None, episode: int | None) -> int | None:
    if season is None or episode is None:
        # median runtime of catalog if available
        rts = [e.runtime for e in episodes if e.runtime]
        if not rts:
            return None
        rts = sorted(rts)
        return rts[len(rts) // 2]
    for e in episodes:
        if e.season == season and e.episode == episode and e.runtime:
            return e.runtime
    rts = [e.runtime for e in episodes if e.runtime]
    if not rts:
        return None
    rts = sorted(rts)
    return rts[len(rts) // 2]


def _process_one_file(
    path: Path,
    episodes: list[Episode],
    settings: Settings,
    series: SeriesInfo,
) -> tuple[MatchResult, list[float]]:
    """Extract + match one video; returns (result, score_row)."""
    if settings.skip_already_named and is_already_named(path.name):
        return (
            MatchResult(
                path=path,
                error=None,
                flags=["already_named", "skipped"],
                dialogue_source="skipped",
                sample_quality=100.0,
            ),
            [0.0] * len(episodes),
        )

    sample = sample_dialogue(
        path,
        offset_minutes=settings.offset_minutes,
        scan_duration_minutes=settings.scan_duration_minutes,
        max_lines=settings.max_lines,
        prefer_english=True,
        adaptive=True,
    )
    duration = sample.duration_sec or probe_duration_seconds(path)

    if sample.is_empty() or sample.error in {
        "no_english_subtitles",
        "no_subtitle_tracks",
        "no_dialogue_extracted",
    }:
        result = MatchResult(
            path=path,
            error=sample.error or sample.track_info or "No dialogue extracted",
            dialogue_source=sample.source,
            dialogue_lines=list(sample.lines),
            sample_quality=sample.quality,
            track_info=sample.track_info,
            flags=["no_match"],
        )
        result = apply_file_flags(result, duration_sec=duration, path=path)
        return result, [0.0] * len(episodes)

    raw_scores = score_all_episodes(
        sample.raw_text,
        episodes,
        lines=sample.lines,
        sample_quality=sample.quality,
    )
    # Soft boost when nested under Season XX
    scores = [
        season_path_boost(path, ep.season, sc)
        for sc, ep in zip(raw_scores, episodes)
    ]

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
    # Prefer path-boosted ranking for primary pick when close
    if scores:
        best_j = max(range(len(scores)), key=lambda j: scores[j])
        if scores[best_j] >= match.confidence - 5 or match.error:
            ep = episodes[best_j]
            match.season = ep.season
            match.episode = ep.episode
            match.title = ep.title
            match.confidence = round(scores[best_j], 1)
            match.low_confidence = match.confidence < settings.low_threshold
            match.error = None

    if (
        settings.llm_enabled
        and sample.quality >= 35
        and (not settings.llm_only_when_low or match.low_confidence or match.error)
    ):
        api = get_secret(KEY_GEMINI) if settings.llm_provider == "gemini" else None
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

    exp_rt = _expected_runtime(episodes, match.season, match.episode)
    match = apply_file_flags(match, duration_sec=duration, expected_runtime=exp_rt, path=path)
    return match, scores


def _second_pass_resolve(
    results: list[MatchResult],
    score_matrix: list[list[float]],
    episodes: list[Episode],
    settings: Settings,
) -> list[MatchResult]:
    """Re-assign problem rows against still-free episodes."""
    if not settings.auto_resolve_problems:
        return results

    assigned: set[tuple[int, int]] = set()
    for r in results:
        if (
            r.season is not None
            and r.episode is not None
            and not r.low_confidence
            and "duplicate_claim" not in r.flags
            and "content_duplicate" not in r.flags
            and not r.error
        ):
            assigned.add((r.season, r.episode))

    # Multipart exceptions: same SxxExx allowed
    multipart_ok: set[tuple[int, int]] = set()
    for r in results:
        if r.season is None or r.episode is None:
            continue
        if any(f.startswith("multipart") for f in r.flags):
            multipart_ok.add((r.season, r.episode))

    problem_idx = [i for i, r in enumerate(results) if is_problem_result(r)]
    if not problem_idx:
        return results

    # Greedy: for each problem row, pick best free episode
    pairs: list[tuple[float, int, int]] = []
    for i in problem_idx:
        if i >= len(score_matrix):
            continue
        for j, sc in enumerate(score_matrix[i]):
            if sc < max(30.0, settings.low_threshold * 0.45):
                continue
            ep = episodes[j]
            key = (ep.season, ep.episode)
            if key in assigned and key not in multipart_ok:
                continue
            pairs.append((sc, i, j))
    pairs.sort(reverse=True, key=lambda x: x[0])

    used_files: set[int] = set()
    used_eps: set[int] = set()
    for sc, i, j in pairs:
        if i in used_files:
            continue
        ep = episodes[j]
        key = (ep.season, ep.episode)
        if key in used_eps and key not in multipart_ok:
            continue
        if key in assigned and key not in multipart_ok:
            continue
        r = results[i]
        r.season = ep.season
        r.episode = ep.episode
        r.title = ep.title
        r.confidence = round(sc, 1)
        r.error = None
        r.low_confidence = sc < settings.low_threshold
        flags = [f for f in r.flags if f not in {"duplicate_claim", "no_match", "low_confidence"}]
        if "retry_resolved" not in flags:
            flags.append("retry_resolved")
        if sc < settings.auto_threshold:
            flags.append("review" if sc >= settings.low_threshold else "low_confidence")
        r.flags = flags
        used_files.add(i)
        used_eps.add(j)
        if key not in multipart_ok:
            assigned.add(key)

    return results


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
    """Identify all videos in folder (and subfolders). Never renames files."""
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

    if getattr(settings, "use_tvmaze", True):
        progress(ProgressEvent("metadata", 0, 1, "Enriching episode plots via TVMaze (free)…"))
        try:
            episodes = enrich_episodes_with_tvmaze(episodes, series.name)
        except Exception as exc:
            progress(ProgressEvent("metadata", 0, 1, f"TVMaze skipped: {exc}"))

    if getattr(settings, "use_reference_subs", True):
        wyzie_key = get_secret(KEY_WYZIE)
        policy = getattr(settings, "refsubs_network_policy", "download-missing") or "download-missing"
        save_cache = getattr(settings, "save_refsubs_to_cache", True)
        progress(
            ProgressEvent(
                "metadata",
                0,
                1,
                f"Reference subtitles (policy={policy})…",
            )
        )
        try:
            max_eps = 80 if season_filter else 30
            stats = attach_reference_subs(
                episodes,
                series.id,
                api_key=wyzie_key,
                max_episodes=max_eps,
                progress=lambda m: progress(ProgressEvent("metadata", 0, 1, m)),
                policy=policy,
                save_to_cache=save_cache,
            )
            progress(ProgressEvent("metadata", 0, 1, stats.summary()))
        except Exception as exc:
            progress(ProgressEvent("metadata", 0, 1, f"Reference subs skipped: {exc}"))

    recursive = getattr(settings, "recursive_scan", True)
    skip_samples = getattr(settings, "skip_sample_folders", True)
    progress(ProgressEvent("scan", 0, 1, f"Scanning {folder}" + (" (recursive)" if recursive else "")))
    files = list_video_files(
        folder,
        recursive=recursive,
        skip_sample_folders=skip_samples,
    )
    n_dirs = len({p.parent for p in files}) if files else 0
    progress(
        ProgressEvent(
            "scan",
            0,
            1,
            f"Found {len(files)} video(s) in {n_dirs} folder(s)",
        )
    )

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
        match, scores = _process_one_file(path, episodes, settings, series)
        results.append(match)
        score_matrix.append(scores)

    progress(ProgressEvent("plan", total, total, "Resolving unique episode assignments…"))
    results = reassign_unique_episodes(
        results,
        episodes,
        score_matrix=score_matrix,
        low_threshold=settings.low_threshold,
        auto_threshold=settings.auto_threshold,
    )
    results = mark_content_duplicates(results)
    results = demote_duplicate_claims(results)

    # Multipart: allow same SxxExx — clear duplicate_claim between multiparts of same ep
    by_code: dict[tuple[int, int], list[int]] = {}
    for i, r in enumerate(results):
        if r.season is None or r.episode is None:
            continue
        by_code.setdefault((r.season, r.episode), []).append(i)
    for code, idxs in by_code.items():
        if len(idxs) < 2:
            continue
        if all(any(f.startswith("multipart") for f in results[i].flags) for i in idxs):
            for i in idxs:
                results[i].flags = [f for f in results[i].flags if f != "duplicate_claim"]
                results[i].low_confidence = results[i].confidence < settings.low_threshold

    if settings.auto_resolve_problems:
        progress(ProgressEvent("plan", total, total, "Auto-resolving duplicates & weak matches…"))
        results = _second_pass_resolve(results, score_matrix, episodes, settings)
        results = demote_duplicate_claims(results)

    progress(ProgressEvent("plan", total, total, "Building rename plan…"))
    output_root = _resolve_output_root(folder, settings)
    plan = build_plan(
        results,
        series_name=series.name,
        scan_root=folder,
        move_to_season=settings.move_to_season and not getattr(settings, "rename_in_place", False),
        fmt=settings.rename_format,
        low_threshold=settings.low_threshold,
        auto_threshold=settings.auto_threshold,
        skip_already_named=settings.skip_already_named,
        output_root=output_root,
        create_series_subfolder=getattr(settings, "output_create_series_subfolder", True),
        rename_in_place=getattr(settings, "rename_in_place", False),
    )

    # Multipart rename suffix
    for row in plan:
        part_flags = [f for f in row.flags if f.startswith("multipart:")]
        if part_flags and row.proposed_name and row.season is not None:
            try:
                part_n = int(part_flags[0].split(":")[1])
            except (IndexError, ValueError):
                part_n = 0
            if part_n and "Part " not in row.proposed_name:
                stem = Path(row.proposed_name).stem
                ext = Path(row.proposed_name).suffix
                row.proposed_name = f"{stem} - Part {part_n}{ext}"

    # Unselect problem rows that still failed
    for row in plan:
        if any(
            f in row.flags
            for f in ("content_duplicate", "partial_or_extra", "multi_episode_or_feature")
        ):
            row.selected = False
        if row.error:
            row.selected = False

    ok = sum(1 for r in plan if r.selected)
    review = sum(1 for r in plan if not r.selected and r.season and not r.error)
    failed = sum(1 for r in plan if r.error or r.season is None)
    log_path = _write_scan_log(plan, series, folder)
    msg = f"Matched {ok} · Review {review} · Failed {failed}"
    if log_path:
        msg += f" · log {log_path.name}"
    progress(ProgressEvent("done", total, total, msg))
    return plan


def retry_problem_rows(
    plan: list[RenamePlanRow],
    *,
    folder: Path,
    series: SeriesInfo,
    episodes: list[Episode],
    settings: Settings,
    progress: ProgressCb | None = None,
) -> list[RenamePlanRow]:
    """Re-extract/match only problem rows; merge back into plan."""
    progress = progress or _noop_progress
    problems = [
        (i, row)
        for i, row in enumerate(plan)
        if row.error
        or row.season is None
        or any(
            f in row.flags
            for f in ("low_confidence", "duplicate_claim", "poor_ocr", "no_match", "name_mismatch")
        )
    ]
    if not problems:
        progress(ProgressEvent("done", 0, 0, "No problem rows to retry"))
        return plan

    results: list[MatchResult] = []
    score_matrix: list[list[float]] = []
    # Keep good rows fixed as assigned
    for i, row in enumerate(plan):
        if any(i == pi for pi, _ in problems):
            continue
    # Build synthetic results for full reassignment of problem set only
    problem_results: list[MatchResult] = []
    problem_scores: list[list[float]] = []
    for n, (idx, row) in enumerate(problems, start=1):
        progress(
            ProgressEvent(
                "extract",
                n,
                len(problems),
                f"Retry ({n}/{len(problems)}): {row.original_name}",
                path=str(row.path),
            )
        )
        match, scores = _process_one_file(row.path, episodes, settings, series)
        problem_results.append(match)
        problem_scores.append(scores)

    problem_results = reassign_unique_episodes(
        problem_results,
        episodes,
        score_matrix=problem_scores,
        low_threshold=settings.low_threshold,
        auto_threshold=settings.auto_threshold,
    )
    problem_results = _second_pass_resolve(
        problem_results, problem_scores, episodes, settings
    )

    # Rebuild only problem indices into a mini plan then splice
    output_root = _resolve_output_root(folder, settings)
    mini = build_plan(
        problem_results,
        series_name=series.name,
        scan_root=folder,
        move_to_season=settings.move_to_season and not settings.rename_in_place,
        fmt=settings.rename_format,
        low_threshold=settings.low_threshold,
        auto_threshold=settings.auto_threshold,
        skip_already_named=False,
        output_root=output_root,
        create_series_subfolder=getattr(settings, "output_create_series_subfolder", True),
        rename_in_place=getattr(settings, "rename_in_place", False),
    )
    new_plan = list(plan)
    for (idx, _), new_row in zip(problems, mini):
        if "retry_resolved" not in new_row.flags:
            new_row.flags.append("retried")
        new_plan[idx] = new_row
    progress(ProgressEvent("done", len(problems), len(problems), f"Retried {len(problems)} row(s)"))
    return new_plan
