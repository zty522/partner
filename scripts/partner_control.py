#!/usr/bin/env python3
"""Pause, resume, and inspect Partner instances with persistent state."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.monitoring.run_control import load_control, set_paused
from partner.governance.scheduler import assert_start_allowed, load_scheduler, set_active_slots

ROOT = "/mnt/e/work/partner_workspace"
ALL = ["01", "02", "03", "04", "05"]


SUPERVISOR = "partner-instance-native.service"


def _supervisor(action: str) -> None:
    subprocess.run(["systemctl", "--user", action, SUPERVISOR], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("pause", "resume", "restart", "switch", "status"))
    parser.add_argument("instances", nargs="*", default=[])
    args = parser.parse_args()
    ids = ALL if not args.instances or "all" in args.instances else args.instances
    invalid = sorted(set(ids) - set(ALL))
    if invalid:
        parser.error(f"unknown instances: {', '.join(invalid)}")
    if args.action == "pause":
        state = set_paused(ROOT, ids, True)
        scheduler = load_scheduler(ROOT)
        remaining = [value for value in scheduler.get("active_slots", []) if value not in ids]
        set_active_slots(ROOT, remaining, reason=f"paused: {','.join(ids)}")
        if not remaining:
            _supervisor("stop")
    elif args.action == "resume":
        state = set_paused(ROOT, ids, False)
        scheduler = load_scheduler(ROOT)
        selected = list(dict.fromkeys([*(scheduler.get("active_slots") or []), *ids]))
        set_active_slots(ROOT, selected, reason=f"resumed: {','.join(ids)}")
        for value in ids:
            assert_start_allowed(ROOT, value)
        _supervisor("start")
    elif args.action == "restart":
        scheduler = load_scheduler(ROOT)
        assigned = set(scheduler.get("active_slots") or [])
        outside = sorted(set(ids) - assigned)
        if outside:
            parser.error(f"restart only accepts current active slots: {', '.join(sorted(assigned)) or 'none'}")
        _supervisor("restart")
        state = load_control(ROOT)
    elif args.action == "switch":
        if not args.instances or "all" in args.instances:
            parser.error("switch requires one or two explicit instance IDs")
        previous = set(load_scheduler(ROOT).get("active_slots", []))
        set_paused(ROOT, [value for value in ALL if value not in ids], True)
        set_paused(ROOT, ids, False)
        set_active_slots(ROOT, ids, reason="manual slot switch")
        # The supervisor owns every child process; never start the retired
        # partner-XX services, which would create duplicate continuation owners.
        _supervisor("restart")
        state = load_control(ROOT)
    else:
        state = load_control(ROOT)
    result = subprocess.run(["systemctl", "--user", "is-active", SUPERVISOR], capture_output=True, text=True)
    active = {"supervisor": result.stdout.strip() or "unknown"}
    for value in ALL:
        pid_path = os.path.join(ROOT, "instances", value, "instance.pid")
        try:
            pid = int(open(pid_path, encoding="utf-8").read().strip())
            os.kill(pid, 0)
            active[value] = f"child_pid:{pid}"
        except (OSError, ValueError):
            active[value] = "inactive"
    print(json.dumps({"control": state, "scheduler": load_scheduler(ROOT), "services": active}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
