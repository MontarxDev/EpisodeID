"""Match dialogue samples against TMDB episode metadata."""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from rapidfuzz import fuzz

from episodeid.models import CandidateMatch, Episode, MatchResult
from episodeid.textutil import join_dialogue

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z']{2,}")

# Common subtitle / TV noise that should not drive matches
_STOP = {
    "the", "and", "for", "that", "this", "with", "you", "your", "are", "was",
    "were", "have", "has", "had", "from", "they", "them", "their", "will",
    "would", "could", "should", "been", "being", "into", "about", "what",
    "when", "where", "which", "who", "whom", "there", "here", "just", "than",
    "then", "now", "out", "not", "but", "all", "any", "can", "her", "his",
    "she", "him", "our", "ours", "its", "yes", "sir", "master", "general",
    "clone", "clones", "droid", "droids", "jedi", "republic", "separatist",
    "force", "must", "get", "one", "two", "off", "too", "very", "indeed",
    "well", "back", "know", "think", "come", "going", "let", "may", "shall",
    "like", "make", "made", "see", "look", "right", "left", "over", "under",
    "after", "before", "only", "also", "more", "some", "such", "while",
    "trying", "attempt", "attempts", "three", "without", "matter", "time",
    "war", "battle", "armies", "enemy", "fire", "shot", "single", "perhaps",
    "further", "evidence", "require", "thought", "believe", "decide", "join",
    "contact", "anything", "launch", "losing", "safety", "retreat", "powers",
    "greatly", "exaggerated", "terrible", "programming", "assures", "delayed",
    "heard", "doubt", "equals", "outnumber", "weakness", "frightened",
}


def _norm_token(tok: str) -> str:
    tok = tok.casefold().strip("'")
    if tok.endswith("'s"):
        tok = tok[:-2]
    return tok


def tokenize(text: str) -> list[str]:
    return [_norm_token(t) for t in _TOKEN_RE.findall(text or "") if _norm_token(t)]


def significant_tokens(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in _STOP and len(t) > 2]


def build_idf(episodes: list[Episode]) -> dict[str, float]:
    """Inverse document frequency over episode title+overview tokens."""
    n = max(len(episodes), 1)
    df: Counter[str] = Counter()
    for ep in episodes:
        df.update(set(significant_tokens(ep.match_corpus + " " + ep.title)))
    return {tok: math.log((n + 1) / (freq + 1)) + 1.0 for tok, freq in df.items()}


def idf_overlap_score(dialogue: str, episode: Episode, idf: dict[str, float]) -> float:
    """0–100 score from IDF-weighted token overlap (good for plots vs dialogue)."""
    d_tokens = significant_tokens(dialogue)
    if not d_tokens:
        return 0.0
    e_set = set(significant_tokens(episode.match_corpus + " " + episode.title))
    if not e_set:
        return 0.0

    d_counts = Counter(d_tokens)
    hit_weight = 0.0
    total_weight = 0.0
    for tok, cnt in d_counts.items():
        # Prefer rare episode-vocabulary tokens; unknown dialogue tokens get low weight
        if tok in e_set:
            w = idf.get(tok, 2.0) * (1.0 + 0.15 * min(cnt, 5))
            hit_weight += w
            total_weight += w
        else:
            total_weight += 0.35  # small mass for non-matching tokens

    coverage = hit_weight / total_weight if total_weight else 0.0

    e_weights = [(t, idf.get(t, 1.0)) for t in e_set]
    d_set = set(d_tokens)
    ep_hit = sum(w for t, w in e_weights if t in d_set)
    ep_all = sum(w for _, w in e_weights) or 1.0
    ep_coverage = ep_hit / ep_all

    title_tokens = set(significant_tokens(episode.title))
    title_bonus = 0.0
    if title_tokens:
        title_bonus = 0.2 * (len(title_tokens & d_set) / len(title_tokens))

    # Distinctive proper-noun style: tokens with high IDF that hit
    distinctive = 0.0
    for tok in d_set & e_set:
        if idf.get(tok, 0) >= 1.8:
            distinctive += idf[tok]
    distinctive = min(1.0, distinctive / 6.0)

    combined = 0.35 * coverage + 0.30 * ep_coverage + 0.15 * title_bonus + 0.35 * distinctive
    return max(0.0, min(100.0, combined * 100.0))


def line_best_scores(lines: list[str], episode: Episode) -> float:
    """Average of best fuzzy scores for top dialogue lines vs episode corpus."""
    if not lines:
        return 0.0
    corpus = episode.match_corpus
    title = episode.title
    scores: list[float] = []
    for line in lines:
        if len(line) < 6:
            continue
        s = max(
            float(fuzz.token_set_ratio(line, corpus)),
            float(fuzz.partial_token_set_ratio(line, corpus)),
            float(fuzz.token_set_ratio(line, title)),
        )
        scores.append(min(100.0, s))
    if not scores:
        return 0.0
    scores.sort(reverse=True)
    top = scores[: min(6, len(scores))]
    return sum(top) / len(top)


def score_dialogue_against_episode(
    dialogue: str,
    episode: Episode,
    *,
    idf: dict[str, float] | None = None,
    lines: list[str] | None = None,
) -> float:
    """Return hybrid score 0–100 for dialogue vs one episode."""
    dialogue = (dialogue or "").strip()
    if not dialogue:
        return 0.0

    idf = idf or {}
    overlap = idf_overlap_score(dialogue, episode, idf) if idf else 0.0

    corpus = episode.match_corpus.strip()
    title = (episode.title or "").strip()
    fuzzy = 0.0
    if corpus:
        fuzzy = max(
            float(fuzz.token_set_ratio(dialogue, corpus)),
            float(fuzz.partial_ratio(dialogue[:500], corpus)),
        )
    if title:
        fuzzy = max(fuzzy, float(fuzz.token_set_ratio(dialogue, title)) * 0.9)

    if lines:
        line_score = line_best_scores(lines, episode)
    else:
        pseudo = [p.strip() for p in re.split(r"[.!?]\s+|\n", dialogue) if len(p.strip()) > 8]
        line_score = line_best_scores(pseudo, episode) if pseudo else 0.0

    # Do not allow pure fuzzy to dominate — plots rarely share wording with dialogue.
    # IDF overlap is the primary signal; fuzzy is a weak support term.
    score = 0.62 * overlap + 0.18 * fuzzy + 0.20 * line_score

    # Strong bonus when high-IDF tokens match (character/place names)
    d_set = set(significant_tokens(dialogue))
    e_set = set(significant_tokens(corpus + " " + title))
    strong_hits = [t for t in d_set & e_set if idf.get(t, 0) >= 1.9]
    if strong_hits:
        score += min(25.0, 6.0 * len(strong_hits) + sum(idf.get(t, 0) for t in strong_hits[:5]))

    return max(0.0, min(100.0, score))


def match_dialogue(
    dialogue: str,
    episodes: list[Episode],
    *,
    path: Path | None = None,
    dialogue_source: str | None = None,
    low_threshold: float = 55.0,
    auto_threshold: float = 70.0,
    top_n: int = 3,
    lines: list[str] | None = None,
) -> MatchResult:
    path = path or Path(".")
    if not dialogue or not dialogue.strip():
        return MatchResult(
            path=path,
            error="No dialogue sample available",
            dialogue_source=dialogue_source,
            low_confidence=True,
        )
    if not episodes:
        return MatchResult(
            path=path,
            error="No episode metadata available",
            dialogue_source=dialogue_source,
            low_confidence=True,
        )

    idf = build_idf(episodes)
    if lines is None:
        lines = [ln.strip() for ln in re.split(r"[\n\r]+|(?<=[.!?])\s+", dialogue) if ln.strip()]

    scored: list[tuple[float, Episode]] = []
    for ep in episodes:
        scored.append(
            (
                score_dialogue_against_episode(dialogue, ep, idf=idf, lines=lines),
                ep,
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best = scored[0]
    if len(scored) > 1:
        margin = best_score - scored[1][0]
        if margin > 5:
            best_score = min(100.0, best_score + min(margin * 0.4, 15))

    candidates = [
        CandidateMatch(
            season=ep.season,
            episode=ep.episode,
            title=ep.title,
            confidence=round(score, 1),
        )
        for score, ep in scored[:top_n]
    ]

    return MatchResult(
        path=path,
        season=best.season,
        episode=best.episode,
        title=best.title,
        confidence=round(best_score, 1),
        low_confidence=best_score < low_threshold,
        candidates=candidates,
        dialogue_source=dialogue_source,
        flags=[]
        if best_score >= auto_threshold
        else (["review"] if best_score >= low_threshold else ["low_confidence"]),
    )


def match_sample(
    lines: list[str],
    episodes: list[Episode],
    **kwargs,
) -> MatchResult:
    return match_dialogue(join_dialogue(lines), episodes, lines=lines, **kwargs)


def demote_duplicate_claims(results: list[MatchResult]) -> list[MatchResult]:
    """Keep highest-confidence claim per SxxExx; flag others as duplicate_claim."""
    best_for_code: dict[str, tuple[int, float]] = {}
    for idx, result in enumerate(results):
        code = result.code
        if not code or result.error:
            continue
        prev = best_for_code.get(code)
        if prev is None or result.confidence > prev[1]:
            best_for_code[code] = (idx, result.confidence)

    winners = {idx for idx, _ in best_for_code.values()}
    for idx, result in enumerate(results):
        code = result.code
        if not code or result.error:
            continue
        if idx not in winners:
            if "duplicate_claim" not in result.flags:
                result.flags.append("duplicate_claim")
            result.low_confidence = True
    return results
