"""Dialogue text cleaning helpers."""

from __future__ import annotations

import re
from html import unescape

_TAG_RE = re.compile(r"<[^>]+>")
_ASS_OVERRIDE_RE = re.compile(r"\{[^}]*\}")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_MULTI_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_LINE_RE = re.compile(r"^[\W_\d]+$", re.UNICODE)


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
    text = _MULTI_SPACE_RE.sub(" ", text).strip(" \t\r\n-–—")
    return text


def is_useful_line(text: str) -> bool:
    if not text or len(text) < 2:
        return False
    if _NON_ALNUM_LINE_RE.match(text):
        return False
    letters = sum(ch.isalpha() for ch in text)
    return letters >= 2


def unique_lines(lines: list[str], max_lines: int = 40, strip_brackets: bool = True) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        line = clean_line(raw, strip_brackets=strip_brackets)
        if not is_useful_line(line):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= max_lines:
            break
    return out


def join_dialogue(lines: list[str]) -> str:
    return " ".join(lines).strip()
