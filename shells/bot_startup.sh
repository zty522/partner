#!/bin/bash
# Partner startup script - runs as systemd service foreground
# The script stays running for systemd to track

set -euo pipefail 2>/dev/null || set -eu

WORKSPACE="${PARTNER_WORKSPACE:-/home/os/.partner/instances/default}"
INSTANCE_ID="${PARTNER_INSTANCE_ID:-default}"
PARTNER_DIR="/mnt/e/work/partner"
PID_FILE="$WORKSPACE/state/qq_bot.pid"

# Source conda environment (systemd doesn't load .bashrc)
if [ -f "/home/os/miniconda3/etc/profile.d/conda.sh" ]; then
    . "/home/os/miniconda3/etc/profile.d/conda.sh"
    conda activate base
fi

cd "$PARTNER_DIR"

# Wait for network
sleep 3

# Clean stale PID
rm -f "$PID_FILE" 2>/dev/null || true

# Run QQ Bot in foreground for systemd to track
exec /home/os/miniconda3/bin/python3 -m partner \
    --instance-id "$INSTANCE_ID" \
    --workspace "$WORKSPACE"
