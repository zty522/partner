"""Conversation-first Textual workspace for Partner."""
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, RichLog, Static

from partner.application import ApplicationReadModel, PartnerApplicationService


class PartnerTUI(App):
    TITLE = "Partner"
    SUB_TITLE = "长期研究与项目工作空间"
    CSS = """
    Screen { background:#101318; color:#E8ECF3; }
    #projects { width:28; background:#151922; border-right:solid #303644; padding:1; }
    #main { width:1fr; padding:1 2; }
    #detail { width:42; background:#151922; border-left:solid #303644; padding:1 2; display:none; }
    #detail.visible { display:block; }
    #feed { height:1fr; background:#101318; border:none; scrollbar-color:#556070 transparent; }
    #status { height:3; color:#99A3B3; padding:1 0; }
    #composer { dock:bottom; margin-top:1; border:solid #596B9B; background:#171C25; }
    ListItem { padding:1; margin-bottom:1; }
    ListItem.--highlight { background:#263154; color:white; }
    .project-title { text-style:bold; }
    .project-meta { color:#8C96A8; }
    #hint { color:#8C96A8; height:2; }
    """
    BINDINGS = [
        Binding("ctrl+p", "focus_projects", "切换项目"),
        Binding("ctrl+j", "show_jobs", "后台工作"),
        Binding("ctrl+e", "toggle_detail", "证据详情"),
        Binding("ctrl+o", "open_artifact", "打开产物"),
        Binding("ctrl+k", "focus_composer", "输入"),
        Binding("ctrl+q", "quit", "退出"),
    ]

    def __init__(self, workspace: str):
        super().__init__(); self.workspace = str(Path(workspace).resolve())
        self.service = PartnerApplicationService(self.workspace)
        self.read_model = ApplicationReadModel(self.workspace)
        self.project_id = ""; self.project_rows = []; self.latest_artifact = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="projects"):
                yield Label("项目", classes="project-title")
                yield ListView(id="project-list")
                yield Static("Ctrl+P 切换项目", id="hint")
            with Vertical(id="main"):
                yield Static("所有项目 · 项目、外部学习和系统改进分线呈现", id="status")
                yield RichLog(id="feed", wrap=True, markup=True)
                yield Input(placeholder="直接描述你想推进的目标；后台工作不会阻塞输入", id="composer")
            with Vertical(id="detail"):
                yield Label("上下文与证据", classes="project-title")
                yield RichLog(id="detail-log", wrap=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_view(); self.set_interval(2.0, self.refresh_view)

    def refresh_view(self) -> None:
        try:
            projects = self.read_model.projects()
            updates = self.read_model.updates(self.project_id, 80)
        except Exception as exc:
            self.query_one("#status", Static).update(f"刷新失败：{type(exc).__name__}")
            return
        rows = [{"project_id":"", "title":"所有项目", "specialist":"统一", "status":"ready"}, *projects]
        signature = [(x.get("project_id"), x.get("status"), x.get("job_count")) for x in rows]
        if signature != getattr(self, "_project_signature", None):
            view = self.query_one("#project-list", ListView); view.clear()
            for row in rows:
                view.append(ListItem(Label(f"{row['title']}\n[dim]{row.get('specialist')} · {row.get('status')}[/dim]"), name=str(row.get("project_id") or "all")))
            self.project_rows = rows; self._project_signature = signature
        update_signature = [(x.update_id, x.finding) for x in updates]
        if update_signature != getattr(self, "_update_signature", None):
            feed = self.query_one("#feed", RichLog); feed.clear()
            if not updates:
                feed.write("[bold]从一个真实问题开始[/bold]\n这里仅展示有意义的工作增量；技术 Event 可在证据详情展开。")
            labels = {"project":"项目", "active_learning":"外部学习", "self_evolution":"系统改进", "conversation":"对话"}
            colors = {"project":"#7EA2FF", "active_learning":"#55C2B3", "self_evolution":"#E3A85B", "conversation":"#AAB4C5"}
            for item in reversed(updates):
                title = labels.get(item.line, item.line); color = colors.get(item.line, "white")
                feed.write(f"[{color}][bold]【{title}】[/bold][/{color}] {item.subject}\n{item.finding}\n[dim]接下来：{item.next}[/dim]\n")
                for path in item.evidence_refs:
                    if Path(path).is_file(): self.latest_artifact = path
            self._update_signature = update_signature

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index or 0
        if index < len(self.project_rows):
            row = self.project_rows[index]; self.project_id = str(row.get("project_id") or "")
            self.query_one("#status", Static).update(f"{row['title']} · 后台执行时仍可继续输入")
            self._update_signature = None; self.refresh_view(); self.action_focus_composer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text: return
        result = self.service.submit(text, channel="tui", sender_id="tui_user", project_id=self.project_id)
        event.input.value = ""
        self.notify(result.message, severity="information" if result.accepted else "error")
        self._update_signature = None; self.refresh_view()

    def action_focus_projects(self) -> None:
        self.query_one("#project-list", ListView).focus()

    def action_focus_composer(self) -> None:
        self.query_one("#composer", Input).focus()

    def action_toggle_detail(self) -> None:
        panel = self.query_one("#detail", Vertical); panel.toggle_class("visible")
        if panel.has_class("visible"):
            rows = self.read_model.event_details(limit=30); log = self.query_one("#detail-log", RichLog); log.clear()
            for row in rows:
                log.write(f"[bold]{row.semantic_summary}[/bold]\n{row.series} · {row.status}\n[dim]{row.node} · tokens {row.token_usage.get('total_tokens', 0)}[/dim]\n")

    def action_show_jobs(self) -> None:
        panel = self.query_one("#detail", Vertical); panel.add_class("visible")
        log = self.query_one("#detail-log", RichLog); log.clear()
        for job in self.read_model.jobs(self.project_id, 60):
            log.write(f"[bold]{job.title}[/bold]\n状态：{job.state}\n{job.latest_result or job.meaningful_progress or '等待调度'}\n")

    def action_open_artifact(self) -> None:
        if not self.latest_artifact:
            self.notify("当前没有可打开的已验证产物", severity="warning"); return
        import subprocess
        subprocess.Popen(["xdg-open", self.latest_artifact], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_tui(args) -> None:
    workspace = getattr(args, "workspace", None)
    if not workspace:
        from partner.state.setup import find_workspace
        workspace = find_workspace()
    if not workspace:
        raise SystemExit("未找到 Partner 工作区，请先运行 partner setup")
    PartnerTUI(str(workspace)).run()


def register_subparser(sub) -> None:
    parser = sub.add_parser("tui", help="打开 Partner 终端工作空间")
    parser.add_argument("--workspace", "-w", default=None, help="Partner 工作区")
    parser.set_defaults(func=cmd_tui)


__all__ = ["PartnerTUI", "cmd_tui", "register_subparser"]
