from episodeid.textutil import clean_line, join_dialogue, unique_lines


def test_clean_line_strips_ass_and_tags():
    raw = r"{\i1}<i>Hello</i> there!"
    assert clean_line(raw) == "Hello there!"


def test_unique_lines_dedupes_and_limits():
    lines = ["Hello", "hello", "World", "!!!", "Another"]
    out = unique_lines(lines, max_lines=2)
    assert out == ["Hello", "World"]


def test_join_dialogue():
    assert join_dialogue(["A", "B"]) == "A B"
