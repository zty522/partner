"""Instance management page - view, start, stop, create and manage instances."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
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
from ..widgets import SectionHeader, AccentButton
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
    """Check if a PID is alive, supporting WSL cross-platform when running as Windows exe."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import subprocess as _subprocess
            r = _subprocess.run(
                ["wsl.exe", "ps", "-p", str(pid), "-o", "pid=", "--no-headers"],
                capture_output=True, text=True, timeout=5,
                creationflags=_subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0 and r.stdout.strip():
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


class NewInstanceDialog(QDialog):
    """Dialog for creating a new instance."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("创建新实例")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._id_input = QLineEdit()
        self._id_input.setPlaceholderText("例如: 06, my_bot_1")
        form.addRow("实例 ID:", self._id_input)

        self._ws_input = QLineEdit()
        self._ws_input.setPlaceholderText("留空自动创建")
        form.addRow("工作区路径:", self._ws_input)

        browse_btn = QPushButton("📂 浏览...")
        browse_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: bold;
                min-height: 38px;
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
                border-color: {THEME.accent_h};
            }}
        """)
        browse_btn.clicked.connect(self._browse)
        ws_row = QHBoxLayout()
        ws_row.addWidget(self._ws_input)
        ws_row.addWidget(browse_btn)
        form.addRow("", ws_row)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作区路径")
        if path:
            self._ws_input.setText(path)

    def get_data(self) -> tuple[str, str]:
        return self._id_input.text().strip(), self._ws_input.text().strip()


class InstancesPage(QWidget):
    """Instance management page."""

    def __init__(self, parent: QWidget | None = None, workspace_path: str = ""):
        super().__init__(parent)
        self._workspace = workspace_path
        self._empty_label: QLabel | None = None
        self._selected_instance_dir: str = ""
        self._selected_instance_id: str = ""
        self._selected_row: int = -1
        self._build_ui()
        self._refresh()

    def set_workspace(self, path: str):
        """Update workspace path and reload data."""
        self._workspace = path
        self._refresh()

    def _show_empty_state(self, message: str):
        """Show a centered placeholder message when no instances found."""
        self._table.setRowCount(0)
        if self._empty_label:
            self._empty_label.setText(message)
            self._empty_label.setVisible(True)
        # Keep QQ panel visible so user can configure even without instances

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        title = QLabel("实例管理")
        title.setObjectName("title")
        main_layout.addWidget(title)

        # Top bar: create button
        top_bar = QHBoxLayout()
        create_btn = AccentButton("+ 创建新实例")
        create_btn.clicked.connect(self._on_create_instance)
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
                border-color: {THEME.accent_h};
            }}
        """)
        refresh_btn.clicked.connect(self._refresh)
        top_bar.addWidget(create_btn)
        top_bar.addWidget(refresh_btn)
        top_bar.addStretch()
        main_layout.addLayout(top_bar)

        # Empty state placeholder (shown when no instances)
        self._empty_label = QLabel("暂无实例，请先创建")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color: {THEME.txt3}; font-size: 16px; padding: 60px;"
        )
        self._empty_label.setVisible(False)
        main_layout.addWidget(self._empty_label)

        # Instance table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["实例 ID", "运行环境", "工作区路径", "QQ 状态"])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setShowGrid(False)
        self._table.itemClicked.connect(self._on_instance_selected)
        self._table.setMinimumHeight(200)
        self._table.verticalHeader().setDefaultSectionSize(44)
        self._table.verticalHeader().hide()
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {THEME.card};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                gridline-color: transparent;
                outline: none;
            }}
            QTableWidget::item {{
                padding: 6px 12px;
                border: none;
                color: {THEME.txt};
            }}
            QTableWidget::item:selected {{
                color: {THEME.txt};
            }}
            QTableWidget::item:hover {{
            }}
            QHeaderView::section {{
                background-color: {THEME.bg2};
                color: {THEME.txt2};
                border: none;
                border-bottom: 1px solid {THEME.border};
                padding: 10px 12px;
                font-weight: bold;
                font-size: 12px;
            }}
            QTableWidget:focus {{
                outline: none;
                border: 1px solid {THEME.border};
            }}
        """)

        main_layout.addWidget(self._table)

        # ── QQ Bot Configuration Panel ──
        self._qq_panel = QWidget()
        self._qq_panel.setObjectName("card")
        self._qq_panel.setStyleSheet(f"""
            QWidget#card {{
                background-color: {THEME.card};
                border: 1px solid {THEME.border};
                border-radius: 10px;
            }}
        """)
        qq_layout = QVBoxLayout(self._qq_panel)
        qq_layout.setContentsMargins(24, 18, 24, 18)
        qq_layout.setSpacing(12)

        qq_title_row = QHBoxLayout()
        qq_title_row.setSpacing(8)
        qq_title = QLabel("QQ Bot 配置")
        qq_title.setStyleSheet(f"color: {THEME.txt2}; font-weight: bold; font-size: 14px; background: transparent; border: none;")
        qq_title_row.addWidget(qq_title)

        self._qq_instance_label = QLabel("")
        self._qq_instance_label.setStyleSheet(f"color: {THEME.txt3}; font-size: 12px; background: transparent; border: none;")
        self._qq_instance_label.setVisible(False)
        qq_title_row.addWidget(self._qq_instance_label)
        qq_title_row.addStretch()
        qq_layout.addLayout(qq_title_row)

        # Form fields
        form_widget = QWidget()
        form_widget.setStyleSheet("background: transparent;")
        form_layout = QFormLayout(form_widget)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(8)

        self._qq_app_id = QLineEdit()
        self._qq_app_id.setPlaceholderText("QQ Bot App ID")
        self._qq_app_id.setStyleSheet(f"padding: 8px 12px; border: 1px solid {THEME.border}; border-radius: 10px; background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {THEME.input_bg}, stop:1 {THEME.bg3}); color: {THEME.txt}; min-height: 38px; font-weight: bold;")
        form_layout.addRow("App ID:", self._qq_app_id)

        self._qq_app_secret = QLineEdit()
        self._qq_app_secret.setPlaceholderText("QQ Bot App Secret")
        self._qq_app_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._qq_app_secret.setStyleSheet(f"padding: 8px 12px; border: 1px solid {THEME.border}; border-radius: 10px; background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {THEME.input_bg}, stop:1 {THEME.bg3}); color: {THEME.txt}; min-height: 38px; font-weight: bold;")
        form_layout.addRow("App Secret:", self._qq_app_secret)

        qq_layout.addWidget(form_widget)

        # Action buttons
        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(10)

        save_qq_btn = QPushButton("💾 保存配置")
        save_qq_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                color: white; border: none;
                border-radius: 10px; padding: 8px 20px; font-size: 13px; font-weight: bold;
                min-height: 38px;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent2}, stop:1 {THEME.accent_h});
            }}
            QPushButton:pressed {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent3}, stop:1 #2A5F8A);
            }}
        """)
        save_qq_btn.clicked.connect(self._on_save_qq_config)

        btn_layout.addWidget(save_qq_btn)
        btn_layout.addStretch()
        qq_layout.addWidget(btn_row)

        self._qq_panel.setVisible(True)
        main_layout.addWidget(self._qq_panel)

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
        """Reload the instance table."""
        # Check workspace
        if not self._workspace or not os.path.exists(self._workspace):
            self._show_empty_state("工作区未配置或路径不存在")
            return

        config_path = self._resolve_global_config_path()
        self._table.setRowCount(0)

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
                        # Read partner_config.json for agent backend info
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

        for inst_id, info in instances_map.items():
            row = self._table.rowCount()
            self._table.insertRow(row)

            inst_dir = info.get("working_dir", "")
            if not inst_dir or not os.path.exists(inst_dir):
                inst_dir = self._resolve_instance_dir(inst_id)
            # On Windows, convert WSL paths (/mnt/e/...) to Windows (E:/...)
            if os.name == "nt" and inst_dir.startswith("/mnt/"):
                inst_dir = wsl_to_windows(inst_dir)

            # Determine environment
            env = info.get("environment", "")
            if not env:
                env = infer_environment_from_path(inst_dir)
            env_tag = format_environment_tag(env)

            self._table.setItem(row, 0, QTableWidgetItem(inst_id))

            # Environment tag
            env_item = QTableWidgetItem(env_tag)
            env_item.setData(Qt.ItemDataRole.UserRole, env)
            env_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, env_item)

            self._table.setItem(row, 2, QTableWidgetItem(inst_dir))

            # QQ status: 已配置 / 未配置
            qq_cfg_path = os.path.join(inst_dir, "config", "qq_config.json")
            qq_cfg = _load_json(qq_cfg_path)
            has_qq = bool(qq_cfg.get("app_id", "").strip())
            qq_status_text = "✅ 已配置" if has_qq else "⚪ 未配置"
            self._table.setItem(row, 3, QTableWidgetItem(qq_status_text))

        # Select first instance by default
        if self._table.rowCount() > 0:
            self._highlight_row(0)
            first_item = self._table.item(0, 0)
            if first_item:
                self._on_instance_selected(first_item)

    def _on_create_instance(self):
        """Show the new instance dialog."""
        dialog = NewInstanceDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            inst_id, ws_path = dialog.get_data()
            if not inst_id:
                QMessageBox.warning(self, "提示", "请输入实例 ID")
                return

            config_path = self._resolve_global_config_path()
            config = _load_json(str(config_path))

            if inst_id in config.get("instances", {}):
                QMessageBox.warning(self, "提示", f"实例 {inst_id} 已存在")
                return

            if not ws_path:
                ws_path = os.path.join(self._resolve_instances_dir(), inst_id)

            os.makedirs(ws_path, exist_ok=True)
            from partner.workspace.workspace_layout import ensure_instance_layout
            ensure_instance_layout(ws_path)

            config.setdefault("instances", {})[inst_id] = {
                "enabled": True,
                "working_dir": ws_path,
                "agent_backend": "hermes",
                "interval_minutes": 30,
                "environment": infer_environment_from_path(ws_path),
            }
            _save_json(str(config_path), config)
            self._refresh()

    def _on_start_instance(self, instance_id: str, inst_dir: str):
        """Start an instance as a background process."""
        pid_path = os.path.join(inst_dir, "instance.pid")
        if os.path.exists(pid_path):
            try:
                pid = int(open(pid_path).read().strip())
                if pid > 0:
                    try:
                        os.kill(pid, 0)  # Check if alive
                        QMessageBox.information(self, "提示", f"实例 {instance_id} 已在运行 (PID {pid})")
                        return
                    except OSError:
                        pass  # Stale PID, continue to start
            except Exception:
                pass

        log_path = os.path.join(inst_dir, "state", "record", "instance.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "partner", "--instance-id", instance_id, "--workspace", inst_dir],
                stdout=open(log_path, "a", encoding="utf-8", errors="replace"),
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
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
                os.kill(pid, 15)  # SIGTERM
                import time
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, 9)  # SIGKILL if still alive
                except OSError:
                    pass
            os.remove(pid_path)
            QMessageBox.information(self, "已停止", f"实例 {instance_id} 已停止")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "停止失败", str(e))

    def _get_qq_config_path(self, inst_dir: str) -> str:
        """Get the QQ config path for an instance."""
        return os.path.join(inst_dir, "config", "qq_config.json")

    def _load_qq_config(self, inst_dir: str) -> dict:
        """Load QQ config for a given instance directory."""
        qq_path = self._get_qq_config_path(inst_dir)
        return _load_json(qq_path)

    def _on_instance_selected(self, item: QTableWidgetItem):
        """Show QQ Bot config when an instance is clicked."""
        row = item.row()
        inst_id_item = self._table.item(row, 0)
        if inst_id_item is None:
            return
        inst_id = inst_id_item.text()
        inst_dir = self._resolve_instance_dir(inst_id)

        self._selected_instance_id = inst_id
        self._selected_instance_dir = inst_dir

        # Highlight the clicked row
        self._highlight_row(item.row())

        # Update instance label in QQ panel
        self._qq_instance_label.setText(f"— {inst_id}")
        self._qq_instance_label.setVisible(True)

        # Load and populate QQ config
        qq_cfg = self._load_qq_config(inst_dir)
        self._qq_app_id.setText(qq_cfg.get("app_id", ""))
        self._qq_app_secret.setText(qq_cfg.get("app_secret", ""))

        self._qq_panel.setVisible(True)

    def _highlight_row(self, row: int):
        """Highlight the selected row with a subtle background."""
        # Clear previous highlight
        if self._selected_row >= 0 and self._selected_row < self._table.rowCount():
            for col in range(self._table.columnCount()):
                item = self._table.item(self._selected_row, col)
                if item:
                    item.setBackground(QColor(THEME.card))
        # Apply new highlight
        if row >= 0 and row < self._table.rowCount():
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item:
                    item.setBackground(QColor(THEME.card_hl))
        self._selected_row = row

    def _on_save_qq_config(self):
        """Save the QQ Bot config for the selected instance."""
        if not self._selected_instance_dir:
            QMessageBox.warning(self, "提示", "请先选择一个实例")
            return

        inst_dir = self._selected_instance_dir
        qq_path = self._get_qq_config_path(inst_dir)
        os.makedirs(os.path.dirname(qq_path), exist_ok=True)

        qq_cfg = {
            "app_id": self._qq_app_id.text().strip(),
            "app_secret": self._qq_app_secret.text().strip(),
        }
        _save_json(qq_path, qq_cfg)
        QMessageBox.information(self, "保存成功", f"QQ Bot 配置已保存到实例 {self._selected_instance_id}")
        self._refresh()
