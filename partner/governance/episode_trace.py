"""Append-only episode evidence and deterministic offline reduction.

The hot runtime already writes task-local JSONL and step payloads.  This module
does not replace that runtime or reinterpret success on the hot path.  It
indexes those immutable facts, builds a replayable graph, and computes a reward
vector that can be inspected by evolution code without trusting model prose.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import now_iso
from .storage import append_jsonl, atomic_json, workspace_root


REWARD_VECTOR_SPEC = {
    "schema_version": 3,
    "dimensions": ["truth", "business_progress", "handoff", "observability", "efficiency", "safety"],
    "range": [0.0, 1.0],
    "hard_gates": ["truth", "safety"],
    "rule": "truth or safety failure forces policy_eligible=false; other dimensions cannot compensate",
}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, ValueError, TypeError):
        pass
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _id(prefix: str, *parts: str) -> str:
    raw = "|".join(str(value) for value in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _window(value: str, seconds: int) -> str:
    try:
        return (datetime.fromisoformat(value) + timedelta(seconds=seconds)).isoformat()
    except (TypeError, ValueError):
        return value


def reward_vector(trajectory: dict[str, Any], reduced: dict[str, Any]) -> dict[str, Any]:
    outcome = trajectory.get("outcome") or {}
    state = trajectory.get("state") or {}
    steps = reduced.get("tool_calls") or []
    model_calls = reduced.get("model_calls") or []
    failures = reduced.get("failure_classes") or []
    truth_audit = outcome.get("truth_audit") or {}
    false_success = bool(outcome.get("false_success"))
    truth = 0.0 if false_success else (1.0 if not truth_audit or truth_audit.get("passed") is True else 0.0)
    safety = 0.0 if any(value == "safety.denied_or_violated" for value in failures) else 1.0
    progress = 1.0 if outcome.get("business_progress") is True else 0.0
    handoff = 1.0 if outcome.get("handoff_consumed") is True else 0.0
    delivery = bool(state.get("delivery_confirmed") or reduced.get("delivery", {}).get("confirmed"))
    started = sum(1 for value in reduced.get("conversation_items") or [] if value.get("kind") == "progress")
    observability = 1.0 if delivery and started >= len(steps) and bool(steps) else (0.5 if delivery else 0.0)
    retries = sum(max(0, int(value.get("attempt") or 1) - 1) for value in model_calls)
    repairs = sum(1 for value in failures if value.startswith("planning."))
    failed_steps = sum(1 for value in steps if value.get("status") != "completed")
    efficiency = max(0.0, 1.0 - min(1.0, 0.15 * retries + 0.2 * repairs + 0.25 * failed_steps))
    values = {
        "truth": round(truth, 4), "business_progress": round(progress, 4),
        "handoff": round(handoff, 4), "observability": round(observability, 4),
        "efficiency": round(efficiency, 4), "safety": round(safety, 4),
    }
    eligible = truth == 1.0 and safety == 1.0 and progress > 0.0
    scalar = 0.0 if not eligible else round(
        0.35 * truth + 0.30 * progress + 0.10 * handoff
        + 0.10 * observability + 0.10 * efficiency + 0.05 * safety, 4
    )
    return {"schema_version": 3, "values": values, "hard_gate_passed": truth == 1.0 and safety == 1.0,
            "policy_eligible": eligible, "scalar": scalar}


def reduce_task_episode(workspace: str, *, instance_id: str, task_id: str,
                        trajectory: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create/rebuild one deterministic Episode Trace v3 bundle."""
    root = workspace_root(workspace)
    task_dir = root / "instances" / instance_id / "state" / "tasks" / task_id
    task_path = task_dir / "task_instance.json"
    log_path = task_dir / "task_log.jsonl"
    task = _json(task_path)
    rows = _jsonl(log_path)
    if not task or not rows:
        return {"ok": False, "status": "missing_task_evidence", "task_dir": str(task_dir)}

    episode_id = _id("episode", instance_id, task_id)
    bundle = root / "share" / "mind" / "governance" / "episodes" / episode_id
    raw_events: list[dict[str, Any]] = []
    for seq, row in enumerate(rows, start=1):
        raw_events.append({
            "schema_version": 3, "seq": seq, "event_id": _id("event", episode_id, str(seq)),
            "episode_id": episode_id, "task_id": task_id, "instance_id": instance_id,
            "observed_at": row.get("ts") or row.get("timestamp") or "", "type": row.get("event") or "unknown",
            "payload": row,
        })

    starts: dict[str, dict[str, Any]] = {}
    tool_calls: list[dict[str, Any]] = []
    model_starts: dict[str, list[dict[str, Any]]] = {}
    model_calls: list[dict[str, Any]] = []
    failures: list[str] = []
    failure_details: list[dict[str, Any]] = []
    artifact_paths: set[str] = set()
    intervention: dict[str, Any] = {}
    for row in rows:
        event = str(row.get("event") or "")
        if event == "planner_experiment_intervention":
            intervention = {
                key: row.get(key)
                for key in (
                    "schema_version", "experiment_id", "match_key", "policy_arm",
                    "strategy_id", "marked", "active", "route", "intervention",
                )
            }
        if event == "plan_executor_step_started":
            starts[str(row.get("step_id") or "")] = row
        elif event in {"plan_executor_step_completed", "plan_executor_step_failed"}:
            step_id = str(row.get("step_id") or "")
            start = starts.pop(step_id, {})
            ok = bool(row.get("ok")) and event.endswith("completed")
            tool_calls.append({
                "tool_call_id": _id("tool", episode_id, step_id), "step_id": step_id,
                "event_type": row.get("event_type") or start.get("event_type") or "",
                "depends_on": start.get("depends_on") or [], "started_at": start.get("ts") or "",
                "ended_at": row.get("ts") or "", "status": "completed" if ok else "failed",
                "elapsed_sec": row.get("elapsed_sec"), "raw_result_ref": str(task_dir / f"_step_{step_id}.result.json"),
            })
            artifact_paths.update(str(value) for value in row.get("files") or [])
            if not ok:
                failures.append(f"tool.{row.get('event_type') or 'unknown'}.failed")
        elif event == "robust_execute_start":
            model_starts.setdefault(str(row.get("event_name") or "model"), []).append(row)
        elif event in {"robust_execute_success", "robust_execute_failure"}:
            name = str(row.get("event_name") or "model")
            start = (model_starts.get(name) or [{}]).pop(0)
            model_calls.append({
                "model_call_id": _id("model", episode_id, name, str(len(model_calls) + 1)),
                "purpose": name, "model": (start.get("metadata") or {}).get("model") or "",
                "attempt": row.get("attempt") or (start.get("metadata") or {}).get("attempt") or 1,
                "started_at": start.get("ts") or "", "ended_at": row.get("ts") or "",
                "status": "completed" if event.endswith("success") else "failed",
            })
        elif event == "manual_plan_preflight_failed":
            failures.append("planning.semantic_preflight")
            failure_details.append({"class": "planning.semantic_preflight", "error": str(row.get("error") or ""),
                                    "attempt": int(row.get("attempt") or 1)})
        elif "timeout" in event or "timeout" in str(row.get("error") or "").lower():
            failures.append("runtime.timeout")
    for step_id, start in starts.items():
        tool_calls.append({"tool_call_id": _id("tool", episode_id, step_id), "step_id": step_id,
                           "event_type": start.get("event_type") or "", "depends_on": start.get("depends_on") or [],
                           "started_at": start.get("ts") or "", "ended_at": "", "status": "interrupted"})
        failures.append("lifecycle.unclosed_tool")

    governance = next((row for row in reversed(rows) if row.get("event") == "manual_iteration_governance"), {})
    embedded = (((governance.get("trajectory") or {}).get("trajectory")) or {})
    trajectory = trajectory or embedded
    receipt = governance.get("receipt") or {}
    artifact_paths.update(str(value) for value in receipt.get("artifacts") or [])
    receipt_id = str(receipt.get("receipt_id") or (trajectory.get("state") or {}).get("receipt_id") or "")
    project_id = str(trajectory.get("project_id") or receipt.get("project_id") or "")
    invalidated = False
    if receipt_id and project_id:
        correction_path = root / "share" / "projects" / project_id / "governance" / "receipt_corrections.jsonl"
        for correction in _jsonl(correction_path):
            if str(correction.get("receipt_id") or "") != receipt_id:
                continue
            if correction.get("action") == "invalidate":
                invalidated = True
            elif correction.get("action") == "reinstate":
                invalidated = False
    if invalidated:
        # Corrections are the append-only authority over an earlier accepted
        # outcome. Keep the old trajectory intact but make the reduced view
        # ineligible and explicit about the false-success evidence.
        trajectory = json.loads(json.dumps(trajectory))
        trajectory.setdefault("outcome", {})["false_success"] = True
        trajectory["outcome"].setdefault("truth_audit", {})["passed"] = False
        trajectory["outcome"]["truth_audit"]["receipt_invalidated"] = receipt_id
        failures.append("verification.invalidated_receipt")

    conversations: list[dict[str, Any]] = [{
        "conversation_item_id": _id("conversation", episode_id, "user"), "kind": "user",
        "model_visible": True, "content_ref": str(task_path), "timestamp": task.get("created_at") or "",
    }]
    # Task-local logs do not prove channel delivery. Governance is the authority
    # for confirmed final delivery; step progress is linked from channel history
    # by the shadow reducer only when explicit messages are found.
    chat_paths = [root / "instances" / instance_id / "state" / "qq_chat_history.jsonl",
                  root / "instances" / instance_id / "dialogue" / "qq_chat_history.jsonl"]
    seen_message: set[str] = set()
    window_start = _window(str(task.get("created_at") or ""), -2)
    window_end = _window(str((rows[-1].get("ts") if rows else "") or ""), 2)
    for chat_path in chat_paths:
        for row in _jsonl(chat_path):
            content = str(row.get("content") or "")
            timestamp = str(row.get("timestamp") or "")
            if row.get("role") != "assistant" or not content or content in seen_message:
                continue
            if (window_start and timestamp and timestamp < window_start) or (window_end and timestamp and timestamp > window_end):
                continue
            step_message = bool(re.match(r"^[⏳✅]\s*\d+/\d+", content))
            if task_id in content or step_message or any(token in content for token in ("收到指令", "已规划", "执行总结", "已完成：步骤")):
                seen_message.add(content)
                kind = "progress" if (step_message or "步骤" in content or "已规划" in content) else "assistant"
                conversations.append({"conversation_item_id": _id("conversation", episode_id, str(len(conversations))),
                                      "kind": kind, "model_visible": False, "user_visible": True,
                                      "timestamp": timestamp, "content_preview": content[:240]})

    reduced: dict[str, Any] = {
        "schema_version": 3, "episode_id": episode_id, "task_id": task_id, "instance_id": instance_id,
        "project_id": project_id,
        "status": (trajectory.get("outcome") or {}).get("status") or task.get("completion_status") or "unknown",
        "conversation_items": conversations, "model_calls": model_calls, "tool_calls": tool_calls,
        "artifacts": [{"artifact_id": _id("artifact", episode_id, value), "path": value,
                       "exists": Path(value).is_file(), "sha256": _sha256(Path(value))} for value in sorted(artifact_paths)],
        "delivery": {"confirmed": bool(receipt.get("delivery_confirmed")), "receipt_id": receipt_id,
                     "receipt_invalidated": invalidated},
        "failure_classes": sorted(set(failures)),
        "failure_details": failure_details,
        "experiment_intervention": intervention,
        "source_refs": [str(task_path), str(log_path)], "reduced_at": now_iso(),
    }
    reduced["reward_vector"] = reward_vector(trajectory, reduced)
    manifest = {
        "schema_version": 3, "trace_id": _id("trace", episode_id), "episode_id": episode_id,
        "task_id": task_id, "instance_id": instance_id, "created_at": now_iso(),
        "observe_first_interpret_later": True, "production_mutation_allowed": False,
        "sources": [{"path": str(path), "sha256": _sha256(path)} for path in (task_path, log_path)],
        "files": {"raw_events": "trace.jsonl", "reduced_state": "state.json"},
    }
    bundle.mkdir(parents=True, exist_ok=True)
    atomic_json(bundle / "manifest.json", manifest)
    # Reducer output is replaceable. Raw source logs remain authoritative; this
    # normalized spine is rebuilt atomically from them rather than appended twice.
    trace_path = bundle / "trace.jsonl"
    trace_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in raw_events), encoding="utf-8")
    atomic_json(bundle / "state.json", reduced)
    atomic_json(root / "share" / "mind" / "governance" / "rl" / "reward_vector_spec.json", REWARD_VECTOR_SPEC)
    return {"ok": True, "status": "reduced", "episode_id": episode_id, "bundle": str(bundle),
            "manifest": manifest, "state": reduced}


def reduce_manual_history(workspace: str, *, instance_id: str, project_id: str = "",
                          limit: int = 50) -> dict[str, Any]:
    """Reduce recent governed manual tasks without changing their runtime state."""
    root = workspace_root(workspace)
    tasks_root = root / "instances" / instance_id / "state" / "tasks"
    candidates = sorted(
        [path for path in tasks_root.glob("*/task_log.jsonl") if path.is_file()],
        key=lambda value: value.stat().st_mtime,
        reverse=True,
    )
    reduced: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for log_path in candidates:
        if len(reduced) >= max(1, int(limit)):
            break
        rows = _jsonl(log_path)
        governance = next((row for row in reversed(rows) if row.get("event") == "manual_iteration_governance"), {})
        embedded = (((governance.get("trajectory") or {}).get("trajectory")) or {})
        row_project = str(embedded.get("project_id") or (governance.get("receipt") or {}).get("project_id") or "")
        if not governance or (project_id and row_project != project_id):
            continue
        task_id = log_path.parent.name
        result = reduce_task_episode(str(root), instance_id=instance_id, task_id=task_id, trajectory=embedded or None)
        if result.get("ok"):
            reduced.append({"task_id": task_id, "episode_id": result.get("episode_id"),
                            "bundle": result.get("bundle"), "state": result.get("state")})
        else:
            skipped.append({"task_id": task_id, "status": str(result.get("status") or "failed")})
    return {"ok": True, "status": "history_reduced", "instance_id": instance_id,
            "project_id": project_id, "reduced": len(reduced), "episodes": reduced, "skipped": skipped}


def try_reduce_manual_task(workspace: str, *, instance_id: str, task_id: str) -> dict[str, Any]:
    """Best-effort hot-path adapter. Trace failure must never fail the task."""
    try:
        return reduce_task_episode(workspace, instance_id=instance_id, task_id=task_id)
    except Exception as exc:  # diagnostic code is deliberately fail-open
        return {"ok": False, "status": "trace_best_effort_failed", "error": str(exc)[:1000]}
