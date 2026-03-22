"""Selector — video selection with shuffle/sequential modes and history."""

import collections
import logging
import random
import threading
from typing import Optional

logger = logging.getLogger("cinegatto.playlist.selector")


class Selector:
    """Picks videos from a playlist (shuffle or sequential) with play history.

    In shuffle mode, videos are picked randomly.
    In sequential mode, videos play in order and wrap around at the end.
    """

    def __init__(self, entries: list[dict], shuffle: bool = True, history_size: int = 50):
        self._entries = list(entries)
        self._shuffle = shuffle
        self._history = collections.deque(maxlen=history_size)
        self._current: Optional[dict] = None
        self._index = 0
        self._lock = threading.Lock()

    def pick(self) -> dict:
        """Pick the next video from the playlist."""
        with self._lock:
            if not self._entries:
                raise ValueError("Cannot pick from empty playlist")
            if self._shuffle:
                video = random.choice(self._entries)
            else:
                video = self._entries[self._index]
                self._index = (self._index + 1) % len(self._entries)
            if self._current is not None:
                self._history.append(self._current)
            self._current = video
            logger.debug("Picked video", extra={
                "video_id": video["id"], "title": video["title"],
                "mode": "shuffle" if self._shuffle else f"sequential[{self._index}]",
            })
            return video

    def previous(self) -> Optional[dict]:
        """Return the previously played video, or None if no history."""
        with self._lock:
            if not self._history:
                return None
            prev = self._history.pop()
            # In sequential mode, step the index back too
            if not self._shuffle and self._current in self._entries:
                idx = self._entries.index(self._current)
                self._index = idx  # so next pick() replays current
            self._current = prev
            logger.debug("Going to previous", extra={"video_id": prev["id"], "title": prev["title"]})
            return prev

    def peek_next(self, n: int = 1) -> list[dict]:
        """Preview the next N videos without advancing playback.

        In sequential mode, peeks at the next indices.
        In shuffle mode, returns random picks (not committed to history).
        """
        with self._lock:
            if not self._entries:
                return []
            if self._shuffle:
                return [random.choice(self._entries) for _ in range(min(n, len(self._entries)))]
            else:
                result = []
                for i in range(n):
                    idx = (self._index + i) % len(self._entries)
                    result.append(self._entries[idx])
                return result

    def get_all_entries(self) -> list[dict]:
        """Return a copy of all playlist entries."""
        with self._lock:
            return list(self._entries)

    def update_entries(self, entries: list[dict]) -> None:
        """Update the playlist entries (e.g., after a refresh)."""
        with self._lock:
            self._entries = list(entries)
            # Clamp index in case playlist shrank
            if self._index >= len(self._entries):
                self._index = 0
            logger.info("Playlist updated", extra={"count": len(entries)})
