"""Prompt Builder — dynamically assembles planning prompts from context.

No hardcoded task types. The prompt is built at runtime using:
  - User message
  - Available skills/events
  - User habits
  - Historical experiences
  - Growth milestones
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_OUTPUT_FORM_PRINCIPLES = """
## 输出形式选择原则
- 如果用户明确要求"表格"、"文件"、"报告"、"保存"、"导出"，则需要生成文件，并设置 `expected_artifacts` 为非空数组。
- 如果用户只是询问信息、建议、解释，没有要求保存成文件，则**不需要生成文件**，设置 `expected_artifacts` 为空数组。
- 当你不确定时，优先选择纯文本回复。
"""


def load_habits_from_db() -> str:
    """Load user habits from SQLite for prompt injection."""
    try:
        from ..meta.learning import load_habits as _load_habits
        habits = _load_habits()
        items = []
        items.append("### 用户偏好")
        if habits.get("prefer_pdf"):
            items.append("- 用户优先接收 PDF 格式的正式报告")
        if habits.get("preferred_language") == "zh":
            items.append("- 使用中文输出")
        if habits.get("avoid_web_search"):
            items.append("- 禁止使用 web_search：经验表明 web_search 对本环境不可靠，会超时。改为用 generate_code + create_file + run_command 三步链完成需要数据获取的任务")
        output_prefs = habits.get("output_preferences", {})
        if output_prefs:
            items.append("")
            items.append("### 动态输出偏好（基于用户反馈学习）")
            for qtype, pref in output_prefs.items():
                pref_label = {
                    "text": "纯文字回复",
                    "file": "生成文件",
                    "table": "表格/CSV",
                    "chart": "图表/图片",
                }.get(str(pref).lower(), str(pref))
                items.append(f"- 对于「{qtype}」类查询，用户偏好：{pref_label}")
        return "\n".join(items) if items else "### 用户偏好\n- 暂无特殊偏好"
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to load habits: %s", exc)
        return "### 用户偏好\n- 暂无特殊偏好"


def load_experiences_from_db(task_message: str, max_experiences: int = 3) -> str:
    """Load relevant experiences from SQLite."""
    try:
        from ..meta.learning import format_experiences_for_prompt
        result = format_experiences_for_prompt(task_message, max_experiences=max_experiences)
        return result if result else "### 相关经验\n- 暂无相关经验"
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to load experiences: %s", exc)
        return "### 相关经验\n- 暂无相关经验"


def load_growth_for_prompt(user_id: str = "default", max_events: int = 3) -> str:
    """Load growth milestones."""
    try:
        from ..meta.learning import format_growth_for_prompt
        result = format_growth_for_prompt(user_id=user_id, max_events=max_events)
        return result if result else ""
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to load growth: %s", exc)
        return ""


def build_available_agents_section() -> str:
    """Build an 'available agents' section for the planner prompt.

    Scans AgentRegistry for all registered agents and renders their
    capabilities so the planner can choose specialized agents.

    Only agents that pass health check (status "ok" or "unknown") are
    included — this prevents the planner from selecting agents that are
    registered but not actually installed (e.g. bamboo-ai).
    """
    try:
        from ..agents.registry import AgentRegistry
        from ..agents.manifest import AgentManifest

        registry = AgentRegistry(workspace=None)
        agents = registry.list_agents()

        # Health-check filter: skip agents that are not installed/available
        healthy_agents = []
        for m in agents:
            try:
                hc = registry.health_check(m.name)
                if hc.get("status") in ("ok", "unknown"):
                    healthy_agents.append(m)
                else:
                    logger.warning(
                        "[PROMPT_BUILDER] excluding agent '%s' — health check: %s (%s)",
                        m.name, hc.get("status"), hc.get("details", ""),
                    )
            except Exception as exc:
                logger.warning(
                    "[PROMPT_BUILDER] health check failed for agent '%s': %s",
                    m.name, exc,
                )

        lines = []
        for m in healthy_agents:
            cat = m.endpoint_config.get("category", "general")
            cat_str = "【专用 Agent】" if cat == "specialized" else "【通用 Agent】"
            desc = m.endpoint_config.get("description_for_planner") or m.description
            caps = ", ".join(m.capabilities[:6])
            lines.append(f"- {cat_str} `{m.name}`: {desc}")
            lines.append(f"  能力: {caps}")
            if cat == "specialized":
                lines.append(f"  调用方式: call_agent_skill(agent=\"{m.name}\", task=..., parameters={{input=..., output=..., question=..., device=...}})")
        if not lines:
            return ""
        result = "### 可用 Agent\n" + "\n".join(lines) + "\n"
        # Add instruction: call specialized agents directly, no pre-steps
        has_specialized = any(
            m.endpoint_config.get("category") == "specialized" for m in healthy_agents
        )
        if has_specialized:
            result += """
### 重要：调用专用 Agent 的规范
- 如果用户的请求适合某个【专用 Agent】的能力范围，**直接使用 call_agent_skill 调用该 Agent**，不需要先用其他步骤（inspect_file、smart_llm_structured_action 等）预处理。
- 专用 Agent 内部会自己处理输入文件读取、分析、报告生成。
- 举例：用户要求"用 cytobridge 分析 data.h5ad 的轨迹"，正确做法是单步：call_agent_skill(agent="cytobridge", task="分析 data.h5ad 的轨迹推断", parameters={input: "data.h5ad", output: "./output", question: "轨迹推断"})
- 禁止在 call_agent_skill 之前增加 atomic_inspect_file、smart_llm_structured_action 等多余步骤。
"""
        return result
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to list agents: %s", exc)
        return ""


def build_prompt(
    user_message: str,
    available_events: str = "",
    habits: str | None = None,
    experiences: str | None = None,
    growth: str | None = None,
    working_dir: str = "",
    expected_artifacts: list | None = None,
    min_steps: int = 2,
    max_steps: int = 8,
    event_type: str = "",
    available_agents: str = "",
) -> str:
    """Build a dynamic planner prompt with experience/habits/growth context."""
    if habits is None:
        habits = load_habits_from_db()
    if experiences is None:
        experiences = load_experiences_from_db(user_message)
    if growth is None:
        growth = load_growth_for_prompt()

    arts = json.dumps(expected_artifacts or [], ensure_ascii=False)

    context_blocks = []
    _debug_ctx = []
    if habits and habits.strip() and "暂无特殊偏好" not in habits:
        context_blocks.append(habits)
        _debug_ctx.append("habits")
    if experiences and experiences.strip() and "暂无相关经验" not in experiences:
        context_blocks.append(experiences)
        _debug_ctx.append("experiences")
    if growth and growth.strip():
        context_blocks.append(growth)
        _debug_ctx.append("growth")
    if _debug_ctx:
        logger.info("[PROMPT_BUILDER] injected context: %s", ", ".join(_debug_ctx))
    context_section = "\n\n".join(context_blocks) if context_blocks else ""
    if context_section:
        context_section = f"\n\n## 上下文知识\n\n{context_section}"

    # Event-specific planning guidance
    event_guidance = ""
    et = event_type.strip().lower()
    import logging
    logging.getLogger(__name__).debug(f"[PROMPT_BUILDER] event_type={et!r}")
    if et == "literature_review":
        event_guidance = (
            "\n\n## literature_review 事件规划指南\n"
            "当前任务是文献综述/方法整理。计划必须包含以下步骤类型组合，每个步骤使用指定的 event_type：\n"
            "1. 搜索步骤：event_type=call_agent_skill，用来搜索文献和方法\n"
            "2. 提取整理步骤：event_type=smart_llm_structured_action，用来提取和结构化整理上一步的结果\n"
            "3. 分析步骤：event_type=smart_llm_structured_action，用来对比分析和总结\n"
            "4. 文件产出步骤：event_type=atomic_write_artifact，用来生成最终综述文件\n"
            "步骤数量至少 4 步，最终步骤必须是 atomic_write_artifact 来输出文件。\n"
            "\n"
            "## 步骤参数书写规范（重要）\n"
            "atomic_write_artifact 的 content 参数必须写成 $step_X.result.content 格式引用上一步的输出：\n"
            "- 正确示例：{\"filename\": \"review.md\", \"content\": \"$step_2.result.content\"}\n"
            "- 禁止把指令性文字写在 content 参数中\n"
            "smart_llm_structured_action 的 parameters.inputs.query 参数写执行指令。"
        )
    elif et == "data_analysis":
        event_guidance = (
            "\n\n## data_analysis 事件规划指南\n"
            "计划应包含：读取数据、统计分析、生成可视化图表。"
        )
    elif et == "web_search":
        event_guidance = (
            "\n\n## web_search 事件规划指南\n"
            "计划应包含：搜索关键词、获取内容、整理结果。"
        )
    elif et == "batch_plan":
        event_guidance = (
            "\n\n## batch_plan 事件规划指南（必须遵守，违反会导致步骤失败）\n"
            "\n"
            "### 通用 Event 说明\n"
            "新的事件系统按执行方式分为三类：\n"
            "- **local** 事件：本地确定性执行（read_file, create_file, transform, filter, sort, run_command, download_file 等）\n"
            "- **agent** 事件：转发给外部 Agent（web_search, summarize, analyze, generate_text, generate_code, write_report, create_diagram）\n"
            "- **llm** 事件：调用 LLM 处理（extract, if_condition, switch, validate, check_quality, audit, compare）\n"
            "\n"
            "### 事件选择建议\n"
            "- 搜索信息 → web_search (agent)\n"
            "- 读取文件 → read_file (local)\n"
            "- 生成文本/文章 → generate_text (agent)\n"
            "- 生成代码 → generate_code (agent)；注意 generate_code 只返回代码文本，不写入文件。要执行代码必须在其后加 create_file(写入.py) 再 run_command(python3 该文件)\n"
            "- 写报告 → write_report (agent)\n"
            "- 画图表/流程图 → create_diagram (agent)\n"
            "- 数据分析 → analyze (agent) 或 extract (llm) 提取结构化字段\n"
            "- 摘要 → summarize (agent)\n"
            "- 格式转换 → transform (local)\n"
            "- 创建文件 → create_file (local) 或 atomic_write_artifact (local, 旧名)\n"
            "- 运行命令 → run_command (local)\n"
            "- 下载文件 → download_file (local)\n"
            "- 对比 → compare (llm)\n"
            "- 验证 → validate (llm)\n"
            "- 条件分支 → if_condition (llm)\n"
            "\n"
            "### 专用 Agent 自动安装\n"
            "- 当用户要求使用某个专用 Agent（如 cytobridge）时，生成两步：先 atomic_ensure_agent_installed(agent=\"cytobridge\") 检查+安装，再 call_agent_skill(agent=\"cytobridge\", task=..., parameters={{...}}) 真实调用。\n"
            "- atomic_ensure_agent_installed 会在 Agent 已安装时直接返回成功（不重复安装），仅在首次或 force_reinstall=true 时执行下载和安装。\n"
            "- 不要合并成一步。安装和调用必须分开成两个步骤，以防安装耗时过长阻塞调用。\n"
            "\n"
            "\n"
            "### 核心规则：规划阶段只出指令，执行阶段才生成内容\n"
            "\n"
            "规划阶段的输出是步骤指令（event_type + parameters），不是内容本身。\n"
            "所有自然语言内容（报告文字、分析结论、代码脚本）必须在执行阶段由 LLM 生成。\n"
            "\n"
            "### 内容生成规范\n"
            "- **禁止在 atomic_write_artifact 的 content 参数中写任何自然语言内容。** 这是最常见且最严重的问题。\n"
            "- content 参数只能写 $step_X.result.content 格式引用。\n"
            "- 凡是需要生成文字、脚本、代码的，必须先安排 smart_llm_structured_action 步骤让 LLM 生成完整内容。\n"
            "  - 正确：\n"
            "    step1 = smart_llm_structured_action(prompt=\"生成一份关于主题X的完整中文报告...\")\n"
            "    step2 = atomic_write_artifact(content=\"$step1.result.content\")\n"
            "  - 错误（被拒绝）：\n"
            "    step1 = atomic_write_artifact(content=\"# 标题\\n\\n正文内容...\")  ← 内联内容\n"
            "    step1 = atomic_write_artifact(content=\"[完整报告内容]\")  ← 占位内容\n"
            "\n"
            "### 交付形式由用户意图决定\n"
            "- 如果用户要求**绘图/图表/可视化**：安排 smart_llm_structured_action 绘图指令步骤即可\n"
            "- 如果用户要求**PDF/文件/表格/CSV/导出**：安排 atomic_write_artifact 步骤写入对应格式\n"
            "- 如果用户没指定格式但偏好 PDF：加 atomic_write_artifact 写 .md 报告 + atomic_convert_md_to_pdf 转 PDF\n"
            "- 如果用户没指定格式且不偏好 PDF：不需要写文件，不要强制安排写文件或转 PDF 步骤\n"
        )

    # Build available agents section
    if not available_agents:
        try:
            available_agents = build_available_agents_section()
        except Exception:
            available_agents = ""

    prompt = f"""你是 Partner 的任务规划器。用户要求：{user_message[:2000]}

可用操作（只能使用这些）：
{available_events or "call_agent_skill, atomic_write_artifact"}{available_agents}{context_section}{event_guidance}

工作目录：{working_dir}
预期产物：{arts}

{_OUTPUT_FORM_PRINCIPLES}

重要：如果用户明确要求使用某个专用 Agent（如 cytobridge、docking-agent 等），
你必须使用 call_agent_skill(agent="agent_name", task=..., parameters={{...}}) 来调用该 Agent。
如果该专用 Agent 可能尚未安装，先用 atomic_ensure_agent_installed(agent="agent_name") 确保安装完成，再用 call_agent_skill 调用。
不要用 web_search、smart_llm_structured_action 或 run_command 替代专用 Agent 的功能。
调用专用 Agent 后，用 smart_llm_structured_action 或 atomic_write_artifact 处理其结果。

请生成一个 JSON 格式的 MicroPlan。严格遵守以下 JSON 语法规则：
- 每两个相邻的数组元素或字典属性之间必须有逗号
- 字符串必须用双引号，不能用单引号
- 最后一个元素后面不能有尾部逗号
- 不要包含任何注释
- 只输出JSON，不要额外文本

输出格式（严格遵循此结构）：
{{"plan": [{{"id": "step1", "event_type": "...", "parameters": {{...}}, "depends_on": []}}], "expected_artifacts": []}}

最终输出的 JSON 必须能被 Python `json.loads()` 正确解析。"""
    import logging
    if event_guidance:
        logging.getLogger(__name__).debug(f"[PROMPT_GUIDANCE] present for {et}: {len(event_guidance)} chars")
    return prompt
