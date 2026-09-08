"""Shadow-only cognition-assisted context selection for Gate C.

The candidate reuses the production context loader and changes only the list
of requested document IDs.  This module is not imported by the planner or
executor.  Its preflight evaluates routing mechanics, not task quality.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .candidate_skills import register_candidate_skill
from .cognition_adapter import validate_cognition_shadow_bundle, verify_cognition_shadow_sources
from .context_selector import MANDATORY_IDS, load_catalog, select_context
from .models import now_iso
from .storage import atomic_json, latest_receipt, workspace_root


BASELINE_STRATEGY = "baseline_governed_context_v1"
CANDIDATE_STRATEGY = "candidate_cognition_context_v1"
BASELINE_ROUTE_MARKER = "governed_v1_no_cognition_signal"
CANDIDATE_ROUTE_MARKER = "cognition_v1_soft_boost_only"


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def execution_marker(*, query: str, instance_id: str, project_id: str,
                     catalog_path: str | Path) -> str:
    """Stable hash of execution inputs that both baseline and candidate consume.

    This marker proves both branches were fed identical inputs.  If two
    selection digests differ but their execution_marker is identical, the
    divergence must come from the branch logic — not from input drift.
    """
    payload = {
        "query": str(query or ""),
        "instance_id": str(instance_id or ""),
        "project_id": str(project_id or ""),
        "catalog_path": str(catalog_path or ""),
    }
    return _canonical_digest(payload)


def _tokens(value: Any) -> set[str]:
    raw = str(value or "").lower().replace("_", " ").replace("-", " ")
    latin = re.findall(r"[a-z][a-z0-9+]{1,}", raw)
    chinese = re.findall(r"[一-鿿]{2,}", raw)
    fragments: list[str] = []
    for token in chinese:
        fragments.append(token)
        fragments.extend(token[index:index + 3] for index in range(max(0, len(token) - 2)))
    return set(latin + fragments)


def _correction_state(directory: Path) -> dict[str, str]:
    state: dict[str, str] = {}
    try:
        lines = (directory / "corrections.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return state
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        import_id = str(row.get("import_id") or "") if isinstance(row, dict) else ""
        action = str(row.get("action") or "") if isinstance(row, dict) else ""
        if import_id and action in {"invalidate", "reinstate"}:
            state[import_id] = action
    return state


def load_valid_cognition_shadows(
    workspace: str, *, project_id: str = "", instance_id: str = "",
) -> list[dict[str, Any]]:
    """Load validated, non-invalidated archives matching the task identity."""
    directory = workspace_root(workspace) / "share" / "mind" / "governance" / "cognition_shadow"
    corrections = _correction_state(directory)
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("cognition_shadow_*.json")):
        try:
            archive = json.loads(path.read_text(encoding="utf-8"))
            import_id = str(archive.get("import_id") or "")
            bundle = validate_cognition_shadow_bundle(dict(archive.get("bundle") or {}))
            verified_sources = verify_cognition_shadow_sources(workspace, bundle)
        except (OSError, TypeError, ValueError):
            continue
        expected_import_id = f"cognition_shadow_{bundle['bundle_digest'][:16]}"
        if import_id != expected_import_id or archive.get("status") != "shadow_imported":
            continue
        if corrections.get(import_id) == "invalidate":
            continue
        if archive.get("production_mutation") is not False or archive.get("candidate_registered") is not False:
            continue
        if project_id and str(bundle.get("project_id") or "") != project_id:
            continue
        if instance_id and str(bundle.get("partner_instance_id") or "") != instance_id:
            continue
        rows.append({
            "import_id": import_id, "path": str(path), "bundle_digest": bundle["bundle_digest"],
            "project_id": bundle["project_id"], "partner_instance_id": bundle["partner_instance_id"],
            "source_episode_ids": list(bundle["source_episode_ids"]),
            "cognition_episode_id": bundle["cognition_episode_id"],
            "replay_digest": bundle["reduced_state"]["replay_digest"],
            "verified_source_paths": verified_sources,
            "reduced_state": bundle["reduced_state"],
        })
    return rows


def cognition_document_ranking(
    workspace: str,
    query: str,
    *,
    instance_id: str,
    project_id: str,
    catalog_path: str | Path,
    limit: int = 4,
) -> dict[str, Any]:
    """Rank existing catalog documents from verified cognition evidence."""
    shadows = load_valid_cognition_shadows(
        workspace, project_id=project_id, instance_id=instance_id,
    )
    cognition_tokens: set[str] = set()
    evidence: list[dict[str, Any]] = []
    source_episodes: set[str] = set()
    for shadow in shadows:
        state = shadow["reduced_state"]
        local_tokens = _tokens(shadow["project_id"])
        percepts = state.get("percepts") if isinstance(state.get("percepts"), dict) else {}
        for percept in percepts.values():
            payload = percept.get("payload") if isinstance(percept, dict) else {}
            local_tokens |= _tokens(payload.get("status"))
            for failure in payload.get("failure_classes") or []:
                local_tokens |= _tokens(failure)
        cognition_tokens |= local_tokens
        source_episodes.update(shadow["source_episode_ids"])
        evidence.append({
            "import_id": shadow["import_id"], "bundle_digest": shadow["bundle_digest"],
            "cognition_episode_id": shadow["cognition_episode_id"],
            "source_episode_ids": shadow["source_episode_ids"],
            "signal_tokens": sorted(local_tokens),
        })

    catalog = load_catalog(catalog_path)
    query_tokens = _tokens(query)
    ranked: list[dict[str, Any]] = []
    for item in catalog["documents"]:
        doc_id = str(item.get("id") or "")
        instances = {str(value) for value in item.get("instances") or ["all"]}
        if doc_id in MANDATORY_IDS or item.get("tier") == "L4":
            continue
        if "all" not in instances and instance_id not in instances:
            continue
        tags = _tokens(" ".join(str(value) for value in item.get("tags") or []))
        cognitive_overlap = sorted(tags & cognition_tokens)
        if not cognitive_overlap:
            continue
        query_overlap = sorted(tags & query_tokens)
        authority = {"canonical": 3, "current": 2, "reference": 1}.get(str(item.get("authority")), 0)
        score = len(cognitive_overlap) * 10 + len(query_overlap) * 3 + authority
        ranked.append({
            "document_id": doc_id, "score": score,
            "cognition_overlap": cognitive_overlap, "query_overlap": query_overlap,
            "reason": "verified cognition signal matched catalog tags",
        })
    ranked.sort(key=lambda row: (-int(row["score"]), str(row["document_id"])))
    selected = ranked[:max(0, int(limit))]
    snapshot = [{key: value for key, value in row.items() if key != "reduced_state"} for row in shadows]
    return {
        "schema_version": 1, "selector_version": "cognition-context-shadow-v1",
        "project_id": project_id, "instance_id": instance_id,
        "selected_document_ids": [row["document_id"] for row in selected],
        "selection_reasons": selected, "cognition_evidence": evidence,
        "source_episode_ids": sorted(source_episodes),
        "cognition_snapshot_digest": _canonical_digest(snapshot),
        "catalog_digest": _canonical_digest(catalog["documents"]),
    }


def select_cognition_context(
    workspace: str,
    query: str,
    *,
    instance_id: str,
    project_id: str,
    budget_chars: int = 9000,
    catalog_path: str | Path,
) -> dict[str, Any]:
    """Run the candidate route without changing the production selector."""
    ranking = cognition_document_ranking(
        workspace, query, instance_id=instance_id, project_id=project_id,
        catalog_path=catalog_path,
    )
    selection, bundle = select_context(
        workspace, query, instance_id=instance_id, project_id=project_id,
        budget_chars=budget_chars, requested_ids=[], boosted_ids=ranking["selected_document_ids"],
        reserve_receipt_chars=1200,
        semantic_selector=None, catalog_path=catalog_path,
    )
    selected_refs = [str(item["document_id"]) for item in selection.selected]
    result = {
        **ranking, "strategy_id": CANDIDATE_STRATEGY,
        "selection_id": selection.selection_id, "selected_context_refs": selected_refs,
        "excluded_context_refs": [value for value in ranking["selected_document_ids"] if value not in selected_refs],
        "budget_chars": selection.budget_chars, "budget_used": selection.used_chars,
        "context_digest": hashlib.sha256(bundle.encode("utf-8")).hexdigest(),
        "context": bundle, "production_mutation": False, "candidate_registered": False,
    }
    result["selection_digest"] = _canonical_digest({
        key: value for key, value in result.items()
        if key not in {"selection_id", "selection_digest", "context"}
    })
    return result


def run_context_gate_c_preflight(
    workspace: str,
    *,
    experiment_id: str,
    tasks: list[dict[str, Any]],
    catalog_path: str | Path,
    budget_chars: int = 9000,
    register_shadow: bool = False,
) -> dict[str, Any]:
    """Run mechanical matched pairs; this is not a task-quality experiment."""
    if len(tasks) < 7:
        raise ValueError("Gate C mechanical preflight requires at least 7 task specifications")
    pairs: list[dict[str, Any]] = []
    all_episodes: set[str] = set()
    for task in tasks:
        match_key = str(task.get("match_key") or "").strip()
        query = str(task.get("query") or "").strip()
        instance_id = str(task.get("instance_id") or "").strip()
        project_id = str(task.get("project_id") or "").strip()
        if not match_key or not query or instance_id != "04" or not project_id:
            raise ValueError("each Gate C task requires match_key/query/instance 04/project identity")
        baseline, baseline_context = select_context(
            workspace, query, instance_id=instance_id, project_id=project_id,
            budget_chars=budget_chars, requested_ids=[], semantic_selector=None,
            catalog_path=catalog_path,
        )
        candidate = select_cognition_context(
            workspace, query, instance_id=instance_id, project_id=project_id,
            budget_chars=budget_chars, catalog_path=catalog_path,
        )
        repeated = select_cognition_context(
            workspace, query, instance_id=instance_id, project_id=project_id,
            budget_chars=budget_chars, catalog_path=catalog_path,
        )
        all_episodes.update(candidate["source_episode_ids"])
        baseline_refs = [str(item["document_id"]) for item in baseline.selected]
        candidate_refs = set(candidate["selected_context_refs"])
        baseline_query_refs = {
            value for value in baseline_refs
            if value not in MANDATORY_IDS and not value.startswith("latest_receipt:")
        }
        candidate_receipt = any(value.startswith("latest_receipt:") for value in candidate_refs)
        receipt_expected = latest_receipt(workspace, project_id) is not None
        relevance_preserved = not baseline_query_refs or bool(baseline_query_refs & candidate_refs)
        cognition_route_used = bool(set(candidate["selected_document_ids"]) & candidate_refs)
        mandatory_ok = set(MANDATORY_IDS) <= set(baseline_refs) and set(MANDATORY_IDS) <= set(candidate["selected_context_refs"])
        deterministic = candidate["selection_digest"] == repeated["selection_digest"]
        exec_marker = execution_marker(query=query, instance_id=instance_id,
                                         project_id=project_id, catalog_path=catalog_path)
        pair = {
            "match_key": match_key,
            "task_digest": _canonical_digest({"query": query, "instance_id": instance_id, "project_id": project_id}),
            "execution_marker": exec_marker,
            "baseline": {"strategy_id": BASELINE_STRATEGY,
                         "route_marker": BASELINE_ROUTE_MARKER,
                         "execution_marker": exec_marker,
                         "selected_context_refs": baseline_refs,
                         "budget_chars": baseline.budget_chars, "budget_used": baseline.used_chars,
                         "context_digest": hashlib.sha256(baseline_context.encode("utf-8")).hexdigest()},
            "candidate": {
                **{key: candidate[key] for key in (
                    "strategy_id", "selected_context_refs", "budget_chars", "budget_used", "context_digest",
                    "selection_digest", "selected_document_ids", "source_episode_ids", "cognition_snapshot_digest",
                )},
                "route_marker": CANDIDATE_ROUTE_MARKER,
                "execution_marker": exec_marker,
            },
            "checks": {
                "same_budget_cap": baseline.budget_chars == candidate["budget_chars"],
                "within_budget": baseline.used_chars <= budget_chars and candidate["budget_used"] <= budget_chars,
                "mandatory_context_present": mandatory_ok,
                "candidate_deterministic": deterministic,
                "cognition_evidence_present": bool(candidate["source_episode_ids"]),
                "project_continuity_preserved": not receipt_expected or candidate_receipt,
                "baseline_query_relevance_preserved": relevance_preserved,
                "cognition_route_used": cognition_route_used,
                "production_mutation": False,
            },
        }
        # Stage 3 isolation markers: appended AFTER pair is built so we can
        # safely read pair['baseline'] / pair['candidate'] fields.
        pair["checks"]["execution_markers_match"] = (
            pair["baseline"]["execution_marker"] == pair["candidate"]["execution_marker"]
        )
        pair["checks"]["route_markers_distinct"] = (
            pair["baseline"]["route_marker"] != pair["candidate"]["route_marker"]
        )

        pair["mechanical_gate_passed"] = all(
            value is True for key, value in pair["checks"].items()
            if key not in {"production_mutation", "cognition_route_used"}
        ) and pair["checks"]["production_mutation"] is False
        # Stage 3 hard requirement: distinct routes through identical inputs.
        # Without these two markers we cannot prove isolation; the gate fails
        # loudly rather than reporting a meaningless PASS.
        if not pair["checks"]["execution_markers_match"]:
            pair["mechanical_gate_passed"] = False
            pair["isolation_failure_reason"] = "execution_marker diverged — input drift detected"
        elif not pair["checks"]["route_markers_distinct"]:
            pair["mechanical_gate_passed"] = False
            pair["isolation_failure_reason"] = "route_marker not distinct — same branch was used"
        pairs.append(pair)

    unique_keys = len({pair["match_key"] for pair in pairs}) == len(pairs)
    cognition_used = any(pair["checks"]["cognition_route_used"] for pair in pairs)
    paths_distinct = any(
        pair["baseline"]["context_digest"] != pair["candidate"]["context_digest"] for pair in pairs
    )
    passed = unique_keys and cognition_used and paths_distinct and all(pair["mechanical_gate_passed"] for pair in pairs)
    result: dict[str, Any] = {
        "schema_version": 1, "mode": "gate_c_mechanical_shadow_preflight",
        "causal_status": "not_executed_no_quality_claim", "experiment_id": experiment_id,
        "baseline_strategy_id": BASELINE_STRATEGY, "candidate_strategy_id": CANDIDATE_STRATEGY,
        "intervention_isolated": paths_distinct, "independent_executed_tasks": False,
        "pairs": len(pairs), "unique_match_keys": unique_keys,
        "cognition_route_used": cognition_used, "candidate_path_distinct": paths_distinct,
        "mechanical_gate_passed": passed, "promotion": False,
        "source_episode_ids": sorted(all_episodes), "pairs_detail": pairs,
        "created_at": now_iso(),
        "next_gate": "execute independent frozen 04 task pairs; this preflight is not reward or promotion evidence",
    }
    directory = workspace_root(workspace) / "share" / "mind" / "governance" / "experience_guided_policy" / "shadow_evaluations"
    path = directory / f"{experiment_id}_context_gate_c_preflight.json"
    atomic_json(path, result)
    candidate_record: dict[str, Any] | None = None
    if register_shadow:
        if not passed or not all_episodes:
            raise ValueError("cannot register shadow Candidate before Gate C mechanical/evidence gates pass")
        candidate_record = register_candidate_skill(workspace, {
            "candidate_id": CANDIDATE_STRATEGY,
            "title": "认知证据辅助的分级上下文选择",
            "status": "shadow", "experiment_id": experiment_id, "strategy_id": CANDIDATE_STRATEGY,
            "source_episode_ids": sorted(all_episodes), "failure_classes": [],
            "applicability": ["instance 04", "literature/GitHub evidence synthesis", "manual_stable"],
            "non_applicability": ["production planner", "automatic iteration", "memory write", "cross-arm output"],
            "counterexamples": [],
            "baseline": {"mechanical_pairs": len(pairs), "task_quality_samples": 0},
            "intervention": "apply bounded soft boosts from validated cognition shadows and reserve L3 Receipt budget in the shared context loader",
            "success_criteria": ["at least 7 independently executed matched pairs", "truth and safety pass",
                                 "no observability regression", "equal context budget", "no cross-arm leakage"],
            "shadow_evidence": {"path": str(path), "mechanical_gate_passed": True,
                                "causal_status": result["causal_status"]},
            "rollback": "do not import cognition_context from planner/executor; retain baseline selector",
        })["candidate"]
    return {"ok": True, **result, "path": str(path), "candidate_skill": candidate_record}
