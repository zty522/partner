#!/usr/bin/env python3
"""Partner — Modern Desktop Application (v6)

Features: Dashboard, Chat, QQ Bot, Logs
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
LOG_FILE_LIMIT = 5
LOG_BYTE_LIMIT = 4000

# ── i18n ──
LANGUAGES = {"zh": "中文", "en": "English"}

L = {
    "zh": {
        "app_title": "Partner",
        "ver": "v0.4.0",
        "tab_dashboard": "仪表盘",
        "tab_chat": "对话",
        "tab_qq": "QQ 机器人",
        "tab_logs": "日志",
        "status_loading": "加载中…",
        "status_no_workspace": "尚未配置工作区\n\n点击「设置向导」开始",
        "status_workspace": "工作区",
        "status_ready": "就绪",
        "status_no_ws_short": "未配置工作区",
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
        "logs_title": "日志查看",
        "btn_reload": "  ⟳  刷新日志  ",
        "logs_no_ws": "未配置工作区。",
        "logs_no_dir": "未找到日志目录。",
        "setup_title": "Partner 设置",
        "setup_sub": "配置你的 AI 研究伙伴",
        "setup_step1": "1. 选择工作区文件夹",
        "setup_step2": "2. AI 后端",
        "setup_step3": "3. QQ 机器人（可选）",
        "setup_browse": "  浏览  ",
        "setup_hermes": "  🤖  Hermes Agent（推荐）",
        "setup_openclaw": "  🧠  OpenClaw",
        "setup_skip": "  ⏳  暂不设置",
        "setup_qq_hint": "填入 QQ 号与密码，也可稍后配置。",
        "setup_qq_id": "QQ 号",
        "setup_qq_pw": "密码",
        "setup_ready": "准备就绪",
        "setup_start": "  🚀  开始设置  ",
        "setup_select_ws": "请选择工作区文件夹",
        "setup_creating": "正在创建工作区…",
        "setup_installing": "正在安装 Hermes Agent…",
        "setup_complete": "设置完成！",
        "lang_toggle": "English",
        "last_update": "最后更新: {time}",
    },
    "en": {
        "app_title": "Partner",
        "ver": "v0.4.0",
        "tab_dashboard": "Dashboard",
        "tab_chat": "Chat",
        "tab_qq": "QQ Bot",
        "tab_logs": "Logs",
        "status_loading": "Loading…",
        "status_no_workspace": "Workspace not configured.\n\nClick 'Setup Wizard' to get started.",
        "status_workspace": "Workspace",
        "status_ready": "Ready",
        "status_no_ws_short": "No workspace",
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
        "logs_title": "Log Viewer",
        "btn_reload": "  ⟳  Reload Logs  ",
        "logs_no_ws": "No workspace configured.",
        "logs_no_dir": "No logs directory found.",
        "setup_title": "Partner Setup",
        "setup_sub": "Configure your AI Research Companion",
        "setup_step1": "1. Choose Workspace Folder",
        "setup_step2": "2. AI Backend",
        "setup_step3": "3. QQ Bot (optional)",
        "setup_browse": "  Browse  ",
        "setup_hermes": "  🤖  Hermes Agent (recommended)",
        "setup_openclaw": "  🧠  OpenClaw",
        "setup_skip": "  ⏳  Skip for now",
        "setup_qq_hint": "Enter QQ ID and password, or configure later.",
        "setup_qq_id": "QQ ID",
        "setup_qq_pw": "Password",
        "setup_ready": "Ready to configure",
        "setup_start": "  🚀  Start Setup  ",
        "setup_select_ws": "Please select a workspace folder.",
        "setup_creating": "Creating workspace…",
        "setup_installing": "Installing Hermes Agent…",
        "setup_complete": "Setup complete!",
        "lang_toggle": "中文",
        "last_update": "Last updated: {time}",
    },
}


def tr(key, lang="zh", **kw):
    """Translate a key, optionally formatting with kwargs."""
    val = L.get(lang, L["zh"]).get(key, key)
    if kw:
        return val.format(**kw)
    return val


# ── Modern Theme (inspired by VS Code / Linear dark) ──
T = {
    "bg":        "#0d1117",   # GitHub dark
    "bg2":       "#161b22",
    "bg3":       "#21262d",
    "card":      "#161b22",
    "card_hl":   "#30363d",
    "accent":    "#58a6ff",   # Blue accent
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

FONT = ("Segoe UI Variable", "Segoe UI", "TkDefaultFont")
FONT_MONO = ("Cascadia Code", "Cascadia Mono", "Consolas", "monospace")


def find_workspace():
    """Find existing Partner workspace (delegates to setup.find_workspace)."""
    from .setup import find_workspace as _fw
    return _fw()


def run_silent(cmd, cwd=None, timeout=30, timeout_ok=False):
    """Run command silently, return (stdout, stderr, returncode).

    Args:
        timeout_ok: If True, TimeoutExpired is treated as success (rc=0).
                    Use for long-running processes (bots, servers) where
                    the command runs indefinitely and timeout means it started OK.
    """
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
            return "", "", 0  # timeout = process still running = success
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
    """Card with accent top border + subtle shadow effect."""
    def __init__(self, parent, title=None, accent_color=None, **kw):
        super().__init__(parent, bg=T["card"], highlightthickness=0, **kw)
        # Thin accent line
        line = tk.Frame(self, bg=accent_color or T["accent"], height=2)
        line.pack(fill=tk.X)
        line.pack_propagate(False)
        body = tk.Frame(self, bg=T["card"])
        body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        if title:
            h = tk.Frame(body, bg=T["card"])
            h.pack(fill=tk.X, padx=18, pady=(14, 2))
            tk.Label(h, text=title, bg=T["card"], fg=accent_color or T["accent2"],
                     font=(FONT[0], 11, "bold"), anchor=tk.W).pack(side=tk.LEFT)
        self.body = body


class Input(tk.Entry):
    """Clean input field with focus glow."""
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
    """Modern flat button with hover state."""
    def __init__(self, parent, text="", command=None,
                 bg=None, fg=None, hover_bg=None, hover_fg=None, **kw):
        self._cmd = command
        bg = bg or T["card_hl"]
        fg = fg or T["txt"]
        hover_bg = hover_bg or T["accent3"]
        hover_fg = hover_fg or "#ffffff"
        super().__init__(parent, bg=bg, **kw)

        self._label = tk.Label(self, text=text, bg=bg, fg=fg,
                               font=(FONT[0], 10), cursor="hand2",
                               padx=16, pady=7)
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
        self._lang = "zh"  # default language
        self.root.title(tr("app_title", self._lang))
        self.root.minsize(820, 620)
        self.root.configure(bg=T["bg"])
        center_window(self.root, 1000, 720)

        self.workspace = find_workspace()
        self._auto_refresh_id = None
        self._build_ui()

        if not self.workspace:
            self._set_status(tr("status_no_workspace", self._lang), T["yellow"])
            self.root.after(500, self._show_setup)
        else:
            self._refresh_status()
            self._load_chat_history()
            self._start_auto_refresh()

    def _tr(self, key, **kw):
        return tr(key, self._lang, **kw)

    def _toggle_lang(self):
        """Switch between Chinese and English."""
        self._lang = "en" if self._lang == "zh" else "zh"
        self._rebuild_ui()

    def _rebuild_ui(self):
        """Rebuild the entire UI with current language."""
        self.root.title(self._tr("app_title"))
        self._active_tab = 0
        # Destroy old tab contents
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
        # Clear everything except main frame
        for w in main.winfo_children():
            w.destroy()

        # ── Header ──
        hdr = tk.Frame(main, bg=T["bg"])
        hdr.pack(fill=tk.X, pady=(0, 12))

        lf = tk.Frame(hdr, bg=T["bg"])
        lf.pack(side=tk.LEFT)
        # App icon badge
        badge = tk.Canvas(lf, width=32, height=32, bg=T["accent3"],
                          highlightthickness=0)
        badge.pack(side=tk.LEFT, padx=(0, 10))
        badge.create_oval(3, 3, 29, 29, fill=T["accent"], outline="")
        badge.create_text(16, 16, text="P", fill="white",
                          font=(FONT[0], 15, "bold"))

        tk.Label(lf, text="Partner", bg=T["bg"], fg=T["txt"],
                 font=(FONT[0], 22, "bold")).pack(side=tk.LEFT)
        tk.Label(lf, text=self._tr("ver"), bg=T["bg"], fg=T["txt3"],
                 font=(FONT[0], 10)).pack(side=tk.LEFT, padx=(8, 0), pady=(5, 0))

        # Right: language toggle + status dot
        rf = tk.Frame(hdr, bg=T["bg"])
        rf.pack(side=tk.RIGHT)

        # Language toggle button
        lang_text = tr("lang_toggle", self._lang)
        self.lang_btn = tk.Label(rf, text=lang_text, bg=T["bg3"], fg=T["txt2"],
                                 font=(FONT[0], 9), cursor="hand2", padx=10, pady=3)
        self.lang_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self.lang_btn.bind("<Button-1>", lambda e: self._toggle_lang())
        self.lang_btn.bind("<Enter>", lambda e: self.lang_btn.configure(bg=T["card_hl"]))
        self.lang_btn.bind("<Leave>", lambda e: self.lang_btn.configure(bg=T["bg3"]))

        # Status dot
        self.dot_canvas = tk.Canvas(rf, width=12, height=12, bg=T["bg"],
                                    highlightthickness=0)
        self.dot_canvas.pack(side=tk.RIGHT, padx=(0, 6))
        self.dot_id = self.dot_canvas.create_oval(1, 1, 11, 11,
            fill=T["txt3"], outline="", width=0)

        self.hdr_status = tk.Label(rf, text="", bg=T["bg"], fg=T["txt3"],
                                   font=(FONT[0], 9))
        self.hdr_status.pack(side=tk.RIGHT)

        # ── Custom Tab Bar ──
        self._tab_frame = tk.Frame(main, bg=T["bg"])
        self._tab_frame.pack(fill=tk.X, pady=(0, 0))

        tab_bar = tk.Frame(self._tab_frame, bg=T["bg2"], height=40)
        tab_bar.pack(fill=tk.X)
        tab_bar.pack_propagate(False)

        self._tabs = [
            (self._tr("tab_dashboard"), "\U0001f4ca"),
            (self._tr("tab_chat"),      "\U0001f4ac"),
            (self._tr("tab_qq"),        "\U0001f916"),
            (self._tr("tab_logs"),      "\U0001f4c4"),
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

        # Build tab contents
        self._build_tab_dashboard()
        self._build_tab_chat()
        self._build_tab_qq()
        self._build_tab_logs()

        # ── Bottom status bar ──
        bar_frame = tk.Frame(main, bg=T["bg2"])
        bar_frame.pack(fill=tk.X, pady=(10, 0))
        self.status_bar = tk.Label(bar_frame, text=self._tr("status_ready"),
                                   bg=T["bg2"], fg=T["txt3"],
                                   font=(FONT[0], 9), anchor=tk.W, padx=14, pady=5)
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
            self._refresh_status()

    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    #  Dashboard Tab
    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    def _build_tab_dashboard(self):
        f = self._tab_contents[0]
        f.configure(bg=T["bg"])

        sc = AccentCard(f, title=self._tr("tab_dashboard"),
                        accent_color=T["blue"])
        sc.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 10))

        self.status_text = tk.Text(sc.body, height=12,
                                   font=(FONT_MONO[0], 10),
                                   bg=T["bg2"], fg=T["txt"], relief=tk.FLAT,
                                   padx=18, pady=14, wrap=tk.WORD,
                                   insertbackground=T["txt"],
                                   selectbackground=T["accent"],
                                   selectforeground="white",
                                   highlightbackground=T["border"],
                                   highlightthickness=1,
                                   borderwidth=0)
        self.status_text.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 14))
        self._set_status(self._tr("status_loading"))

        bf = tk.Frame(f, bg=T["bg"])
        bf.pack(fill=tk.X, padx=0, pady=(0, 0))
        Btn(bf, text=self._tr("btn_refresh"),
            command=self._refresh_status).pack(side=tk.LEFT, padx=(0, 8))
        Btn(bf, text=self._tr("btn_open_ws"),
            command=self._open_workspace).pack(side=tk.LEFT, padx=(0, 8))
        Btn(bf, text=self._tr("btn_setup"), bg=T["accent3"], fg="white",
            hover_bg=T["accent"], command=self._show_setup).pack(side=tk.RIGHT)

    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    #  Chat Tab
    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
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
                                       font=(FONT[0], 9, "italic"))
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
                 font=(FONT[0], 9, "bold"), anchor=tk.W).pack(side=tk.LEFT)
        now = datetime.now().strftime("%H:%M")
        tk.Label(hdr, text=now, bg=bg, fg=T["txt3"],
                 font=(FONT[0], 8), anchor=tk.E).pack(side=tk.RIGHT)

        tk.Label(bubble, text=text, bg=bg, fg=T["txt"],
                 font=(FONT[0], 10), wraplength=480, justify=tk.LEFT,
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
            # Direct import + call (no temp script file - avoids quoting bugs)
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

                # Tier 1: Try LLM
                if adapter:
                    prompt = f"你是Partner，我的私人研究伙伴。用简短自然的口语回复。\n\n用户说: {text}"
                    reply = adapter.chat(prompt)
                    if reply:
                        self.root.after(0, lambda r=reply: self._add_chat_message("bot", r))
                        self.root.after(0, self._hide_thinking)
                        return

                # Tier 2: ConversationEngine fallback
                reply = eng.respond(text)
                self.root.after(0, lambda r=reply: self._add_chat_message("bot", r))
            except Exception as e:
                self.root.after(0, lambda: self._add_chat_message("bot",
                    self._tr("chat_error", msg=str(e)[:100])))
            finally:
                self.root.after(0, self._hide_thinking)

        threading.Thread(target=do_reply, daemon=True).start()

    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    #  QQ Bot Tab — Official QQ Bot only
    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    def _build_tab_qq(self):
        f = self._tab_contents[2]
        f.configure(bg=T["bg"])

        # Info banner
        info = tk.Frame(f, bg=T["bg3"], highlightbackground=T["border"],
                        highlightthickness=1)
        info.pack(fill=tk.X, padx=0, pady=(0, 10))
        tk.Label(info, text=self._tr("qq_banner"),
                 bg=T["bg3"], fg=T["blue"], font=(FONT[0], 10, "bold"),
                 anchor=tk.W).pack(fill=tk.X, padx=16, pady=(8, 2))

        # ── Connection card ──
        cc = AccentCard(f, title=self._tr("qq_config_title"),
                        accent_color=T["accent"])
        cc.pack(fill=tk.X, padx=0, pady=(0, 10))

        # AppID
        aid_row = tk.Frame(cc.body, bg=T["card"])
        aid_row.pack(fill=tk.X, padx=18, pady=(10, 10))
        tk.Label(aid_row, text=self._tr("qq_appid"), bg=T["card"], fg=T["txt2"],
                 font=(FONT[0], 10), width=10, anchor=tk.W).pack(side=tk.LEFT)
        self.qq_appid = Input(aid_row)
        self.qq_appid.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)

        # AppSecret
        sec_row = tk.Frame(cc.body, bg=T["card"])
        sec_row.pack(fill=tk.X, padx=18, pady=(0, 10))
        tk.Label(sec_row, text=self._tr("qq_secret"), bg=T["card"], fg=T["txt2"],
                 font=(FONT[0], 10), width=10, anchor=tk.W).pack(side=tk.LEFT)
        self.qq_secret = Input(sec_row, show="*")
        self.qq_secret.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)

        # Save/Load
        btn_row = tk.Frame(cc.body, bg=T["card"])
        btn_row.pack(fill=tk.X, padx=18, pady=(4, 14))
        Btn(btn_row, text=self._tr("qq_save"), bg=T["accent3"], fg="white",
            hover_bg=T["accent"],
            command=self._save_qq_config).pack(side=tk.LEFT, padx=(0, 8))
        Btn(btn_row, text=self._tr("qq_load"),
            command=self._load_qq_config).pack(side=tk.LEFT)

        # ── Bot Status card ──
        st = AccentCard(f, title=self._tr("qq_status_title"),
                        accent_color=T["green"])
        st.pack(fill=tk.X, padx=0, pady=(0, 0))

        self.qq_status_label = tk.Label(st.body, text=self._tr("qq_not_running"),
                                        bg=T["card"], fg=T["txt3"],
                                        font=(FONT[0], 10))
        self.qq_status_label.pack(anchor=tk.W, padx=18, pady=(6, 8))

        sbf = tk.Frame(st.body, bg=T["card"])
        sbf.pack(fill=tk.X, padx=18, pady=(0, 14))
        Btn(sbf, text=self._tr("qq_start"), bg=T["green"], fg="#0d1117",
            hover_bg="#2ea043",
            command=self._start_qq_bot).pack(side=tk.LEFT, padx=(0, 8))
        Btn(sbf, text=self._tr("qq_stop"), bg=T["red"], fg="white",
            hover_bg="#da3633",
            command=self._stop_qq_bot).pack(side=tk.LEFT)

        self.root.after(300, self._load_qq_config)

    def _qq_config_path(self):
        if not self.workspace:
            return None
        return os.path.join(self.workspace, "qq_config.json")

    def _save_qq_config(self):
        """Save QQ Bot config as official mode only."""
        path = self._qq_config_path()
        if not path:
            messagebox.showerror("Error", self._tr("qq_no_ws"))
            return
        cfg = {
            "mode": "official",
            "app_id": self.qq_appid.get().strip(),
            "app_secret": self.qq_secret.get().strip(),
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(cfg, f, indent=2)
        messagebox.showinfo("Saved", self._tr("qq_saved"))

    def _load_qq_config(self):
        """Load QQ Bot config and populate fields."""
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
        except Exception:
            pass

    def _start_qq_bot(self):
        if not self.workspace:
            messagebox.showerror("Error", self._tr("qq_no_ws"))
            return

        # Save config first
        self._save_qq_config()

        def do_start():
            # Run bot start in foreground so we can see the output
            out, err, rc = run_silent(
                [sys.executable, "-m", "partner.cli", "bot", "start", "qq", "--foreground"],
                timeout=15  # Wait up to 15s for connection
            )
            if rc == 0:
                # Check if there's useful output
                if "✅" in out or "连接" in out:
                    msg = out.split("\n")[-1].strip()[:80]
                else:
                    msg = self._tr("qq_started")
                self.root.after(0, lambda: self.qq_status_label.config(
                    text=msg, fg=T["green"]))
            else:
                err_msg = err[:80] if err else (out[-80:] if out else "?")
                self.root.after(0, lambda: self.qq_status_label.config(
                    text=self._tr("qq_failed", msg=err_msg), fg=T["red"]))
        self.qq_status_label.config(text=self._tr("qq_starting"), fg=T["yellow"])
        threading.Thread(target=do_start, daemon=True).start()

    def _stop_qq_bot(self):
        if not self.workspace:
            return
        def do_stop():
            out, err, rc = run_silent(
                [sys.executable, "-m", "partner.cli", "bot", "stop", "qq"],
                timeout=10
            )
            self.root.after(0, lambda: self.qq_status_label.config(
                text=self._tr("qq_stopped"), fg=T["txt3"]))
        threading.Thread(target=do_stop, daemon=True).start()

    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    #  Logs Tab
    # ── ── ── ── ── ── ── ── ── ── ── ── ── ──
    def _build_tab_logs(self):
        f = self._tab_contents[3]
        f.configure(bg=T["bg"])

        log_card = AccentCard(f, title=self._tr("logs_title"),
                              accent_color=T["yellow"])
        log_card.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 8))

        self.log_text = scrolledtext.ScrolledText(log_card.body,
            font=(FONT_MONO[0], 9), bg=T["bg2"], fg=T["txt"],
            relief=tk.FLAT, padx=14, pady=12, borderwidth=0,
            highlightbackground=T["border"], highlightthickness=1,
            insertbackground=T["txt"],
            selectbackground=T["accent"], selectforeground="white")
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=18, pady=(4, 10))

        bf = tk.Frame(f, bg=T["bg"])
        bf.pack(fill=tk.X, padx=0, pady=(0, 0))
        Btn(bf, text=self._tr("btn_reload"),
            command=self._reload_logs).pack(side=tk.LEFT)

    def _reload_logs(self):
        self.log_text.delete(1.0, tk.END)
        if not self.workspace:
            self.log_text.insert(tk.END, self._tr("logs_no_ws"))
            return
        log_dir = os.path.join(self.workspace, "logs")
        if not os.path.exists(log_dir):
            self.log_text.insert(tk.END, self._tr("logs_no_dir"))
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
        self.dot_canvas.itemconfig(self.dot_id, fill=color)

    def _set_status(self, text, color=None):
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete(1.0, tk.END)
        color = color or T["txt3"]
        self.status_text.tag_configure("ok", foreground=T["green"])
        self.status_text.tag_configure("fail", foreground=T["red"])
        self.status_text.tag_configure("highlight", foreground=T["accent2"])
        self.status_text.tag_configure("dim", foreground=T["txt3"])
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
            elif self._tr("status_workspace") in line and ":" in line:
                parts = line.split(":", 1)
                self.status_text.insert(tk.END, f"{parts[0]}: ", "dim")
                self.status_text.insert(tk.END, parts[1].strip(), "highlight")
            elif "Active Plan:" in line or "Status:" in line or "Goal:" in line:
                label, val = line.split(":", 1)
                self.status_text.insert(tk.END, f"{label}:", "dim")
                self.status_text.insert(tk.END, f"{val}")
            else:
                self.status_text.insert(tk.END, line)
            if not is_last:
                self.status_text.insert(tk.END, "\n")
        self.status_text.config(state=tk.DISABLED)
        self._set_dot(color)
        self.hdr_status.config(text="")

    def _set_status_bar(self, text):
        self.status_bar.config(text=text)

    def _refresh_status(self):
        self._set_status(self._tr("status_loading"), T["yellow"])
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _do_refresh(self):
        if not self.workspace:
            self.root.after(0, lambda: self._set_status(
                self._tr("status_no_workspace"), T["yellow"]))
            return

        ws_label = self._tr("status_workspace")
        lines = [f"{ws_label}: {self.workspace}", ""]
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
            self._tr("last_update", time=now)))

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
        win.title(self._tr("setup_title"))
        win.configure(bg=T["bg"])
        win.transient(self.root)
        win.grab_set()
        win.resizable(True, True)
        center_window(win, 600, 680)

        tk.Label(win, text=self._tr("setup_title"), bg=T["bg"], fg=T["accent2"],
                 font=(FONT[0], 20, "bold")).pack(pady=(20, 2))
        tk.Label(win, text=self._tr("setup_sub"), bg=T["bg"],
                 fg=T["txt3"], font=(FONT[0], 10)).pack(pady=(0, 20))

        # Step 1: Workspace
        s1 = AccentCard(win, title=self._tr("setup_step1"), accent_color=T["green"])
        s1.pack(fill=tk.X, padx=28, pady=(0, 12))
        ws_var = tk.StringVar(value=self.workspace or os.path.expanduser("~/partner_workspace"))
        ws_row = tk.Frame(s1.body, bg=T["card"])
        ws_row.pack(fill=tk.X, padx=18, pady=(4, 14))
        ws_entry = Input(ws_row, textvariable=ws_var)
        ws_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        Btn(ws_row, text=self._tr("setup_browse"), command=lambda: ws_var.set(
            filedialog.askdirectory(title=self._tr("setup_title")) or ws_var.get())
            ).pack(side=tk.LEFT, padx=(10, 0))

        # Step 2: Backend
        s2 = AccentCard(win, title=self._tr("setup_step2"), accent_color=T["blue"])
        s2.pack(fill=tk.X, padx=28, pady=(0, 12))
        backend_var = tk.StringVar(value="hermes")
        for val, label in [("hermes", self._tr("setup_hermes")),
                          ("skip", self._tr("setup_skip"))]:
            tk.Radiobutton(s2.body, text=label, variable=backend_var, value=val,
                          bg=T["card"], fg=T["txt"], selectcolor=T["input_bg"],
                          activebackground=T["card"], activeforeground=T["accent2"],
                          font=(FONT[0], 10)).pack(anchor=tk.W, padx=24, pady=3)

        # Step 3: QQ Bot
        s3 = AccentCard(win, title=self._tr("setup_step3"), accent_color=T["pink"])
        s3.pack(fill=tk.X, padx=28, pady=(0, 12))
        tk.Label(s3.body, text=self._tr("setup_qq_hint"),
                 bg=T["card"], fg=T["txt3"], font=(FONT[0], 9)).pack(anchor=tk.W, padx=18)
        qf = tk.Frame(s3.body, bg=T["card"])
        qf.pack(fill=tk.X, padx=18, pady=(8, 6))
        tk.Label(qf, text=self._tr("setup_qq_id"), bg=T["card"], fg=T["txt2"],
                 width=8, anchor=tk.W).pack(side=tk.LEFT)
        setup_qq_id = Input(qf)
        setup_qq_id.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        qf2 = tk.Frame(s3.body, bg=T["card"])
        qf2.pack(fill=tk.X, padx=18, pady=(0, 14))
        tk.Label(qf2, text=self._tr("setup_qq_pw"), bg=T["card"], fg=T["txt2"],
                 width=8, anchor=tk.W).pack(side=tk.LEFT)
        setup_qq_pw = Input(qf2)
        setup_qq_pw.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        # Status
        status_var = tk.StringVar(value=self._tr("setup_ready"))
        tk.Label(win, textvariable=status_var, bg=T["bg"], fg=T["txt3"],
                 font=(FONT[0], 10)).pack(pady=(4, 0))

        def do_setup():
            ws = ws_var.get().strip()
            if not ws:
                messagebox.showerror("Error", self._tr("setup_select_ws"))
                return
            os.makedirs(ws, exist_ok=True)
            status_var.set(self._tr("setup_creating"))
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

            if backend_var.get() == "hermes":
                status_var.set(self._tr("setup_installing"))
                win.update()
                run_silent([sys.executable, "-m", "pip", "install", "hermes-agent"], timeout=120)

            status_var.set(self._tr("setup_complete"))
            self.workspace = ws
            win.after(600, win.destroy)
            self._rebuild_ui()

        Btn(win, text=self._tr("setup_start"), bg=T["accent3"], fg="white",
            hover_bg=T["accent"], command=do_setup).pack(pady=22)

    def run(self):
        self.root.mainloop()


def main():
    root = tk.Tk()
    app = PartnerApp(root)
    app.run()


if __name__ == "__main__":
    main()
