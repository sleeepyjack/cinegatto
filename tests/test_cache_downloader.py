import os
from unittest.mock import MagicMock, patch

import pytest

from cinegatto.cache.downloader import Downloader


class TestDownloader:
    def _make_downloader(self, tmp_path, max_size=1024 * 1024 * 100):
        from cinegatto.cache.manager import CacheManager
        cache = CacheManager(str(tmp_path / "cache"), max_size)
        dl = Downloader(cache, "best[height<=720]")
        return dl, cache

    def test_enqueue_skips_cached(self, tmp_path):
        dl, cache = self._make_downloader(tmp_path)
        cache_dir = str(tmp_path / "cache")
        # Pre-populate cache
        path = os.path.join(cache_dir, "vid1.mp4")
        with open(path, "wb") as f:
            f.write(b"\x00" * 100)
        cache.register("vid1", path, 100)

        dl.enqueue("vid1", "https://youtube.com/watch?v=vid1")
        assert dl._queue.empty()  # should not be queued

    def test_enqueue_skips_duplicate(self, tmp_path):
        dl, cache = self._make_downloader(tmp_path)
        dl.enqueue("vid1", "https://youtube.com/watch?v=vid1")
        dl.enqueue("vid1", "https://youtube.com/watch?v=vid1")
        assert dl._queue.qsize() == 1

    def test_start_and_stop(self, tmp_path):
        dl, cache = self._make_downloader(tmp_path)
        dl.start()
        assert dl._worker.is_alive()
        dl.stop()
        assert not dl._worker.is_alive()
