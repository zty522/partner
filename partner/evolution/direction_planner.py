from __future__ import annotations
import logging, os, shutil
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Direction:
    name: str
    current_state: str  # 'rough', 'partial', 'complete'
    priority: int  # 1=highest
    impact: str  # 'high', 'medium', 'low'
    actions: list[str] = field(default_factory=list)
    status_evidence: dict = field(default_factory=dict)


class DirectionPlanner:
    def __init__(self):
        self.directions: list[Direction] = []

    def scan_all(self) -> list[Direction]:
        """Scan all directions and evaluate their current state."""
        self.directions = []

        # World Model - check AETHER availability
        wm = self._check_world_model()
        self.directions.append(wm)

        # BioNeMo - check NGC_API_KEY
        bn = self._check_bionemo()
        self.directions.append(bn)

        # Self-evolution meta
        se = self._check_self_evolution()
        self.directions.append(se)

        # Sort by priority
        self.directions.sort(key=lambda d: d.priority)
        return self.directions

    def _check_world_model(self) -> Direction:
        aether = shutil.which("aether")
        has_config = (
            os.path.exists(os.path.expanduser("~/.aether/config.yaml"))
            if aether
            else False
        )
        priority = 1 if not aether else 3
        return Direction(
            name="世界模型 (World Model)",
            current_state="rough" if not aether else "partial",
            priority=priority,
            impact="high",
            status_evidence={"aether_binary": bool(aether), "has_config": has_config},
        )

    def _check_bionemo(self) -> Direction:
        nkey = os.environ.get("NGC_API_KEY", "")
        has_adapter = False
        try:
            from ...bioscience.bionemo_adapter import BioNeMoAdapter

            has_adapter = True
        except Exception:
            pass
        priority = 1 if not nkey else 2 if not has_adapter else 4
        return Direction(
            name="BioNeMo 科学能力",
            current_state="rough" if not nkey else "partial",
            priority=priority,
            impact="high",
            status_evidence={
                "has_api_key": bool(nkey),
                "has_adapter": has_adapter,
            },
        )

    def _check_self_evolution(self) -> Direction:
        try:
            from ..evolution.evolution_db import get_active_rules, get_state

            rules = get_active_rules(min_confidence=0.0, limit=1)
            has_rules = len(rules) > 0
            cycle_cnt = int(get_state("cycle_count", "0"))
        except Exception:
            has_rules = False
            cycle_cnt = 0
        priority = 3 if has_rules else 1
        return Direction(
            name="自进化引擎自身",
            current_state="partial" if has_rules else "rough",
            priority=priority,
            impact="high",
            status_evidence={
                "has_rules": has_rules,
                "cycle_count": cycle_cnt,
            },
        )

    def generate_plan(
        self, directions: list[Direction] | None = None
    ) -> list[dict]:
        """Generate execution plan from directions."""
        dirs = directions or self.directions
        plan = []
        for d in dirs:
            plan.append(
                {
                    "direction": d.name,
                    "priority": d.priority,
                    "current_state": d.current_state,
                    "estimated_minutes": 10 if d.current_state == "rough" else 5,
                    "actions": d.actions or self._suggest_actions(d),
                }
            )
        return plan

    def _suggest_actions(self, d: Direction) -> list[str]:
        base = ["自我审视该方向能力", "检查环境依赖"]
        if d.current_state == "rough":
            base.extend(["搜索可用解决方案", "评估候选方案", "生成接入计划"])
        elif d.current_state == "partial":
            base.extend(["接入缺失组件", "运行可用性测试", "Benchmark 验证"])
        base.append("记录结果到 growth")
        return base
