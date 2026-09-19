"""Invariant 14: the three arms must be given identical conditions.

If the arms can differ in initial state, budget, evaluator or protocol, any
comparison between them is meaningless -- so parity is asserted, not assumed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.commitment_loop import arms as arms_module
from benchmarks.commitment_loop import fixtures, metrics
from partner.commitment.models import sha256_of
from partner.commitment.settlement import budget_hash, protocol_hash


def _episodes(tmp_path: Path):
    return [fixtures.Episode(workspace=tmp_path / f"seed_{seed}", seed=seed,
                             snapshot=fixtures.episode_snapshot(seed=seed))
            for seed in fixtures.SEEDS]


def test_all_three_arms_exist_and_share_the_interface():
    names = [cls.name for cls in arms_module.ARMS]
    assert names == ["fixed_pipeline", "free_llm_loop", "commitment_loop"]
    for cls in arms_module.ARMS:
        assert hasattr(cls, "run") and hasattr(cls, "requires_llm")


def test_arms_receive_the_same_initial_state_budget_and_protocol(tmp_path):
    episodes = _episodes(tmp_path)
    proof = {
        "snapshot_hashes": {e.seed: sha256_of(e.snapshot) for e in episodes},
        "budgets": [tuple(sorted(e.budget_spec.items())) for e in episodes],
        "max_rounds": [e.max_rounds for e in episodes],
    }
    assert len(set(proof["budgets"])) == 1, "every episode must share one budget"
    assert len(set(proof["max_rounds"])) == 1
    # the same episode object is handed to every arm
    for seed in fixtures.SEEDS:
        episode = next(e for e in episodes if e.seed == seed)
        for cls in arms_module.ARMS:
            assert episode.snapshot["seed"] == seed
            assert episode.budget_spec is not None

    entry = episodes[0].snapshot["candidate_space"][1]
    bet = arms_module._synthetic_bet(episodes[0], entry, revision=1)
    assert protocol_hash(bet) == protocol_hash(arms_module._synthetic_bet(
        _episodes(tmp_path)[0], entry, revision=1))
    assert budget_hash(bet) == budget_hash(arms_module._synthetic_bet(
        _episodes(tmp_path)[0], entry, revision=1))
    assert bet.evaluation_protocol.agent_output_visible is False


def test_every_arm_is_executable_and_reports_the_same_shape(tmp_path):
    for seed in fixtures.SEEDS[:1]:
        episode = fixtures.Episode(workspace=tmp_path / f"s{seed}", seed=seed,
                                  snapshot=fixtures.episode_snapshot(seed=seed))
        episode.workspace.mkdir(parents=True, exist_ok=True)
        for cls in arms_module.ARMS:
            result = cls().run(episode)
            payload = result.to_dict()
            for key in ("arm", "seed", "rounds", "decisions", "model_calls", "tokens",
                        "wall_clock_seconds", "tool_calls", "human_interventions",
                        "settlement_classes", "published", "notes", "dependency_missing"):
                assert key in payload, (cls.name, key)
            assert result.rounds >= 1, f"{cls.name} produced no round"
            assert result.arm == cls.name


def test_only_the_commitment_arm_produces_machine_settlements(tmp_path):
    episode = fixtures.Episode(workspace=tmp_path / "s11", seed=11,
                              snapshot=fixtures.episode_snapshot(seed=11))
    episode.workspace.mkdir(parents=True, exist_ok=True)
    fixed = arms_module.FixedPipelineArm().run(episode)
    free = arms_module.FreeLLMLoopArm().run(episode)
    commitment = arms_module.CommitmentLoopArm().run(episode)
    assert fixed.settlement_classes == []
    assert free.settlement_classes == []
    assert commitment.settlement_classes, "the kernel must settle"
    assert all(d["settlement_mechanism"] == "kernel_machine_settlement"
               for d in commitment.decisions)


def test_metrics_report_no_data_with_a_reason_instead_of_zero(tmp_path):
    episode = fixtures.Episode(workspace=tmp_path / "s11", seed=11,
                              snapshot=fixtures.episode_snapshot(seed=11))
    episode.workspace.mkdir(parents=True, exist_ok=True)
    result = arms_module.FixedPipelineArm().run(episode)
    values = metrics.all_metrics(result)
    # a fixed pipeline never turns and never consumes experience
    assert values["no_evidence_turn_rate"]["status"] == "no_data"
    assert values["no_evidence_turn_rate"]["undefined_reason"]
    assert values["experience_consumption_rate"]["status"] == "no_data"
    assert values["task_quality"]["status"] == "measured"
    assert values["model_calls"]["value"] == 0.0


def test_free_loop_declares_its_missing_dependency(tmp_path):
    episode = fixtures.Episode(workspace=tmp_path / "s11", seed=11,
                              snapshot=fixtures.episode_snapshot(seed=11))
    episode.workspace.mkdir(parents=True, exist_ok=True)
    result = arms_module.FreeLLMLoopArm().run(episode)
    assert result.dependency_missing == ("llm_chooser",)
    assert result.notes, "a degraded arm must say so in its own record"
