"""Bug #58 P0.3 (ADR 0066) — auto_resume regression.

Verify that ``auto_resume_waiting_project`` un-strands instances whose
project_loop keeps returning ``no_proposed_action`` for two distinct
reasons:

  1. latest_receipt.stop_reason contains "waiting for the next user
     instruction" — append iteration+1 receipt with previous_receipt ref
  2. project has NO receipt at all (e.g. PROJECTS dict project that
     scaffold created but never iterated) — write iteration=1 receipt

Both must produce a receipt whose ``next_actions[0].status="proposed"``
so the project loop returns a real proposed action on the next tick.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


def _load_auto_resume():
    """Load auto_resume via partner package so dataclass __module__
    resolves correctly (see test_storage_contract for the same dance)."""
    import importlib
    return importlib.import_module("partner.governance.auto_resume")


class TestAutoResumeWaitingProject(unittest.TestCase):
    """Append iteration+1 when latest_receipt.stop_reason says wait."""

    def setUp(self):
        self.mod = _load_auto_resume()
        self.tmp = Path(tempfile.mkdtemp(prefix="auto_resume_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_project_with_waiting_receipt(self, project_id: str = "p_wait"):
        from partner.governance.storage import save_receipt, save_project_state
        from partner.governance.models import IterationReceipt, NextAction
        proj = self.tmp / "share" / "projects" / project_id
        (proj / "governance" / "receipts").mkdir(parents=True)
        # iteration=3, stop_reason says waiting
        receipt = IterationReceipt(
            receipt_id="r_old",
            project_id=project_id,
            iteration=3,
            goal="g",
            inputs=[],
            actions_executed=["exec:x"],
            artifacts=[],
            findings=[],
            unresolved_questions=[],
            next_actions=[],
            stop_reason="bounded manual task completed; waiting for the next user instruction",
            delivery_confirmed=True,
        )
        save_receipt(str(self.tmp), receipt)
        save_project_state(str(self.tmp), type(receipt.project_state) if False else None) \
            if False else None  # skip — we'll save manually below
        # Write project_state manually
        (proj / "governance" / "project_state.json").write_text(json.dumps({
            "project_id": project_id, "status": "active",
            "current_iteration": 3, "latest_receipt_id": "r_old",
            "allow_continue": True,
        }), encoding="utf-8")

    def test_waiting_receipt_appends_new_iteration(self):
        self._setup_project_with_waiting_receipt()
        result = self.mod.auto_resume_waiting_project(
            str(self.tmp), "p_wait", "03")
        self.assertTrue(result["resumed"])
        self.assertEqual(result["iteration"], 4)
        self.assertEqual(result["previous_receipt_id"], "r_old")
        # New receipt on disk
        proj = self.tmp / "share" / "projects" / "p_wait"
        new_receipt_files = sorted(
            (proj / "governance" / "receipts").glob("*.json"))
        self.assertEqual(len(new_receipt_files), 2)
        # New receipt's first next_action must be "proposed"
        latest = json.loads(new_receipt_files[-1].read_text(encoding="utf-8"))
        self.assertEqual(len(latest["next_actions"]), 1)
        self.assertEqual(latest["next_actions"][0]["status"], "proposed")
        # previous_receipt_id was threaded into params for the action
        self.assertEqual(
            latest["next_actions"][0]["params"]["previous_receipt_id"],
            "r_old")

    def test_idempotent_second_call_no_double_append(self):
        self._setup_project_with_waiting_receipt()
        first = self.mod.auto_resume_waiting_project(
            str(self.tmp), "p_wait", "03")
        self.assertTrue(first["resumed"])
        # Second call sees the new (non-waiting) receipt as latest,
        # so it returns not_waiting without writing again.
        second = self.mod.auto_resume_waiting_project(
            str(self.tmp), "p_wait", "03")
        self.assertFalse(second["resumed"])
        self.assertEqual(second["reason"], "not_waiting")
        proj = self.tmp / "share" / "projects" / "p_wait"
        receipt_files = sorted(
            (proj / "governance" / "receipts").glob("*.json"))
        self.assertEqual(len(receipt_files), 2)

    def test_non_waiting_receipt_left_alone(self):
        """A receipt with stop_reason that doesn't say "waiting for
        user" must NOT trigger auto_resume — the project is in
        mid-flight and we should not interrupt it."""
        from partner.governance.storage import save_receipt
        from partner.governance.models import IterationReceipt
        proj = self.tmp / "share" / "projects" / "p_active"
        (proj / "governance" / "receipts").mkdir(parents=True)
        save_receipt(str(self.tmp), IterationReceipt(
            receipt_id="r_active",
            project_id="p_active",
            iteration=2,
            goal="g",
            inputs=[],
            actions_executed=["exec:x"],
            artifacts=[],
            findings=[],
            unresolved_questions=[],
            next_actions=[],
            stop_reason="completed iteration; receipts already pending",
            delivery_confirmed=True,
        ))
        result = self.mod.auto_resume_waiting_project(
            str(self.tmp), "p_active", "03")
        self.assertFalse(result["resumed"])
        self.assertEqual(result["reason"], "not_waiting")
        self.assertEqual(result["current_stop_reason"],
                         "completed iteration; receipts already pending")


class TestAutoResumeInitialReceipt(unittest.TestCase):
    """Append iteration=1 when project has NO receipt at all."""

    def setUp(self):
        self.mod = _load_auto_resume()
        self.tmp = Path(tempfile.mkdtemp(prefix="auto_resume_init_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_empty_project(self, project_id: str = "p_empty"):
        """Project dir exists but no receipt."""
        proj = self.tmp / "share" / "projects" / project_id
        (proj / "governance" / "receipts").mkdir(parents=True)
        # No receipt. But project_state must exist (scaffold would
        # have created it).
        (proj / "governance" / "project_state.json").write_text(json.dumps({
            "project_id": project_id, "status": "active",
            "current_iteration": 0, "latest_receipt_id": "",
            "allow_continue": True,
        }), encoding="utf-8")

    def test_no_receipt_writes_initial_iteration(self):
        self._setup_empty_project()
        result = self.mod.auto_resume_waiting_project(
            str(self.tmp), "p_empty", "05")
        self.assertTrue(result["resumed"])
        self.assertEqual(result["iteration"], 1)
        self.assertTrue(result.get("initial"))
        proj = self.tmp / "share" / "projects" / "p_empty"
        receipt_files = sorted(
            (proj / "governance" / "receipts").glob("*.json"))
        self.assertEqual(len(receipt_files), 1)
        latest = json.loads(receipt_files[0].read_text(encoding="utf-8"))
        self.assertEqual(latest["iteration"], 1)
        # ``goal`` defaults to a generic "承接上一轮..." string when
        # none is supplied; the action's user_request surfaces the
        # project_id explicitly so the instance knows what to work on.
        self.assertIn("承接上一轮", latest["goal"])
        self.assertEqual(len(latest["next_actions"]), 1)
        self.assertEqual(latest["next_actions"][0]["status"], "proposed")
        # Bug #61 (ADR 0067): the user_request must embed an
        # iteration_tag so batch_planner's content-based dedup hashes
        # a different value across resumes; otherwise every resume
        # of the same project emits identical text and the instance
        # skips the plan forever.
        user_request = latest["next_actions"][0]["params"]["user_request"]
        self.assertIn(
            "[iteration_tag=", user_request,
            "user_request must embed an iteration_tag to break "
            "content-based dedup collisions")
        self.assertIn(
            "resume_05_0001", user_request,
            "iteration_tag must include the receipt_id suffix")


if __name__ == "__main__":
    unittest.main()
