"""UserPreferenceStore - 用户偏好存储与学习 (Conversation V2 Phase 4).

Persists user preferences and auto-learns from dialog history:
- Explicit preferences: verbosity, language, notification threshold
- Learned preferences: frequent topics, research focus areas, session patterns
- Personalized response style based on accumulated interaction data

Design: design/conversation_v2.md §4.3
"""

import json
import os
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class UserPreferences:
    """User preference profile.

    Combines explicit settings with auto-learned patterns.
    """
    # --- Explicit preferences (user can set via CLI) ---
    verbosity: str = "normal"       # "brief" | "normal" | "detailed"
    language: str = "zh"            # "zh" | "en" | "mixed"
    research_focus: List[str] = field(default_factory=list)  # user-declared focus areas
    notification_threshold: str = "medium"  # "low" | "medium" | "high"

    # --- Auto-learned preferences ---
    frequent_topics: Dict[str, int] = field(default_factory=dict)  # topic -> query count
    avg_session_length: float = 0.0   # average turns per session
    total_sessions: int = 0           # number of distinct sessions
    total_turns: int = 0              # total conversation turns recorded
    preferred_detail_level: str = ""  # inferred from user behavior: "brief"|"normal"|"detailed"
    topic_categories: Dict[str, int] = field(default_factory=dict)  # category -> count

    # --- Metadata ---
    created_at: str = ""
    last_updated: str = ""
    last_topic_query: str = ""        # most recent topic queried


class UserPreferenceStore:
    """Persistent user preference store with auto-learning.

    Stores preferences in user_prefs.json. Learns from dialog history
    to personalize responses over time.
    """

    def __init__(self, path: str, dialog_history_path: str = ""):
        self.path = path
        self.dialog_history_path = dialog_history_path
        self.prefs = self._load()

    def _load(self) -> 'UserPreferences':
        """Load preferences from JSON file, or create defaults."""
        if not self.path or not os.path.exists(self.path):
            return UserPreferences(
                created_at=datetime.now().isoformat(),
                last_updated=datetime.now().isoformat(),
            )
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.loads(f.read(), strict=False)
            # Filter to known fields only
            known = {k: v for k, v in data.items()
                     if k in UserPreferences.__dataclass_fields__}
            prefs = UserPreferences(**known)
            logger.debug(f"Loaded user prefs: {len(prefs.frequent_topics)} topics, "
                         f"verbosity={prefs.verbosity}")
            return prefs
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.info(f"Creating new user prefs at {self.path}: {e}")
            prefs = UserPreferences(
                created_at=datetime.now().isoformat(),
                last_updated=datetime.now().isoformat(),
            )
            self._save(prefs)
            return prefs

    def _save(self, prefs: 'UserPreferences' = None):
        """Save preferences to JSON."""
        if not self.path:
            return
        prefs = prefs or self.prefs
        prefs.last_updated = datetime.now().isoformat()
        data = asdict(prefs)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # --- Topic Tracking ---

    def record_topic_query(self, topic: str, category: str = ""):
        """Record that the user queried about a topic.

        Args:
            topic: The topic/keyword the user asked about
            category: Optional category (e.g., "research", "tool", "method")
        """
        if not topic:
            return

        topic = topic.strip().lower()
        self.prefs.frequent_topics[topic] = \
            self.prefs.frequent_topics.get(topic, 0) + 1
        self.prefs.last_topic_query = topic

        if category:
            cat = category.strip().lower()
            self.prefs.topic_categories[cat] = \
                self.prefs.topic_categories.get(cat, 0) + 1

        self._save()
        logger.debug(f"Recorded topic query: '{topic}' (count={self.prefs.frequent_topics[topic]})")

    def get_top_topics(self, n: int = 5) -> List[Tuple[str, int]]:
        """Get the user's most frequently queried topics.

        Returns:
            List of (topic, count) tuples, sorted by frequency descending.
        """
        sorted_topics = sorted(
            self.prefs.frequent_topics.items(),
            key=lambda x: -x[1]
        )
        return sorted_topics[:n]

    def get_top_categories(self, n: int = 5) -> List[Tuple[str, int]]:
        """Get most frequent topic categories."""
        sorted_cats = sorted(
            self.prefs.topic_categories.items(),
            key=lambda x: -x[1]
        )
        return sorted_cats[:n]

    def infer_focus_areas(self) -> List[str]:
        """Infer user's research focus areas from query history.

        Returns topics queried at least 3 times, plus explicit research_focus.
        """
        learned = [t[0] for t in self.get_top_topics(10) if t[1] >= 3]
        # Merge with explicit focus, deduplicated
        combined = list(dict.fromkeys(self.prefs.research_focus + learned))
        return combined

    # --- Session Tracking ---

    def record_session_turn(self):
        """Record a single conversation turn (called on each respond())."""
        self.prefs.total_turns += 1
        # Don't save on every turn — batch saves via record_session_end()

    def record_session_end(self):
        """Record end of a conversation session.

        Updates average session length using exponential moving average.
        """
        self.prefs.total_sessions += 1

        # Estimate turns in this session (simplified: just use incremental count)
        # EMA with alpha=0.2
        alpha = 0.2
        if self.prefs.avg_session_length == 0.0:
            self.prefs.avg_session_length = float(self.prefs.total_turns)
        else:
            # Approximate: current session turns ≈ total_turns / total_sessions
            current_avg = self.prefs.total_turns / max(self.prefs.total_sessions, 1)
            self.prefs.avg_session_length = (
                alpha * current_avg +
                (1 - alpha) * self.prefs.avg_session_length
            )

        self._save()

    # --- Verbosity / Detail Level ---

    def get_verbosity(self) -> str:
        """Get effective verbosity setting.

        Returns preferred_detail_level if learned, else explicit verbosity.
        """
        if self.prefs.preferred_detail_level:
            return self.prefs.preferred_detail_level
        return self.prefs.verbosity

    def set_verbosity(self, level: str):
        """Set verbosity explicitly (brief/normal/detailed)."""
        if level in ("brief", "normal", "detailed"):
            self.prefs.verbosity = level
            self.prefs.preferred_detail_level = level
            self._save()
            logger.info(f"Verbosity set to: {level}")

    def infer_verbosity_from_behavior(self, detail_requests: int, total_queries: int):
        """Auto-infer preferred detail level from behavior.

        Args:
            detail_requests: Number of times user asked for more detail
            total_queries: Total queries in recent window
        """
        if total_queries < 5:
            return  # Not enough data

        detail_ratio = detail_requests / total_queries

        if detail_ratio > 0.4:
            self.prefs.preferred_detail_level = "detailed"
        elif detail_ratio < 0.1:
            self.prefs.preferred_detail_level = "brief"
        else:
            self.prefs.preferred_detail_level = "normal"

        self._save()
        logger.info(f"Inferred verbosity: {self.prefs.preferred_detail_level} "
                     f"(detail_ratio={detail_ratio:.2f})")

    # --- Notification Preferences ---

    def get_notification_threshold(self) -> str:
        """Get notification threshold: low/medium/high.

        - "low": notify on almost everything
        - "medium": notify on milestones and high-confidence findings
        - "high": only notify on very important findings
        """
        return self.prefs.notification_threshold

    def set_notification_threshold(self, level: str):
        """Set notification threshold."""
        if level in ("low", "medium", "high"):
            self.prefs.notification_threshold = level
            self._save()

    def should_notify(self, notification_priority: str) -> bool:
        """Check if a notification with given priority should be sent.

        Args:
            notification_priority: "low" | "normal" | "high"

        Returns:
            True if notification should be delivered based on user threshold.
        """
        threshold = self.prefs.notification_threshold
        priority_levels = {"low": 0, "normal": 1, "high": 2}
        threshold_levels = {"low": 0, "medium": 1, "high": 2}

        notif_level = priority_levels.get(notification_priority, 1)
        user_threshold = threshold_levels.get(threshold, 1)

        return notif_level >= user_threshold

    # --- Auto-Learning from Dialog History ---

    def learn_from_dialog_history(self, turns: List[Dict[str, Any]]):
        """Analyze recent dialog turns to update learned preferences.

        Args:
            turns: List of dicts with keys: role, content, intent, topic, timestamp
        """
        if not turns:
            return

        detail_requests = 0
        topics_seen = []

        for turn in turns:
            role = turn.get("role", "")
            content = turn.get("content", "")
            intent = turn.get("intent", "")
            topic = turn.get("topic", "")

            # Count detail/elaborate requests
            if role == "user" and intent in ("detail", "DETAIL"):
                detail_requests += 1

            # Track topics
            if topic:
                topics_seen.append(topic)

        # Update verbosity inference
        user_turns = [t for t in turns if t.get("role") == "user"]
        if user_turns:
            self.infer_verbosity_from_behavior(detail_requests, len(user_turns))

        # Record topics
        for topic in topics_seen:
            self.record_topic_query(topic)

        logger.info(f"Learned from {len(turns)} turns: "
                     f"{detail_requests} detail requests, "
                     f"{len(topics_seen)} topics")

    def auto_learn_from_history_file(self, max_turns: int = 50):
        """Load recent dialog history and learn from it.

        Args:
            max_turns: Maximum number of recent turns to analyze
        """
        if not self.dialog_history_path or not os.path.exists(self.dialog_history_path):
            return

        try:
            turns = []
            decoder = json.JSONDecoder(strict=False)
            with open(self.dialog_history_path, "r", encoding="utf-8") as f:
                content = f.read()

            pos = 0
            while pos < len(content):
                stripped = content[pos:].lstrip()
                if not stripped:
                    break
                try:
                    obj, end = decoder.raw_decode(stripped)
                    turns.append(obj)
                    next_nl = stripped.find("\n", end)
                    if next_nl == -1:
                        break
                    pos = pos + (len(content[pos:]) - len(stripped)) + next_nl + 1
                except json.JSONDecodeError:
                    next_nl = content.find("\n", pos)
                    if next_nl == -1:
                        break
                    pos = next_nl + 1

            # Only learn from recent turns
            recent = turns[-max_turns:]
            self.learn_from_dialog_history(recent)

        except Exception as e:
            logger.warning(f"Failed to auto-learn from dialog history: {e}")

    # --- Personalization Helpers ---

    def get_personalized_greeting(self) -> str:
        """Generate a personalized greeting based on known preferences."""
        focus = self.infer_focus_areas()
        verbosity = self.get_verbosity()

        if verbosity == "brief":
            return "有什么需要？"

        parts = ["你好！"]
        if focus:
            top3 = focus[:3]
            parts.append(f"最近关注的领域: {', '.join(top3)}。")

        if self.prefs.total_sessions > 0:
            parts.append(f"这是我们第 {self.prefs.total_sessions + 1} 次对话。")

        return " ".join(parts)

    def get_response_style_hint(self) -> str:
        """Get a style hint for response generation.

        Returns a brief instruction that can be prepended to LLM prompts
        to personalize response style.
        """
        verbosity = self.get_verbosity()
        lang = self.prefs.language

        hints = []
        if verbosity == "brief":
            hints.append("用简洁的语言回答，避免冗余")
        elif verbosity == "detailed":
            hints.append("详细回答，包含背景信息和示例")

        if lang == "en":
            hints.append("respond in English")
        elif lang == "mixed":
            hints.append("中英混合回答")

        return "；".join(hints) if hints else ""

    # --- Status / Export ---

    def get_status(self) -> Dict[str, Any]:
        """Return preference store status for display."""
        return {
            "verbosity": self.prefs.verbosity,
            "preferred_detail_level": self.prefs.preferred_detail_level,
            "language": self.prefs.language,
            "notification_threshold": self.prefs.notification_threshold,
            "research_focus": self.prefs.research_focus,
            "inferred_focus_areas": self.infer_focus_areas(),
            "top_topics": self.get_top_topics(5),
            "top_categories": self.get_top_categories(5),
            "total_sessions": self.prefs.total_sessions,
            "total_turns": self.prefs.total_turns,
            "avg_session_length": round(self.prefs.avg_session_length, 1),
            "last_topic_query": self.prefs.last_topic_query,
            "last_updated": self.prefs.last_updated,
        }

    def export_summary(self) -> str:
        """Export a human-readable preference summary."""
        lines = ["👤 **用户偏好摘要**\n"]

        lines.append(f"- 回复风格: {self.get_verbosity()}")
        lines.append(f"- 语言偏好: {self.prefs.language}")
        lines.append(f"- 通知阈值: {self.prefs.notification_threshold}")

        focus = self.infer_focus_areas()
        if focus:
            lines.append(f"- 关注领域: {', '.join(focus[:5])}")

        top = self.get_top_topics(5)
        if top:
            topics_str = ", ".join(f"{t}({c})" for t, c in top)
            lines.append(f"- 高频话题: {topics_str}")

        if self.prefs.total_sessions > 0:
            lines.append(f"- 历史对话: {self.prefs.total_sessions} 次, "
                         f"平均 {self.prefs.avg_session_length:.1f} 轮/次")

        return "\n".join(lines)
