"""Dashboard page - main overview with instance status cards, active task pipeline, and more."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QProgressBar,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
)

from partner.monitoring.instance_root import (
    resolve_global_config_path,
    resolve_instance_workspace,
    resolve_instances_dir,
    resolve_partner_root,
)

from ..theme import THEME, get_default_font
from ..widgets import StatusCard, SectionHeader, EventPipelineWidget, AccentButton


def _is_pid_alive(pid: int) -> bool:
    """Check if a PID is alive on Linux/WSL."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


def _read_pid(path: str) -> int:
    try:
        return int(open(path).read().strip())
    except Exception:
        return 0


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _check_instance_running(inst_dir: str) -> str:
    """Return 'running', 'stopped', or 'error' for an instance."""
    inst_pid_path = os.path.join(inst_dir, "instance.pid")
    pid = _read_pid(inst_pid_path)
    if pid > 0 and _is_pid_alive(pid):
        return "running"

    heartbeat_path = os.path.join(inst_dir, "state", "heartbeat.json")
    hb = _load_json(heartbeat_path)
    ts = hb.get("last_heartbeat", "")
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            if (now - dt).total_seconds() < 180:
                return "running"
        except Exception:
            pass

    if pid > 0:
        return "error"

    return "stopped"


def _check_qq_running(inst_dir: str) -> bool:
    qq_pid_path = os.path.join(inst_dir, "state", "qq_bot.pid")
    pid = _read_pid(qq_pid_path)
    return pid > 0 and _is_pid_alive(pid)


def _get_heartbeat(inst_dir: str) -> str:
    hb = _load_json(os.path.join(inst_dir, "state", "heartbeat.json"))
    return hb.get("last_heartbeat", "")


def _start_instance(inst_dir: str, instance_id: str) -> tuple[bool, str]:
    """Start an instance using partner module."""
    log_path = os.path.join(inst_dir, "state/record", "instance.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "partner", "--instance-id", instance_id, "--workspace", inst_dir],
            stdout=open(log_path, "a", encoding="utf-8", errors="replace"),
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        with open(os.path.join(inst_dir, "instance.pid"), "w") as f:
            f.write(str(proc.pid))
        return True, f"已启动 (PID {proc.pid})"
    except Exception as e:
        return False, str(e)


def _stop_instance(inst_dir: str) -> tuple[bool, str]:
    """Stop an instance."""
    inst_pid_path = os.path.join(inst_dir, "instance.pid")
    pid = _read_pid(inst_pid_path)
    if pid > 0:
        try:
            os.kill(pid, 15)  # SIGTERM
            return True, "已停止"
        except Exception as e:
            return False, str(e)
    return True, "未运行"


import sys


class DashboardPage(QWidget):
    """Dashboard overview page."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.setInterval(15000)  # 15 seconds

        self._cards: dict[str, StatusCard] = {}
        self._card_widgets: dict[str, QWidget] = {}
        self._pipelines: list[EventPipelineWidget] = []

        self._build_ui()
        self._refresh()
        self._refresh_timer.start()

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(20)

        # Title
        title = QLabel("仪表盘")
        title.setObjectName("title")
        main_layout.addWidget(title)

        # 1. Instance status cards (top row)
        info_label = QLabel("实例状态")
        info_label.setObjectName("section")
        main_layout.addWidget(info_label)

        self._cards_layout = QHBoxLayout()
        self._cards_layout.setSpacing(12)
        main_layout.addLayout(self._cards_layout)

        # 2. Active task / Event pipeline
        self._pipeline_header = SectionHeader("当前任务")
        main_layout.addWidget(self._pipeline_header)

        self._pipeline_container = QWidget()
        self._pipeline_container.setObjectName("card")
        self._pipeline_layout = QVBoxLayout(self._pipeline_container)
        self._pipeline_layout.setContentsMargins(16, 16, 16, 16)
        self._pipeline_layout.setSpacing(8)

        self._plan_title = QLabel("无活跃任务")
        self._plan_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {THEME.txt2};")
        self._plan_goal = QLabel("")
        self._plan_goal.setStyleSheet(f"font-size: 12px; color: {THEME.txt3};")
        self._plan_progress = QProgressBar()
        self._plan_progress.setVisible(False)
        self._plan_progress.setMaximum(100)

        self._pipeline_widget = EventPipelineWidget()
        self._pipeline_widget.setVisible(False)

        self._pipeline_layout.addWidget(self._plan_title)
        self._pipeline_layout.addWidget(self._plan_goal)
        self._pipeline_layout.addWidget(self._plan_progress)
        self._pipeline_layout.addWidget(self._pipeline_widget)
        self._pipeline_layout.addStretch()

        main_layout.addWidget(self._pipeline_container)

        # 3. World model status
        self._wm_header = SectionHeader("世界模型")
        main_layout.addWidget(self._wm_header)

        self._wm_container = QWidget()
        self._wm_container.setObjectName("card")
        wm_layout = QVBoxLayout(self._wm_container)
        wm_layout.setContentsMargins(16, 16, 16, 16)
        wm_layout.setSpacing(6)

        self._wm_status = QLabel("连接状态: 检测中...")
        self._wm_status.setStyleSheet(f"color: {THEME.txt2};")
        self._wm_score = QLabel("")
        self._wm_score.setStyleSheet(f"color: {THEME.txt3};")

        wm_layout.addWidget(self._wm_status)
        wm_layout.addWidget(self._wm_score)
        main_layout.addWidget(self._wm_container)

        # 4. Recent completed tasks
        self._history_header = SectionHeader("最近完成的任务")
        main_layout.addWidget(self._history_header)

        self._history_list = QListWidget()
        self._history_list.setMaximumHeight(200)
        self._history_list.setStyleSheet(
            f"background-color: {THEME.input_bg}; border: 1px solid {THEME.border}; border-radius: 6px;"
        )
        main_layout.addWidget(self._history_list)

        main_layout.addStretch()

        scroll.setWidget(container)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)

    def _refresh(self):
        """Refresh all dashboard data."""
        self._refresh_instance_cards()
        self._refresh_active_task()
        self._refresh_world_model()
        self._refresh_history()

    def _refresh_instance_cards(self):
        """Update instance status cards."""
        config_path = resolve_global_config_path()
        if not os.path.exists(config_path):
            return

        config = _load_json(str(config_path))
        instances = config.get("instances", {})

        # Build cards
        for inst_id, info in instances.items():
            inst_dir = str(resolve_instance_workspace(inst_id))
            if not os.path.exists(inst_dir):
                inst_dir = info.get("working_dir", "")

            if inst_id in self._cards:
                card = self._cards[inst_id]
            else:
                card = StatusCard(f"实例 {inst_id}", "检测中", THEME.accent)
                self._cards[inst_id] = card
                self._cards_layout.addWidget(card)
                # Add Start/Stop buttons
                btn_layout = QHBoxLayout()
                start_btn = AccentButton("启动")
                stop_btn = QPushButton("停止")
                stop_btn.setObjectName("danger")
                btn_widget = QWidget()
                btn_widget.setLayout(btn_layout)
                btn_layout.addWidget(start_btn)
                btn_layout.addWidget(stop_btn)

                card_id = inst_id
                start_btn.clicked.connect(lambda checked=False, cid=card_id: self._on_start(cid))
                stop_btn.clicked.connect(lambda checked=False, cid=card_id: self._on_stop(cid))

            status = _check_instance_running(inst_dir)
            qq_running = _check_qq_running(inst_dir)
            hb = _get_heartbeat(inst_dir)

            status_map = {
                "running": ("运行中", THEME.green),
                "stopped": ("已停止", THEME.txt3),
                "error": ("异常", THEME.red),
            }
            status_text, status_color = status_map.get(status, ("未知", THEME.yellow))
            card.set_value(status_text)
            card.set_accent(status_color)

            qq_status = "QQ: 运行中" if qq_running else "QQ: 已停止"
            qq_color = THEME.green if qq_running else THEME.txt3
            card.set_status(qq_status, qq_color)

            if hb:
                try:
                    dt = datetime.fromisoformat(hb)
                    hb_str = dt.strftime("%H:%M:%S")
                    card.setToolTip(f"最后心跳: {hb_str}")
                except Exception:
                    pass

    def _on_start(self, instance_id: str):
        inst_dir = str(resolve_instance_workspace(instance_id))
        success, msg = _start_instance(inst_dir, instance_id)
        if success:
            self._refresh()
        else:
            QMessageBox.warning(self, "启动失败", msg)

    def _on_stop(self, instance_id: str):
        inst_dir = str(resolve_instance_workspace(instance_id))
        success, msg = _stop_instance(inst_dir)
        if success:
            self._refresh()
        else:
            QMessageBox.warning(self, "停止失败", msg)

    def _refresh_active_task(self):
        """Check active_plan.json for current task."""
        config_path = resolve_global_config_path()
        config = _load_json(str(config_path))
        default_id = config.get("default_instance", "")
        instances = config.get("instances", {})
        if not default_id or default_id not in instances:
            self._plan_title.setText("无活跃任务 - 未设置默认实例")
            self._pipeline_widget.setVisible(False)
            self._plan_progress.setVisible(False)
            return

        inst_dir = str(resolve_instance_workspace(default_id))
        active_plan_path = os.path.join(inst_dir, "state", "active_plan.json")
        if not os.path.exists(active_plan_path):
            self._plan_title.setText("无活跃任务")
            self._pipeline_widget.setVisible(False)
            self._plan_progress.setVisible(False)
            return

        plan = _load_json(active_plan_path)
        if not plan or plan.get("status") == "idle" or plan == {}:
            self._plan_title.setText("无活跃任务")
            self._pipeline_widget.setVisible(False)
            self._plan_progress.setVisible(False)
            return

        self._plan_title.setText(plan.get("title", plan.get("goal", "任务进行中"))[:50])
        goal = plan.get("goal", "")
        self._plan_goal.setText(f"目标: {goal[:80]}" if goal else "")

        # Progress
        events = plan.get("events", []) or plan.get("steps", [])
        if events:
            total = len(events)
            completed = sum(1 for e in events if e.get("status") == "completed" or e.get("status") == "success")
            pct = int(completed / total * 100) if total > 0 else 0
            self._plan_progress.setValue(pct)
            self._plan_progress.setVisible(True)
            self._plan_progress.setFormat(f"{pct}% ({completed}/{total})")

            # Build pipeline steps
            steps = []
            for i, ev in enumerate(events):
                status = ev.get("status", "pending")
                if status == "success":
                    status = "completed"
                elapsed = ev.get("elapsed", "")
                steps.append({
                    "number": i + 1,
                    "action": ev.get("action", ev.get("type", f"步骤 {i+1}")),
                    "status": status,
                    "elapsed": elapsed,
                })
            self._pipeline_widget.set_steps(steps)
            self._pipeline_widget.setVisible(True)
        else:
            self._plan_progress.setVisible(False)
            self._pipeline_widget.setVisible(False)

    def _refresh_world_model(self):
        """Check world model status."""
        ws_root = str(resolve_partner_root())
        wm_config_path = os.path.join(ws_root, "config", "world_model.yaml")
        self._wm_status.setText("世界模型: 未配置")
        self._wm_status.setStyleSheet(f"color: {THEME.txt3};")
        self._wm_score.setText("")

        # Try to read world_model.yaml
        try:
            if os.path.exists(wm_config_path):
                with open(wm_config_path, "r") as f:
                    content = f.read()
                if content.strip():
                    self._wm_status.setText("世界模型: 已配置")
                    self._wm_status.setStyleSheet(f"color: {THEME.green};")
        except Exception:
            pass

        # Try health endpoint
        import socket as sock_mod
        try:
            s = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("127.0.0.1", 8192))
            s.send(b"health\n")
            data = s.recv(1024).decode("utf-8", "replace").strip()
            s.close()
            self._wm_status.setText("世界模型: 连接正常")
            self._wm_status.setStyleSheet(f"color: {THEME.green};")
            if data:
                self._wm_score.setText(f"响应: {data[:100]}")
        except Exception:
            pass

    def _refresh_history(self):
        """Load recent completed tasks from active_plan history or journal."""
        self._history_list.clear()

        config_path = str(resolve_global_config_path())
        config = _load_json(config_path)
        default_id = config.get("default_instance", "")

        if not default_id:
            return

        inst_dir = str(resolve_instance_workspace(default_id))
        journal_path = os.path.join(inst_dir, "state", "journal.jsonl")
        if not os.path.exists(journal_path):
            return

        plans = []
        try:
            with open(journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            if "plan" in entry or entry.get("type") == "plan_completed":
                                plans.append(entry)
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass

        for entry in plans[-10:]:  # Last 10
            plan = entry.get("plan", entry)
            title = plan.get("title", plan.get("goal", "任务"))[:40]
            ts = entry.get("completed_at", entry.get("created_at", ""))
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    ts = dt.strftime("%m-%d %H:%M")
                except Exception:
                    ts = ts[:16]
            item_text = f"{title}  [{ts}]" if ts else title
            item = QListWidgetItem(item_text)
            self._history_list.addItem(item)
