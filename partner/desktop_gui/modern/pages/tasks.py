"""Task management page - create, view, and manage tasks."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from partner.instance_root import (
    resolve_global_config_path,
    resolve_instance_workspace,
    resolve_partner_root,
)
from partner.config import load_partner_config_data

from ..theme import THEME
from ..widgets import SectionHeader, AccentButton, EventPipelineWidget


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class TasksPage(QWidget):
    """Task management page."""

    def __init__(self, parent: QWidget | None = None, workspace_path: str = ""):
        super().__init__(parent)
        self._workspace = workspace_path
        self._task_items: list = []
        self._selected_plan_path: str = ""
        self._selected_instance_id: str = ""
        self._empty_label: QLabel | None = None
        self._build_ui()
        self._refresh_tasks()

        # Poll for task list updates every 5 seconds
        self._list_timer = QTimer(self)
        self._list_timer.timeout.connect(self._refresh_tasks)
        self._list_timer.start(5000)

        # Poll for active plan detail updates every 3 seconds
        self._detail_timer = QTimer(self)
        self._detail_timer.timeout.connect(self._poll_active_plan)
        self._detail_timer.start(3000)

    def set_workspace(self, path: str):
        """Update workspace path and reload data."""
        self._workspace = path
        self._refresh_tasks()

    def _show_empty_state(self, message: str):
        """Show a centered placeholder message when no data is available."""
        self._active_task_list.clear()
        self._history_task_list.clear()
        self._task_items.clear()
        if self._empty_label:
            self._empty_label.setText(message)
            self._empty_label.setVisible(True)

    def _refresh_visibility(self):
        """Toggle empty label visibility based on task items."""
        has_items = len(self._task_items) > 0 or self._active_task_list.count() > 0 or self._history_task_list.count() > 0
        if self._empty_label:
            self._empty_label.setVisible(not has_items)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # Title
        title = QLabel("任务管理")
        title.setObjectName("title")
        main_layout.addWidget(title)

        # ── Create new task section ──
        create_header = SectionHeader("创建新任务")
        main_layout.addWidget(create_header)

        create_frame = QFrame()
        create_frame.setObjectName("card")
        create_layout = QVBoxLayout(create_frame)
        create_layout.setContentsMargins(16, 16, 16, 16)
        create_layout.setSpacing(8)

        input_row = QHBoxLayout()

        self._msg_input = QLineEdit()
        self._msg_input.setPlaceholderText("输入任务描述...")
        self._msg_input.setMinimumHeight(40)

        self._instance_selector = QComboBox()
        self._instance_selector.setMinimumWidth(150)

        self._submit_btn = AccentButton("提交任务")
        self._submit_btn.clicked.connect(self._on_submit_task)

        input_row.addWidget(self._msg_input, 1)
        input_row.addWidget(self._instance_selector)
        input_row.addWidget(self._submit_btn)

        create_layout.addLayout(input_row)
        main_layout.addWidget(create_frame)

        # ── Task list and detail (split pane) ──
        task_header = SectionHeader("任务列表")
        main_layout.addWidget(task_header)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: task list
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        self._active_task_list = QListWidget()
        self._active_task_list.itemClicked.connect(self._on_task_selected)
        self._active_task_list.setMinimumWidth(250)

        self._history_task_list = QListWidget()
        self._history_task_list.itemClicked.connect(self._on_task_selected)

        tabs.addTab(self._active_task_list, "活跃")
        tabs.addTab(self._history_task_list, "历史")
        left_layout.addWidget(tabs)

        # Empty state placeholder
        self._empty_label = QLabel("暂无活跃任务")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color: {THEME.txt3}; font-size: 16px; padding: 40px;"
        )
        self._empty_label.setVisible(False)
        left_layout.addWidget(self._empty_label)

        splitter.addWidget(left_widget)

        # Right: task detail
        right_widget = QScrollArea()
        right_widget.setWidgetResizable(True)
        right_widget.setFrameShape(QFrame.Shape.NoFrame)

        self._detail_container = QWidget()
        self._detail_layout = QVBoxLayout(self._detail_container)
        self._detail_layout.setContentsMargins(16, 16, 16, 16)
        self._detail_layout.setSpacing(8)

        self._detail_title = QLabel("选择一个任务查看详情")
        self._detail_title.setObjectName("section")
        self._detail_title.setStyleSheet(f"color: {THEME.txt2};")

        self._detail_goal = QLabel("")
        self._detail_goal.setStyleSheet(f"color: {THEME.txt3};")

        self._detail_status = QLabel("")
        self._detail_event_pipeline = EventPipelineWidget()
        self._detail_event_pipeline.setVisible(False)

        # Log output
        self._detail_log = QTextEdit()
        self._detail_log.setReadOnly(True)
        self._detail_log.setMaximumHeight(200)
        self._detail_log.setStyleSheet(
            f"background-color: {THEME.input_bg}; color: {THEME.txt}; "
            f"font-family: monospace; font-size: 12px; "
            f"border: 1px solid {THEME.border}; border-radius: 6px;"
        )

        # Control buttons
        control_layout = QHBoxLayout()
        self._pause_btn = QPushButton("暂停")
        self._resume_btn = QPushButton("继续")
        self._resume_btn.setObjectName("success")
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("danger")
        self._pause_btn.setVisible(False)
        self._resume_btn.setVisible(False)
        self._cancel_btn.setVisible(False)

        control_layout.addWidget(self._pause_btn)
        control_layout.addWidget(self._resume_btn)
        control_layout.addWidget(self._cancel_btn)
        control_layout.addStretch()

        self._detail_layout.addWidget(self._detail_title)
        self._detail_layout.addWidget(self._detail_goal)
        self._detail_layout.addWidget(self._detail_status)
        self._detail_layout.addWidget(self._detail_event_pipeline)
        self._detail_layout.addWidget(QLabel("日志输出:"))
        self._detail_layout.addWidget(self._detail_log)
        self._detail_layout.addLayout(control_layout)
        self._detail_layout.addStretch()

        right_widget.setWidget(self._detail_container)
        splitter.addWidget(right_widget)
        splitter.setSizes([300, 500])

        main_layout.addWidget(splitter)

    def _load_instances(self):
        """Populate instance selector dropdown."""
        self._instance_selector.clear()
        config_path = self._resolve_global_config_path()
        config = _load_json(str(config_path))
        instances = config.get("instances", {})
        for inst_id in instances:
            self._instance_selector.addItem(inst_id, inst_id)

    def _resolve_global_config_path(self) -> str:
        """Resolve global_config.json using workspace or fallback."""
        if self._workspace and os.path.exists(self._workspace):
            path = os.path.join(self._workspace, "config", "global_config.json")
            if os.path.exists(path):
                return path
        # Fallback to standard resolution
        return str(resolve_global_config_path())

    def _resolve_instance_dir(self, instance_id: str) -> str:
        """Resolve instance directory using workspace or fallback."""
        if self._workspace and os.path.exists(self._workspace):
            inst_dir = os.path.join(self._workspace, "instances", instance_id)
            if os.path.exists(inst_dir):
                return inst_dir
        return str(resolve_instance_workspace(instance_id))

    def _on_submit_task(self):
        """Submit a new task to the selected instance."""
        text = self._msg_input.text().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请输入任务描述")
            return

        instance_id = self._instance_selector.currentData()
        if not instance_id:
            QMessageBox.warning(self, "提示", "请选择目标实例")
            return

        inst_dir = self._resolve_instance_dir(instance_id)
        inbox_path = os.path.join(inst_dir, "state", "desktop_inbox.jsonl")
        os.makedirs(os.path.dirname(inbox_path), exist_ok=True)

        event = {
            "id": f"desktop_{uuid.uuid4().hex[:12]}",
            "message_id": f"gui_{uuid.uuid4().hex[:12]}",
            "text": text,
            "display_text": text,
            "source": "desktop",
            "channel": "desktop",
            "sender_id": "desktop_gui",
            "sender_name": "桌面端",
            "attachments": [],
            "created_at": datetime.now().isoformat(),
        }

        try:
            with open(inbox_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._msg_input.clear()
            self._refresh_tasks()
        except Exception as e:
            QMessageBox.critical(self, "提交失败", str(e))

    def _refresh_tasks(self):
        """Reload task lists from instance states."""
        # Check workspace
        if not self._workspace or not os.path.exists(self._workspace):
            self._show_empty_state("工作区未配置或路径不存在")
            return

        self._load_instances()
        self._active_task_list.clear()
        self._history_task_list.clear()
        self._task_items.clear()

        config_path = self._resolve_global_config_path()
        config = _load_json(config_path)
        instances = config.get("instances", {})

        # If no instances in config, try scanning instances directory
        if not instances:
            instances_dir = os.path.join(self._workspace, "instances")
            if os.path.exists(instances_dir):
                for entry in sorted(os.listdir(instances_dir)):
                    inst_path = os.path.join(instances_dir, entry)
                    if os.path.isdir(inst_path):
                        instances[entry] = {"working_dir": inst_path}

        for inst_id in instances:
            inst_dir = self._resolve_instance_dir(inst_id)

            # Check for active plan
            active_plan_path = os.path.join(inst_dir, "state", "active_plan.json")
            if os.path.exists(active_plan_path):
                plan = _load_json(active_plan_path)
                if plan and plan.get("status") and plan.get("status") != "idle" and plan != {}:
                    # Skip truly stale plans: status=active with all phases pending
                    # AND plan was created >30 min ago (new plans should show even
                    # if not yet executed — the bot may be about to run them).
                    created_str = plan.get("created_at", "")
                    phases = plan.get("phases", []) or plan.get("events", []) or plan.get("steps", [])
                    is_stale = False
                    if created_str and phases and plan.get("status") == "active" and plan.get("current_phase_index", 0) == 0:
                        try:
                            created_dt = datetime.fromisoformat(created_str)
                            age = datetime.now(timezone.utc).astimezone() - created_dt if created_dt.tzinfo else datetime.now() - created_dt
                            if age > timedelta(minutes=30):
                                all_pending = all(p.get("status", "pending") == "pending" for p in phases)
                                if all_pending:
                                    is_stale = True
                        except Exception:
                            pass
                    if not is_stale:
                        title = plan.get("title", plan.get("goal", f"实例 {inst_id} 任务"))[:50]
                        item = QListWidgetItem(f"[{inst_id}] {title}")
                        item.setData(Qt.ItemDataRole.UserRole, {
                            "instance_id": inst_id,
                            "source": "active_plan",
                            "path": active_plan_path,
                        })
                        self._active_task_list.addItem(item)
                        self._task_items.append(item)

            # Check journal for history
            journal_path = os.path.join(inst_dir, "state", "journal.jsonl")
            if os.path.exists(journal_path):
                try:
                    with open(journal_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    entry = json.loads(line)
                                    plan = entry.get("plan", entry)
                                    title = plan.get("title", plan.get("goal", ""))[:50]
                                    if title:
                                        ts = entry.get("completed_at", entry.get("created_at", ""))
                                        item = QListWidgetItem(f"[{inst_id}] {title}")
                                        item.setData(Qt.ItemDataRole.UserRole, {
                                            "instance_id": inst_id,
                                            "source": "journal",
                                            "data": entry,
                                        })
                                        self._history_task_list.addItem(item)
                                except json.JSONDecodeError:
                                    pass
                except Exception:
                    pass

            # Also check state/tasks/ directory for task result files
            tasks_dir = os.path.join(inst_dir, "state", "tasks")
            if os.path.exists(tasks_dir):
                try:
                    for fname in sorted(os.listdir(tasks_dir)):
                        if fname.endswith(".json"):
                            task_path = os.path.join(tasks_dir, fname)
                            task_data = _load_json(task_path)
                            if task_data:
                                title = task_data.get("title", task_data.get("goal", fname))[:50]
                                item = QListWidgetItem(f"[{inst_id}] {title}")
                                item.setData(Qt.ItemDataRole.UserRole, {
                                    "instance_id": inst_id,
                                    "source": "active_plan",
                                    "path": task_path,
                                })
                                self._history_task_list.addItem(item)
                                self._task_items.append(item)
                except Exception:
                    pass

        # Show empty state if no tasks found
        if not self._task_items and self._active_task_list.count() == 0 and self._history_task_list.count() == 0:
            self._show_empty_state("暂无活跃任务")
        elif self._empty_label:
            self._empty_label.setVisible(False)

    def _on_task_selected(self, item: QListWidgetItem):
        """Show task details when clicked."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        instance_id = data.get("instance_id", "")
        source = data.get("source", "")

        if source == "active_plan":
            plan_path = data.get("path", "")
            self._selected_plan_path = plan_path
            self._selected_instance_id = instance_id
            plan = _load_json(plan_path)
            self._show_plan_detail(plan, instance_id)
        elif source == "journal":
            entry = data.get("data", {})
            plan = entry.get("plan", entry)
            self._selected_plan_path = ""
            self._selected_instance_id = instance_id
            self._show_plan_detail(plan, instance_id)

    def _poll_active_plan(self):
        """Poll the currently selected active plan for live updates."""
        if not self._selected_plan_path or not os.path.exists(self._selected_plan_path):
            return
        plan = _load_json(self._selected_plan_path)
        if plan and plan.get("status") and plan.get("status") != "idle" and plan != {}:
            self._show_plan_detail(plan, self._selected_instance_id)

    def _show_plan_detail(self, plan: dict, instance_id: str):
        """Display plan details in the detail panel."""
        title = plan.get("title", plan.get("goal", "任务详情"))[:60]
        goal = plan.get("goal", "")
        status = plan.get("status", "unknown")

        self._detail_title.setText(f"[{instance_id}] {title}")
        self._detail_goal.setText(f"目标: {goal}" if goal else "")
        status_colors = {
            "running": THEME.accent,
            "completed": THEME.green,
            "failed": THEME.red,
            "pending": THEME.yellow,
            "idle": THEME.txt3,
        }
        self._detail_status.setText(f"状态: {status}")
        self._detail_status.setStyleSheet(
            f"color: {status_colors.get(status, THEME.txt2)}; font-weight: bold;"
        )

        # Event pipeline - extract from phases or events
        events = plan.get("events", []) or plan.get("phases", []) or plan.get("steps", [])
        if events:
            steps = []
            for i, ev in enumerate(events):
                s = ev.get("status", "pending")
                if s == "success":
                    s = "completed"
                steps.append({
                    "number": i + 1,
                    "action": ev.get("action", ev.get("type", f"步骤 {i+1}")),
                    "status": s,
                    "elapsed": ev.get("elapsed", ""),
                })
            self._detail_event_pipeline.set_steps(steps)
            self._detail_event_pipeline.setVisible(True)
        else:
            self._detail_event_pipeline.setVisible(False)

        # Log output
        log_text = plan.get("log", plan.get("output", ""))
        if isinstance(log_text, list):
            log_text = "\n".join(log_text)
        self._detail_log.setText(log_text[:2000] if log_text else "暂无日志")

        # Show control buttons for active tasks
        self._pause_btn.setVisible(status == "running")
        self._resume_btn.setVisible(status == "paused")
        self._cancel_btn.setVisible(status in ("running", "paused"))
