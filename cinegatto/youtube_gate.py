"""Circuit breaker for YouTube access.

When YouTube returns bot detection errors repeatedly, ALL YouTube requests
(streaming via mpv, cache downloads via yt-dlp, playlist fetches) should
back off to avoid making the block worse.

Usage:
    from cinegatto.youtube_gate import yt_gate

    if yt_gate.is_blocked():
        return  # skip YouTube access

    # ... attempt YouTube access ...
    if failed:
        yt_gate.record_failure()
    else:
        yt_gate.record_success()
"""

import logging
import threading
import time

logger = logging.getLogger("cinegatto.youtube_gate")

_DEFAULT_COOLDOWN = 600  # 10 minutes
_DEFAULT_THRESHOLD = 3   # failures before tripping


class YouTubeGate:
    """Circuit breaker that pauses YouTube access after repeated failures."""

    def __init__(self, threshold: int = _DEFAULT_THRESHOLD,
                 cooldown_sec: float = _DEFAULT_COOLDOWN):
        self._threshold = threshold
        self._cooldown = cooldown_sec
        self._failures = 0
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def is_blocked(self) -> bool:
        """Check if YouTube access should be skipped."""
        with self._lock:
            if time.time() < self._blocked_until:
                return True
            return False

    def record_failure(self) -> None:
        """Record a YouTube access failure. Trips the breaker after threshold."""
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._blocked_until = time.time() + self._cooldown
                logger.warning("YouTube circuit breaker tripped — cooling off for %ds "
                               "after %d consecutive failures",
                               int(self._cooldown), self._failures)
                self._failures = 0

    def record_success(self) -> None:
        """Record a successful YouTube access. Resets the failure counter."""
        with self._lock:
            if self._failures > 0:
                logger.info("YouTube access recovered after failures")
            self._failures = 0
            self._blocked_until = 0.0

    def time_remaining(self) -> float:
        """Seconds until the breaker resets. 0 if not blocked."""
        with self._lock:
            remaining = self._blocked_until - time.time()
            return max(0.0, remaining)


# Global singleton — shared by player, cache, and fetcher
yt_gate = YouTubeGate()
