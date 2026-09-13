#!/usr/bin/env python3
"""Supervise five channel hosts (pure QQ transport) + N shared workers.

ADR 0100 / migration_plan_0100 Phase 6 Step 6.

Each instance is a pure QQ transport — it receives messages and calls
PartnerApplicationService.submit() which does LLM sync + writes a
JobRecord to the global queue.

Shared workers (independent processes) poll the global queue and run the
EventFlowRunner flows.  Worker count is decoupled from instance count and
read from config/runtime/shared_worker_count (default 3).
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

from partner.governance.scheduler import ALL_INSTANCES


def _enabled(root: Path) -> list[str]:
    try:
        value = json.loads((root / "config/partner_config.json").read_text(encoding="utf-8"))
        configured = ((value.get("runtime") or {}).get("instance_native_enabled_instances") or [])
        rows = [str(x) for x in configured if str(x) in ALL_INSTANCES]
        return rows or list(ALL_INSTANCES)
    except (OSError, TypeError, ValueError):
        return list(ALL_INSTANCES)


def _worker_count(root: Path) -> int:
    """Number of shared worker processes.  Decoupled from instance count.

    Reads config/runtime/shared_worker_count.  Falls back to 3.
    Phase 7 will wire this to ResourceScheduler dynamic scaling.
    """
    try:
        value = json.loads((root / "config/partner_config.json").read_text(encoding="utf-8"))
        n = int((value.get("runtime") or {}).get("shared_worker_count") or 3)
        from partner.governance.scheduler import effective_max_active
        return min(max(1, n), len(_enabled(root)), effective_max_active(str(root)))
    except (OSError, TypeError, ValueError):
        from partner.governance.scheduler import effective_max_active
        return min(3, len(_enabled(root)), effective_max_active(str(root)))


def _spawn_instance(root: Path, instance_id: str) -> subprocess.Popen:
    workspace = root / "instances" / instance_id
    workspace.mkdir(parents=True, exist_ok=True)
    output = (workspace / "instance.out.log").open("ab", buffering=0)
    return subprocess.Popen(
        [sys.executable, "-m", "partner", "--instance-id", instance_id,
         "--workspace", str(workspace)],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
    )


def _spawn_worker(root: Path, slot_index: int) -> subprocess.Popen:
    """Spawn one shared worker process against the GLOBAL workspace."""
    log_dir = root / "state" / "application" / "worker_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    output = (log_dir / f"worker_{slot_index}.out.log").open("ab", buffering=0)
    return subprocess.Popen(
        [sys.executable, "-m", "partner.runtime.shared_worker",
         "--workspace", str(root), "--slot-index", str(slot_index)],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--watchdog-seconds", type=float, default=5)
    parser.add_argument("--instances", nargs="+", choices=ALL_INSTANCES, help="Explicit channel hosts for bounded acceptance")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    enabled = args.instances or _enabled(root)
    n_workers = _worker_count(root)

    stopping = False

    def stop(*_args):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    # ADR 0100: instances are pure transports; workers are separate processes.
    instances = {iid: _spawn_instance(root, iid) for iid in enabled}
    workers = {i: _spawn_worker(root, i) for i in range(n_workers)}
    retiring = set()

    print(json.dumps({
        "event": "runtime_topology",
        "channel_hosts": enabled,
        "shared_workers": n_workers,
    }, ensure_ascii=False), flush=True)

    try:
        while not stopping:
            # Watchdog: respawn any dead instance.
            for iid, process in list(instances.items()):
                if process.poll() is not None and not stopping:
                    time.sleep(1)
                    instances[iid] = _spawn_instance(root, iid)
            desired = _worker_count(root)
            # Scale down at a persisted Event boundary, not by killing a task.
            for slot, process in list(workers.items()):
                if slot >= desired and slot not in retiring and process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    retiring.add(slot)
            for slot, process in list(workers.items()):
                if process.poll() is not None and not stopping:
                    del workers[slot]
                    retiring.discard(slot)
            for slot in range(desired):
                if slot not in workers and not stopping:
                    workers[slot] = _spawn_worker(root, slot)
            time.sleep(max(1, args.watchdog_seconds))
    finally:
        for process in list(instances.values()) + list(workers.values()):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.time() + 10
        while time.time() < deadline and any(
            p.poll() is None for p in list(instances.values()) + list(workers.values())
        ):
            time.sleep(0.2)
        for process in list(instances.values()) + list(workers.values()):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
