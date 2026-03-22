"""Background video downloader using yt-dlp."""

import logging
import os
import queue
import subprocess
import shutil
import sys
import threading
from typing import Optional

from cinegatto.cache.manager import CacheManager

logger = logging.getLogger("cinegatto.cache.downloader")

_SENTINEL = object()


class Downloader:
    """Downloads videos in the background via yt-dlp subprocess, one at a time.

    Uses yt-dlp as a subprocess (not as a library) so downloads can be
    interrupted cleanly via process termination on shutdown.
    """

    def __init__(self, cache_manager: CacheManager, format_str: str,
                 cookies_from_browser: str = ""):
        self._cache = cache_manager
        self._format = format_str
        self._cookies_from_browser = cookies_from_browser
        self._queue: queue.Queue = queue.Queue()
        self._queued_ids: set[str] = set()
        self._queued_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._running = False
        self._current_proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._paused = threading.Event()
        self._paused.set()  # starts unpaused

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
        # Kill any in-progress download
        with self._proc_lock:
            if self._current_proc and self._current_proc.poll() is None:
                self._current_proc.terminate()
                try:
                    self._current_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._current_proc.kill()
        # Unblock worker if paused, then drain
        self._paused.set()
        self._queue.put(_SENTINEL)
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=5)
        logger.info("Downloader stopped")

    def pause(self) -> None:
        """Pause downloads — kills any in-progress download and blocks the worker."""
        logger.debug("Downloader pausing")
        self._paused.clear()
        with self._proc_lock:
            if self._current_proc and self._current_proc.poll() is None:
                self._current_proc.terminate()
                try:
                    self._current_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._current_proc.kill()

    def resume(self) -> None:
        """Resume downloads."""
        logger.debug("Downloader resuming")
        self._paused.set()

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
            if not self._running:
                self._queue.task_done()
                break
            # Wait if paused (e.g., while streaming a video)
            self._paused.wait()
            if not self._running:
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
        """Download a video via yt-dlp subprocess to the cache directory."""
        if not self._running:
            return

        # Double-check not already cached
        if self._cache.is_cached(video_id):
            logger.debug("Already cached, skipping download", extra={"video_id": video_id})
            return

        cache_path = self._cache._cache_path
        part_path = os.path.join(cache_path, f"{video_id}.part")
        final_path = os.path.join(cache_path, f"{video_id}.mp4")

        logger.info("Downloading video", extra={"video_id": video_id, "url": url})

        # Find yt-dlp binary — prefer the one in our venv
        yt_dlp_bin = shutil.which("yt-dlp")
        venv_bin = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
        if os.path.isfile(venv_bin):
            yt_dlp_bin = venv_bin

        cmd = [
            yt_dlp_bin or "yt-dlp",
            "-f", self._format,
            "-o", part_path,
            "--merge-output-format", "mp4",
            "--no-warnings",
            "--quiet",
        ]
        if self._cookies_from_browser:
            cmd.extend(["--cookies-from-browser", self._cookies_from_browser])
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
                logger.warning("yt-dlp exited with code %d", returncode, extra={"stderr": stderr})
                self._cleanup_part_files(video_id, cache_path)
                return

        except Exception:
            with self._proc_lock:
                self._current_proc = None
            self._cleanup_part_files(video_id, cache_path)
            raise

        if not self._running:
            self._cleanup_part_files(video_id, cache_path)
            return

        # Find the actual output file (yt-dlp may add extensions)
        actual_path = self._find_output(part_path)
        if not actual_path:
            logger.warning("Downloaded file not found", extra={"video_id": video_id})
            return

        file_size = os.path.getsize(actual_path)

        # Size guard
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

    def _find_output(self, part_path: str) -> Optional[str]:
        """Find the actual output file — yt-dlp may append extensions."""
        for candidate in [part_path, part_path + ".mp4", part_path + ".mkv",
                          part_path + ".webm"]:
            if os.path.isfile(candidate):
                return candidate
        # Also check if yt-dlp already merged to the name without .part
        base = part_path.replace(".part", ".mp4")
        if os.path.isfile(base):
            return base
        return None

    def _cleanup_part_files(self, video_id: str, cache_path: str) -> None:
        """Remove any partial download files."""
        for name in os.listdir(cache_path):
            if name.startswith(video_id) and (".part" in name or name.endswith(".ytdl")):
                try:
                    os.unlink(os.path.join(cache_path, name))
                except FileNotFoundError:
                    pass
