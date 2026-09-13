"""One frontend-safe projection over Application and Event truth."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from partner.event_fabric import EventLedger
from partner.event_fabric.projector import project_summary
from .service import PartnerApplicationService
from .views import ArtifactView, EventDetail, JobView, UserUpdate


LINE_LABEL = {
    "project": "项目", "active_learning": "外部学习",
    "self_evolution": "系统改进", "interaction": "对话",
}


def _safe_text(value: Any) -> str:
    """Keep routine product surfaces free of local paths and raw identifiers."""
    text = str(value or "").strip()
    text = re.sub(r"<(think|analysis)>.*?</\1>", "", text,
                  flags=re.IGNORECASE | re.DOTALL).strip()
    if re.search(r"</?(think|analysis)>", text, re.IGNORECASE):
        return "这条历史记录含未闭合的模型推理，已从默认视图隐藏。"
    text = re.sub(r"(?:[A-Za-z]:[\\/]|/)(?:[^\s;,，。])+", "[本地证据]", text)
    text = re.sub(r"\b(?:evt|flow|job|task|receipt)_[A-Za-z0-9_-]+\b", "[内部记录]", text)
    return text


class ApplicationReadModel:
    def __init__(self, workspace: str):
        self.workspace = str(Path(workspace).resolve())
        self.service = PartnerApplicationService(self.workspace)
        self.ledger = EventLedger(self.workspace)

    def projects(self) -> list[dict[str, Any]]:
        return self.service.projects()

    def jobs(self, project_id: str = "", limit: int = 100) -> list[JobView]:
        summaries = self.ledger.recent_summaries(limit=500, project_id=project_id)
        by_job: dict[str, list[dict[str, Any]]] = {}
        for row in summaries:
            by_job.setdefault(str(row.get("job_id") or row.get("correlation_id") or ""), []).append(row)
        result = []
        for raw in self.service.list_jobs(project_id=project_id, limit=limit):
            rows = by_job.get(str(raw.get("job_id") or ""), [])
            latest = project_summary(rows[0]) if rows else {}
            artifacts = []
            for row in rows:
                for item in row.get("artifacts") or []:
                    path = str(item.get("path") if isinstance(item, dict) else item)
                    if path:
                        artifacts.append(ArtifactView(
                            artifact_id=f"{row.get('event_id')}:{len(artifacts)}",
                            job_id=str(raw.get("job_id") or ""), title=Path(path).name,
                            media_type=Path(path).suffix.lower().lstrip(".") or "file",
                            source_event_id=str(row.get("event_id") or ""),
                            verified=Path(path).is_file(), local_path=path,
                        ))
            result.append(JobView(
                job_id=str(raw.get("job_id") or ""), project_id=str(raw.get("project_id") or ""),
                title=str(raw.get("title") or "未命名工作"), state=str(raw.get("status") or "queued"),
                current_activity=str(raw.get("current_event_id") or ""),
                meaningful_progress=str(latest.get("headline") or ""),
                blocker=str(raw.get("error") or ""), latest_result=str(latest.get("outcome") or ""),
                next_committed_action=str((rows[0].get("next_reason") if rows else "") or ""),
                started_at=str(raw.get("created_at") or ""), updated_at=str(raw.get("updated_at") or ""),
                artifacts=tuple(artifacts), unread=bool(rows),
            ))
        return result

    def updates(self, project_id: str = "", limit: int = 100) -> list[UserUpdate]:
        result = []
        visible_types = {
            "interaction.direct_answer", "project.action_execute",
            "project.outcome_reflect", "active_learning.source_retrieve",
            "active_learning.synthesize", "active_learning.matched_verify",
            "self_evolution.issue_diagnose", "self_evolution.candidate_propose",
            "self_evolution.matched_compare", "self_evolution.promotion_decide",
        }
        for row in self.ledger.recent_summaries(limit=limit, project_id=project_id):
            view = project_summary(row)
            series = str(row.get("series") or "project")
            if series not in {"project", "active_learning", "self_evolution", "interaction"}:
                continue
            event_type = str(row.get("event_type") or "")
            # Only canonical product Events enter the default conversation.
            # Historical controllers may carry delta/final flags but belong in
            # EventDetail, otherwise the rebuilt UI repeats old noisy output.
            if event_type not in visible_types:
                continue
            outcome = str(view.get("outcome") or row.get("headline") or "").strip()
            semantic = row.get("semantic_output") if isinstance(row.get("semantic_output"), dict) else {}
            result.append(UserUpdate(
                update_id=str(row.get("event_id") or ""),
                job_id=str(row.get("job_id") or row.get("correlation_id") or ""),
                project_id=str(row.get("project_id") or ""),
                line="conversation" if series == "interaction" else series,
                kind="blocked" if row.get("status") in {"failed", "blocked"} else
                     "completed" if row.get("notification_kind") in {"final", "milestone"} else "progress",
                subject=_safe_text(row.get("headline") or LINE_LABEL.get(series, series)),
                action=_safe_text(semantic.get("action") or row.get("headline") or "已完成该步骤"),
                finding=_safe_text(semantic.get("finding") or outcome),
                meaning=_safe_text(semantic.get("meaning") or "该结果已经过当前步骤的证据约束。"),
                next=_safe_text(semantic.get("next") or row.get("next_reason") or "根据本轮结果选择下一项实际动作。"),
                created_at=str(row.get("created_at") or row.get("updated_at") or ""),
                evidence_refs=tuple(str(x) for x in row.get("evidence_refs") or []),
            ))
        return result

    def event_details(self, job_id: str = "", limit: int = 200) -> list[EventDetail]:
        result = []
        for row in self.ledger.recent_summaries(limit=limit, include_audit=True):
            if job_id and str(row.get("job_id") or row.get("correlation_id") or "") != job_id:
                continue
            result.append(EventDetail(
                event_id=str(row.get("event_id") or ""), flow_id=str(row.get("flow_id") or ""),
                node=str(row.get("node_id") or ""), series=str(row.get("series") or ""),
                status=str(row.get("status") or ""), semantic_summary=str(row.get("headline") or ""),
                claims=tuple(row.get("claims") or []), evidence=tuple(row.get("evidence_refs") or []),
                token_usage=dict(row.get("token_usage") or {}),
                failure={"class": row.get("failure_class"), "mechanism": row.get("mechanism")},
                next_candidates=tuple(row.get("next_event_candidates") or []),
            ))
        return result
