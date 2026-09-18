#!/usr/bin/env python3
"""Materialise benchmark inputs (M3 / Section 6.4).

Static-implemented. Real preparation (clones, downloads, license checks)
must run in a separate verification pass with the network and license
contract explicitly approved.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _resolve_repo_root() -> Path:
    cur = Path(__file__).resolve().parent
    for _ in range(6):
        if (cur / "pyproject.toml").exists() and (cur / "partner").exists():
            return cur
        cur = cur.parent
    return REPO


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark-workspace", required=True)
    ap.add_argument("--fixtures-dir", default=str(REPO / "partner/benchmark/fixtures"))
    args = ap.parse_args()

    bench_root = Path(args.benchmark_workspace)
    inputs_root = bench_root / "inputs"
    inputs_root.mkdir(parents=True, exist_ok=True)

    manifest = {"copied": [], "missing_external": []}
    import importlib
    fixtures_pkg = "partner.benchmark.fixtures"
    repo_root = _resolve_repo_root()
    sys.path.insert(0, str(repo_root))
    for py in Path(args.fixtures_dir).glob("*.py"):
        if py.name in ("__init__.py", "_base.py"):
            continue
        mod = importlib.import_module(fixtures_pkg + "." + py.stem)
        for relpath, body in mod.ORACLE_INPUTS.items():
            target = inputs_root / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            manifest["copied"].append(str(target.relative_to(bench_root)))
        for relpath in mod.TASK_DEFINITION.get("inputs", []):
            if not (inputs_root / relpath).exists():
                manifest["missing_external"].append(relpath)
    print(json.dumps(manifest, indent=2))
    return 0 if not manifest["missing_external"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
