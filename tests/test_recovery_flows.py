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
from partner.v2 import repair_events
from partner.v2 import molecular_events
from partner.v2 import molecular_diversity_events
from partner.v2 import molecular_iteration_events
from partner.v2 import push_events
from partner.v2.push_events import atomic_push_files
from partner.mind.event_types import EventType, MindEvent
from partner.mind.harness import (
    EventRegistry, HarnessContext, HarnessEventSpec, HarnessStep,
    _fallback_user_progress_text, _resolve_runtime_value, _validate_plan_against_registry,
)


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
