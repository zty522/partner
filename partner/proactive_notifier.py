"""Proactive Notifier - 主动通知系统 (Conversation V2 Phase 3).

After each research cycle, checks if findings warrant notifying the user.
Notifications are based on configurable rules that evaluate knowledge base
state, research milestones, and user interest alignment.

Design: design/conversation_v2.md §4.4
"""

import json
import os
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Callable, Any

from .knowledge import KnowledgeBase, KnowledgeEntry
from .journal import Journal
from .state import StateManager

logger = logging.getLogger(__name__)


@dataclass
class NotificationRule:
    """A single notification rule."""
    name: str
    description: str = ""
    priority: str = "normal"  # "low" | "normal" | "high"
    enabled: bool = True
    cooldown_hours: float = 4.0  # minimum hours between firings
    last_fired: str = ""  # ISO timestamp


@dataclass
class Notification:
    """A generated notification."""
    rule_name: str
    title: str
    body: str
    priority: str = "normal"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


class ProactiveNotifier:
    """Proactive notification system for Partner.

    Evaluates rules after each research cycle and returns notifications
    for findings that warrant user attention.
    """

    def __init__(self, knowledge: KnowledgeBase, journal: Journal,
                 state: StateManager, workspace: str = ""):
        self.knowledge = knowledge
        self.journal = journal
        self.state = state
        self.workspace = workspace

        self.state_dir = os.path.join(workspace, "state") if workspace else None
        if self.state_dir:
            os.makedirs(self.state_dir, exist_ok=True)
            self.config_path = os.path.join(self.state_dir, "notifier_config.json")
            self.notif_log_path = os.path.join(self.state_dir, "notifications.jsonl")
        else:
            self.config_path = None
            self.notif_log_path = None

        # User focus areas (inferred from task history and knowledge tags)
        self._focus_areas: List[str] = []
        self._focus_areas_ts: Optional[datetime] = None

        # Load or initialize rules
        self.rules = self._load_rules()

    @staticmethod
    def _make_default_rules() -> List['NotificationRule']:
        """Create default notification rules."""
        return [
            NotificationRule(name="high_confidence_finding",
                description="Notify when a high-confidence knowledge entry is added",
                priority="high", cooldown_hours=2.0),
            NotificationRule(name="research_milestone",
                description="Notify at task/knowledge count milestones",
                priority="normal", cooldown_hours=12.0),
            NotificationRule(name="user_interest_update",
                description="Notify when new findings match user focus areas",
                priority="normal", cooldown_hours=4.0),
            NotificationRule(name="streak_achievement",
                description="Notify when Partner completes consecutive tasks",
                priority="low", cooldown_hours=24.0),
        ]

    def _load_rules(self) -> List[NotificationRule]:
        """Load notification rules from config, or create defaults."""
        if not self.config_path:
            return self._make_default_rules()
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.loads(f.read(), strict=False)
            rules = []
            for r in data.get("rules", []):
                rules.append(NotificationRule(**{k: v for k, v in r.items()
                                                 if k in NotificationRule.__dataclass_fields__}))
            if rules:
                return rules
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        return self._make_default_rules()


    def _save_rules(self, rules: List[NotificationRule] = None):
        rules = rules or self.rules
        data = {
            "last_updated": datetime.now().isoformat(),
            "rules": [asdict(r) for r in rules],
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _is_cooling_down(self, rule: NotificationRule) -> bool:
        """Check if a rule is still in cooldown period."""
        if not rule.last_fired:
            return False
        try:
            last = datetime.fromisoformat(rule.last_fired)
            delta = datetime.now() - last
            return delta < timedelta(hours=rule.cooldown_hours)
        except (ValueError, TypeError):
            return False

    def _mark_fired(self, rule: NotificationRule):
        """Record that a rule fired now."""
        rule.last_fired = datetime.now().isoformat()

    def check_and_notify(self) -> List[Notification]:
        """Check all rules and return notifications that should be sent.

        Called after research cycles. Returns list of Notification objects.
        """
        notifications: List[Notification] = []

        for rule in self.rules:
            if not rule.enabled:
                continue
            if self._is_cooling_down(rule):
                continue

            try:
                notif = self._evaluate_rule(rule)
                if notif:
                    notifications.append(notif)
                    self._mark_fired(rule)
                    self._log_notification(notif)
            except Exception as e:
                logger.warning(f"Rule '{rule.name}' evaluation failed: {e}")

        if notifications:
            self._save_rules()

        return notifications

    def _evaluate_rule(self, rule: NotificationRule) -> Optional[Notification]:
        """Evaluate a single rule and return a Notification if triggered."""
        handler = getattr(self, f"_check_{rule.name}", None)
        if handler and callable(handler):
            return handler(rule)
        return None

    # --- Rule Implementations ---

    def _check_high_confidence_finding(self, rule: NotificationRule) -> Optional[Notification]:
        """Check if recent knowledge entries have high confidence."""
        recent = self.knowledge.get_recent(3)
        for entry in recent:
            if entry.confidence in ("high", "very_high"):
                return Notification(
                    rule_name=rule.name,
                    title=f"🔑 重要发现：{entry.title}",
                    body=(
                        f"置信度: {entry.confidence}\n"
                        f"分类: {entry.category}\n"
                        f"摘要: {entry.content[:200]}..."
                        if len(entry.content) > 200
                        else f"置信度: {entry.confidence}\n分类: {entry.category}\n\n{entry.content}"
                    ),
                    priority=rule.priority,
                    metadata={"entry_id": entry.id, "category": entry.category},
                )
        return None

    def _check_research_milestone(self, rule: NotificationRule) -> Optional[Notification]:
        """Check if Partner has hit a task/knowledge milestone."""
        stats = self.state.load_stats()
        completed = stats.get("total_tasks_completed", 0)
        kb_count = len(self.knowledge.entries)

        milestones_tasks = [10, 25, 50, 100, 200, 500]
        milestones_kb = [25, 50, 100, 200, 500]

        # Check if we just crossed a milestone
        prev_completed = stats.get("_prev_tasks_completed", 0)
        prev_kb = stats.get("_prev_kb_count", 0)

        hit_task = None
        hit_kb = None
        for m in milestones_tasks:
            if prev_completed < m <= completed:
                hit_task = m
        for m in milestones_kb:
            if prev_kb < m <= kb_count:
                hit_kb = m

        # Update prev counters
        stats["_prev_tasks_completed"] = completed
        stats["_prev_kb_count"] = kb_count
        self.state.update_stats(stats)

        if hit_task or hit_kb:
            parts = []
            if hit_task:
                parts.append(f"已完成 {completed} 个任务 (突破 {hit_task} 里程碑!)")
            if hit_kb:
                parts.append(f"知识库达到 {kb_count} 条 (突破 {hit_kb} 里程碑!)")
            return Notification(
                rule_name=rule.name,
                title="🎯 研究里程碑",
                body="\n".join(parts),
                priority=rule.priority,
                metadata={"tasks": completed, "knowledge": kb_count},
            )
        return None

    def _check_user_interest_update(self, rule: NotificationRule) -> Optional[Notification]:
        """Check if recent findings align with user focus areas."""
        focus = self._get_user_focus_areas()
        if not focus:
            return None

        recent = self.knowledge.get_recent(5)
        for entry in recent:
            entry_text = f"{entry.title} {entry.content} {' '.join(entry.tags)}".lower()
            for area in focus:
                if area.lower() in entry_text:
                    return Notification(
                        rule_name=rule.name,
                        title=f"📚 关注领域更新：「{area}」",
                        body=(
                            f"新发现与你的关注领域相关：\n\n"
                            f"【{entry.title}】\n{entry.content[:200]}"
                            if len(entry.content) > 200
                            else f"【{entry.title}】\n{entry.content}"
                        ),
                        priority=rule.priority,
                        metadata={"focus_area": area, "entry_id": entry.id},
                    )
        return None

    def _check_streak_achievement(self, rule: NotificationRule) -> Optional[Notification]:
        """Check if Partner has completed consecutive tasks in one session."""
        stats = self.state.load_stats()
        cycle = stats.get("cycle_count", 0)

        # Check last 3 journal entries for consecutive completions
        recent_entries = self.journal.get_recent(5)
        if len(recent_entries) < 3:
            return None

        consecutive = 0
        for entry in recent_entries:
            # JournalEntry is a dataclass, access via attributes
            title = getattr(entry, "task_title", "") or ""
            summary = getattr(entry, "result_summary", "") or ""
            if "FAILED" not in title and summary:
                consecutive += 1
            else:
                break

        if consecutive >= 3:
            return Notification(
                rule_name=rule.name,
                title="🔥 连续高效研究",
                body=(
                    f"Partner 连续完成了 {consecutive} 个任务！\n"
                    f"当前总周期数: {cycle}"
                ),
                priority=rule.priority,
                metadata={"streak": consecutive, "cycle": cycle},
            )
        return None

    # --- User Focus Area Detection ---

    def _get_user_focus_areas(self, refresh_hours: float = 6.0) -> List[str]:
        """Get user focus areas, with caching.

        Infers focus areas from:
        1. Knowledge base tags (most frequent)
        2. Recent task tags
        3. User-added direction tasks (priority >= 10)
        """
        now = datetime.now()
        if (self._focus_areas_ts and
                (now - self._focus_areas_ts).total_seconds() < refresh_hours * 3600):
            return self._focus_areas

        areas = set()

        # From knowledge tags
        tag_freq: Dict[str, int] = {}
        for entry in self.knowledge.entries:
            for tag in entry.tags:
                tag_freq[tag] = tag_freq.get(tag, 0) + 1
        # Top 10 most frequent tags
        top_tags = sorted(tag_freq.items(), key=lambda x: -x[1])[:10]
        for tag, count in top_tags:
            if count >= 2:  # at least 2 occurrences
                areas.add(tag)

        # From recent task tags
        try:
            with open(os.path.join(self.state_dir, "task_queue.json"), "r", encoding="utf-8") as f:
                tasks = json.loads(f.read(), strict=False)
            for t in tasks[-20:]:
                for tag in t.get("tags", []):
                    areas.add(tag)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        self._focus_areas = list(areas)
        self._focus_areas_ts = now
        return self._focus_areas

    # --- Notification Logging ---

    def _log_notification(self, notif: Notification):
        """Append notification to log file."""
        try:
            with open(self.notif_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(notif), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Failed to log notification: {e}")

    def get_recent_notifications(self, n: int = 10) -> List[Dict]:
        """Read recent notifications from log."""
        if not os.path.exists(self.notif_log_path):
            return []
        try:
            decoder = json.JSONDecoder(strict=False)
            with open(self.notif_log_path, "r", encoding="utf-8") as f:
                content = f.read()
            entries = []
            pos = 0
            while pos < len(content):
                stripped = content[pos:].lstrip()
                if not stripped:
                    break
                try:
                    obj, end = decoder.raw_decode(stripped)
                    entries.append(obj)
                    next_nl = stripped.find("\n", end)
                    if next_nl == -1:
                        break
                    pos = pos + (len(content[pos:]) - len(stripped)) + next_nl + 1
                except json.JSONDecodeError:
                    next_nl = content.find("\n", pos)
                    if next_nl == -1:
                        break
                    pos = next_nl + 1
            return entries[-n:]
        except Exception:
            return []

    def format_notifications(self, notifications: List[Notification]) -> str:
        """Format notifications for display."""
        if not notifications:
            return ""
        lines = ["📬 **Partner 主动通知**\n"]
        for n in notifications:
            priority_icon = {"high": "🔴", "normal": "🟡", "low": "⚪"}.get(n.priority, "⚪")
            lines.append(f"{priority_icon} **{n.title}**")
            lines.append(n.body)
            lines.append("")
        return "\n".join(lines)

    # --- Configuration ---

    def set_rule_enabled(self, rule_name: str, enabled: bool) -> bool:
        """Enable or disable a rule."""
        for rule in self.rules:
            if rule.name == rule_name:
                rule.enabled = enabled
                self._save_rules()
                return True
        return False

    def set_rule_cooldown(self, rule_name: str, hours: float) -> bool:
        """Update a rule's cooldown period."""
        for rule in self.rules:
            if rule.name == rule_name:
                rule.cooldown_hours = hours
                self._save_rules()
                return True
        return False

    def add_rule(self, rule: NotificationRule):
        """Add a custom notification rule."""
        self.rules.append(rule)
        self._save_rules()

    def get_status(self) -> Dict:
        """Return notifier status for display."""
        return {
            "rules_count": len(self.rules),
            "rules_enabled": sum(1 for r in self.rules if r.enabled),
            "recent_notifications": len(self.get_recent_notifications(50)),
            "focus_areas": self._get_user_focus_areas(),
        }
