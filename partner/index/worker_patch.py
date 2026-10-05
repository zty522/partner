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
        scope = getattr(self, "root_job_id", "")
        if scope:
            # Bounded diagnostic mode: filter in SQL by the root Job and its own
            # flow, so the backlog is never read and never claimed.
            rows = repo.list_by_status(statuses, limit=20, job_id=scope)
            if not rows:
                flow_id = str((repo.get_record(scope) or {}).get("flow_id") or "")
                rows = (repo.list_by_status(statuses, limit=20, flow_id=flow_id)
                        if flow_id else [])
        else:
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
            existing = repo.get_record(job.job_id)
            if existing and existing.get('lease_owner') and float(existing.get('lease_expiry') or 0) > time.time():
                raise RuntimeError('checkpoint requires an owned lease')
            repo.upsert_from_record(job.to_dict(), actor=f'EventWorker.{self.instance_id}.unclaimed_setup',
                projection_path=self.jobs_dir / f'{job.job_id}.json')
            for item in repo.outbox_pending():
                if item['job_id'] == job.job_id:
                    repo.outbox_emit_legacy_json(item['seq'])
            return
        repo.upsert_from_record(job.to_dict(), actor=f'EventWorker.{self.instance_id}',
            owner=owner, fencing_token=self._db_lease_token,
            projection_path=self.jobs_dir / f'{job.job_id}.json')
        # Ordered durable projection. Failed exports remain pending for retry.
        for item in repo.outbox_pending():
            if item['job_id'] == job.job_id:
                repo.outbox_emit_legacy_json(item['seq'])

    def _try_acquire_lock(self, job_id):
        if not hasattr(self, 'root'):
            from partner.runtime.background_actions import identity
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            path = self.lock_dir / f'{job_id}.lock'
            try:
                data = json.loads(path.read_text()) if path.exists() else {}
                if not identity(int(data.get('pid') or 0)):
                    path.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError):
                path.unlink(missing_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, 'w') as handle:
                    json.dump({'pid': os.getpid(), 'ts': time.time(),
                               'process_start': identity(os.getpid())}, handle)
                return True
            except FileExistsError:
                return False
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
        if not hasattr(self, 'root'):
            if getattr(self, '_lock_held', None):
                (self.lock_dir / f'{self._lock_held}.lock').unlink(missing_ok=True)
            self._lock_held = None
            return
        if getattr(self, '_lease_lost', False):
            return  # Leave ownership for explicit recovery; cancelled await may still run.
        if getattr(self, '_db_lease_job_id', None):
            repo = init_jobs(self.root)
            job_id = self._db_lease_job_id
            # A supervisor scale-down asks the worker to stop at the next
            # persisted Event boundary.  The Job is still ``running`` at that
            # point.  Merely dropping the lease leaves it absent from
            # ``ready_jobs`` and it can remain stranded forever.  Convert a
            # boundary-safe running Job back to queued before releasing it.
            current = repo.get_record(job_id) or {}
            ready = current.get('ready_event_ids') or []
            if (current.get('status') == 'running'
                    and not current.get('cancel_requested')
                    and not current.get('current_event_id') and ready):
                repo.schedule(job_id, next_run_at=time.time())
            else:
                repo.release_owned(job_id,
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
