"""Run Log — 代码写入/运行日志（供检查"方案是否真实落地"）。

记录 execute_code / run_command 等代码类事件的运行详情到
{workspace}/state/logs/code_runs.jsonl，字段：
ts, event, workdir, script, exit_code, ok, stdout_preview, stderr_preview
"""
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


def log_code_run(workspace: str = "", *, event: str, workdir: str = "",
                 script: str = "", exit_code: int = 0, ok: bool = True,
                 stdout: str = "", stderr: str = "", error: str = "") -> str:
    """记录一次代码写入/运行事件。返回日志文件路径（失败返回空串）。"""
    try:
        root = workspace or ""
        if not root:
            from ..api_log import workspace_root_from_pointer
            root = workspace_root_from_pointer() or ""
        if not root:
            return ""
        log_dir = os.path.join(root, "state", "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "code_runs.jsonl")
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "workdir": workdir,
            "script": os.path.basename(script) if script else "",
            "script_path": script,
            "exit_code": exit_code,
            "ok": bool(ok),
            "stdout_preview": str(stdout or "")[:500],
            "stderr_preview": str(stderr or "")[:300],
            "error": str(error or "")[:300],
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except Exception as exc:
        logger.debug("log_code_run failed: %s", exc)
        return ""


def recent_code_runs(workspace: str = "", limit: int = 20) -> list[dict]:
    """读取最近代码运行记录（供检查用）。"""
    try:
        root = workspace or ""
        if not root:
            from ..api_log import workspace_root_from_pointer
            root = workspace_root_from_pointer() or ""
        path = os.path.join(root, "state", "logs", "code_runs.jsonl") if root else ""
        if not path or not os.path.exists(path):
            return []
        out = []
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out[-limit:]
    except Exception:
        return []
