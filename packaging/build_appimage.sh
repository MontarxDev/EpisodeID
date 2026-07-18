#!/usr/bin/env bash
# Build a basic AppImage-style distributable directory + optional AppImage.
# Requires: python3, appimagetool (optional), linuxdeploy (optional).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="${ROOT}/dist"
APPDIR="${DIST}/EpisodeID.AppDir"
PYTHON="${PYTHON:-python3}"

echo "==> Cleaning ${APPDIR}"
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin" "${APPDIR}/usr/lib" "${APPDIR}/usr/share/applications" "${APPDIR}/usr/share/icons/hicolor/256x256/apps"

echo "==> Creating virtualenv inside AppDir"
"${PYTHON}" -m venv "${APPDIR}/usr/venv" || {
  echo "python -m venv failed. On Debian/Ubuntu: sudo apt install python3-venv"
  # fallback: use uv if present
  if command -v uv >/dev/null; then
    uv venv "${APPDIR}/usr/venv"
  else
    exit 1
  fi
}

# shellcheck disable=SC1091
source "${APPDIR}/usr/venv/bin/activate"
pip install -U pip wheel
pip install "${ROOT}[ocr]"

cat > "${APPDIR}/usr/bin/episodeid" << 'EOF'
#!/usr/bin/env bash
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${HERE}/venv/bin:${PATH}"
exec "${HERE}/venv/bin/python" -m episodeid "$@"
EOF
chmod +x "${APPDIR}/usr/bin/episodeid"

cat > "${APPDIR}/AppRun" << 'EOF'
#!/usr/bin/env bash
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="${HERE}/usr/bin:${HERE}/usr/venv/bin:${PATH}"
exec "${HERE}/usr/venv/bin/python" -m episodeid "$@"
EOF
chmod +x "${APPDIR}/AppRun"

cat > "${APPDIR}/EpisodeID.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=EpisodeID
Comment=Identify and rename TV episodes from subtitles
Exec=episodeid
Icon=episodeid
Categories=AudioVideo;Video;Utility;
Terminal=false
EOF
cp "${APPDIR}/EpisodeID.desktop" "${APPDIR}/usr/share/applications/"

# Simple placeholder PNG icon (1x1) if no icon provided
python - << 'PY'
from pathlib import Path
try:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (256, 256), (47, 111, 237, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((40, 40, 216, 216), fill=(255, 255, 255, 255))
    d.text((88, 100), "EID", fill=(47, 111, 237, 255))
    out = Path("dist/EpisodeID.AppDir/usr/share/icons/hicolor/256x256/apps/episodeid.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    img.save(Path("dist/EpisodeID.AppDir/episodeid.png"))
except Exception as e:
    print("Icon generation skipped:", e)
PY

echo "==> AppDir ready at ${APPDIR}"
echo "    Run with: ${APPDIR}/AppRun"
echo
echo "To pack as AppImage (if appimagetool installed):"
echo "  appimagetool ${APPDIR} ${DIST}/EpisodeID-x86_64.AppImage"
echo
echo "Note: ffmpeg, mkvtoolnix, and tesseract remain system dependencies."
