#!/usr/bin/env python3
"""Heartbeat updater for Partner - runs every 5 minutes."""
import json
import datetime

path = "/mnt/e/work/partner_workspace/state/heartbeat.json"
with open(path, "r+") as f:
    hb = json.load(f)
    now = datetime.datetime.now().isoformat()
    hb["last_heartbeat"] = now
    hb["status"] = "idle"
    f.seek(0)
    json.dump(hb, f, indent=2)
    f.truncate()
print(f"OK {now}")
