"""Playlist fetcher — uses yt-dlp to extract playlist metadata."""

import logging
import threading

import yt_dlp

logger = logging.getLogger("cinegatto.playlist.fetcher")

_lock = threading.Lock()


def fetch_playlist(playlist_url: str, cookies_from_browser: str = "") -> list[dict]:
    """Fetch playlist metadata from YouTube using yt-dlp.

    Returns a list of video entries with id, title, and url.
    Uses extract_flat to avoid resolving each video's stream URL.
    """
    logger.info("Fetching playlist", extra={"url": playlist_url})

    ydl_opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }
    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)

    with _lock:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)

    entries = [e for e in (info.get("entries") or []) if e is not None]

    if not entries:
        raise ValueError(f"Playlist is empty or could not be fetched: {playlist_url}")

    logger.info("Playlist fetched", extra={"count": len(entries), "url": playlist_url})
    return entries
