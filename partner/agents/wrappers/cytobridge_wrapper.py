#!/usr/bin/env python3
"""CytoBridge Agent Wrapper — adds progress tracking."""
import argparse, json, os, subprocess, sys, time, threading

CYTOBRIDGE_PYTHON = os.path.expanduser("~/miniconda3/envs/cytobridge/bin/python")
CYTOBRIDGE_MODULE = "cytobridge_agent.cli"

def _write_progress(output_dir, stage, pct, msg, eta=None):
    try:
        path = os.path.join(output_dir, "progress.jsonl")
        entry = {"ts": time.time(), "stage": stage, "pct": pct, "msg": msg}
        if eta: entry["eta_seconds"] = eta
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

def _monitor_output(output_dir, stop_event):
    stages = [
        ("converted_input", 20, "Loading & converting data..."),
        ("figures", 40, "Generating visualizations..."),
        ("report.html", 80, "Building HTML report..."),
    ]
    seen = set()
    last_pct = 0
    last_png_count = 0
    start = time.time()
    while not stop_event.is_set():
        if not os.path.isdir(output_dir):
            stop_event.wait(2)
            continue
        all_files = set()
        for root, dirs, files in os.walk(output_dir):
            for fn in files:
                all_files.add(fn)
        new_files = all_files - seen
        if new_files:
            seen = all_files
            for marker, pct, msg in stages:
                if any(marker in f for f in new_files) and pct > last_pct:
                    elapsed = time.time() - start
                    eta = int(elapsed / max(pct, 1) * (100 - pct)) if pct > 0 else None
                    _write_progress(output_dir, "processing", pct, msg, eta)
                    last_pct = pct
        # Track PNG count for more granular progress
        figures_dir = os.path.join(output_dir, "figures")
        if os.path.isdir(figures_dir):
            png_count = len([f for f in os.listdir(figures_dir) if f.endswith('.png')])
            if png_count > last_png_count:
                last_png_count = png_count
                pct = min(40 + png_count * 5, 75)  # 40-75% based on figure count
                if pct > last_pct:
                    elapsed = time.time() - start
                    eta = int(elapsed / max(pct, 1) * (100 - pct)) if pct > 0 else None
                    _write_progress(output_dir, "viz", pct, f"Generated {png_count} figures", eta)
                    last_pct = pct
        # Check for HTML report
        html_path = os.path.join(output_dir, "report.html")
        if os.path.isfile(html_path) and last_pct < 85:
            _write_progress(output_dir, "report", 85, "HTML report generated", None)
            last_pct = 85
        stop_event.wait(3)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--partner-output", default="")
    args, remaining = parser.parse_known_args()
    output_dir = args.partner_output or os.environ.get("CYTOBRIDGE_OUTPUT", "")
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    cmd = [CYTOBRIDGE_PYTHON, "-m", CYTOBRIDGE_MODULE] + remaining
    stop_event = threading.Event()
    if output_dir:
        _write_progress(output_dir, "setup", 5, "Starting CytoBridge...", None)
        t = threading.Thread(target=_monitor_output, args=(output_dir, stop_event))
        t.daemon = True
        t.start()
    start = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=14400,
                          env={**os.environ, "PYTHONUNBUFFERED": "1"})
    except subprocess.TimeoutExpired:
        # Check if report was generated despite timeout
        has_report = os.path.isfile(os.path.join(output_dir, "report.html")) if output_dir else False
        if has_report:
            elapsed = time.time() - start
            if output_dir:
                _write_progress(output_dir, "done", 100, f"Completed (report found after timeout) in {elapsed:.0f}s", None)
            print(json.dumps({"ok": True, "elapsed": round(elapsed,1), "output_dir": output_dir,
                              "has_report": True, "note": "report found after timeout"}))
            sys.exit(0)
        if output_dir:
            _write_progress(output_dir, "timeout", 50, "Timed out after 4h", None)
        print(json.dumps({"ok": False, "error": "Timed out after 4h"}))
        sys.exit(1)
    finally:
        stop_event.set()
    elapsed = time.time() - start
    if output_dir:
        _write_progress(output_dir, "done", 100, f"Completed in {elapsed:.0f}s", None)
    has_report = os.path.isfile(os.path.join(output_dir, "report.html")) if output_dir else False
    print(json.dumps({"ok": r.returncode == 0 or has_report, "elapsed": round(elapsed,1),
                       "returncode": r.returncode, "output_dir": output_dir,
                       "has_report": has_report}, ensure_ascii=False))

if __name__ == "__main__":
    main()
