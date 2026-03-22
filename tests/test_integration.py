"""End-to-end smoke test: HTTP request → controller → mocked player."""

from unittest.mock import MagicMock

import pytest
from flask import Flask

from cinegatto.api.routes import api, init_api
from cinegatto.controller import PlaybackController
from cinegatto.display.noop import NoopDisplay
from cinegatto.player.types import PlayerState
from cinegatto.playlist.selector import Selector


@pytest.fixture
def integration_app():
    """Wire up real controller with mocked player, real selector, real display."""
    player = MagicMock()
    player.get_state.return_value = PlayerState()

    entries = [
        {"id": "vid1", "title": "Birds", "url": "https://youtube.com/watch?v=vid1"},
        {"id": "vid2", "title": "Squirrels", "url": "https://youtube.com/watch?v=vid2"},
    ]
    selector = Selector(entries)
    display = NoopDisplay()
    controller = PlaybackController(player=player, selector=selector, display=display)
    controller.start()

    app = Flask(__name__)
    init_api(controller)
    app.register_blueprint(api)

    yield app.test_client(), player, controller

    controller.stop()


class TestIntegration:
    def test_next_triggers_full_path(self, integration_app):
        """POST /api/next → controller → selector.pick() → player.load_video()."""
        client, player, controller = integration_app
        resp = client.post("/api/next")
        assert resp.status_code == 200

        # Wait for controller to process
        controller._queue.join()

        player.load_video.assert_called_once()
        url = player.load_video.call_args[0][0]
        assert "youtube.com" in url

    def test_pause_triggers_full_path(self, integration_app):
        """POST /api/pause → controller → player.pause()."""
        client, player, controller = integration_app
        resp = client.post("/api/pause")
        assert resp.status_code == 200

        controller._queue.join()
        player.pause.assert_called_once()

    def test_play_triggers_full_path(self, integration_app):
        """POST /api/play → controller → player.play()."""
        client, player, controller = integration_app
        resp = client.post("/api/play")
        assert resp.status_code == 200

        controller._queue.join()
        player.play.assert_called_once()

    def test_status_returns_state(self, integration_app):
        """GET /api/status returns player state."""
        client, player, controller = integration_app
        player.get_state.return_value = PlayerState(
            playing=True, video_title="Birds", position=10.0, duration=100.0
        )
        resp = client.get("/api/status")
        data = resp.get_json()
        assert data["playing"] is True
        assert data["video_title"] == "Birds"
