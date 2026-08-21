"""Screenshot archive for Partner (Sprint 7)."""
import os, time, json, glob, shutil
from datetime import datetime

def archive_screenshots(source_dir, archive_dir, max_per_hour=10):
    """Archive screenshots organized by date/hour."""
    os.makedirs(archive_dir, exist_ok=True)
    screenshots = glob.glob(os.path.join(source_dir, "*.png"))
    if not screenshots:
        return {"ok": True, "archived": 0, "total_screenshots": 0}
    
    by_hour = {}
    for path in screenshots:
        try:
            mt = os.path.getmtime(path)
            hour_key = datetime.fromtimestamp(mt).strftime("%Y%m%d_%H")
            by_hour.setdefault(hour_key, []).append((mt, path))
        except:
            pass
    
    archived = 0
    for hour_key, files in by_hour.items():
        files.sort(reverse=True)
        hour_dir = os.path.join(archive_dir, hour_key)
        os.makedirs(hour_dir, exist_ok=True)
        for mt, path in files[:max_per_hour]:
            dest = os.path.join(hour_dir, os.path.basename(path))
            if not os.path.exists(dest):
                shutil.copy2(path, dest); archived += 1
    
    return {"ok": True, "archived": archived, "hours": len(by_hour), "total_screenshots": len(screenshots)}

def generate_timeline(archive_dir):
    """Generate screenshot timeline with gap detection."""
    entries = []
    for root, dirs, files in os.walk(archive_dir):
        for f in sorted(files):
            if f.endswith(".png"):
                path = os.path.join(root, f)
                sz = os.path.getsize(path)
                mt = os.path.getmtime(path)
                ts = datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M")
                entries.append({"time": ts, "file": f, "size": sz, "hour": os.path.basename(root)})
    
    entries.sort(key=lambda e: e["time"])
    gaps = []
    for i in range(1, len(entries)):
        try:
            prev = datetime.strptime(entries[i-1]["time"], "%Y-%m-%d %H:%M")
            curr = datetime.strptime(entries[i]["time"], "%Y-%m-%d %H:%M")
            dt = (curr - prev).total_seconds()
            if dt > 1800:
                gaps.append({"from": entries[i-1]["time"], "to": entries[i]["time"], "gap_minutes": int(dt/60)})
        except:
            pass
    
    return {"ok": True, "total_screenshots": len(entries), "gaps": gaps, "gap_count": len(gaps)}
