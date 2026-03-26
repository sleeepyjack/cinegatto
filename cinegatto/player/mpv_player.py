"""MpvPlayer — manages an mpv child process and communicates via JSON IPC.

Lifecycle: mpv is started in --idle mode (no file loaded) so the IPC socket
is available immediately. Videos are loaded later via loadfile commands.

Key design decisions:

  Watchdog: A background thread periodically pings mpv via IPC. If mpv crashes
  or becomes unresponsive, the watchdog triggers _restart() which re-spawns mpv
  and reconnects IPC. This is critical for a headless Pi that must keep running
  unattended.

  Seeking flag (_seeking): mpv fires an "end-file" event with reason "error"
  when a seek interrupts a loading file. Without the flag, the end-file handler
  would interpret this as a playback failure and trigger next_video, skipping
  the video the user just seeked in. The flag is set in seek() and cleared on
  "playback-restart" (mpv signals that decoding resumed after the seek).

  Restart retry logic: _restart() uses exponential backoff (2^attempt seconds,
  capped at 30s) with a maximum of 5 attempts. Each attempt re-spawns mpv,
  reconnects IPC, and re-registers event handlers. If all attempts fail, the
  player stops (the watchdog is not restarted). In practice, transient failures
  (e.g., socket file race) usually succeed on the second attempt.

  Event handler registration: Handlers are registered via the IPC on_event()
  mechanism, which means they run on the IPC reader thread. To avoid blocking
  that thread (and risking deadlock — see mpv_ipc.py), retry delays use
  threading.Timer to defer the callback to a new thread.
"""

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
        # Guards against spurious end-file events during seeks. See module docstring.
        self._seeking = False

    def start(self) -> None:
        """Spawn mpv process and connect IPC."""
        self._cleanup_socket()
        self._spawn_mpv()
        self._connect_ipc()
        self._register_event_handlers()
        logger.debug("Event handlers registered")
        self._running = True
        self._start_watchdog()
        logger.info("Player started", extra={"socket": self._socket_path})

    def _register_event_handlers(self) -> None:
        """Register IPC event callbacks for end-of-file handling.

        Callbacks run on the IPC reader thread — must be non-blocking.
        Retry backoff is handled via deferred timers, not inline sleep.
        """
        if self._on_video_end:
            self._consecutive_errors = 0

            def handle_playback_restart(_event):
                # "playback-restart" means mpv resumed decoding (after a seek
                # or new file load). Safe to clear the seeking flag now.
                self._seeking = False
                self._consecutive_errors = 0
                # Successful playback — reset the YouTube gate
                from cinegatto.youtube_gate import yt_gate
                yt_gate.record_success()

            self._ipc.on_event("playback-restart", handle_playback_restart)

            def handle_end_file(event):
                # mpv fires end-file for several reasons:
                #   "eof"   — video finished naturally -> advance to next
                #   "error" — load/decode failure -> retry with backoff
                #   "stop"  — user loaded a new file -> ignore (not a failure)
                reason = event.get("reason", "")
                error = event.get("file_error", "")
                # Seeks can trigger spurious end-file with reason "error"
                if self._seeking:
                    logger.debug("Ignoring end-file during seek", extra={"reason": reason})
                    return
                if reason == "eof":
                    logger.info("Video ended (EOF)")
                    self._consecutive_errors = 0
                    self._on_video_end()
                elif reason == "error":
                    from cinegatto.youtube_gate import yt_gate
                    self._consecutive_errors += 1
                    yt_gate.record_failure()
                    logger.warning("Video failed to load (attempt %d)",
                                   self._consecutive_errors, extra={"error": error})
                    if not self._running:
                        return
                    # Check circuit breaker — if tripped, don't retry until cooldown
                    if yt_gate.is_blocked():
                        logger.info("YouTube gate blocked, pausing retries for %ds",
                                    int(yt_gate.time_remaining()))
                        delay = int(yt_gate.time_remaining()) + 5
                        self._consecutive_errors = 0
                    elif self._consecutive_errors >= 5:
                        logger.error("Too many consecutive errors, retrying in 30s")
                        delay = 30
                        self._consecutive_errors = 0
                    else:
                        delay = 2
                    t = threading.Timer(delay, self._deferred_video_end)
                    t.daemon = True
                    t.start()
                # reason "stop" = we loaded a new file (user action), not a failure

            self._ipc.on_event("end-file", handle_end_file)

    def _deferred_video_end(self) -> None:
        """Called after a delay to retry video advance (non-blocking)."""
        if self._running and self._on_video_end:
            self._on_video_end()

    def _spawn_mpv(self) -> None:
        """Start the mpv child process in idle mode."""
        cmd = [
            "mpv",
            "--idle=yes",
            f"--input-ipc-server={self._socket_path}",
            "--no-terminal",
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
        Uses mpv 0.38+ JSON IPC format: loadfile url flags index options_dict
        """
        if start_percent is not None:
            options = {"start": f"{start_percent:.1f}%"}
            logger.debug("Loading video", extra={"url": url, "start": f"{start_percent:.1f}%"})
            self._ipc.command("loadfile", url, "replace", -1, options)
        else:
            logger.debug("Loading video", extra={"url": url})
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
        """Seek to an absolute position in seconds.

        Sets _seeking=True BEFORE issuing the command to ensure any end-file
        event triggered by the seek is suppressed. Cleared on playback-restart.
        """
        logger.debug("Seeking", extra={"position": position})
        self._seeking = True
        self._ipc.command("seek", position, "absolute")

    def show_video(self, visible: bool) -> None:
        """Show or black out the video. Overlays remain visible.

        Uses brightness/contrast=-100 instead of hiding the window because
        mpv overlays (QR code, cat art) must stay visible even when paused.
        """
        try:
            self._ipc.set_property("brightness", 0 if visible else -100)
            self._ipc.set_property("contrast", 0 if visible else -100)
            logger.debug("Video %s", "visible" if visible else "blacked out")
        except Exception:
            pass

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
        """Start a background thread that pings mpv periodically.

        Polls at half the watchdog_timeout interval so a crash is detected
        within one full timeout period.
        """
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
        """Restart mpv after a crash, with two phases:

        Phase 1 (fast): 5 attempts with exponential backoff (2s, 4s, 8s, 16s, 30s).
        Phase 2 (slow): retry every 60s indefinitely until success or shutdown.

        This ensures the player eventually recovers even from prolonged issues
        (e.g., network outage that prevents mpv from initializing DRM).
        """
        # Phase 1: fast retries
        max_fast = 5
        for attempt in range(1, max_fast + 1):
            if not self._running:
                return
            logger.info("Restarting mpv (fast %d/%d)", attempt, max_fast)
            if self._try_restart():
                return
            delay = min(2 ** attempt, 30)
            time.sleep(delay)

        # Phase 2: slow retries every 60s
        logger.error("Fast restart failed after %d attempts, entering slow retry", max_fast)
        while self._running:
            time.sleep(60)
            if not self._running:
                return
            logger.info("Restarting mpv (slow retry)")
            if self._try_restart():
                return

    def _try_restart(self) -> bool:
        """Single restart attempt. Returns True on success."""
        if self._ipc:
            self._ipc.close()
        self._cleanup_socket()
        try:
            self._spawn_mpv()
            self._connect_ipc()
            self._register_event_handlers()
            self._start_watchdog()
            logger.info("mpv restarted successfully")
            return True
        except Exception:
            logger.exception("Restart attempt failed")
            return False
