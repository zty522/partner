"""Benchmark 评分引擎。

将 Partner 执行结果与标准答案/预期输出对比，返回结构化的评分报告。
支持多种评分方法：精确匹配、模糊匹配、评分细则、LLM 判分。
"""

from __future__ import annotations

import importlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .protocols.base import (
    BenchmarkProtocol,
    BenchmarkTask,
    SCORE_METHOD_EXACT_MATCH,
    SCORE_METHOD_FUZZY_MATCH,
    SCORE_METHOD_RUBRIC,
    SCORE_METHOD_LLM_JUDGE,
    SCORE_METHOD_CUSTOM,
)

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


# ── 评分结果类型 ───────────────────────────────────────────────────────


@dataclass
class RubricItemScore:
    """评分细则中的一项得分。"""
    item: str
    score: float
    max_score: float
    normalized: float  # 0.0 ~ 1.0
    comment: str = ""


@dataclass
class BenchScore:
    """单个任务的评分结果。"""
    task_id: str
    benchmark_id: str
    method: str
    score: float           # 原始分（0-100 归一化）
    normalized: float = 0.0  # 0.0 ~ 1.0
    passed: bool = False
    passing_threshold: float = 0.6
    details: str = ""
    rubric_scores: list[RubricItemScore] = field(default_factory=list)
    error: str = ""
    metadata: JsonDict = field(default_factory=dict)


@dataclass
class BenchScoreSet:
    """一组评分结果（一个 benchmark 的全部任务）。"""
    benchmark_id: str
    suite_version: str
    scores: list[BenchScore]
    total_score: float = 0.0
    max_score: float = 0.0
    normalized_total: float = 0.0
    passed_count: int = 0
    total_count: int = 0
    scored_at: str = ""
    metadata: JsonDict = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        if self.total_count == 0:
            return 0.0
        return self.passed_count / self.total_count

    @property
    def summary(self) -> str:
        return (
            f"Benchmark: {self.benchmark_id}\n"
            f"  总分: {self.total_score:.1f}/{self.max_score:.1f} "
            f"({self.normalized_total:.1%})\n"
            f"  通过: {self.passed_count}/{self.total_count} "
            f"({self.pass_rate:.1%})\n"
            f"  时间: {self.scored_at}"
        )


# ── 评分器注册表 ───────────────────────────────────────────────────────

ScorerFn = Callable[[BenchmarkTask, JsonDict], BenchScore]

_SCORERS: dict[str, ScorerFn] = {}


def register_scorer(method: str, fn: ScorerFn) -> None:
    """注册一个评分函数。"""
    _SCORERS[method] = fn


def _rubric_scorer(task: BenchmarkTask, output: JsonDict) -> BenchScore:
    """基于评分细则（rubric）评分。"""
    rules = task.scoring_rules
    rubric = rules.get("rubric", [])
    threshold = float(rules.get("passing_threshold", 0.6))

    if not rubric:
        return BenchScore(
            task_id=task.task_id,
            benchmark_id="",
            method=SCORE_METHOD_RUBRIC,
            score=0,
            normalized=0.0,
            passed=False,
            details="未定义评分细则",
            error="missing rubric",
        )

    total_score = 0.0
    total_max = 0.0
    items: list[RubricItemScore] = []

    for criterion in rubric:
        item_name = criterion.get("item", "未知")
        max_score = float(criterion.get("max_score", 1))
        total_max += max_score

        # 简单启发式：根据 output 的长度、是否包含关键词等给分
        content = (
            output.get("content", "") or
            output.get("result", "") or
            json.dumps(output, ensure_ascii=False)
        )
        content_str = str(content)

        # 默认给满分的 60%（保守）
        item_score = max_score * 0.6

        # 如果有长内容（>500 字），加分
        if len(content_str) > 500:
            item_score = min(max_score, item_score + max_score * 0.15)
        if len(content_str) > 1500:
            item_score = min(max_score, item_score + max_score * 0.1)

        # 如果包含引用的关键词，加分
        keywords = criterion.get("criteria", "").lower()
        for kw in re.findall(r'[\u4e00-\u9fff\w]{2,}', keywords):
            if kw.lower() in content_str.lower():
                item_score = min(max_score, item_score + max_score * 0.05)

        items.append(RubricItemScore(
            item=item_name,
            score=round(item_score, 2),
            max_score=max_score,
            normalized=round(item_score / max_score, 4) if max_score > 0 else 0,
            comment=f"内容长度 {len(content_str)} 字符" if len(content_str) > 100 else "内容较短",
        ))
        total_score += item_score

    normalized_total = total_score / total_max if total_max > 0 else 0
    passed = normalized_total >= threshold

    return BenchScore(
        task_id=task.task_id,
        benchmark_id="",
        method=SCORE_METHOD_RUBRIC,
        score=round(normalized_total * 100, 2),
        normalized=round(normalized_total, 4),
        passed=passed,
        passing_threshold=threshold,
        details=f"rubric: {total_score:.1f}/{total_max:.1f}",
        rubric_scores=items,
    )


def _exact_match_scorer(task: BenchmarkTask, output: JsonDict) -> BenchScore:
    """精确匹配评分。"""
    expected = task.expected_output
    content = str(output.get("content", output.get("result", "")))

    # 检查所有 key_points 是否在输出中
    key_points = expected.get("key_points", [])
    matched = 0
    for point in key_points:
        if point.lower() in content.lower():
            matched += 1

    total = len(key_points) if key_points else 1
    ratio = matched / total if total > 0 else 0
    threshold = float(task.scoring_rules.get("passing_threshold", 0.8))

    return BenchScore(
        task_id=task.task_id,
        benchmark_id="",
        method=SCORE_METHOD_EXACT_MATCH,
        score=round(ratio * 100, 2),
        normalized=round(ratio, 4),
        passed=ratio >= threshold,
        passing_threshold=threshold,
        details=f"key_points matched: {matched}/{total}",
    )


def _fuzzy_match_scorer(task: BenchmarkTask, output: JsonDict) -> BenchScore:
    """模糊匹配评分（基于文本相似度）。"""
    expected = task.expected_output
    content = str(output.get("content", output.get("result", "")))

    # 使用简单的重叠度作为基线
    key_points = expected.get("key_points", [])
    matched = 0
    for point in key_points:
        # 检查关键词或语义相近的表达
        words = re.findall(r'[\u4e00-\u9fff\w]{2,}', point)
        word_matches = sum(1 for w in words if w.lower() in content.lower())
        if len(words) > 0 and word_matches / len(words) >= 0.5:
            matched += 1

    total = len(key_points) if key_points else 1
    ratio = matched / total if total > 0 else 0
    threshold = float(task.scoring_rules.get("passing_threshold", 0.6))

    return BenchScore(
        task_id=task.task_id,
        benchmark_id="",
        method=SCORE_METHOD_FUZZY_MATCH,
        score=round(ratio * 100, 2),
        normalized=round(ratio, 4),
        passed=ratio >= threshold,
        passing_threshold=threshold,
        details=f"fuzzy key_points matched: {matched}/{total}",
    )


def _llm_judge_scorer(task: BenchmarkTask, output: JsonDict) -> BenchScore:
    """LLM 判分 — 调用 adapter.chat() 进行语义级评分。
    
    评分维度：
    - completeness（完整性）：是否覆盖任务要求的所有方面
    - accuracy（准确性）：数据/结论是否准确
    - presentation（表达）：结构是否清晰、逻辑是否连贯
    """
    content = str(output.get("content", output.get("result", "")))
    if not content or len(content) < 50:
        # Fallback for empty/short output
        return BenchScore(
            task_id=task.task_id,
            benchmark_id="",
            method=SCORE_METHOD_LLM_JUDGE,
            score=0,
            normalized=0.0,
            passed=False,
            details="output too short for LLM evaluation",
        )
    
    dimensions = task.scoring_rules.get("dimensions", {
        "completeness": 0.4, "accuracy": 0.3, "presentation": 0.3,
    })
    key_points = task.expected_output.get("key_points", [])
    threshold = float(task.scoring_rules.get("passing_threshold", 0.6))
    
    # Try LLM scoring
    llm_score = _call_llm_scorer(task, content, key_points, dimensions)
    if llm_score is not None:
        return llm_score
    
    # Fallback: heuristic scoring
    return _heuristic_llm_fallback(task, content, key_points, dimensions, threshold)


def _call_llm_scorer(task: BenchmarkTask, content: str, key_points: list[str], dimensions: dict) -> BenchScore | None:
    """Call LLM to score benchmark output. Returns None if LLM unavailable."""
    try:
        from ..adapters.adapter import AgentAdapter
        adapter = AgentAdapter.get_default()
        if not adapter:
            return None
        
        dims_desc = "\n".join(f"- {k}: {v*100:.0f}% 权重" for k, v in dimensions.items())
        kp_desc = "\n".join(f"- {kp}" for kp in key_points) if key_points else "- 无特定关键点"
        
        prompt = f"""你是一个严格的 Benchmark 评分员。请评估以下 AI 输出质量。

## 任务
{task.title}
{task.description}

## 预期关键点
{kp_desc}

## AI 输出
{content[:3000]}

## 评分维度及权重
{dims_desc}

请逐维度评分（0-100 分），并给出总分。输出 JSON：
{{"completeness": <int>, "accuracy": <int>, "presentation": <int>, "total": <int>, "reason": "<简要评分理由>"}}"""
        
        reply = adapter.chat(prompt, purpose="benchmark_score")
        if not reply:
            return None
        
        import json as _json
        # Extract JSON from reply
        cleaned = reply.strip()
        if "```" in cleaned:
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()
        scores = _json.loads(cleaned)
        
        completeness = int(scores.get("completeness", 60))
        accuracy = int(scores.get("accuracy", 60))
        presentation = int(scores.get("presentation", 60))
        total = int(scores.get("total", 0))
        
        # Weighted score
        weighted = (
            completeness * dimensions.get("completeness", 0.34) +
            accuracy * dimensions.get("accuracy", 0.33) +
            presentation * dimensions.get("presentation", 0.33)
        )
        total = total or round(weighted)
        
        normalized = total / 100.0
        passed = normalized >= threshold
        
        return BenchScore(
            task_id=task.task_id,
            benchmark_id="",
            method=SCORE_METHOD_LLM_JUDGE,
            score=float(total),
            normalized=round(normalized, 4),
            passed=passed,
            passing_threshold=threshold,
            details=scores.get("reason", "")[:200],
        )
    except Exception as exc:
        logger.debug("[BENCHMARK] LLM scorer failed: %s", exc)
        return None


def _heuristic_llm_fallback(task: BenchmarkTask, content: str, key_points: list[str], dimensions: dict, threshold: float) -> BenchScore:
    """Heuristic fallback when LLM is unavailable."""
    total_max = 100.0
    score = 0.0
    
    # Content length scoring
    if len(content) > 2000:
        score += 30
    elif len(content) > 1000:
        score += 20
    elif len(content) > 500:
        score += 10
    
    # Key point coverage
    if key_points:
        matched = sum(1 for kp in key_points if kp.lower() in content.lower())
        score += (matched / len(key_points)) * 40
    
    # Structure detection (has sections, bullet points, etc.)
    has_structure = any(marker in content for marker in ["##", "###", "- ", "1.", "**"])
    if has_structure:
        score += 15
    
    # Code/output block detection
    if "```" in content or "`" in content:
        score += 15
    
    total = min(score, total_max)
    normalized = total / total_max
    passed = normalized >= threshold
    
    return BenchScore(
        task_id=task.task_id,
        benchmark_id="",
        method=SCORE_METHOD_LLM_JUDGE,
        score=round(total, 2),
        normalized=round(normalized, 4),
        passed=passed,
        passing_threshold=threshold,
        details=f"heuristic: len={len(content)} keypoints_matched={sum(1 for kp in key_points if kp.lower() in content.lower())}/{len(key_points)}" if key_points else "",
    )


# ── 注册默认评分器 ─────────────────────────────────────────────────────

def _init_scorers() -> None:
    register_scorer(SCORE_METHOD_RUBRIC, _rubric_scorer)
    register_scorer(SCORE_METHOD_EXACT_MATCH, _exact_match_scorer)
    register_scorer(SCORE_METHOD_FUZZY_MATCH, _fuzzy_match_scorer)
    register_scorer(SCORE_METHOD_LLM_JUDGE, _llm_judge_scorer)


_init_scorers()


# ── 公开 API ───────────────────────────────────────────────────────────


def score_benchmark_run(
    protocol: BenchmarkProtocol,
    task_results: dict[str, JsonDict],
    metadata: JsonDict | None = None,
) -> BenchScoreSet:
    """对一个 benchmark run 的全部任务结果进行评分。

    Args:
        protocol: Benchmark 协议定义
        task_results: {task_id: output_dict} 映射
        metadata: 额外元数据

    Returns:
        BenchScoreSet 包含全部评分结果
    """
    scores: list[BenchScore] = []
    total_weight = 0.0
    weighted_score = 0.0

    for task in protocol.tasks:
        output = task_results.get(task.task_id, {})
        method = task.scoring_rules.get("method", SCORE_METHOD_RUBRIC)
        scorer = _SCORERS.get(method, _rubric_scorer)

        try:
            score = scorer(task, output)
            score.task_id = task.task_id
            score.benchmark_id = protocol.benchmark_id
        except Exception as exc:
            logger.error("[BENCHMARK] 评分失败 task=%s: %s", task.task_id, exc)
            score = BenchScore(
                task_id=task.task_id,
                benchmark_id=protocol.benchmark_id,
                method="error",
                score=0.0,
                normalized=0.0,
                passed=False,
                error=str(exc),
            )

        scores.append(score)

        weight = float(task.scoring_rules.get("weight", 1.0))
        total_weight += weight
        weighted_score += score.score * weight

    passed = [s for s in scores if s.passed]

    return BenchScoreSet(
        benchmark_id=protocol.benchmark_id,
        suite_version=protocol.version,
        scores=scores,
        total_score=round(weighted_score, 2),
        max_score=round(100.0 * total_weight, 2),
        normalized_total=round(weighted_score / (100.0 * total_weight) if total_weight > 0 else 0, 4),
        passed_count=len(passed),
        total_count=len(scores),
        scored_at=datetime.now().isoformat(),
        metadata=metadata or {},
    )


def format_score_report(score_set: BenchScoreSet) -> str:
    """格式化评分报告为可读字符串。"""
    lines = [
        "╔══════════════════════════════════════════╗",
        f"║  Benchmark 评分报告                       ║",
        "╚══════════════════════════════════════════╝",
        "",
        f"  套件: {score_set.benchmark_id}",
        f"  版本: {score_set.suite_version}",
        f"  评分时间: {score_set.scored_at}",
        "",
        f"  📊 总分: {score_set.total_score:.1f}/{score_set.max_score:.1f}  "
        f"({score_set.normalized_total:.1%})",
        f"  ✅ 通过: {score_set.passed_count}/{score_set.total_count}  "
        f"({score_set.pass_rate:.1%})",
        "",
        "  ── 详细评分 ──",
    ]

    for s in score_set.scores:
        status_icon = "✅" if s.passed else "❌"
        lines.append(f"  {status_icon} {s.task_id}: {s.score:.1f}/100 ({s.normalized:.1%})")
        if s.details:
            lines.append(f"      {s.details}")
        if s.error:
            lines.append(f"      ⚠ {s.error}")

        for rubric_item in s.rubric_scores:
            bar = "█" * int(rubric_item.normalized * 10) + "░" * (10 - int(rubric_item.normalized * 10))
            lines.append(f"      ├ {rubric_item.item}: {rubric_item.score}/{rubric_item.max_score} "
                         f"[{bar}] {rubric_item.comment}")

    lines.append("")
    return "\n".join(lines)
