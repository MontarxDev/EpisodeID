from pathlib import Path
from unittest.mock import MagicMock

from episodeid.metadata import TMDBClient


def test_search_and_episodes_cached(tmp_path: Path):
    session = MagicMock()

    def get(url, timeout=30):
        resp = MagicMock()
        resp.status_code = 200
        if "/search/tv" in url:
            resp.json.return_value = {
                "results": [
                    {
                        "id": 4194,
                        "name": "Star Wars: The Clone Wars",
                        "first_air_date": "2008-10-03",
                        "overview": "Animated series",
                    }
                ]
            }
        elif url.rstrip("/").endswith("/tv/4194") or "/tv/4194?" in url:
            resp.json.return_value = {
                "id": 4194,
                "name": "Star Wars: The Clone Wars",
                "first_air_date": "2008-10-03",
                "seasons": [{"season_number": 1, "episode_count": 2}],
            }
        elif "/season/1" in url:
            resp.json.return_value = {
                "episodes": [
                    {"episode_number": 1, "name": "Ambush", "overview": "Yoda ambush"},
                    {"episode_number": 2, "name": "Rising Malevolence", "overview": "Weapon"},
                ]
            }
        else:
            resp.json.return_value = {"images": {}}
        return resp

    session.get.side_effect = get
    client = TMDBClient("fake-key", cache_dir=tmp_path, session=session, sleep=lambda _: None)

    series = client.search_series("Clone Wars")
    assert series[0].id == 4194
    assert series[0].year == 2008

    eps = client.get_all_episodes(4194)
    assert len(eps) == 2
    assert eps[0].title == "Ambush"
    assert (tmp_path / "4194.json").exists()

    # Second call uses cache — no extra HTTP needed for seasons if we short-circuit
    session.get.reset_mock()
    eps2 = client.get_all_episodes(4194)
    assert len(eps2) == 2
    session.get.assert_not_called()
