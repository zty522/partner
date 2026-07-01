"""Log viewer page - view instance logs with filtering and tail mode."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QMessageBox,
)

from partner.monitoring.instance_root import (
    resolve_global_config_path,
    resolve_instance_workspace,
    resolve_partner_root,
)

from ..theme import THEME, get_mono_font
from ..widgets import SectionHeader, AccentButton, fix_combo_wheel, COMBO_WHITE_VIEW_STYLE


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


LOG_LEVEL_COLORS = {
    "INFO": QColor(44, 62, 80),        # dark text
    "DEBUG": QColor(127, 140, 141),    # gray
    "WARNING": QColor(245, 166, 35),   # yellow
    "ERROR": QColor(229, 57, 53),      # red
    "CRITICAL": QColor(200, 30, 30),   # bright red
}


class LogDisplay(QPlainTextEdit):
    """Log display widget with level-based coloring and tail mode."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(get_mono_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setStyleSheet(
            f"background-color: {THEME.input_bg}; color: {THEME.txt}; "
            f"border: 1px solid {THEME.border}; border-radius: 6px; "
            f"padding: 8px;"
        )
        self._tail_mode = True
        self._filter_text = ""

    def append_log_line(self, line: str):
        """Add a log line with color based on log level."""
        if self._filter_text and self._filter_text.lower() not in line.lower():
            return

        # Determine color from log level
        color = LOG_LEVEL_COLORS.get("INFO")
        for level, c in LOG_LEVEL_COLORS.items():
            if f"[{level}]" in line or f" | {level} |" in line or f" - {level} " in line:
                color = c
                break

        QPlainTextEdit.setTextColor(self, color)
        self.appendPlainText(line)

        if self._tail_mode:
            scrollbar = self.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def set_filter(self, text: str):
        self._filter_text = text

    def set_tail_mode(self, enabled: bool):
        self._tail_mode = enabled


class LogsPage(QWidget):
    """Log viewer page."""

    def __init__(self, parent: QWidget | None = None, workspace_path: str = ""):
        super().__init__(parent)
        self._workspace = workspace_path
        self._current_log_path: str = ""
        self._file_info: QLabel = QLabel("未选择日志文件")
        self._line_count: QLabel = QLabel("行数: 0")
        self._log_display: LogDisplay | None = None
        self._filter_input: QLineEdit | None = None
        self._file_selector: QComboBox | None = None
        self._instance_selector: QComboBox | None = None
        self._tail_check: QCheckBox | None = None
        self._refresh_btn: QPushButton | None = None
        self._clear_btn: QPushButton | None = None
        self._open_btn: QPushButton | None = None
        self._copy_btn: QPushButton | None = None
        self._empty_label: QLabel | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_log)
        self._build_ui()
        self._load_instances()
        self._refresh_timer.start(3000)  # Refresh every 3 seconds

    def set_workspace(self, path: str):
        """Update workspace path and reload data."""
        self._workspace = path
        self._load_instances()
        self._refresh_log()

    def _show_empty_state(self, message: str):
        """Show a centered placeholder message when no log data."""
        if self._log_display is not None:
            self._log_display.clear()
            self._log_display.appendPlainText(message)
        if self._file_info is not None:
            self._file_info.setText(message)
        if self._line_count is not None:
            self._line_count.setText("行数: 0")

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(12)

        title = QLabel("日志查看")
        title.setObjectName("title")
        main_layout.addWidget(title)

        # Empty state placeholder
        self._empty_label = QLabel("暂无日志记录")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color: {THEME.txt3}; font-size: 16px; padding: 60px;"
        )
        self._empty_label.setVisible(False)
        main_layout.addWidget(self._empty_label)

        # Controls row
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._instance_selector = QComboBox()
        self._instance_selector.setMinimumWidth(150)
        self._instance_selector.currentIndexChanged.connect(self._on_instance_changed)
        fix_combo_wheel(self._instance_selector)
        self._instance_selector.setStyleSheet(self._instance_selector.styleSheet() + COMBO_WHITE_VIEW_STYLE)
        controls.addWidget(QLabel("实例:"))
        controls.addWidget(self._instance_selector)

        self._file_selector = QComboBox()
        self._file_selector.setMinimumWidth(150)
        self._file_selector.currentIndexChanged.connect(self._on_file_changed)
        self._file_selector.addItems(["partner.log", "qq_bot.log", "world_model.log", "instance.log"])
        fix_combo_wheel(self._file_selector)
        self._file_selector.setStyleSheet(self._file_selector.styleSheet() + COMBO_WHITE_VIEW_STYLE)
        controls.addWidget(QLabel("日志文件:"))
        controls.addWidget(self._file_selector)

        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("过滤文本...")
        self._filter_input.setMaximumWidth(200)
        self._filter_input.textChanged.connect(self._on_filter_changed)
        controls.addWidget(self._filter_input)

        self._tail_check = QCheckBox("自动滚动")
        self._tail_check.setChecked(True)
        self._tail_check.stateChanged.connect(self._on_tail_changed)
        controls.addWidget(self._tail_check)

        controls.addStretch()

        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self._refresh_log)
        controls.addWidget(self._refresh_btn)

        self._clear_btn = QPushButton("清空")
        self._clear_btn.clicked.connect(self._clear_log)
        controls.addWidget(self._clear_btn)

        self._open_btn = QPushButton("打开文件")
        self._open_btn.clicked.connect(self._open_file)
        controls.addWidget(self._open_btn)

        self._copy_btn = QPushButton("复制")
        self._copy_btn.clicked.connect(self._copy_selected)
        controls.addWidget(self._copy_btn)

        main_layout.addLayout(controls)

        # Log display
        self._log_display = LogDisplay()
        main_layout.addWidget(self._log_display, 1)

        # Status bar
        status_bar = QHBoxLayout()
        self._file_info = QLabel("未选择日志文件")
        self._file_info.setStyleSheet(f"font-size: 11px; color: {THEME.txt3};")
        self._line_count = QLabel("行数: 0")
        self._line_count.setStyleSheet(f"font-size: 11px; color: {THEME.txt3};")

        status_bar.addWidget(self._file_info)
        status_bar.addStretch()
        status_bar.addWidget(self._line_count)
        main_layout.addLayout(status_bar)

    def _resolve_global_config_path(self) -> str:
        """Resolve global_config.json using workspace or fallback."""
        if self._workspace and os.path.exists(self._workspace):
            path = os.path.join(self._workspace, "config", "global_config.json")
            if os.path.exists(path):
                return path
        return str(resolve_global_config_path())

    def _resolve_instance_dir(self, instance_id: str) -> str:
        """Resolve instance directory using workspace or fallback."""
        if self._workspace and os.path.exists(self._workspace):
            inst_dir = os.path.join(self._workspace, "instances", instance_id)
            if os.path.exists(inst_dir):
                return inst_dir
        return str(resolve_instance_workspace(instance_id))

    def _load_instances(self):
        """Populate instance selector from global config or filesystem scan."""
        if self._instance_selector is None:
            return
        self._instance_selector.clear()

        if not self._workspace or not os.path.exists(self._workspace):
            self._show_empty_state("工作区未配置或路径不存在")
            return

        # Try config file first
        config_path = self._resolve_global_config_path()
        instances_found = {}

        if os.path.exists(config_path):
            config = _load_json(config_path)
            instances_found.update(config.get("instances", {}))

        # Fallback: scan instances directory
        if not instances_found:
            instances_dir = os.path.join(self._workspace, "instances")
            if os.path.exists(instances_dir):
                for entry in sorted(os.listdir(instances_dir)):
                    if os.path.isdir(os.path.join(instances_dir, entry)):
                        instances_found[entry] = True

        if not instances_found:
            self._show_empty_state("暂无实例或日志记录")
            return

        if self._empty_label:
            self._empty_label.setVisible(False)

        for inst_id in sorted(instances_found.keys()):
            self._instance_selector.addItem(inst_id, inst_id)

    def _resolve_log_path(self, instance_id: str, log_file: str) -> str:
        """Resolve the actual log file path for an instance."""
        inst_dir = self._resolve_instance_dir(instance_id)
        paths_to_try = []

        log_file_lower = log_file.lower()

        if log_file_lower == "partner.log":
            paths_to_try = [
                os.path.join(inst_dir, "state", "logs", "partner.log"),
                os.path.join(inst_dir, "state", "record", "partner.log"),
            ]
        elif log_file_lower == "qq_bot.log":
            paths_to_try = [
                os.path.join(inst_dir, "state", "logs", "qq_bot.log"),
                os.path.join(inst_dir, "state", "qq_bot.log"),
            ]
        elif log_file_lower == "world_model.log":
            paths_to_try = [
                os.path.join(inst_dir, "state", "logs", "world_model.log"),
            ]
        elif log_file_lower == "instance.log":
            paths_to_try = [
                os.path.join(inst_dir, "state", "record", "instance.log"),
                os.path.join(inst_dir, "state", "logs", "instance.log"),
            ]

        # Add fallback: any *.out files in instance root
        if not paths_to_try:
            paths_to_try = list(Path(inst_dir).glob("*.out")) or []

        for p in paths_to_try:
            if os.path.exists(p):
                return str(p)

        # Return the first candidate even if it doesn't exist
        return str(paths_to_try[0]) if paths_to_try else ""

    def _on_instance_changed(self, index: int):
        self._refresh_log()

    def _on_file_changed(self, index: int):
        self._refresh_log()

    def _on_filter_changed(self, text: str):
        self._log_display.set_filter(text)
        self._refresh_log()

    def _on_tail_changed(self, state: int):
        self._log_display.set_tail_mode(state == Qt.CheckState.Checked.value)

    def _refresh_log(self):
        """Reload current log file."""
        instance_id = self._instance_selector.currentData()
        log_file = self._file_selector.currentText()

        if not instance_id:
            self._file_info.setText("请选择实例")
            return

        path = self._resolve_log_path(instance_id, log_file)
        self._current_log_path = path

        if not os.path.exists(path):
            self._log_display.clear()
            self._file_info.setText(f"文件不存在: {path}")
            self._line_count.setText("行数: 0")
            self._log_display.append_log_line("暂无日志记录")
            return

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            self._log_display.clear()
            lines = content.splitlines()
            filter_text = self._filter_input.text().strip()

            for line in lines:
                if filter_text and filter_text.lower() not in line.lower():
                    continue
                self._log_display.append_log_line(line)

            self._file_info.setText(f"{path}  ({len(lines)} 行)")
            self._line_count.setText(f"显示: {self._log_display.blockCount()} 行")

            # Tail mode: auto-scroll
            if self._tail_check.isChecked():
                cursor = self._log_display.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                self._log_display.setTextCursor(cursor)

        except Exception as e:
            self._log_display.clear()
            self._log_display.append_log_line(f"读取日志失败: {e}")

    def _clear_log(self):
        """Clear the log display."""
        self._log_display.clear()
        self._line_count.setText("行数: 0")

    def _open_file(self):
        """Open the log file in the system file explorer."""
        if self._current_log_path and os.path.exists(self._current_log_path):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", self._current_log_path],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(self._current_log_path)])
        else:
            QMessageBox.information(self, "提示", "没有可打开的日志文件")

    def _copy_selected(self):
        """Copy selected text to clipboard."""
        self._log_display.copy()
