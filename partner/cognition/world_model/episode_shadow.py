"""Read-only projection of real Partner Episodes into learning-curve shadows.

The adapter does not claim that an agent Episode is a mathematical function.
It only tests the narrow question: can a provider propose compact hypotheses
for an instance's observed reward trajectory without changing any Episode or
production policy?
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .engine import WorldModelEngine
from .providers import LibraryHypothesisProvider, TransformerHypothesisProvider


def load_episode_trajectories(episodes_root: str | Path) -> tuple[list[dict[str, Any]], str]:
    root = Path(episodes_root)
    episodes = []
    evidence = []
    for path in sorted(root.glob("*/state.json")):
        try:
            raw = path.read_bytes()
            state = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            continue
        reward = state.get("reward_vector") or {}
        scalar = reward.get("scalar")
        if not isinstance(scalar, (int, float)):
            continue
        evidence.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
        episodes.append({
            "episode_id": str(state.get("episode_id") or path.parent.name),
            "instance_id": str(state.get("instance_id") or "unknown"),
            "project_id": str(state.get("project_id") or "unassigned"),
            "reduced_at": str(state.get("reduced_at") or ""),
            "status": str(state.get("status") or "unknown"),
            "reward": float(scalar),
            "hard_gate_passed": bool(reward.get("hard_gate_passed")),
            "policy_eligible": bool(reward.get("policy_eligible")),
            "tool_call_count": len(state.get("tool_calls") or []),
            "model_call_count": len(state.get("model_calls") or []),
        })
    episodes.sort(key=lambda row: (row["instance_id"], row["reduced_at"], row["episode_id"]))
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    groups = []
    for instance in sorted({row["instance_id"] for row in episodes}):
        rows = [row for row in episodes if row["instance_id"] == instance]
        rewards = [row["reward"] for row in rows]
        groups.append({
            "instance_id": instance, "episodes": rows, "count": len(rows),
            "unique_reward_count": len(set(rewards)),
            "zero_reward_rate": float(np.mean([value == 0.0 for value in rewards])),
            "completed_rate": float(np.mean([row["status"] == "completed" for row in rows])),
            "truth_safety_gate_without_completion_count": sum(
                row["hard_gate_passed"] and row["status"] != "completed" for row in rows),
            "curve_shadow_eligible": len(rows) >= 12 and len(set(rewards)) >= 3,
        })
    return groups, digest


def run_episode_shadow(*, provider_kind: str, episodes_root: str | Path,
                       output_dir: str | Path) -> dict[str, Any]:
    if provider_kind == "library":
        provider = LibraryHypothesisProvider("advanced")
        training = {"kind": "none"}
    elif provider_kind == "transformer":
        provider = TransformerHypothesisProvider(seed=17)
        training = provider.fit_synthetic()
    else:
        raise ValueError("provider_kind must be library or transformer")
    groups, evidence_digest = load_episode_trajectories(episodes_root)
    engine = WorldModelEngine(provider=provider)
    evaluated = []
    for group in groups:
        if not group["curve_shadow_eligible"]:
            continue
        rows = group["episodes"]
        split = max(8, int(len(rows) * 0.8))
        if split >= len(rows):
            continue
        train_x = np.arange(split, dtype=float)
        train_y = np.asarray([row["reward"] for row in rows[:split]], dtype=float)
        test_x = np.arange(split, len(rows), dtype=float)
        test_y = np.asarray([row["reward"] for row in rows[split:]], dtype=float)
        result = engine.observe(train_x, train_y, domain_id=f"episode_instance_{group['instance_id']}")
        prediction = engine.predict(result, test_x)
        evaluated.append({
            "instance_id": group["instance_id"], "train_count": split,
            "holdout_count": len(test_y),
            "holdout_rmse": float(np.sqrt(np.mean((prediction - test_y) ** 2))),
            "top_hypothesis": result["top_hypothesis"]["hypothesis_id"],
            "evidence_status": result["evidence_status"],
            "hypotheses_considered": result["hypotheses_considered"],
            "scope_warning": "descriptive reward trajectory only; never infer task truth or causality",
        })
    metrics = {
        "episode_count": sum(group["count"] for group in groups),
        "eligible_instance_count": len(evaluated),
        "mean_holdout_rmse": (float(np.mean([row["holdout_rmse"] for row in evaluated]))
                              if evaluated else None),
        "mean_hypotheses_considered": (
            float(np.mean([row["hypotheses_considered"] for row in evaluated]))
            if evaluated else None),
        "truth_safety_gate_without_completion_count": sum(
            group["truth_safety_gate_without_completion_count"] for group in groups),
        "gate_semantics": "hard_gate_passed means truth+safety only; policy_eligible also needs progress",
    }
    payload = {
        "schema_version": 1, "provider_kind": provider_kind,
        "provider_id": provider.provider_id, "evidence_digest": evidence_digest,
        "evaluator_id": "robust_bic_mdl_v2",
        "episodes_root": str(Path(episodes_root)), "training": training,
        "groups": groups, "evaluated": evaluated, "metrics": metrics,
        "production_mutation": False,
    }
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    output = target / f"{provider_kind}_episode_shadow.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["output_path"] = str(output)
    return payload


def compare_episode_shadows(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if baseline["evidence_digest"] != candidate["evidence_digest"]:
        raise ValueError("baseline/candidate Episode evidence mismatch")
    if baseline.get("evaluator_id") != candidate.get("evaluator_id"):
        raise ValueError("baseline/candidate Episode evaluator mismatch")
    before, after = baseline["metrics"], candidate["metrics"]
    has_evidence = bool(before["eligible_instance_count"] and after["eligible_instance_count"])
    criteria = {
        "matched_read_only_evidence": True,
        "matched_evaluator": True,
        "has_eligible_real_trajectory": has_evidence,
        "no_material_rmse_regression": bool(
            has_evidence and after["mean_holdout_rmse"] <= before["mean_holdout_rmse"] + 0.05),
        "smaller_working_set": bool(
            has_evidence and after["mean_hypotheses_considered"] <
            before["mean_hypotheses_considered"]),
        "production_untouched": (not baseline["production_mutation"] and
                                 not candidate["production_mutation"]),
    }
    return {"decision": "candidate_wins" if all(criteria.values()) else "inconclusive",
            "criteria": criteria, "baseline_metrics": before,
            "candidate_metrics": after, "evidence_digest": baseline["evidence_digest"]}
