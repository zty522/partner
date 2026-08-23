from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .task_instance import TaskInstance


JsonDict = dict[str, Any]


@dataclass
class ExpectedArtifact:
    type: str
    pattern: str
    description: str = ""
    required: bool = True


@dataclass
class ArtifactValidationResult:
    ok: bool
    found: list[JsonDict] = field(default_factory=list)
    missing: list[JsonDict] = field(default_factory=list)


class ArtifactValidator:
    def __init__(self, config: JsonDict | None = None) -> None:
        self.config = config or {}

    def validate(self, task: TaskInstance) -> ArtifactValidationResult:
        found: list[JsonDict] = []
        missing: list[JsonDict] = []
        root = os.path.abspath(task.working_dir)
        for raw in task.expected_artifacts or []:
            expected = ExpectedArtifact(
                type=str(raw.get("type") or "file").lower(),
                pattern=str(raw.get("pattern") or "").strip(),
                description=str(raw.get("description") or ""),
                required=bool(raw.get("required", True)),
            )
            if expected.type == "message":
                if self._message_requirement_satisfied(task, expected):
                    found.append({"type": "message", "pattern": expected.pattern, "description": expected.description})
                elif expected.required:
                    missing.append({
                        "type": expected.type,
                        "pattern": expected.pattern,
                        "description": expected.description,
                    })
                continue
            matches = self._file_matches(root, expected.pattern)
            if matches:
                found.append({
                    "type": expected.type,
                    "pattern": expected.pattern,
                    "description": expected.description,
                    "paths": matches,
                    "provenance": "current_task",
                    "task_root": root,
                })
            elif expected.required:
                missing.append({
                    "type": expected.type,
                    "pattern": expected.pattern,
                    "description": expected.description,
                })
        ok = not missing
        task.append_log("artifact_validation", {"ok": ok, "found": found, "missing": missing})
        return ArtifactValidationResult(ok=ok, found=found, missing=missing)

    def _message_requirement_satisfied(self, task: TaskInstance, expected: ExpectedArtifact) -> bool:
        """Only planning messages need proof in the task log.

        A plain direct reply can still satisfy a generic message artifact.  For
        planning-stage requirements such as "下一步 event 计划", however, a
        failed planner must not pass validation just because the expected type
        is "message"; otherwise remediation reports look like successful plans.
        """
        label = f"{expected.pattern} {expected.description}".lower()
        if not re.search(r"(下一步|next|plan|计划|event)", label, re.I):
            return True
        try:
            with open(task.log_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return False
        if not text.strip():
            return False
        if re.search(r'"event"\s*:\s*"harness_plan"', text):
            return True
        if re.search(r'"event"\s*:\s*"plan_executed"', text):
            return True
        return False

    def _file_matches(self, root: str, pattern: str) -> list[str]:
        if not pattern:
            return []
        paths = []
        seen: set[str] = set()
        # Acceptance is task-local. A global screenshot directory can make an
        # old task's image satisfy a new task's requirements.
        search_roots = [root]
        # Selector/planner outputs sometimes express alternatives as
        # "*.csv, *.xlsx". Treat those as one requirement with multiple
        # acceptable patterns, not as one literal glob and not as two required
        # artifacts.
        patterns = [p.strip() for p in re.split(r"[,，;；]+", pattern) if p.strip()]
        for item in patterns or [pattern]:
            for sr in search_roots:
                full_pattern = item if os.path.isabs(item) else os.path.join(sr, item)
                # 图片类 pattern（*.png 等）同时接受 jpg/jpeg/webp——web_capture 压缩输出 jpg 是已知行为
                matched_any = False
                for path in glob.glob(full_pattern, recursive=True):
                    try:
                        full = os.path.abspath(path)
                        if not (full == sr or full.startswith(sr + os.sep)):
                            continue
                        if self._is_internal_artifact(full):
                            continue
                        if os.path.isfile(full) and os.path.getsize(full) > 0 and full not in seen:
                            seen.add(full)
                            paths.append(full)
                            matched_any = True
                    except Exception:
                        pass
                if not matched_any and os.path.basename(item).lower().endswith(
                        (".png", ".jpg", ".jpeg", ".webp", ".gif")):
                    # 尝试常见图片扩展名（如 *.png → *.jpg/*.jpeg/*.webp/*.gif）
                    for alt_ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                        alt = item[:-4] + alt_ext if item.lower().endswith(
                            (".png", ".jpg", ".jpeg", ".webp", ".gif")) else item
                        for path in glob.glob(os.path.join(sr, alt), recursive=True):
                            try:
                                full = os.path.abspath(path)
                                if not (full == sr or full.startswith(sr + os.sep)):
                                    continue
                                if self._is_internal_artifact(full):
                                    continue
                                if os.path.isfile(full) and os.path.getsize(full) > 0 and full not in seen:
                                    seen.add(full)
                                    paths.append(full)
                            except Exception:
                                pass
        return sorted(paths)

    def _is_internal_artifact(self, path: str) -> bool:
        name = os.path.basename(path or "").lower()
        parts = set(os.path.normpath(path or "").split(os.sep))
        if name.startswith("_step_") and name.endswith(".result.json"):
            return True
        if name in {"_missing_artifacts.md", "_error_report.md", "batch_plan_status.md", "batch_plan_context.md"}:
            return True
        if "fallback" in name or "missing_artifacts" in name or "error_report" in name:
            return True
        return "fallbacks" in parts
