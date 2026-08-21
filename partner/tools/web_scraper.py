"""Web scraper for Partner (Sprint 7). Fetch web pages, arXiv abstracts, GitHub repos."""
import os, re, logging, time
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def fetch_page(url: str, timeout: int = 20, max_chars: int = 50000) -> dict:
    """Fetch a web page and extract readable text content."""
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Partner Research Agent; Sprint7)"
        }, allow_redirects=True)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Remove script, style, nav, footer
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        
        text = soup.get_text(separator="\n", strip=True)
        # Collapse blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text[:max_chars]
        
        title = soup.title.string.strip() if soup.title else ""
        
        return {
            "ok": True,
            "url": url,
            "title": title,
            "status": resp.status_code,
            "text": text,
            "text_length": len(text),
        }
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)}


def search_arxiv(query: str, max_results: int = 5) -> dict:
    """Search arXiv via web scraping (fallback when API is unreachable)."""
    try:
        search_url = f"https://arxiv.org/search/?query={requests.utils.quote(query)}&searchtype=all&start=0"
        
        resp = requests.get(search_url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Partner Research Agent; Sprint7)"
        })
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        
        for li in soup.select("li.arxiv-result")[:max_results]:
            title_el = li.select_one("p.title")
            title = title_el.text.strip() if title_el else ""
            
            authors_el = li.select_one("p.authors")
            authors = authors_el.text.strip().replace("Authors:", "").strip() if authors_el else ""
            
            abstract_el = li.select_one("span.abstract-full")
            abstract = abstract_el.text.strip() if abstract_el else ""
            
            link_el = li.select_one("p.list-title a")
            link = "https://arxiv.org" + link_el["href"] if link_el and link_el.get("href") else ""
            
            if title:
                results.append({
                    "title": title[:200],
                    "authors": authors[:200],
                    "abstract": abstract[:500],
                    "link": link,
                })
        
        return {"ok": True, "query": query, "count": len(results), "results": results}
    
    except Exception as e:
        return {"ok": False, "query": query, "error": str(e)[:200]}


def fetch_github_readme(repo_url: str) -> dict:
    """Fetch a GitHub repository's README."""
    try:
        # Normalize URL to raw README
        if "github.com" not in repo_url:
            return {"ok": False, "error": "Not a GitHub URL"}
        
        # Try raw README paths
        readme_paths = ["README.md", "readme.md", "Readme.md", "README.rst"]
        
        for path in readme_paths:
            raw_url = repo_url.rstrip("/").replace("github.com", "raw.githubusercontent.com") + f"/main/{path}"
            try:
                resp = requests.get(raw_url, timeout=15)
                if resp.status_code == 200:
                    return {
                        "ok": True,
                        "repo_url": repo_url,
                        "raw_url": raw_url,
                        "text": resp.text[:50000],
                        "text_length": len(resp.text),
                    }
            except Exception:
                continue
        
        # Try master branch
        for path in readme_paths:
            raw_url = repo_url.rstrip("/").replace("github.com", "raw.githubusercontent.com") + f"/master/{path}"
            try:
                resp = requests.get(raw_url, timeout=15)
                if resp.status_code == 200:
                    return {"ok": True, "repo_url": repo_url, "raw_url": raw_url, "text": resp.text[:50000], "text_length": len(resp.text)}
            except Exception:
                continue
        
        return {"ok": False, "repo_url": repo_url, "error": "README not found (tried main/master branches)"}
    
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def fetch_github_readme_curl(repo_url: str) -> dict:
    """Fetch GitHub README using curl (works when Python requests blocked)."""
    import subprocess
    
    # Normalize to raw GitHub URL
    repo = repo_url.rstrip("/").replace("https://github.com/", "")
    branches = ["main", "master"]
    readmes = ["README.md", "readme.md", "README.rst"]
    
    for branch in branches:
        for readme in readmes:
            raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{readme}"
            try:
                r = subprocess.run(
                    ["curl", "--noproxy", "*", "-s", "-m", "15", "-L", raw_url],
                    capture_output=True, text=True, timeout=20
                )
                if r.returncode == 0 and len(r.stdout) > 50:
                    return {
                        "ok": True,
                        "repo_url": repo_url,
                        "raw_url": raw_url,
                        "text": r.stdout[:50000],
                        "text_length": len(r.stdout),
                    }
            except Exception:
                continue
    
    return {"ok": False, "repo_url": repo_url, "error": "README not found via curl (tried main/master)"}
