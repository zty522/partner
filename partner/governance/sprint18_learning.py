"""Sprint 18 evidence-driven active-learning and self-evolution control plane.

The module is deliberately deterministic.  LLMs may help produce a report or
candidate patch, but they do not decide whether an observation is learnable,
which topic wins, how a posterior changes, or whether production is mutated.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .evolution_events import append_evolution_event, load_evolution_events
from .models import now_iso
from .storage import append_jsonl, atomic_json, workspace_root


SCHEMA_VERSION = 2
MIN_EXPLORATION_PROBABILITY = 0.10
MIN_INFORMATION_SCORE = 0.03


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                values.append(value)
    except (OSError, ValueError, TypeError):
        pass
    return values


def _digest(value: Any, width: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:width]


def _latest_trajectories(root: Path) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(_rows(root / "share/mind/governance/experience_guided_policy/trajectories.jsonl")):
        identity = str(row.get("trajectory_id") or f"anonymous_{index}")
        latest[identity] = row
    return list(latest.values())


def _failure_identity(row: dict[str, Any]) -> tuple[str, str]:
    outcome = dict(row.get("outcome") or {})
    mechanism = str(outcome.get("failure_mechanism") or "").strip()
    owner = str(outcome.get("failure_owner") or "").strip()
    truth = dict(outcome.get("truth_audit") or {})
    if not mechanism and truth and truth.get("passed") is False:
        mechanism = "claim_level_truth_gate"
        owner = owner or "verification"
    if not mechanism and outcome.get("false_success") is True:
        mechanism = "false_success"
        owner = owner or "verification"
    if not mechanism and str(outcome.get("status") or "") != "completed":
        mechanism = "unclassified_failed_outcome"
        owner = owner or "runtime"
    return owner, mechanism


def trajectory_to_observation(row: dict[str, Any]) -> dict[str, Any]:
    """Project one immutable trajectory into separate learning/promotion gates."""
    trajectory_id = str(row.get("trajectory_id") or "")
    if not trajectory_id:
        raise ValueError("trajectory_id is required")
    state = dict(row.get("state") or {})
    action = dict(row.get("action") or {})
    outcome = dict(row.get("outcome") or {})
    truth_audit = dict(outcome.get("truth_audit") or {})
    status = str(outcome.get("status") or "")
    false_success = bool(outcome.get("false_success"))
    truth_passed = bool(status == "completed" and not false_success
                        and (not truth_audit or truth_audit.get("passed") is True))
    safety_passed = str(outcome.get("failure_owner") or "") != "safety"
    evidence_refs = [str(value) for value in outcome.get("evidence_refs") or [] if str(value)]
    artifact_refs = [str(value) for value in outcome.get("artifacts") or [] if str(value)]
    auditable = bool(artifact_refs or evidence_refs or row.get("work_item_id"))
    # Negative outcomes are essential learning evidence when they are
    # auditable and safe.  They can never qualify for promotion.
    # Newer trajectories carry an explicit eligibility bit.  Honour an
    # explicit false so control-plane monitors never become EGPL samples, while
    # retaining the legacy default for older auditable negative trajectories.
    learning_eligible = bool(
        auditable and safety_passed
        and row.get("learning_observation_eligible") is not False
    )
    promotion_eligible = bool(
        learning_eligible and truth_passed
        and outcome.get("business_progress") is True
        and state.get("delivery_confirmed") is True
        and outcome.get("monitor_only") is not True
        and bool(state.get("receipt_id"))
    )
    owner, mechanism = _failure_identity(row)
    match_key = str(action.get("match_key") or "")
    temporal_window = match_key.split(":", 1)[0] if match_key else str(
        row.get("created_at") or "")[:10]
    observation_id = f"observation_{_digest({'trajectory': trajectory_id, 'schema': SCHEMA_VERSION})}"
    learning_polarity = (
        "neutral"
        if outcome.get("monitor_only") is True
        and row.get("learning_observation_eligible") is False
        else ("positive" if truth_passed else "negative")
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "observation_id": observation_id,
        "episode_id": str(row.get("episode_id") or ""),
        "trajectory_id": trajectory_id,
        "work_item_id": str(row.get("work_item_id") or ""),
        "project_id": str(row.get("project_id") or ""),
        "instance_id": str(row.get("instance_id") or ""),
        "temporal_window": temporal_window,
        "state_features": {
            "source_families": list(state.get("source_families") or []),
            "delivery_confirmed": bool(state.get("delivery_confirmed")),
            "continuation_requested": bool(state.get("continuation_requested")),
        },
        "action_key": str(action.get("action_key") or ""),
        "strategy_id": str(action.get("strategy_id") or ""),
        "policy_arm": str(action.get("policy_arm") or ""),
        "experiment_id": str(action.get("experiment_id") or ""),
        "match_key": match_key,
        "outcome": status,
        "reward": float(row.get("reward") or 0.0),
        "reward_vector": dict(row.get("reward_components") or {}),
        "failure_class": owner,
        "mechanism": mechanism,
        "evidence_refs": evidence_refs,
        "artifact_refs": artifact_refs,
        "truth_passed": truth_passed,
        "safety_passed": safety_passed,
        "delivery_confirmed": bool(state.get("delivery_confirmed")),
        "business_progress": bool(outcome.get("business_progress")),
        "false_success": false_success,
        "monitor_only": bool(outcome.get("monitor_only")),
        "learning_polarity": learning_polarity,
        "learning_eligible": learning_eligible,
        "promotion_eligible": promotion_eligible,
        "novelty_digest": str(outcome.get("outcome_fingerprint") or _digest(outcome, 24)),
        "source_created_at": str(row.get("created_at") or ""),
        "created_at": now_iso(),
    }


def robustness_event_to_observation(event: dict[str, Any]) -> dict[str, Any]:
    """Project a truthful rejected robustness Event into a negative sample."""
    if event.get("event_type") != "active_learning/robustness_evaluated":
        raise ValueError("unsupported learning event")
    event_id = str(event.get("event_id") or "")
    payload = dict(event.get("payload") or {})
    if not event_id or not str(payload.get("decision") or "").startswith("rejected"):
        raise ValueError("a rejected robustness event is required")
    evidence_refs = [str(value) for value in event.get("evidence_refs") or [] if str(value)]
    return {
        "schema_version": SCHEMA_VERSION,
        "observation_id": f"observation_{_digest({'event': event_id, 'schema': SCHEMA_VERSION})}",
        "source_event_id": event_id, "episode_id": "", "trajectory_id": "",
        "work_item_id": "", "project_id": str(event.get("project_id") or ""),
        "instance_id": "02", "temporal_window": str(event.get("occurred_at") or "")[:10],
        "state_features": {"source_families": ["targetdiff", "official_split"],
                           "delivery_confirmed": False, "continuation_requested": True},
        "action_key": "02:active_learning:targetdiff_active_robustness",
        "strategy_id": "targetdiff_active_acquisition_v4", "policy_arm": "candidate",
        "experiment_id": str(event.get("subject_id") or ""), "match_key": "three_seed_equal_budget",
        "outcome": str(payload.get("decision") or "rejected"), "reward": -0.2,
        "reward_vector": {"robustness_gate": -0.2},
        "failure_class": "generalization",
        "mechanism": "active_learning.targetdiff.acquisition_not_robust",
        "evidence_refs": evidence_refs, "artifact_refs": evidence_refs,
        # The experiment is truthful and safe; the tested action is the
        # negative.  Never conflate action failure with a truth-gate failure.
        "truth_passed": True, "safety_passed": True,
        "delivery_confirmed": False, "business_progress": False,
        "false_success": False, "monitor_only": False,
        "learning_polarity": "negative", "learning_eligible": bool(evidence_refs),
        "promotion_eligible": False,
        "novelty_digest": str(event.get("event_hash") or _digest(event, 24)),
        "source_created_at": str(event.get("occurred_at") or ""), "created_at": now_iso(),
    }


def uncertainty_diagnostic_event_to_observation(event: dict[str, Any]) -> dict[str, Any]:
    """Turn a post-hoc uncertainty diagnostic into an auditable learning fact."""
    if event.get("event_type") != "active_learning/uncertainty_diagnostic":
        raise ValueError("unsupported learning event")
    event_id = str(event.get("event_id") or "")
    payload = dict(event.get("payload") or {})
    decision = str(payload.get("decision") or "")
    evidence_refs = [str(value) for value in event.get("evidence_refs") or [] if str(value)]
    if not event_id or decision not in {"uncertainty_informative", "uncertainty_weak_or_miscalibrated"}:
        raise ValueError("valid uncertainty diagnostic event required")
    informative = decision == "uncertainty_informative"
    return {
        "schema_version": SCHEMA_VERSION,
        "observation_id": f"observation_{_digest({'event': event_id, 'schema': SCHEMA_VERSION})}",
        "source_event_id": event_id, "episode_id": "", "trajectory_id": "",
        "work_item_id": "", "project_id": str(event.get("project_id") or ""),
        "instance_id": "02", "temporal_window": str(event.get("occurred_at") or "")[:10],
        "state_features": {"source_families": ["targetdiff", "official_split", "uncertainty"],
                           "delivery_confirmed": False, "continuation_requested": True},
        "action_key": "02:active_learning:targetdiff_uncertainty_diagnostic",
        "strategy_id": "targetdiff_uncertainty_diagnostic_v1", "policy_arm": "shadow",
        "experiment_id": str(event.get("subject_id") or ""), "match_key": "three_seed_reliability",
        "outcome": decision, "reward": .10 if informative else -.10,
        "reward_vector": {"diagnostic_information": .10 if informative else -.10},
        "failure_class": "" if informative else "measurement",
        "mechanism": "" if informative else "active_learning.targetdiff.uncertainty_miscalibration",
        "evidence_refs": evidence_refs, "artifact_refs": evidence_refs,
        "truth_passed": True, "safety_passed": True, "delivery_confirmed": False,
        "business_progress": False, "false_success": False, "monitor_only": False,
        "learning_polarity": "positive" if informative else "negative",
        "learning_eligible": bool(evidence_refs), "promotion_eligible": False,
        "novelty_digest": str(event.get("event_hash") or _digest(event, 24)),
        "source_created_at": str(event.get("occurred_at") or ""), "created_at": now_iso(),
    }


def ingest_learning_observations(workspace: str) -> dict[str, Any]:
    root = workspace_root(workspace)
    directory = root / "share/mind/governance/active_learning/sprint18"
    ledger = directory / "observations.jsonl"
    existing_rows = _rows(ledger)
    latest_existing = {
        str(row.get("observation_id")): row
        for row in _latest_observations(ledger)
    }
    existing = set(latest_existing)
    added: list[dict[str, Any]] = []
    for trajectory in _latest_trajectories(root):
        try:
            observation = trajectory_to_observation(trajectory)
        except ValueError:
            continue
        previous = latest_existing.get(observation["observation_id"])
        if previous is not None:
            comparable = lambda value: {
                key: field for key, field in value.items()
                if key not in {"created_at", "revision", "correction"}
            }
            if comparable(previous) == comparable(observation):
                continue
            observation["revision"] = int(previous.get("revision") or 1) + 1
            observation["correction"] = {
                "kind": "latest_trajectory_projection",
                "trajectory_revision": int(trajectory.get("revision") or 1),
            }
        append_jsonl(ledger, observation)
        existing.add(observation["observation_id"])
        latest_existing[observation["observation_id"]] = observation
        added.append(observation)
    for event in load_evolution_events(str(root)):
        event_type = str(event.get("event_type") or "")
        try:
            if event_type == "active_learning/robustness_evaluated":
                observation = robustness_event_to_observation(event)
            elif event_type == "active_learning/uncertainty_diagnostic":
                observation = uncertainty_diagnostic_event_to_observation(event)
            else:
                continue
        except ValueError:
            continue
        if observation["observation_id"] in existing:
            continue
        append_jsonl(ledger, observation)
        existing.add(observation["observation_id"])
        added.append(observation)
    all_rows = _latest_observations(ledger)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "observations_ingested",
        "total": len(all_rows), "added": len(added),
        "learning_eligible": sum(row.get("learning_eligible") is True for row in all_rows),
        "promotion_eligible": sum(row.get("promotion_eligible") is True for row in all_rows),
        "positive": sum(row.get("learning_polarity") == "positive" for row in all_rows),
        "negative": sum(row.get("learning_polarity") == "negative" for row in all_rows),
        "updated_at": now_iso(), "ledger_path": str(ledger),
    }
    atomic_json(directory / "observation_summary.json", summary)
    return {"ok": True, **summary, "files": [str(ledger)]}


def _topic_key(observation: dict[str, Any]) -> str:
    mechanism = str(observation.get("mechanism") or "")
    if mechanism:
        return f"repair:{mechanism}"
    action = str(observation.get("action_key") or "unknown")
    return f"improve:{action.rsplit(':', 1)[-1]}"


def _latest_observations(path: Path) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(_rows(path)):
        key = str(row.get("trajectory_id") or row.get("observation_id") or index)
        previous = latest.get(key)
        if previous is None or int(row.get("schema_version") or 0) >= int(
                previous.get("schema_version") or 0):
            latest[key] = row
    return list(latest.values())


def rank_learning_topics(workspace: str) -> list[dict[str, Any]]:
    root = workspace_root(workspace)
    directory = root / "share/mind/governance/active_learning/sprint18"
    observations = [row for row in _latest_observations(directory / "observations.jsonl")
                    if row.get("learning_eligible") is True
                    and row.get("monitor_only") is not True]
    prior_decisions = _rows(directory / "topic_selections.jsonl")
    selected_counts: dict[str, int] = defaultdict(int)
    seen_decisions: set[tuple[str, str]] = set()
    for row in prior_decisions:
        topic = str(row.get("topic_key") or "")
        evidence_digest = str(row.get("evidence_digest") or row.get("decision_id") or "")
        identity = (topic, evidence_digest)
        if identity in seen_decisions:
            continue
        seen_decisions.add(identity)
        selected_counts[topic] += 1
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        groups[_topic_key(row)].append(row)
    ranked: list[dict[str, Any]] = []
    for topic_key, rows in groups.items():
        successes = sum(row.get("learning_polarity") == "positive" for row in rows)
        failures = len(rows) - successes
        alpha, beta = 1 + successes, 1 + failures
        mean = alpha / (alpha + beta)
        uncertainty = 4.0 * mean * (1.0 - mean)
        information_gain = uncertainty * math.log1p(len(rows)) / math.log(2 + len(rows))
        projects = {str(row.get("project_id") or "") for row in rows if row.get("project_id")}
        business_value = max(0.2, sum(1.0 if row.get("business_progress") else .65 for row in rows) / len(rows))
        transfer_value = min(1.0, .5 + .25 * len(projects))
        failure_signal = min(1.0, failures / max(1, len(rows))) if failures else .20
        novelty = 1.0 / (1.0 + selected_counts.get(topic_key, 0))
        event_count = sum(len((row.get("reward_vector") or {})) for row in rows) / max(1, len(rows))
        execution_cost = min(.25, .01 * event_count)
        safety_risk = .5 if any(row.get("safety_passed") is False for row in rows) else 0.0
        repetition_penalty = min(.6, .15 * selected_counts.get(topic_key, 0))
        score = (information_gain * uncertainty * business_value * transfer_value
                 * failure_signal * novelty
                 - execution_cost - safety_risk - repetition_penalty)
        evidence = sorted({str(row.get("trajectory_id") or row.get("source_event_id") or
                               row.get("observation_id") or "") for row in rows})
        critic_reasons: list[str] = []
        if not failures and mean > .8:
            critic_reasons.append("too_easy_no_failure_signal")
        if selected_counts.get(topic_key, 0) >= 3:
            critic_reasons.append("repeated_without_new_evidence")
        if safety_risk:
            critic_reasons.append("safety_failure_requires_human_boundary")
        ranked.append({
            "topic_key": topic_key, "sample_count": len(rows),
            "successes": successes, "failures": failures, "projects": sorted(projects),
            "posterior": {"alpha": alpha, "beta": beta, "mean": mean},
            "score_components": {
                "expected_information_gain": information_gain,
                "uncertainty": uncertainty, "business_value": business_value,
                "transfer_value": transfer_value, "failure_signal": failure_signal,
                "novelty": novelty,
                "execution_cost": execution_cost, "safety_risk": safety_risk,
                "repetition_penalty": repetition_penalty,
            },
            "acquisition_score": score,
            "critic": {"passed": not critic_reasons, "reasons": critic_reasons},
            "evidence_trajectory_ids": evidence,
        })
    ranked.sort(key=lambda row: (-float(row["acquisition_score"]), row["topic_key"]))
    return ranked


def select_learning_topic(workspace: str) -> dict[str, Any]:
    root = workspace_root(workspace)
    directory = root / "share/mind/governance/active_learning/sprint18"
    ranked = rank_learning_topics(str(root))
    selected = next((row for row in ranked
                     if row["critic"]["passed"]
                     and row["acquisition_score"] >= MIN_INFORMATION_SCORE), None)
    status = "topic_selected" if selected else "waiting_evidence"
    identity = _digest({"ranked": ranked, "status": status})
    decision = {
        "schema_version": SCHEMA_VERSION, "decision_id": f"topic_selection_{identity}",
        "status": status, "created_at": now_iso(), "ranked_topics": ranked[:20],
        "topic_key": str((selected or {}).get("topic_key") or ""),
        "selected": selected, "minimum_score": MIN_INFORMATION_SCORE,
        "production_mutation": False,
    }
    output = directory / "topic_decisions" / f"{decision['decision_id']}.json"
    atomic_json(output, decision)
    # The summary ledger makes selection frequency and repetition penalties
    # reconstructible without scanning mutable latest-state files.
    append_jsonl(directory / "topic_selections.jsonl", {
        "decision_id": decision["decision_id"], "status": status,
        "topic_key": decision["topic_key"], "created_at": decision["created_at"],
        "evidence_digest": _digest((selected or {}).get("evidence_trajectory_ids") or []),
    })
    event_type = "active_learning/topic_selected" if selected else "active_learning/waiting_evidence"
    event = append_evolution_event(
        str(root), event_type, subject_id=decision["decision_id"],
        project_id="agent_self_evolution",
        payload={"status": status, "topic_key": decision["topic_key"],
                 "acquisition_score": (selected or {}).get("acquisition_score"),
                 "production_mutation": False},
        evidence_refs=[str(output)], idempotency_key=f"s18-topic:{decision['decision_id']}",
    )
    return {"ok": True, "status": status, "decision": decision,
            "path": str(output), "files": [str(output)], "event": event,
            "production_mutation": False}


REPAIR_RECIPES: tuple[dict[str, Any], ...] = (
    {"recipe_id": "recipe_claim_ledger_owner_v1", "match": ("claim", "truth"), "level": "R1",
     "allowed_surface": ["deterministic_postprocessor", "prompt_contract"],
     "intervention": "replace model-authored ledger with machine-verified source/quote ledger"},
    {"recipe_id": "recipe_typed_output_reference_v1", "match": ("output_reference", "reference"), "level": "R1",
     "allowed_surface": ["event_parameters", "deterministic_postprocessor"],
     "intervention": "resolve typed dependency outputs inside the task working directory"},
    {"recipe_id": "recipe_channel_ack_v1", "match": ("delivery", "ack"), "level": "R1",
     "allowed_surface": ["event_parameters", "delivery_observer"],
     "intervention": "separate file upload from progress/final message acknowledgement"},
    {"recipe_id": "recipe_bounded_timeout_v1", "match": ("timeout", "network", "rate_limit"), "level": "R0",
     "allowed_surface": ["timeout", "retry_budget", "backoff"],
     "intervention": "change one bounded call budget without duplicate concurrent retry"},
    {"recipe_id": "recipe_terminal_persistence_v1", "match": ("unclosed", "terminal", "skipped"), "level": "R2",
     "allowed_surface": ["single_allowlisted_handler", "targeted_tests"],
     "intervention": "persist exactly one authoritative terminal for every started step"},
    {"recipe_id": "recipe_uncertainty_calibration_probe_v1",
     "match": ("not_robust", "robustness", "acquisition"), "level": "R0",
     "allowed_surface": ["uncertainty_calibration", "density_slice", "acquisition_parameters"],
     "intervention": "calibrate uncertainty and test density/error-slice conditioning across frozen pool seeds"},
    {"recipe_id": "recipe_uncertainty_estimator_comparison_v1",
     "match": ("uncertainty_miscalibration", "uncertainty_weak"), "level": "R0",
     "allowed_surface": ["offline_uncertainty_estimator", "readonly_benchmark"],
     "intervention": "matched-compare raw tree variance with labelled-only cross-fitted residual uncertainty"},
    {"recipe_id": "recipe_diagnostic_probe_v1", "match": (), "level": "R0",
     "allowed_surface": ["readonly_probe"],
     "intervention": "collect a minimal causal trace before proposing mutation"},
)


def choose_repair_recipe(topic_key: str) -> dict[str, Any]:
    lowered = str(topic_key or "").lower()
    for recipe in REPAIR_RECIPES:
        if recipe["match"] and any(token in lowered for token in recipe["match"]):
            return dict(recipe)
    return dict(REPAIR_RECIPES[-1])


def propose_candidate_for_topic(workspace: str, decision_path: str) -> dict[str, Any]:
    root = workspace_root(workspace)
    path = Path(decision_path).resolve()
    allowed = (root / "share/mind/governance/active_learning/sprint18/topic_decisions").resolve()
    if not path.is_relative_to(allowed) or not path.is_file():
        return {"ok": False, "status": "invalid_topic_decision", "production_mutation": False}
    decision = json.loads(path.read_text(encoding="utf-8"))
    selected = dict(decision.get("selected") or {})
    if decision.get("status") != "topic_selected" or not selected:
        return {"ok": False, "status": "no_selected_topic", "production_mutation": False}
    topic_key = str(selected.get("topic_key") or "")
    recipe = choose_repair_recipe(topic_key)
    candidate_id = f"candidate_s18_{_digest({'decision': decision['decision_id'], 'recipe': recipe['recipe_id']})}"
    level = str(recipe["level"])
    diagnosis = {
        "schema_version": SCHEMA_VERSION,
        "diagnosis_id": f"diagnosis_s18_{_digest(selected)}",
        "topic_key": topic_key,
        "failure_class": topic_key.split(":", 1)[0],
        "mechanism": topic_key.split(":", 1)[-1],
        "causal_evidence": list(selected.get("evidence_trajectory_ids") or []),
        "confidence": float((selected.get("posterior") or {}).get("mean") or 0.5),
        "counter_hypothesis": "the observed failure may be environment- or delivery-specific",
        "created_at": now_iso(),
    }
    bundle = {
        "schema_version": SCHEMA_VERSION, "candidate_id": candidate_id,
        "topic_decision_id": decision["decision_id"], "diagnosis": diagnosis,
        "recipe": recipe,
        "hypothesis": f"{recipe['intervention']} improves {topic_key} under a frozen matched task",
        "unchanged_contracts": ["Event-first", "manual_stable default", "truth gate", "safety gate"],
        "targeted_tests": ["tests/test_sprint18_learning.py"],
        "matched_requirements": ["same task", "same model", "same sources", "same budget", "same truth gate"],
        "rollback": {"action": "discard candidate projection", "production_state_unchanged": True},
        "execution_authorized": level in {"R0", "R1"},
        "requires_human_approval": level in {"R3", "R4"},
        "production_mutation": False, "production_effective": False,
        "status": "candidate_proposed", "created_at": now_iso(),
    }
    directory = root / "share/mind/governance/active_learning/sprint18/candidates"
    output = directory / f"{candidate_id}.json"
    atomic_json(output, bundle)
    event = append_evolution_event(
        str(root), "active_learning/candidate_bundled", subject_id=candidate_id,
        project_id="agent_self_evolution",
        payload={"candidate_id": candidate_id, "topic_key": topic_key,
                 "recipe_id": recipe["recipe_id"], "repair_level": level,
                 "execution_authorized": bundle["execution_authorized"],
                 "production_effective": False},
        evidence_refs=[str(path), str(output)], idempotency_key=f"s18-candidate:{candidate_id}",
    )
    return {"ok": True, "status": "candidate_proposed", "candidate": bundle,
            "path": str(output), "files": [str(output)], "event": event,
            "production_mutation": False, "production_effective": False}


def update_learning_policy(workspace: str) -> dict[str, Any]:
    """Update auditable action posteriors; never project them to production."""
    root = workspace_root(workspace)
    directory = root / "share/mind/governance/active_learning/sprint18"
    observations = [row for row in _latest_observations(directory / "observations.jsonl")
                    if row.get("learning_eligible") is True]
    previous_path = directory / "learning_policy.json"
    try:
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        previous = {"posteriors": {}}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        context = str(row.get("mechanism") or row.get("action_key") or "unknown")
        action = str(row.get("strategy_id") or row.get("policy_arm") or "unknown")
        groups[f"{context}|{action}"].append(row)
    posteriors: dict[str, dict[str, Any]] = {}
    contexts: dict[str, list[str]] = defaultdict(list)
    for key, rows in groups.items():
        success = sum(row.get("learning_polarity") == "positive" for row in rows)
        failure = len(rows) - success
        alpha, beta = 1 + success, 1 + failure
        context, _, action = key.partition("|")
        posteriors[key] = {"alpha": alpha, "beta": beta,
                           "mean": alpha / (alpha + beta), "samples": len(rows),
                           "successes": success, "failures": failure}
        contexts[context].append(action)
    probabilities: dict[str, dict[str, float]] = {}
    for context, actions in contexts.items():
        raw = {action: posteriors[f"{context}|{action}"]["mean"] for action in sorted(set(actions))}
        total = sum(raw.values()) or 1.0
        normalized = {action: value / total for action, value in raw.items()}
        if len(normalized) > 1:
            normalized = {action: max(MIN_EXPLORATION_PROBABILITY, value)
                          for action, value in normalized.items()}
            scale = sum(normalized.values())
            normalized = {action: value / scale for action, value in normalized.items()}
        probabilities[context] = normalized
    changed = probabilities != dict(previous.get("selection_probabilities") or {})
    policy = {
        "schema_version": SCHEMA_VERSION, "policy_id": f"learning_policy_{_digest(posteriors)}",
        "updated_at": now_iso(), "observation_count": len(observations),
        "posteriors": posteriors, "selection_probabilities": probabilities,
        "prior_policy_id": previous.get("policy_id"), "selection_changed": changed,
        "minimum_exploration_probability": MIN_EXPLORATION_PROBABILITY,
        "production_effective": False, "production_mutation": False,
    }
    atomic_json(previous_path, policy)
    append_jsonl(directory / "policy_updates.jsonl", policy)
    event = append_evolution_event(
        str(root), "active_learning/policy_updated", subject_id=policy["policy_id"],
        project_id="agent_self_evolution",
        payload={"policy_id": policy["policy_id"], "observation_count": len(observations),
                 "selection_changed": changed, "production_effective": False},
        evidence_refs=[str(directory / "observations.jsonl"), str(previous_path)],
        idempotency_key=f"s18-policy:{policy['policy_id']}",
    )
    return {"ok": True, "status": "learning_policy_updated", "policy": policy,
            "path": str(previous_path), "files": [str(previous_path)], "event": event,
            "production_mutation": False, "production_effective": False}


def run_sprint18_learning_cycle(workspace: str) -> dict[str, Any]:
    ingestion = ingest_learning_observations(workspace)
    selection = select_learning_topic(workspace)
    candidate: dict[str, Any] = {"ok": False, "status": "not_proposed"}
    if selection.get("status") == "topic_selected":
        candidate = propose_candidate_for_topic(workspace, str(selection.get("path") or ""))
    policy = update_learning_policy(workspace)
    status = "candidate_ready" if candidate.get("ok") else "waiting_evidence"
    return {"ok": True, "status": status, "ingestion": ingestion,
            "selection": selection, "candidate": candidate, "policy": policy,
            "production_mutation": False, "production_effective": False}
