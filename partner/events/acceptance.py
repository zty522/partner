"""Acceptance-only Event handlers.

The `acceptance.echo` Event writes a local audit line and returns
immediately.  Used by partner.event_flows.acceptance to verify the
JobRepository claim/release chain end-to-end.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from partner.event_fabric.catalog import EventDefinition


def acceptance_echo(ctx, params):
    """Write a single line to the local acceptance audit log.

    No external side effects, no LLM calls, no QQ routing.
    """
    audit_dir = Path(ctx.workspace) / "state/acceptance"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "event_id": params.get("event_id", ""),
        "instance_id": getattr(ctx, "instance_id", ""),
        "node_id": params.get("node_id", ""),
        "flow_id": params.get("flow_id", ""),
        "request": params.get("initial", {}).get("request", "")[:200],
    }
    audit_log = audit_dir / "acceptance_audit.jsonl"
    with audit_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + chr(10))
        f.flush()
    return {
        "ok": True,
        "status": "completed",
        "summary": "acceptance.echo wrote audit line",
        "semantic_output": {"written_to": str(audit_log)},
        "files": [str(audit_log)],
        "evidence_refs": [],
        "token_usage": {},
    }


DEFINITIONS = [EventDefinition(
    "acceptance.echo", "acceptance",
    "Acceptance-only Event that writes a local audit line",
    acceptance_echo, execution_method="local", timeout_seconds=30)]
