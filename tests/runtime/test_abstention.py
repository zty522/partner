"""Abstention: the harness declining to wager, as its own terminal state.

These tests pin the whole path: a pure rule that reads same-class history, a real kernel
terminal state (``abstained``) that is not a flavour of ``blocked``/``inconclusive``, a
frozen bet that declares the abstention, a settlement with **no** expectation outcomes and
**no** improvement claim, an experience at ``level="abstention"``, and a reply line that
states the decision and the evidence behind it.  Nothing is executed while abstaining:
the receipts and the artifact say so in machine-readable form.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from partner.application import experience_prior as ep  # noqa: E402
from partner.index.job_repository import init as init_jobs  # noqa: E402

PROJECT_ID = "molecular_generation"
TOKEN = "abstain_trace_02_1700000001"


# --- fixtures ---------------------------------------------------------------

def _settlement(class_name: str, *, minute: int) -> dict:
    return {"settlement_id": f"stl_{class_name}_{minute}", "bet_id": f"bet_{minute}",
            "settlement_class": class_name, "expectations_met": class_name == "supported",
            "improvement_over_baseline": class_name == "supported",
            "supported_claim": "improvement_over_baseline" if class_name == "supported" else "none",
            "publish_eligible": False,
            "publish_blockers": ["environment_not_publishable:isolated_sample",
                                 "isolated_sample_only"],
            "settled_at": f"2026-09-19T20:{minute:02d}:00"}


def _fabricate_same_class(workspace: Path, class_key: str, history: list[str]) -> list[str]:
    """Real settlement-shaped records on disk, all of the given class, oldest first."""
    bet_ids = []
    for index, class_name in enumerate(history):
        bet_id = f"bet_prior_{index}"
        store = workspace / "state" / "commitments" / f"event_flow_{bet_id}" / bet_id
        (store / "settlement").mkdir(parents=True, exist_ok=True)
        (store / "bet.json").write_text(json.dumps({
            "bet_id": bet_id, "run_id": f"event_flow_{bet_id}", "project_id": PROJECT_ID,
            "data_version": f"class:{class_key}", "selected_action": "cand_x",
            "selection_reason": "declared"}), encoding="utf-8")
        (store / "state.json").write_text(json.dumps({
            "state": "CLOSED", "settled": True, "settled_class": class_name}), encoding="utf-8")
        (store / "settlement" / f"stl_{bet_id}_r1.json").write_text(
            json.dumps(_settlement(class_name, minute=index)), encoding="utf-8")
        bet_ids.append(bet_id)
    return bet_ids


def _workspace_with_job(tmp_path: Path, job_id: str, request: str) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    init_jobs(workspace).upsert_from_record({
        "job_id": job_id, "project_id": PROJECT_ID, "status": "running",
        "flow_id": "flow_abstain", "assigned_instance": "02", "request": request,
        "priority": 100, "next_run_at": 0})
    return workspace


def _ctx(workspace: Path, job_id: str) -> SimpleNamespace:
    return SimpleNamespace(workspace=str(workspace), job_id=job_id, instance_id="02",
                           project_id=PROJECT_ID)


def _bounded_class_key(job_id: str, message: str) -> str:
    """The class key the bounded action of this message will freeze, computed the same way."""
    from partner.application.commitment_bounded_adapter import bounded_spec
    spec = bounded_spec(job_id=job_id, trace_token=TOKEN, message=message,
                        instance_id="02", project_id=PROJECT_ID)
    return ep.task_class_key(project_id=PROJECT_ID, action_id="bounded_metric",
                             metric_signature=ep.metric_signature(spec["expected_effects"]))


def _prior(class_key: str, history: list[str]) -> dict:
    rows = [{"bet_id": f"bet_prior_{i}", "settlement_class": name,
             "improvement_over_baseline": name == "supported",
             "publish_blockers": [], "settled_at": f"2026-09-19T20:{i:02d}:00"}
            for i, name in enumerate(history)]
    counts = {"total": len(rows),
              "falsified": sum(1 for name in history if name == "falsified"),
              "supported": sum(1 for name in history if name == "supported"),
              "abstained": sum(1 for name in history if name == "abstained")}
    return {"class_key": class_key, "counts": counts, "rows": rows}


# --- the rule ---------------------------------------------------------------

def test_the_rule_is_pure_and_its_thresholds_are_named_constants():
    assert ep.ABSTAIN_MIN_EVIDENCE == 2
    assert ep.ABSTAIN_REFUTED_RATIO == 0.75
    prior = _prior("cls_x", ["falsified", "falsified"])
    first = ep.decide_abstention(prior)
    second = ep.decide_abstention(prior)
    assert first == second                       # deterministic, no hidden state
    assert set(first) == {"abstain", "reason", "blocked_by", "rule", "evidence"}
    # the decision carries the constants it applied, so a reader never has to guess them
    evidence = first["evidence"]
    assert evidence["min_evidence"] == ep.ABSTAIN_MIN_EVIDENCE
    assert evidence["refuted_ratio_threshold"] == ep.ABSTAIN_REFUTED_RATIO
    assert prior == _prior("cls_x", ["falsified", "falsified"])   # nothing was mutated


def test_all_refuted_without_success_abstains():
    decision = ep.decide_abstention(_prior("cls_x", ["falsified"] * 4))
    assert decision["abstain"] is True
    assert decision["rule"] == ep.ABSTAIN_RULE
    assert decision["blocked_by"] == []
    assert decision["evidence"]["refuted_ratio"] == 1.0
    assert decision["evidence"]["refuted_bet_ids"]


def test_a_mixed_ratio_below_the_threshold_does_not_abstain():
    decision = ep.decide_abstention(_prior("cls_x", ["falsified", "falsified", "inconclusive"]))
    assert decision["abstain"] is False
    assert any("refuted_ratio" in reason for reason in decision["blocked_by"])
    # exactly at the threshold still abstains (3/4), as long as the latest settlement is
    # itself falsified -- the tail condition is what keeps a recovered class from abstaining
    at_threshold = ep.decide_abstention(
        _prior("cls_x", ["falsified", "falsified", "inconclusive", "falsified"]))
    assert at_threshold["abstain"] is True
    assert at_threshold["evidence"]["refuted_ratio"] == 0.75


def test_any_supported_settlement_blocks_the_abstention():
    decision = ep.decide_abstention(_prior("cls_x", ["falsified"] * 3 + ["supported"]))
    assert decision["abstain"] is False
    assert any("supported" in reason for reason in decision["blocked_by"])


def test_too_little_evidence_does_not_abstain():
    decision = ep.decide_abstention(_prior("cls_x", ["falsified"]))
    assert decision["abstain"] is False
    assert any("evidence_total 1" in reason for reason in decision["blocked_by"])
    empty = ep.decide_abstention(ep.empty_prior(class_key="cls_x"))
    assert empty["abstain"] is False
    assert any("evidence_total 0" in reason for reason in empty["blocked_by"])
    # the empty prior also carries its own pre-decided refusal, which is what the spec
    # consumes when the class has no history at all
    assert ep.empty_prior(class_key="cls_x")["abstention"]["blocked_by"] == ["prior_empty"]


def test_an_abstention_is_not_repeated_without_new_evidence():
    decision = ep.decide_abstention(_prior("cls_x", ["falsified"] * 3 + ["abstained"]))
    assert decision["abstain"] is False
    assert any("abstention is already the tail" in reason for reason in decision["blocked_by"])
    # abstentions do not count as evidence either way
    assert decision["evidence"]["evidence_total"] == 3
    assert decision["evidence"]["refuted_ratio"] == 1.0


def test_new_falsified_evidence_after_an_abstention_allows_it_again():
    decision = ep.decide_abstention(
        _prior("cls_x", ["falsified", "falsified", "abstained", "falsified"]))
    assert decision["abstain"] is True


# --- the kernel terminal state ---------------------------------------------

def _frozen_bet():
    from partner.commitment.models import (Budget, CommitmentPolicy, EvaluationProtocol,
                                           ExpectedEffect, FalsificationCondition, BetRecord)
    return BetRecord(
        bet_id="bet_kernel", partner_id="p", project_id=PROJECT_ID, run_id="run_kernel",
        question="declines to wager ?", context_snapshot_ref="context/snapshot.json",
        context_snapshot_hash="sha", candidates=(),
        selected_action="abstain", selection_reason="abstained: nothing to test",
        rejected_alternatives=(), expected_effects=(ExpectedEffect.from_dict(
            {"metric": "tests_passed", "direction": "increase", "unit": "tests",
             "kind": "delta_over_baseline", "min_delta": 1.0, "threshold": 0.0}),),
        falsification_conditions=(FalsificationCondition.from_dict(
            {"code": "no_new_pass", "kind": "metric_violation",
             "params": {"metric": "tests_passed", "delta": 1.0}}),),
        evaluation_protocol=EvaluationProtocol.from_dict(
            {"metric_specs": [{"metric": "tests_passed", "direction": "increase",
                               "unit": "tests"}], "replicates": 1}),
        baseline_ref="baseline:none", budget=Budget.create(
            wall_clock_seconds=60, model_calls=0, actions=1, rounds=1, started_epoch=0.0),
        commitment_policy=CommitmentPolicy.from_dict({"max_rounds": 1}),
        code_version="c", data_version="class:cls_kernel", model_config_ref="m",
        environment="isolated_sample", treatment=None)


def test_abstained_is_registered_and_keeps_its_own_semantics():
    from partner.commitment import models as M
    assert "abstained" in M.SETTLEMENT_CLASSES
    assert "abstained" not in ("supported", "falsified", "blocked", "inconclusive")
    assert M.BLOCKER_ABSTAINED == "abstained"

    settlement = M.SettlementDecision(
        settlement_id="stl_x", bet_id="bet_kernel", settlement_class="abstained",
        expectation_outcomes=(), new_regressions=(), pre_existing_failures=(),
        comparison=M.ComparisonProof(
            inputs_identical=False, evaluator_identical=False, protocol_identical=False,
            budget_comparable=False, environment_identical=False,
            harness_version_identical=False, baseline_treatment="", candidate_treatment="",
            treatment_diff_hash="", expected_treatment_diff_hash="", baseline_code_version="",
            candidate_code_version="", baseline_value=None, candidate_value=None, delta=None,
            detail="no comparison was computed"),
        expectations_met=False, improvement_over_baseline=False,
        baseline_already_satisfied=False, supported_claim="none", environment="isolated_sample",
        improvement_observed=False, publish_eligible=False,
        publish_blockers=(M.BLOCKER_ABSTAINED,), next_state="CLOSED",
        machine_rules=("abstained: no action was executed",))

    # an abstention must not smuggle in a measurement's claims
    import dataclasses
    import pytest
    for field_name, value in (("expectations_met", True), ("improvement_over_baseline", True),
                              ("baseline_already_satisfied", True), ("publish_eligible", True)):
        with pytest.raises(M.ContractError):
            dataclasses.replace(settlement, **{field_name: value})
    with pytest.raises(M.ContractError):
        dataclasses.replace(settlement, supported_claim="absolute_attainment")
    with pytest.raises(M.ContractError):
        dataclasses.replace(settlement, expectation_outcomes=(object(),))


def test_a_habit_or_growth_level_is_still_refused():
    import pytest
    from partner.commitment import models as M
    assert M.LEVEL_ABSTENTION == "abstention"
    for level in ("habit", "growth", "belief", "lesson"):
        with pytest.raises(M.ContractError, match="level must be 'experience'"):
            M.ExperienceRecord(
                experience_id="e", bet_id="b", run_id="r", settlement_ref="s",
                settlement_class="supported", state_action_outcome_chain=("a",),
                scope="x", confidence=0.5, evidence_refs=("ev",), level=level)


# --- the closed loop through the Event layer -------------------------------

def _run_flow(tmp_path: Path, job_id: str, message: str, history: list[str]):
    """prior -> record -> execute -> reconcile, exactly as the flow wires them."""
    from partner.events import commitment as C
    workspace = _workspace_with_job(tmp_path, job_id, message)
    class_key = _bounded_class_key(job_id, message)
    _fabricate_same_class(workspace, class_key, history)
    ctx = _ctx(workspace, job_id)
    prior = C.commitment_prior_recall(ctx, {})
    record = C.commitment_bet_record(ctx, {})
    execute = C.commitment_bet_execute(ctx, {})
    reconcile = C.commitment_reply_reconcile(ctx, {"flow_outputs": {
        "deduplicate": {"message": "本轮已取得进展，测试通过。"}}})
    return {"workspace": workspace, "class_key": class_key, "prior": prior,
            "record": record, "execute": execute, "reconcile": reconcile}


def _settlement_of(workspace: Path, job_id: str) -> dict:
    store = workspace / "state" / "commitments" / f"event_flow_{job_id}" / f"bet_{job_id}"
    names = sorted((store / "settlement").glob("*.json"))
    return json.loads(names[-1].read_text(encoding="utf-8"))


def test_the_closed_loop_settles_abstained_and_nothing_runs(tmp_path):
    job_id = "job_abstain_closed_loop"
    out = _run_flow(tmp_path, job_id, f"classify the declared sample {TOKEN}",
                    ["falsified", "falsified", "falsified"])

    # 1. the prior saw the real history and decided to decline
    prior_output = out["prior"]["semantic_output"]
    assert prior_output["prior_counts"]["falsified"] == 3
    abstention = prior_output["abstention"]
    assert abstention["abstain"] is True
    assert abstention["rule"] == ep.ABSTAIN_RULE
    assert abstention["evidence"]["refuted_ratio"] == 1.0

    # 2. the frozen bet declares the abstention (and froze the evidence into its snapshot)
    assert out["record"]["ok"] is True
    store = out["workspace"] / "state" / "commitments" / f"event_flow_{job_id}" / f"bet_{job_id}"
    bet = json.loads((store / "bet.json").read_text(encoding="utf-8"))
    assert bet["selected_action"] == "abstain"
    snapshot = json.loads((store / "context" / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["abstention"]["abstain"] is True
    assert "refuted" in snapshot["abstention"]["reason"]

    # 3. the settlement is ABSTAINED, with nothing claimed and nothing measured
    settlement = _settlement_of(out["workspace"], job_id)
    assert settlement["settlement_class"] == "abstained"
    assert settlement["expectation_outcomes"] == []
    assert settlement["expectations_met"] is False
    assert settlement["improvement_over_baseline"] is False
    assert settlement["baseline_already_satisfied"] is False
    assert settlement["supported_claim"] == "none"
    assert settlement["publish_eligible"] is False
    assert settlement["publish_blockers"] == ["abstained"]
    assert any(rule.startswith("abstain_reason=") for rule in settlement["machine_rules"])
    assert out["execute"]["semantic_output"]["settlement_class"] == "abstained"

    # 4. the receipt and the abstention artifact both say that nothing ran
    records = json.loads((store / "abstention" / "abstention.json").read_text(encoding="utf-8"))
    assert records["executed"] == {"model_calls": 0, "actions": 0, "arms_run": 0,
                                   "patch_proposed": False, "metric_read": False}
    assert records["abstain_reason"] == abstention["reason"]
    measurements = list((store / "measurements").glob("*.json"))
    assert measurements, "the not-measured record must exist"
    for path in measurements:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["validity"] in ("missing", "invalid")
        assert payload["value"] is None

    # 5. the abstention escapes the notification gate: without these two flags the whole
    #    message path (compose .. send) is skipped and no reply line can ever exist
    assert out["execute"]["requires_human"] is True
    assert out["execute"]["notification_kind"] == "abstention"
    assert out["execute"]["abstention"]["reason"] == abstention["reason"]

    # 6. the reply states the decision, its reason and its evidence
    body = out["reconcile"]["semantic_output"]["settlement_message"]
    assert "本次选择不下注，原因如下" in body
    assert "settlement_class=abstained" in body
    assert "不下注证据：" in body
    assert "本轮已取得进展，测试通过。" not in body      # the claiming draft was dropped


def test_prior_off_never_abstains_on_the_same_history(tmp_path):
    job_id = "job_abstain_prior_off"
    out = _run_flow(tmp_path, job_id,
                    f"classify the declared sample {TOKEN} prior=off",
                    ["falsified", "falsified", "falsified"])
    assert out["prior"]["semantic_output"]["prior_disabled"] is True
    assert out["prior"]["semantic_output"]["abstention"]["abstain"] is False
    # and the audit says *why* there is no prior, rather than blaming a class difference
    snapshot = json.loads((out["workspace"] / "state" / "commitments"
                           / f"event_flow_{job_id}" / f"bet_{job_id}" / "context"
                           / "snapshot.json").read_text(encoding="utf-8"))
    assert "prior_disabled" in snapshot["abstention"]["reason"]
    settlement = _settlement_of(out["workspace"], job_id)
    assert settlement["settlement_class"] != "abstained"
    # ...and the same history with the prior enabled does abstain (the decision is the
    # prior's, never the message's)
    other = _run_flow(tmp_path / "enabled", "job_abstain_enabled",
                      f"classify the declared sample {TOKEN}",
                      ["falsified", "falsified", "falsified"])
    assert _settlement_of(other["workspace"], "job_abstain_enabled")["settlement_class"] == "abstained"
