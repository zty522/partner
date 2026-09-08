import importlib.util
import json
from pathlib import Path


_PATH = Path(__file__).resolve().parents[1] / "scripts/run_serial_experiment_queue.py"
_SPEC = importlib.util.spec_from_file_location("run_serial_experiment_queue", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)


def _task(root, iid, task_id, message, status, created):
    path = root / "instances" / iid / "state/tasks" / task_id / "task_instance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "task_id": task_id, "user_message": message, "created_at": created,
        "working_dir": str(path.parent), "completion_status": status,
        "status": status, "metadata": {},
    }), encoding="utf-8")
    return path


def test_collect_closes_orphans_deduplicates_and_skips_already_done(tmp_path):
    exp = "exp_serial"
    baseline = f"[experiment_id={exp}] [match_key=m1] [policy_arm=baseline] q"
    candidate = f"[experiment_id={exp}] [match_key=m1] [policy_arm=candidate] q"
    old = _task(tmp_path, "04", "old", baseline, "pending", "2026-01-01T00:00:00")
    _task(tmp_path, "04", "duplicate", baseline, "pending", "2026-01-01T00:00:01")
    _task(tmp_path, "04", "candidate_done", candidate, "done", "2026-01-01T00:00:02")
    messages = _MODULE.collect(tmp_path, "04", exp)
    assert messages == [baseline]
    value = json.loads(old.read_text(encoding="utf-8"))
    assert value["completion_status"] == "failed"
    assert value["metadata"]["serial_queue_reconciliation"]["status"] == "superseded_for_serial_redrive"


def test_collect_does_not_touch_another_experiment(tmp_path):
    path = _task(tmp_path, "05", "other", "[experiment_id=other] x", "pending",
                 "2026-01-01T00:00:00")
    assert _MODULE.collect(tmp_path, "05", "wanted") == []
    assert json.loads(path.read_text(encoding="utf-8"))["completion_status"] == "pending"
