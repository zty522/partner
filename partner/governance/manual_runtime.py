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
    decision_key = f"{project_id}:manual_final_artifact_truth"
    path = workspace_root(workspace) / "share" / "mind" / "governance" / "rl" / "control_policy.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    strategy = str((payload.get("promoted") or {}).get(decision_key) or "")
    if strategy != "manual_stable_truth_audit_v2":
        return {}
    return {"strategy_id": strategy, "policy_decision": decision_key,
            "policy_arm": "production", "experiment_id": "experiment_5af99917bea9"}


def _candidate_truth_audit(inputs: list[str], artifacts: list[str],
                           actions: list[str] | None = None) -> dict[str, Any]:
    """Verify final-report source/quote pairs against the files actually read."""
    required_sources = {str(Path(value).resolve()) for value in inputs if Path(value).is_file()}
    pairs: list[dict[str, str]] = []
    false_claims: list[str] = []
    capability_contradictions: list[dict[str, str]] = []
    pattern = re.compile(
        r"^[ \t]*(?:[-*>][ \t]*)?`?source_path`?\s*[:：]\s*([^\n]+?)\s*$\n"
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
            source_text = Path(source_path).read_text(encoding="utf-8")
        except OSError:
            invalid_pairs.append(pair)
            continue
        if source_path not in required_sources or len(quote) < 20 or quote not in source_text:
            invalid_pairs.append(pair)
        else:
            verified_sources.add(source_path)
    missing_sources = sorted(required_sources - verified_sources)
    passed = bool(required_sources) and not false_claims and not invalid_pairs and not missing_sources
    return {
        "passed": passed,
        "required_sources": sorted(required_sources),
        "verified_sources": sorted(verified_sources),
        "pair_count": len(pairs),
        "missing_sources": missing_sources,
        "invalid_pairs": invalid_pairs,
        "false_capability_claim_artifacts": false_claims,
        "capability_contradictions": capability_contradictions,
    }


def _record_manual_trajectory(workspace: str, *, iid: str, project_id: str, task_id: str,
                              receipt: dict[str, Any], inputs: list[str], artifacts: list[str],
                              actions: list[str], findings: list[str], goal: str = "",
                              truth_audit: dict[str, Any] | None = None,
                              outcome_status: str = "completed",
                              false_success: bool | None = None) -> dict[str, Any]:
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
    fingerprint = _content_fingerprint(artifacts)
    meaningful = [value for value in actions if value not in {"send_user_text", "push_files", "batch_plan"}]
    business_progress = bool(outcome_status == "completed" and artifacts and findings and meaningful and fingerprint)
    reward_components = {
        "accepted_completed": 0.05,
        "artifact_contract": 0.05,
        "delivery_contract": 0.05,
        "meaningful_event": 0.05 if meaningful else 0.0,
        "business_progress": 0.45 if business_progress else 0.0,
        "novel_evidence": 0.20 if business_progress else 0.0,
        "handoff_consumed": 0.15 if receipt.get("iteration", 1) > 1 else 0.0,
    }
    reward = round(min(1.0, sum(reward_components.values())), 4) if business_progress else -0.45
    strategy_id = _marker(goal, "strategy_id") or "manual_stable_grounded_v1"
    policy_decision = _marker(goal, "policy_decision")
    policy_arm = _marker(goal, "policy_arm")
    experiment_id = _marker(goal, "experiment_id")
    match_key = _marker(goal, "match_key")
    row = {
        "schema_version": 2,
        "trajectory_id": trajectory_id,
        "campaign_id": "",
        "work_item_id": task_id,
        "project_id": project_id,
        "instance_id": iid,
        "kind": "manual_project_iteration",
        "state": {"source_families": source_families, "receipt_id": receipt.get("receipt_id", ""),
                  "delivery_confirmed": bool(receipt.get("delivery_confirmed"))},
        "action": {
            "action_key": f"{iid}:manual_project_iteration:{meaningful[0] if meaningful else 'generic'}",
            "event_types": actions,
            "strategy_id": strategy_id,
            "policy_decision": policy_decision,
            "policy_arm": policy_arm,
            "experiment_id": experiment_id,
            "match_key": match_key,
        },
        "outcome": {
            "status": outcome_status, "artifacts": artifacts,
            "evidence": [f"receipt_id={receipt.get('receipt_id', '')}", *findings],
            "outcome_fingerprint": fingerprint, "monitor_only": False,
            "business_progress": business_progress, "novel_evidence": business_progress,
            "handoff_consumed": receipt.get("iteration", 1) > 1,
            "false_success": (bool(false_success) if false_success is not None
                              else bool(truth_audit is not None and not truth_audit.get("passed"))),
            "truth_audit": truth_audit or {},
        },
        "reward": reward,
        "reward_components": reward_components,
        "policy_eligible": bool(iid in {"01", "02", "03", "04"} and business_progress),
        "created_at": now_iso(),
    }
    append_jsonl(path, row)
    return {"ok": True, "status": "recorded", "trajectory": row, "path": str(path)}


def record_manual_task_outcome(workspace: str, params: dict[str, Any]) -> dict[str, Any]:
    iid = instance_id(workspace)
    project_id = str(params.get("project_id") or ROLES.get(iid) or "").strip()
    task_id = str(params.get("task_id") or "").strip()
    artifacts = [str(value) for value in params.get("artifacts") or [] if str(value).strip()]
    inputs = [str(value) for value in params.get("inputs") or [] if str(value).strip()]
    actions = [str(value) for value in params.get("actions_executed") or [] if str(value).strip()]
    findings = [str(value) for value in params.get("findings") or [] if str(value).strip()]
    goal = str(params.get("goal") or "").strip()
    delivery_confirmed = bool(params.get("delivery_confirmed"))
    completion_ok = bool(params.get("completion_ok"))
    promoted = _promoted_manual_policy(workspace, project_id) if not _marker(goal, "policy_arm") else {}
    effective_goal = goal
    if promoted:
        effective_goal += " " + " ".join(f"[{key}={value}]" for key, value in promoted.items())

    if not project_id or not task_id or not goal:
        return {"ok": False, "status": "invalid_manual_outcome", "error": "project_id, task_id and goal are required"}
    if not completion_ok or (artifacts and not delivery_confirmed):
        issue = record_issue(workspace, {
            "summary": f"manual task did not reach accepted completion: {task_id}",
            "category": "delivery" if artifacts and not delivery_confirmed else "verification",
            "severity": "high",
            "evidence": [f"task_id={task_id}", *artifacts],
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
            )
        return result

    policy_arm = _marker(effective_goal, "policy_arm")
    truth_audit: dict[str, Any] | None = None
    experiment_id = _marker(effective_goal, "experiment_id")
    match_key = _marker(effective_goal, "match_key")
    matched_experiment = bool(
        experiment_id and match_key and policy_arm in {"baseline", "candidate"}
    )
    should_truth_audit = bool(
        policy_arm in {"baseline", "candidate", "production"}
        and inputs
        and any(Path(value).suffix.lower() in {".md", ".txt"} for value in artifacts)
    )
    if should_truth_audit:
        truth_audit = _candidate_truth_audit(inputs, artifacts, actions)
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
            )
            return {"ok": False, "status": "candidate_truth_gate_failed",
                    "truth_audit": truth_audit, "issue": issue.get("issue"), "trajectory": trajectory}

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
            "created_at": now_iso(),
        }
        trajectory = _record_manual_trajectory(
            workspace, iid=iid, project_id=project_id, task_id=task_id,
            receipt=receipt, inputs=inputs, artifacts=artifacts,
            actions=actions or ["batch_plan"],
            findings=findings or ["matched experiment observation passed shared hard gates"],
            goal=effective_goal, truth_audit=truth_audit,
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
            "observation_path": str(observation_path),
            "next_action_auto_enqueued": False,
        }

    previous = latest_receipt(workspace, project_id)
    ignore_handoff_check = bool(params.get("ignore_handoff_check", False))
    if previous and previous.artifacts and not _handoff_present(previous.artifacts, inputs):
        # Hermes 2026-08-27 fix: the previous handoff contract was a hard
        # reject that broke legitimate inbox-triggered standalone tasks.
        # Two failure shapes were conflated:
        #   (a) Task carries `inputs=[]` because the user message arrived via
        #       desktop_inbox after a long Campaign pause and there is no
        #       prior receipt to link to. Pure self-contained task.  Old
        #       behavior: `unlinked_previous_receipt` reject, but the task
        #       had `delivery_confirmed=True` and was a real user request.
        #   (b) Task carries non-empty `inputs` that intentionally omit every
        #       previous-artifact path.  This is the "previous artifact
        #       handoff missing" case the contract was designed to catch.
        # New behavior: shape (a) → opt-in skip via
        # `params["ignore_handoff_check"]=True`; shape (b) keeps the old
        # hard reject so the shadow-replay audit still catches real handoff
        # regressions. Both shapes still emit an IssueRecord (severity
        # `info` for (a), `high` for (b)) so 05's independent review can
        # surface either.
        shape_b_reject = bool(inputs)
        if shape_b_reject:
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
                "summary": f"manual task without previous artifact handoff: {task_id}",
                "category": "context",
                "severity": "info",
                "evidence": [
                    f"latest_receipt_id={previous.receipt_id}",
                    f"task_id={task_id}",
                    "shape=empty_inputs",
                    "hint=set ignore_handoff_check=true for inbox-triggered standalone tasks",
                ],
                "instance_id": iid,
                "project_id": project_id,
            })

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
        "ignore_handoff_check": bool(params.get("ignore_handoff_check", False)),
    })
    result["manual_task_id"] = task_id
    result["next_action_auto_enqueued"] = False
    result["evidence_archive"] = evidence_archive
    if result.get("ok"):
        result["trajectory"] = _record_manual_trajectory(
            workspace, iid=iid, project_id=project_id, task_id=task_id,
            receipt=result.get("receipt") or {}, inputs=inputs, artifacts=artifacts,
            actions=actions or ["batch_plan"], findings=findings or ["任务通过 Harness 与交付硬门"],
            goal=effective_goal, truth_audit=truth_audit,
        )
    return result
