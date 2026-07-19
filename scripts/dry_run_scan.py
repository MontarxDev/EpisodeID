#!/usr/bin/env python3
"""Identify-only dry-run (never renames). Useful for season / disc debugging.

Examples:
  # Season 5 only against full library (disc-by-disc, filtered discs):
  uv run python scripts/dry_run_scan.py \\
      --folder "$HOME/Videos/Video Files" --season 5

  # Explicit S5-only tree:
  uv run python scripts/dry_run_scan.py --folder /tmp/episodeid-s5-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EpisodeID identify-only dry-run")
    parser.add_argument("--folder", type=Path, required=True, help="Scan root folder")
    parser.add_argument("--season", type=int, default=0, help="Season filter (0=all)")
    parser.add_argument("--series-id", type=int, default=0, help="TMDB series id (0=from settings)")
    parser.add_argument("--series-name", type=str, default="", help="Series name override")
    args = parser.parse_args(argv)

    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        print(f"ERROR: not a directory: {folder}", file=sys.stderr)
        return 2

    from episodeid.config import KEY_TMDB, get_secret, load_settings
    from episodeid.models import SeriesInfo
    from episodeid.pipeline import scan_and_identify
    from episodeid.session_log import SessionLog

    settings = load_settings()
    settings.season_filter = args.season if args.season > 0 else None
    settings.disc_by_disc_scan = True
    settings.sequential_disc_assign = True
    settings.auto_season_from_folder = True
    # Identify only — never apply renames from this script

    series_id = args.series_id or settings.last_series_id or 0
    series_name = args.series_name or settings.last_series_name or "Unknown"
    if not series_id:
        print("ERROR: provide --series-id or set last_series_id in settings", file=sys.stderr)
        return 2

    api_key = get_secret(KEY_TMDB)
    if not api_key:
        print("ERROR: TMDB API key not found in keyring", file=sys.stderr)
        return 2

    series = SeriesInfo(id=int(series_id), name=series_name)
    slog = SessionLog("dryrun")
    print(f"Session: {slog.id}")
    print(f"Folder:  {folder}")
    print(f"Series:  {series.name} ({series.id})")
    print(f"Season:  {settings.season_filter or 'all'}")
    print("Mode:    identify only (no renames)")

    def progress(ev) -> None:
        msg = getattr(ev, "message", str(ev))
        phase = getattr(ev, "phase", "")
        if phase in {"scan", "plan", "done", "metadata"} or "Sequential" in msg or "Disc " in msg:
            print(f"  [{phase}] {msg}", flush=True)

    plan = scan_and_identify(
        folder=folder,
        series=series,
        settings=settings,
        api_key=api_key,
        progress=progress,
        session_log=slog,
    )

    print("\n=== Assignments ===")
    rows = [
        r
        for r in plan
        if getattr(r, "row_kind", "rename") != "inventory_skip"
        and "likely_extra" not in (r.flags or [])
    ]
    rows.sort(key=lambda r: (str(r.path.parent.name), str(r.path.name)))
    for r in rows:
        code = (
            f"S{int(r.season):02d}E{int(r.episode):02d}"
            if r.season is not None and r.episode is not None
            else "—"
        )
        flags = ",".join(r.flags or [])
        print(
            f"{r.path.parent.name}/{r.path.name} -> {code} "
            f"conf={r.confidence:.1f} sel={r.selected} [{flags}]"
        )

    # Season layout checks when --season set
    if args.season > 0:
        print(f"\n=== Season {args.season:02d} layout check ===")
        s_rows = [
            r
            for r in rows
            if r.season == args.season and r.episode is not None
        ]
        by_disc: dict[str, list] = {}
        for r in s_rows:
            by_disc.setdefault(r.path.parent.name, []).append(r)
        ok = True
        for disc, drows in sorted(by_disc.items()):
            drows = sorted(drows, key=lambda r: r.path.name)
            eps = [int(r.episode) for r in drows]
            print(f"  {disc}: {eps}")
            for a, b in zip(eps, eps[1:]):
                if b < a:
                    print(f"    FAIL order: {a} then {b}")
                    ok = False
            # E05 must not appear on a disc whose name ends with D3 when earlier discs exist
            if "D3" in disc.upper() or disc.upper().endswith("_3"):
                if 5 in eps and args.season == 5:
                    print(f"    FAIL: E05 on disc 3 ({disc})")
                    ok = False
        # C5 on D1 should be E05 for Clone Wars S5
        for r in s_rows:
            if "S5_D1" in str(r.path) and r.path.name.startswith("C5"):
                if int(r.episode) != 5:
                    print(f"  FAIL: S5_D1/C5 should be E05, got E{int(r.episode):02d}")
                    ok = False
                else:
                    print("  OK: S5_D1/C5 → E05")
        print("RESULT:", "PASS" if ok else "FAIL")
        print(f"Session dir: {slog.dir}")
        return 0 if ok else 1

    print(f"\nSession dir: {slog.dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
