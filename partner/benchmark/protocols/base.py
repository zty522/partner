"""Benchmark Protocol 基类定义。

每个 benchmark 定义一个「协议」：
  - 输入格式（任务描述、数据文件、参考材料）
  - 预期输出（格式规范、内容标准）
  - 评分规则（精确匹配 / 模糊匹配 / 评分细则）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


JsonDict = dict[str, Any]


@dataclass
class BenchmarkTask:
    """一个独立的 benchmark 任务。"""

    task_id: str
    """该任务在 benchmark 套件内的唯一标识，如 'lit_review_001'。"""

    title: str
    """人类可读的任务标题。"""

    description: str
    """任务的详细描述，给 Agent 的上下文。"""

    input_data: JsonDict = field(default_factory=dict)
    """输入数据。可能包含：
       - query: str             用户查询/问题
       - context: str           背景材料（文献摘要、数据描述等）
       - attachments: list[str] 附件路径
       - parameters: dict       额外参数（输出格式、长度限制等）
    """

    expected_output: JsonDict = field(default_factory=dict)
    """预期输出规范。可能包含：
       - format: str            输出格式（text, markdown, csv, json, pdf）
       - schema: dict           可选 JSON Schema
       - key_points: list[str]  必须覆盖的关键点
       - sample: str            可选输出样例
       - constraints: dict      约束条件（长度、引用数等）
    """

    scoring_rules: JsonDict = field(default_factory=dict)
    """评分规则。可能包含：
       - method: str            评分方法（exact_match / fuzzy_match / rubric / llm_judge）
       - weight: float          该任务在套件中的权重（默认 1.0）
       - rubric: list[dict]     评分细则（用于 rubric 方法）
         [{"item": "摘要质量", "max_score": 5, "criteria": "..."}, ...]
       - passing_threshold: float 及格线
    """

    metadata: JsonDict = field(default_factory=dict)
    """额外元数据，如来源、难度级别、标签等。"""


@dataclass
class BenchmarkProtocol:
    """一个完整的 benchmark 协议定义。"""

    benchmark_id: str
    """benchmark 唯一标识，如 'naturebench/literature_review_v1'。"""

    display_name: str
    """人类可读名称。"""

    description: str
    """Benchmark 的目标、范围和评分哲学的说明。"""

    version: str = "1.0.0"
    """协议版本号。"""

    tasks: list[BenchmarkTask] = field(default_factory=list)
    """该 benchmark 包含的所有任务。"""

    metadata: JsonDict = field(default_factory=dict)
    """额外元数据：作者、来源、参考引用等。"""

    def task_count(self) -> int:
        return len(self.tasks)

    def get_task(self, task_id: str) -> BenchmarkTask | None:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None


# ── 评分方法常量 ──────────────────────────────────────────────────────

SCORE_METHOD_EXACT_MATCH = "exact_match"
SCORE_METHOD_FUZZY_MATCH = "fuzzy_match"
SCORE_METHOD_RUBRIC = "rubric"
SCORE_METHOD_LLM_JUDGE = "llm_judge"
SCORE_METHOD_CUSTOM = "custom"

VALID_SCORE_METHODS = {
    SCORE_METHOD_EXACT_MATCH,
    SCORE_METHOD_FUZZY_MATCH,
    SCORE_METHOD_RUBRIC,
    SCORE_METHOD_LLM_JUDGE,
    SCORE_METHOD_CUSTOM,
}
