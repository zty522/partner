"""Invariant 9: one absolute deadline plus cumulative counters, never reset."""
from __future__ import annotations

import pytest

from partner.commitment import models as M
from partner.commitment import state_machine as sm
from partner.commitment.policy import BudgetLedger
from partner.commitment.ports import FrozenClock
from conftest import ChargingProposer, ScriptedExecutor, build_bet, make_config


def test_ledger_deadline_is_absolute_and_never_resets():
    clock = FrozenClock(1_000.0)
    budget = M.Budget.create(wall_clock_seconds=100, model_calls=5, actions=5, rounds=2,
                            started_epoch=1_000.0)
    assert budget.deadline_epoch == 1_100.0
    ledger = BudgetLedger(budget, M.BudgetUsage(), clock=clock)
    assert ledger.may_spend(model_calls=1).allowed is True
    clock.advance(99)
    assert ledger.exhausted_reason() == ""
    clock.advance(2)  # past the absolute deadline
    assert ledger.exhausted_reason() == "deadline_exceeded"
    # a phase change is not a reset: the same ledger object cannot buy more time
    assert ledger.may_spend(actions=1).allowed is False


def test_spending_the_last_model_call_still_allows_the_execution(workspace):
    """Per-resource budget: the call budget is spent *on* the action, not instead of it.

    Two actions are needed because the baseline control run is itself an executed
    action (it is evidence, not a declared number).
    """
    clock = FrozenClock(1_700_000_000.0)
    config = make_config(model_calls=1, actions=2)
    proposer = ChargingProposer(model_calls=1)
    runner, store, executor = build_bet(workspace, config=config, clock=clock,
                                        proposer=proposer)
    result = runner.run()
    assert result.state == "CLOSED", result.reason
    assert result.settlement is not None
    assert executor.executions == 1, "the execution the call was spent on must still run"
    assert store.load_lifecycle().usage.model_calls == 1


def test_persisted_usage_is_not_refunded_across_a_restart(workspace):
    """A mid-flight restart must not hand back already-spent calls."""
    from partner.commitment.state_machine import BetLifecycle
    clock = FrozenClock(1_700_000_000.0)
    config = make_config(model_calls=1, actions=2)
    runner, store, _ = build_bet(workspace, config=config, clock=clock)
    # simulate a process that died after spending its single model call
    lifecycle = BetLifecycle(bet_id=store.bet_id, state=sm.COMMITTED,
                             usage=M.BudgetUsage(model_calls=1))
    store.save_lifecycle(lifecycle)

    recharging = ChargingProposer(model_calls=1)
    restarted, _, _ = build_bet(workspace, config=config, clock=FrozenClock(1_700_000_001.0),
                                proposer=recharging)
    result = restarted.run()
    assert result.state == sm.BUDGET_EXHAUSTED, result.reason
    assert recharging.calls == 0, "a spent budget must not buy another proposal after restart"
    assert store.load_lifecycle().usage.model_calls == 1


def test_runner_stops_at_budget_exhausted_without_side_effects(workspace):
    config = make_config(model_calls=0)
    runner, store, executor = build_bet(workspace, config=config)
    result = runner.run()
    assert result.state == sm.BUDGET_EXHAUSTED
    assert executor.executions == 0
    assert store.list_artifacts("experience") == []
    assert store.load_lifecycle().usage.model_calls == 0


def test_actions_budget_is_charged_once_per_execution(workspace):
    runner, store, _ = build_bet(workspace, config=make_config(model_calls=2, actions=3))
    runner.run()
    # two actions: the control (baseline) run and the candidate run
    assert store.load_lifecycle().usage.actions == 2
