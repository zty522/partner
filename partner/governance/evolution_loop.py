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
        return {"ok": True, "status": "recorded", "issue": data, "path": str(path)}
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
        return {"ok": True, "status": "candidate", "experiment": data, "path": str(path), "files": [str(path)]}
    except (TypeError, ValueError, OSError) as exc:
        return {"ok": False, "status": "invalid_experiment", "error": str(exc), "retryable": False}


def decide_experiment(workspace: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        requested = str(params.get("decision") or "inconclusive")
        regression_passed = bool(params.get("regression_passed", False))
        criteria = params.get("criteria_results") or {}
        criteria_passed = bool(criteria) and all(bool(value) for value in criteria.values())
        if requested == "promoted" and not criteria_passed:
            return {"ok": False, "status": "promotion_gate_failed",
                    "error": "all declared success criteria must have explicit passing results", "retryable": False}
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
        append_jsonl(governance_log(workspace, "promotion_decisions"), data)
        return {"ok": True, "status": decision.decision, "decision": data,
                "promoted": decision.decision == "promoted",
                "resume_project": str(params.get("project_id") or ""),
                "resume_action_id": str(params.get("resume_action_id") or "")}
    except (TypeError, ValueError, OSError) as exc:
        return {"ok": False, "status": "invalid_promotion_decision", "error": str(exc), "retryable": False}
