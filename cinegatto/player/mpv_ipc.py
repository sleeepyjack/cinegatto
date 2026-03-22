"""Thin wrapper around mpv's JSON IPC protocol over a Unix domain socket."""

import json
import logging
import socket
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger("cinegatto.player.ipc")


class MpvIpcError(Exception):
    pass


class MpvIpc:
    """Communicate with a running mpv instance via its JSON IPC socket."""

    def __init__(self, socket_path: str, timeout: float = 5.0):
        self._socket_path = socket_path
        self._timeout = timeout
        self._lock = threading.Lock()
        self._request_id = 0
        self._event_callbacks: dict[str, list[Callable]] = {}

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(socket_path)
        self._reader = self._sock.makefile("rb")
        logger.debug("Connected to mpv IPC at %s", socket_path)

    def command(self, *args: Any) -> Any:
        """Send a command to mpv and return the response data."""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id
            msg = {"command": list(args), "request_id": req_id}
            self._sock.sendall(json.dumps(msg).encode() + b"\n")
            return self._read_response(req_id)

    def get_property(self, name: str) -> Any:
        """Get a property value from mpv."""
        return self.command("get_property", name)

    def set_property(self, name: str, value: Any) -> None:
        """Set a property value on mpv."""
        self.command("set_property", name, value)

    def _read_response(self, request_id: int) -> Any:
        """Read lines until we get a response matching our request_id.

        Event lines (no request_id) are dispatched to callbacks and skipped.
        """
        while True:
            line = self._reader.readline()
            if not line:
                raise MpvIpcError("Connection closed")

            data = json.loads(line)

            # Event message — dispatch and continue
            if "event" in data and "request_id" not in data:
                event_name = data["event"]
                logger.debug("mpv event: %s", event_name, extra={"event": data})
                for cb in self._event_callbacks.get(event_name, []):
                    try:
                        cb(data)
                    except Exception:
                        logger.exception("Error in event callback for %s", event_name)
                continue

            # Response message
            if data.get("error") != "success":
                raise MpvIpcError(data.get("error", "unknown error"))
            return data.get("data")

    def on_event(self, event_name: str, callback: Callable) -> None:
        """Register a callback for an mpv event."""
        self._event_callbacks.setdefault(event_name, []).append(callback)

    def close(self) -> None:
        """Close the IPC connection."""
        try:
            self._reader.close()
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass
        logger.debug("IPC connection closed")
