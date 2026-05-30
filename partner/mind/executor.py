"""Mind Executor — Hermes 调度转发层。

仅保留 PROJECT / CRON_TICK / REPORT / WAKE_UP 四种事件类型。
Partner 只负责：读 state → 调 Hermes → 转发回复 → 按 UPDATE_STATE: 标记写 state。
"""

import asyncio
import hashlib
import logging
import os
import time as _time
from datetime import datetime
from typing import Optional

from .event_types import MindEvent, EventType, report
from .pool import MindPool

logger = logging.getLogger(__name__)

# ── 全局引用 ────────────────────────────────────────────────────────
_workspace: str = ""
_adapter = None  # AgentAdapter instance
_pool: Optional[MindPool] = None

# 推送回调：msg(str) -> None
_push_callback = None

# 报告去重缓存：{content_hash: timestamp}，10分钟内同一内容不重复推送
_report_dedup_cache: dict = {}


# ── 公开接口 ────────────────────────────────────────────────────────


def set_push_callback(callback):
    """设置推送回调函数。

    callback 签名: func(content: str) -> None
    QQ bridge 在初始化时调用此函数注册回调。
    """
    global _push_callback
    _push_callback = callback
    logger.info(f"[MIND] Push callback registered: {callback}")


def init(workspace: str, adapter=None, **kwargs):
    """初始化 executor（简化版：只设置 workspace + adapter）。"""
    global _workspace, _adapter
    _workspace = workspace
    _adapter = adapter
    if kwargs:
        logger.debug(f"[MIND] init 忽略废弃参数: {list(kwargs.keys())}")
    logger.info(f"[MIND] Executor initialized: workspace={workspace}")


async def ensure_pool() -> MindPool:
    """获取 MindPool 单例。"""
    global _pool
    if _pool is None:
        _pool = await MindPool.get_instance()
    return _pool


async def execute_event(event: MindEvent):
    """执行一个念头：按类型分发到对应的处理函数。"""
    logger.info(f"[执行] 开始处理 #{event.id[:8]} type={event.type.value} "
                f"pri={event.priority}")

    try:
        handler = _get_handler(event.type)
        if handler:
            await handler(event)
        else:
            logger.warning(f"[执行] 无处理函数: {event.type.value}")
        _p = await ensure_pool()
        if _p._auto_save:
            _p.save()
    except asyncio.CancelledError:
        logger.info(f"[执行] 念头 #{event.id[:8]} 被取消")
    except Exception as e:
        logger.error(f"[执行] 念头 #{event.id[:8]} 执行失败: {e}", exc_info=True)


# ── 事件分发 ────────────────────────────────────────────────────────


def _get_handler(event_type: EventType):
    """获取事件类型的处理函数（仅保留 4 种）。"""
    return {
        EventType.PROJECT: _handle_project,
        EventType.CRON_TICK: _handle_cron_tick,
        EventType.REPORT: _handle_report,
        EventType.WAKE_UP: _handle_wake_up,
    }.get(event_type)


# ── PROJECT ─────────────────────────────────────────────────────────


async def _handle_project(event: MindEvent):
    """项目念头：纯 Hermes 调度转发层。

    1. 读取项目状态
    2. 调用 Hermes（带完整上下文），让 Hermes 自主决定做什么
    3. 检查回复中是否有 UPDATE_STATE: 标记 → 如有则更新 state.md
    4. 追加回复到 log.md
    5. 将回复原样推送到 QQ
    6. 将自身放回等待室（5分钟后继续）
    """
    title = event.payload.get("title", "")
    if not title:
        logger.warning(f"[PROJECT] No title, skipping")
        return

    logger.info(f"[PROJECT] Executing step {event.payload.get('step', 0)}: '{title[:60]}'")

    # 0. 确保活跃项目标记
    from ..project_state import (
        read_state_md, write_state_md, append_log, set_active,
    )
    set_active(_workspace, title)

    # 1. 读取项目状态
    state_md = read_state_md(_workspace, title)

    # 2. 调用 Hermes — 让 Hermes 自主决策和执行
    response = None
    if _adapter:
        prompt = (
            f"你是 Hermes，正在持续推进项目「{title}」。\n\n"
            f"## 当前项目状态\n\n"
            f"{state_md[:5000] if state_md else '（新项目，尚无状态记录）'}\n\n"
            f"## 约束\n\n"
            f"- 请不要碰 /mnt/e/work/biomni* 下的任何文件或进程。那是用户手动运行的独立项目。\n\n"
            f"## 你的角色\n\n"
            f"你是这个项目的研究主管。你可以调用任何你拥有的工具（搜索、代码执行、文件读写等）"
            f"来推进项目。请自主决定下一步做什么——搜索资料、分析数据、编写代码、反思进展都行。\n\n"
            f"## 输出要求\n\n"
            f"用自然语言直接回复。告诉用户你做了什么、发现了什么、下一步计划是什么。"
            f"你的回复会被原样推送给用户。\n\n"
            f"如果需要更新项目状态（比如有新发现、完成了一个阶段、需要切换方向等），"
            f"请在回复末尾加上：\n"
            f"UPDATE_STATE:\n"
            f"<新的完整状态文本>\n"
            f"（如果不需要更新状态，可以省略这一行）"
        )
        try:
            response = _adapter.chat(prompt)
        except Exception as e:
            logger.warning(f"[PROJECT] Hermes 调用异常: {e}")
            response = None

    # 3. 处理 Hermes 回复
    hermes_response = (response or "").strip()

    # 3a. 检查 UPDATE_STATE: 标记
    update_marker = "\nUPDATE_STATE:\n"
    new_state = None
    push_text = hermes_response

    if update_marker in hermes_response:
        parts = hermes_response.split(update_marker, 1)
        push_text = parts[0].strip()
        new_state = parts[1].strip()
        if new_state:
            write_state_md(_workspace, title, new_state)
            logger.info(f"[PROJECT] 状态已更新（{len(new_state)} 字符）")

    # 3b. 追加到 log.md
    if hermes_response:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        append_log(_workspace, title, f"### {ts}\n{hermes_response}\n")

    # 4. 推送至 QQ
    pool = await ensure_pool()

    if not hermes_response:
        # 超时或空输出 —— 简短提示，不阻塞
        push_text = "Hermes 正在处理中，下一轮再汇报进展。"

    await pool.put(report(
        content=push_text,
        priority=4,
        source="project:push",
    ))

    # 5. 将自身放回等待室（5分钟后继续）
    next_step = event.payload.get("step", 0) + 1
    await pool.put(MindEvent(
        type=EventType.PROJECT,
        priority=6,
        payload={"title": title, "step": next_step},
        wake_after=_time.time() + 300,
        source="project:recur",
        parent_id=event.id,
    ))
    logger.info(f"[PROJECT] Re-queued for step {next_step} (wake in 300s)")

    logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, "
                f"title='{title[:40]}'")


# ── REPORT ──────────────────────────────────────────────────────────


async def _handle_report(event: MindEvent):
    """汇报念头：直接推送到 QQ（如果有活跃的 bot 连接），含去重。
    移除旧版 JSON 降级写入逻辑，仅保留 push_callback。
    """
    content = event.payload.get("content", "")
    if not content:
        logger.warning(f"[REPORT] Empty content, skipping {event.id[:8]}")
        return

    # ── 去重：同一内容在 10 分钟内不重复推送 ──
    global _report_dedup_cache
    content_stripped = content.strip()
    h = hashlib.md5(content_stripped.encode()).hexdigest()
    now_ts = _time.time()
    stale = [k for k, v in _report_dedup_cache.items() if now_ts - v > 600]
    for k in stale:
        del _report_dedup_cache[k]
    if h in _report_dedup_cache:
        logger.debug(f"[REPORT] 去重跳过重复推送: {content_stripped[:60]}...")
        return
    _report_dedup_cache[h] = now_ts

    logger.info(f"[REPORT] Sending: {content[:80]}...")

    if _push_callback is not None:
        try:
            _push_callback(content)
            logger.info(f"[REPORT] Sent via callback ({len(content)} chars)")
        except Exception as e:
            logger.warning(f"[REPORT] Callback push failed: {e}")
    else:
        logger.info(f"[REPORT] No push callback registered, content dropped")

    logger.info(f"[MIND] DONE event_type=report, id={event.id[:8]}")


# ── CRON_TICK ───────────────────────────────────────────────────────


async def _handle_cron_tick(event: MindEvent):
    """心跳念头：检查 active_project.txt → 如有则创建 PROJECT 事件。

    不提示用户、不搜索、只检查持久化的活跃项目标记。
    """
    pool = await ensure_pool()

    # 检查是否有 PROJECT 事件已在池中
    has_project = False
    for ev in getattr(pool._queue, '_queue', []):
        if hasattr(ev, 'type') and ev.type == EventType.PROJECT:
            has_project = True
            break
    if not has_project:
        for eid, (wake_at, ev) in pool._waiting_room.items():
            if hasattr(ev, 'type') and ev.type == EventType.PROJECT:
                has_project = True
                break

    if not has_project:
        from ..project_state import get_active
        active_name = get_active(_workspace)
        if active_name:
            logger.info(f"[CRON] 检测到活跃项目: {active_name}")
            await pool.put(MindEvent(
                type=EventType.PROJECT,
                priority=2,
                payload={"title": active_name, "step": 0},
                source="cron_tick:resume_active",
            ))
        else:
            logger.info(f"[CRON] 无活跃项目，什么都不做")

    logger.info(f"[MIND] DONE event_type=cron_tick, id={event.id[:8]}")


# ── WAKE_UP ─────────────────────────────────────────────────────────


async def _handle_wake_up(event: MindEvent):
    """唤醒脉冲：检查 active_project.txt → 如有则创建 PROJECT 事件。

    没有活跃项目则什么都不做（不提示用户、不搜索）。
    """
    pool = await ensure_pool()
    logger.info(f"[WAKE_UP] 唤醒脉冲开始执行，池大小: {pool.qsize()}")

    # 检查是否有 PROJECT 事件已在池中
    has_project = False
    for ev in getattr(pool._queue, '_queue', []):
        if hasattr(ev, 'type') and ev.type == EventType.PROJECT:
            has_project = True
            break
    if not has_project:
        for eid, (wake_at, ev) in pool._waiting_room.items():
            if hasattr(ev, 'type') and ev.type == EventType.PROJECT:
                has_project = True
                break

    if not has_project:
        from ..project_state import get_active
        active_name = get_active(_workspace)
        if active_name:
            logger.info(f"[WAKE_UP] 从 active_project.txt 恢复项目: {active_name}")
            await pool.put(MindEvent(
                type=EventType.PROJECT,
                priority=2,
                payload={"title": active_name, "step": 0},
                source="wake_up:resume_active",
            ))
        else:
            logger.info(f"[WAKE_UP] 无活跃项目，什么都不做")

    logger.info(f"[MIND] DONE event_type=wake_up, id={event.id[:8]}")
