from partner.governance.completion_signal import (
    TaskTerminalReceiver,
    emit_task_terminal,
    ledger_path,
    workspace_root_from_task_dir,
)
from partner.harness_core.task_instance import TaskInstance


def test_terminal_mark_wakes_event_driven_controller(tmp_path):
    root = tmp_path / "workspace"
    instance = root / "instances/04"
    with TaskTerminalReceiver(root) as receiver:
        task = TaskInstance.create(str(instance), "bounded test")
        task.mark("done", {"source": "test"})
        event = receiver.wait(.5)
    assert event is not None
    assert event["task_id"] == task.task_id
    assert event["status"] == "done"
    assert event["instance_id"] == "04"
    assert ledger_path(root).is_file()


def test_nonterminal_signal_is_ignored_and_root_shape_is_fail_closed(tmp_path):
    assert emit_task_terminal(tmp_path, task_id="x", status="pending")["status"] == "not_terminal"
    assert workspace_root_from_task_dir(tmp_path / "unrelated/task") is None


def test_preliminary_batch_done_waits_for_final_governance(tmp_path):
    root = tmp_path / "workspace"
    instance = root / "instances/04"
    with TaskTerminalReceiver(root) as receiver:
        task = TaskInstance.create(str(instance), "campaign task")
        task.mark("done", {"source": "batch_plan"})
        assert receiver.wait(.05) is None
        task.mark("failed", {"source": "manual_stop_project_finalization"})
        event = receiver.wait(.5)
    assert event is not None
    assert event["task_id"] == task.task_id
    assert event["status"] == "failed"
