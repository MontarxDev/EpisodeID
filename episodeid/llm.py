"""Optional LLM-based episode identification (short text samples only)."""

from __future__ import annotations

import json
import re
from typing import Any

from episodeid.models import Episode, MatchResult
from pathlib import Path


PROMPT_TEMPLATE = """You identify TV episodes from short subtitle dialogue samples.

Series: {series_name}

Dialogue sample:
\"\"\"
{dialogue}
\"\"\"

Possible episodes (use only these):
{episode_list}

Respond with ONLY valid JSON:
{{"season": <int>, "episode": <int>, "title": "<official title>", "confidence": <0-100>}}
"""


def _format_episode_list(episodes: list[Episode], limit: int = 80) -> str:
    lines = []
    for ep in episodes[:limit]:
        lines.append(f"S{ep.season:02d}E{ep.episode:02d} | {ep.title}")
    if len(episodes) > limit:
        lines.append(f"... ({len(episodes) - limit} more not listed)")
    return "\n".join(lines)


def _parse_llm_json(text: str) -> dict[str, Any]:
    text = text.strip()
    # strip markdown fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


def identify_with_ollama(
    *,
    series_name: str,
    dialogue: str,
    episodes: list[Episode],
    model: str = "llama3.2",
    base_url: str = "http://127.0.0.1:11434",
    path: Path | None = None,
) -> MatchResult:
    import requests

    path = path or Path(".")
    prompt = PROMPT_TEMPLATE.format(
        series_name=series_name,
        dialogue=dialogue[:4000],
        episode_list=_format_episode_list(episodes),
    )
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={"model": model or "llama3.2", "prompt": prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json().get("response") or ""
        data = _parse_llm_json(text)
        return MatchResult(
            path=path,
            season=int(data["season"]),
            episode=int(data["episode"]),
            title=str(data.get("title") or ""),
            confidence=float(data.get("confidence") or 75),
            low_confidence=float(data.get("confidence") or 75) < 55,
            dialogue_source="llm_ollama",
            flags=["llm"],
        )
    except Exception as exc:
        return MatchResult(path=path, error=f"Ollama LLM failed: {exc}", flags=["llm_error"])


def identify_with_gemini(
    *,
    series_name: str,
    dialogue: str,
    episodes: list[Episode],
    api_key: str,
    model: str = "gemini-2.0-flash",
    path: Path | None = None,
) -> MatchResult:
    path = path or Path(".")
    prompt = PROMPT_TEMPLATE.format(
        series_name=series_name,
        dialogue=dialogue[:4000],
        episode_list=_format_episode_list(episodes),
    )
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model or "gemini-2.0-flash",
            contents=prompt,
        )
        text = getattr(response, "text", None) or str(response)
        data = _parse_llm_json(text)
        conf = float(data.get("confidence") or 80)
        return MatchResult(
            path=path,
            season=int(data["season"]),
            episode=int(data["episode"]),
            title=str(data.get("title") or ""),
            confidence=conf,
            low_confidence=conf < 55,
            dialogue_source="llm_gemini",
            flags=["llm"],
        )
    except Exception as exc:
        return MatchResult(path=path, error=f"Gemini LLM failed: {exc}", flags=["llm_error"])


def identify_with_llm(
    *,
    provider: str,
    series_name: str,
    dialogue: str,
    episodes: list[Episode],
    api_key: str | None = None,
    model: str = "",
    ollama_base_url: str = "http://127.0.0.1:11434",
    path: Path | None = None,
) -> MatchResult:
    provider = (provider or "").lower()
    if provider == "ollama":
        return identify_with_ollama(
            series_name=series_name,
            dialogue=dialogue,
            episodes=episodes,
            model=model or "llama3.2",
            base_url=ollama_base_url,
            path=path,
        )
    if provider == "gemini":
        if not api_key:
            return MatchResult(path=path or Path("."), error="Gemini API key missing")
        return identify_with_gemini(
            series_name=series_name,
            dialogue=dialogue,
            episodes=episodes,
            api_key=api_key,
            model=model or "gemini-2.0-flash",
            path=path,
        )
    return MatchResult(
        path=path or Path("."),
        error=f"LLM provider '{provider}' not implemented in this build",
        flags=["llm_error"],
    )
