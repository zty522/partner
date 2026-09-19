"""Abstention -> bounded exploration -> new evidence.

An abstention must not be a dead end.  When a class carries only failure evidence the
harness declines to wager -- and then tries exactly ONE action variant of that same class
that no settled bet has tried yet, under its own smaller budget, so later decisions have
something new to read.  Everything is declared: the variants come from the task, the
thresholds are module constants, and every refusal is recorded.
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
from partner.application.real_task_adapter import declared_variants, real_task_budget  # noqa: E402
from partner.commitment import exploration as ex  # noqa: E402
from partner.events import commitment as C  # noqa: E402
from partner.index.job_repository import init as init_jobs  # noqa: E402

PROJECT_ID = "molecular_generation"
TASK_ID = "explore_after_abstention_probe"
OFF_TASK_ID = "explore_after_abstention_probe_off"
PARTIAL = {"variant_id": "partial_propose", "patch_file": "patch_partial.diff",
           "selected_action": "patch_patch_partial"}
FULL = {"variant_id": "full_contract", "patch_file": "patch_full.diff",
        "selected_action": "patch_patch_full"}
SIGNATURE = "tests_passed:delta_over_baseline:increase"


# --- fixtures on disk ---------------------------------------------------------

def _effects_from_signature(signature: str) -> list[dict]:
    out = []
    for part in signature.split(";"):
        metric, kind, direction = part.split(":")
        out.append({"metric": metric, "kind": kind, "direction": direction,
                    "min_delta": 2.0, "threshold": 6.0})
    return out


def _components(project_id: str, task_id: str, signature: str) -> dict:
    return ep.class_components(project_id=project_id, action_id=f"real_task:{task_id}",
                               metric_signature=signature)


def _settle(workspace: Path, *, project_id: str, task_id: str, signature: str, index: int,
            settlement_class: str, selected_action: str = PARTIAL["selected_action"],
            exploration: bool = False, trace_token: str = "",
            stamp: str = "") -> str:
    """One real-shaped settled bet of a class, exactly as the store writes them."""
    key = ep.task_class_key(**_components(project_id, task_id, signature))
    bet_id = f"bet_fx_{task_id}_{index}"
    store = workspace / "state" / "commitments" / f"event_flow_{bet_id}" / bet_id
    (store / "settlement").mkdir(parents=True, exist_ok=True)
    (store / "context").mkdir(parents=True, exist_ok=True)
    (store / "bet.json").write_text(json.dumps({
        "bet_id": bet_id, "run_id": f"event_flow_{bet_id}", "project_id": project_id,
        "data_version": f"class:{key}", "selected_action": selected_action,
        "selection_reason": "declared", "question": "does the declared patch pass?",
        "expected_effects": _effects_from_signature(signature),
        "created_at": stamp or f"2001-01-01T00:{index:02d}:00"}), encoding="utf-8")
    (store / "context" / "snapshot.json").write_text(json.dumps({
        "trace_token": trace_token or f"fx_{task_id}_{index}", "task_id": task_id,
        "exploration": bool(exploration),
        "variant_id": (FULL if selected_action == FULL["selected_action"] else PARTIAL)["variant_id"]}),
        encoding="utf-8")
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
        "publish_eligible": False, "publish_blockers": [], "machine_rules": [],
        "created_at": (stamp or f"2001-01-01T00:{index:02d}:00")}), encoding="utf-8")
    return key


def _job(workspace: Path, *, job_id: str, message: str, project_id: str = PROJECT_ID) -> SimpleNamespace:
    init_jobs(workspace).upsert_from_record({
        "job_id": job_id, "project_id": project_id, "status": "running",
        "flow_id": f"flow_{job_id}", "assigned_instance": "02", "request": message,
        "priority": 100, "next_run_at": 0})
    return SimpleNamespace(workspace=str(workspace), job_id=job_id, instance_id="02",
                           project_id=project_id)


def _abstain_message(token: str, *, task_id: str = TASK_ID, extra: str = "") -> str:
    return (f"\u3010\u540c\u7c7b\u4efb\u52a1\u3011\u8bf7\u5148\u8bfb\u540c\u7c7b\u5386\u53f2\uff1a"
            f"real_task:{task_id}\n{extra}\ntrace token: {token}\n")


def _trigger(workspace: Path, *, job_id: str = "job_explore_probe",
             token: str = "explA_trace_02",
             task_id: str = TASK_ID, extra: str = "") -> tuple[dict, dict]:
    ctx = _job(workspace, job_id=job_id, message=_abstain_message(token, task_id=task_id,
                                                                  extra=extra))
    # the flow order is recall -> record -> execute: the prior the record node freezes is
    # the one the recall node left, and that is what can trigger an abstention
    recalled = C.commitment_prior_recall(ctx, {})
    assert recalled["ok"] is True, recalled
    recorded = C.commitment_bet_record(ctx, {})
    assert recorded["ok"] is True, recorded
    executed = C.commitment_bet_execute(ctx, {})
    return ctx, executed


def _explore_jobs(workspace: Path) -> list[dict]:
    from partner.index.sqlite_base import get_connection
    rows = get_connection(Path(init_jobs(workspace).db_path)).execute(
        "SELECT job_id, status, channel, sender_id, route, request FROM jobs "
        "WHERE sender_id LIKE ?", (f"{ex.EXPLORE_SENDER_PREFIX}%",)).fetchall()
    return [dict(r) for r in rows]


# --- the kernel's conditions --------------------------------------------------

def test_the_exploration_rides_a_channel_the_delivery_layer_supports():
    """A private channel name would make the exploration's own flow fail at its send node
    ("unsupported delivery channel") *after* its bet had already settled."""
    assert ex.EXPLORE_CHANNEL in ex.DELIVERY_CHANNELS
    assert ex.EXPLORE_SENDER_PREFIX.startswith("commitment_explore:")


def test_the_thresholds_are_named_constants():
    assert ex.EXPLORE_BUDGET_RATIO == 0.25
    assert ex.EXPLORE_MAX_PER_WINDOW == 1
    assert ex.EXPLORE_WINDOW_SECONDS == 7200
    assert ex.EXPLORE_MIN_ACTIONS == 2
    assert (ex.EXPLORE_SKIPPED_DISABLED, ex.EXPLORE_SKIPPED_NO_BUDGET,
            ex.EXPLORE_SKIPPED_PENDING, ex.EXPLORE_SKIPPED_WINDOW_LIMIT,
            ex.EXPLORE_SKIPPED_NO_VARIANT) == ("explore_disabled", "no_budget",
                                               "explore_already_pending", "window_limit",
                                               "explore_no_variant_available")
    assert ex.EXPLORE_TOKEN_PREFIX == "explore_trace_"
    assert "trace" in ex.explore_trace_token("cls_x", epoch=1)


def test_the_exploration_budget_is_a_fraction_and_never_erodes_the_declared_one():
    declared = {"wall_clock_seconds": 600, "model_calls": 1, "actions": 4, "rounds": 1}
    derived = ex.exploration_budget(declared)
    assert derived["wall_clock_seconds"] == 150          # 600 * 0.25
    assert derived["actions"] == 2                       # floored at one bounded action
    assert derived["model_calls"] == 1 and derived["rounds"] == 1
    assert declared == {"wall_clock_seconds": 600, "model_calls": 1, "actions": 4, "rounds": 1}
    assert real_task_budget(exploration=False) == declared          # the wager keeps its own
    assert real_task_budget(exploration=True)["wall_clock_seconds"] == 150


def test_no_budget_means_no_exploration():
    decision = ex.should_explore(budget={"wall_clock_seconds": 1, "actions": 4, "rounds": 1},
                                 variant=FULL)
    assert decision["explore"] is False
    assert decision["reason"] == ex.EXPLORE_SKIPPED_NO_BUDGET


def test_the_window_and_the_pending_rule_bound_the_exploration():
    good = {"wall_clock_seconds": 600, "actions": 4, "rounds": 1}
    assert ex.should_explore(budget=good, explored_in_window=1,
                             variant=FULL)["reason"] == ex.EXPLORE_SKIPPED_WINDOW_LIMIT
    assert ex.should_explore(budget=good, pending=1, variant=FULL)["reason"] == \
        ex.EXPLORE_SKIPPED_PENDING
    assert ex.should_explore(budget=good, variant=None)["reason"] == \
        ex.EXPLORE_SKIPPED_NO_VARIANT
    assert ex.should_explore(enabled=False, budget=good, variant=FULL)["reason"] == \
        ex.EXPLORE_SKIPPED_DISABLED
    assert ex.should_explore(budget=good, variant=FULL)["explore"] is True


def test_the_variant_is_the_first_declared_one_the_class_has_not_tried():
    candidates = [PARTIAL, FULL]
    assert ex.choose_variant(candidates, [])["variant_id"] == "partial_propose"
    assert ex.choose_variant(candidates, [PARTIAL["selected_action"]])["variant_id"] == \
        "full_contract"
    assert ex.choose_variant(candidates, [PARTIAL["selected_action"],
                                          FULL["selected_action"]]) is None
    assert ex.choose_variant([], []) is None


def test_the_window_counts_only_explorations_of_that_class():
    rows = [{"bet_id": "b1", "class_key": "cls_a", "settled_epoch": 9000.0,
             "trace_token": "explore_trace_cls_a_1", "exploration": True,
             "selected_action": "patch_patch_full"},
            {"bet_id": "b2", "class_key": "cls_a", "settled_epoch": 9000.0,
             "trace_token": "normal_trace_1", "exploration": False,
             "selected_action": "patch_patch_partial"},
            {"bet_id": "b3", "class_key": "cls_b", "settled_epoch": 9000.0,
             "trace_token": "explore_trace_cls_b_1", "exploration": True,
             "selected_action": "patch_patch_full"},
            {"bet_id": "b4", "class_key": "cls_a", "settled_epoch": 100.0,
             "trace_token": "explore_trace_cls_a_0", "exploration": True,
             "selected_action": "patch_patch_full"}]
    # inside the window (7200s back from 10000) only b1 counts; b4 is older, b2 is no
    # exploration and b3 belongs to another class
    window = ex.exploration_window(rows, class_key="cls_a", now=10000.0, window_seconds=7200)
    assert window["explored_in_window"] == 1 and window["bet_ids"] == ["b1"]
    old = ex.exploration_window(rows, class_key="cls_a", now=100000.0, window_seconds=7200)
    assert old["explored_in_window"] == 0
    assert ex.tried_actions(rows, class_key="cls_a") == ["patch_patch_full",
                                                        "patch_patch_partial"]


def test_the_declared_variants_come_from_the_task_and_keep_the_class_key():
    task = json.loads((REPO_ROOT / "benchmarks" / "real_tasks" / TASK_ID /
                       "task.json").read_text(encoding="utf-8"))
    variants = declared_variants(task)
    assert [v["variant_id"] for v in variants] == ["partial_propose", "full_contract"]
    assert [v["selected_action"] for v in variants] == ["patch_patch_partial",
                                                       "patch_patch_full"]
    # the class key never depends on which variant a bet carries
    base = dict(project_id=PROJECT_ID, action_id=f"real_task:{TASK_ID}",
                metric_signature=SIGNATURE)
    assert ep.task_class_key(**base) == ep.class_key_from_bet(
        {"project_id": PROJECT_ID, "data_version": f"class:{ep.task_class_key(**base)}",
         "expected_effects": _effects_from_signature(SIGNATURE)})


# --- through the Event layer --------------------------------------------------

def test_an_abstention_triggers_exactly_one_exploration_of_an_untried_variant(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    key = _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE,
                  index=1, settlement_class="falsified")
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE,
            index=2, settlement_class="falsified")
    ctx = _job(workspace, job_id="job_explore_probe",
               message=_abstain_message("explA_trace_02"))
    before = {r[0]: (r[1], r[2]) for r in _all_jobs(workspace)}
    assert C.commitment_prior_recall(ctx, {})["ok"] is True
    assert C.commitment_bet_record(ctx, {})["ok"] is True
    executed = C.commitment_bet_execute(ctx, {})
    out = executed["semantic_output"]
    assert out["settlement_class"] == "abstained"
    exploration = out["exploration"]
    assert exploration["explored"] is True
    assert exploration["reason"] == ex.EXPLORE_TRIGGERED
    assert exploration["noted"] is True
    detail = exploration["detail"]
    assert detail["class_key"] == key
    assert detail["tried_actions"] == ["patch_patch_partial"]
    assert detail["variant_id"] == "full_contract"            # the untried one
    assert detail["exploration_budget"]["wall_clock_seconds"] == 150
    assert exploration["trace_token"].startswith(f"{ex.EXPLORE_TOKEN_PREFIX}{key}_")
    # the exploration job really exists, on its own channel, under the formal service
    jobs = _explore_jobs(workspace)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["job_id"] == exploration["explore_job_id"]
    assert job["sender_id"] == f"{ex.EXPLORE_SENDER_PREFIX}{key}"
    assert job["route"] == "project_iteration"                # the real Event flow
    assert "real_task" in job["request"] and TASK_ID in job["request"]
    assert f"patch={FULL['patch_file']}" in job["request"]    # the untried variant
    assert "explore=on" in job["request"]
    assert f"explore_parent=bet_{ctx.job_id}" in job["request"]
    assert "explore_tried=patch_patch_partial" in job["request"]
    # the event log carries the trigger, and history was not rewritten
    kinds = [row.get("kind") for row in init_jobs(workspace).history(ctx.job_id)]
    assert "commitment.explore_triggered" in kinds
    after = {r[0]: (r[1], r[2]) for r in _all_jobs(workspace)}
    for job_id, state in before.items():
        if job_id != ctx.job_id:
            assert after[job_id] == state, f"existing job {job_id} was modified"
    assert set(after) - set(before) == {exploration["explore_job_id"]}
    trigger_bet = json.loads((workspace / "state" / "commitments" / f"event_flow_{ctx.job_id}" /
                              f"bet_{ctx.job_id}" / "bet.json").read_text(encoding="utf-8"))
    assert trigger_bet["selected_action"] == "abstain"       # the dead end, frozen as such


def _all_jobs(workspace: Path) -> list[tuple]:
    from partner.index.sqlite_base import get_connection
    rows = get_connection(Path(init_jobs(workspace).db_path)).execute(
        "SELECT job_id, status, updated_at FROM jobs").fetchall()
    return [tuple(r) for r in rows]


def test_explore_off_skips_the_exploration_and_says_why(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=1,
            settlement_class="falsified")
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=2,
            settlement_class="falsified")
    ctx, executed = _trigger(workspace, job_id="job_off_probe", token="explOFF_trace_02",
                             extra="explore=off")
    out = executed["semantic_output"]
    assert out["settlement_class"] == "abstained"             # the abstention still stands
    exploration = out["exploration"]
    assert exploration["explored"] is False
    assert exploration["reason"] == ex.EXPLORE_SKIPPED_DISABLED
    assert _explore_jobs(workspace) == []
    kinds = [row.get("kind") for row in init_jobs(workspace).history(ctx.job_id)]
    assert "commitment.explore_skipped" in kinds
    assert "commitment.explore_triggered" not in kinds


def test_the_window_limit_and_the_variant_pool_bound_the_trigger(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=1,
            settlement_class="falsified")
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=2,
            settlement_class="falsified")
    # the class already had its one exploration of the window
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=3,
            settlement_class="falsified", exploration=True,
            trace_token=f"{ex.EXPLORE_TOKEN_PREFIX}cls_{TASK_ID}_1700000000")
    _, executed = _trigger(workspace, job_id="job_window_probe", token="explW_trace_02")
    assert executed["semantic_output"]["exploration"]["reason"] == ex.EXPLORE_SKIPPED_WINDOW_LIMIT
    assert _explore_jobs(workspace) == []


def test_without_an_untried_variant_the_exploration_is_refused_not_invented(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    for index, action in enumerate((PARTIAL["selected_action"], FULL["selected_action"]), start=1):
        _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE,
                index=index, settlement_class="falsified", selected_action=action)
    ctx, executed = _trigger(workspace, job_id="job_novariant_probe", token="explN_trace_02")
    out = executed["semantic_output"]
    assert out["settlement_class"] == "abstained"
    assert out["exploration"]["explored"] is False
    assert out["exploration"]["reason"] == ex.EXPLORE_SKIPPED_NO_VARIANT
    assert out["exploration"]["detail"]["tried_actions"] == ["patch_patch_full",
                                                            "patch_patch_partial"]
    assert _explore_jobs(workspace) == []
    kinds = [row.get("kind") for row in init_jobs(workspace).history(ctx.job_id)]
    assert "commitment.explore_skipped" in kinds


def test_a_pending_exploration_is_not_duplicated(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=1,
            settlement_class="falsified")
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=2,
            settlement_class="falsified")
    ctx, executed = _trigger(workspace, job_id="job_pending_1", token="explP1_trace_02")
    assert executed["semantic_output"]["exploration"]["explored"] is True
    key = executed["semantic_output"]["exploration"]["detail"]["class_key"]
    # new falsified evidence arrives, and a second abstention follows while the first
    # exploration is still queued: it must not add a second exploration
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=4,
            settlement_class="falsified", stamp="2099-01-01T00:04:00")
    _, second = _trigger(workspace, job_id="job_pending_2", token="explP2_trace_02")
    out = second["semantic_output"]
    assert out["exploration"]["explored"] is False
    assert out["exploration"]["reason"] == ex.EXPLORE_SKIPPED_PENDING
    assert len(_explore_jobs(workspace)) == 1
    assert _explore_jobs(workspace)[0]["sender_id"].endswith(key)


def test_the_exploration_settlement_is_read_by_the_next_prior_and_flips_the_decision(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    key = _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE,
                  index=1, settlement_class="falsified")
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=2,
            settlement_class="falsified")
    ctx, executed = _trigger(workspace, job_id="job_before_explore", token="explB_trace_02")
    assert executed["semantic_output"]["exploration"]["explored"] is True

    def recall(job_id: str) -> dict:
        node = C.commitment_prior_recall(
            _job(workspace, job_id=job_id, message=_abstain_message("after_trace_02")), {})
        return node["semantic_output"]

    def freeze(job_id: str, token: str) -> tuple[dict, dict]:
        ctx = _job(workspace, job_id=job_id, message=_abstain_message(token))
        assert C.commitment_prior_recall(ctx, {})["ok"] is True
        assert C.commitment_bet_record(ctx, {})["ok"] is True
        store = (workspace / "state" / "commitments" / f"event_flow_{job_id}" / f"bet_{job_id}")
        return (json.loads((store / "context" / "snapshot.json").read_text(encoding="utf-8")),
                json.loads((store / "bet.json").read_text(encoding="utf-8")))

    # the dead end: the triggering bet abstained, and its record says so
    trigger_bet = json.loads((workspace / "state" / "commitments" /
                              "event_flow_job_before_explore" /
                              "bet_job_before_explore" / "bet.json").read_text(encoding="utf-8"))
    assert trigger_bet["selected_action"] == "abstain"

    # before the exploration lands: no success evidence at all, and the next bet is still
    # barred from abstaining only because the tail is the abstention itself
    snap_before, bet_before = freeze("job_read_before", "readB_trace_02")
    assert snap_before["prior"]["counts"]["supported"] == 0
    assert snap_before["prior"]["counts"]["falsified"] == 2
    assert snap_before["prior"]["abstention"]["abstain"] is False

    # the exploration settles: a real row, same class key, marked as an exploration
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=5,
            settlement_class="supported", selected_action=FULL["selected_action"],
            exploration=True, stamp="2099-01-01T00:05:00",
            trace_token=f"{ex.EXPLORE_TOKEN_PREFIX}{key}_1700000001")

    snap_after, bet_after = freeze("job_read_after", "readA_trace_02")
    assert snap_after["prior"]["counts"]["supported"] == 1            # the new evidence is read
    assert snap_after["prior"]["counts"]["falsified"] == 2
    assert any("supported" in str(reason)                        # ... and it is what blocks
               for reason in snap_after["prior"]["abstention"]["blocked_by"])
    assert (snap_after["prior"]["weighted"]["refuted_ratio"]
            < snap_before["prior"]["weighted"]["refuted_ratio"])          # the failure rate moved
    assert snap_after["prior"]["row_count"] == 4
    # the decision itself: the bet wagers on a real action instead of declining to
    assert bet_after["selected_action"] != "abstain"
    assert bet_before["selected_action"] != "abstain"


def test_an_exploration_bet_may_not_abstain_itself(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=1,
            settlement_class="falsified")
    _settle(workspace, project_id=PROJECT_ID, task_id=TASK_ID, signature=SIGNATURE, index=2,
            settlement_class="falsified")
    ctx = _job(workspace, job_id="job_explore_bet",
               message=_abstain_message("explE_trace_02") + "\nexplore=on\n")
    assert C.commitment_prior_recall(ctx, {})["ok"] is True
    recorded = C.commitment_bet_record(ctx, {})
    assert recorded["ok"] is True
    store = workspace / "state" / "commitments" / "event_flow_job_explore_bet" / "bet_job_explore_bet"
    snapshot = json.loads((store / "context" / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["exploration"] is True
    abstention = snapshot["prior"]["abstention"]
    assert abstention["abstain"] is False
    assert abstention["suppressed_for"] == ex.EXPLORE_ABSTENTION_SUPPRESSED
    assert abstention["evidence"]["would_have_abstained"] is True  # recorded, not hidden
    bet = json.loads((store / "bet.json").read_text(encoding="utf-8"))
    assert bet["budget"]["wall_clock_seconds"] == 150              # its own smaller budget
    assert bet["budget"]["actions"] == 2


def test_the_class_and_similarity_judgements_are_untouched():
    own = ep.class_components(project_id="p1", action_id="real_task:x",
                              metric_signature="m:t:increase")
    other = ep.class_components(project_id="p1", action_id="real_task:y",
                                metric_signature="m:t:increase")
    assert ep.similarity_weight(own, other) == 0.5
    pairs = ep.related_class_keys(ep.task_class_key(**own),
                                  {ep.task_class_key(**other): other}, components=own)
    assert pairs == [(ep.task_class_key(**other), 0.5)]
    assert ep.task_class_key(**own) == ep.task_class_key(project_id="p1",
                                                         action_id="real_task:x",
                                                         metric_signature="m:t:increase")
