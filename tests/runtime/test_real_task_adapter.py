"""Real-task adapter: a real failing test, a declared patch, a real measurement.

The project under test here is a two-file temporary project with a genuinely failing
test, so the adapter's mechanism (shadow repository, patch application, test-runner
parsing, baseline evidence) is exercised end to end without touching any real repo.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from partner.application.real_task_adapter import (
    RealTaskError, apply_patch_text, load_task, run_test_target, stage_shadow_repo,
)

MODULE_V1 = """def answer():
    return 1
"""
MODULE_V2 = """def answer():
    return 2
"""


def _project(tmp_path: Path) -> tuple[Path, Path]:
    """A real (small) project with a real failing test, plus a task definition."""
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (proj / "pkg" / "mod.py").write_text(MODULE_V1, encoding="utf-8")
    (proj / "tests").mkdir()
    (proj / "tests" / "test_mod.py").write_text(
        "from pkg.mod import answer\n\n\n"
        "def test_answer_is_two():\n    assert answer() == 2\n\n\n"
        "def test_module_imports():\n    assert answer() in (1, 2)\n",
        encoding="utf-8")
    tasks = tmp_path / "taskroot" / "benchmarks" / "real_tasks" / "demo"
    tasks.mkdir(parents=True)
    (tasks / "patch.diff").write_text("<<<REPLACE\n" + MODULE_V2, encoding="utf-8")
    (tasks / "task.json").write_text(json.dumps({
        "task_id": "demo", "repo_root": str(proj), "target_module": "pkg/mod.py",
        "test_target": ["tests/test_mod.py"], "patch_file": "patch.diff",
        "timeout_seconds": 120}), encoding="utf-8")
    return tmp_path / "taskroot", proj


def _run_arm(tmp_path: Path, task: dict, arm: str, source: str):
    sandbox = tmp_path / f"sandbox_{arm}"
    sandbox.mkdir(parents=True, exist_ok=True)
    shadow = stage_shadow_repo(task["repo_root"], sandbox, task["target_module"], source)
    return run_test_target(task, overlay=shadow, sandbox=sandbox)


def test_the_control_arm_measures_the_real_failure_and_the_patch_fixes_it(tmp_path):
    task_root, proj = _project(tmp_path)
    task = load_task(task_root, "demo")
    patch = (task_root / "benchmarks" / "real_tasks" / "demo" / "patch.diff").read_text(
        encoding="utf-8")

    control = _run_arm(tmp_path, task, "control", (proj / "pkg" / "mod.py").read_text(
        encoding="utf-8"))
    candidate = _run_arm(tmp_path, task, "candidate", apply_patch_text(
        (proj / "pkg" / "mod.py").read_text(encoding="utf-8"), patch))

    assert control["tests_failed"] == 1 and control["tests_passed"] == 1
    assert control["exit_code"] != 0
    assert candidate["tests_failed"] == 0 and candidate["tests_passed"] == 2
    assert candidate["exit_code"] == 0
    assert candidate["tests_passed"] - control["tests_passed"] == 1


def test_the_shadow_repo_never_writes_to_the_real_project(tmp_path):
    task_root, proj = _project(tmp_path)
    before = hashlib.sha256((proj / "pkg" / "mod.py").read_bytes()).hexdigest()
    task = load_task(task_root, "demo")
    _run_arm(tmp_path, task, "control", MODULE_V2)
    after = hashlib.sha256((proj / "pkg" / "mod.py").read_bytes()).hexdigest()
    assert before == after, "the measured project must not be modified"
    # the shadow is a symlink farm with exactly one real file
    shadow = tmp_path / "sandbox_control" / "repo"
    assert (shadow / "tests").is_symlink()
    assert not (shadow / "pkg" / "mod.py").is_symlink()
    assert (shadow / "pkg" / "mod.py").read_text(encoding="utf-8") == MODULE_V2


def test_load_task_refuses_an_incomplete_definition(tmp_path):
    tasks = tmp_path / "benchmarks" / "real_tasks" / "broken"
    tasks.mkdir(parents=True)
    (tasks / "task.json").write_text(json.dumps({"task_id": "broken"}), encoding="utf-8")
    with pytest.raises(RealTaskError):
        load_task(tmp_path, "broken")
    with pytest.raises(RealTaskError):
        load_task(tmp_path, "does_not_exist")


def test_apply_patch_refuses_an_unmatched_or_ambiguous_block():
    with pytest.raises(RealTaskError):
        apply_patch_text("real source", "<<<FIND\nnot in source\n>>>REPL\nother\n")
    with pytest.raises(RealTaskError):
        apply_patch_text("dup dup", "<<<FIND\ndup\n>>>REPL\nX\n")
    with pytest.raises(RealTaskError):
        apply_patch_text("source", "not a patch format")


def test_find_replace_blocks_apply_in_order():
    out = apply_patch_text("alpha beta", "<<<FIND\nalpha\n>>>REPL\nALPHA\n"
                                           "<<<FIND\nbeta\n>>>REPL\nBETA\n")
    assert out == "ALPHA BETA"


def test_the_executor_reports_a_patch_that_does_not_apply(tmp_path):
    """A patch that cannot be applied is a failed receipt, never a silent control run."""
    from types import SimpleNamespace

    from partner.application.real_task_adapter import RealTaskExecutor

    task_root, proj = _project(tmp_path)
    bad = task_root / "benchmarks" / "real_tasks" / "demo" / "patch.diff"
    bad.write_text("<<<FIND\nthis text is not in the module\n>>>REPL\nX\n", encoding="utf-8")
    executor = RealTaskExecutor()
    bet = SimpleNamespace(bet_id="bet_demo", revision=1, selected_action="patch_demo")
    candidate = SimpleNamespace(candidate_id="patch_demo", params={
        "task_id": "demo", "patch_file": "patch.diff", "project_root": str(task_root),
        "arm": "candidate"})
    receipt = executor.execute(bet=bet, candidate=candidate, workspace=str(tmp_path / "ws"))
    assert receipt.status == "failed" and receipt.exit_code == 2
    assert "does not match the module" in receipt.failure_reason or "RealTaskError" in receipt.failure_reason


def test_siblings_inside_the_target_directory_are_linked_too(tmp_path):
    """Regression: the shadow must not hide the other modules of the target's package.

    An earlier version linked only the ancestors of the target path, so ``pkg/`` kept
    exactly one file.  A test that imported any sibling module then failed with
    ModuleNotFoundError -- a failure that had nothing to do with the candidate.
    """
    task_root, proj = _project(tmp_path)
    sibling = proj / "pkg" / "sibling.py"
    sibling.write_text("VALUE = 42\n", encoding="utf-8")
    task = load_task(task_root, "demo")
    shadow = stage_shadow_repo(str(proj), tmp_path / "sb", "pkg/mod.py", MODULE_V2)
    assert (shadow / "pkg" / "sibling.py").is_symlink()
    assert (shadow / "pkg" / "__init__.py").is_symlink()
    assert not (shadow / "pkg" / "mod.py").is_symlink()
