"""Governance bridge for one bounded manual task.

Manual mode never auto-enqueues the next action.  It does, however, persist a
truthful IterationReceipt so a later user-triggered round can resume from real
artifacts instead of prose in a chat log.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .evolution_loop import record_issue
from .project_loop import record_iteration
from .scheduler import ROLES
from .models import now_iso
from .storage import append_jsonl, instance_id, latest_receipt, workspace_root


def _handoff_present(previous_artifacts: list[str], inputs: list[str]) -> bool:
    prior = {str(Path(value).expanduser()) for value in previous_artifacts}
    current = {str(Path(value).expanduser()) for value in inputs}
    if prior & current or {Path(v).name for v in prior} & {Path(v).name for v in current}:
        return True
    # QQ delivery storage prefixes a timestamp/random token to the original
    # filename.  This is the only durable copy available for some receipts
    # created before manual evidence archival; match the exact original
    # basename as a terminal underscore-delimited suffix, never a substring.
    prior_names = {Path(value).name for value in prior}
    current_names = {Path(value).name for value in current}
    return any(current_name.endswith("_" + prior_name)
               for prior_name in prior_names for current_name in current_names)


def _content_fingerprint(paths: list[str]) -> str:
    digest = hashlib.sha256()
    for raw in sorted(paths):
        path = Path(raw)
        digest.update(path.name.encode("utf-8", errors="replace"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(str(path).encode("utf-8", errors="replace"))
    return digest.hexdigest()[:24]


_VOLATILE_EVIDENCE = re.compile(
    r"(?:project_action|receipt|task|episode|candidate|experiment)_[a-z0-9_-]+"
    r"|(?:[a-z]:)?[/\\][^\s；;,，。]+"
    r"|\b\d{4}-\d{1,2}-\d{1,2}(?:[t\s][0-9:.+-]+)?\b"
    r"|(?<![a-z])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?(?![a-z])",
    re.I,
)


def _stable_semantic_finding(value: str) -> str:
    """Strip run identity and raw measurements from one business claim."""
    text = re.sub(r"\s+", " ", str(value)).strip().lower()
    text = re.sub(r"[；;]?\s*动作选择[=:：]\s*project_action_[a-z0-9_-]+", "", text,
                  flags=re.I)
    text = re.sub(r"[；;]?\s*学习干预[=:：]\s*(?:true|false|是|否)", "", text,
                  flags=re.I)
    text = re.sub(r"[；;]?\s*(?:selection_reason|选择原因)[=:：][^；;]+", "", text,
                  flags=re.I)
    text = _VOLATILE_EVIDENCE.sub("<volatile>", text)
    return re.sub(r"(?:<volatile>[\s:：=/_-]*)+", "<volatile>", text).strip()


def _explicit_improvement_claim(findings: list[str]) -> bool:
    joined = " ".join(str(value).lower() for value in findings)
    return any(token in joined for token in (
        "improvement_vs_baseline", "counterfactual_passed", "baseline-fail/candidate-pass",
        "相对基线改善", "相较基线改善", "反事实验证通过",
    ))


def _native_action_selection(root: Path, findings: list[str], *, iid: str = "",
                             project_id: str = "", actions: list[str] | None = None) -> dict[str, Any]:
    match = re.search(r"动作选择[=:：]\s*(project_action_[a-z0-9_-]+)",
                      " ".join(findings), re.I)
    selection_id = match.group(1) if match else ""
    canonical = root / "share/mind/governance/experience_guided_policy/native_action_selections"
    candidates = ([canonical / f"{selection_id}.json"]
                  if selection_id else [])
    # Some domain Events predate action-selection fields in their textual
    # summary (notably the molecular stages). One instance runs one task at a
    # time, so its newest matching canonical decision is the authoritative
    # fallback until those Event result contracts are migrated.
    if not candidates and iid and project_id:
        candidates = sorted(canonical.glob("*.json"),
                            key=lambda path: path.stat().st_mtime, reverse=True)[:50]
    for candidate in candidates:
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if (isinstance(value, dict)
                    and (not iid or str(value.get("instance_id") or "") == iid)
                    and (not project_id or str(value.get("project_id") or "") == project_id)
                    and (not actions or str(value.get("event_type") or "") in actions)):
                return value
        except (OSError, TypeError, ValueError):
            continue
    return {}


def _candidate_no_change(paths: list[str]) -> dict[str, Any] | None:
    """Return durable evidence that a bounded code probe found no new change.

    This is intentionally artifact-based: prose such as ``no_new_candidate``
    must never be enough to alter reward or project state.
    """
    for raw in paths:
        path = Path(str(raw))
        if path.suffix.lower() != ".json" or not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        if (str(value.get("decision") or "") == "no_new_candidate"
                and value.get("production_effective") is False):
            return {"path": str(path), "candidate_id": str(value.get("candidate_id") or ""),
                    "existing_production_effective": bool(
                        value.get("existing_production_effective"))}
    return None


def _source_families(inputs: list[str]) -> list[str]:
    families: list[str] = []
    for value in inputs:
        lower = str(value).lower()
        family = ""
        for candidate in ("deepseek-harness", "openai-codex", "targetdiff", "github", "literature"):
            if candidate in lower:
                family = candidate
                break
        family = family or Path(value).suffix.lower().lstrip(".") or "local_source"
        if family not in families:
            families.append(family)
    return families


def _marker(goal: str, key: str) -> str:
    match = re.search(rf"\[{re.escape(key)}=([^\]]+)\]", str(goal or ""))
    return str(match.group(1)).strip() if match else ""


def _promoted_manual_policy(workspace: str, project_id: str) -> dict[str, str]:
    path = workspace_root(workspace) / "share" / "mind" / "governance" / "experience_guided_policy" / "control_policy.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    promoted = payload.get("promoted") or {}
    semantic_key = f"{project_id}:planning.semantic_preflight"
    semantic_strategy = str(promoted.get(semantic_key) or "")
    if semantic_strategy == "candidate_preflight_contract_v2":
        return {"strategy_id": semantic_strategy, "policy_decision": semantic_key,
                "policy_arm": "production", "experiment_id": "experiment_6ef2b9be620b"}
    decision_key = f"{project_id}:manual_final_artifact_truth"
    strategy = str(promoted.get(decision_key) or "")
    if strategy != "manual_stable_truth_audit_v2":
        return {}
    return {"strategy_id": strategy, "policy_decision": decision_key,
            "policy_arm": "production", "experiment_id": "experiment_5af99917bea9"}


def _task_canary_policy(workspace: str, task_id: str) -> dict[str, str]:
    """Recover the exact planner-selected canary arm from durable task state."""
    if not task_id:
        return {}
    path = Path(workspace) / "state" / "tasks" / task_id / "task_instance.json"
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
        value = dict((task.get("metadata") or {}).get("planner_experiment_intervention") or {})
    except (OSError, TypeError, ValueError):
        return {}
    if (value.get("route") != "research_adoption_production_canary_v1"
            or value.get("active") is not True):
        return {}
    return {
        "strategy_id": str(value.get("strategy_id") or ""),
        "policy_decision": str(value.get("decision_key") or ""),
        "policy_arm": "production",
        "experiment_id": str(value.get("canary_id") or value.get("experiment_id") or ""),
        "canary_id": str(value.get("canary_id") or ""),
    }


def _candidate_truth_audit(inputs: list[str], artifacts: list[str],
                           actions: list[str] | None = None,
                           *, require_claim_ledger: bool = False) -> dict[str, Any]:
    """Verify final-report source/quote pairs against the files actually read."""
    required_sources = {str(Path(value).resolve()) for value in inputs if Path(value).is_file()}
    pairs: list[dict[str, str]] = []
    false_claims: list[str] = []
    capability_contradictions: list[dict[str, str]] = []
    pattern = re.compile(
        r"^[ \t]*(?:[-*>][ \t]*)?`?source_path`?\s*[:：]\s*([^\n]+?)\s*$\n"
        r"(?:^[ \t]*(?:[-*>][ \t]*)?`?source_identity`?\s*[:：]\s*[^\n]*\s*$\n)?"
        r"^[ \t]*(?:[-*>][ \t]*)?`?evidence_quote`?\s*[:：]\s*([^\n]+?)\s*$",
        re.I | re.M,
    )
    for artifact in artifacts:
        path = Path(artifact)
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        false_capability = re.search(
            r"(?:当前回合|本回合|当前环境|本环境|我|我们).{0,60}"
            r"(?:未配置|没有|缺少|无可用|无法使用|不可用|无法).{0,36}"
            r"(?:shell|file[-_ ]?write|write_to_file|写文件|文件写入|写入工具|写入能力|执行通道)"
            r"|(?:this turn|current (?:turn|environment)|this environment|\bI\b).{0,60}"
            r"(?:no|not available|cannot|can't|unable).{0,36}(?:shell|file[-_ ]?write|write to file)",
            text, re.I | re.S,
        )
        actual_write = any(value in {"create_file", "atomic_write_artifact", "atomic_write_file", "write_file"}
                           for value in (actions or [])) or path.is_file()
        if false_capability and actual_write:
            false_claims.append(str(path))
            capability_contradictions.append({
                "artifact": str(path), "claim": false_capability.group(0)[:160],
                "runtime_evidence": "artifact exists and the task executed a file-write event",
            })
        for source, quote in pattern.findall(text):
            clean_source = source.strip().strip("`\"'“”").rstrip("，,；;。)")
            source_path = str(Path(clean_source).resolve())
            clean_quote = quote.strip().strip("`\"'“”")
            pairs.append({"source_path": source_path, "evidence_quote": clean_quote})

    verified_sources: set[str] = set()
    invalid_pairs: list[dict[str, str]] = []
    for pair in pairs:
        source_path = pair["source_path"]
        quote = pair["evidence_quote"]
        try:
            from .research_learning import (
                normalize_research_evidence_text, read_research_source_text,
            )
            source_text = normalize_research_evidence_text(read_research_source_text(source_path))
        except (OSError, ValueError):
            invalid_pairs.append(pair)
            continue
        if (source_path not in required_sources or len(quote) < 20
                or normalize_research_evidence_text(quote) not in source_text):
            invalid_pairs.append(pair)
        else:
            verified_sources.add(source_path)
    missing_sources = sorted(required_sources - verified_sources)
    passed = bool(required_sources) and not false_claims and not invalid_pairs and not missing_sources
    result = {
        "passed": passed,
        "required_sources": sorted(required_sources),
        "verified_sources": sorted(verified_sources),
        "pair_count": len(pairs),
        "missing_sources": missing_sources,
        "invalid_pairs": invalid_pairs,
        "false_capability_claim_artifacts": false_claims,
        "capability_contradictions": capability_contradictions,
    }
    if require_claim_ledger:
        from partner.mind.claim_ledger import audit_claim_artifacts

        claim_audit = audit_claim_artifacts(
            artifacts, named_input_sources=inputs, require_claims=True,
        )
        result["claim_level"] = claim_audit
        result["passed"] = bool(result["passed"] and claim_audit.get("passed"))
    return result


def _verified_candidate_sources(workspace: str, task_id: str) -> list[str]:
    """Recover only source files proven by a completed research Candidate Event.

    The model cannot widen the truth-gate input set by mentioning a path in its
    report.  A source is admitted only when the persisted ``execute_candidate``
    result contains typed evidence, the path is inside the research allow-list,
    the current bytes match the recorded SHA-256, and the recorded quote is a
    real substring.  This keeps Event-first provenance across the planner /
    executor boundary without treating generated context as ground truth.
    """
    if not task_id:
        return []
    root = workspace_root(workspace).resolve()
    allowed_roots = [
        (root / "external" / "code").resolve(),
        (root / "external" / "literature").resolve(),
    ]
    task_dir = Path(workspace) / "state" / "tasks" / task_id
    admitted: list[str] = []
    for result_path in sorted(task_dir.glob("_step_*.result.json")):
        try:
            envelope = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if str(envelope.get("event_type") or "") != "execute_candidate" or not envelope.get("ok"):
            continue
        result = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
        if (not result.get("ok")
                or str(result.get("candidate_event_type") or "") != "research_adoption_context_shadow"):
            continue
        for evidence in result.get("verified_source_evidence") or []:
            if not isinstance(evidence, dict):
                continue
            try:
                source = Path(str(evidence.get("source_path") or "")).resolve()
                allowed = any(source == base or base in source.parents for base in allowed_roots)
                body = source.read_bytes()
            except (OSError, ValueError):
                continue
            expected_hash = str(evidence.get("source_sha256") or "").lower()
            quote = str(evidence.get("evidence_quote") or "").strip()
            if (not allowed or len(expected_hash) != 64
                    or hashlib.sha256(body).hexdigest() != expected_hash
                    or len(quote) < 20):
                continue
            try:
                from .research_learning import (
                    normalize_research_evidence_text, read_research_source_text,
                )
                text = normalize_research_evidence_text(read_research_source_text(source))
            except (OSError, ValueError):
                continue
            if normalize_research_evidence_text(quote) not in text:
                continue
            value = str(source)
            if value not in admitted:
                admitted.append(value)
    return admitted


def preflight_manual_artifact_truth(workspace: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run the promoted final-artifact truth gate before external delivery.

    This check is read-only.  It deliberately creates no Issue, trajectory or
    Receipt; the normal stop/governance path persists the final outcome once.
    """
    iid = instance_id(workspace)
    project_id = str(params.get("project_id") or ROLES.get(iid) or "").strip()
    goal = str(params.get("goal") or "").strip()
    native_project = bool(
        "[instance_native=true]" in goal and "[native_kind=project]" in goal
    )
    inputs = [str(value) for value in params.get("inputs") or [] if str(value).strip()]
    for value in _verified_candidate_sources(workspace, str(params.get("task_id") or "")):
        if value not in inputs:
            inputs.append(value)
    artifacts = [str(value) for value in params.get("artifacts") or [] if str(value).strip()]
    actions = [str(value) for value in params.get("actions_executed") or [] if str(value).strip()]
    promoted = _promoted_manual_policy(workspace, project_id) if not _marker(goal, "policy_arm") else {}
    effective_goal = goal
    if str(params.get("action_selection_id") or "").strip():
        effective_goal += (
            f" [action_selection_id={str(params['action_selection_id']).strip()}]"
        )
    if promoted:
        effective_goal += " " + " ".join(f"[{key}={value}]" for key, value in promoted.items())
    policy_arm = _marker(effective_goal, "policy_arm")
    applicable = bool(
        not native_project
        and
        policy_arm in {"baseline", "candidate", "production"}
        and (inputs or "execute_candidate" in actions)
        and any(Path(value).suffix.lower() in {".md", ".txt"} for value in artifacts)
    )
    if not applicable:
        return {"applicable": False, "passed": True, "production_mutation": False}
    audit = _candidate_truth_audit(
        inputs, artifacts, actions,
        require_claim_ledger=bool(policy_arm in {"candidate", "production"}),
    )
    return {"applicable": True, **audit, "production_mutation": False}


def _task_partial_artifacts(workspace: str, task_id: str) -> list[str]:
    """Recover real task-local outputs without treating read inputs as outputs."""
    task_dir = Path(workspace) / "state" / "tasks" / task_id
    found: list[str] = []
    for result_path in sorted(task_dir.glob("_step_*.result.json")):
        try:
            row = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        event_type = str(row.get("event_type") or "")
        if event_type in {"atomic_inspect_file", "read_file", "list_directory"}:
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        values = result.get("files") or []
        if isinstance(values, str):
            values = [values]
        path_value = result.get("path")
        if isinstance(path_value, str):
            values = [*values, path_value]
        for value in values:
            path = Path(str(value))
            if not path.is_absolute():
                path = task_dir / path
            try:
                resolved = path.resolve()
                if resolved.is_relative_to(task_dir.resolve()) and resolved.is_file() and str(resolved) not in found:
                    found.append(str(resolved))
            except (OSError, ValueError):
                continue
    return found


def _task_failure_signature(workspace: str, task_id: str) -> tuple[str, str]:
    task_dir = Path(workspace) / "state" / "tasks" / task_id
    log_path = task_dir / "task_log.jsonl"
    owner = mechanism = ""
    try:
        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError, TypeError):
        rows = []
    for row in reversed(rows):
        failures = row.get("failures") or []
        for failure in failures if isinstance(failures, list) else []:
            if not isinstance(failure, dict):
                continue
            owner = str(failure.get("failure_owner") or owner)
            mechanism = str(failure.get("mechanism") or mechanism)
            if mechanism:
                return owner, mechanism
        owner = str(row.get("failure_owner") or owner)
        mechanism = str(row.get("mechanism") or mechanism)
        if mechanism:
            return owner, mechanism
        event_name = str(row.get("event") or "").lower()
        error_text = str(row.get("error") or "").lower()
        if event_name == "manual_plan_preflight_failed":
            return "planner_contract", "planning/semantic_preflight"
        if event_name in {"harness_batch_plan_failed", "batch_plan_handler_failed"}:
            if "timeout" in error_text or "time limit" in error_text or "超过" in error_text:
                return "environment", "planning/batch_planner_timeout"
            return "planner_contract", "planning/batch_planner_exception"
    return owner, mechanism


def _native_business_measurement(workspace: str, task_id: str,
                                 action_id: str) -> dict[str, Any]:
    """Project-specific terminal gate derived from the real Event result.

    A generated PDF and a completed handler prove execution, not business
    improvement.  For actions with an explicit measurement contract, require
    that contract here before granting ``business_progress`` Reward.
    """
    task_dir = Path(workspace) / "state" / "tasks" / task_id
    payload: dict[str, Any] = {}
    for path in sorted(task_dir.glob("_step_*.result.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        outer = row.get("result") if isinstance(row.get("result"), dict) else {}
        inner = outer.get("result") if isinstance(outer.get("result"), dict) else outer
        inner_strategy = str(inner.get("strategy_id") or "")
        detail_metrics = inner.get("metrics") if isinstance(inner.get("metrics"), dict) else {}
        method_id = str(detail_metrics.get("method_id") or "")
        if not inner_strategy and method_id:
            inner_strategy = "molecular_" + method_id
        if not inner_strategy and str(row.get("event_type") or "") == "molecular_docking_holdout":
            inner_strategy = "molecular_1bvr_docking_holdout"
        if not action_id or inner_strategy == action_id:
            payload = inner
            if inner_strategy:
                payload = {**payload, "strategy_id": inner_strategy}
            break
    if not payload:
        return {"applicable": False, "passed": True, "reason": "no_typed_measurement_contract"}
    metrics = payload.get("business_metrics") if isinstance(payload.get("business_metrics"), dict) else {}
    passed = bool(payload.get("ok", True))
    reason = "event_reported_ok"
    if action_id == "01_source_fact_check":
        passed = int(metrics.get("sources_reachable") or 0) >= 1
        reason = "at_least_one_source_must_be_reachable"
    elif action_id == "01_evidence_backed_draft":
        passed = (int(metrics.get("grounded_drafts") or 0) >= 1
                  and int(metrics.get("claims_with_source") or 0) >= 1)
        reason = "draft_requires_claim_level_source"
    elif action_id == "03_md_integrator_comparison":
        passed = (int(metrics.get("integrators_compared") or 0) >= 2
                  and int(metrics.get("stable_simulations") or 0) >= 1)
        reason = "matched_integrator_comparison_required"
    elif action_id == "04_adoption_effect_probe":
        passed = (int(metrics.get("adoption_contexts_consumed") or 0) >= 1
                  and int(metrics.get("adoption_checks_passed") or 0)
                  == int(metrics.get("adoption_checks_total") or -1))
        reason = "typed_external_evidence_must_change_later_action_context"
    elif action_id in {
        "molecular_scaffold_cap", "molecular_pareto_diverse",
        "molecular_maxmin_fingerprint", "molecular_scaffold_round_robin",
        "molecular_llm_greedy_dsl",
    }:
        passed = bool(int(metrics.get("candidate_improved") or 0))
        reason = "candidate_must_beat_frozen_molecular_baseline"
    elif action_id == "molecular_1bvr_docking_holdout":
        passed = float(metrics.get("candidate_minus_baseline_docking_score") or 0.0) < 0.0
        reason = "candidate_docking_score_must_be_lower_than_matched_baseline"
    return {"applicable": True, "passed": bool(passed), "reason": reason,
            # The discovery pass intentionally calls this function with an
            # empty action_id. Preserve the typed Event strategy found in the
            # result; returning the empty filter here used to erase identity
            # and disabled both measurement gates and duplicate detection.
            "strategy_id": str(payload.get("strategy_id") or action_id),
            "metrics": metrics}


_DETERMINISTIC_PROBE_ACTIONS = {
    "05_event_contract_inventory",
    "05_failure_path_regression",
    "05_candidate_gap_matrix",
}


def _resolve_project_id(workspace: str, iid: str, params: dict[str, Any]) -> str:
    """Resolve project identity from the task before consulting old role defaults."""
    explicit = str(params.get("project_id") or "").strip()
    if explicit:
        return explicit
    goal = str(params.get("goal") or "")
    marked = _marker(goal, "project_id")
    if marked:
        return marked
    try:
        from .instance_native import load_state
        current = load_state(workspace_root(workspace), iid).project_id
        if current:
            return current
    except Exception:
        pass
    return str(ROLES.get(iid) or "")


def _record_manual_trajectory(workspace: str, *, iid: str, project_id: str, task_id: str,
                              receipt: dict[str, Any], inputs: list[str], artifacts: list[str],
                              actions: list[str], findings: list[str], goal: str = "",
                              evidence_refs: list[str] | None = None,
                              truth_audit: dict[str, Any] | None = None,
                              outcome_status: str = "completed",
                              false_success: bool | None = None,
                              continuation_requested: bool = False,
                              handoff_consumed: bool = False,
                              failure_owner: str = "", failure_mechanism: str = "",
                              monitor_only: bool = False,
                              learning_only: bool = False,
                              completion_dimensions: dict[str, Any] | None = None) -> dict[str, Any]:
    root = workspace_root(workspace)
    path = root / "share" / "mind" / "governance" / "experience_guided_policy" / "trajectories.jsonl"
    trajectory_id = "traj_manual_" + hashlib.sha256(f"{iid}|{task_id}".encode()).hexdigest()[:16]
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("trajectory_id") == trajectory_id:
                return {"ok": True, "status": "already_recorded", "trajectory": row, "path": str(path)}
    except (OSError, ValueError):
        pass
    source_families = _source_families(inputs)
    evidence_refs = [str(value) for value in (evidence_refs or []) if str(value).strip()]
    fingerprint = _content_fingerprint([*artifacts, *evidence_refs])
    meaningful = [value for value in actions if value not in {"send_user_text", "push_files", "batch_plan"}]
    research_learning = any(
        value.startswith("research_active_learning_")
        or value.startswith("agent_active_learning_")
        or value.startswith("learning_")
        or value in {"external_knowledge_scout", "molecular_external_activity_acquire"}
        or value in {"sprint18_learning_cycle", "targetdiff_active_learning",
                     "targetdiff_active_robustness", "targetdiff_uncertainty_diagnostic",
                     "targetdiff_uncertainty_candidate", "research_adoption_context_shadow"}
        for value in meaningful
    )
    adoption_action = any(value in meaningful for value in {
        "research_adoption_context_shadow", "molecular_external_activity_acquire",
    })
    candidate_no_change_action = "learning_candidate_no_change" in meaningful
    isolated_learning = bool(
        (bool(_marker(goal, "sprint18"))
         or ("[instance_native=true]" in goal and any(marker in goal for marker in (
             "[native_kind=learning]", "[native_kind=external_learning]"))))
        and research_learning
    )
    generic_findings = {
        "已完成本地微计划执行", "任务通过 harness 与交付硬门",
        "isolated learning observation completed",
    }
    substantive_findings = [value for value in findings if value.strip().lower() not in generic_findings]
    native_runtime_task = "[instance_native=true]" in goal
    explicit_selection_id = _marker(goal, "action_selection_id")
    selection_findings = list(substantive_findings)
    if explicit_selection_id:
        selection_findings.append(f"动作选择={explicit_selection_id}")
    selection = _native_action_selection(
        root, selection_findings,
        iid=(iid if native_runtime_task else ""),
        project_id=(project_id if native_runtime_task else ""), actions=actions,
    )
    selection_parameters = selection.get("parameters") or {}
    selection_arm_id = str(selection.get("arm_id") or "")
    native_action_id = str(
        selection_parameters.get("strategy_id")
        or selection.get("event_type") or ""
    )
    discovered = _native_business_measurement(workspace, task_id, "")
    if discovered.get("strategy_id"):
        native_action_id = str(discovered.get("strategy_id") or "")
    self_evolution_action = (
        native_action_id == "05_code_candidate_autonomous"
        or candidate_no_change_action
    )
    business_measurement = _native_business_measurement(
        workspace, task_id, native_action_id
    ) if native_action_id else {"applicable": False, "passed": True}
    semantic_findings = {
        _stable_semantic_finding(value)
        for value in substantive_findings if str(value).strip()
    }
    semantic_signature = hashlib.sha256(
        json.dumps({"action": native_action_id or actions,
                    "claims": sorted(semantic_findings)}, ensure_ascii=False,
                   sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    duplicate_outcome = False
    if semantic_findings and path.is_file():
        try:
            prior_rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()[-500:]]
        except (OSError, TypeError, ValueError):
            prior_rows = []
        for prior in reversed(prior_rows):
            if str(prior.get("project_id") or "") != project_id:
                continue
            prior_action = str((prior.get("action") or {}).get("native_action_id") or "")
            if native_action_id and prior_action and prior_action != native_action_id:
                continue
            if not native_action_id and list((prior.get("action") or {}).get("event_types") or []) != list(actions):
                continue
            if str((prior.get("outcome") or {}).get("status") or "") != "completed":
                continue
            # These 05 actions inspect the same source contracts and execute
            # the same bounded probes. A new task/Receipt/PDF path does not
            # make identical typed metrics new evidence. Treat it as a
            # duplicate even when presentation prose changed between runs.
            prior_measurement = (prior.get("outcome") or {}).get("business_measurement") or {}
            if (native_action_id in _DETERMINISTIC_PROBE_ACTIONS
                    and bool(business_measurement.get("metrics"))
                    and prior_measurement.get("metrics") == business_measurement.get("metrics")):
                duplicate_outcome = True
                break
            prior_findings = {
                _stable_semantic_finding(value)
                for value in (prior.get("outcome") or {}).get("evidence") or []
                if str(value).strip() and not str(value).startswith("receipt_id=")
            }
            if semantic_findings == prior_findings:
                duplicate_outcome = True
                break
    if duplicate_outcome and _explicit_improvement_claim(substantive_findings):
        duplicate_outcome = False
    business_progress = bool(
        not monitor_only and outcome_status == "completed"
        and not research_learning and not self_evolution_action
        and not isolated_learning and not learning_only and not duplicate_outcome
        and bool(business_measurement.get("passed", True))
        and artifacts and substantive_findings and meaningful and fingerprint
    )
    learning_progress = bool(
        not monitor_only and outcome_status == "completed" and research_learning
        and not candidate_no_change_action
        and not duplicate_outcome and (evidence_refs or (adoption_action and artifacts))
        and findings and meaningful and fingerprint
    )
    knowledge_adoption_progress = bool(
        learning_progress and adoption_action
        and artifacts and substantive_findings
    )
    self_evolution_observation = bool(
        not monitor_only and outcome_status == "completed" and self_evolution_action
        and artifacts and substantive_findings and fingerprint
    )
    self_evolution_progress = bool(
        self_evolution_observation and not duplicate_outcome
        and not any(token in " ".join(substantive_findings).lower()
                    for token in ("rejected", "inconclusive", "no_new_candidate",
                                  "production_effective=false"))
    )
    falsified_business_hypothesis = bool(
        not monitor_only and outcome_status == "completed"
        and business_measurement.get("applicable") is True
        and business_measurement.get("passed") is False
        and artifacts and substantive_findings and meaningful and fingerprint
    )
    accepted = outcome_status == "completed"
    full_artifact_contract = bool(accepted and artifacts)
    partial_artifact = bool(not accepted and artifacts)
    reward_components = {
        "accepted_completed": 0.05 if accepted and not monitor_only else 0.0,
        "artifact_contract": 0.05 if full_artifact_contract else 0.0,
        "partial_artifact": 0.05 if partial_artifact else 0.0,
        # A durable matched observation is valid learning evidence, but it is
        # not an external-channel delivery and must not receive that reward.
        "delivery_contract": 0.05 if receipt.get("delivery_confirmed") and not monitor_only else 0.0,
        "meaningful_event": 0.05 if meaningful and not monitor_only else 0.0,
        "business_progress": 0.45 if business_progress else 0.0,
        "learning_progress": 0.30 if learning_progress else 0.0,
        "self_evolution_progress": 0.30 if self_evolution_progress else 0.0,
        "novel_evidence": (0.20 if business_progress else
                           (0.10 if learning_progress or falsified_business_hypothesis else 0.0)),
        "handoff_consumed": 0.15 if handoff_consumed and not monitor_only else 0.0,
    }
    if falsified_business_hypothesis:
        # A completed falsification is useful evidence but a negative signal
        # for choosing this business arm again.  Normalize procedural credits
        # so the action's total Reward is exactly -0.1, rather than the generic
        # -0.45 reserved for execution/verification failure.
        positive = sum(value for value in reward_components.values() if value > 0)
        reward_components["falsified_hypothesis"] = round(-(positive + 0.1), 4)
    reward = (0.0 if monitor_only else (
        round(min(1.0, sum(reward_components.values())), 4)
        if falsified_business_hypothesis
        else (round(min(1.0, sum(reward_components.values())), 4)
        if (business_progress or learning_progress or self_evolution_progress)
        else (0.0 if self_evolution_observation and not duplicate_outcome
              else (-0.1 if accepted and duplicate_outcome
              else round(-0.45 + reward_components["partial_artifact"], 4))
             ))
    ))
    strategy_id = (_marker(goal, "strategy_id") or native_action_id
                   or "manual_stable_grounded_v1")
    policy_decision = (_marker(goal, "policy_decision")
                       or str(selection.get("policy_decision_key") or ""))
    policy_arm = (_marker(goal, "policy_arm")
                  or ("experience_guided_policy" if selection else ""))
    experiment_id = _marker(goal, "experiment_id")
    match_key = _marker(goal, "match_key")
    if failure_mechanism:
        action_identity = failure_mechanism
    elif any("pdf" in value for value in meaningful):
        action_identity = "pdf_output_reference"
    elif meaningful:
        action_identity = meaningful[0]
    else:
        action_identity = "generic_or_unobserved"
    row = {
        "schema_version": 3,
        "trajectory_id": trajectory_id,
        "campaign_id": "",
        "work_item_id": task_id,
        "project_id": project_id,
        "instance_id": iid,
        "kind": "manual_project_iteration",
        "state": {"source_families": source_families, "receipt_id": receipt.get("receipt_id", ""),
                  "delivery_confirmed": bool(receipt.get("delivery_confirmed")),
                  "continuation_requested": bool(continuation_requested),
                  "completion_dimensions": dict(completion_dimensions or {})},
        "action": {
            "action_key": f"{iid}:manual_project_iteration:{action_identity}",
            "event_types": actions,
            "strategy_id": strategy_id,
            "policy_decision": policy_decision,
            "policy_arm": policy_arm,
            "experiment_id": experiment_id,
            "match_key": match_key,
            "native_action_id": native_action_id,
            "selection_arm_id": selection_arm_id or native_action_id,
            "experience_candidate_id": str(
                selection_parameters.get("experience_candidate_id") or ""
            ),
            "action_selection_id": str(selection.get("selection_id") or ""),
        },
        "outcome": {
            "status": outcome_status, "artifacts": artifacts, "evidence_refs": evidence_refs,
            "evidence": [f"receipt_id={receipt.get('receipt_id', '')}", *findings],
            "outcome_fingerprint": fingerprint, "monitor_only": bool(monitor_only),
            "business_progress": business_progress, "learning_progress": learning_progress,
            "knowledge_adoption_progress": knowledge_adoption_progress,
            "self_evolution_observation": self_evolution_observation,
            "self_evolution_progress": self_evolution_progress,
            "candidate_no_change": candidate_no_change_action,
            "business_measurement": business_measurement,
            "novel_evidence": bool(
                business_progress or learning_progress or falsified_business_hypothesis
            ),
            "duplicate_outcome": bool(duplicate_outcome),
            "semantic_signature": semantic_signature,
            "handoff_consumed": bool(handoff_consumed),
            "false_success": (bool(false_success) if false_success is not None
                              else bool(truth_audit and not truth_audit.get("passed"))),
            "truth_audit": truth_audit or {},
            "artifact_completion": "full" if full_artifact_contract else ("partial" if partial_artifact else "none"),
            "failure_owner": (failure_owner or
                              ("business_hypothesis" if falsified_business_hypothesis else "")),
            "failure_mechanism": (failure_mechanism or
                                  ("business/hypothesis_falsified"
                                   if falsified_business_hypothesis else "")),
        },
        "reward": reward,
        "reward_components": reward_components,
        "policy_eligible": bool(
            not monitor_only and not isolated_learning
            and iid in {"01", "02", "03", "04"} and business_progress
        ),
        "learning_observation_eligible": bool(
            iid in {"01", "02", "03", "04", "05"}
            and (outcome_status == "failed" or policy_decision or experiment_id
                 or research_learning or falsified_business_hypothesis)
            and action_identity != "generic_or_unobserved"
        ),
        "created_at": now_iso(),
    }
    append_jsonl(path, row)
    return {"ok": True, "status": "recorded", "trajectory": row, "path": str(path)}


def record_manual_task_outcome(workspace: str, params: dict[str, Any]) -> dict[str, Any]:
    iid = instance_id(workspace)
    project_id = _resolve_project_id(workspace, iid, params)
    task_id = str(params.get("task_id") or "").strip()
    artifacts = [str(value) for value in params.get("artifacts") or [] if str(value).strip()]
    evidence_refs = []
    governance_root = (workspace_root(workspace) / "share/mind/governance").resolve()
    learning_evidence_roots = [
        (governance_root / "research_learning").resolve(),
        (governance_root / "active_learning").resolve(),
        (workspace_root(workspace) / "external/code").resolve(),
        (workspace_root(workspace) / "external/literature").resolve(),
        (workspace_root(workspace) / "external/insights").resolve(),
    ]
    for value in params.get("evidence_refs") or []:
        try:
            path = Path(str(value)).resolve()
            if path.is_file() and any(root in path.parents for root in learning_evidence_roots):
                evidence_refs.append(str(path))
        except (OSError, ValueError):
            continue
    inputs = [str(value) for value in params.get("inputs") or [] if str(value).strip()]
    declared_learning_handoff = False
    try:
        task_value = json.loads((Path(workspace) / "state/tasks" / task_id /
                                 "task_instance.json").read_text(encoding="utf-8"))
        last_plan = ((task_value.get("metadata") or {}).get("last_plan") or [])
        for planned in last_plan if isinstance(last_plan, list) else []:
            planned_params = planned.get("parameters") or {}
            evidence_path = str(planned_params.get("learning_evidence_path") or "")
            if (planned_params.get("learning_handoff_consumed") is True
                    and evidence_path and evidence_path in inputs
                    and Path(evidence_path).is_file()):
                declared_learning_handoff = True
                break
    except (OSError, TypeError, ValueError):
        declared_learning_handoff = False
    truth_inputs = list(inputs)
    for value in _verified_candidate_sources(workspace, task_id):
        if value not in truth_inputs:
            truth_inputs.append(value)
    continuation_value = params.get("continuation_requested")
    continuation_explicit = continuation_value is not None
    continuation_requested = (
        bool(continuation_value) if continuation_explicit else bool(inputs)
    )
    actions = [str(value) for value in params.get("actions_executed") or [] if str(value).strip()]
    findings = [str(value) for value in params.get("findings") or [] if str(value).strip()]
    goal = str(params.get("goal") or "").strip()
    delivery_confirmed = bool(params.get("delivery_confirmed"))
    completion_ok = bool(params.get("completion_ok"))
    execution_ok = bool(params.get("execution_ok", completion_ok))
    local_observation_requested = bool(params.get("local_observation_confirmed"))
    expected_observation_completed = bool(params.get("expected_observation_completed"))
    campaign_monitor = "campaign_report_delivery" in actions
    failure_owner, failure_mechanism = _task_failure_signature(workspace, task_id)
    canary_policy = _task_canary_policy(workspace, task_id)
    sprint18_isolated = bool(_marker(goal, "sprint18"))
    promoted = ((canary_policy or _promoted_manual_policy(workspace, project_id))
                if not _marker(goal, "policy_arm") and not sprint18_isolated
                and not campaign_monitor else {})
    effective_goal = goal
    if str(params.get("action_selection_id") or "").strip():
        effective_goal += (
            f" [action_selection_id={str(params['action_selection_id']).strip()}]"
        )
    if promoted:
        effective_goal += " " + " ".join(f"[{key}={value}]" for key, value in promoted.items())

    def account_canary(*, accepted: bool, false_success: bool = False,
                       truth_gate_failed: bool = False) -> None:
        if not canary_policy.get("canary_id"):
            return
        try:
            from .production_canary import record_production_canary_outcome
            record_production_canary_outcome(
                workspace, canary_id=canary_policy["canary_id"], task_id=task_id,
                accepted=accepted, false_success=false_success,
                truth_gate_failed=truth_gate_failed)
        except Exception:
            # Outcome persistence remains authoritative; canary accounting is
            # a fail-closed secondary projection and must not falsify it.
            pass

    if not project_id or not task_id or not goal:
        return {"ok": False, "status": "invalid_manual_outcome", "error": "project_id, task_id and goal are required"}
    policy_arm = _marker(effective_goal, "policy_arm")
    experiment_id = _marker(effective_goal, "experiment_id")
    match_key = _marker(effective_goal, "match_key")
    matched_experiment = bool(
        experiment_id and match_key and policy_arm in {"baseline", "candidate"}
    )
    isolated_learning_action = bool(
        sprint18_isolated and any(
            value.startswith(("research_active_learning_", "agent_active_learning_", "learning_"))
            or value in {
                "research_adoption_context_shadow", "sprint18_learning_cycle",
                "targetdiff_active_learning", "targetdiff_active_robustness",
                "targetdiff_uncertainty_diagnostic", "targetdiff_uncertainty_candidate",
            }
            for value in actions
        )
    )
    known_native_learning_action = any(
        value.startswith("agent_active_learning_") for value in actions
    )
    known_native_project_action = any(
        value in {"native_project_action", "continuous_project_step", "molecular_generation_step",
                  "molecular_generation_benchmark", "molecular_diversity_benchmark",
                  "molecular_synth_baseline_benchmark", "molecular_goal_optimization_benchmark",
                  "molecular_data_readiness_audit", "molecular_method_candidate_benchmark",
                  "molecular_docking_holdout", "molecular_external_activity_acquire",
                  "external_knowledge_scout", "research_adoption_context_shadow"}
        for value in actions
    )
    # STOP_PROJECT is a transport boundary and older payloads may not preserve
    # the internal markers in root_user_request.  The explicit local-observation
    # bit is produced only by the native executor, so combine it with a known
    # native Event instead of silently degrading a background job into a manual
    # delivery job.  An ordinary task cannot opt in with an arbitrary action.
    native_runtime_task = bool(
        "[instance_native=true]" in goal
        or "native_project_action" in actions
        or (local_observation_requested
            and (known_native_learning_action or known_native_project_action))
    )
    native_learning_action = bool(
        native_runtime_task and known_native_learning_action
        and ("[native_kind=learning]" in goal or local_observation_requested)
    )
    native_project_action = bool(
        native_runtime_task and known_native_project_action
        and ("[native_kind=project]" in goal or local_observation_requested)
    )
    # A matched experiment is an offline observation, not a user delivery.  A
    # flaky external channel must not turn a truthful, durably archived arm
    # into a negative EGPL example.  Production/manual work still requires its
    # real delivery acknowledgement exactly as before.
    local_observation_confirmed = bool(
        (matched_experiment or isolated_learning_action or native_learning_action) and artifacts
        and all(Path(value).is_file() and Path(value).stat().st_size > 0 for value in artifacts)
    )
    # Background instance-native project work deliberately does not notify QQ
    # for every Event.  Accept its local evidence only when the caller marked
    # the observation, execution succeeded, and every declared artifact is a
    # real non-empty file.  This is not a bypass for ordinary manual tasks.
    if native_runtime_task and artifacts:
        local_observation_confirmed = bool(
            execution_ok and completion_ok
            and all(Path(value).is_file() and Path(value).stat().st_size > 0
                    for value in artifacts)
        )
    from .outcome_contract import evaluate_outcome
    completion_dimensions = evaluate_outcome(
        execution_ok=execution_ok,
        verification_ok=completion_ok,
        has_artifacts=bool(artifacts),
        delivery_confirmed=delivery_confirmed,
        local_observation_confirmed=local_observation_confirmed,
        background=native_runtime_task,
        requires_user_delivery=bool(
            artifacts and not native_runtime_task and not local_observation_confirmed
        ),
        production_effective=bool(params.get("production_effective", False)),
        publication_attempted=bool(params.get("publication_attempted", False)),
    ).to_dict()
    # Truth is evaluated before delivery acceptance.  A file rejected by the
    # truth gate must retain that precise negative label even though it was
    # intentionally withheld from the user channel.
    truth_audit: dict[str, Any] | None = None
    should_truth_audit = bool(
        not campaign_monitor
        and not native_project_action
        and policy_arm in {"baseline", "candidate", "production"}
        and (truth_inputs or "execute_candidate" in actions)
        and any(Path(value).suffix.lower() in {".md", ".txt"} for value in artifacts)
    )
    if should_truth_audit:
        truth_audit = _candidate_truth_audit(
            truth_inputs, artifacts, actions,
            require_claim_ledger=bool(policy_arm in {"candidate", "production"}),
        )
        if not truth_audit.get("passed"):
            issue = record_issue(workspace, {
                "summary": f"candidate final-artifact truth gate failed: {task_id}",
                "category": "verification", "severity": "high",
                "evidence": [f"task_id={task_id}", json.dumps(truth_audit, ensure_ascii=False)],
                "instance_id": iid, "project_id": project_id,
            })
            trajectory = _record_manual_trajectory(
                workspace, iid=iid, project_id=project_id, task_id=task_id,
                receipt={}, inputs=inputs, artifacts=artifacts, actions=actions or ["batch_plan"],
                findings=findings or ["candidate truth gate failed"], goal=effective_goal,
                truth_audit=truth_audit, outcome_status="failed",
                continuation_requested=continuation_requested,
                failure_owner="verification", failure_mechanism="claim_level_truth_gate",
            )
            account_canary(accepted=False, truth_gate_failed=True)
            return {"ok": False, "status": "candidate_truth_gate_failed",
                    "truth_audit": truth_audit, "issue": issue.get("issue"), "trajectory": trajectory}
    if campaign_monitor:
        # Monitoring is control-plane telemetry, never a business-policy arm.
        # Record it neutrally even when channel delivery itself failed; the
        # Campaign acceptance layer owns that separate delivery verdict.
        receipt = {
            "receipt_id": "monitor_" + hashlib.sha256(task_id.encode()).hexdigest()[:16],
            "delivery_confirmed": delivery_confirmed,
        }
        trajectory = _record_manual_trajectory(
            workspace, iid=iid, project_id=project_id, task_id=task_id,
            receipt=receipt, inputs=inputs, artifacts=artifacts,
            actions=actions, findings=findings or ["Campaign monitor event recorded"],
            goal=effective_goal,
            # The handler completed the control-plane observation even when
            # the optional user channel did not acknowledge it.  Campaign
            # reconciliation owns that separate report-delivery verdict.
            outcome_status="completed",
            monitor_only=True,
        )
        return {"ok": True, "status": "campaign_monitor_recorded",
                "manual_task_id": task_id, "trajectory": trajectory,
                "project_state_mutated": False, "production_effective": False,
                "delivery_confirmed": delivery_confirmed}

    if not completion_dimensions["work_accepted"]:
        partial_artifacts = _task_partial_artifacts(workspace, task_id)
        for value in partial_artifacts:
            if value not in artifacts:
                artifacts.append(value)
        issue = record_issue(workspace, {
            "summary": f"manual task did not reach accepted completion: {task_id}",
            "category": "delivery" if artifacts and not delivery_confirmed else "verification",
            "severity": "high",
            "evidence": [f"task_id={task_id}", f"failure_owner={failure_owner}",
                         f"failure_mechanism={failure_mechanism}", *artifacts],
            "instance_id": iid,
            "project_id": project_id,
        })
        result = {"ok": False, "status": "manual_outcome_rejected", "issue": issue.get("issue")}
        result["trajectory"] = _record_manual_trajectory(
            workspace, iid=iid, project_id=project_id, task_id=task_id,
            receipt={}, inputs=inputs, artifacts=artifacts, actions=actions or ["batch_plan"],
            findings=findings or ["manual acceptance failed"], goal=effective_goal,
            outcome_status="failed", false_success=bool(artifacts),
                continuation_requested=continuation_requested,
                failure_owner=failure_owner, failure_mechanism=failure_mechanism,
                completion_dimensions=completion_dimensions,
            )
        account_canary(accepted=False, false_success=bool(artifacts))
        return result

    # A Receipt is a durable project handoff.  Task directories are scratch
    # space and can be cleaned, so file-bearing receipts must reference an
    # archived copy rather than a volatile task-local path.
    evidence_archive: dict[str, Any] = {}
    if artifacts:
        try:
            from .evidence_archive import archive_work_item_evidence

            evidence_archive = archive_work_item_evidence(
                workspace,
                campaign_id="manual",
                work_item_id=task_id,
                project_id=project_id,
                instance_id=iid,
                artifacts=artifacts,
                event_types=actions or ["batch_plan"],
            )
            if evidence_archive.get("ok"):
                artifacts = [str(value) for value in evidence_archive.get("artifacts") or []]
        except Exception as exc:
            return {"ok": False, "status": "manual_evidence_archive_failed",
                    "error": str(exc)[:1000]}

    # Matched canary observations are deliberately not project iterations.
    # Advancing the project's latest Receipt after the first arm would make
    # the second arm consume a different handoff and destroy matching.  Both
    # arms still pass the same completion, delivery, archive, and truth gates;
    # they are persisted as trajectories/Episodes in an experiment ledger.
    if matched_experiment:
        observation_id = "observation_" + hashlib.sha256(
            f"{experiment_id}|{match_key}|{policy_arm}|{task_id}".encode("utf-8")
        ).hexdigest()[:16]
        receipt = {
            "receipt_id": observation_id,
            "project_id": project_id,
            "iteration": 0,
            "goal": goal,
            "inputs": inputs,
            "actions_executed": actions or ["batch_plan"],
            "artifacts": artifacts,
            "findings": findings or ["matched experiment observation passed shared hard gates"],
            "next_actions": [],
            "stop_reason": "bounded matched experiment observation completed",
            "delivery_confirmed": delivery_confirmed,
            "local_observation_confirmed": local_observation_confirmed,
            "created_at": now_iso(),
        }
        trajectory = _record_manual_trajectory(
            workspace, iid=iid, project_id=project_id, task_id=task_id,
            receipt=receipt, inputs=inputs, artifacts=artifacts,
            evidence_refs=evidence_refs,
            actions=actions or ["batch_plan"],
            findings=findings or ["matched experiment observation passed shared hard gates"],
            goal=effective_goal, truth_audit=truth_audit,
            continuation_requested=continuation_requested,
        )
        observation_path = (
            workspace_root(workspace) / "share" / "mind" / "governance"
            / "experiment_observations" / experiment_id / f"{observation_id}.json"
        )
        observation_path.parent.mkdir(parents=True, exist_ok=True)
        observation_path.write_text(
            json.dumps({
                "schema_version": 1,
                "observation_id": observation_id,
                "experiment_id": experiment_id,
                "match_key": match_key,
                "policy_arm": policy_arm,
                "task_id": task_id,
                "receipt": receipt,
                "truth_audit": truth_audit or {},
                "trajectory_id": (trajectory.get("trajectory") or {}).get("trajectory_id", ""),
                "project_state_mutated": False,
                "local_observation_confirmed": local_observation_confirmed,
                "created_at": now_iso(),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "ok": True,
            "status": "experiment_observation_recorded",
            "manual_task_id": task_id,
            "receipt": receipt,
            "trajectory": trajectory,
            "truth_audit": truth_audit or {},
            "evidence_archive": evidence_archive,
            "project_state_mutated": False,
            "local_observation_confirmed": local_observation_confirmed,
            "observation_path": str(observation_path),
            "next_action_auto_enqueued": False,
        }

    # Sprint18 Event-first probes are learning observations, not project
    # delivery rounds.  A durable local artifact/evidence bundle is sufficient
    # for this isolated ledger; it must not advance ProjectState or require a
    # user-channel acknowledgement that the WorkItem explicitly disabled.
    if isolated_learning_action:
        observation_id = "learning_" + hashlib.sha256(
            f"sprint18|{iid}|{task_id}".encode("utf-8")
        ).hexdigest()[:16]
        receipt = {
            "receipt_id": observation_id, "project_id": project_id,
            "delivery_confirmed": delivery_confirmed,
            "local_observation_confirmed": local_observation_confirmed,
        }
        trajectory = _record_manual_trajectory(
            workspace, iid=iid, project_id=project_id, task_id=task_id,
            receipt=receipt, inputs=inputs, artifacts=artifacts,
            evidence_refs=evidence_refs, actions=actions or ["batch_plan"],
            findings=findings or ["isolated learning observation completed"],
            goal=effective_goal, truth_audit=truth_audit,
            continuation_requested=continuation_requested,
        )
        return {
            "ok": True, "status": "isolated_learning_observation_recorded",
            "manual_task_id": task_id, "receipt": receipt,
            "trajectory": trajectory, "truth_audit": truth_audit or {},
            "evidence_archive": evidence_archive,
            "project_state_mutated": False,
            "local_observation_confirmed": local_observation_confirmed,
            "production_effective": False,
            "next_action_auto_enqueued": False,
        }

    # A native metacognitive interruption belongs only to the learning
    # ledger. It may resume the suspended project after its terminal, but it
    # must never increment that project's IterationReceipt or earn business
    # progress reward.
    if native_learning_action:
        observation_id = "native_learning_" + hashlib.sha256(
            f"{iid}|{task_id}".encode("utf-8")
        ).hexdigest()[:16]
        observation = {
            "receipt_id": observation_id, "project_id": project_id,
            "delivery_confirmed": delivery_confirmed,
            "local_observation_confirmed": local_observation_confirmed,
        }
        trajectory = _record_manual_trajectory(
            workspace, iid=iid, project_id=project_id, task_id=task_id,
            receipt=observation, inputs=inputs, artifacts=artifacts,
            evidence_refs=list(dict.fromkeys([*evidence_refs, *artifacts])), actions=actions,
            findings=findings or ["bounded native learning interruption completed"],
            goal=effective_goal, truth_audit=truth_audit,
            continuation_requested=False,
        )
        return {
            "ok": True, "status": "native_learning_observation_recorded",
            "manual_task_id": task_id, "receipt": observation,
            "trajectory": trajectory, "project_state_mutated": False,
            "local_observation_confirmed": local_observation_confirmed,
            "production_effective": False, "next_action_auto_enqueued": False,
        }

    # A bounded Candidate probe which proves that the allow-listed repair is
    # already installed is useful learning evidence, but it did not invent or
    # apply a new behavior. Keep it out of ProjectState and business reward.
    no_change = _candidate_no_change(artifacts) if native_project_action else None
    if no_change:
        observation_id = "candidate_no_change_" + hashlib.sha256(
            f"{iid}|{task_id}".encode("utf-8")
        ).hexdigest()[:16]
        observation = {
            "receipt_id": observation_id, "project_id": project_id,
            "delivery_confirmed": delivery_confirmed,
            "local_observation_confirmed": bool(artifacts),
            **no_change,
        }
        learning_actions = [*actions, "learning_candidate_no_change"]
        trajectory = _record_manual_trajectory(
            workspace, iid=iid, project_id=project_id, task_id=task_id,
            receipt=observation, inputs=inputs, artifacts=artifacts,
            evidence_refs=artifacts, actions=learning_actions,
            findings=findings or ["bounded Candidate probe found no new behavior"],
            goal=effective_goal, truth_audit=truth_audit,
            continuation_requested=False, learning_only=True,
        )
        return {
            "ok": True, "status": "candidate_no_change_observation_recorded",
            "manual_task_id": task_id, "receipt": observation,
            "trajectory": trajectory, "project_state_mutated": False,
            "production_effective": False, "next_action_auto_enqueued": False,
            "evidence_archive": evidence_archive,
        }

    # Autonomous project rounds must prove a real action before they may
    # mutate ProjectState.  A generated report and a generic finding are not
    # business progress and instead become an active-learning observation.
    if "[instance_native=true]" in goal:
        from .real_action_contract import assess as assess_real_action
        progress = assess_real_action(
            workspace_root_path=workspace_root(workspace), project_id=project_id,
            findings=findings, actions_executed=actions, artifacts=artifacts,
            max_repeat_findings=2, require_external_artifact=True,
        )
        if not progress.get("ok"):
            mechanism = "project/" + str(progress.get("violation") or "report_only_progress")
            issue = record_issue(workspace, {
                "summary": f"native project round lacked real progress: {task_id}",
                "category": "verification", "severity": "high",
                "evidence": [f"task_id={task_id}", json.dumps(progress, ensure_ascii=False)],
                "instance_id": iid, "project_id": project_id,
            })
            trajectory = _record_manual_trajectory(
                workspace, iid=iid, project_id=project_id, task_id=task_id,
                receipt={}, inputs=inputs, artifacts=artifacts,
                actions=actions or ["batch_plan"], findings=findings or ["real action contract failed"],
                goal=effective_goal, outcome_status="failed", false_success=bool(artifacts),
                continuation_requested=continuation_requested,
                failure_owner="verification", failure_mechanism=mechanism,
            )
            return {"ok": False, "status": "native_real_action_rejected",
                    "issue": issue.get("issue"), "progress_assessment": progress,
                    "trajectory": trajectory}

    previous = latest_receipt(workspace, project_id)
    ignore_handoff_check = bool(params.get("ignore_handoff_check", False))
    # A task's input files describe what it reads; they do not by themselves
    # mean that the task is continuing the latest project Receipt.  The
    # executor supplies this field from TaskInstance.continue_from_project.
    # Keep the legacy conservative inference for direct/older callers that do
    # not yet provide the field, so this contract cannot be weakened silently.
    if previous and previous.artifacts and not _handoff_present(previous.artifacts, inputs):
        # Handoff is a semantic relation, not a property of a non-empty input
        # list.  A standalone task may legitimately read source files or
        # holdouts unrelated to the previous task.  Conversely, an explicit
        # continuation must consume at least one archived previous artifact.
        if continuation_requested:
            issue = record_issue(workspace, {
                "summary": f"manual task missing previous artifact handoff: {task_id}",
                "category": "context",
                "severity": "high",
                "evidence": [f"latest_receipt_id={previous.receipt_id}", f"task_id={task_id}", *inputs],
                "instance_id": iid,
                "project_id": project_id,
            })
            return {
                "ok": False,
                "status": "unlinked_previous_receipt",
                "latest_receipt_id": previous.receipt_id,
                "issue": issue.get("issue"),
            }
        if not ignore_handoff_check:
            record_issue(workspace, {
                "summary": f"standalone manual task starts a new receipt branch: {task_id}",
                "category": "context",
                "severity": "info",
                "evidence": [
                    f"latest_receipt_id={previous.receipt_id}",
                    f"task_id={task_id}",
                    f"input_count={len(inputs)}",
                    "continuation_requested=false",
                ],
                "instance_id": iid,
                "project_id": project_id,
            })
        # The lower-level Receipt validator has the same structural handoff
        # guard.  This explicit semantic decision must be forwarded to it.
        ignore_handoff_check = True

    next_action = str(params.get("next_action") or "").strip()
    next_actions = []
    stop_reason = "bounded manual task completed; waiting for the next user instruction"
    non_action_tokens = (
        "等待用户", "重新发起", "提供正确", "无法继续",
        "根据 Harness 执行结果选择下一步", "若目标已满足则停止",
    )
    if next_action and not any(token in next_action for token in non_action_tokens):
        next_actions = [{
            "title": next_action[:160],
            "event_type": "batch_plan",
            "params": {
                "user_request": next_action,
                "previous_receipt_id": previous.receipt_id if previous else "",
            },
            "status": "proposed",
        }]
        stop_reason = ""
    result = record_iteration(workspace, {
        "project_id": project_id,
        "owner_instance": iid,
        "project_goal": goal,
        "goal": goal,
        "inputs": inputs,
        "actions_executed": actions or ["batch_plan"],
        "artifacts": artifacts,
        "findings": findings or ["任务通过 Harness 与交付硬门"],
        "next_actions": next_actions,
        "stop_reason": stop_reason,
        "project_status": "completed" if not next_actions else "active",
        "delivery_confirmed": delivery_confirmed,
        # Receipt persistence consumes the already-decided completion mode;
        # it must not recreate a contradictory delivery policy from one bit.
        "requires_delivery": bool(
            artifacts and completion_dimensions.get("notification") == "failed"
        ),
        # Hermes 2026-08-27 fix: forward the opt-in flag from the upstream
        # shape-(a) check. Without this propagation, the manual_runtime
        # handoff downgrade is silently undone by record_iteration.
        "ignore_handoff_check": ignore_handoff_check,
    })
    result["manual_task_id"] = task_id
    result["next_action_auto_enqueued"] = False
    result["evidence_archive"] = evidence_archive
    if result.get("ok"):
        result["trajectory"] = _record_manual_trajectory(
            workspace, iid=iid, project_id=project_id, task_id=task_id,
            receipt=result.get("receipt") or {}, inputs=inputs, artifacts=artifacts,
            evidence_refs=evidence_refs,
            actions=actions or ["batch_plan"], findings=findings or ["任务通过 Harness 与交付硬门"],
            goal=effective_goal, truth_audit=truth_audit,
            continuation_requested=continuation_requested,
            handoff_consumed=bool(
                declared_learning_handoff
                or (continuation_requested and previous
                    and _handoff_present(previous.artifacts, inputs))
            ),
            monitor_only=expected_observation_completed,
            completion_dimensions=completion_dimensions,
        )
        account_canary(accepted=True)
    result["completion_dimensions"] = completion_dimensions
    result["local_observation_confirmed"] = local_observation_confirmed
# self_evolve_annotation: candidate_id=repair_to_pr_1ca6fab61c0d4d94 failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_2a4a11789d7c11da failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_341758ef8d7306a1 failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_749d851bcad73abc failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_82a352a10612f31 failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_853de960b7d5e7cf failure_class=tool.atomic_http_get.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_8c427e360f704cea failure_class=tool.extract.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_cf1ddef172241b0c failure_class=tool.extract.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_f450c2b58075c2fa failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_20d5c768740c7b71 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_277c0a88ba10fe92 failure_class=tool.create_file.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_40b511d1712b6a5b failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_41503169f9152818 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_653ec61b358afc27 failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_6605f6e23587f0e4 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_69a48301ee8bbdd failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_6c7abc7e52c2ce8b failure_class=tool.molecular_diversity_benchmark.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_790f8e597f601613 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_b3c4181e85dc6ca6 failure_class=tool.extract.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_f3eb6f006678adef failure_class=tool.atomic_http_get.failed intervention=mechanism_specific_bounded_repair
    return result
