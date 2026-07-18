"""Dialogue text cleaning and quality scoring helpers."""

from __future__ import annotations

import re
from html import unescape

_TAG_RE = re.compile(r"<[^>]+>")
_ASS_OVERRIDE_RE = re.compile(r"\{[^}]*\}")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_MULTI_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_LINE_RE = re.compile(r"^[\W_\d]+$", re.UNICODE)
_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_VOWELS = set("aeiouyAEIOUY")


def strip_markup(text: str) -> str:
    text = unescape(text or "")
    text = _ASS_OVERRIDE_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    text = text.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
    return text


def clean_line(text: str, strip_brackets: bool = True) -> str:
    text = strip_markup(text)
    if strip_brackets:
        text = _BRACKET_RE.sub("", text)
    text = text.replace("\u266a", "").replace("♪", "")
    # Drop common OCR junk characters
    text = re.sub(r"[|\\/_=~`^]+", " ", text)
    text = _MULTI_SPACE_RE.sub(" ", text).strip(" \t\r\n-–—:;,.")
    return text


def is_useful_line(text: str) -> bool:
    return line_quality(text) >= 0.35


def line_quality(text: str) -> float:
    """Return 0–1 quality score for a single dialogue line (OCR-aware)."""
    text = clean_line(text or "")
    if not text or len(text) < 3:
        return 0.0
    if _NON_ALNUM_LINE_RE.match(text):
        return 0.0

    letters = sum(ch.isalpha() for ch in text)
    digits = sum(ch.isdigit() for ch in text)
    spaces = text.count(" ")
    other = len(text) - letters - digits - spaces
    if letters < 4:
        return 0.0

    alpha_ratio = letters / max(len(text), 1)
    junk_ratio = other / max(len(text), 1)
    words = _WORD_RE.findall(text)
    if not words:
        return 0.0

    # Vowel presence — real English words almost always have vowels
    vowelish = sum(1 for w in words if any(c in _VOWELS for c in w) and len(w) >= 2)
    vowel_ratio = vowelish / len(words)

    avg_len = sum(len(w) for w in words) / len(words)
    long_enough = sum(1 for w in words if len(w) >= 3) / len(words)
    # Real subtitle lines usually have 3+ words or one long sentence fragment
    word_count = len(words)

    score = 0.0
    score += 0.30 * min(1.0, alpha_ratio / 0.75)
    score += 0.25 * vowel_ratio
    score += 0.20 * long_enough
    score += 0.10 * min(1.0, avg_len / 4.5)
    score += 0.15 * min(1.0, word_count / 5.0)
    score -= 0.50 * junk_ratio
    score -= 0.25 * (digits / max(len(text), 1))
    if junk_ratio > 0.2:
        score -= 0.25
    if word_count <= 2 and avg_len < 4.5:
        score *= 0.35
    if word_count == 1 and len(words[0]) < 6:
        score *= 0.4
    # OCR garbage often has almost no spaces / random short tokens
    if word_count >= 2 and long_enough < 0.4:
        score *= 0.4
    return max(0.0, min(1.0, score))


def unique_quality_lines(
    lines: list[str],
    max_lines: int = 40,
    *,
    min_quality: float = 0.35,
    strip_brackets: bool = True,
) -> list[str]:
    """Dedupe and keep the highest-quality lines (not first-come)."""
    scored: list[tuple[float, str]] = []
    seen: set[str] = set()
    for raw in lines:
        line = clean_line(raw, strip_brackets=strip_brackets)
        q = line_quality(line)
        if q < min_quality:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        scored.append((q, line))
    scored.sort(key=lambda x: (-x[0], -len(x[1])))
    return [line for _, line in scored[:max_lines]]


def unique_lines(lines: list[str], max_lines: int = 40, strip_brackets: bool = True) -> list[str]:
    # Back-compat wrapper: quality-ranked unique lines
    return unique_quality_lines(
        lines, max_lines=max_lines, min_quality=0.35, strip_brackets=strip_brackets
    )


def join_dialogue(lines: list[str]) -> str:
    return " ".join(lines).strip()


_COMMON_EN = {
    "the", "and", "you", "that", "for", "with", "this", "have", "from", "they",
    "will", "would", "there", "their", "what", "about", "which", "when", "make",
    "like", "time", "just", "know", "take", "people", "into", "year", "your",
    "good", "some", "could", "them", "other", "than", "then", "now", "look",
    "only", "come", "over", "think", "also", "back", "after", "work", "first",
    "well", "even", "want", "because", "these", "give", "most", "must", "master",
    "general", "sir", "yes", "not", "are", "was", "were", "been", "being",
    "join", "republic", "force", "ship", "enemy", "attack", "clone", "droid",
    "jedi", "sith", "count", "king", "queen", "help", "need", "find", "leave",
    "right", "left", "here", "where", "who", "how", "all", "our", "out", "get",
}


def _english_word_ratio(lines: list[str]) -> float:
    words = []
    for ln in lines:
        words.extend(w.casefold() for w in _WORD_RE.findall(ln))
    if not words:
        return 0.0
    hits = sum(1 for w in words if w in _COMMON_EN or (len(w) >= 4 and any(c in _VOWELS for c in w)))
    # Count dictionary hits more heavily
    dict_hits = sum(1 for w in words if w in _COMMON_EN)
    return 0.5 * (hits / len(words)) + 0.5 * min(1.0, dict_hits / max(3, len(words) * 0.25))


def sample_quality(lines: list[str]) -> float:
    """Overall 0–100 quality for a dialogue sample."""
    if not lines:
        return 0.0
    qualities = [line_quality(ln) for ln in lines]
    qualities = [q for q in qualities if q > 0]
    if not qualities:
        return 0.0
    qualities.sort(reverse=True)
    top = qualities[: min(12, len(qualities))]
    avg = sum(top) / len(top)
    coverage = min(1.0, len(top) / 8.0)
    eng = _english_word_ratio(lines)
    # OCR letter-salad can look "letter-rich" but fails English-ness
    score = 100.0 * (0.45 * avg + 0.20 * coverage * avg + 0.35 * eng)
    if eng < 0.15:
        score *= 0.45
    return max(0.0, min(100.0, score))
