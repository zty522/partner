"""Summarize search results — extract structured metadata from arbitrary text.

Designed as a generic atomic event: input can be HTML, plain text, or JSON
from any search source. Output is a structured JSON with paper metadata.
No domain-specific assumptions — works for PubMed, ArXiv, general web search, etc.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from ..harness_core import RobustExecutor, TaskInstance, load_harness_config

logger = logging.getLogger(__name__)

DEFAULT_SUMMARIZE_PROMPT = """从以下搜索/检索结果中提取论文/文章元数据。

原始文本（可能是 HTML、纯文本或 JSON）：
{raw_text}

输出 JSON 对象，包含：
{{
  "total": <整数，提取到的论文总数>,
  "papers": [
    {{
      "title": "论文标题",
      "pmid": "PMID 编号（如无则填空字符串）",
      "doi": "DOI（如无则填空字符串）",
      "authors": "第一作者等（可选）",
      "abstract": "摘要前500字符",
      "method_hints": ["方法关键词，如 'elastic net'"],
      "metric": "关键指标描述，如 'MAE=3.2'",
      "source_url": "来源URL（如无则填空字符串）"
    }}
  ]
}}

只输出 JSON，不要输出任何额外解释。
如果输入中没有可识别的论文/文章信息，输出 {{"total": 0, "papers": []}}。"""


async def summarize_search_results(
    raw_text: str,
    workspace: str,
    adapter: Any,
    *,
    task_instance: TaskInstance | None = None,
    config: dict[str, Any] | None = None,
    max_input_chars: int = 8000,
) -> dict[str, Any]:
    """Extract structured paper metadata from arbitrary search result text.

    Args:
        raw_text: Raw text from search (HTML, plain text, JSON).
        workspace: Partner workspace path.
        adapter: LLM adapter instance.
        task_instance: Optional TaskInstance for logging.
        config: Optional summarizer config dict.
        max_input_chars: Max characters to send to the LLM.

    Returns:
        Structured dict: {"total": int, "papers": [...]}
    """
    if not adapter or not raw_text:
        return {"total": 0, "papers": []}

    cfg = config or {}
    prompt_template = _read_summarize_prompt(workspace, cfg)
    text_sample = str(raw_text)[:max_input_chars]

    prompt = prompt_template.replace("{raw_text}", text_sample)

    robust = RobustExecutor(load_harness_config(workspace))
    result = await robust.execute(
        event_name="summarize_search",
        task_instance=task_instance or TaskInstance.create(workspace, "_summarize_placeholder"),
        operation=lambda: _adapter_chat_safe(adapter, prompt),
        on_timeout="fail_fast",
        on_failure="fail_fast",
        metadata={
            "model": cfg.get("model", "gpt-4o-mini"),
            "input_chars": len(text_sample),
        },
    )

    if not result.ok:
        logger.warning("[SUMMARIZE] LLM call failed: %s", result.error)
        return _fallback_extract(raw_text)

    try:
        parsed = json.loads(str(result.value or "{}"))
    except (json.JSONDecodeError, ValueError):
        logger.warning("[SUMMARIZE] bad JSON from LLM; using fallback extraction")
        return _fallback_extract(raw_text)

    if not isinstance(parsed, dict):
        return _fallback_extract(raw_text)
    if not isinstance(parsed.get("papers"), list):
        parsed["papers"] = []
    parsed["total"] = max(0, int(parsed.get("total", len(parsed["papers"]))))

    if task_instance:
        task_instance.append_log("summarize_search_results", {
            "total": parsed["total"],
            "paper_titles": [p.get("title", "")[:80] for p in parsed["papers"][:10]],
        })

    logger.info("[SUMMARIZE] extracted %s papers", parsed["total"])
    return parsed


def _adapter_chat_safe(adapter: Any, prompt: str) -> str:
    """Synchronous wrapper for adapter.chat."""
    if hasattr(adapter, "chat"):
        return adapter.chat(prompt, purpose="classify")
    return ""


def _fallback_extract(raw_text: str) -> dict[str, Any]:
    """Regex-based fallback when LLM is unavailable."""
    papers: list[dict[str, Any]] = []
    # Try to find PMIDs
    pmid_matches = re.findall(r"\bPMID[:\s]*(\d{5,10})\b", raw_text, re.I)
    # Try to find DOIs
    doi_matches = re.findall(r"\b(10\.\d{4,9}/[^\s,;)\]]+)", raw_text)
    # Try to find titles (lines that look like paper titles)
    lines = raw_text.split("\n")
    for line in lines:
        line = line.strip()
        if not line or len(line) < 20:
            continue
        # Heuristic: a citation-like line
        if re.search(r"\b(20\d{2})\b", line) and re.search(r"[A-Z][a-z]+", line):
            papers.append({
                "title": line[:200],
                "pmid": "",
                "doi": "",
                "authors": "",
                "abstract": "",
                "method_hints": [],
                "metric": "",
                "source_url": "",
            })

    unique_pmids = list(set(pmid_matches))
    unique_dois = list(set(doi_matches))

    return {
        "total": max(len(papers), len(unique_pmids)),
        "papers": papers[:20],
        "_fallback_pmids": unique_pmids[:10],
        "_fallback_dois": unique_dois[:10],
    }


def _read_summarize_prompt(workspace: str, config: dict[str, Any]) -> str:
    """Read the summarize prompt template."""
    rel_path = str(config.get("prompt_template", "prompts/summarize_search.txt"))
    for path in (
        os.path.join(workspace, rel_path),
        os.path.join(os.path.dirname(__file__), "..", rel_path),
    ):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                continue
    return DEFAULT_SUMMARIZE_PROMPT
