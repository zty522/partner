#!/usr/bin/env python3
"""Run the Event-first explicit-handoff-intent Candidate experiment."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from partner.governance.candidate_skills import register_candidate_skill
from partner.governance.evolution_events import verify_evolution_ledger
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.governance.storage import atomic_json
from partner.v2.active_learning_events import atomic_agent_active_learning_feedback
from partner.v2.candidate_events import atomic_execute_candidate


def run(workspace: Path, diagnosis_path: Path) -> dict:
    workspace, diagnosis_path = workspace.resolve(), diagnosis_path.resolve()
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    failure_class = str(diagnosis.get("failure_class") or "")
    issue = record_issue(str(workspace), {
        "summary": "独立任务输入被错误当成 previous Receipt 承接意图",
        "category": "context", "severity": "high",
        "evidence": [str(diagnosis_path)], "instance_id": "05",
        "project_id": "agent_self_evolution",
    })
    experiment = start_experiment(str(workspace), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": "用显式 continuation intent 取代 inputs 非空推断，可放行独立源码/holdout任务，同时保留真正的前序交接硬门",
        "intervention": "stop_project 保留 provenance；manual governance 仅在明确 continuation 时强制消费 previous artifact",
        "baseline": {"diagnosis_id": diagnosis.get("diagnosis_id"),
                     "episode_count": diagnosis.get("episode_count"),
                     "historical_status": "unlinked_previous_receipt"},
        "success_criteria": ["standalone task with input accepted",
                             "explicit missing handoff rejected",
                             "explicit linked handoff accepted",
                             "standalone receives no handoff reward",
                             "linked continuation receives handoff reward",
                             "rejected attempt does not advance Receipt",
                             "isolated workspace only", "production policy untouched"],
        "project_id": "agent_self_evolution", "tests": [str(diagnosis_path)],
    })
    experiment_id = experiment["experiment"]["experiment_id"]
    candidate_id = f"candidate_handoff_intent_{experiment_id[-12:]}"
    canary_id = f"handoff_intent_{experiment_id[-12:]}"
    registration = register_candidate_skill(str(workspace), {
        "candidate_id": candidate_id, "title": "Explicit continuation-intent handoff contract",
        "status": "candidate", "artifact_type": "runtime_contract_canary",
        "project_id": "agent_self_evolution", "experiment_id": experiment_id,
        "strategy_id": "explicit_continuation_intent_handoff_v1",
        "source_episode_ids": [str(row.get("episode_id") or "")
                               for row in diagnosis.get("episodes") or []],
        "success_criteria": experiment["experiment"]["success_criteria"],
        "applicability": [failure_class, "manual_stable Receipt governance"],
        "non_applicability": ["automatic project continuation inference",
                              "production instance launch", "network delivery"],
        "intervention": experiment["experiment"]["intervention"],
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "agent_active_learning_handoff_intent_fresh_canary",
                               "allowed_instances": ["05"],
                               "default_params": {"canary_id": canary_id}},
        "evaluation_contract": {"ready": True, "kind": "artifact_assertions",
                                "checks": experiment["experiment"]["success_criteria"]},
        "baseline": experiment["experiment"]["baseline"],
        "rollback": "remove explicit intent forwarding and restore legacy conservative inference",
    })
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    working = workspace / "instances/05/state/tasks" / f"handoff_intent_canary_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    task = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                           user_message="Event-first handoff-intent Candidate experiment")
    ctx = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                          task_instance=task)
    execution = atomic_execute_candidate(ctx, {
        "candidate_id": candidate_id, "execution_id": f"exec_{experiment_id}",
        "instance_id": "05", "mode": "shadow",
    })
    canary_criteria = dict(execution.get("criteria") or {})
    criteria = {
        **canary_criteria,
        "production_policy_untouched": execution.get("production_mutation") is False,
    }
    all_passed = bool(criteria) and all(criteria.values()) and execution.get("ok") is True
    feedback = atomic_agent_active_learning_feedback(ctx, {
        "context_key": failure_class, "option_id": "bounded_repair",
        "success": all_passed, "evidence_refs": [str(execution.get("path") or "")],
    })
    policy = decide_experiment(str(workspace), {
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "decision": "inconclusive", "evidence": [str(execution.get("path") or "")],
        "regression_passed": all_passed, "criteria_results": criteria,
        "metrics_before": {"historical_false_rejects": diagnosis.get("episode_count")},
        "metrics_after": execution.get("observations") or {},
        "rollback_required": False,
        "reason": "fresh isolated contract canary passed; full repository regression remains the final deployment evidence",
        "project_id": "agent_self_evolution",
    })
    result = {"schema_version": 1, "experiment_id": experiment_id,
              "candidate_id": candidate_id, "candidate_registration": registration,
              "execution": execution, "feedback": feedback, "criteria": criteria,
              "policy_decision": policy,
              "evolution_ledger": verify_evolution_ledger(str(workspace)),
              "production_effective": False}
    result_path = working / "handoff_intent_candidate_experiment.json"
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
                      "observations": result["execution"].get("observations"),
                      "feedback": result["feedback"].get("status"),
                      "policy_status": result["policy_decision"].get("status"),
                      "ledger": result["evolution_ledger"],
                      "result_path": result["result_path"]}, ensure_ascii=False, indent=2))
    return 0 if all(result["criteria"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
