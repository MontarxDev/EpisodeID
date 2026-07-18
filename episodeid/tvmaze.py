"""Free TVMaze metadata enrichment (no API key)."""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path
from typing import Any

import requests

from episodeid.config import cache_dir
from episodeid.models import Episode

TVMAZE_API = "https://api.tvmaze.com"
_TAG_RE = re.compile(r"<[^>]+>")


def tvmaze_cache_dir() -> Path:
    return cache_dir() / "tvmaze"


def _strip_html(text: str) -> str:
    text = unescape(text or "")
    return _TAG_RE.sub("", text).strip()


def search_show(name: str, session: requests.Session | None = None) -> dict[str, Any] | None:
    session = session or requests.Session()
    try:
        resp = session.get(
            f"{TVMAZE_API}/singlesearch/shows",
            params={"q": name},
            timeout=30,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def fetch_episodes(show_id: int, session: requests.Session | None = None) -> list[dict[str, Any]]:
    session = session or requests.Session()
    cache = tvmaze_cache_dir() / f"{show_id}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    resp = session.get(f"{TVMAZE_API}/shows/{show_id}/episodes", timeout=45)
    resp.raise_for_status()
    data = resp.json()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def enrich_episodes_with_tvmaze(
    episodes: list[Episode],
    series_name: str,
    *,
    session: requests.Session | None = None,
) -> list[Episode]:
    """Merge TVMaze summaries into Episode.extra_overview when season/episode match."""
    session = session or requests.Session()
    show = search_show(series_name, session=session)
    if not show or not show.get("id"):
        # try without subtitle after colon
        base = series_name.split(":")[0].strip()
        if base != series_name:
            show = search_show(base, session=session)
    if not show or not show.get("id"):
        return episodes

    try:
        maze_eps = fetch_episodes(int(show["id"]), session=session)
    except (requests.RequestException, ValueError, TypeError):
        return episodes

    by_code: dict[tuple[int, int], dict[str, Any]] = {}
    for me in maze_eps:
        try:
            s, n = int(me.get("season") or 0), int(me.get("number") or 0)
        except (TypeError, ValueError):
            continue
        if s > 0 and n > 0:
            by_code[(s, n)] = me

    for ep in episodes:
        me = by_code.get((ep.season, ep.episode))
        if not me:
            continue
        summary = _strip_html(me.get("summary") or "")
        if summary:
            # Prefer keeping TMDB overview; append TVMaze if different
            if summary.casefold() not in (ep.overview or "").casefold():
                if ep.extra_overview:
                    if summary.casefold() not in ep.extra_overview.casefold():
                        ep.extra_overview = f"{ep.extra_overview} {summary}".strip()
                else:
                    ep.extra_overview = summary
        runtime = me.get("runtime")
        if runtime and not ep.runtime:
            try:
                ep.runtime = int(runtime)
            except (TypeError, ValueError):
                pass
        # Prefer TVMaze title only if TMDB title empty
        name = (me.get("name") or "").strip()
        if name and (not ep.title or ep.title.startswith("Episode ")):
            ep.title = name
    return episodes
