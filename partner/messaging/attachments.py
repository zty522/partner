"""Materialise user attachments (links, images, documents) into usable
text for the message thread and downstream learning.

Supports:
- http(s) links   -> kept as URL sources for source_retrieve
- images (jpg/png/webp/gif) -> downloaded to the work dir for vision
  transcription
- txt/md/csv      -> read as text directly
- pdf             -> extracted text (pdf skill fallback: pypdf if present)
- docx            -> extracted text (python-docx if present)
Every materialisation is bounded and failure-soft: one bad attachment
never kills the message.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
DOC_SUFFIXES = {".pdf", ".docx", ".doc"}


def _sha256_url(url: str) -> str:
    return hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:16]


def _download(url: str, target: Path, timeout: float = 30.0) -> bool:
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 partner"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if not data:
            return False
        target.write_bytes(data)
        return target.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False


def _extract_pdf(path: Path, limit: int = 8000) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            return ""
    try:
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages[:10]:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text.strip())
        return "\n".join(parts)[:limit]
    except Exception:  # noqa: BLE001
        return ""


def _extract_docx(path: Path, limit: int = 8000) -> str:
    try:
        import docx  # python-docx
    except ImportError:
        return ""
    try:
        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs if p.text and p.text.strip()]
        return "\n".join(parts)[:limit]
    except Exception:  # noqa: BLE001
        return ""


def materialise_attachments(
    root: str | os.PathLike,
    attachments: list[dict],
    *,
    work_dir: str | os.PathLike,
) -> list[dict]:
    """Return one entry per attachment with a usable ``material``:
    text, image path, or url."""
    root = Path(root)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("type") or "attachment")
        name = str(item.get("name") or item.get("filename") or "attachment")
        url = str(item.get("url") or item.get("file_url") or item.get("download_url") or "")
        local = str(item.get("path") or "")
        entry = {"kind": kind, "name": name, "url": url, "path": local}
        if local and Path(local).is_file():
            entry["download_state"] = "present"
            entry["local_path"] = local
        elif url:
            suffix = Path(url.split("?")[0]).suffix.lower() or Path(name).suffix.lower()
            target = work / f"{_sha256_url(url)}{suffix if suffix in TEXT_SUFFIXES | IMAGE_SUFFIXES | DOC_SUFFIXES else ''}"
            if _download(url, target):
                entry["download_state"] = "present"
                entry["local_path"] = str(target)
                local = str(target)
            else:
                entry["download_state"] = "failed"
                out.append(entry)
                continue
        else:
            entry["download_state"] = "missing"
            out.append(entry)
            continue
        if not local:
            out.append(entry)
            continue
        path = Path(local)
        suffix = path.suffix.lower()
        if suffix in TEXT_SUFFIXES:
            try:
                entry["material"] = "text"
                entry["text"] = path.read_text(encoding="utf-8", errors="replace")[:8000]
            except OSError:
                entry["material"] = "unreadable"
        elif suffix in IMAGE_SUFFIXES:
            entry["material"] = "image"
        elif suffix == ".pdf":
            entry["material"] = "text"
            entry["text"] = _extract_pdf(path)
        elif suffix == ".docx":
            entry["material"] = "text"
            entry["text"] = _extract_docx(path)
        else:
            entry["material"] = "unknown"
        out.append(entry)
    return out


__all__ = ["materialise_attachments"]
