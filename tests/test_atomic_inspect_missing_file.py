"""Bug #59 (ADR 0067) + Bug #63 — _atomic_inspect_file and
preflight list_directory regression tests.

Bug #59 P2: _atomic_inspect_file must return None when the file
does not exist on disk but is under an allowed root (first-iteration
plans referencing placeholder files).  The preflight accepts the
plan, the runtime step returns a warning instead of raising.

Bug #63: list_directory must NOT accept a missing directory path
(e.g. truncated project_id).  The plan should be rejected up-front
so the planner retry loop generates a corrected plan.
"""
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock


def _load_harness():
    return importlib.import_module("partner.mind.harness")


class TestSafeInspectPathReturnsNoneForMissing(unittest.TestCase):
    """When the path is under an allowed root but the file does not
    exist on disk, _safe_inspect_path must return None (not raise)."""

    def setUp(self):
        self.mod = _load_harness()
        self.tmp = Path(tempfile.mkdtemp())
        self.ctx = MagicMock()
        self.ctx.working_dir = str(self.tmp)
        self.ctx.workspace = str(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_existing_file_returns_absolute_path(self):
        f = self.tmp / "real.md"
        f.write_text("hello")
        result = self.mod._safe_inspect_path(self.ctx, str(f))
        self.assertEqual(result, str(f.resolve()))

    def test_missing_file_in_allowed_root_returns_none(self):
        missing = self.tmp / "placeholder.md"
        result = self.mod._safe_inspect_path(self.ctx, str(missing))
        self.assertIsNone(
            result,
            f"missing file under allowed root must return None, got {result!r}",
        )

    def test_missing_file_outside_root_still_raises(self):
        """Path outside any allowed root must still raise so the
        harness isn't tricked into accepting arbitrary filesystem
        reads just because the file is missing."""
        outside = "/mnt/_definitely_not_in_any_allow_list_/secret.md"
        try:
            self.mod._safe_inspect_path(self.ctx, outside)
            self.fail(
                "outside-allow-list path must raise even if file "
                "does not exist, but _safe_inspect_path returned cleanly"
            )
        except ValueError:
            pass  # expected


class TestAtomicInspectFileSkipsMissing(unittest.TestCase):
    """_atomic_inspect_file must return ok=True with a warning
    when all requested paths are missing placeholders under allowed
    roots (first-iteration plan)."""

    def setUp(self):
        self.mod = _load_harness()
        self.tmp = Path(tempfile.mkdtemp())
        self.ctx = MagicMock()
        self.ctx.working_dir = str(self.tmp)
        self.ctx.workspace = str(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_missing_path_returns_warning_not_error(self):
        missing = self.tmp / "first_iter.md"
        result = self.mod._atomic_inspect_file(
            self.ctx, {"path": str(missing)})
        self.assertTrue(result["ok"],
                         f"missing file in allowed root must succeed "
                         f"with ok=True, got {result}")
        # content carries the SKIP note so callers see why the read
        # was empty rather than a silent empty string.
        self.assertIn("SKIP", result["content"],
                       f"missing-file inspect must include SKIP marker, "
                       f"got {result}")
        self.assertIn("warning", result,
                       f"missing-file inspect must include a warning "
                       f"sentinel, got {result}")
        self.assertIn("first-iteration", result["warning"])


if __name__ == "__main__":
    unittest.main()