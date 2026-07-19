"""Scan folder (recursive), extract dialogue, match episodes, build rename plan."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from episodeid.config import KEY_GEMINI, KEY_WYZIE, Settings, data_dir, get_secret
from episodeid.edge_cases import (
    apply_file_flags,
    is_problem_result,
    mark_content_duplicates,
    season_path_boost,
)
from episodeid.extractor import (
    discover_disc_folders,
    filter_by_size,
    list_video_files,
    probe_duration_seconds,
    sample_dialogue,
    season_hint_from_path,
)
from episodeid.splitter import (
    apply_covered_filter,
    identify_segment,
    inventory_segments,
    is_multi_episode_candidate,
    median_runtime_minutes,
    scan_output_library_for_episodes,
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
        from episodeid.edge_cases import parse_named_episode

        named = parse_named_episode(path.name)
        season = episode = None
        title = ""
        if named:
            season, episode = named
            for ep in episodes:
                if ep.season == season and ep.episode == episode:
                    title = ep.title
                    break
        return (
            MatchResult(
                path=path,
                season=season,
                episode=episode,
                title=title or None,
                confidence=95.0 if named else 0.0,
                low_confidence=not bool(named),
                error=None if named else "Already-named but could not parse SxxExx",
                flags=["already_named", "trusted_filename"] if named else ["already_named"],
                dialogue_source="filename",
                sample_quality=100.0,
            ),
            # Zero scores so unique reassignment does not steal trusted names
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
    session_log: Any | None = None,
) -> list[RenamePlanRow]:
    """Identify all videos in folder (and subfolders). Never renames files."""
    from episodeid.session_log import SessionLog

    settings = settings or Settings()
    progress = progress or _noop_progress
    cancel_check = cancel_check or (lambda: False)
    folder = Path(folder)

    slog = session_log or SessionLog("scan")
    slog.save_settings_snapshot(settings)
    slog.log(
        "scan_config",
        "Starting scan",
        folder=str(folder),
        series=series.name,
        series_id=series.id,
    )

    def _progress(ev: ProgressEvent) -> None:
        slog.progress_callback(ev)
        progress(ev)

    if episodes is None:
        if not api_key:
            raise ValueError("TMDB API key required when episodes not provided")
        _progress(ProgressEvent("metadata", 0, 1, "Fetching episode list from TMDB…"))
        client = TMDBClient(api_key)
        episodes = client.get_all_episodes(series.id)

    if getattr(settings, "use_tvmaze", True):
        _progress(ProgressEvent("metadata", 0, 1, "Enriching episode plots via TVMaze (free)…"))
        try:
            episodes = enrich_episodes_with_tvmaze(list(episodes), series.name)
        except Exception as exc:
            _progress(ProgressEvent("metadata", 0, 1, f"TVMaze skipped: {exc}"))

    all_episodes = list(episodes)
    output_root = _resolve_output_root(folder, settings)

    # Disc-by-disc when scanning a parent of many disc folders
    discs = discover_disc_folders(folder)
    use_disc_mode = (
        getattr(settings, "disc_by_disc_scan", True)
        and len(discs) >= 2
        and not (getattr(settings, "season_filter", None) or 0)
    )
    if use_disc_mode:
        _progress(
            ProgressEvent(
                "scan",
                0,
                len(discs),
                f"Full library mode: {len(discs)} disc folders — processing one disc at a time",
            )
        )
        combined: list[RenamePlanRow] = []
        for di, disc in enumerate(discs, start=1):
            if cancel_check():
                break
            season_hint = (
                season_hint_from_path(disc)
                if getattr(settings, "auto_season_from_folder", True)
                else None
            )
            label = f"Disc {di}/{len(discs)}: {disc.name}"
            if season_hint:
                label += f" (auto S{season_hint:02d})"
            _progress(ProgressEvent("scan", di, len(discs), label))
            slog.log("disc_start", label, path=str(disc), season_hint=season_hint)
            disc_eps = all_episodes
            if season_hint:
                disc_eps = [e for e in all_episodes if e.season == season_hint]
                if not disc_eps:
                    disc_eps = all_episodes
            part = _scan_one_tree(
                folder=disc,
                series=series,
                episodes=disc_eps,
                all_series_episodes=all_episodes,
                settings=settings,
                progress=_progress,
                cancel_check=cancel_check,
                season_hint=season_hint,
                library_root=folder,
            )
            combined.extend(part)
            slog.log("disc_end", f"Finished {disc.name}", rows=len(part))
        _progress(
            ProgressEvent(
                "done",
                len(discs),
                len(discs),
                f"All discs done — {len(combined)} plan row(s)",
            )
        )
        md = slog.finalize_scan(
            series_name=series.name,
            series_id=series.id,
            folder=str(folder),
            output_root=str(output_root),
            plan=combined,
            extra={"mode": "disc_by_disc", "disc_count": len(discs)},
        )
        _progress(ProgressEvent("done", len(combined), len(combined), f"Review log: {md}"))
        return combined

    # Single folder / explicit season filter path
    season_filter = getattr(settings, "season_filter", None) or None
    season_hint = None
    if season_filter and int(season_filter) > 0:
        sf = int(season_filter)
        episodes = [e for e in all_episodes if e.season == sf]
        _progress(
            ProgressEvent(
                "metadata",
                0,
                1,
                f"Season filter: S{sf:02d} only ({len(episodes)} episodes)",
            )
        )
        if not episodes:
            raise ValueError(f"No episodes found for season {sf}")
    elif getattr(settings, "auto_season_from_folder", True):
        season_hint = season_hint_from_path(folder)
        if season_hint:
            episodes = [e for e in all_episodes if e.season == season_hint]
            if episodes:
                _progress(
                    ProgressEvent(
                        "metadata",
                        0,
                        1,
                        f"Auto season from folder: S{season_hint:02d} ({len(episodes)} episodes)",
                    )
                )
            else:
                episodes = all_episodes
                season_hint = None
        else:
            episodes = all_episodes
    else:
        episodes = all_episodes

    plan = _scan_one_tree(
        folder=folder,
        series=series,
        episodes=episodes,
        all_series_episodes=all_episodes,
        settings=settings,
        progress=_progress,
        cancel_check=cancel_check,
        season_hint=season_hint,
        library_root=folder,
    )
    md = slog.finalize_scan(
        series_name=series.name,
        series_id=series.id,
        folder=str(folder),
        output_root=str(output_root),
        plan=plan,
        extra={"mode": "single_tree", "season_hint": season_hint},
    )
    _progress(ProgressEvent("done", len(plan), len(plan), f"Review log: {md}"))
    return plan


def _attach_refs_for_episodes(
    episodes: list[Episode],
    series: SeriesInfo,
    settings: Settings,
    progress: ProgressCb,
) -> None:
    if not getattr(settings, "use_reference_subs", True):
        return
    wyzie_key = get_secret(KEY_WYZIE)
    policy = getattr(settings, "refsubs_network_policy", "download-missing") or "download-missing"
    save_cache = getattr(settings, "save_refsubs_to_cache", True)
    progress(
        ProgressEvent("metadata", 0, 1, f"Reference subtitles (policy={policy})…")
    )
    try:
        # When season-scoped, fetch all episodes in that list
        max_eps = max(len(episodes), 40)
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


def _scan_one_tree(
    *,
    folder: Path,
    series: SeriesInfo,
    episodes: list[Episode],
    all_series_episodes: list[Episode],
    settings: Settings,
    progress: ProgressCb,
    cancel_check: Callable[[], bool],
    season_hint: int | None,
    library_root: Path,
) -> list[RenamePlanRow]:
    """Pass A/B scan for one disc folder or single tree."""
    _attach_refs_for_episodes(episodes, series, settings, progress)

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

    # Size filter for extras; megas pulled aside when multi-episode detection is on
    keep, size_skipped = filter_by_size(
        files,
        enabled=settings.size_filter_enabled,
        ratio=settings.size_filter_ratio,
        max_ratio=2.5 if not getattr(settings, "detect_multi_episode", True) else 99.0,
    )
    # When multi-ep on, re-include large files that size filter would drop via max_ratio
    # (we set max_ratio high above). Still drop tiny extras from keep.
    med_rt = median_runtime_minutes(episodes)
    singles: list[Path] = []
    megas: list[Path] = []
    for p in keep:
        dur = probe_duration_seconds(p)
        if getattr(settings, "detect_multi_episode", True) and is_multi_episode_candidate(
            dur, median_runtime_min=med_rt
        ):
            megas.append(p)
        else:
            singles.append(p)

    # Also check size_skipped for multi-ep when max_ratio was not used
    if getattr(settings, "detect_multi_episode", True):
        for p in size_skipped:
            dur = probe_duration_seconds(p)
            if is_multi_episode_candidate(dur, median_runtime_min=med_rt):
                if p not in megas:
                    megas.append(p)

    progress(
        ProgressEvent(
            "scan",
            0,
            len(singles) + len(megas),
            f"Singles {len(singles)} · multi-episode candidates {len(megas)} · "
            f"skipped extras {len(size_skipped) - sum(1 for p in size_skipped if p in megas)}",
        )
    )

    # ----- Pass A: singles -----
    results: list[MatchResult] = []
    score_matrix: list[list[float]] = []
    total_a = len(singles)
    for idx, path in enumerate(singles, start=1):
        if cancel_check():
            break
        progress(
            ProgressEvent(
                "extract",
                idx,
                total_a,
                f"Single ({idx}/{total_a}): {path.name}",
                path=str(path),
            )
        )
        match, scores = _process_one_file(path, episodes, settings, series)
        results.append(match)
        score_matrix.append(scores)

    progress(ProgressEvent("plan", total_a, total_a, "Resolving unique assignments for singles…"))
    results = reassign_unique_episodes(
        results,
        episodes,
        score_matrix=score_matrix,
        low_threshold=settings.low_threshold,
        auto_threshold=settings.auto_threshold,
    )
    results = mark_content_duplicates(results)
    results = demote_duplicate_claims(results)

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
        progress(ProgressEvent("plan", total_a, total_a, "Auto-resolving singles…"))
        results = _second_pass_resolve(results, score_matrix, episodes, settings)
        results = demote_duplicate_claims(results)

    # Output library is based on the user's scan root (full library), not each disc subfolder
    output_root = _resolve_output_root(library_root, settings)
    plan = build_plan(
        results,
        series_name=series.name,
        scan_root=library_root,
        move_to_season=settings.move_to_season and not getattr(settings, "rename_in_place", False),
        fmt=settings.rename_format,
        low_threshold=settings.low_threshold,
        auto_threshold=settings.auto_threshold,
        skip_already_named=settings.skip_already_named,
        output_root=output_root,
        create_series_subfolder=getattr(settings, "output_create_series_subfolder", True),
        rename_in_place=getattr(settings, "rename_in_place", False),
    )

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

    for row in plan:
        if any(
            f in row.flags
            for f in ("content_duplicate", "partial_or_extra", "multi_episode_or_feature")
        ):
            row.selected = False
        if row.error:
            row.selected = False

    # Covered set from good single matches
    covered: dict[tuple[int, int], str] = {}
    for row in plan:
        if row.season is None or row.episode is None:
            continue
        if row.error or row.confidence < settings.low_threshold:
            continue
        if "content_duplicate" in row.flags:
            continue
        covered[(row.season, row.episode)] = str(row.path)

    if getattr(settings, "skip_split_if_in_output_library", True):
        lib_root = output_root
        if getattr(settings, "output_create_series_subfolder", True):
            from episodeid.renamer import sanitize_filename

            lib_root = output_root / sanitize_filename(series.name)
        for k, v in scan_output_library_for_episodes(lib_root).items():
            covered.setdefault(k, v)

    # ----- Pass B: multi-episode inventory + gap-only splits -----
    split_rows: list[RenamePlanRow] = []
    if getattr(settings, "detect_multi_episode", True) and megas:
        skip_covered = (
            getattr(settings, "skip_split_if_episode_present", True)
            and not getattr(settings, "force_splits_even_if_present", False)
        )
        for mi, mpath in enumerate(megas, start=1):
            if cancel_check():
                break
            progress(
                ProgressEvent(
                    "extract",
                    mi,
                    len(megas),
                    f"Inventory multi-episode ({mi}/{len(megas)}): {mpath.name}",
                    path=str(mpath),
                )
            )
            segs = inventory_segments(mpath, expected_runtime_min=med_rt)
            # Fast path: this disc already has as many good singles as segments → no need to split mega
            disc_root = mpath.parent
            disc_singles = 0
            try:
                disc_root_s = str(disc_root.resolve())
            except OSError:
                disc_root_s = str(disc_root)
            for row in plan:
                if row.season is None or row.confidence < settings.low_threshold:
                    continue
                try:
                    if str(Path(row.path).resolve()).startswith(disc_root_s + "/") or str(
                        Path(row.path).resolve()
                    ).startswith(disc_root_s):
                        # count files under same disc folder tree
                        rp = str(Path(row.path).resolve())
                        if rp == disc_root_s or rp.startswith(disc_root_s + "/"):
                            disc_singles += 1
                except OSError:
                    continue

            if skip_covered and segs and disc_singles >= len(segs):
                progress(
                    ProgressEvent(
                        "plan",
                        0,
                        1,
                        f"Skip mega {mpath.name}: disc already has {disc_singles} "
                        f"episode file(s) ≥ {len(segs)} segment(s)",
                    )
                )
                for seg in segs:
                    seg.skip = True
                    seg.skip_reason = "disc_has_enough_singles"
                    seg.flags.append("skip_disc_complete")
            else:
                for si, seg in enumerate(segs, start=1):
                    progress(
                        ProgressEvent(
                            "match",
                            si,
                            len(segs),
                            f"Identify segment {si}/{len(segs)} in {mpath.name} "
                            f"({seg.start/60:.1f}–{seg.end/60:.1f}m)",
                            path=str(mpath),
                        )
                    )
                    identify_segment(
                        seg,
                        episodes,
                        low_threshold=settings.low_threshold,
                        auto_threshold=settings.auto_threshold,
                    )
                segs = apply_covered_filter(segs, covered, skip_if_covered=skip_covered)

            for seg in segs:
                proposed = ""
                target = output_root
                if seg.season is not None and seg.episode is not None:
                    from episodeid.renamer import format_new_name, resolve_target_dir

                    proposed = format_new_name(
                        series=series.name,
                        season=seg.season,
                        episode=seg.episode,
                        title=seg.title or "Unknown",
                        ext=mpath.suffix,
                        fmt=settings.rename_format,
                    )
                    target = resolve_target_dir(
                        season=seg.season if settings.move_to_season else None,
                        scan_root=library_root,
                        output_root=output_root,
                        series_name=series.name,
                        move_to_season=settings.move_to_season,
                        create_series_subfolder=getattr(
                            settings, "output_create_series_subfolder", True
                        ),
                        source_path=mpath,
                    )

                if seg.skip:
                    kind = "inventory_skip"
                    selected = False
                    flags = list(seg.flags) + ["inventory_skip"]
                else:
                    kind = "split"
                    # Stricter than renames: avoid bad plot-only IDs creating junk files
                    selected = (
                        seg.season is not None
                        and not seg.error
                        and seg.confidence >= settings.auto_threshold
                    )
                    flags = list(seg.flags) + ["split_segment"]
                    if (
                        seg.season is not None
                        and not seg.error
                        and settings.low_threshold <= seg.confidence < settings.auto_threshold
                    ):
                        flags.append("review")

                # Mark covered so later megas don't re-propose
                if selected and seg.season is not None and seg.episode is not None:
                    covered[(seg.season, seg.episode)] = f"{mpath.name}@{seg.start:.0f}"

                split_rows.append(
                    RenamePlanRow(
                        path=mpath,
                        original_name=(
                            f"{mpath.name} [{seg.start/60:.1f}–{seg.end/60:.1f}m]"
                        ),
                        season=seg.season,
                        episode=seg.episode,
                        official_title=seg.title or "",
                        confidence=seg.confidence,
                        proposed_name=proposed or mpath.name,
                        target_dir=target,
                        selected=selected,
                        move_to_season=settings.move_to_season,
                        error=seg.error,
                        dialogue_source="segment_ocr",
                        flags=flags,
                        dialogue_lines=list(seg.dialogue_lines),
                        sample_quality=seg.sample_quality,
                        track_info=f"{seg.method} {seg.start:.1f}-{seg.end:.1f}s",
                        row_kind=kind,
                        split_start=seg.start,
                        split_end=seg.end,
                        skip_reason=seg.skip_reason,
                        covered_by=seg.covered_by,
                    )
                )

    plan = list(plan) + split_rows

    ok = sum(1 for r in plan if r.selected and r.row_kind == "rename")
    splits = sum(1 for r in plan if r.selected and r.row_kind == "split")
    skipped_inv = sum(1 for r in plan if r.row_kind == "inventory_skip")
    review = sum(
        1
        for r in plan
        if not r.selected and r.season and not r.error and r.row_kind != "inventory_skip"
    )
    failed = sum(1 for r in plan if r.error or (r.season is None and r.row_kind == "rename"))
    log_path = _write_scan_log(plan, series, folder)
    msg = (
        f"Matched {ok} · Splits {splits} · Skipped inventory {skipped_inv} · "
        f"Review {review} · Failed {failed}"
    )
    if log_path:
        msg += f" · log {log_path.name}"
    progress(ProgressEvent("done", len(plan), len(plan), msg))
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
