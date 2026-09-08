"""Extended shadow producer for Gate B.

This module is part of HERMES_CONTINUATION_PROMPT.md stage 2.  It produces
a longer cognition event chain from a Partner Episode Trace v3 by deriving
attention/allocated, action/requested, action/completed, and prediction/checked
events strictly from structured fields already present in Episode state.json.

No prose interpretation, no hypothesis invention, no belief claims.  When a
field is absent (e.g. no reward truth component), the corresponding event is
NOT emitted; we never fabricate.

Original cognition_mirror.py is untouched.  Consumers who want the extended
chain call mirror_episode_state_extended(workspace, state_path, extended=True).
Default is False — it falls back to the original 2-event mirror.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .cognition_adapter import import_cognition_shadow, validate_cognition_shadow_bundle
from .cognition_mirror import (
    GENESIS_HASH,
    _atomic_bytes,
    _bundle_digest,
    _digest,
    _event_record,
    _reduced_state,
    mirror_episode_state,
)
from .storage import atomic_json, safe_id, workspace_root


def _tool_events_for_call(call: dict[str, Any], *, parent_event_id: str,
                           source_hash: str, common: dict[str, Any]) -> list[dict[str, Any]]:
    """Build attention/allocated + action/requested + action/completed for one tool_call."""
    tool_call_id = str(call.get("tool_call_id") or "").strip()
    if not tool_call_id:
        return []
    step_id = str(call.get("step_id") or "").strip()
    event_kind = str(call.get("event_type") or "").strip()
    tool_status = str(call.get("status") or "").strip()
    elapsed = call.get("elapsed_sec")
    depends_on = list(call.get("depends_on") or [])

    attention_id = f"cevt:{source_hash[:24]}:attention:{tool_call_id}"
    attention = {
        **common, "event_id": attention_id, "event_type": "attention/allocated",
        "parents": [parent_event_id],
        "payload": {
            "target": tool_call_id, "step_id": step_id,
            "event_kind": event_kind, "depends_on": depends_on,
            "scope": "deterministic projection from tool_calls; no intent claim",
        },
    }

    requested_id = f"cevt:{source_hash[:24]}:action:{tool_call_id}"
    requested = {
        **common, "event_id": requested_id, "event_type": "action/requested",
        "parents": [attention_id],
        "payload": {
            "action_id": tool_call_id, "step_id": step_id,
            "action_kind": event_kind, "depends_on": depends_on,
            "scope": "deterministic projection; pre-execution request from tool_calls",
        },
    }

    completed = {
        **common, "event_id": f"cevt:{source_hash[:24]}:completed:{tool_call_id}",
        "event_type": "action/completed",
        "parents": [requested_id],
        "payload": {
            "action_id": tool_call_id, "step_id": step_id,
            "status": tool_status, "elapsed_sec": elapsed,
            "scope": "deterministic projection; outcome from tool_calls.status",
        },
    }
    return [attention, requested, completed]


def _prediction_event(episode: dict[str, Any], *, parent_event_id: str,
                      source_hash: str, common: dict[str, Any]) -> dict[str, Any] | None:
    """One prediction/checked event per Episode, derived from reward_vector."""
    rv = episode.get("reward_vector") or {}
    values = rv.get("values") if isinstance(rv, dict) else None
    if not isinstance(values, dict) or "truth" not in values:
        return None
    prediction_id = f"cevt:{source_hash[:24]}:prediction"
    return {
        **common, "event_id": prediction_id, "event_type": "prediction/checked",
        "parents": [parent_event_id],
        "payload": {
            "prediction_id": prediction_id,
            "truth_score": values.get("truth"),
            "hard_gate_passed": rv.get("hard_gate_passed"),
            "policy_eligible": rv.get("policy_eligible"),
            "scalar": rv.get("scalar"),
            "scope": "deterministic projection from reward_vector.values.truth",
        },
    }


def _reduced_state_extended(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Reducer for the extended event chain (>=2 events).

    Preserves observation/percept keys so existing Gate B consumers keep
    working; adds attention/actions/predictions arrays.
    """
    if not events:
        raise ValueError("extended reduced_state requires at least 1 event")

    by_type_observations: dict[str, dict[str, Any]] = {}
    by_type_percepts: dict[str, dict[str, Any]] = {}
    attention: list[dict[str, Any]] = []
    actions: dict[str, dict[str, Any]] = {}
    predictions: list[dict[str, Any]] = []

    for event in events:
        et = event.get("event_type")
        summary = {
            "event_id": event["event_id"], "occurred_at": event["occurred_at"],
            "actor": event["actor"], "parents": event["parents"],
            "evidence_refs": event["evidence_refs"], "payload": event["payload"],
        }
        if et == "observation/recorded":
            obs_id = event["payload"].get("observation_id") or event["event_id"]
            by_type_observations[obs_id] = summary
        elif et == "percept/derived":
            perc_id = event["payload"].get("percept_id") or event["event_id"]
            by_type_percepts[perc_id] = summary
        elif et == "attention/allocated":
            attention.append(summary)
        elif et == "action/requested":
            action_id = event["payload"].get("action_id") or event["event_id"]
            actions.setdefault(action_id, {"requested": summary, "completed": None})
        elif et == "action/completed":
            action_id = event["payload"].get("action_id") or event["event_id"]
            entry = actions.setdefault(action_id, {"requested": None, "completed": None})
            entry["completed"] = summary
        elif et == "prediction/checked":
            predictions.append(summary)

    head = events[-1]
    first = events[0]
    state: dict[str, Any] = {
        "schema_version": 1,
        "project_id": first["project_id"],
        "episode_id": first["episode_id"],
        "event_count": len(events),
        "head_event_id": head["event_id"],
        "observations": by_type_observations,
        "percepts": by_type_percepts,
        "retrieved_memories": [], "hypotheses": {}, "actions": actions,
        "predictions": predictions, "belief_revisions": [], "consolidated_memories": [],
        "candidates": {}, "attention": attention, "unresolved_actions": [],
        "timeline": [event["event_id"] for event in events],
        "extended": True,
        "replay_digest": "",
    }
    state["replay_digest"] = _digest(state)
    return state


def mirror_episode_state_extended(workspace: str, state_path: str | Path,
                                   *, extended: bool = False) -> dict[str, Any]:
    """Mirror an Episode Trace v3 to a 2+ event cognition ledger.

    When extended=False (default), behaves identically to mirror_episode_state.
    When extended=True, also derives attention/allocated, action/requested,
    action/completed for every tool_call, plus one prediction/checked when
    reward_vector.values.truth is present.

    The extended mode keeps the original observation/percept pair as the
    chain head so older consumers reading only those fields still work.
    """
    if not extended:
        return mirror_episode_state(workspace, state_path)

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
        **common, "event_id": observation_id, "event_type": "observation/recorded",
        "parents": [],
        "payload": {"observation_id": episode_id, "source_kind": "partner_episode_trace_v3",
                    "task_id": task_id, "instance_id": instance_id,
                    "scope": "deterministic projection; includes tool_calls/extended"},
    }
    percept = {
        **common, "event_id": percept_id, "event_type": "percept/derived",
        "parents": [observation_id],
        "payload": {"percept_id": f"percept:{episode_id}",
                    "status": str(episode.get("status") or "unknown"),
                    "failure_classes": sorted(str(item) for item in episode.get("failure_classes") or []),
                    "reward_vector": dict(episode.get("reward_vector") or {}),
                    "interpretation_scope": "deterministic projection; no causal claim"},
    }

    events: list[dict[str, Any]] = [observation, percept]
    parent_event_id = percept_id
    tool_calls = episode.get("tool_calls") or []
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            triplet = _tool_events_for_call(call, parent_event_id=parent_event_id,
                                             source_hash=source_hash, common=common)
            for ev in triplet:
                events.append(ev)
            if triplet:
                parent_event_id = triplet[-1]["event_id"]

    prediction_event = _prediction_event(episode, parent_event_id=parent_event_id,
                                          source_hash=source_hash, common=common)
    if prediction_event is not None:
        events.append(prediction_event)
        parent_event_id = prediction_event["event_id"]

    records: list[dict[str, Any]] = []
    prev_hash = GENESIS_HASH
    for seq, event in enumerate(events, start=1):
        record = _event_record(event, seq=seq, prev_hash=prev_hash)
        records.append(record)
        prev_hash = record["event_hash"]

    ledger_bytes = b"".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        for record in records
    )

    reduced = _reduced_state_extended(events)
    mirror_id = safe_id(f"{cognition_episode_id}:{source_hash[:16]}:ext")
    directory = root / "share" / "mind" / "governance" / "cognition_mirror" / mirror_id
    ledger_path = directory / "events.jsonl"
    bundle_path = directory / "bundle.json"
    bundle = {
        "schema_version": 1, "kind": "partner_cognition_shadow",
        "extended": True,  # marker that this bundle uses the extended chain
        "project_id": project_id,
        "cognition_episode_id": cognition_episode_id,
        "partner_instance_id": instance_id,
        "partner_task_id": task_id,
        "source_episode_ids": [episode_id],
        "ledger": {"sha256": hashlib.sha256(ledger_bytes).hexdigest(),
                   "head_hash": records[-1]["event_hash"], "event_count": len(records)},
        "reduced_state": reduced,
        "candidate_registration_allowed": False,
        "production_mutation_allowed": False,
        "manual_stable_override": False,
    }
    bundle["bundle_digest"] = _bundle_digest(bundle)
    validate_cognition_shadow_bundle(bundle)

    if ledger_path.exists() and ledger_path.read_bytes() != ledger_bytes:
        raise ValueError("existing extended cognition mirror ledger conflicts with source digest")
    if not ledger_path.exists():
        _atomic_bytes(ledger_path, ledger_bytes)
    if bundle_path.exists():
        existing = json.loads(bundle_path.read_text(encoding="utf-8"))
        if existing != bundle:
            raise ValueError("existing extended cognition mirror bundle conflicts with source digest")
    else:
        atomic_json(bundle_path, bundle)
    imported = import_cognition_shadow(str(root), bundle_path)
    return {
        "ok": True,
        "status": "already_mirrored" if imported["status"] == "already_imported" else "mirrored",
        "mirror_id": mirror_id, "bundle": str(bundle_path), "ledger": str(ledger_path),
        "import_id": imported["import_id"], "source_episode_id": episode_id,
        "production_mutation": False, "candidate_registered": False,
        "event_count": len(records),
        "extended_event_types": ["attention/allocated", "action/requested", "action/completed",
                                  "prediction/checked"],
    }
