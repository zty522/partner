"""Tests for ResponseGenerator and multi-turn conversation (Phase 2).

Tests:
1. ResponseGenerator standalone functionality
2. List result caching
3. Index resolution ("第一个", "第二个")
4. Continuation pagination ("继续")
5. Integration with ConversationEngine
6. Edge cases (empty cache, out-of-range index)
"""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from partner.knowledge import KnowledgeBase, KnowledgeEntry
from partner.response_generator import ResponseGenerator, CachedListResult


# ==================== Helpers ====================

def create_test_kb(tmpdir: str, entries=None) -> KnowledgeBase:
    """Create a KnowledgeBase with test entries."""
    path = os.path.join(tmpdir, "knowledge.json")
    kb = KnowledgeBase(path)
    if entries is None:
        entries = [
            KnowledgeEntry(
                id="k_001", category="findings", title="scGPT单细胞基础模型",
                content="scGPT是一个基于Transformer的单细胞基础模型，用于单细胞组学数据的多任务学习。",
                source="Nature Methods 2024", confidence="high",
                tags=["scGPT", "single-cell", "foundation-model"],
            ),
            KnowledgeEntry(
                id="k_002", category="findings", title="Geneformer基因表达模型",
                content="Geneformer是基于BERT架构的基因表达基础模型，在Cell 2023发表。",
                source="Cell 2023", confidence="high",
                tags=["Geneformer", "gene-expression"],
            ),
            KnowledgeEntry(
                id="k_003", category="methods", title="scFoundation大规模预训练",
                content="scFoundation在1亿单细胞数据上预训练，支持多种下游任务。",
                source="bioRxiv 2024", confidence="medium",
                tags=["scFoundation", "pre-training"],
            ),
            KnowledgeEntry(
                id="k_004", category="findings", title="单细胞衰老时钟模型",
                content="基于单细胞转录组数据的衰老时钟模型，可以预测细胞年龄。",
                source="Aging Cell 2023", confidence="medium",
                tags=["aging", "clock", "single-cell"],
            ),
            KnowledgeEntry(
                id="k_005", category="tools", title="Scanpy单细胞分析工具",
                content="Scanpy是Python生态中最常用的单细胞分析工具包。",
                source="Genome Biology 2018", confidence="high",
                tags=["scanpy", "analysis", "tool"],
            ),
            KnowledgeEntry(
                id="k_006", category="concepts", title="GFlowNet生成流网络",
                content="GFlowNet是一种生成模型框架，用于生成多样性样本。",
                source="NeurIPS 2021", confidence="medium",
                tags=["GFlowNet", "generative-model"],
            ),
        ]
    for e in entries:
        kb.add(e)
    return kb


def make_kb_only(tmpdir):
    """Return just the KnowledgeBase for standalone RG tests."""
    return create_test_kb(tmpdir)


# ==================== ResponseGenerator Standalone Tests ====================

def test_rg_initial_state():
    """Test ResponseGenerator initial state."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        assert not rg.has_cached_results()
        assert rg.get_cached_topic() is None
        assert rg.resolve_index_from_query("第二个") == 2
        assert not rg.is_continuation("第二个")
        assert rg.is_continuation("继续")
        assert rg.is_elaborate_request("详细说说")
        print("  ✓ ResponseGenerator initial state")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_search_and_cache():
    """Test that searching caches results."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        # First search
        result = rg.handle_detail("单细胞")
        assert "单细胞" in result
        assert rg.has_cached_results()
        assert rg.get_cached_topic() == "单细胞"
        assert len(rg._cached_list.entries) > 0
        print("  ✓ ResponseGenerator search and cache")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_index_resolution():
    """Test "第二个" type index resolution."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        # Populate cache
        rg.handle_detail("单细胞")

        # Index patterns
        assert rg.resolve_index_from_query("第一个") == 1
        assert rg.resolve_index_from_query("第二个") == 2
        assert rg.resolve_index_from_query("第3个") == 3
        assert rg.resolve_index_from_query("5") == 5
        assert rg.resolve_index_from_query("2") == 2
        assert rg.resolve_index_from_query("#3") == 3
        assert rg.resolve_index_from_query("number 4") == 4
        assert rg.resolve_index_from_query("你好") is None
        assert rg.resolve_index_from_query("第二个") == 2  # Chinese numeral

        # Resolve valid index
        detail = rg.handle_detail("单细胞", index=1)
        assert "scGPT" in detail or "findings" in detail  # should show entry 1

        detail = rg.handle_detail("单细胞", index=2)
        assert "Geneformer" in detail or "findings" in detail

        # Resolve out-of-range index
        detail = rg.handle_detail("单细胞", index=100)
        assert "只有" in detail  # error message

        detail = rg.handle_detail("单细胞", index=0)
        assert "只有" in detail  # error message

        print("  ✓ ResponseGenerator index resolution")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_index_without_cache():
    """Test index resolution without prior cache."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        detail = rg.handle_detail("单细胞", index=2)
        assert "暂时没有缓存" in detail

        print("  ✓ ResponseGenerator index without cache")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_continuation():
    """Test "继续" pagination."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)
        rg.PAGE_SIZE = 2  # Small page for testing

        # Initial search
        result1 = rg.handle_detail("单细胞")
        assert rg.has_cached_results()

        # Continue
        result2 = rg.handle_detail("单细胞", continuation=True)
        assert result2  # should show next page

        # Continue again — might hit end
        result3 = rg.handle_detail("单细胞", continuation=True)

        print("  ✓ ResponseGenerator continuation")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_continuation_no_cache():
    """Test continuation without cache."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        result = rg.handle_detail("单细胞", continuation=True)
        assert "没有可以继续" in result

        print("  ✓ ResponseGenerator continuation without cache")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_no_topic_no_cache():
    """Test no topic and no cache."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        result = rg.handle_detail(None)
        assert "你想详细了解什么" in result

        print("  ✓ ResponseGenerator no topic no cache")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_no_topic_with_cache():
    """Test no topic but cache exists."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        # Populate cache
        rg.handle_detail("单细胞")

        # Now query with no topic
        result = rg.handle_detail(None)
        assert "上次查询" in result
        assert "单细胞" in result

        print("  ✓ ResponseGenerator no topic with cache")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_no_results():
    """Test search with no matching results."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        result = rg.handle_detail("完全不存在的话题XYZ123")
        assert "没有找到相关内容" in result

        print("  ✓ ResponseGenerator no results")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_clear_cache():
    """Test cache clearing."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        rg.handle_detail("单细胞")
        assert rg.has_cached_results()

        rg.clear_cache()
        assert not rg.has_cached_results()
        assert rg.get_cached_topic() is None

        print("  ✓ ResponseGenerator clear cache")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_single_entry_format():
    """Test detailed view of a single entry has navigation hints."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        # Populate cache
        rg.handle_detail("单细胞")

        # View entry 2 (middle entry — should have prev and next)
        detail = rg.handle_detail("单细胞", index=2)
        assert "2/" in detail  # index/total format
        assert "Geneformer" in detail or "上一条" in detail or "下一条" in detail

        # View entry 1 (first — no prev)
        detail = rg.handle_detail("单细胞", index=1)
        assert "1/" in detail

        print("  ✓ ResponseGenerator single entry format")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_is_continuation_patterns():
    """Test continuation pattern matching."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        assert rg.is_continuation("继续")
        assert rg.is_continuation("然后呢")
        assert rg.is_continuation("还有呢")
        assert rg.is_continuation("接下来呢")
        assert rg.is_continuation("go on")
        assert rg.is_continuation("next")
        assert not rg.is_continuation("第二个")
        assert not rg.is_continuation("你好")
        assert not rg.is_continuation("详细说说")

        print("  ✓ ResponseGenerator continuation patterns")
    finally:
        shutil.rmtree(tmpdir)


def test_rg_elaborate_patterns():
    """Test elaborate request pattern matching."""
    tmpdir = tempfile.mkdtemp()
    try:
        kb = make_kb_only(tmpdir)
        rg = ResponseGenerator(kb)

        assert rg.is_elaborate_request("详细说说")
        assert rg.is_elaborate_request("展开讲讲")
        assert rg.is_elaborate_request("具体说说")
        assert rg.is_elaborate_request("这个")
        assert rg.is_elaborate_request("那个")
        assert rg.is_elaborate_request("elaborate")
        assert not rg.is_elaborate_request("第二个")
        assert not rg.is_elaborate_request("继续")
        assert not rg.is_elaborate_request("你好")

        print("  ✓ ResponseGenerator elaborate patterns")
    finally:
        shutil.rmtree(tmpdir)


# ==================== Integration Tests ====================

def test_integration_conversation_multi_turn():
    """Test full multi-turn conversation flow through ConversationEngine."""
    tmpdir = tempfile.mkdtemp()
    try:
        # Set up state directory
        state_dir = os.path.join(tmpdir, "state")
        os.makedirs(state_dir, exist_ok=True)

        # Create knowledge base with test data
        kb = create_test_kb(tmpdir)
        kb_path = kb.path  # save path

        # Import and create engine components
        from partner.task_queue import TaskQueue
        from partner.journal import Journal
        from partner.state import StateManager
        from partner.conversation import ConversationEngine

        tq = TaskQueue(os.path.join(state_dir, "task_queue.json"))
        journal = Journal(os.path.join(state_dir, "journal.jsonl"))
        state = StateManager(state_dir)

        engine = ConversationEngine(journal, kb, tq, state, workspace=tmpdir)

        # Turn 1: Ask about a topic
        r1 = engine.respond("详细说说 单细胞")
        assert "单细胞" in r1
        assert engine.response_gen.has_cached_results()

        # Turn 2: Ask for "第二个"
        r2 = engine.respond("第二个")
        assert r2  # should have content
        assert "2/" in r2 or "findings" in r2 or "Geneformer" in r2

        # Turn 3: Ask for "继续"
        r3 = engine.respond("继续")
        assert r3  # should have content

        print("  ✓ Integration: multi-turn conversation flow")
    finally:
        shutil.rmtree(tmpdir)


def test_integration_new_topic_clears_cache():
    """Test that a new topic query clears the old cache."""
    tmpdir = tempfile.mkdtemp()
    try:
        state_dir = os.path.join(tmpdir, "state")
        os.makedirs(state_dir, exist_ok=True)

        kb = create_test_kb(tmpdir)

        from partner.task_queue import TaskQueue
        from partner.journal import Journal
        from partner.state import StateManager
        from partner.conversation import ConversationEngine

        tq = TaskQueue(os.path.join(state_dir, "task_queue.json"))
        journal = Journal(os.path.join(state_dir, "journal.jsonl"))
        state = StateManager(state_dir)

        engine = ConversationEngine(journal, kb, tq, state, workspace=tmpdir)

        # Query topic A
        engine.respond("详细说说 单细胞")
        assert engine.response_gen.has_cached_results()
        assert engine.response_gen.get_cached_topic() == "单细胞"

        # Query topic B — should clear cache for topic A
        engine.respond("详细说说 GFlowNet")
        assert engine.response_gen.get_cached_topic() == "GFlowNet"

        print("  ✓ Integration: new topic clears cache")
    finally:
        shutil.rmtree(tmpdir)


def test_integration_context_persistence():
    """Test that context persists across conversation turns."""
    tmpdir = tempfile.mkdtemp()
    try:
        state_dir = os.path.join(tmpdir, "state")
        os.makedirs(state_dir, exist_ok=True)

        kb = create_test_kb(tmpdir)

        from partner.task_queue import TaskQueue
        from partner.journal import Journal
        from partner.state import StateManager
        from partner.conversation import ConversationEngine

        tq = TaskQueue(os.path.join(state_dir, "task_queue.json"))
        journal = Journal(os.path.join(state_dir, "journal.jsonl"))
        state = StateManager(state_dir)

        engine = ConversationEngine(journal, kb, tq, state, workspace=tmpdir)

        # First exchange
        engine.respond("详细说说 单细胞")

        # Context should have the topic
        topic = engine.context.get_active_topic()
        assert topic == "单细胞"

        # Second exchange — "第二个" should resolve to context topic
        r2 = engine.respond("第二个")
        assert r2
        # Should resolve using context + cache
        assert "暂时没有缓存" not in r2  # cache should be active

        print("  ✓ Integration: context persistence across turns")
    finally:
        shutil.rmtree(tmpdir)


# ==================== Run All Tests ====================

if __name__ == "__main__":
    print("Running ResponseGenerator tests...\n")

    # Standalone tests
    test_rg_initial_state()
    test_rg_search_and_cache()
    test_rg_index_resolution()
    test_rg_index_without_cache()
    test_rg_continuation()
    test_rg_continuation_no_cache()
    test_rg_no_topic_no_cache()
    test_rg_no_topic_with_cache()
    test_rg_no_results()
    test_rg_clear_cache()
    test_rg_single_entry_format()
    test_rg_is_continuation_patterns()
    test_rg_elaborate_patterns()

    # Integration tests
    test_integration_conversation_multi_turn()
    test_integration_new_topic_clears_cache()
    test_integration_context_persistence()

    print("\n✅ All ResponseGenerator tests passed!")
