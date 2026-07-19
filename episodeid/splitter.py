"""Detect multi-episode files, inventory segments, and split with ffmpeg."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from episodeid.deps import which
from episodeid.extractor import probe_duration_seconds, run_cmd, sample_dialogue
from episodeid.matcher import match_dialogue, score_all_episodes
from episodeid.models import Episode, MatchResult
from episodeid.renamer import format_new_name, resolve_target_dir, sanitize_filename

# Require this much more confidence before escalation may change episode identity
ESCALATE_IDENTITY_MARGIN = 8.0


@dataclass
class Chapter:
    index: int
    start: float
    end: float
    title: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class SplitSegment:
    source: Path
    start: float
    end: float
    season: int | None = None
    episode: int | None = None
    title: str = ""
    confidence: float = 0.0
    method: str = "chapters"  # chapters | auto | manual
    skip: bool = False
    skip_reason: str = ""
    covered_by: str = ""  # path of existing single file
    flags: list[str] = field(default_factory=list)
    dialogue_lines: list[str] = field(default_factory=list)
    sample_quality: float = 0.0
    error: str | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def code(self) -> str | None:
        if self.season is None or self.episode is None:
            return None
        return f"S{self.season:02d}E{self.episode:02d}"


def probe_chapters_ffprobe(path: Path) -> list[Chapter]:
    ffprobe = which("ffprobe")
    if not ffprobe:
        return []
    proc = run_cmd(
        [
            ffprobe,
            "-v",
            "error",
            "-show_chapters",
            "-of",
            "json",
            str(path),
        ],
        timeout=120,
    )
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    out: list[Chapter] = []
    for i, ch in enumerate(data.get("chapters") or []):
        try:
            start = float(ch.get("start_time") or 0)
            end = float(ch.get("end_time") or start)
        except (TypeError, ValueError):
            continue
        tags = ch.get("tags") or {}
        out.append(
            Chapter(
                index=i,
                start=start,
                end=end,
                title=str(tags.get("title") or ""),
            )
        )
    return out


def probe_chapters_mkvmerge(path: Path) -> list[Chapter]:
    """Fallback chapter probe via mkvmerge -J / chapter XML extract."""
    mkvmerge = which("mkvmerge")
    if not mkvmerge:
        return []
    # Prefer identify JSON when it embeds chapter times (varies by version)
    proc = run_cmd([mkvmerge, "-J", str(path)], timeout=120)
    if proc.returncode == 0:
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            data = {}
        # Some builds only report num_entries; then fall through to XML
        chapters_block = data.get("chapters") or []
        # Try nested structure if present
        atoms: list[dict] = []

        def _walk(obj: object) -> None:
            if isinstance(obj, dict):
                if "ChapterTimeStart" in obj or "chapter_time_start" in obj:
                    atoms.append(obj)
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    _walk(v)

        _walk(chapters_block)
        if atoms:
            out: list[Chapter] = []
            for i, a in enumerate(atoms):
                # nanosecond strings sometimes
                def _ts(key: str) -> float:
                    raw = a.get(key) or a.get(key.replace("Chapter", "chapter")) or 0
                    if isinstance(raw, (int, float)):
                        # if looks like ns
                        return float(raw) / 1e9 if float(raw) > 1e6 else float(raw)
                    s = str(raw)
                    # HH:MM:SS.nnnnnnnnn
                    parts = s.replace(".", ":").split(":")
                    try:
                        if len(parts) >= 3:
                            h, m, sec = int(parts[0]), int(parts[1]), float(parts[2] + ("." + parts[3] if len(parts) > 3 else ""))
                            return h * 3600 + m * 60 + sec
                    except ValueError:
                        pass
                    try:
                        return float(s) / 1e9 if float(s) > 1e6 else float(s)
                    except ValueError:
                        return 0.0

                start = _ts("ChapterTimeStart") if "ChapterTimeStart" in a else _ts("chapter_time_start")
                end = _ts("ChapterTimeEnd") if "ChapterTimeEnd" in a else _ts("chapter_time_end")
                if end <= start:
                    continue
                title = ""
                displays = a.get("ChapterDisplays") or a.get("chapter_displays") or []
                if displays and isinstance(displays, list):
                    title = str((displays[0] or {}).get("ChapterString") or "")
                out.append(Chapter(index=i, start=start, end=end, title=title))
            if out:
                return out

    # XML extract fallback
    mkvextract = which("mkvextract")
    if not mkvextract:
        return []
    import tempfile

    with tempfile.TemporaryDirectory(prefix="episodeid_ch_") as td:
        xml_path = Path(td) / "chapters.xml"
        proc = run_cmd(
            [mkvextract, str(path), "chapters", str(xml_path)],
            timeout=120,
        )
        if proc.returncode != 0 or not xml_path.exists():
            return []
        text = xml_path.read_text(encoding="utf-8", errors="replace")
    # Minimal XML parse without extra deps
    times = re.findall(
        r"<ChapterTimeStart>([^<]+)</ChapterTimeStart>\s*<ChapterTimeEnd>([^<]+)</ChapterTimeEnd>",
        text,
        flags=re.I,
    )
    titles = re.findall(r"<ChapterString>([^<]*)</ChapterString>", text, flags=re.I)

    def _parse_ts(s: str) -> float:
        s = s.strip()
        # 00:24:23.261800000
        m = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", s)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        try:
            return float(s)
        except ValueError:
            return 0.0

    out = []
    for i, (st, en) in enumerate(times):
        start, end = _parse_ts(st), _parse_ts(en)
        if end <= start:
            continue
        title = titles[i] if i < len(titles) else ""
        out.append(Chapter(index=i, start=start, end=end, title=title))
    return out


def probe_chapters(path: Path) -> list[Chapter]:
    """Best available chapter list (ffprobe first, mkvmerge/mkvextract fallback)."""
    ch = probe_chapters_ffprobe(path)
    if len(ch) >= 2:
        return ch
    ch2 = probe_chapters_mkvmerge(path)
    return ch2 if len(ch2) > len(ch) else ch


def is_multi_episode_candidate(
    duration_sec: float,
    *,
    median_runtime_min: float = 22.0,
    min_factor: float = 2.2,
    min_duration_min: float = 45.0,
) -> bool:
    if duration_sec <= 0:
        return False
    d_min = duration_sec / 60.0
    if d_min < min_duration_min:
        return False
    return d_min >= max(min_factor * max(median_runtime_min, 8.0), min_duration_min)


def cluster_chapters_into_episodes(
    chapters: list[Chapter],
    *,
    file_duration: float,
    expected_runtime_min: float = 22.0,
    short_chapter_max: float = 120.0,
) -> list[tuple[float, float]]:
    """Return list of (start, end) episode-length segments from chapters.

    Heuristic used for DVD dumps like Clone Wars C1: groups of long chapters
    separated by short credit/bumper chapters.
    """
    if not chapters or file_duration <= 0:
        return []

    target = max(8.0, expected_runtime_min) * 60.0
    # Boundary candidates: start of chapter after a short chapter
    boundaries = [chapters[0].start]
    for i, ch in enumerate(chapters[:-1]):
        if ch.duration <= short_chapter_max and ch.duration >= 0:
            # next chapter start is a potential new episode
            nxt = chapters[i + 1]
            # only if we've accumulated roughly an episode since last boundary
            if nxt.start - boundaries[-1] >= target * 0.55:
                boundaries.append(nxt.start)

    # Ensure last end
    ends = boundaries[1:] + [file_duration]
    segments = list(zip(boundaries, ends))

    # Merge tiny trailing fragments into previous
    cleaned: list[tuple[float, float]] = []
    for start, end in segments:
        if cleaned and (end - start) < target * 0.35:
            prev_s, _ = cleaned[-1]
            cleaned[-1] = (prev_s, end)
        else:
            cleaned.append((start, end))

    # If we got only 1 segment but file is multi-length, fall through to grid
    if len(cleaned) <= 1 and file_duration >= target * 2.2:
        return auto_grid_segments(file_duration, expected_runtime_min)

    # If way too many tiny segments, re-grid
    if len(cleaned) >= 3:
        avg = sum(e - s for s, e in cleaned) / len(cleaned)
        if avg < target * 0.45:
            return auto_grid_segments(file_duration, expected_runtime_min)

    return cleaned


def auto_grid_segments(
    file_duration: float,
    expected_runtime_min: float = 22.0,
    *,
    force_n: int | None = None,
) -> list[tuple[float, float]]:
    target = max(8.0, expected_runtime_min) * 60.0
    if file_duration < target * 1.5 and not force_n:
        return [(0.0, file_duration)]
    if force_n is not None and force_n >= 2:
        n = force_n
    else:
        n = max(2, int(round(file_duration / target)))
    # Adjust n so remainder isn't tiny (unless forced)
    seg = file_duration / n
    if force_n is None:
        while n > 2 and seg < target * 0.55:
            n -= 1
            seg = file_duration / n
    else:
        seg = file_duration / n
    segs: list[tuple[float, float]] = []
    for i in range(n):
        start = i * seg
        end = file_duration if i == n - 1 else (i + 1) * seg
        segs.append((start, end))
    return segs


def expected_segment_count(
    file_duration: float,
    expected_runtime_min: float = 22.0,
) -> int:
    """Estimate how many episodes fit in a multi-ep file from duration."""
    target = max(8.0, expected_runtime_min) * 60.0
    if file_duration <= 0 or target <= 0:
        return 1
    return max(1, int(round(file_duration / target)))


def chapters_as_episode_segments(
    chapters: list[Chapter],
    file_duration: float,
    *,
    min_ep_min: float = 12.0,
    max_ep_min: float = 40.0,
    min_cover_ratio: float = 0.85,
) -> list[tuple[float, float]] | None:
    """If MKV chapters already look like one episode each, return their spans.

    S7 Blu-ray rips: 4 chapters of ~18–27 min — use as-is (never equal-time grid).
    Returns None when chapters look like menus/bumpers instead.
    """
    if not chapters or file_duration <= 0:
        return None
    min_s = min_ep_min * 60.0
    max_s = max_ep_min * 60.0
    # Prefer chapters already episode-length
    ep_chs = [ch for ch in chapters if min_s <= ch.duration <= max_s]
    if len(ep_chs) < 2:
        # Sometimes last chapter slightly short/long — allow slightly wider band
        ep_chs = [
            ch
            for ch in chapters
            if (min_s * 0.75) <= ch.duration <= (max_s * 1.15) and ch.duration >= 8 * 60
        ]
    if len(ep_chs) < 2:
        return None
    # Cover most of the file
    covered = sum(ch.duration for ch in ep_chs)
    if covered < file_duration * min_cover_ratio:
        return None
    # Sort and use exact chapter boundaries
    ep_chs = sorted(ep_chs, key=lambda c: c.start)
    pairs = [(ch.start, ch.end) for ch in ep_chs]
    # Extend last end to file duration if tiny tail remains
    if pairs and file_duration - pairs[-1][1] < 90 and pairs[-1][1] < file_duration:
        s, _ = pairs[-1]
        pairs[-1] = (s, file_duration)
    # Ensure first starts at 0 if tiny lead-in
    if pairs and pairs[0][0] > 0 and pairs[0][0] < 30:
        pairs[0] = (0.0, pairs[0][1])
    return pairs


def inventory_segments(
    path: Path,
    *,
    expected_runtime_min: float = 22.0,
) -> list[SplitSegment]:
    """Build un-identified segments for a multi-episode file.

    Priority:
      1. Episode-length MKV chapters (S7: 4 chapters → 4 segments) — never
         overridden by duration/median math.
      2. Short-bumper chapter clustering (S1-style megas).
      3. Equal duration grid as last resort.
    """
    path = Path(path)
    duration = probe_duration_seconds(path)
    if duration <= 0:
        return []
    expected_n = expected_segment_count(duration, expected_runtime_min)
    chapters = probe_chapters(path)
    method = "auto"
    pairs: list[tuple[float, float]] = []

    if chapters:
        # Rule A: chapters already = episodes (MKVToolNix "before chapters")
        direct = chapters_as_episode_segments(chapters, duration)
        if direct:
            pairs = direct
            method = "mkv_chapters"
        else:
            pairs = cluster_chapters_into_episodes(
                chapters,
                file_duration=duration,
                expected_runtime_min=expected_runtime_min,
            )
            method = "chapter_cluster"
            # Only re-grid when clustering failed (not when Rule A succeeded)
            if expected_n >= 3 and len(pairs) < expected_n - 1:
                pairs = auto_grid_segments(
                    duration, expected_runtime_min, force_n=expected_n
                )
                method = "auto_grid"
            elif len(pairs) >= expected_n + 2 and expected_n >= 2:
                pairs = auto_grid_segments(
                    duration, expected_runtime_min, force_n=expected_n
                )
                method = "auto_grid"
    else:
        pairs = auto_grid_segments(
            duration,
            expected_runtime_min,
            force_n=expected_n if expected_n >= 2 else None,
        )
        method = "auto_grid"

    return [
        SplitSegment(source=path, start=s, end=e, method=method)
        for s, e in pairs
        if e - s >= 30.0
    ]


def _segment_sample_windows(
    segment: SplitSegment,
    *,
    n_windows: int = 3,
) -> list[tuple[float, float]]:
    """Return list of (offset_min, scan_min) inside the segment.

    Pass 1 ~12%, pass 2 ~45%, pass 3 ~70% into the segment.
    """
    duration_min = max(0.5, (segment.end - segment.start) / 60.0)
    seg_start = segment.start / 60.0
    seg_end = segment.end / 60.0
    scan_min = min(6.0, max(2.0, duration_min * 0.28))
    fracs = [0.12, 0.45, 0.70][: max(1, n_windows)]
    windows: list[tuple[float, float]] = []
    for frac in fracs:
        offset = seg_start + duration_min * frac
        this_scan = scan_min
        if offset + this_scan > seg_end:
            this_scan = max(1.0, seg_end - offset - 0.05)
        if this_scan < 0.8:
            continue
        # keep window fully inside segment
        if offset < seg_start:
            offset = seg_start
        windows.append((offset, this_scan))
    return windows or [(seg_start + duration_min * 0.12, min(4.0, duration_min * 0.4))]


def identify_segment(
    segment: SplitSegment,
    episodes: list[Episode],
    *,
    low_threshold: float = 55.0,
    auto_threshold: float = 70.0,
    escalate_enabled: bool = True,
    escalate_below: float = 80.0,
    max_extra_samples: int = 2,
    progress: Callable[[str], None] | None = None,
) -> SplitSegment:
    """Sample dialogue inside segment window and match.

    Fast first pass; if confidence is below ``escalate_below``, sample more
    windows inside the same segment and re-match (merged + best window).
    """
    n_total = 1 + (max(0, int(max_extra_samples)) if escalate_enabled else 0)
    windows = _segment_sample_windows(segment, n_windows=n_total)

    all_lines: list[str] = []
    seen: set[str] = set()
    best_match: MatchResult | None = None
    first_match: MatchResult | None = None
    best_quality = 0.0
    last_error: str | None = None
    samples_taken = 0
    escalated = False

    def _add_lines(lines: list[str]) -> None:
        for ln in lines:
            key = (ln or "").strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            all_lines.append(ln)

    def _code(m: MatchResult | None) -> tuple[int, int] | None:
        if m is None or m.season is None or m.episode is None:
            return None
        return (int(m.season), int(m.episode))

    def _prefer_match(current: MatchResult | None, candidate: MatchResult) -> MatchResult:
        """Higher conf wins, but do not flip identity without a clear margin over first pass."""
        if current is None:
            return candidate
        if candidate.confidence <= current.confidence:
            return current
        # Allow conf improvement for the *same* episode always
        if _code(candidate) == _code(current) or _code(candidate) == _code(first_match):
            return candidate
        # Identity change: require clear margin vs first-pass pick
        anchor = first_match or current
        if _code(candidate) != _code(anchor):
            if candidate.confidence < (anchor.confidence + ESCALATE_IDENTITY_MARGIN):
                return current
        return candidate

    for wi, (offset_min, scan_min) in enumerate(windows):
        if wi > 0:
            escalated = True
            if progress:
                progress(
                    f"extra sample {wi}/{len(windows) - 1} "
                    f"@ {offset_min:.1f}m ({scan_min:.1f}m window)"
                )

        sample = sample_dialogue(
            segment.source,
            offset_minutes=offset_min,
            scan_duration_minutes=scan_min,
            max_lines=35,
            prefer_english=True,
            adaptive=False,
        )
        samples_taken += 1
        if sample.is_empty() or sample.quality < 30:
            last_error = sample.error or "poor_ocr"
            # First pass hard-empty: still try next window if escalate on
            if wi == 0 and not escalate_enabled:
                segment.error = last_error
                segment.confidence = 0.0
                return segment
            continue

        _add_lines(list(sample.lines))
        best_quality = max(best_quality, sample.quality)

        match = match_dialogue(
            sample.raw_text,
            episodes,
            path=segment.source,
            dialogue_source=sample.source,
            low_threshold=low_threshold,
            auto_threshold=auto_threshold,
            lines=sample.lines,
            sample_quality=sample.quality,
            track_info=sample.track_info,
        )
        if first_match is None:
            first_match = match
        best_match = _prefer_match(best_match, match)

        # Stop early if already strong enough on first-pass identity
        if match.confidence >= escalate_below and (
            first_match is None or _code(match) == _code(first_match)
        ):
            break
        if match.confidence >= escalate_below and first_match and _code(match) != _code(first_match):
            # Strong alternate — only stop if margin is clear
            if match.confidence >= first_match.confidence + ESCALATE_IDENTITY_MARGIN:
                break
        if not escalate_enabled:
            break
        # Hard permanent errors: no more windows help
        if match.error in {"no_english_subtitles", "no_subtitle_tracks"}:
            break

    if not all_lines and best_match is None:
        segment.error = last_error or "no_dialogue_extracted"
        segment.confidence = 0.0
        if escalated:
            segment.flags = list(segment.flags) + ["escalated_sample"]
        return segment

    # Merged re-score — can raise conf, but identity flip needs margin
    if len(all_lines) >= 3:
        from episodeid.textutil import join_dialogue

        merged_text = join_dialogue(all_lines)
        merged = match_dialogue(
            merged_text,
            episodes,
            path=segment.source,
            dialogue_source="segment_ocr_merged",
            low_threshold=low_threshold,
            auto_threshold=auto_threshold,
            lines=all_lines[:50],
            sample_quality=best_quality,
            track_info=f"merged {samples_taken} window(s)",
        )
        best_match = _prefer_match(best_match, merged)
        # If first-pass identity kept, still adopt higher conf from same-code merged scores
        if (
            first_match
            and best_match
            and _code(best_match) == _code(first_match)
            and merged.confidence > best_match.confidence
            and _code(merged) == _code(first_match)
        ):
            best_match = merged

    assert best_match is not None
    # Final safety: never silently abandon first-pass without margin
    if first_match and _code(best_match) != _code(first_match):
        if best_match.confidence < first_match.confidence + ESCALATE_IDENTITY_MARGIN:
            best_match = first_match
            if escalated and "escalate_kept_first" not in (best_match.flags or []):
                pass  # flag added below

    segment.season = best_match.season
    segment.episode = best_match.episode
    segment.title = best_match.title or ""
    segment.confidence = best_match.confidence
    segment.error = best_match.error
    segment.dialogue_lines = (
        list(all_lines[:40]) if all_lines else list(best_match.dialogue_lines or [])
    )
    segment.sample_quality = best_quality or best_match.sample_quality
    flags = list(best_match.flags)
    if escalated:
        if "escalated_sample" not in flags:
            flags.append("escalated_sample")
        if first_match and _code(best_match) == _code(first_match):
            if "escalate_kept_first" not in flags:
                flags.append("escalate_kept_first")
        if (
            best_match.season is not None
            and best_match.confidence >= escalate_below
            and "escalate_improved" not in flags
        ):
            flags.append("escalate_improved")
    segment.flags = flags
    return segment


def reassign_segments_unique(
    segments: list[SplitSegment],
    episodes: list[Episode],
    *,
    covered: dict[tuple[int, int], str] | None = None,
    season_locked: bool = False,
    order_boost: float = 14.0,
    low_threshold: float = 55.0,
    auto_threshold: float = 70.0,
) -> list[SplitSegment]:
    """Unique SxxExx assignment across segments of one multi-ep file.

    When season-locked (e.g. S7 disc), soft-prior segment order → episode order
    so mid-arc Bad Batch eps (E02 vs E03) do not both claim E03.
    """
    if not segments or not episodes:
        return segments

    covered = covered or {}
    blocked = set(covered.keys())

    # Score each segment against catalog
    score_matrix: list[list[float]] = []
    for seg in segments:
        if seg.skip or seg.error in {"no_english_subtitles", "no_subtitle_tracks"}:
            score_matrix.append([0.0] * len(episodes))
            continue
        text = " ".join(seg.dialogue_lines or [])
        if not text.strip():
            score_matrix.append([0.0] * len(episodes))
            continue
        scores = score_all_episodes(
            text,
            episodes,
            lines=list(seg.dialogue_lines),
            sample_quality=seg.sample_quality or 70.0,
        )
        score_matrix.append(list(scores))

    # Order prior among episodes that already look plausible for *this* mega
    # (avoids S7_D2 being forced onto E01–E03 just because they are first in season)
    ep_order = sorted(
        range(len(episodes)),
        key=lambda j: (episodes[j].season, episodes[j].episode),
    )
    plausible: set[int] = set()
    for i, row in enumerate(score_matrix):
        if segments[i].skip:
            continue
        ranked_j = sorted(range(len(row)), key=lambda j: row[j], reverse=True)
        for j in ranked_j[:6]:
            if row[j] >= max(25.0, low_threshold * 0.4):
                key = (episodes[j].season, episodes[j].episode)
                if key not in blocked:
                    plausible.add(j)
    free_order = [j for j in ep_order if j in plausible]
    # If too few plausible, fall back to all free season eps
    if len(free_order) < len([s for s in segments if not s.skip]):
        free_order = [
            j
            for j in ep_order
            if (episodes[j].season, episodes[j].episode) not in blocked
        ]

    active_idx = [i for i, s in enumerate(segments) if not s.skip]
    if free_order and order_boost > 0:
        for rank, si in enumerate(active_idx):
            if rank < len(free_order):
                j = free_order[rank]
                score_matrix[si][j] = float(score_matrix[si][j]) + order_boost
                if rank > 0:
                    score_matrix[si][free_order[rank - 1]] += order_boost * 0.25
                if rank + 1 < len(free_order):
                    score_matrix[si][free_order[rank + 1]] += order_boost * 0.25

    # Greedy unique assignment
    pairs: list[tuple[float, int, int]] = []
    for i, row in enumerate(score_matrix):
        if segments[i].skip:
            continue
        for j, sc in enumerate(row):
            key = (episodes[j].season, episodes[j].episode)
            if key in blocked:
                continue
            if sc >= max(20.0, low_threshold * 0.35):
                pairs.append((sc, i, j))
    pairs.sort(reverse=True, key=lambda x: x[0])

    used_seg: set[int] = set()
    used_ep: set[int] = set()
    assignment: dict[int, tuple[int, float]] = {}
    for sc, i, j in pairs:
        if i in used_seg or j in used_ep:
            continue
        used_seg.add(i)
        used_ep.add(j)
        assignment[i] = (j, sc)

    for i, seg in enumerate(segments):
        if i not in assignment:
            continue
        j, sc = assignment[i]
        ep = episodes[j]
        prev = (seg.season, seg.episode)
        seg.season = ep.season
        seg.episode = ep.episode
        seg.title = ep.title
        seg.confidence = round(float(sc), 1)
        seg.error = None
        flags = [f for f in seg.flags if f not in {"duplicate_global", "duplicate_claim", "no_match"}]
        if "assigned_unique_segment" not in flags:
            flags.append("assigned_unique_segment")
        if prev != (ep.season, ep.episode) and "order_reassigned" not in flags:
            if season_locked:
                flags.append("order_reassigned")
        if sc >= auto_threshold:
            flags = [f for f in flags if f not in {"review", "low_confidence"}]
        elif sc >= low_threshold:
            if "review" not in flags:
                flags.append("review")
        else:
            if "low_confidence" not in flags:
                flags.append("low_confidence")
        seg.flags = flags

    return segments


def apply_covered_filter(
    segments: list[SplitSegment],
    covered: dict[tuple[int, int], str],
    *,
    skip_if_covered: bool = True,
) -> list[SplitSegment]:
    """Mark segments whose SxxExx is already present (do not extract)."""
    if not skip_if_covered:
        return segments
    for seg in segments:
        if seg.season is None or seg.episode is None:
            continue
        key = (seg.season, seg.episode)
        if key in covered:
            seg.skip = True
            seg.skip_reason = "already_present"
            seg.covered_by = covered[key]
            seg.flags = list(seg.flags) + ["skip_already_present"]
    return segments


def scan_output_library_for_episodes(output_root: Path) -> dict[tuple[int, int], str]:
    """Find SxxExx already on disk under output library."""
    found: dict[tuple[int, int], str] = {}
    if not output_root or not Path(output_root).exists():
        return found
    pat = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")
    for p in Path(output_root).rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts"}:
            continue
        m = pat.search(p.name)
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)))
        found.setdefault(key, str(p))
    return found


def split_via_mkvmerge_chapters(
    source: Path,
    out_dir: Path,
    *,
    pattern: str = "part-%03d.mkv",
) -> list[Path]:
    """Split mega using MKVToolNix 'before chapters' mode (chapters:all).

    Same as GUI: Split mode → Before chapters → all.
    Returns output paths sorted in chapter order. Original is never modified.
    """
    mkvmerge = which("mkvmerge")
    if not mkvmerge:
        raise RuntimeError("mkvmerge not found (install mkvtoolnix)")
    source = Path(source)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Clear previous parts in this work dir
    for old in out_dir.glob("part-*.mkv"):
        try:
            old.unlink()
        except OSError:
            pass
    out_template = str(out_dir / pattern)
    cmd = [
        mkvmerge,
        "-o",
        out_template,
        "--split",
        "chapters:all",
        str(source),
    ]
    proc = run_cmd(cmd, timeout=7200)
    # mkvmerge may return 1 for warnings
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            (proc.stderr or proc.stdout or "mkvmerge chapter split failed").strip()[:500]
        )
    parts = sorted(out_dir.glob("part-*.mkv"))
    # Some versions use -001 suffix differently
    if not parts:
        parts = sorted(
            p
            for p in out_dir.iterdir()
            if p.is_file() and p.suffix.lower() == ".mkv" and p.name != source.name
        )
    parts = [p for p in parts if p.stat().st_size > 1000]
    if not parts:
        raise RuntimeError("mkvmerge produced no chapter parts")
    return parts


def row_uses_mkv_chapters(row: object) -> bool:
    """Whether this plan row was inventoried from episode-length MKV chapters."""
    track = str(getattr(row, "track_info", "") or "")
    flags = getattr(row, "flags", None) or []
    if "mkv_chapters" in track or any("mkv_chapters" in str(f) for f in flags):
        return True
    method = track.split()[0] if track else ""
    return method == "mkv_chapters"


def split_file_segment(
    source: Path,
    start: float,
    end: float,
    dest: Path,
    *,
    stream_copy: bool = True,
) -> None:
    """Extract [start, end) from source into dest via ffmpeg."""
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"Target exists: {dest}")

    duration = max(0.1, end - start)
    # -ss after -i is more accurate; for copy, -ss before is faster — use before with copy
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-map",
        "0",
        "-avoid_negative_ts",
        "make_zero",
    ]
    if stream_copy:
        cmd += ["-c", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-c:a", "aac", "-c:s", "copy"]
    cmd.append(str(dest))
    proc = run_cmd(cmd, timeout=3600)
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size < 1000:
        # retry with re-encode if copy failed
        if stream_copy:
            if dest.exists():
                dest.unlink(missing_ok=True)
            split_file_segment(source, start, end, dest, stream_copy=False)
            return
        raise RuntimeError(proc.stderr.strip() or "ffmpeg split failed")


def median_runtime_minutes(episodes: list[Episode], default: float = 22.0) -> float:
    rts = sorted(e.runtime for e in episodes if e.runtime and e.runtime > 5)
    if not rts:
        return default
    return float(rts[len(rts) // 2])
