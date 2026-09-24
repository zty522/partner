"""Public wrapper for launching a complete benchmark Event Flow."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import asyncio
import json

from partner.application import PartnerApplicationService


@dataclass(frozen=True)
class BenchmarkSubmission:
    accepted: bool
    job_id: str
    benchmark_run_id: str
    protocol_id: str
    flow_type: str
    message: str


class PartnerBenchmarkWrapper:
    """Trigger Partner through its public application contract and observe it.

    This class never invokes a subject Event handler. ``wait`` may drive one
    bounded worker for local CLI use; production workers consume the same Job.
    """

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).expanduser().resolve()
        self.service = PartnerApplicationService(self.workspace)

    def submit(self, *, protocol_id: str, request: str, instance_id: str,
               project_id: str, inputs: Mapping[str, Any],
               guardrail_results: Mapping[str, Any] | None = None,
               allow_external_judges: bool = False, channel: str = "local",
               sender_id: str = "benchmark-wrapper") -> BenchmarkSubmission:
        result = self.service.submit(
            request, channel=channel, sender_id=sender_id, persona_hint=instance_id,
            project_id=project_id, report_policy="none", mode="benchmark",
            execution_constraints={
                "benchmark_protocol_id": protocol_id,
                "benchmark_inputs": dict(inputs),
                "benchmark_guardrail_results": dict(guardrail_results or {}),
                "benchmark_allow_external_judges": bool(allow_external_judges),
                "checkpoint_policy": "protocol",
            })
        run_id = ""
        if result.job_id:
            row = self.status(result.job_id) or {}
            run_id = str(row.get("benchmark_run_id") or "")
        return BenchmarkSubmission(
            accepted=result.accepted, job_id=result.job_id,
            benchmark_run_id=run_id, protocol_id=protocol_id,
            flow_type=result.route, message=result.message)

    def status(self, job_id: str) -> dict[str, Any] | None:
        return next((row for row in self.service.list_jobs(limit=500)
                     if row.get("job_id") == job_id), None)

    def wait(self, submission: BenchmarkSubmission) -> dict[str, Any]:
        if not submission.accepted or not submission.job_id:
            raise ValueError("cannot wait for a rejected benchmark submission")
        from partner.runtime.event_worker import EventWorker
        row = self.status(submission.job_id) or {}
        worker = EventWorker(self.workspace, str(row.get("assigned_instance") or ""),
                             root_job_id=submission.job_id)
        job = worker.next_job()
        if job is None:
            terminal = self.status(submission.job_id)
            if terminal and terminal.get("status") in {"completed", "failed", "cancelled"}:
                return terminal
            raise RuntimeError("benchmark job is not runnable")
        try:
            asyncio.run(worker._run_job_to_terminal(job))
        finally:
            worker._release_claim()
        return self.status(submission.job_id) or {}

    def result(self, benchmark_run_id: str) -> dict[str, Any]:
        directory = self.workspace / "state/benchmarks/runs" / benchmark_run_id
        def read(name: str):
            path = directory / name
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return {"run_id": benchmark_run_id, "directory": str(directory),
                "manifest": read("manifest.json"), "settlement": read("settlement.json"),
                "report": read("report.json")}


__all__ = ["BenchmarkSubmission", "PartnerBenchmarkWrapper"]

