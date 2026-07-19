"""Subtitle extraction and dialogue sampling from video files (MKV-first)."""

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
from episodeid.textutil import join_dialogue, sample_quality, unique_quality_lines

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".wmv"}
TEXT_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "srt", "text"}
IMAGE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvdsub", "pgssub", "xsub", "dvb_subtitle"}
ENGLISH_LANGS = {"eng", "en", "english"}


@dataclass
class SubtitleTrack:
    index: int
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


def probe_duration_seconds(path: Path) -> float:
    ffprobe = which("ffprobe")
    if not ffprobe:
        return 0.0
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    proc = run_cmd(cmd, timeout=60)
    if proc.returncode != 0:
        return 0.0
    try:
        data = json.loads(proc.stdout or "{}")
        return float((data.get("format") or {}).get("duration") or 0.0)
    except (ValueError, TypeError, json.JSONDecodeError):
        return 0.0


def adaptive_sample_window(
    duration_sec: float,
    *,
    user_offset_min: float | None = None,
    user_duration_min: float | None = None,
) -> tuple[float, float]:
    """Return (offset_minutes, scan_duration_minutes) adapted to file length."""
    if duration_sec <= 0:
        off = user_offset_min if user_offset_min is not None else 1.0
        dur = user_duration_min if user_duration_min is not None else 8.0
        return max(0.0, off), max(1.0, dur)

    d_min = duration_sec / 60.0

    if d_min < 8:
        off = 0.05 * d_min
        scan = min(d_min * 0.8, d_min - off)
    elif d_min < 16:
        off = min(0.5, 0.08 * d_min)
        scan = min(d_min * 0.65, d_min - off)
    elif d_min < 40:
        off = min(1.5, 0.06 * d_min)
        scan = min(8.0, max(3.0, d_min * 0.35))
    elif d_min < 90:
        off = min(2.0, 0.05 * d_min)
        scan = min(10.0, d_min * 0.25)
    else:
        # Mega / multi-episode: only sample early chapter-ish region
        off = 1.0
        scan = 6.0

    # User overrides are soft caps, still clamp into file
    if user_offset_min is not None:
        off = min(user_offset_min, max(0.0, d_min * 0.25))
    if user_duration_min is not None:
        scan = min(user_duration_min, max(1.0, d_min - off - 0.1))

    if off + scan > d_min:
        scan = max(0.5, d_min - off - 0.05)
    if off >= d_min * 0.4:
        off = max(0.0, d_min * 0.05)
        scan = min(scan, d_min - off)
    return round(off, 3), round(max(0.5, scan), 3)


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
    if lang in ENGLISH_LANGS:
        return 0
    if lang in {"", "und", "unknown"}:
        return 1
    return 2


def is_english_track(track: SubtitleTrack) -> bool:
    return track.language in ENGLISH_LANGS


def pick_subtitle_track(
    tracks: list[SubtitleTrack],
    *,
    prefer_english: bool = True,
) -> SubtitleTrack | None:
    if not tracks:
        return None
    text = [t for t in tracks if t.is_text]
    image = [t for t in tracks if t.is_image]
    text.sort(key=lambda t: (_lang_rank(t.language), t.index))
    image.sort(key=lambda t: (_lang_rank(t.language), t.index))

    if prefer_english:
        eng_text = [t for t in text if is_english_track(t)]
        eng_image = [t for t in image if is_english_track(t)]
        if eng_text:
            return eng_text[0]
        if eng_image:
            return eng_image[0]
        # und text/image before foreign
        und_text = [t for t in text if _lang_rank(t.language) == 1]
        und_image = [t for t in image if _lang_rank(t.language) == 1]
        if und_text:
            return und_text[0]
        if und_image:
            return und_image[0]
        return None  # foreign-only: caller should error

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
        return unique_quality_lines(lines, max_lines=max_lines)

    subs = pysrt.open(str(path), encoding="utf-8", error_handling="pysrt.ERROR_REPLACE")
    return unique_quality_lines([s.text for s in subs], max_lines=max_lines)


def _subtitle_stream_map_index(tracks: list[SubtitleTrack], track: SubtitleTrack) -> int:
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
    cmd = [
        ffmpeg, "-y", "-i", str(path),
        "-map", f"0:s:{s_index}", "-f", "srt", str(out),
    ]
    proc = run_cmd(cmd, timeout=180)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out2 = work_dir / "sample.ass"
        cmd2 = [ffmpeg, "-y", "-i", str(path), "-map", f"0:s:{s_index}", str(out2)]
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
        return unique_quality_lines(dialogue_lines, max_lines=max_lines)

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
        return unique_quality_lines(selected, max_lines=max_lines)
    except Exception:
        return _parse_srt_file(out, max_lines)


def _preprocess_for_ocr(src: Path, dest: Path) -> Path:
    """Crop bottom subtitle band and boost contrast for OCR."""
    try:
        from PIL import Image, ImageOps, ImageFilter

        img = Image.open(src).convert("RGB")
        w, h = img.size
        # Bottom 40% where DVD/BD subs usually sit
        top = int(h * 0.58)
        band = img.crop((0, top, w, h))
        # Upscale for OCR
        band = band.resize((band.width * 2, band.height * 2), Image.Resampling.LANCZOS)
        gray = ImageOps.grayscale(band)
        # Autocontrast + slight sharpen
        gray = ImageOps.autocontrast(gray, cutoff=2)
        gray = gray.filter(ImageFilter.SHARPEN)
        # Light threshold to suppress background
        gray = gray.point(lambda p: 255 if p > 160 else (0 if p < 90 else p))
        gray.save(dest)
        return dest
    except Exception:
        return src


def _ocr_image(path: Path) -> tuple[str, float]:
    """Return (text, avg_confidence 0-1)."""
    tess = which("tesseract")
    if tess:
        proc = run_cmd(
            [tess, str(path), "stdout", "-l", "eng", "--psm", "6"],
            timeout=60,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return proc.stdout or "", 0.7
    if has_rapidocr():
        from rapidocr_onnxruntime import RapidOCR

        if not hasattr(_ocr_image, "_engine"):
            _ocr_image._engine = RapidOCR()  # type: ignore[attr-defined]
        engine = _ocr_image._engine  # type: ignore[attr-defined]
        result, _ = engine(str(path))
        if not result:
            return "", 0.0
        texts = []
        confs = []
        for item in result:
            if not item or len(item) < 2:
                continue
            texts.append(str(item[1]))
            try:
                confs.append(float(item[2]))
            except (TypeError, ValueError, IndexError):
                confs.append(0.5)
        avg = sum(confs) / len(confs) if confs else 0.0
        # RapidOCR conf is often 0–1
        if avg > 1.0:
            avg = avg / 100.0
        return " ".join(texts), avg
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
    max_frames: int = 28,
) -> list[str]:
    """OCR image subs by burning them onto a black canvas (clean glyphs)."""
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    s_index = _subtitle_stream_map_index(tracks, track)
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    prep_dir = work_dir / "prep"
    prep_dir.mkdir(parents=True, exist_ok=True)

    duration = max(scan_duration_minutes * 60.0, 20.0)
    # Sample frequently enough to catch short subtitle events
    fps = max(min(max_frames / max(duration, 1.0), 0.5), 1.0 / 6.0)
    out_pattern = str(frames_dir / "f_%03d.png")
    ss = max(offset_minutes, 0) * 60.0

    # Preferred: black background + subtitle overlay only (no busy video pixels)
    # Size matches common DVD VobSub canvas; scale works if different.
    cmd_black = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=720x480:d={duration:.2f}",
        "-ss", str(ss),
        "-t", str(duration),
        "-i", str(path),
        "-filter_complex",
        f"[0:v][1:s:{s_index}]overlay=shortest=1,fps={fps:.5f}",
        "-frames:v", str(max_frames),
        out_pattern,
    ]
    proc = run_cmd(cmd_black, timeout=600)
    frames = sorted(frames_dir.glob("f_*.png"))

    if not frames:
        # Fallback: burn onto real video, crop bottom band
        for f in frames_dir.glob("f_*.png"):
            f.unlink(missing_ok=True)
        cmd_video = [
            ffmpeg, "-y",
            "-ss", str(ss),
            "-t", str(duration),
            "-i", str(path),
            "-filter_complex",
            f"[0:v][0:s:{s_index}]overlay=shortest=1,fps={fps:.5f},"
            f"crop=iw:ih*0.40:0:ih*0.60",
            "-frames:v", str(max_frames),
            out_pattern,
        ]
        proc = run_cmd(cmd_video, timeout=600)
        frames = sorted(frames_dir.glob("f_*.png"))

    if not frames:
        raise RuntimeError(proc.stderr.strip() or "No subtitle frames extracted for OCR")

    raw_lines: list[str] = []
    for i, frame in enumerate(frames):
        # Light preprocess: upscale + autocontrast only (no hard threshold)
        prepped = prep_dir / f"p_{i:03d}.png"
        try:
            from PIL import Image, ImageOps, ImageFilter

            img = Image.open(frame).convert("L")
            # Keep lower 45% if full frame
            if img.height >= 200:
                top = int(img.height * 0.55)
                img = img.crop((0, top, img.width, img.height))
            img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
            img = ImageOps.autocontrast(img, cutoff=1)
            img = img.filter(ImageFilter.SHARPEN)
            img.save(prepped)
            img_path = prepped
        except Exception:
            img_path = frame

        text, conf = _ocr_image(img_path)
        if (not text.strip() or conf < 0.3) and img_path != frame:
            text2, conf2 = _ocr_image(frame)
            if len(text2.strip()) > len(text.strip()):
                text, conf = text2, conf2
        if not text.strip():
            continue
        for part in re.split(r"[\n\r]+", text):
            part = part.strip()
            if part:
                raw_lines.append(part)

    return unique_quality_lines(raw_lines, max_lines=max_lines, min_quality=0.32)


def sample_dialogue(
    path: Path,
    *,
    offset_minutes: float | None = None,
    scan_duration_minutes: float | None = None,
    max_lines: int = 40,
    work_dir: Path | None = None,
    prefer_english: bool = True,
    adaptive: bool = True,
) -> DialogueSample:
    path = Path(path)
    if not path.exists():
        return DialogueSample(
            source="none", raw_text="", lines=[], track_info="missing file", quality=0.0
        )

    duration_sec = probe_duration_seconds(path)
    if adaptive:
        off, scan = adaptive_sample_window(
            duration_sec,
            user_offset_min=offset_minutes,
            user_duration_min=scan_duration_minutes,
        )
    else:
        off = offset_minutes if offset_minutes is not None else 1.0
        scan = scan_duration_minutes if scan_duration_minutes is not None else 8.0

    # Mega files: flag but still try a short sample
    mega = duration_sec > 90 * 60

    external = find_external_subtitle(path)
    if external:
        lines = _parse_srt_file(external, max_lines)
        q = sample_quality(lines)
        return DialogueSample(
            lines=lines,
            source="external_srt",
            raw_text=join_dialogue(lines),
            track_info=str(external.name),
            quality=q,
            duration_sec=duration_sec,
        )

    tracks = probe_subtitle_tracks(path)
    track = pick_subtitle_track(tracks, prefer_english=prefer_english)
    if track is None:
        langs = ", ".join(sorted({t.language or "und" for t in tracks})) or "none"
        return DialogueSample(
            source="none",
            raw_text="",
            lines=[],
            track_info=f"no english subtitle track (found: {langs})",
            quality=0.0,
            duration_sec=duration_sec,
            error="no_english_subtitles" if tracks else "no_subtitle_tracks",
        )

    own_tmp = work_dir is None
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="episodeid_"))
    try:
        if track.is_text:
            lines = extract_text_subtitles(
                path, track, tracks,
                offset_minutes=off,
                scan_duration_minutes=scan,
                max_lines=max_lines,
                work_dir=work,
            )
            source = "embedded_text"
        else:
            lines = extract_image_subtitles_via_overlay(
                path, track, tracks,
                offset_minutes=off,
                scan_duration_minutes=scan,
                max_lines=max_lines,
                work_dir=work,
            )
            source = "ocr_vobsub" if "dvd" in track.codec else "ocr_pgs"
            if "pgs" in track.codec:
                source = "ocr_pgs"

            # Retry mid-episode if poor quality
            q = sample_quality(lines)
            if q < 40 and duration_sec > 600:
                mid_off = (duration_sec / 60.0) * 0.35
                mid_scan = min(6.0, (duration_sec / 60.0) * 0.2)
                lines2 = extract_image_subtitles_via_overlay(
                    path, track, tracks,
                    offset_minutes=mid_off,
                    scan_duration_minutes=mid_scan,
                    max_lines=max_lines,
                    work_dir=work / "retry",
                    max_frames=32,
                )
                if sample_quality(lines2) > q:
                    lines = lines2

        q = sample_quality(lines)
        err = None
        if q < 25 and lines:
            err = "poor_ocr"
        elif not lines:
            err = "no_dialogue_extracted"
        if mega:
            err = err or "long_title_possible_multi_episode"

        return DialogueSample(
            lines=lines,
            source=source,
            raw_text=join_dialogue(lines),
            track_info=f"#{track.index} {track.codec} {track.language} @ {off:.1f}+{scan:.1f}m",
            quality=q,
            duration_sec=duration_sec,
            error=err,
        )
    except Exception as exc:
        return DialogueSample(
            lines=[],
            source="none",
            raw_text="",
            track_info=f"error: {exc}",
            quality=0.0,
            duration_sec=duration_sec,
            error=str(exc),
        )
    finally:
        if own_tmp:
            shutil.rmtree(work, ignore_errors=True)


_SKIP_DIR_NAMES = {
    ".git", ".svn", ".hg", ".trash", "trash", "@eadir", "#recycle",
    "system volume information", "$recycle.bin", "__pycache__",
}
_SAMPLE_DIR_NAMES = {"sample", "samples", "preview", "previews"}


def list_video_files(
    folder: Path,
    *,
    recursive: bool = True,
    skip_sample_folders: bool = True,
    max_files: int = 2000,
) -> list[Path]:
    """Find video files in folder; optionally recurse into subfolders."""
    folder = Path(folder)
    if not folder.is_dir():
        return []

    files: list[Path] = []
    if not recursive:
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                files.append(p)
        return sorted(files, key=lambda p: str(p).lower())

    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        # Skip hidden / junk path components
        skip = False
        for part in path.relative_to(folder).parts[:-1]:
            low = part.lower()
            if part.startswith(".") or low in _SKIP_DIR_NAMES:
                skip = True
                break
            if skip_sample_folders and low in _SAMPLE_DIR_NAMES:
                skip = True
                break
        if skip:
            continue
        files.append(path)
        if len(files) >= max_files:
            break
    return sorted(files, key=lambda p: str(p).lower())


def season_hint_from_path(path: Path) -> int | None:
    """If path contains Season 01 / S01 / S1_D2 / DISC folder season, return number."""
    import re

    # Prefer more specific patterns first across all path parts
    text_parts = list(path.parts)
    # Also try full path string for S1_D1 style
    joined = " ".join(text_parts)

    patterns = [
        r"(?i)season[\s._-]*(\d{1,2})",
        r"(?i)(?:^|[^a-z0-9])s(?:eason)?[\s._-]*0*(\d{1,2})(?:[^0-9]|$)",
        r"(?i)_s(\d{1,2})(?:_|$|d|disc)",
        r"(?i)(?:^|[^0-9])s(\d{1,2})(?:_d|_disc|d\d|[^0-9]|$)",
    ]
    for part in reversed(text_parts):
        for pat in patterns:
            m = re.search(pat, part)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 40:
                    return n
        m = re.fullmatch(r"[Ss](\d{1,2})", part)
        if m:
            return int(m.group(1))
    for pat in patterns:
        m = re.search(pat, joined)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 40:
                return n
    return None


def discover_disc_folders(root: Path) -> list[Path]:
    """Immediate subdirs of root that contain video files (disc dump folders)."""
    root = Path(root)
    if not root.is_dir():
        return []
    discs: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        # has any video in tree?
        found = False
        for p in child.rglob("*"):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                found = True
                break
        if found:
            discs.append(child)
    return discs


def detect_multipart(name: str) -> int | None:
    """Return part number if filename looks like multi-part of one episode."""
    import re

    m = re.search(r"(?i)(?:^|[^a-z])(?:part|cd|disc|disk)[\s._-]*(\d{1,2})(?:[^0-9]|$)", name)
    if m:
        return int(m.group(1))
    m = re.search(r"(?i)[Ss]\d{1,2}[Ee]\d{1,2}[\s._-]*[Pp](?:art)?(\d{1,2})", name)
    if m:
        return int(m.group(1))
    return None


def filter_by_size(
    files: list[Path],
    *,
    enabled: bool = True,
    ratio: float = 0.25,
    max_ratio: float = 2.5,
) -> tuple[list[Path], list[Path]]:
    """Skip extras (small) and mega multi-title dumps (large) relative to median."""
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
    if not keep:
        return files, []
    return keep, skipped
