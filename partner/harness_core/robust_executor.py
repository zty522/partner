from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

import yaml

from .task_instance import TaskInstance


JsonDict = dict[str, Any]
Operation = Callable[[], Any | Awaitable[Any]]


DEFAULT_CONFIG: JsonDict = {
    "external_calls": {
        "timeout": 60,
        "retries": 1,
        "backoff": "linear",
        "per_event": {
            "atomic_http_get": {"timeout": 20, "retries": 1},
            "micro_planner": {"timeout": 45, "retries": 0},
            "batch_planner": {"timeout": 60, "retries": 0},
            "curiosity_engine": {"timeout": 60, "retries": 0},
            "smart_llm_structured_action": {"timeout": 600, "retries": 0},
            "agent_call": {"timeout": 600, "retries": 0},
        },
        "fallback": {
            "enabled": True,
            "generate_placeholder": True,
            "read_on_fallback": True,
            "treat_fallback_as_success": False,
            "placeholder_template": "fallbacks/{task_id}_{event_name}_draft.md",
        },
    },
    "loop_guard": {
        "max_consecutive_failures": 2,
        "max_replan_without_artifact": 3,
    },
    "remediation": {
        "retry_missing_artifact_event_once": True,
        "accept_nonempty_placeholder": False,
        "error_report_name": "_error_report.md",
        "missing_report_name": "_missing_artifacts.md",
    },
}


def _deep_merge(base: JsonDict, patch: JsonDict) -> JsonDict:
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_harness_config(workspace: str) -> JsonDict:
    candidates = [
        os.path.join(workspace, "external_calls.yaml"),
    ]
    # Also check workspace_root/config/ for the shared config
    try:
        from partner.workspace.workspace_layout import workspace_root_from_instance
        root = workspace_root_from_instance(workspace)
        root_candidate = os.path.join(root, "config", "external_calls.yaml")
        if os.path.exists(root_candidate) and root_candidate not in candidates:
            candidates.append(root_candidate)
    except ImportError:
        pass
    config = dict(DEFAULT_CONFIG)
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                config = _deep_merge(config, loaded)
                config["_config_path"] = path
                break
        except Exception:
            continue
    return config


@dataclass
class RobustResult:
    ok: bool
    status: str = "success"
    value: Any = None
    error: str = ""
    attempts: int = 0
    fallback_path: str = ""
    content_preview: str = ""
    original_error: str = ""
    metadata: JsonDict = field(default_factory=dict)


class RobustExecutor:
    def __init__(self, config: JsonDict | None = None) -> None:
        self.config = config or DEFAULT_CONFIG

    @classmethod
    def from_workspace(cls, workspace: str) -> "RobustExecutor":
        return cls(load_harness_config(workspace))

    async def execute(
        self,
        *,
        event_name: str,
        task_instance: TaskInstance,
        operation: Operation,
        on_timeout: str = "",
        on_failure: str = "",
        metadata: JsonDict | None = None,
    ) -> RobustResult:
        cfg = self.config.get("external_calls") or {}
        event_cfg = ((cfg.get("per_event") or {}).get(event_name) or {})
        timeout_raw = event_cfg.get("timeout", cfg.get("timeout", 60))
        timeout = int(timeout_raw) if timeout_raw is not None else 60
        retries = max(0, int(event_cfg.get("retries", cfg.get("retries", 0)) or 0))
        backoff = str(cfg.get("backoff") or "linear").lower()
        attempts = retries + 1
        last_error = ""
        task_instance.append_log("robust_execute_start", {
            "event_name": event_name,
            "timeout": timeout,
            "retries": retries,
            "metadata": metadata or {},
        })
        for index in range(attempts):
            try:
                if inspect.iscoroutinefunction(operation):
                    coro = operation()
                    value = await coro if timeout <= 0 else await asyncio.wait_for(coro, timeout=timeout)
                else:
                    value = await asyncio.to_thread(operation) if timeout <= 0 else await asyncio.wait_for(asyncio.to_thread(operation), timeout=timeout)
                    if inspect.isawaitable(value):
                        value = await value if timeout <= 0 else await asyncio.wait_for(value, timeout=timeout)
                task_instance.append_log("robust_execute_success", {
                    "event_name": event_name,
                    "attempt": index + 1,
                })
                return RobustResult(True, status="success", value=value, attempts=index + 1, metadata=metadata or {})
            except asyncio.TimeoutError:
                last_error = f"timeout after {timeout}s"
                task_instance.append_log("robust_execute_timeout", {
                    "event_name": event_name,
                    "attempt": index + 1,
                    "error": last_error,
                })
            except Exception as exc:
                last_error = str(exc)
                task_instance.append_log("robust_execute_failure", {
                    "event_name": event_name,
                    "attempt": index + 1,
                    "error": last_error,
                })
            if index < attempts - 1:
                await asyncio.sleep(self._backoff_seconds(index + 1, backoff))
        policy = on_timeout if last_error.startswith("timeout") else on_failure
        if policy == "fail_fast":
            return RobustResult(False, status="failed", error=last_error, attempts=attempts, original_error=last_error, metadata=metadata or {})
        if policy == "generate_placeholder" or (not policy and self._fallback_enabled()):
            path = self._generate_placeholder(task_instance, event_name, last_error)
            content = self._read_fallback_content(path) if self._fallback_read_on_fallback() else ""
            if content and self._fallback_treat_as_success():
                task_instance.append_log("robust_execute_fallback_success", {
                    "event_name": event_name,
                    "fallback_path": path,
                    "content_length": len(content),
                    "original_error": last_error,
                })
                return RobustResult(
                    False,
                    status="fallback_success",
                    value={"content": content, "fallback_path": path, "is_fallback": True},
                    error=last_error,
                    attempts=attempts,
                    fallback_path=path,
                    content_preview=content[:500],
                    original_error=last_error,
                    metadata=metadata or {},
                )
            return RobustResult(False, status="fallback_generated", error=last_error, attempts=attempts, fallback_path=path, original_error=last_error, metadata=metadata or {})
        return RobustResult(False, status="failed", error=last_error, attempts=attempts, original_error=last_error, metadata=metadata or {})

    def _backoff_seconds(self, attempt: int, backoff: str) -> float:
        if backoff == "none":
            return 0
        if backoff == "exponential":
            return min(8.0, float(2 ** max(0, attempt - 1)))
        return min(5.0, float(attempt))

    def _fallback_enabled(self) -> bool:
        fallback = (self.config.get("external_calls") or {}).get("fallback") or {}
        return bool(fallback.get("enabled") and fallback.get("generate_placeholder"))

    def _fallback_read_on_fallback(self) -> bool:
        fallback = (self.config.get("external_calls") or {}).get("fallback") or {}
        return bool(fallback.get("read_on_fallback", True))

    def _fallback_treat_as_success(self) -> bool:
        fallback = (self.config.get("external_calls") or {}).get("fallback") or {}
        return bool(fallback.get("treat_fallback_as_success", True))

    def _read_fallback_content(self, path: str) -> str:
        if not path or not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def _generate_placeholder(self, task: TaskInstance, event_name: str, error_message: str) -> str:
        fallback = (self.config.get("external_calls") or {}).get("fallback") or {}
        template = str(fallback.get("placeholder_template") or "fallbacks/{task_id}_{event_name}_draft.md")
        rel_path = template.format(
            task_id=task.task_id,
            event_name=str(event_name or "event").replace(os.sep, "_"),
        )
        path = rel_path if os.path.isabs(rel_path) else os.path.join(task.working_dir, rel_path)
        root = os.path.abspath(task.working_dir)
        full = os.path.abspath(path)
        if not (full == root or full.startswith(root + os.sep)):
            full = os.path.join(task.working_dir, "fallbacks", f"{task.task_id}_{event_name}_draft.md")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        content = self._render_placeholder(task, event_name, error_message)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
        task.append_log("fallback_placeholder_generated", {
            "event_name": event_name,
            "path": full,
            "error": error_message,
        })
        return full

    def _render_placeholder(self, task: TaskInstance, event_name: str, error_message: str) -> str:
        now = datetime.now().isoformat()
        return (
            f"# Fallback Draft\n\n"
            f"- task_id: {task.task_id}\n"
            f"- event_name: {event_name}\n"
            f"- timestamp: {now}\n"
            f"- error: {error_message or 'unknown error'}\n\n"
            "外部调用未能在当前策略内完成。本文件作为结构化占位交付物，后续事件可以基于它继续整理结果、说明限制或生成错误报告。\n\n"
            "## 用户原始请求\n\n"
            f"{task.user_message}\n"
        )
