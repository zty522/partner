import json
from datetime import datetime, timedelta

from partner.core.delivery_context import (
    local_delivery_target,
    record_active_user_context,
)
from partner.tasks.task_queue import Task, TaskQueue


def test_local_user_context_overrides_stale_qq_target(tmp_path):
    workspace = tmp_path / "instances" / "04"
    (workspace / "state").mkdir(parents=True)
    (workspace / "state" / "qq_user_context.json").write_text(
        json.dumps({"openid": "stale-qq-user"}), encoding="utf-8",
    )

    value = record_active_user_context(
        str(workspace), source="desktop_gui", sender_id="desktop_gui", message_id="local-1",
    )

    assert value["channel"] == "local"
    assert local_delivery_target(str(workspace)) == "desktop_gui"


def test_qq_user_context_does_not_get_local_ack(tmp_path):
    workspace = tmp_path / "instances" / "04"
    (workspace / "state").mkdir(parents=True)
    value = record_active_user_context(
        str(workspace), source="qq", sender_id="real-qq-user", message_id="qq-1",
    )
    assert value["channel"] == "qq"
    assert local_delivery_target(str(workspace)) == ""


def test_runtime_restart_cancels_only_preexisting_pending_tasks(tmp_path):
    path = tmp_path / "task_queue.json"
    queue = TaskQueue(str(path))
    old = Task(title="old", created_at=(datetime.now() - timedelta(minutes=5)).isoformat())
    new = Task(title="new", created_at=(datetime.now() + timedelta(minutes=5)).isoformat())
    done = Task(title="done", status="completed")
    queue.add_task(old)
    queue.add_task(new)
    queue.add_task(done)

    changed = queue.cancel_pending_before(datetime.now(), reason="restart reconciliation")

    assert changed == 1
    reloaded = TaskQueue(str(path))
    assert [task.status for task in reloaded.tasks] == ["cancelled", "pending", "completed"]
    assert reloaded.tasks[0].result_summary == "restart reconciliation"
