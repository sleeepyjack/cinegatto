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
            player.load_video.assert_called_once()
            assert player.load_video.call_args[0][0] == "https://youtube.com/watch?v=abc"
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
            player.load_video.assert_called_once()
            assert player.load_video.call_args[0][0] == "https://youtube.com/watch?v=xyz"
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

    def test_next_with_random_start_passes_start_percent(self):
        """When random_start is enabled, load_video gets a start_percent."""
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        selector = MagicMock()
        selector.pick.return_value = {"id": "abc", "title": "Birds", "url": "https://youtube.com/watch?v=abc"}
        ctrl = self._make_controller(player=player, selector=selector, random_start=True)
        try:
            ctrl.next_video()
            self._wait_for_queue(ctrl)
            player.load_video.assert_called_once()
            _, kwargs = player.load_video.call_args
            assert "start_percent" in kwargs
            assert 0 <= kwargs["start_percent"] <= 80.0
        finally:
            ctrl.stop()

    def test_next_without_random_start_no_start_percent(self):
        """When random_start is disabled, load_video gets no start_percent."""
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        selector = MagicMock()
        selector.pick.return_value = {"id": "abc", "title": "Birds", "url": "https://youtube.com/watch?v=abc"}
        ctrl = self._make_controller(player=player, selector=selector, random_start=False)
        try:
            ctrl.next_video()
            self._wait_for_queue(ctrl)
            player.load_video.assert_called_once()
            _, kwargs = player.load_video.call_args
            assert kwargs.get("start_percent") is None
        finally:
            ctrl.stop()

    def test_set_shuffle(self):
        """set_shuffle toggles the selector's shuffle mode."""
        selector = MagicMock()
        selector.get_shuffle.return_value = True
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        ctrl = self._make_controller(player=player, selector=selector)
        try:
            ctrl.set_shuffle(False)
            selector.set_shuffle.assert_called_once_with(False)
            selector.get_shuffle.return_value = False
            assert ctrl.get_settings()["shuffle"] is False
        finally:
            ctrl.stop()

    def test_set_random_start(self):
        """set_random_start toggles random start."""
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        ctrl = self._make_controller(player=player, random_start=True)
        try:
            ctrl.set_random_start(False)
            assert ctrl.get_settings()["random_start"] is False
        finally:
            ctrl.stop()

    def test_pick_cached_or_next_empty_cache_waits(self):
        """Empty cache waits for downloads, nothing plays."""
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        selector = MagicMock()
        selector.pick.return_value = {"id": "abc", "title": "Birds", "url": "https://youtube.com/watch?v=abc"}
        selector.get_all_entries.return_value = [{"id": "abc", "title": "Birds", "url": "https://youtube.com/watch?v=abc"}]
        selector.peek_next.return_value = []
        cache = MagicMock()
        cache.contains.return_value = False
        cache.get.return_value = None
        ctrl = PlaybackController(player=player, selector=selector, display=MagicMock(),
                                  random_start=False, cache_service=cache)
        ctrl.start()
        try:
            ctrl.next_video()
            ctrl._queue.join()
            player.load_video.assert_not_called()  # nothing to play
            cache.warm.assert_called()  # but download queued
        finally:
            ctrl.stop()

    def test_pick_cached_or_next_fallback_to_cached(self):
        """Uncached pick falls back to a cached video."""
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        selector = MagicMock()
        selector.pick.return_value = {"id": "uncached", "title": "New", "url": "https://youtube.com/watch?v=uncached"}
        selector.get_all_entries.return_value = [
            {"id": "uncached", "title": "New", "url": "https://youtube.com/watch?v=uncached"},
            {"id": "cached", "title": "Old", "url": "https://youtube.com/watch?v=cached"},
        ]
        selector.peek_next.return_value = []
        cache = MagicMock()
        cache.contains.side_effect = lambda vid: vid == "cached"
        cache.get.side_effect = lambda vid: "/fake/cached.mp4" if vid == "cached" else None
        ctrl = PlaybackController(player=player, selector=selector, display=MagicMock(),
                                  random_start=False, cache_service=cache)
        ctrl.start()
        try:
            ctrl.next_video()
            ctrl._queue.join()
            player.load_video.assert_called_once()
            path = player.load_video.call_args[0][0]
            assert path == "/fake/cached.mp4"
        finally:
            ctrl.stop()

    def test_pick_cached_or_next_no_cache_service(self):
        """Without cache service, plays whatever selector picks."""
        player = MagicMock()
        player.get_state.return_value = PlayerState()
        selector = MagicMock()
        selector.pick.return_value = {"id": "abc", "title": "Birds", "url": "https://youtube.com/watch?v=abc"}
        selector.peek_next.return_value = []
        ctrl = PlaybackController(player=player, selector=selector, display=MagicMock(),
                                  random_start=False, cache_service=None)
        ctrl.start()
        try:
            ctrl.next_video()
            ctrl._queue.join()
            player.load_video.assert_called_once()
        finally:
            ctrl.stop()

    def test_random_seek_noop_without_duration(self):
        """Random seek does nothing when duration is 0."""
        player = MagicMock()
        player.get_state.return_value = PlayerState(duration=0)
        ctrl = PlaybackController(player=player, selector=MagicMock(), display=MagicMock())
        ctrl.start()
        try:
            ctrl.random_seek()
            ctrl._queue.join()
            player.seek.assert_not_called()
        finally:
            ctrl.stop()

    def test_random_seek_handles_player_error(self):
        """Random seek swallows player exceptions."""
        player = MagicMock()
        player.get_state.side_effect = Exception("player dead")
        ctrl = PlaybackController(player=player, selector=MagicMock(), display=MagicMock())
        ctrl.start()
        try:
            ctrl.random_seek()
            ctrl._queue.join()
            # Should not crash the worker thread
            ctrl.play()
            ctrl._queue.join()
        finally:
            ctrl.stop()
