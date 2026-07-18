from pathlib import Path

from episodeid.matcher import (
    build_idf,
    demote_duplicate_claims,
    match_dialogue,
    score_dialogue_against_episode,
)
from episodeid.models import Episode, MatchResult


EPISODES = [
    Episode(
        1,
        1,
        "Ambush",
        "Jedi Master Yoda is stranded with clone troopers on Rugosa after an escape pod landing while Asajj Ventress ambushes them and King Katuunko watches.",
    ),
    Episode(
        1,
        2,
        "Rising Malevolence",
        "Anakin and Plo Koon investigate a mysterious Separatist weapon called the Malevolence.",
    ),
    Episode(
        1,
        3,
        "Shadow of Malevolence",
        "The Jedi attempt to destroy the Malevolence superweapon.",
    ),
]


def test_scores_ambush_dialogue_highest():
    dialogue = (
        "Master Yoda's powers have been greatly exaggerated. "
        "In an escape pod, sir? The enemy will fire at anything we launch. "
        "King Katuunko, Ventress will fail. Ambush is the trap."
    )
    lines = [
        "Master Yoda's powers have been greatly exaggerated.",
        "In an escape pod, sir?",
        "King Katuunko waits.",
        "Asajj Ventress will fail.",
    ]
    idf = build_idf(EPISODES)
    ambush = score_dialogue_against_episode(dialogue, EPISODES[0], idf=idf, lines=lines)
    rising = score_dialogue_against_episode(dialogue, EPISODES[1], idf=idf, lines=lines)
    assert ambush > rising
    assert ambush > 40


def test_match_dialogue_picks_best():
    dialogue = (
        "Anakin Skywalker and Plo Koon investigate the Malevolence weapon "
        "mysterious fleet attacks General Grievous"
    )
    result = match_dialogue(dialogue, EPISODES, path=Path("x.mkv"))
    assert result.season == 1
    assert result.episode == 2
    assert result.confidence > 0
    assert len(result.candidates) >= 1


def test_empty_dialogue_errors():
    result = match_dialogue("", EPISODES, path=Path("x.mkv"), sample_quality=80)
    assert result.error


def test_poor_ocr_refuses_match():
    result = match_dialogue(
        "me hoya se pre xyz abc",
        EPISODES,
        path=Path("x.mkv"),
        sample_quality=10,
    )
    assert result.error
    assert result.season is None


def test_demote_duplicates():
    a = MatchResult(path=Path("a.mkv"), season=1, episode=1, title="Ambush", confidence=90)
    b = MatchResult(path=Path("b.mkv"), season=1, episode=1, title="Ambush", confidence=60)
    demote_duplicate_claims([a, b])
    assert "duplicate_claim" in b.flags
    assert "duplicate_claim" not in a.flags
