#!/usr/bin/env python3
"""Partner — Modern Windows Desktop Application (v5)

A polished GUI for managing Partner on Windows.
Features: Dashboard, Chat, QQ Bot Config, Logs
All backend commands run silently without terminal windows.
"""

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from datetime import datetime
from pathlib import Path

# ── Constants ──
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PARTNER_DIR = os.path.dirname(APP_DIR)
WORKSPACE_CANDIDATES = [
    os.path.expanduser("~/partner_workspace"),
    os.path.expanduser("~/.partner"),
    PARTNER_DIR,
]
CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
AUTO_REFRESH_INTERVAL = 15000
CHAT_HISTORY_LIMIT = 30
LOG_FILE_LIMIT = 5
LOG_BYTE_LIMIT = 4000

# ── Theme ──
T = {
    "bg":        "#0c0c1a",
    "bg2":       "#13132a",
    "bg3":       "#181838",
    "card":      "#191938",
    "card_hl":   "#252550",
    "accent":    "#7c5cfc",
    "accent2":   "#a78bfa",
    "accent3":   "#5b41d4",
    "accent_h":  "#9480f5",
    "green":     "#34d399",
    "yellow":    "#fbbf24",
    "red":       "#f87171",
    "blue":      "#60a5fa",
    "pink":      "#e879f9",
    "txt":       "#e2e8f0",
    "txt2":      "#94a3b8",
    "txt3":      "#5b6b8f",
    "border":    "#1e1e44",
    "input_bg":  "#0e0e24",
    "chat_user": "#2a1f60",
    "chat_bot":  "#1a2238",
    "glow":      "#1a1a50",
}


def find_workspace():
    for p in WORKSPACE_CANDIDATES:
        if os.path.exists(os.path.join(p, "partner", "__init__.py")):
            return p
    return None


def run_silent(cmd, cwd=None, timeout=30):
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=cwd or PARTNER_DIR, creationflags=CREATION_FLAGS, env=env,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", 1
    except Exception as e:
        return "", str(e), 1


def load_dialog_history(workspace, n=50):
    hist_path = os.path.join(workspace, "state", "dialog_history.jsonl")
    if not os.path.exists(hist_path):
        return []
    turns = []
    try:
        with open(hist_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-n:]:
            try:
                data = json.loads(line.strip(), strict=False)
                turns.append(data)
            except Exception:
                continue
    except Exception:
        pass
    return turns


def center_window(win, w, h):
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = (sw - w) // 2
    y = (sh - h) // 2
    win.geometry(f"{w}x{h}+{x}+{y}")


    # Remove ttk style configuration — using custom tab bar


# ════════════════════════════════════════════════════════════════
#  UI Components
# ════════════════════════════════════════════════════════════════

class AccentCard(tk.Frame):
    """Card with subtle accent-colored top border line."""
    def __init__(self, parent, title=None, accent_color=None, **kw):
        super().__init__(parent, bg=T["card"], highlightthickness=0, **kw)
        # Subtle accent line at top
        line = tk.Frame(self, bg=accent_color or T["accent"], height=2)
        line.pack(fill=tk.X)
        line.pack_propagate(False)
        # Body with generous internal padding
        body = tk.Frame(self, bg=T["card"])
        body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        if title:
            h = tk.Frame(body, bg=T["card"])
            h.pack(fill=tk.X, padx=18, pady=(16, 2))
            tk.Label(h, text=title, bg=T["card"], fg=T["accent2"],
                     font=("Segoe UI", 12, "bold"), anchor=tk.W).pack(side=tk.LEFT)
        self.body = body


class Input(tk.Entry):
    def __init__(self, parent, **kw):
        kw.setdefault("bg", T["input_bg"])
        kw.setdefault("fg", T["txt"])
        kw.setdefault("insertbackground", T["accent2"])
        kw.setdefault("font", ("Cascadia Code", 10))
        kw.setdefault("relief", tk.FLAT)
        kw.setdefault("highlightbackground", T["border"])
        kw.setdefault("highlightthickness", 1)
        kw.setdefault("highlightcolor", T["accent"])
        super().__init__(parent, **kw)
        self.bind("<FocusIn>", lambda e: self.configure(highlightbackground=T["accent"]))
        self.bind("<FocusOut>", lambda e: self.configure(highlightbackground=T["border"]))


class Btn(tk.Frame):
    """Custom button with hover animation and optional icon."""
    def __init__(self, parent, text="", icon=None, command=None,
                 bg=None, fg=None, hover=None, **kw):
        self._cmd = command
        bg = bg or T["card_hl"]
        fg = fg or T["txt"]
        hover = hover or T["accent"]
        super().__init__(parent, bg=bg, **kw)

        self._label = tk.Label(self, text=text, bg=bg, fg=fg,
                               font=("Segoe UI", 10), cursor="hand2",
                               padx=16, pady=7)
        self._label.pack()
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover
        self._hover_fg = "white" if hover != bg else fg

        self._label.bind("<Button-1>", lambda e: self._cmd() if self._cmd else None)
        self.bind("<Button-1>", lambda e: self._cmd() if self._cmd else None)
        self._label.bind("<Enter>", self._on_enter)
        self._label.bind("<Leave>", self._on_leave)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_enter(self, event):
        self._label.configure(bg=self._hover_bg, fg=self._hover_fg)
        self.configure(bg=self._hover_bg)

    def _on_leave(self, event):
        self._label.configure(bg=self._bg, fg=self._fg)
        self.configure(bg=self._bg)


# ════════════════════════════════════════════════════════════════
#  Main Application
# ════════════════════════════════════════════════════════════════

class PartnerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Partner")
        self.root.minsize(820, 620)
        self.root.configure(bg=T["bg"])
        center_window(self.root, 1000, 720)

        self.workspace = find_workspace()
        self._auto_refresh_id = None
        self._build_ui()

        if not self.workspace:
            self._set_status(
                "Workspace not configured.\n\nClick 'Setup Wizard' to get started.",
                T["yellow"])
            self.root.after(500, self._show_setup)
        else:
            self._refresh_status()
            self._load_chat_history()
            self._start_auto_refresh()

    # ──────────── UI Layout ────────────
    def _build_ui(self):
        main = tk.Frame(self.root, bg=T["bg"])
        main.pack(fill=tk.BOTH, expand=True, padx=24, pady=(16, 20))

        # ── Header ──
        hdr = tk.Frame(main, bg=T["bg"])
        hdr.pack(fill=tk.X, pady=(0, 10))

        lf = tk.Frame(hdr, bg=T["bg"])
        lf.pack(side=tk.LEFT)
        # App icon badge
        badge = tk.Canvas(lf, width=34, height=34, bg=T["accent3"],
                          highlightthickness=0)
        badge.pack(side=tk.LEFT, padx=(0, 10))
        badge.create_oval(4, 4, 30, 30, fill=T["accent"], outline="")
        badge.create_text(17, 17, text="P", fill="white",
                          font=("Segoe UI", 16, "bold"))

        tk.Label(lf, text="Partner", bg=T["bg"], fg=T["txt"],
                 font=("Segoe UI", 24, "bold")).pack(side=tk.LEFT)
        tk.Label(lf, text="v0.3.0", bg=T["bg"], fg=T["txt3"],
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(8, 0), pady=(6, 0))

        # Right: status dot only (cleaner)
        rf = tk.Frame(hdr, bg=T["bg"])
        rf.pack(side=tk.RIGHT)
        # Glowing status dot
        self.dot_canvas = tk.Canvas(rf, width=14, height=14, bg=T["bg"],
                                    highlightthickness=0)
        self.dot_canvas.pack(side=tk.LEFT, padx=(0, 6))
        self.dot_id_outer = self.dot_canvas.create_oval(2, 2, 12, 12,
            fill=T["txt3"], outline=T["border"], width=1)
        self.dot_id_inner = self.dot_canvas.create_oval(5, 5, 9, 9,
            fill=T["txt3"], outline="", width=0)
        self.hdr_status = tk.Label(rf, text="", bg=T["bg"], fg=T["txt3"],
                                   font=("Segoe UI", 9))
        self.hdr_status.pack(side=tk.LEFT)

        # ── Custom Tab Bar (replaces ttk Notebook for full control) ──
        self._tab_frame = tk.Frame(main, bg=T["bg"])
        self._tab_frame.pack(fill=tk.X, pady=(0, 0))

        # Tab bar background
        tab_bar = tk.Frame(self._tab_frame, bg=T["bg2"], height=42)
        tab_bar.pack(fill=tk.X)
        tab_bar.pack_propagate(False)

        # Tab definitions: (label, icon_emoji)
        self._tabs = [
            ("Dashboard", "\U0001f4ca"),
            ("Chat",      "\U0001f4ac"),
            ("QQ Bot",    "\U0001f916"),
            ("Logs",      "\U0001f4c4"),
        ]
        self._tab_buttons = []
        self._tab_indicators = []
        self._tab_contents = []

        for idx, (name, icon) in enumerate(self._tabs):
            # Tab button
            is_active = (idx == 0)
            tab_bg = T["card_hl"] if is_active else T["bg3"]
            tab_fg = T["txt"] if is_active else T["txt3"]
            btn = tk.Frame(tab_bar, bg=tab_bg, cursor="hand2")
            btn.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 1))
            label = tk.Label(btn, text=f"  {icon}  {name}  ",
                             bg=tab_bg, fg=tab_fg,
                             font=("Segoe UI", 10), padx=10)
            label.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

            def make_handler(i):
                return lambda e, idx=i: self._switch_tab(idx)
            btn.bind("<Button-1>", make_handler(idx))
            label.bind("<Button-1>", make_handler(idx))

            # Hover effect
            def make_hover(i):
                def on_enter(e):
                    if i != self._active_tab:
                        for c in e.widget.winfo_children():
                            if isinstance(c, tk.Label):
                                c.configure(bg=T["card_hl"])
                        e.widget.configure(bg=T["card_hl"])
                def on_leave(e):
                    if i != self._active_tab:
                        for c in e.widget.winfo_children():
                            if isinstance(c, tk.Label):
                                c.configure(bg=T["bg3"])
                        e.widget.configure(bg=T["bg3"])
                return on_enter, on_leave
            enter_fn, leave_fn = make_hover(idx)
            btn.bind("<Enter>", enter_fn)
            btn.bind("<Leave>", leave_fn)
            label.bind("<Enter>", enter_fn)
            label.bind("<Leave>", leave_fn)

            self._tab_buttons.append(btn)

            # Content frame (hidden initially)
            content = tk.Frame(main, bg=T["bg"])
            if idx != 0:
                content.pack_forget()
            else:
                content.pack(fill=tk.BOTH, expand=True)
            self._tab_contents.append(content)

        # Active tab bottom accent line
        self._active_tab = 0

        # Build all tab contents
        self._build_tab_dashboard()
        self._build_tab_chat()
        self._build_tab_qq()
        self._build_tab_logs()

    def _switch_tab(self, idx):
        """Switch to tab at given index."""
        if idx == self._active_tab:
            return
        # Hide old content
        self._tab_contents[self._active_tab].pack_forget()
        # Show new content
        self._tab_contents[idx].pack(fill=tk.BOTH, expand=True)
        # Update button styles
        for i, btn in enumerate(self._tab_buttons):
            bg = T["card_hl"] if i == idx else T["bg3"]
            fg = T["txt"] if i == idx else T["txt3"]
            for child in btn.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=bg, fg=fg)
            btn.configure(bg=bg)
        self._active_tab = idx
        # Refresh if switching to dashboard
        if idx == 0:
            self._refresh_status()

        # ── Bottom status bar ──
        bar_frame = tk.Frame(main, bg=T["bg2"])
        bar_frame.pack(fill=tk.X, pady=(8, 0))
        self.status_bar = tk.Label(bar_frame, text="Ready", bg=T["bg2"], fg=T["txt3"],
                                    font=("Segoe UI", 9), anchor=tk.W, padx=14, pady=5)
        self.status_bar.pack(fill=tk.X)

    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    #  Dashboard Tab
    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    def _build_tab_dashboard(self):
        f = self._tab_contents[0]
        f.configure(bg=T["bg"])

        sc = AccentCard(f, title="Status", accent_color=T["blue"])
        sc.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 10))

        self.status_text = tk.Text(sc.body, height=12, font=("Cascadia Code", 10),
                                   bg=T["bg2"], fg=T["txt"], relief=tk.FLAT,
                                   padx=18, pady=14, wrap=tk.WORD,
                                   insertbackground=T["txt"],
                                   selectbackground=T["accent"], selectforeground="white",
                                   highlightbackground=T["border"], highlightthickness=1,
                                   borderwidth=0)
        self.status_text.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 14))
        self._set_status("Loading...")

        bf = tk.Frame(f, bg=T["bg"])
        bf.pack(fill=tk.X, padx=0, pady=(0, 0))
        Btn(bf, text="  \U0001f504  Refresh  ", command=self._refresh_status).pack(side=tk.LEFT, padx=(0, 8))
        Btn(bf, text="  \U0001f4c2  Open Workspace  ", command=self._open_workspace).pack(side=tk.LEFT, padx=(0, 8))
        Btn(bf, text="  \u2699\ufe0f  Setup Wizard  ", bg=T["accent"], fg="white",
            hover=T["accent_h"], command=self._show_setup).pack(side=tk.RIGHT)

    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    #  Chat Tab
    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    def _build_tab_chat(self):
        f = self._tab_contents[1]
        f.configure(bg=T["bg"])

        # Chat display
        chat_frame = tk.Frame(f, bg=T["bg2"], highlightbackground=T["border"],
                              highlightthickness=1)
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 6))

        self.chat_canvas = tk.Canvas(chat_frame, bg=T["bg2"], highlightthickness=0)
        self.chat_scroll = ttk.Scrollbar(chat_frame, orient=tk.VERTICAL,
                                          command=self.chat_canvas.yview)
        self.chat_inner = tk.Frame(self.chat_canvas, bg=T["bg2"])
        self.chat_inner.bind("<Configure>",
            lambda e: self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all")))
        self.chat_canvas.create_window((0, 0), window=self.chat_inner, anchor=tk.NW, tags="inner")
        self.chat_canvas.configure(yscrollcommand=self.chat_scroll.set)
        self.chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat_canvas.pack(fill=tk.BOTH, expand=True, padx=(0, 0), pady=0)
        self.chat_canvas.bind("<Configure>", self._on_chat_resize)

        # Mousewheel
        def _on_mousewheel(event):
            self.chat_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.chat_canvas.bind_all("<MouseWheel>", _on_mousewheel, add="+")

        # Welcome
        self._add_chat_welcome()

        # "Thinking..." indicator
        think_bg = tk.Frame(f, bg=T["bg"])
        self.thinking_label = tk.Label(think_bg, text="  \U0001f4ad  Partner is thinking...",
                                       bg=T["bg"], fg=T["txt3"],
                                       font=("Segoe UI", 9, "italic"))
        self.thinking_label.pack()
        # Not packed in main layout; shown on send

        # Input bar
        ib = tk.Frame(f, bg=T["bg"])
        ib.pack(fill=tk.X, padx=0, pady=(6, 0))
        self.chat_input = Input(ib)
        self.chat_input.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        self.chat_input.bind("<Return>", self._send_chat)
        Btn(ib, text="  \u27a1  Send  ", bg=T["accent"], fg="white",
            hover=T["accent_h"],
            command=lambda: self._send_chat(None)).pack(side=tk.LEFT, padx=(10, 0))

    def _on_chat_resize(self, event):
        self.chat_canvas.itemconfig("inner", width=event.width - 4)

    def _add_chat_welcome(self):
        self._add_chat_message("bot",
            "Hi! I'm Partner, your AI research companion.\n\n"
            "You can chat with me here or through QQ Bot.\n"
            "Just type a message below to get started!")

    def _add_chat_message(self, role, text):
        is_user = (role == "user")
        bg = T["chat_user"] if is_user else T["chat_bot"]
        anchor = tk.E if is_user else tk.W
        p_left = 60 if is_user else 12
        p_right = 12 if is_user else 60

        wrapper = tk.Frame(self.chat_inner, bg=T["bg2"])
        wrapper.pack(fill=tk.X, padx=0, pady=3)

        bubble = tk.Frame(wrapper, bg=bg, padx=0, pady=0,
                          highlightbackground=T["glow"], highlightthickness=0)
        bubble.pack(anchor=anchor, padx=(p_left, p_right))

        # Header row
        hdr = tk.Frame(bubble, bg=bg)
        hdr.pack(fill=tk.X, padx=14, pady=(10, 2))
        role_color = T["pink"] if is_user else T["accent2"]
        tk.Label(hdr, text="You" if is_user else "Partner", bg=bg, fg=role_color,
                 font=("Segoe UI", 9, "bold"), anchor=tk.W).pack(side=tk.LEFT)
        # Timestamp
        now = datetime.now().strftime("%H:%M")
        tk.Label(hdr, text=now, bg=bg, fg=T["txt3"],
                 font=("Segoe UI", 8), anchor=tk.E).pack(side=tk.RIGHT)

        # Body text
        tk.Label(bubble, text=text, bg=bg, fg=T["txt"],
                 font=("Segoe UI", 10), wraplength=480, justify=tk.LEFT,
                 anchor=tk.W).pack(anchor=tk.W, padx=14, pady=(2, 10))

        self.chat_canvas.update_idletasks()
        self.chat_canvas.yview_moveto(1.0)

    def _load_chat_history(self):
        if not self.workspace:
            return
        turns = load_dialog_history(self.workspace, n=CHAT_HISTORY_LIMIT)
        for t in turns:
            role = "user" if t.get("role") == "user" else "bot"
            self._add_chat_message(role, t.get("content", ""))

    def _show_thinking(self):
        self.thinking_label.master.pack(fill=tk.X, padx=14, pady=(0, 0))
        self.chat_canvas.yview_moveto(1.0)
        self.chat_canvas.update_idletasks()

    def _hide_thinking(self):
        self.thinking_label.master.pack_forget()

    def _send_chat(self, event=None):
        text = self.chat_input.get().strip()
        if not text:
            return
        self.chat_input.delete(0, tk.END)
        self._add_chat_message("user", text)
        self._show_thinking()

        def do_reply():
            conv_path = os.path.join(APP_DIR, "conversation.py")
            if not os.path.exists(conv_path):
                self.root.after(0, lambda: self._hide_thinking())
                self.root.after(0, lambda: self._add_chat_message("bot",
                    "Sorry, the conversation module isn't available yet.\n\n"
                    "Run the Setup Wizard to configure Partner properly."))
                return

            script = (
                "import sys, json, os\n"
                f"sys.path.insert(0, {PARTNER_DIR!r})\n"
                "from partner.conversation import ConversationEngine\n"
                "from partner.journal import Journal\n"
                "from partner.knowledge import KnowledgeBase\n"
                "from partner.task_queue import TaskQueue\n"
                "from partner.state import StateManager\n"
                f"ws = {self.workspace!r}\n"
                "j = Journal(os.path.join(ws, 'data', 'journal.jsonl')) if ws and os.path.exists(os.path.join(ws, 'data')) else None\n"
                "k = KnowledgeBase(os.path.join(ws, 'data', 'knowledge.jsonl')) if ws and os.path.exists(os.path.join(ws, 'data')) else None\n"
                "tq = TaskQueue(os.path.join(ws, 'state', 'task_queue.json')) if ws else None\n"
                "st = StateManager(os.path.join(ws, 'state')) if ws else None\n"
                "eng = ConversationEngine(j, k, tq, st, ws or '')\n"
                f"reply = eng.respond({text!r})\n"
                "print(reply)\n"
            )
            script_path = os.path.join(PARTNER_DIR, "_chat_script.py")
            try:
                with open(script_path, "w", encoding="utf-8") as sf:
                    sf.write(script)
                out, err, rc = run_silent([sys.executable, script_path], timeout=30)
            except Exception as e:
                out, err, rc = "", str(e), 1
            finally:
                if os.path.exists(script_path):
                    try:
                        os.remove(script_path)
                    except Exception:
                        pass

            self.root.after(0, lambda: self._hide_thinking())
            if rc == 0 and out:
                self.root.after(0, lambda: self._add_chat_message("bot", out))
            else:
                msg = err[:100] if err else "unknown error"
                self.root.after(0, lambda: self._add_chat_message("bot",
                    f"I couldn't process that right now.\n\n({msg})"))

        threading.Thread(target=do_reply, daemon=True).start()

    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    #  QQ Bot Tab
    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    def _build_tab_qq(self):
        f = self._tab_contents[2]
        f.configure(bg=T["bg"])

        # Info banner
        info = tk.Frame(f, bg="#141433", highlightbackground=T["border"],
                        highlightthickness=1)
        info.pack(fill=tk.X, padx=0, pady=(0, 8))
        tk.Label(info, text="  \U0001f514  You can also chat with Partner through QQ Bot!",
                 bg="#141433", fg=T["blue"], font=("Segoe UI", 10, "bold"),
                 anchor=tk.W).pack(fill=tk.X, padx=16, pady=(10, 2))
        tk.Label(info, text="Configure your QQ Official Bot below. Get AppID/Secret from https://q.qq.com/",
                 bg="#141433", fg=T["txt2"], font=("Segoe UI", 9),
                 anchor=tk.W).pack(fill=tk.X, padx=16, pady=(0, 10))

        # Config card
        cc = AccentCard(f, title="Configuration", accent_color=T["accent"])
        cc.pack(fill=tk.X, padx=0, pady=(0, 8))

        # Form
        for label, attr, show in [("AppID", "qq_appid", False),
                                    ("AppSecret", "qq_secret", True),
                                    ("Token", "qq_token", False)]:
            row = tk.Frame(cc.body, bg=T["card"])
            row.pack(fill=tk.X, padx=18, pady=(0, 10))
            tk.Label(row, text=label, bg=T["card"], fg=T["txt2"],
                     font=("Segoe UI", 10), width=10, anchor=tk.W).pack(side=tk.LEFT)
            inp = Input(row, show="*" if show else "")
            inp.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
            setattr(self, attr, inp)

        btn_row = tk.Frame(cc.body, bg=T["card"])
        btn_row.pack(fill=tk.X, padx=18, pady=(4, 16))
        Btn(btn_row, text="  \U0001f4be  Save Config  ", bg=T["accent"], fg="white",
            hover=T["accent_h"], command=self._save_qq_config).pack(side=tk.LEFT, padx=(0, 8))
        Btn(btn_row, text="  \U0001f4c2  Load Config  ", command=self._load_qq_config).pack(side=tk.LEFT)

        # Status card
        st = AccentCard(f, title="Bot Status", accent_color=T["green"])
        st.pack(fill=tk.X, padx=0, pady=(0, 0))

        self.qq_status_label = tk.Label(st.body, text="Not running", bg=T["card"],
                                        fg=T["txt3"], font=("Segoe UI", 10))
        self.qq_status_label.pack(anchor=tk.W, padx=18, pady=(6, 8))

        sbf = tk.Frame(st.body, bg=T["card"])
        sbf.pack(fill=tk.X, padx=18, pady=(0, 14))
        Btn(sbf, text="  \u25b6  Start Bot  ", bg=T["green"], fg="#0c0c1a",
            hover="#6ee7b0", command=self._start_qq_bot).pack(side=tk.LEFT, padx=(0, 8))
        Btn(sbf, text="  \u25a0  Stop Bot  ", bg=T["red"], fg="white",
            hover="#fca5a5", command=self._stop_qq_bot).pack(side=tk.LEFT)

        self.root.after(300, self._load_qq_config)

    def _qq_config_path(self):
        if not self.workspace:
            return None
        return os.path.join(self.workspace, "qq_config.json")

    def _save_qq_config(self):
        path = self._qq_config_path()
        if not path:
            messagebox.showerror("Error", "No workspace configured.")
            return
        cfg = {
            "app_id": self.qq_appid.get().strip(),
            "app_secret": self.qq_secret.get().strip(),
            "token": self.qq_token.get().strip(),
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(cfg, f, indent=2)
        messagebox.showinfo("Saved", "QQ Bot configuration saved.")

    def _load_qq_config(self):
        path = self._qq_config_path()
        if not path or not os.path.exists(path):
            return
        try:
            with open(path) as f:
                cfg = json.load(f)
            self.qq_appid.delete(0, tk.END)
            self.qq_appid.insert(0, cfg.get("app_id", ""))
            self.qq_secret.delete(0, tk.END)
            self.qq_secret.insert(0, cfg.get("app_secret", ""))
            self.qq_token.delete(0, tk.END)
            self.qq_token.insert(0, cfg.get("token", ""))
        except Exception:
            pass

    def _start_qq_bot(self):
        if not self.workspace:
            messagebox.showerror("Error", "No workspace configured.")
            return
        def do_start():
            out, err, rc = run_silent(
                [sys.executable, "-m", "partner.cli", "bot", "start", "qq"],
                timeout=60
            )
            msg = "Bot started" if rc == 0 else f"Failed: {err[:60]}"
            self.root.after(0, lambda: self.qq_status_label.config(
                text=msg, fg=T["green"] if rc == 0 else T["red"]))
        self.qq_status_label.config(text="Starting...", fg=T["yellow"])
        threading.Thread(target=do_start, daemon=True).start()

    def _stop_qq_bot(self):
        if not self.workspace:
            return
        def do_stop():
            out, err, rc = run_silent(
                [sys.executable, "-m", "partner.cli", "bot", "stop", "qq"],
                timeout=10
            )
            self.root.after(0, lambda: self.qq_status_label.config(text="Stopped", fg=T["txt3"]))
        threading.Thread(target=do_stop, daemon=True).start()

    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    #  Logs Tab
    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    def _build_tab_logs(self):
        f = self._tab_contents[3]
        f.configure(bg=T["bg"])

        log_card = AccentCard(f, title="Log Viewer", accent_color=T["yellow"])
        log_card.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 8))

        self.log_text = scrolledtext.ScrolledText(log_card.body,
            font=("Cascadia Code", 9), bg=T["bg2"], fg=T["txt"],
            relief=tk.FLAT, padx=14, pady=12, borderwidth=0,
            highlightbackground=T["border"], highlightthickness=1,
            insertbackground=T["txt"],
            selectbackground=T["accent"], selectforeground="white")
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=18, pady=(4, 10))

        bf = tk.Frame(f, bg=T["bg"])
        bf.pack(fill=tk.X, padx=0, pady=(0, 0))
        Btn(bf, text="  \U0001f504  Reload Logs  ", command=self._reload_logs).pack(side=tk.LEFT)

    def _reload_logs(self):
        self.log_text.delete(1.0, tk.END)
        if not self.workspace:
            self.log_text.insert(tk.END, "No workspace configured.")
            return
        log_dir = os.path.join(self.workspace, "logs")
        if not os.path.exists(log_dir):
            self.log_text.insert(tk.END, "No logs directory found.")
            return
        files = sorted([f for f in os.listdir(log_dir)
                       if os.path.isfile(os.path.join(log_dir, f))])
        for fname in files[-LOG_FILE_LIMIT:]:
            fp = os.path.join(log_dir, fname)
            try:
                with open(fp, errors="ignore") as fh:
                    content = fh.read()[-LOG_BYTE_LIMIT:]
                self.log_text.insert(tk.END,
                    f"{'='*60}\n  {fname}\n{'='*60}\n{content}\n\n")
            except Exception:
                self.log_text.insert(tk.END,
                    f"{'='*60}\n  {fname}  (unreadable)\n{'='*60}\n\n")
        self.log_text.see(tk.END)

    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    #  Shared Helpers
    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    def _set_dot(self, color):
        self.dot_canvas.itemconfig(self.dot_id_outer, fill=color, outline=color)
        self.dot_canvas.itemconfig(self.dot_id_inner, fill="white", outline="")

    def _set_status(self, text, color=None):
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete(1.0, tk.END)
        # Add colored tags for status indicators
        self.status_text.tag_configure("ok", foreground=T["green"])
        self.status_text.tag_configure("fail", foreground=T["red"])
        self.status_text.tag_configure("highlight", foreground=T["accent2"])
        self.status_text.tag_configure("dim", foreground=T["txt3"])
        # Insert with color markers
        lines = text.split("\n")
        for i, line in enumerate(lines):
            is_last = (i == len(lines) - 1)
            if "[OK]" in line:
                before, rest = line.split("[OK]", 1)
                self.status_text.insert(tk.END, before)
                self.status_text.insert(tk.END, "[OK]", "ok")
                self.status_text.insert(tk.END, rest)
            elif "[--]" in line:
                before, rest = line.split("[--]", 1)
                self.status_text.insert(tk.END, before)
                self.status_text.insert(tk.END, "[--]", "fail")
                self.status_text.insert(tk.END, rest)
            elif "Workspace:" in line:
                self.status_text.insert(tk.END, "Workspace: ", "dim")
                ws_path = line.split("Workspace:", 1)[1].strip()
                self.status_text.insert(tk.END, ws_path, "highlight")
            elif "Active Plan:" in line or "Status:" in line or "Goal:" in line:
                label, val = line.split(":", 1)
                self.status_text.insert(tk.END, f"{label}:", "dim")
                self.status_text.insert(tk.END, f"{val}")
            else:
                self.status_text.insert(tk.END, line)
            if not is_last:
                self.status_text.insert(tk.END, "\n")
        self.status_text.config(state=tk.DISABLED)
        color = color or T["txt3"]
        self._set_dot(color)
        self.hdr_status.config(text="", fg=color)

    def _set_status_bar(self, text):
        self.status_bar.config(text=text)

    def _refresh_status(self):
        self._set_status("Loading...", T["yellow"])
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _do_refresh(self):
        if not self.workspace:
            self.root.after(0, lambda: self._set_status(
                "Workspace not configured.\n\nClick 'Setup Wizard' to get started.",
                T["yellow"]))
            return

        lines = [f"Workspace: {self.workspace}", ""]
        for sub in ["state", "logs", "data"]:
            p = os.path.join(self.workspace, sub)
            lines.append(f"[{'OK' if os.path.exists(p) else '--'}] {sub}/")

        plan_path = os.path.join(self.workspace, "state", "active_plan.json")
        if os.path.exists(plan_path):
            try:
                with open(plan_path) as fh:
                    plan = json.load(fh)
                lines.append("")
                lines.append(f"Active Plan:  {plan.get('title', '-')}")
                lines.append(f"Status:       {plan.get('status', '-')}")
                lines.append(f"Goal:         {plan.get('goal', '-')[:80]}")
                phases = plan.get("phases", [])
                cur = plan.get("current_phase_index", 0)
                for i, ph in enumerate(phases):
                    marker = ">" if i == cur else "+" if ph.get("status") == "completed" else "-"
                    lines.append(f"  [{marker}] {ph.get('name','?')} - {ph.get('status','?')}")
            except Exception:
                lines.append("")
                lines.append("Plan file (corrupted)")
        else:
            lines.append("")
            lines.append("No active plan")

        pid_path = os.path.join(self.workspace, "state", "qq_bot.pid")
        if os.path.exists(pid_path):
            try:
                with open(pid_path) as fh:
                    pid = fh.read().strip()
                lines.append("")
                lines.append(f"QQ Bot: Running (PID {pid})")
            except Exception:
                pass

        ok = os.path.exists(plan_path)
        color = T["green"] if ok else T["yellow"]
        now = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, lambda: self._set_status("\n".join(lines), color))
        self.root.after(0, lambda: self._set_status_bar(
            f"Last updated: {now}  |  Workspace: {self.workspace}"))

    def _start_auto_refresh(self):
        if self._auto_refresh_id:
            self.root.after_cancel(self._auto_refresh_id)
        self._auto_refresh_id = self.root.after(AUTO_REFRESH_INTERVAL, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        self._do_refresh()
        self._auto_refresh_id = self.root.after(AUTO_REFRESH_INTERVAL, self._auto_refresh_tick)

    def _open_workspace(self):
        path = self.workspace or PARTNER_DIR
        if os.path.exists(path):
            os.startfile(path)

    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    #  Setup Wizard
    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    def _show_setup(self):
        win = tk.Toplevel(self.root)
        win.title("Partner Setup")
        win.configure(bg=T["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        center_window(win, 600, 580)

        tk.Label(win, text="Partner Setup", bg=T["bg"], fg=T["accent2"],
                 font=("Segoe UI", 22, "bold")).pack(pady=(28, 2))
        tk.Label(win, text="Configure your AI Research Companion", bg=T["bg"],
                 fg=T["txt3"], font=("Segoe UI", 10)).pack(pady=(0, 24))

        # Step 1: Workspace
        s1 = AccentCard(win, title="1. Choose Workspace Folder", accent_color=T["green"])
        s1.pack(fill=tk.X, padx=28, pady=(0, 14))
        ws_var = tk.StringVar(value=self.workspace or os.path.expanduser("~/partner_workspace"))
        ws_row = tk.Frame(s1.body, bg=T["card"])
        ws_row.pack(fill=tk.X, padx=18, pady=(4, 16))
        ws_entry = Input(ws_row, textvariable=ws_var)
        ws_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        Btn(ws_row, text="  Browse  ", command=lambda: ws_var.set(
            filedialog.askdirectory(title="Select Workspace") or ws_var.get())
            ).pack(side=tk.LEFT, padx=(10, 0))

        # Step 2: Backend
        s2 = AccentCard(win, title="2. AI Backend", accent_color=T["blue"])
        s2.pack(fill=tk.X, padx=28, pady=(0, 14))
        backend_var = tk.StringVar(value="hermes")
        for val, label in [("hermes", "  \U0001f916  Hermes Agent (recommended)"),
                          ("openclaw", "  \U0001f9e0  OpenClaw"),
                          ("skip", "  \u23f3  Skip for now")]:
            tk.Radiobutton(s2.body, text=label, variable=backend_var, value=val,
                          bg=T["card"], fg=T["txt"], selectcolor=T["input_bg"],
                          activebackground=T["card"], activeforeground=T["accent2"],
                          font=("Segoe UI", 10)).pack(anchor=tk.W, padx=24, pady=3)

        # Step 3: QQ Bot
        s3 = AccentCard(win, title="3. QQ Bot (optional)", accent_color=T["pink"])
        s3.pack(fill=tk.X, padx=28, pady=(0, 14))
        tk.Label(s3.body, text="Add your QQ Bot credentials now or later.",
                 bg=T["card"], fg=T["txt3"], font=("Segoe UI", 9)).pack(anchor=tk.W, padx=18)
        qf = tk.Frame(s3.body, bg=T["card"])
        qf.pack(fill=tk.X, padx=18, pady=(8, 8))
        tk.Label(qf, text="AppID:", bg=T["card"], fg=T["txt2"], width=8, anchor=tk.W).pack(side=tk.LEFT)
        setup_appid = Input(qf)
        setup_appid.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        qf2 = tk.Frame(s3.body, bg=T["card"])
        qf2.pack(fill=tk.X, padx=18, pady=(0, 14))
        tk.Label(qf2, text="Secret:", bg=T["card"], fg=T["txt2"], width=8, anchor=tk.W).pack(side=tk.LEFT)
        setup_secret = Input(qf2)
        setup_secret.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        # Status
        status_var = tk.StringVar(value="Ready to configure")
        tk.Label(win, textvariable=status_var, bg=T["bg"], fg=T["txt3"],
                 font=("Segoe UI", 10)).pack(pady=(4, 0))

        def do_setup():
            ws = ws_var.get().strip()
            if not ws:
                messagebox.showerror("Error", "Please select a workspace folder.")
                return
            os.makedirs(ws, exist_ok=True)
            status_var.set("Creating workspace...")
            win.update()

            for sub in ["state", "logs", "data"]:
                os.makedirs(os.path.join(ws, sub), exist_ok=True)

            config = {"workspace": ws, "backend": backend_var.get(),
                      "created": datetime.now().isoformat()}
            with open(os.path.join(ws, "config.json"), "w") as fh:
                json.dump(config, fh, indent=2)

            appid = setup_appid.get().strip()
            secret = setup_secret.get().strip()
            if appid and secret:
                qq_cfg = {"app_id": appid, "app_secret": secret, "token": ""}
                with open(os.path.join(ws, "qq_config.json"), "w") as fh:
                    json.dump(qq_cfg, fh, indent=2)

            if backend_var.get() == "hermes":
                status_var.set("Installing Hermes Agent...")
                win.update()
                run_silent([sys.executable, "-m", "pip", "install", "hermes-agent"], timeout=120)

            status_var.set("Setup complete!")
            self.workspace = ws
            win.after(600, win.destroy)
            self._refresh_status()
            self._load_chat_history()
            self._start_auto_refresh()

        Btn(win, text="  \U0001f680  Start Setup  ", bg=T["accent"], fg="white",
            hover=T["accent_h"], command=do_setup).pack(pady=22)

    def run(self):
        self.root.mainloop()


def main():
    root = tk.Tk()
    app = PartnerApp(root)
    app.run()


if __name__ == "__main__":
    main()
