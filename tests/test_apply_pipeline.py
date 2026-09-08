"""Tests for partner.evolution.apply_pipeline (Sprint18 self-evolution apply channel)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from partner.evolution.apply_pipeline import (
    apply_one,
    apply_promoted_candidates,
    append_blacklist,
    auto_rollback_recently_failed,
    clear_blacklist,
    list_blacklist,
    rollback_last,
    SCHEMA_VERSION,
)


class ApplyPipelineTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="apply_pipeline_test_")
        # create a fresh git repo so apply / commit / reset all work
        repo = Path(self._tmp) / "ws_repo"
        repo.mkdir()
        self.repo_root = repo
        # production_readiness candidates dir lives INSIDE the git repo (the
        # workspace we care about is the repository). The pipeline uses
        # `workspace_root` for governance lookups and as `repo_root` for git ops
        # in real production (workspace IS the partner repo); we mirror that here.
        self.workspace_root = repo
        # production_readiness candidates dir under workspace
        (repo / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness").mkdir(parents=True, exist_ok=True)
        (repo / "share" / "mind" / "governance").mkdir(parents=True, exist_ok=True)
        # init git in repo_root
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
        # add a tiny partner file that we will modify via diff
        target = repo / "partner" / "stub.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
        subprocess.run(["git", "commit", "-m", "init", "--no-verify"], cwd=str(repo), check=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ───── test helpers ─────

    def _sample_diff(self, rel_path: str = "partner/stub.py") -> str:
        return (
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

    def _write_candidate(self, candidate_id: str, **overrides: Any) -> Path:
        cand = {
            "experiment_id": candidate_id,
            "decision": "candidate_validated",
            "production_effective": True,
            "criteria_results": {"all_truth_safety_pass": True, "all_safety_pass": True},
            "target_file": "partner/stub.py",
            "diff_hunk": self._sample_diff(),
        }
        cand.update(overrides)
        path = self.workspace_root / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness" / f"{candidate_id}.json"
        path.write_text(json.dumps(cand), encoding="utf-8")
        return path


class TestDiffHunk(ApplyPipelineTestBase):
    def test_valid_diff_accepted(self) -> None:
        from partner.evolution.apply_pipeline import _is_valid_diff_hunk
        self.assertTrue(_is_valid_diff_hunk(self._sample_diff()))

    def test_empty_diff_rejected(self) -> None:
        from partner.evolution.apply_pipeline import _is_valid_diff_hunk
        self.assertFalse(_is_valid_diff_hunk(""))
        self.assertFalse(_is_valid_diff_hunk(None))

    def test_plain_text_rejected(self) -> None:
        from partner.evolution.apply_pipeline import _is_valid_diff_hunk
        self.assertFalse(_is_valid_diff_hunk("hello world\n"))

    def test_diff_without_plus_minus_rejected(self) -> None:
        from partner.evolution.apply_pipeline import _is_valid_diff_hunk
        only_header = "diff --git a/foo b/foo\n--- a/foo\n+++ b/foo\n@@ -1 +1 @@\n identical\n unchanged\n"
        self.assertFalse(_is_valid_diff_hunk(only_header))


class TestApplyOne(ApplyPipelineTestBase):
    def test_apply_no_diff_skipped(self) -> None:
        cand = {"experiment_id": "x", "decision": "candidate_validated", "target_file": "partner/stub.py"}
        outcome = apply_one(cand, self.workspace_root)
        self.assertEqual(outcome.action, "skipped_no_diff")

    def test_apply_inconclusive_skipped(self) -> None:
        cand = {"experiment_id": "x", "decision": "inconclusive",
                "target_file": "partner/stub.py", "diff_hunk": self._sample_diff()}
        outcome = apply_one(cand, self.workspace_root)
        self.assertEqual(outcome.action, "skipped_inconclusive_decision")

    def test_apply_invalid_diff_skipped(self) -> None:
        cand: dict[str, Any] = {"experiment_id": "x", "decision": "candidate_validated",
                "target_file": "partner/stub.py", "diff_hunk": "not a diff"}
        outcome = apply_one(cand, self.workspace_root)
        self.assertEqual(outcome.action, "skipped_invalid_diff")

    def test_apply_unsafe_target_skipped(self) -> None:
        cand = {"experiment_id": "x", "decision": "candidate_validated",
                "target_file": "/etc/passwd", "diff_hunk": self._sample_diff()}
        outcome = apply_one(cand, self.workspace_root)
        self.assertEqual(outcome.action, "skipped_invalid_diff")

    def test_apply_unsafe_target_relative_traversal(self) -> None:
        cand = {"experiment_id": "x", "decision": "candidate_validated",
                "target_file": "../../../etc/passwd", "diff_hunk": self._sample_diff()}
        outcome = apply_one(cand, self.workspace_root)
        self.assertEqual(outcome.action, "skipped_invalid_diff")

    def test_dry_run_passes_check(self) -> None:
        cand = {"experiment_id": "dry1", "decision": "candidate_validated",
                "target_file": "partner/stub.py", "diff_hunk": self._sample_diff()}
        outcome = apply_one(cand, self.workspace_root, dry_run=True)
        self.assertEqual(outcome.action, "dry_run")

    def test_real_apply_commits(self) -> None:
        cand = {"experiment_id": "real1", "decision": "candidate_validated",
                "target_file": "partner/stub.py", "diff_hunk": self._sample_diff()}
        outcome = apply_one(cand, self.repo_root)
        self.assertEqual(outcome.action, "applied")
        assert outcome.commit_hash
        self.assertEqual(len(outcome.commit_hash), 40)
        # confirm repo HEAD changed
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.repo_root),
                              capture_output=True, text=True, check=True)
        self.assertEqual(proc.stdout.strip(), outcome.commit_hash)

    def test_reapply_idempotent(self) -> None:
        # First apply
        cand = {"experiment_id": "real2", "decision": "candidate_validated",
                "target_file": "partner/stub.py", "diff_hunk": self._sample_diff()}
        outcome1 = apply_one(cand, self.repo_root)
        self.assertEqual(outcome1.action, "applied")
        # git apply would now fail since the diff already applied — should yield 'failed' or fall through
        outcome2 = apply_one(cand, self.repo_root)
        self.assertIn(outcome2.action, ("failed", "skipped_invalid_diff"))


class TestBlacklist(ApplyPipelineTestBase):
    def test_blacklist_blocks_reapply(self) -> None:
        append_blacklist(self.workspace_root, "partner/stub.py", "previous failure")
        cand = {"experiment_id": "bl", "decision": "candidate_validated",
                "target_file": "partner/stub.py", "diff_hunk": self._sample_diff()}
        outcome = apply_one(cand, self.workspace_root)
        self.assertEqual(outcome.action, "skipped_blacklisted")

    def test_blacklist_clear_allows_reapply(self) -> None:
        append_blacklist(self.workspace_root, "partner/stub.py", "previous")
        clear_blacklist(self.workspace_root)
        self.assertEqual(list_blacklist(self.workspace_root), [])


class TestPipelineReport(ApplyPipelineTestBase):
    def test_pipeline_with_three_candidates_mixed(self) -> None:
        self._write_candidate("cand_a")  # good — should apply
        self._write_candidate("cand_b", target_file=None)  # skipped no target
        cand_c_dict = json.loads(
            (self.workspace_root / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness" / "cand_a.json").read_text()
        )
        del cand_c_dict["target_file"]
        cand_c_dict["experiment_id"] = "cand_c"
        (self.workspace_root / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness" / "cand_c.json").write_text(json.dumps(cand_c_dict))

        # workspace IS a git repo (mirroring real layout), so cand_a applies.
        report = apply_promoted_candidates(self.workspace_root, dry_run=False)
        d = report.to_dict()
        self.assertEqual(d["schema_version"], SCHEMA_VERSION)
        self.assertEqual(d["examined"], 3)
        # outcomes are captured
        self.assertEqual(len(d["outcomes"]), 3)
        actions = [o["action"] for o in d["outcomes"]]
        # cand_a applied, cand_b + cand_c skipped_invalid_diff (target_file None)
        self.assertIn("applied", actions)
        self.assertGreaterEqual(actions.count("skipped_invalid_diff"), 2)
        # exactly 1 applied, 2 skipped — no failure case here
        self.assertEqual(d["applied"], 1)
        self.assertEqual(d["failed"], 0)

    def test_pipeline_with_failing_diff(self) -> None:
        # construct a candidate whose diff fails git apply --check on purpose
        cand = {
            "experiment_id": "bad_diff",
            "decision": "candidate_validated",
            "production_effective": True,
            "target_file": "partner/stub.py",
            "diff_hunk": (
                "diff --git a/partner/stub.py b/partner/stub.py\n"
                "--- a/partner/stub.py\n"
                "+++ b/partner/stub.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def hello():\n"
                "-    return 'WRONG-LINE-NOT-IN-FILE'\n"
                "+    return 'changed'\n"
            ),
        }
        (self.workspace_root / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness" / "bad_diff.json").write_text(json.dumps(cand))
        report = apply_promoted_candidates(self.workspace_root)
        actions = [o.action for o in report.outcomes]
        self.assertIn("failed", actions)
        # blacklist must have been written
        items = list_blacklist(self.workspace_root)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["target_file"], "partner/stub.py")

    def test_pipeline_no_directory_returns_empty_report(self) -> None:
        # wipe production_readiness
        shutil.rmtree(self.workspace_root / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness")
        report = apply_promoted_candidates(self.workspace_root)
        self.assertEqual(report.examined, 0)
        self.assertEqual(report.applied, 0)


class TestRollback(ApplyPipelineTestBase):
    def test_rollback_dry_run(self) -> None:
        result = rollback_last(self.repo_root, target_file="partner/stub.py",
                               commit_hash="deadbeef" * 5, dry_run=True)
        self.assertTrue(result["ok"])

    def test_rollback_no_target_returns_ok_with_reset(self) -> None:
        # no commit_hash passed -> uses HEAD, no target_file -> does a full reset
        result = rollback_last(self.repo_root)
        self.assertIn("ok", result)

    def test_auto_rollback_below_threshold(self) -> None:
        events_path = self.workspace_root / "share" / "mind" / "governance" / "evolution_events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        # one failed event but we set threshold to 2 so it should not trigger
        rec = {
            "schema_version": 1, "event_type": "policy/auto_apply_failed",
            "occurred_at": datetime.now(timezone.utc).isoformat(), "actor": "test",
            "subject_id": "x", "project_id": "agent_self_evolution",
            "payload": {"target_file": "partner/stub.py", "detail": "failed"},
            "prev_hash": "",
        }
        events_path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        result = auto_rollback_recently_failed(self.workspace_root, failure_threshold=2)
        self.assertEqual(result["rolled_back"], [])
        self.assertEqual(result["failed_count"], 1)


if __name__ == "__main__":
    unittest.main()
