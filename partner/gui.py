#!/usr/bin/env python3
"""Partner — Modern Desktop Application (v6)

Features: Dashboard, Chat, QQ Bot, Exploration Records
All backend commands run silently without terminal windows.
Language: Chinese / English toggle.
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

# ── i18n ──
LANGUAGES = {"zh": "中文", "en": "English"}

L = {
    "zh": {
        "app_title": "Partner",
        "tab_dashboard": "仪表盘",
        "tab_chat": "对话",
        "tab_qq": "QQ 机器人",
        "tab_logs": "探索记录",
        "status_loading": "加载中…",
        "status_no_workspace": "尚未配置工作区\n\n点击「设置向导」开始",
        "status_workspace": "工作区",
        "status_ready": "就绪",
        "btn_refresh": "  ⟳  刷新  ",
        "btn_open_ws": "  📂  打开工作区  ",
        "btn_setup": "  ⚙  设置向导  ",
        "btn_send": "  ➡  发送  ",
        "thinking": "  💬  Partner 思考中…",
        "chat_welcome": "嗨！我是 Partner，你的 AI 研究伙伴。\n\n你可以在这里和我聊天，或者通过 QQ 机器人联系我。\n在下方输入消息开始吧！",
        "chat_you": "你",
        "chat_partner": "Partner",
        "chat_unavailable": "对话模块暂不可用。\n\n请先运行设置向导配置 Partner。",
        "chat_error": "暂时无法处理这条消息。\n\n({msg})",
        "qq_banner": "  💬  通过 QQ 与 Partner 对话！",
        "qq_config_title": "连接配置",
        "qq_appid": "AppID",
        "qq_secret": "AppSecret",
        "qq_save": "  💾  保存  ",
        "qq_load": "  📂  载入  ",
        "qq_status_title": "机器人状态",
        "qq_not_running": "未运行",
        "qq_start": "  ▶  启动  ",
        "qq_stop": "  ■  停止  ",
        "qq_starting": "启动中…",
        "qq_started": "机器人已启动",
        "qq_failed": "启动失败: {msg}",
        "qq_stopped": "已停止",
        "qq_saved": "QQ 配置已保存",
        "qq_no_ws": "未配置工作区",
        "logs_title": "探索记录",
        "btn_reload": "  ⟳  刷新  ",
        "last_update": "最后更新: {time}",
    },
    "en": {
        "app_title": "Partner",
        "tab_dashboard": "Dashboard",
        "tab_chat": "Chat",
        "tab_qq": "QQ Bot",
        "tab_logs": "Records",
        "status_loading": "Loading…",
        "status_no_workspace": "Workspace not configured.\n\nClick 'Setup Wizard' to get started.",
        "status_workspace": "Workspace",
        "status_ready": "Ready",
        "btn_refresh": "  ⟳  Refresh  ",
        "btn_open_ws": "  📂  Open Workspace  ",
        "btn_setup": "  ⚙  Setup Wizard  ",
        "btn_send": "  ➡  Send  ",
        "thinking": "  💬  Partner is thinking…",
        "chat_welcome": "Hi! I'm Partner, your AI research companion.\n\nChat with me here or through QQ Bot.\nType a message below to get started!",
        "chat_you": "You",
        "chat_partner": "Partner",
        "chat_unavailable": "Conversation module unavailable.\n\nRun Setup Wizard to configure Partner.",
        "chat_error": "Couldn't process that.\n\n({msg})",
        "qq_banner": "  💬  Chat with Partner via QQ!",
        "qq_config_title": "Connection",
        "qq_appid": "AppID",
        "qq_secret": "AppSecret",
        "qq_save": "  💾  Save  ",
        "qq_load": "  📂  Load  ",
        "qq_status_title": "Bot Status",
        "qq_not_running": "Not running",
        "qq_start": "  ▶  Start  ",
        "qq_stop": "  ■  Stop  ",
        "qq_starting": "Starting…",
        "qq_started": "Bot started",
        "qq_failed": "Failed: {msg}",
        "qq_stopped": "Stopped",
        "qq_saved": "QQ config saved",
        "qq_no_ws": "No workspace configured",
        "logs_title": "Records",
        "btn_reload": "  ⟳  Refresh  ",
        "last_update": "Last updated: {time}",
    },
}


def tr(key, lang="zh", **kw):
    val = L.get(lang, L["zh"]).get(key, key)
    if kw:
        return val.format(**kw)
    return val


# ── Modern Theme ──
T = {
    "bg":        "#0d1117",
    "bg2":       "#161b22",
    "bg3":       "#21262d",
    "card":      "#161b22",
    "card_hl":   "#30363d",
    "accent":    "#58a6ff",
    "accent2":   "#79c0ff",
    "accent3":   "#1f6feb",
    "accent_h":  "#79c0ff",
    "green":     "#3fb950",
    "yellow":    "#d29922",
    "red":       "#f85149",
    "blue":      "#58a6ff",
    "pink":      "#f778ba",
    "txt":       "#c9d1d9",
    "txt2":      "#8b949e",
    "txt3":      "#484f58",
    "border":    "#21262d",
    "input_bg":  "#0d1117",
    "chat_user": "#1f6feb",
    "chat_bot":  "#21262d",
    "glow":      "#0d1117",
}

FONT = ("Segoe UI Variable Text", "Microsoft YaHei UI", "Segoe UI", "TkDefaultFont")
FONT_MONO = ("Cascadia Mono", "Cascadia Code", "Consolas", "monospace")
FONT_HEADING = ("Segoe UI Variable Display", "Segoe UI", "TkDefaultFont")


# ── Dual Installation Check ──────────────────────────────

def check_conflicting_installation():
    try:
        import partner as _p
        pip_path = os.path.dirname(os.path.abspath(_p.__file__))
        local_partner = os.path.join(PARTNER_DIR, "partner")
        local_path = os.path.normpath(local_partner)
        pip_norm = os.path.normpath(pip_path)
        if pip_norm != local_path and pip_norm != os.path.normpath(APP_DIR):
            return (pip_norm, local_path)
    except Exception:
        pass
    return None


def find_workspace():
    from .setup import find_workspace as _fw
    return _fw()


def run_silent(cmd, cwd=None, timeout=30, timeout_ok=False):
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
        if timeout_ok:
            return "", "", 0
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


# ════════════════════════════════════════════════════════════════
#  UI Components
# ════════════════════════════════════════════════════════════════

class AccentCard(tk.Frame):
    def __init__(self, parent, title=None, accent_color=None, **kw):
        super().__init__(parent, bg=T["card"], highlightthickness=0, **kw)
        line = tk.Frame(self, bg=accent_color or T["accent"], height=2)
        line.pack(fill=tk.X)
        line.pack_propagate(False)
        body = tk.Frame(self, bg=T["card"])
        body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        if title:
            h = tk.Frame(body, bg=T["card"])
            h.pack(fill=tk.X, padx=18, pady=(14, 2))
            tk.Label(h, text=title, bg=T["card"], fg=accent_color or T["accent2"],
                     font=(FONT_HEADING[0], 11, "bold"), anchor=tk.W).pack(side=tk.LEFT)
        self.body = body


class Input(tk.Entry):
    def __init__(self, parent, **kw):
        kw.setdefault("bg", T["input_bg"])
        kw.setdefault("fg", T["txt"])
        kw.setdefault("insertbackground", T["accent"])
        kw.setdefault("font", (FONT_MONO[0], 10))
        kw.setdefault("relief", tk.FLAT)
        kw.setdefault("highlightbackground", T["border"])
        kw.setdefault("highlightthickness", 1)
        kw.setdefault("highlightcolor", T["accent"])
        super().__init__(parent, **kw)
        self.bind("<FocusIn>", lambda e: self.configure(highlightbackground=T["accent"]))
        self.bind("<FocusOut>", lambda e: self.configure(highlightbackground=T["border"]))


class Btn(tk.Frame):
    def __init__(self, parent, text="", command=None,
                 bg=None, fg=None, hover_bg=None, hover_fg=None, **kw):
        self._cmd = command
        bg = bg or T["card_hl"]
        fg = fg or T["txt"]
        hover_bg = hover_bg or T["accent3"]
        hover_fg = hover_fg or "#ffffff"
        super().__init__(parent, bg=bg, **kw)
        self._label = tk.Label(self, text=text, bg=bg, fg=fg,
                               font=(FONT[0], 10), cursor="hand2", padx=16, pady=7)
        self._label.pack()
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg
        self._hover_fg = hover_fg
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
        self._lang = "zh"
        self.root.title(tr("app_title", self._lang))
        self.root.minsize(920, 680)
        self.root.configure(bg=T["bg"])
        center_window(self.root, 1100, 760)

        # Dual install check
        conflict = check_conflicting_installation()
        if conflict:
            pip_p, local_p = conflict
            messagebox.showwarning(
                "检测到多个 Partner 安装",
                f"你同时有多个 Partner 安装，可能导致版本冲突：\n\n"
                f"  Pip 安装: {pip_p}\n"
                f"  本地安装: {local_p}\n\n"
                f"建议卸载其中一个：pip uninstall partner\n\n"
                f"当前使用的是本地安装版本。"
            )

        self.workspace = find_workspace()
        self._auto_refresh_id = None
        self._build_ui()

        if not self.workspace:
            self._set_dot(T["yellow"])
            self.hdr_status.config(text="未配置工作区")
            self.root.after(500, self._show_setup)
        else:
            self._load_chat_history()
            self._start_auto_refresh()

    def _tr(self, key, **kw):
        return tr(key, self._lang, **kw)

    def _toggle_lang(self):
        self._lang = "en" if self._lang == "zh" else "zh"
        self._rebuild_ui()

    def _rebuild_ui(self):
        self.root.title(self._tr("app_title"))
        self._active_tab = 0
        for child in self._tab_frame.winfo_children():
            child.destroy()
        for i, content in enumerate(self._tab_contents):
            content.destroy()
        self._tab_contents = []
        self._tab_buttons = []
        self._build_ui_parts()

    # ──────────── UI Layout ────────────
    def _build_ui(self):
        main = tk.Frame(self.root, bg=T["bg"])
        main.pack(fill=tk.BOTH, expand=True, padx=24, pady=(16, 20))
        self._main = main
        self._build_ui_parts()

    def _build_ui_parts(self):
        main = self._main
        for w in main.winfo_children():
            w.destroy()

        # ── Header ──
        hdr = tk.Frame(main, bg=T["bg"])
        hdr.pack(fill=tk.X, pady=(0, 12))

        lf = tk.Frame(hdr, bg=T["bg"])
        lf.pack(side=tk.LEFT)
        badge = tk.Canvas(lf, width=32, height=32, bg=T["accent3"],
                          highlightthickness=0)
        badge.pack(side=tk.LEFT, padx=(0, 10))
        badge.create_oval(3, 3, 29, 29, fill=T["accent"], outline="")
        badge.create_text(16, 16, text="P", fill="white",
                          font=(FONT_HEADING[0], 15, "bold"))

        tk.Label(lf, text="Partner", bg=T["bg"], fg=T["txt"],
                 font=(FONT_HEADING[0], 22, "bold")).pack(side=tk.LEFT)

        rf = tk.Frame(hdr, bg=T["bg"])
        rf.pack(side=tk.RIGHT)

        lang_text = tr("lang_toggle", self._lang)
        self.lang_btn = tk.Label(rf, text=lang_text, bg=T["bg3"], fg=T["txt2"],
                                 font=(FONT[0], 10), cursor="hand2", padx=10, pady=3)
        self.lang_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self.lang_btn.bind("<Button-1>", lambda e: self._toggle_lang())
        self.lang_btn.bind("<Enter>", lambda e: self.lang_btn.configure(bg=T["card_hl"]))
        self.lang_btn.bind("<Leave>", lambda e: self.lang_btn.configure(bg=T["bg3"]))

        self.dot_canvas = tk.Canvas(rf, width=12, height=12, bg=T["bg"],
                                    highlightthickness=0)
        self.dot_canvas.pack(side=tk.RIGHT, padx=(0, 6))
        self.dot_id = self.dot_canvas.create_oval(1, 1, 11, 11,
            fill=T["txt3"], outline="", width=0)

        self.hdr_status = tk.Label(rf, text="", bg=T["bg"], fg=T["txt3"],
                                   font=(FONT[0], 10))
        self.hdr_status.pack(side=tk.RIGHT)

        # ── Tab Bar ──
        self._tab_frame = tk.Frame(main, bg=T["bg"])
        self._tab_frame.pack(fill=tk.X, pady=(0, 0))

        tab_bar = tk.Frame(self._tab_frame, bg=T["bg2"], height=40)
        tab_bar.pack(fill=tk.X)
        tab_bar.pack_propagate(False)

        self._tabs = [
            (self._tr("tab_dashboard"), "📊"),
            (self._tr("tab_chat"),      "💬"),
            (self._tr("tab_qq"),        "🤖"),
            (self._tr("tab_logs"),      "📋"),
        ]
        self._tab_buttons = []
        self._tab_contents = []

        for idx, (name, icon) in enumerate(self._tabs):
            is_active = (idx == 0)
            tab_bg = T["card"] if is_active else T["bg2"]
            tab_fg = T["txt"] if is_active else T["txt3"]
            btn = tk.Frame(tab_bar, bg=tab_bg, cursor="hand2")
            btn.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 0))
            label = tk.Label(btn, text=f"  {icon}  {name}  ",
                             bg=tab_bg, fg=tab_fg,
                             font=(FONT[0], 10), padx=12)
            label.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

            def make_handler(i):
                return lambda e, idx=i: self._switch_tab(idx)
            btn.bind("<Button-1>", make_handler(idx))
            label.bind("<Button-1>", make_handler(idx))

            def make_hover(i):
                def on_enter(e):
                    if i != self._active_tab:
                        for c in e.widget.winfo_children():
                            if isinstance(c, tk.Label):
                                c.configure(bg=T["card"])
                        e.widget.configure(bg=T["card"])
                def on_leave(e):
                    if i != self._active_tab:
                        for c in e.widget.winfo_children():
                            if isinstance(c, tk.Label):
                                c.configure(bg=T["bg2"])
                        e.widget.configure(bg=T["bg2"])
                return on_enter, on_leave
            enter_fn, leave_fn = make_hover(idx)
            btn.bind("<Enter>", enter_fn)
            btn.bind("<Leave>", leave_fn)
            label.bind("<Enter>", enter_fn)
            label.bind("<Leave>", leave_fn)

            self._tab_buttons.append(btn)

            content = tk.Frame(main, bg=T["bg"])
            if idx != 0:
                content.pack_forget()
            else:
                content.pack(fill=tk.BOTH, expand=True)
            self._tab_contents.append(content)

        self._active_tab = 0

        self._build_tab_dashboard()
        self._build_tab_chat()
        self._build_tab_qq()
        self._build_tab_logs()

        # Status bar
        bar_frame = tk.Frame(main, bg=T["bg2"])
        bar_frame.pack(fill=tk.X, pady=(10, 0))
        self.status_bar = tk.Label(bar_frame, text=self._tr("status_ready"),
                                   bg=T["bg2"], fg=T["txt3"],
                                   font=(FONT[0], 10), anchor=tk.W, padx=14, pady=5)
        self.status_bar.pack(fill=tk.X)

    def _switch_tab(self, idx):
        if idx == self._active_tab:
            return
        self._tab_contents[self._active_tab].pack_forget()
        self._tab_contents[idx].pack(fill=tk.BOTH, expand=True)
        for i, btn in enumerate(self._tab_buttons):
            bg = T["card"] if i == idx else T["bg2"]
            fg = T["txt"] if i == idx else T["txt3"]
            for child in btn.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=bg, fg=fg)
            btn.configure(bg=bg)
        self._active_tab = idx
        if idx == 0:
            self._refresh_dashboard()

    # ════════════════════════════════════════════════════════════════
    #  Dashboard Tab — Card-based status overview
    # ════════════════════════════════════════════════════════════════

    def _build_tab_dashboard(self):
        f = self._tab_contents[0]
        f.configure(bg=T["bg"])

        # Scrollable card container — fills most of the space
        canvas = tk.Canvas(f, bg=T["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(f, orient=tk.VERTICAL, command=canvas.yview)
        self._dash_inner = tk.Frame(canvas, bg=T["bg"])
        self._dash_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._dash_inner, anchor=tk.NW, tags="inner")
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(fill=tk.BOTH, expand=True)

        # Button bar at the bottom
        bf = tk.Frame(f, bg=T["bg"])
        bf.pack(fill=tk.X, padx=0, pady=(8, 0))
        Btn(bf, text=self._tr("btn_refresh"),
            command=self._refresh_dashboard).pack(side=tk.LEFT, padx=(0, 8))
        Btn(bf, text=self._tr("btn_open_ws"),
            command=self._open_workspace).pack(side=tk.LEFT, padx=(0, 8))
        Btn(bf, text=self._tr("btn_setup"), bg=T["accent3"], fg="white",
            hover_bg=T["accent"], command=self._show_setup).pack(side=tk.RIGHT)

        self._refresh_dashboard()

    def _status_card(self, parent, icon, title, accent, rows, dot=False, dot_green=False):
        """Create a status card with icon, title, and key-value rows."""
        card = tk.Frame(parent, bg=T["card"], highlightbackground=T["border"], highlightthickness=1)
        hdr = tk.Frame(card, bg=T["card"])
        hdr.pack(fill=tk.X, padx=14, pady=(10, 4))
        tk.Label(hdr, text=f"{icon}  {title}", bg=T["card"], fg=accent,
                 font=(FONT_HEADING[0], 12, "bold"), anchor=tk.W).pack(side=tk.LEFT)
        if dot:
            d = tk.Canvas(hdr, width=10, height=10, bg=T["card"], highlightthickness=0)
            d.pack(side=tk.RIGHT)
            d.create_oval(1, 1, 9, 9, fill=T["green"] if dot_green else T["txt3"],
                          outline="", width=0)
        for label, value, vcolor in rows:
            row = tk.Frame(card, bg=T["card"])
            row.pack(fill=tk.X, padx=14, pady=1)
            tk.Label(row, text=label, bg=T["card"], fg=T["txt2"],
                     font=(FONT[0], 10), width=7, anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=str(value), bg=T["card"], fg=vcolor or T["txt"],
                     font=(FONT[0], 10), anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)
        return card

    def _refresh_dashboard(self):
        self._set_dot(T["yellow"])
        self.hdr_status.config(text="刷新中…")
        threading.Thread(target=self._do_refresh_dash, daemon=True).start()

    def _do_refresh_dash(self):
        ws = self.workspace
        state_dir = os.path.join(ws, "state") if ws else None

        # Read state files
        plan, hb, stats, qqcfg = {}, {}, {}, {}
        if state_dir:
            for fname, target in [("active_plan.json", plan), ("heartbeat.json", hb),
                                   ("stats.json", stats)]:
                fp = os.path.join(state_dir, fname)
                if os.path.exists(fp):
                    try:
                        with open(fp) as fh:
                            target.update(json.load(fh))
                    except: pass
        qqp = os.path.join(ws or "", "qq_config.json")
        if os.path.exists(qqp):
            try:
                with open(qqp) as fh:
                    qqcfg.update(json.load(fh))
            except: pass

        smap = {"idle": "空闲", "active": "运行中", "planning": "规划中",
                "completed": "已完成", "working": "工作中", "alive": "在线"}
        qq_alive = hb.get("qq_bot_alive", False)
        hb_st = hb.get("status", "unknown")

        def _update():
            for w in self._dash_inner.winfo_children():
                w.destroy()

            # ── Top row: QQ + Partner status ──
            tr = tk.Frame(self._dash_inner, bg=T["bg"])
            tr.pack(fill=tk.X, padx=0, pady=(0, 8))

            appid = qqcfg.get("app_id", "未配置")
            masked = appid[:4] + "****" if len(appid) > 4 else appid
            self._status_card(tr, "🤖", "QQ 机器人", T["blue"], [
                ("状态", "运行中" if qq_alive else "已停止", T["green"] if qq_alive else T["red"]),
                ("AppID", masked, T["txt2"]),
                ("沙箱", "是" if qqcfg.get("is_sandbox") else "否", T["txt2"]),
            ], dot=True, dot_green=qq_alive).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

            pst = smap.get(hb_st, hb_st)
            pcol = T["green"] if hb_st in ("alive", "working") else T["yellow"]
            self._status_card(tr, "🧠", "Partner 状态", T["pink"], [
                ("状态", pst, pcol),
                ("心跳", hb.get("last_heartbeat", "")[11:19] or "-", T["txt2"]),
                ("周期", str(stats.get("total_cycles", 0)), T["txt2"]),
                ("任务", str(stats.get("total_tasks_completed", 0)), T["txt2"]),
            ], dot=True, dot_green=hb_st in ("alive", "working")).pack(
                side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))

            # ── Plan card ──
            pst2 = smap.get(plan.get("status", "idle"), plan.get("status", "idle"))
            pcol2 = T["green"] if plan.get("status") == "active" else T["yellow"]
            pc = tk.Frame(self._dash_inner, bg=T["card"],
                          highlightbackground=T["border"], highlightthickness=1)
            pc.pack(fill=tk.X, padx=0, pady=(0, 8))
            tk.Label(pc, text="📋  当前计划", bg=T["card"], fg=T["green"],
                     font=(FONT_HEADING[0], 12, "bold"), anchor=tk.W).pack(
                         anchor=tk.W, padx=14, pady=(10, 4))
            pf = tk.Frame(pc, bg=T["card"])
            pf.pack(fill=tk.X, padx=14, pady=(0, 4))
            tk.Label(pf, text="状态:", bg=T["card"], fg=T["txt2"],
                     font=(FONT[0], 10), anchor=tk.W).pack(side=tk.LEFT, padx=(0, 4))
            tk.Label(pf, text=pst2, bg=T["card"], fg=pcol2,
                     font=(FONT[0], 10), anchor=tk.W).pack(side=tk.LEFT, padx=(0, 20))
            tk.Label(pf, text="创建:", bg=T["card"], fg=T["txt2"],
                     font=(FONT[0], 10), anchor=tk.W).pack(side=tk.LEFT, padx=(0, 4))
            tk.Label(pf, text=(plan.get("created_at") or "")[:10] or "-",
                     bg=T["card"], fg=T["txt"], font=(FONT[0], 10)).pack(side=tk.LEFT)

            summary = plan.get("heartbeat_summary") or plan.get("goal") or ""
            if summary:
                sr = tk.Frame(pc, bg=T["card"])
                sr.pack(fill=tk.X, padx=14, pady=(4, 10))
                tk.Label(sr, text="摘要:", bg=T["card"], fg=T["txt2"],
                         font=(FONT[0], 10), anchor=tk.W).pack(side=tk.LEFT, anchor=tk.N)
                tk.Label(sr, text=summary[:300], bg=T["card"], fg=T["txt"],
                         font=(FONT[0], 10), anchor=tk.W, wraplength=550).pack(
                             side=tk.LEFT, fill=tk.X, expand=True)

            # ── Workspace card ──
            wc = tk.Frame(self._dash_inner, bg=T["card"],
                          highlightbackground=T["border"], highlightthickness=1)
            wc.pack(fill=tk.X, padx=0, pady=(0, 8))
            tk.Label(wc, text="📁  工作区", bg=T["card"], fg=T["yellow"],
                     font=(FONT_HEADING[0], 12, "bold"), anchor=tk.W).pack(
                         anchor=tk.W, padx=14, pady=(10, 4))
            wf = tk.Frame(wc, bg=T["card"])
            wf.pack(fill=tk.X, padx=14, pady=(0, 4))
            tk.Label(wf, text="路径:", bg=T["card"], fg=T["txt2"],
                     font=(FONT[0], 10), anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(wf, text=(ws or "-").replace("/", "\\"), bg=T["card"],
                     fg=T["accent2"], font=(FONT_MONO[0], 9), anchor=tk.W).pack(
                         side=tk.LEFT, padx=(6, 0))

            wf2 = tk.Frame(wc, bg=T["card"])
            wf2.pack(fill=tk.X, padx=14, pady=(4, 10))
            parts = []
            for sub in ["projects", "knowledge", "ideas", "dialogue"]:
                p = os.path.join(ws, sub) if ws else None
                if p and os.path.exists(p):
                    cnt = len(os.listdir(p))
                    parts.append(f"{sub}: {cnt}")
            tk.Label(wf2, text="  |  ".join(parts) or "(空)", bg=T["card"], fg=T["txt"],
                     font=(FONT[0], 10), anchor=tk.W).pack(side=tk.LEFT)

            self._set_dot(T["green"] if qq_alive else T["yellow"])
            now = datetime.now().strftime("%H:%M:%S")
            self.hdr_status.config(text=f"刷新于 {now}")
            self.status_bar.config(text=f"最后刷新: {now}")

        self.root.after(0, _update)

    # ════════════════════════════════════════════════════════════════
    #  Chat Tab
    # ════════════════════════════════════════════════════════════════

    def _build_tab_chat(self):
        f = self._tab_contents[1]
        f.configure(bg=T["bg"])

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

        def _on_mousewheel(event):
            self.chat_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.chat_canvas.bind_all("<MouseWheel>", _on_mousewheel, add="+")

        self._add_chat_welcome()

        think_bg = tk.Frame(f, bg=T["bg"])
        self.thinking_label = tk.Label(think_bg, text=self._tr("thinking"),
                                       bg=T["bg"], fg=T["txt3"],
                                       font=(FONT[0], 10, "italic"))
        self.thinking_label.pack()

        ib = tk.Frame(f, bg=T["bg"])
        ib.pack(fill=tk.X, padx=0, pady=(6, 0))
        self.chat_input = Input(ib)
        self.chat_input.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        self.chat_input.bind("<Return>", self._send_chat)
        Btn(ib, text=self._tr("btn_send"), bg=T["accent3"], fg="white",
            hover_bg=T["accent"],
            command=lambda: self._send_chat(None)).pack(side=tk.LEFT, padx=(10, 0))

    def _on_chat_resize(self, event):
        self.chat_canvas.itemconfig("inner", width=event.width - 4)

    def _add_chat_welcome(self):
        self._add_chat_message("bot", self._tr("chat_welcome"))

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

        hdr = tk.Frame(bubble, bg=bg)
        hdr.pack(fill=tk.X, padx=14, pady=(10, 2))
        role_color = T["pink"] if is_user else T["accent2"]
        tk.Label(hdr, text=self._tr("chat_you" if is_user else "chat_partner"),
                 bg=bg, fg=role_color,
                 font=(FONT[0], 10, "bold"), anchor=tk.W).pack(side=tk.LEFT)
        now = datetime.now().strftime("%H:%M")
        tk.Label(hdr, text=now, bg=bg, fg=T["txt3"],
                 font=(FONT[0], 9), anchor=tk.E).pack(side=tk.RIGHT)

        tk.Label(bubble, text=text, bg=bg, fg=T["txt"],
                 font=(FONT[0], 10), wraplength=500, justify=tk.LEFT,
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
            try:
                import sys as _sys
                _sys.path.insert(0, PARTNER_DIR)
                from partner.journal import Journal as _J
                from partner.knowledge import KnowledgeBase as _K
                from partner.task_queue import TaskQueue as _TQ
                from partner.state import StateManager as _SM
                from partner.conversation import ConversationEngine as _CE
                from partner.adapter import create_adapter as _ca

                ws = self.workspace
                j = _J(os.path.join(ws, 'state', 'journal.jsonl')) if ws else None
                k = _K(os.path.join(ws, 'state', 'knowledge.json')) if ws else None
                tq = _TQ(os.path.join(ws, 'state', 'task_queue.json')) if ws else None
                st = _SM(os.path.join(ws, 'state')) if ws else None
                eng = _CE(j, k, tq, st, ws or '')
                adapter = _ca('hermes', ws) if ws else None

                if adapter:
                    prompt = f"你是Partner，我的私人研究伙伴。用简短自然的口语回复。\n\n用户说: {text}"
                    reply = adapter.chat(prompt)
                    if reply:
                        self.root.after(0, lambda r=reply: self._add_chat_message("bot", r))
                        self.root.after(0, self._hide_thinking)
                        return

                reply = eng.respond(text)
                self.root.after(0, lambda r=reply: self._add_chat_message("bot", r))
            except Exception as e:
                self.root.after(0, lambda: self._add_chat_message("bot",
                    self._tr("chat_error", msg=str(e)[:100])))
            finally:
                self.root.after(0, self._hide_thinking)

        threading.Thread(target=do_reply, daemon=True).start()

    # ════════════════════════════════════════════════════════════════
    #  QQ Bot Tab — Multi-Bot Management
    # ════════════════════════════════════════════════════════════════

    def _build_tab_qq(self):
        f = self._tab_contents[2]
        f.configure(bg=T["bg"])

        # Top: add new bot
        top = tk.Frame(f, bg=T["bg"])
        top.pack(fill=tk.X, padx=0, pady=(0, 8))
        Btn(top, text="➕ 添加机器人", bg=T["accent3"], fg="white",
            command=self._qq_add_bot).pack(side=tk.LEFT)

        # Scrollable bot list
        canvas = tk.Canvas(f, bg=T["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(f, orient=tk.VERTICAL, command=canvas.yview)
        self._qq_inner = tk.Frame(canvas, bg=T["bg"])
        self._qq_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._qq_inner, anchor=tk.NW, tags="inner")
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(fill=tk.BOTH, expand=True)

        self._qq_bots = []  # list of dicts: {name, app_id, app_secret, frame, status_label}
        self._load_qq_bots()
        self._qq_render_list()

    def _qq_configs_path(self):
        if not self.workspace:
            return None
        return os.path.join(self.workspace, "qq_configs.json")

    def _load_qq_bots(self):
        self._qq_bots = []
        path = self._qq_configs_path()
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        self._qq_bots.append({
                            "name": item.get("name", f"Bot {len(self._qq_bots)+1}"),
                            "app_id": item.get("app_id", ""),
                            "app_secret": item.get("app_secret", ""),
                        })
            except: pass
        if not self._qq_bots:
            # Fallback: load legacy qq_config.json
            legacy = os.path.join(self.workspace or "", "qq_config.json")
            if os.path.exists(legacy):
                try:
                    with open(legacy, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    if cfg.get("app_id"):
                        self._qq_bots.append({
                            "name": "Bot 1",
                            "app_id": cfg["app_id"],
                            "app_secret": cfg.get("app_secret", ""),
                        })
                except: pass

    def _save_qq_bots(self):
        path = self._qq_configs_path()
        if not path:
            return
        data = []
        for bot in self._qq_bots:
            data.append({
                "name": bot["name"],
                "app_id": bot["app_id"],
                "app_secret": bot["app_secret"],
            })
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _qq_render_list(self):
        for w in self._qq_inner.winfo_children():
            w.destroy()
        if not self._qq_bots:
            tk.Label(self._qq_inner, text="尚未添加 QQ 机器人，点击上方「添加机器人」开始配置",
                     bg=T["bg"], fg=T["txt3"], font=(FONT[0], 11)).pack(pady=40)
            return
        for i, bot in enumerate(self._qq_bots):
            card = tk.Frame(self._qq_inner, bg=T["card"],
                            highlightbackground=T["border"], highlightthickness=1)
            card.pack(fill=tk.X, padx=0, pady=(0, 6))

            # Header: name + status + delete
            hdr = tk.Frame(card, bg=T["card"])
            hdr.pack(fill=tk.X, padx=14, pady=(8, 2))
            tk.Label(hdr, text=f"🤖  {bot['name']}", bg=T["card"], fg=T["blue"],
                     font=(FONT_HEADING[0], 11, "bold"), anchor=tk.W).pack(side=tk.LEFT)
            status_lbl = tk.Label(hdr, text="● 已停止", bg=T["card"], fg=T["red"],
                                  font=(FONT[0], 9), anchor=tk.E)
            status_lbl.pack(side=tk.RIGHT, padx=(8, 0))
            Btn(hdr, text="✕", bg=T["red"], fg="white", hover_bg="#da3633",
                command=lambda idx=i: self._qq_remove_bot(idx)).pack(
                    side=tk.RIGHT, padx=(4, 0))

            # Fields
            fields = tk.Frame(card, bg=T["card"])
            fields.pack(fill=tk.X, padx=14, pady=(2, 4))
            tk.Label(fields, text="名称:", bg=T["card"], fg=T["txt2"],
                     font=(FONT[0], 9), anchor=tk.W).grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
            name_var = tk.StringVar(value=bot["name"])
            name_entry = tk.Entry(fields, textvariable=name_var, bg=T["input_bg"], fg=T["txt"],
                                  font=(FONT[0], 9), relief=tk.FLAT,
                                  highlightbackground=T["border"], highlightthickness=1,
                                  insertbackground=T["txt"])
            name_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 8), ipady=2)
            name_entry.bind("<FocusOut>", lambda e, idx=i, v=name_var: self._qq_update_name(idx, v.get()))

            tk.Label(fields, text="AppID:", bg=T["card"], fg=T["txt2"],
                     font=(FONT[0], 9), anchor=tk.W).grid(row=1, column=0, sticky=tk.W, padx=(0, 4))
            appid_var = tk.StringVar(value=bot["app_id"])
            appid_entry = tk.Entry(fields, textvariable=appid_var, bg=T["input_bg"], fg=T["txt"],
                                   font=(FONT_MONO[0], 9), relief=tk.FLAT,
                                   highlightbackground=T["border"], highlightthickness=1,
                                   insertbackground=T["txt"])
            appid_entry.grid(row=1, column=1, sticky=tk.EW, padx=(0, 8), ipady=2)
            appid_entry.bind("<FocusOut>", lambda e, idx=i, v=appid_var: self._qq_update_appid(idx, v.get()))

            tk.Label(fields, text="Secret:", bg=T["card"], fg=T["txt2"],
                     font=(FONT[0], 9), anchor=tk.W).grid(row=2, column=0, sticky=tk.W, padx=(0, 4))
            sec_var = tk.StringVar(value=bot["app_secret"])
            sec_entry = tk.Entry(fields, textvariable=sec_var, bg=T["input_bg"], fg=T["txt"],
                                 font=(FONT_MONO[0], 9), relief=tk.FLAT, show="*",
                                 highlightbackground=T["border"], highlightthickness=1,
                                 insertbackground=T["txt"])
            sec_entry.grid(row=2, column=1, sticky=tk.EW, padx=(0, 8), ipady=2)
            sec_entry.bind("<FocusOut>", lambda e, idx=i, v=sec_var: self._qq_update_secret(idx, v.get()))

            fields.columnconfigure(1, weight=1)

            # Start/Stop buttons
            btn_row = tk.Frame(card, bg=T["card"])
            btn_row.pack(fill=tk.X, padx=14, pady=(2, 8))
            Btn(btn_row, text="▶ 启动", bg=T["green"], fg="#0d1117",
                command=lambda idx=i: self._qq_start_bot(idx)).pack(side=tk.LEFT, padx=(0, 6))
            Btn(btn_row, text="■ 停止", bg=T["red"], fg="white",
                command=lambda idx=i: self._qq_stop_bot(idx)).pack(side=tk.LEFT)

            bot["frame"] = card
            bot["status_label"] = status_lbl
            bot["name_var"] = name_var

    def _qq_add_bot(self):
        self._qq_bots.append({
            "name": f"Bot {len(self._qq_bots)+1}",
            "app_id": "",
            "app_secret": "",
        })
        self._save_qq_bots()
        self._qq_render_list()

    def _qq_remove_bot(self, idx):
        if 0 <= idx < len(self._qq_bots):
            del self._qq_bots[idx]
            self._save_qq_bots()
            self._qq_render_list()

    def _qq_update_name(self, idx, val):
        if 0 <= idx < len(self._qq_bots):
            self._qq_bots[idx]["name"] = val
            self._save_qq_bots()

    def _qq_update_appid(self, idx, val):
        if 0 <= idx < len(self._qq_bots):
            self._qq_bots[idx]["app_id"] = val
            self._save_qq_bots()

    def _qq_update_secret(self, idx, val):
        if 0 <= idx < len(self._qq_bots):
            self._qq_bots[idx]["app_secret"] = val
            self._save_qq_bots()

    def _qq_start_bot(self, idx):
        if 0 <= idx >= len(self._qq_bots):
            return
        bot = self._qq_bots[idx]
        if not bot["app_id"] or not bot["app_secret"]:
            messagebox.showwarning("提示", "请先填写 AppID 和 AppSecret")
            return
        if hasattr(self, f"_qq_proc_{idx}") and getattr(self, f"_qq_proc_{idx}") is not None:
            messagebox.showinfo("提示", "该机器人已在运行")
            return

        # Write temp config and start
        tmp_cfg = os.path.join(self.workspace, "state", f"qq_bot_{idx}_cfg.json")
        os.makedirs(os.path.dirname(tmp_cfg), exist_ok=True)
        with open(tmp_cfg, "w") as f:
            json.dump({"app_id": bot["app_id"], "app_secret": bot["app_secret"],
                       "mode": "official", "is_sandbox": True}, f)

        def do_start(idx=idx):
            out, err, rc = run_silent(
                [sys.executable, "-m", "partner.cli", "bot", "start", "qq", "--foreground"],
                timeout=15
            )
            def upd():
                if 0 <= idx < len(self._qq_bots) and "status_label" in self._qq_bots[idx]:
                    if rc == 0:
                        self._qq_bots[idx]["status_label"].config(text="● 运行中", fg=T["green"])
                    else:
                        self._qq_bots[idx]["status_label"].config(text="● 启动失败", fg=T["red"])
            self.root.after(0, upd)
            setattr(self, f"_qq_proc_{idx}", None)

        setattr(self, f"_qq_proc_{idx}", True)
        if "status_label" in bot:
            bot["status_label"].config(text="● 启动中…", fg=T["yellow"])
        threading.Thread(target=do_start, daemon=True).start()

    def _qq_stop_bot(self, idx):
        if 0 <= idx >= len(self._qq_bots):
            return
        def do_stop(idx=idx):
            out, err, rc = run_silent(
                [sys.executable, "-m", "partner.cli", "bot", "stop", "qq"],
                timeout=10
            )
            def upd():
                if 0 <= idx < len(self._qq_bots) and "status_label" in self._qq_bots[idx]:
                    self._qq_bots[idx]["status_label"].config(text="● 已停止", fg=T["red"])
            self.root.after(0, upd)
            setattr(self, f"_qq_proc_{idx}", None)
        threading.Thread(target=do_stop, daemon=True).start()    # ════════════════════════════════════════════════════════════════
    #  Exploration Records Tab — File Tree Navigator
    # ════════════════════════════════════════════════════════════════

    def _build_tab_logs(self):
        f = self._tab_contents[3]
        f.configure(bg=T["bg"])

        # Top: root selector + breadcrumb
        top = tk.Frame(f, bg=T["bg"])
        top.pack(fill=tk.X, padx=0, pady=(0, 6))

        self._log_root = tk.StringVar(value="journal")
        roots = [("📝 日志", "journal"), ("💬 对话", "dialogue"),
                 ("📂 项目", "projects"), ("📚 知识", "knowledge")]
        for label, val in roots:
            rb = tk.Radiobutton(top, text=label, variable=self._log_root, value=val,
                                bg=T["bg"], fg=T["txt2"], selectcolor=T["input_bg"],
                                activebackground=T["bg"], activeforeground=T["accent2"],
                                font=(FONT[0], 10), indicatoron=0, padx=12, pady=4,
                                relief=tk.FLAT, overrelief=tk.RAISED,
                                command=self._log_go_root)
            rb.pack(side=tk.LEFT, padx=(0, 4))

        # Breadcrumb bar
        bc = tk.Frame(f, bg=T["bg3"], highlightbackground=T["border"], highlightthickness=1)
        bc.pack(fill=tk.X, padx=0, pady=(0, 6))
        self._log_breadcrumb = tk.Label(bc, text="", bg=T["bg3"], fg=T["txt2"],
                                        font=(FONT[0], 9), anchor=tk.W, padx=10, pady=3)
        self._log_breadcrumb.pack(fill=tk.X)

        # File list (Listbox with double-click)
        list_frame = tk.Frame(f, bg=T["bg2"], highlightbackground=T["border"], highlightthickness=1)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 6))

        self._log_listbox = tk.Listbox(list_frame, bg=T["bg2"], fg=T["txt"],
                                       font=(FONT_MONO[0], 10), relief=tk.FLAT,
                                       selectbackground=T["accent3"], selectforeground="white",
                                       highlightthickness=0, borderwidth=0,
                                       activestyle="none")
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._log_listbox.yview)
        self._log_listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_listbox.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._log_listbox.bind("<Double-Button-1>", self._log_on_double_click)
        self._log_listbox.bind("<Return>", self._log_on_double_click)

        # Bottom: back button + refresh
        bf = tk.Frame(f, bg=T["bg"])
        bf.pack(fill=tk.X, padx=0, pady=(0, 0))
        Btn(bf, text="⬆ 上级目录", command=self._log_go_up).pack(side=tk.LEFT, padx=(0, 8))
        Btn(bf, text="⟳ 刷新", command=self._log_refresh).pack(side=tk.LEFT)

        # Navigation state
        self._log_path_stack = []  # stack of absolute paths
        self._log_current_path = None
        self._log_refresh()

    def _log_go_root(self):
        """Go to root of the selected section."""
        self._log_path_stack = []
        self._log_current_path = None
        self._log_refresh()

    def _log_go_up(self):
        """Go up one directory level."""
        if self._log_path_stack:
            self._log_current_path = self._log_path_stack.pop()
            self._log_refresh()

    def _log_get_base(self):
        """Get the base directory for the selected root section."""
        sec = self._log_root.get()
        if sec == "journal":
            return os.path.join(self.workspace, "state") if self.workspace else None
        return os.path.join(self.workspace, sec) if self.workspace else None

    def _log_on_double_click(self, event=None):
        sel = self._log_listbox.curselection()
        if not sel:
            return
        line = self._log_listbox.get(sel[0])
        # Parse: "  📁  name  (N 项)" or "  📄  name  (123B)"
        name = line[5:].strip()
        # Remove trailing "(N 项)" or "(123B)"
        paren = name.rfind("  (")
        if paren > 0:
            name = name[:paren].strip()

        if self._log_current_path:
            full = os.path.join(self._log_current_path, name)
        else:
            base = self._log_get_base()
            if not base:
                return
            full = os.path.join(base, name)

        if os.path.isdir(full):
            # Navigate into directory
            self._log_path_stack.append(self._log_current_path)
            self._log_current_path = full
            self._log_refresh()
        elif os.path.isfile(full):
            # View file content
            self._log_view_file(full)

    def _log_refresh(self):
        self._log_listbox.delete(0, tk.END)

        if not self.workspace:
            self._log_listbox.insert(tk.END, "  ❌ 未配置工作区")
            return

        sec = self._log_root.get()

        # Journal: special content view
        if sec == "journal":
            self._log_breadcrumb.config(text="📝  日志")
            fp = os.path.join(self.workspace, "state", "journal.jsonl")
            if os.path.exists(fp):
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    if not lines:
                        self._log_listbox.insert(tk.END, "  (暂无记录)")
                    for line in reversed(lines[-100:]):
                        try:
                            entry = json.loads(line.strip())
                            ts = entry.get("timestamp", "")[11:19] or entry.get("timestamp", "")[:19]
                            task = entry.get("task_title", "") or entry.get("type", "")
                            result = entry.get("result_summary", "") or ""
                            text = f"  [{ts}] {task}"
                            if result:
                                text += f" — {result[:60]}"
                            self._log_listbox.insert(tk.END, text)
                        except:
                            self._log_listbox.insert(tk.END, f"  {line.strip()[:80]}")
                except Exception as e:
                    self._log_listbox.insert(tk.END, f"  ❌ {e}")
            else:
                self._log_listbox.insert(tk.END, "  (暂无日志)")
            return

        # Dialogue: special content view
        if sec == "dialogue":
            self._log_breadcrumb.config(text="💬  对话历史")
            fp = os.path.join(self.workspace, "state", "dialog_history.jsonl")
            if os.path.exists(fp):
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    if not lines:
                        self._log_listbox.insert(tk.END, "  (暂无对话)")
                    for line in reversed(lines[-50:]):
                        try:
                            d = json.loads(line.strip())
                            role = d.get("role", "?")
                            content = d.get("content", "")[:100]
                            ts = d.get("timestamp", "")[11:19]
                            mark = "🧑" if role == "user" else "🤖"
                            self._log_listbox.insert(tk.END, f"  [{ts}] {mark} {content}")
                        except:
                            self._log_listbox.insert(tk.END, f"  {line.strip()[:80]}")
                except Exception as e:
                    self._log_listbox.insert(tk.END, f"  ❌ {e}")
            else:
                self._log_listbox.insert(tk.END, "  (暂无对话)")
            return

        # Directory-based sections: list files/folders
        base = self._log_get_base()
        if not base or not os.path.isdir(base):
            self._log_listbox.insert(tk.END, "  ❌ 目录不存在")
            return

        target = self._log_current_path if self._log_current_path else base

        # Update breadcrumb
        rel = os.path.relpath(target, self.workspace) if self.workspace else target
        self._log_breadcrumb.config(text=f"📂  {rel}")

        # Add ".." if not at root
        if self._log_current_path:
            self._log_listbox.insert(tk.END, "  📁  ..  (上级目录)")

        # List contents
        items = []
        for name in sorted(os.listdir(target), key=str.lower):
            full = os.path.join(target, name)
            if os.path.isdir(full):
                cnt = len(os.listdir(full))
                items.append((name, True, cnt))
            else:
                size = os.path.getsize(full)
                items.append((name, False, size))

        if not items and not self._log_current_path:
            self._log_listbox.insert(tk.END, "  (空)")
        else:
            for name, is_dir, extra in items:
                icon = "📁" if is_dir else "📄"
                extra_text = f"({extra} 项)" if is_dir else f"({extra:,}B)"
                self._log_listbox.insert(tk.END, f"  {icon}  {name}  {extra_text}")

    def _log_view_file(self, fpath):
        """Show file content in a popup window."""
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            content = f"读取失败: {e}"

        fname = os.path.basename(fpath)
        rel = os.path.relpath(fpath, self.workspace) if self.workspace else fname

        win = tk.Toplevel(self.root)
        win.title(f"📄 {rel}")
        win.configure(bg=T["bg"])
        win.geometry("750x550")
        win.transient(self.root)

        # Header
        tk.Label(win, text=f"📄  {rel}", bg=T["bg"], fg=T["accent2"],
                 font=(FONT_HEADING[0], 12, "bold"), anchor=tk.W).pack(
                     fill=tk.X, padx=16, pady=(12, 4))

        # Content
        txt = scrolledtext.ScrolledText(win, font=(FONT_MONO[0], 10),
            bg=T["bg2"], fg=T["txt"], relief=tk.FLAT,
            padx=14, pady=12, borderwidth=0,
            highlightbackground=T["border"], highlightthickness=1,
            insertbackground=T["txt"],
            selectbackground=T["accent"], selectforeground="white")
        txt.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 12))
        txt.insert(tk.END, content)
        txt.config(state=tk.DISABLED)
        # If the file is large, scroll to top
        txt.see("1.0")    # ════════════════════════════════════════════════════════════════
    #  Shared Helpers
    # ════════════════════════════════════════════════════════════════

    def _set_dot(self, color):
        self.dot_canvas.itemconfig(self.dot_id, fill=color)

    def _open_workspace(self):
        path = self.workspace or PARTNER_DIR
        if os.path.exists(path):
            os.startfile(path)

    def _start_auto_refresh(self):
        if self._auto_refresh_id:
            self.root.after_cancel(self._auto_refresh_id)
        self._auto_refresh_id = self.root.after(AUTO_REFRESH_INTERVAL, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        self._refresh_dashboard()
        self._auto_refresh_id = self.root.after(AUTO_REFRESH_INTERVAL, self._auto_refresh_tick)

    # ════════════════════════════════════════════════════════════════
    #  Setup Wizard
    # ════════════════════════════════════════════════════════════════

    def _show_setup(self):
        win = tk.Toplevel(self.root)
        win.title(self._tr("setup_title"))
        win.configure(bg=T["bg"])
        win.transient(self.root)
        win.grab_set()
        win.resizable(True, True)
        center_window(win, 600, 680)

        tk.Label(win, text=self._tr("setup_title"), bg=T["bg"], fg=T["accent2"],
                 font=(FONT_HEADING[0], 20, "bold")).pack(pady=(20, 2))
        tk.Label(win, text=self._tr("setup_sub"), bg=T["bg"],
                 fg=T["txt3"], font=(FONT[0], 10)).pack(pady=(0, 20))

        s1 = AccentCard(win, title="1. 选择工作区文件夹", accent_color=T["green"])
        s1.pack(fill=tk.X, padx=28, pady=(0, 12))
        ws_var = tk.StringVar(value=self.workspace or os.path.expanduser("~/partner_workspace"))
        ws_row = tk.Frame(s1.body, bg=T["card"])
        ws_row.pack(fill=tk.X, padx=18, pady=(4, 14))
        ws_entry = Input(ws_row, textvariable=ws_var)
        ws_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        Btn(ws_row, text="浏览", command=lambda: ws_var.set(
            filedialog.askdirectory(title=self._tr("setup_title")) or ws_var.get())
            ).pack(side=tk.LEFT, padx=(10, 0))

        s2 = AccentCard(win, title="2. AI 后端", accent_color=T["blue"])
        s2.pack(fill=tk.X, padx=28, pady=(0, 12))
        backend_var = tk.StringVar(value="hermes")
        for val, label in [("hermes", "🤖  Hermes Agent（推荐）"),
                          ("skip", "⏳  暂不设置")]:
            tk.Radiobutton(s2.body, text=label, variable=backend_var, value=val,
                          bg=T["card"], fg=T["txt"], selectcolor=T["input_bg"],
                          activebackground=T["card"], activeforeground=T["accent2"],
                          font=(FONT[0], 10)).pack(anchor=tk.W, padx=24, pady=3)

        s3 = AccentCard(win, title="3. QQ 机器人（可选）", accent_color=T["pink"])
        s3.pack(fill=tk.X, padx=28, pady=(0, 12))
        tk.Label(s3.body, text="填入 QQ 开放平台的 AppID 和 AppSecret，也可稍后配置。",
                 bg=T["card"], fg=T["txt3"], font=(FONT[0], 9)).pack(anchor=tk.W, padx=18)
        qf = tk.Frame(s3.body, bg=T["card"])
        qf.pack(fill=tk.X, padx=18, pady=(8, 6))
        tk.Label(qf, text="AppID", bg=T["card"], fg=T["txt2"],
                 width=8, anchor=tk.W).pack(side=tk.LEFT)
        setup_qq_id = Input(qf)
        setup_qq_id.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        qf2 = tk.Frame(s3.body, bg=T["card"])
        qf2.pack(fill=tk.X, padx=18, pady=(0, 14))
        tk.Label(qf2, text="AppSecret", bg=T["card"], fg=T["txt2"],
                 width=8, anchor=tk.W).pack(side=tk.LEFT)
        setup_qq_pw = Input(qf2)
        setup_qq_pw.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        status_var = tk.StringVar(value="准备就绪")
        tk.Label(win, textvariable=status_var, bg=T["bg"], fg=T["txt3"],
                 font=(FONT[0], 10)).pack(pady=(4, 0))

        def do_setup():
            ws = ws_var.get().strip()
            if not ws:
                messagebox.showerror("Error", "请选择工作区文件夹")
                return
            os.makedirs(ws, exist_ok=True)
            status_var.set("正在创建工作区…")
            win.update()
            for sub in ["state", "logs", "data"]:
                os.makedirs(os.path.join(ws, sub), exist_ok=True)
            config = {"workspace": ws, "backend": backend_var.get(),
                      "created": datetime.now().isoformat()}
            with open(os.path.join(ws, "config.json"), "w") as fh:
                json.dump(config, fh, indent=2)
            qq_id = setup_qq_id.get().strip()
            qq_pw = setup_qq_pw.get().strip()
            if qq_id:
                qq_cfg = {"mode": "official", "app_id": qq_id, "app_secret": qq_pw}
                with open(os.path.join(ws, "qq_config.json"), "w") as fh:
                    json.dump(qq_cfg, fh, indent=2)
            status_var.set("设置完成！")
            self.workspace = ws
            win.after(600, win.destroy)
            self._rebuild_ui()

        Btn(win, text="🚀  开始设置", bg=T["accent3"], fg="white",
            hover_bg=T["accent"], command=do_setup).pack(pady=22)

    def run(self):
        self.root.mainloop()


def main():
    root = tk.Tk()
    app = PartnerApp(root)
    app.run()


if __name__ == "__main__":
    main()
