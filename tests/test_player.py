import json
import os
import socket
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from cinegatto.player.types import PlayerState, Player
from cinegatto.player.mpv_ipc import MpvIpc, MpvIpcError
from cinegatto.player.mpv_player import MpvPlayer


# --- PlayerState tests ---

class TestPlayerState:
    def test_default_state(self):
        state = PlayerState()
        assert state.playing is False
        assert state.position == 0.0
        assert state.duration == 0.0
        assert state.video_url is None

    def test_to_dict(self):
        state = PlayerState(playing=True, video_url="http://example.com",
                           video_title="Test", position=10.5, duration=100.0)
        d = state.to_dict()
        assert d["playing"] is True
        assert d["video_url"] == "http://example.com"
        assert d["position"] == 10.5


# --- MpvIpc tests (mock the socket) ---

class TestMpvIpc:
    """Test MpvIpc command formatting and response handling via mocks.

    The real IPC uses a dedicated reader thread with socketpair, but
    for unit tests we verify the public interface with mocked internals.
    """

    def test_command_formats_json_correctly(self):
        """command() sends correctly formatted JSON."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._write_lock = threading.Lock()
        ipc._request_id = 0
        ipc._pending = {}
        ipc._pending_lock = threading.Lock()
        ipc._running = True
        ipc._timeout = 2.0
        ipc._sock = MagicMock()

        # Simulate the reader thread delivering a response
        def fake_send(data):
            sent = json.loads(data.decode())
            req_id = sent["request_id"]
            with ipc._pending_lock:
                q = ipc._pending.get(req_id)
            if q:
                q.put(None)  # success, data=None

        ipc._sock.sendall.side_effect = fake_send
        result = ipc.command("loadfile", "http://example.com/video")
        assert result is None

        sent_data = ipc._sock.sendall.call_args[0][0]
        sent_json = json.loads(sent_data.decode())
        assert sent_json["command"] == ["loadfile", "http://example.com/video"]

    def test_get_property_returns_data(self):
        """get_property returns the data from mpv's response."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._write_lock = threading.Lock()
        ipc._request_id = 0
        ipc._pending = {}
        ipc._pending_lock = threading.Lock()
        ipc._running = True
        ipc._timeout = 2.0
        ipc._sock = MagicMock()

        def fake_send(data):
            sent = json.loads(data.decode())
            with ipc._pending_lock:
                q = ipc._pending.get(sent["request_id"])
            if q:
                q.put(True)

        ipc._sock.sendall.side_effect = fake_send
        result = ipc.get_property("pause")
        assert result is True

    def test_command_error_raises(self):
        """An mpv error response raises MpvIpcError."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._write_lock = threading.Lock()
        ipc._request_id = 0
        ipc._pending = {}
        ipc._pending_lock = threading.Lock()
        ipc._running = True
        ipc._timeout = 2.0
        ipc._sock = MagicMock()

        def fake_send(data):
            sent = json.loads(data.decode())
            with ipc._pending_lock:
                q = ipc._pending.get(sent["request_id"])
            if q:
                q.put(MpvIpcError("property not found"))

        ipc._sock.sendall.side_effect = fake_send
        with pytest.raises(MpvIpcError, match="property not found"):
            ipc.get_property("nonexistent")

    def test_event_dispatch(self):
        """_dispatch_event calls registered callbacks."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._event_callbacks = {}
        received = []
        ipc.on_event("file-loaded", lambda e: received.append(e))
        ipc._dispatch_event({"event": "file-loaded"})
        assert len(received) == 1
        assert received[0]["event"] == "file-loaded"

    def test_close_unblocks_pending(self):
        """close() delivers errors to any waiting callers."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._running = True
        ipc._pending = {}
        ipc._pending_lock = threading.Lock()
        ipc._sock = MagicMock()
        ipc._reader = MagicMock()
        ipc._event_callbacks = {}

        # Simulate a pending request
        import queue as q
        resp_queue = q.Queue()
        ipc._pending[1] = resp_queue

        ipc.close()
        result = resp_queue.get(timeout=1)
        assert isinstance(result, MpvIpcError)

    def test_event_dispatch_continues_on_callback_error(self):
        """If one callback raises, the next still runs."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._event_callbacks = {}
        results = []
        ipc.on_event("test", lambda e: 1/0)  # raises
        ipc.on_event("test", lambda e: results.append("ok"))
        ipc._dispatch_event({"event": "test"})
        assert results == ["ok"]


# --- MpvPlayer tests (mock IPC and subprocess) ---

class TestMpvPlayer:
    @patch("cinegatto.player.mpv_player.subprocess.Popen")
    @patch("cinegatto.player.mpv_player.MpvIpc")
    def test_load_video_calls_loadfile(self, MockIpc, MockPopen):
        """load_video sends loadfile command via IPC."""
        mock_ipc = MockIpc.return_value
        mock_ipc.command.return_value = None
        mock_ipc.get_property.return_value = 0.0
        mock_proc = MockPopen.return_value
        mock_proc.poll.return_value = None

        player = MpvPlayer(mpv_args=[], socket_path="/tmp/test-mpv.sock")
        player._ipc = mock_ipc
        player._process = mock_proc
        player._running = True

        player.load_video("https://youtube.com/watch?v=test")
        mock_ipc.command.assert_called_with("loadfile", "https://youtube.com/watch?v=test")

    @patch("cinegatto.player.mpv_player.subprocess.Popen")
    @patch("cinegatto.player.mpv_player.MpvIpc")
    def test_load_video_with_start_percent(self, MockIpc, MockPopen):
        """load_video with start_percent passes start= option to mpv."""
        mock_ipc = MockIpc.return_value
        mock_ipc.command.return_value = None
        mock_proc = MockPopen.return_value
        mock_proc.poll.return_value = None

        player = MpvPlayer(mpv_args=[], socket_path="/tmp/test-mpv.sock")
        player._ipc = mock_ipc
        player._process = mock_proc
        player._running = True

        player.load_video("https://youtube.com/watch?v=test", start_percent=42.5)
        mock_ipc.command.assert_called_with(
            "loadfile", "https://youtube.com/watch?v=test", "replace", -1, {"start": "42.5%"}
        )

    @patch("cinegatto.player.mpv_player.subprocess.Popen")
    @patch("cinegatto.player.mpv_player.MpvIpc")
    def test_play_unpauses(self, MockIpc, MockPopen):
        """play() sets pause property to false."""
        mock_ipc = MockIpc.return_value
        mock_proc = MockPopen.return_value
        mock_proc.poll.return_value = None

        player = MpvPlayer(mpv_args=[], socket_path="/tmp/test-mpv.sock")
        player._ipc = mock_ipc
        player._process = mock_proc
        player._running = True

        player.play()
        mock_ipc.set_property.assert_called_with("pause", False)

    @patch("cinegatto.player.mpv_player.subprocess.Popen")
    @patch("cinegatto.player.mpv_player.MpvIpc")
    def test_pause_pauses(self, MockIpc, MockPopen):
        """pause() sets pause property to true."""
        mock_ipc = MockIpc.return_value
        mock_proc = MockPopen.return_value
        mock_proc.poll.return_value = None

        player = MpvPlayer(mpv_args=[], socket_path="/tmp/test-mpv.sock")
        player._ipc = mock_ipc
        player._process = mock_proc
        player._running = True

        player.pause()
        mock_ipc.set_property.assert_called_with("pause", True)

    @patch("cinegatto.player.mpv_player.subprocess.Popen")
    @patch("cinegatto.player.mpv_player.MpvIpc")
    def test_seek_sends_absolute_seek(self, MockIpc, MockPopen):
        """seek() sends seek command with absolute position."""
        mock_ipc = MockIpc.return_value
        mock_proc = MockPopen.return_value
        mock_proc.poll.return_value = None

        player = MpvPlayer(mpv_args=[], socket_path="/tmp/test-mpv.sock")
        player._ipc = mock_ipc
        player._process = mock_proc
        player._running = True

        player.seek(120.5)
        mock_ipc.command.assert_called_with("seek", 120.5, "absolute")

    @patch("cinegatto.player.mpv_player.subprocess.Popen")
    @patch("cinegatto.player.mpv_player.MpvIpc")
    def test_get_state(self, MockIpc, MockPopen):
        """get_state() reads properties from mpv."""
        mock_ipc = MockIpc.return_value
        mock_ipc.get_property.side_effect = lambda prop: {
            "pause": False,
            "time-pos": 30.0,
            "duration": 600.0,
            "media-title": "Test Video",
            "path": "https://youtube.com/watch?v=test",
        }.get(prop, None)
        mock_proc = MockPopen.return_value
        mock_proc.poll.return_value = None

        player = MpvPlayer(mpv_args=[], socket_path="/tmp/test-mpv.sock")
        player._ipc = mock_ipc
        player._process = mock_proc
        player._running = True

        state = player.get_state()
        assert state.playing is True  # pause=False means playing=True
        assert state.position == 30.0
        assert state.duration == 600.0

    @patch("cinegatto.player.mpv_player.subprocess.Popen")
    @patch("cinegatto.player.mpv_player.MpvIpc")
    def test_get_state_when_idle(self, MockIpc, MockPopen):
        """get_state() returns defaults when mpv has no file loaded."""
        mock_ipc = MockIpc.return_value
        mock_ipc.get_property.side_effect = Exception("property unavailable")
        mock_proc = MockPopen.return_value
        mock_proc.poll.return_value = None

        player = MpvPlayer(mpv_args=[], socket_path="/tmp/test-mpv.sock")
        player._ipc = mock_ipc
        player._process = mock_proc
        player._running = True

        state = player.get_state()
        assert state.playing is False
        assert state.position == 0.0

    @patch("cinegatto.player.mpv_player.subprocess.Popen")
    def test_shutdown_kills_process(self, MockPopen):
        """shutdown() terminates the mpv process."""
        mock_proc = MockPopen.return_value
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0

        player = MpvPlayer(mpv_args=[], socket_path="/tmp/test-mpv.sock")
        player._process = mock_proc
        player._ipc = MagicMock()
        player._running = True
        player._watchdog_thread = None

        player.shutdown()
        assert mock_proc.terminate.called or mock_proc.kill.called

    @patch("cinegatto.player.mpv_player.subprocess.Popen")
    def test_shutdown_cleans_up_socket(self, MockPopen, tmp_path):
        """shutdown() removes the IPC socket file."""
        mock_proc = MockPopen.return_value
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0

        sock_path = str(tmp_path / "test-cinegatto-cleanup.sock")
        player = MpvPlayer(mpv_args=[], socket_path=sock_path)
        player._process = mock_proc
        player._ipc = MagicMock()
        player._running = True
        player._watchdog_thread = None

        # Create a fake socket file
        with open(sock_path, "w") as f:
            f.write("")

        player.shutdown()
        assert not os.path.exists(sock_path)


class TestMpvPlayerEvents:
    """Test event handler logic without real mpv."""

    def _make_player_with_callbacks(self, on_video_end=None):
        """Create a player with mocked IPC, register event handlers, return (player, ipc)."""
        mock_ipc = MagicMock()
        # Capture on_event registrations
        handlers = {}
        def fake_on_event(name, cb):
            handlers.setdefault(name, []).append(cb)
        mock_ipc.on_event.side_effect = fake_on_event

        player = MpvPlayer(mpv_args=[], socket_path="/tmp/test.sock",
                           on_video_end=on_video_end)
        player._ipc = mock_ipc
        player._running = True
        player._seeking = False
        player._register_event_handlers()
        return player, handlers

    def test_end_file_eof_triggers_on_video_end(self):
        called = []
        player, handlers = self._make_player_with_callbacks(on_video_end=lambda: called.append(True))
        handlers["end-file"][0]({"event": "end-file", "reason": "eof"})
        assert called == [True]

    def test_end_file_error_schedules_deferred_retry(self):
        called = []
        player, handlers = self._make_player_with_callbacks(on_video_end=lambda: called.append(True))
        handlers["end-file"][0]({"event": "end-file", "reason": "error", "file_error": "test"})
        # Should NOT call immediately (deferred via timer)
        assert called == []
        # But consecutive errors should increment
        assert player._consecutive_errors == 1

    def test_end_file_ignored_while_seeking(self):
        called = []
        player, handlers = self._make_player_with_callbacks(on_video_end=lambda: called.append(True))
        player._seeking = True
        handlers["end-file"][0]({"event": "end-file", "reason": "error", "file_error": "test"})
        assert called == []
        assert player._consecutive_errors == 0

    def test_end_file_stop_reason_ignored(self):
        called = []
        player, handlers = self._make_player_with_callbacks(on_video_end=lambda: called.append(True))
        handlers["end-file"][0]({"event": "end-file", "reason": "stop"})
        assert called == []

    def test_playback_restart_clears_seeking(self):
        player, handlers = self._make_player_with_callbacks(on_video_end=lambda: None)
        player._seeking = True
        handlers["playback-restart"][0]({"event": "playback-restart"})
        assert player._seeking is False

    def test_end_file_error_uses_gate_cooldown_delay(self):
        """When yt_gate is blocked, retry delay uses gate cooldown time."""
        from unittest.mock import patch
        from cinegatto.youtube_gate import YouTubeGate
        gate = YouTubeGate(threshold=1, cooldown_sec=300)
        gate.record_failure()  # trip the gate

        timer_delays = []
        def mock_timer(delay, fn):
            timer_delays.append(delay)
            t = MagicMock()
            t.daemon = True
            return t

        player, handlers = self._make_player_with_callbacks(on_video_end=lambda: None)
        with patch("cinegatto.player.mpv_player.threading.Timer", side_effect=mock_timer):
            with patch("cinegatto.youtube_gate.yt_gate", gate):
                handlers["end-file"][0]({"event": "end-file", "reason": "error", "file_error": "blocked"})

        assert len(timer_delays) == 1
        # Delay should be based on gate cooldown (~300s + 5), not the normal 2s
        assert timer_delays[0] > 100
