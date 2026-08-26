"""Durable, content-addressed evidence bundles for Campaign outcomes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .models import now_iso
from .storage import atomic_json, workspace_root


VOLATILE_JSON_KEYS = {
    "command", "created_at", "updated_at", "timestamp", "ts", "path",
    "paths", "files", "output_path", "working_dir", "task_id", "campaign_id",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in VOLATILE_JSON_KEYS
        }
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        return round(value, 10)
    return value


def semantic_outcome_fingerprint(paths: list[str], event_types: list[str]) -> str:
    """Hash model-independent result content, not paths or report timestamps."""
    candidates: list[tuple[str, Any]] = []
    for raw in paths:
        path = Path(str(raw))
        if not path.is_file() or path.suffix.lower() != ".json":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        candidates.append((path.name, _canonical(value)))
    payload: dict[str, Any] = {"event_types": sorted(set(event_types))}
    if candidates:
        payload["results"] = [value for _, value in sorted(candidates)]
    else:
        payload["file_hashes"] = sorted(
            _sha256(Path(raw)) for raw in paths if Path(str(raw)).is_file()
        )
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]


def archive_work_item_evidence(
    workspace: str,
    *,
    campaign_id: str,
    work_item_id: str,
    project_id: str,
    instance_id: str,
    artifacts: list[str],
    event_types: list[str],
) -> dict[str, Any]:
    """Copy task-local artifacts into a durable immutable evidence bundle."""
    root = workspace_root(workspace)
    bundle = root / "share" / "evidence" / project_id / campaign_id / work_item_id
    bundle.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    archived: list[str] = []
    used_names: set[str] = set()
    for index, raw in enumerate(artifacts):
        source = Path(str(raw))
        if not source.is_file():
            continue
        digest = _sha256(source)
        name = source.name
        if name in used_names:
            name = f"{index:02d}_{name}"
        used_names.add(name)
        target = bundle / name
        if not target.is_file() or _sha256(target) != digest:
            temporary = bundle / f".{name}.{os.getpid()}.tmp"
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        records.append({
            "source_path": str(source),
            "archived_path": str(target),
            "name": name,
            "size_bytes": target.stat().st_size,
            "sha256": digest,
        })
        archived.append(str(target))
    semantic = semantic_outcome_fingerprint(archived, event_types)
    manifest = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "work_item_id": work_item_id,
        "project_id": project_id,
        "instance_id": instance_id,
        "event_types": list(event_types),
        "semantic_outcome_fingerprint": semantic,
        "artifacts": records,
        "created_at": now_iso(),
    }
    manifest_path = bundle / "evidence_manifest.json"
    atomic_json(manifest_path, manifest)
    return {
        "ok": bool(records),
        "bundle_path": str(bundle),
        "manifest_path": str(manifest_path),
        "artifacts": archived,
        "semantic_outcome_fingerprint": semantic,
        "records": records,
    }
