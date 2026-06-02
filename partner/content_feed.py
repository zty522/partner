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
    urls = extract_urls(text)
    platform = infer_platform(text, urls)
    feed = _load_feed(workspace)
    item = {
        "id": f"cf_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
        "time": _now(),
        "source": source,
        "sender": sender,
        "project": project or "",
        "platform": platform,
        "urls": urls,
        "text": _clip(text, 1200),
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
        _save_feed(workspace, feed)
        return existing
    feed.setdefault("items", []).append(item)
    _save_feed(workspace, feed)
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
        "topic": "AI Agent 发展、长期自主 agent、agent 评测、工具使用、多智能体",
        "rules": [
            "只访问公开页面、公开搜索结果或用户提供链接",
            "不绕过登录、不破解反爬、不批量抓取",
            "小红书/B站/知乎若需要登录，则只记录不可访问和原因",
            "每轮最多提炼 3 条内容信号",
            "内容只作为 hypothesis，不作为事实证据",
        ],
        "sources": [
            {"platform": "zhihu", "url": "https://www.zhihu.com/search?q=AI%20Agent", "enabled": False},
            {"platform": "bilibili", "url": "https://search.bilibili.com/all?keyword=AI%20Agent", "enabled": False},
            {"platform": "xiaohongshu", "url": "https://www.xiaohongshu.com/search_result?keyword=AI%20Agent", "enabled": False},
        ],
    }
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged = {**defaults, **data}
                merged["sources"] = data.get("sources") or defaults["sources"]
                return merged
        except Exception:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(defaults, f, ensure_ascii=False, indent=2)
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


def build_content_feed_context(workspace: str, project: str = "", limit_chars: int = 1000) -> str:
    items = get_open_content_items(workspace, project=project, limit=3)
    if not items:
        return ""
    lines = ["外部内容素材（用户/自巡游未消化信号）："]
    for item in items:
        urls = " ".join(item.get("urls") or [])
        lines.append(
            f"- id={item.get('id')} platform={item.get('platform','unknown')} "
            f"source={item.get('source','')} text={_clip(item.get('text',''), 180)}"
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
    lines.append("")
    for item in reversed(items[-20:]):
        urls = " ".join(item.get("urls") or [])
        lines.append(
            f"- [{item.get('time')}] {item.get('platform','unknown')} "
            f"{item.get('status','open')}：{_clip(item.get('text',''), 160)}"
            + (f" {urls}" if urls else "")
        )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
