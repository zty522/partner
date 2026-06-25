#!/usr/bin/env python3
"""Bridge: convert daily dialogue logs to GUI-compatible dialog_history.jsonl.

The GUI (Partner.exe) reads workspace/state/dialog_history.jsonl (old format).
Current partner writes to instances/<id>/dialogue/YYYY-MM-DD.log (daily format).
This script converts daily logs to the format the GUI expects.

Run periodically or from the Partner startup script.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


def convert_dialogue_to_history(workspace_root: str, instance_id: str = "03") -> None:
    """Read daily dialogue logs and write dialog_history.jsonl for the GUI."""
    dialogue_dir = Path(workspace_root) / "instances" / instance_id / "dialogue"
    if not dialogue_dir.is_dir():
        print(f"[WARN] Dialogue dir not found: {dialogue_dir}")
        return

    output_path = Path(workspace_root) / "state" / "dialog_history.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    turns = []
    for log_file in sorted(dialogue_dir.glob("*.log")):
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            # Parse standalone Q: (user) and A: (partner) messages
            if line.startswith("  Q: "):
                q_text = line[4:].strip()
                if q_text:
                    turns.append({
                        "timestamp": log_file.stem,
                        "time": datetime.now().isoformat(),
                        "role": "user",
                        "content": q_text,
                    })
            elif line.startswith("  A: "):
                a_text = line[4:].strip()
                if a_text:
                    turns.append({
                        "timestamp": log_file.stem,
                        "time": datetime.now().isoformat(),
                        "role": "assistant",
                        "content": a_text,
                    })

    with open(output_path, "w", encoding="utf-8") as f:
        for turn in turns:
            f.write(json.dumps(turn, ensure_ascii=False) + "\n")

    print(f"[OK] Converted {len(turns)} dialogue turns to {output_path}")


if __name__ == "__main__":
    ws = sys.argv[1] if len(sys.argv) > 1 else "/mnt/e/work/partner_workspace"
    instance = sys.argv[2] if len(sys.argv) > 2 else "03"
    convert_dialogue_to_history(ws, instance)
