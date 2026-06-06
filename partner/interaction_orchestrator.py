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
from .outbound_policy import UNAVAILABLE_NOTICE, prefix_event_notice

logger = logging.getLogger(__name__)


def _clip_title(text: str, suffix: str = "") -> str:
    return ""


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
                workspace=self.workspace,
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

    def _recent_deliverable_context(self, limit: int = 12, query: str = "") -> str:
        """Return recent user-facing files so selector can attach follow-up requests.

        This is not intent routing; it is external context. The selector still
        decides whether a file is relevant to the user message.
        """
        exts = {".xlsx", ".xls", ".csv", ".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg", ".webp", ".txt"}
        skip_prefixes = (
            "state/",
            "logs/",
            "10_logs/",
            "system/hermes_home/",
            "system/checks/",
        )
        rows: list[tuple[float, str]] = []
        root = os.path.abspath(self.workspace)
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            if rel_dir == ".":
                rel_dir = ""
            if rel_dir.startswith(("system/hermes_home", "logs", "10_logs", "state")):
                dirnames[:] = []
                continue
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext not in exts:
                    continue
                path = os.path.join(dirpath, name)
                try:
                    rel = os.path.relpath(path, root).replace(os.sep, "/")
                    if rel.startswith(skip_prefixes):
                        continue
                    rows.append((os.path.getmtime(path), rel))
                except OSError:
                    continue
        if not rows:
            return "（无）"
        rows.sort(reverse=True)
        out = []
        for ts, rel in rows[:limit]:
            try:
                stamp = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
            except Exception:
                stamp = ""
            out.append(f"- {stamp} {rel}")
        return "\n".join(out)

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
        lines = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            lines.append(line)
        text = "\n".join(lines).strip()
        return text

    @staticmethod
    def _selector_has_placeholder_values(*, route: str, event_type: str, event_kind: str,
                                         reply: str, pending_action: str) -> bool:
        """Detect schema-template output that parsed as JSON but is not a decision."""
        enum_values = [route, event_type, pending_action]
        if any("|" in str(value) for value in enum_values if value):
            return True
        placeholders = {
            "自由短标签",
            "短标签",
            "event_kind",
            "给用户的自然回复",
            "direct_reply|mind_event|pause_project|none",
            "none|set|keep|clear",
        }
        values = {str(v).strip() for v in (route, event_type, event_kind, reply, pending_action) if str(v).strip()}
        if values & placeholders:
            return True
        return bool(re.search(r"(给用户的自然回复|自由短标签|none\|set\|keep\|clear|direct_reply\|mind_event)", reply or ""))

    def _objective_review_for_selector_gap(self, *, text: str, snapshot: dict, context_resolution: dict,
                                           selector_data: dict, reply: str, pending_action: str,
                                           pending: dict, priority: int) -> InteractionDecision:
        resolved = str(context_resolution.get("resolved_objective") or selector_data.get("objective") or text).strip()
        target = str(selector_data.get("target_project") or "").strip()
        event_kind = re.sub(
            r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+",
            "_",
            str(selector_data.get("event_kind") or ""),
        ).strip("_")[:80]
        if event_kind in {"自由短标签", "短标签", "event_kind"}:
            event_kind = "selector_repair"
        if not target:
            target = event_kind or "用户任务"
        current = (snapshot.get("current", "") or snapshot.get("active_plan", "") or snapshot.get("summary", "") or "")[:700]
        objective = (
            "selector 输出可解析但不是一个可执行决策。先对齐用户根目标、context_resolution 已知信息、"
            "当前项目状态和缺口，然后选择下一个最小可验证 event；不要因为 selector 模板输出而停止或要求用户重复已知信息。"
            f"\n用户消息：{text[:1200]}"
            f"\ncontext_resolution：{json.dumps(context_resolution, ensure_ascii=False)[:1400]}"
            f"\n上一轮 selector 输出：{json.dumps(selector_data, ensure_ascii=False)[:900]}"
            f"\n当前推进摘要：{current}"
            f"\n根目标：{resolved[:1200]}"
        )
        return InteractionDecision(
            reply_to_user=reply or "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
            need_lifeline_update=True,
            lifeline_action="add_task",
            target_project=target,
            task_title=target,
            task_description=objective,
            note=f"SELECTOR_REPAIR_OBJECTIVE_REVIEW: {text[:1200]}",
            event_type="objective_review",
            event_kind=event_kind or "selector_repair",
            stop_after_completion=True,
            priority=max(1, min(priority, 3)),
            pending_action=pending_action,
            pending_followup=pending,
        )

    def _objective_review_from_route_review(self, *, text: str, snapshot: dict, context_resolution: dict,
                                            selector_data: dict | None = None, route_review: dict | None = None,
                                            reply: str = "", pending_action: str = "none",
                                            pending: dict | None = None, priority: int = 2) -> InteractionDecision:
        selector_data = selector_data or {}
        route_review = route_review or {}
        pending = pending if isinstance(pending, dict) else {}
        resolved = str(
            route_review.get("objective")
            or context_resolution.get("resolved_objective")
            or selector_data.get("objective")
            or text
        ).strip()
        target = str(
            route_review.get("target_project")
            or selector_data.get("target_project")
            or context_resolution.get("related_project")
            or ""
        ).strip() or _clip_title(resolved or text) or "用户任务"
        event_kind = re.sub(
            r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+",
            "_",
            str(route_review.get("event_kind") or selector_data.get("event_kind") or "route_review").strip(),
        ).strip("_")[:80] or "route_review"
        current = (snapshot.get("current", "") or snapshot.get("active_plan", "") or snapshot.get("summary", "") or "")[:700]
        objective = (
            "route_review 判断入口路由存在矛盾或不确定。请只做目标/上下文对齐：核对用户消息、"
            "context_resolution、selector 输出、最近项目状态和缺口，然后选择下一个最小可验证 event；"
            "不要因为入口 selector 失败而停止，不要要求用户重复已知信息。"
            f"\n触发原因：{str(route_review.get('reason') or '')[:500]}"
            f"\n建议动作：{str(route_review.get('recommended_action') or '')[:300]}"
            f"\n用户消息：{text[:1200]}"
            f"\ncontext_resolution：{json.dumps(context_resolution, ensure_ascii=False)[:1400]}"
            f"\nselector 输出：{json.dumps(selector_data, ensure_ascii=False)[:1000]}"
            f"\n当前推进摘要：{current}"
            f"\n根目标：{resolved[:1200]}"
        )
        return InteractionDecision(
            reply_to_user=reply or "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
            need_lifeline_update=True,
            lifeline_action="add_task",
            target_project=target,
            task_title=target,
            task_description=objective,
            note=f"ROUTE_REVIEW_OBJECTIVE_REVIEW: {text[:1200]}",
            event_type="objective_review",
            event_kind=event_kind,
            stop_after_completion=True,
            priority=max(1, min(priority, 3)),
            pending_action=pending_action if pending_action in {"none", "set", "keep", "clear"} else "none",
            pending_followup=pending,
        )

    @staticmethod
    def _action_event_types() -> set[str]:
        return {
            "direct_task",
            "literature_review",
            "data_fetch",
            "data_analysis",
            "visualization",
            "evidence_audit",
            "artifact_build",
            "pdf_report",
            "email_delivery",
            "web_search",
            "web_capture",
            "project_think",
            "objective_review",
            "curiosity_explore",
            "habit_update",
            "project",
        }

    def _objective_review_for_unavailable_selector(self, *, text: str, snapshot: dict,
                                                   reason: str = "selector_unavailable") -> InteractionDecision:
        current = (snapshot.get("current", "") or snapshot.get("active_plan", "") or snapshot.get("summary", "") or "")[:700]
        objective = (
            "入口 selector 没有在时限内产出可执行决策。不要停止，也不要要求用户重复原话。"
            "先用当前上下文对齐用户消息、已有项目、待续参数、可交付物和缺口，再选择下一个最小可验证 event。"
            f"\n失败原因：{reason}"
            f"\n用户消息：{text[:1200]}"
            f"\n当前推进摘要：{current}"
        )
        target = _clip_title(text) or "用户任务"
        return InteractionDecision(
            reply_to_user="__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
            need_lifeline_update=True,
            lifeline_action="add_task",
            target_project=target,
            task_title=target,
            task_description=objective,
            note=f"SELECTOR_UNAVAILABLE_OBJECTIVE_REVIEW: {text[:1200]}",
            event_type="objective_review",
            event_kind=reason,
            stop_after_completion=True,
            priority=2,
            pending_action="none",
        )

    def _route_review_with_llm(self, adapter: object, *, text: str, snapshot: dict,
                               context_resolution: dict | None = None,
                               selector_data: dict | None = None,
                               trigger: str = "",
                               pool_stats: dict | None = None) -> dict:
        """One small judge for suspicious route decisions.

        It handles direct_reply suspicion, selector schema contradictions and
        route=none suspicion through the same path, so routing recovery stays a
        mechanism instead of a pile of task-specific patches.
        """
        if not adapter or not (text or "").strip():
            return {}
        context_resolution = context_resolution or {}
        selector_data = selector_data or {}
        pool_stats = pool_stats or {}
        prompt = f"""你是 Partner 的 route_review 小 judge。你只审计入口路由是否自洽，不回答用户、不执行任务。

可用动作：
- accept: 当前 route 可以执行。
- objective_review: 当前 route 矛盾/不确定，需要进入 objective_review 对齐目标、上下文、缺口和下一最小 event。

审计重点：
- direct_reply 是否误吞了需要执行、搜索、生成文件、发送、读附件、继续项目或获取当前事实的目标。
- selector 是否输出了 schema 模板、无效 event_type、route 与 context_resolution 矛盾。
- route=none 是否误吞了新约束、补充参数、附件/链接/合并转发、状态恢复或未完成交付。
- 如果只是缺关键参数且 context_resolution 已明确 missing_slots/user_visible_boundary，accept。
- 不按关键词硬匹配；根据语义、上下文和已有状态判断。

当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
状态摘要：{(snapshot.get('summary', '') or snapshot.get('current', '') or snapshot.get('active_plan', '') or '')[:360]}
Mind pool：{json.dumps(pool_stats, ensure_ascii=False)[:260]}
触发点：{trigger}

用户消息：
{text[:1200]}

context_resolution：
{json.dumps(context_resolution, ensure_ascii=False)[:1400]}

selector/direct route：
{json.dumps(selector_data, ensure_ascii=False)[:1200]}

只输出 JSON：
{{"action":"accept|objective_review","reason":"","event_kind":"route_review","target_project":"","objective":"","recommended_action":""}}
"""
        raw = self._quick_classify_chat(adapter, prompt, max_tokens=120)
        if not raw:
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
        action = str(data.get("action") or "").strip()
        if action not in {"accept", "objective_review"}:
            action = "objective_review"
        return {
            "action": action,
            "reason": str(data.get("reason") or "").strip()[:500],
            "event_kind": str(data.get("event_kind") or "route_review").strip()[:120],
            "target_project": str(data.get("target_project") or "").strip()[:120],
            "objective": str(data.get("objective") or "").strip()[:1200],
            "recommended_action": str(data.get("recommended_action") or "").strip()[:500],
        }

    def _quick_classify_chat(self, adapter: object, prompt: str, *, max_tokens: int = 180) -> str:
        """Use the fastest available classifier path without waiting on large fallbacks."""
        lite = getattr(adapter, "lite", None)
        if lite is not None:
            try:
                raw = lite.chat(prompt, max_tokens=max_tokens, purpose="classify") or ""
                if raw and raw != "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__":
                    return raw
            except Exception as exc:
                logger.debug(f"quick lite classify failed: {exc}")
            return ""
        try:
            return adapter.chat(prompt, max_tokens=max_tokens, purpose="classify") or ""
        except Exception as exc:
            logger.debug(f"quick classify failed: {exc}")
            return ""

    def _lean_decide_with_llm(self, adapter: object, *, sender_id: str, text: str, snapshot: dict,
                              ctx_lines: list[str], pending_followup: dict,
                              pool_stats: dict) -> Optional[InteractionDecision]:
        """Small selector for short inbound messages so long context cannot block the entrance."""
        if not adapter or len(text or "") > 120:
            return None
        prompt = f"""你是 Partner 的 lean event selector。只根据最少上下文选择下一步，不回答任务本身。

可选 route：
- direct_reply：只适合无需当前信息、无需外部访问、无需文件/产物、无需继续项目的普通对话或澄清。
- mind_event：需要执行、搜索、读取当前信息、生成文件、继续项目、整理产物或写记忆。
- none：只有同一消息已在执行时才用。

可选 event_type：
direct_task, literature_review, data_fetch, data_analysis, visualization, evidence_audit, artifact_build, pdf_report, email_delivery, web_search, web_capture, project_think, objective_review, curiosity_explore, habit_update, project, content_digest, reflection, memory_consolidate

当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
状态摘要：{(snapshot.get('summary', '') or '')[:180]}
最近对话：
{chr(10).join(ctx_lines[-3:]) if ctx_lines else '（无）'}
待续参数：
{json.dumps(pending_followup, ensure_ascii=False)[:500] if pending_followup else '（无）'}
Mind pool：{json.dumps(pool_stats, ensure_ascii=False)[:220]}

用户消息：
{text}

只输出 JSON：
{{"route":"","event_type":"","event_kind":"","target_project":"","objective":"","reply_to_user":"","pending_action":"none","stop_after_completion":true,"priority":1,"confidence":0.0,"reason":""}}
"""
        raw = self._quick_classify_chat(adapter, prompt, max_tokens=180)
        if not raw:
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
        route = str(data.get("route") or "none").strip()
        event_type = str(data.get("event_type") or "").strip()
        event_kind = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", str(data.get("event_kind") or "")).strip("_")[:80]
        if event_kind in {"自由短标签", "短标签", "event_kind"}:
            event_kind = "lean_selector"
        reply = self._sanitize_reply_to_user(str(data.get("reply_to_user") or ""))
        pending_action = str(data.get("pending_action") or "none").strip()
        if pending_action not in {"none", "set", "keep", "clear"}:
            pending_action = "none"
        if self._selector_has_placeholder_values(
            route=route,
            event_type=event_type,
            event_kind=event_kind,
            reply=reply,
            pending_action=pending_action,
        ):
            return None
        action_event_types = self._action_event_types()
        if route in action_event_types:
            event_type = route
            route = "mind_event"
        if route not in {"direct_reply", "mind_event", "none"} and event_type in action_event_types:
            route = "mind_event"
        try:
            confidence = float(data.get("confidence") if data.get("confidence") is not None else 0.7)
        except Exception:
            confidence = 0.7
        if confidence < 0.45:
            return None
        try:
            priority = max(1, min(10, int(data.get("priority", 3))))
        except Exception:
            priority = 3
        objective = str(data.get("objective") or "").strip()
        target = str(data.get("target_project") or "").strip()
        route, event_type, event_kind, objective = self._normalize_to_small_event_with_llm(
            adapter,
            route=route,
            event_type=event_type,
            event_kind=event_kind,
            objective=objective,
            user_text=text,
            context_resolution={},
        )
        if route == "direct_reply":
            local_reply = self._direct_reply_from_selector_draft(
                adapter,
                text=text,
                draft=reply,
                snapshot=snapshot,
                ctx_lines=ctx_lines,
            )
            return InteractionDecision(
                reply_to_user=local_reply or reply or "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
                need_lifeline_update=False,
                lifeline_action="none",
                event_type="direct_reply",
                event_kind=event_kind or "lean_direct_reply",
                stop_after_completion=True,
                priority=priority,
                pending_action=pending_action,
            )
        if route == "mind_event" and event_type in action_event_types:
            if not target:
                target = event_kind or _clip_title(objective or text) or "用户任务"
            return InteractionDecision(
                reply_to_user=reply or "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
                need_lifeline_update=True,
                lifeline_action="add_task",
                target_project=target,
                task_title=target,
                task_description=objective or text,
                event_type=event_type,
                event_kind=event_kind or "lean_selector",
                stop_after_completion=bool(data.get("stop_after_completion", True)),
                priority=priority,
                pending_action=pending_action,
            )
        return None

    def _normalize_to_small_event_with_llm(
        self,
        adapter: object,
        *,
        route: str,
        event_type: str,
        event_kind: str,
        objective: str,
        user_text: str,
        context_resolution: dict | None = None,
    ) -> tuple[str, str, str, str]:
        """Ask a small judge whether an execution event is too large."""
        if route != "mind_event":
            return route, event_type, event_kind, objective
        context_resolution = context_resolution or {}
        if context_resolution.get("relation") in {"existing_artifact", "pending_followup"}:
            return route, event_type, event_kind, objective
        action_types = {
            "direct_task",
            "data_fetch",
            "data_analysis",
            "visualization",
            "artifact_build",
            "web_search",
            "web_capture",
            "literature_review",
            "evidence_audit",
            "pdf_report",
        }
        if event_type not in action_types:
            return route, event_type, event_kind, objective
        if not adapter:
            return route, event_type, event_kind, objective
        prompt = f"""你是 Partner 的 small_event_boundary judge。判断 selector 选出的执行 event 是否太大。

原则：
- 一个执行 event 只能做一个可验证动作，并有一个清楚验收标准。
- 如果它把多个依赖阶段合在一起，例如取数+分析+绘图+报告+发送，应判 too_large。
- 如果它只是澄清、拆解、审计一个结论、生成一个文件、读取一个数据源、画一张图等单步动作，应判 ok。
- 不要按关键词硬匹配；根据 objective 是否能被一次 event 稳定完成判断。

用户原始消息：
{user_text[:1000]}

selector event：{event_type}/{event_kind}
selector objective：
{objective[:1400]}

context_resolution：
{json.dumps(context_resolution, ensure_ascii=False)[:900]}

只输出 JSON：
{{"verdict":"ok|too_large","reason":"","first_step_objective":""}}
"""
        raw = self._quick_classify_chat(adapter, prompt, max_tokens=120)
        if not raw:
            return route, event_type, event_kind, objective
        text_out = raw.strip()
        if text_out.startswith("```"):
            text_out = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=re.DOTALL).strip()
        start = text_out.find("{")
        end = text_out.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return route, event_type, event_kind, objective
        try:
            data = json.loads(text_out[start:end + 1])
        except Exception:
            return route, event_type, event_kind, objective
        if str(data.get("verdict") or "").strip() == "too_large":
            first_step = str(data.get("first_step_objective") or "").strip()
            return (
                route,
                "project_think",
                event_kind or "split_goal",
                (
                    "把用户目标拆成多个小 event。本轮只做目标拆解、验收标准和第一个最小可验证动作选择；"
                    "不要在本 event 里执行取数、搜索、分析、绘图、写报告、发邮件或安装依赖。"
                    f"\n原始目标：{user_text[:1200]}"
                    + (f"\nsmall_event_boundary 建议的第一步：{first_step[:800]}" if first_step else "")
                ),
            )
        return route, event_type, event_kind, objective

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

    def _resolve_context_with_llm(self, adapter: object, *, sender_id: str, text: str,
                                  snapshot: dict, ctx_lines: list[str],
                                  pending_followup: dict, recent_files: str,
                                  pool_stats: dict) -> dict:
        """Preflight context-resolution event before choosing an action event."""
        if not adapter or not (text or "").strip():
            return {}
        prompt = f"""你是 Partner 的 context_resolution event。

你的任务不是回答用户，也不是选择执行 event，而是先判断用户这条消息和已有上下文的关系。

可用上下文：
- 普通对话记录：判断是否是在接上刚才说的话。
- 短期待续对话状态：判断是否是在补充缺失参数。
- 当前/历史项目状态：判断是否和已有 project 相关。
- 最近可交付文件：判断是否是在索要、转发、邮件发送、转换或继续处理已有产物。
- Mind pool 状态：判断是否只是重复催促正在执行的同一任务。

当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
状态摘要：{(snapshot.get('summary', '') or '')[:320]}
当前推进：{(snapshot.get('current', '') or snapshot.get('active_plan', '') or '')[:320]}
最近对话：
{chr(10).join(ctx_lines) if ctx_lines else '（无）'}
短期待续对话状态：
{json.dumps(pending_followup, ensure_ascii=False)[:900] if pending_followup else '（无）'}
最近可交付文件：
{recent_files}
Mind pool 状态：{json.dumps(pool_stats, ensure_ascii=False)[:400]}

用户消息：
{text}

严格只输出 JSON：
{{
  "relation": "new_task|direct_conversation|pending_followup|existing_project|existing_artifact|status_check|duplicate_or_running|unclear",
  "should_direct_reply": false,
  "should_enter_mind": true,
  "related_project": "",
  "related_files": [],
  "known_slots": {{}},
  "missing_slots": [],
  "resolved_objective": "把用户消息和相关上下文合并后的目标；不要编造",
  "user_visible_boundary": "需要向用户说明的边界或缺失信息；没有写空",
  "reason": ""
}}

判断原则：
- 如果用户说“今天生成的/刚才那个/之前的/近期的表格/报告/文件”，优先检查最近可交付文件；如果有明显匹配，relation=existing_artifact，related_files 写相对路径或文件名，不要再要求用户重复说明已能从文件名看出的主题/城市。
- 如果用户是在补 SMTP 授权码、发件邮箱、地点、文件路径等缺失信息，relation=pending_followup。
- 如果只是普通闲聊或知识问答且无需工具/文件/项目，should_direct_reply=true。
- 如果需要搜索、生成文件、发邮件、改文件、读附件、继续项目，should_enter_mind=true。
- 如果上下文不足但必须澄清，missing_slots 写具体缺什么，user_visible_boundary 写清楚该问什么。
- 不要输出自然语言解释，只输出 JSON。
"""
        try:
            raw = adapter.chat(prompt, purpose="classify") or ""
        except Exception as exc:
            logger.debug(f"context resolution LLM failed: {exc}")
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
        relation = str(data.get("relation") or "unclear").strip()
        if relation not in {
            "new_task",
            "direct_conversation",
            "pending_followup",
            "existing_project",
            "existing_artifact",
            "status_check",
            "duplicate_or_running",
            "unclear",
        }:
            relation = "unclear"
        return {
            "relation": relation,
            "should_direct_reply": bool(data.get("should_direct_reply")),
            "should_enter_mind": bool(data.get("should_enter_mind")),
            "related_project": str(data.get("related_project") or "").strip()[:120],
            "related_files": [str(x).strip() for x in (data.get("related_files") or []) if str(x).strip()][:8],
            "known_slots": data.get("known_slots") if isinstance(data.get("known_slots"), dict) else {},
            "missing_slots": [str(x).strip() for x in (data.get("missing_slots") or []) if str(x).strip()][:8],
            "resolved_objective": str(data.get("resolved_objective") or "").strip()[:1800],
            "user_visible_boundary": str(data.get("user_visible_boundary") or "").strip()[:800],
            "reason": str(data.get("reason") or "").strip()[:500],
        }

    def _direct_reply_from_context_resolution(self, adapter: object, *, text: str, snapshot: dict,
                                              ctx_lines: list[str], context_resolution: dict) -> str:
        if not adapter:
            return ""
        prompt = f"""你是 Partner 的轻量直接回复模块。用户消息不需要进入任务队列或工具执行。

要求：
- 只自然回复用户当前消息，不创建任务、不承诺后台执行。
- 可以参考最近对话和 context_resolution，但不要机械复述项目状态。
- 如果 context_resolution.user_visible_boundary 有内容，简短说明边界并询问必要补充。
- 不暴露 event、queue、workspace、backend。
- 输出纯文本，不要 JSON。

当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
状态摘要：{(snapshot.get('summary', '') or '')[:220]}
最近对话：
{chr(10).join(ctx_lines[-3:]) if ctx_lines else '（无）'}
context_resolution：
{json.dumps(context_resolution, ensure_ascii=False)[:900]}

用户消息：
{text}
"""
        try:
            return self._sanitize_reply_to_user(adapter.chat(prompt, purpose="interaction") or "")
        except Exception as exc:
            logger.debug(f"direct reply generation failed: {exc}")
            return ""

    def _direct_reply_from_selector_draft(self, adapter: object, *, text: str, draft: str,
                                          snapshot: dict, ctx_lines: list[str]) -> str:
        if not adapter or not (draft or "").strip():
            return ""
        prompt = f"""你是 Partner 的轻量直接回复模块。上游 selector 已判断这条消息只需要直接回复，不需要进入任务队列。

请基于用户消息和上游草稿，生成一句自然、简短、不过度展开的中文回复。

约束：
- 保持草稿的核心意思，但可以去掉多余项目状态或机械套话。
- 不创建任务，不承诺后台执行。
- 不暴露 event、queue、workspace、backend。
- 输出纯文本，不要 JSON。

当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
状态摘要：{(snapshot.get('summary', '') or '')[:180]}
最近对话：
{chr(10).join(ctx_lines[-3:]) if ctx_lines else '（无）'}

用户消息：
{text}

上游草稿：
{draft[:800]}
"""
        try:
            return self._sanitize_reply_to_user(adapter.chat(prompt, purpose="interaction") or "")
        except Exception as exc:
            logger.debug(f"direct reply draft generation failed: {exc}")
            return ""

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
        for item in context[-3:]:
            role = "用户" if item.get("role") == "user" else "Partner"
            ctx_lines.append(f"{role}: {item.get('text', '')[:120]}")
        try:
            from .mind.pool import MindPool

            pool = MindPool.get_sync_instance()
            pool_stats = pool.stats() if pool else {}
        except Exception:
            pool_stats = {}
        recent_files = self._recent_deliverable_context(limit=12, query=text)
        lean_decision = self._lean_decide_with_llm(
            adapter,
            sender_id=sender_id,
            text=text,
            snapshot=snapshot,
            ctx_lines=ctx_lines,
            pending_followup=pending_followup,
            pool_stats=pool_stats,
        )
        if lean_decision:
            return lean_decision
        context_resolution = self._resolve_context_with_llm(
            adapter,
            sender_id=sender_id,
            text=text,
            snapshot=snapshot,
            ctx_lines=ctx_lines,
            pending_followup=pending_followup,
            recent_files=recent_files,
            pool_stats=pool_stats,
        )
        consistency = self._route_review_with_llm(
            adapter,
            text=text,
            snapshot=snapshot,
            context_resolution=context_resolution,
            selector_data={
                "route": "direct_reply",
                "event_type": "direct_reply",
                "event_kind": "context_direct_reply",
            },
            trigger="context_resolution_direct_reply",
            pool_stats=pool_stats,
        ) if context_resolution and context_resolution.get("should_direct_reply") and not context_resolution.get("should_enter_mind") else {}
        if (
            context_resolution
            and context_resolution.get("should_direct_reply")
            and not context_resolution.get("should_enter_mind")
            and consistency
            and consistency.get("action") == "objective_review"
        ):
            return self._objective_review_from_route_review(
                text=text,
                snapshot=snapshot,
                context_resolution={
                    **context_resolution,
                    "route_review": consistency,
                },
                selector_data={"route": "direct_reply", "event_type": "direct_reply"},
                route_review=consistency,
                priority=2,
            )
        if (
            context_resolution
            and context_resolution.get("should_direct_reply")
            and not context_resolution.get("should_enter_mind")
        ):
            boundary = str(context_resolution.get("user_visible_boundary") or "").strip()
            missing = context_resolution.get("missing_slots") or []
            reply = self._direct_reply_from_context_resolution(
                adapter,
                text=text,
                snapshot=snapshot,
                ctx_lines=ctx_lines,
                context_resolution=context_resolution,
            )
            pending_action = "set" if missing else "clear"
            pending = {
                "original_user_request": text[:1200],
                "current_objective": str(context_resolution.get("resolved_objective") or text)[:1200],
                "missing_slots": [str(x) for x in missing][:8],
                "known_slots": context_resolution.get("known_slots") if isinstance(context_resolution.get("known_slots"), dict) else {},
                "last_question": boundary[:800] if boundary else "",
            } if missing else {}
            return InteractionDecision(
                reply_to_user=reply or boundary or "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
                need_lifeline_update=False,
                lifeline_action="none",
                event_type="direct_reply",
                event_kind="context_direct_reply",
                stop_after_completion=True,
                priority=1,
                pending_action=pending_action,
                pending_followup=pending,
            )
        prompt = f"""你是 Partner 的 event selector。只选择下一步 route/event，不执行任务，不写详细执行方案。

可用 route：direct_reply, mind_event, pause_project, none。
可用 event：
- direct_task: 单步直接交付或具体操作
- literature_review: 资料/文献/方法依据整理
- data_fetch: 只获取/下载/保存一个真实数据源
- data_analysis: 只读取已有数据并做统计、质量检查或最小分析
- visualization: 只基于已有数据/结果绘制图表
- evidence_audit: 证据、结论、泄露、可靠性审计
- artifact_build: 非 PDF 文件、表格、PPT、代码等产物构建
- pdf_report: 把已有或本轮结果整理成真实 PDF 报告
- email_delivery: 发送已有或本轮文件到邮箱
- web_search: 搜索公开网页、平台、论文库、数据库并整理来源
- web_capture: 下载公开图片/文件或网页截图
- project_think: 拆解目标、选择路线、定义验收和第一个小 event
- objective_review: 对齐用户目标、上下文、已完成内容、缺口和下一 event
- curiosity_explore: 好奇探索与新假设
- habit_update: 写入习惯/经验/成长
- project, content_digest, reflection, memory_consolidate: 兼容长期项目、内容消化、反思、记忆压缩

当前项目：{snapshot.get('display_project', '') or snapshot.get('focus_project', '')}
状态摘要：{(snapshot.get('summary', '') or '')[:260]}
当前推进：{(snapshot.get('current', '') or snapshot.get('active_plan', '') or '')[:260]}
最近对话：
{chr(10).join(ctx_lines) if ctx_lines else '（无）'}
短期待续对话状态：
{json.dumps(pending_followup, ensure_ascii=False)[:500] if pending_followup else '（无）'}
最近可交付文件：
{recent_files}
context_resolution 结果：
{json.dumps(context_resolution, ensure_ascii=False)[:1600] if context_resolution else '（无，按原始上下文保守判断）'}
Mind pool 状态：{json.dumps(pool_stats, ensure_ascii=False)[:300]}

用户消息：
{text}

严格只输出 JSON：
{{
  "route": "direct_reply|mind_event|pause_project|none",
  "event_type": "direct_task|literature_review|data_fetch|data_analysis|visualization|evidence_audit|artifact_build|pdf_report|email_delivery|web_search|web_capture|project_think|objective_review|curiosity_explore|habit_update|project|content_digest|reflection|memory_consolidate|report",
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
- 先服从 context_resolution；如果它和你的选择矛盾，写明 reason。
- direct_reply 只用于即时回答或缺参澄清；需要执行/文件/搜索/发送/继续项目时选 mind_event。
- route=none 只用于确实同一任务正在处理且无需吸收新信息。
- mind_event 的 objective 只写一个最小可验证目标；多阶段目标选 project_think。
- 需要追问时 pending_action=set，并记录 pending_followup；补齐参数时合并上下文进入 mind_event。
- 输出必须是合法 JSON，不暴露 queue/workspace/backend。
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
            return self._recover_selector_decision_from_text(text_out)
        if not isinstance(data, dict):
            return None
        route = str(data.get("route") or "none").strip()
        event_type = str(data.get("event_type") or "").strip()
        event_kind = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", str(data.get("event_kind") or "")).strip("_")[:80]
        if event_kind in {"自由短标签", "短标签", "event_kind"}:
            event_kind = ""
        target = str(data.get("target_project") or "").strip()
        objective = str(data.get("objective") or "").strip()
        reply = self._sanitize_reply_to_user(str(data.get("reply_to_user") or ""))
        action_event_types = self._action_event_types()
        if route in action_event_types:
            if not event_type or event_type not in action_event_types:
                event_type = route
            route = "mind_event"
        if route not in {"direct_reply", "mind_event", "pause_project", "none"} and event_type in action_event_types:
            route = "mind_event"
        try:
            raw_confidence = data.get("confidence")
            confidence = float(raw_confidence) if raw_confidence is not None else 0.7
        except Exception:
            confidence = 0.7 if (route in {"direct_reply", "mind_event", "pause_project"} or event_type or reply) else 0.0
        if confidence < 0.45 and not (route == "mind_event" and event_type in action_event_types):
            return None
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
        route, event_type, event_kind, objective = self._normalize_to_small_event_with_llm(
            adapter,
            route=route,
            event_type=event_type,
            event_kind=event_kind,
            objective=objective,
            user_text=text,
            context_resolution=context_resolution,
        )
        content_event_types = {"content_digest", "reflection", "memory_consolidate"}
        selector_placeholder = self._selector_has_placeholder_values(
            route=route,
            event_type=event_type,
            event_kind=event_kind,
            reply=reply,
            pending_action=pending_action,
        )
        invalid_mind_event = route == "mind_event" and event_type not in (action_event_types | content_event_types)
        none_against_context = route == "none" and bool(context_resolution.get("should_enter_mind"))
        suspicious_route = selector_placeholder or invalid_mind_event or none_against_context
        route_review = self._route_review_with_llm(
            adapter,
            text=text,
            snapshot=snapshot,
            context_resolution=context_resolution,
            selector_data=data,
            trigger=(
                "selector_placeholder" if selector_placeholder else
                "invalid_mind_event" if invalid_mind_event else
                "none_against_context" if none_against_context else
                "selector_review"
            ),
            pool_stats=pool_stats,
        ) if suspicious_route else {}
        if suspicious_route and route_review.get("action") != "accept":
            if not route_review:
                route_review = {
                    "action": "objective_review",
                    "reason": "route_review_unavailable_after_suspicious_selector",
                    "event_kind": "route_review_unavailable",
                }
            return self._objective_review_from_route_review(
                text=text,
                snapshot=snapshot,
                context_resolution=context_resolution,
                selector_data=data,
                route_review=route_review,
                reply=reply,
                pending_action=pending_action,
                pending=pending,
                priority=priority,
            )

        if route == "direct_reply":
            local_reply = self._direct_reply_from_selector_draft(
                adapter,
                text=text,
                draft=reply,
                snapshot=snapshot,
                ctx_lines=ctx_lines,
            )
            return InteractionDecision(
                reply_to_user=local_reply or reply or "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__",
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
            route_review = self._route_review_with_llm(
                adapter,
                text=text,
                snapshot=snapshot,
                context_resolution=context_resolution,
                selector_data=data,
                trigger="route_none",
                pool_stats=pool_stats,
            )
            if route_review.get("action") != "accept":
                if not route_review:
                    route_review = {
                        "action": "objective_review",
                        "reason": "route_review_unavailable_after_route_none",
                        "event_kind": "route_none_review_unavailable",
                    }
                active_target = (snapshot.get("display_project", "") or snapshot.get("focus_project", "") or target or get_active(self.workspace) or "").strip()
                route_review = {**route_review, "target_project": route_review.get("target_project") or active_target}
                return self._objective_review_from_route_review(
                    text=text,
                    snapshot=snapshot,
                    context_resolution=context_resolution,
                    selector_data=data,
                    route_review=route_review,
                    reply=reply,
                    pending_action=pending_action,
                    pending=pending,
                    priority=2,
                )
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
        if route == "mind_event" and event_type in action_event_types:
            current_project = (snapshot.get("display_project", "") or snapshot.get("focus_project", "") or "").strip()
            if context_resolution.get("relation") == "new_task" and target and current_project and target == current_project:
                target = ""
            if not target:
                target = event_kind or _clip_title(objective or text) or "用户任务"
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

    def _recover_selector_decision_from_text(self, raw: str) -> Optional[InteractionDecision]:
        """Recover a user-visible LLM decision when JSON breaks on natural text.

        The selector sometimes writes a useful reply but forgets to escape quotes
        inside reply_to_user. We should not drop that LLM output and leave QQ with
        only the thinking notice.
        """
        route = self._extract_selector_string_field(raw, "route")
        reply = self._sanitize_reply_to_user(self._extract_selector_string_field(raw, "reply_to_user"))
        if route == "direct_reply" and reply:
            event_kind = self._extract_selector_string_field(raw, "event_kind") or "direct_reply"
            event_kind = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", event_kind).strip("_")[:80]
            return InteractionDecision(
                reply_to_user=reply,
                need_lifeline_update=False,
                lifeline_action="none",
                event_type="direct_reply",
                event_kind=event_kind or "direct_reply",
                stop_after_completion=True,
                priority=5,
                pending_action="none",
            )
        return None

    @staticmethod
    def _extract_selector_string_field(raw: str, field: str) -> str:
        text = raw or ""
        key = re.escape(field)
        next_key = r'"[A-Za-z_][A-Za-z0-9_]*"\s*:'
        pattern = rf'"{key}"\s*:\s*"(?P<value>.*?)(?="\s*,\s*{next_key}|"\s*\}})'
        match = re.search(pattern, text, flags=re.DOTALL)
        if not match:
            return ""
        value = match.group("value")
        value = value.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
        return value.strip()

    def _decide(self, sender_id: str, text: str) -> InteractionDecision:
        snapshot = self.snapshot_builder() or {}
        adapter = self.get_adapter()
        event_decision = self._decide_event_with_llm(adapter, sender_id, text, snapshot)
        if event_decision:
            return event_decision
        if (text or "").strip():
            return self._objective_review_for_unavailable_selector(
                text=text,
                snapshot=snapshot,
                reason="selector_unavailable",
            )
        return InteractionDecision(
            reply_to_user=UNAVAILABLE_NOTICE,
            need_lifeline_update=False,
            lifeline_action="none",
            event_type="direct_reply",
            event_kind="selector_unavailable",
            stop_after_completion=True,
            priority=9,
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
