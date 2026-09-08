"""Self-drive oneshot regression tests.

Verify ``scripts/run_project_self_drive_oneshot.py`` behaviour:

  * idempotent — running twice produces the same emitted/skipped split
  * skips instances whose most recent task is still pending (no spam)
  * skips instances whose project has no project brief (no legacy
    "自动续跑" fallback template)
  * actually writes a per-instance inbox row when the project has a
    real next action and no inflight task

Uses an in-memory tempdir rather than the production workspace so the
tests never disturb live state.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _load_module():
    path = "/mnt/e/work/partner/scripts/run_project_self_drive_oneshot.py"
    spec = importlib.util.spec_from_file_location("rsd_oneshot", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_workspace(root: Path):
    """Build the minimum layout instance_native.py / project_loop
    read: instances/<id>/state/, share/projects/<project>/, etc."""
    for inst in ["01", "02", "03", "04", "05"]:
        inst_root = root / "instances" / inst
        (inst_root / "state").mkdir(parents=True, exist_ok=True)
        # Native state defaults are filled in by load_state from PROJECTS dict.
        # Write the latest task as done so inflight check is clean.
        tasks = inst_root / "state" / "tasks"
        tasks.mkdir(parents=True, exist_ok=True)
        task_dir = tasks / "first-task"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "task_instance.json").write_text(json.dumps({
            "task_id": "first-task", "completion_status": "done",
            "user_message": "seed",
        }), encoding="utf-8")
    # Provide one project with a real brief and a real receipt with
    # next_actions so request_next_action returns a proposed action.
    proj = root / "share" / "projects" / "molecular_generation"
    (proj / "governance" / "receipts").mkdir(parents=True, exist_ok=True)
    (proj / "governance" / "project_state.json").write_text(json.dumps({
        "status": "active",
        "last_iteration_at": "2026-09-06T10:00:00+08:00",
        "last_iteration_kind": "experiment",
        "allow_continue": True,
        "latest_receipt_id": "test_receipt_001",
        "current_iteration": 1,
    }), encoding="utf-8")
    (proj / "project_brief.md").write_text(
        "# molecular_generation\n\nRun a real generation experiment.\n",
        encoding="utf-8")
    (proj / "state.md").write_text("state: idle\n", encoding="utf-8")
    # Receipt with one proposed next_action — this is what
    # request_next_action consumes to produce a non-empty proposed action.
    # Filename MUST be {iteration:04d}_{safe_id(receipt_id)}.json or
    # governance/storage.latest_receipt will skip it (Bug #56 regression).
    # NextAction.from_dict requires `title` (not `description`) — the
    # partner models.py dataclass enforces it.
    receipt = {
        "receipt_id": "test_receipt_001",
        "project_id": "molecular_generation",
        "iteration": 1,
        "goal": "generate one molecule",
        "inputs": [],
        "actions_executed": ["exec:python3 -m pytest tests -q"],
        "artifacts": [],
        "findings": [],
        "unresolved_questions": [],
        "next_actions": [
            {
                "title": "Run the next molecular generation round",
                "action_id": "action_002",
                "event_type": "generate_molecules",
                "params": {"user_request": "检查交付物并补齐缺口"},
                "status": "proposed",
                "created_at": "2026-09-06T10:00:00+08:00",
            }
        ],
        "stop_reason": "",
        "delivery_confirmed": True,
    }
    (proj / "governance" / "receipts" / "0001_test_receipt_001.json").write_text(
        json.dumps(receipt, ensure_ascii=False), encoding="utf-8")


class TestOneshotSkips(unittest.TestCase):
    """Cases where the script must NOT emit a row."""

    def setUp(self):
        self.mod = _load_module()
        self.tmp = Path(tempfile.mkdtemp(prefix="oneshot_test_"))
        _make_workspace(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_idempotent_across_runs(self):
        """After first run, second run must skip all instances that
        were just emitted because their inbox still has an unread
        self_drive_oneshot row.
        """
        first = self.mod.run_once(self.tmp)
        second = self.mod.run_once(self.tmp)
        # First run emits a row for 02 (molecular_generation has brief).
        self.assertEqual(len(first["emitted"]), 1)
        # Second run must not re-emit that same instance.
        self.assertEqual(second["emitted"], [])
        # And 02 must show up as skipped_pending on the second run.
        self.assertIn("02", second["skipped_pending"])
        # Other skip buckets stay stable across runs.
        self.assertEqual(first["skipped_no_next"], second["skipped_no_next"])

    def test_no_emission_uses_legacy_template(self):
        """The legacy '自动续跑' template must never appear in emitted
        rows — that's the whole point of the oneshot rewrite."""
        summary = self.mod.run_once(self.tmp)
        # Anything emitted must contain a project_id field sourced from
        # the real project loop, not from a fixed ROLE_PROJECT string.
        for entry in summary["emitted"]:
            inst, project_id, _ = entry.split(":")
            self.assertNotIn("XX实例", project_id)
            self.assertNotIn("原生项目续跑", project_id)
        # And check the inbox rows that were written
        for inst in ("01", "02", "03", "04", "05"):
            inbox = self.tmp / "instances" / inst / "state" / "desktop_inbox.jsonl"
            if inbox.exists():
                for line in inbox.read_text(encoding="utf-8").splitlines():
                    rec = json.loads(line)
                    self.assertNotIn(
                        "【XX实例自动续跑】", str(rec.get("text") or ""))

    def test_skips_instance_with_inflight_pending_task(self):
        """Instance with a pending task must not receive another emit."""
        # Force instance 02 to have a pending task.
        inst = self.tmp / "instances" / "02"
        tasks = inst / "state" / "tasks"
        # Replace the seed done task with a pending one
        for t in tasks.glob("*/task_instance.json"):
            t.unlink()
        pending_dir = tasks / "inflight"
        pending_dir.mkdir(parents=True, exist_ok=True)
        (pending_dir / "task_instance.json").write_text(json.dumps({
            "task_id": "inflight", "completion_status": "pending",
            "user_message": "in flight",
        }), encoding="utf-8")
        summary = self.mod.run_once(self.tmp)
        emitted_insts = [e.split(":")[0] for e in summary["emitted"]]
        self.assertNotIn("02", emitted_insts)
        self.assertTrue(
            any(s.startswith("02") for s in summary["skipped_pending"]),
            f"02 must be in skipped_pending, got {summary['skipped_pending']}",
        )

    def test_no_next_action_when_no_brief(self):
        """Projects without project_brief.md must skip rather than
        fall back to the legacy template."""
        # molecular_generation has a brief; others don't.
        summary = self.mod.run_once(self.tmp)
        # 4 instances should fall into skipped_no_next (no brief).
        # 01=agent_self_evolution / 03=molecular_dynamics_study /
        # 04=literature_github_learning / 05=hermes_partner_explore all
        # lack briefs in the fixture.
        self.assertEqual(len(summary["skipped_no_next"]), 4,
                         f"expected 4 skipped_no_next, got {summary['skipped_no_next']}")
        # 02=molecular_generation has the brief; should NOT be in no_next.
        no_next_instances = [s.split(":")[0] for s in summary["skipped_no_next"]]
        self.assertNotIn("02", no_next_instances)


class TestOneshotEmits(unittest.TestCase):
    """Cases where the script MUST emit a row."""

    def setUp(self):
        self.mod = _load_module()
        self.tmp = Path(tempfile.mkdtemp(prefix="oneshot_emit_"))
        _make_workspace(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_inbox_row_with_real_project_id(self):
        """02 (molecular_generation) has a real brief + state.md +
        project_state.json, no inflight task → must emit one row."""
        summary = self.mod.run_once(self.tmp)
        self.assertEqual(len(summary["emitted"]), 1,
                         f"expected exactly 1 emit, got {summary}")
        entry = summary["emitted"][0]
        self.assertTrue(entry.startswith("02:molecular_generation:"))
        inbox = self.tmp / "instances" / "02" / "state" / "desktop_inbox.jsonl"
        self.assertTrue(inbox.exists(), f"inbox not written: {inbox}")
        rows = [json.loads(l) for l in inbox.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project_id"], "molecular_generation")
        self.assertEqual(rows[0]["source"], "instance_native")
        self.assertTrue(rows[0]["self_drive_oneshot"])
        self.assertNotIn("【XX实例自动续跑】", rows[0]["text"])

    def test_second_run_no_double_emit(self):
        """After emit, second run within the same inflight window must
        not write another row — oneshot must be one-shot per cycle."""
        first = self.mod.run_once(self.tmp)
        second = self.mod.run_once(self.tmp)
        self.assertEqual(len(first["emitted"]), 1)
        # Second run: 02 has a freshly-emitted row still unread in inbox.
        self.assertEqual(len(second["emitted"]), 0)
        self.assertTrue(
            any(s == "02" for s in second["skipped_pending"]),
            f"02 must be skipped_pending on second run, got {second['skipped_pending']}",
        )
        # And the inbox still has exactly one row.
        inbox = self.tmp / "instances" / "02" / "state" / "desktop_inbox.jsonl"
        rows = [json.loads(l) for l in inbox.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)


class TestOneshotInvariants(unittest.TestCase):
    """Source-level guards pinning the script's contract."""

    def test_no_loop_in_oneshot(self):
        """The oneshot script must NOT have a while-true / asyncio loop —
        it should run exactly once and exit. Legacy self-drive has one."""
        path = "/mnt/e/work/partner/scripts/run_project_self_drive_oneshot.py"
        src = Path(path).read_text(encoding="utf-8")
        self.assertNotIn("while True", src,
                          "oneshot must not have an event loop")
        self.assertNotIn("asyncio.sleep", src,
                          "oneshot must not have async sleep loop")

    def test_legacy_loop_unchanged_in_count(self):
        """The legacy self-drive is replaced at the script layer; this
        test pins that the oneshot doesn't accidentally call into the
        180s-tick loop. Verify by source-level search."""
        path = "/mnt/e/work/partner/scripts/run_project_self_drive_oneshot.py"
        src = Path(path).read_text(encoding="utf-8")
        self.assertNotIn("run_loop", src,
                          "oneshot must not call the legacy run_loop")
        self.assertNotIn("DEFAULT_INTERVAL", src,
                          "oneshot must not depend on legacy 180s tick interval")


if __name__ == "__main__":
    unittest.main()
