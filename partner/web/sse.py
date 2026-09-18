"""Server-Sent Events stream for per-Job timeline updates (M1 / Section 10).

Uses ``rowid`` from ``job_history`` as the cursor so clients can
resume after disconnect.  Heartbeat every 5s.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

try:
    from flask import Response, stream_with_context
    _HAVE_FLASK = True
except Exception:
    _HAVE_FLASK = False


_HEARTBEAT_INTERVAL = 5.0
_POLL_INTERVAL = 1.0


def _format_sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return f"event: {event}\ndata: {payload}\n\n"


def _stream(workspace: Path, job_id: str, after: int) -> Any:
    try:
        from partner.index.job_repository import init as _init_repo
        repo = _init_repo(workspace)
    except Exception as exc:
        yield _format_sse("error", {"error": f"repo unavailable: {exc}"})
        return

    cursor = after
    last_heartbeat = time.time()
    terminal_states = {"completed", "failed", "cancelled"}
    seen_status = None

    while True:
        rows = []
        try:
            for h in repo.history(job_id):
                if h.get("seq", 0) > cursor:
                    rows.append(h)
            rows.sort(key=lambda r: r.get("seq", 0))
        except Exception as exc:
            yield _format_sse("error", {"error": f"history read: {exc}"})
            return

        for r in rows:
            cursor = max(cursor, r.get("seq", 0))
            yield _format_sse("history", r)

        record = repo.get_record(job_id)
        if record:
            current = record.get("status")
            if current != seen_status:
                yield _format_sse("status",
                                   {"status": current,
                                    "finished_at": record.get("finished_at")})
                seen_status = current
            if current in terminal_states:
                yield _format_sse("done", {"status": current})
                return

        now = time.time()
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            yield _format_sse("heartbeat", {"ts": now, "after": cursor})
            last_heartbeat = now

        time.sleep(_POLL_INTERVAL)


def make_sse_response(*, workspace: str | Path, job_id: str, after: int = 0) -> Any:
    if not _HAVE_FLASK:
        raise RuntimeError("flask not available")
    ws = Path(workspace).expanduser().resolve()
    return Response(stream_with_context(_stream(ws, job_id, after)),
                     mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache",
                               "X-Accel-Buffering": "no"})
