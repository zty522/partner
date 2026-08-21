"""Partner v2 — Browser automation module.

浏览器操作通过**独立子进程**执行（``browser_worker.py``），避免在 Partner 主
进程（systemd 服务、多线程、长驻事件循环）里直接启动 chromium 时发生的
SIGTRAP 崩溃。独立子进程环境干净，Playwright Async API 稳定运行。

会话通过 ``launch_persistent_context`` 持久化到实例工作目录下的
``browser_profile``，登录状态 / cookie 在多次操作之间保留。

9 个原子 handler: open, click, type, scroll, extract, wait, video, screenshot, execute.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_worker.py")
_PYTHON = sys.executable or "python3"


def _profile_dir(ctx: Any) -> str:
    """Persistent profile directory for this instance (keeps login state).

    放在 /tmp（Linux tmpfs）而非实例工作目录（NTFS 挂载），避免 chromium
    在 NTFS 上初始化 profile 时的潜在问题。
    """
    ws = getattr(ctx, "workspace", "") or ""
    instance_id = os.path.basename(ws.rstrip("/")) or "default"
    import tempfile
    return os.path.join(tempfile.gettempdir(), f"partner_browser_profile_{instance_id}")


def _run_worker(ctx: Any, action: str, params: JsonDict) -> dict:
    """Run a single browser operation in an isolated process.

    通过 ``systemd-run --user`` 启动 worker，让 chromium 运行在 systemd 直接
    fork 出的干净进程里，而不是 Partner 主进程（systemd 服务 + 多线程 + 长驻
    事件循环）fork 出的子进程里 —— 后者会继承某种状态导致 chromium SIGTRAP。
    """
    try:
        profile = _profile_dir(ctx)
        cmd = [_PYTHON, _WORKER, action, json.dumps(params, ensure_ascii=False), profile]
        proc = subprocess.run(
            ["systemd-run", "--user", "--wait", "--collect", "--pipe"] + cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
        out = proc.stdout.strip()
        # systemd-run 会输出 "Running as unit ..." 等噪音，提取真正的 JSON 行
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        if proc.returncode != 0:
            return {"status": "error", "error": f"worker exit {proc.returncode}: {(proc.stderr or out)[:300]}"}
        return {"status": "error", "error": "worker produced no JSON output"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "browser worker timed out after 180s"}
    except Exception as exc:
        logger.exception("browser worker failed for %s", action)
        return {"status": "error", "error": str(exc)}


# ── Handlers ──────────────────────────────────────────────────────────────────

def atomic_browser_open(ctx: Any, params: JsonDict) -> dict:
    """Open a URL in the browser (headless)."""
    url = params.get("url", "")
    if not url:
        return {"status": "error", "error": "Missing required param: url"}
    result = _run_worker(ctx, "open", {"url": url})
    if result.get("status") == "ok":
        result.setdefault("ok", True)
    return result


def atomic_browser_click(ctx: Any, params: JsonDict) -> dict:
    """Click an element on the page."""
    selector = params.get("selector", "")
    if not selector:
        return {"status": "error", "error": "Missing required param: selector"}
    return _run_worker(ctx, "click", {"selector": selector, "wait_ms": params.get("wait_ms", 0)})


def atomic_browser_type(ctx: Any, params: JsonDict) -> dict:
    """Type text into an input element."""
    selector = params.get("selector", "")
    text = params.get("text", "")
    if not selector:
        return {"status": "error", "error": "Missing required param: selector"}
    if text is None:
        text = ""
    return _run_worker(ctx, "type", {"selector": selector, "text": text, "clear_first": params.get("clear_first", True)})


def atomic_browser_scroll(ctx: Any, params: JsonDict) -> dict:
    """Scroll the page."""
    return _run_worker(ctx, "scroll", {"direction": params.get("direction", "down"), "amount": params.get("amount", 300)})


def atomic_browser_extract(ctx: Any, params: JsonDict) -> dict:
    """Extract content from the page."""
    return _run_worker(ctx, "extract", {
        "selector": params.get("selector", "body"),
        "attribute": params.get("attribute"),
        "format": params.get("format", "text"),
    })


def atomic_browser_wait(ctx: Any, params: JsonDict) -> dict:
    """Wait for an element to reach a given state."""
    selector = params.get("selector", "")
    if not selector:
        return {"status": "error", "error": "Missing required param: selector"}
    return _run_worker(ctx, "wait", {
        "selector": selector,
        "timeout": params.get("timeout", 10_000),
        "state": params.get("state", "visible"),
    })


def atomic_browser_video(ctx: Any, params: JsonDict) -> dict:
    """Open a URL and monitor video playback status."""
    url = params.get("url", "")
    if not url:
        return {"status": "error", "error": "Missing required param: url"}
    # video 操作复用 open + execute 两步：先打开页面，再查 video 状态
    opened = _run_worker(ctx, "open", {"url": url})
    if opened.get("status") != "ok":
        return opened
    js = (
        "() => {"
        "const v = document.querySelector('video');"
        "if (!v) return null;"
        "return {duration: v.duration||0, current_time: v.currentTime||0,"
        " paused: v.paused, ended: v.ended, ready_state: v.readyState,"
        " width: v.videoWidth, height: v.videoHeight};"
        "}"
    )
    info = _run_worker(ctx, "execute", {"script": js})
    if info.get("status") != "ok":
        return info
    video_info = info.get("result")
    if video_info is None:
        return {"status": "error", "error": "No <video> element found on page"}
    playing = not video_info.get("paused", True) and not video_info.get("ended", False)
    return {
        "status": "ok",
        "playing": playing,
        "duration": video_info.get("duration", 0),
        "current_time": video_info.get("current_time", 0),
        "width": video_info.get("width"),
        "height": video_info.get("height"),
        "url": opened.get("url"),
    }


def atomic_browser_screenshot(ctx: Any, params: JsonDict) -> dict:
    """Take a screenshot of the page (or a specific element)."""
    save_path = params.get("save_path", "")
    return _run_worker(ctx, "screenshot", {
        "selector": params.get("selector"),
        "full_page": params.get("full_page", True),
        "save_path": save_path,
    })


def atomic_browser_execute(ctx: Any, params: JsonDict) -> dict:
    """Execute JavaScript in the browser page."""
    script = params.get("script", "")
    if not script:
        return {"status": "error", "error": "Missing required param: script"}
    return _run_worker(ctx, "execute", {"script": script, "args": params.get("args")})


# ── Cleanup helper (for tear-down) ────────────────────────────────────────────

def atomic_browser_close(ctx: Any, _params: JsonDict | None = None) -> dict:
    """Close the browser (worker process is per-call, so this is a no-op)."""
    return {"status": "ok"}


__all__ = [
    "atomic_browser_open",
    "atomic_browser_click",
    "atomic_browser_type",
    "atomic_browser_scroll",
    "atomic_browser_extract",
    "atomic_browser_wait",
    "atomic_browser_video",
    "atomic_browser_screenshot",
    "atomic_browser_execute",
    # internal but exported for convenience
    "atomic_browser_close",
]
