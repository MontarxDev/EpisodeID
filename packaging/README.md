# Packaging EpisodeID

## AppDir / AppImage

```bash
# From repo root
./packaging/build_appimage.sh

# Optional: convert AppDir → AppImage
# Install appimagetool from https://github.com/AppImage/appimagetool
appimagetool dist/EpisodeID.AppDir dist/EpisodeID-x86_64.AppImage
```

The AppImage/AppDir bundles Python + EpisodeID + PySide6 (+ RapidOCR).  
**System packages still required on the target machine:**

```bash
sudo apt install ffmpeg mkvtoolnix tesseract-ocr
```

EpisodeID’s **Settings → Install Missing Dependencies** can run this via `pkexec` on Debian/Ubuntu/Mint.

## Run from source

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[ocr,dev]"
python -m episodeid
```

## .deb (optional / future)

A simple `.deb` can wrap the same venv under `/opt/episodeid` with a `/usr/bin/episodeid` launcher and Depends on `ffmpeg, mkvtoolnix, tesseract-ocr`. Prefer AppImage for portable distribution.
