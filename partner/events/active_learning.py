"""External-knowledge active learning Events."""
from __future__ import annotations

from typing import Any
from pathlib import Path
import json
import os
import re

from partner.event_fabric.catalog import EventDefinition
from ._llm import call_model, json_object
from partner.runtime.action_execution import write_json


def _semantic(params: dict[str, Any], *node_ids: str) -> dict[str, Any]:
    outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    for node_id in node_ids:
        row = outputs.get(node_id)
        if isinstance(row, dict):
            value = row.get("semantic_output")
            if isinstance(value, dict):
                return value
    previous = params.get("previous_semantic")
    return dict(previous) if isinstance(previous, dict) else {}


def _llm(ctx: Any, params: dict[str, Any], purpose: str, instruction: str) -> dict[str, Any]:
    raw, usage = call_model(ctx, purpose=purpose, prompt=(instruction
        + "\n只输出 JSON。知识来源必须可追溯；模型记忆不是外部证据。\n输入="
        + json.dumps(params, ensure_ascii=False)[:48000]))
    value = json_object(raw)
    return {"ok": True, "status": "completed", "semantic_output": value,
            "summary": str(value.get("reason") or value.get("question") or purpose),
            "token_usage": usage, "model_output": raw}


def question_formulate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    # Gap-driven learning (2026-10-10): the learning question must target the
    # previous round's unmet evidence requirements, not a generic curiosity.
    # outcome_verify persists gap_report.json into the job working dir; the
    # active-learning child runs in the same job, so read it directly.
    gaps = []
    try:
        ws = str(getattr(ctx, 'workspace', '') or '')
        jid = str(getattr(ctx, 'job_id', '') or '')
        gap_path = Path(ws) / 'state/event_runtime/work' / jid / 'gap_report.json'
        if gap_path.is_file():
            data = json.loads(gap_path.read_text())
            for row in data.get('round_gaps') or []:
                g = str(row.get('gap') or '')
                if g:
                    gaps.append(g)
    except Exception:
        pass
    gap_note = ('\n【缺口驱动】上一轮核验未满足的缺口（学习必须优先针对这些缺口补能力，'
                'question 必须直接关联至少一条）：' + json.dumps(gaps, ensure_ascii=False)[:8000]
                if gaps else '')
    return _llm(ctx, params, "learning_question_formulate",
        ("把项目当前未知变成一个答案会改变下一行动的可证伪问题。字段 question,decision_impact,"
         "known,unknown,stop_rule。问题必须聚焦本轮真实推进缺口：读了什么、差什么、需要外部学什么。"
         + gap_note))


def source_plan(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _llm(ctx, params, "learning_source_plan",
        "为问题设计多源检索计划，优先论文原文、官方文档和源代码。字段 "
        "queries,source_types,primary_sources,crosscheck_rule,download_plan。queries每项必须有text；"
        "primary_sources必须非空且至少含2条真实可访问的外部来源URL（arXiv论文原文、GitHub仓库或文件、"
        "官方文档均可）；即使是内部接口类问题，也必须给出相邻领域真实来源（如世界模型评估基准、"
        "typed evaluator可审计性设计、JEV类决策接口的公开资料），不得因问题偏内部就清空primary_sources。"
        "URL必须是可直接读取的精确http(s) URL，不得是通配模式、搜索语法或需要再点击的列表页。"
        "本地路径只能来自本轮已验证的输入清单；禁止从记忆猜测历史job路径，禁止通配符。"
        "download_plan可给出需要分步下载的URL，与primary_sources同样优先真实外部来源。")


def _classify_source_kind(url: str) -> str:
    """Classify URL into paper/code/doc."""
    lower = url.lower()
    if any(h in lower for h in ('arxiv.org', 'pubmed', 'semanticscholar', 'aclweb', 'openreview')):
        return 'paper'
    if any(h in lower for h in ('github.com', 'gitlab.com', 'bitbucket.org')):
        return 'code'
    return 'doc'


def _expand_arxiv_url(url: str) -> str:
    """Convert arXiv abs page URL to PDF URL."""
    import re
    m = re.search(r'arxiv\.org/abs/(\d+\.\d+)', url)
    if m:
        return f'https://arxiv.org/pdf/{m.group(1)}.pdf'
    return url


def _expand_github_url(url: str, directory: Path) -> list[dict]:
    """For GitHub repo URLs, fetch README + directory listing + key source files.
    Returns list of receipts (may be empty on failure)."""
    import re
    m = re.match(r'https?://github\.com/([^/]+/[^/]+)(?:/(.+))?', url)
    if not m:
        return []
    repo_path = m.group(1)
    subpath = m.group(2) or ''
    receipts = []
    import httpx
    # Fetch README
    readme_url = f'https://raw.githubusercontent.com/{repo_path}/HEAD/README.md'
    try:
        with httpx.Client(timeout=15, follow_redirects=True, trust_env=False) as client:
            resp = client.get(readme_url, headers={'User-Agent': 'Partner source reader'})
            if resp.status_code == 200 and len(resp.content) > 100:
                import hashlib
                folder = directory / hashlib.sha256(readme_url.encode()).hexdigest()[:20]
                folder.mkdir(parents=True, exist_ok=True)
                text = resp.content.decode('utf-8', errors='replace')
                (folder / 'source.bin').write_bytes(resp.content)
                (folder / 'source.txt').write_text(text, encoding='utf-8')
                from partner.runtime.action_execution import write_json
                receipt = {
                    'url': readme_url, 'final_url': readme_url,
                    'content_type': 'text/markdown', 'bytes': len(resp.content),
                    'raw_path': str(folder / 'source.bin'), 'text_path': str(folder / 'source.txt'),
                    'sha256': hashlib.sha256(resp.content).hexdigest(),
                    'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'text_chars': len(text), 'source_kind': 'code',
                    'github_repo': repo_path, 'github_file': 'README.md',
                }
                write_json(folder / 'receipt.json', receipt)
                receipts.append(receipt)
    except Exception:
        pass
    # Fetch directory listing via GitHub API
    api_url = f'https://api.github.com/repos/{repo_path}/contents/{subpath}'
    try:
        with httpx.Client(timeout=15, follow_redirects=True, trust_env=False) as client:
            resp = client.get(api_url, headers={'User-Agent': 'Partner source reader',
                                                 'Accept': 'application/vnd.github.v3+json'})
            if resp.status_code == 200:
                items = json.loads(resp.content.decode('utf-8', errors='replace'))
                if isinstance(items, list):
                    # Pick up to 5 key source files (prioritize .py, .js, .ts, .rs, .go)
                    code_exts = {'.py', '.js', '.ts', '.rs', '.go', '.java', '.c', '.cpp', '.h'}
                    code_files = [it for it in items
                                  if isinstance(it, dict) and it.get('type') == 'file'
                                  and Path(str(it.get('name', ''))).suffix in code_exts]
                    for item in code_files[:5]:
                        raw_url = item.get('download_url') or ''
                        if raw_url:
                            try:
                                with httpx.Client(timeout=10, follow_redirects=True, trust_env=False) as c2:
                                    r2 = c2.get(raw_url, headers={'User-Agent': 'Partner source reader'})
                                    if r2.status_code == 200 and len(r2.content) > 50:
                                        import hashlib as _hl
                                        folder = directory / _hl.sha256(raw_url.encode()).hexdigest()[:20]
                                        folder.mkdir(parents=True, exist_ok=True)
                                        text = r2.content.decode('utf-8', errors='replace')
                                        (folder / 'source.bin').write_bytes(r2.content)
                                        (folder / 'source.txt').write_text(text, encoding='utf-8')
                                        from partner.runtime.action_execution import write_json
                                        receipt = {
                                            'url': raw_url, 'final_url': raw_url,
                                            'content_type': 'text/plain', 'bytes': len(r2.content),
                                            'raw_path': str(folder / 'source.bin'),
                                            'text_path': str(folder / 'source.txt'),
                                            'sha256': _hl.sha256(r2.content).hexdigest(),
                                            'text_sha256': _hl.sha256(text.encode()).hexdigest(),
                                            'text_chars': len(text), 'source_kind': 'code',
                                            'github_repo': repo_path,
                                            'github_file': item.get('name', ''),
                                        }
                                        write_json(folder / 'receipt.json', receipt)
                                        receipts.append(receipt)
                            except Exception:
                                pass
    except Exception:
        pass
    return receipts



def _win_browser_fetch(url: str) -> dict:
    """Fetch a login-walled page through the Windows-side browser fetcher.

    Partner runs inside WSL; xiaohongshu blocks WSL's egress IP / headless
    automation, but the Windows network stack + system Edge (msedge channel)
    + the dedicated logged-in profile pass.  win_fetch.py returns one JSON
    object on stdout: {status,title,text,media_urls,screenshot_path,reason,url}.
    """
    import subprocess
    win_py = os.environ.get('PARTNER_WIN_PY') or '/mnt/c/Users/zty12/partner_browser_env/Scripts/python.exe'
    win_script = os.environ.get('PARTNER_WIN_FETCH') or 'E:/work/partner/scripts/browser/win_fetch.py'
    try:
        proc = subprocess.run([win_py, win_script, url],
                              capture_output=True, text=True, timeout=180)
        out = (proc.stdout or '').strip()
        if not out:
            return {'status': 'fetch_failed',
                    'reason': 'win_fetch empty output: ' + str(proc.stderr or '')[:150]}
        value = json.loads(out)
        return value if isinstance(value, dict) else {'status': 'fetch_failed',
                                                      'reason': 'win_fetch non-dict output'}
    except Exception as exc:
        return {'status': 'fetch_failed', 'reason': f'win_fetch error: {str(exc)[:200]}'}



def _read_source_images(ctx: Any, media_urls: list[str], directory: Path,
                        limit: int = 8) -> tuple[str, int]:
    """Download up to ``limit`` images and ask the vision-capable model to
    transcribe long-image body text (xiaohongshu long-image notes).

    Partner's model backend (qwen3.8-flash) natively accepts image inputs via
    adapter.chat_with_images; the transcript is merged into the source text so
    borrowable_cards can quote the long-image content like normal prose.

    Returns (transcript, downloaded_count). Empty transcript when no image
    could be downloaded or the adapter has no vision support.
    """
    adapter = getattr(ctx, "adapter", None)
    if adapter is None or not hasattr(adapter, "chat_with_images"):
        return "", 0
    import hashlib as _hl
    import httpx
    img_dir = Path(directory) / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
    # media_urls 混有头像/平台图标（sns-avatar / fe-platform，小图非正文）。
    # 优先取 webpic 图床的正文字图；无则退回原顺序。
    candidates = [u for u in media_urls
                  if "webpic" in u and "avatar" not in u and "fe-platform" not in u]
    if not candidates:
        candidates = list(media_urls)
    for url in candidates[:limit]:
        try:
            with httpx.Client(timeout=30, follow_redirects=True, trust_env=False) as client:
                resp = client.get(url, headers={"User-Agent": ua})
            if resp.status_code != 200 or len(resp.content) < 1000:
                continue
            p = img_dir / (_hl.sha256(url.encode()).hexdigest()[:16] + ".img")
            p.write_bytes(resp.content)
            paths.append(str(p))
        except Exception:
            continue
    if not paths:
        return "", 0
    prompt = (
        "这些图片是一篇小红书长图笔记的正文切片（可能跨多张图连续成文）。"
        "请逐张读取图片中的全部文字与关键视觉信息，把长图正文完整转写为文本；"
        "不要编造看不见的内容；表格/示意图请描述其结构与关键数据。"
        "最后用一句总结这篇笔记的核心观点。"
    )
    # 逐张调用：qwen 视觉路径对批量输入是"一张失败整批放弃"，
    # 坏图（头像/图标/签名过期图）不应拖垮整篇长图的转写。
    transcripts: list[str] = []
    for p in paths:
        try:
            t = adapter.chat_with_images(prompt, [p])
        except Exception:
            t = ""
        t = (t or "").strip()
        if t and "__PARTNER_AGENT_STILL_RUNNING" not in t and "unavailable" not in t.lower():
            transcripts.append(t)
    if not transcripts:
        return "", 0
    return "\n\n".join(transcripts), len(paths)


def _xhs_search_fallback(ctx: Any, url: str, query_texts: list[str],
                         directory: Path) -> tuple[dict | None, dict]:
    """When the xiaohongshu page is login-walled / bodyless, search the open
    web for a non-xhs mirror (InfoQ/知乎/CSDN …) carrying the full text and
    fetch it with the plain HTTP reader.

    Returns (receipt, meta) where receipt is None when no usable mirror was
    found.  The receipt carries ``source_kind=search_fallback`` and the
    original query so provenance stays auditable.
    """
    adapter = getattr(ctx, "adapter", None)
    candidates: list[str] = []
    for row in query_texts:
        row = str(row).strip()
        if not row:
            continue
        if row not in candidates and len(row) >= 6:
            candidates.append(row)
        if len(candidates) >= 2:
            break
    if not candidates:
        return None, {}
    from partner.runtime.source_evidence import fetch, read_verified
    variants = _search_variants(candidates)
    seen: set[str] = set()
    attempts: list[dict] = []
    # 1) adapter.search_web (LLM/agent-backed) if available
    if adapter is not None and hasattr(adapter, "search_web"):
        for query in variants[:2]:
            try:
                rows = adapter.search_web(query)[:6]
            except Exception:
                rows = []
            for row in rows:
                hit_url = str(getattr(row, 'url', '') or '').strip()
                if (not hit_url.startswith(('https://', 'http://')) or hit_url in seen
                        or any(d in hit_url for d in ('xiaohongshu.com', 'xhslink.com',
                                                      'bilibili.com', 'b23.tv'))):
                    continue
                seen.add(hit_url)
                attempts.append({'url': hit_url,
                                 'title': str(getattr(row, 'title', '') or ''),
                                 'query': query})
    # 2) deterministic Bing/Sogou HTML search (WSL-reachable; the DDG
    #    instant-answer API is not a real web search and returns no results)
    for query in variants[:2]:
        for hit in _web_search_urls(query, limit=8):
            hit_url = str(hit.get('url') or '').strip()
            if (not hit_url.startswith(('https://', 'http://')) or hit_url in seen
                    or any(d in hit_url for d in ('xiaohongshu.com', 'xhslink.com',
                                                  'bilibili.com', 'b23.tv'))):
                continue
            seen.add(hit_url)
            attempts.append({'url': hit_url,
                             'title': str(hit.get('title') or ''),
                             'query': query})
    # Prefer candidates whose title overlaps the original query: mirrors that
    # paraphrase the queried title are far more likely to carry the full text
    # than brand homepages / unrelated news.
    gate = re.sub(r'\s+', '', (candidates[0] or '').lower())
    for a in attempts:
        t = re.sub(r'[\s,，。.?？!！:：;；\-_~～()（）"“”\'’]+', '',
                   str(a.get('title') or '').lower())
        if gate and t:
            a['_overlap'] = len(set(gate) & set(t)) / len(set(gate))
        else:
            a['_overlap'] = 0.0
    attempts.sort(key=lambda a: a.get('_overlap', 0.0), reverse=True)
    for attempt in attempts[:8]:
        fetched_text: str = ""
        receipt = None
        # 1) plain HTTP reader (works for direct article pages)
        try:
            receipt = fetch(attempt['url'], directory)
            text = read_verified(receipt, limit=20000, query=attempt['query'])
            fetched_text = str(text.get('text') or '').strip()
        except Exception:
            pass
        # 2) login-state Windows browser for Sogou redirect links / WeChat
        #    articles (httpx cannot follow Sogou's JS/cookie jumps)
        if len(fetched_text) < 200:
            try:
                win = _win_browser_fetch(attempt['url'])
                if str(win.get('status')) in ('text_available', 'metadata_only'):
                    fetched_text = str(win.get('text') or '').strip()
                    if len(fetched_text) >= 200:
                        import hashlib as _hl
                        folder = directory / _hl.sha256(attempt['url'].encode()).hexdigest()[:20]
                        folder.mkdir(parents=True, exist_ok=True)
                        body = fetched_text.encode('utf-8')
                        raw_path, txt_path = folder / 'source.bin', folder / 'source.txt'
                        raw_path.write_bytes(body)
                        txt_path.write_text(fetched_text, encoding='utf-8')
                        from partner.runtime.action_execution import write_json
                        media = [str(m) for m in (win.get('media_urls') or [])
                                 if str(m).startswith(('http://', 'https://'))][:40]
                        receipt = {'url': attempt['url'], 'final_url': attempt['url'],
                                   'content_type': 'text/html(win-browser)',
                                   'bytes': len(body), 'raw_path': str(raw_path),
                                   'text_path': str(txt_path),
                                   'sha256': _hl.sha256(body).hexdigest(),
                                   'text_sha256': _hl.sha256(body).hexdigest(),
                                   'text_chars': len(fetched_text),
                                   'browser_channel': True,
                                   'title': str(win.get('title') or ''),
                                   'media_urls': media}
                        write_json(folder / 'receipt.json', receipt)
            except Exception:
                pass
        if len(fetched_text) < 200 or receipt is None:
            continue
        # Relevance gate.  (1) Latin entities from the ORIGINAL query are a
        # hard gate: a mirror that never mentions any queried latin entity is
        # rejected.  (2) The search-result title must share >= 50% of the
        # distinct characters of the original query — this rejects brand
        # homepages (title "OpenAI") and unrelated news while accepting
        # mirrors whose title paraphrases the queried title.
        gate_query = candidates[0]
        lowered = fetched_text.lower()
        latin_entities = re.findall(r'[A-Za-z][A-Za-z0-9._-]{2,}', gate_query)
        latin_hits = sum(1 for w in latin_entities if w.lower() in lowered)
        if latin_entities and latin_hits == 0:
            continue
        norm_q = set(re.sub(r'\s+', '', gate_query.lower()))
        norm_t = set(re.sub(r'[\s,，。.?？!！:：;；\-_~～()（）"“”\'’]+', '',
                            str(attempt.get('title') or '').lower()))
        if norm_q and norm_t:
            overlap = len(norm_q & norm_t) / len(norm_q)
            if overlap < 0.5:
                continue
        receipt = dict(receipt)
        receipt['source_kind'] = 'search_fallback'
        receipt['search_query'] = attempt['query']
        receipt['search_title'] = str(attempt['title'] or '')[:200]
        receipt['original_url'] = url
        return receipt, {'query': attempt['query'], 'matched': attempt['url'],
                         'title': str(attempt['title'] or '')[:200]}
    return None, {'searched': variants, 'attempted': len(attempts)}


def _query_keywords(query: str) -> set[str]:
    """Extract discriminating keywords from a Chinese/English query for the
    relevance gate: latin tokens len>=3 plus CJK runs len>=4."""
    kws: set[str] = set()
    for token in re.findall(r'[A-Za-z][A-Za-z0-9._-]{2,}', query):
        kws.add(token)
    for run in re.findall(r'[\u4e00-\u9fff]{4,}', query):
        kws.add(run)
    return kws


def _search_variants(query_texts: list[str]) -> list[str]:
    """Build up to 4 search-friendly query variants from the plan queries.

    Exact-quoted and entity-augmented variants are tried before the raw query
    because Bing tokenises bare Chinese queries around the first entity (e.g.
    "OpenAI …" returns the OpenAI homepage)."""
    base = ' '.join(query_texts[:2]).strip()
    variants: list[str] = []
    first = (query_texts[0] or '').strip() if query_texts else ''
    if base and base not in variants:
        variants.append(base)
    if first:
        entities = re.findall(r'[A-Za-z][A-Za-z0-9._-]{2,}', first)
        numbers = re.findall(r'[\d]{2,}(?: ?亿|亿)?', first)
        topic = ''
        m = re.search(r'([\u4e00-\u9fff]{4,8}Jev)|(Jev[\u4e00-\u9fff]{4,8})', first)
        if m:
            topic = m.group(0)
        parts = entities[:3] + numbers[:2]
        if topic:
            parts.append(topic)
        if parts:
            aug = ' '.join(dict.fromkeys(parts))
            if aug and aug not in variants:
                variants.append(aug)
            # Jev-first variant: search engines brand-hijack queries that
            # start with "OpenAI", returning official homepages; putting the
            # rare entity first yields genuine analysis mirrors.
            jf = [p for p in parts if 'Jev' in p or 'jev' in p] + [p for p in parts if 'Jev' not in p and 'jev' not in p]
            if jf:
                jf_q = ' '.join(jf)
                if jf_q not in variants:
                    variants.append(jf_q)
    if first and len(first) >= 6:
        quoted = '"' + first + '"'
        if quoted not in variants:
            variants.append(quoted)
    return variants[:4]


_SEARCH_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _web_search_urls(query: str, limit: int = 6) -> list[dict]:
    """Deterministic web search via httpx with engine rotation and cooling.

    Search engines rate-limit anonymous scraping and degrade repeated queries
    (word-splitting to the brand term).  The FIRST request in a fresh session
    is the most reliable, so we try Sogou, cool down, try Bing, cool down,
    then retry Sogou once.  Results are cached for 10 minutes per query so
    repeated fallback calls do not re-trigger rate limits.  Returns
    [{'url','title'}] deduplicated."""
    import time as _time
    now = _time.time()
    cached = _SEARCH_CACHE.get(query)
    if cached and now - cached[0] < 600:
        return cached[1]
    # 0) Sogou WeChat-article search: by far the most reliable channel for
    #    Chinese news/analysis titles (it returns the mp.weixin.qq.com
    #    redirect directly, which win_fetch renders with login state).
    hits = _sogou_weixin_search(query, limit=limit)
    if len(hits) < 1 or _weixin_hits_relevant(hits, query) < 1:
        # cookie-less httpx results degrade to unrelated headlines; fall back
        # to the real-browser search page (stable, login-state session).
        hits = _sogou_weixin_search_win(query, limit=limit)
    if len(hits) >= 1:
        _SEARCH_CACHE[query] = (now, hits)
        return hits
    _time.sleep(6)
    hits = _sogou_search(query, limit=limit)
    if len(hits) >= 2:
        _SEARCH_CACHE[query] = (now, hits)
        return hits
    _time.sleep(8)
    hits += _bing_search(query, limit=limit)
    if len(hits) >= 2:
        _SEARCH_CACHE[query] = (now, hits)
        return hits
    _time.sleep(8)
    hits += _sogou_search(query, limit=limit)
    seen: set = set()
    out: list[dict] = []
    for h in hits:
        if h['url'] not in seen:
            out.append(h)
            seen.add(h['url'])
        if len(out) >= limit:
            break
    _SEARCH_CACHE[query] = (now, out)
    return out


def _weixin_hits_relevant(hits: list[dict], query: str) -> int:
    """Count how many WeChat hits overlap the query by >= 40% of distinct
    characters — cookie-less searches sometimes return unrelated headlines."""
    gate = set(re.sub(r'\s+', '', query.lower()))
    if not gate:
        return len(hits)
    n = 0
    for h in hits:
        t = set(re.sub(r'[\s,，。.?？!！:：;；\-_~～()（）"“”\'’]+', '',
                       str(h.get('title') or '').lower()))
        if t and len(gate & t) / len(gate) >= 0.4:
            n += 1
    return n


def _sogou_weixin_search_win(query: str, limit: int = 6) -> list[dict]:
    """Search WeChat articles through the real Windows browser (login state,
    stable results).  Returns /link?url=... redirect URLs whose targets are
    mp.weixin.qq.com articles."""
    import urllib.parse as _up
    search_url = ('https://weixin.sogou.com/weixin?type=2&ie=utf8&s_from=input&query='
                  + _up.quote(query) + '&__search_mode=1')
    results: list[dict] = []
    try:
        win = _win_browser_fetch(search_url)
        rows = win.get('search_results') or []
        for row in rows[:limit * 3]:
            hit_url = str(row.get('url') or '').strip()
            title = str(row.get('title') or '').strip()
            if not hit_url.startswith(('http://', 'https://')):
                continue
            if 'weixin.sogou.com/link' not in hit_url and 'mp.weixin.qq.com' not in hit_url:
                continue
            results.append({'url': hit_url, 'title': title[:200]})
            if len(results) >= limit:
                break
    except Exception:
        pass
    return results


def _sogou_weixin_search(query: str, limit: int = 6) -> list[dict]:
    """Search WeChat public-account articles via weixin.sogou.com.

    Returns /link?url=... redirect URLs (resolved against weixin.sogou.com)
    that lead to mp.weixin.qq.com articles — the highest-quality Chinese
    analysis mirror channel.  No cookies required; the plain reader can follow
    the redirect only sometimes, so callers usually need win_fetch."""
    import httpx as _httpx
    results: list[dict] = []
    try:
        with _httpx.Client(timeout=15, follow_redirects=True, trust_env=False) as client:
            resp = client.get('https://weixin.sogou.com/weixin',
                              params={'type': 2, 'query': query, 'ie': 'utf8',
                                      's_from': 'input'},
                              headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                                     'AppleWebKit/537.36 Chrome/120 Safari/537.36'})
        if resp.status_code != 200 or '请输入验证码' in resp.text or 'antispider' in resp.text:
            return results
        for m in re.finditer(r'<h3>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                             resp.text, re.S):
            hit_url = m.group(1).strip()
            title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if hit_url.startswith('/'):
                hit_url = 'https://weixin.sogou.com' + hit_url
            if hit_url.startswith(('http://', 'https://')):
                results.append({'url': hit_url, 'title': title[:200]})
            if len(results) >= limit:
                break
    except Exception:
        pass
    return results


def _sogou_search(query: str, limit: int = 6) -> list[dict]:
    import httpx as _httpx
    results: list[dict] = []
    try:
        with _httpx.Client(timeout=12, follow_redirects=True, trust_env=False) as client:
            resp = client.get('https://www.sogou.com/web',
                              params={'query': query},
                              headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                                     'AppleWebKit/537.36 Chrome/120 Safari/537.36'})
        if resp.status_code != 200 or '安全验证' in resp.text or 'antispider' in resp.text:
            return results
        for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                             resp.text, re.S):
            hit_url = m.group(1).strip().replace('&amp;', '&')
            title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if hit_url.startswith('/'):
                hit_url = 'https://www.sogou.com' + hit_url
            if hit_url.startswith(('http://', 'https://')):
                results.append({'url': hit_url, 'title': title[:200]})
            if len(results) >= limit:
                break
    except Exception:
        pass
    return results


def _bing_search(query: str, limit: int = 6) -> list[dict]:
    import base64 as _b64
    import httpx as _httpx
    results: list[dict] = []
    try:
        with _httpx.Client(timeout=15, follow_redirects=True, trust_env=False) as client:
            resp = client.get('https://www.bing.com/search',
                              params={'q': query, 'mkt': 'zh-CN'},
                              headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                                     'AppleWebKit/537.36 Chrome/120 Safari/537.36'})
        if resp.status_code != 200:
            return results
        for m in re.finditer(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                             resp.text, re.S):
            hit_url, title = m.group(1), re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if '/ck/a' in hit_url:
                encoded = re.search(r'u=a1([A-Za-z0-9+/=]+)', hit_url)
                if encoded:
                    try:
                        decoded = _b64.b64decode(encoded.group(1)).decode('utf-8', errors='ignore')
                        if decoded.startswith(('http://', 'https://')):
                            hit_url = decoded
                    except Exception:
                        pass
            if hit_url.startswith(('http://', 'https://')):
                results.append({'url': hit_url, 'title': title[:200]})
            if len(results) >= limit:
                break
    except Exception:
        pass
    return results



def source_retrieve(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Retrieve external sources with real HTTP fetching.

    v2: arXiv abs→pdf, GitHub repo→README+source, docs→text.
    Failures are recorded explicitly; local cache never impersonates a failed URL.
    """
    adapter = getattr(ctx, "adapter", None)
    if adapter is None:
        return {"ok": False, "status": "failed", "error": "search port unavailable"}
    previous = _semantic(params, "source_plan")
    constraints = ((params.get('intent_contract') or {}).get('execution_constraints') or {})
    local_value = str(constraints.get('local_learning_root') or '').strip()
    local_root = Path(local_value).expanduser() if local_value else Path('/__partner_no_local_source__')
    queries = previous.get("queries") or params.get("queries") or []
    if isinstance(queries, str):
        queries = [queries]
    query_texts = [str(row.get('text') or row.get('query') or '') if isinstance(row, dict)
                   else str(row) for row in queries]
    query_texts = [row for row in query_texts if row.strip()]
    sources: list[dict[str, str]] = []
    # user_sources: URLs the user explicitly asked us to read, injected into
    # run_context by the intake router.  They are mandatory work, so they are
    # fetched even when no source_plan / download_plan exists.
    user_sources = params.get('user_sources') or []
    if isinstance(user_sources, (str, list, tuple)):
        rows_iter = [user_sources] if isinstance(user_sources, str) else list(user_sources)
        for row in rows_iter:
            value = (row.get('url') or row.get('target') or '') if isinstance(row, dict) else str(row)
            value = str(value).strip()
            if value.startswith(('https://', 'http://')) and '*' not in value:
                sources.append({'url': value,
                                'source_kind': _classify_source_kind(value),
                                'user_requested': True})
    proposed = list(previous.get('primary_sources') or params.get('sources') or [])
    download_plan = previous.get('download_plan') or []
    if isinstance(download_plan, dict):
        download_plan = download_plan.get('steps') or []
    proposed.extend(download_plan)
    local_proposed: list[Path] = []
    for raw in params.get('evidence_refs') or []:
        path = Path(str(raw)).expanduser()
        if path.is_file() and '*' not in str(path):
            local_proposed.append(path)
    for row in proposed:
        value = (row.get('url') or row.get('target') or '') if isinstance(row,dict) else str(row)
        value = str(value).strip()
        if value.startswith(('https://','http://')) and '*' not in value:
            sources.append({'url': value, 'source_kind': _classify_source_kind(value)})
        elif value.startswith('/') and '*' not in value:
            candidate = Path(value).expanduser()
            if candidate.is_file():
                local_proposed.append(candidate)
    local_proposed = list(dict.fromkeys(path.resolve() for path in local_proposed))
    if not local_value and not sources and hasattr(adapter, "search_web"):
        for query in query_texts[:4]:
            for row in adapter.search_web(str(query))[:5]:
                if str(getattr(row,'url','')).startswith(('https://','http://')):
                    sources.append({'url':str(row.url),'title':str(row.title),'query':str(query),
                                    'source_kind': _classify_source_kind(str(row.url))})
    if not local_value and not sources and hasattr(adapter, "execute_task") and queries:
        prompt = (
            "使用真实联网检索能力执行以下查询，优先论文原文、官方文档和源码仓库。"
            "不要凭模型记忆补 URL。只输出 JSON：{\"sources\":[{\"query\":\"\","
            "\"title\":\"\",\"url\":\"https://...\",\"snippet\":\"\"}]}。\n查询="
            + json.dumps(query_texts[:4], ensure_ascii=False)
            + "\n工作目录：" + str(getattr(ctx, "working_dir", "") or str(ctx.workspace)+"/state/event_runtime/source_search") + "/" + str(params.get("flow_id") or "standalone")
        )
        try:
            value = json_object(str(adapter.execute_task(prompt) or ""))
            for row in value.get("sources") or []:
                if isinstance(row, dict) and str(row.get("url") or "").startswith(("https://", "http://")):
                    url = str(row.get("url") or "")
                    sources.append({key: str(row.get(key) or "") for key in ("query", "title", "url", "snippet")}
                                   | {'source_kind': _classify_source_kind(url)})
        except (RuntimeError, ValueError, TypeError):
            pass
    if not local_value and not sources and hasattr(adapter, "search_web"):
        for query in query_texts[:4]:
            for row in adapter.search_web(str(query))[:5]:
                if str(getattr(row, "url", "")).startswith(("https://", "http://")):
                    url = str(row.url)
                    sources.append({"query": str(query), "title": str(row.title),
                                    "url": url, "snippet": str(row.snippet),
                                    'source_kind': _classify_source_kind(url)})
    from partner.runtime.source_evidence import fetch
    import hashlib
    unique = {row["url"]: row for row in sources}
    if not unique and not local_value and not local_proposed and not query_texts:
        return {"ok": True, "status": "completed",
                "semantic_output": {"sources": [], "empty": True,
                                    "reason": "no user sources, no queries, no local root"},
                "evidence_refs": [],
                "summary": "无用户指定来源或检索意图，跳过外部抓取"}
    work = Path(getattr(ctx,'working_dir', '') or Path(ctx.workspace)/'state/event_runtime/work'/str(getattr(ctx,'job_id','learning')))
    directory = work / 'sources' / str(params.get('flow_id') or 'standalone')
    downloaded, failures = [], []
    if local_proposed and not local_value:
        # Context evidence files (e.g. handoff.json, input_consumption.json) are
        # provenance references, not a declared learning root.  Elevating them to
        # local_root='/' used to set declared_local_mode and silently skip every
        # external URL planned by source_plan, breaking active-learning runs that
        # must fetch real sources.  Keep them only as hints for the
        # explicit-local-root path; without an explicit local_learning_root the
        # external download plan always runs.
        constraints = {**constraints, 'local_learning_files': [str(p) for p in local_proposed]}
    if local_value and local_root.is_dir():
        preferred: list[Path] = []
        declared = constraints.get('local_learning_files') or []
        if isinstance(declared, str):
            declared = [declared]
        for raw in declared:
            candidate = Path(str(raw)).expanduser().resolve()
            try:
                candidate.relative_to(local_root.resolve())
            except ValueError:
                continue
            preferred.append(candidate)
        catalog_path = Path(ctx.workspace) / 'share/mind/external/catalog.json'
        if not preferred and catalog_path.is_file():
            try:
                catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
                catalog_root = Path(str(catalog.get('external_root') or '')).resolve()
                if catalog_root == local_root.resolve():
                    preferred.extend(Path(str(row.get('path') or '')) for row in
                                     (catalog.get('sources') or [])[:6]
                                     if isinstance(row, dict) and row.get('exists'))
            except (OSError, ValueError, TypeError):
                pass
        if not preferred:
            preferred = [local_root / name for name in
                         ('manifest.json', 'README.md', 'run_arm.py')]
        for source in preferred:
            if not source.is_file() or source.stat().st_size > 2_000_000:
                continue
            body = source.read_bytes()
            text = body.decode('utf-8', errors='replace')
            if len(text.strip()) < 100:
                continue
            target = directory / ('local_' + hashlib.sha256(str(source).encode()).hexdigest()[:16])
            target.mkdir(parents=True, exist_ok=True)
            raw_path, text_path = target / 'source.bin', target / 'source.txt'
            raw_path.write_bytes(body); text_path.write_text(text, encoding='utf-8')
            receipt = {
                'url': 'file-evidence://' + source.name,
                'final_url': str(source.resolve()), 'content_type': 'text/plain',
                'bytes': len(body), 'raw_path': str(raw_path), 'text_path': str(text_path),
                'sha256': hashlib.sha256(body).hexdigest(),
                'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'text_chars': len(text), 'source_kind': 'declared_local_evidence',
            }
            from partner.runtime.action_execution import write_json
            write_json(target / 'receipt.json', receipt)
            downloaded.append(receipt)
    # A declared local learning root is a user-frozen source: when it yields
    # files, consume them directly and do not run network fetches for the
    # default download plan.  The "no local substitution" rule below applies
    # only to the un-declared fallback path.
    declared_local_mode = bool(local_value and local_root.is_dir() and downloaded)
    # Fetch external URLs with real HTTP; expand arXiv/GitHub as needed.
    # Never fall back to local cache when an external URL was planned but failed.
    for row in ([] if declared_local_mode else list(unique.values())[:10]):
        url = row['url']
        kind = row.get('source_kind') or _classify_source_kind(url)
        # arXiv: expand abs→pdf
        if kind == 'paper' and 'arxiv.org/abs/' in url:
            url = _expand_arxiv_url(url)
        # GitHub: expand repo→README+source files
        if kind == 'code' and 'github.com' in url:
            gh_receipts = _expand_github_url(url, directory)
            if gh_receipts:
                downloaded.extend(gh_receipts)
                continue
            else:
                failures.append({'url': row['url'], 'error': 'GitHub expansion failed (no README or source files fetched)',
                                 'source_kind': kind})
                continue
        # Login-state browser channel (2026-10-10) for login-walled social
        # platforms (xiaohongshu / bilibili).  xiaohongshu URLs go through the
        # Windows-side fetcher (win_fetch.py: system Edge + logged-in profile +
        # Windows network stack) because xiaohongshu blocks WSL's egress IP and
        # headless automation.  WSL playwright is only the fallback.  Writes the
        # same receipt schema (source.bin/source.txt/sha256) so source_read
        # works unchanged.  Without a configured profile it fails honestly.
        if any(d in url for d in ('xiaohongshu.com', 'xhslink.com', 'bilibili.com', 'b23.tv')):
            if 'xiaohongshu.com' in url or 'xhslink.com' in url:
                win = _win_browser_fetch(url)
                status = str(win.get('status') or '')
                text = str(win.get('text') or '')
                title = str(win.get('title') or '')
                has_meta = bool(title) or len(text.strip()) >= 40
                if status in ('text_available', 'metadata_only') and has_meta:
                    import hashlib as _hl
                    folder = directory / _hl.sha256(url.encode()).hexdigest()[:20]
                    folder.mkdir(parents=True, exist_ok=True)
                    body = text.encode('utf-8')
                    raw_path, txt_path = folder / 'source.bin', folder / 'source.txt'
                    raw_path.write_bytes(body)
                    txt_path.write_text(text, encoding='utf-8')
                    from partner.runtime.action_execution import write_json
                    media = [str(m) for m in (win.get('media_urls') or [])
                             if str(m).startswith(('http://', 'https://'))][:40]
                    receipt = {'url': url, 'final_url': url,
                               'content_type': 'text/html(win-browser)',
                               'bytes': len(body), 'raw_path': str(raw_path),
                               'text_path': str(txt_path),
                               'sha256': _hl.sha256(body).hexdigest(),
                               'text_sha256': _hl.sha256(body).hexdigest(),
                               'text_chars': len(text), 'source_kind': kind,
                               'browser_channel': True,
                               'title': title,
                               'media_urls': media,
                               'screenshot': str(win.get('screenshot_path') or '')}
                    if len(text.strip()) < 100:
                        receipt['xhs_body_missing'] = True
                    write_json(folder / 'receipt.json', receipt)
                    # Body too short (tags/comments only, e.g. long-image
                    # notes): still try the open-web mirror for the full text.
                    # On success the mirror receipt (full body) is the primary
                    # evidence; the metadata receipt stays as provenance.
                    if len(text.strip()) < 200:
                        fb_receipt, fb_meta = _xhs_search_fallback(ctx, url, query_texts, directory)
                        if fb_receipt is not None:
                            downloaded.append(fb_receipt)
                            continue
                        receipt['xhs_fallback'] = {'query': str(fb_meta.get('searched') or '')[:200],
                                                   'attempted': fb_meta.get('attempted', 0)}
                    downloaded.append(receipt)
                    continue
                # Body missing / login-walled: search the open web for a mirror
                # carrying the full text (InfoQ/知乎/CSDN …) and fetch it with
                # the plain HTTP reader, so read still receives real content.
                fb_receipt, fb_meta = _xhs_search_fallback(ctx, url, query_texts, directory)
                if fb_receipt is not None:
                    downloaded.append(fb_receipt)
                    continue
                # No usable mirror: keep whatever metadata the browser got
                # (title/tags/comment snippet) as a metadata receipt so read can
                # still answer honestly about the note instead of dropping it.
                if status in ('text_available', 'metadata_only') and (title or len(text.strip()) >= 10):
                    import hashlib as _hl
                    folder = directory / _hl.sha256(url.encode()).hexdigest()[:20]
                    folder.mkdir(parents=True, exist_ok=True)
                    body = text.encode('utf-8')
                    raw_path, txt_path = folder / 'source.bin', folder / 'source.txt'
                    raw_path.write_bytes(body)
                    txt_path.write_text(text, encoding='utf-8')
                    from partner.runtime.action_execution import write_json
                    media = [str(m) for m in (win.get('media_urls') or [])
                             if str(m).startswith(('http://', 'https://'))][:40]
                    receipt = {'url': url, 'final_url': url,
                               'content_type': 'text/html(win-browser)',
                               'bytes': len(body), 'raw_path': str(raw_path),
                               'text_path': str(txt_path),
                               'sha256': _hl.sha256(body).hexdigest(),
                               'text_sha256': _hl.sha256(body).hexdigest(),
                               'text_chars': len(text), 'source_kind': kind,
                               'browser_channel': True,
                               'title': title,
                               'media_urls': media,
                               'xhs_body_missing': True,
                               'xhs_fallback': {'query': str(fb_meta.get('searched') or '')[:200],
                                                'attempted': fb_meta.get('attempted', 0)},
                               'screenshot': str(win.get('screenshot_path') or '')}
                    write_json(folder / 'receipt.json', receipt)
                    downloaded.append(receipt)
                    continue
                if status != 'fetch_failed':
                    failures.append({'url': url,
                                     'error': f'win browser channel: {status} {win.get("reason") or ""}; 标题搜索无可用转载全文'.strip()[:240],
                                     'source_kind': kind})
                    continue
                # fall through to WSL fallback below
            try:
                from partner.knowledge.content_tools import fetch_login_browser_full
                res = fetch_login_browser_full(url)
                status = str(res.get('status') or '')
                text = str(res.get('full_text') or '')
                if status in ('text_available', 'metadata_only') and len(text.strip()) >= 100:
                    import hashlib as _hl
                    folder = directory / _hl.sha256(url.encode()).hexdigest()[:20]
                    folder.mkdir(parents=True, exist_ok=True)
                    body = text.encode('utf-8')
                    raw_path, txt_path = folder / 'source.bin', folder / 'source.txt'
                    raw_path.write_bytes(body)
                    txt_path.write_text(text, encoding='utf-8')
                    from partner.runtime.action_execution import write_json
                    receipt = {'url': url, 'final_url': url,
                               'content_type': 'text/html(browser)',
                               'bytes': len(body), 'raw_path': str(raw_path),
                               'text_path': str(txt_path),
                               'sha256': _hl.sha256(body).hexdigest(),
                               'text_sha256': _hl.sha256(body).hexdigest(),
                               'text_chars': len(text), 'source_kind': kind,
                               'browser_channel': True,
                               'title': str(res.get('title') or ''),
                               'media_urls': res.get('media_urls') or [],
                               'screenshot': str(res.get('screenshot_path') or '')}
                    write_json(folder / 'receipt.json', receipt)
                    downloaded.append(receipt)
                    continue
                else:
                    failures.append({'url': url,
                                     'error': f'browser channel: {status} {res.get("reason") or ""}'.strip()[:240],
                                     'source_kind': kind})
                    continue
            except Exception as exc:
                failures.append({'url': url, 'error': f'browser channel failed: {str(exc)[:240]}',
                                 'source_kind': kind})
                continue
        # Generic HTTP fetch
        try:
            receipt = fetch(url, directory)
            receipt['source_kind'] = kind
            downloaded.append(receipt)
        except Exception as exc:
            failures.append({'url': row['url'], 'error': str(exc)[:240], 'source_kind': kind})
    # v2 rule: if external URLs were planned but all failed, do NOT succeed with local-only
    external_planned = any(r.get('source_kind') in ('paper', 'code', 'doc') for r in unique.values())
    external_fetched = any(r.get('source_kind') in ('paper', 'code', 'doc') for r in downloaded)
    if not declared_local_mode and external_planned and not external_fetched and failures:
        return {"ok": False, "status": "failed",
                "error": f"all {len(failures)} external URLs failed; refusing to substitute local cache",
                "semantic_output": {"sources": downloaded, "retrieval_failures": failures,
                                    "external_planned": len(unique), "external_fetched": 0},
                "evidence_refs": [],
                "summary": f"外部来源全部失败（{len(failures)} 条），不以本地缓存冒充"}
    return {"ok": bool(downloaded), "status": "completed" if downloaded else "failed",
            "semantic_output": {"sources": downloaded, 'retrieval_failures':failures,
                                'external_planned': len(unique), 'external_fetched': sum(1 for r in downloaded if r.get('source_kind') in ('paper','code','doc','search_fallback')),
                                'search_mirrors': sum(1 for r in downloaded if r.get('source_kind') == 'search_fallback')},
            "evidence_refs": [x['text_path'] for x in downloaded],
            "summary": f"实际下载并提取 {len(downloaded)} 份来源（{sum(1 for r in downloaded if r.get('source_kind') in ('paper','code','doc','search_fallback'))} 份外部，其中 {sum(1 for r in downloaded if r.get('source_kind') == 'search_fallback')} 份为正文镜像）；{len(failures)} 份失败"}


def source_read(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Deep reading of downloaded sources, producing borrowable_cards.

    v2: outputs structured cards (core_idea/key_method/model_usage/transfer_points/limitations)
    instead of shallow claim/quote extraction. Reads up to 10 sources (was 3).
    Code sources are read for interface signatures / core logic / reusable patterns.
    """
    from pathlib import Path
    from partner.runtime.source_evidence import read_verified
    from partner.runtime.action_execution import write_json
    sources = _semantic(params, 'retrieve').get('sources') or []
    if not sources:
        return {"ok":False,"status":"failed","error":"no downloaded sources to read"}
    
    # v3 fix: 优先读本次 source_retrieve 下载的外部源
    # 过滤掉 file-evidence:// 本地冻结文件，除非外部源全失败
    external_sources = []
    local_fallback = []
    for src in sources:
        # receipt 契约：外部源 url=https://...，本地 evidence url=file-evidence://...
        # 判断必须用 url 字段（receipt 无 path 键）
        if isinstance(src, dict):
            src_url = str(src.get('url') or '')
        else:
            src_url = str(src)
        if src_url.startswith('file-evidence://'):
            local_fallback.append(src)
        else:
            external_sources.append(src)
    
    # 优先使用外部源；仅当外部源全失败时使用本地兜底
    if external_sources:
        sources = external_sources
    elif local_fallback:
        sources = local_fallback
        # 标记为降级
        for src in sources:
            if isinstance(src, dict):
                src['degraded'] = True
                src['source_kind'] = 'local_fallback'
    
    # v2: raise limit from 3 to 10
    sources = sources[:10]
    question = str(_semantic(params,'question').get('question') or params.get('request') or '')[:1600]
    # v3.1: 图片理解接线——正文为长图的来源（xhs_body_missing 或文本过短）下载图片，
    # 用视觉模型转写长图正文；转写文本随后并入 excerpt，使 borrowable_cards 可引用图片内容。
    image_text_by_url: dict[str, str] = {}
    image_count_by_url: dict[str, int] = {}
    for r in sources:
        if not isinstance(r, dict):
            continue
        url = str(r.get('url') or '')
        if not url:
            continue
        missing = bool(r.get('xhs_body_missing'))
        media = [str(m) for m in (r.get('media_urls') or [])
                 if str(m).startswith(('http://', 'https://'))]
        text_chars = int(r.get('text_chars') or 0)
        if not media or not (missing or text_chars < 200):
            continue
        try:
            image_text, n = _read_source_images(ctx, media,
                                                Path(str(r.get('text_path') or '')).parent)
        except Exception:
            image_text, n = '', 0
        if image_text:
            image_text_by_url[url] = image_text
            image_count_by_url[url] = n
    # v2: raise read limit from 6000 to 16000 chars per source
    try: excerpts = [read_verified(r,limit=16000,query=question) for r in sources]
    except (OSError,KeyError,ValueError) as exc:
        return {"ok":False,"status":"failed","error":str(exc)}
    # Merge long-image transcripts BEFORE quote_spans are built, so quotes
    # drawn from the image body validate like any other source text.
    for idx, r in enumerate(sources[:len(excerpts)]):
        url = str(r.get('url') or '') if isinstance(r, dict) else ''
        img_text = image_text_by_url.get(url)
        if img_text and idx < len(excerpts):
            excerpts[idx]['text'] = (excerpts[idx].get('text') or '') \
                + '\n\n[长图正文转写]\n' + img_text
    # Quote selection uses literal spans of the downloaded text.
    for excerpt in excerpts:
        pieces = re.split(r'(?<=[.!?])\s+', re.sub(r'\s+', ' ', excerpt['text']).strip())
        spans = []
        for piece in pieces:
            words = piece.split()
            spans.extend(' '.join(words[start:start+20]) for start in range(0, len(words), 20))
        excerpt['quote_spans'] = {f'q{i+1}': text for i, text in enumerate(spans)}
    # v2 prompt: deep reading → borrowable_cards
    raw,usage=call_model(ctx,purpose='learning_source_read',prompt=(
        '你是深度阅读助手。针对学习问题：'+question+'，逐来源输出结构化借鉴卡片。\n'
        '每来源至少 1 张卡片，格式：\n'
        '{"borrowable_cards":[{"source_url":"...","source_title":"来源标题（从原文或URL推断）","source_kind":"paper|code|doc",\n'
        '  "core_idea":"一句话核心思想（解决什么问题、怎么解决）",\n'
        '  "key_method":"关键方法/机制（不是引用原文，而是方法逻辑）",\n'
        '  "model_usage":"用的模型/架构/数据规模（若适用，否则 null）",\n'
        '  "transfer_points":[{"to_what":"映射到本项目哪个组件","how":"具体怎么借鉴"}],\n'
        '  "limitations":"局限与不适用处",\n'
        '  "quote_ids":["q3","q5"]\n'
        '}]}\n'
        '说明：quote_ids 必须选自该来源的 quote_spans，程序会提取对应原文校验\n'
        '- 代码来源按"接口签名/核心实现逻辑/可复用模式"读，不是全文翻译\n'
        '- 论文来源提取核心思想、方法、模型、可迁移点、局限\n'
        '- 文档来源提取关键概念、使用模式、限制\n'
        '- 整个输出不超过 4000 汉字\n'
        '- 另含 unresolved:[] 字段（无法从来源直接得出的问题）\n'
        '- borrowable_cards 的 source_url 必须从"可用 source_url 白名单"中选择，禁止编造或引用白名单之外的 URL（否则整张卡片被程序拒绝）\n'
        '可用 source_url 白名单：'
        + json.dumps([r['url'] for r in excerpts], ensure_ascii=False) + '\n'
        + json.dumps(excerpts,ensure_ascii=False)))
    known={r['url']:r for r in excerpts}
    import unicodedata
    def normalized(text):
        return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC',str(text))).strip()
    value = {}
    error = ''
    attempts = []
    for attempt in range(2):
        value=json_object(raw)
        cards = value.get('borrowable_cards') or []
        if isinstance(cards, str):
            try:
                cards = json.loads(cards)
            except Exception:
                cards = []
        if not isinstance(cards, list):
            cards = []
        # LLM 输出防御：数组内混入非对象元素（截断/结构异常）时过滤，
        # 仅保留合法卡片对象；过滤后为空则走 repair 重试。
        cards = [c for c in cards if isinstance(c, dict)]
        error = '' if cards else 'no borrowable_cards produced'
        for card in cards:
            if card.get('source_url') not in known:
                error = 'card cites an undownloaded source'; break
            selected = card.get('quote_ids') or []
            if isinstance(selected, str): selected = [selected]
            spans = known[card['source_url']]['quote_spans']
            if any(key not in spans for key in selected):
                error = 'card selects an unknown quote span'; break
            if selected:
                card['quotes'] = [spans[key] for key in selected]
            quotes = card.get('quotes') or []
            if isinstance(quotes, str):
                quotes = [quotes]
            for quote in quotes:
                text=quote.get('text','') if isinstance(quote,dict) else str(quote)
                if text and normalized(text) not in normalized(known[card['source_url']]['text']):
                    error = 'card quote not present in supplied source'; break
        attempts.append({'reading':value, 'error':error})
        if not error: break
        if attempt == 0:
            raw, extra = call_model(ctx,purpose='learning_source_read_repair',prompt=(
                '修正刚才的来源阅读：'+error+'。引文必须逐字复制所给正文的连续短片段，不能翻译、补写或用省略号拼接；'
                '不能找到原句则将该结论列为 unresolved。返回 JSON borrowable_cards([...]),unresolved；quote_ids 只能选本来源 quote_spans 内存在的编号。\n'
                'borrowable_cards 的 source_url 必须从"可用 source_url 白名单"中选择，禁止编造或引用白名单之外的 URL：'
                + json.dumps([r['url'] for r in excerpts], ensure_ascii=False) + '\n'
                +'上次输出='+raw[:24000]+'\n实际原文='+json.dumps(excerpts,ensure_ascii=False)))
            for key in ('prompt_tokens','completion_tokens','total_tokens'):
                usage[key] = usage.get(key,0) + extra.get(key,0)
    audit_path=Path(sources[0]['text_path']).parent.parent/'reading_audit.json'
    write_json(audit_path, {'attempts':attempts, 'sources':[{'url':r['url'], 'sha256':r.get('sha256')} for r in sources]})
    if error:
        return {'ok':False,'status':'failed','error':error,'token_usage':usage,'evidence_refs':[str(audit_path)]}
    path=Path(sources[0]['text_path']).parent.parent/'reading.json';write_json(path,value)
    img_note = ''
    if image_text_by_url:
        total_imgs = sum(image_count_by_url.values())
        img_note = f'；其中 {len(image_text_by_url)} 份正文为长图，已转写 {total_imgs} 张图片'
    return {'ok':True,'status':'completed','semantic_output':value,
            'files':[str(path)],'evidence_refs':[str(path)]+[r['text_path'] for r in sources],
            'summary':f'已深度阅读 {len(cards)} 张借鉴卡片（来自 {len(sources)} 份来源{img_note}）',
            'learning_delta':False,'token_usage':usage}



def claim_crosscheck(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _llm(ctx, params, "learning_claim_crosscheck",
        "逐条把主张绑定到来源片段并找相反证据，禁止跨来源混淆。字段 claims，每项含 claim,source_refs,support,contradictions,confidence；另含 unresolved。")


def synthesize(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _llm(ctx, params, "learning_synthesize",
        "综合已交叉核验的外部证据，形成对项目决策有影响的新认识。字段 findings,novelty,decision_change,limitations,evidence_refs。")


def adoption_candidate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _llm(ctx, params, "learning_adoption_candidate",
        "把新知识转成一个最小、隔离、可回滚的项目动作 Candidate。字段 event_type,parameters,hypothesis,baseline,success_criteria,rollback。"
        "【落地硬规则】parameters 必须包含会在本轮/下轮真实执行的 operation 与 target_files（指向真实文件路径），"
        "落地方式必须是产生实质产物：修改真实 corpus/源码/数据文件、写出可复用的生成器或工具脚本、产出新的可验证度量文件；"
        "禁止只构造消费回执（input_consumption/eligibility 类 JSON）充当学习落地——那会被下游验证判为未实质推进。")


def matched_verify(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    result = _llm(ctx, params, "learning_matched_verify",
        "判断外部知识 Candidate 是否已有同输入、同预算的 baseline/candidate 证据。字段 decision(promote/reject/inconclusive),baseline,candidate,matched_dimensions,reason,evidence_refs；缺少真实执行必须 inconclusive。")
    semantic = result.get("semantic_output") if isinstance(result.get("semantic_output"), dict) else {}
    allowed = {"promote", "reject", "inconclusive"}
    if semantic.get("decision") not in allowed:
        return {**result, "ok": False, "status": "failed", "error": "invalid learning decision"}
    known: list[str] = []
    for row in (params.get("flow_outputs") or {}).values():
        if isinstance(row, dict):
            known.extend(str(value) for value in row.get("evidence_refs") or [])
    cited = [str(value) for value in semantic.get("evidence_refs") or []]
    result["evidence_refs"] = [value for value in cited if value in set(known)] or list(dict.fromkeys(known))
    from partner.runtime.matched_execution import compare
    machine = compare(str(getattr(ctx, "workspace", "")),
                      params.get("baseline_receipt") or {}, params.get("candidate_receipt") or {})
    semantic["model_assessment"] = semantic.get("decision")
    semantic["decision"] = {"promoted": "promote", "rejected": "reject"}.get(machine["decision"], "inconclusive")
    semantic["matched_execution"] = machine
    if machine["decision"] == "inconclusive":
        semantic["reason"] = "尚无同输入、同测试的可信隔离执行证据；模型判断和来源链接不能证明改善。"
    result["learning_delta"] = machine["improved"]
    return result


def handoff_freeze(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Freeze sourced knowledge as a candidate for a later project round.

    Reading is not improvement.  The handoff is ready only when real downloaded
    sources, source-bound readings and an adoption candidate all exist.  Its
    downstream effect remains unverified until a later matched project action.
    """
    outputs = params.get('flow_outputs') if isinstance(params.get('flow_outputs'), dict) else {}
    def sem(name):
        row = outputs.get(name) if isinstance(outputs.get(name), dict) else {}
        return row.get('semantic_output') if isinstance(row.get('semantic_output'), dict) else {}
    retrieved = sem('retrieve').get('sources') or []
    # v2: support both old 'readings' and new 'borrowable_cards' output from source_read
    read_output = sem('read')
    readings = read_output.get('readings') or []
    borrowable_cards = read_output.get('borrowable_cards') or []
    synthesis = sem('synthesize')
    adoption = sem('adoption')
    # v4 fix: handoff source_urls 白名单过滤 file-evidence://（本地旧冻结文件不得进入冻结交接单）
    source_urls = {str(row.get('url') or '') for row in retrieved
                   if isinstance(row, dict) and not str(row.get('url') or '').startswith('file-evidence://')}
    # v2: reading_urls from either readings or borrowable_cards
    reading_urls = {str(row.get('url') or '') for row in readings if isinstance(row, dict)}
    reading_urls |= {str(card.get('source_url') or '') for card in borrowable_cards if isinstance(card, dict)}
    ready = bool(source_urls and reading_urls and reading_urls <= source_urls
                 and adoption.get('event_type') and adoption.get('hypothesis'))
    # User-facing digest: what was found and what it says, so the progress
    # message can tell the user which sources were read and their core ideas
    # instead of relaying raw URLs.
    retrieved_titles = {str(row.get('url') or ''): str(row.get('title') or '').strip()
                        for row in retrieved if isinstance(row, dict)}
    card_titles = {str(card.get('source_url') or ''): str(card.get('source_title') or '').strip()
                   for card in borrowable_cards if isinstance(card, dict)}
    def _title_for(url):
        for source in (card_titles, retrieved_titles):
            title = source.get(url)
            if title and title not in ('未命名资料', 'unknown'):
                return title
        # URL fallback so the user sees a human-readable name instead of
        # "未命名资料": arXiv paper id or GitHub repo path.
        if 'arxiv.org' in url:
            import re as _re
            match = _re.search(r'(\d{4}\.\d{4,5})', url)
            if match:
                return 'arXiv:' + match.group(1)
        for marker in ('raw.githubusercontent.com/', 'github.com/', 'raw.githubusercontent.com/'):
            if marker in url:
                tail = url.split(marker, 1)[-1].split('/', 1)[0]
                return 'GitHub:' + tail
        return ''
    ideas_by_url: dict[str, list[str]] = {}
    for card in borrowable_cards if isinstance(borrowable_cards, list) else []:
        url = str(card.get('source_url') or '')
        if url not in ideas_by_url:
            ideas_by_url[url] = []
        idea = str(card.get('core_idea') or '').strip()
        if idea:
            ideas_by_url[url].append(idea)
    source_ideas = []
    for url in sorted(source_urls):
        if not str(url).startswith('http'):
            continue
        title = _title_for(url) or '未命名资料'
        ideas = ideas_by_url.get(url) or []
        source_ideas.append({'url': url, 'title': title,
                             'core_ideas': ideas[:3], 'idea_count': len(ideas)})
    value = {
        'ready': ready, 'status': 'candidate_frozen' if ready else 'inconclusive',
        'source_urls': sorted(source_urls), 'reading_urls': sorted(reading_urls),
        'source_ideas': source_ideas,
        'source_receipts': [
            {key: row.get(key) for key in (
                'url', 'final_url', 'content_type', 'bytes', 'sha256',
                'text_sha256', 'text_chars', 'raw_path', 'text_path'
            ) if row.get(key) not in (None, '')}
            for row in retrieved if isinstance(row, dict)
        ],
        'candidate': adoption, 'synthesis': synthesis,
        'improvement_verified': False,
        'required_next_evidence': 'next project round must cite this handoff and produce matched downstream evidence',
    }
    path = Path(ctx.working_dir) / str(params.get('flow_id') or 'learning') / 'learning_handoff.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'ok': ready, 'status': 'completed' if ready else 'failed',
            'failure_class': '' if ready else 'learning_evidence',
            'error': '' if ready else 'learning handoff lacks sourced reading or adoption candidate',
            'semantic_output': value, 'summary': '主动学习候选已冻结，尚未宣称改善',
            'files': [str(path)], 'evidence_refs': [str(path)]}



def transfer_mapping(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Map borrowable_cards to executable project actions.

    Takes the borrowable_cards from source_read and produces a transfer_map
    specifying which project components to modify, how, and what evidence is needed.
    """
    readings = _semantic(params, 'read')
    cards = readings.get('borrowable_cards') or []
    if not cards:
        return {"ok": False, "status": "failed", "error": "no borrowable_cards to map"}
    
    # Get project context
    project_id = str(getattr(ctx, 'project_id', '') or params.get('project_id', ''))
    workspace = Path(getattr(ctx, 'workspace', ''))
    
    # Build project structure summary
    project_files = []
    if workspace.exists():
        for pattern in ['**/*.py', '**/*.md']:
            for f in workspace.glob(pattern):
                if 'partner' in str(f) and f.stat().st_size < 50000:
                    project_files.append(str(f.relative_to(workspace)))
                    if len(project_files) >= 20:
                        break
            if len(project_files) >= 20:
                break
    
    raw, usage = call_model(ctx, purpose='learning_transfer_mapping', prompt=(
        '你是项目改进规划助手。基于以下借鉴卡片，输出可执行的项目动作映射。\n'
        '项目 ID: ' + project_id + '\n'
        '项目文件结构（前 40 个）:\n' + '\n'.join(project_files[:40]) + '\n\n'
        '借鉴卡片:\n' + json.dumps(cards, ensure_ascii=False)[:16000] + '\n\n'
        '输出格式：\n'
        '{"transfer_map":[\n'
        '  {"from_source":"source_url","target_component":"相对路径或模块名",\n'
        '   "action":"具体改动描述","expected_effect":"预期效果",\n'
        '   "evidence_requirement":"需要什么证据验证"},\n'
        '  ...\n'
        '],"conflicts":"与现有设计的冲突点（若有）","adoptable":true/false}\n\n'
        '规则：\n'
        '- 每张卡片最多 2 个 transfer_map 条目\n'
        '- target_component 必须是项目中真实存在的文件或模块\n'
        '- action 必须具体可执行（不是"改进性能"这种模糊描述）\n'
        '- evidence_requirement 必须可验证（测试、指标、对比实验）\n'
        '- 如果卡片之间冲突或不可行，adoptable=false 并说明原因\n'
        '- 整个输出不超过 2000 汉字'
    ))
    
    try:
        value = json_object(raw)
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": f"failed to parse transfer_map: {exc}",
                "token_usage": usage}
    
    # Validate structure
    transfer_map = value.get('transfer_map') or []
    if not transfer_map:
        return {"ok": False, "status": "failed", "error": "transfer_map is empty",
                "token_usage": usage}
    
    # Check required fields
    for item in transfer_map:
        for field in ['from_source', 'target_component', 'action', 'expected_effect', 'evidence_requirement']:
            if field not in item:
                return {"ok": False, "status": "failed",
                        "error": f"transfer_map item missing required field: {field}",
                        "token_usage": usage}
    
    path = workspace / 'state' / 'event_runtime' / 'work' / str(getattr(ctx, 'job_id', 'learning')) / 'transfer_mapping.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, value)
    
    return {"ok": True, "status": "completed", "semantic_output": value,
            "files": [str(path)], "evidence_refs": [str(path)],
            "summary": f"已生成 {len(transfer_map)} 条项目动作映射",
            "learning_delta": False, "token_usage": usage}


DEFINITIONS = [
    EventDefinition("active_learning.question_formulate", "active_learning", "形成会改变项目决策的学习问题", question_formulate, execution_method="llm"),
    EventDefinition("active_learning.source_plan", "active_learning", "规划论文、官方文档和代码多源检索", source_plan, execution_method="llm"),
    EventDefinition("active_learning.source_retrieve", "active_learning", "下载并登记外部真实来源", source_retrieve, external_call=True, produces_artifact=True),
    EventDefinition("active_learning.source_read", "active_learning", "深入阅读已登记来源", source_read, external_call=True, reads_existing_artifact=True),
    EventDefinition("active_learning.claim_crosscheck", "active_learning", "主张级证据绑定和跨源反驳", claim_crosscheck, execution_method="llm"),
    EventDefinition("active_learning.synthesize", "active_learning", "形成影响项目决策的新知识", synthesize, execution_method="llm"),
    EventDefinition("active_learning.adoption_candidate", "active_learning", "形成外部知识采用 Candidate", adoption_candidate, execution_method="llm"),
    EventDefinition("active_learning.matched_verify", "active_learning", "基线/候选匹配验证知识采用价值", matched_verify),
    EventDefinition("active_learning.handoff_freeze", "active_learning", "冻结有来源的采用候选并等待下游改善验证", handoff_freeze, reads_existing_artifact=True, produces_artifact=True),
    EventDefinition("active_learning.transfer_mapping", "active_learning", "将借鉴卡片映射为可执行项目动作", transfer_mapping, execution_method="llm"),
]
