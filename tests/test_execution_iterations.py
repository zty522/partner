import json
from pathlib import Path
from types import SimpleNamespace

from partner.v2 import execution_iteration_events as events


def _ctx(root: Path, instance: str):
    workspace = root / "instances" / instance
    working = workspace / "state" / "tasks" / "task"
    working.mkdir(parents=True)
    return SimpleNamespace(
        workspace=str(workspace), working_dir=str(working),
        task_instance=SimpleNamespace(working_dir=str(working)),
    ), working


def test_content_execution_slice_writes_and_runs_code(tmp_path, monkeypatch):
    ctx, working = _ctx(tmp_path, "01")
    source = tmp_path / "external" / "content" / "inbox.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps({"id": "one", "status": "open", "project": "bio", "intent": "reference", "urls": ["u"]}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(events, "_pdf", lambda *_args: (str(working / "report.pdf"), {"quality": {}}))
    (working / "report.pdf").write_bytes(b"pdf")
    result = events.atomic_evidence_execution_slice(ctx, {"wave": 3})
    assert result["ok"] is True
    assert (working / "execution_wave_3.py").is_file()
    payload = json.loads((working / "execution_wave_3_result.json").read_text(encoding="utf-8"))
    assert payload["records"] == 1
    assert payload["actionable_briefs"][0]["publish_authorized"] is False
    assert payload["deduplicated_open_backlog"][0]["risk_flags"] == ["requires_fact_check", "publish_not_authorized"]


def test_affinity_execution_slice_uses_real_pickle_values(tmp_path, monkeypatch):
    import pickle

    ctx, working = _ctx(tmp_path, "02")
    source = tmp_path / "external" / "targetdiff" / "data" / "affinity_info.pkl"
    source.parent.mkdir(parents=True)
    pickle.dump({
        "a": {"rmsd": 1.0, "pk": 5.0, "vina": -8.0},
        "b": {"rmsd": 2.0, "pk": 6.0, "vina": -7.0},
    }, source.open("wb"))
    monkeypatch.setattr(events, "_pdf", lambda *_args: (str(working / "report.pdf"), {"quality": {}}))
    (working / "report.pdf").write_bytes(b"pdf")
    result = events.atomic_evidence_execution_slice(ctx, {"wave": 3})
    assert result["ok"] is True
    payload = json.loads((working / "execution_wave_3_result.json").read_text(encoding="utf-8"))
    assert payload["records"] == 2
    assert payload["measured_pk_records"] == 2
    assert payload["vina_to_pk_baseline"]["causal_claim"] is False
    assert payload["exit_code"] == 0
