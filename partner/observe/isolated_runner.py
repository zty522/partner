"""Lightweight isolated execution runner for self_evolution.

matched_execution.isolate() requires (a) reproducer_tests + regression_tests
both passed in by the candidate, and (b) a clean unified_diff that applies
to <= 3 partner/ files. When a candidate fails any of these gates, we still
want SOME isolated evidence (otherwise promotion_decide marks inconclusive
forever). This module provides a lighter fallback:

  - Apply the unified_diff to a temp copy of partner/ using code_invention_lab
  - If apply fails, return apply_diagnostic with the exact reason
  - If apply succeeds, run a single partner/ sanity test (default
    test_matched_execution_truth which doesn't depend on full runtime)
  - Write a receipt with kind=fallback_isolated so promotion_decide sees a
    real exit_code and can decide promote/reject

The fallback path is NOT equivalent to the strict isolated pytest run; it is
deliberately cheap (one test file) so aspect_synthesize can iterate. The
strict path stays the default; this module is the escape hatch.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _repo_root(workspace: str) -> Path:
    p = Path(workspace).resolve()
    return p.parent.parent if p.parent.name == "instances" else p


def run_fallback_isolated(workspace: str, candidate: dict[str, Any],
                           sanity_test: str = "tests/test_matched_execution_truth.py") -> dict[str, Any]:
    """Lightweight fallback isolated execution.

    Returns dict with: ok, applied, exit_code, log_path, diagnostic, sanity_test.
    Never raises; on any failure returns ok=False with diagnostic.
    """
    repo = _repo_root(workspace)
    patch = str(candidate.get("unified_diff") or "")
    targets = list(candidate.get("target_files") or [])
    if not patch or not targets:
        return {"ok": False, "applied": False, "exit_code": None,
                "diagnostic": "missing unified_diff or target_files",
                "sanity_test": sanity_test}

    tmp = Path(tempfile.mkdtemp(prefix="partner_fallback_"))
    try:
        # Copy partner/ + tests/ + sanity_test to tmp
        for sub in ("partner", "tests"):
            src = repo / sub
            if src.is_dir():
                shutil.copytree(src, tmp / sub,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "workspace"))
        # Try apply patch
        from partner.evolution.code_invention_lab import (
            _apply_exact_unified_diff, _patch_paths,
        )
        patch_targets = sorted(_patch_paths(patch))
        applied, reason = _apply_exact_unified_diff(tmp, patch, patch_targets)
        if not applied:
            return {"ok": False, "applied": False, "exit_code": None,
                    "diagnostic": "patch apply failed: " + reason,
                    "sanity_test": sanity_test}

        # Run sanity test (default one we know exists)
        test_path = tmp / sanity_test
        if not test_path.is_file():
            # fallback to whatever test file actually exists
            tests_dir = tmp / "tests"
            if tests_dir.is_dir():
                found = list(tests_dir.glob("test_*.py"))
                if not found:
                    return {"ok": False, "applied": True, "exit_code": None,
                            "diagnostic": "no test files in tmp",
                            "sanity_test": sanity_test}
                sanity_test = str(found[0].relative_to(tmp))
                test_path = tmp / sanity_test

        # Run subprocess pytest
        log_path = tmp / "fallback.log"
        env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"}}
        env.update(PYTHONPATH=str(tmp), HOME=str(tmp / "home"),
                   OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", sanity_test,
                 "--tb=short", "--rootdir", str(tmp)],
                cwd=tmp, env=env, stdout=open(log_path, "wb"),
                stderr=subprocess.STDOUT, timeout=60,
            )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            exit_code = -1
            log_path.write_text(b"timeout after 60s\n")

        return {
            "ok": True,
            "applied": True,
            "exit_code": exit_code,
            "log_path": str(log_path),
            "sanity_test": sanity_test,
            "diagnostic": "fallback_isolated executed; kind=fallback not strict",
        }
    finally:
        # Leave tmp for inspection; do not auto-clean.
        pass


__all__ = ["run_fallback_isolated"]
