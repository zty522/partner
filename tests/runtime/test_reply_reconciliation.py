"""The settlement is the source of truth for the reply.

The draft prose and the machine verdict come from different parts of the flow and can
disagree -- that happened for real: a ``supported`` settlement went out with a reply
saying the task made no progress.  These tests pin the fix: before the send step the
reply body is rebuilt from the settlement, a contradicting draft is dropped and the
contradiction is recorded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from partner.index.job_repository import init as init_jobs  # noqa: E402

CONTRADICTING_DRAFT = ("本轮契约修复任务未取得进展：执行阶段后台进程没有留下终态回执，"
                       "属执行失败，不是数据被证伪；核验阶段也没有可验证的业务数据。")
AGREEING_DRAFT = "已完成本轮修复，测试通过。"


def _store_with_settlement(tmp_path: Path, job_id: str) -> Path:
    store = (tmp_path / "ws" / "state" / "commitments" / f"event_flow_{job_id}"
             / f"bet_{job_id}")
    (store / "settlement").mkdir(parents=True, exist_ok=True)
    (store / "state.json").write_text(json.dumps({
        "state": "CLOSED", "settled": True, "settled_class": "supported"}),
        encoding="utf-8")
    (store / "settlement" / f"stl_{job_id}_r1.json").write_text(json.dumps({
        "settlement_id": f"stl_{job_id}_r1", "bet_id": f"bet_{job_id}",
        "settlement_class": "supported", "expectations_met": True,
        "improvement_over_baseline": True,
        "supported_claim": "improvement_over_baseline", "publish_eligible": False,
        "publish_blockers": ["environment_not_publishable:isolated_sample",
                             "isolated_sample_only", "single_episode_only"]}),
        encoding="utf-8")
    return store


def _workspace_with_job(tmp_path: Path, job_id: str) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    init_jobs(workspace).upsert_from_record({
        "job_id": job_id, "project_id": "molecular_generation", "status": "running",
        "flow_id": "flow_x", "assigned_instance": "02", "request": "do the thing",
        "priority": 100, "next_run_at": 0})
    return workspace


def _ctx(workspace: Path, job_id: str) -> SimpleNamespace:
    return SimpleNamespace(workspace=str(workspace), job_id=job_id, instance_id="02",
                           project_id="molecular_generation")


def test_the_handler_is_a_pure_contradiction_check():
    from partner.events.commitment import draft_contradicts

    settlement = {"settlement_class": "supported", "improvement_over_baseline": True}
    assert draft_contradicts(CONTRADICTING_DRAFT, settlement)[0] is True
    assert draft_contradicts(AGREEING_DRAFT, settlement)[0] is False
    refuted = {"settlement_class": "refuted", "improvement_over_baseline": False}
    assert draft_contradicts(AGREEING_DRAFT, refuted)[0] is True
    assert draft_contradicts("没有取得改善。", settlement)[0] is True


def test_the_reply_body_is_rebuilt_from_the_settlement(tmp_path):
    from partner.events.commitment import commitment_reply_reconcile

    job_id = "job_reconcile"
    workspace = _workspace_with_job(tmp_path, job_id)
    _store_with_settlement(tmp_path, job_id)
    params = {"flow_outputs": {"deduplicate": {"message": AGREEING_DRAFT}}}
    result = commitment_reply_reconcile(_ctx(workspace, job_id), params)
    out = result["semantic_output"]
    assert out["settlement_present"] is True
    assert out["contradiction"] is False
    body = out["settlement_message"]
    for field in ("settlement_class=supported", "improvement_over_baseline=True",
                  "supported_claim=improvement_over_baseline", "publish_eligible=False",
                  "publish_blockers=environment_not_publishable:isolated_sample",
                  "expectations_met=True"):
        assert field in body, field
    assert AGREEING_DRAFT in body
    assert body.count(AGREEING_DRAFT) == 1, "the draft must appear once, not twice"
    # top level as well, so a delivery Event reading node outputs directly finds it
    assert result["settlement_message"] == body


def test_a_contradicting_draft_is_dropped_and_recorded(tmp_path):
    from partner.events.commitment import commitment_reply_reconcile

    job_id = "job_contradiction"
    workspace = _workspace_with_job(tmp_path, job_id)
    _store_with_settlement(tmp_path, job_id)
    params = {"flow_outputs": {"deduplicate": {"message": CONTRADICTING_DRAFT}}}
    result = commitment_reply_reconcile(_ctx(workspace, job_id), params)
    out = result["semantic_output"]
    assert out["contradiction"] is True and out["draft_replaced"] is True
    assert "未取得进展" not in out["settlement_message"]
    assert "settlement_class=supported" in out["settlement_message"]
    history = init_jobs(workspace).history(job_id)
    assert any(row.get("kind") == "commitment_reply_contradiction" for row in history)


def test_without_a_settlement_the_draft_stands(tmp_path):
    from partner.events.commitment import commitment_reply_reconcile

    job_id = "job_no_settlement"
    workspace = _workspace_with_job(tmp_path, job_id)
    result = commitment_reply_reconcile(_ctx(workspace, job_id),
                                        {"flow_outputs": {"deduplicate": {"message": "hi"}}})
    assert result["semantic_output"]["settlement_present"] is False


def test_the_log_channel_writes_the_settlement_body_not_the_contradicting_draft(tmp_path):
    """End to end through the delivery Event: what lands on the channel is the verdict."""
    from partner.events.commitment import commitment_reply_reconcile
    from partner.events.delivery import send_text

    job_id = "job_delivery"
    workspace = _workspace_with_job(tmp_path, job_id)
    _store_with_settlement(tmp_path, job_id)
    reconcile = commitment_reply_reconcile(
        _ctx(workspace, job_id),
        {"flow_outputs": {"deduplicate": {"message": CONTRADICTING_DRAFT}}})
    # the flow stores the handler's WHOLE result under the node id -- using the
    # flattened semantic_output here would hide the very bug this test exists for
    flow_outputs = {"deduplicate": {"message": CONTRADICTING_DRAFT},
                    "commitment_reconcile": reconcile}
    sent = send_text(_ctx(workspace, job_id),
                     {"channel": "log", "flow_outputs": flow_outputs})
    assert sent["ok"] is True and sent["delivered"] is True
    line = json.loads((workspace / "state" / "outbound" / "replies.log")
                      .read_text(encoding="utf-8").strip().splitlines()[-1])
    body = line["content"]
    assert "settlement_class=supported" in body
    assert "publish_eligible=False" in body
    assert "未取得进展" not in body
    # what lands on the channel must be byte-identical to what the reconcile Event
    # recorded as the reply body, or the audit digest is worthless
    assert body == reconcile["settlement_message"]


def test_a_contradicting_draft_is_not_repeated_in_the_body(tmp_path):
    """The delivered body carries the verdict, not a re-statement of the bad draft."""
    from partner.events.commitment import commitment_reply_reconcile

    job_id = "job_no_echo"
    workspace = _workspace_with_job(tmp_path, job_id)
    _store_with_settlement(tmp_path, job_id)
    result = commitment_reply_reconcile(
        _ctx(workspace, job_id),
        {"flow_outputs": {"deduplicate": {"message": CONTRADICTING_DRAFT}}})
    body = result["settlement_message"]
    assert CONTRADICTING_DRAFT not in body
    assert body.count(CONTRADICTING_DRAFT) == 0
    assert result["draft_mentions_verdict"] is False


def test_the_reconciliation_artifact_is_written_and_self_consistent(tmp_path):
    """A silent failure here would hide exactly the audit the round depends on."""
    import hashlib as _hash

    from partner.events.commitment import commitment_reply_reconcile

    job_id = "job_artifact"
    workspace = _workspace_with_job(tmp_path, job_id)
    store = _store_with_settlement(tmp_path, job_id)
    result = commitment_reply_reconcile(_ctx(workspace, job_id),
                                        {"flow_outputs": {"deduplicate": {"message": AGREEING_DRAFT}}})
    out = result["semantic_output"]
    assert out["artifact_error"] == ""
    artifact = json.loads((store / "reply_reconciliation.json").read_text(encoding="utf-8"))
    body = result["settlement_message"]
    assert artifact["body_digest"] == _hash.sha256(body.encode("utf-8")).hexdigest()
    assert artifact["settlement_id"] == out["settlement_id"]
    assert artifact["publish_blockers"] == out["publish_blockers"]
    assert result["files"] == [str(store / "reply_reconciliation.json")]
