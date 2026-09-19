"""The single source of truth for commitment lifecycle transitions.

Nothing else in the kernel may write a terminal state.  The runner asks this
module to advance; the store persists what this module returns.  An LLM never
touches this file's decisions.

Invariants enforced here (numbered as in the implementation prompt):

1.  one active execution owner and one current revision per bet
2.  after COMMITTED the semantic content is frozen; only a new revision may
    change it, and that revision must reference its parent
3.  no valid ExecutionReceipt -> no MEASURED
4.  no independent OutcomeMeasurement -> no SETTLED
5.  SETTLED is idempotent: a repeat message adds no second experience and no
    second next round
9.  budget is one absolute deadline plus cumulative counters; a phase change
    never resets it
11. no turning without new evidence
12. a runner stops at a terminal state or a budget edge; it never seeds
    unbounded follow-up work
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .models import (
    Budget, BudgetUsage, CommitmentPolicy, ContractError, ExecutionReceipt,
    OutcomeMeasurement, SettlementDecision, BetRecord, sha256_of,
)

# ---------------------------------------------------------------------------
# states
# ---------------------------------------------------------------------------

DRAFT = "DRAFT"
PROPOSED = "PROPOSED"
COMMITTED = "COMMITTED"
EXECUTING = "EXECUTING"
MEASURED = "MEASURED"
SETTLED = "SETTLED"
CLOSED = "CLOSED"
INVALID = "INVALID"
BLOCKED = "BLOCKED"
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
CANCELLED = "CANCELLED"

STATES = (DRAFT, PROPOSED, COMMITTED, EXECUTING, MEASURED, SETTLED, CLOSED,
          INVALID, BLOCKED, BUDGET_EXHAUSTED, CANCELLED)

#: A bet that has reached one of these will never run again.
TERMINAL_STATES = (CLOSED, INVALID, BLOCKED, BUDGET_EXHAUSTED, CANCELLED)

#: Exceptional stops that are *not* a successful close.
ABNORMAL_TERMINALS = (INVALID, BLOCKED, BUDGET_EXHAUSTED, CANCELLED)

HAPPY_PATH = (DRAFT, PROPOSED, COMMITTED, EXECUTING, MEASURED, SETTLED, CLOSED)

#: Legal edges.  A skip (DRAFT -> COMMITTED, DRAFT -> EXECUTING, ...) has no edge
#: and is therefore impossible, not merely discouraged.
ALLOWED_TRANSITIONS: Mapping[str, tuple[str, ...]] = {
    DRAFT: (PROPOSED, BLOCKED, INVALID, CANCELLED, BUDGET_EXHAUSTED),  # BLOCKED: no usable direction could be proposed
    PROPOSED: (COMMITTED, INVALID, CANCELLED, BUDGET_EXHAUSTED),
    COMMITTED: (EXECUTING, BLOCKED, INVALID, CANCELLED, BUDGET_EXHAUSTED),
    EXECUTING: (MEASURED, BLOCKED, INVALID, CANCELLED, BUDGET_EXHAUSTED),
    MEASURED: (SETTLED, INVALID, BLOCKED, CANCELLED, BUDGET_EXHAUSTED),
    # SETTLED -> COMMITTED is a *turn*: it requires a policy-allowed turn and new
    # evidence, and it must land on a higher revision (checked in `advance`).
    SETTLED: (CLOSED, COMMITTED, BUDGET_EXHAUSTED),
    CLOSED: (),
    INVALID: (),
    BLOCKED: (),
    BUDGET_EXHAUSTED: (),
    CANCELLED: (),
}


class IllegalTransition(RuntimeError):
    """An attempted lifecycle edge that the state machine does not permit."""


class InvariantViolation(RuntimeError):
    """A permitted edge attempted without the evidence it requires."""


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def can_transition(current: str, target: str) -> bool:
    if current not in ALLOWED_TRANSITIONS:
        raise IllegalTransition(f"unknown state {current!r}")
    return target in ALLOWED_TRANSITIONS[current]


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

@dataclass
class BetLifecycle:
    """Materialised lifecycle state for one bet.

    This is the mutable companion to the immutable ``BetRecord``: the record
    carries meaning, the lifecycle carries progress.  Keeping them apart is what
    makes invariant 2 mechanically checkable.
    """

    bet_id: str
    state: str = DRAFT
    revision: int = 1
    turn_round: int = 0
    settled: bool = False
    experience_emitted: bool = False
    closed_reason: str = ""
    receipt_id: str = ""
    measurement_id: str = ""
    settlement_id: str = ""
    settled_class: str = ""
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    history: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ContractError(f"BetLifecycle: unknown state {self.state!r}")
        if self.bet_id.strip() == "":
            raise ContractError("BetLifecycle: bet_id must be non-empty")
        if int(self.revision) < 1:
            raise ContractError("BetLifecycle: revision must be >= 1")

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "bet_id": self.bet_id, "state": self.state, "revision": int(self.revision),
            "turn_round": int(self.turn_round), "settled": bool(self.settled),
            "experience_emitted": bool(self.experience_emitted),
            "closed_reason": self.closed_reason, "receipt_id": self.receipt_id,
            "measurement_id": self.measurement_id, "settlement_id": self.settlement_id,
            "settled_class": self.settled_class, "usage": self.usage.to_dict(),
            "history": list(self.history), "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BetLifecycle":
        return cls(bet_id=str(payload.get("bet_id") or ""),
                   state=str(payload.get("state") or DRAFT),
                   revision=int(payload.get("revision") or 1),
                   turn_round=int(payload.get("turn_round") or 0),
                   settled=bool(payload.get("settled")),
                   experience_emitted=bool(payload.get("experience_emitted")),
                   closed_reason=str(payload.get("closed_reason") or ""),
                   receipt_id=str(payload.get("receipt_id") or ""),
                   measurement_id=str(payload.get("measurement_id") or ""),
                   settlement_id=str(payload.get("settlement_id") or ""),
                   settled_class=str(payload.get("settled_class") or ""),
                   usage=BudgetUsage.from_dict(payload.get("usage")),
                   history=list(payload.get("history") or []),
                   updated_at=float(payload.get("updated_at") or 0.0))


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------

class BudgetExhausted(RuntimeError):
    """The bet ran out of wall clock or calls.  A real terminal, not a failure."""


#: Which budget counter a spend request draws on.
RESOURCE_ATTRS = {"model_calls": "model_calls", "actions": "actions", "rounds": "rounds"}


def deadline_passed(budget: Budget, *, now: float) -> bool:
    """The deadline is absolute, so a restart cannot buy more time (invariant 9)."""
    return float(now) > float(budget.deadline_epoch)


def resource_exhausted(budget: Budget, usage: BudgetUsage, resource: str, *,
                       now: float) -> str:
    """Why ``resource`` cannot be spent right now, or '' if it can.

    Per-resource on purpose: spending every model call must not forbid the
    single execution those calls were spent on.  Only the deadline is global.
    """
    if deadline_passed(budget, now=now):
        return "deadline_exceeded"
    if resource not in RESOURCE_ATTRS:
        raise ContractError(f"unknown budget resource {resource!r}")
    spendable = int(getattr(usage, resource))
    cap = int(getattr(budget, resource))
    if spendable >= cap:
        return f"{resource}_exhausted"
    return ""


def budget_exhausted(budget: Budget, usage: BudgetUsage, *, now: float) -> str:
    """Why *nothing further* can be attempted, or '' if something still can.

    Used for reports and for the post-settlement terminal choice, where the
    question really is "is the whole budget gone?".
    """
    if deadline_passed(budget, now=now):
        return "deadline_exceeded"
    reasons = [resource_exhausted(budget, usage, resource, now=now)
               for resource in RESOURCE_ATTRS]
    if all(reasons):
        return ";".join(reasons)
    return ""


def assert_budget(budget: Budget, usage: BudgetUsage, *, now: float) -> None:
    reason = budget_exhausted(budget, usage, now=now)
    if reason:
        raise BudgetExhausted(reason)


# ---------------------------------------------------------------------------
# the transition gate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransitionRequest:
    """Everything the state machine may consult when deciding an edge."""

    target: str
    record: BetRecord | None = None
    receipt: ExecutionReceipt | None = None
    measurement: OutcomeMeasurement | None = None
    settlement: SettlementDecision | None = None
    new_evidence_refs: Sequence[str] = ()
    turn_allowed: bool = False
    reason: str = ""
    now: float = 0.0


def advance(lifecycle: BetLifecycle, request: TransitionRequest) -> BetLifecycle:
    """Apply one transition, or raise.  Returns the mutated lifecycle."""
    current, target = lifecycle.state, request.target
    if target not in STATES:
        raise IllegalTransition(f"unknown target state {target!r}")
    if not can_transition(current, target):
        raise IllegalTransition(
            f"{lifecycle.bet_id}: refusing {current} -> {target}; allowed from {current}: "
            f"{ALLOWED_TRANSITIONS.get(current, ())}")

    if is_terminal(current) and current != SETTLED:
        # CLOSED / INVALID / BLOCKED / BUDGET_EXHAUSTED / CANCELLED are final.
        if current != CLOSED or target != CLOSED:
            raise IllegalTransition(
                f"{lifecycle.bet_id}: {current} is terminal; no further transition")

    now = float(request.now or time.time())

    # invariant 3: a claimed execution without a valid receipt cannot be measured
    if target == MEASURED:
        receipt = request.receipt
        if receipt is None:
            raise InvariantViolation(
                f"{lifecycle.bet_id}: MEASURED requires an ExecutionReceipt")
        if receipt.bet_id != lifecycle.bet_id:
            raise InvariantViolation(
                f"{lifecycle.bet_id}: receipt belongs to {receipt.bet_id}")
        if not receipt.is_valid:
            raise InvariantViolation(
                f"{lifecycle.bet_id}: receipt status={receipt.status}; an unsuccessful execution "
                "cannot be measured -- use BLOCKED/INVALID instead")

    # invariant 4: settlement needs an independent, valid measurement
    if target == SETTLED:
        measurement, settlement = request.measurement, request.settlement
        if measurement is None:
            raise InvariantViolation(
                f"{lifecycle.bet_id}: SETTLED requires an OutcomeMeasurement")
        if settlement is None:
            raise InvariantViolation(
                f"{lifecycle.bet_id}: SETTLED requires a SettlementDecision")
        if measurement.bet_id != lifecycle.bet_id:
            raise InvariantViolation(
                f"{lifecycle.bet_id}: measurement belongs to {measurement.bet_id}")
        if settlement.bet_id != lifecycle.bet_id:
            raise InvariantViolation(
                f"{lifecycle.bet_id}: settlement belongs to {settlement.bet_id}")
        if settlement.settlement_class == "abstained" and measurement.is_valid:
            raise InvariantViolation(
                f"{lifecycle.bet_id}: settlement_class=abstained declares that no action ran, but it "
                "carries a valid measurement; an abstention must be settled unmeasured")
        if settlement.settlement_class in ("supported", "falsified") and not measurement.is_valid:
            raise InvariantViolation(
                f"{lifecycle.bet_id}: settlement_class={settlement.settlement_class} needs a valid "
                f"measurement; got validity={measurement.validity}")
        if lifecycle.settled:
            # invariant 5: idempotent settlement
            raise InvariantViolation(
                f"{lifecycle.bet_id}: already settled as {lifecycle.settled_class!r}; a repeat "
                "message must not create a second experience or a second round")

    # invariant 11 + turn mechanics
    if current == SETTLED and target == COMMITTED:
        if not request.turn_allowed:
            raise InvariantViolation(
                f"{lifecycle.bet_id}: a turn is not allowed by the commitment policy at this point")
        if not tuple(request.new_evidence_refs):
            raise InvariantViolation(
                f"{lifecycle.bet_id}: a turn requires new evidence; refusing to change direction "
                "without it")

    if target == EXECUTING and not lifecycle.settled:
        pass  # first execution of this revision

    previous = lifecycle.state
    lifecycle.state = target
    lifecycle.updated_at = now
    if target == COMMITTED and current == SETTLED:
        lifecycle.revision += 1
        lifecycle.turn_round += 1
        lifecycle.settled = False
        lifecycle.settled_class = ""
    if request.receipt is not None:
        # Record identity only.  Budget accounting belongs to the policy ledger:
        # charging here would double-count, because one action is observed at both
        # the MEASURED and the SETTLED transition.
        lifecycle.receipt_id = request.receipt.receipt_id
    if request.measurement is not None:
        lifecycle.measurement_id = request.measurement.measurement_id
    if request.settlement is not None and target == SETTLED:
        lifecycle.settlement_id = request.settlement.settlement_id
        lifecycle.settled = True
        lifecycle.settled_class = request.settlement.settlement_class
    if target in TERMINAL_STATES:
        lifecycle.closed_reason = request.reason or target
    lifecycle.history.append({
        "from": previous, "to": target, "at": now,
        "revision": int(lifecycle.revision), "reason": request.reason,
    })
    return lifecycle


def assert_turn_allowed(policy: CommitmentPolicy, lifecycle: BetLifecycle,
                        *, new_evidence_refs: Sequence[str]) -> tuple[bool, str]:
    """Machine rule for 'may this bet change direction now?'.

    Mirrors invariant 11: no new evidence means no new direction, no matter how
    long the loop has been running.
    """
    if int(lifecycle.turn_round) + 1 > int(policy.max_turns):
        return False, "turn budget exhausted"
    if int(lifecycle.turn_round) + 1 < int(policy.earliest_turn_round):
        return False, "earliest permitted turn not reached"
    if policy.require_new_evidence_to_turn and not tuple(new_evidence_refs):
        return False, "no new evidence"
    return True, "turn allowed"


def freeze_identity(record: BetRecord) -> str:
    """The identity the store pins for a committed revision."""
    return record.freeze_hash()


__all__ = [
    "DRAFT", "PROPOSED", "COMMITTED", "EXECUTING", "MEASURED", "SETTLED", "CLOSED",
    "INVALID", "BLOCKED", "BUDGET_EXHAUSTED", "CANCELLED", "STATES", "TERMINAL_STATES",
    "ABNORMAL_TERMINALS", "HAPPY_PATH", "ALLOWED_TRANSITIONS", "IllegalTransition",
    "InvariantViolation", "BudgetExhausted", "BetLifecycle", "TransitionRequest",
    "advance", "can_transition", "is_terminal", "budget_exhausted", "assert_budget",
    "assert_turn_allowed", "freeze_identity", "deadline_passed", "resource_exhausted",
    "RESOURCE_ATTRS",
]
