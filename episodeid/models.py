"""Shared data models for EpisodeID."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Episode:
    season: int
    episode: int
    title: str
    overview: str = ""

    @property
    def code(self) -> str:
        return f"S{self.season:02d}E{self.episode:02d}"

    @property
    def match_corpus(self) -> str:
        title = (self.title or "").strip()
        overview = (self.overview or "").strip()
        if overview:
            return f"{title}. {overview}"
        return title


@dataclass
class SeriesInfo:
    id: int
    name: str
    year: int | None = None
    overview: str = ""

    def display_name(self) -> str:
        if self.year:
            return f"{self.name} ({self.year})"
        return self.name


@dataclass
class DialogueSample:
    lines: list[str] = field(default_factory=list)
    source: str = "none"  # external_srt | embedded_text | ocr_pgs | ocr_vobsub | ocr_overlay | none
    raw_text: str = ""
    track_info: str = ""

    def is_empty(self) -> bool:
        return not (self.raw_text or self.lines)


@dataclass
class CandidateMatch:
    season: int
    episode: int
    title: str
    confidence: float


@dataclass
class MatchResult:
    path: Path
    season: int | None = None
    episode: int | None = None
    title: str | None = None
    confidence: float = 0.0
    low_confidence: bool = True
    candidates: list[CandidateMatch] = field(default_factory=list)
    error: str | None = None
    dialogue_source: str | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def code(self) -> str | None:
        if self.season is None or self.episode is None:
            return None
        return f"S{self.season:02d}E{self.episode:02d}"


@dataclass
class RenamePlanRow:
    path: Path
    original_name: str
    season: int | None = None
    episode: int | None = None
    official_title: str = ""
    confidence: float = 0.0
    proposed_name: str = ""
    target_dir: Path | None = None
    selected: bool = False
    move_to_season: bool = False
    error: str | None = None
    dialogue_source: str | None = None
    flags: list[str] = field(default_factory=list)
    candidates: list[CandidateMatch] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "original_name": self.original_name,
            "season": self.season,
            "episode": self.episode,
            "official_title": self.official_title,
            "confidence": self.confidence,
            "proposed_name": self.proposed_name,
            "target_dir": str(self.target_dir) if self.target_dir else None,
            "selected": self.selected,
            "move_to_season": self.move_to_season,
            "error": self.error,
            "dialogue_source": self.dialogue_source,
            "flags": list(self.flags),
        }


@dataclass
class ProgressEvent:
    phase: str
    current: int = 0
    total: int = 0
    message: str = ""
    path: str | None = None
