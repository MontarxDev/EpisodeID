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


def _short_corpus(episode: Episode, max_words: int = 55) -> str:
    """Title + truncated overview so long plots don't dominate fuzzy/IDF."""
    title = (episode.title or "").strip()
    overview = (episode.overview or "").strip()
    words = overview.split()
    if len(words) > max_words:
        overview = " ".join(words[:max_words])
    if overview:
        return f"{title}. {overview}"
    return title


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
    # Temporarily score IDF against truncated corpus via a shallow copy-like Episode
    short_ep = Episode(
        season=episode.season,
        episode=episode.episode,
        title=episode.title,
        overview=" ".join((episode.overview or "").split()[:55]),
    )
    overlap = idf_overlap_score(dialogue, short_ep, idf) if idf else 0.0

    corpus = _short_corpus(episode)
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
        line_score = line_best_scores(lines, short_ep)
    else:
        pseudo = [p.strip() for p in re.split(r"[.!?]\s+|\n", dialogue) if len(p.strip()) > 8]
        line_score = line_best_scores(pseudo, short_ep) if pseudo else 0.0

    # IDF overlap is primary; fuzzy is support. Cap fuzzy contribution.
    score = 0.58 * overlap + 0.17 * min(fuzzy, 75.0) + 0.18 * line_score

    # Strong bonus when high-IDF tokens match (character/place names)
    d_set = set(significant_tokens(dialogue))
    e_set = set(significant_tokens(corpus + " " + title))
    strong_hits = [t for t in d_set & e_set if idf.get(t, 0) >= 1.9]
    if strong_hits:
        score += min(22.0, 5.5 * len(strong_hits) + sum(idf.get(t, 0) for t in strong_hits[:5]))

    # Extra boost for title word hits in dialogue (Ambush rarely helps, but "Rookies" etc. do)
    title_toks = set(significant_tokens(title))
    if title_toks and title_toks & d_set:
        score += 8.0 * (len(title_toks & d_set) / len(title_toks))

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
    sample_quality: float = 100.0,
    track_info: str | None = None,
    min_sample_quality: float = 35.0,
) -> MatchResult:
    path = path or Path(".")
    if lines is None:
        lines = [ln.strip() for ln in re.split(r"[\n\r]+|(?<=[.!?])\s+", dialogue or "") if ln.strip()]

    base_meta = dict(
        dialogue_source=dialogue_source,
        dialogue_lines=list(lines or [])[:20],
        sample_quality=sample_quality,
        track_info=track_info,
    )

    if sample_quality < min_sample_quality:
        return MatchResult(
            path=path,
            error=f"Dialogue sample quality too low ({sample_quality:.0f}%) — refusing match",
            low_confidence=True,
            flags=["poor_ocr", "no_match"],
            **base_meta,
        )
    if not dialogue or not dialogue.strip():
        return MatchResult(
            path=path,
            error="No dialogue sample available",
            low_confidence=True,
            **base_meta,
        )
    if not episodes:
        return MatchResult(
            path=path,
            error="No episode metadata available",
            low_confidence=True,
            **base_meta,
        )

    # Need enough distinctive tokens
    if len(set(significant_tokens(dialogue))) < 3:
        return MatchResult(
            path=path,
            error="Not enough distinctive dialogue tokens to match",
            low_confidence=True,
            flags=["short_sample", "no_match"],
            **base_meta,
        )

    idf = build_idf(episodes)

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

    # Scale confidence by sample quality so OCR junk cannot claim 80%+
    quality_factor = max(0.15, min(1.0, sample_quality / 100.0))
    best_score = best_score * (0.35 + 0.65 * quality_factor)

    candidates = [
        CandidateMatch(
            season=ep.season,
            episode=ep.episode,
            title=ep.title,
            confidence=round(score * (0.35 + 0.65 * quality_factor), 1),
        )
        for score, ep in scored[:top_n]
    ]

    flags: list[str] = []
    if best_score >= auto_threshold:
        pass
    elif best_score >= low_threshold:
        flags.append("review")
    else:
        flags.append("low_confidence")

    return MatchResult(
        path=path,
        season=best.season,
        episode=best.episode,
        title=best.title,
        confidence=round(best_score, 1),
        low_confidence=best_score < low_threshold,
        candidates=candidates,
        flags=flags,
        **base_meta,
    )


def match_sample(
    lines: list[str],
    episodes: list[Episode],
    **kwargs,
) -> MatchResult:
    return match_dialogue(join_dialogue(lines), episodes, lines=lines, **kwargs)


def score_all_episodes(
    dialogue: str,
    episodes: list[Episode],
    *,
    lines: list[str] | None = None,
    sample_quality: float = 100.0,
) -> list[float]:
    """Return raw hybrid scores for each episode (quality-scaled)."""
    if not dialogue or not episodes:
        return [0.0] * len(episodes)
    idf = build_idf(episodes)
    if lines is None:
        lines = [ln.strip() for ln in re.split(r"[\n\r]+|(?<=[.!?])\s+", dialogue) if ln.strip()]
    quality_factor = max(0.15, min(1.0, sample_quality / 100.0))
    scores = []
    for ep in episodes:
        sc = score_dialogue_against_episode(dialogue, ep, idf=idf, lines=lines)
        scores.append(sc * (0.35 + 0.65 * quality_factor))
    return scores


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


def reassign_unique_episodes(
    results: list[MatchResult],
    episodes: list[Episode],
    *,
    score_matrix: list[list[float]] | None = None,
    low_threshold: float = 55.0,
    auto_threshold: float = 70.0,
) -> list[MatchResult]:
    """Greedy unique file→episode assignment maximizing scores.

    ``score_matrix[i][j]`` is score of results[i] vs episodes[j].
    When provided, rewrites season/episode/title/confidence for non-error rows.
    """
    if not results or not episodes or not score_matrix:
        return demote_duplicate_claims(results)

    # Build candidate pairs (score, file_idx, ep_idx)
    pairs: list[tuple[float, int, int]] = []
    for i, row_scores in enumerate(score_matrix):
        if results[i].error and "poor_ocr" in (results[i].flags or []):
            continue
        if results[i].sample_quality and results[i].sample_quality < 30:
            continue
        for j, sc in enumerate(row_scores):
            if sc >= max(25.0, low_threshold * 0.4):
                pairs.append((sc, i, j))
    pairs.sort(reverse=True, key=lambda x: x[0])

    used_files: set[int] = set()
    used_eps: set[int] = set()
    assignment: dict[int, tuple[int, float]] = {}
    for sc, i, j in pairs:
        if i in used_files or j in used_eps:
            continue
        used_files.add(i)
        used_eps.add(j)
        assignment[i] = (j, sc)

    for i, result in enumerate(results):
        if i not in assignment:
            continue
        j, sc = assignment[i]
        ep = episodes[j]
        result.season = ep.season
        result.episode = ep.episode
        result.title = ep.title
        result.confidence = round(sc, 1)
        result.low_confidence = sc < low_threshold
        result.error = None
        flags = [f for f in result.flags if f not in {"duplicate_claim", "low_confidence", "review", "no_match"}]
        if sc >= auto_threshold:
            pass
        elif sc >= low_threshold:
            flags.append("review")
        else:
            flags.append("low_confidence")
        if "assigned_unique" not in flags:
            flags.append("assigned_unique")
        result.flags = flags
        # Refresh top candidates from this row's scores
        ranked = sorted(
            ((score_matrix[i][k], episodes[k]) for k in range(len(episodes))),
            key=lambda x: x[0],
            reverse=True,
        )[:3]
        result.candidates = [
            CandidateMatch(
                season=ep.season,
                episode=ep.episode,
                title=ep.title,
                confidence=round(sc, 1),
            )
            for sc, ep in ranked
        ]

    return demote_duplicate_claims(results)
