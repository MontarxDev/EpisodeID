"""Entry point: python -m episodeid"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="episodeid", description="EpisodeID — TV episode renamer from subtitles")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    parser.add_argument("--cli-check", action="store_true", help="Check dependencies and exit")
    args, _unknown = parser.parse_known_args(argv)

    if args.version:
        from episodeid import __version__

        print(__version__)
        return 0

    if args.cli_check:
        from episodeid.deps import check_tools, ocr_available, summary_text

        print(summary_text())
        for t in check_tools():
            status = t.path or "MISSING"
            print(f"  {t.name}: {status}")
        print(f"  OCR available: {ocr_available()}")
        return 0

    # Default: launch GUI
    try:
        from episodeid.gui.app import run_app
    except ImportError as exc:
        print(f"GUI import failed: {exc}", file=sys.stderr)
        print("Install dependencies: pip install -e '.[ocr]'", file=sys.stderr)
        return 1
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
