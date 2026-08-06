"""Benchmark 学习集成。

将 benchmark 评估结果写入 learning.db 作为 Growth 事件，
供 Partner 的自进化引擎使用。
"""

from __future__ import annotations

import logging
from typing import Any

from ..meta.learning import record_growth, record_experience
from .naturebench_runner import BenchmarkRun

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


def record_benchmark_to_learning(run: BenchmarkRun) -> None:
    """将 benchmark 运行结果记录到 learning.db。

    作为 Growth milestone + 每个任务作为 Experience 记录。
    """
    if not run.score_set:
        logger.warning("[BENCHMARK/LEARNING] 未评分，跳过学习记录")
        return

    score_set = run.score_set

    # 1. Growth milestone: benchmark 完成整体记录
    milestone = (
        f"Benchmark {run.benchmark_id} v{run.suite_version}: "
        f"{score_set.passed_count}/{score_set.total_count} passed "
        f"({score_set.normalized_total:.1%})"
    )
    reflection_lines = [
        f"完成时间: {run.completed_at}",
        f"总耗时: {run.duration_ms}ms",
        f"总分: {score_set.total_score:.1f}/{score_set.max_score:.1f} ({score_set.normalized_total:.1%})",
        f"通过率: {score_set.pass_rate:.1%}",
        "",
        "任务明细:",
    ]
    for s in score_set.scores:
        icon = "✅" if s.passed else "❌"
        reflection_lines.append(f"  {icon} {s.task_id}: {s.score:.1f}/100 ({s.normalized:.1%})")
        if s.rubric_scores:
            for rs in s.rubric_scores:
                reflection_lines.append(f"      {rs.item}: {rs.score}/{rs.max_score}")

    record_growth(
        milestone=milestone,
        reflection="\n".join(reflection_lines),
        category="benchmark_evaluation",
        instance_id="",
    )

    # 2. 每个任务作为 Experience 记录
    for s in score_set.scores:
        task_result = run.results.get(s.task_id)
        task_title = task_result.title if task_result else s.task_id
        record_experience(
            user_message=f"[Benchmark] {run.benchmark_id}/{s.task_id}: {task_title}",
            task_summary=f"Benchmark {run.benchmark_id} 任务 {s.task_id}: 得分 {s.score:.1f}/100",
            output_type="benchmark_score",
            file_format="json",
            success=s.passed,
            skills_used=["benchmark", run.benchmark_id],
        )

    logger.info(
        "[BENCHMARK/LEARNING] 已记录 %s -> %d tasks, pass_rate=%.1f%%",
        run.benchmark_id, len(score_set.scores), score_set.pass_rate * 100,
    )


def make_learning_callback():
    """创建一个可注入 NatureBenchRunner 的学习回调。"""
    return record_benchmark_to_learning
