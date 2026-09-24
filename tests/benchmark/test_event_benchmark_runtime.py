from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from partner.application import PartnerApplicationService
from partner.benchmark.event_runtime import BenchmarkProtocolStore, BenchmarkRunStore
from partner.event_fabric import build_catalog
from partner.event_flows import build_flow_registry
from partner.events import benchmark as events
from partner.runtime.event_worker import EventWorker


class FakeAgent:
    last_usage = {"total_tokens": 11}

    def chat(self, _prompt, purpose="chat", **_kwargs):
        values = {
            "intent_observe": '{"goal":"运行冻结实验","route":"project","reason":"明确"}',
            "intent_counter_read": '{"goal":"运行冻结实验","route":"project","reason":"边界明确"}',
            "intent_synthesize": '{"goal":"运行冻结实验","route":"project_iteration","dispatch_target":"molecular_generation","warm_reply":"开始测试","payload":{}}',
            "project_hypothesis_propose": '{"hypotheses":[{"id":"frozen","claim":"运行冻结实验","event_type":"project.agent_action","expected_observation":"产生指标","disproof":"无指标","risk":"low","required_evidence":["指标"]}]}',
            "project_hypothesis_critic": '{"accepted":["frozen"],"rejected":[],"unknowns":[],"reason":"协议允许"}',
            "project_action_select": '{"selected_id":"frozen","selected_event":"project.agent_action","reason":"协议冻结","success_criteria":["指标"],"evidence_contract":{},"rollback":"删除产物"}',
            "project_outcome_reflect": '{"supported":[],"rejected":[],"unknown":["缺少领域指标"],"business_delta":false,"next_question":"补齐指标","failure_class":"project"}',
            "message_compose": "Benchmark 已完成，结果以冻结报告为准。",
            "message_critic": '{"accepted":true,"problems":[],"revised_message":""}',
            "message_factcheck": '{"unsupported_claims":[]}',
        }
        return values.get(purpose, '{}')

    def execute_task(self, prompt, **_kwargs):
        # The production action validator will honestly mark this as having no
        # business metric.  The benchmark must still resume and settle invalid.
        return "【业务产物】无\n【执行动作】运行测试\n【真实发现】缺少指标\n【未解决】领域数据未接入"

    def search_web(self, _query):
        return []


def workspace(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/partner_config.json").write_text(
        '{"agent":{"backend":"direct"},"core_v1":{"jev":{"mode":"disabled"}}}',
        encoding="utf-8")
    for instance in ("01", "02", "03"):
        (tmp_path / "instances" / instance).mkdir(parents=True)
    return tmp_path


def benchmark_params(tmp_path: Path) -> dict:
    data = tmp_path / "pk.csv"
    data.write_text("target,pK\na,7.1\n", encoding="utf-8")
    return {
        "mode": "benchmark",
        "execution_constraints": {
            "benchmark_protocol_id": "pk_target_feature_v1",
            "benchmark_inputs": {"dataset_path": str(data), "declared_feature": "target_family"},
            "benchmark_guardrail_results": {
                "no_target_leakage": True,
                "official_test_not_used_for_tuning": True,
                "within_budget": True,
                "artifacts_complete": True,
                "secondary_metrics_not_materially_worse": True,
            },
        },
    }


def test_catalog_and_registry_expose_benchmark_as_events_and_flows(tmp_path):
    catalog = build_catalog(workspace=tmp_path)
    registry = build_flow_registry()
    assert catalog.get("checkpoint.capture") is not None
    assert catalog.get("benchmark.paired_compare") is not None
    parent = registry.get("benchmark_experiment")
    subject = registry.get("benchmark_subject")
    assert parent.node("baseline_submit").event_type == "benchmark.variant_submit"
    assert parent.node("candidate_submit").event_type == "benchmark.variant_submit"
    assert len([n for n in subject.nodes if n.event_type == "checkpoint.capture"]) == 6
    assert not [n for n in (*parent.nodes, *subject.nodes) if catalog.get(n.event_type) is None]
    from partner.benchmark.flow_validation import validate_benchmark_flows
    protocol, _ = BenchmarkProtocolStore(tmp_path).load("pk_target_feature_v1")
    assert validate_benchmark_flows(protocol=protocol, parent=parent, subject=subject,
                                    catalog=catalog) == []


def test_protocol_preflight_requires_declared_inputs(tmp_path):
    root = workspace(tmp_path)
    protocol, _ = BenchmarkProtocolStore(root).load("pk_target_feature_v1")
    assert protocol.primary_metric["name"] == "rmse"
    ctx = SimpleNamespace(workspace=str(root))
    base = {
        "benchmark_run_id": "bench_missing", "benchmark_protocol_id": protocol.protocol_id,
        "run_context": {"run_mode": "benchmark", "benchmark_run_id": "bench_missing",
                        "benchmark_protocol_id": protocol.protocol_id},
        "intent_contract": {"benchmark": {"run_mode": "benchmark", "run_id": "bench_missing",
                                             "protocol_id": protocol.protocol_id, "inputs": {}}},
    }
    result = events.environment_preflight(ctx, base)
    assert result["ok"] is False
    assert set(result["semantic_output"]["missing_inputs"]) == {"dataset_path", "declared_feature"}


def test_pair_comparison_and_settlement_are_deterministic(tmp_path):
    root = workspace(tmp_path)
    ctx = SimpleNamespace(workspace=str(root))
    params = {
        "benchmark_run_id": "bench_pair", "benchmark_protocol_id": "pk_target_feature_v1",
        "run_context": {"run_mode": "benchmark", "benchmark_run_id": "bench_pair",
                        "benchmark_protocol_id": "pk_target_feature_v1"},
        "intent_contract": {"benchmark": {"run_mode": "benchmark", "run_id": "bench_pair",
            "protocol_id": "pk_target_feature_v1", "inputs": {},
            "guardrail_results": {"no_target_leakage": True,
                "official_test_not_used_for_tuning": True, "within_budget": True,
                "artifacts_complete": True, "secondary_metrics_not_materially_worse": True}}},
        "flow_outputs": {
            "baseline_metric": {"semantic_output": {"value": 1.0}},
            "candidate_metric": {"semantic_output": {"value": 0.5}},
            "baseline_collect": {"semantic_output": {
                "predictions": [{"sample_id": str(i), "y_true": 1.0, "y_pred": 2.0}
                                for i in range(8)],
                "metrics": {"mae": 1.0, "r2": 0.2},
                "guardrails": {"no_target_leakage": True,
                    "official_test_not_used_for_tuning": True, "within_budget": True}}},
            "candidate_collect": {"semantic_output": {
                "predictions": [{"sample_id": str(i), "y_true": 1.0, "y_pred": 1.5}
                                for i in range(8)],
                "metrics": {"mae": 0.5, "r2": 0.4},
                "guardrails": {"no_target_leakage": True,
                    "official_test_not_used_for_tuning": True, "within_budget": True}}},
        },
    }
    compared = events.paired_compare(ctx, params)
    assert abs(compared["semantic_output"]["effect"] - 0.5) < 1e-9
    assert compared["semantic_output"]["bootstrap_samples"] == 1000
    params["flow_outputs"]["paired_compare"] = compared
    expected = events.expectation_compare(ctx, params)
    assert expected["semantic_output"]["met"] is True
    params["flow_outputs"]["expectation_compare"] = expected
    params["flow_outputs"].update({
        "baseline_integrity": {"semantic_output": {"valid": True}},
        "candidate_integrity": {"semantic_output": {"valid": True}},
    })
    guardrails = events.guardrail_evaluate(ctx, params)
    secondary = next(row for row in guardrails["semantic_output"]["guardrails"]
                     if row["id"] == "secondary_metrics_not_materially_worse")
    assert secondary["status"] == "pass"
    assert secondary["derived_evidence"]["method"] == "parent_metric_comparison"
    params["flow_outputs"]["guardrail_evaluate"] = guardrails
    params["flow_outputs"].update({
        "jev_evaluate": {"semantic_output": {"status": "abstained"}},
        "llm_judge": {"semantic_output": {"status": "abstained"}},
        "execution_parity": {"semantic_output": {"valid": True}},
    })
    aggregate = events.aggregate(ctx, params)
    params["flow_outputs"]["aggregate"] = aggregate
    settled = events.benchmark_settlement(ctx, params)
    assert settled["semantic_output"]["decision"] == "confirmed"
    assert settled["semantic_output"]["authoritative_source"] == "deterministic_evaluators"


def test_application_test_flag_selects_benchmark_parent_flow(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    import partner.events.interaction as interaction
    monkeypatch.setattr(interaction, "intent_observe", lambda *_a, **_k: {"semantic_output": {}})
    monkeypatch.setattr(interaction, "intent_counter_read", lambda *_a, **_k: {"semantic_output": {}})
    monkeypatch.setattr(interaction, "intent_synthesize", lambda *_a, **_k: {
        "semantic_output": {"route": "direct_answer", "dispatch_target": "molecular_generation",
                            "warm_reply": "ok", "payload": {}}})
    opts = benchmark_params(root)
    result = PartnerApplicationService(root).submit(
        "运行冻结 pK 实验", channel="local", sender_id="test", persona_hint="01",
        project_id="molecular_generation", **opts)
    assert result.accepted and result.route == "benchmark_experiment"
    job = PartnerApplicationService(root).list_jobs(limit=1)[0]
    assert job["run_mode"] == "benchmark"
    assert job["benchmark_protocol_id"] == "pk_target_feature_v1"
    state = EventWorker(root, "01").store.load(job["flow_id"])
    assert state.flow_type == "benchmark_experiment"
    assert state.run_context["evaluation_visibility"] == "hidden_until_terminal"


def test_explicit_message_marker_selects_benchmark_without_llm_permission(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    import partner.events.interaction as interaction
    monkeypatch.setattr(interaction, "intent_observe", lambda *_a, **_k: {"semantic_output": {}})
    monkeypatch.setattr(interaction, "intent_counter_read", lambda *_a, **_k: {"semantic_output": {}})
    monkeypatch.setattr(interaction, "intent_synthesize", lambda *_a, **_k: {
        "semantic_output": {"route": "direct_answer", "dispatch_target": "",
                            "warm_reply": "讨论测试", "payload": {}}})
    opts = benchmark_params(root)
    constraints = dict(opts["execution_constraints"])
    constraints.pop("benchmark_protocol_id")
    result = PartnerApplicationService(root).submit(
        "/benchmark pk_target_feature_v1\n运行冻结 pK 实验",
        channel="local", sender_id="test", persona_hint="01",
        project_id="molecular_generation", execution_constraints=constraints)
    assert result.accepted and result.route == "benchmark_experiment"
    job = PartnerApplicationService(root).list_jobs(limit=1)[0]
    assert job["benchmark_protocol_id"] == "pk_target_feature_v1"


def test_public_wrapper_submits_through_application_contract(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    import partner.events.interaction as interaction
    monkeypatch.setattr(interaction, "intent_observe", lambda *_a, **_k: {"semantic_output": {}})
    monkeypatch.setattr(interaction, "intent_counter_read", lambda *_a, **_k: {"semantic_output": {}})
    monkeypatch.setattr(interaction, "intent_synthesize", lambda *_a, **_k: {
        "semantic_output": {"route": "direct_answer", "dispatch_target": "molecular_generation",
                            "warm_reply": "ok", "payload": {}}})
    from partner.benchmark.wrapper import PartnerBenchmarkWrapper
    data = root / "pk.csv"
    data.write_text("target,pK\na,7.1\n", encoding="utf-8")
    result = PartnerBenchmarkWrapper(root).submit(
        protocol_id="pk_target_feature_v1", request="运行冻结实验",
        instance_id="01", project_id="molecular_generation",
        inputs={"dataset_path": str(data), "declared_feature": "target_family"})
    assert result.accepted and result.flow_type == "benchmark_experiment"
    assert result.benchmark_run_id.startswith("bench_")


def test_child_subject_view_excludes_hidden_guardrail_labels(tmp_path):
    root = workspace(tmp_path)
    store = BenchmarkRunStore(root, "bench_isolation")
    assert store.directory.is_dir()
    protocol, _ = BenchmarkProtocolStore(root).load("pk_target_feature_v1")
    from partner.benchmark.event_runtime import public_subject_view
    view = public_subject_view(protocol, {"dataset_path": "/data.csv",
                                          "declared_feature": "family"}, "candidate")
    serialized = json.dumps(view)
    assert "guardrail_results" not in serialized
    assert "expected_effect" not in serialized


def test_benchmark_parent_runs_two_subject_children_and_closes(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    import partner.events.interaction as interaction
    monkeypatch.setattr(interaction, "intent_observe", lambda *_a, **_k: {"semantic_output": {}})
    monkeypatch.setattr(interaction, "intent_counter_read", lambda *_a, **_k: {"semantic_output": {}})
    monkeypatch.setattr(interaction, "intent_synthesize", lambda *_a, **_k: {
        "semantic_output": {"route": "project_iteration",
                            "dispatch_target": "molecular_generation",
                            "warm_reply": "开始", "payload": {}}})
    opts = benchmark_params(root)
    submitted = PartnerApplicationService(root).submit(
        "运行冻结 pK 实验", channel="local", sender_id="test", persona_hint="01",
        project_id="molecular_generation", report_policy="none", **opts)
    worker = EventWorker(root, "01")
    worker.adapter = FakeAgent()
    job = worker.next_job()
    assert job is not None
    asyncio.run(worker._run_job_to_terminal(job))
    worker._release_claim()
    final = next(row for row in PartnerApplicationService(root).list_jobs(limit=10)
                 if row["job_id"] == submitted.job_id)
    assert final["status"] == "completed", final
    run_dir = root / "state/benchmarks/runs" / final["benchmark_run_id"]
    assert (run_dir / "arms/baseline/child_flow.json").is_file()
    assert (run_dir / "arms/candidate/child_flow.json").is_file()
    assert (run_dir / "settlement.json").is_file()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "completed"
    assert manifest["settlement"] in {"invalid", "inconclusive", "falsified",
                                       "insufficient_effect", "confirmed"}
