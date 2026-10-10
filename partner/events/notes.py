"""Mind Notes Events: recall (inject), judge (write/discriminate), promote (upgrade/close).

These are the three LLM-driven doors of the unified note store
(``partner/memory/notes.py``).  Every durable, LLM-usable memory enters,
is used, or leaves through one of these events, so the whole note system has
exactly three prompt contracts instead of scattered one-off memory writes.
"""
from __future__ import annotations

from typing import Any
import json

from partner.event_fabric.catalog import EventDefinition
from partner.memory.notes import NOTE_TYPES, NoteStore, notes_injection
from ._llm import call_model, json_object


class SimpleNamespace:
    """Minimal namespace for rendering injection text in fallbacks/tests."""
    def __init__(self, run_context: dict[str, Any] | None = None):
        self.run_context = run_context or {}


def _workspace(ctx: Any) -> str:
    return str(getattr(ctx, "workspace", "") or getattr(ctx, "workspace_root", ""))


def _inject_into(ctx: Any, injection: str) -> None:
    try:
        ctx.note_injection = injection
    except Exception:
        pass


# ---------------------------------------------------------------------------
# notes.recall — read + inject
# ---------------------------------------------------------------------------

def notes_recall(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Recall relevant notes + open pending and inject them into the run context.

    Params:
      query: str        — semantic recall query (task description / goal)
      types: list[str]  — restrict to these note types (default: all)
      statuses: list[str] — restrict statuses (default: non-dismissed)
      limit: int        — max notes returned
      inject: bool      — whether to write the rendered injection into ctx
    """
    store = NoteStore(_workspace(ctx))
    query = str(params.get("query") or "")
    types = params.get("types") or None
    statuses = params.get("statuses") or None
    limit = max(1, min(12, int(params.get("limit") or 6)))
    project_id = str(params.get("project_id") or getattr(ctx, "project_id", "") or "")
    notes = store.recall(query=query, types=types, statuses=statuses,
                         limit=limit, project_id=project_id)
    pending = store.pending_open(limit=6)
    injection = ""
    if params.get("inject", True):
        fake = SimpleNamespace(run_context={"note_injection": {
            "notes": notes, "pending": pending}})
        injection = notes_injection(fake)
        _inject_into(ctx, injection)
    semantic = {
        "notes": notes,
        "pending": pending,
        "injection": injection,
        "note_stats": store.stats(),
    }
    return {"ok": True, "status": "completed", "semantic_output": semantic,
            "summary": f"召回 {len(notes)} 条相关笔记、{len(pending)} 条待定项",
            "evidence_refs": [n.get("id", "") for n in notes if n.get("id")]}


# ---------------------------------------------------------------------------
# notes.judge — read external/round content, discriminate, write
# ---------------------------------------------------------------------------

def _aggregate_items(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Aggregate distilled points from upstream flow outputs (read/synthesize).

    read.borrowable_cards and synthesize.findings become judge candidates.
    """
    items = list(params.get("items") or [])
    if items:
        return items
    outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    for name in ("read", "source_read"):
        row = outputs.get(name)
        if not isinstance(row, dict):
            continue
        so = row.get("semantic_output") if isinstance(row.get("semantic_output"), dict) else {}
        for card in so.get("borrowable_cards") or []:
            if not isinstance(card, dict):
                continue
            core = str(card.get("core_idea") or "").strip()
            method = str(card.get("key_method") or "").strip()
            content = core or method
            if not content:
                continue
            if core and method:
                content = f"{core}（方法：{method[:120]}）"
            items.append({
                "content": content,
                "source": str(card.get("source_url") or ""),
                "title": str(card.get("source_title") or ""),
                "transfer_points": card.get("transfer_points") or [],
                "limitations": str(card.get("limitations") or ""),
            })
    synth = outputs.get("synthesize")
    if isinstance(synth, dict):
        so = synth.get("semantic_output") if isinstance(synth.get("semantic_output"), dict) else {}
        for finding in so.get("findings") or []:
            text = str(finding.get("text") or finding.get("content") or finding) if isinstance(finding, dict) else str(finding)
            text = text.strip()
            if not text:
                continue
            items.append({"content": text,
                          "source": str(finding.get("source") or "") if isinstance(finding, dict) else ""})
    # learning_improvement_cycle: local_ideas → judge candidates
    for name in ("local_ideas", "local_read", "read_local"):
        row = outputs.get(name)
        if not isinstance(row, dict):
            continue
        so = row.get("semantic_output") if isinstance(row.get("semantic_output"), dict) else {}
        for idea in so.get("ideas") or []:
            if not isinstance(idea, dict):
                continue
            title = str(idea.get("title") or "").strip()
            gap = str(idea.get("partner_gap") or "").strip()
            hypothesis = str(idea.get("hypothesis") or "").strip()
            content = title or gap or hypothesis
            if not content:
                continue
            parts = [p for p in (title, gap, hypothesis) if p]
            items.append({
                "content": "；".join(parts)[:300],
                "source": str((idea.get("source_basis") or [""])[0]) if idea.get("source_basis") else "",
                "experiment": str(idea.get("minimal_experiment") or ""),
                "expected_effect": str(idea.get("expected_effect") or ""),
            })
    return items


def notes_judge(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """LLM discrimination of distilled points: act now / record note / dismiss.

    Params:
      items: list[dict] — distilled points from external reading or a round,
                          each with content/source (and optionally core_idea,
                          method, claims, url).  Defaults to aggregating
                          read.borrowable_cards + synthesize.findings.
      context: str      — optional extra context (current problem list, goal)
      default_record: bool — record_note default for low-confidence items
    """
    items = _aggregate_items(params)
    if not items:
        return {"ok": True, "status": "completed",
                "semantic_output": {"action_now": [], "recorded": [], "dismissed": []},
                "summary": "没有可判别的提炼点"}
    context = str(params.get("context") or "")
    raw, usage = call_model(ctx, purpose="notes_judge", prompt=(
        "你是 partner 的笔记判别器。对每一条提炼点做三分类：\n"
        "  action_now = 已经想清楚、确定要落地的改进（直接进入落地链）\n"
        "  record_note = 有价值但还没想清楚/资料少/风险高，先记入笔记库（必须给出 gap 和 trigger_signal）\n"
        "  dismiss = 无价值、重复或已被证伪（给出理由）\n"
        "硬约束：逐条精读原文，禁止凭标题猜；区分 事实/观点/可借鉴方法；"
        "不确定一律 record_note，不硬改不硬激活；每条必须有可追溯 source；"
        "不把一次成功变永久能力，不把用户偏好当业务事实。\n"
        "只输出 JSON：{\"items\":[{\"index\":0,\"decision\":\"action_now|record_note|dismiss\","
        "\"note_type\":\"pending|insight|lesson|issue_note|user_insight|belief\","
        "\"content\":\"一句话核心内容\",\"confidence\":0.0,\"gap\":\"为什么现在不改/不激活\","
        "\"trigger_signal\":\"将来什么信号下升级\",\"evidence_refs\":[\"可追溯引用\"],\"reason\":\"理由\"}]}\n"
        "附加上下文（当前问题/目标，可选）：\n" + (context[:2000] if context else "（无）")
        + "\n待判别提炼点：\n"
        + json.dumps(items, ensure_ascii=False)[:40000]
    ))
    value = json_object(raw)
    rows = value.get("items") or []
    store = NoteStore(_workspace(ctx))
    action_now, recorded, dismissed = [], [], []
    for row in rows:
        try:
            idx = int(row.get("index") or 0)
        except (TypeError, ValueError):
            idx = 0
        item = items[idx] if 0 <= idx < len(items) else {}
        decision = str(row.get("decision") or "record_note")
        note_type = str(row.get("note_type") or "pending")
        content = str(row.get("content") or item.get("content") or "").strip()
        source = str(item.get("source") or item.get("url") or item.get("title") or "")
        evidence = [str(ref) for ref in (row.get("evidence_refs") or [])
                    if str(ref).strip()] or ([source] if source else [])
        if decision == "action_now":
            action_now.append({"content": content, "source": source,
                               "confidence": float(row.get("confidence") or 0.0),
                               "reason": str(row.get("reason") or "")})
        elif decision == "dismiss":
            dismissed.append({"content": content,
                              "reason": str(row.get("reason") or "")})
        else:
            try:
                note = store.append(note_type, {
                    "content": content,
                    "source": source,
                    "confidence": float(row.get("confidence") or 0.4),
                    "gap": str(row.get("gap") or ""),
                    "trigger_signal": str(row.get("trigger_signal") or ""),
                    "evidence_refs": evidence,
                    "project_id": str(params.get("project_id") or getattr(ctx, "project_id", "") or ""),
                })
                recorded.append(note)
            except Exception as exc:
                dismissed.append({"content": content, "reason": f"record failed: {str(exc)[:120]}"})
    semantic = {"action_now": action_now, "recorded": recorded, "dismissed": dismissed,
                "note_stats": store.stats()}
    return {"ok": True, "status": "completed", "semantic_output": semantic,
            "summary": f"判别 {len(rows)} 条：{len(action_now)} 落地、{len(recorded)} 记笔记、{len(dismissed)} 丢弃",
            "evidence_refs": [n.get("id", "") for n in recorded if n.get("id")],
            "token_usage": usage}


# ---------------------------------------------------------------------------
# notes.promote — upgrade / close based on new evidence
# ---------------------------------------------------------------------------

def notes_promote(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """LLM promotion scan over open notes (pending/insight/lesson/issue_note).

    Params:
      note_ids: list[str] — target ids (default: all open pending + candidate insights)
      evidence: str       — new evidence / this round's outcomes to judge against
    """
    store = NoteStore(_workspace(ctx))
    note_ids = params.get("note_ids") or None
    if note_ids:
        targets = [store.by_id(nid) for nid in note_ids]
        targets = [n for n in targets if n]
    else:
        targets = [n for n in store.list_all()
                   if n.get("type") in {"pending", "insight", "lesson", "issue_note"}
                   and n.get("status") in {"open", "candidate", "progressing"}]
    if not targets:
        return {"ok": True, "status": "completed",
                "semantic_output": {"promoted": [], "kept": [], "dismissed": []},
                "summary": "没有待升级的笔记"}
    evidence = str(params.get("evidence") or "")
    raw, usage = call_model(ctx, purpose="notes_promote", prompt=(
        "你是 partner 的笔记晋升官。判断下列 open 笔记是否可以被本轮新证据升级、保持或关闭。\n"
        "规则：\n"
        "  promote = 新证据充分且明确（复用 partner 的验证门槛：growth 需真实改善+匹配实验；"
        "habit 需 >=2 个不同 flow 的已验证成果；pending 需资料/验证齐备可落地；issue_note 已修复）\n"
        "  keep = 证据仍不足，保持 open（可以补充 gap/trigger_signal）\n"
        "  dismiss = 无关、重复、过期或被证伪（必须给理由）\n"
        "硬约束：没有充分新证据不得 promote；笔记不等于已验证事实；promote 必须引用 evidence_refs。\n"
        "本轮新证据：\n" + (evidence[:3000] if evidence else "（本轮无新增证据摘要）")
        + "\n待判断笔记：\n"
        + json.dumps(targets, ensure_ascii=False)[:40000]
        + "\n只输出 JSON：{\"items\":[{\"id\":\"\",\"decision\":\"promote|keep|dismiss\","
          "\"status\":\"\",\"reason\":\"\"}]}"
    ))
    value = json_object(raw)
    store = NoteStore(_workspace(ctx))
    promoted, kept, dismissed = [], [], []
    for row in value.get("items") or []:
        nid = str(row.get("id") or "")
        decision = str(row.get("decision") or "keep")
        reason = str(row.get("reason") or "")
        target = store.by_id(nid) if nid else None
        if target is None:
            continue
        note_type = target.get("type", "pending")
        spec = NOTE_TYPES.get(note_type, {})
        promote_to = spec.get("promote_to") or "resolved"
        if decision == "promote":
            updated = store.replace(nid, {"status": promote_to,
                                          "reason": reason or "promoted by evidence"})
            promoted.append(updated)
        elif decision == "dismiss":
            updated = store.replace(nid, {"status": "dismissed",
                                          "reason": reason or "dismissed"})
            dismissed.append(updated)
        else:
            kept.append(target)
    semantic = {"promoted": promoted, "kept": kept, "dismissed": dismissed,
                "note_stats": store.stats()}
    return {"ok": True, "status": "completed", "semantic_output": semantic,
            "summary": f"晋升扫描 {len(targets)} 条：{len(promoted)} 升级、{len(kept)} 保持、{len(dismissed)} 关闭",
            "evidence_refs": [n.get("id", "") for n in promoted if n.get("id")],
            "token_usage": usage}


DEFINITIONS = [
    EventDefinition("notes.recall", "notes", "召回相关笔记与待定项并注入运行上下文", notes_recall),
    EventDefinition("notes.judge", "notes", "LLM判别提炼点：落地/记笔记/丢弃，写入统一笔记库", notes_judge, execution_method="llm"),
    EventDefinition("notes.promote", "notes", "LLM晋升扫描：升级/保持/关闭 open 笔记", notes_promote, execution_method="llm"),
]
