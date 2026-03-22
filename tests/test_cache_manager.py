import json
import os
import time

import pytest

from cinegatto.cache.manager import CacheManager


class TestCacheManager:
    def _make_manager(self, tmp_path, max_size=1024 * 1024 * 100):
        """Create a CacheManager with 100 MB default budget."""
        return CacheManager(str(tmp_path / "cache"), max_size)

    def _create_fake_video(self, cache_dir, video_id, size=1000):
        """Create a fake cached video file."""
        path = os.path.join(cache_dir, f"{video_id}.mp4")
        with open(path, "wb") as f:
            f.write(b"\x00" * size)
        return path

    def test_creates_cache_dir(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert os.path.isdir(str(tmp_path / "cache"))

    def test_is_cached_returns_none_for_missing(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.is_cached("nonexistent") is None

    def test_register_and_is_cached(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1", size=500)
        mgr.register("vid1", path, 500)
        result = mgr.is_cached("vid1")
        assert result == path

    def test_is_cached_returns_none_if_file_missing(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        # Register but don't create file
        mgr.register("vid1", "/nonexistent/vid1.mp4", 500)
        assert mgr.is_cached("vid1") is None

    def test_is_cached_returns_none_for_incomplete(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1")
        # Register as incomplete
        mgr._index["entries"]["vid1"] = {
            "file": path, "size": 1000, "last_played": None, "complete": False,
        }
        assert mgr.is_cached("vid1") is None

    def test_touch_updates_last_played(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1")
        mgr.register("vid1", path, 1000)
        old_ts = mgr._index["entries"]["vid1"]["last_played"]
        time.sleep(0.01)
        mgr.touch("vid1")
        new_ts = mgr._index["entries"]["vid1"]["last_played"]
        assert new_ts > old_ts

    def test_remove_deletes_file_and_entry(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1")
        mgr.register("vid1", path, 1000)
        mgr.remove("vid1")
        assert "vid1" not in mgr._index["entries"]
        assert not os.path.exists(path)

    def test_total_size_tracking(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        cache_dir = str(tmp_path / "cache")
        p1 = self._create_fake_video(cache_dir, "vid1", 500)
        p2 = self._create_fake_video(cache_dir, "vid2", 300)
        mgr.register("vid1", p1, 500)
        mgr.register("vid2", p2, 300)
        assert mgr.total_size == 800

    def test_evict_for_removes_lru(self, tmp_path):
        mgr = self._make_manager(tmp_path, max_size=1000)
        cache_dir = str(tmp_path / "cache")
        p1 = self._create_fake_video(cache_dir, "old", 400)
        p2 = self._create_fake_video(cache_dir, "new", 400)
        mgr.register("old", p1, 400)
        mgr.touch("old")
        time.sleep(0.01)
        mgr.register("new", p2, 400)
        mgr.touch("new")
        # Need 300 more bytes — should evict "old" (LRU)
        freed = mgr.evict_for(300, protect_ids=set())
        assert freed >= 300
        assert mgr.is_cached("old") is None
        assert mgr.is_cached("new") is not None

    def test_evict_for_respects_protected_ids(self, tmp_path):
        mgr = self._make_manager(tmp_path, max_size=1000)
        cache_dir = str(tmp_path / "cache")
        p1 = self._create_fake_video(cache_dir, "protected", 400)
        p2 = self._create_fake_video(cache_dir, "expendable", 400)
        mgr.register("protected", p1, 400)
        mgr.register("expendable", p2, 400)
        freed = mgr.evict_for(300, protect_ids={"protected"})
        assert mgr.is_cached("protected") is not None
        assert mgr.is_cached("expendable") is None

    def test_evict_prefers_incomplete_first(self, tmp_path):
        mgr = self._make_manager(tmp_path, max_size=1000)
        cache_dir = str(tmp_path / "cache")
        p1 = self._create_fake_video(cache_dir, "complete", 400)
        mgr.register("complete", p1, 400)
        # Create incomplete entry
        p2 = self._create_fake_video(cache_dir, "partial", 400)
        mgr._index["entries"]["partial"] = {
            "file": p2, "size": 400, "last_played": None, "complete": False,
        }
        mgr._total_size += 400
        freed = mgr.evict_for(300, protect_ids=set())
        assert mgr.is_cached("complete") is not None
        assert "partial" not in mgr._index["entries"]

    def test_cleanup_marks_removed_videos(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        cache_dir = str(tmp_path / "cache")
        p1 = self._create_fake_video(cache_dir, "in_playlist", 400)
        p2 = self._create_fake_video(cache_dir, "removed", 400)
        mgr.register("in_playlist", p1, 400)
        mgr.register("removed", p2, 400)
        mgr.cleanup(current_playlist_ids={"in_playlist"})
        assert mgr._index["entries"]["removed"].get("in_playlist") is False

    def test_index_persists_to_disk(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        cache_dir = str(tmp_path / "cache")
        path = self._create_fake_video(cache_dir, "vid1", 500)
        mgr.register("vid1", path, 500)
        # Load a new manager from same dir
        mgr2 = CacheManager(str(tmp_path / "cache"), 1024 * 1024 * 100)
        assert mgr2.is_cached("vid1") == path

    def test_reconcile_removes_orphan_part_files(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir, exist_ok=True)
        # Create orphan .part file (not in index)
        orphan = os.path.join(cache_dir, "orphan.part")
        with open(orphan, "w") as f:
            f.write("junk")
        mgr = CacheManager(cache_dir, 1024 * 1024 * 100)
        assert not os.path.exists(orphan)

    def test_reconcile_rebuilds_missing_entries(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir, exist_ok=True)
        # Create a .mp4 file with no index entry
        path = os.path.join(cache_dir, "mystery.mp4")
        with open(path, "wb") as f:
            f.write(b"\x00" * 200)
        mgr = CacheManager(cache_dir, 1024 * 1024 * 100)
        assert mgr.is_cached("mystery") is not None

    def test_get_stats(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        stats = mgr.get_stats()
        assert "total_size" in stats
        assert "count" in stats
        assert "max_size" in stats
        assert "hits" in stats
        assert "misses" in stats
