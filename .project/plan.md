# Cinegatto Implementation Plan (v3 — post-review)

## Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Python 3.11+ | yt-dlp native API, mpv IPC, ships with Pi OS Bookworm |
| Package mgmt | pip + venv | Pi OS Bookworm enforces PEP 668. Lightest option. No conda (ARM bloat). |
| Video player | mpv (`vo=drm` on Pi, default on Mac) | Headless KMS/DRM output, hw decode, cross-platform |
| mpv control | Custom thin IPC wrapper (~100 lines) | python-mpv-jsonipc has 65 stars, marginal maintenance. mpv JSON IPC protocol is simple enough to wrap directly. |
| YouTube | yt-dlp Python API (playlist metadata) + mpv ytdl_hook (stream resolution) | Avoids stale stream URLs, separates concerns |
| Web framework | **Flask** | Everything is synchronous (mpv IPC, yt-dlp, subprocess). No async complexity. |
| Test framework | pytest | Fixtures, mocking, parametrize |
| Frontend | Vanilla HTML/CSS/JS | Single file, no build step. This is a cat TV remote. |
| Logging | stdlib `logging` + `python-json-logger` | Structured JSON logs, zero learning curve |
| Pi OS | Lite 64-bit Bookworm | No desktop, minimal footprint |
| Display power | vcgencmd display_power 0/1 | Pi 5 standard |

## Project Structure

Flat layout (no `src/` — single-deployment app, not a PyPI library).

```
cinegatto/                    # repo root
├── pyproject.toml            # metadata, tool config (pytest etc)
├── requirements.txt          # pinned deps (==, true lock file)
├── requirements-dev.txt      # dev/test deps (pinned)
├── config/
│   └── default.json
├── cinegatto/                # main package
│   ├── __init__.py
│   ├── __main__.py           # entry: python -m cinegatto
│   ├── app.py                # bootstrap, wiring, graceful shutdown
│   ├── config.py             # config loading + validation
│   ├── log.py                # structured logging setup
│   ├── controller.py         # PlaybackController — serializes all player commands
│   ├── player/
│   │   ├── __init__.py
│   │   ├── mpv_ipc.py        # thin mpv JSON IPC wrapper (our own)
│   │   ├── mpv_player.py     # player logic: process lifecycle, watchdog
│   │   └── types.py          # Player protocol
│   ├── playlist/
│   │   ├── __init__.py
│   │   ├── fetcher.py        # yt-dlp playlist metadata
│   │   └── selector.py       # random video selection + history (deque)
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py         # Flask blueprint, REST endpoints
│   ├── web/
│   │   └── static/
│   │       └── index.html    # single-file mobile UI
│   └── display/
│       ├── __init__.py
│       ├── types.py           # Display protocol
│       ├── pi.py              # vcgencmd implementation
│       └── noop.py            # macOS/fallback no-op
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_log.py
│   ├── test_player.py
│   ├── test_controller.py
│   ├── test_playlist.py
│   ├── test_api.py
│   ├── test_display.py
│   └── test_integration.py   # end-to-end smoke test
└── scripts/
    ├── provision.sh
    └── cinegatto.service
```

### Changes from v2 (post-review)
- Added `controller.py` — PlaybackController serializes all player commands via worker thread + queue (fixes concurrency gap)
- Added `test_controller.py`
- Watchdog explicitly added to player phase
- Dependencies pinned with `==` (true lock file)
- Systemd unit hardened (PYTHONUNBUFFERED, WorkingDirectory, StartLimit)
- Log ring buffer bounded (500 entries, filterable by level)
- Provisioning verifies binaries after install
- Offline boot → standby + poll (no disk cache — can't stream without network anyway)

## Implementation Phases (TDD)

Each phase: write tests -> red -> implement -> green -> refactor.

### Phase 0: Project Scaffolding + Logging
- [ ] Create pyproject.toml (metadata + pytest config)
- [ ] Create requirements.txt (pinned `==`) and requirements-dev.txt
- [ ] Set up package with __init__.py files
- [ ] `log.py`: structured JSON logging via stdlib + python-json-logger
  - Ring buffer handler (max 500 entries) for /api/logs endpoint
  - Support `level` filter
- [ ] `test_log.py`: verify JSON output, log levels, context fields, ring buffer bounds
- [ ] Verify `pytest` runs green

Logging goes first because every subsequent module needs it.

### Phase 1: Config Module
**Tests first.**

- [ ] `test_config.py`: load default, override with user config, validate required fields, missing file → defaults, invalid JSON → clear error
- [ ] `config.py`: load JSON config, merge defaults, validate
- [ ] `config/default.json`:
  ```json
  {
    "playlist_url": "",
    "api_port": 8080,
    "log_level": "debug",
    "audio": false,
    "mpv_extra_args": [],
    "watchdog_timeout_sec": 10,
    "log_ring_size": 500
  }
  ```

### Phase 2: Player Module + Watchdog (hardest — do it early to de-risk)
**Tests first.** Most uncertain module. Building before playlist avoids rework.

- [ ] `test_player.py`:
  - Player protocol/interface definition
  - mpv_ipc: send command, receive response (mock the socket)
  - load_video sends `loadfile` command
  - play/pause toggle via `set_property`
  - seek to position
  - get_state returns playing/paused, position, duration, current video
  - Observe `file-loaded` event before seeking (critical for random start)
  - Observe `idle-active` / `eof-reached` for video-ends detection
  - Process lifecycle: start, detect crash (BrokenPipeError), cleanup stale socket, restart
  - Graceful shutdown (SIGTERM → kill mpv child)
  - **Watchdog**: heartbeat ping (`get_property pause`) every N seconds, timeout → kill + restart
- [ ] `player/types.py`: Player protocol
- [ ] `player/mpv_ipc.py`: thin wrapper — open Unix socket, send JSON commands, read responses, observe properties
- [ ] `player/mpv_player.py`: spawn mpv, manage lifecycle, watchdog thread, implement Player protocol

**Key details:**
- mpv spawned with `--input-ipc-server=/tmp/cinegatto-mpv.sock --idle=yes`
- `--idle=yes` keeps mpv running between videos
- Random seek happens AFTER `file-loaded` event
- Duration comes from mpv (`get_property duration`), NOT from yt-dlp
- On crash: unlink stale socket file, respawn with backoff
- Watchdog: dedicated thread, configurable timeout (`watchdog_timeout_sec`), logs warning before kill

**Test strategy:** Unit tests mock the socket layer. Integration test (manual) spawns real mpv with `--no-video --no-audio`.

### Phase 3: PlaybackController
**Tests first.** This is the concurrency boundary — all commands go through here.

- [ ] `test_controller.py`:
  - Commands are serialized (no concurrent player access)
  - play/pause/next/previous dispatch correctly
  - next triggers: selector.pick() → player.load_video() → wait file-loaded → seek random
  - previous triggers: selector.previous() → player.load_video() → wait file-loaded → seek random
  - pause triggers: player.pause() → display.power_off()
  - play triggers: display.power_on() → wait → player.play()
  - Command during load (e.g., next while loading) → queued, not dropped
  - Video-ends callback triggers next
  - Status query returns current state without blocking command queue
- [ ] `controller.py`: single worker thread + queue, owns player + selector + display coordination

### Phase 4: Playlist Module
**Tests first.**

- [ ] `test_playlist.py`:
  - Fetcher returns list of entries with id, title, url
  - Fetcher handles network failure → retry with backoff
  - Fetcher handles empty playlist → clear error
  - Selector picks random video
  - Selector tracks history (deque, max ~50)
  - Selector.previous() returns last played video
  - Selector.previous() when no history → None
  - Thread safety: yt-dlp calls protected by lock
- [ ] `fetcher.py`: yt-dlp `extract_flat`. Import once at module level. Threading lock.
- [ ] `selector.py`: random pick + deque history

**Offline behavior:** If fetch fails on boot, enter standby mode (display off) and retry every 60s. No disk cache — can't stream videos without network anyway.

### Phase 5: Display Module
**Tests first.**

- [ ] `test_display.py`: Display protocol, PiDisplay mocks subprocess, NoopDisplay no-ops
- [ ] `types.py`, `pi.py`, `noop.py`
- [ ] Platform detection: auto-select PiDisplay on Linux/ARM, NoopDisplay elsewhere

### Phase 6: REST API + Integration Test
**Tests first.**

- [ ] `test_api.py` (using Flask test_client):
  - POST /api/play → submits play command to controller
  - POST /api/pause → submits pause command to controller
  - POST /api/next → submits next command to controller
  - POST /api/previous → submits previous command to controller
  - GET /api/status → returns current video title, state, position (non-blocking read)
  - GET /api/logs → returns recent log entries from ring buffer
    - Query param `level` filters by min level
    - Query param `limit` caps entries (default 100, max 500)
- [ ] `test_integration.py`: end-to-end smoke test — HTTP request → controller → mocked player, verify full path
- [ ] `routes.py`: Flask blueprint, all handlers submit to controller (never call player directly)
- [ ] Static file serving for web UI

### Phase 7: App Orchestration
- [ ] `app.py`: wire everything together
  - Load config
  - Init logging (with ring buffer)
  - Init display (platform auto-detect)
  - Fetch playlist with retry loop
  - If no network: display off, retry every 60s until playlist fetched
  - Start mpv player (idle mode)
  - Create PlaybackController (player + selector + display)
  - Start Flask API server (threaded)
  - Auto-play first random video via controller
  - **Graceful shutdown**: SIGTERM/SIGINT → controller.stop() → player.shutdown() → exit 0
  - Background thread: periodic playlist re-fetch (every 30 min)
- [ ] `__main__.py`: entry point, call app.run()

### Phase 8: Web UI
- [ ] Single `index.html` with inline CSS + vanilla JS
- [ ] Mobile-optimized (viewport meta, large touch targets, dark theme)
- [ ] Buttons: play/pause, next, previous
- [ ] Status: current video title, play state, position
- [ ] Log viewer (polls GET /api/logs)
- [ ] Uses relative URLs (`fetch('/api/status')`) — served by same Flask app

### Phase 9: Pi Provisioning & Hardware Testing
- [ ] `provision.sh` (idempotent):
  - `sudo apt install -y mpv` — only sudo call; mpv is a native binary
  - Everything else userspace:
    - python3-venv, git, avahi-daemon already ship with Pi OS Lite
    - yt-dlp installed via pip in venv
  - Clone/update repo to ~/cinegatto
  - Create venv, pip install deps
  - Configure mpv for Pi (`vo=drm`, `hwdec=drm-copy`, `fullscreen=yes`)
  - Add user to `video` and `render` groups (one-time sudo)
  - Disable WiFi power save
  - Configure mDNS hostname (`cinegatto.local`)
  - Configure logging to tmpfs (`/run/log/cinegatto/`)
  - **Verify binaries**: check mpv, python3, yt-dlp are available, fail with actionable error if not
  - Install + enable systemd service (sudo for systemctl)
- [ ] `cinegatto.service`:
  ```ini
  [Unit]
  Description=Cinegatto - Cinema for Cats
  After=network-online.target
  Wants=network-online.target

  [Service]
  Type=simple
  User=pi
  WorkingDirectory=/home/pi/cinegatto
  Environment=PYTHONUNBUFFERED=1
  ExecStart=/home/pi/cinegatto/venv/bin/python -m cinegatto
  Restart=always
  RestartSec=5
  StartLimitBurst=5
  StartLimitIntervalSec=60
  TTYPath=/dev/tty1
  StandardInput=tty
  StandardOutput=journal
  StandardError=journal

  [Install]
  WantedBy=multi-user.target
  ```
- [ ] Test on real Pi 5 hardware
- [ ] Verify: boot → auto-play, web UI reachable at cinegatto.local, pause → monitor off
- [ ] Manual soak test: 2 hours of next/pause/resume cycles

## Key Technical Details

### Concurrency model
```
Flask threads (multiple) → PlaybackController command queue (single worker thread)
                        → Player (single owner: the controller worker)
                        → mpv process (single, via IPC socket)

Status reads: controller exposes thread-safe read-only snapshot, no queue needed.
Playlist re-fetch: background thread with lock, updates selector's video list atomically.
```

All player mutations go through the controller's command queue. API handlers never touch the player directly. This eliminates race conditions (double next, pause during load, etc.).

### Video playback flow
```
1. Fetch playlist metadata (yt-dlp extract_flat) — retries on failure
2. Controller: selector.pick() → random video, added to history
3. Controller: player.load_video(youtube_url)
4. Player: mpv loadfile → wait for file-loaded event
5. Player: get_property duration → seek to random.uniform(0, duration * 0.8)
6. Player: observe eof-reached → callback to controller → go to step 2
7. On error → log, skip to step 2
```

### Monitor standby sequence
```
Pause:  controller → player.pause() → wait 500ms → display.power_off()
Resume: controller → display.power_on() → wait 2s (HDMI handshake) → player.play()
```

### Graceful shutdown
```
SIGTERM → app.shutdown()
  → controller.stop() (drains queue, signals worker to exit)
  → player.shutdown() (pause → quit IPC → wait 5s → SIGKILL if needed → unlink socket)
  → exit 0
```

### Watchdog
```
Dedicated thread in MpvPlayer:
  Every watchdog_timeout_sec/2: send get_property ping
  If no response within watchdog_timeout_sec: log warning → kill mpv → restart
  Backoff on repeated failures: 1s, 2s, 4s, 8s, max 30s
```

### Offline boot behavior
```
Boot → attempt playlist fetch → fails (no network)
  → display.power_off() (standby)
  → retry every 60s
  → on success → display.power_on() → auto-play
```
No disk cache. Can't stream without network — caching the playlist index without caching videos is pointless.

### Audio
Off by default (`--no-audio`). Config flag `audio: true` to enable. Avoids ALSA issues on headless Pi.

### Platform capability matrix
| Feature | Pi (Linux/ARM) | macOS |
|---------|---------------|-------|
| Video output | `vo=drm` (KMS/DRM) | default (macOS native) |
| HW decode | `hwdec=drm-copy` | `hwdec=auto` |
| Display power | vcgencmd display_power | no-op (logged) |
| Audio | ALSA (if enabled) | CoreAudio (if enabled) |
| mpv install | `sudo apt install mpv` | `brew install mpv` |

### Dependencies

**requirements.txt** (pinned):
```
flask==3.1.1
yt-dlp==2025.3.21
python-json-logger==3.3.0
```

**requirements-dev.txt** (pinned):
```
pytest==8.3.5
```

3 runtime deps, 1 dev dep. mpv is a system package (apt/brew).

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| mpv DRM on Pi 5 quirks | No video output | Test in Phase 9 early; fallback to `vo=gpu` |
| mpv process crash | Playback stops | Detect via BrokenPipeError, cleanup socket, restart with backoff |
| mpv hangs (alive but unresponsive) | Stuck playback | Watchdog thread: ping every 5s, kill + restart on timeout |
| Race conditions (concurrent commands) | Corrupted state | PlaybackController serializes all commands via queue |
| yt-dlp breaks (YouTube changes) | Can't fetch playlist | Pin version; keep last successful fetch in memory |
| Network not ready on boot | No playlist | Standby + retry every 60s until network available |
| SD card wear from logging | Card dies | Log to tmpfs, rotate aggressively |
| WiFi power save | Web UI unreachable | Disable in provision.sh |
| VT access from systemd | mpv can't render | TTYPath=/dev/tty1 + user in video/render groups |
| Stale mpv socket after crash | Can't reconnect | Unlink socket before spawning new mpv |
| HDMI handshake delay on resume | Black screen flash | 2s delay before unpausing after display_power 1 |
| extract_flat no duration | Can't pre-compute seek | Get duration from mpv post-load |
| Service crash loop | systemd gives up | StartLimitBurst=5/60s, then manual intervention |
