"""Build rename plans, apply renames, undo, and export reports."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from episodeid.models import MatchResult, RenamePlanRow

_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE_RE = re.compile(r"\s+")
_ALREADY_NAMED_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")

DEFAULT_FORMAT = "{series} - S{season:02d}E{episode:02d} - {title}{ext}"


def sanitize_filename(name: str, replacement: str = "") -> str:
    name = name.replace(":", " ")
    name = _ILLEGAL_RE.sub(replacement, name)
    name = _MULTI_SPACE_RE.sub(" ", name).strip(" .")
    return name or "untitled"


def is_already_named(filename: str) -> bool:
    return bool(_ALREADY_NAMED_RE.search(filename))


def format_new_name(
    *,
    series: str,
    season: int,
    episode: int,
    title: str,
    ext: str,
    fmt: str = DEFAULT_FORMAT,
) -> str:
    if not ext.startswith(".") and ext:
        ext = f".{ext}"
    series_s = sanitize_filename(series)
    title_s = sanitize_filename(title)
    try:
        name = fmt.format(
            series=series_s,
            season=season,
            episode=episode,
            title=title_s,
            ext=ext,
        )
    except (KeyError, ValueError):
        name = DEFAULT_FORMAT.format(
            series=series_s,
            season=season,
            episode=episode,
            title=title_s,
            ext=ext,
        )
    return sanitize_filename(name)


def season_dir_name(season: int) -> str:
    return f"Season {season:02d}"


def build_plan_row(
    result: MatchResult,
    *,
    series_name: str,
    scan_root: Path,
    move_to_season: bool = True,
    fmt: str = DEFAULT_FORMAT,
    low_threshold: float = 55.0,
    auto_threshold: float = 70.0,
    skip_already_named: bool = False,
) -> RenamePlanRow:
    path = result.path
    original = path.name
    row = RenamePlanRow(
        path=path,
        original_name=original,
        season=result.season,
        episode=result.episode,
        official_title=result.title or "",
        confidence=result.confidence,
        move_to_season=move_to_season,
        error=result.error,
        dialogue_source=result.dialogue_source,
        flags=list(result.flags),
        candidates=list(result.candidates),
        target_dir=path.parent,
    )

    if result.error or result.season is None or result.episode is None:
        row.selected = False
        row.proposed_name = original
        return row

    if skip_already_named and is_already_named(original):
        row.selected = False
        row.proposed_name = original
        row.flags.append("already_named")
        return row

    ext = path.suffix
    proposed = format_new_name(
        series=series_name,
        season=result.season,
        episode=result.episode,
        title=result.title or "Unknown",
        ext=ext,
        fmt=fmt,
    )
    row.proposed_name = proposed

    if move_to_season:
        row.target_dir = scan_root / season_dir_name(result.season)
    else:
        row.target_dir = path.parent

    if result.confidence >= auto_threshold and "duplicate_claim" not in result.flags:
        row.selected = True
    elif result.confidence >= low_threshold and "duplicate_claim" not in result.flags:
        row.selected = True  # medium: selected but flagged review
    else:
        row.selected = False

    return row


def build_plan(
    results: Iterable[MatchResult],
    **kwargs,
) -> list[RenamePlanRow]:
    return [build_plan_row(r, **kwargs) for r in results]


def apply_renames(
    rows: list[RenamePlanRow],
    *,
    undo_dir: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """Apply selected rows. Returns (successes, failures)."""
    successes: list[dict] = []
    failures: list[dict] = []
    undo_entries: list[dict] = []

    for row in rows:
        if not row.selected:
            continue
        if row.error or not row.proposed_name:
            failures.append({"path": str(row.path), "error": row.error or "No proposed name"})
            continue
        if row.season is None or row.episode is None:
            failures.append({"path": str(row.path), "error": "Missing season/episode"})
            continue

        src = row.path
        if not src.exists():
            failures.append({"path": str(src), "error": "Source file missing"})
            continue

        target_dir = row.target_dir or src.parent
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            failures.append({"path": str(src), "error": f"Cannot create directory: {exc}"})
            continue

        dest = target_dir / row.proposed_name
        if dest.exists() and dest.resolve() != src.resolve():
            failures.append({"path": str(src), "error": f"Target exists: {dest.name}"})
            continue

        try:
            src.rename(dest)
        except OSError as exc:
            failures.append({"path": str(src), "error": str(exc)})
            continue

        entry = {"from": str(src), "to": str(dest)}
        successes.append(entry)
        undo_entries.append(entry)
        row.path = dest
        row.original_name = dest.name

    if undo_dir is not None and undo_entries:
        undo_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        undo_path = undo_dir / f"{stamp}.json"
        undo_path.write_text(
            json.dumps({"created": stamp, "operations": undo_entries}, indent=2),
            encoding="utf-8",
        )

    return successes, failures


def undo_last(undo_dir: Path) -> tuple[list[dict], list[dict]]:
    if not undo_dir.exists():
        return [], [{"error": "No undo directory"}]
    logs = sorted(undo_dir.glob("*.json"), reverse=True)
    if not logs:
        return [], [{"error": "No undo logs found"}]
    data = json.loads(logs[0].read_text(encoding="utf-8"))
    ops = data.get("operations") or []
    successes: list[dict] = []
    failures: list[dict] = []
    for op in reversed(ops):
        src = Path(op["to"])
        dest = Path(op["from"])
        if not src.exists():
            failures.append({"path": str(src), "error": "File missing for undo"})
            continue
        if dest.exists() and dest.resolve() != src.resolve():
            failures.append({"path": str(src), "error": f"Original path occupied: {dest}"})
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
            successes.append({"from": str(src), "to": str(dest)})
        except OSError as exc:
            failures.append({"path": str(src), "error": str(exc)})
    # rename log so it is not re-used as latest
    logs[0].rename(logs[0].with_suffix(".json.undone"))
    return successes, failures


def export_csv(rows: list[RenamePlanRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "original_name",
                "season",
                "episode",
                "official_title",
                "confidence",
                "proposed_name",
                "selected",
                "error",
                "path",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "original_name": row.original_name,
                    "season": row.season,
                    "episode": row.episode,
                    "official_title": row.official_title,
                    "confidence": row.confidence,
                    "proposed_name": row.proposed_name,
                    "selected": row.selected,
                    "error": row.error or "",
                    "path": str(row.path),
                }
            )


def export_json(rows: list[RenamePlanRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([r.to_dict() for r in rows], indent=2),
        encoding="utf-8",
    )
