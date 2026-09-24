"""One application service for every Partner user interface.

This layer owns user intent intake and durable background Jobs.  It does not
execute tools: dispatch pins a canonical Event Flow consumed by EventWorker.
No legacy inbox, batch planner, or Harness participates in production.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence, Union

from partner.governance.instance_native import PROJECTS
from partner.projects.dynamic_project_registry import DynamicProjectRegistry
from partner.projects.session_context import SessionContext
from partner.event_fabric import EventLedger, EventSummary, NextEventSelector
from partner.event_fabric import EventFlowController, EventFlowStore, build_catalog
from partner.event_flows import build_flow_registry
from partner.runtime.request_budget import validate_constraints

from .models import JobRecord, Submission


PROJECT_TO_INSTANCE = {project_id: instance_id for instance_id, (project_id, _title) in PROJECTS.items()}
PROJECT_TITLES = {project_id: title for _instance_id, (project_id, title) in PROJECTS.items()}

_PROJECT_MARKERS = {
    "xiaohongshu_operations": ("小红书", "公众号", "社交账号", "内容运营"),
    "molecular_generation": ("分子生成", "靶点", "对接", "targetdiff", "药物", "molecule"),
    "molecular_dynamics_study": ("分子动力学", "amber", "gromacs", "md 模拟", "md模拟"),
    "literature_github_learning": ("github", "论文", "文献", "源码", "外部学习", "主动学习"),
    "partner_explore": ("partner 自进化", "自进化", "harness", "candidate", "partner框架"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(value: str | os.PathLike) -> Path:
    path = Path(value).expanduser().resolve()
    if path.parent.name == "instances":
        return path.parent.parent
    return path


def _project_via_classifier(text: str, *,
                             workspace_root: "Path" = None,
                             persona_hint: str = "",
                             explicit: str = "",
                             conversation_id: str = "") -> "ProjectDecision":
    """4-level classification path for ``feature_flag == "new"``.

    Always returns a :class:`ProjectDecision` — caller branches on
    ``decision.kind`` rather than guessing project_id from a slug string.
    """
    try:
        from partner.application.project_classifier import (
            ProjectClassifier, ProjectDecision, Kind,
        )
    except Exception:
        # Classifier module unavailable (rare) — return a synthetic
        # CHAT_REPLY decision so the message path stays alive.
        return _fallback_chat_decision(
            reason="classifier module unavailable",
        )

    # workspace_root is passed in by the caller (submit() has self.root).
    # If missing (e.g. unit test calling _project directly), fall back to
    # a heuristic rooted at service.py's parent directory.
    if workspace_root is None:
        workspace_root = Path(__file__).resolve().parents[2]

    try:
        registry = DynamicProjectRegistry(workspace_root=workspace_root)
        session = SessionContext(workspace_root=workspace_root)
        classifier = ProjectClassifier(
            workspace_root=workspace_root,
            session_context=session,
            registry=registry,
            semantic_backend="none",
        )
        return classifier.classify(
            request=text,
            conversation_id=conversation_id,
            explicit_project_id=explicit,
        )
    except Exception as exc:  # noqa: BLE001
        # Log the real cause so the operator can fix it.
        import logging
        logging.getLogger("partner.application.service").exception(
            "[ADR 0099] classifier call failed, falling back to CHAT_REPLY: %s",
            type(exc).__name__,
        )
        return _fallback_chat_decision(
            reason=f"classifier call failed: {type(exc).__name__}: {exc}",
        )


def _fallback_chat_decision(*, reason: str) -> "ProjectDecision":
    """Build a synthetic CHAT_REPLY ProjectDecision without importing
    the classifier module — safe to call from anywhere in service.py."""
    from partner.application.project_classifier import ProjectDecision, Kind
    return ProjectDecision(
        kind=Kind.CHAT_REPLY,
        source_level="L0",
        confidence=0.0,
        reason=reason,
    )


def _normalise_project_result(
    raw: Any, *, workspace_root: "Path", instance_id: str,
) -> str:
    """Legacy-friendly wrapper: returns just the project_id string.

    If ``raw`` is a ProjectDecision (new flag), resolve via
    ``_resolve_classifier_decision`` and return its project_id.
    Legacy callers receive a plain string and are unchanged.

    Chat replies degrade to "" so callers like ``project_query`` and
    ``modify_work`` short-circuit cleanly without crashing on a
    missing project_id.
    """
    if isinstance(raw, str):
        return raw
    decision = raw
    resolved = _resolve_classifier_decision(
        decision, workspace_root=workspace_root, instance_id=instance_id,
    )
    return resolved.get("project_id") or ""


def _normalise_project_result_full(
    raw: Any, *, workspace_root: "Path", instance_id: str,
) -> tuple:
    """Same as ``_normalise_project_result`` but returns the full dict
    so the submit() function can attach an explanatory message to the
    Submission reply.
    """
    if isinstance(raw, str):
        return raw, {}
    resolved = _resolve_classifier_decision(
        raw, workspace_root=workspace_root, instance_id=instance_id,
    )
    return resolved.get("project_id") or "", resolved


def _resolve_classifier_decision(
    decision: Any,
    *,
    workspace_root: "Path",
    instance_id: str,
) -> dict[str, str]:
    """Resolve a classifier decision without creating project artifacts.

    The intent and project-init Events own all new-project mutations.
    Returns a dict with keys:
      ``project_id``     — the slug we will hand to downstream code (or "" for chat)
      ``hint``           — original slug from classifier
      ``kind``           — passthrough of decision.kind.value
      ``status``         — "continue" | "pending_init" | "chat" | "failed"
      ``message``        — human-readable one-liner for the Submission
    """
    try:
        from partner.application.project_classifier import Kind as _KindEnum
    except Exception:
        _KindEnum = None

    if decision.kind == _KindEnum.CHAT_REPLY or str(decision.kind) == "Kind.CHAT_REPLY":
        return {
            "project_id": "",
            "hint": "",
            "kind": "chat_reply",
            "status": "chat",
            "message": "问题已排队，将通过问答 Event 回复。",
        }

    if decision.kind == _KindEnum.EXISTING_PROJECT or str(decision.kind) == "Kind.EXISTING_PROJECT":
        if decision.project_id:
            return {
                "project_id": decision.project_id,
                "hint": "",
                "kind": "existing_project",
                "status": "continue",
                "message": "",
            }
        # existing_project 但 LLM 没给 project_id —— 退化
        return {
            "project_id": "",
            "hint": "",
            "kind": "existing_project",
            "status": "failed",
            "message": "分类器说是已有项目但没给项目 ID；请重述。",
        }

    # Resolve a tentative slug; only interaction.project_init creates it.
    raw_slug = (decision.project_hint or "").strip() or "new_project"
    import re as _re
    # Three-tier normalisation:
    #   1. ASCII-only tokens get joined with "_" (English-friendly)
    #   2. If pure CJK, take first 6 chars (preserves meaning for Chinese
    #      project names) — filesystem supports UTF-8 on WSL/NTFS.
    #   3. If everything else fails, fall back to "new_project"
    ascii_parts = _re.findall(r"[A-Za-z0-9]+", raw_slug)
    cjk_chars = _re.findall(r"[\u4e00-\u9fff]", raw_slug)
    if ascii_parts:
        slug = "_".join(ascii_parts).lower()[:32].rstrip("_") or "new_project"
    elif cjk_chars:
        slug = "".join(cjk_chars)[:8] or "new_project"
    else:
        slug = "new_project"
    return {
        "project_id": slug, "hint": slug, "kind": "new_project_kind",
        "status": "pending_init",
        "message": "请求已排队，先理解意图；需要新项目时由 Event 创建。",
    }



class PartnerApplicationService:
    """Durable project/job facade; safe for multiple frontend processes."""

    def __init__(self, workspace_root: str | os.PathLike):
        self.root = _root(workspace_root)
        self.state = self.root / "state" / "application"
        self.jobs_dir = self.state / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.fabric = EventLedger(self.root)
        # Auto-install the JobRepository dual-write hook so every _save()
        # mirrors into the authoritative SQLite jobs.db.
        try:
            from partner.application.service_db_patch import install_save_db_hook
            install_save_db_hook()
        except Exception:
            pass

    def _application_config(self) -> dict[str, Any]:
        """Read ``application.*`` block from ``config/partner_config.json``.

        Returns an empty dict on any error.  This is the canonical place
        to look up ``application.*`` runtime flags added by ADR 0099.
        """
        try:
            value = json.loads((self.root / "config/partner_config.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}
        return dict(value.get("application") or {})

    def _feature_flag_application(self) -> dict[str, str]:
        """Extract ADR 0099 feature flags with defaults = legacy.

        ``instance_project_binding``  defaults to ``"legacy"`` (no change)
        ``semantic_retrieval_backend`` defaults to ``"none"``   (no L3 hits)
        ``new_project_fallback``      defaults to ``"project_init"``
        """
        cfg = self._application_config()
        return {
            "instance_project_binding": str(cfg.get("instance_project_binding") or "legacy").strip(),
            "semantic_retrieval_backend": str(cfg.get("semantic_retrieval_backend") or "none").strip(),
            "new_project_fallback": str(cfg.get("new_project_fallback") or "project_init").strip(),
        }

    def _intent_enrichment_enabled(self) -> bool:
        try:
            value = json.loads((self.root / "config/partner_config.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return False
        application = dict(value.get("application") or {})
        return str(application.get("intent_enrichment") or "").lower() == "three_pass_required"

    def _project_intent_context(self, project_id: str, persona_hint: str) -> dict[str, Any]:
        """Return bounded project memory so short user messages stay useful."""
        project = str(project_id or "").strip()
        dynamic = {row.project_id:row for row in DynamicProjectRegistry(self.root).list_projects(include_archived=True)}
        if not project and persona_hint in PROJECTS:
            project = PROJECTS[persona_hint][0]
        if project not in PROJECT_TO_INSTANCE and project not in dynamic:
            return {}
        directory = self.root / "share" / "projects" / project

        def _bounded_text(name: str, limit: int = 2400) -> str:
            try:
                return (directory / name).read_text(encoding="utf-8")[:limit]
            except OSError:
                return ""

        # Project ownership vs task execution identity are DISTINCT.  The
        # project may have a default owner (suggestion), but the task is
        # executed by the explicitly specified Partner (persona_hint); a
        # default owner must never overwrite that.
        default_owner = PROJECT_TO_INSTANCE.get(project) or dynamic[project].owner_instance
        context: dict[str, Any] = {
            "project_default_owner": default_owner,
            "executing_partner": persona_hint or default_owner or "",
            # Backward-compat alias: keep instance_id as the *default owner*
            # hint; the authoritative execution identity is executing_partner.
            "instance_id": default_owner,
            "project_id": project,
            "project_title": PROJECT_TITLES.get(project, project),
            "project_brief": _bounded_text("project_brief.md"),
            "project_state": _bounded_text("state.md"),
        }
        receipt_dir = directory / "governance" / "receipts"
        receipts = sorted(receipt_dir.glob("*.json")) if receipt_dir.exists() else []
        if receipts:
            latest = receipts[-1]
            try:
                value = json.loads(latest.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                value = {}
            context["latest_receipt"] = {
                "path": str(latest),
                "receipt_id": value.get("receipt_id"),
                "iteration": value.get("iteration"),
                "goal": value.get("goal"),
                "findings": list(value.get("findings") or [])[-3:],
                "next_actions": list(value.get("next_actions") or [])[:3],
                "unresolved_questions": list(value.get("unresolved_questions") or [])[:3],
                "stop_reason": value.get("stop_reason"),
            }
        return context

    def _enrich_intent(
        self,
        text: str,
        job_id: str,
        *,
        project_id: str = "",
        persona_hint: str = "",
    ) -> dict[str, Any]:
        """Persist intake context; the three LLM passes execute inside the Flow.

        Older releases called a v2 intent helper here and then repeated the
        same reasoning in the Event Flow.  Intake is now deliberately cheap
        and cannot claim that model interpretation happened before execution.
        """
        return {"ok": True, "status": "pending_event_flow", "_model_calls": 0,
                "intent": {"original_request": text,
                           "project_context": self._project_intent_context(project_id, persona_hint)}}

    @contextmanager
    def _locked(self):
        path = self.state / ".lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _save(self, job: JobRecord) -> None:
        from partner.index.job_repository import init
        job.updated_at = _now()
        repo=init(self.root)
        repo.upsert_from_record(job.to_dict(), actor='ApplicationService._save',
            projection_path=self.jobs_dir/f'{job.job_id}.json')
        for row in repo.outbox_pending():
            if row['job_id']==job.job_id:repo.outbox_emit_legacy_json(row['seq'])

    def _append_event(self, event_type: str, job: JobRecord, **extra: Any) -> None:
        row = {"schema_version": 1, "event_type": event_type, "at": _now(),
               "job_id": job.job_id, "project_id": job.project_id,
               "status": job.status, **extra}
        with (self.state / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def list_jobs(self, *, project_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        from partner.index.resource_catalog import job_records
        return job_records(self.root,project_id=project_id,limit=max(1,limit))

    def control_job(self, job_id: str, action: str) -> dict[str, Any]:
        """Persist a checkpoint-safe pause/resume/cancel request.

        Running non-idempotent Events are never killed halfway through; the
        worker applies pause/cancel immediately after their terminal is
        recorded.  Resume makes a paused Job schedulable again.
        """
        action = str(action or "").lower()
        if action not in {"pause", "resume", "cancel"}:
            return {"ok": False, "error": "unsupported_control_action"}
        path = self.jobs_dir / f"{job_id}.json"
        with self._locked():
            try:
                from partner.index.job_repository import init
                raw = init(self.root).get_record(job_id)
                if raw is None:raise ValueError('job not found')
                job = JobRecord(**{key: value for key, value in raw.items()
                                   if key in JobRecord.__dataclass_fields__})
            except (OSError, TypeError, ValueError):
                return {"ok": False, "error": "job_not_found"}
            if job.status in {"completed", "failed", "cancelled"} and action != "resume":
                return {"ok": False, "error": "job_already_terminal"}
            controls = self.state / "controls"; controls.mkdir(parents=True, exist_ok=True)
            control = controls / f"{job_id}.json"
            if action == "resume":
                job.status = "running"
                if job.flow_id:
                    flow = EventFlowStore(self.root).load(job.flow_id)
                    if flow.status == "paused":
                        flow.status = "running"
                        EventFlowStore(self.root).save(flow)
                control.unlink(missing_ok=True)
            else:
                temporary = control.with_suffix(".tmp")
                temporary.write_text(json.dumps({"job_id": job_id, "action": action,
                                                  "requested_at": _now()}, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, control)
                if not job.current_event_id:
                    job.status = "paused" if action == "pause" else "cancelled"
            self._save(job)
            self._append_event("job_control_requested", job, action=action)
        return {"ok": True, "job_id": job_id, "action": action, "status": job.status}


    def submit_acceptance(self, *, request="", channel="local",
                          sender_id="", sender_name="",
                          persona_hint="02"):
        """Test-only entry point: bypass synth_sem, route to acceptance_minimal_chain."""
        from partner.event_fabric.flows import EventFlowController, EventFlowStore
        from partner.event_flows.registry import build_flow_registry
        from partner.event_fabric.catalog import build_catalog
        from partner.index.job_repository import init as _init_jobs
        from partner.application.models import Submission

        catalog = build_catalog()
        registry = build_flow_registry()
        flow_def = registry.get("acceptance_minimal_chain")

        now = _now()
        job_id = "job_acc_" + uuid.uuid4().hex[:12]
        job = JobRecord(
            job_id=job_id,
            project_id="acceptance_test",
            title="Acceptance minimal chain",
            request=request or "Run acceptance_minimal_chain once",
            route="acceptance_minimal_chain",
            flow_type="acceptance_minimal_chain",
            channel=channel,
            sender_id=sender_id,
            sender_name=sender_name,
            persona_hint=persona_hint,
            origin_instance=persona_hint,
            assigned_instance=persona_hint,
            intake_instance_id=persona_hint,
            status="queued",
            created_at=now, updated_at=now,
            intent_contract={"mode": "acceptance"},
        )
        try:
            _init_jobs(self.root).upsert_from_record(
                job.to_dict(), actor="submit_acceptance")
        except Exception as exc:
            return Submission(False, "", "", persona_hint, "rejected",
                              "acceptance_minimal_chain",
                              "DB write failed: " + type(exc).__name__ + ": " + str(exc))
        store = EventFlowStore(self.root)
        controller = EventFlowController(store)
        state = controller.start(
            flow_def, catalog_version=catalog.version,
            task_id=job_id, project_id="acceptance_test",
            instance_id=persona_hint)
        job.flow_id = state.flow_id
        job.event_catalog_version = catalog.version
        job.ready_event_ids = list(state.ready_node_ids)
        _init_jobs(self.root).upsert_from_record(
            job.to_dict(), actor="submit_acceptance.flow")
        self._save(job)
        return Submission(True, job_id, "acceptance_test", persona_hint,
                          job.status, "acceptance_minimal_chain",
                          "submitted to acceptance_minimal_chain", "")


    def projects(self) -> list[dict[str, Any]]:
        self.refresh_jobs()
        jobs = self.list_jobs(limit=1000)
        result = []
        for instance_id, (project_id, title) in PROJECTS.items():
            own = [job for job in jobs if job.get("project_id") == project_id]
            active = next((job for job in own if job.get("status") in {"queued", "dispatched", "running"}), None)
            result.append({
                "project_id": project_id,
                "title": title,
                "specialist": instance_id,
                "status": (active or (own[0] if own else {})).get("status", "ready"),
                "active_job_id": (active or {}).get("job_id", ""),
                "job_count": len(own),
            })
        return result

    def refresh_jobs(self) -> list[str]:
        """Project existing Event tasks back onto application jobs.

        Task/Receipt data remains authoritative.  The application record is a
        navigation index only and never turns a missing task into success.
        """
        changed: list[str] = []
        with self._locked():
            for raw in self.list_jobs(limit=1000):
                if raw.get("status") not in {"dispatched", "running"}:
                    continue
                job_id = str(raw.get("job_id") or "")
                flow_id = str(raw.get("flow_id") or "")
                if flow_id:
                    try:
                        flow = EventFlowStore(self.root).load(flow_id)
                    except (OSError, TypeError, ValueError):
                        flow = None
                    if flow is not None:
                        mapped = flow.status if flow.status in {"completed", "failed", "cancelled"} else "running"
                        if raw.get('suspended_flows'):
                            mapped = 'running'  # The owning worker must first resume the parent.
                        if mapped != raw.get("status"):
                            job = JobRecord(**{key: value for key, value in raw.items()
                                               if key in JobRecord.__dataclass_fields__})
                            job.status = mapped
                            job.current_event_id = flow.current_event_id
                            job.ready_event_ids = list(flow.ready_node_ids)
                            job.completed_event_ids = list(flow.completed_node_ids)
                            if mapped == "failed":
                                job.error = "event_flow_failed:" + ",".join(flow.failed_node_ids)
                            self._save(job)
                            self._append_event("event_flow_status_projected", job, flow_id=flow_id)
                            changed.append(job_id)
                        continue
                from partner.social_video.integration import job_outcome as social_job_outcome
                social_outcome = social_job_outcome(self.root, str(raw.get('request') or ''))
                if social_outcome:
                    job = JobRecord(**{k: v for k, v in raw.items() if k in JobRecord.__dataclass_fields__})
                    job.status, job.error = social_outcome['status'], social_outcome['error']
                    self._save(job)
                    self._append_event('social_workflow_terminal', job)
                    changed.append(job_id)
                    continue
                instance = str(raw.get("assigned_instance") or "")
                tasks = self.root / "instances" / instance / "state" / "tasks"
                matched: dict[str, Any] | None = None
                matched_tasks: list[dict[str, Any]] = []
                if tasks.exists():
                    task_paths = sorted(tasks.glob("*/task_instance.json"),
                                        key=lambda path: path.stat().st_mtime, reverse=True)
                    for path in task_paths:
                        try:
                            value = json.loads(path.read_text(encoding="utf-8"))
                        except (OSError, ValueError, TypeError):
                            continue
                        metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
                        haystack = " ".join((str(value.get("user_message") or ""),
                                              str(value.get("root_user_request") or ""),
                                              str(metadata.get("inbox_message_id") or ""),
                                              str(metadata.get("application_job_id") or "")))
                        request_fingerprint = re.sub(
                            r"\s+", " ", str(raw.get("request") or "").strip()
                        )[:120]
                        normalized_haystack = re.sub(r"\s+", " ", haystack)
                        # Recovery may redrive the exact bounded request after
                        # stripping transport markers.  The durable original
                        # request is then the only remaining correlation key.
                        exact_redrive = bool(
                            request_fingerprint
                            and "[application_bounded=true]" in haystack
                            and request_fingerprint in normalized_haystack
                        )
                        if (job_id in haystack
                                or str(raw.get("message_id") or "") in haystack
                                or exact_redrive):
                            matched_tasks.append(value)
                if matched_tasks:
                    # Restarts can touch an orphaned Task after a later,
                    # successfully redriven Task has already settled.  File
                    # mtime is therefore not terminal authority.  Prefer the
                    # highest Receipt iteration, then a verified terminal,
                    # and use timestamps only as a final tie-breaker.
                    def terminal_rank(value: Mapping[str, Any]) -> tuple[int, int, str]:
                        metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
                        governance = metadata.get("manual_iteration_governance") or {}
                        receipt = governance.get("receipt") or {}
                        dimensions = governance.get("completion_dimensions") or {}
                        try:
                            iteration = int(receipt.get("iteration") or -1)
                        except (TypeError, ValueError):
                            iteration = -1
                        verified = int(bool(
                            dimensions.get("execution") == "succeeded"
                            and dimensions.get("verification") == "accepted"
                        ))
                        timestamp = str(value.get("updated_at") or value.get("created_at") or "")
                        return iteration, verified, timestamp

                    matched_tasks.sort(key=terminal_rank, reverse=True)
                    matched = matched_tasks[0]
                if not matched:
                    continue
                task_status = str(matched.get("completion_status") or "").lower()
                mapped = ({"done": "completed", "completed": "completed", "success": "completed",
                           "failed": "failed", "cancelled": "cancelled"}
                          .get(task_status, "running"))
                matched_metadata = (
                    matched.get("metadata")
                    if isinstance(matched.get("metadata"), dict) else {}
                )
                # The batch harness writes a preliminary ``done`` checkpoint
                # before Receipt creation, evidence archival and final truth
                # dimensions.  A modern Application Job is not terminal at
                # that checkpoint.  Sending there produces a generic message
                # without the PDF, then makes the real terminal invisible.
                if (mapped == "completed"
                        and str(matched_metadata.get("application_job_id") or "") == job_id):
                    settled = matched_metadata.get("manual_iteration_governance") or {}
                    receipt = settled.get("receipt") or {}
                    dimensions = settled.get("completion_dimensions") or {}
                    terminal_ready = bool(
                        settled.get("status")
                        and receipt.get("receipt_id")
                        and dimensions.get("execution")
                        and dimensions.get("verification")
                    )
                    if not terminal_ready:
                        mapped = "running"
                # A bounded application Job can span project → external
                # learning/self-evolution → resumed project. Intermediate
                # Task terminals are milestones, not the Job terminal.
                if mapped in {"completed", "failed"}:
                    try:
                        from partner.governance.instance_native import load_state
                        native = load_state(self.root, instance)
                        task_id = str(matched.get("task_id") or "")
                        matched_governance = (
                            matched_metadata.get("manual_iteration_governance") or {}
                        )
                        restart_orphan_terminal = bool(
                            mapped == "failed"
                            and matched_governance.get("status")
                            == "runtime_restart_orphaned"
                        )
                        # The Application projector and native terminal
                        # consumer are independent loops.  Never notify from a
                        # Task terminal until the native owner has consumed
                        # that exact task; otherwise a learning continuation
                        # can be decided milliseconds after a false "final"
                        # message was already queued.
                        # A redriven bounded Job can finish after a runtime
                        # restart without being written into ``last_task_id``.
                        # Once the native owner is explicitly WAITING, has no
                        # pending message, and still owns this Job, governance
                        # is authoritative enough to close it.  Keeping the
                        # older unconditional equality check stranded a real
                        # completed report in ``running`` forever.
                        owner_settled = bool(
                            native.phase in {"WAITING", "BLOCKED"}
                            and not native.pending_message_id
                            and native.active_application_job_id == job_id
                            and (native.phase == "WAITING"
                                 or "bounded application job completed" in native.reason
                                 # A supervisor restart can replace the
                                 # reason after the Task already wrote a full
                                 # accepted Receipt.  BLOCKED + no pending
                                 # message is then a settled owner, not an
                                 # intermediate continuation.
                                 or (
                                     mapped == "completed"
                                     and matched_governance.get("status")
                                     and (matched_governance.get("receipt") or {}).get("receipt_id")
                                     and (matched_governance.get("completion_dimensions") or {}).get("execution")
                                     == "succeeded"
                                     and (matched_governance.get("completion_dimensions") or {}).get("verification")
                                     == "accepted"
                                 ))
                        )
                        if (str(matched_metadata.get("application_job_id") or "") == job_id
                                and native.last_task_id != task_id
                                and not owner_settled
                                and not restart_orphan_terminal):
                            mapped = "running"
                        elif (not restart_orphan_terminal
                                and native.active_application_job_id == job_id
                                and native.phase in {
                                    "LEARNING_TRIGGERED", "LEARNING_DISPATCHED",
                                    "EXTERNAL_LEARNING_DISPATCHED", "RESUME_PROJECT",
                                    "PROJECT_DISPATCHED", "ADVANCE_PROJECT",
                                }):
                            mapped = "running"
                    except Exception:
                        pass
                if mapped == raw.get("status"):
                    continue
                job = JobRecord(**{key: value for key, value in raw.items()
                                   if key in JobRecord.__dataclass_fields__})
                job.status = mapped
                if mapped == "failed":
                    governance = ((matched.get("metadata") or {}).get("manual_iteration_governance") or {})
                    job.error = str(governance.get("error") or "event_task_failed")
                self._save(job)
                self._append_event("job_status_projected", job,
                                   task_id=str(matched.get("task_id") or ""),
                                   task_status=task_status)
                changed.append(job.job_id)
                if mapped in {"completed", "failed", "cancelled"}:
                    terminal = self.fabric.create(
                        "project.job_terminal", "project", root_event_id=job.root_event_id,
                        correlation_id=job.job_id, project_id=job.project_id, job_id=job.job_id,
                        instance_id=job.assigned_instance, channel=job.channel,
                        payload={"task_id": str(matched.get("task_id") or "")},
                    )
                    governance = ((matched.get("metadata") or {}).get("manual_iteration_governance") or {})
                    summary_text = self._governance_summary(governance, matched)
                    files = self._governance_artifacts(governance, matched)
                    completion = dict(governance.get("completion_dimensions") or {})
                    self.fabric.complete(terminal, EventSummary(
                        event_id=terminal.event_id,
                        status="completed" if mapped == "completed" else ("cancelled" if mapped == "cancelled" else "failed"),
                        headline=("项目工作已完成" if mapped == "completed" else "项目工作未通过"),
                        outcome=summary_text or job.error or mapped,
                        evidence_refs=[f"task:{matched.get('task_id')}"],
                        artifacts=[{"path": str(path)} for path in files],
                        completion=completion,
                        business_delta=bool(mapped == "completed" and summary_text),
                        notification_kind="milestone" if mapped == "completed" else "blocker",
                        human_message=summary_text,
                    ))
                else:
                    running = self.fabric.create(
                        "project.job_running", "project", root_event_id=job.root_event_id,
                        correlation_id=job.job_id, project_id=job.project_id, job_id=job.job_id,
                        instance_id=job.assigned_instance, channel=job.channel,
                        payload={"task_id": str(matched.get("task_id") or "")},
                    )
                    self.fabric.complete(running, EventSummary(
                        event_id=running.event_id, status="completed", headline="项目工作已开始",
                        outcome="真实 Task 已进入执行状态", evidence_refs=[f"task:{matched.get('task_id')}"],
                        notification_kind="routine",
                    ))
                # Every QQ application job returns through the accepting Bot,
                # including same-specialist work. Native application rounds
                # deliberately keep their step stream local, so restricting
                # this to cross-specialist jobs made 02 appear to disappear
                # after the initial queue acknowledgement.
                if (mapped in {"completed", "failed", "cancelled"}
                        and job.channel == "qq" and job.origin_instance):
                    self._queue_origin_notification(
                        job, matched,
                        sorted(matched_tasks, key=terminal_rank),
                    )
        return changed

    def _queue_origin_notification(
        self, job: JobRecord, task: Mapping[str, Any],
        journey_tasks: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        """Return one evidence-bound Job narrative through the accepting Bot.

        Event handlers may emit machine summaries, but the user owns a Job, not
        an Event registry.  Build the terminal copy from the whole Job journey
        so project work, external learning and self-evolution do not appear as
        unrelated or contradictory messages.
        """
        directory = self.state / "outbound" / job.origin_instance
        directory.mkdir(parents=True, exist_ok=True)
        governance = ((task.get("metadata") or {}).get("manual_iteration_governance") or {})
        summary = self._governance_summary(governance, task)
        files = self._governance_artifacts(governance, task)
        pdfs = [str(path) for path in files
                if str(path).lower().endswith(".pdf") and Path(str(path)).is_file()]
        from .narrative import terminal_message

        content = terminal_message(
            job=job.to_dict(), task=task, files=files, summary=summary,
            tasks=journey_tasks,
        )
        payload = {
            "schema_version": 1,
            "job_id": job.job_id,
            "to_user": job.sender_id,
            "content": content,
            "pdf_artifacts": pdfs[:1],
            "text_delivered": False,
            "created_at": _now(),
            "narrative_version": 2,
            "journey_task_ids": [str(row.get("task_id") or "") for row in journey_tasks],
        }
        target = directory / f"{job.job_id}.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    @staticmethod
    def _governance_artifacts(
        governance: Mapping[str, Any], task: Mapping[str, Any]
    ) -> list[str]:
        """Collect terminal artifacts without letting bookkeeping files hide reports.

        Governance ``files`` normally contains the receipt and project-state files,
        while the user-facing PDF lives in the immutable evidence archive.  An
        ``or`` fallback therefore silently discarded the PDF whenever ``files``
        was non-empty.  Merge every authoritative source and preserve order.
        """
        groups = (
            task.get("completed_files") or [],
            governance.get("files") or [],
            (governance.get("evidence_archive") or {}).get("artifacts") or [],
            (governance.get("receipt") or {}).get("artifacts") or [],
        )
        result: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for raw in group if isinstance(group, (list, tuple)) else []:
                value = str(raw or "").strip()
                if value and value not in seen:
                    seen.add(value)
                    result.append(value)
        return result

    @staticmethod
    def _governance_summary(
        governance: Mapping[str, Any], task: Mapping[str, Any]
    ) -> str:
        summary = str(governance.get("summary") or task.get("result_summary") or "").strip()
        if summary:
            return summary
        findings = (governance.get("receipt") or {}).get("findings") or []
        if isinstance(findings, list):
            for finding in reversed(findings):
                value = str(finding or "").strip()
                if value:
                    return value
        return ""

    def _enqueue_outbound_text(self, *, job_id: str, sender_id: str,
                                content: str, persona_hint: str = "",
                                project_id: str = "") -> None:
        """Write a QQ outbound file so the instance notification_poller
        delivers `content` to `sender_id` over QQ. Reuses the schema
        delivery.py / qq_bridge already handle.
        """
        if not sender_id or not content:
            return
        try:
            target_dir = Path(self.root) / "state/application/outbound" / (persona_hint or "")
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{job_id}.json"
            payload = {
                "schema_version": 2,
                "job_id": job_id,
                "to_user": sender_id,
                "content": content,
                "image_artifacts": [],
                "pdf_artifacts": [],
                "text_delivered": False,
                "delivery_state": "queued",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False),
                                encoding="utf-8")
            os.replace(temporary, target)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "[submit] outbound enqueue failed for %s: %s", job_id, exc)




    def submit(
        self,
        text: str,
        *,
        channel: str,
        sender_id: str,
        sender_name: str = "用户",
        persona_hint: str = "",
        project_id: str = "",
        attachments: Sequence[str | os.PathLike | Mapping[str, object]] = (),
        report_policy: str = "milestone",
        execution_constraints: Mapping[str, object] | None = None,
        mode: str = "",
        scope: str = "",
        preallocated_job_id: str = "",
    ) -> Submission:
        clean = str(text or "").replace("\x00", "").strip()
        incoming_constraints = dict(execution_constraints or {})
        # Explicit slash syntax is a transport-level flag, not an LLM intent
        # guess.  Web/API callers should prefer mode=benchmark plus the same
        # structured fields.
        if clean.startswith("/benchmark"):
            first = clean.splitlines()[0].split()
            mode = "benchmark"
            if len(first) > 1 and not incoming_constraints.get("benchmark_protocol_id"):
                incoming_constraints["benchmark_protocol_id"] = first[1].strip()
        if not clean and not attachments:
            return Submission(False, "", "", "", "rejected", "enqueue_work", "消息为空")

        # ---------------------------------------------------------------
        # 1. Intent flow: observe -> counter_read -> synthesize (LLM-driven).
        #    No regex / no hardcoded route / no hardcoded project_id.
        # ---------------------------------------------------------------
        from partner.events.interaction import (
            intent_observe, intent_counter_read, intent_synthesize,
            direct_answer as direct_answer_event,
        )
        from partner.projects.dynamic_project_registry import DynamicProjectRegistry
        from partner.runtime.status_context import runtime_status

        method_arm = str(incoming_constraints.get("method_arm")
                          or incoming_constraints.get("benchmark_method_arm")
                          or "")
        ablation_drop = str(incoming_constraints.get("ablation_drop") or "")
        intent_params_base = {
            "request": clean,
            "attachments": list(attachments),
            "project_id": project_id or persona_hint or "",
            "available_projects": self._list_available_projects(persona_hint),
            "mode": mode,
            "scope": scope,
            "method_arm": method_arm,
            "ablation_drop": ablation_drop,
        }

        try:
            ctx_for_llm = _intent_ctx(self.root, persona_hint, project_id, channel, sender_id)
            observe_out = intent_observe(ctx_for_llm, dict(intent_params_base))
            counter_out = intent_counter_read(ctx_for_llm, {
                **intent_params_base,
                "upstream": {"understand_1": observe_out},
            })
            synth_params = {
                **intent_params_base,
                "upstream": {"understand_1": observe_out, "understand_2": counter_out},
            }
            synth_out = intent_synthesize(ctx_for_llm, synth_params)
        except Exception as exc:  # noqa: BLE001
            return Submission(False, "", "", persona_hint, "rejected", "enqueue_work",
                              f"意图审议失败：{type(exc).__name__}: {exc}")

        synth_sem = (synth_out or {}).get("semantic_output") or {}
        route = str(synth_sem.get("route") or "").strip()
        dispatch_target = str(synth_sem.get("dispatch_target") or "").strip()
        warm_reply = str(synth_sem.get("warm_reply") or "").strip()
        payload = dict(synth_sem.get("payload") or {})
        # Surface improvement mode from payload so downstream routing picks
        # the dedicated partner dispatch target.
        explicit_mode = str(payload.get("mode") or "").strip()
        if explicit_mode in {"self_improvement", "learning_improvement"}:
            dispatch_target = "partner_" + explicit_mode
        # Improvement modes are explicit operator intents, not subject to the
        # LLM's direct_answer heuristic.  Force the bounded project path so the
        # dedicated improvement flow is always selected.
        if mode in {"self_improvement", "learning_improvement"} and scope == "partner":
            route = "project_iteration"
        if mode == "benchmark":
            route = "project_iteration"
            dispatch_target = project_id or dispatch_target or persona_hint or "benchmark"

        if route not in {"direct_answer", "project_iteration"}:
            return Submission(False, "", "", persona_hint, "rejected", "enqueue_work",
                              f"意图审议未给出有效 route：{route!r}")

        # ---------------------------------------------------------------
        # 2a. direct_answer: synchronously run direct_answer event, return text.
        # ---------------------------------------------------------------
        if route == "direct_answer":
            try:
                ans_out = direct_answer_event(ctx_for_llm, {
                    "request": clean,
                    "project_id": dispatch_target or persona_hint or "",
                    "intent_contract": synth_sem,
                })
                answer_text = str((ans_out or {}).get("answer") or warm_reply or
                                  "已收到你的问题，但当前没有可回复的具体内容。").strip()
                if channel == "qq" and sender_id:
                    self._enqueue_outbound_text(
                        job_id=f"da-{uuid.uuid4().hex[:16]}",
                        sender_id=sender_id, content=answer_text,
                        persona_hint=persona_hint or "",
                        project_id=dispatch_target or persona_hint or "",
                    )
                return Submission(True, "", dispatch_target or persona_hint or "",
                                  persona_hint, "completed", "direct_answer",
                                  answer_text, "")
            except Exception as exc:  # noqa: BLE001
                fallback_text = warm_reply or f"已收到；详细回复生成失败：{exc}"
                if channel == "qq" and sender_id:
                    self._enqueue_outbound_text(
                        job_id=f"da-{uuid.uuid4().hex[:16]}",
                        sender_id=sender_id, content=fallback_text,
                        persona_hint=persona_hint or "",
                        project_id=persona_hint or "",
                    )
                return Submission(True, "", "", persona_hint, "completed", "direct_answer",
                                  fallback_text, "")

        # ---------------------------------------------------------------
        # 2b. project_iteration: prepare run state, enqueue flow.
        # ---------------------------------------------------------------
        if not dispatch_target:
            dispatch_target = project_id or persona_hint or "project_iteration"

        # When dispatch points at social/video, materialise runs/<id>/owner.json
        # so downstream stages can verify ownership via trusted_request.
        if dispatch_target in {"browser_video_learning", "xhs_authoring"}:
            from partner.social_video.integration import ensure_edge
            import uuid as _uuid
            run_id = (('xhs-' if dispatch_target == 'xhs_authoring' else 'video-')
                      + _uuid.uuid4().hex[:12])
            from partner.social_video.integration import data_root, digest as _digest, write_json as _write_json
            base = data_root(self.root)
            run_dir = base / 'runs' / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            owner = _digest({'channel': channel, 'sender': sender_id, 'instance': persona_hint})
            params = {'run_id': run_id}
            url = str(payload.get('url') or '').strip()
            topic = str(payload.get('topic') or '').strip()
            media = payload.get('media') or []
            if dispatch_target == 'browser_video_learning':
                if not url:
                    return Submission(False, "", "", persona_hint, "rejected", "enqueue_work",
                                      "video 派发目标需要 url 字段")
                params['url'] = url
            else:
                params['topic'] = topic or clean
                params['media'] = [str(m) for m in media] if isinstance(media, list) else []
            _write_json(run_dir / 'owner.json',
                        {'owner': owner, 'event': dispatch_target, 'params': params})
            # Attach dispatch info so the flow worker threads run_id into stage params.
            intent_params_base['run_id'] = run_id
            intent_params_base['dispatch_event'] = dispatch_target

        job_id = preallocated_job_id if preallocated_job_id else f"job_{uuid.uuid4().hex[:16]}"
        assigned = persona_hint or ""
        intent_contract = dict(synth_sem) if synth_sem else {}
        intent_contract['original_request'] = clean
        intent_contract['mode'] = mode
        intent_contract['scope'] = scope
        intent_contract['dispatch_target'] = dispatch_target
        intent_contract['warm_reply'] = warm_reply
        # Development rollout is scoped to explicitly configured instances.
        # This only changes a new project request; it never schedules work itself.
        cycle_policy = self._application_config().get('bounded_evolution_cycle') or {}
        constraint_input = dict(incoming_constraints)
        if (persona_hint in cycle_policy.get('instances', [])
                and dispatch_target not in {'browser_video_learning', 'xhs_authoring', 'direct_answer'}):
            defaults = {'evolution_cycle': True, 'max_rounds': 2,
                        'evolution_apply': cycle_policy.get('apply') is True,
                        'action_seconds': cycle_policy.get('action_seconds', 300)}
            constraint_input = {**defaults, **constraint_input}
        intent_contract['execution_constraints'] = (
            dict(validate_constraints(constraint_input))
            if constraint_input else {}
        )
        if project_id:
            intent_contract['explicit_project_id'] = project_id
        benchmark_run_id = ""
        benchmark_protocol_id = ""
        checkpoint_policy_ref = ""
        if mode == "benchmark":
            benchmark_protocol_id = str(
                intent_contract['execution_constraints'].get('benchmark_protocol_id') or "")
            if not benchmark_protocol_id:
                return Submission(False, "", "", persona_hint, "rejected", "benchmark_experiment",
                                  "Benchmark 运行缺少 benchmark_protocol_id", "")
            benchmark_run_id = f"bench_{uuid.uuid4().hex[:16]}"
            checkpoint_policy_ref = str(
                intent_contract['execution_constraints'].get('checkpoint_policy') or "protocol")
            intent_contract['benchmark'] = {
                'run_id': benchmark_run_id, 'run_mode': 'benchmark',
                'protocol_id': benchmark_protocol_id,
                'protocol_version': str(intent_contract['execution_constraints'].get(
                    'benchmark_protocol_version') or ''),
                'inputs': dict(intent_contract['execution_constraints'].get('benchmark_inputs') or {}),
                'guardrail_results': dict(intent_contract['execution_constraints'].get(
                    'benchmark_guardrail_results') or {}),
                'allow_external_judges': bool(intent_contract['execution_constraints'].get(
                    'benchmark_allow_external_judges')),
                'checkpoint_policy_ref': checkpoint_policy_ref,
            }

        # Decide flow name based on dispatch_target.
        # Improvement modes take precedence over project routing when explicitly set.
        mode = intent_contract.get('mode') or ''
        scope = intent_contract.get('scope') or ''
        if mode == 'benchmark':
            flow_name = 'benchmark_experiment'
        elif mode == 'self_improvement' and scope == 'partner':
            flow_name = 'self_improvement_cycle'
            dispatch_target = 'partner_self_improvement'
        elif mode == 'learning_improvement' and scope == 'partner':
            flow_name = 'learning_improvement_cycle'
            dispatch_target = 'partner_learning_improvement'
        elif dispatch_target in {"browser_video_learning", "xhs_authoring"}:
            flow_name = dispatch_target
        elif dispatch_target == "direct_answer":
            flow_name = "direct_answer"
        else:
            flow_name = "project_iteration"

        # Explicit, bounded two-round workflow; semantic project selection still
        # belongs to the intent Events, not keyword routing.
        if intent_contract['execution_constraints'].get('evolution_cycle') and not mode:
            flow_name = 'project_cycle'

        try:
            received = self.fabric.create(
                "interaction.message_received", "interaction", correlation_id=job_id,
                project_id=dispatch_target, job_id=job_id, instance_id=assigned,
                channel=channel, payload={"sender_name": sender_name, "has_attachments": bool(attachments)},
            )
            job = JobRecord(
                job_id=job_id, project_id=dispatch_target,
                title=(clean.splitlines()[0][:80] or dispatch_target or clean[:40]),
                request=clean, route=flow_name, channel=channel,
                sender_id=sender_id, sender_name=sender_name, persona_hint=persona_hint,
                origin_instance=persona_hint if persona_hint in PROJECTS else "",
                assigned_instance=assigned,
                intake_instance_id=persona_hint or "",
                created_at=_now(), updated_at=_now(), report_policy=report_policy,
                attachments=[{"path": str(item.get("path")) if isinstance(item, Mapping) else item}
                             for item in attachments],
                intent_contract_path="",
                intent_contract=intent_contract,
                intent_model_calls=sum(out.get('model_calls', 1) for out in (observe_out, counter_out, synth_out)),
                run_mode='benchmark' if mode == 'benchmark' else 'normal',
                benchmark_run_id=benchmark_run_id,
                benchmark_protocol_id=benchmark_protocol_id,
                checkpoint_policy_ref=checkpoint_policy_ref,
                evaluation_visibility='hidden_until_terminal' if mode == 'benchmark' else '',
            )
            job.root_event_id = received.event_id
            self.fabric.complete(received, EventSummary(
                event_id=received.event_id, status="completed",
                headline="收到用户请求", outcome=clean[:240], notification_kind="routine",
            ))
            event_catalog = build_catalog(workspace=self.root)
            event_catalog.snapshot(self.root / "state/event_catalog" / f"catalog_{event_catalog.version}.json")
            flow_definition = build_flow_registry().get(flow_name)
            flow_state = EventFlowController(EventFlowStore(self.root)).start(
                flow_definition, catalog_version=event_catalog.version,
                task_id=job.job_id, project_id=dispatch_target, instance_id=assigned,
                run_context={
                    'run_mode': job.run_mode, 'benchmark_run_id': benchmark_run_id,
                    'benchmark_protocol_id': benchmark_protocol_id,
                    'benchmark_arm_id': '', 'checkpoint_policy_ref': checkpoint_policy_ref,
                    'evaluation_visibility': job.evaluation_visibility,
                    'catalog_version': event_catalog.version,
                } if mode == 'benchmark' else {},
            )
            flow_state.root_event_id = received.event_id
            EventFlowStore(self.root).save(flow_state)
            job.event_catalog_version = event_catalog.version
            job.flow_id = flow_state.flow_id
            job.flow_type = flow_state.flow_type
            job.ready_event_ids = list(flow_state.ready_node_ids)
            with self._locked():
                self._save(job)
                self._append_event("job_accepted", job, route=flow_name, channel=channel)
                self._dispatch_locked(job)
            # Reply: warm_reply from synthesize, with fallback
            reply = warm_reply or f"已派发到 {flow_name}。"
            return Submission(True, job.job_id, dispatch_target, assigned,
                              job.status, flow_name, reply, "")
        except Exception as exc:  # noqa: BLE001
            return Submission(False, "", "", persona_hint, "rejected", flow_name,
                              f"派发失败：{type(exc).__name__}: {exc}", "")

    def submit_native(
        self,
        text: str,
        *,
        instance_id: str,
        project_id: str,
        kind: str = "project",
        channel: str = "local",
        sender_id: str = "",
    ) -> Submission:
        """Create a native continuation Job directly, skipping the LLM intent
        flow.

        Native continuation already knows its partner identity, project, and
        branch; re-running intent_observe/counter_read/synthesize would only
        re-burn LLM calls and could silently re-route the task.  This mirrors
        submit()'s job+flow creation (interaction.message_received -> JobRecord
        -> EventFlowController.start -> _save -> _dispatch_locked) but with the
        flow_name and dispatch_target fixed from ``kind``.
        """
        flow_name = {
            "learning": "self_improvement_cycle",
            "external_learning": "learning_improvement_cycle",
        }.get(kind, "project_iteration")
        dispatch_target = project_id or "project_iteration"
        assigned = instance_id
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        # Native continuation must carry the instance_native marker so the
        # terminal bridge / handle_terminal recognise it as autonomous (not an
        # ordinary manual message).  The marker is a property of the native
        # dispatch, not the caller's request text.
        marker = f"\n\n[instance_native=true] [native_kind={kind}] [project_id={project_id}]"
        text = (text or "").rstrip() + marker
        intent_contract = {
            "original_request": text,
            "mode": ({"learning": "self_improvement",
                      "external_learning": "learning_improvement"}
                     .get(kind, "")),
            "scope": "partner" if kind in {"learning", "external_learning"} else "",
            "dispatch_target": dispatch_target,
            "execution_constraints": {},
            "explicit_project_id": project_id,
        }
        try:
            received = self.fabric.create(
                "interaction.message_received", "interaction", correlation_id=job_id,
                project_id=dispatch_target, job_id=job_id, instance_id=assigned,
                channel=channel or "local",
                payload={"sender_name": f"Partner{instance_id}项目内部续跑",
                         "has_attachments": False},
            )
            job = JobRecord(
                job_id=job_id, project_id=dispatch_target,
                title=(text.splitlines()[0][:80] or dispatch_target),
                request=text, route=flow_name, channel=channel or "local",
                sender_id=(sender_id or f"partner_{instance_id}_self"),
                sender_name=f"Partner{instance_id}项目内部续跑",
                persona_hint=instance_id,
                origin_instance=instance_id if instance_id in PROJECTS else "",
                assigned_instance=assigned,
                intake_instance_id=instance_id,
                created_at=_now(), updated_at=_now(), report_policy="milestone",
                attachments=[], intent_contract_path="",
                intent_contract=intent_contract, intent_model_calls=0,
            )
            job.root_event_id = received.event_id
            self.fabric.complete(received, EventSummary(
                event_id=received.event_id, status="completed",
                headline="收到内部续跑请求", outcome=text[:240],
                notification_kind="routine",
            ))
            event_catalog = build_catalog(workspace=self.root)
            event_catalog.snapshot(self.root / "state/event_catalog" / f"catalog_{event_catalog.version}.json")
            flow_definition = build_flow_registry().get(flow_name)
            if flow_definition is None:
                return Submission(False, "", "", instance_id, "rejected", flow_name,
                                  f"未注册的续跑 flow：{flow_name}")
            flow_state = EventFlowController(EventFlowStore(self.root)).start(
                flow_definition, catalog_version=event_catalog.version,
                task_id=job.job_id, project_id=dispatch_target, instance_id=assigned,
            )
            flow_state.root_event_id = received.event_id
            EventFlowStore(self.root).save(flow_state)
            job.event_catalog_version = event_catalog.version
            job.flow_id = flow_state.flow_id
            job.flow_type = flow_state.flow_type
            job.ready_event_ids = list(flow_state.ready_node_ids)
            with self._locked():
                self._save(job)
                self._append_event("job_accepted", job, route=flow_name, channel="local")
                self._dispatch_locked(job)
            return Submission(True, job.job_id, dispatch_target, assigned,
                              job.status, flow_name, "内部续跑已派发", "")
        except Exception as exc:  # noqa: BLE001
            return Submission(False, "", "", instance_id, "rejected", flow_name,
                              f"内部续跑派发失败：{type(exc).__name__}: {exc}", "")

    def _list_available_projects(self, persona_hint: str = "") -> list[dict[str, str]]:
        """Return concise summary of projects this instance could route to."""
        from partner.projects.dynamic_project_registry import DynamicProjectRegistry
        registry = DynamicProjectRegistry(workspace_root=self.root)
        out: list[dict[str, str]] = []
        for project_id in PROJECTS.values():
            pid = project_id[0] if isinstance(project_id, tuple) else str(project_id)
            out.append({"project_id": pid,
                        "title": PROJECT_TITLES.get(pid, pid)})
        try:
            for row in registry.list_projects():
                pid = getattr(row, "project_id", None)
                if pid and not any(x["project_id"] == pid for x in out):
                    out.append({"project_id": pid,
                                "title": getattr(row, "summary", "") or pid})
        except Exception:
            pass
        return out

    def _dispatch_locked(self, job: JobRecord) -> None:
        # One active action per project. Other projects may run concurrently;
        # process admission remains controlled by the resource-adaptive arbiter.
        for other in self.list_jobs(project_id=job.project_id, limit=1000):
            if other.get("job_id") != job.job_id and other.get("status") in {"dispatched", "running"}:
                job.status = "queued"
                self._save(job)
                return
        selection = NextEventSelector(self.fabric).select([{
            "event_id": job.root_event_id,
            "correlation_id": job.job_id,
            "next_event_candidates": [{
                "event_type": "project.job_dispatch", "series": "project",
                "project_id": job.project_id,
                "concurrency_key": f"project:{job.project_id}",
                "prerequisites": [f"summary:{job.root_event_id}"],
            }],
        }])
        if not selection.selected:
            job.status = "queued"
            job.error = "selector deferred project dispatch"
            self._save(job)
            return
        if job.flow_id and EventFlowStore(self.root).path(job.flow_id).is_file():
            job.status = "dispatched"
            job.message_id = f"flow:{job.flow_id}"
            self._append_event("job_dispatched", job, instance_id=job.assigned_instance,
                               flow_id=job.flow_id, selection_id=selection.selection_id)
            dispatched = self.fabric.create(
                "project.job_dispatched", "project", root_event_id=job.root_event_id,
                correlation_id=job.job_id, project_id=job.project_id, job_id=job.job_id,
                instance_id=job.assigned_instance, channel=job.channel,
                payload={"flow_id": job.flow_id},
            )
            self.fabric.complete(dispatched, EventSummary(
                event_id=dispatched.event_id, status="completed", headline="后台工作已投递",
                outcome=f"由 {job.assigned_instance} 专业角色执行", notification_kind="routine",
            ))
        else:
            job.status = "failed"
            job.error = "event_flow_missing"
            self._append_event("job_dispatch_failed", job, error=job.error)
        self._save(job)

    def dispatch_ready(self) -> list[str]:
        """Dispatch one queued job per idle project; callable by supervisor."""
        self.refresh_jobs()
        dispatched: list[str] = []
        with self._locked():
            jobs = list(reversed(self.list_jobs(limit=1000)))
            active_projects = {str(j.get("project_id")) for j in jobs if j.get("status") in {"dispatched", "running"}}
            for raw in jobs:
                if raw.get("status") != "queued" or raw.get("project_id") in active_projects:
                    continue
                job = JobRecord(**{key: value for key, value in raw.items() if key in JobRecord.__dataclass_fields__})
                self._dispatch_locked(job)
                if job.status == "dispatched":
                    dispatched.append(job.job_id)
                    active_projects.add(job.project_id)
        return dispatched


def _intent_ctx(root, persona_hint, project_id, channel, sender_id):
    """Context for intent events. Must carry a DirectAdapter for cognitive Events."""
    from partner.adapters.adapter import DirectAdapter
    class _Stub:
        pass
    ctx = _Stub()
    ctx.workspace = str(root)
    ctx.project_id = project_id or persona_hint or ""
    ctx.instance_id = persona_hint or ""
    ctx.job_id = ""
    ctx.channel = channel or ""
    ctx.sender_id = sender_id or ""
    ctx.intake_instance_id = persona_hint or ""
    ctx.adapter = DirectAdapter(workspace_path=str(root))
    ctx.event_deadline = None
    return ctx
