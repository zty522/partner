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
from partner.commitment.state_machine import is_terminal
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
# A trace token is whatever the operator wrote; do not dictate the prefix.  Match any
# single identifier-shaped word containing "trace" (runtime_trace_02_x, log_trace_02_x,
# trace_token=abc, canary_trace...), and only that word.
_TRACE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9_-]+[Tt]race[A-Za-z0-9_-]*"
    r"|[Tt]race[_-][A-Za-z0-9_-]+)")


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


#: A message may declare a real repository task instead of the built-in metric action:
#:   real_task:<task_id>   patch:<file>   (patch defaults to patch.diff)
_REAL_TASK_RE = re.compile(r"real_task[:=\s]+([A-Za-z0-9_\-]+)")
_PATCH_RE = re.compile(r"patch[:=\s]+([A-Za-z0-9_.\-]+)")


def _declared_real_task(message: str, params: Mapping[str, Any]) -> dict[str, str]:
    task_id = str(params.get("task_id") or "").strip()
    if not task_id:
        found = _REAL_TASK_RE.search(message or "")
        task_id = found.group(1) if found else ""
    if not task_id:
        return {}
    patch_file = str(params.get("patch_file") or "").strip()
    if not patch_file:
        found = _PATCH_RE.search(message or "")
        patch_file = found.group(1) if found else "patch.diff"
    return {"task_id": task_id, "patch_file": patch_file}


def _prepare_bounded(ctx, params):
    """Resolve the bounded bet this Event acts on: spec, store, snapshot, runner.

    Both commitment Events derive the same spec from the same frozen inputs, so the
    bet the second Event runs is byte-identical to the one the first Event recorded.
    """
    from partner.application.commitment_bounded_adapter import (
        bounded_spec, build_bounded_runner, write_bounded_snapshot,
    )
    from partner.commitment.store import CommitmentStore
    token = _trace_token(ctx, params)
    job_id = str(getattr(ctx, "job_id", "") or "")
    if not token:
        return None, {"ok": False, "status": "failed",
                      "summary": "no trace token found in the triggering message",
                      "semantic_output": {"trace_token": "", "job_id": job_id},
                      "files": [], "evidence_refs": [], "token_usage": {}}
    message = _job_request(ctx) or token
    instance_id = str(getattr(ctx, "instance_id", "") or "")
    project_id = str(getattr(ctx, "project_id", "") or "unassigned")
    declared = _declared_real_task(message, params)
    if declared:
        # A real repository task: the input and the patch are on disk, and the project's
        # own test runner decides the outcome.
        from partner.application.real_task_adapter import (
            RealTaskError, build_real_task_runner, load_task, real_task_spec,
            write_real_task_snapshot,
        )
        task_root = Path(__file__).resolve().parents[2]
        try:
            task = load_task(task_root, declared["task_id"])
        except RealTaskError as exc:
            return None, {"ok": False, "status": "failed",
                          "summary": f"declared real task is unavailable: {exc}",
                          "semantic_output": {"trace_token": token, "job_id": job_id,
                                              "task_id": declared["task_id"]},
                          "files": [], "evidence_refs": [], "token_usage": {}}
        spec = real_task_spec(job_id=job_id, trace_token=token, task_id=declared["task_id"],
                              project_root=str(task_root), patch_file=declared["patch_file"],
                              task=task, instance_id=instance_id, project_id=project_id)
        store = CommitmentStore(Path(ctx.workspace), str(spec["run_id"]), str(spec["bet_id"]))
        snapshot_path = write_real_task_snapshot(
            store=store, spec=spec, job_id=job_id, trace_token=token, task=task,
            project_root=str(task_root), patch_file=declared["patch_file"],
            instance_id=instance_id)
        runner = build_real_task_runner(Path(ctx.workspace), spec, snapshot_path=snapshot_path)
        return {"token": token, "job_id": job_id, "spec": spec, "store": store,
                "runner": runner, "message": message, "action": "real_task",
                "task_id": declared["task_id"], "patch_file": declared["patch_file"]}, None
    spec = bounded_spec(job_id=job_id, trace_token=token, message=message,
                        instance_id=instance_id, project_id=project_id)
    store = CommitmentStore(Path(ctx.workspace), str(spec["run_id"]), str(spec["bet_id"]))
    snapshot_path = write_bounded_snapshot(
        store=store, spec=spec, job_id=job_id, trace_token=token, message=message,
        instance_id=instance_id)
    runner = build_bounded_runner(Path(ctx.workspace), spec, snapshot_path=snapshot_path)
    return {"token": token, "job_id": job_id, "spec": spec, "store": store,
            "runner": runner, "message": message, "action": "bounded_metric"}, None


def commitment_bet_record(ctx, params):
    """Record one BetRecord for the triggering message.  Executes nothing.

    The bet is frozen with its expectation, failure conditions, protocol, budget and
    treatment; execution, measurement and settlement happen in the later
    ``commitment.bet_execute`` Event.  Recording is idempotent: a replay re-freezes
    identical semantics, which the store accepts without touching the record.
    """
    prepared, failure = _prepare_bounded(ctx, params)
    if failure is not None:
        return failure
    token, job_id = prepared["token"], prepared["job_id"]
    spec, store = prepared["spec"], prepared["store"]
    result = prepared["runner"].freeze_only()
    # "recorded" means COMMITTED (the bet exists and is frozen) or already terminal on
    # a replay; anything else means the freeze stopped the bet and nothing was recorded
    if result.state != sm.COMMITTED and not is_terminal(result.state):
        return {"ok": False, "status": result.state,
                "summary": f"bet was not recorded: {result.reason}",
                "semantic_output": {"trace_token": token, "job_id": job_id,
                                    "state": result.state, "reason": result.reason},
                "files": [], "evidence_refs": [], "token_usage": {}}
    record = store.load_bet()
    if not store.has_event(f"{record.bet_id}:bet_recorded"):
        store.append("bet_recorded",
                     {"bet_id": record.bet_id, "trace_token": token, "job_id": job_id,
                      "state": record.status, "freeze_hash": record.freeze_hash(),
                      "recorded_by": "commitment.bet_record"},
                     event_id=f"{record.bet_id}:bet_recorded")
    store.write_manifest(extra={"trace_token": token, "bet_id": record.bet_id,
                               "recorded_by": "commitment.bet_record"})
    noted = False
    try:
        from partner.index.job_repository import init as _init_jobs
        if not any(row.get("kind") == "commitment_bet_recorded"
                   for row in _init_jobs(Path(ctx.workspace)).history(job_id)):
            _init_jobs(Path(ctx.workspace)).note(
                job_id, actor="commitment.bet_record", kind="commitment_bet_recorded",
                detail={"bet_id": record.bet_id, "trace_token": token,
                        "state": record.status, "run_id": spec["run_id"],
                        "store": str(store.root)})
        noted = True
    except Exception:  # noqa: BLE001 - the bet is the deliverable, the note is the trail
        noted = False
    return {
        "ok": True, "status": result.state,
        "summary": f"commitment bet {record.bet_id} recorded for {token} (not yet executed)",
        "semantic_output": {"bet_id": record.bet_id, "trace_token": token,
                            "state": result.state, "run_id": spec["run_id"],
                            "job_id": job_id, "noted_on_timeline": noted,
                            "freeze_hash": record.freeze_hash(), "store": str(store.root),
                            "next_event": map_kernel_state(result.state)},
        "files": [str(store.path("bet.json")), str(store.events_path)],
        "evidence_refs": [], "token_usage": {},
    }


def commitment_bet_execute(ctx, params):
    """Run the recorded bet to a terminal state: act, measure, compare, settle.

    The action is bounded and deterministic (text statistics under one declared
    transform), the baseline is executed and measured through the same instrument,
    and the verdict comes from the kernel's machine rules -- never from this Event.
    """
    prepared, failure = _prepare_bounded(ctx, params)
    if failure is not None:
        return failure
    token, job_id = prepared["token"], prepared["job_id"]
    spec, store, runner = prepared["spec"], prepared["store"], prepared["runner"]
    result = runner.run()
    settlement = result.settlement
    experience = result.experience
    noted = False
    try:
        from partner.index.job_repository import init as _init_jobs
        _init_jobs(Path(ctx.workspace)).note(
            job_id, actor="commitment.bet_execute", kind="commitment_bet_settled",
            detail={"bet_id": store.bet_id, "trace_token": token, "state": result.state,
                    "settlement_class": None if settlement is None else settlement.settlement_class,
                    "settlement_id": None if settlement is None else settlement.settlement_id,
                    "experience_id": None if experience is None else experience.experience_id,
                    "run_id": spec["run_id"], "store": str(store.root)})
        noted = True
    except Exception:  # noqa: BLE001
        noted = False
    return {
        "ok": is_terminal(result.state), "status": result.state,
        "summary": f"commitment bet {store.bet_id} -> {result.state}: {result.reason}",
        "semantic_output": {
            "bet_id": store.bet_id, "trace_token": token, "state": result.state,
            "reason": result.reason, "run_id": spec["run_id"], "job_id": job_id,
            "settlement_id": None if settlement is None else settlement.settlement_id,
            "settlement_class": None if settlement is None else settlement.settlement_class,
            "expectations_met": None if settlement is None else settlement.expectations_met,
            "improvement_over_baseline": (None if settlement is None
                                          else settlement.improvement_over_baseline),
            "publish_eligible": None if settlement is None else settlement.publish_eligible,
            "publish_blockers": [] if settlement is None else list(settlement.publish_blockers),
            "experience_id": None if experience is None else experience.experience_id,
            "noted_on_timeline": noted,
            "receipt_id": None if result.receipt is None else result.receipt.receipt_id,
            "measurement_id": (None if result.measurement is None
                               or not result.measurement.measurements
                               else result.measurement.measurements[0].measurement_id),
            "baseline_id": None if result.baseline_evidence is None
                           else result.baseline_evidence.baseline_id,
            "replayed": result.replayed,
            "artifacts": dict(result.paths),
            "store": str(store.root),
        },
        "files": [path for path in result.paths.values() if isinstance(path, str)],
        "evidence_refs": ([] if settlement is None else [settlement.settlement_id]),
        "token_usage": {},
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
        "commitment.bet_execute", "commitment",
        "Run a recorded commitment bet to a terminal state (bounded action, machine settlement)",
        commitment_bet_execute, execution_method="local", produces_artifact=True,
        timeout_seconds=300, concurrency_scope="project"),
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
           "commitment_bet_record", "commitment_bet_execute",
           "commitment_bet_state", "commitment_bet_settlement"]
