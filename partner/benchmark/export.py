"""标准化执行轨迹导出。

将 Partner 的 Event 执行轨迹导出为 NatureBench 可读的标准化 JSON 格式。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# 当前导出格式版本
NATUREBENCH_EXPORT_VERSION = "1.0.0"


# ── 导出数据模型 ───────────────────────────────────────────────────────


@dataclass
class TraceStep:
    """执行轨迹中的一步。"""
    step_id: str
    event_type: str
    start_time: str
    end_time: str
    duration_ms: int
    status: str  # "success", "failed", "timeout", "fallback"
    input: JsonDict = field(default_factory=dict)
    output: JsonDict = field(default_factory=dict)
    error: str = ""
    metadata: JsonDict = field(default_factory=dict)


@dataclass
class TraceArtifact:
    """执行过程中产生的产物。"""
    path: str
    type: str  # "file", "message", "plot"
    size_bytes: int = 0
    description: str = ""
    content_preview: str = ""


@dataclass
class TaskTrace:
    """单个任务的完整执行轨迹。"""
    task_id: str
    title: str
    benchmark_id: str
    agent_id: str = "partner"
    model_name: str = ""
    provider_name: str = ""
    start_time: str = ""
    end_time: str = ""
    total_duration_ms: int = 0
    status: str = "completed"  # "completed", "failed", "partial"
    steps: list[TraceStep] = field(default_factory=list)
    artifacts: list[TraceArtifact] = field(default_factory=list)
    llm_calls_total: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    metadata: JsonDict = field(default_factory=dict)


@dataclass
class NatureBenchExport:
    """符合 NatureBench 格式的完整导出包。"""
    export_version: str = NATUREBENCH_EXPORT_VERSION
    benchmark_id: str = ""
    suite_version: str = ""
    exported_at: str = ""
    agent_info: JsonDict = field(default_factory=dict)
    tasks: list[TaskTrace] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)


# ── 导出函数 ───────────────────────────────────────────────────────────


def _read_task_log(task_dir: str) -> list[JsonDict]:
    """读取任务日志（task_log.jsonl）。"""
    log_path = os.path.join(task_dir, "task_log.jsonl")
    if not os.path.exists(log_path):
        return []
    entries = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception as exc:
        logger.warning("[EXPORT] 读取任务日志失败: %s", exc)
    return entries


def _read_task_state(task_dir: str) -> JsonDict | None:
    """读取任务状态文件。"""
    state_path = os.path.join(task_dir, "task_instance.json")
    if not os.path.exists(state_path):
        return None
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _compute_duration_ms(start_ts: str, end_ts: str | None = None) -> int:
    """计算两个 ISO 时间戳之间的毫秒差。"""
    if not end_ts:
        return 0
    try:
        start = datetime.fromisoformat(start_ts)
        end = datetime.fromisoformat(end_ts)
        return int((end - start).total_seconds() * 1000)
    except Exception:
        return 0


def build_task_trace(
    task_id: str,
    title: str,
    benchmark_id: str,
    task_dir: str,
    *,
    agent_id: str = "partner",
    model_name: str = "",
    provider_name: str = "",
) -> TaskTrace:
    """从任务工作目录构建 TaskTrace。"""
    log_entries = _read_task_log(task_dir)
    state = _read_task_state(task_dir)

    now = datetime.now().isoformat()
    start_time = now
    end_time = now

    # 从状态文件获取时间
    if state:
        start_time = state.get("created_at", start_time)

    steps: list[TraceStep] = []
    step_counter: dict[str, int] = {}

    for entry in log_entries:
        event = entry.get("event", "")
        ts = entry.get("ts", "")

        if event == "task_instance_created":
            start_time = ts
        elif event == "completion_status_updated":
            end_time = ts

        # 将执行事件转成 TraceStep
        if event in ("robust_execute_start", "robust_execute_success",
                     "robust_execute_failure", "robust_execute_timeout",
                     "harness_plan", "plan_executed", "artifact_validation"):
            step_type = entry.get("event_name", event)
            step_counter[step_type] = step_counter.get(step_type, 0) + 1
            step_id = f"{step_type}_{step_counter[step_type]}"

            step_status = "success"
            if event in ("robust_execute_failure",):
                step_status = "failed"
            elif event == "robust_execute_timeout":
                step_status = "timeout"
            elif event == "robust_execute_fallback_success":
                step_status = "fallback"

            steps.append(TraceStep(
                step_id=step_id,
                event_type=step_type,
                start_time=ts,
                end_time=ts,
                duration_ms=0,
                status=step_status,
                input={k: v for k, v in entry.items() if k not in ("ts", "event")},
                output={},
                error=entry.get("error", ""),
            ))

    total_ms = _compute_duration_ms(start_time, end_time)
    task_status = "completed"
    if state:
        status = state.get("completion_status", "pending")
        if status in ("failed", "pending"):
            task_status = status if status == "failed" else "partial"

    return TaskTrace(
        task_id=task_id,
        title=title,
        benchmark_id=benchmark_id,
        agent_id=agent_id,
        model_name=model_name,
        provider_name=provider_name,
        start_time=start_time,
        end_time=end_time,
        total_duration_ms=total_ms,
        status=task_status,
        steps=steps,
        llm_calls_total=sum(1 for s in steps if "llm" in s.event_type.lower() or "agent" in s.event_type.lower()),
        metadata={"log_entries_count": len(log_entries)},
    )


def export_event_trace(
    task_trace: TaskTrace,
    output_path: str | None = None,
) -> str:
    """将单个 TaskTrace 导出为 JSON 字符串（可选写入文件）。

    Args:
        task_trace: 任务执行轨迹
        output_path: 可选输出文件路径

    Returns:
        JSON 字符串
    """
    data = {
        "export_version": NATUREBENCH_EXPORT_VERSION,
        "type": "task_trace",
        "task_id": task_trace.task_id,
        "title": task_trace.title,
        "benchmark_id": task_trace.benchmark_id,
        "agent_id": task_trace.agent_id,
        "model_name": task_trace.model_name,
        "provider_name": task_trace.provider_name,
        "timing": {
            "start_time": task_trace.start_time,
            "end_time": task_trace.end_time,
            "total_duration_ms": task_trace.total_duration_ms,
        },
        "status": task_trace.status,
        "steps": [
            {
                "step_id": s.step_id,
                "event_type": s.event_type,
                "timing": {
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "duration_ms": s.duration_ms,
                },
                "status": s.status,
                "error": s.error if s.error else None,
                "metadata": s.metadata,
            }
            for s in task_trace.steps
        ],
        "llm_calls_total": task_trace.llm_calls_total,
        "tokens_input": task_trace.tokens_input,
        "tokens_output": task_trace.tokens_output,
    }

    json_str = json.dumps(data, ensure_ascii=False, indent=2)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json_str)
            f.write("\n")
        logger.info("[EXPORT] 轨迹已导出: %s", output_path)

    return json_str


def export_benchmark_results(
    task_results: dict[str, JsonDict],
    task_dirs: dict[str, str],
    benchmark_id: str,
    suite_version: str,
    *,
    agent_info: JsonDict | None = None,
    output_path: str | None = None,
    model_name: str = "",
    provider_name: str = "",
) -> str:
    """导出完整的 benchmark 结果为 NatureBench 格式。

    Args:
        task_results: {task_id: output_dict} 映射
        task_dirs: {task_id: task_dir_path} 映射
        benchmark_id: benchmark 标识
        suite_version: 版本号
        agent_info: Agent 信息
        output_path: 可选输出文件路径
        model_name: 模型名称
        provider_name: 提供商

    Returns:
        JSON 字符串
    """
    tasks: list[TaskTrace] = []
    for task_id, result in task_results.items():
        task_dir = task_dirs.get(task_id, "")
        trace = build_task_trace(
            task_id=task_id,
            title=result.get("title", task_id),
            benchmark_id=benchmark_id,
            task_dir=task_dir,
            model_name=model_name,
            provider_name=provider_name,
        )
        tasks.append(trace)

    export_pkg = NatureBenchExport(
        export_version=NATUREBENCH_EXPORT_VERSION,
        benchmark_id=benchmark_id,
        suite_version=suite_version,
        exported_at=datetime.now().isoformat(),
        agent_info=agent_info or {},
        tasks=tasks,
    )

    data = asdict(export_pkg)
    json_str = json.dumps(data, ensure_ascii=False, indent=2)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json_str)
            f.write("\n")
        logger.info("[EXPORT] Benchmark 结果已导出: %s", output_path)

    return json_str
