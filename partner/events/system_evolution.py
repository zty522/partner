"""System-level self-evolution: trigger and track a real regression job.

The autonomous_evolution loop normally audits the artifacts of the Job that
just ran.  System-level evolution adds an active probe: it submits a bounded
regression task (via the same orchestrator entry a user message uses), tracks
the Job to completion, then hands its messages / PDF / rounds to ``collect``
so the audit can judge user-facing readability and mechanism behaviour and
design a fix.  The regression Job is submitted with ``evolution_cycle: False``
so it never spawns its own evolution child (no recursion).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from partner.event_fabric.catalog import EventDefinition
from partner.runtime.action_execution import write_json

REGRESSION_DIR = "state/evolution_regression"


def _regression_folder(ctx: Any) -> Path:
    base = Path(str(getattr(ctx, "workspace", "") or ""))
    job = str(getattr(ctx, "job_id", "") or "parent")
    return base / REGRESSION_DIR / job


def _jobs_db() -> str:
    cands = glob.glob("/home/os/.local/share/partner/runtime/*/jobs.db")
    return max(cands, key=os.path.getmtime) if cands else ""


def _default_message() -> str:
    return ("【手动回归测试】在 literature_github_learning 项目下验证三阶段能力，"
            "有界运行 3 轮，结束时生成 PDF 报告并经 QQ 投递。"
            "停止条件：3 轮结束或已冻结 baseline 即停。")


def trigger_regression(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Submit one bounded regression Job to a sibling instance and record it.

    Without ``regression_mode`` this is a no-op passthrough so the normal
    per-Job audit keeps its exact behaviour (no extra Job is submitted).
    """
    if not params.get("regression_mode"):
        return {"ok": True, "status": "skipped", "skipped": True,
                "summary": "非系统回归模式，不投递回归任务",
                "semantic_output": {"status": "skipped", "reason": "regression_mode off"}}
    from scripts.messaging.partner_submit import SubmitPayload, submit
    folder = _regression_folder(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    origin_instance = str(getattr(ctx, "instance_id", "") or "")
    instance = str(params.get("regression_instance") or origin_instance or "01")
    sender_openid = str(getattr(ctx, "sender_id", "") or "").strip()
    recipient_ref = f"inst{instance}_{sender_openid}" if sender_openid else f"inst{instance}"
    project = str(params.get("regression_project") or "literature_github_learning")
    message = str(params.get("regression_message") or _default_message())
    request_id = "reg" + uuid.uuid4().hex[:12]
    payload = SubmitPayload(
        instance=instance,
        sender_id="system_evolution",
        sender_name="system_evolution",
        message=message,
        project_id=project,
        mode=str(params.get("regression_mode") or "project_iteration"),
        reply_to="qq",
        conversation_id=None,
        scope=None,
        request_id=request_id,
        recipient_ref=recipient_ref,
        constraints_file=None,
        execution_constraints={"evolution_cycle": False},
        subject_allowed_instances=[instance],
        direct_answer=False,
    )
    try:
        result = submit(payload, str(getattr(ctx, "workspace", "") or ""))
    except Exception as exc:  # orchestrate_submit raises on invalid targets
        value = {"ok": False, "status": "failed", "reason": str(exc)[:300]}
        path = folder / "regression_job.json"
        write_json(path, value)
        return {"ok": False, "status": "failed", "error": str(exc)[:300],
                "path": str(path), "semantic_output": value}
    value = {
        "ok": True,
        "status": "submitted",
        "job_id": result.get("job_id"),
        "instance": result.get("assigned_instance") or instance,
        "request_id": request_id,
        "project_id": project,
        "message": message[:120],
        "execution_constraints": {"evolution_cycle": False},
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = folder / "regression_job.json"
    write_json(path, value)
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "summary": f"已投递系统回归任务给实例 {instance}，job={result.get('job_id')}",
            "semantic_output": value}


def _terminal(status: str) -> bool:
    return status in {"completed", "failed", "cancelled", "terminated"}


def _collect_outputs(ctx: Any, job_id: str, instance: str) -> dict[str, Any]:
    ws = Path(str(getattr(ctx, "workspace", "") or ""))
    out: dict[str, Any] = {"job_id": job_id, "instance": instance}
    rounds = sorted(glob.glob(str(ws / "state/cycles" / job_id / "iterations" / "round_*.json")))
    out["rounds"] = rounds
    round_briefs = []
    for rp in rounds[:6]:
        try:
            row = json.loads(Path(rp).read_text(encoding="utf-8"))
            sem = (row.get("node_outputs") or {}).get("verify", {}).get("semantic_output") or {}
            layers = sem.get("verification_layers") or {}
            round_briefs.append({
                "path": str(rp), "verified": sem.get("verified"),
                "execution": sem.get("execution"), "artifact": sem.get("artifact"),
                "input_eligible": sem.get("input_eligible"),
                "goal": str((row.get("node_outputs") or {}).get("iterate", {}).get("summary") or "")[:140],
            })
        except Exception:
            continue
    out["round_briefs"] = round_briefs
    msgs = sorted(glob.glob(str(ws / "state/application/outbound" / instance /
                                f"{job_id}*.sent")))
    out["message_paths"] = msgs
    message_texts = []
    for mp in msgs[:20]:
        try:
            content = json.loads(Path(mp).read_text(encoding="utf-8")).get("content", "")
            message_texts.append({"path": str(mp), "content": str(content)[:800]})
        except Exception:
            continue
    out["messages"] = message_texts
    pdfs = sorted(glob.glob(str(ws / "state/event_runtime/work" / job_id / "*.pdf")))
    out["pdf_paths"] = pdfs
    pdf_texts = []
    for pp in pdfs[:2]:
        text = ""
        try:
            import fitz
            with fitz.open(pp) as document:
                text = "\n".join(page.get_text() for page in document)[:16000]
        except Exception as exc:
            text = f"PDF content read failed: {str(exc)[:200]}"
        pdf_texts.append({"path": str(pp), "content": text})
    out["pdfs"] = pdf_texts
    return out


def _live_supervise_round(ctx: Any, job_id: str, instance: str,
                          round_path: str, seen_messages: list[str]) -> dict[str, Any]:
    """Supervise one just-finished regression round while the Job is still
    running (运行过程实时监督).  The round record is read in full and the LLM
    judges it item-by-item against the expectation document; verdicts land in
    the supervision folder of the parent Job."""
    from partner.events.supervision import snapshot
    folder = _regression_folder(ctx)
    messages = sorted(set(seen_messages) | set(
        glob.glob(str(Path(str(getattr(ctx, "workspace", ""))) / "state/application/outbound" / instance /
                      f"{job_id}*.sent"))))
    texts = []
    for mp in messages:
        try:
            texts.append(str(Path(mp)))
        except Exception:
            pass
    round_no = Path(round_path).stem  # round_N
    out = snapshot(ctx, {"target": "round", "round": round_no,
                         "expectations_path": "docs/expectations/system_quality.md",
                         "round_records": [round_path], "messages": texts})
    if out.get("ok"):
        verdicts = (out.get("semantic_output") or {}).get("verdicts") or []
        return {"round": round_no, "path": str(round_path), "verdicts": verdicts,
                "usage": (out.get("semantic_output") or {}).get("model_usage") or {}}
    return {"round": round_no, "path": str(round_path), "error": out.get("error") or "supervision failed"}


def track_regression(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Poll the regression Job to a terminal state while supervising each
    finished round live (运行过程实时监督), then collect all outputs."""
    folder = _regression_folder(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    job_path = folder / "regression_job.json"
    if not params.get("regression_mode") or not job_path.is_file():
        value = {"ok": True, "status": "skipped",
                 "reason": "regression_mode off or no job persisted"}
        write_json(folder / "regression_evidence.json", value)
        return {"ok": True, "status": "completed", "summary": "无回归任务可追踪",
                "semantic_output": value}
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = str(job.get("job_id") or "")
    instance = str(job.get("instance") or "02")
    if not job_id or not job.get("ok"):
        value = {"ok": False, "status": "regression_submit_failed",
                 "reason": str(job.get("reason") or "submit rejected")}
        write_json(folder / "regression_evidence.json", value)
        return {"ok": True, "status": "completed", "summary": "回归任务投递失败",
                "semantic_output": value}
    db = _jobs_db()
    ws = Path(str(getattr(ctx, "workspace", "") or ""))
    deadline = time.time() + int(params.get("regression_track_seconds") or 5400)
    status = "queued"
    seen_rounds: set[str] = set()
    live_supervision: list[dict[str, Any]] = []
    while time.time() < deadline:
        try:
            if db:
                con = sqlite3.connect(db)
                row = con.execute("select status from jobs where job_id=?", (job_id,)).fetchone()
                con.close()
                if row:
                    status = str(row[0])
        except Exception:
            status = "queued"
        rounds_now = sorted(glob.glob(str(ws / "state/cycles" / job_id / "iterations" / "round_*.json")))
        for rp in rounds_now:
            if rp in seen_rounds:
                continue
            seen_rounds.add(rp)
            try:
                rec = _live_supervise_round(ctx, job_id, instance, rp, [])
            except Exception as exc:
                rec = {"round": Path(rp).stem, "path": rp, "error": str(exc)[:160]}
            live_supervision.append(rec)
        if _terminal(status):
            break
        time.sleep(20)
    outputs = _collect_outputs(ctx, job_id, instance)
    value = {"ok": True, "status": status, "terminal": _terminal(status),
             "job_id": job_id, "instance": instance,
             "tracked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "live_supervision": live_supervision,
             "outputs": outputs}
    if not _terminal(status):
        value["ok"] = False
        value["reason"] = "tracking deadline exceeded before terminal state"
    path = folder / "regression_evidence.json"
    write_json(path, value)
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "summary": f"回归任务 {job_id} 状态={status}，实时监督 {len(live_supervision)} 轮" + (
                "" if _terminal(status) else "（追踪超时，仅记录部分证据）"),
            "semantic_output": value}


def benchmark_gate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Deterministic gate: after a candidate source apply, the repository-wide
    benchmark suites must stay green before runtime verification can pass."""
    import subprocess
    folder = Path(str(getattr(ctx, "working_dir", "") or ""))
    folder.mkdir(parents=True, exist_ok=True)
    suites = params.get("benchmark_suites") or [
        "benchmark/evolution/", "benchmark/runtime/test_closed_loop_effect_contract.py"]
    results = []
    failed = False
    for suite in suites:
        try:
            r = subprocess.run(
                ["/home/os/miniconda3/bin/python", "-m", "pytest", suite,
                 "-q", "--no-header", "-x"],
                cwd="/mnt/e/work/partner", capture_output=True, text=True, timeout=1800)
            passed = r.returncode == 0
            results.append({"suite": suite, "passed": passed,
                            "tail": r.stdout.strip().splitlines()[-1][:160] if r.stdout.strip() else ""})
            failed = failed or not passed
        except Exception as exc:
            results.append({"suite": suite, "passed": False, "tail": str(exc)[:160]})
            failed = True
    value = {"gate_passed": not failed, "results": results,
             "rule": "benchmark/evolution + runtime contract must stay green after source apply"}
    path = folder / "benchmark_gate.json"
    write_json(path, value)
    return {"ok": True, "status": "completed", "path": str(path), "files": [str(path)],
            "summary": "仓库级基准门" + ("通过" if not failed else "未通过"),
            "semantic_output": value}


DEFINITIONS = [
    EventDefinition("autoevolution.trigger_regression", "evolution",
                    "投递一个有界回归任务给兄弟实例并记录 job（evolution_cycle=False 防递归）", trigger_regression),
    EventDefinition("autoevolution.track_regression", "evolution",
                    "轮询回归任务到终态并收集消息/PDF/轮次证据", track_regression),
    EventDefinition("autoevolution.benchmark_gate", "evolution",
                    "候选源码落地后跑仓库级基准套件作为确定性门", benchmark_gate),
]
