"""Dynamic worker autoscaler for the shared worker pool.

Scales `shared_worker` processes up and down based on real machine
resources (CPU load, available memory) and the global job queue depth.

Rules
-----
* Desired workers = running_jobs + ceil(queued_jobs / 2), clamped to
  [min_workers, max_workers].
* max_workers defaults to min(nproc // 2, 8) and never below 3; it can be
  overridden with ``--max-workers``.
* Scale-up is refused while the 1-minute load average is above
  ``load_ratio * nproc`` (default 0.75).
* Scale-down is forced when free memory drops below ``min_mem_gb`` (2 GB):
  extra workers are removed one per cycle until memory recovers.
* Slot N is started via ``/home/os/partner_scripts/start_worker_slot.sh N``;
  excess workers receive SIGTERM (EventWorker handles it gracefully at an
  Event boundary).
* Decisions are debounced (60 s) to avoid churn; a state file records the
  last action so we never start a worker that is already running and never
  kill one we did not start this cycle.

Usage: python -m partner.runtime.worker_autoscaler [--max-workers N]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

WORKSPACE = Path("/mnt/e/work/partner_workspace")
JOBS_DIR = WORKSPACE / "state/application/jobs"
SCRIPTS = Path("/home/os/partner_scripts")
START_SLOT = SCRIPTS / "start_worker_slot.sh"
STATE_FILE = Path("/tmp/worker_autoscaler_state.json")
MIN_WORKERS = 1
DEBOUNCE_S = 60
LOAD_RATIO = 0.75
MIN_MEM_GB = 2.0


def _nproc() -> int:
    try:
        return int(subprocess.run(["nproc"], capture_output=True, text=True,
                                  check=True).stdout.strip() or "1")
    except Exception:  # noqa: BLE001
        return 1


def _load_avg_1min() -> float:
    try:
        return float(os.getloadavg()[0])
    except Exception:  # noqa: BLE001
        return 0.0


def _mem_available_gb() -> float:
    try:
        lines = subprocess.run(["free", "-g"], capture_output=True, text=True,
                               check=True).stdout.splitlines()
        for line in lines:
            if line.startswith("Mem:"):
                parts = line.split()
                # total used free shared buff/cache available
                if len(parts) >= 7:
                    return float(parts[6])
    except Exception:  # noqa: BLE001
        return 0.0
    return 0.0


def _queue_depth() -> tuple[int, int]:
    """Return (running_jobs, queued_jobs) from the global job store."""
    running = queued = 0
    try:
        for path in JOBS_DIR.glob("*.json"):
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
                status = str(data.get("status") or "")
                if status in {"queued", "dispatched"}:
                    queued += 1
                elif status == "running":
                    running += 1
            except Exception:  # noqa: BLE001
                continue
    except OSError:
        pass
    return running, queued


def _live_workers() -> set[int]:
    """Slot indices of currently running shared workers."""
    live: set[int] = set()
    try:
        out = subprocess.run(["pgrep", "-af", "shared_worker"], capture_output=True,
                             text=True).stdout
        for line in out.splitlines():
            if "--slot-index" in line:
                idx = line.split("--slot-index")[-1].strip().split()[0]
                try:
                    live.add(int(idx))
                except ValueError:
                    continue
    except Exception:  # noqa: BLE001
        pass
    return live


def _start_slot(slot: int) -> None:
    subprocess.Popen(["setsid", "nohup", "bash", str(START_SLOT), str(slot)],
                     stdout=open(f"/tmp/partner_worker{slot}.log", "ab"),
                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)


def _stop_slot(slot: int) -> None:
    try:
        out = subprocess.run(["pgrep", "-af", f"shared_worker.*--slot-index {slot}"],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            pid = line.split()[0]
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (OSError, ValueError):
                continue
    except Exception:  # noqa: BLE001
        pass


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"last_action_ts": 0.0, "last_desired": -1}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-workers", type=int, default=0,
                        help="hard cap (default: min(nproc//2, 8), at least 3)")
    parser.add_argument("--min-workers", type=int, default=MIN_WORKERS)
    args = parser.parse_args()

    nproc = _nproc()
    max_workers = args.max_workers or max(3, min(nproc // 2, 8))
    min_workers = max(1, min(args.min_workers, max_workers))

    running, queued = _queue_depth()
    desired = max(min_workers, min(max_workers, running + (queued + 1) // 2))

    load = _load_avg_1min()
    mem = _mem_available_gb()
    if load > LOAD_RATIO * nproc and desired > min_workers:
        desired = min_workers  # machine busy: park to the floor
    if mem < MIN_MEM_GB:
        desired = min_workers  # memory pressure: shrink
    desired = max(min_workers, min(max_workers, desired))

    live = _live_workers()
    state = _load_state()
    now = time.time()
    if state.get("last_desired") == desired and now - state.get("last_action_ts", 0) < DEBOUNCE_S:
        _save_state({**state, "last_desired": desired, "last_action_ts": now})
        print(f"noop desired={desired} live={sorted(live)} running={running} queued={queued} "
              f"load={load:.2f}/{nproc} mem={mem:.1f}G")
        return 0

    started, stopped = [], []
    for slot in range(desired):
        if slot not in live:
            _start_slot(slot)
            started.append(slot)
    for slot in sorted(live):
        if slot >= desired:
            _stop_slot(slot)
            stopped.append(slot)

    _save_state({"last_desired": desired, "last_action_ts": now})
    print(f"desired={desired} live={sorted(live)} started={started} stopped={stopped} "
          f"running={running} queued={queued} load={load:.2f}/{nproc} mem={mem:.1f}G")
    return 0


if __name__ == "__main__":
    sys.exit(main())
