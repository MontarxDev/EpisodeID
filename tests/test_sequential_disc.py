"""Sequential season-disc assignment (no E15→E20 jumps, no junk rank-0)."""

from pathlib import Path

from episodeid.matcher import (
    filter_sequential_track_indices,
    pick_primary_free_block,
    reassign_sequential_disc,
)
from episodeid.models import Episode, MatchResult


def _eps(start: int, end: int) -> list[Episode]:
    return [Episode(1, e, f"Title {e}") for e in range(start, end + 1)]


def test_sequential_flat_scores_assigns_contiguous_block():
    episodes = _eps(11, 22)
    paths = [Path(f"D{i}_t0{i}.mkv") for i in range(1, 7)]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    matrix = [[50.0] * len(episodes) for _ in paths]
    out = reassign_sequential_disc(
        results, episodes, paths, matrix, blocked=set(), order_penalty=20.0
    )
    assert [r.episode for r in out] == list(range(11, 17))


def test_order_penalty_blocks_jump_to_e20():
    episodes = _eps(11, 22)
    paths = [Path(f"D{i}_t0{i}.mkv") for i in range(1, 7)]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    matrix = [[40.0] * len(episodes) for _ in paths]
    for i in range(5):
        matrix[i][i] = 95.0
    matrix[5][5] = 45.0  # E16
    matrix[5][9] = 71.0  # E20
    out = reassign_sequential_disc(results, episodes, paths, matrix, order_penalty=20.0)
    assert out[5].episode == 16


def test_blocked_early_eps_start_free_block_later():
    episodes = _eps(1, 22)
    paths = [Path(f"D{i}_t0{i}.mkv") for i in range(1, 7)]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    matrix = [[50.0] * len(episodes) for _ in paths]
    blocked = {(1, e) for e in range(1, 11)}
    out = reassign_sequential_disc(
        results, episodes, paths, matrix, blocked=blocked, order_penalty=20.0
    )
    assert [r.episode for r in out] == list(range(11, 17))


def test_junk_d_track_does_not_shift_e_block():
    """S4_D1 style: D1_t08 among E1–E6 must not steal free[0]=E01."""
    episodes = _eps(1, 12)
    paths = [
        Path("D1_t08.mkv"),
        Path("E1_t01.mkv"),
        Path("E2_t02.mkv"),
        Path("E3_t03.mkv"),
        Path("E4_t04.mkv"),
        Path("E5_t05.mkv"),
        Path("E6_t06.mkv"),
    ]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    # D1 weak everywhere; E tracks prefer sequential
    matrix = [[10.0] * len(episodes) for _ in paths]
    for i, p in enumerate(paths):
        if p.name.startswith("E"):
            # E1 → index 0 = E01, etc. file index among E is i-1
            matrix[i][i - 1] = 80.0
    out = reassign_sequential_disc(results, episodes, paths, matrix, order_penalty=20.0)
    # E1–E6 → E01–E06
    e_files = [r for r in out if r.path.name.startswith("E")]
    assert [r.episode for r in e_files] == list(range(1, 7))


def test_gappy_free_prefers_long_block_not_orphan():
    """Free [E05] + [E16–E20], 5 tracks → use E16–E20, not E05 then E16…"""
    episodes = _eps(1, 20)
    # free: E05, E16-E20
    blocked = {(1, e) for e in list(range(1, 5)) + list(range(6, 16))}
    free_j = [
        j
        for j, ep in enumerate(episodes)
        if (ep.season, ep.episode) not in blocked
    ]
    block = pick_primary_free_block(free_j, episodes, n_tracks=5)
    eps = [episodes[j].episode for j in block]
    assert eps == [16, 17, 18, 19, 20], eps

    paths = [Path(f"C{i}_t0{i}.mkv") for i in range(1, 6)]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    matrix = [[50.0] * len(episodes) for _ in paths]
    out = reassign_sequential_disc(
        results, episodes, paths, matrix, blocked=blocked, order_penalty=20.0
    )
    assert [r.episode for r in out] == [16, 17, 18, 19, 20]


def test_filter_majority_letter():
    paths = [
        Path("D1_t08.mkv"),
        Path("E1_t01.mkv"),
        Path("E2_t02.mkv"),
        Path("E3_t03.mkv"),
    ]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    ordered = list(range(4))
    kept = filter_sequential_track_indices(paths, results, ordered)
    names = [paths[i].name for i in kept]
    assert "D1_t08.mkv" not in names
    assert names == ["E1_t01.mkv", "E2_t02.mkv", "E3_t03.mkv"]


def test_blended_conf_raises_weak_sequential():
    episodes = _eps(1, 6)
    paths = [Path(f"D{i}_t0{i}.mkv") for i in range(1, 4)]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    matrix = [[30.0] * len(episodes) for _ in paths]
    for i in range(3):
        matrix[i][i] = 50.0  # exact sequential weak dialogue
    out = reassign_sequential_disc(results, episodes, paths, matrix)
    # blended 0.50*50 + 0.50*90 = 70
    assert out[0].episode == 1
    assert out[0].confidence >= 65
    assert "sequential_prior" in out[0].flags


def test_strict_no_skip_e05_in_seven_track_block():
    """S5_D1 regression: 7 tracks must get E01–E07, never skip E05."""
    episodes = _eps(1, 20)
    paths = [Path(f"C{i}_t0{i}.mkv") for i in range(1, 8)]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    # Bias scores so E06 looks better than E05 for track 5 — strict layout must ignore
    matrix = [[40.0] * len(episodes) for _ in paths]
    for i in range(7):
        matrix[i][i] = 70.0  # E01..E07 diagonal
    matrix[4][4] = 45.0  # E05 weak
    matrix[4][5] = 95.0  # E06 strong for C5
    out = reassign_sequential_disc(results, episodes, paths, matrix, order_penalty=20.0)
    assert [r.episode for r in out] == list(range(1, 8))
