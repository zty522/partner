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


def _systemctl(action: str, instance_ids: list[str]) -> None:
    if instance_ids:
        subprocess.run(["systemctl", "--user", action, *[f"partner-{value}.service" for value in instance_ids]], check=True)


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
        scheduler = load_scheduler(ROOT)
        remaining = [value for value in scheduler.get("active_slots", []) if value not in ids]
        set_active_slots(ROOT, remaining, reason=f"paused: {','.join(ids)}")
        state = load_control(ROOT)
        _systemctl("stop", ids)
    elif args.action == "resume":
        for value in ids:
            assert_start_allowed(ROOT, value)
        state = set_paused(ROOT, ids, False)
        _systemctl("start", ids)
    elif args.action == "restart":
        scheduler = load_scheduler(ROOT)
        assigned = set(scheduler.get("active_slots") or [])
        outside = sorted(set(ids) - assigned)
        if outside:
            parser.error(f"restart only accepts current active slots: {', '.join(sorted(assigned)) or 'none'}")
        _systemctl("restart", ids)
        state = load_control(ROOT)
    elif args.action == "switch":
        if not args.instances or "all" in args.instances:
            parser.error("switch requires one or two explicit instance IDs")
        previous = set(load_scheduler(ROOT).get("active_slots", []))
        set_active_slots(ROOT, ids, reason="manual slot switch")
        _systemctl("stop", sorted(previous - set(ids)))
        # Restart selected slots as well: a no-op `start` leaves already active
        # instances on stale imported code after a manual-mode repair.
        _systemctl("restart", ids)
        state = load_control(ROOT)
    else:
        state = load_control(ROOT)
    active = {}
    for value in ALL:
        result = subprocess.run(["systemctl", "--user", "is-active", f"partner-{value}.service"], capture_output=True, text=True)
        active[value] = result.stdout.strip() or "unknown"
    print(json.dumps({"control": state, "scheduler": load_scheduler(ROOT), "services": active}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
