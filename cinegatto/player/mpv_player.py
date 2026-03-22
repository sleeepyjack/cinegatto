"""MpvPlayer — manages an mpv child process and communicates via JSON IPC."""

import logging
import os
import subprocess
import threading
import time
from typing import Callable, Optional

from cinegatto.player.mpv_ipc import MpvIpc, MpvIpcError
from cinegatto.player.types import PlayerState

logger = logging.getLogger("cinegatto.player")

DEFAULT_SOCKET_PATH = "/tmp/cinegatto-mpv.sock"


class MpvPlayer:
    """Video player backed by an mpv child process with JSON IPC control."""

    def __init__(self, mpv_args: list[str] = None, socket_path: str = DEFAULT_SOCKET_PATH,
                 watchdog_timeout: float = 10.0, on_video_end: Optional[Callable] = None):
        self._mpv_args = mpv_args or []
        self._socket_path = socket_path
        self._watchdog_timeout = watchdog_timeout
        self._on_video_end = on_video_end
        self._process: Optional[subprocess.Popen] = None
        self._ipc: Optional[MpvIpc] = None
        self._running = False
        self._watchdog_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Spawn mpv process and connect IPC."""
        self._cleanup_socket()
        self._spawn_mpv()
        self._connect_ipc()
        self._running = True
        self._start_watchdog()
        logger.info("Player started", extra={"socket": self._socket_path})

    def _spawn_mpv(self) -> None:
        """Start the mpv child process in idle mode."""
        cmd = [
            "mpv",
            "--idle=yes",
            f"--input-ipc-server={self._socket_path}",
            "--no-terminal",
            "--force-window=yes",
        ] + self._mpv_args

        logger.debug("Spawning mpv", extra={"cmd": cmd})
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _connect_ipc(self, retries: int = 10, delay: float = 0.5) -> None:
        """Connect to the mpv IPC socket, retrying until it's available."""
        for attempt in range(retries):
            try:
                self._ipc = MpvIpc(self._socket_path)
                return
            except (ConnectionRefusedError, FileNotFoundError):
                if self._process.poll() is not None:
                    raise RuntimeError("mpv process exited unexpectedly")
                logger.debug("Waiting for mpv socket (attempt %d/%d)", attempt + 1, retries)
                time.sleep(delay)
        raise RuntimeError(f"Could not connect to mpv IPC after {retries} attempts")

    def _cleanup_socket(self) -> None:
        """Remove stale socket file if it exists."""
        if os.path.exists(self._socket_path):
            logger.debug("Removing stale socket", extra={"path": self._socket_path})
            os.unlink(self._socket_path)

    def load_video(self, url: str, start_percent: float = None) -> None:
        """Load a video URL into mpv.

        If start_percent is given (0-100), mpv will seek to that percentage
        after the file loads internally — no need to wait for file-loaded.
        """
        if start_percent is not None:
            options = f"start={start_percent:.1f}%"
            logger.info("Loading video", extra={"url": url, "start": f"{start_percent:.1f}%"})
            self._ipc.command("loadfile", url, "replace", options)
        else:
            logger.info("Loading video", extra={"url": url})
            self._ipc.command("loadfile", url)

    def play(self) -> None:
        """Resume playback."""
        logger.debug("Resuming playback")
        self._ipc.set_property("pause", False)

    def pause(self) -> None:
        """Pause playback."""
        logger.debug("Pausing playback")
        self._ipc.set_property("pause", True)

    def seek(self, position: float) -> None:
        """Seek to an absolute position in seconds."""
        logger.debug("Seeking", extra={"position": position})
        self._ipc.command("seek", position, "absolute")

    def get_state(self) -> PlayerState:
        """Read current player state from mpv properties."""
        try:
            paused = self._ipc.get_property("pause")
            position = self._ipc.get_property("time-pos") or 0.0
            duration = self._ipc.get_property("duration") or 0.0
            title = None
            url = None
            try:
                title = self._ipc.get_property("media-title")
            except Exception:
                pass
            try:
                url = self._ipc.get_property("path")
            except Exception:
                pass
            return PlayerState(
                playing=not paused,
                video_url=url,
                video_title=title,
                position=position,
                duration=duration,
            )
        except Exception:
            logger.debug("Could not read player state (mpv may be idle)")
            return PlayerState()

    def shutdown(self) -> None:
        """Gracefully shut down mpv."""
        logger.info("Shutting down player")
        self._running = False

        # Stop watchdog
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=3)

        # Send quit via IPC if possible
        if self._ipc:
            try:
                self._ipc.command("quit")
            except Exception:
                pass
            self._ipc.close()

        # Terminate process
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("mpv did not exit, sending SIGKILL")
                self._process.kill()
                self._process.wait(timeout=2)

        self._cleanup_socket()
        logger.info("Player shut down")

    # --- Watchdog ---

    def _start_watchdog(self) -> None:
        """Start a background thread that pings mpv periodically."""
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="mpv-watchdog"
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        """Periodically check mpv is responsive."""
        interval = self._watchdog_timeout / 2
        while self._running:
            time.sleep(interval)
            if not self._running:
                break
            try:
                # Check process is alive
                if self._process and self._process.poll() is not None:
                    logger.error("mpv process died (exit code %s)", self._process.returncode)
                    if self._running:
                        self._restart()
                    break

                # Ping via IPC
                self._ipc.get_property("pause")
            except Exception as e:
                logger.warning("Watchdog ping failed: %s", e)
                if self._running:
                    self._restart()
                break

    def _restart(self) -> None:
        """Restart mpv after a crash."""
        logger.info("Restarting mpv")
        if self._ipc:
            self._ipc.close()
        self._cleanup_socket()
        try:
            self._spawn_mpv()
            self._connect_ipc()
            self._start_watchdog()
            logger.info("mpv restarted successfully")
        except Exception:
            logger.exception("Failed to restart mpv")
