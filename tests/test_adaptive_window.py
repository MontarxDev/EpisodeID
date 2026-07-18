from episodeid.extractor import adaptive_sample_window


def test_short_episode_window_stays_inside_file():
    # 11 minute kids episode
    off, scan = adaptive_sample_window(11 * 60)
    assert off >= 0
    assert off + scan <= 11.1
    assert scan >= 3


def test_standard_episode_window():
    off, scan = adaptive_sample_window(23 * 60)
    assert off < 3
    assert 3 <= scan <= 10


def test_mega_file_short_sample():
    off, scan = adaptive_sample_window(116 * 60)
    assert scan <= 8
