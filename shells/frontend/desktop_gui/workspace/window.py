"""Modern conversation-first Partner desktop workspace."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QUrl

from partner.application import ApplicationReadModel, PartnerApplicationService
from .settings import SettingsDialog
from .theme import LIGHT, stylesheet


def label(text: str, role: str = "") -> QLabel:
    value = QLabel(text); value.setWordWrap(True)
    if role: value.setObjectName(role)
    return value


def clear(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget(): item.widget().deleteLater()


class ClickCard(QFrame):
    clicked = Signal(object)

    def __init__(self, payload: Any, selected: bool = False):
        super().__init__(); self.payload = payload
        self.setObjectName("Selected" if selected else "Card")

    def mousePressEvent(self, event):  # noqa: N802
        self.clicked.emit(self.payload); super().mousePressEvent(event)


class ProjectItem(ClickCard):
    def __init__(self, row: dict[str, Any], selected: bool):
        super().__init__(row, selected); box = QVBoxLayout(self); box.setContentsMargins(13, 11, 13, 11)
        top = QHBoxLayout(); top.addWidget(label(str(row.get("title") or "全部项目"), "Heading")); top.addStretch()
        status = str(row.get("status") or "ready")
        dot = label("●", "Meta"); dot.setStyleSheet("color:#16A36A" if status in {"running", "dispatched"} else "color:#98A2B3")
        top.addWidget(dot); box.addLayout(top)
        subtitle = "统一工作空间" if not row.get("project_id") else f"{row.get('specialist')} · {status}"
        box.addWidget(label(subtitle, "Meta"))


class UpdateCard(ClickCard):
    def __init__(self, update):
        super().__init__(update); box = QVBoxLayout(self); box.setContentsMargins(16, 14, 16, 14); box.setSpacing(7)
        title = {"project":"项目", "active_learning":"外部学习",
                 "self_evolution":"系统改进", "conversation":"对话"}.get(update.line, update.line)
        color = {"project":LIGHT["project"], "active_learning":LIGHT["learning"],
                 "self_evolution":LIGHT["evolution"]}.get(update.line, LIGHT["muted"])
        top = QHBoxLayout(); badge = label(title, "Meta"); badge.setStyleSheet(f"color:{color};font-weight:700")
        top.addWidget(badge); top.addStretch(); top.addWidget(label(update.created_at.replace("T", " ")[:16], "Meta")); box.addLayout(top)
        box.addWidget(label(update.subject, "Heading"))
        box.addWidget(label(update.finding or update.action, "Muted"))
        if update.next: box.addWidget(label("接下来：" + update.next, "Meta"))


class PartnerWorkspaceWindow(QMainWindow):
    def __init__(self, workspace_path: str, app=None):
        super().__init__(); self.workspace = str(Path(workspace_path).resolve())
        self.service = PartnerApplicationService(self.workspace)
        self.read_model = ApplicationReadModel(self.workspace)
        self.project_id = ""; self.attachments: list[str] = []
        self.selected_job_id = ""
        self.setWindowTitle("Partner 工作空间")
        self.setMinimumSize(940, 620)
        self._set_comfortable_initial_geometry()
        self.setStyleSheet(stylesheet()); self._build(); self.refresh()
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(1800)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.composer.setFocus)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.toggle_inspector)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.show_settings)

    def _set_comfortable_initial_geometry(self) -> None:
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        width = min(1180, int(available.width() * 0.82)) if available else 1180
        height = min(760, int(available.height() * 0.82)) if available else 760
        width, height = max(940, width), max(620, height)
        self.resize(width, height)
        if available:
            self.move(available.center() - self.rect().center())

    def _build(self) -> None:
        canvas = QWidget(); canvas.setObjectName("Canvas"); self.setCentralWidget(canvas)
        outer = QHBoxLayout(canvas); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        sidebar = QFrame(); sidebar.setObjectName("Sidebar"); sidebar.setFixedWidth(248)
        side = QVBoxLayout(sidebar); side.setContentsMargins(16, 20, 16, 16); side.setSpacing(11)
        self.brand = label(self._config_name(), "Brand")
        side.addWidget(self.brand); side.addWidget(label("长期研究与项目工作空间", "Muted"))
        side.addSpacing(8); side.addWidget(label("项目", "Heading"))
        project_scroll = QScrollArea(); project_scroll.setWidgetResizable(True)
        holder = QWidget(); self.project_layout = QVBoxLayout(holder); self.project_layout.setContentsMargins(0,0,0,0); self.project_layout.setSpacing(8)
        project_scroll.setWidget(holder); side.addWidget(project_scroll, 1)
        side.addWidget(label("后台工作不会阻塞你继续对话。", "Meta")); outer.addWidget(sidebar)

        centre = QWidget(); body = QVBoxLayout(centre); body.setContentsMargins(24, 20, 24, 16); body.setSpacing(12)
        head = QHBoxLayout(); titles = QVBoxLayout(); titles.setSpacing(2)
        self.title = label("所有项目", "Title"); self.subtitle = label("项目、外部学习与系统改进保持分线呈现", "Muted")
        titles.addWidget(self.title); titles.addWidget(self.subtitle); head.addLayout(titles); head.addStretch()
        jobs = QPushButton("查看后台工作"); jobs.setObjectName("Secondary"); jobs.clicked.connect(self.show_jobs); head.addWidget(jobs)
        details = QPushButton("证据详情"); details.setObjectName("Secondary"); details.clicked.connect(self.toggle_inspector); head.addWidget(details)
        settings = QPushButton("设置"); settings.setObjectName("Quiet"); settings.setToolTip("基础设置（Ctrl+,）"); settings.clicked.connect(self.show_settings); head.addWidget(settings)
        body.addLayout(head)
        self.feed_scroll = QScrollArea(); self.feed_scroll.setWidgetResizable(True)
        feed = QWidget(); self.feed_layout = QVBoxLayout(feed); self.feed_layout.setContentsMargins(0,5,5,5); self.feed_layout.setSpacing(10)
        self.feed_scroll.setWidget(feed); body.addWidget(self.feed_scroll, 1)
        compose = QFrame(); compose.setObjectName("Card"); cbox = QVBoxLayout(compose); cbox.setContentsMargins(14,12,14,12); cbox.setSpacing(8)
        self.composer = QTextEdit(); self.composer.setPlaceholderText("直接说你想做什么，例如：继续分子生成项目，优先验证上轮最不确定的假设")
        self.composer.setFixedHeight(82); cbox.addWidget(self.composer)
        actions = QHBoxLayout(); self.attach_label = label("Ctrl+K 聚焦输入", "Meta"); actions.addWidget(self.attach_label); actions.addStretch()
        attach = QPushButton("添加附件"); attach.clicked.connect(self.add_attachment); actions.addWidget(attach)
        send = QPushButton("发送"); send.setObjectName("Primary"); send.clicked.connect(self.submit); actions.addWidget(send); cbox.addLayout(actions)
        body.addWidget(compose); outer.addWidget(centre, 1)

        self.inspector = QFrame(); self.inspector.setObjectName("Inspector"); self.inspector.setFixedWidth(322)
        inspect = QVBoxLayout(self.inspector); inspect.setContentsMargins(18,20,18,16); inspect.setSpacing(10)
        inspect.addWidget(label("上下文与证据", "Brand")); self.inspect_title = label("选择一条进展", "Heading"); inspect.addWidget(self.inspect_title)
        self.inspect_text = QTextEdit(); self.inspect_text.setReadOnly(True); inspect.addWidget(self.inspect_text, 1)
        self.open_artifact = QPushButton("打开所选产物"); self.open_artifact.setObjectName("Secondary"); self.open_artifact.clicked.connect(self.open_selected); self.open_artifact.setEnabled(False)
        inspect.addWidget(self.open_artifact); outer.addWidget(self.inspector)
        controls = QHBoxLayout()
        pause = QPushButton("暂停工作"); pause.clicked.connect(lambda: self.control_selected("pause")); controls.addWidget(pause)
        resume = QPushButton("继续工作"); resume.clicked.connect(lambda: self.control_selected("resume")); controls.addWidget(resume)
        cancel = QPushButton("取消工作"); cancel.clicked.connect(lambda: self.control_selected("cancel")); controls.addWidget(cancel)
        inspect.addLayout(controls)
        self.selected_artifact = ""
        if not self._gui_preference("open_evidence_on_start", False):
            self.inspector.hide()

    def _gui_preference(self, key: str, default: Any) -> Any:
        try:
            from partner.state.config import load_partner_config_data
            data = load_partner_config_data(self.workspace)
            gui = data.get("gui") if isinstance(data.get("gui"), dict) else {}
            return gui.get(key, default)
        except (OSError, ValueError, TypeError):
            return default

    def _config_name(self) -> str:
        try:
            from partner.state.config import load_partner_config_data
            return str(load_partner_config_data(self.workspace).get("name") or "Partner")
        except (OSError, ValueError, TypeError):
            return "Partner"

    def show_settings(self) -> None:
        dialog = SettingsDialog(self.workspace, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        if dialog.saved_workspace != self.workspace:
            self.workspace = dialog.saved_workspace
            self.service = PartnerApplicationService(self.workspace)
            self.read_model = ApplicationReadModel(self.workspace)
            self.project_id = ""
            self.selected_job_id = ""
            self.title.setText("所有项目")
        self.brand.setText(self._config_name())
        self.subtitle.setText("设置已保存；运行中的后台服务将在重启后加载模型配置")
        self.refresh()

    def refresh(self) -> None:
        try:
            projects = self.read_model.projects(); updates = self.read_model.updates(self.project_id, 120)
        except Exception as exc:
            self.subtitle.setText(f"刷新失败：{type(exc).__name__}"); return
        clear(self.project_layout)
        all_row = {"project_id":"", "title":"所有项目", "status":"ready", "specialist":"统一"}
        for row in (all_row, *projects):
            card = ProjectItem(row, str(row.get("project_id") or "") == self.project_id)
            card.clicked.connect(self.select_project); self.project_layout.addWidget(card)
        self.project_layout.addStretch()
        clear(self.feed_layout)
        if not updates:
            empty = QFrame(); empty.setObjectName("Card"); box = QVBoxLayout(empty); box.setContentsMargins(22,28,22,28)
            box.addWidget(label("从一个真实问题开始", "Heading")); box.addWidget(label("这里展示有意义的工作增量；技术 Event 默认折叠在证据详情中。", "Muted")); self.feed_layout.addWidget(empty)
        for update in reversed(updates):
            card = UpdateCard(update); card.clicked.connect(self.inspect_update); self.feed_layout.addWidget(card)
        self.feed_layout.addStretch()

    def select_project(self, row: dict[str, Any]) -> None:
        self.project_id = str(row.get("project_id") or ""); self.title.setText(str(row.get("title") or "所有项目")); self.refresh()

    def inspect_update(self, update) -> None:
        self.inspect_title.setText(update.subject)
        evidence = "\n".join("• " + x for x in update.evidence_refs) or "本步骤没有公开证据引用"
        self.inspect_text.setPlainText(f"做了什么\n{update.action}\n\n发现\n{update.finding}\n\n意义\n{update.meaning}\n\n下一步\n{update.next}\n\n证据\n{evidence}")
        self.selected_artifact = next((x for x in update.evidence_refs if Path(x).is_file()), "")
        self.open_artifact.setEnabled(bool(self.selected_artifact))

    def show_jobs(self) -> None:
        jobs = self.read_model.jobs(self.project_id, 100)
        active = next((row for row in jobs if row.state in {"queued", "dispatched", "running", "paused"}), None)
        self.selected_job_id = active.job_id if active else (jobs[0].job_id if jobs else "")
        self.inspect_title.setText("后台工作")
        self.inspect_text.setPlainText("\n\n".join(f"{x.title}\n状态：{x.state}\n当前：{x.meaningful_progress or x.current_activity or '等待调度'}\n结果：{x.latest_result or '尚无'}" for x in jobs) or "当前没有后台工作")
        self.inspector.show()

    def control_selected(self, action: str) -> None:
        if not self.selected_job_id:
            self.inspect_text.setPlainText("请先打开“后台工作”并选择当前工作。")
            return
        result = self.service.control_job(self.selected_job_id, action)
        self.inspect_text.append("\n" + ("操作已记录，将在安全检查点生效。" if result.get("ok") else
                                           "操作未生效：" + str(result.get("error") or "未知原因")))
        self.refresh()

    def toggle_inspector(self) -> None:
        self.inspector.setVisible(not self.inspector.isVisible())

    def add_attachment(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择附件")
        self.attachments.extend(path for path in paths if path not in self.attachments)
        self.attach_label.setText(f"已选择 {len(self.attachments)} 个附件" if self.attachments else "Ctrl+K 聚焦输入")

    def submit(self) -> None:
        text = self.composer.toPlainText().strip()
        if not text and not self.attachments: return
        result = self.service.submit(text, channel="gui", sender_id="desktop_gui",
                                     project_id=self.project_id, attachments=self.attachments)
        if result.accepted:
            self.composer.clear(); self.attachments.clear(); self.attach_label.setText("已进入后台工作队列")
        else:
            self.attach_label.setText(result.message)
        self.refresh()

    def open_selected(self) -> None:
        if self.selected_artifact:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.selected_artifact))
