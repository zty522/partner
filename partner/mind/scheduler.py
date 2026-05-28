"""Mind Scheduler — 念头调度器。

核心循环 `mind_loop()`：
1. 不断从念头池中取出优先级最高的念头
2. 创建 asyncio.Task 去执行（不阻塞循环）
3. 当池为空时短暂休眠（0.1 秒）
4. 捕获异常，保证循环不崩溃

启动方式：asyncio.run(mind_loop())
"""

import asyncio
import logging
from typing import Optional

from .pool import MindPool
from .executor import execute_event
from .event_types import MindEvent

logger = logging.getLogger(__name__)

# 最大并发执行数，防止念头溢出
MAX_CONCURRENT = 10


async def mind_loop(pool: MindPool = None):
    """念头调度器主循环。

    永久运行，不断从池中取念头、创建 Task 执行。
    当池为空时短暂休眠避免 CPU 空转。

    Args:
        pool: MindPool 实例。为 None 时自动获取单例。
    """
    if pool is None:
        pool = await MindPool.get_instance()

    pending_tasks: set[asyncio.Task] = set()
    logger.info("🧠 Mind Loop 启动")

    try:
        while True:
            # 清理已完成的 task
            pending_tasks = {t for t in pending_tasks if not t.done()}

            # 如果池为空，短暂休眠
            if pool.qsize() == 0:
                await asyncio.sleep(0.1)
                continue

            # 检查并发上限
            if len(pending_tasks) >= MAX_CONCURRENT:
                await asyncio.sleep(0.5)
                continue

            # 取出一个念头
            try:
                event = await asyncio.wait_for(pool.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # 创建异步 Task 执行
            task = asyncio.create_task(
                _run_event_safely(event),
                name=f"mind_{event.id[:8]}",
            )
            pending_tasks.add(task)
            task.add_done_callback(pending_tasks.discard)

    except asyncio.CancelledError:
        logger.info("🧠 Mind Loop 被取消")
        # 取消所有正在执行的 task
        for t in pending_tasks:
            t.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        raise


async def _run_event_safely(event: MindEvent):
    """安全执行一个念头（不抛出异常）。"""
    try:
        await execute_event(event)
    except asyncio.CancelledError:
        logger.info(f"[调度] 念头 {event.id[:8]} 执行被取消")
    except Exception as e:
        logger.error(f"[调度] 念头 {event.id[:8]} 抛出未捕获异常: {e}",
                     exc_info=True)
