"""Background video downloader using yt-dlp."""

import logging
import os
import queue
import threading
from typing import Optional

import yt_dlp

from cinegatto.cache.manager import CacheManager

logger = logging.getLogger("cinegatto.cache.downloader")

_SENTINEL = object()


class Downloader:
    """Downloads videos in the background via yt-dlp, one at a time."""

    def __init__(self, cache_manager: CacheManager, format_str: str):
        self._cache = cache_manager
        self._format = format_str
        self._queue: queue.Queue = queue.Queue()
        self._queued_ids: set[str] = set()
        self._queued_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True, name="cache-downloader"
        )
        self._worker.start()
        logger.info("Downloader started")

    def stop(self) -> None:
        logger.info("Downloader stopping")
        self._running = False
        self._queue.put(_SENTINEL)
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=10)
        logger.info("Downloader stopped")

    def enqueue(self, video_id: str, url: str) -> None:
        """Add a video to the download queue. Skips if already cached or queued."""
        if self._cache.is_cached(video_id):
            return
        with self._queued_lock:
            if video_id in self._queued_ids:
                return
            self._queued_ids.add(video_id)
        self._queue.put((video_id, url))
        logger.debug("Enqueued download", extra={"video_id": video_id})

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                self._queue.task_done()
                break
            video_id, url = item
            try:
                self._download(video_id, url)
            except Exception:
                logger.exception("Download failed", extra={"video_id": video_id})
            finally:
                with self._queued_lock:
                    self._queued_ids.discard(video_id)
                self._queue.task_done()

    def _download(self, video_id: str, url: str) -> None:
        """Download a video via yt-dlp to the cache directory."""
        # Double-check not already cached (may have been cached while queued)
        if self._cache.is_cached(video_id):
            logger.debug("Already cached, skipping download", extra={"video_id": video_id})
            return

        cache_path = self._cache._cache_path
        part_path = os.path.join(cache_path, f"{video_id}.part")
        final_path = os.path.join(cache_path, f"{video_id}.mp4")

        logger.info("Downloading video", extra={"video_id": video_id, "url": url})

        ydl_opts = {
            "format": self._format,
            "outtmpl": part_path,
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception:
            # Clean up partial file on error
            for f in [part_path, part_path + ".part"]:
                try:
                    os.unlink(f)
                except FileNotFoundError:
                    pass
            raise

        # yt-dlp may have created the file with .part extension or merged to .part
        # Find the actual output file
        actual_path = part_path
        if not os.path.isfile(actual_path):
            # yt-dlp sometimes adds .mp4 to the template
            for candidate in [part_path + ".mp4", part_path]:
                if os.path.isfile(candidate):
                    actual_path = candidate
                    break
            else:
                logger.warning("Downloaded file not found", extra={"video_id": video_id})
                return

        file_size = os.path.getsize(actual_path)

        # Size guard: check if this fits in cache (after eviction)
        stats = self._cache.get_stats()
        space_needed = file_size - (stats["max_size"] - stats["total_size"])
        if space_needed > 0:
            freed = self._cache.evict_for(space_needed, protect_ids=set())
            if freed < space_needed:
                logger.warning("Video too large for cache, discarding",
                               extra={"video_id": video_id, "size_mb": file_size // (1024 * 1024)})
                os.unlink(actual_path)
                return

        # Atomic move to final path
        os.replace(actual_path, final_path)
        self._cache.register(video_id, final_path, file_size)
        logger.info("Download complete", extra={
            "video_id": video_id, "size_mb": file_size // (1024 * 1024),
        })
