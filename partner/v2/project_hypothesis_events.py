"""Event-first entry point for bounded project hypothesis Candidates."""
from __future__ import annotations

from typing import Any

from partner.governance.project_hypothesis_engine import candidate_options
from partner.governance.storage import workspace_root


def atomic_project_hypothesis_propose(ctx: Any, params: dict) -> dict:
    """Propose/evaluate one bounded Candidate without executing arbitrary code.

    The same handler is used by the native selector and is registered in the
    Harness registry, so automatic and manual paths share one auditable Event.
    """
    root = workspace_root(str(getattr(ctx, "workspace", "") or params.get("workspace") or ""))
    project_id = str(params.get("project_id") or "")
    if not project_id:
        return {"ok": False, "status": "missing_project_id", "production_effective": False}
    options, candidate = candidate_options(
        root, project_id, int(params.get("project_steps") or 0))
    return {
        "ok": bool(candidate),
        "status": str(candidate.get("decision") or "no_candidate"),
        "candidate": candidate,
        "event_options": [
            {"event_type": event_type, "parameters": parameters}
            for event_type, parameters in options
        ],
        "production_effective": bool(candidate.get("production_effective", False)),
    }


HANDLERS = {"project_hypothesis_propose": atomic_project_hypothesis_propose}

