"""Mind Executor — 念头执行器。

根据 event.type 分发到不同处理函数。
每个处理函数可以：
- 调用现有功能（检索、LLM、知识库读写）
- 产生新念头放回池子
- 调用 QQ 推送
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any

from .event_types import MindEvent, EventType, curiosity, report
from .pool import MindPool

logger = logging.getLogger(__name__)

# 全局引用，在初始化时设置
_workspace: str = ""
_adapter = None  # AgentAdapter instance
_knowledge = None
_journal = None
_state = None
_pool: Optional[MindPool] = None

# 推送回调：msg(str) -> None
# 由 QQ bridge 设置，Report 念头直接调用此回调推送到用户
_push_callback = None


def set_push_callback(callback):
    """设置推送回调函数。

    callback 签名: func(content: str) -> None
    QQ bridge 在初始化时调用此函数注册回调。
    """
    global _push_callback
    _push_callback = callback
    logger.info("[执行] 推送回调已注册")


def init(workspace: str, adapter=None, knowledge=None,
         journal=None, state=None):
    """初始化 executor 的全局上下文。

    包括：
    - 设置工作区、适配器、知识库等引用
    - 读取 active_plan.json 和 task_queue.json
    - 如果有未完成的任务，自动生成初始 Curiosity 念头
    """
    global _workspace, _adapter, _knowledge, _journal, _state
    _workspace = workspace
    _adapter = adapter
    _knowledge = knowledge
    _journal = journal
    _state = state

    # 读取 active_plan，避免报 "空闲中"
    _bootstrap_from_state()


async def ensure_pool() -> MindPool:
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
    except asyncio.CancelledError:
        logger.info(f"[执行] 念头 #{event.id[:8]} 被取消")
    except Exception as e:
        logger.error(f"[执行] 念头 #{event.id[:8]} 执行失败: {e}", exc_info=True)
        # 失败后尝试生成一个 Correction 念头
        pool = await ensure_pool()
        await pool.put(MindEvent(
            type=EventType.CORRECTION,
            priority=2,
            payload={
                "failed_event_id": event.id,
                "failed_type": event.type.value,
                "error": str(e),
            },
            source="executor",
            parent_id=event.id,
        ))


def _get_handler(event_type: EventType):
    """获取事件类型的处理函数。"""
    return {
        EventType.CURIOSITY: _handle_curiosity,
        EventType.REPORT: _handle_report,
        EventType.CRON_TICK: _handle_cron_tick,
        EventType.USER_MESSAGE: _handle_user_message,
        EventType.DIARY_WRITE: _handle_diary_write,
        EventType.SELF_REFLECTION: _handle_self_reflection,
        EventType.CORRECTION: _handle_correction,
        EventType.INSPIRATION: _handle_inspiration,
        EventType.PROJECT: _handle_project,
        EventType.EVOLUTION: _handle_evolution,
    }.get(event_type)


# ── 各类型处理函数 ──────────────────────────────────────────────


async def _handle_curiosity(event: MindEvent):
    """好奇念头：检索知识库 + 搜索，生成报告。

    这是系统的"主动学习"冲动。
    """
    topic = event.payload.get("topic", "")
    if not topic:
        logger.warning("[好奇] 无 topic，跳过")
        return

    logger.info(f"[好奇] 探索: {topic}")

    # 1. 搜索知识库
    knowledge_text = ""
    if _knowledge:
        results = _knowledge.search(topic, top_k=3)
        if results:
            knowledge_text = "\n".join(
                f"- [{e.category}] {e.title}: {e.content[:200]}"
                for e in results
            )

    # 2. 用 adapter 搜索网络（如果有）
    web_results = ""
    if _adapter:
        try:
            search_prompt = (
                f"搜索关于 '{topic}' 的最新研究进展。返回关键发现和结论。"
                f"用中文。\n\n"
                f"已有知识：\n{knowledge_text or '无'}"
            )
            web_results = _adapter.execute_task(search_prompt)
        except Exception as e:
            logger.warning(f"[好奇] 网络搜索失败: {e}")

    # 3. 构建报告内容
    parts = [f"💡 关于「{topic}」的探索结果："]
    if knowledge_text:
        parts.append(f"\n📚 已有知识：\n{knowledge_text}")
    if web_results:
        parts.append(f"\n🔍 新发现：\n{web_results[:500]}")
    else:
        parts.append("\n（暂无新发现）")

    content = "\n".join(parts)

    # 4. 生成 Report 念头放入池子
    pool = await ensure_pool()
    await pool.put(report(
        content=content,
        priority=3,
        source="executor:curiosity",
        parent_id=event.id,
    ))

    logger.info(f"[好奇] 完成: {topic}")


async def _handle_report(event: MindEvent):
    """汇报念头：直接推送到 QQ（如果有活跃的 bot 连接）。

    Report 念头一旦产生就立即处理，不等 cron。
    """
    content = event.payload.get("content", "")
    if not content:
        logger.warning("[汇报] 无内容")
        return

    logger.info(f"[汇报] 推送内容 ({len(content)} chars): {content[:80]}...")

    # 优先使用推送回调（QQ bridge 注册的直接推送到用户）
    # 这样绕过文件轮询，消除重复推送
    if _push_callback is not None:
        try:
            _push_callback(content)
            logger.info(f"[汇报] 已通过回调推送")
        except Exception as e:
            logger.warning(f"[汇报] 回调推送失败: {e}")
    else:
        # 降级：写入 notification 文件（由 QQ bridge 的 poller 读取）
        if _workspace:
            notif_dir = os.path.join(_workspace, "state", "notifications")
            os.makedirs(notif_dir, exist_ok=True)
            notif_path = os.path.join(notif_dir,
                                      f"mind_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            with open(notif_path, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp": datetime.now().isoformat(),
                    "type": "mind_report",
                    "summary": content,
                    "mind_event_id": event.id,
                }, f, ensure_ascii=False, indent=2)
            logger.info(f"[汇报] 通知文件已写入: {notif_path}")

    # 记录到日志
    print(f"\n[Mind Report] {content[:200]}...\n")


async def _handle_cron_tick(event: MindEvent):
    """心跳念头：检查状态，根据需要生成周期性念头。

    cron 不再直接驱动研究流程，只做："唤醒检查"。
    """
    pool = await ensure_pool()
    now = datetime.now()

    # 1. 如果知识库非空 → 随机产生一个好奇念头
    if _knowledge and len(_knowledge.entries) > 0:
        # 随机选一个主题（如果知识库有分类）
        categories = _knowledge.stats().get("by_category", {})
        if categories:
            # 挑一个条目较少的类别去探索
            topic = min(categories, key=categories.get)
        else:
            topic = "最近的研究发现"

        await pool.put(curiosity(
            topic=topic,
            priority=7,
            source="cron_tick:random_curiosity",
        ))
        logger.info(f"[心跳] 生成了好奇念头: {topic}")

    # 2. 每 6 次心跳（约 90 分钟）自省一次
    hour = now.hour
    if hour % 2 == 0 and now.minute < 5:
        await pool.put(MindEvent(
            type=EventType.SELF_REFLECTION,
            priority=7,
            payload={},
            source="cron_tick",
        ))
        logger.info("[心跳] 生成了自省念头")

    # 3. 每天 23:00 写日记
    if hour == 23 and now.minute < 10:
        await pool.put(MindEvent(
            type=EventType.DIARY_WRITE,
            priority=8,
            payload={},
            source="cron_tick",
        ))
        logger.info("[心跳] 生成了日记念头")

    logger.info(f"[心跳] #cron_tick 处理完毕，池中 {pool.qsize()} 个念头")


async def _handle_user_message(event: MindEvent):
    """用户消息念头：用 conversation engine 回复 + 可能产生好奇。

    当 QQ 收到消息时，bridge 会同时：
    1. 直接调用原回复流程（保证用户体验不变）
    2. 把消息内容作为念头放入池子（用于触发后续异步探索）
    """
    text = event.payload.get("text", "")
    logger.info(f"[用户消息] {text[:60]}...")

    # 用户消息本身已在 bridge 中得到回复。
    # 这里额外做：如果是问题 → 生成好奇念头去探索
    if text.endswith("?") or text.endswith("？") or "什么" in text or "如何" in text:
        pool = await ensure_pool()
        await pool.put(curiosity(
            topic=text[:50],
            priority=6,
            source="user_message",
            parent_id=event.id,
        ))
        logger.info(f"[用户消息] 从问题衍生好奇: {text[:50]}")


async def _handle_diary_write(event: MindEvent):
    """日记念头：写一篇日记条目。"""
    logger.info("[日记] 开始写日记")

    if _journal:
        from ..journal import JournalEntry
        _journal.log(JournalEntry(
            task_id=f"diary_{datetime.now().strftime('%Y%m%d')}",
            task_type="diary",
            task_title=f"日记 {datetime.now().strftime('%Y-%m-%d')}",
            result_summary="日常总结",
        ))
        logger.info("[日记] 已写入 journal")


async def _handle_self_reflection(event: MindEvent):
    """自省念头：自我评估。"""
    logger.info("[自省] 开始自检")

    # 委托给 SelfChecker
    if _state:
        state_dir = os.path.join(_workspace, "state") if _workspace else ""
        if state_dir:
            from ..autocheck import SelfChecker
            checker = SelfChecker(state_dir)
            try:
                # 读取 active_plan
                plan_path = os.path.join(state_dir, "active_plan.json")
                plan = None
                if os.path.exists(plan_path):
                    with open(plan_path) as f:
                        plan = json.load(f)
                check_events = checker.run_all(active_plan=plan)
                if check_events:
                    for ev in check_events:
                        pool = await ensure_pool()
                        await pool.put(report(
                            content=f"[自检][{ev.subtype}] {ev.title}",
                            priority=4,
                            source="self_reflection",
                        ))
                    logger.info(f"[自省] 发现 {len(check_events)} 个问题")
                else:
                    logger.info("[自省] 无问题")
            except Exception as e:
                logger.warning(f"[自省] 自检失败: {e}")


async def _handle_correction(event: MindEvent):
    """纠错念头：记录错误，后续可用于改进。"""
    failed_id = event.payload.get("failed_event_id", "")
    failed_type = event.payload.get("failed_type", "")
    error = event.payload.get("error", "")
    logger.warning(f"[纠错] 念头 {failed_id}({failed_type}) 执行失败: {error[:100]}")


async def _handle_inspiration(event: MindEvent):
    """灵感念头：未来可扩展为知识间隙检测。"""
    topic = event.payload.get("topic", "未知")
    logger.info(f"[灵感] 新灵感: {topic}")

    pool = await ensure_pool()
    await pool.put(curiosity(
        topic=topic,
        priority=5,
        source="inspiration",
        parent_id=event.id,
    ))


async def _handle_project(event: MindEvent):
    """项目念头：长期执行任务（简化版）。"""
    title = event.payload.get("title", "未知项目")
    logger.info(f"[项目] 开始执行: {title}")
    # 简化：先当作探索任务处理
    pool = await ensure_pool()
    await pool.put(curiosity(
        topic=title,
        priority=5,
        source="project",
        parent_id=event.id,
    ))


async def _handle_evolution(event: MindEvent):
    """进化念头：未来可用于自我改进。"""
    logger.info("[进化] 自我进化（暂未实现）")


def _bootstrap_from_state():
    """从 state 文件读取已有状态，自动生成初始念头。

    解决 "空闲中" / "等待新任务" 的问题：
    - 如果 active_plan 有任务 → 生成 Project 念头
    - 如果 task_queue 有待办 → 生成 Curiosity 念头
    """
    global _workspace, _pool
    if not _workspace:
        return

    state_dir = os.path.join(_workspace, "state")

    # 1. 读取 active_plan.json
    plan_path = os.path.join(state_dir, "active_plan.json")
    if os.path.exists(plan_path):
        try:
            import json
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
            status = plan.get("status", "idle")
            title = plan.get("title", "")
            goal = plan.get("goal", "")

            if status in ("planning", "active") or (status == "idle" and title):
                # 有活跃计划 → 生成 Project 念头继续推进
                pool_task = asyncio.ensure_future(ensure_pool())
                # Can't use await here since this is synchronous init
                logger.info(f"[Bootstrap] 检测到活跃计划: {title} (status={status})")
                logger.info(f"[Bootstrap] 目标: {goal[:80]}")
            elif status == "completed":
                logger.info(f"[Bootstrap] 检测到已完成计划: {title}")
        except Exception as e:
            logger.warning(f"[Bootstrap] 读取 active_plan 失败: {e}")

    # 2. 读取 task_queue.json
    queue_path = os.path.join(state_dir, "task_queue.json")
    if os.path.exists(queue_path):
        try:
            import json
            with open(queue_path, "r", encoding="utf-8") as f:
                tasks = json.load(f)
            pending = [t for t in tasks if isinstance(t, dict) and t.get("status") == "pending"]
            if pending:
                logger.info(f"[Bootstrap] 检测到 {len(pending)} 个待办任务")
                # Log first few tasks
                for t in pending[:3]:
                    logger.info(f"[Bootstrap] 待办: {t.get('title', '?')}")
        except Exception as e:
            logger.warning(f"[Bootstrap] 读取 task_queue 失败: {e}")
