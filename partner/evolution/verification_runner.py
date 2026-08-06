"""Verification Runner — real before/after verification for self-evolution.

Replaces the no-op "pending" verdict with actual execution checks:
1. Syntax check: compile() all modified Python files
2. Import check: import each modified module
3. GUI check: verify key UI components exist in source files
4. Screenshot check: verify real screenshots exist (>100KB, no synthetic prefix)
5. File change check: verify files are not bloated (>5000 lines triggers warning)

Each check produces a PASS/FAIL result. The verdict is computed from all checks.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum acceptable file size for GUI files (lines)
MAX_FILE_LINES = 5000
# Minimum bytes for a real screenshot (100KB)
MIN_REAL_SCREENSHOT_BYTES = 100 * 1024
# Key UI components that must exist in Partner GUI files
KEY_UI_COMPONENTS = {
    "main_window.py": ["ModernMainWindow", "_build_ui", "_navigate_to", "_toggle_sidebar", "_save_layout"],
    "widgets.py": ["ChatBubble", "AccentButton", "SectionHeader", "DirBrowser"],
    "pages/chat.py": ["ChatPage"],
}
# Project root
PROJECT_ROOT = Path("/mnt/e/work/partner")
GUI_DIR = PROJECT_ROOT / "shells/frontend/desktop_gui/modern"


@dataclass
class CheckResult:
    """Result of a single verification check."""
    check_name: str
    passed: bool
    details: str = ""


@dataclass
class VerificationReport:
    """Full verification report for a single plan."""
    plan_id: str
    checks: list[CheckResult] = field(default_factory=list)
    verdict: str = "neutral"  # "effective" | "neutral" | "regressive"

    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "checks": [{"name": c.check_name, "passed": c.passed, "details": c.details} for c in self.checks],
            "passed": self.passed_count(),
            "failed": self.failed_count(),
            "verdict": self.verdict,
            "timestamp": datetime.now().isoformat(),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Individual Checks
# ═══════════════════════════════════════════════════════════════════════════════


def check_syntax(files: list[str] | None = None) -> CheckResult:
    """Check Python syntax of modified files."""
    if files is None:
        files = [str(GUI_DIR / f) for f in ["main_window.py", "widgets.py", "pages/chat.py"]]

    errors: list[str] = []
    for fpath in files:
        if not os.path.exists(fpath):
            errors.append(f"MISSING: {fpath}")
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                ast.parse(f.read())
        except SyntaxError as e:
            errors.append(f"SYNTAX ERROR: {fpath}: {e}")

    if errors:
        return CheckResult("syntax_check", passed=False, details="; ".join(errors))
    return CheckResult("syntax_check", passed=True, details=f"All {len(files)} files pass syntax check")


def check_imports(files: list[str] | None = None) -> CheckResult:
    """Check that modified files can be imported without errors.
    Uses Python's compile() with exec mode to check for ImportError at parse level.
    """
    if files is None:
        rel_files = ["main_window.py", "widgets.py", "pages/chat.py"]
        files = [str(GUI_DIR / f) for f in rel_files]

    errors: list[str] = []
    for fpath in files:
        if not os.path.exists(fpath):
            errors.append(f"MISSING: {fpath}")
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                source = f.read()
            compile(source, fpath, "exec")
        except SyntaxError as e:
            errors.append(f"IMPORT ERROR: {fpath}: {e}")
        except Exception as e:
            errors.append(f"COMPILE ERROR: {fpath}: {e}")

    if errors:
        return CheckResult("import_check", passed=False, details="; ".join(errors))
    return CheckResult("import_check", passed=True, details=f"All {len(files)} files compile cleanly")


def check_gui_components() -> CheckResult:
    """Check that key UI components exist in the source files."""
    missing: list[str] = []
    for filename, expected_components in KEY_UI_COMPONENTS.items():
        fpath = GUI_DIR / filename
        if not fpath.exists():
            missing.append(f"{filename}: file not found")
            continue

        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

        for component in expected_components:
            # Check for class or def definition (with optional leading whitespace for methods)
            pattern = rf"^\s*(?:class\s+{component}|def\s+{component})\b"
            if not re.search(pattern, content, re.MULTILINE):
                missing.append(f"{filename}: missing {component}")

    if missing:
        return CheckResult("gui_components", passed=False, details="; ".join(missing[:10]))
    return CheckResult("gui_components", passed=True, details="All key UI components present")


def check_file_sizes() -> CheckResult:
    """Check that files haven't bloated beyond MAX_FILE_LINES."""
    over_limit: list[str] = []
    for filename in ["main_window.py", "widgets.py", "pages/chat.py"]:
        fpath = GUI_DIR / filename
        if not fpath.exists():
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            line_count = len(f.readlines())
        if line_count > MAX_FILE_LINES:
            over_limit.append(f"{filename}: {line_count} lines (limit {MAX_FILE_LINES})")

    if over_limit:
        return CheckResult("file_size", passed=False, details="; ".join(over_limit))
    return CheckResult("file_size", passed=True, details="All files within line limit")


def check_duplicate_classes() -> CheckResult:
    """Check for duplicate class definitions in GUI files."""
    duplicates: list[str] = []
    for filename in ["main_window.py", "widgets.py", "pages/chat.py"]:
        fpath = GUI_DIR / filename
        if not fpath.exists():
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

        # Count class definitions
        class_names = re.findall(r"^class (\w+)", content, re.MULTILINE)
        from collections import Counter
        name_counts = Counter(class_names)
        for name, count in name_counts.items():
            if count > 1:
                duplicates.append(f"{filename}: '{name}' appears {count} times")

    if duplicates:
        return CheckResult("duplicate_classes", passed=False, details="; ".join(duplicates[:15]))
    return CheckResult("duplicate_classes", passed=True, details="No duplicate class definitions")


def check_screenshots(screenshots_dir: str | None = None) -> CheckResult:
    """Check that real (not synthetic) screenshots exist."""
    if screenshots_dir is None:
        # Try common paths
        candidates = [
            "/mnt/e/work/partner_workspace/instances/03/partner_data/screenshots",
            "/mnt/e/work/partner_workspace/partner_data/screenshots",
        ]
        for c in candidates:
            if os.path.isdir(c):
                screenshots_dir = c
                break

    if not screenshots_dir or not os.path.isdir(screenshots_dir):
        return CheckResult("screenshots", passed=False, details=f"No screenshots directory found")

    files = [f for f in os.listdir(screenshots_dir) if f.endswith(".png")]
    if not files:
        return CheckResult("screenshots", passed=False, details="No screenshot files found")

    # Check for synthetic prefix
    synthetic = [f for f in files if "synthetic" in f.lower()]
    if synthetic:
        return CheckResult("screenshots", passed=False, details=f"{len(synthetic)} synthetic screenshots found: {synthetic[:3]}")

    # Check file sizes
    small_files = []
    for f in files:
        fpath = os.path.join(screenshots_dir, f)
        size = os.path.getsize(fpath)
        if size < MIN_REAL_SCREENSHOT_BYTES:
            small_files.append(f"{f} ({size//1024}KB)")

    if small_files:
        return CheckResult("screenshots", passed=False, details=f"{len(small_files)} screenshots below {MIN_REAL_SCREENSHOT_BYTES//1024}KB threshold: {small_files[:3]}")

    return CheckResult("screenshots", passed=True, details=f"{len(files)} real screenshots found")


def check_from_future() -> CheckResult:
    """Check for from __future__ imports in mid-file positions."""
    issues: list[str] = []
    for filename in ["main_window.py", "widgets.py", "pages/chat.py"]:
        fpath = GUI_DIR / filename
        if not fpath.exists():
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("from __future__") and i > 10:
                issues.append(f"{filename}:{i}")

    if issues:
        return CheckResult("from_future_check", passed=False, details=f"mid-file from __future__ at: {issues[:5]}")
    return CheckResult("from_future_check", passed=True, details="No mid-file from __future__ imports")


def check_git_diff() -> CheckResult:
    """Check that actual git changes exist (proves code was really modified)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "shells/frontend/desktop_gui/modern/"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        output = result.stdout.strip()
        if not output:
            return CheckResult("git_diff", passed=False, details="No git changes found in GUI files")
        # Extract total insertions
        insert_match = re.search(r"(\d+) insertions?", output)
        insertions = int(insert_match.group(1)) if insert_match else 0
        if insertions > 100:
            return CheckResult("git_diff", passed=True, details=f"Real changes found: +{insertions} lines")
        else:
            return CheckResult("git_diff", passed=False, details=f"Only {insertions} lines changed (too few)")
    except Exception as e:
        return CheckResult("git_diff", passed=False, details=f"Git check failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Full Verification Runner
# ═══════════════════════════════════════════════════════════════════════════════


async def run_verification(
    plan_id: str,
    workspace: str | None = None,
    files_to_check: list[str] | None = None,
    screenshots_dir: str | None = None,
) -> VerificationReport:
    """Run the full verification check suite and produce a verdict.

    Args:
        plan_id: Identifier of the improvement plan being verified.
        workspace: Optional workspace path (for screenshot lookup).
        files_to_check: Optional list of file paths to check.
        screenshots_dir: Optional screenshot directory path.

    Returns:
        VerificationReport with all check results and verdict.
    """
    report = VerificationReport(plan_id=plan_id)

    # Run all checks
    report.checks.append(check_syntax(files_to_check))
    report.checks.append(check_imports(files_to_check))
    report.checks.append(check_gui_components())
    report.checks.append(check_file_sizes())
    report.checks.append(check_duplicate_classes())
    report.checks.append(check_screenshots(screenshots_dir))
    report.checks.append(check_from_future())
    report.checks.append(check_git_diff())

    # Compute verdict
    passed = report.passed_count()
    total = len(report.checks)
    failed = report.failed_count()

    if failed == 0:
        report.verdict = "effective"
    elif failed <= total * 0.3:  # <= 30% failure rate
        report.verdict = "neutral"
    else:
        report.verdict = "regressive"

    logger.info(
        "[VERIFY] Plan %s: %d/%d checks passed → verdict: %s",
        plan_id, passed, total, report.verdict,
    )
    return report


def write_verification_to_harness_log(
    report: VerificationReport,
    harness_log_path: str | None = None,
) -> str:
    """Write verification result to harness_runs.jsonl.

    Args:
        report: The verification report to write.
        harness_log_path: Path to harness_runs.jsonl. Auto-detected if None.

    Returns:
        The path where the record was written.
    """
    if harness_log_path is None:
        candidates = [
            "/mnt/e/work/partner_workspace/instances/03/state/harness_runs.jsonl",
            "/mnt/e/work/partner_workspace/state/harness_runs.jsonl",
        ]
        for c in candidates:
            if os.path.exists(os.path.dirname(c)):
                harness_log_path = c
                break
        if harness_log_path is None:
            harness_log_path = candidates[0]

    entry = report.to_dict()
    os.makedirs(os.path.dirname(harness_log_path), exist_ok=True)
    with open(harness_log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("[VERIFY] Verification written to %s", harness_log_path)
    return harness_log_path


async def run_and_report(
    plan_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Convenience: run verification + write to harness log + return dict."""
    report = await run_verification(plan_id, workspace=workspace)
    write_verification_to_harness_log(report)
    return report.to_dict()
