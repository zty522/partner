"""DGM (Darwin Gödel Machine) inspired lineage / archive adapter (M2).

DGM maintains a *genealogy* of agent variants; each variant has parents,
a fitness score, and an "archive" status.  Partner's
``EvolutionExperiment`` already records parent_run_id and attempt ids;
this adapter formalises the lineage graph as JSON files under
``state/dgm/`` so an external observer can visualise the genealogy
without re-reading the partner database.

Wired into ``partner/events/autonomous_evolution.py::decision`` via
``partner/research/adapters/wiring.py::WIRINGS[dgm]``.  The node
persisted includes ``parent_id``, ``generation``, ``archive_status``
("candidate" / "active" / "retired" / "rejected") and a
``code_fingerprint``.

State honesty: ``static_implemented``.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DGMLineageNode:
    node_id: str
    parent_id: str | None
    generation: int
    archive_status: str  # candidate | active | retired | rejected
    fitness: float | None = None
    code_fingerprint: str = ""
    rationale: str = ""
    schema_version: str = "dgm.node/v1"
    notes: str = ""


def make_node(*, parent_id: str | None, generation: int,
              archive_status: str = "candidate",
              code_fingerprint: str = "",
              fitness: float | None = None,
              rationale: str = "") -> DGMLineageNode:
    if generation < 0:
        raise ValueError("generation must be >= 0")
    if archive_status not in {"candidate", "active", "retired", "rejected"}:
        raise ValueError(f"archive_status invalid: {archive_status!r}")
    if not code_fingerprint:
        raise ValueError("code_fingerprint must be non-empty")
    return DGMLineageNode(
        node_id="dgm_" + uuid.uuid4().hex[:12],
        parent_id=parent_id,
        generation=generation,
        archive_status=archive_status,
        fitness=fitness,
        code_fingerprint=code_fingerprint,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Persistent archive (per workspace, per lineage run)
# ---------------------------------------------------------------------------


def _archive_dir(workspace: "Path") -> Path:
    return Path(workspace).expanduser().resolve() / "state" / "dgm"


def _archive_path(workspace: "Path", run_id: str) -> Path:
    return _archive_dir(workspace) / f"{run_id}.json"


def _read_archive(workspace: "Path", run_id: str) -> list[dict]:
    p = _archive_path(workspace, run_id)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _write_archive(workspace: "Path", run_id: str, nodes: list[dict]) -> None:
    p = _archive_path(workspace, run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(nodes, indent=2, ensure_ascii=False,
                                sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def persist_node(workspace: "Path", run_id: str,
                  node: DGMLineageNode) -> None:
    """Add (or replace by node_id) the node in the per-run archive."""
    nodes = _read_archive(workspace, run_id)
    nodes = [n for n in nodes if n.get("node_id") != node.node_id]
    nodes.append(asdict(node))
    _write_archive(workspace, run_id, nodes)


def lineage_edges(workspace: "Path", run_id: str) -> list[tuple[str, str]]:
    """Return [(parent_id, child_id), ...] edges from the persisted archive."""
    nodes = _read_archive(workspace, run_id)
    out = []
    for n in nodes:
        if n.get("parent_id"):
            out.append((n["parent_id"], n["node_id"]))
    return sorted(out)


def archive(workspace: "Path", run_id: str) -> list[dict]:
    """Return the raw archive list (list of dicts)."""
    return _read_archive(workspace, run_id)


__all__ = [
    "DGMLineageNode", "make_node",
    "persist_node", "lineage_edges", "archive",
]
