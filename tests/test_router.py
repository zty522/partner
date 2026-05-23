"""Tests for ConversationRouter - intent classification and routing."""

import sys
import os

# Add parent to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from partner.router import ConversationRouter, Intent, ParsedQuery


class MockState:
    """Mock StateManager for testing."""
    def load_stats(self):
        return {"total_cycles": 22, "total_tasks_completed": 23}


class MockEntry:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockJournal:
    """Mock Journal for testing."""
    def get_recent(self, n=5):
        return [
            MockEntry(timestamp="2026-05-23T14:49:25", task_title="研究 OpenClaw 架构",
                       result_summary="分析了 OpenClaw 的插件系统"),
            MockEntry(timestamp="2026-05-23T14:19:28", task_title="设计 Agent 适配层",
                       result_summary="创建了完整的适配层设计文档"),
        ]


class MockKnowledge:
    """Mock KnowledgeBase for testing."""
    def __init__(self):
        self.entries = [
            MockEntry(id="k_001", category="findings",
                      title="单细胞基础模型在衰老建模上尚不成熟",
                      content="通用基础模型需要领域微调才能有效。Tadevosyan等人在AgeAnno上微调scGPT。",
                      source="PMID:41465404", related_projects=["age_prediction"],
                      created_at="2026-05-23T02:59:59", confidence="high",
                      tags=["scGPT", "aging", "foundation_model"]),
            MockEntry(id="k_013", category="findings",
                      title="扩散模型在分子生成中的最新进展",
                      content="离散扩散模型已成为分子生成的强大范式。ProDCARL使用RL对齐扩散模型。",
                      source="arXiv", related_projects=["molecular_generation", "acinetobacter"],
                      created_at="2026-05-23T05:51:49", confidence="high",
                      tags=["diffusion", "molecular_generation"]),
        ]
    def stats(self):
        return {"total": 28, "by_category": {"findings": 8, "methods": 10, "tools": 5, "concepts": 3, "pitfalls": 2}}
    def search(self, query, top_k=5):
        results = [e for e in self.entries if query.lower() in e.title.lower() or query.lower() in e.content.lower()]
        return results[:top_k]
    def get_recent(self, n=3):
        return sorted(self.entries, key=lambda e: e.created_at, reverse=True)[:n]


class MockTask:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockTaskQueue:
    """Mock TaskQueue for testing."""
    def __init__(self):
        self.tasks = [
            MockTask(id="task_005", type="literature_search", title="搜索单细胞基础模型最新进展",
                      priority=8, status="pending", tags=["foundation_model", "literature"]),
            MockTask(id="task_018", type="literature_search", title="搜索转录组年龄预测的非线性方法",
                      priority=8, status="pending", tags=["age_prediction", "literature"]),
            MockTask(id="task_077", type="idea_generation", title="Re-test batch correction after bugfix",
                      priority=9, status="pending", tags=["age_prediction", "batch_correction"]),
        ]
    def stats(self):
        by_status = {}
        for t in self.tasks:
            by_status[t.status] = by_status.get(t.status, 0) + 1
        return {"total": len(self.tasks), "by_status": by_status}
    def add_task(self, task):
        self.tasks.append(task)
        return task.id
    def save(self):
        pass


def test_intent_classification():
    """Test that intent classification works correctly."""
    router = ConversationRouter(
        MockJournal(), MockKnowledge(), MockTaskQueue(), MockState()
    )
    
    test_cases = [
        # (query, expected_intent)
        ("最近在研究什么？", Intent.STATUS),
        ("你在干什么？", Intent.STATUS),
        ("what have you been doing?", Intent.STATUS),
        ("进展如何？", Intent.STATUS),
        ("汇报一下", Intent.STATUS),
        
        ("任务队列怎么样？", Intent.PROGRESS),
        ("还有什么要做？", Intent.PROGRESS),
        
        ("关于扩散模型你知道什么？", Intent.KNOWLEDGE),
        ("什么是 scGPT？", Intent.KNOWLEDGE),
        ("know about diffusion models", Intent.KNOWLEDGE),
        
        ("暂停年龄预测，集中做鲍曼不动杆菌", Intent.DIRECTION),
        ("切换到分子生成", Intent.DIRECTION),
        ("focus on AMP design", Intent.DIRECTION),
        
        ("详细说说批次校正", Intent.DETAIL),
        ("展开讲讲扩散模型", Intent.DETAIL),
        
        ("去研究因果推断", Intent.TASK_ADD),
        ("添加任务：研究 GFlowNet", Intent.TASK_ADD),
        
        ("帮助", Intent.HELP),
        ("help", Intent.HELP),
        
        ("今天天气不错", Intent.GENERAL),
    ]
    
    passed = 0
    failed = 0
    for query, expected in test_cases:
        parsed = router.parse_intent(query)
        status = "✅" if parsed.intent == expected else "❌"
        if parsed.intent == expected:
            passed += 1
        else:
            failed += 1
            print(f"  {status} '{query}'")
            print(f"       Expected: {expected.value}, Got: {parsed.intent.value} (conf={parsed.confidence})")
            if parsed.topic:
                print(f"       Topic: {parsed.topic}")
    
    print(f"\nIntent Classification: {passed}/{passed+failed} passed")
    return failed == 0


def test_topic_extraction():
    """Test that topics are correctly extracted from queries."""
    router = ConversationRouter(
        MockJournal(), MockKnowledge(), MockTaskQueue(), MockState()
    )
    
    test_cases = [
        ("关于扩散模型你知道什么？", "扩散模型"),
        ("详细说说批次校正", "批次校正"),
        ("暂停年龄预测，集中做鲍曼不动杆菌", "年龄预测"),
        ("去研究因果推断", "因果推断"),
        ("添加任务：研究 GFlowNet", "研究 GFlowNet"),
    ]
    
    passed = 0
    failed = 0
    for query, expected_topic in test_cases:
        parsed = router.parse_intent(query)
        if parsed.topic and expected_topic in parsed.topic:
            passed += 1
        else:
            failed += 1
            print(f"  ❌ '{query}'")
            print(f"       Expected topic containing: '{expected_topic}', Got: '{parsed.topic}'")
    
    print(f"\nTopic Extraction: {passed}/{passed+failed} passed")
    return failed == 0


def test_routing():
    """Test that routing produces meaningful responses."""
    router = ConversationRouter(
        MockJournal(), MockKnowledge(), MockTaskQueue(), MockState()
    )
    
    queries = [
        "最近在研究什么？",
        "任务队列怎么样？",
        "关于扩散模型你知道什么？",
        "详细说说批次校正",
        "帮助",
    ]
    
    print("\n--- Routing Output Samples ---")
    all_ok = True
    for q in queries:
        response = router.route(q)
        ok = len(response) > 20  # Should produce meaningful response
        if not ok:
            all_ok = False
            print(f"  ❌ '{q}' → response too short ({len(response)} chars)")
        else:
            # Show first 2 lines
            lines = response.split("\n")
            print(f"  ✅ '{q}' → {lines[0][:60]}...")
    
    return all_ok


def main():
    print("=" * 60)
    print("  ConversationRouter Tests")
    print("=" * 60)
    
    r1 = test_intent_classification()
    r2 = test_topic_extraction()
    r3 = test_routing()
    
    print("\n" + "=" * 60)
    if r1 and r2 and r3:
        print("  ✅ All tests passed!")
    else:
        print("  ❌ Some tests failed.")
    print("=" * 60)
    
    return 0 if (r1 and r2 and r3) else 1


if __name__ == "__main__":
    sys.exit(main())
