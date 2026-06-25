# -*- coding: utf-8 -*-
"""Reusable widgets for the modern Partner GUI: buttons, bubbles, cards, dialogs."""

from __future__ import annotations

import os
import re as _re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .theme import THEME, get_default_font


# ---------------------------------------------------------------------------
# SectionHeader — styled section title
# ---------------------------------------------------------------------------


class SectionHeader(QFrame):
    """A styled section header label."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(text)
        label.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {THEME.txt}; background: transparent;"
        )
        layout.addWidget(label)
        layout.addStretch()
        self.setStyleSheet("background: transparent;")


# ---------------------------------------------------------------------------
# ClickableFrame — generic hover+click QFrame
# ---------------------------------------------------------------------------


class ClickableFrame(QFrame):
    """A QFrame that emits a clicked signal on left-click."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# AccentButton — gradient blue primary button
# ---------------------------------------------------------------------------


class AccentButton(QPushButton):
    """Primary action button with blue gradient."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setMinimumHeight(42)
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                color: white;
                border: none;
                border-radius: 10px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: bold;
                min-height: 42px;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent2}, stop:1 {THEME.accent_h});
            }}
            QPushButton:pressed {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent3}, stop:1 #2A5F8A);
            }}
            QPushButton:disabled {{
                background-color: {THEME.bg3};
                color: {THEME.txt3};
            }}
        """)


# ---------------------------------------------------------------------------
# ChatBubble — clickable message bubble with hover effect
# ---------------------------------------------------------------------------


class ChatBubble(QFrame):
    """A chat message bubble with rounded corners, hover highlight, and click to load pipeline."""

    def __init__(
        self,
        content: str,
        role: str = "user",
        timestamp: str = "",
        file_path: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("chat_bubble")
        self._role = role
        self._file_path = file_path

        is_user = role in ("user",)
        bg_color = THEME.chat_user if is_user else THEME.chat_bot
        hl_color = THEME.chat_user_hl if is_user else THEME.chat_bot_hl

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        if file_path:
            self._build_file_widget(layout, content)
        else:
            self.content_label = QLabel(content)
            self.content_label.setWordWrap(True)
            self.content_label.setStyleSheet(
                f"background: transparent; color: {THEME.txt}; font-size: 13px;"
            )
            self.content_label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            self.content_label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(self.content_label)

        self.timestamp_label = QLabel(timestamp)
        self.timestamp_label.setStyleSheet(
            f"background: transparent; color: {THEME.txt3}; font-size: 10px;"
        )
        self.timestamp_label.setAlignment(
            Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(self.timestamp_label)

        self.setStyleSheet(f"""
            QFrame#chat_bubble {{
                background-color: {bg_color};
                border: 1px solid {THEME.border};
                border-radius: 14px;
            }}
            QFrame#chat_bubble:hover {{
                border: 1.5px solid {THEME.accent};
                background-color: {hl_color};
            }}
        """)
        self.setMaximumWidth(700)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)

    def mousePressEvent(self, event):
        """On left-click, navigate to pipeline snapshot if available, or show live plan."""
        from PySide6.QtWidgets import QWidget
        p = self.parent()
        while p is not None:
            if hasattr(p, '_load_pipeline_snapshot'):
                pipeline_path = getattr(self, '_pipeline_path', '')
                p._load_pipeline_snapshot(pipeline_path)
                break
            p = p.parent() if isinstance(p, QWidget) else None
        super().mousePressEvent(event)

    def _build_file_widget(self, layout: QVBoxLayout, content: str):
        """Build a clickable file widget for file references."""
        fname = content.split("\n")[0].strip()
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        file_frame = QFrame()
        file_frame.setObjectName("file_card")
        file_frame.setStyleSheet(f"""
            QFrame#file_card {{
                background-color: {THEME.bg3};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                padding: 8px;
            }}
            QFrame#file_card:hover {{
                background-color: {THEME.card_hl};
                border-color: {THEME.accent};
            }}
        """)
        file_layout = QHBoxLayout(file_frame)
        file_layout.setContentsMargins(4, 4, 4, 4)
        file_layout.setSpacing(8)

        icon_label = QLabel("📄")
        icon_label.setStyleSheet("font-size: 20px; background: transparent;")
        file_layout.addWidget(icon_label)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        name_label = QLabel(fname)
        name_label.setStyleSheet(f"font-size: 11px; color: {THEME.txt}; background: transparent;")
        name_label.setToolTip(self._file_path)
        info_layout.addWidget(name_label)

        size_str = ""
        if self._file_path:
            try:
                size = os.path.getsize(self._file_path)
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1048576:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size / 1048576:.1f} MB"
            except Exception:
                pass
        if size_str:
            size_label = QLabel(size_str)
            size_label.setStyleSheet(f"font-size: 9px; color: {THEME.txt3}; background: transparent;")
            info_layout.addWidget(size_label)

        file_layout.addLayout(info_layout, 1)

        open_btn = QPushButton("打开")
        open_btn.setFixedHeight(32)
        open_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.accent};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.card_hl}, stop:1 {THEME.bg2});
                border-color: {THEME.accent};
            }}
            QPushButton:pressed {{
                background-color: {THEME.bg3};
            }}
        """)
        if self._file_path:
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self._file_path)))
        else:
            open_btn.setEnabled(False)
        file_layout.addWidget(open_btn)

        layout.addWidget(file_frame)

        # File content text (below the card, if any)
        lines = content.split("\n")
        if len(lines) > 1:
            rest = "\n".join(lines[1:]).strip()
            if rest:
                text_label = QLabel(rest)
                text_label.setWordWrap(True)
                text_label.setStyleSheet(
                    f"background: transparent; color: {THEME.txt}; font-size: 13px;"
                )
                text_label.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
                )
                layout.addWidget(text_label)

    def set_content(self, content: str):
        """Update the bubble content text."""
        if hasattr(self, 'content_label') and self.content_label:
            self.content_label.setText(content)


# ---------------------------------------------------------------------------
# EventCard — status-aware event card
# ---------------------------------------------------------------------------


class EventCard(QFrame):
    """A card widget for displaying a single event with status coloring."""

    STATUS_COLORS = {
        "completed": THEME.green,
        "success": THEME.green,
        "running": THEME.accent,
        "pending": THEME.txt3,
        "failed": THEME.red,
        "error": THEME.red,
        "skipped": THEME.yellow,
        "planning": THEME.accent,
    }

    def __init__(self, title: str = "", status: str = "pending", detail: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("event_card")
        color = self.STATUS_COLORS.get(status, THEME.txt3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        header = QHBoxLayout()
        status_dot = QLabel("●")
        status_dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
        header.addWidget(status_dot)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {THEME.txt}; background: transparent;")
        title_label.setWordWrap(True)
        header.addWidget(title_label, 1)
        layout.addLayout(header)

        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setStyleSheet(f"font-size: 11px; color: {THEME.txt2}; background: transparent;")
            detail_label.setContentsMargins(14, 0, 0, 0)
            layout.addWidget(detail_label)

        self.setStyleSheet(f"""
            QFrame#event_card {{
                background-color: {THEME.card};
                border: 1px solid {color};
                border-left: 3px solid {color};
                border-radius: 8px;
            }}
        """)


# ---------------------------------------------------------------------------
# EventStepWidget — expandable step card for pipeline display
# ---------------------------------------------------------------------------


class EventStepWidget(QFrame):
    """A vertical step card with title, status, expandable detail for pipeline display."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("event_step")
        self._expanded = False

        self.setStyleSheet(f"""
            QFrame#event_step {{
                background-color: {THEME.card};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                margin: 0;
            }}
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Title button (clickable header)
        self._title_btn = QPushButton()
        self._title_btn.setFlat(True)
        self._title_btn.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                background: transparent;
                border: none;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 12px;
                color: {THEME.txt};
                max-width: 480px;
            }}
            QPushButton:hover {{
                background-color: {THEME.card_hl};
            }}
        """)
        self._title_btn.clicked.connect(self._toggle_expand)
        self._layout.addWidget(self._title_btn)

        # Detail section (collapsible)
        self._detail_widget = QWidget()
        self._detail_widget.setVisible(False)
        self._detail_widget.setStyleSheet("background: transparent;")
        self._detail_layout = QVBoxLayout(self._detail_widget)
        self._detail_layout.setContentsMargins(14, 0, 14, 10)
        self._detail_layout.setSpacing(4)
        self._layout.addWidget(self._detail_widget)

    def set_step(self, data: dict):
        """Update this step widget with step data."""
        number = data.get("number", 0)
        action = data.get("action", f"步骤 {number}")
        status = data.get("status", "pending")
        elapsed = data.get("elapsed", "")
        event_type = data.get("event_type", "")
        key = data.get("key", "")
        output = data.get("output", "")
        error = data.get("error", "")

        status_icon = {
            "completed": "✅", "success": "✅",
            "running": "🔄", "pending": "⏳",
            "failed": "❌", "error": "❌",
            "skipped": "⏭️",
        }.get(status, "⏳")

        time_str = f" ({elapsed})" if elapsed else ""
        # Truncate action text to prevent overflow
        action_short = action[:50] + ("..." if len(action) > 50 else "")
        type_str = f" [{event_type[:15]}]" if event_type else ""
        self._title_btn.setText(f"{status_icon} step{number}: {action_short}{type_str}{time_str}")

        # Rebuild detail
        for i in reversed(range(self._detail_layout.count())):
            item = self._detail_layout.takeAt(i)
            if item and item.widget():
                item.widget().deleteLater()

        if key:
            k_label = QLabel(f"输入: {key[:200]}")
            k_label.setWordWrap(True)
            k_label.setStyleSheet(f"font-size: 11px; color: {THEME.txt2}; background: transparent;")
            self._detail_layout.addWidget(k_label)

        if output:
            o_label = QLabel(f"输出: {output[:200]}")
            o_label.setWordWrap(True)
            o_label.setStyleSheet(f"font-size: 11px; color: {THEME.green}; background: transparent;")
            self._detail_layout.addWidget(o_label)

        if error:
            e_label = QLabel(f"错误: {error[:200]}")
            e_label.setWordWrap(True)
            e_label.setStyleSheet(f"font-size: 11px; color: {THEME.red}; background: transparent;")
            self._detail_layout.addWidget(e_label)

    def _toggle_expand(self):
        """Toggle the detail section."""
        self._expanded = not self._expanded
        self._detail_widget.setVisible(self._expanded)


# ---------------------------------------------------------------------------
# CollapsibleConfigGroup — advanced settings group
# ---------------------------------------------------------------------------


class CollapsibleConfigGroup(QFrame):
    """A collapsible group for advanced configuration sections."""

    def __init__(self, title: str = "高级设置", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("config_group")
        self._collapsed = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._toggle_btn = QPushButton(f"▶ {title}")
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                background: transparent;
                border: none;
                border-radius: 10px;
                padding: 10px 14px;
                font-size: 12px;
                font-weight: bold;
                color: {THEME.accent};
            }}
            QPushButton:hover {{
                background-color: {THEME.card_hl};
            }}
            QPushButton:pressed {{
                background-color: {THEME.bg3};
            }}
        """)
        self._toggle_btn.clicked.connect(self._toggle)
        self._layout.addWidget(self._toggle_btn)

        self._content = QWidget()
        self._content.setVisible(False)
        self._content.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(14, 4, 14, 10)
        self._content_layout.setSpacing(8)
        self._layout.addWidget(self._content)

        self.setStyleSheet(f"""
            QFrame#config_group {{
                background-color: {THEME.bg3};
                border: 1px solid {THEME.border};
                border-radius: 10px;
            }}
        """)

    def content_layout(self) -> QVBoxLayout:
        """Return the layout for adding child widgets."""
        return self._content_layout

    def set_content(self, widget: QWidget):
        """Set the content widget directly, replacing the default empty layout."""
        # Clear existing layout
        for i in reversed(range(self._content_layout.count())):
            item = self._content_layout.takeAt(i)
            if item and item.widget():
                item.widget().deleteLater()
        self._content_layout.addWidget(widget)

    def _toggle(self):
        """Toggle collapsed state."""
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        arrow = "▼" if not self._collapsed else "▶"
        self._toggle_btn.setText(f"{arrow} {self._toggle_btn.text()[1:].strip()}")

    def set_collapsed(self, collapsed: bool):
        """Set collapsed state explicitly."""
        self._collapsed = collapsed
        self._content.setVisible(not collapsed)
        arrow = "▶" if collapsed else "▼"
        text = self._toggle_btn.text()
        if " " in text:
            rest = text.split(" ", 1)[1]
            self._toggle_btn.setText(f"{arrow} {rest}")
