"""Generic file intake, inspection, and transfer helpers for Partner."""

from __future__ import annotations

import binascii
import json
import mimetypes
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .workspace.workspace_layout import incoming_dir, uploads_dir


KNOWN_EXTENSIONS = {
    ".bmp",
    ".csv",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".pdf",
    ".png",
    ".pptx",
    ".tsv",
    ".txt",
    ".webp",
    ".xls",
    ".xlsx",
}

MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"PK\x03\x04", "zip_or_office"),
    (b"RIFF", "riff_audio_or_video"),
    (b"OggS", "ogg"),
    (b"ID3", "mp3"),
    (b"\x1aE\xdf\xa3", "webm_or_matroska"),
    (b"#!SILK", "silk_audio"),
)

_NTFLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def safe_filename(name: str, fallback: str = "file") -> str:
    text = str(name or "").strip() or fallback
    return "".join(ch if ch not in '<>:"/\\|?*\r\n\t' else "_" for ch in text).strip() or fallback


def instance_upload_dir(workspace: str, instance_id: str = "") -> str:
    root = Path(workspace)
    if root.name == "instances" and instance_id:
        return uploads_dir(str(root / instance_id))
    elif instance_id and (root / "instances").exists():
        return uploads_dir(str(root / "instances" / instance_id))
    else:
        return uploads_dir(str(root))


def instance_incoming_dir(workspace: str) -> str:
    return incoming_dir(workspace)


def sniff_magic(data: bytes) -> str:
    for prefix, label in MAGIC_SIGNATURES:
        if data.startswith(prefix):
            return label
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4_or_quicktime"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav_audio"
    return "unknown"


def inspect_file(path: str, output_dir: str | None = None, max_bytes: int = 64) -> dict[str, Any]:
    file_path = Path(path)
    with file_path.open("rb") as f:
        head = f.read(max_bytes)
    ext = file_path.suffix.lower()
    mime, encoding = mimetypes.guess_type(str(file_path))
    payload: dict[str, Any] = {
        "path": str(file_path),
        "name": file_path.name,
        "size": file_path.stat().st_size if file_path.exists() else 0,
        "extension": ext,
        "mime_guess": mime or "",
        "encoding_guess": encoding or "",
        "magic": sniff_magic(head),
        "first_64_bytes_hex": binascii.hexlify(head).decode("ascii"),
        "known_extension": ext in KNOWN_EXTENSIONS,
        "inspected_at": datetime.now().isoformat(),
    }
    out_dir = Path(output_dir) if output_dir else file_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    inspection_path = out_dir / f"{file_path.stem or 'file'}_inspection.json"
    with inspection_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    payload["inspection_path"] = str(inspection_path)
    return payload


def copy_into_workspace(src_path: str, dest_dir: str, *, prefix_timestamp: bool = True) -> dict[str, Any]:
    src = Path(src_path)
    dest_root = Path(dest_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    name = safe_filename(src.name)
    if prefix_timestamp:
        name = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{name}"
    dest = dest_root / name
    shutil.copy2(str(src), str(dest))
    inspection = inspect_file(str(dest), output_dir=str(dest_root))
    return {
        "name": src.name,
        "server_path": str(dest),
        "size": dest.stat().st_size,
        "inspection": inspection,
    }


def scp_upload(local_path: str, remote: str, *, port: int | str = 22, key_path: str = "", timeout: int = 120) -> tuple[bool, str]:
    cmd = ["scp", "-P", str(port), "-o", "StrictHostKeyChecking=accept-new"]
    if key_path:
        cmd.extend(["-i", key_path])
    cmd.extend([local_path, remote])
    return _run_transfer(cmd, timeout)


def scp_download(remote: str, local_path: str, *, port: int | str = 22, key_path: str = "", timeout: int = 120) -> tuple[bool, str]:
    cmd = ["scp", "-P", str(port), "-o", "StrictHostKeyChecking=accept-new"]
    if key_path:
        cmd.extend(["-i", key_path])
    cmd.extend([remote, local_path])
    return _run_transfer(cmd, timeout)


def _run_transfer(cmd: list[str], timeout: int) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NTFLAGS,
        )
    except Exception as exc:
        return False, str(exc)
    output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
    return result.returncode == 0, output or ("ok" if result.returncode == 0 else "failed")
