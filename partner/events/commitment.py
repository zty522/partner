"""Event Fabric binding for the commitment kernel.

Division of responsibility, deliberately strict:

* **Event Fabric** owns scheduling, retry, recovery and delivery.  It decides
  *when* something runs.
* **The commitment kernel** owns semantics: what a bet means, when it may be
  measured, what a result settles to.  It decides *what is true*.

So the Events here are thin.  They load a declarative bet spec, hand it to the
kernel runner, and project the resulting terminal state back into the Event
summary.  They never write a lifecycle state themselves -- the state machine is
the only writer -- and they never seed a follow-up Event: a bet that reaches a
terminal state ends, and starting another one is a separate, explicit decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from partner.event_fabric.catalog import EventDefinition

from partner.commitment import state_machine as sm
from partner.application.commitment_adapter import build_runner

#: Kernel lifecycle states projected onto Event names.  Used by callers (and by
#: a future readiness gate) to decide which Event is meaningful next; it is a
#: mapping, not a scheduler.
KERNEL_STATE_EVENTS: Mapping[str, str] = {
    sm.DRAFT: "commitment.bet_open",
    sm.PROPOSED: "commitment.bet_commit",
    sm.COMMITTED: "commitment.bet_execute",
    sm.EXECUTING: "commitment.bet_execute",
    sm.MEASURED: "commitment.bet_settle",
    sm.SETTLED: "commitment.bet_settle",
    sm.CLOSED: "commitment.bet_state",
    sm.INVALID: "commitment.bet_state",
    sm.BLOCKED: "commitment.bet_state",
    sm.BUDGET_EXHAUSTED: "commitment.bet_state",
    sm.CANCELLED: "commitment.bet_state",
}


def map_kernel_state(state: str) -> str:
    """The Event name that would advance a bet in ``state``."""
    return KERNEL_STATE_EVENTS.get(state, "commitment.bet_state")


def _load_spec(params: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(params.get("bet_spec"), dict):
        return dict(params["bet_spec"])
    path = params.get("bet_spec_path")
    if not path:
        raise ValueError("commitment Event requires params.bet_spec or params.bet_spec_path")
    return json.loads(Path(str(path)).read_text(encoding="utf-8"))


def _project(ctx, bet_id: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    from partner.commitment.store import CommitmentStore
    store = CommitmentStore(Path(ctx.workspace), str(spec["run_id"]), bet_id)
    lifecycle = store.load_lifecycle()
    return {"state": lifecycle.state, "revision": lifecycle.revision,
            "settled": lifecycle.settled, "settled_class": lifecycle.settled_class,
            "experience_emitted": lifecycle.experience_emitted,
            "chain": store.verify_chain().to_dict(),
            "next_event": map_kernel_state(lifecycle.state)}


def commitment_bet_run(ctx, params):
    """Run one bounded bet to a terminal state (idempotent on replay)."""
    spec = _load_spec(params)
    bet_id = str(spec["bet_id"])
    runner = build_runner(Path(ctx.workspace), spec, use_llm=bool(params.get("use_llm")),
                          llm_max_calls=int(params.get("llm_max_calls") or 2))
    result = runner.run()
    projection = _project(ctx, bet_id, spec)
    settlement = result.settlement
    return {
        "ok": sm.is_terminal(result.state),
        "status": result.state,
        "summary": f"commitment bet {bet_id} -> {result.state}: {result.reason}",
        "semantic_output": {
            "bet_id": bet_id,
            "state": result.state,
            "settlement_class": None if settlement is None else settlement.settlement_class,
            "improvement_observed": None if settlement is None else settlement.improvement_observed,
            "publish_eligible": None if settlement is None else settlement.publish_eligible,
            "replayed": result.replayed,
            "artifacts": dict(result.paths),
            "projection": projection,
        },
        "files": [path for path in result.paths.values() if isinstance(path, str)],
        "evidence_refs": [] if settlement is None else [settlement.settlement_id],
        "token_usage": dict(runner.proposer.transcript[-1].get("usage") or {})
                        if getattr(runner.proposer, "transcript", None) else {},
    }


def commitment_bet_state(ctx, params):
    """Read-only projection of one bet's lifecycle.  Writes nothing."""
    spec = _load_spec(params)
    projection = _project(ctx, str(spec["bet_id"]), spec)
    return {
        "ok": projection["chain"]["ok"],
        "status": projection["state"],
        "summary": f"commitment bet {spec['bet_id']} is {projection['state']}",
        "semantic_output": projection, "files": [], "evidence_refs": [], "token_usage": {},
    }


def commitment_bet_settlement(ctx, params):
    """Read-only projection of a bet's settlement and experience."""
    from partner.commitment.store import CommitmentStore
    spec = _load_spec(params)
    store = CommitmentStore(Path(ctx.workspace), str(spec["run_id"]), str(spec["bet_id"]))
    lifecycle = store.load_lifecycle()
    settlement = store.load_settlement(lifecycle.settlement_id) if lifecycle.settlement_id else None
    experiences = [store.load_experience(identifier)
                   for identifier in store.list_artifacts("experience")]
    return {
        "ok": True,
        "status": lifecycle.state,
        "summary": (f"bet {spec['bet_id']} settled as "
                    f"{None if settlement is None else settlement.settlement_class}"),
        "semantic_output": {
            "settlement": None if settlement is None else settlement.to_dict(),
            "experiences": [e.to_dict() for e in experiences],
        },
        "files": [], "evidence_refs": [], "token_usage": {},
    }


DEFINITIONS = [
    EventDefinition(
        "commitment.bet_run", "commitment",
        "Run one bounded commitment bet to a terminal state (idempotent on replay)",
        commitment_bet_run, execution_method="local", produces_artifact=True,
        timeout_seconds=900, concurrency_scope="project"),
    EventDefinition(
        "commitment.bet_state", "commitment",
        "Read-only lifecycle projection of one commitment bet",
        commitment_bet_state, execution_method="local", timeout_seconds=60),
    EventDefinition(
        "commitment.bet_settlement", "commitment",
        "Read-only settlement and experience projection of one commitment bet",
        commitment_bet_settlement, execution_method="local", timeout_seconds=60),
]

__all__ = ["DEFINITIONS", "KERNEL_STATE_EVENTS", "map_kernel_state", "commitment_bet_run",
           "commitment_bet_state", "commitment_bet_settlement"]
