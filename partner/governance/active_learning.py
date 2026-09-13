"""Episode-grounded active experiment selection for Agent self-evolution."""
from __future__ import annotations

import hashlib
import json
import asyncio
import logging
import re

logger = logging.getLogger(__name__)
from collections import Counter
from pathlib import Path
from typing import Any

from partner.cognition.active_learning import (
    ActiveLearningMemory,
    ActiveLearningOption,
    rank_active_learning_options,
)

from .evolution_events import append_evolution_event
from .models import now_iso
from .storage import append_jsonl, atomic_json, workspace_root


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _llm_self_evolution_diagnosis(payload: dict[str, Any], *, root: Path) -> dict[str, Any]:
    """Three-pass causal diagnosis over Episode, source and project evidence."""
    try:
        from partner.adapters.direct_api import chat
        from .deep_context import build_deep_context_pack, render_deep_context
        failure_class = str(payload.get("failure_class") or "")
        relevant_code = [
            "partner/governance/active_learning.py",
            "partner/governance/manual_runtime.py",
            "partner/mind/executor.py",
        ]
        if "molecular" in json.dumps(payload, ensure_ascii=False).lower() or "algorithm_spec" in json.dumps(payload):
            relevant_code.extend([
                "partner/v2/molecular_iteration_events.py",
                "partner/planner/batch_planner.py",
            ])
        pack = build_deep_context_pack(
            root,
            project_id=str(payload.get("project_id") or "agent_self_evolution"),
            purpose="self_evolution_causal_diagnosis",
            instance_id=str(payload.get("instance_id") or "05"),
            task_id=str(payload.get("task_id") or ""),
            episode_refs=[str(value) for value in payload.get("evidence_refs") or []],
            relevant_code=relevant_code,
            max_chars=110000,
            privacy_mode="derived_only",
        )
        model_payload = dict(payload)
        raw_refs = [str(value) for value in model_payload.pop("evidence_refs", [])]
        model_payload["evidence_ref_hashes"] = [
            hashlib.sha256(value.encode()).hexdigest()[:16] for value in raw_refs
        ]
        evidence = (
            "机器摘要=" + json.dumps(model_payload, ensure_ascii=False)
            + "\n深上下文=" + render_deep_context(pack)
        )
        roles = (
            ("self_evolution_evidence_audit", 7000,
             "只重建失败事实，不提修复。输出 JSON：observed_failure、timeline、source_findings、"
             "missing_evidence、business_vs_partner_boundary。"),
            ("self_evolution_counterfactual", 8000,
             "提出至少三个竞争性根因并设计区分它们的反事实。输出 JSON：causal_hypotheses、"
             "disconfirming_evidence、counterfactual_tests、reward_hacking_risks。"),
            ("self_evolution_causal_diagnosis", 9000,
             "综合前两遍，只输出 JSON：causal_mechanism、disconfirming_evidence、minimal_experiment、"
             "recommended_next_option、confidence、evidence_refs_used、code_locations、unknowns。"),
        )
        drafts: list[dict[str, Any]] = []
        raw = ""
        for purpose, budget, instruction in roles:
            prompt = (
                "你是 Partner 内部自进化诊断员，不是在做外部知识主动学习。不能批准生产，也不能把业务"
                "假设被否证冒充框架错误。recommended_next_option 只能是 bounded_repair、matched_resample、"
                "collect_more_matched_evidence、collect_episode_task_log_evidence、"
                "split_failure_class_by_step_type、split_failure_class_by_outcome_reason。"
                + instruction + "\n" + evidence
            )
            if drafts:
                prompt += "\n前序审议=" + json.dumps(drafts, ensure_ascii=False)
            raw = chat(
                prompt, purpose=purpose, max_tokens=budget,
                temperature=0.15, timeout=120, workspace=str(root),
                instance_id=str(payload.get("instance_id") or payload.get("source_instance") or "05"),
                project_id="agent_self_evolution",
                task_id=str(payload.get("task_id") or ""),
                episode_id=str(payload.get("episode_id") or ""),
                event_type="agent_active_learning_diagnostic_shadow",
            )
            cleaned = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S | re.I)
            decoder = json.JSONDecoder()
            parsed: list[tuple[int, dict[str, Any]]] = []
            for match in re.finditer(r"\{", cleaned):
                try:
                    item, end = decoder.raw_decode(cleaned[match.start():])
                except (TypeError, ValueError):
                    continue
                if isinstance(item, dict):
                    parsed.append((end, item))
            value = max(parsed, key=lambda item: item[0])[1] if parsed else {}
            drafts.append(value)
        value = dict(drafts[-1]) if drafts else {}
        if not isinstance(value, dict):
            return {}
        allowed = {
            "bounded_repair", "matched_resample", "collect_more_matched_evidence",
            "collect_episode_task_log_evidence", "split_failure_class_by_step_type",
            "split_failure_class_by_outcome_reason",
        }
        if str(value.get("recommended_next_option") or "") not in allowed:
            value.pop("recommended_next_option", None)
        value["deliberation_passes"] = [purpose for purpose, _budget, _instruction in roles]
        value["model_calls"] = len(roles)
        value["deep_context_manifest"] = pack["manifest_path"]
        value["deep_context_chars"] = pack["manifest"]["context_chars"]
        value["deep_context_coverage"] = pack["manifest"]["coverage"]
        return value
    except Exception as exc:
        return {"error": type(exc).__name__}


def _audit_causal_diagnosis(*, failure_class: str, episodes: list[dict[str, Any]],
                            llm_diagnosis: dict[str, Any]) -> dict[str, Any]:
    """Keep a model's causal story inside what the persisted Episode proves."""
    mechanism = str(llm_diagnosis.get("causal_mechanism") or "")
    serialized = json.dumps(episodes, ensure_ascii=False)
    audit = {
        "schema_version": 1,
        "llm_diagnosis_is_hypothesis": True,
        "episode_supports_failure_class": bool(failure_class and failure_class in serialized),
        "episode_supports_parameter_provenance": False,
        "unsupported_causal_claims": [],
        "verified_mechanism": "",
        "accepted_for_candidate_design": False,
    }
    # A terminal error can prove the missing contract at the handler boundary,
    # but without an input snapshot it cannot distinguish generation,
    # serialization and dispatch loss.  This was the exact overclaim exposed
    # by the 02 DSL failure.
    if "algorithm_spec" in mechanism or "algorithm_spec" in serialized:
        audit["verified_mechanism"] = (
            "llm_greedy_dsl reached the Event handler without a valid algorithm_spec; "
            "the persisted Episode does not identify whether generation, parsing, serialization, "
            "or dispatch caused the absence"
        )
        provenance_claim = any(token in mechanism.lower() for token in (
            "dispatcher", "planner", "透传", "剥掉", "未计算", "调用方"))
        if provenance_claim:
            audit["unsupported_causal_claims"].append(
                "the Episode has no parameter-provenance snapshot, so dispatcher/planner attribution is unverified")
        audit["accepted_for_candidate_design"] = True
    elif mechanism and audit["episode_supports_failure_class"]:
        audit["verified_mechanism"] = "failure class is observed; the proposed causal mechanism remains unverified"
    return audit


def _state_failure_classes(state: dict[str, Any]) -> list[str]:
    """Return explicit classes plus the single governed outcome fallback."""
    classes = [str(value) for value in state.get("failure_classes") or [] if str(value)]
    if state.get("status") == "failed" and not classes:
        classes = ["outcome.no_business_progress"]
    return classes


def _semantic_preflight_mechanisms(state: dict[str, Any]) -> list[str]:
    """Map structured preflight errors to contract-level repair hypotheses."""
    errors = [str(row.get("error") or "").lower()
              for row in state.get("failure_details") or []
              if isinstance(row, dict)
              and row.get("class") == "planning.semantic_preflight"]
    mechanisms: set[str] = set()
    for error in errors:
        if any(token in error for token in (
                "read input is missing or outside allowed roots", "requires path",
                "only lists the current task directory")):
            mechanisms.add("input_path_contract")
        if any(token in error for token in (
                "evidence-dependent output must reference", "unfilled template",
                "short placeholder", "output content is empty",
                "needs a synthesis step")):
            mechanisms.add("evidence_output_contract")
        if "event_type" in error and ("not allowed" in error or "not registered" in error):
            mechanisms.add("event_contract")
        if "expected_artifacts require" in error:
            mechanisms.add("artifact_contract")
    return sorted(mechanisms)


def _failure_evidence(root: Path, instance_ids: set[str]) -> tuple[list[dict[str, Any]], str]:
    episodes_root = root / "share/mind/governance/episodes"
    rows, sources = [], []
    for path in sorted(episodes_root.glob("*/state.json")):
        state = _json(path)
        if str(state.get("instance_id") or "") not in instance_ids:
            continue
        raw = path.read_bytes()
        classes = _state_failure_classes(state)
        for failure_class in classes:
            rows.append({"episode_id": state.get("episode_id"),
                         "instance_id": state.get("instance_id"),
                         "failure_class": failure_class, "path": str(path)})
        sources.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
    taxonomy_index = _json(root / "share/mind/governance/active_learning/taxonomy_index.json")
    by_parent = dict(taxonomy_index.get("by_parent") or {})
    if not by_parent and taxonomy_index.get("latest_path"):
        legacy = _json(Path(str(taxonomy_index["latest_path"])))
        if legacy.get("parent_failure_class"):
            by_parent[str(legacy["parent_failure_class"])] = str(taxonomy_index["latest_path"])
    taxonomies = [_json(Path(str(value))) for value in by_parent.values() if str(value)]
    taxonomies = [value for value in taxonomies if value.get("status") == "candidate_taxonomy"]
    for taxonomy in sorted(taxonomies, key=lambda value: str(value.get("parent_failure_class") or "")):
        parent = str(taxonomy.get("parent_failure_class") or "")
        assignments = dict(taxonomy.get("episode_assignments") or {})
        refined = []
        for row in rows:
            children = assignments.get(str(row.get("episode_id") or ""), []) if row["failure_class"] == parent else []
            if children:
                refined.extend({**row, "failure_class": str(child),
                                "parent_failure_class": parent} for child in children)
            else:
                refined.append(row)
        rows = refined
    digest = hashlib.sha256(json.dumps({"sources": sources,
                                        "taxonomy_ids": sorted(str(value.get("taxonomy_id") or "")
                                                               for value in taxonomies)}, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    return rows, digest


def _prior(failure_class: str, count: int) -> dict[str, float]:
    transient_hint = any(token in failure_class for token in
                         ("timeout", "network", "delivery", "rate_limit"))
    systematic = 0.35 if transient_hint else 0.60
    systematic = min(0.92, systematic + 0.08 * max(0, count - 1))
    return {"systematic_failure": systematic, "transient_failure": 1.0 - systematic}


def _latest_diagnosis(root: Path, failure_class: str) -> tuple[dict[str, Any], str]:
    matches: list[tuple[float, dict[str, Any], str]] = []
    for path in (root / "share/mind/governance/active_learning/diagnoses").glob("*.json"):
        value = _json(path)
        if str(value.get("failure_class") or "") != failure_class:
            continue
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        matches.append((modified, value, str(path)))
    if not matches:
        return {}, ""
    _, value, path = sorted(matches, key=lambda row: (row[0], row[2]))[-1]
    return value, path


def _options(context_key: str, memory: ActiveLearningMemory,
             evidence_refs: tuple[str, ...], prior: dict[str, float],
             diagnosis_path: str = "") -> list[ActiveLearningOption]:
    expected_repair = (prior.get("systematic_failure", 0.0) * .80
                       + prior.get("transient_failure", 0.0) * .35)
    expected_resample = (prior.get("systematic_failure", 0.0) * .20
                         + prior.get("transient_failure", 0.0) * .78)
    repair_value = .5 * expected_repair + .5 * memory.success_probability(
        context_key, "bounded_repair")
    resample_value = .5 * expected_resample + .5 * memory.success_probability(
        context_key, "matched_resample")
    diagnosed = bool(diagnosis_path)
    options = [
        ActiveLearningOption(
            "diagnose_failure", "agent_active_learning_diagnostic_shadow",
            {"systematic_failure": {"diagnosed_systematic": .85, "diagnosed_transient": .15},
             "transient_failure": {"diagnosed_systematic": .15, "diagnosed_transient": .85}},
            task_value=.85, novelty=.15 if diagnosed else 1.0, cost=.04, risk=.01,
            params={"context_key": context_key}, evidence_refs=evidence_refs,
        ),
        ActiveLearningOption(
            "bounded_repair", ("agent_active_learning_skipped_terminal_repair_shadow"
                               if diagnosis_path else "agent_active_learning_repair_candidate"),
            {"systematic_failure": {"resolved": .80, "unresolved": .20},
             "transient_failure": {"resolved": .35, "unresolved": .65}},
            task_value=repair_value, novelty=.9,
            cost=.005 if diagnosis_path else .12, risk=.003 if diagnosis_path else .08,
            params={"context_key": context_key, "diagnosis_path": diagnosis_path},
            evidence_refs=evidence_refs,
        ),
        ActiveLearningOption(
            "matched_resample", "agent_active_learning_resample_candidate",
            {"systematic_failure": {"resolved": .20, "unresolved": .80},
             "transient_failure": {"resolved": .78, "unresolved": .22}},
            task_value=resample_value, novelty=.75, cost=.08, risk=.03,
            params={"context_key": context_key}, evidence_refs=evidence_refs,
        ),
    ]
    return options


def select_agent_experiment(workspace: str, *, instance_ids: list[str] | None = None,
                            focus_failure_class: str = "") -> dict[str, Any]:
    """Select—but do not execute—the next informative Agent experiment."""
    root = workspace_root(workspace)
    ids = {str(value) for value in (instance_ids or ["03", "05"])}
    rows, evidence_digest = _failure_evidence(root, ids)
    counts = Counter(row["failure_class"] for row in rows)
    if not counts:
        return {"ok": False, "status": "insufficient_failure_evidence",
                "production_mutation": False, "episode_evidence_digest": evidence_digest}
    focus = str(focus_failure_class or "").strip()
    eligible = counts
    if focus:
        eligible = Counter({key: value for key, value in counts.items()
                            if key == focus or key.startswith(focus + "/")})
        if not eligible:
            return {"ok": False, "status": "focus_failure_class_not_found",
                    "focus_failure_class": focus, "failure_counts": dict(counts),
                    "production_mutation": False, "episode_evidence_digest": evidence_digest}
    target, count = sorted(eligible.items(), key=lambda row: (-row[1], row[0]))[0]
    evidence_refs = tuple(sorted({row["path"] for row in rows if row["failure_class"] == target}))
    memory_path = root / "share/mind/governance/active_learning/memory.json"
    memory = ActiveLearningMemory.from_dict(_json(memory_path))
    prior = _prior(target, count)
    diagnosis, diagnosis_path = _latest_diagnosis(root, target)
    classification = str(diagnosis.get("classification") or "")
    confidence = float(diagnosis.get("confidence") or 0.0)
    if classification == "systematic_failure":
        prior = {"systematic_failure": confidence,
                 "transient_failure": max(0.0, 1.0 - confidence)}
    elif classification == "transient_failure":
        prior = {"systematic_failure": max(0.0, 1.0 - confidence),
                 "transient_failure": confidence}
    ranked = rank_active_learning_options(
        prior, _options(target, memory, evidence_refs, prior, diagnosis_path))
    selector_version = 2
    identity = hashlib.sha256(json.dumps({"digest": evidence_digest, "target": target,
                                          "memory": memory.to_dict(),
                                          "selector_version": selector_version}, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()[:16]
    decision_id = f"active_query_{identity}"
    decision = {
        "schema_version": 1, "decision_id": decision_id,
        "selector_version": selector_version,
        "mode": "agent_active_learning_shadow", "created_at": now_iso(),
        "instances": sorted(ids), "episode_evidence_digest": evidence_digest,
        "episode_count_with_failure": len({row["episode_id"] for row in rows}),
        "failure_counts": dict(counts), "target_failure_class": target,
        "focus_failure_class": focus or None,
        "hypothesis_prior": prior, "ranked_options": ranked,
        "latest_diagnosis_id": diagnosis.get("diagnosis_id"),
        "latest_diagnosis_path": diagnosis_path or None,
        "selected_option": ranked[0],
        "selection_rule": "information_gain * task_value * novelty - cost - risk",
        "execution_authorized": False, "production_mutation": False,
        "next_step": "bind the selected proposal to an explicit Event-first matched Experiment before execution",
    }
    output = root / "share/mind/governance/active_learning/decisions" / f"{decision_id}.json"
    atomic_json(output, decision)
    event = append_evolution_event(
        str(root), "active_learning/query_proposed", subject_id=decision_id,
        project_id="agent_self_evolution",
        payload={"decision_id": decision_id, "target_failure_class": target,
                 "selected_option": ranked[0]["option_id"],
                 "expected_information_gain": ranked[0]["expected_information_gain"],
                 "execution_authorized": False},
        evidence_refs=list(evidence_refs), idempotency_key=f"active-query:{decision_id}",
    )
    return {"ok": True, "status": "query_proposed", "decision": decision,
            "path": str(output), "files": [str(output)], "event": event,
            "production_mutation": False}


def record_agent_experiment_feedback(workspace: str, *, context_key: str, option_id: str,
                                     success: bool, evidence_refs: list[str],
                                     observation_id: str = "") -> dict[str, Any]:
    """Update active-learning memory only from explicit verifiable evidence."""
    if not context_key or not option_id or not evidence_refs:
        return {"ok": False, "status": "invalid_feedback",
                "error": "context_key, option_id and evidence_refs are required",
                "production_mutation": False}
    root = workspace_root(workspace)
    memory_path = root / "share/mind/governance/active_learning/memory.json"
    memory = ActiveLearningMemory.from_dict(_json(memory_path))
    before = memory.success_probability(context_key, option_id)
    accepted = memory.observe(context_key, option_id, success=bool(success),
                              observation_id=str(observation_id or ""))
    after = memory.success_probability(context_key, option_id)
    if accepted:
        atomic_json(memory_path, {**memory.to_dict(), "updated_at": now_iso()})
    feedback_id = hashlib.sha256(json.dumps({"context": context_key, "option": option_id,
                                             "success": bool(success), "evidence": evidence_refs},
                                            sort_keys=True).encode()).hexdigest()[:16]
    event = append_evolution_event(
        str(root), ("active_learning/feedback_recorded" if accepted
                    else "active_learning/duplicate_feedback_ignored"),
        subject_id=f"feedback_{feedback_id}",
        project_id="agent_self_evolution",
        payload={"context_key": context_key, "option_id": option_id, "success": bool(success),
                 "success_probability_before": before, "success_probability_after": after},
        evidence_refs=evidence_refs, idempotency_key=f"active-feedback:{feedback_id}",
    )
    return {"ok": True, "status": ("feedback_recorded" if accepted
                                     else "duplicate_feedback_ignored"),
            "observation_id": str(observation_id or ""),
            "before": before, "after": after, "event": event,
            "production_mutation": False}


def diagnose_agent_failure(workspace: str, *, failure_class: str,
                           evidence_refs: list[str]) -> dict[str, Any]:
    """Diagnose Partner itself from Episodes (legacy API name, self-evolution semantics)."""
    if not failure_class or not evidence_refs:
        return {"ok": False, "status": "invalid_diagnostic_request",
                "error": "failure_class and evidence_refs are required",
                "production_mutation": False}
    root = workspace_root(workspace)
    episode_root = (root / "share/mind/governance/episodes").resolve()
    parent_failure_class = failure_class.split("/", 1)[0]
    selected_step_type = failure_class.split("/", 1)[1] if "/" in failure_class else ""
    episodes, unmatched_signatures, readable_logs, missing_evidence = [], Counter(), 0, 0
    evidence_source_counts = Counter()
    accepted_refs = []
    for value in evidence_refs:
        state_path = Path(value).resolve()
        if not state_path.is_relative_to(episode_root) or state_path.name != "state.json":
            continue
        state = _json(state_path)
        state_classes = _state_failure_classes(state)
        if not any(
            value == failure_class
            or value == parent_failure_class
            or value.startswith(parent_failure_class + "/")
            for value in state_classes
        ):
            continue
        accepted_refs.append(str(state_path))
        starts: dict[str, str] = {}
        terminal: set[str] = set()
        outcome_mechanisms: set[str] = {
            str(row.get("mechanism") or row.get("class") or "")
            for row in state.get("failure_details") or []
            if isinstance(row, dict)
            and (str(row.get("class") or "") == failure_class
                 or str(row.get("class") or "").startswith(parent_failure_class))
            and str(row.get("mechanism") or row.get("class") or "")
        }
        preflight_mechanisms = (_semantic_preflight_mechanisms(state)
                                if parent_failure_class == "planning.semantic_preflight" else [])
        source_logs = [Path(str(source)) for source in state.get("source_refs") or []
                       if Path(str(source)).name == "task_log.jsonl" and Path(str(source)).is_file()]
        trace_path = state_path.parent / "trace.jsonl"
        candidates = [(path, "raw_task_log") for path in source_logs]
        if not candidates and trace_path.is_file():
            candidates = [(trace_path, "episode_trace")]
        episode_readable = False
        for path, source_kind in candidates[:1]:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            episode_readable = True
            evidence_source_counts[source_kind] += 1
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
                event = str(payload.get("event") or row.get("type") or "")
                step_id = str(payload.get("step_id") or "")
                if event == "plan_executor_step_started" and step_id:
                    starts[step_id] = str(payload.get("event_type") or "unknown")
                elif event in {"plan_executor_step_completed", "plan_executor_step_failed"} and step_id:
                    terminal.add(step_id)
                if event == "manual_iteration_governance":
                    status = str(payload.get("status") or "").strip()
                    if status:
                        outcome_mechanisms.add(f"governance.{status}")
                elif event == "iteration_check" and payload.get("satisfied") is False:
                    outcome_mechanisms.add("iteration_check.failed")
                elif event == "artifact_validation" and payload.get("ok") is False:
                    outcome_mechanisms.add("artifact_validation.failed")
                elif event in {"delivery_failed", "delivery_unconfirmed"}:
                    outcome_mechanisms.add("delivery.unconfirmed")
        if episode_readable:
            readable_logs += 1
        missing = [event_type for step_id, event_type in starts.items() if step_id not in terminal]
        if parent_failure_class == "planning.semantic_preflight":
            mechanisms = preflight_mechanisms
        elif outcome_mechanisms:
            mechanisms = sorted(outcome_mechanisms)
        elif failure_class.startswith("outcome."):
            mechanisms = sorted(outcome_mechanisms) or missing
        else:
            mechanisms = missing
        if selected_step_type:
            if (parent_failure_class.startswith("outcome.")
                    or parent_failure_class == "planning.semantic_preflight"):
                mechanisms = [value for value in mechanisms if value == selected_step_type]
                if not mechanisms:
                    continue
            elif outcome_mechanisms:
                mechanisms = [value for value in mechanisms
                              if value == failure_class
                              or value.endswith("/" + selected_step_type)]
                if not mechanisms:
                    continue
            else:
                missing = [value for value in missing if value == selected_step_type]
                mechanisms = missing
                if not missing:
                    continue
        if mechanisms:
            unmatched_signatures.update(set(mechanisms))
        elif not starts:
            missing_evidence += 1
        episodes.append({"episode_id": state.get("episode_id"),
                         "task_id": state.get("task_id"),
                         "instance_id": state.get("instance_id"),
                         "project_id": state.get("project_id"),
                         "state_path": str(state_path),
                         "unmatched_step_types": missing,
                         "mechanism_signatures": mechanisms})
    count = len(episodes)
    top_signature, top_count = (unmatched_signatures.most_common(1)[0]
                                if unmatched_signatures else ("none", 0))
    concentration = top_count / max(1, sum(unmatched_signatures.values()))
    evidence_coverage = readable_logs / max(1, count)
    if evidence_coverage < 0.6:
        classification, confidence = "uncertain", max(.1, evidence_coverage)
        next_option = "collect_episode_task_log_evidence"
    elif (failure_class.startswith("outcome.") and count >= 5
          and len(unmatched_signatures) >= 2 and concentration < 0.8):
        classification, confidence = "heterogeneous_failure_class", 1.0 - concentration
        next_option = "split_failure_class_by_outcome_reason"
    elif count >= 5 and unmatched_signatures and concentration < 0.5:
        classification, confidence = "heterogeneous_failure_class", 1.0 - concentration
        next_option = "split_failure_class_by_step_type"
    elif count >= 3 and readable_logs >= 3 and concentration >= 0.5:
        classification, confidence = "systematic_failure", min(.95, .60 + .04 * count)
        next_option = "bounded_repair"
    elif any(token in failure_class for token in ("timeout", "network", "rate_limit")):
        classification, confidence = "transient_failure", .65
        next_option = "matched_resample"
    else:
        classification, confidence = "uncertain", .5
        next_option = "collect_more_matched_evidence"
    llm_diagnosis = _llm_self_evolution_diagnosis({
        "failure_class": failure_class,
        "machine_classification": classification,
        "machine_next_option": next_option,
        "episode_count": count,
        "evidence_coverage": round(evidence_coverage, 4),
        "mechanism_counts": dict(unmatched_signatures),
        "top_signature": top_signature,
        "instance_id": str((episodes[0] if episodes else {}).get("instance_id") or "05"),
        "project_id": str((episodes[0] if episodes else {}).get("project_id") or "agent_self_evolution"),
        "task_id": str((episodes[0] if episodes else {}).get("task_id") or ""),
        "episode_id": str((episodes[0] if episodes else {}).get("episode_id") or ""),
        "evidence_refs": accepted_refs,
    }, root=root)
    claim_audit = _audit_causal_diagnosis(
        failure_class=failure_class, episodes=episodes, llm_diagnosis=llm_diagnosis)
    # The LLM can refine the experiment choice only inside the hard allow-list;
    # sparse/unreadable evidence always keeps the conservative machine choice.
    if (evidence_coverage >= 0.6
            and str(llm_diagnosis.get("recommended_next_option") or "")):
        next_option = str(llm_diagnosis["recommended_next_option"])
    diagnosis_id = "diagnosis_" + hashlib.sha256(json.dumps({
        "failure": failure_class, "refs": accepted_refs,
        "signatures": dict(unmatched_signatures),
        "diagnostic_version": 6,
    }, sort_keys=True).encode()).hexdigest()[:16]
    result = {
        "schema_version": 1, "diagnosis_id": diagnosis_id,
        "diagnostic_version": 6,
        "failure_class": failure_class, "classification": classification,
        "confidence": confidence, "episode_count": count,
        "readable_task_logs": readable_logs,
        "missing_or_unreadable_evidence_count": missing_evidence,
        "evidence_coverage": evidence_coverage,
        "evidence_source_counts": dict(evidence_source_counts),
        "unmatched_step_signatures": dict(unmatched_signatures),
        "top_signature": top_signature, "signature_concentration": concentration,
        "episodes": episodes, "recommended_next_option": next_option,
        "semantic_kind": "partner_self_evolution",
        "llm_diagnosis": llm_diagnosis,
        "claim_audit": claim_audit,
        "llm_role": "bounded_causal_critic_not_production_approver",
        "claim_scope": "diagnosis from persisted Episode/task-log evidence; not proof of repair efficacy",
        "execution_authorized": False, "production_mutation": False,
    }
    output = root / "share/mind/governance/active_learning/diagnoses" / f"{diagnosis_id}.json"
    atomic_json(output, result)
    event = append_evolution_event(
        str(root), "active_learning/diagnosis_completed", subject_id=diagnosis_id,
        project_id="agent_self_evolution",
        payload={"failure_class": failure_class, "classification": classification,
                 "confidence": confidence, "recommended_next_option": next_option,
                 "top_signature": top_signature,
                 "semantic_kind": "partner_self_evolution",
                 "llm_used": bool(llm_diagnosis and not llm_diagnosis.get("error"))},
        evidence_refs=accepted_refs, idempotency_key=f"active-diagnosis:{diagnosis_id}",
    )
    return {"ok": True, "status": "diagnosis_completed", "diagnosis": result,
            "path": str(output), "files": [str(output)], "event": event,
            "summary": f"{failure_class}: {classification}; next={next_option}",
            "_model_calls": int(llm_diagnosis.get("model_calls") or
                                bool(llm_diagnosis and not llm_diagnosis.get("error"))),
            "production_mutation": False}


def observe_manual_failure(workspace: str, *, instance_id: str,
                           task_id: str, reduced: dict[str, Any]) -> dict[str, Any]:
    """Automatically turn a governed manual failure into a bounded learning proposal.

    This is the safe hot-path closure: observe -> select -> diagnose -> propose.
    It never edits source, control policy, or production configuration.  A
    matched experiment is still required before feedback or promotion.
    """
    state = reduced.get("state") if isinstance(reduced.get("state"), dict) else {}
    bundle = str(reduced.get("bundle") or "")
    # Use the same governed fallback as selection/diagnosis. A failed Episode
    # with no tool-level class is still a real negative outcome.
    classes = _state_failure_classes(state)
    preferred = [value for value in classes if (
        value.startswith("planning.output_reference_contract/")
        or value == "verification.claim_level_truth_gate"
        or value.startswith("planning.retry/")
    )]
    if not preferred:
        preferred = classes
    if not preferred or not bundle:
        return {"ok": False, "status": "no_learnable_failure", "production_mutation": False}
    target = sorted(preferred)[0]
    state_path = str(Path(bundle) / "state.json")
    selection = select_agent_experiment(
        workspace, instance_ids=[instance_id], focus_failure_class=target,
    )
    diagnosis = diagnose_agent_failure(
        workspace, failure_class=target, evidence_refs=[state_path],
    )
    root = workspace_root(workspace)
    repair_id = "repair_proposal_" + hashlib.sha256(
        f"{instance_id}|{task_id}|{target}".encode()
    ).hexdigest()[:16]
    intervention = (
        "typed_output_reference_resolution_v1"
        if target.startswith("planning.output_reference_contract/")
        else "claim_ledger_truth_gate_v1"
        if target == "verification.claim_level_truth_gate"
        else "mechanism_specific_bounded_repair"
    )
    proposal = {
        "schema_version": 1, "proposal_id": repair_id, "created_at": now_iso(),
        "instance_id": instance_id, "task_id": task_id,
        "failure_class": target, "intervention": intervention,
        "selection_id": (selection.get("decision") or {}).get("decision_id"),
        "diagnosis_id": (diagnosis.get("diagnosis") or {}).get("diagnosis_id"),
        "required_next_gate": "matched_baseline_candidate_experiment",
        "execution_authorized": False, "production_mutation": False,
        "evidence_refs": [state_path],
    }
    output = root / "share/mind/governance/active_learning/repair_proposals" / f"{repair_id}.json"
    atomic_json(output, proposal)
    # Sprint18 §5 patch 3: bridge repair_proposal to production_readiness so
    # overnight_canary sees bounded candidates and can promote them.
    try:
        from partner.evolution.sprint18_unified_patch import promote_repair_to_production_readiness
        pr_bridge = promote_repair_to_production_readiness(str(root), str(output))
        if pr_bridge.get("ok"):
            logger.info(
                "[SPRINT18_UNIFIED] repair -> production_readiness bridge: %s",
                pr_bridge.get("candidate_id"),
            )
    except Exception as _exc:  # noqa: BLE001
        logger.debug("[SPRINT18_UNIFIED] repair->production bridge skipped: %s", _exc)

    event = append_evolution_event(
        str(root), "active_learning/repair_candidate_proposed", subject_id=repair_id,
        project_id="agent_self_evolution", payload=proposal,
        evidence_refs=[state_path, str(output)],
        idempotency_key=f"manual-failure-learning:{repair_id}",
    )
    return {"ok": True, "status": "failure_observed_and_repair_proposed",
            "proposal": proposal, "path": str(output), "selection": selection,
            "diagnosis": diagnosis, "event": event, "production_mutation": False}


def run_manual_learning_matched_experiment(workspace: str, *, experiment_id: str = "") -> dict[str, Any]:
    """Run frozen checks plus a mechanism-bound native routing match."""
    from partner.mind.claim_ledger import audit_claim_artifacts
    from partner.mind.output_reference import resolve_pdf_source

    root = workspace_root(workspace)
    exp_id = experiment_id or ("experiment_" + hashlib.sha256(
        f"manual-learning-v1|{now_iso()}".encode()).hexdigest()[:16])
    exp_dir = root / "share/mind/governance/active_learning/experiments" / exp_id
    prior_output = exp_dir / "experiment.json"
    if prior_output.is_file():
        prior = _json(prior_output)
        return {
            "ok": prior.get("status") == "passed",
            "status": "idempotent_experiment_replay",
            "experiment": prior,
            "path": str(prior_output), "files": [str(prior_output)],
            "production_mutation": False,
        }
    exp_dir.mkdir(parents=True, exist_ok=True)
    source = exp_dir / "source.md"
    source.write_text(
        "# Harness\nThe runtime records every tool event and retry in an append-only event log.\n",
        encoding="utf-8",
    )
    valid = exp_dir / "candidate_report.md"
    valid.write_text(
        "claim_id: c1\n"
        "claim_text: runtime records every tool event in an event log\n"
        "claim_axes: event_recording\n"
        f"source_path: {source}\nsource_identity: source.md\n"
        "evidence_quote: The runtime records every tool event and retry in an append-only event log.\n"
        "support_type: direct\nrationale: direct statement\n",
        encoding="utf-8",
    )
    confused = exp_dir / "confused_report.md"
    confused.write_text(
        "claim_id: c_bad\nclaim_text: runtime automatically recovers all failures\n"
        "claim_axes: failure_recovery\n"
        f"source_path: {source}\nsource_identity: source.md\n"
        "evidence_quote: The runtime records every tool event and retry in an append-only event log.\n"
        "support_type: direct\nrationale: wrong semantic attribution\n",
        encoding="utf-8",
    )
    baseline = {
        "accepts_literal_membership": source.read_text(encoding="utf-8").find(
            "The runtime records every tool event and retry in an append-only event log."
        ) >= 0,
        "rejects_semantic_confusion": False,
        "resolves_typed_output": False,
    }
    valid_audit = audit_claim_artifacts([str(valid)], named_input_sources=[str(source)])
    confused_audit = audit_claim_artifacts([str(confused)], named_input_sources=[str(source)])
    resolved, resolution_audit = resolve_pdf_source(
        params={"source_path": "$step7.result.path"}, step_id="step8",
        results={"step7": {"result": {"path": str(valid), "files": [str(valid)]}}},
        working_dir=str(exp_dir), event_type="atomic_generate_pdf",
    )
    candidate = {
        "accepts_supported_claim": bool(valid_audit.get("passed")),
        "rejects_semantic_confusion": not bool(confused_audit.get("passed")),
        "resolves_typed_output": bool(resolution_audit.get("resolved"))
                                 and resolved.get("source_path") == str(valid),
    }
    mechanism_match: dict[str, Any] = {"applicable": False, "passed": True}
    if exp_id.startswith("native_episode_"):
        # Versioned replays may suffix an immutable Episode id (for example
        # ``native_episode_abcd_mechanism_v2``). Resolve the longest real
        # Episode prefix instead of treating the suffix as part of its id.
        episode_dirs = sorted(
            (root / "share/mind/governance/episodes").glob("episode_*/state.json"),
            key=lambda value: len(value.parent.name), reverse=True,
        )
        episode_id = next(
            (value.parent.name for value in episode_dirs
             if exp_id.startswith("native_" + value.parent.name)),
            exp_id[len("native_"):],
        )
        episode_path = root / "share/mind/governance/episodes" / episode_id / "state.json"
        episode = _json(episode_path)
        iid = str(episode.get("instance_id") or "")
        from partner.governance.instance_native import PROJECTS
        expected_project = (PROJECTS.get(iid) or ("", ""))[0]
        failure_classes = [str(value) for value in episode.get("failure_classes") or []]
        failure_evidence: list[str] = []
        source_event_types: list[str] = []
        # Older Episode reducers did not project final acceptance failures
        # into failure_classes. Recover a typed signal from their immutable
        # task log; do not rewrite history or silently call an empty class a
        # mechanism match.
        task_id = str(episode.get("task_id") or "")
        task_log = root / "instances" / iid / "state/tasks" / task_id / "task_log.jsonl"
        archived_trace = episode_path.parent / "trace.jsonl"
        failure_log = task_log if task_log.is_file() else archived_trace
        if not failure_classes and failure_log.is_file():
            for line in failure_log.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(row.get("payload"), dict) and row.get("type"):
                    row = row["payload"]
                if row.get("event") == "manual_iteration_governance":
                    embedded = (((row.get("trajectory") or {}).get("trajectory")) or {})
                    source_event_types = [str(value) for value in
                                          (embedded.get("action") or {}).get("event_types") or []]
                if row.get("event") == "iteration_check":
                    missing = [str(value) for value in row.get("missing") or []]
                    failure_evidence.extend(missing)
                    if any(value.startswith("named_artifact:") for value in missing):
                        failure_classes.append(
                            "verification.acceptance_contract/implicit_handoff_artifact")
                    if any(value.startswith("citations<") for value in missing):
                        failure_classes.append(
                            "verification.acceptance_contract/native_project_citation_gate")
                if (row.get("event") == "manual_iteration_governance"
                        and row.get("status") == "candidate_truth_gate_failed"):
                    failure_classes.append(
                        "verification.claim_truth/native_project_scope_mismatch")
                    failure_evidence.append("candidate_truth_gate_failed")
        failure_classes = list(dict.fromkeys(failure_classes))
        try:
            from partner.planner.batch_planner import _native_project_execution_plan
            instance_workspace = root / "instances" / iid
            plan = _native_project_execution_plan(
                str(instance_workspace),
                f"[instance_native=true] [native_kind=project] [project_id={expected_project}]",
                str(exp_dir / "native_candidate"),
            )
            event_types = [step.event_type for step in (plan.plan if plan else [])]
            params = [dict(step.parameters or {}) for step in (plan.plan if plan else [])]
        except Exception as exc:
            event_types, params = [], []
            route_error = f"{type(exc).__name__}: {exc}"
        else:
            route_error = ""
        event_set = set(event_types)
        native_event_bound = bool(event_set and event_set <= {
            "continuous_project_step", "molecular_generation_benchmark",
            "molecular_diversity_benchmark", "molecular_synth_baseline_benchmark",
            "molecular_goal_optimization_benchmark", "external_knowledge_scout",
        })
        supported_failure = bool(failure_classes) and all(
            value == "project/missing_external_action"
            or value == "project/repeated_findings"
            or value.startswith("verification.acceptance_contract/")
            or value == "verification.claim_truth/native_project_scope_mismatch"
            or value == "outcome.duplicate_semantic_result"
            for value in failure_classes
        )
        repeated_result = any(value in {
            "project/repeated_findings", "outcome.duplicate_semantic_result",
        } for value in failure_classes)
        candidate_changes_action = bool(
            not repeated_result
            or (source_event_types and event_types != source_event_types)
        )
        mechanism_match = {
            "applicable": True,
            "episode_id": episode_id,
            "episode_path": str(episode_path),
            "failure_classes": failure_classes,
            "failure_evidence": failure_evidence,
            "instance_id": iid,
            "project_id": expected_project,
            "candidate_event_types": event_types,
            "source_event_types": source_event_types,
            "candidate_parameters": params,
            "route_error": route_error,
            "candidate_binding": (
                "native Event typed-output and scope boundary replay"
                if supported_failure else "unbound_failure_mechanism"
            ),
            "candidate_changes_action": candidate_changes_action,
            "passed": bool(
                episode and expected_project and native_event_bound
                and supported_failure and candidate_changes_action
            ),
        }
    gates = {
        "baseline_exposes_known_gaps": baseline["accepts_literal_membership"]
                                      and not baseline["rejects_semantic_confusion"]
                                      and not baseline["resolves_typed_output"],
        "candidate_accepts_supported_claim": candidate["accepts_supported_claim"],
        "candidate_rejects_semantic_confusion": candidate["rejects_semantic_confusion"],
        "candidate_resolves_typed_output": candidate["resolves_typed_output"],
        "native_mechanism_bound_route": bool(mechanism_match["passed"]),
    }
    passed = all(gates.values())
    result = {
        "schema_version": 1, "experiment_id": exp_id,
        "status": "passed" if passed else "rejected", "created_at": now_iso(),
        "match_key": "frozen_manual_failure_fixtures_v1",
        "baseline": baseline, "candidate": candidate, "gates": gates,
        "claim_audits": {"valid": valid_audit, "confused": confused_audit},
        "resolution_audit": resolution_audit,
        "mechanism_match": mechanism_match,
        "production_mutation": False,
    }
    output = exp_dir / "experiment.json"
    atomic_json(output, result)
    decision = {
        "schema_version": 1, "experiment_id": exp_id,
        "decision": "candidate_validated" if passed else "rejected",
        "production_effective": False,
        "reason": "matched hard gates passed" if passed else "one or more hard gates failed",
        "created_at": now_iso(), "evidence_refs": [str(output)],
    }
    append_jsonl(root / "share/mind/governance/experience_guided_policy/promotion_decisions.jsonl", decision)
    feedback = record_agent_experiment_feedback(
        str(root), context_key="manual.failure_repair",
        option_id="bounded_repair", success=passed, evidence_refs=[str(output)],
    )
    event = append_evolution_event(
        str(root), "active_learning/matched_experiment_completed", subject_id=exp_id,
        project_id="agent_self_evolution",
        payload={"status": result["status"], "gates": gates, "production_effective": False},
        evidence_refs=[str(output)], idempotency_key=f"manual-learning-experiment:{exp_id}",
    )
    return {"ok": passed, "status": result["status"], "experiment": result,
            "decision": decision, "feedback": feedback, "event": event,
            "path": str(output), "files": [str(output)], "production_mutation": False}


def refine_failure_taxonomy(workspace: str, *, diagnosis_path: str) -> dict[str, Any]:
    """Version a heterogeneous failure label into evidence-backed subtypes."""
    root = workspace_root(workspace)
    diagnoses_root = (root / "share/mind/governance/active_learning/diagnoses").resolve()
    path = Path(diagnosis_path).resolve()
    if not path.is_relative_to(diagnoses_root):
        return {"ok": False, "status": "invalid_taxonomy_source",
                "error": "diagnosis_path must be a Partner active-learning diagnosis",
                "production_mutation": False}
    diagnosis = _json(path)
    signatures = dict(diagnosis.get("unmatched_step_signatures") or {})
    if (diagnosis.get("classification") not in {
            "heterogeneous_failure_class", "systematic_failure"}
            or len(signatures) < 2):
        return {"ok": False, "status": "taxonomy_refinement_not_supported",
                "error": "refinement requires at least two evidence-backed mechanisms",
                "production_mutation": False}
    parent = str(diagnosis.get("failure_class") or "")
    assignments = {
        str(row.get("episode_id") or ""): sorted({f"{parent}/{mechanism}"
                                                   for mechanism in (
                                                       row.get("mechanism_signatures")
                                                       or row.get("unmatched_step_types") or [])})
        for row in diagnosis.get("episodes") or []
        if row.get("episode_id") and (
            row.get("mechanism_signatures") or row.get("unmatched_step_types"))
    }
    children = sorted({child for values in assignments.values() for child in values})
    if len(children) < 2:
        return {"ok": False, "status": "insufficient_taxonomy_children",
                "production_mutation": False}
    taxonomy_id = "taxonomy_" + hashlib.sha256(json.dumps({
        "diagnosis_id": diagnosis.get("diagnosis_id"), "assignments": assignments,
    }, sort_keys=True).encode()).hexdigest()[:16]
    taxonomy = {
        "schema_version": 1, "taxonomy_id": taxonomy_id,
        "status": "candidate_taxonomy", "created_at": now_iso(),
        "parent_failure_class": parent, "children": children,
        "episode_assignments": assignments, "source_diagnosis": str(path),
        "scope": "active-learning hypothesis refinement; historical Episodes remain immutable",
        "production_effective": False,
    }
    output = root / "share/mind/governance/active_learning/taxonomies" / f"{taxonomy_id}.json"
    atomic_json(output, taxonomy)
    index_path = root / "share/mind/governance/active_learning/taxonomy_index.json"
    index = _json(index_path)
    by_parent = dict(index.get("by_parent") or {})
    if not by_parent and index.get("latest_path"):
        prior = _json(Path(str(index["latest_path"])))
        if prior.get("parent_failure_class"):
            by_parent[str(prior["parent_failure_class"])] = str(index["latest_path"])
    by_parent[parent] = str(output)
    atomic_json(index_path, {"schema_version": 2, "latest_taxonomy_id": taxonomy_id,
                             "latest_path": str(output), "by_parent": by_parent,
                             "updated_at": now_iso()})
    event = append_evolution_event(
        str(root), "active_learning/strategy_revised", subject_id=taxonomy_id,
        project_id="agent_self_evolution",
        payload={"parent_failure_class": parent, "children": children,
                 "source_diagnosis_id": diagnosis.get("diagnosis_id"),
                 "production_effective": False},
        evidence_refs=[str(path)], idempotency_key=f"active-taxonomy:{taxonomy_id}",
    )
    return {"ok": True, "status": "taxonomy_refined", "taxonomy": taxonomy,
            "path": str(output), "files": [str(output)], "event": event,
            "summary": f"split {parent} into {len(children)} evidence-backed subtypes",
            "production_mutation": False}


def evaluate_skipped_terminal_repair(workspace: str, *, diagnosis_path: str) -> dict[str, Any]:
    """Replay a diagnosis and test terminalizing dependency-skipped steps.

    This is a read-only matched shadow: it does not rewrite task logs or Episode
    bundles.  A missing terminal is repairable only when the same immutable
    trace explicitly records that the step was skipped because a required
    dependency failed.
    """
    root = workspace_root(workspace)
    diagnoses_root = (root / "share/mind/governance/active_learning/diagnoses").resolve()
    path = Path(diagnosis_path).resolve()
    if not path.is_relative_to(diagnoses_root):
        return {"ok": False, "status": "invalid_repair_source",
                "error": "diagnosis_path must be a Partner active-learning diagnosis",
                "production_mutation": False}
    diagnosis = _json(path)
    failure_class = str(diagnosis.get("failure_class") or "")
    step_type = failure_class.split("/", 1)[1] if "/" in failure_class else ""
    if diagnosis.get("classification") != "systematic_failure" or not step_type:
        return {"ok": False, "status": "repair_not_supported",
                "error": "requires a systematic step-subtype diagnosis",
                "production_mutation": False}

    rows_out, evidence_refs = [], []
    baseline_unclosed = repair_terminalized = irreducible_unclosed = 0
    episode_hashes_before: dict[str, str] = {}
    for episode in diagnosis.get("episodes") or []:
        state_path = Path(str(episode.get("state_path") or "")).resolve()
        if not state_path.is_relative_to((root / "share/mind/governance/episodes").resolve()):
            continue
        trace_path = state_path.parent / "trace.jsonl"
        try:
            state_hash = hashlib.sha256(state_path.read_bytes()).hexdigest()
            trace_hash = hashlib.sha256(trace_path.read_bytes()).hexdigest()
            trace_rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        except (OSError, ValueError, TypeError):
            continue
        episode_hashes_before[str(state_path)] = state_hash
        episode_hashes_before[str(trace_path)] = trace_hash
        starts: dict[str, dict[str, Any]] = {}
        terminal: set[str] = set()
        terminal_state: dict[str, str] = {}
        dependency_skips: set[str] = set()
        plan_steps: dict[str, dict[str, Any]] = {}
        for raw in trace_rows:
            payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
            event = str(payload.get("event") or raw.get("type") or "")
            step_id = str(payload.get("step_id") or "")
            if event in {"batch_plan_created", "iteration_started"}:
                raw_steps = payload.get("steps") or payload.get("plan_steps") or []
                for plan_step in raw_steps:
                    if isinstance(plan_step, dict) and plan_step.get("id"):
                        plan_steps[str(plan_step["id"])] = plan_step
            if event == "plan_executor_step_started" and step_id:
                starts[step_id] = {
                    "event_type": str(payload.get("event_type") or ""),
                    "depends_on": [str(value) for value in payload.get("depends_on") or []],
                }
            elif event in {"plan_executor_step_completed", "plan_executor_step_failed"} and step_id:
                terminal.add(step_id)
                terminal_state[step_id] = (
                    "skipped" if payload.get("terminal_status") == "skipped"
                    else ("completed" if payload.get("ok") is True else "failed")
                )
            elif event == "remediation_triggered":
                for failure in payload.get("failures") or []:
                    if not isinstance(failure, dict):
                        continue
                    error = str(failure.get("error") or "")
                    if error.startswith("skipped: required dependencies failed"):
                        dependency_skips.add(str(failure.get("step_id") or ""))
        # V2: infer the exact historical early-return branch even when a
        # successful sibling produced an artifact and remediation never ran.
        # This replays only persisted plan dependencies and parameter refs.
        unmatched_all = {step_id for step_id in starts if step_id not in terminal}
        inferred_skips: set[str] = set(dependency_skips)
        changed = True
        while changed:
            changed = False
            for candidate_step in sorted(unmatched_all - inferred_skips):
                start = starts[candidate_step]
                plan_step = plan_steps.get(candidate_step) or {}
                dependencies = [str(value) for value in
                                (start.get("depends_on") or plan_step.get("depends_on") or [])]
                failed = [dep for dep in dependencies
                          if terminal_state.get(dep) in {"failed", "skipped"}
                          or dep in inferred_skips]
                if not failed:
                    continue
                serialized = json.dumps(plan_step.get("parameters") or {}, ensure_ascii=False)
                referenced = any(
                    token in serialized
                    for dep in failed
                    for token in (f"${dep}", f"${{{dep}", f"{{{{{dep}")
                )
                if len(failed) == len(dependencies) or referenced:
                    inferred_skips.add(candidate_step)
                    terminal_state[candidate_step] = "skipped"
                    changed = True
        unmatched = sorted(step_id for step_id, value in starts.items()
                           if value.get("event_type") == step_type and step_id not in terminal)
        repairable = sorted(set(unmatched) & inferred_skips)
        irreducible = sorted(set(unmatched) - inferred_skips)
        baseline_unclosed += len(unmatched)
        repair_terminalized += len(repairable)
        irreducible_unclosed += len(irreducible)
        rows_out.append({"episode_id": episode.get("episode_id"),
                         "unmatched_step_ids": unmatched,
                         "dependency_skip_evidence": repairable,
                         "direct_remediation_skip_evidence": sorted(set(unmatched) & dependency_skips),
                         "dependency_graph_inferred_skip_evidence": sorted(
                             set(unmatched) & (inferred_skips - dependency_skips)),
                         "irreducible_step_ids": irreducible,
                         "state_path": str(state_path), "trace_path": str(trace_path)})
        evidence_refs.extend([str(state_path), str(trace_path)])

    episode_hashes_after = {value: hashlib.sha256(Path(value).read_bytes()).hexdigest()
                            for value in episode_hashes_before}
    metrics = {
        "matched_episode_count": len(rows_out),
        "baseline_unclosed_count": baseline_unclosed,
        "bounded_repair_terminalized_count": repair_terminalized,
        "bounded_repair_remaining_unclosed_count": irreducible_unclosed,
        "matched_resample_expected_change": "unknown_without_new_execution",
        "no_op_unclosed_count": baseline_unclosed,
        "evidence_coverage": len(rows_out) / max(1, len(diagnosis.get("episodes") or [])),
        "historical_evidence_immutable": episode_hashes_before == episode_hashes_after,
    }
    repair_id = "repair_eval_" + hashlib.sha256(json.dumps({
        "diagnosis": diagnosis.get("diagnosis_id"), "metrics": metrics,
    }, sort_keys=True).encode()).hexdigest()[:16]
    result = {
        "schema_version": 1, "repair_evaluation_id": repair_id,
        "evaluator_version": 2,
        "failure_class": failure_class, "intervention": "terminalize_dependency_skipped_step_v1",
        "arms": {
            "no_op": "replay persisted log unchanged",
            "matched_resample": "not scored: requires a new bounded execution",
            "bounded_repair": "persist one skipped terminal event when dependency-skip evidence exists",
        },
        "metrics": metrics, "episodes": rows_out,
        "claim_scope": "historical observability counterfactual; not task-outcome improvement",
        "production_mutation": False,
    }
    output = root / "share/mind/governance/active_learning/repairs" / f"{repair_id}.json"
    atomic_json(output, result)
    event = append_evolution_event(
        str(root), "active_learning/repair_evaluated", subject_id=repair_id,
        project_id="agent_self_evolution",
        payload={"failure_class": failure_class, "intervention": result["intervention"],
                 "baseline_unclosed_count": baseline_unclosed,
                 "terminalized_count": repair_terminalized,
                 "remaining_unclosed_count": irreducible_unclosed,
                 "production_effective": False},
        evidence_refs=evidence_refs, idempotency_key=f"active-repair-eval:{repair_id}",
    )
    return {"ok": True, "status": "repair_shadow_evaluated", "evaluation": result,
            "path": str(output), "files": [str(output)], "event": event,
            "summary": (f"terminalized {repair_terminalized}/{baseline_unclosed} false-unclosed "
                        f"{step_type} steps in historical replay"),
            "production_mutation": False}


def run_skipped_terminal_fresh_canary(workspace: str, *, canary_id: str) -> dict[str, Any]:
    """Run a bounded local parallel plan through the real PlanExecutor path."""
    root = workspace_root(workspace)
    safe_canary = "".join(ch for ch in str(canary_id or "") if ch.isalnum() or ch in "_-")
    if not safe_canary:
        return {"ok": False, "status": "invalid_canary_id", "production_mutation": False}
    output = (root / "share/mind/governance/active_learning/canaries"
              / f"{safe_canary}.json")
    existing = _json(output)
    if existing.get("ok") is True:
        return {**existing, "status": "idempotent_canary_replay", "path": str(output),
                "files": [str(output)]}

    from types import SimpleNamespace
    from partner.harness_core import TaskInstance
    from partner.mind.harness import (
        EventRegistry, HarnessContext, HarnessEventSpec, HarnessStep, PlanExecutor, StateStore,
    )

    handler_calls: list[str] = []

    async def fail_source(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        handler_calls.append("source")
        return {"ok": False, "error": "deterministic missing source", "retryable": False}

    async def sibling_report(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        handler_calls.append("sibling")
        return {"ok": True, "content": "independent grounded report"}

    async def write_artifact(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        handler_calls.append("artifact")
        return {"ok": True, "content": "artifact persisted in bounded canary"}

    async def must_not_run(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        handler_calls.append("unexpected")
        raise AssertionError("dependency-skipped handler executed")

    registry = EventRegistry()
    registry.register(HarnessEventSpec("read_source", "atomic", "source", fail_source))
    registry.register(HarnessEventSpec("sibling_report", "atomic", "sibling", sibling_report))
    registry.register(HarnessEventSpec(
        "generate_text", "atomic", "compose", must_not_run, execution_method="llm"))
    registry.register(HarnessEventSpec("push_files", "atomic", "deliver", must_not_run))
    registry.register(HarnessEventSpec("write_artifact", "atomic", "artifact", write_artifact))

    canary_root = output.parent / safe_canary
    instance_root = canary_root / "instance"
    instance_root.mkdir(parents=True, exist_ok=True)
    task = TaskInstance.create(str(instance_root), "bounded parallel terminal canary",
                               task_id=f"task_{safe_canary}")
    ctx = HarnessContext(
        title="bounded terminal canary", task_instance=task, progress_callback=None,
        workspace=str(instance_root), project_dir=str(canary_root),
        state_md="", artifact_path="",
        event=SimpleNamespace(type=SimpleNamespace(value="manual"), payload={}),
    )
    plan = [
        HarnessStep("source", "read_source", {}, []),
        HarnessStep("sibling", "sibling_report", {}, []),
        HarnessStep("compose", "generate_text", {"data": "$source.result.content"}, ["source"]),
        HarnessStep("artifact", "write_artifact", {"data": "$sibling.result.content"}, ["sibling"]),
        HarnessStep("deliver", "push_files", {"source": "$compose.result.content"}, ["compose"]),
    ]
    results, model_calls, _, failures = asyncio.run(
        PlanExecutor(registry, StateStore(str(canary_root))).execute(ctx, plan))
    try:
        log_rows = [json.loads(line) for line in Path(task.log_path).read_text(
            encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        log_rows = []
    terminal_rows = [row for row in log_rows
                     if row.get("event") == "plan_executor_step_completed"]
    terminals_by_step = Counter(str(row.get("step_id") or "") for row in terminal_rows)
    terminal_status = {str(row.get("step_id") or ""): str(
        row.get("terminal_status") or ("completed" if row.get("ok") else "failed"))
        for row in terminal_rows}
    criteria = {
        "real_plan_executor_path": set(results) == {step.id for step in plan},
        "parallel_sibling_completed": results.get("sibling", {}).get("ok") is True
                                      and results.get("artifact", {}).get("ok") is True,
        "dependent_handlers_not_called": "unexpected" not in handler_calls,
        "skipped_steps_terminalized_once": terminals_by_step.get("compose") == 1
                                            and terminals_by_step.get("deliver") == 1,
        "terminal_semantics_correct": terminal_status.get("compose") == "skipped"
                                      and terminal_status.get("deliver") == "skipped",
        "no_false_model_call": model_calls == 0,
    }
    result = {
        "schema_version": 1, "canary_id": safe_canary,
        "ok": all(criteria.values()), "status": "fresh_canary_completed",
        "intervention": "terminalize_dependency_skipped_step_v1",
        "topology": "failed source -> skipped generate_text -> skipped push; parallel sibling -> artifact",
        "criteria": criteria, "handler_calls": handler_calls,
        "terminal_status": terminal_status, "terminal_counts": dict(terminals_by_step),
        "model_calls": model_calls, "step_failures": failures,
        "task_log": task.log_path, "production_mutation": False,
    }
    atomic_json(output, result)
    event = append_evolution_event(
        str(root), "active_learning/repair_canary_completed", subject_id=safe_canary,
        project_id="agent_self_evolution",
        payload={"canary_id": safe_canary, "ok": result["ok"], "criteria": criteria,
                 "production_effective": False},
        evidence_refs=[task.log_path, str(output)],
        idempotency_key=f"active-repair-canary:{safe_canary}",
    )
    return {**result, "path": str(output), "files": [str(output)], "event": event,
            "summary": "fresh parallel skipped-terminal canary passed" if result["ok"]
                       else "fresh parallel skipped-terminal canary failed"}


def run_handoff_intent_fresh_canary(workspace: str, *, canary_id: str) -> dict[str, Any]:
    """Exercise standalone/continuation Receipt semantics in an isolated workspace."""
    root = workspace_root(workspace)
    safe_canary = "".join(ch for ch in str(canary_id or "") if ch.isalnum() or ch in "_-")
    if not safe_canary:
        return {"ok": False, "status": "invalid_canary_id", "production_mutation": False}
    output = (root / "share/mind/governance/active_learning/canaries"
              / f"{safe_canary}.json")
    existing = _json(output)
    if existing.get("ok") is True:
        return {**existing, "status": "idempotent_canary_replay", "path": str(output),
                "files": [str(output)]}

    from .manual_runtime import record_manual_task_outcome
    from .storage import latest_receipt

    canary_root = output.parent / safe_canary / "workspace"
    instance_workspace = canary_root / "instances/03"
    artifacts_root = canary_root / "fixtures"
    instance_workspace.mkdir(parents=True, exist_ok=True)
    artifacts_root.mkdir(parents=True, exist_ok=True)
    seed_artifact = artifacts_root / "seed.md"
    independent_input = artifacts_root / "source.py"
    standalone_artifact = artifacts_root / "standalone.md"
    missing_artifact = artifacts_root / "missing.md"
    continued_artifact = artifacts_root / "continued.md"
    seed_artifact.write_text("seed receipt evidence", encoding="utf-8")
    independent_input.write_text("VALUE = 1\n", encoding="utf-8")
    standalone_artifact.write_text("standalone result", encoding="utf-8")
    missing_artifact.write_text("missing-handoff result", encoding="utf-8")
    continued_artifact.write_text("continued result", encoding="utf-8")
    common = {
        "goal": "bounded handoff intent canary", "actions_executed": ["atomic_inspect_file"],
        "findings": ["deterministic canary evidence"], "delivery_confirmed": True,
        "completion_ok": True,
    }
    seed = record_manual_task_outcome(str(instance_workspace), {
        **common, "task_id": f"{safe_canary}_seed", "inputs": [],
        "artifacts": [str(seed_artifact)], "continuation_requested": False,
    })
    standalone = record_manual_task_outcome(str(instance_workspace), {
        **common, "task_id": f"{safe_canary}_standalone",
        "inputs": [str(independent_input)], "artifacts": [str(standalone_artifact)],
        "continuation_requested": False,
    })
    project_id = "molecular_dynamics_study"
    previous = latest_receipt(str(instance_workspace), project_id)
    previous_artifact = str(previous.artifacts[0]) if previous and previous.artifacts else ""
    explicit_missing = record_manual_task_outcome(str(instance_workspace), {
        **common, "task_id": f"{safe_canary}_explicit_missing",
        "inputs": [str(independent_input)], "artifacts": [str(missing_artifact)],
        "continuation_requested": True,
    })
    explicit_linked = record_manual_task_outcome(str(instance_workspace), {
        **common, "task_id": f"{safe_canary}_explicit_linked",
        "inputs": [previous_artifact], "artifacts": [str(continued_artifact)],
        "continuation_requested": True,
    })
    final_receipt = latest_receipt(str(instance_workspace), project_id)
    criteria = {
        "seed_receipt_accepted": seed.get("ok") is True,
        "standalone_with_inputs_accepted": standalone.get("ok") is True,
        "explicit_missing_handoff_rejected": (
            explicit_missing.get("status") == "unlinked_previous_receipt"),
        "explicit_linked_handoff_accepted": explicit_linked.get("ok") is True,
        "standalone_has_no_handoff_reward": (
            ((standalone.get("trajectory") or {}).get("trajectory") or {})
            .get("reward_components", {}).get("handoff_consumed") == 0.0),
        "linked_continuation_has_handoff_reward": (
            ((explicit_linked.get("trajectory") or {}).get("trajectory") or {})
            .get("reward_components", {}).get("handoff_consumed") == 0.15),
        "rejected_attempt_did_not_advance_iteration": (
            bool(final_receipt) and final_receipt.iteration == 3),
        "isolated_from_production_workspace": str(canary_root).startswith(str(output.parent)),
    }
    result = {
        "schema_version": 1, "canary_id": safe_canary,
        "ok": all(criteria.values()), "status": "handoff_intent_canary_completed",
        "intervention": "explicit_continuation_intent_handoff_v1",
        "criteria": criteria,
        "observations": {
            "seed_status": seed.get("status"),
            "standalone_status": standalone.get("status"),
            "explicit_missing_status": explicit_missing.get("status"),
            "explicit_linked_status": explicit_linked.get("status"),
            "final_iteration": final_receipt.iteration if final_receipt else 0,
            "standalone_handoff_consumed": (
                ((standalone.get("trajectory") or {}).get("trajectory") or {})
                .get("outcome", {}).get("handoff_consumed")),
            "linked_handoff_consumed": (
                ((explicit_linked.get("trajectory") or {}).get("trajectory") or {})
                .get("outcome", {}).get("handoff_consumed")),
        },
        "canary_workspace": str(canary_root), "production_mutation": False,
    }
    atomic_json(output, result)
    event = append_evolution_event(
        str(root), "active_learning/repair_canary_completed", subject_id=safe_canary,
        project_id="agent_self_evolution",
        payload={"canary_id": safe_canary, "repair": result["intervention"],
                 "ok": result["ok"], "criteria": criteria,
                 "production_effective": False},
        evidence_refs=[str(output)],
        idempotency_key=f"active-handoff-canary:{safe_canary}",
    )
    return {**result, "path": str(output), "files": [str(output)], "event": event,
            "summary": "fresh handoff-intent canary passed" if result["ok"]
                       else "fresh handoff-intent canary failed"}


def run_preflight_input_manifest_fresh_canary(workspace: str, *, canary_id: str) -> dict[str, Any]:
    """Validate the feature-isolated explicit-input planning contract."""
    root = workspace_root(workspace)
    safe_canary = "".join(ch for ch in str(canary_id or "") if ch.isalnum() or ch in "_-")
    if not safe_canary:
        return {"ok": False, "status": "invalid_canary_id", "production_mutation": False}
    output = root / "share/mind/governance/active_learning/canaries" / f"{safe_canary}.json"
    existing = _json(output)
    if existing.get("ok") is True:
        return {**existing, "status": "idempotent_canary_replay", "path": str(output),
                "files": [str(output)]}

    from partner.mind.harness import EventRegistry, HarnessEventSpec, HarnessStep
    from partner.planner.batch_planner import (
        MicroPlan, _manual_environment_contract, _manual_preflight_plan,
    )

    isolated_root = output.parent / safe_canary / "isolated_root"
    instance = isolated_root / "instances/03"
    working = instance / "state/tasks/task"
    source = isolated_root / "share/evidence/business/source.md"
    missing = isolated_root / "share/evidence/business/missing.md"
    working.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("real bounded business evidence", encoding="utf-8")
    markers = "[experiment_id=e] [match_key=m]"
    baseline_message = (f"[strategy_id=baseline_current_preflight_v1] [policy_arm=baseline] "
                        f"{markers} inspect {source} and ignore unavailable {missing}")
    candidate_message = (f"[strategy_id=candidate_preflight_contract_v2] [policy_arm=candidate] "
                         f"{markers} inspect {source} and ignore unavailable {missing}")
    baseline_contract = _manual_environment_contract(str(instance), str(working), baseline_message)
    candidate_contract = _manual_environment_contract(str(instance), str(working), candidate_message)
    registry = EventRegistry()
    registry.register(HarnessEventSpec(
        "atomic_inspect_file", "atomic", "inspect", lambda ctx, params: {"ok": True},
        reads_existing_artifact=True, execution_method="local"))
    accepted = _manual_preflight_plan(
        MicroPlan(plan=[HarnessStep("read", "atomic_inspect_file",
                                    {"path": str(source)}, [])], expected_artifacts=[]),
        registry=registry, workspace=str(instance), working_dir=str(working),
        user_message=candidate_message,
    )
    invented_rejected = False
    try:
        _manual_preflight_plan(
            MicroPlan(plan=[HarnessStep("read", "atomic_inspect_file",
                                        {"path": str(missing)}, [])], expected_artifacts=[]),
            registry=registry, workspace=str(instance), working_dir=str(working),
            user_message=candidate_message,
        )
    except ValueError:
        invented_rejected = True
    criteria = {
        "baseline_route_unchanged": "verified_input_manifest" not in baseline_contract,
        "candidate_manifest_contains_existing_input": (
            "verified_input_manifest" in candidate_contract
            and str(source.resolve()) in candidate_contract),
        "candidate_manifest_excludes_missing_input": str(missing.resolve()) not in candidate_contract,
        "real_preflight_accepts_verified_path": accepted.plan[0].parameters.get("path") == str(source.resolve()),
        "invented_path_still_rejected": invented_rejected,
        "isolated_workspace_only": str(isolated_root).startswith(str(output.parent)),
    }
    result = {
        "schema_version": 1, "canary_id": safe_canary,
        "ok": all(criteria.values()), "status": "preflight_manifest_canary_completed",
        "intervention": "candidate_verified_input_manifest_v1", "criteria": criteria,
        "source": str(source), "missing": str(missing),
        "production_mutation": False,
    }
    atomic_json(output, result)
    event = append_evolution_event(
        str(root), "active_learning/repair_canary_completed", subject_id=safe_canary,
        project_id="agent_self_evolution",
        payload={"canary_id": safe_canary, "repair": result["intervention"],
                 "ok": result["ok"], "criteria": criteria, "production_effective": False},
        evidence_refs=[str(output)], idempotency_key=f"active-preflight-manifest:{safe_canary}",
    )
    return {**result, "path": str(output), "files": [str(output)], "event": event,
            "summary": "fresh preflight input-manifest canary passed" if result["ok"]
                       else "fresh preflight input-manifest canary failed"}
# partner governance: refreshed active_learning.py
# partner03_native_touch: 2026-09-04T09:21:30.055911

# partner03_native_progress_20260904T015643Z
