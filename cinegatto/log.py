import collections
import logging
import os
from pathlib import Path

from pythonjsonlogger.json import JsonFormatter

_DEFAULT_LOG_FILE = str(Path(__file__).parent.parent / ".cinegatto.log")


class RingBufferHandler(logging.Handler):
    """In-memory ring buffer that stores log entries for the /api/logs endpoint."""

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
        entries = list(self._buffer)
        if level is not None:
            threshold = getattr(logging, level.upper(), logging.DEBUG)
            entries = [e for e in entries if logging.getLevelName(e.get("level", "DEBUG")) >= threshold]
        return entries[:limit]


def setup_logging(level="debug", ring_size=500, log_file=None):
    """Configure structured JSON logging with console output, file, and ring buffer.

    Returns the configured root logger.
    """
    log_level = getattr(logging, level.upper(), logging.DEBUG)

    logger = logging.getLogger("cinegatto")
    logger.setLevel(log_level)
    logger.handlers.clear()

    formatter = JsonFormatter(
        fmt="%(message)s %(levelname)s %(name)s",
        rename_fields={"levelname": "level", "asctime": "timestamp"},
        timestamp=True,
    )

    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handler
    log_path = os.path.expanduser(log_file) if log_file else _DEFAULT_LOG_FILE
    file_handler = logging.FileHandler(log_path, mode="w")  # overwrite each run
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    ring = RingBufferHandler(max_size=ring_size)
    ring.setLevel(log_level)
    logger.addHandler(ring)

    return logger
