#!/usr/bin/env python3
"""Execute one selected Agent active-learning diagnostic as a governed Candidate."""
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


def run(workspace: Path, decision_path: Path) -> dict:
    workspace, decision_path = workspace.resolve(), decision_path.resolve()
    query = json.loads(decision_path.read_text(encoding="utf-8"))
    selected = dict(query.get("selected_option") or {})
    if selected.get("event_type") != "agent_active_learning_diagnostic_shadow":
        raise ValueError("selected option is not an allow-listed diagnostic")
    failure_class = str(query.get("target_failure_class") or "")
    refs = [str(value) for value in selected.get("evidence_refs") or []]
    issue = record_issue(str(workspace), {
        "summary": f"主动学习已选择 {failure_class}，需要匹配诊断区分系统性与瞬态机制",
        "category": "verification", "severity": "medium",
        "evidence": [str(decision_path), *refs], "instance_id": "05",
        "project_id": "agent_self_evolution",
    })
    experiment = start_experiment(str(workspace), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": f"读取匹配 Episode 的未闭合步骤证据，可对 {failure_class} 给出非 uncertain 的可复验诊断",
        "intervention": "只读 Episode state 与 task_log/trace，按未闭合 step signature 聚合",
        "baseline": {"decision_id": query.get("decision_id"),
                     "hypothesis_prior": query.get("hypothesis_prior")},
        "success_criteria": ["all selected evidence is readable", "diagnosis is not uncertain",
                             "diagnosis is persisted", "ledger remains valid",
                             "production remains untouched"],
        "project_id": "agent_self_evolution", "tests": [str(decision_path), *refs],
    })
    experiment_id = experiment["experiment"]["experiment_id"]
    candidate_id = f"candidate_agent_diagnosis_{experiment_id[-12:]}"
    registration = register_candidate_skill(str(workspace), {
        "candidate_id": candidate_id, "title": f"Matched diagnosis: {failure_class}",
        "status": "candidate", "artifact_type": "active_learning_experiment",
        "project_id": "agent_self_evolution", "experiment_id": experiment_id,
        "strategy_id": "episode_matched_diagnostic_v4",
        "source_episode_ids": refs, "success_criteria": experiment["experiment"]["success_criteria"],
        "applicability": [failure_class, "shadow diagnosis"],
        "non_applicability": ["automatic repair", "production mutation", "autonomous promotion"],
        "intervention": "aggregate unmatched step signatures from immutable Episode evidence",
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "agent_active_learning_diagnostic_shadow",
                               "allowed_instances": ["05"],
                               "default_params": {"failure_class": failure_class,
                                                  "evidence_refs": refs}},
        "evaluation_contract": {"ready": True, "kind": "artifact_assertions"},
        "baseline": experiment["experiment"]["baseline"],
        "rollback": "retain diagnosis as shadow evidence; no production state changed",
    })
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    working = workspace / "instances/05/state/tasks" / f"active_diagnostic_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    task = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                           user_message="Event-first matched active-learning diagnostic")
    ctx = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                          task_instance=task)
    execution = atomic_execute_candidate(ctx, {
        "candidate_id": candidate_id, "execution_id": f"exec_{experiment_id}",
        "instance_id": "05", "mode": "shadow",
    })
    diagnosis = dict(execution.get("diagnosis") or {})
    informative = execution.get("ok") is True and diagnosis.get("classification") != "uncertain"
    feedback = atomic_agent_active_learning_feedback(ctx, {
        "context_key": failure_class, "option_id": "diagnose_failure",
        "success": informative, "evidence_refs": [str(execution.get("path") or "")],
    }) if execution.get("path") else {"ok": False, "status": "no_diagnostic_evidence"}
    criteria = {
        "evidence_readable": diagnosis.get("evidence_coverage") == 1.0,
        "informative_diagnosis": informative,
        "diagnosis_persisted": bool(execution.get("path") and Path(execution["path"]).is_file()),
        "ledger_valid": verify_evolution_ledger(str(workspace)).get("ok") is True,
        "production_untouched": execution.get("production_mutation") is False,
    }
    policy = decide_experiment(str(workspace), {
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "decision": "inconclusive", "evidence": [str(execution.get("path") or "")],
        "regression_passed": all(criteria.values()), "criteria_results": criteria,
        "metrics_before": {"classification": "unknown"},
        "metrics_after": {"classification": diagnosis.get("classification"),
                          "confidence": diagnosis.get("confidence"),
                          "evidence_coverage": diagnosis.get("evidence_coverage")},
        "rollback_required": False,
        "reason": "diagnosis updates evidence and action memory; repair efficacy is not yet established",
        "project_id": "agent_self_evolution",
    })
    result = {"schema_version": 1, "experiment_id": experiment_id,
              "candidate_id": candidate_id, "candidate_registration": registration,
              "execution": execution, "feedback": feedback, "criteria": criteria,
              "policy_decision": policy,
              "evolution_ledger": verify_evolution_ledger(str(workspace)),
              "production_effective": False}
    result_path = working / "agent_active_learning_diagnostic_experiment.json"
    atomic_json(result_path, result)
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--decision", required=True)
    args = parser.parse_args()
    result = run(Path(args.workspace), Path(args.decision))
    diagnosis = result["execution"].get("diagnosis") or {}
    print(json.dumps({"experiment_id": result["experiment_id"],
                      "candidate_id": result["candidate_id"],
                      "classification": diagnosis.get("classification"),
                      "confidence": diagnosis.get("confidence"),
                      "criteria": result["criteria"],
                      "feedback": result["feedback"].get("status"),
                      "ledger": result["evolution_ledger"],
                      "result_path": result["result_path"]}, ensure_ascii=False, indent=2))
    return 0 if all(result["criteria"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
