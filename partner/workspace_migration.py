"""Workspace migration and shared-resource merge helpers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from partner.workspace_layout import (
    common_dir,
    ensure_instance_layout,
    external_dir,
    instance_dir,
    workspace_root_from_instance,
)


MANIFEST_NAME = "resource_manifest.json"


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: str, include: tuple[str, ...] = ("common", "external")) -> dict:
    root_path = Path(root).expanduser()
    rows = []
    for top in include:
        base = root_path / top
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.name == MANIFEST_NAME:
                continue
            rel = path.relative_to(root_path).as_posix()
            stat = path.stat()
            rows.append(
                {
                    "path": rel,
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                    "sha256": file_sha256(str(path)),
                }
            )
    return {
        "version": 1,
        "generated_at": datetime.now().isoformat(),
        "root": str(root_path),
        "files": rows,
    }


def write_manifest(root: str) -> str:
    root_path = Path(root).expanduser()
    os.makedirs(common_dir(str(root_path)), exist_ok=True)
    manifest_path = root_path / "common" / MANIFEST_NAME
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(build_manifest(str(root_path)), f, ensure_ascii=False, indent=2)
    return str(manifest_path)


def merge_shared_resources(source_root: str, target_root: str) -> dict:
    source = Path(source_root).expanduser()
    target = Path(target_root).expanduser()
    copied = []
    skipped = []
    conflicts = []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for dirname in ("common", "external"):
        src_base = source / dirname
        if not src_base.exists():
            continue
        dst_base = target / dirname
        for src_file in sorted(src_base.rglob("*")):
            if not src_file.is_file() or src_file.name == MANIFEST_NAME:
                continue
            rel = src_file.relative_to(src_base)
            dst_file = dst_base / rel
            if not dst_file.exists():
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                copied.append(f"{dirname}/{rel.as_posix()}")
                continue
            if file_sha256(str(src_file)) == file_sha256(str(dst_file)):
                skipped.append(f"{dirname}/{rel.as_posix()}")
                continue
            conflict_file = target / dirname / "_conflicts" / stamp / rel
            conflict_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, conflict_file)
            conflicts.append(f"{dirname}/{rel.as_posix()}")
    manifest = write_manifest(str(target))
    return {"copied": copied, "skipped": skipped, "conflicts": conflicts, "manifest": manifest}


def migrate_partner_workspace(source_instance: str, target_root: str, partner_id: str | None = None, merge_shared: bool = True) -> dict:
    source_instance = str(Path(source_instance).expanduser())
    target_root = str(Path(target_root).expanduser())
    partner_id = partner_id or Path(source_instance).name
    target_instance = instance_dir(target_root, partner_id)
    os.makedirs(os.path.dirname(target_instance), exist_ok=True)
    shutil.copytree(source_instance, target_instance, dirs_exist_ok=True)
    ensure_instance_layout(target_instance)
    shared = {}
    if merge_shared:
        source_root = workspace_root_from_instance(source_instance)
        shared = merge_shared_resources(source_root, target_root)
    return {
        "partner_id": partner_id,
        "source_instance": source_instance,
        "target_instance": target_instance,
        "target_root": target_root,
        "shared": shared,
    }
