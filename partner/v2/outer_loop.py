"""
outer_loop.py — GitHub search, web search, knowledge learning

Handler functions:
    atomic_github_search(ctx, params)  -> dict
    atomic_github_clone(ctx, params)   -> dict
    atomic_v2_web_search(ctx, params)  -> dict
    atomic_knowledge_learn(ctx, params) -> dict

All handlers return a dict with an "ok" key (bool).
"""

import os
import json
import time
import uuid
import datetime
import subprocess
from typing import Any

# ---------------------------------------------------------------------------
# Optional imports — gracefully degrade if a library is missing
# ---------------------------------------------------------------------------
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_workspace(path: str) -> str:
    """Return an absolute path under the workspace (creates dirs if needed)."""
    ws = os.environ.get("WORKSPACE_PATH", "/mnt/e/work")
    full = os.path.join(ws, path)
    os.makedirs(full, exist_ok=True)
    return full


def _v2_knowledge_path() -> str:
    """Return the absolute path to the knowledge_v2.json file."""
    return _ensure_workspace("partner/state") + "/knowledge_v2.json"


def _load_knowledge() -> list[dict]:
    """Load existing knowledge records (empty list if missing/corrupt)."""
    path = _v2_knowledge_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _save_knowledge(records: list[dict]) -> None:
    """Write knowledge records atomically via a temp-file swap."""
    path = _v2_knowledge_path()
    tmp = path + ".tmp." + str(uuid.uuid4())[:8]
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 1. atomic_github_search
# ---------------------------------------------------------------------------

def atomic_github_search(ctx: Any, params: dict) -> dict:
    """Search GitHub repositories.

    Params:
        query      (str) — search query (required)
        language   (str) — optional language filter (e.g. "python")
        sort       (str) — sort criterion (default "stars")
        max_results(int) — max results to return (default 5)

    Returns:
        {"ok": True, "results": [...], "count": int}
        or {"ok": False, "error": "..."}
    """
    if not HAS_REQUESTS:
        return {"ok": False, "error": "requests library is not available"}

    query = params.get("query", "").strip()
    if not query:
        return {"ok": False, "error": "query parameter is required"}

    language = params.get("language")
    sort = params.get("sort", "stars")
    max_results = int(params.get("max_results", 5))

    # Build the query string
    q = query
    if language:
        q += f"+language:{language}"

    url = "https://api.github.com/search/repositories"
    headers = {"Accept": "application/vnd.github.v3+json"}
    resp = requests.get(url, headers=headers, params={"q": q, "sort": sort, "per_page": max_results})

    if resp.status_code == 403:
        # Rate-limit info
        reset_ts = resp.headers.get("X-RateLimit-Reset")
        msg = "GitHub API rate limit exceeded"
        if reset_ts:
            try:
                wait = int(reset_ts) - int(time.time())
                msg += f" — resets in {wait}s"
            except ValueError:
                pass
        return {"ok": False, "error": msg}

    if resp.status_code != 200:
        return {"ok": False, "error": f"GitHub API returned {resp.status_code}: {resp.text[:300]}"}

    data = resp.json()
    items = data.get("items", [])

    results = []
    for repo in items[:max_results]:
        results.append({
            "name": repo.get("name", ""),
            "full_name": repo.get("full_name", ""),
            "url": repo.get("html_url", ""),
            "description": repo.get("description") or "",
            "stars": repo.get("stargazers_count", 0),
            "language": repo.get("language"),
            "topics": repo.get("topics", []),
        })

    return {"ok": True, "results": results, "count": len(results)}


# ---------------------------------------------------------------------------
# 2. atomic_github_clone
# ---------------------------------------------------------------------------

def atomic_github_clone(ctx: Any, params: dict) -> dict:
    """Clone a GitHub repository with depth=1.

    Params:
        repo_url    (str) — remote repository URL (required)
        destination (str) — local path to clone into (required)
        branch      (str) — branch name (default "main")

    Returns:
        {"ok": True, "path": str, "output": str}
        or {"ok": False, "error": "..."}
    """
    repo_url = params.get("repo_url", "").strip()
    destination = params.get("destination", "").strip()
    branch = params.get("branch", "main")

    if not repo_url:
        return {"ok": False, "error": "repo_url parameter is required"}
    if not destination:
        return {"ok": False, "error": "destination parameter is required"}

    # Resolve destination relative to workspace if not absolute
    if not os.path.isabs(destination):
        destination = os.path.join(os.environ.get("WORKSPACE_PATH", "/mnt/e/work"), destination)

    try:
        result = subprocess.run(
            ["git", "clone", "--depth=1", "-b", branch, repo_url, destination],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "git clone timed out after 300 seconds"}
    except FileNotFoundError:
        return {"ok": False, "error": "git is not installed or not on PATH"}
    except Exception as exc:
        return {"ok": False, "error": f"subprocess error: {exc}"}

    if result.returncode != 0:
        return {
            "ok": False,
            "error": result.stderr.strip() or result.stdout.strip() or f"git clone failed (exit {result.returncode})",
        }

    return {"ok": True, "path": destination, "output": result.stdout.strip()}


# ---------------------------------------------------------------------------
# 3. atomic_v2_web_search
# ---------------------------------------------------------------------------

def atomic_v2_web_search(ctx: Any, params: dict) -> dict:
    """Search the web or delegate to GitHub search.

    Params:
        query       (str)  — search query (required)
        num_results (int)  — max results to return (default 5)
        source      (str)  — "web" (DuckDuckGo HTML) or "github" (default "web")

    Returns:
        {"ok": True, "results": [{title, url, snippet}], "count": int}
        or {"ok": False, "error": "..."}
    """
    query = params.get("query", "").strip()
    if not query:
        return {"ok": False, "error": "query parameter is required"}

    source = params.get("source", "web")
    num_results = int(params.get("num_results", 5))

    if source == "github":
        # Delegate to the GitHub search handler, adapting its result shape
        gh_result = atomic_github_search(ctx, {"query": query, "max_results": num_results})
        if not gh_result.get("ok"):
            return gh_result

        results = []
        for repo in gh_result.get("results", []):
            results.append({
                "title": repo.get("full_name", repo.get("name", "")),
                "url": repo.get("url", ""),
                "snippet": repo.get("description", ""),
            })

        return {"ok": True, "results": results, "count": len(results)}

    # --- DuckDuckGo HTML search ---
    if not HAS_REQUESTS:
        return {"ok": False, "error": "requests library is not available"}

    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            timeout=15,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "DuckDuckGo search timed out"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "DuckDuckGo connection error — check network"}
    except Exception as exc:
        return {"ok": False, "error": f"DuckDuckGo request failed: {exc}"}

    if resp.status_code != 200:
        return {"ok": False, "error": f"DuckDuckGo returned HTTP {resp.status_code}"}

    # Parse HTML results
    if HAS_BS4:
        soup = BeautifulSoup(resp.text, "html.parser")
        result_divs = soup.select(".result")
        results = []
        for div in result_divs[:num_results]:
            link_tag = div.select_one(".result__title a")
            snippet_tag = div.select_one(".result__snippet")

            title = link_tag.get_text(strip=True) if link_tag else ""
            url = link_tag.get("href", "") if link_tag else ""
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            # DuckDuckGo wraps redirect URLs — extract actual URL
            if "//duckduckgo.com/l/?uddg=" in url:
                from urllib.parse import unquote, urlparse, parse_qs
                parsed = urlparse(url)
                qs = parse_qs(parsed.query)
                decoded = qs.get("uddg", [None])[0]
                if decoded:
                    url = unquote(decoded)
            elif "&uddg=" in url:
                # alternate format
                from urllib.parse import unquote
                try:
                    url = unquote(url.split("&uddg=", 1)[1].split("&", 1)[0])
                except IndexError:
                    pass

            results.append({"title": title, "url": url, "snippet": snippet})

        return {"ok": True, "results": results, "count": len(results)}
    else:
        # Fallback: crude regex extraction when BeautifulSoup is unavailable
        import re
        results = []
        # Very basic link extraction
        link_pattern = re.compile(r'<a[^>]+class="result__title"[^>]*>.*?href="([^"]+)".*?</a>', re.DOTALL)
        snippet_pattern = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)
        title_pattern = re.compile(r'<a[^>]+class="result__title"[^>]*>(.*?)</a>', re.DOTALL)

        links = link_pattern.findall(resp.text)
        titles = [re.sub(r'<[^>]+>', '', t).strip() for t in title_pattern.findall(resp.text)]
        snippets = [re.sub(r'<[^>]+>', '', s).strip() for s in snippet_pattern.findall(resp.text)]

        for i in range(min(num_results, len(links))):
            url = links[i] if i < len(links) else ""
            title = titles[i] if i < len(titles) else ""
            snippet = snippets[i] if i < len(snippets) else ""
            results.append({"title": title, "url": url, "snippet": snippet})

        return {"ok": True, "results": results, "count": len(results)}


# ---------------------------------------------------------------------------
# 4. atomic_knowledge_learn
# ---------------------------------------------------------------------------

def atomic_knowledge_learn(ctx: Any, params: dict) -> dict:
    """Record a knowledge learning entry.

    Params:
        topic        (str)   — topic name (required)
        sources      (list)  — list of source URLs/refs (required)
        key_insights (str, optional) — user-provided insight text
        save         (bool)  — persist to disk immediately (default True)

    Returns:
        {"ok": True, "record_id": str}
        or {"ok": False, "error": "..."}
    """
    topic = params.get("topic", "").strip()
    if not topic:
        return {"ok": False, "error": "topic parameter is required"}

    sources = params.get("sources", [])
    if not isinstance(sources, list):
        return {"ok": False, "error": "sources must be a list"}

    key_insights = params.get("key_insights")
    save = params.get("save", True)

    record_id = str(uuid.uuid4())

    record = {
        "id": record_id,
        "topic": topic,
        "sources": sources,
        "insights": key_insights or "",
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
    }

    if save:
        try:
            records = _load_knowledge()
            records.append(record)
            _save_knowledge(records)
        except OSError as exc:
            return {"ok": False, "error": f"Failed to write knowledge file: {exc}"}

    return {"ok": True, "record_id": record_id}


# ---------------------------------------------------------------------------
# Handler registry (optional, for dynamic dispatch)
# ---------------------------------------------------------------------------

HANDLERS: dict[str, callable] = {
    "github_search": atomic_github_search,
    "github_clone": atomic_github_clone,
    "web_search": atomic_v2_web_search,
    "knowledge_learn": atomic_knowledge_learn,
}


def run_handler(name: str, ctx: Any, params: dict) -> dict:
    """Dispatch to a named handler (convenience for programmatic use)."""
    handler = HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"Unknown handler: {name}"}
    return handler(ctx, params)
