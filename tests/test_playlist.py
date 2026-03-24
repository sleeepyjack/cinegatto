from unittest.mock import MagicMock, patch

import pytest

from cinegatto.playlist.selector import Selector
from cinegatto.playlist.fetcher import fetch_playlist


class TestSelector:
    def _sample_entries(self, n=5):
        return [{"id": f"vid{i}", "title": f"Video {i}", "url": f"https://youtube.com/watch?v=vid{i}"} for i in range(n)]

    def test_pick_returns_random_entry(self):
        entries = self._sample_entries()
        selector = Selector(entries)
        pick = selector.pick()
        assert pick in entries

    def test_pick_adds_to_history(self):
        entries = self._sample_entries()
        selector = Selector(entries)
        first = selector.pick()
        second = selector.pick()
        # After two picks, previous() should return the first one
        assert selector.previous() == first

    def test_previous_returns_last_played(self):
        entries = self._sample_entries()
        selector = Selector(entries)
        first = selector.pick()
        second = selector.pick()
        prev = selector.previous()
        # previous() should return the one before current (i.e., first)
        assert prev == first

    def test_previous_when_no_history_returns_none(self):
        entries = self._sample_entries()
        selector = Selector(entries)
        assert selector.previous() is None

    def test_history_max_size(self):
        entries = self._sample_entries(10)
        selector = Selector(entries, history_size=3)
        for _ in range(5):
            selector.pick()
        # History deque should cap at 3
        assert len(selector._history) <= 3

    def test_pick_from_empty_entries_raises(self):
        selector = Selector([])
        with pytest.raises(ValueError, match="empty"):
            selector.pick()

    def test_update_entries(self):
        entries = self._sample_entries(3)
        selector = Selector(entries)
        new_entries = self._sample_entries(5)
        selector.update_entries(new_entries)
        assert len(selector._entries) == 5

    def test_sequential_mode_plays_in_order(self):
        entries = self._sample_entries(3)
        selector = Selector(entries, shuffle=False)
        picks = [selector.pick() for _ in range(3)]
        assert picks == entries

    def test_sequential_mode_wraps_around(self):
        entries = self._sample_entries(3)
        selector = Selector(entries, shuffle=False)
        picks = [selector.pick() for _ in range(5)]
        assert picks[3] == entries[0]  # wrapped
        assert picks[4] == entries[1]

    def test_shuffle_mode_picks_from_entries(self):
        entries = self._sample_entries(5)
        selector = Selector(entries, shuffle=True)
        for _ in range(10):
            assert selector.pick() in entries

    def test_shuffle_never_repeats_back_to_back(self):
        entries = self._sample_entries(5)
        selector = Selector(entries, shuffle=True)
        prev = selector.pick()
        for _ in range(50):
            current = selector.pick()
            assert current["id"] != prev["id"]
            prev = current

    def test_peek_next_sequential(self):
        entries = self._sample_entries(5)
        selector = Selector(entries, shuffle=False)
        peeked = selector.peek_next(n=2)
        assert len(peeked) == 2
        assert peeked[0] == entries[0]
        assert peeked[1] == entries[1]
        # peek should not advance the index
        pick = selector.pick()
        assert pick == entries[0]

    def test_peek_next_shuffle(self):
        entries = self._sample_entries(5)
        selector = Selector(entries, shuffle=True)
        peeked = selector.peek_next(n=1)
        assert len(peeked) == 1
        assert peeked[0] in entries

    def test_peek_next_empty(self):
        selector = Selector([])
        assert selector.peek_next(n=1) == []

    def test_get_all_entries(self):
        entries = self._sample_entries(5)
        selector = Selector(entries)
        result = selector.get_all_entries()
        assert result == entries
        # Should be a copy
        result.append({"id": "extra"})
        assert len(selector.get_all_entries()) == 5


class TestFetchPlaylist:
    @patch("cinegatto.playlist.fetcher.yt_dlp.YoutubeDL")
    def test_returns_entries(self, MockYDL):
        mock_ydl = MockYDL.return_value.__enter__.return_value
        mock_ydl.extract_info.return_value = {
            "entries": [
                {"id": "abc", "title": "Birds at feeder", "url": "https://youtube.com/watch?v=abc"},
                {"id": "def", "title": "Squirrels playing", "url": "https://youtube.com/watch?v=def"},
            ]
        }
        result = fetch_playlist("https://youtube.com/playlist?list=test")
        assert len(result) == 2
        assert result[0]["id"] == "abc"
        assert result[1]["title"] == "Squirrels playing"

    @patch("cinegatto.playlist.fetcher.yt_dlp.YoutubeDL")
    def test_filters_none_entries(self, MockYDL):
        """yt-dlp can return None entries for unavailable videos."""
        mock_ydl = MockYDL.return_value.__enter__.return_value
        mock_ydl.extract_info.return_value = {
            "entries": [
                {"id": "abc", "title": "Birds", "url": "https://youtube.com/watch?v=abc"},
                None,
                {"id": "def", "title": "Cats", "url": "https://youtube.com/watch?v=def"},
            ]
        }
        result = fetch_playlist("https://youtube.com/playlist?list=test")
        assert len(result) == 2

    @patch("cinegatto.playlist.fetcher.yt_dlp.YoutubeDL")
    def test_empty_playlist_raises(self, MockYDL):
        mock_ydl = MockYDL.return_value.__enter__.return_value
        mock_ydl.extract_info.return_value = {"entries": []}
        with pytest.raises(ValueError, match="empty"):
            fetch_playlist("https://youtube.com/playlist?list=test")

    @patch("cinegatto.playlist.fetcher.yt_dlp.YoutubeDL")
    def test_network_error_raises(self, MockYDL):
        mock_ydl = MockYDL.return_value.__enter__.return_value
        mock_ydl.extract_info.side_effect = Exception("network error")
        with pytest.raises(Exception, match="network error"):
            fetch_playlist("https://youtube.com/playlist?list=test")
