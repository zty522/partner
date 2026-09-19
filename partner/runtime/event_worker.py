"""Production Event worker (shared-mode pool).

Read discipline: queue discovery is authoritative via ``JobRepository``
(installed by ``partner.index.worker_patch`` — ``list_by_status`` /
``claim_job`` / ``upsert_from_record``).  The legacy ``os.scandir``
``_queue_jobs`` is a bounded single-directory enumeration kept only as a
fallback when the index DB is unavailable.  No full-tree walk.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from partner.adapters.adapter import create_adapter
from partner.application.models import JobRecord
from partner.event_fabric import EventFlowController, EventFlowStore, EventLedger, EventSummary, build_catalog
from partner.event_fabric.runner import EventFlowRunner
from partner.event_flows import build_flow_registry

logger = logging.getLogger("partner.runtime.event_worker")


def _root(path: str | os.PathLike) -> Path:
    value = Path(path).expanduser().resolve()
    return value.parent.parent if value.parent.name == "instances" else value


def _agent_config(root: Path, instance_workspace: Path) -> dict[str, Any]:
    for path in (instance_workspace / "partner_config.json", root / "config/partner_config.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value.get("agent"), dict):
                return dict(value["agent"])
        except (OSError, TypeError, ValueError):
            continue
    return {"backend": "hermes"}


@dataclass
class EventContext:
    workspace: str
    instance_workspace: str
    instance_id: str
    project_id: str
    job_id: str
    channel: str
    sender_id: str
    adapter: Any
    # ADR 0100: which QQ Bot originally accepted this Job.  Has a default
    # so the dataclass stays usable for legacy callers that don't set it.
    intake_instance_id: str = ""

    @property
    def task_id(self) -> str:
        return self.job_id

    @property
    def working_dir(self) -> str:
        value = Path(self.workspace) / "state/event_runtime/work" / self.job_id
        value.mkdir(parents=True, exist_ok=True)
        return str(value)

    @property
    def task_instance(self) -> Any:
        # Transitional low-level capabilities may read these neutral fields;
        # orchestration never enters TaskInstance/Harness.
        work = Path(self.workspace) / "state/event_runtime/work" / self.job_id
        work.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            id=self.job_id, task_id=self.job_id, content="",
            workspace=self.workspace, working_dir=str(work), metadata={},
            save=lambda: None, append_log=lambda *_args, **_kwargs: None,
        )


class EventWorker:
    """Execute one instance's queued Jobs, one durable Event at a time."""

    def __init__(self, workspace: str | os.PathLike, instance_id: str,
                 shared_mode: bool = False, root_job_id: str = ""):
        self.root = _root(workspace)
        self.instance_id = str(instance_id)
        self.shared_mode = bool(shared_mode)
        #: Bounded diagnostic mode: this worker may only ever take that one root Job
        #: (or a Job of the same Event Flow), so a single message can be consumed
        #: without draining the global queue.
        self.root_job_id = str(root_job_id or "")
        self.instance_workspace = self.root / "instances" / self.instance_id
        config = _agent_config(self.root, self.instance_workspace)
        self.adapter = create_adapter(
            str(config.get("backend") or "hermes"), str(self.instance_workspace),
            model=config.get("model"), provider=config.get("provider"),
        )
        self.catalog = build_catalog(workspace=self.root)
        self.flows = build_flow_registry()
        self.store = EventFlowStore(self.root)
        self.controller = EventFlowController(self.store)
        self.ledger = EventLedger(self.root)
        self.runner = EventFlowRunner(
            catalog=self.catalog, ledger=self.ledger, controller=self.controller)
        self.jobs_dir = self.root / "state/application/jobs"
        self.lock_dir = self.jobs_dir / "_locks"
        self._lock_held: str | None = None
        self._stopping = False
        if self.shared_mode:
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            self._cleanup_stale_locks()
        # Install index fast-paths (idempotent, safe to call per worker).
        try:
            from partner.index.ledger_patch import install_at_event_worker_init
            from partner.index.worker_patch import install_job_repository_path_on_event_worker
            install_at_event_worker_init()
            install_job_repository_path_on_event_worker()
        except Exception:
            pass  # fall back to legacy readers if index DB is not yet built

    def stop(self, *_args: Any) -> None:
        self._stopping = True

    def _load_job(self, path: Path) -> JobRecord | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            fields = JobRecord.__dataclass_fields__
            return JobRecord(**{key: value for key, value in raw.items() if key in fields})
        except (OSError, TypeError, ValueError):
            return None

    def _save_job(self, job: JobRecord) -> None:
        job.updated_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat()
        path = self.jobs_dir / f"{job.job_id}.json"
        # Unique tmp suffix (pid) so concurrent workers never collide on the
        # same temporary path — the previous shared ".tmp" name caused
        # FileNotFoundError races when two workers wrote the same job.
        temporary = path.with_suffix(f".tmp.{os.getpid()}")
        temporary.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _queue_jobs(self):
        """Discover new jobs each poll without rereading immutable history.

        Terminal records are rechecked within 30 seconds so externally resumed
        jobs remain discoverable. Active records are never cached. Directory
        enumeration also removes deleted entries; this cache is not queue truth.
        """
        cache = getattr(self, '_terminal_job_cache', None)
        if cache is None:
            cache = self._terminal_job_cache = {}
        now = time.monotonic()
        seen = set()
        try:
            with os.scandir(self.jobs_dir) as entries:
                paths = [Path(entry.path) for entry in entries
                         if entry.name.endswith('.json')]
        except FileNotFoundError:
            return
        for path in paths:
            seen.add(path.name)
            previous = cache.get(path.name)
            if previous and now < previous[0]:
                continue
            try:
                stat = path.stat()
                signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except OSError:
                cache.pop(path.name, None)
                continue
            if previous and previous[1] == signature:
                cache[path.name] = (now + 30, signature)
                continue
            job = self._load_job(path)
            if job and job.status in {'completed', 'failed', 'cancelled'}:
                cache[path.name] = (now + 30, signature)
                continue
            cache.pop(path.name, None)
            if job:
                yield job, path
        for name in cache.keys() - seen:
            del cache[name]

    def _try_acquire_lock(self, job_id: str) -> bool:
        from partner.runtime.background_actions import identity
        with (self.lock_dir / '.claim_gate').open('a') as gate:
            fcntl.flock(gate, fcntl.LOCK_EX)
            self._cleanup_stale_lock(job_id)
            path = self.lock_dir / f"{job_id}.lock"
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, 'w') as handle:
                    json.dump({'pid':os.getpid(), 'ts':time.time(), 'process_start':identity(os.getpid())}, handle)
                return True
            except FileExistsError:
                return False

    def _release_claim(self) -> None:
        if self._lock_held is not None:
            with (self.lock_dir / '.claim_gate').open('a') as gate:
                fcntl.flock(gate, fcntl.LOCK_EX)
                path = self.lock_dir / f"{self._lock_held}.lock"
                try:
                    if json.loads(path.read_text()).get('pid') == os.getpid(): path.unlink(missing_ok=True)
                except (OSError, ValueError): pass
            self._lock_held = None

    def _cleanup_stale_lock(self, job_id: str) -> None:
        lock_path = self.lock_dir / f"{job_id}.lock"
        try:
            with open(lock_path, encoding="utf-8") as f:
                data = json.load(f)
            pid = int(data.get("pid") or 0)
            ts = float(data.get("ts") or 0)
            from partner.runtime.background_actions import identity
            current_identity = identity(pid)
            if not current_identity or (data.get("process_start") and data["process_start"] != current_identity):
                lock_path.unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            # corrupt/empty lock — clear it so the job isn't stranded
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _cleanup_stale_locks(self) -> None:
        with (self.lock_dir / '.claim_gate').open('a') as gate:
            fcntl.flock(gate, fcntl.LOCK_EX)
            for path in self.lock_dir.glob('*.lock'):
                self._cleanup_stale_lock(path.stem)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError, ValueError):
            return False

    def _isolate_broken_job(self, job: JobRecord, reason: str) -> None:
        """Fail and record a queued Job whose flow state is unrecoverable.

        Mirrors the failed terminal into the authoritative JobRepository and
        the JSON projection so the queue is clean and the failure is durable
        evidence — never a silent retry loop."""
        try:
            job.status = "failed"
            job.error = reason
            from datetime import datetime, timezone
            job.updated_at = datetime.now(timezone.utc).isoformat()
            from partner.index.job_repository import init as _init_jobs
            repo = _init_jobs(self.root)
            repo.upsert_from_record(
                job.to_dict(), actor=f"EventWorker.{self.instance_id}",
                projection_path=self.jobs_dir / f"{job.job_id}.json",
            )
        except Exception as exc:
            logger.error("failed to isolate broken job %s: %s", job.job_id, exc)

    def _next_scoped_job(self) -> JobRecord | None:
        """Bounded mode: only the named root Job, or a Job of its own flow.

        ``_queue_jobs`` filters in SQL when ``root_job_id`` is set; the flow check
        below is defence in depth so that an unpatched directory-scan fallback still
        cannot hand back an unrelated Job from the backlog.
        """
        if not self.root_job_id:
            raise RuntimeError("_next_scoped_job requires root_job_id")
        allowed_flow = ""
        try:
            from partner.index.job_repository import init as _init_jobs
            record = _init_jobs(self.root).get_record(self.root_job_id) or {}
            allowed_flow = str(record.get("flow_id") or "")
        except Exception:
            allowed_flow = ""
        candidates = [job for job, _path in self._queue_jobs()
                      if job and job.status in {"queued", "dispatched", "running"}
                      and job.flow_id
                      and (job.job_id == self.root_job_id
                           or (allowed_flow and job.flow_id == allowed_flow))]
        candidates.sort(key=lambda value: value.created_at)
        for job in candidates:
            if self._try_acquire_lock(job.job_id):
                self._lock_held = job.job_id
                return job
        return None

    def next_job(self) -> JobRecord | None:
        if self.root_job_id:
            return self._next_scoped_job()
        # ADR 0100: shared workers pick ANY queued/dispatched/running job
        # from the global queue — they are not bound to one instance's
        # assigned_instance.  They also ignore the instance_scheduler gate
        # (that concept is being retired; workers are resource-scaled).
        if self.shared_mode:
            rows: list[tuple[JobRecord, Path]] = []
            for job, path in self._queue_jobs():
                if (job and job.status in {"queued", "dispatched", "running"}
                        and job.flow_id):
                    rows.append((job, path))
            rows.sort(key=lambda pair: pair[0].created_at)
            for job, _path in rows:
                try:
                    state = self.store.load(job.flow_id)
                except (FileNotFoundError, ValueError, OSError) as exc:
                    # A queued Job whose flow state file is missing/corrupt
                    # (stale queue from an earlier run, or a crash between
                    # flow save and job enqueue) must not take down the whole
                    # shared worker.  Isolate it as failed and continue.
                    logger.error(
                        "isolate job %s: flow %s missing/corrupt: %s",
                        job.job_id, job.flow_id, exc,
                    )
                    self._isolate_broken_job(job, f"flow_missing:{job.flow_id}")
                    continue
                control = self.root / "state/application/controls" / f"{job.job_id}.json"
                if state.next_check_at > time.time() and not control.exists():
                    continue
                if state.waiting_task_id and not control.exists():
                    from partner.runtime.background_actions import BackgroundActions, TERMINAL
                    manager = BackgroundActions(self.root)
                    task_path = manager.directory / state.waiting_task_id / 'task.json'
                    if task_path.exists() and manager.inspect(state.waiting_task_id)['status'] not in TERMINAL:
                        # No flow writes before acquiring its claim: another
                        # worker may already be collecting the terminal result.
                        continue
                # O_EXCL lock (atomic on 9p/NTFS where os.rename isn't) —
                # exactly one worker wins the claim; the rest skip it.
                if self._try_acquire_lock(job.job_id):
                    self._lock_held = job.job_id
                    return job
            return None
        scheduler = self.root / "state/instance_scheduler.json"
        if scheduler.is_file():
            try:
                active = set(json.loads(scheduler.read_text(encoding="utf-8")).get("active_slots") or [])
                if self.instance_id not in active:
                    return None
            except (OSError, TypeError, ValueError):
                return None
        rows: list[JobRecord] = []
        for job, path in self._queue_jobs():
            if (job and job.assigned_instance == self.instance_id
                    and job.status in {"queued", "dispatched", "running"} and job.flow_id):
                rows.append(job)
        rows.sort(key=lambda value: value.created_at)
        return rows[0] if rows else None

    def _emit_progress_message(self, *, job, flow_state, node_id, node_output):
        import sys; sys.stderr.write("[TRACE_EMIT] ENTER node=" + node_id + " flow_type=" + str(flow_state.flow_type) + chr(10)); sys.stderr.flush()
        """Run notification.emit_progress after a round node completes.

        Translates the just-completed node's output into a short Chinese
        progress message and writes it to the outbound queue. Failures
        are caught at the caller; this helper raises only on truly
        unexpected errors (e.g. invalid flow_type).
        """
        from partner.events.emit_progress import emit_progress
        # EventContext is defined in this module (line 44); do not import it
        # from partner.event_fabric (it is not exported there — that import
        # raised ImportError and silently swallowed the whole emit_progress call).
        ctx = EventContext(
            workspace=str(self.root),
            instance_workspace=str(self.root / "instances" / (
                job.assigned_instance or job.origin_instance or self.instance_id)),
            instance_id=job.assigned_instance or job.origin_instance or self.instance_id,
            project_id=job.project_id,
            job_id=job.job_id,
            channel=job.channel or "local",
            sender_id=job.sender_id or "",
            intake_instance_id=str(getattr(job, "intake_instance_id", "") or ""),
            adapter=self.adapter,
        )
        params = {
            "completed_node_id": node_id,
            "node_output": node_output,
            "flow_id": flow_state.flow_id,
            "task_id": flow_state.task_id or job.job_id,
            "instance_id": ctx.instance_id,
            "flow_type": flow_state.flow_type,
        }
        import sys as _sys; _result = emit_progress(ctx, params); _sys.stderr.write("[TRACE_EMIT] EXIT ok=" + str(_result.get("ok")) + " text=" + str(_result.get("progress_text",""))[:80] + chr(10)); _sys.stderr.flush()

    def _apply_control(self, job: JobRecord, state: Any) -> bool:
        path = self.root / "state/application/controls" / f"{job.job_id}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return False
        action = str(value.get("action") or "")
        if action not in {"pause", "cancel"}:
            return False
        if state.waiting_task_id:
            from partner.runtime.background_actions import BackgroundActions
            BackgroundActions(self.root).cancel(state.waiting_task_id)
        state.status = "paused" if action == "pause" else "cancelled"
        if action == "cancel":
            state.ready_node_ids = []
        self.store.save(state)
        job.status = state.status
        job.current_event_id = ""
        self._save_job(job)
        path.unlink(missing_ok=True)
        return True

    async def run_job_checkpoint(self, job: JobRecord) -> bool:
        state = self.store.load(job.flow_id)
        if (not state.current_event_id or state.waiting_task_id) and self._apply_control(job, state):
            return True
        if state.status == 'paused':
            return False
        from partner.runtime.request_budget import expired
        if (job.flow_type in {'project_iteration','new_project','browser_video_learning','xhs_authoring'}
                and expired(job.intent_contract) and not state.waiting_task_id):
            state.status = 'paused'
            self.store.save(state)
            job.status = 'paused'
            job.error = '本次观察时段结束，停止新增业务动作；目标是否达成以已有证据为准。'
            self._save_job(job)
            if job.report_policy != 'none':
                self._maybe_start_report(job)
            return True
        try:
            definition = self.flows.get(state.flow_type, version=state.definition_version)
        except KeyError:
            job.status = "failed"
            job.error = "pinned_flow_definition_unavailable_after_restart"
            self._save_job(job)
            return True
        if state.catalog_version != self.catalog.version:
            job.status = "failed"
            job.error = "pinned_catalog_unavailable_after_restart"
            self._save_job(job)
            return True
        if state.definition_version != definition.version:
            job.status = "failed"
            job.error = "pinned_flow_definition_unavailable_after_restart"
            self._save_job(job)
            return True
        # Frontend routing is provisional. The actual three-pass intent Event
        # selects answer versus tool work before either suffix can start.
        if (state.flow_type in {'direct_answer', 'project_iteration', 'new_project'}
                and set(state.completed_node_ids) == {'understand_1', 'understand_2', 'understand_3'}
                and not state.current_event_id and not state.definition_history):
            intent = state.node_outputs.get('understand_3', {}).get('semantic_output') or {}
            target_name = intent.get('route')
            if state.flow_type == 'new_project' and target_name == 'project_iteration':
                target_name = 'new_project'  # Materialize through its init Event first.
            if target_name in {'direct_answer', 'project_iteration'} and target_name != state.flow_type:
                definition = self.flows.get(target_name)
                self.controller.route_after_intent(state,
                    self.flows.get(state.flow_type, version=state.definition_version), definition)
        if job.flow_type != state.flow_type:
            job.flow_type = state.flow_type
            self._save_job(job)
        if state.status in {"completed", "failed", "cancelled"}:
            if job.suspended_flows:
                suspended = dict(job.suspended_flows.pop())
                parent = self.store.load(str(suspended["parent_flow_id"]))
                if suspended.get('kind') == 'cycle':
                    from partner.runtime.cycle_children import merge_child
                    merge_child(self, parent, state, suspended)
                if suspended.get('kind')=='domain_handoff':
                    from partner.runtime.domain_handoff import merge_video_result
                    merge_video_result(parent,state)
                try:
                    self.controller.resume(parent, child_flow_id=state.flow_id)
                except ValueError:
                    # The parent no longer references this child in its
                    # suspended list.  If the parent already reached a
                    # terminal state, the business work is done — reflect
                    # that instead of failing the whole job on a bookkeeping
                    # mismatch.  Otherwise the state is genuinely corrupt.
                    if parent.status in {"completed", "failed", "cancelled"}:
                        job.status = parent.status
                        job.flow_id = parent.flow_id
                        job.flow_type = parent.flow_type
                        job.ready_event_ids = []
                        job.completed_event_ids = list(parent.completed_node_ids)
                        if parent.status == "failed":
                            job.error = "event_flow_failed:" + ",".join(parent.failed_node_ids)
                        self._save_job(job)
                        return True
                    raise
                job.flow_id = parent.flow_id
                job.flow_type = parent.flow_type
                job.status = "running"
                job.ready_event_ids = list(parent.ready_node_ids)
                self._save_job(job)
                return True
            job.status = state.status
            job.current_event_id = ""
            job.ready_event_ids = []
            job.completed_event_ids = list(state.completed_node_ids)
            if state.status == "failed":
                job.error = "event_flow_failed:" + ",".join(state.failed_node_ids)
            self._save_job(job)
            return True
        # Crash recovery happens before normal scheduling.  A terminal Summary
        # proves the node finished and is projected back into Flow state.  An
        # interrupted non-idempotent Event is never replayed automatically,
        # because its external side effect may already have happened.
        if state.current_event_id and not state.waiting_task_id and 'execute' in state.ready_node_ids:
            import hashlib
            key = f"{job.job_id}:{state.flow_id}:execute"
            task_id = 'action_' + hashlib.sha256(key.encode()).hexdigest()[:24]
            receipt = self.root / 'state/event_runtime/background' / task_id / 'task.json'
            if receipt.exists():
                state.waiting_task_id = task_id
                self.store.save(state)
        if state.current_event_id and not state.waiting_task_id:
            event_id = state.current_event_id
            summary = next((row for row in self.ledger.recent_summaries(
                limit=10000, include_audit=True) if row.get("event_id") == event_id), None)
            event = self.ledger.get_event_history(event_id)
            node_id = str(event.get("node_id") or "")
            if summary and node_id in state.ready_node_ids:
                semantic = summary.get("semantic_output") or {}
                projected = {
                    "ok": summary.get("status") == "completed",
                    "status": summary.get("status"), "summary": summary.get("headline"),
                    "semantic_output": semantic,
                    "evidence_refs": summary.get("evidence_refs") or [],
                    "files": [x.get("path") for x in summary.get("artifacts") or [] if isinstance(x, dict)],
                }
                # 把 semantic_output 里的关键字段（queued/delivered/receipt/message）
                # 提升回顶层，保持和正常 output 一致——否则下游 verify 读不到 queued。
                if isinstance(semantic, dict):
                    for key, value in semantic.items():
                        projected.setdefault(key, value)
                state.node_outputs[node_id] = projected
                self.controller.mark_terminal(
                    state, definition, node_id, event_id=event_id, summary=summary)
                state.current_event_id = ""; self.store.save(state)
                return True
            if node_id in state.ready_node_ids:
                node = definition.node(node_id); spec = self.catalog.get(node.event_type)
                if spec is not None and not spec.idempotent:
                    blocked = EventSummary(
                        event_id=event_id, status="blocked",
                        headline="非幂等 Event 在进程中断后需要核验",
                        outcome="运行时没有重放可能已发生的外部副作用。",
                        failure_class="runtime", mechanism="recovery/non_idempotent_unknown",
                        requires_human=True, notification_kind="blocker",
                    )
                    self.ledger.complete(event_id, blocked)
                    self.controller.mark_terminal(
                        state, definition, node_id, event_id=event_id,
                        summary=blocked.to_dict())
                    state.current_event_id = ""; self.store.save(state)
                    return True
            if event and not summary:
                self.ledger.complete(event_id, EventSummary(
                    event_id=event_id, status="cancelled", headline="进程中断，幂等步骤将重新执行",
                    failure_class="runtime", mechanism="recovery/interrupted_idempotent"))
            state.current_event_id = ""; self.store.save(state)
        if not state.ready_node_ids:
            return False
        node_id = state.ready_node_ids[0]
        job.status = "running"
        job.current_event_id = node_id
        self._save_job(job)
        self.adapter.task_id = job.job_id
        self.adapter.project_id = job.project_id
        self.adapter.event_type = definition.node(node_id).event_type
        ctx = EventContext(
            workspace=str(self.root), instance_workspace=str(self.root / 'instances' / (job.assigned_instance or job.origin_instance or self.instance_id)),
            instance_id=job.assigned_instance or job.origin_instance or self.instance_id, project_id=job.project_id,
            job_id=job.job_id, channel=job.channel, sender_id=job.sender_id,
            intake_instance_id=str(getattr(job, "intake_instance_id", "") or ""),
            adapter=self.adapter,
        )
        initial = {
            "request": job.request, "channel": job.channel,
            "sender_id": job.sender_id, "origin_instance": job.origin_instance,
            "project_id": job.project_id, "instance_id": ctx.instance_id,
            "job_id": job.job_id, "root_event_id":job.root_event_id, "report_policy": job.report_policy,
            "intent_contract": job.intent_contract,
            "attachments": job.attachments,
        }
        if job.suspended_flows:
            initial.update(dict(job.suspended_flows[-1].get("context") or {}))
        if state.flow_type == 'project_cycle':
            from partner.events.cycle import enrich
            initial = enrich(ctx, initial, state.node_outputs)
        from partner.runtime.wait_notifications import dispatch_if_due
        await dispatch_if_due(self,job,state,ctx)
        pending=asyncio.create_task(self.runner.run_ready_node(state,definition,node_id,ctx,initial))
        while not pending.done():
            done,_=await asyncio.wait({pending},timeout=30)
            if not done: await dispatch_if_due(self,job,state,ctx)
        result=await pending
        # SIDE-BAND: per-event progress message (only for round-style flows).
        # emit_progress runs after a node completes; it enqueues a short
        # Chinese message to the same outbound queue used by message_critic -> send.
        # Failures are isolated — they must not block the round.
        if (result.output.get("ok") and result.flow_state.flow_type in
                {"project_cycle_round", "project_iteration_round"}):
            try:
                self._emit_progress_message(
                    job=job, flow_state=result.flow_state,
                    node_id=node_id, node_output=result.output,
                )
            except Exception as exc:
                # Side-band failure must not affect round advancement.
                pass
        if result.output.get("status") == "waiting":
            self._save_job(job)
            return False
        if self._apply_control(job, result.flow_state):
            return True
        if node_id == 'init' and result.output.get('ok'):
            project_id = result.output.get('project_id')
            if project_id:
                job.project_id = result.flow_state.project_id = str(project_id)
                self.store.save(result.flow_state)
        if (node_id in {'understand_3', 'init'} and result.output.get('ok')
                and job.channel and job.sender_id and job.project_id):
            from partner.projects.session_context import SessionContext
            from partner.projects.dynamic_project_registry import DynamicProjectRegistry
            if DynamicProjectRegistry(workspace_root=self.root).get_brief_summary(job.project_id):
                origin = job.intake_instance_id or job.origin_instance or job.persona_hint
                SessionContext(workspace_root=self.root).bind(
                    f'{job.channel}:{origin}:{job.sender_id}', job.project_id)
        job.current_event_id = ""
        job.ready_event_ids = list(result.flow_state.ready_node_ids)
        job.completed_event_ids = list(result.flow_state.completed_node_ids)
        job.status = result.flow_state.status if result.flow_state.status != "running" else "running"
        # A child branch may finish either successfully or honestly failed.
        # Keep the owning Job schedulable for the next checkpoint, where the
        # parent is resumed and can report the child outcome.
        if result.flow_state.status in {"completed", "failed", "cancelled"} and job.suspended_flows:
            job.status = "running"
        if result.flow_state.status == "failed":
            job.error = str(result.output.get("error") or "event_flow_failed")
        elif result.flow_state.status == "completed" and not job.suspended_flows:
            # A historical failure that the flow later recovered from must not
            # leave status=completed + error=event_flow_failed.  Clear the stale
            # error so the authoritative terminal state is self-consistent.
            job.error = ""
        if node_id=='execute' and result.output.get('requested_child_flow') and not job.suspended_flows:
            from partner.runtime.domain_handoff import insert_video
            insert_video(self,job,result.flow_state,result.output['requested_child_flow'])
        if result.flow_state.flow_type == "project_iteration" and node_id == "route":
            semantic = result.output.get("semantic_output") if isinstance(result.output.get("semantic_output"), dict) else {}
            child_name = {"active_learning": "active_learning",
                          "self_evolution": "self_evolution"}.get(str(semantic.get("primary_route") or ""))
            if child_name:
                evidence = []
                for value in result.flow_state.node_outputs.values():
                    if isinstance(value, dict):
                        evidence.extend(str(x) for x in value.get("evidence_refs") or value.get("files") or [])
                child_definition = self.flows.get(child_name)
                child = self.controller.start(
                    child_definition, catalog_version=self.catalog.version,
                    task_id=job.job_id, project_id=job.project_id, instance_id=self.instance_id)
                child.root_event_id = result.flow_state.root_event_id
                self.store.save(child)
                self.controller.suspend_for_child(
                    result.flow_state, resume_node_id="notify", child_flow_id=child.flow_id,
                    reason=f"selector inserted {child_name}")
                job.suspended_flows.append({
                    "parent_flow_id": result.flow_state.flow_id,
                    "child_flow_id": child.flow_id,
                    "context": {"evidence_refs": list(dict.fromkeys(evidence)),
                                "parent_flow_outputs": result.flow_state.node_outputs},
                })
                job.flow_id = child.flow_id; job.flow_type = child.flow_type
                job.ready_event_ids = list(child.ready_node_ids); job.status = "running"
        cycle_child = result.output.get('cycle_child') or (
            result.output.get('semantic_output') or {}).get('cycle_child')
        if cycle_child:
            from partner.runtime.cycle_children import start_child
            output_for_child = dict(result.output)
            output_for_child['cycle_child'] = cycle_child
            start_child(self, job, result.flow_state, output_for_child)
        self._save_job(job)
        return True

    async def run_forever(self, poll_seconds: float = 1.0) -> None:
        if threading.current_thread() is threading.main_thread():
            for name in ("SIGTERM", "SIGINT"):
                if hasattr(signal, name):
                    signal.signal(getattr(signal, name), self.stop)
        while not self._stopping:
            # A slow filesystem must not block the asyncio control loop.
            # Await the single scan: never overlap claim attempts on this worker.
            job = await asyncio.to_thread(self.next_job)
            if not job:
                await asyncio.sleep(poll_seconds)
                continue
            try:
                # Run the WHOLE job lifecycle under one atomic claim.  This
                # prevents other workers from re-entering the same flow and
                # re-running nodes (the previous one-checkpoint-per-claim
                # design let three workers alternate on one job and replay
                # execute/verify/send several times).
                await self._run_with_lease(job)
            finally:
                self._release_claim()

    async def _run_with_lease(self, job):
        if not getattr(self, '_db_lease_owner', None):
            return await self._run_job_to_terminal(job)
        stop = threading.Event()
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(self._run_job_to_terminal(job))
        def heartbeat():
            while not stop.wait(10):
                try:
                    valid = self._renew_lease()
                except Exception:
                    valid = False
                if not valid:
                    self._lease_lost = True
                    loop.call_soon_threadsafe(task.cancel)
                    return
        thread = threading.Thread(target=heartbeat, daemon=True, name='partner-lease')
        thread.start()
        try:
            await task
        except asyncio.CancelledError:
            self._lease_lost = True
            self._stopping = True
            raise
        finally:
            stop.set()
            await asyncio.to_thread(thread.join, 35)
            if thread.is_alive():
                self._lease_lost = True
                self._stopping = True

    async def _run_job_to_terminal(self, job: JobRecord) -> None:
        """Drive one claimed job through checkpoints until it reaches a
        terminal state (completed/failed/cancelled) or produces no work.
        A crash marks the job failed instead of re-claiming in a loop."""
        for _ in range(500):  # generous safety cap against runaway loops
            if self._stopping:
                return  # A saved Event boundary is safe for graceful reload.
            try:
                worked = await self.run_job_checkpoint(job)
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.error = f"checkpoint_crashed: {type(exc).__name__}: {exc}"
                try:
                    self._save_job(job)
                except Exception:
                    pass
                return
            if not worked:
                return
            if job.status == 'paused':
                return
            if job.status in {"completed", "failed", "cancelled"}:
                self._maybe_continue_iteration(job)
                from partner.runtime.iteration_receipts import write_request_receipts
                write_request_receipts(self.root, job.root_event_id)
                return

    def _maybe_start_report(self, job: JobRecord, *, supersedes_report: str = '',
                            revision_source: str = '', review_feedback: str = '',
                            reissue_source: str = '') -> None:
        """When a project_iteration flow completes with the selector's
        ``complete`` route, enqueue a PDF_REPORT job so the final milestone
        becomes a real Chinese PDF (report_decide → draft → pdf_render)."""
        import uuid
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        try:
            flow = self.store.load(job.flow_id)
        except Exception:
            return
        # 从已完成的 project_iteration flow 收集证据文件
        evidence = [str(a.get("path") if isinstance(a, dict) else a) for a in job.attachments]
        inherited_evidence = []
        evidence_ids = {}
        figure_manifest = ""
        if supersedes_report:
            prior_report = self._load_job(self.jobs_dir / f'{supersedes_report}.json')
            if prior_report:
                # Revision feedback is additional evidence, not a replacement
                # for sources supporting the already verified original text.
                prior_contract = prior_report.intent_contract or {}
                inherited_evidence = list(prior_contract.get('evidence_refs') or [])
                evidence.extend(inherited_evidence)
                try:
                    previous_outputs = self.store.load(prior_report.flow_id).node_outputs
                    previous_sources = previous_outputs.get('sources', {})
                    figure_manifest = previous_outputs.get('visuals',{}).get('manifest_path','')
                    if figure_manifest and Path(figure_manifest).is_file():
                        for asset in json.loads(Path(figure_manifest).read_text()).get('images',[]):
                            evidence.extend(r['path'] for r in asset['sources'])
                    evidence_ids = {row['path']:row['evidence_id'] for row in
                        previous_sources.get('semantic_output', {}).get('sources', [])}
                except (OSError, ValueError, KeyError):
                    pass
        try:
            for value in flow.node_outputs.values():
                if not isinstance(value, dict):
                    continue
                sem = value.get("semantic_output") if isinstance(value.get("semantic_output"), dict) else {}
                evidence.extend(str(x) for x in (
                    value.get("evidence_refs") or value.get("files")
                    or sem.get("evidence_refs") or sem.get("files") or []))
        except Exception:
            pass
        # The latest verified result is the report's primary evidence. Historical
        # experiments remain indexed, but must not crowd or silently redefine it.
        primary_paths = set(evidence)
        prior_verified = []
        from partner.index.resource_catalog import related_jobs
        from partner.index.worker_patch import _build_jobrecord
        for previous_record in related_jobs(self.root,job.root_event_id):
            previous_job = _build_jobrecord(previous_record)
            if (previous_job and previous_job.root_event_id == job.root_event_id
                    and previous_job.flow_type == 'project_iteration' and previous_job.flow_id):
                try:
                    previous_flow = self.store.load(previous_job.flow_id)
                    verification = previous_flow.node_outputs.get('verify', {})
                    if verification.get('business_delta'):
                        refs = verification.get('evidence_refs') or []
                        evidence.extend(refs)
                        prior_verified.append((previous_job.updated_at, refs))
                except (OSError, ValueError):
                    pass
        if not flow.node_outputs.get('verify', {}).get('business_delta') and prior_verified:
            primary_paths = set(str(a.get('path') if isinstance(a, dict) else a)
                                for a in job.attachments)
            primary_paths.update(max(prior_verified, key=lambda item:item[0])[1])
        primary_paths.update(inherited_evidence)
        # 去重 + 排除 "执行结果.md" / .tmp / 不存在的路径
        seen = set()
        filtered = []
        for x in dict.fromkeys(evidence):
            if not x or x in seen:
                continue
            seen.add(x)
            if x.endswith("执行结果.md") or x.endswith(".tmp"):
                continue
            if not os.path.isfile(x):
                continue
            try:
                if os.path.getsize(x) == 0:
                    continue
            except OSError:
                continue
            filtered.append(x)
        import hashlib
        indexed = []
        seen_hashes = set()
        for raw in filtered:
            with open(raw, 'rb') as handle:
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            indexed.append({'path':raw, 'sha256':digest,
                            'role':'primary' if raw in primary_paths else 'historical'})
        priorities={'.json':0,'.csv':1,'.py':2,'.md':3,'.txt':3,'.smi':4,'.sdf':4,'.png':5,'.jpg':5}
        evidence=[]
        for row in sorted(indexed, key=lambda r:priorities.get(Path(r['path']).suffix.lower(),6)):
            if row['role'] != 'primary':continue
            if row['sha256'] in seen_hashes:continue
            seen_hashes.add(row['sha256'])
            evidence.append(row['path'])
            if len(evidence)>=40:break
        context_refs = [doc['path'] for item in (flow.node_outputs.get('inspect', {}).get('semantic_output', {}).get('local_input_context') or [])
                        for doc in item.get('documents', []) if doc.get('path')]
        if supersedes_report and prior_report:
            context_refs.extend(prior_contract.get('context_refs') or [])
        report_job = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:16]}",
            project_id=job.project_id, title=job.title,
            request=(f"【生成报告】项目「{job.project_id}」本次有界运行已结束，"
                     f"整理真实结论与证据生成中文 PDF 报告。"),
            route="enqueue_work", channel=job.channel, sender_id=job.sender_id,
            persona_hint=job.persona_hint, origin_instance=job.origin_instance,
            assigned_instance=job.assigned_instance,
            intake_instance_id=job.intake_instance_id or job.assigned_instance,
            status="queued", created_at=now, updated_at=now,
            report_policy=job.report_policy,
            attachments=job.attachments,
            root_event_id=job.root_event_id,
            intent_contract={"notification_kind": "final", "force": True,
                             "supersedes_report": supersedes_report,
                             "revision_source": revision_source, "review_feedback": review_feedback,
                             "reissue_source": reissue_source,
                             "evidence_ids": evidence_ids, "figure_manifest": figure_manifest,
                             "evidence_refs": evidence, "context_refs": context_refs,
                             "original_request": job.intent_contract.get("original_request") or job.request},
        )
        from partner.runtime.action_execution import write_json
        manifest = self.root/'state/application/report_evidence'/f"{report_job.job_id}.json"
        write_json(manifest, {'role':'evidence index, not a new experiment', 'files':indexed})
        report_job.intent_contract['context_refs'].append(str(manifest))
        definition = self.flows.get("pdf_report_reissue" if reissue_source else
                                    "pdf_report_revision" if revision_source else "pdf_report")
        if definition is None:
            return
        flow_state = self.controller.start(
            definition, catalog_version=self.catalog.version,
            task_id=report_job.job_id, project_id=report_job.project_id,
            instance_id=report_job.intake_instance_id,
        )
        flow_state.root_event_id = job.root_event_id
        self.store.save(flow_state)
        report_job.flow_id = flow_state.flow_id
        report_job.flow_type = flow_state.flow_type
        report_job.ready_event_ids = list(flow_state.ready_node_ids)
        self._enqueue_followup(report_job)

    def _enqueue_followup(self, job):
        # New work has no lease; it must be claimed independently by a worker.
        from partner.index.job_repository import init
        repo=init(self.root)
        if repo.get_record(job.job_id):raise RuntimeError('followup already exists')
        repo.upsert_from_record(job.to_dict(),actor='enqueue_followup',
            projection_path=self.jobs_dir/f'{job.job_id}.json')
        for item in repo.outbox_pending():
            if item['job_id']==job.job_id:repo.outbox_emit_legacy_json(item['seq'])

    def _maybe_continue_iteration(self, job: JobRecord) -> None:
        """Honour a completed project_iteration flow's ``continue_project``
        decision by enqueueing the next iteration job.

        In the C1 shared-worker pool the instance is a pure transport and no
        longer runs the old instance-native loop, so the worker itself drives
        multi-round iteration.  A configurable cap per original request prevents a
        runaway loop; the selector's own ``complete`` route is the normal
        terminator.
        """
        if job.status=='failed' and job.flow_type.startswith('pdf_report'):
            from partner.runtime.wait_notifications import enqueue_report_failure
            enqueue_report_failure(self,job)
            return
        if job.status == 'completed' and self.shared_mode and job.flow_type == 'browser_video_learning':
            flow = self.store.load(job.flow_id)
            if not flow.failed_node_ids and job.report_policy != 'none':
                self._maybe_start_report(job)
            return
        if job.status != "completed" or not self.shared_mode or job.flow_type not in {"project_iteration", "new_project"}:
            return
        try:
            flow = self.store.load(job.flow_id)
        except Exception:
            return
        if flow.selected_route in {"complete", "report"}:
            if job.report_policy != 'none':
                self._maybe_start_report(job)
            return
        # 只对 continue_project 推进；active_learning / self_evolution 当下停一轮，
        # 等那一轮真正执行完再由本函数重新评估，避免无限嵌套叙事。
        if flow.selected_route != "continue_project":
            return
        from partner.index.resource_catalog import related_jobs
        count = len(related_jobs(self.root,job.root_event_id))
        try:
            runtime = json.loads((self.root/'config/partner_config.json').read_text()).get('runtime') or {}
            max_rounds = max(1, min(50, int(runtime.get('project_iteration_max_rounds', 12))))
        except (OSError, ValueError, TypeError):
            max_rounds = 12
        from partner.runtime.request_budget import round_limit, expired
        max_rounds = round_limit(job.intent_contract, max_rounds)
        if count >= max_rounds or expired(job.intent_contract):
            # 达到本次请求预算后报告真实进度，不将预算耗尽视为目标达成。
            if job.report_policy != 'none':
                self._maybe_start_report(job)
            return
        import uuid
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        # 带上上一轮的具体下一步，避免 execute 陷入"探索环境"而非执行任务。
        next_question = ""
        next_candidates = []
        try:
            reflect = flow.node_outputs.get("reflect", {})
            route_out = flow.node_outputs.get("route", {})
            refl_sem = reflect.get("semantic_output", {}) if isinstance(reflect, dict) else {}
            route_sem = route_out.get("semantic_output", {}) if isinstance(route_out, dict) else {}
            next_question = str(refl_sem.get("next_question") or "")
            next_candidates = list(route_sem.get("next_event_candidates") or [])
        except Exception:
            pass
        # 强制注入"下一轮必须产物文件"——杜绝叙事迭代。
        followup = (next_candidates[0] if next_candidates else "") or next_question
        proposed = (flow.node_outputs.get('continuation') or {}).get('semantic_output') or {}
        if proposed.get('next_goal'):
            followup = proposed
        # 收集上一轮产物路径（真实证据），下一轮 request 直接列在 prompt 里。
        prev_evidence = []
        try:
            for nid in ("init", "execute", "verify", "reflect"):
                v = flow.node_outputs.get(nid, {})
                if isinstance(v, dict):
                    sem = v.get("semantic_output", {}) if isinstance(v.get("semantic_output"), dict) else {}
                    prev_evidence.extend(str(x) for x in (v.get("evidence_refs") or v.get("files")
                                                          or sem.get("evidence_refs") or sem.get("files") or []))
        except Exception:
            pass
        prev_evidence = [x for x in dict.fromkeys(prev_evidence) if x][:5]
        prev_block = ("\n上一轮已留证据（先核验再复用，失败或不适用的结果不能当成有效输入）：\n  - " +
                      "\n  - ".join(prev_evidence)) if prev_evidence else ""
        followup_block = (f"\n仲裁器提出的下一步建议（不是用户约束，可根据原始目标和实际预算修正）：{followup}" if followup else "")
        request = (
            f"【继续迭代 · 第 {count + 1}/{max_rounds} 轮 · 必须产生新证据】"
            f"原始目标：{job.intent_contract.get('original_request') or job.request}\n"
            f"项目「{job.project_id}」上一轮流程已结束，业务成败以验证回执为准。{followup_block}"
            f"{prev_block}"
            "\n【硬约束】本轮 execute 必须产生可核验的新实验数据或具体问题的解决证据，并保存到 work_dir。"
            "文件数量或改名复制不算进展；请保存可解析的数据以及可复现脚本、命令和真实指标。"
            "如果无法产出真文件（如输入不可用、缺工具），必须在 reflect 中说明失败原因，"
            "并由下一轮真正推进，禁止用 'ls / find / 探索环境' 作为有效动作。"
        )
        next_job = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:16]}",
            project_id=job.project_id, title=job.title,
            request=request,
            route="enqueue_work", channel=job.channel, sender_id=job.sender_id,
            persona_hint=job.persona_hint, origin_instance=job.origin_instance,
            assigned_instance=job.assigned_instance,
            intake_instance_id=job.intake_instance_id or job.assigned_instance,
            status="queued", created_at=now, updated_at=now,
            report_policy=job.report_policy,
            attachments=job.attachments,
            root_event_id=job.root_event_id,
            intent_contract={"original_request":job.intent_contract.get("original_request") or job.request,
                "execution_constraints":dict(job.intent_contract.get('execution_constraints') or {}),
                "previous_artifact_hashes":list(set((job.intent_contract.get("previous_artifact_hashes") or []) +
                    [row.get("sha256", "") for row in (flow.node_outputs.get("verify", {}).get("semantic_output", {}).get("evidence") or [])]))},
        )
        definition = self.flows.get("project_iteration")
        flow_state = self.controller.start(
            definition, catalog_version=self.catalog.version,
            task_id=next_job.job_id, project_id=next_job.project_id,
            instance_id=next_job.intake_instance_id,
        )
        flow_state.root_event_id = job.root_event_id
        self.store.save(flow_state)
        next_job.flow_id = flow_state.flow_id
        next_job.flow_type = flow_state.flow_type
        next_job.ready_event_ids = list(flow_state.ready_node_ids)
        self._enqueue_followup(next_job)


def run_instance_event_worker(workspace: str, instance_id: str) -> None:
    asyncio.run(EventWorker(workspace, instance_id).run_forever())
