"""ModernMainWindow - main window with left nav sidebar and right content panel."""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QByteArray, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QSystemTrayIcon,
)

from partner.monitoring.instance_root import (
    resolve_partner_root,
    resolve_global_config_path,
    resolve_instances_dir,
)
from .theme import THEME, get_default_font, generate_stylesheet
from .pages import (
    ChatPage,
    InstancesPage,
    SettingsPage,
    AgentsPage,
)


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


NAV_ITEMS = [
    ("💬", "对话", 0),
    ("💻", "实例管理", 1),
    ("⚙️", "配置中心", 2),
]


class ModernMainWindow(QMainWindow):
    """Main application window with sidebar navigation."""

    def __init__(self, workspace_path: str | None = None, app: QApplication | None = None):
        super().__init__()
        self._workspace_path = workspace_path or str(resolve_partner_root())
        self._app = app
        self._sidebar_expanded = True
        self._nav_buttons: list[QPushButton] = []

        self.setWindowTitle("Partner")
        self.setMinimumSize(1200, 800)
        self.resize(1275, 765)

        # Set up font
        default_font = get_default_font()
        self.setFont(default_font)

        # Apply stylesheet
        self.setStyleSheet(generate_stylesheet())

        self._build_ui()
        self._setup_system_tray()

        # Center on screen
        self._center_on_screen()

        # Restore previous layout (geometry, sidebar state, splitter sizes)
        self._restore_layout()

        # ── First-run setup wizard ──────────────────────────────────────
        QTimer.singleShot(100, self._check_first_run)

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            center = screen.availableGeometry().center()
            frame = self.frameGeometry()
            frame.moveCenter(center)
            self.move(frame.topLeft())

    # ── First-run setup wizard ──────────────────────────────────────────

    def _check_first_run(self):
        """Detect first run and show setup wizard if needed."""
        from partner.state.config import workspace_has_partner_config
        config_exists = workspace_has_partner_config(self._workspace_path)
        if config_exists:
            return  # Already configured

        # Also check fallback: if .partner_workspace pointer exists but
        # workspace hasn't been set up yet, we still show the wizard
        self._show_first_run_wizard()

    def _show_first_run_wizard(self):
        """Show the first-run setup wizard dialog."""
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("欢迎使用 Partner")
        dialog.setMinimumSize(600, 450)
        dialog.resize(640, 480)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Title
        title = QLabel("🤝 欢迎使用 Partner")
        title.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {THEME.accent};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("您的 AI 研究助手 · 首次配置")
        subtitle.setStyleSheet(f"font-size: 13px; color: {THEME.txt2};")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(12)

        # Workspace path
        ws_label = QLabel("工作区路径（存放实例数据与配置）")
        ws_label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {THEME.txt};")
        layout.addWidget(ws_label)

        ws_row = QWidget()
        ws_row.setStyleSheet("background: transparent;")
        ws_row_layout = QHBoxLayout(ws_row)
        ws_row_layout.setContentsMargins(0, 0, 0, 0)

        from pathlib import Path as _Path
        default_ws = str(_Path.home() / "partner_workspace")
        self._wizard_ws_edit = QLineEdit(self._workspace_path if os.path.exists(self._workspace_path) else default_ws)
        self._wizard_ws_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {THEME.input_bg};
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 13px;
            }}
        """)
        ws_row_layout.addWidget(self._wizard_ws_edit, 1)

        browse_btn = QPushButton("浏览")
        browse_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
                min-height: 36px;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.card_hl}, stop:1 {THEME.bg2});
                border-color: {THEME.accent};
                color: {THEME.accent};
            }}
        """)
        browse_btn.clicked.connect(lambda: self._wizard_browse_workspace(dialog))
        ws_row_layout.addWidget(browse_btn)
        layout.addWidget(ws_row)

        layout.addSpacing(8)

        # LLM API section (collapsible)
        api_label = QLabel("配置 LLM API（可选，也可在设置页中配置）")
        api_label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {THEME.txt};")
        layout.addWidget(api_label)

        form = QFormLayout()
        form.setSpacing(8)

        self._wizard_provider = QComboBox()
        self._wizard_provider.addItems(["DeepSeek", "OpenAI", "自定义"])
        form.addRow("Provider:", self._wizard_provider)

        self._wizard_api_key = QLineEdit()
        self._wizard_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._wizard_api_key.setPlaceholderText("sk-... (留空可跳过)")
        form.addRow("API Key:", self._wizard_api_key)

        self._wizard_model = QComboBox()
        self._wizard_model.setEditable(True)
        self._wizard_model.addItems(["deepseek-chat", "deepseek-reasoner", "gpt-4o"])
        self._wizard_model.setCurrentText("deepseek-chat")
        form.addRow("默认模型:", self._wizard_model)

        self._wizard_base_url = QLineEdit()
        self._wizard_base_url.setPlaceholderText("https://api.deepseek.com/v1")
        self._wizard_base_url.setText("https://api.deepseek.com/v1")
        form.addRow("Base URL:", self._wizard_base_url)

        layout.addLayout(form)
        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        skip_btn = QPushButton("跳过")
        skip_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.txt2};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 13px;
                min-height: 38px;
            }}
            QPushButton:hover {{
                border-color: {THEME.accent};
                color: {THEME.accent};
            }}
        """)
        skip_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(skip_btn)

        btn_layout.addStretch()

        save_btn = QPushButton("保存并启动")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 24px;
                font-size: 14px;
                font-weight: bold;
                min-height: 38px;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent2}, stop:1 {THEME.accent_h});
            }}
        """)
        save_btn.clicked.connect(lambda: self._wizard_save(dialog))
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

        dialog.exec()

    def _wizard_browse_workspace(self, dialog):
        """Browse for workspace directory in wizard."""
        directory = QFileDialog.getExistingDirectory(
            dialog, "选择工作区路径",
            self._wizard_ws_edit.text() if hasattr(self, '_wizard_ws_edit') else ""
        )
        if directory and hasattr(self, '_wizard_ws_edit'):
            self._wizard_ws_edit.setText(directory)

    def _wizard_save(self, dialog):
        """Save wizard settings."""
        ws = self._wizard_ws_edit.text().strip()
        if not ws:
            QMessageBox.warning(dialog, "提示", "请选择工作区路径")
            return

        # Create workspace directory structure
        os.makedirs(os.path.join(ws, "config"), exist_ok=True)
        os.makedirs(os.path.join(ws, "instances"), exist_ok=True)

        # Save LLM API config
        reverse_map = {"DeepSeek": "deepseek", "OpenAI": "openai", "自定义": "custom"}
        provider_raw = self._wizard_provider.currentText()
        api_key = self._wizard_api_key.text().strip()
        model = self._wizard_model.currentText().strip()
        base_url = self._wizard_base_url.text().strip() or "https://api.deepseek.com/v1"

        partner_cfg = {
            "workspace": {"path": ws, "readonly_dirs": []},
            "agent": {"backend": "hermes"},
            "llm": {
                "provider": reverse_map.get(provider_raw, "deepseek"),
                "api_key": api_key,
                "model": model or "deepseek-chat",
                "base_url": base_url,
            },
            "scheduler": {"interval_minutes": 30, "max_tasks_per_cycle": 1, "heartbeat_timeout_minutes": 60},
            "name": "Partner",
        }
        from partner.state.config import save_partner_config_data
        save_partner_config_data(ws, partner_cfg)

        # Write pointer file
        from partner.state.setup import save_workspace_pointer
        save_workspace_pointer(ws)

        # Update workspace and reload
        self._workspace_path = ws
        self._instances_page.set_workspace(ws)
        self._settings_page.set_workspace(ws)

        QMessageBox.information(dialog, "完成", "配置已保存！\n\n您可以在「配置中心」中随时修改这些设置。")
        dialog.accept()

    # ── Layout persistence ──────────────────────────────────────────────────

    @staticmethod
    def _layout_path() -> str:
        """Return the path to the GUI layout JSON file."""
        return os.path.join(str(Path.home()), ".partner", "gui_layout.json")

    def _save_layout(self) -> None:
        """Persist window geometry, sidebar state, and splitter sizes to disk."""
        data: dict = {
            "sidebar_expanded": self._sidebar_expanded,
        }
        # Save window geometry as base64
        geo_bytes = self.saveGeometry()
        if geo_bytes:
            data["geometry"] = str(geo_bytes.toBase64(), "utf-8")
        # Save splitter sizes from child pages
        if hasattr(self, "_chat_page") and hasattr(self._chat_page, "_splitter"):
            data["chat_splitter"] = list(self._chat_page._splitter.sizes())
        if hasattr(self, "_settings_page") and hasattr(self._settings_page, "_splitter"):
            data["settings_splitter"] = list(self._settings_page._splitter.sizes())
        # Write to disk
        path = self._layout_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except (OSError, IOError, TypeError):
            pass

    def _restore_layout(self) -> None:
        """Restore window geometry, sidebar state, and splitter sizes from disk."""
        data = _load_json(self._layout_path())
        if not data:
            return
        # Restore window geometry
        geo_b64 = data.get("geometry")
        if geo_b64 and isinstance(geo_b64, str):
            try:
                geo_bytes = QByteArray.fromBase64(geo_b64.encode("utf-8"))
                self.restoreGeometry(geo_bytes)
            except Exception:
                pass
        # Restore sidebar expanded state
        if "sidebar_expanded" in data:
            expanded = bool(data["sidebar_expanded"])
            if expanded != self._sidebar_expanded:
                self._toggle_sidebar()
        # Restore splitters on next event-loop tick (layout needs to settle)
        QTimer.singleShot(0, lambda: self._apply_splitter_sizes(data))

    def _apply_splitter_sizes(self, data: dict) -> None:
        """Apply saved splitter sizes after the layout has settled."""
        # Chat page splitter
        chat_sizes = data.get("chat_splitter")
        if chat_sizes and isinstance(chat_sizes, list) and len(chat_sizes) > 0:
            if hasattr(self, "_chat_page") and hasattr(self._chat_page, "_splitter"):
                try:
                    sizes = [int(s) for s in chat_sizes]
                    if all(s > 0 for s in sizes):
                        self._chat_page._splitter.setSizes(sizes)
                except (ValueError, TypeError):
                    pass
        # Settings page splitter
        settings_sizes = data.get("settings_splitter")
        if settings_sizes and isinstance(settings_sizes, list) and len(settings_sizes) > 0:
            if hasattr(self, "_settings_page") and hasattr(self._settings_page, "_splitter"):
                try:
                    sizes = [int(s) for s in settings_sizes]
                    if all(s > 0 for s in sizes):
                        self._settings_page._splitter.setSizes(sizes)
                except (ValueError, TypeError):
                    pass

    def _build_ui(self):
        """Build the main layout with sidebar and content area."""
        central = QWidget()
        central.setObjectName("centralWindow")
        central.setStyleSheet("QWidget#centralWindow { border: 1px solid #1a1a1a; }")
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Left sidebar ──
        self._sidebar = QFrame()
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setStyleSheet(f"""
            QFrame#sidebar {{
                background-color: {THEME.sidebar_bg};
                border-right: 1px solid {THEME.border};
            }}
        """)
        self._sidebar.setMinimumWidth(THEME.sidebar_collapsed)
        self._sidebar.setMaximumWidth(THEME.sidebar_width)

        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(12, 8, 12, 8)
        sidebar_layout.setSpacing(2)

        # App logo/brand
        brand_label = QLabel("  🤝 Partner")
        brand_label.setObjectName("brand")
        brand_label.setStyleSheet(f"""
            font-size: 16px;
            font-weight: bold;
            color: {THEME.accent};
            padding: 8px 16px;
        """)
        brand_label.setMinimumHeight(36)
        sidebar_layout.addWidget(brand_label)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background-color: {THEME.border}; max-height: 1px;")
        sidebar_layout.addWidget(sep)
        sidebar_layout.addSpacing(8)

        # Navigation buttons
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for icon_text, label, index in NAV_ITEMS:
            btn = QPushButton()
            btn.setObjectName("nav_btn")
            btn.setCheckable(True)
            btn.setMinimumHeight(36)
            btn.setText(f"  {icon_text}  {label}")
            self._nav_buttons.append(btn)
            self._nav_group.addButton(btn, index)
            sidebar_layout.addWidget(btn)

        # Default: first nav button (对话) selected
        if self._nav_buttons:
            self._nav_buttons[0].setChecked(True)

        self._nav_group.idClicked.connect(self._navigate_to)

        sidebar_layout.addStretch()

        # Collapse/expand toggle at bottom
        self._toggle_btn = QPushButton("  ◀  收起侧边栏")
        self._toggle_btn.setObjectName("toggle_btn")
        self._toggle_btn.setToolTip("收起侧边栏 (Ctrl+B)")
        self._toggle_btn.setMinimumHeight(36)
        self._toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.sidebar_bg}, stop:1 {THEME.bg3});
                color: {THEME.txt2};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                padding: 6px 16px;
                text-align: left;
                font-size: 12px;
                font-weight: bold;
                margin-top: 4px;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.nav_hover}, stop:1 {THEME.card_hl});
                color: {THEME.txt};
                border-color: {THEME.accent};
            }}
            QPushButton:pressed {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg3}, stop:1 {THEME.border});
                border-color: {THEME.accent_h};
            }}
        """)
        self._toggle_btn.clicked.connect(self._toggle_sidebar)
        sidebar_layout.addWidget(self._toggle_btn)

        main_layout.addWidget(self._sidebar)

        # ── Right content area ──
        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet(f"background-color: {THEME.bg};")

        self._chat_page = ChatPage()
        self._instances_page = InstancesPage(workspace_path=self._workspace_path)
        self._settings_page = SettingsPage(workspace_path=self._workspace_path)
        self._settings_page.workspace_changed.connect(self._on_workspace_changed)
        self._agents_page = AgentsPage()

        self._content_stack.addWidget(self._chat_page)        # index 0
        self._content_stack.addWidget(self._instances_page)    # index 1
        self._content_stack.addWidget(self._settings_page)     # index 2

        main_layout.addWidget(self._content_stack, 1)

        # Start at chat page
        self._navigate_to(0)

        # Set up debug/test menu
        self._setup_test_menu()

    def _setup_system_tray(self):
        """Set up system tray icon and menu."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = None
            return
        self._tray_icon = QSystemTrayIcon(self)
        self._tray_icon.setToolTip("Partner")

        # Try to set icon from assets
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "partner_app_v2.ico"
        )
        if os.path.exists(icon_path):
            self._tray_icon.setIcon(QIcon(icon_path))

        # Create tray menu
        tray_menu = QMenu()

        show_action = QAction("打开主窗口", self)
        show_action.triggered.connect(self._show_window)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        start_action = QAction("启动服务", self)
        start_action.triggered.connect(self._start_service)
        tray_menu.addAction(start_action)

        stop_action = QAction("停止服务", self)
        stop_action.triggered.connect(self._stop_service)
        tray_menu.addAction(stop_action)

        tray_menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self._tray_icon.setContextMenu(tray_menu)
        self._tray_icon.show()

        # Double-click on tray icon shows window
        self._tray_icon.activated.connect(self._on_tray_activated)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick,
                      QSystemTrayIcon.ActivationReason.Trigger):
            self._show_window()

    def _show_window(self):
        """Show and bring window to front."""
        self.show()
        self.raise_()
        self.activateWindow()

    def _start_service(self):
        """Start the default instance service."""
        if not hasattr(self, '_tray_icon') or not self._tray_icon:
            return
        config_path = resolve_global_config_path()
        config = _load_json(str(config_path))
        default_id = config.get("default_instance", "")
        if default_id:
            inst_dir = str(resolve_instances_dir() / default_id)
            if os.path.exists(os.path.join(inst_dir, "instance.pid")):
                self._tray_icon.showMessage("Partner", f"实例 {default_id} 已在运行",
                                           QSystemTrayIcon.MessageIcon.Information, 2000)
            else:
                self._tray_icon.showMessage("Partner", f"正在启动实例 {default_id}...",
                                           QSystemTrayIcon.MessageIcon.Information, 2000)
        else:
            self._tray_icon.showMessage("Partner", "未设置默认实例",
                                       QSystemTrayIcon.MessageIcon.Warning, 2000)

    def _stop_service(self):
        """Stop the default instance service."""
        if not hasattr(self, '_tray_icon') or not self._tray_icon:
            return
        config_path = resolve_global_config_path()
        config = _load_json(str(config_path))
        default_id = config.get("default_instance", "")
        if default_id:
            inst_dir = str(resolve_instances_dir() / default_id)
            pid_path = os.path.join(inst_dir, "instance.pid")
            if os.path.exists(pid_path):
                try:
                    pid = int(open(pid_path).read().strip())
                    os.kill(pid, 15)
                    self._tray_icon.showMessage("Partner", f"实例 {default_id} 已停止",
                                               QSystemTrayIcon.MessageIcon.Information, 2000)
                except Exception as e:
                    self._tray_icon.showMessage("Partner", f"停止失败: {e}",
                                               QSystemTrayIcon.MessageIcon.Critical, 2000)
            else:
                self._tray_icon.showMessage("Partner", "实例未运行",
                                           QSystemTrayIcon.MessageIcon.Information, 2000)

    def _quit_app(self):
        """Quit the application."""
        if hasattr(self, '_tray_icon') and self._tray_icon:
            self._tray_icon.hide()
        if self._app:
            self._app.quit()
        else:
            QApplication.quit()

    def _toggle_sidebar(self):
        """Toggle sidebar between expanded and collapsed states."""
        self._sidebar_expanded = not self._sidebar_expanded
        width = THEME.sidebar_width if self._sidebar_expanded else THEME.sidebar_collapsed
        self._sidebar.setMaximumWidth(width)
        self._sidebar.setMinimumWidth(width)
        if self._sidebar_expanded:
            self._toggle_btn.setText("  ◀  收起侧边栏")
            self._toggle_btn.setToolTip("收起侧边栏 (Ctrl+B)")
        else:
            self._toggle_btn.setText("  ▶  展开")
            self._toggle_btn.setToolTip("展开侧边栏 (Ctrl+B)")

        for i, btn in enumerate(self._nav_buttons):
            if self._sidebar_expanded:
                icon_text, label, _ = NAV_ITEMS[i]
                btn.setText(f"  {icon_text}  {label}")
            else:
                icon_text, label, _ = NAV_ITEMS[i]
                btn.setText(f"  {icon_text}")
            btn.setToolTip(label if not self._sidebar_expanded else "")

    def _navigate_to(self, index: int):
        """Navigate to a specific page."""
        self._content_stack.setCurrentIndex(index)
        # QButtonGroup manages checked state via exclusive behavior

    def _on_workspace_changed(self, new_workspace: str):
        """Handle workspace path change from settings page."""
        self._workspace_path = new_workspace
        self._instances_page.set_workspace(new_workspace)
        self._settings_page.set_workspace(new_workspace)

    def _update_status(self):
        """Update the tray icon tooltip with instance status."""
        config_path = resolve_global_config_path()
        config = _load_json(str(config_path))
        default_id = config.get("default_instance", "")
        instances = config.get("instances", {})

        if not default_id:
            if hasattr(self, '_tray_icon') and self._tray_icon:
                self._tray_icon.setToolTip("Partner - 未配置")
            return

        inst_dir = str(resolve_instances_dir() / default_id)
        pid_path = os.path.join(inst_dir, "instance.pid")
        running = False
        if os.path.exists(pid_path):
            try:
                pid = int(open(pid_path).read().strip())
                os.kill(pid, 0)
                running = True
            except Exception:
                pass

        if running:
            if hasattr(self, '_tray_icon') and self._tray_icon:
                self._tray_icon.setToolTip(f"Partner - 实例 {default_id} 运行中")
        else:
            if hasattr(self, '_tray_icon') and self._tray_icon:
                self._tray_icon.setToolTip("Partner - 实例已停止")

    def _refresh_all(self):
        """Refresh all pages."""
        self._instances_page._refresh()
        self._update_status()

    def _send_test_message(self):
        """Send a test message to the chat page for debugging."""
        if hasattr(self, '_chat_page') and hasattr(self._chat_page, 'send_test_message'):
            self._chat_page.send_test_message()
        else:
            QMessageBox.information(self, "提示", "聊天页面未初始化")

    def _setup_test_menu(self):
        """Set up a debug/test menu in the menu bar."""
        menu_bar = self.menuBar()
        # Check if test menu already exists
        for action in menu_bar.actions():
            if action.text() == "调试":
                return
        test_menu = menu_bar.addMenu("调试")
        send_test_action = QAction("发送测试消息", self)
        send_test_action.triggered.connect(self._send_test_message)
        test_menu.addAction(send_test_action)

    def _show_about(self):
        """Show the About dialog."""
        QMessageBox.about(
            self,
            "关于 Partner",
            "🤝 Partner v2.0\n\n"
            "你的 AI 研究助手\n"
            "Your AI Research Companion\n\n"
            "基于多实例架构，支持 Hermes / OpenClaw 后端\n"
            "QQ Bot 集成 · 世界模型 · 自动任务调度\n\n"
            "Powered by Nous Research"
        )

    def _open_workspace(self):
        """Open workspace directory in file explorer."""
        if os.path.exists(self._workspace_path):
            if os.name == "nt":
                import subprocess
                subprocess.Popen(["explorer", self._workspace_path],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", self._workspace_path])
        else:
            QMessageBox.warning(self, "提示", f"工作区路径不存在:\n{self._workspace_path}")

    def closeEvent(self, event):
        """Override close event to save layout and minimize to tray instead of quitting."""
        self._save_layout()
        if hasattr(self, '_tray_icon') and self._tray_icon and self._tray_icon.isVisible():
            self.hide()
            self._tray_icon.showMessage(
                "Partner",
                "应用程序已最小化到系统托盘",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
            event.ignore()
        else:
            event.accept()
