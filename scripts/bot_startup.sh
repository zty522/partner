#!/bin/bash
# Partner startup script - runs as systemd service foreground
# The script stays running for systemd to track

set -euo pipefail 2>/dev/null || set -eu

WORKSPACE="$HOME/.partner"
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
exec /home/os/miniconda3/bin/python3 -c "
import sys, os, json
sys.path.insert(0, '$PARTNER_DIR')
sys.argv[0] = sys.argv[0].replace('\\\\', '/')

from partner.qq_official_bridge import QQQfficialBridge

cfg = os.path.join('$WORKSPACE', '00_config', 'partner_config.json')
with open(cfg) as f:
    config = json.load(f)
qq_cfg = os.path.join('$WORKSPACE', '00_config', config.get('messaging', {}).get('qq_config', 'qq_config.json'))

bridge = QQQfficialBridge('$WORKSPACE')
bridge.load_config_from_file(qq_cfg)
bridge.start()
"
