# EpisodeID — Design Specification

**Date:** 2026-07-17  
**Status:** Ready for user review (self-reviewed)  
**License:** MIT  
**Primary target:** Linux Mint / Ubuntu / Debian  

## 1. Product summary

**EpisodeID** is a free, local-first Linux desktop app that identifies TV episode files from poorly named rips (especially DVD/Blu-ray dumps) by extracting dialogue from embedded or external subtitles, matching that dialogue against TMDB episode metadata, and renaming/organizing files only after an explicit preview and user approval.

**Primary test case:** `STAR_WARS_CLONE_WARS_S1_D1/` — Star Wars: The Clone Wars Season 1 disc 1 dumps with names like `D1_t01.mkv`, `C1_t00.mkv`.

### Goals

- Correct Season + Episode + official title identification without requiring cloud LLMs.
- Mandatory preview table with confidence scores before any renames.
- Secure API key storage; no telemetry; video never leaves the machine.
- Distributable as AppImage; system dependency helper for Mint/Ubuntu/Debian.
- General enough for any TV show with TMDB data and extractable subtitles.

### Non-goals (v1)

- Reference subtitle download marketplaces (Wyzie/SubDL/OpenSubtitles) as a required path.
- Local sentence-transformer embeddings as default.
- Multi-series batch sessions, poster browsers, or rich media libraries.
- Hardcoded/burned-in subtitle OCR from video frames (only subtitle *tracks*).
- Windows/macOS first-class packaging (Linux primary).

---

## 2. Decisions

| Topic | Decision |
|-------|----------|
| Product name | **EpisodeID** |
| License | **MIT** |
| Default matching | **TMDB + RapidFuzz** (free/local after key entry) |
| v1 scope | **Solid MVP + AppImage** |
| Architecture | **Modular core library + thin PySide6 GUI** (Approach B) |
| LLM | Optional, settings-gated; not required for happy path |
| Rename default | `{series} - S{season:02d}E{episode:02d} - {title}{ext}` |

Inspiration: extraction and batch ideas from [tvidentify](https://github.com/ram-nat/tvidentify) (MIT). EpisodeID reimplements boundaries for free matching, GUI preview, and security—does not vendor tvidentify as a dependency.

---

## 3. Architecture

### 3.1 Package layout

```
episodeid/
  __init__.py
  models.py          # shared dataclasses
  extractor.py       # ffprobe/ffmpeg/mkvextract + SRT/PGS/VobSub + OCR
  metadata.py        # TMDB client + disk cache
  matcher.py         # RapidFuzz primary; optional LLM hook
  renamer.py         # plan, apply, season folders, undo log
  config.py          # settings + keyring secrets
  deps.py            # dependency check + apt install helper
  pipeline.py        # Scan & Identify orchestration
  gui/
    main_window.py
    settings_dialog.py
    workers.py       # QThread wrappers
    styles.py        # light/dark helpers
tests/
packaging/           # AppImage build scripts
docs/
README.md
pyproject.toml
LICENSE
```

### 3.2 Module responsibilities

| Module | Responsibility | Must not |
|--------|----------------|----------|
| **extractor** | Probe tracks; sample dialogue (offset/duration/max lines); text extract; image OCR | Network; matching |
| **metadata** | TMDB search + episode lists; cache | Touch video files |
| **matcher** | Score sample vs titles/overviews; confidence; duplicate demotion | Rename files |
| **renamer** | Build plan; apply moves/renames; undo log | Identify episodes |
| **config** | Prefs JSON + keyring for secrets | Store API keys in plaintext JSON |
| **pipeline** | Folder scan → extract → match → plan; progress callbacks | Import Qt |
| **gui** | Search, folder pick, table, settings, confirm/apply | Embed ffmpeg/TMDB business logic |
| **deps** | Detect tools; offer `pkexec apt-get install` | Store passwords |

### 3.3 Design rules

1. Core is GUI-independent (headless tests and optional CLI entrypoint).
2. Preview is mandatory: pipeline returns plans only; Apply mutates filesystem.
3. Free path is default: TMDB + RapidFuzz.
4. Privacy: video stays local; network only for TMDB (and optional LLM when enabled).
5. Per-file failures do not abort the batch.

### 3.4 System dependencies

| Tool | Role | Required when |
|------|------|----------------|
| `ffmpeg` / `ffprobe` | Probe + extract text/image subs | Always for identification |
| `mkvextract` / `mkvmerge` | VobSub/PGS extraction from MKV | Image-based tracks |
| `tesseract` | OCR on subtitle bitmaps | Image-based tracks only |

Python (v1): PySide6, rapidfuzz, requests, pysrt (or equivalent SRT parse), keyring; optional google-genai / openai / ollama for LLM mode.

---

## 4. Data flow

### 4.1 Happy path

```
User sets series (TMDB search) + folder + options
        │
        ▼
pipeline.scan_and_identify()
  ├─ deps.check()                          # advisory unless extract fails
  ├─ metadata.get_all_episodes(series_id)  # disk-cached
  └─ for each candidate video:
       extractor.sample_dialogue(...)
       matcher.match(sample, episodes)
       renamer.build_plan_row(...)
        │
        ▼
GUI preview table (user edits / selects)
        │
        ▼  Apply Selected + confirmation
renamer.apply() + undo log
```

### 4.2 Data structures

```python
@dataclass
class Episode:
    season: int
    episode: int
    title: str
    overview: str

@dataclass
class DialogueSample:
    lines: list[str]
    source: str   # external_srt | embedded_text | ocr_pgs | ocr_vobsub | none
    raw_text: str

@dataclass
class MatchResult:
    path: Path
    season: int | None
    episode: int | None
    title: str | None
    confidence: float          # 0–100
    low_confidence: bool
    candidates: list           # top-3 alternates
    error: str | None
    dialogue_source: str | None

@dataclass
class RenamePlanRow:
    path: Path
    original_name: str
    season: int | None
    episode: int | None
    official_title: str
    confidence: float
    proposed_name: str
    target_dir: Path
    selected: bool
    move_to_season: bool
    error: str | None
```

### 4.3 Extraction strategy

1. If sidecar `.srt` / `.ass` exists next to video → use it (prefer language match when multi).
2. Else `ffprobe` subtitle streams:
   - Prefer text codecs (`subrip`, `ass`, `ssa`, `webvtt`) with language eng → und → first.
   - Else image codecs (`hdmv_pgs_subtitle`, `dvd_subtitle` / VobSub) → extract window + OCR.
3. Sample window (defaults, user-configurable):
   - `offset_minutes`: 0 (or small skip if needed for cold opens)
   - `scan_duration_minutes`: 10
   - `max_lines`: 40 unique cleaned lines
4. Cleaning: strip ASS/HTML tags, collapse whitespace, drop empty/near-duplicate lines, optional strip `[SDH]`-style brackets.

### 4.4 Matching strategy (primary)

For each episode, build corpus strings:

- `title`
- `f"{title}. {overview}"`

Scores (RapidFuzz):

- `token_set_ratio(dialogue, title_overview)`
- `partial_ratio(dialogue, title_overview)`
- `token_set_ratio(dialogue, title)` with a small boost when title tokens appear in dialogue

**Confidence** = best score clamped to 0–100.

**Thresholds (defaults):**

| Band | Range | UI behavior |
|------|-------|-------------|
| High | ≥ 70 | Selected; green |
| Medium | 55–69 | Selected; yellow “review” |
| Low | &lt; 55 | Unselected; red |
| Error / no subs | — | Unselected; error message |

**Duplicate SxxExx claims:** keep highest confidence selected; demote others with flag `duplicate_claim`.

**Optional LLM (v1 secondary):** if enabled and key present, may run when confidence &lt; threshold or as explicit mode. Sends only short dialogue sample + series name—never video. Providers: Gemini (preferred free tier), OpenAI, Ollama, xAI/Grok if API available.

### 4.5 File discovery

- Extensions: `.mkv`, `.mp4`, `.m4v`, `.avi`, `.ts`, `.m2ts` (configurable).
- Optional size filter (tvidentify-inspired): treat files much smaller than median as extras/menus (default threshold ~0.25× median, not only 0.7× largest—Clone Wars disc dumps include a multi-GB `C1_t00.mkv` and smaller extras). User can disable filter.
- Process largest-first or alphabetical; show skipped extras in status, not as forced rows (or rows marked skipped).

### 4.6 Rename & organize

- Default format: `{series} - S{season:02d}E{episode:02d} - {title}{ext}`
- Sanitize filename: remove/replace `: * ? " < > | / \`, collapse spaces, trim.
- Season folders: create `Season {season:02d}` **inside the user-selected scan directory** (e.g. `.../STAR_WARS_CLONE_WARS_S1_D1/Season 01/`).
- Collisions: skip with error on that row; do not overwrite without explicit future setting.
- Undo log: `~/.local/share/episodeid/undo/{timestamp}.json` mapping new path → original path.

### 4.7 Caching & paths

| Data | Location |
|------|----------|
| TMDB episode cache | `~/.cache/episodeid/tmdb/{series_id}.json` |
| Non-secret prefs | `~/.config/episodeid/settings.json` |
| API keys | OS keyring service `episodeid` |
| Undo logs | `~/.local/share/episodeid/undo/` |
| Temp extract/OCR | `tempfile` under system temp; cleaned after each file |

### 4.8 Errors

- Per-file extract/match failure → row error; continue batch.
- TMDB 401 → stop with “invalid API key”.
- TMDB 429 → stop with rate-limit guidance.
- Missing ffmpeg → actionable status + Install Dependencies.
- Missing tesseract only when image subs required → row/file-specific message.

---

## 5. User interface (PySide6)

### 5.1 Main window

1. **Header:** EpisodeID, Settings, theme toggle (system / light / dark).
2. **Inputs:** series search + TMDB results picker; folder browse; organize-into-season checkbox; rename format; **Scan & Identify**.
3. **Progress:** bar + phase label + Cancel (cooperative between files).
4. **Preview table (required):**  
   Select | Original | SxxExx | Official Title | Confidence % | Proposed name | (optional Target)  
   - Inline edit SxxExx / title / proposed name.  
   - Row colors by confidence band.  
   - Tooltip: dialogue source + top alternate candidates.
5. **Footer:** Export CSV/JSON; Undo last apply; **Apply Selected Renames** (confirmation dialog).
6. **Status bar:** dependency summary; last message.

### 5.2 Settings

- TMDB API key (keyring) + Test connection + link to free key instructions.
- Matching thresholds, offset, scan duration, max lines, size filter.
- Rename defaults; skip-already-named.
- Optional LLM provider/keys (secondary).
- **Install Missing Dependencies** (`pkexec apt-get install -y ffmpeg mkvtoolnix tesseract-ocr`).
- Clear TMDB cache; open config/cache dirs.

### 5.3 Workers

All scan/extract/match/apply work runs on `QThread` with Qt signals for progress, row results, finished, and errors. UI thread never runs ffmpeg.

### 5.4 Clone Wars acceptance UX

1. Enter free TMDB key once in Settings.  
2. Search “Clone Wars” → select **Star Wars: The Clone Wars (2008)**.  
3. Point at `STAR_WARS_CLONE_WARS_S1_D1`.  
4. Scan → preview S01Exx + titles + confidence.  
5. Fix any low-confidence rows → Apply → names like  
   `Star Wars The Clone Wars - S01E01 - Ambush.mkv`  
   optionally under `Season 01/`.

---

## 6. Packaging, security, testing

### 6.1 Packaging (v1)

**Primary artifact:** single **AppImage** for x86_64 Linux.

- Build approach: bundle a Python venv (EpisodeID + PySide6 + pure-Python deps) with **linuxdeploy + appimagetool** (or `python-appimage` if linuxdeploy proves painful). Pin the winning recipe in `packaging/README.md` once the first successful build lands.
- AppImage does **not** bundle ffmpeg/mkvtoolnix/tesseract (size + licensing; system packages are preferred). Runtime `deps.check()` + Install Dependencies button remain essential.
- Entry point: `episodeid` / `python -m episodeid`.
- Desktop integration metadata: `EpisodeID.desktop`, icon under `packaging/`.
- Optional stretch: `.deb` build notes (not required if AppImage works).

### 6.2 Security & privacy

| Rule | Implementation |
|------|----------------|
| No telemetry | No analytics libraries or phone-home |
| Keys secure | `keyring` only; never log key values |
| Minimal network | TMDB only by default; LLM opt-in |
| No video upload | Extractors are local subprocess only |
| Clear opt-in | Settings toggles for optional cloud features |
| Privilege | `pkexec` only for apt install of known package names; no arbitrary shell from user-controlled strings without validation |
| Path safety | Resolve renames under intended parent; refuse path escape |

### 6.3 Testing strategy

| Layer | What |
|-------|------|
| Unit | Cleaner, matcher scoring on fixture dialogue vs synthetic episodes, rename format sanitizer, duplicate demotion |
| Integration | extractor against real Clone Wars MKV if tools present (marked optional/slow); TMDB client mocked |
| Manual | Full GUI Clone Wars path on developer machine |
| Smoke | `pipeline` dry-run on folder with mocked metadata if no API key in CI |

**Success criteria (Clone Wars disc folder):**

- Majority of true episode files identified to correct S01Exx with confidence ≥ 55 when subtitles exist and TMDB key works.
- Zero renames without Apply.
- Undo restores original names after a test apply (on copies preferred).

**Note on test corpus sizes:** `C1_t00.mkv` is multi-GB (likely full/feature title); `D1`–`D5` are episode-sized; `D6`–`D10` are smaller. Size filter and manual selection must not force junk titles into season packs.

### 6.4 Documentation (v1 deliverable)

README must include:

- What EpisodeID does / privacy model  
- Install AppImage + system deps  
- How to get a free TMDB API key  
- Clone Wars walkthrough  
- Build from source  
- Contribution guide (short)  
- License (MIT)

---

## 7. v1 delivery cut line

### In scope

- [x] Modular core: extractor, metadata, matcher (RapidFuzz), renamer, config, deps, pipeline  
- [x] PySide6 GUI with mandatory preview table, settings, progress, apply + confirm  
- [x] Season folder organization + default clean names  
- [x] keyring for TMDB (+ optional LLM keys)  
- [x] Dependency checker + pkexec apt installer  
- [x] TMDB disk cache  
- [x] Export CSV/JSON report  
- [x] Undo log for last apply  
- [x] Dark/light mode  
- [x] AppImage packaging scripts + working build instructions  
- [x] Polished README  
- [x] Tests for matcher/renamer/cleaning; optional extract integration  
- [x] Optional LLM hook: UI toggle + settings fields; implement **Ollama and/or Gemini** as best-effort (not a Clone Wars acceptance blocker if fuzzy matching succeeds)

### Explicitly deferred (post-v1)

- Wyzie / SubDL / OpenSubtitles reference SRT download  
- sentence-transformers semantic embeddings  
- Full multi-provider LLM parity (all of Gemini, OpenAI, Perplexity, Grok) if not finished  
- Multi-episode single-file disc splitting  
- Hardcoded subtitle OCR  
- First-class .deb package  
- Non-Linux platforms  

---

## 8. Implementation phases (for planning skill)

1. **Scaffold** — pyproject, package skeleton, models, config/keyring.  
2. **Extractor** — probe + text + OCR paths; unit/integration on Clone Wars samples.  
3. **Metadata + matcher** — TMDB + RapidFuzz; fixtures; Clone Wars dialogue fixtures if extract works offline.  
4. **Renamer + pipeline** — plan/apply/undo/export.  
5. **GUI** — main window, workers, settings, preview table.  
6. **Deps installer + polish** — errors, thresholds, theme.  
7. **AppImage + README** — package and document.  
8. **End-to-end Clone Wars verification** — real folder, real renames on copies.

---

## 9. Open implementation details (resolved defaults)

These are fixed for v1 unless the user changes them during review:

| Detail | Default |
|--------|---------|
| Series name sanitization in filenames | Remove `:` and illegal chars; keep words (e.g. `Star Wars The Clone Wars`) |
| Season folder name | `Season 01`, `Season 02`, … |
| Already-named detection | Regex for `S\d{2}E\d{2}` in basename when “skip already named” on |
| Language preference | eng, then und/missing, then first track |
| Concurrent extract | Sequential v1 (simpler, less disk thrash on huge MKVs) |
| App ID / keyring service | `episodeid` |
| Qt binding | PySide6 only |

---

## 10. Approval record

| Section | Status |
|---------|--------|
| § Architecture | Approved |
| § Data flow | Approved |
| § GUI | Approved |
| § Packaging, security, testing | Approved |
| Full written spec | **Pending user review** |
