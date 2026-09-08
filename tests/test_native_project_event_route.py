import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from partner.planner.batch_planner import (
    MicroPlan, _manual_preflight_plan, _native_project_execution_plan,
)
from partner.mind.harness import _compose_parsed_from_results


def _workspace(tmp_path: Path, instance_id: str) -> Path:
    root = tmp_path / "workspace"
    config = root / "config/partner_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"runtime": {
        "instance_native_autonomy": True,
        "instance_native_enabled_instances": ["01", "02", "03", "04", "05"],
    }}), encoding="utf-8")
    workspace = root / "instances" / instance_id
    workspace.mkdir(parents=True)
    return workspace


@pytest.mark.parametrize(("iid", "project_id", "event_type"), [
    ("01", "xiaohongshu_operations", "continuous_project_step"),
    ("02", "molecular_generation", "molecular_generation_benchmark"),
    ("03", "molecular_dynamics_study", "continuous_project_step"),
    ("04", "literature_github_learning", "external_knowledge_scout"),
    ("05", "hermes_partner_explore", "continuous_project_step"),
])
def test_native_project_marker_routes_to_real_event(tmp_path, iid, project_id, event_type):
    workspace = _workspace(tmp_path, iid)
    plan = _native_project_execution_plan(
        str(workspace),
        f"[instance_native=true] [native_kind=project] [project_id={project_id}]",
        str(workspace / "state/tasks/t"),
    )
    assert plan is not None
    assert [step.event_type for step in plan.plan] == [event_type]
    assert plan.plan[0].parameters["native_project_id"] == project_id


def test_native_route_rejects_cross_project_marker(tmp_path):
    workspace = _workspace(tmp_path, "04")
    with pytest.raises(ValueError, match="does not match"):
        _native_project_execution_plan(
            str(workspace),
            "[instance_native=true] [native_kind=project] [project_id=molecular_generation]",
            str(workspace / "state/tasks/t"),
        )


def test_05_explicit_code_candidate_intent_overrides_rotation(tmp_path):
    workspace = _workspace(tmp_path, "05")
    plan = _native_project_execution_plan(
        str(workspace),
        "形成生产代码 Candidate 并隔离验证 "
        "[instance_native=true] [native_kind=project] [project_id=hermes_partner_explore]",
        str(workspace / "state/tasks/t"),
    )
    assert plan.plan[0].event_type == "continuous_project_step"
    assert plan.plan[0].parameters["strategy_id"] == "05_code_candidate_autonomous"


def test_04_explicit_external_learning_intent_routes_to_llm_guided_scout(tmp_path):
    workspace = _workspace(tmp_path, "04")
    plan = _native_project_execution_plan(
        str(workspace),
        "到外部 GitHub 仓库和论文文献中主动学习并拉取真实资料 "
        "[instance_native=true] [native_kind=project] [project_id=literature_github_learning]",
        str(workspace / "state/tasks/t"),
    )
    assert plan.plan[0].event_type == "external_knowledge_scout"


def test_05_duplicate_learning_uses_behavioral_code_candidate_not_generic_fixture(tmp_path):
    workspace = _workspace(tmp_path, "05")
    episode_id = "episode_duplicate05"
    state = tmp_path / "workspace/share/mind/governance/episodes" / episode_id / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "episode_id": episode_id,
        "task_id": "task_duplicate05",
        "instance_id": "05",
        "failure_classes": ["outcome.duplicate_semantic_result"],
    }), encoding="utf-8")
    message = (
        "[learning_for_task=task_duplicate05] "
        "[instance_native=true] [native_kind=learning] "
        "[project_id=hermes_partner_explore]"
    )
    class Registry:
        @staticmethod
        def get(_event_type):
            return SimpleNamespace(produces_artifact=True)
    plan = _manual_preflight_plan(
        MicroPlan(plan=[], expected_artifacts=[]), registry=Registry(),
        workspace=str(workspace), working_dir=str(workspace / "state/tasks/t"),
        user_message=message,
    )
    final = plan.plan[-1]
    assert final.event_type == "continuous_project_step"
    assert final.parameters["strategy_id"] == "05_code_candidate_autonomous"


def test_native_event_envelope_keeps_measured_summary_and_files():
    ctx = SimpleNamespace(event=SimpleNamespace(type=SimpleNamespace(value="batch_plan")))
    parsed = _compose_parsed_from_results(ctx, {
        "native_project_action": {
            "ok": True,
            "result": {
                "ok": True,
                "summary": "03_md_integrator_smoke 已执行；simulations_executed=1",
                "files": ["/tmp/md_metrics.json"],
                "path": "/tmp/md_metrics.json",
            },
        },
    })
    assert parsed["findings"] == ["03_md_integrator_smoke 已执行；simulations_executed=1"]
    assert parsed["files"] == "/tmp/md_metrics.json"

    direct = _compose_parsed_from_results(ctx, {
        "native_project_action": {
            "ok": True,
            "summary": "05_event_contract_inventory 已执行；sources_read=4",
            "result": {"business_metrics": {"sources_read": 4}},
            "files": ["/tmp/inventory.json"],
        },
    })
    assert direct["findings"] == ["05_event_contract_inventory 已执行；sources_read=4"]
    assert direct["files"] == "/tmp/inventory.json"

    molecular = _compose_parsed_from_results(ctx, {
        "native_project_action": {
            "ok": True,
            "files": ["/tmp/report.pdf"],
            "result": {"ok": True, "metrics": {"unique_count": 85,
                                                   "mean_qed": 0.537781},
                       "files": ["/tmp/report.pdf"]},
        },
    })
    assert "unique_count" in molecular["findings"][0]
    assert molecular["findings"] != ["已完成本地微计划执行"]
