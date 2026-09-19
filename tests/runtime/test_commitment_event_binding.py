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
    # the recall node must run *before* the bet is frozen, so the record node depends on
    # it: an unordered sibling could leave the prior unwritten when the bet freezes
    assert commitment.depends_on == ("experience_prior",)
    prior_node = next(n for n in current.nodes if n.node_id == "experience_prior")
    assert prior_node.event_type == "commitment.prior_recall"
    assert prior_node.depends_on == ("understand_3",)
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


# ---------------------------------------------------------------------------
# the execution half: COMMITTED -> SETTLED inside the flow
# ---------------------------------------------------------------------------

def _bounded_context(tmp_path, job_id, token):
    workspace = _workspace_with_job(tmp_path, job_id,
                                    f"please record and settle\n\ntrace token: {token}")
    return SimpleNamespace(workspace=str(workspace), job_id=job_id, instance_id="02",
                           project_id="molecular_generation")


def test_both_message_flows_reach_the_commitment_nodes():
    """The router picks direct_answer or project_iteration by intent, so the kernel
    has to be reachable from both, and older topologies must stay resolvable."""
    registry = build_flow_registry()
    for name, version in (("direct_answer", "1.2.0"), ("project_iteration", "2.6.0")):
        flow = registry.get(name)
        assert flow.version == version, (name, flow.version)
        node_ids = [n.node_id for n in flow.nodes]
        assert "commitment" in node_ids and "commitment_execute" in node_ids, name
        execute = next(n for n in flow.nodes if n.node_id == "commitment_execute")
        assert execute.event_type == "commitment.bet_execute"
        assert execute.depends_on == ("commitment",)
    # the previously shipped topologies keep resolving
    assert len(registry.get("direct_answer", version="1.0.0").nodes) == 9
    # 1.1.0 recorded the bet but had no execution half
    v11 = [n.node_id for n in registry.get("direct_answer", version="1.1.0").nodes]
    assert "commitment" in v11 and "commitment_execute" not in v11
    assert "commitment" not in [n.node_id for n in
                                registry.get("project_iteration", version="2.5.0").nodes]


def test_the_handlers_take_one_bet_from_recorded_to_settled(tmp_path):
    from partner.events.commitment import commitment_bet_execute, commitment_bet_record

    job_id = "job_settle_test"
    token = "runtime_trace_commit_02_settle"
    ctx = _bounded_context(tmp_path, job_id, token)

    recorded = commitment_bet_record(ctx, {})
    assert recorded["ok"] is True and recorded["status"] == "COMMITTED"
    store = Path(str(ctx.workspace)) / "state" / "commitments" / f"event_flow_{job_id}" / \
        f"bet_{job_id}"
    assert (store / "bet.json").is_file()

    settled = commitment_bet_execute(ctx, {})
    out = settled["semantic_output"]
    assert settled["ok"] is True, settled
    assert out["state"] == "CLOSED"
    assert out["settlement_class"] in ("supported", "falsified", "inconclusive")
    assert out["experience_id"], "a valid settlement must mint an experience"
    assert out["receipt_id"] and out["measurement_id"] and out["baseline_id"]
    assert out["replayed"] is False
    lifecycle = json.loads((store / "state.json").read_text(encoding="utf-8"))
    assert lifecycle["settled"] is True and lifecycle["experience_emitted"] is True
    # exactly one settlement and one experience, however often the flow re-enters
    assert len(list((store / "settlement").glob("*.json"))) == 1
    assert len(list((store / "experience").glob("*.json"))) == 1
    assert out["trace_token"] == token
    # the record-only environment keeps the result unpublishable
    assert out["publish_eligible"] is False
    assert any("isolated_sample" in blocker for blocker in out["publish_blockers"])
    # both halves are on the timeline, and neither faked a status change
    history = init_jobs(Path(str(ctx.workspace))).history(job_id)
    kinds = [row["kind"] for row in history]
    assert "commitment_bet_recorded" in kinds and "commitment_bet_settled" in kinds
    for row in history:
        if row["kind"].startswith("commitment_bet_"):
            assert row["from_status"] == row["to_status"] == "running"


def test_a_replay_settles_nothing_new(tmp_path):
    from partner.events.commitment import commitment_bet_execute, commitment_bet_record

    ctx = _bounded_context(tmp_path, "job_replay", "runtime_trace_commit_02_replay")
    commitment_bet_record(ctx, {})
    first = commitment_bet_execute(ctx, {})
    second = commitment_bet_execute(ctx, {})
    assert second["semantic_output"]["replayed"] is True
    assert second["semantic_output"]["settlement_id"] == \
        first["semantic_output"]["settlement_id"]
    store = Path(str(ctx.workspace)) / "state" / "commitments" / "event_flow_job_replay" / \
        "bet_job_replay"
    assert len(list((store / "settlement").glob("*.json"))) == 1


def test_running_a_frozen_bet_continues_it_instead_of_re_freezing(tmp_path):
    """freeze_only() then run() must be a continuation, not a frozen-field edit."""
    from partner.application.commitment_bounded_adapter import (
        bounded_spec, build_bounded_runner, write_bounded_snapshot,
    )
    from partner.commitment.store import CommitmentStore

    job_id, token = "job_freeze_then_run", "runtime_trace_commit_02_two_step"
    message = "please record and settle\n\ntrace token: " + token
    spec = bounded_spec(job_id=job_id, trace_token=token, message=message)
    store = CommitmentStore(Path(str(tmp_path)), spec["run_id"], spec["bet_id"])
    snapshot = write_bounded_snapshot(store=store, spec=spec, job_id=job_id,
                                      trace_token=token, message=message)
    frozen = build_bounded_runner(tmp_path, spec, snapshot_path=snapshot).freeze_only()
    assert frozen.state == "COMMITTED"
    frozen_hash = store.load_bet().freeze_hash()

    from partner.commitment.ports import FrozenClock
    runner = build_bounded_runner(tmp_path, spec, snapshot_path=snapshot)
    result = runner.run()
    assert result.state in ("CLOSED",) or result.state in (
        "INVALID", "BLOCKED", "BUDGET_EXHAUSTED", "CANCELLED"), result.state
    assert result.state == "CLOSED", f"{result.state}: {result.reason}"
    # the freeze was replayed, never edited
    assert store.load_bet().freeze_hash() == frozen_hash
    assert store.load_bet().revision == 1
    assert result.settlement is not None and result.experience is not None


def test_the_bounded_action_is_deterministic_and_comparable(tmp_path):
    from partner.application.commitment_bounded_adapter import (
        CONTROL_TRANSFORM, CANDIDATE_TRANSFORM, text_statistics,
    )

    text = "Alpha beta ALPHA Gamma gamma delta"
    control = text_statistics(text, transform=CONTROL_TRANSFORM)
    candidate = text_statistics(text, transform=CANDIDATE_TRANSFORM)
    assert control == text_statistics(text, transform=CONTROL_TRANSFORM)
    assert candidate == text_statistics(text, transform=CANDIDATE_TRANSFORM)
    # case folding can only reduce the number of distinct tokens
    assert candidate["unique_words"] <= control["unique_words"]
    assert candidate["text_len"] == control["text_len"]
    assert candidate["text_sha256"] != control["text_sha256"]


# ---------------------------------------------------------------------------
# the operator's token shape must not be dictated by the implementation
# ---------------------------------------------------------------------------

def test_any_trace_token_shape_is_recognised():
    from partner.events.commitment import _TRACE_RE

    for token in ("runtime_trace_commit_02_1789813427", "log_trace_02_1789814040",
                  "trace_abc123", "canary_trace_x-1"):
        match = _TRACE_RE.search(f"please act on\n\ntrace token: {token}")
        assert match is not None and match.group(0) == token, token
    # prose that merely contains the word "trace" is not a token
    for prose in ("no token here", "distributional trace", "trace",
                  "let me trace the call"):
        assert _TRACE_RE.search(prose) is None, prose


def test_the_evolution_pipeline_imports_the_flow_registry_from_its_own_module():
    """Regression: importing build_flow_registry from partner.event_fabric raises
    ImportError inside the node and fails the flow with event_flow_failed."""
    import partner.events.evolution_pipeline  # noqa: F401 -- import is the assertion
    from partner.event_flows.registry import build_flow_registry

    assert build_flow_registry().get("self_evolution") is not None
