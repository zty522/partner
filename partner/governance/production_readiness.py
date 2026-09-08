"""Fail-closed production readiness for learned Partner candidates."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .evolution_events import verify_evolution_ledger
from .models import now_iso
from .storage import atomic_json, workspace_root


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _latest_trajectory_revisions(
        trajectories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project an append-only trajectory ledger to one current row per ID.

    Corrections deliberately append a new row instead of rewriting history.
    Readiness is a current-state assessment, so counting every revision as an
    independent sample would inflate sample sizes and retain superseded
    false-success/reward values.  Rows without an identity remain distinct and
    fail naturally at the downstream gates.
    """
    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    anonymous: list[dict[str, Any]] = []
    for position, row in enumerate(trajectories):
        identity = str(row.get("trajectory_id") or "").strip()
        if not identity:
            anonymous.append(row)
            continue
        # Ledger order is authoritative.  revision is useful audit metadata,
        # but older writers did not always increment it consistently.
        latest[identity] = (position, row)
    return anonymous + [row for _, row in sorted(latest.values())]


def _digest(payload: dict[str, Any]) -> str:
    value = {key: item for key, item in payload.items() if key != "attestation_digest"}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def assess_general_llm(experiment_paths: list[str], *, min_model_families: int = 2,
                       min_experiments_per_family: int = 1,
                       min_total_pairs: int = 6) -> dict[str, Any]:
    accepted, rejected = [], []
    for raw in experiment_paths:
        result = _read_json(raw)
        contract = dict(result.get("model_contract") or {})
        criteria = dict(result.get("criteria") or {})
        metrics = dict(result.get("metrics") or {})
        provider = str(contract.get("provider") or "")
        model = str(contract.get("model") or "")
        external = bool(provider and provider != "local" and not contract.get("local_deterministic"))
        payload_safe = all(
            not (audit or {}).get("violations")
            for pair in result.get("pairs") or []
            for audit in (pair.get("external_payload_audits") or {}).values())
        passed = bool(external and criteria and all(criteria.values()) and payload_safe
                      and float(metrics.get("mean_reward_delta") or 0) >= .10
                      and len(result.get("pairs") or []) >= 3)
        row = {"path": raw, "provider": provider, "model": model, "passed": passed,
               "task_ids": [str(pair.get("task_id") or "")
                            for pair in result.get("pairs") or []],
               "payload_safe": payload_safe,
               "mean_reward_delta": metrics.get("mean_reward_delta")}
        (accepted if passed else rejected).append(row)
    families = {(row["provider"].lower(), row["model"].lower()) for row in accepted}
    family_counts = {
        f"{provider}/{model}": sum(
            (row["provider"].lower(), row["model"].lower()) == (provider, model)
            for row in accepted)
        for provider, model in families
    }
    total_pairs = sum(len(row["task_ids"]) for row in accepted)
    task_ids = {task_id for row in accepted for task_id in row["task_ids"] if task_id}
    checks = {
        "minimum_external_model_families": len(families) >= min_model_families,
        "each_model_has_three_matched_tasks": bool(accepted) and all(row["passed"] for row in accepted),
        "all_external_payloads_pass_redaction_audit": bool(accepted) and all(row["payload_safe"] for row in accepted),
        "required_independent_experiments_per_family": bool(families) and all(
            count >= min_experiments_per_family for count in family_counts.values()),
        "at_least_six_total_matched_pairs": total_pairs >= min_total_pairs,
        "at_least_three_task_families": len(task_ids) >= 3,
    }
    return {"ok": all(checks.values()), "checks": checks, "accepted": accepted,
            "rejected": rejected,
            "model_families": [list(value) for value in sorted(families)],
            "family_experiment_counts": family_counts, "total_pairs": total_pairs,
            "task_ids": sorted(task_ids),
            "scope": ("single_authorized_general_purpose_model"
                      if min_model_families == 1 else "cross_model")}


def _artifact_valid(path: str) -> bool:
    try:
        file = Path(path)
        return file.is_file() and file.stat().st_size >= 200
    except OSError:
        return False


def assess_sustained_business(trajectories: list[dict[str, Any]], *,
                              candidate_id: str, min_rounds: int = 6) -> dict[str, Any]:
    rows = [row for row in _latest_trajectory_revisions(trajectories)
            if str((row.get("action") or {}).get("strategy_id") or "") == candidate_id]
    qualified = []
    for row in rows:
        outcome = dict(row.get("outcome") or {})
        state = dict(row.get("state") or {})
        artifacts = [str(value) for value in outcome.get("artifacts") or []]
        if (outcome.get("status") == "completed"
                and outcome.get("business_progress") is True
                and outcome.get("false_success") is not True
                and state.get("delivery_confirmed") is True
                and artifacts and all(_artifact_valid(path) for path in artifacts)):
            qualified.append(row)
    fingerprints = {str((row.get("outcome") or {}).get("outcome_fingerprint") or "")
                    for row in qualified} - {""}
    projects = {str(row.get("project_id") or "") for row in qualified} - {""}
    windows = {str(row.get("created_at") or "")[:10] for row in qualified} - {""}
    rewards = [float(row.get("reward") or 0) for row in qualified]
    checks = {
        "at_least_six_delivered_business_rounds": len(qualified) >= min_rounds,
        "unique_outcomes": len(fingerprints) >= min_rounds,
        "at_least_two_real_projects": len(projects) >= 2,
        "at_least_three_temporal_windows": len(windows) >= 3,
        "mean_verified_reward_at_least_0_70": bool(rewards) and sum(rewards) / len(rewards) >= .70,
    }
    return {"ok": all(checks.values()), "checks": checks,
            "candidate_rows": len(rows), "qualified_rounds": len(qualified),
            "unique_outcomes": len(fingerprints), "projects": sorted(projects),
            "temporal_windows": sorted(windows),
            "mean_reward": (sum(rewards) / len(rewards) if rewards else 0.0)}


def _wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (centre - margin) / denominator


def assess_longitudinal_policy_learning(trajectories: list[dict[str, Any]], *, candidate_id: str,
                           rollback_drill_passed: bool,
                           min_samples_per_arm: int = 20) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"baseline": [], "candidate": []})
    current = _latest_trajectory_revisions(trajectories)
    for row in current:
        action = dict(row.get("action") or {})
        experiment_id, arm = str(action.get("experiment_id") or ""), str(action.get("policy_arm") or "")
        if experiment_id and arm in {"baseline", "candidate"}:
            grouped[experiment_id][arm].append(row)
    eligible = []
    for experiment_id, arms in grouped.items():
        baseline, candidate = arms["baseline"], arms["candidate"]
        if not any(str((row.get("action") or {}).get("strategy_id") or "") == candidate_id
                   for row in candidate):
            continue
        br, cr = ([float(row.get("reward") or 0) for row in values]
                  for values in (baseline, candidate))
        completed = sum((row.get("outcome") or {}).get("status") == "completed"
                        and (row.get("outcome") or {}).get("false_success") is not True
                        for row in candidate)
        eligible.append({
            "experiment_id": experiment_id, "baseline_samples": len(baseline),
            "candidate_samples": len(candidate),
            "baseline_mean": sum(br) / len(br) if br else 0.0,
            "candidate_mean": sum(cr) / len(cr) if cr else 0.0,
            "candidate_success_lcb95": _wilson_lower(completed, len(candidate)),
            "candidate_false_success": sum(bool((row.get("outcome") or {}).get("false_success"))
                                           for row in candidate),
            "negative_samples": {"baseline": sum(value <= 0 for value in br),
                                 "candidate": sum(value <= 0 for value in cr)},
            "projects": sorted({str(row.get("project_id") or "") for row in baseline + candidate
                                if row.get("project_id")}),
            "windows": sorted({str(row.get("created_at") or "")[:10] for row in baseline + candidate
                               if row.get("created_at")}),
        })
    best = max(eligible, key=lambda row: min(row["baseline_samples"], row["candidate_samples"]),
               default={})
    checks = {
        "twenty_real_samples_per_arm": bool(best)
            and min(best["baseline_samples"], best["candidate_samples"]) >= min_samples_per_arm,
        "candidate_reward_gain_at_least_0_15": bool(best)
            and best["candidate_mean"] - best["baseline_mean"] >= .15,
        "candidate_success_lcb95_at_least_0_67": bool(best)
            and best["candidate_success_lcb95"] >= .67,
        "zero_candidate_false_success": bool(best) and best["candidate_false_success"] == 0,
        "negative_samples_in_both_arms": bool(best)
            and all(best["negative_samples"].get(arm, 0) > 0 for arm in ("baseline", "candidate")),
        "at_least_two_projects": bool(best) and len(best["projects"]) >= 2,
        "at_least_three_temporal_windows": bool(best) and len(best["windows"]) >= 3,
        "rollback_drill_passed": bool(rollback_drill_passed),
    }
    return {"ok": all(checks.values()), "checks": checks, "best_experiment": best,
            "experiments_considered": eligible}


def assess_production_readiness(workspace: str, *, candidate_id: str,
                                llm_experiment_paths: list[str],
                                rollback_drill_passed: bool = False,
                                single_authorized_model: bool = False,
                                write_attestation: bool = True) -> dict[str, Any]:
    root = workspace_root(workspace)
    trajectory_path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    trajectories = _read_jsonl(trajectory_path)
    from .candidate_skills import load_candidate_skills
    candidate = next((row for row in load_candidate_skills(str(root))
                      if str(row.get("candidate_id") or "") == candidate_id), {})
    strategy_id = str(candidate.get("strategy_id") or candidate_id)
    result = {
        "schema_version": 1, "candidate_id": candidate_id, "assessed_at": now_iso(),
        "general_llm": assess_general_llm(
            llm_experiment_paths,
            min_model_families=1 if single_authorized_model else 2,
            min_experiments_per_family=2 if single_authorized_model else 1),
        "sustained_business": assess_sustained_business(trajectories, candidate_id=strategy_id),
        "longitudinal_policy_learning": assess_longitudinal_policy_learning(
            trajectories, candidate_id=strategy_id, rollback_drill_passed=rollback_drill_passed),
        "evolution_ledger": verify_evolution_ledger(str(root)),
        "source_paths": {"trajectories": str(trajectory_path),
                         "llm_experiments": list(llm_experiment_paths)},
        "authorization_scope": ("single_authorized_model:minimax"
                                if single_authorized_model else "cross_model"),
        "strategy_id": strategy_id,
    }
    result["production_ready"] = bool(result["general_llm"]["ok"]
                                      and result["sustained_business"]["ok"]
                                      and result["longitudinal_policy_learning"]["ok"]
                                      and result["evolution_ledger"].get("ok") is True)
    result["decision"] = "ready_for_explicit_activation" if result["production_ready"] else "blocked"
    result["attestation_digest"] = _digest(result)
    if write_attestation:
        path = root / "share/mind/governance/experience_guided_policy/production_readiness" / f"{candidate_id}.json"
        atomic_json(path, result)
        result["path"] = str(path)
        # Sprint18 §5 task 6: write_attestation == True means the candidate just
        # became production-ready. Hook the decision_loop here so the governance
        # self-loop can drive a self_evolve branch attempt. Failures are non-fatal
        # — the candidate is already on disk; only the routing is best-effort.
        try:
            from partner.mind.decision_handoff import dispatch_to_decision_loop
            dispatch_to_decision_loop(
                root,
                instance_id="governance",
                task_state={"phase": "PRODUCTION_READINESS_WRITTEN",
                             "candidate_id": candidate_id,
                             "decision": result.get("decision")},
                candidate={"candidate_id": candidate_id,
                           "experiment_id": candidate_id,
                           "decision": "candidate_validated"},
                budget_seconds=15,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                from partner.evolution.ledger import append_event
                append_event(root, event_type="policy/auto_apply_deferred",
                              subject_id=f"canary_{candidate_id}",
                              project_id="agent_self_evolution", actor="production_readiness",
                              payload={"candidate_id": candidate_id, "error": str(exc)[:200]})
            except Exception:
                pass
    return result


def verify_readiness_attestation(path: str, *, workspace: str,
                                 candidate_id: str) -> tuple[bool, str, dict[str, Any]]:
    root = workspace_root(workspace).resolve()
    target = Path(path).resolve()
    allowed = (root / "share/mind/governance/experience_guided_policy/production_readiness").resolve()
    if allowed not in target.parents:
        return False, "readiness attestation must be inside governed workspace", {}
    value = _read_json(target)
    if not value or str(value.get("candidate_id") or "") != candidate_id:
        return False, "readiness candidate mismatch", value
    if str(value.get("attestation_digest") or "") != _digest(value):
        return False, "readiness attestation digest mismatch", value
    if value.get("production_ready") is not True:
        return False, "production readiness gates have not passed", value
    return True, "", value
