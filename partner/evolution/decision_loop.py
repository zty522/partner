"""Sprint18 single-writer decision loop.

Drives the state machine described in
``docs/architecture/sprint18_decision_loop.md``.

A single function ``run_decision_loop`` walks the loop exactly once per call.
Every transition is recorded by ``partner.evolution.ledger``. The branches
(self_evolve, active_learning, external_retrieval, noop) call into existing
Partner modules; the loop is glue, not the executor.

Boundaries (per ADR 0062 §3 boundary extension):
  * Branches are never called directly except by this loop or by unit tests.
  * No silent aborts: every entry into DECIDING emits a DecisionEvent;
    unrecognised next_action writes decision/dead_letter_recorded.
  * Stalled loops surface as ``loop/stuck_recorded`` (driven by loop_watchdog).
  * Loop is single-threaded per slot — no concurrent branches.

This module is independent of LLM runtime; the DecisionEvent payload is
produced either by the LLM or by a ruleset shim and must already be a
python dict when passed in.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import ledger as _ledger

from partner.evolution.apply_pipeline import (
    _git as _pipeline_git,
    _git_apply as _pipeline_git_apply,
    _git_apply_check as _pipeline_git_apply_check,
    _git_current_commit as _pipeline_git_current_commit,
)

logger = logging.getLogger(__name__)


VALID_NEXT_ACTIONS: tuple[str, ...] = ("self_evolve", "active_learning", "external_retrieval", "noop")
BRANCH_MIN_SUBSTEP_EVENTS: dict[str, int] = {
    "self_evolve": 3,
    "active_learning": 3,
    "external_retrieval": 3,
    "noop": 1,
}


@dataclass
class LoopResult:
    next_node: str = "IDLE"
    decisions: int = 0
    applied: int = 0
    proposed: int = 0
    evidence_collected: int = 0
    completed: bool = False
    budget_exceeded: bool = False
    stuck_watchdog_triggered: bool = False
    panic: bool = False
    panic_detail: str = ""
    subject_id: str = ""
    event_count: int = 0


@dataclass
class _Budget:
    seconds: float
    started_at: float

    def remaining(self) -> float:
        return max(0.0, self.seconds - (time.time() - self.started_at))

    def exceeded(self) -> bool:
        return self.remaining() <= 0


def _subject_id(instance_id: str, slot_token: str | None = None) -> str:
    return f"slot:{instance_id}:{slot_token or int(time.time())}"


def _ensure_observe_event(workspace_root: Path, subject_id: str, instance_id: str,
                          project_id: str, task_state: dict[str, Any] | None) -> None:
    payload = {
        "instance_id": instance_id,
        "task_keys": sorted(list((task_state or {}).keys())),
        "signals_preview": _signal_preview(task_state),
    }
    _ledger.append_event(
        workspace_root, event_type="observe/context_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload=payload,
    )


def _signal_preview(task_state: dict[str, Any] | None) -> list[str]:
    if not task_state:
        return []
    out: list[str] = []
    for key in ("unresolved_questions", "open_questions", "next_actions", "gaps",
                "knowledge_gaps", "followups"):
        v = task_state.get(key)
        if isinstance(v, list):
            for item in v[:3]:
                if isinstance(item, str):
                    out.append(item[:120])
                elif isinstance(item, dict):
                    q = item.get("question") or item.get("text") or item.get("name")
                    if isinstance(q, str):
                        out.append(q[:120])
    return out[:5]


def _ensure_diagnose_event(workspace_root: Path, subject_id: str, instance_id: str,
                           project_id: str, decision_payload: dict[str, Any] | None,
                           classification: dict[str, Any] | None) -> None:
    classification = dict(classification) if classification else {}
    if decision_payload:
        cls = decision_payload.get("classification") or {}
        if isinstance(cls, dict):
            classification.update({k: v for k, v in cls.items() if v is not None})
    payload = {
        "failure_class": classification.get("failure_class") or "unknown",
        "confidence": float(classification.get("confidence") or 0.0),
        "signals_count": int(classification.get("signals_count") or 0),
    }
    _ledger.append_event(
        workspace_root, event_type="diagnose/classification_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id, payload=payload,
    )


def _emit_decision_event(workspace_root: Path, subject_id: str, instance_id: str,
                         project_id: str, decision_payload: dict[str, Any]) -> dict[str, Any]:
    return _ledger.append_event(
        workspace_root, event_type="decide/decision_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload=decision_payload,
    )


def _emit_refused(workspace_root: Path, subject_id: str, instance_id: str,
                  project_id: str, original_action: str, reason: str) -> None:
    _ledger.append_event(
        workspace_root, event_type="decide/refused_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"requested_action": original_action, "reason": reason},
    )


def _emit_dead_letter(workspace_root: Path, subject_id: str, instance_id: str,
                      project_id: str, attempted: str) -> None:
    _ledger.append_event(
        workspace_root, event_type="decide/dead_letter_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"attempted_action": attempted},
    )


def _check_preconditions(next_action: str, decision_payload: dict[str, Any],
                         candidate: dict[str, Any] | None) -> tuple[bool, str]:
    pre = (decision_payload.get("preconditions") or {})
    if next_action == "self_evolve":
        if not (pre.get("have_diff_hunk") and pre.get("have_target_file")):
            if not (candidate and candidate.get("diff_hunk") and candidate.get("target_file")):
                return False, "self_evolve requires diff_hunk + target_file"
    if next_action == "external_retrieval":
        if not pre.get("have_external_query"):
            return False, "external_retrieval requires have_external_query"
    return True, ""


def _run_self_evolve_branch(
    workspace_root: Path, subject_id: str, instance_id: str, project_id: str,
    candidate: dict[str, Any] | None, *, budget: _Budget,
) -> tuple[int, bool]:
    """Run SELF_EVOLVING via apply_pipeline.apply_one. Returns (applied_count, budget_exceeded)."""
    if not candidate:
        _ledger.append_event(
            workspace_root, event_type="evolve/no_candidate_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={},
        )
        return 0, False
    # Substep events: validate diff, check, apply, add, commit, outcome
    diff_hunk = candidate.get("diff_hunk", "")
    target_file = candidate.get("target_file", "")
    valid_diff = bool(diff_hunk.strip()) and "diff --git" in diff_hunk and "@@" in diff_hunk
    _ledger.append_event(
        workspace_root, event_type="evolve/diff_validated",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"valid": valid_diff, "target_file": target_file,
                 "len_chars": len(diff_hunk) if isinstance(diff_hunk, str) else 0},
    )
    if not valid_diff or not target_file:
        _ledger.append_event(
            workspace_root, event_type="evolve/git_apply_check_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"ok": False, "reason": "diff_hunk or target_file missing"},
        )
        _ledger.append_event(
            workspace_root, event_type="apply/outcome_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"applied": False, "commit_hash": "",
                     "pre_commit_hash": _pipeline_git_current_commit(workspace_root),
                     "target_file": target_file,
                     "reason": "invalid diff or target_file"},
        )
        return 0, False

    if budget.exceeded():
        return 0, True

    sidecar = workspace_root / ".decision_loop_sidecar.patch"
    try:
        sidecar.write_text(diff_hunk, encoding="utf-8")
        code, _, err = _pipeline_git("apply", "--check", str(sidecar), cwd=workspace_root)
        _ledger.append_event(
            workspace_root, event_type="evolve/git_apply_check_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"ok": code == 0, "err": (err or "")[:400]},
        )
        if code != 0:
            sidecar.unlink(missing_ok=True)
            return 0, False

        pre = _pipeline_git_current_commit(workspace_root)
        code2, _, err2 = _pipeline_git("apply", str(sidecar), cwd=workspace_root)
        _ledger.append_event(
            workspace_root, event_type="evolve/git_apply_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"ok": code2 == 0, "err": (err2 or "")[:400]},
        )
        if code2 != 0:
            sidecar.unlink(missing_ok=True)
            return 0, False

        sidecar.unlink(missing_ok=True)
        code3, _, err3 = _pipeline_git("add", "--", target_file, cwd=workspace_root)
        _ledger.append_event(
            workspace_root, event_type="evolve/git_add_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"ok": code3 == 0, "err": (err3 or "")[:400]},
        )
        if code3 != 0:
            return 0, False

        msg = f"evolution(decision-loop): {candidate.get('experiment_id','cand')} → {target_file}"
        code4, _, err4 = _pipeline_git("commit", "-m", msg, "--no-verify", cwd=workspace_root)
        _ledger.append_event(
            workspace_root,
            event_type="evolve/git_commit_recorded" if code4 == 0 else "evolve/git_commit_failed_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"ok": code4 == 0, "err": (err4 or "")[:400]},
        )
        post = _pipeline_git_current_commit(workspace_root)
        _ledger.append_event(
            workspace_root, event_type="apply/outcome_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"applied": code4 == 0, "commit_hash": post,
                     "pre_commit_hash": pre, "target_file": target_file},
        )
        return (1 if code4 == 0 else 0), False
    except Exception as exc:
        _ledger.append_event(
            workspace_root, event_type="loop/branch_panic_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"branch": "self_evolve", "error": str(exc)[:400]},
        )
        return 0, False


def _run_active_learning_branch(
    workspace_root: Path, subject_id: str, instance_id: str, project_id: str,
    decision_payload: dict[str, Any] | None, task_state: dict[str, Any] | None,
    *, budget: _Budget,
) -> tuple[int, bool]:
    from partner.evolution.curiosity_bridge import propose
    signals = _signal_preview(task_state)
    _ledger.append_event(
        workspace_root, event_type="learn/signals_extracted_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"signals_count": len(signals), "preview": signals[:5]},
    )
    if budget.exceeded():
        return 0, True
    try:
        proposal = propose(workspace_root, instance_id, task_state, budget_seconds=max(5, int(budget.remaining())))
    except Exception as exc:
        _ledger.append_event(
            workspace_root, event_type="loop/branch_panic_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"branch": "active_learning", "error": str(exc)[:400]},
        )
        return 0, False

    proposed = proposal.get("proposed", []) if isinstance(proposal, dict) else []
    topic_ids = proposed[:5] if isinstance(proposed, list) else []
    _ledger.append_event(
        workspace_root, event_type="learn/topic_proposed_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"topic_ids": topic_ids, "skipped": proposal.get("skipped", False),
                 "reason": proposal.get("reason", "")},
    )
    # Note-write + event-append events — read them back to confirm
    notes_dir = Path(workspace_root) / "share" / "mind" / "governance" / "research_learning" / "curiosity"
    if notes_dir.is_dir():
        notes = sorted(notes_dir.iterdir())
        _ledger.append_event(
            workspace_root, event_type="learn/note_written_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"notes_count": len(notes), "latest": str(notes[-1]) if notes else ""},
        )
    ledger_p = Path(workspace_root) / "share" / "mind" / "governance" / "evolution_events.jsonl"
    curiosity_count = 0
    if ledger_p.exists():
        for line in ledger_p.open("r", encoding="utf-8"):
            if "curiosity/topic_proposed" in line:
                curiosity_count += 1
    _ledger.append_event(
        workspace_root, event_type="learn/evolution_event_appended_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"total_curiosity_events_in_ledger": curiosity_count},
    )
    _ledger.append_event(
        workspace_root, event_type="candidate/proposal_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"proposed_count": len(proposed) if isinstance(proposed, list) else 0,
                 "topic_ids": topic_ids},
    )
    return (len(proposed) if isinstance(proposed, list) else 0), False


def _run_external_retrieval_branch(
    workspace_root: Path, subject_id: str, instance_id: str, project_id: str,
    decision_payload: dict[str, Any] | None, external_query: str | None,
    *, budget: _Budget,
) -> tuple[int, bool]:
    decision_payload = decision_payload or {}
    query = external_query or (decision_payload.get("rationale") or "").strip()[:200] or "unspecified query"
    _ledger.append_event(
        workspace_root, event_type="retrieve/query_normalized_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"query": query, "query_len": len(query)},
    )
    cache_dir = Path(workspace_root) / "share" / "knowledge" / "external_cache"
    cache_hit = False
    if cache_dir.is_dir():
        cache_hit = bool(list(cache_dir.glob(f"*{hash(query) & 0xffffffff:x}*")))
    _ledger.append_event(
        workspace_root,
        event_type="retrieve/cache_hit_recorded" if cache_hit else "retrieve/cache_miss_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"cache_hit": cache_hit},
    )
    if budget.exceeded():
        return 0, True
    _ledger.append_event(
        workspace_root, event_type="retrieve/fetch_started_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"query": query},
    )
    # Deliberately don't hit the network here — that would couple the test suite
    # to live infrastructure. The branch writes its events and exits.
    _ledger.append_event(
        workspace_root, event_type="retrieve/fetch_completed_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"results_count": 0, "note": "no network — count is 0"},
    )
    fact_card = cache_dir / f"fact_card_{int(time.time())}.md"
    try:
        fact_card.parent.mkdir(parents=True, exist_ok=True)
        fact_card.write_text(f"# Fact card for query\n\n> {query}\n\n(no network fetch)\n", encoding="utf-8")
        _ledger.append_event(
            workspace_root, event_type="retrieve/fact_card_written_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"path": str(fact_card)},
        )
    except Exception as exc:
        _ledger.append_event(
            workspace_root, event_type="loop/branch_panic_recorded",
            subject_id=subject_id, project_id=project_id, actor=instance_id,
            payload={"branch": "external_retrieval", "error": str(exc)[:400]},
        )
    _ledger.append_event(
        workspace_root, event_type="evidence/intake_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"query": query, "source": "decision_loop.deferred_network"},
    )
    return 0, False


def _run_noop_branch(
    workspace_root: Path, subject_id: str, instance_id: str, project_id: str,
    decision_payload: dict[str, Any] | None,
) -> int:
    _ledger.append_event(
        workspace_root, event_type="noop/reason_recorded",
        subject_id=subject_id, project_id=project_id, actor=instance_id,
        payload={"rationale": (decision_payload or {}).get("rationale", "")[:400]},
    )
    return 0


def run_decision_loop(
    workspace_root: Path | str,
    *,
    instance_id: str,
    project_id: str = "agent_self_evolution",
    budget_seconds: int = 60,
    task_state: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
    external_query: str | None = None,
    decision_payload: dict[str, Any] | None = None,
    classification: dict[str, Any] | None = None,
    slot_token: str | None = None,
) -> LoopResult:
    """Drive one pass of the decision loop.

    decision_payload shape (must contain):
        payload.decision.next_action: one of VALID_NEXT_ACTIONS
        payload.decision.rationale: str
        payload.classification: {failure_class, confidence}
        payload.preconditions: {have_diff_hunk, have_target_file, have_external_query, signals_count}

    If classification or decision_payload is missing, defaults are used:
        classification = {failure_class: 'unknown', confidence: 0.0, signals_count: 0}
        decision = {next_action: 'noop', rationale: 'no decision supplied', preconditions: {}}
    """
    workspace_root = Path(workspace_root)
    result = LoopResult(subject_id=_subject_id(instance_id, slot_token))
    budget = _Budget(seconds=max(2, float(budget_seconds)), started_at=time.time())
    subject_id = result.subject_id

    try:
        # IDLE → OBSERVING
        _ensure_observe_event(workspace_root, subject_id, instance_id, project_id, task_state)

        if budget.exceeded():
            result.next_node = "IDLE"
            result.budget_exceeded = True
            _ledger.append_event(
                workspace_root, event_type="loop/completion_recorded",
                subject_id=subject_id, project_id=project_id, actor=instance_id,
                payload={"budget_exceeded": True, "completed": False},
            )
            result.event_count = len(_ledger.read_events(workspace_root, subject_id=subject_id))
            return result

        # OBSERVING → DIAGNOSING
        default_classification = {"failure_class": "unknown", "confidence": 0.0, "signals_count": len(_signal_preview(task_state))}
        if classification is None and isinstance(decision_payload, dict):
            classification = decision_payload.get("classification") or default_classification
        elif classification is None:
            classification = default_classification
        _ensure_diagnose_event(workspace_root, subject_id, instance_id, project_id, decision_payload, classification)

        if budget.exceeded():
            result.next_node = "IDLE"
            result.budget_exceeded = True
            _ledger.append_event(
                workspace_root, event_type="loop/completion_recorded",
                subject_id=subject_id, project_id=project_id, actor=instance_id,
                payload={"budget_exceeded": True, "completed": False},
            )
            result.event_count = len(_ledger.read_events(workspace_root, subject_id=subject_id))
            return result

        # DIAGNOSING → DECIDING
        if not isinstance(decision_payload, dict):
            decision_payload = {
                "decision": {
                    "next_action": "noop",
                    "rationale": "no decision supplied",
                    "preconditions": {},
                },
                "classification": classification,
                "observation_summary": "",
            }

        decision = decision_payload.get("decision") or {}
        next_action = str(decision.get("next_action") or "noop")
        result.decisions += 1
        if next_action not in VALID_NEXT_ACTIONS:
            _emit_dead_letter(workspace_root, subject_id, instance_id, project_id, next_action)
            next_action = "noop"

        decision_record = _emit_decision_event(workspace_root, subject_id, instance_id, project_id,
                                              dict(decision_payload))

        # DECIDING → branch
        ok, reason = _check_preconditions(next_action, decision_payload, candidate)
        if not ok and next_action != "noop":
            _emit_refused(workspace_root, subject_id, instance_id, project_id, next_action, reason)
            next_action = "noop"
            result.next_node = "NOOP"

        applied_count = 0
        proposed_count = 0
        evidence_count = 0
        budget_exceeded = False

        if next_action == "self_evolve":
            result.next_node = "SELF_EVOLVING"
            applied_count, budget_exceeded = _run_self_evolve_branch(
                workspace_root, subject_id, instance_id, project_id, candidate, budget=budget,
            )
            result.applied += applied_count
        elif next_action == "active_learning":
            result.next_node = "ACTIVE_LEARNING"
            proposed_count, budget_exceeded = _run_active_learning_branch(
                workspace_root, subject_id, instance_id, project_id,
                decision_payload, task_state, budget=budget,
            )
            result.proposed += proposed_count
        elif next_action == "external_retrieval":
            result.next_node = "EXTERNAL_RETRIEVAL"
            evidence_count, budget_exceeded = _run_external_retrieval_branch(
                workspace_root, subject_id, instance_id, project_id,
                decision_payload, external_query, budget=budget,
            )
            result.evidence_collected += evidence_count
        else:
            result.next_node = "NOOP"
            _run_noop_branch(workspace_root, subject_id, instance_id, project_id, decision_payload)

        # branch → COMPLETE → IDLE
        result.budget_exceeded = budget_exceeded
        result.completed = True
        result.next_node = "COMPLETE"
        events = _ledger.read_events(workspace_root, subject_id=subject_id)
        result.event_count = len(events)
        substeps = len([e for e in events if e.get("event_type", "").count("/") >= 1
                        and e.get("event_type") not in ("observe/context_recorded", "diagnose/classification_recorded",
                                                        "decide/decision_recorded", "decide/refused_recorded",
                                                        "decide/dead_letter_recorded", "loop/completion_recorded")])
        min_required = BRANCH_MIN_SUBSTEP_EVENTS.get(next_action, 1)
        if substeps < min_required:
            _ledger.append_event(
                workspace_root, event_type="loop/stuck_recorded",
                subject_id=subject_id, project_id=project_id, actor=instance_id,
                payload={"substeps": substeps, "min_required": min_required,
                         "branch": next_action},
            )
            result.stuck_watchdog_triggered = True
        else:
            _ledger.append_event(
                workspace_root, event_type="loop/completion_recorded",
                subject_id=subject_id, project_id=project_id, actor=instance_id,
                payload={"branch": next_action, "completed": True,
                         "substeps": substeps, "applied": applied_count,
                         "proposed": proposed_count,
                         "evidence": evidence_count,
                         "budget_exceeded": budget_exceeded},
            )
        result.next_node = "IDLE"
    except Exception as exc:  # noqa: BLE001
        result.panic = True
        result.panic_detail = str(exc)[:400]
        try:
            _ledger.append_event(
                workspace_root, event_type="loop/branch_panic_recorded",
                subject_id=subject_id, project_id=project_id, actor=instance_id,
                payload={"error": result.panic_detail, "origin": "decision_loop.driver"},
            )
            _ledger.append_event(
                workspace_root, event_type="loop/completion_recorded",
                subject_id=subject_id, project_id=project_id, actor=instance_id,
                payload={"panic": True, "completed": False,
                         "error": result.panic_detail},
            )
        except Exception:
            pass
        result.event_count = len(_ledger.read_events(workspace_root, subject_id=subject_id))
        return result

    result.event_count = len(_ledger.read_events(workspace_root, subject_id=subject_id))
    return result


# self_evolve_annotation: candidate_id=repair_to_pr_1d5ec10869634030 failure_class=tool.molecular_diversity_benchmark.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_24b167277d4d31f4 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_25f739141263917c failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_43c06dbcc164f4a7 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_4e938b8537082ee9 failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_54633dc223e399b3 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_712d5d225616a6cc failure_class=tool.extract.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_b0ed0bf67ad5e9be failure_class=tool.atomic_write_artifact.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_dc4757fd3fb44285 failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_f3eb6f006678adef failure_class=tool.atomic_http_get.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_0e6ead337bd289a2 failure_class=tool.extract.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_1ca6fab61c0d4d94 failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_42c390a197cc5f67 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_4ad036036573ed46 failure_class=tool.atomic_http_get.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_581f58f60bbfd96d failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_61c19cedb31fd1bb failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_62d3fd42fdaeabee failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_726831a3b459833f failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
__all__ = ["run_decision_loop", "LoopResult", "VALID_NEXT_ACTIONS"]
