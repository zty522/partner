from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from partner.cognition.world_model import TransformerHypothesisProvider, WorldModelEngine
from partner.cognition.world_model.benchmark import compare_benchmarks, run_shadow_benchmark
from partner.cognition.world_model.episode_shadow import load_episode_trajectories
from partner.cognition.world_model.stress_benchmark import (
    compare_stress_benchmarks,
    run_stress_benchmark,
)
from partner.governance.candidate_skills import register_candidate_skill
from partner.v2.world_model_events import atomic_world_model_matched_evaluation


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "instances/05/state/tasks").mkdir(parents=True)
    return root


def test_partner_runtime_has_no_incubator_import_or_path_dependency():
    package = Path(__file__).resolve().parents[1] / "partner"
    offenders = []
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "/mnt/e/work/partner_test" in text or "from bdk" in text or "import bdk" in text:
            offenders.append(str(path.relative_to(package)))
    assert offenders == []


def test_transformer_provider_is_a_real_trainable_provider():
    provider = TransformerHypothesisProvider(seed=17, d_model=16)
    summary = provider.fit_synthetic(examples=80, epochs=5)
    assert summary["examples"] == 80
    engine = WorldModelEngine(provider=provider)
    result = engine.observe([0, .1, .25, .4, .6, .8, 1],
                            [0, .95, 0, -.95, .95, -.95, 0], domain_id="cyclic")
    assert result["provider_id"] == "transformer_hypothesis_v1"
    assert result["hypotheses_considered"] >= 3


def test_transformer_backs_off_and_admits_sparse_evidence_limit():
    provider = TransformerHypothesisProvider(seed=17, d_model=16)
    provider.fit_synthetic(examples=80, epochs=5)
    result = WorldModelEngine(provider=provider).observe(
        [0, .2, .5, .8, 1], [0, .8, -.1, .7, 0], domain_id="sparse")
    assert result["provider_diagnostics"]["top_k"] == 4
    assert result["evidence_status"] == "insufficient_evidence"
    assert set(result["provider_diagnostics"]["selected_families"]) == set(provider.FAMILIES)


def test_three_domain_shadow_benchmark_is_matched_and_event_first(tmp_path):
    baseline = run_shadow_benchmark(provider_kind="library", output_dir=tmp_path / "bench")
    candidate = run_shadow_benchmark(provider_kind="transformer", output_dir=tmp_path / "bench")
    comparison = compare_benchmarks(baseline, candidate)
    assert baseline["dataset_digest"] == candidate["dataset_digest"]
    assert len(baseline["domains"]) == len(candidate["domains"]) == 3
    assert baseline["event_ledger"]["event_count"] == 18
    assert candidate["event_ledger"]["event_count"] == 18
    assert comparison["criteria"]["event_ledgers_valid"] is True


def test_multi_seed_pressure_benchmark_has_hard_recall_and_abstention_gates(tmp_path):
    baseline = run_stress_benchmark(provider_kind="library", output_dir=tmp_path, seeds=2)
    candidate = run_stress_benchmark(provider_kind="transformer", output_dir=tmp_path, seeds=2)
    comparison = compare_stress_benchmarks(baseline, candidate)
    assert baseline["dataset_digest"] == candidate["dataset_digest"]
    assert candidate["metrics"]["case_count"] == 24
    assert comparison["criteria"]["expected_family_proposal_recall"] is True
    assert comparison["criteria"]["sparse_cases_admit_insufficient_evidence"] is True
    assert comparison["criteria"]["event_ledgers_valid"] is True


def test_real_episode_projection_is_read_only_and_requires_variation(tmp_path):
    root = tmp_path / "episodes"
    for index in range(12):
        path = root / f"episode_{index:02d}" / "state.json"
        path.parent.mkdir(parents=True)
        path.write_text(__import__("json").dumps({
            "schema_version": 3, "episode_id": f"episode_{index:02d}",
            "instance_id": "04", "project_id": "literature_github_learning",
            "status": "completed" if index % 3 else "failed",
            "reduced_at": f"2026-08-30T00:{index:02d}:00+08:00",
            "reward_vector": {"scalar": [0.0, 0.9, 1.0][index % 3],
                              "hard_gate_passed": True, "policy_eligible": index % 3 != 0},
            "tool_calls": [], "model_calls": [],
        }), encoding="utf-8")
    groups, digest = load_episode_trajectories(root)
    assert len(groups) == 1 and groups[0]["curve_shadow_eligible"] is True
    assert groups[0]["count"] == 12
    assert len(digest) == 64


def test_candidate_evaluation_executes_candidate_through_event_contract(tmp_path):
    root = _workspace(tmp_path)
    candidate_id = "candidate_transformer_shadow_test"
    register_candidate_skill(str(root), {
        "candidate_id": candidate_id, "title": "transformer shadow", "status": "candidate",
        "artifact_type": "model_policy", "project_id": "agent_self_evolution",
        "source_episode_ids": ["episode_frozen_benchmark"], "success_criteria": ["matched"],
        "applicability": ["shadow benchmark"],
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "world_model_transformer_shadow_benchmark",
                               "allowed_instances": ["05"], "default_params": {"seed": 20260830}},
        "evaluation_contract": {"ready": True, "kind": "event",
                                "event_type": "world_model_matched_evaluation",
                                "allowed_instances": ["05"], "default_params": {}},
    })
    working = root / "instances/05/state/tasks/eval"
    working.mkdir(parents=True)
    task = SimpleNamespace(working_dir=str(working))
    ctx = SimpleNamespace(workspace=str(root / "instances/05"), working_dir=str(working),
                          task_instance=task)
    result = atomic_world_model_matched_evaluation(ctx, {
        "candidate_id": candidate_id, "execution_id": "exec_matched_one", "instance_id": "05",
    })
    assert result["ok"] is True
    assert result["comparison"]["criteria"]["matched_dataset"] is True
    assert result["production_mutation"] is False
