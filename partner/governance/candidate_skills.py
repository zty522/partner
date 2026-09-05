"""Governed Candidate Skill registry backed by append-only revisions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import now_iso
from .storage import append_jsonl, atomic_json, safe_id, workspace_root


STATUSES = {"candidate", "shadow", "canary", "promoted", "rejected", "retired"}


def _required_list(value: Any, name: str) -> list[str]:
    rows = [str(item).strip() for item in (value or []) if str(item).strip()]
    if not rows:
        raise ValueError(f"{name} must not be empty")
    return rows


def register_candidate_skill(workspace: str, payload: dict[str, Any]) -> dict[str, Any]:
    root = workspace_root(workspace)
    candidate_id = str(payload.get("candidate_id") or "").strip()
    if not candidate_id:
        raw = "|".join((str(payload.get("title") or ""), str(payload.get("experiment_id") or "")))
        candidate_id = "candidate_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    status = str(payload.get("status") or "candidate")
    if status not in STATUSES:
        raise ValueError(f"invalid candidate skill status: {status}")
    source_episodes = _required_list(payload.get("source_episode_ids"), "source_episode_ids")
    success_criteria = _required_list(payload.get("success_criteria"), "success_criteria")
    applicability = _required_list(payload.get("applicability"), "applicability")
    directory = root / "share/mind/governance/rl/candidate_skills"
    current_path = directory / f"{safe_id(candidate_id)}.json"
    try:
        previous = json.loads(current_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        previous = {}
    version = int(previous.get("version") or 0) + 1
    execution_contract = dict(payload.get("execution_contract") or {})
    evaluation_contract = dict(payload.get("evaluation_contract") or {})
    execution_ready = bool(
        execution_contract.get("ready")
        and execution_contract.get("kind") == "event"
        and str(execution_contract.get("event_type") or "").strip()
        and isinstance(execution_contract.get("allowed_instances"), list)
        and bool(execution_contract.get("allowed_instances"))
    )
    record = {
        "schema_version": 1, "candidate_id": candidate_id, "version": version,
        "title": str(payload.get("title") or candidate_id), "status": status,
        "artifact_type": str(payload.get("artifact_type") or "legacy_skill"),
        "project_id": str(payload.get("project_id") or ""),
        "source_campaign_id": str(payload.get("source_campaign_id") or ""),
        "experiment_id": str(payload.get("experiment_id") or ""),
        "strategy_id": str(payload.get("strategy_id") or candidate_id),
        "source_episode_ids": sorted(set(source_episodes)),
        "failure_classes": sorted(set(str(value) for value in payload.get("failure_classes") or [] if str(value))),
        "applicability": applicability,
        "non_applicability": [str(value) for value in payload.get("non_applicability") or [] if str(value)],
        "counterexamples": [str(value) for value in payload.get("counterexamples") or [] if str(value)],
        "baseline": dict(payload.get("baseline") or {}),
        "intervention": str(payload.get("intervention") or ""),
        "execution_contract": execution_contract,
        "execution_ready": execution_ready,
        "evaluation_contract": evaluation_contract,
        "evaluation_ready": bool(
            evaluation_contract.get("ready")
            and evaluation_contract.get("kind") == "event"
            and str(evaluation_contract.get("event_type") or "").strip()
            and isinstance(evaluation_contract.get("allowed_instances"), list)
            and bool(evaluation_contract.get("allowed_instances"))
        ),
        "production_readiness_contract": dict(payload.get("production_readiness_contract") or {}),
        "success_criteria": success_criteria,
        "shadow_evidence": dict(payload.get("shadow_evidence") or {}),
        "promotion_decision_id": str(payload.get("promotion_decision_id") or ""),
        "rollback": str(payload.get("rollback") or "do not apply candidate"),
        "production_effective": status == "promoted" and bool(payload.get("promotion_decision_id")),
        "created_at": str(previous.get("created_at") or now_iso()), "updated_at": now_iso(),
    }
    # A status label alone can never activate production.
    if status == "promoted" and not record["promotion_decision_id"]:
        raise ValueError("promoted candidate requires promotion_decision_id")
    atomic_json(current_path, record)
    append_jsonl(directory / "revisions.jsonl", record)
    # Event-first: the registry file is the Candidate artifact; this event is
    # the authoritative statement that a proposal occurred.
    from .evolution_events import append_evolution_event
    event = append_evolution_event(
        workspace,
        "candidate/proposed",
        subject_id=candidate_id,
        project_id=record["project_id"],
        payload={
            "candidate_id": candidate_id,
            "version": version,
            "artifact_type": record["artifact_type"],
            "status": status,
            "execution_ready": execution_ready,
            "artifact_path": str(current_path),
        },
        evidence_refs=record["source_episode_ids"],
        idempotency_key=f"candidate-proposed:{candidate_id}:v{version}",
    )
    return {"ok": True, "status": status, "candidate": record,
            "path": str(current_path), "event": event}


def load_candidate_skills(workspace: str) -> list[dict[str, Any]]:
    root = workspace_root(workspace) / "share/mind/governance/rl/candidate_skills"
    rows: list[dict[str, Any]] = []
    # Hermes 2026-08-27 fix: glob all candidate-skill files, not just the
    # `candidate_*.json` pattern. The directory holds one file per
    # `register_candidate_skill` call keyed by `safe_id(candidate_id)`,
    # and that prefix is not guaranteed to start with `candidate_`.
    # The previous pattern silently dropped every non-default id
    # (e.g. caller-supplied `candidate_id="manual_stable_truth_audit_v2"`),
    # which manifested as 0 matches even after a successful registration.
    # Real-world verification: registering `my-custom-candidate-id`
    # writes `my-custom-candidate-id.json` to disk, but the old glob
    # returned only the Codex 8/27 entries (both starting with `candidate_`).
    # `revisions.jsonl` is the canonical append-only audit log and is read
    # separately; this glob is for loading the latest active record.
    for path in sorted(root.glob("*.json")):
        if path.name == "revisions.jsonl":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def activate_promoted_candidate(
    workspace: str,
    *,
    candidate_id: str,
    decision_key: str,
    policy_event_id: str,
    readiness_attestation_path: str = "",
) -> dict[str, Any]:
    """Make a promoted Candidate production-effective through an Event audit.

    PromotionDecision and activation are separate on purpose: the former says
    the evidence passed; this function performs the reversible control-policy
    mutation and synchronizes the Candidate projection.
    """
    from .candidate_execution import validate_execution_contract
    from .evolution_events import append_evolution_event, load_evolution_events

    candidate = next(
        (row for row in load_candidate_skills(workspace)
         if str(row.get("candidate_id") or "") == str(candidate_id)), None)
    if candidate is None:
        return {"ok": False, "status": "candidate_not_found"}
    ready, reason = validate_execution_contract(candidate.get("execution_contract"))
    if not ready:
        return {"ok": False, "status": "candidate_not_executable", "error": reason}
    policy_event = next(
        (row for row in load_evolution_events(workspace)
         if row.get("event_id") == policy_event_id), None)
    if not policy_event or policy_event.get("event_type") != "policy/promoted":
        return {"ok": False, "status": "promotion_event_not_found"}
    if str(policy_event.get("subject_id") or "") != str(candidate_id):
        return {"ok": False, "status": "promotion_candidate_mismatch"}
    readiness_contract = dict(candidate.get("production_readiness_contract") or {})
    learned_artifact = str(candidate.get("artifact_type") or "") in {
        "event_context_policy", "learned_policy", "offline_rl_policy",
    }
    if readiness_contract.get("required") is True or learned_artifact:
        from .production_readiness import verify_readiness_attestation
        ready, error, attestation = verify_readiness_attestation(
            readiness_attestation_path, workspace=workspace, candidate_id=str(candidate_id))
        if not ready:
            return {"ok": False, "status": "production_readiness_blocked", "error": error,
                    "readiness": attestation}

    root = workspace_root(workspace)
    control_path = root / "share/mind/governance/rl/control_policy.json"
    try:
        control = json.loads(control_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        control = {"schema_version": 1, "promoted": {}}
    control.setdefault("promoted", {})[str(decision_key)] = str(candidate_id)
    control["updated_at"] = now_iso()
    atomic_json(control_path, control)

    path = root / "share/mind/governance/rl/candidate_skills" / f"{safe_id(candidate_id)}.json"
    candidate["version"] = int(candidate.get("version") or 0) + 1
    candidate["status"] = "promoted"
    candidate["promotion_decision_id"] = str(policy_event_id)
    candidate["production_effective"] = True
    candidate["updated_at"] = now_iso()
    atomic_json(path, candidate)
    append_jsonl(path.parent / "revisions.jsonl", candidate)
    event = append_evolution_event(
        workspace, "policy/activated", subject_id=str(candidate_id),
        project_id=str(candidate.get("project_id") or ""), parents=[str(policy_event_id)],
        payload={"candidate_id": candidate_id, "decision_key": decision_key,
                 "production_effective": True, "control_policy_path": str(control_path)},
        evidence_refs=[str(path), str(control_path), *([readiness_attestation_path]
                      if readiness_attestation_path else [])],
        idempotency_key=f"policy-activated:{decision_key}:{candidate_id}:{policy_event_id}",
    )
    return {"ok": True, "status": "activated", "candidate": candidate,
            "event": event, "control_policy_path": str(control_path)}


def project_candidate_decision(
    workspace: str,
    candidate_id: str,
    *,
    experiment_id: str,
    decision: str,
    criteria_results: dict[str, Any],
    metrics_before: dict[str, Any],
    metrics_after: dict[str, Any],
    policy_event_id: str,
) -> dict[str, Any] | None:
    """Refresh the Candidate JSON projection from an authoritative Policy Event."""
    root = workspace_root(workspace) / "share/mind/governance/rl/candidate_skills"
    path = root / f"{safe_id(candidate_id)}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    record["version"] = int(record.get("version") or 0) + 1
    if decision == "rejected":
        record["status"] = "rejected"
    record["shadow_evidence"] = {
        "experiment_id": experiment_id,
        "decision": decision,
        "criteria_results": dict(criteria_results),
        "metrics_before": dict(metrics_before),
        "metrics_after": dict(metrics_after),
        "policy_event_id": policy_event_id,
    }
    record["updated_at"] = now_iso()
    atomic_json(path, record)
    append_jsonl(root / "revisions.jsonl", record)
# self_evolve_annotation: candidate_id=repair_to_pr_085e844e4714171e failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_1da77208f413327a failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_30979bf714da3a39 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_36ade5e3304ad5b4 failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_40b511d1712b6a5b failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_42c390a197cc5f67 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_4d90a8e1dd99e274 failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_69603a654db448d4 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_79426daf6ea3705 failure_class=tool.molecular_diversity_benchmark.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_801ae260bc8cea9f failure_class=tool.atomic_write_artifact.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_95c2b09b3bccbfaf failure_class=runtime.timeout intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_b17a07394633c3a9 failure_class=tool.atomic_write_artifact.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_f7923034ac33acdc failure_class=tool.extract.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_10ebfabea00260c4 failure_class=tool.atomic_http_get.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_129104a43a95216f failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_1d5ec10869634030 failure_class=tool.molecular_diversity_benchmark.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_24b167277d4d31f4 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_25f739141263917c failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
    return record
