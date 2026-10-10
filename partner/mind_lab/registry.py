"""Mind Lab candidate registry (L4: 认知与学习组件清单).

Every L4 capability — implemented, reserved, or a design blueprint — is
registered here so partner can see what it *has* and what it *could* grow
into.  Reserved Event blueprints under ``partner/events/<name>/EVENT.md`` are
listed as candidates: they are instantiated by the note ledger / self-evolution
when evidence calls for them, never by default.

The registry is generated (not hand-maintained): reserved candidates are
discovered from the EVENT.md files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_EVENT_DIRS = ("cross-pollination", "deep-analysis", "exploration",
               "idea-exploration", "literature-deep-dive", "method-learning",
               "project-health-check", "synthesis-review")

# 已具备（implemented）组件归属，物理路径真实存在。
IMPLEMENTED: list[dict[str, Any]] = [
    {"name": "notes", "series": "notes", "status": "implemented",
     "desc": "统一笔记库：recall/judge/promote/evolution_sync + LLM 注入钩子",
     "physical": "partner/memory/notes.py, partner/events/notes.py",
     "wired": ["content_read_reply 1.1.0", "project_iteration 3.1.0",
               "active_learning 2.2.0", "learning_improvement_cycle 1.8.0",
               "self_evolution 2.0.0", "autonomous_evolution 3.4.0"]},
    {"name": "active_learning", "series": "active_learning", "status": "implemented",
     "desc": "外部资料读取 → borrowable_cards → synthesize → judge/落地",
     "physical": "partner/events/active_learning.py, local_learning.py",
     "wired": ["active_learning 2.2.0", "learning_improvement_cycle 1.8.0"]},
    {"name": "world_model", "series": "core", "status": "implemented",
     "desc": "世界模型通道（配置/客户端/CLI 已具备，flow 接线可选）",
     "physical": "partner/world_model/", "wired": ["core.jev_evaluate（JEV）"]},
    {"name": "memory_legacy", "series": "memory", "status": "implemented",
     "desc": "旧语义记忆（observations/lessons/preferences/habits/beliefs/growth），存储已并入统一笔记库",
     "physical": "partner/memory/event_memory.py", "wired": ["memory.* 事件（接口兼容）"]},
    {"name": "local_learning", "series": "improvement", "status": "implemented",
     "desc": "本地代码学习 → local_compare → local_ideas → judge",
     "physical": "partner/events/local_learning.py, improvement.py",
     "wired": ["learning_improvement_cycle 1.8.0"]},
]

# 设计蓝图（reserved candidate）：由笔记/自进化驱动实例化，默认不启用。
RESERVED: list[dict[str, Any]] = []
_EVENT_BASE = Path(__file__).resolve().parents[1] / "events"


def _load_reserved() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in _EVENT_DIRS:
        md = _EVENT_BASE / name / "EVENT.md"
        if not md.exists():
            continue
        front = _front_matter(md)
        rows.append({
            "name": name,
            "series": "candidate",
            "status": "blueprint",
            "desc": front.get("description") or "",
            "tags": front.get("tags") or [],
            "physical": f"partner/events/{name}/EVENT.md",
            "trigger_keywords": front.get("triggers", {}).get("keywords") or [],
            "estimated_minutes": front.get("estimated_minutes"),
            "instantiate_when": "笔记/自进化证据充分时由 LLM 组装实例化",
        })
    return rows


def _front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    try:
        return yaml.safe_load(text[4:end]) or {}
    except Exception:
        return {}


def candidates() -> list[dict[str, Any]]:
    return list(IMPLEMENTED) + list(_load_reserved())


def by_name(name: str) -> dict[str, Any] | None:
    for row in candidates():
        if row.get("name") == name:
            return row
    return None


def summary() -> str:
    rows = candidates()
    implemented = sum(1 for r in rows if r.get("status") == "implemented")
    blueprints = sum(1 for r in rows if r.get("status") == "blueprint")
    return (f"Mind Lab registry: {len(rows)} components "
            f"({implemented} implemented, {blueprints} blueprints)")
