# EpisodeID Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working EpisodeID desktop app (PySide6) that identifies misnamed TV rips via subtitle dialogue + TMDB/RapidFuzz, previews renames, applies with season folders, and packages as AppImage.

**Architecture:** Modular `episodeid` package (extractor, metadata, matcher, renamer, config, deps, pipeline) + thin `episodeid.gui`. Free-first matching; preview mandatory before apply.

**Tech Stack:** Python 3.12+, PySide6, rapidfuzz, requests, keyring, pysrt; system ffmpeg/ffprobe/mkvtoolnix/tesseract; pytest.

**Spec:** `docs/superpowers/specs/2026-07-17-episodeid-design.md`

---

## File map

| Path | Responsibility |
|------|----------------|
| `pyproject.toml` | Package metadata, deps, entry points |
| `episodeid/models.py` | Dataclasses |
| `episodeid/textutil.py` | Dialogue cleaning / unique lines |
| `episodeid/extractor.py` | Subtitle probe + sample (SRT/text/VobSub/PGS+OCR) |
| `episodeid/metadata.py` | TMDB API + disk cache |
| `episodeid/matcher.py` | RapidFuzz matching + confidence |
| `episodeid/renamer.py` | Plan, apply, undo, export |
| `episodeid/config.py` | Settings + keyring |
| `episodeid/deps.py` | Tool check + apt install |
| `episodeid/pipeline.py` | Orchestration |
| `episodeid/llm.py` | Optional Gemini/Ollama |
| `episodeid/gui/*` | Main window, settings, workers |
| `episodeid/__main__.py` | `python -m episodeid` |
| `tests/*` | Unit tests |
| `packaging/*` | AppImage scripts |
| `README.md` | User docs |

---

### Task 1: Project scaffold + models

- [ ] Create `pyproject.toml`, package init, models, basic tests for models/sanitize helpers
- [ ] Commit

### Task 2: textutil + matcher (TDD)

- [ ] Tests for clean_dialogue, match scoring, duplicate demotion, thresholds
- [ ] Implement `textutil.py`, `matcher.py`
- [ ] Commit

### Task 3: renamer (TDD)

- [ ] Tests for format, sanitize, plan, apply, undo, collision skip
- [ ] Implement `renamer.py`
- [ ] Commit

### Task 4: config + deps

- [ ] Settings JSON + keyring wrappers; dependency check/install helpers
- [ ] Tests for config defaults and deps detection (mocked)
- [ ] Commit

### Task 5: metadata (TMDB)

- [ ] Client with search + all episodes; disk cache; tests with mocked HTTP
- [ ] Commit

### Task 6: extractor

- [ ] Probe tracks; external SRT; embedded text; VobSub/PGS OCR sample window
- [ ] Integration-friendly API; unit tests with synthetic SRT files
- [ ] Commit

### Task 7: pipeline + optional LLM stub

- [ ] `scan_and_identify` with progress callback; file size filter
- [ ] Tests with mocked extractor/metadata
- [ ] Commit

### Task 8: GUI

- [ ] Main window, settings dialog, QThread workers, preview table, apply/export/undo
- [ ] Dark/light basics
- [ ] Commit

### Task 9: README + AppImage packaging scripts

- [ ] Full README (TMDB key, Clone Wars walkthrough, build)
- [ ] packaging scripts + LICENSE
- [ ] Commit

### Task 10: End-to-end verification

- [ ] Run unit tests
- [ ] Smoke extract+match on Clone Wars sample if tools+TMDB available
- [ ] Fix issues

---

## Execution note

User requested immediate implementation. Execute tasks inline in order with TDD for pure logic modules; GUI verified manually/smoke.
