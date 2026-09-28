#!/usr/bin/env python3
"""Long-running Partner autonomy monitor.

Reports runtime health (supervisor / workers / bridge alive) plus native Job
counts, and appends an alert record when something looks wrong.  Designed to be
run periodically (cron) so an unattended long run stays observable.

Usage: monitor_native_runtime.py [workspace] [--max-native-jobs N]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _alive(pattern: str) -> int:
    try:
        out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=15).stdout
        return len([line for line in out.splitlines() if line.strip()])
    except (OSError, subprocess.SubprocessError):
        return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ws = Path(args[0] if args else "/mnt/e/work/partner_workspace")
    max_jobs = 400
    for i, a in enumerate(sys.argv):
        if a == "--max-native-jobs" and i + 1 < len(sys.argv):
            max_jobs = int(sys.argv[i + 1])

    supervisor = _alive("run_instance_native_runtime")
    workers = _alive("shared_worker")
    bridge = _alive("run_native_terminal_bridge")

    jobs_dir = ws / "state/application/jobs"
    n_native = 0
    statuses: dict[str, int] = {}
    rounds: dict[str, int] = {}
    for f in jobs_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not str(d.get("sender_id") or "").startswith("partner_"):
            continue
        n_native += 1
        st = str(d.get("status") or "?")
        statuses[st] = statuses.get(st, 0) + 1
        req = str(d.get("request") or "")
        if "轮" in req and "第" in req:
            key = str(d.get("assigned_instance") or "?")
            rounds[key] = rounds.get(key, 0) + 1

    alerts: list[str] = []
    if supervisor == 0:
        alerts.append("supervisor not running")
    if workers == 0:
        alerts.append("no shared workers")
    if bridge == 0:
        alerts.append("terminal bridge not running")
    if n_native >= max_jobs:
        alerts.append(f"native job count {n_native} >= {max_jobs}")

    report = {
        "supervisor": supervisor, "workers": workers, "bridge": bridge,
        "native_jobs": n_native, "statuses": statuses,
        "auto_iteration_jobs_by_instance": rounds, "alerts": alerts,
    }
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if alerts:
        log = ws / "state/application/native_runtime_alerts.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**report, "at": time.time()}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
