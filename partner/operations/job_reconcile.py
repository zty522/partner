"""Reconcile stale nonterminal Job projections without deleting evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import time

from partner.event_fabric.flows import EventFlowStore
from partner.index.job_repository import init


TERMINAL = {"completed", "failed", "cancelled"}


def reconcile_stale_jobs(workspace: str | Path, *, older_than_seconds: float,
                         apply: bool = False, actor: str = "job_reconcile") -> dict[str, Any]:
    """Classify and optionally cancel stale orphan projections.

    This operation is intentionally age-gated.  It never removes a Job, Flow,
    history row or artifact.  Applying it appends Job history, marks the Flow
    cancelled, and refreshes the legacy JSON projection.
    """
    root = Path(workspace).expanduser().resolve()
    repo = init(root)
    cutoff = time.time() - max(1.0, float(older_than_seconds))
    rows = repo.list_by_status(("queued", "running"), limit=100000)
    flow_store = EventFlowStore(root)
    candidates = []
    skipped = []
    for row in rows:
        if float(row.get("updated_at") or 0) > cutoff:
            skipped.append({"job_id": row["job_id"], "reason": "recent"})
            continue
        flow_id = str(row.get("flow_id") or "")
        try:
            flow = flow_store.load(flow_id) if flow_id else None
            flow_status = flow.status if flow else "missing"
        except (OSError, ValueError, TypeError):
            flow, flow_status = None, "missing_or_invalid"
        candidates.append({"job_id": row["job_id"], "job_status": row["status"],
                           "flow_id": flow_id, "flow_status": flow_status,
                           "updated_at": row.get("updated_at")})
        if not apply:
            continue
        detail = {"reason": "stale_nonterminal_projection", "cutoff_epoch": cutoff,
                  "prior_flow_status": flow_status, "evidence_preserved": True}
        repo.request_cancel(row["job_id"], actor=actor)
        repo.note(row["job_id"], actor=actor, kind="stale_projection_reconciled", detail=detail)
        if flow is not None and flow.status not in TERMINAL:
            flow.status = "cancelled"
            flow.recovery_history.append({"at": datetime.now(timezone.utc).isoformat(),
                                          "kind": "stale_projection_reconciled",
                                          "actor": actor, **detail})
            flow_store.save(flow)
        record = repo.get_record(row["job_id"])
        if record is not None:
            target = root / "state/application/jobs" / f"{row['job_id']}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, target)
    report = {"schema_version": 1, "mode": "apply" if apply else "dry_run",
              "cutoff_epoch": cutoff, "nonterminal_seen": len(rows),
              "candidate_count": len(candidates), "skipped_count": len(skipped),
              "candidates": candidates, "skipped": skipped,
              "completed_at": datetime.now(timezone.utc).isoformat()}
    directory = root / "state/maintenance/job_reconciliation"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = directory / f"{stamp}_{report['mode']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(path)
    return report


def reconcile_orphan_flows(workspace: str | Path, *, apply: bool = False,
                           actor: str = "flow_reconcile") -> dict[str, Any]:
    """Close nonterminal Flow projections whose authoritative Job is terminal.

    Candidate discovery uses the native SQLite history index, never a scan of
    every historical Flow JSON.  The JSON remains the detailed state record
    and is updated only after the indexed parent Job proves terminal.
    """
    from partner.index.resource_catalog import ResourceCatalog

    root = Path(workspace).expanduser().resolve()
    jobs = init(root)
    store = EventFlowStore(root)
    indexed = []
    for row in ResourceCatalog(root).query("flow", limit=500):
        try:
            metadata = json.loads(row.get("metadata") or "{}")
        except (TypeError, ValueError):
            metadata = {}
        if metadata.get("status") in {"running", "suspended"}:
            indexed.append({**row, **metadata})
    candidates = []
    skipped = []
    now = datetime.now(timezone.utc).isoformat()
    for row in indexed:
        job_id = str(row.get("task_id") or "")
        job = jobs.get_record(job_id) if job_id else None
        if job and str(job.get("status") or "") not in TERMINAL:
            skipped.append({"flow_id":row.get("flow_id"), "job_id":job_id,
                            "reason":"parent_job_nonterminal"})
            continue
        candidate = {"flow_id":row.get("flow_id"), "flow_status":row.get("status"),
                     "job_id":job_id, "job_status":job.get("status") if job else "missing"}
        candidates.append(candidate)
        if not apply:
            continue
        try:
            flow = store.load(str(row["flow_id"]))
        except (OSError, ValueError, TypeError):
            candidate["apply_error"] = "flow_projection_missing_or_invalid"
            continue
        if flow.status in TERMINAL:
            continue
        flow.status = "cancelled"
        flow.waiting_task_id = ""
        flow.next_check_at = 0.0
        flow.active_child_flow_id = ""
        flow.recovery_history.append({
            "at":now, "kind":"orphan_flow_projection_reconciled",
            "actor":actor, "parent_job_status":job.get("status") if job else "missing",
            "evidence_preserved":True,
        })
        store.save(flow)
    report = {"schema_version":1, "mode":"apply" if apply else "dry_run",
              "indexed_nonterminal_seen":len(indexed),
              "candidate_count":len(candidates), "skipped_count":len(skipped),
              "candidates":candidates, "skipped":skipped, "completed_at":now}
    directory = root / "state/maintenance/flow_reconciliation"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = directory / f"{stamp}_{report['mode']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(path)
    return report


__all__ = ["reconcile_stale_jobs", "reconcile_orphan_flows"]
