#!/usr/bin/env bash
# cinegatto provisioning for Raspberry Pi OS Lite (Bookworm 64-bit)
# Run via bootstrap.sh or directly: cd ~/cinegatto && bash scripts/provision.sh
set -euo pipefail

REPO_DIR="$HOME/cinegatto"
VENV_DIR="$REPO_DIR/venv"
SERVICE_USER="$USER"
SERVICE_NAME="cinegatto"

echo "=== cinegatto provisioning ==="

# --- Verify repo exists ---
if [ ! -f "$REPO_DIR/requirements.txt" ]; then
    echo "ERROR: cinegatto not found at $REPO_DIR."
    echo "  Run bootstrap.sh first, or download manually."
    exit 1
fi

# --- Step 1: Install mpv (only apt call) ---
echo "Installing mpv..."
sudo apt update -qq
sudo apt install -y -qq mpv

# --- Step 2: Verify required binaries ---
echo "Verifying binaries..."
for cmd in python3 mpv; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: $cmd not found."
        exit 1
    fi
done
echo "  python3: $(python3 --version)"
echo "  mpv: $(mpv --version | head -1)"

# --- Step 3: Python venv + deps ---
echo "Setting up Python venv..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt" -q
echo "  Dependencies installed."

# --- Step 4: mpv config for Pi 5 headless (DRM output) ---
echo "Configuring mpv for DRM output..."
mkdir -p "$HOME/.config/mpv"
cat > "$HOME/.config/mpv/mpv.conf" << 'MPVCONF'
vo=drm
hwdec=drm-copy
fullscreen=yes
MPVCONF

# --- Step 5: User groups for DRM/video access ---
echo "Adding user to video and render groups..."
sudo usermod -aG video,render "$USER" 2>/dev/null || true

# --- Step 6: Disable WiFi power save (persistent) ---
echo "Disabling WiFi power save..."
sudo mkdir -p /etc/NetworkManager/conf.d
sudo tee /etc/NetworkManager/conf.d/wifi-powersave-off.conf > /dev/null << 'EOF'
[connection]
wifi.powersave = 2
EOF


# --- Step 8: Generate and install systemd service ---
echo "Installing systemd service..."
cat > /tmp/cinegatto.service << EOF
[Unit]
Description=Cinegatto - Cinema for Cats
After=network-online.target
Wants=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/python -m cinegatto
Restart=always
RestartSec=5
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/cinegatto.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "=== Provisioning complete ==="
echo ""
echo "  sudo systemctl start cinegatto   # start now"
echo "  sudo systemctl status cinegatto  # check status"
echo "  journalctl -u cinegatto -f       # follow logs"
echo ""
echo "  Web UI: http://$(hostname).local:8080"
echo ""
echo "NOTE: Log out and back in for group changes to take effect."
