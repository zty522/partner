#!/usr/bin/env python3
"""Partner 🤝 — Windows GUI

A desktop application for managing Partner on Windows.
Opens a proper window (not a terminal) for configuration and monitoring.
"""

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime
from pathlib import Path

# ── Try to find the workspace ──
def find_workspace():
    candidates = [
        os.path.expanduser("~/partner_workspace"),
        os.path.expanduser("~/.partner_workspace"),
        os.path.expanduser("~/.partner/workspace"),
    ]
    for p in candidates:
        if os.path.exists(os.path.join(p, "state", "active_plan.json")):
            return p
    return None

WORKSPACE = find_workspace()


class PartnerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Partner 🤝")
        self.root.geometry("720x540")
        self.root.minsize(600, 400)

        # Set icon if available
        try:
            self.root.iconbitmap(default=os.path.join(os.path.dirname(__file__), "..", "icon.ico"))
        except Exception:
            pass

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ── Status tab ──
        self.status_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.status_frame, text="📊 Status")
        self._build_status_tab()

        # ── Config tab ──
        self.config_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.config_frame, text="⚙️ Config")
        self._build_config_tab()

        # ── Log tab ──
        self.log_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.log_frame, text="📋 Log")
        self._build_log_tab()

        # Auto-refresh status every 10s
        self._auto_refresh()

    def _build_status_tab(self):
        main = ttk.Frame(self.status_frame)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Status info
        self.status_text = tk.Text(main, height=12, font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4", relief=tk.FLAT)
        self.status_text.pack(fill=tk.BOTH, expand=True)
        self.status_text.insert(tk.END, "Loading status...\n")
        self.status_text.config(state=tk.DISABLED)

        # Buttons
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btn_frame, text="🔄 Refresh", command=self._refresh_status).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🚀 Force Run", command=self._force_run).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🧹 Clear Queue", command=self._clear_queue).pack(side=tk.LEFT, padx=2)

    def _build_config_tab(self):
        main = ttk.Frame(self.config_frame)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # QQ Bot config
        ttk.Label(main, text="QQ Bot", font=("", 12, "bold")).pack(anchor=tk.W)
        qq_frame = ttk.LabelFrame(main, text="QQ Official Bot", padding=8)
        qq_frame.pack(fill=tk.X, pady=5)

        ttk.Label(qq_frame, text="Status:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.qq_status = ttk.Label(qq_frame, text="Checking...")
        self.qq_status.grid(row=0, column=1, sticky=tk.W, padx=5)

        ttk.Button(qq_frame, text="▶ Start Bot", command=self._start_bot).grid(row=1, column=0, padx=5, pady=2)
        ttk.Button(qq_frame, text="⏹ Stop Bot", command=self._stop_bot).grid(row=1, column=1, padx=5, pady=2)

        # Partner update
        ttk.Label(main, text="Updates", font=("", 12, "bold")).pack(anchor=tk.W, pady=(15, 0))
        update_frame = ttk.Frame(main)
        update_frame.pack(fill=tk.X, pady=5)
        ttk.Button(update_frame, text="🔄 Update Partner", command=self._update_partner).pack(side=tk.LEFT, padx=2)
        self.update_status = ttk.Label(update_frame, text="")
        self.update_status.pack(side=tk.LEFT, padx=10)

    def _build_log_tab(self):
        main = ttk.Frame(self.log_frame)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.log_text = scrolledtext.ScrolledText(main, font=("Consolas", 9), height=20)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        ttk.Button(main, text="🔄 Reload Log", command=self._reload_log).pack(pady=5)

    def _refresh_status(self):
        """Read state files and update the status display."""
        if not WORKSPACE:
            self._set_status_text("No Partner workspace found.\nRun 'partner setup' first.")
            return

        lines = []
        # active_plan
        plan_path = os.path.join(WORKSPACE, "state", "active_plan.json")
        if os.path.exists(plan_path):
            with open(plan_path) as f:
                plan = json.load(f)
            lines.append(f"Status:      {plan.get('status', '?')}")
            lines.append(f"Project:     {plan.get('title', '-')}")
            lines.append(f"Goal:        {plan.get('goal', '-')}")
            phases = plan.get("phases", [])
            cur = plan.get("current_phase_index", 0)
            for i, p in enumerate(phases):
                icon = "▶" if i == cur else "✓" if p.get("status") == "completed" else "⏳"
                lines.append(f"  {icon} {p.get('name', '?')} [{p.get('status', '?')}]")
            lines.append(f"Summary:     {plan.get('heartbeat_summary', '-')}")

        # stats
        stats_path = os.path.join(WORKSPACE, "state", "stats.json")
        if os.path.exists(stats_path):
            with open(stats_path) as f:
                stats = json.load(f)
            lines.append(f"")
            lines.append(f"Cycles:      {stats.get('total_cycles', 0)}")
            lines.append(f"Tasks done:  {stats.get('total_tasks_completed', 0)}")
            lines.append(f"Knowledge:   {stats.get('total_knowledge_entries', 0)}")

        # QQ bot
        pid_path = os.path.join(WORKSPACE, "state", "qq_bot.pid")
        if os.path.exists(pid_path):
            with open(pid_path) as f:
                pid = f.read().strip()
            try:
                os.kill(int(pid), 0)
                lines.append(f"")
                lines.append(f"QQ Bot:      Running (PID: {pid}) ✅")
                self.qq_status.config(text=f"Running (PID: {pid})", foreground="green")
            except Exception:
                lines.append(f"")
                lines.append(f"QQ Bot:      Not running ❌")
                self.qq_status.config(text="Not running", foreground="red")
        else:
            self.qq_status.config(text="Not configured", foreground="gray")

        self._set_status_text("\n".join(lines))

    def _set_status_text(self, text):
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete(1.0, tk.END)
        self.status_text.insert(tk.END, text)
        self.status_text.config(state=tk.DISABLED)

    def _auto_refresh(self):
        self._refresh_status()
        self.root.after(10000, self._auto_refresh)

    def _force_run(self):
        if not WORKSPACE:
            return
        msgbox = messagebox.askyesno("Force Run", "Start research execution now?")
        if not msgbox:
            return
        threading.Thread(target=self._run_cmd, args=(
            ["partner", "queue", "clear"],
            ["hermes", "-z", f"读取 {WORKSPACE}/state/active_plan.json，推进研究"],
        ), daemon=True).start()

    def _clear_queue(self):
        if not WORKSPACE:
            return
        os.system(f'partner queue clear >nul 2>&1')
        self._refresh_status()

    def _start_bot(self):
        if not WORKSPACE:
            return
        threading.Thread(target=lambda: os.system(f'start /B partner bot start qq >nul 2>&1'), daemon=True).start()
        self._refresh_status()

    def _stop_bot(self):
        if not WORKSPACE:
            return
        os.system(f'partner bot stop qq >nul 2>&1')
        self._refresh_status()

    def _update_partner(self):
        self.update_status.config(text="Updating...")
        threading.Thread(target=self._do_update, daemon=True).start()

    def _do_update(self):
        result = subprocess.run(["partner", "update"], capture_output=True, text=True, timeout=120)
        self.root.after(0, lambda: self.update_status.config(
            text="✅ Updated" if result.returncode == 0 else "❌ Failed"
        ))

    def _reload_log(self):
        if not WORKSPACE:
            return
        log_path = os.path.join(WORKSPACE, "logs", "qq_bot.log")
        if os.path.exists(log_path):
            with open(log_path, errors="ignore") as f:
                lines = f.readlines()
            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(tk.END, "".join(lines[-100:]))
            self.log_text.see(tk.END)

    @staticmethod
    def _run_cmd(*cmds):
        for cmd in cmds:
            subprocess.run(cmd, capture_output=True, timeout=300)


def main():
    root = tk.Tk()
    app = PartnerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
