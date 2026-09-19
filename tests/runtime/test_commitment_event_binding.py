"""The 02 Event flow must reach the commitment kernel, and only record a bet.

The first attempt at this failed with
``checkpoint_crashed: ValueError: unknown Event series: commitment`` -- a known
precedent (partner/observe/precedents.py case_05_event_series_unknown): adding an
EventDefinition whose series is not in EVENT_SERIES crashes flow startup.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from partner.event_fabric.models import EVENT_SERIES  # noqa: E402
from partner.event_flows.registry import build_flow_registry  # noqa: E402
from partner.events import builtin_definitions  # noqa: E402
from partner.index.job_repository import init as init_jobs  # noqa: E402


def test_the_commitment_series_is_registered():
    """Without this, EventLedger.create rejects the series and the flow crashes."""
    assert "commitment" in EVENT_SERIES


def test_the_commitment_record_event_is_in_the_catalog():
    names = {d.name for d in builtin_definitions()}
    assert "commitment.bet_record" in names


def test_direct_answer_runs_the_commitment_node_and_keeps_the_old_topology():
    registry = build_flow_registry()
    current = registry.get("direct_answer")
    node_ids = [node.node_id for node in current.nodes]
    assert "commitment" in node_ids
    commitment = next(n for n in current.nodes if n.node_id == "commitment")
    assert commitment.event_type == "commitment.bet_record"
    assert commitment.depends_on == ("understand_3",)
    # the previous topology stays resolvable for requests pinned to 1.0.0
    legacy = registry.get("direct_answer", version="1.0.0")
    assert len(legacy.nodes) == 9
    assert "commitment" not in [n.node_id for n in legacy.nodes]


def _workspace_with_job(tmp_path, job_id, request):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    repo = init_jobs(workspace)
    repo.upsert_from_record({"job_id": job_id, "project_id": "p", "status": "running",
                             "flow_id": "flow_x", "assigned_instance": "02",
                             "request": request, "priority": 100, "next_run_at": 0})
    return workspace


def test_the_handler_records_a_bet_carrying_the_trace_token(tmp_path):
    from partner.events.commitment import commitment_bet_record

    job_id = "job_test_record"
    token = "runtime_trace_commit_02_test"
    workspace = _workspace_with_job(tmp_path, job_id, f"please record\n\ntrace token: {token}")
    ctx = SimpleNamespace(workspace=str(workspace), job_id=job_id, instance_id="02",
                          project_id="molecular_generation")
    result = commitment_bet_record(ctx, {})
    assert result["ok"] is True, result
    store = workspace / "state" / "commitments" / f"event_flow_{job_id}" / f"bet_{job_id}"
    bet = json.loads((store / "bet.json").read_text(encoding="utf-8"))
    assert bet["bet_id"] == f"bet_{job_id}"
    assert token in json.dumps(bet, ensure_ascii=False), "the BetRecord must carry the token"
    assert bet["environment"] == "isolated_sample", "a record-only bet is never publishable"
    events = [json.loads(line) for line in
              (store / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    recorded = [e for e in events if e.get("kind") == "bet_recorded"]
    assert recorded and recorded[0]["payload"]["trace_token"] == token
    # and the bet is visible on the Job timeline without faking a status change
    history = init_jobs(workspace).history(job_id)
    notes = [row for row in history if row.get("kind") == "commitment_bet_recorded"]
    assert len(notes) == 1
    assert notes[0]["detail"]["trace_token"] == token
    assert notes[0]["from_status"] == notes[0]["to_status"] == "running"


def test_the_handler_refuses_a_message_without_a_trace_token(tmp_path):
    from partner.events.commitment import commitment_bet_record

    job_id = "job_test_no_token"
    workspace = _workspace_with_job(tmp_path, job_id, "no token here at all")
    ctx = SimpleNamespace(workspace=str(workspace), job_id=job_id, instance_id="02",
                          project_id="molecular_generation")
    result = commitment_bet_record(ctx, {})
    assert result["ok"] is False
    assert "no trace token" in result["summary"]
    assert not (workspace / "state" / "commitments").exists(), "nothing is written on refusal"


def test_the_record_only_bet_can_never_be_published(tmp_path):
    from partner.events.commitment import commitment_bet_record

    job_id = "job_test_env"
    token = "runtime_trace_commit_02_env"
    workspace = _workspace_with_job(tmp_path, job_id, f"trace token: {token}")
    ctx = SimpleNamespace(workspace=str(workspace), job_id=job_id, instance_id="02",
                          project_id="p")
    commitment_bet_record(ctx, {})
    bet = json.loads((workspace / "state" / "commitments" / f"event_flow_{job_id}" /
                      f"bet_{job_id}" / "bet.json").read_text(encoding="utf-8"))
    assert bet["environment"] in ("isolated_sample", "synthetic_fixture", "shadow")
