"""Event-first shadow benchmark handlers for Partner-owned world-model providers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from partner.cognition.world_model.benchmark import compare_benchmarks, run_shadow_benchmark
from partner.cognition.world_model.episode_shadow import compare_episode_shadows, run_episode_shadow
from partner.cognition.world_model.stress_benchmark import (
    compare_stress_benchmarks,
    run_stress_benchmark,
)
from partner.governance.candidate_execution import execute_candidate
from partner.governance.storage import instance_id, workspace_root


def _working(ctx: Any) -> Path:
    root = workspace_root(str(getattr(ctx, "workspace", "")))
    task = getattr(ctx, "task_instance", None)
    value = str(getattr(task, "working_dir", "") if task else "") or str(getattr(ctx, "working_dir", "") or "")
    return (Path(value) if value and value != "." else root) / "world_model_shadow"


def _run(ctx: Any, params: dict[str, Any], provider_kind: str) -> dict[str, Any]:
    try:
        result = run_shadow_benchmark(provider_kind=provider_kind, output_dir=_working(ctx),
                                      seed=int(params.get("seed") or 20260830))
        files = [result["output_path"], result["event_ledger"]["path"]]
        return {"ok": True, "status": "completed", "provider_kind": provider_kind,
                "result": result, "files": files,
                "summary": (f"{provider_kind} world-model shadow: "
                            f"rmse={result['metrics']['mean_holdout_rmse']:.6f}, "
                            f"family_accuracy={result['metrics']['family_accuracy']:.3f}"),
                "production_mutation": False}
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        return {"ok": False, "status": "world_model_shadow_failed", "error": str(exc),
                "retryable": False, "production_mutation": False}


def atomic_world_model_library_shadow(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _run(ctx, params, "library")


def atomic_world_model_transformer_shadow(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _run(ctx, params, "transformer")


def _pressure_bundle(ctx: Any, params: dict[str, Any], provider_kind: str) -> dict[str, Any]:
    try:
        working = _working(ctx) / "pressure_bundle"
        workspace = workspace_root(str(getattr(ctx, "workspace", "")))
        episodes_root = Path(str(params.get("episodes_root") or
                                 (workspace / "share/mind/governance/episodes")))
        stress = run_stress_benchmark(
            provider_kind=provider_kind, output_dir=working,
            seeds=int(params.get("seeds") or 10),
        )
        episodes = run_episode_shadow(
            provider_kind=provider_kind, episodes_root=episodes_root, output_dir=working,
        )
        return {
            "ok": True, "status": "completed", "provider_kind": provider_kind,
            "stress": stress, "episodes": episodes,
            "files": [stress["output_path"], stress["event_ledger"]["path"],
                      episodes["output_path"]],
            "summary": (f"{provider_kind} pressure bundle: "
                        f"stress_rmse={stress['metrics']['mean_holdout_rmse']:.6f}, "
                        f"real_episode_groups={episodes['metrics']['eligible_instance_count']}"),
            "production_mutation": False,
        }
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        return {"ok": False, "status": "world_model_pressure_failed", "error": str(exc),
                "retryable": False, "production_mutation": False}


def atomic_world_model_library_pressure_bundle(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _pressure_bundle(ctx, params, "library")


def atomic_world_model_transformer_pressure_bundle(ctx: Any,
                                                   params: dict[str, Any]) -> dict[str, Any]:
    return _pressure_bundle(ctx, params, "transformer")


CANDIDATE_HANDLERS = {
    "world_model_transformer_shadow_benchmark": atomic_world_model_transformer_shadow,
    "world_model_transformer_pressure_bundle": atomic_world_model_transformer_pressure_bundle,
}


def atomic_world_model_matched_evaluation(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(params.get("candidate_id") or "").strip()
    execution_id = str(params.get("execution_id") or "").strip()
    if not candidate_id or not execution_id:
        return {"ok": False, "status": "invalid_matched_evaluation",
                "error": "candidate_id and execution_id are required", "retryable": False}
    baseline = atomic_world_model_library_shadow(ctx, params)
    workspace = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    candidate = execute_candidate(
        workspace, candidate_id, ctx=ctx, handlers=CANDIDATE_HANDLERS,
        params={"seed": int(params.get("seed") or 20260830)},
        instance_id=str(params.get("instance_id") or instance_id(str(getattr(ctx, "workspace", "")))),
        execution_id=execution_id, mode="shadow",
    )
    if not baseline.get("ok") or not candidate.get("ok"):
        return {"ok": False, "status": "matched_evaluation_failed",
                "baseline": baseline, "candidate": candidate, "retryable": False}
    comparison = compare_benchmarks(baseline["result"], candidate["result"])
    return {"ok": True, "status": "completed", "comparison": comparison,
            "baseline": baseline["result"], "candidate": candidate["result"],
            "files": list(baseline.get("files") or []) + list(candidate.get("files") or []),
            "summary": f"world-model matched evaluation: {comparison['decision']}",
            "production_mutation": False}


def atomic_world_model_pressure_evaluation(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(params.get("candidate_id") or "").strip()
    execution_id = str(params.get("execution_id") or "").strip()
    if not candidate_id or not execution_id:
        return {"ok": False, "status": "invalid_pressure_evaluation",
                "error": "candidate_id and execution_id are required", "retryable": False}
    baseline = atomic_world_model_library_pressure_bundle(ctx, params)
    workspace = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    candidate = execute_candidate(
        workspace, candidate_id, ctx=ctx, handlers=CANDIDATE_HANDLERS,
        params={"seeds": int(params.get("seeds") or 10),
                "episodes_root": params.get("episodes_root")},
        instance_id=str(params.get("instance_id") or instance_id(str(getattr(ctx, "workspace", "")))),
        execution_id=execution_id, mode="shadow",
    )
    if not baseline.get("ok") or not candidate.get("ok"):
        return {"ok": False, "status": "pressure_evaluation_failed",
                "baseline": baseline, "candidate": candidate, "retryable": False}
    stress = compare_stress_benchmarks(baseline["stress"], candidate["stress"])
    episodes = compare_episode_shadows(baseline["episodes"], candidate["episodes"])
    criteria = {f"stress/{key}": value for key, value in stress["criteria"].items()}
    criteria.update({f"episode/{key}": value for key, value in episodes["criteria"].items()})
    decision = "candidate_wins" if all(criteria.values()) else "inconclusive"
    return {
        "ok": True, "status": "completed", "decision": decision,
        "criteria": criteria, "stress_comparison": stress,
        "episode_comparison": episodes,
        "baseline": {"stress": baseline["stress"], "episodes": baseline["episodes"]},
        "candidate": {"stress": candidate["stress"], "episodes": candidate["episodes"]},
        "files": list(baseline.get("files") or []) + list(candidate.get("files") or []),
        "summary": f"world-model pressure evaluation: {decision}",
        "production_mutation": False,
    }


HANDLERS = {
    "world_model_library_shadow_benchmark": atomic_world_model_library_shadow,
    "world_model_transformer_shadow_benchmark": atomic_world_model_transformer_shadow,
    "world_model_matched_evaluation": atomic_world_model_matched_evaluation,
    "world_model_library_pressure_bundle": atomic_world_model_library_pressure_bundle,
    "world_model_transformer_pressure_bundle": atomic_world_model_transformer_pressure_bundle,
    "world_model_pressure_evaluation": atomic_world_model_pressure_evaluation,
}
