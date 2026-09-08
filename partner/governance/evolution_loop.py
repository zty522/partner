"""Issue, experiment, and promotion gates for Partner self-evolution."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .models import EvolutionExperiment, IssueRecord, PromotionDecision, now_iso
from .storage import append_jsonl, atomic_json, governance_log, workspace_root

def _fingerprint(category: str, summary: str, instance: str, project: str) -> str:
    normalized = re.sub(r"\s+", " ", summary.strip().lower())
    raw = "|".join((category, normalized, instance, project))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _read_jsonl(path: Path, limit: int = 500) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except ValueError:
            continue
    return rows


def record_issue(workspace: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        issue = IssueRecord(
            summary=str(params.get("summary") or ""),
            category=str(params.get("category") or "unknown"),
            severity=str(params.get("severity") or "medium"),
            evidence=list(params.get("evidence") or []),
            instance_id=str(params.get("instance_id") or ""),
            project_id=str(params.get("project_id") or ""),
            status=str(params.get("status") or "open"),
        )
        issue.validate()
        path = governance_log(workspace, "issues")
        fingerprint = _fingerprint(issue.category, issue.summary, issue.instance_id, issue.project_id)
        previous = next((row for row in reversed(_read_jsonl(path)) if row.get("fingerprint") == fingerprint), None)
        if previous and previous.get("status") not in {"resolved", "wont_fix"}:
            incoming_evidence = list(dict.fromkeys(issue.evidence))
            previous_evidence = list(previous.get("evidence") or [])
            if set(incoming_evidence).issubset(set(previous_evidence)):
                # Scouts may see an unchanged candidate policy on every tick.
                # Preserve append-only trajectories without inventing issue progress.
                return {"ok": True, "status": "unchanged", "issue": previous, "path": str(path)}
            issue.issue_id = str(previous.get("issue_id") or issue.issue_id)
            issue.created_at = str(previous.get("created_at") or issue.created_at)
            issue.occurrences = int(previous.get("occurrences") or 1) + 1
            issue.evidence = list(dict.fromkeys(previous_evidence + incoming_evidence))
        data = issue.to_dict()
        data["fingerprint"] = fingerprint
        for key in ("source_work_kind", "source_work_item_id", "parent_issue_id", "root_issue_id"):
            if params.get(key):
                data[key] = str(params[key])
        append_jsonl(path, data)
        from .evolution_events import append_evolution_event
        event = append_evolution_event(
            workspace,
            "issue/recorded",
            subject_id=issue.issue_id,
            project_id=issue.project_id,
            payload={"issue_id": issue.issue_id, "category": issue.category,
                     "severity": issue.severity, "status": issue.status},
            evidence_refs=issue.evidence,
            idempotency_key=f"issue-recorded:{issue.issue_id}:occurrence:{issue.occurrences}",
        )
        return {"ok": True, "status": "recorded", "issue": data,
                "path": str(path), "event": event}
    except (TypeError, ValueError, OSError) as exc:
        return {"ok": False, "status": "invalid_issue", "error": str(exc), "retryable": False}


def start_experiment(workspace: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        experiment = EvolutionExperiment(
            issue_id=str(params.get("issue_id") or ""),
            hypothesis=str(params.get("hypothesis") or ""),
            intervention=str(params.get("intervention") or ""),
            baseline=dict(params.get("baseline") or {}),
            success_criteria=list(params.get("success_criteria") or []),
            project_id=str(params.get("project_id") or ""),
            resume_action_id=str(params.get("resume_action_id") or ""),
            tests=list(params.get("tests") or []),
        )
        data = experiment.to_dict()
        path = workspace_root(workspace) / "share" / "mind" / "governance" / "experiments" / f"{experiment.experiment_id}.json"
        atomic_json(path, data)
        append_jsonl(governance_log(workspace, "experiments"), data)
        from .evolution_events import append_evolution_event
        event = append_evolution_event(
            workspace,
            "experiment/started",
            subject_id=experiment.experiment_id,
            project_id=experiment.project_id,
            payload={"experiment_id": experiment.experiment_id,
                     "issue_id": experiment.issue_id, "status": experiment.status,
                     "baseline": experiment.baseline,
                     "success_criteria": experiment.success_criteria},
            evidence_refs=experiment.tests,
            idempotency_key=f"experiment-started:{experiment.experiment_id}",
        )
        return {"ok": True, "status": "candidate", "experiment": data,
                "path": str(path), "files": [str(path)], "event": event}
    except (TypeError, ValueError, OSError) as exc:
        return {"ok": False, "status": "invalid_experiment", "error": str(exc), "retryable": False}


def project_experiment_decision(
    workspace: str,
    *,
    experiment_id: str,
    candidate_id: str,
    decision: str,
    criteria_results: dict[str, Any],
    metrics_before: dict[str, Any],
    metrics_after: dict[str, Any],
    regression_passed: bool,
    policy_event_id: str,
) -> dict[str, Any]:
    """Synchronize query projections from an already authoritative Policy Event."""
    experiment_path = (
        workspace_root(workspace) / "share" / "mind" / "governance"
        / "experiments" / f"{experiment_id}.json"
    )
    try:
        experiment_projection = json.loads(experiment_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        experiment_projection = None
    if isinstance(experiment_projection, dict):
        experiment_projection["status"] = decision
        experiment_projection["result"] = {
            "decision": decision,
            "criteria_results": dict(criteria_results),
            "metrics_before": dict(metrics_before),
            "metrics_after": dict(metrics_after),
            "regression_passed": bool(regression_passed),
            "policy_event_id": policy_event_id,
        }
        experiment_projection["updated_at"] = now_iso()
        atomic_json(experiment_path, experiment_projection)
    candidate_projection = None
    if candidate_id:
        from .candidate_skills import project_candidate_decision
        candidate_projection = project_candidate_decision(
            workspace,
            candidate_id,
            experiment_id=experiment_id,
            decision=decision,
            criteria_results=criteria_results,
            metrics_before=metrics_before,
            metrics_after=metrics_after,
            policy_event_id=policy_event_id,
        )
    return {
        "experiment_projection": str(experiment_path),
        "candidate_projection": candidate_projection,
    }


def decide_experiment(workspace: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        requested = str(params.get("decision") or "inconclusive")
        regression_passed = bool(params.get("regression_passed", False))
        criteria = params.get("criteria_results") or {}
        criteria_passed = bool(criteria) and all(bool(value) for value in criteria.values())
        if requested == "promoted" and not criteria_passed:
            return {"ok": False, "status": "promotion_gate_failed",
                    "error": "all declared success criteria must have explicit passing results", "retryable": False}

        # A named Candidate can only be promoted after it has a real Event
        # execution contract and is bound to this experiment. Knowledge notes
        # and legacy config blobs are proposals, not executable interventions.
        candidate_id = str(params.get("candidate_id") or "").strip()
        candidate_record = None
        if requested == "promoted" and candidate_id:
            from .candidate_execution import load_candidate, validate_execution_contract
            candidate_record = load_candidate(workspace, candidate_id)
            if candidate_record is None:
                return {"ok": False, "status": "candidate_not_found",
                        "error": f"candidate_id {candidate_id!r} not found", "retryable": False}
            ready, execution_reason = validate_execution_contract(
                candidate_record.get("execution_contract")
            )
            if not ready:
                return {"ok": False, "status": "candidate_not_executable",
                        "error": execution_reason, "candidate_id": candidate_id,
                        "retryable": False}
            bound_experiment = str(candidate_record.get("experiment_id") or "")
            requested_experiment = str(params.get("experiment_id") or "")
            if bound_experiment and bound_experiment != requested_experiment:
                return {"ok": False, "status": "candidate_experiment_mismatch",
                        "error": f"candidate is bound to {bound_experiment!r}, not {requested_experiment!r}",
                        "candidate_id": candidate_id, "retryable": False}

        # ---- BDK ocamms promotion guard (opt-in) --------------------------
        # Triggered ONLY when:
        #   1. requested == "promoted"
        #   2. caller passes enforce_bdk_ocamms=True
        #   3. caller passes a candidate_id that maps to a registered skill
        # If any condition fails, we silently skip (existing callers stay
        # untouched).  When triggered and the candidate is BDK-related, we
        # run bdk_ocamms_promotion_guard.  If it returns blocked=True, the
        # promotion is REJECTED with status "bdk_ocamms_blocked" and a
        # structured verdict is returned.  If passed, the verdict is appended
        # to decision.evidence so 05 can audit what was checked.
        bdk_ocamms_verdict = None
        if requested == "promoted" and bool(params.get("enforce_bdk_ocamms")):
            candidate_id = str(params.get("candidate_id") or "").strip()
            if not candidate_id:
                return {"ok": False, "status": "candidate_id_required",
                        "error": "enforce_bdk_ocamms requires candidate_id", "retryable": False}
            else:
                try:
                    from partner.learn.bdk_ocamms_promotion_guard import (
                        bdk_ocamms_promotion_guard,
                    )
                    bdk_ocamms_verdict = bdk_ocamms_promotion_guard(
                        workspace, candidate_id,
                        margin=float(params.get("bdk_ocamms_margin") or 0.20),
                        max_activated=int(params.get("bdk_ocamms_max_activated") or 2),
                    )
                except Exception as exc:
                    return {
                        "ok": False,
                        "status": "bdk_ocamms_guard_error",
                        "error": f"failed to run BDK ocamms guard: {type(exc).__name__}: {exc}",
                        "candidate_id": candidate_id,
                        "retryable": False,
                    }
                if bdk_ocamms_verdict.get("blocked"):
                    return {
                        "ok": False,
                        "status": "bdk_ocamms_blocked",
                        "error": bdk_ocamms_verdict.get("reason")
                                or "BDK ocamms blocked the promotion",
                        "candidate_id": candidate_id,
                        "verdict": bdk_ocamms_verdict,
                        "retryable": False,
                    }
        # ------------------------------------------------------------------

        decision = PromotionDecision(
            experiment_id=str(params.get("experiment_id") or ""),
            decision=requested,
            evidence=list(params.get("evidence") or []),
            regression_passed=regression_passed,
            metrics_before=dict(params.get("metrics_before") or {}),
            metrics_after=dict(params.get("metrics_after") or {}),
            rollback_required=bool(params.get("rollback_required", requested == "rejected")),
            reason=str(params.get("reason") or ""),
        )
        data = decision.to_dict()
        data["criteria_results"] = criteria
        if bdk_ocamms_verdict is not None:
            # Append a structured audit entry so 05 can later reconstruct
            # which BDK guard verdict led to the promotion (or near-fail).
            evidence = list(data.get("evidence") or [])
            evidence.append({
                "kind": "bdk_ocamms_guard",
                "candidate_id": bdk_ocamms_verdict.get("candidate_id"),
                "allowed": bdk_ocamms_verdict.get("allowed"),
                "blocked": bdk_ocamms_verdict.get("blocked"),
                "not_applicable": bdk_ocamms_verdict.get("not_applicable"),
                "reason": bdk_ocamms_verdict.get("reason"),
                "audit": bdk_ocamms_verdict.get("audit"),
            })
            data["evidence"] = evidence
        append_jsonl(governance_log(workspace, "promotion_decisions"), data)
        from .evolution_events import append_evolution_event
        experiment_id = str(params.get("experiment_id") or "")
        completed = append_evolution_event(
            workspace,
            "experiment/completed",
            subject_id=experiment_id,
            project_id=str(params.get("project_id") or ""),
            payload={"experiment_id": experiment_id, "decision": decision.decision,
                     "regression_passed": regression_passed,
                     "criteria_results": criteria,
                     "metrics_before": decision.metrics_before,
                     "metrics_after": decision.metrics_after,
                     "candidate_id": candidate_id},
            evidence_refs=[str(value) for value in data.get("evidence") or []
                           if isinstance(value, str)],
            idempotency_key=f"experiment-completed:{experiment_id}:{decision.decision}",
        )
        policy_event = append_evolution_event(
            workspace,
            f"policy/{decision.decision}",
            subject_id=candidate_id or experiment_id,
            project_id=str(params.get("project_id") or ""),
            parents=[completed["event_id"]],
            payload={"experiment_id": experiment_id, "candidate_id": candidate_id,
                     "decision": decision.decision, "production_effective": False,
                     "reason": decision.reason},
            evidence_refs=[completed["event_id"]],
            idempotency_key=f"policy-decision:{experiment_id}:{decision.decision}:{candidate_id or 'no-candidate'}",
        )
        # JSON files are query-friendly projections; Events remain authority.
        # Keep projections synchronized so a later Agent does not mistake an
        # already completed experiment for a fresh Candidate.
        projections = project_experiment_decision(
            workspace,
            experiment_id=experiment_id,
            candidate_id=candidate_id,
            decision=decision.decision,
            criteria_results=criteria,
            metrics_before=decision.metrics_before,
            metrics_after=decision.metrics_after,
            regression_passed=regression_passed,
            policy_event_id=policy_event["event_id"],
        )
        return {"ok": True, "status": decision.decision, "decision": data,
                "promoted": decision.decision == "promoted",
                "events": [completed, policy_event],
                **projections,
                "resume_project": str(params.get("project_id") or ""),
                "resume_action_id": str(params.get("resume_action_id") or "")}
    except (TypeError, ValueError, OSError) as exc:
        return {"ok": False, "status": "invalid_promotion_decision", "error": str(exc), "retryable": False}
