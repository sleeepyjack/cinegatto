"""CacheService — unified cache index + background downloader.

Architecture: a single worker thread processes downloads sequentially from a
queue. Only one yt-dlp subprocess runs at a time, which avoids saturating the
Pi's bandwidth and disk I/O. The controller and API threads interact via a
small public API:

  get(video_id) → path or None  (instant, sync — index lookup under lock)
  warm(video_id, url)           (async, enqueues download if not cached/queued)
  warm_all(entries)             (async, enqueues all uncached videos)
  cleanup(playlist_ids)         (sync, marks removed videos for priority eviction)
  get_stats() → dict            (sync, returns counters snapshot)

The cache index (cache.json) is the source of truth for what's cached. It's
persisted to disk on every mutation via atomic rename (write to .tmp, then
os.replace) to avoid corruption from crashes.

On startup, _reconcile() handles three recovery cases:
  1. Partial downloads (.part/.ytdl files) left from a crash — deleted.
  2. Index entries whose files are missing — removed from index.
  3. .mp4 files on disk not in the index — re-added (manual file drops work).

Eviction strategy (_evict_for): when the cache is full and a new download
completes, we evict in priority order:
  0 = incomplete entries (should not normally exist)
  1 = videos no longer in the playlist (stale)
  2 = active playlist videos, sorted by LRU (least recently played first)
The currently-downloading video is protected from eviction via protect_ids.
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
# Brief pause between downloads to be a good citizen and reduce the chance
# of YouTube rate-limiting or CAPTCHA-gating yt-dlp requests.
_DOWNLOAD_GAP = 2
_MAX_RETRIES = 3
_RETRY_DELAYS = [300, 900, 1800]  # 5 min, 15 min, 30 min


class CacheService:
    """Unified video cache with background downloads."""

    def __init__(self, cache_path: str, max_size_bytes: int, format_str: str):
        self._cache_path = cache_path
        self._max_size = max_size_bytes
        self._format = format_str

        # Protects the index dict, size counter, and hit/miss counters.
        # Held briefly for reads (get) and writes (post-download registration).
        self._lock = threading.Lock()
        self._index = {"version": _INDEX_VERSION, "entries": {}}
        self._total_size = 0
        self._hits = 0
        self._misses = 0

        self._download_queue: queue.Queue = queue.Queue()
        # _queued_ids tracks video IDs currently in the download queue (not yet
        # started or in progress). Separate from _lock because warm() checks
        # this from multiple threads and we don't want to hold the main lock.
        self._queued_ids: set[str] = set()
        self._queued_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._current_proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._running = False
        self._index_dirty = False
        self._last_error: Optional[dict] = None
        self._downloads_completed = 0
        self._downloads_failed = 0
        self._current_download_id: Optional[str] = None

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
        # Flush any dirty index state (e.g., last_played updates from get())
        with self._lock:
            if self._index_dirty:
                self._save_index()
                self._index_dirty = False
        logger.info("CacheService stopped")

    # --- Public API (called from any thread) ---

    def get(self, video_id: str) -> Optional[str]:
        """Return cached file path if available, else None. Counts hit/miss.

        Checks both the index AND the filesystem (os.path.isfile) because the
        file could have been deleted externally. Updates last_played in memory
        (not flushed to disk here — avoids SD card write on every playback hit).
        Index is persisted on downloads, evictions, and shutdown.
        """
        with self._lock:
            entry = self._index["entries"].get(video_id)
            if entry and entry.get("complete") and os.path.isfile(entry["file"]):
                self._hits += 1
                entry["last_played"] = datetime.now(timezone.utc).isoformat()
                self._index_dirty = True
                return entry["file"]
            self._misses += 1
            return None

    def contains(self, video_id: str) -> bool:
        """Check if video is cached (no hit/miss counting)."""
        with self._lock:
            entry = self._index["entries"].get(video_id)
            return bool(entry and entry.get("complete") and os.path.isfile(entry.get("file", "")))

    def warm(self, video_id: str, url: str) -> None:
        """Queue a video for background download (if not already cached/queued).

        Three-layer dedup: contains() checks the index, _queued_ids catches
        videos waiting in the download queue, and _download() double-checks
        before actually downloading (in case caching completed between enqueue
        and execution).
        """
        if self.contains(video_id):
            logger.debug("Already cached, skipping warm", extra={"video_id": video_id})
            return
        with self._queued_lock:
            if video_id in self._queued_ids:
                return
            self._queued_ids.add(video_id)
        self._download_queue.put(("download", video_id, url))
        logger.debug("Queued for caching", extra={"video_id": video_id})

    def _enqueue_retry(self, video_id: str, url: str, retry_count: int) -> None:
        """Re-enqueue a failed download after a delay (called via Timer)."""
        if not self._running or self.contains(video_id):
            return
        with self._queued_lock:
            if video_id in self._queued_ids:
                return
            self._queued_ids.add(video_id)
        self._download_queue.put(("download", video_id, url, retry_count))
        logger.debug("Retry enqueued", extra={"video_id": video_id, "retry": retry_count})

    def warm_all(self, entries: list[dict]) -> dict:
        """Queue all uncached videos for download. Returns outcome counts."""
        enqueued = 0
        already_cached = 0
        already_queued = 0
        for entry in entries:
            if self.contains(entry["id"]):
                already_cached += 1
            else:
                with self._queued_lock:
                    if entry["id"] in self._queued_ids:
                        already_queued += 1
                        continue
                self.warm(entry["id"], entry["url"])
                enqueued += 1
        result = {
            "enqueued": enqueued, "already_cached": already_cached,
            "already_queued": already_queued, "total": len(entries),
        }
        logger.info("Warm all requested", extra=result)
        return result

    def cleanup(self, current_playlist_ids: set[str]) -> None:
        """Mark videos not in the playlist for priority eviction.

        Does NOT delete files immediately — eviction happens lazily when space
        is needed during _download(). This avoids deleting a video that the
        user might re-add to the playlist later.
        """
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
                "downloads_completed": self._downloads_completed,
                "downloads_failed": self._downloads_failed,
                "queue_depth": self._download_queue.qsize(),
                "current_download": self._current_download_id,
                "last_error": self._last_error,
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
            # Queue items: ("download", video_id, url) or ("download", video_id, url, retry_count)
            _, video_id, url = item[0], item[1], item[2]
            retry_count = item[3] if len(item) > 3 else 0
            with self._lock:
                self._current_download_id = video_id
            # _download returns: "ok", "fail" (retryable), "skip" (permanent)
            result = "fail"
            try:
                result = self._download(video_id, url)
            except Exception:
                with self._lock:
                    self._downloads_failed += 1
                logger.exception("Download failed", extra={"video_id": video_id})
            finally:
                with self._lock:
                    self._current_download_id = None
                with self._queued_lock:
                    self._queued_ids.discard(video_id)
                self._download_queue.task_done()
            # Retry only transient failures, not permanent ones (e.g., too large for cache)
            if result == "fail" and self._running and retry_count < _MAX_RETRIES:
                delay = _RETRY_DELAYS[retry_count]
                logger.debug("Will retry download in %ds",
                             delay, extra={"video_id": video_id, "retry": retry_count + 1})
                t = threading.Timer(delay, self._enqueue_retry,
                                    args=(video_id, url, retry_count + 1))
                t.daemon = True
                t.start()
            # Brief gap between downloads
            if self._running:
                time.sleep(_DOWNLOAD_GAP)

    def _download(self, video_id: str, url: str) -> str:
        """Download a video. Returns 'ok', 'fail' (retryable), or 'skip' (permanent)."""
        if not self._running:
            return "skip"
        if self.contains(video_id):
            logger.debug("Already cached, skipping download", extra={"video_id": video_id})
            return "ok"

        part_path = os.path.join(self._cache_path, f"{video_id}.part")
        final_path = os.path.join(self._cache_path, f"{video_id}.mp4")

        yt_dlp_bin = shutil.which("yt-dlp")
        venv_bin = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
        if os.path.isfile(venv_bin):
            yt_dlp_bin = venv_bin
        yt_dlp_cmd = yt_dlp_bin or "yt-dlp"

        # Pre-check: ask yt-dlp for the file size without downloading.
        # Skips the download entirely if the video is larger than the entire cache,
        # saving bandwidth and time on the Pi's limited connection.
        estimated = self._estimate_size(yt_dlp_cmd, url)
        if estimated and estimated > self._max_size:
            logger.warning("Video too large for cache, skipping download",
                           extra={"video_id": video_id,
                                  "estimated_mb": estimated // (1024 * 1024),
                                  "max_mb": self._max_size // (1024 * 1024)})
            return "skip"  # permanent — retrying won't help

        logger.info("Downloading", extra={
            "video_id": video_id,
            "estimated_mb": estimated // (1024 * 1024) if estimated else "unknown",
        })

        cmd = [
            yt_dlp_cmd,
            "-f", self._format,
            "-o", part_path,
            "--merge-output-format", "mp4",
            "--no-warnings", "--quiet",
        ]
        cmd.append(url)

        try:
            with self._proc_lock:
                if not self._running:
                    return "skip"
                self._current_proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
            proc = self._current_proc
            returncode = proc.wait()
            with self._proc_lock:
                self._current_proc = None

            if returncode != 0:
                stderr = ""
                try:
                    stderr = proc.stderr.read().decode(errors="replace")[:500]
                except Exception:
                    pass
                with self._lock:
                    self._last_error = {
                        "video_id": video_id, "exit_code": returncode,
                        "stderr": stderr, "at": datetime.now(timezone.utc).isoformat(),
                    }
                    self._downloads_failed += 1
                logger.warning("yt-dlp exited with code %d", returncode,
                               extra={"video_id": video_id, "stderr": stderr})
                self._cleanup_part_files(video_id)
                return "fail"  # retryable — network/transient error
        except Exception:
            with self._proc_lock:
                self._current_proc = None
            self._cleanup_part_files(video_id)
            raise

        if not self._running:
            self._cleanup_part_files(video_id)
            return "skip"

        actual_path = self._find_output(part_path)
        if not actual_path:
            logger.warning("Download output not found", extra={"video_id": video_id})
            return "fail"

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
                    return "skip"  # permanent — video won't fit even after eviction

        # Atomic rename: the .part file becomes the final .mp4. os.replace is
        # atomic on POSIX, so a crash mid-rename won't leave a corrupt file.
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
            self._index_dirty = False
            self._downloads_completed += 1
            self._last_error = None  # clear stale error on success
        logger.info("Download complete", extra={
            "video_id": video_id, "size_mb": file_size // (1024 * 1024),
        })
        return "ok"

    # --- Eviction ---

    def _evict_for(self, needed_bytes: int, protect_ids: set[str]) -> int:
        """Free space. Must be called with self._lock held."""
        available = self._max_size - self._total_size
        if available >= needed_bytes:
            return 0

        freed = 0
        target = needed_bytes - available

        # Build eviction candidates with a priority tier + LRU timestamp.
        # Sort key: (priority, last_played) — lowest priority evicted first,
        # then oldest within each tier. Empty string for last_played sorts
        # before any ISO timestamp, so never-played videos evict first.
        candidates = []
        for vid, entry in list(self._index["entries"].items()):
            if vid in protect_ids:
                continue
            priority = 2  # default: active playlist video
            if not entry.get("complete"):
                priority = 0  # incomplete — always evict first
            elif entry.get("in_playlist") is False:
                priority = 1  # removed from playlist — evict before active
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
        """Persist index to disk via atomic write (tmpfile + os.replace).

        Must be called with self._lock held. Writing to a temp file in the
        same directory first ensures os.replace is an atomic rename on the
        same filesystem — a crash mid-write leaves the old index intact.
        """
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
        """Sync index with filesystem on startup. Handles three cases:
        1. Stale partial downloads (.part/.ytdl) from a crash — deleted.
        2. Index entries for files that no longer exist — removed from index.
        3. .mp4 files on disk not tracked in index — adopted into the index.
        """
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
        """Locate the actual output file after yt-dlp finishes.

        yt-dlp may produce different extensions depending on merge format and
        available codecs, so we check several possible suffixes.
        """
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

    def _estimate_size(self, yt_dlp_cmd: str, url: str) -> Optional[int]:
        """Estimate download size via yt-dlp --dump-json. Returns bytes or None."""
        try:
            result = subprocess.run(
                [yt_dlp_cmd, "-f", self._format, "--dump-json", "--no-download", url],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return None
            info = json.loads(result.stdout)
            # filesize is exact (if available), filesize_approx is estimate
            size = info.get("filesize") or info.get("filesize_approx")
            if size:
                return int(size)
            # Fallback: sum requested formats
            formats = info.get("requested_formats", [])
            if formats:
                total = sum(f.get("filesize") or f.get("filesize_approx") or 0 for f in formats)
                if total > 0:
                    return total
            return None
        except Exception:
            logger.debug("Could not estimate download size")
            return None
