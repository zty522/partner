from pathlib import Path
from types import SimpleNamespace
import json

from partner.benchmark.event_runtime import BenchmarkProtocolStore, public_subject_view
from partner.event_fabric.catalog import build_catalog
from partner.event_flows.registry import build_flow_registry
from partner.benchmark.flow_validation import validate_benchmark_flows
from partner.events import loop_bench


class FakeAdapter:
    last_usage = {"prompt_tokens": 10, "completion_tokens": 5}
    def chat_once(self, prompt, **_kwargs):
        if "adversarial critic" in prompt:
            return '{"surviving_ids":["good"],"recommended_id":"good","objections":[]}'
        return ('{"candidate_ids":["bad","good"],"hypothesis":"h",'
                '"expected_observation":"o","failure_condition":"f","evidence_used":[]}')


def _ctx(tmp_path):
    return SimpleNamespace(workspace=str(tmp_path), working_dir=str(tmp_path / "work"),
                           adapter=FakeAdapter())


def _params(task_path, policy="full_partner"):
    view = {"inputs": {"task_path": str(task_path)}, "arm_configuration": {"policy": policy},
            "budget": {"max_seconds": 30}}
    return {"intent_contract": {"benchmark_subject_view": view}, "flow_outputs": {}}


def test_public_view_does_not_expose_evaluator_inputs(tmp_path):
    protocol, _ = BenchmarkProtocolStore(tmp_path).load("partner_loop_full_vs_single_v1")
    view = public_subject_view(protocol, {"task_path": "/public/task.json",
        "arm_runner_path": "/public/run.py", "hidden_oracle_path": "/secret.json"}, "candidate")
    assert view["arm_configuration"]["policy"] == "full_partner"
    assert "arm_policies" not in view["task"]
    assert "hidden_oracle_path" not in view["inputs"]


def test_blind_subject_flow_is_registered_and_valid(tmp_path):
    protocol, _ = BenchmarkProtocolStore(tmp_path).load("partner_loop_full_vs_single_v1")
    flows, catalog = build_flow_registry(), build_catalog(workspace=tmp_path)
    assert validate_benchmark_flows(protocol=protocol, parent=flows.get("benchmark_experiment"),
        subject=flows.get(protocol.subject_flow), catalog=catalog) == []


def test_full_policy_critic_can_change_blind_selection(tmp_path):
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"task_id":"t", "scenario":"x", "candidates":[
        {"id":"bad","description":"b"},{"id":"good","description":"g"}]}), encoding="utf-8")
    params = _params(task)
    inspected = loop_bench.task_inspect(_ctx(tmp_path), params)
    params["flow_outputs"]["inspect"] = inspected
    proposed = loop_bench.candidate_propose(_ctx(tmp_path), params)
    params["flow_outputs"]["propose"] = proposed
    criticised = loop_bench.candidate_critic(_ctx(tmp_path), params)
    params["flow_outputs"]["critic"] = criticised
    selected = loop_bench.candidate_select(_ctx(tmp_path), params)
    assert selected["semantic_output"]["selected_id"] == "good"


def test_public_task_rejects_oracle_key(tmp_path):
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"task_id":"t", "answer":"a", "candidates":[
        {"id":"a"},{"id":"b"}]}), encoding="utf-8")
    result = loop_bench.task_inspect(_ctx(tmp_path), _params(task))
    assert result["status"] == "failed" and "leaks" in result["error"]


def test_public_task_rejects_nested_score_key(tmp_path):
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"task_id":"t", "candidates":[
        {"id":"a","metadata":{"score":1}},{"id":"b"}]}), encoding="utf-8")
    assert loop_bench.task_inspect(_ctx(tmp_path), _params(task))["status"] == "failed"
