"""Append-only storage for Core v1 decisions, settlements and transitions."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import json
import os

from .models import CoreDecisionRecord, CoreSettlement, canonical_json


class CoreStore:
    def __init__(self, workspace: str | Path) -> None:
        root = Path(workspace).resolve()
        if root.parent.name == "instances":
            root = root.parent.parent
        self.workspace_root = root
        self.root = root / "state/core_v1"
        self.root.mkdir(parents=True, exist_ok=True)
        from partner.index.runtime_storage import workspace_dir
        self.db_path = workspace_dir(root) / "core_v1.db"
        self._ensure_index()

    def _ensure_index(self) -> None:
        from partner.index.sqlite_base import get_connection
        connection = get_connection(self.db_path)
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS core_transitions (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS core_transitions_recent
                ON core_transitions(seq DESC);
        """)

    def _write_once(self, path: Path, value: Mapping[str, Any]) -> Path:
        body = canonical_json(value) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != body:
                raise RuntimeError(f"frozen Core v1 artifact differs: {path}")
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
        temporary.write_text(body, encoding="utf-8")
        temporary.replace(path)
        return path

    def save_decision(self, record: CoreDecisionRecord) -> Path:
        return self._write_once(self.root / "decisions" / record.decision_id / "commitment.json",
                                record.to_dict())

    def load_decision(self, decision_id: str) -> dict[str, Any]:
        return json.loads((self.root / "decisions" / decision_id / "commitment.json").read_text(encoding="utf-8"))

    def save_settlement(self, settlement: CoreSettlement) -> Path:
        return self._write_once(self.root / "decisions" / settlement.decision_id /
                                f"{settlement.settlement_id}.json", settlement.to_dict())

    def append_transition(self, value: Mapping[str, Any]) -> Path:
        from partner.index.sqlite_base import get_connection
        body = canonical_json(value)
        decision_id = str(value.get("decision_id") or "")
        if not decision_id:
            raise ValueError("Core transition requires decision_id")
        connection = get_connection(self.db_path)
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO core_transitions(decision_id,payload_json) VALUES (?,?)",
                (decision_id, body))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        if cursor.rowcount == 0:
            existing = connection.execute(
                "SELECT payload_json FROM core_transitions WHERE decision_id=?", (decision_id,)
            ).fetchone()
            if existing is None or str(existing[0]) != body:
                raise RuntimeError(f"transition differs for frozen decision {decision_id}")
        # The per-decision projection is atomic and independently addressable;
        # concurrent workers never append to the same large JSONL on drvfs.
        return self._write_once(
            self.root / "decisions" / decision_id / "transition.json", dict(value))

    def recent_transitions(self, limit: int = 500) -> list[dict[str, Any]]:
        from partner.index.sqlite_base import get_connection
        count = max(1, min(int(limit), 500))
        rows = get_connection(self.db_path).execute(
            "SELECT payload_json FROM core_transitions ORDER BY seq DESC LIMIT ?", (count,)
        ).fetchall()
        values = []
        for row in reversed(rows):
            try:
                value = json.loads(row[0])
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                values.append(value)
        return values
