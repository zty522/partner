from __future__ import annotations

import json
import os

import pytest

from partner.governance.cognition_mirror_extended import (
    mirror_episode_state_extended,
)


def _write_episode(workspace, *, with_tool_calls=True, with_truth=True, status="done"):
    (workspace / "config").mkdir(parents=True)
    (workspace / "config/partner_config.json").write_text(json.dumps({
        "runtime": {"mode": "manual_stable"},
    }), encoding="utf-8")
    state = {
        "schema_version": 3,
        "episode_id": "episode_ext_test",
        "task_id": "task_ext_test",
        "instance_id": "04",
        "project_id": "literature-learning",
        "status": status,
        "failure_classes": [],
        "reward_vector": {
            "values": {"truth": 1.0, "business_progress": 1.0, "handoff": 1.0},
            "hard_gate_passed": True,
            "policy_eligible": True,
            "scalar": 1.0,
        } if with_truth else {"hard_gate_passed": False, "policy_eligible": False},
        "reduced_at": "2026-08-28T13:00:00+08:00",
    }
    if with_tool_calls:
        state["tool_calls"] = [
            {"tool_call_id": "tool_a", "step_id": "step1", "event_type": "read_file",
             "depends_on": [], "started_at": "2026-08-28T13:00:01",
             "ended_at": "2026-08-28T13:00:02", "status": "completed", "elapsed_sec": 1.0,
             "raw_result_ref": "/tmp/_step_step1.result.json"},
            {"tool_call_id": "tool_b", "step_id": "step2", "event_type": "generate_text",
             "depends_on": ["step1"], "started_at": "2026-08-28T13:00:03",
             "ended_at": "2026-08-28T13:00:05", "status": "completed", "elapsed_sec": 2.0,
             "raw_result_ref": "/tmp/_step_step2.result.json"},
        ]
    else:
        state["tool_calls"] = []
    bundle = workspace / "share/mind/governance/episodes/episode_ext_test"
    bundle.mkdir(parents=True)
    state_path = bundle / "state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state, bundle, state_path


def test_extended_false_falls_back_to_original_two_events(tmp_path):
    """Default behaviour must match the original Gate B 2-event mirror exactly."""
    workspace = tmp_path / "ws"
    state, bundle, _ = _write_episode(workspace)
    result = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                            extended=False)
    assert result["status"] == "mirrored"
    # Verify via ledger length (extended=False delegates to mirror_episode_state
    # which returns the original 2-event payload; event_count is not present)
    ledger = open(result["ledger"], encoding="utf-8").read().splitlines()
    assert len(ledger) == 2
    types = [json.loads(line)["event_type"] for line in ledger]
    assert types == ["observation/recorded", "percept/derived"]
    assert result["production_mutation"] is False
    assert result["candidate_registered"] is False


def test_extended_true_emits_two_plus_three_per_tool_call_plus_prediction(tmp_path):
    """With 2 tool_calls and truth reward, expected: 2 + 3*2 + 1 = 9 events."""
    workspace = tmp_path / "ws"
    state, bundle, _ = _write_episode(workspace, with_tool_calls=True, with_truth=True)
    result = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                            extended=True)
    assert result["status"] == "mirrored"
    assert result["event_count"] == 9, f"expected 9 events, got {result['event_count']}"
    assert result["production_mutation"] is False
    assert result["candidate_registered"] is False
    assert result["extended_event_types"] == [
        "attention/allocated", "action/requested", "action/completed", "prediction/checked"
    ]

    # Verify ledger integrity: each event has prev_hash = previous event_hash (or genesis)
    ledger = open(result["ledger"], encoding="utf-8").read().splitlines()
    assert len(ledger) == 9
    parsed = [json.loads(line) for line in ledger]
    for i, record in enumerate(parsed):
        if i == 0:
            assert record["prev_hash"] == "0" * 64
        else:
            assert record["prev_hash"] == parsed[i - 1]["event_hash"]
        assert record["seq"] == i + 1

    # Verify type distribution
    types = [r["event_type"] for r in parsed]
    assert types[0] == "observation/recorded"
    assert types[1] == "percept/derived"
    # 3 events per tool_call x 2 tool_calls
    assert types.count("attention/allocated") == 2
    assert types.count("action/requested") == 2
    assert types.count("action/completed") == 2
    # Final prediction
    assert types[-1] == "prediction/checked"


def test_extended_no_tool_calls_no_truth_two_events_only(tmp_path):
    """Without tool_calls AND without reward truth, extended mode emits exactly 2 events."""
    workspace = tmp_path / "ws"
    state, bundle, _ = _write_episode(workspace, with_tool_calls=False, with_truth=False)
    result = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                            extended=True)
    assert result["event_count"] == 2


def test_extended_no_reward_truth_no_prediction_event(tmp_path):
    """When reward_vector.values.truth is missing, no prediction/checked event."""
    workspace = tmp_path / "ws"
    state, bundle, _ = _write_episode(workspace, with_tool_calls=True, with_truth=False)
    result = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                            extended=True)
    # 2 base + 3*2 = 8 events, no prediction
    assert result["event_count"] == 8, f"expected 8 events, got {result['event_count']}"
    ledger = open(result["ledger"], encoding="utf-8").read().splitlines()
    types = [json.loads(line)["event_type"] for line in ledger]
    assert "prediction/checked" not in types


def test_extended_mirror_idempotent(tmp_path):
    """Second call returns already_mirrored with same mirror_id and event_count."""
    workspace = tmp_path / "ws"
    state, bundle, _ = _write_episode(workspace)
    first = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                           extended=True)
    second = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                            extended=True)
    assert first["mirror_id"] == second["mirror_id"]
    assert first["event_count"] == second["event_count"] == 9
    assert second["status"] == "already_mirrored"


def test_extended_rejects_non_authoritative_state_path(tmp_path):
    """State at non-canonical path must be rejected, same as original mirror."""
    workspace = tmp_path / "ws"
    state, bundle, _ = _write_episode(workspace)
    copied = tmp_path / "copied.json"
    copied.write_bytes((bundle / "state.json").read_bytes())
    with pytest.raises(ValueError, match="authoritative"):
        mirror_episode_state_extended(str(workspace), copied, extended=True)


def test_extended_rejects_missing_fields(tmp_path):
    """Episode without reduced_at or with bad instance_id must be rejected."""
    workspace = tmp_path / "ws"
    state, bundle, state_path = _write_episode(workspace)
    # Strip reduced_at
    bad = {**state, "reduced_at": ""}
    (bundle / "state.json").write_text(json.dumps(bad, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    with pytest.raises(ValueError, match="reduced_at"):
        mirror_episode_state_extended(str(workspace), state_path, extended=True)


def test_extended_tampered_ledger_raises_conflict(tmp_path):
    """If existing extended ledger bytes differ from regenerated, raise ValueError."""
    workspace = tmp_path / "ws"
    state, bundle, _ = _write_episode(workspace)
    result = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                            extended=True)
    ledger_path = result["ledger"]
    # Tamper
    with open(ledger_path, "ab") as f:
        f.write(b'{"tampered": true}\n')
    with pytest.raises(ValueError, match="conflicts"):
        mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                       extended=True)


def test_extended_recovery_from_corrupted_ledger(tmp_path):
    """Removing corrupted mirror artifacts allows clean re-mirror.

    Recovery requires removing three things:
    - ledger (events.jsonl)
    - mirror bundle (bundle.json)
    - the cognition_shadow import destination (the <import_id>.json archive)
    The imports.jsonl is append-only and is NOT cleared.
    """
    workspace = tmp_path / "ws"
    state, bundle, _ = _write_episode(workspace)
    result = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                            extended=True)
    ledger_path = result["ledger"]
    bundle_path = result["bundle"]
    os.remove(ledger_path)
    os.remove(bundle_path)
    # The import destination file (not imports.jsonl which is append-only)
    shadow_dir = workspace / "share/mind/governance/cognition_shadow"
    import_dest = shadow_dir / f"{result['import_id']}.json"
    if import_dest.exists():
        os.remove(import_dest)
    second = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                            extended=True)
    assert second["status"] == "mirrored"
    assert second["event_count"] == 9
    # New ledger must hash to bundle's recorded sha256
    ledger_bytes = open(second["ledger"], "rb").read()
    bundle_json = json.loads(open(second["bundle"], encoding="utf-8").read())
    import hashlib
    assert bundle_json["ledger"]["sha256"] == hashlib.sha256(ledger_bytes).hexdigest()


def test_extended_bundle_marks_extended_flag(tmp_path):
    """Extended bundle keeps validator-compatible kind but adds extended=True flag."""
    workspace = tmp_path / "ws"
    state, bundle, _ = _write_episode(workspace)
    result = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                            extended=True)
    bundle_json = json.loads(open(result["bundle"], encoding="utf-8").read())
    # Kind must remain compatible with the existing shadow bundle validator
    assert bundle_json["kind"] == "partner_cognition_shadow"
    assert bundle_json["extended"] is True
    assert bundle_json["candidate_registration_allowed"] is False
    assert bundle_json["production_mutation_allowed"] is False
    assert bundle_json["manual_stable_override"] is False
    # mirror_id must end with :ext to distinguish from the 2-event mirror
    assert result["mirror_id"].endswith("_ext")


def test_extended_reduced_state_has_extended_marker(tmp_path):
    """Reduced state must carry extended=True for downstream consumers."""
    workspace = tmp_path / "ws"
    state, bundle, _ = _write_episode(workspace)
    result = mirror_episode_state_extended(str(workspace), bundle / "state.json",
                                            extended=True)
    bundle_json = json.loads(open(result["bundle"], encoding="utf-8").read())
    rs = bundle_json["reduced_state"]
    assert rs["extended"] is True
    assert len(rs["attention"]) == 2
    assert len(rs["actions"]) == 2
    assert len(rs["predictions"]) == 1
    # Observation and percept remain in canonical form
    assert len(rs["observations"]) == 1
    assert len(rs["percepts"]) == 1
