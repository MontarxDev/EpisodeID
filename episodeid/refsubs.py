"""Reference subtitle download + sampling for high-accuracy free matching.

Primary provider: Wyzie Subs (free API key from https://store.wyzie.io/redeem).
Results are cached under ~/.cache/episodeid/refsubs/.
"""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import requests

from episodeid.config import cache_dir
from episodeid.models import Episode
from episodeid.textutil import join_dialogue, unique_quality_lines

WYZie_SEARCH = "https://sub.wyzie.io/search"


def refsubs_cache_dir() -> Path:
    return cache_dir() / "refsubs"


def _episode_cache_path(tmdb_id: int, season: int, episode: int) -> Path:
    return refsubs_cache_dir() / str(tmdb_id) / f"S{season:02d}E{episode:02d}.json"


def _sample_from_srt_bytes(data: bytes, max_lines: int = 80) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    # strip BOM
    if text.startswith("\ufeff"):
        text = text[1:]
    # crude SRT parse
    lines: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        parts = [p for p in block.strip().splitlines() if p.strip()]
        if not parts:
            continue
        # drop index + timestamp
        body = []
        for p in parts:
            if re.match(r"^\d+$", p.strip()):
                continue
            if "-->" in p:
                continue
            body.append(p)
        if body:
            lines.append(" ".join(body))
    return unique_quality_lines(lines, max_lines=max_lines, min_quality=0.30)


def _extract_srt_from_download(content: bytes, content_type: str = "") -> bytes | None:
    ctype = (content_type or "").lower()
    if "zip" in ctype or content[:2] == b"PK":
        try:
            with zipfile.ZipFile(BytesIO(content)) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith((".srt", ".vtt", ".ass"))]
                if not names:
                    return None
                # prefer .srt
                names.sort(key=lambda n: (0 if n.lower().endswith(".srt") else 1, n))
                return zf.read(names[0])
        except zipfile.BadZipFile:
            return None
    # plain text srt
    if b"-->" in content[:4000] or content_type.startswith("text"):
        return content
    # try as text anyway
    try:
        content.decode("utf-8")
        return content
    except UnicodeDecodeError:
        return None


def search_wyzie(
    tmdb_id: int,
    season: int,
    episode: int,
    *,
    api_key: str,
    language: str = "en",
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    session = session or requests.Session()
    params = {
        "id": str(tmdb_id),
        "season": str(season),
        "episode": str(episode),
        "language": language,
        "key": api_key,
    }
    resp = session.get(WYZie_SEARCH, params=params, timeout=45)
    if resp.status_code == 401:
        raise RuntimeError("Wyzie API key invalid or missing")
    if resp.status_code == 429:
        raise RuntimeError("Wyzie rate limit exceeded")
    if resp.status_code >= 400:
        raise RuntimeError(f"Wyzie search HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("subtitles", "results", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _pick_best_result(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not results:
        return None
    # Prefer English, srt, high downloads if present
    def score(item: dict[str, Any]) -> tuple:
        lang = str(item.get("language") or item.get("lang") or "").lower()
        fmt = str(item.get("format") or item.get("encoding") or item.get("url") or "").lower()
        eng = 0 if lang in {"en", "eng", "english"} else 1
        srt = 0 if "srt" in fmt or str(item.get("url") or "").endswith(".srt") else 1
        downloads = int(item.get("downloads") or item.get("downloadCount") or 0)
        return (eng, srt, -downloads)

    return sorted(results, key=score)[0]


def download_url(url: str, session: requests.Session | None = None) -> tuple[bytes, str]:
    session = session or requests.Session()
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "")


def get_reference_dialogue_sample(
    tmdb_id: int,
    season: int,
    episode: int,
    *,
    api_key: str | None,
    max_lines: int = 80,
    session: requests.Session | None = None,
    force_refresh: bool = False,
) -> str:
    """Return cached or freshly downloaded reference dialogue sample text."""
    cache_path = _episode_cache_path(tmdb_id, season, episode)
    if cache_path.exists() and not force_refresh:
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            sample = data.get("sample") or ""
            if sample:
                return sample
        except (OSError, json.JSONDecodeError):
            pass

    if not api_key:
        return ""

    session = session or requests.Session()
    try:
        results = search_wyzie(tmdb_id, season, episode, api_key=api_key, session=session)
    except Exception:
        return ""

    best = _pick_best_result(results)
    if not best:
        return ""

    url = best.get("url") or best.get("download") or best.get("link") or best.get("file")
    if not url:
        return ""

    try:
        content, ctype = download_url(str(url), session=session)
        srt = _extract_srt_from_download(content, ctype)
        if not srt:
            return ""
        lines = _sample_from_srt_bytes(srt, max_lines=max_lines)
        sample = join_dialogue(lines)
    except Exception:
        return ""

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "tmdb_id": tmdb_id,
                "season": season,
                "episode": episode,
                "source_url": str(url),
                "sample": sample,
                "line_count": len(lines),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return sample


def attach_reference_subs(
    episodes: list[Episode],
    tmdb_id: int,
    *,
    api_key: str | None,
    max_episodes: int = 40,
    progress: Callable[[str], None] | None = None,
    session: requests.Session | None = None,
) -> int:
    """Download/cache reference dialogue for episodes. Returns count with ref text."""
    if not api_key:
        # Still load any already-cached samples
        attached = 0
        for ep in episodes:
            sample = ""
            cache_path = _episode_cache_path(tmdb_id, ep.season, ep.episode)
            if cache_path.exists():
                try:
                    sample = json.loads(cache_path.read_text(encoding="utf-8")).get("sample") or ""
                except (OSError, json.JSONDecodeError):
                    sample = ""
            if sample:
                ep.ref_dialogue = sample
                attached += 1
        return attached

    session = session or requests.Session()
    progress = progress or (lambda _m: None)
    attached = 0
    # Prefer limiting when many episodes: process season-filtered list (caller should filter)
    targets = episodes[:max_episodes]
    for i, ep in enumerate(targets, start=1):
        progress(f"Reference subtitles {i}/{len(targets)}: {ep.code}")
        sample = get_reference_dialogue_sample(
            tmdb_id,
            ep.season,
            ep.episode,
            api_key=api_key,
            session=session,
        )
        if sample:
            ep.ref_dialogue = sample
            attached += 1
    return attached
