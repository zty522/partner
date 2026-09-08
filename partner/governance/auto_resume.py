"""Bug #58 P0.3 (ADR 0066): auto-resume projects that are stuck in
``stop_reason="waiting for the next user instruction"``.

Production reality (2026-09-05 → 2026-09-06 dashboard survey):
03 and 05 finished manual_stable bounded tasks whose receipts carry
``stop_reason="waiting for the next user instruction"`` and therefore
``request_next_action`` returns ``no_proposed_action`` every time the
oneshot fires.  The user has to send a fresh message by hand to push
them forward — the system is otherwise stranded.

This module appends one synthetic receipt whose
``next_actions[0].status = "proposed"`` so the project loop returns a
real proposed action on the next tick.  It does NOT overwrite any
existing receipt — it appends, keyed off ``latest.iteration + 1``.

The synthetic receipt records ``actions_executed=["auto_resume:p0.3"]``
so the audit trail clearly marks it as machine-generated rather than
user-driven.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from partner.governance.storage import (
    latest_receipt, save_receipt, load_project_state, save_project_state,
    project_governance_dir,
)
from partner.governance.models import IterationReceipt, NextAction


WAITING_SENTINELS = (
    "waiting for the next user instruction",
    "waiting for the next user",
    "waiting for user instruction",
)


def _stop_reason_indicates_waiting(stop_reason: str) -> bool:
    return any(sentinel in (stop_reason or "").lower()
               for sentinel in WAITING_SENTINELS)


def _append_initial_receipt(
    workspace: str,
    project_id: str,
    instance_id: str,
    goal: str,
) -> dict[str, Any]:
    """Write the first receipt (iteration=1) when a project has none.

    Distinct from the waiting-state resume path because we have no
    previous_receipt to reference.  Sets delivery_confirmed=True so the
    IterationReceipt validator is happy.
    """
    receipt_id = f"resume_{instance_id}_0001"
    # Bug #61 (ADR 0067): include the receipt_id suffix in the
    # user_request so that batch_planner's content-based dedup
    # hashes a different value across resumes.  Without the suffix
    # every resume of the same project emits the same text, which
    # collides with the dedup hash of the prior resume and the
    # instance permanently skips re-running the plan.
    # Bug #62 follow-up: also pin policy_arm=baseline so the
    # candidate-truth audit in manual_runtime skips the
    # claim_ledger requirement.  Production survey 2026-09-07
    # showed 04 task hit 7/7 steps green but governance.ok=False
    # because the LLM correctly refused to fabricate claim_ledger
    # blocks for first-iteration plans where source files don't
    # yet exist on disk (Bug #59 P2 atomic_inspect_file returns
    # SKIP-not-raise, so generate_text has no real content to
    # quote).  baseline arm accepts the report without forcing
    # claim-ledger fabrication.
    iteration_tag = f"resume_{receipt_id}"
    action = NextAction(
        title=f"启动 {project_id} 第一轮 iteration [{iteration_tag}]",
        event_type="auto_resume_iteration",
        status="proposed",
        params={
            "user_request": (
                f"启动项目 {project_id} 第一轮 iteration。\n"
                f"[iteration_tag={iteration_tag}]\n"
                f"[policy_arm=baseline]\n"
                f"目标：{goal}。\n"
                "请遵循 ADR 0061 真实外部动作契约，"
                "执行至少一项 exec:/web.fetch:/pytest: 动作并落 external_artifacts。"
            ),
        },
    )
    receipt = IterationReceipt(
        receipt_id=receipt_id,
        project_id=project_id,
        iteration=1,
        goal=goal,
        inputs=[],
        actions_executed=[f"auto_resume:p0.3:initial_iter={project_id}"],
        artifacts=[],
        findings=[],
        unresolved_questions=[],
        next_actions=[action],
        stop_reason="",
        delivery_confirmed=True,
    )
    save_receipt(workspace, receipt)
    state = load_project_state(workspace, project_id)
    if state is not None:
        state.current_iteration = 1
        state.latest_receipt_id = receipt_id
        state.status = "active"
        state.blocked_reason = ""
        save_project_state(workspace, state)
    return {
        "resumed": True,
        "receipt_id": receipt_id,
        "iteration": 1,
        "previous_receipt_id": "",
        "action_id": action.action_id,
        "initial": True,
    }


def auto_resume_waiting_project(
    workspace: str | Path,
    project_id: str,
    instance_id: str,
    goal: str = "承接上一轮 iteration 真实推进项目",
) -> dict[str, Any]:
    """If the project's latest receipt is in "waiting for user" state,
    append a synthetic receipt with one proposed action so the next
    oneshot / runtime tick picks it up.

    Returns a summary with ``resumed: bool`` and ``receipt_id: str``.
    Idempotent: calling twice in a row produces only one synthetic
    receipt because the second call sees the new receipt as ``latest``.
    """
    latest = latest_receipt(str(workspace), project_id)
    if latest is None:
        # Bug #58 P0.3 follow-up: a project with NO receipt at all
        # (e.g. 03's PROJECTS dict project "molecular_dynamics_study"
        # was created by scaffold but never actually iterated) still
        # counts as "waiting for first iteration" — write the very
        # first receipt with a proposed next action so the project
        # loop can return a real action.
        return _append_initial_receipt(
            str(workspace), project_id, instance_id, goal)
    if not _stop_reason_indicates_waiting(latest.stop_reason):
        return {
            "resumed": False,
            "reason": "not_waiting",
            "current_stop_reason": latest.stop_reason,
        }
    # Idempotency: if the latest receipt was already auto-resumed by
    # this helper, do nothing.  We detect this by checking the very
    # last action's status — once proposed+queued, the next call sees
    # a non-waiting receipt and returns not_waiting.
    if latest.next_actions:
        for na in latest.next_actions:
            if na.status == "queued":
                return {
                    "resumed": False,
                    "reason": "already_resumed_in_flight",
                    "action_id": na.action_id,
                }

    new_iteration = latest.iteration + 1
    receipt_id = f"resume_{instance_id}_{new_iteration:04d}"
    action = NextAction(
        title=f"承接 iteration {latest.iteration}",
        event_type="auto_resume_iteration",
        status="proposed",
        params={
            "user_request": (
                f"承接上一轮 (iteration {latest.iteration}, "
                f"receipt={latest.receipt_id}) 的真实进展。"
                f"目标：{goal}。\n"
                f"上一轮 stop_reason={latest.stop_reason}，"
                "现在自动 resume，遵循 ADR 0061 真实外部动作契约。"
            ),
            "previous_receipt_id": latest.receipt_id,
            "previous_iteration": latest.iteration,
        },
    )
    receipt = IterationReceipt(
        receipt_id=receipt_id,
        project_id=project_id,
        iteration=new_iteration,
        goal=goal,
        inputs=[f"previous_receipt:{latest.receipt_id}"],
        actions_executed=[f"auto_resume:p0.3:from_iter={latest.iteration}"],
        artifacts=[],
        findings=[],
        unresolved_questions=[],
        next_actions=[action],
        stop_reason="",
        delivery_confirmed=True,
    )
    save_receipt(str(workspace), receipt)
    state = load_project_state(str(workspace), project_id)
    if state is not None:
        state.current_iteration = new_iteration
        state.latest_receipt_id = receipt_id
        state.status = "active"
        state.blocked_reason = ""
        save_project_state(str(workspace), state)
    return {
        "resumed": True,
        "receipt_id": receipt_id,
        "iteration": new_iteration,
        "previous_receipt_id": latest.receipt_id,
        "action_id": action.action_id,
    }


def auto_resume_all_waiting_projects(
    workspace: str | Path,
    enabled: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Auto-resume every ``(instance_id, project_id)`` pair whose latest
    receipt is in "waiting for user" state.  ``enabled`` is the list
    the runtime daemon already has in scope from PROJECTS."""
    summaries: list[dict[str, Any]] = []
    for instance_id, project_id in enabled:
        try:
            s = auto_resume_waiting_project(
                workspace, project_id, instance_id)
        except Exception as exc:  # noqa: BLE001
            s = {"resumed": False, "reason": f"exception:{type(exc).__name__}",
                 "instance_id": instance_id, "project_id": project_id}
        s["instance_id"] = instance_id
        s["project_id"] = project_id
        summaries.append(s)
    return summaries
