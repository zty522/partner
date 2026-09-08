"""Storage contract regression — pin partner/governance/storage.py
and partner/governance/models.py behaviour that the runtime + oneshot
+ project_scaffold all depend on.

Catches breakage when the partner framework is refactored. Each test
pins one observable contract. If you change any of these, expect
to also update the consumer (oneshot / runtime / dashboard) and
the ADR.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


def _load_storage():
    """Load via the partner package so dataclass __module__ resolves."""
    return importlib.import_module("partner.governance.storage")


def _load_models():
    return importlib.import_module("partner.governance.models")


def _load_project_scaffold():
    return importlib.import_module("partner.governance.project_scaffold")


class TestReceiptFilenameContract(unittest.TestCase):
    """``storage.latest_receipt`` globs ``*.json`` inside
    ``share/projects/<id>/governance/receipts/``. The filename MUST
    match ``{iteration:04d}_{safe_id(receipt_id)}.json`` for the
    file to be picked up after invalidation handling."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="storage_contract_"))
        (self.tmp / "share" / "projects" / "demo" / "governance" / "receipts").mkdir(
            parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_receipt(self, name: str, receipt: dict):
        path = self.tmp / "share" / "projects" / "demo" / "governance" / "receipts" / name
        path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        return path

    def _good_receipt(self, receipt_id: str = "r1", iteration: int = 1):
        return {
            "receipt_id": receipt_id,
            "project_id": "demo",
            "iteration": iteration,
            "goal": "g",
            "inputs": [],
            "actions_executed": ["exec:x"],
            "artifacts": [],
            "findings": [],
            "unresolved_questions": [],
            "next_actions": [],
            "stop_reason": "no_more_actions",
            "delivery_confirmed": True,
        }

    def test_canonical_filename_picked_up(self):
        """``0001_r1.json`` is read by latest_receipt."""
        self._write_receipt("0001_r1.json", self._good_receipt())
        storage = _load_storage()
        latest = storage.latest_receipt(str(self.tmp), "demo")
        self.assertIsNotNone(latest, "canonical filename must be picked up")
        self.assertEqual(latest.receipt_id, "r1")
        self.assertEqual(latest.iteration, 1)

    def test_non_canonical_filename_also_picked_up(self):
        """``r1.json`` (without iteration prefix) is read by
        latest_receipt because it globs ``*.json`` after filtering
        invalidations. Document the actual contract — the oneshot
        fixture relies on this when it writes ``0001_*.json``."""
        self._write_receipt("r1.json", self._good_receipt())
        storage = _load_storage()
        latest = storage.latest_receipt(str(self.tmp), "demo")
        self.assertIsNotNone(
            latest,
            "latest_receipt picks up any *.json under receipts/ "
            "as long as it isn't invalidated; non-canonical names "
            "still load (documented behaviour the oneshot relies on)",
        )

    def test_invalidation_removes_receipt(self):
        """A receipt invalidated via append_receipt_corrections must
        not be returned by latest_receipt."""
        storage = _load_storage()
        self._write_receipt("0001_r1.json", self._good_receipt())
        corrections_path = (self.tmp / "share" / "projects" / "demo" /
                            "governance" / "receipt_corrections.jsonl")
        corrections_path.parent.mkdir(parents=True, exist_ok=True)
        corrections_path.write_text(json.dumps({
            "action": "invalidate", "receipt_id": "r1",
            "reason": "test", "evidence": ["e1"],
        }) + "\n", encoding="utf-8")
        latest = storage.latest_receipt(str(self.tmp), "demo")
        self.assertIsNone(latest, "invalidated receipt must be hidden")


class TestNextActionFieldContract(unittest.TestCase):
    """``NextAction.from_dict`` requires ``title`` and ``event_type``;
    optional fields are params/status/action_id."""

    def test_title_and_event_type_required(self):
        models = _load_models()
        # Missing title → ValueError
        with self.assertRaises(Exception) as ctx:
            models.NextAction.from_dict({"event_type": "x"})
        self.assertIn("title", str(ctx.exception).lower())
        # Missing event_type → ValueError
        with self.assertRaises(Exception) as ctx:
            models.NextAction.from_dict({"title": "t"})
        self.assertIn("event_type", str(ctx.exception).lower())

    def test_description_field_does_not_satisfy_title(self):
        """The oneshot tests originally used ``description`` instead of
        ``title`` and silently fell back to skipped_no_next. Pin the
        contract that ``title`` is the field name, not ``description``."""
        models = _load_models()
        with self.assertRaises(Exception):
            models.NextAction.from_dict({"description": "t", "event_type": "x"})

    def test_status_must_be_known(self):
        models = _load_models()
        with self.assertRaises(Exception) as ctx:
            models.NextAction.from_dict({
                "title": "t", "event_type": "x", "status": "bogus"})
        self.assertIn("invalid action status", str(ctx.exception).lower())


class TestIterationReceiptValidation(unittest.TestCase):
    """IterationReceipt.validate enforces hard contracts used by the
    production_readiness gate."""

    def test_actions_executed_must_be_nonempty(self):
        models = _load_models()
        with self.assertRaises(Exception) as ctx:
            models.IterationReceipt.from_dict({
                "receipt_id": "r1", "project_id": "p", "iteration": 1,
                "goal": "g", "inputs": [], "actions_executed": [],
                "artifacts": [], "findings": [],
                "unresolved_questions": [], "next_actions": [],
                "stop_reason": "ok",
            })
        self.assertIn("actions_executed", str(ctx.exception).lower())

    def test_must_have_either_next_actions_or_stop_reason(self):
        models = _load_models()
        with self.assertRaises(Exception):
            models.IterationReceipt.from_dict({
                "receipt_id": "r1", "project_id": "p", "iteration": 1,
                "goal": "g", "inputs": [], "actions_executed": ["x"],
                "artifacts": [], "findings": [],
                "unresolved_questions": [], "next_actions": [],
                "stop_reason": "",
            })


class TestProjectScaffoldContract(unittest.TestCase):
    """``project_scaffold`` must create the minimum layout the
    project loop and oneshot depend on, and be idempotent."""

    def setUp(self):
        self.mod = _load_project_scaffold()
        self.tmp = Path(tempfile.mkdtemp(prefix="scaffold_contract_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_full_layout(self):
        summary = self.mod.scaffold_project(
            self.tmp, "01", "xiaohongshu_operations", "小红书账户推送")
        proj = Path(summary["project_root"])
        self.assertTrue((proj / "project_brief.md").exists())
        self.assertTrue((proj / "state.md").exists())
        self.assertTrue((proj / "governance" / "project_state.json").exists())
        self.assertTrue((proj / "governance" / "receipts").is_dir())
        self.assertTrue((proj / "external_artifacts").is_dir())
        self.assertTrue((proj / "external_sources").is_dir())

    def test_brief_contains_goal(self):
        self.mod.scaffold_project(
            self.tmp, "01", "xiaohongshu_operations", "为小红书账户撰写推文")
        proj = self.tmp / "share" / "projects" / "xiaohongshu_operations"
        brief = (proj / "project_brief.md").read_text(encoding="utf-8")
        self.assertIn("为小红书账户撰写推文", brief)

    def test_idempotent(self):
        s1 = self.mod.scaffold_project(
            self.tmp, "01", "xiaohongshu_operations", "goal")
        s2 = self.mod.scaffold_project(
            self.tmp, "01", "xiaohongshu_operations", "goal")
        self.assertFalse(s1["skipped_existing"])
        self.assertTrue(s2["skipped_existing"])
        self.assertEqual(s2["created"], [])

    def test_handles_instance_workspace_path(self):
        """scaffold_project must accept an instance workspace
        (``instances/03``) and walk up to the root workspace correctly."""
        inst_ws = self.tmp / "instances" / "03"
        inst_ws.mkdir(parents=True)
        s = self.mod.scaffold_project(
            inst_ws, "03", "molecular_dynamics_study", "MD study")
        self.assertFalse(s["skipped_existing"])
        self.assertTrue(
            (self.tmp / "share" / "projects" / "molecular_dynamics_study"
             / "project_brief.md").exists())

    def test_placeholder_brief_replaced(self):
        """Bug #58 P0.1: a project whose brief is the legacy_seeded
        placeholder (``待补充。``) gets its brief overwritten with a
        real one derived from the goal — so the project loop can
        finally return a proposed next action."""
        # Pre-create the project dir with placeholder brief
        proj = self.tmp / "share" / "projects" / "p1"
        proj.mkdir(parents=True)
        (proj / "governance" / "receipts").mkdir(parents=True)
        (proj / "external_artifacts").mkdir(parents=True)
        (proj / "external_sources").mkdir(parents=True)
        (proj / "project_brief.md").write_text(
            "# p1\n\n## 项目目标\n待补充。\n\n## 当前主线\n待补充。\n",
            encoding="utf-8")
        s = self.mod.scaffold_project(
            self.tmp, "01", "p1", "为小红书账户撰写推文")
        self.assertTrue(s["skipped_existing"])
        self.assertTrue(s.get("replaced_placeholder_brief"))
        new_brief = (proj / "project_brief.md").read_text(encoding="utf-8")
        self.assertIn("为小红书账户撰写推文", new_brief)
        self.assertNotIn("待补充", new_brief)

    def test_real_brief_not_replaced(self):
        """A real brief (no placeholder) must NOT be overwritten on
        a second scaffold call — production_readiness data must
        survive idempotent scaffold."""
        proj = self.tmp / "share" / "projects" / "p2"
        proj.mkdir(parents=True)
        (proj / "governance" / "receipts").mkdir(parents=True)
        (proj / "external_artifacts").mkdir(parents=True)
        (proj / "external_sources").mkdir(parents=True)
        real_brief = "# p2\n\n## Goal\nReal concrete goal\n"
        (proj / "project_brief.md").write_text(real_brief, encoding="utf-8")
        s = self.mod.scaffold_project(
            self.tmp, "02", "p2", "different goal")
        self.assertTrue(s["skipped_existing"])
        self.assertNotIn("replaced_placeholder_brief", s)
        self.assertEqual(
            (proj / "project_brief.md").read_text(encoding="utf-8"),
            real_brief,
        )


class TestLoadStateProjectIdContract(unittest.TestCase):
    """Bug #58 P0.3 follow-up: load_state used to unconditionally
    pull PROJECTS[instance_id] default, overwriting any project_id
    the instance had actually switched to.  Pin the contract: the
    on-disk project_id wins."""

    def setUp(self):
        self.mod = importlib.import_module("partner.governance.instance_native")
        self.tmp = Path(tempfile.mkdtemp(prefix="load_state_contract_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_state(self, instance_id: str, project_id: str) -> None:
        inst = self.tmp / "instances" / instance_id / "state"
        inst.mkdir(parents=True)
        (inst / "native_state.json").write_text(json.dumps({
            "phase": "WAIT_TASK", "reason": "",
            "project_id": project_id,
        }), encoding="utf-8")

    def test_on_disk_project_id_wins_over_default(self):
        # 03's native_state stores "molecular_dynamics_study" even
        # though PROJECTS[03] default is "partner_framework_frontend".
        self._write_state("03", "molecular_dynamics_study")
        s = self.mod.load_state(self.tmp, "03")
        self.assertEqual(s.project_id, "molecular_dynamics_study")

    def test_missing_file_uses_default(self):
        # No native_state.json at all → fall back to PROJECTS default.
        s = self.mod.load_state(self.tmp, "03")
        # PROJECTS[03] = "molecular_dynamics_study"
        self.assertEqual(s.project_id, "molecular_dynamics_study")


if __name__ == "__main__":
    unittest.main()
