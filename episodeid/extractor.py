"""Subtitle extraction and dialogue sampling from video files."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from episodeid.deps import has_rapidocr, which
from episodeid.models import DialogueSample
from episodeid.textutil import join_dialogue, unique_lines

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".wmv"}
TEXT_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "srt", "text"}
IMAGE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvdsub", "pgssub", "xsub", "dvb_subtitle"}


@dataclass
class SubtitleTrack:
    index: int  # stream index in file
    codec: str
    language: str
    title: str
    is_text: bool
    is_image: bool


def run_cmd(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def probe_subtitle_tracks(path: Path) -> list[SubtitleTrack]:
    ffprobe = which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name:stream_tags=language,title",
        "-of",
        "json",
        str(path),
    ]
    proc = run_cmd(cmd, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    data = json.loads(proc.stdout or "{}")
    tracks: list[SubtitleTrack] = []
    for stream in data.get("streams") or []:
        codec = (stream.get("codec_name") or "").lower()
        tags = stream.get("tags") or {}
        lang = (tags.get("language") or tags.get("LANGUAGE") or "").lower()
        title = tags.get("title") or tags.get("TITLE") or ""
        is_text = codec in TEXT_CODECS or codec.endswith("srt")
        is_image = codec in IMAGE_CODECS or "pgs" in codec or "dvd" in codec or "vobsub" in codec
        if not is_text and not is_image:
            # unknown — try both later
            is_text = True
        tracks.append(
            SubtitleTrack(
                index=int(stream["index"]),
                codec=codec,
                language=lang,
                title=title,
                is_text=is_text and not is_image,
                is_image=is_image,
            )
        )
    return tracks


def _lang_rank(lang: str) -> int:
    lang = (lang or "").lower()
    if lang in {"eng", "en", "english"}:
        return 0
    if lang in {"", "und", "unknown"}:
        return 1
    return 2


def pick_subtitle_track(tracks: list[SubtitleTrack]) -> SubtitleTrack | None:
    if not tracks:
        return None
    # Prefer English text, then any text, then English image, then any image
    text = [t for t in tracks if t.is_text]
    image = [t for t in tracks if t.is_image]
    text.sort(key=lambda t: (_lang_rank(t.language), t.index))
    image.sort(key=lambda t: (_lang_rank(t.language), t.index))
    if text:
        return text[0]
    if image:
        return image[0]
    return tracks[0]


def find_external_subtitle(video: Path) -> Path | None:
    stem = video.with_suffix("")
    candidates = [
        video.with_suffix(".srt"),
        video.with_suffix(".ass"),
        video.with_suffix(".ssa"),
        video.with_suffix(".vtt"),
        Path(str(stem) + ".en.srt"),
        Path(str(stem) + ".eng.srt"),
    ]
    for c in candidates:
        if c.exists():
            return c
    # any stem*.srt
    for c in sorted(video.parent.glob(video.stem + "*.srt")):
        return c
    return None


def _parse_srt_file(path: Path, max_lines: int) -> list[str]:
    try:
        import pysrt
    except ImportError:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = []
        for block in re.split(r"\n\s*\n", text):
            parts = block.strip().splitlines()
            if len(parts) >= 3:
                lines.append(" ".join(parts[2:]))
            elif len(parts) == 2 and "-->" not in parts[0]:
                lines.append(parts[-1])
        return unique_lines(lines, max_lines=max_lines)

    subs = pysrt.open(str(path), encoding="utf-8", error_handling="pysrt.ERROR_REPLACE")
    return unique_lines([s.text for s in subs], max_lines=max_lines)


def _subtitle_stream_map_index(tracks: list[SubtitleTrack], track: SubtitleTrack) -> int:
    """Map absolute stream index to 0-based subtitle stream index for -map 0:s:N."""
    subs = sorted(tracks, key=lambda t: t.index)
    for i, t in enumerate(subs):
        if t.index == track.index:
            return i
    return 0


def extract_text_subtitles(
    path: Path,
    track: SubtitleTrack,
    tracks: list[SubtitleTrack],
    *,
    offset_minutes: float,
    scan_duration_minutes: float,
    max_lines: int,
    work_dir: Path,
) -> list[str]:
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    s_index = _subtitle_stream_map_index(tracks, track)
    out = work_dir / "sample.srt"
    # Extract full text track then filter by time via pysrt when possible
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(path),
        "-map",
        f"0:s:{s_index}",
        "-f",
        "srt",
        str(out),
    ]
    proc = run_cmd(cmd, timeout=180)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        # try codec copy / ass
        out2 = work_dir / "sample.ass"
        cmd2 = [
            ffmpeg,
            "-y",
            "-i",
            str(path),
            "-map",
            f"0:s:{s_index}",
            str(out2),
        ]
        proc2 = run_cmd(cmd2, timeout=180)
        if proc2.returncode != 0 or not out2.exists():
            raise RuntimeError(proc.stderr.strip() or "Failed to extract text subtitles")
        text = out2.read_text(encoding="utf-8", errors="replace")
        dialogue_lines = []
        for line in text.splitlines():
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) >= 10:
                    dialogue_lines.append(parts[9])
        return unique_lines(dialogue_lines, max_lines=max_lines)

    try:
        import pysrt

        subs = pysrt.open(str(out), encoding="utf-8", error_handling="pysrt.ERROR_REPLACE")
        start_ms = int(offset_minutes * 60 * 1000)
        end_ms = int((offset_minutes + scan_duration_minutes) * 60 * 1000)
        selected = []
        for s in subs:
            mid = (s.start.ordinal + s.end.ordinal) // 2
            if mid < start_ms:
                continue
            if mid > end_ms:
                break
            selected.append(s.text)
        if not selected:
            selected = [s.text for s in subs[: max_lines * 2]]
        return unique_lines(selected, max_lines=max_lines)
    except Exception:
        return _parse_srt_file(out, max_lines)


def _ocr_image(path: Path) -> str:
    # Prefer tesseract CLI
    tess = which("tesseract")
    if tess:
        proc = run_cmd([tess, str(path), "stdout", "-l", "eng", "--psm", "6"], timeout=60)
        if proc.returncode == 0:
            return proc.stdout or ""
    if has_rapidocr():
        from rapidocr_onnxruntime import RapidOCR

        if not hasattr(_ocr_image, "_engine"):
            _ocr_image._engine = RapidOCR()  # type: ignore[attr-defined]
        engine = _ocr_image._engine  # type: ignore[attr-defined]
        result, _ = engine(str(path))
        if not result:
            return ""
        return " ".join(item[1] for item in result if item and len(item) > 1)
    raise RuntimeError(
        "OCR required for image subtitles but neither tesseract nor rapidocr-onnxruntime is available"
    )


def extract_image_subtitles_via_overlay(
    path: Path,
    track: SubtitleTrack,
    tracks: list[SubtitleTrack],
    *,
    offset_minutes: float,
    scan_duration_minutes: float,
    max_lines: int,
    work_dir: Path,
    max_frames: int = 24,
) -> list[str]:
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    s_index = _subtitle_stream_map_index(tracks, track)
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    # Sample ~1 frame every few seconds with burned-in subtitles
    duration = max(scan_duration_minutes * 60.0, 30.0)
    fps = min(max_frames / duration, 0.5)
    fps = max(fps, 1.0 / 12.0)
    out_pattern = str(frames_dir / "f_%03d.png")
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        str(max(offset_minutes, 0) * 60.0),
        "-t",
        str(duration),
        "-i",
        str(path),
        "-filter_complex",
        f"[0:v][0:s:{s_index}]overlay=shortest=1,fps={fps:.5f}",
        "-frames:v",
        str(max_frames),
        out_pattern,
    ]
    proc = run_cmd(cmd, timeout=600)
    frames = sorted(frames_dir.glob("f_*.png"))
    if not frames:
        raise RuntimeError(
            proc.stderr.strip() or "No subtitle frames extracted for OCR"
        )
    lines: list[str] = []
    for frame in frames:
        text = _ocr_image(frame)
        if text.strip():
            # OCR may return multi-line
            for part in re.split(r"[\n\r]+", text):
                lines.append(part)
    return unique_lines(lines, max_lines=max_lines)


def sample_dialogue(
    path: Path,
    *,
    offset_minutes: float = 1.0,
    scan_duration_minutes: float = 10.0,
    max_lines: int = 40,
    work_dir: Path | None = None,
) -> DialogueSample:
    path = Path(path)
    if not path.exists():
        return DialogueSample(source="none", raw_text="", lines=[], track_info="missing file")

    external = find_external_subtitle(path)
    if external:
        lines = _parse_srt_file(external, max_lines)
        return DialogueSample(
            lines=lines,
            source="external_srt",
            raw_text=join_dialogue(lines),
            track_info=str(external.name),
        )

    tracks = probe_subtitle_tracks(path)
    track = pick_subtitle_track(tracks)
    if track is None:
        return DialogueSample(
            source="none",
            raw_text="",
            lines=[],
            track_info="no subtitle tracks",
        )

    own_tmp = work_dir is None
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="episodeid_"))
    try:
        if track.is_text:
            lines = extract_text_subtitles(
                path,
                track,
                tracks,
                offset_minutes=offset_minutes,
                scan_duration_minutes=scan_duration_minutes,
                max_lines=max_lines,
                work_dir=work,
            )
            source = "embedded_text"
        else:
            lines = extract_image_subtitles_via_overlay(
                path,
                track,
                tracks,
                offset_minutes=offset_minutes,
                scan_duration_minutes=scan_duration_minutes,
                max_lines=max_lines,
                work_dir=work,
            )
            source = "ocr_vobsub" if "dvd" in track.codec else "ocr_pgs"
            if "pgs" in track.codec:
                source = "ocr_pgs"
        return DialogueSample(
            lines=lines,
            source=source,
            raw_text=join_dialogue(lines),
            track_info=f"#{track.index} {track.codec} {track.language}",
        )
    except Exception as exc:
        return DialogueSample(
            lines=[],
            source="none",
            raw_text="",
            track_info=f"error: {exc}",
        )
    finally:
        if own_tmp:
            shutil.rmtree(work, ignore_errors=True)


def list_video_files(folder: Path) -> list[Path]:
    folder = Path(folder)
    files = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    return sorted(files, key=lambda p: p.name.lower())


def filter_by_size(
    files: list[Path],
    *,
    enabled: bool = True,
    ratio: float = 0.25,
    max_ratio: float = 2.5,
) -> tuple[list[Path], list[Path]]:
    """Return (keep, skipped).

    Skips files much smaller than the median (menus/extras) and optionally
    files much larger than the median (full-disc / multi-title dumps).
    """
    if not enabled or len(files) < 3:
        return files, []
    sizes = sorted(p.stat().st_size for p in files)
    median = sizes[len(sizes) // 2]
    if median <= 0:
        return files, []
    low = median * ratio
    high = median * max_ratio if max_ratio and max_ratio > 1 else None
    keep: list[Path] = []
    skipped: list[Path] = []
    for p in files:
        size = p.stat().st_size
        if size < low:
            skipped.append(p)
        elif high is not None and size > high:
            skipped.append(p)
        else:
            keep.append(p)
    # Always keep if filter would drop everything
    if not keep:
        return files, []
    return keep, skipped
