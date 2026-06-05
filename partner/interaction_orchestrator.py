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
from .research_guardrails import record_user_signal_to_mind
from .content_feed import record_shared_content
from .project_registry import (
    find_project,
    import_public_project_context,
    instance_id_from_workspace,
    project_location_hint,
    release_project,
)
from .state import StateManager
from .user_text_safety import has_internal_diff, strip_internal_diff
from .task_queue import TaskQueue, Task
from .outbound_policy import prefix_event_notice

logger = logging.getLogger(__name__)


def _clip_title(text: str, suffix: str = "") -> str:
    return ""


def _is_reference_gathering_request(text: str) -> bool:
    """Deprecated: event selection is handled by the LLM selector."""
    return False


def _derive_task_contract(text: str) -> dict:
    """Deprecated: task contract fields must come from the LLM decision."""
    return {
        "mainline": "",
        "allowed_scope": [],
        "forbidden_scope": [],
        "completion_criteria": [],
    }


def _derive_delivery_mode(text: str, contract: dict | None = None) -> str:
    return "research_project"


def _derive_event_type(text: str, delivery_mode: str = "research_project") -> str:
    """Deprecated: event_type must come from the LLM selector."""
    return "project_think"


def _task_description_from_contract(text: str, contract: dict) -> str:
    return text or ""


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
    delivery_mode: str = "research_project"
    event_type: str = "project"
    event_kind: str = ""
    stop_after_completion: bool = False
    priority: int = 6
    pending_action: str = "none"
    pending_followup: dict = None


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
        self._conversation_state_file = os.path.join(
            self.workspace,
            "state",
            "conversation_state.json",
        )

    def _notify_growth_write(self, project: str, learned: str):
        try:
            from .mind.event_types import EventType, MindEvent
            from .mind.pool import MindPool

            pool = MindPool.get_sync_instance()
            if not pool:
                return
            content = prefix_event_notice(
                f"已写入一条可复用经验：{str(learned or '后续会按这次经验调整判断和推进方式。')[:120]}",
                EventType.HABIT_UPDATE.value,
                event_kind=project or "当前项目",
            )
            pool.put_threadsafe(MindEvent(
                type=EventType.REPORT,
                priority=3,
                payload={
                    "content": content,
                    "force_send": True,
                    "bypass_rate_limit": True,
                    "visible_event_type": EventType.HABIT_UPDATE.value,
                    "visible_event_kind": project or "当前项目",
                },
                source="interaction:growth_notice",
            ))
        except Exception as exc:
            logger.debug(f"failed to enqueue growth notice: {exc}")

    def _load_conversation_state(self) -> dict:
        try:
            with open(self._conversation_state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_conversation_state(self, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._conversation_state_file), exist_ok=True)
            with open(self._conversation_state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.debug(f"failed to save conversation state: {exc}")

    def _get_sender_dialog_state(self, sender_id: str) -> dict:
        state = self._load_conversation_state()
        sender_state = state.get(str(sender_id or ""))
        return sender_state if isinstance(sender_state, dict) else {}

    def _update_sender_dialog_state(self, sender_id: str, decision: InteractionDecision, user_text: str) -> None:
        action = (decision.pending_action or "none").strip()
        pending = decision.pending_followup if isinstance(decision.pending_followup, dict) else {}
        if action not in {"set", "keep", "clear", "none"}:
            action = "none"
        sender_key = str(sender_id or "")
        if not sender_key:
            return
        state = self._load_conversation_state()
        current = state.get(sender_key) if isinstance(state.get(sender_key), dict) else {}
        if action == "set" and pending:
            state[sender_key] = {
                **current,
                "pending_followup": {
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "original_user_request": str(pending.get("original_user_request") or user_text or "")[:1600],
                    "current_objective": str(pending.get("current_objective") or pending.get("original_user_request") or user_text or "")[:1600],
                    "missing_slots": [str(x).strip() for x in (pending.get("missing_slots") or []) if str(x).strip()][:8],
                    "known_slots": {
                        str(k)[:80]: str(v)[:300]
                        for k, v in (pending.get("known_slots") or {}).items()
                        if str(k).strip() and str(v).strip()
                    },
                    "last_question": str(pending.get("last_question") or decision.reply_to_user or "")[:800],
                },
                "last_user_text": (user_text or "")[:1200],
            }
        elif action == "keep":
            if current:
                current["last_user_text"] = (user_text or "")[:1200]
                if isinstance(current.get("pending_followup"), dict):
                    current["pending_followup"]["updated_at"] = datetime.now().isoformat()
                state[sender_key] = current
        elif action == "clear":
            if current:
                current.pop("pending_followup", None)
                current["last_user_text"] = (user_text or "")[:1200]
                state[sender_key] = current
        elif action == "none" and decision.need_lifeline_update:
            if current and current.get("pending_followup"):
                current.pop("pending_followup", None)
                current["last_user_text"] = (user_text or "")[:1200]
                state[sender_key] = current
        if state.get(sender_key) == {}:
            state.pop(sender_key, None)
        self._save_conversation_state(state)

    @staticmethod
    def _sanitize_reply_to_user(reply: str) -> str:
        text = (reply or "").strip()
        if not text:
            return ""
        text = re.sub(r"(?im)^\s*⚠️?\s*Reached maximum iterations.*(?:\n|$)", "", text).strip()
        if not text:
            return ""
        if has_internal_diff(text):
            text = strip_internal_diff(text)
            if not text or has_internal_diff(text):
                return ""
        if re.search(r"(还没有确定具体方向|没有确定具体方向|你这边有什么想做|有什么想做的|可以直接跟我说|我来安排推进)", text):
            return ""
        lines = []
        removed = False
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            trimmed = re.sub(
                r"[，,。；;]?\s*(?:需要的话|如果需要|如需)?\s*随时(?:说|告诉我|跟我说).*",
                "",
                line,
            ).strip("，,。；; ")
            if trimmed and trimmed != line:
                removed = True
                lines.append(trimmed)
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
        if removed and not text:
            return ""
        return text

    @staticmethod
    def _is_status_query(text: str) -> bool:
        return False

    @staticmethod
    def _is_external_content_share(text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        if re.search(r"(https?://|www\.|mp\.weixin\.qq\.com|xiaohongshu\.com|bilibili\.com|zhihu\.com|卡片消息|图文H5|jump_url)", raw, re.I):
            return True
        return False

    @staticmethod
    def _is_project_start_or_research_request(text: str) -> bool:
        return False

    @staticmethod
    def _is_project_pause_request(text: str) -> bool:
        return False

    @staticmethod
    def _infer_project_title_from_user_text(text: str) -> str:
        return ""

    def _infer_project_title_with_llm(self, adapter: object, text: str, snapshot: dict) -> str:
        if not adapter or not (text or "").strip():
            return ""
        prompt = f"""从用户消息中提取最合适的新项目名。只输出 JSON。

当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
用户消息或最近上下文：
{text[:1200]}

要求：
- 项目名只能来自当前这条用户消息中明确出现的主题，不要沿用当前项目
- 如果只是“重新开一个项目/不要接着之前”但没有新主题，输出空字符串
- 如果是“根据这个/基于这个”并且当前消息里出现了研究主题、疾病、数据类型或方法方向，可提取这些主题
- 项目名控制在 8-24 个中文字符，不要写成完整句子
- 不确定就输出空字符串

JSON:
{{"project_title": ""}}
"""
        try:
            raw = adapter.chat(prompt, purpose="classify") or ""
        except Exception as exc:
            logger.debug(f"project title LLM failed: {exc}")
            return ""
        text_out = raw.strip()
        if text_out.startswith("```"):
            text_out = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=re.DOTALL).strip()
        start = text_out.find("{")
        end = text_out.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return ""
        try:
            data = json.loads(text_out[start:end + 1])
        except Exception:
            return ""
        title = str(data.get("project_title") or "").strip()
        title = re.sub(r"[“”\"'「」]", "", title).strip("，,。；;:： ")
        if not title or len(title) > 50:
            return ""
        if title in {"当前项目", "这个项目", "新项目", "重新开一个项目"}:
            return ""
        return title[:36]

    def handle_message(self, sender_id: str, sender_name: str, text: str) -> InteractionDecision:
        decision = self._decide(sender_id, text)
        self._record_event_decision(sender_id, text, decision)
        if decision.need_lifeline_update:
            self._apply_lifeline_update(decision, sender_id=sender_id, sender_name=sender_name, raw_text=text)
        self._update_sender_dialog_state(sender_id, decision, text)
        return decision

    def _record_event_decision(self, sender_id: str, text: str, decision: InteractionDecision):
        """Local audit trail: user message -> lifeline action/event mode."""
        try:
            state_dir = os.path.join(self.workspace, "state")
            os.makedirs(state_dir, exist_ok=True)
            path = os.path.join(state_dir, "event_decisions.jsonl")
            row = {
                "ts": datetime.now().isoformat(),
                "sender_id": str(sender_id or "")[:80],
                "user_text": (text or "")[:1200],
                "reply_preview": (decision.reply_to_user or "")[:300],
                "need_lifeline_update": bool(decision.need_lifeline_update),
                "lifeline_action": decision.lifeline_action,
                "target_project": decision.target_project,
                "task_title": decision.task_title,
                "event_type": decision.event_type,
                "delivery_mode": decision.delivery_mode,
                "event_kind": decision.event_kind,
                "stop_after_completion": bool(decision.stop_after_completion),
                "priority": decision.priority,
                "pending_action": decision.pending_action,
                "pending_followup": decision.pending_followup or {},
                "source": "interaction_orchestrator",
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug(f"failed to record event decision: {exc}")

    def _record_user_research_signal(self, text: str):
        """Deprecated: user-message classification belongs to the LLM selector."""
        return

    def _mentions_possible_correction(self, text: str) -> bool:
        return False

    def _mentions_risk_or_quality_signal(self, text: str) -> bool:
        return False

    @staticmethod
    def _correction_reply(guardrail: dict) -> str:
        return ""

    def _project_start_reply(self, adapter: object, text: str, title: str, snapshot: dict) -> str:
        """Generate a natural user reply while code handles reliable lifeline enqueue."""
        prompt = f"""你是 Partner，用户刚给你启动/推进了一个长期研究方向。
请只回复一小段自然中文，2 句以内。

要求：
- 不要像模板，不要说“收到，我会把...作为当前主线”这类固定句
- 不要问用户下一步，不要让用户选择
- 简短说明你理解的方向，以及你接下来会先从哪里切入
- 不暴露内部机制、lifeline、active_plan、workspace 等词

用户消息：
{text}

系统识别到的项目方向：{title}
当前已有项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
"""
        try:
            raw = adapter.chat(prompt, purpose="interaction") if adapter else ""
        except Exception as exc:
            logger.debug(f"project-start reply LLM failed: {exc}")
            raw = ""
        reply = self._sanitize_reply_to_user(raw)
        if reply:
            return reply[:260]
        return "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__"

    def _project_location_reply(self, title: str, hint: str) -> str:
        return ""

    def _status_reply_with_llm(self, adapter: object, text: str, snapshot: dict) -> str:
        prompt = f"""你是 Partner，用户在问你当前进展。
请基于下面状态写一段自然中文，80-180 字。

要求：
- 不要模板化，不要输出字段名，不要说 workspace、active_plan、FINDINGS、NEXT
- 只讲用户关心的：现在在研究什么、真正完成了什么判断、下一步会做什么
- 如果状态里没有实质进展，就坦诚说还没有可靠新结论，但不要问用户下一步
- 不要暴露内部日志、文件名、路径、JSON、队列、cron、backend

用户消息：{text}
当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
状态摘要：{snapshot.get('summary', '')}
当前推进：{snapshot.get('current', '') or snapshot.get('active_plan', '')}
最近完成：{snapshot.get('recent', '')}
卡点：{snapshot.get('blockers', '')}
下一步：{snapshot.get('next_step', '')}
"""
        try:
            raw = adapter.chat(prompt, purpose="interaction") if adapter else ""
        except Exception as exc:
            logger.debug(f"status reply LLM failed: {exc}")
            raw = ""
        reply = self._sanitize_reply_to_user(raw)
        return reply[:320] if reply else "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__"

    def _content_share_reply_with_llm(self, adapter: object, text: str, snapshot: dict) -> str:
        prompt = f"""用户刚分享了一条外部内容，可能是公众号、小红书、B站、知乎链接或长文本。
请以 Partner 的口吻回复一小段自然中文，2 句以内。

要求：
- 明确表示你会把这条内容当作研究信号来消化
- 不要假装已经读完整链接；如果只是卡片/链接，就说会先基于可见标题摘要判断
- 不要问用户下一步，不要暴露 content_feed、workspace、队列等内部词
- 不要使用固定模板

当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
用户分享内容：
{text[:1200]}
"""
        try:
            raw = adapter.chat(prompt, purpose="interaction") if adapter else ""
        except Exception as exc:
            logger.debug(f"content-share reply LLM failed: {exc}")
            raw = ""
        reply = self._sanitize_reply_to_user(raw)
        return reply[:260] if reply else "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__"

    def _classify_user_intent_with_llm(self, adapter: object, text: str, snapshot: dict) -> dict:
        """Classify user intent before rule fallbacks.

        Rules are useful for outages, but long-running project state changes
        should not depend on enumerating every possible Chinese expression.
        """
        if not adapter or not (text or "").strip():
            return {}
        prompt = f"""你是 Partner 的轻量意图分类器。只判断用户这条消息要触发什么交互动作。

当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
当前摘要：{(snapshot.get('summary', '') or '')[:400]}

用户消息：
{text}

严格只输出 JSON，不要解释：
{{
  "intent": "status_query|pause_project|start_project|switch_project|share_content|risk_signal|correction|casual|unknown",
  "confidence": 0.0,
  "target_project": "",
  "reason": ""
}}

判断标准：
- 用户说“先做到这/先放一下/以后再继续/换个话题/先停这个”等，intent=pause_project
- 用户提出一个新研究、小项目、demo、调研方向，intent=start_project
- 用户明确要接着另一个已有项目或换到某项目，intent=switch_project
- 用户转发截图、链接、公众号、小红书、B站、知乎、长文、老师建议、灵感材料，intent=share_content
- 用户指出数据泄露、异常好、过拟合、结果不可信、走捷径、幻觉，intent=risk_signal
- 用户纠正项目方向或说“不是做X，是做Y”，intent=correction
- 用户问进展/在做什么/运行如何，intent=status_query
- 不确定时 intent=unknown，confidence<=0.5
"""
        try:
            raw = adapter.chat(prompt, purpose="classify") or ""
        except Exception as exc:
            logger.debug(f"intent classifier LLM failed: {exc}")
            return {}
        text_out = raw.strip()
        if text_out.startswith("```"):
            text_out = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=re.DOTALL).strip()
        start = text_out.find("{")
        end = text_out.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            data = json.loads(text_out[start:end + 1])
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        intent = str(data.get("intent") or "unknown").strip()
        allowed = {
            "status_query", "pause_project", "start_project", "switch_project",
            "share_content", "risk_signal", "correction", "casual", "unknown",
        }
        if intent not in allowed:
            intent = "unknown"
        try:
            confidence = float(data.get("confidence", 0))
        except Exception:
            confidence = 0.0
        return {
            "intent": intent,
            "confidence": max(0.0, min(1.0, confidence)),
            "target_project": str(data.get("target_project") or "").strip()[:50],
            "reason": str(data.get("reason") or "").strip()[:200],
        }

    def _decide_event_with_llm(self, adapter: object, sender_id: str, text: str, snapshot: dict) -> Optional[InteractionDecision]:
        """Let the LLM choose the next event from the current mind context.

        This is the preferred path. It does not predefine task categories like
        "research_project"; it only exposes runtime event primitives and asks
        whether the user message needs one.
        """
        if not (text or "").strip():
            return None
        if not adapter:
            return None
        context = self.get_context(sender_id) or []
        dialog_state = self._get_sender_dialog_state(sender_id)
        pending_followup = dialog_state.get("pending_followup") if isinstance(dialog_state, dict) else {}
        if not isinstance(pending_followup, dict):
            pending_followup = {}
        ctx_lines = []
        for item in context[-6:]:
            role = "用户" if item.get("role") == "user" else "Partner"
            ctx_lines.append(f"{role}: {item.get('text', '')[:180]}")
        try:
            from .mind.pool import MindPool

            pool = MindPool.get_sync_instance()
            pool_stats = pool.stats() if pool else {}
        except Exception:
            pool_stats = {}
        prompt = f"""你是 Partner 的 event selector。你的任务不是给任务贴固定类型标签，而是根据用户消息和当前上下文选择下一步 runtime event。

可用 event primitive：
- direct_reply: 直接用 LLM 回复用户，不进入 agent/mind loop。适合问候、简单问答、无需文件/工具/长期上下文的请求。
- none: 不回复、不进入 agent/mind loop。适合同一用户刚刚重复发送的同一任务，且最近对话或运行状态显示任务已经被接收或正在处理。
- direct_task: 一次性直接交付或具体操作。
- literature_review: 资料、文献、参考依据或方法综述。
- data_analysis: 数据读取、统计、作图、脚本运行或最小分析。
- evidence_audit: 证据真实性、可靠性、泄露、过拟合、引用或结论边界审计。
- artifact_build: 构建用户可看的文件、图表、表格、PPT 或其它非 PDF 产物。
- pdf_report: 把已有结果或摘要整理成 PDF 报告并交付；用户要求“报告/发报告/整理报告/最终汇报”且需要实际交付时选这个。
- project_think: 项目起步、目标拆解、难点识别、路线设计或下一步选择。
- curiosity_explore: 好奇探索。和其它 action event 一样由 selector 根据上下文选择；用于产生新问题、新假设或新探索动作，不由关键词或任务类别硬编码触发/禁止。
	- habit_update: 把用户经验、失败教训、行为习惯写成可复用成长记录。
- project: 兼容旧长期项目生命线；仅当用户明确要求持续推进/长期运行/项目循环时使用。
- content_digest: 消化用户分享的链接、截图、长文或外部材料。
- reflection: 做项目、习惯或经验反思。
- memory_consolidate: 压缩记忆，不直接面向用户。
- pause_project: 用户明确说先做到这里/先放一下/换别的时保存当前项目。
- report: 内部传输事件，不要作为用户请求的目标 event；用户要报告时选择 pdf_report。

当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
状态摘要：{(snapshot.get('summary', '') or '')[:500]}
当前推进：{(snapshot.get('current', '') or snapshot.get('active_plan', '') or '')[:500]}
最近对话：
{chr(10).join(ctx_lines) if ctx_lines else '（无）'}
短期待续对话状态：
{json.dumps(pending_followup, ensure_ascii=False)[:1200] if pending_followup else '（无）'}
Mind pool 状态：{json.dumps(pool_stats, ensure_ascii=False)[:800]}

用户消息：
{text}

严格只输出 JSON：
{{
  "route": "direct_reply|mind_event|pause_project|none",
  "event_type": "direct_task|literature_review|data_analysis|evidence_audit|artifact_build|pdf_report|project_think|curiosity_explore|habit_update|project|content_digest|reflection|memory_consolidate|report",
  "event_kind": "自由短标签",
  "target_project": "",
  "objective": "给 agent 的具体目标；如果 direct_reply 可空",
  "reply_to_user": "给用户的自然回复",
  "pending_action": "none|set|keep|clear",
  "pending_followup": {{
    "original_user_request": "",
    "current_objective": "",
    "missing_slots": [],
    "known_slots": {{}},
    "last_question": ""
  }},
  "stop_after_completion": true,
  "priority": 1,
  "confidence": 0.0,
  "reason": ""
}}

选择原则：
- 不要用关键词模板或固定规则匹配用户消息；基于上下文、最近对话、Mind pool、用户目标和可用 event 定义选择。
- route=none 只在语义重复、已在处理、或确实不应回应时使用。
- direct_reply 只用于不需要进入 mind pool 的即时回答或最小澄清。
- mind_event 用于任何需要执行、产物、资料、审计、分析、探索、记忆或持续推进的任务；event_type 由你选择。
- 用户追问“怎么没发报告 / 做咋样了 / 给我报告 / 整理状态报告”且已有项目结果时，选择 pdf_report，让 mind loop 生成并交付真实 PDF；不要选择 report。
- 如果短期待续状态存在，并且用户新消息是在补充缺失参数，必须把 original_user_request、已知参数和新消息合并成 objective，选择 mind_event；不要把补充参数当成一个新闲聊话题。
- 如果本轮需要向用户追问关键参数，route=direct_reply，pending_action=set，pending_followup 记录原始任务、缺失参数、已知参数和你刚问的问题。
- 如果用户明显换话题且不再继续待续任务，pending_action=clear。
- pause_project 只表示保存/暂停当前生命线。
- target_project、objective、stop_after_completion 必须由你根据用户目标和上下文填写；不确定就保守、具体、可执行。
- reply_to_user 不暴露 event、queue、workspace、backend。
"""
        try:
            raw = adapter.chat(prompt, purpose="classify") or ""
        except Exception as exc:
            logger.debug(f"event selector LLM failed: {exc}")
            return None
        text_out = raw.strip()
        if text_out.startswith("```"):
            text_out = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=re.DOTALL).strip()
        start = text_out.find("{")
        end = text_out.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text_out[start:end + 1])
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        try:
            confidence = float(data.get("confidence") or 0)
        except Exception:
            confidence = 0.0
        if confidence < 0.45:
            return None

        route = str(data.get("route") or "none").strip()
        event_type = str(data.get("event_type") or "").strip()
        event_kind = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", str(data.get("event_kind") or "")).strip("_")[:80]
        target = str(data.get("target_project") or "").strip()
        objective = str(data.get("objective") or "").strip()
        reply = self._sanitize_reply_to_user(str(data.get("reply_to_user") or ""))
        stop_after = bool(data.get("stop_after_completion"))
        priority = data.get("priority", 5)
        try:
            priority = max(1, min(10, int(priority)))
        except Exception:
            priority = 5
        pending_action = str(data.get("pending_action") or "none").strip()
        if pending_action not in {"none", "set", "keep", "clear"}:
            pending_action = "none"
        pending = data.get("pending_followup") if isinstance(data.get("pending_followup"), dict) else {}
        if route == "mind_event" and event_type == "report":
            event_type = "pdf_report"
            event_kind = event_kind or "pdf_report"
            if objective:
                objective = (
                    objective
                    + "\n必须生成真实 PDF 报告文件并在 FILES 中写明路径；不要只发送状态文字。"
                )
            else:
                objective = "整理当前项目已有结果，生成真实 PDF 报告文件并交付给用户。"

        if route == "direct_reply":
            return InteractionDecision(
                reply_to_user=reply or "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
                need_lifeline_update=False,
                lifeline_action="none",
                event_type="direct_reply",
                event_kind=event_kind or "direct_reply",
                stop_after_completion=True,
                priority=priority,
                pending_action=pending_action,
                pending_followup=pending,
            )
        if route == "none":
            return InteractionDecision(
                reply_to_user="",
                need_lifeline_update=False,
                lifeline_action="none",
                event_type=event_type or "direct_reply",
                event_kind=event_kind or "duplicate_or_noop",
                stop_after_completion=True,
                priority=priority,
                pending_action=pending_action,
                pending_followup=pending,
            )
        if route == "pause_project":
            return InteractionDecision(
                reply_to_user=reply,
                need_lifeline_update=True,
                lifeline_action="pause_project",
                target_project=target or get_active(self.workspace) or "",
                note=text,
                event_type="project_think",
                event_kind=event_kind or "pause_project",
                stop_after_completion=True,
                priority=priority,
                pending_action="clear",
            )
        action_event_types = {
            "direct_task",
            "literature_review",
            "data_analysis",
            "evidence_audit",
            "artifact_build",
            "pdf_report",
            "project_think",
            "curiosity_explore",
            "habit_update",
            "project",
        }
        if route == "mind_event" and event_type in action_event_types:
            if not target:
                target = self._infer_project_title_with_llm(adapter, text, snapshot) or "用户任务"
            return InteractionDecision(
                reply_to_user=reply or "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
                need_lifeline_update=True,
                lifeline_action="add_task",
                target_project=target,
                task_title=target,
                task_description=objective or text,
                event_type=event_type,
                event_kind=event_kind or "project_step",
                stop_after_completion=stop_after,
                priority=priority,
                pending_action="clear",
            )
        if route == "mind_event" and event_type in {"content_digest", "reflection", "memory_consolidate"}:
            return InteractionDecision(
                reply_to_user=reply or "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
                need_lifeline_update=True,
                lifeline_action="add_task" if event_type == "content_digest" else "add_note",
                target_project=target or get_active(self.workspace) or "",
                task_title=target or event_kind or event_type,
                task_description=objective or text,
                note=objective or text,
                event_type=event_type,
                event_kind=event_kind or event_type,
                stop_after_completion=stop_after,
                priority=priority,
                pending_action="clear" if event_type != "memory_consolidate" else pending_action,
                pending_followup=pending,
            )
        return None

    def _decide(self, sender_id: str, text: str) -> InteractionDecision:
        snapshot = self.snapshot_builder() or {}
        adapter = self.get_adapter()
        event_decision = self._decide_event_with_llm(adapter, sender_id, text, snapshot)
        if event_decision:
            return event_decision
        return InteractionDecision(
            reply_to_user="__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
            need_lifeline_update=False,
            lifeline_action="none",
            event_type="direct_reply",
            event_kind="selector_unavailable",
            stop_after_completion=True,
            priority=9,
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
            reply = "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__"
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
            event_type=str(data.get("event_type") or "project").strip() or "project",
            delivery_mode=str(data.get("delivery_mode") or "research_project").strip()
            if str(data.get("delivery_mode") or "").strip() in {"research_project", "reference_brief", "direct_deliverable", "audit_only"}
            else "research_project",
            priority=priority,
        )

    @staticmethod
    def _ground_scope_item_in_user_text(item: str, raw_text: str) -> str:
        """Return the grounded user-text anchor for a scope item, or empty.

        The interaction LLM may paraphrase, but long-term project boundaries
        should not gain new technical terms that the user did not provide.
        Keep long-term project boundaries grounded in user-provided terms.
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
            self._drop_stale_project_events(target)
            if decision.note:
                append_log(self.workspace, target, decision.note)
            self._touch_active_plan(target, f"用户要求切换并推进：{target}")
            self._log_mutation(action, target, raw_text)
            self._nudge_project(
                target,
                priority=2,
                source="interaction:switch_project",
                delivery_mode=decision.delivery_mode,
                user_request=raw_text,
                event_type=decision.event_type,
                event_kind=decision.event_kind,
                stop_after_completion=decision.stop_after_completion,
            )
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
            previous = get_active(self.workspace) or ""
            if target and previous and target != previous and target not in previous and previous not in target:
                try:
                    from .project_state import set_project_status

                    set_project_status(self.workspace, previous, "waiting", f"用户切换到新项目：{target}")
                    release_project(self.workspace, previous, reason=f"用户切换到新项目：{target}")
                    self._log_mutation("release_previous_project", previous, f"new_project={target}")
                except Exception as exc:
                    logger.debug(f"failed to release previous project before switching: {exc}")
            if target:
                set_active(self.workspace, target)
                try:
                    from .project_state import set_project_status

                    set_project_status(self.workspace, target, "active", f"用户追加任务：{decision.task_title or raw_text[:80]}")
                except Exception as exc:
                    logger.debug(f"failed to activate project on add_task: {exc}")
            if target and decision.priority <= 2:
                self._drop_stale_project_events(target)
            description = decision.task_description or raw_text
            if (
                target
                and (
                    decision.allowed_scope
                    or decision.forbidden_scope
                    or decision.current_mainline
                    or decision.completion_criteria
                )
            ):
                guardrail = {
                    "raw_text": raw_text,
                    "allowed_scope": decision.allowed_scope or [],
                    "forbidden_scope": decision.forbidden_scope or [],
                    "current_mainline": decision.current_mainline or "",
                    "source_roots": decision.source_roots or self._extract_paths(raw_text),
                    "forbidden_evidence_patterns": decision.forbidden_evidence_patterns or [],
                    "completion_criteria": decision.completion_criteria or [],
                }
                record_project_guardrail(self.workspace, target, guardrail)
            is_risk_signal = decision.event_type == "evidence_audit"
            if is_risk_signal:
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
                    self._notify_growth_write(
                        target or "当前项目",
                        "用户的经验判断可能指出模型结果异常、泄露或伪提升，不能当成普通聊天忽略。",
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
            self._nudge_project(
                target or task.title,
                priority=2,
                source="interaction:add_task",
                delivery_mode=decision.delivery_mode,
                user_request=description,
                event_type=decision.event_type,
                event_kind=decision.event_kind,
                stop_after_completion=decision.stop_after_completion,
            )
            return

        if action == "pause_project":
            target = decision.target_project or get_active(self.workspace) or "当前项目"
            try:
                from .project_state import set_project_status

                set_project_status(self.workspace, target, "waiting", decision.note or raw_text)
                release_project(self.workspace, target, reason=decision.note or raw_text)
            except Exception as exc:
                logger.debug(f"failed to pause/release project: {exc}")
            self._log_mutation(action, target, decision.note or raw_text)
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
            self._notify_growth_write(
                target,
                "用户纠偏会改变项目边界，不能只回复“收到”。",
            )
            self._touch_active_plan(target, f"用户纠偏：{raw_text[:120]}")
            self._log_mutation(action, target, raw_text)
            self._nudge_project(
                target,
                priority=1,
                source="interaction:correct_direction",
                delivery_mode=decision.delivery_mode,
                user_request=raw_text,
                event_type=decision.event_type,
                event_kind=decision.event_kind,
                stop_after_completion=decision.stop_after_completion,
            )
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

    def _nudge_project(self, title: str, priority: int = 2, source: str = "interaction",
                       delivery_mode: str = "research_project", user_request: str = "",
                       event_type: str = "project", event_kind: str = "",
                       stop_after_completion: bool = False):
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
            event_type_value = str(event_type or "project").strip().lower()
            try:
                resolved_event_type = EventType(event_type_value)
            except Exception:
                resolved_event_type = EventType.PROJECT
            pool.put_threadsafe(MindEvent(
                type=resolved_event_type,
                priority=priority,
                payload={
                    "title": title,
                    "step": 0,
                    "delivery_mode": delivery_mode if delivery_mode in {"research_project", "reference_brief", "direct_deliverable", "audit_only"} else "research_project",
                    "user_request": user_request[:2000] if user_request else "",
                    "event_type": event_type_value,
                    "event_kind": event_kind[:120] if event_kind else "",
                    "stop_after_completion": bool(stop_after_completion),
                },
                source=source,
            ))
            self._record_enqueued_event(title, priority, source, delivery_mode, user_request, event_kind, stop_after_completion, event_type_value)
        except Exception as exc:
            logger.debug(f"failed to nudge project event: {exc}")

    def _record_enqueued_event(self, title: str, priority: int, source: str,
                               delivery_mode: str, user_request: str,
                               event_kind: str = "", stop_after_completion: bool = False,
                               event_type: str = "project"):
        try:
            state_dir = os.path.join(self.workspace, "state")
            os.makedirs(state_dir, exist_ok=True)
            path = os.path.join(state_dir, "event_decisions.jsonl")
            row = {
                "ts": datetime.now().isoformat(),
                "event": "enqueue_project",
                "title": title,
                "priority": priority,
                "source": source,
                "event_type": event_type,
                "delivery_mode": delivery_mode,
                "event_kind": event_kind,
                "stop_after_completion": bool(stop_after_completion),
                "user_request": (user_request or "")[:1200],
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug(f"failed to record enqueued event: {exc}")

    def _drop_stale_project_events(self, keep_title: str):
        try:
            from .mind.pool import MindPool

            pool = MindPool.get_sync_instance()
            if pool:
                pool.drop_project_events_except(keep_title)
        except Exception as exc:
            logger.debug(f"failed to drop stale project events: {exc}")

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
