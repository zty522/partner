from __future__ import annotations

import os
import logging
from datetime import datetime
from typing import Any

from .task_instance import TaskInstance


JsonDict = dict[str, Any]
logger = logging.getLogger(__name__)

# Minimum content length for a fallback to be considered "meaningful".
# Files smaller than this (e.g. 271-byte placeholders) are treated as empty
# and will NOT satisfy artifact requirements.
MIN_FALLBACK_CONTENT_LENGTH = 500


class RemediationHandler:
    def __init__(self, config: JsonDict | None = None) -> None:
        self.config = config or {}

    def remediate(
        self,
        *,
        task: TaskInstance,
        missing: list[JsonDict],
        failures: list[JsonDict] | None = None,
        fallback_paths: list[str] | None = None,
        reason: str = "",
    ) -> JsonDict:
        remediation_cfg = self.config.get("remediation") or {}
        accepted_fallbacks: list[str] = []
        fallback_outputs: list[JsonDict] = []
        if remediation_cfg.get("accept_nonempty_placeholder", True):
            for path in fallback_paths or []:
                if path and os.path.isfile(path) and os.path.getsize(path) >= MIN_FALLBACK_CONTENT_LENGTH:
                    accepted_fallbacks.append(path)
                    content = self._read_fallback(path) if self._read_on_fallback() else ""
                    if content:
                        fallback_outputs.append({
                            "path": path,
                            "content": content,
                            "is_fallback": True,
                            "status": "fallback_success",
                        })
                        message = f"[REMEDIATION] fallback file exists, treating as success, content length={len(content)}"
                        logger.info(message)
                        task.append_log("remediation_fallback_success", {
                            "message": message,
                            "path": path,
                            "content_length": len(content),
                        })
        task.append_log("remediation_triggered", {
            "reason": reason,
            "missing": missing,
            "failures": failures or [],
            "accepted_fallbacks": accepted_fallbacks,
            "fallback_outputs": [
                {"path": item.get("path"), "content_length": len(str(item.get("content") or ""))}
                for item in fallback_outputs
            ],
        })
        if accepted_fallbacks:
            materialized = self._materialize_fallback_artifacts(task, missing, fallback_outputs)
            report = self._write_missing_report(task, missing, failures or [], accepted_fallbacks, reason)
            task.mark("partial", {"via_fallback": accepted_fallbacks, "materialized_fallback_artifacts": materialized, "report": report})
            return {
                "ok": True,
                "status": "partial",
                "report_path": report,
                "accepted_fallbacks": accepted_fallbacks,
                "fallback_outputs": fallback_outputs,
                "materialized_artifacts": materialized,
            }
        report = self._write_error_report(task, missing, failures or [], reason)
        task.mark("failed", {"report": report})
        return {"ok": False, "status": "failed", "report_path": report, "accepted_fallbacks": []}

    def _read_on_fallback(self) -> bool:
        fallback = (self.config.get("external_calls") or {}).get("fallback") or {}
        return bool(fallback.get("read_on_fallback", True))

    def _read_fallback(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def _materialize_fallback_artifacts(
        self,
        task: TaskInstance,
        missing: list[JsonDict],
        fallback_outputs: list[JsonDict],
    ) -> list[str]:
        content = "\n\n".join(
            str(item.get("content") or "").strip()
            for item in fallback_outputs or []
            if isinstance(item, dict) and str(item.get("content") or "").strip()
        ).strip()
        if not content:
            return []
        paths: list[str] = []
        for item in missing or []:
            if str(item.get("type") or "").strip().lower() != "file":
                continue
            rel = self._artifact_name_from_pattern(str(item.get("pattern") or ""))
            if not rel:
                continue
            path = os.path.abspath(os.path.join(task.working_dir, rel))
            root = os.path.abspath(task.working_dir)
            if not (path == root or path.startswith(root + os.sep)):
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            title = item.get("description") or rel
            body = (
                f"# Partial Fallback Artifact\n\n"
                f"- task_id: {task.task_id}\n"
                f"- status: partial\n"
                f"- expected_artifact: {rel}\n"
                f"- description: {title}\n\n"
                "外部调用未能完整完成。以下内容来自 fallback 草案，作为部分交付物继续推进；"
                "其中缺失的数据和证据需要后续补全。\n\n"
                "## Fallback Content\n\n"
                f"{content.rstrip()}\n"
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            paths.append(path)
            task.append_log("fallback_artifact_materialized", {
                "path": path,
                "content_length": len(body),
                "source": "remediation",
            })
        return paths

    def _artifact_name_from_pattern(self, pattern: str) -> str:
        raw = str(pattern or "").strip().strip("/\\")
        if not raw:
            return ""
        first = raw.replace("，", ",").replace("；", ";").split(",")[0].split(";")[0].strip()
        if not first:
            return ""
        if any(ch in first for ch in "*?[]"):
            ext = os.path.splitext(first.replace("*", "artifact"))[1] or ".md"
            return f"partial_fallback_artifact{ext}"
        return first

    def _write_missing_report(
        self,
        task: TaskInstance,
        missing: list[JsonDict],
        failures: list[JsonDict],
        accepted_fallbacks: list[str],
        reason: str,
    ) -> str:
        name = str((self.config.get("remediation") or {}).get("missing_report_name") or "_missing_artifacts.md")
        path = os.path.join(task.working_dir, name)
        self._write_report(path, task, missing, failures, reason, accepted_fallbacks, "Partial Artifact Report")
        return path

    def _write_error_report(
        self,
        task: TaskInstance,
        missing: list[JsonDict],
        failures: list[JsonDict],
        reason: str,
    ) -> str:
        name = str((self.config.get("remediation") or {}).get("error_report_name") or "_error_report.md")
        path = os.path.join(task.working_dir, name)
        self._write_report(path, task, missing, failures, reason, [], "Error Report")
        return path

    def _write_report(
        self,
        path: str,
        task: TaskInstance,
        missing: list[JsonDict],
        failures: list[JsonDict],
        reason: str,
        accepted_fallbacks: list[str],
        title: str,
    ) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = [
            f"# {title}",
            "",
            f"- task_id: {task.task_id}",
            f"- created_at: {task.created_at}",
            f"- generated_at: {datetime.now().isoformat()}",
            f"- status: {task.completion_status}",
            f"- reason: {reason or 'artifact validation failed'}",
            "",
            "## User Message",
            "",
            task.user_message or "EMPTY",
            "",
            "## Missing Artifacts",
            "",
        ]
        if missing:
            for item in missing:
                lines.append(f"- {item.get('type')} {item.get('pattern')}: {item.get('description') or ''}".rstrip())
        else:
            lines.append("- None")
        lines.extend(["", "## Failures", ""])
        if failures:
            for item in failures:
                lines.append(f"- {item.get('event_type') or item.get('step_id') or 'event'}: {item.get('error') or item}")
        else:
            lines.append("- None recorded")
        lines.extend(["", "## Accepted Fallbacks", ""])
        if accepted_fallbacks:
            lines.extend(f"- {path}" for path in accepted_fallbacks)
        else:
            lines.append("- None")
        lines.extend([
            "",
            "## Manual Recovery",
            "",
            "请补充缺失数据源或稍后重试外部调用；本任务不会继续全局重新规划，以避免失败循环。",
        ])
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")
