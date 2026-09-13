"""Auditable semantic Memory Events."""
from __future__ import annotations

from typing import Any
import json
from partner.event_fabric.catalog import EventDefinition
from partner.memory import EventMemory
from ._llm import call_model, json_object


def _workspace(ctx: Any) -> str:
    return str(getattr(ctx, "workspace", "") or getattr(ctx, "workspace_root", ""))


def context_recall(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    value = EventMemory(_workspace(ctx)).recall(
        project_id=str(params.get("project_id") or ""), query=str(params.get("query") or ""),
        limit=max(1, min(20, int(params.get("limit") or 8))),
    )
    return {"ok": True, "status": "completed", "semantic_output": value,
            "summary": f"召回 {len(value['observations'])} 条相关经验、{len(value['active_habits'])} 条已激活习惯"}


def semantic_update(kind: str):
    def handler(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        record = dict(params.get("record") or {})
        evidence = list(record.get("evidence_refs") or [])
        if kind in {"habit", "growth"} and not evidence:
            return {"ok": False, "status": "failed", "error": "evidence_refs_required"}
        if kind in {"habit", "growth"}:
            from partner.event_fabric import EventLedger
            project = str(params.get('project_id') or getattr(ctx, 'project_id', '') or record.get('project_id') or '')
            if not project or (record.get('project_id') and record['project_id'] != project):
                return {"ok": False, "status": "failed", "error": "verified memory requires matching project scope"}
            record['project_id'] = project
            terminals = EventLedger(_workspace(ctx)).recent_summaries(limit=10000, include_audit=True)
            verified = [row for row in terminals if row.get("event_id") in evidence
                        and row.get("status") == "completed"
                        and (row.get("business_delta") or row.get("evolution_delta"))
                        and (not record.get("project_id") or row.get("project_id") == record["project_id"])]
            flows = {row.get("flow_id") for row in verified if row.get("flow_id")}
            if kind == "habit" and len(flows) < 2:
                return {"ok": False, "status": "failed", "error": "habit requires verified outcomes from two distinct flows"}
            if kind == "growth":
                from partner.runtime.matched_execution import compare
                matched = compare(_workspace(ctx), record.get("baseline_receipt") or {},
                                  record.get("candidate_receipt") or {})
                if not matched["improved"]:
                    return {"ok": False, "status": "failed", "error": "growth requires trusted matched improvement and regression evidence"}
                linked = [row for row in terminals if row.get('event_id') in evidence
                          and row.get('project_id') == project and row.get('status') == 'completed'
                          and row.get('event_type') == 'self_evolution.matched_compare'
                          and set(matched['evidence_refs']).issubset(row.get('evidence_refs') or [])]
                if not linked:
                    return {"ok": False, "status": "failed", "error": "growth receipts require a matching comparison Event in this project"}
                record["evidence_refs"] = list(dict.fromkeys(evidence + matched["evidence_refs"]))
                record['production_effective'] = False
            record["status"] = "active" if kind == "habit" else "confirmed"
        path = EventMemory(_workspace(ctx)).append_semantic(kind, record)
        return {"ok": True, "status": "completed", "files": [path],
                "summary": f"{kind} 语义记录已追加", "semantic_output": record}
    return handler


def semantic_llm(kind: str, *, status: str = "active"):
    def handler(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        known_evidence: list[str] = [str(value) for value in params.get("evidence_refs") or []]
        upstream = {**(params.get("flow_outputs") or {}), **(params.get("upstream") or {})}
        for value in upstream.values():
            if isinstance(value, dict):
                known_evidence.extend(str(ref) for ref in value.get("evidence_refs") or [])
                known_evidence.extend(str(ref) for ref in value.get("files") or [])
        known_evidence = list(dict.fromkeys(value for value in known_evidence if value))
        raw, usage = call_model(ctx, purpose=f"memory_{kind}", prompt=(
            "从以下 Event 终态提取一条可迁移记忆。不得把一次成功变成永久能力，不得把用户偏好当业务事实。"
            "只输出 JSON：{\"content\":\"\",\"scope\":\"\",\"evidence_refs\":[],"
            "\"confidence\":0.0,\"counterexample\":\"\"}。\n"
            + json.dumps(params, ensure_ascii=False)[:48000]
        ))
        record = json_object(raw)
        requested = [str(value) for value in record.get("evidence_refs") or []]
        evidence = [value for value in requested if value in set(known_evidence)] or known_evidence
        if not evidence:
            return {"ok": False, "status": "failed", "error": "semantic memory requires evidence_refs",
                    "token_usage": usage}
        record.update(evidence_refs=evidence, status=status,
                      project_id=str(params.get("project_id") or ""))
        path = EventMemory(_workspace(ctx)).append_semantic(kind, record)
        return {"ok": True, "status": "completed", "files": [path],
                "semantic_output": record, "summary": f"{kind} 记忆已形成",
                "token_usage": usage}
    return handler


DEFINITIONS = [
    EventDefinition("memory.context_recall", "memory", "为当前步骤选择相关经验、偏好、习惯、信念与成长", context_recall),
    EventDefinition("memory.lesson_extract", "memory", "从证据充分的经历形成可迁移 lesson", semantic_llm("lesson"), execution_method="llm"),
    EventDefinition("memory.habit_propose", "memory", "提出待验证行动习惯，不直接激活", semantic_llm("habit", status="candidate"), execution_method="llm"),
    EventDefinition("memory.habit_activate", "memory", "凭验证证据激活习惯", semantic_update("habit")),
    EventDefinition("memory.belief_update", "memory", "以新证据修订项目 belief", semantic_llm("belief"), execution_method="llm"),
    EventDefinition("memory.growth_confirm", "memory", "仅在真实改善后确认持久成长", semantic_update("growth")),
]
