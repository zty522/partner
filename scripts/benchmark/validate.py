#!/usr/bin/env python3
"""Validate benchmark suite + fixture integrity (M3 / Section 6.4).

Real validation path:

* Loads the suite JSON and asserts it can be parsed.
* For each ``task_families`` entry, loads the fixture module by its
  dotted path (``partner.benchmark.fixtures.<family>``) and asserts
  ``TASK_DEFINITION`` / ``ORACLE_INPUTS`` / ``ORACLE_EXPECTED`` /
  ``run()`` are present.
* Loads each protocol referenced by ``baseline_arms`` / ``ablation_arms``
  via the protocol registry (no filename guessing).

State honesty: ``static_implemented``.  No validation has been
executed in this session.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _check_fixture(repo_root: Path, family: str) -> list[str]:
    errors = []
    dotted = f"partner.benchmark.fixtures.{family}"
    sys.path.insert(0, str(repo_root))
    try:
        mod = importlib.import_module(dotted)
    except Exception as exc:
        return [f"fixture {dotted!r} failed to import: {exc}"]
    for required in ("TASK_DEFINITION", "ORACLE_INPUTS", "ORACLE_EXPECTED"):
        if not hasattr(mod, required):
            errors.append(f"fixture {family!r} missing {required!r}")
    if not callable(getattr(mod, "run", None)):
        errors.append(f"fixture {family!r} run() is not callable")
    if not hasattr(mod, "STAGES"):
        errors.append(f"fixture {family!r} missing STAGES list")
    return errors


def _check_protocol(repo_root: Path, protocol_id: str) -> list[str]:
    sys.path.insert(0, str(repo_root))
    try:
        from partner.benchmark.protocol_registry import resolve_path
        path = resolve_path(repo_root, protocol_id)
    except FileNotFoundError as exc:
        return [str(exc)]
    if not path.exists():
        return [f"protocol_id={protocol_id!r} resolves to missing path {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"protocol {protocol_id!r} JSON parse failed: {exc}"]
    if data.get("schema_version") != "research.protocol/v1":
        return [f"protocol {protocol_id!r} has wrong schema_version"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", required=True, help="Path to suite.json")
    ap.add_argument("--fixtures-dir", default=str(REPO / "partner/benchmark/fixtures"))
    args = ap.parse_args()

    suite_path = Path(args.suite)
    if not suite_path.exists():
        print(json.dumps({"ok": False, "error": f"missing suite file: {suite_path}"}))
        return 1
    try:
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"suite JSON parse failed: {exc}"}))
        return 1

    errors: list[str] = []
    families = suite.get("task_families", []) or []
    for family in families:
        errors.extend(_check_fixture(REPO, family))

    for arm_key in ("baseline_arms", "ablation_arms"):
        for arm in suite.get(arm_key, []) or []:
            protocol_id = arm.get("protocol_id") or arm.get("protocol") or "PCI-H1H4-v1"
            errors.extend(_check_protocol(REPO, protocol_id))

    if errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2, ensure_ascii=False))
        return 1
    print(json.dumps({
        "ok": True,
        "suite": suite.get("suite_id"),
        "families": families,
        "protocols_checked": list({a.get("protocol_id", "PCI-H1H4-v1")
                                    for k in ("baseline_arms", "ablation_arms")
                                    for a in suite.get(k, []) or []}),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
