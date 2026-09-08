"""Scaffold a project directory for an instance on first launch.

Used by the runtime daemon so that every enabled instance has a
``share/projects/<project_id>/`` tree with a real project_brief.md,
state.md, and governance/project_state.json — without that the
self-drive oneshot falls back to the legacy "自动续跑" template
because ``project_loop.request_next_action`` finds no brief.

The scaffold is **only** created when the project directory does not
exist yet.  Existing projects (with a real brief or receipts already
on disk) are left untouched — production_readiness work must not be
silently overwritten by an idempotent scaffold.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from partner.workspace.workspace_layout import workspace_root_from_instance


DEFAULT_PROJECT_BRIEF_TEMPLATE = """\
# {project_id}

## Goal
{goal}

## Hard constraint
Every iteration must contain at least one verifiable external action
(see ADR 0061 real-action contract):
- a real computation / experiment whose output lands in external_artifacts
- a real web fetch whose content lands in external_sources
- a real code change in partner/<pkg>/ followed by pytest -q
- a real data write to external_artifacts/<receipt_id>/

``actions_executed`` must list ``exec:...`` / ``web.fetch:URL`` /
``pytest:test_xxx::case_yyy`` / ``atomic_write_file:path`` — not just
``read_file`` or ``generate_text``.

## Next iteration hint
Pick the smallest verifiable external action that advances the goal
and produce one named artifact.
"""

# Bug #58 P0.1 fix (ADR 0066): the legacy_seeded placeholder brief has
# every section written as ``待补充。``.  When the scaffold detects
# that string in an existing brief it overwrites the brief with a real
# one derived from the PROJECTS dict goal, instead of leaving the
# placeholder which causes ``project_loop.request_next_action`` to
# return no_proposed_action and silently strands the instance.
PLACEHOLDER_SENTINEL = "待补充。"

DEFAULT_STATE_TEMPLATE = """\
# State — {project_id}

status: active
last_iteration_at: <unset>
last_iteration_kind: <unset>
notes: scaffolded by run_project_scaffold on first launch.
"""

DEFAULT_PROJECT_STATE_JSON: dict[str, Any] = {
    "status": "active",
    "last_iteration_at": "",
    "last_iteration_kind": "",
    "allow_continue": True,
    "current_iteration": 0,
    "latest_receipt_id": "",
    "blocked_reason": "",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def scaffold_project(
    workspace: str | Path,
    instance_id: str,
    project_id: str,
    goal: str,
) -> dict[str, Any]:
    """Create ``share/projects/<project_id>/`` with brief/state/state.json
    if it does not already exist. Returns a summary with what was
    created so the caller (e.g. reconcile_slots) can log the action."""
    ws_path = Path(workspace)
    # workspace may be the root workspace OR an instance workspace like
    # ``.../instances/03``. Detect which by walking up.
    if ws_path.parent.name == "instances":
        root = ws_path.parent.parent
    else:
        root = ws_path
    proj_root = root / "share" / "projects" / project_id
    summary = {
        "instance_id": instance_id,
        "project_id": project_id,
        "project_root": str(proj_root),
        "created": [],
        "skipped_existing": False,
        "scaffolded_at": _now_iso(),
    }
    if proj_root.exists():
        # Bug #58 P0.1 (ADR 0066): if the brief is still a placeholder
        # (every section says ``待补充。``) overwrite it with a real
        # one derived from PROJECTS.  Otherwise the project loop will
        # keep returning ``no_proposed_action`` and the instance will
        # be silently stranded with no next action.
        brief_path = proj_root / "project_brief.md"
        try:
            existing_brief = brief_path.read_text(encoding="utf-8")
        except OSError:
            existing_brief = ""
        if PLACEHOLDER_SENTINEL in existing_brief:
            brief = DEFAULT_PROJECT_BRIEF_TEMPLATE.format(
                project_id=project_id, goal=goal or "<unset>")
            brief_path.write_text(brief, encoding="utf-8")
            summary["replaced_placeholder_brief"] = True
        summary["skipped_existing"] = True
        return summary
    (proj_root / "governance" / "receipts").mkdir(parents=True, exist_ok=True)
    (proj_root / "external_artifacts").mkdir(parents=True, exist_ok=True)
    (proj_root / "external_sources").mkdir(parents=True, exist_ok=True)
    (proj_root / "reports").mkdir(parents=True, exist_ok=True)

    brief = DEFAULT_PROJECT_BRIEF_TEMPLATE.format(
        project_id=project_id, goal=goal or "<unset>")
    (proj_root / "project_brief.md").write_text(brief, encoding="utf-8")
    summary["created"].append("project_brief.md")

    state = DEFAULT_STATE_TEMPLATE.format(project_id=project_id)
    (proj_root / "state.md").write_text(state, encoding="utf-8")
    summary["created"].append("state.md")

    pstate = dict(DEFAULT_PROJECT_STATE_JSON)
    pstate["project_id"] = project_id
    (proj_root / "governance" / "project_state.json").write_text(
        json.dumps(pstate, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["created"].append("governance/project_state.json")

    # ownership / handoff metadata so the project loop can pick a real
    # next_action (request_next_action needs project_state.json + brief).
    (proj_root / "governance" / "iteration_history.jsonl").write_text(
        "", encoding="utf-8")
    summary["created"].append("governance/iteration_history.jsonl")

    return summary


def scaffold_all_enabled_projects(
    workspace: str | Path,
    instance_ids: list[str],
    projects: dict[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """Scaffold every (instance_id, project_id, goal) pair in
    ``projects`` for the given workspace. Returns per-instance
    summaries."""
    return [
        scaffold_project(workspace, iid, projects[iid][0], projects[iid][1])
        for iid in instance_ids
        if iid in projects
    ]
