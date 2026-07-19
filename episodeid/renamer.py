"""Build rename plans, apply renames, undo, and export reports."""

from __future__ import annotations

import csv
import json
import re
import shutil
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


def resolve_library_root(
    *,
    scan_root: Path,
    output_root: Path | None = None,
    series_name: str = "",
    create_series_subfolder: bool = True,
    rename_in_place: bool = False,
) -> Path:
    """Root directory that will hold Season XX folders (or flat renames)."""
    if rename_in_place:
        return scan_root
    base = Path(output_root) if output_root else Path(scan_root)
    if create_series_subfolder and series_name.strip():
        return base / sanitize_filename(series_name)
    return base


def resolve_target_dir(
    *,
    season: int | None,
    scan_root: Path,
    output_root: Path | None = None,
    series_name: str = "",
    move_to_season: bool = True,
    create_series_subfolder: bool = True,
    rename_in_place: bool = False,
    source_path: Path | None = None,
) -> Path:
    if rename_in_place and source_path is not None:
        if move_to_season and season is not None:
            return source_path.parent  # rare; prefer library root modes
        return source_path.parent
    library = resolve_library_root(
        scan_root=scan_root,
        output_root=output_root,
        series_name=series_name,
        create_series_subfolder=create_series_subfolder,
        rename_in_place=False,
    )
    if move_to_season and season is not None:
        return library / season_dir_name(season)
    return library


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
    output_root: Path | None = None,
    create_series_subfolder: bool = True,
    rename_in_place: bool = False,
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
        dialogue_lines=list(result.dialogue_lines or []),
        sample_quality=result.sample_quality,
        track_info=result.track_info,
    )

    if result.error or result.season is None or result.episode is None:
        row.selected = False
        row.proposed_name = original
        return row

    if skip_already_named and is_already_named(original):
        # Keep identity for covered-set / library, but do not re-rename
        row.selected = False
        row.proposed_name = original
        if "already_named" not in row.flags:
            row.flags.append("already_named")
        if "trusted_filename" in result.flags and "trusted_filename" not in row.flags:
            row.flags.append("trusted_filename")
        # Ensure S/E preserved from match result
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

    if rename_in_place:
        row.target_dir = path.parent
    else:
        row.target_dir = resolve_target_dir(
            season=result.season,
            scan_root=scan_root,
            output_root=output_root,
            series_name=series_name,
            move_to_season=move_to_season,
            create_series_subfolder=create_series_subfolder,
            source_path=path,
        )

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
    """Apply selected rename rows (not splits). Returns (successes, failures)."""
    successes: list[dict] = []
    failures: list[dict] = []
    undo_entries: list[dict] = []

    for row in rows:
        if not row.selected:
            continue
        if getattr(row, "row_kind", "rename") == "split":
            continue  # handled by apply_splits
        if getattr(row, "row_kind", "rename") == "inventory_skip":
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
        try:
            if dest.exists() and dest.resolve() != src.resolve():
                failures.append({"path": str(src), "error": f"Target exists: {dest.name}"})
                continue
            if src.resolve() == dest.resolve():
                successes.append({"from": str(src), "to": str(dest), "note": "already in place"})
                continue
        except OSError:
            pass

        try:
            try:
                src.rename(dest)
            except OSError:
                shutil.move(str(src), str(dest))
        except OSError as exc:
            failures.append({"path": str(src), "error": str(exc)})
            continue

        entry = {"type": "rename", "from": str(src), "to": str(dest)}
        successes.append(entry)
        undo_entries.append(entry)
        row.path = dest
        row.original_name = dest.name

    if undo_dir is not None and undo_entries:
        _append_undo(undo_dir, undo_entries)

    return successes, failures


def apply_splits(
    rows: list[RenamePlanRow],
    *,
    undo_dir: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """Extract time ranges from multi-episode sources. Originals are never deleted."""
    from episodeid.splitter import split_file_segment

    successes: list[dict] = []
    failures: list[dict] = []
    undo_entries: list[dict] = []

    for row in rows:
        if not row.selected or getattr(row, "row_kind", "rename") != "split":
            continue
        if row.split_start is None or row.split_end is None:
            failures.append({"path": str(row.path), "error": "Missing split times"})
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
        dest = target_dir / row.proposed_name
        try:
            split_file_segment(src, float(row.split_start), float(row.split_end), dest)
        except Exception as exc:
            failures.append({"path": str(src), "error": str(exc)})
            continue
        entry = {
            "type": "split",
            "from": str(src),
            "to": str(dest),
            "start": row.split_start,
            "end": row.split_end,
        }
        successes.append(entry)
        undo_entries.append(entry)

    if undo_dir is not None and undo_entries:
        _append_undo(undo_dir, undo_entries)
    return successes, failures


def apply_all_selected(
    rows: list[RenamePlanRow],
    *,
    undo_dir: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """Apply renames and splits for selected rows."""
    ok1, err1 = apply_renames(rows, undo_dir=None)
    ok2, err2 = apply_splits(rows, undo_dir=None)
    successes = ok1 + ok2
    failures = err1 + err2
    if undo_dir is not None and successes:
        _append_undo(undo_dir, successes)
    return successes, failures


def _append_undo(undo_dir: Path, entries: list[dict]) -> None:
    undo_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    undo_path = undo_dir / f"{stamp}.json"
    # merge if same-second
    existing: list[dict] = []
    if undo_path.exists():
        try:
            existing = json.loads(undo_path.read_text(encoding="utf-8")).get("operations") or []
        except (OSError, json.JSONDecodeError):
            existing = []
    undo_path.write_text(
        json.dumps({"created": stamp, "operations": existing + entries}, indent=2),
        encoding="utf-8",
    )


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
        op_type = op.get("type") or "rename"
        created = Path(op["to"])
        if op_type == "split":
            # Split: original mega kept; undo = delete created file only
            if not created.exists():
                failures.append({"path": str(created), "error": "Split output missing for undo"})
                continue
            try:
                created.unlink()
                successes.append({"type": "split_undo", "removed": str(created)})
            except OSError as exc:
                failures.append({"path": str(created), "error": str(exc)})
            continue

        src = created
        dest = Path(op["from"])
        if not src.exists():
            failures.append({"path": str(src), "error": "File missing for undo"})
            continue
        if dest.exists() and dest.resolve() != src.resolve():
            failures.append({"path": str(src), "error": f"Original path occupied: {dest}"})
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                src.rename(dest)
            except OSError:
                shutil.move(str(src), str(dest))
            successes.append({"from": str(src), "to": str(dest)})
        except OSError as exc:
            failures.append({"path": str(src), "error": str(exc)})
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
