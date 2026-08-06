"""Agent Progress Protocol — generic progress reporting for agent subprocesses.

Any agent/tool wrapper writes progress.jsonl to its output directory.
The dispatcher reads it and forwards progress to the harness via callback.

Protocol (one JSON object per line):
    {"ts": "ISO", "step": "name", "percent": 0-100, "message": "...", "eta_seconds": N}
"""

from __future__ import annotations

import json, os, time
from datetime import datetime
from typing import Any


class ProgressWriter:
    """Write progress to {output_dir}/progress.jsonl."""

    def __init__(self, output_dir: str):
        os.makedirs(output_dir, exist_ok=True)
        self._path = os.path.join(output_dir, "progress.jsonl")
        self._start = time.time()

    def update(self, step: str, percent: float, message: str = "",
               eta_seconds: float | None = None, **extra: Any) -> None:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "step": step, "percent": round(percent, 1),
            "message": message,
            "eta_seconds": round(eta_seconds) if eta_seconds is not None else None,
            "elapsed_seconds": round(time.time() - self._start, 1),
        }
        entry.update(extra)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def done(self, message: str = "Complete") -> None:
        self.update("done", 100.0, message, eta_seconds=0)


class ProgressReader:
    """Read progress from {output_dir}/progress.jsonl."""

    def __init__(self, output_dir: str):
        self._path = os.path.join(output_dir, "progress.jsonl")
        self._seen = 0

    def poll(self) -> list[dict]:
        if not os.path.exists(self._path):
            return []
        with open(self._path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        new = all_lines[self._seen:]
        self._seen = len(all_lines)
        return [json.loads(l) for l in new if l.strip()]

    def latest(self) -> dict | None:
        entries = self.poll()
        return entries[-1] if entries else None

    def read_latest(self) -> dict | None:
        """Alias for latest() — used by dispatcher progress polling."""
        return self.latest()

    def format_message(self) -> str | None:
        latest = self.latest()
        if not latest:
            return None
        step = latest.get("step") or latest.get("stage", "?")
        pct = latest.get("percent") or latest.get("pct", 0)
        msg = latest.get("message") or latest.get("msg", "") or step
        eta = latest.get("eta_seconds")
        elapsed = latest.get("elapsed_seconds", 0)
        parts = [f"[进度] {msg}"]
        if pct > 0:
            bar_len = 10
            filled = int(pct / 100 * bar_len)
            bar = "\u2588" * filled + "\u2591" * (bar_len - filled)
            parts.append(f" {bar} {pct:.0f}%")
        if eta and eta > 0:
            parts.append(f" 预计还需 ~{eta/60:.0f}分钟" if eta > 60 else f" 预计还需 ~{eta:.0f}秒")
        elif elapsed > 60:
            parts.append(f" 已运行 {elapsed/60:.0f}分钟")
        return "".join(parts)
