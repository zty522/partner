#!/usr/bin/env bash
set -euo pipefail

ROOT="/mnt/e/work/partner"
WORK_ROOT="/mnt/e/work/partner_workspace/instances"
PYTHON_BIN="/home/os/miniconda3/bin/python3"
INSTANCE_IDS=("01" "03" "04")
LAUNCH_LOG="/mnt/e/work/partner_workspace/start_three_partners.log"

log() {
  local ts
  ts="$(date '+%F %T')"
  echo "[$ts] $*" | tee -a "$LAUNCH_LOG"
}

start_instance() {
  local instance_id="$1"
  local workspace="$WORK_ROOT/$instance_id"
  local log_file="$workspace/10_logs/instance.log"
  local lock_file="$workspace/state/qq_bridge.lock"
  local pid_file="$workspace/instance.pid"

  mkdir -p "$workspace/10_logs" "$workspace/state"

  if pgrep -af "python3? -m partner --instance-id ${instance_id} --workspace ${workspace}" >/dev/null 2>&1; then
    log "[skip] Partner ${instance_id} already running"
    return 0
  fi

  rm -f "$lock_file" "$pid_file"
  export PARTNER_HOME="/mnt/e/work/partner_workspace"
  export PARTNER_PROJECT_INTERVAL_SEC="${PARTNER_PROJECT_INTERVAL_SEC:-1800}"

  log "[start] Partner ${instance_id} (project interval ${PARTNER_PROJECT_INTERVAL_SEC}s)"
  setsid "$PYTHON_BIN" -m partner \
    --instance-id "$instance_id" \
    --workspace "$workspace" \
    </dev/null >> "$log_file" 2>&1 &

  sleep 1
  if pgrep -af "python3? -m partner --instance-id ${instance_id} --workspace ${workspace}" >/dev/null 2>&1; then
    log "[ok] Partner ${instance_id} started"
  else
    log "[warn] Partner ${instance_id} did not stay alive after launch"
  fi
}

cd "$ROOT"
mkdir -p "$(dirname "$LAUNCH_LOG")"
log "===== start_three_partners begin ====="

for instance_id in "${INSTANCE_IDS[@]}"; do
  start_instance "$instance_id"
done

log "[done] requested startup for: ${INSTANCE_IDS[*]}"
