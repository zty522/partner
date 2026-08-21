"""Auto-optimization engine (Sprint 7). Detects issues, proposes fixes based on trend data."""
import os, json, time, logging

logger = logging.getLogger(__name__)

def analyze_and_suggest(workspace_base="/mnt/e/work/partner_workspace/instances") -> dict:
    """Analyze all instances and suggest concrete improvements."""
    from partner.tools.trend_analysis import detect_failure_patterns
    
    report = detect_failure_patterns(workspace_base)
    if not report.get("ok"):
        return {"ok": False, "error": "trend analysis failed"}
    
    suggestions = []
    
    for pattern in report.get("patterns", []):
        if pattern["type"] == "low_success_rate":
            suggestions.append({
                "priority": "P0",
                "target": f"instance {pattern['instance']}",
                "issue": f"Success rate {pattern['value']}",
                "fix": "Inject simpler task. Check if tools are importable. Reduce max_steps.",
                "auto_apply": False,
            })
        
        elif pattern["type"] == "high_self_heal":
            suggestions.append({
                "priority": "P0",
                "target": f"instance {pattern['instance']}",
                "issue": f"Excessive self-heals: {pattern['value']}",
                "fix": "Check self_heal.py for false positives. Increase heal threshold.",
                "auto_apply": False,
            })
        
        elif pattern["type"] == "recurring_error":
            suggestions.append({
                "priority": "P1",
                "target": "all instances",
                "issue": f"Recurring: {pattern['value'][:80]}",
                "fix": "Add error to self_heal skill bank. Check network/env.",
                "auto_apply": True,
            })
    
    # Check for unused tools
    for iid in ["01","02","03","04","05"]:
        logf = os.path.join(workspace_base, iid, "partner.log")
        if os.path.exists(logf):
            with open(logf) as f:
                content = f.read()
            unused = []
            for tool in ["browser_ops", "screen_archive", "content_digest"]:
                if tool not in content:
                    unused.append(tool)
            if unused:
                suggestions.append({
                    "priority": "P2",
                    "target": f"instance {iid}",
                    "issue": f"Unused tools: {', '.join(unused)}",
                    "fix": f"Add {unused[0]} to task template for instance {iid}.",
                    "auto_apply": True,
                })
    
    return {
        "ok": True,
        "patterns_found": report.get("pattern_count", 0),
        "suggestions": suggestions,
        "suggestion_count": len(suggestions),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def apply_suggestions(suggestions: list, workspace_base="/mnt/e/work/partner_workspace/instances") -> dict:
    """Apply auto-fixable suggestions by updating daemon tasks."""
    applied = 0
    for s in suggestions:
        if not s.get("auto_apply"):
            continue
        
        iid = s["target"].replace("instance ", "")
        if iid == "all":
            for i in ["01","02","03","04","05"]:
                applied += _inject_fix_task(i, s, workspace_base)
        elif iid in ["01","02","03","04","05"]:
            applied += _inject_fix_task(iid, s, workspace_base)
    
    return {"ok": True, "applied": applied}


def _inject_fix_task(iid: str, suggestion: dict, base: str) -> int:
    """Inject a fix task into instance inbox."""
    try:
        inbox = os.path.join(base, iid, "state", "desktop_inbox.jsonl")
        os.makedirs(os.path.dirname(inbox), exist_ok=True)
        with open(inbox, "a") as f:
            f.write(json.dumps({
                "id": str(__import__('uuid').uuid4()),
                "message_id": f"fix_{iid}_{int(time.time())}",
                "source": "tui",
                "text": f"【自动优化】{suggestion['fix']}",
                "ts": time.time(),
                "sender_name": "Auto-Optimize",
            }, ensure_ascii=False) + "\n")
        return 1
    except:
        return 0
