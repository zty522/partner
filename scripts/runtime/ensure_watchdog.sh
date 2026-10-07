#!/usr/bin/env bash
# Ensure the partner native runtime watchdog is running.
# Idempotent: if the watchdog is already up, this does nothing.
# Intended to be triggered on VS Code folder open via .vscode/tasks.json.
set -u

WORKSPACE="${PARTNER_WORKSPACE:-/mnt/e/work/partner_workspace}"
REPO="/mnt/e/work/partner"
PY="/home/os/miniconda3/bin/python"
LOG="/tmp/partner_watchdog.log"

if pgrep -f "run_instance_native_runtime.py.*${WORKSPACE}" >/dev/null 2>&1; then
    echo "watchdog already running (${WORKSPACE})"
    exit 0
fi

mkdir -p "$WORKSPACE"
cd "$REPO" || exit 1

# Fully detach so the process survives the VS Code task session.
setsid "$PY" scripts/runtime/run_instance_native_runtime.py \
    --workspace "$WORKSPACE" --watchdog-seconds 30 \
    >>"$LOG" 2>&1 < /dev/null &

echo "watchdog started (pid=$!) -> $LOG"
