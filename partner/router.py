"""Entry router — classifies tasks as direct_llm or batch_plan.

Only two routing decisions:
  - direct_llm: simple Q&A, greetings, trivial tasks — answered directly without planning
  - batch_plan: everything else — goes through full batch planning pipeline

Ollama is NOT a routing type. It is an execution-layer option used inside
both paths when appropriate.
"""

from __future__ import annotations
import logging
import os
import re
import yaml
from typing import Any

logger = logging.getLogger(__name__)

# ── Backward-compatible ConversationRouter stub ─────────────────────
# The new routing logic is in task_router.py.
# This stub keeps conversation.py working without refactoring.

class ConversationRouter:
    """Backward-compatible stub.

    The original ConversationRouter has been replaced by task_router.py.
    This class preserves the old interface for conversation.py.
    """
    def __init__(self, *args, **kwargs):
        logger.debug("ConversationRouter stub used")

    def parse_intent(self, message: str) -> "ParsedQuery":
        return ParsedQuery(text=message, intent="")

    def route(self, message: str, *args, **kwargs) -> str:
        from .task_router import route as task_route
        return task_route(message)


class ParsedQuery:
    """Minimal ParsedQuery stub for backward compatibility."""
    def __init__(self, text: str = "", intent: str = ""):
        self.text = text
        self.intent = intent

from .llm.ollama_probe import is_task_suitable_for_ollama, is_ollama_available

logger = logging.getLogger(__name__)

# ── Config loading ────────────────────────────────────────────────────────


def load_routing_config() -> dict:
    """Load routing rules from config, with fallback defaults."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "configs", "routing_rules.yaml"),
        os.path.expanduser("~/.partner/routing_rules.yaml"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as exc:
                logger.debug("[ROUTER] failed to load %s: %s", path, exc)
    return {
        "direct_llm": {"patterns": [], "max_input_length": 100, "allow_ollama_fallback": True},
        "batch_plan": {"default": True},
    }


# ── Routing ────────────────────────────────────────────────────────────────


def route(user_message: str) -> str:
    """Route a user message to direct_llm or batch_plan.

    Returns:
        "direct_llm" — simple task, reply directly without planning
        "batch_plan" — complex task, needs multi-step planning
    """
    config = load_routing_config()
    msg = (user_message or "").strip()
    if not msg:
        return "batch_plan"

    # Check direct_llm patterns first (simple/greeting tasks)
    direct_cfg = config.get("direct_llm", {})
    patterns = direct_cfg.get("patterns", [])
    if isinstance(patterns, list):
        for pattern in patterns:
            try:
                if re.search(pattern, msg, re.I):
                    max_len = int(direct_cfg.get("max_input_length", 200))
                    if len(msg) <= max_len:
                        logger.info("[ROUTER] direct_llm: matched pattern=%s", pattern)
                        return "direct_llm"
            except re.error:
                continue

    # Check batch_plan patterns (complex/multi-step tasks)
    batch_cfg = config.get("batch_plan", {})
    patterns = batch_cfg.get("patterns", [])
    if isinstance(patterns, list):
        for pattern in patterns:
            try:
                if re.search(pattern, msg, re.I):
                    logger.info("[ROUTER] batch_plan: matched pattern=%s", pattern)
                    return "batch_plan"
            except re.error:
                continue

    # Default
    if batch_cfg.get("default", True):
        return "batch_plan"
    return "direct_llm"


# ── Task classification ───────────────────────────────────────────────────


def classify(message: str) -> dict:
    """Full classification: routing + metadata for execution.

    Returns dict with:
      - routing: "direct_llm" | "batch_plan"
      - use_ollama: bool (whether Ollama should be used if available)
      - task_type: str (general category hint)
    """
    routing = route(message)
    use_ollama = False

    if routing == "direct_llm":
        config = load_routing_config()
        allow_fallback = config.get("direct_llm", {}).get("allow_ollama_fallback", True)
        if allow_fallback and is_ollama_available() and is_task_suitable_for_ollama(message):
            use_ollama = True

    # Simple task type hints
    msg_lower = message.lower()
    task_type = "general"
    if re.search(r"(RNA|rna|基因|转录|差异表达|富集|代谢)", msg_lower):
        task_type = "bioinfo"
    elif re.search(r"(天气|weather|气温|降水)", msg_lower):
        task_type = "weather"
    elif re.search(r"(股票|stock|股价|tsla)", msg_lower):
        task_type = "finance"
    elif re.search(r"(翻译|translate)", msg_lower):
        task_type = "translation"
    elif re.search(r"(你好|hello|hi|hey|谢谢|thanks)", msg_lower):
        task_type = "greeting"
    elif re.search(r"(解释|什么是|说明|定义)", msg_lower):
        task_type = "explanation"
    elif re.search(r"(推荐|餐厅|景点|地方)", msg_lower):
        task_type = "recommendation"
    elif re.search(r"(报告|report|分析|research|研究|整理)", msg_lower):
        task_type = "research"

    return {
        "routing": routing,
        "use_ollama": use_ollama,
        "task_type": task_type,
    }
