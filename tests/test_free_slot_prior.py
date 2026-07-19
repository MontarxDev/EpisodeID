"""Free-slot disc order prior and global reassignment."""

from pathlib import Path

from episodeid.models import Episode, RenamePlanRow
from episodeid.renamer import (
    apply_disc_order_prior,
    clamp_confidence,
    reassign_demoted_to_free_slots,
    source_folder_label,
)


def test_disc_order_prior_skips_blocked_early_episodes():
    # S1_D4 style: E01–E15 covered → free starts at E16
    paths = [Path(f"D{i}_t0{i}.mkv") for i in range(1, 7)]
    episodes = [Episode(1, e, f"Ep {e}") for e in range(1, 23)]
    matrix = [[50.0] * len(episodes) for _ in paths]
    blocked = {(1, e) for e in range(1, 16)}
    apply_disc_order_prior(paths, matrix, episodes, boost=10.0, blocked=blocked)
    # First file should prefer E16 (index 15), not E01 (index 0)
    assert matrix[0][15] > matrix[0][0]
    assert matrix[0][15] >= 60.0
    # Second file → E17
    assert matrix[1][16] > matrix[1][1]


def test_clamp_confidence():
    assert clamp_confidence(105.0) == 100.0
    assert clamp_confidence(-1) == 0.0
    assert clamp_confidence(88.2) == 88.2


def test_source_folder_label_nested_season():
    p = Path("/lib/STAR_WARS_CLONE_WARS_S1_D1/Season 01/Ambush.mkv")
    assert "S1_D1" in source_folder_label(p) or "Season" in source_folder_label(p)
    assert "Season" in source_folder_label(p)


def test_source_folder_label_disc():
    p = Path("/lib/STAR_WARS_CLONE_WARS_S1_D4/D1_t01.mkv")
    assert source_folder_label(p) == "STAR_WARS_CLONE_WARS_S1_D4"


def test_reassign_demoted_fills_free_slot():
    catalog = [Episode(1, e, f"Title {e}", overview=f"unique plot words for episode {e} alpha") for e in range(1, 5)]
    # Winner already holds E01
    winner = RenamePlanRow(
        path=Path("/a/named.mkv"),
        original_name="named.mkv",
        season=1,
        episode=1,
        confidence=95,
        selected=True,
        flags=["trusted_filename", "already_named"],
        official_title="Title 1",
    )
    # Loser claimed E01, demoted — dialogue points at E02
    loser = RenamePlanRow(
        path=Path("/disc/S1_D4/D1_t01.mkv"),
        original_name="D1_t01.mkv",
        season=1,
        episode=1,
        confidence=79,
        selected=False,
        flags=["duplicate_global", "assigned_unique"],
        dialogue_lines=[
            "unique plot words for episode 2 alpha",
            "unique plot words for episode 2 alpha again",
            "more dialogue about episode 2 alpha",
        ],
        sample_quality=90,
        official_title="Title 1",
    )
    rows = reassign_demoted_to_free_slots(
        [winner, loser],
        catalog,
        low_threshold=55.0,
        auto_threshold=70.0,
    )
    # Loser should leave E01 and take a free code (prefer E02 if scores work)
    assert rows[0].episode == 1 and rows[0].selected
    assert rows[1].episode != 1 or "reassigned_global" in rows[1].flags
    if rows[1].season == 1 and rows[1].episode is not None:
        assert (rows[1].season, rows[1].episode) != (1, 1) or not rows[1].selected
