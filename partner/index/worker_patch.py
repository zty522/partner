"""Patch EventWorker to use JobRepository as authoritative queue.

Replaces:
  - _queue_jobs (legacy glob over JSON files) -> JobRepository.list_by_status
  - _try_acquire_lock (fcntl on 9p JSON lock file) -> JobRepository.claim_job(job_id)
  - _save_job -> also mirrors into JobRepository (DB is authoritative)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from partner.application.service import JobRecord


def _row_to_record(row):
    out = dict(row)
    for json_col in ("intent_contract_json", "ready_event_ids_json",
                     "completed_event_ids_json", "suspended_flows_json",
                     "attachments_json", "metadata_json"):
        v = out.pop(json_col, None)
        if v:
            try:
                out[json_col.removesuffix("_json")] = json.loads(v)
            except Exception:
                out[json_col.removesuffix("_json")] = None
        else:
            out[json_col.removesuffix("_json")] = None
    from datetime import datetime, timezone
    for col in ("created_at", "updated_at", "started_at", "finished_at"):
        v = out.get(col)
        if isinstance(v, (int, float)) and v > 0:
            out[col] = datetime.fromtimestamp(float(v), tz=timezone.utc).isoformat()
    out.pop("schema_version", None)
    return out


def _build_jobrecord(row):
    fields = {f.name for f in JobRecord.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in row.items() if k in fields}
    return JobRecord(**kwargs)


def _worker_owner(instance_id):
    from partner.runtime.background_actions import identity
    return f"shared-{instance_id}-{identity(os.getpid())}-{uuid.uuid4().hex[:6]}"


def install_job_repository_path_on_event_worker():
    from partner.runtime import event_worker as ew_module
    return _install(ew_module)


def _install(worker_module):
    try:
        from partner.index.job_repository import init as init_jobs
    except Exception:
        return False

    def _queue_jobs(self):
        try:
            repo = init_jobs(self.root)
        except Exception:
            return
        statuses = ("queued", "dispatched", "running")
        rows = repo.list_by_status(statuses, limit=200)
        for row in rows:
            # Sprint 37: never hand a cancel_requested row back to the worker.
            if row.get("cancel_requested"):
                continue
            full = repo.get_record(row["job_id"]) or row
            try:
                job = _build_jobrecord(full)
            except Exception:
                continue
            path = self.jobs_dir / f"{row['job_id']}.json"
            yield job, path

    def _save_job(self, job):
        from datetime import datetime, timezone
        if getattr(self, '_lease_lost', False):
            raise RuntimeError('lease lost: refusing checkpoint')
        job.updated_at = datetime.now(timezone.utc).isoformat()
        repo = init_jobs(self.root)
        owner = getattr(self, '_db_lease_owner', None)
        if not owner:
            raise RuntimeError('checkpoint requires an owned lease')
        repo.upsert_from_record(job.to_dict(), actor=f'EventWorker.{self.instance_id}',
            owner=owner, fencing_token=self._db_lease_token,
            projection_path=self.jobs_dir / f'{job.job_id}.json')
        # Ordered durable projection. Failed exports remain pending for retry.
        for item in repo.outbox_pending():
            if item['job_id'] == job.job_id:
                repo.outbox_emit_legacy_json(item['seq'])

    def _try_acquire_lock(self, job_id):
        try:
            repo = init_jobs(self.root)
        except Exception:
            return False
        owner = _worker_owner(self.instance_id)
        claimed = repo.claim_job(job_id, owner=owner, instance_id=owner)
        if claimed is None:
            return False
        self._lease_lost = False
        self._db_lease_token = int(claimed.get("fencing_token") or 0)
        self._db_lease_owner = owner
        self._db_lease_job_id = job_id
        return True

    def _release_claim(self):
        if getattr(self, '_lease_lost', False):
            return  # Leave ownership for explicit recovery; cancelled await may still run.
        if getattr(self, '_db_lease_job_id', None):
            init_jobs(self.root).release_owned(self._db_lease_job_id,
                owner=self._db_lease_owner, fencing_token=self._db_lease_token)
        self._lock_held = None
        self._db_lease_token = None
        self._db_lease_owner = None
        self._db_lease_job_id = None

    def _renew_lease(self):
        job_id = getattr(self, "_db_lease_job_id", None)
        token = getattr(self, "_db_lease_token", None)
        owner = getattr(self, "_db_lease_owner", None)
        if not job_id or not token or not owner:
            return False
        try:
            from partner.index.job_repository import init as _init
            return _init(self.root).renew_lease(job_id, owner=owner,
                                                fencing_token=token,
                                                extend_seconds=300.0)
        except Exception:
            return False

    worker_module.EventWorker._queue_jobs = _queue_jobs
    worker_module.EventWorker._save_job = _save_job
    worker_module.EventWorker._try_acquire_lock = _try_acquire_lock
    worker_module.EventWorker._release_claim = _release_claim
    worker_module.EventWorker._renew_lease = _renew_lease
    return True
