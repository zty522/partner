#!/usr/bin/env python3
"""Benchmark executor (M3 / Section 6.3) — round 6 rewrite.

Real execution path.  Does NOT invent Event names; does NOT call
non-existent APIs.  Instead it:

1. Submits the Job through ``orchestrate_submit`` using the
   pre-allocated ``job_id`` so the reservation ↔ Job relationship
   is durable from creation.
2. Polls the JobRepository's terminal state machine strictly:
   ``completed`` is the only success terminal.  ``failed`` /
   ``cancelled`` are recorded but NOT promoted to ``completed``.
   ``queued`` / ``running`` / unknown / missing record are handled
   explicitly (timeout → ``request_cancel`` + missing_reason).
3. The actual Job execution is done by the production EventWorker
   (process running independently).  This executor does NOT spawn a
   second driver; it waits and reads.
4. After terminal, runs ``fixture.reference_oracle`` (writes into
   ``reference/<task_id>/``) and ``fixture.score`` (reads frozen
   Agent outputs, compares byte-for-byte).
5. Writes the canonical ``run.json`` with execution_status /
   evaluation_status / research_outcome separation.

State honesty: ``static_implemented``.  No real benchmark has run.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger("partner.benchmark.executor")

REPO = Path(__file__).resolve().parents[2]


def _resolve_repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    for _ in range(6):
        if (cur / "pyproject.toml").exists() and (cur / "partner").exists():
            return cur
        cur = cur.parent
    return REPO


_TERMINAL_SUCCESS = {"completed"}
_TERMINAL_FAIL = {"failed", "cancelled"}
_TERMINAL_ALL = _TERMINAL_SUCCESS | _TERMINAL_FAIL


@dataclasses.dataclass
class BenchmarkRunConfig:
    run_id: str
    protocol_id: str
    fixture_dotted: str
    benchmark_root: Path
    workspace_dir: Path
    wall_timeout_seconds: int
    wait_timeout_seconds: int
    model: str
    parent_run_id: str | None
    seed: int
    method_arm: str
    method_arm_label: str
    code_sha: str
    code_dirty: bool
    started_at: str

    @classmethod
    def from_args(cls, args) -> "BenchmarkRunConfig":
        sha, dirty = _git_sha()
        bench_root = Path(args.benchmark_workspace).expanduser().resolve()
        # Each run gets its OWN workspace directory (keyed by run_id) so the
        # runtime DB (workspace_dir -> runtime_root/fingerprint) is isolated
        # per run.  This is what prevents a stale queue / missing-flow from
        # a previous run leaking into the next one.
        ws_dir = bench_root / "runs" / args.run_id / "workspace"
        return cls(
            run_id=args.run_id,
            protocol_id=args.protocol_id,
            fixture_dotted=args.fixture,
            benchmark_root=bench_root,
            workspace_dir=ws_dir,
            wall_timeout_seconds=args.wall_timeout_seconds,
            wait_timeout_seconds=args.wait_timeout_seconds,
            model=args.model,
            parent_run_id=args.parent_run_id,
            seed=args.seed,
            method_arm=args.method_arm,
            method_arm_label=args.method_arm_label,
            code_sha=sha,
            code_dirty=dirty,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )

    def jobs_db_path(self) -> Path:
        """The isolated runtime DB for this benchmark run.

        Worker (production EventWorker) reads from this same path so
        the Job record created here is the same one the worker
        claims and drives.
        """
        return self.workspace_dir / "instances" / "bench" / "jobs.db"

    def run_dir(self) -> Path:
        return self.benchmark_root / "runs" / self.run_id

    def inputs_root(self) -> Path:
        return self.workspace_dir / "inputs"

    def outputs_root(self) -> Path:
        return self.workspace_dir / "outputs"

    def reference_root(self) -> Path:
        return self.workspace_dir / "reference"

    def config_path(self) -> Path:
        return self.run_dir() / "config.json"

    def run_json_path(self) -> Path:
        return self.run_dir() / "run.json"


def _git_sha() -> tuple[str, bool]:
    repo_root = _resolve_repo_root()
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "-C", str(repo_root), "status", "--porcelain"], text=True
            ).strip()
        )
        return sha, dirty
    except Exception:
        return "unknown", True


def _load_protocol(protocol_id: str, *, repo_root: Path) -> dict:
    sys.path.insert(0, str(repo_root))
    from partner.benchmark.protocol_registry import resolve_path
    proto_path = resolve_path(repo_root, protocol_id)
    if not proto_path.exists():
        raise FileNotFoundError(
            f"protocol_id={protocol_id!r} not found via registry"
        )
    return json.loads(proto_path.read_text(encoding="utf-8"))


def _load_fixture(fixture_dotted: str, *, repo_root: Path):
    sys.path.insert(0, str(repo_root))
    mod = importlib.import_module(fixture_dotted)
    for required in ("TASK_DEFINITION", "ORACLE_INPUTS", "ORACLE_EXPECTED",
                     "prepare_initial_state", "reference_oracle", "score"):
        if not hasattr(mod, required):
            raise AttributeError(
                f"fixture {fixture_dotted!r} missing required entry {required!r}"
            )
    return mod


def _prepare_isolated_workspace(cfg: BenchmarkRunConfig) -> None:
    cfg.workspace_dir.mkdir(parents=True, exist_ok=True)
    cfg.run_dir().mkdir(parents=True, exist_ok=True)
    cfg.outputs_root().mkdir(parents=True, exist_ok=True)
    cfg.reference_root().mkdir(parents=True, exist_ok=True)
    cfg.jobs_db_path().parent.mkdir(parents=True, exist_ok=True)


def _primary_metric(protocol: dict) -> tuple[str, str]:
    primary = (protocol.get("primary_metrics") or [{}])[0] or {}
    name = str(primary.get("name") or "task_quality").strip()
    direction = str(primary.get("direction") or "higher_is_better").strip()
    return name, direction




def _production_workspace() -> Path:
    """Locate the production workspace (config source for credentials)."""
    pointer = Path(os.path.expanduser("~/.partner_workspace"))
    if pointer.exists():
        raw = pointer.read_text(encoding="utf-8").strip()
        return Path(raw).expanduser().resolve()
    return Path("/mnt/e/work/partner_workspace")


def _bootstrap_benchmark_workspace(cfg: BenchmarkRunConfig) -> None:
    """Make the isolated benchmark workspace a real Partner workspace so the
    production EventWorker can run in it:

    * ``config/partner_config.json`` — agent.backend=direct (reads api.json).
    * ``config/api.json`` — copied from production (LLM credentials).
    * ``projects/``, ``instances/bench/``, ``state/application/jobs/``.
    """
    ws = cfg.workspace_dir
    cfg_dir = ws / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    pcfg = cfg_dir / "partner_config.json"
    if not pcfg.exists():
        pcfg.write_text(json.dumps({
            "agent": {"backend": "direct", "provider": "deepseek", "model": None},
            "runtime": {"mode": "manual_stable"},
        }, ensure_ascii=False), encoding="utf-8")
    api_src = _production_workspace() / "config" / "api.json"
    api_dst = cfg_dir / "api.json"
    if api_src.exists() and not api_dst.exists():
        api_dst.write_text(api_src.read_text(encoding="utf-8"), encoding="utf-8")
    (ws / "projects").mkdir(parents=True, exist_ok=True)
    (ws / "instances" / "bench").mkdir(parents=True, exist_ok=True)
    (ws / "state" / "application" / "jobs").mkdir(parents=True, exist_ok=True)


def _start_benchmark_worker(cfg: BenchmarkRunConfig, repo_root: Path):
    """Start a controlled shared_worker process that claims and drives the
    benchmark Job in the isolated workspace.  This is the real executor —
    it reuses the production EventWorker (shared_mode) end-to-end, not a
    bespoke toy flow."""
    slot = abs(hash(cfg.run_id)) % 1000
    cmd = [
        sys.executable, "-m", "partner.runtime.shared_worker",
        "--workspace", str(cfg.workspace_dir),
        "--slot-index", str(slot),
        "--poll-seconds", "0.5",
    ]
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(repo_root))
    logf = (cfg.run_dir() / "worker.log").open("a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd, cwd=str(repo_root), env=env,
        stdout=logf, stderr=subprocess.STDOUT,
    )
    return proc, logf

def _submit_job(cfg: BenchmarkRunConfig, *, task: dict,
                 repo_root: Path) -> dict:
    """Submit the Job via the unified orchestrator.

    The orchestrator reserves the ``request_id=cfg.run_id`` slot,
    pre-allocates a ``job_id``, and writes both into the production
    jobs.db (isolated to bench_root).  The producer-side chain is:
        reservation.reserved (intent_pending)
        → svc.submit creates Job with preallocated_job_id
        → reservation.submitted with the new Job's id
    """
    sys.path.insert(0, str(repo_root))
    from partner.application.orchestrator import orchestrate_submit
    message = (
        "[benchmark_run_id=" + cfg.run_id + "] "
        "[fixture=" + str(task.get("task_id")) + "] "
        "[method_arm=" + cfg.method_arm + "] "
        "Inputs: " + str(cfg.inputs_root() / "fixtures" / str(task.get("family"))) + ". "
        "Outputs: " + str(cfg.outputs_root()) + ". "
        "Do NOT read: " + str(cfg.reference_root()) + ". "
        "Success criteria: " + json.dumps(task.get("success_criteria") or [], ensure_ascii=False) + "."
    )
    result = orchestrate_submit(
        workspace_root=str(cfg.workspace_dir),
        text=message,
        channel="local",
        sender_id="benchmark:" + cfg.run_id,
        sender_name="BenchmarkExecutor",
        persona_hint="02",
        project_id=str(task.get("family") or "benchmark"),
        # Only the ablation arm goes through execution_constraints
        # (validate_constraints whitelist).  Task config (fixture, model,
        # paths, seed) is already carried in the message body above.
        execution_constraints={
            "method_arm": cfg.method_arm,
            "method_arm_label": cfg.method_arm_label,
        },
        mode="project_iteration",
        scope="",
        request_id=cfg.run_id,
        recipient_ref=None,
        subject_allowed_instances=["01", "02", "03", "04", "05"],
        subject_id="benchmark-" + cfg.run_id,
        attachments=None,
        attachments_signature=None,
    )
    return {
        "job_id": result.job_id,
        "assigned_instance": result.assigned_instance,
        "persona_hint": result.persona_hint,
        "was_idempotent_hit": result.was_idempotent_hit,
        "owner_token": result.owner_token,
        "live_status": result.status,
    }


def _wait_terminal(*, cfg: BenchmarkRunConfig, job_id: str,
                    worker_proc=None) -> tuple[str, dict]:
    """Poll the JobRepository until the Job reaches a terminal state.

    Anything other than ``_TERMINAL_ALL`` (queued/running/unknown/no
    record) is in-progress.  The controlled worker's liveness is checked
    on every poll: if it exited early (crash / missing flow / import
    error) we return ``worker_failed`` immediately instead of burning the
    whole deadline, carrying the exit code.

    Returns ``(final_status, final_record)``.
    """
    sys.path.insert(0, str(_resolve_repo_root()))
    from partner.index.job_repository import init as _init_jobs
    repo = _init_jobs(cfg.workspace_dir)
    deadline = time.time() + cfg.wait_timeout_seconds
    last: dict = {}
    while time.time() < deadline:
        if worker_proc is not None:
            rc = worker_proc.poll()
            if rc is not None:
                return "worker_failed", {
                    "status": "worker_failed",
                    "error": f"benchmark worker exited early (exit code {rc})",
                    "worker_exit_code": rc,
                    "job_status": (last or {}).get("status", "unknown"),
                    "job_id": job_id,
                }
        record = repo.get_record(job_id)
        if record:
            last = record
            st = str(record.get("status") or "")
            if st in _TERMINAL_ALL:
                return st, record
        time.sleep(1.0)
    return "unknown", last  # timeout — caller handles


def _cancel_job(*, cfg: BenchmarkRunConfig, job_id: str) -> bool:
    try:
        from partner.index.job_repository import init as _init_jobs
        repo = _init_jobs(cfg.workspace_dir)
        return bool(repo.request_cancel(job_id, actor="benchmark:" + cfg.run_id))
    except Exception:
        return False


def _run_reference_and_score(cfg: BenchmarkRunConfig, fixture_mod,
                              protocol: dict) -> tuple[list[dict], list[dict]]:
    """Phase 3+4: reference oracle + score Agent outputs.

    Returns ``(scores, issues)``.
    """
    task = fixture_mod.TASK_DEFINITION
    pname, pdir = _primary_metric(protocol)
    agent_dir = cfg.outputs_root()
    try:
        reference_paths = fixture_mod.reference_oracle(cfg.workspace_dir,
                                                       cfg.run_id)
    except Exception as exc:
        return ([{"score_id": "s_" + cfg.run_id + "_primary",
                  "run_id": cfg.run_id, "metric_name": pname,
                  "direction": pdir, "value": None,
                  "confidence_interval": None,
                  "missing_reason": "oracle_failed",
                  "schema_version": "benchmark/v1", "notes": str(exc)}],
                [{"issue_id": "i_" + cfg.run_id + "_oracle",
                  "run_id": cfg.run_id, "category": "evaluator",
                  "severity": "error",
                  "summary": "reference oracle failed: " + str(exc),
                  "evidence_refs": [],
                  "reproduction_command": "scripts/benchmark/run.py",
                  "expected": "reference outputs",
                  "actual": str(exc),
                  "root_cause_confidence": 1.0,
                  "schema_version": "benchmark/v1", "notes": ""}])
    try:
        sr = fixture_mod.score(cfg.workspace_dir, cfg.run_id,
                                agent_dir, reference_paths)
    except Exception as exc:
        return ([{"score_id": "s_" + cfg.run_id + "_primary",
                  "run_id": cfg.run_id, "metric_name": pname,
                  "direction": pdir, "value": None,
                  "confidence_interval": None,
                  "missing_reason": "scorer_failed",
                  "schema_version": "benchmark/v1", "notes": str(exc)}],
                [{"issue_id": "i_" + cfg.run_id + "_scorer",
                  "run_id": cfg.run_id, "category": "evaluator",
                  "severity": "error",
                  "summary": "scorer raised: " + str(exc),
                  "evidence_refs": [],
                  "reproduction_command": "scripts/benchmark/run.py",
                  "expected": "scoring completes",
                  "actual": str(exc),
                  "root_cause_confidence": 1.0,
                  "schema_version": "benchmark/v1", "notes": ""}])

    matched = sr.get("matched", 0)
    expected = sr.get("expected", 0)
    score_value = sr.get("score")
    score = {"score_id": "s_" + cfg.run_id + "_primary",
             "run_id": cfg.run_id, "metric_name": pname,
             "direction": pdir, "value": score_value,
             "confidence_interval": None,
             "missing_reason": None if score_value is not None else "no_match",
             "schema_version": "benchmark/v1",
             "notes": str(matched) + "/" + str(expected) + " matched; "
                       + json.dumps(sr.get("stages", {}), ensure_ascii=False)}
    issues = []
    if matched < expected:
        issues.append({"issue_id": "i_" + cfg.run_id + "_partial",
                       "run_id": cfg.run_id,
                       "category": "candidate",
                       "severity": "warning",
                       "summary": str(matched) + "/" + str(expected) + " artefacts matched reference",
                       "evidence_refs": sorted(reference_paths.keys()),
                       "reproduction_command": "scripts/benchmark/run.py",
                       "expected": "all artefacts match",
                       "actual": str(matched) + "/" + str(expected),
                       "root_cause_confidence": 0.9,
                       "schema_version": "benchmark/v1", "notes": ""})
    return [score], issues


def _build_manifest(cfg, *, final_status, job_id, error=None,
                     execution_status="completed",
                     evaluation_status="completed",
                     research_outcome="completed"):
    return {
        "run_id": cfg.run_id, "protocol_id": cfg.protocol_id,
        "fixture": cfg.fixture_dotted,
        "parent_run_id": cfg.parent_run_id,
        "code_sha": cfg.code_sha, "code_dirty": cfg.code_dirty,
        "model": cfg.model, "method_arm": cfg.method_arm,
        "method_arm_label": cfg.method_arm_label, "seed": cfg.seed,
        "wall_timeout_seconds": cfg.wall_timeout_seconds,
        "budget": {"wallclock_seconds": cfg.wall_timeout_seconds,
                    "candidate_attempts": 4},
        "started_at": cfg.started_at,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "execution_status": execution_status,
        "evaluation_status": evaluation_status,
        "research_outcome": research_outcome,
        "job_id": job_id, "final_job_status": final_status,
        "score_count": 0, "issue_count": 0,
        "error": error,
    }


def _persist_run(cfg, payload: dict) -> None:
    cfg.run_json_path().write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _persist_config(cfg) -> None:
    cfg.config_path().write_text(
        json.dumps(dataclasses.asdict(cfg), default=str, indent=2),
        encoding="utf-8",
    )


def _emit_failure(cfg, *, error: str, execution_status: str,
                   evaluation_status: str, research_outcome: str,
                   final_status: str, job_id: str | None,
                   trajectory: list[dict] | None = None) -> int:
    manifest = _build_manifest(
        cfg, final_status=final_status, job_id=job_id, error=error,
        execution_status=execution_status,
        evaluation_status=evaluation_status,
        research_outcome=research_outcome,
    )
    issues = [{
        "issue_id": "i_" + cfg.run_id + "_" + execution_status,
        "run_id": cfg.run_id, "category": "infrastructure",
        "severity": "error", "summary": error,
        "evidence_refs": [],
        "reproduction_command": "scripts/benchmark/run.py",
        "expected": "completed",
        "actual": execution_status,
        "root_cause_confidence": 1.0,
        "schema_version": "benchmark/v1",
        "notes": "",
    }]
    # Counts must derive from the actual arrays, never a hard-coded 0.
    manifest["issue_count"] = len(issues)
    manifest["score_count"] = 0
    payload = {
        "manifest": manifest,
        "trajectory": trajectory or [],
        "scores": [],
        "issues": issues,
        "report": None,
    }
    _persist_run(cfg, payload)
    print(json.dumps({"ok": False, "error": error,
                       "execution_status": execution_status,
                       "evaluation_status": evaluation_status},
                      ensure_ascii=False))
    _exit = {"load_failed": 2, "prepare_failed": 3,
             "submit_failed": 4, "timeout": 4}
    return _exit.get(execution_status, 5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--protocol-id", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--benchmark-workspace", required=True)
    ap.add_argument("--model", default="minimax/MiniMax-M3")
    ap.add_argument("--method-arm", default="candidate")
    ap.add_argument("--method-arm-label", default="candidate")
    ap.add_argument("--parent-run-id", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--wall-timeout-seconds", type=int, default=600)
    ap.add_argument("--wait-timeout-seconds", type=int, default=600)
    args = ap.parse_args()
    cfg = BenchmarkRunConfig.from_args(args)
    repo_root = _resolve_repo_root()
    _prepare_isolated_workspace(cfg)
    _bootstrap_benchmark_workspace(cfg)
    _persist_config(cfg)

    try:
        protocol = _load_protocol(args.protocol_id, repo_root=repo_root)
        fixture_mod = _load_fixture(args.fixture, repo_root=repo_root)
    except Exception as exc:
        return _emit_failure(cfg, error="load failed: " + str(exc),
                              execution_status="load_failed",
                              evaluation_status="skipped",
                              research_outcome="no_data",
                              final_status="unknown", job_id=None)

    task = fixture_mod.TASK_DEFINITION

    # Dependency check: inputs must be materialised by prepare.py first.
    # (A missing benchmark_workspace/inputs dir means prepare.py never ran,
    #  so external inputs are absent — refuse fast instead of starting a worker.)
    task_inputs = list(task.get("inputs") or [])
    if task_inputs and not (cfg.benchmark_root / "inputs").exists():
        print(json.dumps({"ok": False,
                          "error": "inputs not materialised; run scripts/benchmark/prepare.py first",
                          "execution_status": "prepare_failed",
                          "evaluation_status": "skipped",
                          "missing": task_inputs}, ensure_ascii=False))
        return 3

    # Phase 1: prepare inputs
    try:
        fixture_mod.prepare_initial_state(cfg.workspace_dir, cfg.run_id)
    except Exception as exc:
        return _emit_failure(cfg,
                              error="prepare_initial_state failed: " + str(exc),
                              execution_status="prepare_failed",
                              evaluation_status="skipped",
                              research_outcome="no_data",
                              final_status="unknown", job_id=None)

    # Phase 2: submit through orchestrator (pre-allocates job_id,
    # links reservation ↔ Job)
    try:
        sub = _submit_job(cfg, task=task, repo_root=repo_root)
    except Exception as exc:
        return _emit_failure(cfg, error="submit failed: " + str(exc),
                              execution_status="submit_failed",
                              evaluation_status="skipped",
                              research_outcome="no_data",
                              final_status="unknown", job_id=None)

    trajectory = [{
        "event": "submission",
        "job_id": sub["job_id"],
        "assigned_instance": sub["assigned_instance"],
        "persona_hint": sub["persona_hint"],
    }]

    # Phase 3: start the controlled worker, then wait strictly for
    # terminal.  queued/running/unknown are NOT success.  Only
    # "completed" maps to evaluation_status.
    worker_proc, worker_logf = _start_benchmark_worker(cfg, repo_root)
    try:
        final_status, final_record = _wait_terminal(cfg=cfg, job_id=sub["job_id"], worker_proc=worker_proc)
    finally:
        try:
            worker_proc.terminate()
            worker_proc.wait(timeout=10)
        except Exception:
            try:
                worker_proc.kill()
            except Exception:
                pass
        try:
            worker_logf.close()
        except Exception:
            pass
    trajectory.append({
        "event": "terminal",
        "job_id": sub["job_id"],
        "status": final_status,
        "finished_at": (final_record or {}).get("finished_at"),
    })

    if final_status == "worker_failed":
        return _emit_failure(
            cfg,
            error="benchmark worker exited early: "
                  + str((final_record or {}).get("error") or ""),
            execution_status="worker_failed",
            evaluation_status="skipped",
            research_outcome="no_data",
            final_status="worker_failed",
            job_id=sub["job_id"],
            trajectory=trajectory,
        )

    if final_status == "worker_failed":
        return _emit_failure(
            cfg,
            error="benchmark worker exited early: "
                  + str((final_record or {}).get("error") or ""),
            execution_status="worker_failed",
            evaluation_status="skipped",
            research_outcome="no_data",
            final_status="worker_failed",
            job_id=sub["job_id"],
            trajectory=trajectory,
        )

    if final_status == "unknown":
        # wall-timeout — request cancel and record timeout
        _cancel_job(cfg=cfg, job_id=sub["job_id"])
        return _emit_failure(cfg, error="wall timeout",
                              execution_status="timeout",
                              evaluation_status="skipped",
                              research_outcome="no_data",
                              final_status="unknown",
                              job_id=sub["job_id"],
                              trajectory=trajectory)

    if final_status in _TERMINAL_FAIL:
        # The Job reached a terminal failure (e.g. the independent message
        # review rejected the report).  The Agent may still have produced
        # correct outputs — score them independently so a report-quality
        # failure is not conflated with a task-execution failure, and only
        # real metrics enter aggregation.
        try:
            scores, issues = _run_reference_and_score(cfg, fixture_mod, protocol)
        except Exception as exc:
            scores, issues = [], [{
                "issue_id": "i_" + cfg.run_id + "_score_after_fail",
                "run_id": cfg.run_id, "category": "evaluator",
                "severity": "error", "summary": "scorer after job fail: " + str(exc),
                "evidence_refs": [], "reproduction_command": "scripts/benchmark/run.py",
                "expected": "score", "actual": str(exc),
                "root_cause_confidence": 1.0, "schema_version": "benchmark/v1", "notes": "",
            }]
        has_score = bool(scores) and scores[0].get("value") is not None
        if has_score:
            manifest = _build_manifest(
                cfg, final_status=final_status, job_id=sub["job_id"],
                execution_status="job_" + str(final_status),
                evaluation_status="completed",
                research_outcome="completed_with_report_failure",
            )
            issues.append({
                "issue_id": "i_" + cfg.run_id + "_report_review",
                "run_id": cfg.run_id, "category": "candidate",
                "severity": "warning",
                "summary": "job reached " + str(final_status) + " (report review failed) but artefacts scored",
                "evidence_refs": [], "reproduction_command": "scripts/benchmark/run.py",
                "expected": "completed", "actual": final_status,
                "root_cause_confidence": 0.9, "schema_version": "benchmark/v1", "notes": "",
            })
            manifest["score_count"] = len(scores)
            manifest["issue_count"] = len(issues)
            payload = {"manifest": manifest, "trajectory": trajectory,
                       "scores": scores, "issues": issues, "report": None}
            _persist_run(cfg, payload)
            print(json.dumps({"ok": True, "run_id": cfg.run_id,
                              "execution_status": "job_" + str(final_status),
                              "evaluation_status": "completed",
                              "score_count": len(scores), "issue_count": len(issues),
                              "out_dir": str(cfg.run_dir())}, ensure_ascii=False))
            return 5
        return _emit_failure(
            cfg,
            error="job did not complete: " + str(final_status),
            execution_status="job_" + str(final_status),
            evaluation_status="skipped",
            research_outcome="no_data",
            final_status=final_status,
            job_id=sub["job_id"],
            trajectory=trajectory,
        )

    # Phase 4: reference oracle + score
    scores, issues = _run_reference_and_score(cfg, fixture_mod, protocol)

    has_score = bool(scores) and scores[0].get("value") is not None
    evaluation_status = "completed" if has_score else "no_match"
    research_outcome = ("completed" if has_score else "no_data")
    manifest = _build_manifest(
        cfg, final_status=final_status, job_id=sub["job_id"],
        execution_status="completed",
        evaluation_status=evaluation_status,
        research_outcome=research_outcome,
    )
    manifest["score_count"] = len(scores)
    manifest["issue_count"] = len(issues)
    payload = {
        "manifest": manifest,
        "trajectory": trajectory,
        "scores": scores,
        "issues": issues,
        "report": None,
    }
    _persist_run(cfg, payload)
    print(json.dumps({
        "ok": True, "run_id": cfg.run_id,
        "execution_status": "completed",
        "evaluation_status": evaluation_status,
        "score_count": len(scores), "issue_count": len(issues),
        "out_dir": str(cfg.run_dir()),
    }, ensure_ascii=False))
    return 0 if has_score else 5


if __name__ == "__main__":
    raise SystemExit(main())
