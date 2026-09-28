#!/bin/bash
# Compact status dashboard for all 5 partner instances.
# Reads state files directly (no QQ bridge dependency).
# Shows heartbeat, current task with step progress and failure reason,
# last event, last dialog message, push delivery count, and relay outbox
# backlog so you can verify self-drive business progress without depending
# on QQ-side message delivery.

set -euo pipefail

WORKSPACE_ROOT="${PARTNER_WORKSPACE:-/mnt/e/work/partner_workspace}"
MODE="table"
WATCH_SEC=0
PYBIN="/home/os/miniconda3/bin/python"

while [ $# -gt 0 ]; do
  case "$1" in
    --workspace) WORKSPACE_ROOT="$2"; shift 2;;
    --json) MODE="json"; shift;;
    --watch) WATCH_SEC="$2"; shift 2;;
    -h|--help) echo "Usage: --workspace PATH | --json | --watch N"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

run_once() {
  "$PYBIN" - "$WORKSPACE_ROOT" "$MODE" <<'PYEOF'
import json, os, sys
from pathlib import Path
from datetime import datetime, timezone

workspace_root = Path(sys.argv[1])
mode = sys.argv[2]
now = datetime.now(timezone.utc).astimezone()
now_ts = now.timestamp()

def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

def latest_jsonl(path):
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 32 * 1024))
            tail = f.read().decode("utf-8", errors="replace")
        last = None
        for line in tail.splitlines():
            line = line.strip()
            if line:
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    continue
        return last
    except OSError:
        return None

def age_seconds(ts_str):
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        return round(now_ts - ts, 1)
    except (TypeError, ValueError):
        return None

def count_pattern_in_tail(path, key, value):
    if not path.exists():
        return 0
    cnt = 0
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            tail = f.read().decode("utf-8", errors="replace")
        for line in tail.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get(key) == value:
                cnt += 1
    except OSError:
        return 0
    return cnt

def task_failure_reason(t):
    md = t.get("metadata") or {}
    gov = md.get("manual_iteration_governance") or {}
    if isinstance(gov, dict):
        err = str(gov.get("error") or gov.get("issue") or "")
        if err:
            return f"governance: {err[:200]}"
    errs = t.get("errors") or []
    if errs:
        return f"errors: {str(errs[-1])[:200]}"
    sr = md.get("step_results") or []
    failed = [s for s in sr if isinstance(s, dict) and not s.get("ok")]
    if failed:
        return f"step_failed: {str(failed[-1])[:200]}"
    return ""

results = []
for inst in ["01", "02", "03", "04", "05"]:
    inst_root = workspace_root / "instances" / inst
    state = inst_root / "state"

    heartbeat = read_json(state / "heartbeat.json") or {}
    hb_ts = heartbeat.get("last_heartbeat", "")
    hb_age = age_seconds(hb_ts)
    crash_count = heartbeat.get("crash_count", 0)
    cycle_count = heartbeat.get("cycle_count", 0)

    native = read_json(state / "native_state.json") or {}
    phase = native.get("phase", "?")
    reason = native.get("reason", "")

    # Bug #57 P5 (ADR 0065): derive a more truthful running status
    # from the latest_task rather than reading native_state.phase,
    # which the runtime daemon resets to WAIT_TASK on every recover_or_start
    # even while a task is in flight.
    # Bug #60 follow-up: latest_task is defined later in this loop
    # (after tasks_dir is read), so we must read it from a closure.
    # Instead of relying on forward-references, just initialise the
    # status to the native_phase and update after latest_task is built.
    effective_phase = phase

    tasks_dir = state / "tasks"
    latest_task = None
    if tasks_dir.exists():
        candidates = sorted(
            tasks_dir.glob("*/task_instance.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            t = read_json(candidates[0]) or {}
            md = t.get("metadata") or {}
            sr = md.get("step_results") or {}
            # Bug #60 follow-up: step_results may be a dict (newer
            # partner versions store step_results as ``{step_id:
            # step_dict}``) instead of a list.  Support both shapes.
            if isinstance(sr, dict):
                sr_items = list(sr.values())
            else:
                sr_items = list(sr)
            step_done = sum(1 for s in sr_items if isinstance(s, dict) and s.get("ok"))
            step_total = len(sr_items)
            latest_task = {
                "task_id": (t.get("task_id") or candidates[0].parent.name)[:12],
                "status": t.get("completion_status", "?"),
                "user_message": str(t.get("user_message") or "")[:80],
                "started_age_sec": age_seconds(str(t.get("created_at") or "")),
                "step_done": step_done,
                "step_total": step_total,
                "fail_reason": task_failure_reason(t),
                # Bug #58 P0.2 (ADR 0066): show real external actions vs
                # plain reads so the user can tell apart a real experiment
                # from a write-report-only task.  ``real_external`` = any
                # action whose prefix matches exec:/web.fetch:/pytest:/
                # atomic_write_file:/atomic_push_files:.
                "actions_executed": [
                    str(a) for a in (md.get("actions_executed") or [])
                ][:6],
                "real_external": [
                    str(a) for a in (md.get("actions_executed") or [])
                    if isinstance(a, str) and (
                        a.startswith("exec:")
                        or a.startswith("web.fetch:")
                        or a.startswith("pytest:")
                        or a.startswith("atomic_write_file:")
                        or a.startswith("atomic_push_files:")
                    )
                ][:4],
                # governance.ok=False means task_instance claims success
                # but the production_readiness gate rejected the artifact.
                # This is the contract for the "dialog says success but
                # receipt not on disk" bug.
                "governance_ok": bool(
                    ((t.get("metadata") or {}).get(
                        "manual_iteration_governance") or {}).get("ok")),
            }
            # Bug #58 P0.2 (ADR 0066): also count real receipts on disk
            # under share/projects/<project_id>/governance/receipts/.
            # If dialog claims success but disk has 0 receipts, that is
            # the truth gate mis-report surfaced visually.
            try:
                project_id = (
                    native.get("project_id")
                    or PROJECTS_DEFAULT.get(instance_id, ("",))[0]
                    if "PROJECTS_DEFAULT" in dir() else ""
                )
            except Exception:
                project_id = ""
            receipts_count = 0
            if project_id:
                receipts_dir = (Path("/mnt/e/work/partner_workspace") /
                                "share" / "projects" / project_id /
                                "governance" / "receipts")
                if receipts_dir.exists():
                    receipts_count = sum(
                        1 for _ in receipts_dir.glob("*.json"))
            latest_task["receipts_on_disk"] = receipts_count
            # Governance mismatch flag: status=done but gov.ok=False and
            # no receipt on disk — this is the 03/05 "phantom success"
            # pattern the user observed.  Computed OUTSIDE the
            # ``if project_id:`` block so it always lands in
            # latest_task; previously it was skipped whenever the
            # dashboard failed to derive a project_id and dashboard
            # phase was stuck on IDLE_AFTER_FAILED (Bug #60).
            gov_mismatch = (
                latest_task["status"] == "done"
                and not latest_task["governance_ok"]
            )
            latest_task["gov_mismatch"] = gov_mismatch


    last_event = latest_jsonl(state / "event_pipeline.jsonl") or {}
    last_dialog = latest_jsonl(state / "dialog_history.jsonl") or {}

    # Bug #60 follow-up: now that latest_task is fully built, derive
    # the truthful running status from it.  This MUST happen after
    # latest_task is defined, not before.  Also honour gov_mismatch
    # (Bug #58 P0.2 / ADR 0066): a task_instance that claims
    # ``completion_status=done`` but where governance.ok=False means
    # the production_readiness gate rejected the artifact — that's
    # really a failed task from the operator's perspective.
    if latest_task is not None:
        _lt_status = latest_task.get("status")
        _gov_mismatch = latest_task.get("gov_mismatch", False)
        if _lt_status == "pending":
            effective_phase = "RUNNING_TASK"
        elif _lt_status == "done" and not _gov_mismatch:
            effective_phase = "IDLE_AFTER_DONE"
        else:
            # status=done+gov_mismatch=True OR status=failed
            effective_phase = "IDLE_AFTER_FAILED"

    dialog_path = state / "dialog_history.jsonl"
    pushed = count_pattern_in_tail(dialog_path, "channel", "proactive")

    relay_path = inst_root / "share" / "relay_outbox.jsonl"
    relay_count = 0
    relay_latest = None
    relay_count_1h_ago = 0
    if relay_path.exists():
        try:
            # Read tail + count lines in last 1h by ts parsing.
            cutoff_1h = now_ts - 3600
            with relay_path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 256 * 1024))
                tail_bytes = f.read().decode("utf-8", errors="replace")
            for line in tail_bytes.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                relay_count += 1
                ts_str = str(rec.get("ts") or "")
                try:
                    ts = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")).timestamp()
                    if ts >= cutoff_1h:
                        relay_count_1h_ago += 1
                except (TypeError, ValueError):
                    pass
            relay_latest_row = latest_jsonl(relay_path)
            if relay_latest_row:
                relay_latest = {
                    "ts": relay_latest_row.get("ts", ""),
                    "kind": relay_latest_row.get("kind", ""),
                    "source": str(relay_latest_row.get("source") or "")[:40],
                }
        except OSError:
            pass
    relay_delta_1h = relay_count_1h_ago

    pause_ledger = None
    pause_path = state / "auto_drive_pause.json"
    if pause_path.exists():
        pl = read_json(pause_path)
        if pl:
            pause_ledger = {
                "status": pl.get("status", ""),
                "paused_at": pl.get("paused_at", ""),
                "observed_failure_count": pl.get("observed_failure_count", 0),
                "failure_threshold": pl.get("failure_threshold", 0),
                "window_minutes": pl.get("window_minutes", 0),
            }

    results.append({
        "instance": inst,
        "heartbeat": {"ts": hb_ts, "age_sec": hb_age, "cycles": cycle_count,
                       "crashes": crash_count},
        "phase": effective_phase,
        "native_phase": phase,
        "reason": reason[:60],
        "latest_task": latest_task,
        "last_event": {
            "type": last_event.get("type", ""),
            "ts": last_event.get("ts", ""),
            "seq": last_event.get("seq"),
        },
        "last_dialog": {
            "ts": last_dialog.get("timestamp", ""),
            "channel": last_dialog.get("channel", ""),
            "content": str(last_dialog.get("content") or "")[:60],
        },
        "dialog_proactive_count_tail": pushed,
        "relay_outbox": {
            "total_lines": relay_count,
            "delta_1h": relay_delta_1h,
            "latest": relay_latest,
        },
        "pause_ledger": pause_ledger,
        "totals": {
            "tasks_on_disk": len(candidates) if tasks_dir.exists() else 0,
            "tasks_done": sum(
                1 for c in (candidates if tasks_dir.exists() else [])
                if (read_json(c) or {}).get("completion_status") == "done"
            ),
            "learning_md_count": len(list((inst_root / "partner_data" / "learning").glob("*.md")))
                if (inst_root / "partner_data" / "learning").exists() else 0,
            "proactive_patches": sum(1 for _ in (state / "proactive_patches.jsonl").open())
                if (state / "proactive_patches.jsonl").exists() else 0,
        },
    })

if mode == "json":
    print(json.dumps({"as_of": now.isoformat(), "instances": results},
                     ensure_ascii=False, indent=2))
else:
    print(f"=== partner instance dashboard @ {now.strftime('%H:%M:%S')} ===")
    print(f"workspace: {workspace_root}")
    print()
    for r in results:
        inst = r["instance"]
        hb = r["heartbeat"]
        lt = r["latest_task"] or {}
        le = r["last_event"]
        ld = r["last_dialog"]
        ro = r["relay_outbox"]
        hb_age = hb["age_sec"]
        if hb_age is None:
            hb_alive = "DEAD/UNKNOWN"
        elif hb_age < 60:
            hb_alive = "ALIVE"
        elif hb_age < 600:
            hb_alive = "STALE"
        else:
            hb_alive = "DEAD"
        task_status = lt.get("status", "none")
        task_age = lt.get("started_age_sec", "?")
        task_msg = lt.get("user_message", "")[:50]
        step_done = lt.get("step_done", 0)
        step_total = lt.get("step_total", 0)
        fail_reason = lt.get("fail_reason", "")
        # Bug #60 follow-up: the table-mode print loop reads
        # ``effective_phase`` from the outer scope, which only carries
        # the value from the LAST for-loop iteration over the
        # instance list.  All five instances ended up showing the
        # last instance's phase.  Fix: read it from the per-instance
        # result dict.
        effective_phase = r["phase"]
        print(f"[{inst}] hb={hb_alive} hb_age={hb_age}s cycles={hb['cycles']} crashes={hb['crashes']}")
        print(f"     phase={effective_phase}  reason={r['reason']}")
        if lt:
            print(f"     task: status={task_status} started={task_age}s ago steps={step_done}/{step_total}")
            print(f"           msg: {task_msg}")
            if fail_reason:
                print(f"           fail: {fail_reason}")
        else:
            print(f"     task: (none on disk)")
        print(f"     last_event: {le['type']} ts={le['ts'][-13:] if le['ts'] else '-'} seq={le['seq']}")
        print(f"     last_dialog: ch={ld['channel']} ts={ld['ts'][-13:] if ld['ts'] else '-'} | {ld['content']}")
        print(f"     dialog_proactive(tail256K)={r['dialog_proactive_count_tail']}  relay_outbox_total={ro['total_lines']} delta_1h={ro.get('delta_1h',0)}")
        if ro.get("delta_1h", 0) > 100:
            print(f"     ⚠️  RELAY_BACKLOG: {ro['delta_1h']} rows added in last 1h")
        if ro["latest"]:
            print(f"     relay_latest: {ro['latest']['ts'][-13:]} kind={ro['latest']['kind']} source={ro['latest']['source']}")
        t = r["totals"]
        print(f"     totals: tasks={t['tasks_done']}/{t['tasks_on_disk']} learning_md={t['learning_md_count']} patches={t['proactive_patches']}")
        # Bug #58 P0.2: surface the "phantom success" pattern when
        # dialog says done but receipt not on disk + gov.ok=False.
        if lt.get("gov_mismatch"):
            print(f"     ⚠️  PHANTOM_SUCCESS: dialog=done gov.ok=False receipts_on_disk={lt.get('receipts_on_disk',0)}")
        if lt.get("real_external"):
            print(f"     real_external_actions: {', '.join(lt['real_external'][:3])}")
        elif lt.get("actions_executed"):
            print(f"     actions_executed: {', '.join(lt['actions_executed'][:3])} (no real external action!)")
        if r.get("pause_ledger"):
            pl = r["pause_ledger"]
            print(f"     PAUSE_LEDGER: status={pl['status']} failures={pl['observed_failure_count']}/{pl['failure_threshold']} window={pl['window_minutes']}min paused_at={pl['paused_at'][-13:] if pl['paused_at'] else '-'}")
        print()
PYEOF
}

if [ "$WATCH_SEC" -gt 0 ]; then
  while true; do
    clear 2>/dev/null || true
    run_once
    sleep "$WATCH_SEC"
  done
else
  run_once
fi
