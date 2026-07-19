"""Session logging for later review of scans and applies.

Writes under ~/.local/share/episodeid/sessions/<timestamp>/:
  - events.log      (live progress, one line per event)
  - summary.json    (full machine-readable result)
  - summary.md      (human-readable review)
  - settings.json   (non-secret settings snapshot)

Also updates:
  - ~/.local/share/episodeid/sessions/LATEST  (path to last session)
  - ~/.local/share/episodeid/last-run.log     (short pointer)
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from episodeid.config import data_dir


def sessions_root() -> Path:
    return data_dir() / "sessions"


def _build_provenance() -> dict[str, Any]:
    """Identify which binary / matcher logic produced a session (debug AppImage lag)."""
    import hashlib
    import inspect
    import os
    import sys

    prov: dict[str, Any] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "appimage": bool(os.environ.get("APPIMAGE") or os.environ.get("APPDIR")),
    }
    if os.environ.get("APPIMAGE"):
        prov["appimage_path"] = os.environ.get("APPIMAGE")
    try:
        from episodeid import __version__

        prov["version"] = __version__
    except Exception:
        prov["version"] = "unknown"
    try:
        from episodeid import matcher as matcher_mod

        src = inspect.getsource(matcher_mod.reassign_sequential_disc)
        prov["matcher_sha12"] = hashlib.sha256(src.encode()).hexdigest()[:12]
        prov["sequential_mode"] = (
            "strict"
            if "STRICT sequential" in src and "cand_ranks" not in src
            else "soft_pm1"
            if "cand_ranks" in src
            else "unknown"
        )
        prov["matcher_file"] = getattr(matcher_mod, "__file__", None)
    except Exception as exc:
        prov["matcher_error"] = str(exc)
    try:
        import subprocess

        root = Path(__file__).resolve().parents[1]
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if r.returncode == 0:
            prov["git_head"] = r.stdout.strip()
    except Exception:
        pass
    return prov


class SessionLog:
    def __init__(self, label: str = "scan"):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.id = f"{stamp}_{label}"
        self.dir = sessions_root() / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.log"
        self.summary_json = self.dir / "summary.json"
        self.summary_md = self.dir / "summary.md"
        self.settings_path = self.dir / "settings.json"
        self._start = datetime.now(timezone.utc)
        self._events: list[dict[str, Any]] = []
        self.log("session_start", f"Session {self.id} started")
        self._write_latest_pointer()

    def _write_latest_pointer(self) -> None:
        try:
            latest = sessions_root() / "LATEST"
            latest.write_text(str(self.dir) + "\n", encoding="utf-8")
            (data_dir() / "last-run.log").write_text(
                f"Last session: {self.dir}\n"
                f"Review files:\n"
                f"  {self.summary_md}\n"
                f"  {self.summary_json}\n"
                f"  {self.events_path}\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def log(self, kind: str, message: str, **extra: Any) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {"ts": now, "kind": kind, "message": message, **extra}
        self._events.append(entry)
        line = f"{now} [{kind}] {message}"
        if extra:
            # keep event line readable; dump compact extras
            try:
                line += " | " + json.dumps(extra, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                line += f" | {extra!r}"
        try:
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def progress_callback(self, ev: Any) -> None:
        """Compatible with pipeline ProgressEvent."""
        phase = getattr(ev, "phase", "progress")
        msg = getattr(ev, "message", str(ev))
        cur = getattr(ev, "current", 0)
        total = getattr(ev, "total", 0)
        path = getattr(ev, "path", None)
        self.log(
            "progress",
            msg,
            phase=phase,
            current=cur,
            total=total,
            path=path,
        )

    def save_settings_snapshot(self, settings: Any) -> None:
        try:
            data = settings.to_dict() if hasattr(settings, "to_dict") else asdict(settings) if is_dataclass(settings) else {}
            # never write secrets
            data["_provenance"] = _build_provenance()
            self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.log(
                "provenance",
                "Binary / package provenance",
                **data["_provenance"],
            )
        except Exception as exc:
            self.log("warn", f"Could not snapshot settings: {exc}")

    def finalize_scan(
        self,
        *,
        series_name: str,
        series_id: int | None,
        folder: str,
        output_root: str,
        plan: list[Any],
        extra: dict[str, Any] | None = None,
    ) -> Path:
        """Write summary.json + summary.md from plan rows."""
        rows = []
        for r in plan:
            if hasattr(r, "to_dict"):
                rows.append(r.to_dict())
            else:
                rows.append(r)

        counts = {
            "total_rows": len(rows),
            "selected_renames": sum(
                1 for r in plan if getattr(r, "selected", False) and getattr(r, "row_kind", "rename") == "rename"
            ),
            "selected_splits": sum(
                1 for r in plan if getattr(r, "selected", False) and getattr(r, "row_kind", "rename") == "split"
            ),
            "inventory_skips": sum(1 for r in plan if getattr(r, "row_kind", "") == "inventory_skip"),
            "errors": sum(1 for r in plan if getattr(r, "error", None)),
            "low_confidence": sum(
                1
                for r in plan
                if getattr(r, "confidence", 100) < 55 and getattr(r, "row_kind", "rename") != "inventory_skip"
            ),
            "review_flags": sum(
                1 for r in plan if "review" in (getattr(r, "flags", None) or [])
            ),
        }

        payload = {
            "session_id": self.id,
            "started": self._start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "finished": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "series": {"id": series_id, "name": series_name},
            "folder": folder,
            "output_root": output_root,
            "counts": counts,
            "rows": rows,
            "extra": extra or {},
        }
        self.summary_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        self.summary_md.write_text(
            _render_markdown(payload),
            encoding="utf-8",
        )
        # also keep classic scans/ copy for compatibility
        try:
            scans = data_dir() / "scans"
            scans.mkdir(parents=True, exist_ok=True)
            stamp = self.id.split("_")[0]
            (scans / f"{stamp}.json").write_text(
                json.dumps(payload, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass

        self.log("session_end", f"Scan finished: {counts}")
        self._write_latest_pointer()
        return self.summary_md

    def finalize_apply(
        self,
        *,
        successes: list[dict],
        failures: list[dict],
    ) -> None:
        apply_path = self.dir / "apply.json"
        data = {
            "session_id": self.id,
            "finished": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "success_count": len(successes),
            "failure_count": len(failures),
            "successes": successes,
            "failures": failures,
        }
        apply_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        with self.summary_md.open("a", encoding="utf-8") as fh:
            fh.write("\n\n## Apply results\n\n")
            fh.write(f"- Successes: **{len(successes)}**\n")
            fh.write(f"- Failures: **{len(failures)}**\n\n")
            if successes:
                fh.write("### Succeeded\n\n")
                for s in successes[:50]:
                    fh.write(f"- `{s}`\n")
                if len(successes) > 50:
                    fh.write(f"- … and {len(successes) - 50} more\n")
            if failures:
                fh.write("\n### Failed\n\n")
                for f in failures[:50]:
                    fh.write(f"- `{f}`\n")
        self.log("apply_end", f"Apply done: ok={len(successes)} fail={len(failures)}")
        self._write_latest_pointer()


def _render_markdown(payload: dict[str, Any]) -> str:
    c = payload.get("counts") or {}
    lines = [
        f"# EpisodeID scan review — {payload.get('session_id')}",
        "",
        f"- **Started:** {payload.get('started')}",
        f"- **Finished:** {payload.get('finished')}",
        f"- **Series:** {payload.get('series', {}).get('name')} (id {payload.get('series', {}).get('id')})",
        f"- **Scan folder:** `{payload.get('folder')}`",
        f"- **Output root:** `{payload.get('output_root')}`",
        "",
        "## Counts",
        "",
        f"| Metric | Count |",
        f"|--------|------:|",
        f"| Total rows | {c.get('total_rows', 0)} |",
        f"| Selected renames | {c.get('selected_renames', 0)} |",
        f"| Selected splits | {c.get('selected_splits', 0)} |",
        f"| Inventory skips (already present) | {c.get('inventory_skips', 0)} |",
        f"| Errors | {c.get('errors', 0)} |",
        f"| Low confidence | {c.get('low_confidence', 0)} |",
        f"| Review flags | {c.get('review_flags', 0)} |",
        "",
    ]

    extra = payload.get("extra") or {}
    coverage = extra.get("coverage") or []
    if coverage:
        lines += [
            "## Season coverage",
            "",
            "| Season | Found | Total | Status | Missing |",
            "|--------|------:|------:|--------|---------|",
        ]
        for cov in coverage:
            season = int(cov.get("season") or 0)
            found = int(cov.get("found") or 0)
            total = int(cov.get("total") or 0)
            complete = cov.get("complete")
            missing = cov.get("missing_codes") or []
            miss_s = ", ".join(missing[:12])
            if len(missing) > 12:
                miss_s += f" +{len(missing) - 12}"
            status = "complete" if complete else "incomplete"
            lines.append(
                f"| S{season:02d} | {found} | {total} | {status} | {miss_s or '—'} |"
            )
        lines.append("")
        if extra.get("coverage_summary"):
            lines.append(f"_Summary: {extra['coverage_summary']}_")
            lines.append("")

    lines += [
        "## Rows needing attention",
        "",
    ]

    attention = []
    ok_rows = []
    skips = []
    for r in payload.get("rows") or []:
        kind = r.get("row_kind") or "rename"
        conf = r.get("confidence") or 0
        err = r.get("error")
        flags = r.get("flags") or []
        code = None
        if r.get("season") is not None and r.get("episode") is not None:
            code = f"S{int(r['season']):02d}E{int(r['episode']):02d}"
        entry = {
            "kind": kind,
            "code": code,
            "conf": conf,
            "orig": r.get("original_name"),
            "new": r.get("proposed_name"),
            "selected": r.get("selected"),
            "error": err,
            "flags": flags,
            "skip": r.get("skip_reason"),
            "covered_by": r.get("covered_by"),
            "split": (r.get("split_start"), r.get("split_end")),
            "path": r.get("path"),
            "target": r.get("target_dir"),
            "dialogue": (r.get("dialogue_lines") or [])[:5],
        }
        if kind == "inventory_skip":
            skips.append(entry)
        elif err or conf < 55 or not r.get("selected") or "review" in flags or "duplicate" in str(flags):
            attention.append(entry)
        else:
            ok_rows.append(entry)

    if not attention:
        lines.append("_No obvious problem rows (selected high-confidence items look clean)._")
        lines.append("")
    else:
        lines.append("| Kind | Code | Conf | Selected | Original | Issue |")
        lines.append("|------|------|-----:|:--------:|----------|-------|")
        for e in attention:
            issue = e["error"] or e["skip"] or ",".join(e["flags"][:4]) or "unselected/review"
            lines.append(
                f"| {e['kind']} | {e['code'] or '—'} | {e['conf']:.0f} | "
                f"{'yes' if e['selected'] else 'no'} | `{e['orig']}` | {issue} |"
            )
        lines.append("")

    lines += [
        "## Selected renames / splits",
        "",
    ]
    selected = [e for e in ok_rows + attention if e["selected"]]
    if not selected:
        lines.append("_Nothing selected for apply._")
        lines.append("")
    else:
        lines.append("| Kind | Code | Conf | From | To |")
        lines.append("|------|------|-----:|------|----|")
        for e in selected:
            lines.append(
                f"| {e['kind']} | {e['code'] or '—'} | {e['conf']:.0f} | "
                f"`{e['orig']}` | `{e['new']}` |"
            )
        lines.append("")

    if skips:
        lines += [
            "## Mega inventory skips (already present)",
            "",
            "| Segment | Code | Conf | Covered by |",
            "|---------|------|-----:|------------|",
        ]
        for e in skips:
            lines.append(
                f"| `{e['orig']}` | {e['code'] or '—'} | {e['conf']:.0f} | `{e['covered_by'] or e['skip']}` |"
            )
        lines.append("")

    lines += [
        "## How to review later",
        "",
        "Tell the agent:",
        "",
        "```text",
        "Review my EpisodeID logs under ~/.local/share/episodeid/sessions/LATEST",
        "```",
        "",
        f"This session directory: `{payload.get('session_id')}`",
        "",
    ]
    return "\n".join(lines) + "\n"


def get_latest_session_dir() -> Path | None:
    latest = sessions_root() / "LATEST"
    if not latest.exists():
        # fall back to newest directory
        root = sessions_root()
        if not root.exists():
            return None
        dirs = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
        return dirs[0] if dirs else None
    try:
        p = Path(latest.read_text(encoding="utf-8").strip())
        return p if p.exists() else None
    except OSError:
        return None
