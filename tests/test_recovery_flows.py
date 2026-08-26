from types import SimpleNamespace
import pytest

import partner.core  # initialize core before mind to avoid the package's known import cycle
from partner.__main__ import validate_pdf
from partner.v2.browser import _unit_name
from partner.v2.browser import atomic_browser_open
from partner.v2.browser import atomic_browser_screenshot
from partner.v2.browser import atomic_xhs_inspect_upload_requirements
from partner.v2.browser import atomic_xhs_open_publish_editor
from partner.v2.pdf_events import atomic_generate_pdf, atomic_generate_detailed_pdf
from partner.projects.project_state import update_project_brief_from_contract
from partner.v2 import repair_events
from partner.v2 import molecular_events
from partner.v2 import molecular_diversity_events
from partner.v2 import molecular_iteration_events
from partner.v2 import push_events
from partner.v2.push_events import atomic_push_files
from partner.mind.event_types import EventType, MindEvent
from partner.mind.harness import (
    EventRegistry, HarnessContext, HarnessEventSpec, HarnessStep, _agent_event_handler,
    _clean_generated_python, _fallback_user_progress_text, _local_create_file,
    _referenced_failed_dependencies, _resolve_runtime_value, _validate_plan_against_registry,
)
from partner.mind.executor import _campaign_code_fallback_micro_plan, _required_output_exts


def test_generated_python_rejects_tool_call_transcript():
    code, error = _clean_generated_python(
        '[hermes]\n<think>I should inspect files.</think>\n]<]minimax[>[<tool_call>'
    )
    assert not code
    assert "tool-call transcript" in error


def test_generated_python_extracts_fenced_code():
    code, error = _clean_generated_python(
        "Here is the script:\n```python\nimport json\nprint(json.dumps({'ok': True}))\n```"
    )
    assert not error
    assert code.startswith("import json")


def test_create_file_rejects_invalid_python(tmp_path):
    ctx = SimpleNamespace(
        task_instance=SimpleNamespace(working_dir=str(tmp_path)),
        working_dir=str(tmp_path), project_dir=str(tmp_path),
    )
    result = _local_create_file(ctx, {"path": "bad.py", "content": "]<]minimax[>[<tool_call>"})
    assert not result["ok"]
    assert not (tmp_path / "bad.py").exists()


def test_required_output_exts_handles_chinese_enumeration_separator():
    required = _required_output_exts(
        "生成 result.json、analysis.md、详细 analysis.pdf，并保存 analyzer.py。"
    )
    assert {".json", ".md", ".pdf", ".py"} <= required


def test_plan_routes_python_source_generation_to_generate_code():
    registry = EventRegistry()
    registry.register(HarnessEventSpec("generate_code", "atomic", "", lambda *_: {}))
    registry.register(HarnessEventSpec("create_file", "atomic", "", lambda *_: {}))
    plan = [
        HarnessStep("make", "smart_llm_structured_action", {"prompt": "Generate complete Python code"}, []),
        HarnessStep("save", "create_file", {"path": "analysis.py", "content": "$make.result.content"}, ["make"]),
    ]
    checked = _validate_plan_against_registry(registry, plan, "unit")
    assert checked[0].event_type == "generate_code"
    assert checked[0].parameters["language"] == "python"


def test_agent_handler_uses_concrete_step_type_for_code_gate(tmp_path, monkeypatch):
    import asyncio
    from partner.skills import external_agent_skills

    async def fake_execute_agent_task(**kwargs):
        return SimpleNamespace(
            ok=True, output={"content": "[hermes]\n<think>inspect</think>\n]<]minimax[>[<tool_call>"}, error="",
        )

    monkeypatch.setattr(external_agent_skills, "execute_agent_task", fake_execute_agent_task)
    ctx = SimpleNamespace(
        event=SimpleNamespace(type="campaign_batch_plan"), workspace=str(tmp_path),
        task_instance=None, progress_callback=None,
    )
    result = asyncio.run(_agent_event_handler(ctx, {
        "_harness_event_type": "generate_code", "agent": "hermes", "task": "write Python code",
    }))
    assert result["ok"] is False
    assert "tool-call transcript" in result["error"]


def test_generate_text_forwards_prompt_and_resolved_data_and_strips_reasoning(tmp_path, monkeypatch):
    import asyncio
    from partner.skills import external_agent_skills

    captured = {}

    async def fake_execute_agent_task(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            ok=True,
            output={"content": "[hermes]\n<think>private reasoning</think>\n# Grounded report\n" + "evidence " * 30},
            error="",
        )

    monkeypatch.setattr(external_agent_skills, "execute_agent_task", fake_execute_agent_task)
    ctx = SimpleNamespace(
        event=SimpleNamespace(type="generate_text"), workspace=str(tmp_path), title="test",
        task_instance=None, progress_callback=None,
    )
    result = asyncio.run(_agent_event_handler(ctx, {
        "_harness_event_type": "generate_text", "agent": "hermes",
        "task": "compose report", "prompt": "include exact quotes",
        "data": {"alpha": {"evidence_quote": "actual quote"}},
    }))
    assert "include exact quotes" in captured["task"]
    assert "actual quote" in captured["task"]
    assert "<think>" not in result["content"]
    assert result["content"].startswith("# Grounded report")


def test_generate_text_retries_action_promise_and_requires_finished_artifact(tmp_path, monkeypatch):
    import asyncio
    from partner.skills import external_agent_skills

    calls = []

    async def fake_execute_agent_task(**kwargs):
        calls.append(kwargs["task"])
        if len(calls) == 1:
            content = "[hermes]\n我将根据三个来源撰写报告，并写入指定文件。"
        else:
            content = "[hermes]\n# 完整报告\n\n" + "真实证据与分析。" * 60
        return SimpleNamespace(ok=True, output={"content": content}, error="")

    monkeypatch.setattr(external_agent_skills, "execute_agent_task", fake_execute_agent_task)
    ctx = SimpleNamespace(
        event=SimpleNamespace(type="generate_text"), workspace=str(tmp_path), title="test",
        task_instance=None, progress_callback=None,
    )
    result = asyncio.run(_agent_event_handler(ctx, {
        "_harness_event_type": "generate_text", "agent": "hermes",
        "task": "compose report", "prompt": "生成不少于100字的完整正文",
        "data": {"source": "verified"},
    }))
    assert result.get("ok") is not False
    assert len(calls) == 2
    assert "纠偏" in calls[1]
    assert result["content"].startswith("# 完整报告")


def test_general_agent_allows_timeout_word_inside_substantive_report(tmp_path, monkeypatch):
    import asyncio
    from partner.skills import external_agent_skills

    report = "# Report\n\nTimeout handling is one bounded recovery principle.\n" + "evidence\n" * 80
    adapter = SimpleNamespace(chat=lambda *args, **kwargs: report)
    monkeypatch.setattr(external_agent_skills, "_make_adapter", lambda *args, **kwargs: adapter)
    result = asyncio.run(external_agent_skills.execute_agent_task(
        workspace=str(tmp_path), agent="hermes", task="write report", allow_web=False,
    ))
    assert result.ok is True
    assert "Timeout handling" in result.output["content"]


def test_generate_text_retries_false_file_capability_claim(tmp_path, monkeypatch):
    import asyncio
    from partner.skills import external_agent_skills

    calls = []

    async def fake_execute_agent_task(**kwargs):
        calls.append(kwargs["task"])
        content = (
            "完整分析正文。" * 40 + "本环境未配置文件写入工具，请提供文件写入能力。"
            if len(calls) == 1 else "# 完整且可交付的正文\n\n" + "证据分析。" * 80
        )
        return SimpleNamespace(ok=True, output={"content": content}, error="")

    monkeypatch.setattr(external_agent_skills, "execute_agent_task", fake_execute_agent_task)
    ctx = SimpleNamespace(
        event=SimpleNamespace(type="generate_text"), workspace=str(tmp_path), title="test",
        task_instance=None, progress_callback=None,
    )
    result = asyncio.run(_agent_event_handler(ctx, {
        "_harness_event_type": "generate_text", "agent": "hermes", "task": "compose report",
    }))
    assert result.get("ok") is not False
    assert len(calls) == 2
    assert "未配置文件写入工具" not in result["content"]


def test_generate_text_retries_file_mutation_verifier_and_missing_quote_pairs(tmp_path, monkeypatch):
    import asyncio
    import json
    from partner.skills import external_agent_skills

    calls = []

    async def fake_execute_agent_task(**kwargs):
        calls.append(kwargs["task"])
        if len(calls) == 1:
            content = (
                "任务完成。\n\nFile-mutation verifier: 1 file(s) were NOT modified this turn. "
                "Failed to write file: timed out. " + "状态包装。" * 180
            )
        else:
            content = (
                "# 完整报告\n\n"
                "source_path: /tmp/a.md\n"
                "evidence_quote: 这是来自第一个真实来源且长度超过二十字符的逐字连续引文内容。\n\n"
                "source_path: /tmp/b.md\n"
                "evidence_quote: 这是来自第二个真实来源且长度超过二十字符的逐字连续引文内容。\n\n"
                + "证据分析。" * 180
            )
        return SimpleNamespace(ok=True, output={"content": content}, error="")

    monkeypatch.setattr(external_agent_skills, "execute_agent_task", fake_execute_agent_task)
    ctx = SimpleNamespace(
        event=SimpleNamespace(type="generate_text"), workspace=str(tmp_path), title="test",
        task_instance=None, progress_callback=None,
    )
    result = asyncio.run(_agent_event_handler(ctx, {
        "_harness_event_type": "generate_text", "agent": "hermes",
        "task": "compose report", "prompt": "不少于800个中文字符；保留 source_path 与 evidence_quote 逐字引文",
        "data": {"verified_sources": json.dumps({"a": {}, "b": {}}, ensure_ascii=False)},
    }))
    assert result.get("ok") is not False
    assert len(calls) == 2
    assert result["content"].count("source_path:") == 2


def test_extract_rejects_robust_fallback_as_ungrounded(tmp_path):
    import asyncio
    from partner.mind.harness import _llm_event_handler

    class Robust:
        async def execute(self, **kwargs):
            return SimpleNamespace(
                status="fallback_success", value={"content": "fallback"},
                content_preview="fallback", fallback_path="fallback.md", ok=True,
            )

    ctx = SimpleNamespace(
        adapter=SimpleNamespace(), event=SimpleNamespace(type="extract"),
        title="test", state_md="", artifact_path=str(tmp_path / "result.md"),
        build_action_prompt=None, parse_structured_response=None,
        robust_executor=Robust(), task_instance=SimpleNamespace(),
    )
    result = asyncio.run(_llm_event_handler(ctx, {
        "_harness_event_type": "extract", "data": "real source",
    }))
    assert result["ok"] is False
    assert result["status"] == "fallback_rejected"


def test_direct_template_dependency_is_not_degradable():
    failed = _referenced_failed_dependencies(
        {"path": "analysis.py", "content": "$step_code.result.content"},
        ["step_code", "step_design"],
    )
    assert failed == ["step_code"]


def test_campaign_code_fallback_builds_bounded_execution_chain(tmp_path):
    plan = _campaign_code_fallback_micro_plan(
        "编写 analyze.py，生成 result.json、report.md 和 report.pdf", str(tmp_path)
    )
    assert plan is not None
    assert [step.event_type for step in plan.plan] == [
        "generate_code", "create_file", "run_command", "list_directory", "send_user_text", "push_files",
    ]
    assert {item["pattern"] for item in plan.expected_artifacts} == {
        "analyze.py", "result.json", "report.md", "report.pdf",
    }


def _ctx(workdir, instance="01"):
    return SimpleNamespace(
        workspace=str(workdir.parent.parent / "instances" / instance),
        task_instance=SimpleNamespace(working_dir=str(workdir)),
    )


def test_push_requires_real_channel_ack(tmp_path, monkeypatch):
    image = tmp_path / "login.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 128)
    ctx = _ctx(tmp_path)

    monkeypatch.setattr(push_events, "_deliver_one", lambda path, caption: {
        "ok": False, "delivered": False, "status": "failed", "path": path,
    })
    failed = atomic_push_files(ctx, {"source": str(image)})
    assert failed["ok"] is False
    assert failed["pushed"] == 0
    assert failed["status"] == "failed"

    monkeypatch.setattr(push_events, "_deliver_one", lambda path, caption: {
        "ok": True, "delivered": True, "status": "sent", "path": path,
    })
    sent = atomic_push_files(ctx, {"source": str(image)})
    assert sent["ok"] is True
    assert sent["pushed"] == 1
    assert sent["status"] == "sent"


def test_push_auto_discovery_is_current_task_only(tmp_path, monkeypatch):
    current = tmp_path / "current"
    old = tmp_path / "old"
    current.mkdir()
    old.mkdir()
    (old / "login_old.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 128)
    result = atomic_push_files(_ctx(current), {})
    assert result["ok"] is False
    assert result["status"] == "missing"


def test_push_auto_discovers_xhs_visual_in_current_task(tmp_path, monkeypatch):
    image = tmp_path / "xhs_editor.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 128)
    monkeypatch.setattr(push_events, "_deliver_one", lambda path, caption: {
        "ok": True, "delivered": True, "status": "sent", "path": path,
    })
    result = atomic_push_files(_ctx(tmp_path), {})
    assert result["ok"] is True
    assert result["source"] == str(image)


def test_runtime_workdir_is_not_treated_as_step_reference(tmp_path):
    event = MindEvent(type=EventType.DIRECT_TASK, payload={})
    ctx = HarnessContext(
        workspace=str(tmp_path), event=event, title="test", project_dir=str(tmp_path),
        state_md="", artifact_path="", task_instance=SimpleNamespace(working_dir=str(tmp_path)),
    )
    value = _resolve_runtime_value({"path": "$workdir/xhs_round_summary.md"}, ctx)
    assert value["path"] == str(tmp_path / "xhs_round_summary.md")


def test_missing_summary_reference_gets_factual_progress_message(tmp_path):
    event = MindEvent(type=EventType.DIRECT_TASK, payload={})
    ctx = HarnessContext(
        workspace=str(tmp_path), event=event, title="真实试运行", project_dir=str(tmp_path),
        state_md="", artifact_path="", task_instance=SimpleNamespace(working_dir=str(tmp_path)),
    )
    step = HarnessStep("notify", "atomic_send_user_text", {}, ["inspect"])
    text = _fallback_user_progress_text(ctx, step, {
        "inspect": {"ok": True, "event_type": "atomic_browser_screenshot", "status": "ok", "files": [str(tmp_path / "xhs.png")]},
    })
    assert "atomic_browser_screenshot" in text
    assert "xhs.png" in text
    assert "最终验收" in text


def test_reflection_write_file_alias_is_normalized():
    registry = EventRegistry()
    registry.register(HarnessEventSpec(
        name="atomic_write_artifact", kind="atomic", description="write",
        handler=lambda _ctx, _params: {"ok": True}, produces_artifact=True,
    ))
    plan = _validate_plan_against_registry(
        registry, [HarnessStep("write", "atomic_write_file", {"path": "report.md"}, [])], "test",
    )
    assert plan[0].event_type == "atomic_write_artifact"


def test_unresolved_angle_placeholder_step_is_rejected_before_execution():
    registry = EventRegistry()
    registry.register(HarnessEventSpec(
        name="atomic_read_state", kind="atomic", description="read",
        handler=lambda _ctx, _params: {"ok": True},
    ))
    with pytest.raises(ValueError, match="all steps"):
        _validate_plan_against_registry(
            registry,
            [HarnessStep("read", "atomic_read_state", {"path": "<resolved project state or latest receipt>"}, [])],
            "campaign",
        )


def test_pdf_is_unicode_report_not_minimal_fallback(tmp_path):
    out = tmp_path / "report.pdf"
    content = "# 分子生成报告\n\n这是中文正文。\n\n| 分子 | 数值 |\n| --- | --- |\n| 阿司匹林 | 1.23 |\n" * 8
    result = atomic_generate_pdf(
        _ctx(tmp_path),
        {"title": "真实执行报告", "content": content, "output_path": str(out), "auto_collect_images": False},
    )
    assert result["ok"] is True, result
    assert result.get("minimal") is not True
    assert out.stat().st_size > 1024
    assert validate_pdf(str(out))[0] is True


def test_detailed_pdf_rejects_brief_summary(tmp_path):
    result = atomic_generate_detailed_pdf(
        _ctx(tmp_path),
        {"content": "# 简报\n\n只有一句结论。", "output_path": str(tmp_path / "brief.pdf"), "auto_collect_images": False},
    )
    assert result["ok"] is False
    assert result["status"] == "content_quality_failed"
    assert result["retryable"] is False
    assert result["quality"]["plain_chars"] < 1200


def test_send_user_text_requires_real_ack(monkeypatch):
    monkeypatch.setattr(repair_events, "_deliver_text", lambda text: {
        "ok": False, "delivered": False, "status": "failed", "error": "no ack",
    })
    failed = repair_events.atomic_send_user_text(SimpleNamespace(), {"text": "请登录"})
    assert failed["ok"] is False
    assert failed["delivered"] is False


def test_foreground_login_requires_browser_and_notification_ack(monkeypatch):
    monkeypatch.setattr("partner.v2.browser.atomic_browser_open", lambda ctx, params: {"ok": True, "status": "ok"})
    monkeypatch.setattr(repair_events, "atomic_send_user_text", lambda ctx, params: {
        "ok": True, "delivered": True, "status": "sent",
    })
    result = repair_events.atomic_open_browser_foreground_and_notify(
        SimpleNamespace(), {"url": "https://example.com/login"},
    )
    assert result["ok"] is True
    assert result["visible"] is True
    assert result["kept_open"] is True
    assert result["notified"] is True


def test_verify_login_enqueues_concrete_continuation(tmp_path, monkeypatch):
    workspace = tmp_path / "instances" / "01"
    (workspace / "state").mkdir(parents=True)
    monkeypatch.setattr("partner.v2.browser.atomic_browser_open", lambda ctx, params: {
        "ok": True, "status": "ok", "url": params["url"], "title": "小红书",
    })
    monkeypatch.setattr("partner.v2.browser.atomic_browser_execute", lambda ctx, params: {
        "ok": True,
        "status": "ok",
        "result": {
            "url": "https://www.xiaohongshu.com/explore",
            "title": "小红书 - 你的生活兴趣社区",
            "body": "首页 发布 通知 消息 我",
            "cookieLength": 128,
        },
    })
    monkeypatch.setattr(repair_events, "_deliver_text", lambda text: {
        "ok": True, "delivered": True, "status": "sent",
    })
    result = repair_events.atomic_verify_login_and_continue(
        SimpleNamespace(workspace=str(workspace)), {},
    )
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["next_task_queued"] is True
    assert (workspace / "state" / "login_session.json").exists()
    inbox = (workspace / "state" / "desktop_inbox.jsonl").read_text()
    assert "登录后自动继续" in inbox
    assert "不点击最终发布" in inbox


def test_verify_login_rejects_user_claim_without_page_evidence(tmp_path, monkeypatch):
    workspace = tmp_path / "instances" / "01"
    (workspace / "state").mkdir(parents=True)
    monkeypatch.setattr("partner.v2.browser.atomic_browser_open", lambda ctx, params: {"ok": True, "status": "ok"})
    monkeypatch.setattr("partner.v2.browser.atomic_browser_execute", lambda ctx, params: {
        "ok": True, "status": "ok", "result": {"body": "手机号登录 扫码登录", "cookieLength": 0},
    })
    monkeypatch.setattr(repair_events, "_deliver_text", lambda text: {
        "ok": True, "delivered": True, "status": "sent",
    })
    result = repair_events.atomic_verify_login_and_continue(SimpleNamespace(workspace=str(workspace)), {})
    assert result["ok"] is False
    assert result["status"] == "login_not_verified"
    assert not (workspace / "state" / "desktop_inbox.jsonl").exists()


def test_molecular_benchmark_creates_real_evidence_and_requires_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(molecular_events, "_push_file", lambda path, caption: {
        "ok": True, "delivered": True, "status": "sent", "path": path,
    })
    monkeypatch.setattr(molecular_events, "_push_text", lambda text: {
        "ok": True, "delivered": True, "status": "sent",
    })
    result = molecular_events.atomic_molecular_generation_benchmark(_ctx(tmp_path, "02"), {"deliver": True})
    assert result["ok"] is True
    assert result["metrics"]["valid_count"] >= 50
    assert result["quality"]["plain_chars"] >= 1200
    assert result["quality"]["section_count"] >= 7
    for name in (
        "molecular_candidates.csv", "molecular_metrics.json",
        "molecular_qed_distribution.png", "molecular_generation_report.md",
        "molecular_generation_report.pdf",
    ):
        assert (tmp_path / name).exists()


def test_molecular_diversity_is_incremental_real_experiment(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    base = molecular_events.atomic_molecular_generation_benchmark(_ctx(first, "02"), {})
    assert base["ok"] is True
    result = molecular_diversity_events.atomic_molecular_diversity_benchmark(
        _ctx(second, "02"), {"source": str(first / "molecular_candidates.csv")},
    )
    assert result["ok"] is True, result
    assert result["metrics"]["molecule_count"] >= 50
    assert result["metrics"]["pair_count"] > 1000
    assert result["quality"]["plain_chars"] >= 1200
    assert (second / "molecular_diversity_report.pdf").stat().st_size > 10000


def test_molecular_written_next_steps_are_executed_as_real_rounds(tmp_path):
    first, third, fourth = tmp_path / "first", tmp_path / "third", tmp_path / "fourth"
    first.mkdir(); third.mkdir(); fourth.mkdir()
    base = molecular_events.atomic_molecular_generation_benchmark(_ctx(first, "02"), {})
    assert base["ok"] is True
    synth = molecular_iteration_events.atomic_molecular_synth_baseline_benchmark(
        _ctx(third, "02"), {"source": str(first / "molecular_candidates.csv")},
    )
    assert synth["ok"] is True, synth
    assert synth["metrics"]["rule"]["count"] == synth["metrics"]["stochastic"]["count"]
    optimized = molecular_iteration_events.atomic_molecular_goal_optimization_benchmark(
        _ctx(fourth, "02"), {"source": str(third / "molecular_synth_comparison.csv")},
    )
    assert optimized["ok"] is True, optimized
    assert optimized["metrics"]["selected_count"] == 20
    assert (fourth / "molecular_optimized_candidates.csv").exists()




def test_browser_unit_is_deterministic_per_instance(tmp_path):
    ctx = SimpleNamespace(workspace="/tmp/partner_workspace/instances/01")
    assert _unit_name(ctx) == "partner-browser-01"


def test_headless_false_maps_to_visible(monkeypatch):
    captured = {}
    def fake_run(ctx, action, params, *, visible=None):
        captured.update(action=action, params=params, visible=visible)
        return {"status": "ok"}
    monkeypatch.setattr("partner.v2.browser._run_worker", fake_run)
    result = atomic_browser_open(SimpleNamespace(), {"url": "data:text/html,<h1>ok</h1>", "headless": False})
    assert result["ok"] is True
    assert captured["visible"] is True
    assert captured["params"]["bring_to_front"] is True


def test_screenshot_uses_exact_expected_artifact(tmp_path, monkeypatch):
    captured = {}
    task = SimpleNamespace(
        working_dir=str(tmp_path),
        expected_artifacts=[{"type": "file", "pattern": "browser_acceptance.png", "required": True}],
    )
    def fake_run(ctx, action, params, *, visible=None):
        captured.update(params)
        return {"status": "ok", "path": params["save_path"]}
    monkeypatch.setattr("partner.v2.browser._run_worker", fake_run)
    result = atomic_browser_screenshot(SimpleNamespace(task_instance=task), {})
    assert result["path"] == str(tmp_path / "browser_acceptance.png")


def test_screenshot_honors_filename_in_task_dir(tmp_path, monkeypatch):
    captured = {}
    task = SimpleNamespace(working_dir=str(tmp_path), expected_artifacts=[])
    def fake_run(ctx, action, params, *, visible=None):
        captured.update(params)
        return {"status": "ok", "path": params["save_path"]}
    monkeypatch.setattr("partner.v2.browser._run_worker", fake_run)
    result = atomic_browser_screenshot(
        SimpleNamespace(task_instance=task), {"filename": "xiaohongshu_publish_entry.png"},
    )
    assert result["path"] == str(tmp_path / "xiaohongshu_publish_entry.png")


def test_xhs_publish_editor_is_one_verified_transaction(tmp_path, monkeypatch):
    calls = []

    def fake_run(ctx, action, params, *, visible=None):
        calls.append(action)
        if action == "open":
            return {"status": "ok", "url": params["url"], "title": "小红书创作服务平台"}
        if action == "execute" and len([x for x in calls if x == "execute"]) == 1:
            return {"status": "ok", "result": {"clicked": True, "candidates": 2}}
        if action == "execute":
            return {"status": "ok", "result": {
                "has_upload_tab": True, "has_image_prompt": True,
                "file_input_count": 1, "editor_field_count": 0,
            }}
        if action == "screenshot":
            return {"status": "ok", "path": params["save_path"]}
        raise AssertionError(action)

    monkeypatch.setattr("partner.v2.browser._run_worker", fake_run)
    monkeypatch.setattr("partner.v2.browser._time_mod.sleep", lambda _: None)
    monkeypatch.setattr("partner.v2.vision_events.read_image_with_qwen", lambda *args, **kwargs: {
        "ok": True, "model": "vision-test", "description": "真实发布页面截图",
    })
    monkeypatch.setattr("partner.v2.browser._push_visual_file", lambda *args, **kwargs: {
        "ok": True, "delivered": True, "status": "sent",
    })
    monkeypatch.setattr("partner.v2.browser._push_visual_text", lambda *args, **kwargs: {
        "ok": True, "delivered": True, "status": "sent",
    })
    result = atomic_xhs_open_publish_editor(_ctx(tmp_path), {})
    assert result["ok"] is True
    assert result["status"] == "editor_entry_verified"
    assert calls == ["open", "screenshot", "execute", "execute", "screenshot", "screenshot"]
    assert len(result["visual_steps"]) == 2
    assert (tmp_path / "xiaohongshu_publish_editor_evidence.json").exists()


def test_xhs_upload_audit_preserves_all_three_visual_model_receipts(tmp_path, monkeypatch):
    execute_calls = 0

    def fake_run(ctx, action, params, visible=False):
        nonlocal execute_calls
        if action == "open":
            return {"status": "ok"}
        if action == "screenshot":
            return {"status": "ok", "path": params["save_path"]}
        if action == "execute":
            execute_calls += 1
            if execute_calls == 1:
                return {"status": "ok", "result": {"clicked": True, "candidates": 1}}
            if execute_calls == 2:
                return {"status": "ok", "result": {
                    "has_upload_tab": True, "has_image_prompt": True,
                    "file_input_count": 1,
                }}
            return {"status": "ok", "result": {
                "inputs": [{"accept": ".png", "multiple": True}],
                "requirement_lines": ["最大32MB"],
            }}
        raise AssertionError(action)

    monkeypatch.setattr("partner.v2.browser._run_worker", fake_run)
    monkeypatch.setattr("partner.v2.browser._time_mod.sleep", lambda _: None)
    monkeypatch.setattr("partner.v2.browser._visual_step", lambda ctx, label, filename: {
        "ok": True, "path": str(tmp_path / filename),
        "vision": {"ok": True, "model": "qwen3-vl-flash", "description": label},
    })
    result = atomic_xhs_inspect_upload_requirements(_ctx(tmp_path), {})
    assert result["ok"] is True
    assert len(result["visual_steps"]) == 3
    assert result["model_calls"] == 3
    assert len([step for step in result["visual_steps"] if step["vision"]["model"]]) == 3
def test_project_brief_tolerates_legacy_string_user_correction(tmp_path):
    project = "molecular_generation"
    project_dir = tmp_path / "share" / "projects" / project
    project_dir.mkdir(parents=True)
    (project_dir / "project_brief.md").write_text(
        f"# {project} 项目简报\n\n## 项目目标\n待补充。\n\n## 当前主线\n待补充。\n",
        encoding="utf-8",
    )
    update_project_brief_from_contract(str(tmp_path), project, {
        "current_goal": "建立防泄漏基线",
        "current_mainline": "先完成一个项目",
        "allowed_scope": ["TargetDiff"],
        "forbidden_scope": ["身份回归"],
        "user_corrections": ["旧格式字符串也不能让消息路由崩溃"],
    })
    brief = (project_dir / "project_brief.md").read_text(encoding="utf-8")
    assert "建立防泄漏基线" in brief
