"""Small, safe settings surface for the Partner desktop workspace."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


API_PROVIDERS = ("minimax", "qwen", "openai", "anthropic", "gemini", "zhipu", "moonshot", "deepseek")
BACKENDS = ("hermes", "codex", "openclaw", "direct", "ollama_lite")


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default)
    except (OSError, ValueError, TypeError):
        return dict(default)


def _atomic_json(path: Path, value: dict[str, Any], private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if private:
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SettingsDialog(QDialog):
    """Edit the real workspace and API configuration without exposing secrets."""

    def __init__(self, workspace: str, parent=None):
        super().__init__(parent)
        self.workspace = str(Path(workspace).resolve())
        self.saved_workspace = self.workspace
        self._api_data: dict[str, Any] = {}
        self.setWindowTitle("Partner 设置")
        self.setModal(True)
        self.resize(610, 520)
        self.setMinimumSize(540, 480)
        self._build()
        self._load_workspace(self.workspace)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        title = QLabel("基础设置")
        title.setObjectName("Title")
        root.addWidget(title)
        note = QLabel("配置保存在当前工作区。API 密钥不会在界面中回显，留空表示保留原值。")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "常规")
        tabs.addTab(self._model_tab(), "Agent")
        tabs.addTab(self._api_tab(), "API")
        root.addWidget(tabs, 1)

        self.feedback = QLabel("")
        self.feedback.setObjectName("Meta")
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)
        controls = QHBoxLayout()
        controls.addStretch()
        cancel = QPushButton("取消")
        cancel.setObjectName("Secondary")
        cancel.clicked.connect(self.reject)
        controls.addWidget(cancel)
        save = QPushButton("保存设置")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        controls.addWidget(save)
        root.addLayout(controls)

    def _general_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(14, 20, 14, 14)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(14)
        workspace_row = QHBoxLayout()
        self.workspace_edit = QLineEdit()
        self.workspace_edit.setPlaceholderText("包含 config/partner_config.json 的工作区")
        workspace_row.addWidget(self.workspace_edit, 1)
        browse = QPushButton("选择…")
        browse.setObjectName("Secondary")
        browse.clicked.connect(self._browse_workspace)
        workspace_row.addWidget(browse)
        form.addRow("工作区位置", workspace_row)
        self.name_edit = QLineEdit()
        form.addRow("显示名称", self.name_edit)
        self.open_details = QCheckBox("启动时展开证据详情")
        form.addRow("界面", self.open_details)
        help_text = QLabel("更换工作区后，当前窗口会立即切换到新项目和任务数据。后台服务若在运行，需单独重启后才会使用新位置。")
        help_text.setObjectName("Muted")
        help_text.setWordWrap(True)
        form.addRow("", help_text)
        return page

    def _model_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(14, 20, 14, 14)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.backend = QComboBox()
        self.backend.addItems(BACKENDS)
        self.backend.setEditable(True)
        form.addRow("Agent 后端", self.backend)
        self.agent_provider = QLineEdit()
        self.agent_provider.setPlaceholderText("留空则使用后端默认 Provider")
        form.addRow("Agent Provider", self.agent_provider)
        self.agent_model = QLineEdit()
        self.agent_model.setPlaceholderText("留空则使用后端默认模型")
        form.addRow("Agent 模型", self.agent_model)
        self.timeout = QSpinBox()
        self.timeout.setRange(30, 7200)
        self.timeout.setSuffix(" 秒")
        form.addRow("项目超时", self.timeout)
        help_text = QLabel("Agent 后端负责项目执行；Provider 与模型留空时沿用该后端自己的默认配置。保存后，运行中的后台服务需重启才会加载新值。")
        help_text.setObjectName("Muted")
        help_text.setWordWrap(True)
        form.addRow("", help_text)
        return page

    def _api_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(14, 20, 14, 14)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.api_provider = QComboBox()
        self.api_provider.addItems(API_PROVIDERS)
        self.api_provider.setEditable(True)
        self.api_provider.currentTextChanged.connect(self._load_api_provider)
        form.addRow("API 服务", self.api_provider)
        self.api_base = QLineEdit()
        self.api_base.setPlaceholderText("https://…/v1")
        form.addRow("API 地址", self.api_base)
        self.api_model = QLineEdit()
        form.addRow("默认模型", self.api_model)
        self.vision_model = QLineEdit()
        self.vision_model.setPlaceholderText("可选；用于图片理解")
        form.addRow("视觉模型", self.vision_model)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("留空以保留当前密钥")
        form.addRow("API 密钥", self.api_key)
        self.clear_key = QCheckBox("清除当前服务已保存的密钥")
        form.addRow("", self.clear_key)
        self.key_state = QLabel("")
        self.key_state.setObjectName("Meta")
        form.addRow("", self.key_state)
        return page

    def _workspace_paths(self, workspace: str) -> tuple[Path, Path]:
        root = Path(workspace).expanduser().resolve()
        return root / "config" / "partner_config.json", root / "config" / "api.json"

    def _load_workspace(self, workspace: str) -> None:
        config_path, api_path = self._workspace_paths(workspace)
        config = _read_json(config_path, {})
        agent = config.get("agent") if isinstance(config.get("agent"), dict) else {}
        gui = config.get("gui") if isinstance(config.get("gui"), dict) else {}
        self.workspace_edit.setText(workspace)
        self.name_edit.setText(str(config.get("name") or "Partner"))
        self.backend.setCurrentText(str(agent.get("backend") or "hermes"))
        self.agent_provider.setText(str(agent.get("provider") or ""))
        self.agent_model.setText(str(agent.get("model") or ""))
        self.timeout.setValue(int(agent.get("project_timeout_sec") or 1200))
        self.open_details.setChecked(bool(gui.get("open_evidence_on_start", False)))
        self._api_data = _read_json(api_path, {"apis": {}})
        if not isinstance(self._api_data.get("apis"), dict):
            self._api_data["apis"] = {}
        self._load_api_provider(self.api_provider.currentText())

    def _load_api_provider(self, provider: str) -> None:
        section = (self._api_data.get("apis") or {}).get(provider.strip(), {})
        section = section if isinstance(section, dict) else {}
        self.api_base.setText(str(section.get("base_url") or ""))
        self.api_model.setText(str(section.get("model") or ""))
        self.vision_model.setText(str(section.get("vision_model") or ""))
        configured = bool(str(section.get("api_key") or "").strip())
        self.api_key.clear()
        self.clear_key.setChecked(False)
        self.key_state.setText("已保存密钥；界面不会回显。" if configured else "尚未保存密钥。")

    def _browse_workspace(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择 Partner 工作区", self.workspace_edit.text())
        if not chosen:
            return
        config_path, _ = self._workspace_paths(chosen)
        if not config_path.is_file():
            QMessageBox.warning(self, "不是 Partner 工作区", "所选目录中没有 config/partner_config.json。")
            return
        self.workspace = str(Path(chosen).resolve())
        self._load_workspace(self.workspace)

    def _save(self) -> None:
        target = str(Path(self.workspace_edit.text().strip()).expanduser().resolve())
        config_path, api_path = self._workspace_paths(target)
        if not config_path.is_file():
            self.feedback.setText("无法保存：工作区中没有 config/partner_config.json。")
            return
        config = _read_json(config_path, {})
        agent = config.get("agent") if isinstance(config.get("agent"), dict) else {}
        agent = dict(agent)
        agent.update({
            "backend": self.backend.currentText().strip() or "hermes",
            "provider": self.agent_provider.text().strip() or None,
            "model": self.agent_model.text().strip() or None,
            "project_timeout_sec": self.timeout.value(),
        })
        config["agent"] = agent
        config["name"] = self.name_edit.text().strip() or "Partner"
        gui = config.get("gui") if isinstance(config.get("gui"), dict) else {}
        gui = dict(gui)
        gui["open_evidence_on_start"] = self.open_details.isChecked()
        config["gui"] = gui
        workspace_section = config.get("workspace") if isinstance(config.get("workspace"), dict) else {}
        workspace_section = dict(workspace_section)
        workspace_section["path"] = target
        config["workspace"] = workspace_section

        api_data = _read_json(api_path, self._api_data or {"apis": {}})
        apis = api_data.get("apis") if isinstance(api_data.get("apis"), dict) else {}
        apis = dict(apis)
        provider = self.api_provider.currentText().strip().lower()
        if not provider:
            self.feedback.setText("无法保存：API 服务名称不能为空。")
            return
        section = apis.get(provider) if isinstance(apis.get(provider), dict) else {}
        section = dict(section)
        section["base_url"] = self.api_base.text().strip()
        section["model"] = self.api_model.text().strip()
        if self.vision_model.text().strip():
            section["vision_model"] = self.vision_model.text().strip()
        else:
            section.pop("vision_model", None)
        if self.clear_key.isChecked():
            section["api_key"] = ""
        elif self.api_key.text().strip():
            section["api_key"] = self.api_key.text().strip()
        apis[provider] = section
        api_data["apis"] = apis

        try:
            _atomic_json(config_path, config)
            _atomic_json(api_path, api_data, private=True)
            from partner.state.setup import save_workspace_pointer
            save_workspace_pointer(target)
        except OSError as exc:
            self.feedback.setText(f"保存失败：{exc}")
            return
        self.saved_workspace = target
        self.accept()
