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
    QProgressBar,
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
from .utils.local_config import load_local_config, save_local_config


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
        self._app = app
        self._sidebar_expanded = True
        self._nav_buttons: list[QPushButton] = []

        # Load local config (persistent user preferences)
        self._local_cfg = load_local_config()
        self._workspace_path = (
            workspace_path
            or self._local_cfg.get("last_workspace_path")
            or self._local_cfg.get("default_workspace_path")
            or str(resolve_partner_root())
        )

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

        # ── Initialise ──────────
        self._loading_phase = 0
        QTimer.singleShot(50, lambda: self._update_loading_status("正在初始化窗口…"))
        QTimer.singleShot(200, lambda: self._update_loading_status("正在加载界面组件…"))
        QTimer.singleShot(500, lambda: self._update_loading_status("正在准备数据…"))

    def _center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            center = screen.availableGeometry().center()
            frame = self.frameGeometry()
            frame.moveCenter(center)
            self.move(frame.topLeft())

    # ── Initialise — no setup wizard, always go directly to chat ──────────
        self._navigate_to(0)

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
        # Save splitter sizes from child pages (only if they've been initialised)
        chat_page = self._page_instances.get(0)
        if chat_page is not None and hasattr(chat_page, '_splitter'):
            data["chat_splitter"] = list(chat_page._splitter.sizes())
        settings_page = self._page_instances.get(2)
        if settings_page is not None and hasattr(settings_page, '_splitter'):
            data["settings_splitter"] = list(settings_page._splitter.sizes())
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
        chat_page = self._page_instances.get(0)
        settings_page = self._page_instances.get(2)

        # Chat page splitter
        chat_sizes = data.get("chat_splitter")
        if chat_sizes and isinstance(chat_sizes, list) and len(chat_sizes) > 0:
            if chat_page is not None and hasattr(chat_page, '_splitter'):
                try:
                    sizes = [int(s) for s in chat_sizes]
                    if all(s > 0 for s in sizes):
                        chat_page._splitter.setSizes(sizes)
                except (ValueError, TypeError):
                    pass
        # Settings page splitter
        settings_sizes = data.get("settings_splitter")
        if settings_sizes and isinstance(settings_sizes, list) and len(settings_sizes) > 0:
            if settings_page is not None and hasattr(settings_page, '_splitter'):
                try:
                    sizes = [int(s) for s in settings_sizes]
                    if all(s > 0 for s in sizes):
                        settings_page._splitter.setSizes(sizes)
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

        # ── Loading overlay (shown immediately, hidden when content is ready) ──
        self._loading_overlay = self._build_loading_overlay()
        self._loading_overlay.setVisible(True)
        self._loading_overlay.raise_()

        main_layout.addWidget(self._sidebar)

        # ── Right content area ──
        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet(f"background-color: {THEME.bg};")

        # ── Lazy page creation: create placeholder pages first, instantiate
        #     real page objects only on first navigation. This makes the window
        #     shell appear instantly without waiting for I/O-heavy init. ──
        self._page_instances: dict[int, QWidget] = {}
        self._page_initialized: set[int] = set()

        # Index 0 → ChatPage, 1 → InstancesPage, 2 → SettingsPage
        # Add a blank placeholder widget for each so QStackedWidget has them
        for _ in range(3):
            placeholder = QWidget()
            placeholder.setStyleSheet(f"background-color: {THEME.bg};")
            self._content_stack.addWidget(placeholder)

        main_layout.addWidget(self._content_stack, 1)

        # Start at chat page (triggers lazy creation)
        self._navigate_to(0)

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

    def _build_loading_overlay(self) -> QWidget:
        """Build a loading overlay card shown while the app initialises.

        Shows a card at centre with app name, progress bar, and status hints.
        Hidden automatically once ChatPage finishes its deferred init.
        """
        overlay = QWidget(self)
        overlay.setObjectName("loadingOverlay")
        overlay.setStyleSheet(f"""
            QWidget#loadingOverlay {{
                background-color: {THEME.bg};
            }}
        """)

        layout = QVBoxLayout(overlay)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Card container
        card = QFrame()
        card.setObjectName("loadingCard")
        card.setFixedSize(400, 220)
        card.setStyleSheet(f"""
            QFrame#loadingCard {{
                background-color: {THEME.card};
                border: 1px solid {THEME.border};
                border-radius: 16px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 28, 32, 28)
        card_layout.setSpacing(12)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Logo / title
        title_label = QLabel("🤝  Partner")
        title_label.setStyleSheet(f"""
            font-size: 22px; font-weight: bold; color: {THEME.accent};
            background: transparent; qproperty-alignment: AlignCenter;
        """)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title_label)

        # Status text (updated via _update_loading_status)
        self._loading_status = QLabel("正在加载…")
        self._loading_status.setStyleSheet(f"""
            font-size: 13px; color: {THEME.txt2};
            background: transparent; qproperty-alignment: AlignCenter;
        """)
        self._loading_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._loading_status)

        # Progress bar (indeterminate — continuous loop)
        self._loading_progress = QProgressBar()
        self._loading_progress.setMinimum(0)
        self._loading_progress.setMaximum(0)  # indeterminate
        self._loading_progress.setFixedHeight(6)
        self._loading_progress.setTextVisible(False)
        self._loading_progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {THEME.bg3};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {THEME.accent}, stop:1 {THEME.green});
                border-radius: 3px;
            }}
        """)
        card_layout.addWidget(self._loading_progress)

        layout.addWidget(card)
        return overlay

    def _update_loading_status(self, text: str):
        """Update the loading overlay status text."""
        if hasattr(self, '_loading_status') and self._loading_status:
            self._loading_status.setText(text)
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.processEvents()

    def _hide_loading_overlay(self):
        """Fade out and hide the loading overlay."""
        if hasattr(self, '_loading_overlay') and self._loading_overlay:
            self._loading_overlay.setVisible(False)
            # Allow the content area to receive mouse events
            self._content_stack.raise_()

    def _navigate_to(self, index: int):
        """Navigate to a specific page, creating it lazily on first visit."""
        if index >= self._content_stack.count():
            return

        # Lazy-create the page on first visit
        if index not in self._page_initialized:
            page = self._create_page(index)
            if page is not None:
                # Replace placeholder at this index
                self._content_stack.removeWidget(self._content_stack.widget(index))
                self._content_stack.insertWidget(index, page)

                self._page_instances[index] = page
                self._page_initialized.add(index)

        self._content_stack.setCurrentIndex(index)

    def _create_page(self, index: int) -> QWidget | None:
        """Factory: create the page widget for a given navigation index.

        Also wires cross-page signals once so every page stays in sync.
        """
        from .pages import ChatPage, InstancesPage, SettingsPage

        if index == 0:
            page = ChatPage()
            # Connect cross-page sync signals (set up once here)
            page.instances_changed.connect(self._on_any_instances_changed)
            page.loading_complete.connect(self._hide_loading_overlay)
            return page
        elif index == 1:
            page = InstancesPage(workspace_path=self._workspace_path)
            page.instances_changed.connect(self._on_any_instances_changed)
            return page
        elif index == 2:
            page = SettingsPage(workspace_path=self._workspace_path)
            page.workspace_changed.connect(self._on_workspace_changed)
            page.config_saved.connect(self._on_config_saved)
            return page
        return None

    # ── Cross-page sync handlers ──────────────────────────────────────────

    def _on_any_instances_changed(self):
        """Called when instances are created/modified on any page.

        The originating page already refreshed itself (it calls _refresh()
        before emitting the signal), so here we only sync OTHER pages
        to avoid duplicate I/O that freezes the UI.
        """
        # Refresh chat page's instance selector
        chat_page = self._page_instances.get(0)
        if chat_page is not None and hasattr(chat_page, 'refresh_instance_selector'):
            chat_page.refresh_instance_selector()
        # Refresh settings page's default instance combo
        settings_page = self._page_instances.get(2)
        if settings_page is not None and hasattr(settings_page, '_load_configs'):
            settings_page._load_configs()

    def _on_config_saved(self):
        """Called when the settings page saves any config.

        Refreshes the chat page's instance selector in case the default
        instance or workspace changed.
        """
        chat_page = self._page_instances.get(0)
        if chat_page is not None and hasattr(chat_page, 'refresh_instance_selector'):
            chat_page.refresh_instance_selector()

    def _on_workspace_changed(self, new_workspace: str):
        """Handle workspace path change from settings page."""
        self._workspace_path = new_workspace
        # Save to local config for next startup
        save_local_config({
            "last_workspace_path": new_workspace,
            "default_workspace_path": new_workspace,
        })
        # Propagate workspace to all initialized pages
        for idx in list(self._page_instances):
            pg = self._page_instances[idx]
            if hasattr(pg, 'set_workspace'):
                pg.set_workspace(new_workspace)

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
        for idx in list(self._page_instances):
            pg = self._page_instances[idx]
            if hasattr(pg, '_refresh'):
                pg._refresh()
        self._update_status()

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