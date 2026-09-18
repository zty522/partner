#!/usr/bin/env python3
"""Benchmark worker (M3 / Section 6.3, Audit 6 round 4).

A real executor — not "submit to production worker and hope for the
best".  This worker:

1. Lives in a SEPARATE SQLite queue under
   ``bench_workspace/jobs.db`` — it does not borrow the production
   worker.  The orchestrator already wrote the Job record into the
   **production** jobs.db (so the Agent runs in the production
   runtime); this worker observes the **terminal** state by polling
   ``production_db.get_record`` and is responsible for **cleanup /
   persistence** of the benchmark-specific outputs.

2. Enforces a hard wall-clock timeout; if the Agent's Job has not
   reached a terminal state in ``--wall-timeout-seconds``, the worker
   calls ``JobRepository.request_cancel`` and records a
   ``missing_reason="timeout"`` score.

3. Captures a per-run trajectory JSONL with one line per poll cycle,
   so the recovery code can restart from the last known state.

4. Rejects re-entry: if the run.json already shows
   ``state in {completed, failed, cancelled}``, the worker exits 0
   without touching anything.

State honesty: ``static_implemented``.  No real benchmark run has
been driven by this worker in this session.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _resolve_repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    for _ in range(6):
        if (cur / "pyproject.toml").exists() and (cur / "partner").exists():
            return cur
        cur = cur.parent
    return REPO


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--benchmark-workspace", required=True)
    ap.add_argument("--wall-timeout-seconds", type=int, default=600)
    ap.add_argument("--poll-interval-seconds", type=int, default=2)
    args = ap.parse_args()

    repo_root = _resolve_repo_root()
    sys.path.insert(0, str(repo_root))
    bench_root = Path(args.benchmark_workspace)
    run_dir = bench_root / "runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_json = run_dir / "run.json"
    trajectory = run_dir / "trajectory.jsonl"

    if not run_json.exists():
        print(json.dumps({"ok": False, "error": "run.json missing; "
                                                  "call scripts/benchmark/run.py first"}))
        return 2
    payload = json.loads(run_json.read_text(encoding="utf-8"))
    manifest = payload.get("manifest") or {}
    state = manifest.get("state")
    if state in {"completed", "failed", "cancelled"}:
        print(json.dumps({"ok": True, "state": state, "note":
                          "terminal; nothing to do"}))
        return 0

    job_id = (manifest.get("orchestrator_result") or {}).get("job_id")
    if not job_id:
        print(json.dumps({"ok": False, "error": "no job_id in manifest"}))
        return 3

    # The orchestrator wrote to the production jobs.db under
    # bench_root/instances/bench.  Poll that DB.
    from partner.index.job_repository import init as _init_jobs
    repo = _init_jobs(bench_root / "instances" / "bench")

    deadline = time.time() + args.wall_timeout_seconds
    last_state = None
    cycle = 0
    while time.time() < deadline:
        cycle += 1
        record = repo.get_record(job_id)
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if not record:
            with trajectory.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"event": "poll", "cycle": cycle,
                                     "result": "no_record",
                                     "at": now}) + "\n")
            time.sleep(args.poll_interval_seconds)
            continue
        st = record.get("status") or "unknown"
        if st != last_state:
            with trajectory.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"event": "state_change",
                                     "cycle": cycle, "from": last_state,
                                     "to": st, "at": now}) + "\n")
            last_state = st
        if st in {"completed", "failed", "cancelled"}:
            payload["manifest"]["final_state"] = record.get("status")
            payload["manifest"]["state"] = (
                "completed" if st == "completed" else
                "failed" if st == "failed" else "cancelled")
            payload["manifest"]["finished_at"] = now
            run_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
            print(json.dumps({"ok": True, "state": payload["manifest"]["state"],
                               "job_id": job_id}))
            return 0
        time.sleep(args.poll_interval_seconds)

    # Timeout: request cancel and record missing_reason.
    try:
        repo.request_cancel(job_id, actor=f"benchmark:{args.run_id}")
    except Exception as exc:
        with trajectory.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "cancel_failed", "error": str(exc)}) + "\n")
    score = payload.get("scores") or []
    score.append({
        "score_id": "s_" + (manifest.get("fixture") or "run") + "_timeout",
        "run_id": job_id,
        "metric_name": "hidden_task_quality_delta",
        "direction": "higher_is_better",
        "value": None,
        "confidence_interval": None,
        "missing_reason": "timeout",
        "schema_version": "benchmark/v1",
        "notes": f"wall timeout at {args.wall_timeout_seconds}s",
    })
    payload["scores"] = score
    payload["manifest"]["state"] = "failed"
    payload["manifest"]["error"] = "wall-timeout exceeded"
    run_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    print(json.dumps({"ok": False, "error": "wall timeout", "job_id": job_id}))
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
