import json
import logging

from cinegatto.log import RingBufferHandler, setup_logging


class TestSetupLogging:
    def test_returns_logger(self):
        logger = setup_logging(level="debug", ring_size=100)
        assert isinstance(logger, logging.Logger)

    def test_output_is_json(self, capfd):
        logger = setup_logging(level="debug", ring_size=100)
        logger.info("hello")
        captured = capfd.readouterr()
        record = json.loads(captured.err.strip())
        assert record["message"] == "hello"
        assert "level" in record
        assert "timestamp" in record

    def test_includes_context_fields(self, capfd):
        logger = setup_logging(level="debug", ring_size=100)
        logger.info("loading video", extra={"video_id": "abc123"})
        captured = capfd.readouterr()
        record = json.loads(captured.err.strip())
        assert record["video_id"] == "abc123"


class TestRingBufferHandler:
    def test_max_size(self):
        handler = RingBufferHandler(max_size=3)
        logger = logging.getLogger("test.ring.maxsize")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        for i in range(5):
            logger.info(f"msg {i}")
        entries = handler.get_entries()
        assert len(entries) == 3

    def test_returns_entries(self):
        handler = RingBufferHandler(max_size=10)
        logger = logging.getLogger("test.ring.entries")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.info("first")
        logger.warning("second")
        entries = handler.get_entries()
        assert len(entries) == 2
        assert entries[0]["message"] == "first"
        assert entries[1]["message"] == "second"

    def test_level_filter(self):
        handler = RingBufferHandler(max_size=10)
        logger = logging.getLogger("test.ring.filter")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.debug("debug msg")
        logger.info("info msg")
        logger.warning("warn msg")
        entries = handler.get_entries(level="warning")
        assert len(entries) == 1
        assert entries[0]["message"] == "warn msg"

    def test_overflow_drops_oldest(self):
        handler = RingBufferHandler(max_size=2)
        logger = logging.getLogger("test.ring.overflow")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.info("old")
        logger.info("mid")
        logger.info("new")
        entries = handler.get_entries()
        assert len(entries) == 2
        assert entries[0]["message"] == "mid"
        assert entries[1]["message"] == "new"

    def test_get_entries_returns_tail_not_head(self):
        handler = RingBufferHandler(max_size=100)
        logger = logging.getLogger("test.tail")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        for i in range(20):
            logger.info(f"msg {i}")
        entries = handler.get_entries(limit=5)
        assert len(entries) == 5
        assert entries[0]["message"] == "msg 15"
        assert entries[4]["message"] == "msg 19"

    def test_setup_logging_idempotent(self):
        setup_logging(level="debug", ring_size=100)
        setup_logging(level="debug", ring_size=100)
        logger = logging.getLogger("cinegatto")
        # Should have exactly 3 handlers (console, file, ring) not 6
        assert len(logger.handlers) == 3
