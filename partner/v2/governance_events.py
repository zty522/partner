"""Harness events for governed context, project rounds, and evolution records."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from partner.governance.context_selector import select_context
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.governance.project_loop import record_iteration, request_next_action
from partner.governance.signal_detector import detect_and_record
from partner.governance.storage import governance_log, instance_id, workspace_root
from partner.governance.rl_evolution import evaluate_manual_evolution_evidence
from partner.governance.rl_control import evaluate_canaries


JsonDict = dict[str, Any]


def _workspace(ctx: Any) -> str:
    value = str(getattr(ctx, "workspace", "") or "")
    if value:
        return value
    task = getattr(ctx, "task_instance", None)
    working_dir = str(getattr(task, "working_dir", "") or "")
    match = re.search(r"^(.+?/instances/0[1-5])(?:/|$)", working_dir)
    return match.group(1) if match else working_dir


def _task_dir(ctx: Any) -> Path:
    task = getattr(ctx, "task_instance", None)
    path = str(getattr(task, "working_dir", "") or "")
    if path:
        return Path(path)
    return Path(_workspace(ctx)) / "state" / "context"


def _semantic_selector(prompt: str) -> str:
    try:
        from partner.adapters.direct_api import chat
        return chat(prompt, purpose="classify", max_tokens=1000, temperature=0.0, timeout=45)
    except Exception:
        return "[]"


def atomic_select_context(ctx: Any, params: JsonDict) -> JsonDict:
    workspace = _workspace(ctx)
    query = str(params.get("query") or params.get("task") or "").strip()
    if not workspace or not query:
        return {"ok": False, "status": "missing_workspace_or_query"}
    selection, bundle = select_context(
        workspace,
        query,
        instance_id=str(params.get("instance_id") or instance_id(workspace)),
        project_id=str(params.get("project_id") or ""),
        budget_chars=int(params.get("budget_chars") or 16000),
        requested_ids=list(params.get("document_ids") or []),
        allow_history=bool(params.get("allow_history", False)),
        semantic_selector=_semantic_selector if params.get("use_llm", True) else None,
    )
    output = _task_dir(ctx) / "selected_context.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(bundle + "\n", encoding="utf-8")
    return {"ok": True, "status": "selected", "selection": selection.to_dict(),
            "path": str(output), "files": [str(output)], "content": bundle}


def atomic_record_iteration(ctx: Any, params: JsonDict) -> JsonDict:
    return record_iteration(_workspace(ctx), params)


def atomic_request_next_action(ctx: Any, params: JsonDict) -> JsonDict:
    return request_next_action(_workspace(ctx), params)


def atomic_record_issue(ctx: Any, params: JsonDict) -> JsonDict:
    return record_issue(_workspace(ctx), params)


def atomic_start_evolution_experiment(ctx: Any, params: JsonDict) -> JsonDict:
    return start_experiment(_workspace(ctx), params)


def atomic_decide_evolution_experiment(ctx: Any, params: JsonDict) -> JsonDict:
    return decide_experiment(_workspace(ctx), params)


def atomic_observe_evolution_signals(ctx: Any, params: JsonDict) -> JsonDict:
    workspace = _workspace(ctx)
    issues = detect_and_record(
        workspace,
        instance_id=str(params.get("instance_id") or instance_id(workspace)),
        project_id=str(params.get("project_id") or ""),
        expected_outputs=bool(params.get("expected_outputs", False)),
        files=list(params.get("files") or []),
        event_types=list(params.get("event_types") or []),
        result=dict(params.get("result") or {}),
        prior_event_types=list(params.get("prior_event_types") or []),
    )
    return {"ok": True, "status": "observed", "issue_count": len(issues), "issues": issues}


def atomic_review_manual_evolution_evidence(ctx: Any, params: JsonDict) -> JsonDict:
    result = evaluate_manual_evolution_evidence(
        _workspace(ctx), project_id=str(params.get("project_id") or ""),
    )
    output_dir = _task_dir(ctx)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "manual_evolution_review.json"
    md_path = output_dir / "manual_evolution_review.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = result.get("summary") or {}
    criteria = summary.get("criteria") or {}
    lines = [
        "# 手动轨迹自进化证据审查", "",
        f"- 状态：{result.get('status')}",
        f"- 自动生产晋升：否",
        f"- 样本数：{summary.get('samples', 0)}",
        f"- 唯一结果：{summary.get('unique_outcomes', 0)}",
        f"- 独立 Receipt：{summary.get('unique_receipts', 0)}",
        f"- 来源族：{', '.join(summary.get('source_families') or []) or '无'}", "",
        "## 硬门", "",
    ]
    lines.extend(f"- {'通过' if passed else '未通过'}：{name}" for name, passed in criteria.items())
    lines.extend(["", "## 决定", "", "仅允许建立 candidate 实验；没有 baseline/candidate 前后验证与回归证据，不得 promotion。"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {**result, "files": [str(json_path), str(md_path)], "path": str(md_path)}


def atomic_decide_manual_canary(ctx: Any, params: JsonDict) -> JsonDict:
    """Let instance 05 materialize the already-attested canary decision."""
    experiment_id = str(params.get("experiment_id") or "").strip()
    if not experiment_id:
        return {"ok": False, "status": "missing_experiment_id"}
    workspace = _workspace(ctx)
    root = workspace_root(workspace)
    existing: dict[str, Any] | None = None
    try:
        for line in governance_log(workspace, "promotion_decisions").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if str(row.get("experiment_id") or "") == experiment_id:
                existing = row
    except (OSError, ValueError, TypeError):
        pass
    evaluated = evaluate_canaries(workspace) if existing is None else {"ok": True, "decisions": []}
    matched = next(
        (row for row in evaluated.get("decisions") or []
         if str(((row.get("result") or {}).get("decision") or {}).get("experiment_id") or "") == experiment_id),
        None,
    )
    if matched is None and existing is not None:
        matched = {
            "decision_key": next((str(value).split("=", 1)[1] for value in existing.get("evidence") or []
                                  if str(value).startswith("decision_key=")), ""),
            "decision": existing.get("decision"),
            "metrics": {"baseline": existing.get("metrics_before") or {},
                        "candidate": existing.get("metrics_after") or {}},
            "result": {"ok": True, "status": existing.get("decision"),
                       "promoted": existing.get("decision") == "promoted", "decision": existing},
        }
    if not matched:
        return {"ok": False, "status": "decision_not_ready", "experiment_id": experiment_id,
                "evaluation": evaluated}
    sample_artifacts: list[str] = []
    try:
        trajectory_path = root / "share" / "mind" / "governance" / "rl" / "trajectories.jsonl"
        for line in trajectory_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if str((row.get("action") or {}).get("experiment_id") or "") != experiment_id:
                continue
            for value in (row.get("outcome") or {}).get("artifacts") or []:
                if Path(value).is_file() and str(value) not in sample_artifacts:
                    sample_artifacts.append(str(value))
    except (OSError, ValueError, TypeError):
        pass
    matched["sample_artifacts"] = sample_artifacts
    output_dir = _task_dir(ctx)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "manual_canary_decision.json"
    md_path = output_dir / "manual_canary_decision.md"
    json_path.write_text(json.dumps(matched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metrics = matched.get("metrics") or {}
    result = matched.get("result") or {}
    decision = result.get("decision") or {}
    lines = [
        "# 手动受控 Canary 决策", "",
        f"- Experiment：{experiment_id}",
        f"- Decision：{matched.get('decision')}",
        f"- 晋升方式：本次用户显式触发的 PromotionDecision（不是后台自动晋升）", "",
        "## Baseline", "", f"```json\n{json.dumps(metrics.get('baseline') or {}, ensure_ascii=False, indent=2)}\n```", "",
        "## Candidate", "", f"```json\n{json.dumps(metrics.get('candidate') or {}, ensure_ascii=False, indent=2)}\n```", "",
        "## 硬门", "",
    ]
    lines.extend(
        f"- {'通过' if passed else '未通过'}：{name}"
        for name, passed in (decision.get("criteria_results") or {}).items()
    )
    lines.extend(["", "## 证据", ""])
    lines.extend(f"- {value}" for value in (decision.get("evidence") or []))
    lines.extend(f"- {value}" for value in sample_artifacts)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "status": str(matched.get("decision") or ""),
            "promotion": matched.get("decision") == "promoted", "decision": matched,
            "files": [str(json_path), str(md_path)], "path": str(md_path)}
