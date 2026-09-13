"""Picks an idle instance from a pool, by slot/load rather than project.

ADR 0099 §5: the dispatcher no longer asks "which instance owns this
project" — it asks "which instance has spare capacity right now".

Reads :class:`partner.governance.instance_scheduler` state JSON
(``<workspace>/state/instance_scheduler.json``) when available; otherwise
falls back to scanning ``instances/<id>/state/heartbeat.json`` for each
of the five instances and treating the freshest heartbeat as "alive".
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SlotPick:
    instance_id: str
    slot_id: str  # for now == instance_id; future: per-instance multi-slot
    reason: str  # human-readable explanation for audit


@dataclass
class SlotArbiter:
    """Pick idle instance.  ``workspace_root`` may be root or instance dir."""

    workspace_root: Path
    heartbeat_stale_seconds: float = 600.0  # 10 min — beyond this, treat as dead
    _root: Path = field(init=False, repr=False)

    def __post_init__(self) -> None:
        root = Path(self.workspace_root).expanduser().resolve()
        if root.parent.name == "instances":
            root = root.parent.parent
        self._root = root

    # --------------------------------------------------------- state readers

    def _scheduler_state(self) -> dict:
        """Read the canonical instance_scheduler.json if it exists."""
        path = self._root / "state" / "instance_scheduler.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return {}

    def _heartbeats(self) -> dict[str, float]:
        """Return {instance_id: last_heartbeat_epoch} for every instance dir."""
        out: dict[str, float] = {}
        inst_root = self._root / "instances"
        if not inst_root.is_dir():
            return out
        for entry in os.listdir(inst_root):
            full = inst_root / entry
            if not full.is_dir():
                continue
            hb = full / "state" / "heartbeat.json"
            if not hb.exists():
                continue
            try:
                data = json.loads(hb.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
            ts = data.get("last_heartbeat") or data.get("ts")
            if not ts:
                continue
            try:
                out[entry] = float(str(ts).replace("Z", "+00:00").replace("+00:00", ""))
            except (TypeError, ValueError):
                continue
        return out

    def _busy_set(self, scheduler: dict) -> set[str]:
        """Infer which instances are currently busy.

        Heuristic: an instance is busy if its heartbeat is fresh AND its
        current_task_id is non-empty.  Conservative — false-positive busy
        just means the arbiter picks a different instance, never that we
        starve a real worker.
        """
        busy: set[str] = set()
        for inst_id in os.listdir(self._root / "instances") if (self._root / "instances").is_dir() else []:
            full = self._root / "instances" / inst_id
            if not full.is_dir():
                continue
            hb = full / "state" / "heartbeat.json"
            if not hb.exists():
                continue
            try:
                data = json.loads(hb.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
            task_id = (data.get("current_task_id") or "").strip()
            if task_id:
                busy.add(inst_id)
        # Cross-reference with scheduler state if present
        sched_busy = set((scheduler.get("busy_instances") or []))
        return busy | sched_busy

    def _enabled_instances(self) -> list[str]:
        """Read the 5-instance fixed list (sprint36 ADR 0098)."""
        return ["01", "02", "03", "04", "05"]

    # ----------------------------------------------------------- pick logic

    def pick_idle_instance(self, *,
                           exclude: Optional[set[str]] = None,
                           now: Optional[float] = None) -> Optional[SlotPick]:
        """Return the freshest idle instance, or None if all busy / dead.

        ``exclude`` lets the caller steer away from instances that just
        rejected a job (e.g. transient LLM error).  ``now`` is for tests.
        """
        now = float(now) if now is not None else time.time()
        exclude = set(exclude or set())
        scheduler = self._scheduler_state()
        heartbeats = self._heartbeats()
        busy = self._busy_set(scheduler)
        enabled = self._enabled_instances()
        alive = [i for i in enabled
                 if i in heartbeats
                 and (now - heartbeats[i]) <= self.heartbeat_stale_seconds
                 and i not in busy
                 and i not in exclude]
        if not alive:
            return None
        # Prefer the freshest heartbeat — most recently active worker
        alive.sort(key=lambda i: heartbeats[i], reverse=True)
        winner = alive[0]
        return SlotPick(
            instance_id=winner,
            slot_id=winner,  # 1:1 with instance for now; future: per-slot
            reason=(f"freshest heartbeat among {len(alive)} idle; "
                    f"busy={sorted(busy)} excluded={sorted(exclude)}"),
        )

    def report_instance_busy(self, instance_id: str, job_id: str) -> None:
        """Append-only: append a busy marker so other readers see the lock.

        For now we just write a small JSON sidecar; future iterations may
        integrate with the central scheduler.
        """
        self._sidecar(instance_id, "busy", job_id)

    def report_instance_free(self, instance_id: str, job_id: str) -> None:
        self._sidecar(instance_id, "free", job_id)

    def _sidecar(self, instance_id: str, kind: str, job_id: str) -> None:
        """Append a line to ``<root>/state/slot_arbiter.jsonl`` for audit."""
        path = self._root / "state" / "slot_arbiter.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": time.time(),
                    "instance_id": instance_id,
                    "kind": kind,
                    "job_id": job_id,
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass


__all__ = ["SlotArbiter", "SlotPick"]
