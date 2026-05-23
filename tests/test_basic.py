"""Basic tests for Partner."""

import os
import sys
import json
import tempfile
import shutil

# Add parent to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from partner.config import PartnerConfig, WorkspaceConfig
from partner.task_queue import TaskQueue, Task
from partner.knowledge import KnowledgeBase, KnowledgeEntry
from partner.journal import Journal, JournalEntry
from partner.state import StateManager
from partner.conversation import ConversationEngine
from partner.core import Partner


def test_basic():
    """Run basic tests."""
    # Create temp workspace
    workspace = tempfile.mkdtemp(prefix="partner_test_")
    
    try:
        # Test config
        config = PartnerConfig(
            workspace=WorkspaceConfig(path=workspace),
        )
        config_path = os.path.join(workspace, "config.json")
        config.save(config_path)
        loaded = PartnerConfig.load(config_path)
        assert loaded.workspace.path == workspace
        print("✅ Config: save/load works")
        
        # Test task queue
        tq_path = os.path.join(workspace, "task_queue.json")
        tq = TaskQueue(tq_path)
        task = Task(title="Test task", description="A test", priority=5)
        tq.add_task(task)
        assert len(tq.tasks) == 1
        next_task = tq.get_next()
        assert next_task.title == "Test task"
        tq.complete(task.id, "Done")
        assert tq.tasks[0].status == "completed"
        print("✅ TaskQueue: add/get_next/complete works")
        
        # Test knowledge base
        kb_path = os.path.join(workspace, "knowledge.json")
        kb = KnowledgeBase(kb_path)
        entry = KnowledgeEntry(title="Test finding", content="Something interesting", category="findings")
        kb.add(entry)
        assert len(kb.entries) == 1
        results = kb.search("interesting")
        assert len(results) == 1
        print("✅ KnowledgeBase: add/search works")
        
        # Test journal
        j_path = os.path.join(workspace, "journal.jsonl")
        j = Journal(j_path)
        j.log(JournalEntry(task_title="Test log", result_summary="All good"))
        assert len(j.entries) == 1
        recent = j.get_recent(5)
        assert len(recent) == 1
        print("✅ Journal: log/get_recent works")
        
        # Test state manager
        sm = StateManager(workspace)
        sm.heartbeat(status="working")
        assert sm.is_alive(timeout_minutes=5)
        assert not sm.detect_crash()
        print("✅ StateManager: heartbeat/is_alive works")
        
        # Test conversation
        conv = ConversationEngine(j, kb, tq, sm)
        response = conv.respond("最近在干什么")
        assert "研究进展" in response or "活动" in response
        print("✅ ConversationEngine: status query works")
        
        response = conv.respond("帮助")
        assert "Partner" in response
        print("✅ ConversationEngine: help works")
        
        # Test full Partner
        p = Partner(config)
        p.start()
        status = p.status()
        assert "研究" in status or "周期" in status
        print("✅ Partner: start/status works")
        
        # Test chat
        response = p.chat("最近干了什么？")
        assert len(response) > 0
        print("✅ Partner: chat works")
        
        print("\n🎉 All tests passed!")
        
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    test_basic()
