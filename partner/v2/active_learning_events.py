"""Event-first entry points.

``agent_active_learning_*`` is a compatibility identifier retained for old
plans.  Its semantics are Partner self-evolution; external knowledge active
learning lives under ``research_active_learning_*``.
"""
from __future__ import annotations

from typing import Any
import json
import re
from pathlib import Path

from partner.governance.active_learning import (
    diagnose_agent_failure,
    evaluate_skipped_terminal_repair,
    run_skipped_terminal_fresh_canary,
    run_handoff_intent_fresh_canary,
    run_preflight_input_manifest_fresh_canary,
    record_agent_experiment_feedback,
    run_manual_learning_matched_experiment,
    refine_failure_taxonomy,
    select_agent_experiment,
    observe_manual_failure,
)
from partner.governance.storage import workspace_root
from partner.governance.sprint18_learning import (
    ingest_learning_observations,
    propose_candidate_for_topic,
    run_sprint18_learning_cycle,
    select_learning_topic,
    update_learning_policy,
)
from partner.learn.targetdiff_active_learning import (
    diagnose_targetdiff_uncertainty,
    evaluate_targetdiff_uncertainty_candidate,
    evaluate_targetdiff_robustness,
    run_targetdiff_active_learning,
)


def atomic_agent_active_learning_select(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    instances = [str(value) for value in params.get("instance_ids") or ["03", "05"]]
    result = select_agent_experiment(
        root, instance_ids=instances,
        focus_failure_class=str(params.get("focus_failure_class") or ""),
    )
    return {**result, "semantic_kind": "partner_self_evolution",
            "legacy_event_name": "agent_active_learning_select"}


def atomic_agent_active_learning_feedback(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    raw_success = params.get("success")
    success = raw_success is True or str(raw_success).strip().lower() in {"1", "true", "yes"}
    return record_agent_experiment_feedback(
        root, context_key=str(params.get("context_key") or ""),
        option_id=str(params.get("option_id") or ""), success=success,
        evidence_refs=[str(value) for value in params.get("evidence_refs") or [] if str(value)],
    )


def atomic_agent_active_learning_diagnostic_shadow(ctx: Any,
                                                   params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    result = diagnose_agent_failure(
        root, failure_class=str(params.get("failure_class") or params.get("context_key") or ""),
        evidence_refs=[str(value) for value in params.get("evidence_refs") or [] if str(value)],
    )
    return {**result, "semantic_kind": "partner_self_evolution",
            "legacy_event_name": "agent_active_learning_diagnostic_shadow"}


def _episode_state(ctx: Any, episode_id: str) -> tuple[Path, dict[str, Any]]:
    safe = str(episode_id or "").strip()
    if not re.fullmatch(r"episode_[A-Za-z0-9_-]+", safe):
        raise ValueError("invalid episode_id")
    root = workspace_root(str(getattr(ctx, "workspace", "")))
    path = root / "share/mind/governance/episodes" / safe / "state.json"
    if not path.is_file():
        raise ValueError(f"episode not found: {safe}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"episode state is invalid: {safe}")
    return path, value


def atomic_agent_active_learning_observe_episode(ctx: Any,
                                                  params: dict[str, Any]) -> dict[str, Any]:
    path, state = _episode_state(ctx, str(params.get("episode_id") or ""))
    classes = [str(value) for value in state.get("failure_classes") or [] if str(value)]
    return {
        "ok": True, "status": "episode_observed",
        "episode_id": state.get("episode_id"), "task_id": state.get("task_id"),
        "instance_id": state.get("instance_id"), "failure_classes": classes,
        "path": str(path), "evidence_refs": [str(path)],
        "content": f"已观察 {state.get('episode_id')}；失败类别：{', '.join(classes) or '未标注'}",
        "production_mutation": False, "production_effective": False,
        "semantic_kind": "partner_self_evolution",
        "legacy_event_name": "agent_active_learning_observe_episode",
    }


def atomic_agent_active_learning_propose_episode_repair(ctx: Any,
                                                         params: dict[str, Any]) -> dict[str, Any]:
    state_path, state = _episode_state(ctx, str(params.get("episode_id") or ""))
    root = workspace_root(str(getattr(ctx, "workspace", "")))
    result = observe_manual_failure(
        str(root), instance_id=str(state.get("instance_id") or "04"),
        task_id=str(state.get("task_id") or ""),
        reduced={"state": state, "bundle": str(state_path.parent)},
    )
    if not result.get("ok"):
        return {**result, "production_mutation": False, "production_effective": False}
    task_dir = Path(str(getattr(ctx, "working_dir", "") or
                        getattr(getattr(ctx, "task_instance", None), "working_dir", "")))
    task_dir.mkdir(parents=True, exist_ok=True)
    output = task_dir / f"active_learning_review_{state.get('episode_id')}.json"
    proposal = result.get("proposal") or {}
    review = {
        "schema_version": 1, "status": result.get("status"),
        "episode_id": state.get("episode_id"), "task_id": state.get("task_id"),
        "failure_class": proposal.get("failure_class"),
        "mechanism": proposal.get("failure_class"),
        "proposal_id": proposal.get("proposal_id"),
        "selection_id": proposal.get("selection_id"),
        "diagnosis_id": proposal.get("diagnosis_id"),
        "evidence_refs": proposal.get("evidence_refs") or [str(state_path)],
        "required_next_gate": proposal.get("required_next_gate"),
        "production_mutation": False, "production_effective": False,
        "control_policy_modified": False, "promotion": False,
    }
    output.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True, "status": "readonly_episode_repair_proposed",
        "proposal": proposal, "review": review, "path": str(output),
        "files": [str(output)],
        "content": (
            f"只读主动学习完成：failure_class={review['failure_class']}；"
            f"proposal_id={review['proposal_id']}；production_effective=false；未修改 control_policy，未晋升。"
        ),
        "production_mutation": False, "production_effective": False,
    }


def atomic_agent_active_learning_refine_taxonomy(ctx: Any,
                                                 params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return refine_failure_taxonomy(root, diagnosis_path=str(params.get("diagnosis_path") or ""))


def atomic_agent_active_learning_skipped_terminal_repair_shadow(
        ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return evaluate_skipped_terminal_repair(
        root, diagnosis_path=str(params.get("diagnosis_path") or ""))


def atomic_agent_active_learning_skipped_terminal_fresh_canary(
        ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return run_skipped_terminal_fresh_canary(
        root, canary_id=str(params.get("canary_id") or ""))


def atomic_agent_active_learning_handoff_intent_fresh_canary(
        ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return run_handoff_intent_fresh_canary(
        root, canary_id=str(params.get("canary_id") or ""))


def atomic_agent_active_learning_preflight_manifest_fresh_canary(
        ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return run_preflight_input_manifest_fresh_canary(
        root, canary_id=str(params.get("canary_id") or ""))


def atomic_agent_active_learning_manual_failure_matched(
        ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    result = run_manual_learning_matched_experiment(
        root, experiment_id=str(params.get("experiment_id") or ""),
    )
    # A rejected/inconclusive Candidate is a valid terminal observation for
    # the learning Event.  Preserve the experiment verdict separately; do not
    # turn it into an execution failure that blocks the native learning task.
    experiment = dict(result.get("experiment") or {})
    verdict = str(experiment.get("status") or result.get("status") or "inconclusive")
    if verdict in {"passed", "rejected", "inconclusive"} and result.get("path"):
        return {
            **result,
            "ok": True,
            "status": "matched_experiment_recorded",
            "experiment_verdict": verdict,
            "candidate_validated": verdict == "passed",
            "content": (
                f"matched experiment recorded: verdict={verdict}; "
                "production_effective=false"
            ),
            "production_mutation": False,
            "production_effective": False,
        }
    return result


def atomic_learning_observation_ingest(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return ingest_learning_observations(root)


def atomic_learning_topic_select(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return select_learning_topic(root)


def atomic_learning_candidate_propose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return propose_candidate_for_topic(root, str(params.get("decision_path") or ""))


def atomic_learning_policy_update(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return update_learning_policy(root)


def atomic_sprint18_learning_cycle(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return run_sprint18_learning_cycle(root)


def atomic_targetdiff_active_learning(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    raw_weights = params.get("active_weights") or [.55, .30, .15]
    return run_targetdiff_active_learning(
        root, run_id=str(params.get("run_id") or ""),
        rounds=int(params.get("rounds") or 3),
        batch_size=int(params.get("batch_size") or 40),
        active_weights=tuple(float(value) for value in raw_weights),
        seed=int(params.get("seed") or 20260901),
        active_mode=str(params.get("active_mode") or "raw"),
    )


def atomic_targetdiff_active_robustness(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return evaluate_targetdiff_robustness(
        root,
        run_ids=[str(value) for value in params.get("run_ids") or [] if str(value)],
        evaluation_id=str(params.get("evaluation_id") or
                          "sprint18_targetdiff_seed_robustness_v1"),
    )


def atomic_targetdiff_uncertainty_diagnostic(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return diagnose_targetdiff_uncertainty(
        root,
        seeds=[int(value) for value in params.get("seeds") or [20260901, 20260902, 20260903]],
        evaluation_id=str(params.get("evaluation_id") or
                          "sprint18_targetdiff_uncertainty_diagnostic_v1"),
        labelled_budget=int(params.get("labelled_budget") or 240),
    )


def atomic_targetdiff_uncertainty_candidate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    root = str(workspace_root(str(getattr(ctx, "workspace", ""))))
    return evaluate_targetdiff_uncertainty_candidate(
        root,
        seeds=[int(value) for value in params.get("seeds") or [20260901, 20260902, 20260903]],
        evaluation_id=str(params.get("evaluation_id") or
                          "sprint18_targetdiff_crossfit_uncertainty_candidate_v1"),
        labelled_budget=int(params.get("labelled_budget") or 240),
    )


HANDLERS = {
    "agent_active_learning_observe_episode": atomic_agent_active_learning_observe_episode,
    "agent_active_learning_select": atomic_agent_active_learning_select,
    "agent_active_learning_feedback": atomic_agent_active_learning_feedback,
    "agent_active_learning_diagnostic_shadow": atomic_agent_active_learning_diagnostic_shadow,
    "agent_active_learning_refine_taxonomy": atomic_agent_active_learning_refine_taxonomy,
    "agent_active_learning_skipped_terminal_repair_shadow": atomic_agent_active_learning_skipped_terminal_repair_shadow,
    "agent_active_learning_skipped_terminal_fresh_canary": atomic_agent_active_learning_skipped_terminal_fresh_canary,
    "agent_active_learning_handoff_intent_fresh_canary": atomic_agent_active_learning_handoff_intent_fresh_canary,
    "agent_active_learning_preflight_manifest_fresh_canary": atomic_agent_active_learning_preflight_manifest_fresh_canary,
    "agent_active_learning_manual_failure_matched": atomic_agent_active_learning_manual_failure_matched,
    "agent_active_learning_propose_episode_repair": atomic_agent_active_learning_propose_episode_repair,
    "learning_observation_ingest": atomic_learning_observation_ingest,
    "learning_topic_select": atomic_learning_topic_select,
    "learning_candidate_propose": atomic_learning_candidate_propose,
    "learning_policy_update": atomic_learning_policy_update,
    "sprint18_learning_cycle": atomic_sprint18_learning_cycle,
    "targetdiff_active_learning": atomic_targetdiff_active_learning,
    "targetdiff_active_robustness": atomic_targetdiff_active_robustness,
    "targetdiff_uncertainty_diagnostic": atomic_targetdiff_uncertainty_diagnostic,
    "targetdiff_uncertainty_candidate": atomic_targetdiff_uncertainty_candidate,
}

CANDIDATE_HANDLERS = {
    "agent_active_learning_diagnostic_shadow": atomic_agent_active_learning_diagnostic_shadow,
    "agent_active_learning_refine_taxonomy": atomic_agent_active_learning_refine_taxonomy,
    "agent_active_learning_skipped_terminal_repair_shadow": atomic_agent_active_learning_skipped_terminal_repair_shadow,
    "agent_active_learning_skipped_terminal_fresh_canary": atomic_agent_active_learning_skipped_terminal_fresh_canary,
    "agent_active_learning_handoff_intent_fresh_canary": atomic_agent_active_learning_handoff_intent_fresh_canary,
    "agent_active_learning_preflight_manifest_fresh_canary": atomic_agent_active_learning_preflight_manifest_fresh_canary,
    "agent_active_learning_manual_failure_matched": atomic_agent_active_learning_manual_failure_matched,
    "learning_topic_select": atomic_learning_topic_select,
    "learning_candidate_propose": atomic_learning_candidate_propose,
    "learning_policy_update": atomic_learning_policy_update,
    "sprint18_learning_cycle": atomic_sprint18_learning_cycle,
}
