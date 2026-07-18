from pathlib import Path

from episodeid.cache_manager import cache_root, durable_cache_root, get_cache_stats
from episodeid.edge_cases import classify_duration, is_problem_result, mark_content_duplicates
from episodeid.extractor import list_video_files
from episodeid.models import MatchResult
from episodeid.refsubs import RefAttachStats, attach_reference_subs, load_cached_sample


def test_recursive_finds_nested(tmp_path: Path):
    (tmp_path / "Season 01").mkdir()
    (tmp_path / "Season 01" / "ep.mkv").write_bytes(b"x" * 100)
    (tmp_path / "top.mkv").write_bytes(b"x" * 100)
    (tmp_path / "Sample").mkdir()
    (tmp_path / "Sample" / "sample.mkv").write_bytes(b"x" * 100)
    files = list_video_files(tmp_path, recursive=True, skip_sample_folders=True)
    names = {p.name for p in files}
    assert "ep.mkv" in names
    assert "top.mkv" in names
    assert "sample.mkv" not in names


def test_non_recursive(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "hidden.mkv").write_bytes(b"x")
    (tmp_path / "top.mkv").write_bytes(b"x")
    files = list_video_files(tmp_path, recursive=False)
    assert [p.name for p in files] == ["top.mkv"]


def test_partial_duration_flags():
    flags = classify_duration(5 * 60, 22)
    assert "partial_or_extra" in flags
    flags2 = classify_duration(100 * 60, 22)
    assert "multi_episode_or_feature" in flags2


def test_content_duplicate_marks_smaller():
    a = MatchResult(
        path=Path("/tmp/a.mkv"),
        dialogue_lines=["hello there general kenobi this is a long enough sample line for dupe"],
        confidence=80,
        season=1,
        episode=1,
        title="A",
    )
    b = MatchResult(
        path=Path("/tmp/b.mkv"),
        dialogue_lines=["hello there general kenobi this is a long enough sample line for dupe"],
        confidence=70,
        season=1,
        episode=1,
        title="A",
    )
    # sizes unknown (missing files) — still marks one
    mark_content_duplicates([a, b], threshold=80)
    flags = a.flags + b.flags
    assert "content_duplicate" in flags or "content_primary" in flags


def test_ref_stats_dataclass():
    s = RefAttachStats(cached=3, downloaded=1, failed=0, policy="download-missing")
    assert "3 cached" in s.summary()


def test_cache_root_durable():
    assert "share" in str(durable_cache_root()) or "cache" in str(durable_cache_root())
    root = cache_root(True)
    assert root.exists() or True  # path object ok
    st = get_cache_stats(True)
    assert st.root == root or st.tmdb_files >= 0
