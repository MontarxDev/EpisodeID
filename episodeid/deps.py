"""System dependency detection and install helpers."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


REQUIRED_CORE = ("ffmpeg", "ffprobe")
OPTIONAL_TOOLS = {
    "mkvextract": "mkvtoolnix",
    "mkvmerge": "mkvtoolnix",
    "tesseract": "tesseract-ocr",
}

# libxcb-cursor0 is required by Qt 6.5+ xcb platform (source installs / non-bundled runs)
APT_PACKAGES = ("ffmpeg", "mkvtoolnix", "tesseract-ocr", "libxcb-cursor0")


@dataclass
class ToolStatus:
    name: str
    path: str | None
    required: bool

    @property
    def ok(self) -> bool:
        return bool(self.path)


def which(name: str) -> str | None:
    return shutil.which(name)


def check_tools() -> list[ToolStatus]:
    results = [
        ToolStatus(name=n, path=which(n), required=True) for n in REQUIRED_CORE
    ]
    for name in OPTIONAL_TOOLS:
        results.append(ToolStatus(name=name, path=which(name), required=False))
    return results


def missing_required() -> list[str]:
    return [t.name for t in check_tools() if t.required and not t.ok]


def missing_all() -> list[str]:
    return [t.name for t in check_tools() if not t.ok]


def summary_text() -> str:
    tools = check_tools()
    missing = [t.name for t in tools if not t.ok]
    if not missing:
        return "Dependencies: OK (ffmpeg, mkvtoolnix, tesseract optional for OCR)"
    req_missing = [t.name for t in tools if t.required and not t.ok]
    if req_missing:
        return f"Missing required: {', '.join(req_missing)}"
    return f"Optional missing: {', '.join(missing)}"


def apt_install_command(packages: tuple[str, ...] = APT_PACKAGES) -> list[str]:
    return [
        "pkexec",
        "apt-get",
        "install",
        "-y",
        *packages,
    ]


def install_dependencies(
    packages: tuple[str, ...] = APT_PACKAGES,
    timeout: int = 600,
) -> tuple[int, str]:
    """Run privileged apt install. Returns (returncode, combined output)."""
    cmd = apt_install_command(packages)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out
    except FileNotFoundError:
        return 127, "pkexec or apt-get not found. Install packages manually: " + " ".join(
            packages
        )
    except subprocess.TimeoutExpired:
        return 124, "Dependency installation timed out."


def has_rapidocr() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def ocr_available() -> bool:
    return bool(which("tesseract")) or has_rapidocr()
