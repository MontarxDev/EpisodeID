from pathlib import Path

from episodeid.models import MatchResult
from episodeid.renamer import (
    apply_renames,
    build_plan_row,
    resolve_library_root,
    resolve_target_dir,
)


def test_resolve_library_with_series_subfolder(tmp_path: Path):
    lib = resolve_library_root(
        scan_root=tmp_path / "discs",
        output_root=tmp_path / "TV",
        series_name="Star Wars: The Clone Wars",
        create_series_subfolder=True,
    )
    assert lib == tmp_path / "TV" / "Star Wars The Clone Wars"


def test_target_season_under_output(tmp_path: Path):
    t = resolve_target_dir(
        season=1,
        scan_root=tmp_path / "in",
        output_root=tmp_path / "out",
        series_name="Show",
        move_to_season=True,
        create_series_subfolder=True,
    )
    assert t == tmp_path / "out" / "Show" / "Season 01"


def test_build_plan_uses_output_root(tmp_path: Path):
    src = tmp_path / "discs" / "D1.mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    result = MatchResult(
        path=src,
        season=1,
        episode=1,
        title="Ambush",
        confidence=90,
        low_confidence=False,
    )
    out = tmp_path / "Library"
    row = build_plan_row(
        result,
        series_name="Clone Wars",
        scan_root=tmp_path / "discs",
        move_to_season=True,
        output_root=out,
        create_series_subfolder=True,
    )
    assert row.target_dir == out / "Clone Wars" / "Season 01"
    assert "S01E01" in row.proposed_name

    ok, err = apply_renames([row], undo_dir=tmp_path / "undo")
    assert not err
    assert len(ok) == 1
    dest = out / "Clone Wars" / "Season 01" / row.proposed_name
    assert dest.exists()
    assert not src.exists()
