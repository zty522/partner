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
import os

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
    """Load relevant experiences from SQLite with hierarchical per-task-type analysis.
    
    Injects not just individual experiences, but aggregated stats:
    - Success rate for this task type
    - Recommended agent (based on historical stats)
    - Common failure patterns
    """
    try:
        from ..meta.learning import format_experiences_for_prompt as _fmt_old
        from ..meta.learning import get_relevant_experiences, get_experience_stats
        
        # First, get per-task-type stats from experience DB
        stats = get_experience_stats()
        
        # Extract task type from message (first meaningful keywords)
        task_type = _extract_task_type(task_message)
        
        lines = []
        
        # Section: aggregate stats
        total = stats.get("total", 0)
        successes = stats.get("successes", 0)
        by_agent = stats.get("by_agent", [])
        
        # Import task-type query helper
        from ..meta.learning import get_experiences_by_task_type as _get_by_type
        
        if total >= 3:
            success_rate = successes / max(total, 1)
            lines.append(f"## 历史经验参考（基于 {total} 次历史任务）")
            lines.append(f"- 总体成功率：{success_rate:.0%}（{successes}/{total} 成功）")
            
            # Recommend best agent for this task type
            if task_type and by_agent:
                # Find agents that match this task type
                type_exps = _get_by_type(task_type, limit=30)
                if type_exps:
                    agent_stats = {}
                    for exp in type_exps:
                        agent = str(exp.get("agent_used") or "hermes").strip()
                        if agent not in agent_stats:
                            agent_stats[agent] = {"total": 0, "ok": 0}
                        agent_stats[agent]["total"] += 1
                        if exp.get("success"):
                            agent_stats[agent]["ok"] += 1
                    
                    best_agent = None
                    best_rate = 0
                    best_count = 0
                    for agent, ag_stats in agent_stats.items():
                        if ag_stats["total"] >= 2:
                            rate = ag_stats["ok"] / ag_stats["total"]
                            if rate > best_rate or (rate == best_rate and ag_stats["total"] > best_count):
                                best_agent = agent
                                best_rate = rate
                                best_count = ag_stats["total"]
                    
                    if best_agent:
                        lines.append(f"- 推荐 Agent：「{best_agent}」（{best_count} 次选择，成功率 {best_rate:.0%}）")
            
            # Common failure patterns
            failure_exps = [e for e in _get_by_type(task_type, limit=50) if not e.get("success")]
            if failure_exps and len(failure_exps) >= 2:
                lines.append(f"- 常见失败模式：同类任务中 {len(failure_exps)} 次失败")
                # Sample failure reasons
                failure_reasons = set()
                for fe in failure_exps[:5]:
                    summary = str(fe.get("task_summary") or "")[:60]
                    if summary:
                        failure_reasons.add(summary)
                if failure_reasons:
                    for reason in list(failure_reasons)[:3]:
                        lines.append(f"  - {reason}")
            
            lines.append("")
        
        # Section: individual relevant experiences (existing format)
        old_fmt = _fmt_old(task_message, max_experiences=max_experiences)
        if old_fmt:
            lines.append(old_fmt)
        
        return "\n".join(lines) if lines else ""
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to load experiences: %s", exc)
        return "### 相关经验\n- 暂无相关经验"


def load_rules_for_prompt(workspace_root: str = "", goal_text: str = "") -> str:
    """Load active Rules layer rules for prompt injection.

    Uses the new partner.rules module (P1 - Rules System).
    """
    try:
        from ..rules.rule_injector import RuleInjector
        from ..rules.rule_loader import RuleLoader
        loader = RuleLoader(workspace_root=workspace_root)
        injector = RuleInjector(loader)
        block = injector.inject_rules_block(goal=goal_text)
        return block if block else ""
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to load rules: %s", exc)
        return ""


def load_evolution_rules_from_db(max_rules: int = 2, goal_text: str | None = None) -> str:
    """Load self-evolution rules for prompt injection.

    Injects learned patterns about agent selection, failure avoidance,
    and output preferences derived from execution history.
    Only injects rules relevant to the current task goal.
    """
    try:
        from ..evolution.behavior_tuner import format_rules_for_prompt
        result = format_rules_for_prompt(max_rules=max_rules, goal_text=goal_text)
        return result if result else ""
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to load evolution rules: %s", exc)
        return ""


def load_growth_for_prompt(user_id: str = "default", max_events: int = 1) -> str:
    """Load growth milestones + capability trend."""
    parts = []
    try:
        from ..meta.learning import format_growth_for_prompt
        result = format_growth_for_prompt(user_id=user_id, max_events=max_events)
        if result:
            parts.append(result)
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to load growth: %s", exc)
    
    # Append capability trend
    try:
        from ..meta.learning import generate_capability_trend
        trend = generate_capability_trend()
        if trend:
            parts.append(trend)
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to load capability trend: %s", exc)
    
    return "\n".join(parts) if parts else ""


AGENT_BLOCKLIST = {"pocketflow", "bioinformatics", "bionemo"}

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
        # Use simple import check instead of subprocess health checks (too slow)
        healthy_agents = []
        for m in agents:
            try:
                # Simple module check - fast, no subprocess
                import importlib
                exec_module = m.endpoint_config.get("exec_module", "")
                if exec_module:
                    importlib.import_module(exec_module)
                healthy_agents.append(m)
            except ImportError:
                pass  # Module not installed, skip
            except Exception:
                healthy_agents.append(m)  # Other errors, include anyway

        lines = []
        # Limit to top 30 agents to keep prompt size manageable
        # Prioritize specialized agents first, then alphabetically
        specialized = [m for m in healthy_agents if m.endpoint_config.get("category") == "specialized"]
        general = [m for m in healthy_agents if m.endpoint_config.get("category") != "specialized"]
        display_agents = specialized + sorted(general, key=lambda m: m.name)[:max(0, 30 - len(specialized))]
        skipped = len(healthy_agents) - len(display_agents)
        
        for m in display_agents:
            cat = m.endpoint_config.get("category", "general")
            cat_str = "【专用 Agent】" if cat == "specialized" else "【通用 Agent】"
            desc = m.endpoint_config.get("description_for_planner") or m.description
            caps = ", ".join(m.capabilities[:6])
            lines.append(f"- {cat_str} `{m.name}`: {desc}")
            lines.append(f"  能力标签: {caps}")
            
            # ── Agent/Tool profile (rich metadata for planner) ──
            is_tool = getattr(m, 'is_tool', False)
            is_agent = getattr(m, 'is_agent', False)
            profile = getattr(m, 'tool_profile', None) or getattr(m, 'agent_profile', None) or {}
            if is_tool:
                lines.append(f"  ⚠️ 这是一个工具（非LLM Agent），只能执行固定逻辑，不能自主推理")
            
            if profile:
                atype = profile.get('type', '')
                if atype:
                    type_cn = {
                        "knowledge_lookup": "知识检索工具",
                        "knowledge_query": "知识查询", 
                        "data_analysis": "数据分析Agent",
                        "llm_agent": "LLM Agent"
                    }.get(atype, atype)
                    lines.append(f"  类型: {type_cn}")
                
                # Expected outputs
                outputs = profile.get('expected_outputs', [])
                if outputs:
                    out_strs = [f"{o['format']}({o.get('description','')})" for o in outputs[:5]]
                    lines.append(f"  预期产出: {', '.join(out_strs)}")
                
                # Expected runtime  
                runtime = profile.get('expected_runtime', {})
                if runtime:
                    typical = runtime.get('typical_seconds', 0)
                    if typical > 60:
                        lines.append(f"  预计耗时: ~{typical//60} 分钟")
                    else:
                        lines.append(f"  预计耗时: ~{typical} 秒")
                
                # Task constraints
                constraints = profile.get('task_constraints', {})
                suitable = constraints.get('suitable_for', [])
                not_suitable = constraints.get('not_suitable_for', [])
                if suitable:
                    lines.append(f"  适用: {'; '.join(suitable[:4])}")
                if not_suitable:
                    lines.append(f"  不适用: {'; '.join(not_suitable[:3])}")
                
                # Param convention
                pconv = profile.get('param_convention', {})
                if pconv:
                    use_params = pconv.get('use', [])
                    dont_use = pconv.get('do_not_use', [])
                    note = pconv.get('note', '')
                    if use_params:
                        lines.append(f"  必需参数: {', '.join(use_params)}")
                    if dont_use:
                        lines.append(f"  禁止参数: {', '.join(dont_use)}")
                    if note:
                        lines.append(f"  注意: {note}")
            elif cat == "specialized":
                lines.append(f"  调用方式: call_agent_skill(agent=\"{m.name}\", task=..., parameters={{input=..., output=..., question=..., device=...}})")
        if not lines:
            return ""
        result = "### 可用 Agent\n" + "\n".join(lines)
        if skipped > 0:
            result += f"\n（还有 {skipped} 个通用 Agent 因数量限制未列出，如需特定工具可直接指定名称）\n"
        result += "\n"
        # Add instruction: call specialized agents directly, no pre-steps
        has_specialized = any(
            m.endpoint_config.get("category") == "specialized" for m in healthy_agents
        )
        if has_specialized:
            result += """
### 重要：调用专用 Agent 的规范
- 如果用户的请求适合某个【专用 Agent】的能力范围，**直接使用 call_agent_skill 调用该 Agent**，不需要先用其他步骤（inspect_file、smart_llm_structured_action 等）预处理。
- 专用 Agent 内部会自己处理输入文件读取、分析、报告生成。
- 禁止在 call_agent_skill 之前增加 atomic_inspect_file、smart_llm_structured_action 等多余步骤。

#### Agent 参数规范（极其重要！）
不同 Agent 类型的 parameters 不同，**禁止编造不存在的文件路径作为 input**：

- **数据分析 Agent**（cytobridge、scanpy 等，需要 h5ad/csv 输入文件）：
  正确: parameters={input: "/真实/文件路径.h5ad", output: "./output", question: "分析任务描述"}
  注意: input 必须是真实存在的文件路径，不要编造！

- **分子生成工具**（pocketflow 等，输入PDB输出分子）：
  正确: parameters={input: "/真实/口袋.pdb", output: "./output", num_gen: 100, device: "cpu"}
  input 必须是真实存在的PDB文件路径，output 是输出目录。

- **通用 LLM Agent**（hermes、openclaw 等）：
  正确: parameters={task: "任务描述"}  (不需要 input/output/question)

#### 任务复杂度控制
- 分子生成工具（非 LLM）直接传入PDB文件+参数即可，不要分配复杂的多步分析任务
- 数据分析 Agent（自带 LLM）可以处理复杂任务，但 task 描述应简洁（1-2句话）
- LLM Agent 可以处理多步推理任务
"""
        # ── BioNeMo 专用说明 ──
        bionemo_names = {m.name for m in healthy_agents if "bionemo" in m.name.lower()}
        if any("bionemo" in n for n in bionemo_names):
            result += """
### ⚗️ BioNeMo 科学计算（特殊说明）
BioNeMo 是 NVIDIA 科学计算平台，内部包含多个模型。通过 task_map 自动选择模型：
- **分子生成** → model="molmim", 参数: smiles="种子SMILES", num_samples=20
- **分子对接** → model="diffdock", 参数: ligand="配体.sdf", protein="受体.pdb", poses=20
- **蛋白质折叠** → model="esmfold", 参数: sequence="氨基酸序列"
- **高精度折叠** → model="openfold", 参数: sequence="...", msas=["file.a3m"], use_msa=True
- **蛋白质嵌入** → model="esm2", 参数: sequences=["seq1","seq2"], model_size="650m"|"3b"|"15b"

参数传递: call_agent_skill(agent="bionemo", task="分子生成", parameters={{model: "molmim", smiles: "CCCC", num_samples: 10}})
"""

        # ── Bioinformatics 专用说明 ──
        bio_names = {m.name for m in healthy_agents if "bioinformatics" in m.name.lower()}
        if any("bioinformatics" in n for n in bio_names):
            result += """
### 🧬 开源生信工具集（特殊说明）
全部免费开源，不需要 API Key。内置工具映射：
- **分子操作** → tool="molecule", op="validate/fingerprint/similarity/properties/convert", smiles="...", op 可自动推断
- **序列分析** → tool="sequence", op="translate/gc/revcomp", sequence="DNA序列"
- **多序列比对** → tool="alignment", op="muscle/mafft", input="FASTA文件"
- **序列搜索** → tool="blast", op="blastp/blastn", query="序列", db="数据库路径"

直接 call_agent_skill(agent="bioinformatics", task="验证SMILES", parameters={{tool: "molecule", smiles: "CCCO"}})
"""

        return result
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to list agents: %s", exc)
        return ""


def load_procedural_memory(workspace_root: str, task_message: str) -> str:
    """Load procedural memory from ProcMEM store."""
    try:
        from ..evolution.procedural_memory import ProceduralMemory
        pm = ProceduralMemory(workspace_root)
        # Determine task_type from message keywords
        task_type = "general"
        msg_lower = task_message.lower()
        if any(kw in msg_lower for kw in ['trajectory', '单细胞', 'paga', 'dpt', 'rna']):
            task_type = "trajectory_inference"
        elif any(kw in msg_lower for kw in ['分子生成', 'pocketflow', 'docking', 'protein']):
            task_type = "molecule_generation"
        elif any(kw in msg_lower for kw in ['文献', 'paper', 'review', '分析']):
            task_type = "literature_review"
        memories = pm.retrieve(task_type, {"text": task_message[:200]})
        if memories:
            return pm.format_for_prompt(memories)
        return ""
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to load procedural memory: %s", exc)
        return ""


def load_layered_rules(workspace_root: str) -> str:
    """Load layered context rules from workspace rules/ directory."""
    try:
        from ..context.layered_rules import LayeredRules
        lr = LayeredRules(workspace_root)
        return lr.format_for_prompt(max_rules=8)
    except Exception as exc:
        logger.debug("[PROMPT_BUILDER] failed to load layered rules: %s", exc)
        return ""


def build_prompt(
    user_message: str,
    available_events: str = "",
    habits: str | None = None,
    experiences: str | None = None,
    growth: str | None = None,
    evolution_rules: str | None = None,
    rules: str | None = None,
    working_dir: str = "",
    expected_artifacts: list | None = None,
    min_steps: int = 2,
    max_steps: int = 8,
    event_type: str = "",
    available_agents: str = "",
    workspace_root: str = "",
) -> str:
    """Build a dynamic planner prompt with experience/habits/growth/evolution context."""
    if habits is None:
        habits = load_habits_from_db()
    if experiences is None:
        experiences = load_experiences_from_db(user_message)
    if growth is None:
        growth = load_growth_for_prompt()
    if evolution_rules is None:
        evolution_rules = load_evolution_rules_from_db(goal_text=user_message)
    if rules is None:
        rules = load_rules_for_prompt(workspace_root=workspace_root, goal_text=user_message)
    # Load procedural memory and layered rules
    proc_mem = load_procedural_memory(workspace_root, user_message)
    layered_rules = load_layered_rules(workspace_root)

    arts = json.dumps(expected_artifacts or [], ensure_ascii=False)

    context_blocks = []
    _debug_ctx = []
    # Priority order: habits (user) > evolution (LLM insights) > experiences > growth
    # Each block gets a priority header so the planner knows which to trust more
    if habits and habits.strip() and "暂无特殊偏好" not in habits:
        context_blocks.append(f"### 用户偏好（最高优先级）\n{habits}")
        _debug_ctx.append("habits")
    if evolution_rules and evolution_rules.strip():
        context_blocks.append(f"### 自进化洞察（AI分析，参考优先级）\n{evolution_rules}")
        _debug_ctx.append("evolution")
    if proc_mem and proc_mem.strip():
        context_blocks.append(f"### 程序性记忆（已验证的操作序列）\n{proc_mem}")
        _debug_ctx.append("proc_mem")
    if layered_rules and layered_rules.strip():
        context_blocks.append(f"### 分层上下文规则\n{layered_rules}")
        _debug_ctx.append("layered_rules")
    if rules and rules.strip():
        context_blocks.append(f"### 行为规则\n{rules}")
        _debug_ctx.append("rules")
    if experiences and experiences.strip() and "暂无相关经验" not in experiences:
        context_blocks.append(f"### 历史经验\n{experiences}")
        _debug_ctx.append("experiences")
    if growth and growth.strip():
        context_blocks.append(f"### 成长记录\n{growth}")
        _debug_ctx.append("growth")
    # RL Experience context (from learning.db)
    try:
        from ..evolution.rl_engine import get_rl_loop
        rl = get_rl_loop(workspace_root) if workspace_root else None
        if rl:
            rl_ctx = rl.format_context()
            if rl_ctx and rl_ctx.strip():
                context_blocks.append(f"### 强化学习经验\n{rl_ctx}")
                _debug_ctx.append("rl_experience")
    except Exception:
        pass
    if _debug_ctx:
        logger.info("[PROMPT_BUILDER] injected context: %s", ", ".join(_debug_ctx))
    context_section = "\n\n".join(context_blocks) if context_blocks else ""
    if context_section:
        context_section = f"\n\n## 上下文知识（按优先级排列）\n\n{context_section}"

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
            "### 专用 Agent 自动安装与调用\n"
            "- **当用户要求调用任何外部 CLI 工具时**（如 pandoc、seqkit、gdown、ffmpeg、wkhtmltopdf、samtools 等），生成两步：\n"
            "  1. `atomic_ensure_agent_installed(agent=\"工具名\")` — 检查是否已安装，必要时从 GitHub 自动发现并安装\n"
            "  2. `call_agent_skill(agent=\"工具名\", task=\"用户的完整任务描述\", parameters={{\"input\": \"输入文件路径\", \"output\": \"输出目录\", \"question\": \"任务描述\"}})` — 真实调用\n"
            "- atomic_ensure_agent_installed 会在 Agent 已安装时直接返回成功（不重复安装），仅在首次或 force_reinstall=true 时执行下载和安装。\n"
            "- 不要在规划步骤的 parameters 中直接写 pandoc/seqkit 等工具的具体命令行参数——那由 agent 的 manifest 决定。\n"
            "- 不要合并成一步。安装和调用必须分开成两个步骤，以防安装耗时过长阻塞调用。\n"
            "- 注意：call_agent_skill 会在 agent 未注册时自动让 Hermes 搜索 GitHub 查找该工具并建立接口，所以即使没预先注册也可以调用。\n"
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

    prompt = f"""你是 Partner 的任务规划器。用户要求：{user_message[:1500]}

可用操作（只能使用这些）：
{available_events or "call_agent_skill, atomic_write_artifact"}{available_agents}{context_section}{event_guidance}

工作目录：{working_dir}

⚠️ 规划规则（必须遵守）：
- 不要在临时任务目录（state/tasks/ 下）使用 read_file。临时任务目录在任务创建前是空的，read_file 永远找不到文件。
- 如果任务需要读取项目文件，应在 shared_projects/ 或 data/ 等持久化目录下使用 read_file。
- 第一个步骤直接使用 call_agent_skill 或 web_search，不要用 list_directory + read_file 做无意义的文件探测。
- 如果任务明确给出了文件路径（如 PDB 路径），直接传给 agent 的 input 参数，不要先 read_file 验证。
预期产物：{arts}

{_OUTPUT_FORM_PRINCIPLES}

重要：如果用户明确要求使用某个专用 Agent（如 cytobridge、docking-agent 等），
你必须使用 call_agent_skill(agent="agent_name", task=..., parameters={{...}}) 来调用该 Agent。
如果该专用 Agent 可能尚未安装，先用 atomic_ensure_agent_installed(agent="agent_name") 确保安装完成，再用 call_agent_skill 调用。
不要用 web_search、smart_llm_structured_action 或 run_command 替代专用 Agent 的功能。
调用专用 Agent 后，用 smart_llm_structured_action 或 atomic_write_artifact 处理其结果。

⚠️ cytobridge / cytobridge-agent 特殊规则（必须遵守，违反会导致用户体验严重下降）：
如果专用 Agent 是 cytobridge 或 cytobridge-agent，它已经自动生成了完整的分析报告 (analysis_report_zh.md + figures/ 下的 PNG 图片)。
此时规划必须遵循以下模式：
  1. call_agent_skill(agent="cytobridge" 或 "cytobridge-agent") ← 运行 cytobridge，它自动产出 analysis_report_zh.md + figures/
     parameters 中**必须包含**以下三个参数，缺一不可：
     - input: 输入文件路径（如 /mnt/e/work/data/pancreas.h5ad）
     - question: 从用户消息中提取的完整科学问题描述（如 "对 pancreas.h5ad 做单细胞轨迹推断，分析胰腺细胞的分化路径"）
     - output: 输出目录（填写工作目录 {working_dir}）
     缺少 question 会导致 agent 无法获得任务描述，只读取 SKILL.md 后就停止，不会做任何分析。
  2. atomic_convert_md_to_pdf ← 直接转 PDF（无需 source 参数，会自动在输出目录中找到 .md 文件）
  ❌ 禁止安排 smart_llm_structured_action 生成新报告（cytobridge 已自带了）
  ❌ 禁止安排 atomic_write_artifact 写 .md 文件
  ✅ 最终产物只有 PDF，不交付 .md

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


def _extract_task_type(message: str) -> str:
    """Extract the primary task type from a user message for experience matching."""
    if not message:
        return ""
    
    # Chinese task type keywords
    keywords = {
        "文献综述": "文献综述",
        "单细胞": "单细胞分析",
        "轨迹": "轨迹推断",
        "差异表达": "差异表达分析",
        "分子对接": "分子对接",
        "可视化": "可视化",
        "报告": "报告生成",
        "搜索": "信息搜索",
        "天气": "信息查询",
        "查询": "信息查询",
    }
    
    msg_lower = message.lower()
    for keyword, task_type in keywords.items():
        if keyword in message or keyword.lower() in msg_lower:
            return task_type
    
    # English keywords
    en_keywords = {
        "trajectory": "轨迹推断",
        "differential": "差异表达分析",
        "docking": "分子对接",
        "visualization": "可视化",
        "literature": "文献综述",
        "review": "文献综述",
        "single cell": "单细胞分析",
        "scRNA": "单细胞分析",
    }
    for keyword, task_type in en_keywords.items():
        if keyword in msg_lower:
            return task_type
    
    return ""
