#!/usr/bin/env bash
# One-command cinegatto setup. Can be piped from curl:
#   curl -sSL https://raw.githubusercontent.com/sleeepyjack/cinegatto/main/scripts/bootstrap.sh | bash
set -euo pipefail

REPO_URL="https://github.com/sleeepyjack/cinegatto.git"
REPO_DIR="$HOME/cinegatto"

echo ""
echo "=== cinegatto bootstrap ==="
echo ""

# Clone or update
if [ -d "$REPO_DIR/.git" ]; then
    echo "Updating existing repo..."
    cd "$REPO_DIR" && git pull
else
    echo "Cloning repo..."
    git clone "$REPO_URL" "$REPO_DIR"
fi

# Run provisioning
cd "$REPO_DIR"
bash scripts/provision.sh
