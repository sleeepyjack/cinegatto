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


class TestSettingsEndpoint:
    def test_get_settings(self, client):
        c, controller, _ = client
        controller.get_settings.return_value = {"shuffle": True, "random_start": True}
        resp = c.get("/api/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["shuffle"] is True
        assert data["random_start"] is True

    def test_update_shuffle(self, client):
        c, controller, _ = client
        controller.get_settings.return_value = {"shuffle": False, "random_start": True}
        resp = c.post("/api/settings",
                       json={"shuffle": False})
        assert resp.status_code == 200
        controller.set_shuffle.assert_called_once_with(False)

    def test_update_random_start(self, client):
        c, controller, _ = client
        controller.get_settings.return_value = {"shuffle": True, "random_start": False}
        resp = c.post("/api/settings",
                       json={"random_start": False})
        assert resp.status_code == 200
        controller.set_random_start.assert_called_once_with(False)


class TestSettingsValidation:
    def test_rejects_non_bool_shuffle(self, client):
        c, controller, _ = client
        resp = c.post("/api/settings", json={"shuffle": "false"})
        assert resp.status_code == 400

    def test_rejects_non_bool_random_start(self, client):
        c, controller, _ = client
        resp = c.post("/api/settings", json={"random_start": 0})
        assert resp.status_code == 400


class TestSyncNotReady:
    def test_returns_503(self):
        from cinegatto.api.routes import api, init_api
        from flask import Flask
        app = Flask(__name__)
        init_api(None)  # no controller
        app.register_blueprint(api)
        c = app.test_client()
        resp = c.post("/api/sync")
        assert resp.status_code == 503


class TestCacheDisabled:
    def test_returns_disabled(self):
        from cinegatto.api.routes import api, init_api
        from flask import Flask
        app = Flask(__name__)
        init_api(MagicMock(), cache_service=None)
        app.register_blueprint(api)
        c = app.test_client()
        resp = c.get("/api/cache")
        assert resp.get_json()["enabled"] is False


class TestLogsLimitCapped:
    def test_limit_capped_at_500(self, client):
        c, _, ring = client
        import logging
        logger = logging.getLogger("test.api.logs.cap")
        logger.addHandler(ring)
        logger.setLevel(logging.DEBUG)
        for i in range(600):
            logger.info(f"msg {i}")
        resp = c.get("/api/logs?limit=9999")
        data = resp.get_json()
        assert len(data["entries"]) <= 500


class TestSettingsIncludesPlaylistUrl:
    def test_includes_url_when_set(self):
        from cinegatto.api.routes import api, init_api
        from flask import Flask
        app = Flask(__name__)
        controller = MagicMock()
        controller.get_settings.return_value = {"shuffle": True, "random_start": True}
        init_api(controller, playlist_url="https://youtube.com/playlist?list=TEST")
        app.register_blueprint(api)
        c = app.test_client()
        resp = c.get("/api/settings")
        data = resp.get_json()
        assert data["playlist_url"] == "https://youtube.com/playlist?list=TEST"

    def test_excludes_url_when_not_set(self):
        from cinegatto.api.routes import api, init_api
        from flask import Flask
        app = Flask(__name__)
        controller = MagicMock()
        controller.get_settings.return_value = {"shuffle": True, "random_start": True}
        init_api(controller, playlist_url=None)
        app.register_blueprint(api)
        c = app.test_client()
        resp = c.get("/api/settings")
        data = resp.get_json()
        assert "playlist_url" not in data
