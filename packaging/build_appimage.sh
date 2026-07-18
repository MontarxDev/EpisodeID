#!/usr/bin/env bash
# Build EpisodeID AppDir and (if possible) a portable AppImage.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="${ROOT}/dist"
APPDIR="${DIST}/EpisodeID.AppDir"
ARCH="$(uname -m)"
APPIMAGE_NAME="EpisodeID-${ARCH}.AppImage"
export PATH="${HOME}/.local/bin:${PATH}"

echo "==> Cleaning ${APPDIR}"
rm -rf "${APPDIR}"
mkdir -p \
  "${APPDIR}/usr/bin" \
  "${APPDIR}/usr/share/applications" \
  "${APPDIR}/usr/share/icons/hicolor/256x256/apps"

create_venv() {
  local target="$1"
  if command -v uv >/dev/null 2>&1; then
    echo "==> Creating venv with uv"
    uv venv --clear "${target}"
    return 0
  fi
  if "${PYTHON:-python3}" -m venv "${target}" 2>/dev/null; then
    return 0
  fi
  echo "ERROR: Could not create a virtualenv (need uv or python3-venv)." >&2
  exit 1
}

echo "==> Creating virtualenv inside AppDir"
create_venv "${APPDIR}/usr/venv"

# shellcheck disable=SC1091
source "${APPDIR}/usr/venv/bin/activate"
VENV_PY="${APPDIR}/usr/venv/bin/python"
echo "==> Installing EpisodeID + OCR extras into AppDir"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "${VENV_PY}" -U pip wheel setuptools
  uv pip install --python "${VENV_PY}" "${ROOT}[ocr]"
else
  "${VENV_PY}" -m ensurepip --upgrade 2>/dev/null || true
  "${VENV_PY}" -m pip install -U pip wheel setuptools
  "${VENV_PY}" -m pip install "${ROOT}[ocr]"
fi

cat > "${APPDIR}/usr/bin/episodeid" << 'EOF'
#!/usr/bin/env bash
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${HERE}/venv/bin:${PATH}"
# Prefer system ffmpeg/ffprobe/mkvextract/tesseract
exec "${HERE}/venv/bin/python" -m episodeid "$@"
EOF
chmod +x "${APPDIR}/usr/bin/episodeid"

cat > "${APPDIR}/AppRun" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="${HERE}/usr/bin:${HERE}/usr/venv/bin:${PATH}"
export PYTHONNOUSERSITE=1
# Qt plugins from bundled PySide6
PYSIDE_DIR="$("${HERE}/usr/venv/bin/python" -c 'import PySide6, pathlib; print(pathlib.Path(PySide6.__file__).parent)')"
if [[ -d "${PYSIDE_DIR}/Qt/plugins" ]]; then
  export QT_PLUGIN_PATH="${PYSIDE_DIR}/Qt/plugins${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
fi
if [[ -d "${PYSIDE_DIR}/Qt/lib" ]]; then
  export LD_LIBRARY_PATH="${PYSIDE_DIR}/Qt/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
exec "${HERE}/usr/venv/bin/python" -m episodeid "$@"
EOF
chmod +x "${APPDIR}/AppRun"

cat > "${APPDIR}/episodeid.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=EpisodeID
Comment=Identify and rename TV episodes from subtitles
Exec=episodeid
Icon=episodeid
Categories=AudioVideo;Video;Utility;
Terminal=false
StartupNotify=true
EOF
cp "${APPDIR}/episodeid.desktop" "${APPDIR}/usr/share/applications/episodeid.desktop"

# Icon
python - << 'PY'
from pathlib import Path
try:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (256, 256), (47, 111, 237, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((32, 32, 224, 224), fill=(255, 255, 255, 255))
    d.rectangle((100, 70, 156, 186), fill=(47, 111, 237, 255))
    d.polygon([(128, 60), (170, 120), (86, 120)], fill=(47, 111, 237, 255))
    root = Path("dist/EpisodeID.AppDir")
    icon_path = root / "usr/share/icons/hicolor/256x256/apps/episodeid.png"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(icon_path)
    img.save(root / "episodeid.png")
    print("Icon written")
except Exception as e:
    print("Icon generation skipped:", e)
PY

echo "==> AppDir ready: ${APPDIR}"
echo "    Smoke test: ${APPDIR}/AppRun --version"

# Download appimagetool if missing
TOOL="${DIST}/appimagetool-${ARCH}.AppImage"
if [[ ! -x "${TOOL}" ]]; then
  echo "==> Downloading appimagetool"
  # Official continuous build
  URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
  curl -fsSL -o "${TOOL}" "${URL}" || wget -q -O "${TOOL}" "${URL}"
  chmod +x "${TOOL}"
fi

OUT="${DIST}/${APPIMAGE_NAME}"
rm -f "${OUT}"
echo "==> Building AppImage → ${OUT}"
# Extract appimagetool if FUSE is unavailable
if ! "${TOOL}" --appimage-extract-and-run "${APPDIR}" "${OUT}" 2>"${DIST}/appimagetool.log"; then
  echo "appimagetool failed; trying extract mode..."
  (
    cd "${DIST}"
    rm -rf squashfs-root
    "${TOOL}" --appimage-extract >/dev/null 2>&1 || true
    if [[ -x squashfs-root/AppRun ]]; then
      ARCH="${ARCH}" ./squashfs-root/AppRun "${APPDIR}" "${OUT}"
    else
      cat "${DIST}/appimagetool.log" || true
      exit 1
    fi
  )
fi

chmod +x "${OUT}" 2>/dev/null || true
echo
echo "========================================"
echo " Done."
echo " AppImage: ${OUT}"
echo " Or run AppDir: ${APPDIR}/AppRun"
echo " System deps still needed: ffmpeg mkvtoolnix (tesseract optional)"
echo "========================================"
ls -lh "${OUT}" 2>/dev/null || ls -lh "${APPDIR}/AppRun"
