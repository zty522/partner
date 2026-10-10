#!/usr/bin/env python3
"""Migrate legacy memory JSONL into the unified Mind Notes ledger.

Run once per workspace: reads share/mind/memory/{observations,lessons,
user_preferences,habits,beliefs,growth}.jsonl and appends each row into
share/mind/notes/notes.jsonl with the matching note type.  Idempotent:
skips rows whose source marker already exists in the note store.

Usage::
    python scripts/migration/migrate_legacy_memory.py --workspace <ws>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from partner.mind_lab.notes import NoteStore  # noqa: E402

KIND_TO_FILE = {
    "observation": "observations.jsonl",
    "lesson": "lessons.jsonl",
    "user_preference": "user_preferences.jsonl",
    "habit": "habits.jsonl",
    "belief": "beliefs.jsonl",
    "growth": "growth.jsonl",
}
KIND_TO_TYPE = {"user_preference": "preference"}
STATUS_MAP = {"habit": "candidate", "growth": "confirmed",
              "lesson": "open", "belief": "open",
              "user_preference": "active", "observation": "recorded"}
CONFIDENCE_STR = {"high": 0.8, "medium": 0.6, "low": 0.3}


def _confidence(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return CONFIDENCE_STR.get(str(value).strip().lower(), 0.5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()
    ws = Path(args.workspace).resolve()
    memory_dir = (ws.parent.parent if ws.parent.name == "instances" else ws) / "share/mind/memory"
    store = NoteStore(str(ws))
    existing_sources = {
        str(n.get("source") or "") for n in store.list_all()
        if str(n.get("source") or "").startswith("memory:")
    }
    total = 0
    skipped = 0
    for kind, filename in KIND_TO_FILE.items():
        path = memory_dir / filename
        if not path.exists():
            continue
        note_type = KIND_TO_TYPE.get(kind, kind)
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                marker = f"memory:{kind}:{row.get('record_id') or row.get('event_id') or row.get('headline') or ''}"
                if marker in existing_sources:
                    skipped += 1
                    continue
                content = str(row.get("content") or row.get("headline") or row.get("outcome") or "").strip()
                if not content:
                    skipped += 1
                    continue
                status = str(row.get("status") or STATUS_MAP.get(kind, "open"))
                if kind == "observation":
                    # legacy rows carry Event status (completed/failed), note
                    # observation only records the fact — keep the original
                    # status in the source marker for auditability.
                    status = "recorded"
                if kind == "growth" and status not in {"confirmed", "dismissed"}:
                    # a candidate growth is not a confirmed growth; keep it as
                    # an observation rather than falsely confirming it.
                    note_type = "observation"
                    status = "recorded"
                try:
                    store.append(note_type, {
                        "content": content,
                        "source": marker,
                        "confidence": _confidence(row.get("confidence")),
                        "evidence_refs": list(row.get("evidence_refs") or []),
                        "project_id": str(row.get("project_id") or ""),
                        "scope": str(row.get("scope") or ""),
                        "layer": str(row.get("layer") or "task_specific"),
                        "status": status,
                        "created_at": str(row.get("recorded_at") or ""),
                    })
                    existing_sources.add(marker)
                    total += 1
                except Exception as exc:
                    print(f"skip {kind}: {str(exc)[:120]}")
    print(f"migrated {total} rows, skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
