#!/usr/bin/env python3
"""Run the Event-first skipped-terminal bounded-repair shadow experiment."""
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
        "summary": f"{failure_class} 中 dependency-skipped 步骤缺少权威 terminal log，被误判为 unclosed",
        "category": "event", "severity": "high",
        "evidence": [str(diagnosis_path)], "instance_id": "05",
        "project_id": "agent_self_evolution",
    })
    if not issue.get("ok"):
        raise RuntimeError(f"issue registration failed: {issue}")
    experiment = start_experiment(str(workspace), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": "仅对 trace 中有 required-dependency skip 证据的步骤补充 skipped terminal，可消除假 unclosed 且不掩盖真实中断",
        "intervention": "PlanExecutor 在 dependency skip 早退前持久化 terminal_status=skipped；Episode reducer 将其视为终态而非 tool failure",
        "baseline": {"arm": "no_op", "diagnosis_id": diagnosis.get("diagnosis_id"),
                     "classification": diagnosis.get("classification")},
        "success_criteria": ["all matched Episode evidence readable",
                             "at least one false-unclosed step terminalized",
                             "no evidence-free interruption hidden",
                             "historical Episode evidence byte-identical",
                             "production policy untouched"],
        "project_id": "agent_self_evolution", "tests": [str(diagnosis_path)],
    })
    if not experiment.get("ok"):
        raise RuntimeError(f"experiment registration failed: {experiment}")
    experiment_id = experiment["experiment"]["experiment_id"]
    candidate_id = f"candidate_skipped_terminal_{experiment_id[-12:]}"
    registration = register_candidate_skill(str(workspace), {
        "candidate_id": candidate_id, "title": "Dependency-skipped terminal observability repair",
        "status": "candidate", "artifact_type": "runtime_observability_patch",
        "project_id": "agent_self_evolution", "experiment_id": experiment_id,
        "strategy_id": "terminalize_dependency_skipped_step_v1",
        "source_episode_ids": [str(row.get("state_path") or "")
                               for row in diagnosis.get("episodes") or []],
        "success_criteria": experiment["experiment"]["success_criteria"],
        "applicability": [failure_class, "required dependency failure", "Episode terminal accounting"],
        "non_applicability": ["model timeout", "process crash", "unknown interruption",
                              "task outcome repair", "automatic production promotion"],
        "intervention": experiment["experiment"]["intervention"],
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "agent_active_learning_skipped_terminal_repair_shadow",
                               "allowed_instances": ["05"],
                               "default_params": {"diagnosis_path": str(diagnosis_path)}},
        "evaluation_contract": {"ready": True, "kind": "artifact_assertions",
                                "checks": experiment["experiment"]["success_criteria"]},
        "baseline": experiment["experiment"]["baseline"],
        "rollback": "remove terminal-log hook; historical evidence was never rewritten",
    })
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    working = workspace / "instances/05/state/tasks" / f"active_repair_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    task = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                           user_message="Event-first dependency-skipped repair shadow")
    ctx = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                          task_instance=task)
    execution = atomic_execute_candidate(ctx, {
        "candidate_id": candidate_id, "execution_id": f"exec_{experiment_id}",
        "instance_id": "05", "mode": "shadow",
    })
    metrics = dict((execution.get("evaluation") or {}).get("metrics") or {})
    criteria = {
        "evidence_readable": metrics.get("evidence_coverage") == 1.0,
        "false_unclosed_terminalized": int(metrics.get("bounded_repair_terminalized_count") or 0) > 0,
        "unknown_interruptions_preserved": (
            int(metrics.get("baseline_unclosed_count") or 0)
            == int(metrics.get("bounded_repair_terminalized_count") or 0)
            + int(metrics.get("bounded_repair_remaining_unclosed_count") or 0)),
        "historical_evidence_immutable": metrics.get("historical_evidence_immutable") is True,
        "production_untouched": execution.get("production_mutation") is False,
    }
    feedback = atomic_agent_active_learning_feedback(ctx, {
        "context_key": failure_class, "option_id": "bounded_repair",
        "success": all(criteria.values()), "evidence_refs": [str(execution.get("path") or "")],
    }) if execution.get("path") else {"ok": False, "status": "no_repair_evidence"}
    policy = decide_experiment(str(workspace), {
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "decision": "inconclusive", "evidence": [str(execution.get("path") or "")],
        "regression_passed": all(criteria.values()), "criteria_results": criteria,
        "metrics_before": {"unclosed_count": metrics.get("baseline_unclosed_count")},
        "metrics_after": {"false_unclosed_terminalized": metrics.get("bounded_repair_terminalized_count"),
                          "remaining_real_or_unknown": metrics.get("bounded_repair_remaining_unclosed_count")},
        "rollback_required": False,
        "reason": "historical observability replay passed; a fresh bounded execution and full regression are still required before treating the runtime patch as validated",
        "project_id": "agent_self_evolution",
    })
    result = {"schema_version": 1, "experiment_id": experiment_id,
              "candidate_id": candidate_id, "candidate_registration": registration,
              "execution": execution, "feedback": feedback, "criteria": criteria,
              "policy_decision": policy,
              "evolution_ledger": verify_evolution_ledger(str(workspace)),
              "production_effective": False}
    result_path = working / "agent_active_learning_repair_experiment.json"
    atomic_json(result_path, result)
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--diagnosis", required=True)
    args = parser.parse_args()
    result = run(Path(args.workspace), Path(args.diagnosis))
    metrics = result["execution"]["evaluation"]["metrics"]
    print(json.dumps({"experiment_id": result["experiment_id"],
                      "candidate_id": result["candidate_id"],
                      "metrics": metrics, "criteria": result["criteria"],
                      "feedback": result["feedback"].get("status"),
                      "policy_status": result["policy_decision"].get("status"),
                      "ledger": result["evolution_ledger"],
                      "result_path": result["result_path"]}, ensure_ascii=False, indent=2))
    return 0 if all(result["criteria"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
