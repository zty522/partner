"""External Agent skill executor — simplified with two-tier dispatch.

General agents (hermes, openclaw, codex) are called through adapter.chat().
Specialized CLI agents (cytobridge, etc.) are dispatched through AgentDispatcher.

Each agent invocation is clearly marked with [agent_name] in progress messages.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from .base_skill import Skill, SkillResult

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# ── General agents: called through adapter.chat() ──
_GENERAL_AGENTS = {"hermes", "openclaw", "codex"}

# ── Agents that use direct Python API instead of CLI dispatch ──
_DIRECT_API_AGENTS = {"cytobridge"}


async def execute_agent_task(
    *,
    workspace: str,
    agent: str,
    task: str,
    task_instance: Any = None,
    allow_web: bool = False,
    agent_params: dict | None = None,
) -> SkillResult:
    """Forward a task text to an agent and return its response.

    Two dispatch paths:
    - General agents (hermes, openclaw, codex) → adapter.chat() (existing path)
    - Specialized CLI agents → AgentDispatcher._dispatch_cli()

    Args:
        workspace: Partner workspace path.
        agent: Agent name ("hermes", "openclaw", "cytobridge", etc.).
        task: The natural-language task for the agent to accomplish.
        task_instance: Optional TaskInstance for logging.
        allow_web: Whether to grant web/network permission.

    Returns:
        SkillResult with output containing the agent's response.
    """
    agent = str(agent or "").strip().lower()
    task = str(task or "").strip()
    if not task:
        return SkillResult(False, error="empty task")

    if agent in _GENERAL_AGENTS:
        return await _call_general_agent(agent, workspace, task, task_instance, allow_web, agent_params)
    elif agent in _DIRECT_API_AGENTS:
        return await _call_agent_direct_api(agent, workspace, task, task_instance, agent_params)
    else:
        return await _call_specialized_agent(agent, workspace, task, task_instance, agent_params)


async def _call_general_agent(
    agent: str,
    workspace: str,
    task: str,
    task_instance: Any,
    allow_web: bool,
    agent_params: dict | None = None,
) -> SkillResult:
    """Call a general agent through adapter.chat()."""
    adapter = _make_adapter(agent, workspace)

    prompt = _build_task_prompt(task, allow_web=allow_web)

    if task_instance:
        task_instance.append_log("call_agent_task_started", {
            "agent": agent,
            "task_preview": task[:200],
            "allow_web": allow_web,
        })

    try:
        # Load timeout from external_calls.yaml
        _timeout_sec = None
        try:
            from ..harness_core.robust_executor import load_harness_config
            config = load_harness_config(workspace)
            agent_call_cfg = (config.get("external_calls") or {}).get("per_event", {}).get("agent_call", {})
            _timeout_sec = int(agent_call_cfg.get("timeout", 0)) if "timeout" in agent_call_cfg else None
        except Exception:
            _timeout_sec = None

        if _timeout_sec is not None:
            os.environ["PARTNER_ACTION_AGENT_TIMEOUT_SEC"] = str(_timeout_sec)
            try:
                reply = adapter.chat(prompt, purpose="action")
            finally:
                os.environ.pop("PARTNER_ACTION_AGENT_TIMEOUT_SEC", None)
        else:
            reply = adapter.chat(prompt, purpose="action")
    except Exception as exc:
        logger.warning("[CALL_AGENT] %s chat failed for task=%s: %s", agent, task[:80], exc)
        return SkillResult(False, error=str(exc))

    # Detect common agent error patterns
    reply_lower = reply.lower()
    if not reply or reply.strip() == "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__":
        return SkillResult(False, error=f"{agent} returned no usable output")

    for pattern in [
        "⏱ Timeout — denying command",
        "PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE",
        "Error: agent backend not available",
        "timeout",
        "denying command",
    ]:
        if pattern.lower() in reply_lower:
            logger.warning("[CALL_AGENT] %s returned error pattern '%s' for task=%s", agent, pattern, task[:80])
            return SkillResult(False, error=f"{agent} 执行超时或拒绝请求: {reply[:200]}")

    if task_instance:
        task_instance.append_log("call_agent_task_completed", {
            "agent": agent,
            "reply_preview": reply[:300],
        })

    # Mark output with [agent] header
    result_output = {"content": f"[{agent}]\n{reply}"}
    return SkillResult(True, output=result_output)


async def _call_specialized_agent(
    agent: str,
    workspace: str,
    task: str,
    task_instance: Any,
    agent_params: dict | None = None,
) -> SkillResult:
    """Call a specialized CLI agent through AgentDispatcher.

    These are agents registered via AgentManifest with endpoint_type='cli'.
    They are dispatched as subprocess commands, not through adapter.chat().
    """
    if task_instance:
        task_instance.append_log("call_agent_specialized_started", {
            "agent": agent,
            "task_preview": task[:200],
        })

    try:
        from ..agents.dispatcher import AgentTask, AgentResult
        from ..agents.registry import AgentRegistry
        from ..agents.dispatcher import AgentDispatcher

        registry = AgentRegistry(workspace=workspace)
        dispatcher = AgentDispatcher(registry)

        # ── Auto-discovery: if agent not registered, use Hermes to find it ──
        if not registry.get_agent(agent):
            logger.info("[CALL_AGENT] Agent '%s' not registered, auto-discovering from GitHub...", agent)
            if task_instance:
                task_instance.append_log("call_agent_auto_discover", {
                    "agent": agent,
                })
            try:
                from ..agents.discoverer import discover_and_register_agent
                manifest_path = discover_and_register_agent(
                    agent_name=agent,
                    workspace=workspace,
                )
                if manifest_path:
                    logger.info("[CALL_AGENT] Auto-discovered and registered '%s': %s", agent, manifest_path)
                    # Re-create registry to pick up the new manifest
                    registry = AgentRegistry(workspace=workspace)
                    dispatcher = AgentDispatcher(registry)
                else:
                    logger.warning("[CALL_AGENT] Auto-discovery failed for '%s'", agent)
            except Exception as _disc_exc:
                logger.warning("[CALL_AGENT] Auto-discovery error for '%s': %s", agent, _disc_exc)

        result = await dispatcher.dispatch(
            AgentTask(
                agent=agent,
                task=task,
                parameters=dict(agent_params or {}),
                context={"working_dir": workspace or os.getcwd()},
            )
        )
    except Exception as exc:
        logger.warning("[CALL_AGENT] specialized agent %s dispatch failed task=%s: %s", agent, task[:80], exc)
        return SkillResult(False, error=str(exc))

    if result.status == "error":
        return SkillResult(False, error=result.error or f"{agent} dispatch failed")

    # Extract output
    output_text = result.output.get("text", "") if isinstance(result.output, dict) else str(result.output)

    # ── Enrich content with structured data from agent's output directory ──
    # The agent's stdout is a CLI execution log (tool calls, file reads, LLM
    # conversation). Downstream steps need structured analysis data (DPT values,
    # gene lists, clustering results) which the agent wrote to files in its
    # output directory. We scan that directory and append any structured data
    # files to the content so downstream report generation has real numbers.
    _enrich_blocks = []
    _output_dir = str(dict(agent_params or {}).get("output") or "")
    if not _output_dir or not os.path.isdir(_output_dir):
        # Fallback: try to extract output path from the CLI command metadata
        _cmd = str(result.metadata.get("command", ""))
        _parts = _cmd.split()
        for _i, _p in enumerate(_parts):
            if _p in ("-o", "--output") and _i + 1 < len(_parts):
                _candidate = _parts[_i + 1]
                if os.path.isdir(_candidate):
                    _output_dir = _candidate
                    break
    if _output_dir and os.path.isdir(_output_dir):
        _structured_files = [
            "summary.json", "result.json",
            "data/summary.json", "data/result.json",
            "report/summary.json", "report/result.json",
        ]
        for _rel in _structured_files:
            _fpath = os.path.join(_output_dir, _rel)
            if os.path.isfile(_fpath):
                try:
                    with open(_fpath, "r", encoding="utf-8") as _f:
                        _raw = _f.read()
                    if len(_raw) > 100:
                        _enrich_blocks.append(
                            f"【Cytobridge 结构化分析结果——请基于以下真实数据撰写报告】\n"
                            f"以下是从 cytobridge 输出的结构化分析数据，包含伪时间分布、细胞类型、轨迹参数等关键指标。\n"
                            f"请基于这些真实数值用中文撰写完整的分析报告，不要编造数据。\n"
                            f"{_raw[:30000]}"
                        )
                        break
                except Exception:
                    pass
        # Also enrich with trajectory correlated genes CSV
        _genes_csv = os.path.join(_output_dir, "data", "trajectory_correlated_genes.csv")
        if os.path.isfile(_genes_csv):
            try:
                with open(_genes_csv, "r", encoding="utf-8") as _f:
                    _csv_content = _f.read()
                if len(_csv_content) > 50:
                    _enrich_blocks.append(
                        f"【轨迹相关基因 Top 20】\n{_csv_content[:10000]}"
                    )
            except Exception:
                pass
        # Figure listing
        _fig_dir = os.path.join(_output_dir, "figures")
        if os.path.isdir(_fig_dir):
            _figs = [os.path.join(_fig_dir, f) for f in sorted(os.listdir(_fig_dir))
                     if f.endswith((".png", ".svg", ".pdf"))]
            if _figs:
                _enrich_blocks.append("【可视化图表】\n" + "\n".join(_figs))

    if _enrich_blocks:
        # Append structured data AFTER the execution log, separated by a marker
        output_text = output_text[:100000] + "\n\n---\n\n" + "\n\n".join(_enrich_blocks)
        logger.info("[CALL_AGENT] enriched %s output with %d data blocks (output_dir=%s)",
                     agent, len(_enrich_blocks), _output_dir)

    if task_instance:
        task_instance.append_log("call_agent_specialized_completed", {
            "agent": agent,
            "duration": result.metadata.get("duration"),
            "reply_preview": output_text[:300],
        })

    # Mark output with [agent] header for clear identification
    result_output = {"content": f"[{agent}]\n{output_text}"}
    return SkillResult(True, output=result_output)


async def _call_agent_direct_api(
    agent: str,
    workspace: str,
    task: str,
    task_instance: Any,
    agent_params: dict | None = None,
) -> SkillResult:
    """Call a specialized agent through its direct Python API (not CLI).

    Used for agents like cytobridge where the CLI `run` subcommand runs a
    LangGraph that only completes the intake phase but doesn't produce
    real analysis output. The Python API gives full control over the
    session lifecycle and LangGraph recursion limit.

    Mirror of the benchmark harness approach in
    ``cytobridge-agent/benchmark/agent_runners/cytobridge_runner.py``.
    """
    if task_instance:
        task_instance.append_log("call_agent_direct_api_started", {
            "agent": agent,
            "task_preview": task[:200],
        })

    params = dict(agent_params or {})
    input_path = str(params.get("input") or params.get("file") or "")
    # Route output to task instance's working directory, not cwd
    _task_output_base = (getattr(task_instance, "working_dir", None) or os.getcwd()) if task_instance else os.getcwd()
    output_dir = str(params.get("output") or params.get("output_dir") or "")
    if not output_dir or output_dir in ("./output", "output", ".", ""):
        output_dir = os.path.join(_task_output_base, "output")
    question = str(params.get("question") or task)
    device = str(params.get("device") or "cpu")

    # ── Step 1: Resolve input file path dynamically ──
    from ..utils.file_resolver import resolve_file_path
    resolved_input, found = resolve_file_path(input_path)
    if not found:
        err = (
            f"输入文件未找到: {input_path}。"
            f"已在全盘搜索 basename='{os.path.basename(input_path)}' 但未发现。"
            f"请确认文件已下载到本地磁盘。"
        )
        logger.warning("[CALL_AGENT] %s: %s", agent, err)
        return SkillResult(False, error=err)

    # Use the resolved path
    input_path = resolved_input
    os.makedirs(output_dir, exist_ok=True)

    # ── Step 2: Set environment for long-running analysis ──
    os.environ.setdefault("CYTOBRIDGE_RUNTIME_RECURSION_LIMIT", "200")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

    # ── Step 3: Launch cytobridge via Python API ──
    logs = []
    t0 = time.time()

    try:
        from cytobridge_agent.cli_runtime import SessionOpenSpec, open_or_create_session
        from cytobridge_agent.session_controller import SessionController
        from cytobridge_agent.utils.llm_factory import instantiate_llm

        # Build LLM config from environment
        api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        base_url = os.environ.get("OPENAI_BASE_URL") or ""
        if api_key and api_key.startswith("sk-d") and not base_url:
            base_url = "https://api.deepseek.com"

        llm_config = {
            "model": os.environ.get("HERMES_MODEL") or "deepseek-v4-flash",
            "provider": os.environ.get("PARTNER_PROVIDER") or "deepseek",
            "base_url": base_url or "https://api.deepseek.com",
            "api_key": api_key,
            "auth_mode": "auto",
        }

        def llm_factory(config=None):
            cfg = dict(llm_config)
            if config:
                cfg.update(config)
            llm, _ = instantiate_llm(
                model=cfg.get("model"),
                base_url=cfg.get("base_url"),
                api_key=cfg.get("api_key"),
                auth_mode=cfg.get("auth_mode") or "auto",
                provider=cfg.get("provider") or "auto",
            )
            return llm

        controller = SessionController(
            llm_factory,
            initial_config={
                "llm_provider": llm_config["provider"],
                "llm_base_url": llm_config["base_url"],
                "llm_api_key": llm_config["api_key"],
                "llm_model": llm_config["model"],
                "stop_hook_enabled": False,
            },
        )

        session, spec = open_or_create_session(
            controller,
            SessionOpenSpec(
                input_path=input_path,
                question=question,
                output_dir=output_dir,
                device=device,
                report_format="md",
                enable_multimodal=False,
                user_goal_overrides={
                    "raw_question": question,
                    "requested_analyses": ["trajectory_fate", "drivers_genes"],
                    "device": device,
                    "report_format": "md",
                },
            ),
        )

        # Run the first turn — this advances the LangGraph through
        # intake → preprocessing → training → downstream → report
        turn_result = controller.run_turn(
            f"请完成以下单细胞数据分析任务：{task}\n\n"
            f"数据文件：{input_path}\n"
            f"输出目录：{output_dir}\n"
            f"科学问题：{question}\n"
            f"请完整执行加载、预处理、归一化、降维、轨迹推断(PAGA+DPT)、"
            f"细胞命运分析、驱动基因鉴定。完成后以中文撰写报告。"
        )

        runtime = time.time() - t0
        logs.append(f"Runtime: {runtime:.1f}s")
        logs.append(f"Agent: {agent}")
        logs.append(f"Input: {input_path}")
        logs.append(f"Output: {output_dir}")

        # ── Step 4: Check for real analysis output files ──
        # Don't just trust the agent's stdout — verify the output directory
        # contains actual analysis results (figures, structured data, etc.)
        output_files = []
        figures = []
        structured_data = []

        if os.path.isdir(output_dir):
            for root, dirs, files in os.walk(output_dir):
                for f in files:
                    fpath = os.path.join(root, f)
                    if f.endswith((".png", ".svg", ".pdf")):
                        figures.append(fpath)
                    elif f.endswith((".csv", ".json", ".tsv")):
                        if os.path.getsize(fpath) > 50:
                            structured_data.append(fpath)
                    elif f.endswith(".md") and os.path.getsize(fpath) > 500:
                        output_files.append(fpath)

        # Also check training_runs for the actual analysis output
        training_runs_dir = os.path.join(output_dir, "training_runs")
        if os.path.isdir(training_runs_dir):
            for root, dirs, files in os.walk(training_runs_dir):
                for f in files:
                    fpath = os.path.join(root, f)
                    if f.endswith(".csv") and os.path.getsize(fpath) > 50:
                        structured_data.append(fpath)
                    if f.endswith(".png"):
                        figures.append(fpath)

        # Assemble the output content
        content_parts = [f"[{agent}]"]
        content_parts.append(f"\n**分析完成** ({runtime:.0f}秒)")
        content_parts.append(f"\n输入文件: {input_path}")
        content_parts.append(f"输出目录: {output_dir}")

        if figures:
            content_parts.append(f"\n**图表 ({len(figures)}张):**")
            for fig in figures[:10]:
                content_parts.append(f"- {fig}")

        if structured_data:
            content_parts.append(f"\n**结构化数据 ({len(structured_data)}个文件):**")
            for sd in structured_data[:10]:
                # Read and include first few lines of CSV/JSON data
                try:
                    with open(sd, "r") as sf:
                        preview = sf.read(2000)
                    content_parts.append(f"\n来自 {os.path.relpath(sd, output_dir)}:\n{preview}")
                except Exception:
                    content_parts.append(f"- {sd}")

        if output_files:
            content_parts.append(f"\n**报告 ({len(output_files)}个):**")
            for of in output_files:
                try:
                    with open(of, "r") as of_f:
                        report_content = of_f.read()
                    content_parts.append(f"\n【报告: {os.path.basename(of)}】\n{report_content[:5000]}")
                except Exception:
                    content_parts.append(f"- {of}")

        output_text = "\n\n".join(content_parts)

        if task_instance:
            has_real_output = bool(figures) or bool(structured_data) or bool(output_files)
            task_instance.append_log("call_agent_direct_api_completed", {
                "agent": agent,
                "duration": runtime,
                "has_real_output": has_real_output,
                "figures": len(figures),
                "structured_files": len(structured_data),
                "report_files": len(output_files),
            })

        # ── Step 5: Validate real output was produced ──
        if not figures and not structured_data and not output_files:
            logger.warning(
                "[CALL_AGENT] %s completed but produced no output files in %s. "
                "This is the LangGraph-turn-completes-but-does-no-work pattern.",
                agent, output_dir,
            )
            return SkillResult(
                False,
                error=(
                    f"{agent} 执行完成但未在输出目录 {output_dir} 中生成任何分析文件。"
                    f"可能是 LangGraph 迭代限制导致工作流未完整执行。"
                    f"Agent 日志：{output_text[:2000]}"
                ),
                output={"content": output_text},
            )

        logger.info(
            "[CALL_AGENT] %s completed with real output: %d figures, %d data files, %d reports",
            agent, len(figures), len(structured_data), len(output_files),
        )
        return SkillResult(True, output={"content": output_text})

    except ImportError as e:
        logger.error("[CALL_AGENT] %s Python API not available: %s", agent, e)
        return SkillResult(False, error=f"{agent} 的 Python API 未安装: {e}")
    except Exception as exc:
        runtime = time.time() - t0
        logger.error("[CALL_AGENT] %s direct API call failed after %.1fs: %s",
                     agent, runtime, exc, exc_info=True)
        return SkillResult(False, error=f"{agent} 调用失败 ({runtime:.0f}s): {exc}")


def _build_task_prompt(task: str, *, allow_web: bool = False) -> str:
    """Build a minimal prompt that tells the agent what to do.

    The agent is free to use its own tools and skills.  Partner only
    specifies the task and optionally grants network permission.
    """
    lines = [
        "你被 Partner 编排调度。请完成以下任务，自主决定使用哪些工具和能力。",
        "不要向用户寒暄或提问，直接完成任务并返回结果。",
        "如果任务需要生成文件，请在当前工作目录下生成并返回文件路径。",
        "",
        f"任务：{task}",
    ]
    if allow_web:
        lines.append("")
        lines.append("（Partner 已授权联网）")
    return "\n".join(lines)


def _make_adapter(agent: str, workspace: str):
    """Return the appropriate adapter for a general agent."""
    if agent == "hermes":
        from ..adapter import HermesAdapter
        return HermesAdapter(workspace)
    if agent == "openclaw":
        from ..openclaw_adapter import OpenClawAdapter
        return OpenClawAdapter(workspace)
    if agent == "codex":
        from ..adapter import CodexAdapter
        return CodexAdapter(workspace)
    raise RuntimeError(f"unsupported general agent backend: {agent}")
