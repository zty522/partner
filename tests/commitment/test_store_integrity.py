"""Invariant 12: the append-only hash chain is tamper-evident and fails closed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from partner.commitment.store import CommitmentStore, GENESIS_HASH, StoreIntegrityError
from conftest import build_bet


def test_chain_links_and_verifies(workspace):
    _, store, _ = build_bet(workspace)
    store.append("a", {"n": 1}, event_id="e1")
    store.append("b", {"n": 2}, event_id="e2")
    report = store.verify_chain()
    assert report.ok and report.length == 2
    rows = store.events()
    assert rows[0]["prev_hash"] == GENESIS_HASH
    assert rows[1]["prev_hash"] == rows[0]["hash"]


def test_tampering_is_detected_and_recorded(workspace):
    _, store, _ = build_bet(workspace)
    store.append("a", {"n": 1}, event_id="e1")
    store.append("b", {"n": 2}, event_id="e2")
    lines = store.events_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["payload"] = {"n": 999}          # rewrite history in place
    lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
    store.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = store.verify_chain()
    assert report.ok is False
    assert report.broken_at == 1
    assert "hash mismatch" in report.reason
    assert [row["kind"] for row in store.issues()] == ["chain_hash_mismatch"]


def test_broken_chain_refuses_new_writes_and_never_rewrites_history(workspace):
    from partner.commitment.state_machine import BetLifecycle
    _, store, _ = build_bet(workspace)
    store.append("a", {"n": 1}, event_id="e1")
    store.append("b", {"n": 2}, event_id="e2")
    lines = store.events_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["kind"] = "rewritten"
    lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
    store.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = store.events_path.read_text(encoding="utf-8")

    with pytest.raises(StoreIntegrityError, match="broken chain"):
        store.save_lifecycle(BetLifecycle(bet_id=store.bet_id, state="PROPOSED"))
    assert store.events_path.read_text(encoding="utf-8") == before
    assert "refused_write_on_broken_chain" in [row["kind"] for row in store.issues()]


def test_recovery_replays_the_chain_when_state_is_lost(workspace):
    runner, store, _ = build_bet(workspace)
    runner.run()
    state_path = store.path("state.json")
    expected = store.load_lifecycle()
    state_path.unlink()

    recovered = store.load_lifecycle()
    assert recovered.state == expected.state
    assert recovered.revision == expected.revision
    assert recovered.settlement_id == expected.settlement_id
    assert recovered.experience_emitted == expected.experience_emitted


def test_manifest_detects_a_tampered_artifact(workspace):
    runner, store, _ = build_bet(workspace)
    runner.run()
    assert store.verify_manifest()["ok"] is True
    victim = store.artifact_path("settlement", store.load_lifecycle().settlement_id)
    payload = json.loads(victim.read_text(encoding="utf-8"))
    payload["settlement_class"] = "supported"
    victim.write_text(json.dumps(payload), encoding="utf-8")

    report = store.verify_manifest()
    assert report["ok"] is False
    assert any("settlement" in name for name in report["manifest_mismatches"])


def test_unreadable_state_fails_closed(workspace):
    runner, store, _ = build_bet(workspace)
    runner.run()
    store.path("state.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreIntegrityError):
        store.load_lifecycle()
    assert "state_unreadable" in [row["kind"] for row in store.issues()]
