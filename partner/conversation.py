"""Conversation Interface - the heart of Partner's user interaction.

This is what makes Partner special: you can just TALK to it.
"Hey, what have you been doing?" is a first-class interaction.

This module now delegates to the ConversationRouter for intent classification.
"""

from datetime import datetime, timedelta
from typing import Optional

from .journal import Journal
from .knowledge import KnowledgeBase
from .task_queue import TaskQueue
from .state import StateManager
from .router import ConversationRouter


class ConversationEngine:
    """Handles natural language conversations with the user.
    
    Uses ConversationRouter for intent classification and routing.
    Maintains backward-compatible interface.
    """
    
    def __init__(self, journal: Journal, knowledge: KnowledgeBase,
                 task_queue: TaskQueue, state: StateManager):
        self.journal = journal
        self.knowledge = knowledge
        self.task_queue = task_queue
        self.state = state
        self.router = ConversationRouter(journal, knowledge, task_queue, state)
    
    def respond(self, user_message: str) -> str:
        """Main entry point: take user message, return response."""
        return self.router.route(user_message)
    
    # Legacy methods kept for backward compatibility (used by core.py)
    
    def _handle_status(self) -> str:
        """The core feature: report what Partner has been doing."""
        from .router import ParsedQuery, Intent
        parsed = ParsedQuery(intent=Intent.STATUS, confidence=1.0, query="")
        return self.router._handle_status(parsed)
    
    def _handle_progress(self) -> str:
        """Show task queue status."""
        from .router import ParsedQuery, Intent
        parsed = ParsedQuery(intent=Intent.PROGRESS, confidence=1.0, query="")
        return self.router._handle_progress(parsed)
    
    def _handle_knowledge(self, query: str) -> str:
        """Search and present knowledge."""
        from .router import ParsedQuery, Intent
        parsed = ParsedQuery(intent=Intent.KNOWLEDGE, confidence=1.0, query=query, topic=query)
        return self.router._handle_knowledge(parsed)
    
    def _handle_direction(self, message: str) -> str:
        """Handle direction change requests."""
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
