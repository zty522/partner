"""Response Generator - multi-turn response generation with list caching.

Provides ResponseGenerator class that:
- Caches the last list query results for follow-up index references
- Resolves "第二个", "继续", "this one" type references
- Formats knowledge entries for display
- Integrates with ContextManager for topic-aware responses

Part of ConversationEngine V2 Phase 2.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from .knowledge import KnowledgeBase, KnowledgeEntry


@dataclass
class CachedListResult:
    """Cached list query results for follow-up index references."""
    topic: str
    entries: List[KnowledgeEntry]
    timestamp: str  # ISO format
    formatted_indices: Dict[int, str] = field(default_factory=dict)  # index → short label


class ResponseGenerator:
    """Generates responses with multi-turn list caching support.

    When a knowledge search returns a list, the results are cached.
    Subsequent "第二个" / "2" / "继续" queries can reference cached items.
    """

    # Chinese numeral mapping
    _CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

    # Patterns that extract an index number from user query
    INDEX_PATTERNS = [
        (r"^第?(\d+)个?$", None),           # 第2个, 2个, 2
        (r"^第([一二两三四五六七八九十]+)个?$", "cn"),  # 第一个, 第二个, 第三个
        (r"^number\s*(\d+)$", None),        # number 2
        (r"^#(\d+)$", None),                # #2
        (r"^(\d+)[\.、]?\s*$", None),       # 2.  2、
    ]

    # Patterns that indicate continuation
    CONTINUATION_PATTERNS = [
        r"^(继续|然后呢|还有呢|接下来呢|go on|and then|next)$",
        r"^(再往后|下一批|更多)$",
    ]

    # Patterns for "elaborate on this/that"
    ELABORATE_PATTERNS = [
        r"^(详细说说|展开讲讲|具体说说|深入了解|再说说|多说说|说详细点)$",
        r"^(这个|那个|它|它们|this|that|it)$",
        r"^(elaborate|more details?|tell me more)$",
    ]

    # How many items to show per page in list view
    PAGE_SIZE = 5

    def __init__(self, knowledge: KnowledgeBase):
        self.knowledge = knowledge
        self._cached_list: Optional[CachedListResult] = None
        self._continuation_offset: int = 0  # for "继续" pagination

    def handle_detail(self, topic: str, index: int = None,
                      continuation: bool = False) -> str:
        """Handle a detail/elaborate query.

        Args:
            topic: The topic to search for (may be None if from context)
            index: Specific item index to show (from "第二个")
            continuation: Whether this is a "继续" request

        Returns:
            Formatted response string
        """
        # Case 1: Explicit index reference ("第二个")
        if index is not None:
            return self._resolve_index(index)

        # Case 2: Continuation ("继续")
        if continuation:
            return self._handle_continuation()

        # Case 3: "这个" / "那个" (elaborate on last result)
        # This is handled by checking _cached_list with no index
        # but we need a topic to search

        # Case 4: Normal topic search
        if not topic:
            if self._cached_list:
                return self._show_cached_list_hint()
            return ("你想详细了解什么？请告诉我具体的话题。\n"
                    "比如：「详细说说 单细胞衰老」")

        results = self.knowledge.search(topic, top_k=10)
        if not results:
            return f"关于「{topic}」没有找到相关内容。"

        # Cache results
        self._cached_list = CachedListResult(
            topic=topic,
            entries=results,
            timestamp=self._now_iso(),
            formatted_indices={
                i + 1: entry.title for i, entry in enumerate(results)
            },
        )
        self._continuation_offset = 0

        # Show first page
        return self._format_list_page(results, topic, page_start=0)

    def resolve_index_from_query(self, query: str) -> Optional[int]:
        """Try to extract an index number from a query string.

        Returns the 1-based index, or None if no index found.
        """
        query_stripped = query.strip()
        for pattern, fmt in self.INDEX_PATTERNS:
            match = re.match(pattern, query_stripped, re.IGNORECASE)
            if match:
                try:
                    raw = match.group(1)
                    if fmt == "cn":
                        # Chinese numeral → int
                        val = self._CN_DIGITS.get(raw)
                        if val is not None:
                            return val
                        continue
                    return int(raw)
                except (ValueError, IndexError):
                    continue
        return None

    def is_continuation(self, query: str) -> bool:
        """Check if query is a continuation request."""
        query_stripped = query.strip()
        return any(re.match(p, query_stripped, re.IGNORECASE)
                   for p in self.CONTINUATION_PATTERNS)

    def is_elaborate_request(self, query: str) -> bool:
        """Check if query is an 'elaborate' request (standalone)."""
        query_stripped = query.strip()
        return any(re.match(p, query_stripped, re.IGNORECASE)
                   for p in self.ELABORATE_PATTERNS)

    def get_cached_topic(self) -> Optional[str]:
        """Get the topic of the currently cached list."""
        if self._cached_list:
            return self._cached_list.topic
        return None

    def has_cached_results(self) -> bool:
        """Whether there are cached list results available."""
        return self._cached_list is not None and len(self._cached_list.entries) > 0

    def clear_cache(self):
        """Clear the cached list results."""
        self._cached_list = None
        self._continuation_offset = 0

    # ==================== Internal Methods ====================

    def _resolve_index(self, index: int) -> str:
        """Resolve a user-provided index against cached results."""
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
        """Handle '继续' type queries — show next page of cached results."""
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
        return self._format_list_page(
            remaining, self._cached_list.topic,
            page_start=offset,
            total=len(entries),
        )

    def _format_list_page(self, entries: List[KnowledgeEntry],
                          topic: str, page_start: int = 0,
                          total: int = None) -> str:
        """Format a page of list results."""
        total = total or len(entries)
        lines = [f"🔍 关于「{topic}」的详细信息：\n"]

        for i, entry in enumerate(entries, start=page_start + 1):
            lines.append(f"  {i}. 【{entry.category}】{entry.title}")
            lines.append(f"     置信度: {entry.confidence}")
            content_preview = (entry.content[:200] + "..."
                               if len(entry.content) > 200 else entry.content)
            lines.append(f"     {content_preview}")
            lines.append("")

        # Navigation hint
        if total > page_start + len(entries):
            lines.append(f"📄 显示 {page_start + 1}-{page_start + len(entries)}"
                         f"（共 {total} 条）。说「继续」查看更多。")
        lines.append("💡 你可以说「第一个」「第二个」来查看某条的详情。")
        return "\n".join(lines)

    def _format_single_entry(self, entry: KnowledgeEntry,
                             index: int, total: int) -> str:
        """Format a single knowledge entry as detailed view."""
        lines = [f"📖 [{index}/{total}] 【{entry.category}】{entry.title}\n"]
        lines.append(f"来源: {entry.source}")
        lines.append(f"置信度: {entry.confidence}")
        if entry.related_projects:
            lines.append(f"相关项目: {', '.join(entry.related_projects)}")
        if entry.tags:
            lines.append(f"标签: {', '.join(entry.tags)}")
        lines.append("")
        lines.append(entry.content)

        # Navigation hint
        lines.append("")
        if index > 1:
            lines.append(f"⬅️ 说「第{index - 1}个」查看上一条")
        if index < total:
            lines.append(f"➡️ 说「第{index + 1}个」查看下一条")
        lines.append(f"🔙 说「详细说说 {self._cached_list.topic}」返回列表")

        return "\n".join(lines)

    def _show_cached_list_hint(self) -> str:
        """Show a hint about the cached list when topic is not specified."""
        if not self._cached_list:
            return ("你想详细了解什么？请告诉我具体的话题。\n"
                    "比如：「详细说说 单细胞衰老」")
        count = len(self._cached_list.entries)
        topic = self._cached_list.topic
        return (f"上次查询的是「{topic}」（共 {count} 条结果）。\n"
                f"你可以说「第一个」「第二个」来查看详情，\n"
                f"或者告诉我一个新的话题。")

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime
        return datetime.now().isoformat()
