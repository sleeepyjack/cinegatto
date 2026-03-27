"""Flask blueprint for the cinegatto REST API."""

import logging

from flask import Blueprint, jsonify, request

logger = logging.getLogger("cinegatto.api")

api = Blueprint("api", __name__, url_prefix="/api")

_controller = None
_ring_handler = None
_cache_service = None
_playlist_url = None


def init_api(controller, ring_handler=None, cache_service=None, playlist_url=None):
    global _controller, _ring_handler, _cache_service, _playlist_url
    _controller = controller
    _ring_handler = ring_handler
    _cache_service = cache_service
    _playlist_url = playlist_url


@api.route("/play", methods=["POST"])
def play():
    logger.info("API: play")
    _controller.play()
    return jsonify({"status": "ok"})


@api.route("/pause", methods=["POST"])
def pause():
    logger.info("API: pause")
    _controller.pause()
    return jsonify({"status": "ok"})


@api.route("/next", methods=["POST"])
def next_video():
    logger.info("API: next")
    _controller.next_video()
    return jsonify({"status": "ok"})


@api.route("/previous", methods=["POST"])
def previous_video():
    logger.info("API: previous")
    _controller.previous_video()
    return jsonify({"status": "ok"})


@api.route("/random_seek", methods=["POST"])
def random_seek():
    logger.info("API: random_seek")
    _controller.random_seek()
    return jsonify({"status": "ok"})


@api.route("/status", methods=["GET"])
def status():
    return jsonify(_controller.get_status())


@api.route("/settings", methods=["GET"])
def get_settings():
    settings = _controller.get_settings()
    if _playlist_url:
        settings["playlist_url"] = _playlist_url
    return jsonify(settings)


@api.route("/settings", methods=["POST"])
def update_settings():
    data = request.get_json(silent=True) or {}
    # Strict bool validation — bool("false") == True in Python, so we must
    # reject non-bool types to avoid silently applying the wrong setting.
    for key in ("shuffle", "random_start"):
        if key in data and not isinstance(data[key], bool):
            return jsonify({"error": f"{key} must be a boolean"}), 400
    if "shuffle" in data:
        _controller.set_shuffle(data["shuffle"])
    if "random_start" in data:
        _controller.set_random_start(data["random_start"])
    logger.info("API: settings updated", extra={"settings": data})
    return jsonify(_controller.get_settings())


@api.route("/system", methods=["GET"])
def system_status():
    """System health: CPU, memory, cache status."""
    import os
    from cinegatto.youtube_gate import yt_gate

    # CPU load (1-min avg, normalized to core count)
    try:
        load1 = os.getloadavg()[0]
        cpu_count = os.cpu_count() or 4
        cpu_pct = round(load1 / cpu_count * 100, 1)
    except OSError:
        cpu_pct = 0

    # Memory usage
    mem_pct = 0
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                parts = line.split(":")
                info[parts[0].strip()] = int(parts[1].split()[0])
            total = info["MemTotal"]
            avail = info["MemAvailable"]
            mem_pct = round((1 - avail / total) * 100, 1)
    except Exception:
        pass  # macOS or /proc unavailable

    # Temperature (Pi thermal zone)
    temp_c = 0
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp_c = round(int(f.read().strip()) / 1000, 1)
    except Exception:
        pass

    # Cache
    cache_info = {"cache_count": 0, "cache_total": 0, "cache_downloading": False}
    if _cache_service and _controller:
        stats = _cache_service.get_stats()
        max_size = stats["max_size"] or 1
        cache_info = {
            "cache_count": stats["count"],
            "cache_total": len(_controller.get_playlist_entries()),
            "cache_downloading": stats["current_download"] is not None,
            "cache_disk_pct": round(stats["total_size"] / max_size * 100, 1),
            "cache_size_mb": stats["total_size_mb"],
            "cache_max_mb": stats["max_size_mb"],
        }

    return jsonify({
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "temp_c": temp_c,
        "yt_blocked": yt_gate.is_blocked(),
        **cache_info,
    })


@api.route("/cache", methods=["GET"])
def cache():
    if _cache_service is None:
        return jsonify({"enabled": False})
    from cinegatto.youtube_gate import yt_gate
    stats = _cache_service.get_stats()
    stats["enabled"] = True
    stats["yt_blocked"] = yt_gate.is_blocked()
    stats["yt_cooldown_remaining"] = round(yt_gate.time_remaining())
    return jsonify(stats)


@api.route("/sync", methods=["POST"])
def sync():
    """Refresh playlist from YouTube, then enqueue uncached videos for download."""
    from cinegatto.app import refresh_playlist

    if not _controller:
        return jsonify({"status": "error", "message": "not ready"}), 503

    # Refresh playlist (shared logic with background refresh loop)
    playlist_refreshed = False
    if _playlist_url:
        # Note: refresh_playlist needs direct selector access for update_entries.
        # This is the one place where the API reaches into controller internals,
        # justified by the shared helper pattern.
        playlist_refreshed = refresh_playlist(
            _playlist_url, _controller._selector, _cache_service)
        if playlist_refreshed:
            logger.info("Playlist synced via API")

    # Cache sync
    cache_result = {}
    if _cache_service:
        all_entries = _controller.get_playlist_entries()
        cache_result = _cache_service.warm_all(all_entries)

    return jsonify({
        "status": "ok",
        "playlist_refreshed": playlist_refreshed,
        "playlist_count": len(_controller.get_playlist_entries()),
        **cache_result,
    })


@api.route("/logs", methods=["GET"])
def logs():
    if _ring_handler is None:
        return jsonify({"entries": []})
    level = request.args.get("level", None)
    limit = request.args.get("limit", 100, type=int)
    limit = min(limit, 500)
    entries = _ring_handler.get_entries(level=level, limit=limit)
    return jsonify({"entries": entries})
