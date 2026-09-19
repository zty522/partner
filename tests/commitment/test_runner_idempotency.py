"""Invariants 5 + 12: settlement is idempotent and a runner never floods."""
from __future__ import annotations

import pytest

from partner.commitment import models as M
from conftest import ScriptedExecutor, build_bet, make_config


def test_full_bet_settles_supported_and_closes(workspace):
    runner, store, executor = build_bet(workspace)
    result = runner.run()
    assert result.state == "CLOSED", result.reason
    assert result.settlement.settlement_class == "supported"
    assert result.settlement.improvement_over_baseline is True
    # a synthetic-fixture episode may be supported and still never publishable
    assert result.settlement.publish_eligible is False
    assert "synthetic_fixture_only" in result.settlement.publish_blockers
    assert result.experience is not None
    assert executor.executions == 1
    # the chain and the materialised manifest must agree
    assert store.verify_chain().ok
    assert store.verify_manifest()["ok"] is True
    assert store.load_lifecycle().state == "CLOSED"


def test_rerun_after_terminal_is_a_noop(workspace):
    runner, store, executor = build_bet(workspace)
    first = runner.run()
    assert executor.executions == 1
    events_after_first = len(store.events())

    again, _, _ = build_bet(workspace)
    second = again.run()
    assert second.replayed is True
    assert second.settlement.settlement_id == first.settlement.settlement_id
    assert executor.executions == 1, "a terminal bet must not execute again"
    assert len(store.events()) == events_after_first, "a replay must not append to the chain"
    assert len(store.list_artifacts("experience")) == 1, "a replay must not mint a second experience"


def test_repeat_lifecycle_event_does_not_duplicate(workspace):
    """Replaying the same Event Fabric message must not append a second record."""
    _, store, _ = build_bet(workspace)
    first = store.append("lifecycle", {"state": "PROPOSED"}, event_id="lifecycle:PROPOSED:r1")
    again = store.append("lifecycle", {"state": "PROPOSED"}, event_id="lifecycle:PROPOSED:r1")
    assert first["hash"] == again["hash"]
    assert len(store.events()) == 1


def test_concurrent_settlement_has_exactly_one_winner(workspace):
    """Two settlement owners race; the store lets exactly one through."""
    _, store, _ = build_bet(workspace)
    claim_a = store.append("owner_claim", {"owner": "owner-a"},
                           event_id="settlement_claim:settle_shared")
    claim_b = store.append("owner_claim", {"owner": "owner-b"},
                           event_id="settlement_claim:settle_shared")
    assert claim_a["payload"]["owner"] == "owner-a"
    assert claim_b["payload"]["owner"] == "owner-a"
    winners = [row for row in store.events() if row["event_id"] == "settlement_claim:settle_shared"]
    assert len(winners) == 1


def test_a_second_runner_cannot_claim_the_same_revision(workspace):
    _, store, _ = build_bet(workspace)
    store.append("owner_claim", {"owner": "runner-a"}, event_id="execution_claim:r1")
    second, _, _ = build_bet(workspace, owner_id="runner-b")
    claimed = second._claim("execution_claim:r1")
    assert claimed is False


def test_second_experience_is_refused_by_the_state_machine(workspace):
    """Invariant 5: a settled bet cannot settle again, so a second experience is unreachable."""
    from partner.commitment import state_machine as sm
    from partner.commitment.state_machine import IllegalTransition
    runner, store, _ = build_bet(workspace)
    runner.run()
    lifecycle = store.load_lifecycle()
    assert lifecycle.experience_emitted is True
    with pytest.raises(IllegalTransition):
        sm.advance(lifecycle, sm.TransitionRequest(
            target=sm.SETTLED, now=1.0,
            measurement=store.load_measurement(store.list_artifacts("measurement")[0]),
            settlement=store.load_settlement(lifecycle.settlement_id)))
    assert len(store.list_artifacts("experience")) == 1
