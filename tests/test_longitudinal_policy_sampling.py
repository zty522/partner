import importlib.util
import json
import os
from pathlib import Path


_PATH = Path(__file__).resolve().parents[1] / "scripts/run_longitudinal_policy_sampling.py"
_SPEC = importlib.util.spec_from_file_location("run_longitudinal_policy_sampling", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)


def test_daily_sampler_is_two_slot_date_idempotent_and_does_not_write_rewards(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    sources = []
    for name in ("source.py", "paper.pdf"):
        path = tmp_path / name
        path.write_text("real evidence " * 30, encoding="utf-8")
        sources.append(str(path))
    monkeypatch.setattr(_MODULE, "DEFAULT_SOURCES", sources)
    for iid in ("04", "05"):
        state = root / "instances" / iid / "state"
        (state / "tasks").mkdir(parents=True)
        (state / "instance_runtime.lock").write_text(
            json.dumps({"pid": os.getpid()}), encoding="utf-8")
    first = _MODULE.dispatch_daily(
        root, candidate_id="candidate_x", research_project_id="research_x",
        pairs_per_project=2)
    assert first["ok"] is True
    assert len(first["window"]["messages"]) == 8
    assert {row["instance_id"] for row in first["window"]["messages"]} == {"04", "05"}
    assert {row["policy_arm"] for row in first["window"]["messages"]} == {"baseline", "candidate"}
    second = _MODULE.dispatch_daily(
        root, candidate_id="candidate_x", research_project_id="research_x",
        pairs_per_project=2)
    assert second["status"] == "date_window_already_dispatched"
    assert not (root / "share/mind/governance/experience_guided_policy/trajectories.jsonl").exists()
    assert not (root / "share/mind/governance/experience_guided_policy/control_policy.json").exists()


def test_sampler_refuses_dead_or_backlogged_slot(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    sources = []
    for name in ("source.py", "paper.pdf"):
        path = tmp_path / name
        path.write_text("evidence", encoding="utf-8")
        sources.append(str(path))
    monkeypatch.setattr(_MODULE, "DEFAULT_SOURCES", sources)
    for iid in ("04", "05"):
        (root / "instances" / iid / "state/tasks").mkdir(parents=True)
    result = _MODULE.dispatch_daily(
        root, candidate_id="candidate_x", research_project_id="research_x")
    assert result["ok"] is False
    assert result["status"] == "slots_not_ready"


def test_supervisor_reaudits_but_cannot_activate_before_full_gates(tmp_path):
    root = tmp_path / "workspace"
    # A missing/invalid external experiment makes the general gate fail.  The
    # supervisor may write a blocked attestation, but no production mapping.
    result = _MODULE.review_and_maybe_activate(
        root, candidate_id="candidate_x",
        llm_experiments=[str(tmp_path / "missing_experiment.json")],
        auto_activate_authorized=True)
    assert result["production_ready"] is False
    assert result["activated"] is False
    control = root / "share/mind/governance/experience_guided_policy/control_policy.json"
    assert not control.exists()


def test_matched_experiment_messages_are_preserved_as_serial_tasks():
    from partner.mind.executor import _must_preserve_as_serial_task

    text = "[experiment_id=e1] [match_key=m1] [policy_arm=baseline] query:x"
    assert _must_preserve_as_serial_task(text, "local_canary") is True
    assert _must_preserve_as_serial_task("ordinary correction", "desktop_gui") is False
    assert _must_preserve_as_serial_task("anything", "longitudinal_policy_sampler") is True
