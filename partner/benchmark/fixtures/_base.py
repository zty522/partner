"""Shared fixture infrastructure (M3 / Section 6.3).

The fixture modules under ``partner.benchmark.fixtures.*`` all share
the same prepare / oracle / score contract.  This module holds the
boilerplate so each fixture is small enough to read end-to-end.

Every fixture exposes:

* ``prepare_initial_state(workspace, task_id)`` — write task inputs.
* ``reference_oracle(workspace, task_id) -> dict`` — compute expected
  outputs into ``reference/<task_id>/``.
* ``score(workspace, task_id, agent_dir, reference_paths) -> dict`` —
  compare Agent's outputs against reference outputs.
* ``TASK_DEFINITION`` / ``ORACLE_INPUTS`` / ``ORACLE_EXPECTED`` —
  schema-valid fixture metadata.

State honesty: ``static_implemented``.  Every fixture is synthetic.
"""
from __future__ import annotations

import csv as _csv
import hashlib as _hl
import json as _json
from pathlib import Path


class StageError(AssertionError):
    pass


def read_csv(path: Path):
    if not path.exists():
        raise StageError(f"missing: {path}")
    with path.open(encoding="utf-8") as f:
        return list(_csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise StageError("write_csv: empty rows")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def read_jsonl(path: Path):
    if not path.exists():
        raise StageError(f"missing: {path}")
    with path.open(encoding="utf-8") as f:
        return [_json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(_json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(obj, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def read_json(path: Path):
    return _json.loads(path.read_text(encoding="utf-8"))


def fingerprint_file(path: Path) -> str:
    h = _hl.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_files(agent_path: Path, reference_path: Path) -> bool:
    """Semantic artefact comparison (numeric tolerance, key-order free).

    Delegates to ``semantic_equal`` so JSON/JSONL/CSV artefacts score on
    value equality rather than byte equality, falling back to byte-ish
    comparison only for unstructured text.
    """
    if not agent_path.exists() or not reference_path.exists():
        return False
    return semantic_equal(agent_path, reference_path)


def aggregate_score(per_output_pass: dict) -> dict:
    """Distil per-stage pass/fail into a score dict."""
    if not per_output_pass:
        return {"matched": 0, "expected": 0, "score": None}
    matched = sum(1 for v in per_output_pass.values() if v == "pass")
    expected = len(per_output_pass)
    return {"matched": matched, "expected": expected,
            "score": (matched / expected) if expected else None,
            "stages": per_output_pass}




def _norm_value(v):
    """数值容差归一化：int/float 统一到 6 位小数，数字字符串解析为数值。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 6)
    if isinstance(v, str):
        s = v.strip()
        try:
            return round(float(s), 6)
        except (ValueError, TypeError):
            return s
    if isinstance(v, list):
        return [_norm_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm_value(val) for k, val in sorted(v.items())}
    return v


def semantic_equal(agent_path: Path, reference_path: Path) -> bool:
    """Semantic equality across JSON / JSONL / CSV artefacts.

    * JSON / JSONL: parse and compare with numeric tolerance + key-order
      independence (int 1 == float 1.0).
    * CSV: parse rows and compare cell values with numeric tolerance.
    * Falls back to byte comparison only for non-structured text, so a
      structured artefact with correct values but different whitespace /
      key order / int-vs-float still scores as correct.
    """
    if not agent_path.exists() or not reference_path.exists():
        return False
    try:
        a_raw = agent_path.read_text(encoding="utf-8", errors="replace")
        r_raw = reference_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    suffix = agent_path.suffix.lower()

    if suffix == ".jsonl":
        def _rows(s):
            out = []
            for line in s.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(_norm_value(_json.loads(line)))
                except ValueError:
                    out.append(_norm_value(line))
            return sorted(out, key=lambda x: _json.dumps(x, sort_keys=True))
        return _rows(a_raw) == _rows(r_raw)

    if suffix == ".json":
        try:
            a = _norm_value(_json.loads(a_raw))
            r = _norm_value(_json.loads(r_raw))
            return a == r
        except ValueError:
            return a_raw.strip() == r_raw.strip()

    if suffix == ".csv":
        def _cells(s):
            rows = []
            for line in s.splitlines():
                line = line.strip()
                if not line:
                    continue
                rows.append([_norm_value(c) for c in line.split(",")])
            return rows
        return _cells(a_raw) == _cells(r_raw)

    # unstructured text: byte-ish comparison (strip trailing whitespace)
    return a_raw.strip() == r_raw.strip()

__all__ = ["StageError", "read_csv", "write_csv", "read_jsonl", "write_jsonl",
            "write_json", "read_json", "fingerprint_file", "compare_files",
            "aggregate_score", "semantic_equal"]
