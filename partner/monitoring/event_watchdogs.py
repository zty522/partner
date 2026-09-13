"""Non-semantic liveness watchdogs for the Event runtime.

Watchdogs observe leases and emit anomaly Events.  They never invent project
work, choose a learning topic, retry a failed business action, or send routine
user messages.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import json
import time

from partner.event_fabric import EventLedger, EventSummary


@dataclass(frozen=True)
class WatchdogFinding:
    kind: str
    instance_id: str
    headline: str
    evidence_refs: tuple[str, ...] = ()
    detail: dict[str, Any] | None = None


class EventWatchdogSuite:
    def __init__(self, workspace: str | Path):
        self.root = Path(workspace).resolve()
        self.state_path = self.root / "state/runtime/watchdog_anomalies.json"
        self.ledger = EventLedger(self.root)

    def _prior(self) -> dict[str, str]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return dict(value.get("fingerprints") or {})
        except (OSError, TypeError, ValueError):
            return {}

    def _save(self, fingerprints: Mapping[str, str]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"updated_at": time.time(), "fingerprints": fingerprints},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def inspect(self, children: Mapping[str, Any], *, lease_seconds: int = 900) -> list[WatchdogFinding]:
        now = time.time()
        findings: list[WatchdogFinding] = []
        for instance_id, process in children.items():
            if process.poll() is not None:
                findings.append(WatchdogFinding("runtime.process_exit", instance_id,
                                                f"实例 {instance_id} 进程退出",
                                                detail={"exit_code": process.returncode}))
            tasks = self.root / "instances" / instance_id / "state/tasks"
            for path in tasks.glob("*/task_instance.json") if tasks.exists() else ():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    status = str(value.get("completion_status") or value.get("status") or "")
                    updated = path.stat().st_mtime
                except (OSError, TypeError, ValueError):
                    continue
                if status.lower() in {"pending", "running", "planning", "executing"} and now - updated > lease_seconds:
                    findings.append(WatchdogFinding(
                        "runtime.semantic_progress_stale", instance_id,
                        f"实例 {instance_id} 的任务超过语义进展租约",
                        (str(path),), {"age_seconds": int(now - updated), "status": status}))
            outbound = self.root / "state/application/outbound" / instance_id
            for path in outbound.glob("*.json") if outbound.exists() else ():
                if now - path.stat().st_mtime > lease_seconds:
                    findings.append(WatchdogFinding(
                        "runtime.delivery_stale", instance_id,
                        f"实例 {instance_id} 的待交付消息超过租约", (str(path),),
                        {"age_seconds": int(now - path.stat().st_mtime)}))
        return findings

    def emit_changes(self, findings: list[WatchdogFinding]) -> list[str]:
        prior = self._prior()
        current: dict[str, str] = {}
        emitted: list[str] = []
        for finding in findings:
            key = f"{finding.kind}:{finding.instance_id}"
            body = json.dumps({"headline": finding.headline, "evidence": finding.evidence_refs,
                               "detail": finding.detail or {}}, ensure_ascii=False, sort_keys=True)
            fingerprint = sha256(body.encode("utf-8")).hexdigest()[:16]
            current[key] = fingerprint
            if prior.get(key) == fingerprint:
                continue
            event = self.ledger.create(
                finding.kind, "runtime", instance_id=finding.instance_id,
                concurrency_key=f"runtime:{finding.instance_id}",
                payload={"watchdog": True, **(finding.detail or {})},
            )
            self.ledger.complete(event, EventSummary(
                event_id=event.event_id, status="blocked", headline=finding.headline,
                outcome="watchdog 只报告异常；恢复和重试由所属 Event Flow 决定",
                evidence_refs=list(finding.evidence_refs), failure_class=finding.kind,
                mechanism="runtime/watchdog_observation", notification_kind="blocked",
                requires_human=False,
            ))
            emitted.append(event.event_id)
        self._save(current)
        return emitted

    def observe(self, children: Mapping[str, Any], *, lease_seconds: int = 900) -> list[str]:
        return self.emit_changes(self.inspect(children, lease_seconds=lease_seconds))
