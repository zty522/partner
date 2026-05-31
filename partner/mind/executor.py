"""Mind Executor — Hermes 调度转发层。

仅保留 PROJECT / CRON_TICK / REPORT / WAKE_UP 四种事件类型。
Partner 只负责：读 state → 调 Hermes → 转发回复 → 按 UPDATE_STATE: 标记写 state。
"""

import asyncio
import hashlib
import logging
import os
import re
import time as _time
from datetime import datetime
from typing import Optional

from .event_types import MindEvent, EventType, report
from .pool import MindPool
from ..adapter import USER_FRIENDLY_PROGRESS_REPLY

logger = logging.getLogger(__name__)

# ── 全局引用 ────────────────────────────────────────────────────────
_workspace: str = ""
_adapter = None  # AgentAdapter instance
_pool: Optional[MindPool] = None

# 推送回调：msg(str) -> None
_push_callback = None

# 报告去重缓存：{content_hash: timestamp}，10分钟内同一内容不重复推送
_report_dedup_cache: dict = {}


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _compact_state_snapshot(state_md: str) -> str:
    """Shrink verbose state.md into a compact snapshot for executor prompts."""
    if not state_md:
        return "（新项目，尚无状态记录）"

    lines = [line.rstrip() for line in state_md.splitlines()]
    picked = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# 项目：") or stripped.startswith("最后更新:"):
            picked.append(stripped)
            continue
        if stripped.startswith("## 当前状态") or stripped.startswith("当前状态：") or stripped.startswith("当前聚焦方向："):
            picked.append(stripped)
            continue
        if stripped.startswith("- "):
            picked.append(_clip(stripped, 90))
        if len(picked) >= 4:
            break
    if not picked:
        return _clip(state_md, 260)
    return "\n".join(picked[:4])


def _project_file_hints(workspace: str, title: str) -> str:
    from ..project_state import get_project_dir

    project_dir = get_project_dir(workspace, title)
    if not os.path.isdir(project_dir):
        return "（暂无项目文件）"
    names = []
    for name in sorted(os.listdir(project_dir)):
        path = os.path.join(project_dir, name)
        if os.path.isfile(path):
            names.append(name)
    if not names:
        return "（暂无项目文件）"
    return ", ".join(names[:4])


def _choose_micro_objective(workspace: str, title: str, state_md: str, step: int) -> tuple[str, str]:
    """Return a single-step objective and preferred output artifact."""
    from ..project_state import get_project_dir

    project_dir = get_project_dir(workspace, title)
    title_lc = title.lower()
    state_lc = (state_md or "").lower()

    if "agent" in title_lc and "文献" in title:
        framework_path = os.path.join(project_dir, "evaluation_framework_outline.md")
        gap_matrix_path = os.path.join(project_dir, "benchmark_gap_matrix.md")
        memory_gap_path = os.path.join(project_dir, "long_memory_gap_note.md")
        roadmap_path = os.path.join(project_dir, "next_benchmark_roadmap.md")
        if not os.path.exists(framework_path):
            return (
                "只写一份评测框架提纲，先补 3-5 条核心评测维度，不扩展到新检索。",
                framework_path,
            )
        if not os.path.exists(gap_matrix_path):
            return (
                "只整理一张 benchmark gap matrix，写 4-6 行“已覆盖/未覆盖/影响”的对照。",
                gap_matrix_path,
            )
        if not os.path.exists(memory_gap_path):
            return (
                "只补一张长期记忆评测缺口 note，写清为什么它仍是空白，以及后续怎么补 benchmark。",
                memory_gap_path,
            )
        if not os.path.exists(roadmap_path):
            return (
                "只写一个 2-3 项的后续阅读路线图，每项一句理由。",
                roadmap_path,
            )
        return (
            "只补现有评测框架里最薄弱的一节，最多新增 5 行，不开新主题。",
            framework_path if step % 2 == 0 else memory_gap_path,
        )

    if "鲍曼不动杆菌" in title or "分子生成" in title:
        experiment_path = os.path.join(project_dir, "next_experiment.md")
        scaleup_path = os.path.join(project_dir, "scaleup_plan.md")
        mge_path = os.path.join(project_dir, "mge_followup.md")
        if not os.path.exists(experiment_path):
            return (
                "只写一个下一轮实验计划，包含目标、输入、产出和验收标准，各用一行。",
                experiment_path,
            )
        if not os.path.exists(scaleup_path):
            return (
                "只写一个扩大到 100+ 基因组的样本扩展计划，控制在 6 行内。",
                scaleup_path,
            )
        if not os.path.exists(mge_path):
            return (
                "只写一个移动遗传元件后续分析 note，说明要看什么、为什么重要。",
                mge_path,
            )
        return (
            "只补一个最关键瓶颈的最小实验动作，控制在 5 行内，不铺开多个方向。",
            experiment_path if step % 2 == 0 else mge_path,
        )

    if "年龄预测" in title:
        recovery_path = os.path.join(project_dir, "recovery_checklist.md")
        seed_path = os.path.join(project_dir, "bootstrap_plan.md")
        if not os.path.exists(recovery_path):
            return (
                "已知最新有效源目录是 /mnt/e/work/年龄预测/age_pred_v2。只写一个恢复清单，"
                "围绕这个目录列出先复制什么、先验证什么，不要去别处搜索。",
                recovery_path,
            )
        if not os.path.exists(seed_path):
            return (
                "已知最新有效源目录是 /mnt/e/work/年龄预测/age_pred_v2。只写一个启动计划，"
                "围绕这个目录列 3-5 个最小步骤，不要去别处搜索。",
                seed_path,
            )
        return (
            "已知最新有效源目录是 /mnt/e/work/年龄预测/age_pred_v2。只补一个恢复/启动动作，"
            "最多 5 行，不要展开多个新设想，也不要去别处搜索。",
            recovery_path if step % 2 == 0 else seed_path,
        )

    if "benchmark" in state_lc or "agentbench" in state_lc or "swe-bench" in state_lc:
        return (
            "把当前 benchmark 相关结论整理成一个可复用的小产物，并明确下一步只推进一个最小子问题。",
            "",
        )

    return (
        f"围绕项目「{title}」只推进一个最小闭环步骤。优先基于本地现有状态与文件，产出一个可记录的新结论、"
        f"小文档或明确的下一步执行结果。当前是 step {step}，不要发散。",
        "",
    )


def _build_project_prompt(workspace: str, title: str, state_md: str, step: int) -> tuple[str, str]:
    objective, artifact_path = _choose_micro_objective(workspace, title, state_md, step)
    file_hints = _project_file_hints(workspace, title)
    artifact_hint = os.path.basename(artifact_path) if artifact_path else "（优先补充现有项目文档或状态）"
    state_snapshot = _compact_state_snapshot(state_md)
    prompt = (
        f"你是项目执行器，持续推进项目「{title}」。\n"
        f"目标：{objective}\n"
        f"状态摘要：{state_snapshot}\n"
        f"现有文件：{file_hints}\n"
        f"建议产物：{artifact_hint}\n"
        f"规则：只做一个最小闭环；优先用本地内容；默认不联网；只允许 HTTPS；禁止 curl|bash / curl|python；"
        f"不要问用户，不要给选项，不要碰 /mnt/e/work/biomni*；不要输出 tool_call、function、terminal、read_file、write_file 标签；"
        f"不要描述你“打算检查环境”，不要先说你要去看什么，直接给最终正文。"
        f"把状态摘要视为可信输入，除非目标明确要求，否则不要再重复检查这些文件是否存在。\n"
        f"严格只输出：\n"
        f"DONE: <本轮完成>\n"
        f"FINDINGS: <最多两条发现，用；分隔>\n"
        f"NEXT: <下一步>\n"
        f"STATE_DELTA: <3-6行状态增量，纯文本，不要重写整份 state.md>\n"
        f"ARTIFACT_CONTENT: <如果建议产物不是空，就直接给这个文件的正文；若无则写 EMPTY>\n"
    )
    return prompt, artifact_path


def _extract_labeled_field(text: str, label: str) -> str:
    pattern = rf"^{re.escape(label)}:\s*(.*)$"
    match = re.search(pattern, text, re.MULTILINE)
    return (match.group(1).strip() if match else "")


def _parse_structured_project_response(response: str) -> dict:
    response = (response or "").strip()
    if not response:
        return {}
    if response == USER_FRIENDLY_PROGRESS_REPLY:
        return {}

    if "Too many requests" in response or "Error code: 429" in response:
        return {}
    if "Reached maximum iterations" in response and "DONE:" not in response:
        return {}

    step_done = _extract_labeled_field(response, "DONE")
    next_action = _extract_labeled_field(response, "NEXT")

    findings = []
    findings_line = _extract_labeled_field(response, "FINDINGS")
    if findings_line:
        findings = [
            item.strip(" -")
            for item in re.split(r"[；;]", findings_line)
            if item.strip(" -")
        ][:2]

    state_match = re.search(
        r"^STATE_DELTA:\s*(?P<body>.*)\Z",
        response,
        re.MULTILINE | re.DOTALL,
    )
    tail = state_match.group("body").strip() if state_match else ""
    artifact_content = ""
    state_update = tail
    if tail:
        artifact_split = re.search(
            r"(?m)^\s*ARTIFACT_CONTENT:\s*",
            tail,
        )
        if artifact_split:
            idx = artifact_split.start()
            state_update = tail[:idx].strip()
            artifact_content = tail[artifact_split.end():].strip()
    if not artifact_content:
        artifact_content = _extract_labeled_field(response, "ARTIFACT_CONTENT")

    parsed = {
        "step_done": step_done,
        "findings": findings,
        "next_action": next_action,
        "state_delta": state_update,
        "artifact_content": artifact_content,
    }
    if not any([parsed["step_done"], parsed["findings"], parsed["next_action"], parsed["state_delta"]]):
        return {}
    return parsed


def _merge_state_delta(existing_state: str, title: str, delta: str, step_done: str, next_action: str) -> str:
    """Append a compact delta block instead of asking the model to rewrite full state."""
    existing = (existing_state or "").strip()
    lines = [line.rstrip() for line in (delta or "").splitlines() if line.strip()]
    if not lines and not step_done and not next_action:
        return existing or f"# 项目：{title}\n"

    if not existing:
        existing = f"# 项目：{title}"
    if "# 项目：" not in existing:
        existing = f"# 项目：{title}\n\n{existing}"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = [f"最后更新: {now}", "", "## 当前状态"]
    if step_done:
        block.append(f"- 最近完成：{step_done}")
    for line in lines[:4]:
        cleaned = line.lstrip("- ").strip()
        if cleaned:
            block.append(f"- {cleaned}")
    if next_action:
        block.append(f"- 下一步：{next_action}")

    prefix = []
    seen_current = False
    for raw in existing.splitlines():
        stripped = raw.strip()
        if stripped.startswith("最后更新:"):
            continue
        if stripped == "## 当前状态":
            seen_current = True
            continue
        if seen_current and stripped.startswith("## "):
            seen_current = False
        if not seen_current:
            prefix.append(raw)
    cleaned_prefix = "\n".join(prefix).strip()
    return (cleaned_prefix + "\n\n" + "\n".join(block)).strip() + "\n"


def _normalize_artifact_content(content: str) -> str:
    text = (content or "").strip()
    if not text or text.upper() == "EMPTY":
        return ""
    text = re.sub(r"^```(?:markdown|md|text)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    return text


def _fallback_artifact_content(response: str, artifact_path: str) -> str:
    basename = os.path.basename(artifact_path or "")
    text = (response or "").strip()
    if not basename or not text:
        return ""

    if basename == "next_experiment.md":
        lines = []
        for prefix in ("目标：", "输入：", "产出：", "验收标准："):
            match = re.search(rf"(?m)^{re.escape(prefix)}.*$", text)
            if match:
                lines.append(match.group(0).strip())
        return "\n".join(lines).strip()

    code_block = re.search(r"```(?:markdown|md|text)?\s*(?P<body>.*?)```", text, re.DOTALL)
    if code_block:
        return code_block.group("body").strip()

    named_block = re.search(
        rf"{re.escape(basename)}.*?\n(?P<body>(?:.+\n?){{3,40}})",
        text,
        re.DOTALL,
    )
    if named_block:
        return named_block.group("body").strip()
    return ""


def _write_artifact_file(path: str, content: str) -> bool:
    if not path or not content:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n")
        return True
    except OSError:
        return False


def _format_user_progress_update(parsed: dict) -> str:
    if not parsed.get("step_done"):
        return ""
    lines = [f"最近完成：{parsed['step_done']}"]
    findings = parsed.get("findings") or []
    if findings:
        lines.append("关键发现：")
        for finding in findings[:2]:
            lines.append(f"- {_clip(finding, 140)}")
    if parsed.get("next_action"):
        lines.append(f"下一步：{parsed['next_action']}")
    return "\n".join(lines).strip()


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
    2. 构造紧凑的单步执行 prompt
    3. 解析结构化回复并更新 state.md
    4. 追加执行日志
    5. 只把系统组装后的进展摘要推送到 QQ
    6. 将自身放回等待室（5分钟后继续）
    """
    title = event.payload.get("title", "")
    if not title:
        logger.warning(f"[PROJECT] No title, skipping")
        return

    logger.info(f"[PROJECT] Executing step {event.payload.get('step', 0)}: '{title[:60]}'")

    # 0. 确保活跃项目标记
    from ..project_state import (
        append_log,
        read_state_md,
        resolve_project_name,
        set_active,
        write_state_md,
    )
    title = resolve_project_name(_workspace, title) or title
    set_active(_workspace, title)

    # 1. 读取项目状态
    state_md = read_state_md(_workspace, title)

    # 2. 调用 Hermes — 只推进一个最小闭环步骤
    response = None
    if _adapter:
        prompt, artifact_path = _build_project_prompt(
            workspace=_workspace,
            title=title,
            state_md=state_md,
            step=event.payload.get("step", 0),
        )
        try:
            response = _adapter.chat(prompt, purpose="project")
        except Exception as e:
            logger.warning(f"[PROJECT] Hermes 调用异常: {e}")
            response = None

    # 3. 处理 Hermes 回复
    hermes_response = (response or "").strip()
    parsed = _parse_structured_project_response(hermes_response)
    new_state = _merge_state_delta(
        existing_state=state_md,
        title=title,
        delta=parsed.get("state_delta", ""),
        step_done=parsed.get("step_done", ""),
        next_action=parsed.get("next_action", ""),
    ) if parsed else ""
    push_text = _format_user_progress_update(parsed)

    timed_out_or_stalled = (
        not hermes_response
        or hermes_response.strip() == USER_FRIENDLY_PROGRESS_REPLY
    )
    invalid_structured_reply = bool(hermes_response and not timed_out_or_stalled and not push_text)

    artifact_written = False
    if parsed and artifact_path and not timed_out_or_stalled:
        artifact_text = _normalize_artifact_content(parsed.get("artifact_content", ""))
        if not artifact_text:
            artifact_text = _fallback_artifact_content(hermes_response, artifact_path)
        artifact_written = _write_artifact_file(artifact_path, artifact_text)

    if new_state and not timed_out_or_stalled:
        write_state_md(_workspace, title, new_state)
        logger.info(f"[PROJECT] 状态已更新（{len(new_state)} 字符）")
    if artifact_written:
        logger.info(f"[PROJECT] 产物已写入: {os.path.basename(artifact_path)}")

    # 3b. 追加到 log.md
    if hermes_response and not timed_out_or_stalled:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        append_log(_workspace, title, f"### {ts}\n{hermes_response}\n")

    # 4. 推送至 QQ
    pool = await ensure_pool()

    if not timed_out_or_stalled and push_text:
        await pool.put(report(
            content=push_text,
            priority=4,
            source="project:push",
        ))
    elif invalid_structured_reply:
        logger.info("[PROJECT] No user-facing progress push: invalid structured reply")
    else:
        logger.info("[PROJECT] No user-facing progress push: model timed out or returned stall fallback")

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
