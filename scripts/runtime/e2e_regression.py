#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-command end-to-end regression for Partner instance runs.

Replaces the manual "QQ message -> user -> MainAgent inspects" loop:
  - submits a fixed-regime message headlessly (no QQ needed) unless
    --job-id is given (re-assert an already-completed job),
  - polls the authoritative jobs.db until the job terminates or times out,
  - asserts the full effect contract (rounds completed, verify green,
    PDF produced, user-readable messages),
  - writes a machine-readable report and returns a nonzero exit code on
    any assertion failure.

Usage (WSL):
    /home/os/miniconda3/bin/python scripts/runtime/e2e_regression.py \
        [--instance 01] [--project-id literature_github_learning] \
        [--max-rounds 3] [--timeout-min 50] [--request-id-prefix e2e] \
        [--job-id <existing_job_id>] [--jobs-db <path>]

Exit codes: 0 = all assertions passed; 1 = submit failed;
2 = job not completed in time; 3 = assertions failed (see report).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = "/mnt/e/work/partner"
WORKSPACE = "/mnt/e/work/partner_workspace"
DEFAULT_MESSAGE = (
    "【手动回归测试】在 literature_github_learning 项目下验证三阶段能力，"
    "有界运行 3 轮，结束时生成 PDF 报告并经 QQ 投递。"
    "停止条件：3 轮结束或已冻结 baseline 即停。"
)
INTERNAL_WORDS = [
    "flow_", "event_", "sha256", "content_hash", "bytes=",
    "business_delta", "corpus_ready", "input_elig", "consumption_valid",
    "execution_verified", "artifact_verified", "handoff", "round_000",
    "execution_summary", "input_consumption", ".json", "benchmark_result",
    "budget_guard", "iteration_budget", "target_consistency", "verification_links",
    "semantic_output", "node_outputs", "runtime_trace_token", "flow_id",
]


def find_jobs_db() -> str:
    cands = glob.glob("/home/os/.local/share/partner/runtime/*/jobs.db")
    if not cands:
        cands = glob.glob("/mnt/e/work/partner_workspace/**/jobs.db", recursive=True)
    if not cands:
        raise SystemExit("jobs.db not found; pass --jobs-db explicitly")
    return max(cands, key=os.path.getmtime)


def job_status(db: str, job_id: str) -> str:
    con = sqlite3.connect(db)
    try:
        row = con.execute("select status from jobs where job_id=?", (job_id,)).fetchone()
        return row[0] if row else "unknown"
    finally:
        con.close()


def iter_round_records(job_id: str) -> list:
    """Round records live in state/cycles/<job>/iterations/round_*.json in the
    current flow-record layout; older jobs wrote them at the top level."""
    base = Path(WORKSPACE) / "state/cycles" / job_id
    its = base / "iterations"
    if its.exists():
        files = sorted(its.glob("round_*.json"))
        if files:
            return files
    files = sorted(base.glob("round_*.json"))
    return [f for f in files if "evidence_table" not in f.name]


def extract_verify(rec: dict) -> dict:
    """Current layout: node_outputs.verify.semantic_output.{verified,
    verification_layers}; older layout: top-level verify."""
    no = rec.get("node_outputs") or {}
    if isinstance(no.get("verify"), dict):
        v = no["verify"].get("semantic_output") or no["verify"]
        if isinstance(v, dict):
            return v
    v = rec.get("verify") or rec.get("verification") or {}
    return v if isinstance(v, dict) else {}


def find_pdf(job_id: str):
    hits = sorted((Path(WORKSPACE) / "state/event_runtime/work" / job_id).glob("*.pdf"))
    return hits[0] if hits else None


def message_texts_from_events(job_id: str) -> list[str]:
    """Collect user-facing message bodies from the run log: message compose /
    send / summary compose events carry the final text in their summary."""
    p = Path(WORKSPACE) / "state/run_logs" / job_id / "events.jsonl"
    if not p.exists():
        return []
    texts = []
    wanted = {"presentation.message_compose", "presentation.summary_message_compose",
              "delivery.send_text", "delivery.send_pdf", "delivery.verify",
              "notification.lifecycle_compose", "message.sanitizer"}
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("event_type") not in wanted:
                continue
            s = e.get("summary") or {}
            so = s.get("semantic_output") or s
            for key in ("message", "content", "text", "body"):
                val = so.get(key)
                if isinstance(val, str) and val:
                    texts.append(val)
    return texts


def outbound_texts(job_id: str) -> list[str]:
    base = Path(WORKSPACE) / "state/application/outbound"
    texts = []
    if not base.exists():
        return texts
    for p in sorted(base.glob(f"**/{job_id}*.sent")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            texts.append(p.read_text(encoding="utf-8", errors="ignore"))
            continue
        body = data.get("content") or data.get("body") or data.get("message") or ""
        texts.append(str(body))
    return texts


def user_readable(text: str) -> list:
    hits = []
    for w in INTERNAL_WORDS:
        if w in text:
            hits.append(w)
    return sorted(set(hits))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--instance", default="01")
    p.add_argument("--project-id", default="literature_github_learning")
    p.add_argument("--max-rounds", type=int, default=3)
    p.add_argument("--timeout-min", type=float, default=50.0)
    p.add_argument("--request-id-prefix", default="e2e")
    p.add_argument("--message", default=DEFAULT_MESSAGE)
    p.add_argument("--job-id", default=None)
    p.add_argument("--jobs-db", default=None)
    p.add_argument("--report-dir", default="/mnt/e/work/partner_workspace/state/e2e")
    args = p.parse_args(argv)

    report_dir = Path(args.report_dir)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = report_dir / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    request_id = args.request_id_prefix + "_" + str(int(time.time()))
    report = {
        "stamp": stamp, "request_id": request_id, "job_id": args.job_id,
        "instance": args.instance, "project_id": args.project_id,
        "max_rounds": args.max_rounds, "submitted_at": None,
        "completed_at": None, "status": "pending", "checks": {}, "errors": [],
    }

    db = args.jobs_db or find_jobs_db()
    job_id = args.job_id
    if not job_id:
        sub = subprocess.run(
            [sys.executable, "scripts/messaging/partner_submit.py",
             "--instance", args.instance,
             "--project-id", args.project_id,
             "--message", args.message,
             "--request-id", request_id,
             "--reply-to", "qq",
             "--submit"],
            cwd=REPO, capture_output=True, text=True, timeout=120,
        )
        try:
            out = json.loads(sub.stdout)
        except Exception:
            report["status"] = "submit_failed"
            report["errors"].append("submit stdout unparseable: " + sub.stdout[:300])
            report["errors"].append(sub.stderr[:300])
            _finish(report, run_dir)
            return 1
        if not out.get("accepted") or not out.get("job_id"):
            report["status"] = "submit_failed"
            report["errors"].append(json.dumps(out, ensure_ascii=False)[:400])
            _finish(report, run_dir)
            return 1
        job_id = out["job_id"]
        report["job_id"] = job_id
        report["submitted_at"] = dt.datetime.now().isoformat()

        deadline = time.time() + args.timeout_min * 60
        status = "queued"
        while time.time() < deadline:
            status = job_status(db, job_id)
            if status in ("completed", "failed", "cancelled", "stopped"):
                break
            time.sleep(20)
        report["completed_at"] = dt.datetime.now().isoformat()
        if status != "completed":
            report["status"] = "not_completed_" + status
            report["errors"].append("job ended in %r; expected 'completed'" % status)
            _finish(report, run_dir)
            return 2
    else:
        status = job_status(db, job_id)
        report["status"] = "reassert_" + status
        if status != "completed":
            report["errors"].append("job %s status=%r; expected 'completed'" % (job_id, status))
            _finish(report, run_dir)
            return 2

    checks = report["checks"]
    rounds = iter_round_records(job_id)
    checks["round_count_ok"] = len(rounds) == args.max_rounds
    checks["round_count"] = len(rounds)
    if not checks["round_count_ok"]:
        report["errors"].append(
            "expected %d round records, found %d: %s" % (
                args.max_rounds, len(rounds), ", ".join(p.name for p in rounds)))

    verify_ok = True
    verdicts = []
    for rp in rounds:
        try:
            rec = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            rec = {}
        v = extract_verify(rec)
        layers = v.get("verification_layers") or {}
        verdicts.append({
            "round": rec.get("run_context", {}).get("round_number")
                     or rec.get("round_number"),
            "verified": v.get("verified"),
            "execution": layers.get("execution_verified") or v.get("execution_verified"),
            "artifact": layers.get("artifact_verified") or v.get("artifact_verified"),
            "input_eligible": layers.get("input_eligible") or v.get("input_eligible"),
        })
        if v.get("verified") is not True:
            verify_ok = False
    checks["all_rounds_verified"] = verify_ok
    checks["round_verdicts"] = verdicts
    if not verify_ok:
        report["errors"].append("verify not green in every round: " + json.dumps(verdicts, ensure_ascii=False))

    pdf = find_pdf(job_id)
    checks["pdf_produced"] = pdf is not None
    checks["pdf_path"] = str(pdf) if pdf else None
    if not pdf:
        report["errors"].append("PDF report not produced for job")

    msgs = message_texts_from_events(job_id) + outbound_texts(job_id)
    bad = sorted({w for m in msgs for w in user_readable(m)})
    checks["message_count"] = len(msgs)
    checks["messages_user_readable"] = not bad
    checks["internal_words_left"] = bad
    if bad:
        report["errors"].append("user-facing messages still contain internal tokens: " + str(bad))

    report["status"] = "passed" if not report["errors"] else "failed"
    _finish(report, run_dir)
    return 3 if report["errors"] else 0


def _finish(report: dict, run_dir) -> None:
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
