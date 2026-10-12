# -*- coding: utf-8 -*-
"""Outer-layer Supervisor — cron-driven single-scan live supervision.

Two-layer engine architecture (docs/architecture/two_layer_engine.md):

- OUTER layer: this process.  A cron-invoked, one-shot scan that is fully
  independent of the inner runtime: it collects a runtime snapshot (instances,
  jobs, node stalls, round-plan expectations vs actual traces, outputs), asks
  the LLM (``supervise.live_scan``) to grade problems with evidence, dispatches
  auto-fix repair jobs for small/clear issues (mode=self_improvement with
  ``supervisor_scan:True`` so a repair job never spawns a repair of the
  repair), writes direction-level issues into notes pending for the user, and
  delivers a user-readable report to QQ via the outbound queue.

- INNER layer: the round-based execution loops (project_iteration / cycle
  rounds) whose Round Planner persists expectation documents under
  ``state/supervision/round_plans/<engine>/round_*.json``; the supervisor
  reads those expectations and compares them to actual run traces.

Deterministic code here only collects state and enforces hard gates
(stop control, recursion guard).  All diagnosis, grading and fix selection is
LLM-driven (``supervise.live_scan``).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUPERVISION_DIR = "state/supervision"
INSTANCES = ("01", "02", "03", "04", "05")
NODE_STALL_SECONDS = 900.0      # a node with no new event for this long is stalled
REPORT_WINDOW = 60 * 60 * 24    # report once per day per problem signature at most


def _ws(args) -> Path:
    return Path(args.workspace).resolve()


def _collect_snapshot(ws: Path) -> dict[str, Any]:
    """Deterministic state collection — no LLM here."""
    snap: dict[str, Any] = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "instances": {}, "jobs": [], "round_plans": [], "outputs": {},
    }
    # ---- instances: process liveness + Bot ready heal log ----
    ps = subprocess.run(
        ["ps", "aux"], capture_output=True, text=True, timeout=10)
    ps_text = ps.stdout or ""
    for inst in INSTANCES:
        alive = f"partner --instance-id {inst} " in ps_text
        heal = Path(f"/tmp/partner{inst}_heal.log")
        bot_ready = False
        heal_age = None
        if heal.exists():
            bot_ready = "Bot ready" in heal.read_text(errors="ignore")
            heal_age = time.time() - heal.stat().st_mtime
        snap["instances"][inst] = {
            "alive": alive, "bot_ready": bot_ready, "heal_log_age_s": round(heal_age, 1) if heal_age else None,
        }
    # ---- jobs ----
    jobs_dir = ws / "state/application/jobs"
    if jobs_dir.is_dir():
        rows = []
        for fp in sorted(jobs_dir.glob("job_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows.append({
                "job_id": d.get("job_id"), "status": d.get("status"),
                "flow_type": d.get("flow_type"), "instance": d.get("assigned_instance"),
                "title": str(d.get("title") or "")[:140],
                "created_at": d.get("created_at"), "updated_at": d.get("updated_at"),
                "error": str(d.get("error") or "")[:200],
                "current_event": d.get("current_event_id", ""),
                "supervisor_scan": bool((d.get("execution_constraints") or {}).get("supervisor_scan")),
            })
        snap["jobs"] = rows
    # ---- node stall detection: last event time per running job ----
    now = time.time()
    for row in snap["jobs"]:
        if row["status"] not in ("running", "waiting"):
            continue
        jid = row["job_id"]
        run_dir = ws / "state/run_logs" / str(jid or "")
        last_event = None
        if (run_dir / "events.jsonl").exists():
            last_line = None
            try:
                with (run_dir / "events.jsonl").open("rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    fh.seek(max(0, size - 4000))
                    tail = fh.read().decode("utf-8", "ignore").strip().splitlines()
                if tail:
                    last_line = tail[-1]
            except Exception:
                last_line = None
            if last_line:
                try:
                    ev = json.loads(last_line)
                    ts = ev.get("at") or ev.get("ts") or ev.get("timestamp")
                    if ts and isinstance(ts, (int, float)):
                        last_event = now - float(ts)
                    elif isinstance(ts, str):
                        try:
                            last_event = now - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                        except Exception:
                            last_event = None
                except Exception:
                    last_event = None
        row["node_idle_s"] = round(last_event, 1) if last_event is not None else None
        row["node_stalled"] = last_event is not None and last_event > NODE_STALL_SECONDS
    # ---- round plans (expectations) vs actual traces ----
    plans_root = ws / SUPERVISION_DIR / "round_plans"
    if plans_root.is_dir():
        for engine_dir in sorted(plans_root.iterdir()):
            if not engine_dir.is_dir():
                continue
            for pf in sorted(engine_dir.glob("round_*.json")):
                try:
                    plan = json.loads(pf.read_text(encoding="utf-8"))
                except Exception:
                    continue
                snap["round_plans"].append({
                    "engine": engine_dir.name, "file": str(pf),
                    "round": plan.get("round"), "mode": plan.get("mode"),
                    "goal": str(plan.get("goal") or "")[:300],
                    "flows": (plan.get("flows") or [])[:5],
                    "acceptance_criteria": (plan.get("acceptance_criteria") or [])[:5],
                    "expected_outputs": (plan.get("expected_outputs") or [])[:5],
                })
    # ---- outputs: outbound messages / pdf ----
    out_root = ws / "state/application/outbound"
    if out_root.is_dir():
        for inst_dir in sorted(out_root.iterdir()):
            files = list((inst_dir).glob("*.json")) if inst_dir.is_dir() else []
            if not files:
                continue
            latest = max(files, key=lambda p: p.stat().st_mtime)
            try:
                payload = json.loads(latest.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            snap["outputs"].setdefault(inst_dir.name, {
                "message_count": len(files),
                "latest_delivery_state": payload.get("delivery_state"),
                "latest_text_delivered": payload.get("text_delivered"),
                "latest_content": str(payload.get("content") or "")[:200],
                "latest_ts": payload.get("created_at"),
            })
    return snap


def _make_ctx(ws: Path, args) -> Any:
    from types import SimpleNamespace
    from partner.adapters.adapter import DirectAdapter
    ctx = SimpleNamespace()
    ctx.workspace = str(ws)
    ctx.workspace_root = str(ws)
    ctx.project_id = "supervision"
    ctx.instance_id = "supervisor"
    ctx.job_id = ""
    ctx.channel = "qq"
    ctx.sender_id = str(args.recipient or "")
    ctx.intake_instance_id = "supervisor"
    ctx.adapter = DirectAdapter(workspace_path=str(ws))
    ctx.event_deadline = time.monotonic() + 540
    ctx.request = "supervisor periodic live scan"
    return ctx


def _dispatch(ws: Path, value: dict[str, Any], args) -> dict[str, Any]:
    """Dispatch fixes: auto_fix -> repair job; else -> notes pending. No LLM here."""
    from partner.mind_lab.notes import NoteStore
    store = NoteStore(ws)
    problems = value.get("problems") or []
    dispatched: dict[str, Any] = {"repair_jobs": [], "pending_notes": []}
    control = {}
    try:
        control = json.loads((ws / SUPERVISION_DIR / "supervisor_control.json").read_text(encoding="utf-8"))
    except Exception:
        control = {}
    if control.get("action") == "stop":
        return {**dispatched, "suppressed": "supervisor_control stop"}
    for p in problems:
        severity = str(p.get("severity") or "minor")
        auto = bool(p.get("auto_fix"))
        target = str(p.get("target") or "unknown")
        # recursion guard: never repair a job that is itself a supervisor repair
        if target.startswith("job_") and auto:
            hit = next((j for j in (value.get("_jobs") or []) if j.get("job_id") == target), None)
            if hit and hit.get("supervisor_scan"):
                continue
        if auto:
            try:
                from scripts.messaging.partner_submit import SubmitPayload, submit
                instance = str(args.instance or "01")
                rid = "sup" + str(int(time.time() * 1000))
                payload = SubmitPayload(
                    instance=instance,
                    sender_id="system_supervisor",
                    sender_name="system_supervisor",
                    message=(
                        "【监督发现并自动派发修复】\n"
                        f"问题（{severity}）：{target}\n{p.get('diagnosis', '')}\n"
                        f"建议修复：{p.get('fix', '')}\n"
                        f"证据：{p.get('evidence', '')[:400]}\n"
                        "请按自进化流程：定位根因 → 修复 → 测试 → 验证后晋升，完成后用户可读总结。"),
                    project_id=None,
                    mode="self_improvement",
                    reply_to="qq",
                    conversation_id=None,
                    scope=None,
                    request_id=rid,
                    recipient_ref=f"inst{instance}_{args.recipient or ''}" if args.recipient else f"inst{instance}",
                    constraints_file=None,
                    execution_constraints={
                        "evolution_apply": True, "supervisor_scan": True,
                        "notification_mode": "standard",
                    },
                    subject_allowed_instances=[instance],
                    direct_answer=False,
                )
                res = submit(payload, str(ws))
                dispatched["repair_jobs"].append({
                    "problem": target, "severity": severity, "job_id": res.get("job_id"),
                })
            except Exception as exc:  # noqa: BLE001
                dispatched["repair_jobs"].append({
                    "problem": target, "severity": severity,
                    "error": f"submit failed: {str(exc)[:200]}",
                })
        else:
            try:
                note = store.append("pending", {
                    "content": f"[supervisor:{severity}:{target}] {p.get('diagnosis', '')} — 建议：{p.get('fix', '')}（证据：{p.get('evidence', '')[:300]}）",
                    "source": f"supervisor_scan@{time.strftime('%Y-%m-%dT%H:%M:%S')}",
                    "confidence": 0.7,
                })
                dispatched["pending_notes"].append({"problem": target, "note_id": note.get("id")})
            except Exception as exc:  # noqa: BLE001
                dispatched["pending_notes"].append({"problem": target, "error": str(exc)[:200]})
    return dispatched


def _write_report(ws: Path, value: dict[str, Any], dispatched: dict[str, Any], args) -> Path:
    report_dir = ws / SUPERVISION_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"scan_{time.strftime('%Y%m%d%H%M%S')}.json"
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "problems": value.get("problems") or [],
        "summary": value.get("summary", ""),
        "dispatched": dispatched,
        "model_usage": value.get("model_usage") or {},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _deliver_report(ws: Path, value: dict[str, Any], dispatched: dict[str, Any], args) -> None:
    """Deliver a user-readable report to QQ via the outbound queue."""
    problems = value.get("problems") or []
    if not problems:
        return
    instance = str(args.instance or "01")
    recipient = str(args.recipient or "")
    if not recipient:
        return
    lines = ["【Partner 监督简报】"]
    if value.get("summary"):
        lines.append(str(value["summary"])[:600])
    if dispatched.get("repair_jobs"):
        lines.append("已自动派发修复：" + "、".join(str(j.get("job_id") or j.get("problem")) for j in dispatched["repair_jobs"][:4]))
    if dispatched.get("pending_notes"):
        lines.append("已记录待确认问题 " + str(len(dispatched["pending_notes"])) + " 条（你确认后我再动）")
    if not (dispatched.get("repair_jobs") or dispatched.get("pending_notes")):
        lines.append("本轮无需派发修复。")
    content = "\n".join(lines)
    try:
        target_dir = ws / "state/application/outbound" / instance
        target_dir.mkdir(parents=True, exist_ok=True)
        job_id = f"supervisor_{time.strftime('%Y%m%d%H%M%S')}"
        target = target_dir / f"{job_id}.json"
        payload = {
            "schema_version": 2,
            "job_id": job_id,
            "to_user": recipient,
            "content": content,
            "image_artifacts": [],
            "pdf_artifacts": [],
            "text_delivered": False,
            "delivery_state": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="global workspace root")
    parser.add_argument("--instance", default="01", help="QQ-bound instance used for reporting/dispatch")
    parser.add_argument("--recipient", default="", help="QQ openid to receive reports (empty = no report)")
    parser.add_argument("--expectations-hint", default="", help="optional short baseline summary")
    args = parser.parse_args()
    ws = _ws(args)
    snapshot = _collect_snapshot(ws)
    from partner.events.supervision import live_scan
    ctx = _make_ctx(ws, args)
    try:
        result = live_scan(ctx, {
            "snapshot": snapshot,
            "expectations_hint": args.expectations_hint,
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[supervisor] live_scan failed: {exc}", file=sys.stderr)
        return 2
    value = result.get("semantic_output") or {"problems": [], "summary": ""}
    value.setdefault("problems", [])
    value.setdefault("summary", "")
    value["_jobs"] = snapshot.get("jobs") or []
    dispatched = _dispatch(ws, value, args)
    path = _write_report(ws, value, dispatched, args)
    _deliver_report(ws, value, dispatched, args)
    print(f"[supervisor] scan done: {len(value['problems'])} problems, report={path}")
    for p in value.get("problems") or []:
        print(f"  - [{p.get('severity')}] {p.get('target')}: {str(p.get('diagnosis'))[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
