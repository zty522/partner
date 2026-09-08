#!/usr/bin/env python3
"""Sprint18 §6 follow-up: project self-drive daemon.

For each enabled partner instance this daemon, every ``--interval-seconds``:
    1. ensures the instance's ``state/instance.pid``-locked state has
       ``pending_message_id`` cleared so ``recover_or_start`` can dispatch a
       fresh self-driven task;
    2. computes a role-specific project request (xhs draft / molecule run /
       partner_framework_frontend review / arxiv digest / production_canary
       audit) using ``_next_project_request`` semantics;
    3. writes a single row into that instance's ``desktop_inbox.jsonl``
       with ``source=instance_native`` so the executor poller picks it up;
    4. records ``project_self_drive_emitted`` events to the governance ledger.

The daemon never modifies partner/<pkg>/ business code; it only talks to the
shared ``partner_workspace/state`` JSON files the runtime already understands.
Idempotent: re-running the same tick does not duplicate an inbox row
because each row carries a unique ``message_id`` and the executor's seen_ids
list prevents replay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, "/mnt/e/work/partner")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s project_self_drive %(levelname)s %(message)s",
)
logger = logging.getLogger("project_self_drive")


WORKSPACE = Path(os.environ.get("PARTNER_WORKSPACE", "/mnt/e/work/partner_workspace"))

ROLE_PROJECT = {
    "01": "01实例原生项目续跑_项目_小红书账户推送与维护 最新",
    "02": "02实例原生项目续跑_项目_分子生成方法创新与实践 最新",
    "03": "03实例原生项目续跑_项目_Partner 框架与前端优化",
    "04": "04实例原生项目续跑_项目_文献与代码学习 最新",
    "05": "05实例原生项目续跑_项目_自进化研究 最新",
}

ROLE_PROJECT_GOAL = {
    "01": "为小红书账户撰写与维护至少一篇新推文草稿并记录在 share/projects/partner01_xiaohongshu/governance/receipts 下；必须有可验证的真实外部动作（写文件）。",
    "02": "在 share/projects/partner02_molgen 下执行或重跑一次分子生成实验并落 external_artifacts；不能仅写 continuation.md。",
    "03": "在 share/projects/partner03_framework 下推进一步 Partner 框架真改动（如 partner/governance/*.py 修一行 + pytest -q 摘要）或外部检索命中。",
    "04": "在 share/projects/partner04_lit 上检索并消化 1 篇 arxiv 文献，落到 external_sources/<paper_id>.md；不能用占位文字。",
    "05": "在 share/projects/agent_self_evolution 下运行一次 production_readiness 评估或 apply_pipeline 真合并尝试，落 receipts/<id>.json。",
}

DEFAULT_INTERVAL = 180
DEFAULT_PROJECT_ID = "agent_self_evolution"

_stop = False


def _stop_signal(*_: object) -> None:
    global _stop
    _stop = True
    logger.info("project_self_drive: stop signal received")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _message_id(instance_id: str, project_id: str, kind: str) -> str:
    h = hashlib.sha256(f"{instance_id}|{project_id}|{kind}|{time.time() // 60}".encode()).hexdigest()[:16]
    return f"selfdrive_{instance_id}_{kind}_{h}"


def _load_state(workspace: Path, instance_id: str) -> dict[str, Any]:
    path = workspace / "instances" / instance_id / "state" / "native_state.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(workspace: Path, instance_id: str, state: dict[str, Any]) -> None:
    path = workspace / "instances" / instance_id / "state" / "native_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_inbox(
    workspace: Path, instance_id: str, project_id: str, message_id: str,
    text: str, kind: str,
) -> bool:
    inbox = workspace / "instances" / instance_id / "state" / "desktop_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": message_id, "message_id": message_id, "role": "user",
        "text": text, "content": text,
        "source": "instance_native", "channel": "local",
        "kind": kind, "project_id": project_id,
        "sender_id": f"partner_{instance_id}_self_drive",
        "sender_name": f"Partner{instance_id}项目自驱循环",
        "created_at": _now_iso(),
        "self_drive": True,
    }
    with inbox.open("a", encoding="utf-8") as f:
        import fcntl
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return True


def _append_event(workspace: Path, payload: dict[str, Any], subject_id: str) -> None:
    p = workspace / "share" / "mind" / "governance" / "evolution_events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    seq = 0
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                last_line = ""
                for line in f:
                    line = line.strip()
                    if line:
                        last_line = line
                if last_line:
                    rec = json.loads(last_line)
                    prev_hash = rec.get("event_hash", "")
                    seq = rec.get("seq", 0)
        except Exception:
            prev_hash = ""
    seq += 1
    record = {
        "schema_version": 1, "seq": seq, "event_type": "project_self_drive_emitted",
        "occurred_at": _now_iso(), "actor": "project_self_drive",
        "subject_id": subject_id, "project_id": "agent_self_evolution",
        "parents": [], "payload": payload, "evidence_refs": [],
        "prev_hash": prev_hash,
    }
    body = json.dumps(record, ensure_ascii=False, sort_keys=True)
    record["event_hash"] = hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _project_root(workspace: Path, project_id: str) -> Path:
    return workspace / "share" / "projects" / project_id


# ── Bug #56 layer 2 (2026-09-05): self-drive dedup guard ──
# Real failure: 03 instance was repeatedly injected with the same
# "【03实例自动续跑】" task every 4 minutes for ~50 minutes (seq 333→342
# in event_pipeline.jsonl) because Hermes token plan was exhausted and
# every batch_plan call returned HTTP 429. The runtime reported
# "Batch planner returned invalid JSON" and self-drive kept emitting
# fresh inbox rows without checking whether the same project had been
# failing in a tight loop.
#
# This guard reads the instance's recent event_pipeline.jsonl and skips
# the self-drive emit when too many task_failed events for this project
# happened in the recent window. A pause ledger is written so operators
# can see the dedup trigger fired and why.
#
# See ADR 0063.
DEFAULT_FAILURE_WINDOW_MINUTES = 15
DEFAULT_FAILURE_THRESHOLD = 3
PAUSE_LEDGER_NAME = "auto_drive_pause.json"


def _recent_task_failure_count(workspace: Path, instance_id: str,
                                project_id: str,
                                window_minutes: int = DEFAULT_FAILURE_WINDOW_MINUTES,
                                now: datetime | None = None) -> tuple[int, str, bool]:
    """Count task_failed events for this project in the recent window
    AND detect whether any task_succeeded happened in the same window.

    Returns (failure_count, last_reason, recent_success).  Bug #58
    P1.1 fix (ADR 0066): the dedup guard used to fire purely on
    failure count, which stranded instances whose latest task
    actually succeeded but earlier failures still fell inside the
    window.  ``recent_success=True`` lets the caller skip dedup.
    """
    pipeline = workspace / "instances" / instance_id / "state" / "event_pipeline.jsonl"
    if not pipeline.exists():
        return 0, "", False
    now = now or datetime.now(timezone.utc).astimezone()
    cutoff = now.timestamp() - window_minutes * 60
    failure_count = 0
    last_reason = ""
    recent_success = False
    try:
        # Read tail first (most recent events); cheap enough on a few-MB file.
        with pipeline.open("rb") as f:
            try:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                # Read up to last 256 KB — covers hundreds of recent events.
                read_size = min(size, 256 * 1024)
                f.seek(size - read_size)
                tail = f.read().decode("utf-8", errors="replace")
            except OSError:
                tail = pipeline.read_text(encoding="utf-8", errors="replace")
        last_reason_in_window = ""
        for line in tail.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = rec.get("type")
            if event_type not in {"task_failed", "task_succeeded"}:
                continue
            # Match project by inspecting event context. task_failed rows in
            # event_pipeline.jsonl do not always carry project_id directly,
            # so we additionally match by request substring of the most
            # recently built task title to keep this safe across schema
            # variations.
            ts_str = str(rec.get("ts") or "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                # If ts is missing or unparseable, fall back to file mtime so
                # we still respect the window.
                try:
                    ts = pipeline.stat().st_mtime
                except OSError:
                    ts = now.timestamp()
            if ts < cutoff:
                continue
            msg = str(rec.get("msg") or "")
            # Heuristic: this project is the one whose title contains
            # ROLE_PROJECT[<instance_id>] substring.  A more rigorous
            # approach would thread project_id through event_pipeline,
            # but that requires touching multiple writers; this heuristic
            # is sufficient for the dedup guard because the same instance
            # only has one self-drive project at a time.
            title = ROLE_PROJECT.get(instance_id, "")
            if title and title not in msg:
                # Fall back to substring of project_id alone
                if project_id not in msg:
                    continue
            if event_type == "task_succeeded":
                recent_success = True
                # don't early-break — we still want to count failures
                # that came AFTER a recent success (e.g. the success
                # happened, then a fresh failure happened on the next
                # task). The caller decides what to do with the
                # combination; we just report all the data.
                continue
            failure_count += 1
            last_reason_in_window = msg[:200]
        if last_reason_in_window:
            last_reason = last_reason_in_window
    except OSError:
        return 0, "", False
    return failure_count, last_reason, recent_success


def _pause_path(workspace: Path, instance_id: str) -> Path:
    return workspace / "instances" / instance_id / "state" / PAUSE_LEDGER_NAME


def _write_pause_ledger(workspace: Path, instance_id: str, project_id: str,
                         failure_count: int, threshold: int,
                         window_minutes: int, last_reason: str) -> None:
    """Record that self-drive was paused for this instance due to
    repeated task failures. Operator can read this file to understand
    why the instance stopped receiving self-driven tasks."""
    path = _pause_path(workspace, instance_id)
    try:
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing.update({
            "instance_id": instance_id,
            "project_id": project_id,
            "paused_at": _now_iso(),
            "window_minutes": window_minutes,
            "failure_threshold": threshold,
            "observed_failure_count": failure_count,
            "last_failure_reason": last_reason,
            "status": "paused",
        })
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as exc:
        logger.warning("could not write pause ledger %s: %s", path, exc)


def _build_request(instance_id: str, project_id: str) -> str:
    """Compose a self-driven request referencing real existing artifacts only."""
    title = ROLE_PROJECT.get(instance_id, f"{instance_id}实例原生项目续跑_项目_最新")
    goal = ROLE_PROJECT_GOAL.get(instance_id, f"推进项目 {project_id} 一步。")
    paths = []
    candidates = [
        _project_root(WORKSPACE, project_id) / "project_brief.md",
        _project_root(WORKSPACE, project_id) / "state.md",
        _project_root(WORKSPACE, project_id) / "governance/project_state.json",
        WORKSPACE / "share" / "mind" / "governance" / "evolution_events.jsonl",
    ]
    for p in candidates:
        if p.exists():
            paths.append(str(p))
    sources = "\n".join(f"- {p}" for p in paths) if paths else "(no project files yet)"
    return (
        f"【{instance_id}实例自动续跑】项目：{title}\n"
        f"目标：{goal}\n"
        f"请从下面真实存在的路径开始读取：\n{sources}\n\n"
        f"【硬约束·真实外部动作】本步必须包含至少一项可验证的真实外部动作，"
        f"并把对应真实产物写到非 share/projects/{project_id}/reports 的真实目录。"
        f"actions_executed 必须显式记录 'exec:python3 X.py'、'web.fetch:URL'、"
        f"'pytest:test_xxx'、'atomic_write_file:path' 之类真实动作，不能只是 "
        f"'read_file' 或 'generate_text'。\n\n"
        f"[instance_native=true] [native_kind=project] [project_id={project_id}]\n"
        f"[self_drive=true] [emitted_at={_now_iso()}]"
    )


def _reset_pending(workspace: Path, instance_id: str, project_id: str) -> bool:
    """Clear stale pending_message_id so recover_or_start can dispatch fresh."""
    state = _load_state(workspace, instance_id)
    if not state.get("pending_message_id"):
        return False
    state["pending_message_id"] = ""
    state["pending_kind"] = ""
    state["phase"] = "WAIT_TASK"
    state["reason"] = "self-drive reset: pending cleared for self-driven dispatch"
    _save_state(workspace, instance_id, state)
    return True


def run_once(workspace: Path) -> dict[str, Any]:
    """One self-drive tick: write 5 fresh self-driven inbox rows."""
    summary = {"emitted": [], "resets": [], "skipped": []}
    project_id = os.environ.get("PARTNER_DEFAULT_PROJECT", DEFAULT_PROJECT_ID)
    # Bug #56 layer 2 (ADR 0063): self-drive dedup guard threshold
    # is environment-tunable so canary / production can pick different
    # values without code change.
    window_minutes = int(os.environ.get(
        "PARTNER_SELF_DRIVE_FAILURE_WINDOW_MINUTES",
        DEFAULT_FAILURE_WINDOW_MINUTES,
    ))
    threshold = int(os.environ.get(
        "PARTNER_SELF_DRIVE_FAILURE_THRESHOLD",
        DEFAULT_FAILURE_THRESHOLD,
    ))
    for instance_id in ["01", "02", "03", "04", "05"]:
        try:
            if _reset_pending(workspace, instance_id, project_id):
                summary["resets"].append(instance_id)

            state = _load_state(workspace, instance_id)
            if state.get("phase") == "BLOCKED":
                summary["skipped"].append(f"{instance_id}:BLOCKED")
                continue

            # Bug #56 layer 2 (ADR 0063): if this instance has been failing
            # the same project in a tight loop, do NOT emit a fresh
            # self-drive row. The runtime cannot recover from a quota /
            # adapter / network problem by being injected with the same
            # task more times — it just produces more QQ noise.
            failure_count, last_reason, recent_success = _recent_task_failure_count(
                workspace, instance_id, project_id,
                window_minutes=window_minutes,
            )
            # Bug #58 P1.1 fix (ADR 0066): only pause if there were
            # failures in the window AND no successful task in the
            # same window.  Without this check, an instance whose
            # latest task actually succeeded but older failures still
            # fell inside the window would be silently paused.
            if failure_count >= threshold and not recent_success:
                _write_pause_ledger(
                    workspace, instance_id, project_id,
                    failure_count=failure_count, threshold=threshold,
                    window_minutes=window_minutes, last_reason=last_reason,
                )
                summary["skipped"].append(
                    f"{instance_id}:DEDUP({failure_count} failures in {window_minutes}min)"
                )
                logger.warning(
                    "self-drive dedup: instance=%s skipped, %d failures in last %d min, "
                    "last_reason=%s",
                    instance_id, failure_count, window_minutes, last_reason[:120],
                )
                continue

            kind = "learning" if state.get("phase") in ("WAIT_LEARNING", "LEARNING_TRIGGERED") else "project"
            mid = _message_id(instance_id, project_id, kind)
            text = _build_request(instance_id, project_id)

            if _write_inbox(workspace, instance_id, project_id, mid, text, kind):
                summary["emitted"].append(f"{instance_id}:{mid[:20]}")
                _append_event(
                    workspace,
                    {
                        "instance_id": instance_id, "message_id": mid,
                        "kind": kind, "project_id": project_id,
                        "reset_pending": instance_id in summary["resets"],
                        "request_chars": len(text),
                    },
                    subject_id=f"selfdrive:{instance_id}:{mid[:12]}",
                )
        except Exception as exc:
            summary["skipped"].append(f"{instance_id}:exc:{exc}")
            logger.exception("self_drive tick %s failed: %s", instance_id, exc)
    return summary


def run_loop(workspace: Path, *, interval_seconds: int = DEFAULT_INTERVAL,
             max_iterations: int | None = None) -> int:
    signal.signal(signal.SIGINT, _stop_signal)
    signal.signal(signal.SIGTERM, _stop_signal)
    logger.info("project_self_drive started: workspace=%s interval=%ss",
                workspace, interval_seconds)
    iter_count = 0
    while not _stop:
        try:
            summary = run_once(workspace)
            logger.info("tick=%d emitted=%d resets=%d skipped=%d",
                        iter_count, len(summary["emitted"]),
                        len(summary["resets"]), len(summary["skipped"]))
            if summary["emitted"]:
                logger.info("  emitted details: %s", ", ".join(summary["emitted"]))
            if summary["resets"]:
                logger.info("  resets:         %s", ", ".join(summary["resets"]))
            if summary["skipped"]:
                logger.info("  skipped:        %s", ", ".join(summary["skipped"]))
        except Exception as exc:
            logger.exception("tick %d crashed: %s", iter_count, exc)
        iter_count += 1
        if max_iterations is not None and iter_count >= max_iterations:
            break
        for _ in range(int(interval_seconds)):
            if _stop:
                break
            time.sleep(1)
    logger.info("project_self_drive stopped after %d iterations", iter_count)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("--max-iterations", type=int, default=None)
    args = parser.parse_args()
    return run_loop(WORKSPACE, interval_seconds=args.interval_seconds,
                   max_iterations=args.max_iterations)


if __name__ == "__main__":
    raise SystemExit(main())
