"""GapDiscovery — systematic gap detection for Partner's self-evolution system.

Part of the 5-step self-evolution cycle. Detects gaps across five dimensions:

1. **Task-log analysis** — scan task_log.jsonl files for failure rates, timeout rates,
   and skipped-step cascades to identify performance gaps.

2. **Execution-trace analysis** — parse harness_runs.jsonl for step durations that
   exceed configurable thresholds, flagging bottleneck steps.

3. **User-feedback mining** — search dialog_history.jsonl for negative feedback
   patterns (e.g. "太慢了", "卡住了", "不对") that indicate usability gaps.

4. **External-comparison scanning** — compare Partner's current capability inventory
   against a reference set of known tools/frameworks to surface functionality gaps.

5. **Structured gap output** — every detected gap is emitted as a dict with fields:
   {id, type, description, source, severity}.

Gaps are categorised as: *performance* | *functionality* | *usability*.
Severity levels: *critical* | *high* | *medium* | *low*.
Each gap receives a unique human-readable ID (gap_001, gap_002, …).

Usage:
    from partner.evolution.gap_discovery import GapDiscovery

    gaps = await GapDiscovery.discover_all(workspace="/path/to/instance")
    for g in gaps:
        print(f"[{g['severity']}] {g['type']}: {g['description']}")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration defaults ──────────────────────────────────────────────────

_DEFAULT_BOTTLENECK_THRESHOLD_S: float = 30.0  # steps taking >30s are bottlenecks
_DEFAULT_FAILURE_RATE_THRESHOLD: float = 0.4  # >40% failure => performance gap
_DEFAULT_TIMEOUT_RATE_THRESHOLD: float = 0.3  # >30% timeout => performance gap
_DEFAULT_MIN_SAMPLES: int = 3  # minimum task samples before flagging
_DEFAULT_NEGATIVE_PATTERNS: list[str] = [
    # Chinese negative feedback patterns
    "太慢了",
    "卡住了",
    "不对",
    "错了",
    "不好用",
    "没有用",
    "不行",
    "太差",
    "没反应",
    "错误",
    "失败",
    "没效果",
    "不满意",
    "重新做",
    "重做",
    # English negative feedback patterns
    "too slow",
    "stuck",
    "wrong",
    "incorrect",
    "not working",
    "failed",
    "useless",
    "bad",
    "error",
    "hang",
]
_DEFAULT_STEP_EVENT_BLACKLIST: set[str] = {"harness_batch_plan"}  # not a step event

# ── Reference capability catalog for external comparison ────────────────────

_REFERENCE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "AlphaFold": {
        "domain": "蛋白质结构预测",
        "source": "LangChain / DeepMind",
        "description": "蛋白质结构预测，端到端深度学习模型",
    },
    "DiffDock": {
        "domain": "分子对接",
        "source": "GitHub / 学术文献",
        "description": "基于扩散模型的分子对接与结合构象预测",
    },
    "CellChat": {
        "domain": "单细胞通讯分析",
        "source": "R生态 / Bioconductor",
        "description": "单细胞RNA-seq的细胞间通讯网络推断与分析",
    },
    "LangChain Tool Integration": {
        "domain": "工具调用编排",
        "source": "LangChain框架",
        "description": "声明式工具注册、自动工具选择、错误重试与回退机制",
    },
    "AutoGen Multi-Agent": {
        "domain": "多Agent协作",
        "source": "AutoGen / Microsoft",
        "description": "多Agent对话、任务委派、群体决策与辩论机制",
    },
    "LlamaIndex Data Ingestion": {
        "domain": "数据接入",
        "source": "LlamaIndex框架",
        "description": "多源数据连接器（PDF/DB/API/网页），自动元数据提取与索引",
    },
    "PyMOL Visualization": {
        "domain": "分子可视化",
        "source": "开源工具",
        "description": "蛋白质结构与分子三维结构交互式可视化与渲染",
    },
    "OpenMM MD Simulation": {
        "domain": "分子动力学模拟",
        "source": "开源Python库",
        "description": "高性能GPU加速分子动力学模拟与自由能计算",
    },
    "Seurat Integration": {
        "domain": "单细胞分析",
        "source": "R生态 / Satija Lab",
        "description": "单细胞RNA-seq质控、标准化、聚类与差异分析",
    },
    "GATK Best Practices": {
        "domain": "基因组变异分析",
        "source": "Broad Institute",
        "description": "胚系与体细胞变异检测的标准GATK管线",
    },
}


class GapDiscovery:
    """Systematic gap detection for Partner's self-evolution cycle.

    Provides class/static methods that scan workspace artifacts
    (task logs, execution traces, dialog history) and compare against
    an external reference catalog to produce a structured list of gaps.

    Each gap dict has the schema::

        {
            "id": "gap_001",
            "type": "performance" | "functionality" | "usability",
            "description": "Human-readable description of the gap",
            "source": "task_log | execution_trace | user_feedback | external_comparison",
            "severity": "critical" | "high" | "medium" | "low",
            "detail": { ... }  # extra context (optional)
        }
    """

    _id_counter: int = 0

    # ── Public entry points ─────────────────────────────────────────────

    @classmethod
    async def discover_all(
        cls,
        workspace: str,
        bottleneck_threshold_s: float = _DEFAULT_BOTTLENECK_THRESHOLD_S,
        failure_rate_threshold: float = _DEFAULT_FAILURE_RATE_THRESHOLD,
        timeout_rate_threshold: float = _DEFAULT_TIMEOUT_RATE_THRESHOLD,
        min_samples: int = _DEFAULT_MIN_SAMPLES,
        negative_patterns: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Run all five gap-detection strategies and return unioned results.

        Args:
            workspace: Absolute path to the Partner instance workspace.
            bottleneck_threshold_s: Step durations above this (seconds) are
                flagged as bottlenecks.
            failure_rate_threshold: Task-type failure rates above this
                trigger a performance gap (0.0 – 1.0).
            timeout_rate_threshold: Task-type timeout rates above this
                trigger a performance gap (0.0 – 1.0).
            min_samples: Minimum number of task observations required before
                a gap is declared for a given task type.
            negative_patterns: Custom list of negative feedback substrings
                to search for in dialog history. Falls back to defaults.

        Returns:
            List of structured gap dicts, deduplicated by description.
        """
        cls._id_counter = 0  # reset per run
        all_gaps: list[dict[str, Any]] = []
        seen_descriptions: set[str] = set()

        # Run detectors concurrently — they are I/O-bound scans
        detectors = await asyncio.gather(
            cls._detect_task_log_gaps(
                workspace,
                failure_rate_threshold=failure_rate_threshold,
                timeout_rate_threshold=timeout_rate_threshold,
                min_samples=min_samples,
            ),
            cls._detect_execution_bottlenecks(
                workspace,
                threshold_s=bottleneck_threshold_s,
            ),
            cls._detect_user_feedback_gaps(
                workspace,
                patterns=negative_patterns or _DEFAULT_NEGATIVE_PATTERNS,
            ),
            cls._detect_external_comparison_gaps(),
        )

        for gap_list in detectors:
            for gap in gap_list:
                desc = gap.get("description", "")
                if desc and desc not in seen_descriptions:
                    seen_descriptions.add(desc)
                    all_gaps.append(gap)

        # Sort by severity (critical → low)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        all_gaps.sort(key=lambda g: severity_order.get(g.get("severity", "low"), 99))
        return all_gaps

    # ── Gap ID generation ───────────────────────────────────────────────

    @classmethod
    def _next_gap_id(cls) -> str:
        """Generate a unique gap ID in the form ``gap_001``, ``gap_002``, …"""
        cls._id_counter += 1
        return f"gap_{cls._id_counter:03d}"

    # ── Detector 1: task_log.jsonl analysis ─────────────────────────────

    @classmethod
    async def _detect_task_log_gaps(
        cls,
        workspace: str,
        failure_rate_threshold: float,
        timeout_rate_threshold: float,
        min_samples: int,
    ) -> list[dict[str, Any]]:
        """Scan all task_log.jsonl files for failure / timeout patterns.

        Walks ``{workspace}/state/tasks/*/task_log.jsonl``, counts step
        outcomes per task type (inferred from title in harness_batch_plan
        events), and flags task types exceeding failure / timeout thresholds.

        Returns:
            List of performance-type gap dicts.
        """
        gaps: list[dict[str, Any]] = []
        tasks_dir = os.path.join(workspace, "state", "tasks")
        if not os.path.isdir(tasks_dir):
            logger.debug("[GAP_DISCOVERY] tasks dir not found: %s", tasks_dir)
            return gaps

        # Phase 1: collect raw step outcomes
        outcome_counts: dict[str, Counter[str]] = defaultdict(Counter)
        # Counter keys: 'ok', 'fail', 'timeout', 'skipped', 'unknown'

        task_dirs = [
            os.path.join(tasks_dir, d)
            for d in os.listdir(tasks_dir)
            if os.path.isdir(os.path.join(tasks_dir, d))
        ]

        # Use a thread pool executor to scan log files in parallel
        loop = asyncio.get_running_loop()
        results = await asyncio.gather(
            *[
                loop.run_in_executor(None, cls._parse_single_task_log, td)
                for td in task_dirs
            ],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                logger.debug("[GAP_DISCOVERY] task log parse error: %s", result)
                continue
            if not isinstance(result, tuple) or result is None:
                continue
            task_type, outcomes = result
            outcome_counts[task_type].update(outcomes)

        # Phase 2: build gap records from aggregated stats
        for task_type, counts in outcome_counts.items():
            total = sum(counts.values())
            if total < min_samples:
                continue  # not enough data

            failures = counts.get("fail", 0)
            timeouts = counts.get("timeout", 0)
            ok_count = counts.get("ok", 0)
            skipped = counts.get("skipped", 0)

            failure_rate = failures / max(total, 1)
            timeout_rate = timeouts / max(total, 1)
            ok_rate = ok_count / max(total, 1)

            # --- Failure-rate gap (performance) ---
            if failure_rate >= failure_rate_threshold:
                severity = "critical" if failure_rate >= 0.7 else "high"
                gaps.append(cls._make_gap(
                    type_="performance",
                    description=(
                        f"任务类型「{task_type}」步骤失败率 {failure_rate:.0%} "
                        f"(失败 {failures}/{total} 步)，超过 {failure_rate_threshold:.0%} 阈值"
                    ),
                    source="task_log",
                    severity=severity,
                    detail={
                        "task_type": task_type,
                        "total_steps": total,
                        "failures": failures,
                        "failure_rate": round(failure_rate, 4),
                        "timeouts": timeouts,
                        "timeout_rate": round(timeout_rate, 4),
                        "ok_rate": round(ok_rate, 4),
                        "skipped": skipped,
                    },
                ))

            # --- Timeout-rate gap (performance) ---
            if timeout_rate >= timeout_rate_threshold:
                gaps.append(cls._make_gap(
                    type_="performance",
                    description=(
                        f"任务类型「{task_type}」步骤超时率 {timeout_rate:.0%} "
                        f"(超时 {timeouts}/{total} 步)，超过 {timeout_rate_threshold:.0%} 阈值"
                    ),
                    source="task_log",
                    severity="high",
                    detail={
                        "task_type": task_type,
                        "total_steps": total,
                        "timeouts": timeouts,
                        "timeout_rate": round(timeout_rate, 4),
                        "failures": failures,
                        "failure_rate": round(failure_rate, 4),
                    },
                ))

            # --- High skip rate — cascading failures (performance) ---
            skip_rate = skipped / max(total, 1)
            if skip_rate >= 0.4 and total >= min_samples:
                gaps.append(cls._make_gap(
                    type_="performance",
                    description=(
                        f"任务类型「{task_type}」步骤依赖级联跳过率 {skip_rate:.0%} "
                        f"(跳过 {skipped}/{total} 步)，表明早期步骤失败导致整个管线中断"
                    ),
                    source="task_log",
                    severity="high",
                    detail={
                        "task_type": task_type,
                        "skipped_steps": skipped,
                        "skip_rate": round(skip_rate, 4),
                        "total_steps": total,
                    },
                ))

        return gaps

    @classmethod
    def _parse_single_task_log(cls, task_dir: str) -> tuple[str, list[str]] | None:
        """Parse one task_log.jsonl and return (task_type, list_of_step_outcomes).

        Reads the batch plan event (``harness_batch_plan``) to infer the
        task type from the ``title`` field, then collects the ``kind`` or
        ``ok`` field of subsequent ``harness_step`` events.

        Returns:
            Tuple of (task_type, [outcome_str, …]) or None if not parseable.
        """
        log_path = os.path.join(task_dir, "task_log.jsonl")
        if not os.path.isfile(log_path):
            return None

        task_type: str = "未分类"
        outcomes: list[str] = []

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event = entry.get("event", "")

                    # Extract task type from the batch plan title
                    if event == "harness_batch_plan":
                        title: str = entry.get("title", "")
                        if title:
                            task_type = title.strip()
                        continue

                    # Classify step outcomes
                    if event == "harness_step":
                        kind: str = entry.get("kind", "")
                        ok_val = entry.get("ok", True)
                        result_preview = entry.get("result_preview", "")

                        if kind == "skipped":
                            outcomes.append("skipped")
                        elif not ok_val:
                            # Check if the error message mentions timeout
                            err = result_preview.lower()
                            if "timeout" in err or "timed out" in err:
                                outcomes.append("timeout")
                            else:
                                outcomes.append("fail")
                        elif ok_val and kind != "skipped":
                            outcomes.append("ok")
                        else:
                            outcomes.append("unknown")
        except Exception as exc:
            logger.debug(
                "[GAP_DISCOVERY] error parsing %s: %s", log_path, exc,
            )
            return None

        return (task_type, outcomes) if outcomes else None

    # ── Detector 2: execution-trace bottleneck analysis ─────────────────

    @classmethod
    async def _detect_execution_bottlenecks(
        cls,
        workspace: str,
        threshold_s: float,
    ) -> list[dict[str, Any]]:
        """Analyse harness_runs.jsonl for steps that exceed the duration threshold.

        Computes step duration by subtracting consecutive step timestamps
        for the same batch plan. Steps whose estimated duration is above
        the threshold are flagged as bottlenecks.

        Args:
            workspace: Instance workspace path.
            threshold_s: Duration threshold in seconds.

        Returns:
            List of performance-type bottleneck gap dicts.
        """
        gaps: list[dict[str, Any]] = []
        harness_path = os.path.join(workspace, "state", "harness_runs.jsonl")
        if not os.path.isfile(harness_path):
            logger.debug("[GAP_DISCOVERY] harness_runs.jsonl not found: %s", harness_path)
            return gaps

        # Parse all entries grouped by batch plan
        entries: list[dict[str, Any]] = []
        try:
            with open(harness_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.debug("[GAP_DISCOVERY] error reading harness_runs: %s", exc)
            return gaps

        if not entries:
            return gaps

        # Group entries by batch-plan title → list of (ts, event_type, step_id, ok)
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in entries:
            event = e.get("event", "")
            title = e.get("title", "")
            if event == "harness_batch_plan":
                # Use task_id to group
                task_id = e.get("task_id", "")
                groups[task_id].append(e)
            elif event == "harness_step":
                task_id = e.get("task_id", "")
                groups[task_id].append(e)

        for task_id, group_entries in groups.items():
            # Sort by timestamp
            group_entries.sort(key=lambda x: x.get("ts", ""))

            prev_ts: float | None = None
            prev_step_id: str | None = None
            step_durations: list[dict[str, Any]] = []

            for e in group_entries:
                ts_str = e.get("ts", "")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str).timestamp()
                except (ValueError, TypeError):
                    try:
                        ts = time.mktime(
                            datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S").timetuple()
                        )
                    except (ValueError, TypeError, IndexError):
                        continue

                event_type = e.get("event", "")
                step_id = e.get("step_id", "")
                ok_val = e.get("ok", True)

                if event_type == "harness_batch_plan":
                    # Batch plan itself is not a step
                    prev_ts = ts
                    prev_step_id = "plan"
                    continue

                if event_type in _DEFAULT_STEP_EVENT_BLACKLIST:
                    prev_ts = ts
                    continue

                if prev_ts is not None and prev_step_id is not None:
                    duration = ts - prev_ts
                    step_durations.append({
                        "step_id": step_id or prev_step_id,
                        "event_type": event_type,
                        "duration_s": round(duration, 1),
                        "ok": ok_val,
                        "timestamp": ts_str,
                    })

                prev_ts = ts
                prev_step_id = step_id

            # Check for slow steps
            if not step_durations:
                continue

            # Sort by duration descending, take top 3
            step_durations.sort(key=lambda x: x["duration_s"], reverse=True)
            slow_steps = [s for s in step_durations if s["duration_s"] > threshold_s]

            if slow_steps:
                worst = slow_steps[0]
                slow_count = len(slow_steps)
                all_count = len(step_durations)
                avg_duration = sum(s["duration_s"] for s in step_durations) / max(all_count, 1)

                # Extract a task type hint from the first entry's title
                task_type_hint = "未知"
                for e in group_entries:
                    title = e.get("title", "")
                    if title:
                        task_type_hint = title.strip()
                        break

                severity: str = "critical" if worst["duration_s"] > threshold_s * 3 else "high"
                gaps.append(cls._make_gap(
                    type_="performance",
                    description=(
                        f"执行瓶颈：任务「{task_type_hint}」中步骤 "
                        f"'{worst['step_id']}' 耗时 {worst['duration_s']}s"
                        f"（阈值 {threshold_s}s）。共 {slow_count}/{all_count} 步超时，"
                        f"平均每步 {avg_duration:.1f}s"
                    ),
                    source="execution_trace",
                    severity=severity,
                    detail={
                        "task_id": task_id,
                        "task_type_hint": task_type_hint,
                        "worst_step": worst["step_id"],
                        "worst_duration_s": worst["duration_s"],
                        "bottleneck_count": slow_count,
                        "total_steps": all_count,
                        "avg_step_duration_s": round(avg_duration, 1),
                        "slow_steps": slow_steps[:5],  # top 5
                    },
                ))

        return gaps

    # ── Detector 3: user-feedback mining ────────────────────────────────

    @classmethod
    async def _detect_user_feedback_gaps(
        cls,
        workspace: str,
        patterns: list[str],
    ) -> list[dict[str, Any]]:
        """Scan dialog_history.jsonl for negative user feedback patterns.

        Args:
            workspace: Instance workspace path.
            patterns: List of substrings to search for in user messages.

        Returns:
            List of usability-type gap dicts.
        """
        gaps: list[dict[str, Any]] = []
        dialog_path = os.path.join(workspace, "state", "dialog_history.jsonl")
        if not os.path.isfile(dialog_path):
            logger.debug("[GAP_DISCOVERY] dialog_history.jsonl not found: %s", dialog_path)
            return gaps

        # Compile a single combined pattern for efficiency
        joined_pattern = "|".join(re.escape(p) for p in patterns)
        try:
            regex = re.compile(joined_pattern, re.IGNORECASE)
        except re.error:
            logger.warning("[GAP_DISCOVERY] invalid negative pattern regex")
            return gaps

        pattern_hits: Counter[str] = Counter()
        total_user_messages: int = 0
        sample_messages: list[dict[str, Any]] = []

        try:
            with open(dialog_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    role = entry.get("role", "")
                    if role != "user":
                        continue

                    total_user_messages += 1
                    content: str = entry.get("content", "") or ""
                    matches = regex.findall(content)
                    if matches:
                        for m in matches:
                            pattern_hits[m] += 1
                        if len(sample_messages) < 5:
                            # Keep a few exemplars for context
                            sample_messages.append({
                                "content_preview": content[:200],
                                "matched_patterns": matches,
                                "timestamp": entry.get("timestamp", ""),
                            })
        except Exception as exc:
            logger.debug("[GAP_DISCOVERY] error reading dialog history: %s", exc)
            return gaps

        if not pattern_hits:
            return gaps

        total_negative_messages = sum(pattern_hits.values())
        negative_ratio = total_negative_messages / max(total_user_messages, 1)

        # Determine severity based on hit frequency
        severity: str
        if negative_ratio >= 0.3:
            severity = "critical"
        elif negative_ratio >= 0.15:
            severity = "high"
        elif negative_ratio >= 0.05:
            severity = "medium"
        else:
            severity = "low"

        # Summarise top patterns
        top_patterns = pattern_hits.most_common(5)
        patterns_desc = "、".join(f"'{p}' ({c}次)" for p, c in top_patterns)

        gaps.append(cls._make_gap(
            type_="usability",
            description=(
                f"用户负面反馈模式检测到 {total_negative_messages} 次命中，"
                f"占用户消息的 {negative_ratio:.1%}。高频模式：{patterns_desc}"
            ),
            source="user_feedback",
            severity=severity,
            detail={
                "total_user_messages": total_user_messages,
                "total_negative_hits": total_negative_messages,
                "negative_ratio": round(negative_ratio, 4),
                "pattern_breakdown": dict(pattern_hits.most_common(10)),
                "sample_messages": sample_messages,
            },
        ))

        return gaps

    # ── Detector 4: external-comparison scanning ────────────────────────

    @classmethod
    async def _detect_external_comparison_gaps(cls) -> list[dict[str, Any]]:
        """Compare Partner's known capabilities against the reference catalog.

        This method checks the reference catalog (``_REFERENCE_CAPABILITIES``)
        and flags capabilities that are not yet implemented or documented
        in Partner. Since the actual Partner capability inventory must be
        provided externally or discovered at runtime, this detector emits
        gaps for the *full reference catalog* by default, then any runtime
        integration (e.g. via ``SelfEvolveEngine``) can pass a list of
        already-covered capabilities to suppress known gaps.

        Returns:
            List of functionality-type gap dicts for uncovered capabilities.
        """
        gaps: list[dict[str, Any]] = []

        # Attempt to discover Partner's current capabilities from the
        # SelfReview module (lazy import to avoid circular dependency).
        covered_domains: set[str] = set()
        try:
            from partner.evolution.self_review import SelfReview  # type: ignore

            review = SelfReview()
            inventory = review.generate_capability_inventory()
            for agent in inventory.agents:
                for cap in agent.get("capabilities", []):
                    covered_domains.add(cap.lower())
            # Also add weakness domain mentions
            for w in inventory.weaknesses:
                covered_domains.add(w.lower())
        except ImportError:
            logger.debug(
                "[GAP_DISCOVERY] SelfReview not available — "
                "falling back to full reference catalog"
            )
        except Exception as exc:
            logger.debug(
                "[GAP_DISCOVERY] SelfReview inventory failed: %s", exc
            )

        for cap_name, meta in _REFERENCE_CAPABILITIES.items():
            domain: str = meta.get("domain", "")
            source: str = meta.get("source", "")
            description: str = meta.get("description", "")

            # Simple domain-based overlap check
            domain_keywords = set(domain.lower().split())
            is_covered = bool(
                domain_keywords & covered_domains
                or any(domain.lower() in c for c in covered_domains)
            )

            if is_covered:
                continue

            gaps.append(cls._make_gap(
                type_="functionality",
                description=(
                    f"缺少「{cap_name}」能力（{source}）：{description}。"
                    f"Partner 当前在「{domain}」领域无覆盖。"
                ),
                source="external_comparison",
                severity="high" if domain in ("蛋白质结构预测", "单细胞分析", "工具调用编排") else "medium",
                detail={
                    "capability_name": cap_name,
                    "domain": domain,
                    "external_source": source,
                    "description": description,
                },
            ))

        return gaps

    # ── Gap factory ─────────────────────────────────────────────────────

    @classmethod
    def _make_gap(
        cls,
        type_: str,
        description: str,
        source: str,
        severity: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a structured gap record with a unique ID.

        Args:
            type_: Gap category — ``performance``, ``functionality``, or
                ``usability``.
            description: Human-readable gap description.
            source: Detection method — ``task_log``, ``execution_trace``,
                ``user_feedback``, or ``external_comparison``.
            severity: One of ``critical``, ``high``, ``medium``, ``low``.
            detail: Optional dict with extra context.

        Returns:
            A gap dict adhering to the project schema.
        """
        return {
            "id": cls._next_gap_id(),
            "type": type_,
            "description": description,
            "source": source,
            "severity": severity,
            "detail": detail or {},
        }

    # ── Utility: inspect a single workspace ─────────────────────────────

    @classmethod
    async def scan_workspace(
        cls,
        workspace: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """High-level scan: discover all gaps and return a summary report.

        Args:
            workspace: Instance workspace path.
            **kwargs: Forwarded to ``discover_all()``.

        Returns:
            Report dict with keys:
            - ``gaps``: Full list of gap dicts
            - ``count_by_severity``: Dict e.g. ``{"critical": 2, "high": 3, …}``
            - ``count_by_type``: Dict e.g. ``{"performance": 4, …}``
            - ``summary``: Short human-readable string
        """
        gaps = await cls.discover_all(workspace, **kwargs)

        count_by_severity: Counter[str] = Counter(g.get("severity", "unknown") for g in gaps)
        count_by_type: Counter[str] = Counter(g.get("type", "unknown") for g in gaps)

        total = len(gaps)
        critical = count_by_severity.get("critical", 0)
        high = count_by_severity.get("high", 0)

        summary_parts = [f"发现 {total} 个能力缺口"]
        if critical:
            summary_parts.append(f"其中 {critical} 个严重")
        if high:
            summary_parts.append(f"{high} 个高优先级")

        return {
            "gaps": gaps,
            "count_by_severity": dict(count_by_severity),
            "count_by_type": dict(count_by_type),
            "summary": "，".join(summary_parts) if summary_parts else "无能力缺口",
        }
