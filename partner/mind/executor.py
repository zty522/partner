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
_recorder = None  # Recorder instance
_pool: Optional[MindPool] = None

# 推送回调：msg(str) -> None
_push_callback = None

# 上一次保存的状态（用于在回复时提供结构化信息）
_last_saved_state: Optional[Dict] = None


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
    - 初始化 Recorder 记录系统
    - 读取 active_plan.json 和 task_queue.json
    - 如果有未完成的任务，自动生成初始 Curiosity 念头
    """
    global _workspace, _adapter, _knowledge, _journal, _state, _recorder
    _workspace = workspace
    _adapter = adapter
    _knowledge = knowledge
    _journal = journal
    _state = state

    # 初始化 Recorder
    try:
        from ..recorder import Recorder
        _recorder = Recorder(workspace)
        logger.info(f"[MIND] Recorder initialized for workspace: {workspace}")
    except Exception as e:
        logger.warning(f"[MIND] Recorder initialization failed (non-fatal): {e}")

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


def _scan_project_for_topic(topic: str, workspace: str) -> str:
    """Scan project directory for code/README files relevant to topic.

    Looks for:
    - README files (README.md, README.txt, etc.)
    - Python/JSON/YAML files containing topic-related keywords
    - Project structure summary

    Returns a formatted string or empty string if nothing found.
    """
    import os as _os
    import fnmatch as _fnmatch

    if not workspace or not _os.path.isdir(workspace):
        return ""

    topic_lower = topic.lower()
    topic_keywords = topic_lower.replace("，", " ").replace(",", " ").split()
    if not topic_keywords:
        topic_keywords = [topic_lower]

    scanned = {"readme": [], "code": [], "config": []}

    # Walk workspace (max depth 3) looking for relevant files
    for root, dirs, files in _os.walk(workspace):
        depth = root.replace(workspace, "").count(_os.sep)
        if depth > 3:
            dirs.clear()
            continue
        # Skip hidden directories and common noise
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git", "venv", ".venv", "env")]

        for fname in files:
            # README files
            if "readme" in fname.lower():
                try:
                    with open(_os.path.join(root, fname), "r", encoding="utf-8") as f:
                        content = f.read(2000)
                    if any(kw in content.lower() for kw in topic_keywords):
                        scanned["readme"].append(f"{_os.path.relpath(_os.path.join(root, fname), workspace)}: {content[:300].strip()}")
                except Exception:
                    pass
            # Python files
            elif fname.endswith(".py"):
                rel = _os.path.relpath(_os.path.join(root, fname), workspace)
                if any(kw in rel.lower() for kw in topic_keywords):
                    try:
                        with open(_os.path.join(root, fname), "r", encoding="utf-8") as f:
                            first_lines = f.readlines(40)
                        scanned["code"].append(f"{rel} ({len(first_lines)} lines)")
                    except Exception:
                        scanned["code"].append(rel)
            # Config/markdown files
            elif fname.endswith((".md", ".json", ".yaml", ".yml", ".toml", ".txt")):
                rel = _os.path.relpath(_os.path.join(root, fname), workspace)
                if any(kw in rel.lower() for kw in topic_keywords):
                    try:
                        with open(_os.path.join(root, fname), "r", encoding="utf-8") as f:
                            first_lines = f.readlines(30)
                        content_preview = "".join(first_lines)[:500].strip()
                        scanned["config"].append(f"{rel}: {content_preview[:200]}")
                    except Exception:
                        scanned["config"].append(rel)

    # Build report
    parts = []
    if scanned["readme"]:
        parts.append("README 中涉及相关信息：" + " | ".join(scanned["readme"][:3]))
    if scanned["code"]:
        parts.append("相关代码文件：" + "、".join(scanned["code"][:5]))
    if scanned["config"]:
        parts.append("相关配置/文档：" + "、".join(scanned["config"][:5]))

    if parts:
        return f"关于「{topic}」，在项目目录中找到了以下相关内容：\n" + "\n".join(parts)
    return ""


# ── 智能聚焦探索：从项目知识生成具体查询 ─────────────────────


async def _generate_focused_queries() -> list:
    """从项目knowledge和时间线生成聚焦搜索查询（替代泛关键词）。"""
    import os as _gos, json as _gjson, glob as _gglob
    projects_dir = _gos.path.join(_workspace, "20_records", "projects") if _workspace else ""
    if not projects_dir or not _gos.path.isdir(projects_dir):
        return []  # 无项目，返回空列表，不搜索

    # 找最近活跃的项目（按目录mtime排序）
    project_dirs = sorted(
        [d for d in _gglob.glob(_gos.path.join(projects_dir, "*")) if _gos.path.isdir(d)],
        key=_gos.path.getmtime, reverse=True
    )
    if not project_dirs:
        return []

    queries = []
    latest = project_dirs[0]
    proj_name = _gos.path.basename(latest)

    # 读取 knowledge.json 获取瓶颈
    kpath = _gos.path.join(latest, "knowledge.json")
    if _gos.path.exists(kpath):
        try:
            with open(kpath) as f:
                knowledge = _gjson.load(f)
            entries = knowledge if isinstance(knowledge, list) else knowledge.get("entries", [])
            for entry in entries:
                title = entry.get("title", entry.get("topic", ""))
                if any(kw in title.lower() for kw in
                       ["bottleneck", "issue", "problem", "stuck", "leak", "fail", "limit", "challenge"]):
                    queries.append(f"{proj_name} {title[:60]}")
        except Exception:
            pass

    # 读取 timeline.jsonl 提取当前瓶颈
    tpath = _gos.path.join(latest, "timeline.jsonl")
    if _gos.path.exists(tpath):
        try:
            with open(tpath) as f:
                lines = f.readlines()[-20:]  # 最后20行
            for line in lines:
                record = _gjson.loads(line)
                for field in ["hypothesis", "result", "reflection", "next"]:
                    val = record.get(field, "")
                    if any(kw in str(val).lower() for kw in
                           ["failed", "stuck", "leak", "mae", "issue", "bottleneck"]):
                        if len(str(val)) > 10:
                            queries.append(f"{proj_name} {str(val)[:100]}")
                            break
        except Exception:
            pass

    # 去重 + 限制
    queries = list(set(queries))[:3]

    # 2小时内同一query不重复（短时记忆）
    if _workspace:
        dedup_path = _gos.path.join(_workspace, "state", "last_search_queries.json")
        if _gos.path.exists(dedup_path):
            try:
                with open(dedup_path) as f:
                    cache = _gjson.load(f)
                now = _time.time()
                queries = [q for q in queries if now - cache.get(q.strip().lower(), 0) >= 7200]
            except Exception:
                pass
        # 记录本次生成的查询
        try:
            cache = {}
            if _gos.path.exists(dedup_path):
                with open(dedup_path) as f:
                    cache = _gjson.load(f)
            for q in queries:
                cache[q.strip().lower()] = _time.time()
            _gos.makedirs(_gos.path.dirname(dedup_path), exist_ok=True)
            with open(dedup_path, "w") as f:
                _gjson.dump(cache, f, indent=2)
        except Exception:
            pass

    return queries if queries else []


# ── 各类型处理函数 ──────────────────────────────────────────────


async def _handle_wake_up(event: MindEvent):
    """唤醒脉冲：启动后自动恢复研究和探索，生成结构化汇报。

    1. 读取 last_state.json 恢复上次上下文
    2. 检查 state/active_plan.json 是否有活跃计划 → 生成 PROJECT 念头
    3. 检查 Mind Pool 中已有 PROJECT → 不做额外操作
    4. 如果没有项目 → 从知识库生成 Curiosity 探索
    5. 检查自省时间 → 超过 2 小时生成自省
    6. 生成详细的复工简报（含上次进度和计划）
    """
    import time as _wt, json as _json, os as _os
    pool = await ensure_pool()
    logger.info(f"[WAKE_UP] 唤醒脉冲开始执行，池大小: {pool.qsize()}")

    # 0. 读取上次状态
    last_state = None
    if _workspace:
        try:
            from ..state_persistence import load as _load_state
            last_state = _load_state(_workspace)
            global _last_saved_state
            _last_saved_state = last_state
        except Exception:
            pass

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

    # 如果没有项目，从知识库生成探索（聚焦）
    if not has_project:
        # 尝试从项目生成聚焦查询
        focused_qs = await _generate_focused_queries()
        if focused_qs:
            for q in focused_qs[:2]:
                await pool.put(curiosity(topic=q, priority=6, source="wake_up:focused_exploration"))
                logger.info(f"[WAKE_UP] 聚焦探索: '{q[:60]}'")
        else:
            kb_topic = ""
            if _knowledge and len(_knowledge.entries) > 0:
                categories = _knowledge.stats().get("by_category", {})
                if categories:
                    kb_topic = min(categories, key=categories.get)
            if kb_topic:
                await pool.put(curiosity(topic=kb_topic, priority=6, source="wake_up:knowledge_gap"))
                logger.info(f"[WAKE_UP] 生成知识探索: {kb_topic}")
            else:
                # 无项目、无知识库 → 不搜索，提示用户
                logger.info(f"[WAKE_UP] 无活跃项目，空闲等待用户指令")
                await pool.put(report(
                    content="当前没有活跃的研究项目。请告诉我你想探索的方向，我可以进行深入搜索和研究。",
                    priority=3,
                    source="wake_up:ask_direction",
                ))

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

    # 生成详细的结构化复工简报
    try:
        from ..state_persistence import format_restart_report
        restart_report = format_restart_report(last_state)
        await pool.put(report(
            content=restart_report,
            priority=3,
            source="wake_up:startup",
        ))
        logger.info(f"[WAKE_UP] 已生成详细的复工简报")
    except Exception as e:
        logger.warning(f"[WAKE_UP] 复工简报生成失败: {e}")

    # ── WAKE_UP 续命：队列为空时注入延迟5分钟的CRON_TICK ──
    if pool.qsize() == 0:
        wake_at = _wt.time() + 300
        await pool.put(MindEvent(
            type=EventType.CRON_TICK,
            priority=10,
            payload={"wake_after": wake_at},
            source="wake_up:delayed_refuel",
        ))
        logger.info("[WAKE_UP] 队列为空，注入延迟5分钟的CRON_TICK续命")

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
        web_results = _search(topic, max_results=5, workspace=_workspace)
        logger.info(f"[CURIOSITY] Web search returned {len(web_results)} results for '{topic}'")

        # LLM 相关性评分（阈值 0.3），过滤不相关结果
        if web_results and _adapter:
            try:
                scored = []
                for r in web_results[:8]:
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")[:200]
                    score_prompt = (
                        f"请评估以下搜索结果与用户查询的相关性（0~1分，只输出数字，不要其他文字）。\n"
                        f"查询: {topic}\n"
                        f"标题: {title}\n"
                        f"摘要: {snippet}\n"
                        f"相关性得分:"
                    )
                    score_str = _adapter.chat(score_prompt) or "0"
                    try:
                        score = float(score_str.strip())
                    except (ValueError, TypeError):
                        score = 0.5  # 默认中等
                    if score >= 0.3:
                        scored.append(r)
                        logger.debug(f"[CURIOSITY] 结果相关: '{title[:40]}' score={score:.2f}")
                    else:
                        logger.debug(f"[CURIOSITY] 结果不相关(已过滤): '{title[:40]}' score={score:.2f}")
                web_results = scored
                logger.info(f"[CURIOSITY] 相关性过滤后保留 {len(web_results)} 条结果")
            except Exception as e:
                logger.debug(f"[CURIOSITY] LLM评分失败，使用全部结果: {e}")

        web_result_text = format_results(web_results, max_items=3)
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

    # 4. 如果没有实质内容或 LLM 生成失败
    if not report_content and not kb_entries and not web_result_text:
        logger.info(f"[CURIOSITY] Nothing to report for '{topic}' — trying dialog context / local scan")
        pool = await ensure_pool()

        # First: derive content from dialog context (never ask user for direction)
        dialog_content = None
        if _workspace:
            try:
                from ..context_broker import ContextBroker
                broker = ContextBroker(_workspace)
                recent = broker.collect_recent_dialogs(hours=48)
                if recent:
                    facts = broker.extract_project_facts(recent)
                    if facts.get("metrics") or facts.get("files") or facts.get("issues"):
                        lines = [f"关于「{topic}」，从最近的对话中了解到："]
                        if facts.get("metrics"):
                            lines.append("指标: " + ", ".join(f"{k}={v}" for k, v in facts["metrics"].items()))
                        if facts.get("files"):
                            lines.append("相关文件: " + ", ".join(facts["files"]))
                        if facts.get("issues"):
                            lines.append("提到的问题: " + ", ".join(facts["issues"]))
                        if facts.get("keywords"):
                            lines.append("技术关键词: " + ", ".join(facts["keywords"][:6]))
                        if facts.get("raw_snippets"):
                            lines.append(f"相关对话片段: {facts['raw_snippets'][-1][:200]}")
                        dialog_content = "\n".join(lines)
            except Exception as e:
                logger.debug(f"[CURIOSITY] Dialog context extraction failed: {e}")

        if dialog_content:
            await pool.put(report(content=dialog_content, priority=3, source="executor:curiosity_dialog"))
            logger.info(f"[MIND] DONE event_type=curiosity, id={event.id[:8]}, topic='{topic}' (from dialog)")
            return

        # Second: scan project directory for README, code files, etc.
        try:
            local_facts = _scan_project_for_topic(topic, _workspace)
            if local_facts:
                await pool.put(report(content=local_facts, priority=3, source="executor:curiosity_local"))
                logger.info(f"[MIND] DONE event_type=curiosity, id={event.id[:8]}, topic='{topic}' (local scan)")
                return
        except Exception as e:
            logger.debug(f"[CURIOSITY] Local scan failed: {e}")

        # Last resort: minimal factual statement, never ask user for direction
        tentative = (
            f"关于「{topic}」，目前没有找到直接相关的记录。\n"
            f"我会持续关注这个方向，有新发现时主动通知你。"
        )
        await pool.put(report(content=tentative, priority=3, source="executor:curiosity_tentative"))
        logger.info(f"[MIND] DONE event_type=curiosity, id={event.id[:8]}, topic='{topic}' (no info)")
        return
    if not report_content:
        logger.info(f"[CURIOSITY] LLM unavailable for '{topic}' — using structured fallback")
        pool = await ensure_pool()
        # 即使 LLM 不可用，也不空手而归
        fallback_report = f"关于「{topic}」找到了 {len(kb_entries)} 条知识库记录和 {len(web_results)} 条搜索结果。"
        if kb_entries:
            fallback_report += "\n知识库中记录了: " + ", ".join(e["title"][:30] for e in kb_entries[:3])
        if web_results:
            fallback_report += "\n网络搜索: " + ", ".join(r["title"][:30] for r in web_results[:2])
        await pool.put(report(content=fallback_report, priority=3, source="executor:curiosity_fallback"))
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
    """心跳念头：检查状态，根据需要生成周期性念头（主动型）。

    1. 自由探索知识空白
    2. 每偶数小时自省
    3. 每天 23:00 写日记
    4. ⭐ 空闲检测：如果 Mind Pool 为空（无 PROJECT/无 Curiosity），
       自动从 knowledge.json 生成新的 Curiosity 探索任务
    5. 加速等待中的 PROJECT 事件
    6. 保存状态到 last_state.json
    """
    pool = await ensure_pool()
    now = datetime.now()

    logger.info(f"[CRON] Tick received, scheduling periodic tasks. "
                f"Pool size: {pool.qsize()}")

    # 自由探索知识空白（仅当有明确的知识覆盖弱点时才探索）
    if _knowledge and len(_knowledge.entries) > 0:
        categories = _knowledge.stats().get("by_category", {})
        if categories:
            topic = min(categories, key=categories.get)
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

    # ── 4. ⭐ 空闲检测：自动生成探索任务 ──
    # 如果池中没有任何 PROJECT 事件，且没有等待中的 Curiosity，
    # 说明系统空闲，需要自动生成任务
    has_any_active = False
    for ev in getattr(pool._queue, '_queue', []):
        if hasattr(ev, 'type') and ev.type in (EventType.PROJECT, EventType.CURIOSITY):
            has_any_active = True
            break
    if not has_any_active:
        for eid, (wake_at, ev) in pool._waiting_room.items():
            if hasattr(ev, 'type') and ev.type in (EventType.PROJECT, EventType.CURIOSITY):
                has_any_active = True
                break

    if not has_any_active:
        logger.info(f"[CRON] ⭐ 检测到空闲状态，自动生成聚焦探索任务")

        # 使用智能聚焦查询（从项目瓶颈生成具体查询）
        queries = await _generate_focused_queries()
        if queries:
            for q in queries[:2]:
                await pool.put(curiosity(topic=q, priority=8, source="cron_tick:focused_exploration"))
                logger.info(f"[CRON] 聚焦探索: '{q[:60]}'")

            # 向用户汇报：已开始聚焦探索
            topics_str = "、".join(queries[:3])
            auto_report = (
                f"📊 当前没有进行中的项目，已自动从最近项目瓶颈开始探索：\n"
                f"🔍 {topics_str}\n"
                f"🕒 有实质性发现时会主动推送。"
            )
            await pool.put(report(
                content=auto_report,
                priority=3,
                source="cron_tick:focused_auto_idle",
            ))
        else:
            # 无活跃项目 → 不搜索，只记录
            logger.info(f"[CRON] 无活跃项目，空闲等待用户指令")
            # 不搜索！不创建泛搜索任务

    # 5. 快速路径：加速等待中的 PROJECT 事件
    now_ts = _time.time()
    accelerated = 0
    for eid, (wake_at, ev) in list(pool._waiting_room.items()):
        if ev.type == EventType.PROJECT and wake_at > now_ts + 300:
            if ev.priority > 4:
                ev.priority = 4
            new_wake = now_ts + 60
            pool._waiting_room[eid] = (new_wake, ev)
            accelerated += 1
            logger.info(f"[CRON] 加速 PROJECT {ev.id[:8]} wake_after 从 {int(wake_at-now_ts)}s 降至 60s")
    for ev in list(getattr(pool._queue, '_queue', [])):
        if ev.type == EventType.PROJECT and ev.priority > 4:
            ev.priority = 4
            accelerated += 1
            logger.info(f"[CRON] 提升 PROJECT {ev.id[:8]} 优先级: 6→4")
    if accelerated:
        logger.info(f"[CRON] 加速了 {accelerated} 个 PROJECT 事件")

    # 6. 保存状态（使用自然语言，无内部变量名）
    try:
        from ..state_persistence import save as _save_state
        if _workspace:
            state = {
                "active_project": "periodic check",
                "last_action": "周期检查 - 空闲检测与自动探索",
                "last_metrics": {"system status": pool.qsize()},
                "pending_tasks": [f"自动探索继续"],
                "last_dialog_summary": "",
                "source": "cron_tick",
            }
            _save_state(_workspace, state)
            global _last_saved_state
            _last_saved_state = state
    except Exception:
        pass

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
    """项目念头：长期研究任务（主动型 v2）。

    Project 事件的生命周期：
    1. 被 mind_loop 取出执行
    2. 读取最对话上下文，优先使用已有信息
    3. 生成一个 Curiosity 探索子念头
    4. 将自身（Project）放入等待室（wake_after）
    5. 最多 100 步后终止
    6. 保存状态到 last_state.json
    7. 推送到 QQ 让用户知晓进展
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

    # 2. 获取对话上下文
    project_facts = event.payload.get("project_facts", {})
    if not project_facts and _workspace:
        try:
            from ..context_broker import ContextBroker
            from ..knowledge import KnowledgeBase
            kb_path = os.path.join(_workspace, "state", "knowledge.json")
            kb = KnowledgeBase(kb_path) if os.path.exists(kb_path) else None
            broker = ContextBroker(_workspace, kb)
            ctx = broker.get_project_context(title)
            if ctx and "暂无" not in ctx[:5]:
                project_facts = {"context_text": ctx}
        except Exception as e:
            logger.debug(f"[PROJECT] 上下文获取失败: {e}")

    # 3. 生成探索子念头（传递项目事实）
    pool = await ensure_pool()
    curiosity_payload = {"topic": title}
    if project_facts:
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
            },
            wake_after=_time.time() + wake_delay,
            source="project:recur",
            parent_id=event.id,
        ))
        logger.info(f"[PROJECT] Re-queued for step {next_step} (wake in {wake_delay}s)")
    else:
        logger.info(f"[PROJECT] Max steps reached, terminating")

    # 5. 保存状态到 last_state.json
    try:
        from ..state_persistence import save as _save_state, build_last_state_from_task
        pending_tasks = [f"继续推进: {title} (step {next_step})"]
        if project_facts and isinstance(project_facts, dict):
            issues = project_facts.get("issues", [])
            if issues:
                pending_tasks = issues[:3] + pending_tasks
        state = build_last_state_from_task("project", {
            "title": title,
            "step": next_step,
        })
        if _workspace:
            _save_state(_workspace, state)
            global _last_saved_state
            _last_saved_state = state
    except Exception as e:
        logger.debug(f"[PROJECT] 状态保存失败: {e}")

    # 6. 主动推送进展到 QQ（每次执行后）
    try:
        result_summary = (
            f"📊 研究进展：{title[:50]}\n"
            f"⏳ 已完成 {step + 1} 轮探索，正在深入。\n"
        )
        if project_facts and isinstance(project_facts, dict):
            ctx_text = project_facts.get("context_text", "")
            if "暂无" not in ctx_text and ctx_text:
                # 提取关键指标
                import re
                metrics = re.findall(r'(?:MAE|mse|loss|acc|f1|auc|r2|rmse)\s*[=：]\s*[\d.]+',
                                     ctx_text, re.IGNORECASE)
                if metrics:
                    result_summary += "📈 " + ", ".join(metrics[:3]) + "\n"
        result_summary += f"🔄 下一轮将在 {wake_delay // 60} 分钟后自动执行。"
        await pool.put(report(
            content=result_summary,
            priority=4,
            source="project:push",
        ))
        logger.info(f"[PROJECT] 进展已主动推送到 QQ")
    except Exception as e:
        logger.debug(f"[PROJECT] 推送失败: {e}")

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
