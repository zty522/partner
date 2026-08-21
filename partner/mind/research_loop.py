"""Research Loop — 自主研究循环（替代 OODA）

在 task 完成后决定是否继续、生成下一步任务、直接 enqueue 到事件队列。
不经过 desktop_inbox，不与消息流冲突。
"""

import logging
import os
import shutil
import json
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────

MAX_ROUNDS = 5             # 每轮最大自主循环次数
MAX_SAME_TYPE = 3          # 同类型连续轮次上限
OUTPUT_REQUIRED_TYPES = {   # 必须产出文件的实例（全部实例都需归档，知识才能承接）
    "01", "02", "03", "04", "05",
}

# 实例角色 → 默认研究方向
INSTANCE_ROLES = {
    "01": "desktop_monitor",
    "02": "knowledge_acquisition",
    "03": "deep_research",
    "04": "code_analysis",
    "05": "tool_testing",
}

# 一次性动作关键词：一轮即完成，重复无意义 → 不循环
_ONESHOT_KEYWORDS = (
    "截图", "截屏", "屏幕", "screenshot", "screen",
    "列表", "列出", "目录", "catalog", "ls",
    "发送", "发文件", "发消息", "推送",
    "查询", "查一下", "看一眼", "状态", "健康检查",
    "重启", "启动", "停止",
)

# 深度研究关键词：开放式任务，多轮深入有价值 → 循环
_RESEARCH_KEYWORDS = (
    "研究", "分析", "对比", "比较", "深入", "实现", "探索",
    "改进", "优化", "benchmark", "复现", "综述", "调研",
    "review", "实验", "评估", "剖析",
    "收集", "整理", "汇总", "归纳", "梳理",  # 内容收集/整理类，开放式可深入
)


def should_loop(user_request: str) -> bool:
    """根据任务类型判断是否进入自主循环。

    深度研究类（研究/分析/对比/深入/实现/探索/benchmark）→ True。
    一次性动作（截图/列表/发送/查询/状态）→ False。
    默认不循环（保守）。
    """
    text = (user_request or "").strip()
    if not text:
        return False
    # 研究意图优先：即使同时提到"列出/目录"，只要主意图是研究就循环
    if any(k in text for k in _RESEARCH_KEYWORDS):
        return True
    # 纯一次性动作 → 不循环
    if any(k in text for k in _ONESHOT_KEYWORDS):
        return False
    # 默认不循环（保守）
    return False

# 产出文件扩展名（归档时识别）
_OUTPUT_EXTS = (".md", ".png", ".jpg", ".csv", ".pdf", ".txt", ".json")


def _shared_knowledge_root(workspace: str) -> str:
    """从实例 workspace 推导共享知识库根目录（share/knowledge）。

    workspace = /mnt/e/work/partner_workspace/instances/03
    → /mnt/e/work/partner_workspace/share/knowledge
    """
    if not workspace:
        return ""
    parent = os.path.dirname(os.path.dirname(os.path.abspath(workspace)))
    return os.path.join(parent, "share", "knowledge")


def _instance_knowledge_dir(workspace: str, instance_id: str) -> str:
    """实例的知识库目录（latest 子目录存最新产出）。"""
    root = _shared_knowledge_root(workspace)
    if not root:
        return ""
    return os.path.join(root, instance_id, "latest")


def archive_outputs(instance_id: str, workspace: str, files: list[str], round_num: int = 0) -> list[str]:
    """把本轮产出归档到 share/knowledge/{id}/latest/，追加 history.jsonl。

    文件名带轮次后缀（如 analysis_r2.md），保留历史版本，实现 v1→v2→v3 可追溯。

    Returns:
        归档成功的目标文件路径列表。
    """
    sk_dir = _instance_knowledge_dir(workspace, instance_id)
    if not sk_dir:
        return []
    try:
        os.makedirs(sk_dir, exist_ok=True)
    except Exception:
        return []

    # 从 task 目录定位完整路径（mtime 排序，找最近几轮的产出）
    tasks_dir = os.path.join(workspace, "state", "tasks")
    archived: list[str] = []
    if os.path.isdir(tasks_dir):
        try:
            task_dirs = sorted(
                os.listdir(tasks_dir),
                key=lambda x: os.path.getmtime(os.path.join(tasks_dir, x)),
                reverse=True,
            )
        except Exception:
            task_dirs = []
        want = set(files or [])
        seen = set()
        for td in task_dirs[:3]:
            tp = os.path.join(tasks_dir, td)
            for fn in os.listdir(tp):
                if fn.startswith("_") and fn.startswith("_error"):
                    continue
                if fn.startswith("_step_"):
                    continue
                if fn in ("task_instance.json", "task_log.jsonl", "active_plan.json"):
                    continue
                if fn in seen:
                    continue
                if (want and fn not in want) and not fn.endswith(_OUTPUT_EXTS):
                    continue
                if fn in want or fn.endswith(_OUTPUT_EXTS):
                    src = os.path.join(tp, fn)
                    if not os.path.isfile(src):
                        continue
                    try:
                        # 带轮次后缀，保留历史版本
                        stem, ext = os.path.splitext(fn)
                        dst_fn = f"{stem}_r{round_num}{ext}" if round_num else fn
                        dst = os.path.join(sk_dir, dst_fn)
                        shutil.copy2(src, dst)
                        seen.add(fn)
                        archived.append(dst)
                    except Exception:
                        continue

    # 追加 history.jsonl（记录每轮归档）
    try:
        history_path = os.path.join(os.path.dirname(sk_dir), "history.jsonl")
        record = {
            "round": round_num,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "files": [os.path.basename(p) for p in archived],
        }
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if archived:
        logger.info("[RESEARCH_LOOP] %s archived %d files (round=%s) to shared_knowledge",
                    instance_id, len(archived), round_num)
    return archived


def load_latest_knowledge(instance_id: str, workspace: str, max_chars: int = 2000) -> str:
    """读取 share/knowledge/{id}/latest/ 下最新轮次产出的摘要文本。

    优先取轮次号（_rN 后缀）最大的 .md 文件，实现 v1→v2→v3 累积演进。
    """
    sk_dir = _instance_knowledge_dir(workspace, instance_id)
    if not sk_dir or not os.path.isdir(sk_dir):
        return ""

    # 优先读 .md 文件，按轮次号（_rN）降序取最新
    import re
    candidates = []
    try:
        for fn in os.listdir(sk_dir):
            if fn.endswith(".md"):
                p = os.path.join(sk_dir, fn)
                m = re.search(r"_r(\d+)\.md$", fn)
                rnum = int(m.group(1)) if m else 0
                candidates.append((rnum, os.path.getmtime(p), p))
    except Exception:
        return ""
    if not candidates:
        return ""
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    latest = candidates[0][2]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return ""
    # 取报告后半部分（增量：方案/结论/发现），而非开头（固定的"总设计→目标→现状"）。
    # 开头是 force_design 写的固定框架，每轮不变，取开头会导致每轮注入相同摘要，
    # 迭代退化成重复生成相同内容（实测 r1=r2=r3 md5 完全相同）。
    if len(text) > max_chars:
        head = text[:400]                          # 保留开头 400 字作上下文
        tail = text[-(max_chars - 400):]           # 主要取后半部分（增量）
        text = head + "\n…(中间省略)…\n" + tail
    return text


class ResearchLoopState:
    """单个实例的循环状态，不持久化（重启重置）。"""

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self.round = 0
        self.no_output_streak = 0               # 连续无产出轮次
        self.workspace = ""                     # 实例 workspace 路径
        self.last_event_types: list[str] = []
        self.last_outputs: list[str] = []
        self.parent_user_request: str = ""
        self.active = False


# ── 全局状态（按 instance_id 索引） ──
_states: dict[str, ResearchLoopState] = {}


def get_state(instance_id: str) -> ResearchLoopState:
    if instance_id not in _states:
        _states[instance_id] = ResearchLoopState(instance_id)
    return _states[instance_id]


# ── 主入口：task 完成时调用 ───────────────────────────

async def on_task_done(
    *,
    instance_id: str,
    title: str,
    user_request: str,
    workspace: str = "",
    parsed: dict[str, Any] | None = None,
    files: list[str] | None = None,
    event_types: list[str] | None = None,
    enqueue_fn,   # callable(title, user_request, parent_request)
    notify_fn,    # callable(message) → 通知用户
    adapter=None,  # LLM adapter，用于生成有增量的下一步任务
) -> bool:
    """在 _handle_stop_project 完成后调用。

    Returns:
        True 如果 enqueued 了下一个任务，False 如果停止。
    """
    state = get_state(instance_id)

    # 首次调用 → 判断任务类型是否适合循环
    if not state.active:
        if not should_loop(user_request):
            logger.info("[RESEARCH_LOOP] %s 任务类型不循环（一次性/非研究），跳过自主循环", instance_id)
            return False
        state.active = True
        state.round = 0
        state.parent_user_request = user_request
        state.workspace = workspace

    state.round += 1
    state.last_event_types = (state.last_event_types or []) + (event_types or [])
    state.last_outputs.append(list(files or []))

    logger.info(
        "[RESEARCH_LOOP] %s round=%d/%d events=%s files=%s",
        instance_id, state.round, MAX_ROUNDS,
        event_types, files,
    )

    # ── Gate 1: 轮次限制 ──
    if state.round >= MAX_ROUNDS:
        logger.info("[RESEARCH_LOOP] %s max rounds reached, stopping", instance_id)
        await _stop(state, notify_fn)
        return False

    # ── Gate 2: 多样性检查 ──
    if not _check_diversity(state):
        logger.info("[RESEARCH_LOOP] %s diversity gate failed, stopping", instance_id)
        await _stop(state, notify_fn)
        return False

    # ── Gate 3: 产出质量检查 ──
    if instance_id in OUTPUT_REQUIRED_TYPES:
        if not _has_output_this_round(state):
            state.no_output_streak += 1
            logger.info("[RESEARCH_LOOP] %s no output streak=%d/2", instance_id, state.no_output_streak)
            if state.no_output_streak >= 2:
                logger.info("[RESEARCH_LOOP] %s 2 consecutive no-output, stopping", instance_id)
                await _stop(state, notify_fn)
                return False
            next_task = _generate_fix_task(state, parsed, user_request)
            logger.info("[RESEARCH_LOOP] %s fix task: %s", instance_id, next_task)
        else:
            state.no_output_streak = 0
            archive_outputs(instance_id, workspace, files or [], round_num=state.round)
            next_task = await _generate_next_task(state, parsed, title, user_request, adapter)
    else:
        next_task = await _generate_next_task(state, parsed, title, user_request, adapter)

    if not next_task:
        await _stop(state, notify_fn)
        return False

    await enqueue_fn(
        next_task["title"],
        next_task["user_request"],
        state.parent_user_request,
    )
    logger.info("[RESEARCH_LOOP] %s enqueued: %s", instance_id, next_task.get("title", "")[:60])
    logger.info("[RESEARCH_LOOP] %s returning True", instance_id)
    return True


def reset(instance_id: str) -> None:
    """手动重置循环状态（用户发新消息时调用）。"""
    if instance_id in _states:
        _states[instance_id].active = False
        _states[instance_id].round = 0


# ── Gate 检查 ────────────────────────────────────────

def _check_diversity(state: ResearchLoopState) -> bool:
    """检查最近的事件类型是否过于单一。

    如果最近的 event_types 全是同一类型（如全是 read_file），
    说明陷入了重复模式，应停止。
    """
    recent = state.last_event_types[-MAX_SAME_TYPE:]
    if len(recent) < MAX_SAME_TYPE:
        return True
    # 检查最后 MAX_SAME_TYPE 轮是否有不同的类型
    unique = set(recent)
    if len(unique) == 1:
        only_type = list(unique)[0]
        logger.warning("[RESEARCH_LOOP] diversity: last %d rounds all '%s'", MAX_SAME_TYPE, only_type)
        return False
    return True


def _has_output_this_round(state: ResearchLoopState) -> bool:
    """检查本轮是否有实际产出文件。"""
    # 最后一轮的 files
    if not state.last_outputs:
        return False
    last_files = state.last_outputs[-1] if isinstance(state.last_outputs[-1], list) else []
    return bool(last_files)


# ── 任务生成 ──────────────────────────────────────────

async def _generate_next_task(
    state: ResearchLoopState,
    parsed: dict | None,
    title: str,
    user_request: str,
    adapter=None,
) -> dict | None:
    """根据上一轮成果生成**具体的、有增量的**下一步任务。

    优先用 LLM 基于上一轮成果（后半部分的增量内容）生成具体的下一步，
    避免固定模板导致的"每轮重复生成相同内容"（实测 r1=r2=r3 md5 完全相同）。
    """
    prior = load_latest_knowledge(state.instance_id, getattr(state, "workspace", ""))
    if prior:
        logger.info("[RESEARCH_LOOP] %s injecting prior knowledge (%d chars)", state.instance_id, len(prior))
    else:
        logger.info("[RESEARCH_LOOP] %s no prior knowledge to inject", state.instance_id)

    # ── LLM 生成具体的下一步（有增量）──
    if adapter is not None and prior:
        prompt = (
            "你是自主研究循环的规划者。基于上一轮成果，生成一个具体的、有增量的下一步任务。\n\n"
            f"原始任务：{user_request}\n\n"
            f"【上一轮成果（核心内容）】\n{prior}\n\n"
            "要求：\n"
            "1. 总结上一轮已完成什么、得出什么结论\n"
            "2. 指出尚未深入、尚未解决、可进一步展开的具体方向\n"
            "3. 生成一个具体的下一步任务指令——禁止泛泛的\"继续分析/继续研究\"，必须具体到对象和问题"
            "（例如\"深入分析 executor.py 的事件循环依赖\"、\"对比 A 与 B 的实现\"、\"验证上一轮提出的 X 假设\"）\n"
            "4. 说明这一步要产出什么文件\n\n"
            "只输出任务指令本身（中文，150字以内的一段话，将直接作为下一步任务执行）。"
        )
        try:
            import asyncio as _asyncio
            next_req = await _asyncio.to_thread(adapter.chat, prompt, None, "classify")
            next_req = (next_req or "").strip()
            if len(next_req) >= 20:
                logger.info("[RESEARCH_LOOP] %s LLM-generated next task (%d chars): %s",
                            state.instance_id, len(next_req), next_req[:60])
                return {"title": f"{title}_r{state.round}", "user_request": next_req}
            logger.warning("[RESEARCH_LOOP] %s LLM next task too short, fallback to template", state.instance_id)
        except Exception as exc:
            logger.warning("[RESEARCH_LOOP] %s LLM next-task generation failed, fallback: %s", state.instance_id, exc)

    # ── Fallback：固定模板（LLM 不可用 / prior 为空时）──
    role = INSTANCE_ROLES.get(state.instance_id, "general")
    if role == "desktop_monitor":
        task = "继续桌面监控：再次截图，并与上一轮对比是否有变化。产出变化报告。"
    elif role == "deep_research":
        task = f"继续深度研究（第{state.round}轮）：基于上一轮分析结果，尝试运行代码、对比结果、深入分析。产出更新的研究报告。"
    elif role in ("code_analysis", "knowledge_acquisition"):
        task = f"继续代码分析（第{state.round}轮）：分析上一轮未覆盖的模块，产出更完整的分析报告。"
    elif role == "tool_testing":
        task = f"继续工具测试（第{state.round}轮）：对上一轮发现的工具进行更深入的 benchmark 测试。产出测试报告。"
    else:
        task = f"继续上一轮任务（第{state.round}轮）：深入分析，产出报告。"

    if prior:
        task += f"\n\n【上一轮成果摘要（供参考，请在其基础上深入而非重复）】\n{prior}"

    return {
        "title": f"{title}_r{state.round}",
        "user_request": task,
    }


def _generate_fix_task(
    state: ResearchLoopState,
    parsed: dict | None,
    user_request: str,
) -> dict | None:
    """上一轮没有产出文件 → 生成补救任务。"""
    # Use original parent request, don't accumulate
    task = f"请确保产出 .md 或 .png 文件到当前工作目录。原始任务：{state.parent_user_request[:200]}"
    return {
        "title": f"{state.parent_user_request[:40]}_fix_r{state.round}",
        "user_request": task,
    }


async def _stop(state: ResearchLoopState, notify_fn) -> None:
    """停止循环并通知用户。"""
    state.active = False
    try:
        await notify_fn(
            f"📋 {state.instance_id} 自主研究循环完成（{state.round}/{MAX_ROUNDS} 轮）"
        )
    except Exception as exc:
        logger.debug("[RESEARCH_LOOP] notify failed: %s", exc)
