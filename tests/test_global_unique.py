"""Tests for global unique assignment, collisions, inventory collapse, disc prior."""

from pathlib import Path

from episodeid.models import Episode, RenamePlanRow
from episodeid.renamer import (
    apply_disc_order_prior,
    apply_global_unique_assignment,
    collapse_inventory_skips,
    detect_output_collisions,
    finalize_plan_rows,
    natural_sort_key,
    plan_summary_counts,
)
from episodeid.splitter import auto_grid_segments, expected_segment_count


def _row(
    path: str,
    season: int | None,
    episode: int | None,
    conf: float,
    *,
    selected: bool = True,
    kind: str = "rename",
    flags: list[str] | None = None,
    proposed: str | None = None,
    target: str | None = None,
) -> RenamePlanRow:
    p = Path(path)
    code = f"S{season:02d}E{episode:02d}" if season and episode else "x"
    return RenamePlanRow(
        path=p,
        original_name=p.name,
        season=season,
        episode=episode,
        official_title=f"Ep {episode}",
        confidence=conf,
        proposed_name=proposed or f"Show - {code} - Title.mkv",
        target_dir=Path(target or f"/out/Season {season:02d}" if season else "/out"),
        selected=selected,
        flags=list(flags or []),
        row_kind=kind,
    )


def test_global_unique_keeps_highest_confidence():
    rows = [
        _row("/disc1/D1.mkv", 1, 4, 72),
        _row("/disc2/D2.mkv", 1, 4, 91),
        _row("/disc1/D3.mkv", 1, 5, 80),
    ]
    apply_global_unique_assignment(rows)
    selected = [r for r in rows if r.selected]
    codes = [r.code for r in selected]
    assert codes.count("S01E04") == 1
    assert any(r.path.name == "D2.mkv" and r.selected for r in rows)
    assert any("duplicate_global" in r.flags for r in rows if r.path.name == "D1.mkv")
    assert not any(r.path.name == "D1.mkv" and r.selected for r in rows)


def test_global_unique_prefers_trusted_filename():
    rows = [
        _row("/a/D1.mkv", 1, 1, 95, flags=[]),
        _row(
            "/b/Show - S01E01 - Ambush.mkv",
            1,
            1,
            90,
            selected=False,
            flags=["trusted_filename", "already_named"],
        ),
    ]
    apply_global_unique_assignment(rows)
    # Trusted owns the code; rip duplicate is demoted; neither needs apply
    assert not rows[0].selected
    assert "duplicate_global" in rows[0].flags
    assert not rows[1].selected
    assert "duplicate_global" not in rows[1].flags


def test_output_collision_deselects_lower():
    rows = [
        _row("/a/D1.mkv", 1, 2, 88, proposed="Same.mkv", target="/lib/Season 01"),
        _row("/b/D2.mkv", 1, 2, 70, proposed="Same.mkv", target="/lib/Season 01"),
    ]
    # Different codes so global unique won't touch; force same dest
    rows[1].season = 1
    rows[1].episode = 3
    rows[1].proposed_name = "Same.mkv"
    detect_output_collisions(rows)
    assert sum(1 for r in rows if r.selected) == 1
    assert rows[0].selected
    assert "output_collision" in rows[1].flags


def test_collapse_inventory_skips_to_one_parent():
    mega = Path("/disc/C1_t00.mkv")
    rows = [
        RenamePlanRow(
            path=mega,
            original_name=f"C1_t00.mkv [{i}–{i+20}m]",
            row_kind="inventory_skip",
            skip_reason="disc_has_enough_singles",
            flags=["inventory_skip", "skip_disc_complete"],
            selected=False,
        )
        for i in range(5)
    ]
    rows.append(_row("/disc/D1.mkv", 1, 1, 90))
    out = collapse_inventory_skips(rows)
    skips = [r for r in out if r.row_kind == "inventory_skip"]
    assert len(skips) == 1
    assert "5 segment" in skips[0].official_title
    assert "collapsed_mega" in skips[0].flags
    assert sum(1 for r in out if r.row_kind == "rename") == 1


def test_finalize_plan_end_to_end():
    rows = [
        _row("/d1/A.mkv", 1, 1, 90),
        _row("/d2/B.mkv", 1, 1, 75),
        RenamePlanRow(
            path=Path("/d1/C1.mkv"),
            original_name="C1.mkv [0–20m]",
            row_kind="inventory_skip",
            skip_reason="disc_has_enough_singles",
            flags=["inventory_skip"],
        ),
        RenamePlanRow(
            path=Path("/d1/C1.mkv"),
            original_name="C1.mkv [20–40m]",
            row_kind="inventory_skip",
            skip_reason="disc_has_enough_singles",
            flags=["inventory_skip"],
        ),
    ]
    out = finalize_plan_rows(rows)
    assert sum(1 for r in out if r.selected and r.code == "S01E01") == 1
    assert sum(1 for r in out if r.row_kind == "inventory_skip") == 1
    counts = plan_summary_counts(out)
    assert counts["rename"] == 1
    assert counts["inventory_skip"] == 1


def test_status_label_for_skip_and_extra():
    skip = RenamePlanRow(
        path=Path("x.mkv"),
        original_name="x.mkv",
        row_kind="inventory_skip",
        flags=["inventory_skip"],
    )
    assert skip.status_label() == "SKIP"
    extra = RenamePlanRow(
        path=Path("e.mkv"),
        original_name="e.mkv",
        error="no_english_subtitles",
    )
    assert extra.status_label() == "EXTRA"
    split = RenamePlanRow(
        path=Path("m.mkv"),
        original_name="m.mkv [0–20m]",
        season=7,
        episode=1,
        confidence=93,
        selected=True,
        row_kind="split",
    )
    assert split.status_label() == "SPLIT"


def test_natural_sort_and_disc_order_prior():
    assert natural_sort_key("D2_t02.mkv") < natural_sort_key("D10_t10.mkv")
    paths = [Path(f"D{i}_t0{i}.mkv") for i in (1, 2, 3)]
    episodes = [
        Episode(1, 1, "A"),
        Episode(1, 2, "B"),
        Episode(1, 3, "C"),
    ]
    # Flat scores — prior should lift sequential matches
    matrix = [[50.0, 50.0, 50.0] for _ in paths]
    apply_disc_order_prior(paths, matrix, episodes, boost=10.0)
    assert matrix[0][0] > matrix[0][2]
    assert matrix[2][2] > matrix[2][0]


def test_expected_segment_count_s7_style():
    # ~88 min mega with 22 min episodes → 4 segments
    assert expected_segment_count(88 * 60, 22) == 4
    segs = auto_grid_segments(88 * 60, 22, force_n=4)
    assert len(segs) == 4
