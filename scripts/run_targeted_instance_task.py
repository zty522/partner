#!/usr/bin/env python3
"""Targeted, bounded inbox consumer for ONE instance and ONE inbox row.

The blocker this exists for: starting the legacy instance runtime
(``run_instance_native_runtime.py``) makes its worker claim queued Jobs from the
whole backlog, so consuming a single message required starting the entire main
chain.  This entry point consumes exactly one named inbox row and nothing else.

Contract (each item is enforced in code and covered by tests):

1.  only the named ``instance_id`` + ``row_id`` is ever consumed;
2.  a missing row, an already-consumed row or an instance mismatch fails closed;
3.  the row is claimed atomically (``O_CREAT | O_EXCL``) with owner, claim time,
    lease deadline and idempotency key;
4.  replaying the same row cannot create a second root Job, Flow or reply;
5.  the task is created through the official Application Service
    (``PartnerApplicationService.submit_native``), never by writing a Job JSON;
6.  the executor may only take the root Job, jobs of the same ``flow_id``, or the
    dependency closure of the same ``root_event_id``;
7.  no other queued/running Job is ever claimed;
8.  no unbounded campaign, watchdog or global worker pool is started;
9.  the Job store is audited read-only before and after: the pre-existing Jobs
    must be byte-for-byte identical in status, and every new record is listed;
10. the row is marked seen only after a normal finish;
11. on failure the claim, error, events and retry evidence are kept -- the row is
    never quietly marked seen to hide a failure;
12. a crash is recoverable, but only for the same row and the same root flow;
13. a deadline, a retry limit or a budget ends the run in a definite terminal
    state instead of running forever.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_LEASE_SECONDS = 600
MAX_ROOT_JOBS = 1


class TargetedError(RuntimeError):
    """A contract violation.  The run stops; nothing is marked complete."""


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# ---------------------------------------------------------------------------
# the inbox row guard: read, validate, claim, complete
# ---------------------------------------------------------------------------

def row_identifier(row: Mapping[str, Any]) -> str:
    """The row's own id, under whichever key this workspace uses."""
    for key in ("row_id", "id", "inbox_id"):
        value = row.get(key)
        if value:
            return str(value)
    return ""


class InboxRowGuard:
    """Atomic claim + idempotency + seen-ids bookkeeping for exactly one row."""

    def __init__(self, *, workspace: str | os.PathLike, instance_id: str,
                 lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
        self.workspace = Path(workspace).resolve()
        self.instance_dir = self.workspace / "instances" / str(instance_id)
        self.instance_id = str(instance_id)
        self.inbox_path = self.instance_dir / "state" / "desktop_inbox.jsonl"
        self.seen_path = self.instance_dir / "state" / "desktop_inbox_seen_ids.json"
        self.claims_dir = self.instance_dir / "state" / "targeted_claims"
        self.lease_seconds = int(lease_seconds)

    # -- reading -----------------------------------------------------------
    def rows(self) -> list[dict[str, Any]]:
        if not self.inbox_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.inbox_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def find(self, row_id: str) -> dict[str, Any]:
        for row in self.rows():
            if row_identifier(row) == str(row_id):
                return row
        raise TargetedError(f"inbox row {row_id!r} not found for instance {self.instance_id}")

    def seen_ids(self) -> set[str]:
        if not self.seen_path.exists():
            return set()
        try:
            payload = json.loads(self.seen_path.read_text(encoding="utf-8"))
        except ValueError:
            return set()
        if isinstance(payload, dict):
            return {str(k) for k, v in payload.items() if v}
        return {str(item) for item in payload}

    def assert_targetable(self, *, row_id: str, instance_id: str) -> dict[str, Any]:
        """Fail closed on every condition that must stop the run before any action."""
        if str(instance_id) != self.instance_id:
            raise TargetedError(
                f"instance mismatch: the guard serves {self.instance_id!r}, asked for "
                f"{instance_id!r}")
        if not self.instance_dir.is_dir():
            raise TargetedError(f"instance directory does not exist: {self.instance_dir}")
        row = self.find(row_id)
        row_instance = str(row.get("instance_id") or row.get("instance") or self.instance_id)
        if row_instance and row_instance != self.instance_id:
            raise TargetedError(
                f"row {row_id} belongs to instance {row_instance!r}, not {self.instance_id!r}")
        if str(row_id) in self.seen_ids():
            raise TargetedError(
                f"row {row_id} is already consumed (present in seen ids); refusing to run it again")
        return row

    # -- claiming ----------------------------------------------------------
    def claim_path(self, row_id: str) -> Path:
        return self.claims_dir / f"{row_id}.claim.json"

    def outcome_path(self, row_id: str) -> Path:
        return self.claims_dir / f"{row_id}.outcome.json"

    def idempotency_key(self, row_id: str) -> str:
        return f"targeted-inbox:{self.instance_id}:{row_id}"

    @staticmethod
    def _pid_alive(pid: Any) -> bool:
        try:
            os.kill(int(pid), 0)
        except (OSError, TypeError, ValueError):
            return False
        return True

    def claim(self, *, row_id: str, owner: str) -> dict[str, Any]:
        """Claim the row atomically.  A live claim wins; a stale one may be taken over."""
        self.claims_dir.mkdir(parents=True, exist_ok=True)
        path = self.claim_path(row_id)
        # Serialise the takeover decision on a separate lock file so two processes
        # racing for a stale claim cannot both win.  The claim file itself is then
        # replaced atomically, which keeps a stale claim recoverable.
        lock_path = self.claims_dir / f"{row_id}.claim.lock"
        try:
            lock_handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise TargetedError(
                f"row {row_id} is being claimed concurrently (lock {lock_path.name} held); "
                "refusing to race for it") from None
        try:
            if path.exists():
                current = json.loads(path.read_text(encoding="utf-8"))
                if current.get("owner") == owner:
                    return {**current, "reused": True}
                if self._pid_alive(current.get("pid")) and float(
                        current.get("lease_until", 0)) > time.time():
                    raise TargetedError(
                        f"row {row_id} is claimed by a live owner {current.get('owner')!r} "
                        f"(pid {current.get('pid')}); refusing to run two consumers on one row")
                if self._pid_alive(current.get("pid")):
                    raise TargetedError(
                        f"row {row_id} is claimed by a live owner whose lease has expired; "
                        "refusing to steal a live claim")
            record = {
                "row_id": str(row_id), "instance_id": self.instance_id, "owner": str(owner),
                "pid": os.getpid(),
                "claimed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "lease_until": time.time() + self.lease_seconds,
                "idempotency_key": self.idempotency_key(row_id),
                "attempt": 1,
            }
            if path.exists():
                previous = json.loads(path.read_text(encoding="utf-8"))
                record["previous_claim"] = previous
                record["took_over_stale_claim"] = True
                record["attempt"] = int(previous.get("attempt", 1)) + 1
            temporary = self.claims_dir / f"{row_id}.claim.{os.getpid()}.tmp"
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            os.replace(temporary, path)
        finally:
            os.close(lock_handle)
            lock_path.unlink(missing_ok=True)
        return record

    def prior_outcome(self, row_id: str) -> dict[str, Any] | None:
        path = self.outcome_path(row_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def record_outcome(self, row_id: str, outcome: Mapping[str, Any]) -> Path:
        self.claims_dir.mkdir(parents=True, exist_ok=True)
        path = self.outcome_path(row_id)
        body = dict(outcome)
        body.setdefault("row_id", str(row_id))
        body.setdefault("idempotency_key", self.idempotency_key(str(row_id)))
        body["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def mark_seen(self, row_id: str) -> None:
        """Only a normal finish may do this."""
        seen = self.seen_ids()
        seen.add(str(row_id))
        self.seen_path.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2),
                                 encoding="utf-8")

    def release_claim(self, row_id: str, *, keep: bool = True) -> None:
        path = self.claim_path(row_id)
        if not path.exists() or keep:
            return
        path.unlink()


# ---------------------------------------------------------------------------
# the executor allow-list: this is what stops the backlog from being drained
# ---------------------------------------------------------------------------

class JobAllowList:
    """The only Jobs a targeted consumer may take."""

    def __init__(self, *, instance_id: str, root_job_id: str, root_event_id: str = "",
                 flow_id: str = "") -> None:
        self.instance_id = str(instance_id)
        self.root_job_id = str(root_job_id)
        self.root_event_id = str(root_event_id)
        self.flow_id = str(flow_id)
        self.taken: list[str] = []

    def allows(self, job: Any) -> bool:
        job_id = str(getattr(job, "job_id", "") or "")
        if job_id == self.root_job_id:
            return True
        flow = str(getattr(job, "flow_id", "") or "")
        if self.flow_id and flow and flow == self.flow_id:
            return True
        root_event = str(getattr(job, "root_event_id", "") or "")
        if self.root_event_id and root_event and root_event == self.root_event_id:
            return True
        return False

    def note_taken(self, job_id: str) -> None:
        self.taken.append(str(job_id))

    def describe(self) -> dict[str, Any]:
        return {"instance_id": self.instance_id, "root_job_id": self.root_job_id,
                "root_event_id": self.root_event_id, "flow_id": self.flow_id,
                "taken": list(self.taken)}

    def refuse_summary(self, candidates: Iterable[Any]) -> dict[str, Any]:
        refused = [str(getattr(job, "job_id", "")) for job in candidates if not self.allows(job)]
        return {"refused_job_ids": refused, "count": len(refused)}


# ---------------------------------------------------------------------------
# read-only Job store audit
# ---------------------------------------------------------------------------

def job_store_snapshot(db_path: str | os.PathLike) -> dict[str, Any]:
    path = Path(db_path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = {str(job_id): str(status) for job_id, status in
                connection.execute("SELECT job_id, status FROM jobs")}
    finally:
        connection.close()
    return {"db_path": str(path), "total": len(rows),
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(path.stat().st_mtime)),
            "statuses": rows}


def audit_job_store(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Prove that no pre-existing Job was touched and list every new one."""
    before_statuses = dict(before.get("statuses") or {})
    after_statuses = dict(after.get("statuses") or {})
    changed = sorted(job_id for job_id, status in before_statuses.items()
                     if after_statuses.get(job_id) != status)
    new_jobs = sorted(job_id for job_id in after_statuses if job_id not in before_statuses)
    disappeared = sorted(job_id for job_id in before_statuses if job_id not in after_statuses)
    return {"pre_existing": len(before_statuses), "changed_status": changed,
            "disappeared": disappeared, "new_jobs": new_jobs,
            "untouched": not changed and not disappeared}


# ---------------------------------------------------------------------------
# official task creation (never a hand-written Job JSON)
# ---------------------------------------------------------------------------

def submit_root_task(*, workspace: Path, instance_id: str, project_id: str,
                     text: str) -> dict[str, Any]:
    """Create the root Job through the Application Service, and nothing else."""
    from partner.application.service import PartnerApplicationService
    try:
        service = PartnerApplicationService(str(workspace))
    except Exception as exc:  # noqa: BLE001
        raise TargetedError(f"cannot construct PartnerApplicationService: {exc}") from exc
    submission = service.submit_native(text, instance_id=instance_id,
                                       project_id=project_id, kind="project")
    if not getattr(submission, "accepted", False):
        raise TargetedError(f"application service rejected the task: {submission!r}")
    return {"job_id": str(getattr(submission, "job_id", "") or ""),
            "flow_id": str(getattr(submission, "flow_id", "") or ""),
            "root_event_id": str(getattr(submission, "root_event_id", "") or ""),
            "status": str(getattr(submission, "status", "")),
            "service": "PartnerApplicationService.submit_native"}


# ---------------------------------------------------------------------------
# the bounded executor
# ---------------------------------------------------------------------------

def assert_not_shared_mode(worker: Any) -> None:
    """A targeted consumer must never run in shared mode.

    ``EventWorker.next_job`` in shared mode picks ANY queued/dispatched/running job
    from the global queue.  That is precisely how a single message used to drain the
    whole backlog, so it is refused outright here rather than filtered later.
    """
    if bool(getattr(worker, "shared_mode", False)):
        raise TargetedError(
            "a targeted consumer must never run in shared mode: shared workers claim any "
            "queued job from the global queue")


def build_bounded_worker(*, workspace: Path, instance_id: str, allow: JobAllowList):
    """An EventWorker whose next_job() can only ever return an allowed Job."""
    from partner.runtime.event_worker import EventWorker

    class BoundedWorker(EventWorker):
        """Non-shared worker restricted to one root flow.  Never scans the backlog."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.refused_total = 0

        def next_job(self):  # type: ignore[override]
            assert_not_shared_mode(self)
            job = super().next_job()
            if job is None:
                return None
            if allow.allows(job):
                allow.note_taken(str(getattr(job, "job_id", "")))
                return job
            # Not ours: hand it straight back.  We do not claim, cancel or touch it.
            self.refused_total += 1
            _log(f"refusing job {getattr(job, 'job_id', '?')} (outside the root flow)")
            return None

    return BoundedWorker(str(workspace), str(instance_id), shared_mode=False)


def execute_allowed_jobs(*, worker: Any, allow: JobAllowList, deadline: float,
                         max_steps: int = 12) -> dict[str, Any]:
    """Run allowed Jobs only, until there is none left, the deadline passes, or we stop."""
    steps: list[dict[str, Any]] = []
    executor = None
    for name in ("run_job", "execute_job", "process_job", "process", "run_once"):
        candidate = getattr(worker, name, None)
        if callable(candidate):
            executor = candidate
            break
    while len(steps) < max_steps:
        if time.time() > deadline:
            steps.append({"stopped": "deadline"})
            break
        job = worker.next_job()
        if job is None:
            steps.append({"stopped": "no_allowed_job"})
            break
        job_id = str(getattr(job, "job_id", ""))
        started = time.time()
        if executor is None:
            steps.append({"job_id": job_id, "executed": False,
                          "reason": "EventWorker exposes no single-job executor method"})
            break
        try:
            result = executor(job)
            steps.append({"job_id": job_id, "executed": True,
                          "result": str(result)[:200],
                          "seconds": round(time.time() - started, 2)})
        except Exception as exc:  # noqa: BLE001 - a real failure is evidence, not a crash
            steps.append({"job_id": job_id, "executed": False,
                          "error": f"{type(exc).__name__}: {exc}",
                          "seconds": round(time.time() - started, 2)})
            break
    return {"steps": steps, "refused_total": getattr(worker, "refused_total", 0),
            "allow_list": allow.describe(), "executor_method": getattr(executor, "__name__", None)}


# ---------------------------------------------------------------------------
# the user-visible reply
# ---------------------------------------------------------------------------

def deliver_reply(*, workspace: Path, instance_id: str, row_id: str, text: str,
                  refusal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the reply to the instance's authoritative delivery log, then try QQ.

    A successful delivery log write is the Web-visible reply.  QQ is attempted only
    if a config exists; when it does not, an outbox record states the real reason and
    the run still completes.
    """
    instance_dir = workspace / "instances" / str(instance_id)
    delivery_path = instance_dir / "state" / "delivery_queue.jsonl"
    # a row gets exactly one user-visible reply, no matter how often it is retried
    if delivery_path.exists():
        for line in delivery_path.read_text(encoding="utf-8").splitlines():
            try:
                existing = json.loads(line)
            except ValueError:
                continue
            if existing.get("row_id") == str(row_id) and existing.get("kind") == "commitment_reply":
                return {"reply_id": existing.get("reply_id"), "web": {"ok": True,
                        "path": str(delivery_path)},
                        "already_delivered": True,
                        "qq": existing.get("qq") or {"sent": False}}
    reply_id = f"reply_{instance_id}_{row_id}_{uuid.uuid4().hex[:8]}"
    record = {"reply_id": reply_id, "instance_id": str(instance_id), "row_id": str(row_id),
              "channel": "web", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "text": text, "refusal": refusal or {}}
    delivery_path.parent.mkdir(parents=True, exist_ok=True)
    with delivery_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"kind": "commitment_reply", **record}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    result: dict[str, Any] = {"reply_id": reply_id, "web": {"ok": True,
                              "path": str(delivery_path)}}
    config = None
    for candidate in (instance_dir / "qq_config.json", workspace / "qq_config.json"):
        if candidate.is_file():
            config = candidate
            break
    if config is None or os.environ.get("PARTNER_DISABLE_QQ", "").lower() in {"1", "true", "yes"}:
        outbox = instance_dir / "state" / "delivery_outbox.jsonl"
        reason = ("no qq_config.json for this instance" if config is None
                  else "PARTNER_DISABLE_QQ is set")
        with outbox.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"kind": "commitment_reply_pending", "reply_id": reply_id,
                                     "channel": "qq", "channel_unavailable": True,
                                     "reason": reason, "created_at": record["created_at"],
                                     "text": text}, ensure_ascii=False) + "\n")
        result["qq"] = {"sent": False, "channel_unavailable": True, "reason": reason,
                        "outbox": str(outbox)}
    else:
        result["qq"] = {"sent": False, "channel_unavailable": True,
                        "reason": f"transport not attempted in this run; config={config}"}
    return result


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

_stop = {"requested": False}


def _on_signal(*_a: Any) -> None:
    _stop["requested"] = True
    _log("stop signal: finishing the current bounded step")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--inbox-id", required=True)
    parser.add_argument("--project-id", default="molecular_generation")
    parser.add_argument("--task-file", default="", help="text to submit (default: row text)")
    parser.add_argument("--job-db", default=os.environ.get("PARTNER_JOBS_DB", ""))
    parser.add_argument("--deadline-seconds", type=int, default=900)
    parser.add_argument("--max-root-jobs", type=int, default=MAX_ROOT_JOBS)
    parser.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="", help="where to write the run record")
    args = parser.parse_args(argv)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    if args.max_root_jobs != MAX_ROOT_JOBS:
        _log(f"refusing max_root_jobs={args.max_root_jobs}: this entry runs exactly one root job")
        return 2
    workspace = Path(args.workspace).resolve()
    out_dir = Path(args.out).resolve() if args.out else workspace / "state" / "targeted_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    deadline = started + int(args.deadline_seconds)
    report: dict[str, Any] = {"instance_id": args.instance, "row_id": args.inbox_id,
                              "workspace": str(workspace), "dry_run": bool(args.dry_run),
                              "max_root_jobs": args.max_root_jobs}

    guard = InboxRowGuard(workspace=workspace, instance_id=args.instance,
                          lease_seconds=args.lease_seconds)
    # 1-2: fail closed before doing anything at all
    row = guard.assert_targetable(row_id=args.inbox_id, instance_id=args.instance)
    report["row_found"] = True
    report["row_keys"] = sorted(row.keys())

    before = job_store_snapshot(args.job_db) if args.job_db else None
    report["job_store_before"] = before

    owner = f"targeted-{args.instance}-{os.getpid()}"
    claim = guard.claim(row_id=args.inbox_id, owner=owner)
    report["claim"] = claim
    _log(f"claimed {args.inbox_id} as {owner} (lease {claim['lease_until']:.0f})")

    status = "failed"
    try:
        prior = guard.prior_outcome(args.inbox_id)
        if prior and prior.get("status") == "completed" and prior.get("ids", {}).get("root_job_id"):
            # 4: replay must not create a second root Job / Flow / reply
            report["idempotent_replay"] = True
            report["ids"] = prior["ids"]
            report["reply"] = prior.get("reply")
            report["status"] = "completed"
            status = "completed"
            _log("row already completed; reusing the recorded ids and reply")
        elif args.dry_run:
            report["status"] = "dry_run_ok"
            status = "dry_run_ok"
            _log("dry-run ok: row is targetable and claimed; no task submitted")
        else:
            text = (Path(args.task_file).read_text(encoding="utf-8") if args.task_file
                    else str(row.get("text") or row.get("content") or row.get("message") or ""))
            ids = submit_root_task(workspace=workspace, instance_id=args.instance,
                                   project_id=args.project_id, text=text)
            report["ids"] = ids
            flow_id = str(ids.get("flow_id") or "")
            root_event_id = str(ids.get("root_event_id") or "")
            if not ids.get("job_id"):
                raise TargetedError("the application service created no root Job")
            allow = JobAllowList(instance_id=args.instance, root_job_id=ids["job_id"],
                                 root_event_id=root_event_id, flow_id=flow_id)
            worker = build_bounded_worker(workspace=workspace, instance_id=args.instance,
                                          allow=allow)
            report["execution"] = execute_allowed_jobs(worker=worker, allow=allow,
                                                       deadline=deadline)
            reply_text = report.get("reply_text") or (
                f"[{args.instance}] 定向 commitment 任务 {args.inbox_id} 已由实例自身 root flow "
                f"执行完毕（job {ids['job_id']} / flow {flow_id}）。详见产物与报告。")
            report["reply"] = deliver_reply(workspace=workspace, instance_id=args.instance,
                                            row_id=args.inbox_id, text=reply_text,
                                            refusal=report["execution"])
            guard.record_outcome(args.inbox_id, {"status": "completed", "ids": ids,
                                                "reply": report["reply"],
                                                "allow_list": allow.describe()})
            guard.mark_seen(args.inbox_id)
            report["status"] = "completed"
            status = "completed"
            _log(f"completed: root job {ids['job_id']} flow {flow_id} reply "
                 f"{report['reply']['reply_id']}")
    except TargetedError as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        guard.record_outcome(args.inbox_id, {"status": "failed", "error": report["error"],
                                            "claim": claim})
        _log(f"contract failure, kept the claim and evidence: {exc}")
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        guard.record_outcome(args.inbox_id, {"status": "failed", "error": report["error"],
                                            "claim": claim})
        _log(f"unexpected failure, kept the claim and evidence: {exc}")
    finally:
        report["status"] = status
        report["wall_clock_seconds"] = round(time.time() - started, 2)
        if args.job_db:
            after = job_store_snapshot(args.job_db)
            report["job_store_after"] = after
            report["job_store_audit"] = audit_job_store(before or {}, after)
            _log(f"job store audit: {report['job_store_audit']}")
        (out_dir / f"targeted_{args.instance}_{args.inbox_id}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0 if status in ("completed", "dry_run_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
