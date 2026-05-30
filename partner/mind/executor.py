"""Mind Executor — Hermes 委托循环。

仅保留 PROJECT / CRON_TICK / REPORT / WAKE_UP 四种事件类型。
所有状态读写使用自然语言 .md 文件，Partner 只负责读写，Hermes 负责理解。
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
    """初始化 executor（简化版：只设置 workspace + adapter）。

    旧版参数 knowledge/journal/state 已废弃，传入时仅记录日志。
    """
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
        # Auto-save pool after each event
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
    """项目念头：Hermes 委托循环。

    1. 读取项目状态文件 (state.md)
    2. 调用 Hermes 询问下一步动作
    3. Hermes 返回动作类型（SEARCH/CODE/ANALYZE/REFLECT/CONTINUE）
    4. 执行 Hermes 建议的动作，把结果追加到 log.md
    5. 更新 state.md
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

    # 2. 调用 Hermes 询问下一步动作
    action_type = "CONTINUE"
    action_detail = ""
    action_result = ""

    if _adapter:
        prompt = (
            f"你是 Hermes，正在推进项目「{title}」。\n\n"
            f"## 项目当前状态\n\n"
            f"{state_md[:3000] if state_md else '（新项目，尚无状态记录）'}\n\n"
            f"## 指令\n\n"
            f"请根据项目状态决定下一步动作。\n\n"
            f"只返回以下格式（不要多余文字，不要markdown代码块）：\n"
            f"ACTION: SEARCH | CODE | ANALYZE | REFLECT | CONTINUE\n"
            f"DETAIL: <你的详细指令，50-200字>"
        )
        try:
            response = _adapter.chat(prompt) or ""
            for line in response.strip().split("\n"):
                line = line.strip()
                if line.startswith("ACTION:"):
                    raw = line.replace("ACTION:", "", 1).strip()
                    raw = raw.split()[0] if raw.split() else "CONTINUE"
                    action_type = raw.upper()
                elif line.startswith("DETAIL:"):
                    action_detail = line.replace("DETAIL:", "", 1).strip()
        except Exception as e:
            logger.warning(f"[PROJECT] Hermes 调用失败: {e}")

    # 3. 执行 Hermes 建议的动作
    if action_type == "SEARCH":
        try:
            if _adapter:
                search_prompt = (
                    f"搜索主题：{action_detail or title}\\n"
                    f"返回 3-5 个最有价值的发现（论文、工具、代码库等），\\n"
                    f"每条包含标题和简要说明。"
                )
                action_result = _adapter.chat(search_prompt) or "搜索完成"
            else:
                action_result = "[SEARCH] 搜索适配器不可用"
        except Exception as e:
            action_result = f"搜索执行失败: {e}"
    elif action_type == "CODE":
        action_result = f"[CODE] Hermes 指示代码操作: {action_detail}"
    elif action_type == "ANALYZE":
        action_result = f"[ANALYZE] Hermes 指示分析操作: {action_detail}"
    elif action_type == "REFLECT":
        action_result = f"[REFLECT] Hermes 反思: {action_detail}"
    else:  # CONTINUE / 未知
        action_result = f"[CONTINUE] Hermes 认为暂无必要采取新动作"

    # 4. 记录到 log.md
    log_entry = (
        f"### 动作: {action_type}\n"
        f"- **指令**: {action_detail}\n"
        f"- **结果**: {action_result[:600]}"
    )
    append_log(_workspace, title, log_entry)

    # 5. 更新 state.md
    old_state = state_md
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    updated_state = (
        f"# 项目：{title}\n"
        f"最后更新: {ts}\n\n"
        f"## 当前状态\n"
        f"- 最后动作: {action_type}\n"
        f"- 动作详情: {action_detail or '（无）'}\n"
        f"- 执行结果摘要: {action_result[:200]}\n\n"
    )
    if old_state and not old_state.startswith("# 项目："):
        updated_state += f"## 历史状态\n{old_state[:2000]}\n"
    updated_state += "\n---\n*状态由 Partner 自动更新，Hermes 读取完整上下文。*"
    write_state_md(_workspace, title, updated_state)

    # 6. 将自身放回等待室（5分钟后继续）
    pool = await ensure_pool()
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

    # 7. 推送进展摘要
    push_msg = (
        f"🔬 [{title}] 第 {event.payload.get('step', 0)} 轮完成\n"
        f"动作: {action_type}\n"
        f"{action_detail[:100]}"
    )
    await pool.put(report(
        content=push_msg,
        priority=4,
        source="project:push",
    ))

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
    # 清理超过 10 分钟的旧缓存
    stale = [k for k, v in _report_dedup_cache.items() if now_ts - v > 600]
    for k in stale:
        del _report_dedup_cache[k]
    if h in _report_dedup_cache:
        logger.debug(f"[REPORT] 去重跳过重复推送: {content_stripped[:60]}...")
        return
    _report_dedup_cache[h] = now_ts

    logger.info(f"[REPORT] Sending: {content[:80]}...")

    # 仅使用推送回调
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
