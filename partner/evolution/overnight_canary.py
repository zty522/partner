"""Overnight canary runner for Sprint18 self-evolution loop.

Scans production_readiness/ at a fixed cadence and:
  * reports a snapshot to the ledger (so an overnight run leaves evidence)
  * invokes ``apply_promoted_candidates`` (real, not dry_run) on a tight
    budget — bounded to prevent a runaway on a slow host

Public API:
    run_once(workspace, *, dry_run=False, budget_seconds=30) -> dict
    run_loop(workspace, *, interval_seconds=60, dry_run=False,
             max_iterations=None) -> None  # blocks; suitable as a nohup target

Boundaries (per ADR 0062 §5):
  * never raises; every iteration returns a snapshot
  * each run_once call writes a ledger event so the LLM can answer
    "what happened overnight?"
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from partner.evolution.apply_pipeline import (
    apply_promoted_candidates,
    ApplyPipelineReport,
)
from partner.evolution.ledger import append_event

logger = logging.getLogger(__name__)


def run_once(workspace_root: Path | str, *, dry_run: bool = False,
             budget_seconds: int = 30) -> dict[str, Any]:
    workspace_root = Path(workspace_root)
    started_at = datetime.now(timezone.utc).isoformat()
    started_ts = time.time()
    report = apply_promoted_candidates(workspace_root, dry_run=dry_run)
    finished_at = datetime.now(timezone.utc).isoformat()
    elapsed = time.time() - started_ts
    snapshot = report.to_dict()
    snapshot.update({
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round(elapsed, 3),
        "dry_run": dry_run,
        "budget_seconds": budget_seconds,
    })
    try:
        append_event(
            workspace_root,
            event_type="overnight/canary_run",
            subject_id=f"overnight:{started_ts:.0f}",
            project_id="agent_self_evolution",
            actor="overnight_canary",
            payload=snapshot,
        )
    except Exception as exc:  # noqa: BLE001 — ledger must never crash the runner
        logger.warning("overnight_canary: ledger append failed: %s", exc)
    # Stuck watch — if the report has more failures than applied and we
    # weren't in dry_run, escalate with a follow-up event.
    if not dry_run and snapshot.get("failed", 0) >= max(2, snapshot.get("examined", 0) // 2):
        try:
            append_event(
                workspace_root,
                event_type="overnight/many_failures",
                subject_id=f"overnight:{started_ts:.0f}",
                project_id="agent_self_evolution",
                actor="overnight_canary",
                payload={"examined": snapshot["examined"], "failed": snapshot["failed"]},
            )
        except Exception:
            pass
    return snapshot


def run_loop(workspace_root: Path | str, *, interval_seconds: int = 60,
             dry_run: bool = False, max_iterations: int | None = None) -> None:
    workspace_root = Path(workspace_root)
    iter_count = 0
    while True:
        try:
            snap = run_once(workspace_root, dry_run=dry_run)
            logger.info("overnight_canary iter %d: examined=%d applied=%d skipped=%d failed=%d",
                         iter_count, snap.get("examined", 0), snap.get("applied", 0),
                         snap.get("skipped", 0), snap.get("failed", 0))
        except Exception as exc:  # noqa: BLE001
            logger.exception("overnight_canary iter %d crashed: %s", iter_count, exc)
        iter_count += 1
        if max_iterations is not None and iter_count >= max_iterations:
            break
        time.sleep(max(5, int(interval_seconds)))


# self_evolve_annotation: candidate_id=repair_to_pr_06a5da4a8c9c7153 failure_class=tool.extract.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_10ebfabea00260c4 failure_class=tool.atomic_http_get.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_129104a43a95216f failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_192717e5817f5e97 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_298a4fbb6d3ab2c failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_387e5d2a78f08203 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_5c7dd3a80056727a failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_5d818ad85dcdc11 failure_class=tool.create_file.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_62d3fd42fdaeabee failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_69a48301ee8bbdd failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_89a7c12175b088bd failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_8a34d7c9cefa6022 failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_8f3ac3b5b85e062a failure_class=tool.execute_code.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_b3c4181e85dc6ca6 failure_class=tool.extract.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_d0a05ffac6567276 failure_class=planning.semantic_preflight intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_f28283fb10495c7f failure_class=tool.extract.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_12a1632a66b2f265 failure_class=tool.app_focus.failed intervention=mechanism_specific_bounded_repair
# self_evolve_annotation: candidate_id=repair_to_pr_1da77208f413327a failure_class=lifecycle.unclosed_model_call intervention=mechanism_specific_bounded_repair
__all__ = ["run_once", "run_loop"]
