"""Mind Scheduler — 念头调度器。

核心循环 `mind_loop()`：
1. 内置自脉冲（默认 30 分钟，用于健康检查/恢复，不是 OS 自启动）
2. 不断从念头池中取出优先级最高的念头
3. 创建 asyncio.Task 去执行（不阻塞循环）
4. 当池为空时短暂休眠（0.1 秒）
5. 捕获异常，保证循环不崩溃

启动方式：asyncio.run(mind_loop())
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

from .pool import MindPool
from .executor import execute_event
from .event_types import MindEvent, EventType

logger = logging.getLogger(__name__)

# 最大并发执行数，防止念头溢出
MAX_CONCURRENT = 10
# 自脉冲间隔（秒）。Partner 进程启动后才生效；断电后的进程启动
# 需要依赖 Windows 启动项/任务计划，启动后 WAKE_UP 会立即恢复活跃项目。
SELF_PULSE_INTERVAL = 1800  # 30 分钟


def _state_dir(workspace: str, save_path: str = "") -> str:
    if workspace:
        return os.path.join(workspace, "state")
    if save_path:
        return os.path.dirname(save_path)
    return ""


def _read_json(path: str, default: dict) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else dict(default)
    except Exception:
        return dict(default)


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _record_runtime_event(workspace: str, save_path: str, event: MindEvent, status: str, ok: bool = True) -> None:
    """Keep old status/stats files meaningful for the new mind-loop runtime."""
    state_dir = _state_dir(workspace, save_path)
    if not state_dir:
        return
    now = datetime.now().isoformat()
    hb_path = os.path.join(state_dir, "heartbeat.json")
    stats_path = os.path.join(state_dir, "stats.json")
    prev_hb = _read_json(hb_path, {})
    prev_stats = _read_json(stats_path, {"total_cycles": 0, "total_tasks_completed": 0})
    cycle_count = int(prev_hb.get("cycle_count") or 0)
    total_cycles = int(prev_stats.get("total_cycles") or 0)
    total_done = int(prev_stats.get("total_tasks_completed") or 0)
    if status == "idle":
        cycle_count += 1
        total_cycles += 1
        if ok:
            total_done += 1
    _write_json(hb_path, {
        "last_heartbeat": now,
        "status": status,
        "current_task_id": event.type.value if hasattr(event.type, "value") else str(event.type),
        "cycle_count": cycle_count,
        "crash_count": int(prev_hb.get("crash_count") or 0),
    })
    _write_json(stats_path, {
        **prev_stats,
        "total_cycles": total_cycles,
        "total_tasks_completed": total_done,
        "last_event_type": event.type.value if hasattr(event.type, "value") else str(event.type),
        "last_event_ok": bool(ok),
        "last_heartbeat": now,
    })


async def mind_loop(pool: MindPool = None, save_path: str = "", workspace: str = "",
                    pulse_interval_sec: int = SELF_PULSE_INTERVAL):
    """念头调度器主循环（带持久化 + WAKE_UP 唤醒）。

    永久运行，不断从池中取念头、创建 Task 执行。
    当池为空时短暂休眠避免 CPU 空转。
    内置自脉冲定时器，无需外部 cron 注入 CRON_TICK。
    注意：它不能负责电脑开机后的进程启动，只负责进程已运行后的自检。
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
    if workspace or save_path:
        _record_runtime_event(
            workspace,
            save_path,
            MindEvent(type=EventType.WAKE_UP, priority=0, payload={}, source="startup"),
            "running",
            ok=True,
        )

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

    # 内置自脉冲定时器（30 分钟间隔自动注入 CRON_TICK）
    last_pulse = 0.0
    # 空闲检测
    last_nonempty_time = asyncio.get_event_loop().time()
    has_logged_idle = False

    try:
        while True:
            # 清理已完成的 task
            pending_tasks = {t for t in pending_tasks if not t.done()}

            # ── 自脉冲：每 30 分钟自动注入 CRON_TICK ──
            now = asyncio.get_event_loop().time()
            if now - last_pulse >= pulse_interval_sec:
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
                await asyncio.sleep(0.1)
                continue

            # 创建异步 Task 执行
            _record_runtime_event(workspace, save_path, event, "working", ok=True)
            task = asyncio.create_task(
                _run_event_safely(event, workspace=workspace, save_path=save_path),
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


async def _run_event_safely(event: MindEvent, workspace: str = "", save_path: str = ""):
    """安全执行一个念头（不抛出异常）。"""
    try:
        await execute_event(event)
        _record_runtime_event(workspace, save_path, event, "idle", ok=True)
    except asyncio.CancelledError:
        logger.info(f"[调度] 念头 {event.id[:8]} 执行被取消")
        _record_runtime_event(workspace, save_path, event, "cancelled", ok=False)
    except Exception as e:
        logger.error(f"[调度] 念头 {event.id[:8]} 抛出未捕获异常: {e}",
                     exc_info=True)
        _record_runtime_event(workspace, save_path, event, "error", ok=False)
