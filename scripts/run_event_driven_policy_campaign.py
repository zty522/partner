#!/usr/bin/env python3
"""Run the authorised 04/05 learning Campaign on task-terminal signals."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from partner.governance.campaign import create_campaign, tick_campaign
from partner.governance.campaign_models import CampaignBudget
from partner.governance.campaign_runtime import (
    dispatch_to_instance,
    runtime_instance_ready,
)
from partner.governance.campaign_storage import load_campaign
from partner.governance.campaign_storage import campaign_dir
from partner.governance.completion_signal import TaskTerminalReceiver
from partner.governance.continuous_policy_campaign import (
    CANDIDATE_ID,
    enable_local_observation_fallback,
    seed_window,
    validate_sources,
)
from partner.governance.readiness_activation import review_and_maybe_activate
from partner.governance.storage import atomic_json


def _llm_experiments(root: Path) -> list[str]:
    governed = root / "share/mind/governance/experience_guided_policy/llm_experiments"
    return [str(path) for path in sorted(governed.glob("*.json"))]


def _review(root: Path, candidate_id: str) -> dict:
    return review_and_maybe_activate(
        root,
        candidate_id=candidate_id,
        llm_experiments=_llm_experiments(root),
        auto_activate_authorized=True,
    )


def _create(root: Path, candidate_id: str, duration: int, first_window_pairs: int) -> str:
    missing = validate_sources()
    if missing:
        raise RuntimeError(f"required frozen sources missing: {missing}")
    state = create_campaign(
        str(root),
        goal=("[continuous_policy_campaign=true] 04/05 双槽持续业务改善与长期经验策略学习；"
              "一个真实任务终态后立即派发下一项；MiniMax-M3 only；Event-first；"
              "手动 manual_stable 路径保持不变"),
        allowed_instances=["04", "05"],
        duration_seconds=duration,
        max_active=2,
        report_interval_seconds=3600,
        budget=CampaignBudget(
            max_work_items=96,
            max_failures=80,
            max_retries_per_item=0,
            max_runtime_seconds=duration,
            max_model_calls=240,
            max_cost_units=240.0,
        ),
    )
    created = seed_window(
        str(root), state.campaign_id,
        pairs_per_project=first_window_pairs,
        candidate_id=candidate_id,
    )
    atomic_json(campaign_dir(str(root), state.campaign_id) / "continuous_policy_windows.json", {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "windows": [{"date": datetime.now().astimezone().date().isoformat(),
                     "pairs_per_project": first_window_pairs,
                     "topic_offset": 0,
                     "work_items": len(created)}],
        "max_real_date_windows": 3,
    })
    print(json.dumps({"event": "continuous_campaign_created",
                      "campaign_id": state.campaign_id,
                      "work_items": len(created)}, ensure_ascii=False), flush=True)
    return state.campaign_id


def _ensure_real_date_window(root: Path, campaign_id: str, candidate_id: str) -> dict:
    """Add a small new matched window once per real date, never fake time."""
    path = campaign_dir(str(root), campaign_id) / "continuous_policy_windows.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        state = {"schema_version": 1, "candidate_id": candidate_id,
                 "windows": [], "max_real_date_windows": 3}
    windows = list(state.get("windows") or [])
    today = datetime.now().astimezone().date().isoformat()
    if any(str(row.get("date") or "") == today for row in windows):
        return {"status": "already_seeded", "date": today}
    if len(windows) >= int(state.get("max_real_date_windows") or 3):
        return {"status": "temporal_gate_complete", "windows": len(windows)}
    offset = 14 + max(0, len(windows) - 1) * 3
    created = seed_window(
        str(root), campaign_id, date_window=today,
        pairs_per_project=3, topic_offset=offset,
        candidate_id=candidate_id,
    )
    windows.append({"date": today, "pairs_per_project": 3,
                    "topic_offset": offset, "work_items": len(created)})
    state["windows"] = windows
    state["updated_at"] = datetime.now().astimezone().isoformat()
    atomic_json(path, state)
    return {"status": "real_date_window_seeded", "date": today,
            "work_items": len(created), "windows": len(windows)}


def run(root: Path, campaign_id: str, *, candidate_id: str,
        watchdog_seconds: int = 30) -> int:
    migrated = enable_local_observation_fallback(str(root), campaign_id)
    if migrated:
        print(json.dumps({"event": "local_observation_fallback_enabled",
                          "unstarted_work_items": migrated,
                          "delivery_claim": False}, ensure_ascii=False), flush=True)
    with TaskTerminalReceiver(root) as terminal_events:
        while True:
            temporal = _ensure_real_date_window(root, campaign_id, candidate_id)
            result = tick_campaign(
                str(root), campaign_id,
                dispatch=lambda item, text: dispatch_to_instance(str(root), item, text),
                switch_slots=None,
                # QQ readiness is still recorded by the task and readiness
                # gates, but cannot idle the explicitly authorised local experience-policy learning
                # experiment.  No delivered-business credit is granted.
                runtime_ready=lambda _iid: True,
            )
            readiness = _review(root, candidate_id)
            print(json.dumps({"campaign": result, "temporal": temporal,
                              "readiness": {
                                  "production_ready": readiness["production_ready"],
                                  "decision": readiness["decision"],
                                  "activated": readiness["activated"],
                              }}, ensure_ascii=False), flush=True)
            if result.get("status") in {"completed", "cancelled", "missing_campaign"}:
                return 0
            event = terminal_events.wait(max(2, watchdog_seconds))
            if event:
                print(json.dumps({"event": "terminal_signal_received",
                                  "task_id": event.get("task_id"),
                                  "instance_id": event.get("instance_id"),
                                  "status": event.get("status"),
                                  "next_dispatch": "immediate"}, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--candidate-id", default=CANDIDATE_ID)
    parser.add_argument("--campaign-id", default="")
    parser.add_argument("--duration", type=int, default=7 * 86400)
    parser.add_argument("--first-window-pairs", type=int, default=14)
    parser.add_argument("--watchdog-seconds", type=int, default=30)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    campaign_id = args.campaign_id
    if campaign_id:
        if not load_campaign(str(root), campaign_id):
            raise SystemExit(f"campaign not found: {campaign_id}")
    else:
        campaign_id = _create(root, args.candidate_id, args.duration, args.first_window_pairs)
    return run(root, campaign_id, candidate_id=args.candidate_id,
               watchdog_seconds=args.watchdog_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
