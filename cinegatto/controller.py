"""PlaybackController — serializes all player commands via a single worker thread.

Design: all state-mutating operations (play, pause, next, previous, seek) are
enqueued as tuples and executed sequentially by a single worker thread. This
eliminates race conditions between the API (Flask threads), the player's
on_video_end callback (mpv IPC reader thread), and the playlist refresh thread
— none of them touch the player directly.

Read-only queries (get_status, get_settings) bypass the queue and read directly
from the player/selector, which is safe because those reads are atomic or
protected by their own locks.

The command queue pattern also means the API never blocks waiting for mpv IPC
round-trips; it just enqueues and returns immediately.
"""

import logging
import queue
import random
import threading
import time
from typing import Any, Optional

from cinegatto.player.types import Player, PlayerState

logger = logging.getLogger("cinegatto.controller")

# Unique object used to signal the worker thread to exit. Using object()
# instead of None avoids any chance of collision with a valid command tuple.
_SENTINEL = object()


class PlaybackController:
    """Coordinates player, selector, display, and cache via a serialized command queue.

    All mutations go through the queue. Status reads are non-blocking.
    """

    def __init__(self, player, selector, display, random_start: bool = True,
                 cache_service=None):
        self._player = player
        self._selector = selector
        self._display = display
        self._random_start = random_start
        self._cache = cache_service
        self._queue: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="playback-controller"
        )
        self._worker_thread.start()
        logger.info("PlaybackController started")

    def stop(self) -> None:
        logger.info("PlaybackController stopping")
        self._running = False
        self._queue.put(_SENTINEL)
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)
        logger.info("PlaybackController stopped")

    def play(self) -> None:
        self._queue.put(("play",))

    def pause(self) -> None:
        self._queue.put(("pause",))

    def next_video(self) -> None:
        self._queue.put(("next",))

    def previous_video(self) -> None:
        self._queue.put(("previous",))

    def random_seek(self) -> None:
        self._queue.put(("random_seek",))

    def on_video_end(self) -> None:
        self._queue.put(("next",))

    def set_shuffle(self, enabled: bool) -> None:
        self._selector.set_shuffle(enabled)

    def set_random_start(self, enabled: bool) -> None:
        self._random_start = enabled
        logger.info("Random start set", extra={"random_start": enabled})

    def get_settings(self) -> dict:
        return {
            "shuffle": self._selector.get_shuffle(),
            "random_start": self._random_start,
        }

    def get_playlist_entries(self) -> list[dict]:
        """Public accessor for playlist entries (used by API sync)."""
        return self._selector.get_all_entries()

    def update_playlist(self, entries: list[dict]) -> None:
        """Update playlist entries (used by API sync)."""
        self._selector.update_entries(entries)

    def get_status(self) -> dict:
        status = self._player.get_state().to_dict()
        status["playlist_size"] = len(self._selector.get_all_entries())
        status["playlist_position"] = self._selector.get_current_index()
        return status

    # --- Worker ---

    def _worker_loop(self) -> None:
        while True:
            cmd = self._queue.get()
            if cmd is _SENTINEL:
                self._queue.task_done()
                break
            try:
                self._dispatch(cmd)
            except Exception:
                logger.exception("Error executing command: %s", cmd[0])
            finally:
                self._queue.task_done()

    def _dispatch(self, cmd: tuple) -> None:
        action = cmd[0]
        if action == "play":
            self._do_play()
        elif action == "pause":
            self._do_pause()
        elif action == "next":
            self._do_next()
        elif action == "previous":
            self._do_previous()
        elif action == "random_seek":
            self._do_random_seek()
        else:
            logger.warning("Unknown command: %s", action)

    def _do_play(self) -> None:
        logger.info("Executing play")
        # Order: power on display first, brief delay for HDMI signal to stabilize,
        # then un-black the video and unpause. Reversing this would show a flash
        # of video on a monitor that's still waking up.
        self._display.power_on()
        time.sleep(0.05)
        self._player.show_video(True)
        self._player.play()

    def _do_pause(self) -> None:
        logger.info("Executing pause")
        # Order: pause playback, black out video (so the last frame isn't frozen
        # on screen during HDMI signal loss), brief delay, then power off display.
        # Inverse of _do_play.
        self._player.pause()
        self._player.show_video(False)
        time.sleep(0.05)
        self._display.power_off()

    def _do_next(self) -> None:
        video = self._pick_cached_or_next()
        logger.info("Playing next video", extra={"video_id": video["id"], "title": video["title"]})
        self._load_video(video)

    def _pick_cached_or_next(self) -> dict:
        """Pick next video, preferring cached ones when uncached would fail.

        If the selector's pick is cached, use it. If not, check whether ANY
        cached video exists — if so, fall back to a random cached one. This
        prevents infinite error loops when the network is down but the cache
        has content. Only falls through to an uncached pick if nothing is cached.
        """
        video = self._selector.pick()
        if not self._cache or self._cache.contains(video["id"]):
            return video
        # Selected video is not cached — try to find any cached fallback
        cached_entries = [e for e in self._selector.get_all_entries()
                         if self._cache.contains(e["id"])]
        if cached_entries:
            fallback = random.choice(cached_entries)
            logger.info("Falling back to cached video",
                        extra={"video_id": fallback["id"], "original": video["id"]})
            return fallback
        return video

    def _do_previous(self) -> None:
        video = self._selector.previous()
        if video is None:
            logger.info("No previous video in history")
            return
        logger.info("Playing previous video", extra={"video_id": video["id"], "title": video["title"]})
        self._load_video(video)

    def _load_video(self, video: dict) -> None:
        """Load a video — from cache if available, else stream from YouTube.

        Cache interaction pattern:
        - get() is synchronous and instant (index lookup + file existence check).
        - warm() is fire-and-forget; it enqueues a background download. The cache
          service deduplicates, so calling warm() on an already-cached or
          already-queued video is a no-op.
        - We also pre-warm the next video (peek_next) so it's likely cached
          by the time the current one finishes.
        """
        # Cap at 80% to avoid starting near the end where the video might be
        # credits or a fade-out.
        start_percent = None
        if self._random_start:
            start_percent = random.uniform(0, 80.0)
            logger.info("Random start", extra={"start_percent": round(start_percent, 1)})

        cached_path = self._cache.get(video["id"]) if self._cache else None

        if cached_path:
            logger.info("Cache HIT", extra={"video_id": video["id"]})
            self._player.load_video(cached_path, start_percent=start_percent)
        else:
            logger.info("Cache MISS — streaming", extra={"video_id": video["id"]})
            self._player.load_video(video["url"], start_percent=start_percent)

        # Always request caching (service handles dedup/queue)
        if self._cache:
            self._cache.warm(video["id"], video["url"])
            # Pre-warm the next video so it's ready when the current one ends
            for entry in self._selector.peek_next(n=1):
                self._cache.warm(entry["id"], entry["url"])

    def _do_random_seek(self) -> None:
        """Seek to a random position in the current video (always, ignores random_start setting)."""
        try:
            state = self._player.get_state()
            if state.duration > 0:
                pos = random.uniform(0, state.duration * 0.8)
                logger.info("Random seek", extra={"position": round(pos, 1), "duration": round(state.duration, 1)})
                self._player.seek(pos)
            else:
                logger.debug("Cannot random seek — no duration available")
        except Exception:
            logger.debug("Cannot random seek — player may be idle")
