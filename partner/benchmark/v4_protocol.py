"""Validation and deterministic statistics for Partner v4 benchmark suites."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping
import json
import random
import statistics

ALLOWED_TRACKS = {"project", "active_learning", "self_evolution"}
EXPECTED_ARMS = {
    "project": ["single_turn_no_memory", "full_partner"],
    "active_learning": ["no_read", "read_not_consumed", "read_and_consumed"],
    "self_evolution": ["diagnose_only", "bounded_repair"],
}


def validate_suite(value: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    episodes = value.get("episodes")
    if not isinstance(episodes, list) or len(episodes) < 2:
        return ["episodes must contain at least two rows"]
    ids = [str(row.get("episode_id") or "") for row in episodes if isinstance(row, Mapping)]
    if len(ids) != len(episodes) or not all(ids) or len(set(ids)) != len(ids):
        errors.append("episode_id must be present and unique")
    published: set[str] = set()
    for index, row in enumerate(episodes):
        if not isinstance(row, Mapping):
            errors.append(f"episode[{index}] must be an object"); continue
        public, evaluator = row.get("public"), row.get("evaluator")
        if not isinstance(public, Mapping) or not isinstance(evaluator, Mapping):
            errors.append(f"{ids[index]} requires public and evaluator objects"); continue
        track = str(public.get("track") or "")
        if track not in ALLOWED_TRACKS:
            errors.append(f"{ids[index]} unknown track {track}")
        if list(public.get("arms") or []) != EXPECTED_ARMS.get(track):
            errors.append(f"{ids[index]} arm contract mismatch")
        if any(k in public for k in ("target", "expected_output", "hidden_tests")):
            errors.append(f"{ids[index]} leaks hidden evaluator")
        inputs = public.get("input") or {}
        ref = str(inputs.get("memory_ref") or "") if isinstance(inputs, Mapping) else ""
        if ref and ref not in published:
            errors.append(f"{ids[index]} memory_ref is not produced by an earlier episode: {ref}")
        publish = public.get("publish_memory") or {}
        if publish:
            memory_id = str(publish.get("memory_id") or "")
            if not memory_id or memory_id in published:
                errors.append(f"{ids[index]} invalid or duplicate publish_memory")
            published.add(memory_id)
    budget = value.get("budget") or {}
    declared = int(budget.get("max_episodes") or 0)
    if declared and declared < len(episodes):
        errors.append("max_episodes is smaller than frozen episode count")
    version = str(value.get("benchmark_version") or "")
    if version in {"4.1", "4.2", "4.3"}:
        tracks = [str((row.get("public") or {}).get("track") or "") for row in episodes]
        roles = [str((row.get("public") or {}).get("control_role") or "positive")
                 for row in episodes]
        executors = {str((((row.get("public") or {}).get("input") or {}).get("executor_kind") or ""))
                     for row in episodes}
        if len(episodes) < 7:
            errors.append("v4.1 suite requires at least seven episodes")
        if any(tracks.count(track) < 2 for track in ALLOWED_TRACKS):
            errors.append("v4.1 suite requires at least two episodes per track")
        if "negative" not in roles:
            errors.append("v4.1 suite requires a preregistered negative control")
        if len(executors - {""}) < 3:
            errors.append("v4.1 suite requires at least three real executor families")
        for track in ALLOWED_TRACKS:
            track_rows = [row for row in episodes
                          if str((row.get("public") or {}).get("track") or "") == track]
            notified = [row for row in track_rows
                        if bool((row.get("public") or {}).get("notify_milestone"))]
            if len(notified) != 1 or notified[0] is not track_rows[-1]:
                errors.append(f"v{version} {track} must notify exactly once on its last episode")
    if version in {"4.2", "4.3"}:
        required = [str(value) for value in value.get("required_confirmed_episodes") or []]
        if not required or any(episode_id not in ids for episode_id in required):
            errors.append("v4.2 required_confirmed_episodes must name frozen episodes")
        domains = {str((((row.get("public") or {}).get("input") or {}).get("domain") or ""))
                   for row in episodes
                   if str((row.get("public") or {}).get("track") or "") == "project"}
        defect_types = {str((((row.get("public") or {}).get("input") or {}).get("defect_type") or ""))
                        for row in episodes
                        if str((row.get("public") or {}).get("track") or "") == "self_evolution"}
        if len(episodes) < 12:
            errors.append("v4.2 suite requires at least twelve episodes")
        if len(domains - {""}) < 2:
            errors.append("v4.2 suite requires at least two project domains")
        if len(defect_types - {""}) < 2:
            errors.append("v4.2 suite requires at least two natural defect types")
        multisource = [row for row in episodes if str(
            (((row.get("public") or {}).get("input") or {}).get("executor_kind") or "")) ==
            "multi_source_synthesis_v1"]
        if not multisource or any(len((((row.get("public") or {}).get("input") or {})
                                       .get("source_paths") or [])) < 2 for row in multisource):
            errors.append("v4.2 suite requires a multi-source synthesis episode")
        positions = {str(row.get("episode_id") or ""): index for index, row in enumerate(episodes)}
        sources = {}
        for row in episodes:
            publish = (row.get("public") or {}).get("publish_memory") or {}
            if publish.get("memory_id"):
                sources[str(publish["memory_id"])] = positions[str(row.get("episode_id"))]
        lags = []
        for index, row in enumerate(episodes):
            ref = str((((row.get("public") or {}).get("input") or {}).get("memory_ref") or ""))
            if ref in sources:
                lags.append(index - sources[ref])
        minimum_lag = int(value.get("minimum_memory_lag") or 6)
        if max(lags, default=0) < minimum_lag:
            errors.append(f"v{version} suite requires a settled-memory transfer lag of at least {minimum_lag}")
    if version == "4.3":
        if len(episodes) not in {20, 50}:
            errors.append("v4.3 suite must freeze exactly 20 or 50 episodes")
        holdout = value.get("holdout_policy") or {}
        source_ids = [str(item) for item in holdout.get("source_item_ids") or []]
        defect_ids = [str(item) for item in holdout.get("defect_item_ids") or []]
        development_ids = {str(item) for item in holdout.get("development_item_ids") or []}
        if len(set(source_ids)) < 2:
            errors.append("v4.3 requires at least two frozen holdout source items")
        if len(set(defect_ids)) < 2:
            errors.append("v4.3 requires at least two frozen holdout defect items")
        if development_ids & (set(source_ids) | set(defect_ids)):
            errors.append("v4.3 holdout item ids must be disjoint from development ids")
        checkpoints = [int(item) for item in value.get("retention_checkpoints") or []]
        if not checkpoints or checkpoints != sorted(set(checkpoints)):
            errors.append("v4.3 retention_checkpoints must be unique and ordered")
        if checkpoints and checkpoints[-1] < int(value.get("minimum_memory_lag") or 0):
            errors.append("v4.3 final retention checkpoint is shorter than minimum_memory_lag")
        probe_lags = []
        positions = {str(row.get("episode_id") or ""): i for i, row in enumerate(episodes)}
        source_positions = {}
        for row in episodes:
            publish = (row.get("public") or {}).get("publish_memory") or {}
            if publish.get("memory_id"):
                source_positions[str(publish["memory_id"])] = positions[str(row.get("episode_id"))]
        for index, row in enumerate(episodes):
            public = row.get("public") or {}; inputs = public.get("input") or {}
            ref = str(inputs.get("memory_ref") or "")
            if str(inputs.get("executor_kind") or "") == "typed_memory_recall_v1" and ref in source_positions:
                probe_lags.append(index - source_positions[ref])
        if not set(checkpoints).issubset(set(probe_lags)):
            errors.append("v4.3 retention checkpoints must each have a typed memory probe")
        cross_project = [row for row in episodes if str(
            (((row.get("public") or {}).get("input") or {}).get("executor_kind") or "")) ==
            "memory_guided_age_calibration_v1"]
        if not cross_project:
            errors.append("v4.3 requires a learned-memory-to-project candidate episode")
        review = value.get("review_protocol") or {}
        if int(review.get("reviewer_count") or 0) < 2 or not review.get("blind_arm_labels"):
            errors.append("v4.3 requires a frozen blinded dual-review protocol")
    return errors


def bootstrap_mean_ci(values: list[float], *, seed: int = 20260930,
                      samples: int = 2000) -> dict[str, float | int | None]:
    if not values:
        return {"mean": None, "lower_95": None, "upper_95": None, "samples": samples, "seed": seed}
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(samples))
    return {"mean": statistics.mean(values), "lower_95": means[int(samples * .025)],
            "upper_95": means[min(samples - 1, int(samples * .975))],
            "samples": samples, "seed": seed}


def summarize_records(records: list[Mapping[str, Any]], *, bootstrap_seed: int = 20260930) -> dict[str, Any]:
    effects = [float((r.get("settlement") or {}).get("effect") or 0) for r in records]
    decisions = Counter(str((r.get("settlement") or {}).get("decision") or "") for r in records)
    project = [r for r in records if (r.get("settlement") or {}).get("track") == "project"]
    learning = [r for r in records if (r.get("settlement") or {}).get("track") == "active_learning"]
    evolution = [r for r in records if (r.get("settlement") or {}).get("track") == "self_evolution"]
    def scores(row): return (row.get("settlement") or {}).get("scores") or {}
    project_baseline = [float(scores(r).get("single_turn_no_memory", 0)) for r in project]
    project_partner = [float(scores(r).get("full_partner", 0)) for r in project]
    learning_gains = [float(scores(r).get("read_and_consumed", 0)) - float(scores(r).get("read_not_consumed", 0)) for r in learning]
    defects = [r for r in evolution if float(scores(r).get("diagnose_only", 0)) < 1]
    clean = [r for r in evolution if float(scores(r).get("diagnose_only", 0)) >= 1]
    repair_at_1 = (sum(float(scores(r).get("bounded_repair", 0)) >= 1 for r in defects) / len(defects)) if defects else None
    false_promotions = sum((r.get("settlement") or {}).get("decision") == "confirmed" and
                           float(scores(r).get("bounded_repair", 0)) <= float(scores(r).get("diagnose_only", 0))
                           for r in clean)
    retention_rate = (sum(float(scores(r).get("bounded_repair", 0)) >=
                          float(scores(r).get("diagnose_only", 0)) for r in clean) / len(clean)
                      if clean else None)
    executor_groups: dict[str, list[float]] = {}
    control_results: dict[str, list[bool]] = {"positive": [], "negative": []}
    transfer_lags: list[int] = []
    retention_points: list[dict[str, Any]] = []
    holdout_results: dict[str, list[bool]] = {"source": [], "defect": []}
    cross_track_project: list[dict[str, Any]] = []
    review_agreements: list[bool] = []
    episode_positions = {str(r.get("episode_id") or ""): i for i, r in enumerate(records)}
    for row in records:
        settlement = row.get("settlement") or {}
        executor = str(row.get("executor_kind") or "unspecified")
        executor_groups.setdefault(executor, []).append(float(settlement.get("effect") or 0))
        role = str(row.get("control_role") or "positive")
        control_results.setdefault(role, []).append(
            str(settlement.get("decision") or "") == "confirmed")
        for edge in (row.get("handoff") or {}).get("causal_edges") or []:
            source = episode_positions.get(str(edge.get("source_episode_id") or ""))
            consumer = episode_positions.get(str(edge.get("consumer_episode_id") or ""))
            if source is not None and consumer is not None and consumer > source:
                transfer_lags.append(consumer - source)
                if row.get("executor_kind") == "typed_memory_recall_v1":
                    retention_points.append({"episode_id": str(row.get("episode_id") or ""),
                                             "lag": consumer - source,
                                             "retained": str(settlement.get("decision") or "") == "confirmed"})
        partition = str(row.get("holdout_kind") or "")
        if partition in holdout_results:
            holdout_results[partition].append(str(settlement.get("decision") or "") == "confirmed")
        if row.get("executor_kind") == "memory_guided_age_calibration_v1":
            cross_track_project.append({"episode_id": str(row.get("episode_id") or ""),
                                        "effect": float(settlement.get("effect") or 0),
                                        "confirmed": settlement.get("decision") == "confirmed"})
        reviews = (row.get("checkpoint") or {}).get("blind_reviews") or []
        by_arm_review: dict[str, list[bool]] = {}
        for review in reviews:
            by_arm_review.setdefault(str(review.get("arm_label") or ""), []).append(bool(review.get("pass")))
        review_agreements.extend(len(values) >= 2 and len(set(values)) == 1
                                 for values in by_arm_review.values())
    return {
        "effect_bootstrap_ci": bootstrap_mean_ci(effects, seed=bootstrap_seed),
        "longitudinal_task_utility_auc": {
            "baseline_mean": statistics.mean(project_baseline) if project_baseline else None,
            "partner_mean": statistics.mean(project_partner) if project_partner else None,
            "uplift": (statistics.mean(project_partner)-statistics.mean(project_baseline)) if project else None,
        },
        "learning_to_action_gain": bootstrap_mean_ci(learning_gains, seed=bootstrap_seed),
        "repair_at_1": repair_at_1,
        "false_promotion_rate": false_promotions / len(clean) if clean else 0.0,
        "production_retention_rate": retention_rate,
        "cost_effect_points": [
            {"episode_id": str(r.get("episode_id") or ""),
             "duration_ms": r.get("duration_ms"),
             "effect": float((r.get("settlement") or {}).get("effect") or 0)}
            for r in records
        ],
        "decision_counts": dict(decisions),
        "v41_diversity": {
            "executor_family_count": len([k for k in executor_groups if k != "unspecified"]),
            "effects_by_executor": {k: bootstrap_mean_ci(v, seed=bootstrap_seed)
                                    for k, v in sorted(executor_groups.items())},
            "positive_control_confirmation_rate": (
                sum(control_results.get("positive") or []) /
                len(control_results.get("positive") or [])
                if control_results.get("positive") else None),
            "negative_control_safe_rate": (
                sum(not value for value in (control_results.get("negative") or [])) /
                len(control_results.get("negative") or [])
                if control_results.get("negative") else None),
            "cross_episode_transfer_lags": transfer_lags,
            "max_transfer_lag": max(transfer_lags, default=None),
        },
        "v43_generalization": {
            "holdout_source_confirmation_rate": (sum(holdout_results["source"]) /
                                                    len(holdout_results["source"]) if holdout_results["source"] else None),
            "holdout_defect_repair_at_1": (sum(holdout_results["defect"]) /
                                             len(holdout_results["defect"]) if holdout_results["defect"] else None),
            "retention_curve": sorted(retention_points, key=lambda item: item["lag"]),
            "max_confirmed_retention_lag": max((item["lag"] for item in retention_points
                                                  if item["retained"]), default=None),
            "retention_checkpoint_pass_rate": (sum(item["retained"] for item in retention_points) /
                                                 len(retention_points) if retention_points else None),
            "cross_track_project": cross_track_project,
            "dual_blind_machine_review_agreement": (sum(review_agreements) / len(review_agreements)
                                                       if review_agreements else None),
            "human_review_claimed": False,
        },
    }


def load_and_validate(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_suite(value)
    if errors:
        raise ValueError("; ".join(errors))
    return value
