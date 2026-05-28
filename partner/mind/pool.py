"""Mind Pool — 全局念头池。

核心：asyncio.PriorityQueue 用于 async 环境。
扩展：thread_safe_queue (queue.PriorityQueue) 用于跨线程投递。
延迟：wake_after 支持 — 事件设置 wake_after 后不到时间不出队。
"""

import asyncio
import logging
import queue
import time as _time
from typing import Optional

from .event_types import MindEvent

logger = logging.getLogger(__name__)


class MindPool:
    """全局念头池单例。

    支持 wake_after 延迟唤醒：事件设置 wake_after=time.time()+900
    后，900 秒内不会被 get() 取出。到时间后自动回到主队列。
    """

    _instance: Optional['MindPool'] = None
    _lock = asyncio.Lock()

    def __init__(self, maxsize: int = 0):
        self._queue: asyncio.PriorityQueue[MindEvent] = asyncio.PriorityQueue(maxsize=maxsize)
        self._thread_queue: queue.PriorityQueue[MindEvent] = queue.PriorityQueue(maxsize=0)
        # 等待室：{event.id: (wake_after, event)} — 不到时间的暂存这里
        self._waiting_room: dict = {}
        self._total_put: int = 0
        self._total_get: int = 0

    @classmethod
    async def get_instance(cls) -> 'MindPool':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def get_sync_instance(cls) -> 'MindPool':
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    async def put(self, event: MindEvent):
        """放入一个念头。如设置了 wake_after 则进入等待室。"""
        if event.wake_after and event.wake_after > _time.time():
            self._waiting_room[event.id] = (event.wake_after, event)
            logger.info(f"[MIND] PUT event_type={event.type.value}, id={event.id[:8]}, "
                        f"pri={event.priority} [delayed until {event.wake_after:.0f}]")
        else:
            await self._queue.put(event)
            logger.info(f"[MIND] PUT event_type={event.type.value}, id={event.id[:8]}, "
                        f"pri={event.priority}")
        self._total_put += 1

    def put_threadsafe(self, event: MindEvent):
        self._thread_queue.put(event)
        self._total_put += 1
        logger.info(f"[MIND] PUT(event_type={event.type.value}, id={event.id[:8]}, "
                    f"pri={event.priority})  [threadsafe]")

    async def _drain_thread_queue(self):
        while True:
            try:
                ev = self._thread_queue.get_nowait()
                await self._queue.put(ev)
            except queue.Empty:
                break

    async def _release_waiting(self):
        """将等待室中到时间的事件放回主队列。"""
        now = _time.time()
        ready = [eid for eid, (wake_at, ev) in self._waiting_room.items() if wake_at <= now]
        for eid in ready:
            _, ev = self._waiting_room.pop(eid)
            await self._queue.put(ev)
            logger.info(f"[MIND] Released from waiting room: {ev.type.value}, "
                        f"id={ev.id[:8]}")
        return len(ready)

    async def get(self) -> Optional[MindEvent]:
        """取出优先级最高的、已到唤醒时间的念头。"""
        await self._drain_thread_queue()
        await self._release_waiting()

        if self._queue.qsize() == 0:
            raise asyncio.QueueEmpty

        event = await self._queue.get()
        # 如果事件设置了 wake_after 但还没到，放回等待室，递归取下一个
        if event.wake_after and event.wake_after > _time.time():
            self._waiting_room[event.id] = (event.wake_after, event)
            logger.debug(f"[MIND] Event {event.id[:8]} not yet due "
                         f"(wake_after={event.wake_after:.0f}), back to waiting room")
            return await self.get()

        self._total_get += 1
        logger.info(f"[MIND] START event_type={event.type.value}, id={event.id[:8]}, "
                    f"pri={event.priority}, topic={event.payload.get('topic', '')}")
        return event

    def qsize(self) -> int:
        return self._queue.qsize() + self._thread_queue.qsize() + len(self._waiting_room)

    def stats(self) -> dict:
        return {
            "pool_size": self.qsize(),
            "async_queue_size": self._queue.qsize(),
            "thread_queue_size": self._thread_queue.qsize(),
            "waiting_room_size": len(self._waiting_room),
            "total_put": self._total_put,
            "total_get": self._total_get,
        }
