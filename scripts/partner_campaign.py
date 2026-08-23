#!/usr/bin/env python3
"""Create, run, inspect, pause or stop a recoverable Partner campaign."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.governance.campaign import (
    campaign_snapshot, cancel_campaign, create_campaign, seed_default_work,
    tick_campaign,
)
from partner.governance.campaign_models import CampaignBudget
from partner.governance.campaign_runtime import dispatch_to_instance, switch_runtime_slots
from partner.governance.campaign_storage import (
    active_campaign_id, campaign_dir, load_campaign, save_campaign,
)
from partner.governance.models import now_iso


DEFAULT_ROOT = "/mnt/e/work/partner_workspace"
_running = True


def _duration(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*([smhd]?)\s*", value.lower())
    if not match:
        raise argparse.ArgumentTypeError("duration must look like 30m, 8h or 1d")
    amount = int(match.group(1))
    factor = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    return amount * factor


def _ids(value: str) -> list[str]:
    result = [part.strip() for part in value.split(",") if part.strip()]
    if not result or set(result) - {"01", "02", "03", "04", "05"}:
        raise argparse.ArgumentTypeError("instances must be comma-separated values from 01..05")
    return result


def _signal_handler(_signum, _frame):
    global _running
    _running = False


def _run(root: str, campaign_id: str, interval: int, once: bool = False) -> int:
    lock_path = campaign_dir(root, campaign_id) / "runner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"ok": False, "status": "runner_already_active", "campaign_id": campaign_id}))
            return 2
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
        while _running:
            result = tick_campaign(
                root,
                campaign_id,
                dispatch=lambda item, text: dispatch_to_instance(root, item, text),
                switch_slots=lambda ids: switch_runtime_slots(root, ids),
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if once or result.get("status") in {"completed", "cancelled", "missing_campaign"}:
                break
            time.sleep(max(2, interval))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--goal", required=True)
    start.add_argument("--duration", type=_duration, default=8 * 3600)
    start.add_argument("--instances", type=_ids, default=["01", "02", "03", "04", "05"])
    start.add_argument("--max-active", type=int, default=2)
    start.add_argument("--report-interval", type=_duration, default=3600)
    start.add_argument("--max-work-items", type=int, default=100)
    start.add_argument("--max-failures", type=int, default=12)
    start.add_argument("--max-retries", type=int, default=2)
    start.add_argument("--max-model-calls", type=int, default=500)
    start.add_argument("--max-cost-units", type=float, default=100.0)
    start.add_argument("--no-seed", action="store_true")
    start.add_argument("--detach", action="store_true")
    start.add_argument("--interval", type=int, default=30)

    run = sub.add_parser("run")
    run.add_argument("--campaign-id", required=True)
    run.add_argument("--interval", type=int, default=30)
    run.add_argument("--once", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("--campaign-id", default="")

    pause = sub.add_parser("pause")
    pause.add_argument("--campaign-id", default="")
    pause.add_argument("--reason", default="operator pause")

    resume = sub.add_parser("resume")
    resume.add_argument("--campaign-id", default="")

    stop = sub.add_parser("stop")
    stop.add_argument("--campaign-id", default="")
    stop.add_argument("--reason", default="operator stop")
    args = parser.parse_args()
    root = args.workspace

    if args.command == "start":
        budget = CampaignBudget(
            max_work_items=args.max_work_items,
            max_failures=args.max_failures,
            max_retries_per_item=args.max_retries,
            max_runtime_seconds=args.duration,
            max_model_calls=args.max_model_calls,
            max_cost_units=args.max_cost_units,
        )
        state = create_campaign(
            root, goal=args.goal, allowed_instances=args.instances,
            duration_seconds=args.duration, max_active=args.max_active,
            report_interval_seconds=args.report_interval, budget=budget,
        )
        if not args.no_seed:
            seed_default_work(root, state.campaign_id)
        output = {"ok": True, "campaign_id": state.campaign_id, "status": state.status}
        if args.detach:
            unit = f"partner-campaign-{state.campaign_id.replace('_', '-')[:48]}"
            command = [
                "systemd-run", "--user", f"--unit={unit}", "--collect",
                sys.executable, str(Path(__file__).resolve()), "--workspace", root,
                "run", "--campaign-id", state.campaign_id, "--interval", str(args.interval),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                cancel_campaign(root, state.campaign_id, f"runner launch failed: {result.stderr.strip()}")
                raise SystemExit(result.stderr.strip() or "systemd-run failed")
            output["runner_unit"] = unit
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    campaign_id = args.campaign_id or active_campaign_id(root)
    if not campaign_id:
        raise SystemExit("no campaign id and no active campaign")
    if args.command == "run":
        return _run(root, campaign_id, args.interval, args.once)
    if args.command == "status":
        print(json.dumps(campaign_snapshot(root, campaign_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "stop":
        stopped = cancel_campaign(root, campaign_id, args.reason)
        if stopped.restore_instances:
            switch_runtime_slots(root, stopped.restore_instances)
        print(json.dumps(stopped.to_dict(), ensure_ascii=False, indent=2))
        return 0
    state = load_campaign(root, campaign_id)
    if not state:
        raise SystemExit("campaign not found")
    if args.command == "pause":
        state.status = "paused"
        state.stop_reason = args.reason
    else:
        if state.status not in {"paused", "blocked"}:
            raise SystemExit(f"campaign cannot resume from {state.status}")
        state.status = "running"
        state.stop_reason = ""
    state.updated_at = now_iso()
    save_campaign(root, state)
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
