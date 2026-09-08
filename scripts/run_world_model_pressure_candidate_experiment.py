#!/usr/bin/env python3
"""Run the Event-first pressure + real-Episode Candidate experiment."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from partner.cognition.world_model.stress_benchmark import stress_dataset
from partner.governance.candidate_skills import register_candidate_skill
from partner.governance.evolution_events import verify_evolution_ledger
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.governance.storage import atomic_json
from partner.v2.world_model_events import atomic_world_model_pressure_evaluation


def run(workspace: Path, *, seeds: int = 10) -> dict:
    workspace = workspace.resolve()
    _, dataset_digest = stress_dataset(seeds=seeds)
    episode_root = workspace / "share/mind/governance/episodes"
    issue = record_issue(str(workspace), {
        "summary": "三领域演示未覆盖稀疏、噪声、离群点、分布外参数与真实 Episode 轨迹",
        "category": "model", "severity": "high",
        "evidence": [f"world_model_stress:sha256:{dataset_digest}", str(episode_root)],
        "instance_id": "05", "project_id": "agent_self_evolution",
    })
    if not issue.get("ok"):
        raise RuntimeError(f"issue registration failed: {issue}")
    experiment = start_experiment(str(workspace), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": "带证据退让的 Transformer provider 可在压力集与真实 Episode 只读轨迹上缩小工作集且不损失预测质量",
        "intervention": "稀疏或高 surprise 时扩大候选族；结构网格覆盖训练值之外；证据不足必须显式标记",
        "baseline": {"event_type": "world_model_library_pressure_bundle",
                     "provider": "library_v1", "dataset_digest": dataset_digest,
                     "evaluator_id": "robust_bic_mdl_v2",
                     "episodes_root": str(episode_root)},
        "success_criteria": ["matched pressure dataset", "proposal recall >=0.95",
                             "sparse abstention =1", "no material mean/P90 regression",
                             "real Episode read-only match", "smaller working set"],
        "project_id": "agent_self_evolution",
        "tests": [f"world_model_stress:sha256:{dataset_digest}"],
    })
    if not experiment.get("ok"):
        raise RuntimeError(f"experiment registration failed: {experiment}")
    experiment_id = experiment["experiment"]["experiment_id"]
    candidate_id = f"candidate_world_model_pressure_{experiment_id[-12:]}"
    registration = register_candidate_skill(str(workspace), {
        "candidate_id": candidate_id, "title": "Transformer hypothesis pressure bundle v2",
        "status": "candidate", "artifact_type": "model_policy",
        "project_id": "agent_self_evolution", "experiment_id": experiment_id,
        "strategy_id": "transformer_hypothesis_v2_evidence_backoff",
        "source_episode_ids": [f"world_model_stress:sha256:{dataset_digest}"],
        "success_criteria": experiment["experiment"]["success_criteria"],
        "applicability": ["bounded numeric hypothesis retrieval", "read-only reward trajectory shadow"],
        "non_applicability": ["production prompt mutation", "task truth inference",
                              "causal claims from reward curves", "autonomous policy promotion"],
        "intervention": json.dumps({"provider": "TransformerHypothesisProvider",
                                     "backoff": "sparse_or_surprising_broad_search",
                                     "production_mutation": False}),
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "world_model_transformer_pressure_bundle",
                               "allowed_instances": ["05"],
                               "default_params": {"seeds": seeds,
                                                  "episodes_root": str(episode_root)}},
        "evaluation_contract": {"ready": True, "kind": "event",
                                "event_type": "world_model_pressure_evaluation",
                                "allowed_instances": ["05"],
                                "default_params": {"candidate_id": candidate_id, "seeds": seeds,
                                                   "episodes_root": str(episode_root)}},
        "baseline": experiment["experiment"]["baseline"],
        "rollback": "leave provider unselected; Candidate is shadow-only",
    })
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    working = workspace / "instances/05/state/tasks" / f"world_model_pressure_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    task = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                           user_message="Event-first world-model pressure experiment")
    ctx = SimpleNamespace(workspace=str(workspace / "instances/05"), working_dir=str(working),
                          task_instance=task)
    evaluation = atomic_world_model_pressure_evaluation(ctx, {
        "candidate_id": candidate_id, "execution_id": f"exec_{experiment_id}",
        "instance_id": "05", "seeds": seeds, "episodes_root": str(episode_root),
    })
    decision = decide_experiment(str(workspace), {
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "decision": "inconclusive", "evidence": list(evaluation.get("files") or []),
        "regression_passed": bool(evaluation.get("ok") and
                                  all((evaluation.get("criteria") or {}).values())),
        "criteria_results": evaluation.get("criteria") or {},
        "metrics_before": ((evaluation.get("stress_comparison") or {}).get("baseline_metrics") or {}),
        "metrics_after": ((evaluation.get("stress_comparison") or {}).get("candidate_metrics") or {}),
        "rollback_required": False,
        "reason": "pressure and historical shadows justify more Candidate trials, not production promotion",
        "project_id": "agent_self_evolution",
    })
    result = {"schema_version": 1, "experiment_id": experiment_id,
              "candidate_id": candidate_id, "candidate_registration": registration,
              "evaluation": evaluation, "policy_decision": decision,
              "evolution_ledger": verify_evolution_ledger(str(workspace)),
              "production_effective": False}
    result_path = working / "world_model_pressure_candidate_experiment.json"
    atomic_json(result_path, result)
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--seeds", type=int, default=10)
    args = parser.parse_args()
    result = run(Path(args.workspace), seeds=args.seeds)
    print(json.dumps({"experiment_id": result["experiment_id"],
                      "candidate_id": result["candidate_id"],
                      "evaluation_decision": result["evaluation"].get("decision"),
                      "criteria": result["evaluation"].get("criteria"),
                      "policy_status": result["policy_decision"].get("status"),
                      "ledger": result["evolution_ledger"],
                      "result_path": result["result_path"]}, ensure_ascii=False, indent=2))
    return 0 if result["evaluation"].get("ok") and result["evolution_ledger"].get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
