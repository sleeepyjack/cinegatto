"""Application bootstrap — wires all components together."""

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
    return platform.system() == "Linux" and platform.machine().startswith("aarch64")


def _get_lan_ip() -> str:
    """Get the LAN IP address of this machine."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _create_display():
    if _is_pi():
        from cinegatto.display.pi import PiDisplay
        return PiDisplay()
    return NoopDisplay()


def _fetch_with_retry(playlist_url: str, max_attempts: int = 5, base_delay: float = 5.0) -> list[dict]:
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
    ring_handler = setup_logging(
        level=config["log_level"],
        ring_size=config["log_ring_size"],
        log_file=config.get("log_file"),
    ).handlers[1]  # second handler is the ring buffer
    # Find the ring buffer handler more robustly
    root_logger = logging.getLogger("cinegatto")
    ring_handler = None
    for h in root_logger.handlers:
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
    logger.info("Fetching playlist...")
    try:
        entries = _fetch_with_retry(playlist_url)
    except Exception:
        logger.error("Could not fetch playlist after retries. Entering standby.")
        display.power_off()
        # Keep retrying in background
        entries = _standby_until_playlist(playlist_url, display)

    # Build components — use a mutable ref so player can call controller
    controller_ref = [None]

    def on_video_end():
        if controller_ref[0]:
            controller_ref[0].on_video_end()

    mpv_args = ["--no-audio"] if not config["audio"] else []
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
        logger.warning("Could not apply overlays")

    # Cache setup
    cache_manager = None
    downloader = None
    if config["cache_enabled"]:
        from cinegatto.cache.manager import CacheManager
        from cinegatto.cache.downloader import Downloader
        cache_path = config["cache_path"]
        if not cache_path:
            cache_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
        cache_path = os.path.expanduser(cache_path)
        max_bytes = int(config["cache_max_size_gb"] * 1024**3)
        cache_manager = CacheManager(cache_path, max_bytes)
        downloader = Downloader(cache_manager, config["cache_format"])
        downloader.start()

    selector = Selector(entries, shuffle=config["shuffle"])
    controller = PlaybackController(
        player=player, selector=selector, display=display,
        random_start=config["random_start"],
        cache_manager=cache_manager, downloader=downloader,
    )
    controller_ref[0] = controller
    controller.start()

    # Set up Flask
    app = Flask(__name__,
                static_folder=os.path.join(os.path.dirname(__file__), "web", "static"),
                static_url_path="/static")
    init_api(controller, ring_handler, cache_manager=cache_manager)
    app.register_blueprint(api)

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    # Graceful shutdown
    def shutdown_handler(signum, frame):
        logger.info("Received signal %s, shutting down", signum)
        controller.stop()
        if downloader:
            downloader.stop()
        player.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # Start periodic playlist refresh
    refresh_thread = threading.Thread(
        target=_playlist_refresh_loop,
        args=(playlist_url, selector, cache_manager),
        daemon=True,
        name="playlist-refresh",
    )
    refresh_thread.start()

    # Auto-play first video
    logger.info("Auto-playing first video")
    controller.next_video()

    # Start Flask (blocking)
    logger.info("Starting API server on port %d", config["api_port"])
    app.run(host="0.0.0.0", port=config["api_port"], threaded=True, use_reloader=False)


def _standby_until_playlist(playlist_url: str, display) -> list[dict]:
    """Keep retrying playlist fetch until successful."""
    while True:
        time.sleep(60)
        try:
            entries = fetch_playlist(playlist_url)
            logger.info("Playlist fetched after standby retry")
            display.power_on()
            return entries
        except Exception as e:
            logger.debug("Standby retry failed: %s", e)


def _playlist_refresh_loop(playlist_url: str, selector: Selector,
                           cache_manager=None, interval: float = 1800) -> None:
    """Periodically re-fetch playlist metadata (every 30 min by default)."""
    while True:
        time.sleep(interval)
        try:
            entries = fetch_playlist(playlist_url)
            selector.update_entries(entries)
            if cache_manager:
                playlist_ids = {e["id"] for e in entries}
                cache_manager.cleanup(playlist_ids)
        except Exception as e:
            logger.warning("Playlist refresh failed: %s", e)
