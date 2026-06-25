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
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QSystemTrayIcon,
    QMessageBox,
)

from partner.instance_root import (
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

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            center = screen.availableGeometry().center()
            frame = self.frameGeometry()
            frame.moveCenter(center)
            self.move(frame.topLeft())

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
