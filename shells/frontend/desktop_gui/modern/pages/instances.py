"""Instance management page - view, start, stop, create and manage instances with card-based layout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from partner.monitoring.instance_root import (
    resolve_global_config_path,
    resolve_instance_workspace,
    resolve_instances_dir,
    resolve_partner_root,
)

from ..theme import THEME
from ..widgets import SectionHeader, AccentButton, fix_combo_wheel, COMBO_WHITE_VIEW_STYLE, DirBrowser
from ..utils.path_mapper import format_environment_tag, infer_environment_from_path, wsl_to_windows


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _is_pid_alive(pid: int) -> bool:
    """Check if a PID is alive, supporting both Windows and WSL processes."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            os.kill(pid, 0)
            return True
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        pass
    return False


def _check_status(inst_dir: str) -> str:
    inst_pid_path = os.path.join(inst_dir, "instance.pid")
    try:
        pid = int(open(inst_pid_path).read().strip())
        if pid > 0 and _is_pid_alive(pid):
            return "running"
    except Exception:
        pass
    heartbeat_path = os.path.join(inst_dir, "state", "heartbeat.json")
    hb = _load_json(heartbeat_path)
    ts = hb.get("last_heartbeat", "")
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            if (now - dt).total_seconds() < 180:
                return "running"
        except Exception:
            pass
    return "stopped"


# ---- Card style template ----
_CARD_STYLE = f"""
    QFrame#inst_card {{
        background-color: {THEME.card};
        border: 1px solid {THEME.border};
        border-radius: 12px;
    }}
"""

_ENV_COMBO_STYLE = f"""
    QComboBox {{
        padding: 6px 12px;
        border: 1px solid {THEME.border};
        border-radius: 8px;
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {THEME.input_bg}, stop:1 {THEME.bg3});
        color: {THEME.txt};
        font-size: 12px;
        font-weight: bold;
        min-height: 34px;
        min-width: 100px;
    }}
    QComboBox:hover {{
        border-color: {THEME.accent};
    }}
    QComboBox::drop-down {{
        border: none;
        padding-right: 6px;
    }}
    QComboBox::down-arrow {{
        image: none;
        border: none;
    }}
    {COMBO_WHITE_VIEW_STYLE}
"""

_INPUT_STYLE = f"""
    QLineEdit {{
        padding: 6px 12px;
        border: 1px solid {THEME.border};
        border-radius: 8px;
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {THEME.input_bg}, stop:1 {THEME.bg3});
        color: {THEME.txt};
        font-size: 12px;
        min-height: 34px;
    }}
    QLineEdit:hover {{
        border-color: {THEME.accent};
    }}
    QLineEdit:focus {{
        border-color: {THEME.accent};
    }}
"""

_BROWSE_BTN_STYLE = f"""
    QPushButton {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
        color: {THEME.txt2};
        border: 1px solid {THEME.border};
        border-radius: 8px;
        padding: 6px 14px;
        font-size: 12px;
        font-weight: bold;
        min-height: 34px;
    }}
    QPushButton:hover {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {THEME.card_hl}, stop:1 {THEME.bg2});
        border-color: {THEME.accent};
        color: {THEME.accent};
    }}
    QPushButton:pressed {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {THEME.bg3}, stop:1 {THEME.border});
    }}
"""

_ACTION_BTN_STYLE = f"""
    QPushButton {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {THEME.accent}, stop:1 {THEME.accent3});
        color: white;
        border: none;
        border-radius: 8px;
        padding: 6px 16px;
        font-size: 12px;
        font-weight: bold;
        min-height: 34px;
    }}
    QPushButton:hover {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {THEME.accent2}, stop:1 {THEME.accent_h});
    }}
    QPushButton:pressed {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {THEME.accent3}, stop:1 #2A5F8A);
    }}
"""

_STOP_BTN_STYLE = f"""
    QPushButton {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #E53935, stop:1 #C62828);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 6px 16px;
        font-size: 12px;
        font-weight: bold;
        min-height: 34px;
    }}
    QPushButton:hover {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #EF5350, stop:1 #D32F2F);
    }}
    QPushButton:pressed {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #C62828, stop:1 #B71C1C);
    }}
"""


class NewInstanceDialog(QDialog):
    """Simplified dialog for creating a new instance — just ID input, path auto-derived."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("新建实例")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        prompt = QLabel("输入实例 ID，工作区路径将自动从 workspace 派生。")
        prompt.setStyleSheet(f"color: {THEME.txt2}; font-size: 13px;")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        self._id_input = QLineEdit()
        self._id_input.setPlaceholderText("例如: 06, my_bot_1")
        self._id_input.setStyleSheet(f"""
            QLineEdit {{
                padding: 10px 14px;
                border: 1px solid {THEME.border};
                border-radius: 10px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.input_bg}, stop:1 {THEME.bg3});
                color: {THEME.txt};
                font-size: 14px;
                font-weight: bold;
                min-height: 40px;
            }}
            QLineEdit:focus {{
                border-color: {THEME.accent};
            }}
        """)
        layout.addWidget(self._id_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_data(self) -> str:
        return self._id_input.text().strip()


class InstanceCard(QFrame):
    """A single instance card showing editable fields, status, and controls."""

    # Signal: (instance_id, inst_dir)
    start_requested = Signal(str, str)
    stop_requested = Signal(str, str)
    selected = Signal(str, str)  # (instance_id, inst_dir)

    def __init__(self, instance_id: str, inst_dir: str, parent=None):
        super().__init__(parent)
        self._instance_id = instance_id
        self._inst_dir = inst_dir
        self.setObjectName("inst_card")
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        self.setStyleSheet(_CARD_STYLE)
        self.setFixedHeight(300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(5)

        # ── Title row: "实例 {id}" + env tag + status dot ──
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        self._title_label = QLabel(f"实例 {self._instance_id}")
        self._title_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {THEME.txt}; background: transparent; border: none;"
        )
        title_row.addWidget(self._title_label)

        self._env_tag = QLabel()
        self._env_tag.setStyleSheet(
            f"font-size: 11px; font-weight: bold; color: {THEME.accent}; "
            f"background-color: {THEME.card_hl}; border: 1px solid {THEME.accent}; "
            f"border-radius: 4px; padding: 2px 8px;"
        )
        title_row.addWidget(self._env_tag)

        self._status_dot = QLabel()
        self._status_dot.setFixedSize(12, 12)
        title_row.addWidget(self._status_dot)

        title_row.addStretch()

        # Instance ID read-only label
        id_label = QLabel(f"ID: {self._instance_id}")
        id_label.setStyleSheet(
            f"font-size: 11px; color: {THEME.txt3}; background: transparent; border: none;"
        )
        title_row.addWidget(id_label)

        layout.addLayout(title_row)

        # ── Environment ──
        env_row = QHBoxLayout()
        env_row.setSpacing(6)
        env_label = QLabel("环境:")
        env_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {THEME.txt2}; background: transparent; border: none;"
        )
        env_label.setFixedWidth(60)
        env_row.addWidget(env_label)

        self._env_combo = QComboBox()
        self._env_combo.setStyleSheet(_ENV_COMBO_STYLE)
        # Use QStandardItemModel for fine-grained control
        self._env_model = QStandardItemModel()
        wsl_item = QStandardItem("🐧 WSL Linux")
        self._env_model.appendRow(wsl_item)
        win_item = QStandardItem("🪟 Windows")
        self._env_model.appendRow(win_item)
        # SSH item — may be disabled based on config
        self._env_ssh_item = QStandardItem("☁️ 远程服务器")
        # Check if any SSH servers configured
        config_path = self._get_global_config_path()
        config = _load_json(config_path) if os.path.exists(config_path) else {}
        servers = config.get("servers", {})
        has_ssh = bool(servers) if isinstance(servers, dict) else bool(servers)
        if not has_ssh:
            self._env_ssh_item.setEnabled(False)
            self._env_ssh_item.setToolTip("请在配置中心→Linux 中配置 SSH 服务器")
        self._env_model.appendRow(self._env_ssh_item)
        self._env_combo.setModel(self._env_model)
        self._env_combo.currentTextChanged.connect(self._on_env_changed)
        fix_combo_wheel(self._env_combo)
        env_row.addWidget(self._env_combo)
        env_row.addStretch()
        layout.addLayout(env_row)

        # ── Working directory ──
        ws_row = QHBoxLayout()
        ws_row.setSpacing(6)
        ws_label = QLabel("工作目录:")
        ws_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {THEME.txt2}; background: transparent; border: none;"
        )
        ws_label.setFixedWidth(60)
        ws_row.addWidget(ws_label)

        self._ws_input = QLineEdit()
        self._ws_input.setStyleSheet(_INPUT_STYLE)
        ws_row.addWidget(self._ws_input, 1)

        self._browse_btn = QPushButton("📂 浏览")
        self._browse_btn.setStyleSheet(_BROWSE_BTN_STYLE)
        self._browse_btn.clicked.connect(self._browse)
        ws_row.addWidget(self._browse_btn)
        layout.addLayout(ws_row)

        # ── QQ App ID ──
        qq_app_id_row = QHBoxLayout()
        qq_app_id_row.setSpacing(6)
        qq_app_id_label = QLabel("QQ App ID:")
        qq_app_id_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {THEME.txt2}; background: transparent; border: none;"
        )
        qq_app_id_label.setFixedWidth(90)
        qq_app_id_row.addWidget(qq_app_id_label)

        self._qq_app_id_edit = QLineEdit()
        self._qq_app_id_edit.setPlaceholderText("输入 QQ App ID")
        self._qq_app_id_edit.setStyleSheet(_INPUT_STYLE)
        qq_app_id_row.addWidget(self._qq_app_id_edit, 1)
        layout.addLayout(qq_app_id_row)

        # ── QQ App Secret ──
        qq_app_secret_row = QHBoxLayout()
        qq_app_secret_row.setSpacing(6)
        qq_app_secret_label = QLabel("QQ App Secret:")
        qq_app_secret_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {THEME.txt2}; background: transparent; border: none;"
        )
        qq_app_secret_label.setFixedWidth(90)
        qq_app_secret_row.addWidget(qq_app_secret_label)

        self._qq_app_secret_edit = QLineEdit()
        self._qq_app_secret_edit.setPlaceholderText("输入 QQ App Secret")
        self._qq_app_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._qq_app_secret_edit.setStyleSheet(_INPUT_STYLE)
        qq_app_secret_row.addWidget(self._qq_app_secret_edit, 1)
        layout.addLayout(qq_app_secret_row)

        # ── Action buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._start_btn = QPushButton("▶ 启动")
        self._start_btn.setStyleSheet(_ACTION_BTN_STYLE)
        self._start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("⏹ 停止")
        self._stop_btn.setStyleSheet(_STOP_BTN_STYLE)
        self._stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self._stop_btn)

        self._save_btn = QPushButton("💾 保存")
        self._save_btn.setStyleSheet(_ACTION_BTN_STYLE)
        self._save_btn.clicked.connect(self._on_save_config)
        btn_row.addWidget(self._save_btn)

        btn_row.addStretch()

        self._status_label = QLabel()
        self._status_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {THEME.txt2}; background: transparent; border: none;"
        )
        btn_row.addWidget(self._status_label)

        layout.addLayout(btn_row)

    def _on_env_changed(self, env_text: str):
        """Auto-convert working directory path when environment changes."""
        old_path = self._ws_input.text()
        if old_path:
            # Map the path based on new environment
            new_path = self._convert_path(old_path, env_text)
            if new_path and new_path != old_path:
                self._ws_input.blockSignals(True)
                self._ws_input.setText(new_path)
                self._ws_input.blockSignals(False)

        # Toggle browse button type
        is_wsl = "WSL" in env_text
        is_ssh = "远程" in env_text or "SSH" in env_text
        if is_wsl or is_ssh:
            self._browse_btn.setText("📂 浏览 (WSL)" if is_wsl else "📂 浏览 (SSH)")
            self._browse_btn.setVisible(True)
        else:
            self._browse_btn.setText("📂 浏览")
            self._browse_btn.setVisible(True)

    def _browse(self):
        """Open directory browser based on current environment."""
        env_text = self._env_combo.currentText()
        if "WSL" in env_text:
            self._on_browse_wsl()
        elif "远程" in env_text or "SSH" in env_text:
            self._on_browse_ssh()
        else:
            path = QFileDialog.getExistingDirectory(self, "选择工作区路径")
            if path:
                self._ws_input.setText(path)

    def _get_global_config_path(self) -> str:
        """Resolve global_config.json path using instance dir."""
        workspace_root = os.path.dirname(os.path.dirname(self._inst_dir))
        return os.path.join(workspace_root, "config", "global_config.json")

    def _on_browse_wsl(self):
        """Open WSL directory browser dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle("选择 WSL 工作目录")
        dialog.resize(600, 400)
        browser = DirBrowser(env_type="wsl")
        layout = QVBoxLayout(dialog)
        layout.addWidget(browser)
        browser.path_selected.connect(lambda p: self._on_path_selected(p, dialog))
        dialog.exec()

    def _on_browse_ssh(self):
        """Open SSH directory browser dialog (requires configured SSH)."""
        config_path = self._get_global_config_path()
        config = _load_json(config_path) if os.path.exists(config_path) else {}
        servers = config.get("servers", {})
        if isinstance(servers, dict) and servers:
            # Use first configured server
            first_name = list(servers.keys())[0]
            first_server = servers[first_name]
            host = first_server.get("host", "")
            user = first_server.get("user", "root")
            ssh_host = f"{user}@{host}"
            dialog = QDialog(self)
            dialog.setWindowTitle(f"选择 SSH 工作目录 ({first_name})")
            dialog.resize(600, 400)
            browser = DirBrowser(env_type="ssh", ssh_host=ssh_host)
            layout = QVBoxLayout(dialog)
            layout.addWidget(browser)
            browser.path_selected.connect(lambda p: self._on_path_selected(p, dialog))
            dialog.exec()
        else:
            QMessageBox.information(self, "提示", "请在配置中心→Linux 中配置 SSH 服务器")

    def _on_path_selected(self, path: str, dialog: QDialog):
        self._ws_input.setText(path)
        dialog.accept()

    def _convert_path(self, path: str, env_text: str) -> str:
        """Convert a path string between environments."""
        # Simple conversions: WSL Linux <-> Windows
        is_wsl = "WSL" in env_text
        is_ssh = "远程" in env_text or "SSH" in env_text
        is_win = "Windows" in env_text or env_text.lower() == "windows"
        if is_win and path.startswith("/mnt/"):
            parts = path.split("/")
            # /mnt/e/work/... -> E:/work/...
            if len(parts) >= 3:
                drive = parts[2].upper()
                rest = "/".join(parts[3:])
                return f"{drive}:/{rest}"
        elif (is_wsl or is_ssh) and ":" in path:
            # E:/work/... -> /mnt/e/work/...
            drive_letter = path[0].lower()
            rest = path[3:].replace("\\", "/")
            return f"/mnt/{drive_letter}/{rest}"
        return path

    def _on_start(self):
        self.start_requested.emit(self._instance_id, self._inst_dir)

    def _on_stop(self):
        self.stop_requested.emit(self._instance_id, self._inst_dir)

    def _on_save_config(self):
        """Save all config fields to global_config.json and qq_config.json."""
        workspace_root = os.path.dirname(os.path.dirname(self._inst_dir))

        # Read env from combo
        env_text = self._env_combo.currentText()
        env_map = {"🐧 WSL Linux": "wsl", "🪟 Windows": "windows", "☁️ 远程服务器": "ssh"}
        env_val = env_map.get(env_text, "wsl")

        working_dir = self._ws_input.text().strip()
        app_id = self._qq_app_id_edit.text().strip()
        app_secret = self._qq_app_secret_edit.text().strip()

        # Save to global_config.json
        config_path = self._get_global_config_path()
        config = _load_json(config_path) if os.path.exists(config_path) else {}
        config.setdefault("instances", {})[self._instance_id] = {
            "environment": env_val,
            "working_dir": working_dir,
        }
        _save_json(config_path, config)

        # Save to qq_config.json
        qq_cfg_path = os.path.join(workspace_root, "config", "qq_config.json")
        qq_data = {
            "app_id": app_id,
            "app_secret": app_secret,
            "instance_id": self._instance_id,
        }
        _save_json(qq_cfg_path, qq_data)

        self.refresh()
        QMessageBox.information(self, "配置保存", "环境、工作目录和 QQ 配置已保存")

    def refresh(self):
        """Update card display from current filesystem state."""
        # Determine environment
        config_path = str(resolve_global_config_path())
        config = _load_json(config_path) if os.path.exists(config_path) else {}
        instances = config.get("instances", {})
        inst_cfg = instances.get(self._instance_id, {})
        env = inst_cfg.get("environment", "") or infer_environment_from_path(self._inst_dir)
        working_dir = inst_cfg.get("working_dir", self._inst_dir)

        # Set env combo (block signals to avoid triggering path conversion)
        env_map = {"wsl": "🐧 WSL Linux", "windows": "🪟 Windows", "ssh": "☁️ 远程服务器",
                    "🐧 WSL Linux": "🐧 WSL Linux", "🪟 Windows": "🪟 Windows", "☁️ 远程服务器": "☁️ 远程服务器"}
        env_display = env_map.get(env.lower(), "🐧 WSL Linux") if env else "🐧 WSL Linux"
        self._env_combo.blockSignals(True)
        idx = self._env_combo.findText(env_display)
        if idx >= 0:
            self._env_combo.setCurrentIndex(idx)
        else:
            self._env_combo.setCurrentText(env_display)
        self._env_combo.blockSignals(False)

        # Set env tag
        tag = format_environment_tag(env)
        self._env_tag.setText(tag)

        # Set working dir (block signals to avoid path conversion loop)
        self._ws_input.blockSignals(True)
        self._ws_input.setText(working_dir)
        self._ws_input.blockSignals(False)

        # Browse button visibility based on environment
        self._browse_btn.setVisible(True)

        # Status dot
        status = _check_status(self._inst_dir)
        if status == "running":
            self._status_dot.setText("●")
            self._status_dot.setStyleSheet(
                f"color: {THEME.green}; font-size: 16px; background: transparent; border: none;"
            )
            self._status_label.setText("● 运行中")
            self._status_label.setStyleSheet(
                f"font-size: 12px; font-weight: bold; color: {THEME.green}; background: transparent; border: none;"
            )
        else:
            self._status_dot.setText("●")
            self._status_dot.setStyleSheet(
                f"color: {THEME.txt3}; font-size: 16px; background: transparent; border: none;"
            )
            self._status_label.setText("○ 已停止")
            self._status_label.setStyleSheet(
                f"font-size: 12px; font-weight: bold; color: {THEME.txt3}; background: transparent; border: none;"
            )

        # QQ config: load from workspace_root/config/qq_config.json
        workspace_root = os.path.dirname(os.path.dirname(self._inst_dir))
        qq_cfg_path = os.path.join(workspace_root, "config", "qq_config.json")
        qq_cfg = _load_json(qq_cfg_path)
        # Only show config if instance_id matches
        cfg_inst_id = qq_cfg.get("instance_id", "")
        app_id = qq_cfg.get("app_id", "").strip()
        app_secret = qq_cfg.get("app_secret", "").strip()
        if cfg_inst_id == self._instance_id and app_id:
            self._qq_app_id_edit.setText(app_id)
            self._qq_app_secret_edit.setText(app_secret)
        else:
            self._qq_app_id_edit.setText("")
            self._qq_app_secret_edit.setText("")

    def instance_id(self) -> str:
        return self._instance_id

    def inst_dir(self) -> str:
        return self._inst_dir

    def get_config(self) -> dict:
        """Return current card config values."""
        env_text = self._env_combo.currentText()
        env_map_reverse = {"🐧 WSL Linux": "wsl", "🪟 Windows": "windows", "☁️ 远程服务器": "ssh"}
        env_val = env_map_reverse.get(env_text, env_text.lower())
        return {
            "working_dir": self._ws_input.text(),
            "environment": env_val,
        }


class InstancesPage(QWidget):
    """Instance management page with card-based layout."""

    instances_changed = Signal()  # Emitted when instances are created/modified

    def __init__(self, parent: QWidget | None = None, workspace_path: str = ""):
        super().__init__(parent)
        self._workspace = workspace_path
        self._cards: list[InstanceCard] = []
        self._selected_instance_id: str = ""
        self._selected_instance_dir: str = ""
        self._build_ui()
        self._refresh()

    def set_workspace(self, path: str):
        """Update workspace path and reload data."""
        self._workspace = path
        self._refresh()

    def _show_empty_state(self, message: str):
        """Show a centered placeholder message when no instances found."""
        for card in self._cards:
            card.setVisible(False)
        if self._empty_label:
            self._empty_label.setText(message)
            self._empty_label.setVisible(True)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # Title
        title = QLabel("实例管理")
        title.setObjectName("title")
        main_layout.addWidget(title)

        # Top bar: create + refresh buttons
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        create_btn = AccentButton("+ 新建实例")
        create_btn.clicked.connect(self._on_create_instance)
        top_bar.addWidget(create_btn)

        refresh_btn = QPushButton("⟳ 刷新")
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: bold;
                min-height: 42px;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.card_hl}, stop:1 {THEME.bg2});
                border-color: {THEME.accent};
                color: {THEME.accent};
            }}
            QPushButton:pressed {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg3}, stop:1 {THEME.border});
            }}
        """)
        refresh_btn.clicked.connect(self._refresh)
        top_bar.addWidget(refresh_btn)
        top_bar.addStretch()
        main_layout.addLayout(top_bar)

        # Empty state placeholder
        self._empty_label = QLabel("暂无实例，请先创建")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color: {THEME.txt3}; font-size: 16px; padding: 60px;"
        )
        self._empty_label.setVisible(False)
        main_layout.addWidget(self._empty_label)

        # Scroll area for instance cards
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet(f"""
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background-color: {THEME.bg};
                width: 8px;
                border: none;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {THEME.border};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {THEME.txt3};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

        self._cards_container = QWidget()
        self._cards_container.setStyleSheet("background: transparent;")
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(12)
        self._cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._scroll_area.setWidget(self._cards_container)
        main_layout.addWidget(self._scroll_area, 1)

    def _resolve_global_config_path(self) -> str:
        """Resolve global_config.json using workspace."""
        if self._workspace:
            return os.path.join(self._workspace, "config", "global_config.json")
        return str(resolve_global_config_path())

    def _resolve_instances_dir(self) -> str:
        """Resolve instances directory using workspace."""
        if self._workspace:
            return os.path.join(self._workspace, "instances")
        return str(resolve_instances_dir())

    def _resolve_instance_dir(self, instance_id: str) -> str:
        """Resolve instance directory using workspace."""
        if self._workspace:
            return os.path.join(self._workspace, "instances", instance_id)
        return str(resolve_instance_workspace(instance_id))

    def _refresh(self):
        """Reload instance cards from config and filesystem."""
        # Check workspace
        if not self._workspace or not os.path.exists(self._workspace):
            self._show_empty_state("工作区未配置或路径不存在")
            return

        config_path = self._resolve_global_config_path()

        # Collect instances from config and filesystem
        instances_map = {}

        # Try config file first
        if os.path.exists(config_path):
            config = _load_json(config_path)
            instances_map.update(config.get("instances", {}))

        # Fallback: scan instances directory
        if not instances_map:
            instances_dir = self._resolve_instances_dir()
            if os.path.exists(instances_dir):
                for entry in sorted(os.listdir(instances_dir)):
                    inst_path = os.path.join(instances_dir, entry)
                    if os.path.isdir(inst_path):
                        inst_info = {"working_dir": inst_path}
                        partner_cfg_path = os.path.join(inst_path, "partner_config.json")
                        if os.path.exists(partner_cfg_path):
                            pc = _load_json(partner_cfg_path)
                            inst_info["environment"] = infer_environment_from_path(inst_path)
                        instances_map[entry] = inst_info

        if not instances_map:
            self._show_empty_state("暂无实例，请先创建")
            return

        if self._empty_label:
            self._empty_label.setVisible(False)

        # Clear old cards
        self._clear_cards()

        # Create cards for each instance
        for inst_id, info in instances_map.items():
            inst_dir = info.get("working_dir", "")
            if not inst_dir or not os.path.exists(inst_dir):
                inst_dir = self._resolve_instance_dir(inst_id)
            # On Windows, convert WSL paths
            if os.name == "nt" and inst_dir.startswith("/mnt/"):
                inst_dir = wsl_to_windows(inst_dir)

            card = InstanceCard(inst_id, inst_dir)
            card.start_requested.connect(self._on_start_instance)
            card.stop_requested.connect(self._on_stop_instance)
            self._cards.append(card)
            self._cards_layout.addWidget(card)

        # Select first card by default
        if self._cards:
            first_card = self._cards[0]
            self._selected_instance_id = first_card.instance_id()
            self._selected_instance_dir = first_card.inst_dir()

    def _clear_cards(self):
        """Remove all existing card widgets."""
        for card in self._cards:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards = []

    def _on_create_instance(self):
        """Show the new instance dialog."""
        dialog = NewInstanceDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            inst_id = dialog.get_data()
            if not inst_id:
                QMessageBox.warning(self, "提示", "请输入实例 ID")
                return

            config_path = self._resolve_global_config_path()
            config = _load_json(str(config_path))

            if inst_id in config.get("instances", {}):
                QMessageBox.warning(self, "提示", f"实例 {inst_id} 已存在")
                return

            ws_path = os.path.join(self._resolve_instances_dir(), inst_id)
            os.makedirs(ws_path, exist_ok=True)

            # Defer ensure_instance_layout to avoid blocking
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, lambda: self._ensure_instance_layout(ws_path))

            # Determine environment
            ws_env = ""
            partner_cfg_path = os.path.join(
                os.path.dirname(str(config_path)), "partner_config.json"
            )
            if os.path.exists(partner_cfg_path):
                pc = _load_json(partner_cfg_path)
                ws_env = pc.get("workspace", {}).get("environment", "")
            env = ws_env or infer_environment_from_path(ws_path)

            config.setdefault("instances", {})[inst_id] = {
                "enabled": True,
                "working_dir": ws_path,
                "agent_backend": "hermes",
                "interval_minutes": 30,
                "environment": env,
            }
            _save_json(str(config_path), config)

            # Create stub qq_config.json if it doesn't exist
            workspace_root = os.path.dirname(os.path.dirname(str(config_path)))
            qq_cfg_path = os.path.join(workspace_root, "config", "qq_config.json")
            if not os.path.exists(qq_cfg_path):
                _save_json(qq_cfg_path, {
                    "app_id": "",
                    "app_secret": "",
                    "instance_id": inst_id,
                })

            self._refresh()
            self.instances_changed.emit()

    def _ensure_instance_layout(self, ws_path: str):
        """Create the instance directory structure in background (deferred)."""
        try:
            from partner.workspace.workspace_layout import ensure_instance_layout
            ensure_instance_layout(ws_path)
        except Exception as e:
            print(f"⚠ instance layout error: {e}")

    def _on_start_instance(self, instance_id: str, inst_dir: str):
        """Start an instance as a background process."""
        pid_path = os.path.join(inst_dir, "instance.pid")
        if os.path.exists(pid_path):
            try:
                pid = int(open(pid_path).read().strip())
                if pid > 0:
                    try:
                        os.kill(pid, 0)
                        QMessageBox.information(self, "提示", f"实例 {instance_id} 已在运行 (PID {pid})")
                        return
                    except OSError:
                        pass
            except Exception:
                pass

        # Check heartbeat
        hb = _load_json(os.path.join(inst_dir, "state", "heartbeat.json"))
        ts = hb.get("last_heartbeat", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                if (now - dt).total_seconds() < 180:
                    QMessageBox.information(
                        self, "提示",
                        f"实例 {instance_id} 正在运行（平台间心跳检测，PID 不可见）"
                    )
                    return
            except Exception:
                pass

        log_path = os.path.join(inst_dir, "state", "record", "instance.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        try:
            import shutil as _sh
            _python_exe = _sh.which("python.exe") or _sh.which("python") or "python"
            cmd = [_python_exe, "-m", "partner",
                   "--instance-id", instance_id, "--workspace", inst_dir]
            import subprocess as _sp_root
            try:
                r = _sp_root.run(
                    [_python_exe, "-c",
                     "import partner; import os; "
                     "print(os.path.normpath(os.path.join(partner.__file__, '..', '..')))"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=_sp_root.CREATE_NO_WINDOW,
                )
                project_root = r.stdout.strip() if r.returncode == 0 else None
            except Exception:
                project_root = None
            launch_kwargs = {"cwd": project_root} if project_root else {}

            proc = subprocess.Popen(
                cmd,
                stdout=open(log_path, "a", encoding="utf-8", errors="replace"),
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                **launch_kwargs,
            )
            with open(pid_path, "w") as f:
                f.write(str(proc.pid))
            QMessageBox.information(self, "启动成功", f"实例 {instance_id} 已启动 (PID {proc.pid})")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "启动失败", str(e))

    def _on_stop_instance(self, instance_id: str, inst_dir: str):
        """Stop an instance by killing its process."""
        pid_path = os.path.join(inst_dir, "instance.pid")
        if not os.path.exists(pid_path):
            QMessageBox.information(self, "提示", f"实例 {instance_id} 未运行")
            return
        try:
            pid = int(open(pid_path).read().strip())
            if pid > 0:
                os.kill(pid, 15)
                import time
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, 9)
                except OSError:
                    pass
            os.remove(pid_path)
            QMessageBox.information(self, "已停止", f"实例 {instance_id} 已停止")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "停止失败", str(e))
