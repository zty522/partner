"""Execution gate for Event-first Candidate artifacts."""
from __future__ import annotations

import json
from typing import Any, Callable

from .candidate_skills import load_candidate_skills
from .evolution_events import append_evolution_event, load_evolution_events


Handler = Callable[[Any, dict[str, Any]], dict[str, Any]]


def validate_execution_contract(value: Any) -> tuple[bool, str]:
    if not isinstance(value, dict) or not value.get("ready"):
        return False, "execution contract is not ready"
    if value.get("kind") != "event":
        return False, "only kind=event is supported"
    if not str(value.get("event_type") or "").strip():
        return False, "execution contract requires event_type"
    allowed = value.get("allowed_instances")
    if not isinstance(allowed, list) or not allowed:
        return False, "execution contract requires allowed_instances"
    if not isinstance(value.get("default_params", {}), dict):
        return False, "execution contract default_params must be an object"
    return True, "ready"


def load_candidate(workspace: str, candidate_id: str) -> dict[str, Any] | None:
    return next(
        (row for row in load_candidate_skills(workspace)
         if str(row.get("candidate_id") or "") == candidate_id),
        None,
    )


def execute_candidate(
    workspace: str,
    candidate_id: str,
    *,
    ctx: Any,
    handlers: dict[str, Handler],
    params: dict[str, Any] | None = None,
    instance_id: str,
    execution_id: str,
    mode: str = "shadow",
) -> dict[str, Any]:
    """Execute a Candidate through an allow-listed Partner Event handler.

    Candidate content never imports or invokes arbitrary Python. The persisted
    contract names an Event; the caller supplies the audited handler registry.
    """
    completed_key = f"candidate-execution-completed:{execution_id}"
    prior_completed = next(
        (
            event
            for event in reversed(load_evolution_events(workspace))
            if event.get("idempotency_key") == completed_key
        ),
        None,
    )
    if prior_completed is not None:
        if str(prior_completed.get("subject_id") or "") != candidate_id:
            return {
                "ok": False,
                "status": "candidate_execution_blocked",
                "candidate_id": candidate_id,
                "error": "execution_id is already bound to a different candidate",
                "retryable": False,
                "execution_event_id": prior_completed["event_id"],
            }
        prior_payload = dict(prior_completed.get("payload") or {})
        prior_result = dict(prior_payload.get("result") or {})
        return {
            **prior_result,
            "ok": bool(prior_payload.get("ok")),
            "status": "idempotent_replay",
            "original_status": str(prior_payload.get("status") or ""),
            "candidate_id": candidate_id,
            "candidate_event_type": str(prior_payload.get("event_type") or ""),
            "execution_event_id": prior_completed["event_id"],
        }

    candidate = load_candidate(workspace, candidate_id)
    contract = dict((candidate or {}).get("execution_contract") or {})
    event_type = str(contract.get("event_type") or "")
    requested = append_evolution_event(
        workspace,
        "candidate/execution_requested",
        subject_id=candidate_id,
        project_id=str((candidate or {}).get("project_id") or ""),
        payload={"execution_id": execution_id, "mode": mode,
                 "instance_id": instance_id, "event_type": event_type},
        evidence_refs=list((candidate or {}).get("source_episode_ids") or []),
        idempotency_key=f"candidate-execution-requested:{execution_id}",
    )

    ready, reason = validate_execution_contract(contract)
    status = str((candidate or {}).get("status") or "missing")
    allowed_instances = [str(value) for value in contract.get("allowed_instances") or []]
    error = ""
    if candidate is None:
        error = "candidate not found"
    elif not ready:
        error = reason
    elif status not in {"candidate", "shadow", "canary", "promoted"}:
        error = f"candidate status {status!r} is not executable"
    elif instance_id not in allowed_instances:
        error = f"instance {instance_id!r} is not allowed"
    elif event_type not in handlers:
        error = f"event_type {event_type!r} is not in the allow-listed registry"
    elif mode == "production" and not bool(candidate.get("production_effective")):
        error = "candidate is not production_effective"

    if error:
        result = {"ok": False, "status": "candidate_execution_blocked",
                  "candidate_id": candidate_id, "error": error, "retryable": False}
    else:
        event_params = dict(contract.get("default_params") or {})
        event_params.update(dict(params or {}))
        try:
            result = handlers[event_type](ctx, event_params)
            if not isinstance(result, dict):
                result = {"ok": False, "status": "invalid_handler_result",
                          "error": "candidate event handler did not return an object"}
        except Exception as exc:  # execution boundary must produce a completed event
            result = {"ok": False, "status": "candidate_execution_failed",
                      "error": f"{type(exc).__name__}: {exc}", "retryable": False}

    try:
        persisted_result = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        persisted_result = {
            "ok": bool(result.get("ok")),
            "status": str(result.get("status") or ""),
        }
    completed = append_evolution_event(
        workspace,
        "candidate/execution_completed",
        subject_id=candidate_id,
        project_id=str((candidate or {}).get("project_id") or ""),
        parents=[requested["event_id"]],
        payload={
            "execution_id": execution_id,
            "mode": mode,
            "instance_id": instance_id,
            "event_type": event_type,
            "ok": bool(result.get("ok")),
            "status": str(result.get("status") or ""),
            "result_summary": str(result.get("summary") or result.get("error") or "")[:1000],
            "result": persisted_result,
        },
        evidence_refs=[str(value) for value in result.get("files") or []],
        idempotency_key=completed_key,
    )
    return {**result, "candidate_id": candidate_id, "candidate_event_type": event_type,
            "execution_event_id": completed["event_id"]}
