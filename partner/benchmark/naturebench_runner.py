"""NatureBench 执行器。

使用 Partner Harness 引擎执行 NatureBench 评估任务。
每个 benchmark 问题建模为一个 TaskInstance + Harness MicroPlan。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .benchmark_registry import BenchmarkRegistry, get_registry
from .benchmark_scorer import (
    BenchScoreSet,
    score_benchmark_run,
    format_score_report,
)
from .export import (
    build_task_trace,
    export_benchmark_results,
    TaskTrace,
)
from .harness_integration import (
    HarnessBenchmarkExecutor,
    sync_harness_executor,
)
from .protocols.base import (
    BenchmarkProtocol,
    BenchmarkTask,
)

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


# ── 结果类型 ───────────────────────────────────────────────────────────


@dataclass
class BenchmarkResult:
    """单个 benchmark 任务的执行结果。"""
    task_id: str
    title: str
    status: str  # "completed", "failed", "skipped"
    content: str = ""
    artifacts: list[str] = field(default_factory=list)
    error: str = ""
    duration_ms: int = 0
    trace: TaskTrace | None = None
    metadata: JsonDict = field(default_factory=dict)


@dataclass
class BenchmarkRun:
    """一次完整的 benchmark 运行结果。"""
    benchmark_id: str
    suite_version: str
    started_at: str
    completed_at: str = ""
    duration_ms: int = 0
    results: dict[str, BenchmarkResult] = field(default_factory=dict)
    score_set: BenchScoreSet | None = None
    export_path: str = ""
    config: JsonDict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        total = len(self.results)
        completed = sum(1 for r in self.results.values() if r.status == "completed")
        failed = sum(1 for r in self.results.values() if r.status == "failed")
        skipped = sum(1 for r in self.results.values() if r.status == "skipped")

        lines = [
            f"Benchmark: {self.benchmark_id} v{self.suite_version}",
            f"  状态: {self.completed_at or '运行中'}",
            f"  耗时: {self.duration_ms}ms",
            f"  任务: {completed} ✅ / {failed} ❌ / {skipped} ⏭️  (共 {total})",
        ]
        if self.score_set:
            lines.append(f"  总分: {self.score_set.total_score:.1f}/{self.score_set.max_score:.1f}")
            lines.append(f"  通过率: {self.score_set.pass_rate:.1%}")
        return "\n".join(lines)


# ── 任务执行策略 ───────────────────────────────────────────────────────

# 用户可注入的自定义执行器（支持同步和异步）
TaskExecutorFn = Callable[[BenchmarkTask, str], "BenchmarkResult | Awaitable[BenchmarkResult]"]


from typing import Awaitable


async def _default_task_executor(task: BenchmarkTask, work_dir: str) -> BenchmarkResult:
    """默认的任务执行器：使用简单的文件写入 + LLM 调用。

    对于正式评估，期望用户提供自定义执行器（通过 with_task_executor）。
    默认实现创建一个包含任务描述的 markdown 文件作为占位结果。
    """
    task_dir = os.path.join(work_dir, task.task_id)
    os.makedirs(task_dir, exist_ok=True)

    result_path = os.path.join(task_dir, "result.md")
    content_parts = [
        f"# {task.title}",
        f"",
        f"**Task ID**: {task.task_id}",
        f"**Description**: {task.description}",
        f"**Generated at**: {datetime.now().isoformat()}",
        f"",
        f"## 输入",
        f"",
        f"```json",
        json.dumps(task.input_data, ensure_ascii=False, indent=2),
        f"```",
        f"",
        f"## 输出（占位 - 请替换为实际 Agent 执行结果）",
        f"",
        f"*此处应为 Agent 执行后生成的综述报告/分析结果。*",
        f"",
        f"### 关键点覆盖",
        f"",
    ]
    for point in task.expected_output.get("key_points", []):
        content_parts.append(f"- [ ] {point}")

    content_parts.append("")
    content_parts.append("---")
    content_parts.append("*由 Partner Benchmark 框架自动生成*")

    content = "\n".join(content_parts)
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(content)

    task_state = {
        "task_id": task.task_id,
        "title": task.title,
        "created_at": datetime.now().isoformat(),
        "completion_status": "partial",
        "working_dir": task_dir,
    }
    with open(os.path.join(task_dir, "task_instance.json"), "w", encoding="utf-8") as f:
        json.dump(task_state, f, ensure_ascii=False, indent=2)

    return BenchmarkResult(
        task_id=task.task_id,
        title=task.title,
        status="completed",
        content=content,
        artifacts=[result_path],
        metadata={"is_placeholder": True},
    )


# ── NatureBench Runner ─────────────────────────────────────────────────


class NatureBenchRunner:
    """NatureBench 评估执行器。

    用法:
        runner = NatureBenchRunner()
        runner.with_task_executor(my_executor)  # 可选：自定义执行器
        run = runner.run("naturebench/literature_review_v1")
        print(run.summary)
    """

    def __init__(
        self,
        *,
        registry: BenchmarkRegistry | None = None,
        work_dir: str | None = None,
        model_name: str = "",
        provider_name: str = "",
        agent_id: str = "partner",
        output_dir: str | None = None,
    ):
        self._registry = registry or get_registry()
        self._executor: TaskExecutorFn | None = sync_harness_executor
        self._work_dir = work_dir or tempfile.mkdtemp(prefix="benchmark_")
        self._model_name = model_name
        self._provider_name = provider_name
        self._agent_id = agent_id
        self._output_dir = output_dir or os.path.join(self._work_dir, "results")
        self._learning_callback: Callable[[BenchmarkRun], None] | None = None

    def with_task_executor(self, executor: TaskExecutorFn) -> NatureBenchRunner:
        """设置自定义任务执行器。

        用户可以提供自己的执行函数，使用 Partner Harness 或其他引擎执行任务。
        """
        self._executor = executor
        return self

    def with_learning_callback(
        self, callback: Callable[[BenchmarkRun], None]
    ) -> NatureBenchRunner:
        """设置学习回调，用于将评估结果写入 learning.db。"""
        self._learning_callback = callback
        return self

    def get_protocol(self, benchmark_id: str) -> BenchmarkProtocol:
        """获取已注册的 benchmark protocol。"""
        protocol = self._registry.get(benchmark_id)
        if not protocol:
            available = [s["benchmark_id"] for s in self._registry.list_suites()]
            raise ValueError(
                f"未知 benchmark: {benchmark_id!r}。"
                f"可用: {available}"
            )
        return protocol

    def list_available(self) -> list[dict[str, Any]]:
        return self._registry.list_suites()

    # ── 核心运行逻辑 ──

    def run(
        self,
        benchmark_id: str,
        *,
        task_ids: list[str] | None = None,
        export: bool = True,
    ) -> BenchmarkRun:
        """同步执行一个 benchmark 套件。"""
        return asyncio.run(self._run_async(benchmark_id, task_ids=task_ids, export=export))

    async def _run_async(
        self,
        benchmark_id: str,
        *,
        task_ids: list[str] | None = None,
        export: bool = True,
    ) -> BenchmarkRun:
        protocol = self.get_protocol(benchmark_id)
        started_at = datetime.now().isoformat()
        run = BenchmarkRun(
            benchmark_id=benchmark_id,
            suite_version=protocol.version,
            started_at=started_at,
            config={
                "work_dir": self._work_dir,
                "output_dir": self._output_dir,
                "agent_id": self._agent_id,
            },
        )

        tasks_to_run = protocol.tasks
        if task_ids:
            tasks_to_run = [t for t in tasks_to_run if t.task_id in task_ids]
            missing = set(task_ids) - {t.task_id for t in tasks_to_run}
            if missing:
                logger.warning("[BENCHMARK] 跳过不存在的任务: %s", missing)

        # 确保输出目录存在
        os.makedirs(self._output_dir, exist_ok=True)

        executor = self._executor or _default_task_executor
        task_dirs: dict[str, str] = {}

        for task in tasks_to_run:
            logger.info("[BENCHMARK] 执行任务: %s (%s)", task.task_id, task.title)
            try:
                task_dir = os.path.join(self._work_dir, task.task_id)
                os.makedirs(task_dir, exist_ok=True)
                task_dirs[task.task_id] = task_dir

                start_ts = time.time()
                # 支持同步和异步执行器
                if asyncio.iscoroutinefunction(executor):
                    raw = await executor(task, task_dir)
                else:
                    raw = await asyncio.to_thread(executor, task, task_dir)
                result = raw if isinstance(raw, BenchmarkResult) else BenchmarkResult(
                    task_id=task.task_id, title=task.title, status="completed"
                )
                result.duration_ms = int((time.time() - start_ts) * 1000)

                # 构建执行轨迹
                trace = build_task_trace(
                    task_id=task.task_id,
                    title=task.title,
                    benchmark_id=benchmark_id,
                    task_dir=task_dir,
                    agent_id=self._agent_id,
                    model_name=self._model_name,
                    provider_name=self._provider_name,
                )
                result.trace = trace

            except Exception as exc:
                logger.error("[BENCHMARK] 任务执行失败 %s: %s", task.task_id, exc)
                result = BenchmarkResult(
                    task_id=task.task_id,
                    title=task.title,
                    status="failed",
                    error=str(exc),
                )

            run.results[task.task_id] = result

        run.completed_at = datetime.now().isoformat()
        run.duration_ms = _compute_elapsed_ms(started_at, run.completed_at)

        # 评分
        task_outputs = {
            tid: {
                "content": r.content,
                "title": r.title,
                "status": r.status,
                "artifacts": r.artifacts,
            }
            for tid, r in run.results.items()
        }
        run.score_set = score_benchmark_run(protocol, task_outputs)

        # 导出
        if export:
            task_results_output = {
                tid: {
                    "content": r.content,
                    "title": r.title,
                    "status": r.status,
                    "artifacts": r.artifacts,
                }
                for tid, r in run.results.items()
            }
            export_path = os.path.join(
                self._output_dir,
                f"{benchmark_id.replace('/', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
            export_benchmark_results(
                task_results=task_results_output,
                task_dirs=task_dirs,
                benchmark_id=benchmark_id,
                suite_version=protocol.version,
                output_path=export_path,
                agent_info={"agent_id": self._agent_id},
                model_name=self._model_name,
                provider_name=self._provider_name,
            )
            run.export_path = export_path
            logger.info("[BENCHMARK] 结果已导出: %s", export_path)

        # 学习回调
        if self._learning_callback:
            try:
                self._learning_callback(run)
            except Exception as exc:
                logger.warning("[BENCHMARK] 学习回调失败: %s", exc)

        return run


# ── 模块级便利函数 ──────────────────────────────────────────────────────


def run_benchmark(
    benchmark_id: str,
    *,
    executor: TaskExecutorFn | None = None,
    work_dir: str | None = None,
    output_dir: str | None = None,
    model_name: str = "",
    provider_name: str = "",
    export: bool = True,
) -> BenchmarkRun:
    """快速运行一个 benchmark 的便利函数。"""
    runner = NatureBenchRunner(
        work_dir=work_dir,
        model_name=model_name,
        provider_name=provider_name,
        output_dir=output_dir,
    )
    if executor:
        runner.with_task_executor(executor)
    return runner.run(benchmark_id, export=export)


def _compute_elapsed_ms(start_iso: str, end_iso: str) -> int:
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        return int((end - start).total_seconds() * 1000)
    except Exception:
        return 0
