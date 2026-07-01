"""Configuration center - simplified settings page.

Each category has minimal visible fields, with advanced options in
CollapsibleConfigGroup. API purchase links included where applicable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from PySide6.QtCore import Qt, QUrl, Signal, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
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
from ..widgets import SectionHeader, AccentButton, fix_combo_wheel, COMBO_WHITE_VIEW_STYLE
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
    config_saved = Signal()  # Emitted when any config is saved (including non-workspace changes)

    CATEGORIES = [
        ("🐧", "Linux"),
        ("🤖", "Agent"),
        ("🧠", "LLM API"),
        ("🦙", "Ollama"),
        ("🌐", "世界模型"),
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

    POPULAR_MODELS = [
        ("llama3.3", "Meta Llama 3.3 70B", "6.7B"),
        ("llama3.2", "Meta Llama 3.2 3B", "2.0B"),
        ("llama3.1", "Meta Llama 3.1 8B", "4.7B"),
        ("qwen2.5", "Qwen 2.5 7B", "4.7B"),
        ("qwen2.5-coder", "Qwen 2.5 Coder 7B", "4.7B"),
        ("mistral", "Mistral 7B", "4.1B"),
        ("mixtral", "Mixtral 8x7B", "26B"),
        ("gemma2", "Google Gemma 2 9B", "5.5B"),
        ("deepseek-r1", "DeepSeek R1 7B", "4.7B"),
        ("nomic-embed-text", "Nomic Embed Text", "0.14B"),
    ]

    def __init__(self, parent: QWidget | None = None, workspace_path: str = ""):
        super().__init__(parent)
        self._workspace = workspace_path
        self._config_data: dict = {}
        self._empty_label: QLabel | None = None

        # Instance attrs for each category widget (set by _build_* methods)
        # Agent
        self._agent_install_btns: dict[str, dict[str, QPushButton]] = {}  # {agent_name: {platform: QPushButton}}
        self._agent_status_labels: dict[str, dict[str, QLabel]] = {}      # {agent_name: {platform: QLabel}}
        self._default_agent_combo: QComboBox | None = None
        self._default_platform_combo: QComboBox | None = None
        # LLM
        self._llm_provider_combo: QComboBox | None = None
        self._llm_api_key_edit: QLineEdit | None = None
        self._llm_model_combo: QComboBox | None = None
        self._llm_base_url_combo: QComboBox | None = None
        # Ollama — env-select + detection + model install
        self._ollama_env_combo: QComboBox | None = None
        self._ollama_status_label: QLabel | None = None
        self._ollama_models_label: QLabel | None = None
        self._ollama_url_edit: QLineEdit | None = None  # kept for backward compat, hidden
        self._ollama_models_container: QVBoxLayout | None = None
        self._ollama_refresh_btn: QPushButton | None = None
        # World Model — env-select + detection labels
        self._wm_env_combo: QComboBox | None = None
        self._wm_aether_label: QLabel | None = None
        self._wm_cosmos_label: QLabel | None = None

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
        self._form_stack.addWidget(self._build_linux_page())
        self._form_stack.addWidget(self._build_agent_page())
        self._form_stack.addWidget(self._build_llm_page())
        self._form_stack.addWidget(self._build_ollama_page())
        self._form_stack.addWidget(self._build_wm_page())

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

    def _build_linux_page(self) -> QWidget:
        """Category: Linux — WSL detection, SSH server configuration."""
        import subprocess

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(14)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Title ──
        title = QLabel("Linux 环境配置")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {THEME.txt}; "
            f"background: transparent; padding: 4px 0;"
        )
        layout.addWidget(title)

        desc = QLabel("检测 WSL 发行版和配置 SSH 远程连接")
        desc.setStyleSheet(
            f"font-size: 12px; color: {THEME.txt3}; background: transparent;"
        )
        layout.addWidget(desc)

        # ── WSL Detection Section ──
        wsl_header = QLabel("🐧 WSL 检测")
        wsl_header.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {THEME.txt}; "
            f"background: transparent; padding: 8px 0 4px 0;"
        )
        layout.addWidget(wsl_header)

        detect_btn = QPushButton("🔍 检测 WSL")
        detect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                color: white;
                border: none;
                border-radius: 10px;
                padding: 8px 24px;
                font-size: 13px;
                font-weight: bold;
                min-height: 38px;
                max-width: 160px;
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
        detect_btn.clicked.connect(self._on_detect_wsl)
        layout.addWidget(detect_btn)

        self._wsl_result_label = QLabel("点击按钮检测 WSL 发行版")
        self._wsl_result_label.setWordWrap(True)
        self._wsl_result_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; background: transparent; "
            f"padding: 8px 12px; border: 1px solid {THEME.border}; border-radius: 8px;"
        )
        layout.addWidget(self._wsl_result_label)

        # ── SSH Server Configuration Section ──
        ssh_header = QLabel("🔌 SSH 服务器配置")
        ssh_header.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {THEME.txt}; "
            f"background: transparent; padding: 12px 0 4px 0;"
        )
        layout.addWidget(ssh_header)

        # Add SSH server form
        ssh_form = QWidget()
        ssh_form.setStyleSheet("background: transparent;")
        ssh_form_layout = QFormLayout(ssh_form)
        ssh_form_layout.setSpacing(8)
        ssh_form_layout.setContentsMargins(0, 0, 0, 0)

        self._ssh_name_edit = QLineEdit()
        self._ssh_name_edit.setPlaceholderText("例如: my-server")
        self._ssh_name_edit.setStyleSheet(
            f"padding: 8px 12px; border: 1px solid {THEME.border}; border-radius: 8px; "
            f"background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {THEME.input_bg}, stop:1 {THEME.bg3}); color: {THEME.txt}; "
            f"min-height: 34px;"
        )
        ssh_form_layout.addRow("名称:", self._ssh_name_edit)

        self._ssh_host_edit = QLineEdit()
        self._ssh_host_edit.setPlaceholderText("例如: 192.168.1.100")
        self._ssh_host_edit.setStyleSheet(
            f"padding: 8px 12px; border: 1px solid {THEME.border}; border-radius: 8px; "
            f"background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {THEME.input_bg}, stop:1 {THEME.bg3}); color: {THEME.txt}; "
            f"min-height: 34px;"
        )
        ssh_form_layout.addRow("Host:", self._ssh_host_edit)

        port_row = QHBoxLayout()
        self._ssh_port_edit = QLineEdit()
        self._ssh_port_edit.setPlaceholderText("22")
        self._ssh_port_edit.setStyleSheet(
            f"padding: 8px 12px; border: 1px solid {THEME.border}; border-radius: 8px; "
            f"background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {THEME.input_bg}, stop:1 {THEME.bg3}); color: {THEME.txt}; "
            f"min-height: 34px; max-width: 120px;"
        )
        port_row.addWidget(self._ssh_port_edit)

        self._ssh_user_edit = QLineEdit()
        self._ssh_user_edit.setPlaceholderText("root")
        self._ssh_user_edit.setStyleSheet(
            f"padding: 8px 12px; border: 1px solid {THEME.border}; border-radius: 8px; "
            f"background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {THEME.input_bg}, stop:1 {THEME.bg3}); color: {THEME.txt}; "
            f"min-height: 34px;"
        )
        port_row.addWidget(self._ssh_user_edit, 1)
        ssh_form_layout.addRow("端口 / 用户:", port_row)

        self._ssh_auth_combo = QComboBox()
        self._ssh_auth_combo.addItems(["password", "key", "agent"])
        fix_combo_wheel(self._ssh_auth_combo)
        self._ssh_auth_combo.setStyleSheet(self._ssh_auth_combo.styleSheet() + COMBO_WHITE_VIEW_STYLE)
        ssh_form_layout.addRow("认证方式:", self._ssh_auth_combo)

        layout.addWidget(ssh_form)

        add_ssh_btn = QPushButton("➕ 添加 SSH 服务器")
        add_ssh_btn.setStyleSheet(f"""
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
        """)
        add_ssh_btn.clicked.connect(self._on_add_ssh_server)
        layout.addWidget(add_ssh_btn)

        # ── Configured SSH servers list ──
        ssh_list_header = QLabel("已配置的 SSH 服务器")
        ssh_list_header.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {THEME.txt}; "
            f"background: transparent; padding: 8px 0 4px 0;"
        )
        layout.addWidget(ssh_list_header)

        self._ssh_server_list = QLabel("(无已配置的 SSH 服务器)")
        self._ssh_server_list.setWordWrap(True)
        self._ssh_server_list.setStyleSheet(
            f"font-size: 12px; color: {THEME.txt}; background: transparent; "
            f"padding: 8px 12px; border: 1px solid {THEME.border}; border-radius: 8px;"
        )
        layout.addWidget(self._ssh_server_list)

        layout.addStretch()
        return self._make_form_page(container)

    def _on_detect_wsl(self):
        """Run wsl.exe -l -q to detect installed WSL distros."""
        import subprocess
        self._wsl_result_label.setText("⏳ 检测中...")
        self._wsl_result_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; background: transparent; "
            f"padding: 8px 12px; border: 1px solid {THEME.border}; border-radius: 8px;"
        )
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        # Clear previous distro rows if any
        self._clear_wsl_distro_rows()

        try:
            # Windows CMD outputs UTF-16-LE, try decoding properly
            r = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=15,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            raw = r.stdout
            try:
                text = raw.decode("utf-16-le").strip()
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace").strip()
            distros = [d.strip().replace("\r", "") for d in text.split("\n") if d.strip() and not d.startswith("*")]

            if distros:
                result = "\n".join(f"  ✅ {d}" for d in distros)
                self._wsl_result_label.setText(f"检测到以下 WSL 发行版:\n{result}")
                self._wsl_result_label.setStyleSheet(
                    f"font-size: 13px; color: {THEME.green}; background: transparent; "
                    f"padding: 8px 12px; border: 1px solid {THEME.green}; border-radius: 8px;"
                )
                # Show distro browser rows
                self._show_wsl_distro_rows(distros)
            else:
                self._wsl_result_label.setText("未检测到 WSL 发行版")
                self._wsl_result_label.setStyleSheet(
                    f"font-size: 13px; color: {THEME.red}; background: transparent; "
                    f"padding: 8px 12px; border: 1px solid {THEME.red}; border-radius: 8px;"
                )
        except FileNotFoundError:
            self._wsl_result_label.setText("WSL 未安装或 wsl.exe 不在 PATH 中")
            self._wsl_result_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.red}; background: transparent; "
                f"padding: 8px 12px; border: 1px solid {THEME.red}; border-radius: 8px;"
            )
        except Exception as e:
            self._wsl_result_label.setText(f"检测失败: {e}")
            self._wsl_result_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.red}; background: transparent; "
                f"padding: 8px 12px; border: 1px solid {THEME.red}; border-radius: 8px;"
            )

    def _clear_wsl_distro_rows(self):
        """Remove WSL distro browse rows from the layout."""
        if hasattr(self, '_wsl_distro_widgets'):
            for w in self._wsl_distro_widgets:
                w.setParent(None)
                w.deleteLater()
            self._wsl_distro_widgets = []

    def _show_wsl_distro_rows(self, distros: list[str]):
        """Show browse folder buttons for each WSL distro with agent detection, selectable rows, and set-default button."""
        import subprocess

        if not hasattr(self, '_wsl_distro_widgets'):
            self._wsl_distro_widgets = []
        if not hasattr(self, '_wsl_distro_rows_data'):
            self._wsl_distro_rows_data = {}  # distro -> dict of row widgets
        if not hasattr(self, '_wsl_selected_distro'):
            self._wsl_selected_distro = ""

        # Find the layout — it's the layout of the container in the linux page
        # We insert after the wsl_result_label
        parent_layout = self._wsl_result_label.parent().layout()
        if not parent_layout:
            return

        # Find index of wsl_result_label
        insert_idx = -1
        for i in range(parent_layout.count()):
            item = parent_layout.itemAt(i)
            if item and item.widget() is self._wsl_result_label:
                insert_idx = i + 1
                break

        # Load current default distro from config
        default_distro = ""
        if self._workspace:
            config_path = os.path.join(self._workspace, "config", "global_config.json")
            config = _load_json(config_path)
            default_distro = config.get("wsl", {}).get("default_distro", "")

        self._wsl_distro_rows_data = {}

        for distro in distros:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(8)

            is_default = (distro == default_distro)

            # Default indicator (star)
            default_label = QLabel("★" if is_default else "☆")
            default_label.setToolTip("点击设为默认 WSL 发行版")
            default_label.setStyleSheet(
                f"font-size: 14px; color: {THEME.accent if is_default else THEME.txt3}; "
                f"background: transparent; font-weight: bold;"
            )
            default_label.setCursor(Qt.CursorShape.PointingHandCursor)
            row_layout.addWidget(default_label)

            # Distro label
            distro_label = QLabel(f"  📦 {distro}")
            distro_label.setStyleSheet(
                f"font-size: 12px; font-weight: bold; color: {THEME.txt}; background: transparent;"
            )
            row_layout.addWidget(distro_label)

            # Agent status: Hermes
            hermes_label = QLabel("检测中...")
            hermes_label.setStyleSheet(
                f"font-size: 10px; color: {THEME.txt3}; background: transparent;"
            )
            row_layout.addWidget(hermes_label)

            # Verify distro exists first (prevents false fallback to default distro)
            distro_valid = False
            try:
                verify = subprocess.run(
                    ["wsl.exe", "-d", distro, "bash", "-lc", "echo ok"],
                    capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                distro_valid = (verify.returncode == 0)
            except Exception:
                distro_valid = False

            if distro_valid:
                # Run Hermes detection on this specific distro
                try:
                    rh = subprocess.run(
                        ["wsl.exe", "-d", distro, "bash", "-lc", "command -v hermes 2>/dev/null"],
                        capture_output=True, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    hermes_path = rh.stdout.strip() if rh.returncode == 0 else ""
                    has_hermes = bool(hermes_path)
                    hermes_label.setText("🤖" if has_hermes else "")
                    hermes_label.setToolTip(f"Hermes: {'已安装' if has_hermes else '未安装'}")
                except Exception:
                    hermes_label.setText("")
            else:
                hermes_label.setText("")

            # Agent status: OpenClaw
            openclaw_label = QLabel("检测中..." if distro_valid else "")
            openclaw_label.setStyleSheet(
                f"font-size: 10px; color: {THEME.txt3}; background: transparent;"
            )
            row_layout.addWidget(openclaw_label)

            if distro_valid:
                # Run OpenClaw detection on this specific distro
                try:
                    ro = subprocess.run(
                        ["wsl.exe", "-d", distro, "bash", "-lc", "command -v openclaw 2>/dev/null"],
                        capture_output=True, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    openclaw_path = ro.stdout.strip() if ro.returncode == 0 else ""
                    has_openclaw = bool(openclaw_path)
                    openclaw_label.setText("🦾" if has_openclaw else "")
                    openclaw_label.setToolTip(f"OpenClaw: {'已安装' if has_openclaw else '未安装'}")
                except Exception:
                    openclaw_label.setText("")
            else:
                openclaw_label.setText("")

            # Browse button
            browse_btn = QPushButton("📂 浏览文件夹")
            browse_btn.setFixedHeight(30)
            browse_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                    color: {THEME.txt};
                    border: 1px solid {THEME.border};
                    border-radius: 6px;
                    padding: 4px 14px;
                    font-size: 11px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 {THEME.card_hl}, stop:1 {THEME.bg2});
                    border-color: {THEME.accent};
                    color: {THEME.accent};
                }}
            """)
            browse_btn.clicked.connect(lambda checked=False, d=distro: self._on_browse_wsl_distro(d))
            row_layout.addWidget(browse_btn)

            # Set default button (visible only when selected)
            set_default_btn = QPushButton("★ 设为默认")
            set_default_btn.setVisible(is_default)  # visible if already default
            set_default_btn.setFixedHeight(30)
            set_default_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 4px 14px;
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
            set_default_btn.clicked.connect(lambda checked=False, d=distro: self._on_set_default_wsl(d))
            row_layout.addWidget(set_default_btn)

            # Path label
            path_label = QLabel("")
            path_label.setStyleSheet(
                f"font-size: 11px; color: {THEME.txt3}; background: transparent;"
            )
            row_layout.addWidget(path_label, 1)

            # Default usage label (shown only for default distro)
            default_usage_label = QLabel("默认使用" if is_default else "")
            default_usage_label.setStyleSheet(
                f"font-size: 11px; color: {THEME.green}; background: transparent; font-weight: bold;"
            )
            row_layout.addWidget(default_usage_label)

            # Store row data for later access
            self._wsl_distro_rows_data[distro] = {
                "row": row,
                "default_label": default_label,
                "distro_label": distro_label,
                "hermes_label": hermes_label,
                "openclaw_label": openclaw_label,
                "browse_btn": browse_btn,
                "set_default_btn": set_default_btn,
                "path_label": path_label,
                "default_usage_label": default_usage_label,
                "is_default": is_default,
            }

            # Make entire row clickable
            row.mousePressEvent = lambda e, d=distro: self._on_wsl_distro_selected(d)
            row.setCursor(Qt.CursorShape.PointingHandCursor)

            # Also make the default_label click to select + set default
            def _on_default_label_clicked(e, d=distro):
                self._on_wsl_distro_selected(d)
                self._on_set_default_wsl(d)
            default_label.mousePressEvent = _on_default_label_clicked

            if insert_idx >= 0:
                parent_layout.insertWidget(insert_idx, row)
                insert_idx += 1
            else:
                parent_layout.addWidget(row)
            self._wsl_distro_widgets.append(row)

    def _on_wsl_distro_selected(self, distro: str):
        """Select a WSL distro row and highlight it."""
        if not hasattr(self, '_wsl_distro_rows_data'):
            return

        # Deselect all rows
        for d, data in self._wsl_distro_rows_data.items():
            row = data.get("row")
            if row:
                if d == distro:
                    row.setStyleSheet(
                        f"background-color: rgba(74, 144, 217, 0.08); border-radius: 6px;"
                    )
                else:
                    row.setStyleSheet("background: transparent;")

        # Show set-default button for this distro
        if distro in self._wsl_distro_rows_data:
            data = self._wsl_distro_rows_data[distro]
            btn = data.get("set_default_btn")
            if btn:
                btn.setVisible(True)

        # Hide set-default buttons for all other distros
        for d, data in self._wsl_distro_rows_data.items():
            if d != distro:
                btn = data.get("set_default_btn")
                if btn:
                    btn.setVisible(False)

        self._wsl_selected_distro = distro

    def _on_set_default_wsl(self, distro: str):
        """Set a WSL distro as the default and save to config."""
        if not self._workspace:
            return
        config_path = os.path.join(self._workspace, "config", "global_config.json")
        config = _load_json(config_path)
        wsl_config = config.setdefault("wsl", {})
        wsl_config["default_distro"] = distro
        _save_json(config_path, config)

        # Update all existing rows to reflect the new default
        if hasattr(self, '_wsl_distro_rows_data'):
            for d, data in self._wsl_distro_rows_data.items():
                is_default = (d == distro)
                # Update star label
                default_label = data.get("default_label")
                if default_label:
                    default_label.setText("★" if is_default else "☆")
                    default_label.setStyleSheet(
                        f"font-size: 14px; color: {THEME.accent if is_default else THEME.txt3}; "
                        f"background: transparent; font-weight: bold;"
                    )
                # Update default usage label
                usage_label = data.get("default_usage_label")
                if usage_label:
                    usage_label.setText("默认使用" if is_default else "")
                # Update set-default button visibility
                btn = data.get("set_default_btn")
                if btn:
                    btn.setVisible(False)  # Hide all after setting default

        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, "默认设置", f"已将 {distro} 设为默认 WSL 发行版")

    def _on_browse_wsl_distro(self, distro: str):
        """Open WSL directory browser for a specific distro."""
        from ..widgets import DirBrowser
        dialog = QDialog(self)
        dialog.setWindowTitle(f"选择 {distro} 工作目录")
        dialog.resize(600, 400)
        browser = DirBrowser(env_type="wsl")
        browser.set_distro(distro)
        layout = QVBoxLayout(dialog)
        layout.addWidget(browser)
        browser.path_selected.connect(lambda p: self._on_wsl_distro_path_selected(p, dialog, distro))
        dialog.exec()

    def _on_wsl_distro_path_selected(self, path: str, dialog: QDialog, distro: str):
        """Save the selected WSL distro path."""
        dialog.accept()
        # Save path to global_config or remember it
        if self._workspace:
            config_path = os.path.join(self._workspace, "config", "global_config.json")
            config = _load_json(config_path)
            wsl_config = config.setdefault("wsl", {})
            distro_config = wsl_config.setdefault(distro, {})
            distro_config["workspace_dir"] = path
            _save_json(config_path, config)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "路径已保存",
                f"已将 {distro} 的工作目录设置为:\n{path}"
            )

    def _on_add_ssh_server(self):
        """Add SSH server configuration to global_config.json."""
        name = self._ssh_name_edit.text().strip()
        host = self._ssh_host_edit.text().strip()
        port = self._ssh_port_edit.text().strip() or "22"
        user = self._ssh_user_edit.text().strip() or "root"
        auth = self._ssh_auth_combo.currentText().strip()

        if not name or not host:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "提示", "名称和 Host 为必填项")
            return

        if not self._workspace or not os.path.exists(self._workspace):
            return

        config_path = os.path.join(self._workspace, "config", "global_config.json")
        config = _load_json(config_path)
        servers = config.setdefault("servers", {})

        # Check for duplicate name
        if name in servers:
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "覆盖确认",
                f"SSH 服务器 '{name}' 已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        servers[name] = {
            "host": host,
            "port": int(port),
            "user": user,
            "auth_method": auth,
        }
        _save_json(config_path, config)

        # Clear form
        self._ssh_name_edit.clear()
        self._ssh_host_edit.clear()
        self._ssh_port_edit.setText("22")
        self._ssh_user_edit.setText("root")
        self._ssh_auth_combo.setCurrentIndex(0)

        self._refresh_ssh_server_list()
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, "成功", f"SSH 服务器 '{name}' 已添加")

    def _refresh_ssh_server_list(self):
        """Refresh the configured SSH servers display."""
        if not self._workspace or not os.path.exists(self._workspace):
            return
        config_path = os.path.join(self._workspace, "config", "global_config.json")
        config = _load_json(config_path)
        servers = config.get("servers", {})
        if not servers:
            self._ssh_server_list.setText("(无已配置的 SSH 服务器)")
        else:
            lines = []
            for sname, sinfo in servers.items():
                auth = sinfo.get("auth_method", "password")
                host = sinfo.get("host", "?")
                port = sinfo.get("port", 22)
                user = sinfo.get("user", "root")
                lines.append(f"  🔌 {sname}  —  {user}@{host}:{port}  ({auth})")
            self._ssh_server_list.setText("\n".join(lines))

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
                    border-radius: 10px;
                }}
            """)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 14)
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
        fix_combo_wheel(self._default_agent_combo)
        self._default_agent_combo.setStyleSheet(self._default_agent_combo.styleSheet() + COMBO_WHITE_VIEW_STYLE)
        default_fl.addRow("默认 Agent:", self._default_agent_combo)

        self._default_platform_combo = QComboBox()
        self._default_platform_combo.addItems(["Windows", "Linux"])
        self._default_platform_combo.setCurrentText("Windows")
        fix_combo_wheel(self._default_platform_combo)
        self._default_platform_combo.setStyleSheet(self._default_platform_combo.styleSheet() + COMBO_WHITE_VIEW_STYLE)
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

        # ── Detection: deferred via QTimer so the UI thread is not blocked
        #     by WSL subprocess calls (which can take seconds). ──
        QTimer.singleShot(300, self._run_detection)

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
                    creationflags=subprocess.CREATE_NO_WINDOW,
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
                creationflags=subprocess.CREATE_NO_WINDOW,
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
        self._llm_provider_combo.addItems([
            "DeepSeek", "OpenAI", "Anthropic", "Google Gemini",
            "Groq", "Together AI", "Mistral AI", "xAI",
            "自定义",
        ])
        self._llm_provider_combo.currentTextChanged.connect(self._on_llm_provider_changed)
        fix_combo_wheel(self._llm_provider_combo)
        self._llm_provider_combo.setStyleSheet(self._llm_provider_combo.styleSheet() + COMBO_WHITE_VIEW_STYLE)
        form.addRow("Provider:", self._llm_provider_combo)

        # API Key (password mode)
        self._llm_api_key_edit = QLineEdit()
        self._llm_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._llm_api_key_edit.setPlaceholderText("sk-...")
        self._llm_api_key_edit.mousePressEvent = lambda e: self._on_llm_key_clicked()
        form.addRow("API Key:", self._llm_api_key_edit)

        # Default model
        self._llm_model_combo = QComboBox()
        self._llm_model_combo.setEditable(True)
        self._llm_model_combo.addItems([
            "deepseek-chat", "deepseek-reasoner",
            "gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3", "o4-mini",
            "claude-sonnet-4", "claude-haiku-4", "claude-opus-4",
            "gemini-2.5-pro", "gemini-2.5-flash",
            "mixtral-8x22b", "mistral-large",
            "grok-3", "grok-3-mini",
        ])
        self._llm_model_combo.setCurrentText("deepseek-chat")
        fix_combo_wheel(self._llm_model_combo)
        self._llm_model_combo.setStyleSheet(self._llm_model_combo.styleSheet() + COMBO_WHITE_VIEW_STYLE)
        form.addRow("默认模型:", self._llm_model_combo)

        # Base URL
        self._llm_base_url_combo = QComboBox()
        self._llm_base_url_combo.setEditable(True)
        self._llm_base_url_combo.addItems([
            "https://api.deepseek.com/v1",
            "https://api.openai.com/v1",
            "https://api.anthropic.com/v1",
            "https://generativelanguage.googleapis.com/v1beta",
            "https://api.groq.com/openai/v1",
            "https://api.together.xyz/v1",
            "https://api.mistral.ai/v1",
            "https://api.x.ai/v1",
        ])
        self._llm_base_url_combo.setCurrentText("https://api.deepseek.com/v1")
        fix_combo_wheel(self._llm_base_url_combo)
        self._llm_base_url_combo.setStyleSheet(self._llm_base_url_combo.styleSheet() + COMBO_WHITE_VIEW_STYLE)
        form.addRow("Base URL:", self._llm_base_url_combo)

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
        """Category: Ollama — env-select, detection, model list with install buttons."""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Environment selector ──
        self._ollama_env_combo = QComboBox()
        self._ollama_env_combo.addItems([
            "🐧 WSL Linux",
            "🪟 Windows",
            "☁️ 远程服务器",
        ])
        self._ollama_env_combo.currentIndexChanged.connect(self._on_ollama_env_changed)
        fix_combo_wheel(self._ollama_env_combo)
        self._ollama_env_combo.setStyleSheet(self._ollama_env_combo.styleSheet() + COMBO_WHITE_VIEW_STYLE)
        layout.addWidget(self._ollama_env_combo)

        # ── Detection status row (label + refresh button) ──
        status_row = QWidget()
        status_row.setStyleSheet("background: transparent;")
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)

        self._ollama_status_label = QLabel("选择环境后自动检测...")
        self._ollama_status_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; background: transparent; padding: 4px 0;"
        )
        self._ollama_status_label.setWordWrap(True)
        status_layout.addWidget(self._ollama_status_label, 1)

        self._ollama_refresh_btn = QPushButton("🔄 刷新检测")
        self._ollama_refresh_btn.setFixedHeight(28)
        self._ollama_refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 6px;
                padding: 2px 12px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.card_hl}, stop:1 {THEME.bg2});
                border-color: {THEME.accent};
                color: {THEME.accent};
            }}
        """)
        self._ollama_refresh_btn.clicked.connect(
            lambda: self._detect_ollama(self._ollama_env_combo.currentText() if self._ollama_env_combo else "")
        )
        status_layout.addWidget(self._ollama_refresh_btn)

        layout.addWidget(status_row)

        # ── Available models summary ──
        self._ollama_models_label = QLabel("")
        self._ollama_models_label.setStyleSheet(
            f"font-size: 12px; color: {THEME.txt3}; background: transparent; padding: 4px 0;"
        )
        self._ollama_models_label.setWordWrap(True)
        layout.addWidget(self._ollama_models_label)

        # ── Model cards container (populated by _detect_ollama) ──
        models_widget = QWidget()
        models_widget.setStyleSheet("background: transparent;")
        self._ollama_models_container = QVBoxLayout(models_widget)
        self._ollama_models_container.setSpacing(6)
        self._ollama_models_container.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(models_widget, 1)

        # Hidden URL edit for backward compat
        self._ollama_url_edit = QLineEdit()
        self._ollama_url_edit.setVisible(False)
        self._ollama_url_edit.setText("http://localhost:11434")
        layout.addWidget(self._ollama_url_edit)

        # Run initial detection via timer
        QTimer.singleShot(500, lambda: self._detect_ollama(self._ollama_env_combo.currentText()))

        return self._make_form_page(container)

    def _build_wm_page(self) -> QWidget:
        """Category: 世界模型 — env-select + backend detection."""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Environment selector ──
        self._wm_env_combo = QComboBox()
        self._wm_env_combo.addItems([
            "🐧 WSL Linux",
            "🪟 Windows",
            "☁️ 远程服务器",
        ])
        self._wm_env_combo.currentIndexChanged.connect(self._on_wm_env_changed)
        fix_combo_wheel(self._wm_env_combo)
        self._wm_env_combo.setStyleSheet(self._wm_env_combo.styleSheet() + COMBO_WHITE_VIEW_STYLE)
        layout.addWidget(self._wm_env_combo)

        # ── Detection labels ──
        self._wm_aether_label = QLabel("AETHER 后端: ⏳ 检测中...")
        self._wm_aether_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; background: transparent; padding: 4px 0;"
        )
        layout.addWidget(self._wm_aether_label)

        self._wm_cosmos_label = QLabel("MCP-Cosmos 后端: ⏳ 检测中...")
        self._wm_cosmos_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; background: transparent; padding: 4px 0;"
        )
        layout.addWidget(self._wm_cosmos_label)

        # Run initial detection via timer
        QTimer.singleShot(500, lambda: self._detect_wm_backends(self._wm_env_combo.currentText()))

        return self._make_form_page(container)

    # ── Category switching ───────────────────────────────────────────────

    def _on_category_changed(self, index: int):
        """Switch form when category selection changes."""
        if 0 <= index < self._form_stack.count():
            self._form_stack.setCurrentIndex(index)

    # ── Config loading ───────────────────────────────────────────────────

    def _load_configs(self):
        """Load all configs from disk into the UI fields."""
        # Always try to load LLM config from Hermes (even without workspace)
        self._load_llm_from_hermes()

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

        # ── Ollama ──
        # No direct loading needed — detection runs on env change
        if self._ollama_url_edit:
            self._ollama_url_edit.setText("http://localhost:11434")

        # Trigger Ollama detection on initial load (currentIndexChanged
        # does NOT fire for the default selection at index 0)
        if hasattr(self, '_ollama_env_combo') and self._ollama_env_combo:
            env = self._ollama_env_combo.currentText()
            if env:
                self._detect_ollama(env)

        # ── World Model ──
        # Detection runs on env change — no static fields to load

        # ── SSH Servers ──
        if hasattr(self, '_refresh_ssh_server_list'):
            self._refresh_ssh_server_list()

    # ── Save ─────────────────────────────────────────────────────────────

    def _load_llm_from_hermes(self):
        """Load LLM config from Hermes config.yaml and .env into the UI fields.
        Called unconditionally (even without workspace) so LLM fields are
        always populated on first load.
        """
        hermes_provider = "deepseek"
        hermes_model = "deepseek-chat"
        hermes_base_url = "https://api.deepseek.com/v1"
        hermes_api_key = ""

        # Read from Hermes config.yaml (try Windows path first, then WSL)
        hermes_cfg_path = os.path.expanduser("~/.hermes/config.yaml")
        cfg_yaml_str = None
        if os.path.exists(hermes_cfg_path):
            try:
                with open(hermes_cfg_path) as _fh:
                    cfg_yaml_str = _fh.read()
            except Exception:
                pass
        if not cfg_yaml_str:
            # Try reading from WSL via wsl.exe
            try:
                import subprocess as _sp
                r = _sp.run(
                    ["wsl.exe", "bash", "-lc", "cat /home/os/.hermes/config.yaml 2>/dev/null"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=_sp.CREATE_NO_WINDOW if hasattr(_sp, 'CREATE_NO_WINDOW') else 0,
                )
                if r.returncode == 0 and r.stdout.strip():
                    cfg_yaml_str = r.stdout
            except Exception:
                pass
        if cfg_yaml_str:
            try:
                _cfg = yaml.safe_load(cfg_yaml_str) or {}
                _cfg_model = _cfg.get("model", {}) or {}
                hermes_provider = _cfg_model.get("provider", "deepseek")
                hermes_model = _cfg_model.get("default", "deepseek-chat")
                hermes_base_url = _cfg_model.get("base_url", "https://api.deepseek.com")
            except Exception:
                pass

        # Read API key from .env (try Windows path first, then WSL)
        hermes_env_path = os.path.expanduser("~/.hermes/.env")
        env_text = None
        if os.path.exists(hermes_env_path):
            try:
                with open(hermes_env_path) as _fh:
                    env_text = _fh.read()
            except Exception:
                pass
        if not env_text:
            try:
                import subprocess as _sp
                r = _sp.run(
                    ["wsl.exe", "bash", "-lc", "cat /home/os/.hermes/.env 2>/dev/null"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=_sp.CREATE_NO_WINDOW if hasattr(_sp, 'CREATE_NO_WINDOW') else 0,
                )
                if r.returncode == 0 and r.stdout.strip():
                    env_text = r.stdout
            except Exception:
                pass
        if env_text:
            try:
                for _line in env_text.split("\n"):
                    _line = _line.strip()
                    if "=" in _line and not _line.startswith("#"):
                        _k, _v = _line.split("=", 1)
                        if _k.strip() in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                                           "OPENROUTER_API_KEY"):
                            hermes_api_key = _v.strip().strip("'\"")
                            break
            except Exception:
                pass

        # Populate UI fields
        provider_display = {"deepseek": "DeepSeek", "openai": "OpenAI", "anthropic": "Anthropic",
                            "google": "Google Gemini", "groq": "Groq", "together": "Together AI",
                            "mistral": "Mistral AI", "xai": "xAI", "custom": "自定义"}
        display = provider_display.get(hermes_provider.lower(), "DeepSeek")
        if self._llm_provider_combo:
            idx = self._llm_provider_combo.findText(display)
            if idx >= 0:
                self._llm_provider_combo.setCurrentIndex(idx)
            else:
                self._llm_provider_combo.setCurrentText(display)

        # API key with mask
        key = hermes_api_key
        if key and len(key) > 8 and self._llm_api_key_edit:
            masked = key[:4] + "*" * 8 + key[-4:]
            self._llm_api_key_edit.setText(masked)
            self._llm_api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        elif self._llm_api_key_edit:
            self._llm_api_key_edit.setText(key)
            if key:
                self._llm_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)

        # Model
        model = hermes_model
        if self._llm_model_combo:
            idx = self._llm_model_combo.findText(model)
            if idx >= 0:
                self._llm_model_combo.setCurrentIndex(idx)
            else:
                self._llm_model_combo.setCurrentText(model)

        # Base URL
        base_url = hermes_base_url
        if self._llm_base_url_combo:
            idx = self._llm_base_url_combo.findText(base_url)
            if idx >= 0:
                self._llm_base_url_combo.setCurrentIndex(idx)
            else:
                self._llm_base_url_combo.setCurrentText(base_url)

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

        # ── Agent ──
        agent_cfg = partner_cfg.get("agent", {})
        if self._default_agent_combo:
            agent_cfg["default_agent"] = self._default_agent_combo.currentText().strip()
        if self._default_platform_combo:
            agent_cfg["default_platform"] = self._default_platform_combo.currentText().strip()
        partner_cfg["agent"] = agent_cfg

        # ── LLM API ──
        reverse_map = {"DeepSeek": "deepseek", "OpenAI": "openai", "Anthropic": "anthropic",
                       "Google Gemini": "google", "Groq": "groq", "Together AI": "together",
                       "Mistral AI": "mistral", "xAI": "xai", "自定义": "custom"}
        llm_cfg = partner_cfg.get("llm", {})
        if self._llm_provider_combo:
            raw = self._llm_provider_combo.currentText()
            llm_cfg["provider"] = reverse_map.get(raw, "deepseek")
        if self._llm_api_key_edit:
            current_text = self._llm_api_key_edit.text().strip()
            # Don't overwrite if the text contains masked placeholder
            if "****" not in current_text:
                llm_cfg["api_key"] = current_text
        if self._llm_model_combo:
            llm_cfg["model"] = self._llm_model_combo.currentText().strip()
        if self._llm_base_url_combo:
            llm_cfg["base_url"] = self._llm_base_url_combo.currentText().strip()
        partner_cfg["llm"] = llm_cfg

        # ── Ollama ──
        # No model/url saving needed in new env-detection mode; keep URL for compat
        if self._ollama_url_edit:
            ollama_cfg = partner_cfg.get("ollama", {})
            ollama_cfg["base_url"] = self._ollama_url_edit.text().strip()
            partner_cfg["ollama"] = ollama_cfg

        # ── World Model ──
        # No fields to save in detection-only mode

        # Write to disk
        from partner.state.config import save_partner_config_data
        _save_json(os.path.join(config_dir, "global_config.json"), global_cfg)
        save_partner_config_data(self._workspace, partner_cfg)

        # Write pointer file so resolve_partner_root() can find workspace
        from partner.state.setup import save_workspace_pointer
        save_workspace_pointer(self._workspace)

        QMessageBox.information(self, "保存成功", "配置已保存")
        self.config_saved.emit()

    # ── Event Handlers ───────────────────────────────────────────────────

    def _show_empty_state(self, message: str):
        """Show a centered placeholder message when workspace is not available."""
        if self._empty_label:
            self._empty_label.setText(message)
            self._empty_label.setVisible(True)
            self._splitter.setVisible(False)

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
            "Anthropic": "https://console.anthropic.com/settings/keys",
            "Google Gemini": "https://aistudio.google.com/apikey",
            "Groq": "https://console.groq.com/keys",
            "Together AI": "https://api.together.xyz/settings/api-keys",
            "Mistral AI": "https://console.mistral.ai/api-keys",
            "xAI": "https://console.x.ai/api-keys",
            "自定义": "https://platform.deepseek.com/api_keys",
        }
        url = urls.get(provider, "https://platform.deepseek.com/api_keys")
        QDesktopServices.openUrl(QUrl(url))

    def _on_ollama_env_changed(self, index: int):
        """Handle Ollama environment change — run detection."""
        env_text = self._ollama_env_combo.currentText() if self._ollama_env_combo else ""
        self._detect_ollama(env_text)

    def _detect_ollama(self, env_text: str):
        """Detect Ollama installation and show model list with install buttons."""
        import subprocess
        import shutil

        if not self._ollama_status_label or not self._ollama_models_label:
            return

        self._ollama_status_label.setText("⏳ 检测中...")
        self._ollama_status_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; background: transparent; padding: 4px 0;"
        )
        self._ollama_models_label.setText("")
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        # Clear existing model cards
        if self._ollama_models_container:
            self._clear_layout(self._ollama_models_container)

        installed = False
        installed_models = []

        try:
            if env_text == "🐧 WSL Linux":
                r = subprocess.run(
                    ["wsl", "bash", "-lc", "command -v ollama 2>/dev/null"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if r.returncode == 0:
                    path = r.stdout.strip()
                    installed = bool(path and not path.startswith("/mnt/"))
                    if installed:
                        r2 = subprocess.run(
                            ["wsl", "bash", "-lc", "ollama list 2>/dev/null"],
                            capture_output=True, text=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        if r2.returncode == 0:
                            lines = r2.stdout.strip().split("\n")
                            for line in lines[1:]:
                                parts = line.split()
                                if parts:
                                    installed_models.append(parts[0])
            elif env_text == "🪟 Windows":
                installed = shutil.which("ollama") is not None
                if installed:
                    r = subprocess.run(
                        ["ollama", "list"],
                        capture_output=True, text=True, timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    if r.returncode == 0:
                        lines = r.stdout.strip().split("\n")
                        for line in lines[1:]:
                            parts = line.split()
                            if parts:
                                installed_models.append(parts[0])
            else:  # ☁️ 远程服务器
                self._ollama_status_label.setText("需连接后检测")
                self._ollama_status_label.setStyleSheet(
                    f"font-size: 13px; color: {THEME.txt3}; background: transparent; padding: 4px 0;"
                )
                return
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            installed = False

        installed_set = set(installed_models)

        if installed:
            self._ollama_status_label.setText("✅ Ollama 已安装")
            self._ollama_status_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.green}; background: transparent; padding: 4px 0;"
            )
            if installed_models:
                self._ollama_models_label.setText(f"已安装模型: {', '.join(installed_models)}")
                self._ollama_models_label.setStyleSheet(
                    f"font-size: 12px; color: {THEME.txt}; background: transparent; padding: 4px 0;"
                )
            else:
                self._ollama_models_label.setText("可用模型: (无已安装模型)")
                self._ollama_models_label.setStyleSheet(
                    f"font-size: 12px; color: {THEME.txt3}; background: transparent; padding: 4px 0;"
                )

            # Build model cards
            if self._ollama_models_container:
                section_title = QLabel("热门模型")
                section_title.setStyleSheet(
                    f"font-size: 13px; font-weight: bold; color: {THEME.txt}; background: transparent; padding: 8px 0 4px 0;"
                )
                self._ollama_models_container.addWidget(section_title)

                for model_name, description, size in self.POPULAR_MODELS:
                    model_row = self._build_ollama_model_card(model_name, description, size, installed_set, env_text)
                    self._ollama_models_container.addWidget(model_row)

                self._ollama_models_container.addStretch()
        else:
            self._ollama_status_label.setText("❌ Ollama 未安装")
            self._ollama_status_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.red}; background: transparent; padding: 4px 0;"
            )
            # Show install button
            install_ollama_btn = QPushButton("📥 安装 Ollama")
            install_ollama_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                    color: white; border: none; border-radius: 8px;
                    padding: 8px 20px; font-size: 13px; font-weight: bold;
                    min-height: 36px; max-width: 200px;
                }}
                QPushButton:hover {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 {THEME.accent2}, stop:1 {THEME.accent_h});
                }}
            """)
            install_ollama_btn.clicked.connect(lambda: self._install_ollama_itself(env_text))
            if self._ollama_models_container:
                self._ollama_models_container.addWidget(install_ollama_btn)

    def _build_ollama_model_card(self, model_name: str, description: str, size: str, installed_set: set, env_text: str) -> QWidget:
        """Build a single model card row with name, desc, size, install button, and progress bar."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {THEME.bg2};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                padding: 8px 12px;
            }}
        """)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(10)

        # Name + description
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        name_label = QLabel(f"<b>{model_name}</b>")
        name_label.setStyleSheet(f"font-size: 13px; color: {THEME.txt}; background: transparent;")
        info_col.addWidget(name_label)

        desc_label = QLabel(description)
        desc_label.setStyleSheet(f"font-size: 11px; color: {THEME.txt2}; background: transparent;")
        info_col.addWidget(desc_label)
        card_layout.addLayout(info_col, 1)

        # Size badge
        size_label = QLabel(size)
        size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        size_label.setFixedWidth(56)
        size_label.setStyleSheet(f"""
            font-size: 11px; color: {THEME.txt3};
            background-color: {THEME.bg2};
            border-radius: 4px;
            padding: 2px 6px;
        """)
        card_layout.addWidget(size_label)

        is_installed = model_name in installed_set

        # Install button / installed badge
        btn = QPushButton("✅ 已安装" if is_installed else "⬇ 安装")
        btn.setFixedHeight(30)
        btn.setMinimumWidth(90)
        if is_installed:
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {THEME.bg3};
                    color: {THEME.green};
                    border: 1px solid {THEME.green};
                    border-radius: 6px;
                    padding: 4px 14px;
                    font-size: 11px;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 4px 14px;
                    font-size: 11px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 {THEME.accent2}, stop:1 {THEME.accent_h});
                }}
            """)
            btn.clicked.connect(lambda checked=False, mn=model_name, et=env_text: self._install_ollama_model(mn, et))
        card_layout.addWidget(btn)

        # Progress bar (hidden initially)
        progress = QProgressBar()
        progress.setFixedHeight(12)
        progress.setMinimumWidth(160)
        progress.setRange(0, 0)  # indeterminate
        progress.setVisible(False)
        progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {THEME.bg2};
                border: 1px solid {THEME.border};
                border-radius: 4px;
                text-align: center;
                font-size: 9px;
                color: {THEME.txt2};
            }}
            QProgressBar::chunk {{
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                border-radius: 3px;
            }}
        """)
        card_layout.addWidget(progress)

        # Store the progress bar as an attribute on the button for access during install
        btn._progress_bar = progress
        btn._card = card

        return card

    def _install_ollama_itself(self, env_text: str):
        """Install Ollama itself on the selected environment."""
        import subprocess
        self._ollama_status_label.setText("⏳ 正在安装 Ollama...")
        self._ollama_status_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; background: transparent; padding: 4px 0;"
        )
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        try:
            if env_text == "🐧 WSL Linux":
                cmd = ["wsl", "bash", "-lc",
                       "curl -fsSL https://ollama.com/install.sh | sh 2>&1"]
            else:
                cmd = ["powershell", "-Command",
                       "& {Invoke-WebRequest -Uri https://ollama.com/install.ps1 -OutFile install.ps1; .\\install.ps1; Remove-Item install.ps1}"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0:
                self._ollama_status_label.setText("✅ Ollama 安装成功！请重新检测")
                self._ollama_status_label.setStyleSheet(
                    f"font-size: 13px; color: {THEME.green}; background: transparent; padding: 4px 0;"
                )
            else:
                self._ollama_status_label.setText(f"❌ 安装失败: {r.stderr.strip()[-100:]}")
                self._ollama_status_label.setStyleSheet(
                    f"font-size: 13px; color: {THEME.red}; background: transparent; padding: 4px 0;"
                )
        except Exception as e:
            self._ollama_status_label.setText(f"❌ 安装出错: {e}")
            self._ollama_status_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.red}; background: transparent; padding: 4px 0;"
            )

    def _install_ollama_model(self, model_name: str, env_text: str):
        """Install an Ollama model: run ollama pull with progress feedback."""
        import subprocess

        self._ollama_status_label.setText(f"⏳ 正在安装 {model_name}...")
        self._ollama_status_label.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt2}; background: transparent; padding: 4px 0;"
        )

        # Find the button that triggered this install and show its progress bar
        # We find it by searching the current model cards
        target_progress = None
        target_btn = None
        if self._ollama_models_container:
            for i in range(self._ollama_models_container.count()):
                item = self._ollama_models_container.itemAt(i)
                if item and item.widget():
                    # Check if this widget has children matching our model name
                    card = item.widget()
                    btn = card.findChild(QPushButton)
                    if btn and btn.text() == "⬇ 安装" and btn._progress_bar is not None:
                        # Verify this is the right card by checking for the model name label
                        labels = card.findChildren(QLabel)
                        card_model_name = None
                        for label in labels:
                            txt = label.text()
                            if f"<b>{model_name}</b>" in txt or model_name in txt:
                                card_model_name = model_name
                                break
                        if card_model_name:
                            target_progress = btn._progress_bar
                            target_btn = btn
                            break

        if target_progress:
            target_progress.setVisible(True)
            target_progress.setRange(0, 0)  # indeterminate

        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        try:
            if env_text == "🐧 WSL Linux":
                proc = subprocess.Popen(
                    ["wsl", "bash", "-lc", f"ollama pull {model_name} 2>&1"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                proc = subprocess.Popen(
                    ["ollama", "pull", model_name],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, creationflags=subprocess.CREATE_NO_WINDOW,
                )

            # Read output and update progress
            import threading as _threading
            output_lines = []

            def _reader_thread():
                for line in proc.stdout:
                    output_lines.append(line)

            reader = _threading.Thread(target=_reader_thread, daemon=True)
            reader.start()

            # Wait with periodic UI updates
            from PySide6.QtCore import QCoreApplication
            while proc.poll() is None:
                proc.wait(1)
                QCoreApplication.processEvents()

            reader.join(timeout=2)
            proc.wait(timeout=30)

            if proc.returncode == 0:
                self._ollama_status_label.setText(f"✅ {model_name} 安装成功")
                self._ollama_status_label.setStyleSheet(
                    f"font-size: 13px; color: {THEME.green}; background: transparent; padding: 4px 0;"
                )
                if target_progress:
                    target_progress.setRange(0, 100)
                    target_progress.setValue(100)
                    QTimer.singleShot(1500, lambda: target_progress.setVisible(False))
                if target_btn:
                    target_btn.setText("✅ 已安装")
                    target_btn.setEnabled(False)
                    target_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: {THEME.bg3};
                            color: {THEME.green};
                            border: 1px solid {THEME.green};
                            border-radius: 6px;
                            padding: 4px 14px;
                            font-size: 11px;
                        }}
                    """)
            else:
                error_text = "".join(output_lines[-5:]) if output_lines else "未知错误"
                self._ollama_status_label.setText(f"❌ {model_name} 安装失败: {error_text.strip()}")
                self._ollama_status_label.setStyleSheet(
                    f"font-size: 13px; color: {THEME.red}; background: transparent; padding: 4px 0;"
                )
                if target_progress:
                    target_progress.setVisible(False)

        except Exception as e:
            self._ollama_status_label.setText(f"❌ 安装出错: {e}")
            self._ollama_status_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.red}; background: transparent; padding: 4px 0;"
            )
            if target_progress:
                target_progress.setVisible(False)

        # Re-run detection to refresh installed model list
        QTimer.singleShot(2000, lambda: self._detect_ollama(env_text))

    def _clear_layout(self, layout):
        """Recursively remove all widgets and sub-layouts from a layout."""
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
                elif item.layout():
                    self._clear_layout(item.layout())

    # ── World Model detection ────────────────────────────────────────────

    def _on_wm_env_changed(self, index: int):
        """Handle World Model environment change — run detection."""
        env_text = self._wm_env_combo.currentText() if self._wm_env_combo else ""
        self._detect_wm_backends(env_text)

    def _detect_wm_backends(self, env_text: str):
        """Detect AETHER and MCP-Cosmos backends in the selected environment."""
        import subprocess
        import shutil

        if not self._wm_aether_label or not self._wm_cosmos_label:
            return

        self._wm_aether_label.setText("AETHER 后端: ⏳ 检测中...")
        self._wm_cosmos_label.setText("MCP-Cosmos 后端: ⏳ 检测中...")
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        aether_found = False
        cosmos_found = False

        try:
            if env_text == "🐧 WSL Linux":
                r1 = subprocess.run(
                    ["wsl", "bash", "-lc", "command -v aether 2>/dev/null || command -v aetherd 2>/dev/null || echo ''"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                aether_found = bool(r1.stdout.strip())
                r2 = subprocess.run(
                    ["wsl", "bash", "-lc", "command -v mcp-cosmos 2>/dev/null || command -v cosmos 2>/dev/null || echo ''"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                cosmos_found = bool(r2.stdout.strip())
            elif env_text == "🪟 Windows":
                aether_found = shutil.which("aether") is not None or shutil.which("aetherd") is not None
                cosmos_found = shutil.which("mcp-cosmos") is not None or shutil.which("cosmos") is not None
            else:  # ☁️ 远程服务器
                self._wm_aether_label.setText("AETHER 后端: ⏳ 需连接后检测")
                self._wm_aether_label.setStyleSheet(
                    f"font-size: 13px; color: {THEME.txt3}; background: transparent; padding: 4px 0;"
                )
                self._wm_cosmos_label.setText("MCP-Cosmos 后端: ⏳ 需连接后检测")
                self._wm_cosmos_label.setStyleSheet(
                    f"font-size: 13px; color: {THEME.txt3}; background: transparent; padding: 4px 0;"
                )
                return
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass

        if aether_found:
            self._wm_aether_label.setText("AETHER 后端: ✅")
            self._wm_aether_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.green}; background: transparent; padding: 4px 0;"
            )
        else:
            self._wm_aether_label.setText("AETHER 后端: ❌")
            self._wm_aether_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.red}; background: transparent; padding: 4px 0;"
            )

        if cosmos_found:
            self._wm_cosmos_label.setText("MCP-Cosmos 后端: ✅")
            self._wm_cosmos_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.green}; background: transparent; padding: 4px 0;"
            )
        else:
            self._wm_cosmos_label.setText("MCP-Cosmos 后端: ❌")
            self._wm_cosmos_label.setStyleSheet(
                f"font-size: 13px; color: {THEME.red}; background: transparent; padding: 4px 0;"
            )

    def _on_llm_key_clicked(self):
        """Clear masked API key and switch to password mode for editing."""
        if self._llm_api_key_edit:
            self._llm_api_key_edit.clear()
            self._llm_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._llm_api_key_edit.setFocus()

    def _on_llm_provider_changed(self, provider: str):
        """Update placeholders when provider changes."""
        presets = {
            "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat"),
            "OpenAI": ("https://api.openai.com/v1", "gpt-4o"),
            "Anthropic": ("https://api.anthropic.com/v1", "claude-sonnet-4"),
            "Google Gemini": ("https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash"),
            "Groq": ("https://api.groq.com/openai/v1", "mixtral-8x22b"),
            "Together AI": ("https://api.together.xyz/v1", "mixtral-8x22b"),
            "Mistral AI": ("https://api.mistral.ai/v1", "mistral-large"),
            "xAI": ("https://api.x.ai/v1", "grok-3"),
            "自定义": ("https://api.deepseek.com/v1", "deepseek-chat"),
        }
        url, model = presets.get(provider, ("https://api.deepseek.com/v1", "deepseek-chat"))
        if self._llm_base_url_combo:
            idx = self._llm_base_url_combo.findText(url)
            if idx >= 0:
                self._llm_base_url_combo.setCurrentIndex(idx)
            else:
                self._llm_base_url_combo.setCurrentText(url)
        if self._llm_model_combo:
            idx = self._llm_model_combo.findText(model)
            if idx >= 0:
                self._llm_model_combo.setCurrentIndex(idx)
            else:
                self._llm_model_combo.setCurrentText(model)

    def _get_selected_env_key(self) -> str:
        """Get the internal env key for the instance environment combo."""
        env_map = {
            "🐧 WSL Linux": "wsl_linux",
            "🪟 Windows": "local_windows",
            "☁️ 远程服务器": "ssh_remote",
        }
        text = self._ws_instance_env_combo.currentText() if self._ws_instance_env_combo else ""
        return env_map.get(text, "wsl_linux")

    def showEvent(self, event):
        """Refresh configs when the tab is shown."""
        super().showEvent(event)
        self._load_configs()
