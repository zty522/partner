"""Mind Event Types — Partner 自主系统的念头类型定义。

每个念头是系统内部产生的"我想做这件事"的冲动。
不同于传统任务队列，念头可以自发产生、自我修改、自我销毁。

念头优先级（数值越低越紧急）：
  1  = 立即（用户指令、纠错）
  3  = 高（汇报）
  5  = 中（灵感、好奇）
  7  = 低（自省）
  10 = 最低（心跳、日记）
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4
from typing import Optional, Dict, Any
from enum import Enum


class EventType(str, Enum):
    """所有念头类型。"""
    INSPIRATION = "inspiration"        # 灵感：知识间隙、新组合
    CURIOSITY = "curiosity"            # 好奇：遇到未知概念想学习
    CORRECTION = "correction"          # 纠错：怀疑之前生成有误
    REPORT = "report"                  # 汇报：向用户推送进展或结果
    SELF_REFLECTION = "self_reflection" # 自省：定期自我评估
    EVOLUTION = "evolution"            # 进化：修改自身 prompt/策略
    PROJECT = "project"                # 长期项目：多步推进的研究意图
    DIARY_WRITE = "diary_write"        # 写日记
    CRON_TICK = "cron_tick"            # 由外部 cron 注入的周期性触发
    USER_MESSAGE = "user_message"      # 用户通过 QQ 发来的消息
    WAKE_UP = "wake_up"                  # 启动唤醒：恢复状态后自动探索


@dataclass
class MindEvent:
    """一个念头 = 系统内部一次自主冲动的完整描述。"""
    id: str = field(default_factory=lambda: f"ev_{uuid4().hex[:8]}")
    type: EventType = EventType.CURIOSITY
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

def curiosity(topic: str, priority: int = 5, source: str = "",
              parent_id: str = None) -> MindEvent:
    """创建一个 Curiousity 念头，payload 含 topic。"""
    return MindEvent(
        type=EventType.CURIOSITY,
        priority=priority,
        payload={"topic": topic},
        source=source,
        parent_id=parent_id,
    )


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


def user_message(text: str, sender_id: str = "", sender_name: str = "",
                 priority: int = 1) -> MindEvent:
    """创建一个用户消息念头。"""
    return MindEvent(
        type=EventType.USER_MESSAGE,
        priority=priority,
        payload={"text": text, "sender_id": sender_id, "sender_name": sender_name},
        source="user",
    )


def diary_write(priority: int = 8, source: str = "scheduler") -> MindEvent:
    """创建一个写日记念头。"""
    return MindEvent(
        type=EventType.DIARY_WRITE,
        priority=priority,
        payload={},
        source=source,
    )


def self_reflection(priority: int = 7, source: str = "scheduler") -> MindEvent:
    """创建一个自省念头。"""
    return MindEvent(
        type=EventType.SELF_REFLECTION,
        priority=priority,
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
