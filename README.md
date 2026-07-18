# EpisodeID

**Identify and rename TV episode files from subtitle dialogue — free, local-first, Linux desktop.**

EpisodeID helps fix poorly named DVD/Blu-ray rips (e.g. `D1_t01.mkv`) by:

1. Extracting dialogue from embedded or external subtitles  
2. Matching that dialogue against **TMDB** episode titles/plots with **RapidFuzz**  
3. Showing a full **preview table** with confidence scores  
4. Renaming only after you approve — optionally into `Season 01/` folders  

Primary target: **Linux Mint / Ubuntu / Debian**. License: **MIT**.

---

## Features

- **Free matching path** — TMDB API key + local fuzzy matching (no paid LLM required)
- **Image subtitles** — VobSub/PGS via ffmpeg overlay + OCR (`tesseract` or bundled RapidOCR)
- **Text subtitles** — SRT/ASS embedded tracks and external `.srt` sidecars
- **Mandatory preview** — original → SxxExx + title + confidence + proposed name
- **Season folders** — e.g. `Season 01/Star Wars The Clone Wars - S01E01 - Ambush.mkv`
- **Secure keys** — API keys stored with the OS **keyring**
- **Dependency helper** — one-click `apt` install of ffmpeg / mkvtoolnix / tesseract
- **Undo log** — restore previous filenames after an apply
- **Export** — CSV/JSON reports
- **Optional LLM** — Gemini or local Ollama for low-confidence cases (text samples only)

---

## Privacy

- All video processing is **local**
- Network use: **TMDB** (metadata) and optional LLM APIs you enable
- **Never** uploads video files
- No telemetry

---

## Install (from source)

### System packages

```bash
sudo apt install ffmpeg mkvtoolnix tesseract-ocr libxcb-cursor0
# Optional build helpers:
# sudo apt install python3-venv python3-pip
```

`libxcb-cursor0` is required for the Qt GUI on X11 (Ubuntu/Mint). The AppImage
bundles this library; source installs need the system package.

### Python app

```bash
git clone <this-repo> EpisodeID
cd EpisodeID
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[ocr,dev]"
python -m episodeid
```

### AppImage

```bash
./packaging/build_appimage.sh
./dist/EpisodeID.AppDir/AppRun
# or pack with appimagetool — see packaging/README.md
```

---

## Free TMDB API key

1. Create an account at [themoviedb.org](https://www.themoviedb.org/)  
2. Settings → API → request an API key (Developer)  
3. In EpisodeID: **Settings → TMDB → paste key → Test connection → Save**

Keys are stored via the system keyring (not in plain settings JSON when keyring works).

---

## Example: Star Wars: The Clone Wars (Season 1 disc dump)

Your folder might look like:

```text
STAR_WARS_CLONE_WARS_S1_D1/
  C1_t00.mkv
  D1_t01.mkv
  D2_t02.mkv
  …
```

1. Open EpisodeID  
2. Add TMDB API key in Settings  
3. Search **Clone Wars** → select **Star Wars: The Clone Wars (2008)**  
4. Browse to the folder  
5. Enable **Organize into Season XX folders**  
6. Click **Scan & Identify** (image subs need OCR; first run may download OCR models)  
7. Review the table — green/yellow/red confidence  
8. Edit any wrong SxxExx manually if needed  
9. **Apply Selected Renames**  

Result example:

```text
Season 01/Star Wars The Clone Wars - S01E01 - Ambush.mkv
```

**Tip:** Very small files (menus/extras) are skipped by default via a size filter. Huge multi-title files (e.g. full-disc `C1_t00.mkv`) may need manual handling.

---

## Matching strategy (no LLM required)

Free path (layered for accuracy):

1. **Extract** dialogue from the MKV (text track or OCR of eng image subs)
2. **TMDB** episode titles + plots (your free API key; cached)
3. **TVMaze** free plot enrichment (no key) — merges extra summaries
4. **Reference SRTs** (recommended) — optional free [Wyzie](https://store.wyzie.io/redeem) key downloads English subtitle samples; your dialogue is compared to real episode text (cached under `~/.cache/episodeid/refsubs/`)
5. Score, unique-assign SxxExx across the folder, preview before rename

| Mode | When |
|------|------|
| TMDB + TVMaze plots | Always available free (TMDB key for catalog) |
| **Reference SRT match** | Settings → Accuracy → Wyzie free key (best free accuracy) |
| Optional LLM | Settings → Gemini / Ollama if still stuck |

**Tips for disc rips & big libraries:**

- **Include subfolders** (default on) finds videos in nested Season / disc folders  
- Settings → Accuracy: TVMaze + reference subs; free Wyzie key; policy **Use cache, download missing**  
- Settings → **Cache**: durable path (survives reboot), stats, clear TMDB / TVMaze / ref subs  
- **Match season: Season 01 only** then first scan caches that season’s SRTs (later scans = cache hits only)  
- **Retry problem rows** re-does only dupes / low-confidence / failed rows  
- Hover a row for dialogue sample + OCR quality  
- Scan logs: `~/.local/share/episodeid/scans/`  
- Durable cache default: `~/.local/share/episodeid/cache/`

Confidence bands (defaults):

- **≥ 70%** high (green, selected)  
- **55–69%** review (yellow, selected)  
- **&lt; 55%** low (red, unselected)  
- Poor OCR quality refuses a match instead of guessing

---

## Rename format

Default:

```text
{series} - S{season:02d}E{episode:02d} - {title}{ext}
```

---

## Development

```bash
source .venv/bin/activate
pytest -q
python -m episodeid --cli-check
```

Architecture overview: `docs/superpowers/specs/2026-07-17-episodeid-design.md`

---

## Inspiration

Subtitle extraction ideas inspired by the excellent open-source [tvidentify](https://github.com/ram-nat/tvidentify) (MIT). EpisodeID reimplements a free-first matcher, GUI preview workflow, and secure desktop packaging.

---

## Contributing

Issues and PRs welcome. Please:

- Keep the free/local path working without cloud LLMs  
- Never add telemetry  
- Prefer tests for matcher/renamer/text cleaning  

---

## License

MIT — see [LICENSE](LICENSE).
