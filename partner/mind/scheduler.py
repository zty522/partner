"""Mind Scheduler — 念头调度器。

核心循环 `mind_loop()`：
1. 内置 15 分钟自脉冲（无需外部 cron）
2. 不断从念头池中取出优先级最高的念头
3. 创建 asyncio.Task 去执行（不阻塞循环）
4. 当池为空时短暂休眠（0.1 秒）
5. 捕获异常，保证循环不崩溃

启动方式：asyncio.run(mind_loop())
"""

import asyncio
import logging
from typing import Optional

from .pool import MindPool
from .executor import execute_event
from .event_types import MindEvent, EventType

logger = logging.getLogger(__name__)

# 最大并发执行数，防止念头溢出
MAX_CONCURRENT = 10
# 自脉冲间隔（秒）
SELF_PULSE_INTERVAL = 300  # 5 分钟


async def mind_loop(pool: MindPool = None, save_path: str = "", workspace: str = ""):
    """念头调度器主循环（带持久化 + WAKE_UP 唤醒）。

    永久运行，不断从池中取念头、创建 Task 执行。
    当池为空时短暂休眠避免 CPU 空转。
    内置自脉冲定时器，无需外部 cron 注入 CRON_TICK。
    启动时恢复持久化事件并注入 WAKE_UP 唤醒脉冲。

    Args:
        pool: MindPool 实例。为 None 时自动获取单例。
        save_path: 持久化文件路径。为空时不保存。
        workspace: 实例工作空间路径。
    """
    if pool is None:
        pool = await MindPool.get_instance()
    
    # 设置持久化路径
    if save_path:
        pool._save_path = save_path
        pool._auto_save = True

    pending_tasks: set[asyncio.Task] = set()
    logger.info("🧠 Mind Loop 启动")

    # 从持久化文件恢复事件
    try:
        restored = await pool.load()
        if restored > 0:
            logger.info(f"[调度] 从持久化恢复了 {restored} 个事件")
    except Exception:
        pass

    # 注入 WAKE_UP 唤醒脉冲（最高优先级）
    try:
        from .event_types import wake_up
        await pool.put(wake_up(source="startup"))
        logger.info("[调度] WAKE_UP 唤醒脉冲已注入")
    except Exception as e:
        logger.warning(f"[调度] WAKE_UP 注入失败: {e}")

    # 内置自脉冲定时器（15 分钟间隔自动注入 CRON_TICK）
    last_pulse = 0.0
    # 空闲检测
    last_nonempty_time = asyncio.get_event_loop().time()
    has_logged_idle = False

    try:
        while True:
            # 清理已完成的 task
            pending_tasks = {t for t in pending_tasks if not t.done()}

            # ── 自脉冲：每 15 分钟自动注入 CRON_TICK ──
            now = asyncio.get_event_loop().time()
            if now - last_pulse >= SELF_PULSE_INTERVAL:
                last_pulse = now
                await pool.put(MindEvent(
                    type=EventType.CRON_TICK,
                    priority=10,
                    payload={},
                    source="self_pulse",
                ))
                logger.info("[调度] 自脉冲 CRON_TICK 已注入")

            # 如果池为空，短暂休眠 + 空闲检测
            if pool.qsize() == 0:
                # ── 30 分钟空闲检测 ─────────────────────────────
                idle_duration = now - last_nonempty_time
                if idle_duration >= 1800 and not has_logged_idle:
                    logger.info("空闲状态 - 等待任务 (Mind Pool 连续 30 分钟为空)")
                    has_logged_idle = True
                elif idle_duration < 1800:
                    has_logged_idle = False

                await asyncio.sleep(0.1)
                continue
            else:
                # 有事件进来，重置空闲检测
                last_nonempty_time = now
                has_logged_idle = False

            # 检查并发上限
            if len(pending_tasks) >= MAX_CONCURRENT:
                await asyncio.sleep(0.5)
                continue

            # 取出一个念头（轮询方式，避免 wait_for 的 TimerHandle 内存泄漏）
            event = None
            if pool.qsize() > 0:
                event = await pool.get()
            else:
                # 池为空，短暂休眠后继续
                await asyncio.sleep(0.1)
                continue
            if event is None:
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
