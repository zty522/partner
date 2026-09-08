#!/usr/bin/env python3
"""ADR 0061 rebound: prepare every instance for the real-action contract.

Run this script ONCE after deploying the new contract to give blocked
instances a diagnostic yield (the contractual "watchdog last-resort"
lever, not the evidence-gated path). It writes a JSONL audit row to
``state/instance_native/events.jsonl`` and emits the resulting state so an
operator can verify every instance is ready to run under the new rules.

Usage:
    python scripts/rebound_instances_to_real_action_contract.py \
        --workspace /mnt/e/work/partner_workspace

The script is intentionally NOT a Campaign and not a periodic loop; it
exists solely so the operator can replace the legacy Campaign with the
new instance-native runtime cleanly.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from partner.governance.instance_native import (
    blocked_instances,
    enabled_instances,
    load_state,
    yield_blocked_without_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--reason",
                        default="ADR 0061 contract rebind: every instance "
                                "re-enters the cycle under the new "
                                "real-action contract.")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    configured = enabled_instances(root)
    if not configured:
        print(json.dumps({"status": "native_disabled"}))
        return 2
    audit_path = root / "state/instance_native/rebind_events.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    summary: list[dict] = []
    rebind_log = audit_path.open("a", encoding="utf-8")
    try:
        for entry in blocked_instances(root):
            instance_id = entry["instance_id"]
            result = yield_blocked_without_evidence(
                root, instance_id, reason=args.reason)
            row = {"ts": now, "action": "diagnostic_yield",
                   "instance_id": instance_id, "result": result}
            rebind_log.write(json.dumps(row, ensure_ascii=False) + "\n")
            rebind_log.flush()
            summary.append(row)
        for instance_id in configured:
            if any(item["instance_id"] == instance_id for item in summary):
                continue
            state = load_state(root, instance_id)
            summary.append({"ts": now, "action": "noop",
                            "instance_id": instance_id,
                            "phase": state.phase,
                            "reason": state.reason})
    finally:
        rebind_log.close()
    print(json.dumps({"status": "rebound_complete",
                      "rebinds": summary,
                      "audit_path": str(audit_path)}, ensure_ascii=False,
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
