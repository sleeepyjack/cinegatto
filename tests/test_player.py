import json
import os
import socket
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from cinegatto.player.types import PlayerState, Player
from cinegatto.player.mpv_ipc import MpvIpc
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
    def _make_mock_socket(self, responses):
        """Create a mock socket that returns pre-canned JSON responses."""
        mock_sock = MagicMock()
        # Simulate reading newline-delimited JSON responses
        response_bytes = b"".join(
            json.dumps(r).encode() + b"\n" for r in responses
        )
        mock_sock.makefile.return_value.__enter__ = MagicMock(return_value=MagicMock(
            readline=MagicMock(side_effect=[
                json.dumps(r).encode() + b"\n" for r in responses
            ] + [b""])
        ))
        mock_sock.makefile.return_value.__exit__ = MagicMock(return_value=False)
        return mock_sock

    def test_send_command_formats_json(self):
        """command() sends correctly formatted JSON over the socket."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._sock = MagicMock()
        ipc._lock = threading.Lock()
        ipc._reader = MagicMock()
        ipc._reader.readline.return_value = json.dumps(
            {"error": "success", "data": None, "request_id": 1}
        ).encode() + b"\n"
        ipc._request_id = 0

        result = ipc.command("loadfile", "http://example.com/video")

        sent_data = ipc._sock.sendall.call_args[0][0]
        sent_json = json.loads(sent_data.decode())
        assert sent_json["command"] == ["loadfile", "http://example.com/video"]
        assert "request_id" in sent_json

    def test_get_property(self):
        """get_property sends correct command and returns data."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._sock = MagicMock()
        ipc._lock = threading.Lock()
        ipc._reader = MagicMock()
        ipc._reader.readline.return_value = json.dumps(
            {"error": "success", "data": True, "request_id": 1}
        ).encode() + b"\n"
        ipc._request_id = 0

        result = ipc.get_property("pause")
        sent_data = ipc._sock.sendall.call_args[0][0]
        sent_json = json.loads(sent_data.decode())
        assert sent_json["command"] == ["get_property", "pause"]
        assert result is True

    def test_set_property(self):
        """set_property sends correct command."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._sock = MagicMock()
        ipc._lock = threading.Lock()
        ipc._reader = MagicMock()
        ipc._reader.readline.return_value = json.dumps(
            {"error": "success", "data": None, "request_id": 1}
        ).encode() + b"\n"
        ipc._request_id = 0

        ipc.set_property("pause", True)
        sent_data = ipc._sock.sendall.call_args[0][0]
        sent_json = json.loads(sent_data.decode())
        assert sent_json["command"] == ["set_property", "pause", True]

    def test_command_error_raises(self):
        """An mpv error response raises an exception."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._sock = MagicMock()
        ipc._lock = threading.Lock()
        ipc._reader = MagicMock()
        ipc._reader.readline.return_value = json.dumps(
            {"error": "property not found", "data": None, "request_id": 1}
        ).encode() + b"\n"
        ipc._request_id = 0

        with pytest.raises(Exception, match="property not found"):
            ipc.get_property("nonexistent")

    def test_skips_event_lines(self):
        """IPC reader skips event lines and reads the actual response."""
        ipc = MpvIpc.__new__(MpvIpc)
        ipc._sock = MagicMock()
        ipc._lock = threading.Lock()
        ipc._reader = MagicMock()
        # First readline returns an event, second returns the actual response
        ipc._reader.readline.side_effect = [
            json.dumps({"event": "file-loaded"}).encode() + b"\n",
            json.dumps({"error": "success", "data": 42.0, "request_id": 1}).encode() + b"\n",
        ]
        ipc._request_id = 0
        ipc._event_callbacks = {}

        result = ipc.get_property("duration")
        assert result == 42.0


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
