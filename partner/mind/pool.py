"""Mind Pool — 全局念头池。

核心：asyncio.PriorityQueue 用于 async 环境。
扩展：thread_safe_queue (queue.PriorityQueue) 用于跨线程投递。

所有模块都能通过 get_instance() 获取单例，
用 put(event) 放入新念头，用 async get() 取出最高优先级的念头。

跨线程投递：外部线程调用 put_threadsafe()，mind_loop 会定期 poll。
"""

import asyncio
import logging
import queue
import threading
from typing import Optional

from .event_types import MindEvent

logger = logging.getLogger(__name__)


class MindPool:
    """全局念头池单例。

    使用 asyncio.PriorityQueue 作为主队列，
    queue.PriorityQueue 作为跨线程桥接。
    """

    _instance: Optional['MindPool'] = None
    _lock = asyncio.Lock()

    def __init__(self, maxsize: int = 0):
        self._queue: asyncio.PriorityQueue[MindEvent] = asyncio.PriorityQueue(maxsize=maxsize)
        # 跨线程桥接队列
        self._thread_queue: queue.PriorityQueue[MindEvent] = queue.PriorityQueue(maxsize=0)
        self._total_put: int = 0
        self._total_get: int = 0

    @classmethod
    async def get_instance(cls) -> 'MindPool':
        """获取单例（async 环境用）。"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def get_sync_instance(cls) -> 'MindPool':
        """获取单例（同步/跨线程环境用）。
        注意：必须在 get_instance() 之后调用，否则返回 None。
        """
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例。"""
        cls._instance = None

    async def put(self, event: MindEvent):
        """放入一个念头（async 环境）。"""
        await self._queue.put(event)
        self._total_put += 1
        logger.info(f"[MIND] PUT event_type={event.type.value}, id={event.id[:8]}, "
                    f"pri={event.priority}")

    def put_threadsafe(self, event: MindEvent):
        """从非 async 线程安全地放入一个念头。"""
        self._thread_queue.put(event)
        self._total_put += 1
        logger.info(f"[MIND] PUT(event_type={event.type.value}, id={event.id[:8]}, "
                    f"pri={event.priority})  [threadsafe]")

    async def _drain_thread_queue(self):
        """将跨线程队列中的念头全部搬到 async 队列中。"""
        while True:
            try:
                ev = self._thread_queue.get_nowait()
                await self._queue.put(ev)
            except queue.Empty:
                break

    async def get(self) -> MindEvent:
        """取出优先级最高的念头（阻塞直到非空）。
        取出前先 drain 跨线程队列。
        """
        await self._drain_thread_queue()
        event = await self._queue.get()
        self._total_get += 1
        logger.info(f"[MIND] START event_type={event.type.value}, id={event.id[:8]}, "
                    f"pri={event.priority}, topic={event.payload.get('topic', '')}")
        return event

    def qsize(self) -> int:
        """当前队列总大小（async + 跨线程）。"""
        return self._queue.qsize() + self._thread_queue.qsize()

    def stats(self) -> dict:
        return {
            "pool_size": self.qsize(),
            "async_queue_size": self._queue.qsize(),
            "thread_queue_size": self._thread_queue.qsize(),
            "total_put": self._total_put,
            "total_get": self._total_get,
        }
