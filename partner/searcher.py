"""Searcher — 学术搜索模块。

使用 Python requests 直接调用免费学术搜索 API，
不经过 shell/curl/hermes 子进程，避免安全软件拦截。

当前后端：Semantic Scholar API（免认证，无需 API Key）
备用后端：Crossref API（当 S2 不可用时）
"""

import logging
import json
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Semantic Scholar API
S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_BULK_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"

# Crossref API
CROSSREF_URL = "https://api.crossref.org/works"

# ArXiv API
ARXIV_URL = "http://export.arxiv.org/api/query"

SEARCH_TIMEOUT = 30  # seconds


def search(topic: str, max_results: int = 5, workspace: str = "") -> List[Dict]:
    """搜索学术文献，按优先级尝试多个后端。

    在尝试学术 API 之前，先从 dialog 上下文检查是否有用户已提供的信息。

    Args:
        topic: 搜索主题
        max_results: 最大返回结果数
        workspace: Partner 工作区路径（可选）。提供后可先查 dialog 上下文。

    Returns:
        List[Dict]，每个 dict 包含 title, authors, year, url, abstract, source
    """
    # 0. 优先从 dialog 上下文获取用户已提供的信息
    if workspace:
        try:
            from .context_broker import ContextBroker
            broker = ContextBroker(workspace)
            recent = broker.collect_recent_dialogs(hours=48)
            if recent:
                facts = broker.extract_project_facts(recent)
                dialog_results = _build_dialog_results(topic, facts)
                if dialog_results:
                    logger.info(
                        f"[Searcher] Found {len(dialog_results)} result(s) "
                        f"from dialog context for '{topic}'"
                    )
                    # Record dialog findings
                    _record_knowledge(workspace, topic, dialog_results, "dialog")
                    return dialog_results
        except Exception as e:
            logger.debug(f"[Searcher] Dialog context check failed (non-fatal): {e}")

    # 1. 优先 Semantic Scholar
    try:
        results = _search_semantic_scholar(topic, max_results)
        if results:
            _record_knowledge(workspace, topic, results, "semantic_scholar")
            return results
    except Exception as e:
        logger.warning(f"[Searcher] Semantic Scholar failed: {e}")

    # 降级：Crossref
    try:
        results = _search_crossref(topic, max_results)
        if results:
            _record_knowledge(workspace, topic, results, "crossref")
            return results
    except Exception as e:
        logger.warning(f"[Searcher] Crossref failed: {e}")

    # 降级：ArXiv
    try:
        results = _search_arxiv(topic, max_results)
        if results:
            _record_knowledge(workspace, topic, results, "arxiv")
            return results
    except Exception as e:
        logger.warning(f"[Searcher] ArXiv failed: {e}")

    return []


def _search_semantic_scholar(query: str, limit: int = 5) -> List[Dict]:
    """Semantic Scholar API 搜索。

    免费、无需认证。限制: 100 次/分钟。
    """
    import requests

    params = {
        "query": query,
        "limit": min(limit, 10),
        "fields": "title,authors,year,url,abstract,externalIds",
    }
    resp = requests.get(S2_SEARCH_URL, params=params, timeout=SEARCH_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for paper in data.get("data", []):
        authors = [a.get("name", "") for a in paper.get("authors", []) if a.get("name")]
        results.append({
            "title": paper.get("title", ""),
            "authors": authors[:5],  # 最多5个作者
            "year": paper.get("year"),
            "url": paper.get("url", ""),
            "abstract": (paper.get("abstract") or "")[:500],
            "source": "semantic_scholar",
        })

    logger.info(f"[Searcher] Semantic Scholar: {len(results)} results for '{query}'")
    return results


def _search_crossref(query: str, limit: int = 5) -> List[Dict]:
    """Crossref API 搜索（降级方案）。"""
    import requests

    params = {
        "query": query,
        "rows": min(limit, 10),
        "sort": "relevance",
        "order": "desc",
    }
    headers = {"User-Agent": "Partner/0.4 (mailto:partner@research.ai)"}
    resp = requests.get(CROSSREF_URL, params=params, headers=headers, timeout=SEARCH_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for item in data.get("message", {}).get("items", []):
        authors = [a.get("given", "") + " " + a.get("family", "") for a in item.get("author", [])]
        results.append({
            "title": item.get("title", [""])[0],
            "authors": [a.strip() for a in authors if a.strip()][:5],
            "year": item.get("published-print", {}).get("date-parts", [[None]])[0][0]
                     or item.get("issued", {}).get("date-parts", [[None]])[0][0],
            "url": item.get("URL", ""),
            "abstract": (item.get("abstract") or "")[:500],
            "source": "crossref",
        })

    logger.info(f"[Searcher] Crossref: {len(results)} results for '{query}'")
    return results


def _search_arxiv(query: str, limit: int = 5) -> List[Dict]:
    """ArXiv API 搜索（最终降级）。"""
    import requests
    import re

    params = {
        "search_query": f"all:{query}",
        "max_results": min(limit, 10),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    resp = requests.get(ARXIV_URL, params=params, timeout=SEARCH_TIMEOUT)
    resp.raise_for_status()

    results = []
    # ArXiv returns XML
    entries = re.findall(r"<entry>(.*?)</entry>", resp.text, re.DOTALL)
    for entry in entries[:limit]:
        title = _extract_xml(entry, "title").replace("\n", " ").strip()
        authors_raw = re.findall(r"<name>(.*?)</name>", entry)
        abstract = _extract_xml(entry, "summary").replace("\n", " ").strip()[:500]
        arxiv_id = _extract_xml(entry, "id").strip()
        year = ""
        try:
            published = _extract_xml(entry, "published").strip()[:4]
            if published.isdigit():
                year = int(published)
        except (ValueError, IndexError):
            pass

        results.append({
            "title": title,
            "authors": authors_raw[:5],
            "year": year,
            "url": arxiv_id,
            "abstract": abstract,
            "source": "arxiv",
        })

    logger.info(f"[Searcher] ArXiv: {len(results)} results for '{query}'")
    return results


def _extract_xml(xml_str: str, tag: str) -> str:
    """从 XML 片段中提取标签内容。"""
    import re
    m = re.search(f"<{tag}[^>]*>(.*?)</{tag}>", xml_str, re.DOTALL)
    return m.group(1) if m else ""


def _build_dialog_results(topic: str, facts: Dict) -> List[Dict]:
    """从 dialog 事实中构造类似搜索结果的条目。

    Args:
        topic: 搜索主题
        facts: ContextBroker.extract_project_facts() 返回的事实字典

    Returns:
        格式与 search() 一致的 List[Dict]，source="dialog"
    """
    results = []

    # Build entry from metrics
    metrics = facts.get("metrics", {})
    if metrics:
        metric_lines = ", ".join(f"{k}={v}" for k, v in metrics.items())
        results.append({
            "title": f"对话中的指标 — {topic}",
            "authors": [],
            "year": "",
            "url": "",
            "abstract": f"从最近对话中提取的实验指标：{metric_lines}",
            "source": "dialog",
        })

    # Build entry from files
    files = facts.get("files", [])
    if files:
        results.append({
            "title": f"对话中提到的相关文件 — {topic}",
            "authors": [],
            "year": "",
            "url": "",
            "abstract": "用户项目中涉及的文件：\n" + "\n".join(files[:8]),
            "source": "dialog",
        })

    # Build entry from issues
    issues = facts.get("issues", [])
    if issues:
        results.append({
            "title": f"对话中识别的问题 — {topic}",
            "authors": [],
            "year": "",
            "url": "",
            "abstract": "用户反馈的问题：\n" + "\n".join(issues[:5]),
            "source": "dialog",
        })

    # Build entry from raw snippets
    snippets = facts.get("raw_snippets", [])
    if snippets:
        results.append({
            "title": f"对话摘要 — {topic}",
            "authors": [],
            "year": "",
            "url": "",
            "abstract": snippets[-1][:500],
            "source": "dialog",
        })

    return results


def format_results(results: List[Dict], max_items: int = 3) -> str:
    """将搜索结果格式化为文本。"""
    if not results:
        return ""

    lines = []
    for i, r in enumerate(results[:max_items], 1):
        authors = ", ".join(r.get("authors", []))
        year = r.get("year", "")
        title = r.get("title", "")
        abstract = r.get("abstract", "")[:200]
        source = r.get("source", "")

        parts = [f"{i}. {title}"]
        if authors:
            parts.append(f"   作者: {authors}")
        if year:
            parts.append(f"   年份: {year}")
        if abstract:
            parts.append(f"   摘要: {abstract}")
        parts.append(f"   来源: {source}")
        lines.append("\n".join(parts))

    return "\n\n".join(lines)


def _record_knowledge(workspace: str, topic: str, results: List[Dict], source: str) -> None:
    """Record search results into the Recorder system.

    Args:
        workspace: Partner workspace path
        topic: The search topic (used as project name)
        results: Search result dicts from any backend
        source: Source identifier (e.g. "semantic_scholar", "crossref", "dialog")
    """
    if not workspace or not results:
        return
    try:
        from .recorder import Recorder

        recorder = Recorder(workspace)
        # Use topic as the project name for recording
        project_name = topic[:60] if topic else "search"

        # Record each result individually as knowledge
        for r in results[:5]:  # Limit to top 5 results
            title = r.get("title", "") or ""
            authors = ", ".join(r.get("authors", [])[:3])
            year = r.get("year", "")
            abstract = (r.get("abstract", "") or "")[:300]
            url = r.get("url", "")

            content_parts = [title]
            if authors:
                content_parts.append(f"Authors: {authors}")
            if year:
                content_parts.append(f"Year: {year}")
            if abstract:
                content_parts.append(f"Abstract: {abstract}")
            if url:
                content_parts.append(f"URL: {url}")

            content = " | ".join(content_parts)

            recorder.add_knowledge(
                project=project_name,
                entry_type="search_result",
                content=content,
                source=source,
                confidence=0.7,
            )

        logger.info(
            f"[Searcher] Recorded {min(len(results), 5)} knowledge entries "
            f"from '{source}' for topic '{topic[:40]}'"
        )
    except Exception as e:
        logger.debug(f"[Searcher] Failed to record knowledge (non-fatal): {e}")
