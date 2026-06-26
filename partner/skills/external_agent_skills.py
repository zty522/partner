"""External Agent skill executor — simplified with two-tier dispatch.

General agents (hermes, openclaw, codex) are called through adapter.chat().
Specialized CLI agents (cytobridge, etc.) are dispatched through AgentDispatcher.

Each agent invocation is clearly marked with [agent_name] in progress messages.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .base_skill import Skill, SkillResult

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# ── General agents: called through adapter.chat() ──
_GENERAL_AGENTS = {"hermes", "openclaw", "codex"}


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
