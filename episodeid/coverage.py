"""Season coverage: how many catalog episodes the plan found vs missing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from episodeid.models import Episode, RenamePlanRow


@dataclass
class SeasonCoverage:
    season: int
    total: int
    found: int
    found_codes: list[str] = field(default_factory=list)
    missing_codes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.found >= self.total and self.total > 0

    @property
    def missing_count(self) -> int:
        return max(0, self.total - self.found)

    def short_label(self) -> str:
        """e.g. 'S07: 9/12 missing E04, E07, E12' or 'S01: 22/22 complete'."""
        head = f"S{self.season:02d}: {self.found}/{self.total}"
        if self.complete:
            return f"{head} complete"
        miss = ", ".join(self.missing_codes[:8])
        if len(self.missing_codes) > 8:
            miss += f" +{len(self.missing_codes) - 8} more"
        return f"{head} missing {miss}" if miss else f"{head} incomplete"

    def to_dict(self) -> dict:
        return {
            "season": self.season,
            "total": self.total,
            "found": self.found,
            "found_codes": list(self.found_codes),
            "missing_codes": list(self.missing_codes),
            "complete": self.complete,
        }


def _is_covering_row(row: RenamePlanRow, *, low_threshold: float) -> bool:
    """Whether this plan row counts as covering an SxxExx identity."""
    if row.season is None or row.episode is None:
        return False
    if getattr(row, "row_kind", "rename") == "inventory_skip":
        return False
    if row.error and "no_english" in (row.error or "").lower():
        return False
    flags = row.flags or []
    if "trusted_filename" in flags or "already_named" in flags:
        return True
    if row.selected:
        return True
    if not row.error and row.confidence >= low_threshold:
        return True
    return False


def covered_codes_from_plan(
    plan: Iterable[RenamePlanRow],
    *,
    low_threshold: float = 55.0,
    also_covered: dict[tuple[int, int], str] | None = None,
) -> set[tuple[int, int]]:
    found: set[tuple[int, int]] = set()
    for row in plan:
        if _is_covering_row(row, low_threshold=low_threshold):
            found.add((int(row.season), int(row.episode)))  # type: ignore[arg-type]
    if also_covered:
        found.update(also_covered.keys())
    return found


def season_coverage(
    plan: list[RenamePlanRow],
    catalog: list[Episode],
    *,
    low_threshold: float = 55.0,
    also_covered: dict[tuple[int, int], str] | None = None,
    seasons: set[int] | None = None,
) -> list[SeasonCoverage]:
    """Compute found/missing episode codes per season against the catalog.

    ``seasons`` limits reporting (e.g. only seasons in this scan). Default:
    all seasons present in the catalog.
    """
    by_season: dict[int, list[Episode]] = {}
    for ep in catalog:
        by_season.setdefault(int(ep.season), []).append(ep)

    report_seasons = seasons if seasons is not None else set(by_season.keys())
    # Always include seasons that appear in the plan
    for row in plan:
        if row.season is not None:
            report_seasons.add(int(row.season))

    covered = covered_codes_from_plan(
        plan, low_threshold=low_threshold, also_covered=also_covered
    )

    out: list[SeasonCoverage] = []
    for season in sorted(report_seasons):
        catalog_eps = by_season.get(season, [])
        catalog_codes = {(int(e.season), int(e.episode)) for e in catalog_eps}
        if not catalog_codes and season in report_seasons:
            # Plan-only season with no catalog (shouldn't happen often)
            found_keys = sorted(k for k in covered if k[0] == season)
            out.append(
                SeasonCoverage(
                    season=season,
                    total=len(found_keys),
                    found=len(found_keys),
                    found_codes=[f"S{s:02d}E{e:02d}" for s, e in found_keys],
                    missing_codes=[],
                )
            )
            continue
        found_keys = sorted(k for k in catalog_codes if k in covered)
        missing_keys = sorted(k for k in catalog_codes if k not in covered)
        out.append(
            SeasonCoverage(
                season=season,
                total=len(catalog_codes),
                found=len(found_keys),
                found_codes=[f"S{s:02d}E{e:02d}" for s, e in found_keys],
                missing_codes=[f"S{s:02d}E{e:02d}" for s, e in missing_keys],
            )
        )
    return out


def format_coverage_summary(
    coverage: list[SeasonCoverage],
    *,
    max_incomplete: int = 6,
    max_complete_note: bool = True,
) -> str:
    """One-line / short multi-part status text for UI and progress."""
    if not coverage:
        return ""
    incomplete = [c for c in coverage if not c.complete]
    complete = [c for c in coverage if c.complete]
    parts: list[str] = []
    for c in incomplete[:max_incomplete]:
        parts.append(c.short_label())
    if len(incomplete) > max_incomplete:
        parts.append(f"+{len(incomplete) - max_incomplete} more incomplete seasons")
    if max_complete_note and complete:
        if len(complete) <= 3:
            parts.append(", ".join(c.short_label() for c in complete))
        else:
            seasons = ", ".join(f"S{c.season:02d}" for c in complete[:8])
            more = f" +{len(complete) - 8}" if len(complete) > 8 else ""
            parts.append(f"{len(complete)} seasons complete ({seasons}{more})")
    return " · ".join(parts)


def seasons_touched_by_plan(plan: list[RenamePlanRow]) -> set[int]:
    return {int(r.season) for r in plan if r.season is not None}
