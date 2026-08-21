"""Save latest outputs to stable path for cross-round cumulative reading."""
import os, shutil, glob, json, time

def save_latest(workspace, instance_id=""):
    """Copy latest output files to state/latest/ for next round to read."""
    latest_dir = os.path.join(workspace, "state", "latest")
    os.makedirs(latest_dir, exist_ok=True)
    
    all_files = []
    # Scan both task outputs and screenshots
    for pat in ["*.md", "*.csv", "*.json", "*.png", "*.txt"]:
        for f in glob.glob(os.path.join(workspace, "state", "tasks", "*", pat)):
            sz = os.path.getsize(f)
            if sz > 200 and 'error' not in f.lower() and 'task_instance' not in f and 'step_' not in f:
                all_files.append((os.path.getmtime(f), f, os.path.basename(f)))
    # Also scan screenshots directory
    for pat in ["*.png", "*.csv"]:
        for f in glob.glob(os.path.join(workspace, "state", "screenshots", pat)):
            sz = os.path.getsize(f)
            if sz > 200 and 'error' not in f.lower() and 'task_instance' not in f and 'step_' not in f:
                all_files.append((os.path.getmtime(f), f, os.path.basename(f)))
    all_files.sort(reverse=True)
    
    saved = []
    seen = set()
    for mt, fp, fn in all_files:
        if fn in seen: continue
        seen.add(fn)
        dest = os.path.join(latest_dir, fn)
        try:
            shutil.copy2(fp, dest)
            saved.append(fn)
        except: pass
        if len(saved) >= 10: break
    
    with open(os.path.join(latest_dir, "manifest.json"), "w") as f:
        json.dump({"updated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "files": saved, "count": len(saved)}, f, ensure_ascii=False, indent=2)
    
    return {"ok": True, "saved": len(saved), "files": saved}
