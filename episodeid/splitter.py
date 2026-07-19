"""Detect multi-episode files, inventory segments, and split with ffmpeg."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from episodeid.deps import which
from episodeid.extractor import probe_duration_seconds, run_cmd, sample_dialogue
from episodeid.matcher import match_dialogue
from episodeid.models import Episode, MatchResult
from episodeid.renamer import format_new_name, resolve_target_dir, sanitize_filename


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


def probe_chapters(path: Path) -> list[Chapter]:
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


def inventory_segments(
    path: Path,
    *,
    expected_runtime_min: float = 22.0,
) -> list[SplitSegment]:
    """Build un-identified segments for a multi-episode file.

    Prefers chapter clustering, but if chapters under-segment relative to
    duration/median runtime (common on S7 mega files), forces a duration grid.
    """
    path = Path(path)
    duration = probe_duration_seconds(path)
    if duration <= 0:
        return []
    expected_n = expected_segment_count(duration, expected_runtime_min)
    chapters = probe_chapters(path)
    method = "auto"
    if chapters:
        pairs = cluster_chapters_into_episodes(
            chapters,
            file_duration=duration,
            expected_runtime_min=expected_runtime_min,
        )
        method = "chapters"
        # Under-segmented vs duration estimate → force equal grid
        if expected_n >= 3 and len(pairs) < expected_n - 1:
            pairs = auto_grid_segments(
                duration, expected_runtime_min, force_n=expected_n
            )
            method = "auto_forced"
        # Over-fragmented (too many short pieces) also re-grid
        elif len(pairs) >= expected_n + 2 and expected_n >= 2:
            pairs = auto_grid_segments(
                duration, expected_runtime_min, force_n=expected_n
            )
            method = "auto_forced"
    else:
        pairs = auto_grid_segments(
            duration,
            expected_runtime_min,
            force_n=expected_n if expected_n >= 2 else None,
        )
        method = "auto"

    return [
        SplitSegment(source=path, start=s, end=e, method=method)
        for s, e in pairs
        if e - s >= 30.0
    ]


def identify_segment(
    segment: SplitSegment,
    episodes: list[Episode],
    *,
    low_threshold: float = 55.0,
    auto_threshold: float = 70.0,
) -> SplitSegment:
    """Sample dialogue inside segment window and match."""
    duration_min = max(0.5, (segment.end - segment.start) / 60.0)
    # Sample from ~15% into segment for a few minutes
    offset_min = segment.start / 60.0 + duration_min * 0.12
    scan_min = min(6.0, max(2.0, duration_min * 0.35))
    # Clamp inside segment
    seg_end_min = segment.end / 60.0
    if offset_min + scan_min > seg_end_min:
        scan_min = max(1.0, seg_end_min - offset_min - 0.05)

    sample = sample_dialogue(
        segment.source,
        offset_minutes=offset_min,
        scan_duration_minutes=scan_min,
        max_lines=35,
        prefer_english=True,
        adaptive=False,
    )
    segment.dialogue_lines = list(sample.lines)
    segment.sample_quality = sample.quality
    if sample.is_empty() or sample.quality < 30:
        segment.error = sample.error or "poor_ocr"
        segment.confidence = 0.0
        return segment

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
    segment.season = match.season
    segment.episode = match.episode
    segment.title = match.title or ""
    segment.confidence = match.confidence
    segment.error = match.error
    segment.flags = list(match.flags)
    return segment


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
