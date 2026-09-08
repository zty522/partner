#!/usr/bin/env python3
"""Run one real matched sklearn-vs-BDK experiment through Partner Events."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PARTNER_ROOT = Path(__file__).resolve().parents[1]
if str(PARTNER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARTNER_ROOT))

from partner.governance.candidate_skills import register_candidate_skill
from partner.governance.evolution_events import verify_evolution_ledger
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.governance.storage import atomic_json
from partner.v2.candidate_events import atomic_execute_candidate
from partner.v2.targetdiff_bdk_events import atomic_targetdiff_sklearn_baseline


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _ctx(workspace: Path, working: Path) -> Any:
    instance_workspace = workspace / "instances" / "02"
    task = SimpleNamespace(
        workspace=str(instance_workspace),
        working_dir=str(working),
        user_message="Event-first TargetDiff sklearn-vs-BDK shadow experiment",
    )
    return SimpleNamespace(
        workspace=str(instance_workspace), working_dir=str(working), task_instance=task
    )


def run(workspace: Path, *, epochs: int) -> dict[str, Any]:
    workspace = workspace.resolve()
    data = workspace / "external/targetdiff/data/affinity_info.pkl"
    if not data.is_file():
        raise FileNotFoundError(f"real TargetDiff input missing: {data}")
    data_ref = f"{data}#sha256={_sha256(data)}"
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")

    issue_result = record_issue(str(workspace), {
        "summary": "BDK FunctionPool 对 TargetDiff affinity 回归是否比匹配 sklearn baseline 更有用尚无 Event-first 证据",
        "category": "model",
        "severity": "medium",
        "evidence": [data_ref],
        "instance_id": "02",
        "project_id": "molecular_generation",
    })
    if not issue_result.get("ok"):
        raise RuntimeError(f"issue registration failed: {issue_result}")

    experiment_result = start_experiment(str(workspace), {
        "issue_id": issue_result["issue"]["issue_id"],
        "hypothesis": "在相同 group-disjoint 五折和 vina/rmsd 特征下，BDK FunctionPool 可把 mean RMSE 至少降低 0.02",
        "intervention": "通过 execute_candidate Event 调用 targetdiff_bdk_function_pool；不调整 held-out split",
        "baseline": {
            "event_type": "targetdiff_sklearn_affinity_baseline",
            "models": ["LinearRegression", "HistGradientBoostingRegressor"],
            "split": "sha256(group)%5",
        },
        "success_criteria": [
            "candidate Event execution succeeds",
            "all folds are group-disjoint",
            "BDK mean RMSE is at least 0.02 below the best sklearn mean RMSE",
            "BDK ocamms complexity check passes",
            "at least three independent matched runs exist before promotion",
        ],
        "project_id": "molecular_generation",
        "tests": [data_ref],
    })
    if not experiment_result.get("ok"):
        raise RuntimeError(f"experiment registration failed: {experiment_result}")
    experiment_id = experiment_result["experiment"]["experiment_id"]
    candidate_id = f"candidate_targetdiff_bdk_{experiment_id.removeprefix('experiment_')}"
    run_id = f"event_first_{stamp}"

    candidate_result = register_candidate_skill(str(workspace), {
        "candidate_id": candidate_id,
        "title": "TargetDiff BDK FunctionPool matched-CV Candidate",
        "status": "candidate",
        "artifact_type": "model_policy",
        "project_id": "molecular_generation",
        "experiment_id": experiment_id,
        "strategy_id": "targetdiff_bdk_function_pool_v1",
        "source_episode_ids": [data_ref],
        "success_criteria": experiment_result["experiment"]["success_criteria"],
        "applicability": ["02 TargetDiff affinity regression on vina/rmsd features"],
        "non_applicability": ["LLM reasoning", "Agent runtime", "production causal claims"],
        "intervention": json.dumps({
            "provider": "bdk.function_pool.FunctionPool",
            "kernels": ["linear", "quadratic", "fourier", "expdecay"],
            "epochs": epochs,
        }, ensure_ascii=False),
        "execution_contract": {
            "ready": True,
            "kind": "event",
            "event_type": "targetdiff_bdk_function_pool",
            "allowed_instances": ["02"],
            "default_params": {"epochs": epochs, "run_id": run_id},
        },
        "baseline": experiment_result["experiment"]["baseline"],
        "rollback": "do not add BDK to production choices",
    })

    working = workspace / "instances/02/state/tasks" / f"bdk_event_first_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    ctx = _ctx(workspace, working)
    baseline = atomic_targetdiff_sklearn_baseline(ctx, {"run_id": run_id})
    candidate = atomic_execute_candidate(ctx, {
        "candidate_id": candidate_id,
        "execution_id": f"exec_{experiment_id}",
        "instance_id": "02",
        "mode": "shadow",
        "event_params": {},
    })

    baseline_result = dict(baseline.get("result") or {})
    candidate_result_data = dict(candidate.get("result") or {})
    sklearn_best = min(
        float(baseline_result.get("mean_linear_rmse", float("inf"))),
        float(baseline_result.get("mean_hgb_rmse", float("inf"))),
    )
    bdk_rmse = float(candidate_result_data.get("mean_bdk_rmse", float("inf")))
    delta = bdk_rmse - sklearn_best
    group_disjoint = bool(candidate_result_data.get("folds")) and all(
        int(row.get("group_overlap") or 0) == 0
        for row in candidate_result_data.get("folds") or []
    )
    ledger_before_decision = verify_evolution_ledger(str(workspace))
    criteria = {
        "candidate_event_execution_succeeds": bool(candidate.get("ok")),
        "all_folds_group_disjoint": group_disjoint,
        "bdk_improves_rmse_by_at_least_0_02": delta <= -0.02,
        "bdk_ocamms_passes": bool(candidate_result_data.get("ocamms_all_passed")),
        "three_independent_runs_before_promotion": False,
    }
    # A single matched run is deliberately never sufficient for promotion.
    decision = decide_experiment(str(workspace), {
        "experiment_id": experiment_id,
        "candidate_id": candidate_id,
        "decision": "inconclusive",
        "evidence": list(baseline.get("files") or []) + list(candidate.get("files") or []),
        "regression_passed": bool(baseline.get("ok") and candidate.get("ok")
                                  and group_disjoint and ledger_before_decision.get("ok")),
        "criteria_results": criteria,
        "metrics_before": {
            "best_sklearn_mean_rmse": sklearn_best,
            "linear_mean_rmse": baseline_result.get("mean_linear_rmse"),
            "hgb_mean_rmse": baseline_result.get("mean_hgb_rmse"),
        },
        "metrics_after": {
            "bdk_mean_rmse": bdk_rmse,
            "delta_bdk_minus_sklearn_best": delta,
            "mean_kernel_probs": candidate_result_data.get("mean_kernel_probs"),
            "ocamms_all_passed": candidate_result_data.get("ocamms_all_passed"),
        },
        "rollback_required": False,
        "reason": "single real matched run; insufficient for promotion regardless of direction",
        "project_id": "molecular_generation",
    })
    ledger_after = verify_evolution_ledger(str(workspace))
    summary = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "candidate_id": candidate_id,
        "data_ref": data_ref,
        "epochs": epochs,
        "baseline": baseline,
        "candidate": candidate,
        "candidate_registration": candidate_result,
        "criteria_results": criteria,
        "metrics": {
            "sklearn_best_mean_rmse": sklearn_best,
            "bdk_mean_rmse": bdk_rmse,
            "delta_bdk_minus_sklearn_best": delta,
        },
        "decision": decision,
        "ledger_verification": ledger_after,
        "production_effective": False,
    }
    result_json = working / "event_first_bdk_experiment_result.json"
    atomic_json(result_json, summary)
    result_md = working / "event_first_bdk_experiment_report.md"
    result_md.write_text(
        "\n".join([
            "# Event-first BDK 匹配实验",
            "",
            f"- Experiment: `{experiment_id}`",
            f"- Candidate: `{candidate_id}`",
            f"- sklearn best mean RMSE: **{sklearn_best:.6f}**",
            f"- BDK mean RMSE: **{bdk_rmse:.6f}**",
            f"- Delta (BDK - sklearn): **{delta:+.6f}**",
            f"- Ocamms: **{candidate_result_data.get('ocamms_all_passed')}**",
            f"- Event ledger: **{ledger_after.get('ok')}**, events={ledger_after.get('event_count')}",
            f"- Decision: **{decision.get('status')}**",
            "",
            "单次真实匹配运行不足以晋升；本实验不改变 production policy。",
        ]) + "\n",
        encoding="utf-8",
    )
    summary["files"] = [str(result_json), str(result_md)]
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--epochs", type=int, default=200)
    args = parser.parse_args()
    result = run(Path(args.workspace), epochs=args.epochs)
    print(json.dumps({
        "experiment_id": result["experiment_id"],
        "candidate_id": result["candidate_id"],
        "metrics": result["metrics"],
        "criteria_results": result["criteria_results"],
        "decision": result["decision"].get("status"),
        "ledger_verification": result["ledger_verification"],
        "files": result["files"],
    }, ensure_ascii=False, indent=2))
    return 0 if result["ledger_verification"].get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
