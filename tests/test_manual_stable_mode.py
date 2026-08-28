import asyncio
import importlib
import json
import os
from types import SimpleNamespace

import pytest

import partner.mind.harness as harness_module
import partner.mind.executor as executor_module
from partner.mind.research_loop import on_task_done
from partner.mind.harness import (
    EventRegistry,
    HarnessEventSpec,
    HarnessStep,
    MicroPlan,
    PlanExecutor,
    StateStore,
    _atomic_inspect_file,
    _atomic_compose_structured_result,
    _deterministic_named_source_extract,
    _json_from_llm,
    _llm_event_handler,
    _local_create_file,
    _local_read_file,
    _maybe_trigger_self_reflect_after_write,
    _step_result_summary,
)
from partner.harness_core import TaskInstance
from partner.mind.executor import (
    _batch_plan_dedup_key,
    _manual_task_input_paths,
    _required_output_exts,
    _final_report_delivery_satisfied,
    _resolve_one_shot_output_files,
    _sanitize_user_report_text,
    _select_direct_governance_event,
    _run_batch_check_rule,
)
from partner.planner.batch_planner import (
    BatchPlanner,
    _ensure_write_artifact,
    _manual_environment_contract,
    _manual_experiment_intervention,
    _manual_preflight_plan,
)
from partner.state.config import manual_stable_mode, runtime_capability_enabled, runtime_mode
from partner.v2.campaign_events import atomic_create_campaign, atomic_enqueue_campaign_work
from partner.v2.iteration_events import atomic_next_iteration, atomic_strict_reflect


def _workspace(tmp_path):
    root = tmp_path / "partner_workspace"
    instance = root / "instances" / "03"
    instance.mkdir(parents=True)
    config = root / "config"
    config.mkdir()
    (config / "partner_config.json").write_text(json.dumps({
        "workspace": {"path": str(root)},
        "runtime": {
            "mode": "manual_stable",
            "automatic_campaigns": False,
            "automatic_iteration": False,
            "automatic_self_heal": False,
            "autonomous_cron": False,
            "step_messages": True,
        },
    }), encoding="utf-8")
    return root, instance


def test_manual_stable_is_fail_closed_and_experimental_capabilities_are_off(tmp_path):
    root, instance = _workspace(tmp_path)
    assert runtime_mode(str(instance)) == "manual_stable"
    assert manual_stable_mode(str(instance)) is True
    for capability in ("automatic_campaigns", "automatic_iteration", "automatic_self_heal", "autonomous_cron"):
        assert runtime_capability_enabled(str(instance), capability) is False
    assert runtime_capability_enabled(str(instance), "step_messages") is True


def test_manual_stable_never_selects_legacy_campaign_shortcut():
    handlers = {"continuous_project_step": object(), "review_manual_evolution_evidence": object()}
    assert _select_direct_governance_event(
        "请执行 continuous_project_step", handlers, manual_mode=True,
    ) == ""
    assert _select_direct_governance_event(
        "禁止 continuous_project_step，请生成普通报告", handlers, manual_mode=False,
    ) == ""
    assert _select_direct_governance_event(
        "执行 continuous_project_step", handlers, manual_mode=False,
    ) == "continuous_project_step"


def test_manual_batch_dedup_uses_full_request_not_compact_title():
    title = "用户显式触发 04"
    first = _batch_plan_dedup_key("读取 source-a 并生成 report-a.md", title)
    duplicate = _batch_plan_dedup_key("读取 source-a  并生成 report-a.md", title)
    retry = _batch_plan_dedup_key("读取 source-b 并生成 report-b.md", title)
    assert first == duplicate
    assert first != retry


def test_manual_input_paths_exclude_current_task_outputs(tmp_path):
    source = tmp_path / "source.md"
    output = tmp_path / "report.md"
    source.write_text("source", encoding="utf-8")
    output.write_text("output", encoding="utf-8")
    task = SimpleNamespace(metadata={"last_plan": [
        {"event_type": "read_file", "parameters": {"path": str(source)}},
        {"event_type": "create_file", "parameters": {"path": str(output), "content": "$read.result.content"}},
        {"event_type": "atomic_inspect_file", "parameters": {"path": str(output)}},
    ]})
    assert _manual_task_input_paths(task) == [str(source)]


def test_delivery_scans_for_missing_requested_sidecar(tmp_path, monkeypatch):
    report = tmp_path / "decision.md"
    sidecar = tmp_path / "decision.json"
    report.write_text("user report", encoding="utf-8")
    sidecar.write_text('{"decision":"promoted"}', encoding="utf-8")
    monkeypatch.setattr(executor_module, "_workspace", str(tmp_path))
    files = _resolve_one_shot_output_files(
        str(tmp_path), {"files": [str(report)]}, since_ts=0,
        required_exts={".md", ".json"}, allow_workspace_fallback=False,
    )
    assert str(report) in files
    assert str(sidecar) in files


def test_ensure_write_recognizes_manual_evolution_review_as_artifact_producer(tmp_path):
    plan = MicroPlan(
        plan=[HarnessStep("review", "review_manual_evolution_evidence", {"project_id": "p"}, [])],
        expected_artifacts=[{"type": "file", "pattern": "*.md", "required": True}],
    )
    checked = _ensure_write_artifact(plan, str(tmp_path), "生成审查报告")
    assert len(checked.plan) == 1
    assert checked.plan[0].event_type == "review_manual_evolution_evidence"


def test_rule_check_treats_existing_absolute_paths_as_inputs_and_grounded_citations(tmp_path, monkeypatch):
    _, instance = _workspace(tmp_path)
    monkeypatch.setattr(executor_module, "_workspace", str(instance))
    source_paths = []
    for index in range(3):
        source = tmp_path / f"source_{index}.md"
        source.write_text(f"source evidence {index}", encoding="utf-8")
        source_paths.append(str(source))
    task = TaskInstance.create(str(instance), "grounded report")
    report = os.path.join(task.working_dir, "manual_report.md")
    with open(report, "w", encoding="utf-8") as handle:
        handle.write(
            "# Report\n" + "introductory analysis " * 100 + "\n"
            + "\n".join(f"source_path: {path}" for path in source_paths)
            + "\n" + "evidence " * 40
        )
    task.update_expected_artifacts([{"type": "file", "pattern": "*.md", "required": True}])
    root_goal = "读取 " + "、".join(source_paths) + "，写 manual_report.md 文献报告"
    result = _run_batch_check_rule(task, root_goal, {
        "check": {"min_file_size": 100, "min_file_count": 1, "min_citations": 3},
    })
    assert result["satisfied"] is True
    assert result["citation_count"] >= 3
    assert not any(item.startswith("named_artifact:source_") for item in result["missing"])


def test_manual_stable_blocks_campaign_creation_and_enqueue(tmp_path):
    _, instance = _workspace(tmp_path)
    ctx = SimpleNamespace(workspace=str(instance))
    created = atomic_create_campaign(ctx, {"goal": "must not start"})
    enqueued = atomic_enqueue_campaign_work(ctx, {"campaign_id": "missing"})
    assert created["status"] == "disabled_in_manual_stable"
    assert enqueued["status"] == "disabled_in_manual_stable"


def test_manual_stable_blocks_reflect_and_next_iteration(tmp_path):
    _, instance = _workspace(tmp_path)
    ctx = SimpleNamespace(workspace=str(instance), working_dir=str(instance / "state" / "tasks" / "one"))
    reflected = asyncio.run(atomic_strict_reflect(ctx, {}))
    continued = asyncio.run(atomic_next_iteration(ctx, {}))
    assert reflected["status"] == "disabled_in_manual_stable"
    assert continued["status"] == "disabled_in_manual_stable"


def test_manual_task_completion_never_enqueues_research_loop(tmp_path):
    _, instance = _workspace(tmp_path)
    enqueued = []
    notified = []

    async def enqueue_fn(*args):
        enqueued.append(args)

    async def notify_fn(message):
        notified.append(message)

    result = asyncio.run(on_task_done(
        instance_id="03",
        title="manual task",
        user_request="研究一个问题",
        workspace=str(instance),
        files=[],
        event_types=["execute_code"],
        enqueue_fn=enqueue_fn,
        notify_fn=notify_fn,
        adapter=None,
    ))
    assert result is False
    assert enqueued == []
    assert notified == []


def test_core_leaf_module_import_does_not_require_eager_partner_runtime():
    module = importlib.import_module("partner.core.delivery_queue")
    assert module is not None


def test_manual_planner_does_not_inject_unrequested_design_step(tmp_path):
    _, instance = _workspace(tmp_path)
    planner = BatchPlanner.from_workspace(str(instance))
    assert planner.config["force_design"] is False


def test_planner_json_parser_handles_reasoning_and_braces_inside_strings():
    raw = '<think>reasoning without a closing tag\n' + json.dumps({
        "plan": [{
            "id": "step1",
            "event_type": "run_command",
            "parameters": {"command": "python -c \"print({\'mode\': \'manual_stable\'})\""},
            "depends_on": [],
        }]
    })
    parsed = _json_from_llm(raw)
    assert parsed["plan"][0]["event_type"] == "run_command"


def test_explicit_no_pdf_is_not_treated_as_pdf_requirement():
    assert ".pdf" not in _required_output_exts("不要生成PDF，请只发送文字结果")
    assert ".pdf" in _required_output_exts("请生成 PDF 报告")


def test_source_path_extension_is_not_treated_as_output_requirement():
    required = _required_output_exts(
        "读取 /tmp/trajectory.py，并生成 harness_episode_learning_canary.md"
    )
    assert ".py" not in required
    assert ".md" in required
    assert ".py" in _required_output_exts("请修改 trajectory.py 并保存")


def test_explicit_markdown_report_filename_need_not_contain_report_word():
    assert _final_report_delivery_satisfied(
        "生成 harness_episode_learning_closed_loop.md 中文报告",
        executor_module.EventType.BATCH_PLAN,
        ["/tmp/harness_episode_learning_closed_loop.md"],
    ) is True


def test_shared_partner_config_path_is_recovered_for_manual_read(tmp_path):
    root, instance = _workspace(tmp_path)
    result = _local_read_file(
        SimpleNamespace(workspace=str(instance)),
        {"path": str(root / "partner_config.json")},
    )
    assert result["ok"] is True
    assert "manual_stable" in result["content"]
    assert result["path"].endswith("config/partner_config.json")


def test_reasoning_trace_is_removed_from_user_facing_summary():
    text = _sanitize_user_report_text("<think>private reasoning</think>\n实际结论：manual_stable")
    assert "private reasoning" not in text
    assert text == "实际结论：manual_stable"


def test_manual_report_write_does_not_inject_strict_reflect(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    report = task_dir / "report.md"
    report.write_text("真实报告", encoding="utf-8")
    ctx = SimpleNamespace(workspace=str(instance), working_dir=str(task_dir), task_instance=None)

    _maybe_trigger_self_reflect_after_write(ctx, str(report), "真实报告")

    inbox = instance / "state" / "desktop_inbox.jsonl"
    assert not inbox.exists()


def test_atomic_inspect_file_allows_partner_source_but_not_arbitrary_absolute_path(tmp_path):
    root, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    ctx = SimpleNamespace(workspace=str(instance), working_dir=str(task_dir))
    repo_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "partner", "mind", "harness.py"))

    result = _atomic_inspect_file(ctx, {"path": repo_file, "max_chars": 80})
    assert result["ok"] is True
    assert "Micro-planning harness" in result["content"]

    evidence = root / "share" / "evidence" / "project" / "run" / "manifest.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"status":"verified"}', encoding="utf-8")
    governed = _atomic_inspect_file(ctx, {"path": str(evidence)})
    assert governed["ok"] is True
    assert "verified" in governed["content"]

    outside = tmp_path / "private.txt"
    outside.write_text("do not read", encoding="utf-8")
    try:
        _atomic_inspect_file(ctx, {"path": str(outside)})
    except ValueError as exc:
        assert "allowed read-only roots" in str(exc)
    else:
        raise AssertionError("arbitrary absolute read must remain blocked")


def _manual_registry():
    registry = EventRegistry()
    noop = lambda ctx, params: {"ok": True}
    registry.register(HarnessEventSpec(
        "atomic_inspect_file", "atomic", "inspect", noop,
        reads_existing_artifact=True, execution_method="local",
    ))
    registry.register(HarnessEventSpec(
        "atomic_write_artifact", "atomic", "write", noop,
        produces_artifact=True, execution_method="local",
    ))
    return registry


def test_manual_candidate_prompt_contract_is_feature_isolated(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state/tasks/isolated"
    baseline = (
        "[strategy_id=baseline_current_preflight_v1] [policy_arm=baseline] "
        "[experiment_id=experiment_test] [match_key=pair_1] compare sources"
    )
    candidate = baseline.replace("baseline_current_preflight_v1", "candidate_preflight_contract_v2").replace(
        "policy_arm=baseline", "policy_arm=candidate"
    )

    baseline_route = _manual_experiment_intervention(baseline)
    candidate_route = _manual_experiment_intervention(candidate)
    assert baseline_route["active"] is False
    assert baseline_route["route"] == "baseline_current_contract"
    assert candidate_route["active"] is True
    assert candidate_route["route"] == "candidate_prompt_contract_v2"
    assert "候选实验专属" not in _manual_environment_contract(str(instance), str(task_dir), baseline)
    assert "候选实验专属" in _manual_environment_contract(str(instance), str(task_dir), candidate)
    assert _manual_experiment_intervention("ordinary production task")["route"] == "production_current"


def test_candidate_preflight_binds_literal_named_source_paths_but_baseline_does_not(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state/tasks/isolated-binding"
    task_dir.mkdir(parents=True)
    source = task_dir / "source.md"
    source.write_text("grounded source text", encoding="utf-8")
    registry = _manual_registry()
    registry.register(HarnessEventSpec(
        "extract", "smart", "extract", lambda ctx, params: {"ok": True}, execution_method="local",
    ))

    def plan():
        return MicroPlan(plan=[
            HarnessStep("read", "atomic_inspect_file", {"path": str(source)}, []),
            HarnessStep("extract", "extract", {
                "data": {"source": "from read"},
                "source_paths": {"source": str(source)},
                "fields": ["conclusion", "source_path", "evidence_quote"],
            }, ["read"]),
        ], expected_artifacts=[])

    baseline = _manual_preflight_plan(
        plan(), registry=registry, workspace=str(instance), working_dir=str(task_dir),
        user_message=("[strategy_id=baseline_current_preflight_v1] [policy_arm=baseline] "
                      "[experiment_id=e] [match_key=p]"),
    )
    candidate = _manual_preflight_plan(
        plan(), registry=registry, workspace=str(instance), working_dir=str(task_dir),
        user_message=("[strategy_id=candidate_preflight_contract_v2] [policy_arm=candidate] "
                      "[experiment_id=e] [match_key=p]"),
    )
    assert baseline.plan[1].parameters["data"]["source"] == "from read"
    assert candidate.plan[1].parameters["data"]["source"] == "$read.result.content"
    assert candidate.plan[1].parameters["source_paths"]["source"] == str(source)


def test_manual_plan_preflight_resolves_known_repo_path_and_confines_output(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": "partner/mind/harness.py"}, []),
        HarnessStep("write", "atomic_write_artifact", {
            "path": "/tmp/summary.md", "content": "$read.result.content",
        }, ["read"]),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=_manual_registry(), workspace=str(instance), working_dir=str(task_dir),
    )

    assert checked.plan[0].parameters["path"].endswith("/partner/mind/harness.py")
    assert checked.plan[1].parameters["path"] == str(task_dir / "summary.md")


def test_manual_plan_preflight_allows_governed_cross_instance_evidence(tmp_path):
    root, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    evidence = root / "share" / "evidence" / "project" / "run" / "manifest.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"status":"verified"}', encoding="utf-8")
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": str(evidence)}, []),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=_manual_registry(), workspace=str(instance), working_dir=str(task_dir),
    )

    assert checked.plan[0].parameters["path"] == str(evidence)


def test_manual_plan_preflight_rejects_unknown_event_and_invented_input(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    plan = MicroPlan(plan=[
        HarnessStep("bad", "check_quality", {}, []),
        HarnessStep("invented", "atomic_inspect_file", {"path": "invented.txt"}, []),
    ], expected_artifacts=[])

    try:
        _manual_preflight_plan(
            plan, registry=_manual_registry(), workspace=str(instance), working_dir=str(task_dir),
        )
    except ValueError as exc:
        text = str(exc)
        assert "not allowed in manual_stable" in text
        assert "missing or outside allowed roots" in text
    else:
        raise AssertionError("invalid manual plan must fail before execution")


def test_manual_preflight_normalizes_inspect_file_path_alias(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"file_path": __file__}, []),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=_manual_registry(), workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[0].parameters["path"] == os.path.realpath(__file__)


def test_manual_preflight_rejects_autonomous_continuation_and_fake_external_listing(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec(
        "continuous_project_step", "atomic", "auto", lambda ctx, params: {"ok": True},
    ))
    registry.register(HarnessEventSpec(
        "atomic_list_project_files", "atomic", "list", lambda ctx, params: {"ok": True},
    ))
    plan = MicroPlan(plan=[
        HarnessStep("list", "atomic_list_project_files", {"directory": "/tmp/external"}, []),
        HarnessStep("continue", "continuous_project_step", {}, ["list"]),
    ], expected_artifacts=[])
    try:
        _manual_preflight_plan(
            plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
        )
    except ValueError as exc:
        assert "only lists the current task directory" in str(exc)
        assert "autonomous event continuous_project_step is disabled" in str(exc)
    else:
        raise AssertionError("autonomous/fake directory plan must not pass manual preflight")


def test_manual_plan_preflight_requires_explicit_file_producer(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec(
        "atomic_compose_structured_result", "atomic", "compose", lambda ctx, params: {"ok": True},
        execution_method="local",
    ))
    plan = MicroPlan(
        plan=[HarnessStep("compose", "atomic_compose_structured_result", {"sources": "facts"}, [])],
        expected_artifacts=[{"type": "file", "pattern": "report.md", "required": True}],
    )
    try:
        _manual_preflight_plan(plan, registry=registry, workspace=str(instance), working_dir=str(task_dir))
    except ValueError as exc:
        assert "explicit produces_artifact write step" in str(exc)
    else:
        raise AssertionError("compose-only plan must not satisfy a file contract")


def test_manual_preflight_normalizes_dependency_ids_inside_data_list(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read_a", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("read_b", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("merge", "extract", {"data": ["read_a", "read_b"]}, ["read_a", "read_b"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[-1].parameters["data"] == [
        "$read_a.result.content", "$read_b.result.content",
    ]


def test_manual_preflight_adds_strict_named_source_extract_instruction(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read_a", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("read_b", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("merge", "extract", {
            "data": {"alpha": "read_a", "beta": "read_b"},
            "source_paths": {"alpha": __file__, "beta": __file__},
            "fields": ["conclusion", "source_path", "evidence_quote"],
        }, ["read_a", "read_b"]),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    instruction = checked.plan[-1].parameters["instruction"]
    assert "alpha" in instruction and "beta" in instruction
    assert "逐字、连续复制" in instruction
    assert "禁止改写" in instruction and "省略号" in instruction


def test_manual_preflight_wires_placeholder_named_sources_from_dependencies(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read_a", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("read_b", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("merge", "extract", {
            "data": {"alpha": "<file_content_from_step1>", "beta": "<file_content_from_step2>"},
            "fields": ["conclusion", "source_path", "evidence_quote"],
        }, ["read_a", "read_b"]),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    params = checked.plan[-1].parameters
    assert params["data"] == {
        "alpha": "$read_a.result.content", "beta": "$read_b.result.content",
    }
    assert params["source_paths"] == {"alpha": __file__, "beta": __file__}


def test_manual_preflight_normalizes_nested_placeholder_refs_and_quote_schema(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("step1", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("step2", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("quotes", "extract", {
            "data": {"alpha": "<content of step1>", "beta": "<content of step2>"},
            "fields": ["evidence_quote_per_source", "source_path_per_source"],
        }, ["step1", "step2"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    params = checked.plan[-1].parameters
    assert params["data"] == {
        "alpha": "$step1.result.content", "beta": "$step2.result.content",
    }
    assert params["fields"] == ["conclusion", "source_path", "evidence_quote"]


def test_manual_preflight_wires_synthesis_dependency_when_planner_omits_input(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("compare", "atomic", "compare", lambda ctx, params: {"ok": True}))
    registry.register(HarnessEventSpec("generate_text", "atomic", "generate", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("compose", "generate_text", {"prompt": "summarize the supplied evidence"}, ["read"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[-1].parameters["data"] == "$read.result.content"
    assert "禁止编造步骤编号" in checked.plan[-1].parameters["prompt"]


def test_manual_preflight_maps_grounded_analyze_alias_to_generate_text(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("generate_text", "atomic", "generate", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("analysis", "analyze", {"prompt": "analyze supplied evidence"}, ["read"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[-1].event_type == "generate_text"
    assert checked.plan[-1].parameters["data"] == "$read.result.content"


def test_manual_preflight_maps_report_compose_wrapper_to_generate_text(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("generate_text", "atomic", "generate", lambda ctx, params: {"ok": True}))
    registry.register(HarnessEventSpec(
        "atomic_compose_structured_result", "atomic", "compose", lambda ctx, params: {"ok": True},
    ))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("compose", "atomic_compose_structured_result", {
            "data": "$read.result.content", "sections": ["evidence", "limitations"],
            "min_words": 600, "output_language": "zh",
        }, ["read"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    step = checked.plan[-1]
    assert step.event_type == "generate_text"
    assert "不少于 600 字" in step.parameters["prompt"]
    assert "evidence" in step.parameters["prompt"]
    assert step.parameters["data"] == "$read.result.content"


def test_manual_preflight_repairs_repeated_and_embedded_result_references(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec(
        "atomic_compose_structured_result", "atomic", "compose", lambda ctx, params: {"ok": True},
    ))
    registry.register(HarnessEventSpec("generate_text", "atomic", "generate", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("compose", "atomic_compose_structured_result", {
            "data": {"source": "$read.result.result"},
        }, ["read"]),
        HarnessStep("report", "generate_text", {
            "prompt": "请依据 {{compose.result}} 生成报告",
            "data": "$compose.result.content",
        }, ["compose"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[1].parameters["data"] == {"source": "$read.result.content"}
    assert "$compose.result.content" in checked.plan[2].parameters["prompt"]


def test_manual_preflight_inserts_report_synthesis_and_allows_readback(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    registry.register(HarnessEventSpec("generate_text", "atomic", "generate", lambda ctx, params: {"ok": True}))
    registry.register(HarnessEventSpec(
        "create_file", "atomic", "write", lambda ctx, params: {"ok": True}, produces_artifact=True,
    ))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("quotes", "extract", {
            "data": {"source": "$read.result.content"},
            "fields": ["conclusion", "source_path", "evidence_quote"],
        }, ["read"]),
        HarnessStep("write", "create_file", {
            "path": "report.md", "content": "# 模板\n结论：\n引文：\n",
        }, ["quotes"]),
        HarnessStep("verify", "atomic_inspect_file", {"path": "report.md"}, ["write"]),
    ], expected_artifacts=[{"type": "file", "pattern": "report.md", "required": True}])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert [step.event_type for step in checked.plan] == [
        "atomic_inspect_file", "extract", "generate_text", "create_file", "atomic_inspect_file",
    ]
    writer = checked.plan[3]
    assert writer.depends_on == ["write_synthesis"]
    assert writer.parameters["content"] == "$write_synthesis.result.content"
    assert checked.plan[4].parameters["path"] == str(task_dir / "report.md")


def test_promoted_truth_policy_interposes_deterministic_extract_before_report(tmp_path):
    root, _ = _workspace(tmp_path)
    instance = root / "instances" / "04"
    instance.mkdir(parents=True)
    control = root / "share" / "mind" / "governance" / "rl" / "control_policy.json"
    control.parent.mkdir(parents=True)
    control.write_text(json.dumps({"promoted": {
        "literature_github_learning:manual_final_artifact_truth": "manual_stable_truth_audit_v2",
    }}), encoding="utf-8")
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    registry.register(HarnessEventSpec("generate_text", "atomic", "generate", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("report", "generate_text", {"prompt": "生成来源报告"}, ["read"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert [step.event_type for step in checked.plan] == [
        "atomic_inspect_file", "extract", "generate_text",
    ]
    assert checked.plan[1].parameters["source_paths"] == {"test_manual_stable_mode.py": __file__}
    assert checked.plan[2].parameters["data"] == "$report_truth_extract.result.content"


def test_promoted_truth_policy_keeps_exact_extract_on_lossy_synthesis_path(tmp_path):
    root, _ = _workspace(tmp_path)
    instance = root / "instances" / "04"
    instance.mkdir(parents=True)
    control = root / "share" / "mind" / "governance" / "rl" / "control_policy.json"
    control.parent.mkdir(parents=True)
    control.write_text(json.dumps({"promoted": {
        "literature_github_learning:manual_final_artifact_truth": "manual_stable_truth_audit_v2",
    }}), encoding="utf-8")
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    registry.register(HarnessEventSpec("generate_text", "atomic", "generate", lambda ctx, params: {"ok": True}))
    source = os.path.abspath(__file__)
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": source}, []),
        HarnessStep("quotes", "extract", {
            "data": {"source": "$read.result.content"},
            "source_paths": {"source": source},
            "fields": ["design_principle", "closed_loop_role"],
        }, ["read"]),
        HarnessStep("analysis", "generate_text", {"task": "综合证据"}, ["quotes"]),
        HarnessStep("final", "generate_text", {
            "prompt": "输出 source_path 与 evidence_quote",
            "data": "$analysis.result.content",
        }, ["analysis"]),
    ])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
        user_message="为每个输入输出 source_path: 和 evidence_quote: 逐字引文",
    )

    quotes = checked.plan[1]
    analysis = checked.plan[2]
    final = checked.plan[3]
    assert quotes.parameters["fields"] == ["conclusion", "source_path", "evidence_quote"]
    assert quotes.parameters["format"] == "object_by_source"
    assert analysis.parameters["data"] == {
        "analysis": "$quotes.result.content", "verified_sources": "$quotes.result.content",
    }
    assert final.depends_on == ["analysis", "quotes"]
    assert final.parameters["data"] == {
        "analysis": "$analysis.result.content", "verified_sources": "$quotes.result.content",
    }
    assert "禁止从 analysis 的转述重建" in final.parameters["prompt"]


def test_manual_preflight_turns_markdown_output_spec_into_real_synthesis(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("compare", "atomic", "compare", lambda ctx, params: {"ok": True}))
    registry.register(HarnessEventSpec("generate_text", "atomic", "generate", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("analysis", "compare", {"source_a": "facts", "source_b": "proposal"}, []),
        HarnessStep("compose", "atomic_compose_structured_result", {
            "compose_inputs": {"facts": "$analysis.result.content"},
            "output_spec": {
                "artifact_kind": "markdown_artifact",
                "required_sections": ["证据", "结论"],
                "min_total_chinese_chars": 800,
            },
        }, ["analysis"]),
    ])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )

    compose = checked.plan[-1]
    assert compose.event_type == "generate_text"
    assert "不少于 800 字" in compose.parameters["prompt"]
    assert "证据, 结论" in compose.parameters["prompt"]


def test_manual_preflight_infers_report_dependency_on_prior_extract(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    registry.register(HarnessEventSpec("generate_text", "atomic", "generate", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("evidence", "extract", {"data": "read"}, ["read"]),
        HarnessStep("report", "generate_text", {
            "task": "compose evidence report from the previous extract",
        }, []),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[-1].depends_on == ["evidence"]
    assert checked.plan[-1].parameters["data"] == "$evidence.result.content"


def test_manual_preflight_rejects_empty_writer_and_unsynthesized_evidence(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("evidence", "extract", {
            "data": {"source": "read"},
            "fields": ["conclusion", "source_path", "evidence_quote"],
        }, ["read"]),
        HarnessStep("write", "create_file", {"path": "report.md", "content": None}, ["evidence"]),
    ], expected_artifacts=[])

    try:
        _manual_preflight_plan(
            plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
        )
    except ValueError as exc:
        assert "output content is empty" in str(exc)
        assert "needs a synthesis step" in str(exc)
    else:
        raise AssertionError("empty unsynthesized report must not pass preflight")


def test_manual_preflight_repairs_writer_placeholder_from_markdown_synthesis(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("compose", "extract", {
            "data": "facts", "fields": ["full_markdown_report"],
        }, []),
        HarnessStep("write", "atomic_write_artifact", {
            "path": "report.md", "content": "<完整报告内容由 compose 步骤填入>",
        }, ["compose"]),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[-1].parameters["content"] == "$compose.result.full_markdown_report"


def test_manual_preflight_rewrites_generic_synthesis_content_ref_to_report_field(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("compose", "extract", {
            "data": "facts", "fields": ["full_markdown_report"],
        }, []),
        HarnessStep("write", "atomic_write_artifact", {
            "path": "report.md", "content": "$compose.result.content",
        }, ["compose"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[-1].parameters["content"] == "$compose.result.full_markdown_report"


def test_manual_preflight_rewires_writer_with_extra_read_dependencies(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("generate_text", "atomic", "generate", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("report", "generate_text", {"task": "write report", "data": "read"}, ["read"]),
        HarnessStep("write", "atomic_write_artifact", {
            "path": "report.md", "content": {"planner_placeholder": "report.output.markdown"},
        }, ["read", "report"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[-1].parameters["content"] == "$report.result.content"


def test_manual_plan_preflight_rewires_evidence_dependent_static_template(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec(
        "smart_llm_structured_action", "smart", "analyze", lambda ctx, params: {"ok": True},
        external_call=True, execution_method="llm",
    ))
    plan = MicroPlan(plan=[
        HarnessStep("analysis", "smart_llm_structured_action", {"instruction": "analyze"}, []),
        HarnessStep("write", "atomic_write_artifact", {
            "path": "summary.md",
            "content": "# Summary\n\n## Finding\n> 由 analysis 步骤填入真实结论。" * 3,
        }, ["analysis"]),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[-1].parameters["content"] == "$analysis.result.content"


def test_manual_plan_preflight_normalizes_ref_syntax_recursively(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec(
        "smart_llm_structured_action", "smart", "analyze", lambda ctx, params: {"ok": True},
        external_call=True, execution_method="llm",
    ))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {
            "path": os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "partner", "mind", "harness.py")),
        }, []),
        HarnessStep("analysis", "smart_llm_structured_action", {
            "inputs": {"source": "$ref.read.content", "alternate": "$read.result.output.content"},
        }, ["read"]),
        HarnessStep("write", "atomic_write_artifact", {
            "path": "summary.md", "content": "$ref.analysis.markdown",
        }, ["analysis"]),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )

    assert checked.plan[1].parameters["inputs"]["source"] == "$read.result.content"
    assert checked.plan[1].parameters["inputs"]["alternate"] == "$read.result.content"
    assert checked.plan[2].parameters["content"] == "$analysis.result.content"


def test_manual_plan_preflight_normalizes_braced_planner_refs(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("extract", "extract", {
            "data": "${read.content}", "fields": ["full_markdown_report"],
        }, ["read"]),
        HarnessStep("write", "atomic_write_artifact", {
            "path": "report.md", "content": "${extract.full_markdown_report}",
        }, ["extract"]),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert checked.plan[1].parameters["data"] == "$read.result.content"
    assert checked.plan[2].parameters["content"] == "$extract.result.full_markdown_report"


def test_manual_preflight_drops_planner_owned_terminal_notification(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": __file__}, []),
        HarnessStep("notify", "post_message", {"message": "planner summary"}, ["read"]),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )

    assert [step.id for step in checked.plan] == ["read"]


def test_manual_preflight_confines_canary_decision_to_explicit_reads_and_event(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    source = tmp_path / "partner_workspace" / "share" / "mind" / "governance" / "attestation.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    registry = _manual_registry()
    registry.register(HarnessEventSpec(
        "decide_manual_canary", "atomic", "decide", lambda ctx, params: {"ok": True},
        produces_artifact=True,
    ))
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": str(source)}, []),
        HarnessStep("discover", "list_directory", {"path": str(tmp_path)}, []),
        HarnessStep("invented", "atomic_inspect_file", {"path": "$discover.result.first_file"}, ["discover"]),
        HarnessStep("decide", "decide_manual_canary", {"experiment_id": "experiment_test"}, ["read", "invented"]),
        HarnessStep("rewrite", "atomic_write_artifact", {"path": "decision.md", "content": "$decide.result.content"}, ["decide"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
        user_message=f"读取 {source} 后决定实验",
    )
    assert [step.id for step in checked.plan] == ["read", "decide"]
    assert checked.plan[-1].depends_on == ["read"]


def test_manual_preflight_normalizes_double_brace_refs_and_quote_aliases(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    source = os.path.abspath(__file__)
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": source}, []),
        HarnessStep("extract", "extract", {
            "data": {"tests": "{{read.output}}"},
            "source_paths": {"tests": source},
            "fields": ["verbatim_quote", "source_path", "key_point"],
            "instruction": "return the requested fields",
        }, ["read"]),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    params = checked.plan[1].parameters
    assert params["data"]["tests"] == "$read.result.content"
    assert params["fields"] == ["conclusion", "source_path", "evidence_quote"]
    assert "逐字、连续复制" in params["instruction"]


def test_manual_preflight_rejects_planner_owned_iteration_receipt(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("record_iteration", "atomic", "record", lambda ctx, params: {"ok": True}))
    plan = MicroPlan(plan=[HarnessStep("record", "record_iteration", {}, [])], expected_artifacts=[])

    with pytest.raises(ValueError, match="record_iteration"):
        _manual_preflight_plan(
            plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
        )


def test_manual_preflight_injects_omitted_explicit_input_into_evidence_chain(tmp_path):
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state" / "tasks" / "one"
    task_dir.mkdir(parents=True)
    registry = _manual_registry()
    registry.register(HarnessEventSpec("extract", "atomic", "extract", lambda ctx, params: {"ok": True}))
    first = task_dir / "first.md"
    previous_dir = instance / "state" / "tasks" / "previous"
    previous_dir.mkdir(parents=True)
    second = previous_dir / "previous_round.md"
    first.write_text("first exact quote", encoding="utf-8")
    second.write_text("handoff exact quote", encoding="utf-8")
    plan = MicroPlan(plan=[
        HarnessStep("read_first", "atomic_inspect_file", {"path": str(first)}, []),
        HarnessStep("extract", "extract", {
            "data": {"first": "$read_first.result.content"},
            "source_paths": {"first": str(first)},
            "fields": ["conclusion", "source_path", "evidence_quote"],
        }, ["read_first"]),
    ], expected_artifacts=[])

    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
        user_message=f"承接 {second} 并读取 {first}",
    )
    injected = checked.plan[0]
    extract = next(step for step in checked.plan if step.event_type == "extract")
    assert injected.event_type == "atomic_inspect_file"
    assert injected.parameters["path"] == str(second)
    assert injected.id in extract.depends_on
    assert str(second) in extract.parameters["source_paths"].values()
    assert f"${injected.id}.result.content" in extract.parameters["data"].values()


def test_atomic_inspect_allows_explicit_same_instance_historical_artifact(tmp_path):
    _, instance = _workspace(tmp_path)
    historical = instance / "state" / "tasks" / "old" / "report.md"
    historical.parent.mkdir(parents=True)
    historical.write_text("grounded previous iteration", encoding="utf-8")
    current = instance / "state" / "tasks" / "current"
    current.mkdir(parents=True)
    ctx = SimpleNamespace(workspace=str(instance), working_dir=str(current))

    result = _atomic_inspect_file(ctx, {"path": str(historical)})
    assert result["ok"] is True
    assert result["content"] == "grounded previous iteration"


def test_atomic_inspect_allows_legacy_outgoing_delivery_copy(tmp_path):
    root, instance = _workspace(tmp_path)
    outgoing = root / "files" / "outgoing" / "20260826_old_report.md"
    outgoing.parent.mkdir(parents=True)
    outgoing.write_text("delivered immutable copy", encoding="utf-8")
    current = instance / "state" / "tasks" / "current"
    current.mkdir(parents=True)
    result = _atomic_inspect_file(
        SimpleNamespace(workspace=str(instance), working_dir=str(current)),
        {"path": str(outgoing)},
    )
    assert result["ok"] is True
    assert result["content"] == "delivered immutable copy"


def test_manual_preflight_allows_explicit_outgoing_delivery_copy(tmp_path):
    root, instance = _workspace(tmp_path)
    outgoing = root / "files" / "outgoing" / "old_report.md"
    outgoing.parent.mkdir(parents=True)
    outgoing.write_text("durable delivered evidence", encoding="utf-8")
    current = instance / "state" / "tasks" / "current"
    current.mkdir(parents=True)
    registry = _manual_registry()
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": str(outgoing)}, []),
    ])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(current),
        user_message=f"读取 {outgoing}",
    )
    assert checked.plan[0].parameters["path"] == str(outgoing)


def test_batch_planner_semantically_repairs_invalid_manual_plan_before_execution(tmp_path):
    _, instance = _workspace(tmp_path)
    task = TaskInstance.create(str(instance), "读取 harness.py 并写出摘要")
    registry = _manual_registry()
    repo_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "partner", "mind", "harness.py"))

    class Adapter:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def chat(self, prompt, purpose=""):
            self.calls += 1
            if "语义检查失败" not in prompt:
                return json.dumps({"plan": [{
                    "id": "bad", "event_type": "check_quality",
                    "parameters": {}, "depends_on": [],
                }]})
            return json.dumps({"plan": [
                {"id": "read", "event_type": "atomic_inspect_file",
                 "parameters": {"path": repo_file}, "depends_on": []},
                {"id": "write", "event_type": "atomic_write_artifact",
                 "parameters": {"path": "summary.md", "content": "$read.result.content"},
                 "depends_on": ["read"]},
            ]})

    adapter = Adapter()
    planner = BatchPlanner(
        workspace=str(instance),
        config={"min_steps": 1, "max_steps": 5, "unavailable_retries": 0, "force_design": False},
        world_model_client=None,
    )
    plan, calls = asyncio.run(planner.plan(
        adapter=adapter,
        user_message=task.user_message,
        task_instance=task,
        registry=registry,
    ))

    assert calls == 2
    assert [step.event_type for step in plan.plan] == ["atomic_inspect_file", "atomic_write_artifact"]
    assert plan.plan[0].parameters["path"] == repo_file
    assert plan.plan[1].parameters["path"] == os.path.join(task.working_dir, "summary.md")


def test_create_file_rejects_empty_resolved_content(tmp_path):
    ctx = SimpleNamespace(
        working_dir=str(tmp_path), project_dir=str(tmp_path), task_instance=None,
    )
    result = _local_create_file(ctx, {"path": str(tmp_path / "empty.md"), "content": ""})
    assert result["ok"] is False
    assert "empty content" in result["error"]
    assert not (tmp_path / "empty.md").exists()


def test_compose_structured_result_consumes_sources_content():
    ctx = SimpleNamespace(event=SimpleNamespace(type=SimpleNamespace(value="manual")))
    result = _atomic_compose_structured_result(ctx, {
        "sources": {"content": "真实来源结论"},
    })
    assert result["ok"] is True
    assert result["content"] == "真实来源结论"
    assert result["parsed"]["artifact_content"] == "真实来源结论"


def test_compose_structured_result_accepts_resolved_data_alias():
    ctx = SimpleNamespace(event=SimpleNamespace(type=SimpleNamespace(value="manual")))
    result = _atomic_compose_structured_result(ctx, {
        "data": {"aether": "真实来源 A", "ai2bmd": "真实来源 B"},
    })
    assert result["ok"] is True
    assert "真实来源 A" in result["content"]
    assert "真实来源 B" in result["content"]


def test_named_source_quote_extract_is_deterministic_and_verbatim():
    source = "# Heading\n\nA sufficiently long exact source sentence for deterministic verification.\n"
    result = _deterministic_named_source_extract({
        "data": {"alpha": source},
        "source_paths": {"alpha": "/evidence/alpha.md"},
        "fields": ["conclusion", "source_path", "evidence_quote"],
    })
    assert result["ok"] is True
    assert result["deterministic"] is True
    row = result["json"]["alpha"]
    assert row["source_path"] == "/evidence/alpha.md"
    assert row["evidence_quote"] in source


def test_named_source_quote_extract_skips_stale_capability_claim():
    source = (
        "实际上当前环境的 shell 工具状态未知，我直接基于上游输入进行分析。\n"
        "This later grounded sentence describes the durable event and reducer boundary exactly.\n"
    )
    result = _deterministic_named_source_extract({
        "data": {"alpha": source},
        "source_paths": {"alpha": "/evidence/alpha.md"},
        "fields": ["conclusion", "source_path", "evidence_quote"],
    })
    assert result["ok"] is True
    assert "durable event" in result["json"]["alpha"]["evidence_quote"]
    assert "shell" not in result["json"]["alpha"]["evidence_quote"]


def test_compose_structured_result_renders_json_as_markdown():
    ctx = SimpleNamespace(event=SimpleNamespace(type=SimpleNamespace(value="manual")))
    result = _atomic_compose_structured_result(ctx, {
        "sources": {"content": json.dumps({"positioning": "本地编码代理", "risks": ["不直接复制"]}, ensure_ascii=False)},
        "output_format": "markdown",
    })
    assert result["ok"] is True
    assert result["content"].startswith("# 结构化核对结果")
    assert "本地编码代理" in result["content"]
    assert "不直接复制" in result["content"]


def test_compose_structured_result_renders_fenced_json_as_markdown():
    ctx = SimpleNamespace(event=SimpleNamespace(type=SimpleNamespace(value="manual")))
    result = _atomic_compose_structured_result(ctx, {
        "sources": {"content": '```json\n{"positioning":"本地代理"}\n```'},
        "output_format": "markdown",
    })
    assert result["content"].startswith("# 结构化核对结果")
    assert "本地代理" in result["content"]
    assert "```json" not in result["content"]


def test_compose_structured_result_hides_internal_control_fields():
    ctx = SimpleNamespace(event=SimpleNamespace(type=SimpleNamespace(value="manual")))
    result = _atomic_compose_structured_result(ctx, {
        "sources": {"content": json.dumps({
            "_harness_event_type": "extract",
            "finding": "公开结论",
        }, ensure_ascii=False)},
        "output_format": "markdown",
    })
    assert "公开结论" in result["content"]
    assert "_harness_event_type" not in result["content"]


def test_read_step_summary_does_not_claim_source_file_was_generated():
    summary = _step_result_summary({"ok": True, "path": "/repo/README.md", "size": 123}, "atomic_inspect_file")
    assert summary == "已读取 README.md（123 字节）"


def test_atomic_llm_event_is_counted_as_model_call(tmp_path):
    async def handler(ctx, params):
        return {"ok": True, "content": "grounded"}

    registry = EventRegistry()
    registry.register(HarnessEventSpec(
        "extract", "atomic", "extract", handler, execution_method="llm",
    ))
    ctx = SimpleNamespace(
        title="grounded extraction", task_instance=None, progress_callback=None,
        workspace=str(tmp_path), project_dir=str(tmp_path),
        event=SimpleNamespace(type=SimpleNamespace(value="manual"), payload={}),
    )
    _, model_calls, _, _ = asyncio.run(PlanExecutor(registry, StateStore(str(tmp_path))).execute(
        ctx, [HarnessStep("extract", "extract", {"data": "source"}, [])],
    ))
    assert model_calls == 1


def test_retried_llm_event_counts_every_real_invocation(tmp_path, monkeypatch):
    calls = 0

    async def handler(ctx, params):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"ok": False, "error": "retry me", "retryable": True}
        return {"ok": True, "content": "grounded"}

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(harness_module.asyncio, "sleep", no_sleep)
    registry = EventRegistry()
    registry.register(HarnessEventSpec(
        "extract", "atomic", "extract", handler, execution_method="llm",
    ))
    ctx = SimpleNamespace(
        title="grounded extraction", task_instance=None, progress_callback=None,
        workspace=str(tmp_path), project_dir=str(tmp_path),
        event=SimpleNamespace(type=SimpleNamespace(value="manual"), payload={}),
    )
    _, model_calls, _, _ = asyncio.run(PlanExecutor(registry, StateStore(str(tmp_path))).execute(
        ctx, [HarnessStep("extract", "extract", {"data": "source"}, [])],
    ))
    assert calls == 2
    assert model_calls == 2


def test_llm_handler_uses_concrete_harness_event_and_strips_thinking(tmp_path):
    observed = {}

    class Adapter:
        def chat(self, prompt, purpose=""):
            observed["prompt"] = prompt
            observed["purpose"] = purpose
            return "<think>private reasoning</think>{\"summary\":\"真实提取\"}"

    ctx = SimpleNamespace(
        adapter=Adapter(),
        event=SimpleNamespace(type=SimpleNamespace(value="outer_batch")),
        title="test",
        state_md="",
        artifact_path=str(tmp_path / "result.md"),
        build_action_prompt=None,
        parse_structured_response=None,
        robust_executor=None,
        task_instance=None,
    )
    result = asyncio.run(_llm_event_handler(ctx, {
        "_harness_event_type": "extract",
        "data": "source",
    }))
    assert result["ok"] is True
    assert "执行 extract 操作" in observed["prompt"]
    assert "只能依据参数中显式提供" in observed["prompt"]
    assert observed["purpose"] == "action_think"
    assert "private reasoning" not in result["content"]


def test_llm_handler_does_not_truncate_downstream_content(tmp_path):
    payload = "x" * 4500

    class Adapter:
        def chat(self, prompt, purpose=""):
            return payload

    ctx = SimpleNamespace(
        adapter=Adapter(), event=SimpleNamespace(type=SimpleNamespace(value="outer")),
        title="test", state_md="", artifact_path=str(tmp_path / "result.md"),
        build_action_prompt=None, parse_structured_response=None,
        robust_executor=None, task_instance=None,
    )
    result = asyncio.run(_llm_event_handler(ctx, {"_harness_event_type": "compare", "data": "source"}))
    assert result["content"] == payload
    assert len(result["content_preview"]) < len(result["content"])


def test_llm_handler_keeps_step_data_when_action_prompt_builder_exists(tmp_path):
    observed = {}

    class Adapter:
        def chat(self, prompt, purpose=""):
            observed["prompt"] = prompt
            return '{"finding":"ok"}'

    ctx = SimpleNamespace(
        adapter=Adapter(), event=SimpleNamespace(type=SimpleNamespace(value="outer")),
        title="test", state_md="state", artifact_path=str(tmp_path / "result.md"),
        build_action_prompt=lambda *args: "通用执行上下文",
        parse_structured_response=None, robust_executor=None, task_instance=None,
    )
    result = asyncio.run(_llm_event_handler(ctx, {
        "_harness_event_type": "extract", "data": {"source": "UNIQUE_SOURCE_TEXT"},
    }))
    assert result["ok"] is True
    assert "UNIQUE_SOURCE_TEXT" in observed["prompt"]
    assert "通用执行上下文" in observed["prompt"]


def test_extract_rejects_truncated_outer_json_even_if_nested_object_is_valid(tmp_path):
    class Adapter:
        def chat(self, prompt, purpose=""):
            return '```json\n{"outer":{"finding":"nested-valid"},"unfinished":"text'

    ctx = SimpleNamespace(
        adapter=Adapter(), event=SimpleNamespace(type=SimpleNamespace(value="outer")),
        title="test", state_md="", artifact_path=str(tmp_path / "result.md"),
        build_action_prompt=None, parse_structured_response=_json_from_llm,
        robust_executor=None, task_instance=None,
    )
    result = asyncio.run(_llm_event_handler(ctx, {
        "_harness_event_type": "extract", "data": "source",
    }))
    assert result["ok"] is False
    assert "invalid JSON" in result["error"]


def test_extract_rejects_evidence_quote_absent_from_supplied_source(tmp_path):
    class Adapter:
        def chat(self, prompt, purpose=""):
            return json.dumps({"finding": {"conclusion": "x", "evidence_quote": "invented"}})

    ctx = SimpleNamespace(
        adapter=Adapter(), event=SimpleNamespace(type=SimpleNamespace(value="outer")),
        title="test", state_md="", artifact_path=str(tmp_path / "result.md"),
        build_action_prompt=None, parse_structured_response=None,
        robust_executor=None, task_instance=None,
    )
    result = asyncio.run(_llm_event_handler(ctx, {
        "_harness_event_type": "extract", "data": "actual source",
    }))
    assert result["ok"] is False
    assert "not found in supplied source" in result["error"]


def test_extract_rejects_quote_attributed_to_wrong_named_source(tmp_path):
    class Adapter:
        def chat(self, prompt, purpose=""):
            return json.dumps({
                "alpha": {"conclusion": "x", "evidence_quote": "quote from beta"},
                "beta": {"conclusion": "y", "evidence_quote": "quote from alpha"},
            })

    ctx = SimpleNamespace(
        adapter=Adapter(), event=SimpleNamespace(type=SimpleNamespace(value="outer")),
        title="test", state_md="", artifact_path=str(tmp_path / "result.md"),
        build_action_prompt=None, parse_structured_response=None,
        robust_executor=None, task_instance=None,
    )
    result = asyncio.run(_llm_event_handler(ctx, {
        "_harness_event_type": "extract",
        "data": {"alpha": "quote from alpha", "beta": "quote from beta"},
    }))
    assert result["ok"] is False
    assert "alpha:" in result["error"]


def test_extract_rejects_not_found_for_nonempty_required_named_sources(tmp_path):
    class Adapter:
        def chat(self, prompt, purpose=""):
            return json.dumps({
                "alpha": {
                    "conclusion": "not_found",
                    "source_path": "/source/a",
                    "evidence_quote": "not_found",
                },
            })

    ctx = SimpleNamespace(
        adapter=Adapter(), event=SimpleNamespace(type=SimpleNamespace(value="outer")),
        title="test", state_md="", artifact_path=str(tmp_path / "result.md"),
        build_action_prompt=None, parse_structured_response=None,
        robust_executor=None, task_instance=None,
    )
    result = asyncio.run(_llm_event_handler(ctx, {
        "_harness_event_type": "extract",
        "data": {"alpha": "actual nonempty source"},
        "fields": ["conclusion", "source_path", "evidence_quote"],
    }))
    assert result["ok"] is False
    assert "omitted required grounded evidence" in result["error"]


def test_extract_canonicalizes_model_source_alias_to_runtime_path(tmp_path):
    source_path = str(tmp_path / "source.md")

    class Adapter:
        def chat(self, prompt, purpose=""):
            return json.dumps({
                "alpha": {
                    "conclusion": "grounded",
                    "source_path": "source.md",
                    "evidence_quote": "exact source quote",
                },
            })

    ctx = SimpleNamespace(
        adapter=Adapter(), event=SimpleNamespace(type=SimpleNamespace(value="outer")),
        title="test", state_md="", artifact_path=str(tmp_path / "result.md"),
        build_action_prompt=None, parse_structured_response=None,
        robust_executor=None, task_instance=None,
    )
    result = asyncio.run(_llm_event_handler(ctx, {
        "_harness_event_type": "extract",
        "data": {"alpha": "before exact source quote after"},
        "source_paths": {"alpha": source_path},
        "fields": ["conclusion", "source_path", "evidence_quote"],
    }))
    assert result["ok"] is True
    assert result["parsed"]["alpha"]["source_path"] == source_path
    assert source_path in result["content"]


def test_extract_rejects_non_substantive_report_field(tmp_path):
    class Adapter:
        def chat(self, prompt, purpose=""):
            return json.dumps({"full_markdown_report": "not_found"})

    ctx = SimpleNamespace(
        adapter=Adapter(), event=SimpleNamespace(type=SimpleNamespace(value="outer")),
        title="test", state_md="", artifact_path=str(tmp_path / "result.md"),
        build_action_prompt=None, parse_structured_response=None,
        robust_executor=None, task_instance=None,
    )
    result = asyncio.run(_llm_event_handler(ctx, {
        "_harness_event_type": "extract", "data": "actual source facts",
        "fields": ["full_markdown_report"],
    }))
    assert result["ok"] is False
    assert "no substantive report" in result["error"]


# ── Hermes 2026-08-27 Bug fix regression tests ─────────────────────────────────
# Bug: 03 任务 1/3 "只读诊断" 触发的 plan 是 atomic_inspect_file + atomic_write_artifact
# with static content，被 preflight 拒绝 3 次。Codex 8/27 的 candidate_contract 修复
# 只对标记 candidate 的任务生效，普通 manual_stable 任务仍会撞同样问题。
# Fix: 普通任务 prompt 加 "read → generate_text → writer" 三步拓扑；preflight 放宽
# "evidence-dependent output must reference a dependency result" 规则——当 content
# 长度 ≥ 100 且不含已知占位关键词时放行；当用户消息明确只读时允许 zero-write plan。


def test_manual_stable_environment_contract_requires_three_step_topology_for_unmarked_tasks(tmp_path):
    """普通 manual_stable 任务的 planner prompt 必须包含 read → generate_text → writer 拓扑。"""
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state/tasks/three-step"
    ordinary_message = "请读取 partner/mind/harness.py 并写一份诊断报告"
    contract = _manual_environment_contract(str(instance), str(task_dir), ordinary_message)
    assert "read → generate_text → writer" in contract or "read" in contract and "generate_text" in contract and "writer" in contract
    assert "[manual_stable 通用]" in contract
    # 用户消息含 "诊断" 关键词，应当允许 zero-write
    assert "只读" in contract


def test_manual_preflight_allows_long_substantive_content_without_dependency_reference(tmp_path):
    """长度 ≥ 100 且不含占位关键词的 content 应该通过 preflight，不必强制 $ 引用。"""
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state/tasks/long-content"
    task_dir.mkdir(parents=True)
    source = task_dir / "target_module.py"
    source.write_text("# reference text\n" * 5, encoding="utf-8")
    registry = _manual_registry()
    # 注意：不要用任何会触发 "placeholder" / "待补充" / "TODO" 等关键词的措辞，
    # 因为这些是 _is_placeholder_content 的硬门词。也不要连续用 ":\\n"，
    # 否则会触发 empty_fields ≥ 2 的另一条硬门。
    substantive = (
        "本诊断报告基于 partner/mind/harness.py 第 3147 行附近函数源码的真实分析。"
        "该函数对 .md 与 .py 文件用固定长度阈值判定占位，"
        "短于阈值但合法的真实摘要会被误判。改进方向包括改为可配置阈值或基于内容类型判断，"
        "并把诊断结果以自然语言形式呈现给用户。当前测试覆盖 200 字以上的合法分析报告应当通过。"
    )  # 长度约 210 字，无占位关键词
    plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": str(source)}, []),
        HarnessStep("write", "atomic_write_artifact", {
            "path": str(task_dir / "diagnosis.md"),
            "content": substantive,
        }, ["read"]),
    ], expected_artifacts=[])
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
    )
    assert [step for step in checked.plan if step.id == "write"], "write step must remain in normalized plan"
    write_step = next(step for step in checked.plan if step.id == "write")
    # content 应该被保留（不是被改写为 $ 引用）
    assert write_step.parameters.get("content") == substantive


def test_manual_preflight_still_rejects_short_placeholder_content(tmp_path):
    """< 100 字 或 含占位关键词 的 content 仍然被拒绝。"""
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state/tasks/short-placeholder"
    task_dir.mkdir(parents=True)
    source = task_dir / "harness.py"
    source.write_text("x", encoding="utf-8")
    registry = _manual_registry()
    short_plan = MicroPlan(plan=[
        HarnessStep("read", "atomic_inspect_file", {"path": str(source)}, []),
        HarnessStep("write_short", "atomic_write_artifact", {
            "path": str(task_dir / "short.md"),
            "content": "Output product 1",  # 47 字符 + 触发 placeholder 关键词
        }, ["read"]),
    ], expected_artifacts=[])
    with pytest.raises(ValueError) as exc:
        _manual_preflight_plan(
            short_plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
        )
    assert "placeholder" in str(exc.value).lower() or "evidence-dependent" in str(exc.value).lower()


def test_manual_preflight_allows_zero_write_plan_for_read_only_user_message(tmp_path):
    """用户消息明确只读时，允许 plan 里没有 create_file / atomic_write_artifact。"""
    _, instance = _workspace(tmp_path)
    task_dir = instance / "state/tasks/read-only"
    task_dir.mkdir(parents=True)
    source = task_dir / "harness.py"
    source.write_text("# placeholder\n" * 5, encoding="utf-8")
    registry = _manual_registry()
    user_message = "[manual_stable 任务 1/3：03 只读诊断] 请只读 partner/mind/harness.py 中 _is_placeholder_content 函数，不要修改任何代码"
    plan = MicroPlan(plan=[
        HarnessStep("read1", "atomic_inspect_file", {"path": str(source)}, []),
    ], expected_artifacts=[])
    # 不应当 raise ValueError；plan 应当被原样保留
    checked = _manual_preflight_plan(
        plan, registry=registry, workspace=str(instance), working_dir=str(task_dir),
        user_message=user_message,
    )
    assert [step for step in checked.plan if step.id == "read1"]
