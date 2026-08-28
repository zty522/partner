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
