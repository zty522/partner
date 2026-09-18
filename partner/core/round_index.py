"""Round artifact index (M1 / Section 8 / round 5).

Provides indexed read access to ``project_dir/rounds/round_NNN_*``
without scanning the directory tree.  Round entries are written
through ``record_round`` whenever a round completes; older code that
left them on disk is still readable via ``list_rounds`` which falls
back to ``os.listdir`` only as a recovery path.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


_ROUND_PATTERN = re.compile(r"round_(\d{3})_(.*)$")


def record_round(*, project_dir: str | Path, round_num: int, label: str,
                  files: list[Path] | None = None,
                  metrics: dict | None = None,
                  summary: str | None = None,
                  workspace_root: str | Path | None = None) -> Path:
    """Append a round record to the project index file.

    The index lives at ``<project_dir>/rounds/.round_index.jsonl``.
    """
    pdir = Path(project_dir)
    idx = pdir / "rounds" / ".round_index.jsonl"
    idx.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "round": round_num,
        "label": label,
        "metrics": dict(metrics or {}),
        "summary": (summary or "")[:2000],
        "files": [str(f) for f in (files or [])],
        "schema_version": "round_index/v1",
    }
    with idx.open("a", encoding="utf-8") as fh:
        import fcntl as _fcntl
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
    return idx


def list_rounds(*, project_dir: str | Path) -> list[dict]:
    pdir = Path(project_dir)
    idx = pdir / "rounds" / ".round_index.jsonl"
    out = []
    if idx.exists():
        for line in idx.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    if out:
        return out
    # Recovery fallback: listdir (maintenance-only path).
    rounds_dir = pdir / "rounds"
    if not rounds_dir.is_dir():
        return out
    for dname in os.listdir(rounds_dir):
        m = _ROUND_PATTERN.match(dname)
        if not m:
            continue
        out.append({"round": int(m.group(1)),
                     "label": m.group(2),
                     "metrics": {},
                     "summary": "",
                     "files": [],
                     "schema_version": "round_index/v1",
                     "recovered_from": "listdir"})
    return sorted(out, key=lambda r: r.get("round", 0))


def read_round(*, project_dir: str | Path, round_num: int) -> dict:
    """Return one round's indexed record.  Falls back to ``os.listdir``
    only if the index file is missing."""
    pdir = Path(project_dir)
    idx = pdir / "rounds" / ".round_index.jsonl"
    if idx.exists():
        for line in idx.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("round") == round_num:
                return rec
    # Maintenance-only fallback
    rounds_dir = pdir / "rounds"
    if not rounds_dir.is_dir():
        return {"round": round_num, "label": "", "metrics": {},
                 "summary": "", "files": [], "schema_version": "round_index/v1"}
    files = []
    for dname in os.listdir(rounds_dir):
        if dname.startswith(f"round_{round_num:03d}"):
            round_dir = rounds_dir / dname
            for root, dirs, fnames in os.walk(round_dir):
                for fname in fnames:
                    files.append(str(Path(root) / fname))
    return {"round": round_num, "label": "",
             "metrics": {}, "summary": "", "files": files,
             "schema_version": "round_index/v1",
             "recovered_from": "listdir"}


__all__ = ["record_round", "list_rounds", "read_round"]
