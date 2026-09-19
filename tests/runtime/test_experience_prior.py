"""Experience consumption: the same-class past changes this bet's decision.

Requirements covered here:

* the class criterion is a pure function of declared strings and never an LLM call;
* the read side looks at same-class settled bets only, and is read-only;
* an empty prior adjusts nothing (``prior_adjusted=false``, explicitly), while a
  non-empty prior moves a declared decision variable by a deterministic rule whose
  before/after values are auditable;
* the frozen bet carries the adjusted value, and the counterfactual switch
  (``prior=off``) puts it back to the declared value.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from partner.application import experience_prior as ep  # noqa: E402
from partner.index.job_repository import init as init_jobs  # noqa: E402

PROJECT_ID = "molecular_generation"
ACTION_ID = "real_task:fix_dgm_lineage_contract"
METRIC_SIG = "tests_passed:delta_over_baseline:increase"
CLASS_KEY = ep.task_class_key(project_id=PROJECT_ID, action_id=ACTION_ID,
                             metric_signature=METRIC_SIG)


# ---------------------------------------------------------------------------
# fixtures: fabricated settled bets on disk (never touched again by the read side)
# ---------------------------------------------------------------------------

def _fabricate_settled(tmp_path: Path, *, bet_id: str, class_key: str,
                       settlement_class: str = "supported", improvement: bool = True,
                       supported_claim: str = "improvement_over_baseline",
                       blockers: tuple[str, ...] = ("isolated_sample_only",
                                                    "single_episode_only"),
                       minute: int = 0) -> Path:
    run_id = f"event_flow_{bet_id}"
    store = tmp_path / "ws" / "state" / "commitments" / run_id / f"bet_{bet_id}"
    for sub in ("settlement", "experience", "context"):
        (store / sub).mkdir(parents=True, exist_ok=True)
    (store / "bet.json").write_text(json.dumps({
        "bet_id": bet_id, "run_id": run_id, "project_id": PROJECT_ID,
        "data_version": f"class:{class_key}", "question": f"q for {bet_id}",
        "selected_action": "patch_demo",
        "selection_reason": "the single declared treatment difference of this bet",
        "created_at": f"2026-09-19T20:0{minute}:00", "status": "CLOSED"}), encoding="utf-8")
    (store / "state.json").write_text(json.dumps({
        "state": "CLOSED", "settled": True, "settled_class": settlement_class,
        "experience_emitted": True}), encoding="utf-8")
    (store / "settlement" / f"stl_{bet_id}_r1.json").write_text(json.dumps({
        "settlement_id": f"stl_{bet_id}_r1", "bet_id": bet_id,
        "settlement_class": settlement_class, "expectations_met": settlement_class == "supported",
        "improvement_over_baseline": improvement, "supported_claim": supported_claim,
        "publish_eligible": False, "publish_blockers": list(blockers),
        "settled_at": f"2026-09-19T20:0{minute}:30"}), encoding="utf-8")
    (store / "experience" / f"exp_{bet_id}_r1.json").write_text(json.dumps({
        "experience_id": f"exp_{bet_id}_r1", "settlement_class": settlement_class}),
        encoding="utf-8")
    (store / "context" / "snapshot.json").write_text(json.dumps({
        "trace_token": f"tok_{bet_id}"}), encoding="utf-8")
    return store


def _workspace_with_job(tmp_path: Path, job_id: str, request: str) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    init_jobs(workspace).upsert_from_record({
        "job_id": job_id, "project_id": PROJECT_ID, "status": "running",
        "flow_id": "flow_x", "assigned_instance": "02", "request": request,
        "priority": 100, "next_run_at": 0})
    return workspace


def _declared() -> dict:
    return {"min_delta": 1.0, "replicates": 1, "require_baseline_rerun": True}


# ---------------------------------------------------------------------------
# 1. the class criterion is a pure function, never an LLM
# ---------------------------------------------------------------------------

def test_the_class_key_is_a_pure_function_of_declared_strings():
    first = ep.task_class_key(project_id="p", action_id="real_task:t",
                              metric_signature="m:delta_over_baseline:increase")
    again = ep.task_class_key(project_id="p", action_id="real_task:t",
                              metric_signature="m:delta_over_baseline:increase")
    assert first == again
    assert first.startswith(ep.CLASS_PREFIX)
    # a different action, project or metric shape is a different class
    assert first != ep.task_class_key(project_id="p", action_id="real_task:other",
                                      metric_signature="m:delta_over_baseline:increase")
    assert first != ep.task_class_key(project_id="q", action_id="real_task:t",
                                      metric_signature="m:delta_over_baseline:increase")
    assert first != ep.task_class_key(project_id="p", action_id="real_task:t",
                                      metric_signature="m:delta_over_baseline:decrease")
    # the trace token and the message never participate
    assert ep.task_class_key(project_id="p", action_id="real_task:t",
                             metric_signature="m:delta_over_baseline:increase") == first


def test_the_class_key_round_trips_through_a_frozen_bet():
    bet = {"data_version": f"class:{CLASS_KEY}"}
    assert ep.class_key_from_bet(bet) == CLASS_KEY
    assert ep.class_key_from_bet({"data_version": "message:tok"}) == ""


# ---------------------------------------------------------------------------
# 2. only same-class history is read
# ---------------------------------------------------------------------------

def test_only_same_class_settlements_are_read(tmp_path):
    _fabricate_settled(tmp_path, bet_id="bet_same", class_key=CLASS_KEY, minute=1)
    _fabricate_settled(tmp_path, bet_id="bet_other", class_key="cls_deadbeef", minute=2)
    workspace = tmp_path / "ws"
    rows = ep.scan_settled_bets(workspace)
    assert {r["bet_id"] for r in rows} == {"bet_same", "bet_other"}
    same = ep.select_same_class(rows, CLASS_KEY)
    assert [r["bet_id"] for r in same] == ["bet_same"]
    prior = ep.summarize_prior(same, class_key=CLASS_KEY)
    assert prior["row_count"] == 1
    assert [r["bet_id"] for r in prior["rows"]] == ["bet_same"]
    assert prior["counts"]["supported"] == 1
    # a bet with no settlement is not evidence, and never shows up as a row
    (tmp_path / "ws" / "state" / "commitments" / "event_flow_bet_open"
     / "bet_bet_open").mkdir(parents=True)
    assert {r["bet_id"] for r in ep.scan_settled_bets(workspace)} == {"bet_same", "bet_other"}


def test_the_read_side_never_writes_history(tmp_path):
    store = _fabricate_settled(tmp_path, bet_id="bet_same", class_key=CLASS_KEY, minute=1)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(store.rglob("*.json"))}
    workspace = tmp_path / "ws"
    rows = ep.scan_settled_bets(workspace)
    ep.summarize_prior(ep.select_same_class(rows, CLASS_KEY), class_key=CLASS_KEY)
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(store.rglob("*.json"))}
    assert before == after, "reading a prior must not touch any historical record"


# ---------------------------------------------------------------------------
# 3. an empty prior adjusts nothing
# ---------------------------------------------------------------------------

def test_an_empty_prior_adjusts_nothing_and_says_so():
    prior = ep.empty_prior(class_key=CLASS_KEY)
    adjusted, audit = ep.adjust_declared(_declared(), prior)
    assert adjusted == _declared()
    assert audit["prior_adjusted"] is False
    assert audit["parameters"] == []
    assert audit["reason"] == "prior_empty"


def test_an_absent_prior_file_is_an_empty_prior(tmp_path):
    prior = ep.read_prior(tmp_path / "nothing_here")
    assert prior["empty"] is True and prior["row_count"] == 0
    adjusted, audit = ep.adjust_declared(_declared(), prior)
    assert adjusted["min_delta"] == 1.0 and audit["prior_adjusted"] is False


# ---------------------------------------------------------------------------
# 4/5. the adjustment rules, one test per prior shape, before/after auditable
# ---------------------------------------------------------------------------

def _prior_with(tmp_path, **kwargs):
    _fabricate_settled(tmp_path, bet_id="bet_h", class_key=CLASS_KEY, **kwargs)
    rows = ep.select_same_class(ep.scan_settled_bets(tmp_path / "ws"), CLASS_KEY)
    return ep.summarize_prior(rows, class_key=CLASS_KEY)


def test_supported_history_lowers_the_bar(tmp_path):
    adjusted, audit = ep.adjust_declared(_declared(), _prior_with(tmp_path))
    assert adjusted["min_delta"] == 0.5
    entry = audit["parameters"][0]
    assert (entry["name"], entry["before"], entry["after"]) == ("min_delta", 1.0, 0.5)
    assert entry["rule"] == "supported_history_lowers_the_bar"
    assert audit["prior_adjusted"] is True


def test_refuted_history_raises_the_bar(tmp_path):
    adjusted, audit = ep.adjust_declared(
        _declared(), _prior_with(tmp_path, settlement_class="refuted", improvement=False,
                                 supported_claim="none", blockers=()))
    assert adjusted["min_delta"] == 2.0
    assert audit["parameters"][0]["rule"] == "refuted_history_raises_the_bar"
    assert audit["parameters"][0]["evidence"] == {"refuted": 1, "supported": 0}


def test_improvement_false_forces_a_real_baseline(tmp_path):
    declared = {**_declared(), "require_baseline_rerun": False}
    adjusted, audit = ep.adjust_declared(
        declared, _prior_with(tmp_path, settlement_class="inconclusive", improvement=False,
                              supported_claim="none", blockers=("isolated_sample_only",)))
    assert adjusted["require_baseline_rerun"] is True
    entry = next(p for p in audit["parameters"] if p["name"] == "require_baseline_rerun")
    assert entry["rule"] == "improvement_false_forces_a_real_baseline"
    assert entry["before"] is False and entry["after"] is True


def test_single_episode_only_adds_one_executed_repetition(tmp_path):
    adjusted, audit = ep.adjust_declared(_declared(), _prior_with(tmp_path))
    assert adjusted["replicates"] == 2
    entry = next(p for p in audit["parameters"] if p["name"] == "replicates")
    assert entry["rule"] == "single_episode_blocker_adds_one_executed_repetition"
    assert entry["before"] == 1 and entry["after"] == 2


def test_an_inconclusive_prior_moves_nothing(tmp_path):
    adjusted, audit = ep.adjust_declared(
        _declared(), _prior_with(tmp_path, settlement_class="inconclusive",
                                 improvement=True, supported_claim="none",
                                 blockers=("isolated_sample_only",)))
    assert adjusted["min_delta"] == 1.0 and adjusted["replicates"] == 1
    assert audit["prior_adjusted"] is False
    assert audit["reason"] == "prior_present_no_rule_triggered"


# ---------------------------------------------------------------------------
# 6. the recall Event: real retrieval, auditable, and the counterfactual switch
# ---------------------------------------------------------------------------

def _recall_ctx(workspace: Path, job_id: str) -> SimpleNamespace:
    return SimpleNamespace(workspace=str(workspace), job_id=job_id, instance_id="02",
                           project_id=PROJECT_ID)


def test_the_recall_event_writes_a_prior_for_this_class_only(tmp_path):
    from partner.events.commitment import commitment_prior_recall

    _fabricate_settled(tmp_path, bet_id="bet_same", class_key=CLASS_KEY, minute=1)
    _fabricate_settled(tmp_path, bet_id="bet_other", class_key="cls_other", minute=2)
    job_id = "job_prior_recall"
    token = "runtime_trace_prior_02"
    workspace = _workspace_with_job(tmp_path, job_id,
                                    f"please do it\n\nreal_task:fix_dgm_lineage_contract"
                                    f"\ntrace token: {token}")
    result = commitment_prior_recall(_recall_ctx(workspace, job_id), {})
    assert result["ok"] is True
    out = result["semantic_output"]
    assert out["prior_class_key"] == CLASS_KEY
    assert out["prior_row_count"] == 1
    assert out["prior_bet_ids"] == ["bet_same"]
    assert out["scanned_settled_bets"] == 2
    assert out["prior_empty"] is False
    prior_file = Path(out["prior_path"])
    assert prior_file.is_file()
    stored = json.loads(prior_file.read_text(encoding="utf-8"))
    assert stored["class_key"] == CLASS_KEY and stored["rows"][0]["bet_id"] == "bet_same"


def test_the_counterfactual_switch_writes_no_prior(tmp_path):
    from partner.events.commitment import commitment_prior_recall

    _fabricate_settled(tmp_path, bet_id="bet_same", class_key=CLASS_KEY, minute=1)
    job_id = "job_prior_off"
    token = "runtime_trace_prior_off"
    workspace = _workspace_with_job(tmp_path, job_id,
                                    f"do it\nreal_task:fix_dgm_lineage_contract\n"
                                    f"prior=off\ntrace token: {token}")
    result = commitment_prior_recall(_recall_ctx(workspace, job_id), {})
    assert result["semantic_output"]["prior_disabled"] is True
    assert result["semantic_output"]["prior_row_count"] == 0
    assert not Path(result["semantic_output"].get("prior_path") or "x").exists()


# ---------------------------------------------------------------------------
# the frozen bet carries the adjusted value -- and the counterfactual does not
# ---------------------------------------------------------------------------

def _record_frozen_bet(tmp_path, *, job_id: str, with_history: bool, prior_off: bool):
    from partner.events.commitment import commitment_bet_record, commitment_prior_recall

    if with_history:
        _fabricate_settled(tmp_path, bet_id="bet_hist", class_key=CLASS_KEY, minute=1)
    token = f"runtime_trace_{job_id}"
    message = (f"real fix task\nreal_task:fix_dgm_lineage_contract\n"
               + ("prior=off\n" if prior_off else "")
               + f"trace token: {token}")
    workspace = _workspace_with_job(tmp_path, job_id, message)
    ctx = _recall_ctx(workspace, job_id)
    commitment_prior_recall(ctx, {})
    recorded = commitment_bet_record(ctx, {})
    assert recorded["ok"] is True, recorded.get("summary")
    store = (workspace / "state" / "commitments" / f"event_flow_{job_id}" / f"bet_{job_id}")
    bet = json.loads((store / "bet.json").read_text(encoding="utf-8"))
    snapshot = json.loads((store / "context" / "snapshot.json").read_text(encoding="utf-8"))
    return bet, snapshot


def test_a_non_empty_prior_changes_the_frozen_decision_variable(tmp_path):
    bet, snapshot = _record_frozen_bet(tmp_path, job_id="job_prior_yes",
                                       with_history=True, prior_off=False)
    assert bet["data_version"] == f"class:{CLASS_KEY}"
    assert bet["expected_effects"][0]["min_delta"] == 0.5
    assert bet["evaluation_protocol"]["replicates"] == 2
    audit = snapshot["prior_adjusted_parameter"]
    assert audit["prior_adjusted"] is True
    moved = {p["name"]: (p["before"], p["after"]) for p in audit["parameters"]}
    assert moved["min_delta"] == (1.0, 0.5)
    assert moved["replicates"] == (1, 2)
    assert snapshot["prior"]["class_key"] == CLASS_KEY


def test_the_counterfactual_returns_the_declared_values(tmp_path):
    """With the recall node disabled the decision variable is the declared one again."""
    bet, snapshot = _record_frozen_bet(tmp_path, job_id="job_prior_off",
                                       with_history=True, prior_off=True)
    assert bet["expected_effects"][0]["min_delta"] == 1.0
    assert bet["evaluation_protocol"]["replicates"] == 1
    assert snapshot["prior_adjusted_parameter"]["prior_adjusted"] is False
    assert snapshot["prior_adjusted_parameter"]["reason"] == "prior_empty"
    assert snapshot["prior"]["empty"] is True


def test_a_prior_for_another_class_never_leaks_into_this_bet(tmp_path):
    from partner.events.commitment import commitment_bet_record, commitment_prior_recall
    from partner.application.experience_prior import write_prior

    job_id = "job_wrong_prior"
    token = f"runtime_trace_{job_id}"
    workspace = _workspace_with_job(
        tmp_path, job_id,
        f"real fix task\nreal_task:fix_dgm_lineage_contract\ntrace token: {token}")
    ctx = _recall_ctx(workspace, job_id)
    store = workspace / "state" / "commitments" / f"event_flow_{job_id}" / f"bet_{job_id}"
    write_prior(store, ep.summarize_prior(
        [{"bet_id": "bet_x", "settlement_class": "supported", "improvement_over_baseline": True,
          "publish_blockers": ["single_episode_only"], "settlement_id": "s", "trace_token": "t",
          "selected_action": "a", "selection_reason": "r"}],
        class_key="cls_somewhere_else"))
    commitment_prior_recall(ctx, {})   # this overwrites the stale prior with the real one
    recorded = commitment_bet_record(ctx, {})
    assert recorded["ok"] is True
    bet = json.loads((store / "bet.json").read_text(encoding="utf-8"))
    # no same-class history exists, so nothing was adjusted
    assert bet["expected_effects"][0]["min_delta"] == 1.0
