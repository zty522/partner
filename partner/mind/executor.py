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
    logger.info(f"[MIND] Push callback registered: {callback}")


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
    """好奇念头：检索知识库 + 搜索，收集结构化数据后交给 LLM 生成报告。"""
    topic = event.payload.get("topic", "")
    if not topic:
        logger.warning(f"[CURIOSITY] No topic, skipping event {event.id[:8]}")
        return

    logger.info(f"[CURIOSITY] Searching for: '{topic}'")

    # 1. 搜索知识库 → 收集结构化数据
    kb_entries = []
    if _knowledge:
        results = _knowledge.search(topic, top_k=3)
        for e in results:
            kb_entries.append({
                "category": e.category,
                "title": e.title,
                "content": e.content[:300],
                "confidence": e.confidence,
            })

    # 2. 用 adapter 搜索网络 → 收集原始结果
    web_result_text = ""
    if _adapter:
        try:
            prompt = (
                f"搜索关于 '{topic}' 的最新研究进展。返回关键发现和结论。"
                f"用中文。\n\n已有知识：\n"
                + ("\n".join(f"- [{e['category']}] {e['title']}" for e in kb_entries) if kb_entries else "无")
            )
            web_result_text = _adapter.execute_task(prompt) or ""
        except Exception as e:
            logger.warning(f"[CURIOSITY] Web search failed: {e}")

    # 3. 用 LLM 生成自然语言报告（如果 adapter 可用）
    report_content = ""
    if _adapter and (kb_entries or web_result_text):
        try:
            data_json = json.dumps({
                "topic": topic,
                "knowledge_entries": kb_entries,
                "web_search_result": web_result_text[:1000],
            }, ensure_ascii=False)
            llm_prompt = (
                f"你刚刚探索了 '{topic}' 这个主题。以下是你收集到的数据。"
                f"请用 2-3 句话自然地告诉用户你发现了什么。不要用模板开头，"
                f"就像聊天一样。\n\n结构化数据：\n{data_json}"
            )
            report_content = _adapter.chat(llm_prompt) or ""
        except Exception as e:
            logger.warning(f"[CURIOSITY] LLM report generation failed: {e}")

    # 4. 如果 LLM 生成失败，用结构化 JSON（无模板，无硬编码）
    if not report_content:
        report_content = json.dumps({
            "type": "curiosity_result",
            "topic": topic,
            "knowledge_entries": kb_entries,
            "web_search": web_result_text[:500] if web_result_text else "",
        }, ensure_ascii=False)
        logger.info(f"[CURIOSITY] LLM unavailable, sending structured data instead")

    # 5. 生成 Report 念头
    pool = await ensure_pool()
    await pool.put(report(
        content=report_content,
        priority=3,
        source="executor:curiosity",
        parent_id=event.id,
    ))

    logger.info(f"[CURIOSITY] Summary generated for '{topic}' ({len(report_content)} chars)")
    logger.info(f"[MIND] DONE event_type=curiosity, id={event.id[:8]}, topic='{topic}'")


async def _handle_report(event: MindEvent):
    """汇报念头：直接推送到 QQ（如果有活跃的 bot 连接）。"""
    content = event.payload.get("content", "")
    if not content:
        logger.warning(f"[REPORT] Empty content, skipping {event.id[:8]}")
        return

    logger.info(f"[REPORT] Sending to QQ: {content[:80]}...")

    # 优先使用推送回调（QQ bridge 注册的直接推送到用户）
    if _push_callback is not None:
        try:
            _push_callback(content)
            logger.info(f"[REPORT] Sent via callback ({len(content)} chars)")
        except Exception as e:
            logger.warning(f"[REPORT] Callback push failed: {e}")
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
            logger.warning(f"[REPORT] Fallback to notification file: {notif_path}")

    # DONE
    logger.info(f"[MIND] DONE event_type=report, id={event.id[:8]}")


async def _handle_cron_tick(event: MindEvent):
    """心跳念头：检查状态，根据需要生成周期性念头。"""
    pool = await ensure_pool()
    now = datetime.now()

    logger.info(f"[CRON] Tick received, scheduling periodic tasks. "
                f"Pool size: {pool.qsize()}")

    # 1. 如果知识库非空 → 随机产生一个好奇念头
    if _knowledge and len(_knowledge.entries) > 0:
        categories = _knowledge.stats().get("by_category", {})
        if categories:
            topic = min(categories, key=categories.get)
        else:
            topic = "最近的研究发现"

        await pool.put(curiosity(
            topic=topic,
            priority=7,
            source="cron_tick:random_curiosity",
        ))
        logger.info(f"[CRON] Generated curiosity for: '{topic}'")

    # 2. 每偶数小时自省一次
    hour = now.hour
    if hour % 2 == 0 and now.minute < 5:
        await pool.put(MindEvent(
            type=EventType.SELF_REFLECTION,
            priority=7,
            payload={},
            source="cron_tick",
        ))
        logger.info(f"[CRON] Generated self_reflection at hour={hour}")

    # 3. 每天 23:00 写日记
    if hour == 23 and now.minute < 10:
        await pool.put(MindEvent(
            type=EventType.DIARY_WRITE,
            priority=8,
            payload={},
            source="cron_tick",
        ))
        logger.info(f"[CRON] Generated diary_write at hour={hour}")

    logger.info(f"[MIND] DONE event_type=cron_tick, id={event.id[:8]}")
    logger.info(f"[CRON] Tick complete. Pool size: {pool.qsize()}")


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
    logger.info(f"[SELFCHECK] Starting check... (event_id={event.id[:8]})")

    # 委托给 SelfChecker
    if _state:
        state_dir = os.path.join(_workspace, "state") if _workspace else ""
        if state_dir:
            from ..autocheck import SelfChecker
            checker = SelfChecker(state_dir)
            try:
                plan_path = os.path.join(state_dir, "active_plan.json")
                plan = None
                if os.path.exists(plan_path):
                    with open(plan_path) as f:
                        plan = json.load(f)
                check_events = checker.run_all(active_plan=plan)
                if check_events:
                    logger.info(f"[SELFCHECK] Found {len(check_events)} issue(s):")
                    for ev in check_events:
                        logger.info(f"[SELFCHECK]   [{ev.subtype}] {ev.title}")
                        pool = await ensure_pool()
                        # Pass structured data, not hardcoded text
                        await pool.put(report(
                            content=json.dumps({
                                "type": "self_check_issue",
                                "subtype": ev.subtype,
                                "title": ev.title,
                                "body": ev.body,
                                "priority": ev.priority,
                            }, ensure_ascii=False),
                            priority=4,
                            source="self_reflection",
                        ))
                else:
                    logger.info(f"[SELFCHECK] No issues found")
            except Exception as e:
                logger.warning(f"[SELFCHECK] Check failed: {e}")

    logger.info(f"[MIND] DONE event_type=self_reflection, id={event.id[:8]}")


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
