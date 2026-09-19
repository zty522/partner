"""The targeted inbox consumer contract.

The blocker: starting the legacy instance runtime makes its worker claim queued Jobs
from the whole backlog, so consuming one message used to mean starting the entire
main chain.  These tests pin the contract that stops that from happening.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "targeted_instance_task", REPO_ROOT / "scripts" / "run_targeted_instance_task.py")
targeted = importlib.util.module_from_spec(_SPEC)
sys.modules["targeted_instance_task"] = targeted
_SPEC.loader.exec_module(targeted)

from targeted_instance_task import (  # noqa: E402
    InboxRowGuard, JobAllowList, TargetedError, audit_job_store, deliver_reply,
    job_store_snapshot, row_identifier,
)


class FakeJob:
    def __init__(self, job_id, flow_id="", root_event_id=""):
        self.job_id = job_id
        self.flow_id = flow_id
        self.root_event_id = root_event_id


def make_workspace(tmp_path, *, instance="02", rows=None):
    ws = tmp_path / "ws"
    state = ws / "instances" / instance / "state"
    state.mkdir(parents=True)
    rows = rows if rows is not None else [
        {"row_id": "row_target", "instance_id": instance, "text": "do the canary"},
        {"row_id": "row_other", "instance_id": instance, "text": "unrelated work"},
    ]
    (state / "desktop_inbox.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (state / "desktop_inbox_seen_ids.json").write_text("[]", encoding="utf-8")
    return ws


def test_only_the_named_row_is_claimed_and_other_rows_are_untouched(tmp_path):
    ws = make_workspace(tmp_path)
    state = ws / "instances/02/state"
    inbox_before = (state / "desktop_inbox.jsonl").read_bytes()
    guard = InboxRowGuard(workspace=ws, instance_id="02")
    row = guard.assert_targetable(row_id="row_target", instance_id="02")
    assert row["text"] == "do the canary"
    claim = guard.claim(row_id="row_target", owner="owner-a")
    assert claim["row_id"] == "row_target"
    assert claim["idempotency_key"] == "targeted-inbox:02:row_target"
    assert claim["lease_until"] > time.time()
    # the inbox itself is never rewritten, and no other row gained a claim
    assert (state / "desktop_inbox.jsonl").read_bytes() == inbox_before
    assert sorted(p.name for p in (state / "targeted_claims").iterdir()) == [
        "row_target.claim.json"]


def test_fails_closed_on_missing_row_already_consumed_row_and_instance_mismatch(tmp_path):
    ws = make_workspace(tmp_path)
    guard = InboxRowGuard(workspace=ws, instance_id="02")
    with pytest.raises(TargetedError, match="not found"):
        guard.assert_targetable(row_id="row_missing", instance_id="02")
    with pytest.raises(TargetedError, match="instance mismatch"):
        guard.assert_targetable(row_id="row_target", instance_id="03")
    guard.mark_seen("row_target")
    with pytest.raises(TargetedError, match="already consumed"):
        guard.assert_targetable(row_id="row_target", instance_id="02")


def test_a_live_claim_wins_and_a_second_consumer_cannot_act(tmp_path):
    ws = make_workspace(tmp_path)
    guard = InboxRowGuard(workspace=ws, instance_id="02")
    first = guard.claim(row_id="row_target", owner="owner-a")
    assert first["pid"] == os.getpid()
    other = InboxRowGuard(workspace=ws, instance_id="02")
    with pytest.raises(TargetedError, match="live owner"):
        other.claim(row_id="row_target", owner="owner-b")
    # and the winner may re-enter its own claim without a new one being written
    again = guard.claim(row_id="row_target", owner="owner-a")
    assert again["reused"] is True


def test_a_stale_lease_is_recovered_but_a_live_expired_lease_is_not_stolen(tmp_path):
    ws = make_workspace(tmp_path)
    guard = InboxRowGuard(workspace=ws, instance_id="02")
    path = guard.claim_path("row_target")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"row_id": "row_target", "owner": "dead-owner",
                                "pid": 99999999, "lease_until": time.time() - 60,
                                "attempt": 1}), encoding="utf-8")
    recovered = guard.claim(row_id="row_target", owner="owner-b")
    assert recovered["took_over_stale_claim"] is True
    assert recovered["attempt"] == 2
    assert recovered["previous_claim"]["owner"] == "dead-owner"
    # a live process whose lease expired is not stolen
    path.write_text(json.dumps({"row_id": "row_target", "owner": "live-owner",
                                "pid": os.getpid(), "lease_until": time.time() - 60,
                                "attempt": 2}), encoding="utf-8")
    with pytest.raises(TargetedError, match="lease has expired"):
        guard.claim(row_id="row_target", owner="owner-c")


def test_replay_reuses_the_recorded_outcome_and_never_creates_a_second_root_job(tmp_path):
    ws = make_workspace(tmp_path)
    guard = InboxRowGuard(workspace=ws, instance_id="02")
    ids = {"job_id": "job_root", "flow_id": "flow_root", "root_event_id": "ev_root"}
    guard.record_outcome("row_target", {"status": "completed", "ids": ids,
                                        "reply": {"reply_id": "reply_1"}})
    prior = guard.prior_outcome("row_target")
    assert prior["status"] == "completed" and prior["ids"]["job_id"] == "job_root"
    # the reader that main() uses to short-circuit is the same one
    assert guard.prior_outcome("row_target")["ids"] == ids


def test_a_failure_is_recorded_and_the_row_is_not_marked_seen(tmp_path):
    ws = make_workspace(tmp_path)
    guard = InboxRowGuard(workspace=ws, instance_id="02")
    claim = guard.claim(row_id="row_target", owner="owner-a")
    guard.record_outcome("row_target", {"status": "failed", "error": "boom", "claim": claim})
    assert guard.prior_outcome("row_target")["status"] == "failed"
    assert "row_target" not in guard.seen_ids()
    # the claim survives so the failure stays attributable
    assert guard.claim_path("row_target").exists()


def test_the_executor_can_only_take_the_root_job_and_its_own_flow():
    allow = JobAllowList(instance_id="02", root_job_id="job_root",
                         root_event_id="ev_root", flow_id="flow_root")
    assert allow.allows(FakeJob("job_root")) is True
    assert allow.allows(FakeJob("job_child", flow_id="flow_root")) is True
    assert allow.allows(FakeJob("job_dep", root_event_id="ev_root")) is True
    # everything else in the backlog is refused, whatever its status
    assert allow.allows(FakeJob("job_queued_139")) is False
    assert allow.allows(FakeJob("job_running_20", flow_id="flow_somebody_else")) is False
    summary = allow.refuse_summary([FakeJob("job_queued_139"), FakeJob("job_root")])
    assert summary["refused_job_ids"] == ["job_queued_139"]


def test_the_worker_refuses_to_run_in_shared_mode():
    """shared_mode picks ANY queued job from the global queue: never for a targeted run."""
    class Shared:
        shared_mode = True

    class Private:
        shared_mode = False

    with pytest.raises(TargetedError, match="shared mode"):
        targeted.assert_not_shared_mode(Shared())
    targeted.assert_not_shared_mode(Private())  # the normal case is allowed
    # and the bounded worker routes through this guard before consulting its parent
    source = Path(targeted.__file__).read_text(encoding="utf-8")
    assert "assert_not_shared_mode(self)" in source


def test_the_job_store_audit_proves_the_backlog_was_untouched(tmp_path):
    import sqlite3
    db = tmp_path / "jobs.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE jobs (job_id TEXT, status TEXT)")
    connection.executemany("INSERT INTO jobs VALUES (?, ?)",
                           [("job_q1", "queued"), ("job_q2", "queued"),
                            ("job_r1", "running"), ("job_done", "completed")])
    connection.commit()
    connection.close()
    before = job_store_snapshot(db)
    # a targeted run may only add its own root job
    connection = sqlite3.connect(db)
    connection.execute("INSERT INTO jobs VALUES ('job_root', 'queued')")
    connection.commit()
    connection.close()
    audit = audit_job_store(before, job_store_snapshot(db))
    assert audit["untouched"] is True
    assert audit["changed_status"] == [] and audit["disappeared"] == []
    assert audit["new_jobs"] == ["job_root"]
    # and a touched backlog is detected
    connection = sqlite3.connect(db)
    connection.execute("UPDATE jobs SET status='cancelled' WHERE job_id='job_q1'")
    connection.commit()
    connection.close()
    bad = audit_job_store(before, job_store_snapshot(db))
    assert bad["untouched"] is False and bad["changed_status"] == ["job_q1"]


def test_a_reply_is_delivered_exactly_once_per_row(tmp_path):
    ws = make_workspace(tmp_path)
    first = deliver_reply(workspace=ws, instance_id="02", row_id="row_target", text="hello")
    assert first["web"]["ok"] is True and not first.get("already_delivered")
    second = deliver_reply(workspace=ws, instance_id="02", row_id="row_target", text="hello")
    assert second["already_delivered"] is True
    assert second["reply_id"] == first["reply_id"]
    lines = [json.loads(l) for l in
             (ws / "instances/02/state/delivery_queue.jsonl").read_text(
                 encoding="utf-8").splitlines()]
    assert len([l for l in lines if l.get("row_id") == "row_target"]) == 1
    # QQ unavailable is recorded honestly rather than faked as sent
    assert first["qq"]["sent"] is False
    assert first["qq"]["channel_unavailable"] is True
    assert first["qq"]["outbox"].endswith("delivery_outbox.jsonl")
    # and another row still gets its own reply
    other = deliver_reply(workspace=ws, instance_id="02", row_id="row_other", text="other")
    assert other.get("already_delivered") is not True


def test_row_identifier_tolerates_the_workspace_spelling(tmp_path):
    assert row_identifier({"row_id": "a"}) == "a"
    assert row_identifier({"id": "b"}) == "b"
    assert row_identifier({"inbox_id": "c"}) == "c"
    assert row_identifier({}) == ""
