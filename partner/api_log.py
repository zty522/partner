"""API 调用日志：记录 deepseek / qwen 等外部 API 调用，便于核对调用与成功率。

写入位置：{workspace_root}/state/logs/api_calls.jsonl（每行一个 JSON 对象）。
workspace 根通过 ~/.partner_workspace 指针文件解析（兼容 Windows/WSL 路径）。
日志失败只降级为 debug 日志，绝不抛异常影响主流程。
"""
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


def workspace_root_from_pointer() -> str:
    """~/.partner_workspace 指针 → workspace 根（支持 Windows/WSL 双向路径）。"""
    try:
        pointer = os.path.expanduser("~/.partner_workspace")
        if os.path.exists(pointer):
            raw = open(pointer, encoding="utf-8", errors="replace").read().strip()
            norm = raw.replace("\\", "/")
            if norm.startswith("/mnt/"):
                return norm
            if len(norm) >= 2 and norm[1] == ":":
                return "/mnt/" + norm[0].lower() + norm[2:]
    except Exception:
        pass
    return ""


def append_api_call(
    api: str,
    *,
    model: str = "",
    base_url: str = "",
    purpose: str = "chat",
    status: str = "ok",
    elapsed_ms: int = 0,
    prompt_chars: int = 0,
    response_chars: int = 0,
    error: str = "",
    workspace_root: str = "",
    instance: str = "",
) -> str:
    """追加一条 API 调用记录。返回日志文件路径；失败返回空串（不抛异常）。"""
    try:
        root = workspace_root or workspace_root_from_pointer()
        if not root:
            return ""
        log_dir = os.path.join(root, "state", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "api_calls.jsonl")
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "api": api,
            "model": model,
            "base_url": base_url,
            "purpose": purpose,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "prompt_chars": prompt_chars,
            "response_chars": response_chars,
            "error": error or "",
            "instance": instance,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return log_path
    except Exception as exc:
        logger.debug("append_api_call failed: %s", exc)
        return ""
