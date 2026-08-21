"""Screenshot change detection for Partner (Sprint 7)."""
import os, time, logging, hashlib
from datetime import datetime

logger = logging.getLogger(__name__)


def compare_screenshots(before_path: str, after_path: str = None, output_dir: str = None) -> dict:
    """Compare two screenshots. If after_path is None, takes a new screenshot and compares.
    
    Returns {ok, changed, before, after, diff_pixels, note}
    """
    try:
        from partner.tools.direct_ops import screenshot
        
        b_size = os.path.getsize(before_path) if os.path.exists(before_path) else 0
        
        if after_path is None:
            if output_dir is None:
                output_dir = os.path.dirname(before_path) or "/tmp"
            r = screenshot(output_dir)
            if not r["ok"]:
                return {"ok": False, "error": f"Screenshot failed: {r.get('error')}"}
            after_path = r["path"]
        
        a_size = os.path.getsize(after_path) if os.path.exists(after_path) else 0
        
        # Quick check: file sizes
        size_changed = abs(b_size - a_size) > 1000
        
        # Hash check
        b_hash = ""
        a_hash = ""
        if os.path.exists(before_path):
            with open(before_path, "rb") as f:
                b_hash = hashlib.md5(f.read()).hexdigest()
        if os.path.exists(after_path):
            with open(after_path, "rb") as f:
                a_hash = hashlib.md5(f.read()).hexdigest()
        
        hash_changed = b_hash != a_hash
        
        return {
            "ok": True,
            "changed": hash_changed,
            "before": before_path,
            "after": after_path,
            "before_size": b_size,
            "after_size": a_size,
            "before_hash": b_hash[:8],
            "after_hash": a_hash[:8],
            "size_changed": size_changed,
            "hash_changed": hash_changed,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def monitor_loop(baseline_dir: str, interval: int = 300, max_iterations: int = 100) -> list:
    """Continuously monitor desktop for changes. Returns list of change events."""
    from partner.tools.direct_ops import screenshot
    
    os.makedirs(baseline_dir, exist_ok=True)
    events = []
    
    # Take baseline
    baseline = screenshot(baseline_dir)
    if not baseline["ok"]:
        return events
    
    events.append({
        "time": datetime.now().isoformat(),
        "type": "baseline",
        "path": baseline["path"],
        "size": baseline["size"],
    })
    
    last_path = baseline["path"]
    
    for i in range(max_iterations):
        time.sleep(interval)
        
        # Take new screenshot
        new = screenshot(baseline_dir)
        if not new["ok"]:
            continue
        
        # Compare
        result = compare_screenshots(last_path, new["path"])
        
        if result.get("changed"):
            events.append({
                "time": datetime.now().isoformat(),
                "type": "changed",
                "before": last_path,
                "after": new["path"],
                "before_hash": result.get("before_hash", ""),
                "after_hash": result.get("after_hash", ""),
            })
            logger.info("[monitor] Change detected: %s -> %s", 
                       result.get("before_hash","")[:8], result.get("after_hash","")[:8])
        
        last_path = new["path"]
    
    return events
