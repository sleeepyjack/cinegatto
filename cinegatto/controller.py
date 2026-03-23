"""PlaybackController — serializes all player commands via a single worker thread."""

import logging
import queue
import random
import threading
import time
from typing import Any, Optional

from cinegatto.player.types import Player, PlayerState

logger = logging.getLogger("cinegatto.controller")

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

    def on_video_end(self) -> None:
        self._queue.put(("next",))

    def set_shuffle(self, enabled: bool) -> None:
        self._selector._shuffle = enabled
        logger.info("Shuffle set", extra={"shuffle": enabled})

    def set_random_start(self, enabled: bool) -> None:
        self._random_start = enabled
        logger.info("Random start set", extra={"random_start": enabled})

    def get_settings(self) -> dict:
        return {
            "shuffle": self._selector._shuffle,
            "random_start": self._random_start,
        }

    def get_status(self) -> dict:
        return self._player.get_state().to_dict()

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
        else:
            logger.warning("Unknown command: %s", action)

    def _do_play(self) -> None:
        logger.info("Executing play")
        self._display.power_on()
        time.sleep(0.05)
        self._player.play()

    def _do_pause(self) -> None:
        logger.info("Executing pause")
        self._player.pause()
        time.sleep(0.05)
        self._display.power_off()

    def _do_next(self) -> None:
        video = self._selector.pick()
        logger.info("Playing next video", extra={"video_id": video["id"], "title": video["title"]})
        self._load_video(video)

    def _do_previous(self) -> None:
        video = self._selector.previous()
        if video is None:
            logger.info("No previous video in history")
            return
        logger.info("Playing previous video", extra={"video_id": video["id"], "title": video["title"]})
        self._load_video(video)

    def _load_video(self, video: dict) -> None:
        """Load a video — from cache if available, else stream from YouTube."""
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
            for entry in self._selector.peek_next(n=1):
                self._cache.warm(entry["id"], entry["url"])
