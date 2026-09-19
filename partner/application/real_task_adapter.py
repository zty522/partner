"""A real bounded task for the commitment kernel: fix a real failing test.

The bounded action is a real repository task, not a metric toy:

* the input is a real task definition on disk (``benchmarks/real_tasks/<id>/task.json``)
  that names a real failing test, the real module the candidate may change, and the
  real patch the candidate declares;
* the measurement is the project's own test runner.  ``pytest`` decides how many tests
  pass -- the kernel does not trust a number the action reports about itself;
* the baseline is a *real execution* of the unmodified module (project history state)
  through the same executor and the same evaluator;
* nothing is written into the real repository.  Both arms run against a sandbox overlay
  that shadows the target module through ``PYTHONPATH``.

Scoping: the environment is ``isolated_sample``, so a result can never be published.  A
supported settlement means "the declared patch made the declared test target pass where
the unmodified module did not", which is evidence about that patch, not about the repo.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from partner.commitment import models as M
from partner.commitment.evidence import build_baseline_evidence, environment_fingerprint
from partner.commitment.evaluator import ArtifactIsolation, JsonMetricEvaluator
from partner.commitment.ports import SystemClock
from partner.commitment.proposer import DeterministicProposer
from partner.commitment.selector import GuardedGainSelector
from partner.commitment.store import CommitmentStore, file_sha256
from partner.commitment.freezer import Freezer
from partner.commitment.runner import BetRunner

DOMAIN_VERSION = "real-task-pytest/1"
EXECUTOR_ID = "real-task-pytest-executor"
EXECUTOR_VERSION = "1.0.0"
EVALUATOR_ID = "real-task-pytest-evaluator"
EVALUATOR_VERSION = "1.0.0"
ARTIFACT_KEY = "test_run.json"

TEST_RE = re.compile(r"runtime_real_task[A-Za-z0-9_\-]*")
SUMMARY_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")

#: Where task definitions live, relative to the project root.
TASKS_DIRNAME = ("benchmarks", "real_tasks")


class RealTaskError(RuntimeError):
    """The task could not be carried out as declared."""


def load_task(project_root: str | os.PathLike, task_id: str) -> dict[str, Any]:
    path = Path(project_root).joinpath(*TASKS_DIRNAME, task_id, "task.json")
    if not path.is_file():
        raise RealTaskError(f"no task definition at {path}")
    task = json.loads(path.read_text(encoding="utf-8"))
    for key in ("task_id", "repo_root", "target_module", "test_target"):
        if not str(task.get(key) or "").strip():
            raise RealTaskError(f"task {task_id} is missing {key}")
    return task


def run_test_target(task: Mapping[str, Any], *, overlay: Path | None,
                    sandbox: Path) -> dict[str, Any]:
    """Run the declared test target once, against repo state plus an optional overlay.

    Real measurement: the counts come from the test runner's own summary line, and the
    exit code is kept alongside them.
    """
    repo_root = Path(overlay).resolve() if overlay else Path(str(task["repo_root"]))
    raw_target = task["test_target"]
    target_argv = ([str(x) for x in raw_target] if isinstance(raw_target, (list, tuple))
                   else [str(raw_target)])
    test_target = " ".join(target_argv)
    timeout = float(task.get("timeout_seconds") or 180)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(repo_root)
    started = time.time()
    try:
        completed = subprocess.run(
            ["python3", "-m", "pytest", *target_argv, "-q", "-p", "no:cacheprovider", "--tb=no"],
            cwd=str(repo_root), env=env, capture_output=True, text=True, errors="replace",
            timeout=timeout)
        stdout, code = completed.stdout + completed.stderr, int(completed.returncode)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") + (exc.stderr or "")
        code, timed_out = -1, True
    except Exception as exc:  # noqa: BLE001
        stdout, code, timed_out = f"{type(exc).__name__}: {exc}", -2, False
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", "replace")
    counts = {name: 0 for name in ("passed", "failed", "errors", "skipped")}
    for value, name in SUMMARY_RE.findall(stdout):
        key = "errors" if name.startswith("error") else name
        counts[key] += int(value)
    log = sandbox / ("pytest_stdout.log")
    log.write_text(stdout, encoding="utf-8")
    return {
        "exit_code": code, "timed_out": timed_out,
        "tests_passed": float(counts["passed"]), "tests_failed": float(counts["failed"]),
        "tests_errors": float(counts["errors"]), "tests_skipped": float(counts["skipped"]),
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stdout_ref": str(log), "duration_seconds": round(time.time() - started, 3),
        "test_target": test_target, "repo_root": str(repo_root),
    }


def stage_shadow_repo(repo_root: str | os.PathLike, sandbox: Path, target_rel: str,
                     source_text: str) -> Path:
    """Expose the whole repo through symlinks, with exactly one real file replaced.

    A ``PYTHONPATH`` overlay is not enough: pytest inserts the repository root ahead of
    it, so ``import partner...`` would still resolve to the unmodified module and the
    candidate arm would silently measure the control.  Here every entry is a symlink
    into the real repository except the declared target file, which is a real file
    holding the arm's content.  The real repository is never written to.

    Note the last step: the *siblings inside the target's own directory* must be linked
    too.  An earlier version of this function linked only the ancestors, so a test that
    imported any other module from the same package failed with ``ModuleNotFoundError``
    -- which showed up as a failure that had nothing to do with the candidate.
    """
    repo_root = Path(repo_root).resolve()
    shadow = sandbox / "repo"
    shutil.rmtree(shadow, ignore_errors=True)
    shadow.mkdir(parents=True, exist_ok=True)
    parts = Path(target_rel).parts
    real_dir = repo_root
    for index, part in enumerate(parts[:-1]):
        shadow_dir = shadow.joinpath(*parts[:index])
        for child in real_dir.iterdir():
            if child.name in {".git"} or child.name.startswith("__pycache__"):
                continue
            destination = shadow_dir / child.name
            if not destination.exists():
                os.symlink(child, destination)
        # descend one level for real: the component on the path becomes a real directory
        # so the single replaced file has somewhere real to live
        real_dir = real_dir / part
        shadow_component = shadow_dir / part
        if shadow_component.is_symlink():
            shadow_component.unlink()
        shadow_component.mkdir(parents=True, exist_ok=True)
    target_dir = shadow.joinpath(*parts[:-1])
    target_name = parts[-1]
    for child in real_dir.iterdir():
        if child.name in {".git"} or child.name.startswith("__pycache__"):
            continue
        if child.name == target_name:
            continue
        destination = target_dir / child.name
        if not destination.exists():
            os.symlink(child, destination)
    target = shadow.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source_text, encoding="utf-8")
    return shadow


@dataclass
class RealTaskExecutor:
    """Applies the candidate's declared patch to a sandbox copy and runs the test."""

    executor_id: str = EXECUTOR_ID
    executor_version: str = EXECUTOR_VERSION

    def _sandbox(self, workspace: str, bet_id: str, arm: str) -> Path:
        root = Path(workspace) / "state" / "commitment_execution" / bet_id / arm
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def execute(self, *, bet: M.BetRecord, candidate: M.Candidate,
                workspace: str) -> M.ExecutionReceipt:
        started = time.time()
        params = dict(candidate.params or {})
        arm = str(params.get("arm") or ("candidate" if params.get("patch_file") else "control"))
        sandbox = self._sandbox(workspace, bet.bet_id, arm)
        artifact = sandbox / ARTIFACT_KEY
        try:
            # two distinct roots: the task definition + its patch live next to the
            # project that declares them, while the test itself runs in the real project
            task_root = Path(str(params.get("project_root") or workspace))
            task = load_task(task_root, str(params["task_id"]))
            repo_root = Path(str(task["repo_root"]))
            target_rel = str(task["target_module"])
            module_src = repo_root / target_rel
            if not module_src.is_file():
                raise RealTaskError(f"target module not found: {module_src}")
            patch_bytes = 0
            repeats: list[dict[str, Any]] = []
            if arm == "candidate":
                patch_file = str(params.get("patch_file") or "")
                if not patch_file:
                    raise RealTaskError("candidate declares no patch_file")
                patch_path = task_root.joinpath(*TASKS_DIRNAME, str(task["task_id"]), patch_file)
                if not patch_path.is_file():
                    raise RealTaskError(f"patch file not found: {patch_path}")
                patch_text = patch_path.read_text(encoding="utf-8")
                patch_bytes = len(patch_text.encode("utf-8"))
                source = module_src.read_text(encoding="utf-8")
                arm_source = apply_patch_text(source, patch_text)
            else:
                # the control arm is the project's own history state, verbatim
                arm_source = module_src.read_text(encoding="utf-8")
            shadow = stage_shadow_repo(repo_root, sandbox, target_rel, arm_source)
            patched = shadow.joinpath(*Path(target_rel).parts)
            if arm == "candidate":
                # An adjusted prior may ask for one more repetition.  It is really
                # executed here: N independent sandbox runs, each recorded in the
                # artifact, so declaring replicates=2 is never an overclaim.
                replicates = max(1, int(params.get("replicates") or 1))
                repeats = []
                for index in range(1, replicates):
                    repeat_sandbox = self._sandbox(workspace, bet.bet_id,
                                                   f"{arm}_r{index + 1}")
                    repeat_shadow = stage_shadow_repo(repo_root, repeat_sandbox, target_rel,
                                                      arm_source)
                    repeats.append({"replicate": index + 1,
                                    **run_test_target(task, overlay=repeat_shadow,
                                                      sandbox=repeat_sandbox)})
            # both arms run in a shadow repo through the same mechanism, so the only
            # difference between them is that one file's content
            run = run_test_target(task, overlay=shadow, sandbox=sandbox)
            payload = {"domain_version": DOMAIN_VERSION, "arm": arm,
                       "attempted_replicates": max(1, int(params.get("replicates") or 1)),
                       "replicates_executed": 1 + len(repeats),
                       "replicate_results": [
                           {"replicate": r["replicate"], "tests_passed": r["tests_passed"],
                            "tests_failed": r["tests_failed"], "exit_code": r["exit_code"],
                            "module_sha256": None, "stdout_sha256": r["stdout_sha256"]}
                           for r in (repeats)],
                       "replicates_agreed": all(
                           (r["tests_passed"], r["tests_failed"]) == (run["tests_passed"],
                                                                      run["tests_failed"])
                           for r in (repeats)),
                       "task_id": str(task["task_id"]), "module": target_rel,
                       "module_sha256": file_sha256(patched), "patch_bytes": patch_bytes,
                       "executed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **run}
            artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 -- a real failure is reported, not hidden
            (sandbox / "execution.error").write_text(f"{type(exc).__name__}: {exc}\n",
                                                     encoding="utf-8")
            return M.ExecutionReceipt(
                receipt_id=f"rcpt_{bet.bet_id}_{arm}_failed", bet_id=bet.bet_id,
                requested_action=bet.selected_action, executed_action=bet.selected_action,
                executor_id=self.executor_id, executor_version=self.executor_version,
                started_epoch=started, finished_epoch=time.time(), status="failed", exit_code=2,
                artifacts=(), artifact_hashes={}, log_ref=str(sandbox / "execution.error"),
                data_hash="", budget_consumed={"actions": 1}, human_intervention=False,
                idempotency_key=f"real-task:{bet.bet_id}:r{bet.revision}:{arm}",
                failure_reason=f"{type(exc).__name__}: {exc}")
        finished = time.time()
        return M.ExecutionReceipt(
            receipt_id=f"rcpt_{bet.bet_id}_{arm}_r{bet.revision}", bet_id=bet.bet_id,
            requested_action=bet.selected_action, executed_action=bet.selected_action,
            executor_id=self.executor_id, executor_version=self.executor_version,
            started_epoch=started, finished_epoch=finished, status="completed", exit_code=0,
            artifacts=(str(artifact),), artifact_hashes={str(artifact): file_sha256(artifact)},
            log_ref=str(sandbox / "pytest_stdout.log"), data_hash=file_sha256(artifact),
            budget_consumed={"actions": 1, "wall_clock_seconds": round(finished - started, 3),
                             "tool_calls": 1},
            human_intervention=False,
            idempotency_key=f"real-task:{bet.bet_id}:r{bet.revision}:{arm}")


def apply_patch_text(source: str, patch_text: str) -> str:
    """Apply a declared replacement.

    The patch file is either a full replacement (``<<<REPLACE``) or a list of
    ``<<<FIND`` / ``>>>REPL`` blocks.  Anything else is refused rather than guessed at,
    because a silently mis-applied patch would be measured as if it were the candidate.
    """
    if patch_text.startswith("<<<REPLACE"):
        return patch_text.split("\n", 1)[1] if "\n" in patch_text else ""
    if "<<<FIND" not in patch_text:
        raise RealTaskError("patch file is neither a full replacement nor FIND/REPL blocks")
    out = source
    for block in patch_text.strip().split("<<<FIND")[1:]:
        head, _, rest = block.partition(">>>REPL")
        find = head.strip("\n")
        repl = rest.strip("\n")
        if find not in out:
            raise RealTaskError(f"patch FIND block does not match the module:\n{find[:120]}")
        if out.count(find) != 1:
            raise RealTaskError("patch FIND block is ambiguous (matches more than once)")
        out = out.replace(find, repl)
    return out


def _metric(name: str):
    def extract(payload: Mapping[str, Any]) -> float:
        value = payload.get(name)
        if value is None:
            raise ValueError(f"{name} missing from the test artifact")
        return float(value)
    extract.__name__ = f"_{name}"
    return extract


def real_task_evaluator(*, allowed_roots: Sequence[str | os.PathLike] = ()) -> JsonMetricEvaluator:
    return JsonMetricEvaluator(
        evaluator_id=EVALUATOR_ID, evaluator_version=EVALUATOR_VERSION,
        artifact_key=ARTIFACT_KEY,
        extractors={"tests_passed": _metric("tests_passed"),
                    "tests_failed": _metric("tests_failed"),
                    "tests_errors": _metric("tests_errors"),
                    "patch_bytes": _metric("patch_bytes")},
        direction_by_metric={"tests_passed": "increase", "tests_failed": "increase",
                             "tests_errors": "increase", "patch_bytes": "increase"},
        unit_by_metric={"tests_passed": "tests", "tests_failed": "tests",
                        "tests_errors": "errors", "patch_bytes": "bytes"},
        isolation=(ArtifactIsolation(allowed_roots=allowed_roots) if allowed_roots
                   else ArtifactIsolation()))


@dataclass
class RealTaskBaselineProvider:
    executor: RealTaskExecutor
    evaluator: JsonMetricEvaluator
    environment: str
    environment_fingerprint_value: str
    harness_version: str

    def provide(self, *, bet: M.BetRecord, store: CommitmentStore):
        import dataclasses
        params = dict((bet.candidates[0].params if bet.candidates else {}) or {})
        control = M.Candidate(candidate_id="control_unmodified",
                              description="the unmodified module (project history state)",
                              params={**params, "arm": "control", "patch_file": ""},
                              proposed_by="policy",
                              rationale="the real baseline arm: the module as it stands today")
        exec_bet = dataclasses.replace(
            bet, bet_id=f"{bet.bet_id}__baseline",
            context_snapshot_ref=str(store.path("context", "snapshot.json")))
        receipt = self.executor.execute(bet=exec_bet, candidate=control,
                                        workspace=str(store.workspace))
        store.save_receipt(receipt)
        measured = self.evaluator.measure(bet=exec_bet, receipt=receipt)
        for measurement in measured.measurements:
            store.save_measurement(measurement)
        primary = measured.primary(bet.expected_effects[0].metric)
        values = {k: float(v) for k, v in measured.metric_values.items() if v is not None}
        return build_baseline_evidence(
            baseline_id=f"base_{bet.bet_id}", receipt=receipt, measurement=primary,
            metric_values=values, bet=bet, input_hash=bet.context_snapshot_hash,
            data_hash=receipt.data_hash, treatment="control_unmodified",
            environment=self.environment,
            environment_fingerprint_value=self.environment_fingerprint_value,
            harness_version=self.harness_version, store=store)


def real_task_spec(*, job_id: str, trace_token: str, task_id: str, project_root: str,
                   patch_file: str, task: Mapping[str, Any], instance_id: str = "02",
                   project_id: str = "unassigned") -> dict[str, Any]:
    return {
        "partner_id": f"partner-{instance_id or 'unknown'}",
        "project_id": project_id or "unassigned",
        "run_id": f"event_flow_{job_id}",
        "bet_id": f"bet_{job_id}",
        "question": (f"does the declared patch for {task_id} make its test target pass, "
                     f"where the unmodified module does not? [{trace_token}]"),
        "context_snapshot_ref": "context/snapshot.json",
        "baseline_ref": "control:unmodified_module",
        "environment": "isolated_sample",
        "environment_fingerprint": environment_fingerprint(
            name="real-task-pytest",
            dependencies={"task_id": task_id, "test_target": str(task.get("test_target") or ""),
                          "repo": str(task.get("repo_root") or "")},
            harness_version=DOMAIN_VERSION),
        "harness_version": DOMAIN_VERSION,
        "treatment": {"baseline_treatment": "control_unmodified", "declared_paths": [],
                      "allows_code_change": True,
                      "note": "the declared patch to one module is the only difference"},
        "evaluation_protocol": {
            "evaluator_id": EVALUATOR_ID, "evaluator_version": EVALUATOR_VERSION,
            "independence": "artifact_only", "agent_output_visible": False, "replicates": 1,
            "metric_specs": [{"metric": m} for m in
                             ("tests_passed", "tests_failed", "tests_errors", "patch_bytes")],
        },
        # The bar is declared by the task, so a task can honestly be unattainable (the
        # test target has exactly one test and it already passes: no additional passing
        # test exists, and a frozen threshold above 1 can never be reached).
        "expected_effects": [
            {"metric": "tests_passed", "direction": "increase", "unit": "tests",
             "kind": "delta_over_baseline",
             "min_delta": float(task.get("expected_min_delta") or 1.0),
             "threshold": float(task.get("expected_threshold") or 0.0)},
        ],
        "falsification_conditions": [
            {"code": "no_new_pass", "kind": "metric_violation",
             "description": "the test runner passed no additional test with the patch",
             "params": {"metric": "tests_passed", "delta": 1.0}},
            {"code": "missing_measurement", "kind": "missing_evidence",
             "description": "the test runner produced no usable counts",
             "params": {"metric": "tests_passed"}},
        ],
        "budget": {"wall_clock_seconds": 600, "model_calls": 1, "actions": 4, "rounds": 1},
        "commitment_policy": {"earliest_turn_round": 2, "max_turns": 1,
                              "require_new_evidence_to_turn": True,
                              "early_stop_conditions": ["settled", "budget_exhausted", "blocked"]},
        "max_candidates": 1,
        "code_version": "commitment-kernel",
        "data_version": f"task:{task_id}",
        "expectation_note": str(task.get("expectation_note") or ""),
        "model_config_ref": "none",
        "max_risk": 1.0,
        "scope": "event_flow:real_task",
        "trace_token": trace_token,
    }


class _SnapshotFileReader:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read_frozen_state(self) -> Mapping[str, Any]:
        return json.loads(self._path.read_text(encoding="utf-8"))


def write_real_task_snapshot(*, store: CommitmentStore, spec: Mapping[str, Any], job_id: str,
                            trace_token: str, task: Mapping[str, Any], project_root: str,
                            patch_file: str, instance_id: str = "",
                            prior: Mapping[str, Any] | None = None,
                            prior_audit: Mapping[str, Any] | None = None,
                            abstention: Mapping[str, Any] | None = None) -> Path:
    path = store.path("context", "snapshot.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    patch_path = Path(project_root).joinpath(*TASKS_DIRNAME, str(task["task_id"]), patch_file)
    payload = {
        "source": "02_event_flow", "trace_token": trace_token,
        "task_id": str(task["task_id"]), "job_id": job_id, "instance_id": instance_id,
        "project_id": spec["project_id"], "recorded_by": "commitment.bet_record",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "repo_root": str(task["repo_root"]), "target_module": str(task["target_module"]),
        "test_target": str(task["test_target"]),
        "bug_report": str(task.get("bug_report") or ""),
        "patch_file": patch_file,
        "patch_sha256": (file_sha256(patch_path) if patch_path.is_file() else ""),
        "message": f"real task {task['task_id']} via {trace_token}",
        # The prior this bet was frozen with, and the before/after of every decision
        # variable the prior moved.  The snapshot digest is frozen into the bet as
        # context_snapshot_hash, so this audit is bound to the frozen record.
        "prior": dict(prior or {}),
        "prior_adjusted_parameter": dict(prior_audit or {}),
        "abstention": dict(abstention or {}),
        "task_class_key": (prior or {}).get("class_key") or "",
        "candidate_space": [{
            "candidate_id": f"patch_{Path(patch_file).stem}",
            "description": f"apply the declared patch {patch_file}",
            "params": {"task_id": str(task["task_id"]), "patch_file": patch_file,
                       "project_root": str(project_root), "arm": "candidate",
                       "replicates": int((spec.get("evaluation_protocol") or {})
                                         .get("replicates") or 1)},
            "prior": {"expected_gain": 0.0, "risk": 0.0},
            "rationale": "the single declared treatment difference of this bet",
        }],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_real_task_runner(workspace: str | os.PathLike, spec: Mapping[str, Any], *,
                           snapshot_path: Path, clock=None) -> BetRunner:
    from partner.application.commitment_adapter import runner_config_from_spec
    workspace = Path(workspace)
    clock = clock or SystemClock()
    config = runner_config_from_spec(spec, clock=clock)
    store = CommitmentStore(workspace, config.run_id, str(spec["bet_id"]))
    executor = RealTaskExecutor()
    evaluator = real_task_evaluator(
        allowed_roots=[workspace / "state" / "commitment_execution"])
    provider = RealTaskBaselineProvider(
        executor=executor, evaluator=evaluator, environment=str(spec["environment"]),
        environment_fingerprint_value=str(spec["environment_fingerprint"]),
        harness_version=DOMAIN_VERSION)
    return BetRunner(store=store, config=config, clock=clock, proposer=DeterministicProposer(),
                     executor=executor, evaluator=evaluator,
                     snapshot_reader=_SnapshotFileReader(snapshot_path),
                     selector=GuardedGainSelector(max_risk=float(spec.get("max_risk") or 1.0)),
                     freezer=Freezer(), baseline_provider=provider,
                     owner_id=f"event-flow-real-task-{spec['bet_id']}")


__all__ = ["DOMAIN_VERSION", "EXECUTOR_ID", "EVALUATOR_ID", "ARTIFACT_KEY", "TEST_RE",
           "RealTaskError", "load_task", "run_test_target", "apply_patch_text",
           "RealTaskExecutor", "real_task_evaluator", "RealTaskBaselineProvider",
           "real_task_spec", "write_real_task_snapshot", "build_real_task_runner"]
