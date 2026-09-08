#!/usr/bin/env python3
"""Run a governed fresh PlanExecutor canary for skipped-terminal repair."""
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


def run(workspace: Path, source_repair_path: Path) -> dict:
    workspace, source_repair_path = workspace.resolve(), source_repair_path.resolve()
    repair = json.loads(source_repair_path.read_text(encoding="utf-8"))
    issue = record_issue(str(workspace), {
        "summary": "历史 skipped-terminal replay 需要 fresh PlanExecutor 并行拓扑验证",
        "category": "verification", "severity": "high",
        "evidence": [str(source_repair_path)], "instance_id": "05",
        "project_id": "agent_self_evolution",
    })
    if not issue.get("ok"):
        raise RuntimeError(f"issue registration failed: {issue}")
    experiment = start_experiment(str(workspace), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": "当前 PlanExecutor 在一个分支输入失败、另一分支成功时，会为级联 skip 的 generate_text/push 各写一次终态且不调用其 handler",
        "intervention": "在隔离目录运行真实 PlanExecutor 五步并行 DAG；全部 handler 本地确定性、无外部模型和网络",
        "baseline": {"source_repair_evaluation": repair.get("repair_evaluation_id"),
                     "historical_terminalized": (repair.get("metrics") or {}).get(
                         "bounded_repair_terminalized_count")},
        "success_criteria": ["real PlanExecutor path", "parallel sibling completes",
                             "skipped handlers never run", "each skipped step has one terminal",
                             "terminal semantics are skipped", "zero model calls"],
        "project_id": "agent_self_evolution", "tests": [str(source_repair_path)],
    })
    if not experiment.get("ok"):
        raise RuntimeError(f"experiment registration failed: {experiment}")
    experiment_id = experiment["experiment"]["experiment_id"]
    candidate_id = f"candidate_skipped_fresh_{experiment_id[-12:]}"
    canary_id = f"fresh_terminal_{experiment_id[-12:]}"
    registration = register_candidate_skill(str(workspace), {
        "candidate_id": candidate_id, "title": "Fresh parallel skipped-terminal canary",
        "status": "candidate", "artifact_type": "runtime_observability_canary",
        "project_id": "agent_self_evolution", "experiment_id": experiment_id,
        "strategy_id": "terminalize_dependency_skipped_step_v1_fresh_canary",
        "source_episode_ids": [str(source_repair_path)],
        "success_criteria": experiment["experiment"]["success_criteria"],
        "applicability": ["PlanExecutor dependency propagation", "parallel sibling execution",
                          "skipped terminal accounting"],
        "non_applicability": ["external model quality", "network resample",
                              "original business task outcome", "production promotion"],
        "intervention": experiment["experiment"]["intervention"],
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "agent_active_learning_skipped_terminal_fresh_canary",
                               "allowed_instances": ["05"],
                               "default_params": {"canary_id": canary_id}},
        "evaluation_contract": {"ready": True, "kind": "artifact_assertions",
                                "checks": experiment["experiment"]["success_criteria"]},
        "baseline": experiment["experiment"]["baseline"],
        "rollback": "remove terminal hook; canary runs only in isolated governance directory",
    })
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    working = workspace / "instances/05/state/tasks" / f"fresh_terminal_canary_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    task = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                           user_message="Event-first fresh skipped-terminal canary")
    ctx = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                          task_instance=task)
    execution = atomic_execute_candidate(ctx, {
        "candidate_id": candidate_id, "execution_id": f"exec_{experiment_id}",
        "instance_id": "05", "mode": "shadow",
    })
    criteria = dict(execution.get("criteria") or {})
    all_passed = bool(criteria) and all(criteria.values()) and execution.get("ok") is True
    feedback = atomic_agent_active_learning_feedback(ctx, {
        "context_key": "lifecycle.unclosed_tool/generate_text",
        "option_id": "bounded_repair", "success": all_passed,
        "evidence_refs": [str(execution.get("path") or "")],
    }) if execution.get("path") else {"ok": False, "status": "no_canary_evidence"}
    policy = decide_experiment(str(workspace), {
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "decision": "inconclusive", "evidence": [str(execution.get("path") or "")],
        "regression_passed": all_passed, "criteria_results": criteria,
        "metrics_before": {"fresh_canary": "not_run"},
        "metrics_after": {"fresh_canary": "passed" if all_passed else "failed",
                          "model_calls": execution.get("model_calls"),
                          "terminal_counts": execution.get("terminal_counts")},
        "rollback_required": False,
        "reason": "fresh isolated runtime evidence validates terminal accounting; it is not an original business-task or production canary",
        "project_id": "agent_self_evolution",
    })
    result = {"schema_version": 1, "experiment_id": experiment_id,
              "candidate_id": candidate_id, "candidate_registration": registration,
              "execution": execution, "feedback": feedback,
              "policy_decision": policy,
              "evolution_ledger": verify_evolution_ledger(str(workspace)),
              "production_effective": False}
    result_path = working / "fresh_skipped_terminal_candidate_experiment.json"
    atomic_json(result_path, result)
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--source-repair", required=True)
    args = parser.parse_args()
    result = run(Path(args.workspace), Path(args.source_repair))
    execution = result["execution"]
    print(json.dumps({"experiment_id": result["experiment_id"],
                      "candidate_id": result["candidate_id"],
                      "criteria": execution.get("criteria"),
                      "terminal_status": execution.get("terminal_status"),
                      "model_calls": execution.get("model_calls"),
                      "feedback": result["feedback"].get("status"),
                      "policy_status": result["policy_decision"].get("status"),
                      "ledger": result["evolution_ledger"],
                      "result_path": result["result_path"]}, ensure_ascii=False, indent=2))
    return 0 if execution.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
