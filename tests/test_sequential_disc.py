"""Sequential season-disc assignment (no E15→E20 jumps)."""

from pathlib import Path

from episodeid.matcher import reassign_sequential_disc
from episodeid.models import Episode, MatchResult


def _eps(start: int, end: int) -> list[Episode]:
    return [Episode(1, e, f"Title {e}") for e in range(start, end + 1)]


def test_sequential_flat_scores_assigns_contiguous_block():
    # Free E11–E22, 6 files → E11–E16
    episodes = _eps(11, 22)
    paths = [Path(f"D{i}_t0{i}.mkv") for i in range(1, 7)]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    # Flat scores — only order should decide
    matrix = [[50.0] * len(episodes) for _ in paths]
    out = reassign_sequential_disc(
        results,
        episodes,
        paths,
        matrix,
        blocked=set(),
        order_penalty=20.0,
        low_threshold=55.0,
        auto_threshold=70.0,
    )
    codes = [(r.season, r.episode) for r in out]
    assert codes == [(1, e) for e in range(11, 17)]
    assert all("sequential_disc" in r.flags for r in out)


def test_order_penalty_blocks_jump_to_e20():
    # Simulate D6: E20 raw score much higher, but 4 steps away from sequential E16
    episodes = _eps(11, 22)
    paths = [Path(f"D{i}_t0{i}.mkv") for i in range(1, 7)]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    matrix = [[40.0] * len(episodes) for _ in paths]
    # File ranks 0–4 prefer E11–E15 strongly
    for i in range(5):
        matrix[i][i] = 95.0
    # File 5 (D6): E20 (index 9 in free E11=0..E20=9) scores 71, E16 (index 5) scores 45
    matrix[5][5] = 45.0  # E16
    matrix[5][9] = 71.0  # E20
    out = reassign_sequential_disc(
        results,
        episodes,
        paths,
        matrix,
        order_penalty=20.0,
    )
    assert out[5].episode == 16, f"expected E16, got E{out[5].episode}"
    assert out[4].episode == 15


def test_blocked_early_eps_start_free_block_later():
    # E01–E10 covered → free starts at E11
    episodes = _eps(1, 22)
    paths = [Path(f"D{i}_t0{i}.mkv") for i in range(1, 7)]
    results = [MatchResult(path=p, sample_quality=90) for p in paths]
    matrix = [[50.0] * len(episodes) for _ in paths]
    blocked = {(1, e) for e in range(1, 11)}
    out = reassign_sequential_disc(
        results, episodes, paths, matrix, blocked=blocked, order_penalty=20.0
    )
    assert [r.episode for r in out] == list(range(11, 17))
