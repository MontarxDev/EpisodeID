from pathlib import Path

from episodeid.models import MatchResult
from episodeid.renamer import (
    apply_renames,
    build_plan_row,
    format_new_name,
    is_already_named,
    sanitize_filename,
    undo_last,
)


def test_sanitize_removes_colon():
    assert ":" not in sanitize_filename("Star Wars: The Clone Wars")


def test_format_new_name():
    name = format_new_name(
        series="Star Wars: The Clone Wars",
        season=1,
        episode=1,
        title="Ambush",
        ext=".mkv",
    )
    assert name.startswith("Star Wars The Clone Wars - S01E01 - Ambush")
    assert name.endswith(".mkv")


def test_already_named():
    assert is_already_named("Show - S01E02 - Title.mkv")
    assert not is_already_named("D1_t01.mkv")


def test_build_plan_and_apply(tmp_path: Path):
    src = tmp_path / "D1_t01.mkv"
    src.write_bytes(b"fake")
    result = MatchResult(
        path=src,
        season=1,
        episode=1,
        title="Ambush",
        confidence=88,
        low_confidence=False,
    )
    row = build_plan_row(
        result,
        series_name="Star Wars The Clone Wars",
        scan_root=tmp_path,
        move_to_season=True,
    )
    assert row.selected
    assert "S01E01" in row.proposed_name
    assert row.target_dir == tmp_path / "Season 01"

    undo = tmp_path / "undo"
    ok, err = apply_renames([row], undo_dir=undo)
    assert not err
    assert len(ok) == 1
    dest = tmp_path / "Season 01" / row.proposed_name
    assert dest.exists()
    assert not src.exists()

    restored, uerr = undo_last(undo)
    assert not uerr
    assert src.exists() or any(Path(r["to"]).exists() for r in restored)
