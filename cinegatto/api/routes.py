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
    if "shuffle" in data:
        _controller.set_shuffle(bool(data["shuffle"]))
    if "random_start" in data:
        _controller.set_random_start(bool(data["random_start"]))
    logger.info("API: settings updated", extra={"settings": data})
    return jsonify(_controller.get_settings())


@api.route("/cache", methods=["GET"])
def cache():
    if _cache_service is None:
        return jsonify({"enabled": False})
    stats = _cache_service.get_stats()
    stats["enabled"] = True
    return jsonify(stats)


@api.route("/sync", methods=["POST"])
def sync():
    """Refresh playlist from YouTube, then enqueue uncached videos for download."""
    from cinegatto.playlist.fetcher import fetch_playlist

    if not _controller:
        return jsonify({"status": "error", "message": "not ready"})

    # Refresh playlist
    playlist_refreshed = False
    if _playlist_url:
        try:
            entries = fetch_playlist(_playlist_url)
            _controller._selector.update_entries(entries)
            if _cache_service:
                playlist_ids = {e["id"] for e in entries}
                _cache_service.cleanup(playlist_ids)
            playlist_refreshed = True
            logger.info("Playlist synced via API", extra={"count": len(entries)})
        except Exception as e:
            logger.warning("Playlist sync failed: %s", e)

    # Cache sync
    cache_result = {}
    if _cache_service:
        all_entries = _controller._selector.get_all_entries()
        cache_result = _cache_service.warm_all(all_entries)

    return jsonify({
        "status": "ok",
        "playlist_refreshed": playlist_refreshed,
        "playlist_count": len(_controller._selector.get_all_entries()),
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
