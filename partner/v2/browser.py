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
import threading
from typing import Any

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_worker.py")
_PYTHON = sys.executable or "python3"
_REQUEST_LOCK = threading.RLock()


def _push_visual_file(path: str, caption: str) -> dict:
    from partner.mind.executor import push_file_now
    return push_file_now(path, caption)


def _push_visual_text(content: str) -> dict:
    from partner.mind.executor import push_text_now
    return push_text_now(content)


def _profile_dir(ctx: Any) -> str:
    """Persistent profile directory for this instance (keeps login state).

    放在 /tmp（Linux tmpfs）而非实例工作目录（NTFS 挂载），避免 chromium
    在 NTFS 上初始化 profile 时的潜在问题。
    """
    ws = getattr(ctx, "workspace", "") or ""
    instance_id = os.path.basename(ws.rstrip("/")) or "default"
    import tempfile
    return os.path.join(tempfile.gettempdir(), f"partner_browser_profile_{instance_id}")


_worker_proc = None
_worker_profile = ""
_worker_sock = ""
_worker_unit = ""
_worker_visible = False
import socket as _socket_mod
import time as _time_mod


def _instance_id(ctx: Any) -> str:
    ws = getattr(ctx, "workspace", "") or ""
    value = os.path.basename(ws.rstrip("/")) or "default"
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in value)


def _unit_name(ctx: Any) -> str:
    return f"partner-browser-{_instance_id(ctx)}"


def _socket_request_unlocked(sock_path: str, action: str, params: JsonDict, timeout: int = 150) -> dict:
    s = _socket_mod.socket(_socket_mod.AF_UNIX, _socket_mod.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(sock_path)
        s.sendall(json.dumps({"action": action, "params": params}, ensure_ascii=False).encode("utf-8"))
        s.shutdown(_socket_mod.SHUT_WR)
        chunks = []
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        s.close()


def _socket_request(sock_path: str, action: str, params: JsonDict, timeout: int = 150) -> dict:
    """Serialize page operations for the single-page worker session.

    Harness may schedule independent-looking browser steps in parallel, but a
    click/extract/screenshot sequence operates on one mutable page. Concurrent
    socket requests previously caused empty replies and worker restarts.
    """
    with _REQUEST_LOCK:
        return _socket_request_unlocked(sock_path, action, params, timeout)


def _worker_alive(sock_path: str = "") -> bool:
    """Verify the worker protocol, not merely the existence of a stale socket."""
    sock_path = sock_path or _worker_sock
    if not sock_path or not os.path.exists(sock_path):
        return False
    try:
        return _socket_request(sock_path, "ping", {}, timeout=2).get("status") == "ok"
    except Exception:
        return False


def _ensure_worker(ctx: Any, *, visible: bool = False):
    """Start one deterministic worker per instance and reuse it across restarts."""
    global _worker_proc, _worker_profile, _worker_sock, _worker_unit, _worker_visible
    profile = _profile_dir(ctx)
    sock = os.path.join(profile, "worker.sock")
    unit = _unit_name(ctx)
    if _worker_alive(sock):
        if (not _worker_profile and visible) or (_worker_profile and _worker_visible != visible):
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=15)
            try:
                os.remove(sock)
            except OSError:
                pass
        else:
            _worker_profile, _worker_sock, _worker_unit = profile, sock, unit
            return True

    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=15)
    os.makedirs(profile, exist_ok=True)
    if os.path.exists(sock):
        try:
            os.remove(sock)
        except OSError:
            pass
    subprocess.run(["systemctl", "--user", "reset-failed", unit],
                   capture_output=True, timeout=15)
    try:
        launch = ["systemd-run", "--user", "--collect", f"--unit={unit}",
                  "--property=KillMode=mixed", "--property=TimeoutStopSec=10"]
        if visible:
            display_env = {
                "DISPLAY": os.environ.get("DISPLAY", ":0"),
                "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", "wayland-0"),
                "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                "PULSE_SERVER": os.environ.get("PULSE_SERVER", "unix:/mnt/wslg/PulseServer"),
            }
            launch.extend(f"--setenv={key}={value}" for key, value in display_env.items() if value)
        launch.extend([_PYTHON, _WORKER, "serve_socket", profile, "visible" if visible else "headless"])
        _worker_proc = subprocess.Popen(
            launch,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return None
    _worker_profile = profile
    _worker_sock = sock
    _worker_unit = unit
    _worker_visible = visible
    for _ in range(40):
        if _worker_alive(sock):
            return True
        _time_mod.sleep(0.5)
    return None


def _kill_worker():
    global _worker_proc, _worker_profile, _worker_sock, _worker_unit, _worker_visible
    try:
        if _worker_unit:
            subprocess.run(["systemctl", "--user", "stop", _worker_unit], capture_output=True, timeout=15)
    except Exception:
        pass
    _worker_proc = None
    _worker_profile = ""
    _worker_sock = ""
    _worker_unit = ""
    _worker_visible = False


def _run_worker(ctx: Any, action: str, params: JsonDict, *, visible: bool | None = None) -> dict:
    """通过 unix socket 向长驻 worker 发送操作并接收结果。"""
    try:
        requested_visible = _worker_visible if visible is None and _worker_profile else bool(visible)
        if not _ensure_worker(ctx, visible=requested_visible) or not _worker_sock:
            return {"status": "error", "error": "worker socket 未就绪"}
        return _socket_request(_worker_sock, action, params)
    except Exception as exc:
        logger.exception("browser worker failed for %s", action)
        return {"status": "error", "error": str(exc)}

# ── Handlers ──────────────────────────────────────────────────────────────────

def atomic_browser_open(ctx: Any, params: JsonDict) -> dict:
    """Open a URL. Set ``visible``/``foreground`` for a user-visible window."""
    url = params.get("url", "")
    if not url:
        return {"status": "error", "error": "Missing required param: url"}
    visible = bool(params.get("visible", params.get("foreground", False)))
    if "headless" in params and params.get("headless") is False:
        visible = True
    result = _run_worker(ctx, "open", {"url": url, "bring_to_front": visible}, visible=visible)
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
    """Take a screenshot of the page (or a specific element).

    save_path 缺省时保存到任务工作目录（working_dir/project_dir），
    而不是 /tmp——避免截图产出不可追踪、被推送环节误当文本处理。
    """
    save_path = params.get("save_path") or params.get("path") or params.get("output_path") or ""
    _ti = getattr(ctx, "task_instance", None)
    _ti_wd = getattr(_ti, "working_dir", "") if _ti is not None else ""
    filename = str(params.get("filename") or "").strip()
    if not save_path and filename and _ti_wd:
        save_path = os.path.join(_ti_wd, os.path.basename(filename))
    # Reject unrelated /tmp paths, but allow an explicit path inside this task.
    if save_path.startswith("/tmp"):
        absolute_save = os.path.abspath(save_path)
        absolute_task = os.path.abspath(_ti_wd) if _ti_wd else ""
        if not absolute_task or not (absolute_save == absolute_task or absolute_save.startswith(absolute_task + os.sep)):
            save_path = ""
    if not save_path:
        try:
            for expected in (getattr(_ti, "expected_artifacts", None) or []):
                pattern = str(expected.get("pattern") or "") if isinstance(expected, dict) else ""
                if (pattern.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                        and not any(token in pattern for token in ("*", "?", "[", ",", ";"))):
                    save_path = os.path.join(_ti_wd, os.path.basename(pattern))
                    break
            base = (getattr(ctx, "working_dir", "") or getattr(ctx, "project_dir", "")
                    or _ti_wd or getattr(ctx, "artifact_path", "") or "")
            if base and not save_path:
                os.makedirs(base, exist_ok=True)
                save_path = os.path.join(base, f"screenshot_{int(_time_mod.time())}.png")
        except Exception:
            save_path = ""
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


def _visual_step(ctx: Any, label: str, filename: str) -> dict:
    """Screenshot, vision-describe and deliver one visible browser step."""
    ti = getattr(ctx, "task_instance", None)
    wd = (getattr(ti, "working_dir", "") if ti is not None else "") or \
         getattr(ctx, "working_dir", "") or getattr(ctx, "project_dir", "")
    if not wd:
        return {"ok": False, "label": label, "error": "缺少任务工作目录"}
    os.makedirs(wd, exist_ok=True)
    path = os.path.join(wd, os.path.basename(filename))
    shot = _run_worker(ctx, "screenshot", {"full_page": True, "save_path": path})
    if shot.get("status") != "ok":
        return {"ok": False, "label": label, "error": shot.get("error", "截图失败")}
    try:
        from partner.v2.vision_events import read_image_with_qwen
        vision = read_image_with_qwen(
            path,
            "这是自动化操作步骤截图。请用中文准确描述当前页面、选中的功能、关键文字和可交互控件；"
            "指出它能证明本步骤完成了什么，不要猜测截图外的信息。",
            getattr(ctx, "workspace", ""),
        )
    except Exception as exc:
        vision = {"ok": False, "error": str(exc)}
    try:
        file_delivery = _push_visual_file(path, f"01 操作截图 · {label}")
        if vision.get("ok"):
            text = f"🖼️ 01 操作步骤 · {label}\n视觉大模型：{vision.get('model')}\n图片内容：{vision.get('description')}"
        else:
            text = f"⚠️ 01 操作步骤 · {label}\n截图已生成，但视觉大模型读取失败：{vision.get('error')}"
        text_delivery = _push_visual_text(text)
    except Exception as exc:
        file_delivery = {"ok": False, "delivered": False, "error": str(exc)}
        text_delivery = {"ok": False, "delivered": False, "error": str(exc)}
    ok = bool(vision.get("ok") and file_delivery.get("delivered") and text_delivery.get("delivered"))
    return {"ok": ok, "label": label, "path": path, "vision": vision,
            "file_delivery": file_delivery, "text_delivery": text_delivery}


def atomic_xhs_open_publish_editor(ctx: Any, params: JsonDict) -> dict:
    """Open and verify Xiaohongshu's image/text upload entry atomically."""
    url = str(params.get("url") or
              "https://creator.xiaohongshu.com/publish/publish?source=official")
    opened = _run_worker(ctx, "open", {"url": url, "bring_to_front": True}, visible=True)
    if opened.get("status") != "ok":
        return {"ok": False, "status": "open_failed", "error": opened.get("error", "open failed")}
    visual_steps = [_visual_step(ctx, "步骤1：打开小红书创作发布页", "xhs_step_01_open_publish.png")]

    clicked = _run_worker(ctx, "execute", {"script": """() => {
      const visible = el => {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
      };
      const nodes = [...document.querySelectorAll('button, [role=button], div, span')]
        .filter(el => visible(el) && (el.textContent || '').trim() === '上传图文');
      const target = nodes[0];
      if (!target) return {clicked: false, candidates: 0};
      target.click();
      return {clicked: true, candidates: nodes.length, tag: target.tagName};
    }"""})
    click_info = clicked.get("result") if isinstance(clicked.get("result"), dict) else {}
    if clicked.get("status") != "ok" or not click_info.get("clicked"):
        return {"ok": False, "status": "tab_not_found",
                "error": "真实页面中未找到可见的“上传图文”入口",
                "opened": opened, "click": clicked}

    # The title/body fields appear only after media selection.  At this stage
    # the real acceptance signal is the image uploader, not fabricated fields.
    _time_mod.sleep(1.0)
    evidence = _run_worker(ctx, "execute", {"script": """() => {
      const body = (document.body?.innerText || '').trim();
      const files = [...document.querySelectorAll('input[type=file]')];
      const fields = [...document.querySelectorAll('input:not([type=file]), textarea, [contenteditable=true]')];
      return {
        url: location.href, title: document.title, body_excerpt: body.slice(0, 1600),
        has_upload_tab: body.includes('上传图文'),
        has_image_prompt: /上传图片|拖拽图片|点击上传/.test(body),
        file_input_count: files.length, editor_field_count: fields.length,
        field_hints: fields.slice(0, 12).map(el => ({tag: el.tagName,
          placeholder: el.getAttribute('placeholder') || '',
          contenteditable: el.getAttribute('contenteditable') || ''}))
      };
    }"""})
    info = evidence.get("result") if isinstance(evidence.get("result"), dict) else {}
    verified = bool(info.get("has_upload_tab") and
                    (info.get("has_image_prompt") or int(info.get("file_input_count") or 0) > 0))
    visual_steps.append(_visual_step(ctx, "步骤2：点击并进入上传图文", "xhs_step_02_image_text_tab.png"))

    ti = getattr(ctx, "task_instance", None)
    wd = (getattr(ti, "working_dir", "") if ti is not None else "") or \
         getattr(ctx, "working_dir", "") or getattr(ctx, "project_dir", "")
    if not wd:
        return {"ok": False, "status": "no_working_dir", "error": "缺少任务工作目录", "evidence": info}
    os.makedirs(wd, exist_ok=True)
    screenshot_path = os.path.join(wd, "xiaohongshu_publish_editor.png")
    shot = _run_worker(ctx, "screenshot", {"full_page": True, "save_path": screenshot_path})
    if shot.get("status") != "ok":
        return {"ok": False, "status": "screenshot_failed",
                "error": shot.get("error", "截图失败"), "evidence": info}
    evidence_path = os.path.join(wd, "xiaohongshu_publish_editor_evidence.json")
    with open(evidence_path, "w", encoding="utf-8") as fh:
        json.dump({"verified": verified, "opened": opened, "click": click_info, "evidence": info},
                  fh, ensure_ascii=False, indent=2)
    visual_ok = all(step.get("ok") for step in visual_steps)
    return {
        "ok": bool(verified and visual_ok),
        "status": "editor_entry_verified" if verified and visual_ok else
                  ("visual_delivery_failed" if verified else "editor_entry_unverified"),
        "evidence": info,
        "visual_steps": visual_steps,
        "files": [step["path"] for step in visual_steps if step.get("path")] + [screenshot_path, evidence_path],
        "path": screenshot_path,
        "content": ("已进入上传图文入口；当前可核验证据为图片上传控件。"
                    "标题和正文编辑框通常会在选择图片后出现，本轮未上传或发布任何内容。")
                   if verified else "页面已打开并点击入口，但未检测到图片上传控件。",
    }


def atomic_xhs_inspect_upload_requirements(ctx: Any, params: JsonDict) -> dict:
    """Inspect the real image uploader contract after entering the editor."""
    entry = atomic_xhs_open_publish_editor(ctx, params)
    if not entry.get("ok"):
        return entry
    inspected = _run_worker(ctx, "execute", {"script": """() => {
      const body = (document.body?.innerText || '').trim();
      const inputs = [...document.querySelectorAll('input[type=file]')];
      return {
        url: location.href,
        inputs: inputs.map(el => ({accept: el.accept || '', multiple: !!el.multiple,
          disabled: !!el.disabled, name: el.name || ''})),
        requirement_lines: body.split(String.fromCharCode(10)).map(x => x.trim()).filter(x =>
          /图片|上传|格式|大小|张|宽|高|比例|png|jpg|jpeg/i.test(x)).slice(0, 30)
      };
    }"""})
    if inspected.get("status") != "ok":
        return {"ok": False, "status": "requirements_extract_failed",
                "error": inspected.get("error", "页面要求读取失败"),
                "files": entry.get("files") or []}
    info = inspected.get("result") if isinstance(inspected.get("result"), dict) else {}
    verified = bool(info.get("inputs") or info.get("requirement_lines"))
    ti = getattr(ctx, "task_instance", None)
    wd = (getattr(ti, "working_dir", "") if ti is not None else "") or getattr(ctx, "working_dir", "")
    if not wd:
        return {"ok": False, "status": "no_working_dir", "error": "缺少任务工作目录"}
    json_path = os.path.join(wd, "xiaohongshu_upload_requirements.json")
    md_path = os.path.join(wd, "xiaohongshu_upload_requirements.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=2)
    lines = info.get("requirement_lines") or []
    inputs = info.get("inputs") or []
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("# 小红书上传图文要求：真实页面核验\n\n")
        fh.write("## 已执行动作\n\n重新进入上传图文入口，并直接读取页面文件控件属性与可见要求文字。\n\n")
        fh.write("## 文件控件证据\n\n")
        for idx, item in enumerate(inputs, 1):
            fh.write(f"- 控件 {idx}: accept={item.get('accept') or '页面未声明'}; multiple={item.get('multiple')}; disabled={item.get('disabled')}\n")
        fh.write("\n## 页面要求文字\n\n")
        for line in lines:
            fh.write(f"- {line}\n")
        fh.write("\n## 结论与安全边界\n\n这些是实时 DOM 证据，不是模型猜测。当前只核验上传契约；没有选择用户文件、填写内容或执行公开发布。\n")
    requirement_visual = _visual_step(
        ctx, "步骤3：读取并核验上传格式、大小与分辨率要求", "xhs_step_03_upload_requirements.png",
    )
    all_visual_steps = list(entry.get("visual_steps") or []) + [requirement_visual]
    return {
        "ok": bool(verified and requirement_visual.get("ok")),
        "status": "upload_requirements_verified" if verified and requirement_visual.get("ok") else
                  ("visual_delivery_failed" if verified else "upload_requirements_unverified"),
        "evidence": info, "visual_step": requirement_visual,
        # Keep the two entry-step receipts reachable from the complete audit.
        # Campaign accounting can then count all three real vision calls.
        "visual_steps": all_visual_steps,
        "model_calls": sum(1 for step in all_visual_steps if (step.get("vision") or {}).get("model")),
        "files": list(entry.get("files") or []) + [json_path, md_path]
                 + ([requirement_visual["path"]] if requirement_visual.get("path") else []),
        "path": md_path,
        "content": f"已读取 {len(inputs)} 个文件控件和 {len(lines)} 条页面要求；未上传或发布。",
    }


# ── Cleanup helper (for tear-down) ────────────────────────────────────────────

def atomic_browser_close(ctx: Any, _params: JsonDict | None = None) -> dict:
    """Close the instance browser and its deterministic systemd unit."""
    try:
        if _worker_sock and _worker_alive(_worker_sock):
            _socket_request(_worker_sock, "close", {}, timeout=5)
    except Exception:
        pass
    _kill_worker()
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
    "atomic_xhs_open_publish_editor",
    "atomic_xhs_inspect_upload_requirements",
    # internal but exported for convenience
    "atomic_browser_close",
]
