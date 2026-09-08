"""Evidence-backed offline reinforcement signals for Partner evolution.

This module does not train model weights and never promotes code.  It converts
persisted Campaign outcomes into auditable trajectories and updates a bounded
contextual-bandit candidate policy.  Promotion remains the responsibility of
EvolutionExperiment and PromotionDecision gates.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .campaign_storage import list_work_items, load_campaign
from .models import now_iso
from .storage import append_jsonl, atomic_json, workspace_root


REWARD_SPEC = {
    "schema_version": 2,
    "range": [-1.0, 1.0],
    "components": {
        "business_progress": 0.45,
        "novel_evidence": 0.20,
        "handoff_consumed": 0.15,
        "accepted_completed": 0.05,
        "artifact_contract": 0.05,
        "delivery_contract": 0.05,
        "meaningful_event": 0.05,
        "controlled_wait": 0.05,
        "monitor_cost": -0.05,
        "failed": -0.45,
        "watchdog_or_timeout": -0.25,
        "missing_artifact": -0.18,
        "missing_delivery": -0.18,
        "retry": -0.08,
    },
    "policy_gate": {"minimum_samples": 3, "minimum_mean_reward": 0.25, "minimum_success_rate": 0.67},
}


def is_synthetic_trajectory(row: dict[str, Any]) -> bool:
    """Reject known test-fixture records from every policy calculation."""
    trajectory_id = str(row.get("trajectory_id") or "")
    experiment_id = str(row.get("experiment_id") or "")
    action = row.get("action") or {}
    decision_key = str(row.get("decision_key") or action.get("policy_decision") or "")
    action_experiment_id = str(action.get("experiment_id") or "")
    outcome = row.get("outcome") or {}
    fixture_prefixes = ("bdk_real_", "bdk_closure_", "fixture_")
    return bool(
        row.get("synthetic_fixture")
        or outcome.get("synthetic_fixture")
        or trajectory_id.startswith(fixture_prefixes)
        or experiment_id.startswith(fixture_prefixes)
        or action_experiment_id.startswith(fixture_prefixes)
        or decision_key.startswith(("bdk_real_promote_test_", "bdk_log_test_", "bdk_closure_"))
    )


def effective_trajectory_reward(row: dict[str, Any]) -> float:
    """Conservatively remove ambiguous pre-ADR-0040 handoff reward."""
    reward = float(row.get("reward") or 0.0)
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    components = (row.get("reward_components")
                  if isinstance(row.get("reward_components"), dict) else {})
    if row.get("kind") == "manual_project_iteration" and "continuation_requested" not in state:
        reward -= max(0.0, float(components.get("handoff_consumed") or 0.0))
    return max(-1.0, min(1.0, reward))


def append_trajectory_correction(workspace: str, *, trajectory_id: str,
                                 failure_owner: str, failure_mechanism: str,
                                 partial_artifacts: list[str] | None = None,
                                 evidence_refs: list[str] | None = None) -> dict[str, Any]:
    """Append a corrected revision while preserving the original JSONL row."""
    root = workspace_root(workspace)
    path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError, TypeError):
        rows = []
    prior = next((row for row in reversed(rows)
                  if str(row.get("trajectory_id") or "") == trajectory_id), None)
    if not prior:
        return {"ok": False, "status": "trajectory_not_found"}
    corrected = json.loads(json.dumps(prior))
    corrected["schema_version"] = max(3, int(corrected.get("schema_version") or 0))
    corrected["revision"] = int(prior.get("revision") or 1) + 1
    corrected["corrected_at"] = now_iso()
    corrected["correction"] = {
        "kind": "append_only_reward_and_attribution_correction",
        "evidence_refs": [str(value) for value in evidence_refs or []],
    }
    artifacts = [str(value) for value in partial_artifacts or [] if str(value)]
    corrected.setdefault("outcome", {}).update({
        "status": "failed", "artifacts": artifacts,
        "artifact_completion": "partial" if artifacts else "none",
        "failure_owner": failure_owner,
        "failure_mechanism": failure_mechanism,
        "business_progress": False, "novel_evidence": False,
    })
    corrected.setdefault("action", {})["action_key"] = (
        f"{corrected.get('instance_id')}:manual_project_iteration:{failure_mechanism}"
    )
    corrected["reward_components"] = {
        "accepted_completed": 0.0, "artifact_contract": 0.0,
        "partial_artifact": 0.05 if artifacts else 0.0,
        "delivery_contract": 0.0, "meaningful_event": 0.0,
        "business_progress": 0.0, "novel_evidence": 0.0,
        "handoff_consumed": 0.0,
    }
    corrected["reward"] = -0.4 if artifacts else -0.45
    corrected["policy_eligible"] = False
    corrected["learning_observation_eligible"] = bool(failure_mechanism)
    append_jsonl(path, corrected)
    return {"ok": True, "status": "trajectory_correction_appended",
            "trajectory": corrected, "path": str(path)}


def append_expected_observation_correction(workspace: str, *, trajectory_id: str,
                                           evidence_refs: list[str] | None = None) -> dict[str, Any]:
    """Neutralize a completed expected-observation trajectory append-only.

    A negative probe can complete the user's task without producing a business
    artifact.  It is neither a policy success sample nor a failure sample.
    """
    root = workspace_root(workspace)
    path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError, TypeError):
        rows = []
    prior = next((row for row in reversed(rows)
                  if str(row.get("trajectory_id") or "") == trajectory_id), None)
    if not prior:
        return {"ok": False, "status": "trajectory_not_found"}
    corrected = json.loads(json.dumps(prior))
    corrected["schema_version"] = max(3, int(corrected.get("schema_version") or 0))
    corrected["revision"] = int(prior.get("revision") or 1) + 1
    corrected["corrected_at"] = now_iso()
    corrected["correction"] = {
        "kind": "append_only_expected_observation_correction",
        "evidence_refs": [str(value) for value in evidence_refs or []],
    }
    corrected.setdefault("action", {})["action_key"] = (
        f"{corrected.get('instance_id')}:manual_project_iteration:"
        "observation/expected_missing_input"
    )
    corrected.setdefault("outcome", {}).update({
        "status": "completed", "monitor_only": True,
        "business_progress": False, "novel_evidence": False,
        "false_success": False, "failure_owner": "input_state",
        "failure_mechanism": "observation/expected_missing_input",
    })
    corrected["reward_components"] = {
        "accepted_completed": 0.0, "artifact_contract": 0.0,
        "partial_artifact": 0.0, "delivery_contract": 0.0,
        "meaningful_event": 0.0, "business_progress": 0.0,
        "novel_evidence": 0.0, "handoff_consumed": 0.0,
    }
    corrected["reward"] = 0.0
    corrected["policy_eligible"] = False
    corrected["learning_observation_eligible"] = False
    append_jsonl(path, corrected)
    return {"ok": True, "status": "expected_observation_correction_appended",
            "trajectory": corrected, "path": str(path)}


def append_campaign_monitor_correction(workspace: str, *, trajectory_id: str,
                                       evidence_refs: list[str] | None = None) -> dict[str, Any]:
    """Neutralize a Campaign control-plane report mislabelled as business work."""
    root = workspace_root(workspace)
    path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError, TypeError):
        rows = []
    prior = next((row for row in reversed(rows)
                  if str(row.get("trajectory_id") or "") == trajectory_id), None)
    if not prior:
        return {"ok": False, "status": "trajectory_not_found"}
    if "campaign_report_delivery" not in ((prior.get("action") or {}).get("event_types") or []):
        return {"ok": False, "status": "not_campaign_monitor"}
    corrected = json.loads(json.dumps(prior))
    corrected["schema_version"] = max(3, int(corrected.get("schema_version") or 0))
    corrected["revision"] = int(prior.get("revision") or 1) + 1
    corrected["corrected_at"] = now_iso()
    corrected["correction"] = {
        "kind": "append_only_campaign_monitor_correction",
        "evidence_refs": [str(value) for value in evidence_refs or []],
    }
    corrected.setdefault("action", {}).update({
        "action_key": f"{corrected.get('instance_id')}:campaign_monitor:campaign_report_delivery",
        "strategy_id": "campaign_monitor_v1", "policy_decision": "",
        "policy_arm": "", "experiment_id": "", "match_key": "",
    })
    corrected.setdefault("state", {})["receipt_id"] = (
        "monitor_" + hashlib.sha256(str(corrected.get("work_item_id") or "").encode()).hexdigest()[:16]
    )
    corrected.setdefault("outcome", {}).update({
        "status": "completed", "monitor_only": True,
        "business_progress": False, "learning_progress": False,
        "novel_evidence": False, "false_success": False,
        "failure_owner": "", "failure_mechanism": "",
    })
    corrected["reward_components"] = {
        "accepted_completed": 0.0, "artifact_contract": 0.0,
        "partial_artifact": 0.0, "delivery_contract": 0.0,
        "meaningful_event": 0.0, "business_progress": 0.0,
        "learning_progress": 0.0, "novel_evidence": 0.0,
        "handoff_consumed": 0.0,
    }
    corrected["reward"] = 0.0
    corrected["policy_eligible"] = False
    corrected["learning_observation_eligible"] = False
    append_jsonl(path, corrected)
    return {"ok": True, "status": "campaign_monitor_correction_appended",
            "trajectory": corrected, "path": str(path)}


def append_matched_learning_eligibility_correction(
    workspace: str, *, trajectory_id: str,
) -> dict[str, Any]:
    """Make a real matched baseline/candidate trajectory learnable.

    This only repairs the learning projection.  Delivery, business Reward,
    truth outcome and production-policy eligibility remain exactly as
    originally observed.
    """
    root = workspace_root(workspace)
    path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError, TypeError):
        rows = []
    prior = next((row for row in reversed(rows)
                  if str(row.get("trajectory_id") or "") == trajectory_id), None)
    if not prior:
        return {"ok": False, "status": "trajectory_not_found"}
    action = dict(prior.get("action") or {})
    if (not action.get("experiment_id") or not action.get("match_key")
            or action.get("policy_arm") not in {"baseline", "candidate"}):
        return {"ok": False, "status": "not_matched_experiment"}
    if prior.get("learning_observation_eligible") is True:
        return {"ok": True, "status": "already_learning_eligible",
                "trajectory": prior, "path": str(path)}
    corrected = json.loads(json.dumps(prior))
    corrected["schema_version"] = max(3, int(corrected.get("schema_version") or 0))
    corrected["revision"] = int(prior.get("revision") or 1) + 1
    corrected["corrected_at"] = now_iso()
    corrected["correction"] = {
        "kind": "append_only_matched_learning_eligibility_correction",
        "experiment_id": str(action.get("experiment_id")),
        "match_key": str(action.get("match_key")),
    }
    corrected["learning_observation_eligible"] = True
    # Production policy eligibility is deliberately untouched/fail-closed.
    append_jsonl(path, corrected)
    return {"ok": True, "status": "matched_learning_eligibility_corrected",
            "trajectory": corrected, "path": str(path)}


def append_learning_success_correction(workspace: str, *, trajectory_id: str,
                                       evidence_refs: list[str],
                                       learning_mechanism: str,
                                       strategy_id: str = "sprint18_isolated_candidate",
                                       policy_arm: str = "candidate") -> dict[str, Any]:
    """Append a positive learning revision when durable evidence was omitted.

    This is intentionally narrower than a general reward editor: the original
    outcome must already be completed, every supplied reference must be a real
    file below a governance learning namespace, and the revision remains
    ineligible for production-policy promotion.  It repairs attribution; it
    does not manufacture a business success.
    """
    root = workspace_root(workspace)
    path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError, TypeError):
        rows = []
    prior = next((row for row in reversed(rows)
                  if str(row.get("trajectory_id") or "") == trajectory_id), None)
    if not prior:
        return {"ok": False, "status": "trajectory_not_found"}
    if str((prior.get("outcome") or {}).get("status") or "") != "completed":
        return {"ok": False, "status": "trajectory_not_completed"}
    allowed = (
        (root / "share/mind/governance/active_learning").resolve(),
        (root / "share/mind/governance/research_learning").resolve(),
    )
    verified: list[str] = []
    for value in evidence_refs:
        try:
            evidence = Path(str(value)).resolve()
            if evidence.is_file() and any(base in evidence.parents for base in allowed):
                verified.append(str(evidence))
        except (OSError, ValueError):
            continue
    verified = sorted(set(verified))
    if not verified or not str(learning_mechanism or "").strip():
        return {"ok": False, "status": "verified_learning_evidence_required"}
    prior_correction = dict(prior.get("correction") or {})
    if (prior_correction.get("kind") == "append_only_learning_evidence_attribution_correction"
            and sorted(prior_correction.get("evidence_refs") or []) == verified
            and str((prior.get("action") or {}).get("action_key") or "").endswith(
                ":" + str(learning_mechanism))
            and str((prior.get("action") or {}).get("strategy_id") or "") == strategy_id
            and str((prior.get("action") or {}).get("policy_arm") or "") == policy_arm):
        return {"ok": True, "status": "idempotent_learning_success_correction",
                "trajectory": prior, "path": str(path)}
    corrected = json.loads(json.dumps(prior))
    corrected["schema_version"] = max(3, int(corrected.get("schema_version") or 0))
    corrected["revision"] = int(prior.get("revision") or 1) + 1
    corrected["corrected_at"] = now_iso()
    corrected["correction"] = {
        "kind": "append_only_learning_evidence_attribution_correction",
        "evidence_refs": verified,
        "reason": "durable learning event evidence was omitted from terminal projection",
    }
    outcome = corrected.setdefault("outcome", {})
    outcome.update({
        "evidence_refs": verified,
        "learning_progress": True,
        "novel_evidence": True,
        "business_progress": False,
        "false_success": False,
    })
    corrected.setdefault("action", {})["action_key"] = (
        f"{corrected.get('instance_id')}:manual_project_iteration:{learning_mechanism}"
    )
    corrected["action"]["strategy_id"] = str(strategy_id)
    corrected["action"]["policy_arm"] = str(policy_arm)
    delivery = bool((corrected.get("state") or {}).get("delivery_confirmed"))
    corrected["reward_components"] = {
        "accepted_completed": 0.05,
        "artifact_contract": 0.0,
        "partial_artifact": 0.0,
        "delivery_contract": 0.05 if delivery else 0.0,
        "meaningful_event": 0.05,
        "business_progress": 0.0,
        "learning_progress": 0.30,
        "novel_evidence": 0.10,
        "handoff_consumed": 0.0,
    }
    corrected["reward"] = round(sum(corrected["reward_components"].values()), 4)
    corrected["policy_eligible"] = False
    corrected["learning_observation_eligible"] = True
    append_jsonl(path, corrected)
    return {"ok": True, "status": "learning_success_correction_appended",
            "trajectory": corrected, "path": str(path)}


def _has_delivery(item: Any) -> bool:
    return any(str(value).lower() == "delivery_confirmed=true" for value in item.evidence)


def _progress_signature(item: Any) -> str:
    return _evidence_value(item, "outcome_fingerprint") or _evidence_value(item, "progress_signature")


def _evidence_value(item: Any, key: str) -> str:
    prefix = f"{key}="
    return next((str(value).split("=", 1)[1] for value in reversed(item.evidence)
                 if str(value).startswith(prefix)), "")


def _evidence_bool(item: Any, key: str) -> bool:
    return _evidence_value(item, key).strip().lower() == "true"


def _handoff_consumed(item: Any) -> bool:
    for path in item.artifacts:
        if not str(path).endswith(".json"):
            continue
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if (payload.get("lineage") or {}).get("consumed") is True:
            return True
    return False


def _reward(item: Any, *, novel_evidence: bool = False) -> tuple[float, dict[str, float]]:
    parts: dict[str, float] = {}
    artifact_ok = (not item.requires_artifact) or bool(item.artifacts and all(Path(p).is_file() for p in item.artifacts))
    delivery_ok = (not item.requires_delivery) or _has_delivery(item)
    monitor_only = _evidence_bool(item, "monitor_only") or item.kind == "audit"
    business_progress = _evidence_bool(item, "business_progress") and not monitor_only
    if item.status == "completed":
        parts["accepted_completed"] = 0.05
    elif item.status == "blocked" and any(str(x).startswith("resume_event=") for x in item.evidence):
        parts["controlled_wait"] = 0.05
    elif item.status in {"failed", "blocked", "cancelled"}:
        parts["failed"] = -0.45
    parts["artifact_contract" if artifact_ok else "missing_artifact"] = 0.05 if artifact_ok else -0.18
    parts["delivery_contract" if delivery_ok else "missing_delivery"] = 0.05 if delivery_ok else -0.18
    meaningful = bool([event for event in item.event_types if event not in {"send_user_text", "push_files", "batch_plan"}])
    if meaningful:
        parts["meaningful_event"] = 0.05
    if business_progress:
        parts["business_progress"] = 0.45
    if business_progress and meaningful and novel_evidence:
        parts["novel_evidence"] = 0.20
    if business_progress and _handoff_consumed(item):
        parts["handoff_consumed"] = 0.15
    if monitor_only:
        parts["monitor_cost"] = -0.05
    if "watchdog" in str(item.blocked_reason).lower() or any(
        "timeout" in str(value).lower() or "lease expired" in str(value).lower() for value in item.evidence
    ):
        parts["watchdog_or_timeout"] = -0.25
    if int(item.attempt) > 1:
        parts["retry"] = -0.08 * (int(item.attempt) - 1)
    return round(max(-1.0, min(1.0, sum(parts.values()))), 4), parts


def _action_key(item: Any) -> str:
    meaningful = [event for event in item.event_types if event not in {"send_user_text", "push_files", "batch_plan"}]
    action = meaningful[0] if meaningful else "generic_or_unobserved"
    strategy = re.search(r"\[strategy_id=([^\]]+)\]", str(item.instruction or ""))
    if strategy:
        action = f"{action}:{strategy.group(1)}"
    return f"{item.instance_id}:{item.kind}:{action}"


def _instruction_marker(item: Any, key: str) -> str:
    match = re.search(rf"\[{re.escape(key)}=([^\]]+)\]", str(item.instruction or ""))
    return str(match.group(1)).strip() if match else ""


def _policy_eligible(item: Any, *, business_progress: bool, monitor_only: bool) -> bool:
    action = _action_key(item)
    return bool(
        item.instance_id in {"01", "02", "03", "04"}
        and item.kind == "project_iteration"
        and business_progress
        and not monitor_only
        and not any(value in action for value in ("write_design", "batch_plan", "generic_or_unobserved"))
    )


def _learning_observation_eligible(item: Any, *, monitor_only: bool) -> bool:
    """Whether an outcome is attributable enough to estimate an action.

    Promotion eligibility is deliberately stricter and still requires real
    business progress.  Failed/blocked executions of the same bounded action
    must nevertheless remain in its reward distribution; dropping them would
    train on survivors only and make a repeatedly failing action look perfect.
    """
    action = _action_key(item)
    return bool(
        item.instance_id in {"01", "02", "03", "04"}
        and item.kind == "project_iteration"
        and not monitor_only
        and not any(value in action for value in ("write_design", "batch_plan", "generic_or_unobserved"))
    )


def build_campaign_trajectories(workspace: str, campaign_id: str) -> dict[str, Any]:
    state = load_campaign(workspace, campaign_id)
    if not state:
        return {"ok": False, "status": "missing_campaign", "campaign_id": campaign_id}
    root = workspace_root(workspace)
    policy_dir = root / "share" / "mind" / "governance" / "experience_guided_policy"
    trajectory_log = policy_dir / "trajectories.jsonl"
    existing: set[str] = set()
    try:
        for line in trajectory_log.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            existing.add(str(row.get("trajectory_id") or ""))
    except (OSError, ValueError):
        pass
    created: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    try:
        for line in trajectory_log.read_text(encoding="utf-8").splitlines():
            old = json.loads(line)
            signature = str((old.get("outcome") or {}).get("outcome_fingerprint") or "")
            if signature:
                seen_signatures.add(signature)
    except (OSError, ValueError):
        pass
    for item in sorted(list_work_items(workspace, campaign_id), key=lambda value: value.created_at):
        if item.kind == "report" or item.status not in {"completed", "blocked", "failed", "cancelled"}:
            continue
        trajectory_id = "traj_" + hashlib.sha256(
            f"{campaign_id}|{item.work_item_id}|{item.updated_at}".encode("utf-8")
        ).hexdigest()[:16]
        if trajectory_id in existing:
            continue
        signature = _progress_signature(item)
        novel = bool(signature and signature not in seen_signatures)
        monitor_only = _evidence_bool(item, "monitor_only") or item.kind == "audit"
        business_progress = _evidence_bool(item, "business_progress") and not monitor_only
        reward, components = _reward(item, novel_evidence=novel)
        if signature:
            seen_signatures.add(signature)
        row = {
            "schema_version": 2,
            "trajectory_id": trajectory_id,
            "campaign_id": campaign_id,
            "work_item_id": item.work_item_id,
            "project_id": item.project_id,
            "instance_id": item.instance_id,
            "kind": item.kind,
            "state": {"attempt": item.attempt, "requires_artifact": item.requires_artifact,
                      "requires_delivery": item.requires_delivery},
            "action": {
                "action_key": _action_key(item), "event_types": item.event_types,
                "strategy_id": _instruction_marker(item, "strategy_id"),
                "policy_decision": _instruction_marker(item, "policy_decision"),
                "policy_arm": _instruction_marker(item, "policy_arm"),
            },
            "outcome": {"status": item.status, "blocked_reason": item.blocked_reason,
                        "artifacts": item.artifacts, "evidence": item.evidence,
                        "outcome_fingerprint": signature,
                        "monitor_only": monitor_only, "business_progress": business_progress,
                        "novel_evidence": novel and business_progress,
                        "handoff_consumed": _handoff_consumed(item)},
            "reward": reward,
            "reward_components": components,
            "policy_eligible": _policy_eligible(
                item, business_progress=business_progress, monitor_only=monitor_only,
            ),
            "learning_observation_eligible": _learning_observation_eligible(
                item, monitor_only=monitor_only,
            ),
            "created_at": now_iso(),
        }
        append_jsonl(trajectory_log, row)
        created.append(row)
    atomic_json(policy_dir / "reward_spec.json", REWARD_SPEC)
    return {"ok": True, "status": "recorded", "created": len(created),
            "trajectories": created, "path": str(trajectory_log)}


def update_candidate_policy(workspace: str) -> dict[str, Any]:
    root = workspace_root(workspace)
    policy_dir = root / "share" / "mind" / "governance" / "experience_guided_policy"
    rows: list[dict[str, Any]] = []
    try:
        rows = [json.loads(line) for line in (policy_dir / "trajectories.jsonl").read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        pass
    latest = {str(row.get("trajectory_id")): row for row in rows if isinstance(row, dict)}
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in latest.values():
        outcome = row.get("outcome") or {}
        action_key = str((row.get("action") or {}).get("action_key") or "")
        # Backward-compatible inference lets append-only v2 failure evidence
        # collected before this field existed immediately correct the policy.
        learnable = bool(
            row.get("learning_observation_eligible") is True
            or (
                str(row.get("instance_id") or "") in {"01", "02", "03", "04"}
                and row.get("kind") == "project_iteration"
                and outcome.get("monitor_only") is not True
                and not any(value in action_key for value in (
                    "write_design", "batch_plan", "generic_or_unobserved",
                ))
            )
        )
        if (int(row.get("schema_version") or 0) < 2
                or not learnable
                or is_synthetic_trajectory(row)):
            continue
        groups.setdefault(action_key or "unknown", []).append(row)
    actions = []
    gate = REWARD_SPEC["policy_gate"]
    for key, values in sorted(groups.items()):
        rewards = [effective_trajectory_reward(row) for row in values]
        mean = sum(rewards) / len(rewards)
        success_rate = sum(value > 0 for value in rewards) / len(rewards)
        # Conservative lower confidence score prevents a one-off success from
        # becoming the preferred action.
        lcb = mean - math.sqrt(2.0 * math.log(max(2, len(latest))) / len(rewards))
        eligible = (len(rewards) >= int(gate["minimum_samples"])
                    and mean >= float(gate["minimum_mean_reward"])
                    and success_rate >= float(gate["minimum_success_rate"]))
        actions.append({"action_key": key, "samples": len(rewards),
                        "negative_or_zero_samples": sum(value <= 0 for value in rewards),
                        "mean_reward": round(mean, 4),
                        "success_rate": round(success_rate, 4), "lower_confidence_bound": round(lcb, 4),
                        "eligible_for_canary": eligible})
    policy = {
        "schema_version": 1,
        "status": "candidate",
        "updated_at": now_iso(),
        "algorithm": "offline conservative contextual bandit",
        "trajectory_schema": 2,
        "eligibility_rule": (
            "bounded business project observations include attributable failures in action estimates; "
            "audits, reports, self-evolution and no-change are excluded; canary eligibility still requires gates"
        ),
        "automatic_production_promotion": False,
        "actions": sorted(actions, key=lambda row: row["lower_confidence_bound"], reverse=True),
        "next_step": "eligible actions still require an EvolutionExperiment and PromotionDecision",
    }
    atomic_json(policy_dir / "candidate_policy.json", policy)
    return {"ok": True, "status": "candidate", "policy": policy,
            "path": str(policy_dir / "candidate_policy.json")}


def run_offline_policy_learning_update(workspace: str, campaign_id: str) -> dict[str, Any]:
    trajectories = build_campaign_trajectories(workspace, campaign_id)
    if not trajectories.get("ok"):
        return trajectories
    policy = update_candidate_policy(workspace)
    from .policy_control import evaluate_canaries
    canary = evaluate_canaries(workspace)
    return {"ok": True, "status": "candidate_policy_updated", "campaign_id": campaign_id,
            "new_trajectories": trajectories["created"], "trajectory_path": trajectories["path"],
            "policy_path": policy["path"], "policy": policy["policy"], "canary": canary}


def evaluate_manual_evolution_evidence(workspace: str, *, project_id: str = "") -> dict[str, Any]:
    """Gate a 05 candidate experiment on heterogeneous manual task evidence.

    This function can create a candidate experiment, but never a promotion.
    Baseline/candidate intervention and canary evidence are still required by
    ``decide_experiment``.
    """
    root = workspace_root(workspace)
    path = root / "share" / "mind" / "governance" / "experience_guided_policy" / "trajectories.jsonl"
    rows: list[dict[str, Any]] = []
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        pass
    invalidated_receipts: set[str] = set()
    project_ids = {str(row.get("project_id") or "") for row in rows if isinstance(row, dict)}
    for candidate_project in project_ids:
        if not candidate_project:
            continue
        corrections = root / "share" / "projects" / candidate_project / "governance" / "receipt_corrections.jsonl"
        try:
            for line in corrections.read_text(encoding="utf-8").splitlines():
                correction = json.loads(line)
                receipt_id = str(correction.get("receipt_id") or "")
                if correction.get("action") == "invalidate" and receipt_id:
                    invalidated_receipts.add(receipt_id)
                elif correction.get("action") == "reinstate" and receipt_id:
                    invalidated_receipts.discard(receipt_id)
        except (OSError, ValueError, TypeError):
            pass
    samples = [row for row in rows if isinstance(row, dict)
               and row.get("kind") == "manual_project_iteration"
               and row.get("policy_eligible") is True
               and str((row.get("state") or {}).get("receipt_id") or "") not in invalidated_receipts
               and (not project_id or row.get("project_id") == project_id)]
    fingerprints = {str((row.get("outcome") or {}).get("outcome_fingerprint") or "") for row in samples}
    fingerprints.discard("")
    receipts = {str((row.get("state") or {}).get("receipt_id") or "") for row in samples}
    receipts.discard("")
    families = {str(value) for row in samples for value in ((row.get("state") or {}).get("source_families") or []) if str(value)}
    ready = len(samples) >= 3 and len(fingerprints) >= 3 and len(receipts) >= 3 and len(families) >= 2
    summary = {
        "samples": len(samples), "unique_outcomes": len(fingerprints),
        "unique_receipts": len(receipts), "source_families": sorted(families),
        "criteria": {
            "at_least_3_manual_samples": len(samples) >= 3,
            "at_least_3_unique_outcomes": len(fingerprints) >= 3,
            "at_least_3_receipts": len(receipts) >= 3,
            "at_least_2_source_families": len(families) >= 2,
        },
    }
    if not ready:
        return {"ok": True, "status": "insufficient_evidence", "promotion": False,
                "experiment_created": False, "summary": summary, "trajectory_path": str(path)}
    from .evolution_loop import record_issue, start_experiment

    target_project = project_id or str(samples[-1].get("project_id") or "manual_projects")
    issue = record_issue(workspace, {
        "summary": f"manual trajectories support a bounded strict-evidence candidate for {target_project}",
        "category": "verification", "severity": "medium",
        "evidence": [str(path), *[f"trajectory_id={row.get('trajectory_id')}" for row in samples[-3:]]],
        "instance_id": "05", "project_id": target_project,
    })
    issue_id = str((issue.get("issue") or {}).get("issue_id") or "")
    experiment = start_experiment(workspace, {
        "issue_id": issue_id,
        "hypothesis": "a complete-JSON and source-grounded evidence contract reduces false-success without reducing valid task completion",
        "intervention": "apply the strict evidence contract only to a bounded candidate arm; retain manual fail-closed baseline",
        "baseline": {"strategy_id": "manual_stable_grounded_v1", "samples": len(samples)},
        "success_criteria": [
            "at least 3 samples per arm", "0 false-success in candidate arm",
            "all cited evidence matches its named source", "full regression remains passing",
        ],
        "tests": ["tests/test_manual_stable_mode.py", "tests/test_manual_governance.py", "tests/test_policy_control.py"],
        "project_id": target_project,
    })
    return {"ok": bool(experiment.get("ok")), "status": "candidate_ready",
            "promotion": False, "experiment_created": bool(experiment.get("ok")),
            "summary": summary, "issue": issue.get("issue"),
            "experiment": experiment.get("experiment"), "trajectory_path": str(path)}
