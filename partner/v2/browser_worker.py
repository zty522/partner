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
    "--disable-blink-features=AutomationControlled",
    "--disable-features=AutomationControlled",
    "--lang=zh-CN",
]


async def _apply_stealth(context) -> None:
    """反爬加固：抹掉 headless 自动化特征（webdriver 标记、plugins、languages、UA 一致性）。"""
    try:
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = window.chrome || {runtime: {}};
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            const _q = window.navigator.permissions && window.navigator.permissions.query;
            if (_q) {
                window.navigator.permissions.query = (p) => (
                    p.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : _q(p)
                );
            }
        """)
    except Exception:
        pass


async def _dispatch(action: str, params: dict, page) -> dict:
    if action == "ping":
        return {"status": "ok", "pid": os.getpid()}
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
            if url.startswith("data:text/html,"):
                from urllib.parse import quote
                url = "data:text/html;charset=utf-8," + quote(url.split(",", 1)[1])
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # 给 JS 渲染留时间（Bing 等站点加载慢，立即操作会超时）
            await page.wait_for_timeout(3000)
            if params.get("bring_to_front"):
                await page.bring_to_front()
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
            await page.wait_for_selector(selector, state="visible", timeout=30_000)
            await page.click(selector)
            return {"status": "ok", "selector": selector}
        except Exception as exc:
            hint = await _dump_interactive_elements(page)
            return {"status": "error", "error": str(exc) + hint}

    if action == "type":
        selector = params.get("selector", "")
        text = params.get("text", "")
        if not selector:
            return {"status": "error", "error": "Missing required param: selector"}
        try:
            await page.wait_for_selector(selector, state="visible", timeout=30_000)
            if params.get("clear_first", True):
                await page.fill(selector, "")
            await page.type(selector, text, delay=10)
            return {"status": "ok", "selector": selector}
        except Exception as exc:
            hint = await _dump_interactive_elements(page)
            return {"status": "error", "error": str(exc) + hint}

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
            hint = await _dump_interactive_elements(page)
            return {"status": "error", "error": str(exc) + hint}

    if action == "screenshot":
        selector = params.get("selector")
        # full_page 长页面截图会滚动卡住（Bing 等无限滚动页），默认视口截图
        full_page = params.get("full_page", False)
        save_path = params.get("save_path", "")
        try:
            if save_path:
                path = save_path
            else:
                fd, path = tempfile.mkstemp(suffix=".png", prefix="partner_screenshot_")
                os.close(fd)
            if selector:
                await page.wait_for_selector(selector, state="visible", timeout=30_000)
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
            import traceback as _tb
            _tb.print_exc(file=sys.stderr)
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


async def _dump_interactive_elements(page):
    """失败时 dump 页面可见的可交互元素（让 LLM 拿到真实选择器线索，不再盲猜）。"""
    try:
        items = await page.evaluate("""() => {
            const sel = 'input, button, textarea, select, a[href], [role=button]';
            const els = Array.from(document.querySelectorAll(sel));
            return els.slice(0, 15).map(el => {
                const r = el.getBoundingClientRect();
                if (r.width === 0 && r.height === 0) return null;
                const tag = el.tagName.toLowerCase();
                const id = el.id ? '#' + el.id : '';
                const name = el.name ? '[name=' + el.name + ']' : '';
                const ph = el.placeholder ? ' placeholder=' + JSON.stringify(el.placeholder).slice(0, 40) : '';
                const txt = (el.textContent || '').trim().slice(0, 30);
                return tag + id + name + ph + (txt ? ' text=' + JSON.stringify(txt) : '');
            }).filter(Boolean);
        }""")
        if items:
            return "；可见元素: " + "; ".join(items)
        # 无交互元素时返回页面标题/正文摘要（区分"空白/被拦截"与"未加载完"）
        try:
            info = await page.evaluate("""() => {
                const t = document.title || '';
                const b = (document.body ? document.body.innerText : '').trim().slice(0, 250);
                return {title: t, body: b};
            }""")
            return f"；页面无可交互元素 标题={info.get('title', '')[:60]!r} 正文={info.get('body', '')[:120]!r}"
        except Exception:
            return "；页面无可交互元素(可能未加载完成)"
    except Exception:
        return ""




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
            _UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36")
            if profile_dir:
                context = await p.chromium.launch_persistent_context(
                    profile_dir, headless=True, args=_LAUNCH_ARGS,
                    user_agent=_UA,
                    viewport={"width": 1280, "height": 720},
                )
                await _apply_stealth(context)
            else:
                browser = await p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=_UA,
                )
            page = context.pages[0] if context.pages else await context.new_page()
            result = await _dispatch(action, params, page)
            print(json.dumps(result, ensure_ascii=False, default=str))
            await context.close()
            return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"worker fatal: {type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1


async def _serve_socket(profile_dir: str, visible: bool = False):
    """长驻 socket 模式：systemd-run 干净进程 + unix socket 通信（解决 SIGTRAP + 会话断裂）。"""
    import socket as _sock
    import json as _json

    os.makedirs(profile_dir, exist_ok=True)
    sock_path = os.path.join(profile_dir, "worker.sock")
    if os.path.exists(sock_path):
        os.remove(sock_path)
    server = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(8)
    server.setblocking(False)

    async with async_playwright() as p:
        _UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0.0.0 Safari/537.36")
        context = await p.chromium.launch_persistent_context(
            profile_dir, headless=not visible, args=_LAUNCH_ARGS,
            user_agent=_UA, viewport={"width": 1280, "height": 720},
        )
        await _apply_stealth(context)
        page = context.pages[0] if context.pages else await context.new_page()
        loop = asyncio.get_running_loop()
        while True:
            try:
                conn, _ = await loop.sock_accept(server)
            except asyncio.CancelledError:
                break
            conn.setblocking(False)
            action = ""
            try:
                data = b""
                while True:
                    chunk = await loop.sock_recv(conn, 65536)
                    if not chunk:
                        break
                    data += chunk
                req = _json.loads(data.decode("utf-8"))
                action = req.get("action", "")
                params = req.get("params", {})
                result = await _dispatch(action, params, page)
            except Exception as exc:
                result = {"status": "error", "error": f"serve_socket: {type(exc).__name__}: {exc}"}
            try:
                await loop.sock_sendall(conn, _json.dumps(result, ensure_ascii=False, default=str).encode())
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            if action == "close":
                break
        await context.close()
    try:
        server.close()
        if os.path.exists(sock_path):
            os.remove(sock_path)
    except OSError:
        pass


async def _serve(profile_dir: str):
    """长驻模式：持续读 stdin 的操作指令，共享同一 context/page（解决跨进程会话断裂）。"""
    import json as _json

    async with async_playwright() as p:
        _UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0.0.0 Safari/537.36")
        if profile_dir:
            context = await p.chromium.launch_persistent_context(
                profile_dir, headless=True, args=_LAUNCH_ARGS,
                user_agent=_UA, viewport={"width": 1280, "height": 720},
            )
        else:
            browser = await p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            context = await browser.new_context(viewport={"width": 1280, "height": 720}, user_agent=_UA)
        page = context.pages[0] if context.pages else await context.new_page()
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            try:
                req = _json.loads(line)
                action = req.get("action", "")
                params = req.get("params", {})
                result = await _dispatch(action, params, page)
            except Exception as exc:
                result = {"status": "error", "error": f"serve: {type(exc).__name__}: {exc}"}
            import sys as _sys
            print(f"[serve] action={action} result_type={type(result).__name__}", file=_sys.stderr, flush=True)
            print(_json.dumps(result, ensure_ascii=False, default=str), flush=True)
        await context.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "serve_socket":
        profile_dir = sys.argv[2] if len(sys.argv) > 2 else ""
        visible = len(sys.argv) > 3 and sys.argv[3].lower() == "visible"
        sys.exit(asyncio.run(_serve_socket(profile_dir, visible=visible)))
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        profile_dir = sys.argv[2] if len(sys.argv) > 2 else ""
        sys.exit(asyncio.run(_serve(profile_dir)))
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
