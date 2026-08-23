"""Durable storage for campaigns, work items and instance leases."""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .campaign_models import CampaignReport, CampaignState, InstanceLease, WorkItem
from .storage import append_jsonl, atomic_json, safe_id, workspace_root


def campaigns_root(workspace: str) -> Path:
    return workspace_root(workspace) / "state" / "campaigns"


def campaign_dir(workspace: str, campaign_id: str) -> Path:
    return campaigns_root(workspace) / safe_id(campaign_id)


@contextmanager
def campaign_lock(workspace: str, campaign_id: str) -> Iterator[None]:
    path = campaign_dir(workspace, campaign_id) / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def save_campaign(workspace: str, state: CampaignState) -> Path:
    path = campaign_dir(workspace, state.campaign_id) / "campaign_state.json"
    atomic_json(path, state.to_dict())
    return path


def load_campaign(workspace: str, campaign_id: str) -> CampaignState | None:
    path = campaign_dir(workspace, campaign_id) / "campaign_state.json"
    try:
        return CampaignState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def set_active_campaign(workspace: str, campaign_id: str) -> Path:
    path = campaigns_root(workspace) / "active_campaign.json"
    atomic_json(path, {"campaign_id": campaign_id})
    return path


def active_campaign_id(workspace: str) -> str:
    try:
        data = json.loads((campaigns_root(workspace) / "active_campaign.json").read_text(encoding="utf-8"))
        return str(data.get("campaign_id") or "")
    except (OSError, ValueError, TypeError):
        return ""


def save_work_item(workspace: str, item: WorkItem) -> Path:
    path = campaign_dir(workspace, item.campaign_id) / "work_items" / f"{safe_id(item.work_item_id)}.json"
    atomic_json(path, item.to_dict())
    return path


def load_work_item(workspace: str, campaign_id: str, work_item_id: str) -> WorkItem | None:
    path = campaign_dir(workspace, campaign_id) / "work_items" / f"{safe_id(work_item_id)}.json"
    try:
        return WorkItem.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def list_work_items(workspace: str, campaign_id: str) -> list[WorkItem]:
    root = campaign_dir(workspace, campaign_id) / "work_items"
    values: list[WorkItem] = []
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            values.append(WorkItem.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            continue
    return values


def save_lease(workspace: str, lease: InstanceLease) -> Path:
    path = campaign_dir(workspace, lease.campaign_id) / "leases" / f"{safe_id(lease.lease_id)}.json"
    atomic_json(path, lease.to_dict())
    return path


def load_lease(workspace: str, campaign_id: str, lease_id: str) -> InstanceLease | None:
    path = campaign_dir(workspace, campaign_id) / "leases" / f"{safe_id(lease_id)}.json"
    try:
        return InstanceLease.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def list_leases(workspace: str, campaign_id: str) -> list[InstanceLease]:
    root = campaign_dir(workspace, campaign_id) / "leases"
    values: list[InstanceLease] = []
    for path in sorted(root.glob("*.json")) if root.exists() else []:
        try:
            values.append(InstanceLease.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            continue
    return values


def append_campaign_event(workspace: str, campaign_id: str, event: dict[str, Any]) -> Path:
    path = campaign_dir(workspace, campaign_id) / "events.jsonl"
    append_jsonl(path, event)
    return path


def save_report(workspace: str, report: CampaignReport) -> Path:
    path = campaign_dir(workspace, report.campaign_id) / "reports" / f"{safe_id(report.report_id)}.json"
    atomic_json(path, report.to_dict())
    append_jsonl(campaign_dir(workspace, report.campaign_id) / "report_history.jsonl", report.to_dict())
    return path
