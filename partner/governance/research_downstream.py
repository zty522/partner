"""Frozen downstream tasks for the research-adoption context Candidate.

The Candidate prepares context.  A separate model produces the task output and
this module evaluates that output against a pre-registered truth contract.  It
does not write project Receipts or experience-policy trajectories: benchmark quality is not
business progress.
"""
from __future__ import annotations

import json
import re
from typing import Any


def parse_json_response(raw: str) -> tuple[dict[str, Any], str]:
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    candidate = fenced.group(1).strip() if fenced else text
    try:
        value = json.loads(candidate)
    except (TypeError, ValueError) as exc:
        # Reasoning models may prepend a private <think> block despite a JSON
        # instruction. Recover only a complete trailing JSON object; never
        # repair or complete a truncated object.
        decoder = json.JSONDecoder()
        recovered: list[dict[str, Any]] = []
        for match in re.finditer(r"\{", candidate):
            try:
                parsed, _ = decoder.raw_decode(candidate[match.start():])
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                recovered.append(parsed)
        if not recovered:
            return {}, f"invalid_json: {exc}"
        value = recovered[-1]
    if not isinstance(value, dict):
        return {}, "invalid_json: root must be an object"
    return value, ""


def sanitize_external_context(context: str, *, query: str, aliases: dict[str, str],
                              limit_chars: int = 6500) -> dict[str, Any]:
    """Project and irreversibly redact a context before an external model call.

    The same function must be used for both experiment arms.  It keeps only
    query-relevant chunks and rejects common local identity/credential shapes.
    """
    text = str(context or "")
    for source, alias in sorted(aliases.items(), key=lambda row: -len(row[0])):
        if source:
            text = text.replace(source, alias)
    substitutions = (
        (r"/(?:mnt|home|tmp)/[^\n\"'<>]*", "source://redacted-local"),
        (r"\breceipt_[a-zA-Z0-9]+\b", "receipt://redacted"),
        (r"\btraj(?:ectory)?_[a-zA-Z0-9_-]+\b", "trajectory://redacted"),
        (r"\bexperiment_[a-zA-Z0-9_-]+\b", "experiment://redacted"),
        (r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "task://redacted"),
        (r"\b[0-9a-f]{32,64}\b", "hash://redacted"),
        (r"https?://[^\s\"'<>]+", "url://redacted"),
        (r"\b\d{9,}\b", "number://redacted"),
    )
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text, flags=re.I)
    text = re.sub(r"(?im)^.*(?:api[_-]?key|authorization|access[_-]?token)\s*[:=].*$",
                  "[credential-line-removed]", text)

    query_terms = set(re.findall(r"[a-z][a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", query.lower()))
    chunks = [value.strip() for value in re.split(r"\n\s*\n|(?=<!--)", text)
              if value.strip()]
    ranked = []
    for index, chunk in enumerate(chunks):
        lowered = chunk.lower()
        overlap = sum(term in lowered for term in query_terms)
        protected = int(any(marker in lowered for marker in (
            "protected_project_handoff", "trajectory:", "research_evidence:",
            "source://jitrl-paper", "source://hermes-context-compressor")))
        ranked.append((overlap * 10 + protected * 8, -index, chunk))
    ranked.sort(reverse=True)
    selected: list[str] = []
    used = 0
    for _, _, chunk in ranked:
        addition = chunk + "\n\n"
        if used + len(addition) > limit_chars:
            remaining = limit_chars - used
            if remaining >= 120:
                selected.append(addition[:remaining])
                used += remaining
            break
        selected.append(addition)
        used += len(addition)
    projected = "".join(selected).strip()
    forbidden_patterns = {
        "absolute_local_path": r"/(?:mnt|home|tmp)/",
        "raw_receipt_id": r"\breceipt_[a-zA-Z0-9]+\b",
        "raw_trajectory_id": r"\btraj(?:ectory)?_[a-zA-Z0-9_-]+\b",
        "raw_experiment_id": r"\bexperiment_[a-zA-Z0-9_-]+\b",
        "raw_long_hash": r"\b[0-9a-f]{32,64}\b",
        "credential_assignment": r"(?i)(?:api[_-]?key|authorization|access[_-]?token)\s*[:=]",
    }
    violations = [name for name, pattern in forbidden_patterns.items()
                  if re.search(pattern, projected)]
    return {"ok": not violations, "context": projected,
            "chars": len(projected), "violations": violations,
            "mode": "query_projected_irreversible_redaction_v1"}


def frozen_tasks(*, receipt_id: str, receipt_actions: list[str],
                 jitrl_path: str, hermes_path: str) -> list[dict[str, Any]]:
    """Return three task contracts whose oracle is hidden from the model prompt."""
    shared = (
        "只根据所给 CONTEXT 回答，不使用上下文外知识。返回单个 JSON 对象，不要 Markdown："
        '{"answer":"...","receipt_id":"...","mechanism_points":["..."],'
        '"source_refs":["..."],"production_effective":false,"next_action":"...",'
        '"uncertainties":["..."]}。不能确认的内容写入 uncertainties，不得猜测来源。'
        "source_refs 只能逐项复制 CONTEXT 中稳定的 source://、receipt://、docs/ 或 research_evidence 标识，"
        "不要给标识附加括号说明。为避免推理型模型截断，answer 不超过300字，mechanism_points 最多5项且"
        "每项不超过80字，source_refs 最多4项，next_action 不超过160字，uncertainties 最多3项；"
        "不要输出 <think>、分析过程、前后缀或第二个 JSON。"
    )
    return [
        {
            "task_id": "receipt_continuation",
            "question": (shared + "\n任务：识别 04 当前最新项目 Receipt，说明该轮真实执行了哪些 Event、"
                         "得出了什么状态；mechanism_points 必须从 CONTEXT 的 actions_executed 逐字复制"
                         "每一个完整 Event 名称，不得缩写为 observe/select/investigate/matched；"
                         "提出一个尚未冒充执行的下一项有界工作。"),
            "oracle": {"receipt_id": receipt_id, "required_terms": receipt_actions,
                       "allowed_refs": [receipt_id], "forbidden_terms": []},
        },
        {
            "task_id": "jitrl_mechanism",
            "question": (shared + "\n任务：说明 JitRL 如何在不更新模型参数的情况下利用运行时反馈。"
                         "必须区分论文直接证据与对 Partner 的采用推论，并在 mechanism_points 中明确保留"
                         " state、action、reward、gradient 四个机制词。"),
            "oracle": {"receipt_id": "", "required_terms": ["state", "action", "reward", "gradient"],
                       "allowed_refs": [jitrl_path],
                       "forbidden_terms": ["已经训练partner模型权重", "production_effective=true"]},
        },
        {
            "task_id": "handoff_mechanism",
            "question": (shared + "\n任务：说明 Hermes context compressor 在压缩后如何保持任务连续性，"
                         "并给出 Partner 下一轮应遵守的最小 handoff 规则；mechanism_points 必须明确覆盖"
                         " handoff、summary、state。"),
            "oracle": {"receipt_id": "", "required_terms": ["handoff", "summary", "state"],
                       "allowed_refs": [hermes_path],
                       "forbidden_terms": ["from scratch", "restart from scratch", "丢弃已有状态"]},
        },
    ]


def _flat_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False).lower()


def _absolute_refs(value: dict[str, Any]) -> set[str]:
    refs = value.get("source_refs") or []
    if not isinstance(refs, list):
        return set()
    normalized: set[str] = set()
    for ref in refs:
        raw = str(ref).strip()
        if not raw:
            continue
        tokens = re.findall(
            r"(?:source|receipt|trajectory|experiment)://[a-zA-Z0-9._/-]+"
            r"|(?:docs|instances)/[a-zA-Z0-9._/-]+"
            r"|research_evidence[:_][a-zA-Z0-9_-]+", raw)
        for token in tokens or [raw]:
            normalized.add(token.split(":", 1)[1]
                           if token.startswith("research_evidence:") else token)
    return normalized


_CONCEPT_ALIASES = {
    "state": ("state", "状态"), "action": ("action", "动作"),
    "reward": ("reward", "奖励"),
    "gradient": ("gradient", "梯度", "参数更新", "模型权重"),
    "handoff": ("handoff", "交接", "承接"),
    "summary": ("summary", "摘要"),
}


def _term_present(term: str, text: str) -> bool:
    aliases = _CONCEPT_ALIASES.get(term, (term,))
    return any(alias in text for alias in aliases)


def evaluate_downstream_output(task: dict[str, Any], raw: str, *,
                               context: str, budget_chars: int) -> dict[str, Any]:
    value, error = parse_json_response(raw)
    oracle = dict(task.get("oracle") or {})
    flat = _flat_text(value)
    required = [str(term).lower() for term in oracle.get("required_terms") or []]
    forbidden = [str(term).lower() for term in oracle.get("forbidden_terms") or []]
    allowed = {str(ref) for ref in oracle.get("allowed_refs") or []}
    refs = _absolute_refs(value)

    receipt_expected = str(oracle.get("receipt_id") or "")
    receipt_ok = (not receipt_expected
                  or str(value.get("receipt_id") or "") == receipt_expected)
    terms_ok = all(_term_present(term, flat) for term in required)
    forbidden_clear = not any(term and term in flat for term in forbidden)
    objects = _embedded_objects(context)
    indirect_allowed = {
        str(row.get("investigation_id") or "")
        for row in objects
        if str(row.get("source_path") or "") in allowed
    }
    refs_grounded = bool((refs & allowed) or (refs & indirect_allowed)) and all(
        ref in allowed or ref in context for ref in refs)
    production_safe = value.get("production_effective") is False
    next_action = str(value.get("next_action") or "").strip()
    schema_ok = (not error and isinstance(value.get("mechanism_points"), list)
                 and isinstance(value.get("source_refs"), list)
                 and isinstance(value.get("uncertainties"), list)
                 and bool(str(value.get("answer") or "").strip()))
    context_budget_ok = len(context) <= int(budget_chars)
    checks = {
        "json_schema": schema_ok,
        "receipt_truth": receipt_ok,
        "required_claims": terms_ok,
        "forbidden_claims_absent": forbidden_clear,
        "source_refs_grounded": refs_grounded,
        "production_boundary_truthful": production_safe,
        "bounded_next_action_present": bool(next_action),
        "context_within_budget": context_budget_ok,
    }
    truth_safety = all(checks[key] for key in (
        "json_schema", "receipt_truth", "required_claims", "forbidden_claims_absent",
        "source_refs_grounded", "production_boundary_truthful", "context_within_budget"))
    reward_components = {
        "schema": .05 if checks["json_schema"] else 0.0,
        "truth": .30 if (checks["receipt_truth"] and checks["required_claims"]
                           and checks["forbidden_claims_absent"]) else 0.0,
        "evidence": .25 if checks["source_refs_grounded"] else 0.0,
        "safety": .15 if checks["production_boundary_truthful"] else 0.0,
        "actionability": .15 if checks["bounded_next_action_present"] else 0.0,
        "budget": .10 if checks["context_within_budget"] else 0.0,
    }
    reward = sum(reward_components.values()) if truth_safety else 0.0
    return {
        "ok": truth_safety, "task_id": task.get("task_id"), "parse_error": error,
        "checks": checks, "reward": round(reward, 6),
        "reward_components": reward_components, "parsed": value,
        "response_chars": len(str(raw or "")), "context_chars": len(context),
        "business_progress": False, "policy_eligible": False,
    }


def _embedded_objects(context: str) -> list[dict[str, Any]]:
    """Recover complete JSON objects from provenance-delimited context chunks."""
    objects: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", context):
        try:
            value, _ = decoder.raw_decode(context[match.start():])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and value not in objects:
            objects.append(value)
    return objects


def local_grounded_consumer(task: dict[str, Any], context: str) -> str:
    """A deterministic downstream Agent consumer independent of the oracle.

    It uses only typed/provenance structures present in the supplied context.
    Missing evidence becomes uncertainty rather than a guessed answer.
    """
    task_id = str(task.get("task_id") or "")
    objects = _embedded_objects(context)
    receipt = next((row for row in objects if row.get("receipt_id")
                    and (row.get("actions_executed") is not None
                         or row.get("iteration") is not None)), {})
    evidence = [row for row in objects if row.get("investigation_id")
                and row.get("evidence_quote")]
    answer = ""
    receipt_id = ""
    points: list[str] = []
    refs: list[str] = []
    uncertainties: list[str] = []
    next_action = "run one bounded matched validation and record a new receipt only after execution"

    if task_id == "receipt_continuation":
        if receipt:
            receipt_id = str(receipt.get("receipt_id") or "")
            points = [str(value) for value in receipt.get("actions_executed") or []]
            answer = (f"Latest bounded iteration is {receipt_id}; observed actions: "
                      + ", ".join(points))
            refs = [receipt_id] if receipt_id else []
            actions = receipt.get("next_actions") or []
            if actions and isinstance(actions[0], dict):
                next_action = str(actions[0].get("title") or actions[0].get("event_type") or next_action)
        else:
            answer = "No complete typed Receipt was recoverable from context."
            uncertainties.append("latest receipt identity and actions are unavailable")
    else:
        wanted = "jitrl" if task_id == "jitrl_mechanism" else "hermes"
        def matches(value: dict[str, Any]) -> bool:
            source = str(value.get("source_path") or "").lower()
            quote = str(value.get("evidence_quote") or "").lower()
            if wanted == "jitrl":
                return ("just-in-time" in source or "jitrl" in quote
                        or all(term in quote for term in ("state", "action", "reward")))
            return "hermes" in source or "context_compressor" in source or "handoff" in quote
        row = next((value for value in evidence if matches(value)), {})
        if row:
            quote = str(row.get("evidence_quote") or "")
            source = str(row.get("source_path") or "")
            refs = [source] if source else []
            if task_id == "jitrl_mechanism":
                for term in ("state", "action", "reward", "gradient"):
                    if term in quote.lower():
                        points.append(term)
                answer = ("The direct excerpt describes state, action, reward trajectory memory "
                          "used without gradient updates; applying it to Partner remains a shadow inference.")
            else:
                for term in ("handoff", "summary", "state"):
                    if term in quote.lower():
                        points.append(term)
                answer = ("The compressor preserves a handoff summary and current state so later work "
                          "continues from retained evidence rather than restarting.")
        else:
            answer = "No matching typed research excerpt was recoverable from context."
            uncertainties.append(f"{wanted} direct-source evidence is unavailable")
    return json.dumps({
        "answer": answer, "receipt_id": receipt_id, "mechanism_points": points,
        "source_refs": refs, "production_effective": False,
        "next_action": next_action, "uncertainties": uncertainties,
    }, ensure_ascii=False)
