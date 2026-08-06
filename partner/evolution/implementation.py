"""Implementation — risk-aware plan application with backups and rollback support.

Part of the 5-step self-evolution cycle. Consumes :class:`ImprovementPlan` dicts
produced by :mod:`partner.evolution.plan_formation` and applies them to Partner's
actual files with risk-based guardrails, backups, and full rollback capability.

Risk levels
-----------
- **low** (config): YAML/YAML-like files under ``partner/data/`` or ``partner/config/``,
  and prompt files under ``partner/prompts/`` — auto-apply immediately, no approval
  needed. Backups are still created.
- **medium** (prompt): Prompt template modifications — auto-apply with detailed logging.
- **high** (code): Python source code modifications — never applied directly. A unified
  diff is generated and saved to a ``plans/`` directory under the workspace. The caller
  must arrange for user approval (B-scheme) and then call :meth:`Implementation.apply_diff`.

Implementation flow
-------------------
1. Backup the original file (copy to ``<filepath>.backup``).
2. Apply the change (or generate diff for code changes).
3. Log the change to the ``change_log`` table in the learning database.
4. Return a result summary.

Rollback restores the original file from its ``.backup`` copy and marks the
change log entry as rolled back.

Usage::

    from partner.evolution.implementation import Implementation, ImplementationResult

    plan = {
        "id": "plan_001",
        "target_module": "planner",
        "change_type": "config_change",
        "function_name": "prompts/reflect.txt",
        "new_code": "...",
        "description": "Update reflect prompt",
        "risk_level": "low",
    }

    result = await Implementation.apply_plan(plan)
    if result.success:
        print(f"Applied: {result.file_path}")
    else:
        print(f"Failed: {result.error_message}")
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..utils.workspace import get_learning_db_path, get_partner_data_dir

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# Code root — where the partner Python package lives
CODE_ROOT = Path(__file__).resolve().parent.parent.parent  # /mnt/e/work/partner/partner
# Project root — where the partner project root is (contains shells/, partner/, configs/)
PROJECT_ROOT = CODE_ROOT.parent  # /mnt/e/work/partner

# Risk levels (mirrored from plan_formation for independence)
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
VALID_RISK_LEVELS = (RISK_LOW, RISK_MEDIUM, RISK_HIGH)

# Change types
CONFIG_CHANGE = "config_change"
PROMPT_CHANGE = "prompt_change"
MODIFY_FUNCTION = "modify_function"
ADD_FEATURE = "add_feature"

# Risk → change-type mapping (changes are classified for safety)
_CHANGE_TYPE_RISK: dict[str, str] = {
    CONFIG_CHANGE: RISK_LOW,
    PROMPT_CHANGE: RISK_MEDIUM,
    MODIFY_FUNCTION: RISK_HIGH,
    ADD_FEATURE: RISK_HIGH,
}

# File extensions that count as "code"
_CODE_EXTENSIONS = {".py", ".pyx", ".pyi"}

# Plans directory name (for storing code diffs)
_PLANS_DIR = "plans"

# Backup suffix
_BACKUP_SUFFIX = ".backup"

# ── Change log schema ─────────────────────────────────────────────────────────

_CHANGE_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    target_module TEXT NOT NULL DEFAULT '',
    function_name TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    backup_path TEXT NOT NULL DEFAULT '',
    diff_path TEXT DEFAULT '',
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'applied',
    -- 'applied', 'rolled_back', 'pending_approval', 'failed'
    rolled_back_at TEXT DEFAULT NULL,
    error_message TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_change_log_plan_id ON change_log(plan_id);
CREATE INDEX IF NOT EXISTS idx_change_log_status ON change_log(status);
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Data Types
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ImplementationResult:
    """Result of applying a single improvement plan.

    Attributes:
        success: Whether the plan was applied (or diff generated) successfully.
        plan_id: The ID of the plan that was processed.
        change_type: The type of change (config_change, prompt_change, etc.).
        risk_level: The risk level (low, medium, high).
        file_path: Path to the file that was modified (or would be modified).
        backup_path: Path to the backup file (empty if no backup was made).
        diff_path: Path to the saved diff file (for code changes only).
        change_log_id: ID of the entry in the change_log table.
        error_message: Human-readable error description if success is False.
        needs_approval: Whether this change requires human approval before
            the actual file modification (True for high-risk code changes).
    """

    success: bool = False
    plan_id: str = ""
    change_type: str = ""
    risk_level: str = ""
    file_path: str = ""
    backup_path: str = ""
    diff_path: str = ""
    change_log_id: int = 0
    error_message: str = ""
    needs_approval: bool = False


@dataclass
class RollbackResult:
    """Result of rolling back a previously applied plan.

    Attributes:
        success: Whether the rollback succeeded.
        plan_id: The ID of the plan that was rolled back.
        change_log_id: The change_log entry ID.
        file_path: Path to the file that was restored.
        error_message: Error details if rollback failed.
    """

    success: bool = False
    plan_id: str = ""
    change_log_id: int = 0
    file_path: str = ""
    error_message: str = ""


@dataclass
class ChangeLogEntry:
    """A single entry from the change_log table.

    Attributes:
        id: Primary key.
        plan_id: External plan identifier.
        change_type: Type of change (config, prompt, code).
        risk_level: Risk level (low, medium, high).
        target_module: Partner module targeted.
        function_name: Function/config key/prompt file targeted.
        file_path: Absolute path to the file changed.
        backup_path: Absolute path to the backup file.
        diff_path: Absolute path to the saved diff (code changes only).
        description: Human-readable change description.
        status: Current status — 'applied', 'rolled_back', 'pending_approval', 'failed'.
        rolled_back_at: Timestamp when rolled back (None if never rolled back).
        error_message: Error message if status is 'failed'.
        created_at: Timestamp when the change was first applied.
    """

    id: int = 0
    plan_id: str = ""
    change_type: str = ""
    risk_level: str = ""
    target_module: str = ""
    function_name: str = ""
    file_path: str = ""
    backup_path: str = ""
    diff_path: str = ""
    description: str = ""
    status: str = "applied"
    rolled_back_at: str | None = None
    error_message: str = ""
    created_at: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Database helpers
# ═══════════════════════════════════════════════════════════════════════════════

_impl_db_local = threading.local()


def _get_db(workspace: str | None = None) -> sqlite3.Connection:
    """Get a thread-local connection to the learning database.

    Creates the change_log table if it does not already exist.

    Args:
        workspace: Optional workspace path override. If None, the default
            workspace resolution from :func:`get_learning_db_path` is used.

    Returns:
        An open ``sqlite3.Connection`` with ``row_factory`` set to ``sqlite3.Row``.
    """
    if not hasattr(_impl_db_local, "conn") or _impl_db_local.conn is None:
        db_path = get_learning_db_path(workspace)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_CHANGE_LOG_SCHEMA)
        conn.commit()
        _impl_db_local.conn = conn
    return _impl_db_local.conn


def _close_db() -> None:
    """Close the thread-local database connection if open."""
    if hasattr(_impl_db_local, "conn") and _impl_db_local.conn is not None:
        try:
            _impl_db_local.conn.close()
        except Exception:
            pass
        _impl_db_local.conn = None


# ═══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _resolve_file_path(plan: dict[str, Any]) -> str:
    """Resolve the absolute file path that a plan intends to modify.

    The resolution strategy depends on ``change_type`` and ``function_name``:

    - **config_change / prompt_change**: ``function_name`` may be a relative
      path under the code root (e.g. ``"prompts/reflect.txt"``) or an absolute
      path.  If relative, it is resolved against ``CODE_ROOT``.
    - **modify_function / add_feature**: ``function_name`` is a dotted Python
      symbol path (e.g. ``"planner.batch_planner.BatchPlanner"``).  The
      corresponding ``.py`` file is located by converting dots to path
      separators under ``CODE_ROOT``.

    Args:
        plan: An improvement plan dictionary with ``change_type`` and
            ``function_name``.

    Returns:
        An absolute file path string, or an empty string if the path cannot
        be resolved.
    """
    change_type = plan.get("change_type", "")
    function_name = plan.get("function_name", "")

    if not function_name:
        # Fallback: try target_module as a relative path
        target_module = plan.get("target_module", "")
        if target_module:
            for root in (PROJECT_ROOT, CODE_ROOT):
                candidate = os.path.join(str(root), target_module.lstrip("/"))
                if os.path.exists(candidate):
                    return candidate
                # Try adding .py
                candidate_py = candidate + ".py" if not candidate.endswith(".py") else candidate
                if os.path.exists(candidate_py):
                    return candidate_py
        return ""

    # If already absolute, use as-is
    if os.path.isabs(function_name):
        return function_name

    # For config/prompt changes the function_name is often a relative path
    if change_type in (CONFIG_CHANGE, PROMPT_CHANGE):
        candidate = os.path.join(str(CODE_ROOT), function_name)
        if os.path.exists(candidate) or os.path.exists(os.path.dirname(candidate)):
            return candidate
        # Try partner/data as well for config files
        candidate2 = os.path.join(str(CODE_ROOT), "data", function_name)
        if os.path.exists(candidate2) or os.path.exists(os.path.dirname(candidate2)):
            return candidate2
        # Fallback: just use the relative path against CODE_ROOT
        return candidate

    # For code changes, resolve dotted symbol to file path
    if change_type in (MODIFY_FUNCTION, ADD_FEATURE):
        # Remove the last component (function/class name) to get module path
        parts = function_name.split(".")
        if len(parts) > 1:
            module_path = os.path.join(str(CODE_ROOT), *parts[:-1]) + ".py"
            if os.path.exists(module_path):
                return module_path
        # Fallback: just use the full dotted path as relative file
        py_path = os.path.join(str(CODE_ROOT), function_name.replace(".", "/")) + ".py"
        if os.path.exists(py_path):
            return py_path
        # Try without adding .py (if function_name already includes .py)
        direct = os.path.join(str(CODE_ROOT), function_name)
        if os.path.exists(direct):
            return direct
        # Return the best guess even if it doesn't exist yet
        if os.path.exists(py_path):
            return py_path
        # Check if it's nested under CODE_ROOT/target_module/
        target_module = plan.get("target_module", "")
        if target_module:
            under_module = os.path.join(str(CODE_ROOT), target_module, function_name.replace(".", "/")) + ".py"
            if os.path.exists(under_module):
                return under_module
        # Check against PROJECT_ROOT (for shells/frontend/... paths)
        project_candidate = os.path.join(str(PROJECT_ROOT), function_name.replace(".", "/"))
        if os.path.exists(project_candidate):
            return project_candidate
        project_candidate_py = project_candidate + ".py" if not project_candidate.endswith(".py") else project_candidate
        if os.path.exists(project_candidate_py):
            return project_candidate_py
        # Final fallback: try target_module as direct relative path against PROJECT_ROOT
        if target_module:
            for root in (PROJECT_ROOT, CODE_ROOT):
                tm_candidate = os.path.join(str(root), target_module.lstrip("/"))
                if os.path.exists(tm_candidate):
                    return tm_candidate
        logger.debug("[IMPL] resolved path %s does not exist — marking as unresolvable", py_path)
        return ""

    return ""


def _infer_risk_level(change_type: str) -> str:
    """Return the default risk level for a given change type.

    Args:
        change_type: One of ``"config_change"``, ``"prompt_change"``,
            ``"modify_function"``, ``"add_feature"``.

    Returns:
        ``"low"``, ``"medium"``, or ``"high"``.
    """
    return _CHANGE_TYPE_RISK.get(change_type, RISK_HIGH)


def _get_plans_dir(workspace: str | None = None) -> str:
    """Get the path to the plans directory (for storing code diffs).

    The plans directory lives under the workspace's ``partner_data/`` tree,
    inside a folder named ``_PLANS_DIR``.

    Args:
        workspace: Optional workspace path override.

    Returns:
        Absolute path to the plans directory.
    """
    partner_data = get_partner_data_dir(workspace)
    plans_dir = os.path.join(partner_data, _PLANS_DIR)
    os.makedirs(plans_dir, exist_ok=True)
    return plans_dir


def _create_backup(file_path: str) -> str:
    """Create a ``.backup`` copy of the file at *file_path*.

    The backup is saved as ``<file_path>.backup`` alongside the original.
    If a backup already exists, it is NOT overwritten — the existing backup
    is returned.

    Args:
        file_path: Absolute path to the file to back up.

    Returns:
        The path to the backup file (existing or newly created).

    Raises:
        FileNotFoundError: If *file_path* does not exist.
    """
    backup_path = file_path + _BACKUP_SUFFIX
    if os.path.exists(backup_path):
        logger.debug("Backup already exists at %s — reusing", backup_path)
        return backup_path
    shutil.copy2(file_path, backup_path)
    logger.info("Created backup: %s → %s", file_path, backup_path)
    return backup_path


def _generate_diff(original_path: str, new_content: str, plan_id: str) -> str:
    """Generate a unified diff between the original file and *new_content*.

    Args:
        original_path: Path to the original file on disk.
        new_content: The proposed new content.
        plan_id: Plan ID for naming the diff file.

    Returns:
        The unified diff string.
    """
    with open(original_path, "r", encoding="utf-8") as f:
        original_lines = f.readlines()

    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        original_lines,
        new_lines,
        fromfile=original_path,
        tofile=f"{original_path} (proposed)",
        lineterm="\n",
    )
    return "".join(diff)


def _save_diff(original_path: str, new_content: str, plan_id: str, workspace: str | None = None) -> str:
    """Generate and save a unified diff to the plans directory.

    Args:
        original_path: Path to the original file.
        new_content: Proposed new content.
        plan_id: Plan ID for naming.
        workspace: Optional workspace override.

    Returns:
        Absolute path to the saved diff file.
    """
    plans_dir = _get_plans_dir(workspace)
    diff_text = _generate_diff(original_path, new_content, plan_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    diff_filename = f"{plan_id}_{timestamp}.diff"
    diff_path = os.path.join(plans_dir, diff_filename)

    with open(diff_path, "w", encoding="utf-8") as f:
        f.write(diff_text)

    logger.info("Saved diff: %s", diff_path)
    return diff_path


def _write_file_content(file_path: str, content: str) -> None:
    """Write *content* to *file_path*, creating parent directories if needed.

    Args:
        file_path: Absolute path to the file.
        content: Text content to write.

    Raises:
        OSError: If the file cannot be written.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


def _append_to_file(file_path: str, snippet: str) -> None:
    """Merge *snippet* into an existing file using intelligent CodeMerger.

    Replaces blind appending with structural analysis:
    - Detects duplicate classes/functions and merges into existing ones
    - Strips mid-file __future__ imports
    - Fixes common import errors (QStringListModel from QtCore)
    - Tracks merge results for quality reporting

    Args:
        file_path: Absolute path to the file.
        snippet: Code snippet to merge.

    Raises:
        OSError: If the file cannot be written.
    """
    # Use CodeMerger for intelligent merge instead of blind append
    from partner.evolution.code_merger import CodeMerger
    merger = CodeMerger(file_path)
    result = merger.merge(snippet)

    if result.action == "skipped_duplicate":
        logger.info("[IMPL] CodeMerger skipped duplicate: %s", result.message)
    elif result.action == "merged_into_class":
        logger.info("[IMPL] CodeMerger merged into class '%s': %s", result.target_class, result.message)
    else:
        logger.info("[IMPL] CodeMerger appended: %s", result.message)


def _strip_future_imports(code: str) -> str:
    """Strip ``from __future__ import ...`` lines from generated code.

    These imports must appear at the top of a module.  Auto-evolution
    appends code at the end of a file, so any ``from __future__`` in
    the generated snippet would cause a SyntaxError.
    """
    import re as _re
    result = _re.sub(r'^from __future__ import .*$\\n?', '', code, flags=_re.MULTILINE)
    return result.strip()


def _insert_change_log(
    plan: dict[str, Any],
    file_path: str,
    backup_path: str,
    diff_path: str,
    status: str,
    error_message: str,
    workspace: str | None = None,
) -> int:
    """Insert a new entry into the ``change_log`` table.

    Args:
        plan: The improvement plan dict.
        file_path: Absolute path to the file that was (or would be) changed.
        backup_path: Path to the backup file.
        diff_path: Path to the saved diff (empty for non-code changes).
        status: One of ``'applied'``, ``'rolled_back'``, ``'pending_approval'``,
            ``'failed'``.
        error_message: Error details (empty string if successful).
        workspace: Optional workspace override.

    Returns:
        The row ID of the new change_log entry.
    """
    db = _get_db(workspace)
    cur = db.execute(
        """INSERT INTO change_log
           (plan_id, change_type, risk_level, target_module, function_name,
            file_path, backup_path, diff_path, description, status, error_message)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            plan.get("id", ""),
            plan.get("change_type", ""),
            plan.get("risk_level", RISK_LOW),
            plan.get("target_module", ""),
            plan.get("function_name", ""),
            file_path,
            backup_path,
            diff_path,
            plan.get("description", ""),
            status,
            error_message,
        ),
    )
    db.commit()
    return cur.lastrowid or 0


def _validate_plan(plan: dict[str, Any]) -> str | None:
    """Validate an improvement plan dictionary, returning an error string or None.

    Checks:
    - *plan* is a dict.
    - *id* is present and a non-empty string.
    - *change_type* is one of the recognised types.
    - *risk_level* is one of ``low``, ``medium``, ``high``.

    Args:
        plan: The improvement plan to validate.

    Returns:
        ``None`` if valid, or an error message string describing the issue.
    """
    if not isinstance(plan, dict):
        return "Plan must be a dictionary"

    if not plan.get("id"):
        return "Plan missing required field: 'id'"

    change_type = plan.get("change_type", "")
    if change_type not in (CONFIG_CHANGE, PROMPT_CHANGE, MODIFY_FUNCTION, ADD_FEATURE):
        return (
            f"Invalid change_type {change_type!r}. "
            f"Must be one of {CONFIG_CHANGE}, {PROMPT_CHANGE}, "
            f"{MODIFY_FUNCTION}, {ADD_FEATURE}."
        )

    risk_level = plan.get("risk_level", "")
    if risk_level not in VALID_RISK_LEVELS:
        inferred = _infer_risk_level(change_type)
        plan["risk_level"] = inferred

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════


class Implementation:
    """Risk-aware plan implementation with backups and rollback support.

    ``Implementation`` is the central class of this module.  It applies
    improvement plans to Partner's actual files, creating backups before
    any modification and logging all changes to the ``change_log`` table
    in the learning database.

    Risk-based behaviour
    --------------------
    - **low** (config/prompt files): Applied immediately. Backup is created.
    - **medium** (prompt template files): Applied immediately with detailed logging.
    - **high** (Python code): A unified diff is generated and saved to the
      ``plans/`` directory.  The file is NOT modified — the caller must
      arrange for user approval and then call :meth:`apply_diff` to
      finalise the change.

    Typical usage in the evolution cycle::

        from partner.evolution.implementation import Implementation

        # After plan formation, apply plans
        results = await Implementation.apply_plans(plans_list)

        for r in results:
            if r.needs_approval:
                print(f"Plan {r.plan_id} saved diff to {r.diff_path} — awaiting approval")
            elif r.success:
                print(f"Plan {r.plan_id} applied to {r.file_path}")
            else:
                print(f"Plan {r.plan_id} FAILED: {r.error_message}")
    """

    # ── Apply single plan (risk-aware, with approval gate for code) ──

    @staticmethod
    async def apply_plan(
        plan: dict[str, Any],
        workspace: str | None = None,
    ) -> ImplementationResult:
        """Apply a single improvement plan with risk-based guardrails.

        The method:

        1. Validates the plan dictionary.
        2. Resolves the target file path.
        3. For **low/medium** risk: creates a backup, writes the new content,
           logs the change.
        4. For **high** risk: creates a backup, generates a unified diff,
           saves it to the ``plans/`` directory, logs a ``pending_approval``
           entry.  The file is NOT modified.

        Args:
            plan: An improvement plan dictionary.  Expected keys:

                - ``id`` (str): Unique plan identifier.
                - ``change_type`` (str): One of ``config_change``,
                  ``prompt_change``, ``modify_function``, ``add_feature``.
                - ``risk_level`` (str, optional): One of ``low``, ``medium``,
                  ``high``.  Auto-inferred from ``change_type`` if absent.
                - ``target_module`` (str): Partner module name.
                - ``function_name`` (str): Specific function name, prompt file,
                  or config key.
                - ``new_code`` (str): The new content to write.
                - ``description`` (str): Human-readable change description.

            workspace: Optional workspace path for database and plans directory.
                If ``None``, uses the default workspace resolution.

        Returns:
            An :class:`ImplementationResult` describing the outcome.

        Raises:
            ValueError: If the plan is invalid.
        """
        return await Implementation._apply_plan(
            plan=plan, workspace=workspace, force=False,
        )

    @staticmethod
    async def force_apply(
        plan: dict[str, Any],
        workspace: str | None = None,
    ) -> ImplementationResult:
        """Apply a plan FORCEFULLY — NO approval gate for code changes.

        This is the auto-evolution version of apply_plan(). It behaves
        identically to apply_plan() for low/medium risk plans, but for
        high-risk (code) changes it DIRECTLY WRITES the file instead of
        generating a diff.  A backup is always created first.

        Used by SelfEvolveEngine's auto-implement → verify → decide loop.

        Args:
            plan: Same format as apply_plan().
            workspace: Optional workspace override.

        Returns:
            An ImplementationResult with needs_approval always False.
        """
        return await Implementation._apply_plan(
            plan=plan, workspace=workspace, force=True,
        )

    @staticmethod
    async def _apply_plan(
        plan: dict[str, Any],
        workspace: str | None = None,
        force: bool = False,
    ) -> ImplementationResult:
        start = _time.time()

        # 1. Validate
        error = _validate_plan(plan)
        if error:
            logger.warning("[IMPL] Plan validation failed: %s", error)
            return ImplementationResult(
                success=False,
                plan_id=plan.get("id", "unknown"),
                error_message=error,
            )

        plan_id = plan["id"]
        change_type = plan["change_type"]
        risk_level = plan.get("risk_level", _infer_risk_level(change_type))
        new_code = plan.get("new_code", "")
        description = plan.get("description", "")

        # 2. Resolve file path
        file_path = _resolve_file_path(plan)
        if not file_path:
            msg = f"Could not resolve file path for plan {plan_id}"
            logger.warning("[IMPL] %s", msg)
            _insert_change_log(plan, "", "", "", "failed", msg, workspace)
            return ImplementationResult(
                success=False,
                plan_id=plan_id,
                change_type=change_type,
                risk_level=risk_level,
                error_message=msg,
            )

        logger.info(
            "[IMPL] Applying plan %s: %s → %s (risk=%s)",
            plan_id, change_type, file_path, risk_level,
        )

        # 3. Apply based on risk level
        backup_path = ""
        diff_path = ""
        status = "applied"
        needs_approval = False

        try:
            if risk_level == RISK_HIGH:
                if force:
                    # Force mode: write code directly (for auto-evolution)
                    append_mode = plan.get("append_mode", False)

                    if not new_code or not new_code.strip():
                        # Empty content — skip to avoid wiping files
                        logger.info("[IMPL] Plan %s has empty new_code — skipping", plan_id)
                        status = "skipped"
                        needs_approval = False
                        _insert_change_log(plan, file_path, "", "", "skipped",
                                           "Empty new_code — skipped", workspace)
                        return ImplementationResult(
                            success=True,
                            plan_id=plan_id,
                            change_type=change_type,
                            risk_level=risk_level,
                            file_path=file_path or "",
                            error_message="Skipped: empty new_code",
                        )

                    if os.path.exists(file_path) and append_mode:
                        # Append snippet to existing file
                        backup_path = _create_backup(file_path)
                        _append_to_file(file_path, new_code)
                        logger.info(
                            "[IMPL] Force-appended to %s (plan %s, %d bytes)",
                            file_path, plan_id, len(new_code),
                        )
                    elif os.path.exists(file_path):
                        # Full rewrite of existing file
                        backup_path = _create_backup(file_path)
                        _write_file_content(file_path, new_code)
                        logger.info(
                            "[IMPL] Force-wrote %s (plan %s, %d bytes)",
                            file_path, plan_id, len(new_code),
                        )
                    else:
                        # New file
                        backup_path = ""
                        _write_file_content(file_path, new_code)
                        logger.info(
                            "[IMPL] Created %s (plan %s, %d bytes)",
                            file_path, plan_id, len(new_code),
                        )
                    status = "applied"
                    needs_approval = False
                    logger.info(
                        "[IMPL] Force-applied code change plan %s to %s (%d bytes)",
                        plan_id, file_path, len(new_code),
                    )
                else:
                    # Normal mode: generate diff, do NOT modify the file
                    if os.path.exists(file_path):
                        backup_path = _create_backup(file_path)
                        diff_path = _save_diff(file_path, new_code, plan_id, workspace)
                    else:
                        # New file: generate diff from empty
                        diff_path = _save_diff(
                            file_path,
                            new_code,
                            plan_id,
                            workspace,
                        )
                    status = "pending_approval"
                    needs_approval = True
                    logger.info(
                        "[IMPL] Code change plan %s — diff saved to %s (awaiting approval)",
                        plan_id, diff_path,
                    )

            elif risk_level in (RISK_LOW, RISK_MEDIUM):
                # Config/prompt change: apply directly
                if os.path.exists(file_path):
                    backup_path = _create_backup(file_path)
                if force and plan.get("append_mode", False) and os.path.exists(file_path):
                    # When force=True and append_mode=True, append snippet to existing file
                    _append_to_file(file_path, new_code)
                else:
                    _write_file_content(file_path, new_code)
                logger.info(
                    "[IMPL] Applied plan %s: wrote %d bytes to %s",
                    plan_id, len(new_code), file_path,
                )
            else:
                msg = f"Unknown risk level: {risk_level}"
                _insert_change_log(plan, file_path, "", "", "failed", msg, workspace)
                return ImplementationResult(
                    success=False,
                    plan_id=plan_id,
                    change_type=change_type,
                    risk_level=risk_level,
                    file_path=file_path,
                    error_message=msg,
                )

        except OSError as exc:
            msg = f"File operation failed: {exc}"
            logger.error("[IMPL] %s", msg)
            _insert_change_log(
                plan, file_path, backup_path, diff_path,
                "failed", msg, workspace,
            )
            return ImplementationResult(
                success=False,
                plan_id=plan_id,
                change_type=change_type,
                risk_level=risk_level,
                file_path=file_path,
                backup_path=backup_path,
                error_message=msg,
            )

        # 4. Log the change
        change_log_id = _insert_change_log(
            plan, file_path, backup_path, diff_path,
            status, "", workspace,
        )

        elapsed = _time.time() - start
        logger.info(
            "[IMPL] Plan %s applied in %.3fs (log_id=%d, status=%s)",
            plan_id, elapsed, change_log_id, status,
        )

        return ImplementationResult(
            success=True,
            plan_id=plan_id,
            change_type=change_type,
            risk_level=risk_level,
            file_path=file_path,
            backup_path=backup_path,
            diff_path=diff_path,
            change_log_id=change_log_id,
            needs_approval=needs_approval,
        )

    # ── Rollback ───────────────────────────────────────────────────────────

    @staticmethod
    async def rollback_plan(
        plan_id: str,
        workspace: str | None = None,
    ) -> RollbackResult:
        """Roll back a previously applied plan from its backup.

        Looks up the most recent 'applied' change_log entry for *plan_id*,
        restores the original file from backup, and marks the log as rolled_back.

        Args:
            plan_id: The plan ID to roll back.
            workspace: Optional workspace override.

        Returns:
            A RollbackResult with success status.
        """
        db = _get_db(workspace)
        row = db.execute(
            "SELECT id, file_path, backup_path FROM change_log "
            "WHERE plan_id = ? AND status = 'applied' "
            "ORDER BY id DESC LIMIT 1",
            (plan_id,),
        ).fetchone()

        if not row:
            return RollbackResult(
                success=False,
                plan_id=plan_id,
                error_message=f"No applied entry found for plan {plan_id}",
            )

        log_id = row["id"]
        file_path = row["file_path"]
        backup_path = row["backup_path"]

        if not backup_path or not os.path.exists(backup_path):
            return RollbackResult(
                success=False,
                plan_id=plan_id,
                change_log_id=log_id,
                file_path=file_path,
                error_message=f"Backup file not found: {backup_path}",
            )

        try:
            shutil.copy2(backup_path, file_path)
            db.execute(
                "UPDATE change_log SET status = 'rolled_back', "
                "rolled_back_at = datetime('now') WHERE id = ?",
                (log_id,),
            )
            db.commit()
            logger.info("[IMPL] Rolled back plan %s: %s ← %s", plan_id, file_path, backup_path)
            return RollbackResult(
                success=True,
                plan_id=plan_id,
                change_log_id=log_id,
                file_path=file_path,
            )
        except OSError as exc:
            msg = f"Rollback failed: {exc}"
            logger.error("[IMPL] %s", msg)
            return RollbackResult(
                success=False,
                plan_id=plan_id,
                change_log_id=log_id,
                file_path=file_path,
                error_message=msg,
            )

    # ── Apply multiple plans ──────────────────────────────────────────────

    @staticmethod
    async def apply_plans(
        plans: list[dict[str, Any]],
        workspace: str | None = None,
    ) -> list[ImplementationResult]:
        """Apply multiple improvement plans sequentially.

        Each plan is applied independently.  A failure in one plan does
        **not** cancel subsequent plans — all plans are attempted and
        their individual results returned.

        Args:
            plans: A list of improvement plan dictionaries.  See
                :meth:`apply_plan` for the expected keys.
            workspace: Optional workspace path override.

        Returns:
            A list of :class:`ImplementationResult` instances, one per plan,
            in the same order as the input ``plans``.
        """
        results: list[ImplementationResult] = []
        for i, plan in enumerate(plans):
            logger.debug("[IMPL] Processing plan %d/%d: %s", i + 1, len(plans), plan.get("id", "?"))
            result = await Implementation.apply_plan(plan, workspace)
            results.append(result)
        return results

    # ── Apply a previously saved diff ─────────────────────────────────────

    @staticmethod
    async def apply_diff(
        diff_path: str,
        workspace: str | None = None,
    ) -> ImplementationResult:
        """Apply a previously saved code diff to the target file.

        This is the second step for **high-risk** code changes.  After
        the user approves a diff saved by :meth:`apply_plan`, this method
        applies it: it reads the diff file, extracts the target path
        from the diff header, and patches the file in-place.

        **Important**: The diff must have been generated by this module
        (i.e. the ``fromfile`` line in the diff matches the original file
        path).  Backup must already exist from the :meth:`apply_plan` call.

        Args:
            diff_path: Absolute path to the ``.diff`` file saved by
                :meth:`apply_plan`.
            workspace: Optional workspace path override.

        Returns:
            An :class:`ImplementationResult` describing the outcome.
        """
        if not os.path.isfile(diff_path):
            return ImplementationResult(
                success=False,
                error_message=f"Diff file not found: {diff_path}",
            )

        try:
            # Read the diff
            with open(diff_path, "r", encoding="utf-8") as f:
                diff_text = f.read()

            # Parse the target file path from the diff header
            # Unified diff format: --- path/to/original
            target_path = ""
            for line in diff_text.splitlines():
                if line.startswith("--- "):
                    target_path = line[4:].strip()
                    break

            if not target_path:
                return ImplementationResult(
                    success=False,
                    diff_path=diff_path,
                    error_message="Could not parse target file path from diff header",
                )

            # Verify backup exists
            backup_path = target_path + _BACKUP_SUFFIX
            if not os.path.exists(backup_path):
                return ImplementationResult(
                    success=False,
                    file_path=target_path,
                    diff_path=diff_path,
                    error_message=(
                        f"No backup found at {backup_path}. "
                        "Cannot apply diff without a backup."
                    ),
                )

            # Try to find the change_log entry to update its status
            db = _get_db(workspace)
            row = db.execute(
                "SELECT id FROM change_log WHERE diff_path=? AND status='pending_approval' ORDER BY id DESC LIMIT 1",
                (diff_path,),
            ).fetchone()
            change_log_id = row["id"] if row else 0

            # Apply the diff using difflib.patch-style logic
            # We read the original and reconstruct from the unified diff
            if os.path.exists(target_path):
                with open(target_path, "r", encoding="utf-8") as f:
                    original_lines = f.readlines()
            else:
                original_lines = []

            # Parse the diff to get the new content
            # Extract all lines after ---/+++ header, applying +/- logic
            new_lines: list[str] = []
            in_hunk = False
            for line in diff_text.splitlines(keepends=True):
                if line.startswith("@@ "):
                    in_hunk = True
                    continue
                if not in_hunk:
                    continue
                if line.startswith("+"):
                    new_lines.append(line[1:])
                elif line.startswith("-"):
                    continue  # skip removed lines
                elif line.startswith(" "):
                    new_lines.append(line[1:])
                elif line.startswith("\\"):  # No newline at end of file
                    continue

            if not new_lines:
                return ImplementationResult(
                    success=False,
                    file_path=target_path,
                    diff_path=diff_path,
                    error_message="Diff produced no content to write",
                )

            # Write patched content
            with open(target_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            # Update change_log entry
            if change_log_id:
                db.execute(
                    "UPDATE change_log SET status='applied', error_message='' WHERE id=?",
                    (change_log_id,),
                )
                db.commit()

            logger.info("[IMPL] Applied diff %s → %s", diff_path, target_path)
            return ImplementationResult(
                success=True,
                file_path=target_path,
                backup_path=backup_path,
                diff_path=diff_path,
                change_log_id=change_log_id,
            )

        except Exception as exc:
            logger.error("[IMPL] Failed to apply diff %s: %s", diff_path, exc)
            return ImplementationResult(
                success=False,
                diff_path=diff_path,
                error_message=str(exc),
            )

    # ── Rollback ──────────────────────────────────────────────────────────

    @staticmethod
    async def rollback(
        plan_id: str,
        workspace: str | None = None,
    ) -> RollbackResult:
        """Roll back a previously applied plan by restoring from its backup.

        This method:

        1. Looks up the most recent ``applied`` or ``pending_approval``
           change_log entry for the given ``plan_id``.
        2. Verifies the backup file exists.
        3. Restores the backup to the original file path.
        4. Marks the change_log entry as ``rolled_back`` with a timestamp.

        If the backup file does not exist, the rollback fails with an
        appropriate error message.

        Args:
            plan_id: The plan identifier used when the plan was applied.
            workspace: Optional workspace path override.

        Returns:
            A :class:`RollbackResult` describing the outcome.
        """
        db = _get_db(workspace)

        # Find the most recent applied/pending entry for this plan_id
        rows = db.execute(
            """SELECT id, plan_id, file_path, backup_path, diff_path, status
               FROM change_log
               WHERE plan_id=? AND status IN ('applied', 'pending_approval')
               ORDER BY id DESC
               LIMIT 1""",
            (plan_id,),
        ).fetchall()

        if not rows:
            return RollbackResult(
                success=False,
                plan_id=plan_id,
                error_message=(
                    f"No applied or pending change found for plan_id={plan_id}"
                ),
            )

        entry = dict(rows[0])
        change_log_id = entry["id"]
        file_path = entry["file_path"]
        backup_path = entry["backup_path"]

        # Validate backup exists
        if not backup_path or not os.path.isfile(backup_path):
            return RollbackResult(
                success=False,
                plan_id=plan_id,
                change_log_id=change_log_id,
                file_path=file_path,
                error_message=f"Backup file not found: {backup_path}",
            )

        try:
            # Restore the backup
            shutil.copy2(backup_path, file_path)
            logger.info("[IMPL] Rolled back %s: %s → %s", plan_id, backup_path, file_path)

            # Mark the change_log entry
            now = datetime.now().isoformat()
            db.execute(
                """UPDATE change_log
                   SET status='rolled_back', rolled_back_at=?, error_message=''
                   WHERE id=?""",
                (now, change_log_id),
            )
            db.commit()

            return RollbackResult(
                success=True,
                plan_id=plan_id,
                change_log_id=change_log_id,
                file_path=file_path,
            )

        except OSError as exc:
            msg = f"Rollback failed for plan {plan_id}: {exc}"
            logger.error("[IMPL] %s", msg)
            return RollbackResult(
                success=False,
                plan_id=plan_id,
                change_log_id=change_log_id,
                file_path=file_path,
                error_message=str(exc),
            )

    # ── Change log queries ────────────────────────────────────────────────

    @staticmethod
    def get_change_log(
        limit: int = 20,
        workspace: str | None = None,
        status: str | None = None,
    ) -> list[ChangeLogEntry]:
        """Retrieve recent entries from the change_log table.

        Args:
            limit: Maximum number of entries to return (default 20).
            workspace: Optional workspace path override.
            status: Optional filter by status (``'applied'``, ``'rolled_back'``,
                ``'pending_approval'``, ``'failed'``).  If ``None``, all
                statuses are included.

        Returns:
            A list of :class:`ChangeLogEntry` instances ordered by ID
            descending (most recent first).
        """
        db = _get_db(workspace)

        if status:
            rows = db.execute(
                """SELECT * FROM change_log
                   WHERE status=?
                   ORDER BY id DESC
                   LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT * FROM change_log
                   ORDER BY id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        return [ChangeLogEntry(**dict(r)) for r in rows]

    @staticmethod
    def get_change_log_by_plan(
        plan_id: str,
        workspace: str | None = None,
    ) -> list[ChangeLogEntry]:
        """Retrieve all change_log entries for a specific plan ID.

        Args:
            plan_id: The plan identifier.
            workspace: Optional workspace path override.

        Returns:
            A list of :class:`ChangeLogEntry` instances ordered by ID
            descending.
        """
        db = _get_db(workspace)
        rows = db.execute(
            """SELECT * FROM change_log
               WHERE plan_id=?
               ORDER BY id DESC""",
            (plan_id,),
        ).fetchall()
        return [ChangeLogEntry(**dict(r)) for r in rows]

    @staticmethod
    def count_by_status(
        workspace: str | None = None,
    ) -> dict[str, int]:
        """Count change_log entries grouped by status.

        Args:
            workspace: Optional workspace path override.

        Returns:
            A dict mapping status strings to counts, e.g.
            ``{'applied': 5, 'rolled_back': 2, 'pending_approval': 1, 'failed': 0}``.
        """
        db = _get_db(workspace)
        rows = db.execute(
            """SELECT status, COUNT(*) AS cnt
               FROM change_log
               GROUP BY status
               ORDER BY cnt DESC"""
        ).fetchall()
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = r["cnt"]
        # Ensure all statuses are present
        for s in ("applied", "rolled_back", "pending_approval", "failed"):
            counts.setdefault(s, 0)
        return counts


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone convenience functions
# ═══════════════════════════════════════════════════════════════════════════════


async def apply_plan(plan: dict[str, Any], workspace: str | None = None) -> ImplementationResult:
    """Standalone convenience function wrapping :meth:`Implementation.apply_plan`.

    Args:
        plan: An improvement plan dictionary.
        workspace: Optional workspace path override.

    Returns:
        An :class:`ImplementationResult`.
    """
    return await Implementation.apply_plan(plan, workspace)


async def apply_plans(plans: list[dict[str, Any]], workspace: str | None = None) -> list[ImplementationResult]:
    """Standalone convenience function wrapping :meth:`Implementation.apply_plans`.

    Args:
        plans: A list of improvement plan dictionaries.
        workspace: Optional workspace path override.

    Returns:
        A list of :class:`ImplementationResult` instances.
    """
    return await Implementation.apply_plans(plans, workspace)


async def rollback(plan_id: str, workspace: str | None = None) -> RollbackResult:
    """Standalone convenience function wrapping :meth:`Implementation.rollback`.

    Args:
        plan_id: The plan identifier.
        workspace: Optional workspace path override.

    Returns:
        A :class:`RollbackResult`.
    """
    return await Implementation.rollback(plan_id, workspace)


def get_change_log(limit: int = 20, workspace: str | None = None) -> list[ChangeLogEntry]:
    """Standalone convenience function wrapping :meth:`Implementation.get_change_log`.

    Args:
        limit: Maximum number of entries (default 20).
        workspace: Optional workspace path override.

    Returns:
        A list of :class:`ChangeLogEntry` instances.
    """
    return Implementation.get_change_log(limit=limit, workspace=workspace)


# ── Cleanup ────────────────────────────────────────────────────────────────────


def close_db() -> None:
    """Close the thread-local database connection.

    Should be called when the module is no longer needed (e.g. during
    application shutdown) to release resources.
    """
    _close_db()
