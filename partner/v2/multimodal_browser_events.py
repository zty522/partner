"""Evidence-first multimodal browser observation and bounded community reading."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from partner.governance.project_reasoning import project_reasoning_contract

from .browser import _run_worker
from .vision_events import read_image_with_qwen


PLATFORMS = {
    "xiaohongshu": {
        "start_url": "https://www.xiaohongshu.com/explore",
        "hosts": {"xiaohongshu.com", "www.xiaohongshu.com", "creator.xiaohongshu.com"},
    },
    "wechat_official": {
        "start_url": "https://mp.weixin.qq.com/",
        "hosts": {"mp.weixin.qq.com"},
    },
    "tencent_cloud": {
        "start_url": "https://cloud.tencent.com/developer",
        "hosts": {"cloud.tencent.com"},
    },
    "alibaba_cloud": {
        "start_url": "https://developer.aliyun.com/",
        "hosts": {"developer.aliyun.com"},
    },
}


def _task_dir(ctx: Any) -> Path:
    task = getattr(ctx, "task_instance", None)
    value = str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "") or "")
    path = Path(value) if value else Path(str(getattr(ctx, "workspace", "") or ".")) / "state/multimodal"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_url(platform: str, url: str) -> str:
    config = PLATFORMS.get(platform)
    if not config:
        raise ValueError("unsupported_platform")
    candidate = str(url or config["start_url"]).strip()
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in config["hosts"]:
        raise ValueError("url_outside_platform_allowlist")
    return candidate


def _json_from_llm(prompt: str) -> dict[str, Any]:
    def parse(raw: str) -> dict[str, Any]:
        cleaned = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S | re.I)
        decoder = json.JSONDecoder()
        values: list[tuple[int, dict[str, Any]]] = []
        # raw_decode from every opening brace handles nested objects correctly;
        # a non-greedy regex does not (it stops at the first inner closing brace).
        for match in re.finditer(r"\{", cleaned):
            try:
                value, end = decoder.raw_decode(cleaned[match.start():])
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                values.append((end, value))
        # Prefer the widest valid object.  Scanning also discovers nested dicts,
        # which are valid JSON but are not the requested terminal contract.
        return max(values, key=lambda row: row[0])[1] if values else {}
    try:
        from partner.adapters.direct_api import chat
        raw = chat(prompt, purpose="multimodal_browser_reasoning", max_tokens=5200,
                   temperature=0.2, timeout=180)
        value = parse(str(raw or ""))
        if value:
            value["_llm_calls"] = 1
            return value
        repair = chat(
            "Convert the supplied unfinished browser analysis into ONE strict JSON object only. "
            "Use only evidence already present; do not add facts. Required keys: page_kind, login_state, "
            "evidence_agreement, contradictions, selected_link_index, selection_reason, main_hypothesis, "
            "opposing_hypothesis, falsifier, safe_next_action.\nANALYSIS:\n" + str(raw or "")[-12000:],
            purpose="multimodal_browser_json_reduction", max_tokens=2200,
            temperature=0.0, timeout=120)
        value = parse(str(repair or ""))
        if value:
            value["_llm_calls"] = 2
            return value
        return {"error": "llm_json_terminal_missing", "_llm_calls": 2}
    except Exception as exc:
        return {"error": type(exc).__name__}


def _dom_snapshot(ctx: Any) -> dict[str, Any]:
    result = _run_worker(ctx, "execute", {"script": """() => {
      const visible = el => { const r=el.getBoundingClientRect(); const s=getComputedStyle(el);
        return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden'; };
      const links=[...document.querySelectorAll('a[href]')].filter(visible).slice(0,80)
        .map((el,index)=>({index,text:(el.innerText||el.textContent||'').trim().slice(0,160),href:el.href}));
      const controls=[...document.querySelectorAll('button,input,[role=button]')].filter(visible).slice(0,40)
        .map((el,index)=>({index,tag:el.tagName,type:el.type||'',text:(el.innerText||el.value||el.placeholder||'').trim().slice(0,120)}));
      return {url:location.href,title:document.title||'',body:(document.body?.innerText||'').trim().slice(0,12000),links,controls};
    }"""})
    return result.get("result") if result.get("status") == "ok" and isinstance(result.get("result"), dict) else {}


def _login_state(dom: dict[str, Any]) -> str:
    body = str(dom.get("body") or "")
    controls = json.dumps(dom.get("controls") or [], ensure_ascii=False)
    # Generic navigation labels (消息/创作中心/个人中心) may be rendered
    # behind a login overlay and therefore are not authentication proof.
    positive = ("退出登录", "账号设置", "切换账号")
    # A header login button or copy such as “登录后评论” gates engagement,
    # not public reading.  Only explicit auth-wall mechanisms count here.
    wall = ("扫码登录", "微信登录", "手机号登录", "请先登录", "登录以继续", "登录后查看全文")
    if any(token in body or token in controls for token in wall):
        return "login_required"
    if any(token in body for token in positive):
        return "authenticated_signal_present"
    return "unknown"


def _blocking_state(dom: dict[str, Any]) -> str:
    text = f"{dom.get('title', '')}\n{dom.get('body', '')}".lower()
    challenge_cues = ("captcha verification", "人机验证", "安全验证", "滑块验证", "完成验证后继续")
    if any(cue in text for cue in challenge_cues):
        return "challenge_detected"
    return ""


def _observe(ctx: Any, platform: str, url: str, label: str) -> dict[str, Any]:
    safe = _safe_url(platform, url)
    opened = _run_worker(ctx, "open", {"url": safe, "bring_to_front": True}, visible=True)
    if opened.get("status") != "ok":
        return {"ok": False, "status": "open_failed", "error": opened.get("error", "")}
    dom = _dom_snapshot(ctx)
    if not dom:
        return {"ok": False, "status": "dom_snapshot_failed", "opened": opened}
    # Redirects are checked after navigation as well as before it.
    try:
        _safe_url(platform, str(dom.get("url") or safe))
    except ValueError as exc:
        return {"ok": False, "status": str(exc), "opened": opened, "dom": dom}
    directory = _task_dir(ctx)
    image_path = directory / f"{label}.png"
    shot = _run_worker(ctx, "screenshot", {"full_page": False, "save_path": str(image_path)})
    if shot.get("status") != "ok" or not image_path.is_file():
        return {"ok": False, "status": "screenshot_failed", "dom": dom,
                "error": shot.get("error", "")}
    vision = read_image_with_qwen(
        str(image_path),
        "请严格描述这个真实浏览器截图：页面平台、标题、主要帖子或文章、登录状态提示和可见交互控件。"
        "区分截图中确实可见与推测内容，不要声称发生了截图之外的点击或登录。",
        str(getattr(ctx, "workspace", "") or ""),
    )
    reasoning = _json_from_llm(
        "Reconcile deterministic DOM evidence with a probabilistic vision description. "
        "Choose at most one link index from the supplied DOM links for substantive reading. "
        "Never propose posting, liking, commenting, following, uploading, credential entry, or a link outside the platform. "
        "Return strict JSON with page_kind, login_state, evidence_agreement, contradictions, "
        "selected_link_index, selection_reason, main_hypothesis, opposing_hypothesis, falsifier, safe_next_action.\n"
        + project_reasoning_contract() + "\n"
        + json.dumps({"platform": platform, "goal": "read substantive public community content",
                      "dom": {**dom, "body": str(dom.get("body") or "")[:4000],
                              "links": list(dom.get("links") or [])[:25],
                              "controls": list(dom.get("controls") or [])[:20]},
                      "deterministic_login_state": _login_state(dom),
                      "vision": vision}, ensure_ascii=False)
    )
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    reasoning_calls = int(reasoning.pop("_llm_calls", 1) or 1)
    blocker = _blocking_state(dom)
    record = {"ok": bool(vision.get("ok") and reasoning and not reasoning.get("error") and not blocker),
              "status": blocker or ("observed" if vision.get("ok") else "vision_failed"),
              "platform": platform, "url": dom.get("url"), "title": dom.get("title"),
              "deterministic_login_state": _login_state(dom), "blocking_state": blocker, "dom": dom,
              "screenshot_path": str(image_path), "screenshot_sha256": digest,
              "vision": vision, "reasoning": reasoning,
              "model_calls": {"vision": 1, "reasoning": reasoning_calls}}
    evidence_path = directory / f"{label}.json"
    evidence_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record["evidence_path"] = str(evidence_path)
    return record


def atomic_multimodal_browser_observe(ctx: Any, params: dict) -> dict:
    platform = str(params.get("platform") or "").strip()
    try:
        url = _safe_url(platform, str(params.get("url") or ""))
    except ValueError as exc:
        return {"ok": False, "status": str(exc), "production_effective": False}
    return _observe(ctx, platform, url, "multimodal_observe")


def atomic_multimodal_community_read(ctx: Any, params: dict) -> dict:
    platform = str(params.get("platform") or "").strip()
    try:
        start_url = _safe_url(platform, str(params.get("url") or ""))
    except ValueError as exc:
        return {"ok": False, "status": str(exc), "production_effective": False}
    first = _observe(ctx, platform, start_url, "community_read_01_entry")
    if not first.get("ok"):
        return {"ok": False, "status": first.get("status"), "entry": first,
                "production_effective": False}
    if first.get("deterministic_login_state") == "login_required":
        return {"ok": True, "status": "awaiting_user_login", "entry": first,
                "content": "已将登录页面打开到前台；请手动完成登录。未读取或输入任何凭据。",
                "production_effective": False,
                "model_calls": 1 + int((first.get("model_calls") or {}).get("reasoning") or 0)}
    links = list((first.get("dom") or {}).get("links") or [])
    try:
        selected_index = int((first.get("reasoning") or {}).get("selected_link_index"))
        selected = next(row for row in links if int(row.get("index", -1)) == selected_index)
        selected_url = _safe_url(platform, str(selected.get("href") or ""))
    except (StopIteration, TypeError, ValueError):
        return {"ok": False, "status": "no_safe_reading_link_selected", "entry": first,
                "production_effective": False}
    second = _observe(ctx, platform, selected_url, "community_read_02_article")
    if not second.get("ok"):
        return {"ok": False, "status": second.get("status"), "entry": first,
                "article": second, "production_effective": False}
    result = {"ok": True, "status": "article_observed", "platform": platform,
              "selected_link": selected, "entry": first, "article": second,
              "model_calls": (2 + int((first.get("model_calls") or {}).get("reasoning") or 0)
                              + int((second.get("model_calls") or {}).get("reasoning") or 0)),
              "business_metrics": {"pages_observed": 2, "vision_calls": 2,
                                   "reasoning_calls": (
                                       int((first.get("model_calls") or {}).get("reasoning") or 0)
                                       + int((second.get("model_calls") or {}).get("reasoning") or 0)),
                                   "articles_opened": 1}}
    output = _task_dir(ctx) / "multimodal_community_read.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["evidence_path"] = str(output)
    result["content"] = (f"已真实观察 {first.get('title')}，并打开一条受限链接："
                         f"{second.get('title')}。DOM、截图、视觉描述和 LLM 判断均已保存；未执行任何互动或发布。")
    return result


def atomic_multimodal_login_resume(ctx: Any, params: dict) -> dict:
    """Observe a foreground login page and resume only on deterministic auth evidence.

    Credentials, QR codes and cookies are never read or entered by this Event.  An
    unknown public page is not silently treated as an authenticated session.
    """
    platform = str(params.get("platform") or "").strip()
    try:
        url = _safe_url(platform, str(params.get("url") or ""))
    except ValueError as exc:
        return {"ok": False, "status": str(exc), "production_effective": False}
    observed = _observe(ctx, platform, url, "multimodal_login_resume")
    if not observed.get("ok"):
        return {"ok": False, "status": observed.get("status"), "observation": observed,
                "production_effective": False}
    state = observed.get("deterministic_login_state")
    if state == "authenticated_signal_present":
        return {"ok": True, "status": "login_verified", "observation": observed,
                "content": "已从页面可见信号确认登录状态，可以继续只读任务。",
                "production_effective": False}
    if state == "login_required":
        return {"ok": True, "status": "awaiting_user_login", "observation": observed,
                "content": "登录页已打开到前台，请手动登录；Partner 未读取或填写凭据。",
                "production_effective": False}
    return {"ok": False, "status": "login_state_inconclusive", "observation": observed,
            "content": "页面证据不足以确认已登录，未擅自继续。",
            "production_effective": False}


HANDLERS = {
    "multimodal_browser_observe": atomic_multimodal_browser_observe,
    "multimodal_community_read": atomic_multimodal_community_read,
    "multimodal_login_resume": atomic_multimodal_login_resume,
}


__all__ = ["atomic_multimodal_browser_observe", "atomic_multimodal_community_read",
           "atomic_multimodal_login_resume", "PLATFORMS"]
