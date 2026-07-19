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
    """Inverse document frequency over episode title+overview (+ ref sample) tokens."""
    n = max(len(episodes), 1)
    df: Counter[str] = Counter()
    for ep in episodes:
        blob = f"{ep.match_corpus} {ep.title} {ep.ref_dialogue[:800] if ep.ref_dialogue else ''}"
        df.update(set(significant_tokens(blob)))
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


def _short_corpus(episode: Episode, max_words: int = 70) -> str:
    """Title + truncated combined overviews so long plots don't dominate."""
    title = (episode.title or "").strip()
    overview = f"{episode.overview or ''} {episode.extra_overview or ''}".strip()
    words = overview.split()
    if len(words) > max_words:
        overview = " ".join(words[:max_words])
    if overview:
        return f"{title}. {overview}"
    return title


def _ref_dialogue_score(dialogue: str, ref: str, lines: list[str] | None = None) -> float:
    """0–100 score comparing OCR/sample dialogue to reference SRT text."""
    ref = (ref or "").strip()
    dialogue = (dialogue or "").strip()
    if not ref or not dialogue:
        return 0.0
    scores = [
        float(fuzz.token_set_ratio(dialogue, ref[:4000])),
        float(fuzz.partial_ratio(dialogue[:1500], ref[:4000])),
        float(fuzz.token_sort_ratio(dialogue[:2000], ref[:2000])),
    ]
    if lines:
        # Average of best line matches against reference (subtitle-to-subtitle)
        line_scores = []
        for line in lines[:25]:
            if len(line) < 8:
                continue
            line_scores.append(
                max(
                    float(fuzz.partial_ratio(line, ref)),
                    float(fuzz.token_set_ratio(line, ref[:2500])),
                )
            )
        if line_scores:
            line_scores.sort(reverse=True)
            top = line_scores[: min(10, len(line_scores))]
            scores.append(sum(top) / len(top))
    return max(scores)


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
    # Truncated plot corpus for IDF/fuzzy
    short_overview = " ".join(
        f"{episode.overview or ''} {episode.extra_overview or ''}".split()[:70]
    )
    short_ep = Episode(
        season=episode.season,
        episode=episode.episode,
        title=episode.title,
        overview=short_overview,
        extra_overview="",
        ref_dialogue=episode.ref_dialogue,
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

    # Plot-based hybrid
    plot_score = 0.55 * overlap + 0.18 * min(fuzzy, 75.0) + 0.18 * line_score

    d_set = set(significant_tokens(dialogue))
    e_set = set(significant_tokens(corpus + " " + title))
    strong_hits = [t for t in d_set & e_set if idf.get(t, 0) >= 1.9]
    if strong_hits:
        plot_score += min(18.0, 5.0 * len(strong_hits) + sum(idf.get(t, 0) for t in strong_hits[:5]))

    title_toks = set(significant_tokens(title))
    if title_toks and title_toks & d_set:
        plot_score += 8.0 * (len(title_toks & d_set) / len(title_toks))

    # Reference SRT path — high weight only when sample is substantial.
    # Stub/empty refs (common for some cache entries) must not beat peers with
    # full refs via noise; down-weight short ref dialogue.
    ref_score = 0.0
    ref = (episode.ref_dialogue or "").strip()
    if ref:
        ref_score = _ref_dialogue_score(dialogue, ref, lines=lines)
        ref_len = len(ref)
        if ref_len < 400:
            # Stub cache (~508B JSON / tiny sample): barely use ref channel
            ref_weight = 0.12
        elif ref_len < 1200:
            ref_weight = 0.35
        elif ref_score >= 55:
            ref_weight = 0.72
        elif ref_score >= 35:
            ref_weight = 0.55
        else:
            ref_weight = 0.30
        score = ref_weight * ref_score + (1.0 - ref_weight) * plot_score
    else:
        score = plot_score

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


def _track_letter_prefix(name: str) -> str | None:
    """Leading letter class for DVD dumps: D1_t01 → D, E4_t04 → E, C1_t01 → C."""
    m = re.match(r"^([A-Za-z]+)\d", name or "")
    return m.group(1).upper() if m else None


def filter_sequential_track_indices(
    paths: list[Path],
    results: list[MatchResult],
    ordered_idx: list[int],
) -> list[int]:
    """Keep main episode tracks; drop lone outliers that steal rank 0 (e.g. D1_t08 among E*)."""
    candidates: list[int] = []
    for i in ordered_idx:
        r = results[i]
        if "trusted_filename" in (r.flags or []) and r.season is not None:
            continue
        if r.error and (r.sample_quality or 0) < 30:
            continue
        if "likely_extra" in (r.flags or []):
            continue
        candidates.append(i)
    if len(candidates) < 2:
        return candidates

    # Majority letter prefix among candidates (E vs D vs C)
    letters = [_track_letter_prefix(paths[i].name if i < len(paths) else "") for i in candidates]
    from collections import Counter

    counts = Counter(L for L in letters if L)
    if not counts:
        return candidates
    majority, maj_n = counts.most_common(1)[0]
    # If clear majority (≥ half), drop other prefixes (D1_t08 among E1–E6)
    if maj_n >= max(2, (len(candidates) + 1) // 2):
        filtered = [
            i
            for i, L in zip(candidates, letters)
            if L == majority or L is None
        ]
        if len(filtered) >= 2:
            return filtered
    return candidates


def contiguous_episode_runs(
    free_j: list[int],
    episodes: list[Episode],
) -> list[list[int]]:
    """Group free catalog indices into contiguous episode-number runs (same season)."""
    if not free_j:
        return []
    runs: list[list[int]] = []
    cur = [free_j[0]]
    for j in free_j[1:]:
        prev = episodes[cur[-1]]
        ep = episodes[j]
        if ep.season == prev.season and ep.episode == prev.episode + 1:
            cur.append(j)
        else:
            runs.append(cur)
            cur = [j]
    runs.append(cur)
    return runs


def pick_primary_free_block(
    free_j: list[int],
    episodes: list[Episode],
    n_tracks: int,
) -> list[int]:
    """Choose a contiguous free block sized for n_tracks (prefer longest suitable run).

    Avoids gappy free lists like [E05, E16..E20] mapping track0→E05, track1→E16.
    Orphans (runs of length 1 while a long block exists) are left for a later pass.
    """
    if not free_j or n_tracks <= 0:
        return []
    runs = contiguous_episode_runs(free_j, episodes)
    if not runs:
        return free_j[:n_tracks]

    # Prefer runs that can host n_tracks, then longest, then earliest episode
    def run_key(run: list[int]) -> tuple:
        ep0 = episodes[run[0]]
        fits = 1 if len(run) >= n_tracks else 0
        return (fits, len(run), -ep0.season, -ep0.episode)

    runs_sorted = sorted(runs, key=run_key, reverse=True)
    primary = runs_sorted[0]
    if len(primary) >= n_tracks:
        return primary[:n_tracks]
    # Not enough in one run: take longest then append next best contiguous pieces
    block = list(primary)
    for run in runs_sorted[1:]:
        if len(block) >= n_tracks:
            break
        # Only append if adjacent to block end
        last = episodes[block[-1]]
        first = episodes[run[0]]
        if first.season == last.season and first.episode == last.episode + 1:
            block.extend(run)
        elif len(run) > len(block) and len(block) < n_tracks // 2:
            # Switch to clearly better late block (orphan early hole vs long run)
            block = list(run)
    return block[:n_tracks] if block else free_j[:n_tracks]


def reassign_sequential_disc(
    results: list[MatchResult],
    episodes: list[Episode],
    paths: list[Path],
    score_matrix: list[list[float]],
    *,
    blocked: set[tuple[int, int]] | None = None,
    order_penalty: float = 20.0,
    low_threshold: float = 55.0,
    auto_threshold: float = 70.0,
) -> list[MatchResult]:
    """Assign season-disc tracks to a contiguous free episode block.

    - Drops outlier tracks (e.g. D1_t08 among E* rips) from ranking.
    - Uses longest contiguous free run (not gappy free list).
    - Only allows ±1 slot deviation unless sequential score is terrible.
    - Blends dialogue conf with layout conf for sequential_prior rows.
    """
    from episodeid.renamer import natural_sort_key

    n = len(results)
    if n == 0 or not episodes or not score_matrix or len(score_matrix) != n:
        return reassign_unique_episodes(
            results,
            episodes,
            score_matrix=score_matrix,
            low_threshold=low_threshold,
            auto_threshold=auto_threshold,
            blocked=blocked,
        )

    blocked = set(blocked or set())
    for r in results:
        if "trusted_filename" in (r.flags or []) and r.season is not None and r.episode is not None:
            blocked.add((int(r.season), int(r.episode)))

    free_j_all = sorted(
        (
            j
            for j, ep in enumerate(episodes)
            if (int(ep.season), int(ep.episode)) not in blocked
        ),
        key=lambda j: (episodes[j].season, episodes[j].episode),
    )
    if not free_j_all:
        return results

    order = sorted(
        range(n),
        key=lambda i: natural_sort_key(paths[i].name if i < len(paths) else str(i)),
    )
    assignable = filter_sequential_track_indices(paths, results, order)
    if len(assignable) < 2:
        return reassign_unique_episodes(
            results,
            episodes,
            score_matrix=score_matrix,
            low_threshold=low_threshold,
            auto_threshold=auto_threshold,
            blocked=blocked,
        )

    # Contiguous free block sized for main tracks (show-agnostic layout)
    target_block = pick_primary_free_block(free_j_all, episodes, len(assignable))
    if not target_block:
        target_block = free_j_all[: len(assignable)]

    # STRICT sequential: track rank i → target_block[i].
    # Dialogue only affects confidence — never open mid-block holes (E04→E06).
    assignment: dict[int, tuple[int, float, bool]] = {}
    claimed_ep_j: set[int] = set()
    for rank, file_i in enumerate(assignable):
        row = score_matrix[file_i]
        if rank < len(target_block):
            ep_j = target_block[rank]
            raw = float(row[ep_j]) if ep_j < len(row) else 50.0
            sequential = True
        else:
            # More tracks than primary block: best remaining free (orphans)
            leftovers = [j for j in free_j_all if j not in claimed_ep_j]
            if not leftovers:
                continue
            ep_j = max(
                leftovers,
                key=lambda j: float(row[j]) if j < len(row) else 0.0,
            )
            raw = float(row[ep_j]) if ep_j < len(row) else 0.0
            sequential = False
        claimed_ep_j.add(ep_j)
        assignment[file_i] = (ep_j, raw, sequential)

    for file_i, (ep_j, sc, sequential) in assignment.items():
        ep = episodes[ep_j]
        r = results[file_i]
        r.season = ep.season
        r.episode = ep.episode
        r.title = ep.title
        dialogue_conf = max(0.0, min(100.0, float(sc)))
        layout_conf = 90.0 if sequential else 80.0
        # Layout-backed blend: correct order shouldn't look like a coin-flip
        blended = (
            0.50 * dialogue_conf + 0.50 * layout_conf
            if sequential
            else 0.70 * dialogue_conf + 0.30 * layout_conf
        )
        r.confidence = round(max(0.0, min(100.0, blended)), 1)
        r.low_confidence = dialogue_conf < low_threshold
        r.error = None
        flags = [
            f
            for f in r.flags
            if f
            not in {
                "duplicate_claim",
                "low_confidence",
                "review",
                "no_match",
                "reassigned_global",
            }
        ]
        if "assigned_unique" not in flags:
            flags.append("assigned_unique")
        if "sequential_disc" not in flags:
            flags.append("sequential_disc")
        if sequential and "sequential_prior" not in flags:
            flags.append("sequential_prior")
        if dialogue_conf < low_threshold:
            flags.append("review")
        elif blended < auto_threshold:
            flags.append("review")
        r.flags = flags
        if r.track_info and "dlg=" not in (r.track_info or ""):
            r.track_info = f"{r.track_info} dlg={dialogue_conf:.0f}"
        elif not r.track_info:
            r.track_info = f"dlg={dialogue_conf:.0f}"

    return demote_duplicate_claims(results)


def reassign_unique_episodes(
    results: list[MatchResult],
    episodes: list[Episode],
    *,
    score_matrix: list[list[float]] | None = None,
    low_threshold: float = 55.0,
    auto_threshold: float = 70.0,
    blocked: set[tuple[int, int]] | None = None,
) -> list[MatchResult]:
    """Greedy unique file→episode assignment maximizing scores.

    ``score_matrix[i][j]`` is score of results[i] vs episodes[j].
    ``blocked`` episodes (already covered by prior discs / library) are never assigned.
    """
    if not results or not episodes or not score_matrix:
        return demote_duplicate_claims(results)

    blocked = blocked or set()
    blocked_j = {
        j
        for j, ep in enumerate(episodes)
        if (int(ep.season), int(ep.episode)) in blocked
    }

    # Build candidate pairs (score, file_idx, ep_idx)
    pairs: list[tuple[float, int, int]] = []
    for i, row_scores in enumerate(score_matrix):
        if results[i].error and "poor_ocr" in (results[i].flags or []):
            continue
        if results[i].sample_quality and results[i].sample_quality < 30:
            continue
        # Trusted filenames keep their identity — pin them
        if "trusted_filename" in (results[i].flags or []) and results[i].season is not None:
            continue
        for j, sc in enumerate(row_scores):
            if j in blocked_j:
                continue
            if sc >= max(25.0, low_threshold * 0.4):
                pairs.append((sc, i, j))
    pairs.sort(reverse=True, key=lambda x: x[0])

    used_files: set[int] = set()
    used_eps: set[int] = set()
    # Pin trusted
    for i, result in enumerate(results):
        if "trusted_filename" in (result.flags or []) and result.season is not None:
            used_files.add(i)
            for j, ep in enumerate(episodes):
                if ep.season == result.season and ep.episode == result.episode:
                    used_eps.add(j)
                    break

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
        result.confidence = round(max(0.0, min(100.0, float(sc))), 1)
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
        ranked = sorted(
            ((score_matrix[i][k], episodes[k]) for k in range(len(episodes)) if k not in blocked_j),
            key=lambda x: x[0],
            reverse=True,
        )[:3]
        result.candidates = [
            CandidateMatch(
                season=ep.season,
                episode=ep.episode,
                title=ep.title,
                confidence=round(min(100.0, float(sc)), 1),
            )
            for sc, ep in ranked
        ]

    return demote_duplicate_claims(results)
