"""Trend analysis for Partner active_monitor (Sprint 7). Scans logs, computes success rates, detects patterns."""
import os, re, json, glob, logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def scan_log(workspace: str, hours: int = 24) -> dict:
    """Scan partner.log and compute success/failure rates, common errors, step timing."""
    log_path = os.path.join(workspace, "partner.log")
    if not os.path.exists(log_path):
        return {"ok": False, "error": "No log file"}
    
    with open(log_path) as f:
        lines = f.readlines()
    
    # Count events
    batch_plans = 0
    steps_ok = 0
    steps_fail = 0
    self_heals = 0
    errors = Counter()
    step_durations = []
    last_ts = None
    
    for line in lines:
        # Timestamp
        m = re.search(r'(\d{2}:\d{2}:\d{2})', line)
        if m:
            last_ts = m.group(1)
        
        if 'batch_plan OK' in line:
            batch_plans += 1
        elif re.search(r'STEP.*完成', line):
            steps_ok += 1
            # Extract duration
            dm = re.search(r'耗时\s*([\d.]+)s', line)
            if dm:
                step_durations.append(float(dm.group(1)))
        elif 'SELFHEAL' in line:
            self_heals += 1
        elif 'ERROR' in line or 'error' in line.lower():
            # Extract error type
            em = re.search(r'(?:Error|ERROR|error):\s*(.+?)(?:\s*$|\s*\[)', line)
            if em:
                err_type = em.group(1)[:60].strip()
            else:
                err_type = "unknown_error"
            errors[err_type] += 1
        elif re.search(r'需补齐', line):
            steps_fail += 1
    
    total_steps = steps_ok + steps_fail
    success_rate = (steps_ok / total_steps * 100) if total_steps > 0 else 0
    
    avg_duration = sum(step_durations) / len(step_durations) if step_durations else 0
    
    # Top errors
    top_errors = errors.most_common(5)
    
    return {
        "ok": True,
        "workspace": workspace,
        "lines_scanned": len(lines),
        "batch_plans": batch_plans,
        "steps_ok": steps_ok,
        "steps_fail": steps_fail,
        "success_rate": round(success_rate, 1),
        "self_heals": self_heals,
        "avg_step_duration": round(avg_duration, 1),
        "top_errors": [{"error": e, "count": c} for e, c in top_errors],
        "last_timestamp": last_ts,
    }


def scan_all_instances(base_workspace: str = None) -> dict:
    """Scan all instance logs and produce a consolidated report."""
    if base_workspace is None:
        base_workspace = "/mnt/e/work/partner_workspace/instances"
    
    results = {}
    all_ok = 0
    all_fail = 0
    all_sh = 0
    
    for iid in ["01", "02", "03", "04", "05"]:
        ws = os.path.join(base_workspace, iid)
        r = scan_log(ws)
        if r["ok"]:
            results[iid] = r
            all_ok += r["steps_ok"]
            all_fail += r["steps_fail"]
            all_sh += r["self_heals"]
    
    total = all_ok + all_fail
    rate = (all_ok / total * 100) if total > 0 else 0
    
    return {
        "ok": True,
        "instances": len(results),
        "total_steps_ok": all_ok,
        "total_steps_fail": all_fail,
        "overall_success_rate": round(rate, 1),
        "total_self_heals": all_sh,
        "per_instance": results,
    }


def detect_failure_patterns(base_workspace: str = None) -> dict:
    """Detect recurring failure patterns across all instances."""
    report = scan_all_instances(base_workspace)
    if not report["ok"]:
        return report
    
    patterns = []
    
    # Pattern 1: Low success rate
    for iid, r in report.get("per_instance", {}).items():
        if r["success_rate"] < 50 and r["steps_ok"] + r["steps_fail"] > 5:
            patterns.append({
                "instance": iid,
                "type": "low_success_rate",
                "value": f"{r['success_rate']}%",
                "severity": "high" if r["success_rate"] < 30 else "medium",
            })
    
    # Pattern 2: High self-heal rate
    for iid, r in report.get("per_instance", {}).items():
        if r["self_heals"] > r["batch_plans"] * 2:
            patterns.append({
                "instance": iid,
                "type": "high_self_heal",
                "value": f"{r['self_heals']} heals / {r['batch_plans']} plans",
                "severity": "high",
            })
    
    # Pattern 3: Stalled instances (no recent activity)
    # Pattern 4: Recurring errors
    all_errors = Counter()
    for r in report.get("per_instance", {}).values():
        for e in r.get("top_errors", []):
            all_errors[e["error"]] += e["count"]
    
    for err, count in all_errors.most_common(3):
        if count > 3:
            patterns.append({
                "instance": "all",
                "type": "recurring_error",
                "value": f"{err}: {count} occurrences",
                "severity": "medium",
            })
    
    report["patterns"] = patterns
    report["pattern_count"] = len(patterns)
    return report
