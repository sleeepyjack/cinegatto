"""Selector — random video selection with history for previous support."""

import collections
import logging
import random
import threading
from typing import Optional

logger = logging.getLogger("cinegatto.playlist.selector")


class Selector:
    """Picks random videos from a playlist and maintains play history."""

    def __init__(self, entries: list[dict], history_size: int = 50):
        self._entries = list(entries)
        self._history = collections.deque(maxlen=history_size)
        self._current: Optional[dict] = None
        self._lock = threading.Lock()

    def pick(self) -> dict:
        """Pick a random video from the playlist."""
        with self._lock:
            if not self._entries:
                raise ValueError("Cannot pick from empty playlist")
            video = random.choice(self._entries)
            if self._current is not None:
                self._history.append(self._current)
            self._current = video
            logger.debug("Picked video", extra={"video_id": video["id"], "title": video["title"]})
            return video

    def previous(self) -> Optional[dict]:
        """Return the previously played video, or None if no history."""
        with self._lock:
            if not self._history:
                return None
            prev = self._history.pop()
            if self._current is not None:
                # Don't push current back — we're going backwards
                pass
            self._current = prev
            logger.debug("Going to previous", extra={"video_id": prev["id"], "title": prev["title"]})
            return prev

    def update_entries(self, entries: list[dict]) -> None:
        """Update the playlist entries (e.g., after a refresh)."""
        with self._lock:
            self._entries = list(entries)
            logger.info("Playlist updated", extra={"count": len(entries)})
