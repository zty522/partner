"""ReportGenerator — produce structured Markdown capability reports.

Generates human-readable capability status reports and integration reports
in Chinese, suitable for CLI display and logging.
"""
from __future__ import annotations

import logging
from typing import Any

from .auto_integrate import IntegrationPlan
from .capability_discovery import SolutionCandidate
from .self_review import CapabilityGap, CapabilityInventory

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates Markdown capability and integration reports."""

    # ── Capability Report ───────────────────────────────────────────

    def generate_capability_report(
        self,
        inventory: CapabilityInventory,
        gaps: list[CapabilityGap],
        candidates: dict[str, list[SolutionCandidate]],
    ) -> str:
        """Generate a full capability report in Markdown format.

        Args:
            inventory: Current capability snapshot.
            gaps: Detected capability gaps.
            candidates: Mapping from gap name to list of solution candidates.
        """
        lines: list[str] = []
        lines.append("## Partner 能力拓展报告")
        lines.append("")

        # ── Summary Section ──
        self._write_summary(lines, inventory)

        # ── Gaps Section ──
        self._write_gaps(lines, gaps, candidates)

        return "\n".join(lines)

    def _write_summary(self, lines: list[str], inventory: CapabilityInventory):
        """Write the capability summary header."""
        agent_count = len(inventory.agents)
        lines.append(f"### 当前能力摘要 ({agent_count} 项)")
        lines.append("")

        for agent in inventory.agents:
            name = agent.get("name", "?")
            caps = ", ".join(agent.get("capabilities", [])) or "无"
            health_status = agent.get("health_status", "unknown")
            health_label = self._health_label(health_status)
            lines.append(f"- Agent: {name} ({caps}) - [{health_label}]")

        if not inventory.agents:
            lines.append("- 尚未注册 Agent")

        lines.append(f"- 已注册 Skill: {inventory.skill_count} 个")
        lines.append(f"- 可用 Event: {len(inventory.event_types)} 种")

        stats = inventory.experience_stats
        total = stats.get("total", 0)
        success_rate = stats.get("success_rate", 0.0)
        success_pct = f"{success_rate:.0%}" if success_rate > 0 else "无数据"
        lines.append(f"- 经验统计: 共 {total} 条, 成功率 {success_pct}")
        lines.append("")

        # Per-type success rates
        by_type = stats.get("by_type", {})
        if by_type:
            lines.append("#### 各类任务成功率")
            lines.append("")
            for task_type, tstats in sorted(by_type.items(), key=lambda x: x[1].get("success_rate", 0)):
                t_total = tstats.get("total", 0)
                t_sr = tstats.get("success_rate", 0.0)
                bar = self._progress_bar(t_sr)
                lines.append(f"- {task_type}: {t_sr:.0%} ({t_total} 条) {bar}")
            lines.append("")

        # Known weaknesses
        if inventory.weaknesses:
            lines.append("#### 已知不足")
            lines.append("")
            for w in inventory.weaknesses[:10]:
                lines.append(f"- {w}")
            lines.append("")

    def _write_gaps(
        self,
        lines: list[str],
        gaps: list[CapabilityGap],
        candidates: dict[str, list[SolutionCandidate]],
    ):
        """Write the identified gaps section with solutions."""
        lines.append(f"### 发现的能力缺口 ({len(gaps)} 项)")
        lines.append("")

        if not gaps:
            lines.append("✅ 目前未检测到明显能力缺口。")
            lines.append("")
            return

        priority_labels = {"high": "高", "medium": "中", "low": "低"}
        difficulty_labels = {"easy": "简单", "medium": "中等", "hard": "困难"}
        value_labels = {"high": "高", "medium": "中", "low": "低"}

        for idx, gap in enumerate(gaps, start=1):
            priority = priority_labels.get(gap.priority, gap.priority)
            lines.append(f"#### 缺口 {idx}: {gap.name}")
            lines.append(f"- 描述: {gap.description}")
            lines.append(f"- 优先级: {priority}")
            lines.append(f"- 检测方式: {gap.detection_method}")
            lines.append("")

            gap_candidates = candidates.get(gap.name, [])
            if gap_candidates:
                lines.append("- 候选方案:")
                for c in gap_candidates:
                    difficulty_str = ""
                    value_str = ""

                    # Pre-computed evaluation hints from name/description
                    lines.append(f"  - **{c.name}**")
                    lines.append(f"    - 来源: {c.source}")
                    lines.append(f"    - 描述: {c.description[:100]}...")
                    if c.url:
                        lines.append(f"    - 链接: {c.url}")
                    if c.stars:
                        lines.append(f"    - ⭐ {c.stars}")
                    if c.language:
                        lines.append(f"    - 语言: {c.language}")

                    # If the candidate name matches a known tool, add pre-evaluated difficulty/value
                    from .capability_discovery import _KNOWN_BIO_TOOLS

                    if c.name in _KNOWN_BIO_TOOLS:
                        info = _KNOWN_BIO_TOOLS[c.name]
                        diff = difficulty_labels.get(info.get("difficulty", ""), info.get("difficulty", ""))
                        val = value_labels.get(info.get("expected_value", ""), info.get("expected_value", ""))
                        lines.append(f"    - 接入难度: {diff}")
                        lines.append(f"    - 预期价值: {val}")
                    lines.append("")
            else:
                lines.append("- 候选方案: 暂无推荐方案")
                lines.append("")

    # ── Integration Report ──────────────────────────────────────────

    def generate_integration_report(self, plan: IntegrationPlan) -> str:
        """Generate a Markdown report for an integration plan."""
        lines: list[str] = []
        lines.append("## 集成计划报告")
        lines.append("")
        lines.append(f"### 目标: {plan.candidate_name}")
        lines.append(f"- 集成类型: {plan.integration_type}")
        lines.append("")

        lines.append("### 执行步骤")
        lines.append("")
        for idx, step in enumerate(plan.steps, start=1):
            lines.append(f"{idx}. {step}")
        lines.append("")

        if plan.test_commands:
            lines.append("### 测试命令")
            lines.append("")
            for idx, cmd in enumerate(plan.test_commands, start=1):
                lines.append(f"{idx}. `{cmd}`")
            lines.append("")

        if plan.files_to_create:
            lines.append("### 待创建文件")
            lines.append("")
            for fspec in plan.files_to_create:
                path = fspec["path"]
                content_preview = fspec["content"][:100].replace("\n", " ").strip()
                lines.append(f"- `{path}`")
                lines.append(f"  - 预览: {content_preview}...")
            lines.append("")

        if plan.rollback_plan:
            lines.append("### 回滚计划")
            lines.append("")
            for idx, step in enumerate(plan.rollback_plan, start=1):
                lines.append(f"{idx}. {step}")
            lines.append("")

        return "\n".join(lines)

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _health_label(status: str) -> str:
        mapping = {
            "ok": "健康",
            "unknown": "未知",
            "unavailable": "未安装",
            "timeout": "超时",
            "error": "异常",
        }
        return mapping.get(status, status)

    @staticmethod
    def _progress_bar(ratio: float, width: int = 20) -> str:
        """Generate a simple text progress bar."""
        filled = int(ratio * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}]"
