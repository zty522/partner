"""Sprint 27 gates for useful multi-project learning and self-evolution.

The runtime historically counted distinct files and successful Events as
progress.  This module projects the append-only trajectory ledger into a
stricter view: project progress, external learning adoption, and Partner
self-evolution are three different claims.  It is deliberately read-only and
cannot promote a Candidate.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .storage import workspace_root


PROJECT_IDS = (
    "xiaohongshu_operations",
    "molecular_generation",
    "molecular_dynamics_study",
    "literature_github_learning",
    "hermes_partner_explore",
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def effective_trajectories(workspace: str | Path) -> list[dict[str, Any]]:
    """Return the latest append-only revision of every real trajectory."""
    root = workspace_root(str(workspace))
    path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    latest: dict[str, tuple[int, int, dict[str, Any]]] = {}
    for offset, row in enumerate(_jsonl(path)):
        trajectory_id = str(row.get("trajectory_id") or "")
        if not trajectory_id or row.get("synthetic_fixture"):
            continue
        revision = int(row.get("revision") or 1)
        prior = latest.get(trajectory_id)
        if prior is None or (revision, offset) >= (prior[0], prior[1]):
            latest[trajectory_id] = (revision, offset, row)
    return [value[2] for value in sorted(latest.values(), key=lambda value: value[1])]


def option_health(workspace: str | Path, project_id: str,
                  arm_ids: list[str], *, window: int = 4,
                  negative_streak: int = 2) -> dict[str, Any]:
    """Identify arms that recently produced only repeats/failures.

    Suppression is relative: if every arm is exhausted, the least recently
    sampled arm remains available so the project cannot deadlock.  A generated
    hypothesis arm is treated as new and therefore remains eligible.
    """
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in effective_trajectories(workspace):
        if str(row.get("project_id") or "") != project_id:
            continue
        action = row.get("action") or {}
        arm = str(action.get("selection_arm_id")
                  or action.get("experience_candidate_id")
                  or action.get("native_action_id") or "")
        if arm:
            by_arm[arm].append(row)
    details: list[dict[str, Any]] = []
    suppressed: set[str] = set()
    for arm in arm_ids:
        recent = by_arm.get(arm, [])[-max(1, int(window)):]
        tail = recent[-max(1, int(negative_streak)):]
        stagnant = bool(len(tail) >= negative_streak and all(
            float(row.get("reward") or 0.0) <= 0
            or bool((row.get("outcome") or {}).get("duplicate_outcome"))
            or not bool((row.get("outcome") or {}).get("business_progress")
                        or (row.get("outcome") or {}).get("learning_progress"))
            for row in tail
        ))
        if stagnant:
            suppressed.add(arm)
        details.append({
            "arm_id": arm,
            "samples": len(by_arm.get(arm, [])),
            "recent_rewards": [float(row.get("reward") or 0.0) for row in recent],
            "nonpositive_streak": len(tail) if stagnant else 0,
            "stagnant": stagnant,
        })
    eligible = [arm for arm in arm_ids if arm not in suppressed]
    fail_open_reason = ""
    if arm_ids and not eligible:
        # Keep exactly one escape hatch; hypothesis generation can replace it
        # on the next selection pass when the negative window is observed.
        least_sampled = min(details, key=lambda row: (row["samples"], arm_ids.index(row["arm_id"])))
        eligible = [str(least_sampled["arm_id"])]
        suppressed.discard(eligible[0])
        fail_open_reason = "all_known_arms_stagnant_keep_least_sampled_escape_hatch"
    return {
        "project_id": project_id,
        "eligible_arm_ids": eligible,
        "suppressed_arm_ids": sorted(suppressed),
        "details": details,
        "fail_open_reason": fail_open_reason,
    }


def _matched_adoption_uplifts(root: Path) -> list[dict[str, Any]]:
    """Return strict downstream business wins caused by an adoption Candidate.

    Context recall, a generated report, or merely consuming a typed context is
    intentionally insufficient.  A future experiment must persist two arms
    with the same experiment/match identity, the same non-empty adoption
    Candidate identity, a passed truth audit, and a directional downstream
    business reward.  Keeping this schema explicit makes missing evidence fail
    closed instead of guessing from old heterogeneous observation files.
    """
    grouped: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    base = root / "share/mind/governance/experiment_observations"
    for path in base.glob("**/*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        arm = str(row.get("policy_arm") or "")
        candidate_id = str(row.get("adoption_candidate_id") or "")
        experiment_id = str(row.get("experiment_id") or "")
        match_key = str(row.get("match_key") or "")
        if arm not in {"baseline", "candidate"} or not all(
                (candidate_id, experiment_id, match_key)):
            continue
        grouped[(experiment_id, match_key, candidate_id)][arm] = row

    wins: list[dict[str, Any]] = []
    for (experiment_id, match_key, candidate_id), arms in grouped.items():
        if set(arms) != {"baseline", "candidate"}:
            continue
        baseline, candidate = arms["baseline"], arms["candidate"]
        before = baseline.get("downstream_business_reward")
        after = candidate.get("downstream_business_reward")
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            continue
        truth = candidate.get("truth_audit") or {}
        outcome = candidate.get("outcome") or {}
        if (truth.get("passed") is True and outcome.get("business_progress") is True
                and float(after) > float(before)):
            wins.append({
                "experiment_id": experiment_id,
                "match_key": match_key,
                "adoption_candidate_id": candidate_id,
                "baseline_business_reward": float(before),
                "candidate_business_reward": float(after),
                "delta": round(float(after) - float(before), 6),
            })
    return wins


def assess_sprint27(workspace: str | Path, *, recent_limit: int = 30) -> dict[str, Any]:
    """Audit the three independent Sprint 27 outcome claims."""
    root = workspace_root(str(workspace))
    rows = effective_trajectories(root)
    projects: dict[str, Any] = {}
    for project_id in PROJECT_IDS:
        recent = [row for row in rows if str(row.get("project_id") or "") == project_id][
            -max(1, int(recent_limit)):
        ]
        business = [row for row in recent if (row.get("outcome") or {}).get("business_progress") is True]
        learning = [row for row in recent if (row.get("outcome") or {}).get("learning_progress") is True]
        false_success = [row for row in recent if (row.get("outcome") or {}).get("false_success") is True]
        projects[project_id] = {
            "samples": len(recent),
            "business_progress": len(business),
            "learning_progress": len(learning),
            "false_success": len(false_success),
            "distinct_business_artifacts": len({artifact for row in business
                for artifact in (row.get("outcome") or {}).get("artifacts") or []}),
            "latest_trajectory_id": str(recent[-1].get("trajectory_id") or "") if recent else "",
        }

    candidate_rows: list[dict[str, Any]] = []
    for path in (root / "share/mind/governance/research_adoption_candidates").glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if isinstance(value, dict):
            candidate_rows.append(value)
    valid_contexts: list[Path] = []
    for path in (root / "instances/04/state/tasks").glob("*/research_adoption_context.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if (value.get("latest_receipt_id") and value.get("trajectory_refs")
                and value.get("research_evidence_refs")
                and value.get("production_effective") is False):
            valid_contexts.append(path)
    adoption_runs = [row for row in rows
                     if (row.get("outcome") or {}).get("knowledge_adoption_progress") is True]
    adoption_effects = [row for row in rows
                        if str((row.get("action") or {}).get("native_action_id") or "")
                        == "04_adoption_effect_probe"
                        and (row.get("outcome") or {}).get("business_progress") is True]
    adoption_uplifts = _matched_adoption_uplifts(root)

    event_rows = _jsonl(root / "share/mind/governance/evolution_events.jsonl")
    activations = [row for row in event_rows if row.get("event_type") == "policy/activated"
                   and (row.get("payload") or {}).get("production_effective") is True]
    verified_uplifts: list[dict[str, Any]] = []
    for activation in activations:
        project_id = str(activation.get("project_id") or "")
        created_at = str(activation.get("created_at") or "")
        before = [row for row in rows if str(row.get("project_id") or "") == project_id
                  and str(row.get("created_at") or "") < created_at][-5:]
        after = [row for row in rows if str(row.get("project_id") or "") == project_id
                 and str(row.get("created_at") or "") > created_at][:5]
        before_mean = sum(float(row.get("reward") or 0.0) for row in before) / len(before) if before else 0.0
        after_mean = sum(float(row.get("reward") or 0.0) for row in after) / len(after) if after else 0.0
        if (len(before) >= 2 and len(after) >= 2 and after_mean > before_mean
                and any((row.get("outcome") or {}).get("business_progress") is True for row in after)):
            verified_uplifts.append({"candidate_id": activation.get("subject_id"),
                                     "project_id": project_id,
                                     "before_mean_reward": round(before_mean, 4),
                                     "after_mean_reward": round(after_mean, 4)})

    project_checks = {project_id: bool(value["business_progress"] >= 1
                                       and value["distinct_business_artifacts"] >= 1
                                       and value["false_success"] == 0)
                      for project_id, value in projects.items()}
    active_learning_checks = {
        "external_learning_observed": projects["literature_github_learning"]["learning_progress"] >= 1,
        "adoption_candidate_proposed": any(str(row.get("decision") or "").startswith("proposed")
                                            for row in candidate_rows),
        "candidate_executed_in_shadow": bool(valid_contexts or adoption_runs),
        "learning_changed_a_later_action": bool(adoption_effects),
        "matched_downstream_business_uplift": bool(adoption_uplifts),
    }
    self_evolution_checks = {
        "production_activation_exists": bool(activations),
        "post_activation_business_uplift": bool(verified_uplifts),
        "no_false_success_in_recent_portfolio": all(value["false_success"] == 0
                                                     for value in projects.values()),
    }
    return {
        "schema_version": 1,
        "gate": "sprint27_multi_project_learning_and_improvement",
        "ok": bool(all(project_checks.values())
                   and all(active_learning_checks.values())
                   and all(self_evolution_checks.values())),
        "project_progress": {"checks": project_checks, "details": projects,
                             "ok": all(project_checks.values())},
        "external_active_learning": {"checks": active_learning_checks,
                                     "verified_uplifts": adoption_uplifts,
                                     "ok": all(active_learning_checks.values())},
        "partner_self_evolution": {"checks": self_evolution_checks,
                                   "verified_uplifts": verified_uplifts,
                                   "ok": all(self_evolution_checks.values())},
        "production_effective": False,
        "interpretation": "passed" if all(project_checks.values())
        and all(active_learning_checks.values()) and all(self_evolution_checks.values())
        else "continue bounded work; no maturity claim",
    }
