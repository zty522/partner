#!/usr/bin/env python3
"""Daily workspace maintenance - organize, journal, notify."""
import sys, json, os
sys.path.insert(0, '/mnt/e/work/partner')
from partner.workspace_manager import run_daily_maintenance

ws = '/mnt/e/work/partner_workspace'
result = run_daily_maintenance(ws)

print(f"Workspace: {len(result['actions'])} actions")
for a in result['actions']:
    print(f"  {a}")
print(f"Summary: {result['summary']}")

# Write notification to queue (picked up by running QQ bridge)
if result['summary']:
    notif = {
        "timestamp": __import__('datetime').datetime.now().isoformat(),
        "summary": result['summary'],
        "interesting": result.get('interesting', []),
    }
    notif_dir = os.path.join(ws, "state", "notifications")
    os.makedirs(notif_dir, exist_ok=True)
    with open(os.path.join(notif_dir, "daily_summary.json"), "w") as f:
        json.dump(notif, f, ensure_ascii=False, indent=2)
    print(f"Notification queued")
