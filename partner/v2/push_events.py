"""Verified user file delivery event.

Appending a JSONL record is not delivery. Files are sent through the runtime's
configured channel callback, which is the same path used by normal reports.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_AUTO_FILE_PREFIXES = ("login", "screenshot", "browser", "xhs_", "xiaohongshu")
_AUTO_FILE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf")


def _working_dir(ctx: Any) -> str:
    task = getattr(ctx, "task_instance", None)
    path = getattr(task, "working_dir", "") if task is not None else ""
    if not path:
        path = getattr(ctx, "working_dir", "") or getattr(ctx, "project_dir", "") or ""
    if not path and isinstance(ctx, dict):
        path = str(ctx.get("working_dir") or ctx.get("project_dir") or "")
    return os.path.abspath(path) if path else ""


def _resolve_source(ctx: Any, source: str) -> tuple[str, str]:
    """Resolve an explicit source, or the newest current-task visual artifact."""
    workdir = _working_dir(ctx)
    if source:
        candidate = source if os.path.isabs(source) else os.path.join(workdir, source)
        return os.path.abspath(candidate), "explicit"

    if not workdir or not os.path.isdir(workdir):
        return "", "current task working directory is unavailable"
    candidates = []
    for entry in Path(workdir).iterdir():
        if not entry.is_file():
            continue
        lower = entry.name.lower()
        if lower.endswith(_AUTO_FILE_EXTENSIONS) and lower.startswith(_AUTO_FILE_PREFIXES):
            candidates.append(str(entry))
    if not candidates:
        return "", "no login/screenshot/xhs visual artifact exists in the current task"
    return max(candidates, key=os.path.getmtime), "current_task_auto"


def _deliver_one(path: str, caption: str) -> dict:
    from partner.mind import executor as mind_executor

    result = mind_executor.push_file_now(path, caption)
    result.setdefault("path", path)
    result.setdefault("name", os.path.basename(path))
    return result


def atomic_push_files(ctx, params: dict) -> dict:
    """Deliver files and report actual channel acknowledgements.

    Auto-discovery is intentionally limited to the current task directory. An
    older task's artifact requires an explicit path.
    """
    source, provenance = _resolve_source(ctx, str(params.get("source") or ""))
    caption = str(params.get("caption") or "")
    if not source:
        return {"ok": False, "pushed": 0, "total": 0, "status": "missing", "error": provenance}
    if not os.path.exists(source):
        return {"ok": False, "pushed": 0, "total": 0, "status": "missing", "error": f"source not found: {source}"}

    if os.path.isfile(source):
        paths = [source]
    elif os.path.isdir(source):
        paths = [
            os.path.join(source, name)
            for name in sorted(os.listdir(source))
            if os.path.isfile(os.path.join(source, name)) and os.path.getsize(os.path.join(source, name)) > 0
        ]
    else:
        return {"ok": False, "pushed": 0, "total": 0, "status": "invalid", "error": f"not a file or directory: {source}"}

    results = [_deliver_one(path, caption or os.path.basename(path)) for path in paths]
    pushed = sum(1 for item in results if item.get("delivered") is True)
    ok = bool(results) and pushed == len(results)
    status = "sent" if ok else ("partial" if pushed else "failed")
    response = {
        "ok": ok,
        "status": status,
        "pushed": pushed,
        "total": len(results),
        "provenance": provenance,
        "source": source,
        "results": results,
        "files": paths if ok else [],
    }
    if not ok:
        response["error"] = f"delivery acknowledged for {pushed}/{len(results)} files"
    logger.info("[PUSH-FILES] status=%s acknowledged=%d/%d source=%s", status, pushed, len(results), source)
    try:
        from partner.evolution.evolution_log import log_evolution
        log_evolution("user_file_delivery", detail={"status": status, "pushed": pushed, "total": len(results), "path": source})
    except Exception:
        pass
    return response


__all__ = ["atomic_push_files"]
