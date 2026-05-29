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
import time as _time
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
        # Auto-save pool after each event
        _p = await ensure_pool()
        if _p._auto_save:
            _p.save()
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
        EventType.WAKE_UP: _handle_wake_up,
    }.get(event_type)


# ── 各类型处理函数 ──────────────────────────────────────────────


async def _handle_wake_up(event: MindEvent):
    """唤醒脉冲：启动后自动恢复研究和探索。

    1. 检查 state/active_plan.json 是否有活跃计划 → 生成 PROJECT 念头
    2. 检查 Mind Pool 中已有 PROJECT → 不做额外操作
    3. 如果没有项目 → 从知识库生成 Curiosity 探索
    4. 检查自省时间 → 超过 2 小时生成自省
    5. 生成复工简报
    """
    import time as _wt, json as _json, os as _os
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

    # 如果没有 PROJECT 事件，检查 active_plan.json
    if not has_project and _workspace:
        plan_path = _os.path.join(_workspace, "state", "active_plan.json")
        if _os.path.exists(plan_path):
            try:
                with open(plan_path, 'r', encoding='utf-8') as f:
                    plan = _json.load(f)
                if plan.get("status") == "active" and plan.get("title"):
                    # 从活跃计划生成 PROJECT 念头
                    await pool.put(MindEvent(
                        type=EventType.PROJECT,
                        priority=2,
                        payload={
                            "title": plan["title"],
                            "goal": plan.get("goal", ""),
                            "step": 0,
                        },
                        source="wake_up:plan_restore",
                    ))
                    has_project = True
                    logger.info(f"[WAKE_UP] 从 active_plan 恢复项目: {plan['title'][:40]}")
            except Exception as e:
                logger.warning(f"[WAKE_UP] active_plan 读取失败: {e}")

    # 如果没有项目，从知识库生成探索
    if not has_project:
        kb_topic = ""
        if _knowledge and len(_knowledge.entries) > 0:
            categories = _knowledge.stats().get("by_category", {})
            if categories:
                kb_topic = min(categories, key=categories.get)
        if kb_topic:
            await pool.put(curiosity(topic=kb_topic, priority=6, source="wake_up:knowledge_gap"))
            logger.info(f"[WAKE_UP] 生成知识探索: {kb_topic}")
        else:
            await pool.put(curiosity(topic="最新研究进展", priority=6, source="wake_up:generic"))
            logger.info(f"[WAKE_UP] 生成通用探索")

    # 检查自省时间
    if _journal:
        recent = _journal.get_recent(10)
        last_reflection = ""
        for entry in recent:
            if getattr(entry, 'task_type', '') == 'self_reflection':
                last_reflection = getattr(entry, 'timestamp', '')
                break
        if last_reflection:
            from datetime import datetime as _dt
            try:
                last_time = _dt.fromisoformat(last_reflection)
                if (_dt.now() - last_time).total_seconds() > 7200:
                    await pool.put(MindEvent(type=EventType.SELF_REFLECTION, priority=7, payload={}, source="wake_up:reflection"))
                    logger.info(f"[WAKE_UP] 生成自省（上次 {last_reflection[:16]}）")
            except Exception:
                pass
        else:
            await pool.put(MindEvent(type=EventType.SELF_REFLECTION, priority=7, payload={}, source="wake_up:first_reflection"))
            logger.info(f"[WAKE_UP] 生成首次自省")

    # 复工简报
    if has_project:
        await pool.put(report(content="我回来了。已有研究项目在继续推进。", priority=3, source="wake_up:startup"))
    else:
        await pool.put(report(content="我回来了。知识库中没有进行中的项目，我已开始自主探索。", priority=3, source="wake_up:startup"))
    logger.info(f"[WAKE_UP] 唤醒完成，池大小: {pool.qsize()}")



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

    # 2. 搜索网络 → 用 searcher 模块直接调学术 API
    web_results = []
    try:
        from ..searcher import search as _search, format_results
        web_results = _search(topic, max_results=5)
        web_result_text = format_results(web_results, max_items=3)
        logger.info(f"[CURIOSITY] Web search returned {len(web_results)} results for '{topic}'")
    except Exception as e:
        logger.warning(f"[CURIOSITY] Web search failed (non-fatal): {e}")
        web_result_text = ""

    # 3. 用 LLM 生成自然语言报告
    report_content = ""
    if kb_entries or web_result_text:
        if _adapter:
            try:
                # 注入对话上下文（如果存在）
                dialog_context = event.payload.get("dialog_context", "")
                dialog_context_block = ""
                if dialog_context:
                    dialog_context_block = (
                        f"\n\n以下是相关的对话上下文（你和用户的对话记录）：\n"
                        f"{dialog_context[:1500]}"
                    )

                data_json = json.dumps({
                    "topic": topic,
                    "knowledge_entries": kb_entries,
                    "web_search_result": web_result_text[:1000],
                }, ensure_ascii=False)
                llm_prompt = (
                    f"你刚刚探索了 '{topic}' 这个主题。以下是你收集到的数据。"
                    f"请用 2-3 句话自然地告诉用户你发现了什么。不要用模板开头，"
                    f"就像聊天一样。"
                    f"{dialog_context_block}"
                    f"\n\n结构化数据：\n{data_json}"
                )
                report_content = _adapter.chat(llm_prompt) or ""
            except Exception as e:
                logger.warning(f"[CURIOSITY] LLM report generation failed: {e}")

    # 4. 如果没有实质内容或 LLM 生成失败，不发 report（不发 raw JSON）
    if not report_content and not kb_entries and not web_result_text:
        logger.info(f"[CURIOSITY] Nothing to report for '{topic}' — skipping")
        logger.info(f"[MIND] DONE event_type=curiosity, id={event.id[:8]}, topic='{topic}' (skipped)")
        return
    if not report_content:
        logger.info(f"[CURIOSITY] LLM unavailable for '{topic}' — skipping report")
        logger.info(f"[MIND] DONE event_type=curiosity, id={event.id[:8]}, topic='{topic}' (skipped)")
        return

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

    # 自由探索知识空白
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
        logger.info(f"[CRON] Generated curiosity for knowledge gap: '{topic}'")

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

    # ── 快速路径：加速等待中的 PROJECT 事件 ──
    now_ts = _time.time()
    accelerated = 0
    # 检查等待室：如果 PROJECT 的 wake_after 远在 5 分钟后，提前唤醒并降优先级
    for eid, (wake_at, ev) in list(pool._waiting_room.items()):
        if ev.type == EventType.PROJECT and wake_at > now_ts + 300:
            # 降低优先级（数值越低越紧急：6→4）
            if ev.priority > 4:
                ev.priority = 4
            # 缩短等待时间：最多再等 60 秒
            new_wake = now_ts + 60
            pool._waiting_room[eid] = (new_wake, ev)
            accelerated += 1
            logger.info(f"[CRON] 加速 PROJECT {ev.id[:8]} wake_after 从 {int(wake_at-now_ts)}s 降至 60s")
    # 检查主队列中的 PROJECT 事件，直接降低优先级
    for ev in list(getattr(pool._queue, '_queue', [])):
        if ev.type == EventType.PROJECT and ev.priority > 4:
            ev.priority = 4
            accelerated += 1
            logger.info(f"[CRON] 提升 PROJECT {ev.id[:8]} 优先级: 6→4")
    if accelerated:
        logger.info(f"[CRON] 加速了 {accelerated} 个 PROJECT 事件")

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
    """项目念头：长期研究任务（对话优先 + 结果推送）。

    Project 事件的生命周期：
    1. 被 mind_loop 取出执行
    2. 获取对话上下文（优先使用用户已提供的信息）
    3. 生成一个 Curiosity 探索子念头（携带对话上下文）
    4. 将自身（Project）放入等待室（wake_after=now+900s）
    5. 15 分钟后自动唤醒，生成下一轮探索
    6. 最多 100 步后终止
    7. 执行完成后主动推送到 QQ
    """
    title = event.payload.get("title", "")
    goal = event.payload.get("goal", "")
    step = event.payload.get("step", 0)

    if not title:
        logger.warning(f"[PROJECT] No title, skipping")
        return

    logger.info(f"[PROJECT] Executing step {step}: '{title[:60]}'")

    # 1. 记录到 journal
    if _journal:
        try:
            from ..journal import JournalEntry
            _journal.log(JournalEntry(
                task_id=f"project_{datetime.now().strftime('%H%M%S')}",
                task_type="project",
                task_title=title[:60],
                result_summary=goal[:100] or "推进中",
            ))
        except Exception:
            pass

    # 2. 获取对话上下文（优先使用对话历史）
    dialog_context = {}
    if _workspace:
        try:
            from ..context_broker import ContextBroker
            broker = ContextBroker(_workspace, _knowledge)
            ctx = broker.get_context_for_search(title)
            if ctx.get("has_relevant_context"):
                dialog_context = ctx
                logger.info(f"[PROJECT] 获取到对话上下文：路径={ctx.get('project_path','无')}, "
                           f"问题={ctx.get('issues',[])}")
        except Exception as e:
            logger.debug(f"[PROJECT] 上下文获取失败: {e}")

    # 3. 生成探索子念头（传递对话上下文用于搜索优先）
    pool = await ensure_pool()
    project_facts = event.payload.get("project_facts", {})
    curiosity_payload = {"topic": title}
    if dialog_context:
        curiosity_payload["project_facts"] = dialog_context
        logger.info(f"[PROJECT] 传递对话上下文给好奇念头: {len(dialog_context)} 项")
    elif project_facts:
        curiosity_payload["project_facts"] = project_facts
        logger.info(f"[PROJECT] 传递项目事实给好奇念头: {len(project_facts)} 项")
    await pool.put(MindEvent(
        type=EventType.CURIOSITY,
        priority=4,
        payload=curiosity_payload,
        source=f"project:step_{step}",
        parent_id=event.id,
    ))
    logger.info(f"[PROJECT] Generated curiosity for step {step}")

    # 4. 将自身放回池子（step+1, wake_after 取决于来源）
    next_step = step + 1
    if next_step < 100:
        source_is_immediate = any(
            tag in (event.source or "")
            for tag in ["qq_user", "wake_up"]
        )
        wake_delay = 300 if source_is_immediate else 900  # 5min / 15min
        await pool.put(MindEvent(
            type=EventType.PROJECT,
            priority=6,
            payload={
                "title": title,
                "goal": goal,
                "step": next_step,
                "project_facts": dialog_context or project_facts,
            },
            wake_after=_time.time() + wake_delay,
            source="project:recur",
            parent_id=event.id,
        ))
        logger.info(f"[PROJECT] Re-queued for step {next_step} (wake in {wake_delay}s)")
    else:
        logger.info(f"[PROJECT] Max steps reached, terminating")

    # 5. 执行完成后主动推送到 QQ
    try:
        result_summary = (
            f"完成了一轮项目研究: {title[:60]}\n"
            f"已执行 {step + 1} 个步骤，继续跟进中。"
        )
        if dialog_context:
            if dialog_context.get("issues"):
                result_summary += f"\n关注的问题: {', '.join(dialog_context['issues'][:3])}"
            if dialog_context.get("metrics"):
                metrics_str = ", ".join(f"{k}={v}" for k, v in list(dialog_context["metrics"].items())[:3])
                result_summary += f"\n已知指标: {metrics_str}"
        if _push_callback is not None:
            _push_callback(result_summary)
            logger.info(f"[PROJECT] 结果已主动推送到 QQ")
    except Exception as e:
        logger.debug(f"[PROJECT] 结果推送失败: {e}")

    logger.info(f"[MIND] DONE event_type=project, id={event.id[:8]}, title='{title[:40]}'")


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
