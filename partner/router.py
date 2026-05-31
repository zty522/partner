"""Conversation Router — routes user queries to appropriate handlers via LLM.

This module classifies user intent using regex patterns (fast, no LLM needed for routing),
then generates responses via a provided LLM callable. No hardcoded response templates.

Supported intents (classification only, responses are LLM-generated):
  - GREETING: greeting
  - STATUS: "你在干什么？", "最近做了什么？"
  - KNOWLEDGE: "关于 X 你知道什么？"
  - DIRECTION: "暂停 X，集中做 Y"
  - DETAIL: "详细说说 X"
  - TASK_ADD: "添加任务：研究 X"
  - TASK_CANCEL: "取消任务 Y"
  - WORKSPACE: workspace organization
  - HELP: help
  - GENERAL: anything else
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict, Callable
from enum import Enum


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


# Intent classification rules: (pattern, intent, confidence, topic_group)
INTENT_RULES: List[Tuple[str, Intent, float, Optional[int]]] = [
    # Greetings
    (r"^(你好|您好|嗨|hi|hello|hey|早[上啊]?|晚上好|下午好|中午好|在吗|在不在)",
     Intent.GREETING, 0.99, None),
    (r"^(good morning|good afternoon|good evening|morning|afternoon)",
     Intent.GREETING, 0.99, None),

    # Status queries
    (r"(最近|刚才|今天)(在?干|在?做|研究|搞|忙)(了?什么|啥|什么活)", Intent.STATUS, 0.95, None),
    (r"(你在?干什么|你在?做什么|在忙什么|状态|进展如何|进展怎么样|最近在研究什么)", Intent.STATUS, 0.9, None),
    (r"(干嘛呢|忙啥|干啥|在干嘛|最近在干嘛|在忙啥)", Intent.STATUS, 0.95, None),
    (r"(做咋样了|做得咋样了|做怎么样了|搞咋样了|弄咋样了)", Intent.STATUS, 0.98, None),
    (r"(what (have you|did you|are you)|recent|status|progress)", Intent.STATUS, 0.9, None),
    (r"(汇报|总结一下|最近的?进展)", Intent.STATUS, 0.85, None),

    # Progress / task queue queries
    (r"(任务队列|待办|还有多少任务|pending|任务列表)", Intent.PROGRESS, 0.9, None),
    (r"(还有什么(要|需要)做|下一步做什么|接下来做什么)", Intent.PROGRESS, 0.85, None),

    # Knowledge queries
    (r"关于[「『]?(.+?)[」』]?(你)?(知道|了解|学到|发现)了?什么", Intent.KNOWLEDGE, 0.95, 1),
    (r"(什么是|怎么理解|解释一?下?|说说|聊聊)[「『]?(.+?)[」』]?$", Intent.KNOWLEDGE, 0.85, 2),
    (r"^(知道|了解)(?!最近|一下)\s*(.+?)$", Intent.KNOWLEDGE, 0.7, 2),
    (r"(know about|what is|tell me about|explain)\s+(.+)", Intent.KNOWLEDGE, 0.9, 2),
    (r"(区别|对比|比较).+?(和|与|vs)", Intent.KNOWLEDGE, 0.8, None),

    # Direction change
    (r"(暂停|停止|先不做|搁置)[「『]?(.+?)[」』]?(，|,|。|然后|集中|重点|转|去做)", Intent.DIRECTION, 0.95, 2),
    (r"(集中|重点|优先|focus|switch).+?(做|研究|探索)[「『]?(.+?)[」』]?$", Intent.DIRECTION, 0.9, 3),
    (r"(切换到?|转到?|去做)[「『]?(.+?)[」』]?$", Intent.DIRECTION, 0.85, 2),
    (r"(pause|stop|focus on|switch to|prioritize)\s+(.+)", Intent.DIRECTION, 0.9, 2),

    # Detail queries
    (r"(详细说说|具体讲讲|展开讲讲|深入了解|详细说|具体说|展开说|再说说|多说说)[「『]?(.+?)[」』?？]?$", Intent.DETAIL, 0.95, 2),
    (r"(详细|具体|深入|展开|再多说说|说详细点)[地说一讲聊]?\s*[「『]?(.+?)[」』?？]?$", Intent.DETAIL, 0.9, 2),
    (r"(more about|elaborate|details? on|deep dive)\s+(.+)", Intent.DETAIL, 0.9, 2),

    # Workspace organization
    (r"(整理|重组|重构|重新组织|清理|归档)[一二下]?(workspace|工作区|文件|项目|目录|文件夹)", Intent.WORKSPACE, 0.95, None),

    # Task management
    (r"(添加|新建|增加|add)\s*(一个)?\s*任务[：:]?\s*(.+)", Intent.TASK_ADD, 0.95, 3),
    (r"(去研究|去搜索|去查|帮我查|帮我研究)[一下]?\s*(.+)", Intent.TASK_ADD, 0.85, 2),
    (r"(取消|删除|不要了)\s*(任务)?\s*[「『]?(.+?)[」』]?$", Intent.TASK_CANCEL, 0.9, 3),

    # Help
    (r"^(帮助|help|你(能|会|可以)做什么|\?|？)$", Intent.HELP, 0.99, None),
]


class ConversationRouter:
    """Routes user queries to appropriate handlers via LLM.

    Intent classification is regex-based (fast, low cost).
    Response generation uses a provided LLM callable (no hardcoded templates).

    Usage:
        router = ConversationRouter(journal, knowledge, task_queue, state)
        router.llm_fn = lambda prompt: my_llm_call(prompt)
        response = router.route("最近在研究什么？")
    """

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
        """1-line fallback when LLM is unavailable. Not a multi-line template."""
        if parsed.intent == Intent.GREETING:
            return "你好，有什么需要帮助的？"
        if parsed.intent == Intent.STATUS:
            return "完成了几个研究周期，具体让 LLM 来说吧——不过现在 LLM 没连上。"
        if parsed.intent == Intent.HELP:
            return "你可以问我进展、让我研究新方向、或者聊聊科研想法。"
        return "收到。让 LLM 详细回答你。"

    def parse_intent(self, query: str) -> ParsedQuery:
        """Parse user query into structured intent (regex-based, no LLM)."""
        query_stripped = query.strip()
        best_match: Optional[ParsedQuery] = None
        best_confidence = 0.0

        for pattern, intent, confidence, topic_group in INTENT_RULES:
            match = re.search(pattern, query_stripped, re.IGNORECASE)
            if match and confidence > best_confidence:
                topic = match.group(topic_group) if topic_group and topic_group <= len(match.groups()) else None
                best_match = ParsedQuery(
                    intent=intent,
                    confidence=confidence,
                    query=query_stripped,
                    topic=topic.strip() if topic else None,
                )
                best_confidence = confidence

        if best_match:
            return best_match

        # Fallback: check for knowledge-related keywords
        knowledge_keywords = ["知道", "了解", "知识", "发现", "什么是", "怎么", "区别", "对比",
                              "learned", "know about", "what is"]
        if any(k in query_stripped.lower() for k in knowledge_keywords):
            return ParsedQuery(intent=Intent.KNOWLEDGE, confidence=0.6, query=query_stripped)

        return ParsedQuery(intent=Intent.GENERAL, confidence=0.5, query=query_stripped)
