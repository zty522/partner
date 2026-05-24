"""Tests for StrategyMap — DAG-based task orchestration."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from partner.strategy_map import (
    MilestoneNode, StrategyEdge, StrategyMap,
)


def test_basic_crud():
    """Test basic node and edge operations."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
        json.dump({"nodes": {}, "edges": [], "meta": {}}, f)

    try:
        sm = StrategyMap(path)
        assert len(sm.nodes) == 0

        # Add nodes
        n1 = MilestoneNode(title="Study APEX paper", priority=10, tags=["apex"])
        n2 = MilestoneNode(title="Implement StrategyMap", priority=8, tags=["impl"])
        n3 = MilestoneNode(title="Write tests", priority=6, tags=["test"])

        sm.add_node(n1)
        sm.add_node(n2)
        sm.add_node(n3)
        assert len(sm.nodes) == 3

        # Add edges
        sm.add_edge(n1.id, n2.id, "prerequisite", 1.0)
        sm.add_edge(n2.id, n3.id, "prerequisite", 0.8)
        assert len(sm.edges) == 2

        # Cycle detection
        try:
            sm.add_edge(n3.id, n1.id, "prerequisite")
            assert False, "Should have raised ValueError for cycle"
        except ValueError:
            pass

        # Remove node
        sm.remove_node(n3.id)
        assert len(sm.nodes) == 2
        assert len(sm.edges) == 1

        print("✅ test_basic_crud passed")
    finally:
        os.unlink(path)


def test_ready_nodes():
    """Test get_ready_nodes with prerequisite dependencies."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
        json.dump({"nodes": {}, "edges": [], "meta": {}}, f)

    try:
        sm = StrategyMap(path)

        root = MilestoneNode(title="Root", priority=10)
        child1 = MilestoneNode(title="Child 1", priority=8)
        child2 = MilestoneNode(title="Child 2", priority=7)
        grandchild = MilestoneNode(title="Grandchild", priority=5)

        sm.add_node(root)
        sm.add_node(child1)
        sm.add_node(child2)
        sm.add_node(grandchild)

        sm.add_edge(root.id, child1.id, "prerequisite")
        sm.add_edge(root.id, child2.id, "prerequisite")
        sm.add_edge(child1.id, grandchild.id, "prerequisite")

        # Initially: only root is ready (no prereqs)
        ready = sm.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].id == root.id

        # Achieve root
        sm.achieve(root.id, "Completed root")

        # Now child1 and child2 are ready
        ready = sm.get_ready_nodes()
        ready_ids = {n.id for n in ready}
        assert child1.id in ready_ids
        assert child2.id in ready_ids
        assert grandchild.id not in ready_ids  # child1 not achieved yet

        # Achieve child1
        sm.achieve(child1.id, "Done child1")

        # Grandchild now ready
        ready = sm.get_ready_nodes()
        ready_ids = {n.id for n in ready}
        assert grandchild.id in ready_ids

        print("✅ test_ready_nodes passed")
    finally:
        os.unlink(path)


def test_fork_discovery():
    """Test fork discovery from achieved nodes."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
        json.dump({"nodes": {}, "edges": [], "meta": {}}, f)

    try:
        sm = StrategyMap(path)

        node = MilestoneNode(
            title="Study scGPT",
            priority=10,
            tags=["single-cell", "foundation-model"],
            evidence="scGPT uses gene expression data with transformer architecture",
        )
        sm.add_node(node)
        sm.achieve(node.id, node.evidence)

        # Discover forks with knowledge tags
        forks = sm.discover_forks(
            node.id,
            knowledge_tags=["batch-correction", "gene-regulatory-network"],
        )

        assert len(forks) > 0
        assert len(forks) <= 5

        # Check fork edges exist
        for fork in forks:
            edge = next((e for e in sm.edges
                        if e.from_id == node.id and e.to_id == fork.id), None)
            assert edge is not None
            assert edge.edge_type == "enables"

        # Second call should not generate more forks
        forks2 = sm.discover_forks(node.id, knowledge_tags=["more-tag"])
        assert len(forks2) == 0

        print("✅ test_fork_discovery passed")
    finally:
        os.unlink(path)


def test_policy_selection():
    """Test policy selection scoring."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
        json.dump({"nodes": {}, "edges": [], "meta": {}}, f)

    try:
        sm = StrategyMap(path)

        # High priority node
        n1 = MilestoneNode(title="High priority", priority=12, tags=["important"])
        # Low priority with many successors
        n2 = MilestoneNode(title="Hub node", priority=5, tags=["hub"])
        # Fork node
        n3 = MilestoneNode(title="New fork", priority=9, tags=["fork", "new"])

        sm.add_node(n1)
        sm.add_node(n2)
        sm.add_node(n3)

        # Add successors to n2
        for i in range(3):
            child = MilestoneNode(title=f"Child {i}", priority=3)
            sm.add_node(child)
            sm.add_edge(n2.id, child.id, "enables")

        # Select next — n1 should win due to high priority
        next_node = sm.select_next()
        assert next_node is not None

        print("✅ test_policy_selection passed")
    finally:
        os.unlink(path)


def test_import_from_tasks():
    """Test importing from task_queue."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
        json.dump({"nodes": {}, "edges": [], "meta": {}}, f)

    try:
        sm = StrategyMap(path)

        tasks = [
            {"id": "t1", "status": "pending", "priority": 10, "tags": ["apex", "design"],
             "title": "Design strategy map"},
            {"id": "t2", "status": "pending", "priority": 8, "tags": ["apex", "impl"],
             "title": "Implement DAG"},
            {"id": "t3", "status": "pending", "priority": 7, "tags": ["llm", "research"],
             "title": "Study LLM agents"},
            {"id": "t4", "status": "pending", "priority": 6, "tags": ["llm", "research"],
             "title": "Survey LLM frameworks"},
            {"id": "t5", "status": "completed", "priority": 5, "tags": ["old"],
             "title": "Old task"},
            {"id": "t6", "status": "pending", "priority": 3, "tags": [],
             "title": "Misc task"},
        ]

        created = sm.import_from_tasks(tasks)
        assert created >= 3  # apex group, llm group, misc

        # Check that completed task was skipped
        for node in sm.nodes.values():
            assert "t5" not in node.task_ids

        print("✅ test_import_from_tasks passed")
    finally:
        os.unlink(path)


def test_visualization():
    """Test Mermaid and ASCII output."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
        json.dump({"nodes": {}, "edges": [], "meta": {}}, f)

    try:
        sm = StrategyMap(path)

        n1 = MilestoneNode(title="Root", priority=10, status="achieved")
        n2 = MilestoneNode(title="Branch A", priority=8, status="active")
        n3 = MilestoneNode(title="Branch B", priority=7)
        sm.add_node(n1)
        sm.add_node(n2)
        sm.add_node(n3)
        sm.add_edge(n1.id, n2.id)
        sm.add_edge(n1.id, n3.id)

        mermaid = sm.to_mermaid()
        assert "graph TD" in mermaid
        assert n1.id in mermaid

        ascii_tree = sm.to_ascii()
        assert "Root" in ascii_tree
        assert "✅" in ascii_tree  # achieved icon

        summary = sm.summary()
        assert summary["total_nodes"] == 3
        assert summary["achieved_count"] == 1

        print("✅ test_visualization passed")
    finally:
        os.unlink(path)


def test_depth_computation():
    """Test depth computation in DAG."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
        json.dump({"nodes": {}, "edges": [], "meta": {}}, f)

    try:
        sm = StrategyMap(path)

        n1 = MilestoneNode(title="L0", priority=10)
        n2 = MilestoneNode(title="L1a", priority=8)
        n3 = MilestoneNode(title="L1b", priority=7)
        n4 = MilestoneNode(title="L2", priority=5)

        sm.add_node(n1)
        sm.add_node(n2)
        sm.add_node(n3)
        sm.add_node(n4)

        sm.add_edge(n1.id, n2.id)
        sm.add_edge(n1.id, n3.id)
        sm.add_edge(n2.id, n4.id)

        assert sm.compute_depth(n1.id) == 0
        assert sm.compute_depth(n2.id) == 1
        assert sm.compute_depth(n3.id) == 1
        assert sm.compute_depth(n4.id) == 2

        print("✅ test_depth_computation passed")
    finally:
        os.unlink(path)


def test_persistence():
    """Test save/load roundtrip."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
        json.dump({"nodes": {}, "edges": [], "meta": {}}, f)

    try:
        sm = StrategyMap(path)
        n1 = MilestoneNode(title="Persistent node", priority=8, tags=["test"])
        n2 = MilestoneNode(title="Another node", priority=6)
        sm.add_node(n1)
        sm.add_node(n2)
        sm.add_edge(n1.id, n2.id, "enables")

        # Reload
        sm2 = StrategyMap(path)
        assert len(sm2.nodes) == 2
        assert len(sm2.edges) == 1
        assert sm2.nodes[n1.id].title == "Persistent node"
        assert sm2.edges[0].edge_type == "enables"

        print("✅ test_persistence passed")
    finally:
        os.unlink(path)


if __name__ == "__main__":
    test_basic_crud()
    test_ready_nodes()
    test_fork_discovery()
    test_policy_selection()
    test_import_from_tasks()
    test_visualization()
    test_depth_computation()
    test_persistence()
    print("\n🎉 All StrategyMap tests passed!")
