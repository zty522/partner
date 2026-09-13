"""Restart-boundary Event extension discovery.

There is intentionally no end-user CLI.  Extensions are installed by placing
an audited manifest under the documented directory; instances see the new
immutable catalog after restart.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

from .catalog import EventDefinition


def _delegate(target: str):
    def handler(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        # Resolve against the same immutable canonical catalog.  Extension
        # manifests are aliases, never an escape hatch back into Harness.
        from .catalog import build_catalog
        spec = build_catalog(workspace=getattr(ctx, "workspace", None)).get(target)
        if spec is None or spec.name.startswith("candidate."):
            return {"ok": False, "status": "failed", "error": f"extension target unavailable: {target}"}
        return spec.handler(ctx, params)
    return handler


def _manifest_definitions(directory: Path, *, source: str) -> list[EventDefinition]:
    result: list[EventDefinition] = []
    if not directory.exists():
        return result
    for path in sorted(directory.glob("*.event.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            name = str(value["name"])
            target = str(value["target_event"])
            if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", name):
                raise ValueError("invalid canonical Event name")
            if target == name or not target:
                raise ValueError("extension must delegate to another registered capability")
            result.append(EventDefinition(
                name=name, series=str(value.get("series") or "project"),
                description=str(value.get("description") or name), handler=_delegate(target),
                version=str(value.get("version") or "1.0.0"), source=source,
                execution_method=str(value.get("execution_method") or "local"),
                external_call=bool(value.get("external_call")),
                produces_artifact=bool(value.get("produces_artifact")),
                reads_existing_artifact=bool(value.get("reads_existing_artifact")),
                idempotent=bool(value.get("idempotent", True)),
                permission_class=str(value.get("permission_class") or "local"),
            ))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return result


def extension_definitions(workspace: str | Path | None = None) -> list[EventDefinition]:
    """Three sources: repository, operator workspace, promoted Candidate."""
    repo = Path(__file__).resolve().parents[1]
    definitions = _manifest_definitions(repo / "events/extensions", source="repository_extension")
    if workspace is None:
        return definitions
    root = Path(workspace).resolve()
    if root.parent.name == "instances":
        root = root.parent.parent
    definitions.extend(_manifest_definitions(
        root / "config/event_extensions", source="operator_extension"))
    try:
        from partner.governance.candidate_skills import load_candidate_skills
        for row in load_candidate_skills(str(root)):
            if row.get("status") != "promoted" or not row.get("production_effective"):
                continue
            contract = dict(row.get("execution_contract") or {})
            target = str(contract.get("event_type") or "")
            raw_id = re.sub(r"[^a-z0-9_]+", "_", str(row.get("candidate_id") or "").lower()).strip("_")
            if not raw_id or not target:
                continue
            definitions.append(EventDefinition(
                name=f"candidate.{raw_id}", series="self_evolution",
                description=str(row.get("description") or f"Promoted Candidate {raw_id}"),
                handler=_delegate(target), source="promoted_candidate",
                version=str(row.get("version") or "1.0.0"),
                execution_method="local", idempotent=False,
            ))
    except Exception:
        pass
    return definitions
