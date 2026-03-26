"""Structured JSON logging with a ring buffer for the /api/logs endpoint.

Three handlers are attached to the "cinegatto" logger:
  1. Console (StreamHandler) — for interactive debugging and journalctl on Pi.
  2. File (FileHandler, mode="w") — overwrites each run so the log file doesn't
     grow unbounded on the Pi's SD card. Historical logs live in journalctl.
  3. Ring buffer (RingBufferHandler) — in-memory deque that the /api/logs
     endpoint reads from. This avoids file I/O on every API request and gives
     the web UI fast access to recent logs.

The ring buffer uses a bounded deque (maxlen=ring_size). When full, the oldest
entry is automatically evicted. get_entries() returns entries[-limit:] (the TAIL
of the buffer) because the most recent entries are the most useful — if the
caller asks for 50 entries, they want the last 50, not the first 50 out of 500.
"""

import collections
import logging
import os
from pathlib import Path

from pythonjsonlogger.json import JsonFormatter

_DEFAULT_LOG_FILE = str(Path(__file__).parent.parent / ".cinegatto.log")


class RingBufferHandler(logging.Handler):
    """In-memory ring buffer that stores log entries for the /api/logs endpoint.

    Each emit() formats the record as JSON (via JsonFormatter) and appends
    the parsed dict to the deque. Storing dicts (not raw strings) avoids
    re-parsing on every API request.
    """

    def __init__(self, max_size=500):
        super().__init__()
        self._buffer = collections.deque(maxlen=max_size)
        self._formatter = JsonFormatter(
            fmt="%(message)s %(levelname)s %(name)s",
            rename_fields={"levelname": "level", "asctime": "timestamp"},
            timestamp=True,
        )

    def emit(self, record):
        import json

        formatted = self._formatter.format(record)
        entry = json.loads(formatted)
        self._buffer.append(entry)

    def get_entries(self, level=None, limit=100):
        # Snapshot the deque to a list (thread-safe: deque iteration is atomic in CPython).
        entries = list(self._buffer)
        if level is not None:
            threshold = getattr(logging, level.upper(), logging.DEBUG)
            entries = [e for e in entries if logging.getLevelName(e.get("level", "DEBUG")) >= threshold]
        # entries[-limit:] returns the LAST `limit` items — i.e., the most recent
        # log entries. Using [:limit] would return the oldest, which is wrong for
        # a "show me recent logs" API.
        return entries[-limit:]


def setup_logging(level="debug", ring_size=500, log_file=None):
    """Configure structured JSON logging with console output, file, and ring buffer.

    Returns the configured root logger.
    """
    log_level = getattr(logging, level.upper(), logging.DEBUG)

    logger = logging.getLogger("cinegatto")
    logger.setLevel(log_level)
    logger.handlers.clear()

    # Silence werkzeug's per-request HTTP logs (noisy at INFO from status polling)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    formatter = JsonFormatter(
        fmt="%(message)s %(levelname)s %(name)s",
        rename_fields={"levelname": "level", "asctime": "timestamp"},
        timestamp=True,
    )

    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handler — mode="w" overwrites on each startup to prevent the log file
    # from growing unbounded on the Pi's limited SD card storage.
    log_path = os.path.expanduser(log_file) if log_file else _DEFAULT_LOG_FILE
    file_handler = logging.FileHandler(log_path, mode="w")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    ring = RingBufferHandler(max_size=ring_size)
    ring.setLevel(log_level)
    logger.addHandler(ring)

    return logger
