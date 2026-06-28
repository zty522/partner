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
import queue
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional

import yaml

from ..journal import Journal, JournalEntry
from ..knowledge.knowledge import KnowledgeBase, KnowledgeEntry
from ..projects.project_state import (
    append_log,
    clear_active,
    get_active,
    record_project_guardrail,
    set_active,
)
from ..knowledge.research_memory import record_episode, record_growth_event, record_user_signal
from ..knowledge.research_memory import record_risk_event
from ..knowledge.research_guardrails import record_user_signal_to_mind
from ..knowledge.content_feed import record_shared_content
from ..projects.project_registry import (
    find_project,
    import_public_project_context,
    instance_id_from_workspace,
    project_location_hint,
    release_project,
)
from ..state.state import StateManager
from ..dialogue.user_text_safety import has_internal_diff, strip_internal_diff
from ..tasks.task_queue import TaskQueue, Task
from ..dialogue.outbound_policy import THINKING_NOTICE, UNAVAILABLE_NOTICE, prefix_event_notice
from ..mind import MindEvent, EventType

logger = logging.getLogger(__name__)


DEFAULT_ROUTING_RULES = {
    "routing": {
        "force_event_type": "batch_plan",
        "prefer_project_think": [],
        "allow_direct_delivery_if": [],
    }
}


# ── LLM-based direct reply routing (replaces hardcoded regex patterns) ──

_DRECT_REPLY_CLASSIFICATION_PROMPT = """你是一个消息分类器。判断用户消息：

1. 如果消息是简单的问候、感谢、再见、简单定义查询、天气/时间/汇率查询等，不需要多步执行就能回答，则输出 {"type": "direct_reply", "reply": "你的直接回复"}

2. 如果消息是需要多步执行的任务（数据分析、代码生成、文件操作、研究分析、项目推进、文件路径操作、调用特定工具/agent等），则输出 {"type": "complex_task"}

重要规则：
- 包含文件路径（如 /data/xxx.h5ad、/mnt/xxx 等）的消息一定是 complex_task
- 包含工具/agent/模型名称（如 cytobridge、pancreas.h5ad、单细胞、轨迹推断等专业术语）的消息一定是 complex_task
- 包含具体数据分析操作（如轨迹推断、差异分析、聚类、降维等）的消息一定是 complex_task
- 只有纯粹的问候（你好、嗨、在吗）、感谢（谢谢）、告别（再见）才可能是 direct_reply
- 不确定时优先分类为 complex_task

仅输出 JSON，不要多余内容。用户消息："""

def _classify_for_direct_reply(adapter, text: str) -> dict | None:
    """Use LLM to classify whether a message should get a direct reply or complex task execution.

    Returns None if classification fails (caller falls through to batch_plan).
    """
    if not adapter or not (text or "").strip():
        return None
    try:
        prompt = _DRECT_REPLY_CLASSIFICATION_PROMPT + text
        reply = adapter.chat(prompt, purpose="direct_reply_classify")
        if not reply or not reply.strip():
            return None
        # Clean up common LLM wrapping
        cleaned = reply.strip()
        if cleaned.startswith("```"):
            # Remove code fences
            cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        cleaned = cleaned.strip()
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
        return None
    except (json.JSONDecodeError, Exception) as exc:
        logger.debug("[DIRECT_REPLY_CLASSIFY] LLM classification failed: %s", exc)
        return None


def _try_direct_reply_llm_based(self, text: str) -> Optional[InteractionDecision]:
    """LLM-based direct reply routing: classify then possibly generate reply.

    Replaces the old _try_direct_reply_fast_path that used hardcoded regex patterns.
    Returns InteractionDecision if message is a direct_reply candidate, None for complex_task.
    """
    if not (text or "").strip():
        return None

    adapter = self.get_adapter()
    if not adapter:
        return None

    # Step 1: Classify via LLM
    classification = _classify_for_direct_reply(adapter, text)
    if not classification:
        logger.debug("[ROUTING] direct_reply classification failed or returned empty")
        return None

    if classification.get("type") != "direct_reply":
        logger.debug("[ROUTING] LLM classified as complex_task, routing to batch_plan")
        return None

    # Step 2: Use pre-generated reply from classification
    reply_text = classification.get("reply", "")
    if not reply_text or not reply_text.strip():
        logger.debug("[ROUTING] direct_reply classification had no reply, falling through")
        return None

    sanitized = self._sanitize_reply_to_user(reply_text)
    if not sanitized:
        return None

    # NOTE: The USER_MESSAGE handler in executor.py creates the DIRECT_REPLY event
    # from this InteractionDecision, so we do NOT enqueue a separate event here.

    return InteractionDecision(
        reply_to_user=sanitized,
        need_lifeline_update=False,
        event_type="interaction_reply",
        event_kind="direct_reply",
        stop_after_completion=True,
    )


EVENT_CAPABILITIES = {
    "batch_plan": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "direct_task": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "artifact_build": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "pdf_report": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "visualization": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "web_capture": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "file_inspection": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "email_delivery": {"can_deliver_artifacts": False, "can_execute_atomic_plan": True, "planning_only": False},
    "data_fetch": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "data_analysis": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "web_search": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "literature_review": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "evidence_audit": {"can_deliver_artifacts": True, "can_execute_atomic_plan": True, "planning_only": False},
    "ollama_status": {"can_deliver_artifacts": False, "can_execute_atomic_plan": True, "planning_only": False},
    "project_think": {"can_deliver_artifacts": False, "can_execute_atomic_plan": False, "planning_only": True},
    "objective_review": {"can_deliver_artifacts": False, "can_execute_atomic_plan": False, "planning_only": True},
    "curiosity_explore": {"can_deliver_artifacts": False, "can_execute_atomic_plan": False, "planning_only": True},
    "habit_update": {"can_deliver_artifacts": False, "can_execute_atomic_plan": False, "planning_only": True},
    "project": {"can_deliver_artifacts": False, "can_execute_atomic_plan": False, "planning_only": True},
    "content_digest": {"can_deliver_artifacts": False, "can_execute_atomic_plan": False, "planning_only": False},
    "reflection": {"can_deliver_artifacts": False, "can_execute_atomic_plan": False, "planning_only": True},
    "memory_consolidate": {"can_deliver_artifacts": False, "can_execute_atomic_plan": False, "planning_only": True},
}


def _is_generic_event_title(title: str) -> bool:
    raw = str(title or "").strip()
    if not raw:
        return True
    normalized = raw.lower()
    generic = {
        "用户任务",
        "当前项目",
        "任务",
        "新任务",
        "task",
        "project",
        "report",
    }
    return normalized in EVENT_CAPABILITIES or raw in EVENT_CAPABILITIES or normalized in generic or raw in generic


def _title_from_objective(*values: str) -> str:
    for value in values:
        title = _clip_title(value)
        if title and not _is_generic_event_title(title):
            return title
    return "用户任务"


def _event_capability(event_type: str) -> dict:
    return EVENT_CAPABILITIES.get(str(event_type or "").strip(), {})


def _normalize_expected_artifacts(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "file").strip().lower()
        pattern = str(item.get("pattern") or item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if kind == "file" and not pattern:
            continue
        out.append({
            "type": kind,
            "pattern": pattern,
            "description": description,
            "required": bool(item.get("required", True)),
        })
    return out[:8]


def _normalize_artifact_freshness_policy(raw: object) -> str:
    value = str(raw or "new").strip().lower()
    if value in {"new", "reuse_allowed", "continue_task"}:
        return value
    return "new"


def _deep_merge_dict(base: dict, patch: dict) -> dict:
    out = dict(base or {})
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _clip_title(text: str, suffix: str = "") -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return suffix.strip()[:80]
    raw = re.sub(r"^(请|帮我|麻烦|能不能|可以帮我|你可以|我要|我想)\s*", "", raw, flags=re.I)
    raw = re.sub(r"[。！？!?；;，,]+", " ", raw).strip()
    parts = [p for p in raw.split(" ") if p]
    title = " ".join(parts[:2]) if parts else raw
    title = re.sub(r"[^\w\u4e00-\u9fff ._+-]+", "_", title).strip(" _.-")
    if len(title) > 48:
        title = title[:48].rstrip(" _.-")
    if suffix:
        title = f"{title}_{suffix.strip()}" if title else suffix.strip()
    return title[:80] or "用户任务"


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
    task_instance_id: str = ""
    task_working_dir: str = ""
    continue_from_project: str = ""
    delivery_required: bool = False
    expected_artifacts: list[dict] = None
    artifact_freshness_policy: str = "new"
    reuse_existing_artifact: bool = False
    reuse_reason: str = ""




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
        self._routing_rules_cache: dict | None = None

    def _load_routing_rules(self) -> dict:
        if self._routing_rules_cache is not None:
            return self._routing_rules_cache
        candidates = [
            os.path.join(self.workspace, "config", "routing_rules.yaml"),
            os.path.join(self.workspace, "routing_rules.yaml"),
            # Also try workspace root config (one level up from instances/XX/)
            os.path.join(os.path.dirname(os.path.dirname(self.workspace)), "config", "routing_rules.yaml"),
            os.path.join(os.path.dirname(self.workspace), "config", "routing_rules.yaml"),
        ]
        config = dict(DEFAULT_ROUTING_RULES)
        for path in candidates:
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                if isinstance(loaded, dict):
                    config = _deep_merge_dict(config, loaded)
                    config["_config_path"] = path
                    break
            except Exception as exc:
                logger.debug(f"failed to load routing rules from {path}: {exc}")
        self._routing_rules_cache = config
        return config

    def _try_direct_reply_fast_path(self, text: str) -> Optional[InteractionDecision]:
        """Direct reply routing: only matched by configured routing rules (routing_rules.yaml).
        All other messages go directly to batch_plan — the planner handles both
        simple queries and complex tasks.
        """
        # Check routing_rules.yaml for direct_reply patterns (if any)
        config = self._load_routing_rules()
        routing = config.get("routing") if isinstance(config.get("routing"), dict) else {}
        direct_reply_cfg = routing.get("direct_reply") if isinstance(routing.get("direct_reply"), dict) else {}
        if not direct_reply_cfg.get("enabled", True):
            return None
        patterns = direct_reply_cfg.get("patterns")
        if not isinstance(patterns, list) or not patterns:
            return None
        use_regex = bool(direct_reply_cfg.get("use_regex", False))
        matched = False
        for pattern in patterns:
            pattern = str(pattern).strip()
            if not pattern:
                continue
            try:
                if use_regex:
                    if re.search(pattern, text, re.I | re.S):
                        matched = True
                        break
                else:
                    if pattern.lower() in text.lower():
                        matched = True
                        break
            except re.error:
                continue
        if not matched:
            return None
        logger.info("[ROUTING] direct_reply pattern matched for: %s", text[:80])
        # If matched by rule, call LLM once for the direct reply
        adapter = self.get_adapter()
        if not adapter:
            return InteractionDecision(
                reply_to_user="抱歉，我暂时无法回复",
                need_lifeline_update=False,
                event_type="interaction_reply",
                event_kind="direct_reply",
                stop_after_completion=True,
            )
        try:
            reply = adapter.chat(text, purpose="direct_reply")
            if reply and reply.strip() and "PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE" not in reply:
                sanitized = self._sanitize_reply_to_user(reply)
                if sanitized:
                    return InteractionDecision(
                        reply_to_user=sanitized,
                        need_lifeline_update=False,
                        event_type="interaction_reply",
                        event_kind="direct_reply",
                        stop_after_completion=True,
                    )
        except Exception as exc:
            logger.warning("[ROUTING] direct_reply LLM failed: %s", exc)
        return None

    @staticmethod
    def _first_matching_rule(text: str, rules: object) -> str:
        if not isinstance(rules, list):
            return ""
        for item in rules:
            pattern = ""
            if isinstance(item, dict):
                pattern = str(item.get("pattern") or "").strip()
            else:
                pattern = str(item or "").strip()
            if not pattern:
                continue
            try:
                if re.search(pattern, text or "", flags=re.I | re.S):
                    return pattern
            except re.error as exc:
                logger.debug(f"invalid routing pattern {pattern!r}: {exc}")
        return ""

    def _apply_routing_rules(self, decision: InteractionDecision, user_text: str, task: object | None = None) -> InteractionDecision:
        config = self._load_routing_rules()
        routing = config.get("routing") if isinstance(config.get("routing"), dict) else {}
        prefer_pattern = self._first_matching_rule(user_text, routing.get("prefer_project_think"))
        allow_pattern = self._first_matching_rule(user_text, routing.get("allow_direct_delivery_if"))
        if not prefer_pattern or allow_pattern:
            if _is_generic_event_title(decision.target_project or decision.task_title):
                target = _title_from_objective(user_text, decision.task_description, decision.target_project, decision.task_title)
                decision.target_project = target
                decision.task_title = target
            return decision
        forced_event = str(routing.get("force_event_type") or "batch_plan").strip() or "batch_plan"
        if forced_event not in self._action_event_types():
            forced_event = "batch_plan" if "batch_plan" in self._action_event_types() else "project_think"
        logger.info("[ROUTING] matched pattern %s -> force %s", prefer_pattern, forced_event)
        if task and hasattr(task, "append_log"):
            try:
                task.append_log("routing_decision", {
                    "message": f"[ROUTING] matched pattern {prefer_pattern} -> force {forced_event}",
                    "matched_pattern": prefer_pattern,
                    "allow_pattern": allow_pattern,
                    "forced_event_type": forced_event,
                    "previous_event_type": decision.event_type,
                    "previous_event_kind": decision.event_kind,
                    "config_path": config.get("_config_path") or "",
                })
            except Exception:
                pass
        target = decision.target_project or decision.task_title or _clip_title(user_text) or "用户任务"
        if _is_generic_event_title(target):
            target = _title_from_objective(user_text, decision.task_description, target)
        if decision.event_type == forced_event:
            decision.need_lifeline_update = True
            decision.lifeline_action = "add_task"
            decision.target_project = target
            decision.task_title = target
            if not decision.event_kind or str(decision.event_kind).startswith("routing_prefer_"):
                decision.event_kind = target or "direct"
            decision.stop_after_completion = False
            decision.priority = min(decision.priority or 3, 2)
            return decision
        objective = (
            "按路由规则进入复杂任务批量规划；一次生成可执行 Harness MicroPlan，"
            "再由 Harness 执行依赖和并行步骤。"
            f"\n原始用户目标：{user_text[:1600]}"
            + (f"\n原 selector 目标：{str(decision.task_description or '')[:1200]}" if decision.task_description else "")
        )
        return InteractionDecision(
            reply_to_user=decision.reply_to_user or THINKING_NOTICE,
            need_lifeline_update=True,
            lifeline_action="add_task",
            target_project=target,
            task_title=target,
            task_description=objective,
            delivery_mode=decision.delivery_mode,
            event_type=forced_event,
            event_kind=target or "direct",
            stop_after_completion=False,
            priority=min(decision.priority or 3, 2),
            pending_action=decision.pending_action,
            pending_followup=decision.pending_followup,
            task_instance_id=decision.task_instance_id,
            task_working_dir=decision.task_working_dir,
            continue_from_project=decision.continue_from_project,
            delivery_required=decision.delivery_required,
            expected_artifacts=decision.expected_artifacts,
            artifact_freshness_policy=decision.artifact_freshness_policy,
            reuse_existing_artifact=decision.reuse_existing_artifact,
            reuse_reason=decision.reuse_reason,
        )

    def _notify_growth_write(self, project: str, learned: str):
        """保留兼容 — Harness 接管了事件分发。"""
        logger.debug("_notify_growth_write no-op (Harness handles REPORT events)")

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
            "system/hermes_home/",
            "system/checks/",
        )
        rows: list[tuple[float, str]] = []
        root = os.path.abspath(self.workspace)
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            if rel_dir == ".":
                rel_dir = ""
            if rel_dir.startswith(("system/hermes_home", "logs", "state/record", "state")):
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
        lowered = text.strip().lower()
        if lowered in {"true", "false", "null", "none", "undefined", "{}", "[]", '""', "''"}:
            return ""
        if re.fullmatch(r"""["']?(?:true|false|null|none|undefined)["']?""", lowered):
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

    @staticmethod
    def _looks_actionable_without_selector(text: str) -> bool:
        """Conservative fallback when the selector is unavailable.

        Fallback path for selector outages. Do not classify intent by keywords
        here; non-empty user text is passed to objective_review.
        """
        raw = str(text or "").strip()
        return bool(raw)

    @staticmethod
    def _delivery_mode_for_request(text: str, event_type: str = "") -> tuple[str, bool]:
        """Default execution scope without parsing user intent keywords."""
        _ = text, event_type
        return "research_project", False

    @staticmethod
    def _fallback_event_for_actionable_request(text: str) -> tuple[str, str]:
        _ = text
        return "direct_task", "selector_unavailable_direct_task"

    @staticmethod
    def _sanitize_selector_event_kind(event_kind: str, event_type: str = "") -> str:
        cleaned = re.sub(
            r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+",
            "_",
            str(event_kind or ""),
        ).strip("_")[:80]
        if cleaned in {"自由短标签", "短标签", "event_kind"}:
            return ""
        if (
            cleaned in {"reference_brief", "requested_artifact", "requested_visualization", "requested_report_after_visualization"}
            or cleaned.endswith("_data_fetch")
            or cleaned.endswith("_research_replan")
            or cleaned.endswith("selector_unavailable_replan")
        ):
            return f"selector_{event_type or 'event'}"
        return cleaned

    def _objective_review_for_selector_gap(self, *, text: str, snapshot: dict, context_resolution: dict,
                                           selector_data: dict, reply: str, pending_action: str,
                                           pending: dict, priority: int) -> InteractionDecision:
        resolved = str(context_resolution.get("resolved_objective") or selector_data.get("objective") or text).strip()
        target = str(selector_data.get("target_project") or "").strip()
        event_kind = self._sanitize_selector_event_kind(
            str(selector_data.get("event_kind") or ""),
            str(selector_data.get("event_type") or ""),
        )
        if event_kind in {"自由短标签", "短标签", "event_kind"}:
            event_kind = "selector_repair"
        if not target or _is_generic_event_title(target):
            target = _title_from_objective(resolved, text, event_kind)
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
            reply_to_user=reply or THINKING_NOTICE,
            need_lifeline_update=True,
            lifeline_action="add_task",
            target_project=target,
            task_title=target,
            task_description=objective,
            note=f"SELECTOR_REPAIR_OBJECTIVE_REVIEW: {text[:1200]}",
            event_type="objective_review",
            event_kind=event_kind or "selector_repair",
            stop_after_completion=False,
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
        if _is_generic_event_title(target):
            target = _title_from_objective(resolved, text, target)
        event_kind = self._sanitize_selector_event_kind(
            str(route_review.get("event_kind") or selector_data.get("event_kind") or "route_review").strip(),
            str(selector_data.get("event_type") or ""),
        ) or "route_review"
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
            reply_to_user=reply or THINKING_NOTICE,
            need_lifeline_update=True,
            lifeline_action="add_task",
            target_project=target,
            task_title=target,
            task_description=objective,
            note=f"ROUTE_REVIEW_OBJECTIVE_REVIEW: {text[:1200]}",
            event_type="objective_review",
            event_kind=event_kind,
            stop_after_completion=False,
            priority=max(1, min(priority, 3)),
            pending_action=pending_action if pending_action in {"none", "set", "keep", "clear"} else "none",
            pending_followup=pending,
        )

    @staticmethod
    def _action_event_types() -> set[str]:
        return {
            "direct_task",
            "batch_plan",
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
            "file_inspection",
            "project_think",
            "objective_review",
            "curiosity_explore",
            "habit_update",
            "ollama_status",
            "project",
        }

    def _objective_review_for_unavailable_selector(self, *, text: str, snapshot: dict,
                                                   reason: str = "selector_unavailable") -> InteractionDecision:
        """Selector failed — report error to user, do not add task."""
        _ = text, snapshot  # unused in this simplified version
        event_kind = reason
        return InteractionDecision(
            reply_to_user=UNAVAILABLE_NOTICE,
            need_lifeline_update=False,
            lifeline_action="none",
            event_type="interaction_reply",
            event_kind=event_kind,
            stop_after_completion=True,
            priority=1,
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
        timeout_sec = _env_int("PARTNER_ENTRY_SELECTOR_TIMEOUT_SEC", 0)
        lite = getattr(adapter, "lite", None)
        if lite is not None:
            try:
                raw = self._call_llm_with_deadline(
                    lambda: lite.chat(prompt, max_tokens=max_tokens, purpose="classify") or "",
                    timeout_sec=timeout_sec,
                    label="quick_lite_classify",
                )
                if raw and "PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE" not in raw:
                    return raw
            except Exception as exc:
                logger.debug(f"quick lite classify failed: {exc}")
        try:
            return self._call_llm_with_deadline(
                lambda: adapter.chat(prompt, max_tokens=max_tokens, purpose="classify") or "",
                timeout_sec=timeout_sec,
                label="quick_classify",
            )
        except Exception as exc:
            logger.debug(f"quick classify failed: {exc}")
            return ""

    @staticmethod
    def _call_llm_with_deadline(call: Callable[[], str], *, timeout_sec: int, label: str) -> str:
        """Call entrance LLM; timeout_sec <= 0 disables the outer deadline."""
        if int(timeout_sec or 0) <= 0:
            try:
                return call() or ""
            except Exception as exc:
                logger.debug("%s failed: %s", label, exc)
                return ""
        result_q: queue.Queue[tuple[bool, str]] = queue.Queue(maxsize=1)

        def runner() -> None:
            try:
                result_q.put((True, call() or ""), block=False)
            except Exception as exc:
                result_q.put((False, repr(exc)), block=False)

        worker = threading.Thread(target=runner, name=f"partner-entry-{label}", daemon=True)
        worker.start()
        worker.join(max(1, int(timeout_sec or 1)))
        if worker.is_alive():
            logger.warning("%s timed out after %ss; falling back to queued mind event", label, timeout_sec)
            return ""
        try:
            ok, value = result_q.get_nowait()
        except queue.Empty:
            return ""
        if not ok:
            logger.debug("%s failed: %s", label, value)
            return ""
        return value

    def _lean_decide_with_llm(self, adapter: object, *, sender_id: str, text: str, snapshot: dict,
                              ctx_lines: list[str], pending_followup: dict,
                              pool_stats: dict) -> Optional[InteractionDecision]:
        """Small selector for short inbound messages so long context cannot block the entrance."""
        if not adapter or len(text or "") > 120:
            return None
        capability_table = json.dumps(EVENT_CAPABILITIES, ensure_ascii=False)
        prompt = f"""你是 Partner 的 lean event selector。只根据最少上下文选择下一步，不回答任务本身。

可选 route：
- direct_reply：直接基于已知信息或逻辑分析回答用户问题。即使需要实时数据，如果不需要交付文件、不需要执行外部操作，也可以用 direct_reply 给出一般性分析或建议。
- mind_event：需要执行代码、操作文件、写文件、发邮件等需要工具的实际操作。

路由优先级规则（重要）：
- 纯信息查询（问天气、新闻、股价、名词解释）→ direct_reply
- 研究整理类任务（整理/综述/调研/梳理/归纳某领域的方法/进展/现状/技术）→ mind_event + literature_review
  - 注意：「整理看看」「梳理一下」「调研一下」「归纳总结」「综述」等动词是研究整理信号
  - 这类任务需要工具配合（搜索文献、组织信息），不是简单直接回复能完成的
- 其他情况：direct_reply > mind_event

输出形式判断原则（重要）：
- 如果用户只是询问信息、建议、查询实时数据（天气/新闻/股价），默认走 direct_reply：
  - 给出基于已知信息的一般性分析、建议或查询途径
  - delivery_required=false, expected_artifacts=[]
- 研究整理类任务的 delivery_required 根据用户是否要求输出文件决定：
  - 用户说「整理看看」→ delivery_required=false（先出文本回复），但走 mind_event
  - 用户说「整理成表格/报告/文件」→ delivery_required=true
- 只有用户明确要求表格、文件、报告、保存、导出等具体产出物时：
  - 才走 mind_event + 设置 delivery_required=true
  - 并在 expected_artifacts 中列出具体产出物
- 不要默认要求文件输出

可选 event_type：
batch_plan, direct_task, literature_review, data_fetch, data_analysis, visualization, evidence_audit, artifact_build, pdf_report, email_delivery, web_search, web_capture, file_inspection, project_think, objective_review, curiosity_explore, habit_update, ollama_status, project, content_digest, reflection, memory_consolidate

event_type 选择说明：
- literature_review: 资料/文献/方法依据整理。当用户要求整理/综述/调研/梳理某领域的方法、技术、进展时优先选择。这是研究性任务，走 mind_event 路线。
- web_search: 需要获取实时数据或搜索当前信息
- data_analysis: 已有数据的统计分析

Event capability metadata：
{capability_table}

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
{{"route":"","event_type":"","event_kind":"","target_project":"","objective":"","reply_to_user":"","delivery_required":false,"expected_artifacts":[],"artifact_freshness_policy":"new","reuse_existing_artifact":false,"reuse_reason":"","pending_action":"none","stop_after_completion":true,"priority":1,"confidence":0.0,"reason":""}}

规则：
- 如果当前请求需要最终给用户交付文件/结构化产物，把 delivery_required 设为 true，并在 expected_artifacts 写出类型、pattern、description。
- 如果 delivery_required=true 且 expected_artifacts 含 file，不要选择 planning_only event，也不要选择 can_deliver_artifacts=false 的 event。
- artifact_freshness_policy 默认 new：每条新用户消息创建新 TaskInstance，必须在本轮工作目录生成新交付物。
- 只有用户明确要求继续/重发/复用已有产物，才可设 artifact_freshness_policy=reuse_allowed 或 continue_task，并说明 reuse_reason。
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
        event_kind = self._sanitize_selector_event_kind(str(data.get("event_kind") or ""), event_type)
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
            reply = ""  # mind_event 由执行结果回复，不要 selector 的回复
        if route not in {"direct_reply", "mind_event", "none"} and event_type in action_event_types:
            route = "mind_event"
            reply = ""  # mind_event 由执行结果回复，不要 selector 的回复
        # mind_event 不需要 selector 的回复——实际执行结果会通过 Harness 发送
        if route == "mind_event" and event_type in action_event_types:
            reply = ""
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
        expected_artifacts = _normalize_expected_artifacts(data.get("expected_artifacts"))
        delivery_required = bool(data.get("delivery_required") or expected_artifacts)
        artifact_freshness_policy = _normalize_artifact_freshness_policy(data.get("artifact_freshness_policy"))
        reuse_existing_artifact = bool(data.get("reuse_existing_artifact")) and artifact_freshness_policy != "new"
        reuse_reason = str(data.get("reuse_reason") or "").strip()
        route, event_type, event_kind, objective = self._normalize_to_small_event_with_llm(
            adapter,
            route=route,
            event_type=event_type,
            event_kind=event_kind,
            objective=objective,
            user_text=text,
            delivery_required=delivery_required,
            expected_artifacts=expected_artifacts,
            context_resolution={},
        )
        if delivery_required and not reuse_existing_artifact:
            objective = self._enforce_new_artifact_objective(objective or text)
        if route == "direct_reply" and not delivery_required:
            if not reply:
                logger.debug("lean selector chose direct_reply but reply_to_user is empty — falling through to full selector")
                return None
            return InteractionDecision(
                reply_to_user=reply,
                need_lifeline_update=False,
                lifeline_action="none",
                event_type="interaction_reply",
                event_kind=event_kind or "lean_direct_reply",
                stop_after_completion=True,
                priority=priority,
                pending_action=pending_action,
            )
        if route == "mind_event" and event_type in action_event_types:
            if not target or _is_generic_event_title(target):
                target = _title_from_objective(objective, text, event_kind)
            delivery_mode, inferred_stop = self._delivery_mode_for_request(text, event_type=event_type)
            return InteractionDecision(
                reply_to_user=reply or THINKING_NOTICE,
                need_lifeline_update=True,
                lifeline_action="add_task",
                target_project=target,
                task_title=target,
                task_description=objective or text,
                event_type=event_type,
                event_kind=event_kind or "lean_selector",
                stop_after_completion=bool(data.get("stop_after_completion")) or inferred_stop,
                delivery_mode=delivery_mode,
                priority=priority,
                pending_action=pending_action,
                delivery_required=delivery_required,
                expected_artifacts=expected_artifacts,
                artifact_freshness_policy=artifact_freshness_policy,
                reuse_existing_artifact=reuse_existing_artifact,
                reuse_reason=reuse_reason,
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
        delivery_required: bool = False,
        expected_artifacts: list[dict] | None = None,
        context_resolution: dict | None = None,
    ) -> tuple[str, str, str, str]:
        """Ask a small judge whether an execution event is too large."""
        if route != "mind_event":
            return route, event_type, event_kind, objective
        context_resolution = context_resolution or {}
        if context_resolution.get("relation") in {"existing_artifact", "pending_followup"}:
            return route, event_type, event_kind, objective
        expected_artifacts = expected_artifacts or []
        if delivery_required and expected_artifacts and not _event_capability(event_type).get("can_deliver_artifacts"):
            return route, "direct_task", event_kind or "deliver_artifact", objective
        action_types = {
            "direct_task",
            "data_fetch",
            "data_analysis",
            "visualization",
            "artifact_build",
            "web_search",
            "web_capture",
            "file_inspection",
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
- Partner Harness 可以在一个 direct_task 内先规划、再执行多个 AtomicEvent；例如“获取 JSON 数据并生成一个 CSV/Markdown 表格文件”仍然是一个可验证交付，应判 ok。
- 如果 selector 已声明 delivery_required=true 且 expected_artifacts 非空，不能降级到不具备交付能力的 planning_only event。
- 只有当目标包含多个独立交付物、长期研究链、安装/训练/多轮实验、或无法在一次可验证交付中完成时，才判 too_large。
- 如果它只是澄清、拆解、审计一个结论、生成一个文件、读取一个数据源、画一张图等单步动作，应判 ok。

用户原始消息：
{user_text[:1000]}

selector event：{event_type}/{event_kind}
selector objective：
{objective[:1400]}

context_resolution：
{json.dumps(context_resolution, ensure_ascii=False)[:900]}

delivery_required：{json.dumps(bool(delivery_required), ensure_ascii=False)}
expected_artifacts：
{json.dumps(expected_artifacts, ensure_ascii=False)[:900]}

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

    def _enforce_new_artifact_objective(self, objective: str) -> str:
        marker = "本 TaskInstance 必须在当前 working_dir 生成本轮新的期望交付物；不要把历史项目文件、最近文件或旧 task 目录中的产物作为本轮完成证据。"
        objective = str(objective or "").strip()
        if marker in objective:
            return objective
        return f"{objective}\n{marker}" if objective else marker

    def _handle_direct_llm(self, text: str, use_ollama: bool = False) -> str:
        try:
            adapter = self.get_adapter()
            if use_ollama:
                try:
                    from ..llm.ollama_probe import ollama_chat
                    reply = ollama_chat(text)
                    if reply and len(reply.strip()) > 3:
                        logger.info("[DIRECT_LLM] responded via Ollama")
                        return reply.strip()
                except Exception as ollama_exc:
                    logger.debug("[DIRECT_LLM] Ollama fallback failed: %s", ollama_exc)
            if adapter:
                reply = adapter.chat(text, purpose="direct_reply")
                if reply:
                    return reply.strip()
        except Exception as exc:
            logger.warning("[DIRECT_LLM] failed: %s", exc)
        return "收到"

    def handle_message(self, sender_id: str, sender_name: str, text: str) -> InteractionDecision:
        task = None
        cleaned_text = text
        try:
            from ..harness_core import TaskInstance, parse_continue_project_marker

            cleaned_text, continue_from_project = parse_continue_project_marker(text)
            task = TaskInstance.create(
                self.workspace,
                text,
                continue_from_project=continue_from_project,
                metadata={"sender_id": sender_id, "sender_name": sender_name, "entry": "interaction_orchestrator"},
            )
        except Exception as exc:
            logger.debug(f"failed to create task instance: {exc}")
            continue_from_project = ""
            cleaned_text = text

        # ── Direct-reply LLM-based routing ──
        # Use LLM to decide if this message is a simple query (direct reply) or complex task (batch plan)
        try:
            direct_reply_result = _try_direct_reply_llm_based(self, cleaned_text)
            if direct_reply_result is not None:
                if task:
                    direct_reply_result.task_instance_id = task.task_id
                    direct_reply_result.task_working_dir = task.working_dir
                    direct_reply_result.continue_from_project = task.continue_from_project
                self._record_event_decision(sender_id, cleaned_text, direct_reply_result)
                self._update_sender_dialog_state(sender_id, direct_reply_result, cleaned_text)
                return direct_reply_result
        except Exception as exc:
            logger.debug("[ROUTING] direct_reply fast path failed: %s", exc)

        decision = self._batch_plan_for_message(cleaned_text, sender_id)
        if decision and not decision.need_lifeline_update:
            # Try Ollama first if suitable
            if decision.reply_to_user:
                try:
                    from ..task_router import classify
                    cls = classify(cleaned_text)
                    if cls.get("use_ollama", False):
                        from ..llm.ollama_probe import ollama_chat
                        ollama_reply = ollama_chat(cleaned_text)
                        if ollama_reply and len(ollama_reply.strip()) > 3:
                            decision.reply_to_user = ollama_reply.strip()
                except Exception:
                    pass
            if task:
                decision.task_instance_id = task.task_id
                decision.task_working_dir = task.working_dir
                decision.continue_from_project = task.continue_from_project
            self._record_event_decision(sender_id, cleaned_text, decision)
            self._update_sender_dialog_state(sender_id, decision, cleaned_text)
            return decision

        if task:
            decision.task_instance_id = task.task_id
            decision.task_working_dir = task.working_dir
            decision.continue_from_project = task.continue_from_project
            if task.continue_from_project and decision.artifact_freshness_policy == "new":
                decision.artifact_freshness_policy = "continue_task"
                decision.reuse_existing_artifact = True
                decision.reuse_reason = f"explicit continue_from_project={task.continue_from_project}"
        else:
            decision.continue_from_project = continue_from_project
        decision = self._apply_routing_rules(decision, cleaned_text, task=task)
        if task:
            decision.task_instance_id = task.task_id
            decision.task_working_dir = task.working_dir
            decision.continue_from_project = task.continue_from_project
        self._record_event_decision(sender_id, cleaned_text, decision)
        if decision.need_lifeline_update:
            self._apply_lifeline_update(decision, sender_id=sender_id, sender_name=sender_name, raw_text=cleaned_text)
        self._update_sender_dialog_state(sender_id, decision, cleaned_text)
        return decision

    def _batch_plan_for_message(self, text: str, sender_id: str) -> InteractionDecision:
        """Simplified routing: after fast path check, route directly to batch_plan."""
        from ..dialogue.outbound_policy import THINKING_NOTICE
        title = _clip_title(text)
        return InteractionDecision(
            reply_to_user=THINKING_NOTICE,
            need_lifeline_update=True,
            lifeline_action="add_task",
            target_project=title,
            task_title=title,
            event_type="batch_plan",
            event_kind="direct",
            stop_after_completion=False,
            priority=6,
        )

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
                "task_instance_id": decision.task_instance_id,
                "task_working_dir": decision.task_working_dir,
                "continue_from_project": decision.continue_from_project,
                "delivery_required": bool(decision.delivery_required),
                "expected_artifacts": decision.expected_artifacts or [],
                "artifact_freshness_policy": decision.artifact_freshness_policy,
                "reuse_existing_artifact": bool(decision.reuse_existing_artifact),
                "reuse_reason": decision.reuse_reason,
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
  "reply_to_user": "如果 should_direct_reply=true，在本字段直接给出用户可见回复；否则写空",
  "user_visible_boundary": "需要向用户说明的边界或缺失信息；没有写空",
  "reason": ""
}}

判断原则：
- 如果用户说“今天生成的/刚才那个/之前的/近期的表格/报告/文件”，优先检查最近可交付文件；如果有明显匹配，relation=existing_artifact，related_files 写相对路径或文件名，不要再要求用户重复说明已能从文件名看出的主题/城市。
- 如果用户是在补 SMTP 授权码、发件邮箱、地点、文件路径等缺失信息，relation=pending_followup。
- 如果只是普通闲聊或知识问答且无需工具/文件/项目，should_direct_reply=true。
- 如果 should_direct_reply=true，必须在 reply_to_user 里直接写可发送给用户的自然回复；不要留给后续 direct_reply event。
- 如果需要搜索、生成文件、发邮件、改文件、读附件、继续项目，should_enter_mind=true。
- 如果上下文不足但必须澄清，missing_slots 写具体缺什么，user_visible_boundary 写清楚该问什么。
- 不要输出自然语言解释，只输出 JSON。
"""
        try:
            raw = self._call_llm_with_deadline(
                lambda: adapter.chat(prompt, purpose="classify") or "",
                timeout_sec=_env_int("PARTNER_ENTRY_SELECTOR_TIMEOUT_SEC", 0),
                label="context_resolution",
            )
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
            "reply_to_user": self._sanitize_reply_to_user(str(data.get("reply_to_user") or ""))[:1200],
            "user_visible_boundary": str(data.get("user_visible_boundary") or "").strip()[:800],
            "reason": str(data.get("reason") or "").strip()[:500],
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
        for item in context[-3:]:
            role = "用户" if item.get("role") == "user" else "Partner"
            ctx_lines.append(f"{role}: {item.get('text', '')[:120]}")
        try:
            pool_stats = {}
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
        if (
            context_resolution
            and context_resolution.get("should_direct_reply")
            and not context_resolution.get("should_enter_mind")
        ):
            boundary = str(context_resolution.get("user_visible_boundary") or "").strip()
            missing = context_resolution.get("missing_slots") or []
            reply = self._sanitize_reply_to_user(str(context_resolution.get("reply_to_user") or ""))
            pending_action = "set" if missing else "clear"
            pending = {
                "original_user_request": text[:1200],
                "current_objective": str(context_resolution.get("resolved_objective") or text)[:1200],
                "missing_slots": [str(x) for x in missing][:8],
                "known_slots": context_resolution.get("known_slots") if isinstance(context_resolution.get("known_slots"), dict) else {},
                "last_question": boundary[:800] if boundary else "",
            } if missing else {}
            if not reply and not boundary:
                logger.debug("context resolution chose direct_reply but both reply and boundary are empty — falling through")
                return None
            return InteractionDecision(
                reply_to_user=reply or boundary,
                need_lifeline_update=False,
                lifeline_action="none",
                event_type="interaction_reply",
                event_kind="context_direct_reply",
                stop_after_completion=True,
                priority=1,
                pending_action=pending_action,
                pending_followup=pending,
            )
        capability_table = json.dumps(EVENT_CAPABILITIES, ensure_ascii=False)
        prompt = f"""你是 Partner 的 event selector。只选择下一步 route/event，不执行任务，不写详细执行方案。

可用 route：direct_reply, mind_event, pause_project, none。
可用 event：
- direct_task: 单步直接交付或具体操作
- batch_plan: 复杂任务的顶层批量规划；一次生成 Harness MicroPlan 并执行多个可并行步骤
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
- file_inspection: 对未知附件/二进制/音频先做魔数识别和前 64 字节 hex dump
- project_think: 拆解目标、选择路线、定义验收和第一个小 event
- objective_review: 对齐用户目标、上下文、已完成内容、缺口和下一 event
- curiosity_explore: 好奇探索与新假设
- habit_update: 写入习惯/经验/成长
- ollama_status: 探测已配置/自动发现的 Ollama 是否可用，并汇报当前会不会用于轻量问题
- project, content_digest, reflection, memory_consolidate: 兼容长期项目、内容消化、反思、记忆压缩

Event capability metadata：
{capability_table}

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
  "event_type": "direct_task|literature_review|data_fetch|data_analysis|visualization|evidence_audit|artifact_build|pdf_report|email_delivery|web_search|web_capture|file_inspection|project_think|objective_review|curiosity_explore|habit_update|ollama_status|project|content_digest|reflection|memory_consolidate|report",
  "event_kind": "自由短标签",
  "target_project": "",
  "objective": "给 agent 的具体目标；如果 direct_reply 可空",
  "reply_to_user": "给用户的自然回复",
  "delivery_required": false,
  "expected_artifacts": [
    {{"type": "file|message", "pattern": "*.csv", "description": "交付物说明", "required": true}}
  ],
  "artifact_freshness_policy": "new|reuse_allowed|continue_task",
  "reuse_existing_artifact": false,
  "reuse_reason": "",
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
- 纯信息查询（问天气、新闻、股价、名词解释）→ direct_reply
- 研究整理类任务（整理/综述/调研/梳理/归纳某领域的方法/进展/现状/技术）→ mind_event + literature_review
  - 「整理看看」「梳理一下」「调研一下」「归纳总结」「综述」等动词是研究整理信号
  - 这类任务需要工具配合（搜索文献、组织信息），不是简单直接回复能完成的
- mind_event：需要执行代码、操作文件、写文件、发邮件等需要真实工具的操作。
- route=none 只用于确实同一任务正在处理且无需吸收新信息。
- **输出形式判断**：如果用户只是询问信息、建议、查询实时数据（天气/新闻/股价），默认走 direct_reply，delivery_required=false, expected_artifacts=[]
- 研究整理类任务的 delivery_required 根据用户是否要求输出文件决定：
  - 用户说「整理看看」→ delivery_required=false，走 mind_event + literature_review
  - 用户说「整理成表格/报告/文件」→ delivery_required=true
- 只有用户明确要求"表格"、"文件"、"报告"、"保存"、"导出"时才 delivery_required=true
- mind_event 的 objective 只写一个最小可验证目标；多阶段目标选 project_think。
- 如果用户当前目标需要最终交付文件或结构化产物，delivery_required=true，并声明 expected_artifacts。
- 如果 delivery_required=true 且 expected_artifacts 含 file，event_type 必须具备 can_deliver_artifacts=true；不要选择 planning_only event。
- 每条新用户消息默认 artifact_freshness_policy=new：历史项目/最近文件只能作知识参考，不能作为本轮完成证据。
- 只有用户明确要求继续历史任务、重发已有文件、复用旧产物，才可设 artifact_freshness_policy=reuse_allowed 或 continue_task，并把 reuse_existing_artifact 设为 true 且写明 reuse_reason。
- 需要追问时 pending_action=set，并记录 pending_followup；补齐参数时合并上下文进入 mind_event。
- 输出必须是合法 JSON，不暴露 queue/workspace/backend。
"""
        try:
            raw = self._call_llm_with_deadline(
                lambda: adapter.chat(prompt, purpose="classify") or "",
                timeout_sec=_env_int("PARTNER_ENTRY_SELECTOR_TIMEOUT_SEC", 0),
                label="event_selector",
            )
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
        event_kind = self._sanitize_selector_event_kind(str(data.get("event_kind") or ""), event_type)
        if event_kind in {"自由短标签", "短标签", "event_kind"}:
            event_kind = ""
        target = str(data.get("target_project") or "").strip()
        objective = str(data.get("objective") or "").strip()
        reply = self._sanitize_reply_to_user(str(data.get("reply_to_user") or ""))
        # mind_event 不需要 selector 的回复——实际执行结果会通过 Harness 发送
        if route == "mind_event":
            reply = ""
        expected_artifacts = _normalize_expected_artifacts(data.get("expected_artifacts"))
        delivery_required = bool(data.get("delivery_required") or expected_artifacts)
        artifact_freshness_policy = _normalize_artifact_freshness_policy(data.get("artifact_freshness_policy"))
        reuse_existing_artifact = bool(data.get("reuse_existing_artifact")) and artifact_freshness_policy != "new"
        reuse_reason = str(data.get("reuse_reason") or "").strip()
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
            delivery_required=delivery_required,
            expected_artifacts=expected_artifacts,
            context_resolution=context_resolution,
        )
        if delivery_required and not reuse_existing_artifact:
            objective = self._enforce_new_artifact_objective(objective or text)
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

        if route == "direct_reply" and not delivery_required:
            if not reply:
                logger.debug("full selector chose direct_reply but reply_to_user is empty — falling through to objective_review")
                return self._objective_review_for_unavailable_selector(
                    text=text,
                    snapshot=snapshot,
                    reason="selector_direct_reply_empty",
                )
            return InteractionDecision(
                reply_to_user=reply,
                need_lifeline_update=False,
                lifeline_action="none",
                event_type="interaction_reply",
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
                event_type=event_type or "interaction_reply",
                event_kind=event_kind or "duplicate_or_noop",
                stop_after_completion=True,
                priority=priority,
                pending_action=pending_action,
                pending_followup=pending,
            )
        if route == "pause_project":
            pause_target = target or get_active(self.workspace) or ""
            if _is_generic_event_title(pause_target):
                pause_target = _title_from_objective(text, objective, pause_target)
            return InteractionDecision(
                reply_to_user=reply,
                need_lifeline_update=True,
                lifeline_action="pause_project",
                target_project=pause_target,
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
            if not target or _is_generic_event_title(target):
                target = _title_from_objective(objective, text, event_kind)
            delivery_mode, inferred_stop = self._delivery_mode_for_request(text, event_type=event_type)
            return InteractionDecision(
                reply_to_user=reply or THINKING_NOTICE,
                need_lifeline_update=True,
                lifeline_action="add_task",
                target_project=target,
                task_title=target,
                task_description=objective or text,
                event_type=event_type,
                event_kind=event_kind or "project_step",
                stop_after_completion=stop_after or inferred_stop,
                delivery_mode=delivery_mode,
                priority=priority,
                pending_action="clear",
                delivery_required=delivery_required,
                expected_artifacts=expected_artifacts,
                artifact_freshness_policy=artifact_freshness_policy,
                reuse_existing_artifact=reuse_existing_artifact,
                reuse_reason=reuse_reason,
            )
        if route == "mind_event" and event_type in {"content_digest", "reflection", "memory_consolidate"}:
            return InteractionDecision(
                reply_to_user=reply or THINKING_NOTICE,
                need_lifeline_update=True,
                lifeline_action="add_task" if event_type == "content_digest" else "add_note",
                target_project=target or get_active(self.workspace) or "",
                task_title=target or event_kind or event_type,
                task_description=objective or text,
                note=objective or text,
                event_type=event_type,
                event_kind=event_kind or event_type,
                stop_after_completion=False,
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
            event_kind = self._sanitize_selector_event_kind(event_kind, "direct_reply")
            return InteractionDecision(
                reply_to_user=reply,
                need_lifeline_update=False,
                lifeline_action="none",
                event_type="interaction_reply",
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
            event_type="interaction_reply",
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
            if _is_generic_event_title(target):
                target = _title_from_objective(raw_text, decision.task_description, target)
            previous = get_active(self.workspace) or ""
            if previous and previous != target:
                clear_active(self.workspace, previous)
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
                root_user_request=raw_text,
                event_type=decision.event_type,
                event_kind=decision.event_kind,
                stop_after_completion=decision.stop_after_completion,
                task_instance_id=decision.task_instance_id,
                task_working_dir=decision.task_working_dir,
                continue_from_project=decision.continue_from_project,
                delivery_required=decision.delivery_required,
                expected_artifacts=decision.expected_artifacts or [],
                artifact_freshness_policy=decision.artifact_freshness_policy,
                reuse_existing_artifact=decision.reuse_existing_artifact,
                reuse_reason=decision.reuse_reason,
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
            if _is_generic_event_title(target):
                target = _title_from_objective(raw_text, decision.task_description, target)
            previous = get_active(self.workspace) or ""
            if target and previous and target != previous and target not in previous and previous not in target:
                try:
                    from ..projects.project_state import set_project_status

                    set_project_status(self.workspace, previous, "waiting", f"用户切换到新项目：{target}")
                    release_project(self.workspace, previous, reason=f"用户切换到新项目：{target}")
                    self._log_mutation("release_previous_project", previous, f"new_project={target}")
                except Exception as exc:
                    logger.debug(f"failed to release previous project before switching: {exc}")
            if target:
                if previous and previous != target:
                    clear_active(self.workspace, previous)
                set_active(self.workspace, target)
                try:
                    from ..projects.project_state import set_project_status

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
                # Stale pending task (>30s old with no step files) — skip merge, create new
                created_at = getattr(existing, 'created_at', '')
                try:
                    from datetime import datetime
                    created_dt = datetime.fromisoformat(created_at) if created_at else None
                    if created_dt and (datetime.now() - created_dt).total_seconds() > 30:
                        logger.warning("[LIFELINE] stale pending task %s (%ds old), skipping merge", existing.id, (datetime.now() - created_dt).total_seconds())
                        existing = None
                except Exception:
                    pass
            if existing:
                self._touch_active_plan(target or existing.title, f"用户再次推动：{existing.title}")
                self._log_mutation("merge_task", existing.title, description)
                # 已有 pending task 但可能前次执行已结束（task 从未被标记 completed）
                # 仍然触发 _nudge_project 确保本次有实际执行
                # 不重复创建 Task，直接用已存在的
                self._nudge_project(
                    target or existing.title,
                    priority=2,
                    source="interaction:retry",
                    delivery_mode=decision.delivery_mode,
                    user_request=description,
                    root_user_request=raw_text,
                    event_type=decision.event_type,
                    event_kind=decision.event_kind,
                    stop_after_completion=decision.stop_after_completion,
                    task_instance_id=decision.task_instance_id,
                    task_working_dir=decision.task_working_dir,
                    continue_from_project=decision.continue_from_project,
                    delivery_required=decision.delivery_required,
                    expected_artifacts=decision.expected_artifacts or [],
                    artifact_freshness_policy=decision.artifact_freshness_policy,
                    reuse_existing_artifact=decision.reuse_existing_artifact,
                    reuse_reason=decision.reuse_reason,
                )
                return
            task_title = (decision.task_title or raw_text[:60]).strip()
            if _is_generic_event_title(task_title):
                task_title = _title_from_objective(raw_text, description, task_title)
            task = Task(
                type="deep_dive",
                title=task_title,
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
                root_user_request=raw_text,
                event_type=decision.event_type,
                event_kind=decision.event_kind,
                stop_after_completion=decision.stop_after_completion,
                task_instance_id=decision.task_instance_id,
                task_working_dir=decision.task_working_dir,
                continue_from_project=decision.continue_from_project,
                delivery_required=decision.delivery_required,
                expected_artifacts=decision.expected_artifacts or [],
                artifact_freshness_policy=decision.artifact_freshness_policy,
                reuse_existing_artifact=decision.reuse_existing_artifact,
                reuse_reason=decision.reuse_reason,
            )
            return

        if action == "pause_project":
            target = decision.target_project or get_active(self.workspace) or "当前项目"
            try:
                from ..projects.project_state import set_project_status

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
                root_user_request=raw_text,
                event_type=decision.event_type,
                event_kind=decision.event_kind,
                stop_after_completion=decision.stop_after_completion,
                task_instance_id=decision.task_instance_id,
                task_working_dir=decision.task_working_dir,
                continue_from_project=decision.continue_from_project,
                delivery_required=decision.delivery_required,
                expected_artifacts=decision.expected_artifacts or [],
                artifact_freshness_policy=decision.artifact_freshness_policy,
                reuse_existing_artifact=decision.reuse_existing_artifact,
                reuse_reason=decision.reuse_reason,
            )
            return

    def _update_contract_metadata(self, decision: InteractionDecision, target: str, raw_text: str):
        from ..projects.project_state import read_project_contract, write_project_contract, update_project_brief_from_contract
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
                       root_user_request: str = "",
                       event_type: str = "project", event_kind: str = "",
                       stop_after_completion: bool = False,
                       task_instance_id: str = "",
                       task_working_dir: str = "",
                       continue_from_project: str = "",
                       delivery_required: bool = False,
                       expected_artifacts: list[dict] | None = None,
                       artifact_freshness_policy: str = "new",
                       reuse_existing_artifact: bool = False,
                       reuse_reason: str = ""):
        """Wake the mind loop after a user-driven lifeline mutation.

        This is best-effort: if the process is not running, persisted
        active_plan/state will still be picked up by WAKE_UP on next start.
        """
        if not title:
            return
        if _is_generic_event_title(title):
            title = _title_from_objective(root_user_request, user_request, title)
        try:
            event_type_value = str(event_type or "project").strip().lower()
            # 直接通过 Harness executor 执行事件（MindPool 已移除）
            from ..mind.event_types import MindEvent, EventType
            from ..mind.executor import execute_event
            import asyncio

            ev = MindEvent(
                type=EventType(event_type_value) if event_type_value in {t.value for t in EventType} else EventType.PROJECT,
                priority=priority,
                payload={
                    "title": title,
                    "step": 0,
                    "delivery_mode": delivery_mode if delivery_mode in {"research_project", "reference_brief", "direct_deliverable", "audit_only"} else "research_project",
                    "user_request": (user_request or "")[:2000],
                    "root_user_request": (root_user_request or user_request or "")[:2000],
                    "event_type": event_type_value,
                    "event_kind": (event_kind or "")[:120],
                    "stop_after_completion": bool(stop_after_completion),
                },
                source=source,
            )
            try:
                # In a running event loop: _handle_user_message will queue
                # the BATCH_PLAN event separately. Only execute directly
                # when called from a sync context (CLI, core.chat).
                loop = asyncio.get_running_loop()
                logger.debug("[NUDGE] in running loop, skipping direct execute (queue will handle)")
            except RuntimeError:
                # No running loop — execute synchronously
                asyncio.run(execute_event(ev))
            except Exception as exc:
                logger.warning(f"[NUDGE] direct execute_event failed (non-fatal): {exc}")
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
        """保留兼容 — MindPool 已移除，无需清理。"""
        pass

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
            from ..projects.project_state import get_project_dir
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
                existing_title = str((existing or {}).get("title") or (existing or {}).get("project") or "").strip() if isinstance(existing, dict) else ""
                if isinstance(existing, dict) and existing_title == title:
                    plan.update(existing)
                    # Reset phases — old stale phases from a previous run must not
                    # carry over into the new planning cycle.
                    plan["phases"] = []
                    plan["current_phase_index"] = 0
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
