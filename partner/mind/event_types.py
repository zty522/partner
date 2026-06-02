"""Mind Event Types — Partner 自主系统的念头类型定义。

每个念头是系统内部产生的"我想做这件事"的冲动。
不同于传统任务队列，念头可以自发产生、自我修改、自我销毁。

念头优先级（数值越低越紧急）：
  1  = 立即（用户指令）
  3  = 高（汇报）
  5  = 中（项目）
  7  = 低
  10 = 最低（心跳）

保留的事件类型：PROJECT, REPORT, CRON_TICK, WAKE_UP, REFLECTION,
CROSS_PROJECT, MEMORY_CONSOLIDATE, CONTENT_DIGEST, CONTENT_PATROL
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4
from typing import Optional, Dict, Any
from enum import Enum


class EventType(str, Enum):
    """所有念头类型（精简版）。"""
    PROJECT = "project"                # 长期项目：多步推进的研究意图
    REPORT = "report"                  # 汇报：向用户推送进展或结果
    CRON_TICK = "cron_tick"            # 由外部 cron 注入的周期性触发
    WAKE_UP = "wake_up"                # 启动唤醒：恢复状态后自动探索
    REFLECTION = "reflection"          # 独立长反思：跨轮次整理失败/边界/策略
    CROSS_PROJECT = "cross_project"    # 默认网络：跨项目迁移和旧失败重解释
    MEMORY_CONSOLIDATE = "memory_consolidate"  # 记忆压缩：保持 prompt 轻量
    CONTENT_DIGEST = "content_digest"  # 外部内容消化：用户分享/自巡游素材 → 假设/灵感
    CONTENT_PATROL = "content_patrol"  # 受控巡游：公开内容入口 → content_feed


@dataclass
class MindEvent:
    """一个念头 = 系统内部一次自主冲动的完整描述。"""
    id: str = field(default_factory=lambda: f"ev_{uuid4().hex[:8]}")
    type: EventType = EventType.PROJECT
    priority: int = 5                  # 1=最急, 10=最缓
    payload: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source: str = ""                   # 产生来源："cron_tick" | "executor" | "user" | "self_check"
    parent_id: Optional[str] = None    # 由哪个念头产生的？
    wake_after: Optional[float] = None # 时间戳（time.time()），在此之前不出队

    def __lt__(self, other: 'MindEvent') -> bool:
        """PriorityQueue 使用 < 比较确定顺序。数值越低优先级越高。"""
        if not isinstance(other, MindEvent):
            return NotImplemented
        return self.priority < other.priority

    def __le__(self, other: 'MindEvent') -> bool:
        if not isinstance(other, MindEvent):
            return NotImplemented
        return self.priority <= other.priority

    def __gt__(self, other: 'MindEvent') -> bool:
        if not isinstance(other, MindEvent):
            return NotImplemented
        return self.priority > other.priority

    def __ge__(self, other: 'MindEvent') -> bool:
        if not isinstance(other, MindEvent):
            return NotImplemented
        return self.priority >= other.priority


# ── Convenience factory functions ──────────────────────────────────


def report(content: str, priority: int = 3, source: str = "",
           parent_id: str = None) -> MindEvent:
    """创建一个 Report 念头，payload 含 content。"""
    return MindEvent(
        type=EventType.REPORT,
        priority=priority,
        payload={"content": content},
        source=source,
        parent_id=parent_id,
    )


def cron_tick(source: str = "cron") -> MindEvent:
    """创建一个周期心跳念头。"""
    return MindEvent(
        type=EventType.CRON_TICK,
        priority=10,
        payload={},
        source=source,
    )


def wake_up(source: str = "startup") -> MindEvent:
    """创建一个唤醒念头（启动后立即调度）。"""
    return MindEvent(
        type=EventType.WAKE_UP,
        priority=1,  # 最高优先级
        payload={},
        source=source,
    )


def reflection(source: str = "self_pulse") -> MindEvent:
    return MindEvent(
        type=EventType.REFLECTION,
        priority=7,
        payload={},
        source=source,
    )


def cross_project(source: str = "self_pulse") -> MindEvent:
    return MindEvent(
        type=EventType.CROSS_PROJECT,
        priority=8,
        payload={},
        source=source,
    )


def memory_consolidate(source: str = "self_pulse") -> MindEvent:
    return MindEvent(
        type=EventType.MEMORY_CONSOLIDATE,
        priority=9,
        payload={},
        source=source,
    )
