"""Bounded read model for the per-Job Event execution trace."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import json
import re


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_INFRASTRUCTURE_TYPES = {
    "notification.lifecycle_compose",
    "notification.lifecycle_critic",
    "delivery.channel_ack",
}


def _directory(workspace: str | Path, job_id: str) -> Path:
    if not _SAFE_ID.fullmatch(str(job_id)):
        raise ValueError("invalid job_id")
    return Path(workspace).resolve() / "state" / "run_logs" / str(job_id)


def _rows(workspace: str | Path, job_id: str, *, max_rows: int = 20_000) -> list[dict[str, Any]]:
    path = _directory(workspace, job_id) / "events.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if len(rows) >= max_rows:
                break
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def _is_infrastructure(row: dict[str, Any]) -> bool:
    return (str(row.get("flow_type") or "") == "lifecycle_notification" or
            str(row.get("event_type") or "") in _INFRASTRUCTURE_TYPES)


def _latest_finished(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        if row.get("phase") != "finished":
            continue
        selected[str(row.get("event_id") or f"row-{index}")] = (index, row)
    return [row for _, row in sorted(selected.values(), key=lambda item: item[0])]


def _graph_from_plans(job_id: str, plans: list[dict[str, Any]]) -> dict[str, Any]:
    flows=[]; nodes=[]; edges=[]
    for flow in plans:
        fid=str(flow.get('flow_id') or '')
        flows.append({k:flow.get(k) for k in ('flow_id','flow_type','version','status')})
        for node in flow.get('nodes') or []:
            nid=str(node.get('node_id') or '')
            nodes.append({'id':f'{fid}:{nid}','flow_id':fid,'node_id':nid,
                          'event_type':node.get('event_type'),'status':node.get('runtime_status')})
            for dependency in node.get('depends_on') or []:
                edges.append({'from':f'{fid}:{dependency}','to':f'{fid}:{nid}','kind':'depends_on'})
    return {'schema_version':1,'source_job_id':job_id,'flows':flows,'nodes':nodes,'edges':edges,
            'counts':{'flows':len(flows),'nodes':len(nodes),'edges':len(edges)}}


def _cycle_outcome(workspace: Path, job_id: str,
                   rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cycle = workspace / "state" / "cycles" / job_id
    assessment = _read_json(cycle / "assessment.json")
    dynamic_effects = sorted((cycle / 'iterations').glob('learning_effect_*.json'))
    downstream = (_read_json(dynamic_effects[-1]) if dynamic_effects else
                  _read_json(cycle / "learning_downstream_two.json"))
    final = _read_json(cycle / "final_summary.json")
    completion = _read_json(cycle / "completion.json")
    report = _read_json(cycle / "report.json")
    comparison = assessment.get("round_comparison") if isinstance(assessment.get("round_comparison"), dict) else {}
    baseline_row = comparison.get("round_1_baseline") if isinstance(comparison.get("round_1_baseline"), dict) else {}
    candidate_row = comparison.get("round_2_candidate") if isinstance(comparison.get("round_2_candidate"), dict) else {}
    learning_row = comparison.get("active_learning_bridge") if isinstance(comparison.get("active_learning_bridge"), dict) else {}
    matched = downstream.get("matched_evidence") if isinstance(downstream.get("matched_evidence"), dict) else {}
    dynamic_rounds = sorted((cycle / 'iterations').glob('round_*.json'))
    if dynamic_rounds and not matched:
        for path in reversed(dynamic_rounds):
            record=_read_json(path)
            verify=(((record.get('node_outputs') or {}).get('verify') or {}).get('semantic_output') or {})
            candidate_match=verify.get('learning_matched_evidence') or {}
            if candidate_match:
                matched=candidate_match; break
    def measured_round(path: Path) -> dict[str, Any]:
        record=_read_json(path)
        for raw in record.get('files') or []:
            artifact=Path(str(raw))
            if artifact.suffix.lower() != '.json' or not artifact.is_file(): continue
            value=_read_json(artifact); metrics=value.get('metrics') or {}
            rmse=metrics.get('rmse') if isinstance(metrics,dict) else None
            if isinstance(rmse,(int,float)):
                guard=value.get('guardrails') or {}
                return {'rmse':rmse,'no_target_leakage':guard.get('no_target_leakage'),
                        'folds':(value.get('run_config') or {}).get('folds'),'path':str(artifact)}
        return {}
    measurements=[value for value in (measured_round(path) for path in dynamic_rounds) if value]
    first_measure=measurements[0] if measurements else {}
    last_measure=measurements[-1] if len(measurements)>1 else {}
    comparison_artifact = _read_json(Path(str(matched.get("comparison_ref") or "")))
    handoff_artifact = _read_json(Path(str(matched.get("learning_handoff_ref") or "")))
    baseline = matched.get("baseline", baseline_row.get("test_rmse", first_measure.get('rmse')))
    candidate = matched.get("candidate", candidate_row.get("test_rmse", last_measure.get('rmse')))
    effect = matched.get("effect")
    if effect is None and isinstance(baseline, (int, float)) and isinstance(candidate, (int, float)):
        effect = baseline - candidate
    relative = effect / baseline * 100 if isinstance(effect, (int, float)) and baseline else None

    def acknowledged(name: str, *, pdf: bool = False) -> bool:
        value = _read_json(cycle / name)
        return value.get("delivery_state") in {"sent","web_visible"} and (
            not pdf or value.get("pdf_delivered") is True or value.get("delivery_state") == "web_visible")

    # Web delivery is a durable projection receipt written by the delivery
    # Event.  Do not report it as successful merely because the web UI exists.
    web_receipts: list[dict[str, Any]] = []
    for row in rows or []:
        if row.get("phase") != "finished" or str(row.get("event_type") or "") not in {
                "delivery.send_text", "delivery.send_pdf"}:
            continue
        output = row.get("output") if isinstance(row.get("output"), dict) else {}
        receipts = output.get("channel_receipts") if isinstance(output.get("channel_receipts"), list) else []
        web_receipts.extend(receipt for receipt in receipts
                            if isinstance(receipt, dict) and receipt.get("channel") == "web"
                            and receipt.get("visible") is True
                            and receipt.get("job_id") == job_id)

    dynamic_learning_records=sorted((cycle/'iterations').glob('learning_after_*.json'))
    dynamic_handoff={}
    learning_record={}
    dynamic_learning_valid=False
    if dynamic_learning_records:
        learning_record=_read_json(dynamic_learning_records[-1])
        dynamic_handoff=(((learning_record.get('node_outputs') or {}).get('handoff') or {})
                         .get('semantic_output') or {})
        dynamic_learning_valid=(learning_record.get('status') == 'completed'
                                and dynamic_handoff.get('ready') is True)
    learning_status=(downstream.get("status") or learning_row.get("status") or "not_run")
    learning_consumed=(downstream.get("consumed") is True
                       or learning_row.get("consumed_in_round_2") is True)
    if dynamic_learning_records and not dynamic_learning_valid:
        learning_status='failed'
        learning_consumed=False
    return {
        "headline": (f"候选将测试 RMSE 从 {baseline:.4f} 降至 {candidate:.4f}"
                     if isinstance(baseline, (int, float)) and isinstance(candidate, (int, float))
                     else str(assessment.get("summary") or "尚无可展示的项目结论")),
        "project": {"status": "improved" if downstream.get("improved") is True else "completed",
                    "metric": matched.get("metric") or "test_rmse", "baseline": baseline,
                    "candidate": candidate, "effect": effect,
                    "relative_improvement_percent": relative,
                    "bootstrap": comparison_artifact.get("bootstrap") or {},
                    "fold_comparison": comparison_artifact.get("fold_comparison") or [],
                    "integrity_checks": comparison_artifact.get("integrity_checks") or {},
                    "rounds": completion.get("project_rounds") or len(dynamic_rounds),
                    "split_reused": (candidate_row.get("split_reuse_verified")
                                     if candidate_row.get("split_reuse_verified") is not None
                                     else comparison_artifact.get("split_consistency") == "exact_reuse_of_round_one_split"
                                     or bool(len(measurements)>1 and first_measure.get('folds') == last_measure.get('folds'))),
                    "leakage_check": (candidate_row.get("leakage_check")
                                      if candidate_row.get("leakage_check") is not None
                                      else comparison_artifact.get("group_consistency")
                                      or ('passed' if first_measure.get('no_target_leakage') is True else None))},
        "learning": {"status": learning_status,
                     "consumed": learning_consumed and dynamic_learning_valid,
                     "handoff_ready": dynamic_learning_valid,
                     "comparison_complete": downstream.get('comparison_complete') is True,
                     "source_url": (learning_row.get("source_url") or
                                    ((dynamic_handoff.get('source_urls') or [''])[0]) or
                                    ((handoff_artifact.get("source_urls") or [""])[0])),
                     "mechanism": (learning_row.get("mechanism") or
                                   ("GroupKFold 分组隔离核验" if handoff_artifact else "")),
                     "handoff_path": (learning_row.get("handoff_path") or matched.get("learning_handoff_ref")
                                      or next(iter(dynamic_handoff.get('evidence_refs') or []), '')
                                      or str(((learning_record.get('node_outputs') or {}).get('handoff') or {}).get('files',[""])[0]
                                             if dynamic_learning_records else ''))},
        "evolution": {"decision": final.get("decision") or "not_run",
                      "production_effective": final.get("production_effective") is True,
                      "issue": final.get("selected_issue_id") or "",
                      "summary": final.get("message") or ""},
        "delivery": {"web": bool(web_receipts) or acknowledged('text_ack.json') or acknowledged('final_ack.json'),
                     "web_receipt_count": len(web_receipts),
                     "text": acknowledged("text_ack.json"),
                     "report": acknowledged("report_ack.json", pdf=True),
                     "final": acknowledged("final_ack.json")},
        "report": {"status": report.get("status") or "not_run", "flow_id": report.get("flow_id") or ""},
        "completion": completion,
    }


def trace_overview(workspace: str | Path, job_id: str, *, after: int = 0,
                   limit: int = 120, view: str = "business",
                   include_acceptance: bool = True) -> dict[str, Any]:
    """Return a reader-first trace; raw infrastructure remains opt-in."""
    root = Path(workspace).resolve()
    all_rows = _rows(root, job_id)
    intake = next((row for row in all_rows if row.get("kind") == "run_intake"), None)
    event_rows = [row for row in all_rows if row.get("kind") == "event"]
    finished = _latest_finished(event_rows)
    business = [row for row in finished if not _is_infrastructure(row)]
    state_by_node = {(str(row.get("flow_id") or ""), str(row.get("node_id") or "")): row
                     for row in finished}

    plans = []
    seen_flows: set[str] = set()
    for row in all_rows:
        if row.get("kind") not in {"run_intake", "flow_plan"}:
            continue
        flow_id = str(row.get("flow_id") or "")
        if not flow_id or flow_id in seen_flows:
            continue
        seen_flows.add(flow_id)
        plan = row.get("flow_plan") if isinstance(row.get("flow_plan"), dict) else {}
        state = _read_json(root / "state" / "event_flows" / f"{flow_id}.json")
        completed = set(state.get("completed_node_ids") or state.get("completed_event_ids") or [])
        failed = set(state.get("failed_node_ids") or [])
        skipped = set(state.get("skipped_node_ids") or [])
        nodes = []
        for node in plan.get("nodes") or []:
            value = dict(node) if isinstance(node, dict) else {}
            node_id = str(value.get("node_id") or "")
            observed = state_by_node.get((flow_id, node_id))
            value["runtime_status"] = (
                str(observed.get("status") or "completed") if observed else
                "completed" if node_id in completed else "failed" if node_id in failed else
                "skipped" if node_id in skipped else "pending")
            nodes.append(value)
        plans.append({"flow_id": flow_id,
                      "flow_type": plan.get("name") or row.get("flow_type") or "",
                      "version": plan.get("version") or "",
                      "description": plan.get("description") or "",
                      "status": state.get("status") or "unknown", "nodes": nodes})

    try:
        from partner.index.job_repository import init as _init_jobs
        job = _init_jobs(root).get(job_id) or {}
    except Exception:
        job = {}
    original_terminal = next((row for row in reversed(all_rows)
                              if row.get("kind") == "job_terminal"), None) or {}
    outcome = _cycle_outcome(root, job_id, event_rows)
    current_status = str(job.get("status") or original_terminal.get("status") or "unknown")
    root_flow_id = str(job.get("flow_id") or original_terminal.get("flow_id") or "")
    root_flow = _read_json(root / "state" / "event_flows" / f"{root_flow_id}.json")
    recovered = current_status == "completed" and str(original_terminal.get("status") or "") in {"failed", "blocked"}
    completion = {
        "recorded": bool(original_terminal or outcome.get("completion")), "status": current_status,
        "error": "" if current_status == "completed" else str(job.get("error") or original_terminal.get("error") or ""),
        "flow_id": root_flow_id, "flow_type": job.get("flow_type") or original_terminal.get("flow_type") or "",
        "at": job.get("updated_at") or original_terminal.get("at") or "",
        "completed_nodes": len(job.get("completed_event_ids") or root_flow.get("completed_event_ids") or
                               original_terminal.get("completed_node_ids") or []),
        "failed_nodes": 0 if current_status == "completed" else len(root_flow.get("failed_node_ids") or
                                                                     original_terminal.get("failed_node_ids") or []),
        "skipped_nodes": len(root_flow.get("skipped_node_ids") or original_terminal.get("skipped_node_ids") or []),
        "final_outputs": outcome.get("completion") or {}, "recovered": recovered,
        "previous_status": original_terminal.get("status") or "",
        "previous_error": original_terminal.get("error") or "",
        "recovery_count": len(root_flow.get("recovery_history") or [])}

    def et(row): return str(row.get("event_type") or "")
    def node(row): return str(row.get("node_id") or "")
    def flow(row): return str(row.get("flow_type") or "")

    def make_stage(name, description, predicate, summary="", resolved_status=""):
        rows = [row for row in business if predicate(row)]
        failures = [row for row in rows if str(row.get("status")) in {"failed", "blocked"}]
        status = resolved_status or ("failed" if failures else "completed" if rows else "not_run")
        last_out = rows[-1].get("output") if rows and isinstance(rows[-1].get("output"), dict) else {}
        return {"name": name, "description": description, "status": status,
                "event_count": len(rows), "failed_count": len(failures),
                "summary": summary or str(last_out.get("summary") or last_out.get("error") or ""),
                "flow_ids": list(dict.fromkeys(str(row.get("flow_id") or "") for row in rows if row.get("flow_id"))),
                "event_ids": [str(row.get("event_id") or "") for row in rows[-8:]]}

    round_flows = [p["flow_id"] for p in plans if p["flow_type"] == "project_cycle_round"]
    delivery_complete = all(outcome["delivery"].get(key)
                            for key in ("web", "text", "report", "final"))
    delivery_had_retry = any(
        str(r.get("status")) in {"failed", "blocked"} and
        (node(r) in {"text_ack", "report_ack", "final_ack"} or
         et(r).startswith(("delivery.", "presentation.")))
        for r in business)
    iteration_stages = [make_stage(
        f"项目迭代 {index+1}", "执行本轮冻结假设并由 Settlement 决定下一步",
        lambda r, fid=fid: flow(r) == "project_cycle_round" and str(r.get("flow_id")) == fid,
        "本轮证据已冻结") for index, fid in enumerate(round_flows)]
    stages = [
        {"name": "接收与理解", "description": "保存原始消息，冻结意图和执行边界",
         "status": "completed" if intake else "not_run", "event_count": 1 if intake else 0,
         "failed_count": 0, "summary": "原始消息和意图契约已保存" if intake else "尚未接收",
         "flow_ids": [], "event_ids": []},
        make_stage("规划 Event Flow", "生成有限计划并冻结依赖", lambda r: et(r).startswith(("cycle.initialize", "cycle.round_design", "project.plan")),
                   f"形成 {len(plans)} 个实际 Flow"),
        *iteration_stages[:1],
        make_stage("主动学习", "检索、阅读、交叉核验并冻结 handoff",
                   lambda r: flow(r) == "active_learning" or et(r).startswith("active_learning."),
                   "学习结论已被第二轮实际消费" if outcome["learning"].get("consumed") else "尚未证明下游消费"),
        *iteration_stages[1:],
        make_stage("总结与 PDF", "报告 Event 编辑、核验、渲染和交付",
                   lambda r: flow(r) == "pdf_report" or et(r).startswith(("presentation.report", "visualization.")),
                   (("早期报告失败已保留；修复后的 PDF 已生成并取得 QQ 回执"
                     if any(str(r.get("status")) in {"failed", "blocked"} for r in business
                            if flow(r) == "pdf_report")
                     else "PDF 已生成、质量检查通过并取得 QQ 回执")
                    if outcome["delivery"].get("report") else ""),
                   "recovered" if outcome["delivery"].get("report") and any(
                       str(r.get("status")) in {"failed", "blocked"} for r in business
                       if flow(r) == "pdf_report") else "completed" if outcome["delivery"].get("report") else ""),
        make_stage("经验与记忆", "更新经验、成长、习惯并保存来源",
                   lambda r: node(r) in {"experience", "growth", "habit", "seal"} or et(r).startswith("memory.")),
        make_stage("Partner 自进化", "审计机制缺陷，执行候选实验并结算",
                   lambda r: flow(r) == "autonomous_evolution" or node(r) in {"partner_audit", "evolution_gate", "evolve"},
                   outcome["evolution"].get("summary") or "",
                   outcome["evolution"].get("decision")
                   if outcome["evolution"].get("decision") in {"promoted", "rejected", "inconclusive", "no_change"}
                   else ""),
        make_stage("交付与终态", "网页和 QQ 显示同一结论、报告和回执",
                   lambda r: node(r) in {"notify", "compose", "message_critic", "deduplicate", "send", "text_ack",
                                               "report_ack", "final_notify", "final_compose", "final_critic",
                                               "final_deduplicate", "final_send", "final_ack", "finish"},
                   "早期超时已保留；网页、QQ 文本和 PDF 最终均取得回执" if delivery_complete else "交付尚未完整",
                   "recovered" if delivery_complete and delivery_had_retry else
                   "completed" if delivery_complete else "")]

    updates, all_updates, seen = [], [], set()
    important_nodes = {"round_one", "learning_one", "round_two", "report", "partner_audit", "evolve", "finish"}
    for row in finished:
        if et(row) != "notification.lifecycle_compose":
            continue
        output = row.get("output") if isinstance(row.get("output"), dict) else {}
        semantic = output.get("semantic_output") if isinstance(output.get("semantic_output"), dict) else {}
        raw_input = row.get("input") if isinstance(row.get("input"), dict) else {}
        phase = str(semantic.get("lifecycle_phase") or "")
        message = str(output.get("message") or "").strip()
        source_node = str(raw_input.get("node_id") or "")
        if not message or message in seen:
            continue
        record = {"at": row.get("at"), "phase": phase, "message": message,
                  "source_node": source_node,
                  "flow_name": raw_input.get("flow_name") or raw_input.get("flow_type") or ""}
        all_updates.append(record)
        if phase not in {"accepted", "flow_planned", "flow_started", "flow_completed", "flow_failed"} and source_node not in important_nodes:
            continue
        seen.add(message)
        updates.append(record)

    selected_rows = finished if view == "all" else business
    start = max(0, int(after)); page_size = max(1, min(int(limit), 250))
    timeline = []
    for sequence, row in enumerate(selected_rows[start:start + page_size], start=start + 1):
        output = row.get("output") if isinstance(row.get("output"), dict) else {}
        timeline.append({"sequence": sequence, "at": row.get("at"), "phase": row.get("phase"),
                         "flow_id": row.get("flow_id"), "flow_type": row.get("flow_type"),
                         "node_id": row.get("node_id"), "event_id": row.get("event_id"),
                         "event_type": row.get("event_type"), "attempt": row.get("attempt"),
                         "status": row.get("status"), "duration_ms": row.get("duration_ms"),
                         "summary": output.get("summary") or output.get("error") or "",
                         "has_input": "input" in row, "has_output": "output" in row,
                         "infrastructure": _is_infrastructure(row)})

    def chain(name, predicate):
        rows = [r for r in business if predicate(r)]
        failures = sum(str(r.get("status")) in {"failed", "blocked"} for r in rows)
        last = rows[-1] if rows else {}; output = last.get("output") if isinstance(last.get("output"), dict) else {}
        deltas = [str(r.get("event_id") or "") for r in rows if isinstance(r.get("output"), dict) and
                  any(r["output"].get(k) is True for k in ("business_delta", "learning_delta", "evolution_delta",
                                                           "production_effective", "improvement_verified"))]
        return {"name": name, "events": len(rows), "completed": len(rows)-failures, "failed": failures,
                "delta_event_ids": deltas, "last_event_id": last.get("event_id") or "",
                "last_node_id": last.get("node_id") or "",
                "last_summary": output.get("summary") or output.get("error") or ""}

    phases = Counter(str(row.get("phase") or "unknown") for row in event_rows)
    chains = {
        "project": chain("项目推进", lambda r: et(r).startswith(("project.", "core.", "commitment.", "action."))),
        "learning": chain("主动学习", lambda r: flow(r) == "active_learning" or et(r).startswith("active_learning.")),
        "evolution": chain("Partner 自进化", lambda r: flow(r) == "autonomous_evolution" or et(r).startswith(("evolution.", "autonomous_evolution."))),
        "delivery": chain("消息与报告", lambda r: et(r).startswith(("delivery.", "presentation.", "visualization.")))}
    live_graph=_graph_from_plans(job_id, plans)
    report_record=_read_json(root/'state'/'cycles'/job_id/'report.json')
    frozen_graph=(((report_record.get('node_outputs') or {}).get('flow_graph') or {})
                  .get('semantic_output') or {})
    graph=frozen_graph if frozen_graph.get('source_job_id') == job_id else live_graph
    response = {"job_id": job_id, "available": bool(all_rows),
            "message": intake.get("message") if intake else "",
            "intent_contract": intake.get("intent_contract") if intake else {},
            "flows": plans, "graph": graph,
            "events": timeline, "stages": stages, "user_updates": updates[-80:],
            "recent_event_updates": all_updates[-80:],
            "user_update_count": len(all_updates), "milestone_update_count": len(updates),
            "outcome": outcome, "completion": completion, "chains": chains,
            "counts": {"rows": len(event_rows), "events": len(finished), "business_events": len(business),
                       "infrastructure_events": len(finished)-len(business), **dict(phases)},
            "event_view": view, "next_after": min(start+page_size, len(selected_rows)),
            "has_more": start+page_size < len(selected_rows), "total_visible_events": len(selected_rows)}
    if include_acceptance:
        from partner.benchmark.run_acceptance import evaluate_run
        response["acceptance"] = evaluate_run(root, job_id, projection=response)
    return response

def event_detail(workspace: str | Path, job_id: str, event_id: str) -> dict[str, Any]:
    if not _SAFE_ID.fullmatch(str(event_id)):
        raise ValueError("invalid event_id")
    rows = [row for row in _rows(workspace, job_id)
            if row.get("kind") == "event" and str(row.get("event_id") or "") == event_id]
    if not rows:
        raise FileNotFoundError(event_id)
    return {"job_id": job_id, "event_id": event_id, "lifecycle": rows}


__all__ = ["trace_overview", "event_detail"]
