"""Chat/dialogue page - dual-column design with source filter and EventStepWidget pipeline display."""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QUrl
from PySide6.QtGui import (
    QAction,
    QDragEnterEvent,
    QDropEvent,
    QPixmap,
    QClipboard,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QSplitter,
    QMenu,
)

from partner.monitoring.instance_root import (
    resolve_partner_root,
    resolve_global_config_path,
)

from ..theme import THEME
from ..widgets import ChatBubble, EventStepWidget


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_jsonl(path: str, n: int = 500) -> list[dict]:
    """Load up to n JSONL entries from a file (newest first)."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return rows


def _resolve_instance_env(instance_id: str) -> str:
    """Read environment type from global_config.json for an instance.

    Returns 'wsl', 'local_windows', or auto-detects from working_dir path.
    """
    try:
        cfg = _load_json(str(resolve_global_config_path()))
        instances = cfg.get("instances", {})
        info = instances.get(instance_id, {})
        env = info.get("environment", "").strip().lower()
        if env in ("wsl", "local_windows", "local_linux"):
            return env
        wd = info.get("working_dir", "").replace("\\", "/")
        if wd.startswith("/mnt/") or wd.startswith("/"):
            return "wsl"
        if len(wd) >= 2 and wd[1] == ":":
            return "local_windows"
    except Exception:
        pass
    return "wsl"


def _parse_log_file(path: str) -> list[dict]:
    """Parse a dialogue .log file, handling multi-line A: content and continuation lines.

    The log format has QQ entries written AFTER the Partner's processing cycle:
      [Partner progress messages referencing user] ... [QQ: Q:用户 / A:非ce]

    We reorder: user messages are emitted BEFORE the next cycle's assistant messages,
    so the conversation reads: User Q -> Bot processing -> Bot response -> User Q -> ...
    """
    turns = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    ts_map: dict[int, str] = {}
    for i, line in enumerate(lines):
        m = re.match(r"\[(\d{2}:\d{2}:\d{2})\] \[(QQ|Partner)\]", line)
        if m:
            ts_map[i] = f"{Path(path).stem}T{m.group(1)}"

    pending_user: dict | None = None  # buffered user message from QQ block
    current_a_parts: list[str] = []
    a_ts_line = -1

    def flush_assistant():
        nonlocal current_a_parts, a_ts_line, pending_user
        if not current_a_parts:
            return
        content = "\n".join(current_a_parts)
        if not content:
            current_a_parts = []
            a_ts_line = -1
            return

        # Filter out pure "[进度] 正在思考..." / "[进度]thinking..." notices
        if re.match(r"^\[进度\]\s*正在?思考", content):
            current_a_parts = []
            a_ts_line = -1
            return

        # Filter out "已停止「xxx」的当前执行链" — internal execution notices, not chat
        if re.match(r"^已停止「", content):
            current_a_parts = []
            a_ts_line = -1
            return
        # Flush any pending user BEFORE this assistant turn
        if pending_user:
            turns.append(pending_user)
            pending_user = None
        a_ts = Path(path).stem
        for idx in range(a_ts_line - 1, -1, -1):
            if idx in ts_map:
                a_ts = ts_map[idx]
                break
        turns.append({
            "role": "assistant", "content": content,
            "timestamp": a_ts, "source": "qq",
        })
        current_a_parts = []
        a_ts_line = -1

    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped.startswith("  Q: "):
            # Flush any pending_user BEFORE a new user message to handle
            # consecutive user messages without intervening assistant turn
            if pending_user:
                turns.append(pending_user)
                pending_user = None
            flush_assistant()
            q_text = stripped[4:].strip()
            q_ts = Path(path).stem
            for idx in range(i - 1, -1, -1):
                if idx in ts_map:
                    q_ts = ts_map[idx]
                    break
            # Buffer user message -- will be flushed before next assistant turn
            pending_user = {
                "role": "user", "content": q_text,
                "timestamp": q_ts, "source": "qq",
            }
        elif stripped.startswith("  A: "):
            a_text = stripped[4:].strip()
            flush_assistant()
            current_a_parts = [a_text]
            a_ts_line = i
        elif current_a_parts and stripped and not stripped.startswith("  Q: ") and not stripped.startswith("  A: ") and not stripped.startswith("["):
            current_a_parts.append(stripped)

    # Flush remaining
    flush_assistant()
    if pending_user:
        turns.append(pending_user)

    return turns


def _dialogue_cache_path(workspace: str, instance_id: str) -> str:
    """Path to the cached dialogue turns for a given instance.

    Cache contains pre-parsed dialogue turns so subsequent loads are instant
    (pure JSON deserialization instead of re-parsing log files).
    """
    cache_dir = os.path.join(workspace, ".dialogue_cache")
    return os.path.join(cache_dir, f"{instance_id}.json")


def _dialogue_source_mtime(workspace: str, instance_id: str) -> float:
    """Return the latest modification time of all source files for an instance.

    Used to invalidate the cache when source files change.
    """
    import time
    latest = 0.0
    inst_dir = Path(workspace) / "instances" / instance_id
    # Primary source
    qq_path = inst_dir / "state" / "qq_chat_history.jsonl"
    if qq_path.exists():
        try:
            mtime = qq_path.stat().st_mtime
            if mtime > latest:
                latest = mtime
        except Exception:
            pass
    # Secondary: .log files in dialogue/
    dialogue_dir = inst_dir / "dialogue"
    if dialogue_dir.is_dir():
        for log_file in dialogue_dir.glob("*.log"):
            try:
                mtime = log_file.stat().st_mtime
                if mtime > latest:
                    latest = mtime
            except Exception:
                pass
    return latest


def _load_dialogue_turns(workspace: str, instance_id: str = "",
                         offset: int = 0, limit: int = 200,
                         use_cache: bool = True) -> list[dict]:
    """Load turns from qq_chat_history.jsonl (primary) + .log files (fallback).

    When use_cache=True (default), checks a JSON cache file first.  If the
    cache is recent (source files unchanged), loads from cache — sub-millisecond
    instead of 100ms+ parsing.

    qq_chat_history.jsonl in state/ has the complete conversation including
    both user messages (from QQ) and assistant responses.  Returns entries
    sorted chronologically (oldest first), newest last.
    """
    # ── Cache fast-path ──────────────────────────────────────────────────
    if use_cache and instance_id:
        cache_path = _dialogue_cache_path(workspace, instance_id)
        if os.path.exists(cache_path):
            try:
                cached = _load_json(cache_path)
                cache_mtime = cached.get("_cache_mtime", 0)
                source_mtime = _dialogue_source_mtime(workspace, instance_id)
                if cache_mtime >= source_mtime:
                    turns = cached.get("turns", [])
                    if offset == 0:
                        return turns[-limit:] if limit < len(turns) else turns
                    return turns[offset:offset + limit]
            except Exception:
                pass

    # ── Full parse (slow path) ───────────────────────────────────────────
    turns: list[dict] = []
    instances: list[str] = []
    if instance_id:
        instances = [instance_id]
    else:
        try:
            inst_dirs = sorted(Path(workspace).glob("instances/*"), reverse=True)
            instances = [d.name for d in inst_dirs]
        except Exception:
            return []

    for inst_id in instances:
        inst_dir = Path(workspace) / "instances" / inst_id

        # Primary source: state/qq_chat_history.jsonl (complete user+assistant)
        qq_path = inst_dir / "state" / "qq_chat_history.jsonl"
        if qq_path.exists():
            try:
                rows = _load_jsonl(str(qq_path))
                for row in rows:
                    role = row.get("role", "user")
                    content = row.get("content", row.get("text", ""))
                    if not content:
                        continue
                    # Filter out internal notices
                    if "思考中" in str(content):
                        continue
                    if str(content).startswith("已停止「"):
                        continue
                    ts = str(row.get("timestamp") or row.get("created_at") or "")
                    source = row.get("source", "qq")
                    row_out = {
                        "role": role,
                        "content": content,
                        "timestamp": ts,
                        "source": source if source else "qq",
                        "instance_id": inst_id,
                    }
                    turns.append(row_out)
            except Exception:
                pass

        # Secondary source: .log files (for any assistant messages not in jsonl)
        dialogue_dir = inst_dir / "dialogue"
        if dialogue_dir.is_dir():
            for log_file in sorted(dialogue_dir.glob("*.log"), reverse=True):
                try:
                    parsed = _parse_log_file(str(log_file))
                    for entry in parsed:
                        entry["instance_id"] = inst_id
                        # Deduplicate against existing qq_chat entries by content[:120]
                        # IMPORTANT: Strip "Q: " / "  Q: " / "A: " / "  A: " prefixes from
                        # .log entries before comparing because .log has "  Q: " prefix
                        # while JSONL entries have plain content with no prefix.
                        raw_content = str(entry.get("content", ""))
                        stripped = raw_content
                        for prefix in ("Q: ", "  Q: ", "A: ", "  A: "):
                            if stripped.startswith(prefix):
                                stripped = stripped[len(prefix):]
                                break
                        is_dup = False
                        for existing in turns:
                            existing_content = str(existing.get("content", ""))
                            # Also strip prefixes from existing content for comparison
                            ec = existing_content
                            for prefix in ("Q: ", "  Q: ", "A: ", "  A: "):
                                if ec.startswith(prefix):
                                    ec = ec[len(prefix):]
                                    break
                            if existing.get("instance_id") == inst_id and \
                               ec[:120] == stripped[:120]:
                                is_dup = True
                                break
                        if not is_dup:
                            turns.append(entry)
                except Exception:
                    pass

    # Deduplicate by (instance_id, timestamp[:19], content[:120]) to catch near-duplicates
    seen: set = set()
    deduped = []
    for t in sorted(turns, key=lambda r: str(r.get("timestamp") or "")):
        ts = str(t.get("timestamp", ""))
        key = (t.get("instance_id", ""),
               ts[:19],  # Use seconds precision instead of date-only
               str(t.get("content", ""))[:120])
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    # For each dialogue entry, try to find a matching pipeline snapshot
    # by looking for conversations/<round_id>/pipeline.json where the
    # round_id matches the entry's approximate timestamp.
    import glob as _glob
    conv_base = os.path.join(workspace, "conversations")
    for t in deduped:
        ts = str(t.get("timestamp", ""))
        # Try exact timestamp match first — round_id format matches executor.py:
        #   now_ts.replace(":", "-").replace(".", "-")
        # where now_ts is the full ISO timestamp like "2026-06-19T23:14:26.257699"
        if ts:
            # Format 1: full timestamp with microseconds (executor.py line 5860)
            round_id = ts.replace(":", "-").replace(".", "-")
            snap = os.path.join(conv_base, round_id, "pipeline.json")
            if os.path.exists(snap):
                t["pipeline_path"] = snap
            else:
                # Format 2: truncate to seconds (some older snapshots)
                ts_sec = ts[:19].replace(":", "-").replace("T", "-")
                for d in _glob.glob(os.path.join(conv_base, ts_sec + "*")):
                    pip = os.path.join(d, "pipeline.json")
                    if os.path.exists(pip):
                        t["pipeline_path"] = pip
                        break

    # Apply offset/limit pagination (offset=0 -> newest limit entries)
    deduped.sort(key=lambda r: str(r.get("timestamp") or ""))
    total = len(deduped)

    # ── Save full result to cache for instant subsequent loads ──
    if use_cache and instance_id and deduped:
        try:
            cache_path = _dialogue_cache_path(workspace, instance_id)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            cache_data = {
                "_cache_mtime": _dialogue_source_mtime(workspace, instance_id),
                "turns": deduped,
            }
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False)
        except Exception:
            pass

    if offset >= total:
        return []
    return deduped[-(offset + limit):][:limit] if offset == 0 else deduped[offset:offset + limit]


def _format_timestamp(ts: str) -> str:
    """Format an ISO timestamp to a short readable form."""
    if not ts:
        return ""
    try:
        if "T" in ts:
            dt = datetime.fromisoformat(ts)
        else:
            return ts
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        if dt.date() == now.date():
            return dt.strftime("%H:%M:%S")
        elif (now - dt).days < 7:
            return dt.strftime("%m-%d %H:%M")
        else:
            return dt.strftime("%m-%d")
    except Exception:
        return ts


def _format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------------
# Attachment file widget (shown in the attachment bar)
# ---------------------------------------------------------------------------


class AttachmentFileWidget(QFrame):
    """A small widget representing an attached file in the input area."""

    remove_clicked = Signal(str)

    def __init__(self, file_path: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._file_path = file_path
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("attachment_file")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        name = os.path.basename(self._file_path)
        size = _format_file_size(os.path.getsize(self._file_path))

        # Try to show thumbnail for images
        ext = Path(self._file_path).suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            pixmap = QPixmap(self._file_path)
            if not pixmap.isNull():
                thumb = QLabel()
                thumb.setPixmap(pixmap.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
                thumb.setFixedSize(24, 24)
                thumb.setStyleSheet("border-radius: 4px; background: transparent;")
                layout.addWidget(thumb)
        else:
            icon_label = QLabel("\U0001f4ce")
            icon_label.setStyleSheet("font-size: 14px; background: transparent;")
            icon_label.setFixedWidth(20)
            layout.addWidget(icon_label)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)
        name_label = QLabel(name[:20] + ("..." if len(name) > 20 else ""))
        name_label.setStyleSheet(f"font-size: 11px; color: {THEME.txt}; background: transparent;")
        name_label.setToolTip(self._file_path)
        info_layout.addWidget(name_label)

        size_label = QLabel(size)
        size_label.setStyleSheet(f"font-size: 9px; color: {THEME.txt3}; background: transparent;")
        info_layout.addWidget(size_label)
        layout.addLayout(info_layout)

        # Remove button
        remove_btn = QPushButton("\u00d7")
        remove_btn.setFixedSize(18, 18)
        remove_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {THEME.txt3};
                border: none;
                font-size: 14px;
                font-weight: bold;
                padding: 0;
            }}
            QPushButton:hover {{
                color: {THEME.red};
            }}
        """)
        remove_btn.clicked.connect(lambda: self.remove_clicked.emit(self._file_path))
        layout.addWidget(remove_btn)

        self.setStyleSheet(f"""
            QFrame#attachment_file {{
                background-color: {THEME.bg3};
                border: 1px solid {THEME.border};
                border-radius: 6px;
            }}
        """)
        self.setFixedHeight(42)


# ---------------------------------------------------------------------------
# Chat Page
# ---------------------------------------------------------------------------


class ChatPage(QWidget):
    """Chat/dialogue page with dual-column layout: chat (left) + pipeline (right) + input bar."""

    # Signal emitted when the instances page creates/deletes instances — lets
    # the main window know to propagate updates across all subscribing pages.
    instances_changed = Signal()

    # Signal emitted after the first deferred initialisation completes.
    # Lets the main window hide its loading overlay.
    loading_complete = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._messages: list[dict] = []
        self._polled_message_ids: set[str] = set()
        self._seen_responses: set[tuple[str, str]] = set()
        self._pending_message_id: str | None = None
        self._poll_start_time: float = 0.0
        self._poll_retries: int = 0
        self._poll_active: bool = False
        self._auto_starting: bool = False
        self._selected_instance_id: str = ""
        self._loading_widget_index: int | None = None
        self._attachments: list[str] = []
        self._event_step_widgets: list = []
        self._source_filter: str = "全部"
        # Pagination state — log file is newest-first
        self._load_offset: int = 0             # How many entries already loaded from file top
        self._page_size: int = 200             # Turns to load per batch
        self._workspace_path: str = ""         # Resolved once on first load
        self._loading_more: bool = False       # Guard against re-entrant scroll-up
        # Historical pipeline snapshot flag — stops _poll_active_plan from overwriting
        self._showing_historical_pipeline: bool = False

        self.setAcceptDrops(True)
        self._build_ui()

        # ── Defer all heavy I/O so the window shell shows immediately ──
        QTimer.singleShot(0, self._deferred_init)

    def _deferred_init(self):
        """Finish initialisation after the event loop has rendered the window.

        Order matters: load instance selector first (which populates the
        dropdown, selects the newest instance, and triggers _on_instance_changed
        → _load_history), then start the poll timers.
        """
        # Show loading status in the main window overlay
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, '_update_loading_status'):
                parent._update_loading_status("正在加载对话记录…")
                break
            parent = parent.parent() if hasattr(parent, 'parent') else None

        # Step 1: load instance selector → _on_instance_changed → _load_history
        # This is the single entry point for initial data loading.
        self._load_instance_selector()

        # Step 2: poll timers for incoming messages and live pipeline
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_new_messages)
        self._poll_timer.start(2000)

        self._plan_timer = QTimer(self)
        self._plan_timer.timeout.connect(self._poll_active_plan)
        self._plan_timer.start(2000)

        # Notify main window that loading is complete
        self.loading_complete.emit()

    # ------------------------------------------------------------------
    # UI Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # -- Top splitter: Chat (left) + Pipeline (right) --
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(1)
        self._splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: {THEME.border};
                width: 1px;
            }}
            QSplitter::handle:hover {{
                background-color: {THEME.accent};
            }}
        """)
        # Make splitter respond to resize events for responsive behavior
        self._splitter.splitterMoved.connect(self._on_splitter_moved)

        # Left: Chat area
        self._build_chat_side()
        self._splitter.addWidget(self._chat_side_container)

        # Right: Pipeline area
        self._build_pipeline_side()
        self._splitter.addWidget(self._pipeline_side_container)

        # Set 50/50 split
        self._splitter.setSizes([550, 550])
        self._splitter.setMinimumWidth(550)

        # Restore saved splitter position from layout file
        self._restore_splitter_position()

        main_layout.addWidget(self._splitter, 1)

        # -- Bottom: Input area --
        self._build_input_area()
        self._input_frame.setMaximumHeight(200)
        main_layout.addWidget(self._input_frame)

        # Install resize event on the page itself for responsive behavior
        self._last_width = 0
        self._responsive_vertical = False

    # ------------------------------------------------------------------
    # Splitter persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _layout_path() -> str:
        """Return the path to the GUI layout JSON file."""
        return os.path.join(str(Path.home()), ".partner", "gui_layout.json")

    def _restore_splitter_position(self):
        """Try to restore the splitter position from the layout file."""
        try:
            data = _load_json(self._layout_path())
            sizes = data.get("splitter_position")
            if sizes and isinstance(sizes, list) and len(sizes) == 2:
                sizes_int = [int(s) for s in sizes]
                if all(s > 0 for s in sizes_int):
                    self._splitter.setSizes(sizes_int)
        except Exception:
            pass

    def _save_splitter_position(self):
        """Save the splitter position to the layout file."""
        try:
            path = self._layout_path()
            data = _load_json(path)
            data["splitter_position"] = list(self._splitter.sizes())
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_splitter_moved(self, pos, index):
        """Handle splitter movement - save the new position and trigger responsive check."""
        self._save_splitter_position()

    def resizeEvent(self, event):
        """Detect window size and switch splitter orientation when narrow."""
        super().resizeEvent(event)
        new_width = event.size().width()
        if new_width < 800 and new_width != self._last_width:
            if not self._responsive_vertical:
                self._responsive_vertical = True
                self._splitter.setOrientation(Qt.Orientation.Vertical)
                self._splitter.setSizes([400, 300])
        elif new_width >= 800 and self._responsive_vertical:
            self._responsive_vertical = False
            self._splitter.setOrientation(Qt.Orientation.Horizontal)
            self._splitter.setSizes([600, 400])
        self._last_width = new_width

    # -- Chat Side (left) --

    def _build_chat_side(self):
        self._chat_side_container = QWidget()
        self._chat_side_container.setObjectName("chat_side")
        self._chat_side_container.setMinimumWidth(300)
        self._chat_side_container.setStyleSheet(f"""
            QWidget#chat_side {{
                background-color: {THEME.bg};
            }}
        """)
        layout = QVBoxLayout(self._chat_side_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title header with source filter
        title_bar = QFrame()
        title_bar.setStyleSheet(
            f"background-color: {THEME.card}; border-bottom: 1px solid {THEME.border};"
        )
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(20, 12, 16, 12)

        title_label = QLabel("\U0001f4ac \u5bf9\u8bdd")
        title_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {THEME.txt}; background: transparent;"
        )
        title_layout.addWidget(title_label)
        title_layout.addSpacing(12)

        # Source filter dropdown
        self._source_filter_combo = QComboBox()
        self._source_filter_combo.addItems(["全部", "GUI", "QQ", "CLI"])
        self._source_filter_combo.setFixedSize(110, 32)
        self._source_filter_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                padding: 2px 32px 2px 12px;
                font-size: 12px;
                min-height: 32px;
            }}
            QComboBox:hover {{
                border-color: {THEME.accent};
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.card_hl}, stop:1 {THEME.bg2});
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border: none;
                border-top-right-radius: 10px;
                border-bottom-right-radius: 10px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 7px solid transparent;
                border-right: 7px solid transparent;
                border-top: 9px solid {THEME.txt2};
                margin-right: 2px;
            }}
            QComboBox::down-arrow:hover {{
                border-top-color: {THEME.accent};
            }}
            QComboBox QAbstractItemView {{
                background-color: {THEME.bg2};
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                selection-background-color: {THEME.bg3};
                selection-color: {THEME.accent};
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 8px 12px;
                border-radius: 6px;
                min-height: 30px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: rgba(74, 144, 217, 0.10);
                color: {THEME.accent};
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: rgba(74, 144, 217, 0.15);
                color: {THEME.accent};
                font-weight: bold;
            }}
        """)
        self._source_filter_combo.currentTextChanged.connect(self._on_source_filter_changed)
        title_layout.addWidget(self._source_filter_combo)
        title_layout.addSpacing(8)

        # Instance selector (moved from input area to header)
        self._instance_selector = QComboBox()
        self._instance_selector.setFixedSize(110, 32)
        self._instance_selector.setPlaceholderText("实例...")
        self._instance_selector.setStyleSheet(f"""
            QComboBox {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg2}, stop:1 {THEME.bg3});
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                padding: 2px 36px 2px 12px;
                font-size: 12px;
                min-height: 32px;
            }}
            QComboBox:hover {{
                border-color: {THEME.accent};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border: none;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 7px solid transparent;
                border-right: 7px solid transparent;
                border-top: 9px solid {THEME.txt2};
                margin-right: 4px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {THEME.bg};
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 8px;
                padding: 4px;
                selection-background-color: {THEME.bg3};
            }}
            QComboBox QAbstractItemView::item {{
                padding: 6px 14px;
                border-radius: 6px;
                min-height: 30px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: rgba(74, 144, 217, 0.10);
                color: {THEME.accent};
            }}
            QComboBox QAbstractItemView::item:selected {{
                background-color: rgba(74, 144, 217, 0.15);
                color: {THEME.accent};
                font-weight: bold;
            }}
        """)
        self._instance_selector.currentIndexChanged.connect(self._on_instance_changed)
        title_layout.addWidget(self._instance_selector)

        title_layout.addStretch()

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            "font-size: 12px; padding: 4px 8px; background: transparent;"
        )
        self._status_label.setVisible(False)
        title_layout.addWidget(self._status_label)

        # Workspace path label (compact)
        ws_path = self._workspace()
        ws_label = QLabel(f"\U0001f4c1 {ws_path}")
        ws_label.setStyleSheet(
            f"font-size: 10px; color: {THEME.txt3}; background: transparent;"
        )
        ws_label.setToolTip(ws_path)
        ws_label.setMaximumWidth(180)
        ws_label.setWordWrap(False)
        title_layout.addWidget(ws_label)

        layout.addWidget(title_bar)

        # Scroll area with lazy-load on scroll-to-top
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet(f"""
            QScrollArea {{
                background-color: {THEME.bg};
                border: none;
            }}
        """)
        self._scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

        self._chat_container = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(12, 8, 12, 8)
        self._chat_layout.setSpacing(6)
        self._chat_layout.setAlignment(Qt.AlignmentFlag.AlignBottom)

        # Empty state
        self._chat_empty = QLabel("\u53d1\u9001\u6d88\u606f\u5f00\u59cb\u5bf9\u8bdd")
        self._chat_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chat_empty.setStyleSheet(f"""
            font-size: 14px;
            color: {THEME.txt3};
            background: transparent;
            padding: 40px 20px;
        """)
        self._chat_layout.addWidget(self._chat_empty)

        self._scroll_area.setWidget(self._chat_container)
        layout.addWidget(self._scroll_area, 1)

        # Scroll-to-bottom button
        bottom_btn_bar = QWidget()
        bottom_btn_bar.setStyleSheet("background: transparent;")
        bottom_btn_layout = QHBoxLayout(bottom_btn_bar)
        bottom_btn_layout.setContentsMargins(0, 0, 16, 4)

        self._scroll_to_bottom_btn = QPushButton("\u2193 \u6eda\u52a8\u5230\u5e95\u90e8")
        self._scroll_to_bottom_btn.setFixedSize(130, 32)
        self._scroll_to_bottom_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.card}, stop:1 {THEME.bg3});
                color: {THEME.txt2};
                border: 1px solid {THEME.border};
                border-radius: 16px;
                font-size: 12px;
                font-weight: bold;
                padding: 0 16px;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent}, stop:1 {THEME.accent3});
                color: white;
                border-color: {THEME.accent};
            }}
            QPushButton:pressed {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.accent3}, stop:1 #2A5F8A);
                color: white;
                border-color: {THEME.accent3};
            }}
        """)
        self._scroll_to_bottom_btn.clicked.connect(self._scroll_to_bottom)
        bottom_btn_layout.addStretch()
        bottom_btn_layout.addWidget(self._scroll_to_bottom_btn)
        layout.addWidget(bottom_btn_bar)

    # -- Pipeline Side (right) --

    def _build_pipeline_side(self):
        self._pipeline_side_container = QWidget()
        self._pipeline_side_container.setObjectName("pipeline_side")
        self._pipeline_side_container.setMinimumWidth(250)
        self._pipeline_side_container.setStyleSheet(f"""
            QWidget#pipeline_side {{
                background-color: {THEME.bg};
                border-left: 1px solid {THEME.border};
            }}
        """)
        layout = QVBoxLayout(self._pipeline_side_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title header
        title_bar = QFrame()
        title_bar.setStyleSheet(
            f"background-color: {THEME.card}; border-bottom: 1px solid {THEME.border};"
        )
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(20, 12, 20, 12)

        title_label = QLabel("🔄 Event 流水线")
        title_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {THEME.txt}; background: transparent;"
        )
        title_layout.addWidget(title_label)
        title_layout.addSpacing(8)

        self._plan_name_label = QLabel("")
        self._plan_name_label.setStyleSheet(
            f"font-size: 12px; color: {THEME.txt2}; background: transparent;"
        )
        self._plan_name_label.setMaximumWidth(200)
        self._plan_name_label.setWordWrap(False)
        self._plan_name_label.setToolTip("")
        title_layout.addWidget(self._plan_name_label)

        title_layout.addStretch()

        # Mode indicator label
        self._pipeline_mode_label = QLabel("")
        self._pipeline_mode_label.setStyleSheet(
            f"font-size: 11px; font-weight: bold; color: {THEME.accent}; background: transparent;"
        )
        self._pipeline_mode_label.setVisible(False)
        title_layout.addWidget(self._pipeline_mode_label)

        layout.addWidget(title_bar)

        # Progress bar
        progress_bar_container = QFrame()
        progress_bar_container.setStyleSheet(
            f"background-color: {THEME.card}; border-bottom: 1px solid {THEME.border};"
        )
        progress_layout = QHBoxLayout(progress_bar_container)
        progress_layout.setContentsMargins(20, 8, 20, 8)

        self._pipeline_progress = QProgressBar()
        self._pipeline_progress.setMinimum(0)
        self._pipeline_progress.setMaximum(100)
        self._pipeline_progress.setValue(0)
        self._pipeline_progress.setTextVisible(True)
        self._pipeline_progress.setFixedHeight(20)
        self._pipeline_progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {THEME.bg3};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                text-align: center;
                color: {THEME.txt};
                font-size: 11px;
            }}
            QProgressBar::chunk {{
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {THEME.accent}, stop:1 {THEME.green});
                border-radius: 9px;
            }}
        """)
        self._pipeline_progress.setVisible(False)
        progress_layout.addWidget(self._pipeline_progress, 1)

        self._pipeline_progress_label = QLabel("")
        self._pipeline_progress_label.setStyleSheet(
            f"font-size: 11px; color: {THEME.txt2}; background: transparent;"
        )
        progress_layout.addWidget(self._pipeline_progress_label)
        layout.addWidget(progress_bar_container)

        # Scroll area for step widgets
        pipeline_scroll = QScrollArea()
        pipeline_scroll.setWidgetResizable(True)
        pipeline_scroll.setFrameShape(QFrame.Shape.NoFrame)
        pipeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pipeline_scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {THEME.bg};
                border: none;
            }}
        """)

        self._pipeline_container = QWidget()
        self._pipeline_layout = QVBoxLayout(self._pipeline_container)
        self._pipeline_layout.setContentsMargins(12, 12, 12, 12)
        self._pipeline_layout.setSpacing(6)
        self._pipeline_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Empty state
        self._pipeline_empty = QLabel("空闲，等待新消息")
        self._pipeline_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pipeline_empty.setStyleSheet(f"""
            font-size: 14px;
            color: {THEME.txt3};
            background: transparent;
            padding: 40px 20px;
        """)
        self._pipeline_layout.addWidget(self._pipeline_empty)

        # Planning indicator (shown when plan.status == "planning" with no phases yet)
        self._pipeline_planning = QLabel("")
        self._pipeline_planning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pipeline_planning.setStyleSheet(f"""
            font-size: 14px;
            color: {THEME.accent};
            background: transparent;
            padding: 20px;
        """)
        self._pipeline_planning.setVisible(False)
        self._pipeline_layout.addWidget(self._pipeline_planning)

        # Event step widgets will be inserted here
        self._pipeline_steps_layout = QVBoxLayout()
        self._pipeline_steps_layout.setSpacing(6)
        self._pipeline_layout.addLayout(self._pipeline_steps_layout)

        # Summary section (between steps and artifacts)
        self._pipeline_summary_section = QWidget()
        self._pipeline_summary_section.setVisible(False)
        summary_layout_outer = QVBoxLayout(self._pipeline_summary_section)
        summary_layout_outer.setContentsMargins(0, 8, 0, 0)
        summary_layout_outer.setSpacing(4)
        summary_title = QLabel("📋 执行总结")
        summary_title.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {THEME.txt2}; background: transparent;"
        )
        summary_layout_outer.addWidget(summary_title)
        self._pipeline_summary = QLabel("")
        self._pipeline_summary.setWordWrap(True)
        self._pipeline_summary.setStyleSheet(
            f"font-size: 11px; color: {THEME.txt}; background: transparent; line-height: 1.5;"
        )
        summary_layout_outer.addWidget(self._pipeline_summary)
        self._pipeline_layout.addWidget(self._pipeline_summary_section)

        # Artifacts section
        self._artifacts_section = QWidget()
        self._artifacts_section.setVisible(False)
        art_layout = QVBoxLayout(self._artifacts_section)
        art_layout.setContentsMargins(0, 8, 0, 0)
        art_layout.setSpacing(6)

        art_title = QLabel("\U0001f4ce \u4e2d\u95f4\u4ea7\u7269")
        art_title.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {THEME.txt2}; background: transparent;"
        )
        art_layout.addWidget(art_title)

        self._artifacts_list = QVBoxLayout()
        self._artifacts_list.setSpacing(4)
        art_layout.addLayout(self._artifacts_list)
        art_layout.addStretch()

        self._pipeline_layout.addWidget(self._artifacts_section)
        self._pipeline_layout.addStretch()

        pipeline_scroll.setWidget(self._pipeline_container)
        layout.addWidget(pipeline_scroll, 1)

        # Footer bar (empty for now — reserved for future pipeline controls)
        footer_bar = QFrame()
        footer_bar.setStyleSheet(
            f"background-color: {THEME.card}; border-top: 1px solid {THEME.border};"
        )
        footer_layout = QHBoxLayout(footer_bar)
        footer_layout.setContentsMargins(16, 4, 16, 4)
        layout.addWidget(footer_bar)

    # -- Input Area --

    def _build_input_area(self):
        self._input_frame = QFrame()
        self._input_frame.setObjectName("input_area")
        self._input_frame.setStyleSheet(f"""
            QFrame#input_area {{
                background-color: {THEME.card};
                border-top: 1px solid {THEME.border};
            }}
        """)

        input_layout = QVBoxLayout(self._input_frame)
        input_layout.setContentsMargins(16, 10, 16, 12)
        input_layout.setSpacing(8)

        # -- Attachment bar (scrollable list of attached files) --
        self._attachment_bar = QWidget()
        self._attachment_bar.setVisible(False)
        self._attachment_bar.setStyleSheet("background: transparent;")
        attachment_layout = QHBoxLayout(self._attachment_bar)
        attachment_layout.setContentsMargins(0, 0, 0, 0)
        attachment_layout.setSpacing(6)

        self._attachment_scroll = QScrollArea()
        self._attachment_scroll.setWidgetResizable(True)
        self._attachment_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._attachment_scroll.setMaximumHeight(48)
        self._attachment_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._attachment_scroll.setStyleSheet("background: transparent; border: none;")

        self._attachment_list_widget = QWidget()
        self._attachment_list_widget.setStyleSheet("background: transparent;")
        self._attachment_list_layout = QHBoxLayout(self._attachment_list_widget)
        self._attachment_list_layout.setContentsMargins(0, 0, 0, 0)
        self._attachment_list_layout.setSpacing(6)
        self._attachment_list_layout.addStretch()

        self._attachment_scroll.setWidget(self._attachment_list_widget)
        attachment_layout.addWidget(self._attachment_scroll)

        # Clear all attachments button
        self._clear_attachments_btn = QPushButton("\u6e05\u9664")
        self._clear_attachments_btn.setFixedHeight(28)
        self._clear_attachments_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {THEME.txt3};
                border: 1px solid {THEME.border};
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
                padding: 2px 10px;
            }}
            QPushButton:hover {{
                color: {THEME.red};
                border-color: {THEME.red};
                background-color: rgba(229, 57, 53, 0.05);
            }}
            QPushButton:pressed {{
                background-color: rgba(229, 57, 53, 0.12);
            }}
        """)
        self._clear_attachments_btn.clicked.connect(self._clear_attachments)
        attachment_layout.addWidget(self._clear_attachments_btn)

        input_layout.addWidget(self._attachment_bar)

        # Input row: text + controls all in ONE horizontal line
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        # Text input
        self._input = QPlainTextEdit()
        self._input.setPlaceholderText("输入消息... (Enter 发送, Shift+Enter 换行)")
        self._input.setMinimumHeight(42)
        self._input.setMaximumHeight(120)
        self._input.setAttribute(Qt.WA_InputMethodEnabled, True)
        self._input.installEventFilter(self)
        self._input.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {THEME.input_bg};
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 13px;
            }}
            QPlainTextEdit:focus {{
                border-color: {THEME.accent};
            }}
        """)
        self._input.textChanged.connect(self._on_text_changed)
        self._input.setAcceptDrops(False)
        input_row.addWidget(self._input, 1)

        # Ensure input has focus for IME (Chinese input support)
        self._input.setFocus()

        # Attach button
        attach_btn = QPushButton("📎 文件")
        attach_btn.setFixedSize(100, 42)
        attach_btn.setToolTip("添加文件")
        attach_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.input_bg}, stop:1 {THEME.bg3});
                color: {THEME.txt2};
                border: 1px solid {THEME.border};
                border-radius: 10px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.card_hl}, stop:1 {THEME.bg2});
                border-color: {THEME.accent};
                color: {THEME.accent};
            }}
            QPushButton:pressed {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {THEME.bg3}, stop:1 {THEME.border});
                border-color: {THEME.accent3};
                color: {THEME.accent3};
            }}
        """)
        attach_btn.clicked.connect(self._on_attach_file)
        input_row.addWidget(attach_btn)

        # Send button
        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(100, 42)
        self._send_btn.setEnabled(False)
        self._send_btn.clicked.connect(self._on_send)
        self._update_send_button_style()
        input_row.addWidget(self._send_btn)

        input_layout.addLayout(input_row)

        # Load instances — _deferred_init handles this first, then history.
        # Not deferred separately here to avoid race with _deferred_init.
        # See _deferred_init for the actual call order.

    # ------------------------------------------------------------------
    # Drag & Drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path):
                self._add_attachment(file_path)
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Source Filter
    # ------------------------------------------------------------------

    def _on_source_filter_changed(self, text: str):
        """Apply source filter to the message list."""
        self._source_filter = text
        self._apply_source_filter()

    def _apply_source_filter(self):
        """Re-render messages based on the active source filter."""
        # We rebuild the chat display from self._messages with the filter
        filter_val = self._source_filter
        # Remove existing message widgets (keep loading placeholder and empty state)
        self._clear_chat_widgets_only()

        # Show empty state if no messages
        filtered = []
        for m in self._messages:
            src = m.get("source", "")
            if filter_val == "\u5168\u90e8":
                filtered.append(m)
            elif filter_val == "GUI" and src in ("gui", ""):
                filtered.append(m)
            elif filter_val == "QQ" and src == "qq":
                filtered.append(m)
            elif filter_val == "CLI" and src == "cli":
                filtered.append(m)

        if not filtered:
            self._chat_empty = QLabel("\u53d1\u9001\u6d88\u606f\u5f00\u59cb\u5bf9\u8bdd")
            self._chat_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._chat_empty.setStyleSheet(f"""
                font-size: 14px;
                color: {THEME.txt3};
                background: transparent;
                padding: 40px 20px;
            """)
            self._chat_layout.insertWidget(0, self._chat_empty)
        else:
            # Remove empty state if present
            self._remove_chat_empty()

        # Re-add filtered messages
        for m in filtered:
            role = m.get("role", "user")
            content = m.get("content", "")
            ts = m.get("timestamp", "")
            inst_id = m.get("instance_id", "")
            if content:
                self._add_message_widget(role, content, ts, inst_id, at_end=True)

    def _clear_chat_widgets_only(self):
        """Remove all message/render widgets from chat, preserving self._messages."""
        # Find and remove all message wrapper widgets (but not empty state which is recreated)
        items_to_remove = []
        for i in range(self._chat_layout.count()):
            item = self._chat_layout.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is None:
                continue
            # Remove message wrappers (QWidget containers with QHBoxLayout)
            if isinstance(w, QWidget) and w.layout() and isinstance(w.layout(), QHBoxLayout):
                items_to_remove.append(i)
            # Also remove the empty state label that we manage
            elif isinstance(w, QLabel):
                txt = w.text() if hasattr(w, 'text') else ""
                if txt in ("\u53d1\u9001\u6d88\u606f\u5f00\u59cb\u5bf9\u8bdd", "\u52a0\u8f7d\u4e2d..."):
                    items_to_remove.append(i)
            # Remove thinking container
            elif w.objectName() == "thinking_container":
                items_to_remove.append(i)

        # Remove in reverse order to preserve indices
        for idx in reversed(sorted(items_to_remove)):
            item = self._chat_layout.takeAt(idx)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()

        self._loading_container = None
        self._loading_bubble = None

    def _remove_chat_empty(self):
        """Remove the empty state label from chat layout."""
        for i in range(self._chat_layout.count()):
            item = self._chat_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), QLabel):
                w = item.widget()
                if w.text() == "\u53d1\u9001\u6d88\u606f\u5f00\u59cb\u5bf9\u8bdd":
                    self._chat_layout.takeAt(i)
                    w.deleteLater()
                    break

    # ------------------------------------------------------------------
    # Attachment Handling
    # ------------------------------------------------------------------

    def _on_attach_file(self):
        """Open file dialog to select files."""
        files, _ = QFileDialog.getOpenFileNames(
            self, "\u9009\u62e9\u6587\u4ef6", "",
            "\u6240\u6709\u6587\u4ef6 (*);;\u56fe\u7247 (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;\u6587\u672c (*.txt *.md *.json *.csv)"
        )
        for f in files:
            self._add_attachment(f)

    def _add_attachment(self, file_path: str):
        """Add a file to the attachment list."""
        if not os.path.isfile(file_path):
            return
        if file_path in self._attachments:
            return

        self._attachments.append(file_path)

        # Create attachment widget
        widget = AttachmentFileWidget(file_path)
        widget.remove_clicked.connect(self._remove_attachment)
        # Insert before the stretch
        self._attachment_list_layout.insertWidget(
            self._attachment_list_layout.count() - 1, widget
        )

        self._attachment_bar.setVisible(True)
        self._on_text_changed()

    def _remove_attachment(self, file_path: str):
        """Remove a file from the attachment list."""
        if file_path in self._attachments:
            self._attachments.remove(file_path)

        # Find and remove the widget
        for i in range(self._attachment_list_layout.count()):
            item = self._attachment_list_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                if isinstance(w, AttachmentFileWidget) and w._file_path == file_path:
                    self._attachment_list_layout.takeAt(i)
                    w.deleteLater()
                    break

        if not self._attachments:
            self._attachment_bar.setVisible(False)
        self._on_text_changed()

    def _clear_attachments(self):
        """Remove all attachments."""
        self._attachments.clear()
        # Clear all attachment widgets
        while self._attachment_list_layout.count() > 0:
            item = self._attachment_list_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._attachment_list_layout.addStretch()
        self._attachment_bar.setVisible(False)
        self._on_text_changed()

    # ------------------------------------------------------------------
    # Send / Input
    # ------------------------------------------------------------------

    def _update_send_button_style(self):
        """Update send button styling based on enabled state."""
        enabled = self._send_btn.isEnabled()
        text = self._input.toPlainText().strip()
        has_content = bool(text) or bool(self._attachments)
        self._send_btn.setEnabled(has_content)

        if has_content:
            self._send_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #5BA3E6, stop:1 {THEME.accent3});
                    color: white;
                    border: none;
                    border-radius: 10px;
                    padding: 8px 24px;
                    font-weight: bold;
                    font-size: 14px;
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
            """)
        else:
            self._send_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #CFD8DC, stop:1 #B0BEC5);
                    color: white;
                    border: none;
                    border-radius: 10px;
                    padding: 8px 24px;
                    font-size: 14px;
                    font-weight: bold;
                    min-height: 42px;
                }}
            """)

    def _on_text_changed(self):
        """Enable/disable send button based on input content and attachments."""
        self._update_send_button_style()

    def eventFilter(self, obj, event):
        from PySide6.QtGui import QKeyEvent
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key_event = QKeyEvent(event)
            if key_event.key() == Qt.Key.Key_Return and not (key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    def _load_instance_selector(self):
        """Populate the instance selector from the current workspace's global_config.json.

        Blocks the currentIndexChanged signal during rebuild so that
        _on_instance_changed is NOT triggered by setCurrentIndex(0).
        The caller (refresh_instance_selector / deferred init) is responsible
        for setting the final selection and triggering history reload.
        """
        # Block signals to prevent _on_instance_changed from firing
        self._instance_selector.blockSignals(True)
        self._instance_selector.clear()
        ws = self._workspace()
        # Instances are stored in global_config.json, not partner_config.json
        config_path = os.path.join(ws, "config", "global_config.json")
        if os.path.exists(config_path):
            config = _load_json(config_path)
            instances = config.get("instances", {})
            for inst_id in instances:
                env = _resolve_instance_env(inst_id)
                env_tag = {"wsl": "WSL", "local_windows": "Win", "local_linux": "Linux"}.get(env, env)
                label = f"{inst_id} [{env_tag}]"
                self._instance_selector.addItem(label, inst_id)
        # Fallback: scan filesystem
        if self._instance_selector.count() == 0:
            inst_dir = os.path.join(ws, "instances")
            if os.path.exists(inst_dir):
                for entry in sorted(os.listdir(inst_dir)):
                    if os.path.isdir(os.path.join(inst_dir, entry)):
                        self._instance_selector.addItem(entry, entry)

        if self._instance_selector.count() > 0:
            self._instance_selector.setCurrentIndex(
                self._instance_selector.count() - 1  # newest last
            )

        # Restore signal delivery
        self._instance_selector.blockSignals(False)

        # Ensure _on_instance_changed fires for the initial selection,
        # since blockSignals suppressed the setCurrentIndex signal.
        if self._instance_selector.count() > 0:
            self._on_instance_changed(self._instance_selector.currentIndex())

    def refresh_instance_selector(self):
        """Public method called by main window when instances change in other pages.

        Repopulates the dropdown. If a new instance was added (the dropdown
        has more items than before), auto-selects the newest one so the user
        doesn't have to manually switch.
        """
        old_count = self._instance_selector.count()
        self._load_instance_selector()
        new_count = self._instance_selector.count()
        # If new instances appeared, auto-select the newest (already done by
        # _load_instance_selector which selects the last item).
        # Otherwise restore previous selection.
        if new_count == old_count:
            current_data = self._instance_selector.currentData() or ""
            if current_data:
                idx = self._instance_selector.findData(current_data)
                if idx >= 0:
                    # _load_instance_selector already called _on_instance_changed,
                    # but we need to switch back to the old selection manually.
                    # Block temporarily to avoid a second history reload.
                    self._instance_selector.blockSignals(True)
                    self._instance_selector.setCurrentIndex(idx)
                    self._instance_selector.blockSignals(False)
                    self._selected_instance_id = current_data

    def _on_instance_changed(self, index: int):
        self._selected_instance_id = self._instance_selector.currentData() or ""

        # Stop any active polling
        self._poll_active = False
        self._pending_message_id = None
        self._poll_retries = 0
        self._auto_starting = False  # Reset for the new instance

        # Reload dialogue history for the new instance
        self._load_history()

        # Reload pipeline for the new instance
        self._poll_active_plan()

        # Check instance status and update display
        self._update_instance_status_display()

    def _update_instance_status_display(self):
        """Update the status display based on current instance status.
        Auto-starts the instance if it's configured for WSL and not running."""
        if not self._selected_instance_id:
            if hasattr(self, '_status_label'):
                self._status_label.setText("未选择实例")
                self._status_label.setStyleSheet(
                    f"font-size: 12px; color: {THEME.txt3}; background: transparent;"
                )
                self._status_label.setVisible(True)
            return

        running = self._check_instance_status()
        if hasattr(self, '_status_label'):
            if running:
                inst_name = self._selected_instance_id
                env = _resolve_instance_env(inst_name)
                env_tag = {"wsl": "WSL", "local_windows": "Win", "local_linux": "Linux"}.get(env, env)
                self._status_label.setText(f"● {inst_name} [{env_tag}] 运行中")
                self._status_label.setStyleSheet(
                    f"font-size: 12px; color: {THEME.green}; background: transparent;"
                )
                self._status_label.setVisible(True)
                self._auto_starting = False  # Clear auto-start lock
                return

        # Instance not running — try to auto-start
        self._auto_start_instance()

    def _auto_start_instance(self):
        """Auto-start the current instance using the system Python.

        In a frozen EXE, sys.executable == Partner.exe (the GUI).  Running
        Partner.exe -m partner would start another GUI, not the backend.
        Instead, use the system Python (which has the partner package
        installed via pip) to launch the backend process.
        """
        import subprocess as _sp
        inst_id = self._selected_instance_id
        if not inst_id:
            return

        # Prevent concurrent auto-start attempts
        if self._auto_starting:
            return
        self._auto_starting = True
        try:
            inst_dir = self._instance_dir(inst_id)
        except Exception:
            self._auto_starting = False
            return

        # Check if already running
        if self._check_instance_status():
            self._auto_starting = False
            return

        # ── Build command ─────────────────────────────────────────────────
        frozen = getattr(sys, 'frozen', False)
        if frozen:
            env_type = _resolve_instance_env(inst_id)
            if env_type == "wsl":
                # WSL instance — launch via wsl.exe on WSL's python3
                wsl_workspace = inst_dir.replace("\\", "/")
                cmd = ["wsl.exe", "-e", "python3", "-m", "partner",
                       "--instance-id", inst_id, "--workspace", wsl_workspace]
                launch_kwargs = {}
                try:
                    r = _sp.run(
                        ["wsl.exe", "-e", "python3", "-c",
                         "import partner; import os; "
                         "print(os.path.normpath(os.path.join(partner.__file__, '..', '..')))"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if r.returncode == 0:
                        wsl_root = r.stdout.strip()
                        if wsl_root.startswith("/mnt/"):
                            drive = wsl_root[5]
                            rest = wsl_root[6:].replace("/", "\\")
                            launch_kwargs["cwd"] = f"{drive}:{rest}"
                except Exception:
                    pass
            else:
                # local_windows or local_linux — run natively
                import shutil as _sh
                _python_exe = _sh.which("python.exe") or _sh.which("python") or "python"
                cmd = [_python_exe, "-m", "partner",
                       "--instance-id", inst_id, "--workspace", inst_dir]
                launch_kwargs = {}
                try:
                    r = _sp.run(
                        [_python_exe, "-c",
                         "import partner; import os; "
                         "print(os.path.normpath(os.path.join(partner.__file__, '..', '..')))"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=_sp.CREATE_NO_WINDOW,
                    )
                    if r.returncode == 0:
                        launch_kwargs["cwd"] = r.stdout.strip()
                except Exception:
                    pass
        else:
            cmd = [sys.executable, "-m", "partner",
                   "--instance-id", inst_id, "--workspace", inst_dir]
            launch_kwargs = {}
        try:
            log_path = os.path.join(inst_dir, "state", "record", "instance.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            log_file = open(log_path, "a", encoding="utf-8", errors="replace")
            _sp.Popen(
                cmd,
                stdout=log_file, stderr=_sp.STDOUT,
                creationflags=_sp.CREATE_NO_WINDOW if os.name == "nt" else 0,
                **launch_kwargs,
            )
            # Update status
            if hasattr(self, '_status_label'):
                self._status_label.setText(f"● {inst_id} 启动中...")
                self._status_label.setStyleSheet(
                    f"font-size: 12px; color: {THEME.yellow}; background: transparent;"
                )
                self._status_label.setVisible(True)
            # Keep retrying status check every 5s for up to 30s
            self._auto_start_retries = 0
            QTimer.singleShot(5000, self._retry_instance_status)
        except Exception:
            self._auto_starting = False
            if hasattr(self, '_status_label'):
                self._status_label.setText(f"● {inst_id} 未运行")
                self._status_label.setStyleSheet(
                    f"font-size: 12px; color: {THEME.red}; background: transparent;"
                )
                self._status_label.setVisible(True)

    def _retry_instance_status(self):
        """Check instance status after auto-start, retry up to 6 times (30s)."""
        self._auto_start_retries = getattr(self, '_auto_start_retries', 0) + 1
        if self._check_instance_status():
            # Instance is now running
            self._auto_starting = False
            if hasattr(self, '_status_label'):
                inst_name = self._selected_instance_id or ""
                env = _resolve_instance_env(inst_name)
                env_tag = {"wsl": "WSL", "local_windows": "Win", "local_linux": "Linux"}.get(env, env)
                self._status_label.setText(f"● {inst_name} [{env_tag}] 运行中")
                self._status_label.setStyleSheet(
                    f"font-size: 12px; color: {THEME.green}; background: transparent;"
                )
                self._status_label.setVisible(True)
            return

        # Still not running — retry or give up
        if self._auto_start_retries >= 6:  # 30 seconds max
            self._auto_starting = False
            # Try to read the last lines of instance.log for diagnosis
            error_hint = "启动超时"
            inst_dir = self._instance_dir(self._selected_instance_id or "")
            log_path = os.path.join(inst_dir, "state", "record", "instance.log")
            if os.path.exists(log_path):
                try:
                    lines = open(log_path, "r", encoding="utf-8", errors="replace").read().splitlines()
                    # Show last non-empty lines (max 2) that look like errors
                    err_lines = [l.strip() for l in lines if l.strip() and
                                 any(kw in l.lower() for kw in ("error", "traceback", "exception", "failed", "无法"))]
                    if err_lines:
                        error_hint = err_lines[-1][:60]
                    elif lines:
                        # Fall back to last line of log
                        last = lines[-1].strip()[:60]
                        if last:
                            error_hint = last
                except Exception:
                    pass
            if hasattr(self, '_status_label'):
                self._status_label.setText(f"● {error_hint}")
                self._status_label.setStyleSheet(
                    f"font-size: 12px; color: {THEME.red}; background: transparent;"
                )
                self._status_label.setVisible(True)
            return

        QTimer.singleShot(5000, self._retry_instance_status)

    def send_test_message(self):
        """Send a test message to verify the chat pipeline end-to-end."""
        test_text = f"[test] 测试消息 {datetime.now().strftime('%H:%M:%S')}"
        self._add_message("user", test_text, timestamp=datetime.now().isoformat(),
                          instance_id=self._selected_instance_id or "", source="gui")
        # Also trigger a simulated assistant reply after a short delay
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1000, lambda: self._add_message(
            "assistant",
            f"这是自动回复的测试消息，收到时间: {datetime.now().strftime('%H:%M:%S')}",
            timestamp=datetime.now().isoformat(),
            instance_id=self._selected_instance_id or "",
            source="gui"
        ))

    def _workspace(self) -> str:
        """Return the current workspace path, falling back to the global default."""
        return self._workspace_path or str(resolve_partner_root())

    def _instance_dir(self, instance_id: str) -> str:
        """Resolve an instance directory under the current workspace."""
        return os.path.join(self._workspace(), "instances", instance_id)

    def _inbox_path(self, instance_id: str) -> str:
        inst_dir = self._instance_dir(instance_id)
        return os.path.join(inst_dir, "state", "desktop_inbox.jsonl")

    def _active_plan_path(self, instance_id: str) -> str:
        inst_dir = self._instance_dir(instance_id)
        return os.path.join(inst_dir, "state", "active_plan.json")

    def _on_send(self):
        """Send message + attachments to the selected instance."""
        # Stop viewing historical pipeline — switch back to live mode
        self._showing_historical_pipeline = False

        text = self._input.toPlainText().strip()
        if not text and not self._attachments:
            return

        instance_id = self._instance_selector.currentData()
        if not instance_id:
            if self._instance_selector.count() > 0:
                instance_id = self._instance_selector.currentData()
            else:
                return

        # Build event with attachments
        msg_id = f"gui_{uuid.uuid4().hex[:12]}"
        event = {
            "id": msg_id,
            "text": text,
            "source": "gui",
            "sender_id": "desktop_gui",
            "attachments": [
                {"path": f, "name": os.path.basename(f)} for f in self._attachments
            ],
            "created_at": datetime.now().isoformat(),
        }

        # Write to inbox
        inbox_path = self._inbox_path(instance_id)
        try:
            os.makedirs(os.path.dirname(inbox_path), exist_ok=True)
            with open(inbox_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            return

        # Track for reply
        self._pending_message_id = msg_id
        self._poll_start_time = datetime.now().timestamp()
        self._poll_active = True
        self._seen_responses.clear()

        # Show user message with attachments
        display_text = text
        if self._attachments:
            file_info = "\n".join(f"\U0001f4ce {os.path.basename(f)}" for f in self._attachments)
            if display_text:
                display_text += "\n" + file_info
            else:
                display_text = file_info
        self._add_message("user", display_text, instance_id=instance_id, source="gui")

        self._input.clear()
        self._clear_attachments()
        self._update_send_button_style()

        # Show loading placeholder
        self._show_loading_placeholder()

    # ------------------------------------------------------------------
    # Chat Messages
    # ------------------------------------------------------------------

    def _show_loading_placeholder(self):
        """Insert a '思考中...' placeholder message."""
        self._remove_loading_placeholder()

        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        thinking_bubble = ChatBubble("\u601d\u8003\u4e2d... (0s)", role="assistant", timestamp="")
        thinking_bubble.setObjectName("thinking_bubble")
        wrapper.addWidget(thinking_bubble)
        wrapper.addStretch()

        container = QWidget()
        container.setLayout(wrapper)
        container.setStyleSheet("background: transparent;")
        container.setObjectName("thinking_container")

        self._chat_layout.addWidget(container)
        self._loading_container = container
        self._loading_bubble = thinking_bubble

    def _remove_loading_placeholder(self):
        """Remove the '思考中...' placeholder."""
        if hasattr(self, '_loading_container') and self._loading_container:
            try:
                idx = self._chat_layout.indexOf(self._loading_container)
                if idx >= 0:
                    item = self._chat_layout.takeAt(idx)
                    if item and item.widget():
                        item.widget().deleteLater()
            except Exception:
                pass
            self._loading_container = None
            self._loading_bubble = None

    def _replace_loading_with_reply(self, content: str, timestamp: str = "", instance_id: str = ""):
        """Replace the '思考中...' placeholder with actual assistant reply."""
        self._remove_loading_placeholder()
        self._poll_active = False
        self._pending_message_id = None

        self._add_message("assistant", content, timestamp, instance_id)

    def _update_loading_elapsed(self, elapsed: int):
        """Update the loading placeholder with elapsed time."""
        if hasattr(self, '_loading_bubble') and self._loading_bubble:
            self._loading_bubble.set_content(f"\u601d\u8003\u4e2d... ({elapsed}s)")

    def _add_message(self, role: str, content: str, timestamp: str = "",
                     instance_id: str = "", source: str = "",
                     pipeline_path: str = ""):
        """Add a message bubble to the chat area. Detects filenames for clickable links.

        Also stores the message in self._messages and re-applies source filter.
        """
        ts_display = _format_timestamp(timestamp) if timestamp else ""

        # Detect file references in assistant messages
        file_path = ""
        if role != "user" and content:
            ext = Path(content).suffix.lower()
            if ext in (".pdf", ".csv", ".json", ".md", ".png", ".jpg", ".txt", ".log", ".yaml", ".toml"):
                fname = content.split("\n")[0].strip()
                found = self._find_instance_file(fname, instance_id)
                if found:
                    file_path = found

        # Store message
        msg_entry = {
            "role": role,
            "content": content,
            "timestamp": timestamp,
            "instance_id": instance_id,
            "source": source,
            "pipeline_path": pipeline_path,
        }
        self._messages.append(msg_entry)

        # If source filter is active, check if this message should be displayed
        filter_val = self._source_filter
        src = source
        should_display = True
        if filter_val != "全部":
            if filter_val == "GUI" and src not in ("gui", ""):
                should_display = False
            elif filter_val == "QQ" and src != "qq":
                should_display = False
            elif filter_val == "CLI" and src != "cli":
                should_display = False

        if should_display:
            self._remove_chat_empty()
            self._add_message_widget(role, content, ts_display, instance_id,
                                     file_path=file_path, at_end=True, pipeline_path=pipeline_path)

    def _add_message_widget(self, role: str, content: str, ts_display: str,
                            instance_id: str = "", file_path: str = "",
                            at_end: bool = True, pipeline_path: str = ""):
        """Create and add a ChatBubble widget to the chat layout."""
        # Use local file_path if not passed
        if not file_path and role != "user" and content:
            ext = Path(content).suffix.lower()
            if ext in (".pdf", ".csv", ".json", ".md", ".png", ".jpg", ".txt", ".log", ".yaml", ".toml"):
                fname = content.split("\n")[0].strip()
                found = self._find_instance_file(fname, instance_id)
                if found:
                    file_path = found

        bubble = ChatBubble(content, role=role, timestamp=ts_display, file_path=file_path)

        # Store pipeline_path on bubble for mousePressEvent lookup
        bubble._pipeline_path = pipeline_path

        # Add right-click context menu
        bubble.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        source_text = content[:80] + ("..." if len(content) > 80 else "")
        bubble.customContextMenuRequested.connect(
            lambda pos, c=content, i=instance_id: self._show_message_context_menu(pos, c, i, bubble)
        )

        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        if role == "user":
            wrapper.addStretch()
            wrapper.addWidget(bubble)
        else:
            wrapper.addWidget(bubble)
            wrapper.addStretch()

        container = QWidget()
        container.setLayout(wrapper)
        container.setStyleSheet("background: transparent;")

        if at_end:
            self._chat_layout.addWidget(container)
        else:
            self._chat_layout.insertWidget(0, container)

    def _show_message_context_menu(self, pos, content: str, instance_id: str, bubble: QWidget):
        """Show right-click context menu for a message bubble."""
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {THEME.bg2};
                color: {THEME.txt};
                border: 1px solid {THEME.border};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 8px 20px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {THEME.bg3};
                color: {THEME.accent};
            }}
        """)

        copy_action = QAction("\u590d\u5236\u6d88\u606f", self)
        copy_action.triggered.connect(lambda: self._copy_message(content))
        menu.addAction(copy_action)

        copy_id_action = QAction(f"\u590d\u5236 ID: {instance_id}", self)
        copy_id_action.triggered.connect(lambda: self._copy_message(instance_id))
        menu.addAction(copy_id_action)

        menu.exec(bubble.mapToGlobal(pos))

    def _copy_message(self, text: str):
        """Copy text to clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

    def _find_instance_file(self, filename: str, instance_id: str) -> str:
        """Search for a file in the instance's state directories.
        Returns the absolute path if found, empty string otherwise."""
        if not instance_id or not filename:
            return ""
        workspace = self._workspace()
        inst_dir = Path(workspace) / "instances" / instance_id
        if not inst_dir.is_dir():
            return ""
        # Search in state/user/reports, state/tasks, and system/hermes_work
        search_roots = [
            inst_dir / "state" / "user" / "reports",
            inst_dir / "state" / "tasks",
            inst_dir / "system" / "hermes_work",
        ]
        for root in search_roots:
            if root.is_dir():
                for found in root.rglob(filename):
                    if found.is_file():
                        return str(found)
        return ""

    def _clear_chat(self):
        """Remove all message widgets from the chat area."""
        while self._chat_layout.count() > 0:
            item = self._chat_layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is None:
                continue
            w.deleteLater()
        self._messages.clear()
        self._loading_container = None
        self._loading_bubble = None

    def _check_instance_status(self) -> bool:
        """Check if the target instance is running (supports WSL cross-platform).

        Three detection methods:
        1. PID file + os.kill(pid, 0) — works for Windows-native PIDs
        2. heartbeat.json stamp within 180s — works for WSL instances (cross-platform)
        3. qq_bot.pid fallback — QQ bridge PID check
        """
        inst_id = self._selected_instance_id or self._instance_selector.currentData()
        if not inst_id:
            return False
        inst_dir = self._instance_dir(inst_id)

        # Method 1: PID file check
        pid = None
        pid_path = os.path.join(inst_dir, "instance.pid")
        if os.path.exists(pid_path):
            try:
                pid = int(Path(pid_path).read_text().strip())
            except Exception:
                pid = None

        if pid is not None:
            try:
                os.kill(pid, 0)
                return True
            except (OSError, PermissionError):
                # WSL PIDs are NOT visible from Windows — os.kill fails with
                # WinError 87 even when the process is alive. Fall through to
                # heartbeat check instead of giving up.
                pass

        # Method 2: heartbeat.json staleness (works cross-platform on shared FS)
        heartbeat_path = os.path.join(inst_dir, "state", "heartbeat.json")
        if os.path.exists(heartbeat_path):
            try:
                with open(heartbeat_path, "r", encoding="utf-8") as _hb_f:
                    _hb = json.load(_hb_f)
                ts = _hb.get("last_heartbeat", "")
                if ts:
                    dt = datetime.fromisoformat(ts)
                    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                    if (now - dt).total_seconds() < 180:
                        return True
            except Exception:
                pass

        # Method 3: qq_bot.pid (QQ bridge fallback)
        qq_pid_path = os.path.join(inst_dir, "state", "qq_bot.pid")
        if os.path.exists(qq_pid_path):
            try:
                pid2 = int(Path(qq_pid_path).read_text().strip())
                os.kill(pid2, 0)
                return True
            except (OSError, PermissionError):
                pass

        return False

    def _show_start_button(self):
        """Show a '启动实例' button and status label."""
        self._status_label.setText("\u25cf \u5b9e\u4f8b\u672a\u8fd0\u884c")
        self._status_label.setStyleSheet(
            f"font-size: 12px; padding: 4px 8px; background: transparent; color: {THEME.red};"
        )
        self._status_label.setVisible(True)

    def _show_error(self, msg: str):
        """Show an error as an assistant message bubble."""
        self._add_message("assistant", f"[{msg}]", datetime.now().isoformat(),
                          self._selected_instance_id or "")
        self._scroll_to_bottom()

    def _load_history(self):
        """Load newest PAGE_SIZE turns for the selected instance."""
        self._clear_chat()
        self._load_offset = 0
        self._showing_historical_pipeline = False  # Reset to live mode on history reload

        # Show loading indicator
        loading = QLabel("加载中...")
        loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading.setStyleSheet(
            f"font-size: 13px; color: {THEME.txt3}; background: transparent; padding: 20px;"
        )
        self._chat_layout.addWidget(loading)
        QApplication.processEvents()

        self._workspace_path = self._workspace_path or self._workspace()
        inst_id = self._selected_instance_id or ""
        turns = _load_dialogue_turns(self._workspace_path, inst_id, offset=0, limit=self._page_size)

        # Remove loading indicator
        idx = self._chat_layout.indexOf(loading)
        if idx >= 0:
            item = self._chat_layout.takeAt(idx)
            if item and item.widget():
                item.widget().deleteLater()

        self._load_offset = len(turns)

        # Determine source for history entries
        for turn in turns:
            if "source" not in turn:
                turn["source"] = "gui"

        # Display in chronological order: oldest first → newest at bottom
        for turn in turns:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            ts = turn.get("timestamp", "")
            inst_id = turn.get("instance_id", "")
            source = turn.get("source", "gui")
            pipeline_path = turn.get("pipeline_path", "")
            if content:
                self._add_message(role, content, ts, inst_id, source=source, pipeline_path=pipeline_path)

        # Check instance status
        if not self._check_instance_status():
            self._show_start_button()

        self._scroll_to_bottom()

    def _on_scroll_changed(self, value: int):
        """Detect scroll-to-top to load older messages from file."""
        if self._loading_more:
            return
        scrollbar = self._scroll_area.verticalScrollBar()
        if value == scrollbar.minimum() and self._load_offset > 0:
            self._loading_more = True
            # At top — load next batch of older entries from file
            more = _load_dialogue_turns(
                self._workspace_path, self._selected_instance_id or "",
                offset=self._load_offset, limit=self._page_size
            )
            if not more:
                return
            # Insert in reverse at TOP of chat (so oldest appear above)
            for turn in reversed(more):
                role = turn.get("role", "user")
                content = turn.get("content", "")
                ts = turn.get("timestamp", "")
                inst_id = turn.get("instance_id", "")
                source = turn.get("source", "gui")
                if content:
                    ts_display = _format_timestamp(ts) if ts else ""
                    file_path = ""
                    if role != "user" and content:
                        ext = Path(content).suffix.lower()
                        if ext in (".pdf", ".csv", ".json", ".md", ".png", ".jpg", ".txt", ".log", ".yaml", ".toml"):
                            fname = content.split("\n")[0].strip()
                            found = self._find_instance_file(fname, inst_id)
                            if found:
                                file_path = found

                    # Store in messages list
                    pipeline_path = turn.get("pipeline_path", "")
                    self._messages.insert(0, {
                        "role": role, "content": content,
                        "timestamp": ts, "instance_id": inst_id,
                        "source": source,
                        "pipeline_path": pipeline_path,
                    })

                    # Only add widget if it passes the source filter
                    filter_val = self._source_filter
                    should_display = True
                    if filter_val != "全部":
                        if filter_val == "GUI" and source not in ("gui", ""):
                            should_display = False
                        elif filter_val == "QQ" and source != "qq":
                            should_display = False
                        elif filter_val == "CLI" and source != "cli":
                            should_display = False

                    if should_display:
                        self._add_message_widget(role, content, ts_display,
                                                 inst_id, file_path=file_path, at_end=False,
                                                 pipeline_path=pipeline_path)

            self._load_offset += len(more)
            self._loading_more = False

    def set_workspace(self, ws: str) -> None:
        """Called when the workspace path changes in settings. Reloads everything."""
        self._workspace_path = ws
        self._messages: list[dict] = []
        self._polled_message_ids: set[str] = set()
        self._seen_responses: set[tuple[str, str]] = set()
        self._pending_message_id: str | None = None
        self._selected_instance_id = ""
        self._showing_historical_pipeline = False

        # Clear pipeline panel
        for w in self._event_step_widgets:
            self._pipeline_steps_layout.removeWidget(w)
            w.deleteLater()
        self._event_step_widgets = []
        self._pipeline_empty.setVisible(True)
        self._pipeline_planning.setVisible(False)
        self._pipeline_progress.setValue(0)
        self._pipeline_progress.setVisible(False)
        self._pipeline_progress_label.setText("")
        self._plan_name_label.setText("")
        self._pipeline_mode_label.setVisible(False)

        self._load_instance_selector()
        self._load_history()
        QTimer.singleShot(200, self._update_instance_status_display)
        QTimer.singleShot(300, self._poll_active_plan)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_new_messages(self):
        """Poll for new replies from instances with retry and instance health check."""
        if self._poll_active and self._pending_message_id:
            elapsed = int(datetime.now().timestamp() - self._poll_start_time)
            self._update_loading_elapsed(elapsed)

            # While waiting for the final reply, also show progress messages
            # so the user sees "[进度] 正在 1/6：web_search" instead of just
            # "思考中... (Xs)" for 120 seconds.
            inst_id = self._selected_instance_id or ""
            if inst_id:
                inst_dir = os.path.join(self._workspace(), "instances", inst_id)
                qq_path = os.path.join(inst_dir, "state", "qq_chat_history.jsonl")
                if os.path.exists(qq_path):
                    rows = _load_jsonl(qq_path, n=10)
                    latest_ts = ""
                    for m in self._messages:
                        mt = str(m.get("timestamp") or "")
                        if mt > latest_ts:
                            latest_ts = mt
                    for row in reversed(rows):
                        role = row.get("role", "")
                        if role not in ("assistant", "partner"):
                            continue
                        content = row.get("content", row.get("text", ""))
                        if not content:
                            continue
                        ts = str(row.get("timestamp") or row.get("created_at") or "")
                        if ts <= latest_ts:
                            continue
                        if "[进度]" in str(content) or "[EVENT]" in str(content):
                            dedup_key = (ts, content[:50])
                            if dedup_key in self._seen_responses:
                                continue
                            self._seen_responses.add(dedup_key)
                            self._add_message("assistant", content, ts, inst_id, source="qq")

            if elapsed > 120:
                bot_help = ("[响应超时 - 未收到回复]\n"
                           "请确认 Partner Bot 正在运行（在 WSL 中执行 partner bot start）")
                self._replace_loading_with_reply(
                    bot_help,
                    datetime.now().isoformat(),
                    self._selected_instance_id or ""
                )
                self._pending_message_id = None
                self._poll_retries = 0
                return

        if not self._poll_active or not self._pending_message_id:
            # Background poll for new messages even when not actively waiting
            # ONLY poll messages newer than the latest known timestamp to avoid
            # re-adding historical messages out of chronological order.
            workspace = self._workspace()
            try:
                # Find the latest timestamp among current messages
                latest_ts = ""
                for m in self._messages:
                    mt = str(m.get("timestamp") or "")
                    if mt > latest_ts:
                        latest_ts = mt

                # Only poll the currently selected instance, not all instances.
                # Otherwise, switching instances shows a mix of all histories.
                target_instances: list[str] = []
                selected = self._selected_instance_id
                if selected:
                    target_instances = [selected]
                else:
                    # Fall back to all instances (e.g. during initial load)
                    for d in sorted(Path(workspace).glob("instances/*"), reverse=True):
                        target_instances.append(d.name)

                for inst_id in target_instances:
                    instance_dir = Path(workspace) / "instances" / inst_id

                    # Check qq_chat_history.jsonl
                    qq_path = os.path.join(str(instance_dir), "state", "qq_chat_history.jsonl")
                    if os.path.exists(qq_path):
                        rows = _load_jsonl(qq_path, n=20)
                        for row in reversed(rows):
                            role = row.get("role", "")
                            if role not in ("assistant", "partner"):
                                continue
                            content = row.get("content", row.get("text", ""))
                            if not content:
                                continue
                            if "思考中" in str(content):
                                continue
                            ts = str(row.get("timestamp") or row.get("created_at") or "")
                            # Skip messages not newer than the latest already shown
                            if ts <= latest_ts:
                                continue
                            dedup_key = (ts, content[:50])
                            if dedup_key in self._seen_responses:
                                continue
                            self._seen_responses.add(dedup_key)
                            already = False
                            for m in self._messages:
                                if m.get("content") == content and m.get("role") in ("assistant", "partner"):
                                    already = True
                                    break
                            if not already:
                                self._add_message("assistant", content, ts, inst_id, source="qq")

                    # Also check .log files for new assistant messages
                    dialogue_dir = instance_dir / "dialogue"
                    if dialogue_dir.is_dir():
                        for log_file in sorted(dialogue_dir.glob("*.log"), reverse=True)[:1]:
                            log_ts = log_file.stem  # YYYY-MM-DD
                            parsed = _parse_log_file(str(log_file))
                            for entry in parsed[:10]:
                                role = entry.get("role", "assistant")
                                content = str(entry.get("content", "") or "")
                                if not content:
                                    continue
                                if "思考中" in str(content):
                                    continue
                                if content.startswith("已停止「"):
                                    continue
                                ts = entry.get("timestamp", log_ts)
                                if ts <= latest_ts:
                                    continue
                                dedup_key = (ts, content[:50])
                                if dedup_key in self._seen_responses:
                                    continue
                                self._seen_responses.add(dedup_key)
                                already = False
                                for m in self._messages:
                                    if m.get("content") == content and m.get("role") in (role, "assistant"):
                                        already = True
                                        break
                                if not already:
                                    self._add_message(role, content, ts, inst_id, source="qq")
            except Exception:
                pass
            return

        # Active polling: try to find reply — only check selected instance
        workspace = self._workspace()
        try:
            selected_id = self._selected_instance_id
            if selected_id:
                target_poll = [selected_id]
            else:
                target_poll = [d.name for d in sorted(Path(workspace).glob("instances/*"), reverse=True)]

            for inst_id in target_poll:
                instance_dir = Path(workspace) / "instances" / inst_id

                # When actively waiting for a reply, only accept messages that are
                # explicitly tied to our pending message (reply_to match) OR were
                # created after we sent our message (timestamp newer than poll start).
                # This prevents stale assistant messages (e.g. "已停止「xxx」" internal
                # notices that lack reply_to) from being treated as responses.

                # Check qq_chat_history.jsonl
                qq_path = os.path.join(str(instance_dir), "state", "qq_chat_history.jsonl")
                if os.path.exists(qq_path):
                    rows = _load_jsonl(qq_path, n=20)
                    for row in reversed(rows):
                        role = row.get("role", "")
                        if role not in ("assistant", "partner"):
                            continue
                        content = row.get("content", row.get("text", ""))
                        if not content:
                            continue
                        if "思考中" in str(content):
                            continue

                        reply_to = row.get("reply_to", "")
                        ts = str(row.get("timestamp") or row.get("created_at") or "")

                        # Must be either reply_to match or clearly newer than our send time
                        if self._pending_message_id:
                            if reply_to == self._pending_message_id:
                                pass  # explicit reply — accept
                            elif ts:
                                # Timestamp must be > poll_start_time to qualify
                                try:
                                    msg_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                    msg_ts = msg_dt.timestamp()
                                    if msg_ts <= self._poll_start_time:
                                        continue
                                except Exception:
                                    continue  # can't parse timestamp, skip
                            else:
                                continue  # no reply_to and no timestamp — skip

                        dedup_key = (ts, content[:50])
                        if dedup_key in self._seen_responses:
                            continue
                        self._seen_responses.add(dedup_key)

                        already = False
                        for m in self._messages:
                            if m.get("content") == content and m.get("role") in ("assistant", "partner"):
                                already = True
                                break
                        if not already:
                            self._replace_loading_with_reply(content, ts, inst_id)
                            self._pending_message_id = None
                            self._poll_retries = 0
                            return

                # Also check dialog_history.jsonl
                from partner.workspace.workspace_layout import history_paths
                for path in history_paths(str(instance_dir), "dialog_history.jsonl"):
                    if not os.path.exists(path):
                        continue
                    rows = _load_jsonl(path, n=20)
                    for row in reversed(rows):
                        role = row.get("role", "")
                        if role not in ("assistant", "partner"):
                            continue
                        content = row.get("content", row.get("text", ""))
                        if not content:
                            continue
                        if "思考中" in str(content):
                            continue

                        reply_to = row.get("reply_to", "")
                        ts = str(row.get("timestamp") or row.get("created_at") or "")

                        # Same timestamp/reply_to filtering as above
                        if self._pending_message_id:
                            if reply_to == self._pending_message_id:
                                pass
                            elif ts:
                                try:
                                    msg_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                    msg_ts = msg_dt.timestamp()
                                    if msg_ts <= self._poll_start_time:
                                        continue
                                except Exception:
                                    continue
                            else:
                                continue

                        dedup_key = (ts, content[:50])
                        if dedup_key in self._seen_responses:
                            continue
                        self._seen_responses.add(dedup_key)

                        already = False
                        for m in self._messages:
                            if m.get("content") == content and m.get("role") in ("assistant", "partner"):
                                already = True
                                break
                        if not already:
                            self._replace_loading_with_reply(content, ts, inst_id)
                            self._pending_message_id = None
                            self._poll_retries = 0
                            return

                # Also check .log files (newest entries at front for prepend format)
                try:
                    dialogue_dir = instance_dir / "dialogue"
                    if dialogue_dir.is_dir():
                        log_files = sorted(dialogue_dir.glob("*.log"), reverse=True)
                        for log_file in log_files[:1]:
                            parsed = _parse_log_file(str(log_file))
                            for row in parsed[:5]:
                                content = row.get("content", "")
                                if not content:
                                    continue
                                role = row.get("role", "assistant")
                                ts = row.get("timestamp", "")
                                # Only accept entries with timestamps newer than poll start
                                if ts and self._poll_start_time > 0:
                                    try:
                                        msg_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                        msg_ts = msg_dt.timestamp()
                                        if msg_ts <= self._poll_start_time:
                                            continue
                                    except Exception:
                                        continue
                                # Check for duplicates
                                already = False
                                for m in self._messages:
                                    if m.get("content") == content and m.get("role") == role:
                                        already = True
                                        break
                                if not already:
                                    if role in ("assistant", "partner"):
                                        self._replace_loading_with_reply(content, ts, inst_id)
                                        self._pending_message_id = None
                                        self._poll_retries = 0
                                        return
                                    elif role == "user":
                                        self._add_message("user", content, timestamp=ts,
                                                          instance_id=inst_id, source="qq")
                except Exception:
                    pass
        except Exception:
            pass

        # If we're actively polling and reached here, check instance health
        if self._poll_active and self._pending_message_id:
            if not self._check_instance_status():
                self._poll_retries += 1
                if self._poll_retries >= 3:  # 3 retries = ~6 seconds
                    self._replace_loading_with_reply(
                        "[\u5b9e\u4f8b\u672a\u8fd0\u884c\uff0c\u65e0\u6cd5\u5904\u7406\u6d88\u606f]",
                        datetime.now().isoformat(),
                        self._selected_instance_id or ""
                    )
                    self._show_start_button()
                    self._pending_message_id = None
                    self._poll_retries = 0
            else:
                # Instance is running, reset retry count
                self._poll_retries = 0

    # ------------------------------------------------------------------
    # Execution Pipeline
    # ------------------------------------------------------------------

    def _poll_active_plan(self):
        """Poll active_plan.json and update EventStepWidgets + artifacts."""
        workspace = self._workspace()
        instance_id = self._selected_instance_id or ""

        # Check instance status first — but still attempt to read active_plan
        # even if status check fails.
        if instance_id:
            # NOTE: Do NOT call _update_instance_status_display here.
            # It would restart _auto_start_instance every time the poll timer
            # fires after a timeout, creating an infinite start→timeout→start
            # loop ("来回切换").  Status is updated only on instance selection
            # changes (_on_instance_changed) and the auto-start retry chain
            # (_retry_instance_status).
            pass

        instances_to_check = [instance_id] if instance_id else []
        if not instances_to_check:
            try:
                ws = self._workspace()
                cfg_path = os.path.join(ws, "config", "partner_config.json")
                config = _load_json(cfg_path)
                instances_to_check = list(config.get("instances", {}).keys())
            except Exception:
                return

        found_active = False
        # Skip polling when viewing a historical pipeline snapshot
        if self._showing_historical_pipeline:
            return
        for inst_id in instances_to_check:
            inst_dir = self._instance_dir(inst_id)
            plan_path = os.path.join(inst_dir, "state", "active_plan.json")
            if os.path.exists(plan_path):
                plan = _load_json(plan_path)
                if plan and plan.get("status") and plan.get("status") not in ("idle",):
                    # Skip stale plans that have been sitting at "active" with all
                    # phases pending for >5 min (zombie from a previous run).
                    plan_status = plan.get("status", "")
                    phases = plan.get("phases", []) or plan.get("events", []) or plan.get("steps", [])
                    if plan_status != "planning" and phases and all(
                        p.get("status", "pending") == "pending" for p in phases
                    ):
                        hb = plan.get("last_heartbeat", plan.get("created_at", ""))
                        if hb:
                            try:
                                hb_dt = datetime.fromisoformat(hb)
                                now_dt = datetime.now(hb_dt.tzinfo) if hb_dt.tzinfo else datetime.now()
                                if (now_dt - hb_dt).total_seconds() > 300:  # 5 min
                                    continue  # stale — skip
                            except Exception:
                                pass
                    found_active = True
                    self._update_pipeline(plan)
                    break

        if not found_active:
            self._set_pipeline_idle()

    def _load_pipeline_snapshot(self, pipeline_path: str):
        """Load a pipeline snapshot from a conversation round into the right-side display.
        Falls back to the current active_plan.json if the snapshot doesn't exist.
        Stops the plan timer so the user sees the pipeline update clearly."""
        # Pause the live poll timer so our update is visible
        if hasattr(self, '_plan_timer') and self._plan_timer.isActive():
            self._plan_timer.stop()
        
        plan = None
        is_snapshot = bool(pipeline_path)
        if is_snapshot:
            try:
                import json as _json
                with open(pipeline_path, "r", encoding="utf-8") as f:
                    plan = _json.load(f)
            except Exception:
                pass
        
        if plan is None:
            # Fallback: load current active_plan.json
            instance_id = self._selected_instance_id or ""
            if instance_id:
                inst_dir = self._instance_dir(instance_id)
                plan_path = os.path.join(inst_dir, "state", "active_plan.json")
                try:
                    with open(plan_path, "r", encoding="utf-8") as f:
                        plan = json.load(f)
                except Exception:
                    pass
        
        if isinstance(plan, dict):
            self._showing_historical_pipeline = is_snapshot
            self._pipeline_empty.setVisible(False)
            self._update_pipeline(plan)
            # Show mode label
            mode_text = "📋 历史快照" if is_snapshot else "📋 当前计划"
            if hasattr(self, '_pipeline_mode_label'):
                self._pipeline_mode_label.setText(mode_text)
                self._pipeline_mode_label.setVisible(True)

    def _back_to_live(self):
        """Switch back to live pipeline view."""
        self._showing_historical_pipeline = False
        if hasattr(self, '_pipeline_mode_label'):
            self._pipeline_mode_label.setVisible(False)
        # Resume polling
        if hasattr(self, '_plan_timer') and not self._plan_timer.isActive():
            self._plan_timer.start(2000)
        self._poll_active_plan()

    def _update_pipeline(self, plan: dict):
        """Update the pipeline display with EventStepWidgets and artifacts."""
        status = plan.get("status", "idle")
        plan_name = plan.get("name") or plan.get("plan_name") or plan.get("title", "")
        if plan_name:
            self._plan_name_label.setText(plan_name)
            self._plan_name_label.setToolTip(plan_name)
        else:
            self._plan_name_label.setText("")

        # Hide empty state
        self._pipeline_empty.setVisible(False)

        # Get events/steps
        events = plan.get("events", []) or plan.get("steps", []) or plan.get("phases", [])

        # If in active/planning state but no concrete steps yet, show a "规划中..." indicator
        if status in ("planning", "active") and not events:
            self._pipeline_progress.setVisible(False)
            self._pipeline_progress_label.setText("")
            # Show planning indicator
            self._pipeline_planning.setVisible(True)
            self._pipeline_planning.setText(f"🔄 规划中... ({plan.get('goal','')[:50]})")
            self._pipeline_planning.setToolTip(plan.get("goal", ""))
            # Hide all event step widgets
            for widget in self._event_step_widgets:
                widget.setVisible(False)
            return

        # Hide planning indicator when we have steps
        self._pipeline_planning.setVisible(False)

        # Update progress bar
        total = len(events)
        completed = sum(1 for ev in events if ev.get("status") in ("success", "completed", "failed", "error"))
        if total > 0:
            self._pipeline_progress.setVisible(True)
            pct = int((completed / total) * 100)
            self._pipeline_progress.setValue(pct)
            self._pipeline_progress.setFormat(f"\u5df2\u5b8c\u6210 {completed}/{total} \u6b65")
            self._pipeline_progress_label.setText(f"{pct}%")
        else:
            self._pipeline_progress.setVisible(False)
            self._pipeline_progress_label.setText("")

        # Get or create EventStepWidgets
        while len(self._event_step_widgets) < len(events):
            widget = EventStepWidget()
            self._event_step_widgets.append(widget)
            self._pipeline_steps_layout.addWidget(widget)

        # Hide excess widgets
        for i in range(len(events), len(self._event_step_widgets)):
            self._event_step_widgets[i].setVisible(False)

        # Update visible widgets
        for i, ev in enumerate(events):
            s = ev.get("status", "pending")
            if s == "success":
                s = "completed"
            action = ev.get("action", ev.get("type", ev.get("name",
                       ev.get("event_type", ev.get("summary", f"\u6b65\u9aa4 {i+1}")))))
            elapsed = ev.get("elapsed", "")
            agent = ev.get("agent", "")

            # Build detailed step data for EventStepWidget
            step_data = {
                "number": i + 1,
                "action": action,
                "status": s,
                "elapsed": elapsed,
                "event_type": ev.get("event_type", ""),
                "key": ev.get("input_summary", ev.get("key", ev.get("query", ""))),
                "output": ev.get("output_summary", ev.get("output", ev.get("result", ""))),
                "error": ev.get("error", ""),
            }

            if i < len(self._event_step_widgets):
                widget = self._event_step_widgets[i]
                widget.set_step(step_data)
                widget.setVisible(True)

        # Update artifacts
        artifacts = plan.get("artifacts", []) or plan.get("files", [])
        if artifacts:
            self._update_artifacts(artifacts)
        else:
            # Check for artifact fields directly in events
            art_list = []
            for ev in events:
                for key in ("artifacts", "files", "outputs"):
                    items = ev.get(key, [])
                    if isinstance(items, list):
                        art_list.extend(items)
            if art_list:
                self._update_artifacts(art_list)
            else:
                self._artifacts_section.setVisible(False)
        
        # Update summary
        summary_text = plan.get("summary", "") or plan.get("heartbeat_summary", "") or plan.get("result", "")
        if summary_text:
            self._pipeline_summary.setText(summary_text[:500])
            self._pipeline_summary_section.setVisible(True)
        else:
            self._pipeline_summary_section.setVisible(False)

    def _update_artifacts(self, artifacts: list):
        """Update the artifacts section."""
        self._artifacts_section.setVisible(True)

        # Clear existing artifact widgets
        while self._artifacts_list.count() > 0:
            item = self._artifacts_list.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        for art in artifacts:
            if isinstance(art, str):
                name = art
                status = "completed"
                path_str = art
            elif isinstance(art, dict):
                name = art.get("name", art.get("path", "unknown"))
                status = art.get("status", "completed")
                path_str = art.get("path", art.get("name", ""))
            else:
                continue

            self._add_artifact_row(name, status, path_str)

    def _add_artifact_row(self, name: str, status: str, path_str: str = ""):
        """Add a single artifact row."""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 4, 8, 4)
        row_layout.setSpacing(8)

        # Status icon
        if status in ("completed", "success", "generated"):
            icon = "\u2705"
        elif status in ("running", "generating"):
            icon = "\U0001f504"
        elif status in ("failed", "error"):
            icon = "\u274c"
        else:
            icon = "\u23f3"

        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 12px; background: transparent;")
        icon_label.setFixedWidth(20)
        row_layout.addWidget(icon_label)

        # Name
        display_name = name[:40] + ("..." if len(name) > 40 else "")
        name_label = QLabel(display_name)
        name_label.setStyleSheet(
            f"font-size: 11px; color: {THEME.txt}; background: transparent;"
        )
        name_label.setToolTip(path_str if path_str else name)
        row_layout.addWidget(name_label, 1)

        # Status text
        status_text_map = {
            "completed": "\u2705 \u5df2\u751f\u6210",
            "success": "\u2705 \u5df2\u751f\u6210",
            "generated": "\u2705 \u5df2\u751f\u6210",
            "running": "\U0001f504 \u751f\u6210\u4e2d",
            "generating": "\U0001f504 \u751f\u6210\u4e2d",
            "failed": "\u274c \u5931\u8d25",
            "error": "\u274c \u5931\u8d25",
            "pending": "\u23f3 \u7b49\u5f85\u4e2d",
        }
        st_label = QLabel(status_text_map.get(status, status))
        st_label.setStyleSheet(
            f"font-size: 10px; color: {THEME.txt3}; background: transparent;"
        )
        row_layout.addWidget(st_label)

        self._artifacts_list.addWidget(row)

    def _set_pipeline_idle(self):
        """Set the pipeline display to idle/empty state."""
        self._plan_name_label.setText("")
        self._plan_name_label.setToolTip("")
        self._pipeline_empty.setVisible(True)
        self._pipeline_progress.setVisible(False)
        self._pipeline_progress_label.setText("")
        self._pipeline_planning.setVisible(False)

        # Hide all event step widgets
        for widget in self._event_step_widgets:
            widget.setVisible(False)

        # Hide artifacts
        self._artifacts_section.setVisible(False)

        # Hide summary
        if hasattr(self, '_pipeline_summary_section'):
            self._pipeline_summary_section.setVisible(False)

    # ------------------------------------------------------------------
    # Scrolling
    # ------------------------------------------------------------------

    def _scroll_to_bottom(self):
        """Scroll the chat area to the bottom, deferring if layout needs refresh."""
        from PySide6.QtCore import QTimer
        scrollbar = self._scroll_area.verticalScrollBar()
        if scrollbar:
            # Defer by one event-loop cycle so the layout finishes adding widgets
            QTimer.singleShot(50, lambda sb=scrollbar: sb.setValue(sb.maximum()))

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self):
        """Reload history and instance list."""
        self._load_instance_selector()
        self._load_history()
