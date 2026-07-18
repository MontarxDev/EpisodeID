#!/usr/bin/env bash
# Build EpisodeID AppDir and a portable AppImage.
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
  "${APPDIR}/usr/lib" \
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

copy_lib_tree() {
  # Copy a .so and follow basic soname symlinks into AppDir/usr/lib
  local src="$1"
  local dest_dir="${APPDIR}/usr/lib"
  [[ -e "${src}" ]] || return 1
  local real
  real="$(readlink -f "${src}")"
  local base
  base="$(basename "${real}")"
  cp -a "${real}" "${dest_dir}/${base}"
  # Also copy the original path name if different (symlink name)
  local name
  name="$(basename "${src}")"
  if [[ "${name}" != "${base}" ]]; then
    ln -sfn "${base}" "${dest_dir}/${name}"
  fi
  # Common soname variants sitting next to the file
  local dir
  dir="$(dirname "${real}")"
  local stem="${base%%.so*}"
  for f in "${dir}/${stem}.so"*; do
    [[ -e "${f}" ]] || continue
    local bn
    bn="$(basename "${f}")"
    if [[ -L "${f}" ]]; then
      local target
      target="$(readlink "${f}")"
      ln -sfn "${target}" "${dest_dir}/${bn}"
    else
      cp -an "${f}" "${dest_dir}/${bn}" 2>/dev/null || true
    fi
  done
  return 0
}

bundle_apt_package_libs() {
  # Download .deb without root and extract matching .so files
  local pkg="$1"
  shift
  local patterns=("$@")
  local tmp
  tmp="$(mktemp -d)"
  echo "==> Fetching ${pkg} for bundling"
  (
    cd "${tmp}"
    if ! apt-get download "${pkg}" >/dev/null 2>&1; then
      echo "WARNING: apt-get download ${pkg} failed" >&2
      rm -rf "${tmp}"
      return 1
    fi
    local deb
    deb="$(ls ./*.deb 2>/dev/null | head -1)"
    [[ -n "${deb}" ]] || return 1
    dpkg-deb -x "${deb}" extracted
  )
  local found=0
  for pattern in "${patterns[@]}"; do
    while IFS= read -r -d '' f; do
      copy_lib_tree "${f}" && found=1 || true
      echo "    bundled $(basename "${f}") from ${pkg}"
    done < <(find "${tmp}/extracted" -type f \( -name "${pattern}" -o -name "${pattern}.*" \) -print0 2>/dev/null)
  done
  rm -rf "${tmp}"
  [[ "${found}" -eq 1 ]]
}

bundle_system_or_apt_lib() {
  local soname="$1"
  local apt_pkg="$2"
  # Prefer already-installed system lib
  local path
  path="$(ldconfig -p 2>/dev/null | awk -v n="${soname}" '$1 == n {print $NF; exit}')"
  if [[ -n "${path}" && -e "${path}" ]]; then
    copy_lib_tree "${path}"
    echo "    bundled ${soname} from system (${path})"
    return 0
  fi
  # Search common multiarch paths
  for candidate in \
    "/usr/lib/${ARCH}-linux-gnu/${soname}" \
    "/lib/${ARCH}-linux-gnu/${soname}" \
    "/usr/lib/x86_64-linux-gnu/${soname}" \
    "/lib/x86_64-linux-gnu/${soname}"; do
    if [[ -e "${candidate}" ]]; then
      copy_lib_tree "${candidate}"
      echo "    bundled ${soname} from ${candidate}"
      return 0
    fi
  done
  bundle_apt_package_libs "${apt_pkg}" "${soname}"
}

bundle_missing_from_ldd() {
  # After PySide is installed, ldd platform plugin and copy anything "not found"
  local pyside_dir
  pyside_dir="$("${VENV_PY}" -c 'import PySide6, pathlib; print(pathlib.Path(PySide6.__file__).parent)')"
  local qxcb="${pyside_dir}/Qt/plugins/platforms/libqxcb.so"
  [[ -f "${qxcb}" ]] || return 0
  # Ensure our usr/lib is visible when resolving later
  export LD_LIBRARY_PATH="${APPDIR}/usr/lib:${pyside_dir}/Qt/lib:${LD_LIBRARY_PATH:-}"
  local missing
  missing="$(ldd "${qxcb}" 2>/dev/null | awk '/not found/ {print $1}' || true)"
  if [[ -z "${missing}" ]]; then
    echo "==> libqxcb deps OK (no missing libs after bundling)"
    return 0
  fi
  echo "==> Still missing after first pass:"
  echo "${missing}"
  # Map common sonames to apt packages
  while read -r soname; do
    [[ -n "${soname}" ]] || continue
    case "${soname}" in
      libxcb-cursor.so.0) bundle_system_or_apt_lib "${soname}" libxcb-cursor0 || true ;;
      libxcb-icccm.so.4) bundle_system_or_apt_lib "${soname}" libxcb-icccm4 || true ;;
      libxcb-image.so.0) bundle_system_or_apt_lib "${soname}" libxcb-image0 || true ;;
      libxcb-keysyms.so.1) bundle_system_or_apt_lib "${soname}" libxcb-keysyms1 || true ;;
      libxcb-render-util.so.0) bundle_system_or_apt_lib "${soname}" libxcb-render-util0 || true ;;
      libxcb-util.so.1) bundle_system_or_apt_lib "${soname}" libxcb-util1 || true ;;
      libxkbcommon-x11.so.0) bundle_system_or_apt_lib "${soname}" libxkbcommon-x11-0 || true ;;
      libxkbcommon.so.0) bundle_system_or_apt_lib "${soname}" libxkbcommon0 || true ;;
      *)
        # Best-effort: find on system
        path="$(ldconfig -p 2>/dev/null | awk -v n="${soname}" '$1 == n {print $NF; exit}')"
        if [[ -n "${path}" ]]; then
          copy_lib_tree "${path}" || true
          echo "    bundled ${soname} from system"
        else
          echo "    WARNING: could not bundle ${soname}" >&2
        fi
        ;;
    esac
  done <<< "${missing}"
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

echo "==> Bundling X11/Qt platform libraries (fixes silent xcb crash)"
# Critical since Qt 6.5 — often missing on minimal Mint/Ubuntu
bundle_system_or_apt_lib "libxcb-cursor.so.0" "libxcb-cursor0" || {
  echo "ERROR: failed to obtain libxcb-cursor.so.0 — AppImage would not start on X11" >&2
  exit 1
}
# Common companions for qxcb on various desktops
for pair in \
  "libxcb-icccm.so.4:libxcb-icccm4" \
  "libxcb-image.so.0:libxcb-image0" \
  "libxcb-keysyms.so.1:libxcb-keysyms1" \
  "libxcb-render-util.so.0:libxcb-render-util0" \
  "libxcb-util.so.1:libxcb-util1"; do
  soname="${pair%%:*}"
  pkg="${pair##*:}"
  bundle_system_or_apt_lib "${soname}" "${pkg}" || true
done
bundle_missing_from_ldd

cat > "${APPDIR}/usr/bin/episodeid" << 'EOF'
#!/usr/bin/env bash
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${HERE}/venv/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/lib:${LD_LIBRARY_PATH:-}"
exec "${HERE}/venv/bin/python" -m episodeid "$@"
EOF
chmod +x "${APPDIR}/usr/bin/episodeid"

cat > "${APPDIR}/AppRun" << 'EOF'
#!/usr/bin/env bash
# EpisodeID AppImage launcher
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="${HERE}/usr/bin:${HERE}/usr/venv/bin:${PATH}"
export PYTHONNOUSERSITE=1

LOG_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/episodeid"
LOG_FILE="${LOG_DIR}/last-run.log"
mkdir -p "${LOG_DIR}" 2>/dev/null || true

PYSIDE_DIR="$("${HERE}/usr/venv/bin/python" -c 'import PySide6, pathlib; print(pathlib.Path(PySide6.__file__).parent)' 2>/dev/null || true)"

# Bundled extra libs (libxcb-cursor etc.) MUST come first
export LD_LIBRARY_PATH="${HERE}/usr/lib${PYSIDE_DIR:+:${PYSIDE_DIR}/Qt/lib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
if [[ -n "${PYSIDE_DIR}" && -d "${PYSIDE_DIR}/Qt/plugins" ]]; then
  export QT_PLUGIN_PATH="${PYSIDE_DIR}/Qt/plugins${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
fi
# Prefer native platform; allow override via env
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-}"

show_error() {
  local msg="$1"
  echo "${msg}" >&2
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="EpisodeID" --width=420 --text="${msg}" 2>/dev/null || true
  elif command -v kdialog >/dev/null 2>&1; then
    kdialog --error "${msg}" 2>/dev/null || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send -u critical "EpisodeID failed" "${msg}" 2>/dev/null || true
  fi
}

# CLI-only flags: skip GUI crash handling noise
case "${1:-}" in
  --version|--cli-check|-h|--help)
    exec "${HERE}/usr/venv/bin/python" -m episodeid "$@"
    ;;
esac

set +e
"${HERE}/usr/venv/bin/python" -m episodeid "$@" > >(tee -a "${LOG_FILE}") 2> >(tee -a "${LOG_FILE}" >&2)
rc=$?
set -e

if [[ "${rc}" -ne 0 ]]; then
  msg="EpisodeID failed to start (exit ${rc}).

Often missing: libxcb-cursor0 (Qt X11).
This AppImage should bundle it; if you still see this, open a terminal and run:

  ${HERE}/AppRun

Log: ${LOG_FILE}"
  # Only pop dialog for likely GUI failures (no args / desktop launch)
  if [[ $# -eq 0 ]]; then
    show_error "${msg}"
  fi
fi
exit "${rc}"
EOF
chmod +x "${APPDIR}/AppRun"

cat > "${APPDIR}/episodeid.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=EpisodeID
Comment=Identify and rename TV episodes from subtitles
Exec=episodeid
Icon=episodeid
Categories=AudioVideo;Video;
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

echo "==> Verifying libqxcb linkage"
PYSIDE_DIR="$("${VENV_PY}" -c 'import PySide6, pathlib; print(pathlib.Path(PySide6.__file__).parent)')"
export LD_LIBRARY_PATH="${APPDIR}/usr/lib:${PYSIDE_DIR}/Qt/lib:${LD_LIBRARY_PATH:-}"
if ldd "${PYSIDE_DIR}/Qt/plugins/platforms/libqxcb.so" 2>/dev/null | grep -q "not found"; then
  echo "WARNING: still missing libs:" >&2
  ldd "${PYSIDE_DIR}/Qt/plugins/platforms/libqxcb.so" 2>/dev/null | grep "not found" || true
else
  echo "    libqxcb: all dynamic deps resolved"
fi
ls -la "${APPDIR}/usr/lib" | head -30

echo "==> AppDir ready: ${APPDIR}"
echo "    Smoke test: ${APPDIR}/AppRun --version"
"${APPDIR}/AppRun" --version

# Download appimagetool if missing
TOOL="${DIST}/appimagetool-${ARCH}.AppImage"
if [[ ! -x "${TOOL}" ]]; then
  echo "==> Downloading appimagetool"
  URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
  curl -fsSL -o "${TOOL}" "${URL}" || wget -q -O "${TOOL}" "${URL}"
  chmod +x "${TOOL}"
fi

OUT="${DIST}/${APPIMAGE_NAME}"
rm -f "${OUT}"
echo "==> Building AppImage → ${OUT}"
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
