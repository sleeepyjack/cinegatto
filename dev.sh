#!/usr/bin/env bash
# One-click dev launcher for macOS.
# Starts cinegatto with a visible mpv video window and prints the web UI link.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure venv exists
if [ ! -f "venv/bin/python" ]; then
    echo "Creating venv..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt -r requirements-dev.txt -q
fi

# Kill any leftover mpv/cinegatto from a previous run
pkill -f "cinegatto-mpv.sock" 2>/dev/null || true
rm -f /tmp/cinegatto-mpv.sock

PORT=${PORT:-8080}
LAN_IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "<your-ip>")

echo ""
echo "  cinegatto - cinema for cats"
echo ""
echo "  Web UI:  http://localhost:${PORT}"
echo "  Phone:   http://${LAN_IP}:${PORT}"
echo ""
echo "  Press Ctrl+C to stop"
echo ""

exec ./venv/bin/python -m cinegatto "$@"
