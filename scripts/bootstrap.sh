#!/usr/bin/env bash
# One-command cinegatto setup. Can be piped from curl:
#   curl -sSL https://raw.githubusercontent.com/sleeepyjack/cinegatto/main/scripts/bootstrap.sh | bash
set -euo pipefail

REPO_URL="https://github.com/sleeepyjack/cinegatto/archive/refs/heads/main.tar.gz"
REPO_DIR="$HOME/cinegatto"

echo ""
echo "=== cinegatto bootstrap ==="
echo ""

# Download and extract (no git needed)
echo "Downloading cinegatto..."
mkdir -p "$REPO_DIR"
curl -sSL "$REPO_URL" | tar xz --strip-components=1 -C "$REPO_DIR"

# Run provisioning
cd "$REPO_DIR"
bash scripts/provision.sh
