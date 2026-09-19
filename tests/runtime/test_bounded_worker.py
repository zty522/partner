"""The bounded runtime filter must never let a diagnostic run touch the backlog.

These tests use the real JobRepository against a real SQLite file: the scoping has
to happen in SQL, not by reading everything and filtering in Python.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from partner.index.job_repository import JobRepository  # noqa: E402


def _repo(tmp_path) -> JobRepository:
    repo = JobRepository(tmp_path / "jobs.db")
    repo._ensure_schema()
    return repo


def _add(repo, job_id, *, status="queued", flow_id="flow_root", instance="02"):
    repo.upsert_from_record({"job_id": job_id, "project_id": "p", "status": status,
                             "flow_id": flow_id, "assigned_instance": instance,
                             "priority": 100, "next_run_at": 0})


def test_unscoped_list_by_status_is_unchanged(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "job_a")
    _add(repo, "job_b", flow_id="flow_other")
    rows = repo.list_by_status(("queued", "dispatched", "running"), limit=50)
    assert {r["job_id"] for r in rows} == {"job_a", "job_b"}


def test_scoping_by_job_id_happens_in_sql(tmp_path):
    repo = _repo(tmp_path)
    for i in range(25):
        _add(repo, f"job_backlog_{i:02d}", flow_id=f"flow_backlog_{i:02d}")
    _add(repo, "job_root", flow_id="flow_root")
    rows = repo.list_by_status(("queued",), limit=20, job_id="job_root")
    assert [r["job_id"] for r in rows] == ["job_root"], "backlog must not leak in"
    # the filter is in the WHERE clause, so even a tiny limit cannot reach them
    assert "job_backlog" not in json.dumps(rows)


def test_scoping_by_flow_id_only_returns_that_flow(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "job_root", flow_id="flow_root")
    _add(repo, "job_child", flow_id="flow_root")
    _add(repo, "job_foreign", flow_id="flow_backlog")
    rows = repo.list_by_status(("queued",), limit=20, flow_id="flow_root")
    assert sorted(r["job_id"] for r in rows) == ["job_child", "job_root"]


def test_scoping_by_root_event_id(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "job_root")
    repo.upsert_from_record({"job_id": "job_dep", "project_id": "p", "status": "queued",
                             "flow_id": "flow_root", "root_event_id": "evt_root",
                             "assigned_instance": "02", "priority": 100, "next_run_at": 0})
    repo.upsert_from_record({"job_id": "job_other", "project_id": "p", "status": "queued",
                             "flow_id": "flow_other", "root_event_id": "evt_other",
                             "assigned_instance": "02", "priority": 100, "next_run_at": 0})
    rows = repo.list_by_status(("queued",), limit=20, root_event_id="evt_root")
    assert [r["job_id"] for r in rows] == ["job_dep"]


def test_a_terminal_root_job_is_not_handed_back(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "job_root", status="completed")
    rows = repo.list_by_status(("queued", "dispatched", "running"), limit=20,
                               job_id="job_root")
    assert rows == []


def test_the_worker_scope_refuses_a_foreign_job_and_allows_its_own_flow(tmp_path,
                                                                      monkeypatch):
    """EventWorker._next_scoped_job: root job yes, its flow yes, backlog never."""
    from partner.runtime import event_worker as ew_module

    repo = _repo(tmp_path)
    _add(repo, "job_root", flow_id="flow_root")
    _add(repo, "job_child", flow_id="flow_root")
    _add(repo, "job_backlog", flow_id="flow_zzz")

    worker = object.__new__(ew_module.EventWorker)
    worker.root = tmp_path
    worker.root_job_id = "job_root"
    worker._lock_held = None

    class Job:
        def __init__(self, row):
            self.job_id = row["job_id"]
            self.flow_id = row["flow_id"]
            self.status = row["status"]
            self.created_at = row["created_at"] if "created_at" in row else "2026-01-01"

    allowed = []
    for row in repo.list_by_status(("queued",), limit=20):
        # the real worker_patch re-reads the full record; do the same here
        allowed.append(Job(repo.get_record(row["job_id"])))
    monkeypatch.setattr(worker, "_queue_jobs",
                        lambda: [(job, Path("x")) for job in allowed
                                 if job.status in {"queued", "dispatched", "running"}])
    monkeypatch.setattr(worker, "_try_acquire_lock", lambda job_id: True)
    monkeypatch.setattr(ew_module.EventWorker, "_scoped_repo", None, raising=False)
    picked = []
    while True:
        job = worker._next_scoped_job()
        if job is None:
            break
        picked.append(job.job_id)
        allowed = [j for j in allowed if j.job_id != job.job_id]
    assert "job_backlog" not in picked, "a foreign flow must never be picked up"
    assert set(picked) <= {"job_root", "job_child"}


def test_next_job_routes_to_the_scoped_path_when_a_root_job_is_named(tmp_path,
                                                                     monkeypatch):
    from partner.runtime import event_worker as ew_module
    worker = object.__new__(ew_module.EventWorker)
    worker.root_job_id = "job_root"
    calls = []
    monkeypatch.setattr(worker, "_next_scoped_job",
                        lambda: (calls.append("scoped"), "SENTINEL")[1])
    monkeypatch.setattr(worker, "_queue_jobs",
                        lambda: pytest.fail("the global queue must not be consulted"))
    assert worker.next_job() == "SENTINEL"
    assert calls == ["scoped"]


def test_an_unscoped_worker_still_uses_the_normal_path(tmp_path, monkeypatch):
    from partner.runtime import event_worker as ew_module
    worker = object.__new__(ew_module.EventWorker)
    worker.root_job_id = ""
    monkeypatch.setattr(worker, "_next_scoped_job",
                        lambda: pytest.fail("scoped path requires a root job"))
    # the normal path returns None for a worker with no matching instance job
    worker.instance_id = "02"
    worker.shared_mode = False
    worker._lock_held = None
    monkeypatch.setattr(worker, "_queue_jobs", lambda: [])
    object.__setattr__(worker, "root", tmp_path)
    assert worker.next_job() is None
