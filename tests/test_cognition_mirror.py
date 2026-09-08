from __future__ import annotations

import json

from partner.governance.cognition_mirror import (
    mirror_episode_state,
    mirror_reduced_episode,
    try_mirror_reduced_episode,
)


def _episode(workspace, *, enabled=False):
    (workspace / "config").mkdir(parents=True)
    (workspace / "config/partner_config.json").write_text(json.dumps({
        "runtime": {"mode": "manual_stable", "cognition_shadow_mirror": enabled},
    }), encoding="utf-8")
    state = {
        "schema_version": 3, "episode_id": "episode_gate_b", "task_id": "task_gate_b",
        "instance_id": "04", "project_id": "literature-learning", "status": "done",
        "failure_classes": [], "reward_vector": {"policy_eligible": True, "scalar": 0.9},
        "reduced_at": "2026-08-28T12:00:00+08:00",
    }
    bundle = workspace / "share/mind/governance/episodes/episode_gate_b"
    bundle.mkdir(parents=True)
    state_path = bundle / "state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state, bundle, state_path


def test_runtime_mirror_is_disabled_and_has_no_side_effects(tmp_path):
    workspace = tmp_path / "workspace"
    state, bundle, _ = _episode(workspace)
    result = try_mirror_reduced_episode(str(workspace), {"ok": True, "bundle": str(bundle), "state": state})
    assert result == {"ok": True, "status": "disabled", "mirrored": False,
                      "production_mutation": False, "candidate_registered": False}
    assert not (workspace / "share/mind/governance/cognition_mirror").exists()


def test_enabled_runtime_mirror_is_native_shadow_and_idempotent(tmp_path):
    workspace = tmp_path / "workspace"
    state, bundle, _ = _episode(workspace, enabled=True)
    trace = {"ok": True, "bundle": str(bundle), "state": state}
    first = try_mirror_reduced_episode(str(workspace), trace)
    second = try_mirror_reduced_episode(str(workspace), trace)
    assert first["status"] == "mirrored"
    assert second["status"] == "already_mirrored"
    assert first["mirror_id"] == second["mirror_id"]
    ledger = open(first["ledger"], encoding="utf-8").read().splitlines()
    assert len(ledger) == 2
    assert json.loads(ledger[0])["event_type"] == "observation/recorded"
    assert json.loads(ledger[1])["event_type"] == "percept/derived"
    imports = workspace / "share/mind/governance/cognition_shadow/imports.jsonl"
    assert len(imports.read_text(encoding="utf-8").splitlines()) == 1
    assert not (workspace / "share/mind/governance/experience_guided_policy/candidate_skills").exists()


def test_runtime_mirror_fails_open_on_mismatched_reducer_state(tmp_path):
    workspace = tmp_path / "workspace"
    state, bundle, _ = _episode(workspace, enabled=True)
    changed = {**state, "status": "invented"}
    result = try_mirror_reduced_episode(str(workspace), {"ok": True, "bundle": str(bundle), "state": changed})
    assert result["ok"] is False
    assert result["status"] == "mirror_best_effort_failed"
    assert "does not match" in result["error"]


def test_explicit_mirror_requires_authoritative_episode_path(tmp_path):
    workspace = tmp_path / "workspace"
    _, _, state_path = _episode(workspace)
    copied = tmp_path / "copied_state.json"
    copied.write_bytes(state_path.read_bytes())
    try:
        mirror_episode_state(str(workspace), copied)
    except ValueError as exc:
        assert "authoritative" in str(exc)
    else:
        raise AssertionError("non-authoritative state path was accepted")


def test_explicit_gate_b_does_not_require_runtime_enablement(tmp_path):
    workspace = tmp_path / "workspace"
    state, bundle, _ = _episode(workspace)
    result = mirror_reduced_episode(str(workspace), {"ok": True, "bundle": str(bundle), "state": state})
    assert result["status"] == "mirrored"
    assert result["production_mutation"] is False
    assert result["candidate_registered"] is False
