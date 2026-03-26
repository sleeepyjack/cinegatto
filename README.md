# cinegatto

A cinema for cats. Plays wildlife videos from a YouTube playlist on a Raspberry Pi 5 connected to a monitor. Cats watch it; humans control it remotely via a mobile web UI.

```
  /\_/\
 ( o.o )
  > ^ <
```

## Features

- **Autoplay on boot** — starts playing a random video from your YouTube playlist
- **Random shuffle + random start position** — every viewing is different
- **Web UI** — mobile-optimized remote control (play/pause, next, previous, random seek)
- **Video caching** — downloads videos in the background for instant playback and offline support
- **Monitor standby** — turns off the display on pause via DDC/CI
- **QR code overlay** — scan the QR code on the video to open the web UI on your phone
- **Network resilient** — falls back to cached videos when WiFi drops

## Hardware

- Raspberry Pi 5 (tested with 8GB)
- 1080p HDMI monitor
- WiFi or Ethernet
- SD card (32GB+, or USB storage for larger cache)

## Quick Setup

### 1. Flash SD card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to flash **Pi OS Lite (64-bit, Bookworm)**. In the settings (gear icon):

- Hostname: your choice (e.g., `cinegatto`)
- Username: your choice (e.g., `cinegatto`)
- Password: your choice
- WiFi: enter SSID + password
- SSH: enable

### 2. Deploy

SSH into the Pi and run:

```bash
ssh <username>@<hostname>.local
curl -sSL "https://raw.githubusercontent.com/sleeepyjack/cinegatto/main/scripts/bootstrap.sh" | bash
```

By default this installs to `~/cinegatto`. Override with `CINEGATTO_DIR`:

```bash
CINEGATTO_DIR=/opt/cinegatto curl -sSL "..." | bash
```

This downloads cinegatto, installs dependencies (mpv, ddcutil, Python venv), configures the system, and sets up the systemd service.

### 3. Configure

Edit the config file to set your playlist:

```bash
nano ~/cinegatto/cinegatto.json
```

Set your YouTube playlist URL:

```json
{
  "playlist_url": "https://youtube.com/playlist?list=YOUR_PLAYLIST_ID"
}
```

### 4. Start

```bash
sudo systemctl start cinegatto
```

Open the web UI: **http://&lt;hostname&gt;.local:8080**

cinegatto starts automatically on boot.

## Web UI

The web UI is mobile-optimized. Access it from your phone by scanning the QR code shown on the video, or navigate to `http://cinegatto.local:8080`.

**Controls:**
- ◀◀ Previous / ▶ Play/Pause / ▶▶ Next / 🎲 Random seek
- 🔀 Shuffle — toggle random vs sequential order
- 🎲 Seek — toggle random start position for new videos
- 🔄 Sync — refresh playlist from YouTube + cache new videos

**Logs** — expandable log viewer at the bottom for debugging.

## Configuration

All settings live in `cinegatto.json`. Only override what you need — defaults are sensible.

| Key | Default | Description |
|-----|---------|-------------|
| `playlist_url` | *(required)* | YouTube playlist URL |
| `api_port` | `8080` | Web UI port |
| `audio` | `false` | Enable audio output |
| `shuffle` | `true` | Random video order |
| `random_start` | `true` | Seek to random position on load |
| `cache_enabled` | `true` | Download videos for offline playback |
| `cache_disk_usage_pct` | `80` | Max % of disk space for cache |
| `cache_format` | `bestvideo[height<=1080]...` | yt-dlp format for downloads |
| `playlist_refresh_sec` | `1800` | Playlist refresh interval (seconds) |
| `log_level` | `info` | Logging level (debug for development) |

## Local Development (macOS)

```bash
# Install mpv
brew install mpv

# Set up venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Run
./dev.sh

# Tests
pytest
```

## Architecture

```
cinegatto/
├── app.py              # Bootstrap, wiring, startup sequence
├── controller.py       # Command queue (play/pause/next/seek)
├── config.py           # JSON config loading + validation
├── log.py              # Structured JSON logging + ring buffer
├── cache/
│   └── service.py      # Video caching + background downloads
├── player/
│   ├── mpv_ipc.py      # mpv JSON IPC with dedicated reader thread
│   ├── mpv_player.py   # Process lifecycle, watchdog, event handling
│   ├── qr_overlay.py   # QR code + ASCII art overlays
│   └── types.py        # Player protocol
├── playlist/
│   ├── fetcher.py      # yt-dlp playlist metadata
│   └── selector.py     # Shuffle/sequential with history
├── display/
│   ├── pi.py           # DDC/CI monitor power (Pi 5)
│   └── noop.py         # No-op for macOS
├── api/
│   └── routes.py       # Flask REST API
└── web/
    └── static/
        └── index.html  # Single-file mobile web UI
```

## License

MIT

## Links

- [GitHub](https://github.com/sleeepyjack/cinegatto)
