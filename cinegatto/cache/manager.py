"""CacheManager — manages cached video files with index, eviction, and size tracking."""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("cinegatto.cache.manager")

_INDEX_FILE = "cache.json"
_INDEX_VERSION = 1


class CacheManager:
    """Manages a directory of cached video files with an on-disk index.

    Thread-safe: all mutations are protected by an internal lock.
    """

    def __init__(self, cache_path: str, max_size_bytes: int):
        self._cache_path = cache_path
        self._max_size = max_size_bytes
        self._lock = threading.Lock()
        self._index = {"version": _INDEX_VERSION, "entries": {}}
        self._total_size = 0
        self._hits = 0
        self._misses = 0

        os.makedirs(cache_path, exist_ok=True)
        self._load_index()
        self._reconcile()
        self._recompute_size()
        logger.info("CacheManager initialized", extra={
            "path": cache_path, "max_size_mb": max_size_bytes // (1024 * 1024),
            "cached_videos": len(self._index["entries"]),
            "total_size_mb": self._total_size // (1024 * 1024),
        })

    @property
    def total_size(self) -> int:
        return self._total_size

    def is_cached(self, video_id: str) -> Optional[str]:
        """Return file path if video is cached and complete, else None."""
        with self._lock:
            entry = self._index["entries"].get(video_id)
            if entry and entry.get("complete") and os.path.isfile(entry["file"]):
                self._hits += 1
                return entry["file"]
            self._misses += 1
            return None

    def touch(self, video_id: str) -> None:
        """Update last_played timestamp for a cached video."""
        with self._lock:
            entry = self._index["entries"].get(video_id)
            if entry:
                entry["last_played"] = datetime.now(timezone.utc).isoformat()
                self._save_index()

    def register(self, video_id: str, file_path: str, size: int) -> None:
        """Register a completed download in the index."""
        with self._lock:
            self._index["entries"][video_id] = {
                "file": file_path,
                "size": size,
                "last_played": datetime.now(timezone.utc).isoformat(),
                "complete": True,
            }
            self._recompute_size()
            self._save_index()
            logger.info("Registered cached video", extra={
                "video_id": video_id, "size_mb": size // (1024 * 1024),
            })

    def remove(self, video_id: str) -> None:
        """Remove a cached video (file + index entry)."""
        with self._lock:
            entry = self._index["entries"].pop(video_id, None)
            if entry:
                try:
                    os.unlink(entry["file"])
                except FileNotFoundError:
                    pass
                self._recompute_size()
                self._save_index()
                logger.info("Removed cached video", extra={"video_id": video_id})

    def evict_for(self, needed_bytes: int, protect_ids: set[str]) -> int:
        """Free up space by evicting cached videos.

        Returns total bytes freed. Eviction priority:
        1. Incomplete downloads
        2. Videos not in playlist (in_playlist=False)
        3. LRU (least recently played)
        Never evicts IDs in protect_ids.
        """
        with self._lock:
            available = self._max_size - self._total_size
            if available >= needed_bytes:
                return 0

            freed = 0
            target = needed_bytes - available

            # Build eviction candidates sorted by priority
            candidates = []
            for vid, entry in list(self._index["entries"].items()):
                if vid in protect_ids:
                    continue
                priority = 2  # default: LRU
                if not entry.get("complete"):
                    priority = 0  # incomplete first
                elif entry.get("in_playlist") is False:
                    priority = 1  # removed from playlist
                last = entry.get("last_played") or ""
                candidates.append((priority, last, vid, entry))

            candidates.sort(key=lambda x: (x[0], x[1]))

            for _, _, vid, entry in candidates:
                if freed >= target:
                    break
                size = entry.get("size", 0)
                try:
                    os.unlink(entry["file"])
                except FileNotFoundError:
                    pass
                del self._index["entries"][vid]
                freed += size
                logger.info("Evicted cached video", extra={"video_id": vid, "freed_mb": size // (1024 * 1024)})

            self._recompute_size()
            self._save_index()
            return freed

    def cleanup(self, current_playlist_ids: set[str]) -> None:
        """Mark videos not in the current playlist for priority eviction."""
        with self._lock:
            for vid, entry in self._index["entries"].items():
                entry["in_playlist"] = vid in current_playlist_ids
            self._save_index()

    def get_stats(self) -> dict:
        """Return cache statistics."""
        with self._lock:
            return {
                "total_size": self._total_size,
                "total_size_mb": self._total_size // (1024 * 1024),
                "count": len(self._index["entries"]),
                "max_size": self._max_size,
                "max_size_mb": self._max_size // (1024 * 1024),
                "hits": self._hits,
                "misses": self._misses,
            }

    # --- Internal ---

    def _load_index(self) -> None:
        """Load index from disk, or start fresh."""
        index_path = os.path.join(self._cache_path, _INDEX_FILE)
        if os.path.isfile(index_path):
            try:
                with open(index_path) as f:
                    data = json.load(f)
                if data.get("version") == _INDEX_VERSION:
                    self._index = data
                    logger.debug("Loaded cache index", extra={"entries": len(data.get("entries", {}))})
                else:
                    logger.warning("Cache index version mismatch, starting fresh")
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupt cache index, starting fresh")

    def _save_index(self) -> None:
        """Atomically write index to disk (temp file + rename)."""
        index_path = os.path.join(self._cache_path, _INDEX_FILE)
        fd, tmp_path = tempfile.mkstemp(dir=self._cache_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._index, f, indent=2)
            os.replace(tmp_path, index_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    def _reconcile(self) -> None:
        """Reconcile index with filesystem on startup.

        - Remove index entries whose files are missing
        - Add index entries for .mp4 files not in the index
        - Delete orphan .part files
        """
        # Clean up orphan .part files
        for name in os.listdir(self._cache_path):
            path = os.path.join(self._cache_path, name)
            if name.endswith(".part"):
                logger.debug("Removing orphan .part file", extra={"path": path})
                os.unlink(path)

        # Remove entries for missing files
        for vid in list(self._index["entries"]):
            entry = self._index["entries"][vid]
            if not os.path.isfile(entry.get("file", "")):
                logger.debug("Removing index entry for missing file", extra={"video_id": vid})
                del self._index["entries"][vid]

        # Add entries for untracked .mp4 files
        for name in os.listdir(self._cache_path):
            if not name.endswith(".mp4"):
                continue
            video_id = name[:-4]  # strip .mp4
            if video_id not in self._index["entries"]:
                path = os.path.join(self._cache_path, name)
                size = os.path.getsize(path)
                self._index["entries"][video_id] = {
                    "file": path,
                    "size": size,
                    "last_played": None,
                    "complete": True,
                }
                logger.debug("Indexed untracked video", extra={"video_id": video_id, "size": size})

        self._save_index()

    def _recompute_size(self) -> None:
        """Recompute total cached size from index."""
        self._total_size = sum(
            e.get("size", 0) for e in self._index["entries"].values()
        )
