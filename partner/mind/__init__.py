"""Mind — Partner 的念头系统（Harness 驱动版）。

核心组件：
- event_types.py: 念头类型定义（@dataclass）
- executor.py: 念头执行分发器（由 Harness 直接调用）
"""

from .event_types import (
    MindEvent, EventType,
    report, cron_tick, wake_up, direct_reply,
)
from .executor import (
    init as init_executor,
    execute_event,
    set_file_push_callback,
    set_push_callback,
    start_event_loop,
    enqueue_user_message,
)

__all__ = [
    "MindEvent", "EventType",
    "report", "cron_tick", "wake_up", "direct_reply",
    "init_executor", "execute_event",
    "set_push_callback", "set_file_push_callback",
    "start_event_loop", "enqueue_user_message",
]
