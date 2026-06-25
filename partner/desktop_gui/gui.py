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
import webbrowser
import ctypes
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
        "no_agent_title": "⚠  当前没有可用的 AI 引擎",
        "no_agent_desc": "对话需要 Hermes Agent 后端支持。\n请安装后再试。",
        "btn_install_agent": "  📥  安装 Hermes Agent  ",
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
        "lang_toggle": "EN",
        "setup_title": "连接 Partner",
        "setup_sub": "选择本机工作区，或接入 Linux / WSL 上正在运行的 Partner。",
        "chat_remote_readonly": "当前连接的是 Linux / WSL 端 Partner 工作区。Windows 桌面端暂时只提供查看，不直接接管聊天与运行。",
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
        "lang_toggle": "中文",
        "setup_title": "Connect Partner",
        "setup_sub": "Choose a local workspace or connect to a Partner already running on Linux / WSL.",
        "chat_remote_readonly": "This window is connected to a Linux / WSL Partner workspace. The Windows desktop app is currently view-only for status and records.",
    },
}


def tr(key, lang="zh", **kw):
    val = L.get(lang, L["zh"]).get(key, key)
    if kw:
        return val.format(**kw)
    return val


# ── Modern Theme ──
T = {
    "bg":        "#0a0d12",
    "bg2":       "#10151d",
    "bg3":       "#171e28",
    "card":      "#121923",
    "card_hl":   "#1d2735",
    "accent":    "#69b1ff",
    "accent2":   "#9bc8ff",
    "accent3":   "#2f7cf6",
    "accent_h":  "#8cc2ff",
    "green":     "#44c38a",
    "yellow":    "#f0b35a",
    "red":       "#ff6b6b",
    "blue":      "#69b1ff",
    "pink":      "#ff8cb3",
    "txt":       "#eef4ff",
    "txt2":      "#99a7bf",
    "txt3":      "#627086",
    "border":    "#1e2937",
    "input_bg":  "#0c1219",
    "chat_user": "#2f7cf6",
    "chat_bot":  "#182230",
    "glow":      "#0c1219",
    "side":      "#0f141d",
}

FONT = ("Segoe UI Variable Text", "Microsoft YaHei UI", "Segoe UI", "TkDefaultFont")
FONT_MONO = ("Cascadia Mono", "Cascadia Code", "Consolas", "monospace")
FONT_HEADING = ("Segoe UI Variable Display", "Microsoft YaHei UI", "Segoe UI", "TkDefaultFont")

FONT_SIZE_BASE = 12
FONT_SIZE_SMALL = 10
FONT_SIZE_BODY = 13
FONT_SIZE_TITLE = 16
FONT_SIZE_HERO = 28


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
    """Load dialog history from daily .log files (new format) or legacy dialog_history.jsonl."""
    # New format: instances/<id>/dialogue/YYYY-MM-DD.log
    for instance_dir in sorted(Path(workspace).glob("instances/*/dialogue"), reverse=True):
        log_files = sorted(instance_dir.glob("*.log"), reverse=True)
        if log_files:
            turns = []
            for log_file in log_files[:3]:  # last 3 days
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                # Parse both Q: (user) and standalone A: (partner) messages
                for line in lines:
                    if line.startswith("  Q: "):
                        turns.append({
                            "timestamp": log_file.stem,
                            "role": "user",
                            "content": line[4:].strip(),
                        })
                    elif line.startswith("  A: "):
                        turns.append({
                            "timestamp": log_file.stem,
                            "role": "assistant",
                            "content": line[4:].strip(),
                        })
            if turns:
                return turns[-n*2:]
    # Fallback: old format state/dialog_history.jsonl
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


def setup_dpi_awareness(root=None):
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    if root is not None:
        try:
            dpi = root.winfo_fpixels("1i")
            root.tk.call("tk", "scaling", max(1.6, dpi / 72.0))
        except Exception:
            try:
                root.tk.call("tk", "scaling", 1.75)
            except Exception:
                pass


def is_wsl_unc_path(path):
    if not path:
        return False
    norm = str(path).replace("/", "\\")
    return norm.startswith("\\\\wsl$\\")


def detect_wsl_distros():
    if os.name != "nt":
        return []
    try:
        result = subprocess.run(
            ["wsl.exe", "-l", "-q"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=CREATION_FLAGS,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def linux_path_to_unc(linux_path, distro_name):
    if not linux_path or not distro_name:
        return ""
    clean = linux_path.strip().replace("/", "\\").lstrip("\\")
    return f"\\\\wsl$\\{distro_name}\\{clean}"


def save_gui_bridge_settings(data):
    path = os.path.expanduser("~/.partner_gui_bridge.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_gui_bridge_settings():
    path = os.path.expanduser("~/.partner_gui_bridge.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_json_file(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def read_text_file(path):
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def count_jsonl_lines(path):
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def format_relative_time(ts):
    dt = parse_iso(ts)
    if not dt:
        return "-"
    delta = datetime.now(dt.tzinfo) - dt if dt.tzinfo else datetime.now() - dt
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    days = hours // 24
    return f"{days} 天前"


def format_duration(ts):
    dt = parse_iso(ts)
    if not dt:
        return "-"
    delta = datetime.now(dt.tzinfo) - dt if dt.tzinfo else datetime.now() - dt
    total_hours = max(0, int(delta.total_seconds() // 3600))
    days, hours = divmod(total_hours, 24)
    if days > 0:
        return f"{days}天 {hours}小时"
    if hours > 0:
        return f"{hours}小时"
    minutes = max(1, int(delta.total_seconds() // 60))
    return f"{minutes}分钟"


def format_tokens(value):
    if not value:
        return "0"
    if value >= 1000000:
        return f"{value / 1000000:.1f}M"
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


def summarize_markdown(md_text):
    if not md_text:
        return ""
    lines = []
    for raw in md_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            line = line[2:]
        lines.append(line)
    return lines[0][:140] if lines else ""


def read_token_usage(instance_dir):
    total = 0
    today = 0
    csv_path = os.path.join(instance_dir, "projects", "metrics", "token_usage.csv")
    if os.path.exists(csv_path):
        try:
            import csv
            today_key = datetime.now().date().isoformat()
            with open(csv_path, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    tokens = int(row.get("total_tokens", 0) or 0)
                    total += tokens
                    if (row.get("timestamp", "") or "").startswith(today_key):
                        today += tokens
        except Exception:
            total = 0
            today = 0
    if total:
        return total, today

    log_path = os.path.join(instance_dir, "logs", "hermes_chat.jsonl")
    if not os.path.exists(log_path):
        return 0, 0
    today_key = datetime.now().date().isoformat()
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                tokens = int(row.get("total_tokens_est") or 0)
                total += tokens
                if (row.get("ts", "") or "").startswith(today_key):
                    today += tokens
    except Exception:
        return 0, 0
    return total, today


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
                     font=(FONT_HEADING[0], FONT_SIZE_TITLE, "bold"), anchor=tk.W).pack(side=tk.LEFT)
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
                               font=(FONT[0], FONT_SIZE_BASE), cursor="hand2", padx=18, pady=10)
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
        setup_dpi_awareness(self.root)
        self.root.title(tr("app_title", self._lang))
        self.root.minsize(1180, 820)
        self.root.configure(bg=T["bg"])
        center_window(self.root, 1360, 920)
        try:
            self.root.option_add("*Font", f"{{{FONT[0]}}} {FONT_SIZE_BASE}")
        except Exception:
            pass

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
        self.bridge_settings = load_gui_bridge_settings()
        self.workspace_mode = "wsl" if is_wsl_unc_path(self.workspace) else "local"
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

        shell = tk.Frame(main, bg=T["bg"])
        shell.pack(fill=tk.BOTH, expand=True)

        side = tk.Frame(shell, bg=T["side"], width=250, highlightbackground=T["border"], highlightthickness=1)
        side.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 16))
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=T["side"])
        brand.pack(fill=tk.X, padx=18, pady=(18, 18))
        badge = tk.Canvas(brand, width=40, height=40, bg=T["side"], highlightthickness=0)
        badge.pack(side=tk.LEFT, padx=(0, 12))
        badge.create_oval(3, 3, 37, 37, fill=T["accent3"], outline="")
        badge.create_text(20, 20, text="P", fill="white", font=(FONT_HEADING[0], 18, "bold"))
        title_wrap = tk.Frame(brand, bg=T["side"])
        title_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(title_wrap, text="Partner", bg=T["side"], fg=T["txt"],
                 font=(FONT_HEADING[0], 24, "bold"), anchor=tk.W).pack(anchor=tk.W)
        mode_text = "Linux / WSL 已连接" if self.workspace_mode == "wsl" else "Windows 本地工作区"
        tk.Label(title_wrap, text=mode_text, bg=T["side"], fg=T["txt2"],
                 font=(FONT[0], FONT_SIZE_SMALL), anchor=tk.W).pack(anchor=tk.W, pady=(2, 0))

        side_status = tk.Frame(side, bg=T["bg3"])
        side_status.pack(fill=tk.X, padx=18, pady=(0, 18))
        top_line = tk.Frame(side_status, bg=T["bg3"])
        top_line.pack(fill=tk.X, padx=12, pady=(10, 4))
        self.dot_canvas = tk.Canvas(top_line, width=12, height=12, bg=T["bg3"], highlightthickness=0)
        self.dot_canvas.pack(side=tk.LEFT, padx=(0, 8))
        self.dot_id = self.dot_canvas.create_oval(1, 1, 11, 11, fill=T["txt3"], outline="", width=0)
        self.hdr_status = tk.Label(top_line, text="", bg=T["bg3"], fg=T["txt2"], font=(FONT[0], FONT_SIZE_BASE))
        self.hdr_status.pack(side=tk.LEFT)

        self._tab_frame = tk.Frame(side, bg=T["side"])
        self._tab_frame.pack(fill=tk.X, padx=12, pady=(0, 8))

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
            tab_bg = T["card_hl"] if is_active else T["side"]
            tab_fg = T["txt"] if is_active else T["txt2"]
            btn = tk.Frame(self._tab_frame, bg=tab_bg, cursor="hand2")
            btn.pack(fill=tk.X, pady=4)
            label = tk.Label(btn, text=f"  {icon}  {name}",
                             bg=tab_bg, fg=tab_fg,
                             font=(FONT[0], 13, "bold" if is_active else "normal"),
                             padx=18, pady=14, anchor=tk.W)
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
                                c.configure(bg=T["bg3"])
                        e.widget.configure(bg=T["bg3"])
                def on_leave(e):
                    if i != self._active_tab:
                        for c in e.widget.winfo_children():
                            if isinstance(c, tk.Label):
                                c.configure(bg=T["side"])
                        e.widget.configure(bg=T["side"])
                return on_enter, on_leave
            enter_fn, leave_fn = make_hover(idx)
            btn.bind("<Enter>", enter_fn)
            btn.bind("<Leave>", leave_fn)
            label.bind("<Enter>", enter_fn)
            label.bind("<Leave>", leave_fn)

            self._tab_buttons.append(btn)

        side_footer = tk.Frame(side, bg=T["side"])
        side_footer.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=18)
        lang_text = tr("lang_toggle", self._lang)
        self.lang_btn = tk.Label(side_footer, text=lang_text, bg=T["bg3"], fg=T["txt2"],
                                 font=(FONT[0], FONT_SIZE_BASE), cursor="hand2", padx=14, pady=8)
        self.lang_btn.pack(side=tk.LEFT)
        self.lang_btn.bind("<Button-1>", lambda e: self._toggle_lang())
        self.lang_btn.bind("<Enter>", lambda e: self.lang_btn.configure(bg=T["card_hl"]))
        self.lang_btn.bind("<Leave>", lambda e: self.lang_btn.configure(bg=T["bg3"]))

        action_btn = Btn(side_footer, text="连接设置", command=self._show_setup)
        action_btn.pack(side=tk.RIGHT)

        content_shell = tk.Frame(shell, bg=T["bg"])
        content_shell.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        topbar = tk.Frame(content_shell, bg=T["bg"])
        topbar.pack(fill=tk.X, pady=(0, 12))
        tk.Label(topbar, text="Desktop Control Surface", bg=T["bg"], fg=T["txt3"],
                 font=(FONT[0], FONT_SIZE_BASE)).pack(anchor=tk.W)
        self.page_title = tk.Label(topbar, text=self._tabs[0][0], bg=T["bg"], fg=T["txt"],
                                   font=(FONT_HEADING[0], FONT_SIZE_HERO, "bold"))
        self.page_title.pack(anchor=tk.W, pady=(4, 0))

        content_host = tk.Frame(content_shell, bg=T["bg"])
        content_host.pack(fill=tk.BOTH, expand=True)

        for idx, _ in enumerate(self._tabs):
            content = tk.Frame(content_host, bg=T["bg"])
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
            bg = T["card_hl"] if i == idx else T["side"]
            fg = T["txt"] if i == idx else T["txt2"]
            for child in btn.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=bg, fg=fg, font=(FONT[0], 13, "bold" if i == idx else "normal"))
            btn.configure(bg=bg)
        self._active_tab = idx
        if hasattr(self, "page_title"):
            self.page_title.config(text=self._tabs[idx][0])
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

    def _metric_pill(self, parent, label, value, accent=None):
        pill = tk.Frame(parent, bg=T["bg3"], padx=10, pady=8)
        tk.Label(pill, text=label, bg=T["bg3"], fg=T["txt2"],
                 font=(FONT[0], 9)).pack(anchor=tk.W)
        tk.Label(pill, text=value, bg=T["bg3"], fg=accent or T["txt"],
                 font=(FONT_HEADING[0], 12, "bold")).pack(anchor=tk.W, pady=(2, 0))
        return pill

    def _collect_dashboard_snapshot(self):
        ws = self.workspace
        snapshot = {
            "workspace": ws,
            "workspace_summary": load_json_file(os.path.join(ws, "state", "stats.json")) if ws else {},
            "global_config": load_json_file(os.path.join(ws, "global_config.json")) if ws else {},
            "instances": [],
            "alerts": [],
            "source": "wsl" if is_wsl_unc_path(ws) else "local",
        }

        from partner.adapter import HermesAdapter as _HA
        hermes = _HA.detect_installation()
        snapshot["hermes"] = hermes
        if not hermes["available"]:
            snapshot["alerts"].append(("error", "未检测到 Hermes，聊天和自动研究无法启动。"))
        elif hermes["issues"]:
            snapshot["alerts"].append(("warn", "Hermes 已找到，但配置不完整，部分功能可能不可用。"))

        instances_cfg = (snapshot["global_config"].get("instances") or {}) if ws else {}
        if instances_cfg:
            for instance_id, cfg in sorted(instances_cfg.items()):
                instance_dir = cfg.get("working_dir") or os.path.join(ws, "instances", instance_id)
                snapshot["instances"].append(self._collect_instance_snapshot(instance_id, instance_dir, cfg))
        elif ws:
            snapshot["instances"].append(self._collect_instance_snapshot("default", ws, {}))

        if not snapshot["instances"]:
            snapshot["alerts"].append(("warn", "当前还没有可展示的 Partner 实例。"))
        if snapshot["source"] == "wsl":
            snapshot["alerts"].append(("warn", "当前是 Linux / WSL 工作区连接模式。Windows 端主要用于查看状态、日志和项目进展。"))
        return snapshot

    def _collect_instance_snapshot(self, instance_id, instance_dir, cfg):
        plan = load_json_file(os.path.join(instance_dir, "state", "active_plan.json"))
        heartbeat = load_json_file(os.path.join(instance_dir, "state", "heartbeat.json"))
        active_project = load_json_file(os.path.join(instance_dir, "projects", "active_project.json"))
        knowledge = load_json_file(os.path.join(instance_dir, "state", "knowledge.json"))
        summary_md = read_text_file(os.path.join(instance_dir, "state", "user", "current_project", "summary.md"))
        journal_count = count_jsonl_lines(os.path.join(instance_dir, "state", "journal.jsonl"))
        token_total, token_today = read_token_usage(instance_dir)

        phases = plan.get("phases") or []
        completed = sum(1 for p in phases if p.get("status") == "completed")
        total_phases = len(phases)
        focus = (
            active_project.get("project_name")
            or plan.get("title")
            or plan.get("goal")
            or "尚未明确研究方向"
        )
        current_action = (
            plan.get("heartbeat_summary")
            or active_project.get("current_phase")
            or summarize_markdown(summary_md)
            or "等待下一步指令"
        )
        knowledge_entries = (knowledge.get("meta") or {}).get("total_entries")
        if knowledge_entries is None:
            knowledge_entries = len(knowledge.get("entries") or [])

        status = heartbeat.get("status") or plan.get("status") or "idle"
        status_map = {
            "alive": ("在线", T["green"]),
            "working": ("执行中", T["green"]),
            "active": ("推进中", T["green"]),
            "planning": ("规划中", T["yellow"]),
            "completed": ("已完成", T["accent2"]),
            "idle": ("空闲", T["txt2"]),
        }
        status_text, status_color = status_map.get(status, (status, T["txt2"]))

        if knowledge_entries >= 8 or completed >= 4:
            growth = "成长快，已形成稳定经验"
        elif knowledge_entries >= 3 or completed >= 2:
            growth = "持续积累中"
        elif journal_count > 0:
            growth = "刚起步，已有探索痕迹"
        else:
            growth = "尚未形成经验沉淀"

        return {
            "id": instance_id,
            "dir": instance_dir,
            "backend": cfg.get("agent_backend", "hermes"),
            "enabled": cfg.get("enabled", True),
            "interval_minutes": cfg.get("interval_minutes", 30),
            "focus": focus,
            "status_text": status_text,
            "status_color": status_color,
            "current_action": current_action,
            "plan_title": plan.get("title") or "",
            "last_heartbeat": heartbeat.get("last_heartbeat") or plan.get("last_heartbeat") or "",
            "run_duration": format_duration(plan.get("created_at") or heartbeat.get("last_heartbeat")),
            "last_seen": format_relative_time(heartbeat.get("last_heartbeat") or plan.get("last_heartbeat")),
            "cycle_count": heartbeat.get("cycle_count") or 0,
            "crash_count": heartbeat.get("crash_count") or 0,
            "progress_text": f"{completed}/{total_phases} 阶段" if total_phases else ("已完成" if plan.get("status") == "completed" else "未拆分阶段"),
            "progress_pct": int((completed / total_phases) * 100) if total_phases else (100 if plan.get("status") == "completed" else 0),
            "knowledge_entries": knowledge_entries,
            "journal_count": journal_count,
            "growth": growth,
            "token_total": token_total,
            "token_today": token_today,
            "summary": summarize_markdown(summary_md),
        }

    def _refresh_dashboard(self):
        self._set_dot(T["yellow"])
        self.hdr_status.config(text="刷新中…")
        threading.Thread(target=self._do_refresh_dash, daemon=True).start()

    def _do_refresh_dash(self):
        snapshot = self._collect_dashboard_snapshot()

        def _update():
            for w in self._dash_inner.winfo_children():
                w.destroy()

            instances = snapshot["instances"]
            total_instances = len(instances)
            active_instances = sum(1 for item in instances if item["status_color"] == T["green"])
            total_tokens = sum(item["token_total"] for item in instances)
            today_tokens = sum(item["token_today"] for item in instances)

            hero = tk.Frame(self._dash_inner, bg=T["accent3"], padx=18, pady=18)
            hero.pack(fill=tk.X, pady=(0, 10))
            tk.Label(hero, text="研究伙伴总览", bg=T["accent3"], fg="white",
                     font=(FONT_HEADING[0], 18, "bold")).pack(anchor=tk.W)
            tk.Label(
                hero,
                text="只展示用户真正关心的内容：配置了多少个 partner、分别在做什么、推进到哪一步、近期是否有异常。",
                bg=T["accent3"],
                fg="#dbeafe",
                font=(FONT[0], 10),
                wraplength=900,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(6, 14))
            hero_metrics = tk.Frame(hero, bg=T["accent3"])
            hero_metrics.pack(fill=tk.X)
            self._metric_pill(hero_metrics, "已配置 Partner", str(total_instances), "#ffffff").pack(side=tk.LEFT, padx=(0, 10))
            self._metric_pill(hero_metrics, "当前活跃", str(active_instances), "#9ae6b4").pack(side=tk.LEFT, padx=(0, 10))
            self._metric_pill(hero_metrics, "累计 Token", format_tokens(total_tokens), "#fde68a").pack(side=tk.LEFT, padx=(0, 10))
            self._metric_pill(hero_metrics, "今日 Token", format_tokens(today_tokens), "#fbcfe8").pack(side=tk.LEFT)

            sys_card = tk.Frame(self._dash_inner, bg=T["card"],
                                highlightbackground=T["border"], highlightthickness=1)
            sys_card.pack(fill=tk.X, pady=(0, 10))
            tk.Label(sys_card, text="系统健康", bg=T["card"], fg=T["accent2"],
                     font=(FONT_HEADING[0], 12, "bold")).pack(anchor=tk.W, padx=14, pady=(12, 8))
            hermes = snapshot["hermes"]
            hermes_status = "已检测到" if hermes["available"] else "未检测到"
            hermes_color = T["green"] if hermes["available"] else T["red"]
            rows = [
                ("Hermes", hermes_status, hermes_color),
                ("来源", "Linux / WSL" if snapshot["source"] == "wsl" else "Windows 本地", T["txt"]),
                ("执行文件", hermes.get("executable") or "未找到", T["txt2"]),
                ("配置文件", hermes.get("config_path") or "未找到", T["txt2"]),
                ("工作区", (snapshot["workspace"] or "-").replace("/", "\\"), T["accent2"]),
            ]
            for label, value, color in rows:
                row = tk.Frame(sys_card, bg=T["card"])
                row.pack(fill=tk.X, padx=14, pady=2)
                tk.Label(row, text=label, bg=T["card"], fg=T["txt2"],
                         font=(FONT[0], 10), width=8, anchor=tk.W).pack(side=tk.LEFT)
                tk.Label(row, text=value, bg=T["card"], fg=color,
                         font=(FONT_MONO[0], 9) if label != "Hermes" else (FONT[0], 10, "bold"),
                         anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)
            if snapshot["alerts"]:
                for level, text in snapshot["alerts"]:
                    bg = "#2b1d1d" if level == "error" else "#2d2617"
                    fg = T["red"] if level == "error" else T["yellow"]
                    warn = tk.Frame(sys_card, bg=bg)
                    warn.pack(fill=tk.X, padx=14, pady=(8, 0))
                    tk.Label(warn, text=text, bg=bg, fg=fg, font=(FONT[0], 10),
                             wraplength=860, justify=tk.LEFT).pack(anchor=tk.W, padx=12, pady=10)

            for item in instances:
                card = tk.Frame(self._dash_inner, bg=T["card"],
                                highlightbackground=T["border"], highlightthickness=1)
                card.pack(fill=tk.X, pady=(0, 10))

                hdr = tk.Frame(card, bg=T["card"])
                hdr.pack(fill=tk.X, padx=14, pady=(12, 6))
                tk.Label(hdr, text=f"Partner {item['id']}", bg=T["card"], fg=T["txt"],
                         font=(FONT_HEADING[0], 14, "bold")).pack(side=tk.LEFT)
                tk.Label(hdr, text=item["status_text"], bg=T["card"], fg=item["status_color"],
                         font=(FONT[0], 10, "bold"), padx=10, pady=3).pack(side=tk.RIGHT)

                tk.Label(card, text=item["focus"], bg=T["card"], fg=T["accent2"],
                         font=(FONT[0], 11, "bold"), wraplength=900, justify=tk.LEFT).pack(
                             anchor=tk.W, padx=14)
                tk.Label(card, text=item["current_action"], bg=T["card"], fg=T["txt"],
                         font=(FONT[0], 10), wraplength=900, justify=tk.LEFT).pack(
                             anchor=tk.W, padx=14, pady=(6, 10))

                metrics = tk.Frame(card, bg=T["card"])
                metrics.pack(fill=tk.X, padx=14)
                self._metric_pill(metrics, "运行时长", item["run_duration"], item["status_color"]).pack(side=tk.LEFT, padx=(0, 8))
                self._metric_pill(metrics, "最近心跳", item["last_seen"], T["txt"]).pack(side=tk.LEFT, padx=(0, 8))
                self._metric_pill(metrics, "Cycles", str(item["cycle_count"]), T["txt"]).pack(side=tk.LEFT, padx=(0, 8))
                self._metric_pill(metrics, "今日 Token", format_tokens(item["token_today"]), T["yellow"]).pack(side=tk.LEFT, padx=(0, 8))
                self._metric_pill(metrics, "累计 Token", format_tokens(item["token_total"]), T["accent2"]).pack(side=tk.LEFT)

                prog = tk.Frame(card, bg=T["card"])
                prog.pack(fill=tk.X, padx=14, pady=(10, 4))
                tk.Label(prog, text="项目推进", bg=T["card"], fg=T["txt2"],
                         font=(FONT[0], 10)).pack(side=tk.LEFT)
                tk.Label(prog, text=item["progress_text"], bg=T["card"], fg=T["txt"],
                         font=(FONT[0], 10, "bold")).pack(side=tk.LEFT, padx=(8, 0))
                bar_wrap = tk.Frame(card, bg=T["bg3"], height=8)
                bar_wrap.pack(fill=tk.X, padx=14)
                bar_wrap.pack_propagate(False)
                bar = tk.Frame(bar_wrap, bg=item["status_color"], width=max(24, int(8.6 * item["progress_pct"])))
                bar.pack(side=tk.LEFT, fill=tk.Y)

                foot = tk.Frame(card, bg=T["card"])
                foot.pack(fill=tk.X, padx=14, pady=(10, 12))
                left = tk.Frame(foot, bg=T["card"])
                left.pack(side=tk.LEFT, fill=tk.X, expand=True)
                tk.Label(left, text=f"学习/成长：{item['growth']}", bg=T["card"], fg=T["green"],
                         font=(FONT[0], 10)).pack(anchor=tk.W)
                tk.Label(left, text=f"经验条目 {item['knowledge_entries']} · 探索记录 {item['journal_count']} · 崩溃 {item['crash_count']}",
                         bg=T["card"], fg=T["txt2"], font=(FONT[0], 9)).pack(anchor=tk.W, pady=(4, 0))
                if item["summary"]:
                    tk.Label(left, text=f"项目摘要：{item['summary']}", bg=T["card"], fg=T["txt2"],
                             font=(FONT[0], 9), wraplength=860, justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))

            dot_color = T["green"] if hermes["available"] and active_instances else (T["yellow"] if hermes["available"] else T["red"])
            self._set_dot(dot_color)
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

    def _add_no_agent_message(self):
        """Show a warning bubble that Hermes Agent is not installed, with an install button."""
        from partner.adapter import HermesAdapter as _HA
        diag = _HA.detect_installation()
        wrapper = tk.Frame(self.chat_inner, bg=T["bg2"])
        wrapper.pack(fill=tk.X, padx=0, pady=3)

        bubble = tk.Frame(wrapper, bg=T["yellow"], padx=0, pady=0,
                          highlightbackground=T["glow"], highlightthickness=0)
        bubble.pack(anchor=tk.W, padx=(12, 60))

        # Title bar
        hdr = tk.Frame(bubble, bg=T["yellow"])
        hdr.pack(fill=tk.X, padx=14, pady=(10, 2))
        tk.Label(hdr, text="Partner", bg=T["yellow"], fg="#0d1117",
                 font=(FONT[0], 10, "bold"), anchor=tk.W).pack(side=tk.LEFT)

        # Warning text
        title_label = tk.Label(bubble, text=self._tr("no_agent_title"),
                               bg=T["yellow"], fg="#0d1117",
                               font=(FONT[0], 11, "bold"), wraplength=450,
                               justify=tk.LEFT, anchor=tk.W)
        title_label.pack(anchor=tk.W, padx=14, pady=(2, 0))

        detail = ""
        if diag.get("issues"):
            detail = "\n\n" + "；".join(diag["issues"])
        elif diag.get("executable"):
            detail = f"\n\n已找到: {diag['executable']}"
        desc_label = tk.Label(bubble, text=self._tr("no_agent_desc") + detail,
                              bg=T["yellow"], fg="#0d1117",
                              font=(FONT[0], 10), wraplength=450,
                              justify=tk.LEFT, anchor=tk.W)
        desc_label.pack(anchor=tk.W, padx=14, pady=(4, 6))

        # Install button row
        btn_row = tk.Frame(bubble, bg=T["yellow"])
        btn_row.pack(fill=tk.X, padx=14, pady=(0, 10))
        Btn(btn_row, text=self._tr("btn_install_agent"),
            bg=T["accent3"], fg="white", hover_bg=T["accent"],
            command=lambda: webbrowser.open("https://hermes-agent.nousresearch.com/docs")
            ).pack(side=tk.LEFT)

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
        if self.workspace_mode == "wsl":
            self._add_chat_message("bot", self._tr("chat_remote_readonly"))
            return
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

                # No reply from adapter — check if Hermes is installed
                from partner.adapter import HermesAdapter as _HA
                if not _HA.is_available():
                    self.root.after(0, self._add_no_agent_message)
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

        if self.workspace_mode == "wsl":
            card = tk.Frame(f, bg=T["card"], highlightbackground=T["border"], highlightthickness=1)
            card.pack(fill=tk.X, pady=(0, 8))
            tk.Label(card, text="Linux / WSL 运行中的 QQ 机器人", bg=T["card"], fg=T["accent2"],
                     font=(FONT_HEADING[0], 14, "bold")).pack(anchor=tk.W, padx=18, pady=(16, 6))
            tk.Label(card, text="当前连接的是 Linux 端 Partner 工作区。Windows 桌面端不直接启动或停止 QQ 机器人；请在 Linux / WSL 端继续管理运行。",
                     bg=T["card"], fg=T["txt2"], font=(FONT[0], 10), wraplength=780, justify=tk.LEFT).pack(
                         anchor=tk.W, padx=18, pady=(0, 16))
            return

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
        from partner.config import save_partner_config_data
        from partner.setup import save_workspace_pointer

        win = tk.Toplevel(self.root)
        win.title(self._tr("setup_title"))
        win.configure(bg=T["bg"])
        win.transient(self.root)
        win.grab_set()
        win.resizable(True, True)
        center_window(win, 720, 820)

        tk.Label(win, text=self._tr("setup_title"), bg=T["bg"], fg=T["accent2"],
                 font=(FONT_HEADING[0], 22, "bold")).pack(pady=(20, 2))
        tk.Label(win, text=self._tr("setup_sub"), bg=T["bg"],
                 fg=T["txt3"], font=(FONT[0], 10)).pack(pady=(0, 20))

        mode_var = tk.StringVar(value="wsl" if self.workspace_mode == "wsl" else "local")
        current_distro = (self.bridge_settings.get("wsl_distro") or "").strip()
        distros = detect_wsl_distros()
        if not current_distro and distros:
            current_distro = distros[0]
        distro_var = tk.StringVar(value=current_distro)
        linux_path_default = self.bridge_settings.get("linux_workspace") or "/mnt/e/work/partner_workspace"
        linux_path_var = tk.StringVar(value=linux_path_default)
        local_default = self.workspace if self.workspace_mode == "local" and self.workspace else os.path.expanduser("~/partner_workspace")
        ws_var = tk.StringVar(value=local_default)

        s0 = AccentCard(win, title="1. 连接方式", accent_color=T["accent2"])
        s0.pack(fill=tk.X, padx=28, pady=(0, 12))
        mode_wrap = tk.Frame(s0.body, bg=T["card"])
        mode_wrap.pack(fill=tk.X, padx=18, pady=(6, 14))
        for val, label, desc in [
            ("local", "Windows 本地工作区", "这个桌面端直接管理本机上的 Partner"),
            ("wsl", "连接 Linux / WSL 中的 Partner", "读取 Linux / WSL 工作区里的实例状态、项目和日志"),
        ]:
            card = tk.Frame(mode_wrap, bg=T["bg3"], highlightbackground=T["border"], highlightthickness=1)
            card.pack(fill=tk.X, pady=5)
            top = tk.Frame(card, bg=T["bg3"])
            top.pack(fill=tk.X, padx=12, pady=(10, 4))
            tk.Radiobutton(top, text=label, variable=mode_var, value=val,
                           bg=T["bg3"], fg=T["txt"], selectcolor=T["input_bg"],
                           activebackground=T["bg3"], activeforeground=T["accent2"],
                           font=(FONT[0], 10, "bold")).pack(anchor=tk.W)
            tk.Label(card, text=desc, bg=T["bg3"], fg=T["txt2"], font=(FONT[0], 9),
                     wraplength=600, justify=tk.LEFT).pack(anchor=tk.W, padx=16, pady=(0, 12))

        s1 = AccentCard(win, title="2. Windows 本地工作区", accent_color=T["green"])
        s1.pack(fill=tk.X, padx=28, pady=(0, 12))
        ws_row = tk.Frame(s1.body, bg=T["card"])
        ws_row.pack(fill=tk.X, padx=18, pady=(4, 14))
        ws_entry = Input(ws_row, textvariable=ws_var)
        ws_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        Btn(ws_row, text="浏览", command=lambda: ws_var.set(
            filedialog.askdirectory(title=self._tr("setup_title")) or ws_var.get())
            ).pack(side=tk.LEFT, padx=(10, 0))

        s1b = AccentCard(win, title="3. Linux / WSL 工作区", accent_color=T["yellow"])
        s1b.pack(fill=tk.X, padx=28, pady=(0, 12))
        tk.Label(s1b.body, text="推荐直接连接 WSL 中正在运行的 Partner 根目录，例如 `/mnt/e/work/partner_workspace`。Windows 端会转换成 `\\\\wsl$` 路径。", bg=T["card"], fg=T["txt2"], font=(FONT[0], 9), wraplength=620, justify=tk.LEFT).pack(anchor=tk.W, padx=18, pady=(6, 8))
        distro_row = tk.Frame(s1b.body, bg=T["card"])
        distro_row.pack(fill=tk.X, padx=18, pady=(0, 8))
        tk.Label(distro_row, text="WSL 发行版", bg=T["card"], fg=T["txt2"], width=10, anchor=tk.W).pack(side=tk.LEFT)
        distro_entry = Input(distro_row, textvariable=distro_var)
        distro_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        if distros:
            tk.Label(s1b.body, text=f"已检测到: {', '.join(distros[:4])}", bg=T["card"], fg=T["txt3"], font=(FONT[0], 9)).pack(anchor=tk.W, padx=18, pady=(0, 4))
        linux_row = tk.Frame(s1b.body, bg=T["card"])
        linux_row.pack(fill=tk.X, padx=18, pady=(0, 14))
        tk.Label(linux_row, text="Linux 路径", bg=T["card"], fg=T["txt2"], width=10, anchor=tk.W).pack(side=tk.LEFT)
        linux_entry = Input(linux_row, textvariable=linux_path_var)
        linux_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)

        s2 = AccentCard(win, title="4. AI 后端", accent_color=T["blue"])
        s2.pack(fill=tk.X, padx=28, pady=(0, 12))
        backend_var = tk.StringVar(value="hermes")
        for val, label in [("hermes", "🤖  Hermes Agent（推荐）"),
                          ("skip", "⏳  暂不设置")]:
            tk.Radiobutton(s2.body, text=label, variable=backend_var, value=val,
                          bg=T["card"], fg=T["txt"], selectcolor=T["input_bg"],
                          activebackground=T["card"], activeforeground=T["accent2"],
                          font=(FONT[0], 10)).pack(anchor=tk.W, padx=24, pady=3)

        s3 = AccentCard(win, title="5. QQ 机器人（可选）", accent_color=T["pink"])
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
            mode = mode_var.get()
            qq_id = setup_qq_id.get().strip()
            qq_pw = setup_qq_pw.get().strip()
            final_ws = ""

            if mode == "local":
                ws = ws_var.get().strip()
                if not ws:
                    messagebox.showerror("Error", "请选择工作区文件夹")
                    return
                os.makedirs(ws, exist_ok=True)
                status_var.set("正在创建本地工作区…")
                win.update()
                for sub in ["state", "logs", "data", "config"]:
                    os.makedirs(os.path.join(ws, sub), exist_ok=True)
                config = {
                    "workspace": {"path": ws, "readonly_dirs": []},
                    "agent": {"backend": backend_var.get()},
                    "scheduler": {"interval_minutes": 30, "max_tasks_per_cycle": 1, "heartbeat_timeout_minutes": 60},
                    "name": "Partner",
                }
                save_partner_config_data(ws, config)
                if qq_id:
                    qq_cfg = {"mode": "official", "app_id": qq_id, "app_secret": qq_pw}
                    with open(os.path.join(ws, "config", "qq_config.json"), "w", encoding="utf-8") as fh:
                        json.dump(qq_cfg, fh, indent=2, ensure_ascii=False)
                final_ws = ws
            else:
                distro = distro_var.get().strip()
                linux_path = linux_path_var.get().strip()
                if not distro or not linux_path:
                    messagebox.showerror("Error", "请填写 WSL 发行版和 Linux 工作区路径")
                    return
                unc_path = linux_path_to_unc(linux_path, distro)
                if not unc_path:
                    messagebox.showerror("Error", "无法生成 WSL 路径")
                    return
                status_var.set("正在连接 Linux / WSL 工作区…")
                win.update()
                save_gui_bridge_settings({
                    "mode": "wsl",
                    "wsl_distro": distro,
                    "linux_workspace": linux_path,
                    "unc_workspace": unc_path,
                    "saved_at": datetime.now().isoformat(),
                })
                final_ws = unc_path

            save_workspace_pointer(final_ws)
            qq_id = setup_qq_id.get().strip()
            status_var.set("设置完成！")
            self.workspace = final_ws
            self.workspace_mode = "wsl" if mode == "wsl" else "local"
            self.bridge_settings = load_gui_bridge_settings()
            win.after(600, win.destroy)
            self._rebuild_ui()

        Btn(win, text="🚀  开始设置", bg=T["accent3"], fg="white",
            hover_bg=T["accent"], command=do_setup).pack(pady=22)

    def run(self):
        self.root.mainloop()


def main():
    if os.name == "nt":
        try:
            from partner.gui_qt import launch as launch_qt
            raise SystemExit(launch_qt())
        except ImportError:
            pass
    root = tk.Tk()
    app = PartnerApp(root)
    app.run()


if __name__ == "__main__":
    main()
