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
    path = workspace_root(workspace) / "share" / "mind" / "governance" / "rl" / "control_policy.json"
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
    inputs = [str(value) for value in params.get("inputs") or [] if str(value).strip()]
    for value in _verified_candidate_sources(workspace, str(params.get("task_id") or "")):
        if value not in inputs:
            inputs.append(value)
    artifacts = [str(value) for value in params.get("artifacts") or [] if str(value).strip()]
    actions = [str(value) for value in params.get("actions_executed") or [] if str(value).strip()]
    promoted = _promoted_manual_policy(workspace, project_id) if not _marker(goal, "policy_arm") else {}
    effective_goal = goal
    if promoted:
        effective_goal += " " + " ".join(f"[{key}={value}]" for key, value in promoted.items())
    policy_arm = _marker(effective_goal, "policy_arm")
    applicable = bool(
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
            if owner or mechanism:
                return owner, mechanism
        owner = str(row.get("failure_owner") or owner)
        mechanism = str(row.get("mechanism") or mechanism)
        if owner or mechanism:
            return owner, mechanism
    return owner, mechanism


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
                              monitor_only: bool = False) -> dict[str, Any]:
    root = workspace_root(workspace)
    path = root / "share" / "mind" / "governance" / "rl" / "trajectories.jsonl"
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
        or value in {"sprint18_learning_cycle", "targetdiff_active_learning",
                     "targetdiff_active_robustness", "targetdiff_uncertainty_diagnostic",
                     "targetdiff_uncertainty_candidate", "research_adoption_context_shadow"}
        for value in meaningful
    )
    isolated_learning = bool(_marker(goal, "sprint18")) and research_learning
    business_progress = bool(
        not monitor_only and outcome_status == "completed"
        and not isolated_learning and artifacts and findings and meaningful and fingerprint
    )
    learning_progress = bool(
        not monitor_only and outcome_status == "completed" and research_learning
        and evidence_refs and findings and meaningful and fingerprint
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
        "novel_evidence": 0.20 if business_progress else (0.10 if learning_progress else 0.0),
        "handoff_consumed": 0.15 if handoff_consumed and not monitor_only else 0.0,
    }
    reward = (0.0 if monitor_only else (
        round(min(1.0, sum(reward_components.values())), 4) if (business_progress or learning_progress)
        else round(-0.45 + reward_components["partial_artifact"], 4)
    ))
    strategy_id = _marker(goal, "strategy_id") or "manual_stable_grounded_v1"
    policy_decision = _marker(goal, "policy_decision")
    policy_arm = _marker(goal, "policy_arm")
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
                  "continuation_requested": bool(continuation_requested)},
        "action": {
            "action_key": f"{iid}:manual_project_iteration:{action_identity}",
            "event_types": actions,
            "strategy_id": strategy_id,
            "policy_decision": policy_decision,
            "policy_arm": policy_arm,
            "experiment_id": experiment_id,
            "match_key": match_key,
        },
        "outcome": {
            "status": outcome_status, "artifacts": artifacts, "evidence_refs": evidence_refs,
            "evidence": [f"receipt_id={receipt.get('receipt_id', '')}", *findings],
            "outcome_fingerprint": fingerprint, "monitor_only": bool(monitor_only),
            "business_progress": business_progress, "learning_progress": learning_progress,
            "novel_evidence": bool(business_progress or learning_progress),
            "handoff_consumed": bool(handoff_consumed),
            "false_success": (bool(false_success) if false_success is not None
                              else bool(truth_audit is not None and not truth_audit.get("passed"))),
            "truth_audit": truth_audit or {},
            "artifact_completion": "full" if full_artifact_contract else ("partial" if partial_artifact else "none"),
            "failure_owner": failure_owner,
            "failure_mechanism": failure_mechanism,
        },
        "reward": reward,
        "reward_components": reward_components,
        "policy_eligible": bool(
            not monitor_only and not isolated_learning
            and iid in {"01", "02", "03", "04"} and business_progress
        ),
        "learning_observation_eligible": bool(
            iid in {"01", "02", "03", "04", "05"}
            and (policy_decision or experiment_id or research_learning)
            and action_identity != "generic_or_unobserved"
        ),
        "created_at": now_iso(),
    }
    append_jsonl(path, row)
    return {"ok": True, "status": "recorded", "trajectory": row, "path": str(path)}


def record_manual_task_outcome(workspace: str, params: dict[str, Any]) -> dict[str, Any]:
    iid = instance_id(workspace)
    project_id = str(params.get("project_id") or ROLES.get(iid) or "").strip()
    task_id = str(params.get("task_id") or "").strip()
    artifacts = [str(value) for value in params.get("artifacts") or [] if str(value).strip()]
    evidence_refs = []
    governance_root = (workspace_root(workspace) / "share/mind/governance").resolve()
    learning_evidence_roots = [
        (governance_root / "research_learning").resolve(),
        (governance_root / "active_learning").resolve(),
    ]
    for value in params.get("evidence_refs") or []:
        try:
            path = Path(str(value)).resolve()
            if path.is_file() and any(root in path.parents for root in learning_evidence_roots):
                evidence_refs.append(str(path))
        except (OSError, ValueError):
            continue
    inputs = [str(value) for value in params.get("inputs") or [] if str(value).strip()]
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
    expected_observation_completed = bool(params.get("expected_observation_completed"))
    campaign_monitor = "campaign_report_delivery" in actions
    failure_owner, failure_mechanism = _task_failure_signature(workspace, task_id)
    canary_policy = _task_canary_policy(workspace, task_id)
    sprint18_isolated = bool(_marker(goal, "sprint18"))
    promoted = ((canary_policy or _promoted_manual_policy(workspace, project_id))
                if not _marker(goal, "policy_arm") and not sprint18_isolated
                and not campaign_monitor else {})
    effective_goal = goal
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
    # A matched experiment is an offline observation, not a user delivery.  A
    # flaky external channel must not turn a truthful, durably archived arm
    # into a negative RL example.  Production/manual work still requires its
    # real delivery acknowledgement exactly as before.
    local_observation_confirmed = bool(
        (matched_experiment or isolated_learning_action) and artifacts
        and all(Path(value).is_file() and Path(value).stat().st_size > 0 for value in artifacts)
    )
    # Truth is evaluated before delivery acceptance.  A file rejected by the
    # truth gate must retain that precise negative label even though it was
    # intentionally withheld from the user channel.
    truth_audit: dict[str, Any] | None = None
    should_truth_audit = bool(
        not campaign_monitor
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

    # Sprint18 §6 follow-up: delivery failure alone must not void an otherwise
    # completed run. If the task shipped real artifacts (verified by file
    # existence + size) and the actions were batch_plan-class, treat the
    # work as accepted and tag the QQ/email delivery failure separately.
    real_artifacts = [
        value for value in (artifacts or [])
        if Path(str(value)).is_file() and Path(str(value)).stat().st_size > 0
    ]
    promoted = _promoted_manual_policy(workspace, project_id) if not _marker(goal, "policy_arm") else {}
    effective_goal = goal
    if promoted:
        effective_goal += " " + " ".join(f"[{key}={value}]" for key, value in promoted.items())
    policy_arm = _marker(effective_goal, "policy_arm")
    delivery_failure_only = (
        not completion_ok
        and bool(real_artifacts)
        and not delivery_confirmed
        and not local_observation_confirmed
        and policy_arm not in {"baseline", "candidate", "production"}
        and any(
            value.startswith(prefix)
            or value in ("generate_text", "create_file")
            for value in actions
            for prefix in (
                "atomic_", "batch_plan", "web_fetch", "web_search",
                "push_files", "execute_code", "execute_candidate",
                "partner_", "molecular_", "literature_", "agent_",
                "research_", "xiaohongshu_",
            )
        )
    )
    if delivery_failure_only:
        from .manual_runtime_helpers import record_delivery_failure
        try:
            record_delivery_failure(workspace, task_id, iid=iid, project_id=project_id,
                                    artifacts=real_artifacts, actions=actions)
        except Exception:
            pass
        receipt = {
            "receipt_id": "delivery_" + hashlib.sha256(task_id.encode()).hexdigest()[:16],
            "delivery_confirmed": False, "delivery_skipped": True,
        }
        trajectory_findings = list(findings or [])
        trajectory_findings.append("delivery_only_failure: artifacts accepted despite channel ack failure")
        trajectory = _record_manual_trajectory(
            workspace, iid=iid, project_id=project_id, task_id=task_id,
            receipt=receipt, inputs=inputs, artifacts=real_artifacts,
            actions=actions, findings=trajectory_findings,
            goal=effective_goal, outcome_status="completed",
        )
        return {"ok": True, "status": "delivery_only_failure_accepted",
                "manual_task_id": task_id, "trajectory": trajectory,
                "project_state_mutated": False, "production_effective": True,
                "delivery_confirmed": False,
                "artifacts": real_artifacts}

    if not completion_ok or (artifacts and not delivery_confirmed and not local_observation_confirmed):
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
        if _marker(effective_goal, "experiment_id"):
            result["trajectory"] = _record_manual_trajectory(
                workspace, iid=iid, project_id=project_id, task_id=task_id,
                receipt={}, inputs=inputs, artifacts=artifacts, actions=actions or ["batch_plan"],
                findings=findings or ["manual acceptance failed"], goal=effective_goal,
                outcome_status="failed", false_success=bool(artifacts),
                continuation_requested=continuation_requested,
                failure_owner=failure_owner, failure_mechanism=failure_mechanism,
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
        "requires_delivery": bool(artifacts),
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
                continuation_requested and previous
                and _handoff_present(previous.artifacts, inputs)
            ),
            monitor_only=expected_observation_completed,
        )
        account_canary(accepted=True)
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
    return result
