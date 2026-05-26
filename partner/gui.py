#!/usr/bin/env python3
"""Partner 🤝 — Windows Desktop Application

A proper software window for managing Partner on Windows.
Opens a GUI (not terminal) for configuration and monitoring.
"""

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox
    HAS_TK = True
except ImportError:
    HAS_TK = False


def find_workspace():
    candidates = [
        os.path.expanduser("~/partner_workspace"),
        os.path.expanduser("~/.partner"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."),
    ]
    for p in candidates:
        sp = os.path.join(p, "state", "active_plan.json")
        if os.path.exists(sp):
            return p
    return None


WORKSPACE = find_workspace()
APP_DIR = os.path.dirname(os.path.abspath(__file__))


class PartnerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Partner \U0001f91d — AI Research Companion")
        self.root.geometry("780x580")
        self.root.minsize(640, 420)

        # Style
        style = ttk.Style()
        style.theme_use("clam")
        bg = "#f5f5f5"
        self.root.configure(bg=bg)

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Tabs
        self._build_status_tab()
        self._build_config_tab()
        self._build_log_tab()

        self._refresh_status()
        self._auto_refresh()

    # ── Status Tab ──
    def _build_status_tab(self):
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="\U0001f4ca Status")
        main = ttk.Frame(f)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Status text area
        self.status_text = tk.Text(main, height=14, font=("Consolas", 10),
                                   bg="#1e1e1e", fg="#d4d4d4", relief=tk.FLAT, padx=8, pady=8)
        self.status_text.pack(fill=tk.BOTH, expand=True)

        # Button bar
        btnf = ttk.Frame(main)
        btnf.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btnf, text="\U0001f504 Refresh", command=self._refresh_status).pack(side=tk.LEFT, padx=3)
        ttk.Button(btnf, text="\U0001f680 Force Run", command=self._force_run).pack(side=tk.LEFT, padx=3)
        ttk.Button(btnf, text="\U0001f9f9 Clear Queue", command=self._clear_queue).pack(side=tk.LEFT, padx=3)

    # ── Config Tab ──
    def _build_config_tab(self):
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="\u2699\ufe0f Config")
        main = ttk.Frame(f)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # QQ Bot section
        ttk.Label(main, text="QQ Bot", font=("", 13, "bold")).pack(anchor=tk.W)
        qqf = ttk.LabelFrame(main, text="QQ Official Bot", padding=10)
        qqf.pack(fill=tk.X, pady=8)

        sf = ttk.Frame(qqf)
        sf.pack(fill=tk.X)
        ttk.Label(sf, text="Status:").pack(side=tk.LEFT)
        self.qq_status = ttk.Label(sf, text="...", font=("", 10, "bold"))
        self.qq_status.pack(side=tk.LEFT, padx=10)

        bf = ttk.Frame(qqf)
        bf.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bf, text="\u25b6 Start", command=self._start_bot).pack(side=tk.LEFT, padx=3)
        ttk.Button(bf, text="\u23f9 Stop", command=self._stop_bot).pack(side=tk.LEFT, padx=3)

        # Update section
        ttk.Label(main, text="Updates", font=("", 13, "bold")).pack(anchor=tk.W, pady=(20, 0))
        uf = ttk.Frame(main)
        uf.pack(fill=tk.X, pady=5)
        ttk.Button(uf, text="\U0001f504 Update Partner", command=self._update_partner).pack(side=tk.LEFT, padx=3)
        self.update_status = ttk.Label(uf, text="")
        self.update_status.pack(side=tk.LEFT, padx=10)

    # ── Log Tab ──
    def _build_log_tab(self):
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="\U0001f4cb Log")
        main = ttk.Frame(f)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.log_text = scrolledtext.ScrolledText(main, font=("Consolas", 9), height=20)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        ttk.Button(main, text="\U0001f504 Reload", command=self._reload_log).pack(pady=5)

    # ── Status logic ──
    def _refresh_status(self):
        if not WORKSPACE:
            self._set_status("No Partner workspace found.\nRun: partner setup")
            return

        lines = []
        plan_path = os.path.join(WORKSPACE, "state", "active_plan.json")
        if os.path.exists(plan_path):
            with open(plan_path) as f:
                p = json.load(f)
            lines.append(f"Status:      {p.get('status', '?')}")
            lines.append(f"Project:     {p.get('title', '-')}")
            lines.append(f"Goal:        {p.get('goal', '-')}")
            phases = p.get("phases", [])
            cur = p.get("current_phase_index", 0)
            for i, ph in enumerate(phases):
                ic = "\u25b6" if i == cur else "\u2713" if ph.get("status") == "completed" else "\u23f3"
                lines.append(f"  {ic} {ph.get('name','?')} [{ph.get('status','?')}]")
            lines.append(f"Summary:     {p.get('heartbeat_summary', '-')}")

        stats_path = os.path.join(WORKSPACE, "state", "stats.json")
        if os.path.exists(stats_path):
            with open(stats_path) as f:
                s = json.load(f)
            lines.append("")
            lines.append(f"Cycles:      {s.get('total_cycles', 0)}")
            lines.append(f"Tasks done:  {s.get('total_tasks_completed', 0)}")
            lines.append(f"Knowledge:   {s.get('total_knowledge_entries', 0)}")

        # QQ bot status
        pid_path = os.path.join(WORKSPACE, "state", "qq_bot.pid")
        if os.path.exists(pid_path):
            with open(pid_path) as f:
                pid = f.read().strip()
            try:
                os.kill(int(pid), 0)
                lines.append("")
                lines.append(f"QQ Bot:      Running (PID: {pid})")
                self.qq_status.config(text=f"Running (PID: {pid})", foreground="green")
            except Exception:
                lines.append("")
                lines.append("QQ Bot:      Stopped")
                self.qq_status.config(text="Stopped", foreground="red")
        else:
            self.qq_status.config(text="Not configured", foreground="gray")

        self._set_status("\n".join(lines))

    def _set_status(self, text):
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete(1.0, tk.END)
        self.status_text.insert(tk.END, text)
        self.status_text.config(state=tk.DISABLED)

    def _auto_refresh(self):
        self._refresh_status()
        self.root.after(15000, self._auto_refresh)

    # ── Actions ──
    def _force_run(self):
        if not messagebox.askyesno("Force Run", "Start research execution now?"):
            return
        threading.Thread(target=lambda: os.system(
            f'start /B hermes -z "读取 {WORKSPACE}/state/active_plan.json 推进研究" --skills partner-research >nul 2>&1'
        ), daemon=True).start()
        messagebox.showinfo("Done", "Research started in background")

    def _clear_queue(self):
        os.system("partner queue clear >nul 2>&1" if os.name == "nt" else "partner queue clear 2>/dev/null")
        self._refresh_status()

    def _start_bot(self):
        threading.Thread(target=lambda: os.system(
            "start /B partner bot start qq >nul 2>&1" if os.name == "nt"
            else "partner bot start qq 2>/dev/null"
        ), daemon=True).start()
        self.root.after(2000, self._refresh_status)

    def _stop_bot(self):
        os.system("partner bot stop qq >nul 2>&1" if os.name == "nt" else "partner bot stop qq 2>/dev/null")
        self._refresh_status()

    def _update_partner(self):
        self.update_status.config(text="Updating...")
        threading.Thread(target=self._do_update, daemon=True).start()

    def _do_update(self):
        r = subprocess.run(["partner", "update"], capture_output=True, text=True, timeout=120)
        self.root.after(0, lambda: self.update_status.config(
            text="\u2705 Updated" if r.returncode == 0 else "\u274c Failed"
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


def main():
    root = tk.Tk()
    app = PartnerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    if not HAS_TK:
        print("Error: tkinter not available. Install Python with 'tcl/tk' support.")
        sys.exit(1)
    try:
        main()
    except Exception as e:
        import traceback
        with open(os.path.expanduser("~/partner_gui_error.log"), "w") as f:
            traceback.print_exc(file=f)
        print(f"GUI error: {e}")
        print("See ~/partner_gui_error.log for details")
        input("Press Enter to exit...")
