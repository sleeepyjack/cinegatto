"""Application bootstrap — wires all components together.

This is the top-level entry point that orchestrates the startup sequence.
The ordering is intentional and load-bearing:

  1. Config + logging — must come first so all subsequent components log properly.
  2. Display — created early so we can power off the monitor if playlist fetch fails.
  3. Playlist fetch (with retry) — needed before we can build the Selector.
  4. Player (mpv) — started before the Controller because the Controller issues
     play commands immediately. mpv is launched in --idle mode so it's ready.
  5. QR overlay — applied right after the player starts, before any video loads.
  6. Cache service — optional, started before the Controller so cache lookups
     work from the very first video.
  7. Controller — depends on player, selector, display, and cache. Uses a
     mutable ref (controller_ref) to break the circular dependency where
     the player's on_video_end callback needs to call back into the controller,
     but the controller hasn't been constructed yet when the player is created.
  8. Flask API — registered after the controller exists so routes can reference it.
  9. Playlist refresh thread — daemon thread that periodically re-fetches the
     YouTube playlist and evicts removed videos from cache.
 10. Auto-play first video — fires only after everything is wired up.
 11. Flask run (blocking) — takes over the main thread. Shutdown is handled
     via signal handlers (SIGTERM/SIGINT) registered before this call.
"""

import logging
import os
import platform
import signal
import sys
import threading
import time

from flask import Flask

from cinegatto.api.routes import api, init_api
from cinegatto.config import load_config
from cinegatto.controller import PlaybackController
from cinegatto.display.noop import NoopDisplay
from cinegatto.log import RingBufferHandler, setup_logging
from cinegatto.player.mpv_player import MpvPlayer
from cinegatto.player.qr_overlay import apply_overlays
from cinegatto.playlist.fetcher import fetch_playlist
from cinegatto.playlist.selector import Selector

logger = logging.getLogger("cinegatto.app")


def _is_pi() -> bool:
    """Detect Raspberry Pi by OS + architecture. Avoids reading /proc files for portability."""
    return platform.system() == "Linux" and platform.machine().startswith("aarch64")


def _get_lan_ip() -> str:
    """Get the LAN IP address of this machine.

    Uses a UDP connect trick: connecting a UDP socket doesn't send any data,
    but the OS picks the right source interface for routing to 8.8.8.8,
    revealing our LAN IP. Works without actual network access.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _create_display():
    """Factory: real display control on Pi, no-op on macOS.

    Deferred import of PiDisplay avoids importing Pi-specific deps (vcgencmd,
    xrandr wrappers) on macOS where they don't exist.
    """
    if _is_pi():
        from cinegatto.display.pi import PiDisplay
        return PiDisplay()
    return NoopDisplay()


def _fetch_with_retry(playlist_url: str, max_attempts: int = 5,
                      base_delay: float = 5.0) -> list[dict]:
    """Fetch playlist with exponential backoff retry."""
    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_playlist(playlist_url)
        except Exception as e:
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Playlist fetch failed (attempt %d/%d), retrying in %.0fs",
                attempt, max_attempts, delay,
                extra={"error": str(e)},
            )
            if attempt == max_attempts:
                raise
            time.sleep(delay)


def run(config_path: str = None) -> None:
    """Main entry point — start cinegatto."""
    config = load_config(config_path=config_path)
    setup_logging(
        level=config["log_level"],
        ring_size=config["log_ring_size"],
        log_file=config.get("log_file"),
    )
    # Find the ring buffer handler by type (not positional index, which is fragile).
    ring_handler = None
    for h in logging.getLogger("cinegatto").handlers:
        if isinstance(h, RingBufferHandler):
            ring_handler = h
            break

    logger.info("Starting cinegatto", extra={"config": config})

    # Validate playlist URL
    playlist_url = config["playlist_url"]
    if not playlist_url:
        logger.error("No playlist_url configured. Set it in your config file.")
        sys.exit(1)

    # Build display
    display = _create_display()

    # Fetch playlist (with retry)
    logger.debug("Fetching playlist...")
    try:
        entries = _fetch_with_retry(playlist_url)
    except Exception:
        logger.error("Could not fetch playlist after retries. Entering standby.")
        display.power_off()
        # Keep retrying in background
        entries = _standby_until_playlist(playlist_url, display)

    # Break circular dependency: player needs on_video_end -> controller.next_video,
    # but controller needs player. We use a mutable list as an indirection layer
    # so the closure captures the list (stable reference) and reads [0] at call time
    # (after controller is assigned). A single-element list is used instead of a
    # nonlocal variable because on_video_end is defined before controller exists.
    controller_ref = [None]

    def on_video_end():
        if controller_ref[0]:
            controller_ref[0].on_video_end()

    mpv_args = ["--no-audio"] if not config["audio"] else []
    if not _is_pi():
        mpv_args.append("--force-window=yes")  # macOS needs a window; Pi uses DRM (no window)
    # Tell mpv where to find yt-dlp (systemd PATH doesn't include the venv)
    yt_dlp_path = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
    if os.path.isfile(yt_dlp_path):
        mpv_args.append(f"--script-opts=ytdl_hook-ytdl_path={yt_dlp_path}")
    mpv_args.extend(config.get("mpv_extra_args", []))

    player = MpvPlayer(
        mpv_args=mpv_args,
        watchdog_timeout=config["watchdog_timeout_sec"],
        on_video_end=on_video_end,
    )
    player.start()

    # QR code overlay pointing to web UI
    try:
        web_url = f"http://{_get_lan_ip()}:{config['api_port']}"
        apply_overlays(player._ipc, web_url)
    except Exception:
        logger.exception("Could not apply overlays")

    # Cache setup — gracefully degrade to streaming-only if cache dir is unavailable
    cache_service = None
    if config["cache_enabled"]:
        try:
            from cinegatto.cache.service import CacheService
            cache_path = config["cache_path"]
            if not cache_path:
                cache_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
            cache_path = os.path.expanduser(cache_path)
            cache_service = CacheService(
                cache_path, config["cache_format"],
                disk_usage_pct=config["cache_disk_usage_pct"],
            )
            cache_service.start()
        except Exception:
            logger.exception("Cache unavailable, continuing without caching")
            cache_service = None

    selector = Selector(entries, shuffle=config["shuffle"])
    controller = PlaybackController(
        player=player, selector=selector, display=display,
        random_start=config["random_start"],
        cache_service=cache_service,
    )
    controller_ref[0] = controller
    controller.start()

    # Set up Flask
    app = Flask(__name__,
                static_folder=os.path.join(os.path.dirname(__file__), "web", "static"),
                static_url_path="/static")
    init_api(controller, ring_handler, cache_service=cache_service, playlist_url=playlist_url)
    app.register_blueprint(api)

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    # Graceful shutdown — order matters: stop the controller (drains command queue),
    # then cache (kills any in-progress yt-dlp download), then player (terminates mpv).
    def shutdown_handler(signum, frame):
        logger.info("Received signal %s, shutting down", signum)
        controller.stop()
        if cache_service:
            cache_service.stop()
        player.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # Start periodic playlist refresh
    refresh_thread = threading.Thread(
        target=_playlist_refresh_loop,
        args=(playlist_url, selector, cache_service, config["playlist_refresh_sec"]),
        daemon=True,
        name="playlist-refresh",
    )
    refresh_thread.start()

    # Wake display and auto-play first video
    display.power_on()
    logger.info("Auto-playing first video")
    controller.next_video()

    # Warm entire playlist in background so the cache fills up ASAP.
    # This is the primary network resilience strategy: once cached,
    # playback works offline. Dedup in warm_all means this is safe
    # even though _load_video also calls warm() for the current video.
    if cache_service:
        cache_service.warm_all(entries)

    # Start Flask (blocking) — this is the last call in run(); it takes over the
    # main thread. use_reloader=False is critical: the reloader forks the process,
    # which would duplicate mpv and all daemon threads.
    logger.info("Starting API server on port %d", config["api_port"])
    app.run(host="0.0.0.0", port=config["api_port"], threaded=True, use_reloader=False)


def _standby_until_playlist(playlist_url: str, display) -> list[dict]:
    """Keep retrying playlist fetch until successful.

    Called when the initial fetch (with exponential backoff) exhausted all attempts.
    At this point the display is already powered off. We retry every 60s indefinitely
    because on a headless Pi there's nothing else to do — the network may come up later.
    """
    while True:
        time.sleep(60)
        try:
            entries = fetch_playlist(playlist_url)
            logger.info("Playlist fetched after standby retry")
            display.power_on()
            return entries
        except Exception as e:
            logger.debug("Standby retry failed: %s", e)


def refresh_playlist(playlist_url: str, selector: Selector, cache_service=None) -> bool:
    """Fetch playlist from YouTube, update selector, clean cache. Returns True on success."""
    try:
        entries = fetch_playlist(playlist_url)
        selector.update_entries(entries)
        if cache_service:
            playlist_ids = {e["id"] for e in entries}
            cache_service.cleanup(playlist_ids)
        return True
    except Exception as e:
        logger.warning("Playlist refresh failed: %s", e)
        return False


def _playlist_refresh_loop(playlist_url: str, selector: Selector,
                           cache_service=None, interval: float = 1800) -> None:
    """Periodically re-fetch playlist metadata."""
    while True:
        time.sleep(interval)
        refresh_playlist(playlist_url, selector, cache_service)
