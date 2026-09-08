"""Reversible, fail-closed production canaries for learned Event Candidates.

This is deliberately separate from ``control_policy.promoted``.  A canary may
collect real production evidence while the full longitudinal readiness gate is
still blocked; it never changes the Candidate projection to
``production_effective=true`` and it cannot be mistaken for promotion.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .candidate_execution import load_candidate, validate_execution_contract
from .evolution_events import append_evolution_event
from .models import now_iso
from .storage import atomic_json, workspace_root


CANARY_KEY = "literature_github_learning:research_adoption_context"


def _control_path(workspace: str) -> Path:
    return workspace_root(workspace) / "share/mind/governance/experience_guided_policy/control_policy.json"


def _load_control(workspace: str) -> dict[str, Any]:
    try:
        value = json.loads(_control_path(workspace).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        value = {"schema_version": 1, "promoted": {}}
    return value if isinstance(value, dict) else {"schema_version": 1, "promoted": {}}


def authorize_production_canary(
    workspace: str, *, candidate_id: str, readiness_attestation_path: str,
    authorization_ref: str, max_accepted_tasks: int = 12,
    duration_hours: int = 168, allowed_instances: list[str] | None = None,
) -> dict[str, Any]:
    """Authorize bounded real-traffic sampling without claiming promotion."""
    candidate = load_candidate(workspace, candidate_id)
    ready, reason = validate_execution_contract((candidate or {}).get("execution_contract"))
    if not candidate or not ready:
        return {"ok": False, "status": "candidate_not_executable", "error": reason}
    try:
        target = Path(readiness_attestation_path).resolve()
        allowed = (workspace_root(workspace)
                   / "share/mind/governance/experience_guided_policy/production_readiness").resolve()
        if allowed not in target.parents:
            raise ValueError("attestation outside governed directory")
        attestation = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {"ok": False, "status": "readiness_attestation_missing"}
    from .production_readiness import _digest as readiness_digest
    general = dict(attestation.get("general_llm") or {})
    ledger = dict(attestation.get("evolution_ledger") or {})
    if (str(attestation.get("candidate_id") or "") != candidate_id
            or str(attestation.get("attestation_digest") or "") != readiness_digest(attestation)
            or general.get("ok") is not True or ledger.get("ok") is not True):
        return {"ok": False, "status": "canary_prerequisites_blocked",
                "required": ["general_llm", "evolution_ledger"]}
    allowed = sorted(set(str(v) for v in (allowed_instances or ["04"])))
    contract_allowed = set(str(v) for v in
                           (candidate.get("execution_contract") or {}).get("allowed_instances") or [])
    if not allowed or not set(allowed) <= contract_allowed:
        return {"ok": False, "status": "canary_instance_scope_invalid"}
    now = datetime.now(timezone.utc).astimezone()
    canary_id = "canary_" + hashlib.sha256(
        f"{candidate_id}|{authorization_ref}|{now.isoformat()}".encode()
    ).hexdigest()[:16]
    record = {
        "schema_version": 1, "canary_id": canary_id, "status": "active",
        "decision_key": CANARY_KEY, "candidate_id": candidate_id,
        "strategy_id": str(candidate.get("strategy_id") or candidate_id),
        "event_type": str((candidate.get("execution_contract") or {}).get("event_type") or ""),
        "research_project_id": str(((candidate.get("execution_contract") or {})
                                    .get("default_params") or {}).get("research_project_id") or ""),
        "allowed_instances": allowed,
        "intent_terms": ["研究", "文献", "github", "源码", "证据", "harness", "对比"],
        "requires_existing_source_paths": 2,
        "max_accepted_tasks": max(1, min(int(max_accepted_tasks), 50)),
        "accepted_tasks": 0, "failed_tasks": 0,
        "automatic_rollback": {"consecutive_failures": 2, "false_success": 1,
                               "truth_gate_failures": 1},
        "authorized_at": now_iso(),
        "expires_at": (now + timedelta(hours=max(1, min(int(duration_hours), 24 * 30)))).isoformat(),
        "authorization_ref": str(authorization_ref),
        "readiness_attestation_path": str(Path(readiness_attestation_path).resolve()),
        "full_production_ready": False,
        "production_route_effective": True,
        "candidate_projection_production_effective": False,
    }
    control = _load_control(workspace)
    control.setdefault("canaries", {})[CANARY_KEY] = record
    control["updated_at"] = now_iso()
    atomic_json(_control_path(workspace), control)
    event = append_evolution_event(
        workspace, "policy/canary_activated", subject_id=candidate_id,
        project_id="literature_github_learning",
        payload={key: record[key] for key in (
            "canary_id", "decision_key", "candidate_id", "allowed_instances",
            "max_accepted_tasks", "expires_at", "production_route_effective",
            "candidate_projection_production_effective")},
        evidence_refs=[readiness_attestation_path],
        idempotency_key=f"policy-canary-activated:{canary_id}",
    )
    return {"ok": True, "status": "canary_activated", "canary": record,
            "event": event, "control_policy_path": str(_control_path(workspace))}


def resolve_production_canary(workspace: str, *, instance_id: str,
                              user_message: str) -> dict[str, Any]:
    """Return an applicable canary route; never silently widens its scope."""
    record = dict((_load_control(workspace).get("canaries") or {}).get(CANARY_KEY) or {})
    if record.get("status") != "active" or instance_id not in record.get("allowed_instances", []):
        return {"active": False, "reason": "inactive_or_instance_out_of_scope"}
    try:
        expired = datetime.fromisoformat(str(record.get("expires_at"))) <= datetime.now(timezone.utc).astimezone()
    except (TypeError, ValueError):
        expired = True
    if expired or int(record.get("accepted_tasks") or 0) >= int(record.get("max_accepted_tasks") or 0):
        return {"active": False, "reason": "expired_or_budget_exhausted"}
    lower = str(user_message or "").lower()
    if not any(term.lower() in lower for term in record.get("intent_terms") or []):
        return {"active": False, "reason": "intent_out_of_scope"}
    return {"active": True, **record, "policy_arm": "production",
            "route": "research_adoption_production_canary_v1"}


def rollback_production_canary(workspace: str, *, reason: str,
                               expected_canary_id: str = "") -> dict[str, Any]:
    control = _load_control(workspace)
    canaries = control.setdefault("canaries", {})
    record = dict(canaries.get(CANARY_KEY) or {})
    if not record:
        return {"ok": True, "status": "already_inactive"}
    if expected_canary_id and record.get("canary_id") != expected_canary_id:
        return {"ok": False, "status": "canary_identity_mismatch"}
    record["status"] = "rolled_back"
    record["production_route_effective"] = False
    record["rolled_back_at"] = now_iso()
    record["rollback_reason"] = str(reason)
    canaries[CANARY_KEY] = record
    control["updated_at"] = now_iso()
    atomic_json(_control_path(workspace), control)
    event = append_evolution_event(
        workspace, "policy/canary_rolled_back",
        subject_id=str(record.get("candidate_id") or ""),
        project_id="literature_github_learning",
        payload={"canary_id": record.get("canary_id"), "reason": reason,
                 "production_route_effective": False},
        evidence_refs=[str(_control_path(workspace))],
        idempotency_key=f"policy-canary-rollback:{record.get('canary_id')}:{reason}",
    )
    return {"ok": True, "status": "canary_rolled_back", "canary": record, "event": event}


def record_production_canary_outcome(workspace: str, *, canary_id: str,
                                     task_id: str, accepted: bool,
                                     false_success: bool = False,
                                     truth_gate_failed: bool = False) -> dict[str, Any]:
    """Account one terminal canary task and enforce automatic rollback."""
    control = _load_control(workspace)
    record = dict((control.setdefault("canaries", {})).get(CANARY_KEY) or {})
    if record.get("canary_id") != canary_id:
        return {"ok": False, "status": "canary_identity_mismatch"}
    seen = list(record.get("terminal_task_ids") or [])
    if task_id in seen:
        return {"ok": True, "status": "idempotent_outcome", "canary": record}
    seen.append(task_id)
    record["terminal_task_ids"] = seen[-100:]
    if accepted:
        record["accepted_tasks"] = int(record.get("accepted_tasks") or 0) + 1
        record["consecutive_failures"] = 0
    else:
        record["failed_tasks"] = int(record.get("failed_tasks") or 0) + 1
        record["consecutive_failures"] = int(record.get("consecutive_failures") or 0) + 1
    if false_success:
        record["false_success_count"] = int(record.get("false_success_count") or 0) + 1
    if truth_gate_failed:
        record["truth_gate_failure_count"] = int(record.get("truth_gate_failure_count") or 0) + 1
    record["last_outcome_at"] = now_iso()
    record["last_task_id"] = task_id
    thresholds = dict(record.get("automatic_rollback") or {})
    rollback_reason = ""
    if int(record.get("false_success_count") or 0) >= int(thresholds.get("false_success") or 1):
        rollback_reason = "automatic:false_success"
    elif int(record.get("truth_gate_failure_count") or 0) >= int(thresholds.get("truth_gate_failures") or 1):
        rollback_reason = "automatic:truth_gate_failure"
    elif int(record.get("consecutive_failures") or 0) >= int(thresholds.get("consecutive_failures") or 2):
        rollback_reason = "automatic:consecutive_failures"
    elif int(record.get("accepted_tasks") or 0) >= int(record.get("max_accepted_tasks") or 0):
        rollback_reason = "automatic:sample_budget_complete"
    control["canaries"][CANARY_KEY] = record
    control["updated_at"] = now_iso()
    atomic_json(_control_path(workspace), control)
    if rollback_reason:
        return rollback_production_canary(
            workspace, reason=rollback_reason, expected_canary_id=canary_id)
    return {"ok": True, "status": "canary_outcome_recorded", "canary": record}


def run_rollback_drill(workspace: str, *, candidate_id: str,
                       readiness_attestation_path: str) -> dict[str, Any]:
    """Exercise activate -> resolve -> rollback on the real reversible policy path."""
    activation = authorize_production_canary(
        workspace, candidate_id=candidate_id,
        readiness_attestation_path=readiness_attestation_path,
        authorization_ref="explicit_user_authorization:2026-09-01",
        max_accepted_tasks=1, duration_hours=1, allowed_instances=["04"],
    )
    if not activation.get("ok"):
        return {"ok": False, "status": "drill_activation_failed", "activation": activation}
    probe = resolve_production_canary(
        workspace, instance_id="04", user_message="研究 GitHub 源码和文献证据并对比")
    rollback = rollback_production_canary(
        workspace, reason="governed_rollback_drill",
        expected_canary_id=str((activation.get("canary") or {}).get("canary_id") or ""))
    after = resolve_production_canary(
        workspace, instance_id="04", user_message="研究 GitHub 源码和文献证据并对比")
    passed = bool(probe.get("active") is True and rollback.get("ok")
                  and after.get("active") is False)
    result = {"schema_version": 1, "ok": passed,
              "status": "rollback_drill_passed" if passed else "rollback_drill_failed",
              "candidate_id": candidate_id, "activation": activation,
              "route_before_rollback": probe, "rollback": rollback,
              "route_after_rollback": after, "completed_at": now_iso()}
    path = (workspace_root(workspace) / "share/mind/governance/experience_guided_policy/rollback_drills"
            / f"{candidate_id}.json")
    atomic_json(path, result)
    result["path"] = str(path)
    return result
