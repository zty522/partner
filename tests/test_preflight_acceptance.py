"""Bug #59 (ADR 0067) — batch_planner preflight regression tests.

Pin the two fixes that un-strand instances after P0.3 auto_resume.
"""
import sys
import tempfile
import unittest
from pathlib import Path


def _load_batch_planner():
    import importlib
    return importlib.import_module("partner.planner.batch_planner")


# Minimal event registry for preflight's registry.get(event_type) check.
_REGISTRY_EVENTS = {
    "atomic_inspect_file": True,
    "atomic_read_state": True,
    "atomic_list_project_files": True,
    "atomic_list_directory": True,
    "atomic_write_artifact": True,
    "create_file": True,
    "generate_text": True,
    "list_directory": True,
    "read_file": True,
}


class _FakeStep:
    """Shim that gives batch_planner preflight the attributes it
    reads off each plan step (id, event_type, parameters, depends_on)."""

    def __init__(self, d):
        self.id = d.get("id", "")
        self.event_type = d.get("event_type", "")
        self.parameters = d.get("parameters", {}) or {}
        self.depends_on = d.get("depends_on", []) or []


class _FakePlan:
    """Shim for MicroPlan — preflight iterates ``plan.plan`` and reads
    step attributes via the dataclass fields above."""

    def __init__(self, step_dicts):
        self.plan = [_FakeStep(d) for d in step_dicts]
        self.expected_artifacts = []


class TestPreflightAcceptsFirstIteration(unittest.TestCase):
    """P0: a read step whose path is under an allowed read root but
    the file does not exist on disk must NOT fail the whole plan —
    it should be allowed with a warning."""

    def setUp(self):
        self.mod = _load_batch_planner()

    def _preflight(self, plan, tmp):
        class _Registry:
            """Minimal registry stub: ``.get(event_type)`` returns a
            truthy sentinel for known event_types, None otherwise."""
            def get(self, event_type):
                return _REGISTRY_EVENTS.get(event_type)
        # ``_manual_preflight_plan`` raises ValueError when issues are
        # found; it returns a normalised MicroPlan when clean.  Capture
        # the issues list from the raised message so tests can assert
        # on the rejection/warning contract.
        try:
            self.mod._manual_preflight_plan(
                _FakePlan(plan),
                registry=_Registry(),
                workspace=str(tmp),
                working_dir=str(tmp),
                user_message="m",
            )
            return []
        except ValueError as exc:
            msg = str(exc)
            # Strip the "manual plan preflight failed: " prefix.
            prefix = "manual plan preflight failed: "
            if msg.startswith(prefix):
                msg = msg[len(prefix):]
            return msg.split("; ")

    def test_existing_file_passes(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            f = tmp / "existing.md"
            f.write_text("hello")
            issues = self._preflight(
                [{"id": "s1", "event_type": "atomic_inspect_file",
                  "parameters": {"path": str(f)}}],
                tmp)
            # Existing file must produce zero preflight issues.
            self.assertEqual(issues, [],
                              f"existing file must pass, got {issues}")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_file_in_allowed_root_is_rejected_before_execution(self):
        """An allowed parent directory does not make an invented file evidence."""
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "share" / "projects").mkdir(parents=True)
            missing = tmp / "share" / "projects" / "demo" / "state.md"
            issues = self._preflight(
                [{"id": "s1", "event_type": "atomic_inspect_file",
                  "parameters": {"path": str(missing)}}],
                tmp)
            self.assertTrue(any("missing or outside allowed roots" in value for value in issues))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestListProjectFilesPreflight(unittest.TestCase):
    """P1: atomic_list_project_files with an explicit ``directory``
    argument is no longer hard-rejected."""

    def setUp(self):
        self.mod = _load_batch_planner()

    def test_explicit_directory_requires_general_list_event(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            d = tmp / "share" / "projects" / "demo"
            d.mkdir(parents=True)
            class _Registry:
                def get(self, event_type):
                    return _REGISTRY_EVENTS.get(event_type)
            try:
                self.mod._manual_preflight_plan(
                    _FakePlan([{
                        "id": "s1", "event_type": "atomic_list_project_files",
                        "parameters": {"directory": str(d), "limit": 50},
                    }]),
                    registry=_Registry(),
                    workspace=str(tmp),
                    working_dir=str(tmp),
                    user_message="m",
                )
                issues = []
            except ValueError as exc:
                msg = str(exc)
                prefix = "manual plan preflight failed: "
                if msg.startswith(prefix):
                    msg = msg[len(prefix):]
                issues = msg.split("; ")
            joined = "; ".join(issues)
            self.assertIn("use list_directory", joined)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestListDirectoryPreflightExistenceCheck(unittest.TestCase):
    """Bug #63 (ADR 0067): list_directory with a truncated or
    missing directory path (e.g. share/projects/molgen_explorati
    when the real id is molecular_dynamics_study) used to pass
    preflight silently and then fail at runtime with
    FileNotFoundError.  Now the preflight rejects it."""

    def setUp(self):
        self.mod = _load_batch_planner()

    def _preflight(self, plan, tmp):
        class _Registry:
            def get(self, event_type):
                return _REGISTRY_EVENTS.get(event_type)
        try:
            self.mod._manual_preflight_plan(
                _FakePlan(plan),
                registry=_Registry(),
                workspace=str(tmp),
                working_dir=str(tmp),
                user_message="m",
            )
            return []
        except ValueError as exc:
            msg = str(exc)
            prefix = "manual plan preflight failed: "
            if msg.startswith(prefix):
                msg = msg[len(prefix):]
            return msg.split("; ")

    def test_existing_directory_passes(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            d = tmp / "share" / "projects" / "demo"
            d.mkdir(parents=True)
            issues = self._preflight(
                [{"id": "s1", "event_type": "list_directory",
                  "parameters": {"path": str(d)}}],
                tmp)
            self.assertEqual(issues, [],
                              f"existing dir must pass, got {issues}")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_directory_in_allowed_root_rejected(self):
        """LLM-generated plans sometimes pass a truncated project id
        path (e.g. share/projects/molgen_explorati when the real id
        is molecular_dynamics_study).  Production 03 task on
        2026-09-07 hit this: preflight passed, list_directory step
        failed at runtime with FileNotFoundError, failure_mechanism
        empty, task status=failed.  Force rejection so planner
        retry loop generates a corrected plan."""
        tmp = Path(tempfile.mkdtemp())
        try:
            missing = tmp / "share" / "projects" / "molgen_explorati"
            issues = self._preflight(
                [{"id": "s1", "event_type": "list_directory",
                  "parameters": {"path": str(missing)}}],
                tmp)
            joined = "; ".join(issues)
            self.assertIn(
                "does not exist on disk", joined,
                f"missing directory must be rejected, got {issues}")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
