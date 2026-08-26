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
    record = {
        "schema_version": 1, "candidate_id": candidate_id, "version": version,
        "title": str(payload.get("title") or candidate_id), "status": status,
        "experiment_id": str(payload.get("experiment_id") or ""),
        "strategy_id": str(payload.get("strategy_id") or candidate_id),
        "source_episode_ids": sorted(set(source_episodes)),
        "failure_classes": sorted(set(str(value) for value in payload.get("failure_classes") or [] if str(value))),
        "applicability": applicability,
        "non_applicability": [str(value) for value in payload.get("non_applicability") or [] if str(value)],
        "counterexamples": [str(value) for value in payload.get("counterexamples") or [] if str(value)],
        "baseline": dict(payload.get("baseline") or {}),
        "intervention": str(payload.get("intervention") or ""),
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
    return {"ok": True, "status": status, "candidate": record, "path": str(current_path)}


def load_candidate_skills(workspace: str) -> list[dict[str, Any]]:
    root = workspace_root(workspace) / "share/mind/governance/rl/candidate_skills"
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("candidate_*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows

