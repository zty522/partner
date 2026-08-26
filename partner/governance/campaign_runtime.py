"""Runtime adapters for campaign dispatch and two-slot service switching."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .campaign_models import WorkItem
from .scheduler import load_scheduler, set_active_slots
from .storage import workspace_root


def runtime_instance_ready(workspace: str, instance_id: str) -> bool:
    """Return true only when the instance's real delivery bridge is ready."""
    path = workspace_root(workspace) / "instances" / str(instance_id) / "state" / "qq_delivery_state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return payload.get("delivery_ready") is True and payload.get("status") == "ready"


def dispatch_to_instance(workspace: str, item: WorkItem, instruction: str) -> str:
    """Append one uniquely identified task to an instance inbox."""
    root = workspace_root(workspace)
    instance_workspace = root / "instances" / item.instance_id
    state_dir = instance_workspace / "state"
    if not state_dir.is_dir():
        raise RuntimeError(f"instance workspace missing: {instance_workspace}")
    # Retries must be distinct inbox messages. Reusing the first attempt's id
    # lets the instance-level deduplicator consume the retry without executing
    # it, while the controller incorrectly believes it was queued.
    recoveries = sum(str(value).startswith("transport_recovery=") for value in item.evidence)
    recovery_suffix = f"_recovery_{recoveries}" if recoveries else ""
    message_id = (
        f"campaign_{item.campaign_id}_{item.work_item_id}_attempt_{max(1, item.attempt)}"
        f"{recovery_suffix}"
    )
    entry = {
        "id": message_id,
        "message_id": message_id,
        "role": "user",
        "content": instruction,
        "source": "campaign_controller",
        "sender_id": "campaign_controller",
        "sender_name": "Partner Campaign",
        "campaign_id": item.campaign_id,
        "work_item_id": item.work_item_id,
    }
    inbox = state_dir / "desktop_inbox.jsonl"
    with inbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return message_id


def switch_runtime_slots(workspace: str, instance_ids: list[str]) -> None:
    """Persist slots, then stop removed units and start selected units."""
    root = str(workspace_root(workspace))
    previous = set(load_scheduler(root).get("active_slots") or [])
    selected = list(dict.fromkeys(str(value) for value in instance_ids))
    set_active_slots(root, selected, reason="campaign scheduler")
    removed = sorted(previous - set(selected))
    if removed:
        subprocess.run(
            ["systemctl", "--user", "stop", *[f"partner-{value}.service" for value in removed]],
            check=True,
        )
    if selected:
        subprocess.run(
            ["systemctl", "--user", "start", *[f"partner-{value}.service" for value in selected]],
            check=True,
        )
