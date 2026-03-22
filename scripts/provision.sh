#!/usr/bin/env bash
# cinegatto provisioning script for Raspberry Pi OS Lite (Bookworm 64-bit)
# Run this on the Pi after flashing the OS and enabling SSH.
# Usage: bash provision.sh
set -euo pipefail

REPO_DIR="$HOME/cinegatto"
VENV_DIR="$REPO_DIR/venv"
SERVICE_NAME="cinegatto"

echo "=== cinegatto provisioning ==="

# --- Step 1: Install mpv (only sudo call for packages) ---
echo "Installing mpv..."
sudo apt update -qq
sudo apt install -y -qq mpv

# --- Step 2: Verify required binaries ---
echo "Verifying binaries..."
for cmd in python3 mpv git; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: $cmd not found. Please install it manually."
        exit 1
    fi
done
echo "  python3: $(python3 --version)"
echo "  mpv: $(mpv --version | head -1)"
echo "  git: $(git --version)"

# --- Step 3: Clone or update repo ---
if [ -d "$REPO_DIR/.git" ]; then
    echo "Updating existing repo..."
    cd "$REPO_DIR" && git pull
else
    echo "Cloning repo..."
    # TODO: Replace with actual repo URL once published
    echo "ERROR: Please clone the cinegatto repo to $REPO_DIR first."
    echo "  git clone <repo-url> $REPO_DIR"
    exit 1
fi

# --- Step 4: Create venv and install deps ---
echo "Setting up Python venv..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt" -q
echo "  Dependencies installed."

# --- Step 5: Configure mpv for Pi 5 headless ---
echo "Configuring mpv for DRM output..."
mkdir -p "$HOME/.config/mpv"
cat > "$HOME/.config/mpv/mpv.conf" << 'MPVCONF'
vo=drm
hwdec=drm-copy
fullscreen=yes
MPVCONF

# --- Step 6: User groups for DRM/video access ---
echo "Adding user to video and render groups..."
sudo usermod -aG video,render "$USER" 2>/dev/null || true

# --- Step 7: Disable WiFi power save ---
echo "Disabling WiFi power save..."
sudo iw wlan0 set power_save off 2>/dev/null || echo "  (WiFi power save: skipped, may not apply)"

# --- Step 8: Configure tmpfs for logs ---
echo "Setting up tmpfs log directory..."
sudo mkdir -p /run/log/cinegatto
sudo chown "$USER:$USER" /run/log/cinegatto

# --- Step 9: Install systemd service ---
echo "Installing systemd service..."
sudo cp "$REPO_DIR/scripts/cinegatto.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "=== Provisioning complete ==="
echo ""
echo "To start cinegatto now:"
echo "  sudo systemctl start cinegatto"
echo ""
echo "To check status:"
echo "  sudo systemctl status cinegatto"
echo "  journalctl -u cinegatto -f"
echo ""
echo "Web UI will be available at:"
echo "  http://$(hostname).local:8080"
echo ""
echo "NOTE: You may need to log out and back in for group changes to take effect."
