#!/usr/bin/env python3
"""Resume a previously-crashed benchmark run (M3 / Section 6.3) — round 5.

Resume reads the persisted ``runs/<run_id>/config.json`` (the
canonical run contract) and the ``runs/<run_id>/run.json`` (the
checkpointed manifest).  It then chooses one of three paths:

1. **terminal** — the run.json shows ``state in {completed, failed,
   cancelled}``; emit nothing, exit 0.
2. **active lease** — a worker still owns the Job; refuse to take
   over (exit 3) so we don't race against the live process.
3. **stale or missing checkpoint** — re-run the executor with the
   exact same ``run_id`` so the orchestrator's idempotency layer
   resolves the existing reservation (rather than starting over).

The resume never re-runs the Agent or recomputes the reference
oracle; if the run already has a ``scores`` list it is preserved.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _resolve_repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    for _ in range(6):
        if (cur / "pyproject.toml").exists() and (cur / "partner").exists():
            return cur
        cur = cur.parent
    return Path("/mnt/e/work/partner")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="")
    ap.add_argument("--run-json", default="")
    ap.add_argument("--benchmark-workspace", required=True)
    ap.add_argument("--rebuild-config-from-manifest", action="store_true",
                    help="If the config.json is missing, recover from run.json.")
    args = ap.parse_args()

    if not args.run_id and not args.run_json:
        ap.error("one of --run-id or --run-json is required")

    bench_root = Path(args.benchmark_workspace).expanduser().resolve()
    if args.run_json:
        run_json = Path(args.run_json).expanduser().resolve()
        run_dir = run_json.parent
        run_id = args.run_id or run_json.stem
    else:
        run_dir = bench_root / "runs" / args.run_id
        run_json = run_dir / "run.json"
        run_id = args.run_id

    if not run_json.exists():
        print(json.dumps({"ok": False, "error": "run.json missing; "
                                                  "call scripts/benchmark/run.py first"}))
        return 1

    payload = json.loads(run_json.read_text(encoding="utf-8"))
    manifest = payload.get("manifest") or {}

    # Non-replayable side-effect guard: refuse to resume if the trajectory
    # records an external side effect that cannot be safely replayed.
    for ev in (payload.get("trajectory") or []):
        if ev.get("external_side_effect"):
            issues_dir = bench_root / "issues"
            issues_dir.mkdir(parents=True, exist_ok=True)
            (issues_dir / f"resume_block_{run_id}.jsonl").write_text(
                json.dumps({"run_id": run_id, "event": ev.get("event"),
                            "evidence_ref": ev.get("evidence_ref"),
                            "blocked": "non_replayable_side_effect"}) + "\n",
                encoding="utf-8")
            print(json.dumps({"ok": False,
                              "error": "non-replayable external side effect recorded; refusing resume"}))
            return 6

    state = manifest.get("state") or manifest.get("execution_status")
    if state in {"completed", "failed", "cancelled"}:
        print(json.dumps({"ok": True, "state": state,
                           "note": "terminal; nothing to resume"}))
        return 0

    if state == "running":
        print(json.dumps({"ok": False, "error": "active lease; will not preempt",
                          "run_id": run_id}))
        return 3

    config_json = run_dir / "config.json"
    # Recover config from manifest if missing.
    if not config_json.exists():
        if args.rebuild_config_from_manifest:
            cfg = {
                "run_id": manifest.get("run_id") or run_id,
                "protocol_id": manifest.get("protocol_id"),
                "fixture_dotted": manifest.get("fixture"),
                "benchmark_workspace": str(bench_root),
                "wall_timeout_seconds": manifest.get("wall_timeout_seconds", 600),
                "wait_timeout_seconds": manifest.get("wait_timeout_seconds", 600),
                "model": manifest.get("model", "minimax/MiniMax-M3"),
                "parent_run_id": manifest.get("parent_run_id"),
                "seed": manifest.get("seed", 0),
                "method_arm": manifest.get("method_arm", "candidate"),
                "method_arm_label": manifest.get("method_arm_label", "candidate"),
                "code_sha": manifest.get("code_sha", "unknown"),
                "code_dirty": manifest.get("code_dirty", False),
                "started_at": manifest.get("started_at", time.strftime("%Y-%m-%dT%H:%M:%S%z")),
            }
            config_json.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        else:
            print(json.dumps({"ok": False, "error": "config.json missing; "
                                                      "pass --rebuild-config-from-manifest"}))
            return 4

    # Check active lease (only meaningful with a Job record).
    job_id = manifest.get("job_id")
    if job_id:
        repo_root = _resolve_repo_root()
        sys.path.insert(0, str(repo_root))
        try:
            from partner.index.job_repository import init as _init_jobs
            repo = _init_jobs(bench_root / "instances" / f"bench_{run_id}")
            record = repo.get_record(job_id)
            if record and record.get("status") == "running":
                expiry = record.get("lease_expiry") or 0
                if expiry > time.time():
                    print(json.dumps({
                        "ok": False, "error": "active lease; will not preempt",
                        "job_id": job_id, "lease_expiry": expiry,
                    }))
                    return 3
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"lease check: {exc}"}))
            return 3

    # Re-run the executor; idempotency layer ties the run_id to the
    # existing Job record, so the Agent does NOT execute again.
    cfg = json.loads(config_json.read_text(encoding="utf-8"))
    argv = [
        "executor.py",
        "--run-id", cfg["run_id"],
        "--protocol-id", cfg["protocol_id"],
        "--fixture", cfg["fixture_dotted"],
        "--benchmark-workspace", cfg["benchmark_workspace"],
        "--model", cfg.get("model", "minimax/MiniMax-M3"),
        "--method-arm", cfg.get("method_arm", "candidate"),
        "--method-arm-label", cfg.get("method_arm_label", "candidate"),
        "--seed", str(cfg.get("seed", 0)),
        "--wall-timeout-seconds", str(cfg.get("wall_timeout_seconds", 600)),
        "--wait-timeout-seconds", str(cfg.get("wait_timeout_seconds", 600)),
    ]
    if cfg.get("parent_run_id"):
        argv += ["--parent-run-id", cfg["parent_run_id"]]
    saved = sys.argv
    sys.argv = argv
    try:
        from scripts.benchmark.executor import main as _executor_main
        rc = _executor_main()
    finally:
        sys.argv = saved
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
