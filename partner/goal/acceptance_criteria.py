"""Acceptance Criteria Generator — dynamically generates verification criteria.

Zero task-type assumptions: no "if literature_review" or "if breakthrough" checks.
All domain knowledge lives only in the prompt template and config.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from ..harness_core import RobustExecutor, TaskInstance, load_harness_config

logger = logging.getLogger(__name__)

DEFAULT_ACCEPTANCE_PROMPT = """用户任务：{user_message}

请输出完成该任务后，需要满足哪些具体条件（用简洁的列表形式，每行一个条件）。
条件应可验证，例如：
- "包含至少5种方法"
- "每个方法有MAE值"
- "所有数据引用自真实文献"
- "包含方法对比表格"
- "包含局限性分析"

重要：输出形式判断原则
- 如果用户只是询问信息、建议、查询（如天气、股价、翻译、问答），最终交付物是文字回复，不需要生成文件。
- 如果用户明确要求"报告"、"PDF"、"表格"、"保存"、"文档"，才需要文件输出。
- 不要默认要求 PDF 或文件格式。
- 条件中不要包含"以XX格式呈现"这类格式要求，除非用户明确指定了格式。
不要输出任何额外解释。"""


class AcceptanceCriteriaGenerator:
    """Generates natural-language acceptance criteria from a user message."""

    def __init__(
        self,
        workspace: str,
        adapter: Any,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.workspace = workspace
        self.adapter = adapter
        self.config = config or {}

    async def generate(
        self,
        user_message: str,
        context: str = "",
        *,
        task_instance: TaskInstance | None = None,
    ) -> str:
        """Call LLM to produce acceptance criteria for the given user message.

        Args:
            user_message: The user's original task description.
            context: Optional additional context (workspace summary, etc.).
            task_instance: Optional TaskInstance for logging.

        Returns:
            Natural-language acceptance criteria string (line-per-condition).
        """
        if not self.adapter:
            logger.warning("[ACCEPTANCE] no adapter available; returning fallback criteria")
            return self._fallback_criteria(user_message)

        prompt_template = self._read_prompt("acceptance_criteria.txt", DEFAULT_ACCEPTANCE_PROMPT)
        prompt = prompt_template.replace("{user_message}", str(user_message or "")[:3000])
        if context:
            prompt += "\n\n额外上下文：\n" + str(context)[:2000]

        robust = RobustExecutor(load_harness_config(self.workspace))
        result = await robust.execute(
            event_name="acceptance_criteria",
            task_instance=task_instance or TaskInstance.create(self.workspace, "_criteria_placeholder"),
            operation=lambda: self._adapter_chat_safe(prompt),
            on_timeout="fail_fast",
            on_failure="fail_fast",
            metadata={
                "model": self.config.get("model", "deepseek-v4-flash"),
            },
        )

        if not result.ok:
            logger.warning("[ACCEPTANCE] LLM call failed: %s", result.error)
            return self._fallback_criteria(user_message)

        criteria = str(result.value or "").strip()
        if not criteria:
            return self._fallback_criteria(user_message)

        if task_instance:
            task_instance.append_log("acceptance_criteria_generated", {
                "criteria": criteria,
                "model": self.config.get("model"),
            })

        logger.info("[ACCEPTANCE] generated %s chars of criteria", len(criteria))
        return criteria

    def _adapter_chat_safe(self, prompt: str) -> str:
        """Synchronous wrapper for adapter.chat to use with RobustExecutor."""
        if hasattr(self.adapter, "chat"):
            return self.adapter.chat(prompt, purpose="classify")
        return ""

    @staticmethod
    def _fallback_criteria(user_message: str) -> str:
        """Produce a generic fallback when LLM is unavailable."""
        import re
        text = str(user_message or "")
        lines = [
            "- 产物文件已生成（非空、非诊断性文件）",
            "- 所有结论应有真实数据或引用支撑",
            "- 产物与用户原始目标直接相关",
        ]
        if re.search(r"搜索|检索|查找|find|search|literature|paper", text, re.I):
            lines.append("- 检索结果包含至少3条真实来源（PMID/DOI/URL）")
        if re.search(r"对比|比较|compare|对比分析", text, re.I):
            lines.append("- 包含方法/指标/效果的对比分析")
        if re.search(r"报告|report|综述|review|总结|summarize", text, re.I):
            lines.append("- 包含摘要、方法、结果、局限的完整报告结构")
        if re.search(r"突破|创新|novel|breakthrough|方向", text, re.I):
            lines.append("- 包含突破方向或创新建议分析章节")
        return "\n".join(lines)

    def _read_prompt(self, rel_path: str, fallback: str) -> str:
        """Read a prompt template from repo configs or workspaces."""
        for path in (
            os.path.join(self.workspace, rel_path),
            os.path.join(self.workspace, "config", rel_path),
            os.path.join(os.path.dirname(__file__), "..", "prompts", rel_path),
        ):
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    continue
        return fallback
