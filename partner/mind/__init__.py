"""Mind — Partner 的自主念头系统。

抛弃旧 Event 模板 → ActivePlan → cron 执行模式。
新模型：Event = 念头（Mind Event），自发、实时、自主执行。

核心组件：
- event_types.py: 念头类型定义（@dataclass）
- pool.py: 全局优先级队列（asyncio.PriorityQueue）
- scheduler.py: 异步调度循环 (mind_loop)
- executor.py: 念头执行分发器
"""

from .event_types import (
    MindEvent, EventType,
    report, cron_tick, wake_up,
)
from .pool import MindPool
from .scheduler import mind_loop
from .executor import init as init_executor, execute_event, set_push_callback

__all__ = [
    "MindEvent", "EventType",
    "report", "cron_tick", "wake_up",
    "MindPool", "mind_loop", "init_executor", "execute_event",
    "set_push_callback",
]
