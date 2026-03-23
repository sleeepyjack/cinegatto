import json
import os
import time

import pytest

from cinegatto.cache.service import CacheService


class TestCacheService:
    def _make_service(self, tmp_path, max_size=1024 * 1024 * 100):
        """Create a CacheService with 100 MB budget, no downloads."""
        svc = CacheService(str(tmp_path / "cache"), max_size,
                           format_str="best", cookies_from_browser="")
        return svc

    def _create_fake_video(self, cache_dir, video_id, size=1000):
        path = os.path.join(cache_dir, f"{video_id}.mp4")
        with open(path, "wb") as f:
            f.write(b"\x00" * size)
        return path

    def test_creates_cache_dir(self, tmp_path):
        svc = self._make_service(tmp_path)
        assert os.path.isdir(str(tmp_path / "cache"))

    def test_get_returns_none_for_missing(self, tmp_path):
        svc = self._make_service(tmp_path)
        assert svc.get("nonexistent") is None

    def test_register_and_get(self, tmp_path):
        svc = self._make_service(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1", size=500)
        with svc._lock:
            svc._index["entries"]["vid1"] = {
                "file": path, "size": 500, "last_played": None, "complete": True,
            }
            svc._recompute_size()
            svc._save_index()
        result = svc.get("vid1")
        assert result == path

    def test_get_returns_none_if_file_missing(self, tmp_path):
        svc = self._make_service(tmp_path)
        with svc._lock:
            svc._index["entries"]["vid1"] = {
                "file": "/nonexistent/vid1.mp4", "size": 500,
                "last_played": None, "complete": True,
            }
        assert svc.get("vid1") is None

    def test_contains_no_stat_side_effect(self, tmp_path):
        svc = self._make_service(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1", size=500)
        with svc._lock:
            svc._index["entries"]["vid1"] = {
                "file": path, "size": 500, "last_played": None, "complete": True,
            }
        assert svc.contains("vid1") is True
        assert svc._hits == 0  # contains doesn't count

    def test_get_counts_hits_and_misses(self, tmp_path):
        svc = self._make_service(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1", size=500)
        with svc._lock:
            svc._index["entries"]["vid1"] = {
                "file": path, "size": 500, "last_played": None, "complete": True,
            }
        svc.get("vid1")  # hit
        svc.get("vid2")  # miss
        assert svc._hits == 1
        assert svc._misses == 1

    def test_get_updates_last_played(self, tmp_path):
        svc = self._make_service(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1", size=500)
        with svc._lock:
            svc._index["entries"]["vid1"] = {
                "file": path, "size": 500, "last_played": None, "complete": True,
            }
        svc.get("vid1")
        assert svc._index["entries"]["vid1"]["last_played"] is not None

    def test_evict_for_removes_lru(self, tmp_path):
        svc = self._make_service(tmp_path, max_size=1000)
        cache_dir = str(tmp_path / "cache")
        p1 = self._create_fake_video(cache_dir, "old", 400)
        p2 = self._create_fake_video(cache_dir, "new", 400)
        with svc._lock:
            svc._index["entries"]["old"] = {
                "file": p1, "size": 400, "last_played": "2020-01-01", "complete": True,
            }
            svc._index["entries"]["new"] = {
                "file": p2, "size": 400, "last_played": "2025-01-01", "complete": True,
            }
            svc._recompute_size()
            freed = svc._evict_for(300, protect_ids=set())
        assert freed >= 300
        assert svc.get("old") is None
        assert svc.get("new") is not None

    def test_evict_respects_protected_ids(self, tmp_path):
        svc = self._make_service(tmp_path, max_size=1000)
        cache_dir = str(tmp_path / "cache")
        p1 = self._create_fake_video(cache_dir, "protected", 400)
        p2 = self._create_fake_video(cache_dir, "expendable", 400)
        with svc._lock:
            svc._index["entries"]["protected"] = {
                "file": p1, "size": 400, "last_played": "2020-01-01", "complete": True,
            }
            svc._index["entries"]["expendable"] = {
                "file": p2, "size": 400, "last_played": "2025-01-01", "complete": True,
            }
            svc._recompute_size()
            svc._evict_for(300, protect_ids={"protected"})
        assert svc.contains("protected")
        assert not svc.contains("expendable")

    def test_cleanup_marks_removed(self, tmp_path):
        svc = self._make_service(tmp_path)
        cache_dir = str(tmp_path / "cache")
        p1 = self._create_fake_video(cache_dir, "in_list", 400)
        p2 = self._create_fake_video(cache_dir, "removed", 400)
        with svc._lock:
            svc._index["entries"]["in_list"] = {
                "file": p1, "size": 400, "last_played": None, "complete": True,
            }
            svc._index["entries"]["removed"] = {
                "file": p2, "size": 400, "last_played": None, "complete": True,
            }
        svc.cleanup({"in_list"})
        assert svc._index["entries"]["removed"]["in_playlist"] is False

    def test_index_persists(self, tmp_path):
        svc = self._make_service(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1", 500)
        with svc._lock:
            svc._index["entries"]["vid1"] = {
                "file": path, "size": 500, "last_played": None, "complete": True,
            }
            svc._save_index()
        svc2 = CacheService(str(tmp_path / "cache"), 1024 * 1024 * 100,
                            format_str="best", cookies_from_browser="")
        assert svc2.contains("vid1")

    def test_reconcile_removes_orphan_parts(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir, exist_ok=True)
        orphan = os.path.join(cache_dir, "orphan.part")
        with open(orphan, "w") as f:
            f.write("junk")
        svc = CacheService(cache_dir, 1024 * 1024 * 100,
                           format_str="best", cookies_from_browser="")
        assert not os.path.exists(orphan)

    def test_reconcile_indexes_untracked_mp4(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, "mystery.mp4")
        with open(path, "wb") as f:
            f.write(b"\x00" * 200)
        svc = CacheService(cache_dir, 1024 * 1024 * 100,
                           format_str="best", cookies_from_browser="")
        assert svc.contains("mystery")

    def test_get_stats(self, tmp_path):
        svc = self._make_service(tmp_path)
        stats = svc.get_stats()
        assert "total_size" in stats
        assert "count" in stats
        assert "max_size" in stats
        assert "hits" in stats
        assert "misses" in stats

    def test_warm_skips_cached(self, tmp_path):
        svc = self._make_service(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1", 100)
        with svc._lock:
            svc._index["entries"]["vid1"] = {
                "file": path, "size": 100, "last_played": None, "complete": True,
            }
        svc.warm("vid1", "https://youtube.com/watch?v=vid1")
        assert svc._download_queue.empty()

    def test_warm_enqueues_uncached(self, tmp_path):
        svc = self._make_service(tmp_path)
        svc.warm("vid1", "https://youtube.com/watch?v=vid1")
        assert not svc._download_queue.empty()

    def test_start_and_stop(self, tmp_path):
        svc = self._make_service(tmp_path)
        svc.start()
        assert svc._worker.is_alive()
        svc.stop()
        assert not svc._worker.is_alive()
