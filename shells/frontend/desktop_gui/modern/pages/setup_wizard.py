"""Setup Wizard page - first-run configuration for new Partner workspace."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class SetupWizardPage(QWidget):
    """First-run setup wizard to configure Partner workspace and preferences.

    Binds a workspace path, tests the environment, and emits
    ``setup_completed`` when the user finishes.
    """

    setup_completed = Signal(str)

    def __init__(self, workspace_path: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._workspace_path = workspace_path or str(Path.home() / "partner_workspace")

        self._build_ui()
        self._load_current_config()

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        # Header
        header = QLabel("🎉 欢迎使用 Partner！")
        header.setStyleSheet("font-size: 28px; font-weight: 700; color: #16202d;")
        layout.addWidget(header)

        subtitle = QLabel("请配置工作区路径和基础设置，即可开始使用。")
        subtitle.setStyleSheet("font-size: 15px; color: #6b7788; margin-bottom: 12px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        form_layout = QVBoxLayout(content)
        form_layout.setSpacing(14)
        form_layout.setContentsMargins(0, 0, 0, 0)

        # ── Workspace path ──────────────────────────────────────────────
        ws_label = QLabel("工作区路径")
        ws_label.setStyleSheet("font-size: 14px; font-weight: 600; color: #16202d;")
        form_layout.addWidget(ws_label)

        ws_row = QHBoxLayout()
        self._ws_input = QLineEdit()
        self._ws_input.setPlaceholderText("选择或输入工作区路径...")
        self._ws_input.setStyleSheet("""
            QLineEdit {
                background: #f3f6fa; border: 1px solid #d8e0ea;
                border-radius: 12px; padding: 10px 12px;
                font-size: 14px;
            }
        """)
        ws_row.addWidget(self._ws_input, 1)

        browse_btn = QPushButton("浏览...")
        browse_btn.setStyleSheet("""
            QPushButton {
                background: #edf2f7; border: 1px solid #d8e0ea;
                border-radius: 12px; padding: 10px 16px;
                font-size: 14px; font-weight: 600;
            }
            QPushButton:hover { background: #e2e8f0; }
        """)
        browse_btn.clicked.connect(self._on_browse)
        ws_row.addWidget(browse_btn)
        form_layout.addLayout(ws_row)

        form_layout.addSpacing(8)

        # ── Action buttons ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._complete_btn = QPushButton("✅ 完成配置")
        self._complete_btn.setStyleSheet("""
            QPushButton {
                background: #2d6df6; color: white;
                border: none; border-radius: 12px;
                padding: 12px 28px; font-size: 15px; font-weight: 700;
            }
            QPushButton:hover { background: #2563eb; }
        """)
        self._complete_btn.clicked.connect(self._on_complete)
        btn_row.addWidget(self._complete_btn)

        form_layout.addLayout(btn_row)
        form_layout.addStretch(1)

        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    # ── State helpers ───────────────────────────────────────────────────

    def _load_current_config(self):
        """Pre-fill the workspace path from the current value."""
        if self._workspace_path:
            self._ws_input.setText(self._workspace_path)

    def _on_browse(self):
        """Open a folder picker and update the workspace path."""
        folder = QFileDialog.getExistingDirectory(
            self, "选择工作区目录", self._ws_input.text() or str(Path.home()),
        )
        if folder:
            self._ws_input.setText(folder)

    def _on_complete(self):
        """Validate the workspace path and emit setup_completed."""
        path = self._ws_input.text().strip()
        if not path:
            QMessageBox.warning(self, "路径为空", "请选择或输入一个工作区路径。")
            return

        # Create the workspace directory if it doesn't exist
        os.makedirs(path, exist_ok=True)

        # Create minimal config structure
        config_dir = os.path.join(path, "config")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "partner_config.json")
        if not os.path.exists(config_path):
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": "0.7.0",
                    "workspace": {"path": path},
                }, f, ensure_ascii=False, indent=2)

        self.setup_completed.emit(path)
