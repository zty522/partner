"""Configuration center - simplified settings page.

Each category has minimal visible fields, with advanced options in
CollapsibleConfigGroup. API purchase links included where applicable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from partner.monitoring.instance_root import (
    resolve_global_config_path,
    resolve_instance_workspace,
    resolve_partner_root,
)

from ..theme import THEME
from ..widgets import SectionHeader, AccentButton
from ..utils.path_mapper import (
    ENVIRONMENT_TYPES,
    ENVIRONMENT_LABELS,
    detect_current_environment,
    format_environment_tag,
    infer_environment_from_path,
    display_path,
)


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


def _make_field_row(label: str, widget: QWidget) -> QWidget:
    """Create a labeled row widget for use in QFormLayout."""
    row = QWidget()
    row.setStyleSheet("background: transparent;")
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 4, 0, 4)
    lbl = QLabel(label)
    lbl.setMinimumWidth(120)
    lbl.setStyleSheet(f"color: {THEME.txt}; background: transparent;")
    rl.addWidget(lbl)
    rl.addWidget(widget, 1)
    return row


class SettingsPage(QWidget):
    """Configuration center with left category list + right form panels.

    Simplified design: each category has minimal visible fields.
    """

    workspace_changed = Signal(str)

    CATEGORIES = [
        ("🖥", "工作区"),
        ("🤖", "Agent"),
        ("🧠", "LLM API"),
        ("🦙", "Ollama"),
        ("🌐", "世界模型"),
        ("🖥", "服务器"),
    ]

    # Built-in general agents for detection UI
    GENERAL_AGENTS = {
        "hermes": {
            "label": "Hermes",
            "desc": "通用 AI Agent，支持多种工具和任务",
            "cmd": "hermes",
            "install_win": "pip install hermes-agent",
            "install_linux": "pip3 install hermes-agent",
        },
        "openclaw": {
            "label": "OpenClaw",
            "desc": "开源命令行 Agent，专注编码与分析",
            "cmd": "openclaw",
            "install_win": "npm install -g openclaw",
            "install_linux": "npm install -g openclaw",
        },
    }

    def __init__(self, parent: QWidget | None = None, workspace_path: str = ""):
        super().__init__(parent)
        self._workspace = workspace_path
        self._config_data: dict = {}
        self._empty_label: QLabel | None = None

        # Instance attrs for each category widget (set by _build_* methods)
        # Workspace
        self._ws_path_edit: QLineEdit | None = None
        self._ws_browse_btn: QPushButton | None = None
        self._default_instance_combo: QComboBox | None = None
        # Agent
        self._agent_install_btns: dict[str, dict[str, QPushButton]] = {}  # {agent_name: {platform: QPushButton}}
        self._agent_status_labels: dict[str, dict[str, QLabel]] = {}      # {agent_name: {platform: QLabel}}
        self._default_agent_combo: QComboBox | None = None
        self._default_platform_combo: QComboBox | None = None
        # LLM
        self._llm_provider_combo: QComboBox | None = None
        self._llm_api_key_edit: QLineEdit | None = None
        self._llm_model_combo: QComboBox | None = None
        self._llm_base_url_edit: QLineEdit | None = None
        # Ollama
        self._ollama_url_edit: QLineEdit | None = None
        self._ollama_model_combo: QComboBox | None = None
        self._ollama_refresh_btn: QPushButton | None = None
        # World Model
        self._wm_enable_cb: QCheckBox | None = None
        self._wm_provider_combo: QComboBox | None = None
        self._wm_endpoint_edit: QLineEdit | None = None
        # Server
        self._server_name_edit: QLineEdit | None = None
        self._server_host_edit: QLineEdit | None = None
        self._server_port_spin: QSpinBox | None = None
        self._server_user_edit: QLineEdit | None = None
        self._server_auth_combo: QComboBox | None = None

        self._build_ui()
        self._load_configs()

    def set_workspace(self, path: str):
        """Update workspace path and reload configs."""
        self._workspace = path
        self._load_configs()

    # ── UI Build ─────────────────────────────────────────────────────────

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        title = QLabel("配置中心")
        title.setObjectName("title")
        main_layout.addWidget(title)

        # Empty state placeholder
        self._empty_label = QLabel("")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color: {THEME.txt3}; font-size: 16px; padding: 60px; background: transparent;"
        )
        self._empty_label.setVisible(False)
        main_layout.addWidget(self._empty_label)

        # Splitter: left categories + right forms
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: category list
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(4)

        cat_label = QLabel("配置分类")
        cat_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; font-weight: bold; background: transparent;"
        )

        self._cat_list = QListWidget()
        self._cat_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {THEME.card};
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                outline: none;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 12px 16px;
                border-radius: 6px;
                margin: 2px 0px;
                font-size: 13px;
            }}
            QListWidget::item:selected {{
                background-color: {THEME.bg3};
                color: {THEME.accent};
            }}
            QListWidget::item:hover {{
                background-color: {THEME.card_hl};
            }}
        """)
        self._cat_list.currentRowChanged.connect(self._on_category_changed)

        for icon, cat_name in self.CATEGORIES:
            item = QListWidgetItem(f"  {icon}  {cat_name}")
            self._cat_list.addItem(item)

        left_layout.addWidget(cat_label)
        left_layout.addWidget(self._cat_list, 1)

        # Right: form panels in a scrollable stacked widget
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(4)

        form_label = QLabel("配置编辑")
        form_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; font-weight: bold; background: transparent;"
        )

        self._form_stack = QStackedWidget()

        # Build each category form widget
        self._form_stack.addWidget(self._build_workspace_page())
        self._form_stack.addWidget(self._build_agent_page())
        self._form_stack.addWidget(self._build_llm_page())
        self._form_stack.addWidget(self._build_ollama_page())
        self._form_stack.addWidget(self._build_wm_page())
        self._form_stack.addWidget(self._build_server_page())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
        """)
        scroll.setWidget(self._form_stack)

        right_layout.addWidget(form_label)
        right_layout.addWidget(scroll, 1)

        # Save button bar
        btn_layout = QHBoxLayout()
        save_btn = AccentButton("保存")
        save_btn.clicked.connect(self._on_save)
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(f"""
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
        cancel_btn.clicked.connect(self._load_configs)
        btn_layout.addStretch()
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        right_layout.addLayout(btn_layout)

        self._splitter.addWidget(left_panel)
        self._splitter.addWidget(right_panel)
        self._splitter.setSizes([200, 600])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)

        main_layout.addWidget(self._splitter, 1)

        # Select first category
        self._cat_list.setCurrentRow(0)

    # ── Helper: wrap a widget in a form page ─────────────────────────────

    def _make_form_page(self, content: QWidget) -> QWidget:
        """Wrap content widget in a page with proper margins."""
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(content)
        layout.addStretch()
        return page

    # ── Category Form Builders ───────────────────────────────────────────

    def _build_workspace_page(self) -> QWidget:
        """Category: 工作区 — workspace path + default instance."""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        form.setSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)

        # Workspace path with browse button
        path_row = QWidget()
        path_row.setStyleSheet("background: transparent;")
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self._ws_path_edit = QLineEdit()
        self._ws_path_edit.setPlaceholderText("/mnt/e/work/partner_workspace")
        self._ws_browse_btn = QPushButton("📂 浏览...")
        self._ws_browse_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                padding: 8px 16px;
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
        self._ws_browse_btn.clicked.connect(self._on_browse_workspace)
        path_layout.addWidget(self._ws_path_edit, 1)
        path_layout.addWidget(self._ws_browse_btn)
        form.addRow("工作区路径:", path_row)

        # Default instance
        self._default_instance_combo = QComboBox()
        self._default_instance_combo.setEditable(True)
        self._default_instance_combo.addItems(["01", "02", "03", "04", "05"])
        self._default_instance_combo.setCurrentText("03")
        form.addRow("默认实例:", self._default_instance_combo)

        layout.addLayout(form)

        return self._make_form_page(container)

    def _build_agent_page(self) -> QWidget:
        """Category: Agent — 通用 Agent (hermes/openclaw) + 专精 Agent + 默认选择."""
        import shutil
        import subprocess

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Section: 通用 Agent ──
        general_header = QLabel("通用 Agent")
        general_header.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {THEME.txt}; "
            f"background: transparent; padding: 4px 0;"
        )
        layout.addWidget(general_header)

        general_desc = QLabel("内置通用 Agent，自动检测安装状态")
        general_desc.setStyleSheet(
            f"font-size: 12px; color: {THEME.txt3}; background: transparent;"
        )
        layout.addWidget(general_desc)

        for agent_key, agent_info in self.GENERAL_AGENTS.items():
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background-color: {THEME.card};
                    border: 1px solid {THEME.border};
                    border-radius: 10px;
                }}
            """)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(6)

            # Header row: name + version
            name_row = QHBoxLayout()
            name_row.setSpacing(8)
            name_icon = QLabel("🤖")
            name_icon.setStyleSheet("font-size: 16px; background: transparent;")
            name_label = QLabel(f"{agent_info['label']}")
            name_label.setStyleSheet(
                f"font-size: 14px; font-weight: bold; color: {THEME.txt}; background: transparent;"
            )
            ver_label = QLabel(f"v2.0.0" if agent_key == "hermes" else "v1.0.0")
            ver_label.setStyleSheet(
                f"font-size: 11px; color: {THEME.txt3}; background: transparent;"
            )
            name_row.addWidget(name_icon)
            name_row.addWidget(name_label)
            name_row.addWidget(ver_label)
            name_row.addStretch()
            card_layout.addLayout(name_row)

            # Description
            desc_label = QLabel(agent_info["desc"])
            desc_label.setStyleSheet(
                f"font-size: 12px; color: {THEME.txt2}; background: transparent;"
            )
            desc_label.setWordWrap(True)
            card_layout.addWidget(desc_label)

            # Platform status rows
            self._agent_status_labels[agent_key] = {}
            self._agent_install_btns[agent_key] = {}
            for platform_key, platform_label in [("windows", "Windows"), ("linux", "Linux")]:
                plat_row = QHBoxLayout()
                plat_row.setSpacing(8)

                plat_dot = QLabel("●")
                plat_dot.setStyleSheet("font-size: 10px; color: #666; background: transparent;")
                plat_name = QLabel(f"{platform_label}:")
                plat_name.setStyleSheet(
                    f"font-size: 12px; color: {THEME.txt2}; background: transparent; min-width: 60px;"
                )
                status_label = QLabel("检测中...")
                status_label.setStyleSheet(
                    f"font-size: 12px; color: {THEME.txt3}; background: transparent;"
                )
                self._agent_status_labels[agent_key][platform_key] = status_label

                install_btn = QPushButton("安装")
                install_btn.setFixedHeight(28)
                install_btn.setFixedWidth(100)
                install_btn.setEnabled(False)
                install_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {THEME.bg3};
                        color: {THEME.txt3};
                        border: 1px solid {THEME.border};
                        border-radius: 6px;
                        padding: 4px 12px;
                        font-size: 11px;
                        font-weight: bold;
                    }}
                    QPushButton:disabled {{
                        background-color: {THEME.bg3};
                        color: {THEME.txt3};
                    }}
                """)
                install_btn.clicked.connect(
                    lambda checked=False, ak=agent_key, pk=platform_key: self._on_install_agent(ak, pk)
                )
                self._agent_install_btns[agent_key][platform_key] = install_btn

                plat_row.addWidget(plat_dot)
                plat_row.addWidget(plat_name)
                plat_row.addWidget(status_label, 1)
                plat_row.addWidget(install_btn)
                plat_row.addStretch()
                card_layout.addLayout(plat_row)

            layout.addWidget(card)

        # ── Section: 专精 Agent ──
        spec_header = QLabel("专精 Agent")
        spec_header.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {THEME.txt}; "
            f"background: transparent; padding: 4px 0; margin-top: 8px;"
        )
        layout.addWidget(spec_header)

        spec_desc = QLabel("其他已注册的专业 Agent（Codex、CytoBridge 等）")
        spec_desc.setStyleSheet(
            f"font-size: 12px; color: {THEME.txt3}; background: transparent;"
        )
        layout.addWidget(spec_desc)

        self._specialist_list = QLabel("(暂无专精 Agent)")
        self._specialist_list.setWordWrap(True)
        self._specialist_list.setStyleSheet(
            f"color: {THEME.txt}; background: transparent; padding: 8px 0;"
        )
        layout.addWidget(self._specialist_list)

        # Add specialist agent button
        add_spec_btn = QPushButton("➕ 添加专精 Agent")
        add_spec_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: bold;
                min-height: 38px;
                max-width: 200px;
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
        add_spec_btn.clicked.connect(self._on_add_agent)
        layout.addWidget(add_spec_btn)

        # ── Section: 默认 Agent ──
        default_header = QLabel("默认 Agent")
        default_header.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {THEME.txt}; "
            f"background: transparent; padding: 4px 0; margin-top: 8px;"
        )
        layout.addWidget(default_header)

        default_form = QWidget()
        default_form.setStyleSheet("background: transparent;")
        default_fl = QFormLayout(default_form)
        default_fl.setSpacing(8)
        default_fl.setContentsMargins(0, 0, 0, 0)

        self._default_agent_combo = QComboBox()
        self._default_agent_combo.addItems(["hermes", "openclaw"])
        self._default_agent_combo.setCurrentText("hermes")
        default_fl.addRow("默认 Agent:", self._default_agent_combo)

        self._default_platform_combo = QComboBox()
        self._default_platform_combo.addItems(["Windows", "Linux"])
        self._default_platform_combo.setCurrentText("Windows")
        default_fl.addRow("运行平台:", self._default_platform_combo)

        layout.addWidget(default_form)

        # ── 重新检测按钮 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        retry_btn = QPushButton("🔄 重新检测")
        retry_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                padding: 8px 18px;
                font-size: 12px;
                font-weight: bold;
                min-height: 36px;
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
        retry_btn.clicked.connect(lambda: self._run_detection())
        btn_row.addWidget(retry_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Detection: run immediately (no QTimer — unreliable in frozen EXE) ──
        self._run_detection()

        return self._make_form_page(container)

    def _run_detection(self):
        """Reset status labels and run detection immediately."""
        import subprocess
        # Reset all status labels to show "检测中..."
        for agent_key in self.GENERAL_AGENTS:
            for platform_key in ("windows", "linux"):
                label = self._agent_status_labels.get(agent_key, {}).get(platform_key)
                if label:
                    label.setText("检测中...")
                    label.setStyleSheet(
                        f"font-size: 12px; color: {THEME.txt3}; background: transparent;"
                    )
        # Force UI update so "检测中..." shows before blocking detection
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        # Run detection
        self._detect_all_agents()

    def _detect_all_agents(self):
        """Run detection for all general agents on Windows and Linux."""
        import shutil
        import subprocess

        for agent_key in self.GENERAL_AGENTS:
            cmd = self.GENERAL_AGENTS[agent_key]["cmd"]
            self._check_agent_on_platform(agent_key, "windows", cmd)
            self._check_agent_on_platform(agent_key, "linux", cmd)

    def _check_agent_on_platform(self, agent_key: str, platform: str, cmd: str):
        """Check if an agent command is available on a given platform and update UI.

        Windows: uses shutil.which() to check PATH.
        Linux: uses WSL 'command -v' and verifies the result is a
               native Linux path (not Windows interop at /mnt/...).
               This ensures WSL's own packages are detected correctly
               rather than Windows npm/pip binaries visible through
               WSL's Windows PATH interop.
        """
        import shutil
        import subprocess

        status_label = self._agent_status_labels.get(agent_key, {}).get(platform)
        install_btn = self._agent_install_btns.get(agent_key, {}).get(platform)
        if not status_label or not install_btn:
            return

        installed = False
        try:
            if platform == "windows":
                # shutil.which() reliably checks Windows PATH
                installed = shutil.which(cmd) is not None
            else:
                # Linux via WSL — use command -v to find the binary path,
                # then verify it's a native Linux path (not /mnt/... interop)
                r = subprocess.run(
                    ["wsl", "bash", "-lc", f"command -v {cmd}"],
                    capture_output=True, text=True, timeout=15,
                )
                if r.returncode == 0:
                    path = r.stdout.strip()
                    # Only count as Linux-installed if the binary lives
                    # in the native Linux filesystem, NOT in Windows PATH
                    # interop (which appears as /mnt/c/... etc.)
                    if path and not path.startswith("/mnt/"):
                        installed = True
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            installed = False

        if installed:
            status_label.setText("✅ 已安装")
            status_label.setStyleSheet(
                f"font-size: 12px; color: {THEME.green}; background: transparent;"
            )
            install_btn.setEnabled(False)
            install_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {THEME.bg3};
                    color: {THEME.txt3};
                    border: 1px solid {THEME.border};
                    border-radius: 6px;
                    padding: 4px 12px;
                    font-size: 11px;
                    font-weight: bold;
                }}
                QPushButton:disabled {{
                    background-color: {THEME.bg3};
                    color: {THEME.txt3};
                }}
            """)
        else:
            status_label.setText("❌ 未安装")
            status_label.setStyleSheet(
                f"font-size: 12px; color: {THEME.red}; background: transparent;"
            )
            install_btn.setEnabled(True)
            install_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 4px 12px;
                    font-size: 11px;
                    font-weight: bold;
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

    def _on_install_agent(self, agent_key: str, platform: str):
        """Install an agent on the specified platform."""
        import subprocess
        from PySide6.QtWidgets import QMessageBox

        agent_info = self.GENERAL_AGENTS.get(agent_key)
        if not agent_info:
            return

        install_cmd = agent_info["install_win"] if platform == "windows" else agent_info["install_linux"]

        if platform == "linux":
            # Run via WSL
            full_cmd = f'wsl bash -c "{install_cmd}"'
        else:
            full_cmd = install_cmd

        reply = QMessageBox.question(
            self, "安装 Agent",
            f"将在 {platform.title()} 上安装 {agent_info['label']}:\n\n"
            f"命令: {full_cmd}\n\n"
            f"确认执行？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Update status
        status_label = self._agent_status_labels.get(agent_key, {}).get(platform)
        if status_label:
            status_label.setText("⏳ 安装中...")
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.processEvents()

        try:
            result = subprocess.run(
                full_cmd if platform == "linux" else install_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                QMessageBox.information(
                    self, "安装成功",
                    f"{agent_info['label']} 已成功安装到 {platform.title()}!"
                )
                # Re-detect
                self._detect_all_agents()
            else:
                error_msg = result.stderr.strip() or f"退出码 {result.returncode}"
                QMessageBox.warning(
                    self, "安装失败",
                    f"{agent_info['label']} 安装失败:\n{error_msg}"
                )
                if status_label:
                    status_label.setText("❌ 安装失败")
        except subprocess.TimeoutExpired:
            QMessageBox.warning(self, "安装超时", "安装命令执行超时（>120秒）")
            if status_label:
                status_label.setText("❌ 超时")
        except Exception as e:
            QMessageBox.warning(self, "安装错误", f"执行安装时出错:\n{e}")
            if status_label:
                status_label.setText("❌ 错误")

    def _build_llm_page(self) -> QWidget:
        """Category: LLM API — provider, key, model, base URL, purchase link, advanced."""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        form.setSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)

        # Provider
        self._llm_provider_combo = QComboBox()
        self._llm_provider_combo.addItems(["DeepSeek", "OpenAI", "自定义"])
        self._llm_provider_combo.currentTextChanged.connect(self._on_llm_provider_changed)
        form.addRow("Provider:", self._llm_provider_combo)

        # API Key (password mode)
        self._llm_api_key_edit = QLineEdit()
        self._llm_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._llm_api_key_edit.setPlaceholderText("sk-...")
        form.addRow("API Key:", self._llm_api_key_edit)

        # Default model
        self._llm_model_combo = QComboBox()
        self._llm_model_combo.setEditable(True)
        self._llm_model_combo.addItems([
            "deepseek-chat", "deepseek-reasoner",
            "gpt-4o", "gpt-4o-mini",
        ])
        self._llm_model_combo.setCurrentText("deepseek-chat")
        form.addRow("默认模型:", self._llm_model_combo)

        # Base URL
        self._llm_base_url_edit = QLineEdit()
        self._llm_base_url_edit.setPlaceholderText("https://api.deepseek.com/v1")
        self._llm_base_url_edit.setText("https://api.deepseek.com/v1")
        form.addRow("Base URL:", self._llm_base_url_edit)

        # Purchase link button
        purchase_btn = QPushButton("🔗 获取 API Key →")
        purchase_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                color: white;
                border: none;
                border-radius: 10px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: bold;
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
        purchase_btn.clicked.connect(self._on_open_api_purchase)
        form.addRow("", purchase_btn)

        layout.addLayout(form)

        return self._make_form_page(container)

    def _build_ollama_page(self) -> QWidget:
        """Category: Ollama — service URL, model combo, refresh, advanced."""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        form.setSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)

        # Service URL
        self._ollama_url_edit = QLineEdit()
        self._ollama_url_edit.setPlaceholderText("http://localhost:11434")
        self._ollama_url_edit.setText("http://localhost:11434")
        form.addRow("服务 URL:", self._ollama_url_edit)

        # Available models
        self._ollama_model_combo = QComboBox()
        self._ollama_model_combo.setEditable(True)
        self._ollama_model_combo.setPlaceholderText("(点击刷新检测模型)")
        form.addRow("可用模型:", self._ollama_model_combo)

        # Refresh button
        self._ollama_refresh_btn = QPushButton("🔄 刷新模型")
        self._ollama_refresh_btn.setStyleSheet(f"""
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
        self._ollama_refresh_btn.clicked.connect(self._on_refresh_ollama_models)
        form.addRow("", self._ollama_refresh_btn)

        layout.addLayout(form)

        return self._make_form_page(container)

    def _build_wm_page(self) -> QWidget:
        """Category: 世界模型 — enable, provider, endpoint, timeout."""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        form.setSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)

        # Enable
        self._wm_enable_cb = QCheckBox("启用世界模型")
        form.addRow("", self._wm_enable_cb)

        # Provider
        self._wm_provider_combo = QComboBox()
        self._wm_provider_combo.addItems(["AETHER", "MCP-Cosmos"])
        form.addRow("Provider:", self._wm_provider_combo)

        # Endpoint URL
        self._wm_endpoint_edit = QLineEdit()
        self._wm_endpoint_edit.setPlaceholderText("http://localhost:8100")
        self._wm_endpoint_edit.setText("http://localhost:8100")
        form.addRow("端点 URL:", self._wm_endpoint_edit)

        # Timeout
        self._wm_timeout_spin = QSpinBox()
        self._wm_timeout_spin.setRange(1, 9999)
        self._wm_timeout_spin.setValue(60)
        self._wm_timeout_spin.setSuffix(" 秒")
        form.addRow("超时(秒):", self._wm_timeout_spin)

        layout.addLayout(form)

        return self._make_form_page(container)

    def _build_server_page(self) -> QWidget:
        """Category: 服务器 (远程) — name, host, port, user, auth method."""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        form.setSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)

        # Name
        self._server_name_edit = QLineEdit()
        self._server_name_edit.setPlaceholderText("my-server")
        form.addRow("名称:", self._server_name_edit)

        # Host
        self._server_host_edit = QLineEdit()
        self._server_host_edit.setPlaceholderText("192.168.1.100")
        form.addRow("主机:", self._server_host_edit)

        # Port
        self._server_port_spin = QSpinBox()
        self._server_port_spin.setRange(1, 65535)
        self._server_port_spin.setValue(22)
        form.addRow("端口:", self._server_port_spin)

        # Username
        self._server_user_edit = QLineEdit()
        self._server_user_edit.setPlaceholderText("ubuntu")
        self._server_user_edit.setText("ubuntu")
        form.addRow("用户名:", self._server_user_edit)

        # Auth method
        self._server_auth_combo = QComboBox()
        self._server_auth_combo.addItems(["密码", "私钥"])
        form.addRow("认证方式:", self._server_auth_combo)

        layout.addLayout(form)

        return self._make_form_page(container)

    # ── Category switching ───────────────────────────────────────────────

    def _on_category_changed(self, index: int):
        """Switch form when category selection changes."""
        if 0 <= index < self._form_stack.count():
            self._form_stack.setCurrentIndex(index)

    # ── Config loading ───────────────────────────────────────────────────

    def _load_configs(self):
        """Load all configs from disk into the UI fields."""
        if not self._workspace or not os.path.exists(self._workspace):
            self._show_empty_state("工作区未配置或路径不存在")
            return

        if self._empty_label:
            self._empty_label.setVisible(False)
            self._splitter.setVisible(True)

        config_dir = os.path.join(self._workspace, "config")
        os.makedirs(config_dir, exist_ok=True)

        global_cfg = _load_json(os.path.join(config_dir, "global_config.json"))
        partner_cfg = _load_json(os.path.join(config_dir, "partner_config.json"))

        # ── Workspace ──
        ws_info = partner_cfg.get("workspace", {})
        ws_path = ws_info.get("path", self._workspace)
        if self._ws_path_edit:
            self._ws_path_edit.setText(ws_path)

        default_inst = global_cfg.get("default_instance", "03")
        if self._default_instance_combo:
            idx = self._default_instance_combo.findText(str(default_inst))
            if idx >= 0:
                self._default_instance_combo.setCurrentIndex(idx)
            else:
                self._default_instance_combo.setCurrentText(str(default_inst))

        # ── Agent ──
        agent_cfg = partner_cfg.get("agent", {})
        # Specialised agents list
        registered_agents = agent_cfg.get("registered", [])
        general_names = set(self.GENERAL_AGENTS.keys())
        specialist_agents = [a for a in registered_agents if a not in general_names]
        if self._specialist_list:
            if specialist_agents:
                lines = "\n".join(f"• {a}" for a in specialist_agents)
                self._specialist_list.setText(lines)
            else:
                self._specialist_list.setText("(暂无专精 Agent)")

        # Default agent + platform
        if self._default_agent_combo:
            default_agent = agent_cfg.get("default_agent", "hermes")
            idx = self._default_agent_combo.findText(default_agent)
            if idx >= 0:
                self._default_agent_combo.setCurrentIndex(idx)
            else:
                self._default_agent_combo.setCurrentText(default_agent)
        if self._default_platform_combo:
            default_platform = agent_cfg.get("default_platform", "Windows")
            idx = self._default_platform_combo.findText(default_platform)
            if idx >= 0:
                self._default_platform_combo.setCurrentIndex(idx)
            else:
                self._default_platform_combo.setCurrentText(default_platform)

        # ── LLM API ──
        llm_cfg = partner_cfg.get("llm", {})
        provider_map = {"deepseek": "DeepSeek", "openai": "OpenAI", "custom": "自定义"}
        llm_provider = llm_cfg.get("provider", "deepseek")
        mapped = provider_map.get(llm_provider.lower(), "DeepSeek")
        if self._llm_provider_combo:
            idx = self._llm_provider_combo.findText(mapped)
            if idx >= 0:
                self._llm_provider_combo.setCurrentIndex(idx)
        if self._llm_api_key_edit:
            self._llm_api_key_edit.setText(llm_cfg.get("api_key", ""))
        if self._llm_model_combo:
            model_val = llm_cfg.get("model", "deepseek-chat")
            idx = self._llm_model_combo.findText(model_val)
            if idx >= 0:
                self._llm_model_combo.setCurrentIndex(idx)
            else:
                self._llm_model_combo.setCurrentText(model_val)
        if self._llm_base_url_edit:
            self._llm_base_url_edit.setText(
                llm_cfg.get("base_url", "https://api.deepseek.com/v1")
            )

        # ── Ollama ──
        ollama_cfg = partner_cfg.get("ollama", {})
        if self._ollama_url_edit:
            self._ollama_url_edit.setText(
                ollama_cfg.get("base_url", "http://localhost:11434")
            )
        if self._ollama_model_combo:
            model_name = ollama_cfg.get("model", "")
            if model_name:
                idx = self._ollama_model_combo.findText(model_name)
                if idx >= 0:
                    self._ollama_model_combo.setCurrentIndex(idx)
                else:
                    self._ollama_model_combo.setCurrentText(model_name)

        # ── World Model ──
        wm_cfg = partner_cfg.get("world_model", {})
        if self._wm_enable_cb:
            self._wm_enable_cb.setChecked(bool(wm_cfg.get("enabled", False)))
        if self._wm_provider_combo:
            provider = wm_cfg.get("provider", "AETHER")
            idx = self._wm_provider_combo.findText(provider)
            if idx >= 0:
                self._wm_provider_combo.setCurrentIndex(idx)
        if self._wm_endpoint_edit:
            self._wm_endpoint_edit.setText(
                wm_cfg.get("endpoint", "http://localhost:8100")
            )

        # ── Server ──
        server_cfg = global_cfg.get("server", {})
        if self._server_name_edit:
            self._server_name_edit.setText(server_cfg.get("name", ""))
        if self._server_host_edit:
            self._server_host_edit.setText(server_cfg.get("host", ""))
        if self._server_port_spin:
            self._server_port_spin.setValue(int(server_cfg.get("port", 22)))
        if self._server_user_edit:
            self._server_user_edit.setText(server_cfg.get("username", "ubuntu"))
        if self._server_auth_combo:
            auth = server_cfg.get("auth_method", "密码")
            idx = self._server_auth_combo.findText(auth)
            if idx >= 0:
                self._server_auth_combo.setCurrentIndex(idx)

    # ── Save ─────────────────────────────────────────────────────────────

    def _on_save(self):
        """Save all configs to disk."""
        if not self._workspace or not os.path.exists(self._workspace):
            QMessageBox.warning(self, "保存失败", "工作区路径无效")
            return

        config_dir = os.path.join(self._workspace, "config")
        os.makedirs(config_dir, exist_ok=True)

        # Load existing configs to preserve unknown keys
        global_cfg = _load_json(os.path.join(config_dir, "global_config.json"))
        partner_cfg = _load_json(os.path.join(config_dir, "partner_config.json"))

        # ── Workspace ──
        old_workspace = self._workspace
        partner_cfg["workspace"] = partner_cfg.get("workspace", {})
        if self._ws_path_edit:
            partner_cfg["workspace"]["path"] = self._ws_path_edit.text().strip()

        if self._default_instance_combo:
            global_cfg["default_instance"] = self._default_instance_combo.currentText().strip()

        # ── Agent ──
        agent_cfg = partner_cfg.get("agent", {})
        if self._default_agent_combo:
            agent_cfg["default_agent"] = self._default_agent_combo.currentText().strip()
        if self._default_platform_combo:
            agent_cfg["default_platform"] = self._default_platform_combo.currentText().strip()
        partner_cfg["agent"] = agent_cfg

        # ── LLM API ──
        reverse_map = {"DeepSeek": "deepseek", "OpenAI": "openai", "自定义": "custom"}
        llm_cfg = partner_cfg.get("llm", {})
        if self._llm_provider_combo:
            raw = self._llm_provider_combo.currentText()
            llm_cfg["provider"] = reverse_map.get(raw, "deepseek")
        if self._llm_api_key_edit:
            llm_cfg["api_key"] = self._llm_api_key_edit.text().strip()
        if self._llm_model_combo:
            llm_cfg["model"] = self._llm_model_combo.currentText().strip()
        if self._llm_base_url_edit:
            llm_cfg["base_url"] = self._llm_base_url_edit.text().strip()
        partner_cfg["llm"] = llm_cfg

        # ── Ollama ──
        ollama_cfg = partner_cfg.get("ollama", {})
        if self._ollama_url_edit:
            ollama_cfg["base_url"] = self._ollama_url_edit.text().strip()
        if self._ollama_model_combo:
            ollama_cfg["model"] = self._ollama_model_combo.currentText().strip()
        partner_cfg["ollama"] = ollama_cfg

        # ── World Model ──
        wm_cfg = partner_cfg.get("world_model", {})
        if self._wm_enable_cb:
            wm_cfg["enabled"] = self._wm_enable_cb.isChecked()
        if self._wm_provider_combo:
            wm_cfg["provider"] = self._wm_provider_combo.currentText()
        if self._wm_endpoint_edit:
            wm_cfg["endpoint"] = self._wm_endpoint_edit.text().strip()
        partner_cfg["world_model"] = wm_cfg

        # ── Server ──
        server_cfg = global_cfg.get("server", {})
        if self._server_name_edit:
            server_cfg["name"] = self._server_name_edit.text().strip()
        if self._server_host_edit:
            server_cfg["host"] = self._server_host_edit.text().strip()
        if self._server_port_spin:
            server_cfg["port"] = self._server_port_spin.value()
        if self._server_user_edit:
            server_cfg["username"] = self._server_user_edit.text().strip()
        if self._server_auth_combo:
            server_cfg["auth_method"] = self._server_auth_combo.currentText()
        global_cfg["server"] = server_cfg

        # Write to disk
        from partner.state.config import save_partner_config_data
        _save_json(os.path.join(config_dir, "global_config.json"), global_cfg)
        save_partner_config_data(self._workspace, partner_cfg)

        # Write pointer file so resolve_partner_root() can find workspace
        from partner.state.setup import save_workspace_pointer
        save_workspace_pointer(self._workspace)

        # Detect workspace path change and emit signal
        new_ws = self._ws_path_edit.text().strip() if self._ws_path_edit else ""
        if new_ws and new_ws != old_workspace:
            self._workspace = new_ws
            self.workspace_changed.emit(new_ws)

        QMessageBox.information(self, "保存成功", "配置已保存")

    # ── Event Handlers ───────────────────────────────────────────────────

    def _show_empty_state(self, message: str):
        """Show a centered placeholder message when workspace is not available."""
        if self._empty_label:
            self._empty_label.setText(message)
            self._empty_label.setVisible(True)
            self._splitter.setVisible(False)

    def _on_browse_workspace(self):
        """Open directory browser for workspace path."""
        directory = QFileDialog.getExistingDirectory(
            self, "选择工作区路径", self._ws_path_edit.text() if self._ws_path_edit else ""
        )
        if directory and self._ws_path_edit:
            self._ws_path_edit.setText(directory)

    def _on_add_agent(self):
        """Open file dialog to select an agent manifest file, then register it."""
        from PySide6.QtWidgets import QMessageBox
        try:
            from partner.agents.registry import AgentRegistry
            from partner.monitoring.instance_root import resolve_partner_root
        except ImportError:
            # Fallback: just update the specialist list
            file_path, _ = QFileDialog.getOpenFileName(
                self, "选择 Agent Manifest 文件", "", "Manifest Files (*.json *.yaml *.yml);;All Files (*)"
            )
            if file_path:
                fname = os.path.basename(file_path)
                current = self._specialist_list.text() if self._specialist_list else ""
                if current == "(暂无专精 Agent)":
                    self._specialist_list.setText(f"• {fname}")
                else:
                    self._specialist_list.setText(f"{current}\n• {fname}")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择 Agent Manifest 文件", "", "Manifest Files (*.json *.yaml *.yml);;All Files (*)"
        )
        if not file_path:
            return

        try:
            registry = AgentRegistry(workspace=str(resolve_partner_root()))
            success = registry.register_from_file(file_path)
            if success:
                QMessageBox.information(self, "成功", "Agent 已注册")
                self._load_configs()
            else:
                QMessageBox.warning(self, "注册失败", "无法注册 Agent")
        except Exception as e:
            QMessageBox.warning(self, "注册失败", f"无法注册 Agent: {e}")

    def _on_open_api_purchase(self):
        """Open the API key purchase page in the default browser."""
        provider = self._llm_provider_combo.currentText() if self._llm_provider_combo else "DeepSeek"
        urls = {
            "DeepSeek": "https://platform.deepseek.com/api_keys",
            "OpenAI": "https://platform.openai.com/api-keys",
            "自定义": "https://platform.deepseek.com/api_keys",
        }
        url = urls.get(provider, "https://platform.deepseek.com/api_keys")
        QDesktopServices.openUrl(QUrl(url))

    def _on_refresh_ollama_models(self):
        """Refresh available Ollama models by calling the local API."""
        # Reset combo to show loading
        if self._ollama_model_combo:
            self._ollama_model_combo.clear()
            self._ollama_model_combo.setPlaceholderText("(检测中...)")

        base_url = self._ollama_url_edit.text().strip() if self._ollama_url_edit else "http://localhost:11434"
        api_url = f"{base_url.rstrip('/')}/api/tags"

        try:
            import urllib.request
            import json as _json

            req = urllib.request.Request(api_url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read().decode())

            models = [m["name"] for m in data.get("models", [])]
            if self._ollama_model_combo:
                self._ollama_model_combo.clear()
                if models:
                    self._ollama_model_combo.addItems(models)
                    self._ollama_model_combo.setCurrentIndex(0)
                else:
                    self._ollama_model_combo.setPlaceholderText("(无可用模型)")
        except Exception:
            if self._ollama_model_combo:
                self._ollama_model_combo.clear()
                self._ollama_model_combo.setPlaceholderText("(无法连接 Ollama 服务)")

    def _on_llm_provider_changed(self, provider: str):
        """Update placeholders when provider changes."""
        presets = {
            "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat"),
            "OpenAI": ("https://api.openai.com/v1", "gpt-4o"),
            "自定义": ("https://api.deepseek.com/v1", "deepseek-chat"),
        }
        url, model = presets.get(provider, ("https://api.deepseek.com/v1", "deepseek-chat"))
        if self._llm_base_url_edit and not self._llm_base_url_edit.text().strip():
            self._llm_base_url_edit.setText(url)
        if self._llm_model_combo:
            idx = self._llm_model_combo.findText(model)
            if idx >= 0:
                self._llm_model_combo.setCurrentIndex(idx)
            else:
                self._llm_model_combo.setCurrentText(model)

    def showEvent(self, event):
        """Refresh configs when the tab is shown."""
        super().showEvent(event)
        self._load_configs()
