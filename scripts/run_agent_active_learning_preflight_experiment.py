#!/usr/bin/env python3
"""Run the Event-first verified-input-manifest repair Candidate."""
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
        "summary": "planner receives roots but not a verified manifest of explicit business inputs",
        "category": "planning", "severity": "high", "evidence": [str(diagnosis_path)],
        "instance_id": "05", "project_id": "agent_self_evolution",
    })
    experiment = start_experiment(str(workspace), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": "candidate-only verified input manifest reduces input_path_contract failures without weakening invented-path rejection",
        "intervention": "candidate planner contract lists only explicit existing allow-listed paths; baseline prompt remains byte-semantically unchanged",
        "baseline": {"diagnosis_id": diagnosis.get("diagnosis_id"),
                     "episode_count": diagnosis.get("episode_count")},
        "success_criteria": ["baseline route unchanged", "existing explicit input included",
                             "missing input excluded", "real preflight accepts verified path",
                             "invented path remains rejected", "isolated workspace only",
                             "production policy untouched"],
        "project_id": "agent_self_evolution", "tests": [str(diagnosis_path)],
    })
    experiment_id = experiment["experiment"]["experiment_id"]
    candidate_id = f"candidate_preflight_manifest_{experiment_id[-12:]}"
    canary_id = f"preflight_manifest_{experiment_id[-12:]}"
    registration = register_candidate_skill(str(workspace), {
        "candidate_id": candidate_id, "title": "Verified explicit-input planner manifest",
        "status": "candidate", "artifact_type": "planner_contract_canary",
        "project_id": "agent_self_evolution", "experiment_id": experiment_id,
        "strategy_id": "candidate_verified_input_manifest_v1",
        "source_episode_ids": [str(row.get("episode_id") or "")
                               for row in diagnosis.get("episodes") or []],
        "success_criteria": experiment["experiment"]["success_criteria"],
        "applicability": [failure_class, "manual_stable explicit file tasks"],
        "non_applicability": ["invented path recovery", "deleted input recreation",
                              "production auto-promotion"],
        "intervention": experiment["experiment"]["intervention"],
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "agent_active_learning_preflight_manifest_fresh_canary",
                               "allowed_instances": ["05"],
                               "default_params": {"canary_id": canary_id}},
        "evaluation_contract": {"ready": True, "kind": "artifact_assertions",
                                "checks": experiment["experiment"]["success_criteria"]},
        "baseline": experiment["experiment"]["baseline"],
        "rollback": "remove candidate manifest paragraph; baseline and preflight hard gate remain",
    })
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    working = workspace / "instances/05/state/tasks" / f"preflight_manifest_canary_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    task = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                           user_message="Event-first preflight manifest Candidate")
    ctx = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                          task_instance=task)
    execution = atomic_execute_candidate(ctx, {
        "candidate_id": candidate_id, "execution_id": f"exec_{experiment_id}",
        "instance_id": "05", "mode": "shadow",
    })
    criteria = {**dict(execution.get("criteria") or {}),
                "production_policy_untouched": execution.get("production_mutation") is False}
    passed = bool(criteria) and all(criteria.values()) and execution.get("ok") is True
    feedback = atomic_agent_active_learning_feedback(ctx, {
        "context_key": failure_class, "option_id": "bounded_repair", "success": passed,
        "evidence_refs": [str(execution.get("path") or "")],
    })
    policy = decide_experiment(str(workspace), {
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "decision": "inconclusive", "evidence": [str(execution.get("path") or "")],
        "regression_passed": passed, "criteria_results": criteria,
        "metrics_before": {"matched_failures": diagnosis.get("episode_count")},
        "metrics_after": {"fresh_contract_canary": "passed" if passed else "failed"},
        "rollback_required": False,
        "reason": "fresh runtime contract passed; real matched business executions are still required",
        "project_id": "agent_self_evolution",
    })
    result = {"schema_version": 1, "experiment_id": experiment_id,
              "candidate_id": candidate_id, "registration": registration,
              "execution": execution, "criteria": criteria, "feedback": feedback,
              "policy_decision": policy, "evolution_ledger": verify_evolution_ledger(str(workspace)),
              "production_effective": False}
    result_path = working / "preflight_manifest_candidate_experiment.json"
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
                      "feedback": result["feedback"].get("status"),
                      "policy_status": result["policy_decision"].get("status"),
                      "ledger": result["evolution_ledger"],
                      "result_path": result["result_path"]}, ensure_ascii=False, indent=2))
    return 0 if all(result["criteria"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
