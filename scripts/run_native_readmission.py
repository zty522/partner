#!/usr/bin/env python3
"""Native readmission watchdog (long-run autonomy).

Autonomous continuation has two drivers:
  1. completion signal (terminal_bridge): a Job terminal -> next step
  2. slot readmission (this): a yielded/waiting instance + free slot -> next step

Without (2), an instance that yields its slot under the multi-instance fairness
quantum (instance_native_slot_quantum_project_steps) has no terminal to react
to and stalls forever.  recover_or_start() already encodes the admission policy
(busy / BLOCKED-without-evidence-change / bounded-application guards), so this
is a thin periodic sweep over it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from partner.governance.instance_native import (  # noqa: E402
    enabled_instances,
    load_state,
    recover_or_start,
)

# Statuses meaning "no readmission happened / not allowed right now".
QUIET = {
    "busy", "native_disabled", "application_job_precedes_auto_seed",
    "bounded_application_wait", "blocked_requires_evidence_change",
    "pending_dispatch",
}


def sweep(root: Path) -> list[dict]:
    rows: list[dict] = []
    for iid in enabled_instances(root):
        try:
            state = load_state(root, iid)
            before = state.phase
            result = recover_or_start(root, iid)
            rows.append({"instance_id": iid, "phase_before": before,
                         "status": result.get("status")})
        except Exception as exc:  # noqa: BLE001 — one instance must not stop the sweep
            rows.append({"instance_id": iid, "error": f"{type(exc).__name__}: {exc}"})
    return rows


def loop(root: Path, interval: float) -> None:
    while True:
        try:
            for row in sweep(root):
                if row.get("status") not in QUIET:
                    print(json.dumps({"event": "native_readmitted", **row},
                                     ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001 — watchdog must never die
            print(json.dumps({"event": "readmission_error",
                              "error": f"{type(exc).__name__}: {exc}"},
                             ensure_ascii=False), flush=True)
        time.sleep(max(5.0, interval))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="/mnt/e/work/partner_workspace")
    parser.add_argument("--interval", type=float, default=20)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.once:
        for row in sweep(root):
            print(json.dumps(row, ensure_ascii=False), flush=True)
        return 0
    loop(root, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
