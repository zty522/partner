"""Cross-cutting self-reflection events.

After every PROJECT_ITERATION round (and other aspects on schedule) the
runtime reads the round's full flow_outputs, the instance's recent round
summaries, and the instance's history of self-evolution modifications,
then runs an LLM-driven three-pass review that mirrors the intent
pipeline:

  aspect_observe       — extract the actual facts of what happened
  aspect_counter_read  — attack the observe: what is missing, what is
                          misleading, what does the model not see
  aspect_synthesize    — decide whether non-trivial improvements exist;
                          if so, emit at most 3 candidate modifications
                          shaped for self_evolution.candidate_propose

The aspect name ("iteration" | "intent" | "message" | "pdf_report" |
"event_flow") selects which slice of partner history the events read.
The pipeline is the same for every aspect.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from partner.event_fabric.catalog import EventDefinition
from ._llm import call_model, json_object


VALID_ASPECTS = ("iteration", "intent", "message", "pdf_report", "event_flow", "pre_iteration")


def _workspace(ctx: Any) -> str:
    return str(getattr(ctx, "workspace", "") or "")


def _output(params: dict[str, Any], node_id: str) -> dict[str, Any]:
    outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    value = outputs.get(node_id)
    return dict(value) if isinstance(value, dict) else {}


def _aspect(params: dict[str, Any]) -> str:
    raw = str(params.get("aspect") or (params.get("intent_contract") or {}).get("aspect") or "iteration")
    return raw if raw in VALID_ASPECTS else "iteration"


def _load_history(workspace: str, aspect: str, limit: int) -> dict[str, Any]:
    """Pull lightweight summaries of recent rounds and self-evolution
    modifications from on-disk ledgers. Bounded by `limit` per source so
    the prompt stays cheap.
    """
    if not workspace:
        return {"round_summaries": [], "evolution_events": [], "issue_open": 0}
    base = Path(workspace)
    out = {"round_summaries": [], "evolution_events": [], "issue_open": 0}
    try:
        from partner.observe.precedents import precedents_as_prompt_text
        out["precedents"] = precedents_as_prompt_text(base, max_chars=4000)
    except Exception:  # noqa: BLE001
        out["precedents"] = ""

    rounds_root = base / "state/event_flows"
    if rounds_root.exists():
        for path in [Path(r['path']) for r in __import__('partner.index.resource_catalog',fromlist=['ResourceCatalog']).ResourceCatalog(base).query('flow',limit=limit)]:
            try:
                rec = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
            if rec.get("flow_type") not in {"project_iteration", "pdf_report", "message_delivery",
                                            "direct_answer", "intent", "new_project"}:
                continue
            summary = {
                "flow_id": rec.get("flow_id", ""),
                "flow_type": rec.get("flow_type", ""),
                "project_id": rec.get("project_id", ""),
                "instance_id": rec.get("instance_id", ""),
                "status": rec.get("status", ""),
                "ready_node_ids": list(rec.get("ready_node_ids") or []),
                "completed_node_ids": list(rec.get("completed_node_ids") or []),
                "failed_node_ids": list(rec.get("failed_node_ids") or []),
                "updated_at": rec.get("updated_at", ""),
            }
            for node in ("reflect", "select", "synthesize", "answer", "notify", "claims"):
                value = rec.get("node_outputs", {}).get(node)
                if isinstance(value, dict) and value.get("summary"):
                    summary["summary"] = value.get("summary")
                    break
            out["round_summaries"].append(summary)

    evo = base / "share/mind/governance/evolution_events_epoch2.jsonl"
    if not evo.exists():
        evo = base / "state/event_flows/_evolution_active.jsonl"
    if evo.exists():
        try:
            lines = evo.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
            for line in lines:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                out["evolution_events"].append({
                    "event_type": rec.get("event_type", ""),
                    "subject_id": rec.get("subject_id", ""),
                    "occurred_at": rec.get("occurred_at", ""),
                    "payload": rec.get("payload") or {},
                })
        except OSError:
            pass

    issues_path = base / "state/governance/issues.jsonl"
    if issues_path.exists():
        try:
            opened = 0
            for line in issues_path.read_text(encoding="utf-8",
                                             errors="replace").splitlines()[-200:]:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("status") == "open":
                    opened += 1
            out["issue_open"] = opened
        except OSError:
            pass
    return out


def aspect_observe(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    aspect = _aspect(params)
    current = params.get("current_round") or _output(params, "reflect") or params.get("flow_outputs") or {}
    history = _load_history(_workspace(ctx), aspect, limit=8)
    payload = {
        "aspect": aspect,
        "current_round_summary": current.get("summary") or current.get("semantic_output") or {},
        "current_round_weight": (current.get("semantic_output") or {}).get("weight"),
        "current_round_business_delta": (current.get("semantic_output") or {}).get("business_delta"),
        "current_round_lessons": (current.get("semantic_output") or {}).get("lesson"),
        "round_summaries_count": len(history["round_summaries"]),
        "recent_rounds": history["round_summaries"][:5],
        "self_evolution_history_count": len(history["evolution_events"]),
        "self_evolution_history": history["evolution_events"][:5],
        "open_issues": history["issue_open"],
    }
    raw, usage = call_model(ctx, purpose=f"aspect_{aspect}_observe", prompt=(
        "你是 Partner 自进化 observe Event。aspect=" + aspect + "。"
        "下面 current_round 是本轮的真实事实（observe 不评估好坏，只列事实）。"
        "recent_rounds 是同一 instance 的最近 N 轮 summary。"
        "self_evolution_history 是这个 instance 已记录的自进化修改。"
        "请输出 JSON 字段：facts (本轮发生的具体事件，每条 1 句)；"
        "missing_evidence (本应该记录但没有的证据)；"
        "recurring_patterns (recent_rounds 里反复出现的模式)；"
        "modification_outcomes (self_evolution_history 里最近修改的成功/失败)。"
        "不评估，不建议，只列事实。如果某一项为空，明确写 none，不要替观察做判断。\n"
        "current_round=" + json.dumps(payload["current_round_summary"], ensure_ascii=False)[:6000]
        + "\nrecent_rounds=" + json.dumps(payload["recent_rounds"], ensure_ascii=False)[:8000]
        + "\nself_evolution_history=" + json.dumps(payload["self_evolution_history"], ensure_ascii=False)[:6000]
        + "\nopen_issues=" + str(payload["open_issues"])
    ))
    value = json_object(raw)
    value["aspect"] = aspect
    value["history_snapshot"] = {
        "round_count": payload["round_summaries_count"],
        "evolution_count": payload["self_evolution_history_count"],
        "open_issues": payload["open_issues"],
    }
    return {"ok": True, "status": "completed",
            "semantic_output": value, "token_usage": usage,
            "summary": f"aspect={aspect} observe: "
                       f"{len(value.get('facts') or [])} facts, "
                       f"{len(value.get('recurring_patterns') or [])} patterns"}


def aspect_counter_read(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    aspect = _aspect(params)
    upstream = _output(params, "observe").get("semantic_output") or {}
    raw, usage = call_model(ctx, purpose=f"aspect_{aspect}_counter_read", prompt=(
        "你是 Partner 自进化 counter_read Event。aspect=" + aspect + "。"
        "上一轮 observe 的事实清单如下，逐条找三种漏洞：\n"
        "1) 缺失：observe 漏掉或没看出来的事实，包括 partner runtime 自身行为（例：调了几个 event、retry 几次、被 message_critic 拒收次数）。"
        "2) 反例：observe 列的事实和最近 5 轮 summary 是否矛盾。"
        "3) reward hacking：observe 列的事实是否只是叙述、而不是真实可核验的事实（叙述不等于改进；同一段被记成多轮不同结论也是漏洞）。\n"
        "只输出 JSON 字段 missing, contradictions, fabrication_risks，每条 1 句话，不要给出建议。\n"
        "observe=" + json.dumps(upstream, ensure_ascii=False)[:14000]
    ))
    value = json_object(raw)
    value["aspect"] = aspect
    return {"ok": True, "status": "completed",
            "semantic_output": value, "token_usage": usage,
            "summary": f"aspect={aspect} counter_read: "
                       f"{len(value.get('missing') or [])} missing, "
                       f"{len(value.get('fabrication_risks') or [])} fabrication_risks"}


def aspect_synthesize(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Decide whether non-trivial improvements exist.

    When the decision is no-op the event writes aspect/no_op and
    aspect/throttled counters; downstream code skips spawning
    self_evolution. When decision is candidate it emits up to 3
    candidates shaped for self_evolution.candidate_propose.
    """
    aspect = _aspect(params)
    observe = _output(params, "observe").get("semantic_output") or {}
    counter = _output(params, "counter_read").get("semantic_output") or {}
    history = _load_history(_workspace(ctx), aspect, limit=6)
    no_op_streak = params.get("no_op_streak", 0)
    raw, usage = call_model(ctx, purpose=f"aspect_{aspect}_synthesize", prompt=(
        "你是 Partner 自进化 synthesize Event。aspect=" + aspect + "。" + chr(10)
        + "【已知修复案例库 (precedents) - INSTANCE 症状匹配时复用 fix_shape 模板】" + chr(10)
        + str(history.get("precedents", "")) + chr(10)
        + "已知事实=" + json.dumps(observe, ensure_ascii=False)[:10000]
        + "\n漏洞=" + json.dumps(counter, ensure_ascii=False)[:10000]
        + "\n最近 self_evolution 修改=" + json.dumps(history["evolution_events"][:5], ensure_ascii=False)[:6000]
        + "\n最近轮次=" + json.dumps(history["round_summaries"][:3], ensure_ascii=False)[:6000]
        + "\n连续 no_op 次数=" + str(no_op_streak)
        + "\n\n任务：综合上述，输出 JSON 字段 decision ('no_op' | 'candidate'), "
          "reason (一段话解释为什么)，"
          "improvement_focus (一段话描述本轮最值得改进的 1 个具体机制点), "
          "candidates (数组，最多 3 个，每个含 target_files, change, "
          "causal_hypothesis, expected_improvement, risk, rollback 字段；"
          "target_files 必须落到 partner/ 仓库内具体相对路径；"
          "change 必须能被改写为小 diff；"
          "expected_improvement 必须能被某条事实或漏洞佐证，不接受泛泛而谈。"
          "当 decision='candidate' 时 candidates 至少 1 条)。"
          "判定规则：本 aspect 已连续 3 轮 no_op → 必须 decision='candidate' 且 candidates 至少 1 条 target_files 落在上方 precedents 提示的 root_cause_files 之一；本 aspect 已连续 5 轮 no_op → 必须 decision='candidate',"
          "且 candidates 中至少 1 条要求读 runtime_metrics 这种已存在指标，否则视为 not 非平凡。"
          "如果上方 precedents 中某 case 的 symptom_signals 在 INSTANCE 历史中持续匹配（observe_completed 连续 3+ 轮命中信号），必须 decision='candidate' 且复用该 case 的 fix_shape 作为 candidate_template.change 草案。如果 facts/fabrication_risks 都为空，且连续 no_op < 3，"
          "且 self_evolution_history 最近 5 条都 rejected → 倾向 decision='no_op'。"
          "no_op 时 improvement_focus 与 candidates 可以为空数组。"
          "禁止建议改动 freeze_boundary.yaml、catalog.py、_llm.py、self_evolution.promotion_decide。"
          "禁止凭空编造路径；不确定时写 no_op 也不要猜。"
    ))
    value = json_object(raw)
    decision = str(value.get("decision") or "").strip()
    if decision not in {"no_op", "candidate"}:
        decision = "no_op"
    value["decision"] = decision
    value["aspect"] = aspect
    if decision == "candidate":
        candidates = value.get("candidates") or []
        value["candidates"] = candidates[:3]
    return {"ok": True, "status": "completed",
            "semantic_output": value, "token_usage": usage,
            "summary": f"aspect={aspect} synthesize: decision={decision}, "
                       f"candidates={len(value.get('candidates') or [])}"}


def aspect_emit(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Write aspect-level evolution events to the audit ledger.

    Pure bookkeeping: log aspect/no_op | aspect/candidate_emitted |
    aspect/throttled so operators can see what self-reflection decided
    without grepping flow_outputs. Skips writing when workspace is
    missing.
    """
    aspect = _aspect(params)
    synth = _output(params, "synthesize").get("semantic_output") or {}
    decision = str(synth.get("decision") or "no_op")
    if append_evolution_event is None or not _workspace(ctx):
        return {"ok": True, "status": "completed",
                "summary": f"aspect={aspect} emit skipped (no ledger)"}
    try:
        append_evolution_event(
            _workspace(ctx),
            "aspect/observe_completed",
            subject_id=f"aspect/{aspect}/{params.get('round_id', '')}",
            project_id=str(params.get("project_id") or ""),
            payload={"aspect": aspect,
                     "facts_count": len((_output(params, "observe").get("semantic_output") or {}).get("facts") or []),
                     "patterns_count": len((_output(params, "observe").get("semantic_output") or {}).get("recurring_patterns") or [])},
            idempotency_key=f"aspect-observe:{aspect}:{params.get('round_id', '')}:{time.time():.0f}",
        )
        append_evolution_event(
            _workspace(ctx),
            "aspect/counter_read_completed",
            subject_id=f"aspect/{aspect}/{params.get('round_id', '')}",
            project_id=str(params.get("project_id") or ""),
            payload={"aspect": aspect,
                     "missing": len((_output(params, "counter_read").get("semantic_output") or {}).get("missing") or []),
                     "fabrication_risks": len((_output(params, "counter_read").get("semantic_output") or {}).get("fabrication_risks") or [])},
            idempotency_key=f"aspect-counter:{aspect}:{params.get('round_id', '')}:{time.time():.0f}",
        )
        append_evolution_event(
            _workspace(ctx),
            "aspect/synthesize_decided",
            subject_id=f"aspect/{aspect}/{params.get('round_id', '')}",
            project_id=str(params.get("project_id") or ""),
            payload={"aspect": aspect, "decision": decision,
                     "candidates_count": len(synth.get("candidates") or [])},
            idempotency_key=f"aspect-synth:{aspect}:{params.get('round_id', '')}:{time.time():.0f}",
        )
        if decision == "no_op":
            append_evolution_event(
                _workspace(ctx),
                "aspect/no_op",
                subject_id=f"aspect/{aspect}/{params.get('round_id', '')}",
                project_id=str(params.get("project_id") or ""),
                payload={"aspect": aspect,
                         "reason": str(synth.get("reason") or "")[:1200]},
                idempotency_key=f"aspect-noop:{aspect}:{params.get('round_id', '')}:{time.time():.0f}",
            )
        else:
            append_evolution_event(
                _workspace(ctx),
                "aspect/candidate_emitted",
                subject_id=f"aspect/{aspect}/{params.get('round_id', '')}",
                project_id=str(params.get("project_id") or ""),
                payload={"aspect": aspect,
                         "candidates": synth.get("candidates") or []},
                idempotency_key=f"aspect-candidate:{aspect}:{params.get('round_id', '')}:{time.time():.0f}",
            )
    except (OSError, ValueError) as exc:
        return {"ok": False, "status": "failed", "error": str(exc),
                "summary": f"aspect={aspect} emit failed"}
    return {"ok": True, "status": "completed",
            "summary": f"aspect={aspect} emit: decision={decision}"}


try:
    from partner.governance.evolution_events import append_evolution_event
except Exception:  # noqa: BLE001
    append_evolution_event = None


DEFINITIONS = [
    EventDefinition("evolution.aspect_observe", "evolution",
                    "忠实验证 partner 各方面本轮事实（message、iteration、pdf_report、intent、event_flow）",
                    aspect_observe, execution_method="llm"),
    EventDefinition("evolution.aspect_counter_read", "evolution",
                    "对 observe 输出的事实找缺失、反例、reward hacking 风险",
                    aspect_counter_read, execution_method="llm"),
    EventDefinition("evolution.aspect_synthesize", "evolution",
                    "综合事实+漏洞，决定 no_op 或 candidate；输出最多 3 个修改候选",
                    aspect_synthesize, execution_method="llm"),
    EventDefinition("evolution.aspect_emit", "evolution",
                    "把 aspect 事件写入 evolution ledger；纯簿记，不决策",
                    aspect_emit),
]
