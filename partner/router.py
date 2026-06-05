"""Conversation Router - legacy direct-chat router.

The QQ runtime does not use this module for event selection. It is kept for
GUI/core compatibility and returns a neutral ParsedQuery so higher-level LLM
selectors own intent decisions.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Callable
from enum import Enum

from .outbound_policy import UNAVAILABLE_NOTICE


class Intent(Enum):
    """User intent classification."""
    GREETING = "greeting"
    STATUS = "status"
    PROGRESS = "progress"
    KNOWLEDGE = "knowledge"
    DIRECTION = "direction"
    DETAIL = "detail"
    TASK_ADD = "task_add"
    TASK_CANCEL = "task_cancel"
    WORKSPACE = "workspace"
    HELP = "help"
    GENERAL = "general"


@dataclass
class ParsedQuery:
    """Result of intent parsing."""
    intent: Intent
    confidence: float
    query: str
    topic: Optional[str] = None
    params: Optional[Dict] = None


class ConversationRouter:
    """Routes legacy direct-chat messages through an optional LLM callable."""

    def __init__(self, journal, knowledge, task_queue, state):
        self.journal = journal
        self.knowledge = knowledge
        self.task_queue = task_queue
        self.state = state
        # LLM callable: fn(prompt: str) -> str | None
        # If None, _minimal_fallback() is used instead
        self.llm_fn: Optional[Callable[[str], Optional[str]]] = None

    def route(self, query: str) -> str:
        """Classify intent, build an LLM prompt from it, call LLM for response.

        If LLM is unavailable, returns a minimal 1-line fallback.
        """
        parsed = self.parse_intent(query)

        # Try LLM response first
        if self.llm_fn:
            prompt = self._build_prompt(parsed)
            try:
                reply = self.llm_fn(prompt)
                if reply:
                    return reply
            except Exception:
                pass

        # Minimal fallback (1 line, not a template)
        return self._minimal_fallback(parsed)

    def _build_prompt(self, parsed: ParsedQuery) -> str:
        """Build an LLM prompt from the parsed intent + available state data.

        The prompt is structured: role → context → state → query → instructions.
        No hardcoded response templates — the LLM generates naturally.
        """
        lines = ["你是 Partner，一个自主研究的 AI 伙伴。用中文简短自然地回复。\n"]

        # Context: recent activity
        if self.journal:
            recent = self.journal.get_recent(3)
            if recent:
                lines.append("最近活动:")
                for e in recent:
                    lines.append(f"- {e.timestamp[:16]}: {e.task_title}")
                lines.append("")

        # Context: stats
        if self.state:
            stats = self.state.load_stats()
            cycles = stats.get("total_cycles", 0)
            completed = stats.get("total_tasks_completed", 0)
            if cycles or completed:
                lines.append(f"已完成 {cycles} 个研究周期，{completed} 个任务。")

        # Context: knowledge count
        if self.knowledge:
            kb_stats = self.knowledge.stats()
            if kb_stats.get("total", 0) > 0:
                lines.append(f"知识库 {kb_stats['total']} 条。")
            lines.append("")

        # Intent + user message
        intent_labels = {
            Intent.GREETING: "问候",
            Intent.STATUS: "询问进展",
            Intent.KNOWLEDGE: "询问知识",
            Intent.DIRECTION: "改变方向",
            Intent.DETAIL: "请求详情",
            Intent.TASK_ADD: "添加任务",
            Intent.TASK_CANCEL: "取消任务",
            Intent.HELP: "寻求帮助",
            Intent.GENERAL: "日常对话",
        }
        intent_label = intent_labels.get(parsed.intent, "日常对话")
        lines.append(f"[用户意图: {intent_label}]")
        lines.append(f"[用户消息] {parsed.query}")
        if parsed.topic:
            lines.append(f"[话题] {parsed.topic}")
        lines.append("")

        # Instructions
        lines.append("请直接回复用户。不要用markdown，不要用**加粗**。自然简短即可。")
        if parsed.intent == Intent.STATUS:
            lines.append("给出简短的进展总结，提及最近完成的任务和知识库变化。")
        elif parsed.intent == Intent.KNOWLEDGE:
            lines.append(f"如果知识库中有关于「{parsed.topic or parsed.query}」的信息，简要回答；没有就说还没研究过这个方向。")
        elif parsed.intent == Intent.HELP:
            lines.append("说明你可以帮用户做什么：推进研究、查看进展、探索新方向等。")
        elif parsed.intent == Intent.GREETING:
            lines.append("简短打招呼，询问需要什么帮助。")
        elif parsed.intent == Intent.DETAIL:
            lines.append("如果知识库有相关详情，简要介绍关键点。")

        return "\n".join(lines)

    def _minimal_fallback(self, parsed: ParsedQuery) -> str:
        """Only fallback when the LLM is unavailable."""
        return UNAVAILABLE_NOTICE

    def parse_intent(self, query: str) -> ParsedQuery:
        """Return a neutral intent; runtime selection belongs to LLM selectors."""
        query_stripped = query.strip()
        return ParsedQuery(intent=Intent.GENERAL, confidence=0.5, query=query_stripped)
