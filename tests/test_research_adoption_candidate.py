from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

from partner.governance.models import IterationReceipt, NextAction
from partner.governance.context_selector import select_context
from partner.governance.research_adoption import (
    compile_research_candidate,
    select_research_adoption_context,
)
from partner.governance.research_learning import normalize_research_evidence_text
from partner.governance.storage import save_receipt
from partner.v2.candidate_events import atomic_execute_candidate


def _research(workspace: Path, *, include_paper: bool = True) -> str:
    project_id = "research-source"
    directory = (workspace / "share/mind/governance/research_learning/projects"
                 / project_id)
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({
        "status": "observed", "evidence_digest": "d" * 64,
    }), encoding="utf-8")
    code_source = workspace / "external/code/hermes/context.py"
    paper_source = workspace / "external/literature/jitrl.txt"
    code_source.parent.mkdir(parents=True)
    paper_source.parent.mkdir(parents=True)
    code_quote = "Protect the head and retain a handoff summary under a token budget."
    paper_quote = "Retrieve state action reward trajectories at test time."
    code_source.write_text(code_quote, encoding="utf-8")
    paper_source.write_text(paper_quote, encoding="utf-8")
    rows = [{
        "investigation_id": "ev_code", "evidence_found": True,
        "question": "How does context compaction preserve continuity under token budget?",
        "source_path": str(code_source),
        "source_identity": {"kind": "code", "sha256": hashlib.sha256(code_source.read_bytes()).hexdigest()},
        "matched_terms": ["context", "compaction", "preserve", "continuity", "budget"],
        "evidence_quote": code_quote,
    }]
    if include_paper:
        rows.append({
            "investigation_id": "ev_paper", "evidence_found": True,
            "question": "runtime feedback improve agent without gradient updates",
            "source_path": str(paper_source),
            "source_identity": {"kind": "paper", "sha256": hashlib.sha256(paper_source.read_bytes()).hexdigest()},
            "matched_terms": ["runtime", "feedback", "agent", "gradient", "updates", "improve"],
            "evidence_quote": paper_quote,
        })
    (directory / "investigations.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    evidence_dir = directory / "evidence"
    evidence_dir.mkdir()
    for row in rows:
        (evidence_dir / f"{row['investigation_id']}.json").write_text(
            json.dumps(row), encoding="utf-8")
    return project_id


def _runtime(workspace: Path) -> None:
    save_receipt(str(workspace), IterationReceipt(
        project_id="literature_github_learning", iteration=2, goal="continue research",
        inputs=["source.py"], actions_executed=["inspect_source"], artifacts=["report.md"],
        findings=["direct evidence found"],
        next_actions=[NextAction(title="compare implementation", event_type="inspect_source")],
        delivery_confirmed=True,
    ))
    path = workspace / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"trajectory_id": "traj_other", "project_id": "molecular_generation",
         "action": {"action_key": "02:benchmark"}, "outcome": {"business_progress": True},
         "reward": .85},
        {"trajectory_id": "traj_research", "project_id": "literature_github_learning",
         "kind": "manual_project_iteration",
         "state": {"receipt_id": "receipt_previous"},
         "action": {"action_key": "04:inspect_source", "event_types": ["inspect_source"]},
         "outcome": {"learning_progress": True, "novel_evidence": True,
                     "evidence": ["direct source"]},
         "reward": .55, "reward_components": {"learning_progress": .3}},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _locals() -> list[str]:
    root = Path(__file__).resolve().parents[1]
    return [str(root / "partner/governance/context_selector.py"),
            str(root / "partner/governance/manual_runtime.py"),
            str(root / "partner/governance/storage.py")]


def test_compile_requires_complementary_external_and_local_evidence(tmp_path: Path) -> None:
    project = _research(tmp_path, include_paper=False)
    blocked = compile_research_candidate(
        str(tmp_path), research_project_id=project, experiment_id="experiment_x",
        local_evidence_paths=_locals(), candidate_id="candidate_blocked")
    assert blocked["ok"] is False
    assert blocked["gates"]["two_independent_sources"] is False
    assert blocked["gates"]["trajectory_memory_claim"] is False


def test_research_evidence_normalization_handles_pdf_layout_typography() -> None:
    assert normalize_research_evidence_text("agent’s\n  state — retained") == \
        "agent's state - retained"


def test_compiled_candidate_is_event_executable_and_production_isolated(tmp_path: Path) -> None:
    project = _research(tmp_path)
    _runtime(tmp_path)
    compiled = compile_research_candidate(
        str(tmp_path), research_project_id=project, experiment_id="experiment_adoption",
        local_evidence_paths=_locals(), candidate_id="candidate_adoption")
    assert compiled["ok"] is True
    record = compiled["registration"]["candidate"]
    assert record["execution_ready"] is True
    assert record["execution_contract"]["kind"] == "event"
    assert record["evaluation_ready"] is False
    assert record["production_effective"] is False
    assert all(compiled["adoption"]["gates"].values())

    task_dir = tmp_path / "instances/05/state/tasks/shadow"
    task = SimpleNamespace(working_dir=str(task_dir))
    ctx = SimpleNamespace(workspace=str(tmp_path / "instances/05"),
                          working_dir=str(task_dir), task_instance=task)
    result = atomic_execute_candidate(ctx, {
        "candidate_id": "candidate_adoption", "execution_id": "exec_adoption",
        "instance_id": "05", "mode": "shadow",
        "event_params": {"query": "继续 inspect source 并承接项目",
                         "project_id": "literature_github_learning",
                         "research_project_id": project, "instance_id": "04",
                         "budget_chars": 9000},
    })
    assert result["ok"] is True
    assert result["candidate_event_type"] == "research_adoption_context_shadow"
    assert result["latest_receipt_id"]
    assert "traj_research" in result["trajectory_refs"]
    assert "traj_other" not in result["context"]
    assert result["research_evidence_refs"]
    assert len(result["verified_source_evidence"]) == len(result["research_evidence_refs"])
    assert all(row["source_sha256"] for row in result["verified_source_evidence"])
    assert all(ref in result["context"] for ref in result["research_evidence_refs"])
    assert all(ref in result["context"] for ref in result["trajectory_refs"])
    assert result["budget_used"] <= result["budget_chars"]
    assert result["production_effective"] is False
    assert Path(result["path"]).is_file()


def test_selection_is_deterministic_budgeted_and_keeps_next_action(tmp_path: Path) -> None:
    project = _research(tmp_path)
    _runtime(tmp_path)
    first = select_research_adoption_context(
        str(tmp_path), query="继续 inspect source", project_id="literature_github_learning",
        research_project_id=project, instance_id="04", budget_chars=5000)
    second = select_research_adoption_context(
        str(tmp_path), query="继续 inspect source", project_id="literature_github_learning",
        research_project_id=project, instance_id="04", budget_chars=5000)
    assert first["selection_digest"] == second["selection_digest"]
    assert first["budget_used"] <= 5000
    assert first["next_actions"][0]["event_type"] == "inspect_source"
    assert first["latest_receipt_id"] in first["context"]
    assert "traj_research" in first["context"]


def test_chinese_runtime_feedback_query_prioritizes_jitrl_and_diverse_sources(tmp_path: Path) -> None:
    project = _research(tmp_path)
    _runtime(tmp_path)
    result = select_research_adoption_context(
        str(tmp_path), query="说明 JitRL 如何利用运行时反馈且不更新模型参数",
        project_id="literature_github_learning", research_project_id=project,
        instance_id="04", budget_chars=9000)
    assert result["research_evidence_refs"][0] == "ev_paper"
    assert len(result["research_evidence_refs"]) == 2


def test_candidate_refuses_project_without_receipt(tmp_path: Path) -> None:
    project = _research(tmp_path)
    result = select_research_adoption_context(
        str(tmp_path), query="continue", project_id="missing",
        research_project_id=project)
    assert result["ok"] is False
    assert result["status"] == "candidate_missing_project_receipt"


def test_production_context_budget_counts_provenance_wrappers(tmp_path: Path) -> None:
    _runtime(tmp_path)
    selection, context = select_context(
        str(tmp_path), "继续 inspect source", instance_id="04",
        project_id="literature_github_learning", budget_chars=5000,
        semantic_selector=None)
    assert len(context) <= 5000
    assert selection.used_chars <= 5000
