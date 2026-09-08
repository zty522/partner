"""Inbox-triggered execution of partner learn_from_hermes.

This module lets Partner instances automatically run
`learn_from_hermes.main()` when a marker entry appears in their
`desktop_inbox.jsonl`. It does NOT replace the standard inbox poller; it is
a separate background thread that scans the same file for a specific marker
type.

Marker format (one JSONL line in desktop_inbox.jsonl):
    {
        "id": "<unique-id>",
        "source": "learn_trigger",
        "text": "/learn_from_hermes",
        "workspace": "<optional: partner workspace path>",
        "source_root": "<optional: hermes_external_learning path>",
        "dry_run": false
    }

When the poller sees this marker:
  1. Marks the entry as seen (so it does not fire repeatedly)
  2. Invokes `partner.learn.learn_from_hermes.main(argv)` with the
     provided args (or defaults).
  3. Writes a result line to `share/mind/governance/learn_trigger_log.jsonl`
     for audit.  Each line records: id, timestamp, exit_code, stdout_tail.

The poller is intentionally narrow: it only triggers on the specific marker
format above.  Other inbox entries are left untouched for the normal
desktop_inbox poller to handle.

Honesty:
    - This module is OFF by default.  The `start_learn_trigger_poller()`
      function must be called explicitly (e.g. by an opt-in boot hook).
    - It does NOT modify the standard inbox poller or any production flow.
    - Triggers are idempotent: a marker is consumed exactly once.
    - A failing learn run does NOT crash the poller; the error is logged.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default paths can be overridden via env vars / CLI args.
_DEFAULT_WORKSPACE = "/mnt/e/work/partner_workspace"
_DEFAULT_SOURCE_ROOT = str(Path(__file__).resolve().parents[2] /
                           "docs/knowledge/incubated_external_learning")
_TRIGGER_SOURCE = "learn_trigger"
_TRIGGER_TEXT = "/learn_from_hermes"
_LOG_FILENAME = "learn_trigger_log.jsonl"
_SLEEP_SECONDS = 2.0


def _log_path(workspace: str) -> Path:
    root = Path(workspace) / "share" / "mind" / "governance"
    root.mkdir(parents=True, exist_ok=True)
    return root / _LOG_FILENAME


def _seen_path(workspace: str) -> Path:
    root = Path(workspace) / "share" / "mind" / "governance"
    root.mkdir(parents=True, exist_ok=True)
    return root / "learn_trigger_seen_ids.json"


def _load_seen_ids(workspace: str) -> set[str]:
    path = _seen_path(workspace)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(item) for item in data}
    except (OSError, ValueError, TypeError):
        pass
    return set()


def _save_seen_ids(workspace: str, seen: set[str]) -> None:
    path = _seen_path(workspace)
    path.write_text(json.dumps(sorted(seen), ensure_ascii=False),
                     encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")


def _append_log(workspace: str, record: dict[str, Any]) -> None:
    path = _log_path(workspace)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _is_trigger(entry: dict[str, Any]) -> bool:
    return (
        str(entry.get("source") or "") == _TRIGGER_SOURCE
        and str(entry.get("text") or "").strip() == _TRIGGER_TEXT
    )


def _run_learn_marker(workspace: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Invoke learn_from_hermes.main() with args from the marker entry."""
    from partner.learn.learn_from_hermes import main as lfh_main

    args: list[str] = [
        "--workspace", str(entry.get("workspace") or workspace),
    ]
    if entry.get("source_root"):
        args.extend(["--source", str(entry["source_root"])])
    if entry.get("dry_run"):
        args.append("--dry-run")

    # Capture stdout by redirecting sys.stdout.
    import io
    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buf
    exit_code = -1
    error: str | None = None
    try:
        exit_code = lfh_main(args)
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else -1
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    finally:
        sys.stdout = real_stdout

    return {
        "id": entry.get("id") or "",
        "timestamp": _now_iso(),
        "exit_code": exit_code,
        "stdout_tail": buf.getvalue()[-2000:],
        "error": error,
        "args": args,
    }


def start_learn_trigger_poller(
    workspace: str = _DEFAULT_WORKSPACE,
    *,
    inbox_path: str | None = None,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Start a background thread that polls inbox + runs learn_from_hermes on triggers.

    Returns the Thread object.  Caller is responsible for keeping the thread
    alive (e.g. by joining, or by relying on the daemon flag + process exit).

    Args:
        workspace: Partner workspace root (default: /mnt/e/work/partner_workspace).
        inbox_path: optional explicit inbox path.  If None, derived from
                   `<workspace>/instances/04/state/desktop_inbox.jsonl`.
                   We default to 04 because 04 is the literature instance.
        stop_event: optional threading.Event to signal graceful shutdown.
    """
    inbox = Path(inbox_path) if inbox_path else (
        Path(workspace) / "instances" / "04" / "state" / "desktop_inbox.jsonl"
    )
    seen = _load_seen_ids(workspace)
    stop_event = stop_event or threading.Event()

    def _loop() -> None:
        while not stop_event.is_set():
            try:
                if inbox.exists():
                    for raw_line in inbox.read_text(encoding="utf-8").splitlines():
                        stripped = raw_line.strip()
                        if not stripped:
                            continue
                        try:
                            entry = json.loads(stripped)
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(entry, dict):
                            continue
                        msg_id = str(entry.get("id") or entry.get("message_id") or "")
                        if not msg_id or msg_id in seen:
                            continue
                        if not _is_trigger(entry):
                            # Don't consume non-trigger entries — leave them
                            # for the normal desktop_inbox poller.
                            continue
                        seen.add(msg_id)
                        _save_seen_ids(workspace, seen)
                        record = _run_learn_marker(workspace, entry)
                        _append_log(workspace, record)
            except Exception as exc:
                _append_log(workspace, {
                    "timestamp": _now_iso(),
                    "error": f"poller_loop: {type(exc).__name__}: {exc}",
                })
            # Use Event.wait so we can be interrupted promptly.
            stop_event.wait(_SLEEP_SECONDS)

    thread = threading.Thread(target=_loop, name="learn-trigger-poller", daemon=True)
    thread.start()
    return thread


def trigger_learn_now(
    workspace: str = _DEFAULT_WORKSPACE,
    *,
    source_root: str | None = _DEFAULT_SOURCE_ROOT,
    dry_run: bool = False,
) -> str:
    """Synchronous one-shot trigger: append a marker to inbox + run learn_from_hermes.

    Used for tests and manual scripts that don't want to spin up a thread.
    Writes a marker to the configured inbox and invokes the run function.
    Returns the marker id.
    """
    inbox = Path(workspace) / "instances" / "04" / "state" / "desktop_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    marker_id = f"learn_trigger_{int(time.time() * 1000)}"
    marker = {
        "id": marker_id,
        "source": _TRIGGER_SOURCE,
        "text": _TRIGGER_TEXT,
        "timestamp": _now_iso(),
        "workspace": workspace,
        "source_root": source_root,
        "dry_run": dry_run,
    }
    with inbox.open("a", encoding="utf-8") as f:
        f.write(json.dumps(marker, ensure_ascii=False) + "\n")
    record = _run_learn_marker(workspace, marker)
    _append_log(workspace, record)
    return marker_id


if __name__ == "__main__":  # pragma: no cover
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", default=_DEFAULT_WORKSPACE)
    p.add_argument("--source-root", default=_DEFAULT_SOURCE_ROOT)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--async", dest="async_run", action="store_true",
                   help="Start a background poller instead of one-shot run.")
    args = p.parse_args()
    if args.async_run:
        t = start_learn_trigger_poller(args.workspace)
        print(f"poller started on {args.workspace}, thread={t.name}, daemon={t.daemon}")
        print("press Ctrl-C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("stopping...")
    else:
        marker_id = trigger_learn_now(
            args.workspace, source_root=args.source_root, dry_run=args.dry_run,
        )
        print(f"triggered marker {marker_id}; see log at {args.workspace}/share/mind/governance/learn_trigger_log.jsonl")
