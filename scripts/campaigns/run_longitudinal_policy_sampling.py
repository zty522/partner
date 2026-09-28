#!/usr/bin/env python3
"""Dispatch real, date-separated matched Partner tasks for longitudinal experience-policy learning.

The sampler never edits timestamps, rewards, trajectories, PromotionDecisions
or ``control_policy.promoted``.  It only writes ordinary USER_MESSAGE rows to
the two configured instance inboxes.  Normal Harness truth/delivery/reward
governance remains the sole authority for whether a dispatched arm qualifies.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from partner.governance.storage import atomic_json


DEFAULT_SOURCES = [
    "/mnt/e/work/partner_workspace/external/code/hermes-agent/agent/context_compressor.py",
    "/mnt/e/work/partner_workspace/external/literature/Just-In-Time Reinforcement Learning Continual Learning in LLM Agents Without Gradient Updates.pdf",
]
PROJECTS = [
    ("04", "literature_github_learning",
     "比较 WebRL 运行时反馈课程与 Hermes 上下文连续性机制，形成可核验的文献/GitHub项目建议"),
    ("05", "agent_self_evolution",
     "比较运行时反馈学习与上下文压缩机制，为 Partner 自进化提出带真实证据的安全采用边界"),
]


def _state_path(root: Path, candidate_id: str) -> Path:
    return (root / "share/mind/governance/experience_guided_policy/longitudinal_sampling"
            / f"{candidate_id}.json")


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _runtime_alive(workspace: Path) -> bool:
    try:
        owner = json.loads((workspace / "state/instance_runtime.lock").read_text(encoding="utf-8"))
        os.kill(int(owner.get("pid") or 0), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _pending_count(workspace: Path) -> int:
    count = 0
    for path in (workspace / "state/tasks").glob("*/task_instance.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if str(value.get("completion_status") or "pending") not in {"done", "failed"}:
            count += 1
    return count


def _inject(workspace: Path, instance_id: str, text: str) -> str:
    message_id = f"longpolicy_{instance_id}_{uuid.uuid4().hex[:12]}"
    row = {
        "id": message_id, "message_id": message_id,
        "text": text[:12000], "display_text": text[:12000],
        "source": "longitudinal_policy_sampler", "channel": "local",
        "sender_id": "longitudinal_policy_sampler", "sender_name": "Partner 经验策略采样器",
        "attachments": [], "created_at": datetime.now().astimezone().isoformat(),
    }
    inbox = workspace / "state/desktop_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return message_id


def dispatch_daily(root: Path, *, candidate_id: str,
                   research_project_id: str, pairs_per_project: int = 2) -> dict:
    path = _state_path(root, candidate_id)
    state = _load(path)
    today = datetime.now().astimezone().date().isoformat()
    windows = dict(state.get("windows") or {})
    if today in windows:
        return {"ok": True, "status": "date_window_already_dispatched",
                "window": windows[today], "path": str(path)}
    missing = [value for value in DEFAULT_SOURCES if not Path(value).is_file()]
    if missing:
        return {"ok": False, "status": "source_missing", "missing": missing}
    unavailable = []
    for iid, _, _ in PROJECTS:
        workspace = root / "instances" / iid
        if not _runtime_alive(workspace) or _pending_count(workspace) > 4:
            unavailable.append({"instance_id": iid, "alive": _runtime_alive(workspace),
                                "pending": _pending_count(workspace)})
    if unavailable:
        return {"ok": False, "status": "slots_not_ready", "instances": unavailable}
    experiment_id = f"experiment_research_longitudinal_{today.replace('-', '')}"
    source_contract = ",".join(DEFAULT_SOURCES)
    dispatched = []
    for iid, project_id, base_query in PROJECTS:
        workspace = root / "instances" / iid
        for serial in range(1, max(1, min(int(pairs_per_project), 5)) + 1):
            query = f"{base_query}；采样变体{serial}只改变问题措辞，不改变来源、预算和验真合同"
            match_key = f"{today}:{project_id}:pair_{serial:02d}"
            shared = (
                f"[experiment_id={experiment_id}] [match_key={match_key}] "
                f"query: {query}, project_id: {project_id}, "
                f"research_project_id: {research_project_id}, instance_id: {iid}, "
                f"budget_chars: 9000, source_paths=[{source_contract}]"
            )
            baseline = (f"[policy_arm=baseline] [strategy_id=baseline_governed_context_v1] "
                        + shared)
            candidate = (f"[policy_arm=candidate] [strategy_id=candidate_evidence_trajectory_context_v1] "
                         f"Candidate ID={candidate_id} " + shared)
            for arm, message in (("baseline", baseline), ("candidate", candidate)):
                dispatched.append({"instance_id": iid, "project_id": project_id,
                                   "match_key": match_key, "policy_arm": arm,
                                   "message_id": _inject(workspace, iid, message)})
    window = {"date": today, "experiment_id": experiment_id,
              "pairs_per_project": pairs_per_project, "projects": [p[1] for p in PROJECTS],
              "messages": dispatched, "dispatched_at": datetime.now().astimezone().isoformat(),
              "qualification": "pending normal Harness/Claim/delivery/reward gates"}
    windows[today] = window
    state = {"schema_version": 1, "candidate_id": candidate_id,
             "provider_constraint": "MiniMax-M3 only; no DeepSeek fallback",
             "max_active_slots": 2, "real_date_windows_only": True,
             "windows": windows, "updated_at": datetime.now().astimezone().isoformat()}
    atomic_json(path, state)
    return {"ok": True, "status": "daily_window_dispatched", "window": window,
            "path": str(path)}


def review_and_maybe_activate(root: Path, *, candidate_id: str,
                              llm_experiments: list[str],
                              auto_activate_authorized: bool = False) -> dict:
    from partner.governance.readiness_activation import review_and_maybe_activate as review

    return review(
        root,
        candidate_id=candidate_id,
        llm_experiments=llm_experiments,
        auto_activate_authorized=auto_activate_authorized,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--research-project", default="partner04_harness_learning_20260831_v3")
    parser.add_argument("--pairs-per-project", type=int, default=2)
    parser.add_argument("--interval", type=int, default=21600)
    parser.add_argument("--duration", type=int, default=604800)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--llm-experiment", action="append", default=[])
    parser.add_argument("--auto-activate-authorized", action="store_true")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    deadline = time.monotonic() + max(1, args.duration)
    while True:
        result = dispatch_daily(
            root, candidate_id=args.candidate_id,
            research_project_id=args.research_project,
            pairs_per_project=args.pairs_per_project)
        review = review_and_maybe_activate(
            root, candidate_id=args.candidate_id,
            llm_experiments=list(args.llm_experiment),
            auto_activate_authorized=args.auto_activate_authorized,
        ) if args.llm_experiment else {"decision": "review_not_configured"}
        print(json.dumps({"sampling": result, "readiness_review": review},
                         ensure_ascii=False), flush=True)
        if args.once or time.monotonic() >= deadline:
            return 0 if result.get("ok") else 2
        time.sleep(max(300, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
