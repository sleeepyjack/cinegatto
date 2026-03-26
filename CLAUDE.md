# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**cinegatto** is a cinema for cats — a Raspberry Pi 5 application that plays wildlife videos fullscreen from a curated YouTube playlist. Cats watch it; humans control it remotely via a mobile-optimized web UI.

Target hardware: Raspberry Pi 5 + 1080p HDMI monitor, headless (no keyboard/mouse/desktop environment).

## Core Design Constraints

- **Mac-testable**: Core functionality runs on macOS for local development. Pi-specific code (display management via DDC/CI, DRM video output) is isolated behind interfaces.
- **Headless Pi**: No desktop environment. mpv renders directly via DRM/KMS (`vo=drm`).
- **Single-purpose**: The Pi runs only cinegatto. Provisioning is automated via `scripts/bootstrap.sh`.
- **LAN-only API**: No authentication needed. Flask serves the API and web UI.
- **Cache-first**: Videos are downloaded to local cache. Playback prefers cached files; streaming is only used as a first-run bootstrap until the cache warms up.

## Architecture

```
cinegatto/
├── app.py              # Bootstrap, wiring, startup sequence (11 ordered steps)
├── controller.py       # Command queue serializes all player mutations
├── config.py           # JSON config loading + type/bounds validation
├── log.py              # Structured JSON logging + ring buffer for /api/logs
├── cache/
│   └── service.py      # Unified cache index + background yt-dlp downloads
├── player/
│   ├── mpv_ipc.py      # mpv JSON IPC with dedicated reader thread
│   ├── mpv_player.py   # Process lifecycle, watchdog, event handling
│   ├── qr_overlay.py   # QR code + ASCII art overlays on video
│   └── types.py        # Player protocol
├── playlist/
│   ├── fetcher.py      # yt-dlp playlist metadata extraction
│   └── selector.py     # Shuffle/sequential selection with history
├── display/
│   ├── pi.py           # DDC/CI monitor power control (Pi 5)
│   └── noop.py         # No-op for macOS development
├── api/
│   └── routes.py       # Flask REST API + settings + cache + sync
└── web/
    └── static/
        └── index.html  # Single-file mobile web UI
```

## Key Implemented Features

1. **Autoplay on boot** — systemd service, plays random video from playlist
2. **Random video + random start position** — configurable shuffle and seek
3. **Infinite loop** — playlist loops with no-repeat in shuffle mode
4. **Web UI + REST API** — play/pause, next, previous, random seek, settings, sync, logs
5. **Monitor standby on pause** — DDC/CI via ddcutil (Pi 5), brightness hack on macOS
6. **Video caching** — background downloads, LRU eviction, retry on failure, pre-check size
7. **Network resilience** — cache-first playback, auto-warm on startup, download retry
8. **QR code overlay** — scannable link to web UI, repositions on playback-restart
9. **Provisioning** — single `curl | bash` command from bare Pi OS to running service

## Key Threading Constraints

- **Controller command queue**: All player mutations go through a single worker thread. API threads enqueue, never touch the player directly.
- **IPC reader thread**: Dedicated thread reads mpv socket. Event callbacks run on this thread — they MUST NOT make IPC calls (deadlock). Defer to threading.Timer.
- **Cache download worker**: Single background thread, one yt-dlp subprocess at a time.
- **Seeking flag**: Set on seek, cleared on playback-restart. Suppresses spurious end-file events.

## Logging Philosophy

Log at `info` level for user-visible actions (play, pause, next, cache hit/miss, downloads). Use `debug` for internals (mpv events, IPC commands, overlay positioning). Werkzeug HTTP logs are suppressed. Structured JSON output.

## Provisioning

```bash
curl -sSL https://raw.githubusercontent.com/sleeepyjack/cinegatto/main/scripts/bootstrap.sh | bash
```

Downloads the repo as a tarball (no git needed), installs mpv + ddcutil, creates Python venv, configures mpv for DRM output, sets up systemd service. Works with any username/hostname.
