"""Lightweight external content acquisition tools.

These are ordinary Partner tools, not Hermes skills.  They keep social/article
ingestion visible and bounded without bloating the agent prompt or skill list.
The default path only reads public web pages.  Login browser, OCR, and video
transcripts are optional capabilities that must be explicitly configured.
"""

from __future__ import annotations

import html
import base64
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Any


BLOCKED_DOMAINS = (
    "xiaohongshu.com",
    "xhslink.com",
    "bilibili.com",
    "b23.tv",
)


def _login_browser_configured() -> bool:
    return (
        os.getenv("PARTNER_ENABLE_BROWSER_LOGIN", "").lower() in {"1", "true", "on", "yes"}
        and bool(os.getenv("PARTNER_BROWSER_PROFILE", "").strip())
    )


@dataclass
class AcquisitionResult:
    mode: str
    status: str
    title: str = ""
    text_preview: str = ""
    reason: str = ""
    next_request: str = ""
    source_url: str = ""
    tool_name: str = ""
    files: list[str] = None
    media_urls: list[str] = None
    metadata: dict[str, Any] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["files"] = data.get("files") or []
        data["media_urls"] = data.get("media_urls") or []
        data["metadata"] = data.get("metadata") or {}
        return data


def _clip(text: str, limit: int = 1600) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _clean_document_text(text: str) -> str:
    """Remove common Office XML shape/style noise from extracted text."""
    if not text:
        return ""
    noise_patterns = [
        r"\bstyle\.visibility\b",
        r"\bppt_[a-z]\b",
        r"\bppt_[whxy]\b",
        r"\bvisibility\b",
        r"\bxml\b",
    ]
    for pattern in noise_patterns:
        text = re.sub(pattern, " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?:\s+[A-Za-z_]{1,4}){6,}", " ", text)
    return text.strip()


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
DOC_EXTS = (".docx", ".pptx", ".xlsx", ".pdf", ".txt", ".md", ".csv", ".tsv")
VIDEO_EXTS = (".mp4", ".m4v", ".mov", ".webm", ".mkv")


def is_image_url(url: str) -> bool:
    raw = (url or "").split("?", 1)[0].lower()
    return raw.endswith(IMAGE_EXTS)


def _looks_like_image_bytes(raw: bytes) -> bool:
    if not raw:
        return False
    return (
        raw.startswith(b"\xff\xd8\xff")
        or raw.startswith(b"\x89PNG\r\n\x1a\n")
        or raw.startswith(b"GIF87a")
        or raw.startswith(b"GIF89a")
        or raw.startswith(b"RIFF") and b"WEBP" in raw[:16]
    )


def _guess_ext(url: str, ctype: str, raw: bytes) -> str:
    path_ext = os.path.splitext((url or "").split("?", 1)[0])[1].lower()
    if path_ext in IMAGE_EXTS + DOC_EXTS:
        return path_ext
    ctype_l = (ctype or "").lower()
    if _looks_like_image_bytes(raw):
        if raw.startswith(b"\x89PNG"):
            return ".png"
        if raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
            return ".webp"
        if raw.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        return ".jpg"
    if raw.startswith(b"%PDF") or "pdf" in ctype_l:
        return ".pdf"
    if raw.startswith(b"PK\x03\x04"):
        # Office OpenXML files are zip containers. Inspect entries to classify.
        try:
            import io

            with zipfile.ZipFile(io.BytesIO(raw[:4_000_000])) as zf:
                names = set(zf.namelist())
            if any(name.startswith("word/") for name in names):
                return ".docx"
            if any(name.startswith("ppt/") for name in names):
                return ".pptx"
            if any(name.startswith("xl/") for name in names):
                return ".xlsx"
        except Exception:
            pass
        return ".zip"
    if "text/" in ctype_l or "json" in ctype_l or "xml" in ctype_l:
        return ".txt"
    return ".bin"


def _xml_text(raw: bytes) -> str:
    try:
        root = ET.fromstring(raw)
    except Exception:
        return ""
    chunks: list[str] = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        # Prefer visible text nodes. Office XML also contains many shape/layout
        # attributes that look like text but are not user content.
        if tag not in {"t", "instrText", "v"}:
            continue
        if node.text and node.text.strip():
            chunks.append(node.text.strip())
    if not chunks:
        for node in root.iter():
            if node.text and node.text.strip():
                chunks.append(node.text.strip())
    return _clean_document_text(" ".join(chunks))


def _extract_docx(path: str) -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                text = _xml_text(zf.read(name))
                if text:
                    chunks.append(text)
    return "\n".join(chunks)


def _extract_pptx(path: str) -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in sorted(zf.namelist()):
            if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
                text = _xml_text(zf.read(name))
                if text:
                    chunks.append(text)
    return "\n".join(chunks)


def _extract_xlsx(path: str) -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared_text = _xml_text(zf.read("xl/sharedStrings.xml"))
            shared = [x.strip() for x in re.split(r"\s{2,}|\n", shared_text) if x.strip()]
        if shared:
            chunks.append("Shared strings: " + " | ".join(shared[:300]))
        for name in sorted(zf.namelist()):
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                text = _xml_text(zf.read(name))
                if text:
                    chunks.append(text)
    return "\n".join(chunks)


def _extract_pdf(path: str) -> str:
    # Prefer command-line pdftotext when available; fall back to pypdf if present.
    if shutil.which("pdftotext"):
        out = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", path, "-"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if out.stdout.strip():
            return out.stdout
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages[:40])
    except Exception:
        return ""


def _extract_attachment_text(path: str, ext: str) -> str:
    try:
        if ext == ".docx":
            return _extract_docx(path)
        if ext == ".pptx":
            return _extract_pptx(path)
        if ext == ".xlsx":
            return _extract_xlsx(path)
        if ext == ".pdf":
            return _extract_pdf(path)
        if ext in {".txt", ".md", ".csv", ".tsv"}:
            with open(path, "rb") as f:
                return f.read(1_500_000).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    return ""


def download_attachment_url(url: str, dest_dir: str, timeout: int = 30) -> AcquisitionResult:
    """Download a QQ/platform attachment and extract text when possible.

    This is deliberately generic and conservative: it saves the file first,
    detects by magic bytes/content, then extracts common document formats.
    """
    if not re.match(r"(?i)^https?://", url or ""):
        return AcquisitionResult(
            mode="attachment_download",
            status="access_limited",
            reason="不是 HTTP(S) 附件地址。",
            next_request="请提供文件路径、正文或可下载链接。",
            source_url=url,
        )
    os.makedirs(dest_dir, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 PartnerResearchBot/0.6 (attachment reader)",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(25_000_000)
        if not raw:
            return AcquisitionResult(
                mode="attachment_download",
                status="fetch_failed",
                reason="附件下载为空。",
                next_request="请重新发送文件或提供本地路径。",
                source_url=url,
            )
        ext = _guess_ext(url, ctype, raw)
        name = f"attachment_{abs(hash(url))}{ext}"
        path = os.path.join(dest_dir, name)
        with open(path, "wb") as f:
            f.write(raw)
        if ext in IMAGE_EXTS or _looks_like_image_bytes(raw):
            return AcquisitionResult(
                mode="attachment_download",
                status="image_available",
                title=os.path.basename(path),
                text_preview=path,
                source_url=url,
            )
        text = _extract_attachment_text(path, ext)
        if len(re.sub(r"\s+", "", text or "")) >= 40:
            return AcquisitionResult(
                mode="attachment_download",
                status="text_available",
                title=os.path.basename(path),
                text_preview=_clip(_clean_document_text(text), 2600),
                reason=f"saved:{path}",
                source_url=url,
            )
        return AcquisitionResult(
            mode="attachment_download",
            status="file_available",
            title=os.path.basename(path),
            text_preview=path,
            reason=f"已保存附件，但未能抽取足够正文。Content-Type: {ctype or 'unknown'}; ext={ext}",
            next_request="如果需要精读，请发送正文、截图，或安装对应解析依赖。",
            source_url=url,
        )
    except Exception as exc:
        return AcquisitionResult(
            mode="attachment_download",
            status="fetch_failed",
            reason=str(exc)[:180],
            next_request="请提供文件路径、正文或重新发送附件。",
            source_url=url,
        )


def download_media_url(url: str, dest_dir: str, timeout: int = 20) -> AcquisitionResult:
    """Download a public media URL into dest_dir.

    This does not bypass login or platform restrictions. It only handles URLs
    that QQ/platform events already expose to the bot.
    """
    if not re.match(r"(?i)^https?://", url or ""):
        return AcquisitionResult(
            mode="media_download",
            status="access_limited",
            reason="不是 HTTP(S) 图片地址。",
            next_request="请提供可下载图片链接、正文或本地图片路径。",
            source_url=url,
        )
    os.makedirs(dest_dir, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 PartnerResearchBot/0.6 (media reader)",
            "Accept": "image/*,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(8_000_000)
        if not raw:
            return AcquisitionResult(
                mode="media_download",
                status="fetch_failed",
                reason="图片下载为空。",
                next_request="请重新发送图片或提供本地路径。",
                source_url=url,
            )
        ext = ".jpg"
        if "png" in ctype:
            ext = ".png"
        elif "webp" in ctype:
            ext = ".webp"
        elif "gif" in ctype:
            ext = ".gif"
        elif is_image_url(url):
            ext = os.path.splitext(url.split("?", 1)[0])[1].lower() or ext
        elif raw.startswith(b"\x89PNG"):
            ext = ".png"
        elif raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
            ext = ".webp"
        elif raw.startswith((b"GIF87a", b"GIF89a")):
            ext = ".gif"
        if "image/" not in ctype and not is_image_url(url) and not _looks_like_image_bytes(raw):
            return AcquisitionResult(
                mode="media_download",
                status="metadata_only",
                reason=f"URL 返回的不是图片 Content-Type: {ctype or 'unknown'}",
                next_request="请提供图片文件或正文。",
                source_url=url,
            )
        name = f"media_{abs(hash(url))}{ext}"
        path = os.path.join(dest_dir, name)
        with open(path, "wb") as f:
            f.write(raw)
        return AcquisitionResult(
            mode="media_download",
            status="image_available",
            title=os.path.basename(path),
            text_preview=path,
            source_url=url,
        )
    except Exception as exc:
        return AcquisitionResult(
            mode="media_download",
            status="fetch_failed",
            reason=str(exc)[:180],
            next_request="请提供可下载图片链接、正文或本地图片路径。",
            source_url=url,
        )


def _strip_html(raw: str) -> tuple[str, str]:
    title = ""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw or "")
    if m:
        title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw or "")
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return title, text


def _domain(url: str) -> str:
    m = re.match(r"(?i)^https?://([^/]+)", url or "")
    return (m.group(1) if m else "").lower()


def _platform_for_url(url: str) -> str:
    domain = _domain(url)
    checks = [
        ("xiaohongshu", ("xiaohongshu.com", "xhslink.com")),
        ("wechat", ("mp.weixin.qq.com",)),
        ("bilibili", ("bilibili.com", "b23.tv")),
        ("zhihu", ("zhihu.com",)),
        ("youtube", ("youtube.com", "youtu.be")),
        ("github", ("github.com",)),
    ]
    for platform, needles in checks:
        if any(needle in domain for needle in needles):
            return platform
    return "web"


def _safe_name(name: str, default: str = "content") -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", name or default).strip("_")
    return text[:80] or default


def _json_request(url: str, timeout: int = 15, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req_headers = {
        "User-Agent": "Mozilla/5.0 PartnerResearchBot/0.7 (public content reader)",
        "Accept": "application/json,text/plain,*/*;q=0.5",
        "Referer": "https://www.bilibili.com/",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(3_000_000).decode("utf-8", errors="ignore")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _bilibili_bvid(url: str) -> str:
    match = re.search(r"/video/(BV[A-Za-z0-9]+)", url or "")
    if match:
        return match.group(1)
    match = re.search(r"\b(BV[A-Za-z0-9]{8,})\b", url or "")
    return match.group(1) if match else ""


def fetch_bilibili_video(url: str, dest_dir: str = "", timeout: int = 20) -> AcquisitionResult:
    """Read public Bilibili video metadata, intro, pages and top comments."""
    bvid = _bilibili_bvid(url)
    if not bvid:
        return AcquisitionResult(
            mode="bilibili_api",
            status="metadata_only",
            reason="未识别到 BV 号。",
            next_request="请提供 B站视频 BV 链接或公开视频 URL。",
            source_url=url,
        )
    try:
        view = _json_request(
            "https://api.bilibili.com/x/web-interface/view?bvid=" + urllib.parse.quote(bvid),
            timeout=timeout,
        )
        data = view.get("data") or {}
        if not data:
            return AcquisitionResult(
                mode="bilibili_api",
                status="fetch_failed",
                reason=str(view.get("message") or "B站 API 未返回视频数据")[:180],
                source_url=url,
            )
        aid = data.get("aid")
        title = str(data.get("title") or bvid)
        desc = str(data.get("desc") or "")
        owner = data.get("owner") or {}
        stat = data.get("stat") or {}
        pages = data.get("pages") or []
        comments: list[str] = []
        if aid:
            try:
                reply = _json_request(
                    f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&next=0",
                    timeout=timeout,
                )
                replies = ((reply.get("data") or {}).get("replies") or [])[:10]
                for item in replies:
                    member = item.get("member") or {}
                    content = item.get("content") or {}
                    message = str(content.get("message") or "").strip()
                    pictures = content.get("pictures") or []
                    if message:
                        comments.append(f"{member.get('uname') or '用户'}: {message}")
                    for pic in pictures[:4]:
                        img = pic.get("img_src") or pic.get("src")
                        if img:
                            comments.append(f"{member.get('uname') or '用户'} image: {img}")
            except Exception:
                pass
        lines = [
            f"Title: {title}",
            f"Owner: {owner.get('name') or ''} uid={owner.get('mid') or ''}",
            f"Stats: view={stat.get('view')} like={stat.get('like')} coin={stat.get('coin')} reply={stat.get('reply')}",
            f"Published: {data.get('pubdate')}",
            f"Description: {desc}",
            "Pages: " + "; ".join(str(p.get("part") or p.get("page") or "") for p in pages[:12]),
        ]
        if comments:
            lines.append("Top comments/public images:")
            lines.extend(comments[:18])
        media_urls = []
        pic = str(data.get("pic") or "")
        if pic:
            media_urls.append(pic)
        face = str(owner.get("face") or "")
        if face:
            media_urls.append(face)
        return AcquisitionResult(
            mode="bilibili_api",
            status="text_available",
            title=title,
            text_preview=_clip("\n".join(lines), 3200),
            source_url=url,
            media_urls=media_urls,
            metadata={"bvid": bvid, "aid": aid, "owner": owner, "stat": stat},
        )
    except Exception as exc:
        return AcquisitionResult(
            mode="bilibili_api",
            status="fetch_failed",
            reason=str(exc)[:180],
            next_request="如果公开视频 API 不可用，请配置登录态浏览器/cookie 工具或转发页面正文。",
            source_url=url,
        )


def download_video_url(url: str, dest_dir: str, timeout: int = 180) -> AcquisitionResult:
    """Download public video with yt-dlp when available and explicitly allowed."""
    if os.getenv("PARTNER_ENABLE_VIDEO_DOWNLOAD", "").lower() not in {"1", "true", "on", "yes"}:
        return AcquisitionResult(
            mode="video_download",
            status="disabled",
            reason="视频下载默认关闭。",
            next_request="设置 PARTNER_ENABLE_VIDEO_DOWNLOAD=1 并安装 yt-dlp；只下载有权访问的公开内容。",
            source_url=url,
        )
    exe = shutil.which("yt-dlp")
    if not exe:
        return AcquisitionResult(
            mode="video_download",
            status="dependency_missing",
            reason="yt-dlp 未安装。",
            next_request="安装 yt-dlp，或只发送公开视频链接和元数据。",
            source_url=url,
        )
    os.makedirs(dest_dir, exist_ok=True)
    out_tpl = os.path.join(dest_dir, "%(title).80s_%(id)s.%(ext)s")
    cookie_args: list[str] = []
    cookie_file = os.getenv("PARTNER_YTDLP_COOKIES", "").strip()
    if cookie_file:
        cookie_args = ["--cookies", cookie_file]
    cmd = [
        exe,
        "--no-playlist",
        "--restrict-filenames",
        "--write-info-json",
        "-f",
        os.getenv("PARTNER_YTDLP_FORMAT", "bv*+ba/best"),
        "-o",
        out_tpl,
        *cookie_args,
        url,
    ]
    try:
        subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return AcquisitionResult(mode="video_download", status="tool_timeout", reason="yt-dlp 超时。", source_url=url)
    except Exception as exc:
        return AcquisitionResult(mode="video_download", status="tool_failed", reason=str(exc)[:180], source_url=url)
    files: list[str] = []
    for cur, _, names in os.walk(dest_dir):
        for name in names:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTS + (".json",):
                files.append(os.path.join(cur, name))
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    video_files = [p for p in files if os.path.splitext(p)[1].lower() in VIDEO_EXTS]
    if not video_files:
        return AcquisitionResult(
            mode="video_download",
            status="fetch_failed",
            reason="yt-dlp 未生成视频文件。",
            next_request="检查 URL、cookie、平台权限或只发送公开链接。",
            source_url=url,
            files=files[:4],
        )
    return AcquisitionResult(
        mode="video_download",
        status="file_available",
        title=os.path.basename(video_files[0]),
        text_preview=video_files[0],
        source_url=url,
        files=video_files[:3] + [p for p in files if p.endswith(".json")][:2],
    )


def extract_video_keyframes(path: str, dest_dir: str, *, count: int = 6) -> AcquisitionResult:
    if not path or not os.path.exists(path):
        return AcquisitionResult(mode="video_keyframes", status="missing_file", reason="视频文件不存在。", source_url=path)
    exe = shutil.which("ffmpeg")
    if not exe:
        return AcquisitionResult(
            mode="video_keyframes",
            status="dependency_missing",
            reason="ffmpeg 未安装。",
            next_request="安装 ffmpeg 后可抽取视频关键帧供视觉模型读取。",
            source_url=path,
        )
    os.makedirs(dest_dir, exist_ok=True)
    pattern = os.path.join(dest_dir, _safe_name(os.path.splitext(os.path.basename(path))[0]) + "_frame_%02d.jpg")
    cmd = [
        exe,
        "-y",
        "-i",
        path,
        "-vf",
        f"fps=1/{max(1, int(60 / max(1, count)))}",
        "-frames:v",
        str(max(1, count)),
        pattern,
    ]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
    files = sorted(os.path.join(dest_dir, name) for name in os.listdir(dest_dir) if name.endswith(".jpg"))
    if not files:
        return AcquisitionResult(mode="video_keyframes", status="tool_failed", reason=(proc.stderr or "")[-300:], source_url=path)
    return AcquisitionResult(
        mode="video_keyframes",
        status="image_available",
        title=os.path.basename(path),
        text_preview="\n".join(files[:count]),
        source_url=path,
        files=files[:count],
    )


def _load_command_tools() -> list[dict[str, Any]]:
    """Load optional content acquisition command adapters.

    This is deliberately not a Hermes skill.  It is a small Partner-side tool
    registry.  Example:

    PARTNER_CONTENT_TOOL_COMMANDS='[
      {"name":"agent-reach-xhs","platforms":["xiaohongshu"],"cmd":"agent-reach read {url}"},
      {"name":"wechat-mcp","platforms":["wechat"],"cmd":"weixin-mcp-server read {url}"}
    ]'

    The command is executed without a shell; placeholders are substituted after
    shlex splitting.  Unknown tools simply degrade to the next reader.
    """
    raw = os.getenv("PARTNER_CONTENT_TOOL_COMMANDS", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("tools") or []
    if not isinstance(data, list):
        return []
    tools: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        cmd = item.get("cmd") or item.get("command")
        if not cmd:
            continue
        platforms = item.get("platforms") or item.get("platform") or ["web"]
        if isinstance(platforms, str):
            platforms = [platforms]
        tools.append({
            "name": str(item.get("name") or item.get("id") or "external_content_tool"),
            "platforms": [str(x).lower() for x in platforms],
            "cmd": str(cmd),
            "timeout": int(item.get("timeout") or os.getenv("PARTNER_CONTENT_TOOL_TIMEOUT", "45")),
        })
    return tools


def _run_command_tool(tool: dict[str, Any], url: str, platform: str) -> AcquisitionResult:
    cmd_template = str(tool.get("cmd") or "")
    try:
        parts = shlex.split(cmd_template)
    except Exception as exc:
        return AcquisitionResult(
            mode="external_tool",
            status="tool_config_invalid",
            reason=f"命令解析失败：{str(exc)[:120]}",
            next_request="检查 PARTNER_CONTENT_TOOL_COMMANDS 配置。",
            source_url=url,
            tool_name=str(tool.get("name") or ""),
        )
    if not parts:
        return AcquisitionResult(mode="external_tool", status="tool_config_invalid", source_url=url)
    exe = parts[0]
    if shutil.which(exe) is None:
        return AcquisitionResult(
            mode="external_tool",
            status="tool_missing",
            reason=f"命令不存在：{exe}",
            next_request="安装对应 CLI/MCP wrapper，或让 Partner 使用 public-web/正文转发降级路径。",
            source_url=url,
            tool_name=str(tool.get("name") or ""),
        )
    args = [
        p.replace("{url}", url).replace("{platform}", platform)
        for p in parts
    ]
    if all("{url}" not in p for p in parts) and url not in args:
        args.append(url)
    try:
        proc = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=int(tool.get("timeout") or 45),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return AcquisitionResult(
            mode="external_tool",
            status="tool_timeout",
            reason="外部内容工具超时。",
            next_request="先记录限制并使用公开替代源；必要时用户可转发正文/截图。",
            source_url=url,
            tool_name=str(tool.get("name") or ""),
        )
    except Exception as exc:
        return AcquisitionResult(
            mode="external_tool",
            status="tool_failed",
            reason=str(exc)[:180],
            next_request="先降级到 public-web/登录态浏览器/用户正文。",
            source_url=url,
            tool_name=str(tool.get("name") or ""),
        )
    output = (proc.stdout or "").strip()
    if not output:
        return AcquisitionResult(
            mode="external_tool",
            status="tool_failed",
            reason=(proc.stderr or f"exit={proc.returncode}")[:180],
            next_request="外部工具无正文输出；先降级到 public-web/用户正文。",
            source_url=url,
            tool_name=str(tool.get("name") or ""),
        )
    title = ""
    text = output
    try:
        obj = json.loads(output)
        if isinstance(obj, dict):
            title = str(obj.get("title") or obj.get("name") or "")
            text = str(
                obj.get("markdown")
                or obj.get("content")
                or obj.get("text")
                or obj.get("summary")
                or json.dumps(obj, ensure_ascii=False)
            )
    except Exception:
        pass
    if len(re.sub(r"\s+", "", text)) < 80:
        return AcquisitionResult(
            mode="external_tool",
            status="metadata_only",
            title=title,
            text_preview=_clip(text, 500),
            reason="外部工具只返回少量元数据。",
            next_request="请用户转发正文/截图，或换用登录态/平台专用工具。",
            source_url=url,
            tool_name=str(tool.get("name") or ""),
        )
    return AcquisitionResult(
        mode="external_tool",
        status="text_available",
        title=title,
        text_preview=_clip(text, 2200),
        source_url=url,
        tool_name=str(tool.get("name") or ""),
    )


def fetch_with_external_tools(url: str, platform: str = "", timeout: int = 45) -> AcquisitionResult:
    platform = (platform or _platform_for_url(url)).lower()
    last: AcquisitionResult | None = None
    for tool in _load_command_tools():
        platforms = set(tool.get("platforms") or [])
        if "all" not in platforms and platform not in platforms and "web" not in platforms:
            continue
        tool = {**tool, "timeout": tool.get("timeout") or timeout}
        result = _run_command_tool(tool, url, platform)
        last = result
        if result.status == "text_available":
            return result
    return last or AcquisitionResult(
        mode="external_tool",
        status="not_configured",
        reason="未配置平台专用外部内容工具。",
        next_request="可设置 PARTNER_CONTENT_TOOL_COMMANDS 接入 Agent-Reach/MCP/yt-dlp 等 CLI。",
        source_url=url,
    )


def fetch_with_jina_reader(url: str, timeout: int = 20) -> AcquisitionResult:
    if os.getenv("PARTNER_ENABLE_JINA_READER", "1").lower() in {"0", "false", "off", "no"}:
        return AcquisitionResult(
            mode="jina_reader",
            status="disabled",
            source_url=url,
        )
    reader_url = "https://r.jina.ai/" + url
    headers = {
        "User-Agent": "PartnerResearchBot/0.6",
        "Accept": "text/plain, text/markdown, */*;q=0.5",
    }
    api_key = os.getenv("JINA_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(reader_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1_500_000).decode("utf-8", errors="ignore")
        if len(re.sub(r"\s+", "", raw)) < 120:
            return AcquisitionResult(
                mode="jina_reader",
                status="metadata_only",
                text_preview=_clip(raw, 500),
                reason="Jina Reader 未返回足够正文。",
                next_request="换用平台专用工具/登录态浏览器，或请用户转发正文/截图。",
                source_url=url,
            )
        title = ""
        m = re.search(r"(?im)^Title:\s*(.+)$", raw)
        if m:
            title = m.group(1).strip()
        return AcquisitionResult(
            mode="jina_reader",
            status="text_available",
            title=title,
            text_preview=_clip(raw, 2200),
            source_url=url,
        )
    except Exception as exc:
        return AcquisitionResult(
            mode="jina_reader",
            status="fetch_failed",
            reason=str(exc)[:180],
            next_request="换用平台专用工具/登录态浏览器，或请用户转发正文/截图。",
            source_url=url,
        )


def acquire_url(url: str, timeout: int = 12) -> AcquisitionResult:
    """Best-effort URL acquisition with bounded, explainable fallbacks."""
    platform = _platform_for_url(url)
    external = fetch_with_external_tools(url, platform=platform)
    if external.status == "text_available":
        return external
    if platform == "bilibili":
        bili = fetch_bilibili_video(url, timeout=max(timeout, 20))
        if bili.status == "text_available":
            return bili

    # Jina is useful for public articles, WeChat pages, PDFs, and many normal
    # web pages. It does not bypass login and may still fail on app-only pages.
    if platform in {"web", "wechat", "zhihu", "github"} or os.getenv("PARTNER_TRY_JINA_FOR_ALL", "").lower() in {"1", "true", "on", "yes"}:
        jina = fetch_with_jina_reader(url, timeout=max(timeout, 20))
        if jina.status == "text_available":
            return jina

    public = fetch_public_url(url, timeout=timeout)
    if public.status == "text_available":
        return public
    if external.status not in {"not_configured", "tool_missing"}:
        public.reason = f"{public.reason}; 外部工具状态: {external.status} {external.reason}".strip("; ")
        public.tool_name = external.tool_name
    return public


def fetch_public_url(url: str, timeout: int = 10) -> AcquisitionResult:
    domain = _domain(url)
    if any(d in domain for d in BLOCKED_DOMAINS):
        if _login_browser_configured():
            return fetch_with_login_browser(url, timeout=timeout)
        return AcquisitionResult(
            mode="public_web",
            status="needs_user_body",
            reason="平台通常需要登录、App 环境或动态渲染，Partner 默认不绕过限制。",
            next_request="请用户转发正文、截图、视频摘要或可公开访问的原文链接。",
            source_url=url,
        )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 PartnerResearchBot/0.6 "
                "(public content reader; no login bypass)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            raw_bytes = resp.read(1_200_000)
        if "pdf" in ctype.lower():
            return AcquisitionResult(
                mode="public_web",
                status="pdf_detected",
                reason="检测到 PDF；当前轻量工具不解析 PDF 正文。",
                next_request="后续可接入 PDF parser，或请用户转发摘要/关键页截图。",
                source_url=url,
            )
        raw = raw_bytes.decode("utf-8", errors="ignore")
        title, text = _strip_html(raw)
        media_urls = sorted(set(re.findall(r'https?://[^"\'<>\s]+\.(?:jpg|jpeg|png|webp|gif|mp4|webm)(?:\?[^"\'<>\s]*)?', raw, flags=re.I)))[:20]
        if len(text) < 120:
            return AcquisitionResult(
                mode="public_web",
                status="metadata_only",
                title=title,
                text_preview=_clip(text, 500),
                reason="公开页面正文过短，可能需要登录、动态渲染或反爬。",
                next_request="请用户转发正文/截图，或配置登录态浏览器读取。",
                source_url=url,
                media_urls=media_urls,
            )
        return AcquisitionResult(
            mode="public_web",
            status="text_available",
            title=title,
            text_preview=_clip(text),
            source_url=url,
            media_urls=media_urls,
        )
    except urllib.error.HTTPError as exc:
        return AcquisitionResult(
            mode="public_web",
            status="access_limited",
            reason=f"HTTP {exc.code}",
            next_request="请用户转发正文/截图，或配置登录态浏览器读取。",
            source_url=url,
        )
    except Exception as exc:
        return AcquisitionResult(
            mode="public_web",
            status="fetch_failed",
            reason=str(exc)[:180],
            next_request="先记录线索并从公开替代源推进；如需精读，请用户转发正文/截图。",
            source_url=url,
        )


def fetch_with_login_browser(url: str, timeout: int = 20) -> AcquisitionResult:
    """Optional login-state browser reader.

    This uses a user-provided browser profile only.  It does not bypass login,
    captchas, paywalls, or anti-bot controls.  If Playwright/profile is not
    available, it returns a visible limitation instead of pretending success.
    """
    profile = os.getenv("PARTNER_BROWSER_PROFILE", "").strip()
    if not profile:
        return AcquisitionResult(
            mode="login_browser",
            status="not_configured",
            reason="PARTNER_BROWSER_PROFILE 未配置。",
            next_request="请用户转发正文/截图，或配置登录态浏览器 profile。",
            source_url=url,
        )
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return AcquisitionResult(
            mode="login_browser",
            status="dependency_missing",
            reason=f"Playwright 不可用：{str(exc)[:120]}",
            next_request="安装 playwright 并执行 playwright install，或请用户转发正文/截图。",
            source_url=url,
        )
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile,
                headless=True,
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            title = page.title() or ""
            text = page.locator("body").inner_text(timeout=5000)
            media_urls = page.evaluate(
                """() => Array.from(new Set([
                    ...Array.from(document.images || []).map(x => x.currentSrc || x.src),
                    ...Array.from(document.querySelectorAll('video, source')).map(x => x.currentSrc || x.src)
                ].filter(Boolean)))"""
            )
            screenshot_path = ""
            if os.getenv("PARTNER_BROWSER_SAVE_SCREENSHOT", "1").lower() not in {"0", "false", "off", "no"}:
                out_dir = os.getenv("PARTNER_BROWSER_CAPTURE_DIR", "").strip() or tempfile.gettempdir()
                os.makedirs(out_dir, exist_ok=True)
                screenshot_path = os.path.join(out_dir, f"partner_page_{abs(hash(url))}.png")
                try:
                    page.screenshot(path=screenshot_path, full_page=True)
                except Exception:
                    screenshot_path = ""
            context.close()
        if len(text.strip()) < 120:
            return AcquisitionResult(
                mode="login_browser",
                status="metadata_only",
                title=title,
                text_preview=_clip(text, 500),
                reason="登录态浏览器只读到很少正文，可能仍需 App、验证码或动态权限。",
                next_request="请用户转发正文/截图或视频摘要。",
                source_url=url,
                files=[screenshot_path] if screenshot_path else [],
                media_urls=[str(x) for x in (media_urls or []) if str(x).strip()][:40],
            )
        return AcquisitionResult(
            mode="login_browser",
            status="text_available",
            title=title,
            text_preview=_clip(text),
            source_url=url,
            files=[screenshot_path] if screenshot_path else [],
            media_urls=[str(x) for x in (media_urls or []) if str(x).strip()][:40],
        )
    except Exception as exc:
        return AcquisitionResult(
            mode="login_browser",
            status="fetch_failed",
            reason=str(exc)[:180],
            next_request="请用户转发正文/截图；Partner 会先用公开替代源继续推进。",
            source_url=url,
        )


def ocr_image_path(path: str) -> AcquisitionResult:
    if not path or not os.path.exists(path):
        return AcquisitionResult(
            mode="ocr",
            status="missing_file",
            reason="图片路径不存在。",
            next_request="请用户重新发送截图或提供本地图片路径。",
        )
    try:
        from PIL import Image
        import pytesseract

        text = pytesseract.image_to_string(Image.open(path), lang=os.getenv("PARTNER_OCR_LANG", "chi_sim+eng"))
        return AcquisitionResult(
            mode="ocr",
            status="text_available" if text.strip() else "empty",
            text_preview=_clip(text),
            source_url=path,
        )
    except Exception as exc:
        return AcquisitionResult(
            mode="ocr",
            status="dependency_missing",
            reason=str(exc)[:180],
            next_request="安装 pillow/pytesseract/tesseract-ocr，或请用户转发文字正文。",
            source_url=path,
        )


def split_image_for_vision(
    path: str,
    dest_dir: str,
    *,
    max_height: int = 1200,
    overlap: int = 120,
    max_segments: int = 8,
) -> list[str]:
    """Split tall social screenshots into readable chunks for vision models.

    Long mobile screenshots are often misread when passed as one image.  This
    helper keeps ordinary images unchanged and creates overlapping vertical
    chunks for tall screenshots.  It is a preprocessing utility, not OCR.
    """
    if not path or not os.path.exists(path):
        return []
    try:
        from PIL import Image

        image = Image.open(path)
        width, height = image.size
        if height <= max_height or height <= width * 2.2:
            return [path]
        os.makedirs(dest_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(path))[0]
        out: list[str] = []
        top = 0
        idx = 1
        step = max(240, max_height - max(0, overlap))
        while top < height and len(out) < max_segments:
            bottom = min(height, top + max_height)
            crop = image.crop((0, top, width, bottom))
            chunk_path = os.path.join(dest_dir, f"{stem}_chunk_{idx:02d}.jpg")
            crop.convert("RGB").save(chunk_path, quality=92)
            out.append(chunk_path)
            if bottom >= height:
                break
            top += step
            idx += 1
        return out or [path]
    except Exception:
        return [path]


def describe_tool_status(workspace: str = "") -> dict[str, Any]:
    """Return configured acquisition capabilities for user-visible diagnostics."""
    browser_enabled = os.getenv("PARTNER_ENABLE_BROWSER_LOGIN", "").lower() in {"1", "true", "on", "yes"}
    browser_profile = os.getenv("PARTNER_BROWSER_PROFILE", "")
    ocr_enabled = os.getenv("PARTNER_ENABLE_OCR", "").lower() in {"1", "true", "on", "yes"}
    video_enabled = os.getenv("PARTNER_ENABLE_VIDEO_TRANSCRIPT", "").lower() in {"1", "true", "on", "yes"}
    command_tools = _load_command_tools()
    return {
        "public_web": "enabled",
        "jina_reader": "enabled" if os.getenv("PARTNER_ENABLE_JINA_READER", "1").lower() not in {"0", "false", "off", "no"} else "disabled",
        "user_forwarded_body": "enabled",
        "login_browser": "enabled" if browser_enabled and browser_profile else "not_configured",
        "ocr": "enabled" if ocr_enabled else "not_configured",
        "video_transcript": "enabled" if video_enabled else "not_configured",
        "external_commands": [
            {
                "name": t.get("name", ""),
                "platforms": t.get("platforms", []),
                "command": shlex.split(str(t.get("cmd", "")))[0] if str(t.get("cmd", "")).strip() else "",
                "available": bool(shutil.which(shlex.split(str(t.get("cmd", "")))[0])) if str(t.get("cmd", "")).strip() else False,
            }
            for t in command_tools
        ],
    }


def write_tool_status(workspace: str) -> None:
    if not workspace:
        return
    user_dir = os.path.join(workspace, "state", "user")
    os.makedirs(user_dir, exist_ok=True)
    status = describe_tool_status(workspace)
    lines = [
        "# 内容获取工具链",
        "",
        "- 公开网页读取：enabled",
        f"- Jina Reader 网页转 Markdown：{status['jina_reader']}",
        "- 用户转发正文/截图摘要：enabled",
        f"- 登录态浏览器：{status['login_browser']}",
        f"- OCR：{status['ocr']}",
        f"- 视频转写：{status['video_transcript']}",
        f"- 外部 CLI/MCP 工具：{len(status.get('external_commands') or [])} configured",
        "",
        "外部工具配置：通过 PARTNER_CONTENT_TOOL_COMMANDS 注册 Agent-Reach、weixin-mcp、zhihu-mcp、yt-dlp 或自定义 MCP wrapper；这些属于 Partner 工具层，不写入 Hermes skill。",
        "",
        "原则：不绕过登录、不破解反爬、不把链接卡片当正文。读不到正文时会记录限制，并请求用户转发正文/截图，或切换到公开替代源继续推进。",
        "",
        "命令行：python -m partner.content_tools acquire <url> --dest <dir> --json",
    ]
    with open(os.path.join(user_dir, "content_tools_status.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def dump_result(result: AcquisitionResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False)


def acquire_and_materialize(url: str, dest_dir: str, *, timeout: int = 20,
                            download_media: bool = True,
                            download_video: bool = False,
                            keyframes: bool = False) -> AcquisitionResult:
    """Acquire URL content and optionally save linked media/video artifacts."""
    os.makedirs(dest_dir, exist_ok=True)
    result = acquire_url(url, timeout=timeout)
    files = list(result.files or [])
    media_urls = list(result.media_urls or [])
    if download_media:
        media_dir = os.path.join(dest_dir, "media")
        for media_url in media_urls[:20]:
            if not is_image_url(media_url):
                continue
            item = download_media_url(media_url, media_dir, timeout=timeout)
            if item.status == "image_available" and item.text_preview:
                files.append(item.text_preview)
    if download_video:
        video_dir = os.path.join(dest_dir, "video")
        video = download_video_url(url, video_dir)
        if video.files:
            files.extend(video.files)
        if keyframes:
            for path in video.files or []:
                if os.path.splitext(path)[1].lower() in VIDEO_EXTS:
                    frames = extract_video_keyframes(path, os.path.join(dest_dir, "keyframes"))
                    files.extend(frames.files or [])
                    break
    text_path = os.path.join(dest_dir, "content_acquisition.md")
    lines = [
        f"# Content Acquisition",
        "",
        f"- URL: {url}",
        f"- Mode: {result.mode}",
        f"- Status: {result.status}",
        f"- Title: {result.title}",
        f"- Tool: {result.tool_name}",
        f"- Reason: {result.reason}",
        f"- Next request: {result.next_request}",
        "",
        "## Text",
        result.text_preview or "",
        "",
        "## Media URLs",
        "\n".join(f"- {x}" for x in media_urls[:80]) or "EMPTY",
        "",
        "## Files",
        "\n".join(f"- {x}" for x in files[:80]) or "EMPTY",
    ]
    with open(text_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    files.append(text_path)
    result.files = [x for x in files if x]
    result.media_urls = media_urls
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Partner external content acquisition tool")
    sub = parser.add_subparsers(dest="cmd")
    acquire = sub.add_parser("acquire", help="read a public/platform URL and save artifacts")
    acquire.add_argument("url")
    acquire.add_argument("--dest", default=os.path.join(os.getcwd(), "content_acquisition"))
    acquire.add_argument("--timeout", type=int, default=20)
    acquire.add_argument("--no-media", action="store_true")
    acquire.add_argument("--download-video", action="store_true")
    acquire.add_argument("--keyframes", action="store_true")
    acquire.add_argument("--json", action="store_true")
    status = sub.add_parser("status", help="show configured content tools")
    status.add_argument("--workspace", default="")
    args = parser.parse_args(argv)
    if args.cmd == "acquire":
        result = acquire_and_materialize(
            args.url,
            args.dest,
            timeout=args.timeout,
            download_media=not args.no_media,
            download_video=args.download_video,
            keyframes=args.keyframes,
        )
        if args.json:
            print(dump_result(result))
        else:
            print(f"{result.status}: {result.title or result.source_url}")
            if result.text_preview:
                print(result.text_preview)
            if result.files:
                print("FILES:")
                for path in result.files:
                    print(path)
        return 0 if result.status in {"text_available", "image_available", "file_available", "metadata_only"} else 2
    if args.cmd == "status":
        print(json.dumps(describe_tool_status(args.workspace), ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
