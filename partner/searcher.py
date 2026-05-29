"""Searcher — 学术搜索模块（对话优先版 v2）。

使用 Python requests 直接调用免费学术搜索 API，
不经过 shell/curl/hermes 子进程，避免安全软件拦截。

搜索策略（优先级）：
1. 对话上下文（context_broker）— 用户已提供的信息优先
2. Semantic Scholar API（免认证，无需 API Key）
3. Crossref API（当 S2 不可用时）
4. ArXiv API（最终降级）

当前后端：Semantic Scholar API（免认证，无需 API Key）
备用后端：Crossref API（当 S2 不可用时）
"""

import logging
import json
from typing import List, Dict, Optional, Callable
from datetime import datetime

logger = logging.getLogger(__name__)

# 对话上下文提供者（由 bridge 在初始化时注册）
_context_provider: Optional[Callable[[str], Dict]] = None

# Semantic Scholar API
S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_BULK_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"

# Crossref API
CROSSREF_URL = "https://api.crossref.org/works"

# ArXiv API
ARXIV_URL = "http://export.arxiv.org/api/query"

SEARCH_TIMEOUT = 30  # seconds


def set_context_provider(provider: Callable[[str], Dict]):
    """注册对话上下文提供者（由 QQ bridge 初始化时调用）。

    provider: fn(query) -> Dict 包含 project_path, issues, files, scripts 等
    """
    global _context_provider
    _context_provider = provider
    logger.info("[Searcher] 对话上下文提供者已注册")


def search(topic: str, max_results: int = 5) -> List[Dict]:
    """搜索学术文献，按优先级尝试多个后端。

    策略：
    1. 如果对话上下文提供者返回了有效信息（路径、行号、指标），
       直接返回这些上下文作为"搜索结果"，标记 source: "dialog"
    2. 否则按序尝试学术 API

    Args:
        topic: 搜索主题
        max_results: 最大返回结果数

    Returns:
        List[Dict]，每个 dict 包含 title, authors, year, url, abstract, source
    """
    # 步骤1：检查对话上下文
    dialog_results = _search_dialog_context(topic)
    if dialog_results:
        logger.info(f"[Searcher] 使用对话上下文作为搜索结果 ({len(dialog_results)} 条)")
        return dialog_results

    # 步骤2：学术 API
    try:
        results = _search_semantic_scholar(topic, max_results)
        if results:
            return results
    except Exception as e:
        logger.warning(f"[Searcher] Semantic Scholar failed: {e}")

    # 降级：Crossref
    try:
        results = _search_crossref(topic, max_results)
        if results:
            return results
    except Exception as e:
        logger.warning(f"[Searcher] Crossref failed: {e}")

    # 降级：ArXiv
    try:
        results = _search_arxiv(topic, max_results)
        if results:
            return results
    except Exception as e:
        logger.warning(f"[Searcher] ArXiv failed: {e}")

    return []


def _search_dialog_context(topic: str) -> List[Dict]:
    """从对话上下文中搜索（优先于网络搜索）。

    如果有对话上下文提供者且返回了信息，直接封装为搜索结果返回。

    Returns:
        非空列表表示有对话上下文可用；空列表表示需要走网络搜索
    """
    if not _context_provider:
        return []

    try:
        ctx = _context_provider(topic)
        if not ctx or not ctx.get("has_relevant_context"):
            return []

        results = []

        # 如果有项目路径 + 问题，构造一个"搜索结果"
        project_path = ctx.get("project_path", "")
        issues = ctx.get("issues", [])
        files = ctx.get("files", [])
        scripts = ctx.get("scripts", [])
        metrics = ctx.get("metrics", {})
        line_numbers = ctx.get("line_numbers", [])

        # 构造对话知识条目
        parts = ["【从对话中获取的项目信息】"]
        if project_path:
            parts.append(f"项目路径: {project_path}")
        if metrics:
            parts.append(f"已知指标: {', '.join(f'{k}={v}' for k, v in metrics.items())}")
        if issues:
            parts.append(f"已知问题: {', '.join(issues)}")
        if line_numbers:
            parts.append(f"相关行号: {', '.join(str(n) for n in line_numbers)}")
        if files:
            parts.append(f"相关文件: {', '.join(files)}")
        if scripts:
            parts.append(f"脚本: {', '.join(scripts)}")

        results.append({
            "title": f"对话上下文: {project_path or topic}",
            "authors": ["用户"],
            "year": datetime.now().year,
            "url": project_path or "",
            "abstract": "\n".join(parts),
            "source": "dialog",
            "_dialog_context": ctx,  # 内部标记
        })

        # 如果有原始对话片段，追加为额外结果
        for i, snippet in enumerate(ctx.get("raw_snippets", [])[:3]):
            results.append({
                "title": f"对话参考 #{i + 1}",
                "authors": ["用户"],
                "year": datetime.now().year,
                "url": "",
                "abstract": snippet[:300],
                "source": "dialog",
            })

        return results

    except Exception as e:
        logger.warning(f"[Searcher] 对话上下文搜索失败: {e}")
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
