"""Atomic stores for governance records."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .models import IterationReceipt, ProjectState


def workspace_root(workspace: str) -> Path:
    path = Path(workspace).expanduser().resolve()
    parts = path.parts
    if "instances" in parts:
        index = parts.index("instances")
        return Path(*parts[:index])
    return path


def instance_id(workspace: str) -> str:
    match = re.search(r"[/\\]instances[/\\](0[1-5])(?:[/\\]|$)", str(workspace))
    return match.group(1) if match else ""


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-一-鿿]+", "_", str(value or "")).strip("_") or "project"


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False) + "\n")


def project_governance_dir(workspace: str, project_id: str) -> Path:
    return workspace_root(workspace) / "share" / "projects" / safe_id(project_id) / "governance"


def save_project_state(workspace: str, state: ProjectState) -> Path:
    path = project_governance_dir(workspace, state.project_id) / "project_state.json"
    atomic_json(path, state.to_dict())
    return path


def load_project_state(workspace: str, project_id: str) -> ProjectState | None:
    path = project_governance_dir(workspace, project_id) / "project_state.json"
    try:
        return ProjectState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def save_receipt(workspace: str, receipt: IterationReceipt) -> Path:
    root = project_governance_dir(workspace, receipt.project_id)
    path = root / "receipts" / f"{receipt.iteration:04d}_{safe_id(receipt.receipt_id)}.json"
    data = receipt.to_dict()
    atomic_json(path, data)
    append_jsonl(root / "iteration_history.jsonl", data)
    return path


def latest_receipt(workspace: str, project_id: str) -> IterationReceipt | None:
    root = project_governance_dir(workspace, project_id)
    directory = root / "receipts"
    paths = sorted(directory.glob("*.json")) if directory.exists() else []
    invalidated: set[str] = set()
    try:
        for line in (root / "receipt_corrections.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("action") == "invalidate" and row.get("receipt_id"):
                invalidated.add(str(row["receipt_id"]))
            elif row.get("action") == "reinstate" and row.get("receipt_id"):
                invalidated.discard(str(row["receipt_id"]))
    except (OSError, ValueError, TypeError):
        pass
    for path in reversed(paths):
        try:
            value = IterationReceipt.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue
        if value.receipt_id not in invalidated:
            return value
    return None


def governance_log(workspace: str, name: str) -> Path:
    return workspace_root(workspace) / "share" / "mind" / "governance" / f"{safe_id(name)}.jsonl"
