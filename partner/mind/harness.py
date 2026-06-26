"""Micro-planning harness for Partner action events.

The harness turns one user/action goal into a short plan, then executes local
atomic events without asking the LLM again.  Smart events remain available for
cases where a local deterministic step cannot produce the required result.
"""

from __future__ import annotations

import asyncio
import csv
import inspect
import json
import json5 as json5_module
import logging
import os
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

_HAS_JSON5 = True
from ..harness_core import (
    ArtifactValidator,
    RemediationHandler,
    RobustExecutor,
    TaskInstance,
    load_harness_config,
)
from ..skills.summarize_search import summarize_search_results
from ..utils.text_cleaner import clean_user_facing_text
from .event_types import EventType, MindEvent

logger = logging.getLogger(__name__)


JsonDict = dict[str, Any]
EventHandler = Callable[["HarnessContext", JsonDict], JsonDict | Awaitable[JsonDict]]


@dataclass
class HarnessEventSpec:
    name: str
    kind: str
    description: str
    handler: EventHandler
    produces_artifact: bool = False
    reads_existing_artifact: bool = False
    external_call: bool = False
    execution_method: str = "local"  # "agent", "llm", or "local" — config-driven, not hardcoded


@dataclass
class HarnessStep:
    id: str
    event_type: str
    parameters: JsonDict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class MicroPlan:
    plan: list[HarnessStep]
    expected_artifacts: list[JsonDict] = field(default_factory=list)


@dataclass
class HarnessResult:
    ok: bool
    parsed: JsonDict
    plan: list[HarnessStep]
    step_results: dict[str, JsonDict]
    reason: str = ""
    llm_calls: int = 0
    stalled_steps: int = 0


@dataclass
class HarnessContext:
    workspace: str
    event: MindEvent
    title: str
    project_dir: str
    state_md: str
    artifact_path: str
    adapter: Any = None
    build_action_prompt: Callable[[MindEvent, str, str, str], str] | None = None
    parse_structured_response: Callable[[str], JsonDict] | None = None
    task_instance: TaskInstance | None = None
    config: JsonDict = field(default_factory=dict)
    robust_executor: RobustExecutor | None = None
    log_path: str = ""
    progress_callback: Callable[[JsonDict], Any] | None = None

    @property
    def payload(self) -> JsonDict:
        return self.event.payload or {}

    @property
    def user_goal(self) -> str:
        return str(self.payload.get("user_request") or self.payload.get("root_user_request") or self.title or "").strip()

    @property
    def working_dir(self) -> str:
        if self.task_instance:
            return self.task_instance.working_dir
        return self.project_dir


class EventRegistry:
    def __init__(self) -> None:
        self._events: dict[str, HarnessEventSpec] = {}

    def register(self, spec: HarnessEventSpec) -> None:
        if spec.name in self._events:
            raise ValueError(f"duplicate harness event: {spec.name}")
        if spec.kind not in {"atomic", "smart"}:
            raise ValueError(f"invalid harness event kind: {spec.kind}")
        self._events[spec.name] = spec

    def get(self, name: str) -> HarnessEventSpec | None:
        return self._events.get(str(name or "").strip())

    def describe_for_prompt(self) -> str:
        rows = []
        # Exclude fragile events that LLM can't parameterize correctly
        excluded = {"atomic_json_table_artifact"}
        for name in sorted(self._events):
            if name in excluded:
                continue
            spec = self._events[name]
            caps = []
            if spec.produces_artifact:
                caps.append("produces_artifact")
            if spec.reads_existing_artifact:
                caps.append("reads_existing_artifact")
            if spec.external_call:
                caps.append("external_call")
            cap_text = f" capabilities={','.join(caps)}" if caps else ""
            exec_method = f" exec={spec.execution_method}"
            rows.append(f"- {name} ({spec.kind}{cap_text}{exec_method}): {spec.description}")
        return "\n".join(rows)


class StateStore:
    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.state_dir = os.path.join(workspace, "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.log_path = os.path.join(self.state_dir, "harness_runs.jsonl")
        self.snapshot_path = os.path.join(self.state_dir, "harness_state.json")

    def append(self, row: JsonDict) -> None:
        row = {"ts": datetime.now().isoformat(), **row}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def update_snapshot(self, patch: JsonDict) -> None:
        data: JsonDict = {}
        try:
            with open(self.snapshot_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
        data.update({"updated_at": datetime.now().isoformat(), **patch})
        with open(self.snapshot_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def recent_results(self, limit: int = 12) -> list[JsonDict]:
        if not os.path.exists(self.log_path):
            return []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
        except Exception:
            return []
        out = []
        for line in lines:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    out.append(row)
            except Exception:
                continue
        return out


def _validate_plan_against_registry(
    registry: EventRegistry,
    plan: list[HarnessStep],
    context_name: str = "",
) -> list[HarnessStep]:
    """Filter plan steps to only those with event_types registered in the registry.

    Steps with unknown event_types are logged and dropped.  If no steps remain,
    raises ValueError.
    """
    valid: list[HarnessStep] = []
    for step in plan:
        if registry.get(step.event_type):
            valid.append(step)
        else:
            logger.warning(
                "[PLAN] %s dropping step %s with unknown event_type=%s",
                context_name, step.id, step.event_type,
            )
    if not valid:
        raise ValueError(
            f"all steps have unknown event types in {context_name}"
        )
    return valid


def _clip(text: Any, limit: int = 1200) -> str:
    raw = str(text or "").strip()
    return raw if len(raw) <= limit else raw[: max(0, limit - 1)].rstrip() + "…"


def _json_from_llm(raw: str) -> Any:
    text = (raw or "").strip()
    # Strip [ollama] prefix added by OllamaLiteAdapter before JSON parsing
    if text.startswith("[ollama]") or text.startswith("[ollama]\n"):
        text = text.split("\n", 1)[1].strip() if "\n" in text else text[len("[ollama]"):].strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    start_obj = text.find("{")
    start_arr = text.find("[")
    starts = [x for x in (start_obj, start_arr) if x >= 0]
    if not starts:
        raise ValueError("no JSON object/array in planner output")
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    if end <= start:
        raise ValueError("incomplete JSON in planner output")
    json_text = text[start:end + 1]

    # Attempt 1: standard parse
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: fix common LLM JSON errors with regex + retry
    fixed = _repair_json_commas(json_text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Attempt 3: json5 fallback (handles trailing commas, single quotes, unquoted keys)
    if _HAS_JSON5:
        try:
            return json5_module.loads(json_text)
        except Exception:
            pass
        try:
            return json5_module.loads(fixed)
        except Exception:
            pass

    # All attempts exhausted
    raise ValueError(
        f"JSON parse failed after all repair attempts. "
        f"Original error: could not parse JSON content"
    )


def _repair_json_commas(text: str) -> str:
    """Insert missing commas in LLM-produced JSON.

    Handles the most common LLM JSON error: missing comma between two
    adjacent elements in an array or two properties in an object.

    Applies regex fixes iteratively until stable, since one fix may
    reveal another (e.g. [1 2 3] needs two comma insertions).
    """
    fixes = [
        # Missing comma between closing and opening braces in arrays: }{
        (re.compile(r"}(\s*){"), r"},\1{"),
        # Missing comma between number and opening brace: 0{
        (re.compile(r"(\d)(\s*){"), r"\1,\2{"),
        # Missing comma between closing brace and quoted string: }"key"
        (re.compile(r"}(\s*)\""), r'},\1"'),
        # Missing comma between quoted string and opening brace: "key"{
        (re.compile(r'"(\s*){'), r'",\1{'),
        # Missing comma between two number values: 0 1 → 0, 1
        (re.compile(r"(\d)(\s+)(\d)"), r"\1,\2\3"),
        # Missing comma between two string literals: "a" "b"
        (re.compile(r'"(\s+)"'), r'",\1"'),
        # Missing comma between number and quoted string: 1 "key" (needs at least 1 space)
        (re.compile(r'(\d)(\s+)"'), r'\1,\2"'),
    ]
    prev = None
    while text != prev:
        prev = text
        for pattern, replacement in fixes:
            text = pattern.sub(replacement, text)
    return text


def _normalize_micro_plan(raw_plan: Any, max_steps: int = 5) -> MicroPlan:
    items = raw_plan.get("plan") if isinstance(raw_plan, dict) else raw_plan
    if not isinstance(items, list):
        raise ValueError("micro planner output must be a JSON array or {plan: []}")
    plan: list[HarnessStep] = []
    seen: set[str] = set()
    for idx, item in enumerate(items[: max(1, int(max_steps or 5))], start=1):
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "").strip()
        if not event_type:
            continue
        step_id = str(item.get("id") or f"step_{idx}").strip()
        step_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", step_id)[:60] or f"step_{idx}"
        if step_id in seen:
            step_id = f"{step_id}_{idx}"
        seen.add(step_id)
        depends = item.get("depends_on") or []
        if isinstance(depends, str):
            depends = [depends]
        params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
        # Sanitize parameters: fix fullwidth parentheses in prompt/instruction (breaks markdown ![]())
        for key in ("prompt", "instruction"):
            if key in params and isinstance(params[key], str):
                params[key] = params[key].replace("（", "(").replace("）", ")")
        plan.append(HarnessStep(
            id=step_id,
            event_type=event_type,
            parameters=params,
            depends_on=[str(x).strip() for x in depends if str(x).strip()],
        ))
    if not plan:
        raise ValueError("empty micro plan")
    expected = []
    if isinstance(raw_plan, dict) and isinstance(raw_plan.get("expected_artifacts"), list):
        for item in raw_plan.get("expected_artifacts") or []:
            if isinstance(item, dict):
                expected.append(item)
    return MicroPlan(plan=plan, expected_artifacts=expected)


def _merge_expected_artifacts(*groups: list[JsonDict]) -> list[JsonDict]:
    merged: list[JsonDict] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "file").strip().lower()
            pattern = str(item.get("pattern") or item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            if kind == "file" and not pattern:
                continue
            key = (kind, pattern)
            if key in seen:
                continue
            seen.add(key)
            merged.append({
                "type": kind,
                "pattern": pattern,
                "description": description,
                "required": bool(item.get("required", True)),
            })
    return merged


def _expected_file_artifacts(expected: list[JsonDict] | None) -> list[JsonDict]:
    return [
        item for item in (expected or [])
        if isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "file"
    ]


def _artifact_policy(payload: JsonDict) -> str:
    value = str(payload.get("artifact_freshness_policy") or "new").strip().lower()
    if value in {"new", "reuse_allowed", "continue_task"}:
        return value
    return "new"


def _reuse_existing_artifact_allowed(payload: JsonDict) -> bool:
    return bool(payload.get("reuse_existing_artifact")) and _artifact_policy(payload) != "new"


def _plan_has_artifact_producer(registry: EventRegistry, plan: list[HarnessStep]) -> bool:
    for step in plan:
        spec = registry.get(step.event_type)
        if spec and spec.produces_artifact:
            return True
    return False


def _stringify_reference_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _get_fallback_path(step_result: dict) -> str | None:
    """Extract the fallback file path from a step result's metadata.

    Handles multiple metadata key conventions:
    - result["metadata"]["fallback_path"]
    - result["metadata"]["fallbacks"]
    - result["metadata"]["fallback"]
    """
    if not isinstance(step_result, dict):
        return None
    metadata = step_result.get("metadata") or step_result.get("result", {}).get("metadata") or {}
    if not isinstance(metadata, dict):
        return None
    for key in ("fallback_path", "fallback", "fallbacks"):
        val = metadata.get(key)
        if isinstance(val, str) and os.path.exists(val):
            return val
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and os.path.exists(item):
                    return item
    return None


def _resolve_value(value: Any, results: dict[str, JsonDict]) -> Any:
    if isinstance(value, str) and "$" in value:
        exact_ref = re.fullmatch(r"\$([A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_$-]+)*)", value.strip())

        def resolve_path(path: str) -> Any:
            parts = path.split(".")
            if not parts:
                return None
            current: Any = results.get(parts[0], {})
            for part in parts[1:]:
                if part == "$":
                    continue
                if isinstance(current, dict):
                    if part in current:
                        current = current.get(part)
                    elif part == "result":
                        # $step_X.result.content — the "result" key is
                        # transparent; look through it directly.
                        continue
                    elif isinstance(current.get("parsed"), dict) and part in current.get("parsed", {}):
                        current = current.get("parsed", {}).get(part)
                    elif isinstance(current.get("json"), dict) and part in current.get("json", {}):
                        current = current.get("json", {}).get(part)
                    else:
                        return None
                else:
                    return None
            # ── Fallback: if resolved to None/null and step has fallback file, read it ─
            if current is None or (isinstance(current, str) and current.strip().lower() in ("null", "", "none")):
                step_result = results.get(parts[0], {})
                fallback_path = _get_fallback_path(step_result)
                if fallback_path:
                    try:
                        with open(fallback_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        if content and len(content) > 50:
                            return content
                    except Exception:
                        pass
            return current

        if exact_ref:
            resolved = resolve_path(exact_ref.group(1))
            return resolved if resolved is not None else None

        def repl(match: re.Match[str]) -> str:
            resolved = resolve_path(match.group(1))
            if resolved is None:
                return ""
            return _stringify_reference_value(resolved)

        return re.sub(r"\$([A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_$-]+)*)", repl, value)
    if isinstance(value, str) and value.startswith("$"):
        parts = value[1:].split(".")
        if not parts:
            return value
        current: Any = results.get(parts[0], {})
        for part in parts[1:]:
            if part == "$":
                continue
            if isinstance(current, dict):
                if part in current:
                    current = current.get(part)
                elif isinstance(current.get("parsed"), dict) and part in current.get("parsed", {}):
                    current = current.get("parsed", {}).get(part)
                elif isinstance(current.get("json"), dict) and part in current.get("json", {}):
                    current = current.get("json", {}).get(part)
                else:
                    return None
            else:
                return None
        # ── Fallback: read from file if resolved to null ─
        if current is None or (isinstance(current, str) and current.strip().lower() in ("null", "", "none")):
            step_result = results.get(parts[0], {})
            fallback_path = _get_fallback_path(step_result)
            if fallback_path:
                try:
                    with open(fallback_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    if content and len(content) > 50:
                        return content
                except Exception:
                    pass
        return current
    if isinstance(value, list):
        return [_resolve_value(v, results) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_value(v, results) for k, v in value.items()}
    return value


def _resolve_reference(value: Any, results: dict[str, JsonDict]) -> Any:
    if not isinstance(value, str):
        return _resolve_value(value, results)
    ref = value.strip()
    if not ref:
        return ""
    if ref.startswith("$"):
        return _resolve_value(ref, results)
    if ".$." in ref:
        left, right = ref.split(".$.", 1)
        if left in results:
            return _resolve_value(f"${left}.{right}", results)
    first = ref.split(".", 1)[0]
    if first in results:
        return _resolve_value(f"${ref}", results)
    return value


def _normalize_step_params(event_type: str, params: JsonDict, results: dict[str, JsonDict]) -> JsonDict:
    if not isinstance(params, dict):
        return params
    out = dict(params)
    if event_type == "atomic_write_artifact" and not any(str(out.get(k) or "").strip() for k in ("content", "content_template")):
        for key in ("content_from", "content_source", "source_content", "artifact_content_from", "text_from"):
            if key in out:
                resolved = _resolve_reference(out.get(key), results)
                if isinstance(resolved, dict):
                    for inner_key in ("artifact_content", "content", "literature_review_md", "review_markdown", "markdown", "text", "step_done"):
                        if str(resolved.get(inner_key) or "").strip():
                            resolved = resolved.get(inner_key)
                            break
                if resolved is not None and str(resolved).strip():
                    out["content"] = resolved if isinstance(resolved, str) else json.dumps(resolved, ensure_ascii=False, indent=2)
                    break
    if event_type == "atomic_json_table_artifact" and not any(k in out for k in ("data", "json")):
        for key in ("data_from", "json_from", "rows_from", "source_data", "input_from"):
            if key in out:
                resolved = _resolve_reference(out.get(key), results)
                if resolved is not None:
                    out["data"] = resolved
                    break
        # Auto-inject: if still no data, find JSON from dependency results
        if "data" not in out and "json" not in out:
            for dep_id, dep_result in results.items():
                content = str(dep_result.get("content") or "")
                if not content or not content.strip():
                    content = str(dep_result.get("artifact_content") or "")
                if content and content.strip():
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, (dict, list)):
                            out["data"] = parsed
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Fallback: check if dependency step wrote JSON files
                if "data" not in out and "json" not in out:
                    parsed = dep_result.get("parsed") or {}
                    files_str = str(parsed.get("files") or dep_result.get("files") or "").strip()
                    if files_str:
                        for maybe_path in re.split(r"[\n;]", files_str):
                            maybe_path = maybe_path.strip().lstrip("- ").strip()
                            if maybe_path.endswith(".json") and os.path.exists(maybe_path):
                                try:
                                    with open(maybe_path, "r", encoding="utf-8") as fd:
                                        file_data = json.load(fd)
                                    if isinstance(file_data, dict):
                                        # Try to find an array field
                                        for val in file_data.values():
                                            if isinstance(val, list) and len(val) > 0:
                                                out["data"] = file_data
                                                break
                                        if "data" in out:
                                            break
                                    elif isinstance(file_data, list):
                                        out["data"] = file_data
                                        break
                                except Exception:
                                    pass
    return out


def _progress_score(result: JsonDict) -> int:
    if not result or result.get("ok") is False:
        return 0
    score = 0
    for key in ("content", "path", "files", "findings", "artifact_content", "status"):
        value = result.get(key)
        if value:
            score += 1
    return score


def _dependency_content(results: dict[str, JsonDict], depends_on: list[str]) -> str:
    for dep in depends_on or []:
        result = results.get(dep) or {}
        # Prefer parsed.artifact_content (clean structured output) over raw LLM content
        parsed = result.get("parsed")
        if isinstance(parsed, dict):
            for key in ("artifact_content", "content", "step_done"):
                value = str(parsed.get(key) or "").strip()
                if value and value.upper() != "EMPTY":
                    return value
        for key in ("content", "artifact_content"):
            value = str(result.get(key) or "").strip()
            if value and value.upper() != "EMPTY":
                return value
    return ""


async def _maybe_await(value: JsonDict | Awaitable[JsonDict]) -> JsonDict:
    if inspect.isawaitable(value):
        return await value
    return value


class MicroPlanner:
    def __init__(self, registry: EventRegistry) -> None:
        self.registry = registry

    async def plan(self, ctx: HarnessContext, history: list[JsonDict]) -> tuple[MicroPlan, int]:
        if not ctx.adapter:
            return self._fallback_plan(ctx), 0
        task_id = ctx.task_instance.task_id if ctx.task_instance else ""
        expected_artifacts = ctx.task_instance.expected_artifacts if ctx.task_instance else []
        delivery_required = bool(ctx.payload.get("delivery_required") or expected_artifacts)
        artifact_freshness_policy = _artifact_policy(ctx.payload)
        reuse_existing_artifact = _reuse_existing_artifact_allowed(ctx.payload)
        continue_note = (
            f"本任务显式继续历史项目：{ctx.task_instance.continue_from_project}"
            if ctx.task_instance and ctx.task_instance.continue_from_project
            else "本任务不是显式继续历史项目；历史项目状态只能作为知识参考，不能作为完成证据。"
        )
        prompt = f"""你是 Partner Harness 的 Micro Planner。你只规划接下来 2-5 个小事件，不执行任务。

目标：用尽量少的 SmartEvent，把本轮目标拆成可本地执行的 AtomicEvent。只有确实需要语言理解、生成、判断时才用 SmartEvent。

可用事件注册表：
{self.registry.describe_for_prompt()}

当前 event：{ctx.event.type.value}
任务/项目：{ctx.title}
当前 TaskInstance：
- task_id: {task_id}
- working_dir: {ctx.working_dir}
- expected_artifacts 当前值: {json.dumps(expected_artifacts, ensure_ascii=False)}
- delivery_required: {json.dumps(delivery_required, ensure_ascii=False)}
- artifact_freshness_policy: {artifact_freshness_policy}
- reuse_existing_artifact: {json.dumps(reuse_existing_artifact, ensure_ascii=False)}
- reuse_reason: {str(ctx.payload.get("reuse_reason") or "")[:500]}
- {continue_note}

用户目标：
{ctx.user_goal[:1600]}

当前状态摘要：
{ctx.state_md[:1800] if ctx.state_md else "EMPTY"}

最近 Harness 结果：
{json.dumps(history[-6:], ensure_ascii=False)[:1800]}

输出 JSON 对象：
{{
  "plan": [
    {{"id":"step_1","event_type":"registry_name","parameters":{{}}, "depends_on":[]}}
  ],
  "expected_artifacts": [
    {{"type":"file","pattern":"*.md","description":"最终报告或错误说明","required":true}}
  ]
}}

规则：
- 优先使用 atomic_read_state、atomic_list_project_files、atomic_inspect_file、atomic_ollama_status、atomic_compose_structured_result、atomic_write_artifact。
- 如果任务只需要获取外部数据（天气、汇率、股价等），优先使用 atomic_http_get 而不是 call_agent_skill。
- 如果输入/依赖结果是 JSON 且 expected_artifacts 要求结构化表格文件，使用 smart_llm_structured_action 处理数据后用 atomic_write_artifact 写入文件；不要用 SmartEvent 做 JSON→表格转换。
- 如果 expected_artifacts 指定了文件扩展名或多个可选扩展名（例如 *.csv, *.xlsx），生成文件的 filename/format 必须满足这些扩展名；不要用 Markdown 文件替代 CSV/XLS/XLSX 等目标格式。
- 如果用户目标包含明确的数量/时段/范围要求，表格生成步骤必须用 min_rows 或其他本地参数声明最低完整性要求；外部数据源本身也必须能覆盖该范围。
- 如果最近 Harness 结果里出现 table completeness check failed（rows < min_rows），不要重复同一个数据源/同一 rows_path 计划；必须改用能覆盖范围的数据源，或写入目标格式的明确失败/部分结果说明，不能声称已完成。
- 不要为读取文件、列目录、HTTP GET、写文件、拼装结果调用 LLM。
- atomic_http_get 的 url 参数必须包含 https:// 协议头。wttr.in 不支持 num_of_days 参数；如需指定天数，使用 Open-Meteo API（api.open-meteo.com）的 forecast_days=N 参数。
- 如果目标要求生成自然语言内容、复杂判断或下一步决策，才使用 smart_llm_structured_action。
- 如果没有足够信息，先用 AtomicEvent 收集状态，再用一个 SmartEvent。
- 当前 event 如果是 project_think，只能做目标拆解/状态读取/下一步选择；禁止规划 atomic_http_get、atomic_write_artifact、web/search/data fetch 等执行型步骤。
- 规划阶段必须声明本轮 expected_artifacts；如果入口已有 expected_artifacts，不得弱化或删除。
- 不要根据任何历史文件的存在与否判定任务已完成，除非本 TaskInstance 显式 continue，且历史文件正好符合本次用户要求的格式和时效性。
- 如果 artifact_freshness_policy=new 或 reuse_existing_artifact=false，文件型 expected_artifacts 必须在当前 working_dir 内由 produces_artifact 的事件生成；不能规划"只列目录/只检查旧文件/再让 SmartEvent 判断是否存在"来满足交付。
- 如果确实要复用已有文件，必须看到 reuse_existing_artifact=true，并规划 reads_existing_artifact 后再复制/写入当前 working_dir。
- 所有文件写入路径必须相对当前 working_dir，不写到旧项目目录。
- 引用前一步骤的输出使用 $step_id.field 语法（例如 $fetch.content 或 $fetch.json），不要使用 {{ }} 模板语法。
- 【重要】atomic_write_artifact 的 content 参数禁止出现以下模式（会被判定为占位内容而拒绝写入）：
  - ...（省略号）
  - [完整...]、[详细...]、[content]、[placeholder] 等方括号占位标记
  - 内容长度不足 100 字符（视为占位）
  - 这是 LLM 常见的省略行为，但必须杜绝。
- 【重要】对于需要生成自然语言内容（报告、分析、总结、文档）或脚本代码（Python、shell、R）的任务，分两步执行：
  1. 先用 smart_llm_structured_action 生成完整内容（LLM 输出全文，不是骨架）
  2. 再用 atomic_write_artifact 以 content="$step_X.result.content" 写入文件
- atomic_write_artifact 的 content 参数应该引用前一步骤的输出（$step_X.result.content），而不是内联写入内容。内联内容仅在写入极短的配置值、路径等时可以使用。
- 错误示例（会被拒绝）：
  {"path":"report.md","content":"# 欧拉公式报告\n\n## 概述\n欧拉公式 ... [完整报告内容]"}  ← 占位内容
  {"path":"plot.py","content":"import numpy as np\nimport matplotlib.pyplot as plt\n..."}  ← 截断代码
- 正确示例：
  {"path":"report.md","content":"$step_2.result.content"}  ← 引用生成步骤的输出
- 【重要】用户要求"绘图"时（如 matplotlib、plot、图表、可视化），必须用 smart_llm_structured_action 生成图像。正确流程：smart_llm_structured_action（生成并执行 Python 绘图代码，保存为 .png）→ smart_llm_structured_action（生成 Markdown 报告，用 ![](file.png) 引用）→ atomic_write_artifact（写入 .md）→ atomic_convert_md_to_pdf（转 PDF）
- 不要输出解释，只输出 JSON 对象。
"""
        if ctx.robust_executor and ctx.task_instance:
            robust = await ctx.robust_executor.execute(
                event_name="micro_planner",
                task_instance=ctx.task_instance,
                operation=lambda: ctx.adapter.chat(prompt, purpose="classify"),
                on_timeout="fail_fast",
                on_failure="fail_fast",
            )
            raw = robust.value if robust.ok else ""
        else:
            raw = await asyncio.to_thread(ctx.adapter.chat, prompt, purpose="classify")
        micro_plan = _normalize_micro_plan(_json_from_llm(raw or ""))
        micro_plan = self._sanitize_for_event(ctx, micro_plan)
        self._validate_artifact_plan(ctx, micro_plan)
        return micro_plan, 1

    def _fallback_plan(self, ctx: HarnessContext) -> MicroPlan:
        return MicroPlan(
            plan=[
                HarnessStep("read_state", "atomic_read_state", {"title": ctx.title}, []),
                HarnessStep("list_files", "atomic_list_project_files", {"limit": 20}, []),
                HarnessStep(
                    "llm_action",
                    "smart_llm_structured_action",
                    {"state": "$read_state.content", "files": "$list_files.files"},
                    ["read_state", "list_files"],
                ),
            ],
            expected_artifacts=[{"type": "message", "pattern": "text", "description": "最终回复", "required": True}],
        )

    def _sanitize_for_event(self, ctx: HarnessContext, micro_plan: MicroPlan) -> MicroPlan:
        if ctx.event.type != EventType.PROJECT_THINK:
            return micro_plan
        forbidden = {
            "atomic_http_get",
            "atomic_write_artifact",
            "atomic_json_table_artifact",
            "smart_llm_structured_action",
        }
        if not any(step.event_type in forbidden for step in micro_plan.plan):
            return micro_plan
        if ctx.task_instance:
            ctx.task_instance.append_log("micro_plan_sanitized", {
                "reason": "project_think_cannot_execute_deliverable_steps",
                "original_plan": [step.__dict__ for step in micro_plan.plan],
            })
        return MicroPlan(
            plan=[
                HarnessStep("read_state", "atomic_read_state", {}, []),
                HarnessStep("list_files", "atomic_list_project_files", {"limit": 20}, []),
                HarnessStep(
                    "compose",
                    "atomic_compose_structured_result",
                    {
                        "step_done": "已完成目标拆解边界检查",
                        "findings": ["project_think 只负责拆解和选择下一步，不执行取数或生成交付物"],
                        "next_action": "根据根目标和 event capability metadata 选择下一个最小执行 event；资料依据整理走可检索/综述能力，数据获取走数据能力，最终产物由具备交付能力的 event 生成。",
                        "state_delta": "project_think sanitized executable micro-plan",
                    },
                    ["read_state", "list_files"],
                ),
            ],
            expected_artifacts=[{"type": "message", "pattern": "text", "description": "项目思考结果", "required": True}],
        )

    def _validate_artifact_plan(self, ctx: HarnessContext, micro_plan: MicroPlan) -> None:
        if ctx.event.type in {EventType.PROJECT_THINK, EventType.OBJECTIVE_REVIEW, EventType.HABIT_UPDATE, EventType.CURIOSITY_EXPLORE}:
            return
        expected = _merge_expected_artifacts(
            ctx.task_instance.expected_artifacts if ctx.task_instance else [],
            micro_plan.expected_artifacts,
        )
        if not _expected_file_artifacts(expected):
            return
        if _reuse_existing_artifact_allowed(ctx.payload):
            return
        if _plan_has_artifact_producer(self.registry, micro_plan.plan):
            return
        detail = {
            "reason": "file_artifact_requires_producer_event",
            "artifact_freshness_policy": _artifact_policy(ctx.payload),
            "reuse_existing_artifact": bool(ctx.payload.get("reuse_existing_artifact")),
            "expected_artifacts": expected,
            "plan": [step.__dict__ for step in micro_plan.plan],
        }
        if ctx.task_instance:
            ctx.task_instance.append_log("micro_plan_rejected", detail)
        raise ValueError("micro plan missing artifact-producing event for expected file artifacts")


class PlanExecutor:
    def __init__(self, registry: EventRegistry, store: StateStore) -> None:
        self.registry = registry
        self.store = store

    async def execute(self, ctx: HarnessContext, plan: list[HarnessStep]) -> tuple[dict[str, JsonDict], int, int]:
        pending = {step.id: step for step in plan}
        total_steps = len(plan)
        step_ordinals = {step.id: idx for idx, step in enumerate(plan, start=1)}
        results: dict[str, JsonDict] = {}
        llm_calls = 0
        stalled = 0
        while pending:
            ready = [
                step for step in pending.values()
                if all(dep in results for dep in step.depends_on)
            ]
            if not ready:
                raise RuntimeError("plan has unresolved or cyclic dependencies")
            if len(ready) > 1:
                logger.info(
                    "[HARNESS_PARALLEL] running %s ready steps for title=%s: %s",
                    len(ready),
                    ctx.title,
                    [step.id for step in ready],
                )
                if ctx.task_instance:
                    ctx.task_instance.append_log("harness_parallel_batch", {
                        "title": ctx.title,
                        "step_ids": [step.id for step in ready],
                        "event_types": [step.event_type for step in ready],
                    })
                await self._emit_progress(ctx, {
                    "phase": "parallel_start",
                    "step_ids": [step.id for step in ready],
                    "event_types": [step.event_type for step in ready],
                    "descriptions": [
                        {
                            "id": step.id,
                            "event_type": step.event_type,
                            "description": _step_description(step),
                            "depends_on": step.depends_on,
                        }
                        for step in ready
                    ],
                    "total_steps": total_steps,
                })
            batch = await asyncio.gather(*(self._run_step(ctx, step, results, step_ordinals.get(step.id, 0), total_steps) for step in ready))
            for step, result, kind in batch:
                results[step.id] = result
                pending.pop(step.id, None)
                llm_calls += 1 if kind == "smart" else 0
                stalled = stalled + 1 if _progress_score(result) == 0 else 0
                self.store.append({
                    "event": "harness_step",
                    "title": ctx.title,
                    "step_id": step.id,
                    "event_type": step.event_type,
                    "kind": kind,
                    "ok": bool(result.get("ok", True)),
                    "progress_score": _progress_score(result),
                    "result_preview": _clip(result, 500),
                })
        return results, llm_calls, stalled

    async def _emit_progress(self, ctx: HarnessContext, payload: JsonDict) -> None:
        if not ctx.progress_callback:
            return
        try:
            value = ctx.progress_callback(payload)
            if inspect.isawaitable(value):
                await value
        except Exception as exc:
            logger.debug("[PLAN_EXECUTOR] progress callback failed: %s", exc)

    async def _run_step(
        self,
        ctx: HarnessContext,
        step: HarnessStep,
        results: dict[str, JsonDict],
        ordinal: int,
        total_steps: int,
    ) -> tuple[HarnessStep, JsonDict, str]:
        spec = self.registry.get(step.event_type)
        if not spec:
            raise RuntimeError(f"unregistered harness event: {step.event_type}")
        description = _step_description(step)
        logger.info(
            "[STEP %s/%s] 正在执行: %s (%s, 依赖: %s)",
            ordinal or "?",
            total_steps,
            description,
            step.event_type,
            step.depends_on or [],
        )
        await self._emit_progress(ctx, {
            "phase": "step_start",
            "step_id": step.id,
            "ordinal": ordinal,
            "total_steps": total_steps,
            "event_type": step.event_type,
            "description": description,
            "depends_on": step.depends_on,
        })
        start_ts = time.time()
        if ctx.task_instance:
            ctx.task_instance.append_log("plan_executor_step_started", {
                "step_id": step.id,
                "event_type": step.event_type,
                "depends_on": step.depends_on,
                "description": description,
                "ordinal": ordinal,
                "total_steps": total_steps,
            })
        failed_deps = [
            dep for dep in step.depends_on
            if (results.get(dep) or {}).get("ok") is False
        ]
        if failed_deps:
            # If ALL deps failed, skip this step entirely
            if len(failed_deps) == len(step.depends_on):
                logger.warning(
                    "[STEP %s/%s] %s skipped: all dependencies failed (%s)",
                    ordinal or "?", total_steps, step.id, ", ".join(failed_deps),
                )
                result = {"ok": False, "error": f"skipped: all dependencies failed ({', '.join(failed_deps)})"}
                # Emit step_complete for skipped steps so the pipeline shows them
                await self._emit_progress(ctx, {
                    "phase": "step_complete",
                    "step_id": step.id,
                    "ordinal": ordinal,
                    "total_steps": total_steps,
                    "event_type": step.event_type,
                    "description": _step_description(step),
                    "ok": False,
                    "elapsed_sec": 0,
                    "files": [],
                    "summary": "skipped: dependencies failed",
                })
                return step, result, "skipped"
            logger.warning(
                "[STEP %s/%s] %s has failed deps (%s); executing with degraded data",
                ordinal or "?", total_steps, step.id, ", ".join(failed_deps),
            )
            # Tag partial dependencies in parameters so handlers can react
            step.parameters = dict(step.parameters or {})
            step.parameters["_partial_deps"] = failed_deps
            # Build _available_data dict: only successful deps' results
            available = {}
            for dep in step.depends_on:
                if dep not in failed_deps:
                    dep_result = results.get(dep) or {}
                    available[dep] = {
                        "ok": dep_result.get("ok", False),
                        "content": dep_result.get("content", ""),
                        "json": dep_result.get("json"),
                        "parsed": dep_result.get("parsed"),
                        "files": dep_result.get("files", []),
                    }
            if available:
                step.parameters["_available_data"] = available
            if ctx.task_instance:
                ctx.task_instance.append_log("plan_executor_partial_deps", {
                    "step_id": step.id,
                    "failed_dependencies": failed_deps,
                    "available_dependencies": list(available.keys()),
                })
            # Fall through — _resolve_value will return None for missing content,
            # and _dependency_content fallback (below) will still collect available data.
        params = _normalize_step_params(step.event_type, _resolve_value(step.parameters, results), results)
        if step.event_type in ("atomic_convert_md_to_pdf", "atomic_write_artifact") and isinstance(params, dict):
            if step.event_type == "atomic_convert_md_to_pdf":
                source = str(params.get("source") or params.get("path") or "").strip()
                if not source:
                    for dep in step.depends_on or []:
                        dep_result = (results.get(dep) or {})
                        dep_parsed = dep_result.get("parsed") if isinstance(dep_result.get("parsed"), dict) else {}
                        for key in ("files", "path"):
                            dep_files = dep_parsed.get(key) or dep_result.get(key) or []
                            if isinstance(dep_files, str):
                                dep_files = [dep_files]
                            for f in dep_files:
                                if isinstance(f, str) and f.lower().endswith(".md") and os.path.exists(f):
                                    params = dict(params)
                                    params["path"] = f
                                    params["source"] = f
                                    if ctx.task_instance:
                                        ctx.task_instance.append_log("pdf_source_filled_from_dependency", {
                                            "step_id": step.id,
                                            "depends_on": step.depends_on,
                                            "source": f,
                                        })
                                    break
                            if params.get("source"):
                                break
                if not params.get("source"):
                    # Fallback: scan _step_*.result.json files on disk
                    _work_dir = getattr(ctx.task_instance, "working_dir", "") if ctx.task_instance else ""
                    if _work_dir and os.path.isdir(_work_dir):
                        _step_files = sorted([f for f in os.listdir(_work_dir) if f.startswith("_step_") and f.endswith(".result.json")], reverse=True)
                        for _sf in _step_files:
                            try:
                                with open(os.path.join(_work_dir, _sf), "r") as _sf_f:
                                    _sf_data = json.load(_sf_f)
                                _result = _sf_data.get("result") or {}
                                _dep_files = _result.get("files") or []
                                if isinstance(_dep_files, str):
                                    _dep_files = [_dep_files]
                                _dep_path = _result.get("path") or []
                                if isinstance(_dep_path, str):
                                    _dep_path = [_dep_path]
                                _all_files = list(_dep_files) + [p for p in _dep_path if p not in _dep_files]
                                for _df in _all_files:
                                    if isinstance(_df, str) and _df.lower().endswith(".md") and os.path.exists(_df):
                                        params = dict(params)
                                        params["path"] = _df
                                        params["source"] = _df
                                        if ctx.task_instance:
                                            ctx.task_instance.append_log("pdf_source_filled_from_disk", {
                                                "step_id": step.id,
                                                "source": _df,
                                                "found_in": _sf,
                                            })
                                        break
                                if params.get("source"):
                                    break
                            except Exception:
                                continue
            else:
                content = str(params.get("content") or params.get("message") or params.get("artifact_content") or "").strip()
                if not content or content.upper() == "EMPTY":
                    fallback_content = _dependency_content(results, step.depends_on)
                    if fallback_content:
                        params = dict(params)
                        params["content"] = fallback_content
                        if ctx.task_instance:
                            ctx.task_instance.append_log("artifact_content_filled_from_dependency", {
                                "step_id": step.id,
                                "depends_on": step.depends_on,
                                "content_length": len(fallback_content),
                            })
        try:
            # --- Retry logic for transient failures ---
            MAX_STEP_RETRIES = 2
            attempt = 0
            last_exc = None
            result = None
            while attempt <= MAX_STEP_RETRIES:
                try:
                    result = await _maybe_await(spec.handler(ctx, params))
                    if isinstance(result, dict) and result.get("ok") is False and attempt < MAX_STEP_RETRIES:
                        # Check if the error is retryable
                        error_str = str(result.get("error", ""))
                        retryable_signals = ["timeout", "time out", "connection", "refused",
                                             "temporarily", "rate limit", "too many",
                                             "resource temporarily", "try again", "503", "502"]
                        if any(s in error_str.lower() for s in retryable_signals):
                            attempt += 1
                            wait = 2 ** attempt
                            logger.info("[RETRY step %s/%s] attempt %d failed (%s), retrying in %ds",
                                        ordinal or "?", total_steps, attempt, error_str, wait)
                            await asyncio.sleep(wait)
                            continue
                    last_exc = None
                    break
                except (ConnectionError, TimeoutError, asyncio.TimeoutError) as exc:
                    last_exc = str(exc)
                    if attempt < MAX_STEP_RETRIES:
                        attempt += 1
                        wait = 2 ** attempt
                        logger.info("[RETRY step %s/%s] exception on attempt %d (%s), retrying in %ds",
                                    ordinal or "?", total_steps, attempt, last_exc, wait)
                        await asyncio.sleep(wait)
                        continue
                    break
                except Exception as exc:
                    last_exc = str(exc)
                    break
            # --- End retry logic ---
        except Exception as exc:
            logger.warning("[HARNESS] step failed %s/%s: %s", step.id, step.event_type, exc)
            result = {"ok": False, "error": str(exc)}
        if not isinstance(result, dict):
            result = {"ok": True, "content": result}
        if result.get("status") == "fallback_success":
            content = str(result.get("content") or result.get("artifact_content") or "")
            result["ok"] = bool(content.strip())
            result["is_fallback"] = True
            if ctx.task_instance:
                ctx.task_instance.append_log("plan_executor_fallback_success", {
                    "step_id": step.id,
                    "event_type": step.event_type,
                    "fallback_path": result.get("fallback_path") or "",
                    "content_length": len(content),
                })
        result.setdefault("ok", True)
        result.setdefault("event_type", step.event_type)
        result_path = _write_step_result_json(ctx, step, result, ordinal, total_steps, description)
        if result_path:
            result.setdefault("result_json_path", result_path)
        produced_files: list[str] = []
        raw_files = result.get("files") or []
        if isinstance(raw_files, str):
            raw_files = [raw_files]
        if isinstance(raw_files, list):
            produced_files.extend(str(path) for path in raw_files if str(path).strip())
        path = str(result.get("path") or "").strip()
        if path and path not in produced_files:
            produced_files.append(path)
        elapsed = time.time() - start_ts
        summary = _step_result_summary(result)
        logger.info("[STEP %s/%s] 完成: %s 耗时 %.1fs 产出: %s", ordinal or "?", total_steps, step.id, elapsed, produced_files)
        if ctx.task_instance:
            ctx.task_instance.append_log("plan_executor_step_completed", {
                "step_id": step.id,
                "event_type": step.event_type,
                "ok": bool(result.get("ok", True)),
                "files": produced_files,
                "progress_score": _progress_score(result),
                "description": description,
                "ordinal": ordinal,
                "total_steps": total_steps,
                "elapsed_sec": elapsed,
                "summary": summary,
            })
        await self._emit_progress(ctx, {
            "phase": "step_complete",
            "step_id": step.id,
            "ordinal": ordinal,
            "total_steps": total_steps,
            "event_type": step.event_type,
            "description": description,
            "ok": bool(result.get("ok", True)),
            "elapsed_sec": elapsed,
            "files": produced_files,
            "summary": summary,
        })
        return step, result, spec.kind


def _write_step_result_json(
    ctx: HarnessContext,
    step: HarnessStep,
    result: JsonDict,
    ordinal: int,
    total_steps: int,
    description: str,
) -> str:
    task = ctx.task_instance
    if not task or not getattr(task, "working_dir", ""):
        return ""
    try:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(step.id or "step")).strip("._-") or "step"
        path = os.path.join(task.working_dir, f"_step_{safe_id}.result.json")
        payload = {
            "step_id": step.id,
            "event_type": step.event_type,
            "depends_on": step.depends_on,
            "ordinal": ordinal,
            "total_steps": total_steps,
            "description": description,
            "ok": bool(result.get("ok", True)),
            "result": result,
        }
        os.makedirs(task.working_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        task.append_log("step_result_json_written", {
            "step_id": step.id,
            "path": path,
            "ok": bool(result.get("ok", True)),
        })
        return path
    except Exception as exc:
        logger.debug("[PLAN_EXECUTOR] failed to write step result JSON for %s: %s", step.id, exc)
        return ""


def _step_description(step: HarnessStep) -> str:
    params = step.parameters if isinstance(step.parameters, dict) else {}
    skill = str(params.get("skill") or params.get("skill_name") or "").strip()
    agent_name = str(params.get("agent") or "").strip()
    inputs = params.get("inputs") if isinstance(params.get("inputs"), dict) else {}
    if step.event_type == "atomic_ensure_agent_installed":
        desc = f"检查 Agent: {agent_name}" if agent_name else "检查 Agent"
    elif step.event_type in {"atomic_execute_skill", "call_agent_skill"}:
        if step.event_type == "call_agent_skill" and agent_name:
            desc = f"调用 Agent: {agent_name}"
        elif skill:
            query = str(inputs.get("query") or inputs.get("keywords") or inputs.get("topic") or "").strip()
            if query:
                desc = f"调用 {skill} 检索/执行：{_clip(query, 80)}"
            else:
                desc = f"调用 {skill}"
        else:
            desc = step.event_type
    elif step.event_type == "smart_llm_structured_action":
        instruction = str(params.get("prompt") or params.get("instruction") or params.get("task") or params.get("objective") or "").strip()
        desc = _clip(instruction, 90) if instruction else "执行 LLM 任务步骤"
    elif step.event_type == "atomic_write_artifact":
        filename = str(params.get("filename") or params.get("path") or params.get("output_file") or "").strip()
        desc = f"写入文件 {filename or 'artifact'}"
    elif step.event_type == "atomic_json_table_artifact":
        filename = str(params.get("filename") or params.get("output_file") or "").strip()
        desc = f"生成表格文件 {filename or 'table artifact'}"
    elif step.event_type == "atomic_read_state":
        desc = "读取当前任务状态"
    elif step.event_type == "atomic_list_project_files":
        desc = "列出当前任务文件"
    else:
        if agent_name and "agent" in step.event_type:
            desc = f"{step.event_type} (Agent: {agent_name})"
        else:
            desc = step.event_type
    if os.environ.get("PARTNER_PROVIDER", "").lower().find("ollama") != -1:
        desc += " [本地模型]"
    return desc


def _step_result_summary(result: JsonDict) -> str:
    if not isinstance(result, dict):
        return ""
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    content_preview = str(result.get("content") or result.get("artifact_content") or "")
    if (
        result.get("is_fallback")
        or metadata.get("fallback_path")
        or "Fallback Draft" in content_preview
        or "Partial Fallback Artifact" in content_preview
    ):
        reason = str(result.get("error") or metadata.get("original_error") or "").strip()
        if reason:
            return f"外部调用未拿到真实结果，已生成 fallback 占位；原因：{_clip(reason, 90)}"
        return "外部调用未拿到真实结果，已生成 fallback 占位；不会当作真实检索结果"
    if result.get("error"):
        return _clip(result.get("error"), 120)
    json_obj = result.get("json")
    if isinstance(json_obj, dict):
        if "count" in json_obj:
            sources = ", ".join(str(x) for x in json_obj.get("sources") or [] if str(x).strip())
            return f"得到 {json_obj.get('count')} 条结果" + (f"；来源：{sources}" if sources else "")
    if result.get("path"):
        return f"生成文件 {os.path.basename(str(result.get('path')))}"
    if result.get("files"):
        files = result.get("files")
        if isinstance(files, list):
            return "生成文件 " + ", ".join(os.path.basename(str(x)) for x in files[:3])
        return f"生成文件 {os.path.basename(str(files))}"
    if result.get("summary"):
        return str(result["summary"])[:120]
    return _clip(result.get("content") or result.get("artifact_content") or "", 120)


def _latest_structured_result(results: dict[str, JsonDict]) -> JsonDict:
    for result in reversed(list(results.values())):
        parsed = result.get("parsed")
        if isinstance(parsed, dict) and parsed:
            return parsed
    return {}


def _merge_result_files(parsed: JsonDict, results: dict[str, JsonDict], validation: Any = None) -> JsonDict:
    merged = dict(parsed or {})
    files: list[str] = []
    raw_files = str(merged.get("files") or "").strip()
    if raw_files and raw_files.upper() != "EMPTY":
        files.extend(x.strip() for x in re.split(r"[;\n，,]+", raw_files) if x.strip())
    for result in results.values():
        value = result.get("files") or []
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            files.extend(str(path).strip() for path in value if str(path).strip())
        path = str(result.get("path") or "").strip()
        if path:
            files.append(path)
    if validation is not None:
        for found in getattr(validation, "found", []) or []:
            for path in found.get("paths") or []:
                files.append(str(path).strip())
    deduped: list[str] = []
    for path in files:
        if path and path.upper() != "EMPTY" and path not in deduped:
            deduped.append(path)
    if deduped:
        merged["files"] = "; ".join(deduped[:8])
        merged.setdefault("evidence", merged["files"])
    return merged


async def run_harness(
    *,
    workspace: str,
    event: MindEvent,
    title: str,
    project_dir: str,
    state_md: str,
    artifact_path: str,
    adapter: Any,
    build_action_prompt: Callable[[MindEvent, str, str, str], str],
    parse_structured_response: Callable[[str], JsonDict],
    max_replans: int = 1,
) -> HarnessResult:
    registry = default_registry()
    store = StateStore(workspace)
    config = load_harness_config(workspace)
    payload = event.payload or {}
    task = TaskInstance.load_or_create(
        workspace,
        task_id=str(payload.get("task_id") or ""),
        user_message=str(payload.get("root_user_request") or payload.get("original_user_request") or payload.get("parent_user_request") or payload.get("user_request") or title or ""),
        continue_from_project=str(payload.get("continue_from_project") or ""),
        metadata={
            "title": title,
            "event_type": event.type.value,
            "source": "harness",
            "current_event_instruction": str(payload.get("user_request") or "")[:1800],
            "previous_next_action": str(payload.get("previous_next_action") or "")[:1000],
        },
    )
    payload["task_id"] = task.task_id
    payload["task_working_dir"] = task.working_dir
    payload["continue_from_project"] = task.continue_from_project
    payload["artifact_freshness_policy"] = _artifact_policy(payload)
    payload["reuse_existing_artifact"] = _reuse_existing_artifact_allowed(payload)
    task.append_log("harness_context_selected", {
        "title": title,
        "legacy_project_dir": project_dir,
        "legacy_state_used_as_completion_evidence": False,
        "continue_from_project": task.continue_from_project,
        "artifact_freshness_policy": payload["artifact_freshness_policy"],
        "reuse_existing_artifact": payload["reuse_existing_artifact"],
        "reuse_reason": str(payload.get("reuse_reason") or "")[:500],
    })
    selector_expected = payload.get("expected_artifacts") if isinstance(payload.get("expected_artifacts"), list) else []
    if event.type in {EventType.PROJECT_THINK, EventType.OBJECTIVE_REVIEW, EventType.HABIT_UPDATE, EventType.CURIOSITY_EXPLORE}:
        root_expected = payload.get("root_expected_artifacts") if isinstance(payload.get("root_expected_artifacts"), list) else []
        if root_expected:
            task.append_log("planning_event_root_expected_artifacts_preserved", {
                "root_expected_artifacts": root_expected,
                "current_expected_artifacts": selector_expected,
            })
        selector_expected = [
            item for item in selector_expected
            if isinstance(item, dict) and str(item.get("type") or "").strip().lower() != "file"
        ] or [{"type": "message", "pattern": "text", "description": "下一步 event 计划", "required": True}]
    if selector_expected:
        task.update_expected_artifacts(_merge_expected_artifacts(task.expected_artifacts, selector_expected))
    effective_state_md = state_md if task.continue_from_project else ""
    task_artifact_path = os.path.join(task.working_dir, os.path.basename(artifact_path or f"{event.type.value}_result.md"))
    robust_executor = RobustExecutor(config)
    ctx = HarnessContext(
        workspace=workspace,
        event=event,
        title=title,
        project_dir=project_dir,
        state_md=effective_state_md,
        artifact_path=task_artifact_path,
        adapter=adapter,
        build_action_prompt=build_action_prompt,
        parse_structured_response=parse_structured_response,
        task_instance=task,
        config=config,
        robust_executor=robust_executor,
        log_path=store.log_path,
    )
    planner = MicroPlanner(registry)
    executor = PlanExecutor(registry, store)
    validator = ArtifactValidator(config)
    remediation = RemediationHandler(config)
    loop_cfg = config.get("loop_guard") or {}
    max_replans_cfg = max(0, int(loop_cfg.get("max_replan_without_artifact") or max_replans))
    max_failures_cfg = max(1, int(loop_cfg.get("max_consecutive_failures") or 2))
    max_attempts = max(1, max_replans_cfg + 1)
    total_llm_calls = 0
    last_reason = ""
    failures_without_progress = 0
    replan_without_artifact = 0
    fallback_paths: list[str] = []
    last_results: dict[str, JsonDict] = {}
    last_plan: list[HarnessStep] = []
    for attempt in range(max_attempts):
        try:
            history = store.recent_results()
            micro_plan, planner_calls = await planner.plan(ctx, history)
            plan = micro_plan.plan
            plan = _validate_plan_against_registry(registry, plan, "run_harness")
            last_plan = plan
            if micro_plan.expected_artifacts:
                task.update_expected_artifacts(_merge_expected_artifacts(task.expected_artifacts, micro_plan.expected_artifacts))
            total_llm_calls += planner_calls
            store.append({
                "event": "harness_plan",
                "title": title,
                "task_id": task.task_id,
                "attempt": attempt,
                "plan": [step.__dict__ for step in plan],
                "expected_artifacts": task.expected_artifacts,
                "planner_llm_calls": planner_calls,
            })
            results, exec_llm_calls, stalled = await executor.execute(ctx, plan)
            last_results = results
            total_llm_calls += exec_llm_calls
            fallback_paths.extend(_collect_fallback_paths(results))
            parsed = _latest_structured_result(results) or _compose_parsed_from_results(ctx, results)
            validation = validator.validate(task)
            parsed = _merge_result_files(parsed, results, validation)
            store.update_snapshot({
                "last_title": title,
                "last_task_id": task.task_id,
                "last_event_type": event.type.value,
                "last_ok": validation.ok,
                "last_llm_calls": total_llm_calls,
                "last_plan": [step.__dict__ for step in plan],
            })
            if validation.ok:
                task.mark("done", {"llm_calls": total_llm_calls})
                return HarnessResult(True, parsed, plan, results, llm_calls=total_llm_calls, stalled_steps=stalled)
            replan_without_artifact += 1
            last_reason = "expected artifacts missing"
            if stalled >= 3:
                failures_without_progress += 1
            if failures_without_progress >= max_failures_cfg or replan_without_artifact >= max_replans_cfg:
                remedied = remediation.remediate(
                    task=task,
                    missing=validation.missing,
                    failures=_collect_failures(results),
                    fallback_paths=fallback_paths,
                    reason="loop_guard_or_artifact_validation",
                )
                parsed = _parsed_from_remediation(ctx, parsed, remedied)
                return HarnessResult(bool(remedied.get("ok")), parsed, plan, results, reason=last_reason, llm_calls=total_llm_calls, stalled_steps=stalled)
            if attempt < max_attempts - 1:
                last_reason = "three consecutive steps made no progress"
                continue
            remedied = remediation.remediate(
                task=task,
                missing=validation.missing,
                failures=_collect_failures(results),
                fallback_paths=fallback_paths,
                reason="artifact_validation_after_plan",
            )
            parsed = _parsed_from_remediation(ctx, parsed, remedied)
            return HarnessResult(bool(remedied.get("ok")), parsed, plan, results, reason=last_reason, llm_calls=total_llm_calls, stalled_steps=stalled)
        except Exception as exc:
            last_reason = str(exc)
            failures_without_progress += 1
            store.append({"event": "harness_replan", "title": title, "task_id": task.task_id, "attempt": attempt, "reason": last_reason})
            task.append_log("harness_replan_triggered", {
                "attempt": attempt,
                "reason": last_reason,
                "failures_without_progress": failures_without_progress,
            })
            if failures_without_progress >= max_failures_cfg or attempt >= max_attempts - 1:
                break
    missing = validator.validate(task).missing
    remedied = remediation.remediate(
        task=task,
        missing=missing,
        failures=[{"event_type": "harness_loop", "error": last_reason}],
        fallback_paths=fallback_paths,
        reason="harness_loop_guard_terminated",
    )
    parsed = _parsed_from_remediation(ctx, {}, remedied)
    return HarnessResult(bool(remedied.get("ok")), parsed, last_plan, last_results, reason=last_reason, llm_calls=total_llm_calls)


async def run_harness_plan(
    *,
    workspace: str,
    event: MindEvent,
    title: str,
    project_dir: str,
    state_md: str,
    artifact_path: str,
    adapter: Any,
    build_action_prompt: Callable[[MindEvent, str, str, str], str],
    parse_structured_response: Callable[[str], JsonDict],
    micro_plan: MicroPlan,
    planner_llm_calls: int = 0,
    progress_callback: Callable[[JsonDict], Any] | None = None,
) -> HarnessResult:
    """Execute a prebuilt MicroPlan with the normal Harness executor and validators."""
    registry = default_registry()
    store = StateStore(workspace)
    config = load_harness_config(workspace)
    payload = event.payload or {}
    task = TaskInstance.load_or_create(
        workspace,
        task_id=str(payload.get("task_id") or ""),
        user_message=str(payload.get("root_user_request") or payload.get("original_user_request") or payload.get("parent_user_request") or payload.get("user_request") or title or ""),
        continue_from_project=str(payload.get("continue_from_project") or ""),
        metadata={
            "title": title,
            "event_type": event.type.value,
            "source": "batch_harness",
            "current_event_instruction": str(payload.get("user_request") or "")[:1800],
        },
    )
    payload["task_id"] = task.task_id
    payload["task_working_dir"] = task.working_dir
    payload["continue_from_project"] = task.continue_from_project
    payload["artifact_freshness_policy"] = _artifact_policy(payload)
    payload["reuse_existing_artifact"] = _reuse_existing_artifact_allowed(payload)
    expected = payload.get("expected_artifacts") if isinstance(payload.get("expected_artifacts"), list) else []
    if micro_plan.expected_artifacts:
        expected = _merge_expected_artifacts(expected, micro_plan.expected_artifacts)
    if expected:
        task.update_expected_artifacts(_merge_expected_artifacts(task.expected_artifacts, expected))
    task_artifact_path = os.path.join(task.working_dir, os.path.basename(artifact_path or f"{event.type.value}_result.md"))
    ctx = HarnessContext(
        workspace=workspace,
        event=event,
        title=title,
        project_dir=project_dir,
        state_md=state_md if task.continue_from_project else "",
        artifact_path=task_artifact_path,
        adapter=adapter,
        build_action_prompt=build_action_prompt,
        parse_structured_response=parse_structured_response,
        task_instance=task,
        config=config,
        robust_executor=RobustExecutor(config),
        log_path=store.log_path,
        progress_callback=progress_callback,
    )
    executor = PlanExecutor(registry, store)
    validator = ArtifactValidator(config)
    remediation = RemediationHandler(config)
    total_llm_calls = max(0, int(planner_llm_calls or 0))
    results: dict[str, JsonDict] = {}
    fallback_paths: list[str] = []
    try:
        store.append({
            "event": "harness_batch_plan",
            "title": title,
            "task_id": task.task_id,
            "plan": [step.__dict__ for step in micro_plan.plan],
            "expected_artifacts": task.expected_artifacts,
            "planner_llm_calls": planner_llm_calls,
        })
        task.append_log("harness_batch_plan_started", {
            "plan": [step.__dict__ for step in micro_plan.plan],
            "expected_artifacts": task.expected_artifacts,
        })
        # Validate all plan event_types against the registry
        micro_plan = MicroPlan(
            plan=_validate_plan_against_registry(registry, micro_plan.plan, "run_harness_plan"),
            expected_artifacts=micro_plan.expected_artifacts,
        )
        results, exec_llm_calls, stalled = await executor.execute(ctx, micro_plan.plan)
        total_llm_calls += exec_llm_calls
        fallback_paths = _collect_fallback_paths(results)
        parsed = _latest_structured_result(results) or _compose_parsed_from_results(ctx, results)
        validation = validator.validate(task)
        # If any step reported file_not_found, override validation to failed
        # so the pipeline stops and reports the missing file to the user.
        step_errors = _collect_failures(results)
        if step_errors and any("file_not_found" in (e.get("error") or "") for e in step_errors):
            validation.ok = False
            if not validation.missing:
                validation.missing = [e.get("error", "file_not_found") for e in step_errors if "file_not_found" in (e.get("error") or "")]
        parsed = _merge_result_files(parsed, results, validation)
        store.update_snapshot({
            "last_title": title,
            "last_task_id": task.task_id,
            "last_event_type": event.type.value,
            "last_ok": validation.ok,
            "last_llm_calls": total_llm_calls,
            "last_plan": [step.__dict__ for step in micro_plan.plan],
        })
        if validation.ok:
            task.mark("done", {"llm_calls": total_llm_calls, "source": "batch_plan"})
            return HarnessResult(True, parsed, micro_plan.plan, results, llm_calls=total_llm_calls, stalled_steps=stalled)
        remedied = remediation.remediate(
            task=task,
            missing=validation.missing,
            failures=_collect_failures(results),
            fallback_paths=fallback_paths,
            reason="batch_plan_artifact_validation",
        )
        parsed = _parsed_from_remediation(ctx, parsed, remedied)
        # Extract specific step error for the reason, e.g. "file_not_found: /data/pancreas.h5ad"
        step_error = _collect_failure_reasons(results)
        fail_reason = step_error or "expected artifacts missing"
        return HarnessResult(bool(remedied.get("ok")), parsed, micro_plan.plan, results, reason=fail_reason, llm_calls=total_llm_calls, stalled_steps=stalled)
    except Exception as exc:
        task.append_log("harness_batch_plan_failed", {"error": str(exc)})
        remedied = remediation.remediate(
            task=task,
            missing=validator.validate(task).missing,
            failures=[{"event_type": "batch_plan", "error": str(exc)}],
            fallback_paths=fallback_paths,
            reason="batch_plan_exception",
        )
        parsed = _parsed_from_remediation(ctx, {}, remedied)
        return HarnessResult(bool(remedied.get("ok")), parsed, micro_plan.plan, results, reason=str(exc), llm_calls=total_llm_calls)


def _compose_parsed_from_results(ctx: HarnessContext, results: dict[str, JsonDict]) -> JsonDict:
    findings = []
    files = []
    evidence = []
    artifact = ""
    for step_id, result in results.items():
        if result.get("findings"):
            values = result["findings"] if isinstance(result["findings"], list) else [result["findings"]]
            findings.extend(str(x) for x in values if str(x).strip())
        if result.get("files"):
            values = result["files"] if isinstance(result["files"], list) else [result["files"]]
            files.extend(str(x) for x in values if str(x).strip())
        if result.get("path"):
            evidence.append(str(result["path"]))
        if result.get("content") and not artifact:
            artifact = str(result["content"])
    return {
        "action": ctx.event.type.value,
        "step_done": "Harness 已执行微计划",
        "findings": findings[:4] or ["已完成本地微计划执行"],
        "evidence": "; ".join(evidence[:4]) or "system:harness",
        "next_action": "根据 Harness 执行结果选择下一步 event；若目标已满足则停止。",
        "state_delta": f"harness event={ctx.event.type.value}; steps={len(results)}",
        "files": "; ".join(files[:8]) if files else "EMPTY",
        "artifact_content": artifact or "EMPTY",
    }


def _collect_fallback_paths(results: dict[str, JsonDict]) -> list[str]:
    paths: list[str] = []
    for result in results.values():
        for key in ("fallback_path", "path"):
            value = str(result.get(key) or "")
            if value and "fallback" in value and value not in paths:
                paths.append(value)
    return paths


def _collect_failures(results: dict[str, JsonDict]) -> list[JsonDict]:
    failures: list[JsonDict] = []
    for step_id, result in results.items():
        if result.get("ok") is False or result.get("error"):
            failures.append({
                "step_id": step_id,
                "event_type": result.get("event_type") or "",
                "error": result.get("error") or "unknown failure",
            })
    return failures


def _collect_failure_reasons(results: dict[str, JsonDict]) -> str:
    """Extract the first meaningful error from step results.
    Returns specific error like 'file_not_found: /data/pancreas.h5ad' or empty string."""
    for step_id, result in results.items():
        error = result.get("error") or ""
        if error and "file_not_found" in str(error).lower():
            return str(error)[:300]
        if error:
            return str(error)[:300]
    return ""


def _parsed_from_remediation(ctx: HarnessContext, base: JsonDict, remedied: JsonDict) -> JsonDict:
    report_path = str(remedied.get("report_path") or "")
    materialized = [str(x) for x in (remedied.get("materialized_artifacts") or []) if str(x).strip()]
    fallback_outputs = remedied.get("fallback_outputs") if isinstance(remedied.get("fallback_outputs"), list) else []
    fallback_content = "\n\n".join(
        str(item.get("content") or "").strip()
        for item in fallback_outputs
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ).strip()
    base = dict(base or {})
    base.setdefault("action", ctx.event.type.value)
    base["delivery_status"] = str(remedied.get("status") or "failed")
    base["step_done"] = "Harness 已执行补救流程并停止本轮任务"
    base["findings"] = [
        "期望交付物缺失或外部调用失败，已按配置生成补救报告",
        f"status={remedied.get('status') or 'failed'}",
    ]
    base["evidence"] = report_path or "system:harness_remediation"
    base["next_action"] = "等待用户补充缺失外部条件或重新发起任务；本轮不会继续重规划。"
    base["state_delta"] = f"harness remediation task_id={ctx.task_instance.task_id if ctx.task_instance else ''}"
    if materialized:
        base["files"] = "; ".join(materialized[:6])
        if report_path:
            base["evidence"] = f"{base['files']}; diagnostic={report_path}"
    else:
        base["files"] = report_path or "EMPTY"
    if fallback_content:
        base["artifact_content"] = fallback_content[:2500]
    elif str(remedied.get("status") or "failed") == "partial" and report_path and os.path.exists(report_path):
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                base["artifact_content"] = f.read(2000)
        except Exception:
            base["artifact_content"] = "EMPTY"
    else:
        base["artifact_content"] = "EMPTY"
    return base


# ── Atomic handlers ─────────────────────────────────────────────────


def _atomic_read_state(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    path = os.path.join(ctx.working_dir, "task_instance.json")
    return {
        "ok": True,
        "content": ctx.state_md or "",
        "path": path,
        "source": "task_instance" if not (ctx.task_instance and ctx.task_instance.continue_from_project) else "continued_project_reference",
    }


def _atomic_list_project_files(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    limit = int(params.get("limit") or 30)
    rows = []
    root = ctx.working_dir
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir.startswith("."):
            rel_dir = ""
        if len(rows) >= limit:
            break
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                rows.append({
                    "path": os.path.relpath(path, root).replace(os.sep, "/"),
                    "size": os.path.getsize(path),
                    "mtime": os.path.getmtime(path),
                })
            except OSError:
                continue
            if len(rows) >= limit:
                break
    rows.sort(key=lambda item: item.get("mtime", 0), reverse=True)
    return {"ok": True, "files": [row["path"] for row in rows], "content": json.dumps(rows, ensure_ascii=False)}


def _safe_project_path(ctx: HarnessContext, raw: str) -> str:
    path = str(raw or "").strip()
    if not path:
        raise ValueError("missing path")
    # Shorten excessively long filenames while preserving extension
    base = os.path.basename(path)
    dirname = os.path.dirname(path)
    if len(base) > 80:
        name, ext = os.path.splitext(base)
        # Take first 60 chars + hash suffix + extension
        short = name[:60] + "_" + str(abs(hash(name)))[:6] + ext
        path = os.path.join(dirname, short) if dirname else short
    if not os.path.isabs(path):
        path = os.path.join(ctx.working_dir, path)
    root = os.path.abspath(ctx.working_dir)
    full = os.path.abspath(path)
    if not full.startswith(root + os.sep) and full != root:
        raise ValueError("path escapes task working_dir")
    return full


def _atomic_inspect_file(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    path = _safe_project_path(ctx, str(params.get("path") or ""))
    max_chars = int(params.get("max_chars") or 4000)
    with open(path, "rb") as f:
        data = f.read(max_chars)
    text = data.decode("utf-8", "replace")
    return {
        "ok": True,
        "path": path,
        "content": text,
        "size": os.path.getsize(path),
        "hex64": data[:64].hex(),
    }


def _atomic_ollama_status(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    try:
        from ..ollama_pool import heartbeat_probe, load_ollama_pool_config

        cfg = load_ollama_pool_config(ctx.workspace)
        status = heartbeat_probe(ctx.workspace, purpose=str(params.get("purpose") or "report"))
        return {
            "ok": True,
            "status": status,
            "findings": [
                f"Ollama {'可用' if status.get('selected') else '不可用'}",
                f"mode={status.get('mode') or cfg.get('mode')}",
            ],
            "content": json.dumps(status, ensure_ascii=False),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _try_execute_local_script(workspace: str, task_instance: Any, skill_name: str, inputs: dict) -> dict | None:
    """Generic script executor: when a skill isn't registered, look for
    executable scripts in the task working directory and run them.

    Supported conventions:
    - skill name containing "python" or "py": runs .py files
    - skill name containing "shell" or "sh" or "bash": runs .sh files
    - Otherwise: looks for any executable file matching the skill name
    """
    if not task_instance:
        return None
    work_dir = str(getattr(task_instance, "working_dir", "") or "")
    if not work_dir or not os.path.isdir(work_dir):
        return None

    skill_lower = skill_name.lower()

    # Determine file extensions to look for based on skill name
    if "python" in skill_lower or "py" in skill_lower:
        extensions = [".py"]
    elif "shell" in skill_lower or "sh" in skill_lower or "bash" in skill_lower:
        extensions = [".sh"]
    elif "r_" in skill_lower or skill_lower == "r":
        extensions = [".r", ".R"]
    else:
        extensions = [".py", ".sh"]

    # Find the most recently written script file with matching extension
    candidates: list[tuple[str, float]] = []
    for fname in os.listdir(work_dir):
        fpath = os.path.join(work_dir, fname)
        if not os.path.isfile(fpath):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext in extensions:
            try:
                mtime = os.path.getmtime(fpath)
                candidates.append((fpath, mtime))
            except OSError:
                continue

    if not candidates:
        return None

    # Sort by modification time (most recent first)
    candidates.sort(key=lambda x: x[1], reverse=True)
    script_path = candidates[0][0]

    # Determine interpreter
    ext = os.path.splitext(script_path)[1].lower()
    if ext == ".py":
        interpreter = ["python3"]
    elif ext == ".sh":
        interpreter = ["bash"]
    elif ext in (".r", ".R"):
        interpreter = ["Rscript"]
    else:
        interpreter = ["python3"]

    timeout = max(10, min(600, int(inputs.get("timeout", 120))))
    try:
        logger.info("[HARNESS] executing local script: %s with %s (timeout=%ss)", script_path, interpreter[0], timeout)
        result = subprocess.run(
            interpreter + [script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        output = (result.stdout or "")[:8000]
        errors = (result.stderr or "")[:2000]
        logger.info("[HARNESS] local script %s exit=%d stdout=%dB stderr=%dB",
                     script_path, result.returncode, len(result.stdout or ""), len(result.stderr or ""))
        return {
            "ok": result.returncode == 0,
            "skill": skill_name,
            "content": output,
            "error": errors if result.returncode != 0 else "",
            "metadata": {
                "script_path": script_path,
                "exit_code": result.returncode,
                "interpreter": interpreter[0],
            },
        }
    except subprocess.TimeoutExpired:
        logger.warning("[HARNESS] local script %s timed out after %ss", script_path, timeout)
        return {"ok": False, "skill": skill_name, "error": f"script timed out after {timeout}s"}
    except Exception as exc:
        logger.warning("[HARNESS] local script %s failed: %s", script_path, exc)
        return {"ok": False, "skill": skill_name, "error": str(exc)}


async def _atomic_execute_skill(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Execute a call_agent_skill event — forward the task text to the agent.

    Simplified interface:
      {"agent": "hermes", "task": "成都近一周天气，表格发我"}

    Partner no longer manages per-skill configurations or command templates.
    The agent decides internally which tools and skills to use.
    """
    agent = str(params.get("agent") or ctx.config.get("default_agent") or "hermes").strip().lower()
    task = str(params.get("task") or params.get("query") or params.get("user_request") or "").strip()
    import logging as _harness_log; _harness_log.warning("[HARNESS_DEBUG] _atomic_execute_skill called agent=%s task=%s params_keys=%s", agent, task[:50], list(params.keys()))
    if not task:
        # Fallback to event title when MicroPlanner omits task parameter
        task = str(getattr(ctx, "title", "") or getattr(ctx.event, "title", "") or "").strip()
    if not task:
        return {"ok": False, "error": "missing task (pass task, query, or user_request in parameters)"}
    if not ctx.task_instance:
        return {"ok": False, "error": "missing task instance"}

    # ── Enhance task prompt for cytobridge to ensure complete pipeline execution ──
    # The agent's exec mode runs one turn. Without explicit guidance the LLM may
    # stop after reading workflow skills and inspecting data. We append a
    # directive to run the full analysis pipeline autonomously.
    if agent == "cytobridge" and not task.endswith("【完整管线】"):
        task = task.rstrip(".,。，") + "。请完整执行整个分析管线：数据加载与预处理、归一化与特征选择、降维(PCA/UMAP)、轨迹推断与拟时序分析、分化路径与细胞命运图谱、驱动基因鉴定。完成后用中文写出完整报告。不要只检查数据就停止。【完整管线】"
        params["task"] = task

    # ── Pre-flight file existence check ──
    # Before dispatching to the agent, check if referenced input files exist.
    # This prevents the agent from running and returning a misleading error.
    input_file = str(params.get("input") or params.get("file") or params.get("data") or params.get("file_path") or "").strip()
    if not input_file:
        # Also check nested "parameters" dict where the planner stores them
        _nested = params.get("parameters")
        if isinstance(_nested, dict):
            input_file = str(_nested.get("input") or _nested.get("file") or _nested.get("data") or _nested.get("file_path") or "").strip()
    if not input_file:
        # Also scan task text for likely file paths
        _path_match = re.search(r"(?:/data/|/mnt/[a-z]/|[a-zA-Z]:\\\\)[\w/. -]+\.\w+", task)
        if _path_match:
            input_file = _path_match.group(0)
    if input_file:
        # Normalize common prefixes
        if not os.path.isabs(input_file) and not input_file.startswith("/"):
            input_file = "/" + input_file
        if not os.path.exists(input_file):
            # Search common locations
            fname = os.path.basename(input_file)
            found = []
            for _root in ("/data", "/mnt/e/work/data", "/mnt/e/work", os.path.expanduser("~")):
                if os.path.isdir(_root):
                    for _dir, _, _files in os.walk(_root):
                        if fname in _files:
                            found.append(os.path.join(_dir, fname))
                        if len(found) >= 3:
                            break
                    if found:
                        break
            if found:
                ctx.task_instance.append_log("preflight_file_resolved", {
                    "original": input_file,
                    "resolved": found[0],
                })
                # Update the input file path to the resolved location so the
                # agent receives a valid path, not the original non-existent one
                input_file = found[0]
                params["input"] = input_file
            else:
                ctx.task_instance.append_log("preflight_file_missing", {
                    "path": input_file,
                })
                return {
                    "ok": False,
                    "agent": agent,
                    "error": f"Input file not found: {input_file}. Searched common locations but did not find '{fname}'.",
                    "_error_type": "file_not_found",
                }
    # ── End pre-flight check ──

    ctx.task_instance.append_log("call_agent_skill_forwarded", {
        "agent": agent,
        "task_preview": task[:200],
    })

    try:
        from ..skills.external_agent_skills import execute_agent_task

        # Forward remaining params (input, output, device, question, etc.)
        # for CLI placeholder substitution in specialized agents
        agent_params = {
            k: v for k, v in params.items()
            if k not in ("agent", "task", "query", "user_request", "allow_web")
        }
        # Promote nested "parameters" dict to top level — the planner nests
        # agent-specific args (input, output, question, device) under a
        # "parameters" key, but CLI placeholder substitution needs them flat.
        nested_params = agent_params.pop("parameters", None)
        if isinstance(nested_params, dict):
            # Don't overwrite existing top-level keys with nested ones
            for k, v in nested_params.items():
                if k not in agent_params:
                    agent_params[k] = v

        # ── Direct cytobridge dispatch via wrapper script (DISABLED) ──
        # The old hardcoded bypass that tried to call the deleted cytobridge-wrapper.
        # Now all agents including cytobridge go through execute_agent_task()
        # at the end of this function via the standard AgentDispatcher path.
        if False and agent == "cytobridge":
            import logging as _cytolog
            _cytolog.warning("[CYTO_DEBUG] entering cytobridge bypass agent_params_keys=%s input=%s output=%s question=%s device=%s",
                list(agent_params.keys()),
                str(agent_params.get("input", "")),
                str(agent_params.get("output", "")),
                str(agent_params.get("question", "")),
                str(agent_params.get("device", "")),
            )
            ctx.task_instance.append_log("cytobridge_direct_dispatch", {
                "agent": agent,
                "input": str(agent_params.get("input", "")),
                "has_question": bool(agent_params.get("question")),
            })
            import subprocess as _sp
            # Long timeout (7200s = 2h) for full CPU trajectory inference on large datasets
            _cytobridge_timeout = 7200
            try:
                _input = str(agent_params.get("input") or "")
                _question = str(agent_params.get("question") or task[:200])
                _output = str(agent_params.get("output") or "")
                _device = str(agent_params.get("device") or "cpu")
                # Force CPU — CUDA not available in subprocess environment
                _device = "cpu"
                # Always use a fixed, writable output directory under workspace
                _output = str(agent_params.get("parameters", {}).get("output") or agent_params.get("output") or "")
                if _output:
                    # Sanitize: reject non-writable paths (e.g. planner may hallucinate /data/xxx)
                    # and relative paths (e.g. ./output/cytobridge — ends up in partner src dir)
                    _output_ok = True
                    if not os.path.isabs(_output):
                        _cytolog.warning("[CYTO_DEBUG] rejecting relative output path: %s", _output)
                        _output_ok = False
                    else:
                        try:
                            os.makedirs(_output, exist_ok=True)
                            _test = os.path.join(_output, ".cytobridge_writable_test")
                            with open(_test, "w") as _tf:
                                _tf.write("ok")
                            os.remove(_test)
                        except (OSError, PermissionError):
                            _cytolog.warning("[CYTO_DEBUG] output path not writable: %s, falling back to workspace", _output)
                            _output_ok = False
                    if not _output_ok:
                        _output = ""
                if not _output:
                    _task_id = getattr(ctx.task_instance, "task_id", None) or str(ctx.task_instance.event_id if hasattr(ctx.task_instance, "event_id") else "")[:12] if ctx.task_instance else ""
                    _output = os.path.join(ctx.workspace, "system", "hermes_work", "cytobridge_output")
                    if _task_id:
                        _output = os.path.join(_output, _task_id)
                    os.makedirs(_output, exist_ok=True)
                _cytolog.warning("[CYTO_DEBUG] resolved output=%s", _output)
                # Resolve wrapper path: search known locations since Partner's
                # subprocess PATH is minimal (no ~/.local/bin or conda)
                import shutil as _cytoshutil
                _wrap_path = _cytoshutil.which("cytobridge-wrapper")
                if not _wrap_path:
                    _wrap_candidates = [
                        os.path.expanduser("~/.local/bin/cytobridge-wrapper"),
                        os.path.expanduser("~/miniconda3/bin/cytobridge-wrapper"),
                        "/mnt/e/work/scripts/cytobridge-wrapper",
                    ]
                    for _cand in _wrap_candidates:
                        if os.path.isfile(_cand) and os.access(_cand, os.X_OK):
                            _wrap_path = _cand
                            break
                _wrapper_cmd = [_wrap_path or "cytobridge-wrapper", "--input", _input, "-q", _question, "-o", _output, "--device", _device]
                _cytolog.warning("[CYTO_DEBUG] running wrapper cmd=%s", " ".join(_wrapper_cmd))
                _env = os.environ.copy()
                _env["OPENAI_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
                _env["OPENAI_BASE_URL"] = "https://api.deepseek.com"
                # Thread safety: use 2 threads to balance parallelism and memory.
                # 4 threads caused worse OOM, 1 thread caused workspace fragmentation.
                _env["OPENBLAS_NUM_THREADS"] = "2"
                _env["MKL_NUM_THREADS"] = "2"
                _env["OMP_NUM_THREADS"] = "2"
                _env["NUMEXPR_NUM_THREADS"] = "2"
                _cytolog.warning("[CYTO_DEBUG] wrapper timeout=%ss", _cytobridge_timeout)
                _r = _sp.run(_wrapper_cmd, capture_output=True, text=True, timeout=_cytobridge_timeout, env=_env)
                _cytolog.warning("[CYTO_DEBUG] wrapper completed rc=%s stdout_len=%s stderr_len=%s stderr_preview=%s",
                    _r.returncode, len(_r.stdout or ""), len(_r.stderr or ""), (_r.stderr or "")[:200])
                _content = _r.stdout + _r.stderr
                ctx.task_instance.append_log("cytobridge_wrapper_completed", {
                    "exit_code": _r.returncode,
                    "stdout_len": len(_r.stdout or ""),
                    "stderr_len": len(_r.stderr or ""),
                })
                if _r.returncode == 0 or _content.strip():
                    # Post-process: scan output directory for generated files
                    _result_files = []
                    if os.path.isdir(_output):
                        for _root, _dirs, _files in os.walk(_output):
                            for _f in _files:
                                _result_files.append(os.path.join(_root, _f))
                    # Enrich content with structured analysis data from result files
                    # so downstream steps ($step1.result.content) get real data
                    # instead of the wrapper's execution log.
                    _enriched = _content[:50000]
                    _result_json_path = os.path.join(_output, "result.json")
                    _summary_json_path = os.path.join(_output, "data", "summary.json")
                    _correlated_genes_path = os.path.join(_output, "data", "trajectory_correlated_genes.csv")
                    _figure_dir = os.path.join(_output, "figures")
                    _enrich_blocks = []
                    # 1) result.json — full analysis summary
                    for _rj_path in (_result_json_path, _summary_json_path):
                        if os.path.isfile(_rj_path):
                            try:
                                with open(_rj_path, "r") as _rj_f:
                                    _rj_data = json.load(_rj_f)
                                _enrich_blocks.append(
                                    f"【Cytobridge 分析数据——请用自然语言写分析报告，不要生成代码】\n"
                                    f"以下是从 cytobridge 输出的结构化分析结果，包含细胞数量、聚类、伪时间分布等关键指标。\n"
                                    f"请基于这些数据撰写一份完整的自然语言中文分析报告，不要写 Python 代码或计算脚本。\n"
                                    f"{json.dumps(_rj_data, ensure_ascii=False, indent=2)}"
                                )
                                break
                            except Exception:
                                pass
                    # 2) trajectory correlated genes (top 20)
                    if os.path.isfile(_correlated_genes_path):
                        try:
                            import csv as _csv_mod
                            _cg_lines = []
                            with open(_correlated_genes_path, "r") as _cg_f:
                                _cg_reader = _csv_mod.reader(_cg_f)
                                for _i, _row in enumerate(_cg_reader):
                                    if _i > 20:
                                        break
                                    _cg_lines.append("\t".join(_row))
                            if _cg_lines:
                                _enrich_blocks.append(f"【轨迹相关基因 Top 20】\n" + "\n".join(_cg_lines))
                        except Exception:
                            pass
                    # 3) figure file listing
                    if os.path.isdir(_figure_dir):
                        _figs = [os.path.join(_figure_dir, _f) for _f in sorted(os.listdir(_figure_dir)) if _f.endswith((".png", ".svg", ".pdf"))]
                        if _figs:
                            _enrich_blocks.append(f"【可视化图表】\n" + "\n".join(_figs))
                    if _enrich_blocks:
                        _enriched = _content[:50000] + "\n\n" + "\n\n".join(_enrich_blocks)
                    return {"ok": True, "skill": "cytobridge", "content": _enriched[:50000], "content_full": _enriched, "_via": "wrapper", "exit_code": _r.returncode, "files": _result_files}
                return {"ok": False, "skill": "cytobridge", "error": _content[:500], "_error_type": "agent_error", "_via": "wrapper", "exit_code": _r.returncode}
            except _sp.TimeoutExpired:
                _cytolog.warning("[CYTO_DEBUG] wrapper timed out after %ss", _cytobridge_timeout)
                return {"ok": False, "skill": "cytobridge", "error": f"cytobridge wrapper timed out after {_cytobridge_timeout}s", "_error_type": "timeout"}
            except Exception as _exc:
                _cytolog.warning("[CYTO_DEBUG] wrapper exception: %s", _exc, exc_info=True)
                return {"ok": False, "skill": "cytobridge", "error": str(_exc)[:300], "_error_type": "agent_error"}

        result = await execute_agent_task(
            workspace=ctx.workspace,
            agent=agent,
            task=task,
            task_instance=ctx.task_instance,
            allow_web=bool(params.get("allow_web", False)),
            agent_params=agent_params,
        )
    except Exception as exc:
        logger.warning("[HARNESS] call_agent_skill failed agent=%s task=%s error=%s", agent, task[:80], exc)
        return {"ok": False, "error": str(exc)}

    if not result.ok:
        # Classify error from the error text (not content) for downstream diagnosis
        _agent_err_signals = [
            "error code:", "error occurred", "incorrect api key",
            "401", "402", "403", "429", "500", "502", "503",
            "authentication failed", "api key not found",
            "command not found", "no module named",
            "connection refused", "connection timed out",
            "traceback (most recent call last)",
        ]
        _file_err_signals = ["no such file", "file not found", "cannot open",
                             "No such file or directory", "does not exist",
                             "failed to open", "not found:"]
        _api_err_signals = ["401", "incorrect api key", "authentication failed",
                            "invalid api key", "api key not found"]
        err_text = (result.error or "").lower()
        err_type = "unknown"
        # CLI-not-found is an agent_error, not file_not_found — check before
        # the general "not found:" signal which also matches "CLI not found"
        if any(s in err_text for s in _api_err_signals):
            err_type = "api_key"
        elif "cli not found" in err_text or "command not found" in err_text:
            err_type = "agent_error"
        elif any(s in err_text for s in _file_err_signals):
            err_type = "file_not_found"
        elif any(s in err_text for s in _agent_err_signals):
            err_type = "agent_error"
        return {"ok": False, "skill": f"{agent}", "error": result.error or "agent returned no result",
                "_error_type": err_type}

    output = result.output or {}
    content = output.get("content") or ""
    # Detect agent-internal errors: even with exit code 0, the agent might
    # have failed internally (e.g., LLM call returned 401, file not found).
    # Strategy: check stderr (result.error) for ALL agents, but only check
    # stdout (content) for GENERAL agents (hermes, openclaw, codex).
    _GENERAL_AGENT_SET = {"hermes", "openclaw", "codex"}
    _HTTP_STATUS_SIGNALS = ["401", "402", "403", "429", "500", "502", "503"]  # Used only for general agents
    _AGENT_ERROR_SIGNALS = [
        "error code:", "error occurred", "incorrect api key",
        "authentication failed", "api key not found",
        "command not found", "no module named",
        "connection refused", "connection timed out",
        "traceback (most recent call last)",
    ]
    # File/signal-based error classification for downstream decision-making
    _FILE_NOT_FOUND_SIGNALS = ["no such file", "file not found", "cannot open",
                               "No such file or directory", "does not exist",
                               "failed to open", "not found:"]
    _API_KEY_SIGNALS = ["incorrect api key", "authentication failed",
                        "invalid api key", "api key not found"]
    # Check stderr (result.error) for ALL agent types
    err_text = (result.error or "").lower()
    has_agent_error_stderr = any(signal in err_text for signal in _AGENT_ERROR_SIGNALS)
    has_file_error_stderr = any(signal in err_text for signal in _FILE_NOT_FOUND_SIGNALS)
    has_api_key_error_stderr = any(signal in err_text for signal in _API_KEY_SIGNALS)
    has_http_error_stderr = any(signal in err_text for signal in _HTTP_STATUS_SIGNALS)
    # Check stdout (content) only for GENERAL agents
    content_lower = content.lower()
    is_general = agent in _GENERAL_AGENT_SET
    has_agent_error_stdout = any(signal in content_lower for signal in _AGENT_ERROR_SIGNALS) if is_general else False
    has_file_error_stdout = any(signal in content_lower for signal in _FILE_NOT_FOUND_SIGNALS) if is_general else False
    has_api_key_error_stdout = any(signal in content_lower for signal in _API_KEY_SIGNALS) if is_general else False
    has_http_error_stdout = any(signal in content_lower for signal in _HTTP_STATUS_SIGNALS) if is_general else False
    # Merge stderr and stdout checks
    has_agent_error = has_agent_error_stderr or has_agent_error_stdout
    has_file_error = has_file_error_stderr or has_file_error_stdout
    has_api_key_error = has_api_key_error_stderr or has_api_key_error_stdout
    # Determine error type for downstream classification
    error_type = "unknown"
    if has_api_key_error:
        error_type = "api_key"
    elif has_file_error:
        error_type = "file_not_found"
    elif has_agent_error:
        error_type = "agent_error"
    # Attach error classification metadata — return ok=False for ALL detected errors, not just agent_error
    if error_type != "unknown":
        return {
            "ok": False,
            "agent": agent,
            "error": f"Agent '{agent}' reported an error: {content[:500]}",
            "_error_type": error_type,  # For downstream root cause diagnosis
        }
    return {
        "ok": True,
        "agent": agent,
        "content": content[:200000] if agent not in {"hermes", "openclaw", "codex"} else content[:50000],
        "json": output,
    }


def _atomic_ensure_agent_installed(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Ensure a specialized agent is installed and available.

    Checks via registry.health_check(). If the agent is unavailable and
    its manifest has install_info, auto-installs it (pip/git/script).
    Pre-pends conda bin dirs to PATH so shutil.which() can find agents
    installed in ~/miniconda3/bin/ or ~/.local/bin/.
    """
    agent = str(params.get("agent") or "").strip().lower()
    if not agent:
        return {"ok": False, "error": "missing agent name"}

    # Prepend conda/local bin dirs to PATH so health_check can find agents.
    # IMPORTANT: conda env bin dirs (e.g. ~/miniconda3/envs/*/bin) must NOT be
    # added because they may contain agents with different Python versions
    # that shadow the base miniconda's working agent binaries.
    _conda_bins = [
        os.path.expanduser("~/miniconda3/bin"),
        os.path.expanduser("~/.local/bin"),
    ]
    _existing_path = os.environ.get("PATH", "")
    _new_path_parts = [b for b in _conda_bins if os.path.isdir(b) and b not in _existing_path]
    if _new_path_parts:
        os.environ["PATH"] = ":".join(_new_path_parts + [_existing_path])

    from ..agents.registry import AgentRegistry
    registry = AgentRegistry(workspace=ctx.workspace if hasattr(ctx, "workspace") else None)
    health = registry.health_check(agent)
    status = health.get("status", "error")

    if status == "ok":
        return {"ok": True, "agent": agent, "status": "already_installed"}

    # Agent is not available — try to auto-install if manifest has install_info
    manifest = registry.get_agent(agent)
    if not manifest or not manifest.install_info:
        return {
            "ok": False,
            "agent": agent,
            "error": f"Agent '{agent}' not found and no install_info available: {health.get('details', '')}",
            "health": health,
        }

    # Attempt installation
    import subprocess as _sp
    _info = manifest.install_info
    _method = _info.get("method", "")
    _desc = _info.get("description", f"Installing {agent}...")
    import logging as _log
    _log.warning("[HARNESS] %s", _desc)

    try:
        if _method == "pip":
            _package = _info.get("package", agent)
            _r = _sp.run(["pip", "install", _package], capture_output=True, text=True, timeout=300)
            if _r.returncode != 0:
                return {"ok": False, "agent": agent, "error": f"pip install failed: {_r.stderr[:500]}", "health": health}
        elif _method == "git":
            _source = _info.get("source", "")
            _target = os.path.expanduser(_info.get("target", "~/.partner/agents"))
            os.makedirs(_target, exist_ok=True)
            _r = _sp.run(["git", "clone", _source, os.path.join(_target, agent)], capture_output=True, text=True, timeout=300)
            if _r.returncode != 0:
                return {"ok": False, "agent": agent, "error": f"git clone failed: {_r.stderr[:500]}", "health": health}
            _install_cmd = _info.get("post_install")
            if _install_cmd:
                _r = _sp.run(_install_cmd, shell=True, capture_output=True, text=True, timeout=300, cwd=os.path.join(_target, agent))
                if _r.returncode != 0:
                    return {"ok": False, "agent": agent, "error": f"post-install failed: {_r.stderr[:500]}", "health": health}
        elif _method == "script":
            _script = _info.get("script", "")
            _r = _sp.run(_script, shell=True, capture_output=True, text=True, timeout=600)
            if _r.returncode != 0:
                return {"ok": False, "agent": agent, "error": f"install script failed: {_r.stderr[:500]}", "health": health}
        else:
            return {"ok": False, "agent": agent, "error": f"unsupported install method: {_method}", "health": health}
    except _sp.TimeoutExpired:
        return {"ok": False, "agent": agent, "error": f"installation timed out for method={_method}", "health": health}
    except Exception as _exc:
        return {"ok": False, "agent": agent, "error": f"installation exception: {_exc}", "health": health}

    # Re-check health after installation
    _post_health = registry.health_check(agent)
    if _post_health.get("status") == "ok":
        return {"ok": True, "agent": agent, "status": "installed", "health": _post_health}
    return {"ok": False, "agent": agent, "error": f"Agent '{agent}' still unavailable after install: {_post_health.get('details', '')}", "health": _post_health}


def _clean_script_content(content: str) -> str:
    """Clean internal markers and diff artifacts from script files.

    Unlike markdown content (cleaned by text_cleaner), script files need
    surgical removal of only the characters that would break syntax:
    - ┊ (diff column separator, U+250A) — breaks Python syntax
    - Lines that are pure diff metadata (review diff, @@ headers)
    - Lines that start with "+" or "-" diff prefix (but keep the content after the prefix)
    """
    if not content:
        return content

    lines = content.split("\n")
    cleaned = []
    skip_diff_block = False

    for line in lines:
        stripped = line.strip()

        # Remove ┊ character
        line = line.replace("┊", "")

        # Detect and skip diff block headers
        if re.match(r"^(review\s+diff|diff\s+--git|index\s+[0-9a-f]+\.\.[0-9a-f]+)", stripped, re.I):
            skip_diff_block = True
            continue
        if re.match(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@", stripped):
            continue
        if re.match(r"^(---\s+a/|\+\+\+\s+b/)", stripped):
            continue

        # Inside a diff block, strip "+" or "-" prefix from content lines
        if skip_diff_block:
            if stripped == "" or stripped.startswith("+"):
                # Empty line or addition — keep the content after "+"
                if stripped.startswith("+"):
                    line = line.replace("+", "", 1) if line.startswith("+") else line
                    skip_diff_block = False  # End of diff block after first content line
                else:
                    skip_diff_block = False
                    continue
            elif stripped.startswith("-"):
                continue  # Skip removed lines
            else:
                skip_diff_block = False

        cleaned.append(line)

    result = "\n".join(cleaned)
    # Also remove any stray ┊ that might remain
    result = result.replace("┊", "")
    return result


def _is_placeholder_content(content: str, output_path: str = "") -> bool:
    """Check if content is a placeholder/redirect that shouldn't be delivered.

    Detects:
    - Content that just says "see file X" (redirect)
    - Content that's a plan template structure
    - Content that's suspiciously short for its type
    """
    if not content or len(content.strip()) < 100:
        return True

    lower = content.lower()
    first_300 = content[:300].lower()

    # Redirect pattern: "see file X" or "见文件 X"
    if re.search(r"(?:see|见|参考|查看)\s*(?:batch_plan_result|the\s+file|file\s+above|attached|attached file)", first_300):
        return True

    # Plan template leakage: Harness MicroPlan or Phase N: structure
    if re.search(r"harness\s+(micro)?plan", lower) and re.search(r"phase\s+\d+|step\s+\d+\.\d+", lower):
        return True

    # Suspiciously short for a document type
    ext = os.path.splitext(output_path)[1].lower()
    if ext == ".md" and len(content) < 200:
        return True
    if ext == ".pdf" and len(content) < 200:
        return True
    if ext in (".py", ".sh", ".bash", ".r", ".R") and len(content) < 200:
        return True

    # Placeholder bracket patterns: "[完整...]", "[详细...]", "[content]", "[placeholder]"
    if re.search(r"\[\s*(?:完整|详细|content|placeholder|详细内容|完整内容|完整报告|完整的|内容)\s*\]", first_300):
        return True

    # Truncation marker: "..." near end of content, with no substantial content after it
    stripped = content.strip()
    if stripped.rstrip(".").endswith("...") and len(stripped) < 500:
        # Check if the "..." is within the last 20% of content
        idx = stripped.rfind("...")
        if idx >= len(stripped) * 0.8:
            return True

    return False


def _resolve_step_variables(text: str, task_instance: Any) -> str:
    """Resolve $step_X.Y template variables using step result files.

    Supports:
    - $step_X.result.content  → result["content"] from step X's result file
    - $step_X.result.json     → result["json"] from step X's result file
    - $step_X.summary          → result["content"][:200] from step X
    - $step_X_Y.Z             → step with sub-id like step_5_2

    Falls back to scanning all step result files by step_id if exact file
    is not found. Returns empty string for unresolvable variables instead
    of leaking raw template syntax.
    """
    if not text or not task_instance:
        return text or ""

    work_dir = str(getattr(task_instance, "working_dir", "") or "")
    if not work_dir or not os.path.isdir(work_dir):
        return text

    # Pre-scan all step result files for step_id matching
    _all_step_results: dict[str, dict] | None = None

    def _load_all_step_results() -> dict[str, dict]:
        nonlocal _all_step_results
        if _all_step_results is not None:
            return _all_step_results
        _all_step_results = {}
        try:
            for fname in os.listdir(work_dir):
                if fname.startswith("_step_") and fname.endswith(".result.json"):
                    try:
                        fpath = os.path.join(work_dir, fname)
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.loads(f.read())
                        sid = str(data.get("step_id") or "")
                        if sid:
                            _all_step_results[sid] = data
                    except Exception:
                        continue
        except Exception:
            pass
        return _all_step_results

    def _resolve_var(match: re.Match) -> str:
        var = match.group(0)  # e.g. "$step_4.result.content"
        # Parse: $step_X.Y.Z → step_id="step_X", path_parts=[...,"content"]
        inner = var[1:]  # remove leading "$"
        parts = inner.split(".")
        if len(parts) < 2:
            return ""  # not enough parts, don't leak raw var
        step_key = parts[0]  # e.g. "step_4" or "step_5_2"
        # Build step file path pattern
        step_files = [f for f in os.listdir(work_dir)
                      if f.startswith(f"_step_{step_key}.") and f.endswith(".result.json")]
        if not step_files:
            # Fallback: try scanning all step result files by step_id
            all_results = _load_all_step_results()
            if step_key in all_results:
                step_data = all_results[step_key]
            else:
                return ""  # completely unresolvable
        else:
            step_path = os.path.join(work_dir, step_files[0])
            try:
                with open(step_path, "r", encoding="utf-8") as f:
                    step_data = json.loads(f.read())
            except Exception:
                return ""
        try:
            # Navigate the JSON path from the step data
            current = step_data
            for key in parts[1:]:  # skip the step key
                if isinstance(current, dict):
                    current = current.get(key, "")
                elif isinstance(current, list):
                    try:
                        current = current[int(key)]
                    except (ValueError, IndexError):
                        return ""
                else:
                    return ""
            if current is None or current == "":
                return ""
            # Convert to string
            if isinstance(current, (dict, list)):
                result_str = json.dumps(current, ensure_ascii=False, indent=2)
            else:
                result_str = str(current)
            # Limit length
            if len(result_str) > 12000:
                result_str = result_str[:12000] + "\n...(truncated)"
            return result_str
        except Exception:
            return ""

    return re.sub(r"\$step_\w+(?:\.\w+)*", _resolve_var, text)


def _resolve_write_content_from_deps(ctx, content: str) -> str | None:
    """When write_artifact content is a placeholder, try reading from dep step files."""
    import glob
    if not ctx or not ctx.task_instance:
        return None
    work_dir = getattr(ctx.task_instance, "working_dir", None)
    if not work_dir:
        return None
    # Scan step result files for actual content
    for fpath in sorted(glob.glob(os.path.join(work_dir, "_step_*.result.json"))):
        try:
            with open(fpath, "r", encoding="utf-8") as fd:
                data = json.load(fd)
            result = data.get("result") or {}
            # Check for files written by the dep step, try to read them
            files_field = result.get("files") or (result.get("parsed") or {}).get("files") or ""
            if isinstance(files_field, str):
                for line in files_field.split("\n"):
                    line = line.strip().lstrip("- ").strip()
                    if line.endswith(".csv") and os.path.exists(line):
                        with open(line, "r", encoding="utf-8") as rf:
                            return rf.read()
                    # Also check for .md files — LLM often writes report .md as side-effect
                    if line.endswith(".md") and os.path.exists(line):
                        with open(line, "r", encoding="utf-8") as rf:
                            md_content = rf.read()
                        if len(md_content) > 500:
                            return md_content
            # Also check result.files array for .md files
            result_files = result.get("files") or []
            if isinstance(result_files, list):
                for rf_path in result_files:
                    if isinstance(rf_path, str) and rf_path.endswith(".md") and os.path.exists(rf_path):
                        with open(rf_path, "r", encoding="utf-8") as rf:
                            md_content = rf.read()
                        if len(md_content) > 500:
                            return md_content
        except Exception:
            continue
    # Fallback: check result.parsed.artifact_content
    for fpath in sorted(glob.glob(os.path.join(work_dir, "_step_*.result.json"))):
        try:
            with open(fpath, "r", encoding="utf-8") as fd:
                data = json.load(fd)
            result = data.get("result") or {}
            parsed = result.get("parsed") or {}
            raw = str(parsed.get("artifact_content") or "")
            if raw and len(raw) > 100 and not _is_placeholder_content(raw, ""):
                return raw
        except Exception:
            continue
    return None


def _atomic_write_artifact(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    content = str(params.get("content") or params.get("content_template") or "").strip()
    # Resolve $step_X.Y template variables before writing
    content = _resolve_step_variables(content, ctx.task_instance)
    # ── Content quality check: if resolved content is too short (<2000B),
    # try reading from dependency step's output .md files.
    # The LLM often writes the full report to a separate file and puts
    # only a summary in result.content — downstream steps get the summary
    # but should deliver the full report.
    if len(content) < 2000 and ctx.task_instance:
        dep_content = _resolve_write_content_from_deps(ctx, content)
        if dep_content:
            logger.info("[HARNESS] falling back to dep file content: was %dB, now %dB", len(content), len(dep_content))
            content = dep_content
    if not content or content.upper() == "EMPTY":
        expected = ctx.task_instance.expected_artifacts if ctx.task_instance else []
        if _expected_file_artifacts(expected):
            return {"ok": False, "error": "empty artifact content for expected file artifact", "path": "", "content": "", "files": []}
        return {"ok": True, "path": "", "content": "", "files": []}
    path = _safe_project_path(ctx, str(params.get("path") or params.get("filename") or params.get("file_name") or ctx.artifact_path))
    # Quality check: reject placeholder/redirect content
    if _is_placeholder_content(content, path):
        logger.warning("[HARNESS] rejecting placeholder content for artifact: %s (len=%d, first_100=%s)",
                       path, len(content), content[:100].replace("\n", " "))
        # Fallback: try to read content from dependency step results
        dep_content = _resolve_write_content_from_deps(ctx, content)
        if dep_content:
            content = dep_content
        else:
            return {"ok": False, "error": "placeholder/rejected content: content is a redirect or plan template", "path": path, "content": content[:200], "files": []}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # ── Guard: if file already exists from step side-effect and is larger, keep it ──
    if os.path.exists(path):
        existing_size = os.path.getsize(path)
        incoming_size = len(content.encode("utf-8"))
        if existing_size > incoming_size * 1.5 and existing_size >= 500:
            logger.info("[HARNESS] skipping atomic_write_artifact overwrite: existing %s (%dB) > incoming (%dB)",
                        path, existing_size, incoming_size)
            return {"ok": True, "path": path, "content": f"skipped overwrite (existing file larger)", "files": [path]}
    output_format = str(params.get("format") or params.get("output_format") or "").strip().lower()
    # ── Clean content before writing ──
    ext = os.path.splitext(path)[1].lower()
    if ext in (".py", ".sh", ".r", ".bash", ".R"):
        # Script files need cleaning but must remain syntactically valid.
        cleaned_script = _clean_script_content(content)
        if cleaned_script != content:
            logger.info("[HARNESS] cleaned internal markers from script: %s (was %dB, now %dB)",
                        path, len(content), len(cleaned_script))
            content = cleaned_script
    else:
        # ── Clean user-facing content (markdown, CSV, text) ───────────
        try:
            from ..utils.text_cleaner import clean_user_facing_text
            cleaned = clean_user_facing_text(content)
            if cleaned != content:
                logger.info("[HARNESS] cleaned user-facing content: %s (was %dB, now %dB)",
                            path, len(content), len(cleaned))
                content = cleaned
        except Exception:
            pass
        # ── CSV-specific: extract tables/CSV from mixed content ──
        if ext in (".csv",):
            all_lines = [l.strip() for l in content.split("\n") if l.strip()]
            csv_lines = []
            for line in all_lines:
                # Skip diff/comment markers
                if line.startswith("┊") or line.startswith("@@") or line.startswith("---") or line.startswith("+++"):
                    continue
                # Strip diff +/- prefix
                raw = line.lstrip("+-").strip()
                # Skip separator lines
                if "--" in raw and "|" in raw:
                    continue
                # If line looks like table data (pipe-separated or comma-separated columns)
                if raw.startswith("|") and raw.endswith("|"):
                    cells = [c.strip() for c in raw.split("|")[1:-1]]
                    csv_lines.append(",".join(cells))
                elif "," in raw and not raw.startswith("┊"):
                    csv_lines.append(raw)
            if csv_lines:
                content = "\n".join(csv_lines) + "\n"
                logger.info("[HARNESS] extracted CSV data: %s (%d rows)", path, len(csv_lines))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
        if not content.endswith("\n"):
            f.write("\n")
    return {"ok": True, "path": path, "files": [path], "content": content[:1000]}


def _path_tokens(path: str) -> list[str]:
    raw = str(path or "").strip()
    if not raw:
        return []
    raw = raw.replace("[", ".").replace("]", "")
    # Strip JSONPath root prefix ($)
    raw = raw.removeprefix("$.").removeprefix("$")
    return [part for part in raw.split(".") if part != ""]


def _extract_json_path(data: Any, path: str, default: Any = "") -> Any:
    current = data
    tokens = _path_tokens(path)
    for token in tokens:
        if token == "*":
            if isinstance(current, list):
                # If more tokens follow * (e.g. $.weather[*].date),
                # they are column-level paths, not rows_path
                # Return the list as-is so caller gets row objects
                return current
            return default
        if isinstance(current, dict):
            current = current.get(token, default)
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except Exception:
                return default
        else:
            # Can't descend further; mark as unmatched and let
            # suffix fallback try shorter paths below.
            current = default
            break
    # ── Fallback: if path tokens didn't match but a shorter suffix does ──
    # This handles transposed column-oriented data where keys lost their
    # parent prefix (e.g. column "daily.time" but row has key "time").
    if current is default or current == "":
        # Try matching from the last token backwards
        for end_idx in range(len(tokens) - 1, 0, -1):
            suffix_tokens = tokens[end_idx:]
            c = data
            match = True
            for t in suffix_tokens:
                if isinstance(c, dict):
                    c = c.get(t)
                    if c is None:
                        match = False
                        break
                elif isinstance(c, list):
                    try:
                        c = c[int(t)]
                    except Exception:
                        match = False
                        break
                else:
                    match = False
                    break
            if match and c is not None:
                return c
    return current


def _coerce_table_rows(data: Any, rows_path: str) -> list[Any]:
    rows = _extract_json_path(data, rows_path, default=data) if rows_path else data
    if isinstance(rows, list):
        return rows
    if isinstance(rows, dict):
        # ── Check if this is column-oriented data (Open-Meteo style) ──
        # Where the dict has array values of equal length (e.g. {"time": [...], "temp": [...]}),
        # transpose into row objects: [{time: X, temp: Y}, ...]
        array_fields = {k: v for k, v in rows.items() if isinstance(v, list) and len(v) > 0}
        if array_fields:
            lengths = [len(v) for v in array_fields.values()]
            if len(set(lengths)) == 1 and lengths[0] > 1:
                num_rows = lengths[0]
                transposed = []
                for i in range(num_rows):
                    row = {}
                    for k, v in array_fields.items():
                        row[k] = v[i]
                    transposed.append(row)
                return transposed
        # Fall back to extracting a single list from values
        list_values = [value for value in rows.values() if isinstance(value, list)]
        if len(list_values) == 1:
            return list_values[0]
        return [rows]
    # ── Fallback: try common JSON paths when no rows_path specified or it produced nothing ─
    if not rows or (isinstance(rows, list) and len(rows) == 0):
        if isinstance(data, dict):
            # Try common keys that might contain row data
            for key in ("results", "data", "items", "rows", "records", "entries", "list", "values", "weather", "daily", "forecast", "forecasts"):
                val = data.get(key)
                if isinstance(val, list) and len(val) > 0:
                    return val
                if isinstance(val, dict):
                    inner = _coerce_table_rows(val, "")
                    if inner:
                        return inner
            # Find the longest list value (likely the data array)
            best_val = None
            best_len = 0
            for val in data.values():
                if isinstance(val, list) and len(val) > best_len:
                    best_val = val
                    best_len = len(val)
            if best_val is not None:
                return best_val
            # Fallback: try first dict with array children
            # Where the dict has array values of equal length (e.g. {"time": [...], "temp": [...]}),
            # transpose them into row objects: [{time: X, temp: Y}, ...]
            array_fields = {k: v for k, v in data.items() if isinstance(v, list) and len(v) > 0}
            if array_fields:
                lengths = [len(v) for v in array_fields.values()]
                if len(set(lengths)) == 1 and lengths[0] > 0:
                    num_rows = lengths[0]
                    transposed = []
                    for i in range(num_rows):
                        row = {}
                        for k, v in array_fields.items():
                            row[k] = v[i]
                        transposed.append(row)
                    return transposed
        elif isinstance(data, list) and len(data) > 0:
            return data
    return []


def _scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(_scalar_text(item) for item in value if _scalar_text(item))
    if isinstance(value, dict):
        for key in ("value", "name", "description", "text", "desc"):
            if key in value:
                return _scalar_text(value.get(key))
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _clean_column_path(path: str) -> str:
    """Strip wildcard prefixes from column paths for row-level resolution.
    
    E.g. $.weather[*].date → date,  items[*].name → name
    """
    tokens = _path_tokens(path)
    if "*" in tokens:
        star_idx = tokens.index("*")
        after_star = tokens[star_idx + 1:]
        return ".".join(after_star) if after_star else ""
    return path


def _atomic_convert_md_to_pdf(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Convert a Markdown file to PDF using weasyprint."""
    import os
    import glob as _glob_mod
    # Limit OpenBLAS threads to prevent OOM in WSL/numpy-backed environments
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    source = str(params.get("source") or params.get("path") or "").strip()
    # Phase 1: explicit source parameter or dependency step search
    if not source:
        if ctx and ctx.task_instance:
            _work_dir = str(getattr(ctx.task_instance, "working_dir", "") or "")
            if _work_dir and os.path.isdir(_work_dir):
                _step_files = [f for f in os.listdir(_work_dir) if f.startswith("_step_") and f.endswith(".result.json")]
                for _sf in reversed(sorted(_step_files)):
                    try:
                        import json as _json_mod
                        with open(os.path.join(_work_dir, _sf), "r") as _sf_f:
                            _sf_data = _json_mod.load(_sf_f)
                        _result = _sf_data.get("result") or {}
                        _dep_files = _result.get("files") or []
                        if isinstance(_dep_files, str):
                            _dep_files = [_dep_files]
                        _dep_path = _result.get("path") or []
                        if isinstance(_dep_path, str):
                            _dep_path = [_dep_path]
                        _dep_files = list(_dep_files) + [p for p in _dep_path if p not in _dep_files]
                        for _df in _dep_files:
                            if isinstance(_df, str) and _df.endswith(".md"):
                                if os.path.exists(_df):
                                    source = _df
                                    break
                                _resolved = os.path.join(_work_dir, _df)
                                if not os.path.isabs(_df) and os.path.exists(_resolved):
                                    source = _resolved
                        if source:
                            break
                    except Exception:
                        continue
    # Phase 2: fallback — scan working dir for any .md files from prior steps
    if not source and ctx and ctx.task_instance:
        _work_dir = str(getattr(ctx.task_instance, "working_dir", "") or "")
        if _work_dir and os.path.isdir(_work_dir):
            _candidates = sorted(_glob_mod.glob(os.path.join(_work_dir, "*.md")))
            if _candidates:
                source = _candidates[-1]  # newest .md file
    if not source:
        return {"ok": False, "error": "missing source path (pass source or path)"}
    source_path = _safe_project_path(ctx, source)
    if not os.path.exists(source_path):
        return {"ok": False, "error": f"source file not found: {source_path}"}
    dest = str(params.get("dest") or params.get("output") or params.get("filename") or "").strip()
    if not dest:
        base = os.path.splitext(source_path)[0]
        dest = base + ".pdf"
    dest_path = _safe_project_path(ctx, dest)
    
    try:
        import markdown
        from weasyprint import HTML
        with open(source_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        html_content = markdown.markdown(md_content, extensions=["extra", "codehilite", "tables"])
        html_full = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@font-face {{
    font-family: 'CJK Fallback';
    src: local('WenQuanYi Zen Hei'), local('SimHei'), local('Noto Sans CJK SC'), local('Noto Sans SC');
    unicode-range: U+4E00-9FFF, U+3000-303F, U+FF00-FFEF;
}}
body {{ font-family: 'CJK Fallback', sans-serif; margin: 2cm; line-height: 1.6; }}
h1 {{ color: #333; border-bottom: 2px solid #4A90D9; }}
h2 {{ color: #555; }}
code {{ background: #f4f4f4; padding: 2px 5px; border-radius: 3px; }}
pre {{ background: #f8f8f8; padding: 10px; border-radius: 5px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background-color: #4A90D9; color: white; }}
img {{ max-width: 100%; height: auto; }}
</style></head><body>{html_content}</body></html>"""
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        HTML(string=html_full).write_pdf(dest_path)
        size = os.path.getsize(dest_path)
        return {
            "ok": True,
            "path": dest_path,
            "files": [dest_path],
            "content": f"PDF generated: {dest_path} ({size} bytes)",
            "artifact_content": f"PDF: {dest_path}",
            "findings": [f"PDF generated: {dest_path} ({size} bytes)"],
        }
    except Exception as exc:
        return {"ok": False, "error": f"PDF conversion failed: {exc}"}


def _atomic_json_table_artifact(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    output_path = str(
        params.get("path")
        or params.get("filename")
        or params.get("output_file")
        or params.get("output_filename")
        or params.get("output_path")
        or ctx.artifact_path
    )
    fmt = str(params.get("format") or params.get("output_format") or os.path.splitext(output_path)[1].lstrip(".") or "csv").lower()
    # Auto-correct: atomic_json_table_artifact is for structured data, prefer csv
    if fmt in ("md", "markdown", "txt"):
        explicit_format = params.get("format") or params.get("output_format")
        if not explicit_format:
            # Format was inferred from path extension — always default to csv
            fmt = "csv"
            base, _ = os.path.splitext(output_path)
            output_path = base + ".csv"
        else:
            # Format was explicitly set — check expected_artifacts for override
            task = ctx.task_instance
            if task and task.expected_artifacts:
                wants_csv = any(
                    ".csv" in str(item.get("pattern", ""))
                    for item in (task.expected_artifacts or [])
                    if isinstance(item, dict)
                )
                if wants_csv:
                    fmt = "csv"
                    base, _ = os.path.splitext(output_path)
                    output_path = base + ".csv"
    data = params.get("data", params.get("json"))
    # Fallback: if no data, try to read from dependency step files
    if not data and ctx and ctx.task_instance:
        import glob
        wd = getattr(ctx.task_instance, "working_dir", None)
        if wd:
            for fpath in sorted(glob.glob(os.path.join(wd, "_step_*.result.json"))):
                try:
                    with open(fpath, "r", encoding="utf-8") as fd:
                        sdata = json.load(fd)
                    sres = sdata.get("result") or {}
                    files_str = str(sres.get("files") or (sres.get("parsed") or {}).get("files") or "")
                    for line in files_str.split("\n"):
                        lp = line.strip().lstrip("- ").strip()
                        if lp.endswith((".csv", ".json")) and os.path.exists(lp):
                            with open(lp, "r", encoding="utf-8") as rf:
                                raw = rf.read()
                            if raw.strip():
                                try:
                                    parsed = json.loads(raw)
                                    if isinstance(parsed, (dict, list)):
                                        data = parsed
                                        break
                                except (json.JSONDecodeError, TypeError):
                                    # CSV content, try to use as lines
                                    lines = [l for l in raw.split("\n") if l.strip()]
                                    if len(lines) > 1:
                                        data = raw  # let CSV writer handle it
                                        break
                    if data:
                        break
                except Exception:
                    continue
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as exc:
            # If data is CSV text (not JSON), write it directly
            if fmt == "csv" and data.strip() and "," in data[:200]:
                content = data.strip() + "\n"
                path = _safe_project_path(ctx, output_path)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
                lines = [l for l in content.splitlines() if l.strip()]
                return {
                    "ok": True,
                    "path": path,
                    "files": [path],
                    "content": content[:1000],
                    "rows": max(0, len(lines) - 1),
                    "columns": [c.strip() for c in lines[0].split(",")] if lines else [],
                    "artifact_content": content[:1000],
                    "coerced_from_text": True,
                }
            if fmt in {"md", "markdown", "txt"} and data.strip():
                content = data.strip() + "\n"
                path = _safe_project_path(ctx, output_path)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
                return {
                    "ok": True,
                    "path": path,
                    "files": [path],
                    "content": content[:1000],
                    "rows": max(0, len([line for line in content.splitlines() if line.strip()]) - 2),
                    "columns": [],
                    "artifact_content": content[:1000],
                    "coerced_from_text": True,
                }
            return {"ok": False, "error": f"invalid JSON data: {exc}"}
    rows = _coerce_table_rows(data, str(params.get("rows_path") or ""))
    columns_raw = params.get("columns") or []
    if isinstance(columns_raw, dict):
        columns_raw = [{"header": key, "path": value} for key, value in columns_raw.items()]
    elif isinstance(columns_raw, list):
        columns_raw = [
            {"header": item, "path": item} if isinstance(item, str) else item
            for item in columns_raw
        ]
    if not isinstance(columns_raw, list):
        columns_raw = []
    # Auto-generate columns from row data when plan provides none or mismatched
    if not columns_raw and rows and isinstance(rows, list) and len(rows) > 0 and isinstance(rows[0], dict):
        columns_raw = [{"header": str(k), "path": str(k)} for k in rows[0]]
    if not columns_raw:
        return {"ok": False, "error": "missing table columns"}
    try:
        min_rows = max(0, int(params.get("min_rows") or params.get("minimum_rows") or 0))
    except Exception:
        min_rows = 0
    if min_rows and len(rows) < min_rows:
        return {
            "ok": False,
            "error": f"table completeness check failed: rows={len(rows)} min_rows={min_rows}",
            "rows": len(rows),
            "min_rows": min_rows,
        }
    columns: list[tuple[str, str, Any]] = []
    for item in columns_raw:
        if not isinstance(item, dict):
            continue
        header = str(item.get("header") or item.get("name") or item.get("label") or item.get("path") or item.get("key") or "").strip()
        path = str(item.get("path") or item.get("key") or item.get("field") or "").strip()
        default = item.get("default", "")
        if header and path:
            # Clean wildcard prefixes from column paths (e.g. $.weather[*].date → date)
            clean_path = _clean_column_path(path)
            columns.append((header, clean_path, default))
    if not columns:
        # Auto-generate columns from row data when plan columns don't match
        if rows and isinstance(rows, list) and len(rows) > 0:
            first_row = rows[0]
            if isinstance(first_row, dict):
                for key in first_row:
                    columns.append((str(key), str(key), ""))
        if not columns:
            return {"ok": False, "error": "no usable table columns"}
    # Verify specified column paths resolve to data in rows
    if rows and isinstance(rows, list) and len(rows) > 0 and isinstance(rows[0], dict):
        has_any_value = any(
            _extract_json_path(rows[0], path, "") not in ("", None)
            for _, path, _ in columns
        )
        if not has_any_value and len(columns) > 0:
            # None of the specified columns match data — auto-generate from keys
            columns = [(str(k), str(k), "") for k in rows[0]]
    if fmt in {"md", "markdown"}:
        headers = [header for header, _, _ in columns]
        table_lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]
        for row in rows:
            values = [_scalar_text(_extract_json_path(row, path, default)) for _, path, default in columns]
            table_lines.append("| " + " | ".join(value.replace("\n", " ") for value in values) + " |")
        content = "\n".join(table_lines) + "\n"
    else:
        from io import StringIO

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow([header for header, _, _ in columns])
        for row in rows:
            writer.writerow([_scalar_text(_extract_json_path(row, path, default)) for _, path, default in columns])
        content = buf.getvalue()
    path = _safe_project_path(ctx, output_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return {
        "ok": True,
        "path": path,
        "files": [path],
        "content": content[:1000],
        "rows": len(rows),
        "columns": [header for header, _, _ in columns],
        "artifact_content": content[:1000],
        "findings": [f"生成表格：{len(rows)} 行，{len(columns)} 列（{', '.join(header for header, _, _ in columns[:5])}{'…' if len(columns) > 5 else ''}）"],
    }


def _atomic_compose_structured_result(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    findings = params.get("findings")
    if isinstance(findings, str):
        findings = [findings]
    if not isinstance(findings, list):
        findings = []
    content = str(params.get("content") or params.get("message") or params.get("artifact_content") or "").strip()
    files_value = params.get("files")
    if not files_value:
        files_value = params.get("artifacts") or params.get("file_paths")
    if isinstance(files_value, (list, tuple, set)):
        files_text = "; ".join(str(x) for x in files_value if str(x or "").strip())
    else:
        files_text = str(files_value or "").strip()
    parsed = {
        "action": str(params.get("action") or ctx.event.type.value),
        "step_done": str(params.get("step_done") or "Harness 本地事件已完成"),
        "findings": [str(x) for x in findings if str(x).strip()] or ["本轮由 Harness 本地步骤完成"],
        "evidence": str(params.get("evidence") or "system:harness_atomic"),
        "next_action": str(params.get("next_action") or "如目标未完成，继续生成下一轮微计划。"),
        "state_delta": str(params.get("state_delta") or f"harness atomic result for {ctx.event.type.value}"),
        "files": files_text or "EMPTY",
        "artifact_content": content or "EMPTY",
    }
    return {"ok": True, "parsed": parsed, "content": content}


# ── Smart handlers ──────────────────────────────────────────────────


def _collect_hermes_artifacts(ctx: HarnessContext) -> list[str]:
    """Copy generated artifacts (images, scripts) from Hermes working dir to task dir."""
    copied = []
    try:
        import shutil
        hermes_cwd = os.path.join(ctx.workspace, "system", "hermes_work")
        if not os.path.isdir(hermes_cwd):
            return copied
        task_dir = os.path.dirname(ctx.artifact_path) if ctx.artifact_path else ""
        if not task_dir or not os.path.isdir(task_dir):
            return copied
        for fname in os.listdir(hermes_cwd):
            if fname.startswith(".") or fname in ("batch_plan_result.md",):
                continue
            src = os.path.join(hermes_cwd, fname)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(task_dir, fname)
            if os.path.exists(dst):
                continue
            # Only copy relevant file types
            ext = os.path.splitext(fname)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".py", ".json", ".csv", ".txt", ".md"):
                shutil.copy2(src, dst)
                copied.append(dst)
    except Exception as exc:
        logger.warning("[COLLECT_HERMES_ARTIFACTS] failed: %s", exc)
    return copied


async def _smart_llm_structured_action(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    if not ctx.adapter:
        return {"ok": False, "error": "missing adapter"}
    if not ctx.build_action_prompt or not ctx.parse_structured_response:
        return {"ok": False, "error": "missing action prompt/parser"}
    prompt = ctx.build_action_prompt(ctx.event, ctx.title, ctx.state_md, ctx.artifact_path)
    # Extract the actual step instruction from parameters
    step_prompt = (params.get("prompt") or params.get("instruction") or "").strip()
    if step_prompt:
        # When the step has an explicit prompt/instruction, use it as the TASK,
        # not as "completed results". Prepend it as the primary directive.
        prompt = (
            "## 当前步骤的具体任务（必须执行，不要只做分析报告）\n\n"
            + step_prompt
            + "\n\n## Partner 执行上下文（供参考，不要覆盖上面的任务指令）\n\n"
            + prompt
        )
    else:
        # No step prompt — fall back to passing params as reference context
        extra_limit = 12000 if str(params.get("report_mode") or "").strip() == "final_pdf_report" else 6000
        extra = json.dumps(params, ensure_ascii=False)[:extra_limit]
        if extra and extra != "{}":
            prompt += "\n\nHarness 已完成的本地步骤结果（只读参考，不要重复执行）：\n" + extra
    if ctx.robust_executor and ctx.task_instance:
        robust = await ctx.robust_executor.execute(
            event_name="smart_llm_structured_action",
            task_instance=ctx.task_instance,
            operation=lambda: ctx.adapter.chat(prompt, purpose="action"),
            on_timeout="generate_placeholder",
            on_failure="generate_placeholder",
        )
        if robust.status == "fallback_success":
            content = str((robust.value or {}).get("content") or robust.content_preview or "")
            return {
                "ok": True,
                "status": "fallback_success",
                "content": content,
                "fallback_path": robust.fallback_path,
                "files": [robust.fallback_path] if robust.fallback_path else [],
                "is_fallback": True,
                "original_error": robust.original_error or robust.error,
                "parsed": {
                    "action": ctx.event.type.value,
                    "step_done": "外部 LLM 调用超时，已读取 fallback 草案继续执行",
                    "findings": ["fallback 文件已作为当前事件输出使用"],
                    "evidence": robust.fallback_path or "system:fallback",
                    "next_action": "基于 fallback 内容继续后续步骤；必要时在最终结果中标注不完整。",
                    "state_delta": f"fallback_success event={ctx.event.type.value}",
                    "files": robust.fallback_path or "EMPTY",
                    "artifact_content": content[:1200] or "EMPTY",
                },
            }
        if not robust.ok:
            return {
                "ok": False,
                "status": robust.status,
                "error": robust.error,
                "fallback_path": robust.fallback_path,
                "files": [robust.fallback_path] if robust.fallback_path else [],
                "content": "",
            }
        raw = robust.value
    else:
        raw = await asyncio.to_thread(ctx.adapter.chat, prompt, purpose="action")
    # Copy generated files (images, scripts) from Hermes working directory to task directory
    copied_files = _collect_hermes_artifacts(ctx)
    parsed = ctx.parse_structured_response(raw or "")
    if not parsed:
        # LLM returned content that can't be parsed as structured JSON.
        # If the raw output has meaningful content, treat it as a success
        # so the pipeline shows "completed" instead of "failed".
        raw_clipped = _clip(raw, 1000)
        if raw and len(raw.strip()) > 50:
            return {"ok": True, "content": raw_clipped, "parsed": {},
                    "_unparsed": True, "warning": "LLM 返回了非结构化文本，已按原始内容处理"}
        return {"ok": False, "error": "LLM returned no structured result", "content": raw_clipped}
    result = {"ok": True, "parsed": parsed, "content": _clip(raw, 1200)}
    # If the LLM returned structured output with artifact_content,
    # use that as the primary content instead of the raw JSON diff.
    # This ensures downstream steps ($step_X.result.content) get the
    # actual report text, not the JSON/diff wrapper.
    if isinstance(parsed, dict):
        art = parsed.get("artifact_content") or ""
        step_done = parsed.get("step_done") or ""
        if art and len(art) > 100:
            result["content"] = art[:12000]
        elif step_done and len(step_done) > 50:
            result["content"] = step_done[:12000]
    # If the action step reports file_not_found, treat it as a hard failure
    # so the pipeline stops and reports the missing file to the user.
    action = str(parsed.get("action") or "").strip().lower()
    if action == "file_not_found":
        missing = str(parsed.get("step_done") or parsed.get("findings") or "").strip()[:200]
        logger.warning("[HARNESS] smart_llm action reported file_not_found: %s", missing or parsed)
        return {"ok": False, "error": f"file_not_found: {missing}" if missing else "file_not_found: missing input file"}
    if copied_files:
        result["files"] = copied_files
    return result


async def _summarize_search_handler(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Harness event handler: summarize arbitrary search result text into structured JSON.

    Supports degraded mode: if _partial_deps is set, the handler only processes
    input that is available and ignores missing dependencies gracefully.
    """
    # Check for partial deps mode
    partial_deps = params.get("_partial_deps")
    if partial_deps:
        logger.warning("[SUMMARIZE] partial deps mode: missing %s; processing available data only",
                       ", ".join(str(d) for d in partial_deps))

    # Try multiple parameter names for the raw text input
    raw_text = str(params.get("raw_text") or params.get("content") or "").strip()
    # Also check for _available_data dict (passed by improved harness when deps fail)
    available = params.get("_available_data")
    if isinstance(available, dict):
        # Collect all available step results and concatenate their text
        texts = []
        for dep_id, dep_result in available.items():
            dep_content = str(dep_result.get("content") or dep_result.get("json") or "")[:8000]
            if dep_content.strip() and dep_content != "null":
                texts.append(dep_content)
        if texts:
            raw_text = "\n\n".join(texts)
        elif not raw_text:
            return {"ok": False, "error": "no input text provided; pass raw_text or content in parameters", "total": 0, "papers": []}

    if not raw_text:
        return {"ok": False, "error": "no input text provided; pass raw_text or content in parameters", "total": 0, "papers": []}

    summarizer_config = ctx.config.get("summarizer", {}) if isinstance(ctx.config, dict) else {}
    result = await summarize_search_results(
        raw_text=raw_text,
        workspace=ctx.workspace,
        adapter=ctx.adapter,
        task_instance=ctx.task_instance,
        config=summarizer_config,
        max_input_chars=int(params.get("max_input_chars", 8000)),
    )
    return {"ok": True, **result}


# ── Event execution method configuration loading ──

_EVENT_EXECUTION_CONFIG: dict[str, str] | None = None

def _load_event_execution_config() -> dict[str, str]:
    """Load event execution method configuration from file.
    
    Maps event names to execution methods: "agent", "llm", "local".
    Fallback: all events are "local" if config file is missing.
    Loaded once and cached.
    """
    global _EVENT_EXECUTION_CONFIG
    if _EVENT_EXECUTION_CONFIG is not None:
        return _EVENT_EXECUTION_CONFIG

    config: dict[str, str] = {}
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "global_config", "event_execution.yaml"),
        os.path.join(os.path.expanduser("~"), ".partner", "event_execution.yaml"),
        os.path.join(os.path.expanduser("~"), ".partner", "config", "event_execution.yaml"),
    ]
    loaded = False
    for path in candidates:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            try:
                import yaml
                with open(abs_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                raw = data.get("events", {})
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if isinstance(v, str) and v in ("agent", "llm", "local"):
                            config[k] = v
                    loaded = True
                    logger.info("[HARNESS] loaded event execution config from %s (%d events)", abs_path, len(config))
                    break
            except Exception as exc:
                logger.debug("[HARNESS] failed to load event config from %s: %s", abs_path, exc)

    if not loaded:
        logger.info("[HARNESS] no event_execution.yaml found, all events default to local")
    _EVENT_EXECUTION_CONFIG = config
    return config


def _get_event_execution_method(event_name: str) -> str:
    """Get the execution method for an event, defaulting to 'local'."""
    config = _load_event_execution_config()
    return config.get(event_name, "local")


# ── Generic LLM event handler ──

async def _llm_event_handler(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Generic handler for LLM-driven events (extract, if_condition, validate, etc.).
    
    Calls adapter.chat() with a structured prompt based on the event name and params.
    Uses build_action_prompt if available, otherwise constructs a minimal prompt.
    """
    if not ctx.adapter:
        return {"ok": False, "error": "missing adapter"}

    event_name = getattr(ctx.event, "type", None)
    event_name = str(event_name.value if hasattr(event_name, "value") else event_name)

    # Build the instruction from params
    instruction = str(params.get("prompt") or params.get("instruction") or params.get("task") or "").strip()
    if not instruction:
        instruction = f"执行 {event_name} 操作，基于提供的参数：{json.dumps(params, ensure_ascii=False)[:2000]}"
    
    # Use action prompt builder if available
    if ctx.build_action_prompt:
        base_prompt = ctx.build_action_prompt(ctx.event, ctx.title, ctx.state_md, ctx.artifact_path)
        prompt = f"## 当前步骤指令\n\n{instruction}\n\n## 执行上下文\n\n{base_prompt}"
    else:
        prompt = f"## 指令\n\n{instruction}\n\n## 参数\n\n{json.dumps(params, ensure_ascii=False)[:3000]}"

    # Add output format guidance
    if event_name in ("extract",):
        prompt += "\n\n请以JSON结构化格式输出提取结果。"
    elif event_name in ("if_condition", "switch"):
        prompt += "\n\n请输出你的判断决策（只输出判断结果，不要额外解释）。"
    elif event_name in ("validate", "check_quality"):
        prompt += "\n\n请输出验证/检查结果。格式：{\"passed\": true/false, \"issues\": [\"...\"], \"summary\": \"...\"}"
    elif event_name in ("audit",):
        prompt += "\n\n请输出审计报告。格式：{\"passed\": true/false, \"findings\": [...], \"recommendations\": [...]}"
    elif event_name in ("compare",):
        prompt += "\n\n请输出对比分析结果。格式：{\"differences\": [...], \"similarities\": [...], \"summary\": \"...\"}"

    try:
        if ctx.robust_executor and ctx.task_instance:
            robust = await ctx.robust_executor.execute(
                event_name=event_name,
                task_instance=ctx.task_instance,
                operation=lambda: ctx.adapter.chat(prompt, purpose="action"),
                on_timeout="generate_placeholder",
                on_failure="generate_placeholder",
            )
            if robust.status == "fallback_success":
                return {
                    "ok": True,
                    "status": "fallback_success",
                    "content": str((robust.value or {}).get("content") or robust.content_preview or ""),
                    "fallback_path": robust.fallback_path,
                    "is_fallback": True,
                }
            if not robust.ok:
                return {"ok": False, "status": robust.status, "error": robust.error, "content": ""}
            raw = robust.value
        else:
            raw = await asyncio.to_thread(ctx.adapter.chat, prompt, purpose="action")

        parsed = ctx.parse_structured_response(raw or "") if ctx.parse_structured_response else None
        result = {"ok": True, "content": _clip(raw, 3000)}
        if parsed:
            result["parsed"] = parsed
        if event_name in ("if_condition", "switch"):
            decision = (raw or "").strip().lower()
            result["decision"] = decision
        return result
    except Exception as exc:
        logger.warning("[HARNESS] llm_event_handler %s failed: %s", event_name, exc)
        return {"ok": False, "error": str(exc)}


# ── Generic Agent event handler ──

async def _agent_event_handler(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Generic handler for Agent-executed events (web_search, summarize, generate_text, etc.).
    
    Forwards the task to an external Agent via AgentDispatcher.
    Uses the event name to select the best agent via auto-selection.
    """
    event_name = str(getattr(ctx.event, "type", "") or "")
    if hasattr(event_name, "value"):
        event_name = event_name.value

    # Map event to recommended agent type
    event_to_capability = {
        "web_search": ["web_research", "general_purpose", "research"],
        "summarize": ["research", "data_analysis", "general_purpose"],
        "analyze": ["data_analysis", "reasoning", "general_purpose"],
        "generate_text": ["general_purpose", "reasoning"],
        "generate_code": ["coding", "code_generation"],
        "write_report": ["general_purpose", "data_analysis", "research"],
        "create_diagram": ["data_visualization", "general_purpose"],
    }

    task = str(params.get("task") or params.get("query") or params.get("instruction") or params.get("prompt") or "").strip()
    if not task:
        task = str(getattr(ctx.event, "title", "") or ctx.title or "").strip()
    if not task:
        return {"ok": False, "error": f"missing task for {event_name}"}

    # Try auto-selection: find best agent for this event's capabilities
    preferred_agent = params.get("agent", "")
    if preferred_agent:
        agent_name = str(preferred_agent).strip().lower()
    else:
        try:
            from ..agents.registry import AgentRegistry
            from ..agents.dispatcher import AgentDispatcher, AgentTask

            registry = AgentRegistry(workspace=ctx.workspace)
            selected_agent = None
            capabilities = event_to_capability.get(event_name, ["general_purpose"])
            for cap in capabilities:
                matches = registry.find_by_capability(cap)
                if matches:
                    selected_agent = matches[0].name
                    break
            if not selected_agent:
                agents = registry.list_agents()
                if agents:
                    selected_agent = agents[0].name
            agent_name = selected_agent or "hermes"
        except Exception as exc:
            logger.debug("[HARNESS] agent auto-selection failed, using default: %s", exc)
            agent_name = "hermes"

    # Forward remaining parameters as agent_params
    agent_params = {k: v for k, v in params.items() if k not in ("agent", "task", "query", "user_request", "allow_web", "prompt", "instruction")}

    try:
        from ..skills.external_agent_skills import execute_agent_task
        result = await execute_agent_task(
            workspace=ctx.workspace,
            agent=agent_name,
            task=task,
            task_instance=ctx.task_instance,
            allow_web=bool(params.get("allow_web", event_name == "web_search")),
            agent_params=agent_params,
        )

        if not result.ok:
            # ── web_search failure: record habit + auto-fallback to generate_code ──
            if event_name == "web_search":
                # Record learning habit so future planner prompts avoid web_search
                try:
                    from ..meta.learning import update_habits
                    update_habits({"avoid_web_search": True})
                    logger.info("[HARNESS] recorded avoid_web_search habit after web_search failure")
                except Exception as _hl:
                    logger.debug("[HARNESS] failed to update habit: %s", _hl)
                # Scheme C: auto-fallback to generate_code
                logger.info("[HARNESS] web_search failed, falling back to generate_code for: %s", task[:80])
                event_name = "generate_code"
                task = "写一个 Python 脚本完成以下任务（不要搜索，直接编程）：\n" + task
                agent_params["prompt"] = task
                try:
                    from ..skills.external_agent_skills import execute_agent_task
                    result = await execute_agent_task(
                        workspace=ctx.workspace,
                        agent=agent_name,
                        task=task,
                        task_instance=ctx.task_instance,
                        allow_web=False,
                        agent_params=agent_params,
                    )
                except Exception as _fe:
                    logger.warning("[HARNESS] generate_code fallback also failed: %s", _fe)
                    return {"ok": False, "skill": agent_name,
                            "error": f"web_search failed, generate_code fallback also failed: {_fe}"}
                if not result.ok:
                    return {"ok": False, "skill": agent_name,
                            "error": f"web_search failed, generate_code fallback: {result.error}"}
                # Fall through to generate_code handling below
            else:
                return {"ok": False, "skill": agent_name, "error": result.error or "agent returned no result"}
        output = result.output or {}
        content = output.get("content") or ""

        # ── generate_code: auto-write returned code to a file ──
        # The planner may generate a plan like: generate_code → run_command(path/to/file.py)
        # But generate_code only returns code as text — nobody writes it to disk.
        # We extract the code (+ strip diff/prefix markers) and write a .py file
        # so run_command can actually execute it.
        _written_files = []
        if event_name == "generate_code" and content:
            _task_dir = getattr(ctx.task_instance, "working_dir", None) if ctx.task_instance else None
            if _task_dir and os.path.isdir(_task_dir):
                # Try to extract filename from diff header like "a/name.py → b/name.py"
                _fname_match = re.search(r"→\s*b/([\w.-]+\.py)", content)
                _fname = _fname_match.group(1) if _fname_match else "generated_code.py"
                _fpath = os.path.join(_task_dir, _fname)
                # Strip [agent] header and diff markers, keep actual code lines
                _code_lines = []
                _in_code = False
                for _line in content.split("\n"):
                    # Skip [hermes] header
                    if _line.startswith("[") and "]" in _line[:20]:
                        continue
                    # Skip diff review header lines
                    if _line.startswith("┊") or _line.startswith("diff ") or _line.startswith("---") or _line.startswith("+++"):
                        continue
                    if _line.startswith("@@"):
                        _in_code = True
                        continue
                    if _in_code:
                        # Strip leading '+' or leave as-is for context lines
                        if _line.startswith("+"):
                            _code_lines.append(_line[1:])
                        elif _line.startswith(" "):
                            _code_lines.append(_line[1:])  # context line
                        # Skip lines starting with '-' (removed lines)
                if _code_lines:
                    _code_text = "\n".join(_code_lines)
                    if _code_text.strip():
                        try:
                            with open(_fpath, "w", encoding="utf-8") as _f:
                                _f.write(_code_text)
                            logger.info("[HARNESS] wrote generated code to %s (%d bytes)", _fpath, len(_code_text))
                            ctx.task_instance.append_log("code_written", {
                                "path": _fpath,
                                "size": len(_code_text),
                            })
                            _written_files.append(_fpath)
                        except Exception as _exc:
                            logger.warning("[HARNESS] failed to write generated code: %s", _exc)

        result_json = {"content": str(content)[:8000], "json": output}
        if _written_files:
            result_json["files"] = _written_files
            result_json["path"] = _written_files[0]
        return result_json
    except Exception as exc:
        logger.warning("[HARNESS] agent_event_handler failed event=%s agent=%s error=%s", event_name, agent_name, exc)
        return {"ok": False, "error": str(exc)}


# ── New local event handlers ──


def _local_read_file(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Read a local file. Parameters: path, offset, limit."""
    path = str(params.get("path") or params.get("filename") or "").strip()
    if not path:
        return {"ok": False, "error": "missing path"}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"ok": True, "content": content, "size": len(content)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _local_query_api(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Call an arbitrary HTTP API. Parameters: url, method, headers, body, timeout."""
    url = str(params.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "missing url"}
    import urllib.request
    method = str(params.get("method") or "GET").upper()
    headers = params.get("headers") if isinstance(params.get("headers"), dict) else {}
    timeout = int(params.get("timeout") or 20)
    try:
        req = urllib.request.Request(url, method=method, headers={
            "User-Agent": "Mozilla/5.0 Partner/0.7", **headers
        })
        body = params.get("body")
        if body and method in ("POST", "PUT", "PATCH"):
            import json as _json
            data = _json.dumps(body).encode("utf-8") if isinstance(body, dict) else str(body).encode("utf-8")
            req.data = data
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        parsed = None
        try:
            import json as _json
            parsed = _json.loads(raw)
        except Exception:
            pass
        return {"ok": True, "content": raw, "json": parsed, "status": resp.getcode()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _local_transform(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Transform data between formats. Parameters: input_format, output_format, data, path."""
    import csv, io, json as _json
    data = params.get("data")
    src = str(params.get("input_format") or "").strip().lower()
    dst = str(params.get("output_format") or "").strip().lower()
    path = str(params.get("path") or "").strip()
    if path and not data:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as exc:
            return {"ok": False, "error": f"cannot read {path}: {exc}"}
        ext = os.path.splitext(path)[1].lower()
        if not src:
            src = {"json": "json", "csv": "csv", "md": "markdown", "html": "html", "yaml": "yaml", "yml": "yaml", "xml": "xml", "toml": "toml"}.get(ext, "text")
        if not data:
            data = raw
    if data is None:
        return {"ok": False, "error": "missing data or path"}
    if not dst:
        dst = src  # identity

    try:
        if src == "json_literal" and isinstance(data, (dict, list)):
            parsed = data
        elif src == "json":
            parsed = _json.loads(str(data)) if isinstance(data, str) else data
        elif src == "csv":
            reader = csv.DictReader(io.StringIO(str(data)))
            parsed = list(reader)
        elif src in ("yaml", "yml"):
            import yaml as _yaml
            parsed = _yaml.safe_load(str(data))
        else:
            parsed = str(data)

        if dst == "json":
            result = _json.dumps(parsed, indent=2, ensure_ascii=False)
        elif dst == "csv":
            if isinstance(parsed, list) and parsed:
                out = io.StringIO()
                writer = csv.DictWriter(out, fieldnames=parsed[0].keys())
                writer.writeheader()
                writer.writerows(parsed)
                result = out.getvalue()
            else:
                result = str(data)
        elif dst == "markdown":
            if isinstance(parsed, list) and parsed:
                import re as _re
                header = "| " + " | ".join(parsed[0].keys()) + " |"
                sep = "| " + " | ".join("---" for _ in parsed[0].keys()) + " |"
                rows = ["| " + " | ".join(str(r.get(k, "")) for k in parsed[0].keys()) + " |" for r in parsed]
                result = header + "\n" + sep + "\n" + "\n".join(rows)
            else:
                result = str(data)
        elif dst == "text":
            result = str(data) if isinstance(data, str) else _json.dumps(data, indent=2, ensure_ascii=False)
        else:
            result = str(data)
        return {"ok": True, "content": result, "format": dst}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _local_filter(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Filter data by condition. Parameters: data, field, operator, value.
    Operators: eq, ne, gt, lt, contains, regex
    """
    data = params.get("data")
    if not data:
        return {"ok": False, "error": "missing data"}
    field = str(params.get("field") or "").strip()
    op = str(params.get("operator") or "eq").strip().lower()
    value = params.get("value")
    if not data:
        return {"ok": False, "error": "missing data"}
    if isinstance(data, str):
        try:
            import json as _json
            data = _json.loads(data)
        except Exception:
            return {"ok": False, "error": "data is not valid JSON"}
    items = data if isinstance(data, list) else [data]
    filtered = []
    import re as _re
    for item in items:
        if isinstance(item, dict) and field:
            actual = item.get(field)
        else:
            actual = item
        match = False
        if op == "eq":
            match = actual == value
        elif op == "ne":
            match = actual != value
        elif op == "gt":
            try:
                match = float(actual) > float(value)
            except (ValueError, TypeError):
                match = str(actual) > str(value)
        elif op == "lt":
            try:
                match = float(actual) < float(value)
            except (ValueError, TypeError):
                match = str(actual) < str(value)
        elif op == "contains":
            match = str(value).lower() in str(actual).lower()
        elif op == "regex":
            match = bool(_re.search(str(value), str(actual)))
        if match:
            filtered.append(item)
    return {"ok": True, "filtered": filtered, "count": len(filtered), "total": len(items)}


def _local_sort(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Sort data. Parameters: data, key, reverse, field."""
    data = params.get("data")
    if not data:
        return {"ok": False, "error": "missing data"}
    key = str(params.get("key") or params.get("field") or "").strip()
    reverse = bool(params.get("reverse", False))
    if isinstance(data, str):
        try:
            import json as _json
            data = _json.loads(data)
        except Exception:
            return {"ok": False, "error": "data is not valid JSON"}
    items = list(data) if isinstance(data, list) else [data]
    try:
        if key:
            items.sort(key=lambda x: str(x.get(key, "") if isinstance(x, dict) else x), reverse=reverse)
        else:
            items.sort(reverse=reverse)
        return {"ok": True, "sorted": items, "count": len(items)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _local_download_file(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Download a file from URL. Parameters: url, destination."""
    url = str(params.get("url") or "").strip()
    dest = str(params.get("destination") or params.get("dest") or params.get("path") or "").strip()
    if not url:
        return {"ok": False, "error": "missing url"}
    import urllib.request
    timeout = int(params.get("timeout") or 60)
    try:
        if not dest:
            dest = os.path.basename(url.split("?")[0]) or "downloaded_file"
            dest = os.path.join(ctx.working_dir, dest)
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        urllib.request.urlretrieve(url, dest)
        size = os.path.getsize(dest) if os.path.exists(dest) else 0
        return {"ok": True, "path": dest, "size": size}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _local_create_file(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Create a file with content. Parameters: path, content, format.
    Replaces atomic_write_artifact and atomic_json_table_artifact.
    """
    path = str(params.get("path") or params.get("filename") or params.get("output") or "").strip()
    content = params.get("content")
    if not path and not content:
        return {"ok": False, "error": "missing path or content"}

    # If no path specified, derive from format/context
    if not path:
        path = os.path.join(ctx.working_dir or ctx.project_dir or ".", "output.txt")
    # Resolve relative paths against task working directory
    if not os.path.isabs(path) and ctx.task_instance:
        _work_dir = getattr(ctx.task_instance, "working_dir", None) or getattr(ctx, "working_dir", None)
        if _work_dir:
            path = os.path.join(_work_dir, path)

    # Resolve content template variables
    if isinstance(content, str) and "$step_" in content and ctx.task_instance:
        from ..harness_core.task_instance import TaskInstance
        content = _resolve_step_variables(content, ctx.task_instance)

    # Handle JSON data → table conversion (previously atomic_json_table_artifact)
    fmt = str(params.get("format") or os.path.splitext(path)[1].lower() or "").strip()
    json_data = params.get("json_data") or params.get("data")
    rows_path = params.get("rows_path") or params.get("data_path")
    if json_data and not content:
        try:
            import json as _json
            parsed = json_data if isinstance(json_data, (dict, list)) else _json.loads(str(json_data))
            if rows_path:
                for part in str(rows_path).strip(".").split("."):
                    if isinstance(parsed, dict):
                        parsed = parsed.get(part, {})
                    elif isinstance(parsed, list) and part.lstrip("-").isdigit():
                        parsed = parsed[int(part)]
            columns = params.get("columns")
            if isinstance(parsed, list) and parsed:
                if fmt in (".csv", "csv"):
                    import csv, io
                    out = io.StringIO()
                    keys = columns if isinstance(columns, list) else (
                        list(columns.values()) if isinstance(columns, dict)
                        else list(parsed[0].keys()) if isinstance(parsed[0], dict)
                        else [f"col{i}" for i in range(len(parsed[0]))] if isinstance(parsed[0], (list, tuple))
                        else [])
                    writer = csv.writer(out)
                    writer.writerow(keys)
                    for row in parsed:
                        writer.writerow([str(row.get(k, "")) if isinstance(row, dict) else str(row) for k in keys])
                    content = out.getvalue()
                    if not path.endswith(".csv"):
                        path += ".csv"
                else:
                    header = "| " + " | ".join(columns if isinstance(columns, list) else (list(columns.values()) if isinstance(columns, dict) else list(parsed[0].keys()))) + " |"
                    sep = "| " + " | ".join("---" for _ in (columns if isinstance(columns, list) else (list(columns.values()) if isinstance(columns, dict) else list(parsed[0].keys())))) + " |"
                    rows = []
                    for row in parsed:
                        if isinstance(row, dict):
                            keys = columns if isinstance(columns, list) else list(columns.values()) if isinstance(columns, dict) else list(row.keys())
                            rows.append("| " + " | ".join(str(row.get(k, "")) for k in keys) + " |")
                        else:
                            rows.append(f"| {row} |")
                    content = header + "\n" + sep + "\n" + "\n".join(rows)
        except Exception as exc:
            return {"ok": False, "error": f"json table conversion failed: {exc}"}

    if content is None:
        content = ""
    content_str = str(content)

    # ── Python file: extract clean code from Markdown-wrapped output ──
    # LLMs often return Python code embedded in Markdown (with Chinese comments,
    # bullet lists, code fences). `create_file` writes this as-is, causing
    # SyntaxError when `run_command` tries to execute it.
    _ext = os.path.splitext(path)[1].lower()
    if _ext == ".py" and content_str.strip():
        # 1) Try to extract from Markdown code fence ```python ... ```
        _fence_match = re.search(
            r"```(?:python)?\s*\n(.*?)```",
            content_str, re.DOTALL
        )
        if _fence_match:
            _extracted = _fence_match.group(1).strip()
            if _extracted:
                content_str = _extracted
        # 2) Validate with ast.parse; if it fails, try heuristic cleanup
        try:
            import ast as _ast_mod
            _ast_mod.parse(content_str)
        except SyntaxError:
            # Heuristic: strip leading non-code lines (start with '- ', '# ', or are markdown headers)
            _lines = content_str.split("\n")
            _code_start = 0
            for _i, _l in enumerate(_lines):
                _stripped = _l.strip()
                # Skip blank lines, markdown bullets, headers, and non-code descriptions
                if _stripped and not _stripped.startswith(("#!", "import", "from", "def ", "class ", "@", "if ", "for ", "while ", "try:", "with ", "print", "return", "pass", "break", "continue", "raise", "yield", "assert", "del ", "global", "nonlocal", '"', "'", "self.", "self ", "result", "output", "data", "url", "response", "species", "taxon", "occurrence")):
                    continue
                _code_start = _i
                break
            if _code_start > 0:
                content_str = "\n".join(_lines[_code_start:])
            # Try to validate again; if still fails, log warning but keep content
            try:
                _ast_mod.parse(content_str)
            except SyntaxError as _se:
                logger.warning("[HARNESS] Python file %s has syntax error after cleanup: %s", path, _se)

    # Create file
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content_str)
        return {"ok": True, "path": path, "size": len(content_str), "format": fmt or os.path.splitext(path)[1].lstrip(".") or "text"}
    except Exception as exc:
        return {"ok": False, "error": f"create_file failed: {exc}"}


def _local_run_command(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """Run a system command. Parameters: command, timeout, workdir."""
    cmd = str(params.get("command") or params.get("cmd") or "").strip()
    if not cmd:
        return {"ok": False, "error": "missing command"}
    timeout_sec = int(params.get("timeout") or 120)
    workdir = str(params.get("workdir") or "").strip() or ctx.working_dir
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout_sec, cwd=workdir or None,
        )
        return {
            "ok": r.returncode == 0,
            "stdout": str(r.stdout or "")[:8000],
            "stderr": str(r.stderr or "")[:2000],
            "exit_code": r.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"command timed out after {timeout_sec}s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _local_list_directory(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """List directory contents. Parameters: path, pattern."""
    path = str(params.get("path") or params.get("directory") or "").strip() or ctx.working_dir
    pattern = str(params.get("pattern") or "*").strip()
    try:
        import glob
        full_pattern = os.path.join(path, pattern)
        files = sorted(glob.glob(full_pattern))
        items = []
        for f in files:
            stat = os.stat(f)
            items.append({
                "name": os.path.basename(f),
                "path": f,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "is_dir": os.path.isdir(f),
            })
        return {"ok": True, "items": items, "count": len(items)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


_DEFAULT_REGISTRY: EventRegistry | None = None


def default_registry() -> EventRegistry:
    """Register all 29 harness events with the new generic event system.
    
    Events are categorized by execution_method:
    - "agent": forwarded to external Agent via AgentDispatcher
    - "llm": processed by LLM via adapter.chat()
    - "local": executed by deterministic local handlers
    
    The execution method is driven by event_execution.yaml config.
    Old events are kept for backward compat but marked consistently.
    """
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is not None:
        return _DEFAULT_REGISTRY
    registry = EventRegistry()

    # ── Legacy events (backward compat) ──
    registry.register(HarnessEventSpec("atomic_read_state", "atomic", "读取当前项目 state.md。", _atomic_read_state, reads_existing_artifact=True, execution_method="local"))
    registry.register(HarnessEventSpec("atomic_list_project_files", "atomic", "列出项目目录最近文件。", _atomic_list_project_files, reads_existing_artifact=True, execution_method="local"))
    registry.register(HarnessEventSpec("atomic_inspect_file", "atomic", "读取工作区内一个文件的文本、大小和前 64 字节 hex。", _atomic_inspect_file, reads_existing_artifact=True, execution_method="local"))
    registry.register(HarnessEventSpec("atomic_ollama_status", "atomic", "探测 Ollama 池状态。", _atomic_ollama_status, execution_method="local"))
    registry.register(HarnessEventSpec("atomic_http_get", "atomic", "HTTP GET 请求获取外部数据（天气、汇率、股价等）。", _atomic_http_get, external_call=True, execution_method="local"))
    registry.register(HarnessEventSpec("call_agent_skill", "atomic", "Forward task to an external Agent. Default: hermes.", _atomic_execute_skill, external_call=True, execution_method="agent"))
    registry.register(HarnessEventSpec("atomic_ensure_agent_installed", "atomic", "检查并安装专用 Agent。参数: agent (必需), force_reinstall (可选, 默认 false)。在 call_agent_skill 之前使用。", _atomic_ensure_agent_installed, external_call=True, execution_method="local"))
    registry.register(HarnessEventSpec("atomic_write_artifact", "atomic", "写入工作区内的 artifact 文件。（保留旧名，建议使用 create_file）", _atomic_write_artifact, produces_artifact=True, execution_method="local"))
    registry.register(HarnessEventSpec("atomic_compose_structured_result", "atomic", "把确定性结果拼成 Partner 结构化结果。", _atomic_compose_structured_result, execution_method="local"))
    registry.register(HarnessEventSpec("atomic_convert_md_to_pdf", "atomic", "将 Markdown 文件转换为 PDF。", _atomic_convert_md_to_pdf, produces_artifact=True, execution_method="local"))
    registry.register(HarnessEventSpec("smart_llm_structured_action", "smart", "执行 LLM 驱动的任务步骤（绘图、内容生成、分析等）。", _smart_llm_structured_action, external_call=True, execution_method="llm"))
    registry.register(HarnessEventSpec("summarize_search_results", "atomic", "对检索结果文本进行通用摘要。", _summarize_search_handler, external_call=True, execution_method="agent"))

    # ── 29 New Generic Events ──
    #   execution_method is set explicitly here AND driven by event_execution.yaml at runtime.
    #   The config file override happens at execution time via _resolve_execution_method().
    #   Setting it here gives a sensible default if the config file is missing.

    # Information Retrieval
    registry.register(HarnessEventSpec("web_fetch", "atomic", "获取指定 URL 的内容（HTML/JSON/文本）。参数: url, timeout, headers", _atomic_http_get, external_call=True, execution_method="local"))
    registry.register(HarnessEventSpec("web_search", "atomic", "执行网页搜索，返回结构化结果。参数: query, num_results", _agent_event_handler, external_call=True, execution_method="agent"))
    registry.register(HarnessEventSpec("read_file", "atomic", "读取本地文件内容。参数: path, encoding", _local_read_file, reads_existing_artifact=True, execution_method="local"))
    registry.register(HarnessEventSpec("query_api", "atomic", "调用任意 HTTP API。参数: url, method, headers, body", _local_query_api, external_call=True, execution_method="local"))
    registry.register(HarnessEventSpec("list_directory", "atomic", "列出目录内容。参数: path, pattern", _local_list_directory, reads_existing_artifact=True, execution_method="local"))

    # Information Processing
    registry.register(HarnessEventSpec("extract", "atomic", "从文本/JSON/HTML 中提取指定字段。参数: data, fields, format", _llm_event_handler, execution_method="llm"))
    registry.register(HarnessEventSpec("summarize", "atomic", "对长文本做摘要。参数: content, max_length", _agent_event_handler, external_call=True, execution_method="agent"))
    registry.register(HarnessEventSpec("analyze", "atomic", "对数据做分析（趋势、异常、解读）。参数: data, analysis_type", _agent_event_handler, external_call=True, execution_method="agent"))
    registry.register(HarnessEventSpec("transform", "atomic", "格式转换（JSON→CSV, Markdown→HTML 等）。参数: data, input_format, output_format, path", _local_transform, execution_method="local"))
    registry.register(HarnessEventSpec("filter", "atomic", "按条件筛选数据。参数: data, field, operator, value", _local_filter, execution_method="local"))
    registry.register(HarnessEventSpec("sort", "atomic", "对数据排序。参数: data, key, reverse", _local_sort, execution_method="local"))

    # Content Generation
    registry.register(HarnessEventSpec("generate_text", "atomic", "生成任意文本内容（文章、邮件、回复等）。参数: task, prompt, style", _agent_event_handler, external_call=True, execution_method="agent"))
    registry.register(HarnessEventSpec("generate_code", "atomic", "生成代码。参数: task, language, requirements。如果生成Python绘图代码（matplotlib/seaborn），必须包含中文字体设置：import matplotlib; matplotlib.rcParams['font.sans-serif']=['WenQuanYi Zen Hei','SimHei','DejaVu Sans']; matplotlib.rcParams['axes.unicode_minus']=False", _agent_event_handler, external_call=True, execution_method="agent"))
    registry.register(HarnessEventSpec("write_report", "atomic", "生成结构化报告。参数: task, format, sections", _agent_event_handler, external_call=True, execution_method="agent"))
    registry.register(HarnessEventSpec("create_diagram", "atomic", "生成图表/流程图/架构图。参数: task, diagram_type, description。生成的Python绘图代码必须包含中文字体设置：import matplotlib; matplotlib.rcParams['font.sans-serif']=['WenQuanYi Zen Hei','SimHei','DejaVu Sans']; matplotlib.rcParams['axes.unicode_minus']=False", _agent_event_handler, external_call=True, execution_method="agent"))

    # Execution & Operations
    registry.register(HarnessEventSpec("run_command", "atomic", "执行系统命令或脚本。参数: command, timeout, workdir", _local_run_command, execution_method="local"))
    registry.register(HarnessEventSpec("send_email", "atomic", "发送邮件（占位，需配置 SMTP）。参数: to, subject, body", _llm_event_handler, execution_method="local"))
    registry.register(HarnessEventSpec("post_message", "atomic", "发送消息到指定渠道。参数: channel, message", _llm_event_handler, execution_method="local"))
    registry.register(HarnessEventSpec("create_file", "atomic", "创建文件（自动识别格式）。参数: path, content, format", _local_create_file, produces_artifact=True, execution_method="local"))
    registry.register(HarnessEventSpec("download_file", "atomic", "从 URL 下载文件。参数: url, destination, timeout", _local_download_file, external_call=True, execution_method="local"))

    # Decision & Control
    registry.register(HarnessEventSpec("if_condition", "atomic", "条件分支（基于前序结果判断）。参数: condition, data", _llm_event_handler, execution_method="llm"))
    registry.register(HarnessEventSpec("switch", "atomic", "多路分支选择。参数: cases, data", _llm_event_handler, execution_method="llm"))
    registry.register(HarnessEventSpec("loop", "atomic", "循环执行子步骤。参数: iterations, steps", _llm_event_handler, execution_method="llm"))
    registry.register(HarnessEventSpec("wait", "atomic", "等待指定时间。参数: seconds", _llm_event_handler, execution_method="local"))
    registry.register(HarnessEventSpec("retry", "atomic", "重试前序失败的步骤。参数: max_retries, step_id", _llm_event_handler, execution_method="local"))

    # Validation & Quality
    registry.register(HarnessEventSpec("validate", "atomic", "验证数据/文件是否符合预期格式。参数: data, schema, rules", _llm_event_handler, execution_method="llm"))
    registry.register(HarnessEventSpec("check_quality", "atomic", "用 LLM 检查内容质量。参数: content, criteria", _llm_event_handler, execution_method="llm"))
    registry.register(HarnessEventSpec("audit", "atomic", "审计执行过程完整性和合规性。参数: data, logs", _llm_event_handler, execution_method="llm"))
    registry.register(HarnessEventSpec("compare", "atomic", "对比两个数据源或文件。参数: source_a, source_b, aspects", _llm_event_handler, execution_method="llm"))

    _DEFAULT_REGISTRY = registry
    return registry


async def _atomic_http_get(ctx: HarnessContext, params: JsonDict) -> JsonDict:
    """轻量 HTTP GET 请求，获取外部数据。
    
    参数：
        url (str): 请求 URL（必填）
        timeout (int): 超时秒数，默认 20
        headers (dict): 自定义请求头
        
    返回：
        {"ok": true, "content": "原始文本", "json": {解析后的JSON} 或 None}
    """
    url = str(params.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "missing url"}
    # Auto-fix: add https:// if no protocol specified
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    
    import urllib.request, json, time
    timeout = max(5, min(60, int(params.get("timeout") or 20)))
    headers = params.get("headers") if isinstance(params.get("headers"), dict) else {}
    req_headers = {
        "User-Agent": "Mozilla/5.0 Partner/0.7",
        **headers,
    }
    
    # Retry on transient SSL/network errors
    max_attempts = 3
    last_error = ""
    raw = ""
    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            break  # success
        except urllib.error.URLError as e:
            last_error = str(e)
            if attempt < max_attempts:
                wait = 2 ** attempt
                time.sleep(wait)
            continue
        except Exception as e:
            last_error = str(e)
            if attempt < max_attempts:
                wait = 2 ** attempt
                time.sleep(wait)
            continue
    else:
        return {"ok": False, "error": f"HTTP request failed after {max_attempts} attempts: {last_error}"}
    
    # Try to parse as JSON
    parsed = None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    
    return {
        "ok": True,
        "content": raw.strip(),
        "json": parsed,
        "summary": f"获取到 {len(raw.strip())} 字节的外部数据" + (f"，含 {len(parsed)} 个字段" if isinstance(parsed, (dict, list)) else ""),
    }
