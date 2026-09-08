"""LLM-guided, evidence-grounded external research Event.

The model chooses questions, judges candidate value and synthesises ideas.
Deterministic code owns acquisition, provenance, novelty and file safety.  A
model response alone can never claim that a repository or paper was fetched.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from partner.governance.models import now_iso
from partner.governance.storage import append_jsonl, workspace_root


GITHUB_SEARCH = "https://api.github.com/search/repositories"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
ARXIV_SEARCH = "https://export.arxiv.org/api/query"
CROSSREF_SEARCH = "https://api.crossref.org/works"
USER_AGENT = "PartnerResearchBot/1.0 (external active learning)"
DOMAIN_ANCHORS = (
    "llm agent", "language model agent", "agentic", "tool use", "tool-use",
    "agent harness", "self-improv", "self evol", "reinforcement learning agent",
    "continual learning agent", "test-time learning",
)


def _context(ctx: Any) -> tuple[Path, Path]:
    workspace = str(getattr(ctx, "workspace", "") or "")
    task = getattr(ctx, "task_instance", None)
    working = str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "") or "")
    root = workspace_root(workspace or working)
    task_dir = Path(working or root / "instances/04/state/external_learning")
    task_dir.mkdir(parents=True, exist_ok=True)
    return root, task_dir


def _json_object(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S).strip()
    start = cleaned.find("{")
    if start < 0:
        return {}
    depth = 0
    for index in range(start, len(cleaned)):
        if cleaned[index] == "{":
            depth += 1
        elif cleaned[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(cleaned[start:index + 1])
                    return value if isinstance(value, dict) else {}
                except (TypeError, ValueError):
                    return {}
    return {}


def _clean_markdown(raw: str) -> str:
    value = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S | re.I).strip()
    heading = re.search(r"(?m)^#\s+", value)
    return value[heading.start():].strip() if heading else value


def _llm(prompt: str, *, max_tokens: int = 5000,
         purpose: str = "classify") -> str:
    from partner.adapters.direct_api import chat
    return chat(prompt, purpose=purpose, max_tokens=max_tokens,
                temperature=0.25, timeout=150)


def _seen_urls(root: Path) -> set[str]:
    path = root / "external/insights/discovery_index.jsonl"
    seen: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            for url in value.get("source_urls") or []:
                if str(url).strip():
                    seen.add(str(url).strip())
    except (OSError, TypeError, ValueError):
        pass
    return seen


def _record_failure(root: Path, *, status: str, detail: dict[str, Any]) -> None:
    append_jsonl(root / "external/insights/discovery_failures.jsonl", {
        "created_at": now_iso(), "status": status, **detail,
    })


def _domain_relevant_paper(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ("title", "abstract", "venue")).lower()
    readable = len(str(row.get("abstract") or "").strip()) >= 180 or bool(row.get("pdf_url"))
    return readable and any(anchor in text for anchor in DOMAIN_ANCHORS)


def _query_plan(root: Path, topic: str, recent_titles: list[str]) -> tuple[dict[str, Any], str]:
    prompt = f"""你是 Partner 的外部知识主动学习研究员。目标不是泛搜，而是选择最可能改变 Agent Harness、
主动学习或可验证自进化设计的未知证据。主题：{topic}
最近已读标题：{json.dumps(recent_titles[-12:], ensure_ascii=False)}
请输出严格 JSON：
{{"github_query":"英文 GitHub 搜索词","paper_query":"英文论文搜索词","knowledge_gap":"一个可证伪知识缺口","selection_criteria":["标准"]}}
查询必须具体、彼此互补，不要重复 DeepSeek/Codex/Hermes/OpenClaw 四套已知 Harness 的普通介绍。"""
    raw = _llm(prompt, max_tokens=2200, purpose="classify")
    plan = _json_object(raw)
    plan.setdefault("github_query", "LLM agent harness event sourcing self improvement")
    plan.setdefault("paper_query", "LLM agent active learning self improvement runtime feedback")
    plan.setdefault("knowledge_gap", "Which runtime feedback mechanism changes a later agent action?")
    plan.setdefault("selection_criteria", ["可复现", "有运行时代码", "能形成可证伪实验"])
    return plan, raw


def _github_candidates(query: str, limit: int = 8) -> list[dict[str, Any]]:
    response = requests.get(GITHUB_SEARCH, params={"q": query, "sort": "stars", "per_page": limit},
                            headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
                            timeout=25)
    response.raise_for_status()
    values = []
    for row in response.json().get("items") or []:
        values.append({"name": row.get("full_name"), "url": row.get("html_url"),
                       "clone_url": row.get("clone_url"), "description": row.get("description") or "",
                       "stars": int(row.get("stargazers_count") or 0),
                       "language": row.get("language") or "", "updated_at": row.get("updated_at") or ""})
    return values


def _semantic_scholar_candidates(query: str, limit: int) -> list[dict[str, Any]]:
    response = requests.get(S2_SEARCH, params={
        "query": query, "limit": limit,
        "fields": "title,authors,year,url,abstract,externalIds,openAccessPdf,citationCount,venue",
    }, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    values = []
    for row in response.json().get("data") or []:
        pdf = row.get("openAccessPdf") or {}
        values.append({"paper_id": row.get("paperId"), "title": row.get("title") or "",
                       "url": row.get("url") or "", "pdf_url": pdf.get("url") or "",
                       "abstract": row.get("abstract") or "", "year": row.get("year"),
                       "venue": row.get("venue") or "", "citation_count": row.get("citationCount") or 0,
                       "authors": [a.get("name") for a in row.get("authors") or [] if a.get("name")][:8],
                       "external_ids": row.get("externalIds") or {}})
    return values


def _arxiv_candidates(query: str, limit: int) -> list[dict[str, Any]]:
    response = requests.get(ARXIV_SEARCH, params={
        "search_query": '(all:"large language model" OR all:LLM) AND (all:agent OR all:"tool use")',
        "start": 0, "max_results": limit,
        "sortBy": "relevance", "sortOrder": "descending",
    }, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    values = []
    for entry in root.findall("a:entry", ns):
        url = str(entry.findtext("a:id", default="", namespaces=ns)).strip()
        arxiv_id = url.rstrip("/").split("/")[-1]
        authors = [str(node.findtext("a:name", default="", namespaces=ns)).strip()
                   for node in entry.findall("a:author", ns)]
        values.append({
            "paper_id": arxiv_id,
            "title": re.sub(r"\s+", " ", entry.findtext("a:title", default="", namespaces=ns)).strip(),
            "url": url, "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            "abstract": re.sub(r"\s+", " ", entry.findtext("a:summary", default="", namespaces=ns)).strip(),
            "year": str(entry.findtext("a:published", default="", namespaces=ns))[:4],
            "venue": "arXiv", "citation_count": 0, "authors": [a for a in authors if a][:8],
            "external_ids": {"ArXiv": arxiv_id}, "source": "arxiv",
        })
    return values


def _crossref_candidates(query: str, limit: int) -> list[dict[str, Any]]:
    response = requests.get(CROSSREF_SEARCH, params={"query.bibliographic": query, "rows": limit,
                                                     "select": "DOI,title,author,published,URL,abstract,container-title,is-referenced-by-count"},
                            headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    values = []
    for row in ((response.json().get("message") or {}).get("items") or []):
        dates = ((row.get("published") or {}).get("date-parts") or [[]])[0]
        authors = [" ".join(filter(None, [a.get("given"), a.get("family")]))
                   for a in row.get("author") or []]
        abstract = re.sub(r"<[^>]+>", " ", str(row.get("abstract") or ""))
        values.append({
            "paper_id": row.get("DOI") or "", "title": " ".join(row.get("title") or []),
            "url": row.get("URL") or "", "pdf_url": "", "abstract": re.sub(r"\s+", " ", abstract).strip(),
            "year": dates[0] if dates else None,
            "venue": " ".join(row.get("container-title") or []),
            "citation_count": row.get("is-referenced-by-count") or 0,
            "authors": [a for a in authors if a][:8], "external_ids": {"DOI": row.get("DOI") or ""},
            "source": "crossref",
        })
    return values


def _paper_candidates(query: str, limit: int = 8) -> list[dict[str, Any]]:
    errors = []
    for backend in (_semantic_scholar_candidates, _arxiv_candidates, _crossref_candidates):
        try:
            values = backend(query, limit)
            if values:
                return values
        except Exception as exc:
            errors.append(f"{backend.__name__}: {exc}")
    raise RuntimeError("all academic search backends failed: " + " | ".join(errors))


def _choose(plan: dict[str, Any], repos: list[dict[str, Any]],
            papers: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    compact_repos = [{k: row.get(k) for k in ("name", "description", "stars", "language", "url")}
                     for row in repos]
    compact_papers = [{k: row.get(k) for k in ("title", "year", "venue", "citation_count", "abstract", "url")}
                      for row in papers]
    prompt = f"""你是外部知识主动学习的 acquisition critic。知识缺口：{plan.get('knowledge_gap')}
候选 GitHub（索引从0开始）：{json.dumps(compact_repos, ensure_ascii=False)[:12000]}
候选论文（索引从0开始）：{json.dumps(compact_papers, ensure_ascii=False)[:18000]}
选择互补的一项代码和一篇论文，优先可复现机制、新颖性和对 Partner 的可实验性，而非只看星数/引用。
输出严格 JSON：{{"repo_index":0,"paper_index":0,"repo_reason":"...","paper_reason":"...","expected_information_gain":"...","possible_disconfirmation":"..."}}。"""
    raw = _llm(prompt, max_tokens=3000, purpose="classify")
    choice = _json_object(raw)
    try:
        choice["repo_index"] = min(max(0, int(choice.get("repo_index", 0))), max(0, len(repos) - 1))
        choice["paper_index"] = min(max(0, int(choice.get("paper_index", 0))), max(0, len(papers) - 1))
    except (TypeError, ValueError):
        choice.update({"repo_index": 0, "paper_index": 0})
    return choice, raw


def _slug(value: str, fallback: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")[:100]
    return result or fallback


def _clone_repo(root: Path, repo: dict[str, Any]) -> dict[str, Any]:
    clone_url = str(repo.get("clone_url") or "")
    if not clone_url.startswith("https://github.com/"):
        return {"ok": False, "error": "untrusted clone URL"}
    target = root / "external/code/discovered" / _slug(str(repo.get("name") or "repo"), "repo")
    if target.is_dir():
        return {"ok": True, "path": str(target), "status": "already_present"}
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "clone", "--depth", "1", clone_url, str(target)],
                          text=True, capture_output=True, timeout=180, check=False)
    return {"ok": proc.returncode == 0, "path": str(target), "status": "cloned" if proc.returncode == 0 else "failed",
            "error": (proc.stderr or proc.stdout)[-1200:] if proc.returncode else ""}


def _save_paper(root: Path, paper: dict[str, Any]) -> dict[str, Any]:
    paper_dir = root / "external/literature/discovered"
    paper_dir.mkdir(parents=True, exist_ok=True)
    identity = str((paper.get("external_ids") or {}).get("ArXiv") or paper.get("paper_id") or "paper")
    stem = _slug(identity, "paper")
    metadata = paper_dir / f"{stem}.md"
    metadata.write_text(
        "# " + str(paper.get("title") or "Untitled paper") + "\n\n"
        + f"- URL: {paper.get('url') or ''}\n- Year: {paper.get('year')}\n- Venue: {paper.get('venue') or ''}\n"
        + f"- Authors: {', '.join(paper.get('authors') or [])}\n\n## Abstract\n\n{paper.get('abstract') or 'Abstract unavailable.'}\n",
        encoding="utf-8",
    )
    result = {"ok": True, "metadata_path": str(metadata), "pdf_path": "", "status": "metadata_saved"}
    pdf_url = str(paper.get("pdf_url") or "")
    if pdf_url.startswith("https://"):
        try:
            response = requests.get(pdf_url, headers={"User-Agent": USER_AGENT}, timeout=45)
            response.raise_for_status()
            body = response.content
            if body.startswith(b"%PDF") and 1024 < len(body) <= 30_000_000:
                pdf = paper_dir / f"{stem}.pdf"
                pdf.write_bytes(body)
                result.update({"pdf_path": str(pdf), "status": "pdf_downloaded",
                               "pdf_sha256": hashlib.sha256(body).hexdigest()})
        except Exception as exc:
            result["pdf_error"] = str(exc)[:500]
    return result


def _repo_excerpt(path: str) -> tuple[list[str], str]:
    root = Path(path)
    candidates = [root / "README.md", root / "README.rst", root / "docs/README.md"]
    selected = [value for value in candidates if value.is_file()][:1]
    if not selected:
        fallback = next(iter(root.glob("*.md")), None)
        selected = [fallback] if fallback else []
    code_candidates: list[Path] = []
    for value in root.rglob("*"):
        if not value.is_file() or value.suffix.lower() not in {
                ".py", ".ts", ".tsx", ".js", ".rs", ".go"}:
            continue
        if any(part in {".git", "node_modules", "dist", "build", "vendor"}
               for part in value.parts):
            continue
        name = value.name.lower()
        score = sum(token in name for token in (
            "agent", "event", "runtime", "memory", "skill", "hook", "reward", "learn"))
        try:
            size = value.stat().st_size
        except OSError:
            continue
        if score and 100 <= size <= 300_000:
            code_candidates.append(value)
    code_candidates.sort(key=lambda value: (-sum(token in value.name.lower() for token in (
        "agent", "event", "runtime", "memory", "skill", "hook", "reward", "learn")), str(value)))
    selected.extend(code_candidates[:3])
    excerpts = []
    paths = []
    for source in selected:
        try:
            content = source.read_text(encoding="utf-8", errors="replace")[:6000]
        except OSError:
            continue
        paths.append(str(source))
        excerpts.append(f"\n--- FILE: {source.relative_to(root)} ---\n{content}")
    return paths, "".join(excerpts)[:22000]


def _paper_excerpt(saved: dict[str, Any], paper: dict[str, Any]) -> tuple[str, str, int]:
    """Read real PDF pages when available; otherwise label the abstract fallback."""
    pdf_path = Path(str(saved.get("pdf_path") or ""))
    if pdf_path.is_file():
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            chunks = []
            pages_read = min(8, len(reader.pages))
            for page in reader.pages[:pages_read]:
                chunks.append(str(page.extract_text() or ""))
            text = "\n".join(chunks).strip()[:22000]
            if len(text) >= 500:
                return str(pdf_path), text, pages_read
        except Exception:
            pass
    return str(saved.get("metadata_path") or ""), str(paper.get("abstract") or "")[:10000], 0


def _synthesise(plan: dict[str, Any], choice: dict[str, Any], repo: dict[str, Any],
                paper: dict[str, Any], repo_paths: list[str], repo_excerpt: str,
                paper_path: str, paper_excerpt: str, paper_pages_read: int) -> tuple[str, str]:
    prompt = f"""你是 Partner 的研究合作者。必须只依据下面的真实证据写中文研究札记，不可补写未读内容。
知识缺口：{plan.get('knowledge_gap')}
选择理由：{json.dumps(choice, ensure_ascii=False)}
代码来源：{repo.get('url')}；实际读取文件：{json.dumps(repo_paths, ensure_ascii=False)}\n代码摘录：\n{repo_excerpt[:22000]}
论文来源：{paper.get('url')}；题名：{paper.get('title')}；证据文件：{paper_path}；
实际读取 PDF 页数：{paper_pages_read}（0 表示只读摘要）\n论文证据：\n{paper_excerpt[:22000]}

输出 Markdown，必须包含：本轮真正学到的机制、代码与论文相互支持/冲突之处、对 Partner 的三个原创想法、
每个想法的最小可证伪实验、可能推翻当前判断的证据、明确没有读到/不能声称的边界。不要写套话，不要把摘要当全文。"""
    raw = _llm(prompt, max_tokens=6500, purpose="research_synthesis")
    return _clean_markdown(raw), raw


def atomic_external_knowledge_scout(ctx: Any, params: dict) -> dict:
    root, task_dir = _context(ctx)
    topic = str(params.get("topic") or
                "LLM agent active learning, harness reliability and verifiable self-improvement").strip()
    query_variant = int(params.get("query_variant") or 0)
    if query_variant:
        focuses = ("causal diagnosis", "episodic memory", "counterexample generation",
                   "tool reliability", "continual learning", "evaluation harness")
        topic += "; focus: " + focuses[(query_variant - 1) % len(focuses)]
    index_path = root / "external/insights/discovery_index.jsonl"
    prior = []
    try:
        prior = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()[-30:]]
    except (OSError, TypeError, ValueError):
        pass
    recent_titles = [str(item) for row in prior for item in row.get("source_titles") or []]
    plan, query_raw = _query_plan(root, topic, recent_titles)
    seen = _seen_urls(root)
    try:
        repos = [row for row in _github_candidates(str(plan["github_query"])) if row.get("url") not in seen]
        if not repos:
            # LLM-generated queries can be too narrow. Preserve its knowledge
            # gap, but expand recall with two declared domain queries; the LLM
            # acquisition critic still chooses the final evidence pair.
            expanded = []
            for query in ("LLM agent harness", "self evolving agent active learning"):
                expanded.extend(_github_candidates(query, 6))
            by_url = {str(row.get("url") or ""): row for row in expanded
                      if row.get("url") and row.get("url") not in seen}
            repos = list(by_url.values())
        papers = [row for row in _paper_candidates(str(plan["paper_query"]))
                  if row.get("url") not in seen and _domain_relevant_paper(row)]
        if not papers:
            expanded_papers = _paper_candidates(
                "large language model agents tool use continual learning self improvement", 10)
            papers = [row for row in expanded_papers
                      if row.get("url") not in seen and _domain_relevant_paper(row)]
    except Exception as exc:
        _record_failure(root, status="external_search_failed",
                        detail={"query_plan": plan, "error": str(exc)[:1000], "llm_calls": 1})
        return {"ok": False, "status": "external_search_failed", "error": str(exc)[:1000],
                "llm_calls": 1, "production_effective": False}
    if not repos or not papers:
        _record_failure(root, status="no_novel_cross_source_pair",
                        detail={"query_plan": plan, "repo_count": len(repos),
                                "paper_count": len(papers), "llm_calls": 1})
        return {"ok": False, "status": "no_novel_cross_source_pair",
                "error": f"novel repos={len(repos)}, papers={len(papers)}", "llm_calls": 1,
                "production_effective": False}
    choice, choice_raw = _choose(plan, repos, papers)
    repo = repos[int(choice.get("repo_index") or 0)]
    paper = papers[int(choice.get("paper_index") or 0)]
    cloned = _clone_repo(root, repo)
    saved_paper = _save_paper(root, paper)
    repo_sources, excerpt = _repo_excerpt(str(cloned.get("path") or "")) if cloned.get("ok") else ([], "")
    paper_source, paper_text, paper_pages = _paper_excerpt(saved_paper, paper)
    synthesis, synthesis_raw = _synthesise(
        plan, choice, repo, paper, repo_sources, excerpt,
        paper_source, paper_text, paper_pages)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    insight_dir = root / "external/insights"
    insight_dir.mkdir(parents=True, exist_ok=True)
    note = insight_dir / f"{stamp}_external_learning.md"
    note.write_text(synthesis or "LLM synthesis failed; see machine record.", encoding="utf-8")
    external_files = [str(note), str(saved_paper.get("metadata_path") or "")]
    if cloned.get("ok"):
        external_files.append(str(cloned.get("path")))
    if saved_paper.get("pdf_path"):
        external_files.append(str(saved_paper["pdf_path"]))
    record = {
        "schema_version": 1, "created_at": now_iso(), "topic": topic,
        "query_plan": plan, "selection": choice, "repository": repo, "paper": paper,
        "clone": cloned, "paper_acquisition": saved_paper, "repo_evidence_paths": repo_sources,
        "repo_excerpt_sha256": hashlib.sha256(excerpt.encode()).hexdigest() if excerpt else "",
        "paper_evidence_path": paper_source,
        "paper_excerpt_sha256": hashlib.sha256(paper_text.encode()).hexdigest() if paper_text else "",
        "evidence_depth": {"repository_files_read": len(repo_sources),
                           "paper_pdf_pages_read": paper_pages,
                           "paper_scope": "pdf_excerpt" if paper_pages else "abstract_only"},
        "insight_path": str(note), "source_urls": [str(repo.get("url") or ""), str(paper.get("url") or "")],
        "source_titles": [str(repo.get("name") or ""), str(paper.get("title") or "")],
        "llm_trace": {"calls": 3, "query_response_chars": len(query_raw),
                      "selection_response_chars": len(choice_raw),
                      "synthesis_response_chars": len(synthesis_raw)},
        "production_effective": False,
    }
    machine = insight_dir / f"{stamp}_external_learning.json"
    machine.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    append_jsonl(index_path, {**record, "machine_path": str(machine)})
    task_copy = task_dir / "04_external_knowledge_scout.json"
    task_copy.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    report = task_dir / "04_external_knowledge_scout.md"
    report.write_text(
        "# 外部知识主动学习札记\n\n"
        f"> 知识缺口：{plan.get('knowledge_gap')}\n\n"
        f"- GitHub：[{repo.get('name')}]({repo.get('url')})，本地：`{cloned.get('path')}`；实际读取 {len(repo_sources)} 个文件\n"
        f"- 论文：[{paper.get('title')}]({paper.get('url')})，证据：`{paper_source}`；实际读取 PDF {paper_pages} 页（0 表示摘要）\n"
        f"- LLM 关键判断调用：3 次（选题、acquisition、综合与反驳）\n\n"
        + (synthesis or "模型综合失败；本轮不得形成知识结论。") + "\n",
        encoding="utf-8",
    )
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf = task_dir / "04_external_knowledge_scout.pdf"
    pdf_result = atomic_generate_detailed_pdf(ctx, {
        "source_path": str(report), "output_path": str(pdf),
        "title": "外部知识主动学习札记", "report_style": "source_review",
        "min_content_chars": 700, "min_sections": 4,
    })
    ok = bool(cloned.get("ok") and saved_paper.get("ok") and synthesis)
    ok = bool(ok and pdf_result.get("ok"))
    return {"ok": ok, "status": "completed" if ok else "evidence_incomplete",
            "strategy_id": "04_external_knowledge_scout", "query_plan": plan,
            "selection": choice, "repository": repo, "paper": paper,
            "clone": cloned, "paper_acquisition": saved_paper,
            "pdf_generation": pdf_result,
            "evidence_depth": record["evidence_depth"],
            "llm_synthesis": synthesis, "llm_trace": record["llm_trace"],
            "external_files": [value for value in external_files if value],
            "business_metrics": {"llm_calls": 3, "repositories_acquired": int(bool(cloned.get("ok"))),
                                 "papers_acquired": int(bool(saved_paper.get("ok"))),
                                 "repository_files_read": len(repo_sources),
                                 "paper_pdf_pages_read": paper_pages,
                                 "novel_source_pairs": 1, "ideas_requested": 3},
            "production_effective": False,
            "files": [str(task_copy), str(report), str(pdf)] if pdf_result.get("ok") else [str(task_copy), str(report)]}


HANDLERS = {"external_knowledge_scout": atomic_external_knowledge_scout}
