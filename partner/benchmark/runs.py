"""In-memory benchmark run manager (M3 / Section 6.3).

Static-implemented. The class keeps an append-only list of typed records
so that the ``scripts/benchmark/run.py`` script can be exercised without
touching a real workspace. It does *not* execute any agent, does *not*
read files, and does *not* call any LLM. Real runner work is intentionally
out of scope for tonight.

Runners in the verification pass must:

* use ``scripts/benchmark/run.py`` as the entry point;
* pass an explicit ``workspace`` distinct from production;
* bind ``code_sha`` to a git commit hash so reverting is auditable;
* record every external action under ``actions.jsonl`` with its target URL.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .schemas import (
    Score, Issue, Report, RunManifest, Trajectory, EvolutionAttempt,
)


class BenchmarkRun:
    """A typed scratchpad that one script invocation can fill in."""

    def __init__(self, *, run_id: str, protocol_id: str, code_sha: str,
                 model: str, parent_run_id: str | None = None,
                 random_seed: int = 0,
                 budget: dict[str, Any] | None = None) -> None:
        self.manifest = RunManifest(
            run_id=run_id,
            protocol_id=protocol_id,
            parent_run_id=parent_run_id,
            code_sha=code_sha,
            code_dirty=False,
            model=model,
            tool_permissions=["fs.read", "fs.write"],
            budget=budget or {"wallclock_seconds": 3600, "llm_calls": 200},
            random_seed=random_seed,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            state="draft",
        )
        self.trajectories: list[Trajectory] = []
        self.attempts: list[EvolutionAttempt] = []
        self.scores: list[Score] = []
        self.issues: list[Issue] = []
        self.report: Report | None = None

    # ----- append-only mutators --------------------------------------------

    def start(self) -> None:
        self._set_state("running")

    def finish(self) -> None:
        self._set_state("completed")

    def fail(self) -> None:
        self._set_state("failed")

    def cancel(self) -> None:
        self._set_state("cancelled")

    def _set_state(self, state: str) -> None:
        from dataclasses import replace
        self.manifest = replace(self.manifest, state=state)

    def add_trajectory(self, *, run_id: str | None = None, state: str = "running",
                       events: list[dict[str, Any]] | None = None,
                       notes: str = "") -> Trajectory:
        traj = Trajectory(
            trajectory_id="traj_" + uuid.uuid4().hex[:12],
            run_id=run_id or self.manifest.run_id,
            state=state,
            events=list(events or []),
            notes=notes,
        )
        self.trajectories.append(traj)
        return traj

    def add_attempt(self, *, parent_attempt_id: str | None, decision: str,
                    evidence_refs: list[str], hypothesis: str,
                    expected_effect_size: float, observed_effect_size: float,
                    notes: str = "") -> EvolutionAttempt:
        attempt = EvolutionAttempt(
            attempt_id="att_" + uuid.uuid4().hex[:12],
            run_id=self.manifest.run_id,
            parent_attempt_id=parent_attempt_id,
            decision=decision,
            evidence_refs=list(evidence_refs),
            hypothesis=hypothesis,
            expected_effect_size=float(expected_effect_size),
            observed_effect_size=float(observed_effect_size),
            notes=notes,
        )
        self.attempts.append(attempt)
        return attempt

    def add_score(self, *, metric_name: str, direction: str, value: float,
                  confidence_interval: tuple[float, float] | None = None,
                  missing_reason: str | None = None,
                  notes: str = "") -> Score:
        score = Score(
            score_id="score_" + uuid.uuid4().hex[:12],
            run_id=self.manifest.run_id,
            metric_name=metric_name,
            direction=direction,
            value=float(value),
            confidence_interval=confidence_interval,
            missing_reason=missing_reason,
            notes=notes,
        )
        self.scores.append(score)
        return score

    def add_issue(self, *, category: str, severity: str, summary: str,
                  evidence_refs: list[str], reproduction_command: str,
                  expected: str, actual: str,
                  root_cause_confidence: float = 0.0,
                  notes: str = "") -> Issue:
        issue = Issue(
            issue_id="iss_" + uuid.uuid4().hex[:12],
            run_id=self.manifest.run_id,
            category=category,
            severity=severity,
            summary=summary,
            evidence_refs=list(evidence_refs),
            reproduction_command=reproduction_command,
            expected=expected,
            actual=actual,
            root_cause_confidence=float(root_cause_confidence),
            notes=notes,
        )
        self.issues.append(issue)
        return issue

    def set_report(self, *, title: str, sections: list[str],
                   metric_table: list[dict[str, Any]],
                   paper_figure_refs: list[str],
                   missing_or_incomparable: list[str],
                   notes: str = "") -> Report:
        self.report = Report(
            report_id="rep_" + uuid.uuid4().hex[:12],
            run_id=self.manifest.run_id,
            title=title,
            sections=list(sections),
            metric_table=list(metric_table),
            paper_figure_refs=list(paper_figure_refs),
            missing_or_incomparable=list(missing_or_incomparable),
            notes=notes,
        )
        return self.report

    # ----- persistence helpers (not invoked by static-only mode) ----------

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": asdict(self.manifest),
            "trajectories": [asdict(t) for t in self.trajectories],
            "attempts": [asdict(a) for a in self.attempts],
            "scores": [asdict(s) for s in self.scores],
            "issues": [asdict(i) for i in self.issues],
            "report": asdict(self.report) if self.report else None,
        }

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )


__all__ = ["BenchmarkRun"]
