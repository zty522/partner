"""Project cognition: evidence-bound memory, belief revision, and action influence.

This module deliberately sits between project truth (Receipt/trajectory) and
action selection.  It does not execute tools and it cannot promote code.  Its
job is to make a long-running project able to say which prior fact changed the
next decision, then preserve that claim in an append-only hash chain.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from .models import now_iso
from .storage import latest_receipt, load_project_state, workspace_root


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _ledger(root: Path, project_id: str) -> Path:
    path = root / "share/projects" / project_id / "cognition/events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    output: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict):
            output.append(row)
    return output


def _append(path: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    with lock.open("a+", encoding="utf-8") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        previous = ""
        existing = _rows(path)
        if existing:
            previous = str(existing[-1].get("record_hash") or "")
        value = dict(row)
        value["previous_hash"] = previous
        value["record_hash"] = hashlib.sha256(
            (previous + _canonical(value)).encode("utf-8")
        ).hexdigest()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
    return value


def verify_project_cognition(workspace: str | Path, project_id: str) -> dict[str, Any]:
    path = _ledger(workspace_root(str(workspace)), project_id)
    previous = ""
    count = 0
    for line_number, source in enumerate(_rows(path), 1):
        row = dict(source)
        stored = str(row.pop("record_hash", ""))
        linked = str(row.get("previous_hash") or "")
        expected = hashlib.sha256((previous + _canonical(row)).encode("utf-8")).hexdigest()
        if linked != previous or stored != expected:
            return {"ok": False, "records": count, "line": line_number,
                    "error": "hash_chain_mismatch", "path": str(path)}
        previous = stored
        count += 1
    return {"ok": True, "records": count, "head": previous, "path": str(path)}


def _compact(value: Any, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def model_safe_cognition_context(value: Any) -> Any:
    """Irreversibly remove local paths before an external model call."""
    if isinstance(value, dict):
        return {str(key): model_safe_cognition_context(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [model_safe_cognition_context(item) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    paths = re.findall(r"(?:[A-Za-z]:[\\/]|/)[^\s；;，,]+", text)
    for path in paths:
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
        text = text.replace(path, f"[local-evidence:{digest}]")
    return text


def _trajectory_rows(root: Path, project_id: str, limit: int = 6) -> list[dict[str, Any]]:
    from .portfolio_improvement import effective_trajectories
    selected: list[dict[str, Any]] = []
    for row in effective_trajectories(root)[-1200:]:
        if str(row.get("project_id") or "") == project_id:
            selected.append(row)
    return selected[-max(1, limit):]


def load_project_brief_guardrails(workspace: str | Path, project_id: str) -> str:
    """Extract the project brief's falsified routes / next minimum action /
    forbidden directions so the planner can treat them as hard constraints
    instead of re-proposing already-falsified actions.

    Returns a compact Chinese block ('' when the brief is absent or has no
    guardrail sections), ready to inject into the planner prompt.
    """
    root = workspace_root(str(workspace))
    brief = root / "share/projects" / project_id / "project_brief.md"
    if not brief.is_file():
        return ""
    try:
        text = brief.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    guardrail_titles = ("已证明不行的路线", "禁止跑偏方向", "下一步最小动作", "当前瓶颈")
    sections: list[str] = []
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            title = line[3:].strip()
            if any(k in title for k in guardrail_titles):
                current = title
                sections.append(f"【{title}】")
            else:
                current = None
        elif current and line:
            sections.append(line)
    if not sections:
        return ""
    return "【项目简报硬约束（必须遵守，不要重提已证伪的路线）】\n" + "\n".join(sections)


def build_project_cognition_context(
    workspace: str | Path,
    project_id: str,
    *,
    options: list[tuple[str, dict[str, Any]]] | None = None,
    project_steps: int = 0,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Build a bounded context whose every memory has an auditable source id."""
    root = workspace_root(str(workspace))
    state = load_project_state(str(root), project_id)
    receipt = latest_receipt(str(root), project_id)
    trajectories = _trajectory_rows(root, project_id)
    cognition = _rows(_ledger(root, project_id))[-40:]
    memories: list[dict[str, Any]] = []
    if receipt:
        memories.append({
            "memory_id": f"receipt:{receipt.receipt_id}",
            "kind": "project_handoff",
            "content": _compact("；".join(receipt.findings) or receipt.goal, 900),
            "evidence_refs": list(receipt.artifacts[:4]),
            "unresolved_questions": list(receipt.unresolved_questions[:5]),
        })
    for row in reversed(trajectories):
        trajectory_id = str(row.get("trajectory_id") or "")
        if not trajectory_id:
            continue
        outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
        action = row.get("action") if isinstance(row.get("action"), dict) else {}
        memories.append({
            "memory_id": f"trajectory:{trajectory_id}",
            "kind": "verified_experience",
            "content": _compact("；".join(str(v) for v in outcome.get("evidence") or []), 700),
            "action": str(action.get("selection_arm_id") or action.get("native_action_id") or ""),
            "reward": row.get("reward"),
            "business_progress": bool(outcome.get("business_progress")),
            "duplicate_outcome": bool(outcome.get("duplicate_outcome")),
            "evidence_refs": list(outcome.get("artifacts") or [])[:3],
        })
        if len(memories) >= 7:
            break
    latest_reflection = next(
        (row for row in reversed(cognition) if row.get("event_type") == "outcome_reflected"), {}
    )
    if latest_reflection:
        memories.insert(0, {
            "memory_id": f"cognition:{latest_reflection.get('event_id')}",
            "kind": "belief_revision",
            "content": _compact(latest_reflection.get("belief_revision"), 900),
            "evidence_refs": list(latest_reflection.get("evidence_refs") or []),
            "next_project_question": _compact(latest_reflection.get("next_project_question"), 300),
        })
    # User habits are constraints, not evidence that a scientific action will
    # work.  Keep only stable presentation/working preferences and name their
    # source so the LLM cannot launder them into business facts.
    habit_constraints: list[dict[str, Any]] = []
    try:
        from partner.meta.learning import get_growth_timeline, load_habits
        habits = load_habits()
        for key in ("preferred_language", "prefer_pdf"):
            if key in habits:
                habit_constraints.append({"memory_id": f"habit:{key}",
                                           "kind": "user_preference",
                                           "value": habits[key]})
        for growth in get_growth_timeline(limit=2):
            growth_id = str(growth.get("id") or hashlib.sha256(
                str(growth).encode()).hexdigest()[:12])
            memories.append({
                "memory_id": f"growth:{growth_id}",
                "kind": "capability_claim_requires_revalidation",
                "content": _compact(growth.get("milestone"), 400),
                "evidence_refs": [],
                "usable_for_business_causality": False,
            })
    except Exception:
        pass
    candidates = []
    for event_type, params in options or []:
        candidates.append({
            "arm_id": str(params.get("experience_candidate_id")
                          or params.get("strategy_id") or event_type),
            "event_type": event_type,
            "parameters": dict(params),
        })
    recent_actions = [
        str((row.get("action") or {}).get("selection_arm_id")
            or (row.get("action") or {}).get("native_action_id") or "")
        for row in trajectories
    ]
    context = {
        "schema_version": 1,
        "project_id": project_id,
        "goal": str(getattr(state, "goal", "") or getattr(receipt, "goal", "") or ""),
        "project_status": str(getattr(state, "status", "") or "unknown"),
        "project_steps": int(project_steps),
        "latest_receipt_id": str(getattr(receipt, "receipt_id", "") or ""),
        "current_questions": list(getattr(receipt, "unresolved_questions", []) or [])[:6],
        "memories": memories[:8],
        "habit_constraints": habit_constraints,
        "recent_actions": recent_actions,
        "candidate_capabilities": candidates,
        "latest_belief_revision": latest_reflection.get("belief_revision") or {},
        "latest_next_project_question": str(latest_reflection.get("next_project_question") or ""),
    }
    serialized = json.dumps(context, ensure_ascii=False)
    while len(serialized) > max_chars and len(context["memories"]) > 2:
        context["memories"].pop()
        serialized = json.dumps(context, ensure_ascii=False)
    context["context_chars"] = len(serialized)
    context["memory_ids"] = [str(row.get("memory_id") or "") for row in context["memories"]]
    return context


def latest_action_preference(
    workspace: str | Path, project_id: str, eligible_arm_ids: list[str],
) -> dict[str, Any]:
    """Return one unconsumed, evidence-bound recommendation from the last outcome."""
    rows = _rows(_ledger(workspace_root(str(workspace)), project_id))
    used_sources = {str(row.get("cognitive_preference_source") or "")
                    for row in rows if row.get("event_type") == "action_decided"}
    for row in reversed(rows):
        if row.get("event_type") != "outcome_reflected":
            continue
        event_id = str(row.get("event_id") or "")
        arm = str(row.get("recommended_next_arm_id") or "")
        if event_id and event_id not in used_sources and arm in set(eligible_arm_ids):
            return {"arm_id": arm, "source_event_id": event_id,
                    "reason": str(row.get("recommendation_reason") or "")}
        return {}
    return {}


def record_action_decision(
    workspace: str | Path,
    *,
    project_id: str,
    selection_id: str,
    context: Mapping[str, Any],
    review: Mapping[str, Any],
    chosen_arm_id: str,
    event_type: str,
    parameters: Mapping[str, Any],
    cognitive_preference_source: str = "",
) -> dict[str, Any]:
    allowed_memory_ids = set(str(value) for value in context.get("memory_ids") or [])
    requested = [str(value) for value in review.get("memory_refs_used") or []]
    used = [value for value in requested if value in allowed_memory_ids]
    event_id = f"cog_decision_{hashlib.sha256(selection_id.encode()).hexdigest()[:16]}"
    existing = next((row for row in _rows(_ledger(workspace_root(str(workspace)), project_id))
                     if row.get("event_id") == event_id), None)
    if existing:
        return existing
    proposal = review.get("proposed_event_candidate")
    candidate_assessment: dict[str, Any] = {}
    if isinstance(proposal, dict) and proposal:
        capabilities = list(context.get("candidate_capabilities") or [])
        proposal_event = str(proposal.get("event_type") or "")
        proposal_arm = str(proposal.get("arm_id") or "")
        def proposal_matches(row: Mapping[str, Any]) -> bool:
            arm_matches = proposal_arm == str(row.get("arm_id") or "")
            event_matches = proposal_event == str(row.get("event_type") or "")
            if proposal_arm and proposal_event:
                return arm_matches and event_matches
            if proposal_arm:
                return arm_matches
            if proposal_event:
                return event_matches
            return False
        matching = [row for row in capabilities if proposal_matches(row)]
        if len(matching) == 1:
            declared = dict(matching[0].get("parameters") or {})
            requested_params = dict(proposal.get("parameters") or {})
            candidate_assessment = {
                "status": ("validated_existing_capability"
                           if all(key in declared and declared[key] == value
                                  for key, value in requested_params.items())
                           else "rejected_parameter_contract_mismatch"),
                "arm_id": matching[0].get("arm_id"),
                "event_type": matching[0].get("event_type"),
                "requested_parameters": requested_params,
                "executable_this_turn": False,
                "reason": "proposal is advisory; a later Selection must consume it",
            }
        else:
            candidate_assessment = {
                "status": "requires_isolated_candidate_compilation",
                "event_type": proposal_event, "arm_id": proposal_arm,
                "executable_this_turn": False,
                "reason": "proposal does not resolve to one current allow-listed capability",
            }
    return _append(_ledger(workspace_root(str(workspace)), project_id), {
        "schema_version": 1, "event_id": event_id, "event_type": "action_decided",
        "project_id": project_id, "selection_id": selection_id,
        "chosen_arm_id": chosen_arm_id, "chosen_event_type": event_type,
        "parameters": dict(parameters), "memory_refs_used": used,
        "memory_influence": _compact(review.get("memory_influence"), 600),
        "project_question": _compact(review.get("project_question"), 400),
        "decision_summary": _compact(review.get("decision_summary") or review.get("reason"), 600),
        "user_narrative": _compact(review.get("user_narrative"), 700),
        "proposed_event_candidate": dict(proposal) if isinstance(proposal, dict) else {},
        "candidate_assessment": candidate_assessment,
        "cognitive_preference_source": cognitive_preference_source,
        "created_at": now_iso(),
    })


def _parse_json(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S | re.I)
    decoder = json.JSONDecoder()
    values: list[tuple[int, dict[str, Any]]] = []
    for match in re.finditer(r"\{", cleaned):
        try:
            value, end = decoder.raw_decode(cleaned[match.start():])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            values.append((end, value))
    return max(values, key=lambda item: item[0])[1] if values else {}


def reflect_on_project_outcome(
    workspace: str | Path,
    *,
    project_id: str,
    instance_id: str,
    task_id: str,
    trajectory: Mapping[str, Any],
    receipt: Mapping[str, Any],
    use_llm: bool = True,
) -> dict[str, Any]:
    """Use a fourth LLM role to revise beliefs after verified terminal evidence."""
    root = workspace_root(str(workspace))
    action = trajectory.get("action") if isinstance(trajectory.get("action"), dict) else {}
    outcome = trajectory.get("outcome") if isinstance(trajectory.get("outcome"), dict) else {}
    selection_id = str(action.get("action_selection_id") or "")
    event_id = "cog_outcome_" + hashlib.sha256(
        f"{project_id}|{task_id}|{selection_id}".encode()
    ).hexdigest()[:16]
    existing = next((row for row in _rows(_ledger(root, project_id))
                     if row.get("event_id") == event_id), None)
    if existing:
        return existing
    selection: dict[str, Any] = {}
    if selection_id:
        path = root / "share/mind/governance/experience_guided_policy/native_action_selections" / f"{selection_id}.json"
        try:
            selection = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            selection = {}
    evidence_ids = [
        f"selection:{selection_id}" if selection_id else "",
        f"receipt:{receipt.get('receipt_id', '')}" if receipt.get("receipt_id") else "",
        f"trajectory:{trajectory.get('trajectory_id', '')}" if trajectory.get("trajectory_id") else "",
    ]
    evidence_ids = [value for value in evidence_ids if value]
    actual = {
        "selected_arm_id": str(action.get("selection_arm_id") or action.get("native_action_id") or ""),
        "reward": trajectory.get("reward"),
        "business_progress": bool(outcome.get("business_progress")),
        "duplicate_outcome": bool(outcome.get("duplicate_outcome")),
        "failure_mechanism": str(outcome.get("failure_mechanism") or ""),
        "findings": [_compact(value, 360) for value in receipt.get("findings") or []][:6],
        "unresolved_questions": [_compact(value, 260) for value in receipt.get("unresolved_questions") or []][:5],
    }
    reflection: dict[str, Any] = {}
    outcome_audit: dict[str, Any] = {}
    llm_error = ""
    model_calls = 0
    if use_llm and selection:
        safe_selection = {
            "arm_id": selection.get("arm_id"),
            "considered": selection.get("considered"),
            "decision_summary": (selection.get("llm_review") or {}).get("decision_summary")
                                or (selection.get("llm_review") or {}).get("reason"),
            "main_hypothesis": (selection.get("llm_review") or {}).get("main_hypothesis"),
            "memory_refs_used": (selection.get("llm_review") or {}).get("memory_refs_used"),
        }
        prompt = (
            "你是项目终态反思者。只依据给出的已验证终态修订认识，不得把文件生成或消息发送当业务提升。"
            "负实验是有效证据；框架错误、知识缺口和业务否证必须区分。推荐下一 arm 只能来自 considered。"
            "输出严格 JSON：belief_revision（对象，含 supported/rejected/revised/unknown）、"
            "memory_to_consolidate、next_project_question、recommended_next_arm_id、"
            "recommendation_reason、user_narrative。user_narrative 用三句话说明原问题、真实发现、接下来为何改变。\n"
            + json.dumps(model_safe_cognition_context(
                {"evidence_ids": evidence_ids, "decision": safe_selection,
                 "verified_outcome": actual}), ensure_ascii=False)
        )
        try:
            from partner.adapters.direct_api import chat
            audit_prompt = (
                "你是独立终态证据审计者，不提出下一步。逐项核对机器结果是否支持 business_progress、"
                "是否重复、Reward 方向是否合理，以及失败属于业务假设、外部知识、环境还是 Partner 机制。"
                "输出严格 JSON：verified_claims、unsupported_claims、failure_class、reward_assessment、"
                "remaining_unknowns。不得把文件生成、消息发送或模型自评当业务提升。\n"
                + json.dumps(model_safe_cognition_context(
                    {"evidence_ids": evidence_ids, "decision": safe_selection,
                     "verified_outcome": actual}), ensure_ascii=False)
            )
            audit_raw = chat(
                audit_prompt, purpose="project_outcome_audit", max_tokens=5000,
                temperature=0.1, timeout=75, workspace=str(root),
                instance_id=instance_id, project_id=project_id,
                event_type="project.outcome_reflected",
            )
            model_calls += 1
            outcome_audit = _parse_json(str(audit_raw or ""))
            audit_keys = {"verified_claims", "unsupported_claims", "failure_class",
                          "reward_assessment", "remaining_unknowns"}
            if not audit_keys.issubset(outcome_audit):
                audit_repair = chat(
                    "把下面审计整理为一个严格 JSON 对象，必须包含 verified_claims、unsupported_claims、"
                    "failure_class、reward_assessment、remaining_unknowns。不得增加事实，只输出 JSON。\n"
                    + str(audit_raw or "")[-24000:],
                    purpose="project_outcome_audit_json_repair", max_tokens=4000,
                    temperature=0.0, timeout=75, workspace=str(root),
                    instance_id=instance_id, project_id=project_id,
                    event_type="project.outcome_reflected",
                )
                model_calls += 1
                outcome_audit = _parse_json(str(audit_repair or ""))
            if not audit_keys.issubset(outcome_audit):
                outcome_audit = {"status": "inconclusive", "error": "outcome_audit_json_missing"}
            prompt += "\n独立证据审计=" + json.dumps(outcome_audit, ensure_ascii=False)
            raw = chat(prompt, purpose="project_outcome_reflection", max_tokens=7000,
                       temperature=0.2, timeout=75, workspace=str(root),
                       instance_id=instance_id, project_id=project_id,
                       event_type="project.outcome_reflected")
            model_calls += 1
            reflection = _parse_json(str(raw or ""))
            reflection_keys = {
                "belief_revision", "memory_to_consolidate", "next_project_question",
                "recommended_next_arm_id", "recommendation_reason", "user_narrative",
            }
            belief_keys = {"supported", "rejected", "revised", "unknown"}
            def reflection_complete(value: Mapping[str, Any]) -> bool:
                belief = value.get("belief_revision")
                return bool(
                    reflection_keys.issubset(value)
                    and isinstance(belief, dict)
                    and belief_keys.issubset(belief)
                    and str(value.get("memory_to_consolidate") or "").strip()
                    and str(value.get("next_project_question") or "").strip()
                    and str(value.get("user_narrative") or "").strip()
                )
            if not reflection_complete(reflection):
                reflection_repair = chat(
                    "把下面终态反思整理成一个严格 JSON 对象，必须包含 belief_revision（对象，含"
                    "supported/rejected/revised/unknown）、memory_to_consolidate、next_project_question、"
                    "recommended_next_arm_id、recommendation_reason、user_narrative。不得增加事实；"
                    "recommended_next_arm_id 只能沿用原文。只输出 JSON。\n"
                    + str(raw or "")[-30000:],
                    purpose="project_outcome_reflection_json_repair", max_tokens=5000,
                    temperature=0.0, timeout=75, workspace=str(root),
                    instance_id=instance_id, project_id=project_id,
                    event_type="project.outcome_reflected",
                )
                model_calls += 1
                reflection = _parse_json(str(reflection_repair or ""))
            if not reflection_complete(reflection):
                llm_error = "reflection_json_missing"
        except Exception as exc:
            llm_error = type(exc).__name__
    considered_arms = {
        str(row.get("strategy_id") or row.get("event_type") or "")
        for row in selection.get("considered") or [] if isinstance(row, dict)
    }
    recommended = str(reflection.get("recommended_next_arm_id") or "")
    if recommended not in considered_arms:
        recommended = ""
    belief_revision = reflection.get("belief_revision")
    if not isinstance(belief_revision, dict):
        belief_revision = {"status": "inconclusive" if llm_error else "observed",
                           "verified_outcome": actual}
    return _append(_ledger(root, project_id), {
        "schema_version": 1, "event_id": event_id, "event_type": "outcome_reflected",
        "project_id": project_id, "selection_id": selection_id, "task_id": task_id,
        "evidence_refs": evidence_ids, "verified_outcome": actual,
        "outcome_audit": outcome_audit,
        "belief_revision": belief_revision,
        "memory_to_consolidate": _compact(reflection.get("memory_to_consolidate"), 700),
        "next_project_question": _compact(reflection.get("next_project_question"), 500),
        "recommended_next_arm_id": recommended,
        "recommendation_reason": _compact(reflection.get("recommendation_reason"), 500),
        "user_narrative": _compact(reflection.get("user_narrative"), 800),
        "llm_participation": {"attempted": bool(use_llm and selection),
                              "completed": bool(reflection and not llm_error),
                              "calls": model_calls,
                              "error": llm_error},
        "created_at": now_iso(),
    })


def latest_project_narrative(workspace: str | Path, project_id: str) -> dict[str, Any]:
    for row in reversed(_rows(_ledger(workspace_root(str(workspace)), project_id))):
        if row.get("event_type") == "outcome_reflected":
            return {"user_narrative": str(row.get("user_narrative") or ""),
                    "next_project_question": str(row.get("next_project_question") or ""),
                    "belief_revision": row.get("belief_revision") or {},
                    "evidence_refs": list(row.get("evidence_refs") or [])}
    return {}


__all__ = [
    "build_project_cognition_context", "latest_action_preference",
    "latest_project_narrative", "model_safe_cognition_context", "record_action_decision",
    "reflect_on_project_outcome", "verify_project_cognition",
]
