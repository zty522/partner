"""Agent management page - register, view, and manage agents.

Left-list + right-detail layout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFormLayout,
)

from partner.agents.registry import AgentRegistry
from partner.agents.manifest import AgentManifest
from partner.monitoring.instance_root import resolve_partner_root

from ..theme import THEME


# ── Built-in predefined manifests for quick add ──

BUILTIN_MANIFESTS = {
    "Hermes": {
        "name": "hermes",
        "version": "2.0.0",
        "description": "Nous Research's flagship general-purpose AI agent",
        "capabilities": ["reasoning", "coding", "research", "analysis"],
        "input_formats": ["text", "json", "markdown"],
        "output_formats": ["text", "json", "markdown"],
        "endpoint_type": "cli",
        "endpoint_config": {"command": "hermes"},
        "timeout": 300,
        "health_check_cmd": "which hermes",
    },
    "OpenClaw": {
        "name": "openclaw",
        "version": "1.0.0",
        "description": "Open-source code generation and analysis agent",
        "capabilities": ["code_generation", "code_review", "refactoring"],
        "input_formats": ["text", "code", "diff"],
        "output_formats": ["text", "code", "diff"],
        "endpoint_type": "cli",
        "endpoint_config": {"command": "openclaw"},
        "timeout": 300,
        "health_check_cmd": "which openclaw",
    },
    "Codex": {
        "name": "codex",
        "version": "1.0.0",
        "description": "OpenAI Codex-powered code generation agent",
        "capabilities": ["code_generation", "code_completion", "translation"],
        "input_formats": ["text", "code"],
        "output_formats": ["text", "code"],
        "endpoint_type": "http",
        "endpoint_config": {"url": "https://api.openai.com/v1/completions"},
        "timeout": 60,
        "health_check_cmd": "",
    },
    "CytoBridge": {
        "name": "cytobridge",
        "version": "1.0.0",
        "description": "Single-cell trajectory inference and analysis agent",
        "capabilities": ["trajectory_inference", "cell_dynamics", "single_cell_analysis"],
        "input_formats": ["h5ad", "loom", "csv"],
        "output_formats": ["h5ad", "pdf", "png", "json"],
        "endpoint_type": "cli",
        "endpoint_config": {"command": "cytobridge"},
        "timeout": 600,
        "health_check_cmd": "which cytobridge",
    },
}


class AddAgentDialog(QDialog):
    """Dialog for adding a new agent manifest."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._manifest: AgentManifest | None = None
        self.setWindowTitle("添加 Agent")
        self.setMinimumSize(600, 500)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Tabs
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                background-color: {THEME.bg};
                border: 1px solid {THEME.border};
                border-radius: 6px;
            }}
            QTabBar::tab {{
                background-color: {THEME.bg2};
                color: {THEME.txt2};
                border: 1px solid {THEME.border};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 10px 22px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {THEME.bg};
                color: {THEME.accent};
                border-bottom: 2px solid {THEME.accent};
            }}
            QTabBar::tab:hover {{
                background-color: {THEME.card_hl};
            }}
        """)

        # Tab 1: Quick add (now first tab)
        tab_quick = QWidget()
        tab_quick_layout = QVBoxLayout(tab_quick)
        tab_quick_layout.setContentsMargins(12, 12, 12, 12)
        tab_quick_layout.setSpacing(8)

        quick_label = QLabel("快速添加预定义的 Agent:")
        quick_label.setStyleSheet(f"font-size: 13px; color: {THEME.txt}; font-weight: bold;")

        # Use buttons instead of list for quick add
        quick_btn_layout = QVBoxLayout()
        quick_btn_layout.setSpacing(8)
        for name, manifest_data in BUILTIN_MANIFESTS.items():
            btn = QPushButton(f"{name} — {manifest_data['description'][:60]}")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {THEME.card};
                    color: {THEME.txt};
                    border: 1px solid {THEME.border};
                    border-radius: 8px;
                    padding: 10px 16px;
                    text-align: left;
                    font-size: 13px;
                    min-height: 42px;
                }}
                QPushButton:hover {{
                    background-color: {THEME.card_hl};
                    border-color: {THEME.accent};
                }}
            """)
            btn.clicked.connect(lambda checked=False, n=name, d=manifest_data: self._on_quick_add(n, d))
            quick_btn_layout.addWidget(btn)

        quick_btn_layout.addStretch()
        tab_quick_layout.addWidget(quick_label)
        tab_quick_layout.addLayout(quick_btn_layout)
        tabs.addTab(tab_quick, "快速添加")

        # Tab 2: Paste JSON
        tab_json = QWidget()
        tab_json_layout = QVBoxLayout(tab_json)
        tab_json_layout.setContentsMargins(12, 12, 12, 12)
        tab_json_layout.setSpacing(8)

        json_label = QLabel("粘贴 Agent Manifest JSON:")
        json_label.setStyleSheet(f"font-size: 13px; color: {THEME.txt};")

        self._json_editor = QTextEdit()
        self._json_editor.setPlaceholderText('{\n  "name": "my-agent",\n  "version": "1.0.0",\n  ...\n}')
        self._json_editor.setMinimumHeight(250)

        tab_json_layout.addWidget(json_label)
        tab_json_layout.addWidget(self._json_editor)
        tabs.addTab(tab_json, "粘贴 JSON")

        # Tab 3: Upload file
        tab_file = QWidget()
        tab_file_layout = QVBoxLayout(tab_file)
        tab_file_layout.setContentsMargins(12, 12, 12, 12)
        tab_file_layout.setSpacing(8)

        file_label = QLabel("选择 Manifest 文件 (.json 或 .yaml):")
        file_label.setStyleSheet(f"font-size: 13px; color: {THEME.txt};")

        file_btn_layout = QHBoxLayout()
        self._file_path_label = QLabel("未选择文件")
        self._file_path_label.setStyleSheet(f"font-size: 12px; color: {THEME.txt2};")

        self._browse_btn = QPushButton("浏览...")
        self._browse_btn.setObjectName("accent")
        self._browse_btn.setMaximumWidth(100)
        self._browse_btn.clicked.connect(self._browse_file)

        self._selected_file_path: str = ""

        file_btn_layout.addWidget(self._file_path_label, 1)
        file_btn_layout.addWidget(self._browse_btn)

        tab_file_layout.addWidget(file_label)
        tab_file_layout.addLayout(file_btn_layout)
        tab_file_layout.addStretch()
        tabs.addTab(tab_file, "上传文件")

        layout.addWidget(tabs)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self.reject)

        self._confirm_btn = QPushButton("确认添加")
        self._confirm_btn.setObjectName("accent")
        self._confirm_btn.clicked.connect(self._on_confirm)

        btn_layout.addWidget(self._cancel_btn)
        btn_layout.addWidget(self._confirm_btn)
        layout.addLayout(btn_layout)

    def _browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择 Manifest 文件", "",
            "Manifest 文件 (*.json *.yaml *.yml);;所有文件 (*)"
        )
        if file_path:
            self._selected_file_path = file_path
            self._file_path_label.setText(file_path)

    def _on_quick_add(self, name: str, data: dict):
        """Handle quick-add button click by setting manifest and accepting."""
        try:
            self._manifest = AgentManifest.from_dict(data)
            errors = self._manifest.validate()
            if errors:
                QMessageBox.warning(
                    self, "验证错误",
                    "Manifest 验证失败:\n" + "\n".join(f"• {e}" for e in errors)
                )
                return
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "错误", f"添加失败:\n{e}")

    def _on_confirm(self):
        """Validate input and create AgentManifest."""
        tabs = self.findChild(QTabWidget)
        if not tabs:
            return
        current_tab = tabs.currentIndex()

        try:
            if current_tab == 0:
                # Already handled by _on_quick_add buttons, but if user clicks confirm on tab 0
                QMessageBox.warning(self, "提示", "请在快速添加标签页中点击对应的 Agent 按钮")
                return
            elif current_tab == 1:
                # Paste JSON
                raw = self._json_editor.toPlainText().strip()
                if not raw:
                    QMessageBox.warning(self, "提示", "请输入 JSON 内容")
                    return
                data = json.loads(raw)
                self._manifest = AgentManifest.from_dict(data)
            elif current_tab == 2:
                # Upload file
                if not self._selected_file_path:
                    QMessageBox.warning(self, "提示", "请选择一个 manifest 文件")
                    return
                self._manifest = AgentManifest.from_file(self._selected_file_path)
            else:
                return

            # Validate
            errors = self._manifest.validate()
            if errors:
                QMessageBox.warning(
                    self, "验证错误",
                    "Manifest 验证失败:\n" + "\n".join(f"• {e}" for e in errors)
                )
                return

            self.accept()
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, "JSON 解析错误", f"无效的 JSON 格式:\n{e}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"添加失败:\n{e}")

    def get_manifest(self) -> AgentManifest | None:
        return self._manifest


class AgentsPage(QWidget):
    """Agent management page - left list + right detail layout."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._registry = AgentRegistry(workspace=str(resolve_partner_root()))
        self._agents: list[AgentManifest] = []
        self._manifest_map: dict[str, AgentManifest] = {}
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(12)
        # ── Title ──
        title = QLabel("Agent 管理")
        title.setObjectName("title")
        main_layout.addWidget(title)

        # ── Top bar ──
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self._add_btn = QPushButton("+ 添加 Agent")
        self._add_btn.setObjectName("accent")

        # Dropdown menu for add button
        self._add_menu = QMenu(self)
        presets_menu = self._add_menu.addMenu("预置")
        for name in BUILTIN_MANIFESTS:
            preset_action = presets_menu.addAction(name)
            preset_action.triggered.connect(lambda checked=False, n=name: self._on_add_preset(n))
        self._add_menu.addSeparator()
        paste_action = self._add_menu.addAction("粘贴 JSON")
        paste_action.triggered.connect(lambda: self._on_add_dialog(tab=1))
        upload_action = self._add_menu.addAction("上传文件")
        upload_action.triggered.connect(lambda: self._on_add_dialog(tab=2))
        self._add_btn.setMenu(self._add_menu)

        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self._refresh)

        top_bar.addWidget(self._add_btn)
        top_bar.addWidget(self._refresh_btn)
        top_bar.addStretch()

        top_bar.addWidget(QLabel(""))  # placeholder for button alignment

        main_layout.addLayout(top_bar)

        # ── Splitter: left list + right detail ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Agent list
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(4)

        list_label = QLabel("已注册 Agent")
        list_label.setStyleSheet(f"font-size: 13px; color: {THEME.txt2}; font-weight: bold;")

        self._agent_list = QListWidget()
        self._agent_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {THEME.card};
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                outline: none;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 10px 14px;
                border-radius: 6px;
                margin: 2px 0px;
            }}
            QListWidget::item:selected {{
                background-color: {THEME.bg3};
                color: {THEME.accent};
            }}
            QListWidget::item:hover {{
                background-color: {THEME.card_hl};
            }}
        """)
        self._agent_list.currentItemChanged.connect(self._on_agent_selected)

        self._status_label = QLabel("0 个 Agent")
        self._status_label.setStyleSheet(f"font-size: 12px; color: {THEME.txt3};")

        left_layout.addWidget(list_label)
        left_layout.addWidget(self._agent_list, 1)
        left_layout.addWidget(self._status_label)

        # Right: Agent detail panel
        right_panel = QWidget()
        self._detail_panel = AgentDetailPanel()
        self._detail_panel.setVisible(False)

        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(4)

        detail_label = QLabel("Agent 详情")
        detail_label.setStyleSheet(f"font-size: 13px; color: {THEME.txt2}; font-weight: bold;")

        right_layout.addWidget(detail_label)
        right_layout.addWidget(self._detail_panel, 1)

        # Connect deleted signal to refresh
        self._detail_panel.deleted.connect(self._refresh)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([280, 500])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter, 1)

        # Initial load
        self._refresh()

    def _refresh(self):
        """Reload the agent list from registry."""
        self._agent_list.clear()
        self._manifest_map.clear()
        self._detail_panel.setVisible(False)

        try:
            self._agents = self._registry.list_agents()
        except Exception:
            self._agents = []

        if not self._agents:
            self._status_label.setText("0 个 Agent")
            empty_item = QListWidgetItem("暂无注册的 Agent\n点击上方「添加 Agent」")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            empty_item.setForeground(Qt.GlobalColor.gray)
            self._agent_list.addItem(empty_item)
            return

        for manifest in self._agents:
            status_icon = self._get_status_icon(manifest.name)
            display_text = f"{status_icon}  {manifest.name}  v{manifest.version}"
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, manifest.name)
            self._agent_list.addItem(item)
            self._manifest_map[manifest.name] = manifest

        self._status_label.setText(f"{len(self._agents)} 个 Agent")

    def _get_status_icon(self, name: str) -> str:
        """Get status icon for an agent."""
        try:
            result = self._registry.health_check(name)
            status = result.get("status", "unknown")
            icons = {"ok": "✅", "unavailable": "❌", "error": "❌", "timeout": "⚠️", "unknown": "❓"}
            return icons.get(status, "❓")
        except Exception:
            return "❓"

    def _on_agent_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None):
        """Show detail panel when an agent is selected."""
        if current is None:
            self._detail_panel.setVisible(False)
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        if name and name in self._manifest_map:
            self._detail_panel.show_manifest(self._manifest_map[name])

    def _on_add_preset(self, name: str):
        """Quick-add a built-in preset agent."""
        data = BUILTIN_MANIFESTS.get(name)
        if not data:
            return
        try:
            manifest = AgentManifest.from_dict(data)
            errors = manifest.validate()
            if errors:
                QMessageBox.warning(
                    self, "验证错误",
                    "Manifest 验证失败:\n" + "\n".join(f"• {e}" for e in errors)
                )
                return
            self._registry.register_agent(manifest)
            QMessageBox.information(self, "成功", f"Agent \"{manifest.name}\" 已注册")
            self._refresh()
        except Exception as e:
            QMessageBox.warning(self, "错误", f"添加失败: {e}")

    def _on_add_dialog(self, tab: int = 0):
        """Open the Add Agent dialog with a specific tab selected."""
        dialog = AddAgentDialog(self)
        tabs_widget = dialog.findChild(QTabWidget)
        if tabs_widget and 0 <= tab < tabs_widget.count():
            tabs_widget.setCurrentIndex(tab)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            manifest = dialog.get_manifest()
            if manifest:
                try:
                    self._registry.register_agent(manifest)
                    QMessageBox.information(self, "成功", f"Agent \"{manifest.name}\" 已注册")
                    self._refresh()
                except Exception as e:
                    QMessageBox.warning(self, "错误", f"注册失败: {e}")


class AgentDetailPanel(QWidget):
    """Detail panel for a selected agent, shown on the right side."""

    # Signal to notify parent to refresh the list
    deleted = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._manifest: AgentManifest | None = None
        self._registry = AgentRegistry(workspace=str(resolve_partner_root()))
        self._health_status = "unknown"
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {THEME.card};
                border: 1px solid {THEME.border};
                border-radius: 8px;
            }}
        """)

        content = QWidget()
        content.setStyleSheet(f"background-color: {THEME.card};")
        form_layout = QVBoxLayout(content)
        form_layout.setContentsMargins(20, 20, 20, 20)
        form_layout.setSpacing(12)

        # Name + version header
        self._name_label = QLabel("")
        self._name_label.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {THEME.txt}; background: transparent;"
        )
        form_layout.addWidget(self._name_label)

        # Description
        self._desc_label = QLabel("")
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; background: transparent;"
        )
        form_layout.addWidget(self._desc_label)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background-color: {THEME.border}; max-height: 1px; background: transparent;")
        form_layout.addWidget(sep)

        # Form fields using QFormLayout
        fl = QFormLayout()
        fl.setSpacing(8)
        fl.setContentsMargins(0, 8, 0, 8)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._fields: dict[str, QLabel] = {}
        field_defs = [
            ("版本", "version"),
            ("端点类型", "endpoint_type"),
            ("超时时间", "timeout"),
            ("输入格式", "input_formats"),
            ("输出格式", "output_formats"),
            ("能力", "capabilities"),
            ("健康状态", "health_status"),
        ]

        for label, key in field_defs:
            value_label = QLabel("")
            value_label.setWordWrap(True)
            value_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.txt}; background: transparent;"
            )
            fl.addRow(
                QLabel(f"{label}:"),
                value_label
            )
            fl.labelForField(value_label).setStyleSheet(
                f"font-size: 13px; color: {THEME.txt2}; font-weight: bold; background: transparent; min-width: 80px;"
            )
            self._fields[key] = value_label

        form_layout.addLayout(fl)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background-color: {THEME.border}; max-height: 1px; background: transparent;")
        form_layout.addWidget(sep2)

        # Endpoint config details
        ep_label = QLabel("端点配置:")
        ep_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; font-weight: bold; background: transparent;"
        )
        form_layout.addWidget(ep_label)

        self._endpoint_text = QLabel("")
        self._endpoint_text.setWordWrap(True)
        self._endpoint_text.setStyleSheet(
            f"font-size: 12px; color: {THEME.txt}; background: transparent;"
            f"font-family: monospace;"
        )
        form_layout.addWidget(self._endpoint_text)

        form_layout.addStretch()

        # Action buttons at bottom
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self._health_btn = QPushButton("🔄 测试连接")
        self._health_btn.setObjectName("accent")
        self._health_btn.setMaximumWidth(130)
        self._health_btn.clicked.connect(self._on_health_check)

        self._delete_btn = QPushButton("🗑 删除")
        self._delete_btn.setObjectName("danger")
        self._delete_btn.setMaximumWidth(100)
        self._delete_btn.clicked.connect(self._on_delete)

        btn_layout.addStretch()
        btn_layout.addWidget(self._health_btn)
        btn_layout.addWidget(self._delete_btn)

        form_layout.addLayout(btn_layout)

        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    def show_manifest(self, manifest: AgentManifest):
        """Display the given manifest in the detail panel."""
        self._manifest = manifest
        self._name_label.setText(f"{manifest.name}  v{manifest.version}")
        self._desc_label.setText(manifest.description)

        self._fields["version"].setText(manifest.version)
        self._fields["endpoint_type"].setText(manifest.endpoint_type)
        self._fields["timeout"].setText(f"{manifest.timeout}s")

        in_fmts = ", ".join(manifest.input_formats) if manifest.input_formats else "-"
        out_fmts = ", ".join(manifest.output_formats) if manifest.output_formats else "-"
        self._fields["input_formats"].setText(in_fmts)
        self._fields["output_formats"].setText(out_fmts)

        caps = ", ".join(manifest.capabilities) if manifest.capabilities else "-"
        self._fields["capabilities"].setText(caps)

        # Endpoint config
        ep_json = json.dumps(manifest.endpoint_config, indent=2, ensure_ascii=False) if manifest.endpoint_config else "-"
        self._endpoint_text.setText(ep_json)

        # Health status
        self._health_status = "unknown"
        self._fields["health_status"].setText("❓ 未知")
        self._fields["health_status"].setStyleSheet(
            f"font-size: 13px; color: {THEME.txt3}; background: transparent;"
        )

        self.setVisible(True)

    def _on_health_check(self):
        """Run health check for the displayed agent."""
        if not self._manifest:
            return
        self._fields["health_status"].setText("检查中...")
        self._fields["health_status"].setStyleSheet(
            f"font-size: 13px; color: {THEME.yellow}; background: transparent;"
        )
        # Force UI update
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        try:
            result = self._registry.health_check(self._manifest.name)
            status = result.get("status", "unknown")
            details = result.get("details", "")
            status_text = {"ok": "✅ 可用", "unavailable": "❌ 不可用",
                           "error": "❌ 错误", "timeout": "⚠️ 超时",
                           "unknown": "❓ 未知"}.get(status, status)
            self._health_status = status

            if status == "ok":
                color = THEME.green
            elif status == "unavailable":
                color = THEME.yellow
            else:
                color = THEME.red

            display = f"{status_text}"
            if details:
                display += f" — {details}"
            self._fields["health_status"].setText(display)
            self._fields["health_status"].setStyleSheet(
                f"font-size: 13px; color: {color}; background: transparent;"
            )
        except Exception as e:
            self._fields["health_status"].setText(f"❌ 检查失败: {e}")
            self._fields["health_status"].setStyleSheet(
                f"font-size: 13px; color: {THEME.red}; background: transparent;"
            )

    def _on_delete(self):
        """Unregister the displayed agent."""
        if not self._manifest:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除 Agent \"{self._manifest.name}\" 吗？\n"
            f"此操作将从用户注册中移除该 Agent 配置。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            registry = AgentRegistry(workspace=str(resolve_partner_root()))
            success = registry.unregister_agent(self._manifest.name)
            if success:
                QMessageBox.information(self, "成功", f"Agent \"{self._manifest.name}\" 已删除")
                self.setVisible(False)
                # Emit signal for parent to refresh
                self.deleted.emit()
            else:
                QMessageBox.warning(self, "提示", f"无法删除 Agent \"{self._manifest.name}\"（可能为内置 Agent）")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"删除失败: {e}")
