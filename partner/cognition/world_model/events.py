"""Append-only hash-linked events for Partner's world-model shadow path."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


class WorldModelEventLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _tail(self) -> tuple[int, str]:
        sequence, head = 0, GENESIS
        if not self.path.is_file():
            return sequence, head
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            stored = json.loads(line)
            supplied = str(stored.pop("event_hash"))
            if int(stored["sequence"]) != sequence + 1 or stored["previous_hash"] != head:
                raise ValueError("refusing to append to a broken world-model event chain")
            if _digest(stored) != supplied:
                raise ValueError("refusing to append to a tampered world-model event chain")
            sequence, head = int(stored["sequence"]), supplied
        return sequence, head

    def append(self, event_type: str, *, episode_id: str, domain_id: str,
               payload: dict[str, Any], parents: list[str] | None = None,
               evidence_refs: list[str] | None = None) -> dict[str, Any]:
        sequence, previous = self._tail()
        event = {
            "schema_version": 1, "event_id": f"wm_evt_{sequence + 1:08d}",
            "event_type": event_type,
            "occurred_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "episode_id": episode_id, "domain_id": domain_id,
            "parents": list(parents or []), "evidence_refs": list(evidence_refs or []),
            "payload": dict(payload),
        }
        record = {"sequence": sequence + 1, "previous_hash": previous, "event": event}
        record["event_hash"] = _digest(record)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return event

    def verify(self) -> dict[str, Any]:
        try:
            count, head = self._tail()
            return {"ok": True, "event_count": count, "head_hash": head}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return {"ok": False, "event_count": 0, "reason": str(exc)}
