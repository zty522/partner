#!/bin/bash
# Inject one message to all 5 partner instances (reverse direction: you → bot).
#
# This uses scripts/partner_local_message.py which writes to each instance's
# desktop_inbox.jsonl, so the message bypasses QQ push limitations and goes
# directly through the local inbox poller.  Each instance processes it as a
# real USER_MESSAGE event and replies via its normal QQ bridge (subject to
# the bot-user push relationship state).
#
# Usage:
#   bash scripts/test/inject_all_instances.sh "hello from hermes"
#   bash scripts/test/inject_all_instances.sh --text "ping" --timeout 60

set -euo pipefail

WORKSPACE_ROOT="${PARTNER_WORKSPACE:-/mnt/e/work/partner_workspace}"
TEXT=""
TIMEOUT=900
INJECT_BIN="/mnt/e/work/partner/scripts/partner_local_message.py"

usage() {
  cat <<'EOF'
inject_all_instances.sh — inject one USER_MESSAGE to all 5 instances

  --text       message text (required unless $TEXT is set)
  --timeout    seconds to wait for task completion per instance (default 900)
  --workspace  workspace root (default $PARTNER_WORKSPACE)
  -h | --help  show this help

Examples:
  bash scripts/test/inject_all_instances.sh --text "ping"
  bash scripts/test/inject_all_instances.sh --text "what's your last_heartbeat?" --timeout 60
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --text) TEXT="$2"; shift 2;;
    --timeout) TIMEOUT="$2"; shift 2;;
    --workspace) WORKSPACE_ROOT="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [ -z "$TEXT" ]; then
  echo "ERROR: --text is required" >&2
  usage
  exit 2
fi
if [ ! -f "$INJECT_BIN" ]; then
  echo "ERROR: $INJECT_BIN not found" >&2
  exit 1
fi

PARTNER_BIN="/home/os/miniconda3/bin/python"
declare -A RESULTS

echo "=== injecting text to all 5 instances ==="
echo "text: $TEXT"
echo "workspace: $WORKSPACE_ROOT"
echo ""

for inst in 01 02 03 04 05; do
  echo "--- $inst ---"
  if "$PARTNER_BIN" "$INJECT_BIN" "$inst" \
      --workspace-root "$WORKSPACE_ROOT" \
      --text "$TEXT" \
      --timeout "$TIMEOUT"; then
    RESULTS[$inst]="done"
  else
    RESULTS[$inst]="failed-or-timeout"
  fi
  echo ""
done

echo "=== summary ==="
for inst in 01 02 03 04 05; do
  printf '  %s: %s\n' "$inst" "${RESULTS[$inst]}"
done