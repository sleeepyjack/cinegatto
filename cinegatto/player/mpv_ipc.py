"""Thin wrapper around mpv's JSON IPC protocol over a Unix domain socket.

Threading architecture and why events/responses are decoupled:

mpv sends two kinds of JSON messages over the socket, interleaved on the
same stream:
  1. Events (unsolicited): {"event": "end-file", ...}
  2. Command responses (solicited): {"request_id": 42, "data": ..., "error": "success"}

A single dedicated reader thread (_read_loop) continuously reads lines from
the socket and routes them:
  - Events go to registered callbacks (dispatched immediately on the reader thread).
  - Responses go to per-request Queue objects that the calling thread is blocking on.

Why a dedicated reader thread instead of read-on-demand:
  - mpv can send events at any time. If no one is reading, the socket buffer
    fills up, stalling mpv's event delivery.
  - Without a reader thread, a command() call would have to skip over any
    interleaved events while looking for its response, creating ordering bugs.
  - Separating read from write means multiple threads can issue commands
    concurrently (serialized by _write_lock) without worrying about whose
    response they'll read.

DEADLOCK RISK: Event callbacks run on the reader thread. If a callback calls
command() and the write lock is held by another thread that's waiting for a
response (which the blocked reader can't deliver), you get a deadlock. Callers
must keep callbacks non-blocking — use threading.Timer or queue dispatch to
defer IPC calls out of the callback.
"""

import json
import logging
import queue
import socket
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger("cinegatto.player.ipc")


class MpvIpcError(Exception):
    pass


class MpvIpc:
    """Communicate with a running mpv instance via its JSON IPC socket.

    Architecture:
    - A background reader thread continuously reads from the socket.
    - Event messages are dispatched to registered callbacks on the reader thread.
    - Command responses are routed to the calling thread via per-request queues.
    - Commands are serialized via a write lock (one command at a time).
    """

    def __init__(self, socket_path: str, timeout: float = 5.0):
        self._socket_path = socket_path
        self._timeout = timeout
        # Serializes socket writes. Only one command can be in-flight at a time
        # because we need to register the response queue before sending, and
        # the request_id counter must be incremented atomically with the send.
        self._write_lock = threading.Lock()
        self._request_id = 0
        self._event_callbacks: dict[str, list[Callable]] = {}
        # Maps request_id -> Queue. The reader thread puts the response into
        # the queue; the calling thread blocks on queue.get(timeout).
        self._pending: dict[int, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._running = True

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(socket_path)
        self._reader = self._sock.makefile("rb")

        # Start dedicated reader thread
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="mpv-ipc-reader"
        )
        self._reader_thread.start()
        logger.debug("Connected to mpv IPC at %s", socket_path)

    def command(self, *args: Any) -> Any:
        """Send a command to mpv and return the response data.

        Blocks until mpv responds (or timeout).
        """
        with self._write_lock:
            self._request_id += 1
            req_id = self._request_id

            # Create a response queue for this request
            resp_queue: queue.Queue = queue.Queue()
            with self._pending_lock:
                self._pending[req_id] = resp_queue

            msg = {"command": list(args), "request_id": req_id}
            try:
                self._sock.sendall(json.dumps(msg).encode() + b"\n")
            except Exception:
                with self._pending_lock:
                    self._pending.pop(req_id, None)
                raise

        # Wait for the reader thread to deliver our response
        try:
            result = resp_queue.get(timeout=self._timeout)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise MpvIpcError(f"Timeout waiting for response to request {req_id}")

        if isinstance(result, Exception):
            raise result
        return result

    def get_property(self, name: str) -> Any:
        """Get a property value from mpv."""
        return self.command("get_property", name)

    def set_property(self, name: str, value: Any) -> None:
        """Set a property value on mpv."""
        self.command("set_property", name, value)

    def on_event(self, event_name: str, callback: Callable) -> None:
        """Register a callback for an mpv event.

        WARNING: Callbacks run on the reader thread. If a callback issues an
        IPC command (directly or indirectly), it will deadlock if another
        thread holds _write_lock and is waiting for a response. Always defer
        IPC-calling work to a separate thread (e.g., threading.Timer).
        """
        self._event_callbacks.setdefault(event_name, []).append(callback)

    def close(self) -> None:
        """Close the IPC connection and stop the reader thread."""
        self._running = False
        try:
            self._reader.close()
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass
        # Unblock any waiting callers
        with self._pending_lock:
            for rq in self._pending.values():
                rq.put(MpvIpcError("Connection closed"))
            self._pending.clear()
        logger.debug("IPC connection closed")

    # --- Reader thread ---

    def _read_loop(self) -> None:
        """Continuously read from socket, dispatching events and routing responses."""
        while self._running:
            try:
                line = self._reader.readline()
                if not line:
                    logger.debug("IPC socket closed (EOF)")
                    break
                data = json.loads(line)
            except (json.JSONDecodeError, OSError, ValueError):
                if self._running:
                    logger.debug("IPC read error, stopping reader")
                break

            if "event" in data and "request_id" not in data:
                # Unsolicited event — dispatch to callbacks on this thread.
                self._dispatch_event(data)
            elif "request_id" in data:
                # Solicited command response — route to the thread that sent
                # the command by putting the result into its per-request queue.
                req_id = data["request_id"]
                with self._pending_lock:
                    resp_queue = self._pending.pop(req_id, None)
                if resp_queue:
                    if data.get("error") != "success":
                        resp_queue.put(MpvIpcError(data.get("error", "unknown error")))
                    else:
                        resp_queue.put(data.get("data"))

        # Reader thread exiting — unblock any threads waiting on command responses.
        # Without this, those threads would hang forever on queue.get().
        with self._pending_lock:
            for rq in self._pending.values():
                rq.put(MpvIpcError("Connection closed"))
            self._pending.clear()

    def _dispatch_event(self, data: dict) -> None:
        """Dispatch an event to registered callbacks."""
        event_name = data["event"]
        logger.debug("mpv event: %s", event_name, extra={"event": data})
        for cb in self._event_callbacks.get(event_name, []):
            try:
                cb(data)
            except Exception:
                logger.exception("Error in event callback for %s", event_name)
