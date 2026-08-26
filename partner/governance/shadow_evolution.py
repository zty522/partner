"""Shadow-only process evolution over reduced Episode Trace v3 bundles."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .evolution_loop import record_issue, start_experiment
from .models import now_iso
from .storage import append_jsonl, atomic_json, governance_log, workspace_root


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, ValueError, TypeError):
        pass
    return rows


def _states(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "share/mind/governance/episodes").glob("episode_*/state.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(value, dict):
            value["state_path"] = str(path)
            rows.append(value)
    return rows


def run_shadow_evolution(workspace: str, *, project_id: str = "") -> dict[str, Any]:
    """Cluster observed process failures and create at most one candidate.

    This is experience reuse, not production learning: it never changes a
    prompt, policy, skill, event handler, scheduler, or control_policy.json.
    """
    root = workspace_root(workspace)
    rows = [row for row in _states(root) if not project_id or row.get("project_id") == project_id]
    failures = Counter(value for row in rows for value in row.get("failure_classes") or [])
    if not rows:
        return {"ok": True, "status": "insufficient_evidence", "promotion": False, "episodes": 0}
    target = failures.most_common(1)[0][0] if failures else "reward.credit_assignment_missing"
    candidate_id = {
        "planning.semantic_preflight": "candidate_preflight_aware_planning_v1",
        "runtime.timeout": "candidate_failure_class_recovery_v1",
        "lifecycle.unclosed_tool": "candidate_lifecycle_brackets_v1",
    }.get(target, "candidate_episode_reward_vector_v1")
    issue = record_issue(workspace, {
        "summary": f"episode shadow evidence identified process target: {target}",
        "category": "planning" if target.startswith("planning.") else "verification",
        "severity": "medium", "evidence": [str(row["state_path"]) for row in rows[-5:]],
        "instance_id": "05", "project_id": project_id or "partner_self_evolution",
    }).get("issue") or {}
    hypothesis = f"{candidate_id} reduces {target} while preserving truth, safety and manual user visibility"
    prior: dict[str, Any] = {}
    for row in reversed(_read_jsonl(governance_log(workspace, "experiments"))):
        if (str(row.get("issue_id") or "") == str(issue.get("issue_id") or "")
                and str(row.get("hypothesis") or "") == hypothesis
                and row.get("status") in {"candidate", "validating"}):
            prior = row
            break
    experiment = prior or (start_experiment(workspace, {
        "issue_id": issue.get("issue_id") or "", "hypothesis": hypothesis,
        "intervention": "shadow replay only; candidate may annotate plans but cannot execute or enter control_policy",
        "baseline": {"strategy_id": "current_production", "episode_ids": [row.get("episode_id") for row in rows]},
        "success_criteria": [
            "at least 10 matched episodes per arm before promotion review",
            "truth and safety hard gates remain 1.0",
            "candidate reduces target failure rate without lower business progress or observability",
            "full regression passes and a user-authorized PromotionDecision exists",
        ],
        "tests": ["tests/test_episode_trace.py", "tests/test_rl_control.py"],
        "project_id": project_id or "partner_self_evolution",
    }).get("experiment") or {})
    result = {
        "schema_version": 1, "mode": "shadow", "status": "candidate_created",
        "promotion": False, "production_mutation": False, "candidate_id": candidate_id,
        "target_failure_class": target, "failure_counts": dict(failures), "episodes": len(rows),
        "episode_ids": [row.get("episode_id") for row in rows], "issue_id": issue.get("issue_id") or "",
        "experiment_id": experiment.get("experiment_id") or "", "created_at": now_iso(),
        "next_gate": "collect matched baseline/candidate shadow episodes; minimum 10 per arm",
    }
    out_dir = root / "share/mind/governance/rl/shadow_experiments"
    atomic_json(out_dir / f"{result['experiment_id'] or candidate_id}.json", result)
    append_jsonl(root / "share/mind/governance/rl/shadow_runs.jsonl", result)
    return {"ok": True, **result}
