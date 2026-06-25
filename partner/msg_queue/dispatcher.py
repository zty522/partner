"""
Dispatcher — intelligent task routing based on content analysis and instance capabilities.
"""
from __future__ import annotations
import json, logging, os, re, yaml
from typing import Any

logger = logging.getLogger(__name__)

def load_routing_config() -> dict:
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "configs", "routing.yaml"),
        os.path.expanduser("~/.partner/routing.yaml"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path) as f:
                return yaml.safe_load(f) or {}
    return {}

def classify_message(text: str) -> dict:
    """Classify a user message to determine routing requirements."""
    text_lower = text.lower()
    # Detect task type
    task_type = "general"
    if re.search(r"(天气|weather|气温|湿度|降水)", text_lower):
        task_type = "weather"
    elif re.search(r"(股票|stock|tesla|tsla|股价|投资)", text_lower):
        task_type = "finance"
    elif re.search(r"(欧拉|euler|公式|数学|plot|graph)", text_lower):
        task_type = "math"
    elif re.search(r"(转录|RNA|基因|transcriptome|年龄预测|文献|综述|论文)", text_lower):
        task_type = "research"
    elif re.search(r"(代码|code|python|实现|写一个)", text_lower):
        task_type = "code"

    # Detect required capabilities
    capabilities = set()
    capabilities.add("call_agent_skill")
    if re.search(r"(表格|csv|excel)", text_lower):
        capabilities.add("csv_output")
    if re.search(r"(图|plot|chart|chart|可视化)", text_lower):
        capabilities.add("visualization")
    if re.search(r"(pdf|报告|report)", text_lower):
        capabilities.add("pdf_output")

    return {
        "task_type": task_type,
        "capabilities": sorted(capabilities),
        "complexity": "complex" if len(text) > 50 else "simple",
    }

def find_best_instance(task_type: str, required_capabilities: list,
                       instances: list[dict], preferred: str = "") -> str | None:
    """Find the best instance for a task based on capabilities and load."""
    # If preferred instance is available and capable, use it
    if preferred:
        for inst in instances:
            if inst["instance_id"] == preferred:
                return preferred

    # Score instances
    scored = []
    for inst in instances:
        caps = json.loads(inst.get("capabilities", "[]"))
        score = 0
        # Capability match
        for req in required_capabilities:
            if req in caps:
                score += 10
        # Load penalty (lighter load = better)
        load = int(inst.get("load", 0))
        score -= load * 2
        scored.append((score, inst["instance_id"]))

    scored.sort(key=lambda x: -x[0])
    return scored[0][1] if scored else None
