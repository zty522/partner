"""Event-first, reward-grounded selection of the next project action."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .evolution_events import append_evolution_event
from .storage import workspace_root
from .project_reasoning import project_reasoning_contract


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()[:16]


def _arm_id(event_type: str, parameters: dict[str, Any]) -> str:
    return str(parameters.get("experience_candidate_id")
               or parameters.get("strategy_id") or event_type)


def _reward_history(root: Path, project_id: str) -> dict[str, list[float]]:
    history: dict[str, list[float]] = {}
    path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-2000:]
    except OSError:
        return history
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if str(row.get("project_id") or "") != project_id:
            continue
        action = row.get("action") if isinstance(row.get("action"), dict) else {}
        arm = str(action.get("native_action_id") or "")
        if not arm:
            continue
        try:
            reward = float(row.get("reward"))
        except (TypeError, ValueError):
            continue
        history.setdefault(arm, []).append(max(-1.0, min(1.0, reward)))
    return history


def _llm_deliberation_enabled(root: Path) -> bool:
    path = root / "config/partner_config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    policy = config.get("experience_guided_policy") or {}
    return str(policy.get("llm_deliberation") or "").lower() == "every_project_action"


def _parse_llm_json(raw: str) -> dict[str, Any]:
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
    return max(values, key=lambda row: row[0])[1] if values else {}


def _llm_deliberate(project_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Require one bounded LLM deliberation for every substantive action choice."""
    try:
        from partner.adapters.direct_api import chat
        prompt = (
            "你是 Partner 项目动作审议者。每一轮都必须认真分析，只能从给定候选中选择一个，不得创造新动作。"
            "区分现实证据、解释和未知；联想历史但不把相似当因果；"
            "保留主假设与反对假设，优先选择能产生反证并改变下一决策的真实动作。"
            "判断本轮属于业务推进、外部学习还是 Partner 自进化，三者不得冒充。"
            "机器 bandit 已按真实终态 Reward 排序；仅根据动作是否可能产生新的可验证业务证据打破近似平局。"
            "输出严格 JSON，字段为 arm_id、observed_facts、analogy_not_causality、constraints、"
            "main_hypothesis、opposing_hypothesis、counterexample、reason、next_belief_update、loop_kind。"
            "loop_kind 只能是 business_progress、external_active_learning、partner_self_evolution。项目="
            + project_id + "；候选="
            + "\n" + project_reasoning_contract()
            + "\n" + json.dumps(candidates, ensure_ascii=False)
        )
        raw = chat(prompt, purpose="project_action_deliberation", max_tokens=5200,
                   temperature=0.2, timeout=90)
        value = _parse_llm_json(str(raw or ""))
        if value:
            value["_llm_calls"] = 1
            return value
        reduced = chat(
            "把下面未完成的项目动作审议压缩成一个严格 JSON 对象，只能使用已有证据，不添加事实。"
            "字段必须为 arm_id、observed_facts、analogy_not_causality、constraints、main_hypothesis、"
            "opposing_hypothesis、counterexample、reason、next_belief_update、loop_kind。只输出 JSON。\n"
            + str(raw or "")[-12000:],
            purpose="project_action_deliberation_reduction", max_tokens=2600,
            temperature=0.0, timeout=90,
        )
        value = _parse_llm_json(str(reduced or ""))
        if value:
            value["_llm_calls"] = 2
            return value
        return {"error": "llm_json_terminal_missing", "_llm_calls": 2}
    except Exception as exc:  # LLM is advisory; the auditable bandit remains available.
        return {"error": type(exc).__name__}


def select_project_action(
    workspace: str | Path,
    *,
    instance_id: str,
    project_id: str,
    options: list[tuple[str, dict[str, Any]]],
    project_steps: int,
    learning_interruptions: int = 0,
) -> dict[str, Any]:
    """Choose from allow-listed Events using verified terminal rewards.

    Unseen arms are explored in domain order.  Once evidence exists, a UCB
    contextual-bandit score balances empirical reward and uncertainty.  An
    occasional LLM critic may break a close top-two tie, but cannot add an Event
    or override a clearly superior machine score.
    """
    if not options:
        raise ValueError("at least one project action is required")
    root = workspace_root(str(workspace))
    base_options = list(options)
    hypothesis_candidate: dict[str, Any] = {}
    try:
        from partner.v2.project_hypothesis_events import atomic_project_hypothesis_propose
        class _Context:
            workspace = str(root)
        event_result = atomic_project_hypothesis_propose(
            _Context(), {"project_id": project_id, "project_steps": int(project_steps)})
        hypothesis_candidate = dict(event_result.get("candidate") or {})
        extra = [(str(row.get("event_type") or ""), dict(row.get("parameters") or {}))
                 for row in event_result.get("event_options") or []
                 if row.get("event_type")]
        options = [*base_options, *extra]
    except Exception as exc:
        hypothesis_candidate = {"decision": "engine_failed", "error": type(exc).__name__}
    normal_index = max(0, int(project_steps)) % len(options)
    intervention_applied = bool(learning_interruptions and len(options) > 1)
    history = _reward_history(root, project_id)
    arm_ids = [_arm_id(event, params) for event, params in options]
    unseen = [index for index, arm in enumerate(arm_ids) if not history.get(arm)]
    scores: list[float] = []
    total = sum(len(values) for values in history.values())
    for arm in arm_ids:
        values = history.get(arm, [])
        if not values:
            scores.append(float("inf"))
            continue
        normalized_mean = sum((value + 1.0) / 2.0 for value in values) / len(values)
        scores.append(normalized_mean + 0.35 * math.sqrt(math.log(total + 2) / len(values)))
    if unseen:
        start_index = ((normal_index + 1) % len(options)
                       if intervention_applied else normal_index)
        ordered = [(start_index + offset) % len(options) for offset in range(len(options))]
        selected_index = next(index for index in ordered if index in unseen)
        selection_reason = ("self_evolution_counterfactual_exploration"
                            if intervention_applied else "egpl_explore_unobserved_arm")
    else:
        ranked = sorted(range(len(options)), key=lambda index: (-scores[index], index))
        selected_index = ranked[0]
        selection_reason = "egpl_ucb_verified_terminal_reward"
        if intervention_applied and len(ranked) > 1:
            selected_index = ranked[1]
            selection_reason = "self_evolution_counterfactual_exploration"
    llm_review: dict[str, Any] = {}
    finite_ranked = sorted((i for i in range(len(options)) if math.isfinite(scores[i])),
                           key=lambda index: (-scores[index], index))
    review_indexes = unseen if unseen else finite_ranked[:3]
    llm_required = _llm_deliberation_enabled(root)
    if review_indexes and llm_required:
        llm_review = _llm_deliberate(project_id, [
            {"arm_id": arm_ids[index], "event_type": options[index][0],
             "parameters": options[index][1],
             "verified_samples": len(history.get(arm_ids[index], [])),
             "mean_reward": (round(sum(history[arm_ids[index]]) / len(history[arm_ids[index]]), 4)
                             if history.get(arm_ids[index]) else None),
             "ucb_score": (round(scores[index], 4) if math.isfinite(scores[index]) else None)}
            for index in review_indexes
        ])
        reviewed_arm = str(llm_review.get("arm_id") or "")
        close_ranked = (len(finite_ranked) > 1
                        and abs(scores[finite_ranked[0]] - scores[finite_ranked[1]]) <= 0.15)
        allowed_override = set(arm_ids[index] for index in unseen)
        if not unseen and close_ranked:
            allowed_override = set(arm_ids[index] for index in finite_ranked[:2])
        if reviewed_arm in allowed_override:
            selected_index = arm_ids.index(reviewed_arm)
            selection_reason = ("egpl_unseen_exploration_with_llm_deliberation" if unseen
                                else "egpl_ucb_close_tie_with_llm_deliberation")
    event_type, parameters = options[selected_index]
    considered = [
        {"index": index, "event_type": event, "strategy_id": str(params.get("strategy_id") or "")}
        for index, (event, params) in enumerate(options)
    ]
    identity = _digest({
        "instance_id": instance_id, "project_id": project_id,
        "project_steps": project_steps, "learning_interruptions": learning_interruptions,
        "selected_index": selected_index, "considered": considered,
    })
    selection = {
        "selection_id": f"project_action_{identity}",
        "instance_id": instance_id,
        "project_id": project_id,
        "project_steps": int(project_steps),
        "learning_interruptions": int(learning_interruptions),
        "normal_index": normal_index,
        "selected_index": selected_index,
        "event_type": event_type,
        "arm_id": arm_ids[selected_index],
        "parameters": dict(parameters),
        "considered": considered,
        "selection_reason": selection_reason,
        "learning_intervention_applied": intervention_applied,
        "policy_decision_key": f"native_project_action:{project_id}:v1",
        "policy_learning_name": "experience_guided_policy_learning",
        "policy_learning_display_name": "经验驱动策略学习",
        "selection_algorithm": "ucb_contextual_bandit",
        "reward_source": "verified_terminal_trajectory",
        "arm_statistics": [
            {"arm_id": arm, "samples": len(history.get(arm, [])),
             "mean_reward": (round(sum(history[arm]) / len(history[arm]), 4)
                             if history.get(arm) else None),
             "ucb_score": (round(scores[index], 4) if math.isfinite(scores[index]) else None)}
            for index, arm in enumerate(arm_ids)
        ],
        "llm_review": llm_review,
        "llm_participation": {"required": llm_required,
                              "completed": bool(llm_review and not llm_review.get("error")),
                              "calls": int(llm_review.get("_llm_calls") or 0),
                              "role": "bounded_deliberation_no_promotion_authority"},
        "hypothesis_candidate": hypothesis_candidate,
    }
    output = root / "share/mind/governance/experience_guided_policy/native_action_selections" / f"{selection['selection_id']}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    selected_event_type = (
        "active_learning/project_action_selected" if intervention_applied
        else "project/action_selected"
    )
    payload = {key: selection[key] for key in (
        "instance_id", "event_type", "parameters", "selection_reason",
        "learning_intervention_applied")}
    try:
        append_evolution_event(
            str(root), selected_event_type,
            subject_id=selection["selection_id"], project_id=project_id,
            payload=payload, evidence_refs=[str(output)],
            idempotency_key=f"project-action:{selection['selection_id']}",
        )
    except ValueError as exc:
        # A long-running instance can import evolution_events before a rolling
        # upgrade adds the new event names.  Do not fail the business plan for
        # this schema-cache mismatch: preserve the precise subtype in the
        # payload using the pre-existing compatibility event, then use the new
        # type after the process naturally restarts.
        if "unsupported evolution event_type" not in str(exc):
            raise
        append_evolution_event(
            str(root), "active_learning/strategy_revised",
            subject_id=selection["selection_id"], project_id=project_id,
            payload={**payload, "semantic_event_type": selected_event_type,
                     "compatibility_reason": "rolling_upgrade_event_registry_cache"},
            evidence_refs=[str(output)],
            idempotency_key=f"project-action-compat:{selection['selection_id']}",
        )
    return selection
