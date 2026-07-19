from pathlib import Path

from episodeid.extractor import discover_disc_folders, season_hint_from_path
from episodeid.models import RenamePlanRow


def test_season_hint_s1_d1():
    p = Path("/data/STAR_WARS_CLONE_WARS_S1_D1/C1_t00.mkv")
    assert season_hint_from_path(p) == 1
    assert season_hint_from_path(Path("/data/STAR_WARS_CLONE_WARS_S1_D1")) == 1


def test_season_hint_s7():
    assert season_hint_from_path(Path("Video Files/STAR_WARS_THE_CLONE_WARS_S7_D2/A1.mkv")) == 7


def test_season_hint_season_folder():
    assert season_hint_from_path(Path("/lib/Season 03/ep.mkv")) == 3


def test_rename_plan_row_code():
    r = RenamePlanRow(path=Path("x.mkv"), original_name="x.mkv", season=2, episode=5)
    assert r.code == "S02E05"


def test_discover_disc_folders(tmp_path: Path):
    d1 = tmp_path / "SHOW_S1_D1"
    d2 = tmp_path / "SHOW_S2_D1"
    empty = tmp_path / "empty"
    d1.mkdir()
    d2.mkdir()
    empty.mkdir()
    (d1 / "a.mkv").write_bytes(b"x")
    (d2 / "sub").mkdir()
    (d2 / "sub" / "b.mkv").write_bytes(b"x")
    discs = discover_disc_folders(tmp_path)
    names = {p.name for p in discs}
    assert names == {"SHOW_S1_D1", "SHOW_S2_D1"}
    assert empty.name not in names


def test_season_filter_keeps_matching_discs_only(tmp_path: Path):
    """Season filter must select S5 discs without leaving disc-by-disc mode."""
    for name in ("SHOW_S4_D1", "SHOW_S5_D1", "SHOW_S5_D2", "SHOW_S5_D3", "SHOW_S6_D1"):
        d = tmp_path / name
        d.mkdir()
        (d / "C1_t01.mkv").write_bytes(b"x")
    discs = discover_disc_folders(tmp_path)
    s5 = [d for d in discs if season_hint_from_path(d) == 5]
    assert [d.name for d in s5] == ["SHOW_S5_D1", "SHOW_S5_D2", "SHOW_S5_D3"]
    # Still multi-disc → disc-by-disc eligible
    assert len(s5) >= 2
