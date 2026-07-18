from episodeid.textutil import (
    clean_line,
    join_dialogue,
    line_quality,
    sample_quality,
    unique_lines,
    unique_quality_lines,
)


def test_clean_line_strips_ass_and_tags():
    raw = r"{\i1}<i>Hello</i> there!"
    assert clean_line(raw) == "Hello there!"


def test_unique_lines_prefers_quality_not_junk():
    lines = ["= | 7 se Pre", "me hoya", "Too late it is. Sprung is the trap.", "hello"]
    out = unique_quality_lines(lines, max_lines=5, min_quality=0.35)
    assert any("trap" in ln.lower() or "late" in ln.lower() for ln in out)
    assert not any("|" in ln for ln in out)


def test_gibberish_low_quality():
    assert line_quality("= | 7 se Pre") < 0.35
    assert line_quality("Too late it is. Sprung is the trap.") >= 0.35


def test_sample_quality_empty():
    assert sample_quality([]) == 0.0


def test_join_dialogue():
    assert join_dialogue(["A", "B"]) == "A B"
