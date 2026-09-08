from __future__ import annotations

import json
from pathlib import Path
import asyncio
from types import SimpleNamespace

import pytest

from partner.governance import research_learning as rl
from partner.governance.manual_runtime import _record_manual_trajectory
from partner.mind.event_types import EventType, MindEvent
from partner.mind import executor as mind_executor
from partner.mind.harness import (
    EventRegistry,
    HarnessContext,
    HarnessEventSpec,
    HarnessStep,
    PlanExecutor,
    StateStore,
)


@pytest.fixture()
def source_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    external = tmp_path / "external"
    external.mkdir()
    first = external / "generic.py"
    second = external / "continuation.py"
    first.write_text("def execute():\n    return 'tool result'\n", encoding="utf-8")
    second.write_text(
        "class ContinuationLedger:\n"
        "    # Persist checkpoint receipt before context compaction and resume.\n"
        "    def resume_from_checkpoint(self): return 'continuation evidence'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rl, "_ALLOWED_SOURCE_ROOTS", (external,))
    return first, second


def test_research_cycle_selects_informative_real_source(tmp_path: Path, source_roots) -> None:
    first, second = source_roots
    workspace = tmp_path / "workspace"
    observed = rl.observe_research_project(
        str(workspace), project_id="agent-continuation", goal="improve reliable continuation",
        questions=["How does checkpoint receipt preserve continuation resume evidence?"],
        source_paths=[str(first), str(second)],
    )
    assert observed["ok"] is True
    assert observed["findings"]
    selected = rl.select_research_query(str(workspace), project_id="agent-continuation")
    assert selected["ok"] is True
    assert selected["findings"]
    assert selected["selected"]["params"]["source_path"] == str(second)
    investigated = rl.investigate_selected_query(str(workspace), project_id="agent-continuation")
    assert investigated["ok"] is True
    assert "证据摘录" in investigated["findings"][1]
    assert investigated["evidence_found"] is True
    assert investigated["source_identity"]["sha256"]
    assert "checkpoint" in investigated["evidence_quote"].lower()
    assert investigated["evidence_quality"] >= .55
    assert investigated["belief_update"]["posterior"]["mechanism_supported"] > .5


def test_research_cycle_updates_belief_and_selects_unseen_option(
        tmp_path: Path, source_roots) -> None:
    first, second = source_roots
    workspace = tmp_path / "workspace"
    rl.observe_research_project(
        str(workspace), project_id="continuing", goal="continue learning",
        questions=["checkpoint receipt continuation resume evidence"],
        source_paths=[str(first), str(second)],
    )
    first_selection = rl.select_research_query(str(workspace), project_id="continuing")
    rl.investigate_selected_query(str(workspace), project_id="continuing")
    second_selection = rl.select_research_query(str(workspace), project_id="continuing")
    assert second_selection["selected"]["option_id"] != first_selection["selected"]["option_id"]
    assert second_selection["selected"]["novelty"] == 1.0
    assert second_selection["hypothesis_prior"]["mechanism_supported"] > .5


def test_evidence_extractor_prefers_prose_over_pseudocode() -> None:
    text = (
        "for a in C do z(s,a) ← 0 // gradient updates agent feedback runtime. "
        "Q(s,a) == value => update.\n\n"
        "The training-free agent uses runtime feedback to improve its policy without gradient updates. "
        "It stores successful trajectories in non-parametric memory and retrieves them at test time."
    )
    quote, hits, quality = rl._best_evidence(
        text, ["runtime", "feedback", "improve", "agent", "gradient", "updates"])
    assert "training-free" in quote
    assert len(hits) >= 5
    assert quality >= .55


def test_evidence_extractor_penalizes_table_caption() -> None:
    text = (
        "Table 7. Agent gradient updates improve results.\n\n"
        "The agent stores runtime feedback in a non-parametric memory. "
        "It then improves its test-time policy without gradient updates."
    )
    quote, hits, quality = rl._best_evidence(
        text, ["agent", "runtime", "feedback", "improves", "gradient", "updates"])
    assert "non-parametric memory" in quote
    assert "Table 7" not in quote
    assert quality >= .55


def test_evidence_extractor_accepts_jitrl_semantic_terms_and_figure_reference() -> None:
    text = (
        "Our approach, Just-In-Time Reinforcement Learning, enables general-purpose learning "
        "while avoiding gradient updates. Instead of gradient updates, it maintains a dynamic "
        "memory bank that stores trajectories as state, action, reward triplets, as illustrated "
        "in Figure 1. Given the agent's current state, it retrieves relevant trajectories and "
        "learns from them just in time."
    )
    quote, hits, quality = rl._best_evidence(
        text, ["runtime", "feedback", "improve", "agent", "gradient", "updates"])
    assert "Figure 1" in quote
    assert set(hits) == {"runtime", "feedback", "improve", "agent", "gradient", "updates"}
    assert quality >= .9


def test_matched_candidate_is_shadow_only(tmp_path: Path, source_roots) -> None:
    first, second = source_roots
    workspace = tmp_path / "workspace"
    rl.observe_research_project(
        str(workspace), project_id="matched", goal="test selector",
        questions=["checkpoint receipt continuation resume evidence"],
        source_paths=[str(first), str(second)],
    )
    result = rl.run_research_selector_matched_experiment(str(workspace), project_id="matched")
    assert result["ok"] is True
    assert result["decision"] == "accept_for_shadow"
    assert result["metrics"]["candidate_mean_evidence_coverage"] > result["metrics"]["baseline_mean_evidence_coverage"]
    assert result["production_effective"] is False
    assert result["promotion"] is False


def test_rejects_missing_or_outside_sources(tmp_path: Path, source_roots) -> None:
    first, _ = source_roots
    result = rl.observe_research_project(
        str(tmp_path / "workspace"), project_id="unsafe", goal="x", questions=["q"],
        source_paths=[str(first), "/etc/passwd"],
    )
    assert result["ok"] is False
    assert result["status"] == "source_validation_failed"


def test_manifest_and_evidence_are_append_only_auditable(tmp_path: Path, source_roots) -> None:
    first, second = source_roots
    workspace = tmp_path / "workspace"
    rl.observe_research_project(
        str(workspace), project_id="audit", goal="x",
        questions=["checkpoint continuation"], source_paths=[str(first), str(second)],
    )
    rl.select_research_query(str(workspace), project_id="audit")
    evidence = rl.investigate_selected_query(str(workspace), project_id="audit")
    stored = json.loads(Path(evidence["path"]).read_text(encoding="utf-8"))
    assert stored["evidence_quote"] == evidence["evidence_quote"]
    assert stored["production_mutation"] is False
    events = Path(evidence["path"]).parents[1] / "events.jsonl"
    assert len(events.read_text(encoding="utf-8").splitlines()) == 3


def test_research_events_execute_inline_without_thread_wakeup(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restricted runtimes may deny asyncio's cross-thread wakeup socket."""
    registry = EventRegistry()
    calls: list[str] = []

    def local_handler(_ctx, params):
        calls.append(str(params["name"]))
        return {"ok": True, "status": "observed", "content": params["name"]}

    event_names = [
        "execute_candidate",
        "select_context",
        "research_active_learning_observe",
        "research_active_learning_select",
        "research_active_learning_investigate",
        "research_active_learning_matched",
    ]
    for name in event_names:
        registry.register(HarnessEventSpec(name, "atomic", name, local_handler))

    async def forbidden_to_thread(*_args, **_kwargs):
        raise AssertionError("bounded research governance events must execute inline")

    monkeypatch.setattr(asyncio, "to_thread", forbidden_to_thread)
    event = MindEvent(type=EventType.BATCH_PLAN, payload={"user_request": "research"})
    ctx = HarnessContext(
        workspace=str(tmp_path), event=event, title="research", project_dir=str(tmp_path),
        state_md="", artifact_path=str(tmp_path / "artifact.md"),
    )
    steps = [HarnessStep(name, name, {"name": name, "use_llm": False},
                         [event_names[index - 1]] if index else [])
             for index, name in enumerate(event_names)]
    results, llm_calls, _, failures = asyncio.run(
        PlanExecutor(registry, StateStore(str(tmp_path))).execute(ctx, steps))
    assert calls == event_names
    assert list(results) == event_names
    assert llm_calls == 0
    assert failures == {}


def test_research_evidence_gets_positive_learning_not_business_reward(tmp_path: Path) -> None:
    workspace = tmp_path / "instances" / "04"
    evidence = tmp_path / "share" / "mind" / "governance" / "research_learning" / "p" / "evidence.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"evidence_found": true}', encoding="utf-8")
    result = _record_manual_trajectory(
        str(workspace), iid="04", project_id="literature_github_learning", task_id="research-task",
        receipt={"receipt_id": "r1", "delivery_confirmed": True}, inputs=[], artifacts=[],
        evidence_refs=[str(evidence)],
        actions=["research_active_learning_observe", "research_active_learning_investigate"],
        findings=["真实证据已记录"],
    )
    trajectory = result["trajectory"]
    assert trajectory["outcome"]["business_progress"] is False
    assert trajectory["outcome"]["learning_progress"] is True
    assert trajectory["outcome"]["novel_evidence"] is True
    assert trajectory["reward"] > 0
    assert trajectory["policy_eligible"] is False
    assert trajectory["learning_observation_eligible"] is True


def test_agent_learning_evidence_gets_positive_learning_reward_for_instance05(tmp_path: Path) -> None:
    workspace = tmp_path / "instances" / "05"
    evidence = tmp_path / "share/mind/governance/active_learning/experiments/e1/experiment.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"all_gates_passed": true}', encoding="utf-8")
    result = _record_manual_trajectory(
        str(workspace), iid="05", project_id="agent_self_evolution", task_id="agent-learning-task",
        receipt={"receipt_id": "r5", "delivery_confirmed": True}, inputs=[], artifacts=[],
        evidence_refs=[str(evidence)],
        actions=["atomic_inspect_file", "agent_active_learning_manual_failure_matched"],
        findings=["匹配实验四项硬门通过"],
    )
    trajectory = result["trajectory"]
    assert trajectory["outcome"]["learning_progress"] is True
    assert trajectory["reward"] == 0.55
    assert trajectory["policy_eligible"] is False
    assert trajectory["learning_observation_eligible"] is True


def test_stop_terminal_preserves_research_evidence_channel(
        monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []

    class Queue:
        async def put(self, event):
            captured.append(event)

    async def pool():
        return Queue()

    monkeypatch.setattr(mind_executor, "ensure_pool", pool)
    parent = MindEvent(type=EventType.BATCH_PLAN, payload={})
    asyncio.run(mind_executor._enqueue_stop_project_event(
        parent, "research", "done",
        {"completion_evidence_files": ["/tmp/evidence.json"]},
    ))
    assert captured[0].payload["completion_evidence_files"] == ["/tmp/evidence.json"]


def test_terminal_collects_agent_and_targetdiff_learning_evidence(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace" / "instances" / "05"
    active = tmp_path / "workspace/share/mind/governance/active_learning"
    matched = active / "experiments/e1/experiment.json"
    targetdiff = active / "targetdiff/run.json"
    matched.parent.mkdir(parents=True)
    targetdiff.parent.mkdir(parents=True)
    matched.write_text('{"passed": true}', encoding="utf-8")
    targetdiff.write_text('{"decision": "accept"}', encoding="utf-8")
    monkeypatch.setattr(mind_executor, "_workspace", str(workspace))
    result = SimpleNamespace(step_results={
        "matched": {
            "event_type": "agent_active_learning_manual_failure_matched",
            "result": {"experiment_path": str(matched)},
        },
        "targetdiff": {
            "event_type": "targetdiff_active_learning",
            "path": str(targetdiff),
        },
    })
    assert mind_executor._research_learning_evidence_files(result) == [
        str(matched), str(targetdiff),
    ]
