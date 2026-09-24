"""Multi-task, multi-seed orchestration over benchmark Event Flows."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Iterable
import json
import math
import random
import uuid

from .wrapper import PartnerBenchmarkWrapper


@dataclass(frozen=True)
class BenchmarkSuiteCase:
    task_id: str
    declared_feature: str
    seed: int


def render_suite_artifacts(directory: str | Path, result: dict[str, Any]) -> dict[str, str]:
    """Render a deterministic effect figure and human-readable suite report."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    tasks = list(result.get("by_task") or {})
    means = [float((result["by_task"][task] or {}).get("mean_effect") or 0.0) for task in tasks]
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    positions = list(range(len(tasks)))
    ax.bar(positions, means, color="#2878B5", alpha=.82, label="task mean")
    for x, task in enumerate(tasks):
        values = list((result["by_task"][task] or {}).get("effects") or [])
        offsets = [(-.08 + .16 * i / max(1, len(values)-1)) for i in range(len(values))]
        ax.scatter([x + o for o in offsets], values, color="#D95319", zorder=3,
                   label="seed effect" if x == 0 else None)
    ax.axhline(.03, color="#7E2F8E", linestyle="--", linewidth=1.5,
               label="predeclared minimum 0.03")
    overall = result.get("mean_effect")
    ci = result.get("across_run_bootstrap_ci") or []
    if isinstance(overall, (int, float)) and len(ci) == 2:
        ax.axhspan(float(ci[0]), float(ci[1]), color="#77AC30", alpha=.15,
                   label="across-run 95% CI")
        ax.axhline(float(overall), color="#77AC30", linewidth=1.5, label="overall mean")
    ax.set_xticks(positions, tasks, rotation=12, ha="right")
    ax.set_ylabel("RMSE improvement (baseline - candidate)")
    ax.set_title("Partner Core v1 Davis target-feature benchmark")
    ax.grid(axis="y", alpha=.2)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    figure = target / "suite_effects.png"
    fig.savefig(figure, dpi=180)
    plt.close(fig)
    report = target / "suite_report.md"
    lines = ["# Core v1 benchmark suite", "",
             f"- Suite: `{result.get('suite_id')}`",
             f"- Valid runs: `{result.get('completed_valid_runs')}/{result.get('started_matrix_size')}`",
             f"- Confirmed runs: `{result.get('confirmed_runs')}`",
             f"- Mean RMSE improvement: `{result.get('mean_effect')}`",
             f"- Across-run bootstrap 95% CI: `{result.get('across_run_bootstrap_ci')}`",
             "", "![Suite effects](suite_effects.png)", "", "## Per task", ""]
    for task in tasks:
        row = result["by_task"][task]
        lines.append(f"- `{task}`: mean `{row.get('mean_effect')}`, effects `{row.get('effects')}`")
    lines.extend(["", "完整逐运行证据见 `suite_result.json` 与各 task/seed JSON。", ""])
    report.write_text("\n".join(lines), encoding="utf-8")
    return {"figure": str(figure), "report": str(report)}


class PartnerBenchmarkSuite:
    """Run a frozen matrix; every cell is a full benchmark Event Flow."""

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).expanduser().resolve()
        self.wrapper = PartnerBenchmarkWrapper(self.workspace)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return dict(value) if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _bootstrap(values: list[float], seed: int = 20260924) -> list[float] | None:
        if not values:
            return None
        rng = random.Random(seed)
        samples = sorted(mean(values[rng.randrange(len(values))] for _ in values)
                         for _ in range(2000))
        return [samples[int(.025 * len(samples))], samples[int(.975 * len(samples)) - 1]]

    def run(self, *, protocol_id: str, instance_id: str, project_id: str,
            dataset_path: str | Path, arm_runner_path: str | Path,
            cases: Iterable[BenchmarkSuiteCase], allow_external_judges: bool = False,
            existing_runs: dict[tuple[str, int], dict[str, str]] | None = None) -> dict[str, Any]:
        frozen = tuple(cases)
        if len({c.task_id for c in frozen}) < 2 or len({c.seed for c in frozen}) < 2:
            raise ValueError("a full suite requires at least two tasks and two seeds")
        suite_id = "suite_" + uuid.uuid4().hex[:16]
        directory = self.workspace / "state/benchmarks/suites" / suite_id
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "matrix.json").write_text(json.dumps({
            "suite_id": suite_id, "protocol_id": protocol_id,
            "cases": [asdict(c) for c in frozen],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rows: list[dict[str, Any]] = []
        for case in frozen:
            reused = (existing_runs or {}).get((case.task_id, case.seed))
            if reused:
                job_id, run_id = str(reused.get("job_id") or ""), str(reused.get("run_id") or "")
                terminal = self.wrapper.status(job_id) or {}
                if terminal.get("status") != "completed" or not run_id:
                    raise ValueError(f"reused run is not completed: {case.task_id}/{case.seed}")
            else:
                submitted = self.wrapper.submit(
                    protocol_id=protocol_id,
                    request=(f"/benchmark {protocol_id}\n冻结套件 {suite_id}；"
                             f"task={case.task_id} seed={case.seed}"),
                    instance_id=instance_id, project_id=project_id,
                    inputs={"dataset_path": str(Path(dataset_path).resolve()),
                            "declared_feature": case.declared_feature,
                            "arm_runner_path": str(Path(arm_runner_path).resolve()),
                            "benchmark_seed": case.seed, "task_id": case.task_id},
                    allow_external_judges=allow_external_judges)
                terminal = self.wrapper.wait(submitted)
                job_id, run_id = submitted.job_id, submitted.benchmark_run_id
            run_dir = self.workspace / "state/benchmarks/runs" / run_id
            comparison = self._read(run_dir / "comparison.json")
            settlement = self._read(run_dir / "settlement.json")
            guardrails = self._read(run_dir / "guardrails.json")
            row = {**asdict(case), "job_id": job_id,
                   "benchmark_run_id": run_id, "reused": bool(reused),
                   "job_status": terminal.get("status"),
                   "decision": settlement.get("decision"),
                   "effect": comparison.get("effect"),
                   "within_run_ci": comparison.get("confidence_interval"),
                   "hard_guardrails_pass": guardrails.get("hard_pass"),
                   "run_directory": str(run_dir)}
            rows.append(row)
            (directory / f"{case.task_id}__seed_{case.seed}.json").write_text(
                json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        effects = [float(r["effect"]) for r in rows
                   if isinstance(r.get("effect"), (int, float)) and math.isfinite(float(r["effect"]))]
        complete = [r for r in rows if r["job_status"] == "completed"
                    and r["hard_guardrails_pass"] is True]
        by_task = {}
        for task in sorted({c.task_id for c in frozen}):
            values = [float(r["effect"]) for r in rows if r["task_id"] == task
                      and isinstance(r.get("effect"), (int, float))]
            by_task[task] = {"runs": len(values), "mean_effect": mean(values) if values else None,
                             "effects": values}
        result = {
            "schema_version": 1, "suite_id": suite_id, "protocol_id": protocol_id,
            "started_matrix_size": len(frozen), "completed_valid_runs": len(complete),
            "all_runs_valid": len(complete) == len(frozen),
            "confirmed_runs": sum(r.get("decision") == "confirmed" for r in rows),
            "mean_effect": mean(effects) if effects else None,
            "effect_std": pstdev(effects) if len(effects) > 1 else 0.0 if effects else None,
            "across_run_bootstrap_ci": self._bootstrap(effects),
            "by_task": by_task, "runs": rows,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        result["artifacts"] = render_suite_artifacts(directory, result)
        (directory / "suite_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {**result, "directory": str(directory)}


__all__ = ["BenchmarkSuiteCase", "PartnerBenchmarkSuite", "render_suite_artifacts"]
