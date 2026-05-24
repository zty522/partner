"""Tests for DialogHistory, ContextManager, and ConversationEngine V2."""

import json
import os
import sys
import tempfile
import shutil

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from partner.dialog_history import DialogHistory, DialogTurn
from partner.context import ContextManager


def test_dialog_turn_dataclass():
    """Test DialogTurn creation and serialization."""
    turn = DialogTurn(role="user", content="你好", timestamp="2026-01-01T00:00:00")
    assert turn.role == "user"
    assert turn.content == "你好"
    assert turn.intent is None
    assert turn.topic is None

    turn_with_meta = DialogTurn(
        role="partner", content="你好！",
        timestamp="2026-01-01T00:00:01",
        intent="status", topic="研究进展"
    )
    assert turn_with_meta.intent == "status"
    assert turn_with_meta.topic == "研究进展"
    print("  ✓ DialogTurn dataclass")


def test_dialog_history_append_and_load():
    """Test appending turns and loading recent ones."""
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "test_history.jsonl")
        history = DialogHistory(path)

        # Append several turns
        for i in range(5):
            history.append(DialogTurn(
                role="user" if i % 2 == 0 else "partner",
                content=f"消息 {i}",
                timestamp=f"2026-01-01T00:0{i}:00",
            ))

        # Load all
        turns = history.load_recent(10)
        assert len(turns) == 5
        assert turns[0].content == "消息 0"
        assert turns[4].content == "消息 4"

        # Load limited
        turns_2 = history.load_recent(2)
        assert len(turns_2) == 2
        assert turns_2[0].content == "消息 3"
        assert turns_2[1].content == "消息 4"

        # Count
        assert history.count() == 5
        print("  ✓ DialogHistory append & load")
    finally:
        shutil.rmtree(tmpdir)


def test_dialog_history_search():
    """Test searching by topic keyword."""
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "test_history.jsonl")
        history = DialogHistory(path)

        history.append(DialogTurn(role="user", content="研究单细胞衰老", timestamp="t1", topic="衰老"))
        history.append(DialogTurn(role="partner", content="我来查查", timestamp="t2"))
        history.append(DialogTurn(role="user", content="GFlowNet是什么", timestamp="t3", topic="GFlowNet"))
        history.append(DialogTurn(role="partner", content="GFlowNet是一种...", timestamp="t4"))

        results = history.search_by_topic("衰老")
        assert len(results) == 1
        assert "衰老" in results[0].content

        results = history.search_by_topic("GFlowNet")
        assert len(results) == 2  # matches topic and content
        print("  ✓ DialogHistory search_by_topic")
    finally:
        shutil.rmtree(tmpdir)


def test_context_manager():
    """Test ContextManager sliding window and topic tracking."""
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "test_history.jsonl")
        ctx = ContextManager(path, max_turns=5)

        # Initially empty
        assert not ctx.has_recent_context()
        assert ctx.get_active_topic() is None
        assert ctx.get_context_summary() == ""

        # Add turns
        ctx.add_turn("user", "研究单细胞衰老", topic="单细胞衰老")
        ctx.add_turn("partner", "好的，我来搜索一下")
        ctx.add_turn("user", "有什么发现？")

        assert ctx.has_recent_context()
        assert ctx.get_active_topic() == "单细胞衰老"

        summary = ctx.get_context_summary()
        assert "对话上下文" in summary
        assert "单细胞衰老" in summary
        assert "用户: 研究单细胞衰老" in summary

        # Last partner response
        assert ctx.get_last_partner_response() == "好的，我来搜索一下"

        # Recent user messages
        user_msgs = ctx.get_recent_user_messages(2)
        assert len(user_msgs) == 2
        assert user_msgs[0] == "研究单细胞衰老"
        assert user_msgs[1] == "有什么发现？"

        print("  ✓ ContextManager basic operations")
    finally:
        shutil.rmtree(tmpdir)


def test_context_manager_sliding_window():
    """Test that sliding window drops old turns."""
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "test_history.jsonl")
        ctx = ContextManager(path, max_turns=3)

        for i in range(6):
            ctx.add_turn("user", f"消息 {i}", topic=f"topic_{i}")

        # Only last 3 in memory
        assert len(ctx.recent_turns) == 3
        assert ctx.recent_turns[0].content == "消息 3"
        assert ctx.recent_turns[2].content == "消息 5"

        # But all 6 on disk
        assert ctx.history.count() == 6

        # Active topic is the last one
        assert ctx.get_active_topic() == "topic_5"

        print("  ✓ ContextManager sliding window")
    finally:
        shutil.rmtree(tmpdir)


def test_context_persistence():
    """Test that context survives across ContextManager instances."""
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "test_history.jsonl")

        # First session
        ctx1 = ContextManager(path, max_turns=5)
        ctx1.add_turn("user", "研究衰老", topic="衰老")
        ctx1.add_turn("partner", "收到")

        # Second session (simulates restart)
        ctx2 = ContextManager(path, max_turns=5)
        assert ctx2.has_recent_context()
        assert ctx2.get_active_topic() == "衰老"
        assert len(ctx2.recent_turns) == 2

        print("  ✓ ContextManager persistence across sessions")
    finally:
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    print("Running DialogHistory & ContextManager tests...\n")
    test_dialog_turn_dataclass()
    test_dialog_history_append_and_load()
    test_dialog_history_search()
    test_context_manager()
    test_context_manager_sliding_window()
    test_context_persistence()
    print("\n✅ All tests passed!")
