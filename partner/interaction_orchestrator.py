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
from .project_state import (
    append_log,
    get_active,
    record_project_guardrail,
    set_active,
)
from .research_memory import record_episode, record_growth_event, record_user_signal
from .research_memory import record_risk_event
from .content_feed import record_shared_content
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
    allowed_scope: list[str] = None
    forbidden_scope: list[str] = None
    current_mainline: str = ""
    source_roots: list[str] = None
    forbidden_evidence_patterns: list[str] = None
    completion_criteria: list[str] = None
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

    @staticmethod
    def _sanitize_reply_to_user(reply: str) -> str:
        text = (reply or "").strip()
        if not text:
            return ""
        if re.search(r"(还没有确定具体方向|没有确定具体方向|你这边有什么想做|有什么想做的|可以直接跟我说|我来安排推进)", text):
            return ""
        lines = []
        removed = False
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if re.search(
                r"(有啥想继续搞|随时说|随时告诉我|你想让我|你要我|要不要|请选择|你想怎么|你想先|给我方向|你看.*方向|还是我|你这边有什么想做|有什么想做的|直接跟我说)",
                line,
            ):
                removed = True
                continue
            if ("?" in line or "？" in line) and re.search(r"(什么|吗|要不要|还是|想不想|方向|继续)", line):
                removed = True
                continue
            lines.append(line)
        text = "\n".join(lines).strip()
        text = re.sub(
            r"(如需|如果你需要|请告知|等待你|待用户).*",
            "",
            text,
        ).strip()
        if removed:
            suffix = "我会按当前主线继续推进，并把下一步结果写进 workspace。"
            text = f"{text}\n{suffix}".strip() if text else suffix
        return text

    @staticmethod
    def _is_status_query(text: str) -> bool:
        raw = (text or "").strip()
        return bool(re.search(r"(在做什么|做什么内容|现在.*做啥|现在.*干嘛|进展|做咋样|做到哪|运行.*怎样|什么状态)", raw))

    @staticmethod
    def _status_reply_from_snapshot(snapshot: dict) -> str:
        project = (snapshot.get("display_project") or snapshot.get("focus_project") or "当前项目").strip()
        current = (snapshot.get("current") or snapshot.get("summary") or snapshot.get("active_plan") or "").strip()
        recent = (snapshot.get("recent") or "").strip()
        next_step = (snapshot.get("next_step") or "").strip()
        blockers = (snapshot.get("blockers") or "").strip()
        lines = [f"现在主要在推进「{project}」。"]
        if current:
            lines.append(f"当前状态：{current[:160]}")
        if recent:
            lines.append(f"最近动作：{recent[:160]}")
        if blockers:
            lines.append(f"当前卡点：{blockers[:140]}")
        if next_step:
            lines.append(f"下一步：{next_step[:160]}")
        if len(lines) == 1:
            lines.append("我已经读取当前 workspace，会先从已有状态和最近日志里找一个最小推进点继续做。")
        lines.append("我会继续自己推进，不会等你选方向。")
        return "\n".join(lines)

    def handle_message(self, sender_id: str, sender_name: str, text: str) -> InteractionDecision:
        self._record_user_research_signal(text)
        decision = self._decide(sender_id, text)
        if decision.need_lifeline_update:
            self._apply_lifeline_update(decision, sender_id=sender_id, sender_name=sender_name, raw_text=text)
        return decision

    def _record_user_research_signal(self, text: str):
        raw = (text or "").strip()
        if not raw:
            return
        target = get_active(self.workspace) or ""
        try:
            record_shared_content(
                self.workspace,
                text=raw,
                project=target,
                sender="user",
                source="interaction_user_share",
            )
        except Exception as exc:
            logger.debug(f"failed to record interaction shared content: {exc}")
        kind = ""
        if re.search(r"(老师|导师|文献|论文|推文|视频|看到|想到|灵感|思路|想法|启发|建议)", raw):
            kind = "teacher_advice" if "老师" in raw or "导师" in raw else "user_idea"
        elif self._mentions_risk_or_quality_signal(raw):
            kind = "risk_signal"
        elif self._mentions_possible_correction(raw):
            kind = "correction_signal"
        if not kind:
            return
        try:
            record_user_signal(self.workspace, target, raw, kind=kind)
        except Exception as exc:
            logger.debug(f"failed to record user research signal: {exc}")

    def _mentions_possible_correction(self, text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        return bool(re.search(r"(不是做|不要做|别做|不要再做|别再做|停止|搞错|跑偏|纠正|回到|应该是做|是做.+不是做)", raw))

    def _mentions_risk_or_quality_signal(self, text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        return bool(re.search(
            r"(数据泄露|泄露|leakage|data leak|不可信|太好|好得不正常|异常好|过拟合|造假|幻觉|编造|走捷径|不合理|有问题|可能错|不对劲)",
            raw,
            re.I,
        ))

    @staticmethod
    def _correction_reply(guardrail: dict) -> str:
        mainline = (guardrail.get("current_mainline") or "").strip()
        if mainline:
            return f"方向已纠正：后续按「{mainline}」推进，旧方向会从当前主线里排除。"
        return "方向已纠正，我会按这条边界更新当前主线，后续不再沿错误方向推进。"

    def _decide(self, sender_id: str, text: str) -> InteractionDecision:
        snapshot = self.snapshot_builder() or {}
        context = self.get_context(sender_id) or []
        adapter = self.get_adapter()

        if self._is_status_query(text):
            return InteractionDecision(
                reply_to_user=self._status_reply_from_snapshot(snapshot),
                need_lifeline_update=False,
                lifeline_action="none",
            )

        if self._mentions_risk_or_quality_signal(text):
            project = (snapshot.get("display_project") or snapshot.get("focus_project") or get_active(self.workspace) or "").strip()
            return InteractionDecision(
                reply_to_user=(
                    "这个风险我会先放到最高优先级处理。下一轮先做证据审计，"
                    "在确认没有泄露/伪提升前，不继续把当前结果当最佳结论推进。"
                ),
                need_lifeline_update=True,
                lifeline_action="add_task",
                target_project=project,
                task_title="风险审计：用户指出当前结果可能存在可信度问题",
                task_description=(
                    f"用户风险判断：{text}\n"
                    "先暂停围绕当前最佳结果继续调参，优先审计数据泄露、验证划分、特征工程是否使用全量数据、"
                    "bootstrap/交叉验证是否把测试信息泄露进训练。必须把审计结论写入项目 workspace。"
                ),
                note=f"用户风险信号：{text}",
                priority=1,
            )

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
疑似纠偏：{self._mentions_possible_correction(text)}

最近对话：
{context_block}

用户消息：
{text}

请严格只输出一个 JSON 对象，不要加解释，不要 markdown。字段如下：
{{
  "reply_to_user": "给用户的自然回复",
  "need_lifeline_update": true,
  "lifeline_action": "none|add_task|switch_project|add_note|add_knowledge|correct_direction",
  "target_project": "项目名，可空",
  "task_title": "任务标题，可空",
  "task_description": "任务描述，可空",
  "note": "要写入生命线的备注，可空",
  "knowledge_title": "知识标题，可空",
  "knowledge_content": "知识内容，可空",
  "current_mainline": "用户明确指定的新主线，可空",
  "allowed_scope": ["用户明确允许/要求的方向，没有则空数组"],
  "forbidden_scope": ["用户明确禁止/纠正排除的方向，没有则空数组"],
  "source_roots": ["用户明确指定的真实项目/数据/代码根目录，没有则空数组"],
  "forbidden_evidence_patterns": ["用户明确禁止作为证据的数据/结论模式，没有则空数组"],
  "completion_criteria": ["用户明确要求完成前必须满足的验收条件，没有则空数组"],
  "priority": 1
}}

规则：
- 如果只是问候、闲聊、纯进展询问，通常 need_lifeline_update=false
- 如果用户明确要求继续推进、重建、切换方向、补充、恢复、研究、整理、写入 workspace，通常 need_lifeline_update=true
- 用户让你继续做某件事时，优先 lifeline_action=add_task
- 用户纠正项目方向时，只有在你能从上下文明白“新主线/允许方向/禁止方向”时才 lifeline_action=correct_direction
- 如果用户表达含糊，不要写长期边界；改用 add_note 或 add_task，把不确定性写进 note/task_description
- correct_direction 时必须填 current_mainline、allowed_scope、forbidden_scope 中至少一个，禁止凭空扩展用户没说的范围
- 如果用户明确说“真实项目在/数据在/代码在/最新目录是 ...”，把路径填入 source_roots
- 如果用户明确说“不能用合成数据/不能用模拟结果/不要把临时结果当结论”，把这些原话要点填入 forbidden_evidence_patterns
- 如果用户明确说“完成前必须/需要/验收标准是 ...”，把条件填入 completion_criteria
- 回复要自然，不模板化，不暴露内部实现，不问用户下一步
- 如果要改生命线，reply_to_user 里仍然先正常回复用户，再简短说你会怎么继续做
"""

        raw = adapter.chat(prompt, purpose="interaction")
        decision = self._parse_decision(raw)
        if decision:
            return decision

        fallback_reply = self._status_reply_from_snapshot(snapshot) if snapshot else "收到，我会基于当前 workspace 继续推进，并把下一步结果写进去。"
        return InteractionDecision(
            reply_to_user=fallback_reply,
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
        reply = self._sanitize_reply_to_user(data.get("reply_to_user") or "")
        if not reply:
            snapshot = self.snapshot_builder() or {}
            reply = self._status_reply_from_snapshot(snapshot) if snapshot else "收到，我会按当前主线继续推进。"
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
            allowed_scope=[str(x).strip() for x in (data.get("allowed_scope") or []) if str(x).strip()][:8],
            forbidden_scope=[str(x).strip() for x in (data.get("forbidden_scope") or []) if str(x).strip()][:12],
            current_mainline=(data.get("current_mainline") or "").strip(),
            source_roots=[str(x).strip() for x in (data.get("source_roots") or []) if str(x).strip()][:8],
            forbidden_evidence_patterns=[str(x).strip() for x in (data.get("forbidden_evidence_patterns") or []) if str(x).strip()][:12],
            completion_criteria=[str(x).strip() for x in (data.get("completion_criteria") or []) if str(x).strip()][:12],
            priority=priority,
        )

    @staticmethod
    def _ground_scope_item_in_user_text(item: str, raw_text: str) -> str:
        """Return the grounded user-text anchor for a scope item, or empty.

        The interaction LLM may paraphrase, but long-term project boundaries
        should not gain new technical terms that the user did not provide.
        If an LLM writes "VAE分子生成" but the user only wrote "VAE", keep
        "VAE" rather than the expanded phrase.
        """
        item_norm = (item or "").strip()
        raw_norm = (raw_text or "").strip()
        if not item_norm or not raw_norm:
            return ""
        item_lc = item_norm.lower()
        raw_lc = raw_norm.lower()
        if item_lc in raw_lc:
            return item_norm
        ascii_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{1,}", item_norm)
        for tok in ascii_tokens:
            if tok.lower() in raw_lc:
                return tok
        return ""

    def _ground_guardrail_in_user_text(self, guardrail: dict, raw_text: str) -> dict:
        allowed = []
        for item in guardrail.get("allowed_scope") or []:
            grounded = self._ground_scope_item_in_user_text(str(item), raw_text)
            if grounded and grounded not in allowed:
                allowed.append(grounded)
        forbidden = []
        for item in guardrail.get("forbidden_scope") or []:
            grounded = self._ground_scope_item_in_user_text(str(item), raw_text)
            if grounded and grounded not in forbidden:
                forbidden.append(grounded)
        mainline = (guardrail.get("current_mainline") or "").strip()
        if mainline and mainline.lower() not in (raw_text or "").lower():
            mainline = "；".join(allowed[:4])
        return {
            **guardrail,
            "allowed_scope": allowed,
            "forbidden_scope": forbidden,
            "current_mainline": mainline,
        }

    def _apply_lifeline_update(self, decision: InteractionDecision, sender_id: str, sender_name: str, raw_text: str):
        action = decision.lifeline_action or "none"
        if decision.source_roots or decision.forbidden_evidence_patterns or decision.completion_criteria or self._extract_paths(raw_text):
            target_for_contract = decision.target_project or get_active(self.workspace) or "当前项目"
            self._update_contract_metadata(decision, target_for_contract, raw_text)

        if action == "switch_project":
            target = decision.target_project or decision.task_title or raw_text[:30]
            set_active(self.workspace, target)
            if decision.note:
                append_log(self.workspace, target, decision.note)
            self._touch_active_plan(target, f"用户要求切换并推进：{target}")
            self._log_mutation(action, target, raw_text)
            self._nudge_project(target, priority=2, source="interaction:switch_project")
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
            if decision.priority <= 2 or self._mentions_risk_or_quality_signal(raw_text):
                self._append_breakthrough_queue(
                    target or "当前项目",
                    reason="用户高优先级风险/质量信号",
                    next_action=description,
                    raw_text=raw_text,
                )
                try:
                    record_risk_event(self.workspace, target or "当前项目", "user quality/risk signal", raw_text, severity="high")
                    record_episode(
                        self.workspace,
                        target or "当前项目",
                        "用户经验触发风险审计",
                        evidence=raw_text,
                        lesson="用户基于经验指出结果异常时，应先审计证据和泄露风险，再继续优化。",
                        risk="user_quality_signal",
                    )
                    record_growth_event(
                        self.workspace,
                        target or "当前项目",
                        trigger=raw_text,
                        learned="用户的经验判断可能指出模型结果异常、泄露或伪提升，不能当成普通聊天忽略。",
                        behavior_change="以后遇到异常好/不可信/可能泄露的提醒时，先暂停调参并做证据审计，再决定是否继续推进。",
                        evidence="breakthrough_queue.md",
                        category="user_experience",
                    )
                except Exception as exc:
                    logger.debug(f"failed to record user risk signal: {exc}")
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
            self._nudge_project(target or task.title, priority=2, source="interaction:add_task")
            return

        if action == "correct_direction":
            target = decision.target_project or get_active(self.workspace) or "当前项目"
            guardrail = {
                "raw_text": raw_text,
                "allowed_scope": decision.allowed_scope or [],
                "forbidden_scope": decision.forbidden_scope or [],
                "current_mainline": decision.current_mainline or "",
                "source_roots": decision.source_roots or self._extract_paths(raw_text),
                "forbidden_evidence_patterns": decision.forbidden_evidence_patterns or [],
                "completion_criteria": decision.completion_criteria or [],
            }
            guardrail = self._ground_guardrail_in_user_text(guardrail, raw_text)
            if not (guardrail["allowed_scope"] or guardrail["forbidden_scope"] or guardrail["current_mainline"]):
                append_log(self.workspace, target, f"用户疑似纠偏但边界不明确，未写入长期约束：{raw_text}")
                self._log_mutation("note_uncertain_correction", target, raw_text)
                return
            record_project_guardrail(self.workspace, target, guardrail)
            record_episode(
                self.workspace,
                target,
                "用户纠偏进入项目生命线",
                evidence=raw_text,
                lesson="用户纠偏必须写入 contract/brief/active_plan，不能只口头承认。",
                risk="direction_drift",
            )
            record_growth_event(
                self.workspace,
                target,
                trigger=raw_text,
                learned="用户纠偏会改变项目边界，不能只回复“收到”。",
                behavior_change="以后先按用户明确边界更新项目主线和禁止方向，再让生命线继续推进。",
                evidence="project_contract.json",
                category="direction_correction",
            )
            self._touch_active_plan(target, f"用户纠偏：{raw_text[:120]}")
            self._log_mutation(action, target, raw_text)
            self._nudge_project(target, priority=1, source="interaction:correct_direction")
            return

    def _update_contract_metadata(self, decision: InteractionDecision, target: str, raw_text: str):
        from .project_state import read_project_contract, write_project_contract, update_project_brief_from_contract
        contract = read_project_contract(self.workspace, target)
        for key, values in (
            ("source_roots", decision.source_roots or self._extract_paths(raw_text)),
            ("forbidden_evidence_patterns", decision.forbidden_evidence_patterns or []),
            ("completion_criteria", decision.completion_criteria or []),
        ):
            merged = []
            for item in list(contract.get(key) or []) + list(values or []):
                if item and item not in merged:
                    merged.append(item)
            contract[key] = merged[:20]
        write_project_contract(self.workspace, target, contract)
        update_project_brief_from_contract(self.workspace, target, contract)
        self._log_mutation("update_project_contract", target, raw_text)

    def _nudge_project(self, title: str, priority: int = 2, source: str = "interaction"):
        """Wake the mind loop after a user-driven lifeline mutation.

        This is best-effort: if the process is not running, persisted
        active_plan/state will still be picked up by WAKE_UP on next start.
        """
        if not title:
            return
        try:
            from .mind.event_types import EventType, MindEvent
            from .mind.pool import MindPool

            pool = MindPool.get_sync_instance()
            if not pool:
                return
            pool.put_threadsafe(MindEvent(
                type=EventType.PROJECT,
                priority=priority,
                payload={"title": title, "step": 0},
                source=source,
            ))
        except Exception as exc:
            logger.debug(f"failed to nudge project event: {exc}")

    @staticmethod
    def _extract_paths(text: str) -> list[str]:
        paths = []
        for match in re.findall(r"(?:/mnt|/home|[A-Za-z]:\\)[^\s，。；;、]+", text or ""):
            cleaned = match.strip().rstrip("。；;,，")
            if cleaned and cleaned not in paths:
                paths.append(cleaned)
        return paths[:8]

    def _append_breakthrough_queue(self, target: str, *, reason: str, next_action: str, raw_text: str):
        if not target:
            return
        try:
            from .project_state import get_project_dir
            project_dir = get_project_dir(self.workspace, target)
            path = os.path.join(project_dir, "breakthrough_queue.md")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            exists = os.path.exists(path)
            with open(path, "a", encoding="utf-8") as f:
                if not exists:
                    f.write(f"# {target} 突破队列\n\n")
                    f.write("这个文件记录用户信号、完成态逃逸、证据不足时生成的下一突破口。\n")
                f.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} | open | user_signal\n")
                f.write(f"- 触发原因：{reason}\n")
                f.write(f"- 用户原话：{raw_text[:260]}\n")
                f.write(f"- 必须推进：{next_action[:800]}\n")
                f.write("- 验收标准：必须先形成证据审计/风险复盘文件；审计前不得把可疑结果继续当最佳结论。\n")
        except Exception as exc:
            logger.debug(f"failed to append breakthrough queue: {exc}")

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
