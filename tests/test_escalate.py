"""Tests for escalating multi-sample identification."""

from pathlib import Path
from unittest.mock import patch

from episodeid.models import DialogueSample, Episode
from episodeid.splitter import SplitSegment, _segment_sample_windows, identify_segment


def _eps() -> list[Episode]:
    return [
        Episode(7, 1, "The Bad Batch", "Bad Batch Wrecker Yalbec Queen defective clones"),
        Episode(7, 2, "A Distant Echo", "Purkoll signal Echo rescue prisoner on planet"),
        Episode(7, 3, "On the Wings of Keeradaks", "Techno Union Skako Minor profit margin"),
    ]


def _sample(lines: list[str], quality: float = 90.0) -> DialogueSample:
    return DialogueSample(
        lines=lines,
        source="ocr_pgs",
        raw_text=" ".join(lines),
        track_info="test",
        quality=quality,
        duration_sec=1800,
    )


def test_segment_windows_three_passes():
    seg = SplitSegment(Path("mega.mkv"), 0.0, 1800.0)
    wins = _segment_sample_windows(seg, n_windows=3)
    assert len(wins) == 3
    # offsets increase
    assert wins[0][0] < wins[1][0] < wins[2][0]


def test_identify_stops_when_first_pass_high():
    from episodeid.models import MatchResult

    seg = SplitSegment(Path("mega.mkv"), 0.0, 1800.0)
    calls = {"n": 0}

    def fake_sample(*_a, **_k):
        calls["n"] += 1
        return _sample(
            [
                "Wrecker cut off the Yalbec Queen stinger",
                "defective clones Bad Batch cavalry",
                "Echo fingerprints Separatist strategies",
            ]
        )

    def fake_match(*_a, **_k):
        return MatchResult(
            path=Path("mega.mkv"),
            season=7,
            episode=1,
            title="The Bad Batch",
            confidence=93.0,
            low_confidence=False,
        )

    with (
        patch("episodeid.splitter.sample_dialogue", side_effect=fake_sample),
        patch("episodeid.splitter.match_dialogue", side_effect=fake_match),
    ):
        identify_segment(
            seg,
            _eps(),
            escalate_enabled=True,
            escalate_below=80.0,
            max_extra_samples=2,
        )
    # Strong first hit should not need extra samples
    assert calls["n"] == 1
    assert "escalated_sample" not in seg.flags
    assert seg.episode == 1


def test_identify_escalates_when_low_then_improves():
    seg = SplitSegment(Path("mega.mkv"), 0.0, 1800.0)
    calls = {"n": 0}

    weak = _sample(
        [
            "a slight communication problem here",
            "we will leave his planet for good",
            "as soon as we rescue him",
        ],
        quality=85,
    )
    strong = _sample(
        [
            "Rex Anakin Bad Batch mysterious Separatist signal",
            "Echo is the prisoner in Purkoll",
            "we must find the signal source",
            "A Distant Echo from the Techno Union",
        ],
        quality=90,
    )

    def fake_sample(*_a, **_k):
        calls["n"] += 1
        return weak if calls["n"] == 1 else strong

    with patch("episodeid.splitter.sample_dialogue", side_effect=fake_sample):
        identify_segment(
            seg,
            _eps(),
            escalate_enabled=True,
            escalate_below=80.0,
            max_extra_samples=2,
            low_threshold=55.0,
            auto_threshold=70.0,
        )
    assert calls["n"] >= 2
    assert "escalated_sample" in seg.flags
    assert seg.season == 7
    assert seg.episode == 2


def test_identify_no_escalate_when_disabled():
    seg = SplitSegment(Path("mega.mkv"), 0.0, 1800.0)
    calls = {"n": 0}

    def fake_sample(*_a, **_k):
        calls["n"] += 1
        return _sample(["hello there general kenobi maybe"], quality=80)

    with patch("episodeid.splitter.sample_dialogue", side_effect=fake_sample):
        identify_segment(
            seg,
            _eps(),
            escalate_enabled=False,
            escalate_below=80.0,
            max_extra_samples=2,
        )
    assert calls["n"] == 1
