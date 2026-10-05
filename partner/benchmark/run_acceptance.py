"""Deterministic product acceptance for one complete Partner run.

This evaluates observable effects rather than trusting a root ``completed``
flag.  It is deliberately read-only so it can be called by the Web projection,
the benchmark wrapper, or a later acceptance Event without changing execution.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import json


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def _sha(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _runtime_evidence(root: Path, job_id: str) -> dict[str, Any]:
    """Project durable delivery/report evidence from the Event log.

    Improvement workstreams intentionally do not use the project cycle's
    ``report.json`` wrapper.  Their report and delivery Events are still
    first-class evidence and must not disappear from acceptance merely because
    they use a different Flow shape.
    """
    path = root / "state" / "run_logs" / job_id / "events.jsonl"
    result: dict[str, Any] = {"messages": [], "web": False, "qq_text": False,
                              "qq_pdf": False, "pdf_sha256": "", "outputs": {}}
    if not path.is_file():
        return result
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if row.get("kind") != "event" or row.get("phase") != "finished":
                continue
            output = row.get("output") if isinstance(row.get("output"), dict) else {}
            node_id = str(row.get("node_id") or "")
            if node_id:
                result["outputs"][node_id] = output
            semantic = output.get("semantic_output") if isinstance(output.get("semantic_output"), dict) else {}
            if semantic.get("delivery_state") == "sent":
                components = semantic.get("component_acks") or []
                if any(isinstance(item, dict) and item.get("kind") == "text" for item in components):
                    result["qq_text"] = True
                pdf_ack = next((item for item in components
                                if isinstance(item, dict) and item.get("kind") == "pdf"), {})
                if pdf_ack:
                    result["qq_pdf"] = True
                    result["pdf_sha256"] = str(pdf_ack.get("sha256") or semantic.get("pdf_sha256") or "")
            event_type = str(row.get("event_type") or "")
            if event_type not in {"delivery.send_text", "delivery.send_pdf"}:
                continue
            if event_type == "delivery.send_pdf" and output.get("pdf_sha256"):
                result["pdf_sha256"] = str(output.get("pdf_sha256"))
            message = str(output.get("message") or
                          (output.get("semantic_output") or {}).get("message") or "").strip()
            if message and message not in result["messages"]:
                result["messages"].append(message)
            receipts = output.get("channel_receipts") or []
            for receipt in receipts if isinstance(receipts, list) else []:
                if not isinstance(receipt, dict):
                    continue
                channel = str(receipt.get("channel") or "")
                delivered = receipt.get("delivered") is True or receipt.get("visible") is True
                if channel == "web" and delivered:
                    result["web"] = True
                if channel == "qq" and delivered:
                    if event_type == "delivery.send_pdf":
                        result["qq_pdf"] = True
                        result["pdf_sha256"] = str(receipt.get("sha256") or output.get("sha256") or "")
                    else:
                        result["qq_text"] = True
    return result


def evaluate_run(workspace: str | Path, job_id: str, *,
                 projection: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stable checklist and score for a run-trace projection."""
    root = Path(workspace).resolve()
    cycle = root / "state" / "cycles" / job_id
    completion = dict(projection.get("completion") or {})
    outcome = dict(projection.get("outcome") or {})
    counts = dict(projection.get("counts") or {})
    intent = dict(projection.get("intent_contract") or {})
    constraints = dict(intent.get("execution_constraints") or {})
    report_required = str(intent.get("report_policy") or "none") != "none"
    evolution_required = bool(constraints.get("evolution_cycle"))
    mode = str(constraints.get("notification_mode") or "legacy")
    runtime = _runtime_evidence(root, job_id)
    checks: list[dict[str, Any]] = []

    def add(identifier: str, label: str, passed: bool | None, *, actual: Any,
            expected: str, evidence: str = "", required: bool = True) -> None:
        status = "not_applicable" if passed is None else "passed" if passed else "failed"
        checks.append({"id": identifier, "label": label, "status": status,
                       "required": required, "actual": actual,
                       "expected": expected, "evidence": evidence})

    add("terminal_closure", "根流程真实闭环",
        completion.get("status") == "completed" and completion.get("failed_nodes") == 0,
        actual={"status": completion.get("status"), "failed_nodes": completion.get("failed_nodes")},
        expected="completed 且当前失败节点为 0")
    add("event_observability", "业务 Event 可审计",
        int(counts.get("business_events") or 0) > 0 and bool(projection.get("flows")),
        actual={"business_events": counts.get("business_events"),
                "flows": len(projection.get("flows") or [])},
        expected="至少一个实际 Flow，且业务 Event 输入输出进入日志")

    root_flow_type = str(completion.get("flow_type") or "")
    improvement_workstream = root_flow_type in {"learning_improvement_cycle", "self_improvement_cycle"}
    project = dict(outcome.get("project") or {})
    project_rounds=int(project.get('rounds') or 0)
    if project_rounds <= 1:
        project_ok=(isinstance(project.get('baseline'),(int,float)) and
                    str(project.get('leakage_check') or '').lower() not in {'','failed','false'})
    else:
        project_ok = (isinstance(project.get("baseline"), (int, float)) and
                      isinstance(project.get("candidate"), (int, float)) and
                      project.get("split_reused") is True and
                      str(project.get("leakage_check") or "").lower() not in {"", "failed", "false"})
    add("matched_project_iteration", "项目迭代为匹配比较", None if improvement_workstream else project_ok,
        actual={key: project.get(key) for key in
                ("baseline", "candidate", "effect", "split_reused", "leakage_check")},
        expected="一轮任务有真实 baseline；多轮比较还须有 candidate、复用 split 且泄漏检查通过",
        required=not improvement_workstream)

    learning = dict(outcome.get("learning") or {})
    learning_expected = any(str(flow.get("flow_type")) == "active_learning"
                            for flow in projection.get("flows") or [])
    handoff = Path(str(learning.get("handoff_path") or ""))
    add("learning_consumption", "主动学习被下游消费",
        (learning.get("consumed") is True and bool(learning.get("source_url")) and
         learning.get('handoff_ready') is True and
         learning.get('comparison_complete') is True and handoff.is_file()) if learning_expected else None,
        actual={"consumed": learning.get("consumed"), "source_url": learning.get("source_url"),
                "handoff_ready": learning.get('handoff_ready'),
                "comparison_complete": learning.get('comparison_complete'),
                "handoff_exists": handoff.is_file()},
        expected="真实来源与 ready handoff 存在，并由后一轮匹配实验消费和完成效果比较",
        required=learning_expected)

    final = _read(cycle / "final_summary.json")
    frozen = _read(cycle / "final_run_state.json")
    evolved = frozen.get("evolution_outcome") if isinstance(frozen.get("evolution_outcome"), dict) else {}
    decision = str(final.get("decision") or (outcome.get("evolution") or {}).get("decision") or "")
    decision = decision or str(evolved.get("status") or "")
    honest = decision in {"promoted", "rejected", "inconclusive", "no_change"}
    if decision == "promoted":
        honest = honest and final.get("production_effective") is True and final.get("evidence_verified") is True
    elif decision:
        honest = honest and final.get("production_effective") is not True
    add("evolution_settlement", "自进化按证据诚实裁决", honest if evolution_required else None,
        actual={"decision": decision, "production_effective": final.get("production_effective"),
                "matched_verified": final.get("matched_verified")},
        expected="失败候选拒绝；只有匹配实验和生产验证均通过才 promoted",
        evidence=str(cycle / "final_summary.json"), required=evolution_required)

    delivery = dict(outcome.get("delivery") or {})
    delivery["web"] = delivery.get("web") is True or runtime["web"]
    delivery["text"] = delivery.get("text") is True or runtime["qq_text"]
    delivery["report"] = delivery.get("report") is True or runtime["qq_pdf"]
    # Improvement workstreams have one evidence-bound settlement message and
    # its report attachment; that pair is their terminal notification.
    if str(completion.get("flow_type") or "").endswith("improvement_cycle"):
        delivery["final"] = delivery.get("final") is True or runtime["qq_text"]
    requested_channels=set(constraints.get('delivery_channels') or ['web'])
    delivery_ok = (('web' not in requested_channels or delivery.get("web") is True) and
                   ('qq' not in requested_channels or (delivery.get("text") is True and delivery.get("final") is True)) and
                   (not report_required or delivery.get("report") is True))
    add("channel_delivery", "Web 与 QQ 交付闭环", delivery_ok,
        actual={**delivery,'requested_channels':sorted(requested_channels)},
        expected="所有请求渠道取得真实回执；要求报告时 PDF 也有回执")

    update_count = max(int(projection.get("user_update_count") or 0),
                       len(runtime["messages"]) + (1 if runtime["qq_pdf"] else 0))
    if mode == "standard":
        density_pass = ((3 <= update_count <= 12) if improvement_workstream
                        else (5 <= update_count <= 20))
    else:
        density_pass = None
    add("message_density", "过程消息密度", density_pass,
        actual={"mode": mode, "messages": update_count},
        expected=("独立学习/自进化约 3–12 条；项目周期约 5–20 条，均保留关键结论和下一步"),
        required=mode == "standard")

    report = _read(cycle / "report.json")
    work = root / "state" / "event_runtime" / "work" / job_id
    manifest = _read(work / "report_manifest.json")
    improvement_report = bool(manifest and str(completion.get("flow_type") or "").endswith("improvement_cycle"))
    if not report and improvement_report:
        report = {"status": "completed", "flow_id": manifest.get("flow_id"),
                  "node_outputs": runtime["outputs"]}
    add("report_flow_terminal", "报告 Flow 完整完成",
        report.get('status') == 'completed' if report_required else None,
        actual={'status': report.get('status'), 'flow_id': report.get('flow_id')},
        expected='报告子 Flow 的所有必需 Event 均完成',
        evidence=str(cycle / 'report.json'), required=report_required)
    render = ((report.get("node_outputs") or {}).get("render") or {})
    pdf = Path(str(render.get("pdf_path") or render.get("path") or ""))
    if not pdf.is_file() and manifest:
        pdf_item = next((row for row in manifest.get("artifacts") or []
                         if isinstance(row, dict) and str(row.get("path") or "").lower().endswith(".pdf")), {})
        pdf = Path(str(pdf_item.get("path") or ""))
    pdf_stats: dict[str, Any] = {"path": str(pdf), "exists": pdf.is_file(),
                                 "pages": 0, "text_chars": 0, "images": 0,
                                 "layout_errors": [], "text": ""}
    if pdf.is_file():
        try:
            import fitz
            with fitz.open(pdf) as document:
                pdf_stats["pages"] = len(document)
                for page in document:
                    page_text=page.get_text().strip()
                    pdf_stats["text_chars"] += len(page_text)
                    pdf_stats["text"] += page_text + "\n"
                    pdf_stats["images"] += len(page.get_image_info())
                    for block in page.get_text("blocks"):
                        if (block[0] < -2 or block[1] < -2 or
                                block[2] > page.rect.width + 2 or block[3] > page.rect.height + 2):
                            pdf_stats["layout_errors"].append("content outside page")
        except Exception as exc:  # pragma: no cover - optional runtime dependency
            pdf_stats["layout_errors"].append(f"{type(exc).__name__}: {exc}")
    pdf_ok = (pdf_stats["exists"] and 1 <= pdf_stats["pages"] <= 12 and
              pdf_stats["text_chars"] >= 500 and pdf_stats["images"] >= 1 and
              not pdf_stats["layout_errors"])
    add("report_readability", "PDF 可读性与信息密度", pdf_ok if report_required else None,
        actual=pdf_stats, expected="1–12 页、正文不少于 500 字、至少一张证据图、无越界或乱码",
        evidence=str(pdf), required=report_required)

    report_ack = _read(cycle / "report_ack.json")
    pdf_ack = next((row for row in report_ack.get("component_acks") or []
                    if isinstance(row, dict) and row.get("kind") == "pdf"), {})
    current_sha = _sha(pdf) if pdf.is_file() else ""
    sent_sha = str(pdf_ack.get("sha256") or runtime.get("pdf_sha256") or "")
    requested_channels=set(constraints.get('delivery_channels') or ['web'])
    same_version = (bool(current_sha) if 'qq' not in requested_channels
                    else bool(current_sha and sent_sha and current_sha == sent_sha))
    add("report_version_identity", "Web 与 QQ 报告版本一致", same_version if report_required else None,
        actual={"current_sha256": current_sha, "qq_ack_sha256": sent_sha},
        expected="QQ 请求时 ACK 哈希与当前 PDF 一致；Web-only 时当前索引 PDF 存在且可哈希",
        evidence=str(cycle / "report_ack.json"), required=report_required)

    summaries = ((report.get("node_outputs") or {}).get("summaries") or {}).get("files") or []
    if improvement_report:
        summaries = [str(row.get("path")) for row in manifest.get("artifacts") or []
                     if isinstance(row, dict) and str(row.get("path") or "").lower().endswith((".md", ".html"))]
    summary_ok = len(summaries) >= 2 and all(Path(str(path)).is_file() for path in summaries[:2])
    add("reader_artifacts", "读者产物完整", summary_ok if report_required else None,
        actual=list(summaries), expected="结果总结、运行总结及 PDF 均由报告 Flow 生成",
        required=report_required)

    graph=((report.get('node_outputs') or {}).get('flow_graph') or {}).get('semantic_output') or {}
    graph_verify=((report.get('node_outputs') or {}).get('flow_graph_verify') or {}).get('semantic_output') or {}
    projected_graph=projection.get('graph') or {}
    if improvement_report:
        graph_files=[row for row in manifest.get('artifacts') or [] if isinstance(row,dict)
                     and str(row.get('path') or '').lower().endswith(('graph.json','.svg'))]
        graph_ok=bool(graph_files and all(Path(str(row.get('path'))).is_file() for row in graph_files))
    else:
        graph_ok=bool(graph and graph_verify.get('verified') is True and
                      graph.get('counts') == projected_graph.get('counts'))
    add('runtime_graph_identity','网页与PDF运行图同源',graph_ok if report_required else None,
        actual={'report_counts':graph.get('counts'),'web_counts':projected_graph.get('counts'),
                'verified':graph_verify.get('verified')},
        expected='报告 graph.json 与网页运行投影的 Flow/Event/edge 数完全一致',required=report_required)

    draft=((report.get('node_outputs') or {}).get('draft') or {})
    markdown=Path(str(draft.get('path') or ''))
    ratio=0.0
    text=str(pdf_stats.pop('text',''))
    if text:
        marker=min([i for i in (text.find('附录：实际 Event'),text.find('附录：运行')) if i>=0] or [len(text)])
        research=''.join(text[:marker].split()); total=''.join(text.split())
        ratio=len(research)/max(1,len(total))
    add('report_research_ratio','PDF正文以研究内容为主',ratio>=.8 if report_required else None,
        actual={'ratio':round(ratio,3),'markdown':str(markdown)},expected='研究正文字符占比至少80%',required=report_required)

    log_path = root / "state" / "run_logs" / job_id / "events.jsonl"
    log_bytes = log_path.stat().st_size if log_path.is_file() else 0
    add("bounded_run_log", "运行日志有界", 0 < log_bytes <= 25_000_000,
        actual={"bytes": log_bytes, "megabytes": round(log_bytes / 1_000_000, 3)},
        expected="日志存在且不超过 25 MB", evidence=str(log_path))

    required_checks = [row for row in checks if row["required"] and row["status"] != "not_applicable"]
    passed = sum(row["status"] == "passed" for row in required_checks)
    failed = [row["id"] for row in required_checks if row["status"] == "failed"]
    score = round(100 * passed / len(required_checks), 1) if required_checks else 0.0
    return {"schema_version": 1, "job_id": job_id,
            "status": "passed" if not failed else "needs_attention",
            "score": score, "passed": passed, "required": len(required_checks),
            "failed_check_ids": failed, "checks": checks,
            "interpretation": "所有必需效果均有独立证据" if not failed else
                              "流程能运行，但仍有必需效果未被证据证明"}


__all__ = ["evaluate_run"]
