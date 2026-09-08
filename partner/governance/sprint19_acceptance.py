"""Evidence-backed acceptance checks for Sprint 19's three distinct loops."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import now_iso
from .storage import atomic_json, workspace_root


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, TypeError, ValueError):
        pass
    return rows


def audit_project_iteration(workspace: str | Path, project_id: str) -> dict[str, Any]:
    root = workspace_root(str(workspace))
    receipt_paths = sorted(
        (root / "share/projects" / project_id / "governance/receipts").glob("*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    receipts = [_json(path) for path in receipt_paths[-5:]]
    receipts = [value for value in receipts if value]
    ids = [str(value.get("receipt_id") or "") for value in receipts]
    artifact_sets = [tuple(sorted(str(item) for item in value.get("artifacts") or []))
                     for value in receipts]
    # The executor action is often the stable Event name.  The actual domain
    # action is carried by the result artifact/finding (for example
    # 03_md_timestep_stability versus 03_md_temperature_sweep).
    action_sets = []
    for value in receipts:
        semantic = [str(item) for item in value.get("actions_executed") or []]
        semantic.extend(Path(str(item)).stem for item in value.get("artifacts") or []
                        if str(item).endswith(".json"))
        if value.get("findings"):
            semantic.extend(str(item).split(" 已执行", 1)[0]
                            for item in value.get("findings") or [])
        action_sets.append(tuple(dict.fromkeys(semantic)))
    existing_artifacts = [
        item for value in receipts for item in value.get("artifacts") or []
        if Path(str(item)).is_file()
    ]
    gates = {
        "multiple_receipts": len(receipts) >= 2,
        "receipt_identity_advanced": len(set(ids)) >= 2,
        "actions_changed": len(set(action_sets)) >= min(2, len(action_sets)),
        "artifacts_changed": len(set(artifact_sets)) >= min(2, len(artifact_sets)),
        "real_artifact_exists": bool(existing_artifacts),
        "next_action_or_truthful_stop": bool(receipts and (
            receipts[-1].get("next_actions") or receipts[-1].get("stop_reason"))),
    }
    return {
        "capability": "iterative_project_progress", "project_id": project_id,
        "passed": all(gates.values()), "gates": gates,
        "receipt_ids": ids, "recent_action_sets": action_sets,
        "existing_artifacts": existing_artifacts[-8:],
        "evidence_refs": [str(path) for path in receipt_paths[-5:]],
    }


def audit_active_learning(workspace: str | Path, instance_id: str = "") -> dict[str, Any]:
    root = workspace_root(str(workspace))
    native = _jsonl(root / "state/instance_native/events.jsonl")
    if instance_id:
        native = [row for row in native if str(row.get("instance_id") or "") == instance_id]
    triggered = [row for row in native if row.get("event_type") == "native_learning_triggered"]
    completed = [row for row in native if row.get("event_type") == "native_learning_completed"]
    selections = sorted(
        (root / "share/mind/governance/experience_guided_policy/native_action_selections").glob("*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    selected = [_json(path) for path in selections]
    if instance_id:
        selected = [row for row in selected if row.get("instance_id") == instance_id]
    interventions = [row for row in selected if row.get("learning_intervention_applied") is True]
    intervention_ids = {str(row.get("selection_id") or "") for row in interventions}
    successful_intervention_receipts: list[str] = []
    for path in (root / "share/projects").glob("*/governance/receipts/*.json"):
        receipt = _json(path)
        text = json.dumps(receipt.get("findings") or [], ensure_ascii=False)
        if (receipt.get("delivery_confirmed") is True
                and any(identity and identity in text for identity in intervention_ids)):
            successful_intervention_receipts.append(str(path))
    policy = _json(root / "share/mind/governance/active_learning/sprint18/learning_policy.json")
    gates = {
        "failure_or_gap_observed": bool(triggered),
        "bounded_learning_completed": bool(completed),
        "next_action_selection_recorded": bool(selected),
        "learning_changed_next_action": bool(interventions),
        "changed_action_executed_successfully": bool(successful_intervention_receipts),
        "learning_posterior_recorded": bool(policy.get("posteriors")),
    }
    evidence = [str(path) for path in selections[-5:]] + successful_intervention_receipts[-5:]
    policy_path = root / "share/mind/governance/active_learning/sprint18/learning_policy.json"
    if policy_path.is_file():
        evidence.append(str(policy_path))
    return {
        "capability": "active_learning", "instance_id": instance_id or "all",
        "passed": all(gates.values()), "gates": gates,
        "learning_trigger_count": len(triggered),
        "learning_completed_count": len(completed),
        "intervention_selection_count": len(interventions),
        "latest_intervention": interventions[-1] if interventions else {},
        "successful_intervention_receipts": successful_intervention_receipts[-5:],
        "selection_changed": bool(policy.get("selection_changed")),
        "evidence_refs": evidence,
    }


def audit_self_evolution(workspace: str | Path) -> dict[str, Any]:
    root = workspace_root(str(workspace))
    candidates = []
    for path in (root / "share/mind/governance/experience_guided_policy").rglob("*candidate*.json"):
        value = _json(path)
        if value.get("candidate_id") or value.get("experiment_id"):
            candidates.append((path, value))
    for path in (root / "share/mind/governance/code_candidates").glob("*/result.json"):
        value = _json(path)
        if value.get("candidate_id"):
            candidates.append((path, value))
    applied = [(path, value) for path, value in candidates
               if (value.get("production_effective") is True
                   and str(value.get("decision") or "") != "already_effective")
               or str(value.get("decision") or "") in {"applied", "promoted"}]
    behavioral = [(path, value) for path, value in applied if
                  value.get("target_file") or value.get("patch") or value.get("diff")
                  or (value.get("candidate") or {}).get("target_file")]
    decisions = _jsonl(root / "share/mind/governance/experience_guided_policy/promotion_decisions.jsonl")
    gates = {
        "candidate_recorded": bool(candidates),
        "matched_decision_recorded": bool(decisions),
        "production_effective_candidate": bool(applied),
        "behavior_changing_surface": bool(behavioral),
    }
    return {
        "capability": "bounded_self_evolution", "passed": all(gates.values()),
        "gates": gates, "candidate_count": len(candidates),
        "production_effective_count": len(applied),
        "behavioral_production_count": len(behavioral),
        "evidence_refs": [str(path) for path, _ in (behavioral or applied or candidates)[-8:]],
        "claim_limit": "bounded self-evolution; this does not prove unrestricted or long-term autonomous evolution",
    }


def run_sprint19_acceptance(workspace: str | Path, *, project_id: str = "",
                            instance_id: str = "") -> dict[str, Any]:
    root = workspace_root(str(workspace))
    projects = [project_id] if project_id else [
        "xiaohongshu_operations", "molecular_generation", "molecular_dynamics_study",
        "literature_github_learning", "hermes_partner_explore",
    ]
    project_results = [audit_project_iteration(root, value) for value in projects]
    active = audit_active_learning(root, instance_id)
    evolution = audit_self_evolution(root)
    result = {
        "schema_version": 1, "created_at": now_iso(),
        "status": "passed" if all(row["passed"] for row in project_results)
        and active["passed"] and evolution["passed"] else "gaps_remain",
        "project_iteration": project_results,
        "active_learning": active, "self_evolution": evolution,
    }
    output = root / "share/mind/governance/experience_guided_policy/sprint19/acceptance_latest.json"
    atomic_json(output, result)
    result["path"] = str(output)
    result["files"] = [str(output)]
    result["ok"] = True
    return result
