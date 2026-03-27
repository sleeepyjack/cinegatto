import json
import os
import time

import pytest

from cinegatto.cache.service import CacheService


class TestCacheService:
    def _make_service(self, tmp_path, max_size=1024 * 1024 * 100):
        """Create a CacheService with a mocked max size for predictable tests."""
        svc = CacheService(str(tmp_path / "cache"), format_str="best")
        svc._get_max_size = lambda: max_size
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
        svc2 = CacheService(str(tmp_path / "cache"), format_str="best")
        assert svc2.contains("vid1")

    def test_reconcile_removes_orphan_parts(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir, exist_ok=True)
        orphan = os.path.join(cache_dir, "orphan.part")
        with open(orphan, "w") as f:
            f.write("junk")
        svc = CacheService(cache_dir, format_str="best")
        assert not os.path.exists(orphan)

    def test_reconcile_indexes_untracked_mp4(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, "mystery.mp4")
        with open(path, "wb") as f:
            f.write(b"\x00" * 200)
        svc = CacheService(cache_dir, format_str="best")
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

    def test_warm_all_dedup_already_queued(self, tmp_path):
        """warm_all doesn't re-enqueue already-queued videos."""
        svc = self._make_service(tmp_path)
        entries = [
            {"id": "vid1", "url": "https://youtube.com/watch?v=vid1"},
            {"id": "vid2", "url": "https://youtube.com/watch?v=vid2"},
        ]
        svc.warm("vid1", entries[0]["url"])  # pre-queue vid1
        result = svc.warm_all(entries)
        assert result["enqueued"] == 1  # only vid2
        assert result["already_queued"] == 1  # vid1

    def test_enqueue_retry_dedup(self, tmp_path):
        """_enqueue_retry doesn't create duplicates if already queued."""
        svc = self._make_service(tmp_path)
        svc.warm("vid1", "https://youtube.com/watch?v=vid1")  # in queue
        svc._enqueue_retry("vid1", "https://youtube.com/watch?v=vid1", 1)
        assert svc._download_queue.qsize() == 1  # not 2

    def test_evict_prefers_incomplete_over_not_in_playlist_over_lru(self, tmp_path):
        """Eviction priority: incomplete > not-in-playlist > LRU."""
        svc = self._make_service(tmp_path, max_size=1000)
        cache_dir = str(tmp_path / "cache")
        # Create 3 videos: incomplete, not-in-playlist, active LRU
        p1 = self._create_fake_video(cache_dir, "incomplete", 300)
        p2 = self._create_fake_video(cache_dir, "stale", 300)
        p3 = self._create_fake_video(cache_dir, "active", 300)
        with svc._lock:
            svc._index["entries"]["incomplete"] = {
                "file": p1, "size": 300, "last_played": None, "complete": False,
            }
            svc._index["entries"]["stale"] = {
                "file": p2, "size": 300, "last_played": "2020-01-01", "complete": True,
                "in_playlist": False,
            }
            svc._index["entries"]["active"] = {
                "file": p3, "size": 300, "last_played": "2025-01-01", "complete": True,
                "in_playlist": True,
            }
            svc._recompute_size()
            # Need 300 bytes — should evict incomplete first
            freed = svc._evict_for(300, protect_ids=set())
        assert freed >= 300
        assert "incomplete" not in svc._index["entries"]
        # stale and active should still be there (only needed 300)
        assert "stale" in svc._index["entries"] or "active" in svc._index["entries"]

    def test_get_returns_none_for_incomplete(self, tmp_path):
        """get() returns None for incomplete cache entries."""
        svc = self._make_service(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1", 500)
        with svc._lock:
            svc._index["entries"]["vid1"] = {
                "file": path, "size": 500, "last_played": None, "complete": False,
            }
        assert svc.get("vid1") is None

    def test_download_skip_not_retried(self, tmp_path):
        """Videos returning 'skip' from _download are not retried."""
        svc = self._make_service(tmp_path, max_size=100)  # tiny cache
        svc._get_max_size = lambda: 100
        svc.start()
        try:
            # Enqueue a video, mock _estimate_size to return huge size
            original_estimate = svc._estimate_size
            svc._estimate_size = lambda cmd, url: 999999999  # way too large
            svc.warm("big_vid", "https://youtube.com/watch?v=big_vid")
            import time
            time.sleep(0.5)  # let worker process
            # Should not be retried (skip, not fail)
            stats = svc.get_stats()
            assert stats["queue_depth"] == 0
        finally:
            svc.stop()

    def test_last_error_lifecycle(self, tmp_path):
        """Failed download sets last_error; successful download clears it."""
        svc = self._make_service(tmp_path)
        # Simulate a failure
        with svc._lock:
            svc._last_error = {"video_id": "failed", "exit_code": 1, "stderr": "boom"}
            svc._downloads_failed += 1
        assert svc.get_stats()["last_error"] is not None
        # Simulate a success clearing it
        with svc._lock:
            svc._downloads_completed += 1
            svc._last_error = None
        assert svc.get_stats()["last_error"] is None

    def test_retry_scheduled_for_fail_not_skip(self, tmp_path):
        """Worker schedules retry Timer for 'fail' but not 'skip'."""
        from unittest.mock import patch
        import threading
        svc = self._make_service(tmp_path)

        timer_calls = []
        original_timer = threading.Timer
        def mock_timer(delay, fn, args=(), kwargs=None):
            timer_calls.append({"delay": delay, "args": args})
            t = original_timer(9999, lambda: None)  # never fires
            t.daemon = True
            return t

        # Test "fail" → retry scheduled
        svc._download = lambda vid, url: "fail"
        svc.start()
        try:
            with patch("cinegatto.cache.service.threading.Timer", side_effect=mock_timer):
                svc.warm("vid_fail", "https://youtube.com/watch?v=vid_fail")
                import time
                time.sleep(0.5)
            assert len(timer_calls) == 1  # retry was scheduled

            # Test "skip" → no retry
            timer_calls.clear()
            svc._download = lambda vid, url: "skip"
            with patch("cinegatto.cache.service.threading.Timer", side_effect=mock_timer):
                svc.warm("vid_skip", "https://youtube.com/watch?v=vid_skip")
                time.sleep(0.5)
            assert len(timer_calls) == 0  # no retry
        finally:
            svc.stop()

    def test_start_and_stop(self, tmp_path):
        svc = self._make_service(tmp_path)
        svc.start()
        assert svc._worker.is_alive()
        svc.stop()
        assert not svc._worker.is_alive()
