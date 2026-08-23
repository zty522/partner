#!/usr/bin/env python3
"""Deterministic fake-clock soak test for the Campaign Controller."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.governance.campaign import (
    campaign_instruction, campaign_snapshot, create_campaign, seed_default_work,
    tick_campaign,
)
from partner.governance.campaign_models import CampaignBudget, WorkItem


def _complete_fake_task(root: Path, item: WorkItem, instruction: str, serial: int) -> None:
    task_dir = root / "instances" / item.instance_id / "state" / "tasks" / f"fake-{serial:04d}"
    task_dir.mkdir(parents=True, exist_ok=True)
    artifact = task_dir / f"evidence_{serial:04d}.md"
    artifact.write_text(f"unique campaign evidence {serial}\n" + "verified\n" * 80, encoding="utf-8")
    (task_dir / "task_instance.json").write_text(json.dumps({
        "task_id": f"fake-{serial:04d}",
        "user_message": instruction,
        "metadata": {"step_results": {"send": {"ok": True, "delivered": True}}},
    }), encoding="utf-8")
    rows = [
        {"event": "plan_executor_step_completed", "event_type": "read_file", "ok": True},
        {"event": "plan_executor_step_completed", "event_type": "send_user_text", "ok": True},
        {"event": "completion_status_updated", "status": "done", "llm_calls": 1},
        {"event": "iteration_llm_check", "satisfied": True, "missing": []},
    ]
    (task_dir / "task_log.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def run_simulation(cycles: int = 120) -> dict:
    with tempfile.TemporaryDirectory(prefix="partner_campaign_soak_") as temp:
        root = Path(temp) / "workspace"
        for instance in ("01", "02", "03", "04", "05"):
            (root / "instances" / instance / "state" / "tasks").mkdir(parents=True)
        start = datetime.now(timezone.utc).astimezone()
        campaign = create_campaign(
            str(root), goal="deterministic soak", allowed_instances=["01", "02", "03", "04", "05"],
            duration_seconds=max(60, cycles * 60), max_active=2, report_interval_seconds=300,
            budget=CampaignBudget(
                max_work_items=max(20, cycles * 2), max_failures=5, max_retries_per_item=2,
                max_runtime_seconds=max(60, cycles * 60), max_model_calls=cycles * 4,
                max_cost_units=cycles * 4,
            ),
        )
        seed_default_work(str(root), campaign.campaign_id)
        dispatched_ids: set[str] = set()
        switches: list[list[str]] = []
        serial = 0

        def dispatch(item: WorkItem, instruction: str) -> str:
            nonlocal serial
            serial += 1
            task_id = f"fake-{serial:04d}"
            if task_id in dispatched_ids:
                raise AssertionError("duplicate task id")
            dispatched_ids.add(task_id)
            _complete_fake_task(root, item, instruction, serial)
            return task_id

        result = {}
        for index in range(cycles + 5):
            now = start + timedelta(seconds=index * 60)
            result = tick_campaign(
                str(root), campaign.campaign_id, dispatch=dispatch,
                switch_slots=lambda ids: switches.append(list(ids)), now=now,
                lease_seconds=120,
            )
            if result.get("status") == "completed":
                break
        snapshot = campaign_snapshot(str(root), campaign.campaign_id)
        assert all(len(values) <= 2 for values in switches)
        assert len(dispatched_ids) == serial
        assert snapshot["campaign"]["usage"]["work_items_completed"] > 0
        assert snapshot["campaign"]["status"] == "completed"
        reports = list((root / "state" / "campaigns" / campaign.campaign_id / "reports").glob("*.md"))
        assert reports
        return {
            "ok": True,
            "cycles_requested": cycles,
            "ticks": index + 1,
            "tasks_dispatched": serial,
            "max_slots_observed": max((len(value) for value in switches), default=0),
            "completed_work_items": snapshot["campaign"]["usage"]["work_items_completed"],
            "failures": snapshot["campaign"]["usage"]["failures"],
            "final_status": snapshot["campaign"]["status"],
            "reports": len(reports),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=120)
    args = parser.parse_args()
    print(json.dumps(run_simulation(max(1, args.cycles)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
