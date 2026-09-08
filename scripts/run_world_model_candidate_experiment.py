#!/usr/bin/env python3
"""Register and execute one Event-first matched world-model Candidate experiment."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from partner.cognition.world_model.benchmark import frozen_dataset
from partner.governance.candidate_skills import register_candidate_skill
from partner.governance.evolution_events import verify_evolution_ledger
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.governance.storage import atomic_json
from partner.v2.world_model_events import atomic_world_model_matched_evaluation


def run(workspace: Path) -> dict:
    workspace = workspace.resolve()
    _, dataset_digest = frozen_dataset()
    evidence_ref = f"world_model_frozen_three_domain:sha256:{dataset_digest}"
    issue = record_issue(str(workspace), {
        "summary": "固定函数库在多领域中评估全部假设，Transformer provider 能否缩小工作集且不损失真值",
        "category": "model", "severity": "medium", "evidence": [evidence_ref],
        "instance_id": "05", "project_id": "agent_self_evolution",
    })
    if not issue.get("ok"):
        raise RuntimeError(f"issue registration failed: {issue}")
    experiment = start_experiment(str(workspace), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": "Transformer HypothesisProvider 在三个冻结领域维持 holdout/family 质量并减少假设工作集",
        "intervention": "通过 execute_candidate Event 使用 transformer_hypothesis_v1；统计评分保持不变",
        "baseline": {"event_type": "world_model_library_shadow_benchmark",
                     "provider": "library_v1", "dataset_digest": dataset_digest},
        "success_criteria": ["matched dataset", "no RMSE regression >0.02",
                             "family accuracy not worse and >=2/3", "smaller working set",
                             "both Event ledgers verify"],
        "project_id": "agent_self_evolution", "tests": [evidence_ref],
    })
    if not experiment.get("ok"):
        raise RuntimeError(f"experiment registration failed: {experiment}")
    experiment_id = experiment["experiment"]["experiment_id"]
    candidate_id = f"candidate_world_model_transformer_{experiment_id[-12:]}"
    candidate = register_candidate_skill(str(workspace), {
        "candidate_id": candidate_id, "title": "Transformer HypothesisProvider shadow v1",
        "status": "candidate", "artifact_type": "model_policy",
        "project_id": "agent_self_evolution", "experiment_id": experiment_id,
        "strategy_id": "transformer_hypothesis_v1", "source_episode_ids": [evidence_ref],
        "success_criteria": experiment["experiment"]["success_criteria"],
        "applicability": ["bounded numeric hypothesis retrieval", "shadow three-domain benchmark"],
        "non_applicability": ["production prompt mutation", "general causal world model", "safety decisions"],
        "intervention": json.dumps({"provider": "partner.cognition.world_model.TransformerHypothesisProvider",
                                     "truth_judge": "independent_bic_mdl", "production_mutation": False}),
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "world_model_transformer_shadow_benchmark",
                               "allowed_instances": ["05"], "default_params": {"seed": 20260830}},
        "evaluation_contract": {"ready": True, "kind": "event",
                                "event_type": "world_model_matched_evaluation",
                                "allowed_instances": ["05"],
                                "default_params": {"candidate_id": candidate_id, "seed": 20260830}},
        "baseline": experiment["experiment"]["baseline"],
        "rollback": "do not select transformer_hypothesis_v1 outside shadow evaluation",
    })
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    working = workspace / "instances/05/state/tasks" / f"world_model_candidate_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    task = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                           user_message="Event-first world-model matched shadow experiment")
    ctx = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                          task_instance=task)
    evaluation = atomic_world_model_matched_evaluation(ctx, {
        "candidate_id": candidate_id, "execution_id": f"exec_{experiment_id}",
        "instance_id": "05", "seed": 20260830,
    })
    comparison = dict(evaluation.get("comparison") or {})
    criteria = dict(comparison.get("criteria") or {})
    decision = decide_experiment(str(workspace), {
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "decision": "inconclusive", "evidence": list(evaluation.get("files") or []),
        "regression_passed": bool(evaluation.get("ok") and all(criteria.values())),
        "criteria_results": criteria,
        "metrics_before": comparison.get("baseline_metrics") or {},
        "metrics_after": comparison.get("candidate_metrics") or {},
        "rollback_required": False,
        "reason": "one synthetic matched benchmark supports shadow continuation, not production promotion",
        "project_id": "agent_self_evolution",
    })
    result = {"schema_version": 1, "experiment_id": experiment_id,
              "candidate_id": candidate_id, "candidate_registration": candidate,
              "evaluation": evaluation, "policy_decision": decision,
              "evolution_ledger": verify_evolution_ledger(str(workspace)),
              "production_effective": False}
    result_path = working / "world_model_candidate_experiment.json"
    atomic_json(result_path, result)
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--workspace", required=True)
    args = parser.parse_args(); result = run(Path(args.workspace))
    print(json.dumps({"experiment_id": result["experiment_id"], "candidate_id": result["candidate_id"],
                      "comparison": result["evaluation"].get("comparison"),
                      "policy_status": result["policy_decision"].get("status"),
                      "ledger": result["evolution_ledger"], "result_path": result["result_path"]},
                     ensure_ascii=False, indent=2))
    return 0 if result["evaluation"].get("ok") and result["evolution_ledger"].get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
