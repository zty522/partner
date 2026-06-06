"""External content feed for user-shared and self-collected research signals.

This module stores social/video/article snippets as lightweight records. It
does not scrape protected platforms or bypass login. User-shared content and
future autonomous crawlers both write into the same feed, so project loops can
turn external signals into hypotheses, notes, or next actions.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

from .content_tools import acquire_url, download_attachment_url, download_media_url, is_image_url, write_tool_status


URL_RE = re.compile(r"https?://[^\s，。；、<>\"']+", re.I)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _state_path(workspace: str, *parts: str) -> str:
    path = os.path.join(workspace, "state", *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _system_path(workspace: str, *parts: str) -> str:
    path = os.path.join(workspace, "system", *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _append_jsonl(path: str, row: dict[str, Any]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _clip(text: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def extract_urls(text: str) -> list[str]:
    urls = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(".,;，。；、)")
        if url not in urls:
            urls.append(url)
    return urls[:12]


def extract_raw_urls(raw: Any) -> list[str]:
    """Recursively extract URL-like fields from QQ/platform raw events."""
    found: list[str] = []

    def add(value: str):
        for url in extract_urls(value):
            if url not in found:
                found.append(url)

    def walk(obj: Any):
        if isinstance(obj, str):
            add(obj)
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_l = str(key).lower()
                if any(token in key_l for token in ("url", "uri", "src", "file", "media", "image", "thumb")):
                    if isinstance(value, str):
                        add(value)
                    else:
                        walk(value)
                elif isinstance(value, (dict, list, tuple)):
                    walk(value)
            return
        if isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)

    walk(raw)
    return found[:20]


def raw_has_media(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    media_keys = {"attachments", "media", "image", "images", "video", "file"}
    return any(bool(raw.get(key)) for key in media_keys)


def infer_platform(text: str, urls: list[str] | None = None) -> str:
    raw = (text or "").lower()
    joined = " ".join(urls or []).lower()
    probe = f"{raw} {joined}"
    checks = [
        ("bilibili", ("bilibili", "b23.tv", "b站", "b 站")),
        ("xiaohongshu", ("xiaohongshu", "xhslink", "小红书")),
        ("wechat", ("mp.weixin.qq.com", "公众号", "微信")),
        ("zhihu", ("zhihu.com", "知乎")),
        ("twitter_x", ("x.com", "twitter.com")),
        ("youtube", ("youtube.com", "youtu.be")),
    ]
    for name, needles in checks:
        if any(needle in probe for needle in needles):
            return name
    return "unknown"


def _non_url_text(text: str) -> str:
    return re.sub(URL_RE, " ", text or "").strip()


def infer_access_status(text: str, platform: str = "", urls: list[str] | None = None) -> str:
    """Classify how much real content Partner can see.

    This is intentionally conservative: if a platform card/link is not readable
    from the message itself, Partner should not infer the missing body.
    """
    raw = text or ""
    compact = re.sub(r"\s+", " ", _non_url_text(raw))
    probe = f"{raw} {' '.join(urls or [])}".lower()
    if any(is_image_url(url) for url in (urls or [])):
        return "media_available"
    if re.search(r"(仅支持.*app|app\s*内查看|打开.*app|复制.*打开|验证码|captcha|登录后|需要登录|403|无法访问|不可访问|正文获取限制)", probe, re.I):
        return "access_limited"
    if platform == "xiaohongshu" and re.search(r"(xhslink|xiaohongshu\.com/(?:discovery|explore|user|search)|小红书)", probe):
        if len(compact) < 180:
            return "access_limited"
    if platform in {"wechat", "bilibili", "zhihu"} and urls and len(compact) < 120:
        return "metadata_only"
    if urls and len(compact) < 80:
        return "link_only"
    if len(compact) >= 220:
        return "text_available"
    return "metadata_only"


def _project_relevance(project: str, text: str) -> bool:
    project_raw = (project or "").lower()
    raw = (text or "").lower()
    if not project_raw or not raw:
        return False
    tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,}", project_raw))
    if not tokens:
        return False
    return any(token in raw for token in list(tokens)[:12])


def infer_content_intent(
    text: str,
    project: str = "",
    platform: str = "",
    urls: list[str] | None = None,
    access_status: str = "",
) -> str:
    """Separate instruction, project reference, casual learning, and inaccessible links."""
    access_status = access_status or infer_access_status(text, platform, urls)
    compact = _non_url_text(text)
    raw = (text or "").lower()
    if access_status == "media_available" and project:
        return "project_reference"
    if access_status == "access_limited" and len(compact) < 180:
        return "access_limited"
    directive = re.search(
        r"(尝试|试试|加入|纳入|加进去|用这个|改成|测试|验证|推进|研究|分析|帮我|你可以|"
        r"结合|继续|参考这个|按这个|做一下|看看.*能不能|把.*放进|把.*加入)",
        text or "",
    )
    if directive:
        return "project_instruction" if project else "general_instruction"
    if project and _project_relevance(project, raw):
        return "project_reference"
    # 用户随手分享的长文/科普/视频，不应自动改变项目主线。
    return "general_learning"


def looks_like_external_content(text: str, raw: Any = None) -> bool:
    if extract_urls(text):
        return True
    if re.search(r"(小红书|b站|B站|公众号|知乎|视频|推文|帖子|笔记|刷到|看到|分享|链接|截图|转载)", text or ""):
        return True
    if isinstance(raw, dict):
        rich_keys = {"attachments", "media", "image", "images", "video", "file"}
        if any(key in raw for key in rich_keys):
            return True
    return False


def _load_feed(workspace: str) -> dict[str, Any]:
    path = _state_path(workspace, "content_feed.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("items", [])
            return data
    except Exception:
        pass
    return {"version": 1, "updated_at": _now(), "items": []}


def _save_feed(workspace: str, feed: dict[str, Any]):
    feed["updated_at"] = _now()
    items = feed.get("items") or []
    seen = set()
    compact = []
    for item in reversed(items):
        key = tuple(item.get("urls") or []) or (item.get("text", "")[:160], item.get("platform", ""))
        if key in seen:
            continue
        seen.add(key)
        compact.append(item)
        if len(compact) >= 300:
            break
    feed["items"] = list(reversed(compact))
    with open(_state_path(workspace, "content_feed.json"), "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)
    _write_user_summary(workspace, feed)


def _content_key(item: dict[str, Any]) -> tuple:
    urls = tuple(item.get("urls") or [])
    if urls:
        return ("urls", urls)
    return ("text", (item.get("text") or "")[:220], item.get("platform") or "")


def _find_existing_item(feed: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any] | None:
    key = _content_key(candidate)
    for item in reversed(feed.get("items") or []):
        if _content_key(item) == key:
            return item
    return None


def record_shared_content(
    workspace: str,
    *,
    text: str,
    project: str = "",
    sender: str = "",
    source: str = "user_share",
    raw: Any = None,
) -> dict[str, Any] | None:
    """Record one external content signal if it looks relevant."""
    if not looks_like_external_content(text, raw):
        return None
    text_urls = extract_urls(text)
    raw_urls = extract_raw_urls(raw)
    urls = []
    for url in [*text_urls, *raw_urls]:
        if url not in urls:
            urls.append(url)
    platform = infer_platform(text, urls)
    access_status = infer_access_status(text, platform, urls)
    intent = infer_content_intent(text, project, platform, urls, access_status)
    acquisition = {}
    media_files: list[dict[str, str]] = []
    has_raw_media = raw_has_media(raw)
    attachment_results: list[dict[str, str]] = []
    if has_raw_media and raw_urls:
        attachment_dir = _system_path(workspace, "attachments")
        for url in raw_urls[:4]:
            result = download_attachment_url(url, attachment_dir)
            row = result.to_dict()
            attachment_results.append(row)
        ok_text = [row for row in attachment_results if row.get("status") == "text_available" and row.get("text_preview")]
        ok_image = [row for row in attachment_results if row.get("status") == "image_available" and row.get("text_preview")]
        ok_file = [row for row in attachment_results if row.get("status") == "file_available" and row.get("text_preview")]
        if ok_text:
            access_status = "text_available"
            acquisition = {
                "mode": "attachment_download",
                "status": "text_available",
                "attachments": ok_text,
                "all_attachment_results": attachment_results,
                "text_preview": "\n\n".join(str(row.get("text_preview") or "") for row in ok_text[:2]),
            }
        elif ok_image:
            access_status = "media_available"
            acquisition = {
                "mode": "attachment_download",
                "status": "image_available",
                "attachments": ok_image,
                "all_attachment_results": attachment_results,
            }
        elif ok_file:
            access_status = "file_available"
            acquisition = {
                "mode": "attachment_download",
                "status": "file_available",
                "attachments": ok_file,
                "all_attachment_results": attachment_results,
                "text_preview": "\n".join(str(row.get("text_preview") or "") for row in ok_file[:3]),
            }
    media_urls = [
        url for url in urls
        if is_image_url(url)
        or (has_raw_media and url in raw_urls)
        or "multimedia.nt.qq.com.cn/" in url.lower()
    ]
    if media_urls and not acquisition:
        media_dir = _system_path(workspace, "media")
        for url in media_urls[:4]:
            result = download_media_url(url, media_dir)
            row = result.to_dict()
            media_files.append(row)
        ok_files = [row for row in media_files if row.get("status") == "image_available" and row.get("text_preview")]
        if ok_files:
            access_status = "media_available"
            acquisition = {
                "mode": "media_download",
                "status": "image_available",
                "media_files": ok_files,
                "all_media_results": media_files,
            }
    if urls and access_status not in {"media_available", "text_available", "file_available"}:
        try:
            result = acquire_url(urls[0])
            acquisition = result.to_dict()
            if result.status == "text_available":
                access_status = "text_available"
            elif result.status in {"access_limited", "needs_user_body", "fetch_failed"}:
                access_status = "access_limited"
        except Exception as exc:
            acquisition = {
                "mode": "public_web",
                "status": "fetch_failed",
                "reason": str(exc)[:180],
                "next_request": "请用户转发正文、截图或摘要。",
            }
    should_nudge_project = intent in {"project_instruction", "project_reference"} and access_status != "access_limited"
    feed = _load_feed(workspace)
    item = {
        "id": f"cf_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
        "time": _now(),
        "source": source,
        "sender": sender,
        "project": project or "",
        "platform": platform,
        "intent": intent,
        "access_status": access_status,
        "scope": "project" if intent in {"project_instruction", "project_reference"} else "general",
        "should_nudge_project": should_nudge_project,
        "urls": urls,
        "text": _clip(text, 1200),
        "acquisition": acquisition,
        "media_files": media_files,
        "attachment_files": attachment_results,
        "visible_body": _clip(
            (acquisition.get("text_preview") if isinstance(acquisition, dict) else "") or _non_url_text(text),
            1800,
        ),
        "raw_hint": _raw_hint(raw),
        "status": "open",
        "digest": "",
        "hypotheses": [],
        "risk": "",
    }
    existing = _find_existing_item(feed, item)
    if existing:
        # Preserve the first id/status so a queued digest still targets one stable item.
        existing["time"] = existing.get("time") or item["time"]
        existing["project"] = existing.get("project") or item["project"]
        existing["sender"] = existing.get("sender") or item["sender"]
        existing["source"] = existing.get("source") or item["source"]
        # If a first pass recorded the text as generic content and a later
        # interaction decision resolves it to a project, upgrade the record.
        if item.get("project") and not existing.get("project"):
            existing["project"] = item["project"]
        if item.get("intent") in {"project_instruction", "project_reference"}:
            existing["intent"] = item["intent"]
            existing["scope"] = "project"
            existing["should_nudge_project"] = item.get("access_status") != "access_limited"
        else:
            existing.setdefault("intent", item["intent"])
            existing.setdefault("scope", item["scope"])
            existing.setdefault("should_nudge_project", item["should_nudge_project"])
        # Upgrade weak/old records when a later pass successfully downloads or
        # parses the same attachment/link. This fixes cases where a QQ file was
        # first stored as binary PK/metadata, then a document parser became
        # available.
        if item.get("access_status") in {"text_available", "media_available", "file_available"}:
            existing["access_status"] = item["access_status"]
            existing["acquisition"] = item.get("acquisition") or existing.get("acquisition") or {}
            existing["media_files"] = item.get("media_files") or existing.get("media_files") or []
            existing["attachment_files"] = item.get("attachment_files") or existing.get("attachment_files") or []
            existing["visible_body"] = item.get("visible_body") or existing.get("visible_body") or ""
        else:
            existing.setdefault("access_status", item["access_status"])
        _save_feed(workspace, feed)
        return existing
    feed.setdefault("items", []).append(item)
    _save_feed(workspace, feed)
    write_tool_status(workspace)
    _append_jsonl(_system_path(workspace, "content_feed", "inbox.jsonl"), item)
    return item


def _raw_hint(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    keys = []
    for key in ("attachments", "media", "image", "images", "video", "file"):
        if key in raw:
            keys.append(key)
    return ",".join(keys)


def get_open_content_items(workspace: str, project: str = "", limit: int = 3) -> list[dict[str, Any]]:
    feed = _load_feed(workspace)
    out = []
    for item in reversed(feed.get("items") or []):
        if str(item.get("status", "open")).lower() not in {"open", "unprocessed", ""}:
            continue
        if project and item.get("project") and item.get("project") != project:
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return list(reversed(out))


def mark_content_processed(workspace: str, item_id: str, digest: str = "", status: str = "digested"):
    if not item_id:
        return
    feed = _load_feed(workspace)
    for item in feed.get("items") or []:
        if item.get("id") == item_id:
            item["status"] = status
            item["processed_at"] = _now()
            if digest:
                item["digest"] = _clip(digest, 1200)
            break
    _save_feed(workspace, feed)


def ensure_content_sources(workspace: str) -> dict[str, Any]:
    """Create/read controlled autonomous content patrol configuration."""
    path = _system_path(workspace, "content_feed", "sources.json")
    defaults = {
        "enabled": False,
        "interval_hours": 6,
        "topic": "",
        "rules": [
            "只访问公开页面、公开搜索结果或用户提供链接",
            "不绕过登录、不破解反爬、不批量抓取",
            "平台需要登录、权限或验证码时，只记录不可访问和原因",
            "每轮最多提炼 3 条内容信号",
            "内容只作为 hypothesis，不作为事实证据",
        ],
        "sources": [],
    }
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged = {**defaults, **data}
                merged["sources"] = data.get("sources") or defaults["sources"]
                write_tool_status(workspace)
                return merged
        except Exception:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(defaults, f, ensure_ascii=False, indent=2)
    write_tool_status(workspace)
    return defaults


def content_patrol_enabled(workspace: str) -> bool:
    cfg = ensure_content_sources(workspace)
    if bool(cfg.get("enabled")):
        return True
    # Instance 05 is conventionally the autonomous media-learning partner.
    norm = os.path.normpath(workspace)
    return norm.endswith(os.sep + "05") or os.path.basename(norm) == "05"


def build_patrol_prompt_context(workspace: str, limit_chars: int = 1800) -> str:
    cfg = ensure_content_sources(workspace)
    enabled_sources = [s for s in (cfg.get("sources") or []) if s.get("enabled")]
    if not enabled_sources:
        enabled_sources = (cfg.get("sources") or [])[:3]
    lines = [
        "# 自主内容巡游配置",
        f"主题：{cfg.get('topic', '')}",
        "规则：",
    ]
    for rule in cfg.get("rules") or []:
        lines.append(f"- {rule}")
    lines.append("入口：")
    for source in enabled_sources[:6]:
        lines.append(f"- {source.get('platform','unknown')}: {source.get('url','')}")
    text = "\n".join(lines)
    if len(text) <= limit_chars:
        return text
    return text[: max(0, limit_chars - 1)].rstrip() + "…"


def build_content_feed_context(workspace: str, project: str = "", limit_chars: int = 5000) -> str:
    feed = _load_feed(workspace)
    items = []
    for item in reversed(feed.get("items") or []):
        if project and item.get("project") and item.get("project") != project:
            continue
        if not bool(item.get("should_nudge_project", False)) and item.get("intent") not in {"project_reference", "project_instruction"}:
            continue
        if not (item.get("visible_body") or item.get("text") or item.get("digest")):
            continue
        items.append(item)
        if len(items) >= 3:
            break
    items = list(reversed(items))
    if not items:
        return ""
    lines = ["外部内容素材（用户/自巡游未消化信号）："]
    for item in items:
        urls = " ".join(item.get("urls") or [])
        visible = item.get("visible_body") or item.get("text", "")
        attachment_status = ""
        attachments = item.get("attachment_files") or []
        if any((row.get("status") == "text_available" and row.get("text_preview")) for row in attachments if isinstance(row, dict)):
            attachment_status = " attachment_text=available"
        lines.append(
            f"- id={item.get('id')} platform={item.get('platform','unknown')} "
            f"intent={item.get('intent','unknown')} access={item.get('access_status','unknown')} "
            f"source={item.get('source','')}{attachment_status} text={_clip(visible, 1400)}"
            + (f" urls={urls}" if urls else "")
        )
    text = "\n".join(lines)
    if len(text) <= limit_chars:
        return text
    return text[: max(0, limit_chars - 1)].rstrip() + "…"


def _write_user_summary(workspace: str, feed: dict[str, Any]):
    user_dir = os.path.join(workspace, "user")
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, "content_feed_summary.md")
    items = feed.get("items") or []
    lines = ["# 外部内容素材箱", ""]
    lines.append("这里记录用户分享或 Partner 自主巡游获得的外部内容信号。")
    lines.append("读不到正文时会明确标记 access_limited/metadata_only，不把链接卡片当作全文。")
    lines.append("")
    for item in reversed(items[-20:]):
        urls = " ".join(item.get("urls") or [])
        acq = item.get("acquisition") if isinstance(item.get("acquisition"), dict) else {}
        title = acq.get("title") or ""
        next_request = acq.get("next_request") or ""
        lines.append(
            f"- [{item.get('time')}] {item.get('platform','unknown')} "
            f"{item.get('status','open')} / {item.get('intent','unknown')} / {item.get('access_status','unknown')}："
            f"{_clip(title or item.get('visible_body') or item.get('text',''), 180)}"
            + (f" {urls}" if urls else "")
        )
        if next_request:
            lines.append(f"  - 获取限制：{_clip(next_request, 120)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
