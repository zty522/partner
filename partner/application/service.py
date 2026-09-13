"""One application service for every Partner user interface.

This layer owns user intent intake and durable background Jobs.  It does not
execute tools: dispatch pins a canonical Event Flow consumed by EventWorker.
No legacy inbox, batch planner, or Harness participates in production.
"""

from __future__ import annotations

import fcntl
import json
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

from .models import JobRecord, Submission


PROJECT_TO_INSTANCE = {project_id: instance_id for instance_id, (project_id, _title) in PROJECTS.items()}
PROJECT_TITLES = {project_id: title for _instance_id, (project_id, title) in PROJECTS.items()}

_PROJECT_MARKERS = {
    "xiaohongshu_operations": ("小红书", "公众号", "社交账号", "内容运营"),
    "molecular_generation": ("分子生成", "靶点", "对接", "targetdiff", "药物", "molecule"),
    "molecular_dynamics_study": ("分子动力学", "amber", "gromacs", "md 模拟", "md模拟"),
    "literature_github_learning": ("github", "论文", "文献", "源码", "外部学习", "主动学习"),
    "hermes_partner_explore": ("partner 自进化", "自进化", "harness", "candidate", "partner框架"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(value: str | os.PathLike) -> Path:
    path = Path(value).expanduser().resolve()
    if path.parent.name == "instances":
        return path.parent.parent
    return path


def _route(text: str) -> str:
    value = str(text or "").strip().lower()
    if re.search(r"(^|\s)/(pause|resume|cancel|stop)\b", value) or any(x in value for x in ("暂停任务", "恢复任务", "取消任务")):
        return "modify_work"
    if any(x in value for x in ("同意 candidate", "批准 candidate", "拒绝 candidate", "批准修复", "同意修复")):
        return "approval_response"
    if any(x in value for x in ("现在在做什么", "进展如何", "项目状态", "任务状态", "有哪些任务",
                                  "有哪些项目", "哪些项目", "项目正在运行", "查看进度")):
        return "project_query"
    conversational = ("你好", "在吗", "谢谢", "早上好", "晚上好", "告诉我你能做什么",
                      "告诉我你现在可以", "介绍一下你自己")
    if len(value) <= 80 and any(x in value for x in conversational):
        return "chat_reply"
    return "enqueue_work"


def _project(text: str, persona_hint: str = "", explicit: str = "",
            *, conversation_id: str = "",
            feature_flag: str = "legacy",
            workspace_root: "Path" = None) -> Union[str, "ProjectDecision"]:
    """Resolve a request to a project_id (or ProjectDecision under "new").

    ADR 0099: legacy flag returns a project_id string (existing 5-instance
    × 5-project keyword matching — unchanged).  When ``feature_flag ==
    "new"``, returns a :class:`ProjectDecision` so the caller can branch
    on ``existing_project`` / ``new_project_kind`` / ``chat_reply``.
    """
    if feature_flag == "new":
        return _project_via_classifier(
            text, workspace_root=workspace_root,
            persona_hint=persona_hint, explicit=explicit,
            conversation_id=conversation_id,
        )
    # ----- legacy path (unchanged) -----
    if explicit in PROJECT_TO_INSTANCE:
        return explicit
    value = str(text or "").lower()
    scores = {
        project_id: sum(1 for marker in markers if marker.lower() in value)
        for project_id, markers in _PROJECT_MARKERS.items()
    }
    winner = max(scores, key=scores.get)
    if scores[winner] > 0:
        return winner
    if persona_hint in PROJECTS:
        return PROJECTS[persona_hint][0]
    return PROJECTS["04"][0]
    if feature_flag == "new":
        return _project_via_classifier(
            text, persona_hint=persona_hint, explicit=explicit,
            conversation_id=conversation_id,
        )
    # ----- legacy path (unchanged) -----
    if explicit in PROJECT_TO_INSTANCE:
        return explicit
    value = str(text or "").lower()
    scores = {
        project_id: sum(1 for marker in markers if marker.lower() in value)
        for project_id, markers in _PROJECT_MARKERS.items()
    }
    winner = max(scores, key=scores.get)
    if scores[winner] > 0:
        return winner
    if persona_hint in PROJECTS:
        return PROJECTS[persona_hint][0]
    return PROJECTS["04"][0]


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
        if project not in PROJECT_TO_INSTANCE and persona_hint in PROJECTS:
            project = PROJECTS[persona_hint][0]
        if project not in PROJECT_TO_INSTANCE:
            return {}
        directory = self.root / "share" / "projects" / project

        def _bounded_text(name: str, limit: int = 2400) -> str:
            try:
                return (directory / name).read_text(encoding="utf-8")[:limit]
            except OSError:
                return ""

        context: dict[str, Any] = {
            "instance_id": PROJECT_TO_INSTANCE[project],
            "project_id": project,
            "project_title": PROJECT_TITLES[project],
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
        job.updated_at = _now()
        path = self.jobs_dir / f"{job.job_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _append_event(self, event_type: str, job: JobRecord, **extra: Any) -> None:
        row = {"schema_version": 1, "event_type": event_type, "at": _now(),
               "job_id": job.job_id, "project_id": job.project_id,
               "status": job.status, **extra}
        with (self.state / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def list_jobs(self, *, project_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self.jobs_dir.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if project_id and row.get("project_id") != project_id:
                continue
            rows.append(row)
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows[: max(1, limit)]

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
                raw = json.loads(path.read_text(encoding="utf-8"))
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
    ) -> Submission:
        clean = str(text or "").replace("\x00", "").strip()
        if not clean and not attachments:
            return Submission(False, "", "", "", "rejected", "enqueue_work", "消息为空")
        route = _route(clean)
        from partner.social_video.integration import intake as social_intake
        try:
            social_request = social_intake(self.root, clean, channel=channel, sender_id=sender_id,
                                           instance=persona_hint, attachments=attachments)
        except (ValueError, OSError, KeyError) as exc:
            return Submission(False, "", "", "", "rejected", "enqueue_work", str(exc))
        if social_request:
            clean = social_request
            project_id = "xiaohongshu_operations"
            route = "enqueue_work"
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        intent_result: dict[str, Any] = {}
        intent_contract: dict[str, Any] = {}
        # ADR 0099: read the feature flag once and propagate to every
        # ``_project`` call below.  ``conversation_id`` is derived from
        # the sender_id so a follow-up in the same QQ chat hits the
        # session-context lookup (L2).
        _app_flags = self._feature_flag_application()
        _feature_flag = _app_flags["instance_project_binding"]
        _conversation_id = f"{channel}:{persona_hint}:{sender_id}" if sender_id else ""
        # ADR 0099: when the new flag is on, run the classifier once
        # up front and remember the kind so flow_name routing below can
        # dispatch to NEW_PROJECT when needed.  Legacy flag skips this.
        _classification_kind = ""
        _classification_hint = ""
        if _feature_flag == "new":
            try:
                from partner.application.project_classifier import (
                    ProjectClassifier, Kind as _Kind,
                )
                from partner.projects.dynamic_project_registry import (
                    DynamicProjectRegistry,
                )
                from partner.projects.session_context import SessionContext
                from partner.workspace.workspace_layout import (
                    workspace_root_path,
                )
                _ws = self.root
                _reg = DynamicProjectRegistry(workspace_root=_ws)
                _sess = SessionContext(workspace_root=_ws)
                _clf = ProjectClassifier(
                    workspace_root=_ws,
                    session_context=_sess,
                    registry=_reg,
                    semantic_backend="none",
                )
                _decision = _clf.classify(
                    request=clean,
                    conversation_id=_conversation_id,
                    explicit_project_id=project_id,
                )
                _classification_kind = _decision.kind.value
                _classification_hint = _decision.project_hint or ""
            except Exception:
                # Classifier unavailable — leave kind empty; flow_name
                # falls through to the default "project_iteration".
                pass
        # The accepting Bot is stable routing evidence. Resolve its project
        # before enrichment so natural messages such as “继续上次实验” can use
        # the real project brief and latest Receipt.
        _project_raw = _project(
            clean, persona_hint, project_id,
            conversation_id=_conversation_id,
            feature_flag=_feature_flag,
            workspace_root=self.root,
        )
        # ADR 0099: under the "new" flag, _project returns a
        # ProjectDecision rather than a project_id string.  Resolve
        # decision into a project_id here so downstream legacy code
        # (modify_work, project_query, etc.) keeps working unchanged.
        contextual_project = _normalise_project_result(
            _project_raw, workspace_root=self.root, instance_id=persona_hint,
        )
        if _feature_flag == 'new':
            _classification_kind = getattr(getattr(_project_raw, 'kind', None), 'value', '')
            if _classification_kind == 'chat_reply':
                route = 'chat_reply'
        if route == "modify_work":
            lowered = clean.lower()
            action = ("cancel" if ("cancel" in lowered or "取消" in clean) else
                      "resume" if ("resume" in lowered or "恢复" in clean or "继续任务" in clean) else
                      "pause")
            candidates = [row for row in self.list_jobs(project_id=contextual_project, limit=50)
                          if row.get("status") in {"queued", "dispatched", "running", "paused"}]
            if not candidates:
                return Submission(False, "", contextual_project,
                                  PROJECT_TO_INSTANCE[contextual_project], "rejected", route,
                                  "当前项目没有可控制的后台工作。")
            target = candidates[0]
            controlled = self.control_job(str(target["job_id"]), action)
            wording = {"pause": "暂停", "resume": "继续", "cancel": "取消"}[action]
            return Submission(bool(controlled.get("ok")), str(target["job_id"]), contextual_project,
                              str(target.get("assigned_instance") or ""),
                              str(controlled.get("status") or target.get("status") or ""), route,
                              (f"已记录{wording}请求，将在安全检查点生效。" if controlled.get("ok")
                               else "操作未生效：" + str(controlled.get("error") or "未知原因")))
        if route == "enqueue_work":
            try:
                intent_result = self._enrich_intent(
                    clean,
                    job_id,
                    project_id=contextual_project,
                    persona_hint=persona_hint,
                )
                intent_contract = dict(intent_result.get("intent") or {})
            except Exception as exc:
                intent_result = {"ok": False, "status": "failed", "error": type(exc).__name__}
        # Deterministic command recognition remains constitutional.  For an
        # ordinary work request the model may refine the project hint, but it
        # cannot turn an actionable request into chat or grant new authority.
        model_mode = str(intent_contract.get("interaction_mode") or "")
        if route == "enqueue_work" and model_mode in {"modify_work", "approval_response", "cancel_or_pause"}:
            route = model_mode
        project_signal = clean + "\n" + str(intent_contract.get("project_hint") or "")
        # Explicit project selection and the accepting specialist are stable
        # routing evidence.  A strong project name in the user's own words may
        # still cross-route (for example asking bot 03 to run a molecular-
        # generation experiment), but an LLM-invented project_hint may not
        # override the explicit channel when the original request is neutral.
        # Without this boundary, a malformed intent pass sent a 03 MD
        # experiment to the 05 Partner-code lane.
        if _feature_flag == "new":
            _raw = _project_raw
        elif project_id or persona_hint:
            _raw = _project(
                clean, persona_hint, project_id,
                conversation_id=_conversation_id,
                feature_flag=_feature_flag,
                workspace_root=self.root,
            )
        else:
            _raw = _project(
                project_signal, persona_hint, project_id,
                conversation_id=_conversation_id,
                feature_flag=_feature_flag,
                workspace_root=self.root,
            )
        selected_project, _classification_extra = _normalise_project_result_full(
            _raw, workspace_root=self.root, instance_id=persona_hint,
        )
        # The selected project chooses its current specialist. A channel's bot
        # identity is a stable fallback, not ownership of every topic.
        # ADR 0100: assigned_instance is ADVISORY only — it records the
        # QQ channel / instance the user originally chatted on.  Actual
        # job execution is performed by an independent worker process
        # (see partner/runtime/shared_worker.py, scheduled for Phase 6)
        # which picks jobs off the queue without consulting this field.
        # We just stamp the inbound persona_hint so downstream message
        # push can route end-of-Flow notifications back to the right Bot.
        assigned = persona_hint or ""
        # Phase 5.3 (migration_plan_0100): legacy keyword fallback is kept
        # so the legacy flag still produces sensible values.  Under the
        # new flag the value above is what the worker uses.
        if _feature_flag != "new" or not assigned:
            assigned = PROJECT_TO_INSTANCE.get(selected_project, assigned)
            if not assigned and _feature_flag != "new":
                # Legacy path: old behaviour — fall through to PROJECTS lookup
                for cand_instance, (proj_id, _title) in PROJECTS.items():
                    if proj_id == selected_project:
                        assigned = cand_instance
                        break
        if project_id:
            intent_contract['explicit_project_id'] = project_id
        job = JobRecord(
            job_id=job_id, project_id=selected_project,
            title=(clean.splitlines()[0][:80] or
                   PROJECT_TITLES.get(selected_project, selected_project) or
                   clean[:40]), request=clean,
            route=route, channel=channel, sender_id=sender_id,
            persona_hint=persona_hint,
            origin_instance=persona_hint if persona_hint in PROJECTS else "",
            assigned_instance=assigned,
            # ADR 0100: intake_instance_id records which QQ Bot (and
            # therefore which instance) the user originally chatted
            # with.  Worker pool uses this to route terminal-flow
            # notifications back to the right Bot via the polling
            # delivery loop.  Defaults to persona_hint (== sender's
            # selected instance); empty only when no persona_hint.
            intake_instance_id=persona_hint or "",
            created_at=_now(), updated_at=_now(), report_policy=report_policy,
            attachments=[{"path": str(item.get("path") if isinstance(item, Mapping) else item)} for item in attachments],
            intent_contract_path=str(intent_result.get("contract_path") or ""),
            intent_contract=intent_contract,
            intent_model_calls=int(intent_result.get("_model_calls") or 0),
        )
        received = self.fabric.create(
            "interaction.message_received", "interaction", correlation_id=job.job_id,
            project_id=selected_project, job_id=job.job_id, instance_id=assigned,
            channel=channel, payload={"sender_name": sender_name, "has_attachments": bool(attachments)},
        )
        job.root_event_id = received.event_id
        self.fabric.complete(received, EventSummary(
            event_id=received.event_id, status="completed", headline="收到用户请求",
            outcome=clean[:240], notification_kind="routine",
            next_event_candidates=[],
        ))
        intent_event = self.fabric.create(
            "interaction.intent_enrichment", "interaction", root_event_id=received.event_id,
            parent_event_id=received.event_id, correlation_id=job.job_id,
            project_id=selected_project, job_id=job.job_id, instance_id=assigned,
            channel=channel, payload={"strategy_id": "user_intent_enrichment_v3_three_pass"},
        )
        model_ok = False
        self.fabric.complete(intent_event, EventSummary(
            event_id=intent_event.event_id,
            # Event completion and model quality are orthogonal. The Event
            # completed even when it had to emit a conservative fallback.
            status="completed",
            headline="三遍意图理解已排入 Event Flow",
            outcome=clean[:500],
            claims=[{"name": "model_calls", "value": job.intent_model_calls},
                    {"name": "model_interpretation_ok", "value": model_ok}],
            evidence_refs=([job.intent_contract_path] if job.intent_contract_path else []),
            notification_kind="routine",
            next_event_candidates=[{
                "event_type": "interaction.intent_routed", "series": "interaction",
                "project_id": selected_project, "concurrency_key": f"intent:{job.job_id}",
                "prerequisites": [f"summary:{intent_event.event_id}"],
            }],
        ))
        intent_required_but_failed = False
        routed = self.fabric.create(
            "interaction.intent_routed", "interaction", root_event_id=received.event_id,
            parent_event_id=intent_event.event_id, correlation_id=job.job_id,
            project_id=selected_project, job_id=job.job_id, instance_id=assigned, channel=channel,
            payload={"route": route},
        )
        self.fabric.complete(routed, EventSummary(
            event_id=routed.event_id, status="completed", headline="请求已理解并路由",
            outcome=f"{route} → {selected_project} → {assigned}",
            claims=[{"name": "route", "value": route}, {"name": "assigned_instance", "value": assigned}],
            notification_kind="routine",
            next_event_candidates=[{
                "event_type": "project.job_dispatch", "series": "project",
                "project_id": selected_project, "concurrency_key": f"project:{selected_project}",
                "prerequisites": [f"summary:{routed.event_id}"],
            }],
        ))
        # Pin a durable Event Flow and catalog snapshot to this Job.  Existing
        # inbox dispatch remains the executor during migration, but restarts no
        # longer need to infer which semantic checkpoint owns the task.
        # ADR 0099: under the new flag the classifier also chose a
        # kind (existing / new / chat).  If it picked "new", route to
        # the NEW_PROJECT flow which will materialise the project via
        # ``interaction.project_init`` before the next event runs.
        if social_request:
            from partner.social_video.integration import load_request
            flow_name = load_request(self.root, clean)["event"]
        elif _feature_flag == "new" and _classification_kind == "new_project_kind":
            flow_name = "new_project"
        elif route == "chat_reply":
            flow_name = "direct_answer"
        else:
            flow_name = "project_iteration"
        try:
            event_catalog = build_catalog(workspace=self.root)
            event_catalog.snapshot(
                self.root / "state/event_catalog" / f"catalog_{event_catalog.version}.json")
            flow_definition = build_flow_registry().get(flow_name)
            flow_state = EventFlowController(EventFlowStore(self.root)).start(
                flow_definition, catalog_version=event_catalog.version,
                task_id=job.job_id, project_id=selected_project, instance_id=assigned,
            )
            flow_state.root_event_id = received.event_id
            EventFlowStore(self.root).save(flow_state)
            job.event_catalog_version = event_catalog.version
            job.flow_id = flow_state.flow_id
            job.flow_type = flow_state.flow_type
            job.ready_event_ids = list(flow_state.ready_node_ids)
        except Exception as exc:
            # Submission truth is preserved; the failed flow pin is explicit
            # and prevents an invisible partial migration.
            job.error = f"event_flow_initialization_failed:{type(exc).__name__}"
        with self._locked():
            if intent_required_but_failed:
                job.status = "rejected"
                job.error = "three_pass_intent_required_but_not_completed"
            elif route == "project_query":
                job.status = "completed"
            self._save(job)
            self._append_event("job_accepted", job, route=route, channel=channel)
            if route != "project_query" and not intent_required_but_failed:
                self._dispatch_locked(job)
        if intent_required_but_failed:
            msg = (
                "本次任务没有入队：三遍意图理解未得到三份可解析的模型结果。"
                "已保留失败 Event，未用保守模板冒充理解成功。"
            )
            return Submission(False, job.job_id, selected_project, assigned,
                              job.status, route, msg, "")
        if route == "project_query":
            rows = self.projects()
            active = [row for row in rows if row["status"] in {"queued", "dispatched", "running"}]
            detail = "；".join(
                f"{row['specialist']} {row['title']}：{row['status']}"
                for row in (active or rows)
            )
            msg = f"项目状态（读取于当前持久账本）：{detail}。"
            return Submission(True, job.job_id, selected_project, assigned,
                              job.status, route, msg, "")
        # Title can fall back to slug or first line if project not in
        # PROJECT_TITLES (new flag with brand-new project that wasn't
        # auto-registered).
        _title = (PROJECT_TITLES.get(selected_project)
                  or selected_project
                  or clean.splitlines()[0][:80])
        # ADR 0099: if the classifier just auto-created a new project,
        # surface the "已自动创建新项目" message instead of the default
        # "已加入后台项目队列" wording.
        _classification_msg = ""
        try:
            _raw2 = _project_raw
            if _raw2 is not None and not isinstance(_raw2, str):
                from partner.application.project_classifier import Kind as _KE
                if _raw2.kind == _KE.NEW_PROJECT_KIND:
                    _classification_extra_again = _resolve_classifier_decision(
                        _raw2, workspace_root=self.root, instance_id=persona_hint,
                    )
                    _classification_msg = _classification_extra_again.get("message", "")
                    # Override project_id with the materialised one if present
                    if _classification_extra_again.get("project_id"):
                        selected_project = _classification_extra_again["project_id"]
        except Exception:
            pass
        msg = _classification_msg or (
            f"已加入后台项目队列：{_title}；"
            f"任务 {job.job_id}，由 {assigned}专业角色承接。"
            f"你可以继续对话，不必等待它结束。"
        )
        return Submission(True, job.job_id, selected_project, assigned, job.status, route, msg, job.message_id)

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
