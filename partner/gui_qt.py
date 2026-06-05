#!/usr/bin/env python3
"""Partner desktop GUI built with PySide6.

This module is intentionally self-contained so Windows can prefer a modern Qt
desktop UI while the older tkinter UI remains available as fallback.
"""

from __future__ import annotations

import csv
import base64
import ctypes
import html
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

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint, QSize, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
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
    QMenu,
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QScrollArea,
    QGraphicsDropShadowEffect,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


APP_DIR = os.path.dirname(os.path.abspath(__file__))
PARTNER_DIR = os.path.dirname(APP_DIR)
ICON_DIR = os.path.join(APP_DIR, "assets", "icons")
CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
AUTO_REFRESH_INTERVAL_MS = 15000
CHAT_HISTORY_LIMIT = 30
APP_ICON_PATH = os.path.join(APP_DIR, "assets", "partner_app_v2.ico")
PARTNER_CONFIG_SCHEMA_VERSION = "0.7.0-config-v1"
MIN_PAGE_LOADING_MS = 1000

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
    "settings": "settings.svg",
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
    "chevron_right": "chevron_right.svg",
    "ollama": "ollama.svg",
}


def icon_path(key: str) -> str:
    return os.path.join(ICON_DIR, SVG_ICONS.get(key, "file.svg"))


def icon_url(key: str) -> str:
    return icon_path(key).replace("\\", "/")


def load_svg_icon(key: str) -> QIcon:
    return QIcon(icon_path(key))


def set_windows_app_id():
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Partner.Desktop")
    except Exception:
        pass


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
        top: -2px;
        padding: 0 8px;
        background: {COLORS['panel_alt']};
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


def show_partner_notice(parent, title: str, message: str, kind: str = "warning") -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title or "Partner")
    dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dialog.setAttribute(Qt.WA_TranslucentBackground, True)
    dialog.setModal(True)
    dialog.setStyleSheet(dialog_stylesheet())

    root = QVBoxLayout(dialog)
    root.setContentsMargins(18, 18, 18, 18)
    shell = QFrame()
    shell.setObjectName("Card")
    root.addWidget(shell)

    layout = QVBoxLayout(shell)
    layout.setContentsMargins(24, 22, 24, 20)
    layout.setSpacing(16)

    head = QHBoxLayout()
    icon = QLabel()
    icon_key = "hermes_bad" if kind == "warning" else "hermes_ok"
    icon.setPixmap(load_svg_icon(icon_key).pixmap(20, 20))
    title_label = QLabel(title or "Partner")
    title_label.setStyleSheet("font-size: 18px; font-weight: 780;")
    head.addWidget(icon)
    head.addWidget(title_label)
    head.addStretch(1)
    layout.addLayout(head)

    body = QLabel(str(message or ""))
    body.setWordWrap(True)
    body.setStyleSheet(f"color: {COLORS['subtext']}; font-size: 14px; line-height: 1.55;")
    layout.addWidget(body)

    footer = QHBoxLayout()
    ok_btn = QPushButton("知道了")
    ok_btn.setObjectName("PrimaryAction")
    ok_btn.clicked.connect(dialog.accept)
    footer.addStretch(1)
    footer.addWidget(ok_btn)
    layout.addLayout(footer)

    dialog.resize(460, 210)
    dialog.exec()


def ask_partner_confirm(parent, title: str, message: str) -> bool:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title or "Partner")
    dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dialog.setAttribute(Qt.WA_TranslucentBackground, True)
    dialog.setModal(True)
    dialog.setStyleSheet(dialog_stylesheet())

    root = QVBoxLayout(dialog)
    root.setContentsMargins(18, 18, 18, 18)
    shell = QFrame()
    shell.setObjectName("Card")
    root.addWidget(shell)

    layout = QVBoxLayout(shell)
    layout.setContentsMargins(24, 22, 24, 20)
    layout.setSpacing(16)

    head = QHBoxLayout()
    icon = QLabel()
    icon.setPixmap(load_svg_icon("settings").pixmap(20, 20))
    title_label = QLabel(title or "Partner")
    title_label.setStyleSheet("font-size: 18px; font-weight: 780;")
    head.addWidget(icon)
    head.addWidget(title_label)
    head.addStretch(1)
    layout.addLayout(head)

    body = QLabel(str(message or ""))
    body.setWordWrap(True)
    body.setStyleSheet(f"color: {COLORS['subtext']}; font-size: 14px; line-height: 1.55;")
    layout.addWidget(body)

    footer = QHBoxLayout()
    cancel = QPushButton("取消")
    confirm = QPushButton("确认")
    confirm.setObjectName("PrimaryAction")
    cancel.clicked.connect(dialog.reject)
    confirm.clicked.connect(dialog.accept)
    footer.addStretch(1)
    footer.addWidget(cancel)
    footer.addWidget(confirm)
    layout.addLayout(footer)

    dialog.resize(460, 210)
    return dialog.exec() == QDialog.Accepted


def prompt_partner_text(parent, title: str, label: str, text: str = "") -> tuple[str, bool]:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title or "Partner")
    dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dialog.setAttribute(Qt.WA_TranslucentBackground, True)
    dialog.setModal(True)
    dialog.setStyleSheet(dialog_stylesheet())

    root = QVBoxLayout(dialog)
    root.setContentsMargins(18, 18, 18, 18)
    shell = QFrame()
    shell.setObjectName("Card")
    root.addWidget(shell)

    layout = QVBoxLayout(shell)
    layout.setContentsMargins(24, 22, 24, 20)
    layout.setSpacing(14)

    title_label = QLabel(title or "Partner")
    title_label.setStyleSheet("font-size: 18px; font-weight: 780;")
    layout.addWidget(title_label)

    label_widget = QLabel(label or "")
    label_widget.setStyleSheet(f"color: {COLORS['subtext']}; font-size: 14px;")
    layout.addWidget(label_widget)

    input_widget = QLineEdit()
    input_widget.setText(str(text or ""))
    input_widget.selectAll()
    layout.addWidget(input_widget)

    footer = QHBoxLayout()
    cancel = QPushButton("取消")
    confirm = QPushButton("确认")
    confirm.setObjectName("PrimaryAction")
    cancel.clicked.connect(dialog.reject)
    confirm.clicked.connect(dialog.accept)
    footer.addStretch(1)
    footer.addWidget(cancel)
    footer.addWidget(confirm)
    layout.addLayout(footer)

    dialog.resize(460, 230)
    input_widget.setFocus()
    ok = dialog.exec() == QDialog.Accepted
    return input_widget.text(), ok


class StableComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


def bridge_onboarding_done(settings: dict | None) -> bool:
    settings = settings or {}
    return bool(settings.get("onboarding_completed")) and settings.get("onboarding_schema_version") == PARTNER_CONFIG_SCHEMA_VERSION


def current_install_stamp() -> str:
    """Stable-ish marker for the currently installed desktop executable."""
    if not os.path.exists(sys.executable):
        return ""
    try:
        st = os.stat(sys.executable)
        return f"{int(st.st_mtime)}:{st.st_size}"
    except OSError:
        return ""


def default_local_workspace_path() -> str:
    if os.name == "nt":
        return r"D:\partner_workspace"
    return str(Path.home() / "partner_workspace")

I18N = {
    "zh": {
        "app_title": "Partner",
        "subtitle": "Desktop Research Control Surface",
        "tab_dashboard": "仪表盘",
        "tab_chat": "对话",
        "tab_qq": "实例 / QQ 机器人",
        "tab_logs": "探索记录",
        "tab_ollama": "Ollama",
        "tab_settings": "配置服务器",
        "partner_config": "Partner 配置",
        "refresh": "刷新",
        "refreshing": "刷新中…",
        "loading_partner": "正在加载 Partner",
        "loading_workspace": "正在读取工作区与运行状态…",
        "switching_page": "正在切换页面",
        "opening_page": "正在打开 {page}…",
        "config_footer": "连接配置：{path}",
        "config_footer_empty": "连接配置：未读取",
        "mode_local": "Windows 本地工作区",
        "mode_wsl": "Linux / WSL 已连接",
        "mode_ssh": "SSH 服务器已连接",
        "setup_title": "连接 Partner",
        "setup_sub": "选择本机工作区、Linux / WSL，或通过 SSH 连接服务器上已经在运行的 Partner。",
        "chat_remote_readonly": "当前连接的是 Linux / WSL 工作区。Windows 桌面端当前只负责查看，不直接接管对话与运行。",
        "chat_target": "发送目标",
        "chat_no_instance": "未选择实例",
        "chat_current_target": "当前发送给实例 {id}",
        "chat_input_placeholder": "输入消息…",
        "chat_send": "发送",
        "chat_no_workspace": "尚未配置工作区。",
        "chat_no_available_instance": "当前没有可用实例。",
        "chat_no_send_instance": "当前没有可发送消息的实例。",
        "chat_remote_failed": "远端投递失败：{msg}",
        "chat_thinking": "Partner 正在思考中…",
        "chat_unavailable": "暂时无法处理这条消息。",
        "chat_synced_hint": "已同步 QQ 对话记录",
        "lang_toggle": "EN",
        "qq_source_current": "数据来源：当前工作区",
        "qq_instances": "实例",
        "qq_bots": "QQ 机器人",
        "qq_details": "详情",
        "qq_instance_status_empty": "实例状态：未选择",
        "qq_bot_status_empty": "机器人状态：未选择",
        "qq_detail_hint": "当前选择的实例和机器人会在这里同步展示。",
        "qq_auto_hint": "实例启动时会自动启动对应 QQ 机器人；关闭实例会一起停止。",
        "add": "新增",
        "delete": "删除",
        "configure": "配置",
        "start_instance": "开启实例",
        "stop_instance": "关闭实例",
        "instance_already_running": "实例已经在运行。",
        "instance_started": "实例已启动。",
        "instance_start_failed": "实例启动失败。",
        "instance_stopped": "实例已停止。",
        "instance_stop_failed": "实例停止失败。",
        "instance_no_pid": "实例没有运行中的 PID。",
        "qq_not_configured": "当前实例还没有 QQ 机器人配置。",
        "qq_config_missing": "未配置 QQ 机器人，实例会先独立运行。",
        "qq_running": "运行中",
        "qq_stopped": "已停止",
        "qq_status_running": "机器人状态：运行中",
        "qq_status_stopped": "机器人状态：已停止",
        "qq_status_none": "机器人状态：当前实例未配置机器人",
        "current_instance": "当前实例",
        "status_label": "状态",
        "bot_count": "机器人数量",
        "qq_bot": "QQ 机器人",
        "control_backend": "控制后端",
        "instance_label": "实例 {id}",
        "instance_label_02": "实例02",
        "logs_dir": "记录目录",
        "logs_summary": "按实例浏览 user 文件夹",
        "logs_preview": "内容预览",
        "logs_preview_select": "选择 user 文件查看详情",
        "logs_preview_meta": "右侧直接预览内容，左侧只保留目录结构。",
        "ollama_source": "为每个实例配置可用的 Ollama 连接。",
        "ollama_enabled": "已启用",
        "ollama_mode": "使用范围",
        "ollama_model": "当前模型",
        "ollama_usage": "最近调用",
        "ollama_instances_pool": "实例与连接池",
        "ollama_scope_help": "先选实例和范围，再维护连接池。",
        "ollama_mode_hint": "off 不用；lite 轻任务；project 项目优先；all 全部优先。",
        "ollama_add": "新增连接",
        "ollama_remove": "删除连接",
        "ollama_editor": "连接编辑器",
        "ollama_editor_hint": "编辑当前这一个连接，可指向本机电脑、服务器或自定义 Ollama。",
        "ollama_local": "本机电脑 Ollama",
        "ollama_server": "服务器 Ollama",
        "ollama_custom": "自定义地址",
        "ollama_name_placeholder": "连接备注，可留空自动生成",
        "ollama_url_placeholder": "例如 http://127.0.0.1:11434 或 http://203.0.113.10:11434",
        "ollama_models_placeholder": "按优先顺序填写，例如 qwen2.5:14b,qwen2.5:7b",
        "ollama_enabled_check": "此连接启用",
        "ollama_location": "连接位置",
        "ollama_name": "连接备注",
        "ollama_url": "Ollama 地址",
        "ollama_models": "模型顺序",
        "ollama_save": "保存配置",
        "ollama_test": "探测状态",
        "ollama_runtime_summary": "这里会显示连接状态、当前选中的模型、最近调用与回退情况。",
        "settings_intro": "在这里统一配置连接方式、工作区路径和本机可用 Agent。",
        "settings_mode_title": "连接方式",
        "settings_workspace": "工作区",
        "settings_mode_help": "把本地、WSL 和 SSH 放到同一页管理，不再弹出旧式设置窗口。",
        "settings_local_radio": "Windows 本地工作区",
        "settings_wsl_radio": "连接 Linux / WSL",
        "settings_ssh_radio": "连接 SSH 服务器",
        "settings_local_placeholder": "选择本地 Partner workspace",
        "settings_browse_workspace": "浏览工作区",
        "settings_wsl_distro": "WSL 发行版",
        "settings_linux_path": "Linux 路径",
        "settings_detect_agents": "重新检测 Agent",
        "settings_save": "保存高级设置",
        "settings_agent_title": "本机 Agent",
        "settings_agent_help": "自动识别本机已安装的 Hermes、Codex、OpenClaw 等 Agent，并可指定本地工作区默认使用哪个。",
        "settings_default_agent": "本地默认 Agent",
        "dashboard_overview": "研究伙伴总览",
        "dashboard_configured": "已配置",
        "dashboard_active": "活跃中",
        "dashboard_total_tokens": "累计 Token",
        "dashboard_today_tokens": "今日 Token",
        "dashboard_hermes_online": "Hermes 在线",
        "dashboard_hermes_missing": "Hermes 缺失",
        "dashboard_active_instances": "{count} 个实例推进中",
        "dashboard_today_usage": "今日消耗 {tokens}",
        "dashboard_growth": "{count} 个实例已有积累",
        },
    "en": {
        "app_title": "Partner",
        "subtitle": "Desktop Research Control Surface",
        "tab_dashboard": "Dashboard",
        "tab_chat": "Chat",
        "tab_qq": "Instances / QQ Bots",
        "tab_logs": "Records",
        "tab_ollama": "Ollama",
        "tab_settings": "Server Config",
        "partner_config": "Partner Setup",
        "refresh": "Refresh",
        "refreshing": "Refreshing…",
        "loading_partner": "Loading Partner",
        "loading_workspace": "Reading workspace and runtime state…",
        "switching_page": "Switching Page",
        "opening_page": "Opening {page}…",
        "config_footer": "Connection config: {path}",
        "config_footer_empty": "Connection config: not loaded",
        "mode_local": "Windows Local Workspace",
        "mode_wsl": "Linux / WSL Connected",
        "mode_ssh": "SSH Server Connected",
        "setup_title": "Connect Partner",
        "setup_sub": "Choose a local workspace, Linux / WSL, or connect to an already running server-side Partner over SSH.",
        "chat_remote_readonly": "This window is attached to a Linux / WSL workspace. The Windows desktop app is view-only for now.",
        "chat_target": "Target",
        "chat_no_instance": "No instance selected",
        "chat_current_target": "Sending to instance {id}",
        "chat_input_placeholder": "Type a message…",
        "chat_send": "Send",
        "chat_no_workspace": "Workspace is not configured.",
        "chat_no_available_instance": "No available instance.",
        "chat_no_send_instance": "No instance available for sending messages.",
        "chat_remote_failed": "Remote delivery failed: {msg}",
        "chat_thinking": "Partner is thinking…",
        "chat_unavailable": "This message cannot be processed right now.",
        "chat_synced_hint": "QQ conversation history synced",
        "lang_toggle": "中文",
        "qq_source_current": "Source: current workspace",
        "qq_instances": "Instances",
        "qq_bots": "QQ Bots",
        "qq_details": "Details",
        "qq_instance_status_empty": "Instance status: none selected",
        "qq_bot_status_empty": "Bot status: none selected",
        "qq_detail_hint": "The selected instance and bot are shown here.",
        "qq_auto_hint": "Starting an instance also starts its QQ bot; stopping the instance stops both.",
        "add": "Add",
        "delete": "Delete",
        "configure": "Configure",
        "start_instance": "Start Instance",
        "stop_instance": "Stop Instance",
        "instance_already_running": "The instance is already running.",
        "instance_started": "Instance started.",
        "instance_start_failed": "Instance failed to start.",
        "instance_stopped": "Instance stopped.",
        "instance_stop_failed": "Instance failed to stop.",
        "instance_no_pid": "No running PID was found for this instance.",
        "qq_not_configured": "This instance has no QQ bot configuration.",
        "qq_config_missing": "QQ bot is not configured, so the instance will run by itself.",
        "qq_running": "Running",
        "qq_stopped": "Stopped",
        "qq_status_running": "Bot status: running",
        "qq_status_stopped": "Bot status: stopped",
        "qq_status_none": "Bot status: no bot configured for this instance",
        "current_instance": "Current instance",
        "status_label": "Status",
        "bot_count": "Bot count",
        "qq_bot": "QQ bot",
        "control_backend": "Control backend",
        "instance_label": "Instance {id}",
        "instance_label_02": "Instance 02",
        "logs_dir": "Record Folders",
        "logs_summary": "Browse the user folder by instance",
        "logs_preview": "Preview",
        "logs_preview_select": "Select a user file",
        "logs_preview_meta": "Preview content on the right; browse folders on the left.",
        "ollama_source": "Configure available Ollama connections for each instance.",
        "ollama_enabled": "Enabled",
        "ollama_mode": "Scope",
        "ollama_model": "Current Model",
        "ollama_usage": "Recent Calls",
        "ollama_instances_pool": "Instances and Pool",
        "ollama_scope_help": "Choose an instance and scope, then edit the connection pool.",
        "ollama_mode_hint": "off disables it; lite for light tasks; project for project work; all for priority use.",
        "ollama_add": "Add Connection",
        "ollama_remove": "Remove Connection",
        "ollama_editor": "Connection Editor",
        "ollama_editor_hint": "Edit one connection pointing to this PC, a server, or a custom Ollama endpoint.",
        "ollama_local": "Local Ollama",
        "ollama_server": "Server Ollama",
        "ollama_custom": "Custom URL",
        "ollama_name_placeholder": "Optional note; auto-generated when empty",
        "ollama_url_placeholder": "Example: http://127.0.0.1:11434 or http://203.0.113.10:11434",
        "ollama_models_placeholder": "Priority order, e.g. qwen2.5:14b,qwen2.5:7b",
        "ollama_enabled_check": "Enable this connection",
        "ollama_location": "Location",
        "ollama_name": "Name",
        "ollama_url": "Ollama URL",
        "ollama_models": "Model Order",
        "ollama_save": "Save Config",
        "ollama_test": "Probe Status",
        "ollama_runtime_summary": "Connection status, selected model, recent calls, and fallbacks are shown here.",
        "settings_intro": "Configure connection mode, workspace paths, and local agents in one place.",
        "settings_mode_title": "Connection Mode",
        "settings_workspace": "Workspace",
        "settings_mode_help": "Manage local, WSL, and SSH connections from this page.",
        "settings_local_radio": "Windows Local Workspace",
        "settings_wsl_radio": "Connect Linux / WSL",
        "settings_ssh_radio": "Connect SSH Server",
        "settings_local_placeholder": "Choose a local Partner workspace",
        "settings_browse_workspace": "Browse Workspace",
        "settings_wsl_distro": "WSL Distro",
        "settings_linux_path": "Linux Path",
        "settings_detect_agents": "Detect Agents",
        "settings_save": "Save Advanced Settings",
        "settings_agent_title": "Local Agents",
        "settings_agent_help": "Detect installed Hermes, Codex, OpenClaw, and other agents, then choose the default for local workspaces.",
        "settings_default_agent": "Local Default Agent",
        "dashboard_overview": "Partner Overview",
        "dashboard_configured": "Configured",
        "dashboard_active": "Active",
        "dashboard_total_tokens": "Total Tokens",
        "dashboard_today_tokens": "Today Tokens",
        "dashboard_hermes_online": "Hermes Online",
        "dashboard_hermes_missing": "Hermes Missing",
        "dashboard_active_instances": "{count} active instances",
        "dashboard_today_usage": "Today {tokens}",
        "dashboard_growth": "{count} instances with memory",
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


def workspace_instance_label(root: str) -> str:
    clean = str(root or "").rstrip("/\\")
    name = os.path.basename(clean)
    return name or "workspace"


def _bridge_settings_candidates() -> list[str]:
    paths: list[str] = []
    try:
        ws = find_workspace()
    except Exception:
        ws = None
    if ws and os.path.isdir(ws):
        paths.append(os.path.join(ws, "00_config", "gui_bridge.json"))
    repo_workspace = os.path.join(os.path.dirname(PARTNER_DIR), "partner_workspace")
    if os.path.isdir(repo_workspace):
        paths.append(os.path.join(repo_workspace, "00_config", "gui_bridge.json"))
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


def load_gui_bridge_settings_with_path() -> tuple[dict, str]:
    for path in _bridge_settings_candidates():
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f), path
        except Exception:
            continue
    return {}, ""


def load_gui_bridge_settings() -> dict:
    data, _ = load_gui_bridge_settings_with_path()
    return data


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
                encoding="utf-8",
                errors="replace",
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
            timeout=5,
            creationflags=CREATION_FLAGS,
        )
        if result.returncode != 0:
            return []
        raw = result.stdout or b""
        try:
            decoded = raw.decode("utf-16le")
        except Exception:
            decoded = raw.decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")
        cleaned = decoded.replace("\x00", "")
        return [line.strip() for line in cleaned.splitlines() if line.strip()]
    except Exception:
        return []


def detect_local_agents() -> list[dict]:
    def first_existing(candidates: list[str]) -> str:
        for candidate in candidates:
            if candidate and os.path.exists(os.path.expandvars(candidate)):
                return os.path.expandvars(candidate)
        return ""

    def wsl_which(binary: str) -> str:
        if os.name != "nt":
            return ""
        try:
            safe_binary = shlex.quote(binary)
            script = (
                f"p=$(command -v {safe_binary} 2>/dev/null); "
                f"for c in ~/.local/bin/{binary} ~/.npm-global/bin/{binary} ~/.openclaw/bin/{binary}; do "
                "if [ -z \"$p\" ] && [ -x \"$c\" ]; then p=\"$c\"; fi; "
                "done; printf \"%s\" \"$p\""
            )
            encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
            result = subprocess.run(
                ["wsl.exe", "bash", "-lc", f"printf %s {shlex.quote(encoded_script)} | base64 -d | bash"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=4,
                creationflags=CREATION_FLAGS,
            )
            path = (result.stdout or b"").decode("utf-8", errors="replace").strip()
            return f"WSL: {path}" if path else ""
        except Exception:
            return ""

    home = str(Path.home())
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    hermes = (
        shutil.which("hermes")
        or first_existing(
            [
                os.path.join(appdata, "Python", "Python314", "Scripts", "hermes.exe"),
                os.path.join(appdata, "Python", "Python313", "Scripts", "hermes.exe"),
                os.path.join(appdata, "Python", "Python312", "Scripts", "hermes.exe"),
                os.path.join(appdata, "npm", "hermes.cmd"),
                os.path.join(localappdata, "hermes", "hermes-agent", "venv", "Scripts", "hermes.exe"),
                os.path.join(home, ".local", "bin", "hermes"),
                "/usr/local/bin/hermes",
            ]
        )
    )
    codex = shutil.which("codex") or first_existing([os.path.join(appdata, "npm", "codex.cmd")])
    openclaw = (
        shutil.which("openclaw")
        or first_existing(
            [
                os.path.join(appdata, "npm", "openclaw.cmd"),
                os.path.join(localappdata, "Programs", "OpenClaw", "openclaw.exe"),
                os.path.join(home, ".local", "bin", "openclaw"),
                "/usr/local/bin/openclaw",
                "/usr/bin/openclaw",
            ]
        )
        or wsl_which("openclaw")
    )
    claude = shutil.which("claude") or first_existing([os.path.join(home, ".local", "bin", "claude"), "/usr/local/bin/claude", "/usr/bin/claude"])
    return [
        {"name": "hermes", "label": "Hermes Agent", "available": bool(hermes), "path": hermes or ""},
        {"name": "codex", "label": "OpenAI Codex", "available": bool(codex), "path": codex or ""},
        {"name": "openclaw", "label": "OpenClaw", "available": bool(openclaw), "path": openclaw or ""},
        {"name": "claude_code", "label": "Claude Code", "available": bool(claude), "path": claude or ""},
    ]


def linux_path_to_unc(linux_path: str, distro_name: str) -> str:
    if not linux_path or not distro_name:
        return ""
    clean = linux_path.strip().replace("/", "\\").lstrip("\\")
    return f"\\\\wsl$\\{distro_name}\\{clean}"


def find_workspace() -> Optional[str]:
    from partner.setup import find_workspace as _fw
    return _fw()


def load_dialog_history(workspace: str, n: int = 50) -> list[dict]:
    turns = []
    state_dir = os.path.join(workspace, "state")
    paths = [
        os.path.join(state_dir, "dialog_history.jsonl"),
        os.path.join(state_dir, "qq_chat_history.jsonl"),
    ]
    if not any(os.path.exists(path) for path in paths):
        return []
    try:
        for path in paths:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines[-n:]:
                try:
                    row = json.loads(line.strip(), strict=False)
                    if isinstance(row, dict):
                        turns.append(row)
                except Exception:
                    continue
    except Exception:
        pass
    turns.sort(key=lambda item: str(item.get("timestamp") or item.get("created_at") or ""))
    return turns[-n:]


def append_synced_chat_history(workspace: str, row: dict):
    if not workspace:
        return
    try:
        state_dir = os.path.join(workspace, "state")
        os.makedirs(state_dir, exist_ok=True)
        path = os.path.join(state_dir, "qq_chat_history.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist.exe", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATION_FLAGS,
                timeout=5,
            )
            return str(pid) in (result.stdout or "")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_pid(pid: int) -> tuple[bool, str]:
    if not pid or pid <= 0:
        return False, "无效 PID。"
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATION_FLAGS,
                timeout=10,
            )
            output = (result.stdout or result.stderr or "").strip()
            return result.returncode == 0, output
        except Exception as exc:
            return False, str(exc)
    try:
        os.kill(pid, signal.SIGTERM)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def run_silent(cmd, cwd=None, timeout=30, timeout_ok=False):
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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


class RefreshWorker(QObject):
    finished = Signal(dict)

    def __init__(
        self,
        owner: "PartnerQtWindow",
        page_index: int,
        force: bool,
        chat_instance: tuple[str | None, str | None],
        log_instance: tuple[str | None, str | None],
    ):
        super().__init__()
        self.owner = owner
        self.page_index = page_index
        self.force = force
        self.chat_instance = chat_instance
        self.log_instance = log_instance

    def run(self):
        result = {
            "page_index": self.page_index,
            "force": self.force,
            "finished_at": datetime.now().strftime("%H:%M:%S"),
            "error": "",
        }
        try:
            if self.force:
                self.owner._remote_text_cache.clear()
                self.owner._remote_user_file_list_cache.clear()
            if self.owner.workspace_mode == "ssh":
                self.owner.fetch_remote_bundle(force=self.force)
                if self.page_index == 1:
                    _, inst_dir = self.chat_instance
                    if inst_dir:
                        self.owner.remote_dialog_history(inst_dir, n=CHAT_HISTORY_LIMIT)
            elif self.page_index == 3:
                self.owner.fetch_remote_bundle(force=self.force)
        except Exception as exc:
            result["error"] = str(exc)
        self.finished.emit(result)


class MetricCard(QFrame):
    def __init__(self, label: str, value: str, accent: str = COLORS["text"], icon: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self._accent = accent
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
        self.label_widget = QLabel(label)
        self.label_widget.setObjectName("MetricLabel")
        self.value_widget = QLabel(value)
        self.value_widget.setStyleSheet(f"color: {accent}; font-size: 22px; font-weight: 760;")
        layout.addWidget(self.label_widget)
        layout.addWidget(self.value_widget)

    def set_value(self, value: str, accent: str | None = None):
        color = accent or self._accent
        self._accent = color
        self.value_widget.setText(value)
        self.value_widget.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: 760;")


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

        badge = QLabel()
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(34, 34)
        badge.setStyleSheet("background: transparent;")
        if os.path.exists(APP_ICON_PATH):
            badge.setPixmap(QIcon(APP_ICON_PATH).pixmap(34, 34))
        else:
            badge.setText("🤝")
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

        self.lang_btn = QPushButton(tr("lang_toggle", self.window.lang))
        self.lang_btn.setFixedSize(58, 34)
        self.lang_btn.setCursor(Qt.PointingHandCursor)
        self.lang_btn.setStyleSheet(
            f"QPushButton{{background:{COLORS['muted']};color:{COLORS['text']};border:1px solid {COLORS['border']};"
            "border-radius:12px;font-size:13px;font-weight:700;padding:0;}}"
            "QPushButton:hover{background:#e8eef6;}"
        )
        self.lang_btn.clicked.connect(self.window.toggle_language)
        layout.addWidget(self.lang_btn)

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
        self.local_input = QLineEdit(workspace if workspace_mode == "local" else default_local_workspace_path())
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self.pick_local_dir)
        local_layout.addWidget(self.local_input)
        local_layout.addWidget(browse_btn)
        shell_layout.addWidget(local_box)

        wsl_box = QGroupBox("Linux / WSL 工作区")
        wsl_layout = QGridLayout(wsl_box)
        distros = detect_wsl_distros()
        distro_default = (bridge_settings.get("wsl_distro") or (distros[0] if distros else ""))
        self.distro_input = StableComboBox()
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
        self.ssh_host_input = QLineEdit(bridge_settings.get("ssh_host") or "")
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
                show_partner_notice(self, "Partner", "请选择工作区文件夹")
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
                show_partner_notice(self, "Partner", "请填写 WSL 发行版和 Linux 路径")
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
                show_partner_notice(self, "Partner", "SSH 端口必须是整数")
                return
            if not host or not user or not key or not remote_ws:
                show_partner_notice(self, "Partner", "请填写 SSH host / user / key / remote workspace")
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


class OnboardingDialog(QDialog):
    def __init__(self, parent: "PartnerQtWindow"):
        super().__init__(parent)
        self.parent_window = parent
        self.drag_origin: Optional[QPoint] = None
        self.setWindowTitle("Partner 配置")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(980, 720)
        self.resize(1080, 760)
        self.setStyleSheet(dialog_stylesheet() + self.onboarding_stylesheet())

        self.bridge_settings = dict(parent.bridge_settings or {})
        default_ws = parent.workspace if parent.workspace_mode == "local" and parent.workspace else default_local_workspace_path()
        self.detected_agents = detect_local_agents()
        self.detected_wsl_distros = detect_wsl_distros()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("OnboardingShell")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 105))
        shell.setGraphicsEffect(shadow)
        root.addWidget(shell)

        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        titlebar = QHBoxLayout()
        titlebar.setContentsMargins(22, 16, 12, 10)
        titlebar.setSpacing(12)
        icon = QLabel()
        icon.setFixedSize(34, 34)
        icon.setPixmap(QIcon(APP_ICON_PATH).pixmap(34, 34) if os.path.exists(APP_ICON_PATH) else self.parent_window.qt_icon("instances").pixmap(28, 28))
        title = QLabel("Partner 配置")
        title.setStyleSheet("font-size: 15px; font-weight: 760;")
        close = QPushButton("×")
        close.setObjectName("TitleControlClose")
        close.setFixedSize(46, 38)
        close.clicked.connect(self.reject)
        titlebar.addWidget(icon)
        titlebar.addWidget(title)
        titlebar.addStretch(1)
        titlebar.addWidget(close)
        layout.addLayout(titlebar)

        content = QHBoxLayout()
        content.setContentsMargins(28, 10, 28, 24)
        content.setSpacing(24)
        layout.addLayout(content, 1)

        left = QFrame()
        left.setObjectName("OnboardingHero")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(26, 26, 26, 26)
        left_layout.setSpacing(18)
        headline = QLabel("让 Partner 成为研究伙伴")
        headline.setWordWrap(True)
        headline.setStyleSheet("font-size: 34px; font-weight: 820; line-height: 1.1;")
        subtitle = QLabel(
            "Partner 会把目标、计划、经验和运行状态持续沉淀在 workspace 中，并调用 Openclaw、Hermes 等 agent 推进研究。"
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("OnboardingSubtle")
        left_layout.addWidget(headline)
        left_layout.addWidget(subtitle)
        left_layout.addSpacing(8)
        for icon_key, text in (
            ("folder", "配置 workspace，保存实例、项目、日志和经验"),
            ("active", "检测 WSL / Linux，并帮助打开安装入口"),
            ("hermes_ok", "检测 Openclaw、Hermes"),
            ("token", "引导准备 API、QQ 机器人"),
        ):
            row = self.feature_row(icon_key, text)
            left_layout.addWidget(row)
        left_layout.addStretch(1)
        advanced = QLabel("进阶功能：连接远程服务器、配置 Ollama 池、切换更多 Agent、管理 QQ 机器人和实例运维。")
        advanced.setWordWrap(True)
        advanced.setObjectName("OnboardingNote")
        left_layout.addWidget(advanced)

        right = QFrame()
        right.setObjectName("OnboardingPanel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(24, 24, 24, 20)
        right_layout.setSpacing(14)
        panel_title = QLabel("配置")
        panel_title.setStyleSheet("font-size: 24px; font-weight: 820;")
        right_layout.addWidget(panel_title)

        self.workspace_input = QLineEdit(default_ws)
        self.workspace_input.setPlaceholderText("选择或创建 Partner workspace")
        browse = QPushButton("浏览")
        browse.setObjectName("TertiaryAction")
        browse.clicked.connect(self.pick_workspace)
        ws_row = QHBoxLayout()
        ws_row.addWidget(self.workspace_input, 1)
        ws_row.addWidget(browse)
        right_layout.addWidget(self.field_card("Workspace", "所有实例、项目、日志和配置会保存在这里。", ws_row))

        wsl_card = self.status_card(
            "WSL / Ubuntu",
            self.wsl_status_text(),
            [
                ("重新检测", self.refresh_wsl_status),
                ("安装 WSL", self.open_wsl_install),
                ("安装 Ubuntu", self.open_ubuntu_install),
                ("打开 Linux", self.open_wsl_terminal),
            ],
        )
        self.wsl_status_label = wsl_card.findChild(QLabel, "StatusText")
        right_layout.addWidget(wsl_card)

        agent_card = self.status_card(
            "Agent",
            self.agent_status_text(),
            [
                ("重新检测", self.refresh_agent_status),
                ("安装 Hermes", self.open_hermes_install_help),
                ("安装 OpenClaw", self.open_openclaw_install_help),
            ],
        )
        self.agent_status_label = agent_card.findChild(QLabel, "StatusText")
        right_layout.addWidget(agent_card)

        api_row = QHBoxLayout()
        self.api_provider_combo = StableComboBox()
        self.api_provider_combo.setObjectName("ModernCombo")
        self.api_provider_combo.addItems(["DeepSeek", "OpenAI"])
        if str(self.bridge_settings.get("api_provider") or "").lower() == "openai":
            self.api_provider_combo.setCurrentText("OpenAI")
        api_row.addWidget(self.api_provider_combo, 1)
        api_buy = QPushButton("打开 API 平台")
        api_buy.setObjectName("TertiaryAction")
        api_buy.clicked.connect(self.open_selected_api_provider)
        qq_btn = QPushButton("QQ 机器人")
        qq_btn.setObjectName("TertiaryAction")
        qq_btn.clicked.connect(lambda: self.open_url("https://q.qq.com/"))
        api_row.addWidget(api_buy)
        api_row.addWidget(qq_btn)
        api_card = self.field_card(
            "API 与 QQ 机器人",
            "先选择 API 服务商，再跳转到对应平台购买或创建 key。QQ 机器人需要在腾讯 QQ 机器人平台创建并获取 AppID / Secret。",
            api_row,
        )
        right_layout.addWidget(api_card)

        right_layout.addStretch(1)
        footer = QHBoxLayout()
        self.skip_btn = QPushButton("稍后配置")
        self.skip_btn.setObjectName("SecondaryAction")
        self.skip_btn.clicked.connect(self.skip)
        self.start_btn = QPushButton("完成并进入 Partner")
        self.start_btn.setObjectName("PrimaryAction")
        self.start_btn.clicked.connect(self.finish)
        footer.addWidget(self.skip_btn)
        footer.addStretch(1)
        footer.addWidget(self.start_btn)
        right_layout.addLayout(footer)

        content.addWidget(left, 9)
        content.addWidget(right, 10)

    def onboarding_stylesheet(self) -> str:
        return f"""
        QFrame#OnboardingShell {{
            background: {COLORS['shell']};
            border: 1px solid {COLORS['border']};
            border-radius: 24px;
        }}
        QFrame#OnboardingHero {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ffffff, stop:1 #edf5ff);
            border: 1px solid #d8e6f6;
            border-radius: 20px;
        }}
        QFrame#OnboardingPanel {{
            background: {COLORS['panel_alt']};
            border: 1px solid {COLORS['border']};
            border-radius: 20px;
        }}
        QFrame#SetupStep {{
            background: #f5f8fc;
            border: 1px solid #d9e3ee;
            border-radius: 16px;
        }}
        QLabel#OnboardingSubtle {{
            color: {COLORS['subtext']};
            font-size: 15px;
            line-height: 1.35;
        }}
        QLabel#OnboardingNote {{
            color: #31506f;
            background: #e9f2ff;
            border: 1px solid #d0e3ff;
            border-radius: 14px;
            padding: 14px;
            font-weight: 600;
        }}
        QLabel#StatusText {{
            color: {COLORS['subtext']};
            font-size: 13px;
        }}
        """

    def feature_row(self, icon_key: str, text: str) -> QFrame:
        row = QFrame()
        row.setObjectName("SetupStep")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        icon = QLabel()
        icon.setFixedSize(26, 26)
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(self.parent_window.qt_icon(icon_key).pixmap(18, 18))
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("font-weight: 650;")
        layout.addWidget(icon)
        layout.addWidget(label, 1)
        return row

    def field_card(self, title: str, subtitle: str, child_layout: QHBoxLayout) -> QFrame:
        card = QFrame()
        card.setObjectName("SetupStep")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 15px; font-weight: 760;")
        sub = QLabel(subtitle)
        sub.setObjectName("StatusText")
        sub.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(sub)
        layout.addLayout(child_layout)
        return card

    def status_card(self, title: str, status: str, actions: list[tuple[str, Callable]]) -> QFrame:
        card = QFrame()
        card.setObjectName("SetupStep")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 15px; font-weight: 760;")
        status_label = QLabel(status)
        status_label.setObjectName("StatusText")
        status_label.setWordWrap(True)
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        for label, handler in actions:
            btn = QPushButton(label)
            btn.setObjectName("TertiaryAction")
            btn.clicked.connect(handler)
            actions_row.addWidget(btn)
        actions_row.addStretch(1)
        layout.addWidget(title_label)
        layout.addWidget(status_label)
        layout.addLayout(actions_row)
        return card

    def pick_workspace(self):
        path = QFileDialog.getExistingDirectory(self, "选择 Partner workspace", self.workspace_input.text().strip() or str(Path.home()))
        if path:
            self.workspace_input.setText(path)

    def wsl_status_text(self) -> str:
        distros = detect_wsl_distros()
        self.detected_wsl_distros = distros
        if distros:
            ubuntu = [d for d in distros if "ubuntu" in d.lower()]
            ubuntu_text = "Ubuntu 已安装" if ubuntu else "未检测到 Ubuntu"
            return f"已检测到 WSL：{'、'.join(distros[:3])}；{ubuntu_text}。"
        return "未检测到 WSL / Ubuntu。可以继续使用本地工作区，也可以一键安装 WSL 和 Ubuntu。"

    def refresh_wsl_status(self):
        if self.wsl_status_label:
            self.wsl_status_label.setText("正在检测 WSL / Ubuntu…")
            QApplication.processEvents()
            self.wsl_status_label.setText(self.wsl_status_text())

    def agent_status_text(self) -> str:
        by_name = {str(a.get("name") or ""): a for a in self.detected_agents}
        parts = []
        for name, label in (("hermes", "Hermes"), ("openclaw", "OpenClaw")):
            item = by_name.get(name) or {}
            if item.get("available"):
                path = str(item.get("path") or "").strip()
                parts.append(f"已检测到 {label}" + (f"：{path}" if path else ""))
            else:
                parts.append(f"未安装 {label}")
        return "；".join(parts) + "。"

    def refresh_agent_status(self):
        self.detected_agents = detect_local_agents()
        if self.agent_status_label:
            self.agent_status_label.setText("正在检测 Hermes / OpenClaw…")
            QApplication.processEvents()
            self.agent_status_label.setText(self.agent_status_text())

    def open_url(self, url: str):
        QDesktopServices.openUrl(QUrl(url))

    def confirm_action(self, message: str, title: str = "Partner") -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dialog.setAttribute(Qt.WA_TranslucentBackground, True)
        dialog.setModal(True)
        dialog.setStyleSheet(dialog_stylesheet())
        root = QVBoxLayout(dialog)
        root.setContentsMargins(18, 18, 18, 18)
        shell = QFrame()
        shell.setObjectName("Card")
        root.addWidget(shell)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(self.parent_window.qt_icon("settings").pixmap(18, 18))
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 17px; font-weight: 780;")
        head.addWidget(icon)
        head.addWidget(title_label)
        head.addStretch(1)
        layout.addLayout(head)
        text = QLabel(message)
        text.setWordWrap(True)
        text.setStyleSheet(f"color: {COLORS['subtext']}; font-size: 14px;")
        layout.addWidget(text)
        footer = QHBoxLayout()
        cancel = QPushButton("取消")
        confirm = QPushButton("确认")
        confirm.setObjectName("PrimaryAction")
        cancel.clicked.connect(dialog.reject)
        confirm.clicked.connect(dialog.accept)
        footer.addStretch(1)
        footer.addWidget(cancel)
        footer.addWidget(confirm)
        layout.addLayout(footer)
        dialog.resize(420, 190)
        return dialog.exec() == QDialog.Accepted

    def run_installer_command(self, command: str, title: str = "Partner 安装"):
        if os.name == "nt":
            escaped_title = title.replace("'", "''")
            ps = (
                f"$host.UI.RawUI.WindowTitle = '{escaped_title}'; "
                "$ErrorActionPreference='Continue'; "
                "Write-Host 'Partner 正在执行安装命令…'; "
                "Write-Host ''; "
                f"{command}; "
                "$partnerExitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }; "
                "Write-Host ''; "
                "if ($partnerExitCode -eq 0) { "
                "Write-Host '安装成功。' -ForegroundColor Green "
                "} else { "
                "Write-Host ('安装失败，退出码：' + $partnerExitCode) -ForegroundColor Red "
                "}; "
                "Read-Host '按 Enter 关闭窗口'"
            )
            subprocess.Popen(
                ["powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", ps],
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            return
        subprocess.Popen(["bash", "-lc", command])

    def open_wsl_terminal(self):
        if os.name == "nt":
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", "Partner Linux", "wsl.exe"], creationflags=0)
            except Exception:
                self.open_url("ms-windows-store://pdp/?ProductId=9PN20MSR04DW")

    def open_wsl_install(self):
        if os.name == "nt":
            if self.detected_wsl_distros:
                if not self.confirm_action("已检测到 WSL。是否仍然打开 WSL 安装命令？"):
                    return
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", "Partner WSL 安装", "cmd.exe", "/k", "wsl.exe --install"], creationflags=0)
            except Exception:
                self.open_url("ms-windows-store://pdp/?ProductId=9PN20MSR04DW")

    def open_ubuntu_install(self):
        if os.name == "nt":
            if any("ubuntu" in d.lower() for d in self.detected_wsl_distros):
                if not self.confirm_action("已检测到 Ubuntu。是否仍然打开 Ubuntu 安装命令？"):
                    return
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", "Partner Ubuntu 安装", "cmd.exe", "/k", "wsl.exe --install -d Ubuntu"], creationflags=0)
            except Exception:
                self.open_url("ms-windows-store://pdp/?ProductId=9PDXGNCFSCZV")

    def open_hermes_install_help(self):
        if any(a.get("name") == "hermes" and a.get("available") for a in self.detected_agents):
            if not self.confirm_action("已检测到 Hermes。是否要重新安装 Hermes？"):
                return
        else:
            if not self.confirm_action("未检测到 Hermes。是否现在打开安装命令？"):
                return
        if os.name == "nt":
            self.run_installer_command("irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1 | iex", "Hermes 安装")
        else:
            self.run_installer_command("curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash", "Hermes 安装")

    def open_openclaw_install_help(self):
        if any(a.get("name") == "openclaw" and a.get("available") for a in self.detected_agents):
            if not self.confirm_action("已检测到 OpenClaw。是否要重新安装 OpenClaw？"):
                return
        else:
            if not self.confirm_action("未检测到 OpenClaw。是否现在打开安装命令？"):
                return
        if os.name == "nt":
            self.run_installer_command(
                'wsl.exe bash -lc "set -o pipefail; echo Installing OpenClaw...; curl -fsSL https://openclaw.ai/install-cli.sh | bash"',
                "OpenClaw 安装",
            )
        else:
            self.run_installer_command("curl -fsSL https://openclaw.ai/install-cli.sh | bash", "OpenClaw 安装")

    def open_selected_api_provider(self):
        provider = self.api_provider_combo.currentText().strip().lower()
        if provider == "deepseek":
            self.open_url("https://platform.deepseek.com/")
            return
        self.open_url("https://platform.openai.com/")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() <= 72:
            self.drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_origin = None
        super().mouseReleaseEvent(event)

    def _save_onboarding_settings(self, completed: bool):
        from partner.config import save_partner_config_data
        from partner.setup import save_workspace_pointer

        ws = self.workspace_input.text().strip() or default_local_workspace_path()
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
        settings = dict(self.bridge_settings or {})
        settings.update(
            {
                "mode": "local",
                "api_provider": self.api_provider_combo.currentText().strip().lower() or "deepseek",
                "onboarding_completed": completed,
                "onboarding_schema_version": PARTNER_CONFIG_SCHEMA_VERSION if completed else "",
                "onboarding_completed_at": datetime.now().isoformat() if completed else "",
                "install_config_prompt_stamp": current_install_stamp(),
                "install_config_prompted_at": datetime.now().isoformat(),
                "saved_at": datetime.now().isoformat(),
            }
        )
        save_gui_bridge_settings(settings, workspace_hint=ws)
        self.parent_window.workspace = ws
        self.parent_window.workspace_mode = "local"
        self.parent_window.bridge_settings, self.parent_window.bridge_settings_path = load_gui_bridge_settings_with_path()

    def _mark_seen_without_reconfigure(self):
        settings = dict(self.bridge_settings or {})
        settings.update(
            {
                "onboarding_completed": True,
                "onboarding_schema_version": PARTNER_CONFIG_SCHEMA_VERSION,
                "onboarding_completed_at": datetime.now().isoformat(),
                "install_config_prompt_stamp": current_install_stamp(),
                "install_config_prompted_at": datetime.now().isoformat(),
                "saved_at": datetime.now().isoformat(),
            }
        )
        save_gui_bridge_settings(settings, workspace_hint=self.parent_window.workspace if self.parent_window.workspace_mode == "local" else None)
        self.parent_window.bridge_settings, self.parent_window.bridge_settings_path = load_gui_bridge_settings_with_path()

    def skip(self):
        self._mark_seen_without_reconfigure()
        self.accept()

    def finish(self):
        self._save_onboarding_settings(completed=True)
        self.accept()


class LoadingOverlay(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LoadingOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"""
            QFrame#LoadingOverlay {{
                background: rgba(247, 250, 252, 218);
                border-radius: 24px;
            }}
            QFrame#LoadingPanel {{
                background: {COLORS['panel_alt']};
                border: 1px solid {COLORS['border']};
                border-radius: 18px;
            }}
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        panel = QFrame()
        panel.setObjectName("LoadingPanel")
        panel.setFixedSize(360, 174)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(28, 24, 28, 24)
        panel_layout.setSpacing(12)
        self.title = QLabel("正在加载 Partner")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size: 20px; font-weight: 780;")
        self.message = QLabel("正在读取工作区与运行状态…")
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setObjectName("Subtle")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        panel_layout.addStretch(1)
        panel_layout.addWidget(self.title)
        panel_layout.addWidget(self.message)
        panel_layout.addWidget(self.progress)
        panel_layout.addStretch(1)
        row.addWidget(panel)
        row.addStretch(1)
        root.addLayout(row)
        root.addStretch(1)
        self.hide()

    def set_message(self, title: str, message: str):
        self.title.setText(title)
        self.message.setText(message)


class PartnerQtWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.bridge_settings, self.bridge_settings_path = load_gui_bridge_settings_with_path()
        self.lang = str(self.bridge_settings.get("language") or "zh")
        if self.lang not in I18N:
            self.lang = "zh"
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
        self._refresh_worker: RefreshWorker | None = None
        self._refresh_inflight = False
        self._last_refresh_at = ""
        self._local_agents_cache: list[dict] = []
        self._loading_generation = 0
        self._loading_started_at = 0.0
        self._auto_start_done = False
        self._onboarding_visible = False

        self.setWindowTitle(tr("app_title", self.lang))
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if os.path.exists(APP_ICON_PATH):
            self.setWindowIcon(QIcon(APP_ICON_PATH))
        self.resize(1240, 820)
        self.setMinimumSize(1080, 720)
        self.build_ui()
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.setGeometry(self.rect())
        self.show_loading(tr("loading_partner", self.lang), tr("loading_workspace", self.lang))
        self.request_refresh(force=True)
        QTimer.singleShot(700, self.maybe_show_onboarding)
        QTimer.singleShot(1800, self.auto_start_instances_once)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.auto_refresh_tick)
        self.timer.start(AUTO_REFRESH_INTERVAL_MS)

    def nav_label(self, key: str) -> str:
        return tr(key, self.lang)

    def text(self, key: str, **kwargs) -> str:
        value = tr(key, self.lang)
        if kwargs:
            try:
                return value.format(**kwargs)
            except Exception:
                return value
        return value

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
                top: -2px;
                padding: 0 8px;
                background: {COLORS['panel_alt']};
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
            QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QComboBox, QTreeWidget {{
                background: {COLORS['muted']};
                border: 1px solid {COLORS['border']};
                border-radius: 14px;
                padding: 12px;
                selection-background-color: {COLORS['accent']};
            }}
            QTreeWidget {{
                padding: 8px;
            }}
            QTreeWidget#ExplorerTree {{
                padding: 4px;
                outline: none;
                show-decoration-selected: 0;
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
            QListWidget#ManagedList {{
                padding: 8px;
            }}
            QListWidget#ManagedList::item {{
                min-height: 42px;
                padding: 8px 12px;
                margin: 4px 0;
                border-radius: 12px;
            }}
            QListWidget#OllamaEndpointList {{
                padding: 8px;
            }}
            QListWidget#OllamaEndpointList::item {{
                min-height: 42px;
                padding: 8px 12px;
                margin: 4px 0;
                border-radius: 12px;
            }}
            QListWidget#OllamaEndpointList::item:selected {{
                background: {COLORS['accent']};
                color: white;
            }}
            QTreeWidget::item {{
                padding: 6px 4px;
                border-radius: 10px;
            }}
            QTreeWidget::item:selected {{
                background: #e7f0ff;
                color: #174ea6;
            }}
            QTreeWidget#ExplorerTree::item {{
                min-height: 28px;
                padding: 4px 2px;
                border-radius: 8px;
            }}
            QTreeWidget#ExplorerTree::item:selected {{
                background: #e7f0ff;
                color: #174ea6;
            }}
            QTreeWidget#ExplorerTree::item:hover {{
                background: #eef4ff;
            }}
            QTreeView::branch {{
                background: transparent;
            }}
            QTreeView::branch:has-siblings:!adjoins-item,
            QTreeView::branch:has-siblings:adjoins-item,
            QTreeView::branch:!has-children:!has-siblings:adjoins-item {{
                border-image: none;
                image: none;
            }}
            QTreeView::branch:closed:has-children {{
                image: url({icon_url('chevron_right')});
            }}
            QTreeView::branch:open:has-children {{
                image: url({icon_url('chevron_down')});
            }}
            QTreeView#ExplorerTree::branch {{
                margin-left: 2px;
                margin-right: 2px;
                background: transparent;
            }}
            QTreeView#ExplorerTree::branch:selected,
            QTreeView#ExplorerTree::branch:hover {{
                background: transparent;
            }}
            QLabel#Subtle {{
                color: {COLORS['subtext']};
            }}
            QLabel#FooterMeta {{
                color: {COLORS['dim']};
                font-size: 11px;
                padding: 2px 0 0 0;
            }}
            QLabel#PanelEyebrow {{
                color: {COLORS['subtext']};
                font-size: 13px;
                font-weight: 600;
            }}
            QLabel#FieldLabel {{
                color: {COLORS['subtext']};
                font-size: 12px;
                font-weight: 700;
                padding-bottom: 2px;
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
            QPushButton#TertiaryAction {{
                background: transparent;
                border: 1px solid #d8e0ea;
                border-radius: 12px;
                padding: 8px 12px;
                font-size: 13px;
                color: {COLORS['subtext']};
            }}
            QPushButton#TertiaryAction:hover {{
                background: #f3f7fb;
                color: {COLORS['text']};
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
        self.status_text = QLabel(self.text("refreshing"))
        self.status_text.setStyleSheet(f"color: {COLORS['yellow']}; font-size: 13px; font-weight: 600;")
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_text)
        status_row.addStretch(1)
        side_layout.addLayout(status_row)

        self.nav_buttons = []
        nav_group = QButtonGroup(self)
        tabs = [
            (self.nav_label("tab_chat"), 0, "chat"),
            (self.nav_label("tab_dashboard"), 1, "dashboard"),
            (self.nav_label("tab_qq"), 2, "instances"),
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

        ollama_btn = QPushButton(self.nav_label("tab_ollama"))
        ollama_btn.setObjectName("NavButton")
        ollama_btn.setIcon(self.qt_icon("ollama"))
        ollama_btn.setIconSize(QSize(18, 18))
        ollama_btn.setCheckable(True)
        ollama_btn.setMinimumHeight(50)
        ollama_btn.clicked.connect(lambda checked=False: self.switch_page(3))
        nav_group.addButton(ollama_btn)
        side_layout.addWidget(ollama_btn)
        self.nav_buttons.append(ollama_btn)

        advanced_btn = QPushButton(self.nav_label("tab_settings"))
        advanced_btn.setObjectName("NavButton")
        advanced_btn.setIcon(self.qt_icon("settings"))
        advanced_btn.setIconSize(QSize(18, 18))
        advanced_btn.setCheckable(True)
        advanced_btn.setMinimumHeight(50)
        advanced_btn.clicked.connect(lambda checked=False: self.switch_page(4))
        nav_group.addButton(advanced_btn)
        side_layout.addWidget(advanced_btn)
        self.nav_buttons.append(advanced_btn)

        self.partner_config_btn = QPushButton(self.text("partner_config"))
        self.partner_config_btn.setObjectName("SecondaryAction")
        self.partner_config_btn.setIcon(self.qt_icon("configured"))
        self.partner_config_btn.setIconSize(QSize(18, 18))
        self.partner_config_btn.setMinimumHeight(50)
        self.partner_config_btn.clicked.connect(self.open_partner_config)
        side_layout.addWidget(self.partner_config_btn)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(34, 28, 34, 30)
        content_layout.setSpacing(18)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.page_title = QLabel(tr("tab_chat", self.lang))
        self.page_title.setStyleSheet("font-size: 30px; font-weight: 760;")
        title_row.addWidget(self.page_title)
        title_row.addStretch(1)
        self.refresh_btn = QPushButton(self.text("refresh"))
        self.refresh_btn.setObjectName("SecondaryAction")
        self.refresh_btn.setIcon(self.qt_icon("today"))
        self.refresh_btn.setIconSize(QSize(16, 16))
        self.refresh_btn.clicked.connect(lambda: self.request_refresh(force=True))
        title_row.addWidget(self.refresh_btn)
        content_layout.addLayout(title_row)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)

        self.dashboard_page = self.build_dashboard_page()
        self.chat_page = self.build_chat_page()
        self.qq_page = self.build_qq_page()
        self.ollama_page = self.build_ollama_page()
        self.settings_page = self.build_settings_page()

        self.stack.addWidget(self.chat_page)
        self.stack.addWidget(self.dashboard_page)
        self.stack.addWidget(self.qq_page)
        self.stack.addWidget(self.ollama_page)
        self.stack.addWidget(self.settings_page)

        self.footer_config_label = QLabel(self.text("config_footer_empty"))
        self.footer_config_label.setObjectName("FooterMeta")
        self.footer_config_label.setWordWrap(False)
        self.footer_config_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        content_layout.addWidget(self.footer_config_label)

        body_root.addWidget(sidebar)
        body_root.addWidget(content, 1)

    def toggle_language(self):
        self.lang = "en" if self.lang == "zh" else "zh"
        self.bridge_settings["language"] = self.lang
        save_gui_bridge_settings(self.bridge_settings, self.workspace)
        current = self.stack.currentIndex() if hasattr(self, "stack") else 0
        self.build_ui()
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.setGeometry(self.rect())
        self.stack.setCurrentIndex(current)
        for idx, btn in enumerate(getattr(self, "nav_buttons", [])):
            btn.setChecked(idx == current)
        self.page_title.setText(self.page_names()[current])
        self.update_mode_label()
        self.render_current_page(current)

    def page_names(self) -> list[str]:
        return [
            tr("tab_chat", self.lang),
            tr("tab_dashboard", self.lang),
            tr("tab_qq", self.lang),
            tr("tab_ollama", self.lang),
            tr("tab_settings", self.lang),
        ]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.setGeometry(self.rect())

    def show_loading(self, title: str = "正在加载 Partner", message: str = "请稍候…"):
        self._loading_generation += 1
        self._loading_started_at = time.monotonic()
        if not hasattr(self, "loading_overlay"):
            return
        self.loading_overlay.setGeometry(self.rect())
        self.loading_overlay.set_message(title, message)
        self.loading_overlay.show()
        self.loading_overlay.raise_()
        QApplication.processEvents()

    def hide_loading(self, generation: int | None = None, minimum_ms: int = 0):
        if generation is not None and generation != self._loading_generation:
            return
        if minimum_ms > 0 and self._loading_started_at:
            elapsed_ms = int((time.monotonic() - self._loading_started_at) * 1000)
            remaining = max(0, minimum_ms - elapsed_ms)
            if remaining > 0:
                current_generation = self._loading_generation
                QTimer.singleShot(remaining, lambda: self.hide_loading(current_generation, 0))
                return
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.hide()

    def maybe_show_onboarding(self):
        install_stamp = current_install_stamp()
        install_prompt_seen = bool(install_stamp) and self.bridge_settings.get("install_config_prompt_stamp") == install_stamp
        if bridge_onboarding_done(self.bridge_settings) and install_prompt_seen:
            return
        self.hide_loading(minimum_ms=MIN_PAGE_LOADING_MS)
        dialog = OnboardingDialog(self)
        self._onboarding_visible = True
        accepted = dialog.exec()
        self._onboarding_visible = False
        if accepted:
            self.bridge_settings, self.bridge_settings_path = load_gui_bridge_settings_with_path()
            self.workspace, self.workspace_mode = resolve_initial_workspace(self.bridge_settings)
            if install_stamp and self.bridge_settings.get("install_config_prompt_stamp") != install_stamp:
                settings = dict(self.bridge_settings or {})
                settings["install_config_prompt_stamp"] = install_stamp
                settings["install_config_prompted_at"] = datetime.now().isoformat()
                save_gui_bridge_settings(settings, workspace_hint=self.workspace if self.workspace_mode == "local" else None)
                self.bridge_settings, self.bridge_settings_path = load_gui_bridge_settings_with_path()
            self.request_refresh(force=True)
            QTimer.singleShot(300, self.auto_start_instances_once)
            return
        settings = dict(self.bridge_settings or {})
        settings.update(
            {
                "onboarding_completed": True,
                "onboarding_schema_version": PARTNER_CONFIG_SCHEMA_VERSION,
                "onboarding_completed_at": datetime.now().isoformat(),
                "install_config_prompt_stamp": install_stamp,
                "install_config_prompted_at": datetime.now().isoformat(),
                "saved_at": datetime.now().isoformat(),
            }
        )
        save_gui_bridge_settings(settings, workspace_hint=self.workspace if self.workspace_mode == "local" else None)
        self.bridge_settings, self.bridge_settings_path = load_gui_bridge_settings_with_path()
        QTimer.singleShot(300, self.auto_start_instances_once)

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
        self.chat_target_label = QLabel(self.text("chat_target"))
        self.chat_target_label.setObjectName("PanelEyebrow")
        self.chat_instance_combo = StableComboBox()
        self.chat_instance_combo.setObjectName("ModernCombo")
        self.chat_instance_combo.setMinimumWidth(220)
        self.chat_instance_combo.currentIndexChanged.connect(self.on_chat_instance_changed)
        self.chat_target_hint = QLabel(self.text("chat_no_instance"))
        self.chat_target_hint.setStyleSheet(f"color: {COLORS['text']}; font-size: 14px; font-weight: 600;")
        header_layout.addWidget(self.chat_target_label)
        header_layout.addWidget(self.chat_instance_combo, 0)
        header_layout.addWidget(self.chat_target_hint, 1)
        layout.addWidget(header)

        self.chat_stage = QFrame()
        self.chat_stage.setObjectName("Card")
        stage_layout = QVBoxLayout(self.chat_stage)
        stage_layout.setContentsMargins(24, 20, 24, 20)
        stage_layout.setSpacing(16)
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.NoFrame)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.chat_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            f"QScrollBar:vertical {{ background:{COLORS['muted']}; width:10px; border-radius:5px; }}"
            f"QScrollBar::handle:vertical {{ background:{COLORS['border']}; border-radius:5px; min-height:40px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
        )
        self.chat_messages_body = QWidget()
        self.chat_messages_body.setObjectName("ChatMessagesBody")
        self.chat_messages_body.setStyleSheet(
            f"QWidget#ChatMessagesBody {{ background: {COLORS['muted']}; border: 1px solid {COLORS['border']}; border-radius: 16px; }}"
        )
        self.chat_messages_layout = QVBoxLayout(self.chat_messages_body)
        self.chat_messages_layout.setContentsMargins(20, 18, 20, 18)
        self.chat_messages_layout.setSpacing(12)
        self.chat_messages_layout.setAlignment(Qt.AlignTop)
        self.chat_scroll.setWidget(self.chat_messages_body)
        stage_layout.addWidget(self.chat_scroll, 1)
        layout.addWidget(self.chat_stage, 1)

        input_wrap = QFrame()
        input_wrap.setObjectName("Card")
        row = QHBoxLayout(input_wrap)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(12)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText(self.text("chat_input_placeholder"))
        self.chat_input.returnPressed.connect(self.send_chat)
        self.chat_attach_btn = QPushButton()
        self.chat_attach_btn.setToolTip("发送文件" if self.lang == "zh" else "Send file")
        self.chat_attach_btn.setIcon(self.qt_icon("file"))
        self.chat_attach_btn.setIconSize(QSize(18, 18))
        self.chat_attach_btn.setFixedWidth(52)
        self.chat_attach_btn.clicked.connect(self.send_chat_files)
        self.chat_send_btn = QPushButton(self.text("chat_send"))
        self.chat_send_btn.setMinimumWidth(96)
        self.chat_send_btn.clicked.connect(self.send_chat)
        row.addWidget(self.chat_input, 1)
        row.addWidget(self.chat_attach_btn)
        row.addWidget(self.chat_send_btn)
        layout.addWidget(input_wrap)
        return page

    def build_qq_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(4, 4, 4, 4)
        page_layout.setSpacing(14)

        self.qq_source_banner = QFrame()
        self.qq_source_banner.setObjectName("Card")
        banner_layout = QHBoxLayout(self.qq_source_banner)
        banner_layout.setContentsMargins(16, 12, 16, 12)
        banner_layout.setSpacing(12)
        banner_icon = QLabel()
        banner_icon.setPixmap(self.qt_icon("source_wsl").pixmap(16, 16))
        self.qq_source_label = QLabel(self.text("qq_source_current"))
        self.qq_source_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 14px; font-weight: 600;")
        banner_layout.addWidget(banner_icon)
        banner_layout.addWidget(self.qq_source_label, 1)
        page_layout.addWidget(self.qq_source_banner)

        layout = QHBoxLayout()
        layout.setSpacing(14)

        left_box = QFrame()
        left_box.setObjectName("Card")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(18, 18, 18, 18)
        left_layout.setSpacing(10)
        left_head = QHBoxLayout()
        left_head.setSpacing(10)
        left_title = QLabel(self.text("qq_instances"))
        left_title.setStyleSheet("font-size: 18px; font-weight: 760;")
        left_head.addWidget(left_title)
        left_head.addStretch(1)
        add_instance_btn = QPushButton(self.text("add"))
        del_instance_btn = QPushButton(self.text("delete"))
        add_instance_btn.setObjectName("TertiaryAction")
        del_instance_btn.setObjectName("TertiaryAction")
        add_instance_btn.setMinimumWidth(72)
        del_instance_btn.setMinimumWidth(82)
        self.add_instance_btn = add_instance_btn
        self.del_instance_btn = del_instance_btn
        add_instance_btn.clicked.connect(self.add_instance)
        del_instance_btn.clicked.connect(self.delete_instance)
        left_head.addWidget(add_instance_btn)
        left_head.addWidget(del_instance_btn)
        left_layout.addLayout(left_head)
        self.instance_status = QLabel(self.text("qq_instance_status_empty"))
        self.instance_status.setObjectName("Subtle")
        left_layout.addWidget(self.instance_status)
        self.instance_list = QListWidget()
        self.instance_list.setObjectName("ManagedList")
        self.instance_list.setMinimumWidth(260)
        self.instance_list.setFixedHeight(270)
        self.instance_list.currentItemChanged.connect(self.on_instance_selected)
        left_layout.addWidget(self.instance_list)
        left_layout.addSpacing(10)
        left_actions = QHBoxLayout()
        left_actions.setSpacing(8)
        self.start_instance_btn = QPushButton(self.text("start_instance"))
        self.stop_instance_btn = QPushButton(self.text("stop_instance"))
        self.start_instance_btn.setMinimumWidth(132)
        self.stop_instance_btn.setMinimumWidth(132)
        self.start_instance_btn.clicked.connect(self.start_selected_instance)
        self.stop_instance_btn.clicked.connect(self.stop_selected_instance)
        left_actions.addWidget(self.start_instance_btn)
        left_actions.addWidget(self.stop_instance_btn)
        left_layout.addLayout(left_actions)
        self.qq_auto_hint = QLabel(self.text("qq_auto_hint"))
        self.qq_auto_hint.setObjectName("Subtle")
        self.qq_auto_hint.setWordWrap(True)
        left_layout.addWidget(self.qq_auto_hint)

        mid_box = QFrame()
        mid_box.setObjectName("Card")
        mid_layout = QVBoxLayout(mid_box)
        mid_layout.setContentsMargins(18, 18, 18, 18)
        mid_layout.setSpacing(10)
        mid_head = QHBoxLayout()
        mid_head.setSpacing(8)
        mid_title = QLabel(self.text("qq_bots"))
        mid_title.setStyleSheet("font-size: 18px; font-weight: 760;")
        mid_head.addWidget(mid_title)
        mid_head.addStretch(1)
        add_bot_btn = QPushButton(self.text("add"))
        config_bot_btn = QPushButton(self.text("configure"))
        del_bot_btn = QPushButton(self.text("delete"))
        add_bot_btn.setObjectName("TertiaryAction")
        config_bot_btn.setObjectName("TertiaryAction")
        del_bot_btn.setObjectName("TertiaryAction")
        add_bot_btn.setMinimumWidth(72)
        config_bot_btn.setMinimumWidth(110)
        del_bot_btn.setMinimumWidth(82)
        self.add_bot_btn = add_bot_btn
        self.config_bot_btn = config_bot_btn
        self.del_bot_btn = del_bot_btn
        add_bot_btn.clicked.connect(self.add_bot)
        config_bot_btn.clicked.connect(self.configure_bot)
        del_bot_btn.clicked.connect(self.delete_bot)
        mid_head.addWidget(add_bot_btn)
        mid_head.addWidget(config_bot_btn)
        mid_head.addWidget(del_bot_btn)
        mid_layout.addLayout(mid_head)
        self.bot_status = QLabel(self.text("qq_bot_status_empty"))
        self.bot_status.setObjectName("Subtle")
        mid_layout.addWidget(self.bot_status)
        self.bot_list = QListWidget()
        self.bot_list.setObjectName("ManagedList")
        self.bot_list.setMinimumWidth(360)
        self.bot_list.setFixedHeight(170)
        self.bot_list.currentItemChanged.connect(self.on_bot_selected)
        mid_layout.addWidget(self.bot_list)
        mid_layout.addSpacing(8)
        detail_title = QLabel(self.text("qq_details"))
        detail_title.setStyleSheet("font-size: 16px; font-weight: 760;")
        mid_layout.addWidget(detail_title)
        right_hint = QLabel(self.text("qq_detail_hint"))
        right_hint.setObjectName("Subtle")
        right_hint.setWordWrap(True)
        mid_layout.addWidget(right_hint)
        self.qq_info = QTextBrowser()
        self.qq_info.setOpenExternalLinks(False)
        self.qq_info.setFixedHeight(150)
        mid_layout.addWidget(self.qq_info, 1)

        layout.addWidget(left_box, 1)
        layout.addWidget(mid_box, 1)
        page_layout.addLayout(layout, 1)
        return page

    def build_logs_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        layout = QHBoxLayout(body)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(16)

        left_box = QFrame()
        left_box.setObjectName("Card")
        left = QVBoxLayout(left_box)
        left.setContentsMargins(18, 18, 18, 18)
        left.setSpacing(12)
        left_title = QLabel(self.text("logs_dir"))
        left_title.setStyleSheet("font-size: 18px; font-weight: 760;")
        left.addWidget(left_title)
        self.log_summary = QLabel(self.text("logs_summary"))
        self.log_summary.setObjectName("Subtle")
        left.addWidget(self.log_summary)
        self.log_instance_combo = StableComboBox()
        self.log_instance_combo.setObjectName("ModernCombo")
        self.log_instance_combo.setMinimumWidth(220)
        self.log_instance_combo.currentIndexChanged.connect(self.refresh_logs)
        self.log_root_combo = StableComboBox()
        self.log_root_combo.setObjectName("ModernCombo")
        self.log_root_combo.addItems(["user"])
        self.log_root_combo.currentIndexChanged.connect(self.refresh_logs)
        self.log_root_combo.hide()
        self.log_breadcrumb = QLabel("user")
        self.log_breadcrumb.setObjectName("Subtle")
        self.log_breadcrumb.setWordWrap(False)
        self.log_breadcrumb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.log_tree = QTreeWidget()
        self.log_tree.setObjectName("ExplorerTree")
        self.log_tree.setColumnCount(1)
        self.log_tree.setHeaderHidden(True)
        self.log_tree.setRootIsDecorated(True)
        self.log_tree.setItemsExpandable(True)
        self.log_tree.setAnimated(False)
        self.log_tree.setUniformRowHeights(True)
        self.log_tree.setIndentation(14)
        self.log_tree.setIconSize(QSize(14, 14))
        self.log_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log_tree.setTextElideMode(Qt.ElideMiddle)
        self.log_tree.setExpandsOnDoubleClick(False)
        self.log_tree.setFrameShape(QFrame.NoFrame)
        self.log_tree.setMinimumHeight(280)
        self.log_tree.setMinimumWidth(420)
        self.log_tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.log_tree.setAllColumnsShowFocus(False)
        self.log_tree.setFocusPolicy(Qt.NoFocus)
        self.log_tree.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        self.log_tree.itemSelectionChanged.connect(self.show_log_item)
        self.log_tree.itemClicked.connect(self.on_log_tree_clicked)
        left.addWidget(self.log_instance_combo)
        left.addWidget(self.log_breadcrumb)
        left.addWidget(self.log_tree, 1)

        right_box = QFrame()
        right_box.setObjectName("Card")
        right = QVBoxLayout(right_box)
        right.setContentsMargins(18, 18, 18, 18)
        right.setSpacing(12)
        right_title = QLabel(self.text("logs_preview"))
        right_title.setStyleSheet("font-size: 18px; font-weight: 760;")
        self.log_preview_title = QLabel(self.text("logs_preview_select"))
        self.log_preview_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        right.addWidget(right_title)
        self.log_preview_meta = QLabel(self.text("logs_preview_meta"))
        self.log_preview_meta.setObjectName("Subtle")
        right.addWidget(self.log_preview_meta)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        right.addWidget(self.log_preview_title)
        right.addWidget(self.log_view, 1)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_box)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(0, 12)
        splitter.setStretchFactor(1, 14)
        splitter.setSizes([560, 700])
        layout.addWidget(splitter)
        return page

    def build_ollama_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        page_layout = QVBoxLayout(body)
        page_layout.setContentsMargins(4, 4, 4, 4)
        page_layout.setSpacing(8)

        self.ollama_source_banner = QFrame()
        self.ollama_source_banner.setObjectName("Card")
        banner_layout = QHBoxLayout(self.ollama_source_banner)
        banner_layout.setContentsMargins(16, 12, 16, 12)
        banner_layout.setSpacing(12)
        banner_icon = QLabel()
        banner_icon.setPixmap(self.qt_icon("ollama").pixmap(16, 16))
        self.ollama_source_label = QLabel(self.text("ollama_source"))
        self.ollama_source_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 14px; font-weight: 600;")
        self.ollama_source_label.setWordWrap(True)
        banner_layout.addWidget(banner_icon)
        banner_layout.addWidget(self.ollama_source_label, 1)
        page_layout.addWidget(self.ollama_source_banner)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(12)
        self.ollama_enabled_card = MetricCard(self.text("ollama_enabled"), "-", COLORS["text"], "settings")
        self.ollama_mode_card = MetricCard(self.text("ollama_mode"), "-", COLORS["accent"], "active")
        self.ollama_model_card = MetricCard(self.text("ollama_model"), "-", COLORS["green"], "ollama")
        self.ollama_usage_card = MetricCard(self.text("ollama_usage"), "-", COLORS["yellow"], "token")
        summary_row.addWidget(self.ollama_enabled_card)
        summary_row.addWidget(self.ollama_mode_card)
        summary_row.addWidget(self.ollama_model_card)
        summary_row.addWidget(self.ollama_usage_card)
        page_layout.addLayout(summary_row)

        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.setChildrenCollapsible(False)

        left_box = QFrame()
        left_box.setObjectName("Card")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(8)
        left_layout.setAlignment(Qt.AlignTop)
        left_title = QLabel(self.text("ollama_instances_pool"))
        left_title.setStyleSheet("font-size: 18px; font-weight: 760;")
        left_layout.addWidget(left_title)
        self.ollama_scope_help = QLabel(self.text("ollama_scope_help"))
        self.ollama_scope_help.setObjectName("Subtle")
        self.ollama_scope_help.setWordWrap(True)
        left_layout.addWidget(self.ollama_scope_help)
        self.ollama_instance_combo = StableComboBox()
        self.ollama_instance_combo.setObjectName("ModernCombo")
        self.ollama_instance_combo.currentIndexChanged.connect(self.refresh_ollama_page)
        left_layout.addWidget(self.ollama_instance_combo)
        self.ollama_mode_combo = StableComboBox()
        self.ollama_mode_combo.setObjectName("ModernCombo")
        self.ollama_mode_combo.addItems(["off", "lite", "project", "all"])
        left_layout.addWidget(self.ollama_mode_combo)
        self.ollama_mode_hint = QLabel(self.text("ollama_mode_hint"))
        self.ollama_mode_hint.setObjectName("Subtle")
        self.ollama_mode_hint.setWordWrap(True)
        left_layout.addWidget(self.ollama_mode_hint)
        self.ollama_endpoint_list = QListWidget()
        self.ollama_endpoint_list.setObjectName("OllamaEndpointList")
        self.ollama_endpoint_list.setMinimumHeight(118)
        self.ollama_endpoint_list.setMaximumHeight(150)
        self.ollama_endpoint_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.ollama_endpoint_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.ollama_endpoint_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.ollama_endpoint_list.currentItemChanged.connect(self.on_ollama_endpoint_selected)
        left_layout.addWidget(self.ollama_endpoint_list)
        left_actions = QHBoxLayout()
        left_actions.setSpacing(8)
        self.ollama_add_btn = QPushButton(self.text("ollama_add"))
        self.ollama_add_btn.setObjectName("TertiaryAction")
        self.ollama_remove_btn = QPushButton(self.text("ollama_remove"))
        self.ollama_remove_btn.setObjectName("TertiaryAction")
        self.ollama_add_btn.clicked.connect(self.add_ollama_endpoint)
        self.ollama_remove_btn.clicked.connect(self.remove_ollama_endpoint)
        left_actions.addWidget(self.ollama_add_btn)
        left_actions.addWidget(self.ollama_remove_btn)
        left_actions.addStretch(1)
        left_layout.addLayout(left_actions)

        right_box = QFrame()
        right_box.setObjectName("Card")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(12)
        right_title = QLabel(self.text("ollama_editor"))
        right_title.setStyleSheet("font-size: 18px; font-weight: 760;")
        right_layout.addWidget(right_title)
        self.ollama_editor_hint = QLabel(self.text("ollama_editor_hint"))
        self.ollama_editor_hint.setObjectName("Subtle")
        self.ollama_editor_hint.setWordWrap(True)
        right_layout.addWidget(self.ollama_editor_hint)
        form_wrap = QFrame()
        form_wrap.setObjectName("MetricCard")
        form_layout = QVBoxLayout(form_wrap)
        form_layout.setContentsMargins(16, 16, 16, 16)
        form_layout.setSpacing(10)
        self.ollama_location_combo = StableComboBox()
        self.ollama_location_combo.setObjectName("ModernCombo")
        self.ollama_location_combo.addItems([self.text("ollama_local"), self.text("ollama_server"), self.text("ollama_custom")])
        self.ollama_location_combo.currentIndexChanged.connect(self.on_ollama_location_changed)
        self.ollama_name_input = QLineEdit()
        self.ollama_name_input.setPlaceholderText(self.text("ollama_name_placeholder"))
        self.ollama_url_input = QLineEdit()
        self.ollama_url_input.setPlaceholderText(self.text("ollama_url_placeholder"))
        self.ollama_models_input = QLineEdit()
        self.ollama_models_input.setPlaceholderText(self.text("ollama_models_placeholder"))
        self.ollama_enabled_check = QCheckBox(self.text("ollama_enabled_check"))
        self.ollama_enabled_check.setChecked(True)
        for title, widget in (
            (self.text("ollama_location"), self.ollama_location_combo),
            (self.text("ollama_name"), self.ollama_name_input),
            (self.text("ollama_url"), self.ollama_url_input),
            (self.text("ollama_models"), self.ollama_models_input),
        ):
            label = QLabel(title)
            label.setObjectName("FieldLabel")
            form_layout.addWidget(label)
            form_layout.addWidget(widget)
        form_layout.addWidget(self.ollama_enabled_check)
        right_layout.addWidget(form_wrap)
        action_row = QHBoxLayout()
        self.ollama_save_btn = QPushButton(self.text("ollama_save"))
        self.ollama_save_btn.clicked.connect(self.save_ollama_settings)
        self.ollama_test_btn = QPushButton(self.text("ollama_test"))
        self.ollama_test_btn.setObjectName("SecondaryAction")
        self.ollama_test_btn.clicked.connect(self.test_ollama_settings)
        action_row.addWidget(self.ollama_save_btn)
        action_row.addWidget(self.ollama_test_btn)
        action_row.addStretch(1)
        right_layout.addLayout(action_row)
        self.ollama_runtime_summary = QLabel(self.text("ollama_runtime_summary"))
        self.ollama_runtime_summary.setObjectName("Subtle")
        self.ollama_runtime_summary.setWordWrap(True)
        right_layout.addWidget(self.ollama_runtime_summary)
        self.ollama_status_view = QPlainTextEdit()
        self.ollama_status_view.setReadOnly(True)
        self.ollama_status_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.ollama_status_view.setMinimumHeight(120)
        right_layout.addWidget(self.ollama_status_view, 1)

        content_splitter.addWidget(left_box)
        content_splitter.addWidget(right_box)
        content_splitter.setStretchFactor(0, 9)
        content_splitter.setStretchFactor(1, 15)
        content_splitter.setSizes([420, 800])
        page_layout.addWidget(content_splitter, 1)
        return page

    def build_settings_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        page_layout = QVBoxLayout(body)
        page_layout.setContentsMargins(4, 4, 4, 4)
        page_layout.setSpacing(12)

        intro = QFrame()
        intro.setObjectName("Card")
        intro_layout = QHBoxLayout(intro)
        intro_layout.setContentsMargins(16, 12, 16, 12)
        intro_layout.setSpacing(12)
        intro_icon = QLabel()
        intro_icon.setPixmap(self.qt_icon("settings").pixmap(16, 16))
        self.settings_intro_label = QLabel(self.text("settings_intro"))
        self.settings_intro_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 14px; font-weight: 600;")
        self.settings_intro_label.setWordWrap(True)
        intro_layout.addWidget(intro_icon)
        intro_layout.addWidget(self.settings_intro_label, 1)
        page_layout.addWidget(intro)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.settings_mode_card = MetricCard(self.text("settings_mode_title"), "-", COLORS["accent"], "settings")
        self.settings_workspace_card = MetricCard(self.text("settings_workspace"), "-", COLORS["text"], "folder")
        self.settings_agent_card = MetricCard(self.text("settings_agent_title"), "-", COLORS["green"], "active")
        top.addWidget(self.settings_mode_card)
        top.addWidget(self.settings_workspace_card)
        top.addWidget(self.settings_agent_card)
        page_layout.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        left_box = QFrame()
        left_box.setObjectName("Card")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(18, 18, 18, 18)
        left_layout.setSpacing(12)
        left_title = QLabel(self.text("settings_mode_title"))
        left_title.setStyleSheet("font-size: 18px; font-weight: 760;")
        left_layout.addWidget(left_title)
        self.settings_mode_help = QLabel(self.text("settings_mode_help"))
        self.settings_mode_help.setObjectName("Subtle")
        self.settings_mode_help.setWordWrap(True)
        left_layout.addWidget(self.settings_mode_help)

        self.settings_mode_group = QButtonGroup(self)
        self.settings_local_radio = QRadioButton(self.text("settings_local_radio"))
        self.settings_wsl_radio = QRadioButton(self.text("settings_wsl_radio"))
        self.settings_ssh_radio = QRadioButton(self.text("settings_ssh_radio"))
        for btn in (self.settings_local_radio, self.settings_wsl_radio, self.settings_ssh_radio):
            self.settings_mode_group.addButton(btn)
            left_layout.addWidget(btn)

        self.settings_local_input = QLineEdit()
        self.settings_local_input.setPlaceholderText(self.text("settings_local_placeholder"))
        self.settings_local_browse_btn = QPushButton(self.text("settings_browse_workspace"))
        self.settings_local_browse_btn.setObjectName("TertiaryAction")
        self.settings_local_browse_btn.clicked.connect(self.pick_settings_local_dir)
        local_row = QHBoxLayout()
        local_row.addWidget(self.settings_local_input, 1)
        local_row.addWidget(self.settings_local_browse_btn)
        left_layout.addLayout(local_row)

        self.settings_distro_combo = StableComboBox()
        self.settings_distro_combo.setObjectName("ModernCombo")
        self.settings_distro_combo.setEditable(True)
        self.settings_linux_path_input = QLineEdit()
        self.settings_linux_path_input.setPlaceholderText("/mnt/e/work/partner_workspace")
        left_layout.addWidget(self.field_block(self.text("settings_wsl_distro"), self.settings_distro_combo))
        left_layout.addWidget(self.field_block(self.text("settings_linux_path"), self.settings_linux_path_input))

        self.settings_ssh_host_input = QLineEdit()
        self.settings_ssh_port_input = QLineEdit()
        self.settings_ssh_user_input = QLineEdit()
        self.settings_ssh_key_input = QLineEdit()
        self.settings_ssh_workspace_input = QLineEdit()
        self.settings_ssh_partner_dir_input = QLineEdit()
        ssh_grid = QGridLayout()
        ssh_grid.setHorizontalSpacing(12)
        ssh_grid.setVerticalSpacing(10)
        ssh_grid.addWidget(QLabel("Host"), 0, 0)
        ssh_grid.addWidget(self.settings_ssh_host_input, 0, 1)
        ssh_grid.addWidget(QLabel("Port"), 0, 2)
        ssh_grid.addWidget(self.settings_ssh_port_input, 0, 3)
        ssh_grid.addWidget(QLabel("User"), 1, 0)
        ssh_grid.addWidget(self.settings_ssh_user_input, 1, 1)
        ssh_grid.addWidget(QLabel("Key"), 1, 2)
        ssh_grid.addWidget(self.settings_ssh_key_input, 1, 3)
        ssh_grid.addWidget(QLabel("Remote Workspace"), 2, 0)
        ssh_grid.addWidget(self.settings_ssh_workspace_input, 2, 1, 1, 3)
        ssh_grid.addWidget(QLabel("Partner Dir"), 3, 0)
        ssh_grid.addWidget(self.settings_ssh_partner_dir_input, 3, 1, 1, 3)
        ssh_wrap = QWidget()
        ssh_wrap.setLayout(ssh_grid)
        left_layout.addWidget(ssh_wrap)

        left_actions = QHBoxLayout()
        self.settings_detect_agents_btn = QPushButton(self.text("settings_detect_agents"))
        self.settings_detect_agents_btn.setObjectName("TertiaryAction")
        self.settings_detect_agents_btn.clicked.connect(self.refresh_settings_page)
        self.settings_save_btn = QPushButton(self.text("settings_save"))
        self.settings_save_btn.clicked.connect(self.save_settings_page)
        left_actions.addWidget(self.settings_detect_agents_btn)
        left_actions.addStretch(1)
        left_actions.addWidget(self.settings_save_btn)
        left_layout.addLayout(left_actions)

        right_box = QFrame()
        right_box.setObjectName("Card")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(12)
        right_title = QLabel(self.text("settings_agent_title"))
        right_title.setStyleSheet("font-size: 18px; font-weight: 760;")
        right_layout.addWidget(right_title)
        self.settings_agent_help = QLabel(self.text("settings_agent_help"))
        self.settings_agent_help.setObjectName("Subtle")
        self.settings_agent_help.setWordWrap(True)
        right_layout.addWidget(self.settings_agent_help)
        self.settings_agent_list = QListWidget()
        self.settings_agent_list.setMinimumHeight(180)
        right_layout.addWidget(self.settings_agent_list)
        self.settings_agent_backend_combo = StableComboBox()
        self.settings_agent_backend_combo.setObjectName("ModernCombo")
        self.settings_agent_backend_combo.addItems(["hermes", "codex", "openclaw", "claude_code"])
        right_layout.addWidget(self.field_block(self.text("settings_default_agent"), self.settings_agent_backend_combo))
        self.settings_agent_detail = QPlainTextEdit()
        self.settings_agent_detail.setReadOnly(True)
        self.settings_agent_detail.setMinimumHeight(180)
        right_layout.addWidget(self.settings_agent_detail, 1)

        split.addWidget(left_box)
        split.addWidget(right_box)
        split.setStretchFactor(0, 13)
        split.setStretchFactor(1, 10)
        split.setSizes([760, 520])
        page_layout.addWidget(split, 1)
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
        if idx < 0 or idx >= self.stack.count():
            return
        if idx != self.stack.currentIndex():
            self.show_loading(self.text("switching_page"), self.text("opening_page", page=self.page_names()[idx]))
        else:
            return
        self.stack.setCurrentIndex(idx)
        for nav_idx, btn in enumerate(getattr(self, "nav_buttons", [])):
            btn.setChecked(nav_idx == idx)
        self.page_title.setText(
            [
                *self.page_names(),
            ][idx]
        )
        self.request_refresh(force=False, page_index=idx, silent=True)
        current_generation = self._loading_generation
        QTimer.singleShot(
            MIN_PAGE_LOADING_MS,
            lambda: self.hide_loading(current_generation, 0) if not self._refresh_inflight else None,
        )

    def open_setup(self):
        self.switch_page(4)

    def open_partner_config(self):
        self.hide_loading(minimum_ms=MIN_PAGE_LOADING_MS)
        dialog = OnboardingDialog(self)
        if dialog.exec():
            self.bridge_settings, self.bridge_settings_path = load_gui_bridge_settings_with_path()
            self.workspace, self.workspace_mode = resolve_initial_workspace(self.bridge_settings)
            self.refresh_settings_page()
            self.request_refresh(force=True)

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
  "ollama": {{}},
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
def summarize_runs(root):
    path = os.path.join(root, "logs", "agent_runs.jsonl")
    rows = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(item, dict):
                        rows.append(item)
        except Exception:
            rows = []
    rows = rows[-80:]
    total_tokens = sum(int(r.get("total_tokens_est") or 0) for r in rows)
    failed = sum(1 for r in rows if str(r.get("status") or "").lower() not in {{"ok", "empty"}})
    purposes = {{}}
    for row in rows:
        purpose = str(row.get("purpose") or "unknown")
        purposes[purpose] = purposes.get(purpose, 0) + 1
    return {{
        "calls": len(rows),
        "failed": failed,
        "total_tokens_est": total_tokens,
        "purpose_counts": purposes,
        "last_model": str(rows[-1].get("model") or "") if rows else "",
        "last_provider": str(rows[-1].get("provider") or "") if rows else "",
        "last_status": str(rows[-1].get("status") or "") if rows else "",
    }}
def load_agent_cfg(root):
    for rel in ("00_config/partner_config.json", "partner_config.json"):
        path = os.path.join(root, rel)
        if os.path.exists(path):
            try:
                data = json.load(open(path, "r", encoding="utf-8"))
                agent = data.get("agent") if isinstance(data, dict) else None
                if isinstance(agent, dict):
                    return agent
            except Exception:
                continue
    return {{}}
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
bundle["ollama"] = {{
  "workspace_agent": load_agent_cfg(ws),
  "pool_status": load_json(os.path.join(ws, "state", "ollama_pool_status.json")),
  "dynamic_status": load_json(os.path.join(ws, "state", "dynamic_ollama_status.json")),
  "lite_status": load_json(os.path.join(ws, "state", "ollama_lite_status.json")),
  "runtime": summarize_runs(ws),
}}
instances = (bundle["global_config"].get("instances") or {{}})
for inst_id in sorted(instances.keys()):
    inst_dir = os.path.join(ws, "instances", inst_id)
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
    def pid_cmdline(text):
        try:
            pid = int(text or "0")
        except Exception:
            return ""
        if pid <= 0:
            return ""
        cmdline_path = f"/proc/{{pid}}/cmdline"
        if not os.path.exists(cmdline_path):
            return ""
        try:
            raw = open(cmdline_path, "rb").read()
            return raw.replace(b"\\x00", b" ").decode("utf-8", "replace").strip()
        except Exception:
            return ""
    def instance_pid_alive(text, inst_id, inst_dir):
        cmd = pid_cmdline(text)
        if not cmd:
            return False
        if "python" not in cmd or "partner" not in cmd:
            return False
        if f"--instance-id {{inst_id}}" in cmd:
            return True
        return inst_dir in cmd
    def qq_pid_alive(text, inst_dir):
        cmd = pid_cmdline(text)
        if not cmd:
            return False
        if "qq_official_bridge" in cmd or "qq_official_bot" in cmd:
            return True
        return "partner" in cmd and inst_dir in cmd and ("qq" in cmd.lower() or "bot" in cmd.lower())
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
      "instance_running": instance_pid_alive(instance_pid, inst_id, inst_dir),
      "qq_running": qq_pid_alive(qq_pid, inst_dir),
      "token_total": token_total,
      "token_today": token_today,
      "ollama": {{
        "agent": load_agent_cfg(inst_dir),
        "pool_status": load_json(os.path.join(inst_dir, "state", "ollama_pool_status.json")),
        "dynamic_status": load_json(os.path.join(inst_dir, "state", "dynamic_ollama_status.json")),
        "lite_status": load_json(os.path.join(inst_dir, "state", "ollama_lite_status.json")),
        "runtime": summarize_runs(inst_dir),
      }},
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
        qq_hist_path = os.path.join(workspace, "state", "qq_chat_history.jsonl")
        cmd = self.remote_python_command(
            "import json, os\n"
            f"paths = [{hist_path!r}, {qq_hist_path!r}]\n"
            f"limit = {int(n)}\n"
            "rows = []\n"
            "for p in paths:\n"
            "    if os.path.exists(p):\n"
            "        with open(p, 'r', encoding='utf-8', errors='replace') as f:\n"
            "            lines = f.readlines()[-limit:]\n"
            "        for line in lines:\n"
            "            try:\n"
            "                item = json.loads(line.strip())\n"
            "                if isinstance(item, dict):\n"
            "                    rows.append(item)\n"
            "            except Exception:\n"
            "                continue\n"
            "rows.sort(key=lambda item: str(item.get('timestamp') or item.get('created_at') or ''))\n"
            "rows = rows[-limit:]\n"
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
            "qq_history_path = os.path.join(state_dir, 'qq_chat_history.jsonl')\n"
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
            "sync_turn = dict(turn)\n"
            "sync_turn.update({'source': 'desktop_gui', 'channel': 'desktop', 'sender_id': 'windows_gui', 'sender_name': 'Windows Desktop'})\n"
            "with open(qq_history_path, 'a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps(sync_turn, ensure_ascii=False) + '\\n')\n"
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
            snapshot["instances"].append(self.collect_instance_snapshot(workspace_instance_label(ws), ws))

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
        elif not is_active:
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
        self.request_refresh(force=True)

    def active_config_display_path(self) -> str:
        return self.bridge_settings_path or "未读取到本地连接配置文件"

    def compact_config_display_path(self) -> str:
        path = self.active_config_display_path()
        if not path:
            return "未读取到连接配置"
        normalized = path.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        if len(parts) >= 3:
            return "…/" + "/".join(parts[-3:])
        return path

    def set_status_indicator(self, color: str, text: str):
        self._status_dot_color = color
        self.status_dot.setStyleSheet(f"color: {color}; font-size: 18px;")
        self.status_text.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: 600;")
        self.status_text.setText(text)

    def update_mode_label(self):
        if self.workspace_mode == "ssh":
            self.mode_label.setText(tr("mode_ssh", self.lang))
        elif self.workspace_mode == "wsl":
            self.mode_label.setText(tr("mode_wsl", self.lang))
        else:
            self.mode_label.setText(tr("mode_local", self.lang))
        if hasattr(self, "footer_config_label"):
            self.footer_config_label.setText(self.text("config_footer", path=self.compact_config_display_path()))

    def set_refresh_state(self, refreshing: bool, message: str | None = None):
        if refreshing:
            self.set_status_indicator(COLORS["accent"], message or self.text("refreshing"))
            self.refresh_btn.setEnabled(False)
            self.refresh_btn.setText(self.text("refreshing"))
            return
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText(self.text("refresh"))
        if message:
            self.status_text.setText(message)

    def render_current_page(self, page_index: int | None = None):
        current = self.stack.currentIndex() if page_index is None else page_index
        self.update_mode_label()
        if current == 0:
            self.refresh_chat_page()
        elif current == 1:
            self.refresh_dashboard()
        elif current == 2:
            self.refresh_qq_page()
        elif current == 3:
            self.refresh_ollama_page()
        elif current == 4:
            self.refresh_settings_page()

    def auto_refresh_tick(self):
        if self._refresh_inflight:
            return
        if self.stack.currentIndex() == 1:
            self.request_refresh(force=False, silent=True)
            return
        self.request_refresh(force=False, page_index=-1, silent=True)

    def auto_start_instances_once(self):
        if self._auto_start_done or not self.workspace:
            return
        if getattr(self, "_onboarding_visible", False):
            return
        if not bridge_onboarding_done(self.bridge_settings):
            return
        self._auto_start_done = True
        instances = self.available_instances()
        if not instances:
            return

        def _start():
            for inst_id, inst_dir in instances:
                if not self.instance_process_running(inst_id, inst_dir):
                    self.start_instance_runtime(inst_id, inst_dir)

        threading.Thread(target=_start, daemon=True).start()
        QTimer.singleShot(3000, lambda: self.request_refresh(force=False, page_index=self.stack.currentIndex(), silent=True))

    def request_refresh(self, force: bool = False, page_index: int | None = None, silent: bool = False):
        self.update_mode_label()
        current = self.stack.currentIndex() if page_index is None else page_index
        if self._refresh_inflight:
            if not silent:
                self.set_refresh_state(True, "刷新仍在进行…")
            return
        self._refresh_inflight = True
        if force or not silent:
            self.show_loading("正在刷新", "正在同步工作区状态…" if self.workspace_mode != "ssh" else "正在同步远端服务器状态…")
        self.set_refresh_state(True, "正在刷新…" if self.workspace_mode != "ssh" else "正在同步远端状态…")
        if self.workspace_mode != "ssh":
            QTimer.singleShot(
                0,
                lambda idx=current: self.finish_refresh(
                    {"page_index": idx, "finished_at": datetime.now().strftime("%H:%M:%S"), "error": ""}
                ),
            )
            return
        chat_instance = self.current_chat_instance() if current == 0 else (None, None)
        log_instance = (None, None)
        worker = RefreshWorker(self, current, force, chat_instance, log_instance)
        self._refresh_worker = worker
        worker.finished.connect(self.finish_refresh)
        threading.Thread(target=worker.run, daemon=True).start()

    def finish_refresh(self, result: dict):
        self._refresh_inflight = False
        self._refresh_worker = None
        self._last_refresh_at = result.get("finished_at") or datetime.now().strftime("%H:%M:%S")
        if result.get("page_index", self.stack.currentIndex()) >= 0:
            self.render_current_page(self.stack.currentIndex())
        self.hide_loading(minimum_ms=MIN_PAGE_LOADING_MS)
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText(self.text("refresh"))
        if result.get("error"):
            self.set_status_indicator(COLORS["yellow"], f"刷新失败 · {self._last_refresh_at}")
        elif self.stack.currentIndex() != 1:
            self.set_status_indicator(COLORS["green"], f"已刷新 · {self._last_refresh_at}")

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

    def field_block(self, title: str, widget: QWidget) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("FieldLabel")
        layout.addWidget(label)
        layout.addWidget(widget)
        return wrap

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

    def compact_instance_action(self, text: str) -> str:
        clean = " ".join(str(text or "").replace("\n", " ").split())
        if not clean:
            return "当前没有明确动作"
        if len(clean) <= 40:
            return clean
        return clean[:40].rstrip() + "…"

    def compact_instance_focus(self, text: str) -> str:
        clean = " ".join(str(text or "").replace("\n", " ").split())
        return clean or "尚未命名研究方向"

    def chat_turn_html(self, role: str, text: str, meta_label: str | None = None) -> str:
        safe_text = html.escape(str(text or "")).replace("\n", "<br>")
        is_user = role in {"user", "human"}
        align = "right" if is_user else "left"
        bubble_bg = "#2f6df6" if is_user else "#edf3fb"
        bubble_fg = "#ffffff" if is_user else COLORS["text"]
        border = "#2f6df6" if is_user else COLORS["border"]
        side_pad = "0 4px 0 110px" if is_user else "0 110px 0 4px"
        return (
            "<table width='100%' cellspacing='0' cellpadding='0' style='margin:0 0 12px 0;'>"
            "<tr>"
            f"<td align='{align}' style='padding:{side_pad};'>"
            f"<span style='display:inline-block; text-align:left; background:{bubble_bg}; color:{bubble_fg}; "
            f"border:1px solid {border}; border-radius:14px; padding:12px 14px; font-size:15px; "
            f"line-height:1.45; white-space:normal; word-break:break-word;'>{safe_text}</span>"
            "</td>"
            "</tr>"
            "</table>"
        )

    def render_chat_html(self, inst_id: str | None, turns: list[dict], remote_hint: bool = False, inst_dir: str | None = None) -> str:
        display_id = self.display_instance_id(inst_id, inst_dir)
        display_label = self.display_instance_label(inst_id, inst_dir)
        header = (
            f"<div style='padding:2px 4px 14px 4px; color:{COLORS['subtext']}; font-size:13px;'>"
            f"<b>{html.escape(display_label if self.lang == 'zh' else f'Instance {display_id}')}</b>"
        )
        if remote_hint:
            header += ". Messages are queued remotely." if self.lang == "en" else "。发送后会写入远端待处理队列。"
        header += f"<span style='margin-left:10px;'>· {html.escape(self.text('chat_synced_hint'))}</span>"
        header += "</div>"
        if not turns:
            empty = (
                f"<div style='height:100%; min-height:360px; display:flex; align-items:center; justify-content:center;'>"
                f"<div style='max-width:420px; text-align:center;'>"
                f"<div style='font-size:28px; font-weight:760; color:{COLORS['text']}; margin-bottom:10px;'>{html.escape(display_label if self.lang == 'zh' else f'Instance {display_id}')}</div>"
                f"<div style='font-size:15px; color:{COLORS['subtext']};'>{'The real message stream for this instance will appear here.' if self.lang == 'en' else '这里会显示该实例的真实消息流。'}</div>"
                f"<div style='font-size:14px; color:#6b7788; margin-top:12px;'>"
                f"{('No remote messages yet. Your messages will enter this instance queue.' if remote_hint else 'No local messages yet.') if self.lang == 'en' else ('当前还没有远端对话记录。你发出的消息会进入该实例的待处理队列。' if remote_hint else '当前还没有本地对话记录。')}"
                f"</div></div></div>"
            )
            return header + empty
        chunks = []
        for turn in turns:
            role = str(turn.get("role") or "assistant")
            chunks.append(self.chat_turn_html(role, str(turn.get("content") or turn.get("text") or "")))
        body = "".join(chunks)
        return header + body

    def clear_chat_messages(self):
        if not hasattr(self, "chat_messages_layout"):
            return
        while self.chat_messages_layout.count():
            item = self.chat_messages_layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                self.clear_any_layout(child_layout)

    def chat_bubble_max_width(self) -> int:
        viewport_width = 760
        if hasattr(self, "chat_scroll") and self.chat_scroll.viewport():
            viewport_width = max(520, self.chat_scroll.viewport().width())
        return max(320, min(780, viewport_width - 170))

    def add_chat_notice(self, text: str):
        notice = QLabel(str(text or ""))
        notice.setWordWrap(True)
        notice.setAlignment(Qt.AlignCenter)
        notice.setStyleSheet(f"color:{COLORS['subtext']}; font-size:13px; padding:8px 16px;")
        self.chat_messages_layout.addWidget(notice)

    def format_file_size(self, size: int | str | None) -> str:
        try:
            value = float(size or 0)
        except Exception:
            value = 0
        units = ["B", "KB", "MB", "GB"]
        idx = 0
        while value >= 1024 and idx < len(units) - 1:
            value /= 1024
            idx += 1
        if idx == 0:
            return f"{int(value)} {units[idx]}"
        return f"{value:.1f} {units[idx]}"

    def chat_files_dir(self, inst_dir: str) -> str:
        path = os.path.join(inst_dir, "state", "chat_files")
        os.makedirs(path, exist_ok=True)
        return path

    def store_chat_attachment(self, inst_dir: str, src_path: str) -> dict:
        src = Path(src_path)
        base = src.name or "file"
        safe_name = "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in base).strip() or "file"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest_name = f"{stamp}_{safe_name}"
        dest = os.path.join(self.chat_files_dir(inst_dir), dest_name)
        shutil.copy2(str(src), dest)
        size = os.path.getsize(dest)
        return {
            "type": "file",
            "name": base,
            "stored_name": dest_name,
            "size": size,
            "rel_path": os.path.join("state", "chat_files", dest_name).replace("\\", "/"),
            "source_path": str(src),
        }

    def resolve_chat_attachment_path(self, inst_dir: str | None, attachment: dict) -> str:
        if not inst_dir or not isinstance(attachment, dict):
            return ""
        rel_path = str(attachment.get("rel_path") or "").replace("/", os.sep)
        if rel_path:
            return os.path.join(inst_dir, rel_path)
        stored = str(attachment.get("stored_name") or "")
        if stored:
            return os.path.join(inst_dir, "state", "chat_files", stored)
        path = str(attachment.get("path") or attachment.get("source_path") or "")
        return path if os.path.isabs(path) else ""

    def open_chat_attachment(self, inst_dir: str | None, attachment: dict):
        path = self.resolve_chat_attachment_path(inst_dir, attachment)
        if not path or not os.path.exists(path):
            show_partner_notice(self, "Partner", "文件不存在或还没有同步到本机。" if self.lang == "zh" else "The file does not exist or has not been synced locally.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def save_chat_attachment_as(self, inst_dir: str | None, attachment: dict):
        path = self.resolve_chat_attachment_path(inst_dir, attachment)
        if not path or not os.path.exists(path):
            show_partner_notice(self, "Partner", "文件不存在或还没有同步到本机。" if self.lang == "zh" else "The file does not exist or has not been synced locally.")
            return
        name = str(attachment.get("name") or os.path.basename(path))
        dest, _ = QFileDialog.getSaveFileName(self, "另存文件" if self.lang == "zh" else "Save file", name)
        if not dest:
            return
        try:
            shutil.copy2(path, dest)
        except Exception as exc:
            show_partner_notice(self, "Partner", f"保存失败：{exc}" if self.lang == "zh" else f"Save failed: {exc}")

    def build_attachment_card(self, attachment: dict, inst_dir: str | None) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"background:{COLORS['muted']}; border:1px solid {COLORS['border']}; border-radius:10px;"
        )
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(self.qt_icon("file").pixmap(18, 18))
        layout.addWidget(icon)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name = QLabel(str(attachment.get("name") or attachment.get("stored_name") or "file"))
        name.setWordWrap(False)
        name.setStyleSheet(f"font-size:13px; font-weight:700; color:{COLORS['text']}; background:transparent; border:none;")
        meta = QLabel(self.format_file_size(attachment.get("size")))
        meta.setStyleSheet(f"font-size:12px; color:{COLORS['subtext']}; background:transparent; border:none;")
        text_col.addWidget(name)
        text_col.addWidget(meta)
        layout.addLayout(text_col, 1)
        open_btn = QPushButton("打开" if self.lang == "zh" else "Open")
        save_btn = QPushButton("另存" if self.lang == "zh" else "Save")
        open_btn.setObjectName("TertiaryAction")
        save_btn.setObjectName("TertiaryAction")
        open_btn.clicked.connect(lambda checked=False, att=attachment, root=inst_dir: self.open_chat_attachment(root, att))
        save_btn.clicked.connect(lambda checked=False, att=attachment, root=inst_dir: self.save_chat_attachment_as(root, att))
        layout.addWidget(open_btn)
        layout.addWidget(save_btn)
        return card

    def add_chat_bubble(self, role: str, text: str, attachments: list[dict] | None = None, inst_dir: str | None = None):
        is_user = role in {"user", "human"}
        raw_text = str(text or "")
        row = QWidget()
        row.setStyleSheet("background: transparent; border: none;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        bubble = QFrame()
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 10, 12, 10)
        bubble_layout.setSpacing(8)
        bubble.setMaximumWidth(self.chat_bubble_max_width())
        bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        if raw_text:
            text_label = QLabel(raw_text)
            text_label.setWordWrap(True)
            text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            text_label.setContextMenuPolicy(Qt.CustomContextMenu)
            text_label.customContextMenuRequested.connect(
                lambda pos, label=text_label, value=raw_text: self.show_chat_bubble_menu(label, value, pos)
            )
            text_color = "#ffffff" if is_user else COLORS["text"]
            text_label.setStyleSheet(f"background:transparent; border:none; padding:0; font-size:15px; line-height:1.45; color:{text_color};")
            bubble_layout.addWidget(text_label)
        for attachment in attachments or []:
            if isinstance(attachment, dict):
                bubble_layout.addWidget(self.build_attachment_card(attachment, inst_dir))
        if is_user:
            bubble.setStyleSheet(
                "background:#2f6df6; color:#ffffff; border:1px solid #2f6df6;"
                "border-radius:14px;"
            )
            row_layout.addStretch(1)
            row_layout.addWidget(bubble, 0, Qt.AlignRight)
        else:
            bubble.setStyleSheet(
                f"background:#ffffff; color:{COLORS['text']}; border:1px solid {COLORS['border']};"
                "border-radius:14px;"
            )
            row_layout.addWidget(bubble, 0, Qt.AlignLeft)
            row_layout.addStretch(1)
        self.chat_messages_layout.addWidget(row)

    def show_chat_bubble_menu(self, label: QLabel, text: str, pos):
        selected = label.selectedText().strip() if hasattr(label, "selectedText") else ""
        menu = QMenu(label)
        copy_action = menu.addAction("复制" if self.lang == "zh" else "Copy")
        copy_action.triggered.connect(lambda: self.copy_chat_text(selected or str(text or "")))
        menu.exec(label.mapToGlobal(pos))

    def copy_chat_text(self, text: str):
        QApplication.clipboard().setText(str(text or ""))

    def render_chat_messages(self, inst_id: str | None, turns: list[dict], remote_hint: bool = False, inst_dir: str | None = None):
        self.clear_chat_messages()
        display_id = self.display_instance_id(inst_id, inst_dir)
        display_label = self.display_instance_label(inst_id, inst_dir)
        hint = display_label if self.lang == "zh" else f"Instance {display_id}"
        if remote_hint:
            hint += ". Messages are queued remotely." if self.lang == "en" else "。发送后会写入远端待处理队列。"
        self.add_chat_notice(f"{hint} · {self.text('chat_synced_hint')}")
        if not turns:
            empty = (
                "The real message stream for this instance will appear here."
                if self.lang == "en"
                else "这里会显示该实例的真实消息流。"
            )
            self.add_chat_notice(empty)
            self.scroll_chat_to_bottom()
            return
        for turn in turns:
            role = str(turn.get("role") or "assistant")
            attachments = turn.get("attachments") if isinstance(turn.get("attachments"), list) else []
            self.add_chat_bubble(role, str(turn.get("content") or turn.get("text") or ""), attachments, inst_dir)
        self.scroll_chat_to_bottom()

    def scroll_chat_to_bottom(self):
        if not hasattr(self, "chat_scroll"):
            return
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(0, lambda: self.chat_scroll.verticalScrollBar().setValue(self.chat_scroll.verticalScrollBar().maximum()))

    def build_instance_dashboard_card(self, item: InstanceSnapshot) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        top_row = QHBoxLayout()
        badge = QLabel(self.display_instance_label(item.id, getattr(item, "working_dir", None)))
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

        focus = QLabel(self.compact_instance_focus(item.focus))
        focus.setWordWrap(True)
        focus.setStyleSheet("font-size: 19px; font-weight: 760;")
        layout.addWidget(focus)

        meta = QLabel(self.compact_instance_action(item.current_action))
        meta.setWordWrap(False)
        meta.setStyleSheet(f"font-size: 13px; color: {COLORS['subtext']}; font-weight: 600;")
        layout.addWidget(meta)

        chips = QHBoxLayout()
        chips.setSpacing(10)
        chips.addWidget(self.build_mini_pill("runtime", item.run_duration, item.status_color))
        chips.addWidget(self.build_mini_pill("progress", item.progress_text, COLORS["accent"]))
        chips.addWidget(self.build_mini_pill("growth", f"{item.knowledge_entries} 经验 / {item.journal_count} 探索", COLORS["text"]))
        layout.addLayout(chips)

        footer = QLabel(
            f"最近心跳 {item.last_seen}  ·  {format_tokens(item.token_total)} total  ·  {format_tokens(item.token_today)} today"
        )
        footer.setWordWrap(True)
        footer.setStyleSheet(f"font-size: 12px; color: {COLORS['dim']};")
        layout.addWidget(footer)
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
        title = QLabel(self.text("dashboard_overview"))
        title.setStyleSheet(f"font-size: 24px; font-weight: 760; color: {COLORS['text']};")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self.build_mini_pill("hermes_ok" if snapshot["hermes"]["available"] else "hermes_bad", self.text("dashboard_hermes_online") if snapshot["hermes"]["available"] else self.text("dashboard_hermes_missing"), COLORS["green"] if snapshot["hermes"]["available"] else COLORS["red"]))
        hero_layout.addLayout(title_row)
        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        metrics.addWidget(MetricCard(self.text("dashboard_configured"), str(len(instances)), COLORS["text"], "configured"))
        metrics.addWidget(MetricCard(self.text("dashboard_active"), str(active_instances), COLORS["green"], "active"))
        metrics.addWidget(MetricCard(self.text("dashboard_total_tokens"), format_tokens(total_tokens), COLORS["yellow"], "token"))
        metrics.addWidget(MetricCard(self.text("dashboard_today_tokens"), format_tokens(today_tokens), COLORS["pink"], "today"))
        hero_layout.addLayout(metrics)
        pulse_row = QHBoxLayout()
        pulse_row.setSpacing(12)
        pulse_row.addWidget(self.build_mini_pill("active", self.text("dashboard_active_instances", count=active_instances), COLORS["green"]))
        pulse_row.addWidget(self.build_mini_pill("today", self.text("dashboard_today_usage", tokens=format_tokens(today_tokens)), COLORS["yellow"]))
        if instances:
            growth_count = sum(1 for item in instances if item.knowledge_entries > 0 or item.journal_count > 0)
            pulse_row.addWidget(self.build_mini_pill("growth", self.text("dashboard_growth", count=growth_count), COLORS["accent"]))
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
        refresh_at = self._last_refresh_at or datetime.now().strftime("%H:%M:%S")
        if not hermes["available"]:
            self.set_status_indicator(COLORS["yellow"], f"Hermes 异常 · {refresh_at}")
        elif active_instances:
            self.set_status_indicator(COLORS["green"], f"运行正常 · {refresh_at}")
        else:
            self.set_status_indicator(COLORS["yellow"], f"当前空闲 · {refresh_at}")

    def refresh_chat_page(self):
        self.populate_chat_instances()
        self.clear_chat_messages()
        inst_id, inst_dir = self.current_chat_instance()
        target_text = self.text("chat_current_target", id=self.display_instance_id(inst_id, inst_dir)) if inst_id else self.text("chat_no_instance")
        if hasattr(self, "chat_target_hint"):
            self.chat_target_hint.setText(target_text if inst_dir else self.text("chat_no_instance"))
        if self.workspace_mode == "wsl":
            mode_text = "Linux / WSL"
            self.add_chat_notice(f"当前连接的是 {mode_text} 工作区")
            self.add_chat_notice(f"当前目标：{self.display_instance_label(inst_id, inst_dir)}")
            self.add_chat_notice("Windows 端当前只展示状态，不直接接管 Linux 侧聊天。")
            self.chat_input.setEnabled(False)
            self.chat_send_btn.setEnabled(False)
            return
        self.chat_input.setEnabled(True)
        self.chat_send_btn.setEnabled(True)
        if not self.workspace:
            self.add_chat_notice(self.text("chat_no_workspace"))
            return
        if not inst_dir:
            self.add_chat_notice(self.text("chat_no_available_instance"))
            self.chat_input.setEnabled(False)
            self.chat_send_btn.setEnabled(False)
            return
        if self.workspace_mode == "ssh":
            turns = self.remote_dialog_history(inst_dir, n=CHAT_HISTORY_LIMIT)
            self.render_chat_messages(inst_id, turns, remote_hint=True, inst_dir=inst_dir)
        else:
            turns = load_dialog_history(inst_dir, n=CHAT_HISTORY_LIMIT)
            self.render_chat_messages(inst_id, turns, remote_hint=False, inst_dir=inst_dir)

    def append_chat_message(self, role: str, text: str, attachments: list[dict] | None = None, inst_dir: str | None = None):
        self.add_chat_bubble(role, text, attachments, inst_dir)
        self.scroll_chat_to_bottom()

    def send_chat_files(self):
        if self.workspace_mode == "wsl":
            self.append_chat_message("bot", tr("chat_remote_readonly", self.lang))
            return
        inst_id, inst_dir = self.current_chat_instance()
        if not inst_dir:
            self.append_chat_message("bot", self.text("chat_no_send_instance"))
            return
        if self.workspace_mode == "ssh":
            self.append_chat_message("bot", "远端实例暂不支持从桌面直接上传文件，请先切换到 Windows 本地工作区。" if self.lang == "zh" else "Remote instances do not support direct desktop file upload yet.")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要发送的文件" if self.lang == "zh" else "Choose files to send")
        if not paths:
            return
        attachments = []
        failed = []
        for path in paths:
            try:
                attachments.append(self.store_chat_attachment(inst_dir, path))
            except Exception as exc:
                failed.append(f"{os.path.basename(path)}: {exc}")
        if not attachments:
            if failed:
                show_partner_notice(self, "Partner", "文件发送失败：\n" + "\n".join(failed[:5]))
            return
        names = "、".join(att.get("name", "file") for att in attachments[:3])
        if len(attachments) > 3:
            names += f" 等 {len(attachments)} 个文件"
        content = f"发送文件：{names}" if self.lang == "zh" else f"Sent files: {', '.join(att.get('name', 'file') for att in attachments)}"
        row = {
            "role": "user",
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "source": "desktop_gui",
            "channel": "desktop",
            "sender_id": "windows_gui",
            "sender_name": "Windows Desktop",
            "attachments": attachments,
        }
        append_synced_chat_history(inst_dir, row)
        self.append_chat_message("user", content, attachments, inst_dir)
        if failed:
            self.append_chat_message("bot", "部分文件发送失败：\n" + "\n".join(failed[:5]))

    def send_chat(self):
        if self.workspace_mode == "wsl":
            self.append_chat_message("bot", tr("chat_remote_readonly", self.lang))
            return
        text = self.chat_input.text().strip()
        if not text:
            return
        inst_id, inst_dir = self.current_chat_instance()
        if not inst_dir:
            self.append_chat_message("bot", self.text("chat_no_send_instance"))
            return
        self.chat_input.clear()
        self.append_chat_message("user", text)
        if self.workspace_mode == "ssh":
            ok, msg = self.enqueue_remote_chat_message(inst_id, inst_dir, text)
            self.append_chat_message("bot", msg if ok else self.text("chat_remote_failed", msg=msg))
            return
        append_synced_chat_history(
            inst_dir,
            {
                "role": "user",
                "content": text,
                "timestamp": datetime.now().isoformat(),
                "source": "desktop_gui",
                "channel": "desktop",
                "sender_id": "windows_gui",
                "sender_name": "Windows Desktop",
            },
        )
        self.append_chat_message("bot", self.text("chat_thinking"))
        worker = ChatWorker(inst_dir, text)
        self.chat_worker = worker

        def _run():
            worker.run()

        worker.finished.connect(self.on_chat_finished)
        threading.Thread(target=_run, daemon=True).start()

    def on_chat_finished(self, status: str, payload: str):
        # Drop the last thinking paragraph by rebuilding content if needed.
        self.refresh_chat_page()
        if status == "ok":
            inst_id, inst_dir = self.current_chat_instance()
            if inst_dir:
                append_synced_chat_history(
                    inst_dir,
                    {
                        "role": "assistant",
                        "content": payload,
                        "timestamp": datetime.now().isoformat(),
                        "source": "desktop_gui",
                        "channel": "desktop",
                        "sender_id": "partner",
                        "sender_name": "Partner",
                    },
                )
            self.append_chat_message("bot", payload)
        else:
            self.append_chat_message("bot", f"{self.text('chat_unavailable')}\n\n({payload[:120]})")

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

    def load_instance_partner_agent_config(self, instance_dir: str) -> dict:
        if self.workspace_mode == "ssh":
            inst_id = os.path.basename(instance_dir.rstrip("/\\"))
            item = (self.fetch_remote_bundle().get("instances") or {}).get(inst_id) or {}
            ollama = item.get("ollama") or {}
            agent = ollama.get("agent") or {}
            return agent if isinstance(agent, dict) else {}
        try:
            from partner.config import load_partner_config_data

            data = load_partner_config_data(instance_dir)
            agent = data.get("agent") if isinstance(data, dict) else {}
            return agent if isinstance(agent, dict) else {}
        except Exception:
            return {}

    def save_instance_partner_agent_config(self, instance_dir: str, agent_cfg: dict) -> tuple[bool, str]:
        if self.workspace_mode == "ssh":
            path_list_json = json.dumps(
                [
                    remote_path_join(instance_dir, "00_config", "partner_config.json"),
                    remote_path_join(instance_dir, "partner_config.json"),
                ],
                ensure_ascii=False,
            )
            script = (
                "import json, os\n"
                f"root = {instance_dir!r}\n"
                f"agent = {json.dumps(agent_cfg, ensure_ascii=False)!r}\n"
                "agent = json.loads(agent)\n"
                f"paths = json.loads({path_list_json!r})\n"
                "target = paths[0] if os.path.exists(paths[0]) else (paths[1] if os.path.exists(paths[1]) else paths[0])\n"
                "data = {}\n"
                "if os.path.exists(target):\n"
                "    try:\n"
                "        data = json.load(open(target, 'r', encoding='utf-8'))\n"
                "    except Exception:\n"
                "        data = {}\n"
                "data['agent'] = agent\n"
                "os.makedirs(os.path.dirname(paths[0]), exist_ok=True)\n"
                "for p in paths:\n"
                "    with open(p, 'w', encoding='utf-8') as f:\n"
                "        json.dump(data, f, ensure_ascii=False, indent=2)\n"
                "print('ok')\n"
            )
            ok, out = self.run_ssh(self.remote_python_command(script), capture=True)
            if ok:
                self._remote_bundle_cache = None
                self._remote_bundle_ts = 0.0
            return ok, out or ("已保存。" if ok else "保存失败。")
        try:
            from partner.config import load_partner_config_data, save_partner_config_data

            data = load_partner_config_data(instance_dir)
        except Exception:
            data = {"workspace": {"path": instance_dir, "readonly_dirs": []}, "name": "Partner"}
        data["agent"] = agent_cfg
        try:
            from partner.config import save_partner_config_data

            save_partner_config_data(instance_dir, data)
            return True, "已保存。"
        except Exception as exc:
            return False, str(exc)

    def instance_ollama_snapshot(self, instance_id: str, instance_dir: str) -> dict:
        if self.workspace_mode == "ssh":
            item = (self.fetch_remote_bundle().get("instances") or {}).get(instance_id) or {}
            ollama = item.get("ollama") or {}
            return ollama if isinstance(ollama, dict) else {}
        agent = self.load_instance_partner_agent_config(instance_dir)
        runtime = {}
        pool_status = {}
        dynamic_status = {}
        lite_status = {}
        try:
            from partner.runtime_monitor import summarize_agent_runs

            runtime = summarize_agent_runs(instance_dir)
        except Exception:
            runtime = {}
        pool_status = load_json_file(os.path.join(instance_dir, "state", "ollama_pool_status.json"))
        dynamic_status = load_json_file(os.path.join(instance_dir, "state", "dynamic_ollama_status.json"))
        lite_status = load_json_file(os.path.join(instance_dir, "state", "ollama_lite_status.json"))
        return {
            "agent": agent,
            "pool_status": pool_status,
            "dynamic_status": dynamic_status,
            "lite_status": lite_status,
            "runtime": runtime,
        }

    def populate_ollama_instances(self):
        if not hasattr(self, "ollama_instance_combo"):
            return
        current_data = self.ollama_instance_combo.currentData()
        current_id = current_data[0] if current_data else None
        self.ollama_instance_combo.blockSignals(True)
        self.ollama_instance_combo.clear()
        for inst_id, inst_dir in self.available_instances():
            self.ollama_instance_combo.addItem(self.display_instance_label(inst_id, inst_dir), (inst_id, inst_dir))
        if self.ollama_instance_combo.count():
            target_idx = 0
            for idx in range(self.ollama_instance_combo.count()):
                data = self.ollama_instance_combo.itemData(idx)
                if data and data[0] == current_id:
                    target_idx = idx
                    break
            self.ollama_instance_combo.setCurrentIndex(target_idx)
        self.ollama_instance_combo.blockSignals(False)

    def current_ollama_instance(self) -> tuple[str | None, str | None]:
        if not hasattr(self, "ollama_instance_combo") or self.ollama_instance_combo.count() == 0:
            return None, None
        data = self.ollama_instance_combo.currentData()
        if not data:
            return None, None
        return data[0], data[1]

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

    def compatible_qq_config_paths(self, instance_dir: str) -> tuple[str, str]:
        return (
            os.path.join(instance_dir, "00_config", "qq_config.json"),
            os.path.join(instance_dir, "qq_config.json"),
        )

    def sync_compatible_qq_config(self, instance_dir: str, bot: dict | None = None) -> tuple[bool, str]:
        if self.workspace_mode == "ssh":
            return True, ""
        selected = bot
        if not selected:
            bots, _ = self.load_bot_configs(instance_dir)
            selected = bots[0] if bots else None
        if not isinstance(selected, dict) or not str(selected.get("app_id") or "").strip():
            return False, "当前实例还没有可用的 QQ 机器人配置。"
        primary, legacy = self.compatible_qq_config_paths(instance_dir)
        try:
            os.makedirs(os.path.dirname(primary), exist_ok=True)
            for path in (primary, legacy):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(selected, f, ensure_ascii=False, indent=2)
            return True, ""
        except Exception as exc:
            return False, f"同步 QQ 机器人配置失败：{exc}"

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
                    encoding="utf-8",
                    errors="replace",
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
                encoding="utf-8",
                errors="replace",
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
        if self.workspace_mode == "local":
            return False
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
            return self.instance_has_qq_config(instance_dir) and self.instance_pid_running(instance_dir)
        try:
            pid_text = self.remote_text(pid_path).strip() if self.workspace_mode == "ssh" else read_text_file(pid_path)
            pid = int(pid_text or "0")
        except ValueError:
            return self.instance_has_qq_config(instance_dir) and self.instance_pid_running(instance_dir)
        if not pid:
            return self.instance_has_qq_config(instance_dir) and self.instance_pid_running(instance_dir)
        if self.control_backend() in {"wsl", "ssh"}:
            ok, _ = self.run_workspace_command(f"kill -0 {pid}")
            return ok
        if pid_is_alive(pid):
            return True
        return self.instance_has_qq_config(instance_dir) and self.instance_pid_running(instance_dir)

    def instance_pid_running(self, instance_dir: str) -> bool:
        pid_path = self.instance_pid_path(instance_dir)
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

    def instance_has_qq_config(self, instance_dir: str) -> bool:
        cfg_path = self.instance_config_path(instance_dir)
        return self.remote_exists(cfg_path) if self.workspace_mode == "ssh" else os.path.exists(cfg_path)

    def runtime_process_running(self, instance_id: str, instance_dir: str) -> bool:
        return self.instance_process_running(instance_id, instance_dir)

    def write_runtime_pidfiles_command(self, instance_dir: str, pid_expr: str = "$!") -> str:
        instance_pid = shlex.quote(self.instance_pid_path(instance_dir))
        bot_pid = shlex.quote(self.bot_pid_path(instance_dir))
        state_dir = shlex.quote(os.path.dirname(self.bot_pid_path(instance_dir)))
        return f"mkdir -p {state_dir} && echo {pid_expr} > {instance_pid} && echo {pid_expr} > {bot_pid}"

    def start_instance_runtime(self, instance_id: str, instance_dir: str) -> tuple[bool, str]:
        if self.instance_process_running(instance_id, instance_dir):
            return True, self.text("instance_already_running")
        bots, _ = self.load_bot_configs(instance_dir)
        if bots:
            ok, sync_msg = self.sync_compatible_qq_config(instance_dir, bots[0])
            if not ok:
                return False, sync_msg
        log_path = os.path.join(instance_dir, "10_logs", "instance.log")
        if self.control_backend() in {"wsl", "ssh"}:
            python_cmd = shlex.quote(self.workspace_python_cmd())
            partner_dir = shlex.quote(self.workspace_partner_dir())
            workspace = shlex.quote(instance_dir)
            inst = shlex.quote(instance_id)
            log = shlex.quote(log_path)
            mkdir = shlex.quote(os.path.dirname(log_path))
            write_pids = self.write_runtime_pidfiles_command(instance_dir)
            if self.control_backend() == "ssh":
                cmd = f"mkdir -p {mkdir} && cd {partner_dir} && nohup {python_cmd} -m partner --instance-id {inst} --workspace {workspace} >> {log} 2>&1 & {write_pids}"
            else:
                cmd = f"mkdir -p {mkdir} && cd {partner_dir} && nohup {python_cmd} -m partner --instance-id {inst} --workspace {workspace} >> {log} 2>&1 & {write_pids}"
            ok, out = self.run_workspace_command(cmd, capture=True)
            return ok, out or (self.text("instance_started") if ok else self.text("instance_start_failed"))
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        cmd = [sys.executable, "-m", "partner", "--instance-id", instance_id, "--workspace", instance_dir]
        try:
            log_handle = open(log_path, "a", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                cmd,
                cwd=self.workspace_partner_dir(),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                creationflags=CREATION_FLAGS,
            )
            os.makedirs(os.path.dirname(self.bot_pid_path(instance_dir)), exist_ok=True)
            with open(self.instance_pid_path(instance_dir), "w", encoding="utf-8") as f:
                f.write(str(proc.pid))
            with open(self.bot_pid_path(instance_dir), "w", encoding="utf-8") as f:
                f.write(str(proc.pid))
            return True, f"{self.text('instance_started')} (PID {proc.pid})"
        except Exception as exc:
            return False, str(exc)

    def stop_instance_runtime(self, instance_id: str, instance_dir: str) -> tuple[bool, str]:
        pid_path = self.instance_pid_path(instance_dir)
        pid_exists = self.remote_exists(pid_path) if self.workspace_mode == "ssh" else os.path.exists(pid_path)
        pid = int((self.remote_text(pid_path).strip() if self.workspace_mode == "ssh" else read_text_file(pid_path)) or "0") if pid_exists else 0
        if self.control_backend() in {"wsl", "ssh"}:
            if pid:
                bot_pid_path = shlex.quote(self.bot_pid_path(instance_dir))
                ok, out = self.run_workspace_command(f"kill {pid} ; rm -f {shlex.quote(pid_path)} {bot_pid_path}")
                return ok, out or (self.text("instance_stopped") if ok else self.text("instance_stop_failed"))
            return False, self.text("instance_no_pid")
        if pid and pid_is_alive(pid):
            ok, out = terminate_pid(pid)
            if ok:
                if os.path.exists(pid_path):
                    os.remove(pid_path)
                bot_pid = self.bot_pid_path(instance_dir)
                if os.path.exists(bot_pid):
                    os.remove(bot_pid)
                return True, self.text("instance_stopped")
            return False, out or self.text("instance_stop_failed")
        bot_pid = self.bot_pid_path(instance_dir)
        if os.path.exists(bot_pid):
            os.remove(bot_pid)
        return False, self.text("instance_no_pid")

    def start_bot_runtime(self, instance_dir: str, bot: dict | None = None) -> tuple[bool, str]:
        ok, sync_msg = self.sync_compatible_qq_config(instance_dir, bot)
        if not ok:
            return False, sync_msg
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
            cmd = f"cd {partner_dir} && {python_cmd} -m partner.cli bot start qq --workspace {workspace}"
            ok, out = self.run_workspace_command(cmd, capture=True)
            return ok, out or ("QQ 机器人已启动。" if ok else "QQ 机器人启动失败。")
        cmd = [sys.executable, "-m", "partner.cli", "bot", "start", "qq", "--workspace", instance_dir]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
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
            cmd = f"cd {partner_dir} && {python_cmd} -m partner.cli bot stop qq --workspace {workspace}"
            ok, out = self.run_workspace_command(cmd, capture=True)
            return ok, out or ("QQ 机器人已停止。" if ok else "QQ 机器人停止失败。")
        cmd = [sys.executable, "-m", "partner.cli", "bot", "stop", "qq", "--workspace", instance_dir]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
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
            return []
        if root:
            return [(workspace_instance_label(root), root)]
        return []

    def display_instance_id(self, inst_id: str | None, inst_dir: str | None = None) -> str:
        raw_id = str(inst_id or "").strip()
        raw_dir = str(inst_dir or "").replace("\\", "/").rstrip("/")
        workspace_dir = str(self.workspace or "").replace("\\", "/").rstrip("/")
        if raw_id == "partner_workspace" or raw_dir.endswith("/partner_workspace") or (workspace_dir and raw_dir == workspace_dir and raw_dir.endswith("/partner_workspace")):
            return "02"
        return raw_id or "-"

    def display_instance_label(self, inst_id: str | None, inst_dir: str | None = None) -> str:
        display_id = self.display_instance_id(inst_id, inst_dir)
        if display_id == "02":
            return self.text("instance_label_02")
        return self.text("instance_label", id=display_id)

    def detect_ollama_location_type(self, base_url: str) -> str:
        url = str(base_url or "").strip().lower()
        if not url:
            return self.text("ollama_local")
        if "127.0.0.1" in url or "localhost" in url:
            return self.text("ollama_local")
        host = str(self.bridge_settings.get("ssh_host") or "").strip().lower()
        if host and host in url:
            return self.text("ollama_server")
        return self.text("ollama_custom")

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
            self.chat_instance_combo.addItem(self.display_instance_label(inst_id, inst_dir), (inst_id, inst_dir))
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
            self.log_instance_combo.addItem(self.display_instance_label(inst_id, inst_dir), (inst_id, inst_dir))
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
            self.chat_target_hint.setText(self.text("chat_current_target", id=self.display_instance_id(inst_id, inst_dir)) if inst_id else self.text("chat_no_instance"))
        if self.stack.currentIndex() == 0:
            self.request_refresh(force=False, page_index=0, silent=True)

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

    def bot_display_id(self, bot: dict, fallback_index: int = 1) -> str:
        if not isinstance(bot, dict):
            return f"机器人 {fallback_index}"
        for key in ("id", "robot_id", "app_id", "name"):
            value = str(bot.get(key) or "").strip()
            if value:
                return value
        return f"机器人 {fallback_index}"

    def save_bot_configs(self, instance_dir: str, bots: list[dict]):
        path = os.path.join(instance_dir, "qq_configs.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bots, f, ensure_ascii=False, indent=2)
        if bots:
            self.sync_compatible_qq_config(instance_dir, bots[0])

    def refresh_qq_page(self):
        remote_managed = self.workspace_mode == "ssh"
        current_item = self.instance_list.currentItem() if hasattr(self, "instance_list") else None
        current_inst = current_item.data(Qt.UserRole)[0] if current_item else None
        current_bot_name = None
        if self.bot_list.currentItem():
            current_bot_payload = self.bot_list.currentItem().data(Qt.UserRole)
            if current_bot_payload and len(current_bot_payload) > 1:
                current_bot_name = (current_bot_payload[1] or {}).get("name")
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
        target_row = 0
        for inst_id, inst_dir in self.available_instances():
            running = self.instance_process_running(inst_id, inst_dir)
            state = self.text("qq_running") if running else self.text("qq_stopped")
            item = QListWidgetItem(f"{self.display_instance_label(inst_id, inst_dir)}  ·  {state}")
            item.setData(Qt.UserRole, (inst_id, inst_dir))
            self.instance_list.addItem(item)
            if current_inst and inst_id == current_inst:
                target_row = self.instance_list.count() - 1
        if self.instance_list.count():
            self.instance_list.blockSignals(True)
            self.instance_list.setCurrentRow(target_row)
            self.instance_list.blockSignals(False)
            self._pending_bot_name = current_bot_name
            self.on_instance_selected(self.instance_list.currentItem())
        elif remote_managed:
            bundle = self.fetch_remote_bundle()
            message = bundle.get("error") or "远端工作区没有读到实例配置。"
            self.qq_info.setPlainText(f"SSH 服务器已连接，但实例列表为空。\n\n原因: {message}")

    def on_instance_selected(self, current, previous=None):
        self.bot_list.clear()
        if not current:
            self.bot_status.setText(self.text("qq_bot_status_empty"))
            self.qq_info.setPlainText(self.text("qq_detail_hint"))
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
                        self.chat_target_hint.setText(self.text("chat_current_target", id=self.display_instance_id(inst_id, inst_dir)))
                    break
        if hasattr(self, "log_instance_combo") and self.log_instance_combo.count():
            for idx in range(self.log_instance_combo.count()):
                data = self.log_instance_combo.itemData(idx)
                if data and data[0] == inst_id:
                    self.log_instance_combo.blockSignals(True)
                    self.log_instance_combo.setCurrentIndex(idx)
                    self.log_instance_combo.blockSignals(False)
                    break
        if hasattr(self, "ollama_instance_combo") and self.ollama_instance_combo.count():
            for idx in range(self.ollama_instance_combo.count()):
                data = self.ollama_instance_combo.itemData(idx)
                if data and data[0] == inst_id:
                    self.ollama_instance_combo.blockSignals(True)
                    self.ollama_instance_combo.setCurrentIndex(idx)
                    self.ollama_instance_combo.blockSignals(False)
                    break
        instance_running = self.instance_process_running(inst_id, inst_dir)
        bots, _ = self.load_bot_configs(inst_dir)
        instance_state = self.text("qq_running") if instance_running else self.text("qq_stopped")
        bot_running = self.qq_bot_running(inst_dir)
        instance_label = self.display_instance_label(inst_id, inst_dir)
        display_id = self.display_instance_id(inst_id, inst_dir)
        for idx, bot in enumerate(bots):
            name = self.bot_display_id(bot, idx + 1)
            bot_state = self.text("qq_running") if bot_running else self.text("qq_stopped")
            item = QListWidgetItem(f"{name}  ·  {bot_state}")
            item.setData(Qt.UserRole, (idx, bot))
            self.bot_list.addItem(item)
        self.instance_status.setText(f"{self.text('current_instance')}：{display_id} · {instance_state}")
        self.bot_status.setText(self.text("qq_status_running") if bot_running else self.text("qq_status_stopped"))
        self.qq_info.setHtml(
            f"<div style='font-size:14px; line-height:1.78;'>"
            f"<div style='font-size:18px; font-weight:760; margin-bottom:8px;'>{html.escape(instance_label)}</div>"
            f"<div><b>{self.text('status_label')}</b>：{instance_state}</div>"
            f"<div><b>{self.text('bot_count')}</b>：{len(bots)}</div>"
            f"<div><b>{self.text('qq_bot')}</b>：{self.text('qq_running') if bot_running else self.text('qq_stopped')}</div>"
            f"<div><b>{self.text('control_backend')}</b>：{'SSH' if self.control_backend() == 'ssh' else ('WSL' if self.control_backend() == 'wsl' else 'Windows')}</div>"
            f"<div style='margin-top:12px; color:{COLORS['subtext']}; font-size:12px;'>{inst_dir}</div>"
            f"</div>"
        )
        if self.bot_list.count():
            target_bot_row = 0
            pending_bot_name = getattr(self, "_pending_bot_name", None)
            if pending_bot_name:
                for idx in range(self.bot_list.count()):
                    payload = self.bot_list.item(idx).data(Qt.UserRole)
                    if payload and ((payload[1] or {}).get("name") == pending_bot_name):
                        target_bot_row = idx
                        break
            self.bot_list.setCurrentRow(target_bot_row)
        else:
            self.bot_status.setText(self.text("qq_status_none"))
        self._pending_bot_name = None

    def on_bot_selected(self, current, previous=None):
        inst_id, inst_dir = self.selected_instance()
        if not current or not inst_dir:
            return
        idx, bot = current.data(Qt.UserRole)
        running = self.qq_bot_running(inst_dir)
        self.bot_status.setText(self.text("qq_status_running") if running else self.text("qq_status_stopped"))
        self.qq_info.setHtml(
            f"<div style='font-size:14px; line-height:1.78;'>"
            f"<div style='font-size:18px; font-weight:760; margin-bottom:8px;'>{self.bot_display_id(bot, idx + 1)}</div>"
            f"<div><b>{self.text('current_instance')}</b>：{self.display_instance_id(inst_id, inst_dir)}</div>"
            f"<div><b>AppID</b>：{bot.get('app_id', '')}</div>"
            f"<div><b>Sandbox</b>：{bot.get('is_sandbox', False)}</div>"
            f"<div><b>{self.text('status_label')}</b>：{self.text('qq_running') if running else self.text('qq_stopped')}</div>"
            f"</div>"
        )

    def start_selected_instance(self):
        inst_id, inst_dir = self.selected_instance()
        if not inst_id or not inst_dir:
            return
        ok, msg = self.start_instance_runtime(inst_id, inst_dir)
        if not ok:
            show_partner_notice(self, "Partner", msg or self.text("instance_start_failed"))
        self.request_refresh(force=True, page_index=2)

    def stop_selected_instance(self):
        inst_id, inst_dir = self.selected_instance()
        if not inst_id or not inst_dir:
            return
        ok, msg = self.stop_instance_runtime(inst_id, inst_dir)
        if not ok:
            show_partner_notice(self, "Partner", msg or self.text("instance_stop_failed"))
        self.request_refresh(force=True, page_index=2)

    def start_selected_bot(self):
        inst_id, inst_dir = self.selected_instance()
        if not inst_dir:
            return
        current = self.bot_list.currentItem() if hasattr(self, "bot_list") else None
        bot = None
        if current:
            payload = current.data(Qt.UserRole)
            if payload and len(payload) > 1:
                bot = payload[1]
        ok, msg = self.start_bot_runtime(inst_dir, bot)
        if not ok:
            show_partner_notice(self, "Partner", msg or "QQ 机器人启动失败。")
        self.request_refresh(force=True, page_index=2)

    def stop_selected_bot(self):
        inst_id, inst_dir = self.selected_instance()
        if not inst_dir:
            return
        ok, msg = self.stop_bot_runtime(inst_dir)
        if not ok:
            show_partner_notice(self, "Partner", msg or "QQ 机器人停止失败。")
        self.request_refresh(force=True, page_index=2)

    def add_instance(self):
        root, cfg, cfg_path = self.get_instance_root_and_config()
        if not root:
            show_partner_notice(self, "Partner", "当前工作区不可用。")
            return
        instance_id, ok = prompt_partner_text(self, "新增实例", "实例 ID")
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
        root, cfg, cfg_path = self.get_instance_root_and_config()
        if not inst_id or inst_dir == root:
            show_partner_notice(self, "Partner", "当前工作区根实例不能在这里删除。")
            return
        if not ask_partner_confirm(self, "删除实例", f"确定删除 {self.display_instance_label(inst_id, inst_dir)} 吗？"):
            return
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
        name, ok = prompt_partner_text(self, "新增机器人", "机器人名称")
        if not ok:
            return
        appid, ok = prompt_partner_text(self, "新增机器人", "AppID")
        if not ok:
            return
        secret, ok = prompt_partner_text(self, "新增机器人", "AppSecret")
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
        name, ok = prompt_partner_text(self, "配置机器人", "机器人名称", text=bot.get("name", ""))
        if not ok:
            return
        appid, ok = prompt_partner_text(self, "配置机器人", "AppID", text=bot.get("app_id", ""))
        if not ok:
            return
        secret, ok = prompt_partner_text(self, "配置机器人", "AppSecret", text=bot.get("app_secret", ""))
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
        if not ask_partner_confirm(self, "删除机器人", f"确定删除机器人 {self.bot_display_id(bot, idx + 1)} 吗？"):
            return
        bots, _ = self.load_bot_configs(inst_dir)
        if 0 <= idx < len(bots):
            bots.pop(idx)
        self.save_bot_configs(inst_dir, bots)
        self.on_instance_selected(self.instance_list.currentItem())

    def refresh_logs(self):
        self.populate_log_instances()
        self.log_tree.clear()
        self.log_view.clear()
        if hasattr(self, "log_preview_title"):
            self.log_preview_title.setText("选择 user 文件查看详情")
        if not self.workspace:
            self.log_summary.setText("工作区未配置")
            if hasattr(self, "log_summary"):
                self.log_breadcrumb.setText("未配置工作区")
            return
        inst_id, inst_dir = self.current_log_instance()
        if not inst_dir:
            self.log_summary.setText("当前没有可用实例")
            if hasattr(self, "log_summary"):
                self.log_breadcrumb.setText("没有可用实例")
            return
        root_name = self.log_root_combo.currentText()
        if hasattr(self, "log_breadcrumb"):
            self.log_breadcrumb.setText(f"{self.display_instance_id(inst_id, inst_dir)} / {root_name}")
        total_items = 0
        target_path, is_dir_root = self.resolve_log_root(inst_dir, root_name)
        base = target_path
        if self.workspace_mode == "ssh":
            entries = self.remote_walk_user_files(base)
        else:
            if not os.path.isdir(base):
                if hasattr(self, "log_summary"):
                    self.log_summary.setText(f"{self.display_instance_label(inst_id, inst_dir)} · user 目录不存在")
                return
            entries = self.local_walk_user_files(base)
        if not entries:
            if hasattr(self, "log_summary"):
                self.log_summary.setText(f"{self.display_instance_label(inst_id, inst_dir)} · user 目录暂无记录")
            return
        total_items = len(entries)
        self.build_log_tree(base, entries)
        self.log_tree.expandToDepth(0)
        target = self.first_file_tree_item()
        if target:
            parent = target.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self.log_tree.setCurrentItem(target)

        source_label = "SSH" if self.workspace_mode == "ssh" else ("WSL" if self.workspace_mode == "wsl" else "本地")
        if hasattr(self, "log_summary"):
            self.log_summary.setText(f"{self.display_instance_label(inst_id, inst_dir)} · {total_items} 个文件 · 来源 {source_label}")

    def build_log_tree(self, base: str, entries: list[str]):
        folders: dict[str, QTreeWidgetItem] = {}
        folder_font = QFont()
        folder_font.setWeight(QFont.DemiBold)
        for rel_path in sorted(entries, key=lambda item: ([part.lower() for part in item.split("/")[:-1]], item.count("/"), item.lower())):
            full = remote_path_join(base, rel_path) if self.workspace_mode == "ssh" else os.path.join(base, rel_path)
            parts = [part for part in rel_path.split("/") if part]
            parent = None
            prefix = []
            for folder in parts[:-1]:
                prefix.append(folder)
                key = "/".join(prefix)
                node = folders.get(key)
                if node is None:
                    node = QTreeWidgetItem([folder])
                    node.setIcon(0, self.qt_icon("folder"))
                    node.setFont(0, folder_font)
                    node.setData(0, Qt.UserRole + 1, key)
                    if parent is None:
                        self.log_tree.addTopLevelItem(node)
                    else:
                        parent.addChild(node)
                    folders[key] = node
                parent = node
            file_item = QTreeWidgetItem([parts[-1] if parts else rel_path])
            file_item.setIcon(0, self.qt_icon("file"))
            file_item.setData(0, Qt.UserRole, full)
            file_item.setData(0, Qt.UserRole + 1, rel_path)
            if parent is None:
                self.log_tree.addTopLevelItem(file_item)
            else:
                parent.addChild(file_item)
        self.sort_log_tree(self.log_tree.invisibleRootItem())

    def sort_log_tree(self, item: QTreeWidgetItem):
        if item.childCount() <= 1:
            return
        children = [item.child(idx) for idx in range(item.childCount())]
        for child in children:
            item.removeChild(child)
        children.sort(
            key=lambda child: (
                1 if child.data(0, Qt.UserRole) else 0,
                child.text(0).lower(),
            )
        )
        for child in children:
            item.addChild(child)
            self.sort_log_tree(child)

    def first_file_tree_item(self):
        def _walk(node: QTreeWidgetItem):
            for idx in range(node.childCount()):
                child = node.child(idx)
                if child.data(0, Qt.UserRole):
                    return child
                found = _walk(child)
                if found:
                    return found
            return None

        root = self.log_tree.invisibleRootItem()
        return _walk(root)

    def show_log_item(self, *args):
        current = self.log_tree.currentItem() if hasattr(self, "log_tree") else None
        if not current:
            return
        payload = current.data(0, Qt.UserRole)
        if not payload:
            if hasattr(self, "log_breadcrumb"):
                self.log_breadcrumb.setText(current.data(0, Qt.UserRole + 1) or current.text(0))
            return
        if hasattr(self, "log_preview_title"):
            self.log_preview_title.setText(current.data(0, Qt.UserRole + 1) or current.text(0))
        if self.workspace_mode == "ssh":
            content = self.remote_text(str(payload))
            self.log_view.setPlainText(content or "(空文件)")
            if hasattr(self, "log_breadcrumb"):
                self.log_breadcrumb.setText(current.data(0, Qt.UserRole + 1) or current.text(0))
        elif os.path.isfile(str(payload)):
            self.log_view.setPlainText(read_text_file(str(payload)) or "(空文件)")
            if hasattr(self, "log_breadcrumb"):
                self.log_breadcrumb.setText(current.data(0, Qt.UserRole + 1) or current.text(0))
        else:
            self.log_view.setPlainText(str(payload))
            if hasattr(self, "log_breadcrumb"):
                self.log_breadcrumb.setText(current.data(0, Qt.UserRole + 1) or current.text(0))

    def on_log_tree_clicked(self, item, column=0):
        if not item:
            return
        payload = item.data(0, Qt.UserRole)
        if payload:
            return
        item.setExpanded(not item.isExpanded())

    def pick_settings_local_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作区文件夹", self.settings_local_input.text().strip() or str(Path.home()))
        if path:
            self.settings_local_input.setText(path)

    def refresh_settings_page(self):
        self._local_agents_cache = detect_local_agents()
        if self.workspace_mode == "ssh":
            mode_text = "SSH"
        elif self.workspace_mode == "wsl":
            mode_text = "WSL"
        else:
            mode_text = "本地"
        ws_text = self.workspace or "-"
        if len(ws_text) > 22:
            ws_text = "…" + ws_text[-21:]
        available_agents = [item["label"] for item in self._local_agents_cache if item.get("available")]
        self.settings_mode_card.set_value(mode_text, COLORS["accent"])
        self.settings_workspace_card.set_value(ws_text, COLORS["text"])
        self.settings_agent_card.set_value(str(len(available_agents)), COLORS["green"] if available_agents else COLORS["subtext"])

        distros = detect_wsl_distros()
        current_distro = self.settings_distro_combo.currentText().strip()
        self.settings_distro_combo.blockSignals(True)
        self.settings_distro_combo.clear()
        self.settings_distro_combo.addItems(distros)
        self.settings_distro_combo.setCurrentText(current_distro or str(self.bridge_settings.get("wsl_distro") or (distros[0] if distros else "")))
        self.settings_distro_combo.blockSignals(False)

        self.settings_local_radio.setChecked(self.workspace_mode == "local")
        self.settings_wsl_radio.setChecked(self.workspace_mode == "wsl")
        self.settings_ssh_radio.setChecked(self.workspace_mode == "ssh")
        self.settings_local_input.setText(self.workspace if self.workspace_mode == "local" else str(find_workspace() or self.workspace or ""))
        self.settings_linux_path_input.setText(str(self.bridge_settings.get("linux_workspace") or "/mnt/e/work/partner_workspace"))
        self.settings_ssh_host_input.setText(str(self.bridge_settings.get("ssh_host") or ""))
        self.settings_ssh_port_input.setText(str(self.bridge_settings.get("ssh_port") or 22))
        self.settings_ssh_user_input.setText(str(self.bridge_settings.get("ssh_user") or "ubuntu"))
        self.settings_ssh_key_input.setText(str(self.bridge_settings.get("ssh_key") or "/mnt/e/work/temp/zty.pem"))
        self.settings_ssh_workspace_input.setText(str(self.bridge_settings.get("ssh_workspace") or "/home/ubuntu/partner_workspace"))
        self.settings_ssh_partner_dir_input.setText(str(self.bridge_settings.get("ssh_partner_dir") or "/home/ubuntu/partner"))

        self.settings_agent_list.clear()
        lines = []
        preferred_backend = "hermes"
        if self.workspace_mode == "local" and self.workspace:
            agent_cfg = self.load_instance_partner_agent_config(self.workspace)
            preferred_backend = str(agent_cfg.get("backend") or "hermes")
        self.settings_agent_backend_combo.setCurrentText(preferred_backend if preferred_backend in {"hermes", "codex", "openclaw", "claude_code"} else "hermes")
        for item in self._local_agents_cache:
            state = "已检测到" if item.get("available") else "未检测到"
            path = str(item.get("path") or "")
            row = QListWidgetItem(f"{item.get('label')}  ·  {state}")
            row.setToolTip(path or state)
            self.settings_agent_list.addItem(row)
            lines.append(f"{item.get('label')}: {state}")
            if path:
                lines.append(path)
            lines.append("")
        if not lines:
            lines = ["还没有检测到任何本机 Agent。"]
        self.settings_agent_detail.setPlainText("\n".join(lines).strip())

    def save_settings_page(self):
        from partner.config import save_partner_config_data
        from partner.setup import save_workspace_pointer

        if self.settings_local_radio.isChecked():
            ws = self.settings_local_input.text().strip()
            if not ws:
                show_partner_notice(self, "Partner", "请选择本地工作区。")
                return
            os.makedirs(ws, exist_ok=True)
            for sub in ["state", "logs", "data", "00_config"]:
                os.makedirs(os.path.join(ws, sub), exist_ok=True)
            backend = self.settings_agent_backend_combo.currentText().strip() or "hermes"
            config = {
                "workspace": {"path": ws, "readonly_dirs": []},
                "agent": {"backend": backend},
                "scheduler": {"interval_minutes": 30, "max_tasks_per_cycle": 1, "heartbeat_timeout_minutes": 60},
                "name": "Partner",
            }
            save_partner_config_data(ws, config)
            save_workspace_pointer(ws)
            local_settings = dict(self.bridge_settings or {})
            local_settings.update({"mode": "local", "saved_at": datetime.now().isoformat()})
            save_gui_bridge_settings(local_settings, workspace_hint=ws)
            self.workspace = ws
            self.workspace_mode = "local"
        elif self.settings_wsl_radio.isChecked():
            distro = self.settings_distro_combo.currentText().strip()
            linux_path = self.settings_linux_path_input.text().strip()
            if not distro or not linux_path:
                show_partner_notice(self, "Partner", "请填写 WSL 发行版和 Linux 路径。")
                return
            unc = linux_path_to_unc(linux_path, distro)
            wsl_settings = dict(self.bridge_settings or {})
            wsl_settings.update(
                {
                    "mode": "wsl",
                    "wsl_distro": distro,
                    "linux_workspace": linux_path,
                    "unc_workspace": unc,
                    "saved_at": datetime.now().isoformat(),
                }
            )
            save_gui_bridge_settings(wsl_settings)
            save_workspace_pointer(unc)
            self.workspace = unc
            self.workspace_mode = "wsl"
        else:
            host = self.settings_ssh_host_input.text().strip()
            user = self.settings_ssh_user_input.text().strip()
            key = self.settings_ssh_key_input.text().strip()
            remote_ws = self.settings_ssh_workspace_input.text().strip()
            partner_dir = self.settings_ssh_partner_dir_input.text().strip()
            try:
                port = int(self.settings_ssh_port_input.text().strip() or "22")
            except ValueError:
                show_partner_notice(self, "Partner", "SSH 端口必须是整数。")
                return
            if not host or not user or not key or not remote_ws:
                show_partner_notice(self, "Partner", "请填写 SSH host / user / key / remote workspace。")
                return
            ssh_settings = dict(self.bridge_settings or {})
            ssh_settings.update(
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
            save_gui_bridge_settings(ssh_settings)
            self.workspace = remote_ws
            self.workspace_mode = "ssh"
        self.bridge_settings, self.bridge_settings_path = load_gui_bridge_settings_with_path()
        self.request_refresh(force=True, page_index=4)

    def refresh_ollama_page(self):
        self.populate_ollama_instances()
        inst_id, inst_dir = self.current_ollama_instance()
        if hasattr(self, "ollama_source_label"):
            source = "Windows 本地" if self.workspace_mode == "local" else ("Linux / WSL" if self.workspace_mode == "wsl" else f"服务器 {self.bridge_settings.get('ssh_host') or '-'}")
            self.ollama_source_label.setText(
                f"{self.display_instance_label(inst_id, inst_dir) if inst_id else '实例 -'} 的 Ollama 连接管理 · 来源 {source}"
            )
        if not inst_id or not inst_dir:
            self.ollama_endpoint_list.clear()
            self.clear_ollama_form()
            self.ollama_runtime_summary.setText("当前没有可管理的实例。")
            self.ollama_status_view.setPlainText("没有读取到实例，无法配置 Ollama 连接。")
            return
        snap = self.instance_ollama_snapshot(inst_id, inst_dir)
        agent = snap.get("agent") if isinstance(snap.get("agent"), dict) else {}
        pool = agent.get("ollama_pool") if isinstance(agent.get("ollama_pool"), dict) else {}
        endpoints = pool.get("endpoints") if isinstance(pool.get("endpoints"), list) else []
        mode = str(pool.get("mode") or "off")
        enabled = bool(pool.get("enabled", False))
        runtime = snap.get("runtime") if isinstance(snap.get("runtime"), dict) else {}
        pool_status = snap.get("pool_status") if isinstance(snap.get("pool_status"), dict) else {}
        dynamic_status = snap.get("dynamic_status") if isinstance(snap.get("dynamic_status"), dict) else {}
        lite_status = snap.get("lite_status") if isinstance(snap.get("lite_status"), dict) else {}

        self.ollama_enabled_card.set_value("是" if enabled else "否", COLORS["green"] if enabled else COLORS["subtext"])
        self.ollama_mode_card.set_value(mode, COLORS["accent"])
        selected_model = pool_status.get("selected") or dynamic_status.get("selected") or lite_status.get("model") or "-"
        self.ollama_model_card.set_value(selected_model, COLORS["green"] if selected_model != "-" else COLORS["subtext"])
        self.ollama_usage_card.set_value(str(runtime.get("calls") or 0), COLORS["yellow"])
        self.ollama_mode_combo.blockSignals(True)
        self.ollama_mode_combo.setCurrentText(mode if mode in {"off", "lite", "project", "all"} else "off")
        self.ollama_mode_combo.blockSignals(False)

        current_name = None
        current = self.ollama_endpoint_list.currentItem()
        if current:
            payload = current.data(Qt.UserRole)
            current_name = payload.get("name") if isinstance(payload, dict) else None
        self.ollama_endpoint_list.blockSignals(True)
        self.ollama_endpoint_list.clear()
        target_row = 0
        for idx, endpoint in enumerate(endpoints):
            if not isinstance(endpoint, dict):
                continue
            name = str(endpoint.get("name") or f"ollama{idx+1}")
            base_url = str(endpoint.get("base_url") or "")
            model_list = [str(x) for x in (endpoint.get("models") or []) if str(x).strip()]
            location = self.detect_ollama_location_type(base_url).replace("电脑 Ollama", "电脑").replace(" Ollama", "")
            model_hint = model_list[0] if model_list else "未填模型"
            extra = f"{len(model_list)}模型" if len(model_list) > 1 else model_hint
            line = f"{name}  ·  {location}  ·  {extra}"
            item = QListWidgetItem(line)
            item.setToolTip(base_url or line)
            item.setData(Qt.UserRole, endpoint)
            item.setSizeHint(QSize(0, 46))
            self.ollama_endpoint_list.addItem(item)
            if current_name and name == current_name:
                target_row = self.ollama_endpoint_list.count() - 1
        self.ollama_endpoint_list.blockSignals(False)
        if self.ollama_endpoint_list.count():
            self.ollama_endpoint_list.setCurrentRow(target_row)
        else:
            self.clear_ollama_form()

        self.ollama_scope_help.setText(f"当前实例：{self.display_instance_id(inst_id, inst_dir)}。先选使用范围，再维护下面这组连接。")
        status_lines = [
            f"实例: {self.display_instance_id(inst_id, inst_dir)}",
            f"已启用: {'是' if enabled else '否'}",
            f"使用范围: {mode}",
            f"连接数: {len(endpoints)}",
            f"最近调用: {runtime.get('calls') or 0}",
            f"估算 Token: {runtime.get('total_tokens_est') or 0}",
            f"当前选中模型: {selected_model}",
            f"Pool 状态: {pool_status.get('reason') or pool_status.get('fallback') or '-'}",
            f"Dynamic 状态: {dynamic_status.get('reason') or dynamic_status.get('fallback') or '-'}",
            f"Lite 状态: {lite_status.get('reason') or ('available' if lite_status.get('available') else 'not_checked')}",
        ]
        probe_rows = pool_status.get("probe_results") or dynamic_status.get("probe_results") or []
        if probe_rows:
            status_lines.append("")
            status_lines.append("最近探测结果:")
            for row in probe_rows[:12]:
                if not isinstance(row, dict):
                    continue
                status_lines.append(
                    f"- {row.get('endpoint') or row.get('model') or '-'} | {row.get('model') or '-'} | {'OK' if row.get('ok') else 'FAIL'} | {row.get('reason') or ''}"
                )
        self.ollama_runtime_summary.setText(
            f"最近调用 {runtime.get('calls') or 0} 次，失败 {runtime.get('failed') or 0} 次，估算 token {runtime.get('total_tokens_est') or 0}。"
        )
        self.ollama_status_view.setPlainText("\n".join(status_lines))

    def clear_ollama_form(self):
        self.ollama_location_combo.blockSignals(True)
        self.ollama_location_combo.setCurrentText("本机电脑 Ollama")
        self.ollama_location_combo.blockSignals(False)
        self.ollama_name_input.clear()
        self.ollama_url_input.clear()
        self.ollama_models_input.clear()
        self.ollama_url_input.setPlaceholderText("例如 http://127.0.0.1:11434 或 http://203.0.113.10:11434")
        self.ollama_enabled_check.setChecked(True)

    def on_ollama_endpoint_selected(self, current, previous=None):
        if not current:
            self.clear_ollama_form()
            return
        endpoint = current.data(Qt.UserRole) or {}
        base_url = str(endpoint.get("base_url") or "")
        location_type = self.detect_ollama_location_type(base_url)
        self.ollama_location_combo.blockSignals(True)
        self.ollama_location_combo.setCurrentText(location_type)
        self.ollama_location_combo.blockSignals(False)
        self.ollama_name_input.setText(str(endpoint.get("name") or ""))
        self.ollama_url_input.setText(base_url)
        self.ollama_models_input.setText(",".join(str(x) for x in (endpoint.get("models") or [])))
        self.ollama_enabled_check.setChecked(bool(endpoint.get("enabled", True)))
        self.on_ollama_location_changed()

    def on_ollama_location_changed(self):
        choice = self.ollama_location_combo.currentText().strip()
        host = str(self.bridge_settings.get("ssh_host") or "").strip()
        if choice == "本机电脑 Ollama":
            self.ollama_url_input.setPlaceholderText("http://127.0.0.1:11434")
        elif choice == "服务器 Ollama":
            placeholder = f"http://{host}:11434" if host else "http://服务器IP:11434"
            self.ollama_url_input.setPlaceholderText(placeholder)
        else:
            self.ollama_url_input.setPlaceholderText("填写自定义 Ollama 地址，例如 http://10.0.0.8:11434")

    def save_ollama_settings(self):
        inst_id, inst_dir = self.current_ollama_instance()
        if not inst_dir:
            return
        agent = self.load_instance_partner_agent_config(inst_dir)
        pool = agent.get("ollama_pool") if isinstance(agent.get("ollama_pool"), dict) else {}
        endpoints = [e for e in (pool.get("endpoints") or []) if isinstance(e, dict)]
        mode = self.ollama_mode_combo.currentText().strip() or "off"
        name = self.ollama_name_input.text().strip()
        base_url = self.ollama_url_input.text().strip().rstrip("/")
        models = [x.strip() for x in self.ollama_models_input.text().split(",") if x.strip()]
        enabled = self.ollama_enabled_check.isChecked()
        if name or base_url or models:
            if not base_url:
                show_partner_notice(self, "Partner", "请至少填写一个 Ollama 地址。")
                return
            if not name:
                auto_name = self.detect_ollama_location_type(base_url).replace(" Ollama", "")
                host_part = base_url.replace("http://", "").replace("https://", "").replace("/", "")
                name = f"{auto_name}-{host_part}"
            updated = {"name": name, "base_url": base_url, "models": models or ["qwen2.5:7b"], "enabled": enabled}
            replaced = False
            for idx, endpoint in enumerate(endpoints):
                if str(endpoint.get("name") or "") == name:
                    endpoints[idx] = updated
                    replaced = True
                    break
            if not replaced:
                endpoints.append(updated)
        pool["enabled"] = mode != "off"
        pool["mode"] = mode
        pool.setdefault("probe_timeout_sec", 2)
        pool.setdefault("chat_timeout_sec", 90)
        pool.setdefault("max_input_chars", 4000)
        pool["endpoints"] = endpoints
        agent["ollama_pool"] = pool
        dynamic_cfg = agent.get("dynamic_ollama") if isinstance(agent.get("dynamic_ollama"), dict) else {}
        dynamic_cfg["enabled"] = mode in {"project", "all"}
        agent["dynamic_ollama"] = dynamic_cfg
        ok, msg = self.save_instance_partner_agent_config(inst_dir, agent)
        if not ok:
            show_partner_notice(self, "Partner", msg or "Ollama 配置保存失败。")
            return
        self.request_refresh(force=True, page_index=3)

    def add_ollama_endpoint(self):
        self.clear_ollama_form()
        self.ollama_name_input.setFocus()

    def remove_ollama_endpoint(self):
        inst_id, inst_dir = self.current_ollama_instance()
        current = self.ollama_endpoint_list.currentItem()
        if not inst_dir or not current:
            return
        endpoint = current.data(Qt.UserRole) or {}
        name = str(endpoint.get("name") or "").strip()
        if not name:
            return
        agent = self.load_instance_partner_agent_config(inst_dir)
        pool = agent.get("ollama_pool") if isinstance(agent.get("ollama_pool"), dict) else {}
        endpoints = [e for e in (pool.get("endpoints") or []) if isinstance(e, dict) and str(e.get("name") or "").strip() != name]
        pool["endpoints"] = endpoints
        agent["ollama_pool"] = pool
        ok, msg = self.save_instance_partner_agent_config(inst_dir, agent)
        if not ok:
            show_partner_notice(self, "Partner", msg or "删除 Ollama 连接失败。")
            return
        self.request_refresh(force=True, page_index=3)

    def test_ollama_settings(self):
        inst_id, inst_dir = self.current_ollama_instance()
        if not inst_dir:
            return
        self.set_refresh_state(True, "正在探测 Ollama…")
        if self.workspace_mode == "ssh":
            script = (
                "import json\n"
                "from partner.ollama_pool import test_pool\n"
                f"print(json.dumps(test_pool({inst_dir!r}, purpose='project'), ensure_ascii=False))\n"
            )
            ok, out = self.run_ssh(f"cd {shlex.quote(self.workspace_partner_dir())} && {self.remote_python_command(script)}", capture=True)
            if not ok:
                show_partner_notice(self, "Partner", out or "远端 Ollama 探测失败。")
            self._remote_bundle_cache = None
            self._remote_bundle_ts = 0.0
        else:
            try:
                from partner.ollama_pool import test_pool

                test_pool(inst_dir, purpose="project")
            except Exception as exc:
                show_partner_notice(self, "Partner", str(exc))
        self.request_refresh(force=True, page_index=3)


def launch():
    set_windows_app_id()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Partner")
    app.setOrganizationName("Partner")
    if os.path.exists(APP_ICON_PATH):
        app.setWindowIcon(QIcon(APP_ICON_PATH))
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
