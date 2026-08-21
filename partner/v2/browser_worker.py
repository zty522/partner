"""Partner v2 — Browser worker (独立进程).

用法:
    python3 browser_worker.py <action> <params_json> <profile_dir>

在独立子进程里用 Playwright Async API 执行单个浏览器操作，把结果以 JSON
写到 stdout。独立进程环境干净，避免在 Partner 主进程（systemd 服务、多线程、
长驻事件循环）里启动 chromium 时发生的 SIGTRAP 崩溃。

profile_dir 用于持久化会话（登录状态 / cookie），为空时用临时 profile。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

from playwright.async_api import async_playwright

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-dev-shm-usage",
]


async def _dispatch(action: str, params: dict, page) -> dict:
    pw_timeout = None
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        pw_timeout = PlaywrightTimeout
    except Exception:
        pass

    if action == "open":
        url = params.get("url", "")
        if not url:
            return {"status": "error", "error": "Missing required param: url"}
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            return {
                "status": "ok",
                "url": page.url,
                "title": await page.title(),
                "status_code": resp.status if resp else None,
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    if action == "click":
        selector = params.get("selector", "")
        if not selector:
            return {"status": "error", "error": "Missing required param: selector"}
        wait_ms = int(params.get("wait_ms", 0))
        try:
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)
            await page.wait_for_selector(selector, state="visible", timeout=10_000)
            await page.click(selector)
            return {"status": "ok", "selector": selector}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    if action == "type":
        selector = params.get("selector", "")
        text = params.get("text", "")
        if not selector:
            return {"status": "error", "error": "Missing required param: selector"}
        try:
            await page.wait_for_selector(selector, state="visible", timeout=10_000)
            if params.get("clear_first", True):
                await page.fill(selector, "")
            await page.type(selector, text, delay=10)
            return {"status": "ok", "selector": selector}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    if action == "scroll":
        direction = params.get("direction", "down")
        amount = int(params.get("amount", 300))
        delta = amount if direction == "down" else -amount
        try:
            await page.evaluate(f"window.scrollBy(0, {delta})")
            await page.wait_for_timeout(100)
            scroll_y = await page.evaluate("window.scrollY")
            return {"status": "ok", "scroll_y": scroll_y, "direction": direction}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    if action == "extract":
        selector = params.get("selector", "body")
        attribute = params.get("attribute")
        fmt = params.get("format", "text")
        _sel = selector.replace("'", "\\'")
        _attr = (attribute or "").replace("'", "\\'")
        try:
            if fmt == "list":
                if attribute:
                    js = f"() => Array.from(document.querySelectorAll('{_sel}')).map(el => el.getAttribute('{_attr}'))"
                else:
                    js = f"() => Array.from(document.querySelectorAll('{_sel}')).map(el => el.textContent?.trim() || '')"
                content = await page.evaluate(js)
            elif attribute:
                js = f"() => {{const el = document.querySelector('{_sel}'); return el ? el.getAttribute('{_attr}') : null;}}"
                content = await page.evaluate(js)
            elif fmt == "html":
                el = await page.query_selector(selector)
                content = await el.inner_html() if el else None
            else:
                el = await page.query_selector(selector)
                content = await el.inner_text() if el else None
            if content is None:
                return {"status": "error", "error": f"Element not found: {selector}"}
            if isinstance(content, str) and fmt != "html":
                content = "\n".join(line.strip() for line in content.splitlines()).strip()
            return {"status": "ok", "content": content}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    if action == "wait":
        selector = params.get("selector", "")
        if not selector:
            return {"status": "error", "error": "Missing required param: selector"}
        timeout = int(params.get("timeout", 10_000))
        state = params.get("state", "visible")
        try:
            await page.wait_for_selector(selector, state=state, timeout=timeout)
            return {"status": "ok", "selector": selector, "state": state}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    if action == "screenshot":
        selector = params.get("selector")
        full_page = params.get("full_page", True)
        save_path = params.get("save_path", "")
        try:
            if save_path:
                path = save_path
            else:
                fd, path = tempfile.mkstemp(suffix=".png", prefix="partner_screenshot_")
                os.close(fd)
            if selector:
                await page.wait_for_selector(selector, state="visible", timeout=10_000)
                el = await page.query_selector(selector)
                if el is None:
                    return {"status": "error", "error": f"Element not found: {selector}"}
                await el.screenshot(path=path)
                note = f"element screenshot: {selector}"
            else:
                await page.screenshot(path=path, full_page=full_page)
                note = "full page" if full_page else "viewport"
            return {"status": "ok", "ok": True, "path": path, "note": note, "content": path, "files": [path]}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    if action == "execute":
        script = params.get("script", "")
        if not script:
            return {"status": "error", "error": "Missing required param: script"}
        try:
            if params.get("args") is not None:
                result = await page.evaluate(script, params["args"])
            else:
                result = await page.evaluate(script)
            return {"status": "ok", "result": result}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    if action == "close":
        return {"status": "ok"}

    return {"status": "error", "error": f"Unknown action: {action}"}


async def _main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"status": "error", "error": "usage: browser_worker.py <action> <params_json> [profile_dir]"}))
        return 1

    action = sys.argv[1]
    try:
        params = json.loads(sys.argv[2])
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"bad params json: {exc}"}))
        return 1
    profile_dir = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else ""

    try:
        async with async_playwright() as p:
            if profile_dir:
                context = await p.chromium.launch_persistent_context(
                    profile_dir, headless=True, args=_LAUNCH_ARGS
                )
            else:
                browser = await p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
            page = context.pages[0] if context.pages else await context.new_page()
            result = await _dispatch(action, params, page)
            print(json.dumps(result, ensure_ascii=False, default=str))
            await context.close()
            return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"worker fatal: {type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
