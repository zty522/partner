import sys, os
sys.path.insert(0, r"E:\work\partner")

# 1. Test find_workspace
from partner.state.setup import find_workspace
ws = find_workspace()
print(f"find_workspace() = {ws}")

# 2. Test is_wsl_unc_path
from partner.desktop_gui.gui_qt import is_wsl_unc_path
print(f"is_wsl_unc_path({ws}) = {is_wsl_unc_path(ws)}")

# 3. Test readable_filesystem_path
from partner.desktop_gui.gui_qt import readable_filesystem_path
print(f"readable_path = {readable_filesystem_path(ws) if ws else 'N/A'}")

# 4. Check instances
if ws:
    from pathlib import Path
    p = Path(ws)
    print(f"Workspace exists: {p.exists()}")
    inst_dir = p / "instances"
    print(f"Instances dir: {inst_dir}, exists: {inst_dir.exists()}")
    for d in sorted(inst_dir.glob("*")):
        print(f"  Instance: {d.name}, dialogue: {(d/'dialogue').exists()}")
        log_files = sorted((d/'dialogue').glob("*.log"), reverse=True)
        if log_files:
            print(f"    Log files: {len(log_files)}, latest: {log_files[0].name}")
        else:
            print(f"    No log files!")

# 5. Test load_dialog_history
from partner.desktop_gui.gui_qt import load_dialog_history
turns = load_dialog_history(ws, n=10)
print(f"load_dialog_history returned {len(turns)} turns")
if turns:
    for t in turns[:3]:
        print(f"  role={t.get('role')}, content={str(t.get('content',''))[:60]}...")
