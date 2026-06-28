"""Runtime cost and progress summaries for user-visible Partner status."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any


def _read_jsonl(path: str, limit: int = 80) -> list[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = [line.strip() for line in f if line.strip()]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in rows[-limit:]:
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                out.append(data)
        except Exception:
            continue
    return out


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def summarize_agent_runs(workspace: str, limit: int = 80) -> dict[str, Any]:
    rows = _read_jsonl(os.path.join(workspace, "state", "logs", "agent_runs.jsonl"), limit=limit)
    if not rows:
        return {
            "calls": 0,
            "failed": 0,
            "total_tokens_est": 0,
            "total_elapsed_ms": 0,
            "project_calls": 0,
            "report_calls": 0,
            "last_error": "",
            "last_model": "",
            "last_provider": "",
            "backend_counts": {},
            "purpose_counts": {},
            "ollama_lite": _read_json(os.path.join(workspace, "state", "ollama_lite_status.json")),
            "dynamic_ollama": _read_json(os.path.join(workspace, "state", "dynamic_ollama_status.json")),
        }
    total_tokens = sum(int(r.get("total_tokens_est") or 0) for r in rows)
    total_elapsed = sum(int(r.get("elapsed_ms") or 0) for r in rows)
    failed = [r for r in rows if str(r.get("status") or "").lower() not in {"ok", "empty"}]
    project_calls = sum(1 for r in rows if r.get("purpose") == "project")
    report_calls = sum(1 for r in rows if r.get("purpose") == "report")
    backend_counts: dict[str, int] = {}
    purpose_counts: dict[str, int] = {}
    for row in rows:
        backend = str(row.get("backend") or "unknown")
        purpose = str(row.get("purpose") or "unknown")
        backend_counts[backend] = backend_counts.get(backend, 0) + 1
        purpose_counts[purpose] = purpose_counts.get(purpose, 0) + 1
    last = rows[-1]
    last_error = ""
    for row in reversed(rows):
        if row.get("error") or str(row.get("status") or "").lower() == "failed":
            last_error = str(row.get("error") or row.get("stdout_preview") or row.get("stderr_preview") or "")[:240]
            break
    return {
        "calls": len(rows),
        "failed": len(failed),
        "total_tokens_est": total_tokens,
        "total_elapsed_ms": total_elapsed,
        "project_calls": project_calls,
        "report_calls": report_calls,
        "last_error": last_error,
        "last_model": str(last.get("model") or ""),
        "last_provider": str(last.get("provider") or ""),
        "backend_counts": backend_counts,
        "purpose_counts": purpose_counts,
        "ollama_lite": _read_json(os.path.join(workspace, "state", "ollama_lite_status.json")),
        "dynamic_ollama": _read_json(os.path.join(workspace, "state", "dynamic_ollama_status.json")),
    }


def _format_duration(ms: int) -> str:
    seconds = max(0, int(ms / 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def publish_runtime_cost_summary(workspace: str) -> dict[str, Any]:
    summary = summarize_agent_runs(workspace)
    user_dir = os.path.join(workspace, "state", "user")
    os.makedirs(user_dir, exist_ok=True)
    backend_text = ", ".join(
        f"{k}={v}" for k, v in sorted((summary.get("backend_counts") or {}).items())
    ) or "暂无"
    purpose_text = ", ".join(
        f"{k}={v}" for k, v in sorted((summary.get("purpose_counts") or {}).items())
    ) or "暂无"
    ollama = summary.get("ollama_lite") or {}
    if ollama:
        ollama_text = (
            f"{'available' if ollama.get('available') else 'unavailable'}; "
            f"model={ollama.get('model') or ''}; "
            f"url={ollama.get('base_url') or ''}; "
            f"fallback={ollama.get('fallback') or 'primary_agent'}"
            + (f"; reason={ollama.get('reason')}" if ollama.get("reason") else "")
        )
    else:
        ollama_text = "not_checked_yet"
    dynamic_ollama = summary.get("dynamic_ollama") or {}
    if dynamic_ollama:
        dynamic_text = (
            f"selected={dynamic_ollama.get('selected') or 'api_fallback'}; "
            f"url={dynamic_ollama.get('base_url') or ''}; "
            f"fallback={dynamic_ollama.get('fallback') or ''}"
            + (f"; reason={dynamic_ollama.get('reason')}" if dynamic_ollama.get("reason") else "")
        )
    else:
        dynamic_text = "not_checked_yet"
    text = (
        "# Runtime Cost\n\n"
        f"更新时间：{datetime.now().isoformat(timespec='seconds')}\n\n"
        "## 最近调用概况\n"
        f"- 最近调用数：{summary['calls']}\n"
        f"- 项目执行调用：{summary['project_calls']}\n"
        f"- 汇报调用：{summary['report_calls']}\n"
        f"- 后端分布：{backend_text}\n"
        f"- 用途分布：{purpose_text}\n"
        f"- Ollama Lite：{ollama_text}\n"
        f"- Dynamic Ollama：{dynamic_text}\n"
        f"- 失败/异常调用：{summary['failed']}\n"
        f"- 估算 token：{summary['total_tokens_est']}\n"
        f"- 累计耗时：{_format_duration(summary['total_elapsed_ms'])}\n"
        f"- 最近模型：{summary['last_provider']}/{summary['last_model']}\n\n"
        "## 最近异常\n"
        f"{summary['last_error'] or '暂无最近异常。'}\n\n"
        "说明：token 是基于字符数的粗略估算，用于观察消耗趋势，不等同于服务商账单。\n"
    )
    with open(os.path.join(user_dir, "runtime_cost.md"), "w", encoding="utf-8") as f:
        f.write(text)
    return summary


def compact_runtime_context(workspace: str) -> str:
    summary = summarize_agent_runs(workspace, limit=40)
    if not summary.get("calls"):
        return ""
    return (
        "运行消耗摘要："
        f"最近{summary['calls']}次调用，估算token {summary['total_tokens_est']}，"
        f"耗时{_format_duration(summary['total_elapsed_ms'])}，"
        f"失败{summary['failed']}次，"
        f"后端分布{summary.get('backend_counts') or {}}。"
        f"Ollama Lite={summary.get('ollama_lite') or 'not_checked_yet'}。"
        f"Dynamic Ollama={summary.get('dynamic_ollama') or 'not_checked_yet'}。"
        f"{' 最近异常：' + summary['last_error'][:120] if summary.get('last_error') else ''}"
    )
