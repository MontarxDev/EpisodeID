from pathlib import Path

from episodeid.matcher import match_dialogue, score_dialogue_against_episode, build_idf
from episodeid.models import Episode
from episodeid.tvmaze import enrich_episodes_with_tvmaze


def test_reference_dialogue_beats_misleading_plot():
    """OCR dialogue matching a reference SRT should win over plot-only noise."""
    ambush = Episode(
        1,
        1,
        "Ambush",
        "Jedi Master Yoda faces Ventress.",
        ref_dialogue=(
            "Retreat you must. Master Yoda's warship. Toydarian Royal Delegation. "
            "The enemy will fire at anything we launch. Suppose you and your droid armies."
        ),
    )
    gungan = Episode(
        1,
        12,
        "The Gungan General",
        "Anakin and Obi-Wan held for ransom. Republic special envoy delivers ransom with Jar Jar.",
        ref_dialogue=(
            "Hondo Ohnaka welcomes you. The ransom is ready. Jar Jar take charge of the mission."
        ),
    )
    episodes = [ambush, gungan]
    dialogue = (
        "suppose you and your droid armies Retreat you must "
        "Toydarian Royal Delegation Master Yoda's warship "
        "The enemy will fire at anything we launch"
    )
    lines = dialogue.split("  ") if "  " in dialogue else [
        "suppose you and your droid armies",
        "Retreat you must",
        "Toydarian Royal Delegation",
        "Master Yoda's warship",
        "The enemy will fire at anything we launch",
    ]
    idf = build_idf(episodes)
    a = score_dialogue_against_episode(dialogue, ambush, idf=idf, lines=lines)
    g = score_dialogue_against_episode(dialogue, gungan, idf=idf, lines=lines)
    assert a > g
    result = match_dialogue(
        dialogue, episodes, path=Path("d1.mkv"), lines=lines, sample_quality=90
    )
    assert result.season == 1 and result.episode == 1


def test_tvmaze_enrich_mock(monkeypatch):
    episodes = [
        Episode(1, 1, "Ambush", "Short TMDB plot."),
    ]

    def fake_search(name, session=None):
        return {"id": 563, "name": "Star Wars: The Clone Wars"}

    def fake_fetch(show_id, session=None):
        return [
            {
                "season": 1,
                "number": 1,
                "name": "Ambush",
                "summary": "<p>Yoda and clones face Ventress and her droid army on a strategic world.</p>",
                "runtime": 22,
            }
        ]

    monkeypatch.setattr("episodeid.tvmaze.search_show", fake_search)
    monkeypatch.setattr("episodeid.tvmaze.fetch_episodes", fake_fetch)
    out = enrich_episodes_with_tvmaze(episodes, "Star Wars: The Clone Wars")
    assert "Ventress" in out[0].extra_overview or "Ventress" in out[0].match_corpus
    assert out[0].runtime == 22


def test_ref_srt_parse_sample():
    from episodeid.refsubs import _sample_from_srt_bytes

    srt = b"""1
00:00:01,000 --> 00:00:03,000
Retreat you must.

2
00:00:04,000 --> 00:00:06,000
Master Yoda's warship is under attack.
"""
    lines = _sample_from_srt_bytes(srt)
    assert any("Retreat" in ln for ln in lines)
