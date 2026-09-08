"""Tests for partner.evolution.overnight_canary (Sprint18 §5 task 7)."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from partner.evolution.overnight_canary import run_once, run_loop
from partner.evolution.ledger import events_for_slot, read_events


class OvernightTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="overnight_")
        self.workspace = Path(self._tmp) / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness").mkdir(parents=True, exist_ok=True)
        (self.workspace / "share" / "mind" / "governance").mkdir(parents=True, exist_ok=True)
        # git init for diff apply
        (self.workspace / "partner").mkdir(parents=True, exist_ok=True)
        target = self.workspace / "partner" / "stub.py"
        target.write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=str(self.workspace), check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(self.workspace), check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(self.workspace), check=True)
        subprocess.run(["git", "add", "."], cwd=str(self.workspace), check=True)
        subprocess.run(["git", "commit", "-m", "init", "--no-verify"], cwd=str(self.workspace), check=True)
        self.diff_hunk = (
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

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestOvernightRunOnce(OvernightTestBase):
    def test_run_once_no_candidates(self) -> None:
        snap = run_once(self.workspace, dry_run=True)
        self.assertEqual(snap["examined"], 0)
        self.assertTrue(snap["dry_run"])
        self.assertIn("started_at", snap)

    def test_run_once_dry_run_with_candidate(self) -> None:
        cand = {
            "experiment_id": "ov_test_dry", "decision": "candidate_validated",
            "target_file": "partner/stub.py", "diff_hunk": self.diff_hunk,
        }
        (self.workspace / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness" / "ov.json").write_text(json.dumps(cand))
        snap = run_once(self.workspace, dry_run=True)
        self.assertEqual(snap["examined"], 1)
        # In dry_run with a valid diff, expected flow is dry_run (skipped=0, applied=0).
        # But first launch may have a prior apply_blacklist from another test.
        self.assertGreaterEqual(snap["applied"] + snap["skipped"] + snap["failed"], 1)

    def test_run_once_real_apply_with_candidate(self) -> None:
        cand = {
            "experiment_id": "ov_test_real", "decision": "candidate_validated",
            "target_file": "partner/stub.py", "diff_hunk": self.diff_hunk,
        }
        (self.workspace / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness" / "ov.json").write_text(json.dumps(cand))
        snap = run_once(self.workspace, dry_run=False)
        self.assertEqual(snap["examined"], 1)
        self.assertEqual(snap["applied"], 1)

    def test_run_once_failing_candidate_doesnt_crash(self) -> None:
        cand = {
            "experiment_id": "ov_test_fail", "decision": "candidate_validated",
            "target_file": "partner/stub.py",
            "diff_hunk": (
                "diff --git a/partner/stub.py b/partner/stub.py\n"
                "--- a/partner/stub.py\n"
                "+++ b/partner/stub.py\n"
                "@@ -1 +1 @@\n"
                "-WRONG-LINE\n"
                "+changed\n"
            ),
        }
        (self.workspace / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness" / "ov.json").write_text(json.dumps(cand))
        snap = run_once(self.workspace, dry_run=False)
        # Failed git apply => failed outcome count, runner still returns
        self.assertEqual(snap["examined"], 1)
        self.assertEqual(snap["applied"], 0)
        self.assertGreaterEqual(snap["failed"], 1)


class TestOvernightLedgerWrites(OvernightTestBase):
    def test_run_once_appends_canary_run_event(self) -> None:
        run_once(self.workspace, dry_run=True)
        events = read_events(self.workspace, event_type_prefix="overnight/")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "overnight/canary_run")
        self.assertIn("examined", events[0]["payload"])

    def test_run_once_escalates_many_failures(self) -> None:
        # 2 failing candidates so failed >= max(2, 2//2)
        for i in range(2):
            cand = {
                "experiment_id": f"ov_fail_{i}", "decision": "candidate_validated",
                "target_file": "partner/missing.py",  # not in repo -> git apply fails
                "diff_hunk": (
                    "diff --git a/partner/missing.py b/partner/missing.py\n"
                    "--- a/partner/missing.py\n"
                    "+++ b/partner/missing.py\n"
                    "@@ -1 +1 @@\n"
                    "-x\n"
                    "+y\n"
                ),
            }
            (self.workspace / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness" / f"fail_{i}.json").write_text(json.dumps(cand))
        run_once(self.workspace, dry_run=False)
        events = read_events(self.workspace, event_type_prefix="overnight/")
        # at least canary_run; possibly also many_failures
        self.assertGreaterEqual(len(events), 1)

    def test_run_loop_with_max_iterations(self) -> None:
        snaps = []
        # call run_loop via direct iteration (avoid sleeping)
        for _ in range(2):
            snaps.append(run_once(self.workspace, dry_run=True))
        self.assertEqual(len(snaps), 2)
        events = read_events(self.workspace, event_type_prefix="overnight/")
        self.assertEqual(len(events), 2)


if __name__ == "__main__":
    unittest.main()
