"""Tests for ProactiveNotifier (Conversation V2 Phase 3)."""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from partner.knowledge import KnowledgeBase, KnowledgeEntry
from partner.journal import Journal, JournalEntry
from partner.state import StateManager
from partner.proactive_notifier import ProactiveNotifier, NotificationRule, Notification


class TestProactiveNotifier:
    """Test suite for ProactiveNotifier."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, exist_ok=True)

        self.kb_path = os.path.join(self.state_dir, "knowledge.json")
        self.journal_path = os.path.join(self.state_dir, "journal.jsonl")
        self.stats_path = os.path.join(self.state_dir, "stats.json")

        # Initialize empty knowledge base
        kb = KnowledgeBase(self.kb_path)
        kb.save()

        # Initialize empty stats
        with open(self.stats_path, "w") as f:
            json.dump({"total_tasks_completed": 5, "cycle_count": 3}, f)

        self.kb = KnowledgeBase(self.kb_path)
        self.journal = Journal(self.journal_path)
        self.state = StateManager(self.state_dir)
        self.notifier = ProactiveNotifier(
            self.kb, self.journal, self.state, workspace=self.tmpdir
        )

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init_creates_default_rules(self):
        """Notifier should create 4 default rules on first run."""
        assert len(self.notifier.rules) == 4
        names = {r.name for r in self.notifier.rules}
        assert "high_confidence_finding" in names
        assert "research_milestone" in names
        assert "user_interest_update" in names
        assert "streak_achievement" in names

    def test_rules_persist_across_instances(self):
        """Rules (with cooldown state) should persist."""
        self.notifier.rules[0].last_fired = datetime.now().isoformat()
        self.notifier._save_rules()

        # Reload
        notifier2 = ProactiveNotifier(
            self.kb, self.journal, self.state, workspace=self.tmpdir
        )
        assert notifier2.rules[0].last_fired != ""

    def test_high_confidence_finding_triggered(self):
        """High confidence entries should trigger notification."""
        self.kb.add(KnowledgeEntry(
            title="Important Discovery",
            content="Something very important found in research.",
            category="findings",
            confidence="high",
            tags=["test"],
        ))

        notifications = self.notifier.check_and_notify()
        titles = [n.title for n in notifications]
        assert any("重要发现" in t for t in titles)

    def test_high_confidence_not_triggered_for_low(self):
        """Low/medium confidence entries should not trigger high-confidence rule."""
        self.kb.add(KnowledgeEntry(
            title="Minor Finding",
            content="Not very confident about this.",
            confidence="low",
        ))

        notifications = self.notifier.check_and_notify()
        titles = [n.title for n in notifications]
        assert not any("重要发现" in t for t in titles)

    def test_cooldown_prevents_repeated_firing(self):
        """Rules in cooldown should not fire again."""
        self.kb.add(KnowledgeEntry(
            title="Discovery 1",
            content="First finding.",
            confidence="high",
        ))
        notifs1 = self.notifier.check_and_notify()
        assert len(notifs1) >= 1

        # Add another high confidence entry immediately
        self.kb.add(KnowledgeEntry(
            title="Discovery 2",
            content="Second finding.",
            confidence="high",
        ))
        notifs2 = self.notifier.check_and_notify()
        # Should NOT fire again due to cooldown
        titles = [n.title for n in notifs2]
        assert not any("重要发现" in t for t in titles)

    def test_streak_achievement(self):
        """3+ consecutive successful journal entries should trigger streak."""
        for i in range(4):
            self.journal.log(JournalEntry(
                task_id=f"task_{i}",
                task_type="deep_dive",
                task_title=f"Completed task {i}",
                result_summary=f"Result {i}",
            ))

        # Disable other rules to isolate streak
        for r in self.notifier.rules:
            if r.name != "streak_achievement":
                r.enabled = False

        notifications = self.notifier.check_and_notify()
        titles = [n.title for n in notifications]
        assert any("连续" in t for t in titles)

    def test_no_notifications_when_nothing_new(self):
        """No notifications when nothing noteworthy happened."""
        notifications = self.notifier.check_and_notify()
        assert len(notifications) == 0

    def test_enable_disable_rule(self):
        """Rules can be enabled/disabled."""
        assert self.notifier.set_rule_enabled("high_confidence_finding", False)
        assert not self.notifier.rules[0].enabled  # first rule
        assert self.notifier.set_rule_enabled("high_confidence_finding", True)
        assert self.notifier.rules[0].enabled

    def test_set_cooldown(self):
        """Cooldown period can be changed."""
        assert self.notifier.set_rule_cooldown("high_confidence_finding", 1.0)
        rule = next(r for r in self.notifier.rules if r.name == "high_confidence_finding")
        assert rule.cooldown_hours == 1.0

    def test_notification_logging(self):
        """Notifications should be logged to file."""
        self.kb.add(KnowledgeEntry(
            title="Logged Finding",
            content="This should be logged.",
            confidence="high",
        ))
        self.notifier.check_and_notify()

        recent = self.notifier.get_recent_notifications(10)
        assert len(recent) >= 1
        assert "Logged Finding" in recent[0]["title"]

    def test_format_notifications(self):
        """Formatting should produce readable output."""
        notifs = [
            Notification(
                rule_name="test",
                title="🔑 Test Title",
                body="Test body content",
                priority="high",
            )
        ]
        formatted = self.notifier.format_notifications(notifs)
        assert "📬" in formatted
        assert "Test Title" in formatted
        assert "Test body" in formatted

    def test_format_empty_notifications(self):
        """Empty notifications list returns empty string."""
        assert self.notifier.format_notifications([]) == ""

    def test_get_status(self):
        """Status should return rule counts and focus areas."""
        status = self.notifier.get_status()
        assert "rules_count" in status
        assert "rules_enabled" in status
        assert status["rules_count"] == 4

    def test_focus_areas_from_tags(self):
        """Focus areas should be inferred from frequent knowledge tags."""
        # Add entries with common tags
        for i in range(3):
            self.kb.add(KnowledgeEntry(
                title=f"Entry {i}",
                content=f"Content about LLM {i}",
                tags=["llm", "ai"],
            ))

        areas = self.notifier._get_user_focus_areas(refresh_hours=0)
        assert "llm" in areas
        assert "ai" in areas


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
