from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from partner.governance.manual_runtime import _candidate_truth_audit
from partner.mind.harness import (
    EventRegistry,
    HarnessEventSpec,
    HarnessStep,
    MicroPlan,
    run_harness_plan,
    _preserve_candidate_verified_sources,
)
from partner.harness_core.task_instance import TaskInstance
from partner.mind.event_types import EventType, MindEvent
from partner.mind.output_reference import (
    FAILURE_OWNER_INPUT_STATE,
    MECHANISM_EXPECTED_MISSING_INPUT,
    classify_failure_owner,
)
from partner.planner.batch_planner import _manual_preflight_plan
from partner.v2.active_learning_events import (
    atomic_agent_active_learning_observe_episode,
    atomic_agent_active_learning_propose_episode_repair,
)


def _registry(*names: str) -> EventRegistry:
    registry = EventRegistry()
    for name in names:
        registry.register(HarnessEventSpec(
            name, "atomic", name, lambda ctx, params: {"ok": True},
            produces_artifact=name == "agent_active_learning_propose_episode_repair",
            execution_method="local",
        ))
    return registry


def test_verified_sources_replace_broken_model_ledger_with_grounded_blocks(tmp_path: Path):
    source = tmp_path / "source.md"
    quote = "Partner 文档记录上下文、记忆和自进化边界，并要求所有结论保留真实证据。"
    source.write_text(quote + "\n", encoding="utf-8")
    data = {"verified_sources": json.dumps({
        "SOURCE": {"source_path": str(source), "conclusion": quote, "evidence_quote": quote},
    }, ensure_ascii=False)}
    broken = """# Report

## Claim Ledger
claim_id: BROKEN
claim_text: unsupported
claim_axes: context_management
support_type: direct
"""
    result = _preserve_candidate_verified_sources(
        "[strategy_id=candidate_preflight_contract_v2] [policy_arm=candidate] "
        "[experiment_id=e] [match_key=m]",
        data, broken,
    )
    assert "claim_id: BROKEN" not in result
    assert "claim_id: AUTO-SOURCE-001" in result
    assert f"source_path: {source}" in result
    assert f"source_identity: {source.name}" in result
    assert f"evidence_quote: {quote}" in result
    artifact = tmp_path / "report.md"
    artifact.write_text(result, encoding="utf-8")
    audit = _candidate_truth_audit(
        [str(source)], [str(artifact)], ["create_file"], require_claim_ledger=True,
    )
    assert audit["passed"] is True, audit


def test_expected_missing_probe_is_one_read_without_writer(tmp_path: Path):
    root = tmp_path / "workspace"
    instance = root / "instances" / "04"
    working = instance / "state" / "tasks" / "task"
    working.mkdir(parents=True)
    missing = working / "__missing__.md"
    registry = _registry("atomic_inspect_file", "atomic_write_artifact")
    original = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": str(missing)}, []),
        HarnessStep("write", "atomic_write_artifact", {"path": "report.md", "content": "bad"}, ["read"]),
    ], expected_artifacts=[{"type": "file", "pattern": "*.md"}])
    accepted = _manual_preflight_plan(
        original, registry=registry, workspace=str(instance), working_dir=str(working),
        user_message=f"请检查 {missing}，这个文件预期不存在；不得生成替代文件。",
    )
    assert len(accepted.plan) == 1
    assert accepted.plan[0].event_type == "atomic_inspect_file"
    assert accepted.plan[0].parameters["_expected_missing_probe"] is True
    assert accepted.expected_artifacts == []
    owner, mechanism = classify_failure_owner(
        error=f"file_not_found (预期不存在): {missing}",
        event_type="atomic_inspect_file", has_user_provided_inputs=True,
        typed_reference_resolved=False,
    )
    # Generic classification describes an unexpected absent user input.  The
    # explicit expected-observation execution path is tested end-to-end below
    # and must instead complete successfully as input_state.
    assert mechanism


def test_expected_missing_probe_is_successful_negative_observation_end_to_end(tmp_path: Path):
    import asyncio

    instance = tmp_path / "workspace" / "instances" / "04"
    instance.mkdir(parents=True)
    missing = instance / "state" / "tasks" / "probe" / "__missing__.md"
    task = TaskInstance.create(
        str(instance), "这个文件预期不存在，不得生成替代文件", task_id="probe",
    )
    progress: list[dict] = []

    async def capture(update: dict):
        progress.append(dict(update))

    result = asyncio.run(run_harness_plan(
        workspace=str(instance),
        event=MindEvent(type=EventType.BATCH_PLAN, payload={"task_id": "probe"}),
        title="预期缺失验收",
        project_dir=task.working_dir,
        state_md="",
        artifact_path="",
        adapter=None,
        build_action_prompt=lambda *args: "",
        parse_structured_response=lambda text: {},
        micro_plan=MicroPlan(plan=[HarnessStep(
            "check_expected_missing_input", "atomic_inspect_file",
            {"path": str(missing), "_expected_missing_probe": True}, [],
        )], expected_artifacts=[]),
        planner_llm_calls=0,
        progress_callback=capture,
    ))

    step = result.step_results["check_expected_missing_input"]
    assert result.ok is True
    assert step["ok"] is True
    assert step["exists"] is False
    assert step["observation_met"] is True
    assert step["failure_owner"] == FAILURE_OWNER_INPUT_STATE
    assert step["mechanism"] == MECHANISM_EXPECTED_MISSING_INPUT
    assert not Path(task.working_dir, "_error_report.md").exists()
    completed = [row for row in progress if row.get("phase") == "step_complete"]
    assert completed and completed[-1]["ok"] is True
    saved = TaskInstance.load(str(instance), "probe")
    assert saved.completion_status == "done"


def test_user_authorized_readonly_active_learning_becomes_four_event_plan(tmp_path: Path):
    root = tmp_path / "workspace"
    instance = root / "instances" / "04"
    working = instance / "state" / "tasks" / "task"
    working.mkdir(parents=True)
    episode_id = "episode_deadbeef"
    state_path = root / "share" / "mind" / "governance" / "episodes" / episode_id / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "episode_id": episode_id, "task_id": "task-old", "instance_id": "04",
        "failure_classes": ["planning.output_reference_contract/typed_reference_unresolved"],
    }), encoding="utf-8")
    names = (
        "agent_active_learning_observe_episode", "agent_active_learning_select",
        "agent_active_learning_diagnostic_shadow", "agent_active_learning_propose_episode_repair",
    )
    accepted = _manual_preflight_plan(
        MicroPlan(plan=[HarnessStep("wrong", "atomic_list_project_files", {}, [])]),
        registry=_registry(*names), workspace=str(instance), working_dir=str(working),
        user_message=(f"请以 Event-first 对 {episode_id} 做主动学习只读分析；"
                      "不修改生产代码或 control_policy，不执行 promotion，production_effective=false。"),
    )
    assert [step.event_type for step in accepted.plan] == list(names)
    assert accepted.plan[-1].depends_on == ["diagnose_episode"]
    assert accepted.expected_artifacts[0]["pattern"] == "active_learning_review_*.json"


def test_readonly_episode_review_writes_task_local_proposal_without_production_mutation(tmp_path: Path):
    root = tmp_path / "workspace"
    instance = root / "instances" / "04"
    working = instance / "state" / "tasks" / "review"
    working.mkdir(parents=True)
    episode_id = "episode_cafebabe"
    bundle = root / "share" / "mind" / "governance" / "episodes" / episode_id
    bundle.mkdir(parents=True)
    state = {
        "episode_id": episode_id, "task_id": "task-failed", "instance_id": "04",
        "status": "failed",
        "failure_classes": ["planning.output_reference_contract/typed_reference_unresolved"],
        "failure_details": [{
            "class": "planning.output_reference_contract/typed_reference_unresolved",
            "error": "typed reference unresolved",
        }],
    }
    (bundle / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (bundle / "trace.jsonl").write_text(json.dumps({
        "type": "plan_executor_step_completed",
        "payload": {"event_type": "atomic_convert_md_to_pdf", "ok": False,
                    "error": "typed reference unresolved"},
    }) + "\n", encoding="utf-8")
    ctx = SimpleNamespace(workspace=str(instance), working_dir=str(working), task_instance=None)
    observed = atomic_agent_active_learning_observe_episode(ctx, {"episode_id": episode_id})
    assert observed["ok"] is True
    proposed = atomic_agent_active_learning_propose_episode_repair(ctx, {"episode_id": episode_id})
    assert proposed["ok"] is True, proposed
    assert proposed["production_effective"] is False
    assert proposed["production_mutation"] is False
    review = json.loads(Path(proposed["path"]).read_text(encoding="utf-8"))
    assert review["promotion"] is False
    assert review["control_policy_modified"] is False
    assert review["failure_class"].startswith("planning.output_reference_contract/")


def test_readonly_active_learning_four_event_chain_executes_end_to_end(tmp_path: Path):
    import asyncio

    root = tmp_path / "workspace"
    instance = root / "instances" / "04"
    instance.mkdir(parents=True)
    episode_id = "episode_fullchain"
    bundle = root / "share" / "mind" / "governance" / "episodes" / episode_id
    bundle.mkdir(parents=True)
    failure_class = "planning.output_reference_contract/typed_reference_unresolved"
    state = {
        "episode_id": episode_id, "task_id": "task-failed", "instance_id": "04",
        "status": "failed", "failure_classes": [failure_class],
        "failure_details": [{"class": failure_class, "error": "typed reference unresolved"}],
    }
    (bundle / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (bundle / "trace.jsonl").write_text(json.dumps({
        "type": "plan_executor_step_completed",
        "payload": {"event_type": "atomic_convert_md_to_pdf", "ok": False,
                    "error": "typed reference unresolved"},
    }) + "\n", encoding="utf-8")
    task = TaskInstance.create(str(instance), "Event-first 只读主动学习", task_id="review")
    plan = MicroPlan(plan=[
        HarnessStep("observe_episode", "agent_active_learning_observe_episode",
                    {"episode_id": episode_id}, []),
        HarnessStep("select_diagnostic", "agent_active_learning_select",
                    {"instance_ids": ["04"], "focus_failure_class": failure_class},
                    ["observe_episode"]),
        HarnessStep("diagnose_episode", "agent_active_learning_diagnostic_shadow",
                    {"failure_class": failure_class,
                     "evidence_refs": [str(bundle / "state.json")]},
                    ["select_diagnostic"]),
        HarnessStep("propose_repair", "agent_active_learning_propose_episode_repair",
                    {"episode_id": episode_id}, ["diagnose_episode"]),
    ], expected_artifacts=[{
        "type": "file", "pattern": "active_learning_review_*.json",
        "description": "只读主动学习审查记录", "required": True,
    }])
    progress: list[dict] = []

    async def capture(update: dict):
        progress.append(dict(update))

    result = asyncio.run(run_harness_plan(
        workspace=str(instance),
        event=MindEvent(type=EventType.BATCH_PLAN, payload={"task_id": "review"}),
        title="只读主动学习验收", project_dir=task.working_dir,
        state_md="", artifact_path="", adapter=None,
        build_action_prompt=lambda *args: "", parse_structured_response=lambda text: {},
        micro_plan=plan, planner_llm_calls=0, progress_callback=capture,
    ))

    assert result.ok is True, result.reason
    assert all(value.get("ok") is True for value in result.step_results.values())
    proposal = result.step_results["propose_repair"]
    assert proposal["production_effective"] is False
    assert proposal["production_mutation"] is False
    review = json.loads(Path(proposal["path"]).read_text(encoding="utf-8"))
    assert review["control_policy_modified"] is False
    assert review["promotion"] is False
    assert TaskInstance.load(str(instance), "review").completion_status == "done"
    completed = [row for row in progress if row.get("phase") == "step_complete"]
    assert len(completed) == 4
    assert all(row["ok"] is True for row in completed)
