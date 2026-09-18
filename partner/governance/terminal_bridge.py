"""Terminal bridge: connect the production worker's job terminal to the
instance-native completion-signal state machine.

Sprint 37.  The production worker finishes a Job by writing its terminal to
``state/application/jobs/<job_id>.json`` (status=completed/failed) plus a
``state/event_flows/<flow_id>.json`` whose ``node_summaries`` carry the real
business evidence (outcome/artifacts/evidence_refs/next_event_candidates).
But ``instance_native.handle_terminal`` reads the older shape
``instances/<id>/state/tasks/<task_id>/task_instance.json``.  Nothing wires the
two together, so autonomous continuation never fires.

This module bridges them: it rebuilds the task_instance shape from the Job +
Flow terminal, then calls ``handle_terminal`` to advance the state machine
(project continuation / learning branch / YIELD_SLOT / BLOCKED).  It only
bridges *native* self-seeded jobs (``sender_id`` prefixed ``partner_``); an
ordinary user message keeps its manual_stable stop semantics.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .instance_native import handle_terminal, load_state


def build_task_instance(root: Path, job: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the instance_native task_instance shape from a Job + its Flow."""
    flow: dict[str, Any] = {}
    flow_id = str(job.get("flow_id") or "")
    if flow_id:
        flow_path = root / "state/event_flows" / f"{flow_id}.json"
        if flow_path.exists():
            try:
                flow = json.loads(flow_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                flow = {}

    status = str(job.get("status") or "")
    completion_status = "done" if status == "completed" else "failed"

    summaries = flow.get("node_summaries") or {}
    # Prefer the execute node's real business evidence (business_delta files +
    # command receipts) over a coarse node_summaries aggregate.  ADR 0061's
    # real_action_contract needs a real action signal ("exec:" / "scientific_")
    # plus real artifact *paths*; the coarse aggregate mixes planner prose and
    # evolution mechanism names and stores artifacts as dict-string reprs, which
    # fails that contract even when execute truly ran a scientific experiment.
    executed = (flow.get("node_outputs") or {}).get("execute") or {}
    exec_sem = executed.get("semantic_output") if isinstance(executed.get("semantic_output"), dict) else {}
    exec_files = executed.get("files") or exec_sem.get("files") or []
    exec_result = str(executed.get("summary") or exec_sem.get("result") or "").strip()
    exec_capability = str(executed.get("executed_capability") or "").strip()
    command_receipts = exec_sem.get("command_receipts") or []

    findings: list[str] = []
    actions_executed: list[str] = []
    artifacts: list[str] = []
    next_actions: list[str] = []

    if exec_capability:
        actions_executed.append(exec_capability)
    if command_receipts:
        actions_executed.append(f"exec: {len(command_receipts)} 条命令回执")
    if exec_result:
        findings.append(exec_result)
    for f in (exec_files or []):
        if isinstance(f, str) and f not in artifacts:
            artifacts.append(f)
    for a in (exec_sem.get("verified_artifacts") or exec_sem.get("declared_artifacts") or []):
        if isinstance(a, str) and a not in artifacts:
            artifacts.append(a)

    # Fallback: coarse aggregate only for the fields execute did not provide,
    # and only keep string-shaped values (never dict reprs) for artifacts.
    if not findings:
        for _nid, s in summaries.items():
            if not isinstance(s, dict):
                continue
            outcome = str(s.get("outcome") or "").strip()
            if outcome and outcome not in findings:
                findings.append(outcome)
    if not actions_executed:
        for _nid, s in summaries.items():
            if not isinstance(s, dict):
                continue
            mech = str(s.get("mechanism") or "").strip()
            if mech and mech not in actions_executed:
                actions_executed.append(mech)
    for _nid, s in summaries.items():
        if not isinstance(s, dict):
            continue
        for nc in (s.get("next_event_candidates") or []):
            if isinstance(nc, dict):
                for k in ("event_type", "question"):
                    if nc.get(k) and str(nc[k]) not in next_actions:
                        next_actions.append(str(nc[k]))
    # Fallback artifacts: keep string-shaped artifact entries from
    # node_summaries, but never dict-string reprs ("{'path': ...}").
    if not artifacts:
        for _nid, s in summaries.items():
            if not isinstance(s, dict):
                continue
            for a in (s.get("artifacts") or []):
                if isinstance(a, str) and a not in artifacts and not a.startswith("{"):
                    artifacts.append(a)
    # evidence_refs are durable proof of a real action; surface them as real
    # artifact paths (string only) so ADR 0061 sees a non-report-only attempt.
    if not artifacts or completion_status == "failed":
        for _nid, s in summaries.items():
            if not isinstance(s, dict):
                continue
            for e in (s.get("evidence_refs") or []):
                if isinstance(e, str) and e not in artifacts:
                    artifacts.append(e)

    governance = {
        "ok": status == "completed",
        "status": "completed" if status == "completed" else (job.get("error") or "failed"),
        "error": str(job.get("error") or ""),
        "receipt": {
            "next_actions": next_actions,
            "findings": findings,
            "actions_executed": actions_executed,
            "artifacts": artifacts,
            "unresolved_questions": [],
        },
        "trajectory": {"trajectory": {"outcome": {"status": status}}},
    }
    return {
        "task_id": str(job.get("job_id") or ""),
        "completion_status": completion_status,
        "user_message": str(job.get("request") or ""),
        "metadata": {
            "manual_iteration_governance": governance,
            "step_results": {},
        },
    }


def bridge_and_advance(root: Path) -> list[dict[str, Any]]:
    """Bridge every terminal native Job to handle_terminal, once each.

    Idempotent via the instance's ``last_task_id`` marker: a Job whose terminal
    was already consumed is skipped.  Returns one record per advanced Job.
    """
    root = Path(root)
    jobs_dir = root / "state/application/jobs"
    if not jobs_dir.exists():
        return []
    advanced: list[dict[str, Any]] = []
    for path in sorted(jobs_dir.glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if job.get("status") not in ("completed", "failed"):
            continue
        if not str(job.get("sender_id") or "").startswith("partner_"):
            continue  # ordinary user task keeps manual_stable stop semantics
        instance_id = str(job.get("assigned_instance") or "")
        task_id = str(job.get("job_id") or "")
        if not instance_id or not task_id:
            continue
        task_dir = root / "instances" / instance_id / "state/tasks" / task_id
        # Sprint 37 (queue-runaway fix): idempotency must NOT depend on
        # handle_terminal reaching its final statement (``state.last_task_id =
        # task_id`` sits at the bottom of that function).  Five early exits --
        # native_disabled / terminal_not_settled / superseded_by_application_job
        # / manual_task_not_auto_continued / learning_input_missing -- return
        # before it, so a last_task_id comparison let this watchdog re-process
        # the SAME terminal on EVERY sweep.  Each re-process reached an
        # _enqueue branch and seeded another native Job: measured +14 queued/min
        # against a ~1.4/min drain, unbounded.
        # A durable per-terminal marker is authoritative: one terminal, one
        # advance, whichever branch handle_terminal takes.
        consumed_path = task_dir / "terminal_consumed.json"
        if consumed_path.exists():
            continue
        state = load_state(root, instance_id)
        if str(state.last_task_id or "") == task_id:
            consumed_path.parent.mkdir(parents=True, exist_ok=True)
            consumed_path.write_text(
                json.dumps({"job_id": task_id, "status": "previously_consumed_by_last_task_id"},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        task_instance = build_task_instance(root, job)
        ti_path = task_dir / "task_instance.json"
        ti_path.parent.mkdir(parents=True, exist_ok=True)
        ti_path.write_text(json.dumps(task_instance, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            result = handle_terminal(root, instance_id=instance_id, task_id=task_id)
        except Exception as exc:  # noqa: BLE001 -- a crash must not re-queue the terminal
            result = {"ok": False, "status": "terminal_advance_failed",
                      "task_id": task_id, "error": f"{type(exc).__name__}: {exc}"}
        consumed_path.write_text(json.dumps({
            "job_id": task_id,
            "instance_id": instance_id,
            "status": str((result or {}).get("status") or ""),
            "consumed_at": time.time(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        advanced.append({"job_id": task_id, "instance_id": instance_id, "result": result})
    return advanced


def watchdog_loop(root: Path, *, interval: float = 5.0, once: bool = False) -> None:
    """Completion-signal driven loop: bridge terminals -> advance native state.

    Not a timer-driven poller: each pass only reacts to Jobs that actually
    reached a terminal.  A pass with no new terminal advances nothing.
    """
    import time

    root = Path(root)
    while True:
        try:
            advanced = bridge_and_advance(root)
            if advanced:
                for row in advanced:
                    print(json.dumps({
                        "event": "native_terminal_advanced",
                        "job_id": row["job_id"],
                        "instance_id": row["instance_id"],
                        "status": (row.get("result") or {}).get("status"),
                    }, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001 — watchdog must never die
            print(json.dumps({"event": "native_bridge_error", "error": f"{type(exc).__name__}: {exc}"},
                             ensure_ascii=False), flush=True)
        if once:
            return
        time.sleep(max(1.0, interval))
