"""Reference subtitle download + sampling for high-accuracy free matching.

Primary provider: Wyzie Subs (free API key from https://store.wyzie.io/redeem).
Results are cached under the durable/app cache root (survives reboot).
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
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
    if text.startswith("\ufeff"):
        text = text[1:]
    lines: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        parts = [p for p in block.strip().splitlines() if p.strip()]
        if not parts:
            continue
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
                names.sort(key=lambda n: (0 if n.lower().endswith(".srt") else 1, n))
                return zf.read(names[0])
        except zipfile.BadZipFile:
            return None
    if b"-->" in content[:4000] or ctype.startswith("text"):
        return content
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


def load_cached_sample(tmdb_id: int, season: int, episode: int) -> str:
    cache_path = _episode_cache_path(tmdb_id, season, episode)
    if not cache_path.exists():
        return ""
    try:
        return json.loads(cache_path.read_text(encoding="utf-8")).get("sample") or ""
    except (OSError, json.JSONDecodeError):
        return ""


def get_reference_dialogue_sample(
    tmdb_id: int,
    season: int,
    episode: int,
    *,
    api_key: str | None,
    max_lines: int = 80,
    session: requests.Session | None = None,
    force_refresh: bool = False,
    allow_download: bool = True,
    save_to_cache: bool = True,
) -> tuple[str, str]:
    """Return (sample_text, source) where source is cache|download|none."""
    if not force_refresh:
        cached = load_cached_sample(tmdb_id, season, episode)
        if cached:
            return cached, "cache"

    if not allow_download or not api_key:
        return "", "none"

    session = session or requests.Session()
    try:
        results = search_wyzie(tmdb_id, season, episode, api_key=api_key, session=session)
    except Exception:
        return "", "none"

    best = _pick_best_result(results)
    if not best:
        return "", "none"

    url = best.get("url") or best.get("download") or best.get("link") or best.get("file")
    if not url:
        return "", "none"

    try:
        content, ctype = download_url(str(url), session=session)
        srt = _extract_srt_from_download(content, ctype)
        if not srt:
            return "", "none"
        lines = _sample_from_srt_bytes(srt, max_lines=max_lines)
        sample = join_dialogue(lines)
    except Exception:
        return "", "none"

    if save_to_cache and sample:
        cache_path = _episode_cache_path(tmdb_id, season, episode)
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
    return sample, "download"


@dataclass
class RefAttachStats:
    cached: int = 0
    downloaded: int = 0
    failed: int = 0
    attached: int = 0
    policy: str = "download-missing"

    def summary(self) -> str:
        return (
            f"Reference: {self.cached} cached · {self.downloaded} downloaded · "
            f"{self.failed} failed · policy={self.policy}"
        )


def attach_reference_subs(
    episodes: list[Episode],
    tmdb_id: int,
    *,
    api_key: str | None,
    max_episodes: int = 40,
    progress: Callable[[str], None] | None = None,
    session: requests.Session | None = None,
    policy: str = "download-missing",
    save_to_cache: bool = True,
) -> RefAttachStats:
    """Attach reference dialogue. policy: download-missing | cache-only | force-refresh."""
    stats = RefAttachStats(policy=policy)
    progress = progress or (lambda _m: None)
    session = session or requests.Session()
    force = policy == "force-refresh"
    allow_download = policy in {"download-missing", "force-refresh"} and bool(api_key)

    targets = episodes[:max_episodes]
    for i, ep in enumerate(targets, start=1):
        progress(f"Reference subtitles {i}/{len(targets)}: {ep.code}")
        sample, source = get_reference_dialogue_sample(
            tmdb_id,
            ep.season,
            ep.episode,
            api_key=api_key,
            session=session,
            force_refresh=force,
            allow_download=allow_download,
            save_to_cache=save_to_cache,
        )
        if sample:
            ep.ref_dialogue = sample
            stats.attached += 1
            if source == "cache":
                stats.cached += 1
            elif source == "download":
                stats.downloaded += 1
        else:
            # try pure cache load when download failed
            cached = load_cached_sample(tmdb_id, ep.season, ep.episode)
            if cached and not force:
                ep.ref_dialogue = cached
                stats.attached += 1
                stats.cached += 1
            else:
                stats.failed += 1
    return stats
