"""MKV chapter inventory + mkvmerge split command construction."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from episodeid.splitter import (
    Chapter,
    chapters_as_episode_segments,
    inventory_segments,
    row_uses_mkv_chapters,
    split_via_mkvmerge_chapters,
)
from episodeid.models import RenamePlanRow


def _s7_d1_chapters() -> list[Chapter]:
    """Real S7_D1 chapter times (seconds)."""
    return [
        Chapter(0, 0.0, 1463.2618, "Chapter 01"),
        Chapter(1, 1463.2618, 2874.404867, "Chapter 02"),
        Chapter(2, 2874.404867, 3976.906267, "Chapter 03"),
        Chapter(3, 3976.906267, 5364.576, "Chapter 04"),
    ]


def test_chapters_as_episodes_s7_style():
    ch = _s7_d1_chapters()
    pairs = chapters_as_episode_segments(ch, 5364.576)
    assert pairs is not None
    assert len(pairs) == 4
    assert abs(pairs[0][0] - 0.0) < 0.01
    assert abs(pairs[1][0] - 1463.2618) < 0.01
    assert abs(pairs[3][1] - 5364.576) < 0.01


def test_inventory_respects_chapters_even_when_median_is_26(tmp_path: Path):
    """Regression: median 26 min must not force 3-grid when 4 MKV chapters exist."""
    fake = tmp_path / "A1_t00.mkv"
    fake.write_bytes(b"x" * 100)

    with (
        patch("episodeid.splitter.probe_duration_seconds", return_value=5364.576),
        patch("episodeid.splitter.probe_chapters", return_value=_s7_d1_chapters()),
    ):
        segs = inventory_segments(fake, expected_runtime_min=26.0)
    assert len(segs) == 4
    assert segs[0].method == "mkv_chapters"
    # Real chapter bounds, not equal 29.8m thirds
    assert segs[0].end < 1500
    assert abs(segs[1].start - 1463.26) < 1.0
    assert segs[2].start > 2800


def test_short_chapters_not_treated_as_episodes():
    # Many tiny chapters → None → cluster path
    ch = [Chapter(i, i * 30.0, (i + 1) * 30.0) for i in range(20)]
    assert chapters_as_episode_segments(ch, 600.0) is None


def test_row_uses_mkv_chapters_flag():
    row = RenamePlanRow(
        path=Path("m.mkv"),
        original_name="m.mkv",
        flags=["split_segment", "mkv_chapters"],
        track_info="mkv_chapters 0.0-1463.0s",
    )
    assert row_uses_mkv_chapters(row) is True
    row2 = RenamePlanRow(
        path=Path("m.mkv"),
        original_name="m.mkv",
        track_info="auto_grid 0.0-100.0s",
    )
    assert row_uses_mkv_chapters(row2) is False


def test_split_via_mkvmerge_builds_chapters_all(tmp_path: Path):
    src = tmp_path / "mega.mkv"
    src.write_bytes(b"fake")
    out = tmp_path / "parts"
    out.mkdir()

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stderr = ""
    mock_proc.stdout = ""

    def fake_run(cmd, **_kw):
        # Simulate mkvmerge writing numbered parts
        for i in range(1, 5):
            p = out / f"part-{i:03d}.mkv"
            p.write_bytes(b"x" * 2000)
        return mock_proc

    with (
        patch("episodeid.splitter.which", return_value="/usr/bin/mkvmerge"),
        patch("episodeid.splitter.run_cmd", side_effect=fake_run) as run,
    ):
        parts = split_via_mkvmerge_chapters(src, out)
    assert len(parts) == 4
    cmd = run.call_args[0][0]
    assert "--split" in cmd
    assert "chapters:all" in cmd
    assert str(src) in cmd
