"""Centralized cache paths, stats, clear, and migration to durable storage."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from episodeid import __app_id__


def xdg_cache_root() -> Path:
    return Path.home() / ".cache" / __app_id__


def durable_cache_root() -> Path:
    return Path.home() / ".local" / "share" / __app_id__ / "cache"


def cache_root(durable: bool = True) -> Path:
    """Prefer durable location so disk cleaners that wipe ~/.cache don't nuke refs."""
    return durable_cache_root() if durable else xdg_cache_root()


def tmdb_dir(durable: bool = True) -> Path:
    return cache_root(durable) / "tmdb"


def tvmaze_dir(durable: bool = True) -> Path:
    return cache_root(durable) / "tvmaze"


def refsubs_dir(durable: bool = True) -> Path:
    return cache_root(durable) / "refsubs"


def migrate_cache_to_durable() -> dict[str, int]:
    """Copy missing files from ~/.cache/episodeid → durable share path."""
    src = xdg_cache_root()
    dst = durable_cache_root()
    moved = {"files": 0, "dirs": 0}
    if not src.exists():
        return moved
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(item, target)
            moved["files"] += 1
        except OSError:
            continue
    return moved


def ensure_cache_layout(durable: bool = True) -> Path:
    root = cache_root(durable)
    for sub in ("tmdb", "tvmaze", "refsubs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    if durable:
        migrate_cache_to_durable()
    return root


@dataclass
class CacheStats:
    root: Path
    tmdb_files: int
    tvmaze_files: int
    refsubs_files: int
    total_bytes: int

    def human_size(self) -> str:
        n = float(self.total_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
            n /= 1024
        return f"{n:.1f} GB"


def _dir_stats(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    count = 0
    size = 0
    for f in path.rglob("*"):
        if f.is_file():
            count += 1
            try:
                size += f.stat().st_size
            except OSError:
                pass
    return count, size


def get_cache_stats(durable: bool = True) -> CacheStats:
    root = cache_root(durable)
    # Also count legacy xdg if durable (show combined truth for user)
    tmdb_n, tmdb_b = _dir_stats(tmdb_dir(durable))
    tv_n, tv_b = _dir_stats(tvmaze_dir(durable))
    ref_n, ref_b = _dir_stats(refsubs_dir(durable))
    if durable:
        lt, lb = _dir_stats(xdg_cache_root() / "tmdb")
        vt, vb = _dir_stats(xdg_cache_root() / "tvmaze")
        rt, rb = _dir_stats(xdg_cache_root() / "refsubs")
        # Prefer not double-counting same relative paths after migration — approximate sum is fine
        tmdb_n = max(tmdb_n, lt)
        tv_n = max(tv_n, vt)
        ref_n = max(ref_n, rt)
        total = tmdb_b + tv_b + ref_b + lb + vb + rb
    else:
        total = tmdb_b + tv_b + ref_b
    return CacheStats(
        root=root,
        tmdb_files=tmdb_n,
        tvmaze_files=tv_n,
        refsubs_files=ref_n,
        total_bytes=total,
    )


def clear_dir(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


def clear_tmdb(durable: bool = True) -> int:
    n = clear_dir(tmdb_dir(durable))
    if durable:
        n += clear_dir(xdg_cache_root() / "tmdb")
    return n


def clear_tvmaze(durable: bool = True) -> int:
    n = clear_dir(tvmaze_dir(durable))
    if durable:
        n += clear_dir(xdg_cache_root() / "tvmaze")
    return n


def clear_refsubs(durable: bool = True, series_id: int | None = None) -> int:
    if series_id is not None:
        n = clear_dir(refsubs_dir(durable) / str(series_id))
        if durable:
            n += clear_dir(xdg_cache_root() / "refsubs" / str(series_id))
        return n
    n = clear_dir(refsubs_dir(durable))
    if durable:
        n += clear_dir(xdg_cache_root() / "refsubs")
    return n


def clear_all(durable: bool = True) -> int:
    return clear_tmdb(durable) + clear_tvmaze(durable) + clear_refsubs(durable)
