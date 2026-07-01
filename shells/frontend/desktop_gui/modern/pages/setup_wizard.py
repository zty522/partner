"""Setup Wizard page - simplified welcome page for first launch.

No longer configures workspace — user sets workspace inside the app.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class SetupWizardPage(QWidget):
    """Welcome page shown on first launch (no workspace config)."""

    setup_completed = Signal(str)

    def __init__(self, workspace_path: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        header = QLabel("🎉 欢迎使用 Partner！")
        header.setStyleSheet("font-size: 28px; font-weight: 700; color: #16202d;")
        layout.addWidget(header)

        subtitle = QLabel(
            "请先在「配置中心」-「工作区」中配置运行环境和实例，\n"
            "然后即可在「对话」页面开始使用。"
        )
        subtitle.setStyleSheet("font-size: 15px; color: #6b7788; margin-bottom: 12px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        form_layout = QVBoxLayout(content)
        form_layout.setSpacing(14)
        form_layout.setContentsMargins(0, 0, 0, 0)

        info_label = QLabel("首次使用请先完成以下步骤：\n\n"
                            "1. 打开「配置中心」-「LLM API」配置 API Key\n"
                            "2. 打开「配置中心」-「工作区」设置运行环境和实例\n"
                            "3. 返回「对话」页面开始交互")
        info_label.setStyleSheet("font-size: 14px; color: #374151; line-height: 1.6;")
        info_label.setWordWrap(True)
        form_layout.addWidget(info_label)

        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    def _load_current_config(self):
        pass
