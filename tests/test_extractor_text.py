from pathlib import Path

from episodeid.extractor import filter_by_size, find_external_subtitle, list_video_files
from episodeid.textutil import unique_lines


def test_list_and_filter(tmp_path: Path):
    big = tmp_path / "ep1.mkv"
    mid = tmp_path / "ep2.mkv"
    small = tmp_path / "menu.mkv"
    big.write_bytes(b"x" * 1000)
    mid.write_bytes(b"x" * 900)
    small.write_bytes(b"x" * 50)
    files = list_video_files(tmp_path)
    assert len(files) == 3
    keep, skipped = filter_by_size(files, enabled=True, ratio=0.25)
    assert small in skipped
    assert big in keep


def test_external_srt(tmp_path: Path):
    video = tmp_path / "show.mkv"
    video.write_bytes(b"x")
    srt = tmp_path / "show.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello there\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nGeneral Kenobi\n",
        encoding="utf-8",
    )
    found = find_external_subtitle(video)
    assert found == srt


def test_unique_from_srt_lines():
    lines = unique_lines(["Hello there", "Hello there", "General Kenobi"])
    assert lines == ["Hello there", "General Kenobi"]
