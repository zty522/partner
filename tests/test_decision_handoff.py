"""Tests for partner.mind.decision_handoff."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from partner.mind.decision_handoff import (
    dispatch_to_decision_loop,
    make_classification,
    make_decision,
    make_full_payload,
    make_preconditions_external,
    make_preconditions_self_evolve,
    recommend_next_action,
    summarise_run,
    validate_payload,
)
from partner.evolution.decision_loop import VALID_NEXT_ACTIONS
from partner.evolution.ledger import events_for_slot, ledger_path


class DecisionHandoffTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="decision_handoff_")
        self.workspace = Path(self._tmp) / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "share" / "mind" / "governance").mkdir(parents=True, exist_ok=True)
        (self.workspace / "partner").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestConstructors(DecisionHandoffTestBase):
    def test_make_classification_clamps_confidence(self) -> None:
        cls = make_classification("bug_in_partner_source", confidence=2.5, signals_count=3)
        self.assertEqual(cls["failure_class"], "bug_in_partner_source")
        self.assertEqual(cls["confidence"], 1.0)
        self.assertEqual(cls["signals_count"], 3)

    def test_make_decision_rejects_invalid_action(self) -> None:
        with self.assertRaises(ValueError):
            make_decision("totally_made_up_action")

    def test_make_decision_caps_rationale(self) -> None:
        d = make_decision("noop", "x" * 1000)
        self.assertEqual(len(d["rationale"]), 600)

    def test_make_preconditions_self_evolve_no_diff(self) -> None:
        p = make_preconditions_self_evolve(None)
        self.assertFalse(p["have_diff_hunk"])
        self.assertFalse(p["have_target_file"])

    def test_make_preconditions_self_evolve_with_diff(self) -> None:
        cand = {"diff_hunk": "diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new\n",
                "target_file": "partner/x.py"}
        p = make_preconditions_self_evolve(cand)
        self.assertTrue(p["have_diff_hunk"])
        self.assertTrue(p["have_target_file"])

    def test_make_preconditions_external_query(self) -> None:
        p = make_preconditions_external("hello world")
        self.assertTrue(p["have_external_query"])
        p2 = make_preconditions_external("")
        self.assertFalse(p2["have_external_query"])

    def test_validate_payload_happy(self) -> None:
        payload = make_full_payload(
            observation_summary="obs",
            classification=make_classification(),
            decision=make_decision("noop", "r"),
            preconditions={"have_diff_hunk": False, "have_target_file": False,
                            "have_external_query": False, "signals_count": 0},
        )
        ok, reason = validate_payload(payload)
        self.assertTrue(ok, reason)

    def test_validate_payload_rejects_unknown_action(self) -> None:
        payload = make_full_payload(
            observation_summary="obs",
            classification=make_classification(),
            decision={"next_action": "self_evolve_unbound", "rationale": "r",
                       "estimated_cost_seconds": 1, "requires_external": False,
                       "requires_apply": False},
            preconditions={"have_diff_hunk": True, "have_target_file": True,
                            "have_external_query": False, "signals_count": 0},
        )
        ok, _ = validate_payload(payload)
        self.assertFalse(ok)


class TestRecommender(DecisionHandoffTestBase):
    def test_failure_class_routes_to_action(self) -> None:
        # Provide a candidate so the heuristic doesn't downgrade
        cand = {"diff_hunk": "x", "target_file": "partner/x.py"}
        d = recommend_next_action(failure_class="bug_in_partner_source", candidate=cand)
        self.assertEqual(d["next_action"], "self_evolve")

    def test_regression_routes_self_evolve(self) -> None:
        cand = {"diff_hunk": "x", "target_file": "partner/x.py"}
        d = recommend_next_action(failure_class="regression_in_recent_apply", candidate=cand)
        self.assertEqual(d["next_action"], "self_evolve")

    def test_missing_capability_routes_external(self) -> None:
        d = recommend_next_action(failure_class="missing_capability")
        self.assertEqual(d["next_action"], "external_retrieval")

    def test_knowledge_gap_routes_active_learning(self) -> None:
        d = recommend_next_action(failure_class="knowledge_gap")
        self.assertEqual(d["next_action"], "active_learning")

    def test_self_evolve_without_candidate_falls_back_to_noop(self) -> None:
        # failure_class routes to self_evolve but the function's own gate downgrades
        # to noop when no candidate diff is present; loop then refuses.
        d = recommend_next_action(failure_class="bug_in_partner_source")
        self.assertEqual(d["next_action"], "noop")

    def test_candidate_with_diff_routes_self_evolve(self) -> None:
        cand = {"diff_hunk": "x", "target_file": "partner/x.py"}
        d = recommend_next_action(candidate=cand)
        self.assertEqual(d["next_action"], "self_evolve")

    def test_external_query_routes_external_retrieval(self) -> None:
        d = recommend_next_action(external_query="find best practice")
        self.assertEqual(d["next_action"], "external_retrieval")

    def test_signals_route_active_learning(self) -> None:
        d = recommend_next_action(
            task_state={"unresolved_questions": ["what is hermes six-step?"]})
        self.assertEqual(d["next_action"], "active_learning")

    def test_no_signals_routes_noop(self) -> None:
        d = recommend_next_action(task_state={"unrelated": "x"})
        self.assertEqual(d["next_action"], "noop")

    def test_blocked_phase_stays_noop(self) -> None:
        d = recommend_next_action(task_state={"unresolved_questions": ["x", "y"]},
                                  instance_phase="BLOCKED")
        self.assertEqual(d["next_action"], "noop")


class TestDispatcher(DecisionHandoffTestBase):
    def test_dispatch_noop_completes_and_writes_ledger(self) -> None:
        result = dispatch_to_decision_loop(
            self.workspace, instance_id="03", budget_seconds=5,
            task_state={"phase": "PROJECT"},
        )
        self.assertTrue(result.completed)
        self.assertEqual(result.next_node, "IDLE")
        events = events_for_slot(self.workspace, result.subject_id)
        types = [e["event_type"] for e in events]
        for required in ("observe/context_recorded", "diagnose/classification_recorded",
                          "decide/decision_recorded", "noop/reason_recorded",
                          "loop/completion_recorded"):
            self.assertIn(required, types)

    def test_dispatch_with_explicit_action_runs_branch(self) -> None:
        # external_retrieval branch fully observable from ledger
        result = dispatch_to_decision_loop(
            self.workspace, instance_id="05", budget_seconds=10,
            external_query="partner slot scheduler",
            decision=make_decision("external_retrieval", "test", requires_external=True),
        )
        events = events_for_slot(self.workspace, result.subject_id)
        types = [e["event_type"] for e in events]
        self.assertIn("retrieve/query_normalized_recorded", types)
        self.assertIn("evidence/intake_recorded", types)
        self.assertTrue(result.completed)

    def test_dispatch_invalid_payload_falls_back_to_noop(self) -> None:
        # Pass a malformed decision dict that should trip validate_payload
        result = dispatch_to_decision_loop(
            self.workspace, instance_id="01", budget_seconds=5,
            decision={"next_action": "completely_unknown", "rationale": ""},
        )
        # Either dead-lettered or refused → still produces a decision event
        events = events_for_slot(self.workspace, result.subject_id)
        types = [e["event_type"] for e in events]
        self.assertIn("decide/decision_recorded", types)
        # And completes
        self.assertTrue(result.completed)

    def test_dispatch_panic_returns_safe_loopresult(self) -> None:
        # Simulate panicking path by feeding None task_state and none fields
        # The dispatcher must NOT raise.
        result = dispatch_to_decision_loop(
            self.workspace, instance_id="02", budget_seconds=3,
            task_state=None,
            decision=None, classification=None, preconditions=None,
            candidate=None, external_query=None,
        )
        self.assertIsNotNone(result)


class TestSummariseRun(DecisionHandoffTestBase):
    def test_summarise_completed_no_branch(self) -> None:
        result = dispatch_to_decision_loop(
            self.workspace, instance_id="03", budget_seconds=5,
            task_state={"phase": "PROJECT"})
        s = summarise_run(result)
        self.assertIn("completed", s)
        self.assertIn("events=", s)

    def test_summarise_external_branch_marks_branch(self) -> None:
        result = dispatch_to_decision_loop(
            self.workspace, instance_id="04", budget_seconds=5,
            external_query="test", decision=make_decision("external_retrieval", "r", requires_external=True))
        s = summarise_run(result)
        self.assertIn("completed", s)
        self.assertIn("events=", s)
        # branch was entered and substep events written (evidence may be 0 since stub doesn't fetch)
        events = events_for_slot(self.workspace, result.subject_id)
        types = [e["event_type"] for e in events]
        self.assertIn("retrieve/query_normalized_recorded", types)
        self.assertIn("evidence/intake_recorded", types)


if __name__ == "__main__":
    unittest.main()
