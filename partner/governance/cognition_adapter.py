"""Shadow-only adapter for externally produced cognition ledgers.

The adapter is deliberately absent from the manual_stable hot path.  It can
validate and archive an explicitly supplied research bundle, then build a
schema-compatible Candidate Skill *draft*.  It never registers, promotes,
enqueues, or executes that draft.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import now_iso
from .storage import append_jsonl, atomic_json, safe_id, workspace_root


def _required(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _required_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    result = sorted(set(str(item).strip() for item in value if str(item).strip()))
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _digest(value: dict[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "bundle_digest"}
    raw = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_cognition_shadow_bundle(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("cognition shadow bundle must be an object")
    if value.get("schema_version") != 1 or value.get("kind") != "partner_cognition_shadow":
        raise ValueError("unsupported cognition shadow bundle contract")
    if value.get("production_mutation_allowed") is not False:
        raise ValueError("cognition shadow cannot allow production mutation")
    if value.get("candidate_registration_allowed") is not False:
        raise ValueError("cognition shadow cannot allow candidate registration")
    if value.get("manual_stable_override") is not False:
        raise ValueError("cognition shadow cannot override manual_stable")
    supplied_digest = _required(value.get("bundle_digest"), "bundle_digest")
    if supplied_digest != _digest(value):
        raise ValueError("cognition shadow bundle digest mismatch")
    instance_id = _required(value.get("partner_instance_id"), "partner_instance_id")
    if instance_id not in {"01", "02", "03", "04", "05"}:
        raise ValueError("partner_instance_id must be 01..05")
    episodes = _required_list(value.get("source_episode_ids"), "source_episode_ids")
    if any(not episode.startswith("episode_") for episode in episodes):
        raise ValueError("source_episode_ids must be Partner episode_ identifiers")
    state = value.get("reduced_state")
    ledger = value.get("ledger")
    if not isinstance(state, dict) or not isinstance(ledger, dict):
        raise ValueError("reduced_state and ledger are required objects")
    if state.get("project_id") != value.get("project_id"):
        raise ValueError("project identity mismatch")
    if state.get("episode_id") != value.get("cognition_episode_id"):
        raise ValueError("cognition episode identity mismatch")
    if int(state.get("event_count") or 0) != int(ledger.get("event_count") or -1):
        raise ValueError("event_count mismatch")
    for name, digest in (("ledger.sha256", ledger.get("sha256")),
                         ("ledger.head_hash", ledger.get("head_hash")),
                         ("reduced_state.replay_digest", state.get("replay_digest"))):
        if len(str(digest or "")) != 64:
            raise ValueError(f"{name} must be a sha256 digest")
    result = json.loads(json.dumps(value))
    result["source_episode_ids"] = episodes
    return result


def load_cognition_shadow_bundle(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read cognition shadow bundle: {exc}") from exc
    return validate_cognition_shadow_bundle(value)


def _verify_source_episodes(workspace: str, bundle: dict[str, Any]) -> list[str]:
    root = workspace_root(workspace)
    verified: list[str] = []
    linked_task = False
    linked_project = False
    for episode_id in bundle["source_episode_ids"]:
        path = root / "share" / "mind" / "governance" / "episodes" / episode_id / "state.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"source Partner episode is missing or invalid: {episode_id}") from exc
        if not isinstance(state, dict) or state.get("schema_version") != 3 or state.get("episode_id") != episode_id:
            raise ValueError(f"source Partner episode identity mismatch: {episode_id}")
        if (str(state.get("task_id") or "") == str(bundle["partner_task_id"])
                and str(state.get("instance_id") or "") == str(bundle["partner_instance_id"])):
            linked_task = True
            linked_project = str(state.get("project_id") or "partner-unassigned") == str(bundle["project_id"])
        verified.append(str(path))
    if not linked_task:
        raise ValueError("no source Partner episode matches partner_task_id/partner_instance_id")
    if not linked_project:
        raise ValueError("source Partner episode project_id does not match bundle project_id")
    return verified


def verify_cognition_shadow_sources(workspace: str, bundle: dict[str, Any]) -> list[str]:
    """Public read-only verification seam for downstream shadow consumers."""
    return _verify_source_episodes(workspace, validate_cognition_shadow_bundle(bundle))


def import_cognition_shadow(workspace: str, path: str | Path) -> dict[str, Any]:
    """Archive one validated bundle without activating any behavior."""
    bundle = load_cognition_shadow_bundle(path)
    verified_sources = _verify_source_episodes(workspace, bundle)
    root = workspace_root(workspace)
    digest = str(bundle["bundle_digest"])
    import_id = f"cognition_shadow_{digest[:16]}"
    directory = root / "share" / "mind" / "governance" / "cognition_shadow"
    destination = directory / f"{safe_id(import_id)}.json"
    if destination.exists():
        return {
            "ok": True, "status": "already_imported", "import_id": import_id,
            "path": str(destination), "production_mutation": False,
        }
    record = {
        "schema_version": 1,
        "import_id": import_id,
        "bundle_digest": digest,
        "project_id": bundle["project_id"],
        "cognition_episode_id": bundle["cognition_episode_id"],
        "source_episode_ids": bundle["source_episode_ids"],
        "verified_source_paths": verified_sources,
        "source_path": str(Path(path).resolve()),
        "status": "shadow_imported",
        "production_mutation": False,
        "candidate_registered": False,
        "imported_at": now_iso(),
        "bundle": bundle,
    }
    atomic_json(destination, record)
    append_jsonl(directory / "imports.jsonl", {key: item for key, item in record.items() if key != "bundle"})
    return {"ok": True, "status": "shadow_imported", "import_id": import_id,
            "path": str(destination), "production_mutation": False}


def correct_cognition_shadow_import(
    workspace: str, *, import_id: str, action: str, reason: str,
) -> dict[str, Any]:
    """Append an invalidate/reinstate decision; never rewrite the archive."""
    if action not in {"invalidate", "reinstate"}:
        raise ValueError("action must be invalidate or reinstate")
    import_id = _required(import_id, "import_id")
    reason = _required(reason, "reason")
    root = workspace_root(workspace)
    directory = root / "share" / "mind" / "governance" / "cognition_shadow"
    archive = directory / f"{safe_id(import_id)}.json"
    if not archive.is_file():
        raise ValueError(f"unknown cognition shadow import: {import_id}")
    record = {
        "schema_version": 1, "import_id": import_id, "action": action,
        "reason": reason, "corrected_at": now_iso(),
    }
    append_jsonl(directory / "corrections.jsonl", record)
    return {"ok": True, **record}


def candidate_skill_draft_from_cognition(
    workspace: str,
    bundle: dict[str, Any],
    *,
    candidate_id: str,
    title: str,
    intervention: str,
    applicability: list[str],
    success_criteria: list[str],
) -> dict[str, Any]:
    """Build, but do not register, a Partner-compatible shadow candidate."""
    value = validate_cognition_shadow_bundle(bundle)
    verified_sources = _verify_source_episodes(workspace, value)
    candidate_id = _required(candidate_id, "candidate_id")
    applicability = _required_list(applicability, "applicability")
    success_criteria = _required_list(success_criteria, "success_criteria")
    state = value["reduced_state"]
    return {
        "candidate_id": candidate_id,
        "title": _required(title, "title"),
        "status": "shadow",
        "experiment_id": "",
        "strategy_id": candidate_id,
        "source_episode_ids": value["source_episode_ids"],
        "failure_classes": [],
        "applicability": applicability,
        "non_applicability": ["automatic production mutation", "manual_stable override"],
        "counterexamples": [],
        "baseline": {
            "cognition_event_count": int(state.get("event_count") or 0),
            "unresolved_actions": len(state.get("unresolved_actions") or []),
        },
        "intervention": _required(intervention, "intervention"),
        "success_criteria": success_criteria,
        "shadow_evidence": {
            "bundle_digest": value["bundle_digest"],
            "cognition_episode_id": value["cognition_episode_id"],
            "replay_digest": state["replay_digest"],
            "verified_source_paths": verified_sources,
        },
        "rollback": "do not register or activate this shadow draft",
    }
