"""Conversation — 上下文感知对话引擎 + 多轮回复生成。

合并自 conversation.py + response_generator.py。

包含：
- CachedListResult / ResponseGenerator: 多轮对话列表缓存与索引引用
- ConversationEngine: 上下文感知对话引擎，基于 ConversationRouter（LLM 响应，无硬编码）
"""

import re
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from .journal import Journal
from .knowledge import KnowledgeBase, KnowledgeEntry
from .task_queue import TaskQueue
from .state import StateManager
from .router import ConversationRouter, Intent, ParsedQuery
from .dialog import DialogHistory, DialogTurn, ContextManager
from .autocheck import ProactiveNotifier, Notification
from .user_prefs import UserPreferenceStore


# ════════════════════════════════════════════════════════════════
# ResponseGenerator（来自 response_generator.py）
# ════════════════════════════════════════════════════════════════

@dataclass
class CachedListResult:
    """Cached list query results for follow-up index references."""
    topic: str
    entries: List[KnowledgeEntry]
    timestamp: str
    formatted_indices: Dict[int, str] = field(default_factory=dict)


class ResponseGenerator:
    """Generates responses with multi-turn list caching support.

    When a knowledge search returns a list, the results are cached.
    Subsequent "第二个" / "2" / "继续" queries can reference cached items.
    """

    _CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

    INDEX_PATTERNS = [
        (r"^第?(\d+)个?$", None),
        (r"^第([一二两三四五六七八九十]+)个?$", "cn"),
        (r"^number\s*(\d+)$", None),
        (r"^#(\d+)$", None),
        (r"^(\d+)[\.、]?\s*$", None),
    ]

    CONTINUATION_PATTERNS = [
        r"^(继续|然后呢|还有呢|接下来呢|go on|and then|next)$",
        r"^(再往后|下一批|更多)$",
    ]

    ELABORATE_PATTERNS = [
        r"^(详细说说|展开讲讲|具体说说|深入了解|再说说|多说说|说详细点)$",
        r"^(这个|那个|它|它们|this|that|it)$",
        r"^(elaborate|more details?|tell me more)$",
    ]

    PAGE_SIZE = 5

    def __init__(self, knowledge: KnowledgeBase):
        self.knowledge = knowledge
        self._cached_list: Optional[CachedListResult] = None
        self._continuation_offset: int = 0

    def handle_detail(self, topic: str, index: int = None,
                      continuation: bool = False) -> str:
        """Handle a detail/elaborate query."""
        if index is not None:
            return self._resolve_index(index)
        if continuation:
            return self._handle_continuation()
        if not topic:
            if self._cached_list:
                return self._show_cached_list_hint()
            return ("你想详细了解什么？请告诉我具体的话题。\n"
                    "比如：「详细说说 单细胞衰老」")
        results = self.knowledge.search(topic, top_k=10)
        if not results:
            return f"关于「{topic}」没有找到相关内容。"
        self._cached_list = CachedListResult(
            topic=topic,
            entries=results,
            timestamp=self._now_iso(),
            formatted_indices={i + 1: entry.title for i, entry in enumerate(results)},
        )
        self._continuation_offset = 0
        return self._format_list_page(results, topic, page_start=0)

    def resolve_index_from_query(self, query: str) -> Optional[int]:
        query_stripped = query.strip()
        for pattern, fmt in self.INDEX_PATTERNS:
            match = re.match(pattern, query_stripped, re.IGNORECASE)
            if match:
                try:
                    raw = match.group(1)
                    if fmt == "cn":
                        val = self._CN_DIGITS.get(raw)
                        if val is not None:
                            return val
                        continue
                    return int(raw)
                except (ValueError, IndexError):
                    continue
        return None

    def is_continuation(self, query: str) -> bool:
        return any(re.match(p, query.strip(), re.IGNORECASE)
                   for p in self.CONTINUATION_PATTERNS)

    def is_elaborate_request(self, query: str) -> bool:
        return any(re.match(p, query.strip(), re.IGNORECASE)
                   for p in self.ELABORATE_PATTERNS)

    def get_cached_topic(self) -> Optional[str]:
        return self._cached_list.topic if self._cached_list else None

    def has_cached_results(self) -> bool:
        return self._cached_list is not None and len(self._cached_list.entries) > 0

    def clear_cache(self):
        self._cached_list = None
        self._continuation_offset = 0

    def _resolve_index(self, index: int) -> str:
        if not self._cached_list:
            return ("暂时没有缓存的查询结果。请先用「详细说说 X」查询某个话题，"
                    "然后就可以说「第二个」来查看具体条目。")
        entries = self._cached_list.entries
        if index < 1 or index > len(entries):
            return (f"上次查询「{self._cached_list.topic}」只有 "
                    f"{len(entries)} 个结果，请指定 1-{len(entries)} 之间的数字。")
        target = entries[index - 1]
        return self._format_single_entry(target, index, len(entries))

    def _handle_continuation(self) -> str:
        if not self._cached_list:
            return "没有可以继续的结果。请先查询某个话题。"
        entries = self._cached_list.entries
        offset = self._continuation_offset + self.PAGE_SIZE
        if offset >= len(entries):
            return (f"「{self._cached_list.topic}」的所有 "
                    f"{len(entries)} 个结果已经展示完毕。\n"
                    f"你可以说「第一个」「第二个」来查看详情。")
        self._continuation_offset = offset
        remaining = entries[offset:offset + self.PAGE_SIZE]
        return self._format_list_page(remaining, self._cached_list.topic,
                                       page_start=offset, total=len(entries))

    def _format_list_page(self, entries, topic, page_start=0, total=None):
        total = total or len(entries)
        lines = [f"🔍 关于「{topic}」的详细信息：\n"]
        for i, entry in enumerate(entries, start=page_start + 1):
            lines.append(f"  {i}. 【{entry.category}】{entry.title}")
            lines.append(f"     置信度: {entry.confidence}")
            content_preview = (entry.content[:200] + "..." if len(entry.content) > 200 else entry.content)
            lines.append(f"     {content_preview}")
            lines.append("")
        if total > page_start + len(entries):
            lines.append(f"📄 显示 {page_start + 1}-{page_start + len(entries)}"
                         f"（共 {total} 条）。说「继续」查看更多。")
        lines.append("💡 你可以说「第一个」「第二个」来查看某条的详情。")
        return "\n".join(lines)

    def _format_single_entry(self, entry, index, total):
        lines = [f"📖 [{index}/{total}] 【{entry.category}】{entry.title}\n"]
        lines.append(f"来源: {entry.source}")
        lines.append(f"置信度: {entry.confidence}")
        if entry.related_projects:
            lines.append(f"相关项目: {', '.join(entry.related_projects)}")
        if entry.tags:
            lines.append(f"标签: {', '.join(entry.tags)}")
        lines.append("")
        lines.append(entry.content)
        lines.append("")
        if index > 1:
            lines.append(f"⬅️ 说「第{index - 1}个」查看上一条")
        if index < total:
            lines.append(f"➡️ 说「第{index + 1}个」查看下一条")
        lines.append(f"🔙 说「详细说说 {self._cached_list.topic}」返回列表")
        return "\n".join(lines)

    def _show_cached_list_hint(self) -> str:
        if not self._cached_list:
            return "你想详细了解什么？请告诉我具体的话题。\n比如：「详细说说 单细胞衰老」"
        return (f"上次查询的是「{self._cached_list.topic}」（共 {len(self._cached_list.entries)} 条结果）。\n"
                f"你可以说「第一个」「第二个」来查看详情，或者告诉我一个新的话题。")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat()


# ════════════════════════════════════════════════════════════════
# ConversationEngine
# ════════════════════════════════════════════════════════════════

CONTEXT_AWARE_PATTERNS = [
    (r"^(详细说说|展开讲讲|具体说说|深入了解|再说说|多说说|说详细点)$", Intent.DETAIL, 0.95),
    (r"^第([一二两三四五六七八九十]+)个?$", Intent.DETAIL, 0.9),
    (r"^第?(\d+)个?$", Intent.DETAIL, 0.9),
    (r"^(继续|然后呢|还有呢|接下来呢|go on|and then)$", Intent.DETAIL, 0.85),
    (r"^(这个|那个|它|它们|this|that|it)$", Intent.DETAIL, 0.8),
]


class ConversationEngine:
    """Context-aware conversation engine with dialog history.

    Response generation uses router.route() which calls LLM.
    No hardcoded response templates.
    """

    def __init__(self, journal: Journal, knowledge: KnowledgeBase,
                 task_queue: TaskQueue, state: StateManager,
                 workspace: str = ""):
        self.journal = journal
        self.knowledge = knowledge
        self.task_queue = task_queue
        self.state = state

        state_dir = os.path.join(workspace, "state") if workspace else None
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
        history_path = os.path.join(state_dir, "dialog_history.jsonl") if state_dir else None
        self.dialog_history = DialogHistory(history_path)
        self.context = ContextManager(history_path)

        self.response_gen = ResponseGenerator(knowledge)
        self.notifier = ProactiveNotifier(knowledge, journal, state, workspace)

        if state_dir:
            self.user_prefs = UserPreferenceStore(
                os.path.join(state_dir, "user_prefs.json"),
                dialog_history_path=history_path,
            )
        else:
            self.user_prefs = UserPreferenceStore("")

        self.router = ConversationRouter(journal, knowledge, task_queue, state)

    def respond(self, user_message: str) -> str:
        """Main entry point: context-aware conversation response.

        Uses router.route() which calls LLM for response generation.
        No hardcoded templates.
        """
        parsed = self._parse_with_context(user_message)

        from_context = (parsed.params or {}).get("from_context", False)
        if parsed.topic and not from_context:
            self.response_gen.clear_cache()
            self.user_prefs.record_topic_query(parsed.topic)

        self.user_prefs.record_session_turn()

        if self.context:
            self.context.add_turn("user", user_message,
                                  intent=parsed.intent.value, topic=parsed.topic)

        response = self._generate_response(parsed)

        if self.context:
            self.context.add_turn("partner", response)

        return response

    def check_proactive(self) -> List[str]:
        notifications = self.notifier.check_and_notify()
        if not notifications:
            return []
        return [self.notifier.format_notifications(notifications)]

    def _parse_with_context(self, query: str) -> ParsedQuery:
        """Parse intent with context awareness."""
        query_stripped = query.strip()

        for pattern, intent, confidence in CONTEXT_AWARE_PATTERNS:
            match = re.match(pattern, query_stripped)
            if match:
                active_topic = self.context.get_active_topic() if self.context else None
                index = None
                continuation = False
                if match.groups():
                    raw = match.group(1)
                    try:
                        index = int(raw)
                    except (ValueError, IndexError):
                        cn_val = ResponseGenerator._CN_DIGITS.get(raw)
                        if cn_val is not None:
                            index = cn_val
                if self.response_gen.is_continuation(query_stripped):
                    continuation = True

                if active_topic or self.response_gen.has_cached_results():
                    return ParsedQuery(intent=intent, confidence=confidence, query=query,
                                       topic=active_topic,
                                       params={"from_context": True, "index": index,
                                               "continuation": continuation})

        parsed = self.router.parse_intent(query)
        if parsed.confidence >= 0.8:
            return parsed

        fuzzy = self._fuzzy_classify(query)
        if fuzzy:
            return fuzzy

        return parsed

    def _fuzzy_classify(self, query: str) -> Optional[ParsedQuery]:
        intent_keywords = {
            Intent.STATUS: ["状态", "进展", "做了什么", "研究了什么", "最近"],
            Intent.KNOWLEDGE: ["知道", "了解", "什么是", "解释", "区别"],
            Intent.DIRECTION: ["暂停", "切换", "优先", "集中", "重点"],
        }
        best_intent = None
        best_score = 0
        for intent, kw_list in intent_keywords.items():
            score = sum(1 for kw in kw_list if kw in query)
            if score > best_score:
                best_score = score
                best_intent = intent
        if best_intent and best_score >= 2:
            return ParsedQuery(intent=best_intent, confidence=0.7, query=query)
        return None

    def _generate_response(self, parsed: ParsedQuery) -> str:
        """Generate response via LLM (router.route)."""
        if parsed.intent == Intent.DETAIL:
            return self._handle_detail(parsed)
        # Router uses LLM (no hardcoded templates)
        return self.router.route(parsed.query)

    def _handle_detail(self, parsed: ParsedQuery) -> str:
        topic = parsed.topic
        index = parsed.params.get("index") if parsed.params else None
        continuation = (parsed.params or {}).get("continuation", False)
        return self.response_gen.handle_detail(topic, index=index, continuation=continuation)

    # ── Legacy methods (all go through router.route → LLM) ──

    def _handle_status(self) -> str:
        return self.router.route("最近在研究什么？")

    def _handle_progress(self) -> str:
        return self.router.route("任务进展如何？")

    def _handle_knowledge(self, query: str) -> str:
        return self.router.route(f"关于{query}你知道什么？")

    def _handle_direction(self, message: str) -> str:
        return self.router.route(message)

    def _handle_help(self) -> str:
        return self.router.route("帮助")

    def _handle_general(self, message: str) -> str:
        return self.router.route(message)
