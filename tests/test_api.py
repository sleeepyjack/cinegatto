import json
from unittest.mock import MagicMock

import pytest
from flask import Flask

from cinegatto.api.routes import api, init_api
from cinegatto.log import RingBufferHandler
from cinegatto.player.types import PlayerState


@pytest.fixture
def app():
    """Create a Flask test app with mocked controller."""
    app = Flask(__name__)
    controller = MagicMock()
    controller.get_status.return_value = {
        "playing": True,
        "video_url": "https://youtube.com/watch?v=abc",
        "video_title": "Birds at Feeder",
        "position": 42.5,
        "duration": 3600.0,
    }
    ring = RingBufferHandler(max_size=100)
    init_api(controller, ring)
    app.register_blueprint(api)
    return app, controller, ring


@pytest.fixture
def client(app):
    flask_app, controller, ring = app
    return flask_app.test_client(), controller, ring


class TestPlayEndpoint:
    def test_play_returns_ok(self, client):
        c, controller, _ = client
        resp = c.post("/api/play")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"
        controller.play.assert_called_once()

    def test_pause_returns_ok(self, client):
        c, controller, _ = client
        resp = c.post("/api/pause")
        assert resp.status_code == 200
        controller.pause.assert_called_once()

    def test_next_returns_ok(self, client):
        c, controller, _ = client
        resp = c.post("/api/next")
        assert resp.status_code == 200
        controller.next_video.assert_called_once()

    def test_previous_returns_ok(self, client):
        c, controller, _ = client
        resp = c.post("/api/previous")
        assert resp.status_code == 200
        controller.previous_video.assert_called_once()


class TestStatusEndpoint:
    def test_returns_player_state(self, client):
        c, _, _ = client
        resp = c.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["playing"] is True
        assert data["video_title"] == "Birds at Feeder"
        assert data["position"] == 42.5
        assert data["duration"] == 3600.0


class TestLogsEndpoint:
    def test_returns_log_entries(self, client):
        c, _, ring = client
        # Add some entries to the ring buffer
        import logging
        logger = logging.getLogger("test.api.logs")
        logger.addHandler(ring)
        logger.setLevel(logging.DEBUG)
        logger.info("test message 1")
        logger.warning("test message 2")

        resp = c.get("/api/logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["entries"]) == 2

    def test_filters_by_level(self, client):
        c, _, ring = client
        import logging
        logger = logging.getLogger("test.api.logs.filter")
        logger.addHandler(ring)
        logger.setLevel(logging.DEBUG)
        logger.debug("debug msg")
        logger.warning("warn msg")

        resp = c.get("/api/logs?level=warning")
        data = resp.get_json()
        assert len(data["entries"]) == 1
        assert data["entries"][0]["message"] == "warn msg"

    def test_limits_entries(self, client):
        c, _, ring = client
        import logging
        logger = logging.getLogger("test.api.logs.limit")
        logger.addHandler(ring)
        logger.setLevel(logging.DEBUG)
        for i in range(10):
            logger.info(f"msg {i}")

        resp = c.get("/api/logs?limit=3")
        data = resp.get_json()
        assert len(data["entries"]) == 3
