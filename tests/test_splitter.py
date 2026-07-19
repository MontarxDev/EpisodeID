from pathlib import Path

from episodeid.models import Episode
from episodeid.splitter import (
    Chapter,
    SplitSegment,
    apply_covered_filter,
    auto_grid_segments,
    cluster_chapters_into_episodes,
    is_multi_episode_candidate,
)


def _c1_like_chapters() -> list[Chapter]:
    """Simplified Clone Wars C1 pattern: 5 episodes × (3 long + short + tiny)."""
    chapters: list[Chapter] = []
    t = 0.0
    idx = 0
    for ep in range(5):
        for long in (427, 479, 410):
            chapters.append(Chapter(idx, t, t + long, f"L{ep}"))
            t += long
            idx += 1
        chapters.append(Chapter(idx, t, t + 40, "credits"))
        t += 40
        idx += 1
        chapters.append(Chapter(idx, t, t + 0.1, "mark"))
        t += 0.1
        idx += 1
    return chapters


def test_is_multi_episode_candidate():
    assert is_multi_episode_candidate(116 * 60, median_runtime_min=22)
    assert not is_multi_episode_candidate(22 * 60, median_runtime_min=22)


def test_cluster_c1_like_into_five():
    chs = _c1_like_chapters()
    file_dur = chs[-1].end
    segs = cluster_chapters_into_episodes(
        chs, file_duration=file_dur, expected_runtime_min=22
    )
    assert len(segs) == 5
    for s, e in segs:
        length = e - s
        assert 15 * 60 < length < 30 * 60


def test_auto_grid_roughly_counts():
    segs = auto_grid_segments(110 * 60, 22)
    assert 4 <= len(segs) <= 6


def test_covered_filter_skips_existing():
    segs = [
        SplitSegment(Path("mega.mkv"), 0, 1300, season=1, episode=1, title="Ambush", confidence=90),
        SplitSegment(Path("mega.mkv"), 1300, 2600, season=1, episode=2, title="Rising", confidence=88),
    ]
    covered = {(1, 1): "D1_t01.mkv"}
    apply_covered_filter(segs, covered, skip_if_covered=True)
    assert segs[0].skip is True
    assert segs[0].covered_by == "D1_t01.mkv"
    assert segs[1].skip is False
