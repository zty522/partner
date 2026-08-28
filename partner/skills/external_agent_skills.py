"""External Agent skill executor — simplified with two-tier dispatch.

General agents (hermes, openclaw, codex) are called through adapter.chat().
Specialized CLI agents (cytobridge, etc.) are dispatched through AgentDispatcher.

Each agent invocation is clearly marked with [agent_name] in progress messages.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
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
    progress_callback: Any = None,
    **extra_kwargs,  # Absorb unknown params gracefully
) -> SkillResult:
    # Log unknown params for debugging, never crash
    if extra_kwargs:
        logger.debug("[AGENT_TASK] received extra kwargs (ignored): %s", list(extra_kwargs.keys()))
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
        return await _call_general_agent(
            agent, workspace, task, task_instance, allow_web, agent_params,
            purpose=agent_params.get("purpose", "action") if agent_params else "action",
        )
    else:
        return await _call_specialized_agent(agent, workspace, task, task_instance, agent_params, progress_callback)


async def _call_general_agent(
    agent: str,
    workspace: str,
    task: str,
    task_instance: Any,
    allow_web: bool,
    agent_params: dict | None = None,
    *,
    purpose: str = "action",
) -> SkillResult:
    """Call a general agent through adapter.chat().

    Hermes 2026-08-27 fix (Bug #41): accept an explicit `purpose` so
    callers can route pure-text generation events (generate_text /
    write_report / summarize / analyze) through the no-tool `report`
    purpose instead of the default `action` purpose that exposes
    `terminal,file,web` tools. The action toolset confuses the model
    into wrapping its response in a `write_file` tool_call JSON
    envelope, which the partner framework then writes verbatim to
    the destination .md file. The `report` purpose runs single-turn
    with no tools so the model returns plain text.
    """
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
            # If the caller passed a different purpose (e.g. "report" for
            # pure-text generation), use that env var so the adapter can
            # load the right timeout. The action-purpose timeout is the
            # default; the "report" purpose reads from its own env var.
            if purpose == "report":
                os.environ["PARTNER_REPORT_TIMEOUT_SEC"] = str(_timeout_sec)
                try:
                    reply = adapter.chat(prompt, purpose=purpose)
                finally:
                    os.environ.pop("PARTNER_REPORT_TIMEOUT_SEC", None)
            else:
                os.environ["PARTNER_ACTION_AGENT_TIMEOUT_SEC"] = str(_timeout_sec)
                try:
                    reply = adapter.chat(prompt, purpose=purpose)
                finally:
                    os.environ.pop("PARTNER_ACTION_AGENT_TIMEOUT_SEC", None)
        else:
            reply = adapter.chat(prompt, purpose=purpose)
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
    ]:
        if pattern.lower() in reply_lower:
            logger.warning("[CALL_AGENT] %s returned error pattern '%s' for task=%s", agent, pattern, task[:80])
            return SkillResult(False, error=f"{agent} 执行超时或拒绝请求: {reply[:200]}")
    # Ordinary deliverables may legitimately discuss timeout handling or a
    # denied command. Treat those generic words as transport errors only in a
    # short, error-shaped response; scanning an entire report caused valid
    # architecture analyses to be discarded.
    if len(reply.strip()) < 500 and re.search(
        r"(?:^|\n)\s*(?:error\s*[:：]\s*)?(?:timeout|timed out|denying command)\b",
        reply_lower,
    ):
        logger.warning("[CALL_AGENT] %s returned short timeout/refusal response for task=%s", agent, task[:80])
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
    progress_callback: Any = None,
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

        # ── Wrap async progress_callback for dispatcher's daemon poll thread ──
        _safe_cb = progress_callback
        if _safe_cb is not None:
            import asyncio as _asyncio_wrap
            import inspect as _inspect_wrap
            if _inspect_wrap.iscoroutinefunction(_safe_cb):
                try:
                    _main_loop = _asyncio_wrap.get_running_loop()
                except RuntimeError:
                    _main_loop = None
                if _main_loop is not None:
                    def _threadsafe_cb(msg):
                        _asyncio_wrap.run_coroutine_threadsafe(_safe_cb(msg), _main_loop)
                    _safe_cb = _threadsafe_cb
                else:
                    _safe_cb = None
        result = await dispatcher.dispatch(
            AgentTask(
                agent=agent,
                task=task,
                parameters=dict(agent_params or {}),
                context={
                    "working_dir": workspace or os.getcwd(),
                    "progress_callback": _safe_cb
                },
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
        # First, list all output files for diagnostic visibility
        _all_files = []
        for _root, _dirs, _files in os.walk(_output_dir):
            for _f in _files:
                _fp = os.path.join(_root, _f)
                try:
                    _sz = os.path.getsize(_fp)
                    _rel = os.path.relpath(_fp, _output_dir)
                    _all_files.append(f"{_rel} ({_sz:,} bytes)")
                except Exception:
                    pass
        if _all_files:
            _enrich_blocks.append(
                "【完整输出文件清单】\n" + "\n".join(sorted(_all_files)[:50])
            )

        # Specific structured data files (JSON)
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
        # Also scan for markdown reports (cytobridge-agent produces report.md,
        # pancreas_trajectory_report_v2.md, etc.)
        # NOTE: Only include the HEADERS/structure of each report (~500 chars),
        # NOT the full content. Including the full report confuses downstream
        # LLM steps (they try to diff/review the existing report instead of
        # generating a new one from the analysis data).
        for _root, _dirs, _files in os.walk(_output_dir):
            for _f in sorted(_files):
                if not _f.endswith(".md"):
                    continue
                _fpath = os.path.join(_root, _f)
                try:
                    with open(_fpath, "r", encoding="utf-8", errors="replace") as _fh:
                        _md_content = _fh.read(500)
                    if len(_md_content) > 50:
                        # Extract just the title/headers structure
                        _title_lines = []
                        for _line in _md_content.split("\n"):
                            if _line.startswith("#") and _line.strip():
                                _title_lines.append(_line.strip())
                            if len(_title_lines) >= 10:
                                break
                        _enrich_blocks.append(
                            f"【Agent 已有报告——{_f}】报告标题结构：\n" + "\n".join(_title_lines) if _title_lines else f"【Agent 已有报告——{_f}】（{os.path.getsize(_fpath)} bytes）"
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

    # ── Package agent outputs into a comprehensive PDF ──
    # Specialized agents (cytobridge, etc.) produce PNG figures, CSV tables,
    # and reports as native output.  Package them into a single deliverable
    # PDF that embeds everything — no need to build custom formatting per agent.
    _pkg_pdf = _package_agent_outputs(_output_dir, agent)
    if _pkg_pdf:
        output_text += f"\n\n【综合报告 PDF】{_pkg_pdf}"
        logger.info("[CALL_AGENT] packaged %s outputs into PDF: %s", agent, _pkg_pdf)

    if task_instance:
        task_instance.append_log("call_agent_specialized_completed", {
            "agent": agent,
            "duration": result.metadata.get("duration"),
            "reply_preview": output_text[:300],
        })

    # Mark output with [agent] header for clear identification
    result_output = {"content": f"[{agent}]\n{output_text}"}
    return SkillResult(True, output=result_output)


# ── Generic agent output packaging ────────────────────────────────────
# Any specialized agent (cytobridge, docking-agent, etc.) produces PNG
# figures, CSV data tables, and text reports.  This function scans the
# output directory, builds a single HTML page with everything embedded
# (base64 images, inline tables), and converts it to a deliverable PDF.


def _package_agent_outputs(output_dir: str, agent_name: str) -> str:
    """Package agent outputs into a PDF, reusing the agent's own HTML report.

    Strategy:
    1. If the agent produced its own HTML report (report.html), reuse it:
       embed PNG figures as base64 so the PDF is self-contained.
    2. Otherwise fall back to building a simple HTML from discovered files.

    This avoids duplicating the agent's visualization design — let the
    specialist agent design its own report, Partner just packages it.
    """
    if not output_dir or not os.path.isdir(output_dir):
        return ""

    import glob
    import re

    pngs = sorted(glob.glob(os.path.join(output_dir, "**/*.png"), recursive=True))
    csvs = sorted(glob.glob(os.path.join(output_dir, "**/*.csv"), recursive=True))
    htmls = sorted(glob.glob(os.path.join(output_dir, "*.html"), recursive=False))

    if not pngs and not csvs and not htmls:
        return ""

    # Strategy 1: reuse agent's native HTML report (the best option)
    _html_content = None
    if htmls:
        # Pick the most substantial HTML (largest file, likely the report)
        _html_path = max(htmls, key=lambda p: os.path.getsize(p) if os.path.isfile(p) else 0)
        try:
            with open(_html_path, "r", encoding="utf-8") as _fh:
                _html_content = _fh.read(200000)

            # Embed PNG images referenced by the agent's HTML as base64
            def _embed_img(m: re.Match) -> str:
                _src = m.group(1)
                if _src.startswith("data:"):
                    return m.group(0)  # already embedded
                # Resolve relative to the HTML file's directory
                _img_dir = os.path.dirname(_html_path)
                _img_path = os.path.join(_img_dir, _src)
                # Also try relative to output_dir (some agents use output_dir as root)
                if not os.path.isfile(_img_path):
                    _img_path = os.path.join(output_dir, _src)
                if os.path.isfile(_img_path):
                    try:
                        import base64
                        with open(_img_path, "rb") as _fh_img:
                            _b64 = base64.b64encode(_fh_img.read()).decode()
                        return f'<img src="data:image/png;base64,{_b64}"'
                    except Exception:
                        pass
                return m.group(0)

            _html_content = re.sub(r'<img\s+[^>]*src="([^"]+)"', _embed_img, _html_content)
            logger.info("[PACKAGE] reusing agent's native HTML: %s", _html_path)
        except Exception as exc:
            logger.warning("[PACKAGE] failed to read agent HTML %s: %s", _html_path, exc)
            _html_content = None

    # Strategy 2 (fallback): build simple HTML from discovered files
    if _html_content is None:
        _lines: list[str] = []
        _lines.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
        _lines.append(f"<title>{agent_name} — 分析报告</title>")
        _lines.append("<style>")
        _lines.append("body{font-family:system-ui,sans-serif;margin:2cm;line-height:1.6}")
        _lines.append("img{max-width:100%;margin:1em 0;border:1px solid #ddd}")
        _lines.append("table{border-collapse:collapse;width:100%;margin:1em 0;font-size:90%}")
        _lines.append("td,th{border:1px solid #ccc;padding:6px 10px;text-align:left}")
        _lines.append("th{background:#f0f0f0;font-weight:600}")
        _lines.append(".section{margin:2em 0}")
        _lines.append("h2{color:#2c5282;border-bottom:2px solid #e2e8f0}")
        _lines.append("</style></head><body>")
        _lines.append(f"<h1>{agent_name} 分析报告</h1>")

        for png_path in pngs:
            try:
                import base64
                with open(png_path, "rb") as _fh:
                    _b64 = base64.b64encode(_fh.read()).decode()
                _rel = os.path.relpath(png_path, output_dir)
                _lines.append(f"<figure><figcaption>{_rel}</figcaption>")
                _lines.append(f"<img src='data:image/png;base64,{_b64}'>")
                _lines.append("</figure>")
            except Exception:
                pass

        for csv_path in csvs:
            try:
                _rel = os.path.relpath(csv_path, output_dir)
                _lines.append(f"<h3>{_rel}</h3><table>")
                with open(csv_path, "r", encoding="utf-8") as _fh:
                    _rows = _fh.readlines()
                for _i, _row in enumerate(_rows[:50]):
                    _cells = _row.strip().split(",")
                    _tag = "th" if _i == 0 else "td"
                    _lines.append("<tr>" + "".join(f"<{_tag}>{__import__('html').escape(c[:60])}</{_tag}>" for c in _cells) + "</tr>")
                _lines.append("</table>")
            except Exception:
                pass

        _lines.append("</body></html>")
        _html_content = "\n".join(_lines)

    try:
        from weasyprint import HTML
        # ── Append standard references for single-cell analysis ──
        # The agent's report may not include references for the methods it used
        # (PAGA, DPT, scanpy, etc.). Add them to the HTML before PDF conversion.
        _references_html = """
<div class="references-section" style="margin-top:3em;padding-top:1em;border-top:2px solid #4A90D9;">
<h2 style="color:#2c5282;">参考文献</h2>
<ul style="line-height:1.8;font-size:90%;">
<li>Wolf, F.A., Angerer, P., Theis, F.J. (2018). SCANPY: large-scale single-cell gene expression data analysis. <i>Genome Biology</i>, 19:15. doi:10.1186/s13059-017-1382-0</li>
<li>Wolf, F.A., Hamey, F., Plass, M. et al. (2019). PAGA: graph abstraction reconciles clustering with trajectory inference through a topology preserving map of single cells. <i>Genome Biology</i>, 20:59. doi:10.1186/s13059-019-1663-x</li>
<li>Haghverdi, L., Büttner, M., Wolf, F.A. et al. (2016). Diffusion pseudotime robustly reconstructs lineage branching. <i>Nature Methods</i>, 13:845–848. doi:10.1038/nmeth.3971</li>
<li>Bastidas-Ponce, A., Tritschler, S., Dony, L. et al. (2019). Comprehensive single cell mRNA profiling reveals a detailed roadmap for pancreatic endocrinogenesis. <i>Development</i>, 146(12):dev173849. doi:10.1242/dev.173849</li>
</ul>
</div>"""
        if "</body>" in _html_content:
            _html_content = _html_content.replace("</body>", _references_html + "\n</body>")
        else:
            _html_content += _references_html
        _pdf_path = os.path.join(output_dir, f"{agent_name}_report.pdf")
        HTML(string=_html_content).write_pdf(_pdf_path)
        if os.path.isfile(_pdf_path) and os.path.getsize(_pdf_path) > 1000:
            return _pdf_path
    except Exception as exc:
        logger.warning("[PACKAGE] PDF generation failed for %s: %s", agent_name, exc)
    return ""


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
        from ..adapters.adapter import HermesAdapter
        return HermesAdapter(workspace)
    if agent == "openclaw":
        from ..agents.openclaw_adapter import OpenClawAdapter
        return OpenClawAdapter(workspace)
    if agent == "codex":
        from ..adapters.adapter import CodexAdapter
        return CodexAdapter(workspace)
    raise RuntimeError(f"unsupported general agent backend: {agent}")
