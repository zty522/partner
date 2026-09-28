"""Per-Job, append-only human and machine readable Event execution trace.

Every semantic Event passes through :class:`EventFlowRunner`; that boundary
calls this module so handlers do not need bespoke logging.  The trace is an
observability projection, never execution truth (the Event ledger remains
authoritative).  Values are recursively redacted and bounded before writing.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import fcntl
import hashlib
import json
import os


_SECRET_PARTS = ("api_key", "apikey", "authorization", "access_token",
                 "refresh_token", "token", "password", "passwd", "secret", "cookie")
_MAX_STRING = 20_000
_MAX_ITEMS = 100
_MAX_DEPTH = 8
_DEDUP_MIN_BYTES = 4_096


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                     separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def sanitize(value: Any, *, _depth: int = 0, _key: str = "") -> Any:
    """Return a JSON-safe, credential-redacted and size-bounded projection."""
    lowered = _key.lower().replace("-", "_")
    if any(part in lowered for part in _SECRET_PARTS):
        return "[REDACTED]"
    if _depth >= _MAX_DEPTH:
        return {"truncated": True, "reason": "max_depth", "digest": _digest(value)}
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): sanitize(v, _depth=_depth + 1, _key=str(k))
                for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        seq = list(value)
        out = [sanitize(v, _depth=_depth + 1) for v in seq[:_MAX_ITEMS]]
        if len(seq) > _MAX_ITEMS:
            out.append({"truncated": True, "omitted_items": len(seq) - _MAX_ITEMS,
                        "full_digest": _digest(seq)})
        return out
    if isinstance(value, bytes):
        return {"binary": True, "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, str) and len(value) > _MAX_STRING:
        return value[:_MAX_STRING] + (
            f"\n...[TRUNCATED {len(value)-_MAX_STRING} chars; {_digest(value)}]")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def definition_dict(definition: Any) -> dict[str, Any]:
    return {
        "name": definition.name,
        "version": definition.version,
        "description": definition.description,
        "nodes": [sanitize(asdict(node)) for node in definition.nodes],
    }


class EventRunLog:
    """Concurrency-safe trace projection rooted at ``state/run_logs/<job>``."""

    def __init__(self, workspace: str | os.PathLike, job_id: str):
        root = Path(workspace).expanduser().resolve()
        if root.parent.name == "instances":
            root = root.parent.parent
        safe_job = "".join(c for c in str(job_id) if c.isalnum() or c in "_-.")
        self.job_id = safe_job or "unknown_job"
        self.directory = root / "state" / "run_logs" / self.job_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events_path = self.directory / "events.jsonl"
        self.markdown_path = self.directory / "RUN_LOG.md"
        self.lock_path = self.directory / ".lock"
        # Runner invocations construct a fresh EventRunLog for each node. A
        # process-local set therefore cannot deduplicate across real Events.
        # Digest markers provide a persistent, concurrency-safe index while
        # the Event Ledger remains the authoritative full record.
        self.digest_directory = self.directory / ".payload_digests"
        self.digest_directory.mkdir(parents=True, exist_ok=True)

    def _claim_digest(self, digest: str) -> bool:
        """Atomically claim a content digest; false means it was seen before."""
        marker = self.digest_directory / digest.removeprefix("sha256:")
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def _payload_projection(self, value: Any, digest: str) -> Any:
        """Content-address large payloads and repeated nested subtrees.

        Flow inputs grow as previous outputs are embedded under ``upstream``
        and ``flow_outputs``.  Comparing only the outer object misses that
        structural repetition because every new node adds one small field.
        Recursing after claiming the root keeps the first occurrence readable
        while later roots can reference any repeated large child.
        """
        safe = sanitize(value)

        def project(current: Any, *, known_digest: str = "") -> Any:
            encoded = json.dumps(current, ensure_ascii=False, sort_keys=True,
                                 default=str, separators=(",", ":")).encode("utf-8")
            if len(encoded) < _DEDUP_MIN_BYTES:
                return current
            current_digest = known_digest or _digest(current)
            if not self._claim_digest(current_digest):
                return {"ref": current_digest, "truncated": True,
                        "reason": "duplicate_large_payload"}
            if isinstance(current, Mapping):
                return {str(key): project(child) for key, child in current.items()}
            if isinstance(current, list):
                return [project(child) for child in current]
            return current

        return project(safe, known_digest=digest)

    def _append(self, row: Mapping[str, Any], markdown: str,
                *, once_marker: Path | None = None) -> None:
        value = {"at": _now(), "job_id": self.job_id, **sanitize(dict(row))}
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if once_marker is not None and once_marker.exists():
                return
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(value, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            new_file = not self.markdown_path.exists()
            with self.markdown_path.open("a", encoding="utf-8") as handle:
                if new_file:
                    handle.write(f"# Partner 运行日志：{self.job_id}\n\n")
                    handle.write("> 此文件是便于查看的投影；权威执行事实位于 Event Ledger。敏感字段已脱敏，大对象会截断并保留摘要。\n\n")
                handle.write(markdown.rstrip() + "\n\n")
                handle.flush()
                os.fsync(handle.fileno())
            if once_marker is not None:
                once_marker.write_text(value["at"] + "\n", encoding="utf-8")

    def intake(self, *, message: str, channel: str, sender_id: str,
               project_id: str, instance_id: str, intent_contract: Any,
               flow_id: str, definition: Any) -> None:
        plan = definition_dict(definition)
        snapshot = self.directory / f"flow_{flow_id}_{definition.version}.json"
        snapshot.write_text(json.dumps(sanitize(plan), ensure_ascii=False, indent=2),
                            encoding="utf-8")
        payload = {"kind": "run_intake", "message": message, "channel": channel,
                   "sender_id": sender_id, "project_id": project_id,
                   "instance_id": instance_id, "intent_contract": intent_contract,
                   "flow_id": flow_id, "flow_plan": plan,
                   "flow_snapshot": str(snapshot)}
        self._append(payload,
            "## 收到消息并规划 Flow\n\n"
            f"- 时间：`{_now()}`\n- 渠道：`{channel}`\n- 项目：`{project_id}`\n"
            f"- 实例：`{instance_id}`\n- Flow：`{definition.name}` `{flow_id}` v{definition.version}\n\n"
            f"### 原始消息\n\n```text\n{sanitize(message)}\n```\n\n"
            f"### Event 流\n\n```json\n{json.dumps(sanitize(plan), ensure_ascii=False, indent=2)}\n```")

    def flow_plan(self, *, flow_id: str, definition: Any,
                  reason: str = "runtime_plan") -> None:
        plan = definition_dict(definition)
        snapshot = self.directory / f"flow_{flow_id}_{definition.name}_{definition.version}.json"
        if snapshot.exists():
            return
        snapshot.write_text(json.dumps(sanitize(plan), ensure_ascii=False, indent=2),
                            encoding="utf-8")
        self._append({"kind": "flow_plan", "flow_id": flow_id, "reason": reason,
                      "flow_plan": plan, "flow_snapshot": str(snapshot)},
                     f"## Flow 计划：{definition.name}\n\n"
                     f"- 原因：`{reason}`\n- Flow ID：`{flow_id}`\n- 版本：`{definition.version}`\n\n"
                     f"```json\n{json.dumps(sanitize(plan), ensure_ascii=False, indent=2)}\n```")

    def event(self, *, phase: str, flow_id: str, flow_type: str, node_id: str,
              event_id: str, event_type: str, inputs: Any = None,
              outputs: Any = None, status: str = "", duration_ms: float | None = None,
              attempt: int = 1) -> None:
        # Background Events can be polled hundreds of times with the same
        # Event id.  One waiting transition plus the terminal output preserves
        # the Event lifecycle without turning a ten-minute run into a 200 MB
        # repetition of identical inputs and outputs.
        if phase in {'started', 'waiting'}:
            safe_event = ''.join(c for c in str(event_id) if c.isalnum() or c in '_-.')
            marker = self.directory / f'.seen_{safe_event}_{phase}'
            try:
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                return
        payload = {"kind": "event", "phase": phase, "flow_id": flow_id,
                   "flow_type": flow_type, "node_id": node_id,
                   "event_id": event_id, "event_type": event_type,
                   "attempt": attempt, "status": status,
                   "duration_ms": duration_ms}
        input_digest = _digest(inputs) if inputs is not None else None
        output_digest = _digest(outputs) if outputs is not None else None

        if inputs is not None:
            payload["input_digest"] = input_digest
            payload["input"] = self._payload_projection(inputs, input_digest)
        if outputs is not None:
            payload["output_digest"] = output_digest
            payload["output"] = self._payload_projection(outputs, output_digest)
        body = [f"## Event {phase}：{node_id}", "",
                f"- Event：`{event_type}` (`{event_id}`)",
                f"- Flow：`{flow_type}` (`{flow_id}`)", f"- 尝试：`{attempt}`"]
        if status:
            body.append(f"- 状态：`{status}`")
        if duration_ms is not None:
            body.append(f"- 耗时：`{duration_ms:.2f} ms`")
        if inputs is not None:
            body.extend(["", "### 输入", "", "```json",
                         json.dumps(sanitize(payload["input"]), ensure_ascii=False, indent=2), "```"])
        if outputs is not None:
            body.extend(["", "### 输出", "", "```json",
                         json.dumps(sanitize(payload["output"]), ensure_ascii=False, indent=2), "```"])
        self._append(payload, "\n".join(body))

    def terminal(self, *, status: str, flow_id: str, flow_type: str,
                 error: str = "", completed_nodes: Any = None,
                 failed_nodes: Any = None, skipped_nodes: Any = None,
                 final_outputs: Any = None) -> None:
        """Append one explicit Job closure record.

        Event rows prove individual checkpoints, but they did not previously
        state whether the owning Job itself had settled.  This projection is
        deliberately idempotent because a worker may observe the terminal
        boundary more than once during recovery.
        """
        payload = {
            "kind": "job_terminal", "status": status,
            "flow_id": flow_id, "flow_type": flow_type, "error": error,
            "completed_node_ids": list(completed_nodes or []),
            "failed_node_ids": list(failed_nodes or []),
            "skipped_node_ids": list(skipped_nodes or []),
            "final_outputs": final_outputs or {},
        }
        body = ["## Job 终态", "", f"- 状态：`{status}`",
                f"- Flow：`{flow_type}` (`{flow_id}`)",
                f"- 已完成节点：`{len(payload['completed_node_ids'])}`",
                f"- 失败节点：`{len(payload['failed_node_ids'])}`",
                f"- 跳过节点：`{len(payload['skipped_node_ids'])}`"]
        if error:
            body.extend(["", "### 错误", "", "```text", str(sanitize(error)), "```"])
        body.extend(["", "### 最终节点输出", "", "```json",
                     json.dumps(sanitize(final_outputs or {}), ensure_ascii=False, indent=2),
                     "```"])
        # A failed terminal may later be repaired through an explicit bounded
        # recovery.  Keep both append-only facts and let readers select the
        # latest one.  Repeated identical writes remain idempotent, while a
        # completed terminal can never be overwritten by a late failure.
        state_path = self.directory / ".job_terminal_state.json"
        signature = _digest(payload)
        state = _read_terminal_state(state_path)
        if state.get("signature") == signature:
            return
        if state.get("status") == "completed" and status != "completed":
            return
        self._append(payload, "\n".join(body))
        temporary = state_path.with_suffix(f".tmp.{os.getpid()}")
        temporary.write_text(json.dumps({"status": status, "signature": signature,
                                         "at": _now()}, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, state_path)


def _read_terminal_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


__all__ = ["EventRunLog", "sanitize", "definition_dict"]
