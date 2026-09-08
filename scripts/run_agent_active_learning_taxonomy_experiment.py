#!/usr/bin/env python3
"""Run a governed shadow experiment that refines an Agent failure taxonomy."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from partner.governance.candidate_skills import register_candidate_skill
from partner.governance.evolution_events import verify_evolution_ledger
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.governance.storage import atomic_json
from partner.v2.active_learning_events import atomic_agent_active_learning_select
from partner.v2.candidate_events import atomic_execute_candidate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(workspace: Path, diagnosis_path: Path) -> dict:
    workspace = workspace.resolve()
    diagnosis_path = diagnosis_path.resolve()
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    episode_paths = [Path(row["state_path"]) for row in diagnosis.get("episodes") or []]
    episode_hashes_before = {str(path): _sha256(path) for path in episode_paths}

    issue = record_issue(str(workspace), {
        "summary": "单一 lifecycle.unclosed_tool 标签混合多个未闭合步骤机制，降低主动实验选择精度",
        "category": "model", "severity": "high",
        "evidence": [str(diagnosis_path)], "instance_id": "05",
        "project_id": "agent_self_evolution",
    })
    if not issue.get("ok"):
        raise RuntimeError(f"issue registration failed: {issue}")
    experiment = start_experiment(str(workspace), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": "按 Episode 未闭合 step_type 版本化拆分粗失败标签，会令下一次主动查询指向可诊断子机制，同时不改写历史 Episode 或生产策略",
        "intervention": "从 v4 异质性诊断生成 Candidate taxonomy，并由选择器读取其 Episode assignments",
        "baseline": {"failure_class": diagnosis.get("failure_class"),
                     "classification": diagnosis.get("classification"),
                     "signature_concentration": diagnosis.get("signature_concentration")},
        "success_criteria": ["at least two evidence-backed child classes",
                             "historical Episodes byte-identical",
                             "next query targets a child class",
                             "evolution ledger remains valid",
                             "production remains untouched"],
        "project_id": "agent_self_evolution", "tests": [str(diagnosis_path)],
    })
    if not experiment.get("ok"):
        raise RuntimeError(f"experiment registration failed: {experiment}")
    experiment_id = experiment["experiment"]["experiment_id"]
    candidate_id = f"candidate_failure_taxonomy_{experiment_id[-12:]}"
    registration = register_candidate_skill(str(workspace), {
        "candidate_id": candidate_id, "title": "Episode-grounded failure taxonomy refinement",
        "status": "candidate", "artifact_type": "active_learning_policy",
        "project_id": "agent_self_evolution", "experiment_id": experiment_id,
        "strategy_id": "failure_taxonomy_accommodation_v1",
        "source_episode_ids": [str(path) for path in episode_paths],
        "success_criteria": experiment["experiment"]["success_criteria"],
        "applicability": ["heterogeneous failure diagnosis", "shadow active-learning query"],
        "non_applicability": ["historical Episode mutation", "production policy mutation",
                              "automatic repair", "autonomous promotion"],
        "intervention": "create versioned child labels from persisted unmatched step types",
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "agent_active_learning_refine_taxonomy",
                               "allowed_instances": ["05"],
                               "default_params": {"diagnosis_path": str(diagnosis_path)}},
        "evaluation_contract": {"ready": True, "kind": "artifact_assertions",
                                "checks": experiment["experiment"]["success_criteria"]},
        "baseline": experiment["experiment"]["baseline"],
        "rollback": "remove taxonomy_index projection; immutable Episode history is unchanged",
    })

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    working = workspace / "instances/05/state/tasks" / f"active_taxonomy_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    task = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                           user_message="Event-first Agent active-learning taxonomy experiment")
    ctx = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                          task_instance=task)
    execution = atomic_execute_candidate(ctx, {
        "candidate_id": candidate_id, "execution_id": f"exec_{experiment_id}",
        "instance_id": "05", "mode": "shadow",
    })
    next_query = atomic_agent_active_learning_select(ctx, {
        "instance_ids": ["03", "05"],
        "focus_failure_class": str(diagnosis.get("failure_class") or ""),
    })
    episode_hashes_after = {str(path): _sha256(path) for path in episode_paths}
    child_target = str((next_query.get("decision") or {}).get("target_failure_class") or "")
    criteria = {
        "multiple_children": len((execution.get("taxonomy") or {}).get("children") or []) >= 2,
        "episodes_immutable": episode_hashes_before == episode_hashes_after,
        "next_query_targets_child": child_target.startswith(
            str(diagnosis.get("failure_class") or "") + "/"),
        "ledger_valid": verify_evolution_ledger(str(workspace)).get("ok") is True,
        "production_untouched": execution.get("production_mutation") is False and
                                (execution.get("taxonomy") or {}).get("production_effective") is False,
    }
    decision = decide_experiment(str(workspace), {
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "decision": "inconclusive",
        "evidence": [str(execution.get("path") or ""), str(next_query.get("path") or "")],
        "regression_passed": all(criteria.values()), "criteria_results": criteria,
        "metrics_before": {"query_granularity": "parent_failure_class"},
        "metrics_after": {"query_granularity": "step_type_child",
                          "next_target": child_target,
                          "child_count": len((execution.get("taxonomy") or {}).get("children") or [])},
        "rollback_required": False,
        "reason": "taxonomy accommodation works in shadow; repair efficacy still requires matched trials",
        "project_id": "agent_self_evolution",
    })
    result = {"schema_version": 1, "experiment_id": experiment_id,
              "candidate_id": candidate_id, "candidate_registration": registration,
              "execution": execution, "next_query": next_query, "criteria": criteria,
              "policy_decision": decision,
              "evolution_ledger": verify_evolution_ledger(str(workspace)),
              "production_effective": False}
    result_path = working / "agent_active_learning_taxonomy_experiment.json"
    atomic_json(result_path, result)
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--diagnosis", required=True)
    args = parser.parse_args()
    result = run(Path(args.workspace), Path(args.diagnosis))
    print(json.dumps({"experiment_id": result["experiment_id"],
                      "candidate_id": result["candidate_id"],
                      "criteria": result["criteria"],
                      "next_target": result["next_query"]["decision"]["target_failure_class"],
                      "policy_status": result["policy_decision"].get("status"),
                      "ledger": result["evolution_ledger"],
                      "result_path": result["result_path"]}, ensure_ascii=False, indent=2))
    return 0 if all(result["criteria"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
