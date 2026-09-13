"""Machine-verifiable maturity gates for longitudinal Partner improvement.

These assessors deliberately separate business progress, external learning and
Partner self-evolution.  They never promote a Candidate; they only describe
what evidence is present and which prerequisite is still missing.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev
from typing import Any

from .storage import workspace_root


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    values: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def _day(path: Path, value: dict[str, Any]) -> str:
    raw = str(value.get("created_at") or "")[:10]
    if len(raw) == 10:
        return raw
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().date().isoformat()
    except OSError:
        return ""


def _work_item_days(root: Path) -> dict[str, str]:
    """Recover original evidence dates from append-only execution truth.

    Early metric rows predated the ``created_at`` contract. Their temporary
    artifacts may later be removed by Task TTL, but the work-item timestamp is
    still authoritative in the trajectory ledger. Migration time and a
    durable ledger's mtime must never replace the observation day.
    """
    ledger = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    result: dict[str, str] = {}
    for row in _jsonl(ledger):
        work_item_id = str(row.get("work_item_id") or "")
        created_at = str(row.get("created_at") or "")[:10]
        if work_item_id and len(created_at) == 10:
            result[work_item_id] = created_at
    return result


def _evidence_day(path: Path, value: dict[str, Any], work_item_days: dict[str, str]) -> str:
    direct = str(value.get("created_at") or "")[:10]
    if len(direct) == 10:
        return direct
    candidates = [str(path), str(value.get("artifact_path") or ""),
                  str(value.get("source_file") or "")]
    for candidate in candidates:
        for part in Path(candidate).parts:
            if part in work_item_days:
                return work_item_days[part]
    return _day(path, value)


def _mean_ci95(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0, "mean": 0.0, "lower": 0.0, "upper": 0.0}
    centre = mean(values)
    margin = 0.0 if len(values) < 2 else 1.96 * stdev(values) / math.sqrt(len(values))
    return {"n": len(values), "mean": centre,
            "lower": centre - margin, "upper": centre + margin}


def assess_molecular_longitudinal(workspace: str | Path, *,
                                  ignore_calendar_days: bool = False) -> dict[str, Any]:
    root = workspace_root(str(workspace))
    work_item_days = _work_item_days(root)
    rows: list[dict[str, Any]] = []
    task_values = [(path, _json(path)) for path in (root / "instances/02/state/tasks").glob(
        "*/molecular_method_candidate_metrics.json")]
    ledger = root / "share/projects/molecular_generation/metrics/method_candidates.jsonl"
    ledger_values = [(Path(str(value.get("artifact_path") or ledger)), value)
                     for value in _jsonl(ledger)]
    for path, value in [*task_values, *ledger_values]:
        delta = dict(value.get("candidate_minus_baseline") or {})
        seed = int(value.get("replicate_seed") or 0)
        method = str(value.get("method_id") or "")
        if not seed or not method or "fingerprint_diversity" not in delta:
            continue
        rows.append({"path": str(path), "day": _evidence_day(path, value, work_item_days), "seed": seed,
                     "method": method, "supported": bool(value.get("candidate_improved")),
                     "fingerprint_delta": float(delta.get("fingerprint_diversity") or 0),
                     "scaffold_delta": float(delta.get("scaffold_count") or 0),
                     "qed_delta": float(delta.get("mean_qed") or 0),
                     "sa_delta": float(delta.get("mean_sa") or 0),
                     "docking_delta": delta.get("docking_score"),
                     "synthesis_success_delta": delta.get("synthesis_success")})
    # One observation per method/seed.  Re-running the same seed must not
    # manufacture longitudinal sample size.
    unique = {(row["method"], row["seed"]): row for row in rows}
    rows = sorted(unique.values(), key=lambda row: (row["day"], row["seed"], row["method"]))
    by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_method[row["method"]].append(row)
    maxmin = by_method.get("maxmin_fingerprint", [])
    fp_ci = _mean_ci95([row["fingerprint_delta"] for row in maxmin])
    qed_ci = _mean_ci95([row["qed_delta"] for row in maxmin])
    days = sorted({row["day"] for row in rows if row["day"]})
    docking_by_source: dict[str, dict[str, Any]] = {}
    docking_task_values = [(path, _json(path)) for path in
                           (root / "instances/02/state/tasks").glob("*/molecular_docking_holdout.json")]
    docking_ledger = root / "share/projects/molecular_generation/metrics/docking_holdouts.jsonl"
    docking_ledger_values = [(Path(str(value.get("artifact_path") or docking_ledger)), value)
                             for value in _jsonl(docking_ledger)]
    for path, value in [*docking_task_values, *docking_ledger_values]:
        source = str(value.get("source_file") or "")
        if source and value.get("candidate_minus_baseline_docking_score") is not None:
            docking_by_source[source] = {"path": str(path),
                                         "day": _evidence_day(path, value, work_item_days),
                                         "source_file": source,
                                         "delta": float(value["candidate_minus_baseline_docking_score"])}
    docking_runs = list(docking_by_source.values())
    # Legacy metric rows may contain a computed ``synthesis_success`` score.
    # That field is a proxy and must never satisfy a wet-lab evidence gate.
    synthesis_runs = [row for row in rows if row["synthesis_success_delta"] is not None]
    from .experimental_evidence import audit_experimental_synthesis_evidence
    experimental = audit_experimental_synthesis_evidence(str(root))
    checks = {
        "twelve_distinct_method_seed_trials": len(rows) >= 12,
        "five_maxmin_holdout_seeds": len({row["seed"] for row in maxmin}) >= 5,
        "three_calendar_days": bool(ignore_calendar_days or len(days) >= 3),
        "fingerprint_gain_ci95_above_zero": fp_ci["n"] >= 5 and fp_ci["lower"] > 0,
        "qed_loss_ci95_within_0_05": qed_ci["n"] >= 5 and qed_ci["lower"] >= -0.05,
        "three_fixed_pocket_docking_holdouts": len(docking_runs) >= 3,
        "published_experimental_synthesis_evidence": bool(
            experimental["published_experimental_synthesis_evidence"]),
    }
    return {"ok": all(checks.values()), "checks": checks, "trials": len(rows),
            "days": days, "methods": {key: len(value) for key, value in by_method.items()},
            "maxmin_fingerprint_delta_ci95": fp_ci, "maxmin_qed_delta_ci95": qed_ci,
            "docking_holdouts": len(docking_runs),
            "legacy_synthesis_proxy_trials": len(synthesis_runs),
            "experimental_synthesis_trials": experimental["verified_records"],
            "partner_candidate_wet_lab_trials": experimental["partner_candidate_wet_lab_records"],
            "experimental_synthesis_evidence": experimental,
            "downstream_checks": {
                "partner_candidate_wet_lab_validation": bool(
                    experimental["partner_candidate_wet_lab_validation"]),
            },
            "calendar_day_gate_ignored": bool(ignore_calendar_days),
            "docking_evidence": docking_runs[-10:], "evidence": rows[-20:]}


def assess_external_learning_longitudinal(workspace: str | Path, *,
                                          ignore_calendar_days: bool = False) -> dict[str, Any]:
    root = workspace_root(str(workspace))
    runs: list[dict[str, Any]] = []
    task_values = [(path, _json(path)) for path in
                   (root / "instances/04/state/tasks").glob("*/04_external_knowledge_scout.json")]
    discovery = root / "external/insights/discovery_index.jsonl"
    discovery_values = [(Path(str(value.get("machine_path") or discovery)), value)
                        for value in _jsonl(discovery)]
    attempts: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path, value in [*task_values, *discovery_values]:
        repository = dict(value.get("repository") or {})
        paper = dict(value.get("paper") or {})
        depth = dict(value.get("evidence_depth") or {})
        quality = dict(value.get("source_quality") or {})
        pair = (str(repository.get("url") or ""), str(paper.get("paper_id") or ""))
        row = {"path": str(path), "day": _day(path, value), "pair": pair,
               "quality": bool(int(quality.get("repository_pre_score") or 0) > 0
                               and quality.get("repository_files_gate")
                               and quality.get("paper_fulltext_gate")),
               "repo_files": int(depth.get("repository_files_read") or 0),
               "paper_pages": int(depth.get("paper_pdf_pages_read") or 0),
               "candidate_id": str((value.get("adoption_candidate") or {}).get("candidate_id") or "")}
        attempt_key = (str(value.get("created_at") or path), *pair)
        attempts[attempt_key] = row
    runs = list(attempts.values())
    # Re-reading the same source pair with a temporarily weaker acquisition
    # must not erase an earlier verified full-text/code observation.  Keep the
    # strongest auditable record per stable pair; raw attempts remain intact.
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in runs:
        if not all(row["pair"]):
            continue
        prior = unique.get(row["pair"])
        score = (int(row["quality"]), row["repo_files"], row["paper_pages"])
        prior_score = ((int(prior["quality"]), prior["repo_files"], prior["paper_pages"])
                       if prior else (-1, -1, -1))
        if score > prior_score:
            unique[row["pair"]] = row
    high_quality = [row for row in unique.values() if row["quality"]]
    candidate_dir = root / "share/mind/governance/research_adoption_candidates"
    candidates = [_json(path) for path in candidate_dir.glob("*.json")]
    proposed = [row for row in candidates if str(row.get("decision") or "").startswith("proposed")]
    validated = [row for row in candidates
                 if row.get("production_effective") is True
                 or str(row.get("decision") or "") in {
                     "validated", "validated_shadow", "promoted", "applied"}]
    days = sorted({row["day"] for row in high_quality if row["day"]})
    checks = {
        "ten_unique_high_quality_source_pairs": len(high_quality) >= 10,
        "three_calendar_days": bool(ignore_calendar_days or len(days) >= 3),
        "three_legal_adoption_candidates": len(proposed) >= 3,
        "one_isolated_candidate_validated": bool(validated),
    }
    return {"ok": all(checks.values()), "checks": checks,
            "runs": len(runs), "unique_pairs": len(unique),
            "high_quality_pairs": len(high_quality), "days": days,
            "proposed_candidates": len(proposed), "validated_candidates": len(validated),
            "evidence": high_quality[-15:]}


def assess_sprint24(workspace: str | Path, *, ignore_calendar_days: bool = False) -> dict[str, Any]:
    molecular = assess_molecular_longitudinal(workspace, ignore_calendar_days=ignore_calendar_days)
    external = assess_external_learning_longitudinal(workspace, ignore_calendar_days=ignore_calendar_days)
    return {"schema_version": 1, "gate": "sprint24_longitudinal_improvement",
            "ok": bool(molecular["ok"] and external["ok"]),
            "molecular": molecular, "external_learning": external,
            "production_effective": False,
            "acceptance_scope": ("operator_authorized_without_calendar_day_gate"
                                 if ignore_calendar_days else "strict_all_gates"),
            "interpretation": ("longitudinal improvement gate passed"
                               if molecular["ok"] and external["ok"]
                               else "evidence collection must continue; do not claim sustained improvement")}
