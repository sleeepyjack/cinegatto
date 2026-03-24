"""CacheService — unified cache index + background downloader.

Single service thread handles all cache operations. Downloads run one
at a time, always (no pause/resume logic). The controller interacts
via simple sync/async methods:

  get(video_id) → path or None  (instant, sync)
  warm(video_id, url)           (async, queues download)
  warm_all(entries)             (async, queues all uncached)
  cleanup(playlist_ids)         (async, marks removed videos)
  get_stats() → dict            (sync)
"""

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("cinegatto.cache")

_INDEX_FILE = "cache.json"
_INDEX_VERSION = 1
_SENTINEL = object()
_DOWNLOAD_GAP = 2  # seconds between downloads to avoid hammering YouTube


class CacheService:
    """Unified video cache with background downloads."""

    def __init__(self, cache_path: str, max_size_bytes: int, format_str: str):
        self._cache_path = cache_path
        self._max_size = max_size_bytes
        self._format = format_str

        self._lock = threading.Lock()
        self._index = {"version": _INDEX_VERSION, "entries": {}}
        self._total_size = 0
        self._hits = 0
        self._misses = 0

        self._download_queue: queue.Queue = queue.Queue()
        self._queued_ids: set[str] = set()
        self._queued_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._current_proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._running = False

        os.makedirs(cache_path, exist_ok=True)
        self._load_index()
        self._reconcile()
        self._recompute_size()
        logger.info("CacheService initialized", extra={
            "path": cache_path, "max_size_mb": max_size_bytes // (1024 * 1024),
            "cached_videos": len(self._index["entries"]),
            "total_size_mb": self._total_size // (1024 * 1024),
        })

    def start(self) -> None:
        """Start the background download worker."""
        self._running = True
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True, name="cache-service"
        )
        self._worker.start()

    def stop(self) -> None:
        """Stop the service, killing any in-progress download."""
        logger.info("CacheService stopping")
        self._running = False
        # Kill active download
        with self._proc_lock:
            if self._current_proc and self._current_proc.poll() is None:
                self._current_proc.terminate()
                try:
                    self._current_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._current_proc.kill()
        self._download_queue.put(_SENTINEL)
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=5)
        logger.info("CacheService stopped")

    # --- Public API (called from any thread) ---

    def get(self, video_id: str) -> Optional[str]:
        """Return cached file path if available, else None. Counts hit/miss."""
        with self._lock:
            entry = self._index["entries"].get(video_id)
            if entry and entry.get("complete") and os.path.isfile(entry["file"]):
                self._hits += 1
                entry["last_played"] = datetime.now(timezone.utc).isoformat()
                self._save_index()
                return entry["file"]
            self._misses += 1
            return None

    def contains(self, video_id: str) -> bool:
        """Check if video is cached (no hit/miss counting)."""
        with self._lock:
            entry = self._index["entries"].get(video_id)
            return bool(entry and entry.get("complete") and os.path.isfile(entry.get("file", "")))

    def warm(self, video_id: str, url: str) -> None:
        """Queue a video for background download (if not already cached/queued)."""
        if self.contains(video_id):
            return
        with self._queued_lock:
            if video_id in self._queued_ids:
                return
            self._queued_ids.add(video_id)
        self._download_queue.put(("download", video_id, url))
        logger.debug("Queued for caching", extra={"video_id": video_id})

    def warm_all(self, entries: list[dict]) -> None:
        """Queue all uncached videos for download."""
        enqueued = 0
        for entry in entries:
            if not self.contains(entry["id"]):
                self.warm(entry["id"], entry["url"])
                enqueued += 1
        logger.info("Warm all requested", extra={"enqueued": enqueued, "total": len(entries)})

    def cleanup(self, current_playlist_ids: set[str]) -> None:
        """Mark videos not in the playlist for priority eviction."""
        with self._lock:
            for vid, entry in self._index["entries"].items():
                entry["in_playlist"] = vid in current_playlist_ids
            self._save_index()

    def get_stats(self) -> dict:
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

    # --- Download worker ---

    def _worker_loop(self) -> None:
        while True:
            item = self._download_queue.get()
            if item is _SENTINEL:
                self._download_queue.task_done()
                break
            if not self._running:
                self._download_queue.task_done()
                break
            _, video_id, url = item
            try:
                self._download(video_id, url)
            except Exception:
                logger.exception("Download failed", extra={"video_id": video_id})
            finally:
                with self._queued_lock:
                    self._queued_ids.discard(video_id)
                self._download_queue.task_done()
            # Brief gap between downloads
            if self._running:
                time.sleep(_DOWNLOAD_GAP)

    def _download(self, video_id: str, url: str) -> None:
        if not self._running:
            return
        if self.contains(video_id):
            logger.debug("Already cached, skipping", extra={"video_id": video_id})
            return

        part_path = os.path.join(self._cache_path, f"{video_id}.part")
        final_path = os.path.join(self._cache_path, f"{video_id}.mp4")

        logger.info("Downloading", extra={"video_id": video_id})

        yt_dlp_bin = shutil.which("yt-dlp")
        venv_bin = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
        if os.path.isfile(venv_bin):
            yt_dlp_bin = venv_bin

        cmd = [
            yt_dlp_bin or "yt-dlp",
            "-f", self._format,
            "-o", part_path,
            "--merge-output-format", "mp4",
            "--no-warnings", "--quiet",
        ]
        cmd.append(url)

        try:
            with self._proc_lock:
                if not self._running:
                    return
                self._current_proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
            returncode = self._current_proc.wait()
            with self._proc_lock:
                self._current_proc = None

            if returncode != 0:
                stderr = ""
                try:
                    stderr = self._current_proc.stderr.read().decode(errors="replace")[:500]
                except Exception:
                    pass
                logger.warning("yt-dlp exited with code %d", returncode,
                               extra={"video_id": video_id, "stderr": stderr})
                self._cleanup_part_files(video_id)
                return
        except Exception:
            with self._proc_lock:
                self._current_proc = None
            self._cleanup_part_files(video_id)
            raise

        if not self._running:
            self._cleanup_part_files(video_id)
            return

        actual_path = self._find_output(part_path)
        if not actual_path:
            logger.warning("Download output not found", extra={"video_id": video_id})
            return

        file_size = os.path.getsize(actual_path)

        # Evict if needed
        with self._lock:
            space_needed = file_size - (self._max_size - self._total_size)
            if space_needed > 0:
                freed = self._evict_for(space_needed, protect_ids={video_id})
                if freed < space_needed:
                    logger.warning("Video too large for cache",
                                   extra={"video_id": video_id, "size_mb": file_size // (1024 * 1024)})
                    os.unlink(actual_path)
                    return

        os.replace(actual_path, final_path)
        with self._lock:
            self._index["entries"][video_id] = {
                "file": final_path,
                "size": file_size,
                "last_played": datetime.now(timezone.utc).isoformat(),
                "complete": True,
            }
            self._recompute_size()
            self._save_index()
        logger.info("Download complete", extra={
            "video_id": video_id, "size_mb": file_size // (1024 * 1024),
        })

    # --- Eviction ---

    def _evict_for(self, needed_bytes: int, protect_ids: set[str]) -> int:
        """Free space. Must be called with self._lock held."""
        available = self._max_size - self._total_size
        if available >= needed_bytes:
            return 0

        freed = 0
        target = needed_bytes - available

        candidates = []
        for vid, entry in list(self._index["entries"].items()):
            if vid in protect_ids:
                continue
            priority = 2
            if not entry.get("complete"):
                priority = 0
            elif entry.get("in_playlist") is False:
                priority = 1
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
            logger.info("Evicted", extra={"video_id": vid, "freed_mb": size // (1024 * 1024)})

        self._recompute_size()
        self._save_index()
        return freed

    # --- Index persistence ---

    def _load_index(self) -> None:
        index_path = os.path.join(self._cache_path, _INDEX_FILE)
        if os.path.isfile(index_path):
            try:
                with open(index_path) as f:
                    data = json.load(f)
                if data.get("version") == _INDEX_VERSION:
                    self._index = data
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupt cache index, starting fresh")

    def _save_index(self) -> None:
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
        for name in os.listdir(self._cache_path):
            path = os.path.join(self._cache_path, name)
            if name.endswith(".part") or name.endswith(".ytdl"):
                os.unlink(path)

        for vid in list(self._index["entries"]):
            entry = self._index["entries"][vid]
            if not os.path.isfile(entry.get("file", "")):
                del self._index["entries"][vid]

        for name in os.listdir(self._cache_path):
            if not name.endswith(".mp4"):
                continue
            video_id = name[:-4]
            if video_id not in self._index["entries"]:
                path = os.path.join(self._cache_path, name)
                size = os.path.getsize(path)
                self._index["entries"][video_id] = {
                    "file": path, "size": size,
                    "last_played": None, "complete": True,
                }

        self._save_index()

    def _recompute_size(self) -> None:
        self._total_size = sum(
            e.get("size", 0) for e in self._index["entries"].values()
        )

    # --- Helpers ---

    def _find_output(self, part_path: str) -> Optional[str]:
        for candidate in [part_path, part_path + ".mp4", part_path + ".mkv",
                          part_path + ".webm"]:
            if os.path.isfile(candidate):
                return candidate
        base = part_path.replace(".part", ".mp4")
        if os.path.isfile(base):
            return base
        return None

    def _cleanup_part_files(self, video_id: str) -> None:
        for name in os.listdir(self._cache_path):
            if name.startswith(video_id) and (".part" in name or name.endswith(".ytdl")):
                try:
                    os.unlink(os.path.join(self._cache_path, name))
                except FileNotFoundError:
                    pass
