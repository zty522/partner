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
            # Some legacy validators expose a human-readable missing reason
            # instead of an artifact contract.  It can be reported, but must
            # never be treated as a dict-shaped file specification.
            if not isinstance(item, dict):
                continue
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
        # ADR 0043 P0-A.6: error reports must NOT blame the user when the
        # failure came from internal output-reference contract, retry loop, or
        # event-handler bugs.  Determine the predominant failure_owner across
        # all failures and route the recovery message accordingly.
        owner_counts = {}
        for _f in failures or []:
            owner_counts[_f.get("failure_owner") or ""] = owner_counts.get(_f.get("failure_owner") or "", 0) + 1
        predominant_owner = max(owner_counts.items(), key=lambda x: x[1])[0] if owner_counts else ""
        if predominant_owner in ("output_reference", "planner_contract"):
            recovery_msg = (
                "Partner 内部生成产物引用合同未解析（task 输入已正确）。"
                "已在 harness 层引入 typed reference 和任务目录沙箱化解析；"
                "若仍失败，说明需要新一轮 bounded repair，请把本任务交由主动学习诊断。"
            )
        elif predominant_owner == "environment":
            recovery_msg = (
                "运行环境异常导致失败，请检查工作目录、依赖与权限；"
                "不是用户输入引起的，Partner 无法自动恢复此类故障。"
            )
        elif predominant_owner == "delivery":
            recovery_msg = (
                "产物已生成但未送达用户通道，请检查网络和发送通道；"
                "不是用户输入引起的，Partner 无法自动恢复此类故障。"
            )
        else:
            recovery_msg = (
                "已生成部分产物（如 Markdown），但 PDF 阶段失败。"
                "本错误归因为内部 reference 或 event-handler，不是用户输入问题；"
                "若需补充数据，请先确认中间产物确实存在再发送。"
            )
        lines.extend([
            "",
            "## Manual Recovery",
            "",
            recovery_msg,
        ])
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")
