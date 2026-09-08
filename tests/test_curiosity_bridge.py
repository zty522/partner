"""Tests for partner.evolution.curiosity_bridge (Sprint18 self-evolution budget)."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from partner.evolution.curiosity_bridge import (
    DEFAULT_CURIOSITY_BRIDGE_CONFIG,
    propose,
    run_evolution_budget,
)


class CuriosityBridgeTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="curiosity_test_")
        self.workspace_root = Path(self._tmp) / "ws"
        self.workspace_root.mkdir()
        (self.workspace_root / "share" / "mind" / "governance").mkdir(parents=True)
        # pre-existing events file with one event so prev_hash can be read
        events = self.workspace_root / "share" / "mind" / "governance" / "evolution_events.jsonl"
        seed = {
            "schema_version": 1, "event_type": "existing/seed",
            "occurred_at": "2026-09-01T00:00:00+08:00", "actor": "test",
            "subject_id": "seed", "project_id": "agent_self_evolution",
            "payload": {}, "evidence_refs": [], "prev_hash": "",
            "event_hash": "0" * 64,
        }
        events.write_text(json.dumps(seed) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestSignalExtraction(CuriosityBridgeTestBase):
    def test_propose_no_state_skipped(self) -> None:
        result = propose(self.workspace_root, "03", None)
        self.assertTrue(result["skipped"])
        self.assertEqual(result["proposed"], [])

    def test_propose_no_signals_skipped(self) -> None:
        result = propose(self.workspace_root, "03", {"unrelated_field": "x"})
        self.assertTrue(result["skipped"])

    def test_propose_signals_generates_topics(self) -> None:
        state = {
            "unresolved_questions": [
                "Can we apply BDK closure across partner/governance/scheduler.py without breaking the slot invariant?",
                "Should curiosity_bridge use a stable hash for idempotency?",
            ]
        }
        result = propose(self.workspace_root, "03", state, budget_seconds=10)
        self.assertFalse(result["skipped"])
        self.assertGreaterEqual(len(result["proposed"]), 1)
        # events file should now have at least 2 new events appended
        events_path = self.workspace_root / "share" / "mind" / "governance" / "evolution_events.jsonl"
        lines = [json.loads(l) for l in events_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        curiosity_events = [l for l in lines if l.get("event_type") == "curiosity/topic_proposed"]
        self.assertGreaterEqual(len(curiosity_events), 1)
        # notes directory created
        notes_dir = self.workspace_root / "share" / "mind" / "governance" / "research_learning" / "curiosity"
        self.assertTrue(notes_dir.is_dir())
        self.assertGreaterEqual(len(list(notes_dir.iterdir())), 1)

    def test_idempotent_topic_id(self) -> None:
        from partner.evolution.curiosity_bridge import _topic_id
        a = _topic_id("03", "same hint text")
        b = _topic_id("03", "same hint text")
        # bucket is hour-truncated; within the same hour same hint should collide
        self.assertEqual(a, b)


class TestBudget(CuriosityBridgeTestBase):
    def test_run_evolution_budget_with_apply_dry_run(self) -> None:
        result = run_evolution_budget(self.workspace_root, "04",
                                      task_state={"unresolved_questions": [
                                          "Is apply_pipeline coverage sufficient across the 13 promotion path candidates?"
                                      ]},
                                      budget_seconds=15)
        self.assertEqual(result["instance_id"], "04")
        self.assertIn("apply_dry_run", result)
        self.assertIsInstance(result["apply_dry_run"], dict)
        self.assertIn("examined", result["apply_dry_run"])

    def test_run_evolution_budget_no_signals(self) -> None:
        result = run_evolution_budget(self.workspace_root, "05",
                                      task_state=None, budget_seconds=10)
        self.assertEqual(result["instance_id"], "05")
        # still records budget_run_completed event
        events_path = self.workspace_root / "share" / "mind" / "governance" / "evolution_events.jsonl"
        lines = [json.loads(l) for l in events_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        budget_events = [l for l in lines if l.get("event_type") == "curiosity/budget_run_completed"]
        self.assertEqual(len(budget_events), 1)

    def test_default_config_used(self) -> None:
        self.assertIn("budget_seconds", DEFAULT_CURIOSITY_BRIDGE_CONFIG)
        self.assertEqual(DEFAULT_CURIOSITY_BRIDGE_CONFIG["budget_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
