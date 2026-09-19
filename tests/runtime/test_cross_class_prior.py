"""Cross-class prior transfer: borrowing from related classes, never at the expense of
the direct one.

The similarity judgement is a pure function of the three declared class components; the
borrowed evidence is discounted by that similarity and *added* to the direct evidence,
which keeps weight 1.0 throughout.  The related path opens only when the direct class does
not carry enough evidence of its own, and it is recorded -- with every weight, every
class key and the resulting before/after -- in ``prior_adjusted_parameter``.
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
TOKEN = "crossclass_trace_02_1700000001"


def _components(project_id: str, action_id: str, metric: str) -> dict:
    return ep.class_components(project_id=project_id, action_id=action_id, metric_signature=metric)


def _row(bet_id: str, class_key: str, settlement_class: str, *, minute: int,
         supported_claim: str = "", blockers: tuple = ()) -> dict:
    return {"bet_id": bet_id, "run_id": f"run_{bet_id}", "class_key": class_key,
            "settlement_class": settlement_class,
            "expectations_met": settlement_class == "supported",
            "improvement_over_baseline": settlement_class == "supported",
            "supported_claim": supported_claim or ("improvement_over_baseline"
                                                   if settlement_class == "supported" else "none"),
            "publish_blockers": list(blockers), "settled_at": f"2026-09-19T20:{minute:02d}:00",
            "settled_epoch": float(minute), "settlement_ref": f"/x/{bet_id}"}


def _prior_of(rows, class_key, components, related=None):
    return ep.summarize_prior(rows, class_key=class_key, components=components, related=related)


def _related(weight: float, rows, *, reasons=("same_project_id",)) -> dict:
    return {"cls_related": {"weight": weight, "reasons": list(reasons),
                            "components": {"project_id": PROJECT_ID, "action_id": "a",
                                           "metric_signature": "m"}, "rows": rows}}


# --- the similarity judgement -------------------------------------------------

def test_the_similarity_judgement_is_pure_and_its_weights_are_named_constants():
    assert (ep.RELATED_WEIGHT_SAME_ACTION, ep.RELATED_WEIGHT_SAME_METRIC,
            ep.RELATED_WEIGHT_SAME_PROJECT) == (0.6, 0.5, 0.4)
    assert ep.DIRECT_WEIGHT == 1.0
    own = _components("p1", "task:a", "m:t:increase")
    other = _components("p1", "task:b", "m:t:increase")
    assert ep.similarity_weight(own, other) == ep.similarity_weight(own, dict(other))
    assert ep.similarity_weight(own, other) == 0.5      # deterministic, no hidden state
    assert ep.similarity_weight(own, {}) == 0.0


def test_each_similarity_has_its_weight_and_they_never_add():
    own = _components("p1", "task:a", "m:t:increase")
    same_action = _components("p2", "task:a", "other:t:increase")     # action only -> 0.6
    same_metric = _components("p3", "task:z", "m:t:increase")         # metric only -> 0.5
    same_project = _components("p1", "task:z", "other:t:increase")    # project only -> 0.4
    action_and_project = _components("p1", "task:a", "other:t:increase")
    all_three = _components("p1", "task:a", "m:t:increase")
    assert ep.similarity_weight(own, same_action) == 0.6
    assert ep.similarity_weight(own, same_metric) == 0.5
    assert ep.similarity_weight(own, same_project) == 0.4
    assert ep.similarity_weight(own, action_and_project) == 0.6       # max, not 1.0
    assert ep.similarity_weight(own, all_three) == 1.0                # the same class
    # ... and a class that is identical is never offered as a related source
    key = ep.task_class_key(**own)
    known = {key: own, ep.task_class_key(**same_action): same_action}
    assert [k for k, _ in ep.related_class_keys(key, known, components=own)] == \
        [ep.task_class_key(**same_action)]


def test_related_class_keys_is_stably_sorted_and_skips_unverifiable_classes():
    own = _components("p1", "task:a", "m:t:increase")
    weak = _components("p1", "task:z", "other:t:increase")            # 0.4
    strong = _components("p2", "task:a", "other:t:increase")          # 0.6
    tie = _components("p1", "task:y", "m:t:increase")                 # 0.5
    known = {
        ep.task_class_key(**weak): {"components": weak},
        ep.task_class_key(**strong): {"components": strong},
        ep.task_class_key(**tie): {"components": tie},
        "cls_unverified": {"components": strong, "verified": False},
        "cls_bare_key_without_components": {},
    }
    ordered = ep.related_class_keys(ep.task_class_key(**own), known, components=own)
    assert [weight for _, weight in ordered] == [0.6, 0.5, 0.4]       # weight desc
    assert ordered == ep.related_class_keys(ep.task_class_key(**own), known, components=own)
    assert "cls_unverified" not in [key for key, _ in ordered]
    assert ep.related_class_keys(ep.task_class_key(**own), {}, components=own) == []


# --- triggering the related path ---------------------------------------------

def test_an_empty_direct_class_triggers_the_related_path():
    own = _components(PROJECT_ID, "real_task:t", "m:t:increase")
    key = ep.task_class_key(**own)
    prior = _prior_of([], key, own,
                      related=_related(0.5, [_row("b1", "cls_related", "falsified", minute=1)]))
    assert prior["related"]["triggered"] is True
    assert prior["related"]["used"] is True
    assert prior["applied_from"] == "weighted"
    assert prior["empty"] is False                    # borrowed evidence is still evidence
    assert prior["weighted"]["falsified"] == 0.5
    assert prior["weighted"]["evidence_total"] == 0.5
    adjusted, audit = ep.adjust_declared(
        {"min_delta": 1.0, "replicates": 1, "require_baseline_rerun": True}, prior)
    assert adjusted["min_delta"] == 1.5               # base * (1 + 0.5) via the weighted view
    assert audit["applied_from"] == "weighted"
    assert audit["used_direct_only"] is False


def test_no_related_history_behaves_exactly_like_an_empty_direct_class():
    own = _components(PROJECT_ID, "real_task:t", "m:t:increase")
    key = ep.task_class_key(**own)
    for related in ({}, None):
        prior = _prior_of([], key, own, related=related)
        assert prior["related"]["used"] is False
        assert prior["related"]["reason"] == "no_related_class_history"
        assert prior["applied_from"] == "direct"
        adjusted, audit = ep.adjust_declared(
            {"min_delta": 1.0, "replicates": 1, "require_baseline_rerun": True}, prior)
        assert adjusted["min_delta"] == 1.0
        assert audit["prior_adjusted"] is False
        assert audit["adjustment_rule"] == "none"


def test_sufficient_direct_evidence_never_opens_the_related_path():
    own = _components(PROJECT_ID, "real_task:t", "m:t:increase")
    key = ep.task_class_key(**own)
    direct = [_row(f"d{i}", key, "falsified", minute=i) for i in range(2)]
    prior = _prior_of(direct, key, own,
                      related=_related(0.5, [_row("r1", "cls_related", "supported", minute=9)]))
    assert prior["related"]["triggered"] is False
    assert prior["related"]["used"] is False
    assert prior["related"]["reason"] == "direct_evidence_sufficient"
    assert prior["applied_from"] == "direct"
    adjusted, audit = ep.adjust_declared(
        {"min_delta": 1.0, "replicates": 1, "require_baseline_rerun": True}, prior)
    assert audit["used_direct_only"] is True
    assert audit["related_class_keys"] == []          # nothing borrowed, nothing claimed
    assert audit["related_class_keys_considered"] == [["cls_related", 0.5]]
    # the related class' supported row must not have relaxed anything: direct-only stats rule
    assert adjusted["min_delta"] == 3.0               # 1.0 * (1 + 2 direct falsified)
    assert audit["adjustment_rule"] == "refuted_history_raises_the_bar"
    assert audit["applied_evidence"]["supported"] == 0


# --- the weighted statistics --------------------------------------------------

def test_weighted_statistics_fold_related_evidence_at_its_weight():
    own = _components(PROJECT_ID, "real_task:t", "m:t:increase")
    key = ep.task_class_key(**own)
    direct = [_row("d1", key, "falsified", minute=1)]                     # weight 1.0
    related_rows = [_row("r1", "cls_related", "falsified", minute=2),
                    _row("r2", "cls_related", "supported", minute=3)]
    prior = _prior_of(direct, key, own, related=_related(0.5, related_rows))
    # one direct row is *not* enough on its own (the minimum is two), so the related path
    # opens and the weighted view is what gets applied
    assert prior["related"]["triggered"] is True
    assert prior["related"]["used"] is True
    assert prior["applied_from"] == "weighted"
    assert prior["weighted"]["falsified"] == 1.0 + 0.5 * 1
    assert prior["weighted"]["supported"] == 0.0 + 0.5 * 1
    assert prior["weighted"]["evidence_total"] == 1.0 + 0.5 * 2
    assert prior["counts"]["falsified"] == 1          # direct counts untouched
    assert prior["counts"]["evidence_total"] == 1


def test_the_weighted_view_applies_when_it_is_used():
    own = _components(PROJECT_ID, "real_task:t", "m:t:increase")
    key = ep.task_class_key(**own)
    related_rows = [_row("r1", "cls_related", "falsified", minute=1),
                    _row("r2", "cls_related", "falsified", minute=2),
                    _row("r3", "cls_related", "supported", minute=3)]
    prior = _prior_of([], key, own, related=_related(0.5, related_rows))
    assert prior["applied_from"] == "weighted"
    adjusted, audit = ep.adjust_declared(
        {"min_delta": 2.0, "replicates": 1, "require_baseline_rerun": True}, prior)
    assert audit["applied_evidence"]["falsified"] == 1.0        # 2 * 0.5
    assert audit["applied_evidence"]["supported"] == 0.5        # 1 * 0.5
    assert audit["applied_evidence"]["evidence_total"] == 1.5
    assert audit["applied_evidence"]["refuted_ratio"] == round(1.0 / 1.5, 6)  # 0.6667 >= 0.5
    assert audit["adjustment_rule"] == "refuted_history_raises_the_bar"
    assert adjusted["min_delta"] == 4.0                         # 2.0 * (1 + 1.0)


def test_related_evidence_only_supplements_the_direct_one():
    own = _components(PROJECT_ID, "real_task:t", "m:t:increase")
    key = ep.task_class_key(**own)
    direct = [_row("d1", key, "falsified", minute=1)]
    prior = _prior_of(direct, key, own,
                      related=_related(0.4, [_row("r1", "cls_related", "supported", minute=2)]))
    # the direct row is still there, at full weight, in both views
    assert prior["counts"]["falsified"] == 1
    assert prior["weighted"]["falsified"] >= 1.0
    assert [r["bet_id"] for r in prior["rows"]] == ["d1"]
    assert prior["related"]["direct_weight"] == 1.0
    assert prior["related"]["classes"][0]["bet_ids"] == ["r1"]


def test_the_audit_records_direct_related_weighted_rule_and_before_after():
    own = _components(PROJECT_ID, "real_task:t", "m:t:increase")
    key = ep.task_class_key(**own)
    prior = _prior_of([], key, own,
                      related=_related(0.5, [_row("r1", "cls_related", "falsified", minute=1),
                                             _row("r2", "cls_related", "falsified", minute=2)]))
    adjusted, audit = ep.adjust_declared(
        {"min_delta": 1.0, "replicates": 1, "require_baseline_rerun": True}, prior)
    assert audit["direct_class_evidence"]["evidence_total"] == 0
    assert audit["related_class_keys"] == [["cls_related", 0.5]]
    assert audit["related_class_reasons"] == {"cls_related": ["same_project_id"]}
    assert audit["weighted_evidence"]["falsified"] == 1.0
    assert audit["applied_evidence"]["falsified"] == 1.0
    assert audit["adjustment_rule"] == "refuted_history_raises_the_bar"
    assert audit["parameters"][0]["before"] == 1.0 and audit["parameters"][0]["after"] == 2.0
    assert audit["related_evidence"]["used"] is True


def test_the_abstention_rule_uses_the_weighted_statistics():
    own = _components(PROJECT_ID, "real_task:t", "m:t:increase")
    key = ep.task_class_key(**own)
    # the weighted evidence must itself clear ABSTAIN_MIN_EVIDENCE: 4 borrowed rows at 0.6
    refuted = [_row(f"r{i}", "cls_related", "falsified", minute=i) for i in range(4)]
    decision = ep.decide_abstention(_prior_of([], key, own, related=_related(0.6, refuted)))
    assert decision["abstain"] is True
    assert decision["evidence"]["refuted_ratio"] == 1.0
    assert decision["evidence"]["evidence_total"] == 2.4          # 4 * 0.6
    # ... while two borrowed rows (1.2) would not: the threshold is dimensional
    thin = ep.decide_abstention(_prior_of([], key, own, related=_related(0.6, refuted[:2])))
    assert thin["abstain"] is False
    # and one borrowed success is enough to refuse the abstention
    with_success = refuted + [_row("r9", "cls_related", "supported", minute=9)]
    refused = ep.decide_abstention(_prior_of([], key, own, related=_related(0.6, with_success)))
    assert refused["abstain"] is False
    assert any("supported" in reason for reason in refused["blocked_by"])


def test_the_tail_follows_the_view_that_was_applied():
    own = _components(PROJECT_ID, "real_task:t", "m:t:increase")
    key = ep.task_class_key(**own)
    direct = [_row("d1", key, "falsified", minute=1)]          # direct tail: falsified
    related_rows = [_row("r1", "cls_related", "supported", minute=9)]   # related tail: supported
    # the direct class is thin, so the weighted view applies: the tail is the supported one
    weighted_prior = _prior_of(direct, key, own, related=_related(0.5, related_rows))
    assert weighted_prior["applied_from"] == "weighted"
    assert weighted_prior["tail"]["settlement_class"] == "supported"
    assert weighted_prior["tail"]["tail_from"] == "weighted"
    # with enough direct evidence the related tail must not decide anything
    enough = [_row("d1", key, "falsified", minute=1), _row("d2", key, "falsified", minute=2)]
    direct_prior = _prior_of(enough, key, own, related=_related(0.5, related_rows))
    assert direct_prior["applied_from"] == "direct"
    assert direct_prior["tail"]["settlement_class"] == "falsified"
    assert direct_prior["tail"]["tail_from"] == "direct"
    # ... and the abstention rule reads exactly that tail
    assert ep.decide_abstention(direct_prior)["abstain"] is True
    assert ep.decide_abstention(weighted_prior)["abstain"] is False


def test_prior_off_never_uses_the_related_path():
    prior = ep.empty_prior(class_key="cls_t", reason="prior_disabled")
    assert "related" not in prior or not prior.get("related")
    assert prior["abstention"]["abstain"] is False
    adjusted, audit = ep.adjust_declared(
        {"min_delta": 4.0, "replicates": 1, "require_baseline_rerun": True}, prior)
    assert adjusted["min_delta"] == 4.0
    assert audit["used_direct_only"] is True
    assert audit["related_class_keys"] == []


# --- through the Event layer, on disk ----------------------------------------

def _effects_from_signature(signature: str) -> list[dict]:
    out = []
    for part in signature.split(";"):
        metric, kind, direction = part.split(":")
        out.append({"metric": metric, "kind": kind, "direction": direction,
                    "min_delta": 1.0, "threshold": 0.0})
    return out


def _fabricate_class(workspace: Path, *, project_id: str, task_id: str,
                     signature: str, history: list[str]) -> str:
    """A real-shaped settled class on disk: bet + snapshot + state + settlement."""
    action_id = f"real_task:{task_id}"
    key = ep.task_class_key(project_id=project_id, action_id=action_id,
                            metric_signature=signature)
    for index, settlement_class in enumerate(history):
        bet_id = f"bet_cc_{task_id}_{index}"
        store = workspace / "state" / "commitments" / f"event_flow_{bet_id}" / bet_id
        (store / "settlement").mkdir(parents=True, exist_ok=True)
        (store / "context").mkdir(parents=True, exist_ok=True)
        (store / "bet.json").write_text(json.dumps({
            "bet_id": bet_id, "run_id": f"event_flow_{bet_id}", "project_id": project_id,
            "data_version": f"class:{key}", "selected_action": "patch_x",
            "selection_reason": "declared", "expected_effects": _effects_from_signature(signature),
            "created_at": f"2026-09-19T19:{index:02d}:00"}), encoding="utf-8")
        (store / "context" / "snapshot.json").write_text(json.dumps({
            "trace_token": f"cc_{task_id}_{index}", "task_id": task_id}), encoding="utf-8")
        (store / "state.json").write_text(json.dumps({
            "state": "CLOSED", "settled": True, "settled_class": settlement_class}),
            encoding="utf-8")
        (store / "settlement" / f"stl_{bet_id}_r1.json").write_text(json.dumps({
            "settlement_id": f"stl_{bet_id}_r1", "bet_id": bet_id,
            "settlement_class": settlement_class,
            "expectations_met": settlement_class == "supported",
            "improvement_over_baseline": settlement_class == "supported",
            "supported_claim": ("improvement_over_baseline" if settlement_class == "supported"
                                else "none"),
            "publish_eligible": False,
            "publish_blockers": ["environment_not_publishable:isolated_sample"],
            "created_at": f"2026-09-19T20:{index:02d}:00"}), encoding="utf-8")
    return key


def test_the_event_layer_borrows_related_evidence_and_freezes_the_weighted_result(tmp_path):
    from partner.events import commitment as C
    from partner.application.commitment_bounded_adapter import bounded_spec

    job_id = "job_cross_class"
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    init_jobs(workspace).upsert_from_record({
        "job_id": job_id, "project_id": PROJECT_ID, "status": "running",
        "flow_id": "flow_cc", "assigned_instance": "02",
        "request": f"classify the declared sample {TOKEN}", "priority": 100, "next_run_at": 0})
    ctx = SimpleNamespace(workspace=str(workspace), job_id=job_id, instance_id="02",
                          project_id=PROJECT_ID)
    bundle, failure = C._resolve_declared(ctx, {})
    assert failure is None
    own = dict(bundle["components"])
    # a related class: same project, same metric, a different action
    related_key = _fabricate_class(workspace, project_id=own["project_id"],
                                   task_id="some_other_task",
                                   signature=own["metric_signature"],
                                   history=["falsified", "falsified", "falsified"])
    assert related_key != bundle["class_key"]

    prior = C.commitment_prior_recall(ctx, {})
    out = prior["semantic_output"]
    assert out["prior_row_count"] == 0                     # the direct class is empty
    assert out["prior_empty"] is False                     # borrowed evidence still counts
    assert out["applied_from"] == "weighted"
    assert out["related"]["used"] is True
    assert [[c["class_key"], c["weight"]] for c in out["related"]["classes"]] == \
        [[related_key, 0.5]]                               # same project + same metric
    assert out["weighted"]["falsified"] == 1.5             # 3 rows * 0.5

    recorded = C.commitment_bet_record(ctx, {})
    assert recorded["ok"] is True
    store = workspace / "state" / "commitments" / f"event_flow_{job_id}" / f"bet_{job_id}"
    snapshot = json.loads((store / "context" / "snapshot.json").read_text(encoding="utf-8"))
    audit = snapshot["prior_adjusted_parameter"]
    assert audit["applied_from"] == "weighted"
    assert audit["related_class_keys"] == [[related_key, 0.5]]
    assert audit["weighted_evidence"]["falsified"] == 1.5
    # The bounded action declares ``min_delta = 0.0``, so nothing can be raised *from* it:
    # the weighted evidence is recorded, and the adjustment is honestly empty.  The same
    # prior does move a bar that exists (the real-task declaration carries one).
    assert audit["adjustment_rule"] == "none"
    assert audit["parameters"] == []
    bet = json.loads((store / "bet.json").read_text(encoding="utf-8"))
    assert bet["expected_effects"][0]["min_delta"] == 0.0
    moved, audit2 = ep.adjust_declared(
        {"min_delta": 1.0, "replicates": 1, "require_baseline_rerun": True},
        json.loads((store / "context" / "prior.json").read_text(encoding="utf-8")))
    assert moved["min_delta"] == 2.5                  # 1.0 * (1 + 3 * 0.5)
    assert audit2["adjustment_rule"] == "refuted_history_raises_the_bar"
