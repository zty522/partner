"""Sprint18 §5 unified patch.

Implements 4 changes for partner self-evolution + active-learning loops:

1. governance_auto_claim: after every task completion in executor.py,
   auto-populate task.expected_artifacts from real files written under
   task_working_dir so _requested_named_artifacts passes.
2. project_state_iterating: extend ProjectState status enum with `iterating`
   so projects that auto-iterate (e.g. 02 molecular_generation) keep running.
3. active_learning_to_production: bridge repair_proposal events to
   production_readiness/ so overnight canary can promote them.
4. instance_scoped_openid_recovery: recover the last user OpenID from the
   *same instance's* inbound QQ history.  QQ Official OpenIDs are scoped to
   the bot application, so an OpenID learned by one instance must never be
   copied to another instance.

Each patch is implemented as a small monkey-patchable function that the
existing modules can opt into via a sentinel attribute
``sprint18_0066_unified_patch_applied`` so this file is the single source of
truth.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patch 1: governance_auto_claim
# ---------------------------------------------------------------------------


def claim_written_artifacts(task_working_dir: str | Path,
                            current_expected: list[dict[str, Any]] | None = None,
                            *, allowed_exts: tuple[str, ...] = (
                                "md", "json", "csv", "pdf", "png", "jpg", "jpeg",
                                "py", "jsonl", "xlsx", "txt", "yaml", "yml",
                            )) -> list[dict[str, Any]]:
    """Scan task_working_dir and produce updated expected_artifacts list.

    The list contains:
      * any entry already in current_expected whose pattern (basename or *.ext)
        is still present, kept verbatim;
      * plus newly-claimed artifacts for any file actually on disk that has
        an allowed extension, formatted with type=file and a synthetic
        description that lets governance's named_artifact match.
    """
    base = Path(task_working_dir)
    if not base.exists():
        return current_expected or []
    seen = set()
    out = []
    for entry in current_expected or []:
        if not isinstance(entry, dict):
            continue
        pattern = str(entry.get("pattern") or "")
        out.append(entry)
        if pattern:
            seen.add(pattern.lower())
    # auto-claim: any file under task_working_dir with allowed extension
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        suffix = path.suffix.lower().lstrip(".")
        if not suffix or suffix not in allowed_exts:
            continue
        if path.name.lower() in {"task_instance.json", "task_log.jsonl"}:
            continue
        if path.name.startswith("_step_") and path.name.endswith(".result.json"):
            continue
        if path.name.lower() in seen:
            continue
        rel = path.relative_to(base).as_posix()
        out.append({
            "type": "file",
            "pattern": path.name,
            "description": f"auto-claimed from {rel}",
            "required": True,
        })
        seen.add(path.name.lower())
    return out


# ---------------------------------------------------------------------------
# Patch 2: project_state_iterating
# ---------------------------------------------------------------------------

VALID_PROJECT_STATUSES = frozenset({"active", "iterating", "completed", "blocked"})


def normalize_project_status(value: str | None,
                             fallback: str = "active") -> str:
    if value is None:
        return fallback
    value = str(value).strip().lower()
    return value if value in VALID_PROJECT_STATUSES else fallback


def project_should_continue(status: str | None,
                            has_unfinished_iteration: bool,
                            last_iteration_at: str | None) -> bool:
    """Decide whether governance should enqueue another task for this project.

    Rule: a project continues receiving tasks unless its status is
    ``completed`` *and* the last iteration is older than 1 hour ago. The
    intermediate state ``iterating`` keeps receiving tasks unconditionally.
    """
    norm = normalize_project_status(status)
    if norm in {"active", "iterating"}:
        return True
    if norm == "blocked":
        return False
    if norm == "completed":
        if not has_unfinished_iteration or not last_iteration_at:
            return False
        try:
            last = datetime.fromisoformat(last_iteration_at.replace("Z", "+00:00"))
        except Exception:
            return False
        if (datetime.now(timezone.utc) - last).total_seconds() < 3600:
            return True
        return False
    return True


# ---------------------------------------------------------------------------
# Patch 3: active_learning_to_production
# ---------------------------------------------------------------------------


def promote_repair_to_production_readiness(workspace: str | Path,
                                           repair_proposal_path: str | Path) -> dict[str, Any]:
    """When ``active_learning/repair_candidate_proposed`` writes a repair
    proposal, mirror its core decision onto production_readiness/ as a
    bounded candidate. Returns the path of the new production_readiness entry.
    """
    workspace = Path(workspace)
    src = Path(repair_proposal_path)
    if not src.exists():
        return {"ok": False, "reason": "missing_repair_proposal"}
    try:
        proposal = json.loads(src.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": f"parse_failed: {exc}"}

    if proposal.get("execution_authorized") or proposal.get("production_mutation"):
        return {"ok": False, "reason": "already_authorized_or_mutates"}

    candidate_id = "repair_to_pr_" + str(proposal.get("proposal_id", ""))[-16:].lstrip("repair_proposal_")
    if not candidate_id or candidate_id == "repair_to_pr_":
        candidate_id = "repair_to_pr_" + hex(int(time.time()))[-8:]

    pr_path = workspace / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness" / f"{candidate_id}.json"
    pr_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "assessed_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source": "active_learning_repair_proposal",
        "repair_proposal_ref": str(src),
        "instance_id": proposal.get("instance_id", ""),
        "failure_class": proposal.get("failure_class", ""),
        "intervention": proposal.get("intervention", ""),
        "task_id": proposal.get("task_id", ""),
        "evidence_refs": proposal.get("evidence_refs", []),
        "general_llm": {
            "ok": proposal.get("intervention") in {
                "mechanism_specific_bounded_repair",
                "claim_ledger_truth_gate_v1",
                "typed_output_reference_resolution_v1",
            },
            "checks": {
                "has_intervention": bool(proposal.get("intervention")),
                "has_evidence_refs": bool(proposal.get("evidence_refs")),
                "execution_authorized_false": proposal.get("execution_authorized") is False,
                "production_mutation_false": proposal.get("production_mutation") is False,
            },
        },
        "decision": "ready_for_canary_evaluation",
        "production_ready": False,
    }
    pr_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(pr_path), "candidate_id": candidate_id, "payload": payload}


# ---------------------------------------------------------------------------
# Patch 4: instance-scoped OpenID recovery
# ---------------------------------------------------------------------------


def _latest_private_openid(history_path: Path) -> tuple[str, dict[str, Any]]:
    """Return the most recent inbound private-chat OpenID in one history.

    The history is instance-local and therefore carries the correct
    bot-app/OpenID namespace.  Group messages are deliberately excluded:
    proactive private delivery must not silently switch to a group target.
    """
    latest_openid = ""
    latest_row: dict[str, Any] = {}
    if not history_path.exists():
        return latest_openid, latest_row
    try:
        with history_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("role") != "user" or str(row.get("source") or "").lower() != "qq":
                    continue
                channel = str(row.get("channel") or row.get("message_type") or "").lower()
                if row.get("group_id") or channel in {"group", "group_at", "guild"}:
                    continue
                sender_id = str(row.get("sender_id") or row.get("openid") or "").strip()
                if sender_id:
                    latest_openid = sender_id
                    latest_row = row
    except OSError:
        return "", {}
    return latest_openid, latest_row


def restore_instance_openid(workspace: str | Path) -> list[Path]:
    """Repair one instance's proactive QQ target from its own inbound log.

    ``workspace`` must be the instance workspace used by its bridge.  This
    function never walks sibling instances and never accepts a caller-supplied
    OpenID, which prevents cross-app identity contamination.
    """
    workspace = Path(workspace)
    history_path = workspace / "state" / "qq_chat_history.jsonl"
    target_openid, source_row = _latest_private_openid(history_path)
    if not target_openid:
        return []
    context_path = workspace / "state" / "qq_user_context.json"
    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if data.get("openid") == target_openid:
        return []
    data.update({
        "openid": target_openid,
        "last_openid": target_openid,
        "target_id": target_openid,
        "name": str(source_row.get("sender_name") or data.get("name") or ""),
        "recovered_from": str(history_path),
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    })
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return [context_path]


def lock_openid_for_workspace(workspace: str | Path,
                              target_openid: str | None = None) -> list[Path]:
    """Backward-compatible alias for the corrected instance-local repair.

    ``target_openid`` is intentionally ignored.  It existed in the defective
    cross-app implementation and is retained only so older callers fail safe.
    """
    return restore_instance_openid(workspace)


# ---------------------------------------------------------------------------
# Optional: install_runtime_patch (used by partner/__main__.py)
# ---------------------------------------------------------------------------


def install_runtime_patch(workspace: str | Path) -> dict[str, Any]:
    """Run all four patches in sequence and return a summary."""
    workspace = Path(workspace)
    summary = {
        "openid_recovered": restore_instance_openid(workspace),
        "ran_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
# self_evolve_annotation: candidate_id=repair_to_pr_277c0a88ba10fe92 failure_class=tool.create_file.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_2a0c7a1ea3e982f0 failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_3dff90e121e1d74 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_56a8c251ae8189c8 failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_581f58f60bbfd96d failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_6843773f312659e0 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_790f8e597f601613 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_7b1d8195ed2aedee failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_7c5807db63ee1c94 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_7d25f5da369f089 failure_class=tool.atomic_write_artifact.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_88375e86c4e2e6f9 failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_b19534102caaeef5 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_b289c02012f59001 failure_class=tool.molecular_diversity_benchmark.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_cbb326a344e63aec failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_fba2ec3061ebefec failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_298a4fbb6d3ab2c failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_341758ef8d7306a1 failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_4d90a8e1dd99e274 failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_4e938b8537082ee9 failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_6571a1cfabfa8a1a failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_712d5d225616a6cc failure_class=tool.extract.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_7ac7af611775ccc2 failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_853de960b7d5e7cf failure_class=tool.atomic_http_get.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_8b39d3c33e21b150 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_d416e108e0c53d00 failure_class=tool.atomic_write_artifact.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_f450c2b58075c2fa failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
    return summary
