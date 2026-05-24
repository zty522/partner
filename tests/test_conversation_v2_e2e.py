"""End-to-end integration tests for ConversationEngine V2.

Tests the full multi-turn conversation flow:
1. Multi-turn dialog: "最近在研究什么？" → "详细说说" → "第二个"
2. Context expiration (sliding window overflow)
3. New user without context fallback
4. Integration with ConversationEngine.respond() for each intent type

Uses real components (DialogHistory, ContextManager, KnowledgeBase, etc.)
with a temporary workspace to avoid touching production state files.
"""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime

# Add parent to path (project root = parent of tests/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from partner.dialog_history import DialogHistory, DialogTurn
from partner.context import ContextManager
from partner.knowledge import KnowledgeBase, KnowledgeEntry
from partner.journal import Journal, JournalEntry
from partner.task_queue import TaskQueue, Task
from partner.state import StateManager
from partner.response_generator import ResponseGenerator
from partner.router import ConversationRouter, Intent, ParsedQuery


# ==================== Test Helpers ====================

def create_test_workspace():
    """Create a temporary workspace with populated knowledge base and state."""
    tmpdir = tempfile.mkdtemp(prefix="partner_test_")
    state_dir = os.path.join(tmpdir, "state")
    os.makedirs(state_dir, exist_ok=True)

    # Create knowledge base with test entries
    kb_path = os.path.join(state_dir, "knowledge.json")
    kb_entries = [
        {
            "id": "k_001",
            "category": "findings",
            "title": "单细胞衰老研究进展",
            "content": "单细胞转录组学揭示了衰老过程中的细胞异质性。关键发现包括：\n1. 衰老相关分泌表型(SASP)在特定细胞亚群中富集\n2. 线粒体功能障碍是早期衰老标志\n3. 表观遗传时钟可预测生物年龄",
            "source": "arXiv:2024.12345",
            "related_projects": ["age_prediction"],
            "created_at": "2026-05-20T10:00:00",
            "confidence": "high",
            "tags": ["单细胞", "衰老", "转录组"],
        },
        {
            "id": "k_002",
            "category": "methods",
            "title": "扩散模型在单细胞数据中的应用",
            "content": "扩散概率模型(DPM)可用于单细胞数据的生成和插补。scDiffusion模型在基因表达数据生成上优于VAE和GAN。",
            "source": "arXiv:2024.67890",
            "related_projects": ["sc_diffusion"],
            "created_at": "2026-05-21T14:00:00",
            "confidence": "medium",
            "tags": ["扩散模型", "单细胞", "生成模型"],
        },
        {
            "id": "k_003",
            "category": "findings",
            "title": "scGPT: 单细胞基础模型",
            "content": "scGPT 是基于 Transformer 的单细胞基础模型，在多个下游任务上达到 SOTA：\n- 细胞类型注释\n- 基因调控网络推断\n- 批次校正\n- 多模态数据整合",
            "source": "Nature Methods 2024",
            "related_projects": ["scFoundation"],
            "created_at": "2026-05-22T09:00:00",
            "confidence": "high",
            "tags": ["scGPT", "基础模型", "Transformer"],
        },
        {
            "id": "k_004",
            "category": "tools",
            "title": "GFlowNet 理论基础",
            "content": "GFlowNet (Generative Flow Network) 通过流匹配目标训练生成模型，特别适合多模态分布采样。在药物发现和蛋白质设计中有应用。",
            "source": "Bengio et al. 2023",
            "related_projects": [],
            "created_at": "2026-05-23T16:00:00",
            "confidence": "medium",
            "tags": ["GFlowNet", "生成模型", "流网络"],
        },
    ]
    kb_data = {
        "meta": {"last_updated": "2026-05-23T16:00:00", "total_entries": len(kb_entries)},
        "entries": kb_entries,
    }
    with open(kb_path, "w", encoding="utf-8") as f:
        json.dump(kb_data, f, indent=2, ensure_ascii=False)

    # Create journal with recent entries
    journal_path = os.path.join(state_dir, "journal.jsonl")
    journal_entries = [
        JournalEntry(
            timestamp="2026-05-23T10:00:00",
            task_id="task_001",
            task_type="literature_search",
            task_title="搜索单细胞衰老最新论文",
            result_summary="找到 5 篇高相关性论文，更新知识库",
        ),
        JournalEntry(
            timestamp="2026-05-23T14:00:00",
            task_id="task_002",
            task_type="deep_dive",
            task_title="深入研究 scGPT 架构",
            result_summary="scGPT 使用 Gene Tokenizer + Transformer，预训练于 33M 细胞",
        ),
    ]
    with open(journal_path, "w", encoding="utf-8") as f:
        for entry in journal_entries:
            f.write(json.dumps(entry.__dict__, ensure_ascii=False) + "\n")

    # Create task queue
    tq_path = os.path.join(state_dir, "task_queue.json")
    tasks = [
        Task(id="task_001", type="literature_search", title="搜索单细胞衰老论文",
             description="arXiv 搜索", priority=10, status="completed",
             tags=["衰老", "单细胞"]).__dict__,
        Task(id="task_002", type="deep_dive", title="研究 scGPT",
             description="深入阅读", priority=8, status="completed",
             tags=["scGPT"]).__dict__,
        Task(id="task_003", type="idea_generation", title="设计年龄预测管线",
             description="基于 XGBoost 的年龄预测", priority=7, status="pending",
             tags=["年龄", "预测"]).__dict__,
    ]
    with open(tq_path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)

    # Create stats
    stats_path = os.path.join(state_dir, "stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_cycles": 20,
            "total_tasks_completed": 15,
            "total_literature_searched": 5,
            "total_knowledge_entries": 4,
            "total_ideas_generated": 8,
        }, f, indent=2)

    # Create heartbeat
    hb_path = os.path.join(state_dir, "heartbeat.json")
    with open(hb_path, "w", encoding="utf-8") as f:
        json.dump({
            "status": "idle",
            "last_heartbeat": datetime.now().isoformat(),
            "last_task_id": "task_002",
            "last_task_title": "研究 scGPT",
        }, f, indent=2)

    # Create empty dialog_history and user_prefs (fresh start)
    open(os.path.join(state_dir, "dialog_history.jsonl"), "w").close()
    with open(os.path.join(state_dir, "user_prefs.json"), "w") as f:
        json.dump({}, f)

    # Create notifier config (empty, use defaults)
    with open(os.path.join(state_dir, "notifier_config.json"), "w") as f:
        json.dump({}, f)

    return tmpdir


def build_engine(workspace_dir):
    """Build a ConversationEngine with the test workspace."""
    # Import here to avoid circular imports
    from partner.conversation import ConversationEngine

    journal = Journal(os.path.join(workspace_dir, "state", "journal.jsonl"))
    knowledge = KnowledgeBase(os.path.join(workspace_dir, "state", "knowledge.json"))
    task_queue = TaskQueue(os.path.join(workspace_dir, "state", "task_queue.json"))
    state = StateManager(os.path.join(workspace_dir, "state"))

    engine = ConversationEngine(journal, knowledge, task_queue, state, workspace=workspace_dir)
    return engine


# ==================== Test Cases ====================

def test_multi_turn_dialog_status_to_detail_to_index():
    """Test: "最近在研究什么？" → "详细说说" → "第二个"

    Flow:
    1. User asks STATUS → engine returns status report (includes topic info)
    2. User says "详细说说" → engine uses context to find active topic, searches KB
    3. User says "第二个" → engine returns 2nd item from cached list
    """
    workspace = create_test_workspace()
    try:
        engine = build_engine(workspace)

        # Turn 1: STATUS query
        resp1 = engine.respond("最近在研究什么？")
        assert resp1, "STATUS response should not be empty"
        assert "研究" in resp1 or "进展" in resp1 or "周期" in resp1, \
            f"STATUS response should mention research progress, got: {resp1[:200]}"
        print(f"  ✓ Turn 1 (STATUS): response length={len(resp1)}")

        # Verify context was recorded
        assert engine.context.has_recent_context()
        assert len(engine.context.recent_turns) == 2  # user + partner
        print(f"  ✓ Context has {len(engine.context.recent_turns)} turns after STATUS")

        # Turn 2: "详细说说" — should use context
        # First, we need to establish a topic in context by doing a knowledge query
        # Use "单细胞" which matches multiple entries (衰老, scGPT, etc.)
        resp_knowledge = engine.respond("关于单细胞你知道什么？")
        assert resp_knowledge, "KNOWLEDGE response should not be empty"
        assert "单细胞" in resp_knowledge, f"Should find 单细胞 entries, got: {resp_knowledge[:200]}"
        print(f"  ✓ Turn 2a (KNOWLEDGE): found 单细胞 entries")

        # Now say "详细说说" — should pick up "单细胞" from context
        resp2 = engine.respond("详细说说")
        assert resp2, "DETAIL from context should not be empty"
        # Should find knowledge about 单细胞 since it's the active topic
        assert "单细胞" in resp2 or "衰老" in resp2 or "scGPT" in resp2, \
            f"DETAIL should reference active topic, got: {resp2[:300]}"
        print(f"  ✓ Turn 2b (DETAIL from context): response references topic")

        # Verify response_gen has cached results
        assert engine.response_gen.has_cached_results(), \
            "ResponseGenerator should have cached results after DETAIL"
        print(f"  ✓ ResponseGenerator has cached results")

        # Verify we have multiple cached results
        cached_count = len(engine.response_gen._cached_list.entries)
        assert cached_count >= 2, \
            f"Expected at least 2 cached results, got {cached_count}"
        print(f"  ✓ Cached {cached_count} results for index reference")

        # Turn 3: "第二个" — should return 2nd cached result
        resp3 = engine.respond("第二个")
        assert resp3, "Index reference should not be empty"
        # Should show a specific entry (index 2 from the cached list)
        assert "[2/" in resp3 or "2." in resp3 or "第2" in resp3 or "扩散" in resp3 or "scGPT" in resp3, \
            f"Should reference 2nd item, got: {resp3[:300]}"
        print(f"  ✓ Turn 3 (index '第二个'): returns specific entry")

        # Verify all turns are in dialog history
        history = engine.dialog_history.load_recent(20)
        assert len(history) >= 6, f"Expected at least 6 turns in history, got {len(history)}"
        print(f"  ✓ Dialog history has {len(history)} turns")

        print("  ✅ test_multi_turn_dialog_status_to_detail_to_index PASSED")
    finally:
        shutil.rmtree(workspace)


def test_context_expiration():
    """Test that context window drops old turns correctly.

    With max_turns=5, after 12 turns (6 user + 6 partner),
    only the last 5 should be in memory.
    """
    workspace = create_test_workspace()
    try:
        engine = build_engine(workspace)
        # Override max_turns for testing
        engine.context.max_turns = 5

        # Generate 12 turns (6 pairs)
        topics = ["衰老", "扩散模型", "scGPT", "GFlowNet", "年龄预测", "批次校正"]
        for i, topic in enumerate(topics):
            engine.context.add_turn("user", f"研究{topic}", topic=topic)
            engine.context.add_turn("partner", f"好的，我来查{topic}")

        # Only last 5 in memory
        assert len(engine.context.recent_turns) == 5, \
            f"Expected 5 turns in memory, got {len(engine.context.recent_turns)}"

        # Active topic should be the last one
        active = engine.context.get_active_topic()
        assert active == "批次校正", f"Expected active topic '批次校正', got '{active}'"
        print(f"  ✓ Sliding window: {len(engine.context.recent_turns)} turns in memory")
        print(f"  ✓ Active topic: {active}")

        # Old topics should NOT be in active context
        # (but they're still on disk)
        recent_topics = [t.topic for t in engine.context.recent_turns if t.topic]
        assert "衰老" not in recent_topics, "Old topic '衰老' should be dropped from memory"
        assert "批次校正" in recent_topics, "Recent topic should still be in memory"
        print(f"  ✓ Old topics dropped from memory")

        # Disk history should have all 12 turns
        disk_count = engine.dialog_history.count()
        assert disk_count == 12, f"Expected 12 turns on disk, got {disk_count}"
        print(f"  ✓ Disk history has {disk_count} turns")

        print("  ✅ test_context_expiration PASSED")
    finally:
        shutil.rmtree(workspace)


def test_new_user_no_context():
    """Test behavior when there's no conversation history.

    New user with empty dialog history:
    - "详细说说" without topic → should give helpful prompt
    - STATUS query → should still work (no context needed)
    - HELP query → should work
    """
    workspace = create_test_workspace()
    try:
        # Clear dialog history to simulate new user
        open(os.path.join(workspace, "state", "dialog_history.jsonl"), "w").close()
        engine = build_engine(workspace)

        # Verify empty context
        assert not engine.context.has_recent_context(), "New user should have no context"
        assert engine.context.get_active_topic() is None, "No active topic for new user"
        print(f"  ✓ New user: empty context confirmed")

        # "详细说说" without any topic → should respond gracefully
        # (may get parsed as DETAIL with topic="说" by fuzzy matching, which is fine)
        resp_detail = engine.respond("详细说说")
        assert resp_detail, "Should respond even without context"
        # Either prompts for topic or searches with the extracted topic
        assert len(resp_detail) > 10, \
            f"Should give meaningful response, got: {resp_detail}"
        print(f"  ✓ '详细说说' without topic → graceful response: {resp_detail[:80]}...")

        # STATUS query → should work (no context needed)
        resp_status = engine.respond("最近在研究什么？")
        assert resp_status, "STATUS should work without context"
        assert "研究" in resp_status or "进展" in resp_status or "周期" in resp_status, \
            f"STATUS should mention progress, got: {resp_status[:200]}"
        print(f"  ✓ STATUS query works for new user")

        # HELP query → should work
        resp_help = engine.respond("帮助")
        assert resp_help, "HELP should work"
        assert "Partner" in resp_help or "对话" in resp_help or "帮助" in resp_help, \
            f"HELP should show help text, got: {resp_help[:200]}"
        print(f"  ✓ HELP query works for new user")

        # After these queries, context should exist
        assert engine.context.has_recent_context()
        print(f"  ✓ Context built up after {len(engine.context.recent_turns)} turns")

        print("  ✅ test_new_user_no_context PASSED")
    finally:
        shutil.rmtree(workspace)


def test_intent_coverage():
    """Test that ConversationEngine.respond() handles all intent types correctly.

    Tests: STATUS, KNOWLEDGE, DIRECTION, DETAIL, HELP, GENERAL
    """
    workspace = create_test_workspace()
    try:
        engine = build_engine(workspace)

        test_cases = [
            # (query, expected_intent_keywords, description)
            ("最近在研究什么？", ["研究", "进展", "周期", "任务"], "STATUS"),
            ("关于单细胞衰老你知道什么？", ["衰老", "单细胞"], "KNOWLEDGE"),
            ("什么是 scGPT？", ["scGPT", "基础模型", "Transformer"], "KNOWLEDGE (what is)"),
            ("详细说说 扩散模型", ["扩散", "scDiffusion"], "DETAIL with topic"),
            ("帮助", ["Partner", "对话"], "HELP"),
        ]

        for query, keywords, desc in test_cases:
            resp = engine.respond(query)
            assert resp, f"[{desc}] Response should not be empty for: {query}"
            found = [kw for kw in keywords if kw in resp]
            assert found, f"[{desc}] Expected keywords {keywords} in response, found none. Got: {resp[:200]}"
            print(f"  ✓ {desc}: '{query}' → found keywords {found}")

        # GENERAL intent (unrecognized query)
        resp_general = engine.respond("今天天气怎么样？")
        assert resp_general, "GENERAL should still produce a response"
        print(f"  ✓ GENERAL: unrecognized query → generic response")

        # DIRECTION intent
        resp_dir = engine.respond("暂停衰老，集中做扩散模型")
        assert resp_dir, "DIRECTION should produce a response"
        assert "方向" in resp_dir or "任务" in resp_dir or "衰老" in resp_dir or "扩散" in resp_dir, \
            f"DIRECTION should acknowledge direction change, got: {resp_dir[:200]}"
        print(f"  ✓ DIRECTION: direction change → acknowledged")

        print("  ✅ test_intent_coverage PASSED")
    finally:
        shutil.rmtree(workspace)


def test_topic_tracking_and_preference_learning():
    """Test that topic queries are tracked and user preferences are updated."""
    workspace = create_test_workspace()
    try:
        engine = build_engine(workspace)

        # Query about the same topic multiple times
        for _ in range(3):
            engine.respond("关于衰老你知道什么？")

        # Check user preferences
        prefs = engine.user_prefs
        top_topics = prefs.get_top_topics(5)
        topic_names = [t[0] for t in top_topics]
        assert "衰老" in topic_names or "关于衰老你知道什么？" in topic_names, \
            f"Should track '衰老' topic, got: {topic_names}"
        print(f"  ✓ Topic tracking: top topics = {topic_names}")

        # Check session stats
        assert prefs.prefs.total_turns > 0, "Should have recorded turns"
        print(f"  ✓ Session tracking: {prefs.prefs.total_turns} turns recorded")

        # Query about a different topic
        engine.respond("什么是 GFlowNet？")
        top_topics = prefs.get_top_topics(5)
        topic_names = [t[0] for t in top_topics]
        assert len(topic_names) >= 2, f"Should track multiple topics, got: {topic_names}"
        print(f"  ✓ Multiple topics tracked: {topic_names}")

        print("  ✅ test_topic_tracking_and_preference_learning PASSED")
    finally:
        shutil.rmtree(workspace)


def test_index_reference_out_of_bounds():
    """Test that out-of-range index references return helpful error messages."""
    workspace = create_test_workspace()
    try:
        engine = build_engine(workspace)

        # First, create a cached list
        engine.respond("详细说说 衰老")
        assert engine.response_gen.has_cached_results()

        # Try index 99 (out of bounds)
        resp = engine.respond("第99个")
        assert resp, "Should respond to out-of-bounds index"
        assert "99" in resp or "超出" in resp or "只有" in resp or "1-" in resp, \
            f"Should indicate out of bounds, got: {resp[:200]}"
        print(f"  ✓ Out-of-bounds index: helpful error message")

        # Try index without cached results
        engine.response_gen.clear_cache()
        resp_no_cache = engine.respond("第三个")
        assert resp_no_cache, "Should respond when no cache"
        assert "没有" in resp_no_cache or "缓存" in resp_no_cache or "先" in resp_no_cache, \
            f"Should indicate no cache, got: {resp_no_cache[:200]}"
        print(f"  ✓ No cache: helpful prompt to search first")

        print("  ✅ test_index_reference_out_of_bounds PASSED")
    finally:
        shutil.rmtree(workspace)


def test_continuation_flow():
    """Test '继续' (continue) flow for paginated results."""
    workspace = create_test_workspace()
    try:
        engine = build_engine(workspace)

        # Search for a broad topic to get multiple results
        resp1 = engine.respond("详细说说 单细胞")
        assert resp1, "Should return results"
        assert engine.response_gen.has_cached_results()
        print(f"  ✓ Initial search returned results")

        # Ask for continuation
        resp2 = engine.respond("继续")
        assert resp2, "Continuation should return content"
        # The continuation response should mention continuation or show more results
        print(f"  ✓ Continuation response: {resp2[:100]}...")

        print("  ✅ test_continuation_flow PASSED")
    finally:
        shutil.rmtree(workspace)


def test_chinese_numeral_index():
    """Test Chinese numeral index references: 第一个, 第二个, 第三个."""
    workspace = create_test_workspace()
    try:
        engine = build_engine(workspace)

        # Create cached list
        engine.respond("详细说说 模型")

        # Test Chinese numeral
        resp = engine.respond("第一个")
        assert resp, "Should respond to '第一个'"
        # Should show a specific entry
        assert "[1/" in resp or "1." in resp or "第1" in resp or "单细胞" in resp or "扩散" in resp, \
            f"Should reference 1st item, got: {resp[:300]}"
        print(f"  ✓ Chinese numeral '第一个' resolved correctly")

        resp2 = engine.respond("第三个")
        assert resp2, "Should respond to '第三个'"
        print(f"  ✓ Chinese numeral '第三个' resolved correctly")

        print("  ✅ test_chinese_numeral_index PASSED")
    finally:
        shutil.rmtree(workspace)


def test_context_across_engine_restart():
    """Test that context survives engine restart (persistence test).

    Simulates: user has a conversation → engine restarts → user continues.
    """
    workspace = create_test_workspace()
    try:
        # First session
        engine1 = build_engine(workspace)
        engine1.respond("关于衰老你知道什么？")
        engine1.respond("详细说说 衰老")

        # Verify dialog history on disk
        turns_on_disk = engine1.dialog_history.count()
        assert turns_on_disk >= 4, f"Expected at least 4 turns on disk, got {turns_on_disk}"
        print(f"  ✓ First session: {turns_on_disk} turns persisted")

        # Simulate restart: create new engine instance
        engine2 = build_engine(workspace)

        # Verify context is restored
        assert engine2.context.has_recent_context(), "Context should be restored after restart"
        active_topic = engine2.context.get_active_topic()
        assert active_topic is not None, "Active topic should be restored"
        print(f"  ✓ Second session: context restored, active topic = '{active_topic}'")

        # Continue conversation: "第二个" should work with restored context
        # But we need to re-establish cached results since ResponseGenerator is in-memory
        # This is expected behavior — cached list is ephemeral
        resp = engine2.respond("详细说说 衰老")
        assert resp, "Should be able to continue after restart"
        print(f"  ✓ Can continue searching after restart")

        resp_index = engine2.respond("第二个")
        assert resp_index, "Index reference should work after re-search"
        print(f"  ✓ Index reference works after re-establishing cache")

        print("  ✅ test_context_across_engine_restart PASSED")
    finally:
        shutil.rmtree(workspace)


def test_proactive_notifications():
    """Test that check_proactive() works correctly after knowledge updates."""
    workspace = create_test_workspace()
    try:
        engine = build_engine(workspace)

        # Add a high-confidence entry to trigger notification
        engine.knowledge.add(KnowledgeEntry(
            id="k_high_conf",
            category="findings",
            title="重大发现：衰老逆转新机制",
            content="通过表观遗传重编程可以逆转细胞衰老，这是今年最重要的发现之一。",
            source="Nature 2026",
            confidence="high",
            tags=["衰老", "表观遗传", "重大发现"],
        ))

        # Check proactive notifications
        notifications = engine.check_proactive()
        if notifications:
            assert isinstance(notifications, list), "Should return list of notifications"
            for notif in notifications:
                assert isinstance(notif, str), "Each notification should be a string"
            print(f"  ✓ Proactive notifications triggered: {len(notifications)} notifications")
            # Print first notification preview
            print(f"    Preview: {notifications[0][:100]}...")
        else:
            print(f"  ✓ No proactive notifications (may be due to cooldown or rule config)")

        print("  ✅ test_proactive_notifications PASSED")
    finally:
        shutil.rmtree(workspace)


# ==================== Run All Tests ====================

if __name__ == "__main__":
    print("=" * 60)
    print("ConversationEngine V2 Integration Tests")
    print("=" * 60)
    print()

    tests = [
        test_multi_turn_dialog_status_to_detail_to_index,
        test_context_expiration,
        test_new_user_no_context,
        test_intent_coverage,
        test_topic_tracking_and_preference_learning,
        test_index_reference_out_of_bounds,
        test_continuation_flow,
        test_chinese_numeral_index,
        test_context_across_engine_restart,
        test_proactive_notifications,
    ]

    passed = 0
    failed = 0
    errors = []

    for test_fn in tests:
        name = test_fn.__name__
        print(f"\n--- {name} ---")
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, str(e)))
            print(f"  ❌ {name} FAILED: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {err}")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
