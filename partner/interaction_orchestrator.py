"""Interaction orchestrator for user messages.

Separates user-message handling from the autonomous mind loop:
- LLM decides the user-facing reply
- LLM decides whether the lifeline needs mutation
- Code applies the mutation to task/state/project records
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional

from .journal import Journal, JournalEntry
from .knowledge import KnowledgeBase, KnowledgeEntry
from .project_state import append_log, get_active, set_active
from .state import StateManager
from .task_queue import TaskQueue, Task

logger = logging.getLogger(__name__)


@dataclass
class InteractionDecision:
    reply_to_user: str
    need_lifeline_update: bool = False
    lifeline_action: str = "none"
    target_project: str = ""
    task_title: str = ""
    task_description: str = ""
    note: str = ""
    knowledge_title: str = ""
    knowledge_content: str = ""
    priority: int = 6


class InteractionOrchestrator:
    """LLM-driven interaction line that can mutate the autonomous lifeline."""

    def __init__(
        self,
        workspace: str,
        journal: Journal,
        knowledge: KnowledgeBase,
        task_queue: TaskQueue,
        state_manager: StateManager,
        get_adapter: Callable[[], object],
        get_context: Callable[[str], list],
        snapshot_builder: Callable[[], Optional[Dict[str, str]]],
    ):
        self.workspace = workspace
        self.journal = journal
        self.knowledge = knowledge
        self.task_queue = task_queue
        self.state_manager = state_manager
        self.get_adapter = get_adapter
        self.get_context = get_context
        self.snapshot_builder = snapshot_builder

    def handle_message(self, sender_id: str, sender_name: str, text: str) -> InteractionDecision:
        decision = self._decide(sender_id, text)
        if decision.need_lifeline_update:
            self._apply_lifeline_update(decision, sender_id=sender_id, sender_name=sender_name, raw_text=text)
        return decision

    def _decide(self, sender_id: str, text: str) -> InteractionDecision:
        snapshot = self.snapshot_builder() or {}
        context = self.get_context(sender_id) or []
        adapter = self.get_adapter()

        ctx_lines = []
        for item in context[-4:]:
            role = "用户" if item.get("role") == "user" else "Partner"
            ctx_lines.append(f"{role}: {item.get('text', '')[:120]}")
        context_block = "\n".join(ctx_lines) if ctx_lines else "（无）"

        prompt = f"""你是 Partner 的交互编排器。你要同时完成两件事：
1. 给用户一个自然口语回复
2. 判断这条消息是否需要修改后台生命线（mind loop）

当前聚焦项目：{snapshot.get('display_project', '')}
当前进展：{snapshot.get('summary', '')}
当前推进：{snapshot.get('current', '') or snapshot.get('active_plan', '')}
最近完成：{snapshot.get('recent', '')}
当前卡点：{snapshot.get('blockers', '')}
下一步：{snapshot.get('next_step', '')}

最近对话：
{context_block}

用户消息：
{text}

请严格只输出一个 JSON 对象，不要加解释，不要 markdown。字段如下：
{{
  "reply_to_user": "给用户的自然回复",
  "need_lifeline_update": true,
  "lifeline_action": "none|add_task|switch_project|add_note|add_knowledge",
  "target_project": "项目名，可空",
  "task_title": "任务标题，可空",
  "task_description": "任务描述，可空",
  "note": "要写入生命线的备注，可空",
  "knowledge_title": "知识标题，可空",
  "knowledge_content": "知识内容，可空",
  "priority": 1
}}

规则：
- 如果只是问候、闲聊、纯进展询问，通常 need_lifeline_update=false
- 如果用户明确要求继续推进、重建、切换方向、补充、恢复、研究、整理、写入 workspace，通常 need_lifeline_update=true
- 用户让你继续做某件事时，优先 lifeline_action=add_task
- 回复要自然，不模板化，不暴露内部实现，不问用户下一步
- 如果要改生命线，reply_to_user 里仍然先正常回复用户，再简短说你会怎么继续做
"""

        raw = adapter.chat(prompt, purpose="interaction")
        decision = self._parse_decision(raw)
        if decision:
            return decision

        return InteractionDecision(
            reply_to_user="思路我接住了。我会把这条要求并进当前主线，继续往下推进，有结果了再跟你说。",
            need_lifeline_update=bool(re.search(r"(继续|推进|重建|恢复|研究|补充|整理|写进|复制)", text)),
            lifeline_action="add_task" if re.search(r"(继续|推进|重建|恢复|研究|补充|整理|写进|复制)", text) else "none",
            task_title=text[:50],
            task_description=text,
            priority=7,
        )

    def _parse_decision(self, raw: str) -> Optional[InteractionDecision]:
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        reply = (data.get("reply_to_user") or "").strip()
        if not reply:
            return None
        priority = data.get("priority", 6)
        try:
            priority = max(1, min(10, int(priority)))
        except Exception:
            priority = 6
        return InteractionDecision(
            reply_to_user=reply,
            need_lifeline_update=bool(data.get("need_lifeline_update")),
            lifeline_action=(data.get("lifeline_action") or "none").strip() or "none",
            target_project=(data.get("target_project") or "").strip(),
            task_title=(data.get("task_title") or "").strip(),
            task_description=(data.get("task_description") or "").strip(),
            note=(data.get("note") or "").strip(),
            knowledge_title=(data.get("knowledge_title") or "").strip(),
            knowledge_content=(data.get("knowledge_content") or "").strip(),
            priority=priority,
        )

    def _apply_lifeline_update(self, decision: InteractionDecision, sender_id: str, sender_name: str, raw_text: str):
        action = decision.lifeline_action or "none"
        if action == "switch_project":
            target = decision.target_project or decision.task_title or raw_text[:30]
            set_active(self.workspace, target)
            if decision.note:
                append_log(self.workspace, target, decision.note)
            self._touch_active_plan(target, f"用户要求切换并推进：{target}")
            self._log_mutation(action, target, raw_text)
            return

        if action == "add_note":
            target = decision.target_project or get_active(self.workspace) or "当前项目"
            note = decision.note or raw_text
            append_log(self.workspace, target, note)
            self._log_mutation(action, target, note)
            return

        if action == "add_knowledge":
            if decision.knowledge_title and decision.knowledge_content:
                self.knowledge.add(KnowledgeEntry(
                    category="findings",
                    title=decision.knowledge_title,
                    content=decision.knowledge_content,
                    related_projects=[decision.target_project or get_active(self.workspace) or ""],
                    source="user_message",
                    tags=["user_injected"],
                ))
                self._log_mutation(action, decision.knowledge_title, decision.knowledge_content)
            return

        if action == "add_task":
            target = decision.target_project or get_active(self.workspace) or ""
            if target:
                set_active(self.workspace, target)
            description = decision.task_description or raw_text
            existing = self.task_queue.find_similar_pending(description, sender_id=sender_id)
            if existing:
                self._touch_active_plan(target or existing.title, f"用户再次推动：{existing.title}")
                self._log_mutation("merge_task", existing.title, description)
                return
            task = Task(
                type="deep_dive",
                title=(decision.task_title or raw_text[:60]).strip(),
                description=description,
                priority=decision.priority,
                tags=["qq_task", "lifeline"],
                source="qq",
                sender_id=sender_id,
                sender_name=sender_name or "QQ用户",
            )
            self.task_queue.add_task(task)
            if decision.note and target:
                append_log(self.workspace, target, decision.note)
            self._touch_active_plan(target or task.title, f"用户追加任务：{task.title}")
            self._log_mutation(action, task.title, description)
            return

    def _touch_active_plan(self, title: str, heartbeat_summary: str):
        state_dir = os.path.join(self.workspace, "state")
        os.makedirs(state_dir, exist_ok=True)
        plan_path = os.path.join(state_dir, "active_plan.json")
        now = datetime.now().isoformat()
        plan = {
            "status": "planning",
            "title": title,
            "goal": heartbeat_summary,
            "created_at": now,
            "current_phase_index": 0,
            "phases": [],
            "last_heartbeat": now,
            "heartbeat_summary": heartbeat_summary,
        }
        if os.path.exists(plan_path):
            try:
                with open(plan_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    plan.update(existing)
                    plan["status"] = "planning" if existing.get("status") in ("idle", "completed", "planning") else existing.get("status", "planning")
                    plan["title"] = title or existing.get("title", "")
                    plan["goal"] = heartbeat_summary
                    plan["last_heartbeat"] = now
                    plan["heartbeat_summary"] = heartbeat_summary
            except Exception:
                pass
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

    def _log_mutation(self, action: str, subject: str, detail: str):
        try:
            self.journal.log(JournalEntry(
                task_id=f"lifeline_{datetime.now().strftime('%H%M%S')}",
                task_type="lifeline_update",
                task_title=f"用户消息触发生命线更新: {action}",
                result_summary=f"{subject} | {detail[:160]}",
            ))
        except Exception as exc:
            logger.warning(f"failed to log lifeline mutation: {exc}")
