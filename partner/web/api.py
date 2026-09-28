"""Top-level HTTP API surface (M1 / Section 4) — round 5.

All state-changing routes go through ``partner.application.orchestrator``.
The orchestrator raises ``_IdempotencyInProgress`` when a previous
same-key request is still being processed; the API maps that to a
409 with retry-after metadata.  Idempotency replays return the live
Job status (not hard-coded ``"queued"``).

State honesty: ``static_implemented``.  No request has been served yet.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    from flask import request, jsonify, abort, session, current_app
    _HAVE_FLASK = True
except Exception:
    _HAVE_FLASK = False


logger = logging.getLogger("partner.web.api")


def _workspace_root() -> Path:
    return Path(session.get("workspace") or current_app.config.get("PARTNER_WORKSPACE")
                or "/mnt/e/work/partner_workspace")


def _resolve_caller_subject() -> dict:
    sub = session.get("subject") or {}
    if not sub:
        abort(401, description="not authenticated")
    return sub


def _job_view_url(job_id: str) -> str:
    return f"/?job={job_id}"


def _authorised_job(workspace: Path, job_id: str) -> dict:
    sub = _resolve_caller_subject()
    from partner.index.job_repository import init as _init_repo
    record = _init_repo(workspace).get_record(job_id)
    if not record:
        abort(404, description="job not found")
    allowed = {str(value) for value in sub.get("allowed_instances") or []}
    assigned = str(record.get("assigned_instance") or record.get("origin_instance") or "")
    if assigned and assigned not in allowed:
        abort(403, description="job belongs to an unauthorised instance")
    return record


def register_api_routes(app: Any) -> None:
    if not _HAVE_FLASK:
        return

    @app.post("/api/submit")
    def submit() -> Any:
        body = request.get_json(silent=True) or {}
        sub = _resolve_caller_subject()
        instance = str(body.get("instance") or "")
        if instance not in {"01", "02", "03", "04", "05"}:
            abort(400, description="instance must be one of 01..05")
        if not (body.get("project_id") or body.get("mode") or body.get("direct_answer")):
            abort(400, description="one of project_id / mode / direct_answer is required")
        if body.get("project_id") and body.get("mode"):
            abort(400, description="project_id and mode are mutually exclusive")

        from partner.application.orchestrator import (
            orchestrate_submit,
            IdempotencyConflict,
            IdempotencyScopeError, NotYetOwned,
            UnauthorisedInstanceError,
        )
        from partner.application.orchestrator import _IdempotencyInProgress

        direct = bool(body.get("direct_answer"))
        # direct_answer has no reservation: keep request_id None so the
        # orchestrator takes its direct path (accepted=True, job_id="")
        # instead of the idempotent path (which requires a job_id).
        request_id = None if direct else body.get("request_id")
        if request_id and len(request_id) < 8:
            abort(400, description="request_id must be >= 8 chars")

        sync_qq = body.get("sync_qq") is True
        recipient_ref = str(body.get("recipient_ref") or "")
        submit_channel = str(body.get("channel") or "web")
        constraints = dict(body.get("execution_constraints") or {})
        if sync_qq:
            from partner.identity.binding import load_bot_binding
            binding = load_bot_binding(str(_workspace_root()), instance)
            if len(binding.allowed_user_openids) != 1:
                abort(409, description="QQ 同步要求该实例恰好绑定一个授权用户")
            recipient_ref = f"inst{instance}_{binding.allowed_user_openids[0]}"
            submit_channel = "both"
            constraints["delivery_channels"] = ["web", "qq"]
        try:
            result = orchestrate_submit(
                workspace_root=str(_workspace_root()),
                text=str(body.get("message") or ""),
                channel=submit_channel,
                sender_id=str(body.get("sender_id") or
                              f"web:{sub.get('subject_id', 'anon')}:{instance}"),
                sender_name=str(body.get("sender_name") or
                                sub.get("display_name") or "WebUser"),
                persona_hint=instance,
                project_id=str(body.get("project_id") or ""),
                mode=str(body.get("mode") or ""),
                scope=str(body.get("scope") or ""),
                execution_constraints=constraints,
                attachments=body.get("attachments"),
                report_policy=str(body.get("report_policy") or "milestone"),
                request_id=request_id,
                recipient_ref=recipient_ref,
                subject_allowed_instances=sub.get("allowed_instances") or [],
                subject_id=str(sub.get("subject_id") or ""),
                attachments_signature=body.get("attachments_signature"),
            )
        except IdempotencyConflict as exc:
            return jsonify({
                "status": "conflict", "error": str(exc),
                "request_id": request_id,
            }), 409
        except _IdempotencyInProgress as exc:
            return jsonify({
                "status": "in_progress",
                "error": str(exc),
                "request_id": request_id,
                "retry_after_ms": 1500,
            }), 409
        except (IdempotencyScopeError, NotYetOwned) as exc:
            return jsonify({"status": "rejected", "error": str(exc)}), 409
        except UnauthorisedInstanceError as exc:
            return jsonify({"status": "rejected", "error": f"unauthorised: {exc}"}), 403
        except Exception as exc:
            logger.exception("submit: orchestrator failed")
            return jsonify({"error": str(exc)}), 500

        return jsonify({
            "status": "queued" if not result.was_idempotent_hit else "idempotent_replay",
            "accepted": True,
            "queued": not result.was_idempotent_hit,
            "job_id": result.job_id,
            "assigned_instance": result.assigned_instance,
            "persona_hint": result.persona_hint,
            "flow_id": None,
            "web_view_url": _job_view_url(result.job_id) if result.job_id else None,
            "delivery": {"web": "pending", "qq": "n_a"},
            "request_id": result.request_id,
            "fingerprint": result.fingerprint,
            "owner_token": result.owner_token,
            "was_idempotent_hit": result.was_idempotent_hit,
            "live_status": result.status,
        })

    @app.post("/api/cancel/<job_id>")
    def cancel(job_id: str) -> Any:
        sub = _resolve_caller_subject()
        workspace = _workspace_root()
        try:
            from partner.index.job_repository import init as _init_repo
            repo = _init_repo(workspace)
            ok = repo.request_cancel(job_id, actor=f"web:{sub.get('subject_id', 'anon')}")
        except Exception as exc:
            return jsonify({"error": f"cancel failed: {exc}"}), 500
        return jsonify({"status": "cancel_requested" if ok else "not_found",
                         "job_id": job_id}), (200 if ok else 404)

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str) -> Any:
        workspace = _workspace_root()
        record = _authorised_job(workspace, job_id)
        # Redact metadata.json secret keys.
        record.pop("metadata_json", None)
        record.pop("metadata", None)
        from partner.runtime.event_run_log import sanitize
        return jsonify({"job": sanitize(record)})

    @app.get("/api/jobs/<job_id>/timeline")
    def job_timeline(job_id: str) -> Any:
        workspace = _workspace_root()
        record = _authorised_job(workspace, job_id)
        try:
            from partner.index.job_repository import init as _init_repo
            repo = _init_repo(workspace)
            history = repo.history(job_id)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"job_id": job_id, "history": history, "events": history,
                         "current_status": record.get("status")})

    @app.get("/api/jobs/<job_id>/run-trace")
    def job_run_trace(job_id: str) -> Any:
        workspace = _workspace_root()
        _authorised_job(workspace, job_id)
        try:
            from partner.web.run_trace import trace_overview
            value = trace_overview(workspace, job_id,
                                   after=int(request.args.get("after", "0")),
                                   limit=int(request.args.get("limit", "120")),
                                   view=str(request.args.get("view", "business")))
        except ValueError as exc:
            abort(400, description=str(exc))
        return jsonify(value)

    @app.get("/api/jobs/<job_id>/run-trace/events/<event_id>")
    def job_run_trace_event(job_id: str, event_id: str) -> Any:
        workspace = _workspace_root()
        _authorised_job(workspace, job_id)
        try:
            from partner.web.run_trace import event_detail
            value = event_detail(workspace, job_id, event_id)
        except ValueError as exc:
            abort(400, description=str(exc))
        except FileNotFoundError:
            abort(404, description="event trace not found")
        return jsonify(value)

    @app.get("/api/jobs/<job_id>/events")
    def job_events(job_id: str) -> Any:
        """Server-Sent Events stream of timeline updates.

        Cursor is the rowid in job_history; on each tick the client
        receives any history rows with seq > after.
        """
        workspace = _workspace_root()
        _authorised_job(workspace, job_id)
        after = int(request.args.get("after", "0"))
        from partner.web.sse import make_sse_response
        return make_sse_response(workspace=workspace,
                                  job_id=job_id, after=after)

    @app.get("/api/projects")
    def list_projects() -> Any:
        _resolve_caller_subject()
        workspace = _workspace_root()
        try:
            from partner.projects.dynamic_project_registry import DynamicProjectRegistry
            reg = DynamicProjectRegistry(workspace)
            projects = [{"project_id": r.project_id, "status": r.status,
                         "owner_instance": r.owner_instance,
                         "summary": r.summary}
                        for r in reg.list_projects(include_archived=False)]
        except Exception as exc:
            return jsonify({"error": f"project registry unavailable: {exc}",
                            "projects": []}), 503
        return jsonify({"projects": projects})

    @app.get("/api/capability_matrix")
    def capability_matrix() -> Any:
        _resolve_caller_subject()
        p = Path(__file__).resolve().parents[2] / "docs/research/capability_matrix.md"
        if not p.exists():
            return jsonify({"error": "capability matrix not found"}), 404
        return jsonify({"path": str(p), "size_bytes": p.stat().st_size})

    @app.get("/api/bindings/<instance_id>")
    def get_binding(instance_id: str) -> Any:
        sub = _resolve_caller_subject()
        if instance_id not in set(sub.get("allowed_instances") or []):
            abort(403, description="instance not allowed")
        workspace = _workspace_root()
        try:
            from partner.identity.binding import load_bot_binding
            b = load_bot_binding(str(workspace), instance_id)
            return jsonify({
                "instance_id": b.instance_id,
                "bot_id": b.bot_id,
                "bot_name": b.bot_name,
                "sender_openid": b.sender_openid,
                "allowed_user_openids": list(b.allowed_user_openids),
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 503

    @app.get("/api/evolution/runs")
    def evolution_runs() -> Any:
        _resolve_caller_subject()
        workspace = _workspace_root()
        runs = []
        runs_dir = workspace / "runs"
        if runs_dir.exists():
            for run_json in sorted(runs_dir.glob("*/run.json")):
                try:
                    payload = json.loads(run_json.read_text(encoding="utf-8"))
                    m = payload.get("manifest") or {}
                    runs.append({
                        "run_id": m.get("run_id"),
                        "protocol_id": m.get("protocol_id"),
                        "parent_run_id": m.get("parent_run_id"),
                        "code_sha": m.get("code_sha"),
                        "code_dirty": m.get("code_dirty"),
                        "model": m.get("model"),
                        "state": m.get("state"),
                        "score_count": m.get("score_count", 0),
                        "issue_count": m.get("issue_count", 0),
                        "fixture": m.get("fixture"),
                        "started_at": m.get("started_at"),
                        "finished_at": m.get("finished_at"),
                    })
                except Exception:
                    continue
        return jsonify({"runs": runs[:min(int(request.args.get("limit", 100)), 500)]})

    @app.get("/api/evolution/<run_id>/compare")
    def evolution_compare(run_id: str) -> Any:
        _resolve_caller_subject()
        path = _workspace_root() / "runs" / run_id / "run.json"
        if not path.exists():
            abort(404, description="run not found")
        payload = json.loads(path.read_text(encoding="utf-8"))
        scores = payload.get("scores", []) or []
        issues = payload.get("issues", []) or []
        return jsonify({
            "comparison": {
                "run_id": run_id,
                "all_scores": scores,
                "issues": issues,
                "manifest": payload.get("manifest"),
            }
        })

    @app.get("/api/artifacts")
    def list_artifacts() -> Any:
        _resolve_caller_subject()
        workspace = _workspace_root()
        try:
            from partner.index.artifact_repository import init as _init_art
            repo = _init_art(workspace)
        except Exception as exc:
            return jsonify({"error": f"artifact index unavailable: {exc}",
                            "artifacts": []}), 503
        job_id = request.args.get("job_id", "")
        kind = request.args.get("type", "")
        limit = min(int(request.args.get("limit", 100)), 500)
        try:
            if job_id:
                artifacts = repo.by_job(job_id, kind=kind, limit=limit)
            elif kind:
                artifacts = repo.by_kind(kind, limit=limit)
            else:
                artifacts = []
        except Exception as exc:
            return jsonify({"error": str(exc), "artifacts": []}), 503
        projected = []
        for row in artifacts:
            value = dict(row)
            value["type"] = value.get("kind") or ""
            value["size_bytes"] = int(value.get("byte_size") or 0)
            value["produced_at"] = value.get("created_at")
            value["download_url"] = f"/api/artifacts/{value.get('artifact_id')}/download"
            suffix = Path(str(value.get("path") or "")).suffix.lower()
            value["previewable"] = suffix in {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".md", ".txt", ".json"}
            value["preview_url"] = f"/api/artifacts/{value.get('artifact_id')}/preview"
            projected.append(value)
        return jsonify({"artifacts": projected})

    @app.get("/api/artifacts/<artifact_id>/download")
    def download_artifact(artifact_id: str) -> Any:
        _resolve_caller_subject()
        workspace = _workspace_root()
        try:
            from partner.index.artifact_repository import init as _init_art
            row = _init_art(workspace).get(artifact_id)
        except Exception as exc:
            return jsonify({"error": f"artifact index unavailable: {exc}"}), 503
        if not row:
            abort(404, description="artifact not found")
        _authorised_job(workspace, str(row.get("job_id") or ""))
        path = Path(str(row.get("path") or ""))
        if not path.is_file():
            abort(404, description="artifact file missing")
        from flask import send_file
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.get("/api/artifacts/<artifact_id>/preview")
    def preview_artifact(artifact_id: str) -> Any:
        """Display a registered, authorised artifact without exposing paths."""
        _resolve_caller_subject()
        workspace = _workspace_root()
        try:
            from partner.index.artifact_repository import init as _init_art
            row = _init_art(workspace).get(artifact_id)
        except Exception as exc:
            return jsonify({"error": f"artifact index unavailable: {exc}"}), 503
        if not row:
            abort(404, description="artifact not found")
        _authorised_job(workspace, str(row.get("job_id") or ""))
        path = Path(str(row.get("path") or ""))
        if not path.is_file():
            abort(404, description="artifact file missing")
        suffix = path.suffix.lower()
        allowed = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".md", ".txt", ".json"}
        if suffix not in allowed:
            abort(415, description="artifact type is not previewable")
        from flask import send_file
        return send_file(path, as_attachment=False, download_name=path.name,
                         mimetype={".md": "text/plain; charset=utf-8",
                                   ".txt": "text/plain; charset=utf-8",
                                   ".json": "application/json"}.get(suffix))

    @app.get("/api/learning/sources")
    def learning_sources() -> Any:
        _resolve_caller_subject()
        workspace = _workspace_root()
        try:
            import yaml
            path = workspace / "docs/dependencies/external_sources.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception as exc:
            return jsonify({"error": f"manifest unreadable: {exc}",
                            "sources": []}), 503
        srcs = []
        for s in (data or {}).get("sources", []) or []:
            srcs.append({
                "id": s.get("id"), "title": s.get("title"),
                "url": s.get("url"), "version_reference": s.get("version_reference"),
                "licence": s.get("licence"), "status": s.get("status"),
                "reused_in": s.get("reused_in"),
            })
        return jsonify({"sources": srcs})

    @app.get("/api/config")
    def config_view() -> Any:
        _resolve_caller_subject()
        workspace = _workspace_root()
        out = {"instances": [], "model": "unknown", "budget": {}}
        try:
            from partner.governance.instance_native import PROJECTS
        except Exception:
            PROJECTS = {}
        cfg_path = workspace / "config" / "partner_config.json"
        instances_cfg = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                instances_cfg = ((cfg.get("instances") or {}) if isinstance(cfg, dict) else {})
            except Exception:
                instances_cfg = {}
        for instance_id, (project_id, _title) in PROJECTS.items():
            inst_cfg = instances_cfg.get(instance_id) or {}
            out["instances"].append({
                "instance_id": instance_id,
                "project_id": project_id,
                "persona_hint": instance_id,
                "bot_id": (inst_cfg.get("bot_aliases") or [""])[0]
                    if inst_cfg.get("bot_aliases") else "",
                "bot_name": inst_cfg.get("bot_name", ""),
            })
        api_path = workspace / "config" / "api.json"
        if api_path.exists():
            try:
                api_data = json.loads(api_path.read_text(encoding="utf-8"))
                out["model"] = api_data.get("default_provider", "unknown")
                apis = api_data.get("apis") or {}
                chosen = apis.get(out["model"]) or {}
                out["model"] = chosen.get("model", out["model"])
            except Exception:
                pass
        return jsonify(out)


# Required for time import inside submit().
import time
