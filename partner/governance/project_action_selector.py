"""Event-first, reward-grounded selection of the next project action."""
from __future__ import annotations

import hashlib
import inspect
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


def _human_arm_name(arm_id: str) -> str:
    return {
        "molecular_scaffold_cap": "骨架配额选择",
        "molecular_pareto_diverse": "Pareto 多样性选择",
        "molecular_maxmin_fingerprint": "MaxMin 指纹选择",
        "molecular_scaffold_round_robin": "骨架轮转选择",
        "molecular_llm_greedy_dsl": "LLM 受限贪心策略",
        "molecular_1bvr_docking_holdout": "1BVR 固定口袋对接",
    }.get(str(arm_id), str(arm_id))


def _effective_selector_trajectories(root: Path) -> list[dict[str, Any]]:
    """Project the latest revision while retaining legacy rows without ids."""
    path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-2000:]
    except OSError:
        return []
    latest: dict[str, tuple[int, int, dict[str, Any]]] = {}
    anonymous: list[tuple[int, dict[str, Any]]] = []
    for offset, line in enumerate(lines):
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        trajectory_id = str(row.get("trajectory_id") or "")
        if not trajectory_id:
            anonymous.append((offset, row))
            continue
        revision = int(row.get("revision") or 1)
        prior = latest.get(trajectory_id)
        if prior is None or (revision, offset) >= (prior[0], prior[1]):
            latest[trajectory_id] = (revision, offset, row)
    ordered = [*anonymous, *((value[1], value[2]) for value in latest.values())]
    return [row for _offset, row in sorted(ordered, key=lambda value: value[0])]


def _reward_history(root: Path, project_id: str) -> dict[str, list[float]]:
    history: dict[str, list[float]] = {}
    for row in _effective_selector_trajectories(root):
        if str(row.get("project_id") or "") != project_id:
            continue
        action = row.get("action") if isinstance(row.get("action"), dict) else {}
        # Generated hypothesis Candidates have a unique identity even when
        # they exercise the same Event strategy.  Learn against that identity
        # so one falsified Candidate is not repeatedly treated as unseen.
        arm = str(action.get("selection_arm_id")
                  or action.get("experience_candidate_id")
                  or action.get("native_action_id") or "")
        if not arm:
            continue
        try:
            reward = float(row.get("reward"))
        except (TypeError, ValueError):
            continue
        history.setdefault(arm, []).append(max(-1.0, min(1.0, reward)))
    return history


def _recent_arm_sequence(root: Path, project_id: str, *, limit: int = 20) -> list[str]:
    """Return recent executed arm identities in durable trajectory order.

    Reward maximisation alone can repeatedly exploit a deterministic probe
    whose output is already known.  The ordered sequence is kept separately
    from aggregated reward history so the selector can enforce a small
    evidence-diversity gate without changing historical rewards.
    """
    output: list[str] = []
    for row in _effective_selector_trajectories(root):
        if str(row.get("project_id") or "") != project_id:
            continue
        action = row.get("action") if isinstance(row.get("action"), dict) else {}
        arm = str(action.get("selection_arm_id")
                  or action.get("experience_candidate_id")
                  or action.get("native_action_id") or "")
        if arm:
            output.append(arm)
    return output[-max(1, int(limit)):]


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


def _llm_deliberate(root: Path, instance_id: str, project_id: str,
                    candidates: list[dict[str, Any]],
                    cognition_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run observe -> counter-read -> synthesis before a substantive choice."""
    try:
        from partner.adapters.direct_api import chat
        from .project_cognition import model_safe_cognition_context
        from .deep_context import build_deep_context_pack, render_deep_context
        safe_cognition_context = model_safe_cognition_context(cognition_context or {})
        relevant_code = {
            "molecular_generation": [
                "partner/v2/molecular_iteration_events.py",
                "partner/governance/project_action_selector.py",
            ],
            "literature_github_learning": [
                "partner/v2/external_learning_events.py",
                "partner/governance/research_adoption.py",
            ],
            "partner_explore": [
                "partner/governance/active_learning.py",
                "partner/evolution/bounded_code_candidate.py",
            ],
        }.get(project_id, ["partner/governance/project_action_selector.py"])
        deep_pack = build_deep_context_pack(
            root, project_id=project_id, purpose="project_action_deliberation",
            instance_id=instance_id, relevant_code=relevant_code, max_chars=90000,
            privacy_mode="derived_only",
        )
        base = (
            "严格区分现实证据、解释和未知；联想不是因果。业务推进、外部学习与 Partner 自进化不得互相冒充。"
            "只能选择给定 arm_id；可以提出 proposed_event_candidate 供以后隔离验证，但本轮不得直接执行新动作。"
            "只输出当前角色要求的严格 JSON。loop_kind 只能是 business_progress、external_active_learning、"
            "partner_self_evolution。项目=" + project_id + "\n" + project_reasoning_contract()
            + "\n每个数组最多6项，每项只写一个事实；reason与假设各不超过180个汉字，"
            "不要复述候选全文。引用记忆时只能使用项目情境里的 memory_id；用户习惯只能约束表达/工作方式，"
            "不能作为业务因果证据。\n项目情境="
            + json.dumps(safe_cognition_context, ensure_ascii=False)
            + "\n深上下文证据包=" + render_deep_context(deep_pack)
            + "\n候选=" + json.dumps(candidates, ensure_ascii=False)
        )
        drafts: list[dict[str, Any]] = []
        roles = (
            ("project_action_observation", 5000,
             "第一遍重建事实。字段仅为 observed_facts、constraints、unknowns、arm_observations；"
             "arm_observations 是 arm_id 到可证伪观测的一句话映射。不要选择。"),
            ("project_action_association", 5000,
             "第二遍做受控联想。字段仅为 relevant_memories、structural_analogies、transfer_limits、"
             "new_questions。每条联想必须带 memory_id，并说明哪里只是相似、不能当因果。不要选择。"),
            ("project_action_counter_read", 5000,
             "第三遍只反驳。字段仅为 opposing_hypothesis、counterexamples、repetition_risks、"
             "missing_evidence。不要选择。"),
            ("project_action_candidate_invention", 6000,
             "第四遍发明候选思路。字段仅为 candidate_hypotheses、falsifiers、information_gain、"
             "proposed_event_candidate。至少比较两个不同机制；新 Event 仅是以后隔离编译的建议，"
             "本轮仍不得选择或执行白名单外能力。"),
            ("project_action_synthesis", 7000,
             "第五遍才选择。字段为 arm_id、observed_facts、analogy_not_causality、constraints、"
             "main_hypothesis、opposing_hypothesis、counterexample、reason、next_belief_update、"
             "loop_kind、proposed_event_candidate、memory_refs_used、memory_influence、project_question、"
             "decision_summary、user_narrative。选择最小但有业务信息增量的一项，并写停止或转向条件。"
             "user_narrative 用三句短话说明原问题、现在做什么、为什么这比重复上一轮更有价值。"),
        )
        calls = 0
        for purpose, token_limit, instruction in roles:
            prompt = instruction + "\n" + base
            if drafts:
                prompt += "\n已有审议" + json.dumps(drafts, ensure_ascii=False)[:16000]
            raw = chat(prompt, purpose=purpose, max_tokens=token_limit,
                       temperature=0.2,
                       timeout=(180 if purpose == "project_action_synthesis" else 120),
                       workspace=str(root),
                       instance_id=instance_id, project_id=project_id,
                       event_type="project_action_selected")
            calls += 1
            value = _parse_llm_json(str(raw or ""))
            required_by_role = {
                "project_action_observation": ("observed_facts", "unknowns"),
                "project_action_association": ("relevant_memories", "transfer_limits"),
                "project_action_counter_read": ("opposing_hypothesis", "missing_evidence"),
                "project_action_candidate_invention": ("candidate_hypotheses", "falsifiers"),
                "project_action_synthesis": (
                    "arm_id", "loop_kind", "memory_refs_used", "memory_influence",
                    "project_question", "decision_summary", "user_narrative",
                ),
            }
            required = required_by_role[purpose]
            structurally_complete = bool(value and all(key in value for key in required))
            if not structurally_complete:
                repair_evidence = (str(raw or "")[-24000:] if str(raw or "").strip()
                                   else base[-60000:] + "\n已有审议="
                                   + json.dumps(drafts, ensure_ascii=False)[-22000:])
                repaired = chat(
                    "把下列原始审议整理成符合本角色合同的一个 JSON 对象。不得增加事实；缺失信息用空数组、"
                    "空字符串或 null 表示，但所有要求字段必须存在。角色合同：" + instruction
                    + "\n只输出 JSON，不复述任务，不输出 Markdown。\n原始审议：\n"
                    + repair_evidence,
                    purpose=purpose + "_json_repair", max_tokens=4000,
                    temperature=0.0, timeout=150, workspace=str(root),
                    instance_id=instance_id, project_id=project_id,
                    event_type="project_action_selected",
                )
                calls += 1
                value = _parse_llm_json(str(repaired or ""))
                structurally_complete = bool(
                    value and all(key in value for key in required)
                )
            if not structurally_complete:
                # One malformed critic must not erase the other epistemic
                # passes. Preserve the failure as structured evidence and let
                # synthesis reason with an explicit unknown.  Synthesis itself
                # remains mandatory and is checked after all three roles.
                value = {
                    "role": purpose,
                    "parse_error": True,
                    "unknowns": [f"{purpose}_json_missing"],
                    "raw_excerpt": str(raw or "")[-1000:],
                }
            drafts.append(value)
        value = dict(drafts[-1])
        value["_llm_calls"] = calls
        value["deliberation_passes"] = [role for role, _, _instruction in roles]
        value["degraded_passes"] = [
            role for (role, _limit, _instruction), draft in zip(roles, drafts)
            if draft.get("parse_error")
        ]
        value["deep_context_manifest"] = deep_pack["manifest_path"]
        value["deep_context_coverage"] = deep_pack["manifest"]["coverage"]
        value["deep_context_chars"] = deep_pack["manifest"]["context_chars"]
        if value.get("parse_error"):
            value["error"] = "project_action_synthesis_json_missing"
        return value
    except Exception as exc:  # LLM is advisory; the auditable bandit remains available.
        return {"error": type(exc).__name__, "_llm_calls": locals().get("calls", 0)}


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
    # Two consecutive non-positive terminals remove a known arm from the
    # immediate choice set.  This prevents UCB exploration from turning a
    # finite project into an endless rotation of already-falsified actions.
    from .portfolio_improvement import option_health
    all_options = list(options)
    all_arm_ids = [_arm_id(event, params) for event, params in all_options]
    health = option_health(root, project_id, all_arm_ids)
    eligible_ids = set(health["eligible_arm_ids"])
    filtered = [(event, params) for event, params in options
                if _arm_id(event, params) in eligible_ids]
    if filtered:
        options = filtered
    forced_diversity_arm = ""
    # If historical health filtering leaves only the very deterministic arm
    # that has just been executed twice, fail-open must not mean "run the same
    # PDF probe forever". Re-admit exactly one least-sampled alternative for
    # a bounded counterfactual turn. Its own terminal Reward remains fully
    # authoritative and it gains no promotion privilege from this escape.
    preselection_recent = _recent_arm_sequence(
        root, project_id, limit=max(2, len(all_options)),
    )
    if (len(options) == 1 and len(all_options) > 1
            and len(preselection_recent) >= 2
            and preselection_recent[-1] == preselection_recent[-2]
            and _arm_id(*options[0]) == preselection_recent[-1]):
        alternatives = [row for row in all_options if _arm_id(*row) != preselection_recent[-1]]
        history_for_escape = _reward_history(root, project_id)
        if alternatives:
            chosen = min(
                alternatives,
                key=lambda row: (
                    len(history_for_escape.get(_arm_id(*row), [])),
                    preselection_recent.count(_arm_id(*row)),
                    all_options.index(row),
                ),
            )
            options = [chosen]
            forced_diversity_arm = _arm_id(*chosen)
    from .project_cognition import (
        build_project_cognition_context,
        latest_action_preference,
        record_action_decision,
    )
    cognition_context = build_project_cognition_context(
        root, project_id, options=options, project_steps=project_steps,
    )
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
        review_candidates = [
            {"arm_id": arm_ids[index], "event_type": options[index][0],
             "parameters": options[index][1],
             "verified_samples": len(history.get(arm_ids[index], [])),
             "mean_reward": (round(sum(history[arm_ids[index]]) / len(history[arm_ids[index]]), 4)
                             if history.get(arm_ids[index]) else None),
             "ucb_score": (round(scores[index], 4) if math.isfinite(scores[index]) else None)}
            for index in review_indexes
        ]
        # Preserve the old four-argument seam used by downstream adapters and
        # tests while the native implementation consumes the richer context.
        if len(inspect.signature(_llm_deliberate).parameters) >= 5:
            llm_review = _llm_deliberate(
                root, instance_id, project_id, review_candidates, cognition_context,
            )
        else:  # pragma: no cover - compatibility adapter exercised downstream
            llm_review = _llm_deliberate(root, instance_id, project_id, review_candidates)
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
    preference = latest_action_preference(root, project_id, arm_ids)
    cognitive_preference_source = str(preference.get("source_event_id") or "")
    cited_memories = {str(value) for value in llm_review.get("memory_refs_used") or []}
    fresh_review_consumed_preference = bool(
        cognitive_preference_source
        and f"cognition:{cognitive_preference_source}" in cited_memories
        and str(llm_review.get("arm_id") or "") in set(arm_ids)
    )
    if preference and not fresh_review_consumed_preference:
        selected_index = arm_ids.index(str(preference["arm_id"]))
        selection_reason = "prior_belief_revision_consumed"
    if forced_diversity_arm:
        selection_reason = "evidence_diversity_escape_from_single_repeated_arm"
    # Evidence-diversity hard gate. A successful deterministic check is not
    # infinitely valuable: if the proposed arm already dominates the most
    # recent option-sized window, execute the least-used eligible alternative.
    # This runs after LLM review because the reviewer is advisory and cannot
    # authorize repetitive evidence farming.
    recent_arms = _recent_arm_sequence(root, project_id, limit=max(2, len(options)))
    recent_counts = {arm: recent_arms.count(arm) for arm in arm_ids}
    selected_arm = arm_ids[selected_index]
    minimum_recent = min(recent_counts.values()) if recent_counts else 0
    immediate_repeat = bool(recent_arms and recent_arms[-1] == selected_arm)
    repeated_dominance = bool(recent_counts.get(selected_arm, 0) >= 2
                              and recent_counts.get(selected_arm, 0) > minimum_recent)
    if len(options) > 1 and (immediate_repeat or repeated_dominance):
        alternatives = [index for index, arm in enumerate(arm_ids)
                        if arm != selected_arm and (
                            immediate_repeat or recent_counts.get(arm, 0) == minimum_recent)]
        if alternatives:
            selected_index = max(
                alternatives,
                key=lambda index: (scores[index], -index),
            )
            selection_reason = ("evidence_diversity_gate_after_repeated_arm"
                                if repeated_dominance
                                else "evidence_diversity_gate_after_immediate_repeat")
    event_type, parameters = options[selected_index]
    effective_review = dict(llm_review)
    if cognitive_preference_source and selection_reason == "prior_belief_revision_consumed":
        preference_memory_id = f"cognition:{cognitive_preference_source}"
        effective_review["memory_refs_used"] = list(dict.fromkeys([
            *[str(value) for value in effective_review.get("memory_refs_used") or []],
            preference_memory_id,
        ]))
    reviewed_arm = str(llm_review.get("arm_id") or "")
    if ((reviewed_arm and reviewed_arm != arm_ids[selected_index])
            or (selection_reason == "prior_belief_revision_consumed" and not reviewed_arm)):
        override_reason = (
            str(preference.get("reason") or "上一轮终态建议转向")
            if selection_reason == "prior_belief_revision_consumed"
            else "重复/证据多样性硬门要求改选"
        )
        effective_review.update({
            "memory_influence": override_reason,
            "decision_summary": (
                f"综合审议原建议 {reviewed_arm or '未形成可执行选择'}；最终硬门实际选择 {arm_ids[selected_index]}："
                f"{override_reason}。"
            ),
            "user_narrative": (
                f"上一轮留下了尚未解决的问题。本轮没有机械重复既有路线，"
                f"而是实际改用{_human_arm_name(arm_ids[selected_index])}。"
                "这次结果将检验转向是否带来新证据。"
            ),
        })
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
        "recent_arm_sequence": recent_arms,
        "evidence_diversity_gate_applied": (
            selection_reason in {
                "evidence_diversity_gate_after_repeated_arm",
                "evidence_diversity_gate_after_immediate_repeat",
                "evidence_diversity_escape_from_single_repeated_arm",
            }
        ),
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
        "value_gate": health,
        "project_cognition": {
            "latest_receipt_id": cognition_context.get("latest_receipt_id"),
            "current_questions": cognition_context.get("current_questions"),
            "memory_ids": cognition_context.get("memory_ids"),
            "context_chars": cognition_context.get("context_chars"),
            "memory_refs_used": [value for value in effective_review.get("memory_refs_used") or []
                                 if value in set(cognition_context.get("memory_ids") or [])],
            "memory_influence": str(effective_review.get("memory_influence") or ""),
            "project_question": str(effective_review.get("project_question") or ""),
            "decision_summary": str(effective_review.get("decision_summary")
                                    or effective_review.get("reason") or ""),
            "user_narrative": str(effective_review.get("user_narrative") or ""),
            "cognitive_preference_source": cognitive_preference_source,
            "preference_reassessed_by_fresh_synthesis": fresh_review_consumed_preference,
        },
    }
    output = root / "share/mind/governance/experience_guided_policy/native_action_selections" / f"{selection['selection_id']}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record_action_decision(
        root, project_id=project_id, selection_id=selection["selection_id"],
        context=cognition_context, review=effective_review,
        chosen_arm_id=selection["arm_id"], event_type=event_type,
        parameters=parameters,
        cognitive_preference_source=cognitive_preference_source,
    )
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
