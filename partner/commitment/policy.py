"""Budget and stop/turn policy.  Machine rules only.

The policy answers three questions and nothing else:

* may this bet spend another model call / action / round? (absolute deadline,
  cumulative counters -- never reset by a phase change)
* may this bet change direction now?
* after a settlement, does the bet close, or is one more bounded round created?
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .models import Budget, BudgetUsage, CommitmentPolicy
from .state_machine import (
    BUDGET_EXHAUSTED, CLOSED, BetLifecycle, assert_turn_allowed, budget_exhausted,
    deadline_passed,
)


@dataclass
class SpendDecision:
    allowed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": bool(self.allowed), "reason": self.reason}


class BudgetLedger:
    """Cumulative budget accounting that survives process restarts.

    ``usage`` is loaded from persisted state, never reconstructed by counting
    events in the current process, so a restart cannot refund spent calls.
    """

    def __init__(self, budget: Budget, usage: BudgetUsage, *, clock) -> None:
        self._budget = budget
        self._usage = usage
        self._clock = clock

    @property
    def budget(self) -> Budget:
        return self._budget

    @property
    def usage(self) -> BudgetUsage:
        return self._usage

    def exhausted_reason(self) -> str:
        return budget_exhausted(self._budget, self._usage, now=self._clock.now())

    def may_spend(self, *, model_calls: int = 0, actions: int = 0, rounds: int = 0) -> SpendDecision:
        """May the requested resources be spent?

        Checked per resource plus the absolute deadline.  A global "is everything
        gone?" test would be wrong here: spending the last model call must not
        forbid the one execution that call was made for.
        """
        if deadline_passed(self._budget, now=self._clock.now()):
            return SpendDecision(False, "deadline_exceeded")
        for resource, amount in (("model_calls", model_calls), ("actions", actions),
                                ("rounds", rounds)):
            if int(amount) <= 0:
                continue
            if int(getattr(self._usage, resource)) + int(amount) > int(getattr(self._budget, resource)):
                return SpendDecision(False, f"{resource}_exhausted")
        return SpendDecision(True, "within budget")

    def charge(self, *, model_calls: int = 0, actions: int = 0, rounds: int = 0) -> None:
        self._usage.model_calls = int(self._usage.model_calls) + int(model_calls)
        self._usage.actions = int(self._usage.actions) + int(actions)
        self._usage.rounds = int(self._usage.rounds) + int(rounds)

    def snapshot(self) -> dict[str, Any]:
        return {"usage": self._usage.to_dict(),
                "budget": self._budget.to_dict(),
                "deadline_remaining_s": round(float(self._budget.deadline_epoch) - self._clock.now(), 3),
                "exhausted": self.exhausted_reason()}


def turn_decision(policy: CommitmentPolicy, lifecycle: BetLifecycle, *,
                  new_evidence_refs: Sequence[str]) -> SpendDecision:
    allowed, reason = assert_turn_allowed(policy, lifecycle, new_evidence_refs=new_evidence_refs)
    return SpendDecision(allowed, reason)


def post_settlement_decision(*, settlement_class: str, lifecycle: BetLifecycle,
                             budget: Budget, usage: BudgetUsage, max_rounds: int,
                             clock) -> tuple[str, str]:
    """Return ``(next_state, reason)`` after a settlement.

    Default is to stop.  A further bounded round is created only when the
    settled bet produced a usable negative or inconclusive result, the round
    budget is not spent, and the caller explicitly asked for more rounds.  A
    'supported' settlement closes: the direction is established, and re-running
    it would be the 'invalid iteration without new evidence' the kernel forbids.
    """
    if settlement_class == "supported":
        return CLOSED, "direction supported; stop and keep the verified direction"
    if settlement_class in ("invalid", "blocked"):
        return CLOSED, f"settlement_class={settlement_class}; no further round is meaningful"
    if settlement_class == "abstained":
        # Not a negative result and not an inconclusive one: the bet never ran, so no
        # further round can follow from it without new evidence.
        return CLOSED, "abstention recorded; no action was executed and no round is created"
    if settlement_class == "falsified":
        if int(usage.rounds) >= int(max_rounds):
            return CLOSED, "round budget exhausted after falsification"
        if budget_exhausted(budget, usage, now=clock.now()):
            return BUDGET_EXHAUSTED, "budget exhausted after falsification"
        return CLOSED, "falsification recorded; a new bet must be created explicitly"
    # inconclusive
    if int(usage.rounds) >= int(max_rounds):
        return CLOSED, "round budget exhausted after an inconclusive measurement"
    return CLOSED, "inconclusive recorded; stopping rather than repeating without new evidence"


__all__ = ["SpendDecision", "BudgetLedger", "turn_decision", "post_settlement_decision"]
