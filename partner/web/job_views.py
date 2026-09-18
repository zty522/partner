"""Read-only job / event views (M1 / Section 10).

Endpoints go through the indexed resource layer.  Authorization is
enforced at the auth layer (the session subject must be loaded).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from flask import jsonify, abort
    _HAVE_FLASK = True
except Exception:
    _HAVE_FLASK = False


def register_job_views(app: Any) -> None:
    if not _HAVE_FLASK:
        return

    @app.get("/api/jobs")
    def list_jobs():
        from flask import request, session
        sub = session.get("subject") or {}
        if not sub:
            abort(401)
        ws = Path(session.get("workspace") or "/mnt/e/work/partner_workspace")
        limit = min(int(request.args.get("limit", 100)), 500)
        status = request.args.get("status")
        try:
            from partner.index.job_repository import init as _init_repo
            repo = _init_repo(ws)
            statuses = [status] if status else ["queued", "running", "completed", "failed", "cancelled"]
            rows = repo.list_by_status(statuses, limit=limit)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 503
        return jsonify({"jobs": rows})
