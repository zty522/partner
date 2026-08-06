"""Self-Description — Partner 架构自我描述模块。

输出 Partner 当前架构的结构化 JSON，供架构映射器使用。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── 核心描述维度 ────────────────────────────────────────────────────

def get_architecture() -> dict[str, Any]:
    """返回 Partner 当前架构的结构化描述。

    自动从运行时状态和配置中读取实际值，不是硬编码。
    """
    import os as _os
    import sys as _sys

    # 读取 Harness 配置
    _max_steps = _get_harness_config("max_plan_steps", 10)
    _max_iter = _get_harness_config("max_iterations", 3)
    _timeout = _get_harness_config("step_timeout", 300)

    arch = {
        "orchestration": "event_driven",
        "parallelism": "llm_controlled",
        "communication": "implicit_reference",
        "planning": "batch",
        "execution": "deterministic_with_redesign",
        "learning": "rule_extraction",
    }

    # 检测是否有 architecture_insight 规则已被应用
    _insight_count = _count_architecture_insights()
    if _insight_count > 0:
        arch["architecture_insights_applied"] = _insight_count

    details = {
        "event_types": _count_v2_events(),
        "step_limit": _max_steps,
        "max_iterations": _max_iter,
        "step_timeout_s": _timeout,
        "dependency_resolution": "depends_on_array",
        "event_registry_count": _count_event_registry(),
        "v2_modules": _list_v2_modules(),
        "ollama_models": _list_ollama_models(),
        "agent_count": _count_agents(),
        "platform": _sys.platform,
        "wsl_host": _os.path.isdir("/mnt/c"),
    }

    return {"architecture": arch, "details": details}


def describe_for_prompt() -> str:
    """返回 Human-readable 的架构描述，可用于注入 Planner Prompt。"""
    arch = get_architecture()
    a = arch["architecture"]
    d = arch["details"]

    lines = [
        "## Partner 当前架构",
        f"- 编排模式: {a['orchestration']} — 事件驱动，通过 depends_on 定义依赖",
        f"- 并行策略: {a['parallelism']} — Planner 决定哪些步骤可并行（depends_on=[]）",
        f"- 模块间通信: {a['communication']} — step 通过 $step_X.field 引用前序输出",
        f"- 规划方式: {a['planning']} — 任务开始时生成 {d['step_limit']} 步计划",
        f"- 执行模式: {a['execution']} — 失败时触发 redesign（最多 {d['max_iterations']} 轮）",
        f"- 学习机制: {a['learning']} — 从执行历史提取 keyword→成功率 映射规则",
        f"- Event 类型数: {d['event_types']}（含 v2 扩展）",
        f"- 事件注册总数: {d['event_registry_count']}",
        f"- Agent 数: {d['agent_count']}",
    ]

    if "architecture_insights_applied" in a:
        lines.append(f"- ✅ 已应用架构改进规则: {a['architecture_insights_applied']} 条")

    if d.get("ollama_models"):
        lines.append(f"- 本地可用模型: {', '.join(d['ollama_models'][:5])}")
    if d["wsl_host"]:
        lines.append("- Windows 主机可用: ✅")

    return "\n".join(lines)


# ── 辅助函数 ────────────────────────────────────────────────────────

def _get_harness_config(key: str, default: Any = None) -> Any:
    """从 harness 配置文件中读取配置项。"""
    try:
        import os as _os
        import yaml as _yaml

        _paths = [
            _os.path.join(_os.path.dirname(__file__), "..", "config", "harness_goals.yaml"),
            _os.path.join(_os.path.dirname(__file__), "..", "..", "config", "harness_goals.yaml"),
        ]
        for _p in _paths:
            _exp = _os.path.expanduser(_p)
            if _os.path.exists(_exp):
                with open(_exp) as _f:
                    _cfg = _yaml.safe_load(_f)
                return _cfg.get(key, default)
    except Exception:
        pass
    return default


def _count_v2_events() -> int:
    """统计 v2 模块注册的 Event 数量。"""
    try:
        from partner.v2 import get_all_events
        return len(get_all_events())
    except Exception:
        return 0


def _count_event_registry() -> int:
    """统计 Harness EventRegistry 中注册的事件总数。"""
    try:
        from partner.mind.harness import default_registry
        return len(default_registry()._events)
    except Exception:
        return 0


def _list_v2_modules() -> list[str]:
    """列出 v2 模块。"""
    import os as _os
    _v2_dir = _os.path.join(_os.path.dirname(__file__), "..", "v2")
    if _os.path.isdir(_v2_dir):
        return sorted([
            f.replace(".py", "") for f in _os.listdir(_v2_dir)
            if f.endswith(".py") and not f.startswith("__")
        ])
    return []


def _list_ollama_models() -> list[str]:
    """列出 Ollama 本地可用的模型。"""
    try:
        import json, urllib.request
        _resp = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        _models = json.loads(_resp.read().decode()).get("models", [])
        return [m["name"] for m in _models]
    except Exception:
        return []


def _count_agents() -> int:
    """统计 AgentRegistry 中的 Agent 数量。"""
    try:
        from partner.agents.registry import AgentRegistry
        return len(AgentRegistry().list_agents())
    except Exception:
        return 0


def _count_architecture_insights() -> int:
    """统计 evolution_rules 表中有多少 category='architecture_insight' 的规则。"""
    try:
        from .evolution_db import GLOBAL_DB_PATH
        import sqlite3
        db = sqlite3.connect(GLOBAL_DB_PATH)
        cur = db.execute(
            "SELECT COUNT(*) FROM evolution_rules WHERE category='architecture_insight'"
        )
        result = cur.fetchone()[0]
        db.close()
        return result
    except Exception:
        return 0
