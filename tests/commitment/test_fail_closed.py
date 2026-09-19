"""Invariant 10: missing, corrupt, or contaminated evidence is never a zero or a win."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from partner.commitment import models as M
from partner.commitment.evaluator import (
    ArtifactIsolation, NotIndependentError,
)
from conftest import ScriptedExecutor, build_bet, make_config


class SelfReportExecutor(ScriptedExecutor):
    """An executor that offers its own verdict as the artifact to be measured."""

    def execute(self, *, bet, candidate, workspace):
        out_dir = Path(workspace) / "state" / "execution" / bet.bet_id
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / "llm_verdict.json"
        artifact.write_text(json.dumps({"verdict": "the candidate worked"}), encoding="utf-8")
        from partner.commitment.store import file_sha256
        now = 1_700_000_000.0
        return M.ExecutionReceipt(
            receipt_id="rcpt_selfreport", bet_id=bet.bet_id,
            requested_action=bet.selected_action, executed_action=bet.selected_action,
            executor_id="scripted", executor_version="1.0.0", started_epoch=now,
            finished_epoch=now + 0.1, status="completed", exit_code=0,
            artifacts=(str(artifact),), artifact_hashes={str(artifact): file_sha256(artifact)},
            log_ref="", data_hash="", budget_consumed={}, human_intervention=False,
            idempotency_key="idem-self-report")


@pytest.mark.parametrize("mode,expected_state,expected_validity", [
    ("missing", "INVALID", "missing"),
    ("mutated", "INVALID", "invalid"),
    ("empty", "INVALID", "invalid"),
])
def test_bad_evidence_fails_closed(workspace, mode, expected_state, expected_validity):
    runner, store, _ = build_bet(workspace, executor=ScriptedExecutor(
        workspace=workspace, values=[12.0, 13.0], mode=mode))
    result = runner.run()
    assert result.state == expected_state
    assert result.measurement is not None
    assert result.measurement.validity == expected_validity
    assert result.measurement.missing_reason, "an unusable measurement must state why"
    assert result.settlement is None, "no settlement is produced from unusable evidence"
    assert store.list_artifacts("experience") == []


def test_a_missing_artifact_is_not_scored_as_zero(workspace):
    runner, _, _ = build_bet(workspace, executor=ScriptedExecutor(
        workspace=workspace, values=[12.0], mode="missing"))
    result = runner.run()
    assert all(m.value is None for m in result.measurement.measurements)
    assert not any(m.is_valid for m in result.measurement.measurements)


def test_agent_self_report_cannot_be_measured(workspace):
    runner, _, _ = build_bet(workspace, executor=SelfReportExecutor(
        workspace=workspace, values=[12.0]))
    result = runner.run()
    assert result.state == "INVALID"
    assert "fail_closed" in result.notes
    assert "self-report" in result.reason


def test_isolation_refuses_verdict_like_names():
    isolation = ArtifactIsolation()
    with pytest.raises(NotIndependentError, match="self-report"):
        isolation.check_path("/tmp/whatever/llm_verdict.json")
    with pytest.raises(NotIndependentError):
        isolation.check_path("/tmp/whatever/success_claim.json")


def test_isolation_refuses_paths_outside_declared_roots(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    isolation = ArtifactIsolation(allowed_roots=[root])
    inside = root / "result.json"
    inside.write_text("{}", encoding="utf-8")
    assert isolation.check_path(inside) == inside
    with pytest.raises(NotIndependentError, match="outside the declared artifact roots"):
        isolation.check_path(tmp_path / "elsewhere" / "result.json")


def test_measurement_rejects_agent_mutated_artifacts():
    with pytest.raises(M.ContractError, match="not independent of the agent"):
        M.OutcomeMeasurement(
            measurement_id="m", bet_id="b", receipt_id="r", metric="mean", value=1.0,
            direction="increase", unit="", measured_by="ev", evaluator_version="1",
            evidence_refs=("a.json",), missing_reason="", validity="valid",
            agent_artifacts_unchanged=False, artifact_hashes_before={}, artifact_hashes_after={})


def test_measurement_rejects_changed_hashes():
    with pytest.raises(M.ContractError, match="hashes differ"):
        M.OutcomeMeasurement(
            measurement_id="m", bet_id="b", receipt_id="r", metric="mean", value=1.0,
            direction="increase", unit="", measured_by="ev", evaluator_version="1",
            evidence_refs=("a.json",), missing_reason="", validity="valid",
            agent_artifacts_unchanged=True, artifact_hashes_before={"a": "1"},
            artifact_hashes_after={"a": "2"})


def test_receipt_that_does_not_name_the_protocol_artifact_is_refused(workspace):
    runner, _, _ = build_bet(workspace, executor=ScriptedExecutor(
        workspace=workspace, values=[12.0]))
    bet = None

    class NoArtifact:
        def execute(self, *, bet, candidate, workspace):
            return M.ExecutionReceipt(
                receipt_id="r", bet_id=bet.bet_id, requested_action=bet.selected_action,
                executed_action=bet.selected_action, executor_id="e", executor_version="1",
                started_epoch=1.0, finished_epoch=2.0, status="completed", exit_code=0,
                artifacts=("something_else.json",), artifact_hashes={"something_else.json": "x"},
                log_ref="", data_hash="", budget_consumed={}, human_intervention=False,
                idempotency_key="k")

    runner.executor = NoArtifact()
    result = runner.run()
    assert result.state == "INVALID"
    assert "does not name the declared artifact" in result.reason
