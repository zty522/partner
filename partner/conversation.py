"""Conversation Interface V2 - context-aware conversation engine.

Builds on ConversationRouter with:
- DialogHistory: persistent conversation history
- ContextManager: sliding window context for multi-turn understanding
- Context-aware intent parsing (handles "详细说说", "第二个", etc.)
"""

import re
from datetime import datetime, timedelta
from typing import Optional, List

from .journal import Journal
from .knowledge import KnowledgeBase
from .task_queue import TaskQueue
from .state import StateManager
from .router import ConversationRouter, Intent, ParsedQuery
from .dialog_history import DialogHistory, DialogTurn
from .context import ContextManager
from .response_generator import ResponseGenerator
from .proactive_notifier import ProactiveNotifier, Notification
from .user_prefs import UserPreferenceStore


# Context-sensitive intent patterns: short phrases that need context
CONTEXT_AWARE_PATTERNS = [
    # Standalone "elaborate" — needs topic from context
    (r"^(详细说说|展开讲讲|具体说说|深入了解|再说说|多说说|说详细点)$", Intent.DETAIL, 0.95),
    # Chinese numeral index references: "第一个", "第二个", "第三个"
    (r"^第([一二两三四五六七八九十]+)个?$", Intent.DETAIL, 0.9),
    # Index references: "第一个", "第二个", "第3个"
    (r"^第?(\d+)个?$", Intent.DETAIL, 0.9),
    # Continuation: "继续", "然后呢", "还有呢"
    (r"^(继续|然后呢|还有呢|接下来呢|go on|and then)$", Intent.DETAIL, 0.85),
    # Pronoun references: "这个", "那个", "它"
    (r"^(这个|那个|它|它们|this|that|it)$", Intent.DETAIL, 0.8),
]


class ConversationEngine:
    """Context-aware conversation engine with dialog history.

    Maintains backward-compatible respond() interface while adding
    multi-turn context awareness.
    """

    def __init__(self, journal: Journal, knowledge: KnowledgeBase,
                 task_queue: TaskQueue, state: StateManager,
                 workspace: str = "/mnt/e/work/study_room"):
        self.journal = journal
        self.knowledge = knowledge
        self.task_queue = task_queue
        self.state = state

        import os
        state_dir = os.path.join(workspace, "state")

        # New V2 components
        self.dialog_history = DialogHistory(os.path.join(state_dir, "dialog_history.jsonl"))
        self.context = ContextManager(os.path.join(state_dir, "dialog_history.jsonl"))

        # ResponseGenerator for multi-turn list caching (Phase 2)
        self.response_gen = ResponseGenerator(knowledge)

        # ProactiveNotifier for post-cycle notifications (Phase 3)
        self.notifier = ProactiveNotifier(knowledge, journal, state, workspace)

        # UserPreferenceStore for personalization (Phase 4)
        self.user_prefs = UserPreferenceStore(
            os.path.join(state_dir, "user_prefs.json"),
            dialog_history_path=os.path.join(state_dir, "dialog_history.jsonl"),
        )

        # Keep old router as fallback
        self.router = ConversationRouter(journal, knowledge, task_queue, state)

    def respond(self, user_message: str) -> str:
        """Main entry point: context-aware conversation response."""
        # 1. Parse intent with context
        parsed = self._parse_with_context(user_message)

        # Track topic queries for cache management:
        # Only clear cache for NEW topic queries (not context-references)
        from_context = (parsed.params or {}).get("from_context", False)
        if parsed.topic and not from_context:
            self.response_gen.clear_cache()  # genuinely new topic → clear old cache
            # Record topic for preference learning (Phase 4)
            self.user_prefs.record_topic_query(parsed.topic)

        # Track conversation turn for session stats
        self.user_prefs.record_session_turn()

        # 2. Record user message in context
        self.context.add_turn(
            "user", user_message,
            intent=parsed.intent.value,
            topic=parsed.topic,
        )

        # 3. Generate response
        response = self._generate_response(parsed)

        # 4. Record partner response
        self.context.add_turn("partner", response)

        return response

    def check_proactive(self) -> List[str]:
        """Check if proactive notifications should be sent.

        Called after research cycles to see if findings
        warrant notifying the user.

        Returns list of formatted notification strings.
        """
        notifications = self.notifier.check_and_notify()
        if not notifications:
            return []
        return [self.notifier.format_notifications(notifications)]

    def _parse_with_context(self, query: str) -> ParsedQuery:
        """Parse intent with context awareness.

        Layer 1: Context-sensitive patterns (short phrases needing context)
        Layer 2: Standard regex matching via router
        Layer 3: Fuzzy keyword matching fallback
        """
        query_stripped = query.strip()

        # Layer 1: Context-sensitive patterns
        for pattern, intent, confidence in CONTEXT_AWARE_PATTERNS:
            match = re.match(pattern, query_stripped)
            if match:
                active_topic = self.context.get_active_topic()
                # Check if this is an index reference or continuation
                index = None
                continuation = False
                if match.groups():
                    raw = match.group(1)
                    try:
                        index = int(raw)
                    except (ValueError, IndexError):
                        # Try Chinese numeral
                        cn_val = ResponseGenerator._CN_DIGITS.get(raw)
                        if cn_val is not None:
                            index = cn_val
                if self.response_gen.is_continuation(query_stripped):
                    continuation = True

                if active_topic or self.response_gen.has_cached_results():
                    # Extract index if present
                    return ParsedQuery(
                        intent=intent,
                        confidence=confidence,
                        query=query,
                        topic=active_topic,
                        params={
                            "from_context": True,
                            "index": index,
                            "continuation": continuation,
                        },
                    )

        # Layer 2: Standard router parsing
        parsed = self.router.parse_intent(query)
        if parsed.confidence >= 0.8:
            return parsed

        # Layer 3: Fuzzy keyword matching
        fuzzy = self._fuzzy_classify(query)
        if fuzzy:
            return fuzzy

        return parsed

    def _fuzzy_classify(self, query: str) -> Optional[ParsedQuery]:
        """Fallback: keyword-based fuzzy intent classification."""
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
        """Generate response based on parsed intent."""
        # DETAIL intent: use context-aware handling
        if parsed.intent == Intent.DETAIL:
            return self._handle_detail(parsed)

        # Other intents: delegate to router
        handler = self.router._handlers.get(parsed.intent, self.router._handle_general)
        return handler(parsed)

    def _handle_detail(self, parsed: ParsedQuery) -> str:
        """Handle detail/elaborate queries with context and index support."""
        topic = parsed.topic
        index = parsed.params.get("index") if parsed.params else None
        continuation = (parsed.params or {}).get("continuation", False)

        # Delegate to ResponseGenerator
        return self.response_gen.handle_detail(
            topic, index=index, continuation=continuation,
        )

    def _format_detail(self, entry) -> str:
        """Format a single knowledge entry as detailed view."""
        lines = [f"📖 【{entry.category}】{entry.title}\n"]
        lines.append(f"来源: {entry.source}")
        lines.append(f"置信度: {entry.confidence}")
        if entry.related_projects:
            lines.append(f"相关项目: {', '.join(entry.related_projects)}")
        lines.append("")
        lines.append(entry.content)
        return "\n".join(lines)

    # Legacy methods kept for backward compatibility (used by core.py)

    def _handle_status(self) -> str:
        from .router import ParsedQuery, Intent
        parsed = ParsedQuery(intent=Intent.STATUS, confidence=1.0, query="")
        return self.router._handle_status(parsed)

    def _handle_progress(self) -> str:
        from .router import ParsedQuery, Intent
        parsed = ParsedQuery(intent=Intent.PROGRESS, confidence=1.0, query="")
        return self.router._handle_progress(parsed)

    def _handle_knowledge(self, query: str) -> str:
        from .router import ParsedQuery, Intent
        parsed = ParsedQuery(intent=Intent.KNOWLEDGE, confidence=1.0, query=query, topic=query)
        return self.router._handle_knowledge(parsed)

    def _handle_direction(self, message: str) -> str:
        from .router import ParsedQuery, Intent
        parsed = ParsedQuery(intent=Intent.DIRECTION, confidence=1.0, query=message)
        return self.router._handle_direction(parsed)

    def _handle_help(self) -> str:
        from .router import ParsedQuery, Intent
        parsed = ParsedQuery(intent=Intent.HELP, confidence=1.0, query="")
        return self.router._handle_help(parsed)

    def _handle_general(self, message: str) -> str:
        from .router import ParsedQuery, Intent
        parsed = ParsedQuery(intent=Intent.GENERAL, confidence=0.5, query=message)
        return self.router._handle_general(parsed)
