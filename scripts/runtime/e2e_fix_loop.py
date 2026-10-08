#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auto-fix loop on top of e2e_regression.py.

When the end-to-end regression fails, this script:
  1. re-runs e2e_regression and captures the failure report,
  2. matches the failure signatures against registered fix rules
     (scripts/runtime/e2e_fix_rules/*.json),
  3. for each matched rule: backs up the target file, applies the patch,
     runs the rule's focused tests, then re-runs e2e_regression;
     if the re-run still fails, restores the backup (rollback),
  4. writes a fix report under state/e2e/fixes/<stamp>/ and returns:
       0 = e2e green (no fix needed or fixed and verified),
       3 = failure with no matching rule (human intervention needed),
       4 = matched rule applied but e2e still failed after rollback.

Rule JSON schema (scripts/runtime/e2e_fix_rules/<id>.json):
{
  "id": "verify_business_delta",
  "match_errors": ["verify not green", "business_delta"],   // OR over signatures
  "patch": {"file": "partner/events/project.py",
            "old": "...exact old text...",
            "new": "...exact new text..."},
  "tests": ["benchmark/runtime/test_closed_loop_effect_contract.py"],
  "summary": "outcome_verify reads business_delta from execute"
}
match_errors may be empty only if patch is null (diagnostic-only rule).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = "/mnt/e/work/partner"
RULES_DIR = Path(REPO) / "scripts/runtime/e2e_fix_rules"
BACKUP_DIR = Path(REPO) / "scripts/runtime/fix_backups"
REPORT_BASE = Path("/mnt/e/work/partner_workspace/state/e2e/fixes")
E2E = Path(REPO) / "scripts/runtime/e2e_regression.py"
PY = "/home/os/miniconda3/bin/python"


def load_rules() -> list[dict]:
    if not RULES_DIR.exists():
        return []
    rules = []
    for p in sorted(RULES_DIR.glob("*.json")):
        try:
            rules.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"[warn] bad rule file {p}: {exc}")
    return rules


def match_rules(errors: list[str], rules: list[dict]) -> list[dict]:
    joined = " || ".join(errors).lower()
    hits = []
    for rule in rules:
        sigs = [s.lower() for s in rule.get("match_errors", [])]
        if sigs and any(s in joined for s in sigs):
            hits.append(rule)
    return hits


def apply_patch(rule: dict, backup_dir: Path) -> bool:
    patch = rule.get("patch")
    if not patch:
        return True  # diagnostic-only rule
    target = Path(REPO) / patch["file"]
    if not target.exists():
        print(f"[fail] patch target missing: {target}")
        return False
    text = target.read_text(encoding="utf-8")
    old = patch["old"]
    if old not in text:
        print(f"[fail] anchor not found in {patch['file']}")
        return False
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup_dir / f"{target.name}.{rule['id']}.bak")
    target.write_text(text.replace(old, patch["new"], 1), encoding="utf-8")
    print(f"[ok] applied {rule['id']} -> {patch['file']}")
    return True


def run_tests(tests: list[str]) -> bool:
    for t in tests:
        r = subprocess.run([PY, "-m", "pytest", t, "-q", "--no-header"],
                           cwd=REPO, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            print(f"[fail] focused tests {t}: rc={r.returncode}\n{r.stdout[-800:]}")
            return False
    return True


def run_e2e(args: list[str]) -> int:
    r = subprocess.run([PY, str(E2E)] + args,
                       cwd=REPO, capture_output=True, text=True, timeout=3600)
    print(r.stdout[-3000:])
    return r.returncode


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-rounds", type=int, default=3)
    p.add_argument("--timeout-min", type=float, default=50.0)
    p.add_argument("--no-e2e-args", action="store_true",
                   help="debug: skip e2e and only test rule matching")
    args = p.parse_args(argv)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    fix_dir = REPORT_BASE / stamp
    fix_dir.mkdir(parents=True, exist_ok=True)
    e2e_args = ["--max-rounds", str(args.max_rounds),
                "--timeout-min", str(args.timeout_min)]

    rc = run_e2e(e2e_args)
    if rc == 0:
        print("e2e green: no fix needed")
        return 0
    if rc in (1, 2):
        print(f"e2e infrastructure failure rc={rc}; cannot auto-fix, see report")
        return rc

    # rc == 3: assertions failed -> collect report
    reports = sorted((Path("/mnt/e/work/partner_workspace/state/e2e")).glob("*/*.json"))
    if not reports:
        print("[fail] no e2e report found")
        return 3
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    errors = report.get("errors", [])
    rules = load_rules()
    hits = match_rules(errors, rules)
    record = {"stamp": stamp, "job_id": report.get("job_id"),
              "errors": errors, "matched_rules": [r["id"] for r in hits]}
    (fix_dir / "diagnosis.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    if not hits:
        print("no fix rule matches; human intervention needed.\n"
              f"diagnosis: {fix_dir / 'diagnosis.json'}")
        return 3

    for rule in hits:
        print(f"== rule {rule['id']}: {rule.get('summary', '')}")
        if not apply_patch(rule, BACKUP_DIR):
            record.setdefault("failures", []).append(rule["id"] + ": patch apply failed")
            continue
        tests = rule.get("tests", [])
        if tests and not run_tests(tests):
            print(f"[rollback] tests failed for {rule['id']}")
            _rollback(rule, BACKUP_DIR)
            record.setdefault("failures", []).append(rule["id"] + ": focused tests failed")
            continue
        print(f"[ok] focused tests passed for {rule['id']}; re-running e2e")
        rc2 = run_e2e(e2e_args)
        if rc2 != 0:
            print(f"[rollback] e2e still failing after {rule['id']}")
            _rollback(rule, BACKUP_DIR)
            record.setdefault("failures", []).append(rule["id"] + ": e2e still failing")
            continue
        print(f"[ok] rule {rule['id']} verified end-to-end")
        record.setdefault("fixed", []).append(rule["id"])
        return 0

    (fix_dir / "fix_report.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"fix report: {fix_dir / 'fix_report.json'}")
    return 4


def _rollback(rule: dict, backup_dir: Path) -> None:
    patch = rule.get("patch")
    if not patch:
        return
    target = Path(REPO) / patch["file"]
    bak = backup_dir / f"{target.name}.{rule['id']}.bak"
    if bak.exists():
        shutil.copy2(bak, target)
        print(f"[rollback] restored {target}")


if __name__ == "__main__":
    raise SystemExit(main())
