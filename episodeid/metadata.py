"""TMDB metadata client with on-disk cache."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from episodeid.config import tmdb_cache_dir
from episodeid.models import Episode, SeriesInfo

TMDB_API = "https://api.themoviedb.org/3"


class TMDBError(Exception):
    pass


class TMDBClient:
    def __init__(
        self,
        api_key: str,
        *,
        cache_dir: Path | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key or not api_key.strip():
            raise TMDBError("TMDB API key is required")
        self.api_key = api_key.strip()
        self.cache_dir = cache_dir or tmdb_cache_dir()
        self.session = session or requests.Session()
        self.sleep = sleep

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        params["api_key"] = self.api_key
        url = f"{TMDB_API}{path}?{urlencode(params)}"
        try:
            resp = self.session.get(url, timeout=30)
        except requests.RequestException as exc:
            raise TMDBError(f"Network error contacting TMDB: {exc}") from exc

        if resp.status_code == 401:
            raise TMDBError("Invalid TMDB API key (HTTP 401)")
        if resp.status_code == 429:
            raise TMDBError("TMDB rate limit exceeded (HTTP 429). Try again later.")
        if resp.status_code >= 400:
            raise TMDBError(f"TMDB error HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise TMDBError("Invalid JSON from TMDB") from exc

    def search_series(self, query: str, page: int = 1) -> list[SeriesInfo]:
        data = self._get("/search/tv", {"query": query, "page": page})
        results = []
        for item in data.get("results") or []:
            year = None
            first = item.get("first_air_date") or ""
            if len(first) >= 4 and first[:4].isdigit():
                year = int(first[:4])
            results.append(
                SeriesInfo(
                    id=int(item["id"]),
                    name=item.get("name") or item.get("original_name") or "Unknown",
                    year=year,
                    overview=item.get("overview") or "",
                )
            )
        return results

    def get_series(self, series_id: int) -> SeriesInfo:
        data = self._get(f"/tv/{series_id}")
        year = None
        first = data.get("first_air_date") or ""
        if len(first) >= 4 and first[:4].isdigit():
            year = int(first[:4])
        return SeriesInfo(
            id=int(data["id"]),
            name=data.get("name") or "Unknown",
            year=year,
            overview=data.get("overview") or "",
        )

    def _episodes_cache_path(self, series_id: int) -> Path:
        return self.cache_dir / f"{series_id}.json"

    def get_all_episodes(
        self,
        series_id: int,
        *,
        force_refresh: bool = False,
    ) -> list[Episode]:
        cache_path = self._episodes_cache_path(series_id)
        if not force_refresh and cache_path.exists():
            try:
                raw = json.loads(cache_path.read_text(encoding="utf-8"))
                return [
                    Episode(
                        season=int(e["season"]),
                        episode=int(e["episode"]),
                        title=e.get("title") or "",
                        overview=e.get("overview") or "",
                    )
                    for e in raw.get("episodes") or []
                ]
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass

        series = self._get(f"/tv/{series_id}")
        episodes: list[Episode] = []
        seasons = series.get("seasons") or []
        for season_meta in seasons:
            season_num = int(season_meta.get("season_number") or 0)
            if season_num <= 0:
                continue  # skip specials by default
            data = self._get(f"/tv/{series_id}/season/{season_num}")
            for ep in data.get("episodes") or []:
                episodes.append(
                    Episode(
                        season=season_num,
                        episode=int(ep.get("episode_number") or 0),
                        title=(ep.get("name") or "").strip() or f"Episode {ep.get('episode_number')}",
                        overview=(ep.get("overview") or "").strip(),
                    )
                )
            self.sleep(0.05)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "series_id": series_id,
            "name": series.get("name"),
            "episodes": [
                {
                    "season": e.season,
                    "episode": e.episode,
                    "title": e.title,
                    "overview": e.overview,
                }
                for e in episodes
            ],
        }
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return episodes

    def test_connection(self) -> str:
        data = self._get("/configuration")
        if "images" in data:
            return "TMDB connection OK"
        return "TMDB responded but configuration looked unexpected"


def clear_tmdb_cache(cache: Path | None = None) -> int:
    cache = cache or tmdb_cache_dir()
    if not cache.exists():
        return 0
    count = 0
    for path in cache.glob("*.json"):
        path.unlink(missing_ok=True)
        count += 1
    return count
