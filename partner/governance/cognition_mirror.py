"""Partner-native Episode -> cognition shadow sidecar.

This module deliberately does not import the BDK research package.  It emits
the same small, hash-chained exchange contract from an already reduced Partner
Episode Trace v3.  The runtime wrapper is feature-gated and fail-open; neither
entry point registers a Candidate or changes production policy.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .cognition_adapter import import_cognition_shadow, validate_cognition_shadow_bundle
from .storage import atomic_json, safe_id, workspace_root
from ..state.config import runtime_capability_enabled


GENESIS_HASH = "0" * 64


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bundle_digest(value: dict[str, Any]) -> str:
    return _digest({key: item for key, item in value.items() if key != "bundle_digest"})


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _event_record(event: dict[str, Any], *, seq: int, prev_hash: str) -> dict[str, Any]:
    record = {**event, "seq": seq, "prev_hash": prev_hash}
    record["event_hash"] = _digest(record)
    return record


def _reduced_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    first, second = events
    observation = {
        "event_id": first["event_id"], "occurred_at": first["occurred_at"],
        "actor": first["actor"], "parents": first["parents"],
        "evidence_refs": first["evidence_refs"], "payload": first["payload"],
    }
    percept = {
        "event_id": second["event_id"], "occurred_at": second["occurred_at"],
        "actor": second["actor"], "parents": second["parents"],
        "evidence_refs": second["evidence_refs"], "payload": second["payload"],
    }
    state: dict[str, Any] = {
        "schema_version": 1,
        "project_id": first["project_id"],
        "episode_id": first["episode_id"],
        "event_count": 2,
        "head_event_id": second["event_id"],
        "observations": {first["payload"]["observation_id"]: observation},
        "percepts": {second["payload"]["percept_id"]: percept},
        "retrieved_memories": [], "hypotheses": {}, "actions": {},
        "predictions": [], "belief_revisions": [], "consolidated_memories": [],
        "candidates": {}, "attention": [], "unresolved_actions": [],
        "timeline": [first["event_id"], second["event_id"]],
        "replay_digest": "",
    }
    state["replay_digest"] = _digest(state)
    return state


def mirror_episode_state(workspace: str, state_path: str | Path) -> dict[str, Any]:
    """Explicitly mirror one authoritative Episode state; still shadow-only."""
    source = Path(state_path).resolve()
    try:
        raw = source.read_bytes()
        episode = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Partner Episode state: {exc}") from exc
    if not isinstance(episode, dict) or episode.get("schema_version") != 3:
        raise ValueError("source must be a Partner Episode Trace v3 state")
    episode_id = str(episode.get("episode_id") or "").strip()
    task_id = str(episode.get("task_id") or "").strip()
    instance_id = str(episode.get("instance_id") or "").strip()
    if not episode_id.startswith("episode_") or not task_id or instance_id not in {"01", "02", "03", "04", "05"}:
        raise ValueError("source Episode requires valid episode/task/instance identity")

    root = workspace_root(workspace)
    authoritative = (root / "share" / "mind" / "governance" / "episodes" / episode_id / "state.json").resolve()
    if source != authoritative:
        raise ValueError("source must be the authoritative Partner Episode state path")

    source_hash = hashlib.sha256(raw).hexdigest()
    project_id = str(episode.get("project_id") or "partner-unassigned").strip()
    cognition_episode_id = f"cog:{episode_id}"
    occurred_at = str(episode.get("reduced_at") or "").strip()
    if not occurred_at:
        raise ValueError("source Episode requires reduced_at for deterministic mirroring")
    observation_id = f"cevt:{source_hash[:24]}:observation"
    percept_id = f"cevt:{source_hash[:24]}:percept"
    actor = {"kind": "system", "id": "partner-episode-adapter", "role": "observer"}
    common = {
        "schema_version": 1, "occurred_at": occurred_at, "actor": actor,
        "project_id": project_id, "episode_id": cognition_episode_id,
        "policy_version": "research-l0", "visibility": "audit",
        "evidence_refs": [f"sha256:{source_hash}"],
    }
    observation = {
        **common, "event_id": observation_id, "event_type": "observation/recorded", "parents": [],
        "payload": {"observation_id": episode_id, "source_kind": "partner_episode_trace_v3",
                    "task_id": task_id, "instance_id": instance_id},
    }
    percept = {
        **common, "event_id": percept_id, "event_type": "percept/derived", "parents": [observation_id],
        "payload": {"percept_id": f"percept:{episode_id}",
                    "status": str(episode.get("status") or "unknown"),
                    "failure_classes": sorted(str(item) for item in episode.get("failure_classes") or []),
                    "reward_vector": dict(episode.get("reward_vector") or {}),
                    "interpretation_scope": "deterministic projection; no causal claim"},
    }
    first = _event_record(observation, seq=1, prev_hash=GENESIS_HASH)
    second = _event_record(percept, seq=2, prev_hash=first["event_hash"])
    records = [first, second]
    ledger_bytes = b"".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        for record in records
    )
    reduced = _reduced_state([observation, percept])
    mirror_id = safe_id(f"{cognition_episode_id}:{source_hash[:16]}")
    directory = root / "share" / "mind" / "governance" / "cognition_mirror" / mirror_id
    ledger_path = directory / "events.jsonl"
    bundle_path = directory / "bundle.json"
    bundle = {
        "schema_version": 1, "kind": "partner_cognition_shadow", "project_id": project_id,
        "cognition_episode_id": cognition_episode_id, "partner_instance_id": instance_id,
        "partner_task_id": task_id, "source_episode_ids": [episode_id],
        "ledger": {"sha256": hashlib.sha256(ledger_bytes).hexdigest(),
                   "head_hash": second["event_hash"], "event_count": 2},
        "reduced_state": reduced, "candidate_registration_allowed": False,
        "production_mutation_allowed": False, "manual_stable_override": False,
    }
    bundle["bundle_digest"] = _bundle_digest(bundle)
    validate_cognition_shadow_bundle(bundle)

    if ledger_path.exists() and ledger_path.read_bytes() != ledger_bytes:
        raise ValueError("existing cognition mirror ledger conflicts with source digest")
    if not ledger_path.exists():
        _atomic_bytes(ledger_path, ledger_bytes)
    if bundle_path.exists():
        existing = json.loads(bundle_path.read_text(encoding="utf-8"))
        if existing != bundle:
            raise ValueError("existing cognition mirror bundle conflicts with source digest")
    else:
        atomic_json(bundle_path, bundle)
    imported = import_cognition_shadow(str(root), bundle_path)
    return {
        "ok": True, "status": "already_mirrored" if imported["status"] == "already_imported" else "mirrored",
        "mirror_id": mirror_id, "bundle": str(bundle_path), "ledger": str(ledger_path),
        "import_id": imported["import_id"], "source_episode_id": episode_id,
        "production_mutation": False, "candidate_registered": False,
    }


def mirror_reduced_episode(workspace: str, trace_result: dict[str, Any]) -> dict[str, Any]:
    """Mirror the exact Episode bundle returned by the hot-path reducer."""
    if not isinstance(trace_result, dict) or not trace_result.get("ok"):
        raise ValueError("a successful Episode reduction result is required")
    bundle = Path(str(trace_result.get("bundle") or ""))
    state_path = bundle / "state.json"
    expected = trace_result.get("state")
    if not isinstance(expected, dict):
        raise ValueError("Episode reduction result is missing state")
    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    if on_disk != expected:
        raise ValueError("Episode reduction result does not match authoritative state.json")
    return mirror_episode_state(workspace, state_path)


def try_mirror_reduced_episode(workspace: str, trace_result: dict[str, Any]) -> dict[str, Any]:
    """Feature-gated, fail-open runtime adapter."""
    if not runtime_capability_enabled(workspace, "cognition_shadow_mirror"):
        return {"ok": True, "status": "disabled", "mirrored": False,
                "production_mutation": False, "candidate_registered": False}
    try:
        return mirror_reduced_episode(workspace, trace_result)
    except Exception as exc:
        return {"ok": False, "status": "mirror_best_effort_failed", "error": str(exc)[:1000],
                "production_mutation": False, "candidate_registered": False}
