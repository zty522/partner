import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from partner.governance.campaign import (
    campaign_instruction, campaign_snapshot, complete_campaign_work,
    cancel_campaign, create_campaign, enqueue_work_item, seed_default_work, tick_campaign,
)
from partner.governance.campaign_models import CampaignBudget, CampaignState, WorkItem
from partner.governance.campaign_runtime import dispatch_to_instance
from partner.governance.campaign_storage import (
    list_leases, list_work_items, load_campaign, load_work_item, save_campaign,
    save_lease, save_work_item,
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

    item.attempt = 2
    retry_task_id = dispatch_to_instance(root, item, campaign_instruction(item))
    assert retry_task_id != task_id
    rows = [json.loads(line) for line in (Path(root) / "instances/01/state/desktop_inbox.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len({entry["message_id"] for entry in rows}) == 2


def _write_delivery_task(root, item, delivered=True):
    task = Path(root) / "instances" / item.instance_id / "state" / "tasks" / "task-1"
    task.mkdir(parents=True, exist_ok=True)
    (task / "task_instance.json").write_text(json.dumps({
        "user_message": campaign_instruction(item),
        "metadata": {"step_results": {"send": {"ok": delivered, "delivered": delivered}}},
    }), encoding="utf-8")
    (task / "task_log.jsonl").write_text(
        json.dumps({"event": "iteration_llm_check", "satisfied": True}) + "\n",
        encoding="utf-8",
    )


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
        "instance_id": "01", "project_id": "p", "kind": "audit",
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
    assert receipt and receipt.artifacts == [str(artifact)]
    assert len(receipt.next_actions) == 1
    proposed = request_next_action(root, {"project_id": "partner_framework_frontend"})
    assert proposed["status"] == "proposed" and proposed["queued"] is False


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
