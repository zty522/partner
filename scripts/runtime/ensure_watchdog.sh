#!/usr/bin/env bash
# Ensure the partner native runtime watchdog is running.
# Idempotent: if the watchdog is already up, this does nothing.
# Intended to be triggered on VS Code folder open via .vscode/tasks.json.
set -u

WORKSPACE="${PARTNER_WORKSPACE:-/mnt/e/work/partner_workspace}"
REPO="/mnt/e/work/partner"
PY="/home/os/miniconda3/bin/python"
LOG="/tmp/partner_watchdog.log"

# Login-state browser channel for active learning on login-walled platforms
# (xiaohongshu/bilibili).  The profile is a dedicated Chrome user-data dir
# logged into xiaohongshu once; see docs/temp.md problem 35.
export PARTNER_ENABLE_BROWSER_LOGIN=1
export PARTNER_BROWSER_PROFILE="${PARTNER_BROWSER_PROFILE:-/mnt/c/Users/zty12/partner_profile}"
export PARTNER_BROWSER_SAVE_SCREENSHOT=1
export PARTNER_BROWSER_CAPTURE_DIR="${PARTNER_BROWSER_CAPTURE_DIR:-/mnt/e/work/partner_workspace/state/browser_captures}"

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
