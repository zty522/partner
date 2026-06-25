"""Conversation - legacy direct-chat facade.

The QQ/event runtime uses InteractionOrchestrator. This module remains as a
small compatibility facade for PartnerCore and desktop chat widgets.
"""

import os
from typing import List, Optional

from .journal import Journal
from .knowledge import KnowledgeBase
from .task_queue import TaskQueue
from .state import StateManager
from .router import ConversationRouter, ParsedQuery
from .dialog import DialogHistory, ContextManager
from .autocheck import ProactiveNotifier
from .user_prefs import UserPreferenceStore
from .workspace_layout import dialog_history_path, ensure_instance_layout


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
        if workspace:
            ensure_instance_layout(workspace)
        history_path = dialog_history_path(workspace) if workspace else None
        self.dialog_history = DialogHistory(history_path)
        self.context = ContextManager(history_path)

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

        if parsed.topic:
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
        """Return neutral parsed query; runtime intent selection belongs to LLM."""
        return self.router.parse_intent(query)

    def _fuzzy_classify(self, query: str) -> Optional[ParsedQuery]:
        return None

    def _generate_response(self, parsed: ParsedQuery) -> str:
        """Generate response via LLM (router.route)."""
        return self.router.route(parsed.query)

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
