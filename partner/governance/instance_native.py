"""Event-driven, project-native continuation for Partner instances.

This is deliberately not a Campaign.  Each instance owns its project state and
chooses between one project action and one bounded learning interruption from
durable task/Receipt evidence.  The global runtime grants a resource-adaptive
number of compute slots, but it does not create curricula or periodic reports.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .models import now_iso
from .project_loop import request_next_action
from . import scheduler as _scheduler_mod
from .storage import atomic_json, latest_receipt, workspace_root


PROJECTS = {
    "01": ("xiaohongshu_operations", "小红书账户推送与维护"),
    "02": ("molecular_generation", "分子生成方法创新与实践"),
    "03": ("molecular_dynamics_study", "分子动力学模拟学习与尝试"),
    "04": ("literature_github_learning", "文献与 GitHub 代码真实学习、复现和采用"),
    "05": ("hermes_partner_explore", "hermes 与 partner 代码探索并写新 skill 与 event"),
}
TERMINAL = {"done", "failed"}
BUSY = {"created", "planning", "running", "waiting", "executing"}


def authoritative_terminal_event(event: dict[str, Any]) -> bool:
    """Accept only the terminal written after manual governance settles.

    Harness writes an earlier execution terminal before Receipt/truth/reward
    finalization.  Advancing on that hint creates overlapping project actions.
    """
    return bool(
        str(event.get("status") or "") in TERMINAL
        and str((event.get("data") or {}).get("source") or "")
        == "manual_stop_project_finalization"
    )


@dataclass
class NativeInstanceState:
    schema_version: int = 1
    instance_id: str = ""
    project_id: str = ""
    enabled: bool = False
    phase: str = "WAITING"
    pending_message_id: str = ""
    pending_kind: str = ""
    last_task_id: str = ""
    last_terminal_status: str = ""
    project_steps: int = 0
    project_steps_since_yield: int = 0
    learning_interruptions: int = 0
    consecutive_failures: int = 0
    suspended_project_request: str = ""
    reason: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def state_dir(workspace: str | Path) -> Path:
    return workspace_root(str(workspace)) / "state/instance_native"


def state_path(workspace: str | Path, instance_id: str) -> Path:
    return state_dir(workspace) / f"instance_{instance_id}.json"


def event_path(workspace: str | Path) -> Path:
    return state_dir(workspace) / "events.jsonl"


def load_native_runtime_config(workspace: str | Path) -> dict[str, Any]:
    path = workspace_root(str(workspace)) / "config/partner_config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        runtime = dict(data.get("runtime") or {})
    except (OSError, TypeError, ValueError):
        runtime = {}
    enabled = [str(value) for value in runtime.get("instance_native_enabled_instances") or []]
    return {
        "enabled": bool(runtime.get("instance_native_autonomy", False)),
        "enabled_instances": [value for value in enabled if value in PROJECTS],
        # ADR 0062: defer to scheduler.effective_max_active which itself reads
        # partner_config.json OR falls back to /proc-based host estimation.
        # The legacy `or 2` hard floor is removed.
        "max_active": _scheduler_mod.effective_max_active(
            workspace_root=str(workspace)),
        "max_learning_interruptions_per_failure": max(
            1, int(runtime.get("instance_native_max_learning_interruptions_per_failure") or 1)),
        "slot_quantum_project_steps": max(
            1, int(runtime.get("instance_native_slot_quantum_project_steps") or 2)),
    }


def load_state(workspace: str | Path, instance_id: str) -> NativeInstanceState:
    # Bug #58 P0.3 follow-up fix (ADR 0066): read both the legacy
    # ``state/instance_native/instance_XX.json`` schema and the newer
    # ``instances/<id>/state/native_state.json`` schema.  Production
    # instances have both files; the legacy one wins because it carries
    # the authoritative phase/pending_message_id from
    # recover_or_start, while the newer one carries the project_id the
    # instance has actually switched to.  Merge them with the newer
    # file's project_id taking precedence.
    default_project_id = PROJECTS.get(instance_id, ("", ""))[0]
    legacy: dict = {}
    newer: dict = {}
    try:
        legacy = json.loads(
            state_path(workspace, instance_id).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        pass
    # Newer schema path: instances/<id>/state/native_state.json
    newer_path = Path(workspace) / "instances" / instance_id / "state" / "native_state.json"
    if newer_path.exists():
        try:
            newer = json.loads(newer_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            pass
    if not legacy and not newer:
        return NativeInstanceState(instance_id=instance_id,
                                   project_id=default_project_id)
    # Start with legacy (authoritative for phase / pending), overlay
    # newer for project_id (instance-switched-to truth).
    merged: dict = dict(legacy)
    if newer.get("project_id"):
        merged["project_id"] = newer["project_id"]
    merged.setdefault("instance_id", instance_id)
    allowed = NativeInstanceState.__dataclass_fields__
    return NativeInstanceState(**{key: item for key, item in merged.items()
                                  if key in allowed})


def save_state(workspace: str | Path, state: NativeInstanceState) -> None:
    state.updated_at = now_iso()
    atomic_json(state_path(workspace, state.instance_id), state.to_dict())


def _append_event(workspace: str | Path, event_type: str, state: NativeInstanceState,
                  **payload: Any) -> None:
    path = event_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"schema_version": 1, "event_type": event_type, "at": now_iso(),
           "instance_id": state.instance_id, "project_id": state.project_id,
           "phase": state.phase, **payload}
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _task_values(root: Path, instance_id: str) -> list[tuple[Path, dict[str, Any]]]:
    output: list[tuple[Path, dict[str, Any]]] = []
    directory = root / "instances" / instance_id / "state/tasks"
    for path in directory.glob("*/task_instance.json") if directory.exists() else []:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        output.append((path, value))
    return sorted(output, key=lambda pair: pair[0].stat().st_mtime, reverse=True)


def instance_busy(workspace: str | Path, instance_id: str) -> bool:
    for _path, value in _task_values(workspace_root(str(workspace)), instance_id)[:12]:
        if str(value.get("completion_status") or "").lower() in BUSY:
            return True
    return False


def _pending_dispatch_exists(workspace: str | Path, instance_id: str,
                             message_id: str) -> bool:
    """Return whether a pending native message still has a durable owner."""
    if not message_id:
        return False
    root = workspace_root(str(workspace))
    state_root = root / "instances" / instance_id / "state"
    for _path, value in _task_values(root, instance_id)[:40]:
        metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
        if message_id in {
            str(metadata.get("inbox_message_id") or ""),
            str(value.get("inbox_message_id") or ""),
        } and str(value.get("completion_status") or "").lower() in BUSY:
            return True
    # Inbox is append-only: a historical row is not proof that work remains.
    # Once the instance has persisted the id in its seen ledger, only a live
    # linked Task can own it. Treating any old line as pending stranded native
    # state after service restarts.
    seen_path = state_root / "desktop_inbox_seen_ids.json"
    try:
        seen = json.loads(seen_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        seen = []
    if message_id in {str(value) for value in (seen if isinstance(seen, list) else [])}:
        return False
    inbox = state_root / "desktop_inbox.jsonl"
    try:
        return any(message_id in line for line in inbox.read_text(encoding="utf-8").splitlines())
    except OSError:
        pass
    return False


def _native_marker(value: dict[str, Any]) -> bool:
    text = str(value.get("user_message") or value.get("root_user_request") or "")
    return "[instance_native=true]" in text


def _terminal_task(workspace: str | Path, instance_id: str,
                   task_id: str) -> tuple[Path | None, dict[str, Any]]:
    root = workspace_root(str(workspace))
    exact = root / "instances" / instance_id / "state/tasks" / task_id / "task_instance.json"
    candidates = [(exact, {})] if exact.exists() else []
    candidates.extend(_task_values(root, instance_id)[:20])
    for path, cached in candidates:
        try:
            value = cached or json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if str(value.get("task_id") or path.parent.name) == task_id:
            return path, value
    return None, {}


def _failure_evidence(value: dict[str, Any]) -> tuple[bool, str]:
    status = str(value.get("completion_status") or "").lower()
    governance = dict((value.get("metadata") or {}).get("manual_iteration_governance") or {})
    accepted = bool(status == "done" and governance.get("ok", True))
    reason = str(governance.get("error") or governance.get("status") or status)
    return (not accepted), reason


def _should_continue_project(workspace: str | Path, state: NativeInstanceState) -> bool:
    """Read project_state.json; if status is iterating (or active with
    last_iteration_at < 1h), this project should keep receiving tasks."""
    try:
        from partner.evolution.sprint18_unified_patch import (
            project_should_continue, normalize_project_status,
        )
        ps_path = workspace_root(str(workspace)) / "share/projects" / state.project_id / "governance/project_state.json"
        if not ps_path.exists():
            return True
        data = json.loads(ps_path.read_text(encoding="utf-8"))
        status = normalize_project_status(data.get("status"))
        last_iteration_at = data.get("last_iteration_at")
        return project_should_continue(
            status,
            has_unfinished_iteration=bool(data.get("allow_continue", True)),
            last_iteration_at=last_iteration_at,
        )
    except Exception:
        return True


def _append_iteration_handoff(workspace: str | Path, request: str,
                              state: NativeInstanceState) -> str:
    if not _should_continue_project(workspace, state):
        return request
    handoff = (
        "\n\n【迭代模式：此项目处于 iterating 状态】\n"
        "- 本轮必须选定与上一轮不同的指标或样本空间（不能重做一样的验证）；\n"
        "- 完成后请更新 share/projects/<project_id>/governance/project_state.json "
        "里的 last_iteration_at、last_iteration_kind 字段；\n"
        "- 真实外部动作的产物路径必须落到 external_artifacts/<UTC日期>_<kind>/ 下。\n"
    )
    return request + handoff


def _next_project_request_dict(
    workspace: str | Path, state: NativeInstanceState,
) -> dict[str, Any]:
    """Build the next project step request as a structured dict.

    Returns ``{"is_fallback": False, "request": str, "reason": "..."}``
    when the project loop produced a real proposed action, or
    ``{"is_fallback": True, "request": str, "reason": str, ...}``
    when the call had to assemble a generic template because the
    project has no proposed next action.

    ADR 0065: callers must consume ``is_fallback`` rather than
    string-sniffing the rendered request — that broke every time
    the template wording was changed.
    """
    root = workspace_root(str(workspace))
    proposed = request_next_action(str(root), {"project_id": state.project_id})
    if proposed.get("ok") and proposed.get("status") == "proposed":
        action = dict(proposed.get("action") or {})
        params = dict(action.get("params") or {})
        request_text = str(params.get("user_request") or (
            f"【项目续跑】只执行 Event {action.get('event_type')}，参数："
            f"{json.dumps(params, ensure_ascii=False)}"
        ))
        return {
            "is_fallback": False,
            "request": request_text,
            "reason": "project_loop.request_next_action:proposed",
            "action_id": action.get("action_id", ""),
            "event_type": action.get("event_type", ""),
            "params": params,
        }
    _project, goal = PROJECTS[state.instance_id]
    receipt = latest_receipt(str(root), state.project_id)
    receipt_ref = receipt.receipt_id if receipt else "none"
    project_root = root / "share/projects" / state.project_id
    source_paths = [
        project_root / "project_brief.md",
        project_root / "project_contract.json",
        project_root / "state.md",
        project_root / "governance/project_state.json",
    ]
    if receipt:
        matches = sorted((project_root / "governance/receipts").glob(
            f"*_{receipt.receipt_id}.json"))
        if matches:
            source_paths.append(matches[-1])
        source_paths.extend(Path(value) for value in receipt.artifacts[:4])
    grounded = [str(path.resolve()) for path in source_paths if path.is_file()]
    sources = "\n".join(f"- {path}" for path in dict.fromkeys(grounded))
    base_request = (
        f"【{state.instance_id}实例原生项目续跑】项目：{goal}。"
        f"最新 Receipt={receipt_ref}。只从下面真实存在的路径开始读取，不得猜测或虚构路径：\n"
        f"{sources}\n"
        # ADR 0061 real-action contract: a project step is only an advance
        # when it ships a verifiable external action and at least one
        # real artifact.  Reading project files and writing another analysis
        # is not an advance.
        "\n【硬约束·真实外部动作】本步必须包含至少一项可验证的真实外部动作，"
        "并写入对应真实产物（artifacts 必须是真实写入的文件路径或外部记录 ID），"
        "不得仅通过读取项目文件再生成一份分析报告/continuation.md 来冒充推进：\n"
        "- 计算/实验：跑脚本并把输出写到非 share/projects/<project>/reports 的真实目录。\n"
        "- 真实检索：抓取外部页面/PDF 并落到 share/projects/<project>/external_sources/。\n"
        "- 真实代码改动：修改 partner/<pkg>/ 下任一文件后跑对应 pytest 并附 pytest 摘要。\n"
        "- 真实数据写入：把新数据落到 share/projects/<project>/external_artifacts/<receipt_id>/。\n"
        "actions_executed 必须显式记录上述外部动作（如 'exec:python3 X.py'、"
        "'web.fetch:URL'、'pytest:test_xxx::case_yyy'），不能只是 'read_file'、'generate_text'。"
        "findings 必须包含「真实外部动作 + 真实产物路径 + 具体结论/数字/失败原因」三要素，"
        "不得出现「已完成本地微计划执行」这类与上轮 findings 雷同的占位句。\n"
        "【边界·不越权】外部发布、账户登录、生产策略晋升和直接修改生产源码仍须遵守既有审批边界；"
        "到达边界时改做边界前可验证工作并记录所需授权，不得擅自越权。"
        "若实际遇到失败、矛盾、高不确定性或知识缺口，如实记录，不要绕过或伪造进展。\n"
    )
    fallback_request = _append_iteration_handoff(workspace, base_request, state)
    return {
        "is_fallback": True,
        "request": fallback_request,
        "reason": "project_loop.request_next_action:no_proposed_action",
        "proposed_status": proposed.get("status", ""),
        "stop_reason": proposed.get("stop_reason", ""),
    }


def _next_project_request(workspace: str | Path, state: NativeInstanceState) -> str:
    """Back-compat wrapper around ``_next_project_request_dict``.

    Returns the rendered request string only — callers that need to
    distinguish a real proposed action from the legacy fallback
    template must use ``_next_project_request_dict`` and check
    ``is_fallback``.
    """
    return _next_project_request_dict(workspace, state)["request"]


def _findings_signature(findings: list[str]) -> set[str]:
    """Return the set of normalised finding sentences used for near-duplicate detection."""
    out: set[str] = set()
    for item in findings or []:
        text = re.sub(r"\s+", " ", str(item or "")).strip().lower()
        if len(text) >= 8:
            out.add(text)
    return out


def _list_project_artifacts(root: Path, project_id: str) -> set[str]:
    project_root = root / "share/projects" / project_id
    if not project_root.exists():
        return set()
    out: set[str] = set()
    receipts = project_root / "governance/receipts"
    if receipts.exists():
        for path in receipts.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for artifact in value.get("artifacts", []) or []:
                out.add(str(Path(str(artifact)).resolve()))
    return out


def _assess_step_real_progress(
    root: Path,
    project_id: str,
    *,
    findings: list[str],
    actions_executed: list[str],
    artifacts: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    """ADR 0061 guard: thin wrapper around partner.governance.real_action_contract.

    Keeps the historic instance_native signature so callers/tests stay stable
    while ensuring both runtimes (instance-native and manual) evaluate against
    the same rule set.
    """
    try:
        from .real_action_contract import assess as _contract_assess
    except Exception as exc:  # pragma: no cover - import guard
        return {"ok": False, "violation": "contract_unavailable",
                "evidence": {"error": str(exc)}}
    history_window = int(config.get("max_repeat_findings") or 2)
    return _contract_assess(
        workspace_root_path=root,
        project_id=project_id,
        findings=list(findings or []),
        actions_executed=list(actions_executed or []),
        artifacts=list(artifacts or []),
        max_repeat_findings=max(2, history_window),
        require_external_artifact=bool(config.get("require_external_artifact", True)),
    )


def _learning_request(state: NativeInstanceState, task_id: str, task_path: Path | None,
                      reason: str) -> str:
    evidence = str(task_path.parent if task_path else task_id)
    return (
        f"【{state.instance_id}实例 Partner 自进化诊断】[learning_for_task={task_id}] "
        f"失败或知识缺口：{reason[:500]}。真实证据：{evidence}。"
        "这是面向 Partner 自身机制的 self-evolution，不是面向外部知识的主动学习。"
        "以 Event-first 方式执行一次受限 observe→select→diagnose→repair proposal；"
        "其中 diagnose 必须调用 LLM causal critic，并保留机器证据硬门；"
        "如果已有可执行 Candidate，做隔离 baseline/candidate 验证。"
        "必须记录 promoted/rejected/inconclusive，不修改 control_policy。只有白名单代码面、"
        "baseline-fail/candidate-pass、聚焦回归、原子回滚四门同时通过时，才允许自动实施；"
        "其余 Candidate 不得自行批准生产。"
        "完成后返回原项目，不开启 Campaign 或定时轮询。"
    )


def _ensure_episode(workspace: str | Path, instance_id: str, task_id: str) -> dict[str, Any]:
    """Materialize the real failed task as learning input before dispatch."""
    try:
        from .episode_trace import reduce_task_episode

        return reduce_task_episode(
            str(workspace_root(str(workspace))), instance_id=instance_id, task_id=task_id,
        )
    except Exception as exc:
        return {"ok": False, "status": "episode_reduction_failed", "error": str(exc)}


def _enqueue(workspace: str | Path, state: NativeInstanceState, *, kind: str,
             request: str) -> dict[str, Any]:
    root = workspace_root(str(workspace))
    digest = hashlib.sha256(
        # updated_at is changed whenever a consumed/orphaned dispatch is
        # recovered. Without a dispatch generation, re-enqueue produced the
        # same already-seen message id and the instance silently ignored it.
        f"{state.instance_id}|{kind}|{state.last_task_id}|{state.updated_at}|{time.time_ns()}|{request}".encode("utf-8")
    ).hexdigest()[:16]
    message_id = f"native_{state.instance_id}_{digest}"
    marker = (
        f"\n\n[instance_native=true] [native_kind={kind}] [project_id={state.project_id}]"
    )
    project_label = PROJECTS.get(state.instance_id, (state.project_id, state.project_id))[1]
    headed_request = (
        f"【{state.instance_id}实例项目：{project_label}】\n"
        f"项目 ID：{state.project_id}\n\n{request}"
    )
    row = {
        "id": message_id, "message_id": message_id, "role": "user",
        # Human/project content must lead.  Existing title and project-context
        # parsers intentionally read the first line; putting control markers
        # first redirects the task into a fake marker-named project.
        "text": headed_request + marker, "content": headed_request + marker,
        "source": "instance_native", "channel": "local",
        "sender_id": f"partner_{state.instance_id}_self",
        "sender_name": f"Partner{state.instance_id}项目内部续跑",
        "created_at": now_iso(),
    }
    inbox = root / "instances" / state.instance_id / "state/desktop_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    state.pending_message_id = message_id
    state.pending_kind = kind
    state.phase = "LEARNING_DISPATCHED" if kind == "learning" else "PROJECT_DISPATCHED"
    state.reason = "meaningful terminal-triggered transition"
    save_state(workspace, state)
    _append_event(workspace, "native_task_dispatched", state,
                  message_id=message_id, kind=kind)
    return {"ok": True, "status": "dispatched", "message_id": message_id,
            "kind": kind, "instance_id": state.instance_id}


def recover_or_start(workspace: str | Path, instance_id: str) -> dict[str, Any]:
    config = load_native_runtime_config(workspace)
    state = load_state(workspace, instance_id)
    state.enabled = bool(config["enabled"] and instance_id in config["enabled_instances"])
    save_state(workspace, state)
    if not state.enabled:
        return {"ok": False, "status": "native_disabled", "instance_id": instance_id}
    if state.phase == "BLOCKED":
        return {"ok": False, "status": "blocked_requires_evidence_change",
                "instance_id": instance_id, "reason": state.reason}
    if instance_busy(workspace, instance_id):
        state.phase, state.reason = "WAIT_TASK", "instance already has a live task"
        save_state(workspace, state)
        return {"ok": True, "status": "busy", "instance_id": instance_id}
    if state.pending_message_id:
        # Preserve a real queued/live dispatch, but recover state left behind
        # by old runtimes that wrote pending_message_id without an inbox row
        # or task owner.  Such a phantom otherwise stops the project forever.
        if _pending_dispatch_exists(workspace, instance_id, state.pending_message_id):
            return {"ok": True, "status": "pending_dispatch", "instance_id": instance_id,
                    "message_id": state.pending_message_id}
        stale = state.pending_message_id
        state.pending_message_id = ""
        state.pending_kind = ""
        state.phase = "WAITING"
        state.consecutive_failures = 0
        state.learning_interruptions = 0
        state.reason = "recovered orphaned native pending dispatch"
        save_state(workspace, state)
        _append_event(workspace, "native_pending_recovered", state,
                      stale_message_id=stale)
    # Slot admission must be immediate.  The former startup decision-loop
    # model call consumed up to 60 seconds per instance before the subprocess
    # even existed.  Metacognitive work is now triggered only by a settled
    # failure/knowledge-gap terminal in handle_terminal.
    return _enqueue(workspace, state, kind="project",
                    request=_next_project_request(workspace, state))


def handle_terminal(workspace: str | Path, *, instance_id: str,
                    task_id: str) -> dict[str, Any]:
    config = load_native_runtime_config(workspace)
    state = load_state(workspace, instance_id)
    state.enabled = bool(config["enabled"] and instance_id in config["enabled_instances"])
    if not state.enabled:
        save_state(workspace, state)
        return {"ok": False, "status": "native_disabled", "instance_id": instance_id}
    if state.last_task_id == task_id:
        return {"ok": True, "status": "terminal_already_consumed", "task_id": task_id}
    path, value = _terminal_task(workspace, instance_id, task_id)
    if not value or str(value.get("completion_status") or "").lower() not in TERMINAL:
        return {"ok": False, "status": "terminal_not_settled", "task_id": task_id}
    # Ordinary user tasks retain manual_stable stop semantics.  Only explicit
    # project next_actions or native tasks may enter autonomous continuation.
    governance = dict((value.get("metadata") or {}).get("manual_iteration_governance") or {})
    receipt = dict(governance.get("receipt") or {})
    has_next = bool(receipt.get("next_actions"))
    if not _native_marker(value) and not has_next:
        return {"ok": True, "status": "manual_task_not_auto_continued", "task_id": task_id}
    failure, reason = _failure_evidence(value)
    unresolved = [str(item).strip() for item in receipt.get("unresolved_questions") or []
                  if str(item).strip()]
    governed_trajectory = ((governance.get("trajectory") or {}).get("trajectory") or {})
    governed_outcome = governed_trajectory.get("outcome") or {}
    duplicate_outcome = bool(governed_outcome.get("duplicate_outcome"))
    knowledge_gap = bool(not failure and (
        (unresolved and not has_next) or duplicate_outcome
    ))
    if knowledge_gap:
        reason = (
            "outcome.duplicate_semantic_result: current Event produced no new business evidence"
            if duplicate_outcome
            else "unresolved project knowledge gap: " + "; ".join(unresolved[:3])
        )
    completed_kind = state.pending_kind or (
        "learning" if "[native_kind=learning]" in str(value.get("user_message") or "") else "project"
    )
    state.last_task_id = task_id
    state.last_terminal_status = str(value.get("completion_status") or "")
    state.pending_message_id = ""
    state.pending_kind = ""
    if completed_kind == "learning":
        state.learning_interruptions += 1
        if failure:
            state.consecutive_failures += 1
            state.phase, state.reason = "BLOCKED", "bounded learning interruption failed"
            save_state(workspace, state)
            _append_event(workspace, "native_learning_blocked", state,
                          task_id=task_id, reason=reason,
                          semantic_kind="partner_self_evolution")
            return {"ok": False, "status": "learning_blocked", "task_id": task_id,
                    "reason": reason}
        state.consecutive_failures = 0
        state.phase = "RESUME_PROJECT"
        save_state(workspace, state)
        _append_event(workspace, "native_learning_completed", state, task_id=task_id,
                      semantic_kind="partner_self_evolution")
        return _enqueue(workspace, state, kind="project",
                        request=state.suspended_project_request
                        or _next_project_request(workspace, state))
    state.project_steps += 1
    state.project_steps_since_yield += 1
    # One duplicate is a negative reward for the project-action bandit, not an
    # immediate excuse to run a five-step internal diagnosis.  Give the newly
    # informed selector one counterfactual action first; only a repeated
    # semantic plateau escalates into Partner self-evolution.
    if duplicate_outcome and not failure and state.consecutive_failures < 1:
        state.consecutive_failures = 1
        state.phase = "ADVANCE_PROJECT"
        state.reason = "semantic duplicate recorded as EGPL negative; select counterfactual arm"
        save_state(workspace, state)
        _append_event(workspace, "native_project_reward_observed", state,
                      task_id=task_id, reward=-0.1,
                      semantic_kind="project_policy_learning")
        return _enqueue(workspace, state, kind="project",
                        request=_next_project_request(workspace, state))
    if failure or knowledge_gap:
        state.consecutive_failures += 1
        # A learning interruption is useful only when the immediately resumed
        # project gets one chance to apply it.  If that project still fails,
        # do not let one unhealthy instance monopolise a scarce runtime slot
        # by starting another learn/retry loop.  Yield it and let the normal
        # rotation revisit the project with its durable evidence later.
        if state.learning_interruptions >= int(
                config["max_learning_interruptions_per_failure"]):
            state.project_steps_since_yield = 0
            state.learning_interruptions = 0
            state.phase = "YIELD_SLOT"
            state.reason = "bounded learning budget exhausted after failed retry"
            state.suspended_project_request = _next_project_request(workspace, state)
            save_state(workspace, state)
            _append_event(workspace, "native_slot_yielded", state,
                          task_id=task_id, reason=reason,
                          trigger="learning_budget_exhausted")
            return {"ok": True, "status": "yield_slot", "instance_id": instance_id,
                    "task_id": task_id, "reason": reason}
        state.suspended_project_request = _next_project_request(workspace, state)
        episode = _ensure_episode(workspace, instance_id, task_id)
        if not episode.get("ok"):
            state.phase, state.reason = "BLOCKED", "failed task could not be reduced to Episode"
            save_state(workspace, state)
            _append_event(workspace, "native_learning_blocked", state,
                          task_id=task_id, reason=state.reason, episode=episode,
                          semantic_kind="partner_self_evolution")
            return {"ok": False, "status": "learning_input_missing", "task_id": task_id,
                    "episode": episode}
        state.phase = "LEARNING_TRIGGERED"
        save_state(workspace, state)
        _append_event(workspace, "native_learning_triggered", state,
                      task_id=task_id, reason=reason,
                      semantic_kind="partner_self_evolution")
        # Sprint18 §5 task 3: also drive the decision_loop so the failure is
        # reflected as DecisionEvent → branch event sequence in the ledger. This
        # gives LLM reviewers a unified view of "why this branch was chosen after
        # failure" without needing to read native_learning_triggered side-channels.
        try:
            from partner.mind.decision_handoff import (
                dispatch_to_decision_loop, signals_count,
            )
            signals = signals_count(receipt)
            classification = {"failure_class": "bug_in_partner_source",
                              "confidence": 0.6, "signals_count": signals}
            dispatch_to_decision_loop(
                workspace,
                instance_id=instance_id,
                task_state={"phase": "LEARNING_TRIGGERED", "failure_class": "bug_in_partner_source",
                            "reason": reason, "task_id": task_id,
                            "unresolved_questions": receipt.get("unresolved_questions", [])},
                classification=classification,
                budget_seconds=20,
            )
        except Exception as exc:  # noqa: BLE001 — loop never blocks learning enqueue
            _append_event(workspace, "curiosity/budget_run_failed", state,
                          payload={"where": "handle_terminal.failure", "error": str(exc)[:200]})
        return _enqueue(workspace, state, kind="learning",
                        request=_learning_request(state, task_id, path, reason))
    state.consecutive_failures = 0
    state.learning_interruptions = 0
    state.suspended_project_request = ""
    # ADR 0061: block report-only repetition before advancing the project.
    progress_assessment = _assess_step_real_progress(
        workspace_root(str(workspace)), state.project_id,
        findings=list(receipt.get("findings") or []),
        actions_executed=list(receipt.get("actions_executed") or []),
        artifacts=list(receipt.get("artifacts") or []),
        config=config,
    )
    if not progress_assessment.get("ok"):
        violation = str(progress_assessment.get("violation") or "report_only_progress")
        evidence = dict(progress_assessment.get("evidence") or {})
        state.phase = "BLOCKED"
        state.reason = f"ADR 0061 real-action contract violated: {violation}"
        save_state(workspace, state)
        _append_event(workspace, "native_project_blocked", state,
                      task_id=task_id, reason=state.reason,
                      trigger="real_action_contract", evidence=evidence)
        return {"ok": False, "status": "progress_blocked",
                "task_id": task_id, "violation": violation,
                "evidence": evidence, "instance_id": instance_id}
    if (len(config["enabled_instances"]) > int(config["max_active"])
            and state.project_steps_since_yield >= int(config["slot_quantum_project_steps"])):
        state.project_steps_since_yield = 0
        state.phase, state.reason = "YIELD_SLOT", "bounded project quantum completed"
        save_state(workspace, state)
        _append_event(workspace, "native_slot_yielded", state, task_id=task_id)
        return {"ok": True, "status": "yield_slot", "instance_id": instance_id,
                "task_id": task_id}
    state.phase = "ADVANCE_PROJECT"
    save_state(workspace, state)
    _append_event(workspace, "native_project_completed", state, task_id=task_id)
    return _enqueue(workspace, state, kind="project",
                    request=_next_project_request(workspace, state))


def redrive_learning(workspace: str | Path, *, instance_id: str,
                     failed_task_id: str, reason: str) -> dict[str, Any]:
    """Explicitly redrive one rejected learning canary after its route is fixed."""
    config = load_native_runtime_config(workspace)
    state = load_state(workspace, instance_id)
    if not (config["enabled"] and instance_id in config["enabled_instances"]):
        return {"ok": False, "status": "native_disabled", "instance_id": instance_id}
    if instance_busy(workspace, instance_id):
        return {"ok": False, "status": "instance_busy", "instance_id": instance_id}
    episode = _ensure_episode(workspace, instance_id, failed_task_id)
    if not episode.get("ok"):
        return {"ok": False, "status": "learning_input_missing",
                "instance_id": instance_id, "episode": episode}
    state.enabled = True
    state.pending_message_id = ""
    state.pending_kind = ""
    state.suspended_project_request = _next_project_request(workspace, state)
    state.phase = "LEARNING_TRIGGERED"
    state.reason = str(reason)
    save_state(workspace, state)
    _append_event(workspace, "native_learning_redriven", state,
                  task_id=failed_task_id, reason=reason)
    path, _value = _terminal_task(workspace, instance_id, failed_task_id)
    return _enqueue(
        workspace, state, kind="learning",
        request=_learning_request(state, failed_task_id, path, reason),
    )


def enabled_instances(workspace: str | Path) -> list[str]:
    config = load_native_runtime_config(workspace)
    return list(config["enabled_instances"]) if config["enabled"] else []


def blocked_instances(workspace: str | Path) -> list[dict[str, Any]]:
    """Return every instance whose state machine is currently BLOCKED (ADR 0061)."""
    output: list[dict[str, Any]] = []
    for instance_id in enabled_instances(workspace):
        try:
            state = load_state(workspace, instance_id)
        except Exception:
            continue
        if state.phase != "BLOCKED":
            continue
        output.append({"instance_id": instance_id,
                       "phase": state.phase,
                       "reason": state.reason,
                       "consecutive_failures": state.consecutive_failures,
                       "learning_interruptions": state.learning_interruptions,
                       "updated_at": state.updated_at})
    return output


def unblock_blocked_instance(workspace: str | Path, instance_id: str,
                              *, evidence_paths: list[str],
                              reason: str = "") -> dict[str, Any]:
    """Return a blocked instance to YIELD_SLOT only after real external evidence lands.

    evidence_paths must be absolute paths to files that were created or
    modified outside the partner runtime (a user message, an external fetch,
    a freshly-written artifact).  The state machine cannot skip this gate —
    a project that ran itself in circles must surface real events before it
    runs again.  This is the only sanctioned way out of BLOCKED.
    """
    if instance_id not in PROJECTS:
        return {"ok": False, "status": "unknown_instance", "instance_id": instance_id}
    if not evidence_paths:
        return {"ok": False, "status": "missing_evidence",
                "instance_id": instance_id,
                "error": "evidence_paths is required and must be non-empty"}
    cleaned: list[str] = []
    for raw in evidence_paths:
        try:
            path = Path(str(raw)).expanduser().resolve()
        except OSError as exc:
            return {"ok": False, "status": "bad_evidence_path",
                    "evidence_path": str(raw), "error": str(exc),
                    "instance_id": instance_id}
        if not path.is_file():
            return {"ok": False, "status": "evidence_not_real",
                    "evidence_path": str(raw),
                    "error": "evidence path does not point to a real file",
                    "instance_id": instance_id}
        if "share/projects" in str(path) and "/reports/" in str(path):
            return {"ok": False, "status": "evidence_is_self_report",
                    "evidence_path": str(raw),
                    "error": "evidence under share/projects/*/reports/ is not real",
                    "instance_id": instance_id}
        cleaned.append(str(path))
    state = load_state(workspace, instance_id)
    if state.phase != "BLOCKED":
        return {"ok": False, "status": "not_blocked",
                "instance_id": instance_id, "phase": state.phase}
    state.consecutive_failures = 0
    state.learning_interruptions = 0
    state.phase = "YIELD_SLOT"
    state.reason = (
        f"unblocked with external evidence ({reason or 'manual unblock'}): "
        + "; ".join(cleaned[:3])
    )
    save_state(workspace, state)
    _append_event(workspace, "native_unblocked", state,
                  reason=state.reason,
                  evidence_paths=cleaned,
                  trigger="external_evidence")
    return {"ok": True, "status": "unblocked_to_yield",
            "instance_id": instance_id,
            "evidence_paths": cleaned,
            "phase": state.phase,
            "reason": state.reason}


def yield_blocked_without_evidence(workspace: str | Path, instance_id: str,
                                    *, reason: str) -> dict[str, Any]:
    """Force a stuck BLOCKED instance into a fresh YIELD_SLOT for diagnostic re-inspection.

    Use only when watchdog must yield the slot regardless of evidence; the
    event log records the override and the caller must publish the reason
    so a human can clean up the situation later.  This is the harness's
    last-resort lever, not the project author's.
    """
    if instance_id not in PROJECTS:
        return {"ok": False, "status": "unknown_instance", "instance_id": instance_id}
    state = load_state(workspace, instance_id)
    if state.phase != "BLOCKED":
        return {"ok": False, "status": "not_blocked",
                "instance_id": instance_id, "phase": state.phase}
    state.consecutive_failures = 0
    state.learning_interruptions = 0
    state.phase = "YIELD_SLOT"
    state.reason = f"diagnostic yield from BLOCKED: {reason}"
    save_state(workspace, state)
    _append_event(workspace, "native_unblocked", state,
                  reason=state.reason, trigger="diagnostic_yield")
    return {"ok": True, "status": "diagnostic_yielded",
            "instance_id": instance_id, "phase": state.phase,
            "reason": state.reason}
