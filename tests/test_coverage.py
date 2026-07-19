from pathlib import Path

from episodeid.coverage import (
    format_coverage_summary,
    season_coverage,
)
from episodeid.models import Episode, RenamePlanRow


def _ep(s: int, e: int) -> Episode:
    return Episode(season=s, episode=e, title=f"Ep {e}")


def _row(s: int, e: int, conf: float = 90, selected: bool = True, **kw) -> RenamePlanRow:
    return RenamePlanRow(
        path=Path(f"/x/S{s:02d}E{e:02d}.mkv"),
        original_name=f"S{s:02d}E{e:02d}.mkv",
        season=s,
        episode=e,
        confidence=conf,
        selected=selected,
        official_title=f"Ep {e}",
        **kw,
    )


def test_season_coverage_missing_list():
    catalog = [_ep(7, i) for i in range(1, 13)]
    plan = [
        _row(7, 1, 93),
        _row(7, 2, 68),
        _row(7, 3, 92),
    ]
    cov = season_coverage(plan, catalog, low_threshold=55)
    assert len(cov) == 1
    s7 = cov[0]
    assert s7.season == 7
    assert s7.total == 12
    assert s7.found == 3
    assert "S07E04" in s7.missing_codes
    assert "S07E12" in s7.missing_codes
    assert s7.missing_count == 9
    assert "3/12" in s7.short_label()
    assert "missing" in s7.short_label()
    assert "S07E12" in s7.short_label() or "+1 more" in s7.short_label()


def test_complete_season():
    catalog = [_ep(1, i) for i in range(1, 5)]
    plan = [_row(1, i) for i in range(1, 5)]
    cov = season_coverage(plan, catalog)
    assert cov[0].complete
    assert "complete" in cov[0].short_label()


def test_trusted_filename_covers_without_selected():
    catalog = [_ep(1, 1), _ep(1, 2)]
    plan = [
        _row(1, 1, 95, selected=False, flags=["trusted_filename", "already_named"]),
    ]
    cov = season_coverage(plan, catalog)
    assert cov[0].found == 1
    assert "S01E01" in cov[0].found_codes
    assert "S01E02" in cov[0].missing_codes


def test_also_covered_from_output_library():
    catalog = [_ep(7, i) for i in range(1, 5)]
    plan = [_row(7, 1)]
    also = {(7, 2): "/out/S07E02.mkv", (7, 3): "/out/S07E03.mkv"}
    cov = season_coverage(plan, catalog, also_covered=also)
    assert cov[0].found == 3
    assert "S07E04" in cov[0].missing_codes


def test_inventory_skip_does_not_cover():
    catalog = [_ep(1, 1)]
    plan = [
        RenamePlanRow(
            path=Path("/mega.mkv"),
            original_name="mega.mkv",
            season=None,
            episode=None,
            row_kind="inventory_skip",
            selected=False,
        )
    ]
    cov = season_coverage(plan, catalog)
    assert cov[0].found == 0


def test_format_summary_incomplete_first():
    catalog = [_ep(1, i) for i in range(1, 3)] + [_ep(7, i) for i in range(1, 4)]
    plan = [_row(1, 1), _row(1, 2), _row(7, 1)]
    cov = season_coverage(plan, catalog)
    text = format_coverage_summary(cov)
    assert "S07" in text
    assert "missing" in text or "1/3" in text
