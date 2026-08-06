"""Architecture Mapper — 分析外部系统架构，对照自身，生成改进方案。

输入：外部系统架构描述 + 自身架构描述（self_description.py）
输出：结构化改进方案列表
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── 核心函数 ────────────────────────────────────────────────────────

_MAPPING_PROMPT = """你是一个架构分析映射器。分析外部系统的架构设计，对照 Partner AI Agent 的当前架构，
识别差距并生成具体的改进方案。

Partner 当前架构：
{self_arch}

外部系统架构信息：
{external_info}

请分析：

1. 外部系统的核心架构设计原则是什么？
2. 对照 Partner 的当前架构，哪些原则 Partner 已经具备、哪些存在差距？
3. 对于每个差距，是否值得借鉴、是否可以落地到 Partner？

对每个改进方案，给出以下信息：
- external_pattern: 外部系统的设计模式名称
- external_source: 外部系统的具体做法
- current_state: Partner 当前的对应做法
- gap: 差距分析
- apply_to: 改进适用范围（event_parallelism | step_dependency | micro_planner | harness_execution | prompt_template | module_interface | learning_mechanism）
- proposed_change: 具体的改进方案描述
- implementation_level: 实施层面（config | prompt | code）
- estimated_effort: 工作量评估（low | medium | high）
- confidence: 置信度（0-1）

输出 JSON 数组：
[
  {{
    "id": "imp_001",
    "external_pattern": "...",
    "external_source": "...",
    "current_state": "...",
    "gap": "...",
    "apply_to": "event_parallelism",
    "proposed_change": "...",
    "implementation_level": "config",
    "estimated_effort": "medium",
    "confidence": 0.85
  }}
]

如果没有可借鉴的改进点，输出空数组 []。
"""


def analyze(external_info: str) -> list[dict[str, Any]]:
    """分析外部架构，返回改进方案列表。

    Args:
        external_info: 外部系统的架构描述（文本）。

    Returns:
        改进方案列表，每个元素包含 id、external_pattern、proposed_change 等字段。
    """
    if not external_info or not external_info.strip():
        logger.warning("[ARCH_MAPPER] empty external_info")
        return []

    try:
        from .self_description import describe_for_prompt
        from ..adapters.adapter import get_adapter

        self_arch = describe_for_prompt()
        prompt = _MAPPING_PROMPT.format(self_arch=self_arch, external_info=external_info[:6000])
        from ..adapters.adapter import HermesAdapter
        adapter = HermesAdapter()
        if not adapter:
            logger.warning("[ARCH_MAPPER] no adapter available")
            return _fallback_rule_based(external_info)

        reply = adapter.chat(prompt, purpose="architecture_mapping")
        if not reply or not reply.strip():
            return _fallback_rule_based(external_info)

        # Parse JSON from LLM response
        cleaned = reply.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        cleaned = cleaned.strip()

        improvements = json.loads(cleaned)
        if isinstance(improvements, list):
            # Assign IDs and timestamp
            for i, imp in enumerate(improvements):
                imp["id"] = imp.get("id", f"imp_{i+1:03d}")
                imp["created_at"] = datetime.now().isoformat()
            logger.info("[ARCH_MAPPER] generated %d improvements", len(improvements))
            return improvements
        return _fallback_rule_based(external_info)

    except Exception as e:
        logger.error("[ARCH_MAPPER] failed: %s", e)
        return _fallback_rule_based(external_info)


def _fallback_rule_based(external_info: str) -> list[dict]:
    """当 LLM 不可用时，基于规则做简单的架构分析。"""
    info_lower = external_info.lower()
    improvements = []

    # Detect multi-track/parallel patterns
    if any(kw in info_lower for kw in ["multi-track", "parallel", "multi-head", "multi-channel", "多轨道", "并行"]):
        improvements.append({
            "id": "imp_fb_001",
            "external_pattern": "多轨道/并行处理",
            "external_source": "外部系统使用多轨道或并行处理架构",
            "current_state": "Partner Event 流水线为单轨道顺序执行",
            "gap": "无法同时处理不同类型的子任务",
            "apply_to": "event_parallelism",
            "proposed_change": "引入 Event 分类标签（data/analysis/generation/validation），同类 Event 优先并行",
            "implementation_level": "config",
            "risk_level": "low",
            "estimated_effort": "medium",
            "confidence": 0.6,
            "created_at": datetime.now().isoformat(),
        })

    # Detect explicit interface patterns
    if any(kw in info_lower for kw in ["interface", "contract", "schema", "type-check", "显式接口", "类型声明"]):
        improvements.append({
            "id": "imp_fb_002",
            "external_pattern": "显式接口契约",
            "external_source": "外部系统模块间使用显式类型声明和接口契约通信",
            "current_state": "Partner 使用隐式 $step_X.field 引用",
            "gap": "无类型检查，运行时才能发现接口不匹配",
            "apply_to": "step_dependency",
            "proposed_change": "step parameters 增加 input_type/output_type 声明字段",
            "implementation_level": "prompt",
            "risk_level": "medium",
            "estimated_effort": "medium",
            "confidence": 0.55,
            "created_at": datetime.now().isoformat(),
        })

    # Detect iterative/adaptive planning
    if any(kw in info_lower for kw in ["iterative", "adaptive", "dynamic", "rollout", "滚动规划", "自适应"]):
        improvements.append({
            "id": "imp_fb_003",
            "external_pattern": "滚动/自适应规划",
            "external_source": "外部系统根据当前状态动态调整后续计划",
            "current_state": "Partner 使用批处理规划（一次性生成 5-10 步）",
            "gap": "无法根据中间结果动态调整方向",
            "apply_to": "micro_planner",
            "proposed_change": "增加 rolling_plan 的调用频率，每完成 2 步重新规划一次",
            "implementation_level": "config",
            "risk_level": "low",
            "estimated_effort": "low",
            "confidence": 0.7,
            "created_at": datetime.now().isoformat(),
        })

    return improvements


def save_improvements(improvements: list[dict]) -> int:
    """将改进方案写入 evolution_rules 表（category='architecture_insight'）
    和 knowledge 表（跨会话知识回溯）。

    Returns:
        写入的规则数量。
    """
    if not improvements:
        return 0

    try:
        from .evolution_db import GLOBAL_DB_PATH
        import sqlite3

        db = sqlite3.connect(GLOBAL_DB_PATH)
        written = 0
        for imp in improvements:
            rule_text = (
                f"[架构借鉴] {imp.get('external_pattern', '?')}: "
                f"{imp.get('proposed_change', '')[:200]}"
            )
            confidence = imp.get("confidence", 0.5)

            # Check for duplicates
            existing = db.execute(
                "SELECT COUNT(*) FROM evolution_rules WHERE rule_text=? AND category='architecture_insight'",
                (rule_text[:200],),
            ).fetchone()[0]

            if existing == 0:
                # Store full improvement JSON in condition column for ArchitectureImprover
                details = json.dumps(imp, ensure_ascii=False)
                db.execute(
                    "INSERT INTO evolution_rules (rule_type, rule_text, category, confidence, condition, created_at) "
                    "VALUES ('architecture', ?, 'architecture_insight', ?, ?, datetime('now'))",
                    (rule_text[:500], confidence, details),
                )
                written += 1

        # Also write to knowledge table for cross-session recall
        try:
            for imp in improvements:
                db.execute(
                    "INSERT INTO knowledge (topic, sources, insights, integration_plan, risk_level) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        f"架构改进: {imp.get('external_pattern', '?')}",
                        json.dumps([imp.get("external_source", "")], ensure_ascii=False),
                        imp.get("proposed_change", ""),
                        json.dumps(imp, ensure_ascii=False),
                        imp.get("risk_level", "low"),
                    ),
                )
        except Exception as _ke:
            logger.debug("[ARCH_MAPPER] knowledge write skipped: %s", _ke)

        db.commit()
        db.close()
        logger.info("[ARCH_MAPPER] saved %d architecture insight rules, %d knowledge entries", written, len(improvements))
        return written

    except Exception as e:
        logger.error("[ARCH_MAPPER] failed to save improvements: %s", e)
        return 0
