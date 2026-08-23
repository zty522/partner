"""Declarative project protocol loader and Research Loop bridge."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from .project_loop import enqueue_next_action, record_iteration
from .storage import latest_receipt


PROTOCOL_DIR = Path(__file__).resolve().parents[1] / "protocols"


def load_protocols(directory: str | Path = PROTOCOL_DIR) -> list[dict[str, Any]]:
    protocols: list[dict[str, Any]] = []
    for path in sorted(Path(directory).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("protocol_id") or not isinstance(data.get("transitions"), dict):
            raise ValueError(f"invalid protocol: {path}")
        data["_path"] = str(path)
        protocols.append(data)
    return protocols


def transition_for(instance_id: str, event_types: set[str]) -> tuple[dict[str, Any], str, dict[str, Any]] | None:
    matches = []
    for protocol in load_protocols():
        if str(protocol.get("instance_id")) != str(instance_id):
            continue
        for event_type, transition in protocol["transitions"].items():
            if event_type in event_types:
                matches.append((protocol, event_type, transition))
    if len(matches) > 1:
        raise ValueError(f"multiple protocol transitions matched: {[item[1] for item in matches]}")
    return matches[0] if matches else None


async def apply_transition(
    *,
    instance_id: str,
    workspace: str,
    title: str,
    event_types: set[str],
    files: list[str],
    parent_user_request: str,
    enqueue_fn: Callable[[str, str, str], Awaitable[Any]],
) -> dict[str, Any] | None:
    matched = transition_for(instance_id, event_types)
    if not matched:
        return None
    protocol, event_type, transition = matched
    project_id = str(protocol["protocol_id"])
    previous = latest_receipt(workspace, project_id)
    # Project iteration is a monotonic history counter.  Protocol iteration is
    # only the position inside one workflow cycle; re-running a completed
    # protocol must append to history instead of colliding with an old receipt.
    project_iteration = (previous.iteration + 1) if previous else 1
    next_spec = transition.get("next")
    next_actions = []
    if next_spec:
        next_actions = [{
            "title": f"{title}{next_spec.get('title_suffix', '_next')}",
            "event_type": str(next_spec["event_type"]),
            "params": {"user_request": str(next_spec["user_request"]), "source_from_previous": True},
            "status": "proposed",
        }]
    inputs = list(previous.artifacts) if previous else []
    receipt_result = record_iteration(workspace, {
        "project_id": project_id,
        "owner_instance": instance_id,
        "project_goal": str(protocol.get("goal") or title),
        "iteration": project_iteration,
        "goal": f"execute {event_type}",
        "inputs": inputs,
        "actions_executed": [event_type],
        "artifacts": list(files or []),
        "findings": [str(transition.get("finding") or "")],
        "next_actions": next_actions,
        "stop_reason": str(transition.get("stop_reason") or ""),
        "project_status": str(transition.get("terminal_status") or "active"),
        "resume_event": str(transition.get("resume_event") or ""),
        "delivery_confirmed": True,
        "requires_delivery": bool(files),
    })
    if not receipt_result.get("ok"):
        return {"handled": True, "continued": False, "error": receipt_result}
    if not next_actions:
        return {
            "handled": True,
            "continued": False,
            "project_id": project_id,
            "message": f"📋 {instance_id} 项目阶段已收敛\n{transition.get('finding', '')}\n停止边界：{transition.get('stop_reason', '')}",
            "receipt": receipt_result["receipt"],
        }
    queued = await enqueue_next_action(workspace, project_id, enqueue_fn, parent_user_request)
    action = next_actions[0]
    return {
        "handled": True,
        "continued": bool(queued.get("queued")),
        "project_id": project_id,
        "message": (
            f"🔁 {instance_id} 项目累计第 {project_iteration} 轮完成并续跑"
            f"（协议步骤 {transition['iteration']}）\n"
            f"本轮发现：{transition.get('finding', '')}\n"
            f"下一动作状态：{'queued' if queued.get('queued') else 'proposed'}\n"
            f"已执行入队：{action['params']['user_request']}"
        ),
        "receipt": receipt_result["receipt"],
        "queue": queued,
    }
