import asyncio
import json
from pathlib import Path

import pytest

from partner.governance.context_selector import load_catalog, select_context
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.governance.models import IterationReceipt, NextAction
from partner.governance.project_loop import invalidate_receipt, record_iteration, request_next_action
from partner.governance.protocols import apply_transition, transition_for
from partner.governance.scheduler import assert_start_allowed, load_scheduler, set_active_slots
from partner.governance.signal_detector import detect_signals
from partner.governance.storage import latest_receipt
from partner.v2 import get_all_events


def _workspace(tmp_path, instance="01"):
    path = tmp_path / "workspace" / "instances" / instance
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def test_catalog_has_mandatory_context_and_valid_tiers():
    catalog = load_catalog()
    ids = {item["id"] for item in catalog["documents"]}
    assert {"current_status", "self_awareness", "verification_rules"} <= ids


def test_context_selection_is_budgeted_provenanced_and_instance_specific(tmp_path):
    workspace = _workspace(tmp_path, "01")
    selection, bundle = select_context(
        workspace,
        "打开小红书登录页面，截图并让视觉模型说明操作",
        instance_id="01",
        budget_chars=9000,
        semantic_selector=lambda prompt: '["xiaohongshu_playbook"]',
    )
    selected_ids = {item["document_id"] for item in selection.selected}
    assert {"current_status", "self_awareness", "verification_rules", "xiaohongshu_playbook"} <= selected_ids
    assert selection.used_chars <= selection.budget_chars
    assert "source:docs/playbooks/xiaohongshu_browser.md" in bundle

    deterministic, _ = select_context(
        workspace, "打开小红书登录页并截图", instance_id="01", budget_chars=9000,
    )
    assert "xiaohongshu_playbook" in {item["document_id"] for item in deterministic.selected}

    molecular, _ = select_context(
        _workspace(tmp_path, "02"), "承接分子生成实验并分析多样性",
        instance_id="02", budget_chars=9000,
    )
    assert "molecular_project" in {item["document_id"] for item in molecular.selected}


def test_iteration_receipt_requires_next_action_or_stop_reason():
    receipt = IterationReceipt(
        project_id="p", iteration=1, goal="g", inputs=[],
        actions_executed=["event"], artifacts=[], findings=[],
    )
    with pytest.raises(ValueError):
        receipt.to_dict()


def test_project_round_handoff_and_queue_ack(tmp_path):
    workspace = _workspace(tmp_path, "02")
    first = record_iteration(workspace, {
        "project_id": "mol", "owner_instance": "02", "goal": "round1",
        "actions_executed": ["generate"], "artifacts": ["first.csv"],
        "findings": ["baseline"], "delivery_confirmed": True,
        "next_actions": [{"title": "round2", "event_type": "diversity", "status": "proposed"}],
    })
    assert first["ok"] is True
    proposed = request_next_action(workspace, {"project_id": "mol"})
    assert proposed["queued"] is False
    assert proposed["status"] == "proposed"
    queued = request_next_action(workspace, {"project_id": "mol", "task_id": "task-2"})
    assert queued["queued"] is True
    bad_second = record_iteration(workspace, {
        "project_id": "mol", "owner_instance": "02", "goal": "round2",
        "actions_executed": ["diversity"], "artifacts": [], "findings": [],
        "stop_reason": "done", "inputs": [],
    })
    assert bad_second["ok"] is False
    assert "previous artifact" in bad_second["error"]


def test_receipt_correction_preserves_history_and_restores_previous(tmp_path):
    workspace = _workspace(tmp_path, "01")
    first = record_iteration(workspace, {
        "project_id": "p", "owner_instance": "01", "goal": "valid",
        "actions_executed": ["audit"], "artifacts": ["a.md"], "findings": ["valid"],
        "stop_reason": "stage done", "delivery_confirmed": True,
    })
    second = record_iteration(workspace, {
        "project_id": "p", "owner_instance": "01", "goal": "premature",
        "inputs": ["a.md"], "actions_executed": ["audit"], "artifacts": ["b.md"],
        "findings": ["not final"], "stop_reason": "wrong", "delivery_confirmed": True,
    })
    corrected = invalidate_receipt(
        workspace, "p", second["receipt"]["receipt_id"], reason="premature reconciliation",
        evidence=["iteration_llm_check.satisfied=false"], restore_status="completed",
    )
    assert corrected["ok"] is True
    assert latest_receipt(workspace, "p").receipt_id == first["receipt"]["receipt_id"]
    receipts = list((tmp_path / "workspace/share/projects/p/governance/receipts").glob("*.json"))
    assert len(receipts) == 2


def test_issue_dedup_and_evolution_promotion_gate(tmp_path):
    workspace = _workspace(tmp_path, "05")
    params = {
        "summary": "报告写了下一步但没有执行", "category": "planning",
        "severity": "high", "evidence": ["task-a/log.jsonl"], "instance_id": "02",
    }
    first = record_issue(workspace, params)
    second = record_issue(workspace, {**params, "evidence": ["task-b/log.jsonl"]})
    assert first["issue"]["issue_id"] == second["issue"]["issue_id"]
    assert second["issue"]["occurrences"] == 2
    unchanged = record_issue(workspace, {**params, "evidence": ["task-b/log.jsonl"]})
    assert unchanged["status"] == "unchanged"
    assert unchanged["issue"]["occurrences"] == 2
    assert len(Path(unchanged["path"]).read_text(encoding="utf-8").splitlines()) == 2
    experiment = start_experiment(workspace, {
        "issue_id": first["issue"]["issue_id"],
        "hypothesis": "显式 NextAction 状态能阻止假续跑",
        "intervention": "增加 Receipt 和 queue ack",
        "baseline": {"queued": False},
        "success_criteria": ["真实 task id", "回归通过"],
        "tests": ["test_project_round_handoff_and_queue_ack"],
    })
    denied = decide_experiment(workspace, {
        "experiment_id": experiment["experiment"]["experiment_id"],
        "decision": "promoted", "evidence": ["pytest"], "regression_passed": True,
        "criteria_results": {"真实 task id": True, "回归通过": False},
    })
    assert denied["ok"] is False
    promoted = decide_experiment(workspace, {
        "experiment_id": experiment["experiment"]["experiment_id"],
        "decision": "promoted", "evidence": ["pytest", "canary"], "regression_passed": True,
        "criteria_results": {"真实 task id": True, "回归通过": True},
    })
    assert promoted["promoted"] is True


def test_two_slot_scheduler_is_a_hard_gate(tmp_path):
    root = str(tmp_path / "workspace")
    state = set_active_slots(root, ["03", "05"], reason="test")
    assert state["active_slots"] == ["03", "05"]
    assert_start_allowed(root, "03")
    with pytest.raises(RuntimeError):
        assert_start_allowed(root, "01")
    with pytest.raises(ValueError):
        set_active_slots(root, ["01", "02", "03"])


def test_runtime_signal_detector_only_records_explicit_failures():
    clean = detect_signals(instance_id="01", files=["step.png"], event_types=["browser_screenshot"], result={"ok": True})
    assert clean == []
    issues = detect_signals(
        instance_id="01", expected_outputs=True, files=[], event_types=["browser_screenshot"],
        result={"ok": False, "status": "failed", "delivery_required": True, "delivery_confirmed": False},
    )
    assert {item["category"] for item in issues} == {"event", "verification", "delivery"}


def test_runtime_signal_detector_finds_three_identical_rounds():
    issues = detect_signals(
        instance_id="02", files=["r3.pdf"], event_types=["analyze"],
        prior_event_types=["analyze", "analyze"], result={"ok": True},
    )
    assert len(issues) == 1
    assert issues[0]["category"] == "planning"


def test_declarative_protocol_records_receipt_before_queue(tmp_path):
    workspace = _workspace(tmp_path, "02")
    calls = []

    async def enqueue(title, request, parent):
        calls.append((title, request, parent))
        return {"task_id": "queued-task"}

    result = asyncio.run(apply_transition(
        instance_id="02", workspace=workspace, title="mol",
        event_types={"molecular_generation_benchmark"}, files=["candidates.csv"],
        parent_user_request="研究分子生成", enqueue_fn=enqueue,
    ))
    assert result["continued"] is True
    assert result["queue"]["task_id"] == "queued-task"
    assert calls and "molecular_diversity_benchmark" in calls[0][1]
    receipt = latest_receipt(workspace, "molecular_generation")
    assert receipt is not None and receipt.iteration == 1

    # A later protocol cycle appends to project history.  Its protocol-local
    # step number must never overwrite or reject an earlier project receipt.
    repeated = asyncio.run(apply_transition(
        instance_id="02", workspace=workspace, title="mol-repeat",
        event_types={"molecular_generation_benchmark"}, files=["candidates-2.csv"],
        parent_user_request="继续研究分子生成", enqueue_fn=enqueue,
    ))
    assert repeated["continued"] is True
    assert latest_receipt(workspace, "molecular_generation").iteration == 2


def test_governance_events_are_registered():
    names = {item[0] for item in get_all_events()}
    assert {
        "select_context", "record_iteration", "request_next_action", "record_issue",
        "start_evolution_experiment", "decide_evolution_experiment", "observe_evolution_signals",
        "campaign_status", "create_campaign", "enqueue_campaign_work", "pause_campaign", "cancel_campaign",
    } <= names
