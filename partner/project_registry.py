"""Shared project registry across Partner instances.

This is intentionally separate from Hermes memory/skills.  It is a small
Partner-owned index that lets instances know whether a project is local,
owned by another bot, or released to a shared pool for later pickup.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


PUBLIC_STATUSES = {"archived", "public"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _workspace_root(workspace: str) -> Path:
    path = Path(workspace).expanduser().resolve()
    if path.parent.name == "instances":
        return path.parent.parent
    return path


def instance_id_from_workspace(workspace: str) -> str:
    path = Path(workspace).expanduser().resolve()
    if path.parent.name == "instances":
        return path.name
    return os.environ.get("PARTNER_INSTANCE_ID", "") or "default"


def registry_path(workspace: str) -> Path:
    root = _workspace_root(workspace)
    path = root / "shared_projects" / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("projects", {})
            return data
    except Exception:
        pass
    return {"version": 1, "projects": {}}


def _save(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def normalize_project_name(name: str) -> str:
    text = re.sub(r"\s+", "", (name or "").strip().lower())
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "", text)
    return text or "project"


def _summarize_project(workspace: str, project: str, max_chars: int = 360) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", project or "project").strip("_") or "project"
    for rel in (
        f"20_records/projects/{safe}/project_brief.md",
        f"20_records/projects/{safe}/state.md",
        f"user/projects/{safe}/mind_status.md",
        f"user/projects/{safe}/research_journey.md",
    ):
        path = Path(workspace) / rel
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if text:
            compact = re.sub(r"\s+", " ", text)
            return compact[:max_chars].rstrip()
    return ""


def register_project(
    workspace: str,
    project: str,
    *,
    status: str = "active",
    reason: str = "",
    make_public: bool | None = None,
) -> dict[str, Any]:
    project = (project or "").strip()
    if not project:
        return {}
    path = registry_path(workspace)
    data = _load(path)
    key = normalize_project_name(project)
    now = _now()
    instance_id = instance_id_from_workspace(workspace)
    row = data.setdefault("projects", {}).setdefault(key, {})
    row.update({
        "project_name": row.get("project_name") or project,
        "canonical_key": key,
        "owner_instance": row.get("owner_instance") or instance_id,
        "last_instance": instance_id,
        "last_workspace": str(Path(workspace).expanduser().resolve()),
        "status": status or row.get("status") or "active",
        "updated_at": now,
        "reason": reason[:240],
        "summary": _summarize_project(workspace, project) or row.get("summary", ""),
    })
    if make_public is True:
        row["public"] = True
    elif make_public is False:
        row["public"] = False
    else:
        row["public"] = bool(row.get("public"))
    if row["public"]:
        row.setdefault("released_at", now)
    elif "released_at" in row:
        row.pop("released_at", None)
    data["updated_at"] = now
    _save(path, data)
    return row


def release_project(workspace: str, project: str, *, reason: str = "") -> dict[str, Any]:
    return register_project(workspace, project, status="waiting", reason=reason, make_public=True)


def keep_project_private(workspace: str, project: str, *, reason: str = "") -> dict[str, Any]:
    """Mark a project as instance-owned, even if its lifecycle status is waiting."""
    return register_project(workspace, project, status="waiting", reason=reason, make_public=False)


def maybe_release_inactive_active_project(workspace: str, project: str, *, inactive_hours: int = 24) -> bool:
    """Release an active project to the public pool if it has been untouched long enough."""
    if not project or inactive_hours <= 0:
        return False
    plan_path = Path(workspace) / "state" / "active_plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(plan, dict):
        return False
    if str(plan.get("title") or "") != project:
        return False
    status = str(plan.get("project_status") or plan.get("status") or "").lower()
    if status and status not in {"active", "planning", "cooling_down"}:
        return False
    last_raw = str(plan.get("last_heartbeat") or plan.get("created_at") or "")
    try:
        last = datetime.fromisoformat(last_raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return False
    age_hours = (datetime.now() - last).total_seconds() / 3600
    if age_hours < inactive_hours:
        return False
    release_project(workspace, project, reason=f"inactive for {int(age_hours)}h")
    return True


def claim_project(workspace: str, project: str, *, reason: str = "") -> dict[str, Any]:
    row = register_project(workspace, project, status="active", reason=reason, make_public=False)
    if row:
        path = registry_path(workspace)
        data = _load(path)
        key = normalize_project_name(project)
        if key in data.get("projects", {}):
            data["projects"][key]["owner_instance"] = instance_id_from_workspace(workspace)
            data["projects"][key]["last_instance"] = instance_id_from_workspace(workspace)
            data["projects"][key]["public"] = False
            data["projects"][key]["updated_at"] = _now()
            data["updated_at"] = _now()
            _save(path, data)
            return data["projects"][key]
    return row


def find_project(workspace: str, project: str) -> dict[str, Any] | None:
    key = normalize_project_name(project)
    data = _load(registry_path(workspace))
    projects = data.get("projects", {})
    if key in projects:
        return dict(projects[key])
    query = key
    for row in projects.values():
        name_key = normalize_project_name(str(row.get("project_name") or ""))
        if query and (query in name_key or name_key in query):
            return dict(row)
    return None


def project_location_hint(workspace: str, project: str) -> str:
    row = find_project(workspace, project)
    if not row:
        return ""
    here = instance_id_from_workspace(workspace)
    owner = str(row.get("owner_instance") or "")
    last = str(row.get("last_instance") or owner)
    public = bool(row.get("public"))
    status = str(row.get("status") or "")
    if owner == here or last == here:
        return ""
    if public or status in PUBLIC_STATUSES:
        return ""
    return ""


def _safe_project_name(project: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", project or "project").strip("_") or "project"


def _copytree_missing(src: Path, dst: Path) -> bool:
    if not src.exists() or not src.is_dir():
        return False
    copied = False
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if target.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        copied = True
    return copied


def import_public_project_context(workspace: str, project: str) -> bool:
    """Import a released/shared project into this instance without overwriting local work."""
    row = find_project(workspace, project)
    if not row or not row.get("public"):
        return False
    source_ws = Path(str(row.get("last_workspace") or "")).expanduser()
    target_ws = Path(workspace).expanduser().resolve()
    if not source_ws.exists() or source_ws.resolve() == target_ws:
        return False
    safe = _safe_project_name(str(row.get("project_name") or project))
    copied = False
    copied = _copytree_missing(source_ws / "20_records" / "projects" / safe, target_ws / "20_records" / "projects" / safe) or copied
    copied = _copytree_missing(source_ws / "user" / "projects" / safe, target_ws / "user" / "projects" / safe) or copied
    copied = _copytree_missing(source_ws / "user" / "reports" / safe, target_ws / "user" / "reports" / safe) or copied
    if copied:
        claim_project(workspace, str(row.get("project_name") or project), reason="imported public project context")
    return copied
