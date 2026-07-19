"""Classify partials, multiparts, content duplicates, and problem rows."""

from __future__ import annotations

import re
from pathlib import Path

from rapidfuzz import fuzz

from episodeid.extractor import detect_multipart, season_hint_from_path
from episodeid.models import MatchResult
from episodeid.renamer import is_already_named

_ALREADY_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")


def parse_named_episode(filename: str) -> tuple[int, int] | None:
    m = _ALREADY_RE.search(filename)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def classify_duration(
    duration_sec: float,
    expected_runtime_min: float | None,
) -> list[str]:
    flags: list[str] = []
    if duration_sec <= 0:
        return flags
    d_min = duration_sec / 60.0
    if expected_runtime_min and expected_runtime_min > 5:
        ratio = d_min / expected_runtime_min
        if ratio < 0.40:
            flags.append("partial_or_extra")
        elif ratio < 0.75:
            flags.append("possibly_partial")
        elif ratio > 2.5:
            flags.append("multi_episode_or_feature")
    else:
        # Heuristic without runtime
        if d_min > 90:
            flags.append("multi_episode_or_feature")
        elif d_min < 8:
            flags.append("possibly_short")
    return flags


def apply_file_flags(
    result: MatchResult,
    *,
    duration_sec: float = 0.0,
    expected_runtime: int | None = None,
    path: Path | None = None,
) -> MatchResult:
    path = path or result.path
    flags = list(result.flags)
    for f in classify_duration(duration_sec, float(expected_runtime) if expected_runtime else None):
        if f not in flags:
            flags.append(f)

    part = detect_multipart(path.name)
    if part is not None:
        if "multipart" not in flags:
            flags.append("multipart")
        result.flags = flags
        # stash part number in flags as multipart:N for renamer later
        mp = f"multipart:{part}"
        if mp not in flags:
            flags.append(mp)

    named = parse_named_episode(path.name)
    if named and result.season and result.episode:
        if named != (result.season, result.episode):
            if "name_mismatch" not in flags:
                flags.append("name_mismatch")

    # Don't auto-select partials/mega
    if any(x in flags for x in ("partial_or_extra", "multi_episode_or_feature")):
        result.low_confidence = True
        if result.confidence < 90:
            # demote so renamer won't auto-select
            result.confidence = min(result.confidence, 54.0)

    result.flags = flags
    return result


def mark_content_duplicates(results: list[MatchResult], threshold: float = 90.0) -> list[MatchResult]:
    """If two files share nearly identical dialogue, keep larger file selected."""
    texts: list[tuple[int, str, int]] = []
    for i, r in enumerate(results):
        text = " ".join(r.dialogue_lines) if r.dialogue_lines else ""
        if len(text) < 40:
            continue
        try:
            size = r.path.stat().st_size if r.path.exists() else 0
        except OSError:
            size = 0
        texts.append((i, text, size))

    for a in range(len(texts)):
        for b in range(a + 1, len(texts)):
            i, ti, si = texts[a]
            j, tj, sj = texts[b]
            sim = float(fuzz.token_set_ratio(ti[:3000], tj[:3000]))
            if sim < threshold:
                continue
            # Prefer larger file as primary
            keep, drop = (i, j) if si >= sj else (j, i)
            for idx in (drop,):
                r = results[idx]
                if "content_duplicate" not in r.flags:
                    r.flags.append("content_duplicate")
                r.low_confidence = True
                # Force unselected later via confidence
                if r.confidence > 50:
                    r.confidence = min(r.confidence, 50.0)
            # tag keeper
            if "content_primary" not in results[keep].flags:
                results[keep].flags.append("content_primary")
    return results


def is_problem_result(r: MatchResult) -> bool:
    # Season-disc layout identities are fixed; auto-resolve must not re-open holes
    if "sequential_disc" in (r.flags or []):
        return False
    if r.error:
        return True
    bad_flags = {
        "poor_ocr",
        "no_match",
        "duplicate_claim",
        "low_confidence",
        "content_duplicate",
        "partial_or_extra",
        "name_mismatch",
    }
    if any(f in r.flags for f in bad_flags):
        return True
    if r.season is None or r.episode is None:
        return True
    return False


def season_path_boost(path: Path, season: int, base_score: float) -> float:
    hint = season_hint_from_path(path)
    if hint is None:
        return base_score
    if hint == season:
        return min(100.0, base_score + 6.0)
    return max(0.0, base_score - 3.0)
