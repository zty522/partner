"""ADR 0062 §2 — Partner 通用外部检索（web_fetch + cache）。

This wraps hermes_external_learning.process.md 6-step fetch into a
single Python-callable API so partner-runtime's active learning can
fetch external material on demand (vs. relying on Hermes as a subprocess).

The companion module ``partner.knowledge.searcher`` already covers the
academic-arXiv / Semantic Scholar / Crossref / PubMed pipeline; this
module fills the generic web-fetch gap with a 12-hour cache and works
in any sandbox without a separate Hermes process.

Boundary (matches ADR 0062 §4):
- No writes to partner/<pkg>/ code, no QQ push, no manual_stable flip
- Fetched artefacts go to share/external_cache/<source>/<sha-of-url>.json
- Cache hit returns the same artefact, no network call
- Failure: empty list, never raises (active learning must degrade open)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from .storage import workspace_root


CACHE_TTL_SECONDS = 12 * 60 * 60
FETCH_TIMEOUT = 20  # seconds per request
USER_AGENT = "PartnerResearchBot/0.9 (ADR 0062; mailto:partner@research.ai)"


def _cache_dir(workspace: Path) -> Path:
    return workspace / "share/external_cache"


def _cache_path(workspace: Path, source: str, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    out = _cache_dir(workspace) / source
    out.mkdir(parents=True, exist_ok=True)
    return out / f"{digest}.json"


def _strip_html(text: str) -> str:
    """Cheap HTML → text. The retrieval pipeline upgrades this when needed."""
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _http_get(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        body = resp.read(800_000).decode("utf-8", "replace")
        content_type = resp.headers.get("Content-Type", "")
    text = _strip_html(body) if "html" in content_type.lower() else body
    return {"url": url, "title": _extract_title(text), "snippet": text[:1500],
            "fetched_at": time.time(), "content_type": content_type, "byte_size": len(body)}


def _extract_title(text: str) -> str:
    if not text:
        return ""
    first_line = text.split("\n", 1)[0].strip()
    return first_line[:120] if first_line else ""


def _cache_valid(record: dict[str, Any]) -> bool:
    return (time.time() - float(record.get("fetched_at", 0))) < CACHE_TTL_SECONDS


def fetch(workspace: str | Path, url: str, *, force: bool = False) -> dict[str, Any]:
    """Fetch one URL with 12h cache; returns a normalised record."""
    ws = Path(workspace_root(str(workspace)))
    target = _cache_path(ws, "http", url)
    if target.exists() and not force:
        try:
            cached = json.loads(target.read_text(encoding="utf-8"))
            if _cache_valid(cached):
                cached["cache_hit"] = True
                return cached
        except (OSError, ValueError):
            pass
    record = _http_get(url)
    target.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    record["cache_hit"] = False
    return record


def search(workspace: str | Path, queries: Iterable[str],
           *, sources: tuple[str, ...] = ("arxiv",),
           limit_per_source: int = 5,
           use_cache: bool = True) -> list[dict[str, Any]]:
    """Thin wrapper over partner.knowledge.searcher using local cache as front-end.

    ``sources`` accepts whatever ``partner.knowledge.searcher.search`` accepts.
    Cache only applies to our own ``fetch`` results, not to upstream APIs
    (which have their own rate-limit logic).
    """
    from ..knowledge.searcher import search as _native_search
    out: list[dict[str, Any]] = []
    for q in queries:
        try:
            results = _native_search(q, max_results=limit_per_source, workspace=str(workspace))
        except Exception:
            results = []
        for r in results:
            r["query"] = q
            r["source"] = r.get("source") or "searcher"
            out.append(r)
        if not use_cache:
            break
    return out[: limit_per_source * len(tuple(queries))]


def harvest_for_topic(workspace: str | Path, topic: str,
                      *, max_results: int = 5) -> list[dict[str, Any]]:
    """Active-learning driver: fetch a few external snippets for one topic.

    Used by ``partner.learn.learn_from_hermes`` so each failed task can
    pull fresh external context instead of looping on past receipts.
    """
    queries = [topic]
    if len(topic) > 3:
        eng = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", topic)
        if eng:
            queries.append(" ".join(eng[:5]))
    return search(workspace, queries, limit_per_source=max_results)
