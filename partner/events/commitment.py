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
import re
import time
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


#: A trace token is how an operator follows one message through the kernel.
_TRACE_RE = re.compile(r"runtime_trace[A-Za-z0-9_\-]*")


def _job_request(ctx) -> str:
    try:
        from partner.index.job_repository import init as _init_jobs
        record = _init_jobs(Path(ctx.workspace)).get_record(str(getattr(ctx, "job_id", ""))) or {}
        return str(record.get("request") or "")
    except Exception:  # noqa: BLE001 - a missing request is reported, never invented
        return ""


def _trace_token(ctx, params: Mapping[str, Any]) -> str:
    direct = str(params.get("trace_token") or "").strip()
    if direct:
        return direct
    for candidate in (params.get("message"), params.get("goal"), _job_request(ctx)):
        found = _TRACE_RE.search(str(candidate or ""))
        if found:
            return found.group(0)
    return ""


def commitment_bet_record(ctx, params):
    """Create and record one BetRecord for the triggering message.

    Deliberately record-only: this Event freezes a bet, persists it, appends it to
    the bet's own event chain and notes it on the Job timeline.  It executes **no
    action**, calls no LLM and touches no project data, so the kernel stops at the
    recorded-bet stage.  ``environment=isolated_sample`` keeps the record
    non-publishable by construction.
    """
    from partner.commitment import models as M
    from partner.commitment import state_machine as sm
    from partner.commitment.freezer import FreezeRequest, Freezer
    from partner.commitment.selector import GuardedGainSelector
    from partner.commitment.state_machine import BetLifecycle
    from partner.commitment.store import CommitmentStore

    token = _trace_token(ctx, params)
    job_id = str(getattr(ctx, "job_id", "") or "")
    if not token:
        return {"ok": False, "status": "failed",
                "summary": "no trace token found in the triggering message",
                "semantic_output": {"trace_token": "", "job_id": job_id},
                "files": [], "evidence_refs": [], "token_usage": {}}

    run_id = str(params.get("run_id") or f"event_flow_{job_id or 'unknown'}")
    bet_id = str(params.get("bet_id") or f"bet_{job_id or 'unknown'}")
    store = CommitmentStore(Path(ctx.workspace), run_id, bet_id)
    message = _job_request(ctx) or token
    candidate_id = "cand_record_only"
    snapshot = {
        "source": "02_event_flow", "trace_token": token, "message": message[:4000],
        "job_id": job_id, "instance_id": str(getattr(ctx, "instance_id", "") or ""),
        "project_id": str(getattr(ctx, "project_id", "") or ""),
        "recorded_by": "commitment.bet_record",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "candidate_space": [{"candidate_id": candidate_id,
                             "description": "record-only placeholder action",
                             "params": {"action": "noop"},
                             "prior": {"expected_gain": 0.0, "risk": 0.0},
                             "rationale": "this bet exists to record the message; nothing runs"}],
    }
    snapshot_hash = store.save_context_snapshot(snapshot)
    candidate = M.Candidate(candidate_id=candidate_id,
                            description="record-only placeholder action",
                            params={"action": "noop"}, proposed_by="policy",
                            rationale="record-only: no experiment is executed")
    selection = GuardedGainSelector(max_risk=1.0).select(candidates=(candidate,),
                                                        snapshot=snapshot)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    frozen = Freezer().freeze(
        FreezeRequest(
            partner_id=str(params.get("partner_id")
                           or f"partner-{getattr(ctx, 'instance_id', '') or 'unknown'}"),
            project_id=str(getattr(ctx, "project_id", "") or "unassigned"),
            run_id=run_id, question=f"record the message carrying {token}",
            context_snapshot_ref="context/snapshot.json",
            context_snapshot_hash=snapshot_hash,
            candidates=(candidate,), selection=selection,
            expected_effects=(M.ExpectedEffect(metric="record_only", direction="increase",
                                               threshold=0.0, unit="unit",
                                               kind="absolute_threshold"),),
            falsification_conditions=(M.FalsificationCondition(
                code="no_action_executed", kind="missing_evidence",
                description="no action was executed, so nothing can be measured",
                params={"metric": "record_only"}),),
            evaluation_protocol=M.EvaluationProtocol(
                evaluator_id="event-flow-record-only", evaluator_version="1.0.0",
                metric_specs=({"metric": "record_only"},), replicates=1),
            baseline_ref="none:record-only",
            budget=M.Budget.create(wall_clock_seconds=60, model_calls=0, actions=1,
                                   rounds=1, started_epoch=time.time()),
            commitment_policy=M.CommitmentPolicy(
                earliest_turn_round=1, max_turns=1, require_new_evidence_to_turn=True,
                early_stop_conditions=("no_action_executed",)),
            code_version=str(params.get("code_version") or "commitment-kernel"),
            data_version=f"message:{token}", model_config_ref="none",
            environment="isolated_sample", treatment=None),
        bet_id=bet_id, now_iso=now_iso)
    store.save_bet(frozen)
    # the lifecycle state must agree with the frozen record: the Freezer produces a
    # COMMITTED-ready bet, so recording it moves the lifecycle to the same state
    store.save_lifecycle(BetLifecycle(bet_id=bet_id, state=frozen.status),
                         reason="recorded by the 02 event flow; no execution")
    store.append("bet_recorded",
                 {"bet_id": bet_id, "trace_token": token, "job_id": job_id,
                  "state": frozen.status, "freeze_hash": frozen.freeze_hash(),
                  "recorded_by": "commitment.bet_record"},
                 event_id=f"{bet_id}:bet_recorded")
    store.write_manifest(extra={"trace_token": token, "bet_id": bet_id,
                                "recorded_by": "commitment.bet_record"})
    noted = False
    try:
        from partner.index.job_repository import init as _init_jobs
        _init_jobs(Path(ctx.workspace)).note(
            job_id, actor="commitment.bet_record", kind="commitment_bet_recorded",
            detail={"bet_id": bet_id, "trace_token": token, "state": frozen.status,
                    "run_id": run_id, "store": str(store.root)})
        noted = True
    except Exception:  # noqa: BLE001 - the bet is the deliverable, the note is the trail
        noted = False
    return {
        "ok": True, "status": frozen.status,
        "summary": f"commitment bet {bet_id} recorded for {token} (no experiment executed)",
        "semantic_output": {"bet_id": bet_id, "trace_token": token, "state": frozen.status,
                            "run_id": run_id, "job_id": job_id, "noted_on_timeline": noted,
                            "freeze_hash": frozen.freeze_hash(),
                            "store": str(store.root)},
        "files": [str(store.path("bet.json")), str(store.events_path)],
        "evidence_refs": [], "token_usage": {},
    }


DEFINITIONS = [
    EventDefinition(
        "commitment.bet_run", "commitment",
        "Run one bounded commitment bet to a terminal state (idempotent on replay)",
        commitment_bet_run, execution_method="local", produces_artifact=True,
        timeout_seconds=900, concurrency_scope="project"),
    EventDefinition(
        "commitment.bet_record", "commitment",
        "Record one BetRecord for the triggering message (record-only, no execution)",
        commitment_bet_record, execution_method="local", produces_artifact=True,
        timeout_seconds=120, concurrency_scope="project"),
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
           "commitment_bet_record",
           "commitment_bet_state", "commitment_bet_settlement"]
