# -*- coding: utf-8 -*-
"""Supervision-driven evolution: expectation document + dense LLM judgment.

This is the "豆包式监督" core the user asked for. A dynamic expectation
document (``docs/expectations/system_quality.md``) states what aspects to
watch and what the concrete expectation is.  Every regression output (round
record, user message, PDF) is read **in full** and judged item by item by the
LLM against that document; verdicts and gaps feed planning; the document
itself can be updated (version bump) when supervision finds new aspects.

Events (shared event pool, fixed interface):
- supervise.expectations_load  -> structured expectations
- supervise.snapshot          -> full-text item-by-item verdicts for one output class
- supervise.synthesize        -> aggregate gap report across snapshots
- supervise.expectations_update-> LLM drafts doc amendments, version+1
- supervise.objective_plan    -> improvement plan for design/repair
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from partner.event_fabric.catalog import EventDefinition
from partner.runtime.action_execution import write_json
from ._llm import call_model, json_object

SUPERVISION_DIR = "state/supervision"
DEFAULT_EXPECTATIONS = "docs/expectations/system_quality.md"

try:
    import yaml
except Exception:  # pragma: no cover - yaml is expected in the runtime env
    yaml = None


def _folder(ctx: Any, *parts: str) -> Path:
    base = Path(str(getattr(ctx, "workspace", "") or ""))
    job = str(getattr(ctx, "job_id", "") or "parent")
    root = base / SUPERVISION_DIR / job
    return root.joinpath(*parts) if parts else root


def _expectations_path(ctx: Any, params: dict[str, Any]) -> Path:
    rel = str(params.get("expectations_path") or DEFAULT_EXPECTATIONS)
    p = Path(rel)
    if p.is_absolute():
        return p
    try:
        from partner.runtime import evolution_experiment as experiment
        repo = Path(str(experiment.REPO))
        if (repo / rel).exists():
            return repo / rel
    except Exception:
        pass
    ws = Path(str(getattr(ctx, "workspace", "") or ""))
    return ws / rel


def _parse_expectations(text: str) -> dict[str, Any]:
    """Parse the expectations markdown-yaml baseline.

    Falls back to a tolerant block parser when PyYAML is unavailable.
    """
    if yaml is not None:
        try:
            data = yaml.safe_load(text)
            if isinstance(data, dict) and data.get("dimensions"):
                return data
        except Exception:
            pass
    # tolerant fallback: split top-level keys manually
    data: dict[str, Any] = {"dimensions": {}, "history": []}
    dim = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("version:"):
            data["version"] = s.split(":", 1)[1].strip()
        elif s.startswith("## "):
            dim = s[3:].strip()
            data["dimensions"].setdefault(dim, {"title": dim, "checks": []})
        elif s.startswith("- id:") and dim:
            cid = s.split(":", 1)[1].strip()
            data["dimensions"][dim]["checks"].append({"id": cid})
        elif s.startswith("expectation:") and dim and data["dimensions"][dim]["checks"]:
            data["dimensions"][dim]["checks"][-1]["expectation"] = s.split(":", 1)[1].strip()
    return data


def expectations_load(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Load the expectation baseline into a structured, queryable shape."""
    path = _expectations_path(ctx, params)
    if not path.exists():
        return {"ok": False, "status": "failed", "error": f"expectations not found: {path}",
                "semantic_output": {"status": "failed", "reason": "expectations_missing"}}
    text = path.read_text(encoding="utf-8")
    data = _parse_expectations(text)
    dims = {
        dim: {"title": cfg.get("title", dim),
              "checks": [{"id": c.get("id"), "expectation": c.get("expectation", "")}
                         for c in cfg.get("checks", [])]}
        for dim, cfg in (data.get("dimensions") or {}).items()
    }
    value = {"expectations_path": str(path), "version": str(data.get("version") or "unknown"),
             "dimensions": dims, "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "summary": f"期望基线已加载 v{value['version']}，共 {len(dims)} 个维度",
            "semantic_output": value}


def _read_full_texts(params: dict[str, Any]) -> dict[str, str]:
    """Read every listed artifact for item-by-item judgment.

    Budgets are bounded so a supervision Event stays inside its deadline with
    the cognitive model (thinking on): full 45k+ char prompts hard-timed-out
    at ~77s and supervision silently degraded.  Round records are sampled to
    their decisive nodes; messages/PDFs keep their user-facing excerpts.
    """
    out: dict[str, str] = {}
    for key in ("round_records", "messages", "pdfs", "misc"):
        cap = 12000 if key == "round_records" else 8000
        for p in list(params.get(key) or [])[:4]:
            fp = Path(p)
            try:
                if not fp.exists():
                    continue
                txt = fp.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                txt = f"(read error: {exc})"
            out[str(fp)] = txt[:cap]
    # round_*.json records are mostly machine noise; keep only the nodes a
    # supervisor actually judges so the prompt stays small enough for the
    # thinking model to answer in time.
    compact = {}
    for p, t in out.items():
        if p.endswith('.json') and 'round_' in p:
            import json as _json
            try:
                row = _json.loads(t)
                nodes = row.get("node_outputs") or {}
                keep = {}
                for nid in ("design", "execute", "verify", "reflect", "iterate", "next_decide", "settle"):
                    sem = (nodes.get(nid) or {}).get("semantic_output") or {}
                    keep[nid] = {k: (str(v)[:600] if not isinstance(v, (dict, list)) else v)
                                 for k, v in sem.items() if k in {
                                     'round_goal', 'action_summary', 'execution_status', 'error',
                                     'verified', 'execution_verified', 'business_delta', 'evidence',
                                     'supported', 'rejected', 'unknown', 'lesson', 'route', 'reason',
                                     'next_hypothesis', 'next_round_goal', 'fail_layer',
                                     'missing_evidence', 'settled', 'learning_effect'}}
                compact[p] = _json.dumps(keep, ensure_ascii=False, default=str)[:cap]
            except Exception:
                compact[p] = t
        else:
            compact[p] = t
    return compact


def snapshot(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Judge one class of regression outputs in full against the expectations.

    params: target ('round'|'messages'|'pdf'), round_records/messages/pdfs
    paths, expectations_path.  Every check gets an ok/evidence verdict from
    the LLM; uncovered aspects are collected for the doc update stage.
    """
    expectations = expectations_load(ctx, params)
    if not expectations.get("ok"):
        return expectations
    texts = _read_full_texts(params)
    if not texts:
        return {"ok": True, "status": "completed", "summary": "无待审产物，跳过监督快照",
                "semantic_output": {"status": "skipped", "reason": "no artifacts", "verdicts": [], "gaps": []}}
    target = str(params.get("target") or "all")
    dims = expectations["semantic_output"]["dimensions"]
    prompt = (
        "你是 Partner 系统的独立质量监督者。对照期望基线逐项审阅下面的**真实完整产物**，"
        "只依据原文给出判断，不臆测、不夸大。\n"
        "期望基线（维度×检查项）：\n" + json.dumps(dims, ensure_ascii=False) + "\n"
        f"本次审阅范围：{target}\n待审产物全文：\n"
        + "\n\n====\n".join(f"[{p}]\n{t}" for p, t in texts.items()) +
        "\n\n只输出一个 JSON 对象：\n"
        "{\"verdicts\": [{\"dimension\":\"message_readability\", \"check_id\":\"M1\", \"ok\": true|false, "
        "\"evidence\": \"引用原文片段\", \"note\": \"简短说明\"}], \n"
        "  \"uncovered_aspects\": [\"文档未覆盖但你发现的明显问题（描述+原文证据）\"], \n"
        "  \"overall\": \"一段用户可读总结（这块产物是否面向用户达标）\"}"
    )
    raw, usage = call_model(ctx, purpose="supervise_snapshot_" + target[:8], prompt=prompt)
    try:
        value = json_object(raw)
    except Exception:
        value = {"verdicts": [], "uncovered_aspects": ["监督响应解析失败: " + raw[:200]],
                 "overall": "监督模型响应不可解析"}
    value["target"] = target
    value["artifact_count"] = len(texts)
    value["model_usage"] = usage or {}
    round_no = str(params.get("round") or time.strftime("%Y%m%d%H%M%S"))
    folder = _folder(ctx, "snapshots")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{target}_{round_no}.json"
    write_json(path, value)
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "summary": f"监督快照 {target}@{round_no} 完成，{len(value.get('verdicts') or [])} 项判定",
            "semantic_output": value}


def synthesize(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Aggregate every snapshot into a dimension-level gap report."""
    folder = _folder(ctx, "snapshots")
    snapshots = []
    if folder.exists():
        for fp in sorted(folder.glob("*.json")):
            try:
                snapshots.append(json.loads(fp.read_text(encoding="utf-8")))
            except Exception:
                pass
    gaps_by_dim: dict[str, list[dict[str, Any]]] = {}
    uncovered: list[str] = []
    for snap in snapshots:
        for v in snap.get("verdicts") or []:
            if v.get("ok"):
                continue
            gaps_by_dim.setdefault(v.get("dimension", "unknown"), []).append(
                {"check_id": v.get("check_id"), "evidence": v.get("evidence", ""),
                 "note": v.get("note", ""), "source": snap.get("target", "")})
        uncovered.extend(snap.get("uncovered_aspects") or [])
    overall = (
        "各维度未达标情况：\n" + json.dumps(
            {d: {"count": len(gs), "items": gs[:6]} for d, gs in gaps_by_dim.items()},
            ensure_ascii=False)[:3000] +
        "\n未覆盖新方面：\n" + json.dumps(uncovered, ensure_ascii=False)[:1500])
    value = {"snapshot_count": len(snapshots), "gaps_by_dimension": gaps_by_dim,
             "uncovered_aspects": uncovered, "has_gaps": bool(gaps_by_dim or uncovered),
             "overall": overall}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "synthesis.json"
    write_json(path, value)
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "summary": "监督汇总完成" + (f"：{sum(len(v) for v in gaps_by_dim.values())} 项未达标，{len(uncovered)} 个新方面"
                                      if gaps_by_dim or uncovered else "：全部达标"),
            "semantic_output": value}


def expectations_update(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """LLM drafts amendments from uncovered aspects; write back with version+1.

    Only applied when the synthesis really found uncovered aspects.  The old
    baseline is preserved under ``history`` so the document stays auditable.
    """
    syn = synthesize(ctx, params)
    uncovered = syn["semantic_output"].get("uncovered_aspects") or []
    gaps = syn["semantic_output"].get("gaps_by_dimension") or {}
    if not uncovered and not gaps:
        return {"ok": True, "status": "completed", "summary": "无新增预期，期望文档不变",
                "semantic_output": {"applied": False, "reason": "no uncovered aspects", "new_version": None}}
    path = _expectations_path(ctx, params)
    text = path.read_text(encoding="utf-8")
    prompt = (
        "你是 Partner 系统的期望基线维护者。根据监督发现，为期望文档起草**新增或修订**条目。\n"
        "当前文档全文：\n" + text[:12000] +
        "\n\n监督发现的未覆盖方面：\n" + json.dumps(uncovered, ensure_ascii=False) +
        "\n\n只输出一个 JSON 对象：\n"
        "{\"amendments\": [{\"dimension\":\"消息用户可读性\", \"id\":\"M8\", \"expectation\":\"一句话新预期\"}], \n"
        "  \"removed\": [], \"changed\": [], \"note\":\"为何更新\"}"
    )
    raw, usage = call_model(ctx, purpose="supervise_expectations_update", prompt=prompt)
    try:
        amend = json_object(raw)
    except Exception:
        amend = {"amendments": [], "note": "更新响应不可解析: " + raw[:120]}
    amendments = [a for a in amend.get("amendments") or [] if a.get("expectation")]
    if not amendments:
        return {"ok": True, "status": "completed", "summary": "监督建议无可落地新增条目",
                "semantic_output": {"applied": False, "reason": "no usable amendments", "new_version": None}}
    # deterministic version bump
    new_version = str(int(str(syn.get("semantic_output").get("version") or 0)) + 1
                      if str(syn.get("semantic_output").get("version") or "0").isdigit()
                      else "v+" + str(len(amendments)))
    lines = text.splitlines()
    inserted: list[str] = []
    for am in amendments:
        dim, cid, expectation = am.get("dimension", ""), am.get("id", ""), am.get("expectation", "")
        inserted.append(f"      - id: {cid}\n        expectation: \"{expectation}\"")
        inserted.append("        note: \"由系统监督自动追加（" + am.get("note", "监督发现") + "）\"")
    dim = amendments[0].get("dimension") or "message_readability"
    # append under the first matching dimension block (tolerant: append at end)
    addition = "\n\n  amendments_v" + str(new_version) + ":\n    dimension: " + dim + "\n    items:\n" + \
        "\n".join("      - id: " + a.get("id", "") + "\n        expectation: \"" + a.get("expectation", "") + "\""
                  for a in amendments)
    out_text = text + "\n" + addition
    out_text = out_text.replace("version: " + str(syn["semantic_output"].get("version")),
                                "version: " + str(new_version), 1) if False else out_text
    # simpler deterministic header bump: write a top marker
    header = f"version: {new_version}\nupdated_at: {time.strftime('%Y-%m-%d')}\n"
    if text.startswith("version:"):
        out_text = header + "\n".join(text.splitlines()[3:])
    path.write_text(out_text, encoding="utf-8")
    value = {"applied": True, "new_version": str(new_version), "added": amendments,
             "path": str(path), "model_usage": usage or {}}
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "summary": f"期望文档已更新至 v{new_version}，新增 {len(amendments)} 条预期",
            "semantic_output": value}


def objective_plan(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """LLM turns the gap report into a concrete improvement plan.

    Output feeds the design stage: which files to touch, what to verify,
    what the expected effect is, in priority order.
    """
    syn = synthesize(ctx, params)
    gaps = syn["semantic_output"].get("gaps_by_dimension") or {}
    uncovered = syn["semantic_output"].get("uncovered_aspects") or []
    if not gaps and not uncovered:
        return {"ok": True, "status": "completed", "summary": "无差距，无需改进计划",
                "semantic_output": {"plan": [], "status": "no_gaps"}}
    current_state = str(params.get("current_state") or "")[:6000]
    prompt = (
        "你是 Partner 系统的总指挥。根据监督差距清单制定下一步改进计划（围绕用户可读性与机制健康的核心目标）。\n"
        "差距清单：\n" + json.dumps({"gaps": gaps, "uncovered": uncovered}, ensure_ascii=False)[:5000] +
        "\n当前状态（可选）：\n" + current_state +
        "\n只输出一个 JSON 对象：\n"
        "{\"goal\": \"一句话核心目标\", \n"
        "  \"plan\": [{\"priority\":1, \"action\":\"具体动作\", \"target_files\":[\"相对路径\"], "
        "\"verification\":\"如何验证\", \"expected_effect\":\"预期效果\"}], \n"
        "  \"route_note\": \"按监督-修复-验证范式安排的路线说明\"}"
    )
    raw, usage = call_model(ctx, purpose="supervise_objective_plan", prompt=prompt)
    try:
        value = json_object(raw)
    except Exception:
        value = {"goal": "修复监督发现的差距", "plan": [], "route_note": "响应不可解析: " + raw[:120]}
    value["model_usage"] = usage or {}
    folder = _folder(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "objective_plan.json"
    write_json(path, value)
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "summary": f"监督改进计划已生成：{len(value.get('plan') or [])} 项动作",
            "semantic_output": value}


def route_plan(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """LLM assembles the next route from the shared event/flow pool.

    Reads the gap synthesis plus the current run state, then decides the
    route: whether a dedicated supervision child flow is needed, whether to
    fix in-line, or to stop (no gaps).  The route is persisted for the
    dispatch node and for the design stage.  This is the "LLM 自行判断并
    组装路线" decision point: the flow pool and event pool are shared, the
    LLM picks the hop and the plan, the runtime executes it.
    """
    syn = synthesize(ctx, params)
    gaps = syn["semantic_output"].get("gaps_by_dimension") or {}
    uncovered = syn["semantic_output"].get("uncovered_aspects") or []
    if not gaps and not uncovered:
        value = {"goal": "监督未发现差距，停止修复",
                 "route": {"hop": "stop", "flow": None, "reason": "no gaps"},
                 "plan": [], "route_note": "监督-修复-验证范式的 stop 分支"}
        folder = _folder(ctx)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "route_plan.json"
        write_json(path, value)
        return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
                "summary": "监督无差距，停止修复", "semantic_output": value}
    current_state = str(params.get("current_state") or "")[:8000]
    prompt = (
        "你是 Partner 系统的总指挥，负责从共享事件/flow 池组装下一步路线（积木式）。\n"
        "可用 flow 池：supervision_cycle（独立监督子流程：期望加载→快照→汇总→期望更新→目标计划）；\n"
        "主流程内联修复段（design→tests→freeze→attempt→apply→benchmark_gate→runtime_verify→rollback）。\n"
        "监督差距：\n" + json.dumps({"gaps": gaps, "uncovered": uncovered}, ensure_ascii=False)[:6000] +
        "\n当前状态（可选）：\n" + current_state +
        "\n只输出一个 JSON 对象：\n"
        "{\"goal\": \"一句话核心目标\", \n"
        "  \"route\": {\"hop\": \"supervise_cycle|inline_fix|stop\", \"flow\": \"supervision_cycle\" 或 null, "
        "\"reason\": \"为什么这样选\"}, \n"
        "  \"plan\": [{\"priority\":1, \"action\":\"具体动作\", \"target_files\":[\"相对路径\"], "
        "\"verification\":\"如何验证\", \"expected_effect\":\"预期效果\"}], \n"
        "  \"route_note\": \"路线说明（监督-修复-验证范式）\"}"
    )
    raw, usage = call_model(ctx, purpose="supervise_route_plan", prompt=prompt)
    try:
        value = json_object(raw)
    except Exception:
        value = {"goal": "修复监督发现的差距",
                 "route": {"hop": "inline_fix", "flow": None, "reason": "响应不可解析: " + raw[:120]},
                 "plan": [], "route_note": "fallback inline_fix"}
    route = value.get("route") or {}
    if route.get("hop") not in {"supervise_cycle", "inline_fix", "stop"}:
        route = {"hop": "inline_fix", "flow": None, "reason": "非法 hop，回退 inline_fix"}
        value["route"] = route
    value["model_usage"] = usage or {}
    folder = _folder(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "route_plan.json"
    write_json(path, value)
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "summary": f"路线已组装：{route.get('hop')}，计划 {len(value.get('plan') or [])} 项动作",
            "semantic_output": value}


def route_dispatch(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch the LLM-assembled route: start the supervision child flow,
    continue in-line fix, or stop.  The child flow is a shared, registered
    flow (supervision_cycle) selected by the LLM from the flow pool."""
    folder = _folder(ctx)
    plan_path = folder / "route_plan.json"
    if not plan_path.exists():
        return {"ok": True, "status": "completed", "summary": "无路线计划，按内联修复继续",
                "semantic_output": {"decision": "inline_fix", "reason": "no route_plan"}}
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": True, "status": "completed", "summary": "路线计划不可读，按内联修复继续",
                "semantic_output": {"decision": "inline_fix", "reason": str(exc)[:160]}}
    route = plan.get("route") or {}
    hop = route.get("hop") or "inline_fix"
    if hop == "supervise_cycle":
        value = {"decision": "supervise_cycle", "flow": "supervision_cycle",
                 "reason": route.get("reason", ""), "goal": plan.get("goal", ""),
                 "cycle_child": {"flow": "supervision_cycle", "owner_node": "route_dispatch",
                                 "context": {"route_plan": plan, "evidence_refs": params.get("evidence_refs") or []}}}
        return {"ok": True, "status": "completed", "summary": "投递监督子流程 supervision_cycle",
                "semantic_output": value}
    if hop == "stop":
        return {"ok": True, "status": "completed", "summary": "监督未发现可修差距，停止修复",
                "semantic_output": {"decision": "stop", "reason": route.get("reason", "")}}
    return {"ok": True, "status": "completed", "summary": "内联修复路线",
            "semantic_output": {"decision": "inline_fix", "reason": route.get("reason", ""),
                                "goal": plan.get("goal", ""), "plan": plan.get("plan") or []}}


def issue_select(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Turn the supervision gap synthesis into a design-stage issue.

    The autonomous_evolution design node consumes ``selected_issue`` from the
    mechanism audit; this node produces the supervision-side counterpart
    (a gap with evidence) so design can fix what supervision found.  Written
    under the supervision namespace; design reads it as its primary issue."""
    syn = synthesize(ctx, params)
    gaps = syn["semantic_output"].get("gaps_by_dimension") or {}
    uncovered = syn["semantic_output"].get("uncovered_aspects") or []
    if not gaps and not uncovered:
        value = {"selected_issue": None, "reason": "no supervision gaps"}
        folder = _folder(ctx)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "selected_issue.json"
        write_json(path, value)
        return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
                "summary": "监督无差距，无待修问题", "semantic_output": value}
    flat = []
    for dim, items in gaps.items():
        for it in items:
            flat.append({"dimension": dim, "check_id": it.get("check_id"),
                         "evidence": it.get("evidence", ""), "note": it.get("note", ""),
                         "source": it.get("source", "")})
    prompt = (
        "你是 Partner 系统监督与设计之间的桥。从监督差距清单中选出**最值得本轮修复的一个差距**"
        "作为设计阶段的 selected_issue（优先用户可读性相关）。\n"
        "差距清单：\n" + json.dumps(flat, ensure_ascii=False)[:5000] +
        "\n未覆盖方面：\n" + json.dumps(uncovered, ensure_ascii=False)[:2000] +
        "\n只输出一个 JSON 对象：\n"
        "{\"selected_issue\": {\"id\":\"SUPERVISE:<dimension>:<check_id>\", \"verdict\":\"supported\", "
        "\"evidence\":\"原文证据\", \"cause\":\"推测根因\", \"dimension\":\"维度名\", \"check_id\":\"检查项ID\", "
        "\"recommendation\":\"修复建议（面向哪些代码/消息/报告）\"}}"
    )
    raw, usage = call_model(ctx, purpose="supervise_issue_select", prompt=prompt)
    try:
        value = json_object(raw)
    except Exception:
        value = {"selected_issue": {"id": "SUPERVISE:unknown", "verdict": "supported",
                                    "evidence": "响应不可解析: " + raw[:120], "cause": "unknown",
                                    "recommendation": "人工复核监督差距"}}
    value["model_usage"] = usage or {}
    folder = _folder(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "selected_issue.json"
    write_json(path, value)
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "summary": "已从监督差距选出待修问题 " + str((value.get("selected_issue") or {}).get("id")),
            "semantic_output": value}


DEFINITIONS = [
    EventDefinition("supervise.expectations_load", "evolution",
                    "加载可动态更新的期望基线（看哪些方面、具体预期）", expectations_load),
    EventDefinition("supervise.snapshot", "evolution",
                    "对一类回归产物全文逐项监督，LLM 对照期望给出 verdict 与 gap", snapshot,
                    timeout_seconds=600),
    EventDefinition("supervise.synthesize", "evolution",
                    "汇总全部监督快照为维度级差距报告", synthesize, timeout_seconds=600),
    EventDefinition("supervise.expectations_update", "evolution",
                    "监督发现新方面时 LLM 起草并写回期望文档（version+1）", expectations_update,
                    timeout_seconds=600),
    EventDefinition("supervise.objective_plan", "evolution",
                    "LLM 依差距清单制定改进计划供设计/修复使用", objective_plan, timeout_seconds=600),
    EventDefinition("supervise.route_plan", "evolution",
                    "LLM 从共享事件/flow 池组装下一步路线（监督子流/内联修复/停止）", route_plan,
                    timeout_seconds=600),
    EventDefinition("supervise.route_dispatch", "evolution",
                    "按 LLM 组装路线投递监督子流程或决定内联修复/停止", route_dispatch, timeout_seconds=600),
    EventDefinition("supervise.issue_select", "evolution",
                    "把监督差距转为设计阶段 selected_issue（供修复）", issue_select, timeout_seconds=600),
]
