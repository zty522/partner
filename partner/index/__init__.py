"""Index bootstrap / apply_changes / reconcile / health_report / rebuild.

These are dedicated maintenance entry points and should be invoked
from a cron / systemd-style process, not from regular production
Events.  A regular production Event that wants to find history /
code / docs / artifacts calls the appropriate Repository API
directly; it does not call `index.bootstrap` or `index.rebuild`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime_storage import workspace_dir
from .job_repository import init as init_jobs
from .history_repository import init as init_history
from .document_repository import init as init_documents
from .code_repository import init as init_code
from .memory_repository import init as init_memory
from .artifact_repository import init as init_artifacts
from .source_repository import init as init_sources


@dataclass
class IndexReport:
    started_at: float
    finished_at: float
    components: dict[str, dict[str, Any]]
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": self.finished_at - self.started_at,
            "components": self.components,
            "errors": self.errors,
        }


def bootstrap(workspace_root: Path, *, force: bool = False) -> IndexReport:
    """First-time bootstrap: create every index, ingest everything.

    `force=True` re-ingests even unchanged files (slow; intended for
    recovery, not for normal polling).
    """
    started = time.time()
    errors: list[str] = []
    components: dict[str, dict[str, Any]] = {}

    # 1) job repository (creates DB only)
    try:
        jr = init_jobs(workspace_root)
        components["jobs"] = {"stats": jr.stats()}
    except Exception as exc:
        errors.append(f"jobs: {exc}")

    # 2) history repository (initial = rebuild)
    try:
        hr = init_history(workspace_root)
        if force:
            components["history"] = hr.rebuild()
        else:
            # incremental ingest of all sources
            stats = {}
            for source in ("events.jsonl", "summaries.jsonl",
                           "work_items.jsonl", "selections.jsonl"):
                stats[source] = hr.ingest_jsonl(source).get("ingested", 0)
            flow_dir = workspace_root / "state" / "event_flows"
            flow_count = 0
            if flow_dir.exists():
                for path in flow_dir.glob("flow_*.json"):
                    hr.ingest_flow_state(path)
                    flow_count += 1
            stats["flows"] = flow_count
            components["history"] = stats
    except Exception as exc:
        errors.append(f"history: {exc}")

    # 3) documents
    try:
        partner_root = workspace_root.parent if (workspace_root / "partner").exists() else workspace_root
        catalog = partner_root / "docs" / "catalog.yaml"
        if not catalog.exists():
            catalog = workspace_root / "docs" / "catalog.yaml"
        dr = init_documents(workspace_root, catalog)
        dr.ingest_catalog()
        if force:
            # ingest every Markdown referenced in catalog (bounded)
            for doc in dr.list_documents():
                p = doc.get("path")
                if not p:
                    continue
                full = partner_root / p
                if full.exists() and full.suffix in (".md", ".markdown"):
                    dr.ingest_markdown(doc["doc_id"], full, force=True)
        components["documents"] = {"catalog_entries": len(dr.list_documents())}
    except Exception as exc:
        errors.append(f"documents: {exc}")

    # 4) code
    try:
        cr = init_code(workspace_root)
        components["code"] = cr.incremental_ingest(force=force)
    except Exception as exc:
        errors.append(f"code: {exc}")

    # 5) memory
    try:
        mr = init_memory(workspace_root)
        components["memory"] = mr.ingest_all(force=force)
    except Exception as exc:
        errors.append(f"memory: {exc}")

    # 6) artifacts (no automatic discovery; records are inserted by
    # Events that produce them)
    try:
        ar = init_artifacts(workspace_root)
        components["artifacts"] = {"ready": True}
    except Exception as exc:
        errors.append(f"artifacts: {exc}")

    # 7) sources (no automatic network use; registrations only)
    try:
        sr = init_sources(workspace_root)
        components["sources"] = {"ready": True}
    except Exception as exc:
        errors.append(f"sources: {exc}")

    return IndexReport(
        started_at=started,
        finished_at=time.time(),
        components=components,
        errors=errors,
    )


def apply_changes(workspace_root: Path) -> dict[str, Any]:
    """Incremental ingest for all indices.

    Safe to call repeatedly.  Use this from a periodic maintenance task
    that runs out-of-band from production Events.
    """
    return bootstrap(workspace_root, force=False).to_dict()


def reconcile(workspace_root: Path) -> dict[str, Any]:
    """Force a rebuild of the indexed projections.  Use when a partial
    failure leaves the index out of sync with the durable JSONL /
    documents / source tree.
    """
    return bootstrap(workspace_root, force=True).to_dict()


def health_report(workspace_root: Path) -> dict[str, Any]:
    """Report per-component counts and watermark information.  Cheap."""
    out: dict[str, Any] = {"workspace": str(workspace_root)}
    try:
        hr = init_history(workspace_root)
        offsets = {k: hr.offset(k) for k in
                   ("events.jsonl", "summaries.jsonl", "work_items.jsonl", "selections.jsonl")}
        out["history"] = {
            "watermarks": offsets,
            "event_count": int(next(iter(
                conn.execute("SELECT COUNT(*) FROM events").fetchone()
                for conn in []), (0,))[0]) if False else None,
        }
    except Exception as exc:
        out["history_error"] = str(exc)
    try:
        cr = init_code(workspace_root)
        from .sqlite_base import get_connection as _gc
        conn = _gc(cr.db_path)
        out["code"] = {
            "files": int(conn.execute("SELECT COUNT(*) FROM repo_files").fetchone()[0]),
            "symbols": int(conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]),
        }
    except Exception as exc:
        out["code_error"] = str(exc)
    return out


def rebuild(workspace_root: Path) -> dict[str, Any]:
    return reconcile(workspace_root)
