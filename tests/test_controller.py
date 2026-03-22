import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from cinegatto.controller import PlaybackController
from cinegatto.player.types import PlayerState


class TestPlaybackController:
    def _make_controller(self, player=None, selector=None, display=None, random_start=False):
        player = player or MagicMock()
        selector = selector or MagicMock()
        display = display or MagicMock()
        # Default get_state to return idle
        if not hasattr(player.get_state, 'return_value') or player.get_state.return_value is MagicMock:
            player.get_state.return_value = PlayerState()
        ctrl = PlaybackController(player=player, selector=selector, display=display, random_start=random_start)
        ctrl.start()
        return ctrl

    def _wait_for_queue(self, ctrl, timeout=2.0):
        """Wait for the command queue to drain."""
        ctrl._queue.join()

    def test_play_resumes_and_powers_display(self):
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        display = MagicMock()
        ctrl = self._make_controller(player=player, display=display)
        try:
            ctrl.play()
            self._wait_for_queue(ctrl)
            display.power_on.assert_called_once()
            player.play.assert_called_once()
        finally:
            ctrl.stop()

    def test_pause_pauses_and_powers_off_display(self):
        player = MagicMock()
        player.get_state.return_value = PlayerState(playing=True)
        display = MagicMock()
        ctrl = self._make_controller(player=player, display=display)
        try:
            ctrl.pause()
            self._wait_for_queue(ctrl)
            player.pause.assert_called_once()
            display.power_off.assert_called_once()
        finally:
            ctrl.stop()

    def test_next_picks_video_and_loads(self):
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        selector = MagicMock()
        selector.pick.return_value = {"id": "abc", "title": "Birds", "url": "https://youtube.com/watch?v=abc"}
        ctrl = self._make_controller(player=player, selector=selector)
        try:
            ctrl.next_video()
            self._wait_for_queue(ctrl)
            selector.pick.assert_called_once()
            player.load_video.assert_called_once_with("https://youtube.com/watch?v=abc")
        finally:
            ctrl.stop()

    def test_previous_loads_from_history(self):
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        selector = MagicMock()
        selector.previous.return_value = {"id": "xyz", "title": "Squirrels", "url": "https://youtube.com/watch?v=xyz"}
        ctrl = self._make_controller(player=player, selector=selector)
        try:
            ctrl.previous_video()
            self._wait_for_queue(ctrl)
            selector.previous.assert_called_once()
            player.load_video.assert_called_once_with("https://youtube.com/watch?v=xyz")
        finally:
            ctrl.stop()

    def test_previous_with_no_history_does_nothing(self):
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        selector = MagicMock()
        selector.previous.return_value = None
        ctrl = self._make_controller(player=player, selector=selector)
        try:
            ctrl.previous_video()
            self._wait_for_queue(ctrl)
            player.load_video.assert_not_called()
        finally:
            ctrl.stop()

    def test_commands_are_serialized(self):
        """Multiple rapid commands should execute sequentially, not concurrently."""
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        execution_order = []

        def mock_play():
            execution_order.append("play")

        def mock_pause():
            execution_order.append("pause")

        player.play.side_effect = mock_play
        player.pause.side_effect = mock_pause

        display = MagicMock()
        ctrl = self._make_controller(player=player, display=display)
        try:
            ctrl.play()
            ctrl.pause()
            ctrl.play()
            self._wait_for_queue(ctrl)
            assert execution_order == ["play", "pause", "play"]
        finally:
            ctrl.stop()

    def test_get_status_returns_state_without_blocking(self):
        player = MagicMock()
        player.get_state.return_value = PlayerState(
            playing=True, video_title="Birds", position=30.0, duration=600.0
        )
        ctrl = self._make_controller(player=player)
        try:
            status = ctrl.get_status()
            assert status["playing"] is True
            assert status["video_title"] == "Birds"
            assert status["position"] == 30.0
        finally:
            ctrl.stop()

    def test_stop_drains_queue(self):
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        ctrl = self._make_controller(player=player)
        ctrl.play()
        ctrl.stop()
        # Should not hang — stop drains the queue and exits
        assert not ctrl._worker_thread.is_alive()

    def test_next_with_random_start_seeks(self):
        """When random_start is enabled, next should seek after loading."""
        player = MagicMock()
        player.get_state.return_value = PlayerState(duration=1000.0)
        selector = MagicMock()
        selector.pick.return_value = {"id": "abc", "title": "Birds", "url": "https://youtube.com/watch?v=abc"}
        ctrl = self._make_controller(player=player, selector=selector, random_start=True)
        try:
            ctrl.next_video()
            self._wait_for_queue(ctrl)
            player.load_video.assert_called_once()
            player.seek.assert_called_once()
            seek_pos = player.seek.call_args[0][0]
            assert 0 <= seek_pos <= 800.0  # 80% of duration
        finally:
            ctrl.stop()

    def test_next_without_random_start_does_not_seek(self):
        """When random_start is disabled, next should not seek."""
        player = MagicMock()
        player.get_state.return_value = PlayerState(duration=1000.0)
        selector = MagicMock()
        selector.pick.return_value = {"id": "abc", "title": "Birds", "url": "https://youtube.com/watch?v=abc"}
        ctrl = self._make_controller(player=player, selector=selector, random_start=False)
        try:
            ctrl.next_video()
            self._wait_for_queue(ctrl)
            player.load_video.assert_called_once()
            player.seek.assert_not_called()
        finally:
            ctrl.stop()
