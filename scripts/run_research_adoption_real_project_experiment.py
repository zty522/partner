#!/usr/bin/env python3
"""Compile multi-source evidence and run five real-project matched shadow pairs."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from partner.governance.context_selector import select_context
from partner.governance.evolution_events import verify_evolution_ledger
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.governance.research_adoption import compile_research_candidate
from partner.governance.storage import atomic_json, latest_receipt, workspace_root
from partner.v2.candidate_events import atomic_execute_candidate


PROJECTS = (
    ("01", "xiaohongshu_operations"),
    ("02", "molecular_generation"),
    ("03", "partner_framework_frontend"),
    ("04", "literature_github_learning"),
    ("05", "agent_self_evolution"),
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


def _oracle_trajectory(root: Path, project_id: str) -> dict[str, Any] | None:
    rows = [row for row in _jsonl(root / "share/mind/governance/experience_guided_policy/trajectories.jsonl")
            if str(row.get("project_id") or "") == project_id]
    meaningful = [row for row in rows if ((row.get("outcome") or {}).get("business_progress")
                                           or (row.get("outcome") or {}).get("learning_progress")
                                           or (row.get("outcome") or {}).get("failure_mechanism"))]
    return (meaningful or rows)[-1] if (meaningful or rows) else None


def _marker(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def run(workspace: Path, *, research_project_id: str, budget_chars: int = 9000) -> dict[str, Any]:
    root = workspace_root(str(workspace.resolve()))
    research_dir = (root / "share/mind/governance/research_learning/projects"
                    / research_project_id)
    evidence_paths = sorted(str(value) for value in (research_dir / "evidence").glob("*.json"))
    issue = record_issue(str(root), {
        "summary": "已审计的源码/论文证据尚未成为可执行的项目上下文 Candidate",
        "category": "context", "severity": "medium", "instance_id": "04",
        "project_id": "agent_self_evolution",
        "evidence": [str(research_dir / "manifest.json"), *evidence_paths],
    })
    if not issue.get("ok"):
        raise RuntimeError(f"issue registration failed: {issue}")
    experiment = start_experiment(str(root), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": "保护最新 Receipt 预算并按当前任务检索同项目 state/action/reward 轨迹，可在相同上下文预算内提高真实项目承接证据完整度",
        "intervention": "多源证据编译的 Event Candidate：canonical docs + protected Receipt + same-project trajectory retrieval + audited excerpts",
        "baseline": {"strategy_id": "baseline_governed_context_v1",
                     "budget_chars": budget_chars, "projects": [value for _, value in PROJECTS]},
        "success_criteria": ["all five real project states have a Receipt and trajectory",
                             "same frozen query, project, Receipt, trajectory and budget per pair",
                             "candidate improves mean evidence recall",
                             "candidate preserves Receipt and same-project trajectory in every pair",
                             "no cross-project trajectory leakage", "all outputs stay within budget",
                             "production policy remains untouched"],
        "project_id": "agent_self_evolution",
        "tests": [str(research_dir / "manifest.json"), *evidence_paths],
    })
    if not experiment.get("ok"):
        raise RuntimeError(f"experiment registration failed: {experiment}")
    experiment_id = experiment["experiment"]["experiment_id"]
    candidate_id = f"candidate_research_adoption_{experiment_id[-12:]}"
    compiled = compile_research_candidate(
        str(root), research_project_id=research_project_id, experiment_id=experiment_id,
        candidate_id=candidate_id,
        local_evidence_paths=[
            str(Path(__file__).resolve().parents[1] / "partner/governance/context_selector.py"),
            str(Path(__file__).resolve().parents[1] / "partner/governance/manual_runtime.py"),
            str(Path(__file__).resolve().parents[1] / "partner/governance/storage.py"),
        ],
    )
    if not compiled.get("ok"):
        raise RuntimeError(f"candidate compilation failed: {compiled}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    working = root / "instances/05/state/tasks" / f"research_adoption_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    pairs: list[dict[str, Any]] = []
    for index, (source_instance, project_id) in enumerate(PROJECTS, 1):
        receipt = latest_receipt(str(root), project_id)
        trajectory = _oracle_trajectory(root, project_id)
        if receipt is None or trajectory is None:
            pairs.append({"project_id": project_id, "ok": False,
                          "error": "real Receipt or trajectory missing"})
            continue
        action_key = str((trajectory.get("action") or {}).get("action_key") or "")
        trajectory_id = str(trajectory.get("trajectory_id") or "")
        query = (f"继续真实项目 {project_id}；承接 Receipt {receipt.receipt_id}；"
                 f"结合历史动作 {action_key} 的真实结果选择下一项有界工作，不重复已完成内容。")
        frozen = {"query": query, "project_id": project_id,
                  "receipt_id": receipt.receipt_id, "trajectory_id": trajectory_id,
                  "action_key": action_key, "budget_chars": budget_chars}
        marker = _marker(frozen)

        baseline_selection, baseline_context = select_context(
            str(root), query, instance_id=source_instance, project_id=project_id,
            budget_chars=budget_chars, requested_ids=[], semantic_selector=None,
        )
        baseline_refs = [str(row.get("document_id") or "")
                         for row in baseline_selection.selected]
        task_dir = working / f"pair_{index}_{project_id}"
        task = SimpleNamespace(workspace=str(root / "instances/05"), working_dir=str(task_dir),
                               user_message=query)
        ctx = SimpleNamespace(workspace=str(root / "instances/05"), working_dir=str(task_dir),
                              task_instance=task)
        candidate = atomic_execute_candidate(ctx, {
            "candidate_id": candidate_id,
            "execution_id": f"exec_{experiment_id}_{index}_{marker[:10]}",
            "instance_id": "05", "mode": "shadow",
            "event_params": {"query": query, "project_id": project_id,
                             "research_project_id": research_project_id,
                             "instance_id": source_instance, "budget_chars": budget_chars},
        })
        candidate_context = str(candidate.get("context") or "")
        baseline_checks = {
            "receipt": receipt.receipt_id in baseline_context,
            "trajectory": trajectory_id in baseline_context,
            "action": bool(action_key and action_key in baseline_context),
            "within_budget": len(baseline_context) <= budget_chars,
        }
        candidate_checks = {
            "receipt": (candidate.get("latest_receipt_id") == receipt.receipt_id
                        and receipt.receipt_id in candidate_context),
            "trajectory": trajectory_id in candidate_context,
            "action": bool(action_key and action_key in candidate_context),
            "within_budget": int(candidate.get("budget_used") or budget_chars + 1) <= budget_chars,
            "same_project_only": all(
                str(row.get("project_id") or "") == project_id
                for row in _jsonl(root / "share/mind/governance/experience_guided_policy/trajectories.jsonl")
                if str(row.get("trajectory_id") or "") in set(candidate.get("trajectory_refs") or [])
            ),
            "audited_research_evidence": bool(candidate.get("research_evidence_refs")),
            "production_untouched": candidate.get("production_effective") is False,
        }
        baseline_recall = sum(bool(baseline_checks[key]) for key in ("receipt", "trajectory", "action")) / 3
        candidate_recall = sum(bool(candidate_checks[key]) for key in ("receipt", "trajectory", "action")) / 3
        pairs.append({
            "ok": bool(candidate.get("ok")), "match_key": f"real-project-{index}",
            "source_instance": source_instance, "project_id": project_id,
            "frozen_input": frozen, "execution_marker": marker,
            "baseline": {"strategy_id": "baseline_governed_context_v1",
                         "execution_marker": marker, "selected_context_refs": baseline_refs,
                         "context_digest": hashlib.sha256(baseline_context.encode()).hexdigest(),
                         "evidence_recall": baseline_recall, "checks": baseline_checks},
            "candidate": {"strategy_id": candidate.get("strategy_id"),
                          "execution_marker": marker,
                          "execution_event_id": candidate.get("execution_event_id"),
                          "selected_context_refs": candidate.get("selected_context_refs"),
                          "trajectory_refs": candidate.get("trajectory_refs"),
                          "research_evidence_refs": candidate.get("research_evidence_refs"),
                          "context_digest": candidate.get("context_digest"),
                          "evidence_recall": candidate_recall, "checks": candidate_checks,
                          "artifact_path": candidate.get("path")},
            "delta": candidate_recall - baseline_recall,
        })

    valid = [row for row in pairs if row.get("ok")]
    baseline_mean = sum(row["baseline"]["evidence_recall"] for row in valid) / max(1, len(valid))
    candidate_mean = sum(row["candidate"]["evidence_recall"] for row in valid) / max(1, len(valid))
    criteria = {
        "five_real_projects": len(valid) == len(PROJECTS),
        "frozen_pair_identity": all(row["baseline"]["execution_marker"]
                                    == row["candidate"]["execution_marker"] for row in valid),
        "mean_evidence_recall_improved": candidate_mean > baseline_mean,
        "receipt_and_trajectory_preserved": all(
            row["candidate"]["checks"]["receipt"]
            and row["candidate"]["checks"]["trajectory"] for row in valid),
        "no_cross_project_leakage": all(
            row["candidate"]["checks"]["same_project_only"] for row in valid),
        "within_equal_budget": all(row["baseline"]["checks"]["within_budget"]
                                   and row["candidate"]["checks"]["within_budget"] for row in valid),
        "production_untouched": all(
            row["candidate"]["checks"]["production_untouched"] for row in valid),
    }
    result = {
        "schema_version": 1, "experiment_id": experiment_id, "candidate_id": candidate_id,
        "research_project_id": research_project_id, "mode": "matched_real_project_shadow",
        "compiled_candidate": compiled, "pairs": pairs,
        "metrics": {"pairs": len(valid), "baseline_mean_evidence_recall": baseline_mean,
                    "candidate_mean_evidence_recall": candidate_mean,
                    "delta": candidate_mean - baseline_mean,
                    "all_candidate_checks_passed": all(criteria.values())},
        "criteria": criteria, "production_effective": False, "promotion": False,
        "limits": ["measures context/evidence preparation, not downstream LLM answer quality",
                   "one frozen snapshot per project is not longitudinal business improvement",
                   "full regression is recorded separately before any later production decision"],
    }
    result_path = working / "real_project_matched_experiment.json"
    atomic_json(result_path, result)
    decision = decide_experiment(str(root), {
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "decision": "inconclusive", "criteria_results": criteria,
        "evidence": [str(result_path), compiled["adoption_path"],
                     *[str(row["candidate"].get("artifact_path") or "") for row in valid]],
        "regression_passed": False,
        "metrics_before": {"mean_evidence_recall": baseline_mean},
        "metrics_after": {"mean_evidence_recall": candidate_mean,
                          "matched_real_projects": len(valid)},
        "rollback_required": False,
        "reason": "real project context preparation improved, but downstream task outcomes and longitudinal business reward are not yet measured",
        "project_id": "agent_self_evolution",
    })
    result["policy_decision"] = decision
    result["evolution_ledger"] = verify_evolution_ledger(str(root))
    atomic_json(result_path, result)
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--research-project", required=True)
    parser.add_argument("--budget-chars", type=int, default=9000)
    args = parser.parse_args()
    result = run(Path(args.workspace), research_project_id=args.research_project,
                 budget_chars=args.budget_chars)
    print(json.dumps({"experiment_id": result["experiment_id"],
                      "candidate_id": result["candidate_id"],
                      "metrics": result["metrics"], "criteria": result["criteria"],
                      "policy_status": result["policy_decision"].get("status"),
                      "ledger": result["evolution_ledger"],
                      "result_path": result["result_path"]}, ensure_ascii=False, indent=2))
    return 0 if all(result["criteria"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
