"""Create bounded, falsifiable Event Candidates from project history."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .models import now_iso
from .storage import atomic_json
from .evolution_events import append_evolution_event
from .project_reasoning import project_reasoning_contract


GRAMMARS: dict[str, list[dict[str, Any]]] = {
    "xiaohongshu_operations": [
        {"event": "continuous_project_step", "strategy": "01_claim_evidence_matrix", "key": "evidence_variant", "low": 1, "high": 5, "measures": ["records_with_source_evidence", "publish_authorized"]},
        {"event": "continuous_project_step", "strategy": "01_source_fact_check", "key": "evidence_variant", "low": 1, "high": 5, "measures": ["sources_checked", "sources_reachable"]}],
    "molecular_generation": [
        {"event": "molecular_method_candidate_benchmark", "strategy": "molecular_method_hypothesis",
         "key": "candidate_variant", "low": 1, "high": 5,
         "measures": ["candidate_improved", "scaffold_count_delta",
                      "fingerprint_diversity_delta", "mean_qed_delta"]},
        {"event": "molecular_generation_benchmark", "strategy": "", "key": "experiment_seed", "low": 20260901, "high": 20260999, "measures": ["validity", "uniqueness", "mean_qed"]},
        {"event": "molecular_synth_baseline_benchmark", "strategy": "", "key": "experiment_seed", "low": 20260901, "high": 20260999, "measures": ["mean_qed", "mean_sa", "uniqueness"]},
        {"event": "molecular_docking_holdout", "strategy": "molecular_1bvr_docking_holdout",
         "key": "ligands_per_arm", "low": 2, "high": 6,
         "measures": ["baseline_mean_vina_score", "candidate_mean_vina_score",
                      "candidate_minus_baseline_docking_score"]}],
    "molecular_dynamics_study": [
        {"event": "continuous_project_step", "strategy": "03_md_timestep_stability", "key": "candidate_variant", "low": 1, "high": 5, "measures": ["worst_relative_energy_drift", "stable_simulations"]},
        {"event": "continuous_project_step", "strategy": "03_md_temperature_sweep", "key": "candidate_variant", "low": 1, "high": 5, "measures": ["worst_relative_energy_drift", "stable_simulations"]}],
    "literature_github_learning": [
        {"event": "external_knowledge_scout", "strategy": "", "key": "query_variant", "low": 1, "high": 6,
         "measures": ["repositories_acquired", "papers_acquired", "repository_files_read", "paper_pdf_pages_read"]},
        {"event": "continuous_project_step", "strategy": "04_adapter_contract", "key": "source_variant", "low": 1, "high": 8,
         "measures": ["concepts_mapped", "partner_contracts_present", "source_files_read"]}],
    "hermes_partner_explore": [
        {"event": "continuous_project_step", "strategy": "05_failure_path_regression", "key": "code_variant", "low": 1, "high": 8,
         "measures": ["sources_read", "focused_regression_passed", "gaps_identified"]},
        {"event": "continuous_project_step", "strategy": "05_candidate_gap_matrix", "key": "code_variant", "low": 1, "high": 8,
         "measures": ["sources_read", "focused_regression_passed", "gaps_identified", "candidate_specs"]}],
}


def _rows(root: Path, project_id: str) -> list[dict[str, Any]]:
    path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    output: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-1000:]
    except OSError:
        return output
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and value.get("project_id") == project_id:
            output.append(value)
    return output


PROJECT_INSTANCES = {
    "xiaohongshu_operations": "01",
    "molecular_generation": "02",
    "molecular_dynamics_study": "03",
    "literature_github_learning": "04",
    "hermes_partner_explore": "05",
}


def _llm(prompt: str, *, root: Path, project_id: str,
         purpose: str = "hypothesis_candidate_design") -> dict[str, Any]:
    try:
        from partner.adapters.direct_api import chat
        raw = chat(prompt, purpose=purpose, max_tokens=1800,
                   temperature=0.2, timeout=90, workspace=str(root),
                   instance_id=PROJECT_INSTANCES.get(project_id, ""),
                   project_id=project_id, event_type="project_hypothesis_propose")
        cleaned = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S | re.I)
        match = re.search(r"\{.*\}", cleaned, re.S)
        value = json.loads(match.group(0)) if match else {}
        return value if isinstance(value, dict) else {}
    except Exception as exc:
        return {"error": type(exc).__name__}


def _directory(root: Path) -> Path:
    return root / "share/mind/governance/experience_guided_policy/project_candidates"


def _latest(root: Path, project_id: str) -> tuple[Path | None, dict[str, Any]]:
    values: list[tuple[float, Path, dict[str, Any]]] = []
    for path in _directory(root).glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if value.get("project_id") == project_id:
            values.append((path.stat().st_mtime, path, value))
    if not values:
        return None, {}
    _, path, value = max(values, key=lambda row: row[0])
    return path, value


def _evaluate(path: Path, candidate: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    measures = [str(value) for value in candidate.get("measurement_contract") or [] if str(value)]
    if not measures:
        candidate.update({"decision": "rejected", "production_effective": False,
                          "rejection_reason": "missing_event_measurement_contract",
                          "evaluated_at": now_iso()})
        atomic_json(path, candidate)
        return candidate
    candidate_id = str(candidate.get("candidate_id") or "")
    samples = [row for row in rows
               if str((row.get("action") or {}).get("selection_arm_id")
                      or (row.get("action") or {}).get("experience_candidate_id")
                      or (row.get("action") or {}).get("native_action_id") or "")
               == candidate_id]
    if not samples:
        return candidate
    evidence = "\n".join(str(item)
                           for row in samples
                           for item in ((row.get("outcome") or {}).get("evidence") or []))
    observed = [measure for measure in measures if measure in evidence]
    if not observed:
        candidate.update({"decision": "rejected", "production_effective": False,
                          "rejection_reason": "measurement_contract_not_observed",
                          "canary_samples": len(samples), "evaluated_at": now_iso()})
        atomic_json(path, candidate)
        return candidate
    rewards = [float(row.get("reward") or 0.0) for row in samples]
    mean = sum(rewards) / len(rewards)
    baseline = float(candidate.get("baseline_mean_reward") or -0.1)
    candidate.update({"canary_samples": len(rewards), "canary_mean_reward": round(mean, 4),
                      "reward_gain": round(mean - baseline, 4), "evaluated_at": now_iso()})
    if mean <= 0:
        candidate.update({"decision": "rejected", "production_effective": False,
                          "rejection_reason": "non_positive_verified_terminal_reward"})
    elif len(rewards) >= 3 and mean - baseline >= 0.15:
        # Canary evidence may nominate a matched experiment, but cannot promote
        # itself. Baseline/candidate execution and the promotion ledger are a
        # separate Event-first gate.
        candidate.update({"decision": "ready_for_matched_experiment",
                          "production_effective": False})
    else:
        candidate.update({"decision": "continue_canary", "production_effective": False})
    atomic_json(path, candidate)
    return candidate


def _option(candidate: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    params = dict(candidate.get("parameters") or {})
    params.update({"experience_candidate_id": candidate["candidate_id"],
                   "hypothesis": candidate.get("hypothesis", ""),
                   "falsifier": candidate.get("falsifier", ""),
                   "expected_observation": candidate.get("expected_observation", ""),
                   "measurement_contract": list(candidate.get("measurement_contract") or [])})
    return str(candidate["event_type"]), params


def _baseline(rows: list[dict[str, Any]], event_type: str) -> float:
    values = [float(row.get("reward") or 0.0) for row in rows
              if event_type in list((row.get("action") or {}).get("event_types") or [])
              and not str((row.get("action") or {}).get("native_action_id") or "").startswith("hypothesis_")]
    return round(sum(values[-10:]) / len(values[-10:]), 4) if values else -0.1


def _grammar_index(value: Any, grammar: list[dict[str, Any]], proposal: dict[str, Any] | None = None) -> int:
    """Accept an integer index or an exact Event/strategy identifier from the LLM."""
    try:
        index = int(value)
        if 0 <= index < len(grammar):
            return index
    except (TypeError, ValueError):
        pass
    normalized = str(value or "").strip()
    matches = [index for index, row in enumerate(grammar)
               if normalized in {str(row.get("event") or ""),
                                 str(row.get("strategy") or "")}]
    if len(matches) == 1:
        return matches[0]
    # Some models return the shared Event name while naming the exact strategy
    # elsewhere in their structured answer. Resolve only an exact, unique
    # allow-listed strategy occurrence; never use fuzzy semantic matching.
    if len(matches) > 1 and proposal:
        serialized = json.dumps(proposal, ensure_ascii=False)
        narrowed = [index for index in matches
                    if str(grammar[index].get("strategy") or "")
                    and str(grammar[index]["strategy"]) in serialized]
        if len(narrowed) == 1:
            return narrowed[0]
    raise ValueError("grammar_index_not_resolved")


def candidate_options(root: Path, project_id: str,
                      project_steps: int) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    """Return an active Candidate, or propose one after three negative rounds."""
    rows = _rows(root, project_id)
    path, candidate = _latest(root, project_id)
    if path and candidate:
        candidate = _evaluate(path, candidate, rows)
        if candidate.get("decision") in {"proposed_canary", "continue_canary"}:
            return [_option(candidate)], candidate
        last_step = int(candidate.get("proposed_project_step") or 0)
        cooldown = 1 if project_id == "molecular_generation" else 3
        if project_steps - last_step < cooldown:
            return [], candidate
    negative_window = 2 if project_id == "molecular_generation" else 3
    recent = rows[-negative_window:]
    grammar = GRAMMARS.get(project_id, [])
    if len(recent) < negative_window or not grammar:
        return [], {}
    if any(float(row.get("reward") or 0.0) > 0 for row in recent):
        return [], {}
    compact = [{"action": (row.get("action") or {}).get("native_action_id"),
                "events": (row.get("action") or {}).get("event_types"),
                "reward": row.get("reward"),
                "duplicate": (row.get("outcome") or {}).get("duplicate_outcome"),
                "failure": (row.get("outcome") or {}).get("failure_mechanism")}
               for row in recent]
    prompt = """You design one bounded Partner project intervention. The grammar's measures are the
only facts the Event can observe. Do not name tools, causal effects, scientific quantities, or success
criteria outside those measures. Your prose is advisory: the runtime will replace hypothesis and
falsifier with a machine-grounded measurement contract.
Separate observed facts, interpretation, and unknowns. Retrieve analogies but do not treat similarity as causality.
State a main hypothesis, an opposing hypothesis, and evidence that would falsify the main one.
Choose exactly one grammar index and one bounded integer variant. The action must create new business evidence,
not merely another report. Classify the intervention as business_progress, external_active_learning, or
partner_self_evolution. Return strict JSON with grammar_index, variant, hypothesis, opposing_hypothesis,
falsifier, expected_observation, rationale, intervention_kind.
""" + "\n\n" + project_reasoning_contract() + "\n\n" + json.dumps(
        {"project_id": project_id, "history": compact, "grammar": grammar}, ensure_ascii=False)
    proposal = _llm(prompt, root=root, project_id=project_id,
                    purpose="hypothesis_candidate_design")
    try:
        index = _grammar_index(proposal.get("grammar_index"), grammar, proposal)
        selected = grammar[index]
        variant = max(int(selected["low"]), min(int(selected["high"]), int(proposal.get("variant"))))
    except (IndexError, KeyError, TypeError, ValueError):
        return [], {"decision": "inconclusive", "llm": proposal}
    critic = _llm(
        "Audit this bounded project proposal against the exact Event grammar. Identify unsupported claims, "
        "the strongest counterexample, and whether the proposed prose exceeds the declared measures. "
        "Return strict JSON with contract_mismatch, unsupported_claims, strongest_counterexample, "
        "recommended_belief_update. You cannot approve production.\n"
        + project_reasoning_contract() + "\n"
        + json.dumps({"proposal": proposal, "selected_grammar": selected}, ensure_ascii=False),
        root=root, project_id=project_id, purpose="hypothesis_candidate_critic",
    )
    identity = hashlib.sha256(json.dumps({"project": project_id, "step": project_steps,
                                          "selected": selected, "variant": variant,
                                          "hypothesis": proposal.get("hypothesis")},
                                         ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    candidate_id = f"hypothesis_{identity}"
    params = {str(selected["key"]): variant}
    if selected.get("strategy"):
        params["strategy_id"] = str(selected["strategy"])
    measures = [str(value) for value in selected.get("measures") or [] if str(value)]
    event_label = "/".join(value for value in
                           (str(selected["event"]), str(selected.get("strategy") or "")) if value)
    machine_hypothesis = (
        f"将 {selected['key']} 设为 {variant} 并执行 {event_label}，应产生非重复终态，"
        f"且至少一个声明指标相对冻结基线改善：{', '.join(measures)}。"
    )
    machine_falsifier = (
        "出现 duplicate_outcome=true，或终态 Reward 不高于冻结基线，"
        f"或未观测到声明指标（{', '.join(measures)}），即否证该候选。"
    )
    candidate = {"schema_version": 2, "candidate_id": candidate_id,
                 "project_id": project_id, "event_type": selected["event"],
                 "parameters": params, "hypothesis": machine_hypothesis,
                 "opposing_hypothesis": "参数变化不会带来非重复证据或声明指标改善。",
                 "falsifier": machine_falsifier,
                 "expected_observation": {"declared_measures": measures,
                                          "terminal_reward_compared_to": "frozen_baseline_mean_reward"},
                 "measurement_contract": measures,
                 "llm_advisory": {"hypothesis": str(proposal.get("hypothesis") or ""),
                                  "opposing_hypothesis": str(proposal.get("opposing_hypothesis") or ""),
                                  "falsifier": str(proposal.get("falsifier") or ""),
                                  "expected_observation": str(proposal.get("expected_observation") or "")},
                 "llm_critic": critic,
                 "llm_trace": {"calls": 2, "roles": ["proposal", "independent_contract_critic"]},
                 "rationale": str(proposal.get("rationale") or ""),
                 "intervention_kind": ("external_active_learning"
                                       if selected["event"] == "external_knowledge_scout"
                                       else "business_progress"),
                 "baseline_mean_reward": _baseline(rows, str(selected["event"])),
                 "proposed_project_step": int(project_steps),
                 "isolation_validation": {"event_allowlisted": True,
                                          "parameters_bounded": True,
                                          "measurement_contract_declared": bool(measures),
                                          "arbitrary_code_execution": False},
                 "decision": "proposed_canary",
                 "production_effective": False, "created_at": now_iso()}
    path = _directory(root) / f"{candidate_id}.json"
    atomic_json(path, candidate)
    append_evolution_event(
        str(root), "candidate/proposed", subject_id=candidate_id,
        project_id=project_id,
        payload={"candidate_id": candidate_id, "scope": "bounded_project_event_parameters",
                 "event_type": selected["event"], "parameters": params,
                 "production_effective": False},
        evidence_refs=[str(path)], idempotency_key=f"project-hypothesis:{candidate_id}",
    )
    return [_option(candidate)], candidate
