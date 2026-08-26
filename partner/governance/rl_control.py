"""Conservative RL control plane: choose canary arms and decide them from v2 outcomes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evolution_loop import decide_experiment, record_issue, start_experiment
from .models import now_iso
from .storage import append_jsonl, atomic_json, governance_log, workspace_root


MIN_ARM_SAMPLES = 3
MIN_CANDIDATE_SUCCESS = 0.67
MIN_REWARD_GAIN = 0.15


def start_manual_truth_canary(workspace: str, project_id: str) -> dict[str, Any]:
    """Create or reuse the bounded v1-v2 manual truth-audit experiment."""
    decision_key = f"{project_id}:manual_final_artifact_truth"
    issue = record_issue(workspace, {
        "summary": f"manual final-artifact truth canary: {project_id}",
        "category": "verification", "severity": "medium",
        "evidence": [f"project_id={project_id}", "baseline=manual_stable_grounded_v1",
                     "candidate=manual_stable_truth_audit_v2"],
        "instance_id": "05", "project_id": project_id,
    }).get("issue") or {}
    issue_id = str(issue.get("issue_id") or "")
    experiments = _read_jsonl(governance_log(workspace, "experiments"))
    existing = next((row for row in reversed(experiments)
                     if row.get("issue_id") == issue_id and row.get("status") == "candidate"), None)
    if existing:
        experiment = existing
    else:
        experiment = (start_experiment(workspace, {
            "issue_id": issue_id,
            "hypothesis": "a deterministic final-artifact source/quote audit reduces false-success without harming completion",
            "intervention": "candidate alone verifies every declared source_path/evidence_quote pair against each real input file",
            "baseline": {"strategy_id": "manual_stable_grounded_v1"},
            "success_criteria": [
                "at least 3 samples per arm", "0 false-success in candidate arm",
                "all candidate source quotes match named inputs",
                "candidate mean v2 reward improves by >= 0.15", "full regression remains passing",
            ],
            "tests": ["tests/test_manual_governance.py", "tests/test_rl_control.py"],
            "project_id": project_id,
        }).get("experiment") or {})
    return {"ok": bool(experiment), "decision_key": decision_key,
            "experiment": experiment, "issue": issue}


def assign_manual_canary(workspace: str, *, project_id: str, experiment_id: str,
                         decision_key: str) -> dict[str, Any]:
    """Alternate candidate/baseline and return markers for one explicit task."""
    rl_dir = workspace_root(workspace) / "share" / "mind" / "governance" / "rl"
    prior = [row for row in _read_jsonl(rl_dir / "canary_assignments.jsonl")
             if row.get("experiment_id") == experiment_id and row.get("decision_key") == decision_key]
    arm = "candidate" if len(prior) % 2 == 0 else "baseline"
    strategy_id = "manual_stable_truth_audit_v2" if arm == "candidate" else "manual_stable_grounded_v1"
    assignment_id = f"assign_{len(prior) + 1:02d}_{experiment_id}"
    row = {
        "assignment_id": assignment_id, "project_id": project_id,
        "decision_key": decision_key, "strategy_id": strategy_id,
        "policy_arm": arm, "experiment_id": experiment_id, "selected_at": now_iso(),
    }
    append_jsonl(rl_dir / "canary_assignments.jsonl", row)
    markers = (f"[strategy_id={strategy_id}] [policy_decision={decision_key}] "
               f"[policy_arm={arm}] [experiment_id={experiment_id}]")
    requirement = ""
    if arm == "candidate":
        requirement = (
            "候选臂额外合同：最终 Markdown 必须为每一个实际输入文件分别写连续两行 "
            "source_path: <绝对路径> 和 evidence_quote: <从该文件逐字连续复制且至少20字符的原文>；"
            "最终治理层会重新打开源文件逐项核验，少一项或改写即失败。"
        )
    return {"ok": True, **row, "markers": markers, "requirement": requirement}


def record_regression_attestation(workspace: str, *, experiment_id: str, command: str,
                                  passed: bool, summary: str) -> dict[str, Any]:
    if not experiment_id or not command or not summary:
        raise ValueError("experiment_id, command and summary are required")
    path = (workspace_root(workspace) / "share" / "mind" / "governance" / "rl"
            / "regression_attestations" / f"{experiment_id}.json")
    payload = {"experiment_id": experiment_id, "command": command, "passed": bool(passed),
               "summary": summary, "recorded_at": now_iso()}
    atomic_json(path, payload)
    return {"ok": True, "attestation": payload, "path": str(path)}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, ValueError):
        pass
    return rows


def _experiment_for(workspace: str, project_id: str, decision_key: str,
                    baseline: str, candidate: str) -> str:
    issue = record_issue(workspace, {
        "summary": f"RL canary action selection: {decision_key}",
        "category": "planning", "severity": "medium",
        "evidence": [f"project_id={project_id}", f"baseline={baseline}", f"candidate={candidate}"],
        "instance_id": "05", "project_id": project_id,
    }).get("issue") or {}
    issue_id = str(issue.get("issue_id") or "")
    existing = _read_jsonl(governance_log(workspace, "experiments"))
    prior = next((row for row in reversed(existing) if row.get("issue_id") == issue_id), None)
    if prior:
        return str(prior.get("experiment_id") or "")
    result = start_experiment(workspace, {
        "issue_id": issue_id,
        "hypothesis": f"candidate {candidate} improves verified business progress over {baseline}",
        "intervention": "alternate baseline/candidate in bounded safe Campaign WorkItems",
        "baseline": {"strategy_id": baseline},
        "success_criteria": [
            "at least 3 samples per arm", "candidate success rate >= 0.67",
            "candidate mean v2 reward improves by >= 0.15", "regression remains passing",
        ],
        "tests": ["tests/test_rl_control.py", "tests/test_campaign.py"],
        "project_id": project_id,
    })
    return str((result.get("experiment") or {}).get("experiment_id") or "")


def choose_action(workspace: str, project_id: str, decision_key: str,
                  choices: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose promoted action, otherwise alternate a bounded baseline/candidate canary."""
    if not choices:
        raise ValueError("choices must not be empty")
    root = workspace_root(workspace)
    rl_dir = root / "share" / "mind" / "governance" / "rl"
    control_path = rl_dir / "control_policy.json"
    try:
        control = json.loads(control_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        control = {"schema_version": 1, "promoted": {}}
    promoted_id = str((control.get("promoted") or {}).get(decision_key) or "")
    selected = next((choice for choice in choices if choice.get("strategy_id") == promoted_id), None)
    arm = "production" if selected else ""
    experiment_id = ""
    if not selected:
        assignments = [row for row in _read_jsonl(rl_dir / "canary_assignments.jsonl")
                       if row.get("decision_key") == decision_key]
        if len(choices) == 1:
            selected, arm = choices[0], "baseline"
        else:
            # Start with one safe candidate, then alternate to obtain a matched baseline.
            selected = choices[1] if len(assignments) % 2 == 0 else choices[0]
            arm = "candidate" if selected is choices[1] else "baseline"
            experiment_id = _experiment_for(
                workspace, project_id, decision_key,
                str(choices[0]["strategy_id"]), str(choices[1]["strategy_id"]),
            )
    result = dict(selected)
    result.update({
        "policy_decision": decision_key,
        "policy_arm": arm,
        "experiment_id": experiment_id,
    })
    append_jsonl(rl_dir / "canary_assignments.jsonl", {
        "project_id": project_id, "decision_key": decision_key,
        "strategy_id": result["strategy_id"], "policy_arm": arm,
        "experiment_id": experiment_id, "selected_at": now_iso(),
    })
    return result


def evaluate_canaries(workspace: str) -> dict[str, Any]:
    """Write one promotion/rejection/inconclusive decision once both arms are testable."""
    root = workspace_root(workspace)
    rl_dir = root / "share" / "mind" / "governance" / "rl"
    corrections: set[str] = set()
    projects_root = root / "share" / "projects"
    for path in projects_root.glob("*/governance/receipt_corrections.jsonl"):
        for correction in _read_jsonl(path):
            receipt_id = str(correction.get("receipt_id") or "")
            if correction.get("action") == "invalidate" and receipt_id:
                corrections.add(receipt_id)
            elif correction.get("action") == "reinstate" and receipt_id:
                corrections.discard(receipt_id)
    trajectories = [row for row in _read_jsonl(rl_dir / "trajectories.jsonl")
                    if int(row.get("schema_version") or 0) >= 2
                    and (row.get("policy_eligible") is True
                         or bool((row.get("action") or {}).get("experiment_id")))
                    and str((row.get("state") or {}).get("receipt_id") or "") not in corrections]
    assignments = _read_jsonl(rl_dir / "canary_assignments.jsonl")
    existing = _read_jsonl(governance_log(workspace, "promotion_decisions"))
    experiments = {str(row.get("experiment_id")): row for row in _read_jsonl(governance_log(workspace, "experiments"))}
    decisions: list[dict[str, Any]] = []
    control_path = rl_dir / "control_policy.json"
    try:
        control = json.loads(control_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        control = {"schema_version": 1, "promoted": {}}
    for key in sorted({str(row.get("decision_key")) for row in assignments if row.get("decision_key")}):
        selected = [row for row in assignments if row.get("decision_key") == key]
        experiment_id = next((str(row.get("experiment_id")) for row in reversed(selected)
                              if row.get("experiment_id")), "")
        if not experiment_id or any(row.get("experiment_id") == experiment_id for row in existing):
            continue
        rows = [row for row in trajectories
                if (row.get("action") or {}).get("policy_decision") == key
                and (row.get("action") or {}).get("experiment_id") == experiment_id]
        arms = {arm: [row for row in rows if (row.get("action") or {}).get("policy_arm") == arm]
                for arm in ("baseline", "candidate")}
        if any(len(values) < MIN_ARM_SAMPLES for values in arms.values()):
            continue
        metrics: dict[str, Any] = {}
        for arm, values in arms.items():
            rewards = [float(row.get("reward") or 0.0) for row in values]
            metrics[arm] = {
                "samples": len(rewards), "mean_reward": sum(rewards) / len(rewards),
                "success_rate": sum(value > 0 for value in rewards) / len(rewards),
                "false_success_count": sum(bool((value.get("outcome") or {}).get("false_success")) for value in values),
                "unique_tasks": len({str(value.get("work_item_id") or value.get("trajectory_id") or "") for value in values}),
            }
        experiment = experiments.get(experiment_id) or {}
        manual_truth_experiment = any(
            "false-success" in str(criterion) or "source quotes" in str(criterion)
            for criterion in (experiment.get("success_criteria") or [])
        )
        candidate_grounded = all(
            bool(((row.get("outcome") or {}).get("truth_audit") or {}).get("passed"))
            for row in arms["candidate"]
        ) if manual_truth_experiment else True
        attestation_path = rl_dir / "regression_attestations" / f"{experiment_id}.json"
        try:
            attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            attestation = {}
        # Do not consume the one-shot experiment decision before the declared
        # regression command has actually run and persisted its result.
        if not attestation:
            continue
        regression_passed = bool(attestation.get("passed"))
        gain = metrics["candidate"]["mean_reward"] - metrics["baseline"]["mean_reward"]
        candidate_clean = metrics["candidate"]["false_success_count"] == 0 and candidate_grounded
        if (regression_passed and candidate_clean
                and metrics["candidate"]["success_rate"] >= MIN_CANDIDATE_SUCCESS
                and gain >= MIN_REWARD_GAIN):
            decision, rollback = "promoted", False
        elif (not regression_passed or metrics["candidate"]["false_success_count"] > 0 or not candidate_grounded
              or gain <= -0.10 or metrics["candidate"]["success_rate"] < MIN_CANDIDATE_SUCCESS):
            decision, rollback = "rejected", True
        else:
            decision, rollback = "inconclusive", False
        candidate_id = next((str(row.get("strategy_id")) for row in reversed(selected)
                             if row.get("policy_arm") == "candidate"), "")
        outcome = decide_experiment(workspace, {
            "experiment_id": experiment_id, "decision": decision,
            "evidence": [str(rl_dir / "trajectories.jsonl"), str(attestation_path),
                         f"decision_key={key}", f"reward_gain={gain:.4f}",
                         *[f"trajectory_id={row.get('trajectory_id')}" for row in arms["baseline"] + arms["candidate"]]],
            "regression_passed": regression_passed,
            "criteria_results": {
                "at least 3 samples per arm": True,
                "candidate success rate >= 0.67": metrics["candidate"]["success_rate"] >= MIN_CANDIDATE_SUCCESS,
                "candidate mean v2 reward improves by >= 0.15": gain >= MIN_REWARD_GAIN,
                "0 false-success in candidate arm": metrics["candidate"]["false_success_count"] == 0,
                "all candidate source quotes match named inputs": candidate_grounded,
                "full regression remains passing": regression_passed,
            },
            "metrics_before": metrics["baseline"], "metrics_after": metrics["candidate"],
            "rollback_required": rollback,
            "reason": f"bounded canary comparison; candidate-baseline reward gain={gain:.4f}; regression_attested={regression_passed}",
        })
        if outcome.get("ok") and decision == "promoted":
            control.setdefault("promoted", {})[key] = candidate_id
            control["updated_at"] = now_iso()
        decisions.append({"decision_key": key, "decision": decision, "metrics": metrics, "result": outcome})
    atomic_json(control_path, control)
    return {"ok": True, "decisions": decisions, "control_policy_path": str(control_path)}
