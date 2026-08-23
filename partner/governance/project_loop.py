"""Evidence-backed project iteration receipts and next-action state changes."""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from .models import IterationReceipt, NextAction, ProjectState, now_iso
from .storage import (
    append_jsonl,
    governance_log,
    instance_id,
    latest_receipt,
    load_project_state,
    project_governance_dir,
    save_project_state,
    save_receipt,
)


def generic_project_id(instance: str, parent_request: str) -> str:
    compact = re.sub(r"\s+", " ", str(parent_request or "").strip().lower())
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()[:10]
    return f"instance_{instance}_{digest}"


def _actions(values: Any) -> list[NextAction]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("next_actions must be a list")
    return [value if isinstance(value, NextAction) else NextAction.from_dict(dict(value)) for value in values]


def _artifact_handoff(previous: IterationReceipt | None, inputs: list[str]) -> bool:
    if not previous or not previous.artifacts:
        return True
    normalized_inputs = {str(Path(value).expanduser()) for value in inputs}
    previous_names = {Path(value).name for value in previous.artifacts}
    input_names = {Path(value).name for value in normalized_inputs}
    return bool(normalized_inputs.intersection(previous.artifacts) or previous_names.intersection(input_names))


def record_iteration(workspace: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        project_id = str(params.get("project_id") or "").strip()
        previous = latest_receipt(workspace, project_id) if project_id else None
        iteration = int(params.get("iteration") or ((previous.iteration + 1) if previous else 1))
        inputs = [str(value) for value in params.get("inputs") or []]
        if previous and iteration <= previous.iteration:
            raise ValueError("iteration must advance beyond latest receipt")
        if previous and not _artifact_handoff(previous, inputs):
            raise ValueError("new iteration must reference at least one previous artifact")
        receipt = IterationReceipt(
            project_id=project_id,
            iteration=iteration,
            goal=str(params.get("goal") or ""),
            inputs=inputs,
            actions_executed=list(params.get("actions_executed") or []),
            artifacts=list(params.get("artifacts") or []),
            findings=list(params.get("findings") or []),
            unresolved_questions=list(params.get("unresolved_questions") or []),
            next_actions=_actions(params.get("next_actions")),
            stop_reason=str(params.get("stop_reason") or ""),
            delivery_confirmed=bool(params.get("delivery_confirmed", False)),
        )
        if params.get("requires_delivery", True) and receipt.artifacts and not receipt.delivery_confirmed:
            raise ValueError("artifact-bearing iteration requires delivery confirmation")
        receipt_path = save_receipt(workspace, receipt)
        state = load_project_state(workspace, project_id) or ProjectState(
            project_id=project_id,
            owner_instance=str(params.get("owner_instance") or instance_id(workspace)),
            status="active",
            goal=str(params.get("project_goal") or params.get("goal") or ""),
        )
        state.current_iteration = receipt.iteration
        state.latest_receipt_id = receipt.receipt_id
        state.updated_at = now_iso()
        if receipt.next_actions:
            state.status = "active"
            state.blocked_reason = ""
        else:
            terminal = str(params.get("project_status") or "completed")
            if terminal not in {"blocked", "completed", "paused"}:
                terminal = "completed"
            state.status = terminal
            state.blocked_reason = receipt.stop_reason if terminal == "blocked" else ""
            state.resume_event = str(params.get("resume_event") or "")
        state_path = save_project_state(workspace, state)
        return {"ok": True, "status": "recorded", "receipt": receipt.to_dict(),
                "receipt_path": str(receipt_path), "project_state_path": str(state_path),
                "files": [str(receipt_path), str(state_path)]}
    except (TypeError, ValueError, OSError) as exc:
        return {"ok": False, "status": "invalid_iteration_receipt", "error": str(exc), "retryable": False}


def request_next_action(workspace: str, params: dict[str, Any]) -> dict[str, Any]:
    project_id = str(params.get("project_id") or "").strip()
    receipt = latest_receipt(workspace, project_id) if project_id else None
    if not receipt:
        return {"ok": False, "status": "missing_receipt", "retryable": False}
    history_path = project_governance_dir(workspace, project_id) / "action_history.jsonl"
    effective: dict[str, str] = {}
    try:
        for line in history_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if isinstance(row, dict) and row.get("action_id"):
                effective[str(row["action_id"])] = str(row.get("status") or "")
    except (OSError, ValueError):
        pass
    proposed = next(
        (action for action in receipt.next_actions
         if effective.get(action.action_id, action.status) == "proposed"),
        None,
    )
    if not proposed:
        return {"ok": False, "status": "no_proposed_action", "stop_reason": receipt.stop_reason, "retryable": False}
    task_id = str(params.get("task_id") or "").strip()
    if not task_id:
        return {"ok": True, "status": "proposed", "action": proposed.to_dict(),
                "queued": False, "requires_runtime_enqueue": True}
    proposed.status = "queued"
    proposed.task_id = task_id
    proposed.updated_at = now_iso()
    append_jsonl(history_path, proposed.to_dict())
    return {"ok": True, "status": "queued", "action": proposed.to_dict(), "queued": True, "task_id": task_id}


def record_action_state(
    workspace: str,
    project_id: str,
    action_id: str,
    status: str,
    *,
    task_id: str = "",
    blocked_reason: str = "",
) -> dict[str, Any]:
    """Append an auditable action transition without rewriting old receipts."""
    receipt = latest_receipt(workspace, project_id)
    if not receipt:
        return {"ok": False, "status": "missing_receipt", "retryable": False}
    action = next((value for value in receipt.next_actions if value.action_id == action_id), None)
    if not action:
        return {"ok": False, "status": "missing_action", "retryable": False}
    try:
        action.status = str(status)
        action.task_id = str(task_id or action.task_id)
        action.blocked_reason = str(blocked_reason or "")
        action.updated_at = now_iso()
        data = action.to_dict()
        append_jsonl(project_governance_dir(workspace, project_id) / "action_history.jsonl", data)
        return {"ok": True, "status": action.status, "action": data}
    except (OSError, ValueError) as exc:
        return {"ok": False, "status": "invalid_action_transition", "error": str(exc), "retryable": False}


def invalidate_receipt(
    workspace: str,
    project_id: str,
    receipt_id: str,
    *,
    reason: str,
    evidence: list[str],
    restore_status: str = "paused",
) -> dict[str, Any]:
    """Invalidate an erroneous receipt without deleting historical evidence."""
    if not str(reason).strip() or not evidence:
        return {"ok": False, "status": "invalid_correction", "error": "reason and evidence are required"}
    if restore_status not in {"active", "paused", "blocked", "completed"}:
        return {"ok": False, "status": "invalid_correction", "error": "invalid restore_status"}
    root = project_governance_dir(workspace, project_id)
    target = next((path for path in (root / "receipts").glob("*.json")
                   if receipt_id in path.name), None)
    if not target:
        return {"ok": False, "status": "missing_receipt"}
    correction = {
        "receipt_id": receipt_id,
        "project_id": project_id,
        "action": "invalidate",
        "reason": str(reason),
        "evidence": [str(value) for value in evidence],
        "created_at": now_iso(),
    }
    append_jsonl(root / "receipt_corrections.jsonl", correction)
    latest = latest_receipt(workspace, project_id)
    state = load_project_state(workspace, project_id)
    if state:
        state.current_iteration = latest.iteration if latest else 0
        state.latest_receipt_id = latest.receipt_id if latest else ""
        state.status = restore_status
        state.blocked_reason = str(reason) if restore_status == "blocked" else ""
        state.updated_at = now_iso()
        save_project_state(workspace, state)
    return {"ok": True, "status": "invalidated", "correction": correction,
            "latest_receipt_id": latest.receipt_id if latest else ""}


async def enqueue_next_action(
    workspace: str,
    project_id: str,
    enqueue_fn: Callable[[str, str, str], Awaitable[Any]],
    parent_user_request: str = "",
) -> dict[str, Any]:
    """Queue one proposed action and only mark it queued after enqueue succeeds."""
    prepared = request_next_action(workspace, {"project_id": project_id})
    if not prepared.get("ok") or not prepared.get("requires_runtime_enqueue"):
        return prepared
    action = prepared["action"]
    request = action["params"].get("user_request") or (
        f"【声明式项目续跑】直接调用且只调用 {action['event_type']}。"
        f"参数：{json.dumps(action['params'], ensure_ascii=False)}"
    )
    result = await enqueue_fn(action["title"], str(request), parent_user_request)
    task_id = ""
    if isinstance(result, dict):
        task_id = str(result.get("task_id") or result.get("id") or "")
    elif result:
        task_id = str(result)
    if not task_id:
        return {
            "ok": False,
            "status": "enqueue_missing_task_id",
            "queued": False,
            "error": "runtime enqueue callback did not return a task/event id",
            "retryable": True,
        }
    return request_next_action(workspace, {"project_id": project_id, "task_id": task_id})
