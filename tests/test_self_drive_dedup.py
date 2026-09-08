"""Bug #56 layer 2 fix regression — self-drive dedup guard.

Real failure: 2026-09-05 instance 03 was injected with the same
"【03实例自动续跑】" task every ~4 minutes for ~50 minutes (seq 333→342
in event_pipeline.jsonl) because Hermes token plan was exhausted and
every batch_plan call returned HTTP 429. The self-drive daemon kept
emitting fresh inbox rows without checking whether the same project
had been failing in a tight loop.

This guard reads the instance's recent event_pipeline.jsonl and skips
the self-drive emit when too many task_failed events for this project
happened in the recent window. A pause ledger is written so operators
can see the dedup trigger fired and why.

See ADR 0063.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


def _load_self_drive_module():
    """scripts/run_project_self_drive.py has no __init__.py in its parent;
    load it via importlib so we can exercise its run_once and helpers."""
    path = "/mnt/e/work/partner/scripts/run_project_self_drive.py"
    spec = importlib.util.spec_from_file_location("rsd_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRecentTaskFailureCount(unittest.TestCase):
    """Pin the count + last-reason contract of the dedup guard."""

    def setUp(self):
        self.mod = _load_self_drive_module()
        self.tmp = Path(tempfile.mkdtemp(prefix="partner_dedup_"))
        self.inst = self.tmp / "instances" / "03" / "state"
        self.inst.mkdir(parents=True)
        (self.inst / "native_state.json").write_text(json.dumps({
            "phase": "WAIT_TASK", "reason": "", "project_id": "partner03_framework",
            "pending_message_id": "", "pending_kind": "",
        }))

    def _write_pipeline(self, rows):
        path = self.inst / "event_pipeline.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _make_failure_event(self, ts, msg_text=None):
        msg_text = msg_text or (
            "Partner ─ 03实例自动续跑_项目_03实例原生项目续跑_项目_Partner 框架与前端优化\n"
            "❌ Batch planner returned invalid JSON [type=ValueError, pos=unknown]: n"
        )
        return {"type": "task_failed", "status": "error",
                "ts": ts.isoformat(), "seq": 333, "msg": msg_text}

    # ── Real 03 incident shape (5 failures in 50 minutes) ──
    def test_five_failures_within_window_count(self):
        now = datetime.now(timezone.utc).astimezone()
        self._write_pipeline(
            [self._make_failure_event(now) for _ in range(5)]
        )
        count, last_reason, _ = self.mod._recent_task_failure_count(
            self.tmp, "03", "partner03_framework")
        assert count == 5, f"expected 5, got {count}"
        assert "Batch planner returned invalid JSON" in last_reason, (
            f"last_reason must contain the real failure message; got: {last_reason!r}"
        )

    def test_failures_outside_window_excluded(self):
        now = datetime.now(timezone.utc).astimezone()
        # 20 minutes ago, outside the 15-minute window
        old_ts = datetime.fromtimestamp(now.timestamp() - 20 * 60, tz=now.tzinfo)
        self._write_pipeline([self._make_failure_event(old_ts) for _ in range(10)])
        count, _, _ = self.mod._recent_task_failure_count(
            self.tmp, "03", "partner03_framework", window_minutes=15)
        assert count == 0, f"old failures must be excluded; got {count}"

    def test_missing_pipeline_returns_zero(self):
        # No event_pipeline.jsonl at all — guard must not crash.
        count, reason, _ = self.mod._recent_task_failure_count(
            self.tmp, "03", "partner03_framework")
        assert count == 0
        assert reason == ""

    def test_non_failure_events_not_counted(self):
        now = datetime.now(timezone.utc).astimezone()
        rows = [
            self._make_failure_event(now),
            {"type": "user_message", "ts": now.isoformat(),
             "text": "【03实例自动续跑】项目..."},
            {"type": "task_succeeded", "ts": now.isoformat(),
             "msg": "everything fine"},
        ]
        self._write_pipeline(rows)
        count, _, _ = self.mod._recent_task_failure_count(
            self.tmp, "03", "partner03_framework")
        assert count == 1, f"only task_failed must count; got {count}"

    def test_other_project_failures_excluded_by_heuristic(self):
        """The guard uses ROLE_PROJECT title substring as heuristic.
        Failures from a different project must not increment this counter."""
        now = datetime.now(timezone.utc).astimezone()
        other_msg = (
            "Partner ─ 04实例自动续跑_项目_04实例原生项目续跑_项目_文献与代码学习\n"
            "❌ some other failure"
        )
        self._write_pipeline([
            self._make_failure_event(now),
            self._make_failure_event(now, msg_text=other_msg),
        ])
        count, _, _ = self.mod._recent_task_failure_count(
            self.tmp, "03", "partner03_framework")
        # 03's own failure matches; 04's failure message lacks
        # ROLE_PROJECT[03] substring, so it must be excluded.
        assert count == 1, f"only 03's failure must count; got {count}"


class TestRunOnceDedup(unittest.TestCase):
    """End-to-end: run_once must skip an instance with too many
    recent failures, write the pause ledger, and continue emitting
    for healthy instances."""

    def setUp(self):
        self.mod = _load_self_drive_module()
        self.tmp = Path(tempfile.mkdtemp(prefix="partner_dedup_e2e_"))
        # Set up all 5 instances with their ROLE_PROJECT native_state
        self._setup_instance("01", "01实例原生项目续跑_项目_小红书账户推送与维护 最新")
        self._setup_instance("02", "02实例原生项目续跑_项目_分子生成方法创新与实践 最新")
        self._setup_instance("03", "03实例原生项目续跑_项目_Partner 框架与前端优化")
        self._setup_instance("04", "04实例原生项目续跑_项目_文献与代码学习 最新")
        self._setup_instance("05", "05实例原生项目续跑_项目_自进化研究 最新")

    def _setup_instance(self, instance_id: str, project_title: str):
        inst = self.tmp / "instances" / instance_id / "state"
        inst.mkdir(parents=True, exist_ok=True)
        (inst / "native_state.json").write_text(json.dumps({
            "phase": "WAIT_TASK", "reason": "", "project_id": "agent_self_evolution",
            "pending_message_id": "", "pending_kind": "",
        }))
        # Provide a recent task_failed event for 03 only
        if instance_id == "03":
            now = datetime.now(timezone.utc).astimezone()
            msg = (
                f"Partner ─ 03实例自动续跑_项目_{project_title}\n"
                "❌ Batch planner returned invalid JSON [type=ValueError, pos=unknown]: n"
            )
            rows = [{"type": "task_failed", "status": "error",
                     "ts": now.isoformat(), "seq": 333 + i, "msg": msg}
                    for i in range(5)]
            with (inst / "event_pipeline.jsonl").open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_03_dedup_skipped_others_emitted(self):
        # Make sure desktop_inbox parent dirs exist for all instances
        for i in ["01", "02", "03", "04", "05"]:
            (self.tmp / "instances" / i / "state").mkdir(parents=True, exist_ok=True)
        summary = self.mod.run_once(self.tmp)
        # 03 must be skipped with DEDUP marker
        skipped_03 = [s for s in summary["skipped"] if s.startswith("03:DEDUP")]
        assert skipped_03, (
            f"03 must be DEDUP-skipped; got skipped={summary['skipped']}"
        )
        # 03 must NOT be in emitted
        emitted_03 = [e for e in summary["emitted"] if e.startswith("03:")]
        assert not emitted_03, (
            f"03 must NOT be in emitted list; got emitted={summary['emitted']}"
        )

    def test_pause_ledger_written_with_observed_count(self):
        for i in ["01", "02", "03", "04", "05"]:
            (self.tmp / "instances" / i / "state").mkdir(parents=True, exist_ok=True)
        self.mod.run_once(self.tmp)
        ledger_path = self.tmp / "instances" / "03" / "state" / "auto_drive_pause.json"
        assert ledger_path.exists(), f"pause ledger must exist at {ledger_path}"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert ledger["status"] == "paused"
        assert ledger["observed_failure_count"] == 5
        assert ledger["failure_threshold"] == self.mod.DEFAULT_FAILURE_THRESHOLD
        assert "Batch planner returned invalid JSON" in ledger["last_failure_reason"]

    def test_threshold_env_var_overrides_default(self):
        for i in ["01", "02", "03", "04", "05"]:
            (self.tmp / "instances" / i / "state").mkdir(parents=True, exist_ok=True)
        # With threshold=10, the 5 failures must NOT trigger dedup
        os.environ["PARTNER_SELF_DRIVE_FAILURE_THRESHOLD"] = "10"
        try:
            summary = self.mod.run_once(self.tmp)
            emitted_03 = [e for e in summary["emitted"] if e.startswith("03:")]
            assert emitted_03, (
                "with threshold=10 and only 5 failures, 03 must still emit; "
                f"got summary={summary}"
            )
        finally:
            del os.environ["PARTNER_SELF_DRIVE_FAILURE_THRESHOLD"]

    def test_ledger_not_written_for_healthy_instance(self):
        for i in ["01", "02", "03", "04", "05"]:
            (self.tmp / "instances" / i / "state").mkdir(parents=True, exist_ok=True)
        self.mod.run_once(self.tmp)
        # 01 has no failures — must NOT have a pause ledger
        ledger_01 = self.tmp / "instances" / "01" / "state" / "auto_drive_pause.json"
        assert not ledger_01.exists(), (
            f"01 must not have a pause ledger; found at {ledger_01}"
        )

    # ── Bug #58 P1.1 (ADR 0066): recent_success must short-circuit dedup ──
    def test_recent_success_returns_true(self):
        # TestRunOnceDedup.setUp already seeded 03 with 5 task_failed
        # events under the ROLE_PROJECT[03] title.  Append one
        # task_succeeded (with the same project title in its msg so the
        # guard's heuristic matcher accepts it) and verify recent_success.
        now = datetime.now(timezone.utc).astimezone()
        pipeline = self.tmp / "instances" / "03" / "state" / "event_pipeline.jsonl"
        msg = (
            "Partner ─ 03实例自动续跑_项目_03实例原生项目续跑_项目_Partner 框架与前端优化\n"
            "✅ 03 real task succeeded"
        )
        with pipeline.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "task_succeeded", "status": "ok",
                "ts": now.isoformat(), "seq": 999, "msg": msg,
            }) + "\n")
        _count, _reason, recent_success = self.mod._recent_task_failure_count(
            self.tmp, "03", "partner03_framework")
        assert recent_success is True, (
            "task_succeeded in window must set recent_success=True"
        )

    def test_recent_success_overrides_failure_count(self):
        """A failure + a later success in the same window must NOT pause
        dedup — the success proves the instance recovered."""
        now = datetime.now(timezone.utc).astimezone()
        pipeline = self.tmp / "instances" / "03" / "state" / "event_pipeline.jsonl"
        msg = (
            "Partner ─ 03实例自动续跑_项目_03实例原生项目续跑_项目_Partner 框架与前端优化\n"
            "✅ 03 succeeded after failures"
        )
        with pipeline.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "task_succeeded", "status": "ok",
                "ts": now.isoformat(), "seq": 999, "msg": msg,
            }) + "\n")
        count, _reason, recent_success = self.mod._recent_task_failure_count(
            self.tmp, "03", "partner03_framework")
        assert count == 5  # the 5 seeded failures are still in the window
        assert recent_success is True


if __name__ == "__main__":
    unittest.main()
