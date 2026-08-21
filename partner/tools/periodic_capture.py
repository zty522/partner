"""Periodic screenshot capture daemon (Sprint 7). Captures desktop every N minutes."""
import os, time, json, logging
from datetime import datetime

logger = logging.getLogger(__name__)

def run_periodic(instance_dir: str, interval_minutes: int = 30, max_captures: int = 100) -> dict:
    """Run periodic screenshot capture. Returns summary after max_captures."""
    from partner.tools.direct_ops import screenshot
    from partner.tools.screen_archive import archive_screenshots
    
    capture_dir = os.path.join(instance_dir, "state", "screenshots")
    archive_dir = os.path.join(instance_dir, "state", "archive")
    os.makedirs(capture_dir, exist_ok=True)
    
    captures = []
    for i in range(max_captures):
        time.sleep(interval_minutes * 60)
        
        r = screenshot(capture_dir)
        if r["ok"]:
            captures.append({
                "time": datetime.now().isoformat(),
                "path": r["path"],
                "size": r["size"],
            })
            logger.info("[periodic] Capture %d: %s (%d bytes)", i+1, r["path"], r["size"])
    
    # Archive all
    archived = archive_screenshots(capture_dir, archive_dir)
    
    return {
        "ok": True,
        "captures": len(captures),
        "archived": archived.get("archived", 0),
        "capture_dir": capture_dir,
        "archive_dir": archive_dir,
    }


def single_capture_and_compare(instance_dir: str) -> dict:
    """Take one screenshot and compare with previous. Returns change detection result."""
    from partner.tools.direct_ops import screenshot
    from partner.tools.screen_monitor import compare_screenshots
    
    capture_dir = os.path.join(instance_dir, "state", "screenshots")
    os.makedirs(capture_dir, exist_ok=True)
    
    # Find most recent previous screenshot
    import glob
    prev = sorted(glob.glob(os.path.join(capture_dir, "*.png")), key=os.path.getmtime, reverse=True)
    
    new = screenshot(capture_dir)
    if not new["ok"]:
        return {"ok": False, "error": new.get("error")}
    
    if prev:
        return compare_screenshots(prev[0], new["path"])
    else:
        return {"ok": True, "changed": True, "reason": "first capture", "after": new["path"], "after_size": new["size"]}
