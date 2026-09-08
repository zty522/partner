#!/usr/bin/env python3
"""Spawn and supervise the five production instance subprocesses.

Architecture change (ADR 0064, 2026-09-05): the runtime daemon no longer
plays the role of an external business-progress watcher. Each instance is
now a real subprocess that drives its own inbox / event loop / QQ bridge
(via ``python -m partner --instance-id 0X --workspace <root>/instances/0X``).
This daemon only:

  1. spawns the subprocesses,
  2. supervises them — detects exits and respawns with rate-limit backoff,
  3. forwards SIGINT/SIGTERM to children and waits for graceful shutdown,
  4. keeps the slot arbiter (``reconcile_slots``) so external pause / resume
     still works without restart.

The previous ``TaskTerminalReceiver`` + watchdog loop is kept in helper
form (``recover_or_start``, ``handle_terminal``, ``reconcile_slots``)
because the arbiter and tests still reference them, but main() no longer
calls them in the hot path. Per-instance business progress now lives
inside each subprocess.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from partner.governance.instance_native import (
    enabled_instances, load_native_runtime_config,
    PROJECTS,
)
from partner.governance.scheduler import (
    effective_max_active, load_scheduler, resource_capacity_snapshot,
    set_active_slots,
)

# Helper functions kept for arbiter + tests — main() no longer calls them
# in the hot path (ADR 0064), but external callers (CLI / tests) still do.
from partner.governance.instance_native import (
    authoritative_terminal_event, handle_terminal, load_state,
    recover_or_start,  # noqa: F401  (re-exported for back-compat)
)
from partner.governance.completion_signal import TaskTerminalReceiver  # noqa: F401


# ── ADR 0064 spawn tunables ──────────────────────────────────────────
DEFAULT_RESPAWN_BACKOFF_SEC = 5
DEFAULT_MAX_RESPAWNS_PER_MIN = 6  # rate-limit respawn to avoid crash-loop
SHUTDOWN_GRACE_SEC = 10


def _unseen_inbox_exists(root: Path, instance_id: str) -> bool:
    """Return whether queued user work must run before any auto-resume."""
    state_dir = root / "instances" / instance_id / "state"
    try:
        seen_value = json.loads((state_dir / "desktop_inbox_seen_ids.json").read_text(
            encoding="utf-8"))
        seen = {str(value) for value in seen_value} if isinstance(seen_value, list) else set()
    except (OSError, TypeError, ValueError):
        seen = set()
    try:
        lines = (state_dir / "desktop_inbox.jsonl").read_text(encoding="utf-8").splitlines()
        for line in lines:
            row = json.loads(line)
            message_id = str(row.get("message_id") or row.get("id") or "")
            if message_id and message_id not in seen:
                return True
    except (OSError, TypeError, ValueError):
        pass
    return False


def _instance_workspace(root: Path, instance_id: str) -> Path:
    return root / "instances" / instance_id


def _spawn_instance(
    root: Path, instance_id: str, python_exe: str,
) -> subprocess.Popen:
    """Spawn a single instance subprocess and write its PID file.

    Returns the Popen handle. The subprocess is responsible for its own
    inbox polling, event loop, and QQ bridge — runtime daemon does not
    interact with it beyond lifecycle supervision.
    """
    inst_dir = _instance_workspace(root, instance_id)
    inst_dir.mkdir(parents=True, exist_ok=True)
    pid_path = inst_dir / "instance.pid"
    log_path = inst_dir / "instance.out.log"

    # Detached so the subprocess outlives our stdout buffering / shell quirks.
    # New session so the child has its own pgid; we send signals to the
    # whole group on shutdown.
    proc = subprocess.Popen(
        [python_exe, "-m", "partner",
         "--instance-id", instance_id,
         "--workspace", str(inst_dir)],
        cwd=str(root),
        stdout=open(log_path, "ab", buffering=0),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        pid_path.write_text(str(proc.pid), encoding="utf-8")
    except OSError as exc:
        print(f"runtime: failed to write pid file for {instance_id}: {exc}",
              flush=True)
    print(json.dumps({
        "event": "instance_spawned",
        "instance_id": instance_id,
        "pid": proc.pid,
        "log": str(log_path),
    }, ensure_ascii=False), flush=True)
    return proc


def _is_alive(proc: subprocess.Popen) -> bool:
    if proc.poll() is None:
        return True
    return False


def _reconcile_slots(root: Path, configured: list[str], max_active: int) -> list[str]:
    """Keep busy instances and rotate only instances that explicitly yield.

    This helper is preserved from the previous arbiter-only runtime; it
    mutates ``instance_scheduler.json`` and writes ``native_state.json``
    but does NOT spawn processes. Spawning is now ``_spawn_instance``'s
    job, called separately for each ``selected`` instance.
    """
    current = [value for value in load_scheduler(str(root)).get("active_slots") or []
               if value in configured]
    keep = [value for value in current
            if load_state(root, value).phase not in {"YIELD_SLOT", "BLOCKED", "WAITING"}]
    start = (configured.index(current[-1]) + 1) % len(configured) if current else 0
    ordered = configured[start:] + configured[:start]
    candidates = [value for value in ordered if value not in keep
                  and load_state(root, value).phase != "BLOCKED"]
    # Resource pressure stops new admission, never an in-flight action.  If
    # capacity shrank below the number of busy children, drain naturally and
    # reconcile again after one reaches a yield/terminal state.
    if len(keep) > max_active:
        return current
    selected = (keep + candidates)[:max_active]
    if selected != current:
        # This daemon owns the child processes.  Calling campaign_runtime's
        # systemd switcher here starts a second copy of an instance and makes
        # the scheduler ledger diverge from the processes we supervise.
        set_active_slots(str(root), selected, reason="instance-native completion-signal rotation")
        for instance_id in selected:
            state = load_state(root, instance_id)
            if state.phase in {"YIELD_SLOT", "WAITING", "WAITING_SLOT"}:
                state.phase = "WAITING"
                state.reason = "resource slot granted"
                # A new slot quantum is a fresh bounded attempt. Budgets from
                # the previous visit must not block the project before it can
                # apply newly persisted evidence.
                state.consecutive_failures = 0
                state.learning_interruptions = 0
                from partner.governance.instance_native import save_state
                save_state(root, state)
    return selected


def _shutdown_children(children: dict[str, subprocess.Popen]) -> None:
    """Forward SIGTERM, wait SHUTDOWN_GRACE_SEC, then SIGKILL stragglers."""
    for instance_id, proc in children.items():
        if not _is_alive(proc):
            continue
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            print(json.dumps({
                "event": "instance_terminating",
                "instance_id": instance_id,
                "pid": proc.pid,
            }, ensure_ascii=False), flush=True)
        except ProcessLookupError:
            pass
    deadline = time.time() + SHUTDOWN_GRACE_SEC
    while time.time() < deadline:
        if all(not _is_alive(p) for p in children.values()):
            break
        time.sleep(0.5)
    for instance_id, proc in children.items():
        if not _is_alive(proc):
            continue
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=1)
            print(json.dumps({
                "event": "instance_killed",
                "instance_id": instance_id,
                "pid": proc.pid,
            }, ensure_ascii=False), flush=True)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass


def _stop_child(instance_id: str, proc: subprocess.Popen) -> None:
    """Stop one process after its instance yielded its durable slot."""
    if not _is_alive(proc):
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=SHUTDOWN_GRACE_SEC)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if _is_alive(proc):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument(
        "--watchdog-seconds", type=int, default=10,
        help="seconds between supervision sweeps (process aliveness + slot reconcile)",
    )
    parser.add_argument(
        "--respawn-backoff-sec", type=int, default=DEFAULT_RESPAWN_BACKOFF_SEC,
    )
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    stopping = False

    def stop(*_a):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    configured = enabled_instances(root)
    if not configured:
        print(json.dumps({"status": "native_disabled"}), flush=True)
        return 2
    max_active = int(load_native_runtime_config(root)["max_active"])
    selected = _reconcile_slots(root, configured, max_active)
    # Bug #57 P0 + Bug #58 P0.3 (ADR 0065 / 0066): unconditionally
    # scaffold + auto-resume for every active instance, on every
    # reconcile.  Originally the scaffold/auto_resume block was guarded
    # by ``if selected != current`` so it only ran when the slot set
    # changed — but that meant a freshly-restarted runtime with the
    # same slot set never ran it, leaving placeholder briefs and
    # "waiting for user" projects stranded indefinitely.
    for instance_id in selected:
        try:
            from partner.governance.project_scaffold import scaffold_project
            project_id, goal = PROJECTS.get(instance_id, ("", ""))
            if project_id:
                scaffold_summary = scaffold_project(
                    root, instance_id, project_id, goal)
                if scaffold_summary.get("replaced_placeholder_brief"):
                    print(
                        f"scaffold replaced placeholder brief for "
                        f"{instance_id}/{project_id}",
                        flush=True)
        except Exception as exc:
            import sys as _sys
            print(f"scaffold skipped for {instance_id}: {exc}",
                  file=_sys.stderr, flush=True)
        explicit_pending = _unseen_inbox_exists(root, instance_id)
        try:
            seeded = ({"ok": True, "status": "explicit_inbox_precedes_auto_seed"}
                      if explicit_pending else recover_or_start(root, instance_id))
            print(json.dumps({"event": "instance_native_seed",
                              "instance_id": instance_id, "result": seeded},
                             ensure_ascii=False), flush=True)
        except Exception as exc:
            print(f"native seed failed for {instance_id}: {exc}",
                  file=sys.stderr, flush=True)
        try:
            from partner.governance.auto_resume import (
                auto_resume_waiting_project,
            )
            project_id, _goal = PROJECTS.get(instance_id, ("", ""))
            if project_id and not explicit_pending:
                resume_summary = auto_resume_waiting_project(
                    root, project_id, instance_id)
                if resume_summary.get("resumed"):
                    print(
                        f"auto_resume: {instance_id}/{project_id} "
                        f"-> receipt={resume_summary['receipt_id']}",
                        flush=True)
        except Exception as exc:
            import sys as _sys
            print(f"auto_resume skipped for {instance_id}: {exc}",
                  file=_sys.stderr, flush=True)
    python_exe = sys.executable

    children: dict[str, subprocess.Popen] = {}
    respawn_history: dict[str, list[float]] = {i: [] for i in selected}

    # Initial spawn — synchronous so startup errors surface immediately.
    for instance_id in selected:
        children[instance_id] = _spawn_instance(root, instance_id, python_exe)

    print(json.dumps({
        "event": "supervision_started",
        "supervised": list(children.keys()),
        "watchdog_seconds": args.watchdog_seconds,
    }, ensure_ascii=False), flush=True)

    # Supervision loop. Each tick:
    #   1. detect dead children → respawn with rate-limit backoff
    #   2. refresh slot arbiter in case pause / resume / block changed
    try:
        while not stopping:
            time.sleep(max(1, args.watchdog_seconds))
            now = time.time()
            # Re-evaluate host pressure every sweep.  The configuration value
            # is only a ceiling; memory/load can shrink or expand live slots.
            max_active = effective_max_active(str(root))
            capacity = resource_capacity_snapshot(str(root))
            active_now = set(_reconcile_slots(root, configured, max_active))
            print(json.dumps({"event": "resource_capacity_checked", **capacity},
                             ensure_ascii=False), flush=True)
            for instance_id in list(children.keys()):
                proc = children[instance_id]
                if instance_id not in active_now:
                    _stop_child(instance_id, proc)
                    children.pop(instance_id, None)
                    continue
                if _is_alive(proc):
                    continue
                # Process exited. Capture exit, decide whether to respawn.
                exit_code = proc.returncode
                history = respawn_history[instance_id]
                history.append(now)
                # Trim history to last 60 seconds for rate limit.
                history[:] = [t for t in history if now - t < 60]
                if len(history) > DEFAULT_MAX_RESPAWNS_PER_MIN:
                    print(json.dumps({
                        "event": "instance_respawn_rate_limited",
                        "instance_id": instance_id,
                        "respawns_last_60s": len(history),
                        "last_exit_code": exit_code,
                    }, ensure_ascii=False), flush=True)
                    continue
                # Backoff before respawn to avoid tight crash loops.
                time.sleep(min(args.respawn_backoff_sec, 30))
                if stopping:
                    break
                children[instance_id] = _spawn_instance(
                    root, instance_id, python_exe)
                print(json.dumps({
                    "event": "instance_respawned",
                    "instance_id": instance_id,
                    "prev_exit_code": exit_code,
                }, ensure_ascii=False), flush=True)

            # Use the single authoritative snapshot computed at the start of
            # this sweep.  A second reconciliation here used to observe phase
            # changes caused while stopping children, choose a different pair,
            # and spawn it before the first pair was reaped on the next sweep.
            # That produced a real ~watchdog-length three-process overlap.
            try:
                # Spawn any newly-active instances that are not yet children.
                for instance_id in active_now:
                    if instance_id not in children:
                        if not _unseen_inbox_exists(root, instance_id):
                            recover_or_start(root, instance_id)
                        children[instance_id] = _spawn_instance(
                            root, instance_id, python_exe)
                        respawn_history.setdefault(instance_id, [])
            except Exception as exc:
                print(json.dumps({
                    "event": "reconcile_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }, ensure_ascii=False), flush=True)
    finally:
        print(json.dumps({"event": "supervision_stopping"}), flush=True)
        _shutdown_children(children)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
