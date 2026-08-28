"""Matched counterfactual replay for low-risk Harness strategies."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .candidate_skills import register_candidate_skill
from .models import now_iso
from .storage import atomic_json, workspace_root


ADDRESSABLE_PREFLIGHT = (
    "evidence-dependent output must reference",
    "needs a synthesis step",
    "file expected_artifacts require",
    "output content is empty",
    "short placeholder",
    "unfilled template",
)

ISOLATED_PREFLIGHT_CANDIDATE = "candidate_preflight_contract_v2"
ISOLATED_PREFLIGHT_BASELINE = "baseline_current_preflight_v1"


def _states(root: Path, project_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "share/mind/governance/episodes").glob("episode_*/state.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(row, dict) and (not project_id or row.get("project_id") == project_id):
            rows.append(row)
    return rows


def _trajectory_actions(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "share/mind/governance/rl/trajectories.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            task_id = str(value.get("work_item_id") or "") if isinstance(value, dict) else ""
            if task_id:
                rows[task_id] = value
    except (OSError, ValueError, TypeError):
        pass
    return rows


def evaluate_preflight_shadow(workspace: str, *, project_id: str,
                              experiment_id: str, minimum_pairs: int = 10) -> dict[str, Any]:
    root = workspace_root(workspace)
    rows = _states(root, project_id)
    trajectories = _trajectory_actions(root)
    pairs: list[dict[str, Any]] = []
    canaries: list[dict[str, Any]] = []
    for row in rows:
        trajectory = trajectories.get(str(row.get("task_id") or ""), {})
        action = trajectory.get("action") if isinstance(trajectory.get("action"), dict) else {}
        executed_candidate = (
            action.get("strategy_id") == "candidate_preflight_aware_planning_v1"
            and (not experiment_id or action.get("experiment_id") == experiment_id)
        )
        details = [value for value in row.get("failure_details") or []
                   if value.get("class") == "planning.semantic_preflight"]
        baseline_failed = bool(details)
        repairs = sum(1 for value in row.get("model_calls") or []
                      if value.get("purpose") == "batch_planner_semantic_repair")
        if executed_candidate:
            values = (row.get("reward_vector") or {}).get("values", {})
            canaries.append({
                "episode_id": row.get("episode_id"), "task_id": row.get("task_id"),
                "candidate_executed": True, "status": row.get("status"),
                "preflight_failed": baseline_failed,
                "semantic_repair_calls": repairs,
                "truth": values.get("truth", 0.0),
                "observability": values.get("observability", 0.0),
                "policy_eligible": bool((row.get("reward_vector") or {}).get("policy_eligible")),
                "reward": (row.get("reward_vector") or {}).get("scalar", 0.0),
            })
            continue
        errors = [str(value.get("error") or "") for value in details]
        addressable = baseline_failed and all(any(token in error for token in ADDRESSABLE_PREFLIGHT)
                                              for error in errors)
        candidate_projected_failed = baseline_failed and not addressable
        baseline_repairs = repairs
        pairs.append({
            "episode_id": row.get("episode_id"), "baseline_observed": True,
            "candidate_executed": False, "baseline_preflight_failed": baseline_failed,
            "candidate_projected_preflight_failed": candidate_projected_failed,
            "addressable_by_deterministic_contract": addressable,
            "baseline_semantic_repair_calls": baseline_repairs,
            "candidate_projected_repair_calls": 0 if addressable else baseline_repairs,
            "truth": (row.get("reward_vector") or {}).get("values", {}).get("truth", 0.0),
            "observability": (row.get("reward_vector") or {}).get("values", {}).get("observability", 0.0),
        })
    sample_count = len(pairs)
    baseline_failures = sum(value["baseline_preflight_failed"] for value in pairs)
    projected_failures = sum(value["candidate_projected_preflight_failed"] for value in pairs)
    repairs_before = sum(value["baseline_semantic_repair_calls"] for value in pairs)
    repairs_after = sum(value["candidate_projected_repair_calls"] for value in pairs)
    ready = sample_count >= minimum_pairs
    canary_metrics = {
        "executed": len(canaries),
        "completed": sum(value["status"] == "completed" for value in canaries),
        "policy_eligible": sum(value["policy_eligible"] for value in canaries),
        "preflight_failures": sum(value["preflight_failed"] for value in canaries),
        "semantic_repair_calls": sum(value["semantic_repair_calls"] for value in canaries),
        "truth_passes": sum(value["truth"] == 1.0 for value in canaries),
        "observability_passes": sum(value["observability"] == 1.0 for value in canaries),
    }
    result = {
        "schema_version": 1, "mode": "matched_counterfactual_shadow",
        "causal_status": "projected_plus_bounded_canary" if canaries else "projected_not_executed",
        # The policy markers currently provide attribution only.  Until the
        # planner actually selects different baseline/candidate execution
        # paths, these observations cannot identify an intervention effect.
        "intervention_isolated": False,
        "promotion_blockers": [
            "baseline/candidate execution path is not feature-isolated",
            "matched executions from distinct bounded tasks are still required",
        ],
        "promotion": False,
        "project_id": project_id, "experiment_id": experiment_id,
        "strategy_id": "candidate_preflight_aware_planning_v1", "pairs": sample_count,
        "minimum_pairs": minimum_pairs, "sample_gate_passed": ready,
        "metrics": {
            "baseline_preflight_failures": baseline_failures,
            "candidate_projected_preflight_failures": projected_failures,
            "baseline_semantic_repair_calls": repairs_before,
            "candidate_projected_repair_calls": repairs_after,
            "projected_failures_avoided": baseline_failures - projected_failures,
            "projected_model_calls_saved": repairs_before - repairs_after,
        },
        "pairs_detail": pairs,
        "executed_canaries": {"metrics": canary_metrics, "episodes": canaries},
        "created_at": now_iso(),
        "next_gate": (
            "feature-isolate baseline/candidate execution, then collect matched executions; bounded canaries are not promotion evidence"
            if canaries else "execute a bounded canary; projected shadow metrics are not promotion evidence"
        ),
    }
    directory = root / "share/mind/governance/rl/shadow_evaluations"
    path = directory / f"{experiment_id}_preflight.json"
    atomic_json(path, result)
    skill = register_candidate_skill(workspace, {
        "candidate_id": "candidate_preflight_aware_planning_v1",
        "title": "在模型规划前注入可执行 preflight 合同并优先确定性修复",
        "status": "canary" if canaries else ("shadow" if ready else "candidate"),
        "experiment_id": experiment_id,
        "strategy_id": "candidate_preflight_aware_planning_v1",
        "source_episode_ids": [str(value["episode_id"]) for value in pairs + canaries],
        "failure_classes": ["planning.semantic_preflight"],
        "applicability": ["manual_stable", "file-producing batch plans"],
        "non_applicability": ["browser side effects", "human approval actions", "unknown event types"],
        "counterexamples": [str(value["episode_id"]) for value in pairs if value["truth"] < 1.0],
        "baseline": result["metrics"],
        "intervention": "known writer/dependency/synthesis defects use deterministic normalization before model repair",
        "success_criteria": ["baseline/candidate intervention path is feature-isolated",
                             "at least 10 matched pairs", "candidate canary truth=1",
                             "no observability regression", "fewer semantic repair model calls"],
        "shadow_evidence": {
            "path": str(path), "causal_status": result["causal_status"],
            "executed_canary_metrics": canary_metrics,
        },
        "rollback": "retain current planner and semantic repair path",
    })
    return {"ok": True, **result, "path": str(path), "candidate_skill": skill.get("candidate")}


def evaluate_isolated_preflight_canary(
    workspace: str,
    *,
    project_id: str,
    experiment_id: str,
    minimum_pairs: int = 3,
) -> dict[str, Any]:
    """Evaluate independently executed match-key pairs with route proof."""
    root = workspace_root(workspace)
    trajectories = _trajectory_actions(root)
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    rejected: list[dict[str, str]] = []
    for state in _states(root, project_id):
        task_id = str(state.get("task_id") or "")
        trajectory = trajectories.get(task_id, {})
        action = trajectory.get("action") if isinstance(trajectory.get("action"), dict) else {}
        if action.get("experiment_id") != experiment_id:
            continue
        arm = str(action.get("policy_arm") or "")
        match_key = str(action.get("match_key") or "")
        strategy = str(action.get("strategy_id") or "")
        proof = state.get("experiment_intervention") or {}
        expected_strategy = (
            ISOLATED_PREFLIGHT_CANDIDATE if arm == "candidate" else ISOLATED_PREFLIGHT_BASELINE
        )
        proof_ok = bool(
            match_key
            and arm in {"baseline", "candidate"}
            and strategy == expected_strategy
            and proof.get("experiment_id") == experiment_id
            and proof.get("policy_arm") == arm
            and proof.get("strategy_id") == strategy
            and proof.get("match_key") == match_key
            and proof.get("route") == (
                "candidate_prompt_contract_v2" if arm == "candidate" else "baseline_current_contract"
            )
            and bool(proof.get("active")) == (arm == "candidate")
        )
        if not proof_ok:
            rejected.append({"task_id": task_id, "reason": "missing_or_invalid_intervention_proof"})
            continue
        repairs = sum(
            1 for value in state.get("model_calls") or []
            if value.get("purpose") == "batch_planner_semantic_repair"
        )
        values = (state.get("reward_vector") or {}).get("values") or {}
        arm_rows = grouped.setdefault(match_key, {})
        existing = arm_rows.get(arm) or {}
        executed_at = str(trajectory.get("created_at") or state.get("reduced_at") or "")
        if existing and str(existing.get("_executed_at") or "") >= executed_at:
            continue
        arm_rows[arm] = {
            "task_id": task_id,
            "episode_id": state.get("episode_id"),
            "strategy_id": strategy,
            "route": proof.get("route"),
            "status": state.get("status"),
            "truth": float(values.get("truth") or 0.0),
            "observability": float(values.get("observability") or 0.0),
            "semantic_repair_calls": repairs,
            "reward": float((state.get("reward_vector") or {}).get("scalar") or 0.0),
            "policy_eligible": bool((state.get("reward_vector") or {}).get("policy_eligible")),
            "_executed_at": executed_at,
        }
    pairs = [
        {"match_key": key, "baseline": arms["baseline"], "candidate": arms["candidate"]}
        for key, arms in sorted(grouped.items())
        if set(arms) == {"baseline", "candidate"}
        and arms["baseline"]["task_id"] != arms["candidate"]["task_id"]
    ]
    for pair in pairs:
        pair["baseline"].pop("_executed_at", None)
        pair["candidate"].pop("_executed_at", None)
    pair_count = len(pairs)
    baseline = [pair["baseline"] for pair in pairs]
    candidate = [pair["candidate"] for pair in pairs]

    def mean(rows: list[dict[str, Any]], key: str) -> float:
        return round(sum(float(row[key]) for row in rows) / len(rows), 4) if rows else 0.0

    metrics = {
        "baseline_completed": sum(row["status"] == "completed" for row in baseline),
        "candidate_completed": sum(row["status"] == "completed" for row in candidate),
        "baseline_truth_passes": sum(row["truth"] == 1.0 for row in baseline),
        "candidate_truth_passes": sum(row["truth"] == 1.0 for row in candidate),
        "baseline_observability_passes": sum(row["observability"] == 1.0 for row in baseline),
        "candidate_observability_passes": sum(row["observability"] == 1.0 for row in candidate),
        "baseline_semantic_repair_calls": sum(row["semantic_repair_calls"] for row in baseline),
        "candidate_semantic_repair_calls": sum(row["semantic_repair_calls"] for row in candidate),
        "baseline_mean_reward": mean(baseline, "reward"),
        "candidate_mean_reward": mean(candidate, "reward"),
    }
    sample_gate = pair_count >= max(1, int(minimum_pairs))
    quality_gate = bool(
        sample_gate
        and metrics["candidate_completed"] == pair_count
        and metrics["candidate_truth_passes"] == pair_count
        and metrics["candidate_observability_passes"] >= metrics["baseline_observability_passes"]
        and metrics["candidate_semantic_repair_calls"] <= metrics["baseline_semantic_repair_calls"]
        and metrics["candidate_mean_reward"] >= metrics["baseline_mean_reward"]
    )
    result = {
        "schema_version": 1,
        "mode": "matched_executed_isolated_canary",
        "project_id": project_id,
        "experiment_id": experiment_id,
        "baseline_strategy_id": ISOLATED_PREFLIGHT_BASELINE,
        "candidate_strategy_id": ISOLATED_PREFLIGHT_CANDIDATE,
        "intervention_isolated": pair_count > 0,
        "independent_task_ids": all(
            pair["baseline"]["task_id"] != pair["candidate"]["task_id"] for pair in pairs
        ),
        "pairs": pair_count,
        "minimum_pairs": minimum_pairs,
        "sample_gate_passed": sample_gate,
        "quality_gate_passed": quality_gate,
        "promotion": False,
        "decision": "ready_for_explicit_decision" if quality_gate else "inconclusive",
        "metrics": metrics,
        "pairs_detail": pairs,
        "rejected_executions": rejected,
        "created_at": now_iso(),
    }
    directory = root / "share/mind/governance/rl/shadow_evaluations"
    path = directory / f"{experiment_id}_isolated_preflight.json"
    atomic_json(path, result)
    return {"ok": True, **result, "path": str(path)}
