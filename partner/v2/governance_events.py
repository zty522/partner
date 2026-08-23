"""Harness events for governed context, project rounds, and evolution records."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from partner.governance.context_selector import select_context
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.governance.project_loop import record_iteration, request_next_action
from partner.governance.signal_detector import detect_and_record
from partner.governance.storage import instance_id


JsonDict = dict[str, Any]


def _workspace(ctx: Any) -> str:
    value = str(getattr(ctx, "workspace", "") or "")
    if value:
        return value
    task = getattr(ctx, "task_instance", None)
    working_dir = str(getattr(task, "working_dir", "") or "")
    match = re.search(r"^(.+?/instances/0[1-5])(?:/|$)", working_dir)
    return match.group(1) if match else working_dir


def _task_dir(ctx: Any) -> Path:
    task = getattr(ctx, "task_instance", None)
    path = str(getattr(task, "working_dir", "") or "")
    if path:
        return Path(path)
    return Path(_workspace(ctx)) / "state" / "context"


def _semantic_selector(prompt: str) -> str:
    try:
        from partner.adapters.direct_api import chat
        return chat(prompt, purpose="classify", max_tokens=1000, temperature=0.0, timeout=45)
    except Exception:
        return "[]"


def atomic_select_context(ctx: Any, params: JsonDict) -> JsonDict:
    workspace = _workspace(ctx)
    query = str(params.get("query") or params.get("task") or "").strip()
    if not workspace or not query:
        return {"ok": False, "status": "missing_workspace_or_query"}
    selection, bundle = select_context(
        workspace,
        query,
        instance_id=str(params.get("instance_id") or instance_id(workspace)),
        project_id=str(params.get("project_id") or ""),
        budget_chars=int(params.get("budget_chars") or 16000),
        requested_ids=list(params.get("document_ids") or []),
        allow_history=bool(params.get("allow_history", False)),
        semantic_selector=_semantic_selector if params.get("use_llm", True) else None,
    )
    output = _task_dir(ctx) / "selected_context.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(bundle + "\n", encoding="utf-8")
    return {"ok": True, "status": "selected", "selection": selection.to_dict(),
            "path": str(output), "files": [str(output)], "content": bundle}


def atomic_record_iteration(ctx: Any, params: JsonDict) -> JsonDict:
    return record_iteration(_workspace(ctx), params)


def atomic_request_next_action(ctx: Any, params: JsonDict) -> JsonDict:
    return request_next_action(_workspace(ctx), params)


def atomic_record_issue(ctx: Any, params: JsonDict) -> JsonDict:
    return record_issue(_workspace(ctx), params)


def atomic_start_evolution_experiment(ctx: Any, params: JsonDict) -> JsonDict:
    return start_experiment(_workspace(ctx), params)


def atomic_decide_evolution_experiment(ctx: Any, params: JsonDict) -> JsonDict:
    return decide_experiment(_workspace(ctx), params)


def atomic_observe_evolution_signals(ctx: Any, params: JsonDict) -> JsonDict:
    workspace = _workspace(ctx)
    issues = detect_and_record(
        workspace,
        instance_id=str(params.get("instance_id") or instance_id(workspace)),
        project_id=str(params.get("project_id") or ""),
        expected_outputs=bool(params.get("expected_outputs", False)),
        files=list(params.get("files") or []),
        event_types=list(params.get("event_types") or []),
        result=dict(params.get("result") or {}),
        prior_event_types=list(params.get("prior_event_types") or []),
    )
    return {"ok": True, "status": "observed", "issue_count": len(issues), "issues": issues}
