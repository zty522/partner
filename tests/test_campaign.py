import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from partner.governance.campaign import (
    _artifact_semantic_problems, _progress_signature, _requested_named_artifacts, build_campaign_report, campaign_instruction, campaign_snapshot, complete_campaign_work,
    cancel_campaign, create_campaign, enqueue_work_item, materialize_evolution_work,
    materialize_portfolio_work, materialize_project_actions, materialize_targetdiff_continuous_work,
    seed_default_work, seed_execution_work, seed_targetdiff_continuous_work,
    seed_portfolio_work, seed_targetdiff_project_work, tick_campaign,
)
from partner.governance.evolution_loop import record_issue
from partner.governance.campaign_models import CampaignBudget, CampaignState, WorkItem
from partner.governance.campaign_runtime import dispatch_to_instance, runtime_instance_ready
from partner.governance.campaign_storage import (
    list_leases, list_work_items, load_campaign, load_work_item, save_campaign,
    save_lease, save_work_item, set_active_campaign,
)
from partner.governance.project_loop import enqueue_next_action, record_iteration, request_next_action
from partner.governance.storage import latest_receipt, load_project_state


def _root(tmp_path):
    root = tmp_path / "workspace"
    for instance in ("01", "02", "03", "04", "05"):
        (root / "instances" / instance / "state" / "tasks").mkdir(parents=True)
    return str(root)


def _campaign(root, instances=None, duration=3600, max_items=20):
    return create_campaign(
        root,
        goal="overnight governance test",
        allowed_instances=instances or ["01", "02", "03"],
        duration_seconds=duration,
        report_interval_seconds=3600,
        budget=CampaignBudget(max_work_items=max_items, max_runtime_seconds=duration),
    )


def test_campaign_contract_rejects_more_than_two_active(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root)
    state.active_instances = ["01", "02", "03"]
    with pytest.raises(ValueError):
        state.to_dict()
    with pytest.raises(ValueError, match="unfinished campaign"):
        _campaign(root)


def test_seed_and_tick_dispatch_at_most_two_and_is_idempotent(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root)
    seed_default_work(root, state.campaign_id)
    calls = []
    switches = []

    def dispatch(item, instruction):
        calls.append((item.instance_id, item.work_item_id, instruction))
        return f"task-{item.work_item_id}"

    first = tick_campaign(root, state.campaign_id, dispatch=dispatch, switch_slots=switches.append)
    assert len(first["dispatched"]) == 2
    assert len({row["instance_id"] for row in first["dispatched"]}) == 2
    assert switches and len(switches[-1]) <= 2
    second = tick_campaign(root, state.campaign_id, dispatch=dispatch, switch_slots=switches.append)
    assert second["dispatched"] == []
    assert len(calls) == 2
    assert all("[PARTNER_CAMPAIGN " in instruction for _, _, instruction in calls)
    assert all("Campaign 总目标：overnight governance test" in instruction for _, _, instruction in calls)
    assert any("xiaohongshu_inspect_upload_requirements" in instruction for _, _, instruction in calls)
    assert any("molecular_data_readiness_audit" in instruction for _, _, instruction in calls)


def test_human_required_work_is_not_dispatched(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "xiaohongshu_operations",
        "kind": "project_iteration", "title": "真实发布", "instruction": "发布内容",
        "autonomy": "human_required",
    })
    result = tick_campaign(root, state.campaign_id, dispatch=lambda *_: "must-not-run")
    assert result["dispatched"] == []
    assert load_work_item(root, state.campaign_id, item.work_item_id).status == "proposed"


def test_sensitive_instruction_defaults_to_human_gate(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "xiaohongshu_operations",
        "kind": "project_iteration", "title": "publish", "instruction": "现在真实发布这篇内容",
    })
    assert item.autonomy == "human_required"


def test_paused_campaign_does_not_dispatch(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    seed_default_work(root, state.campaign_id)
    state = load_campaign(root, state.campaign_id)
    state.status = "paused"
    save_campaign(root, state)
    result = tick_campaign(root, state.campaign_id, dispatch=lambda *_: "must-not-run")
    assert result["status"] == "paused" and result["dispatched"] == []


def test_delivery_work_waits_for_runtime_channel_readiness(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["03"])
    enqueue_work_item(root, state.campaign_id, {
        "instance_id": "03", "project_id": "p", "kind": "project_iteration",
        "title": "delivery", "instruction": "work", "requires_delivery": True,
    })
    calls = []
    waiting = tick_campaign(
        root, state.campaign_id, dispatch=lambda *_: calls.append("dispatch") or "task",
        runtime_ready=lambda _: False,
    )
    assert waiting["dispatched"] == [] and calls == []
    assert list_work_items(root, state.campaign_id)[0].status == "proposed"
    ready = tick_campaign(
        root, state.campaign_id, dispatch=lambda *_: calls.append("dispatch") or "task",
        runtime_ready=lambda _: True,
    )
    assert ready["dispatched"] and calls == ["dispatch"]


def test_runtime_delivery_readiness_uses_explicit_state_file(tmp_path):
    root = _root(tmp_path)
    state_file = Path(root) / "instances/03/state/qq_delivery_state.json"
    assert runtime_instance_ready(root, "03") is False
    state_file.write_text(json.dumps({"delivery_ready": False, "status": "starting"}), encoding="utf-8")
    assert runtime_instance_ready(root, "03") is False
    state_file.write_text(json.dumps({"delivery_ready": True, "status": "ready"}), encoding="utf-8")
    assert runtime_instance_ready(root, "03") is True


def test_superseded_terminal_controller_cannot_restore_stale_slots(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    state.status = "cancelled"
    state.restore_instances = ["01", "02"]
    save_campaign(root, state)
    set_active_campaign(root, "campaign_new_owner")
    switches = []
    result = tick_campaign(
        root, state.campaign_id, dispatch=lambda *_: "must-not-run",
        switch_slots=lambda ids: switches.append(ids),
    )
    assert result["status"] == "cancelled"
    assert switches == []


def test_blocked_empty_queue_releases_all_runtime_slots(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    switches = []
    result = tick_campaign(
        root, state.campaign_id, dispatch=lambda *_: "must-not-run",
        switch_slots=lambda ids: switches.append(list(ids)),
    )
    assert result["status"] == "blocked"
    assert result["active_instances"] == []
    assert switches == [[]]


def test_blocked_campaign_still_schedules_checkpoint_report(tmp_path):
    root = _root(tmp_path)
    state = create_campaign(
        root, goal="blocked visibility", allowed_instances=["01"],
        duration_seconds=3600, report_interval_seconds=60,
        budget=CampaignBudget(max_runtime_seconds=3600),
    )
    state.status = "blocked"
    state.last_report_at = (datetime.now(timezone.utc) - timedelta(seconds=65)).isoformat()
    save_campaign(root, state)
    result = tick_campaign(root, state.campaign_id, dispatch=lambda *_: "unused")
    reports = [item for item in list_work_items(root, state.campaign_id) if item.kind == "report"]
    assert result["status"] == "blocked"
    assert len(reports) == 1
    assert reports[0].title == "Campaign 定时进度摘要"


def test_final_report_separates_primary_blocks_from_report_chain_issues(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    primary = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "project_iteration",
        "title": "business", "instruction": "audit",
    })
    primary.status = "blocked"
    primary.blocked_reason = "waiting for user evidence"
    save_work_item(root, primary)
    report_item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "report",
        "title": "old report", "instruction": "send", "requires_artifact": False,
    })
    report_item.status = "blocked"
    report_item.blocked_reason = "legacy report failed"
    save_work_item(root, report_item)
    report, path = build_campaign_report(root, state.campaign_id, "final", "campaign deadline reached")
    assert "业务轮次已收口 1/1" in report.summary
    assert "报告链问题 1" in report.summary
    assert "campaign deadline reached" in report.summary
    assert "本报告送达回执成功后自动 completed" in report.summary
    assert "当前统计不含本报告自身" in path.read_text(encoding="utf-8")
    assert report.blocked_items == ["01:business — waiting for user evidence"]
    assert "legacy report failed" not in path.read_text(encoding="utf-8")


def test_repeated_report_delivery_signature_does_not_trigger_business_fuse(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    signature = _progress_signature(["campaign_report_delivery"], [])
    for index in range(2):
        old = enqueue_work_item(root, state.campaign_id, {
            "instance_id": "01", "project_id": "p", "kind": "report",
            "title": f"report-{index}", "instruction": "send", "requires_artifact": False,
        })
        old.status = "completed"
        old.task_id = f"old-report-{index}"
        old.evidence = [f"progress_signature={signature}"]
        save_work_item(root, old)
    current = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "report",
        "title": "final", "instruction": "send", "requires_artifact": False,
    })
    current.status = "queued"
    current.attempt = 1
    current.task_id = "report-task"
    save_work_item(root, current)
    task = Path(root) / "instances/01/state/tasks/report-task"
    task.mkdir(parents=True)
    (task / "task_instance.json").write_text(json.dumps({
        "task_id": "report-task", "user_message": campaign_instruction(current),
        "metadata": {"step_results": {"send": {"delivered": True}}},
    }), encoding="utf-8")
    (task / "task_log.jsonl").write_text(json.dumps({
        "event": "plan_executor_step_completed", "event_type": "campaign_report_delivery",
    }) + "\n", encoding="utf-8")
    result = complete_campaign_work(root, campaign_instruction(current), event_types=["campaign_report_delivery"])
    assert result["ok"] is True
    assert result["status"] == "completed"


def test_cancel_campaign_closes_work_and_leases(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    seed_default_work(root, state.campaign_id)
    tick_campaign(root, state.campaign_id, dispatch=lambda *_: "active-task")
    from partner.tasks.task_queue import Task, TaskQueue
    queue_path = Path(root) / "instances/01/state/task_queue.json"
    queue = TaskQueue(str(queue_path))
    runtime_task = Task(description=f"[PARTNER_CAMPAIGN campaign_id={state.campaign_id} work_item_id=x]")
    queue.add_task(runtime_task)
    cancel_campaign(root, state.campaign_id, "operator stop")
    assert all(item.status in {"completed", "blocked", "cancelled"}
               for item in list_work_items(root, state.campaign_id))
    assert all(lease.status != "active" for lease in list_leases(root, state.campaign_id))
    assert TaskQueue(str(queue_path)).tasks[0].status == "failed"


def test_runtime_dispatch_writes_unique_campaign_message(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "audit",
        "title": "audit", "instruction": "inspect", "requires_delivery": False,
    })
    task_id = dispatch_to_instance(root, item, campaign_instruction(item))
    row = json.loads((Path(root) / "instances/01/state/desktop_inbox.jsonl").read_text(encoding="utf-8"))
    assert row["message_id"] == task_id
    assert row["campaign_id"] == state.campaign_id
    assert row["work_item_id"] == item.work_item_id
    assert "[campaign_attempt=1] [transport_recovery=0]" in row["content"]

    item.attempt = 2
    retry_task_id = dispatch_to_instance(root, item, campaign_instruction(item))
    assert retry_task_id != task_id
    rows = [json.loads(line) for line in (Path(root) / "instances/01/state/desktop_inbox.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len({entry["message_id"] for entry in rows}) == 2
    assert "[campaign_attempt=2] [transport_recovery=0]" in rows[-1]["content"]
    assert rows[0]["content"] != rows[-1]["content"]


def _write_delivery_task(root, item, delivered=True):
    task = Path(root) / "instances" / item.instance_id / "state" / "tasks" / "task-1"
    task.mkdir(parents=True, exist_ok=True)
    (task / "task_instance.json").write_text(json.dumps({
        "user_message": campaign_instruction(item),
        "metadata": {"step_results": {"send": {"ok": delivered, "delivered": delivered}}},
    }), encoding="utf-8")
    rows = [
        {"event": "campaign_progress_update", "phase": phase, "delivered": delivered, "ok": delivered}
        for phase in ("instruction_received", "started", "executed", "verified", "finished")
    ] + [{"event": "iteration_llm_check", "satisfied": True}]
    (task / "task_log.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8",
    )


def test_bounded_campaign_event_requires_three_user_progress_callbacks(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["03"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "03", "project_id": "partner_framework_frontend",
        "kind": "project_iteration", "title": "visible work",
        "instruction": "直接执行确定性事件 framework_campaign_contract_audit。",
        "requires_artifact": False, "requires_delivery": True,
    })
    item.status = "queued"; item.task_id = "task-visible"; item.attempt = 1
    save_work_item(root, item)
    instruction = campaign_instruction(item)
    assert "[user_progress_v2=true]" in instruction
    task = Path(root) / "instances/03/state/tasks/task-visible"
    task.mkdir(parents=True)
    (task / "task_instance.json").write_text(json.dumps({
        "task_id": "task-visible", "user_message": instruction,
        "metadata": {"step_results": {"send": {"delivered": True}}},
    }), encoding="utf-8")
    (task / "task_log.jsonl").write_text(
        json.dumps({"event": "iteration_llm_check", "satisfied": True}) + "\n",
        encoding="utf-8",
    )
    result = complete_campaign_work(root, instruction, event_types=["framework_campaign_contract_audit"])
    assert result["ok"] is False
    assert "required user progress callback missing: executed, finished, instruction_received, started, verified" in " ".join(result["work_item"]["evidence"])


def test_completion_requires_artifact_and_real_delivery(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "xiaohongshu_operations",
        "kind": "project_iteration", "title": "round", "instruction": "work",
    })
    item.status = "queued"
    item.task_id = "task-1"
    item.attempt = 1
    from partner.governance.campaign_storage import save_work_item
    save_work_item(root, item)
    failed = complete_campaign_work(root, campaign_instruction(item), files=[], event_types=["write"])
    assert failed["handled"] is True and failed["ok"] is False
    assert load_work_item(root, state.campaign_id, item.work_item_id).status == "failed"


def test_reconcile_detects_persisted_task_failure_without_waiting_for_lease(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "xiaohongshu_operations",
        "kind": "project_iteration", "title": "round", "instruction": "work",
    })
    tick_campaign(root, state.campaign_id, dispatch=lambda *_: "task-failed")
    task = Path(root) / "instances/01/state/tasks/task-failed"
    task.mkdir(parents=True)
    (task / "task_instance.json").write_text(json.dumps({
        "task_id": "task-failed", "user_message": campaign_instruction(item),
        "completion_status": "failed", "metadata": {},
    }), encoding="utf-8")
    (task / "task_log.jsonl").write_text(
        json.dumps({"event": "completion_status_updated", "status": "failed"}) + "\n",
        encoding="utf-8",
    )
    tick_campaign(root, state.campaign_id, dispatch=lambda *_: "retry-task")
    current = load_work_item(root, state.campaign_id, item.work_item_id)
    assert current.attempt == 2
    assert current.task_id == "retry-task"
    assert "task completion reported failure" in current.evidence


def test_queued_message_seen_without_task_instance_gets_transport_redispatch(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["05"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "05", "project_id": "agent_self_evolution", "kind": "audit",
        "title": "recover transport", "instruction": "audit",
        "requires_artifact": False, "requires_delivery": False,
    })
    first = tick_campaign(root, state.campaign_id, dispatch=lambda *_: "first-message")
    assert first["dispatched"]
    current = load_work_item(root, state.campaign_id, item.work_item_id)
    current.updated_at = (datetime.now(timezone.utc) - timedelta(seconds=65)).isoformat()
    save_work_item(root, current)
    calls = []
    result = tick_campaign(
        root, state.campaign_id,
        dispatch=lambda candidate, _: calls.append(list(candidate.evidence)) or "recovery-message",
    )
    recovered = load_work_item(root, state.campaign_id, item.work_item_id)
    assert result["dispatched"][0]["task_id"] == "recovery-message"
    assert recovered.attempt == 1
    assert "transport_recovery=1" in recovered.evidence
    assert calls == [["transport_recovery=1"]]


def test_evidence_backed_business_block_is_terminal_and_records_receipt(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["02"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "02", "project_id": "molecular_generation",
        "kind": "project_iteration", "title": "data readiness", "instruction": "audit",
    })
    item.status = "queued"
    item.task_id = "task-blocked"
    item.attempt = 1
    save_work_item(root, item)
    task = Path(root) / "instances/02/state/tasks/task-blocked"
    task.mkdir(parents=True)
    artifact = task / "molecular_data_readiness_report.md"
    artifact.write_text("# evidence\n" + "data readiness " * 100, encoding="utf-8")
    (task / "task_instance.json").write_text(json.dumps({
        "task_id": "task-blocked", "user_message": campaign_instruction(item),
        "completion_status": "done",
        "metadata": {"step_results": {"send": {"delivered": True}}},
    }), encoding="utf-8")
    (task / "task_log.jsonl").write_text("\n".join([
        json.dumps({"event": "completion_status_updated", "status": "done"}),
        json.dumps({"event": "iteration_llm_check", "satisfied": True}),
        json.dumps({"event": "campaign_work_blocked", "reason": "target data missing",
                    "resume_event": "molecular_target_data_available"}),
        json.dumps({"event": "campaign_model_usage_checkpoint", "planner_llm_calls": 2}),
        json.dumps({"event": "plan_executor_step_completed",
                    "event_type": "molecular_data_readiness_audit", "llm_calls": 3}),
    ]) + "\n", encoding="utf-8")
    result = complete_campaign_work(
        root, campaign_instruction(item), files=[str(artifact)],
        event_types=["molecular_data_readiness_audit"], success=True,
    )
    assert result["ok"] is True and result["status"] == "blocked"
    receipt = latest_receipt(root, "molecular_generation")
    assert receipt and receipt.stop_reason == "target data missing"
    project_state = load_project_state(root, "molecular_generation")
    assert project_state.status == "blocked"
    assert project_state.resume_event == "molecular_target_data_available"
    assert load_campaign(root, state.campaign_id).usage.model_calls == 5
    receipt_id = receipt.receipt_id
    usage_before = load_campaign(root, state.campaign_id).usage.work_items_completed
    duplicate = complete_campaign_work(
        root, campaign_instruction(item), files=[str(artifact)],
        event_types=["molecular_data_readiness_audit"], success=True,
    )
    assert duplicate["status"] == "already_blocked"
    assert latest_receipt(root, "molecular_generation").receipt_id == receipt_id
    assert load_campaign(root, state.campaign_id).usage.work_items_completed == usage_before


def test_completion_requires_explicitly_named_artifact(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "project_iteration",
        "title": "named output", "instruction": "必须新建 exact_report.md",
        "requires_delivery": False,
    })
    item.status = "queued"
    item.task_id = "task-named"
    item.attempt = 1
    save_work_item(root, item)
    wrong = Path(root) / "other_report.md"
    wrong.write_text("real but wrong artifact", encoding="utf-8")
    result = complete_campaign_work(root, campaign_instruction(item), files=[str(wrong)], event_types=["write"])
    assert result["ok"] is False
    assert "exact_report.md" in " ".join(result["work_item"]["evidence"])
    assert "business_progress=false" in result["work_item"]["evidence"]


def test_successful_completion_records_receipt_and_next_action(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["03"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "03", "project_id": "partner_framework_frontend",
        "kind": "project_iteration", "title": "framework round", "instruction": "work",
    })
    item.status = "queued"
    item.task_id = "task-1"
    item.attempt = 1
    from partner.governance.campaign_storage import save_work_item
    save_work_item(root, item)
    _write_delivery_task(root, item, delivered=True)
    artifact = Path(root) / "instances/03/state/tasks/task-1/report.md"
    artifact.write_text("evidence" * 100, encoding="utf-8")
    result = complete_campaign_work(
        root, campaign_instruction(item), files=[str(artifact)],
        event_types=["read_file", "run_command", "send_user_text"],
    )
    assert result["ok"] is True and result["delivery_confirmed"] is True
    receipt = latest_receipt(root, "partner_framework_frontend")
    assert receipt and len(receipt.artifacts) == 1
    archived = Path(receipt.artifacts[0])
    assert archived.is_file() and "/share/evidence/" in str(archived)
    assert archived.read_text(encoding="utf-8") == artifact.read_text(encoding="utf-8")
    assert len(receipt.next_actions) == 1
    proposed = request_next_action(root, {"project_id": "partner_framework_frontend"})
    assert proposed["status"] == "proposed" and proposed["queued"] is False


def test_bounded_governance_stage_proposes_declared_executable_next_action(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["04"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "04", "project_id": "literature_github_learning",
        "kind": "project_iteration", "title": "external slice", "instruction": "work",
    })
    item.status = "queued"; item.task_id = "task-1"; item.attempt = 1
    save_work_item(root, item)
    _write_delivery_task(root, item, delivered=True)
    artifact = Path(root) / "instances/04/state/tasks/task-1/external.md"
    artifact.write_text("evidence" * 100, encoding="utf-8")
    result = complete_campaign_work(
        root, campaign_instruction(item), files=[str(artifact)],
        event_types=["external_learning_index_slice"],
    )
    assert result["ok"] is True
    receipt = latest_receipt(root, "literature_github_learning")
    assert receipt and len(receipt.next_actions) == 1
    action = receipt.next_actions[0]
    assert action.event_type == "continuous_project_step"
    assert action.params["strategy_id"] == "04_harness_mapping"
    prepared = request_next_action(root, {"project_id": "literature_github_learning"})
    assert prepared["status"] == "proposed" and prepared["action"]["action_id"] == action.action_id


def test_watchdog_expiry_retries_with_new_task_id(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "audit",
        "title": "stale", "instruction": "audit", "requires_delivery": False,
    })
    first = tick_campaign(root, state.campaign_id, dispatch=lambda *_: "task-old")
    assert first["dispatched"]
    lease = next(value for value in list_leases(root, state.campaign_id) if value.status == "active")
    lease.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    save_lease(root, lease)
    second = tick_campaign(root, state.campaign_id, dispatch=lambda *_: "task-new")
    current = load_work_item(root, state.campaign_id, item.work_item_id)
    assert second["dispatched"][0]["task_id"] == "task-new"
    assert current.attempt == 2 and current.task_id == "task-new"
    assert load_campaign(root, state.campaign_id).usage.retries == 1


def test_recovery_does_not_treat_iteration_boundary_as_final_completion(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    item = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "audit",
        "title": "multi-iteration", "instruction": "audit", "requires_delivery": False,
        "requires_artifact": False,
    })
    tick_campaign(root, state.campaign_id, dispatch=lambda *_: "task-boundary")
    task = Path(root) / "instances/01/state/tasks/task-boundary"
    task.mkdir(parents=True)
    (task / "task_instance.json").write_text(json.dumps({
        "task_id": "task-boundary", "user_message": campaign_instruction(item), "metadata": {},
    }), encoding="utf-8")
    (task / "task_log.jsonl").write_text(
        json.dumps({"event": "completion_status_updated", "status": "done", "llm_calls": 1}) + "\n" +
        json.dumps({"event": "iteration_llm_check", "satisfied": False, "missing": ["delivery"]}) + "\n",
        encoding="utf-8",
    )
    tick_campaign(root, state.campaign_id, dispatch=lambda *_: "must-not-repeat")
    current = load_work_item(root, state.campaign_id, item.work_item_id)
    assert current.status == "running"
    assert current.attempt == 1


def test_three_identical_progress_signatures_are_blocked(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["03"])
    artifacts = []
    for index in range(3):
        artifact = Path(root) / f"same-{index}.md"
        artifact.write_text("identical evidence", encoding="utf-8")
        artifacts.append(str(artifact))
    signature = None
    for index in range(2):
        prior = enqueue_work_item(root, state.campaign_id, {
            "instance_id": "03", "project_id": "p", "kind": "audit",
            "title": f"prior-{index}", "instruction": "audit", "requires_delivery": False,
        })
        prior.status = "completed"
        prior.task_id = f"prior-task-{index}"
        prior.event_types = ["read_file"]
        prior.artifacts = [artifacts[index]]
        from partner.governance.campaign import _progress_signature
        signature = _progress_signature(prior.event_types, prior.artifacts)
        prior.evidence = [f"progress_signature={signature}"]
        save_work_item(root, prior)
    current = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "03", "project_id": "p", "kind": "audit",
        "title": "third", "instruction": "audit", "requires_delivery": False,
    })
    current.status = "queued"
    current.task_id = "third-task"
    current.attempt = 1
    save_work_item(root, current)
    result = complete_campaign_work(root, campaign_instruction(current),
                                    files=[artifacts[2]], event_types=["read_file"])
    assert result["ok"] is False
    assert "same event/artifact signature" in " ".join(result["work_item"]["evidence"])


def test_enqueue_requires_real_runtime_task_id(tmp_path):
    root = _root(tmp_path)
    record_iteration(root, {
        "project_id": "p", "owner_instance": "01", "goal": "one",
        "actions_executed": ["audit"], "artifacts": [], "findings": ["f"],
        "next_actions": [{"title": "two", "event_type": "audit", "status": "proposed"}],
        "requires_delivery": False,
    })

    async def no_ack(*_args):
        return None

    result = asyncio.run(enqueue_next_action(root, "p", no_ack))
    assert result["queued"] is False
    assert result["status"] == "enqueue_missing_task_id"
    assert request_next_action(root, {"project_id": "p"})["status"] == "proposed"


def test_named_artifact_contract_excludes_read_inputs_but_keeps_outputs():
    names = _requested_named_artifacts(
        "读取最新 execution_wave_3_result.json 和 source.csv。必须实际编写 validator.py，"
        "生成 validation.json、report.md 和 report.pdf。"
    )
    assert names == {"validator.py", "validation.json", "report.md", "report.pdf"}


def test_named_artifact_contract_does_not_truncate_jsonl_input():
    names = _requested_named_artifacts(
        "核验 /tmp/issues.jsonl，生成 learning_index.json 和 report.pdf。"
    )
    assert names == {"learning_index.json", "report.pdf"}


def test_targetdiff_semantic_gate_rejects_identity_leakage(tmp_path):
    result = tmp_path / "targetdiff_residual_analysis.json"
    result.write_text(json.dumps({"metrics": {
        "train_mean_vina": -8.2,
        "linear_model": {"rmse": 0.0, "coef": 1.0, "intercept": 0.0},
        "baseline_train_mean": {"rmse": 2.6},
    }}), encoding="utf-8")
    problems = _artifact_semantic_problems("生成 targetdiff_residual_analysis.json", [str(result)])
    assert problems == ["TargetDiff target leakage: Vina was used as both feature and prediction target"]


def test_deadline_creates_final_report(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"], duration=60)
    future = datetime.now(timezone.utc).astimezone() + timedelta(seconds=120)
    switches = []
    result = tick_campaign(root, state.campaign_id, dispatch=lambda *_: "final-task", now=future,
                           switch_slots=lambda ids: switches.append(list(ids)))
    assert result["status"] == "running"
    final_item = next(item for item in list_work_items(root, state.campaign_id)
                      if item.title == "Campaign 最终日报")
    assert final_item.status == "queued"
    _write_delivery_task(root, final_item, delivered=True)
    completed = complete_campaign_work(root, campaign_instruction(final_item), files=[], event_types=["send_user_text"])
    assert completed["ok"] is True
    result = tick_campaign(root, state.campaign_id, dispatch=lambda *_: "unused", now=future,
                           switch_slots=lambda ids: switches.append(list(ids)))
    assert result["status"] == "completed"
    assert switches[-1] == ["01", "02"]
    snap = campaign_snapshot(root, state.campaign_id)
    assert snap["campaign"]["stop_reason"] == "campaign deadline reached"
    assert list((Path(root) / "state/campaigns" / state.campaign_id / "reports").glob("*.md"))


def test_deadline_drains_running_work_before_final_report(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"], duration=60)
    seed_default_work(root, state.campaign_id)
    tick_campaign(root, state.campaign_id, dispatch=lambda *_: "running-task")
    future = datetime.now(timezone.utc).astimezone() + timedelta(seconds=120)
    result = tick_campaign(root, state.campaign_id, dispatch=lambda *_: "must-not-run", now=future)
    assert result["phase"] == "draining"
    assert result["dispatched"] == []


def test_work_item_creation_cap_does_not_preempt_admitted_work(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"], max_items=1)
    seeded = seed_default_work(root, state.campaign_id)
    assert len(seeded) == 1
    result = tick_campaign(root, state.campaign_id, dispatch=lambda *_: "primary-task")
    assert result["dispatched"][0]["work_item_id"] == seeded[0].work_item_id
    assert not any(item.title == "Campaign 最终日报" for item in list_work_items(root, state.campaign_id))


def test_failure_budget_latches_cancels_unstarted_and_only_dispatches_final(tmp_path):
    root = _root(tmp_path)
    state = create_campaign(
        root, goal="hard failure stop", allowed_instances=["01"], duration_seconds=3600,
        budget=CampaignBudget(max_work_items=8, max_failures=1, max_retries_per_item=0,
                              max_runtime_seconds=3600),
    )
    first = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "audit", "title": "first",
        "instruction": "fail", "requires_artifact": False, "requires_delivery": False,
    })
    second = enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "audit", "title": "must not start",
        "instruction": "never dispatch", "priority": 1, "requires_artifact": False,
        "requires_delivery": False,
    })
    dispatched = []
    tick_campaign(root, state.campaign_id,
                  dispatch=lambda item, _: dispatched.append(item.title) or "failed-task")
    failed = complete_campaign_work(root, campaign_instruction(first), success=False)
    assert failed["status"] == "blocked"
    result = tick_campaign(
        root, state.campaign_id,
        dispatch=lambda item, _: dispatched.append(item.title) or "final-task",
    )
    assert result["dispatched"][0]["work_item_id"] != second.work_item_id
    assert load_work_item(root, state.campaign_id, second.work_item_id).status == "cancelled"
    assert dispatched == ["first", "Campaign 最终日报"]
    assert load_campaign(root, state.campaign_id).stop_reason.startswith("finalizing: failure budget exhausted")


def test_total_budget_reserves_final_report_slot(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01"], max_items=3)
    enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "audit", "title": "one",
        "instruction": "one", "requires_artifact": False, "requires_delivery": False,
    })
    enqueue_work_item(root, state.campaign_id, {
        "instance_id": "01", "project_id": "p", "kind": "audit", "title": "two",
        "instruction": "two", "requires_artifact": False, "requires_delivery": False,
    })
    with pytest.raises(ValueError, match="budget exhausted"):
        enqueue_work_item(root, state.campaign_id, {
            "instance_id": "01", "project_id": "p", "kind": "report", "title": "checkpoint",
            "instruction": "send", "requires_artifact": False,
        })


def test_seeded_rl_owner_prevents_parallel_issue_materialization(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["05"], max_items=10)
    seed = seed_default_work(root, state.campaign_id)[0]
    issue = record_issue(root, {
        "summary": "root failure", "category": "verification", "severity": "high",
        "evidence": ["real evidence"], "instance_id": "05", "project_id": "agent_self_evolution",
    })["issue"]
    assert materialize_evolution_work(root, state.campaign_id) == []
    seed.status = "completed"; seed.task_id = "seed-task"
    save_work_item(root, seed)
    assert materialize_evolution_work(root, state.campaign_id) == []


def test_seeded_rl_audit_waits_for_other_instances_to_finish(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01", "05"], max_items=10)
    seeds = seed_default_work(root, state.campaign_id)
    dispatched = []
    result = tick_campaign(
        root,
        state.campaign_id,
        dispatch=lambda item, _: dispatched.append(item.instance_id) or f"task-{item.instance_id}",
    )
    assert [row["instance_id"] for row in result["dispatched"]] == ["01"]
    assert dispatched == ["01"]

    business = next(item for item in seeds if item.instance_id == "01")
    business.status = "blocked"
    business.blocked_reason = "evidence-backed boundary"
    business.evidence = ["resume_event=user_authorization_available"]
    save_work_item(root, business)
    result = tick_campaign(
        root,
        state.campaign_id,
        dispatch=lambda item, _: dispatched.append(item.instance_id) or f"task-{item.instance_id}",
    )
    assert [row["instance_id"] for row in result["dispatched"]] == ["05"]
    assert dispatched == ["01", "05"]


def test_execution_profile_seeds_two_real_waves_and_rl_last(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["01", "02", "03", "04", "05"], max_items=20)
    items = seed_execution_work(root, state.campaign_id, waves=2)
    assert len(items) == 9
    assert sum(item.instance_id == "05" for item in items) == 1
    assert all("evidence_execution_slice" in item.instruction for item in items)
    assert {"execution_wave=1", "execution_wave=2"} <= {
        marker for item in items for marker in ("execution_wave=1", "execution_wave=2") if marker in item.instruction
    }
    dispatched = []
    tick_campaign(root, state.campaign_id,
                  dispatch=lambda item, _: dispatched.append(item.instance_id) or f"task-{item.work_item_id}")
    assert len(dispatched) == 2
    assert len(set(dispatched)) == 2
    assert set(dispatched) <= {"01", "02", "03", "04"}
    assert "05" not in dispatched


def test_molecular_profile_seeds_ordered_project_arc_and_rl_last(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["02", "05"], max_items=10)
    items = seed_targetdiff_project_work(root, state.campaign_id, stages=5)
    assert [item.instance_id for item in items] == ["02"] * 5 + ["05"]
    assert [item.priority for item in items[:5]] == [90, 89, 88, 87, 86]
    assert all(f"targetdiff_stage={stage}" in items[stage - 1].instruction for stage in range(1, 6))
    dispatched = []
    tick_campaign(root, state.campaign_id,
                  dispatch=lambda item, _: dispatched.append(item.title) or f"task-{item.work_item_id}")
    assert dispatched == [items[0].title]
    assert items[-1].title not in dispatched


def test_molecular_continuous_replenishes_after_receipt_and_rl_milestones(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["02", "05"], max_items=20)
    stage9 = seed_targetdiff_continuous_work(root, state.campaign_id)[0]
    assert "targetdiff_stage=9" in stage9.instruction
    assert materialize_targetdiff_continuous_work(root, state.campaign_id) == []
    assert materialize_project_actions(root, state.campaign_id) == []
    assert materialize_evolution_work(root, state.campaign_id) == []

    stage9.status = "completed"; stage9.task_id = "task-stage9"
    stage9.event_types = ["targetdiff_ligand_aggregation_cv"]
    save_work_item(root, stage9)
    stage10 = materialize_targetdiff_continuous_work(root, state.campaign_id)[0]
    assert "targetdiff_stage=10" in stage10.instruction
    assert stage10.instance_id == "02"

    stage10.status = "completed"; stage10.task_id = "task-stage10"
    stage10.event_types = ["targetdiff_target_balanced_metrics"]
    save_work_item(root, stage10)
    checkpoint = materialize_targetdiff_continuous_work(root, state.campaign_id)[0]
    assert checkpoint.instance_id == "05"
    assert "rl_after_targetdiff_stage=10" in checkpoint.instruction
    # The next business experiment must wait for the milestone audit.
    assert materialize_targetdiff_continuous_work(root, state.campaign_id) == []

    checkpoint.status = "completed"; checkpoint.task_id = "task-rl10"
    checkpoint.event_types = ["offline_rl_self_evolution"]
    save_work_item(root, checkpoint)
    stage11 = materialize_targetdiff_continuous_work(root, state.campaign_id)[0]
    assert "targetdiff_stage=11" in stage11.instruction
    assert stage11.instance_id == "02"


def test_portfolio_continuous_rotates_changed_inputs_with_two_slots_and_rl_gate(tmp_path):
    root = _root(tmp_path)
    workspace = Path(root)
    content = workspace / "external/content/inbox.jsonl"
    content.parent.mkdir(parents=True)
    content.write_text('{"title":"new bounded source"}\n', encoding="utf-8")
    learning = workspace / "external/code/SESA-Self-Evolving-Search-Agents-master/README.md"
    learning.parent.mkdir(parents=True)
    learning.write_text("# SESA evidence\n", encoding="utf-8")
    state = create_campaign(
        root, goal="[portfolio_continuous=true] five project rotation",
        allowed_instances=["01", "02", "03", "04", "05"], duration_seconds=3600,
        max_active=2, budget=CampaignBudget(max_work_items=20, max_runtime_seconds=3600),
    )
    assert seed_portfolio_work(root, state.campaign_id) == []
    seeded = materialize_portfolio_work(root, state.campaign_id)
    assert {item.instance_id for item in seeded} == {"01", "03", "04"}
    assert all("source_fingerprint=" in item.instruction for item in seeded)
    assert materialize_project_actions(root, state.campaign_id) == []
    assert materialize_evolution_work(root, state.campaign_id) == []

    dispatched = []
    first = tick_campaign(
        root, state.campaign_id,
        dispatch=lambda item, _: dispatched.append(item.instance_id) or f"task-{item.work_item_id}",
    )
    assert len(first["dispatched"]) == 2
    assert len(set(dispatched)) == 2
    assert "05" not in dispatched

    for item in list_work_items(root, state.campaign_id):
        if "portfolio_lane=" in item.instruction:
            item.status = "completed"
            item.task_id = f"done-{item.work_item_id}"
            item.event_types = ["bounded_project_event"]
            item.artifacts = [f"/{item.instance_id}.json"]
            save_work_item(root, item)
    rl = materialize_portfolio_work(root, state.campaign_id)
    assert len(rl) == 1 and rl[0].instance_id == "05"
    assert "offline_rl_self_evolution" in rl[0].instruction

    rl[0].status = "completed"
    rl[0].task_id = "done-rl"
    rl[0].event_types = ["offline_rl_self_evolution"]
    save_work_item(root, rl[0])
    exploration = materialize_portfolio_work(root, state.campaign_id)
    assert {item.instance_id for item in exploration} == {"01", "03", "04"}
    assert all("portfolio_exploration_round=1" in item.instruction for item in exploration)
    snapshot = campaign_snapshot(root, state.campaign_id)
    assert snapshot["portfolio"]["lanes"]["02"]["status"] == "waiting_input"
    assert snapshot["portfolio"]["lanes"]["05"]["status"] == "waiting_wave"


def test_portfolio_requires_two_stable_input_observations_before_redispatch(tmp_path):
    root = _root(tmp_path)
    content = Path(root) / "external/content/inbox.jsonl"
    content.parent.mkdir(parents=True)
    content.write_text('{"title":"first"}\n', encoding="utf-8")
    state = create_campaign(
        root, goal="[portfolio_continuous=true] stable input",
        allowed_instances=["01"], duration_seconds=3600,
        budget=CampaignBudget(max_work_items=10, max_runtime_seconds=3600),
    )
    assert seed_portfolio_work(root, state.campaign_id) == []
    first = materialize_portfolio_work(root, state.campaign_id)
    assert len(first) == 1
    first[0].status = "completed"; first[0].task_id = "done-first"
    save_work_item(root, first[0])
    content.write_text('{"title":"first"}\n{"title":"second"}\n', encoding="utf-8")
    assert materialize_portfolio_work(root, state.campaign_id) == []
    assert campaign_snapshot(root, state.campaign_id)["portfolio"]["lanes"]["01"]["status"] == "observing_stability"
    changed = materialize_portfolio_work(root, state.campaign_id)
    assert len(changed) == 1 and changed[0].instance_id == "01"


def test_portfolio_exhausted_old_curriculum_admits_new_meaningful_wave(tmp_path):
    root = _root(tmp_path)
    workspace = Path(root)
    content = workspace / "external/content/inbox.jsonl"
    content.parent.mkdir(parents=True)
    content.write_text('{"id":"one","urls":["https://example.org"],"visible_body":"evidence"}\n',
                       encoding="utf-8")
    split = workspace / "external/targetdiff/data/split_by_name.pt"
    split.parent.mkdir(parents=True)
    split.write_bytes(b"stable split")
    learning = workspace / "external/code/deepseek-harness/docs/architecture.md"
    learning.parent.mkdir(parents=True)
    learning.write_text("append-only evidence", encoding="utf-8")
    state = create_campaign(
        root, goal="[portfolio_continuous=true] renew old curriculum",
        allowed_instances=["01", "02", "03", "04", "05"], duration_seconds=3600,
        max_active=2, budget=CampaignBudget(max_work_items=30, max_runtime_seconds=3600),
    )
    seed_portfolio_work(root, state.campaign_id)
    initial = materialize_portfolio_work(root, state.campaign_id)
    for item in initial:
        item.status = "completed"; item.task_id = f"done-{item.work_item_id}"
        item.event_types = ["bounded_project_event"]
        save_work_item(root, item)
    rl = materialize_portfolio_work(root, state.campaign_id)
    assert len(rl) == 1 and rl[0].instance_id == "05"
    rl[0].status = "completed"; rl[0].task_id = "done-rl"
    save_work_item(root, rl[0])

    portfolio_path = workspace / "state/campaigns" / state.campaign_id / "portfolio_state.json"
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    for instance, old_round in {"01": 2, "02": 2, "03": 3, "04": 3}.items():
        portfolio["lanes"][instance]["exploration_round"] = old_round
    portfolio_path.write_text(json.dumps(portfolio), encoding="utf-8")

    renewed = materialize_portfolio_work(root, state.campaign_id)
    assert {item.instance_id for item in renewed} == {"01", "02", "03", "04"}
    instructions = "\n".join(item.instruction for item in renewed)
    assert "strategy_id=01_claim_evidence_matrix" in instructions
    assert "targetdiff_official_split_error_slices" in instructions
    assert "strategy_id=03_runtime_recovery_canary" in instructions
    assert "strategy_id=04_adapter_contract" in instructions


def test_portfolio_scout_due_fills_two_slots_without_waking_rl(tmp_path):
    root = _root(tmp_path)
    state = create_campaign(
        root, goal="[portfolio_continuous=true] batched scouts",
        allowed_instances=["01", "03", "04", "05"], duration_seconds=3600,
        max_active=2, budget=CampaignBudget(max_work_items=30, max_runtime_seconds=3600),
    )
    # Framework input always exists. Add the other two bounded inputs.
    content = Path(root) / "external/content/inbox.jsonl"
    content.parent.mkdir(parents=True)
    content.write_text('{"id":"one"}\n', encoding="utf-8")
    learning = Path(root) / "external/code/deepseek-harness/docs/architecture.md"
    learning.parent.mkdir(parents=True)
    learning.write_text("append-only", encoding="utf-8")
    seed_portfolio_work(root, state.campaign_id)
    initial = materialize_portfolio_work(root, state.campaign_id)
    for item in initial:
        item.status = "completed"; item.task_id = f"done-{item.work_item_id}"
        item.event_types = ["bounded_project_event"]
        save_work_item(root, item)
    rl = materialize_portfolio_work(root, state.campaign_id)[0]
    rl.status = "completed"; rl.task_id = "done-rl"; save_work_item(root, rl)

    portfolio_path = Path(root) / "state/campaigns" / state.campaign_id / "portfolio_state.json"
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    from partner.governance.campaign import PORTFOLIO_EXPLORATION
    for instance in ("01", "03", "04"):
        portfolio["lanes"][instance]["exploration_round"] = len(PORTFOLIO_EXPLORATION[instance])
    portfolio["next_scout_at"] = ""
    portfolio_path.write_text(json.dumps(portfolio), encoding="utf-8")

    scouts = materialize_portfolio_work(root, state.campaign_id)
    assert len(scouts) == 2
    assert len({item.instance_id for item in scouts}) == 2
    assert all(item.kind == "audit" and "portfolio_scout=true" in item.instruction for item in scouts)
    for item in scouts:
        item.status = "completed"; item.task_id = f"done-{item.work_item_id}"
        save_work_item(root, item)
    assert not any(item.instance_id == "05" for item in materialize_portfolio_work(root, state.campaign_id))


def test_portfolio_old_curriculum_completion_opens_v3_business_wave(tmp_path):
    root = _root(tmp_path)
    workspace = Path(root)
    content = workspace / "external/content/inbox.jsonl"
    content.parent.mkdir(parents=True)
    content.write_text('{"id":"one","urls":["https://example.org"],"visible_body":"evidence"}\n',
                       encoding="utf-8")
    split = workspace / "external/targetdiff/data/split_by_name.pt"
    split.parent.mkdir(parents=True)
    split.write_bytes(b"stable split")
    learning = workspace / "external/code/deepseek-harness/docs/architecture.md"
    learning.parent.mkdir(parents=True)
    learning.write_text("append-only", encoding="utf-8")
    state = create_campaign(
        root, goal="[portfolio_continuous=true] v3 renewal",
        allowed_instances=["01", "02", "03", "04", "05"], duration_seconds=3600,
        max_active=2, budget=CampaignBudget(max_work_items=30, max_runtime_seconds=3600),
    )
    seed_portfolio_work(root, state.campaign_id)
    initial = materialize_portfolio_work(root, state.campaign_id)
    for item in initial:
        item.status = "completed"; item.task_id = f"done-{item.work_item_id}"
        item.event_types = ["bounded_project_event"]
        save_work_item(root, item)
    rl = materialize_portfolio_work(root, state.campaign_id)[0]
    rl.status = "completed"; rl.task_id = "done-rl"; save_work_item(root, rl)

    portfolio_path = workspace / "state/campaigns" / state.campaign_id / "portfolio_state.json"
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    # These were the exact terminal lengths of the previous curriculum.
    for instance, previous_length in {"01": 3, "02": 3, "03": 4, "04": 4}.items():
        portfolio["lanes"][instance]["exploration_round"] = previous_length
    portfolio_path.write_text(json.dumps(portfolio), encoding="utf-8")

    renewed = materialize_portfolio_work(root, state.campaign_id)
    assert {item.instance_id for item in renewed} == {"01", "02", "03", "04"}
    instructions = "\n".join(item.instruction for item in renewed)
    assert "strategy_id=01_claim_risk_queue" in instructions
    assert "strategy_id=02_model_risk_register" in instructions
    assert "strategy_id=03_user_observability_canary" in instructions
    assert "strategy_id=04_reference_gap_matrix" in instructions


def test_tick_materializes_receipt_continuation_before_rl_checkpoint(tmp_path):
    root = _root(tmp_path)
    state = create_campaign(
        root, goal="[portfolio_continuous=true] continuation before rl",
        allowed_instances=["03", "05"], duration_seconds=3600,
        max_active=2, budget=CampaignBudget(max_work_items=12, max_runtime_seconds=3600),
    )
    seed_portfolio_work(root, state.campaign_id)
    initial = materialize_portfolio_work(root, state.campaign_id)[0]
    initial.status = "completed"; initial.task_id = "done-initial"
    initial.event_types = ["framework_campaign_contract_audit"]
    initial.artifacts = ["/framework.json"]
    initial.evidence = ["business_progress=true"]
    save_work_item(root, initial)
    recorded = record_iteration(root, {
        "project_id": "partner_framework_frontend", "owner_instance": "03",
        "goal": "continue framework", "inputs": [], "actions_executed": ["audit"],
        "artifacts": ["/framework.json"], "delivery_confirmed": True,
        "next_actions": [{"title": "next framework canary", "event_type": "continuous_project_step",
                          "params": {"user_request": "[strategy_id=03_user_observability_canary] "
                                                     "直接执行确定性事件 continuous_project_step。"}}],
    })
    assert recorded["ok"] is True
    dispatched = []
    tick_campaign(root, state.campaign_id,
                  dispatch=lambda item, _: dispatched.append(item) or f"task-{item.work_item_id}")
    assert dispatched and dispatched[0].instance_id == "03"
    assert not any(item.instance_id == "05" for item in list_work_items(root, state.campaign_id))


def test_portfolio_suppresses_repetitive_scouts_when_business_density_degrades(tmp_path):
    from partner.governance.campaign import PORTFOLIO_EXPLORATION

    root = _root(tmp_path)
    state = create_campaign(
        root, goal="[portfolio_continuous=true] density gate",
        allowed_instances=["03", "04", "05"], duration_seconds=7200,
        max_active=2, budget=CampaignBudget(max_work_items=30, max_runtime_seconds=7200),
    )
    portfolio_path = Path(root) / "state/campaigns" / state.campaign_id / "portfolio_state.json"
    seed_portfolio_work(root, state.campaign_id)
    initial = materialize_portfolio_work(root, state.campaign_id)
    for item in initial:
        item.status = "completed"; item.task_id = f"done-{item.work_item_id}"
        item.event_types = ["bounded_project_event"]; save_work_item(root, item)
    rl = materialize_portfolio_work(root, state.campaign_id)[0]
    rl.status = "completed"; rl.task_id = "done-rl"; save_work_item(root, rl)
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    for instance in ("03", "04"):
        portfolio["lanes"][instance]["exploration_round"] = len(PORTFOLIO_EXPLORATION[instance])
    portfolio["next_scout_at"] = ""
    portfolio_path.write_text(json.dumps(portfolio), encoding="utf-8")
    # Add six completed no-change audits to make the recent evidence window unhealthy.
    for index in range(6):
        item = enqueue_work_item(root, state.campaign_id, {
            "instance_id": "03" if index % 2 == 0 else "04", "project_id": "density",
            "kind": "audit", "title": f"scout-{index}",
            "instruction": "[portfolio_scout=true] [portfolio_lane=03] no change",
            "requires_artifact": False, "requires_delivery": False,
        })
        item.status = "completed"; item.task_id = f"done-{index}"
        item.evidence = ["business_progress=false", "no_change=true"]
        save_work_item(root, item)
    assert materialize_portfolio_work(root, state.campaign_id) == []
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    assert portfolio["progress_density"]["status"] == "degraded_waiting_new_hypothesis"
    assert portfolio["lanes"]["03"]["scout_status"] == "suppressed_low_business_density"


def test_inherited_completed_curriculum_can_start_scout_without_current_outcome(tmp_path):
    from partner.governance.campaign import PORTFOLIO_EXPLORATION

    root = _root(tmp_path)
    first = create_campaign(
        root, goal="[portfolio_continuous=true] predecessor",
        allowed_instances=["03", "04"], duration_seconds=3600,
        max_active=2, budget=CampaignBudget(max_work_items=10, max_runtime_seconds=3600),
    )
    seed_portfolio_work(root, first.campaign_id)
    materialize_portfolio_work(root, first.campaign_id)
    portfolio_path = Path(root) / "state/campaigns" / first.campaign_id / "portfolio_state.json"
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    for instance in ("03", "04"):
        portfolio["lanes"][instance]["exploration_round"] = len(PORTFOLIO_EXPLORATION[instance])
    portfolio["next_scout_at"] = ""
    portfolio_path.write_text(json.dumps(portfolio), encoding="utf-8")
    cancel_campaign(root, first.campaign_id, "test predecessor complete")

    second = create_campaign(
        root, goal="[portfolio_continuous=true] inherited scout",
        allowed_instances=["03", "04"], duration_seconds=3600,
        max_active=2, budget=CampaignBudget(max_work_items=10, max_runtime_seconds=3600),
    )
    scouts = seed_portfolio_work(root, second.campaign_id)
    assert len(scouts) == 2
    assert all("portfolio_scout=true" in item.instruction for item in scouts)
    assert not any(item.instance_id == "05" for item in scouts)


def test_deadline_final_sync_captures_late_rl_audit_outcome(tmp_path):
    root = _root(tmp_path)
    state = _campaign(root, ["05"], duration=60, max_items=4)
    audit = seed_default_work(root, state.campaign_id)[0]
    artifact = Path(root) / "rl-audit.md"
    artifact.write_text("audit", encoding="utf-8")
    audit.status = "completed"
    audit.task_id = "rl-task"
    audit.attempt = 1
    audit.artifacts = [str(artifact)]
    audit.event_types = ["offline_rl_self_evolution"]
    audit.evidence = ["delivery_confirmed=True"]
    save_work_item(root, audit)

    future = datetime.now(timezone.utc).astimezone() + timedelta(seconds=120)
    tick_campaign(root, state.campaign_id, dispatch=lambda *_: "final-task", now=future)
    trajectories = Path(root) / "share/mind/governance/rl/trajectories.jsonl"
    rows = [json.loads(line) for line in trajectories.read_text(encoding="utf-8").splitlines()]
    assert [row["work_item_id"] for row in rows] == [audit.work_item_id]
    assert rows[0]["action"]["action_key"] == "05:project_iteration:offline_rl_self_evolution"
    events = Path(root) / "state/campaigns" / state.campaign_id / "events.jsonl"
    assert "offline_rl_final_sync" in events.read_text(encoding="utf-8")


def test_dashboard_exposes_campaign_without_full_work_payload(tmp_path, monkeypatch):
    root = _root(tmp_path)
    state = _campaign(root, ["01"])
    seed_default_work(root, state.campaign_id)
    from partner.monitoring import partner_dashboard as dashboard
    monkeypatch.setattr(dashboard, "_service_state", lambda _instance: "inactive")
    snap = dashboard.snapshot(workspace_root=root, code_root=str(tmp_path),
                              pytest_summary_path=str(tmp_path / "missing"))
    assert snap["campaign"]["campaign_id"] == state.campaign_id
    assert snap["campaign"]["work_items"]["proposed"] == 1
    assert "instruction" not in snap["campaign"]
