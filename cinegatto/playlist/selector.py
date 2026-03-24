"""Selector — video selection with shuffle/sequential modes and history.

Two playback modes:
  - Shuffle: random.choice from the full playlist. To avoid repeats, the
    currently-playing video is excluded from candidates (when playlist has >1
    entry). This is a simple no-immediate-repeat strategy, not full-deck shuffle,
    because with a small playlist (~20 videos) full-deck shuffle would feel
    predictable ("I know that one is coming soon").
  - Sequential: linear walk through the playlist with wraparound. _index
    tracks the next position to play.

History is stored in a bounded deque (default 50). The deque's maxlen provides
automatic eviction of old entries, so we never need manual cleanup. The
previous() method pops from the deque, effectively rewinding through history.

All methods are thread-safe via self._lock because pick/previous can be called
from the controller worker thread while update_entries is called from the
playlist refresh thread.
"""

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
        # Bounded deque: maxlen auto-evicts oldest entries when full.
        # Stores previously-played videos for the "previous" button.
        self._history = collections.deque(maxlen=history_size)
        self._current: Optional[dict] = None
        # _index is only used in sequential mode; tracks next-to-play position.
        self._index = 0
        self._lock = threading.Lock()

    def pick(self) -> dict:
        """Pick the next video from the playlist."""
        with self._lock:
            if not self._entries:
                raise ValueError("Cannot pick from empty playlist")
            if self._shuffle:
                candidates = self._entries
                # No-immediate-repeat: exclude the currently-playing video so the
                # same video never plays twice in a row. Only when >1 video exists
                # (otherwise we'd have zero candidates).
                if self._current and len(self._entries) > 1:
                    candidates = [e for e in self._entries if e["id"] != self._current["id"]]
                video = random.choice(candidates)
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
            # In sequential mode, rewind the index so that calling pick() next
            # returns the video we just skipped back from (i.e., the user can
            # go back and then forward to return to where they were).
            if not self._shuffle and self._current in self._entries:
                idx = self._entries.index(self._current)
                self._index = idx
            self._current = prev
            logger.debug("Going to previous", extra={"video_id": prev["id"], "title": prev["title"]})
            return prev

    def peek_next(self, n: int = 1) -> list[dict]:
        """Preview the next N videos without advancing playback.

        Used by the controller to pre-warm the cache for upcoming videos.
        In sequential mode, peeks at the next indices (deterministic).
        In shuffle mode, returns random picks (non-deterministic, and these
        picks are NOT committed to history — the actual pick() may differ).
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

    def get_current_index(self) -> Optional[int]:
        """Return 1-based index of current video in playlist, or None."""
        with self._lock:
            if self._current and self._entries:
                try:
                    return self._entries.index(self._current) + 1
                except ValueError:
                    pass
            return None

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
