from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from ..harness_core import ArtifactValidator, RobustExecutor, TaskInstance, load_harness_config
from ..mind.harness import EventRegistry, MicroPlan, _json_from_llm, _normalize_micro_plan

logger = logging.getLogger(__name__)


DEFAULT_CURIOSITY_CONFIG: dict[str, Any] = {
    "enabled": True,
    "max_depth": 2,
    "min_search_results": 3,
    "min_artifact_length": 100,
    "required_fields_for_breakthrough": ["limitation", "novel_approach"],
}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_config(workspace: str) -> dict[str, Any]:
    candidates = [
        os.path.join(workspace, "config", "curiosity.yaml"),
        os.path.join(workspace, "curiosity.yaml"),
    ]
    config = dict(DEFAULT_CURIOSITY_CONFIG)
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
        except Exception as exc:
            logger.debug("[CURIOSITY] failed to load config %s: %s", path, exc)
    return config


def _safe_read(path: str, limit: int = 12000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except Exception:
        return ""


def _workspace_files(task: TaskInstance) -> list[str]:
    paths: list[str] = []
    if not task.working_dir or not os.path.isdir(task.working_dir):
        return paths
    for root, _dirs, names in os.walk(task.working_dir):
        for name in names:
            if name.startswith("."):
                continue
            paths.append(os.path.join(root, name))
    return sorted(paths)


@dataclass
class CuriosityEngine:
    workspace: str
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_workspace(cls, workspace: str) -> "CuriosityEngine":
        return cls(workspace=workspace, config=_load_config(workspace))

    @property
    def max_depth(self) -> int:
        return max(0, int(self.config.get("max_depth") or 0))

    def _gap_report(self, *, task: TaskInstance, user_goal: str, validation: Any | None = None) -> dict[str, Any]:
        if validation is None:
            validation = ArtifactValidator(load_harness_config(self.workspace)).validate(task)
        files = _workspace_files(task)
        min_len = max(0, int(self.config.get("min_artifact_length") or 100))
        short_files = []
        for path in files:
            if path.lower().endswith((".md", ".txt", ".csv", ".json")):
                size = os.path.getsize(path) if os.path.exists(path) else 0
                if size < min_len and not os.path.basename(path).startswith("_"):
                    short_files.append({"path": path, "size": size})
        combined = "\n".join(_safe_read(path, 3000) for path in files if path.lower().endswith((".md", ".txt", ".json")))
        required_fields = [str(x) for x in (self.config.get("required_fields_for_breakthrough") or []) if str(x).strip()]
        needs_breakthrough_support = bool(re.search(r"突破|创新|新方法|novel|breakthrough", user_goal or "", re.I))
        missing_fields = [
            field for field in required_fields
            if needs_breakthrough_support and field.lower() not in combined.lower()
        ]
        min_results = max(0, int(self.config.get("min_search_results") or 3))
        result_counts = [int(x) for x in re.findall(r'"?(?:result_count|rows|count)"?\s*[:=]\s*(\d+)', combined)]
        low_results = bool(result_counts and min(result_counts) < min_results)
        return {
            "missing": getattr(validation, "missing", []) or [],
            "short_files": short_files[:8],
            "low_results": low_results,
            "result_counts": result_counts[:8],
            "missing_breakthrough_fields": missing_fields,
            "existing_files": files[:30],
        }

    def should_explore(self, *, task: TaskInstance, user_goal: str, validation: Any | None = None, depth: int = 0) -> tuple[bool, dict[str, Any]]:
        if not self.config.get("enabled", True) or depth >= self.max_depth:
            return False, {"reason": "disabled_or_depth_limit", "depth": depth}
        report = self._gap_report(task=task, user_goal=user_goal, validation=validation)
        triggered = bool(report["missing"] or report["short_files"] or report["low_results"] or report["missing_breakthrough_fields"])
        report["triggered"] = triggered
        return triggered, report

    async def maybe_explore(
        self,
        *,
        adapter: Any,
        task_instance: TaskInstance,
        user_goal: str,
        registry: EventRegistry,
        validation: Any | None = None,
        depth: int = 0,
        previous_result: Any | None = None,
    ) -> tuple[MicroPlan | None, int]:
        should, report = self.should_explore(task=task_instance, user_goal=user_goal, validation=validation, depth=depth)
        task_instance.append_log("curiosity_gap_check", report)
        if not should:
            logger.info("[CURIOSITY] no exploration needed task_id=%s depth=%s", task_instance.task_id, depth)
            return None, 0
        if not adapter:
            logger.warning("[CURIOSITY] adapter unavailable; cannot generate exploration plan")
            return None, 0
        prompt = f"""你是 Partner 的 CuriosityEngine。你只为当前信息缺口生成 1-3 个 Harness 补充步骤，不做全局重规划。

用户目标：
{user_goal[:1800]}

TaskInstance:
- task_id: {task_instance.task_id}
- working_dir: {task_instance.working_dir}
- expected_artifacts: {json.dumps(task_instance.expected_artifacts, ensure_ascii=False)}

信息缺口报告：
{json.dumps(report, ensure_ascii=False)[:2200]}

可用 Harness event registry：
{registry.describe_for_prompt()}

规则：
- 只填补缺口，不重复已有文件或已有查询。
- 计划长度 1-3。
- 禁止 curiosity_explore。
- 如果已有 fallback/部分草案，优先基于它写出可交付的补充文件或错误边界。
- 输出 JSON 对象：{{"plan":[{{"id":"step_1","event_type":"registry_name","parameters":{{}}, "depends_on":[]}}],"expected_artifacts":[...]}}
- 不要输出解释，只输出 JSON。
"""
        robust = RobustExecutor(load_harness_config(self.workspace))
        result = await robust.execute(
            event_name="curiosity_engine",
            task_instance=task_instance,
            operation=lambda: adapter.chat(prompt, purpose="classify"),
            on_timeout="fail_fast",
            on_failure="fail_fast",
            metadata={"depth": depth, "config_path": self.config.get("_config_path") or ""},
        )
        if not result.ok:
            logger.warning("[CURIOSITY] planner failed task_id=%s: %s", task_instance.task_id, result.error)
            return None, 1
        plan = _normalize_micro_plan(_json_from_llm(str(result.value or "")), max_steps=3)
        existing_names = {os.path.basename(path) for path in report.get("existing_files") or []}
        for index, step in enumerate(plan.plan, start=1):
            params = dict(step.parameters or {})
            name = str(params.get("filename") or params.get("path") or "").strip()
            if name and os.path.basename(name) in existing_names:
                root, ext = os.path.splitext(name)
                params["filename" if "filename" in params else "path"] = f"{root}_curiosity_{depth + 1}_{index}{ext or '.md'}"
                step.parameters = params
        task_instance.append_log("curiosity_plan_created", {
            "depth": depth,
            "steps": [step.__dict__ for step in plan.plan],
            "expected_artifacts": plan.expected_artifacts,
        })
        logger.info("[CURIOSITY] generated %s exploration steps task_id=%s depth=%s", len(plan.plan), task_instance.task_id, depth)
        return plan, 1
