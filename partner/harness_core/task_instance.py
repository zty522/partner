from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


JsonDict = dict[str, Any]


def _utcish_now() -> str:
    return datetime.now().isoformat()


def parse_continue_project_marker(text: str) -> tuple[str, str]:
    """Return cleaned text and an optional project name from --continue-project."""
    raw = str(text or "")
    match = re.search(r"(?:^|\s)--continue-project(?:=|\s+)(?P<name>\"[^\"]+\"|'[^']+'|\S+)", raw)
    if not match:
        return raw, ""
    name = match.group("name").strip().strip("\"'")
    cleaned = (raw[:match.start()] + " " + raw[match.end():]).strip()
    return re.sub(r"\s+", " ", cleaned), name


@dataclass
class TaskInstance:
    task_id: str
    user_message: str
    created_at: str
    working_dir: str
    expected_artifacts: list[JsonDict] = field(default_factory=list)
    completion_status: str = "pending"
    # Hermes 2026-08-27 fix (Bug #48): mirror completion_status with the
    # canonical top-level status field.  asdict() persists both, so
    # downstream monitors can rely on task_instance.json["status"] to
    # know whether the task has finished (was previously always None).
    status: str = "pending"
    continue_from_project: str = ""
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        workspace: str,
        user_message: str,
        *,
        continue_from_project: str = "",
        task_id: str | None = None,
        metadata: JsonDict | None = None,
    ) -> "TaskInstance":
        task_id = task_id or str(uuid.uuid4())
        working_dir = os.path.join(workspace, "state", "tasks", task_id)
        task = cls(
            task_id=task_id,
            user_message=str(user_message or ""),
            created_at=_utcish_now(),
            working_dir=working_dir,
            continue_from_project=str(continue_from_project or ""),
            metadata=dict(metadata or {}),
        )
        os.makedirs(task.working_dir, exist_ok=True)
        task.save()
        task.append_log("task_instance_created", {
            "continue_from_project": task.continue_from_project,
            "working_dir": task.working_dir,
        })
        return task

    @classmethod
    def load(cls, workspace: str, task_id: str) -> "TaskInstance":
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "", str(task_id or ""))
        if not safe_id:
            return None
        path = os.path.join(workspace, "state", "tasks", safe_id, "task_instance.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("invalid task_instance.json")
        data.setdefault("task_id", safe_id)
        data.setdefault("working_dir", os.path.dirname(path))
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})

    @classmethod
    def load_or_create(
        cls,
        workspace: str,
        *,
        task_id: str = "",
        user_message: str = "",
        continue_from_project: str = "",
        metadata: JsonDict | None = None,
    ) -> "TaskInstance":
        if task_id:
            try:
                return cls.load(workspace, task_id)
            except Exception:
                pass
        return cls.create(
            workspace,
            user_message,
            continue_from_project=continue_from_project,
            metadata=metadata,
        )

    @property
    def state_path(self) -> str:
        return os.path.join(self.working_dir, "task_instance.json")

    @property
    def log_path(self) -> str:
        return os.path.join(self.working_dir, "task_log.jsonl")

    def save(self) -> None:
        os.makedirs(self.working_dir, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    def append_log(self, event: str, data: JsonDict | None = None) -> None:
        os.makedirs(self.working_dir, exist_ok=True)
        row = {"ts": _utcish_now(), "event": event, **dict(data or {})}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def update_expected_artifacts(self, expected: list[JsonDict]) -> None:
        cleaned: list[JsonDict] = []
        for item in expected or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "file").strip().lower()
            pattern = str(item.get("pattern") or item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            if kind == "file" and not pattern:
                continue
            cleaned.append({
                "type": kind,
                "pattern": pattern,
                "description": description,
                "required": bool(item.get("required", True)),
            })
        self.expected_artifacts = cleaned
        self.save()
        self.append_log("expected_artifacts_updated", {"expected_artifacts": cleaned})

    def mark(self, status: str, data: JsonDict | None = None) -> None:
        if status not in {"pending", "partial", "done", "failed"}:
            raise ValueError(f"invalid task status: {status}")
        # Hermes 2026-08-27 fix (Bug #48): mirror the status into the top-level
        # self.status field.  Previously only self.completion_status was
        # written, so persisted task_instance.json had status=None for every
        # completed task, making downstream monitors and reviewers unable to
        # distinguish "in flight" from "done".
        self.status = status
        self.completion_status = status
        self.save()
        self.append_log("completion_status_updated", {"status": status, **dict(data or {})})
        terminal_data = dict(data or {})
        # Artifact validation inside the batch harness happens before the
        # outer executor runs claim truth, delivery and Receipt governance.
        # Its ``done`` checkpoint is therefore not an authoritative terminal
        # and must not wake a Campaign before the final verdict exists.
        preliminary_batch_done = (
            status == "done" and terminal_data.get("source") == "batch_plan"
        )
        if status in {"done", "failed"} and not preliminary_batch_done:
            # A durable ledger plus a best-effort named-pipe wakeup lets an
            # explicitly authorised Campaign dispatch the next bounded item
            # immediately.  Manual tasks merely append the audit row; absence
            # of a controller is expected and never changes their result.
            try:
                from ..governance.completion_signal import emit_task_terminal

                emit_task_terminal(
                    self.working_dir,
                    task_id=self.task_id,
                    status=status,
                    data=terminal_data,
                )
            except Exception:
                # Observability must never become task-completion authority.
                pass


def reconcile_orphaned_task_instances(workspace: str, cutoff: datetime) -> int:
    """Finalize in-memory executions that cannot survive a runtime restart.

    ``TaskQueue`` already cancels its mirrors at startup, but the authoritative
    ``TaskInstance`` used by native project continuation previously stayed
    ``pending`` forever. That made the controller suppress redispatch after a
    host restart. Failed finalization is honest: the interrupted execution is
    not resumed or reported as successful, and its terminal can trigger the
    bounded Event-first learning path.
    """
    tasks_root = os.path.join(str(workspace), "state", "tasks")
    if not os.path.isdir(tasks_root):
        return 0
    changed = 0
    for name in os.listdir(tasks_root):
        path = os.path.join(tasks_root, name, "task_instance.json")
        if not os.path.isfile(path):
            continue
        try:
            task = TaskInstance.load(str(workspace), name)
            if str(task.completion_status or "").lower() not in {
                "pending", "running", "planning", "executing",
            }:
                continue
            created = datetime.fromisoformat(str(task.created_at))
            compare_cutoff = cutoff
            if created.tzinfo is not None and compare_cutoff.tzinfo is None:
                created = created.replace(tzinfo=None)
            elif created.tzinfo is None and compare_cutoff.tzinfo is not None:
                created = created.replace(tzinfo=compare_cutoff.tzinfo)
            if created >= compare_cutoff:
                continue
            governance = dict((task.metadata or {}).get("manual_iteration_governance") or {})
            governance.update({
                "ok": False,
                "status": "runtime_restart_orphaned",
                "error": "execution process ended before an authoritative terminal result",
            })
            task.metadata["manual_iteration_governance"] = governance
            task.save()
            task.mark("failed", {
                "source": "manual_stop_project_finalization",
                "governance_status": "runtime_restart_orphaned",
            })
            changed += 1
        except (OSError, TypeError, ValueError):
            continue
    return changed
