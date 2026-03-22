# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**cinegatto** is a cinema for cats — a Raspberry Pi 5 application that plays wildlife videos fullscreen from a curated YouTube playlist. Cats watch it; humans control it remotely via a mobile-optimized web UI.

Target hardware: Raspberry Pi 5 + 1080p HDMI monitor, headless (no keyboard/mouse/desktop environment).

## Core Design Constraints

- **Mac-testable**: Core functionality must run on macOS for local development. Pi-specific code (display management, systemd, GPIO) should be isolated behind interfaces so the rest can run and be tested on macOS.
- **Headless Pi**: No desktop environment. The app owns the display directly (e.g., via `mpv` or similar framebuffer/kms player).
- **Single-purpose**: The Pi runs only cinegatto. No other services. Provisioning should be automated from bare metal.
- **LAN-only API**: No authentication needed. Keep the API simple.

## Intended Architecture

```
cinegatto/
├── config/          # JSON config (playlist URL, resolution, etc.)
├── player/          # Video playback abstraction (isolate Pi-specific calls here)
├── api/             # REST API server (play/pause/next/prev/shuffle/logs)
├── web/             # Mobile-optimized frontend
├── playlist/        # YouTube playlist fetching and random selection
├── display/         # Monitor power management (standby on pause) — Pi-specific
├── logging/         # Structured logging at debug/trace level
└── scripts/         # Bootstrap/provisioning scripts for Pi setup
```

The player module should have a platform interface so macOS uses a compatible backend (e.g., `mpv` via CLI) and Pi uses the same or a Pi-optimized variant.

## Key Features to Implement

1. **Autoplay on boot** — systemd service starts cinegatto and begins playing a random video
2. **Random video + random start position** — select random video from playlist, seek to random timestamp
3. **Infinite loop** — playlist loops continuously
4. **Web UI + REST API** — play/pause, next, previous, shuffle, log viewer
5. **Monitor standby on pause** — use `xrandr` / `vcgencmd` / DPMS to cut display power
6. **Config file** — JSON config for playlist URL, resolution, behavior flags
7. **Rigorous logging** — debug/trace level logs for every meaningful event (used for agentic debugging)

## Logging Philosophy

Log generously at `debug` and `trace` levels. These logs are used for agentic debugging, so include context: what action was taken, what state was before/after, any relevant IDs or paths. Structured logging (JSON) preferred.

## Provisioning

The setup script should take a fresh Pi OS install from zero to running cinegatto as a boot service with a single command. Document any manual steps (WiFi credentials, etc.) that can't be automated.
