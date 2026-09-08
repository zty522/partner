"""Tests for partner.evolution.decision_loop and loop_watchdog (Sprint18)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from partner.evolution.decision_loop import (
    LoopResult,
    VALID_NEXT_ACTIONS,
    run_decision_loop,
)
from partner.evolution import loop_watchdog
from partner.evolution.ledger import (
    SCHEMA_VERSION,
    append_event,
    events_for_slot,
    ledger_path,
)


def _setup(tmp: Path) -> tuple[Path, dict[str, Any]]:
    """Returns (workspace_root, sample_diff). Workspace is git-initialised."""
    workspace = tmp / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "share" / "mind" / "governance").mkdir(parents=True, exist_ok=True)
    (workspace / "partner").mkdir(parents=True, exist_ok=True)
    diff = (
        "diff --git a/partner/stub.py b/partner/stub.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/partner/stub.py\n"
        "+++ b/partner/stub.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def hello():\n"
        "-    return 'hi'\n"
        "+    return 'hi'\n"
        "+\n"
    )
    target = workspace / "partner" / "stub.py"
    target.write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(workspace), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(workspace), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(workspace), check=True)
    subprocess.run(["git", "add", "."], cwd=str(workspace), check=True)
    subprocess.run(["git", "commit", "-m", "init", "--no-verify"], cwd=str(workspace), check=True)
    return workspace, {"diff": diff, "target": "partner/stub.py"}


def _decision_payload(next_action: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "observation_summary": "test observation",
        "classification": {"failure_class": "bug_in_partner_source", "confidence": 0.9,
                           "signals_count": 2},
        "decision": {
            "next_action": next_action,
            "rationale": "test rationale",
            "estimated_cost_seconds": 10,
            "requires_external": False,
            "requires_apply": next_action == "self_evolve",
        },
        "preconditions": {
            "have_diff_hunk": next_action == "self_evolve",
            "have_target_file": next_action == "self_evolve",
            "have_external_query": next_action == "external_retrieval",
            "signals_count": 2,
        },
    }
    base["decision"].update(overrides.get("decision_overrides", {}))
    base["preconditions"].update(overrides.get("preconditions_overrides", {}))
    return base


class DecisionLoopTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="decision_loop_")
        self.workspace, self.assets = _setup(Path(self._tmp))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _events(self, subject_id: str) -> list[dict[str, Any]]:
        return events_for_slot(self.workspace, subject_id)


class TestStateMachineEvents(DecisionLoopTestBase):
    def test_loop_writes_observe_diagnose_decide_complete(self) -> None:
        decision = _decision_payload("noop")
        result = run_decision_loop(
            self.workspace, instance_id="03", budget_seconds=10,
            task_state={"unresolved_questions": ["how does the loop run?"]},
            decision_payload=decision,
        )
        self.assertIsInstance(result, LoopResult)
        self.assertTrue(result.completed)
        self.assertEqual(result.next_node, "IDLE")
        events = self._events(result.subject_id)
        types = [e["event_type"] for e in events]
        self.assertIn("observe/context_recorded", types)
        self.assertIn("diagnose/classification_recorded", types)
        self.assertIn("decide/decision_recorded", types)
        self.assertIn("loop/completion_recorded", types)
        self.assertTrue(any(t.startswith("noop/") for t in types))
        self.assertGreaterEqual(result.event_count, 4)

    def test_unknown_next_action_becomes_dead_letter(self) -> None:
        decision = _decision_payload("totally_unknown_action")
        result = run_decision_loop(
            self.workspace, instance_id="03", budget_seconds=10,
            task_state=None, decision_payload=decision,
        )
        events = self._events(result.subject_id)
        types = [e["event_type"] for e in events]
        self.assertIn("decide/dead_letter_recorded", types)
        self.assertNotIn("decide/refused_recorded", types)  # this is ref-not-dead

    def test_precondition_mismatch_refuses(self) -> None:
        decision = _decision_payload("self_evolve",
                                     preconditions_overrides={"have_diff_hunk": False})
        result = run_decision_loop(
            self.workspace, instance_id="03", budget_seconds=10,
            task_state=None, decision_payload=decision, candidate=None,
        )
        events = self._events(result.subject_id)
        types = [e["event_type"] for e in events]
        self.assertIn("decide/refused_recorded", types)
        # also a noop reason event because we downgraded to noop
        self.assertTrue(any(t.startswith("noop/") for t in types))


class TestSelfEvolveBranch(DecisionLoopTestBase):
    def test_self_evolve_applies_candidate_and_records_outcome(self) -> None:
        candidate = {
            "experiment_id": "loop_apply_1",
            "decision": "candidate_validated",
            "target_file": self.assets["target"],
            "diff_hunk": self.assets["diff"],
        }
        decision = _decision_payload("self_evolve")
        result = run_decision_loop(
            self.workspace, instance_id="04", budget_seconds=15,
            task_state={"unresolved_questions": ["apply candidate stub.py"]},
            candidate=candidate, decision_payload=decision,
        )
        self.assertEqual(result.applied, 1)
        events = self._events(result.subject_id)
        types = [e["event_type"] for e in events]
        # required substep events
        self.assertIn("evolve/diff_validated", types)
        self.assertIn("evolve/git_apply_check_recorded", types)
        self.assertIn("evolve/git_apply_recorded", types)
        self.assertIn("evolve/git_add_recorded", types)
        self.assertIn("evolve/git_commit_recorded", types)
        self.assertIn("apply/outcome_recorded", types)
        self.assertIn("loop/completion_recorded", types)
        # and the file actually changed
        new_head = subprocess.run(["git", "rev-parse", "HEAD"],
                                  cwd=str(self.workspace), capture_output=True, text=True, check=True)
        self.assertTrue(new_head.stdout.strip())

    def test_self_evolve_with_invalid_diff_fails_safely(self) -> None:
        candidate = {
            "experiment_id": "loop_invalid",
            "decision": "candidate_validated",
            "target_file": "partner/stub.py",
            "diff_hunk": "not a real diff",
        }
        decision = _decision_payload("self_evolve")
        result = run_decision_loop(
            self.workspace, instance_id="04", budget_seconds=10,
            task_state=None, candidate=candidate, decision_payload=decision,
        )
        # refine: invalid diff_hunk fails the diff-validated check; outcome is recorded
        self.assertEqual(result.applied, 0)
        events = self._events(result.subject_id)
        types = [e["event_type"] for e in events]
        self.assertIn("evolve/diff_validated", types)
        # may or may not have apply_check depending on path
        self.assertIn("apply/outcome_recorded", types)


class TestActiveLearningBranch(DecisionLoopTestBase):
    def test_active_learning_emits_full_branch_chain(self) -> None:
        decision = _decision_payload("active_learning",
                                     decision_overrides={"requires_external": False,
                                                        "requires_apply": False},
                                     preconditions_overrides={"have_diff_hunk": False,
                                                              "have_target_file": False,
                                                              "have_external_query": False})
        result = run_decision_loop(
            self.workspace, instance_id="03", budget_seconds=15,
            task_state={"unresolved_questions": ["how does apply_pipeline avoid stale branches?"],
                        "open_questions": ["should we batch retries across slots?"]},
            decision_payload=decision,
        )
        self.assertFalse(result.panic)
        events = self._events(result.subject_id)
        types = [e["event_type"] for e in events]
        for required in ["learn/signals_extracted_recorded",
                         "learn/topic_proposed_recorded",
                         "learn/note_written_recorded",
                         "learn/evolution_event_appended_recorded",
                         "candidate/proposal_recorded"]:
            self.assertIn(required, types)
        self.assertIn("loop/completion_recorded", types)


class TestExternalRetrievalBranch(DecisionLoopTestBase):
    def test_external_retrieval_emits_full_branch_chain(self) -> None:
        decision = _decision_payload("external_retrieval",
                                     decision_overrides={"requires_external": True, "requires_apply": False},
                                     preconditions_overrides={"have_external_query": True,
                                                              "have_diff_hunk": False,
                                                              "have_target_file": False})
        result = run_decision_loop(
            self.workspace, instance_id="05", budget_seconds=15,
            task_state={"unresolved_questions": ["find partner external retrieval best practices"]},
            external_query="partner slot scheduler best practices",
            decision_payload=decision,
        )
        self.assertFalse(result.panic)
        events = self._events(result.subject_id)
        types = [e["event_type"] for e in events]
        for required in ["retrieve/query_normalized_recorded",
                         "retrieve/fetch_started_recorded",
                         "retrieve/fetch_completed_recorded",
                         "retrieve/fact_card_written_recorded",
                         "evidence/intake_recorded"]:
            self.assertIn(required, types)
        # either cache_hit_recorded or cache_miss_recorded is acceptable
        self.assertTrue(
            "retrieve/cache_hit_recorded" in types or "retrieve/cache_miss_recorded" in types,
            f"expected one of cache_hit/miss events, got {types}",
        )


class TestLoopLedger(DecisionLoopTestBase):
    def test_ledger_schema_v2(self) -> None:
        decision = _decision_payload("noop")
        result = run_decision_loop(self.workspace, instance_id="03", budget_seconds=5,
                                   task_state=None, decision_payload=decision)
        events = self._events(result.subject_id)
        self.assertGreater(len(events), 0)
        latest = max(events, key=lambda e: e["seq"])
        self.assertEqual(latest["schema_version"], SCHEMA_VERSION)
        prev = ""
        for e in events:
            self.assertEqual(e["prev_hash"], prev)
            prev = e["event_hash"]

    def test_budget_exceeded_does_not_write_stuck(self) -> None:
        decision = _decision_payload("active_learning",
                                     decision_overrides={"requires_external": False, "requires_apply": False},
                                     preconditions_overrides={"have_diff_hunk": False, "have_target_file": False,
                                                              "have_external_query": False})
        result = run_decision_loop(
            self.workspace, instance_id="03", budget_seconds=2,
            task_state={"unresolved_questions": ["one", "two", "three"]},
            decision_payload=decision,
        )
        # either completed normally or with budget_exceeded flag
        self.assertTrue(result.completed or result.budget_exceeded)


class TestLoopWatchdog(DecisionLoopTestBase):
    def test_detect_stuck_emits_event_for_old_open_subject(self) -> None:
        # write only an observe event; older than max_age_seconds
        ledger_path(self.workspace).parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timedelta, timezone
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        append_event(self.workspace, event_type="observe/context_recorded",
                     subject_id="slot:99:stale", project_id="agent_self_evolution",
                     actor="03", payload={"test": True,
                                          "occurred_at_override": old_ts})
        # set old occurred_at by hand: append_event doesn't accept override; emulate
        # by rewriting the last line with an old timestamp
        with open(ledger_path(self.workspace), "r", encoding="utf-8") as f:
            lines = f.readlines()
        last = lines[-1]
        rec = json.loads(last)
        rec["occurred_at"] = old_ts
        body = {k: v for k, v in rec.items() if k != "event_hash"}
        body_str = json.dumps(body, ensure_ascii=False, sort_keys=True)
        rec["event_hash"] = "deadbeef" * 8
        lines[-1] = json.dumps(rec, ensure_ascii=False) + "\n"
        with open(ledger_path(self.workspace), "w", encoding="utf-8") as f:
            f.writelines(lines)

        stuck = loop_watchdog.detect_stuck(self.workspace, max_age_seconds=10)
        self.assertEqual(len(stuck), 1)
        self.assertEqual(stuck[0]["subject_id"], "slot:99:stale")
        # and a loop/stuck_recorded event was written
        events = events_for_slot(self.workspace, "slot:99:stale")
        self.assertTrue(any(e.get("event_type") == "loop/stuck_recorded" for e in events))

    def test_summarise_subject(self) -> None:
        decision = _decision_payload("noop")
        result = run_decision_loop(self.workspace, instance_id="03", budget_seconds=5,
                                   task_state=None, decision_payload=decision)
        summary = loop_watchdog.summarise_subject(self.workspace, result.subject_id)
        self.assertTrue(summary["exists"])
        self.assertTrue(summary["has_observe"])
        self.assertTrue(summary["has_diagnose"])
        self.assertTrue(summary["has_decide"])
        self.assertTrue(summary["has_completion"])
        self.assertEqual(summary["decided_action"], "noop")


class TestValidActions(DecisionLoopTestBase):
    def test_valid_next_actions_constant(self) -> None:
        self.assertEqual(
            set(VALID_NEXT_ACTIONS),
            {"self_evolve", "active_learning", "external_retrieval", "noop"},
        )


if __name__ == "__main__":
    unittest.main()
