import json
from datetime import datetime, timedelta
from pathlib import Path

from partner.governance.instance_native import (
    authoritative_terminal_event,
    blocked_instances,
    handle_terminal,
    load_state,
    redrive_learning,
    recover_or_start,
    unblock_blocked_instance,
    yield_blocked_without_evidence,
)
from partner.harness_core.task_instance import TaskInstance, reconcile_orphaned_task_instances


def _root(tmp_path: Path, enabled=("04",)) -> Path:
    root = tmp_path / "workspace"
    (root / "config").mkdir(parents=True)
    (root / "config/partner_config.json").write_text(json.dumps({
        "runtime": {
            "mode": "manual_stable",
            "instance_native_autonomy": True,
            "instance_native_enabled_instances": list(enabled),
            "instance_native_max_active": 2,
            "instance_native_max_learning_interruptions_per_failure": 1,
            "instance_native_slot_quantum_project_steps": 2,
        }
    }), encoding="utf-8")
    for instance_id in ("01", "02", "03", "04", "05"):
        (root / "instances" / instance_id / "state/tasks").mkdir(parents=True)
    return root


def _external_artifact(root: Path, project_id: str, label: str = "run.json") -> Path:
    """Drop a real artifact under the ADR 0061 accepted external location."""
    directory = root / "share/projects" / project_id / "external_artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / label
    target.write_text('{"ran": true, "label": "%s"}' % label, encoding="utf-8")
    return target


def _task(root: Path, instance_id: str, task_id: str, *, status="done",
          native=True, governance_ok=True, next_actions=None,
          findings=None, actions_executed=None, artifacts=None,
          project_id=None) -> None:
    directory = root / "instances" / instance_id / "state/tasks" / task_id
    directory.mkdir(parents=True)
    project_id = project_id or "literature_github_learning"
    artifact_paths = list(artifacts or [])
    if native and not artifact_paths and status == "done" and governance_ok:
        artifact_paths.append(str(_external_artifact(root, project_id, f"{task_id}.json")))
    receipt = {
        "next_actions": list(next_actions or []),
        "findings": list(findings if findings is not None else
                         [f"r进度 token={task_id} 已记录真实外部动作"]),
        "actions_executed": list(actions_executed or ["exec:python3 -m pytest tests/test_instance_native.py"]),
        "artifacts": artifact_paths,
        "unresolved_questions": [],
    }
    value = {
        "task_id": task_id,
        "completion_status": status,
        "user_message": (
            "[instance_native=true] [native_kind=project] do work"
            if native else "ordinary user task"
        ),
        "metadata": {"manual_iteration_governance": {
            "ok": governance_ok,
            "status": "recorded" if governance_ok else "truth_gate_failed",
            "receipt": receipt,
        }},
    }
    (directory / "task_instance.json").write_text(
        json.dumps(value), encoding="utf-8")
    (directory / "task_log.jsonl").write_text(
        json.dumps({"ts": "2026-01-01T00:00:00", "event": "task_instance_created"}) + "\n",
        encoding="utf-8",
    )


def _inbox(root: Path, instance_id="04") -> list[dict]:
    path = root / "instances" / instance_id / "state/desktop_inbox.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_recovery_seeds_one_project_action_and_never_duplicates_pending(tmp_path):
    root = _root(tmp_path)
    project = root / "share/projects/literature_github_learning"
    project.mkdir(parents=True)
    brief = project / "project_brief.md"
    brief.write_text("# Real project brief", encoding="utf-8")
    first = recover_or_start(root, "04")
    second = recover_or_start(root, "04")
    assert first["status"] == "dispatched"
    assert first["kind"] == "project"
    assert second["status"] == "pending_dispatch"
    rows = _inbox(root)
    assert len(rows) == 1
    assert "[instance_native=true]" in rows[0]["text"]
    assert not rows[0]["text"].startswith("[instance_native=true]")
    assert rows[0]["text"].startswith("【04实例项目：文献与 GitHub")
    assert "【04实例原生项目续跑】" in rows[0]["text"]
    assert str(brief.resolve()) in rows[0]["text"]
    assert "不得猜测或虚构路径" in rows[0]["text"]
    assert "Campaign" not in rows[0]["text"]


def test_recovery_replaces_phantom_pending_dispatch(tmp_path):
    root = _root(tmp_path)
    from partner.governance.instance_native import save_state
    state = load_state(root, "04")
    state.enabled = True
    state.phase = "PROJECT_DISPATCHED"
    state.pending_message_id = "native_04_missing_from_inbox_and_tasks"
    state.pending_kind = "project"
    save_state(root, state)
    result = recover_or_start(root, "04")
    assert result["status"] == "dispatched"
    assert result["message_id"] != "native_04_missing_from_inbox_and_tasks"
    assert len(_inbox(root)) == 1
    events = (root / "state/instance_native/events.jsonl").read_text(encoding="utf-8")
    assert "native_pending_recovered" in events


def test_seen_append_only_inbox_row_is_not_treated_as_pending_forever(tmp_path):
    root = _root(tmp_path)
    first = recover_or_start(root, "04")
    seen = root / "instances/04/state/desktop_inbox_seen_ids.json"
    seen.write_text(json.dumps([first["message_id"]]), encoding="utf-8")

    second = recover_or_start(root, "04")

    assert second["status"] == "dispatched"
    assert second["message_id"] != first["message_id"]
    assert len(_inbox(root)) == 2


def test_successful_native_project_terminal_immediately_dispatches_next_project(tmp_path):
    root = _root(tmp_path)
    recover_or_start(root, "04")
    _task(root, "04", "task-success")
    result = handle_terminal(root, instance_id="04", task_id="task-success")
    assert result["status"] == "dispatched"
    assert result["kind"] == "project"
    state = load_state(root, "04")
    assert state.project_steps == 1
    assert state.learning_interruptions == 0
    assert len(_inbox(root)) == 2


def test_failed_native_project_inserts_one_bounded_learning_interrupt(tmp_path):
    root = _root(tmp_path)
    recover_or_start(root, "04")
    _task(root, "04", "task-failed", status="failed", governance_ok=False)
    result = handle_terminal(root, instance_id="04", task_id="task-failed")
    assert result["kind"] == "learning"
    assert "observe→select→diagnose→repair proposal" in _inbox(root)[-1]["text"]
    assert "Campaign" in _inbox(root)[-1]["text"]  # explicit prohibition only
    state = load_state(root, "04")
    assert state.phase == "LEARNING_DISPATCHED"
    assert state.consecutive_failures == 1
    # Episode id is deterministic but implementation-owned; prove the failed
    # task is discoverable by its factual task_id before learning is queued.
    episodes = list((root / "share/mind/governance/episodes").glob("episode_*/state.json"))
    assert any(json.loads(path.read_text())["task_id"] == "task-failed" for path in episodes)


def test_completed_learning_returns_to_suspended_project(tmp_path):
    root = _root(tmp_path)
    recover_or_start(root, "04")
    _task(root, "04", "task-failed", status="failed", governance_ok=False)
    handle_terminal(root, instance_id="04", task_id="task-failed")
    _task(root, "04", "task-learning", status="done", governance_ok=True)
    # State is authoritative for the dispatched kind; the runtime task marker
    # may still be rendered by the existing manual planner.
    result = handle_terminal(root, instance_id="04", task_id="task-learning")
    assert result["kind"] == "project"
    state = load_state(root, "04")
    assert state.learning_interruptions == 1
    assert state.phase == "PROJECT_DISPATCHED"


def test_failed_retry_after_one_learning_yields_slot_instead_of_learning_forever(tmp_path):
    root = _root(tmp_path, enabled=("02", "04", "05"))
    recover_or_start(root, "04")
    _task(root, "04", "first-failure", status="failed", governance_ok=False)
    assert handle_terminal(root, instance_id="04", task_id="first-failure")["kind"] == "learning"
    _task(root, "04", "learning", status="done", governance_ok=True)
    assert handle_terminal(root, instance_id="04", task_id="learning")["kind"] == "project"
    _task(root, "04", "failed-retry", status="failed", governance_ok=False)

    result = handle_terminal(root, instance_id="04", task_id="failed-retry")

    state = load_state(root, "04")
    assert result["status"] == "yield_slot"
    assert state.phase == "YIELD_SLOT"
    assert state.learning_interruptions == 0
    assert len(_inbox(root)) == 3


def test_ordinary_manual_task_without_declared_next_action_still_stops(tmp_path):
    root = _root(tmp_path)
    _task(root, "04", "manual", native=False)
    result = handle_terminal(root, instance_id="04", task_id="manual")
    assert result["status"] == "manual_task_not_auto_continued"
    assert not (root / "instances/04/state/desktop_inbox.jsonl").exists()


def test_disabled_instance_cannot_seed_or_continue(tmp_path):
    root = _root(tmp_path, enabled=("04",))
    assert recover_or_start(root, "02")["status"] == "native_disabled"
    _task(root, "02", "task")
    assert handle_terminal(root, instance_id="02", task_id="task")["status"] == "native_disabled"


def test_restart_does_not_silently_bypass_a_blocked_learning_state(tmp_path):
    root = _root(tmp_path)
    recover_or_start(root, "04")
    state = load_state(root, "04")
    state.phase = "BLOCKED"
    state.pending_message_id = ""
    state.reason = "bounded learning failed"
    from partner.governance.instance_native import save_state
    save_state(root, state)

    result = recover_or_start(root, "04")

    assert result["status"] == "blocked_requires_evidence_change"
    assert len(_inbox(root)) == 1


def test_unresolved_gap_triggers_learning_without_a_clock(tmp_path):
    root = _root(tmp_path)
    recover_or_start(root, "04")
    _task(root, "04", "gap")
    task = root / "instances/04/state/tasks/gap/task_instance.json"
    value = json.loads(task.read_text())
    value["metadata"]["manual_iteration_governance"]["receipt"]["unresolved_questions"] = [
        "缺少上游 API 的真实字段合同"
    ]
    task.write_text(json.dumps(value), encoding="utf-8")
    result = handle_terminal(root, instance_id="04", task_id="gap")
    assert result["kind"] == "learning"
    assert "unresolved project knowledge gap" in _inbox(root)[-1]["text"]


def test_instance_yields_resource_slot_after_bounded_project_quantum(tmp_path):
    root = _root(tmp_path, enabled=("02", "04", "05"))
    recover_or_start(root, "04")
    _task(root, "04", "one")
    assert handle_terminal(root, instance_id="04", task_id="one")["kind"] == "project"
    _task(root, "04", "two")
    result = handle_terminal(root, instance_id="04", task_id="two")
    assert result["status"] == "yield_slot"
    assert load_state(root, "04").phase == "YIELD_SLOT"


def test_only_post_governance_terminal_can_advance_native_loop():
    early = {"status": "done", "data": {"source": "harness_execution"}}
    final = {"status": "done", "data": {"source": "manual_stop_project_finalization"}}
    assert authoritative_terminal_event(early) is False
    assert authoritative_terminal_event(final) is True


def test_rejected_learning_route_can_be_explicitly_redriven(tmp_path):
    root = _root(tmp_path)
    _task(root, "04", "failed-source", status="failed", governance_ok=False)
    result = redrive_learning(
        root, instance_id="04", failed_task_id="failed-source",
        reason="deterministic learning route installed",
    )
    assert result["kind"] == "learning"
    assert load_state(root, "04").phase == "LEARNING_DISPATCHED"


def test_runtime_restart_finalizes_orphaned_native_task_and_emits_terminal(tmp_path):
    root = _root(tmp_path)
    instance_root = root / "instances/04"
    task = TaskInstance.create(
        str(instance_root),
        "native project work [instance_native=true] [native_kind=project]",
    )
    cutoff = datetime.now() + timedelta(seconds=1)

    changed = reconcile_orphaned_task_instances(str(instance_root), cutoff)

    settled = TaskInstance.load(str(instance_root), task.task_id)
    assert changed == 1
    assert settled.completion_status == "failed"
    governance = settled.metadata["manual_iteration_governance"]
    assert governance["status"] == "runtime_restart_orphaned"
    terminals = (root / "state/campaigns/task_terminal_events.jsonl").read_text(encoding="utf-8")
    assert task.task_id in terminals
    assert "manual_stop_project_finalization" in terminals



# ADR 0061: real-action contract guards -------------------------------------------------------------

def test_native_report_only_step_is_blocked_before_dispatching_next(tmp_path):
    """A receipt whose findings repeat verbatim and whose artifacts point at non-external paths
    must NOT advance the project and must surface a real-action contract violation."""
    root = _root(tmp_path)
    recover_or_start(root, "04")
    # Inject a baseline "old" receipt that sets up the repetition window.
    project = root / "share/projects/literature_github_learning"
    (project / "governance/receipts").mkdir(parents=True, exist_ok=True)
    baseline = project / "governance/receipts/iter_001_baseline.json"
    baseline.write_text(json.dumps({
        "iteration": 1,
        "findings": ["已完成本地微计划执行，未产生外部产物"],
        "actions_executed": ["generate_text"],
        "artifacts": [str(project / "reports/empty.md")],
    }), encoding="utf-8")
    # Now dispatch a project task whose findings are an exact repeat and whose artifacts
    # are merely references to internal report paths.
    _task(root, "04", "task-repeat",
          findings=["已完成本地微计划执行，未产生外部产物"],
          actions_executed=["generate_text"],
          artifacts=[str(project / "reports/empty2.md")])
    result = handle_terminal(root, instance_id="04", task_id="task-repeat")
    assert result["status"] == "progress_blocked", result
    assert result["violation"] in {"repeated_findings", "missing_external_action", "no_real_artifact"}
    state = load_state(root, "04")
    assert state.phase == "BLOCKED"
    assert "ADR 0061" in state.reason
    assert any(json.loads(line)["event_type"] == "native_project_blocked"
               for line in (root / "state/instance_native/events.jsonl").read_text(
                   encoding="utf-8").splitlines() if line.strip())


def test_unblock_requires_real_evidence_paths(tmp_path):
    root = _root(tmp_path)
    recover_or_start(root, "04")
    _task(root, "04", "task-repeat",
          findings=["已完成本地微计划执行，未产生外部产物"],
          actions_executed=["generate_text"],
          artifacts=["/nope/definitely/fake/path.json"])
    handle_terminal(root, instance_id="04", task_id="task-repeat")
    state = load_state(root, "04")
    assert state.phase == "BLOCKED"

    # 1) Empty evidence must be rejected.
    bad = unblock_blocked_instance(root, "04", evidence_paths=[])
    assert bad["status"] == "missing_evidence"
    # 2) Non-existent file must be rejected.
    bad = unblock_blocked_instance(root, "04", evidence_paths=["/nope/missing.json"])
    assert bad["status"] == "evidence_not_real"
    # 3) Reports-only evidence must be rejected.
    project = root / "share/projects/literature_github_learning"
    fake_evidence = project / "reports" / "still_self_report.md"
    fake_evidence.parent.mkdir(parents=True, exist_ok=True)
    fake_evidence.write_text("just another report", encoding="utf-8")
    bad = unblock_blocked_instance(root, "04", evidence_paths=[str(fake_evidence)])
    assert bad["status"] == "evidence_is_self_report"
    # 4) Real external evidence must succeed.
    real_evidence = root / "share/projects/literature_github_learning/external_artifacts/run.json"
    real_evidence.parent.mkdir(parents=True, exist_ok=True)
    real_evidence.write_text('{"kind":"web_fetch","url":"https://example.com"}',
                             encoding="utf-8")
    ok = unblock_blocked_instance(root, "04", evidence_paths=[str(real_evidence)],
                                  reason="manual diagnostic")
    assert ok["ok"] is True
    assert ok["status"] == "unblocked_to_yield"
    state = load_state(root, "04")
    assert state.phase == "YIELD_SLOT"
    # 5) Diagnostic yield from another BLOCKED instance works without evidence.
    _task(root, "04", "task-repeat2",
          findings=["已完成本地微计划执行，未产生外部产物"],
          actions_executed=["generate_text"],
          artifacts=["/still/fake.json"])
    handle_terminal(root, instance_id="04", task_id="task-repeat2")
    state = load_state(root, "04")
    assert state.phase == "BLOCKED"
    ok2 = yield_blocked_without_evidence(root, "04", reason="watchdog diagnostic")
    assert ok2["ok"] is True
    state = load_state(root, "04")
    assert state.phase == "YIELD_SLOT"
    # 6) blocked_instances helper reports the right shape after the path runs.
    blocked = blocked_instances(root)
    assert blocked == []


def test_happy_path_with_real_external_artifact_advances(tmp_path):
    root = _root(tmp_path)
    recover_or_start(root, "04")
    # Provide explicit artifact + action; findings differ from prior receipts.
    project = root / "share/projects/literature_github_learning"
    evidence = project / "external_artifacts" / "first_real_run.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text('{"action":"web.fetch","url":"https://docs.partner.dev"}',
                        encoding="utf-8")
    _task(root, "04", "task-real",
          findings=["抓取外部文档成功，产物实际保存"],
          actions_executed=["web.fetch:https://docs.partner.dev"],
          artifacts=[str(evidence)])
    result = handle_terminal(root, instance_id="04", task_id="task-real")
    assert result["status"] == "dispatched", result
    assert result["kind"] == "project"
    state = load_state(root, "04")
    assert state.phase in {"PROJECT_DISPATCHED", "ADVANCE_PROJECT"}
    assert state.project_steps == 1
