# -*- coding: utf-8 -*-
"""Windows-side login-state browser fetcher for Partner (xiaohongshu etc.).

Runs on Windows with system Edge (msedge channel) + the dedicated logged-in
profile (C:\\Users\\zty12\\partner_profile), so it uses the Windows network
stack, real Edge fingerprints and the user's login state.  WSL-side Partner
invokes this script via WSL interop:

    <win-python> scripts/browser/win_fetch.py <url>

Outputs a single JSON object on stdout:
    {"status": "text_available"|"metadata_only"|"fetch_failed"|"bad_usage",
     "title": str, "text": str, "media_urls": [str],
     "screenshot_path": str, "reason": str, "url": str}
"""
import json
import os
import re
import sys
import time


def _main() -> int:
    if len(sys.argv) < 2:
        sys.stdout.write(json.dumps({"status": "bad_usage", "reason": "missing url",
                                     "title": "", "text": "", "media_urls": [],
                                     "screenshot_path": "", "url": ""}, ensure_ascii=False))
        return 2
    url = sys.argv[1]
    profile = os.environ.get("PARTNER_BROWSER_PROFILE", r"C:\Users\zty12\partner_profile")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        sys.stdout.write(json.dumps({"status": "fetch_failed", "reason": f"playwright missing: {exc}",
                                     "title": "", "text": "", "media_urls": [],
                                     "screenshot_path": "", "url": url}, ensure_ascii=False))
        return 1
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile,
                channel="msedge",
                headless=True,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            except Exception:
                pass
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                ctx.close()
                sys.stdout.write(json.dumps({"status": "fetch_failed", "reason": f"goto: {exc}",
                                             "title": "", "text": "", "media_urls": [],
                                             "screenshot_path": "", "url": url}, ensure_ascii=False))
                return 1
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            # scroll a few times to trigger lazy content
            for _ in range(3):
                try:
                    page.mouse.wheel(0, 1200)
                except Exception:
                    pass
                time.sleep(1.0)
            title = ""
            try:
                title = page.title() or ""
            except Exception:
                pass
            text = ""
            try:
                text = page.locator("body").inner_text(timeout=10000) or ""
            except Exception:
                pass
            media_urls = []
            try:
                media_urls = page.evaluate(
                    """() => Array.from(new Set([
                        ...Array.from(document.images || []).map(x => x.currentSrc || x.src),
                        ...Array.from(document.querySelectorAll('video, source')).map(x => x.currentSrc || x.src)
                    ].filter(Boolean)))"""
                )
            except Exception:
                pass
            media_urls = [str(x) for x in (media_urls or []) if str(x).strip()][:40]
            screenshot_path = ""
            if os.environ.get("PARTNER_BROWSER_SAVE_SCREENSHOT", "1").lower() not in {"0", "false", "off", "no"}:
                out_dir = os.environ.get("PARTNER_BROWSER_CAPTURE_DIR", "").strip() or os.path.join(
                    os.path.expanduser("~"), "partner_browser_captures")
                try:
                    os.makedirs(out_dir, exist_ok=True)
                    screenshot_path = os.path.join(out_dir, f"partner_page_{abs(hash(url))}.png")
                    page.screenshot(path=screenshot_path, full_page=True)
                except Exception:
                    screenshot_path = ""
            ctx.close()
            status = "text_available" if len(text.strip()) >= 100 else "metadata_only"
            reason = ""
            if "当前笔记暂时无法浏览" in text or "无法浏览" in text:
                status = "fetch_failed"
                reason = "笔记详情需要 xsec_token（当前链接不可浏览），请提供带 xsec_token 的分享链接或从搜索/推荐页抓取"
            elif len(text.strip()) < 100:
                reason = "正文过短，可能需 App/验证码或动态权限"
            sys.stdout.write(json.dumps({
                "status": status, "title": title, "text": text,
                "media_urls": media_urls, "screenshot_path": screenshot_path,
                "reason": reason, "url": url}, ensure_ascii=False))
            return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({"status": "fetch_failed", "reason": str(exc)[:300],
                                     "title": "", "text": "", "media_urls": [],
                                     "screenshot_path": "", "url": url}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(_main())
