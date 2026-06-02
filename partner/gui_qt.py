#!/usr/bin/env python3
"""Partner desktop GUI built with PySide6.

This module is intentionally self-contained so Windows can prefer a modern Qt
desktop UI while the older tkinter UI remains available as fallback.
"""

from __future__ import annotations

import csv
import base64
import json
import os
import posixpath
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time
import tempfile
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint, QSize
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QScrollArea,
    QGraphicsDropShadowEffect,
    QInputDialog,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


APP_DIR = os.path.dirname(os.path.abspath(__file__))
PARTNER_DIR = os.path.dirname(APP_DIR)
ICON_DIR = os.path.join(APP_DIR, "assets", "icons")
CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
AUTO_REFRESH_INTERVAL_MS = 15000
CHAT_HISTORY_LIMIT = 30

COLORS = {
    "bg": "#eef3f8",
    "shell": "#f7fafc",
    "panel": "#edf2f7",
    "panel_alt": "#ffffff",
    "muted": "#f3f6fa",
    "border": "#d8e0ea",
    "text": "#16202d",
    "subtext": "#6b7788",
    "dim": "#8a95a5",
    "accent": "#2d6df6",
    "accent_soft": "#6e9cff",
    "green": "#2fa36b",
    "yellow": "#c98a2e",
    "red": "#de5b5b",
    "pink": "#d97098",
}

SVG_ICONS = {
    "dashboard": "dashboard.svg",
    "chat": "chat.svg",
    "instances": "instances.svg",
    "logs": "logs.svg",
    "configured": "configured.svg",
    "active": "active.svg",
    "token": "token.svg",
    "today": "today.svg",
    "growth": "growth.svg",
    "runtime": "runtime.svg",
    "progress": "progress.svg",
    "heartbeat": "heartbeat.svg",
    "knowledge": "knowledge.svg",
    "records": "records.svg",
    "hermes_ok": "hermes_ok.svg",
    "hermes_bad": "hermes_bad.svg",
    "source_local": "source_local.svg",
    "source_wsl": "source_wsl.svg",
    "stage": "stage.svg",
    "instance_dot": "instance_dot.svg",
    "journal": "journal.svg",
    "dialogue": "dialogue.svg",
    "folder": "folder.svg",
    "file": "file.svg",
    "chevron_down": "chevron_down.svg",
}


def icon_path(key: str) -> str:
    return os.path.join(ICON_DIR, SVG_ICONS.get(key, "file.svg"))


def icon_url(key: str) -> str:
    return icon_path(key).replace("\\", "/")


def load_svg_icon(key: str) -> QIcon:
    return QIcon(icon_path(key))


def dialog_stylesheet() -> str:
    return f"""
    QDialog, QWidget {{
        color: {COLORS['text']};
        font-family: 'Segoe UI Variable Text', 'Microsoft YaHei UI', 'Segoe UI';
        font-size: 14px;
    }}
    QFrame#Card, QGroupBox {{
        background: {COLORS['panel_alt']};
        border: 1px solid {COLORS['border']};
        border-radius: 20px;
    }}
    QGroupBox {{
        margin-top: 10px;
        padding-top: 20px;
        font-size: 14px;
        font-weight: 700;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 18px;
        top: 10px;
    }}
    QLabel#Subtle {{
        color: {COLORS['subtext']};
    }}
    QPushButton {{
        background: {COLORS['muted']};
        border: 1px solid {COLORS['border']};
        border-radius: 12px;
        padding: 10px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: #edf3fa;
    }}
    QPushButton#PrimaryAction {{
        background: {COLORS['accent']};
        color: white;
        border-color: #2563eb;
    }}
    QPushButton#PrimaryAction:hover {{
        background: #2563eb;
    }}
    QPushButton#TitleControlClose {{
        background: transparent;
        color: #0f172a;
        border: none;
        border-radius: 0;
        font-size: 18px;
        font-weight: 400;
    }}
    QPushButton#TitleControlClose:hover {{
        background: {COLORS['red']};
        color: white;
    }}
    QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QComboBox {{
        background: {COLORS['muted']};
        border: 1px solid {COLORS['border']};
        border-radius: 12px;
        padding: 10px 12px;
        selection-background-color: {COLORS['accent']};
    }}
    QComboBox {{
        padding-right: 36px;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 32px;
        border: none;
        margin-right: 8px;
    }}
    QComboBox::down-arrow {{
        image: url({icon_url('chevron_down')});
        width: 12px;
        height: 12px;
    }}
    QRadioButton {{
        spacing: 10px;
        font-size: 14px;
    }}
    QRadioButton::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 9px;
        border: 2px solid #bfd0e4;
        background: white;
    }}
    QRadioButton::indicator:checked {{
        border: 5px solid {COLORS['accent']};
        background: white;
    }}
    """

I18N = {
    "zh": {
        "app_title": "Partner",
        "subtitle": "Desktop Research Control Surface",
        "tab_dashboard": "仪表盘",
        "tab_chat": "对话",
        "tab_qq": "实例 / QQ 机器人",
        "tab_logs": "探索记录",
        "mode_local": "Windows 本地工作区",
        "mode_wsl": "Linux / WSL 已连接",
        "mode_ssh": "SSH 服务器已连接",
        "setup_title": "连接 Partner",
        "setup_sub": "选择本机工作区、Linux / WSL，或通过 SSH 连接服务器上已经在运行的 Partner。",
        "chat_remote_readonly": "当前连接的是 Linux / WSL 工作区。Windows 桌面端当前只负责查看，不直接接管对话与运行。",
    },
    "en": {
        "app_title": "Partner",
        "subtitle": "Desktop Research Control Surface",
        "tab_dashboard": "Dashboard",
        "tab_chat": "Chat",
        "tab_qq": "Instances / QQ Bots",
        "tab_logs": "Records",
        "mode_local": "Windows Local Workspace",
        "mode_wsl": "Linux / WSL Connected",
        "mode_ssh": "SSH Server Connected",
        "setup_title": "Connect Partner",
        "setup_sub": "Choose a local workspace, Linux / WSL, or connect to an already running server-side Partner over SSH.",
        "chat_remote_readonly": "This window is attached to a Linux / WSL workspace. The Windows desktop app is view-only for now.",
    },
}


def tr(key: str, lang: str = "zh") -> str:
    return I18N.get(lang, I18N["zh"]).get(key, key)


def load_json_file(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def read_text_file(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def count_jsonl_lines(path: str) -> int:
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def parse_iso(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def format_relative_time(value: str) -> str:
    dt = parse_iso(value)
    if not dt:
        return "-"
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    delta = max(0, int((now - dt).total_seconds()))
    if delta < 60:
        return "刚刚"
    minutes = delta // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    return f"{hours // 24} 天前"


def format_duration(value: str) -> str:
    dt = parse_iso(value)
    if not dt:
        return "-"
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    total_hours = max(0, int((now - dt).total_seconds() // 3600))
    days, hours = divmod(total_hours, 24)
    if days:
        return f"{days}天 {hours}小时"
    if hours:
        return f"{hours}小时"
    minutes = max(1, int((now - dt).total_seconds() // 60))
    return f"{minutes}分钟"


def format_tokens(value: int) -> str:
    if not value:
        return "0"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def summarize_markdown(md_text: str) -> str:
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
    return lines[0][:160] if lines else ""


def _bridge_settings_candidates() -> list[str]:
    paths: list[str] = []
    repo_workspace = os.path.join(os.path.dirname(PARTNER_DIR), "partner_workspace")
    if os.path.isdir(repo_workspace):
        paths.append(os.path.join(repo_workspace, "00_config", "gui_bridge.json"))
    try:
        ws = find_workspace()
    except Exception:
        ws = None
    if ws and os.path.isdir(ws):
        paths.append(os.path.join(ws, "00_config", "gui_bridge.json"))
    paths.append(os.path.expanduser("~/.partner_gui_bridge.json"))
    if os.name != "nt":
        win_home = "/mnt/c/Users/zty12/.partner_gui_bridge.json"
        if os.path.exists(win_home):
            paths.append(win_home)
    dedup = []
    for p in paths:
        if p and p not in dedup:
            dedup.append(p)
    return dedup


def save_gui_bridge_settings(data: dict, workspace_hint: str | None = None):
    targets = []
    if workspace_hint and os.path.isdir(workspace_hint):
        targets.append(os.path.join(workspace_hint, "00_config", "gui_bridge.json"))
    targets.extend(_bridge_settings_candidates())
    written = False
    for path in targets:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            written = True
        except Exception:
            continue
    if not written:
        pass


def load_gui_bridge_settings() -> dict:
    for path in _bridge_settings_candidates():
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return {}


def resolve_initial_workspace(bridge_settings: dict | None) -> tuple[Optional[str], str]:
    settings = bridge_settings or {}
    mode = (settings.get("mode") or "").strip()
    if mode == "ssh":
        return settings.get("ssh_workspace") or "", "ssh"
    ws = find_workspace()
    if is_wsl_unc_path(ws or ""):
        return ws, "wsl"
    return ws, "local"


def ensure_private_key_copy(path: str) -> str:
    if not path:
        return ""
    safe_copy = "/tmp/partner_gui_ssh_key.pem"
    try:
        shutil.copy2(path, safe_copy)
        os.chmod(safe_copy, 0o600)
        return safe_copy
    except Exception:
        return path


def prepare_windows_ssh_key_copy(path: str) -> tuple[bool, str]:
    if not path:
        return False, "SSH key 路径为空。"
    source = wsl_to_windows_path(path)
    key_home = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Partner", "ssh")
    username = os.environ.get("USERNAME", "").strip()
    try:
        os.makedirs(key_home, exist_ok=True)
        with open(source, "rb") as src:
            key_bytes = src.read()
        fd, safe_key = tempfile.mkstemp(prefix="partner_gui_", suffix=".pem", dir=key_home)
        with os.fdopen(fd, "wb") as dst:
            dst.write(key_bytes)
    except Exception as exc:
        return False, f"SSH key 复制失败: {exc}"
    if not username:
        return False, "无法确定 Windows 当前用户名，不能设置 SSH key 权限。"
    acl_steps = [
        ["icacls", safe_key, "/inheritance:r"],
        ["icacls", safe_key, "/grant:r", f"{username}:R"],
        ["icacls", safe_key, "/remove:g", "Everyone", "Users", "Authenticated Users"],
    ]
    for cmd in acl_steps:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                creationflags=CREATION_FLAGS,
                timeout=10,
            )
        except Exception as exc:
            return False, f"设置 SSH key 权限失败: {exc}"
        if result.returncode != 0:
            output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
            return False, f"设置 SSH key 权限失败: {output or 'icacls 返回非零状态'}"
    return True, safe_key


def windows_to_wsl_path(path: str) -> str:
    if not path:
        return ""
    text = str(path).strip().replace("\\", "/")
    if text.startswith("/mnt/"):
        return text
    if len(text) >= 3 and text[1] == ":" and text[2] == "/":
        drive = text[0].lower()
        rest = text[3:]
        return f"/mnt/{drive}/{rest}"
    return text


def wsl_to_windows_path(path: str) -> str:
    if not path:
        return ""
    text = str(path).strip().replace("\\", "/")
    if len(text) >= 3 and text[1] == ":":
        return text.replace("/", "\\")
    if text.startswith("/mnt/") and len(text) > 6:
        drive = text[5].upper()
        rest = text[7:]
        return f"{drive}:\\{rest.replace('/', '\\')}"
    return text.replace("/", "\\")


def remote_path_join(*parts: str) -> str:
    cleaned = []
    for idx, part in enumerate(parts):
        if part is None:
            continue
        text = str(part).replace("\\", "/")
        if idx == 0:
            cleaned.append(text.rstrip("/"))
        else:
            cleaned.append(text.strip("/"))
    if not cleaned:
        return ""
    return posixpath.join(*cleaned)


def is_wsl_unc_path(path: str) -> bool:
    return bool(path) and str(path).replace("/", "\\").startswith("\\\\wsl$\\")


def detect_wsl_distros() -> list[str]:
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


def linux_path_to_unc(linux_path: str, distro_name: str) -> str:
    if not linux_path or not distro_name:
        return ""
    clean = linux_path.strip().replace("/", "\\").lstrip("\\")
    return f"\\\\wsl$\\{distro_name}\\{clean}"


def find_workspace() -> Optional[str]:
    from partner.setup import find_workspace as _fw
    return _fw()


def load_dialog_history(workspace: str, n: int = 50) -> list[dict]:
    hist_path = os.path.join(workspace, "state", "dialog_history.jsonl")
    if not os.path.exists(hist_path):
        return []
    turns = []
    try:
        with open(hist_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-n:]:
            try:
                turns.append(json.loads(line.strip(), strict=False))
            except Exception:
                continue
    except Exception:
        pass
    return turns


def read_token_usage(instance_dir: str) -> tuple[int, int]:
    total = 0
    today = 0
    csv_path = os.path.join(instance_dir, "20_records", "metrics", "token_usage.csv")
    if os.path.exists(csv_path):
        try:
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


def pid_is_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run_silent(cmd, cwd=None, timeout=30, timeout_ok=False):
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or PARTNER_DIR,
            creationflags=CREATION_FLAGS,
            env=env,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        if timeout_ok:
            return "", "", 0
        return "", "Command timed out", 1
    except Exception as exc:
        return "", str(exc), 1


@dataclass
class InstanceSnapshot:
    id: str
    dir: str
    focus: str
    status_text: str
    status_color: str
    is_active: bool
    current_action: str
    last_seen: str
    run_duration: str
    cycle_count: int
    crash_count: int
    progress_text: str
    progress_pct: int
    knowledge_entries: int
    journal_count: int
    growth: str
    token_total: int
    token_today: int
    summary: str


class ChatWorker(QObject):
    finished = Signal(str, str)

    def __init__(self, workspace: str, text: str):
        super().__init__()
        self.workspace = workspace
        self.text = text

    def run(self):
        try:
            from partner.journal import Journal as _J
            from partner.knowledge import KnowledgeBase as _K
            from partner.task_queue import TaskQueue as _TQ
            from partner.state import StateManager as _SM
            from partner.conversation import ConversationEngine as _CE
            from partner.adapter import create_adapter as _ca

            ws = self.workspace
            j = _J(os.path.join(ws, "state", "journal.jsonl")) if ws else None
            k = _K(os.path.join(ws, "state", "knowledge.json")) if ws else None
            tq = _TQ(os.path.join(ws, "state", "task_queue.json")) if ws else None
            st = _SM(os.path.join(ws, "state")) if ws else None
            eng = _CE(j, k, tq, st, ws or "")
            adapter = _ca("hermes", ws) if ws else None

            if adapter:
                prompt = f"你是Partner，我的私人研究伙伴。用简短自然的口语回复。\n\n用户说: {self.text}"
                reply = adapter.chat(prompt)
                if reply:
                    self.finished.emit("ok", reply)
                    return

            reply = eng.respond(self.text)
            self.finished.emit("ok", reply)
        except Exception as exc:
            self.finished.emit("error", str(exc))


class MetricCard(QFrame):
    def __init__(self, label: str, value: str, accent: str = COLORS["text"], icon: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        if icon:
            icon_widget = QLabel()
            icon_widget.setAlignment(Qt.AlignCenter)
            icon_widget.setFixedSize(38, 38)
            icon_widget.setStyleSheet(
                f"background: {COLORS['panel_alt']}; border-radius: 19px;"
            )
            icon_widget.setPixmap(load_svg_icon(icon).pixmap(18, 18))
            layout.addWidget(icon_widget)
        label_widget = QLabel(label)
        label_widget.setObjectName("MetricLabel")
        value_widget = QLabel(value)
        value_widget.setStyleSheet(f"color: {accent}; font-size: 22px; font-weight: 760;")
        layout.addWidget(label_widget)
        layout.addWidget(value_widget)


class TitleBar(QFrame):
    def __init__(self, window: "PartnerQtWindow", parent=None):
        super().__init__(parent)
        self.window = window
        self.drag_origin: Optional[QPoint] = None
        self.setFixedHeight(56)
        self.setObjectName("TitleBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 8, 0, 0)
        layout.setSpacing(10)

        badge = QLabel("P")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(30, 30)
        badge.setStyleSheet(
            f"background:{COLORS['accent']}; color:white; border-radius:15px; font-size:14px; font-weight:800;"
        )
        layout.addWidget(badge)

        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(0)
        self.title_label = QLabel("Partner")
        self.title_label.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.sub_label = QLabel("")
        self.sub_label.setObjectName("Subtle")
        labels.addWidget(self.title_label)
        if self.sub_label.text():
            labels.addWidget(self.sub_label)
        layout.addLayout(labels)
        layout.addStretch(1)

        self.min_btn = QPushButton("−")
        self.max_btn = QPushButton("□")
        self.close_btn = QPushButton("×")
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(0)
        for btn in (self.min_btn, self.max_btn, self.close_btn):
            btn.setFixedSize(46, 40)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:transparent;color:#0f172a;border:none;border-radius:0;font-size:16px;font-weight:500;}"
                "QPushButton:hover{background:#e2e8f0;color:#0f172a;}"
            )
        self.close_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:#0f172a;border:none;border-radius:0;font-size:17px;font-weight:400;}}"
            f"QPushButton:hover{{background:{COLORS['red']};color:white;}}"
        )
        self.min_btn.clicked.connect(self.window.showMinimized)
        self.max_btn.clicked.connect(self.window.toggle_max_restore)
        self.close_btn.clicked.connect(self.window.close)
        controls.addWidget(self.min_btn)
        controls.addWidget(self.max_btn)
        controls.addWidget(self.close_btn)
        layout.addLayout(controls)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_origin = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None and event.buttons() & Qt.LeftButton and not self.window.isMaximized():
            self.window.move(event.globalPosition().toPoint() - self.drag_origin)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_origin = None
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.window.toggle_max_restore()
            event.accept()


class SetupDialog(QDialog):
    def __init__(self, parent, workspace_mode: str, workspace: str, bridge_settings: dict):
        super().__init__(parent)
        self.setWindowTitle(tr("setup_title", parent.lang))
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(760, 760)
        self.parent_window = parent
        self.bridge_settings = bridge_settings or {}
        self.result_workspace = workspace
        self.result_mode = workspace_mode

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(0)
        self.setStyleSheet(dialog_stylesheet())

        shell = QFrame()
        shell.setObjectName("Card")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 90))
        shell.setGraphicsEffect(shadow)
        root.addWidget(shell)

        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(22, 18, 22, 22)
        shell_layout.setSpacing(16)

        header = QHBoxLayout()
        head_label = QLabel(tr("setup_title", parent.lang))
        head_label.setStyleSheet("font-size: 14px; font-weight: 700;")
        header.addWidget(head_label)
        close_btn = QPushButton("×")
        close_btn.setObjectName("TitleControlClose")
        close_btn.setFixedSize(46, 38)
        close_btn.clicked.connect(self.reject)
        header.addStretch(1)
        header.addWidget(close_btn)
        shell_layout.addLayout(header)

        title = QLabel(tr("setup_title", parent.lang))
        title.setStyleSheet("font-size: 28px; font-weight: 800;")
        shell_layout.addWidget(title)
        sub = QLabel(tr("setup_sub", parent.lang))
        sub.setStyleSheet(f"color: {COLORS['subtext']}; font-size: 14px;")
        sub.setWordWrap(True)
        shell_layout.addWidget(sub)

        mode_box = QGroupBox("连接方式")
        mode_layout = QVBoxLayout(mode_box)
        self.mode_group = QButtonGroup(self)
        self.local_radio = QRadioButton("Windows 本地工作区")
        self.wsl_radio = QRadioButton("连接 Linux / WSL 中的 Partner")
        self.ssh_radio = QRadioButton("连接 SSH 服务器中的 Partner")
        self.mode_group.addButton(self.local_radio)
        self.mode_group.addButton(self.wsl_radio)
        self.mode_group.addButton(self.ssh_radio)
        self.local_radio.setChecked(workspace_mode == "local")
        self.wsl_radio.setChecked(workspace_mode == "wsl")
        self.ssh_radio.setChecked(workspace_mode == "ssh")
        mode_layout.addWidget(self.local_radio)
        mode_layout.addWidget(self.wsl_radio)
        mode_layout.addWidget(self.ssh_radio)
        shell_layout.addWidget(mode_box)

        local_box = QGroupBox("Windows 本地工作区")
        local_layout = QHBoxLayout(local_box)
        self.local_input = QLineEdit(workspace if workspace_mode == "local" else str(Path.home() / "partner_workspace"))
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self.pick_local_dir)
        local_layout.addWidget(self.local_input)
        local_layout.addWidget(browse_btn)
        shell_layout.addWidget(local_box)

        wsl_box = QGroupBox("Linux / WSL 工作区")
        wsl_layout = QGridLayout(wsl_box)
        distros = detect_wsl_distros()
        distro_default = (bridge_settings.get("wsl_distro") or (distros[0] if distros else ""))
        self.distro_input = QComboBox()
        self.distro_input.setObjectName("ModernCombo")
        self.distro_input.setEditable(True)
        self.distro_input.addItems(distros)
        if distro_default:
            self.distro_input.setCurrentText(distro_default)
        self.linux_path_input = QLineEdit(bridge_settings.get("linux_workspace") or "/mnt/e/work/partner_workspace")
        hint = QLabel("例如 `/mnt/e/work/partner_workspace`，会转换成 `\\\\wsl$` 路径连接。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['subtext']};")
        wsl_layout.addWidget(QLabel("WSL 发行版"), 0, 0)
        wsl_layout.addWidget(self.distro_input, 0, 1)
        wsl_layout.addWidget(QLabel("Linux 路径"), 1, 0)
        wsl_layout.addWidget(self.linux_path_input, 1, 1)
        wsl_layout.addWidget(hint, 2, 0, 1, 2)
        shell_layout.addWidget(wsl_box)

        ssh_box = QGroupBox("SSH 服务器")
        ssh_layout = QGridLayout(ssh_box)
        self.ssh_host_input = QLineEdit(bridge_settings.get("ssh_host") or "159.75.97.6")
        self.ssh_port_input = QLineEdit(str(bridge_settings.get("ssh_port") or 22))
        self.ssh_user_input = QLineEdit(bridge_settings.get("ssh_user") or "ubuntu")
        self.ssh_key_input = QLineEdit(bridge_settings.get("ssh_key") or "/mnt/e/work/temp/zty.pem")
        self.ssh_workspace_input = QLineEdit(bridge_settings.get("ssh_workspace") or "/home/ubuntu/partner_workspace")
        self.ssh_partner_dir_input = QLineEdit(bridge_settings.get("ssh_partner_dir") or "/home/ubuntu/partner")
        ssh_hint = QLabel("支持 host / port / user / key / remote workspace，仪表盘和实例控制会通过 SSH 读取与执行。")
        ssh_hint.setWordWrap(True)
        ssh_hint.setStyleSheet(f"color: {COLORS['subtext']};")
        ssh_layout.addWidget(QLabel("Host"), 0, 0)
        ssh_layout.addWidget(self.ssh_host_input, 0, 1)
        ssh_layout.addWidget(QLabel("Port"), 0, 2)
        ssh_layout.addWidget(self.ssh_port_input, 0, 3)
        ssh_layout.addWidget(QLabel("User"), 1, 0)
        ssh_layout.addWidget(self.ssh_user_input, 1, 1)
        ssh_layout.addWidget(QLabel("Key"), 1, 2)
        ssh_layout.addWidget(self.ssh_key_input, 1, 3)
        ssh_layout.addWidget(QLabel("Remote Workspace"), 2, 0)
        ssh_layout.addWidget(self.ssh_workspace_input, 2, 1, 1, 3)
        ssh_layout.addWidget(QLabel("Partner Dir"), 3, 0)
        ssh_layout.addWidget(self.ssh_partner_dir_input, 3, 1, 1, 3)
        ssh_layout.addWidget(ssh_hint, 4, 0, 1, 4)
        shell_layout.addWidget(ssh_box)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel_btn = QPushButton("取消")
        save_btn = QPushButton("保存")
        save_btn.setObjectName("PrimaryAction")
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self.save)
        footer.addWidget(cancel_btn)
        footer.addWidget(save_btn)
        shell_layout.addLayout(footer)

    def pick_local_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作区文件夹", self.local_input.text().strip() or str(Path.home()))
        if path:
            self.local_input.setText(path)

    def save(self):
        from partner.config import save_partner_config_data
        from partner.setup import save_workspace_pointer

        if self.local_radio.isChecked():
            ws = self.local_input.text().strip()
            if not ws:
                QMessageBox.warning(self, "Partner", "请选择工作区文件夹")
                return
            os.makedirs(ws, exist_ok=True)
            for sub in ["state", "logs", "data", "00_config"]:
                os.makedirs(os.path.join(ws, sub), exist_ok=True)
            config = {
                "workspace": {"path": ws, "readonly_dirs": []},
                "agent": {"backend": "hermes"},
                "scheduler": {"interval_minutes": 30, "max_tasks_per_cycle": 1, "heartbeat_timeout_minutes": 60},
                "name": "Partner",
            }
            save_partner_config_data(ws, config)
            save_workspace_pointer(ws)
            self.result_workspace = ws
            self.result_mode = "local"
        elif self.wsl_radio.isChecked():
            distro = self.distro_input.currentText().strip()
            linux_path = self.linux_path_input.text().strip()
            if not distro or not linux_path:
                QMessageBox.warning(self, "Partner", "请填写 WSL 发行版和 Linux 路径")
                return
            unc = linux_path_to_unc(linux_path, distro)
            save_gui_bridge_settings(
                {
                    "mode": "wsl",
                    "wsl_distro": distro,
                    "linux_workspace": linux_path,
                    "unc_workspace": unc,
                    "saved_at": datetime.now().isoformat(),
                }
            )
            save_workspace_pointer(unc)
            self.result_workspace = unc
            self.result_mode = "wsl"
        else:
            host = self.ssh_host_input.text().strip()
            user = self.ssh_user_input.text().strip()
            key = self.ssh_key_input.text().strip()
            remote_ws = self.ssh_workspace_input.text().strip()
            partner_dir = self.ssh_partner_dir_input.text().strip()
            try:
                port = int(self.ssh_port_input.text().strip() or "22")
            except ValueError:
                QMessageBox.warning(self, "Partner", "SSH 端口必须是整数")
                return
            if not host or not user or not key or not remote_ws:
                QMessageBox.warning(self, "Partner", "请填写 SSH host / user / key / remote workspace")
                return
            save_gui_bridge_settings(
                {
                    "mode": "ssh",
                    "ssh_host": host,
                    "ssh_port": port,
                    "ssh_user": user,
                    "ssh_key": key,
                    "ssh_workspace": remote_ws,
                    "ssh_partner_dir": partner_dir or "/home/ubuntu/partner",
                    "saved_at": datetime.now().isoformat(),
                }
            )
            self.result_workspace = remote_ws
            self.result_mode = "ssh"
        self.accept()


class PartnerQtWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.lang = "zh"
        self.bridge_settings = load_gui_bridge_settings()
        self.workspace, self.workspace_mode = resolve_initial_workspace(self.bridge_settings)
        self.chat_worker = None
        self._normal_geometry = None
        self._status_dot_color = COLORS["accent"]
        self._remote_bundle_cache: dict | None = None
        self._remote_bundle_ts: float = 0.0
        self._prepared_windows_ssh_key: str = ""
        self._prepared_windows_ssh_key_source: str = ""
        self._remote_user_file_list_cache: dict[str, tuple[float, list[str]]] = {}
        self._remote_text_cache: dict[str, tuple[float, str]] = {}

        self.setWindowTitle(tr("app_title", self.lang))
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(1240, 820)
        self.setMinimumSize(1080, 720)
        self.build_ui()
        self.refresh_all()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_dashboard)
        self.timer.start(AUTO_REFRESH_INTERVAL_MS)

    def nav_label(self, key: str) -> str:
        return tr(key, self.lang)

    def activity_glyph(self, idx: int) -> str:
        frames = ["◜", "◝", "◞", "◟"]
        return frames[idx % len(frames)]

    def qt_icon(self, key: str):
        return load_svg_icon(key)

    def build_ui(self):
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{
                background: transparent;
                color: {COLORS['text']};
                font-family: 'Segoe UI Variable Text', 'Microsoft YaHei UI', 'Segoe UI';
                font-size: 15px;
            }}
            QFrame#Shell {{
                background: {COLORS['shell']};
                border: 1px solid {COLORS['border']};
                border-radius: 24px;
            }}
            QFrame#TitleBar {{
                background: transparent;
                border-bottom: 1px solid {COLORS['border']};
            }}
            QFrame#SideBar {{
                background: {COLORS['panel']};
                border-right: 1px solid {COLORS['border']};
                border-top-left-radius: 22px;
                border-bottom-left-radius: 22px;
            }}
            QFrame#Card, QGroupBox {{
                background: {COLORS['panel_alt']};
                border: 1px solid {COLORS['border']};
                border-radius: 20px;
            }}
            QGroupBox {{
                margin-top: 10px;
                padding-top: 20px;
                font-size: 15px;
                font-weight: 700;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 18px;
                top: 10px;
            }}
            QPushButton {{
                background: {COLORS['muted']};
                border: 1px solid {COLORS['border']};
                border-radius: 14px;
                padding: 11px 16px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: #e8eef6;
            }}
            QPushButton:checked {{
                background: {COLORS['accent']};
                color: white;
                border-color: {COLORS['accent_soft']};
            }}
            QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QComboBox {{
                background: {COLORS['muted']};
                border: 1px solid {COLORS['border']};
                border-radius: 14px;
                padding: 12px;
                selection-background-color: {COLORS['accent']};
            }}
            QComboBox {{
                padding-right: 36px;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 32px;
                border: none;
                margin-right: 8px;
            }}
            QComboBox::down-arrow {{
                image: url({icon_url('chevron_down')});
                width: 12px;
                height: 12px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 6px 0 6px 0;
            }}
            QScrollBar::handle:vertical {{
                background: #cdd7e4;
                min-height: 56px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #b6c5d8;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
                height: 0;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 10px;
                margin: 0 6px 0 6px;
            }}
            QScrollBar::handle:horizontal {{
                background: #cdd7e4;
                min-width: 56px;
                border-radius: 5px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: #b6c5d8;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                background: transparent;
                width: 0;
            }}
            QListWidget::item {{
                padding: 12px;
                border-radius: 12px;
            }}
            QListWidget::item:selected {{
                background: {COLORS['accent']};
                color: white;
            }}
            QLabel#Subtle {{
                color: {COLORS['subtext']};
            }}
            QFrame#Hero {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f8fbff, stop:1 #eef4fb);
                border: 1px solid #dbe6f2;
                border-radius: 24px;
            }}
            QFrame#MetricCard {{
                background: {COLORS['muted']};
                border: 1px solid {COLORS['border']};
                border-radius: 18px;
            }}
            QFrame#MiniPill {{
                background: {COLORS['muted']};
                border: 1px solid {COLORS['border']};
                border-radius: 16px;
            }}
            QPushButton#NavButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 12px;
                padding: 12px 16px;
                text-align: left;
                font-weight: 600;
            }}
            QPushButton#NavButton:hover {{
                background: #eef3f9;
                border-color: #d7e0eb;
            }}
            QPushButton#NavButton:checked {{
                background: #e7f0ff;
                color: #174ea6;
                border-color: #c8dafc;
            }}
            QPushButton#SecondaryAction {{
                background: #f6f9fc;
                border: 1px solid #d8e0ea;
                border-radius: 12px;
                padding: 12px 16px;
            }}
            QPushButton#SecondaryAction:hover {{
                background: #eef3f9;
            }}
            QLabel#MetricLabel {{
                color: {COLORS['subtext']};
                font-size: 13px;
            }}
            """
        )

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("Shell")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 120))
        shell.setGraphicsEffect(shadow)
        root.addWidget(shell)

        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.title_bar = TitleBar(self, shell)
        shell_layout.addWidget(self.title_bar)

        body = QWidget()
        shell_layout.addWidget(body, 1)
        body_root = QHBoxLayout(body)
        body_root.setContentsMargins(0, 0, 0, 0)
        body_root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("SideBar")
        sidebar.setFixedWidth(246)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(22, 24, 22, 24)
        side_layout.setSpacing(16)

        brand = QLabel("Partner")
        brand.setStyleSheet("font-size: 24px; font-weight: 760; letter-spacing: 0.5px;")
        side_layout.addWidget(brand)
        self.mode_label = QLabel()
        self.mode_label.setObjectName("Subtle")
        side_layout.addWidget(self.mode_label)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {COLORS['yellow']}; font-size: 18px;")
        self.status_text = QLabel("刷新中…")
        self.status_text.setObjectName("Subtle")
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_text)
        status_row.addStretch(1)
        side_layout.addLayout(status_row)

        self.nav_buttons = []
        nav_group = QButtonGroup(self)
        tabs = [
            (self.nav_label("tab_dashboard"), 0, "dashboard"),
            (self.nav_label("tab_chat"), 1, "chat"),
            (self.nav_label("tab_qq"), 2, "instances"),
            (self.nav_label("tab_logs"), 3, "logs"),
        ]
        for text, index, icon_key in tabs:
            btn = QPushButton(text)
            btn.setObjectName("NavButton")
            btn.setIcon(self.qt_icon(icon_key))
            btn.setIconSize(QSize(18, 18))
            btn.setCheckable(True)
            btn.setMinimumHeight(50)
            btn.clicked.connect(lambda checked=False, idx=index: self.switch_page(idx))
            nav_group.addButton(btn)
            side_layout.addWidget(btn)
            self.nav_buttons.append(btn)
        self.nav_buttons[0].setChecked(True)

        side_layout.addStretch(1)
        self.settings_btn = QPushButton("连接设置")
        self.settings_btn.setObjectName("SecondaryAction")
        self.settings_btn.setIcon(self.qt_icon("instances"))
        self.settings_btn.setIconSize(QSize(18, 18))
        self.settings_btn.clicked.connect(self.open_setup)
        side_layout.addWidget(self.settings_btn)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(34, 28, 34, 30)
        content_layout.setSpacing(18)

        self.page_caption = QLabel(tr("subtitle", self.lang))
        self.page_caption.setObjectName("Subtle")
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.page_title = QLabel(tr("tab_dashboard", self.lang))
        self.page_title.setStyleSheet("font-size: 30px; font-weight: 760;")
        title_row.addWidget(self.page_title)
        title_row.addStretch(1)
        content_layout.addWidget(self.page_caption)
        content_layout.addLayout(title_row)
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        action_row.addStretch(1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("SecondaryAction")
        self.refresh_btn.setIcon(self.qt_icon("today"))
        self.refresh_btn.setIconSize(QSize(16, 16))
        self.refresh_btn.clicked.connect(self.refresh_all)
        action_row.addWidget(self.refresh_btn)
        content_layout.addLayout(action_row)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)

        self.dashboard_page = self.build_dashboard_page()
        self.chat_page = self.build_chat_page()
        self.qq_page = self.build_qq_page()
        self.logs_page = self.build_logs_page()

        self.stack.addWidget(self.dashboard_page)
        self.stack.addWidget(self.chat_page)
        self.stack.addWidget(self.qq_page)
        self.stack.addWidget(self.logs_page)

        body_root.addWidget(sidebar)
        body_root.addWidget(content, 1)

    def build_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        self.dashboard_scroll = QScrollArea()
        self.dashboard_scroll.setWidgetResizable(True)
        self.dashboard_scroll.setFrameShape(QFrame.NoFrame)
        self.dashboard_container = QWidget()
        self.dashboard_layout = QVBoxLayout(self.dashboard_container)
        self.dashboard_layout.setContentsMargins(4, 4, 4, 4)
        self.dashboard_layout.setSpacing(18)
        self.dashboard_scroll.setWidget(self.dashboard_container)
        layout.addWidget(self.dashboard_scroll)
        return page

    def build_chat_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(16)
        header = QFrame()
        header.setObjectName("Card")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(12)
        target_label = QLabel("发送目标")
        target_label.setStyleSheet(f"color: {COLORS['subtext']}; font-size: 13px; font-weight: 600;")
        self.chat_instance_combo = QComboBox()
        self.chat_instance_combo.setObjectName("ModernCombo")
        self.chat_instance_combo.currentIndexChanged.connect(self.on_chat_instance_changed)
        self.chat_target_hint = QLabel("未选择实例")
        self.chat_target_hint.setStyleSheet(f"color: {COLORS['text']}; font-size: 14px; font-weight: 600;")
        header_layout.addWidget(target_label)
        header_layout.addWidget(self.chat_instance_combo, 0)
        header_layout.addWidget(self.chat_target_hint, 1)
        layout.addWidget(header)
        self.chat_view = QTextBrowser()
        self.chat_view.setOpenExternalLinks(True)
        self.chat_view.setMinimumHeight(500)
        layout.addWidget(self.chat_view, 1)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("输入消息…")
        self.chat_input.returnPressed.connect(self.send_chat)
        self.chat_send_btn = QPushButton("发送")
        self.chat_send_btn.clicked.connect(self.send_chat)
        row.addWidget(self.chat_input, 1)
        row.addWidget(self.chat_send_btn)
        layout.addLayout(row)
        return page

    def build_qq_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(4, 4, 4, 4)
        page_layout.setSpacing(12)

        self.qq_source_banner = QFrame()
        self.qq_source_banner.setObjectName("Card")
        banner_layout = QHBoxLayout(self.qq_source_banner)
        banner_layout.setContentsMargins(16, 12, 16, 12)
        banner_layout.setSpacing(12)
        banner_icon = QLabel()
        banner_icon.setPixmap(self.qt_icon("source_wsl").pixmap(16, 16))
        self.qq_source_label = QLabel("数据来源：当前工作区")
        self.qq_source_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 14px; font-weight: 600;")
        banner_layout.addWidget(banner_icon)
        banner_layout.addWidget(self.qq_source_label, 1)
        page_layout.addWidget(self.qq_source_banner)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        left_box = QGroupBox("实例")
        left_layout = QVBoxLayout(left_box)
        left_layout.setSpacing(12)
        self.instance_status = QLabel("实例状态：未选择")
        self.instance_status.setObjectName("Subtle")
        left_layout.addWidget(self.instance_status)
        self.instance_list = QListWidget()
        self.instance_list.currentItemChanged.connect(self.on_instance_selected)
        left_layout.addWidget(self.instance_list, 1)
        row1 = QHBoxLayout()
        add_instance_btn = QPushButton("新增实例")
        del_instance_btn = QPushButton("删除实例")
        self.add_instance_btn = add_instance_btn
        self.del_instance_btn = del_instance_btn
        add_instance_btn.clicked.connect(self.add_instance)
        del_instance_btn.clicked.connect(self.delete_instance)
        row1.addWidget(add_instance_btn)
        row1.addWidget(del_instance_btn)
        left_layout.addLayout(row1)
        row1b = QHBoxLayout()
        self.start_instance_btn = QPushButton("开启实例")
        self.stop_instance_btn = QPushButton("关闭实例")
        self.start_instance_btn.clicked.connect(self.start_selected_instance)
        self.stop_instance_btn.clicked.connect(self.stop_selected_instance)
        row1b.addWidget(self.start_instance_btn)
        row1b.addWidget(self.stop_instance_btn)
        left_layout.addLayout(row1b)

        mid_box = QGroupBox("QQ 机器人")
        mid_layout = QVBoxLayout(mid_box)
        mid_layout.setSpacing(12)
        self.bot_status = QLabel("机器人状态：未选择")
        self.bot_status.setObjectName("Subtle")
        mid_layout.addWidget(self.bot_status)
        self.bot_list = QListWidget()
        self.bot_list.currentItemChanged.connect(self.on_bot_selected)
        mid_layout.addWidget(self.bot_list, 1)
        row2 = QHBoxLayout()
        add_bot_btn = QPushButton("新增机器人")
        config_bot_btn = QPushButton("配置机器人")
        del_bot_btn = QPushButton("删除机器人")
        self.add_bot_btn = add_bot_btn
        self.config_bot_btn = config_bot_btn
        self.del_bot_btn = del_bot_btn
        add_bot_btn.clicked.connect(self.add_bot)
        config_bot_btn.clicked.connect(self.configure_bot)
        del_bot_btn.clicked.connect(self.delete_bot)
        row2.addWidget(add_bot_btn)
        row2.addWidget(config_bot_btn)
        row2.addWidget(del_bot_btn)
        mid_layout.addLayout(row2)
        row2b = QHBoxLayout()
        self.start_bot_btn = QPushButton("开启 QQ 机器人")
        self.stop_bot_btn = QPushButton("关闭 QQ 机器人")
        self.start_bot_btn.clicked.connect(self.start_selected_bot)
        self.stop_bot_btn.clicked.connect(self.stop_selected_bot)
        row2b.addWidget(self.start_bot_btn)
        row2b.addWidget(self.stop_bot_btn)
        mid_layout.addLayout(row2b)

        right_box = QGroupBox("详情")
        right_layout = QVBoxLayout(right_box)
        self.qq_info = QTextBrowser()
        right_layout.addWidget(self.qq_info, 1)

        layout.addWidget(left_box, 1)
        layout.addWidget(mid_box, 1)
        layout.addWidget(right_box, 2)
        page_layout.addLayout(layout, 1)
        return page

    def build_logs_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(16)

        left_box = QGroupBox("记录目录")
        left = QVBoxLayout(left_box)
        left.setSpacing(12)
        self.log_metrics = QHBoxLayout()
        self.log_metrics.setSpacing(12)
        left.addLayout(self.log_metrics)
        self.log_instance_combo = QComboBox()
        self.log_instance_combo.setObjectName("ModernCombo")
        self.log_instance_combo.currentIndexChanged.connect(self.refresh_logs)
        self.log_root_combo = QComboBox()
        self.log_root_combo.setObjectName("ModernCombo")
        self.log_root_combo.addItems(["user"])
        self.log_root_combo.currentIndexChanged.connect(self.refresh_logs)
        self.log_breadcrumb = QLabel("当前目录")
        self.log_breadcrumb.setObjectName("Subtle")
        self.log_list = QListWidget()
        self.log_list.currentItemChanged.connect(self.show_log_item)
        left.addWidget(self.log_instance_combo)
        left.addWidget(self.log_root_combo)
        left.addWidget(self.log_breadcrumb)
        left.addWidget(self.log_list, 1)

        right_box = QGroupBox("内容预览")
        right = QVBoxLayout(right_box)
        right.setSpacing(12)
        self.log_preview_title = QLabel("选择左侧记录查看详情")
        self.log_preview_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        right.addWidget(self.log_preview_title)
        right.addWidget(self.log_view, 1)
        layout.addWidget(left_box, 1)
        layout.addWidget(right_box, 2)
        return page

    def toggle_max_restore(self):
        if self.isMaximized():
            self.showNormal()
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
            self.title_bar.max_btn.setText("□")
            return
        self._normal_geometry = self.geometry()
        self.showMaximized()
        self.title_bar.max_btn.setText("❐")

    def switch_page(self, idx: int):
        self.stack.setCurrentIndex(idx)
        self.page_title.setText(
            [
                tr("tab_dashboard", self.lang),
                tr("tab_chat", self.lang),
                tr("tab_qq", self.lang),
                tr("tab_logs", self.lang),
            ][idx]
        )
        if idx == 0:
            self.refresh_dashboard()
        elif idx == 2:
            self.refresh_qq_page()
        elif idx == 3:
            self.refresh_logs()

    def open_setup(self):
        dlg = SetupDialog(self, self.workspace_mode, self.workspace or "", self.bridge_settings)
        if dlg.exec():
            self.workspace = dlg.result_workspace
            self.workspace_mode = dlg.result_mode
            self.bridge_settings = load_gui_bridge_settings()
            self.refresh_all()

    def ssh_target(self) -> tuple[str, str, str, str]:
        host = (self.bridge_settings.get("ssh_host") or "").strip()
        user = (self.bridge_settings.get("ssh_user") or "").strip()
        key = (self.bridge_settings.get("ssh_key") or "").strip()
        port = str(self.bridge_settings.get("ssh_port") or "22").strip()
        return host, user, key, port

    def run_ssh(self, command: str, capture: bool = True) -> tuple[bool, str]:
        host, user, key, port = self.ssh_target()
        if not host or not user or not key:
            return False, "SSH 配置不完整。"
        target = f"{user}@{host}"
        if os.name == "nt":
            safe_key = ""
            if self._prepared_windows_ssh_key and self._prepared_windows_ssh_key_source == key and os.path.exists(self._prepared_windows_ssh_key):
                safe_key = self._prepared_windows_ssh_key
            else:
                ok, safe_key = prepare_windows_ssh_key_copy(key)
                if not ok:
                    return False, safe_key
                self._prepared_windows_ssh_key = safe_key
                self._prepared_windows_ssh_key_source = key
            ssh_exe = r"C:\Windows\System32\OpenSSH\ssh.exe"
            cmd = [
                ssh_exe,
                "-i",
                safe_key,
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-p",
                port,
                target,
                command,
            ]
        else:
            safe_key = ensure_private_key_copy(key)
            cmd = [
                "ssh",
                "-i",
                safe_key,
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-p",
                port,
                target,
                command,
            ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATION_FLAGS,
                timeout=20,
            )
            output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
            return result.returncode == 0, output
        except Exception as exc:
            return False, str(exc)

    def remote_python_command(self, script: str) -> str:
        encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
        py = "import base64;exec(base64.b64decode(%r).decode('utf-8'))" % encoded
        return "python3 -c " + shlex.quote(py)

    def fetch_remote_bundle(self, force: bool = False) -> dict:
        if not force and self._remote_bundle_cache and (time.time() - self._remote_bundle_ts) < 15:
            return self._remote_bundle_cache
        workspace = self.workspace or ""
        script = f"""
import json, os, shutil
ws = {workspace!r}
def detect_remote_hermes(global_config):
    candidates = []
    direct = shutil.which("hermes")
    if direct:
        candidates.append(direct)
    home = os.path.expanduser("~")
    for extra in (
        os.path.join(home, ".local", "bin", "hermes"),
        os.path.join(home, ".cargo", "bin", "hermes"),
    ):
        if os.path.exists(extra):
            candidates.append(extra)
    python_cmd = str(global_config.get("python_cmd") or "").strip()
    if python_cmd:
        sibling = os.path.join(os.path.dirname(python_cmd), "hermes")
        if os.path.exists(sibling):
            candidates.append(sibling)
    candidates = [p for p in candidates if p]
    return {{
        "available": bool(candidates),
        "path": candidates[0] if candidates else "",
        "issues": [] if candidates else ["cli_missing_in_noninteractive_shell"],
    }}
bundle = {{
  "workspace": ws,
  "global_config": {{}},
  "hermes": {{"available": False, "path": "", "issues": []}},
  "error": "",
  "instances": {{}}
}}
gc = os.path.join(ws, "global_config.json")
if os.path.exists(gc):
    try:
        bundle["global_config"] = json.load(open(gc, "r", encoding="utf-8"))
    except Exception:
        bundle["global_config"] = {{}}
bundle["hermes"] = detect_remote_hermes(bundle["global_config"])
instances = (bundle["global_config"].get("instances") or {{}})
for inst_id in sorted(instances.keys()):
    inst_dir = os.path.join(ws, "instances", inst_id)
    def load_json(p):
        if os.path.exists(p):
            try:
                return json.load(open(p, "r", encoding="utf-8"))
            except Exception:
                return {{}}
        return {{}}
    def load_text(p):
        if os.path.exists(p):
            try:
                return open(p, "r", encoding="utf-8", errors="replace").read()
            except Exception:
                return ""
        return ""
    def count_lines(p):
        if os.path.exists(p):
            try:
                return sum(1 for _ in open(p, "r", encoding="utf-8", errors="replace"))
            except Exception:
                return 0
        return 0
    def read_tokens(inst_dir):
        total = 0
        today = 0
        from datetime import datetime
        csv_path = os.path.join(inst_dir, "20_records", "metrics", "token_usage.csv")
        if os.path.exists(csv_path):
            try:
                today_key = datetime.now().date().isoformat()
                import csv
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
        log_path = os.path.join(inst_dir, "logs", "hermes_chat.jsonl")
        if not os.path.exists(log_path):
            return 0, 0
        today_key = datetime.now().date().isoformat()
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    tokens = int(row.get("total_tokens_est") or row.get("total_tokens") or 0)
                    total += tokens
                    stamp = row.get("ts", "") or row.get("timestamp", "") or ""
                    if stamp.startswith(today_key):
                        today += tokens
        except Exception:
            return 0, 0
        return total, today
    bot_cfg = os.path.join(inst_dir, "qq_configs.json")
    primary_bot = os.path.join(inst_dir, "00_config", "qq_config.json")
    legacy_bot = os.path.join(inst_dir, "qq_config.json")
    if os.path.exists(bot_cfg):
        try:
            bots = json.load(open(bot_cfg, "r", encoding="utf-8"))
        except Exception:
            bots = []
    else:
        single = load_json(primary_bot) if os.path.exists(primary_bot) else load_json(legacy_bot)
        bots = [single] if single else []
    qq_pid_path = os.path.join(inst_dir, "state", "qq_bot.pid")
    qq_pid = ""
    if os.path.exists(qq_pid_path):
        try:
            qq_pid = open(qq_pid_path).read().strip()
        except Exception:
            qq_pid = ""
    instance_pid_path = os.path.join(inst_dir, "instance.pid")
    instance_pid = ""
    if os.path.exists(instance_pid_path):
        try:
            instance_pid = open(instance_pid_path).read().strip()
        except Exception:
            instance_pid = ""
    def pid_alive(text):
        try:
            os.kill(int(text or "0"), 0)
            return True
        except Exception:
            return False
    token_total, token_today = read_tokens(inst_dir)
    bundle["instances"][inst_id] = {{
      "dir": inst_dir,
      "plan": load_json(os.path.join(inst_dir, "state", "active_plan.json")),
      "heartbeat": load_json(os.path.join(inst_dir, "state", "heartbeat.json")),
      "active_project": load_json(os.path.join(inst_dir, "20_records", "active_project.json")),
      "knowledge": load_json(os.path.join(inst_dir, "state", "knowledge.json")),
      "summary": load_text(os.path.join(inst_dir, "user", "current_project", "summary.md")),
      "journal_count": count_lines(os.path.join(inst_dir, "state", "journal.jsonl")),
      "bots": bots,
      "qq_pid": qq_pid,
      "instance_pid": instance_pid,
      "instance_running": pid_alive(instance_pid),
      "qq_running": pid_alive(qq_pid),
      "token_total": token_total,
      "token_today": token_today,
    }}
print(json.dumps(bundle, ensure_ascii=False))
"""
        cmd = self.remote_python_command(script)
        ok, out = self.run_ssh(cmd, capture=True)
        if not ok or not out:
            self._remote_bundle_cache = {
                "workspace": workspace,
                "global_config": {},
                "hermes": {"available": False, "path": "", "issues": ["ssh_failed"]},
                "error": out or "SSH 连接失败。",
                "instances": {},
            }
            self._remote_bundle_ts = time.time()
            return self._remote_bundle_cache
        try:
            self._remote_bundle_cache = json.loads(out)
        except Exception:
            start = out.find("{")
            end = out.rfind("}")
            if start != -1 and end > start:
                try:
                    self._remote_bundle_cache = json.loads(out[start : end + 1])
                except Exception:
                    self._remote_bundle_cache = {
                        "workspace": workspace,
                        "global_config": {},
                        "hermes": {"available": False, "path": "", "issues": ["parse_failed"]},
                        "error": out[:400],
                        "instances": {},
                    }
            else:
                self._remote_bundle_cache = {
                    "workspace": workspace,
                    "global_config": {},
                    "hermes": {"available": False, "path": "", "issues": ["parse_failed"]},
                    "error": out[:400],
                    "instances": {},
                }
        self._remote_bundle_ts = time.time()
        return self._remote_bundle_cache

    def remote_exists(self, path: str) -> bool:
        ok, _ = self.run_ssh(f"test -e {shlex.quote(path)}", capture=True)
        return ok

    def remote_isdir(self, path: str) -> bool:
        ok, _ = self.run_ssh(f"test -d {shlex.quote(path)}", capture=True)
        return ok

    def remote_json(self, path: str) -> dict:
        cmd = self.remote_python_command(
            "import json, os\n"
            f"p = {path!r}\n"
            "if os.path.exists(p):\n"
            "    try:\n"
            "        print(json.dumps(json.load(open(p, 'r', encoding='utf-8')), ensure_ascii=False))\n"
            "    except Exception:\n"
            "        print('{}')\n"
            "else:\n"
            "    print('{}')\n"
        )
        ok, out = self.run_ssh(cmd, capture=True)
        if not ok or not out:
            return {}
        try:
            return json.loads(out)
        except Exception:
            return {}

    def remote_text(self, path: str) -> str:
        cached = self._remote_text_cache.get(path)
        if cached and (time.time() - cached[0]) < 20:
            return cached[1]
        cmd = self.remote_python_command(
            "import os\n"
            f"p = {path!r}\n"
            "if os.path.exists(p):\n"
            "    try:\n"
            "        print(open(p, 'r', encoding='utf-8', errors='replace').read())\n"
            "    except Exception:\n"
            "        print('')\n"
            "else:\n"
            "    print('')\n"
        )
        ok, out = self.run_ssh(cmd, capture=True)
        text = out if ok else ""
        self._remote_text_cache[path] = (time.time(), text)
        return text

    def remote_count_lines(self, path: str) -> int:
        cmd = self.remote_python_command(
            "import os\n"
            f"p = {path!r}\n"
            "print(sum(1 for _ in open(p, 'r', encoding='utf-8', errors='replace')) if os.path.exists(p) else 0)\n"
        )
        ok, out = self.run_ssh(cmd, capture=True)
        try:
            return int((out or "0").strip())
        except Exception:
            return 0

    def remote_listdir(self, path: str) -> list[tuple[str, bool]]:
        cmd = self.remote_python_command(
            "import json, os\n"
            f"p = {path!r}\n"
            "items = []\n"
            "if os.path.isdir(p):\n"
            "    for name in sorted(os.listdir(p), key=str.lower):\n"
            "        full = os.path.join(p, name)\n"
            "        items.append({'name': name, 'is_dir': os.path.isdir(full)})\n"
            "print(json.dumps(items, ensure_ascii=False))\n"
        )
        ok, out = self.run_ssh(cmd, capture=True)
        if not ok or not out:
            return []
        try:
            data = json.loads(out)
            return [(item.get("name", ""), bool(item.get("is_dir"))) for item in data if item.get("name")]
        except Exception:
            return []

    def remote_walk_user_files(self, root: str, max_depth: int = 3) -> list[str]:
        cached = self._remote_user_file_list_cache.get(root)
        if cached and (time.time() - cached[0]) < 20:
            return cached[1]
        cmd = self.remote_python_command(
            "import json, os\n"
            f"root = {root!r}\n"
            f"max_depth = {int(max_depth)}\n"
            "rows = []\n"
            "if os.path.isdir(root):\n"
            "    for cur, dirs, files in os.walk(root):\n"
            "        rel = os.path.relpath(cur, root)\n"
            "        depth = 0 if rel == '.' else rel.count(os.sep) + 1\n"
            "        if depth > max_depth:\n"
            "            dirs[:] = []\n"
            "            continue\n"
            "        for name in sorted(files):\n"
            "            full = os.path.join(cur, name)\n"
            "            rows.append(os.path.relpath(full, root))\n"
            "print(json.dumps(rows, ensure_ascii=False))\n"
        )
        ok, out = self.run_ssh(cmd, capture=True)
        if not ok or not out:
            return []
        try:
            data = json.loads(out)
            rows = [str(item) for item in data]
            self._remote_user_file_list_cache[root] = (time.time(), rows)
            return rows
        except Exception:
            return []

    def local_walk_user_files(self, root: str, max_depth: int = 3) -> list[str]:
        rows: list[str] = []
        if not os.path.isdir(root):
            return rows
        for cur, dirs, files in os.walk(root):
            rel = os.path.relpath(cur, root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth > max_depth:
                dirs[:] = []
                continue
            for name in sorted(files):
                full = os.path.join(cur, name)
                rows.append(os.path.relpath(full, root))
        return rows

    def remote_dialog_history(self, workspace: str, n: int = 50) -> list[dict]:
        hist_path = os.path.join(workspace, "state", "dialog_history.jsonl")
        cmd = self.remote_python_command(
            "import json, os\n"
            f"p = {hist_path!r}\n"
            f"limit = {int(n)}\n"
            "rows = []\n"
            "if os.path.exists(p):\n"
            "    with open(p, 'r', encoding='utf-8', errors='replace') as f:\n"
            "        lines = f.readlines()[-limit:]\n"
            "    for line in lines:\n"
            "        try:\n"
            "            rows.append(json.loads(line.strip()))\n"
            "        except Exception:\n"
            "            continue\n"
            "print(json.dumps(rows, ensure_ascii=False))\n"
        )
        ok, out = self.run_ssh(cmd, capture=True)
        if not ok or not out:
            return []
        try:
            return json.loads(out)
        except Exception:
            return []

    def enqueue_remote_chat_message(self, instance_id: str, instance_dir: str, text: str) -> tuple[bool, str]:
        script = (
            "import json, os, uuid\n"
            "from datetime import datetime\n"
            f"inst_id = {instance_id!r}\n"
            f"ws = {instance_dir!r}\n"
            f"user_text = {text!r}\n"
            "state_dir = os.path.join(ws, 'state')\n"
            "os.makedirs(state_dir, exist_ok=True)\n"
            "history_path = os.path.join(state_dir, 'dialog_history.jsonl')\n"
            "queue_path = os.path.join(state_dir, 'task_queue.json')\n"
            "turn = {\n"
            "  'role': 'user',\n"
            "  'content': user_text,\n"
            "  'timestamp': datetime.now().isoformat(),\n"
            "  'intent': 'task',\n"
            "  'topic': None,\n"
            "}\n"
            "with open(history_path, 'a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps(turn, ensure_ascii=False) + '\\n')\n"
            "tasks = []\n"
            "if os.path.exists(queue_path):\n"
            "    try:\n"
            "        with open(queue_path, 'r', encoding='utf-8') as f:\n"
            "            data = json.load(f)\n"
            "        if isinstance(data, list):\n"
            "            tasks = [t for t in data if isinstance(t, dict)]\n"
            "    except Exception:\n"
            "        tasks = []\n"
            "normalized = ' '.join(user_text.split())\n"
            "for task in reversed(tasks[-50:]):\n"
            "    if task.get('status') in ('pending', 'in_progress') and ' '.join((task.get('description') or '').split()) == normalized:\n"
            "        print(json.dumps({'ok': True, 'queued': False, 'task_id': task.get('id') or '', 'message': '已存在相同待处理任务'}, ensure_ascii=False))\n"
            "        raise SystemExit(0)\n"
            "task = {\n"
            "  'id': f\"task_{uuid.uuid4().hex[:8]}\",\n"
            "  'type': 'deep_dive',\n"
            "  'title': (user_text[:60] or f'GUI message to {inst_id}').strip(),\n"
            "  'description': user_text,\n"
            "  'priority': 5,\n"
            "  'created_at': datetime.now().isoformat(),\n"
            "  'ttl_hours': 48,\n"
            "  'status': 'pending',\n"
            "  'tags': ['desktop_gui', 'chat_task'],\n"
            "  'result_summary': '',\n"
            "  'completed_at': None,\n"
            "  'source': 'desktop_gui',\n"
            "  'sender_id': 'windows_gui',\n"
            "  'sender_name': 'Windows Desktop',\n"
            "}\n"
            "tasks.append(task)\n"
            "with open(queue_path, 'w', encoding='utf-8') as f:\n"
            "    json.dump(tasks, f, ensure_ascii=False, indent=2)\n"
            "print(json.dumps({'ok': True, 'queued': True, 'task_id': task['id'], 'message': '消息已写入远端任务队列'}, ensure_ascii=False))\n"
        )
        cmd = self.remote_python_command(script)
        ok, out = self.run_ssh(cmd, capture=True)
        if not ok:
            return False, out or "远端任务写入失败。"
        try:
            data = json.loads(out)
            return bool(data.get("ok")), str(data.get("message") or "消息已写入远端任务队列")
        except Exception:
            return False, out or "远端返回格式异常。"

    def collect_dashboard_snapshot(self):
        ws = self.workspace
        remote_bundle = self.fetch_remote_bundle() if self.workspace_mode == "ssh" else None
        if self.workspace_mode == "ssh":
            hermes = (remote_bundle or {}).get("hermes") or {"available": False, "issues": ["ssh_failed"]}
        else:
            from partner.adapter import HermesAdapter
            hermes = HermesAdapter.detect_installation()
        snapshot = {
            "workspace": ws,
            "global_config": (remote_bundle or {}).get("global_config") if self.workspace_mode == "ssh" else self.global_config(),
            "instances": [],
            "alerts": [],
            "source": "ssh" if self.workspace_mode == "ssh" else ("wsl" if is_wsl_unc_path(ws or "") else "local"),
            "hermes": hermes,
            "remote_error": (remote_bundle or {}).get("error") if self.workspace_mode == "ssh" else "",
        }
        if self.workspace_mode == "ssh":
            if snapshot["remote_error"]:
                snapshot["alerts"].append(("error", f"SSH 数据读取失败：{snapshot['remote_error']}"))
            elif not snapshot["global_config"].get("instances"):
                snapshot["alerts"].append(("error", "已连接 SSH 服务器，但没有读到实例配置。"))
            elif not snapshot["hermes"]["available"]:
                snapshot["alerts"].append(("warn", "SSH 已连通，实例数据已读取；但远端非交互环境里没有找到 Hermes 命令。"))
        elif not snapshot["hermes"]["available"]:
            snapshot["alerts"].append(("error", "未检测到 Hermes，聊天和自动研究无法启动。"))
        elif snapshot["hermes"]["issues"]:
            snapshot["alerts"].append(("warn", "Hermes 已找到，但配置不完整，部分功能可能不可用。"))

        instances_cfg = (snapshot["global_config"].get("instances") or {}) if ws else {}
        if instances_cfg:
            for instance_id, cfg in sorted(instances_cfg.items()):
                if self.workspace_mode == "ssh":
                    instance_dir = ((remote_bundle or {}).get("instances") or {}).get(instance_id, {}).get("dir") or os.path.join(ws, "instances", instance_id)
                else:
                    instance_dir = cfg.get("working_dir") or os.path.join(ws, "instances", instance_id)
                snapshot["instances"].append(self.collect_instance_snapshot(instance_id, instance_dir))
        elif ws:
            snapshot["instances"].append(self.collect_instance_snapshot("default", ws))

        if snapshot["source"] == "wsl":
            snapshot["alerts"].append(("warn", "当前是 Linux / WSL 工作区连接模式。Windows 端主要用于查看状态、日志和项目进展。"))
        elif snapshot["source"] == "ssh":
            host = self.bridge_settings.get("ssh_host") or "SSH"
            snapshot["alerts"].append(("warn", f"当前是 SSH 服务器连接模式：{host}。状态、启停与日志预览都通过远端读取。"))
        return snapshot

    def collect_instance_snapshot(self, instance_id: str, instance_dir: str) -> InstanceSnapshot:
        if self.workspace_mode == "ssh":
            remote_item = (self.fetch_remote_bundle().get("instances") or {}).get(instance_id, {})
            plan = remote_item.get("plan") or {}
            heartbeat = remote_item.get("heartbeat") or {}
            active_project = remote_item.get("active_project") or {}
            knowledge = remote_item.get("knowledge") or {}
            summary_md = remote_item.get("summary") or ""
            journal_count = int(remote_item.get("journal_count") or 0)
            token_total = int(remote_item.get("token_total") or 0)
            token_today = int(remote_item.get("token_today") or 0)
        else:
            plan = load_json_file(os.path.join(instance_dir, "state", "active_plan.json"))
            heartbeat = load_json_file(os.path.join(instance_dir, "state", "heartbeat.json"))
            active_project = load_json_file(os.path.join(instance_dir, "20_records", "active_project.json"))
            knowledge = load_json_file(os.path.join(instance_dir, "state", "knowledge.json"))
            summary_md = read_text_file(os.path.join(instance_dir, "user", "current_project", "summary.md"))
            journal_count = count_jsonl_lines(os.path.join(instance_dir, "state", "journal.jsonl"))
            token_total, token_today = read_token_usage(instance_dir)

        phases = plan.get("phases") or []
        completed = sum(1 for p in phases if p.get("status") == "completed")
        total_phases = len(phases)
        focus = active_project.get("project_name") or plan.get("title") or plan.get("goal") or "尚未明确研究方向"
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
            "alive": ("在线", COLORS["green"]),
            "working": ("执行中", COLORS["green"]),
            "active": ("推进中", COLORS["green"]),
            "planning": ("规划中", COLORS["yellow"]),
            "completed": ("已完成", COLORS["accent_soft"]),
            "idle": ("空闲", COLORS["subtext"]),
        }
        status_text, status_color = status_map.get(status, (status, COLORS["subtext"]))
        if knowledge_entries >= 8 or completed >= 4:
            growth = "成长快，已形成稳定经验"
        elif knowledge_entries >= 3 or completed >= 2:
            growth = "持续积累中"
        elif journal_count > 0:
            growth = "刚起步，已有探索痕迹"
        else:
            growth = "尚未形成经验沉淀"
        is_active = False
        if self.workspace_mode == "ssh":
            remote_item = (self.fetch_remote_bundle().get("instances") or {}).get(instance_id, {})
            is_active = bool(remote_item.get("instance_running") or remote_item.get("qq_running"))
        if not is_active:
            hb = parse_iso(heartbeat.get("last_heartbeat") or plan.get("last_heartbeat"))
            if hb:
                now = datetime.now(hb.tzinfo) if hb.tzinfo else datetime.now()
                is_active = (now - hb).total_seconds() < 24 * 3600

        return InstanceSnapshot(
            id=instance_id,
            dir=instance_dir,
            focus=focus,
            status_text=status_text,
            status_color=status_color,
            is_active=is_active,
            current_action=current_action,
            last_seen=format_relative_time(heartbeat.get("last_heartbeat") or plan.get("last_heartbeat")),
            run_duration=format_duration(plan.get("created_at") or heartbeat.get("last_heartbeat")),
            cycle_count=heartbeat.get("cycle_count") or 0,
            crash_count=heartbeat.get("crash_count") or 0,
            progress_text=f"{completed}/{total_phases} 阶段" if total_phases else ("已完成" if plan.get("status") == "completed" else "未拆分阶段"),
            progress_pct=int((completed / total_phases) * 100) if total_phases else (100 if plan.get("status") == "completed" else 0),
            knowledge_entries=knowledge_entries or 0,
            journal_count=journal_count,
            growth=growth,
            token_total=token_total,
            token_today=token_today,
            summary=summarize_markdown(summary_md),
        )

    def refresh_all(self):
        self._remote_text_cache.clear()
        self._remote_user_file_list_cache.clear()
        if self.workspace_mode == "ssh":
            self.mode_label.setText(tr("mode_ssh", self.lang))
        elif self.workspace_mode == "wsl":
            self.mode_label.setText(tr("mode_wsl", self.lang))
        else:
            self.mode_label.setText(tr("mode_local", self.lang))
        current = self.stack.currentIndex() if hasattr(self, "stack") else 0
        if self.workspace_mode == "ssh":
            self.fetch_remote_bundle(force=True)
        self.refresh_dashboard()
        if current == 1:
            self.refresh_chat_page()
        elif current == 2:
            self.refresh_qq_page()
        elif current == 3:
            self.refresh_logs()

    def clear_layout(self, layout: QVBoxLayout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                self.clear_layout(child_layout)

    def clear_any_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                self.clear_any_layout(child_layout)

    def build_mini_pill(self, icon: str, text: str, color: str) -> QFrame:
        pill = QFrame()
        pill.setObjectName("MiniPill")
        layout = QHBoxLayout(pill)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)
        glyph = QLabel()
        glyph.setFixedWidth(20)
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setPixmap(self.qt_icon(icon).pixmap(16, 16))
        text_label = QLabel(text)
        text_label.setStyleSheet(f"font-size: 13px; color: {COLORS['text']}; font-weight: 600;")
        layout.addWidget(glyph)
        layout.addWidget(text_label)
        return pill

    def build_instance_dashboard_card(self, item: InstanceSnapshot) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        top_row = QHBoxLayout()
        badge = QLabel(f"实例 {item.id}")
        badge.setStyleSheet(
            f"background:{COLORS['muted']}; border:1px solid {COLORS['border']};"
            "border-radius:12px; padding:6px 10px; font-size:13px; font-weight:700;"
        )
        top_row.addWidget(badge)
        top_row.addStretch(1)
        state = QLabel(item.status_text)
        state.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {item.status_color};")
        top_row.addWidget(state)
        layout.addLayout(top_row)

        focus = QLabel(item.focus)
        focus.setWordWrap(True)
        focus.setStyleSheet("font-size: 20px; font-weight: 760;")
        layout.addWidget(focus)

        current = QLabel(item.current_action)
        current.setWordWrap(True)
        current.setStyleSheet(f"font-size: 14px; color: {COLORS['subtext']};")
        layout.addWidget(current)

        chips = QHBoxLayout()
        chips.setSpacing(10)
        chips.addWidget(self.build_mini_pill("runtime", item.run_duration, item.status_color))
        chips.addWidget(self.build_mini_pill("progress", item.progress_text, COLORS["accent"]))
        chips.addWidget(self.build_mini_pill("growth", item.growth.split("，")[0], COLORS["text"]))
        layout.addLayout(chips)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        stats.addWidget(MetricCard("心跳", item.last_seen, COLORS["text"], "heartbeat"))
        stats.addWidget(MetricCard("经验", str(item.knowledge_entries), COLORS["accent"], "knowledge"))
        stats.addWidget(MetricCard("探索", str(item.journal_count), COLORS["pink"], "records"))
        layout.addLayout(stats)
        return card

    def refresh_dashboard(self):
        snapshot = self.collect_dashboard_snapshot()
        self.clear_layout(self.dashboard_layout)

        instances = snapshot["instances"]
        active_instances = sum(1 for item in instances if item.is_active)
        total_tokens = sum(item.token_total for item in instances)
        today_tokens = sum(item.token_today for item in instances)

        hero = QFrame()
        hero.setObjectName("Hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(28, 24, 28, 24)
        hero_layout.setSpacing(16)
        title_row = QHBoxLayout()
        title = QLabel("研究伙伴总览")
        title.setStyleSheet(f"font-size: 24px; font-weight: 760; color: {COLORS['text']};")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self.build_mini_pill("hermes_ok" if snapshot["hermes"]["available"] else "hermes_bad", "Hermes 在线" if snapshot["hermes"]["available"] else "Hermes 缺失", COLORS["green"] if snapshot["hermes"]["available"] else COLORS["red"]))
        hero_layout.addLayout(title_row)
        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        metrics.addWidget(MetricCard("已配置", str(len(instances)), COLORS["text"], "configured"))
        metrics.addWidget(MetricCard("活跃中", str(active_instances), COLORS["green"], "active"))
        metrics.addWidget(MetricCard("累计 Token", format_tokens(total_tokens), COLORS["yellow"], "token"))
        metrics.addWidget(MetricCard("今日 Token", format_tokens(today_tokens), COLORS["pink"], "today"))
        hero_layout.addLayout(metrics)
        pulse_row = QHBoxLayout()
        pulse_row.setSpacing(12)
        pulse_row.addWidget(self.build_mini_pill("active", f"{active_instances} 个实例推进中", COLORS["green"]))
        pulse_row.addWidget(self.build_mini_pill("today", f"今日消耗 {format_tokens(today_tokens)}", COLORS["yellow"]))
        if instances:
            growth_count = sum(1 for item in instances if item.knowledge_entries > 0 or item.journal_count > 0)
            pulse_row.addWidget(self.build_mini_pill("growth", f"{growth_count} 个实例已有积累", COLORS["accent"]))
        hero_layout.addLayout(pulse_row)
        self.dashboard_layout.addWidget(hero)

        if instances:
            all_box = QGroupBox("全部实例")
            all_layout = QGridLayout(all_box)
            all_layout.setContentsMargins(18, 18, 18, 18)
            all_layout.setHorizontalSpacing(14)
            all_layout.setVerticalSpacing(14)
            for idx, item in enumerate(instances):
                all_layout.addWidget(self.build_instance_dashboard_card(item), idx // 2, idx % 2)
            self.dashboard_layout.addWidget(all_box)

        strip = QHBoxLayout()
        strip.setSpacing(12)
        hermes = snapshot["hermes"]
        strip.addWidget(MetricCard("Hermes", "已连接" if hermes["available"] else "缺失", COLORS["green"] if hermes["available"] else COLORS["red"], "hermes_ok" if hermes["available"] else "hermes_bad"))
        source_text = "SSH" if snapshot["source"] == "ssh" else ("WSL" if snapshot["source"] == "wsl" else "本地")
        source_icon = "source_wsl" if snapshot["source"] in {"wsl", "ssh"} else "source_local"
        strip.addWidget(MetricCard("来源", source_text, COLORS["accent"], source_icon))
        if instances:
            progressing = sum(1 for item in instances if item.progress_pct > 0 or item.progress_text != "未拆分阶段")
            strip.addWidget(MetricCard("推进中", str(progressing), COLORS["text"], "stage"))
        strip_wrap = QFrame()
        strip_wrap.setObjectName("Card")
        strip_wrap_layout = QVBoxLayout(strip_wrap)
        strip_wrap_layout.setContentsMargins(10, 10, 10, 10)
        strip_wrap_layout.addLayout(strip)
        self.dashboard_layout.addWidget(strip_wrap)

        if snapshot["alerts"]:
            alert_wrap = QFrame()
            alert_wrap.setObjectName("Card")
            alert_layout = QVBoxLayout(alert_wrap)
            alert_layout.setContentsMargins(18, 16, 18, 16)
            for level, text in snapshot["alerts"][:2]:
                warn = QLabel(text)
                warn.setWordWrap(True)
                warn.setStyleSheet(f"color: {COLORS['red'] if level == 'error' else COLORS['yellow']}; font-size: 13px;")
                alert_layout.addWidget(warn)
            self.dashboard_layout.addWidget(alert_wrap)

        self.dashboard_layout.addStretch(1)
        dot_color = COLORS["green"] if hermes["available"] and active_instances else (COLORS["yellow"] if hermes["available"] else COLORS["red"])
        self._status_dot_color = dot_color
        self.status_dot.setStyleSheet(f"color: {dot_color}; font-size: 18px;")
        self.status_text.setText(f"刷新于 {datetime.now().strftime('%H:%M:%S')}")

    def refresh_chat_page(self):
        self.populate_chat_instances()
        self.chat_view.clear()
        inst_id, inst_dir = self.current_chat_instance()
        target_text = f"当前发送给实例 {inst_id}" if inst_id else "未选择实例"
        if hasattr(self, "chat_target_hint"):
            self.chat_target_hint.setText(target_text if inst_dir else "未选择实例")
        if self.workspace_mode == "wsl":
            mode_text = "Linux / WSL"
            self.chat_view.setHtml(
                f"<p style='color:{COLORS['yellow']};'>当前连接的是 {mode_text} 工作区。</p>"
                f"<p>当前目标：<b>{inst_id or '-'}</b></p>"
                f"<p style='color:{COLORS['subtext']};'>Windows 桌面端当前以状态查看和运维控制为主，不直接接管聊天。</p>"
            )
            self.chat_input.setEnabled(False)
            self.chat_send_btn.setEnabled(False)
            return
        self.chat_input.setEnabled(True)
        self.chat_send_btn.setEnabled(True)
        if not self.workspace:
            self.chat_view.setPlainText("尚未配置工作区。")
            return
        if not inst_dir:
            self.chat_view.setPlainText("当前没有可用实例。")
            self.chat_input.setEnabled(False)
            self.chat_send_btn.setEnabled(False)
            return
        if self.workspace_mode == "ssh":
            turns = self.remote_dialog_history(inst_dir, n=CHAT_HISTORY_LIMIT)
            self.chat_view.setHtml(
                f"<p style='color:{COLORS['subtext']};'>当前目标：<b>实例 {inst_id}</b>。发送消息后会写入远端任务队列，由该实例后台消费。</p>"
            )
        else:
            turns = load_dialog_history(inst_dir, n=CHAT_HISTORY_LIMIT)
        if not turns:
            greeting = f"<p>嗨！这是实例 <b>{inst_id}</b> 的对话页。</p>"
            if self.workspace_mode == "ssh":
                greeting += "<p style='color:#6b7788;'>当前还没有远端对话记录。你发出的消息会进入该实例的待处理队列。</p>"
            self.chat_view.setHtml(greeting)
            return
        for turn in turns:
            role = "你" if turn.get("role") == "user" else "Partner"
            color = COLORS["accent_soft"] if role == "你" else COLORS["text"]
            content = turn.get("content", "").replace("\n", "<br>")
            self.chat_view.append(f"<p><b style='color:{color}'>{role}</b><br>{content}</p>")

    def append_chat_message(self, role: str, text: str):
        color = COLORS["accent_soft"] if role == "user" else COLORS["text"]
        label = "你" if role == "user" else "Partner"
        self.chat_view.append(f"<p><b style='color:{color}'>{label}</b><br>{text.replace(chr(10), '<br>')}</p>")

    def send_chat(self):
        if self.workspace_mode == "wsl":
            self.append_chat_message("bot", tr("chat_remote_readonly", self.lang))
            return
        text = self.chat_input.text().strip()
        if not text:
            return
        inst_id, inst_dir = self.current_chat_instance()
        if not inst_dir:
            self.append_chat_message("bot", "当前没有可发送消息的实例。")
            return
        self.chat_input.clear()
        self.append_chat_message("user", text)
        if self.workspace_mode == "ssh":
            ok, msg = self.enqueue_remote_chat_message(inst_id, inst_dir, text)
            self.append_chat_message("bot", msg if ok else f"远端投递失败：{msg}")
            return
        self.append_chat_message("bot", "Partner 正在思考中…")
        worker = ChatWorker(inst_dir, text)
        self.chat_worker = worker

        def _run():
            worker.run()

        worker.finished.connect(self.on_chat_finished)
        threading.Thread(target=_run, daemon=True).start()

    def on_chat_finished(self, status: str, payload: str):
        cursor = self.chat_view.textCursor()
        cursor.movePosition(cursor.End)
        self.chat_view.setTextCursor(cursor)
        # Drop the last thinking paragraph by rebuilding content if needed.
        self.refresh_chat_page()
        self.append_chat_message("bot", payload if status == "ok" else f"暂时无法处理这条消息。\n\n({payload[:120]})")

    def get_instance_root_and_config(self):
        ws = self.workspace or ""
        global_cfg_path = os.path.join(ws, "global_config.json")
        if self.workspace_mode == "ssh":
            return ws, (self.fetch_remote_bundle().get("global_config") or {}), global_cfg_path
        if os.path.exists(global_cfg_path):
            return ws, load_json_file(global_cfg_path), global_cfg_path
        return ws, {}, global_cfg_path

    def global_config(self) -> dict:
        _, cfg, _ = self.get_instance_root_and_config()
        return cfg or {}

    def control_backend(self) -> str:
        if self.workspace_mode == "ssh":
            return "ssh"
        cfg = self.global_config()
        python_cmd = str(cfg.get("python_cmd") or "")
        partner_dir = str(cfg.get("partner_dir") or "")
        if self.workspace_mode == "wsl" or python_cmd.startswith("/") or partner_dir.startswith("/"):
            return "wsl"
        return "local"

    def control_distro(self) -> str:
        return (self.bridge_settings.get("wsl_distro") or (detect_wsl_distros()[0] if detect_wsl_distros() else "")).strip()

    def workspace_python_cmd(self) -> str:
        if self.workspace_mode == "ssh":
            return str(self.bridge_settings.get("ssh_python") or "python3")
        cfg = self.global_config()
        return str(cfg.get("python_cmd") or sys.executable)

    def workspace_partner_dir(self) -> str:
        if self.workspace_mode == "ssh":
            return str(self.bridge_settings.get("ssh_partner_dir") or "/home/ubuntu/partner")
        cfg = self.global_config()
        return str(cfg.get("partner_dir") or PARTNER_DIR)

    def instance_pid_path(self, instance_dir: str) -> str:
        return os.path.join(instance_dir, "instance.pid")

    def bot_pid_path(self, instance_dir: str) -> str:
        return os.path.join(instance_dir, "state", "qq_bot.pid")

    def instance_config_path(self, instance_dir: str) -> str:
        primary = os.path.join(instance_dir, "00_config", "qq_config.json")
        legacy = os.path.join(instance_dir, "qq_config.json")
        if self.workspace_mode == "ssh":
            return primary if self.remote_exists(primary) else legacy
        return primary if os.path.exists(primary) else legacy

    def run_workspace_command(self, command: str, capture: bool = False) -> tuple[bool, str]:
        backend = self.control_backend()
        if backend == "ssh":
            return self.run_ssh(command, capture=capture)
        if backend == "wsl":
            distro = self.control_distro()
            if not distro:
                return False, "未配置 WSL 发行版，无法执行 Linux 侧控制命令。"
            cmd = ["wsl.exe", "-d", distro, "bash", "-lc", command]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=capture,
                    text=True,
                    creationflags=CREATION_FLAGS,
                    timeout=20,
                )
                output = (result.stdout or result.stderr or "").strip()
                return result.returncode == 0, output
            except Exception as exc:
                return False, str(exc)
        try:
            result = subprocess.run(
                command,
                capture_output=capture,
                text=True,
                cwd=self.workspace_partner_dir(),
                shell=True,
                creationflags=CREATION_FLAGS,
                timeout=20,
            )
            output = (result.stdout or result.stderr or "").strip()
            return result.returncode == 0, output
        except Exception as exc:
            return False, str(exc)

    def instance_process_running(self, instance_id: str, instance_dir: str) -> bool:
        if self.workspace_mode == "ssh":
            item = (self.fetch_remote_bundle().get("instances") or {}).get(instance_id) or {}
            if item.get("instance_running"):
                return True
            heartbeat = item.get("heartbeat") or {}
            dt = parse_iso((heartbeat.get("last_heartbeat") or "")) if heartbeat else None
            if dt:
                now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                return (now - dt).total_seconds() < 600
            return False
        pid_path = self.instance_pid_path(instance_dir)
        pid_exists = self.remote_exists(pid_path) if self.workspace_mode == "ssh" else os.path.exists(pid_path)
        if pid_exists:
            try:
                pid_text = self.remote_text(pid_path).strip() if self.workspace_mode == "ssh" else read_text_file(pid_path)
                pid = int(pid_text or "0")
            except ValueError:
                pid = 0
            if pid:
                if self.control_backend() in {"wsl", "ssh"}:
                    ok, _ = self.run_workspace_command(f"kill -0 {pid}")
                    if ok:
                        return True
                elif pid_is_alive(pid):
                    return True
        heartbeat = load_json_file(os.path.join(instance_dir, "state", "heartbeat.json"))
        dt = parse_iso((heartbeat.get("last_heartbeat") or "")) if heartbeat else None
        if dt:
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            return (now - dt).total_seconds() < 600
        return False

    def qq_bot_running(self, instance_dir: str) -> bool:
        if self.workspace_mode == "ssh":
            inst_id = os.path.basename(instance_dir.rstrip("/\\"))
            item = (self.fetch_remote_bundle().get("instances") or {}).get(inst_id) or {}
            return bool(item.get("qq_running"))
        pid_path = self.bot_pid_path(instance_dir)
        pid_exists = self.remote_exists(pid_path) if self.workspace_mode == "ssh" else os.path.exists(pid_path)
        if not pid_exists:
            return False
        try:
            pid_text = self.remote_text(pid_path).strip() if self.workspace_mode == "ssh" else read_text_file(pid_path)
            pid = int(pid_text or "0")
        except ValueError:
            return False
        if not pid:
            return False
        if self.control_backend() in {"wsl", "ssh"}:
            ok, _ = self.run_workspace_command(f"kill -0 {pid}")
            return ok
        return pid_is_alive(pid)

    def start_instance_runtime(self, instance_id: str, instance_dir: str) -> tuple[bool, str]:
        if self.instance_process_running(instance_id, instance_dir):
            return True, "实例已经在运行。"
        log_path = os.path.join(instance_dir, "10_logs", "instance.log")
        if self.control_backend() in {"wsl", "ssh"}:
            python_cmd = shlex.quote(self.workspace_python_cmd())
            partner_dir = shlex.quote(self.workspace_partner_dir())
            workspace = shlex.quote(instance_dir)
            inst = shlex.quote(instance_id)
            log = shlex.quote(log_path)
            pidfile = shlex.quote(self.instance_pid_path(instance_dir))
            mkdir = shlex.quote(os.path.dirname(log_path))
            if self.control_backend() == "ssh":
                cmd = f"mkdir -p {mkdir} && cd {partner_dir} && nohup {python_cmd} -m partner --instance-id {inst} --workspace {workspace} >> {log} 2>&1 & echo $! > {pidfile}"
            else:
                cmd = f"cd {partner_dir} && nohup {python_cmd} -m partner --instance-id {inst} --workspace {workspace} >> {log} 2>&1 & echo $! > {pidfile}"
            ok, out = self.run_workspace_command(cmd, capture=True)
            return ok, out or ("实例已启动。" if ok else "实例启动失败。")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        cmd = [sys.executable, "-m", "partner", "--instance-id", instance_id, "--workspace", instance_dir]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.workspace_partner_dir(),
                stdout=open(log_path, "a"),
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                creationflags=CREATION_FLAGS,
            )
            with open(self.instance_pid_path(instance_dir), "w", encoding="utf-8") as f:
                f.write(str(proc.pid))
            return True, f"实例已启动 (PID {proc.pid})。"
        except Exception as exc:
            return False, str(exc)

    def stop_instance_runtime(self, instance_id: str, instance_dir: str) -> tuple[bool, str]:
        pid_path = self.instance_pid_path(instance_dir)
        pid_exists = self.remote_exists(pid_path) if self.workspace_mode == "ssh" else os.path.exists(pid_path)
        pid = int((self.remote_text(pid_path).strip() if self.workspace_mode == "ssh" else read_text_file(pid_path)) or "0") if pid_exists else 0
        if self.control_backend() in {"wsl", "ssh"}:
            if pid:
                ok, out = self.run_workspace_command(f"kill {pid} && rm -f {shlex.quote(pid_path)}")
                return ok, out or ("实例已停止。" if ok else "实例停止失败。")
            return False, "实例没有运行中的 PID。"
        if pid and pid_is_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                if os.path.exists(pid_path):
                    os.remove(pid_path)
                return True, "实例已停止。"
            except OSError as exc:
                return False, str(exc)
        return False, "实例没有运行中的 PID。"

    def start_bot_runtime(self, instance_dir: str) -> tuple[bool, str]:
        cfg_path = self.instance_config_path(instance_dir)
        cfg_exists = self.remote_exists(cfg_path) if self.workspace_mode == "ssh" else os.path.exists(cfg_path)
        if not cfg_exists:
            return False, "当前实例还没有 QQ 机器人配置。"
        if self.qq_bot_running(instance_dir):
            return True, "QQ 机器人已经在运行。"
        if self.control_backend() in {"wsl", "ssh"}:
            python_cmd = shlex.quote(self.workspace_python_cmd())
            partner_dir = shlex.quote(self.workspace_partner_dir())
            workspace = shlex.quote(instance_dir)
            cmd = f"cd {partner_dir} && {python_cmd} -m partner bot start qq --workspace {workspace}"
            ok, out = self.run_workspace_command(cmd, capture=True)
            return ok, out or ("QQ 机器人已启动。" if ok else "QQ 机器人启动失败。")
        cmd = [sys.executable, "-m", "partner", "bot", "start", "qq", "--workspace", instance_dir]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.workspace_partner_dir(),
                creationflags=CREATION_FLAGS,
                timeout=20,
            )
            output = (result.stdout or result.stderr or "").strip()
            return result.returncode == 0, output or ("QQ 机器人已启动。" if result.returncode == 0 else "QQ 机器人启动失败。")
        except Exception as exc:
            return False, str(exc)

    def stop_bot_runtime(self, instance_dir: str) -> tuple[bool, str]:
        if self.control_backend() in {"wsl", "ssh"}:
            python_cmd = shlex.quote(self.workspace_python_cmd())
            partner_dir = shlex.quote(self.workspace_partner_dir())
            workspace = shlex.quote(instance_dir)
            cmd = f"cd {partner_dir} && {python_cmd} -m partner bot stop qq --workspace {workspace}"
            ok, out = self.run_workspace_command(cmd, capture=True)
            return ok, out or ("QQ 机器人已停止。" if ok else "QQ 机器人停止失败。")
        cmd = [sys.executable, "-m", "partner", "bot", "stop", "qq", "--workspace", instance_dir]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.workspace_partner_dir(),
                creationflags=CREATION_FLAGS,
                timeout=20,
            )
            output = (result.stdout or result.stderr or "").strip()
            return result.returncode == 0, output or ("QQ 机器人已停止。" if result.returncode == 0 else "QQ 机器人停止失败。")
        except Exception as exc:
            return False, str(exc)

    def available_instances(self):
        root, cfg, _ = self.get_instance_root_and_config()
        instances = []
        if isinstance(cfg.get("instances"), dict) and cfg.get("instances"):
            for inst_id, meta in sorted(cfg["instances"].items()):
                if self.workspace_mode == "ssh":
                    instances.append((inst_id, remote_path_join(root, "instances", inst_id)))
                else:
                    instances.append((inst_id, meta.get("working_dir") or os.path.join(root, "instances", inst_id)))
            return instances
        if self.workspace_mode == "ssh":
            remote_instances = (self.fetch_remote_bundle().get("instances") or {})
            if remote_instances:
                return [
                    (inst_id, item.get("dir") or remote_path_join(root, "instances", inst_id))
                    for inst_id, item in sorted(remote_instances.items())
                ]
        if root:
            return [("default", root)]
        return []

    def selected_instance(self):
        item = self.instance_list.currentItem() if hasattr(self, "instance_list") else None
        if not item:
            instances = self.available_instances()
            return instances[0] if instances else (None, None)
        return item.data(Qt.UserRole)

    def populate_chat_instances(self):
        if not hasattr(self, "chat_instance_combo"):
            return
        current_id, _ = self.current_chat_instance()
        instances = self.available_instances()
        self.chat_instance_combo.blockSignals(True)
        self.chat_instance_combo.clear()
        for inst_id, inst_dir in instances:
            self.chat_instance_combo.addItem(f"实例 {inst_id}", (inst_id, inst_dir))
        if instances:
            target_index = 0
            if current_id:
                for idx, (inst_id, _) in enumerate(instances):
                    if inst_id == current_id:
                        target_index = idx
                        break
            self.chat_instance_combo.setCurrentIndex(target_index)
        self.chat_instance_combo.blockSignals(False)

    def current_chat_instance(self):
        if hasattr(self, "chat_instance_combo") and self.chat_instance_combo.count():
            data = self.chat_instance_combo.currentData()
            if data:
                return data
        instances = self.available_instances()
        return instances[0] if instances else (None, None)

    def populate_log_instances(self):
        if not hasattr(self, "log_instance_combo"):
            return
        current_id, _ = self.current_log_instance()
        instances = self.available_instances()
        self.log_instance_combo.blockSignals(True)
        self.log_instance_combo.clear()
        for inst_id, inst_dir in instances:
            self.log_instance_combo.addItem(f"实例 {inst_id}", (inst_id, inst_dir))
        if instances:
            target_index = 0
            if current_id:
                for idx, (inst_id, _) in enumerate(instances):
                    if inst_id == current_id:
                        target_index = idx
                        break
            self.log_instance_combo.setCurrentIndex(target_index)
        self.log_instance_combo.blockSignals(False)

    def current_log_instance(self):
        if hasattr(self, "log_instance_combo") and self.log_instance_combo.count():
            data = self.log_instance_combo.currentData()
            if data:
                return data
        instances = self.available_instances()
        return instances[0] if instances else (None, None)

    def resolve_log_root(self, instance_dir: str, root_name: str) -> tuple[str, bool]:
        if root_name == "user":
            return (remote_path_join(instance_dir, "user") if self.workspace_mode == "ssh" else os.path.join(instance_dir, "user")), True
        return (remote_path_join(instance_dir, "user") if self.workspace_mode == "ssh" else os.path.join(instance_dir, "user")), True

    def on_chat_instance_changed(self, index: int):
        inst_id, inst_dir = self.current_chat_instance()
        if hasattr(self, "chat_target_hint"):
            self.chat_target_hint.setText(f"当前发送给实例 {inst_id}" if inst_id else "未选择实例")
        if self.stack.currentIndex() == 1:
            self.refresh_chat_page()

    def load_bot_configs(self, instance_dir: str):
        bots = []
        path = os.path.join(instance_dir, "qq_configs.json")
        if self.workspace_mode == "ssh":
            bundle = self.fetch_remote_bundle()
            inst_id = os.path.basename(instance_dir)
            data = ((bundle.get("instances") or {}).get(inst_id) or {}).get("bots") or []
        else:
            data = load_json_file(path)
        if isinstance(data, list):
            bots = data
        else:
            primary = os.path.join(instance_dir, "00_config", "qq_config.json")
            legacy = os.path.join(instance_dir, "qq_config.json")
            single = self.remote_json(primary) if self.workspace_mode == "ssh" else load_json_file(primary)
            if not single:
                single = self.remote_json(legacy) if self.workspace_mode == "ssh" else load_json_file(legacy)
            if single:
                bots = [single]
        return bots, path

    def save_bot_configs(self, instance_dir: str, bots: list[dict]):
        path = os.path.join(instance_dir, "qq_configs.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bots, f, ensure_ascii=False, indent=2)

    def refresh_qq_page(self):
        remote_managed = self.workspace_mode == "ssh"
        if hasattr(self, "qq_source_label"):
            if self.workspace_mode == "ssh":
                host = self.bridge_settings.get("ssh_host") or "未知服务器"
                ws = self.bridge_settings.get("ssh_workspace") or (self.workspace or "")
                self.qq_source_label.setText(f"数据来源：服务器 {host}  ·  工作区 {ws}")
            elif self.workspace_mode == "wsl":
                self.qq_source_label.setText(f"数据来源：Linux / WSL  ·  工作区 {self.workspace or '-'}")
            else:
                self.qq_source_label.setText(f"数据来源：Windows 本地  ·  工作区 {self.workspace or '-'}")
        if hasattr(self, "add_instance_btn"):
            self.add_instance_btn.setEnabled(not remote_managed)
            self.del_instance_btn.setEnabled(not remote_managed)
            self.add_bot_btn.setEnabled(not remote_managed)
            self.config_bot_btn.setEnabled(not remote_managed)
            self.del_bot_btn.setEnabled(not remote_managed)
        self.instance_list.clear()
        for inst_id, inst_dir in self.available_instances():
            running = self.instance_process_running(inst_id, inst_dir)
            state = "运行中" if running else "已停止"
            item = QListWidgetItem(f"{inst_id}  ·  {state}")
            item.setData(Qt.UserRole, (inst_id, inst_dir))
            self.instance_list.addItem(item)
        if self.instance_list.count():
            self.instance_list.setCurrentRow(0)
        elif remote_managed:
            bundle = self.fetch_remote_bundle()
            message = bundle.get("error") or "远端工作区没有读到实例配置。"
            self.qq_info.setPlainText(f"SSH 服务器已连接，但实例列表为空。\n\n原因: {message}")

    def on_instance_selected(self, current, previous=None):
        self.bot_list.clear()
        if not current:
            return
        inst_id, inst_dir = current.data(Qt.UserRole)
        if hasattr(self, "chat_instance_combo") and self.chat_instance_combo.count():
            for idx in range(self.chat_instance_combo.count()):
                data = self.chat_instance_combo.itemData(idx)
                if data and data[0] == inst_id:
                    self.chat_instance_combo.blockSignals(True)
                    self.chat_instance_combo.setCurrentIndex(idx)
                    self.chat_instance_combo.blockSignals(False)
                    if hasattr(self, "chat_target_hint"):
                        self.chat_target_hint.setText(f"当前发送给实例 {inst_id}")
                    break
        if hasattr(self, "log_instance_combo") and self.log_instance_combo.count():
            for idx in range(self.log_instance_combo.count()):
                data = self.log_instance_combo.itemData(idx)
                if data and data[0] == inst_id:
                    self.log_instance_combo.blockSignals(True)
                    self.log_instance_combo.setCurrentIndex(idx)
                    self.log_instance_combo.blockSignals(False)
                    break
        instance_running = self.instance_process_running(inst_id, inst_dir)
        bots, _ = self.load_bot_configs(inst_dir)
        for idx, bot in enumerate(bots):
            name = bot.get("name") or f"Bot {idx+1}"
            bot_state = "运行中" if self.qq_bot_running(inst_dir) else "已停止"
            item = QListWidgetItem(f"{name}  ·  {bot_state}")
            item.setData(Qt.UserRole, (idx, bot))
            self.bot_list.addItem(item)
        self.instance_status.setText(f"实例状态：{'运行中' if instance_running else '已停止'}")
        self.bot_status.setText(f"机器人状态：{'运行中' if self.qq_bot_running(inst_dir) else '已停止'}")
        self.qq_info.setPlainText(
            f"实例: {inst_id}\n"
            f"目录: {inst_dir}\n"
            f"运行状态: {'运行中' if instance_running else '已停止'}\n"
            f"机器人数量: {len(bots)}\n"
            f"控制后端: {'SSH' if self.control_backend() == 'ssh' else ('WSL' if self.control_backend() == 'wsl' else 'Windows')}\n"
            f"数据来源: {'服务器 ' + str(self.bridge_settings.get('ssh_host')) if self.workspace_mode == 'ssh' else ('Linux / WSL' if self.workspace_mode == 'wsl' else 'Windows 本地')}"
        )
        if self.bot_list.count():
            self.bot_list.setCurrentRow(0)

    def on_bot_selected(self, current, previous=None):
        inst_id, inst_dir = self.selected_instance()
        if not current or not inst_dir:
            return
        idx, bot = current.data(Qt.UserRole)
        running = self.qq_bot_running(inst_dir)
        self.bot_status.setText(f"机器人状态：{'运行中' if running else '已停止'}")
        self.qq_info.setPlainText(
            f"实例: {inst_id}\n"
            f"机器人: {bot.get('name') or f'Bot {idx+1}'}\n"
            f"AppID: {bot.get('app_id', '')}\n"
            f"Sandbox: {bot.get('is_sandbox', False)}\n"
            f"运行状态: {'运行中' if running else '已停止'}"
        )

    def start_selected_instance(self):
        inst_id, inst_dir = self.selected_instance()
        if not inst_id or not inst_dir:
            return
        ok, msg = self.start_instance_runtime(inst_id, inst_dir)
        if not ok:
            QMessageBox.warning(self, "Partner", msg or "实例启动失败。")
        self.refresh_qq_page()

    def stop_selected_instance(self):
        inst_id, inst_dir = self.selected_instance()
        if not inst_id or not inst_dir:
            return
        ok, msg = self.stop_instance_runtime(inst_id, inst_dir)
        if not ok:
            QMessageBox.warning(self, "Partner", msg or "实例停止失败。")
        self.refresh_qq_page()

    def start_selected_bot(self):
        inst_id, inst_dir = self.selected_instance()
        if not inst_dir:
            return
        ok, msg = self.start_bot_runtime(inst_dir)
        if not ok:
            QMessageBox.warning(self, "Partner", msg or "QQ 机器人启动失败。")
        self.refresh_qq_page()

    def stop_selected_bot(self):
        inst_id, inst_dir = self.selected_instance()
        if not inst_dir:
            return
        ok, msg = self.stop_bot_runtime(inst_dir)
        if not ok:
            QMessageBox.warning(self, "Partner", msg or "QQ 机器人停止失败。")
        self.refresh_qq_page()

    def add_instance(self):
        root, cfg, cfg_path = self.get_instance_root_and_config()
        if not root:
            QMessageBox.warning(self, "Partner", "当前工作区不可用。")
            return
        instance_id, ok = QInputDialog.getText(self, "新增实例", "实例 ID")
        if not ok or not instance_id.strip():
            return
        instance_id = instance_id.strip()
        inst_dir = os.path.join(root, "instances", instance_id)
        os.makedirs(inst_dir, exist_ok=True)
        for sub in ["00_config", "10_logs", "20_records", "logs", "state", "system", "99_temp"]:
            os.makedirs(os.path.join(inst_dir, sub), exist_ok=True)
        cfg.setdefault("instances", {})
        cfg["instances"][instance_id] = {
            "enabled": True,
            "working_dir": inst_dir,
            "qq_config": "00_config/qq_config.json",
            "agent_backend": "hermes",
            "interval_minutes": 30,
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self.refresh_qq_page()

    def delete_instance(self):
        inst_id, inst_dir = self.selected_instance()
        if not inst_id or inst_id == "default":
            QMessageBox.warning(self, "Partner", "默认实例不能在这里删除。")
            return
        if QMessageBox.question(self, "删除实例", f"确定删除实例 {inst_id} 吗？") != QMessageBox.Yes:
            return
        root, cfg, cfg_path = self.get_instance_root_and_config()
        if isinstance(cfg.get("instances"), dict):
            cfg["instances"].pop(inst_id, None)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        shutil.rmtree(inst_dir, ignore_errors=True)
        self.refresh_qq_page()

    def add_bot(self):
        inst_id, inst_dir = self.selected_instance()
        if not inst_dir:
            return
        name, ok = QInputDialog.getText(self, "新增机器人", "机器人名称")
        if not ok:
            return
        appid, ok = QInputDialog.getText(self, "新增机器人", "AppID")
        if not ok:
            return
        secret, ok = QInputDialog.getText(self, "新增机器人", "AppSecret")
        if not ok:
            return
        bots, _ = self.load_bot_configs(inst_dir)
        bots.append({"name": name.strip() or f"Bot {len(bots)+1}", "app_id": appid.strip(), "app_secret": secret.strip(), "mode": "official", "is_sandbox": True})
        self.save_bot_configs(inst_dir, bots)
        self.on_instance_selected(self.instance_list.currentItem())

    def configure_bot(self):
        inst_id, inst_dir = self.selected_instance()
        current = self.bot_list.currentItem()
        if not inst_dir or not current:
            return
        idx, bot = current.data(Qt.UserRole)
        name, ok = QInputDialog.getText(self, "配置机器人", "机器人名称", text=bot.get("name", ""))
        if not ok:
            return
        appid, ok = QInputDialog.getText(self, "配置机器人", "AppID", text=bot.get("app_id", ""))
        if not ok:
            return
        secret, ok = QInputDialog.getText(self, "配置机器人", "AppSecret", text=bot.get("app_secret", ""))
        if not ok:
            return
        bots, _ = self.load_bot_configs(inst_dir)
        if 0 <= idx < len(bots):
            bots[idx].update({"name": name.strip(), "app_id": appid.strip(), "app_secret": secret.strip()})
        self.save_bot_configs(inst_dir, bots)
        self.on_instance_selected(self.instance_list.currentItem())

    def delete_bot(self):
        inst_id, inst_dir = self.selected_instance()
        current = self.bot_list.currentItem()
        if not inst_dir or not current:
            return
        idx, bot = current.data(Qt.UserRole)
        if QMessageBox.question(self, "删除机器人", f"确定删除机器人 {bot.get('name') or idx+1} 吗？") != QMessageBox.Yes:
            return
        bots, _ = self.load_bot_configs(inst_dir)
        if 0 <= idx < len(bots):
            bots.pop(idx)
        self.save_bot_configs(inst_dir, bots)
        self.on_instance_selected(self.instance_list.currentItem())

    def refresh_logs(self):
        self.populate_log_instances()
        self.log_list.clear()
        self.log_view.clear()
        self.clear_any_layout(self.log_metrics)
        if hasattr(self, "log_preview_title"):
            self.log_preview_title.setText("选择 user 文件查看详情")
        if not self.workspace:
            self.log_list.addItem("未配置工作区")
            return
        inst_id, inst_dir = self.current_log_instance()
        if not inst_dir:
            self.log_list.addItem("没有可用实例")
            return
        root_name = self.log_root_combo.currentText()
        if hasattr(self, "log_breadcrumb"):
            self.log_breadcrumb.setText(f"{inst_id} / {root_name}")
        total_items = 0
        target_path, is_dir_root = self.resolve_log_root(inst_dir, root_name)
        base = target_path
        if self.workspace_mode == "ssh":
            entries = self.remote_walk_user_files(base)
        else:
            if not os.path.isdir(base):
                self.log_list.addItem("暂无目录")
                return
            entries = self.local_walk_user_files(base)
        if not entries:
            self.log_list.addItem("暂无记录")
            return
        total_items = len(entries)
        for rel_path in entries:
            full = remote_path_join(base, rel_path) if self.workspace_mode == "ssh" else os.path.join(base, rel_path)
            item = QListWidgetItem(self.qt_icon("file"), rel_path)
            item.setData(Qt.UserRole, full)
            self.log_list.addItem(item)

        self.log_metrics.addWidget(MetricCard("实例", str(inst_id), COLORS["accent"], "instances"))
        self.log_metrics.addWidget(MetricCard("目录", root_name, COLORS["accent"], "logs"))
        self.log_metrics.addWidget(MetricCard("条目", str(total_items), COLORS["text"], "configured"))
        source_label = "SSH" if self.workspace_mode == "ssh" else ("WSL" if self.workspace_mode == "wsl" else "本地")
        source_icon = "source_wsl" if self.workspace_mode in {"ssh", "wsl"} else "source_local"
        self.log_metrics.addWidget(MetricCard("工作区", source_label, COLORS["green"], source_icon))

    def show_log_item(self, current: QListWidgetItem, previous: QListWidgetItem = None):
        if not current:
            return
        payload = current.data(Qt.UserRole)
        if hasattr(self, "log_preview_title"):
            self.log_preview_title.setText(current.text())
        if not payload:
            self.log_view.setPlainText(current.text())
            return
        if self.workspace_mode == "ssh":
            content = self.remote_text(str(payload))
            self.log_view.setPlainText(content or "(空文件)")
            if hasattr(self, "log_breadcrumb"):
                self.log_breadcrumb.setText(str(payload))
        elif os.path.isfile(str(payload)):
            self.log_view.setPlainText(read_text_file(str(payload)) or "(空文件)")
            if hasattr(self, "log_breadcrumb"):
                self.log_breadcrumb.setText(str(payload))
        else:
            self.log_view.setPlainText(str(payload))
            if hasattr(self, "log_breadcrumb"):
                self.log_breadcrumb.setText(str(payload))


def launch():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Partner")
    app.setOrganizationName("Partner")
    font = QFont("Segoe UI Variable Text", 12)
    app.setFont(font)
    window = PartnerQtWindow()
    screen = app.primaryScreen()
    if screen:
        geo = screen.availableGeometry()
        x = geo.x() + max(20, (geo.width() - window.width()) // 2)
        y = geo.y() + max(20, (geo.height() - window.height()) // 2)
        window.move(x, y)
    window.show()
    return app.exec()
