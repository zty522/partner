"""Partner v2 — Browser automation module.

Playwright-based browser automation with lazy initialisation and 9 atomic
handlers: open, click, type, scroll, extract, wait, video, screenshot, execute.
"""

from __future__ import annotations

import logging
import os
import time
import subprocess
from typing import Any

from playwright.sync_api import (
    sync_playwright,
    Page,
    Browser,
    TimeoutError as PlaywrightTimeout,
)

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# ── Global (lazy) Playwright objects ──────────────────────────────────────────

_playwright: Any = None
_browser: Browser | None = None
_page: Page | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _install_chromium() -> None:
    """Install Playwright Chromium if not already available."""
    logger.info("Installing Playwright Chromium …")
    try:
        subprocess.run(
            ["python3", "-m", "playwright", "install", "chromium"],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Chromium installed successfully.")
    except subprocess.CalledProcessError as exc:
        logger.error("Failed to install Chromium: %s", exc.stderr)
        raise RuntimeError(f"Could not install Chromium: {exc.stderr}") from exc


def _ensure_browser(headless: bool = True, browser_type: str = "chromium") -> Page:
    """Lazy-initialise Playwright browser + page.

    Returns the global ``_page`` object.  On first call this starts a Playwright
    instance and opens a new browser context + page.  Subsequent calls reuse the
    same page unless the ``browser_type`` differs, in which case a new browser
    is launched.
    """
    global _playwright, _browser, _page  # noqa: PLW0603

    if _page is not None and _browser is not None:
        # Check whether the page is still alive
        try:
            _ = _page.url
            return _page
        except Exception:
            logger.info("Page is closed, will create a new one.")
            _page = None
            _browser = None

    # Verify Chromium is installed; install if missing
    try:
        from playwright.sync_api import sync_playwright as _sp  # noqa: F811
    except ImportError:
        raise ImportError("playwright is not installed — run: pip install playwright")

    # Try to detect whether the browser binaries are present
    _check_browser_installed(browser_type)

    if _playwright is None:
        _playwright = sync_playwright().start()

    launch_kwargs: dict[str, Any] = {"headless": headless}

    if browser_type == "chromium":
        _browser = _playwright.chromium.launch(**launch_kwargs)
    elif browser_type == "firefox":
        _browser = _playwright.firefox.launch(**launch_kwargs)
    elif browser_type == "webkit":
        _browser = _playwright.webkit.launch(**launch_kwargs)
    else:
        raise ValueError(f"Unsupported browser_type: {browser_type!r}")

    context = _browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    _page = context.new_page()
    return _page


def _check_browser_installed(browser_type: str) -> None:
    """Check browser binaries are present, install if missing."""
    # Playwright stores browsers under ~/.cache/ms-playwright/
    cache_dir = os.path.expanduser("~/.cache/ms-playwright")
    if not os.path.isdir(cache_dir) or not os.listdir(cache_dir):
        logger.info("No Playwright browsers found in cache; installing Chromium …")
        _install_chromium()


def _close_browser() -> None:
    """Cleanly shut down the browser and Playwright instance."""
    global _playwright, _browser, _page  # noqa: PLW0603

    if _page is not None:
        try:
            _page.close()
        except Exception:
            pass
        _page = None
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
        _playwright = None


# ── Handlers ──────────────────────────────────────────────────────────────────

def atomic_browser_open(ctx: Any, params: JsonDict) -> dict:
    """Open a URL in the browser.

    Parameters
    ----------
    url : str
        The URL to navigate to.
    headless : bool, optional
        Whether to run in headless mode (default ``True``).
    browser_type : str, optional
        Browser engine — ``"chromium"`` (default), ``"firefox"``, or ``"webkit"``.

    Returns
    -------
    dict
        ``{"status": "ok", "url": "<current-url>"}``
    """
    url = params.get("url", "")
    if not url:
        return {"status": "error", "error": "Missing required param: url"}

    headless = params.get("headless", True)
    browser_type = params.get("browser_type", "chromium")

    page = _ensure_browser(headless=headless, browser_type=browser_type)

    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        status_code = response.status if response else None
        logger.info("Navigated to %s — status %s", url, status_code)
        return {
            "status": "ok",
            "url": page.url,
            "title": page.title(),
            "status_code": status_code,
        }
    except PlaywrightTimeout:
        logger.warning("Timeout loading %s", url)
        return {
            "status": "ok",
            "url": page.url,
            "title": page.title(),
            "note": "page did not finish loading within 30s timeout",
        }
    except Exception as exc:
        logger.exception("Failed to open %s", url)
        return {"status": "error", "error": str(exc)}


def atomic_browser_click(ctx: Any, params: JsonDict) -> dict:
    """Click an element on the page.

    Parameters
    ----------
    selector : str
        CSS / XPath / text selector for the element.
    wait_ms : int, optional
        Milliseconds to wait before clicking (default ``0``).

    Returns
    -------
    dict
        ``{"status": "ok"}`` or ``{"status": "error", "error": "..."}``
    """
    selector = params.get("selector", "")
    if not selector:
        return {"status": "error", "error": "Missing required param: selector"}

    page = _ensure_browser()
    wait_ms = int(params.get("wait_ms", 0))

    try:
        if wait_ms > 0:
            page.wait_for_timeout(wait_ms)
        # Wait for the element to be visible before clicking
        page.wait_for_selector(selector, state="visible", timeout=10_000)
        page.click(selector)
        return {"status": "ok", "selector": selector}
    except PlaywrightTimeout:
        return {"status": "error", "error": f"Element not found or not visible: {selector}"}
    except Exception as exc:
        logger.exception("Click failed on %s", selector)
        return {"status": "error", "error": str(exc)}


def atomic_browser_type(ctx: Any, params: JsonDict) -> dict:
    """Type text into an input element.

    Parameters
    ----------
    selector : str
        CSS / XPath selector for the input element.
    text : str
        The text to type.
    clear_first : bool, optional
        Clear the input field before typing (default ``True``).

    Returns
    -------
    dict
        ``{"status": "ok"}``
    """
    selector = params.get("selector", "")
    text = params.get("text", "")
    if not selector:
        return {"status": "error", "error": "Missing required param: selector"}
    if text is None:
        text = ""

    page = _ensure_browser()
    clear_first = params.get("clear_first", True)

    try:
        page.wait_for_selector(selector, state="visible", timeout=10_000)
        if clear_first:
            page.fill(selector, "")
        page.type(selector, text, delay=10)  # small delay for realism
        return {"status": "ok", "selector": selector}
    except PlaywrightTimeout:
        return {"status": "error", "error": f"Input element not found: {selector}"}
    except Exception as exc:
        logger.exception("Type failed on %s", selector)
        return {"status": "error", "error": str(exc)}


def atomic_browser_scroll(ctx: Any, params: JsonDict) -> dict:
    """Scroll the page.

    Parameters
    ----------
    direction : str
        ``"down"`` or ``"up"``.
    amount : int
        Number of pixels to scroll.

    Returns
    -------
    dict
        ``{"status": "ok", "scroll_y": <new-scroll-position>}``
    """
    direction = params.get("direction", "down")
    amount = int(params.get("amount", 300))

    page = _ensure_browser()

    delta = amount if direction == "down" else -amount

    try:
        page.evaluate(f"window.scrollBy(0, {delta})")
        # Give layout a moment to settle
        page.wait_for_timeout(100)
        scroll_y = page.evaluate("window.scrollY")
        return {"status": "ok", "scroll_y": scroll_y, "direction": direction}
    except Exception as exc:
        logger.exception("Scroll failed")
        return {"status": "error", "error": str(exc)}


def atomic_browser_extract(ctx: Any, params: JsonDict) -> dict:
    """Extract content from the page.

    Parameters
    ----------
    selector : str, optional
        CSS selector for the element(s) to extract (default ``"body"``).
    attribute : str, optional
        If set, extract this attribute instead of text content.
    format : str, optional
        ``"text"`` (default), ``"html"``, or ``"list"`` (multiple elements).

    Returns
    -------
    dict
        ``{"status": "ok", "content": <extracted-content>}``
    """
    selector = params.get("selector", "body")
    attribute = params.get("attribute")
    fmt = params.get("format", "text")

    page = _ensure_browser()

    # Escape single quotes for JS string literals (can't use backslash in f-strings)
    _sel = selector.replace("'", "\\'")
    _attr = (attribute or "").replace("'", "\\'")

    try:
        if fmt == "list":
            # Return an array of values from all matching elements
            if attribute:
                js = (
                    "() => {"
                    f"const els = document.querySelectorAll('{_sel}');"
                    f"return Array.from(els).map(el => el.getAttribute('{_attr}'));"
                    "}"
                )
            else:
                js = (
                    "() => {"
                    f"const els = document.querySelectorAll('{_sel}');"
                    "return Array.from(els).map(el => el.textContent?.trim() || '');"
                    "}"
                )
            content = page.evaluate(js)
        elif attribute:
            # Single element, extract attribute
            js = (
                "() => {"
                f"const el = document.querySelector('{_sel}');"
                f"return el ? el.getAttribute('{_attr}') : null;"
                "}"
            )
            content = page.evaluate(js)
        elif fmt == "html":
            el = page.query_selector(selector)
            content = el.inner_html() if el else None
        else:
            el = page.query_selector(selector)
            content = el.inner_text() if el else None

        if content is None:
            return {"status": "error", "error": f"Element not found: {selector}"}

        # Normalise whitespace for text-only results
        if isinstance(content, str) and fmt != "html":
            content = "\n".join(line.strip() for line in content.splitlines()).strip()

        return {"status": "ok", "content": content}
    except Exception as exc:
        logger.exception("Extract failed on %s", selector)
        return {"status": "error", "error": str(exc)}


def atomic_browser_wait(ctx: Any, params: JsonDict) -> dict:
    """Wait for an element to reach a given state.

    Parameters
    ----------
    selector : str
        CSS / XPath selector.
    timeout : int, optional
        Maximum wait time in milliseconds (default ``10_000``).
    state : str, optional
        One of ``"attached"``, ``"detached"``, ``"visible"`` (default), ``"hidden"``.

    Returns
    -------
    dict
        ``{"status": "ok"}``
    """
    selector = params.get("selector", "")
    if not selector:
        return {"status": "error", "error": "Missing required param: selector"}

    page = _ensure_browser()
    timeout = int(params.get("timeout", 10_000))
    state = params.get("state", "visible")

    valid_states = {"attached", "detached", "visible", "hidden"}
    if state not in valid_states:
        return {"status": "error", "error": f"Invalid state: {state!r}; expected one of {valid_states}"}

    try:
        page.wait_for_selector(selector, state=state, timeout=timeout)
        return {"status": "ok", "selector": selector, "state": state}
    except PlaywrightTimeout:
        return {
            "status": "error",
            "error": f"Timeout after {timeout}ms waiting for state '{state}' on {selector}",
        }
    except Exception as exc:
        logger.exception("Wait failed on %s", selector)
        return {"status": "error", "error": str(exc)}


def atomic_browser_video(ctx: Any, params: JsonDict) -> dict:
    """Open a URL and monitor video playback status.

    Parameters
    ----------
    url : str
        The page URL containing a video.
    wait_for_play : bool, optional
        Whether to wait for playback to start (default ``True``).
    max_wait : int, optional
        Maximum seconds to wait for playback (default ``30``).

    Returns
    -------
    dict
        ``{"status": "ok", "playing": true/false, "duration": ..., "current_time": ...}``
    """
    url = params.get("url", "")
    if not url:
        return {"status": "error", "error": "Missing required param: url"}

    page = _ensure_browser()
    wait_for_play = params.get("wait_for_play", True)
    max_wait = int(params.get("max_wait", 30))

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        if wait_for_play:
            deadline = time.time() + max_wait
            played = False
            while time.time() < deadline:
                try:
                    is_playing = page.evaluate(
                        """() => {
                            const v = document.querySelector('video');
                            if (!v) return false;
                            return v.currentTime > 0 && !v.paused && !v.ended;
                        }"""
                    )
                    if is_playing:
                        played = True
                        break
                except Exception:
                    pass
                page.wait_for_timeout(500)

            if not played:
                # Try clicking the video element to trigger play
                try:
                    page.evaluate(
                        """() => {
                            const v = document.querySelector('video');
                            if (v) v.play();
                        }"""
                    )
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

        # Gather video metadata
        video_info = page.evaluate(
            """() => {
                const v = document.querySelector('video');
                if (!v) return null;
                return {
                    duration: v.duration || 0,
                    current_time: v.currentTime || 0,
                    paused: v.paused,
                    ended: v.ended,
                    ready_state: v.readyState,
                    width: v.videoWidth,
                    height: v.videoHeight,
                };
            }"""
        )

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
            "url": page.url,
        }
    except Exception as exc:
        logger.exception("Video monitoring failed on %s", url)
        return {"status": "error", "error": str(exc)}


def atomic_browser_screenshot(ctx: Any, params: JsonDict) -> dict:
    """Take a screenshot of the page (or a specific element).

    Parameters
    ----------
    selector : str, optional
        If provided, screenshot only this element.
    full_page : bool, optional
        Capture the full scrollable page (default ``True`` when no selector).

    Returns
    -------
    dict
        ``{"status": "ok", "path": "<absolute-path>"}``
        The screenshot is saved to a temp file; the caller is expected to copy
        or move it as needed.
    """
    import tempfile

    page = _ensure_browser()
    selector = params.get("selector")
    full_page = params.get("full_page", True)

    try:
        suffix = ".png"
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="partner_screenshot_")
        os.close(fd)

        if selector:
            page.wait_for_selector(selector, state="visible", timeout=10_000)
            el = page.query_selector(selector)
            if el is None:
                return {"status": "error", "error": f"Element not found: {selector}"}
            el.screenshot(path=path)
            note = f"element screenshot: {selector}"
        else:
            page.screenshot(path=path, full_page=full_page)
            note = "full page" if full_page else "viewport"

        logger.info("Screenshot saved to %s (%s)", path, note)
        return {
            "status": "ok",
            "path": path,
            "note": note,
        }
    except Exception as exc:
        logger.exception("Screenshot failed")
        return {"status": "error", "error": str(exc)}


def atomic_browser_execute(ctx: Any, params: JsonDict) -> dict:
    """Execute JavaScript in the browser page.

    Parameters
    ----------
    script : str
        JavaScript code to execute (function body or expression).
    args : list, optional
        Arguments to pass to the script (if it is a function).

    Returns
    -------
    dict
        ``{"status": "ok", "result": <js-return-value>}``
    """
    script = params.get("script", "")
    if not script:
        return {"status": "error", "error": "Missing required param: script"}

    page = _ensure_browser()
    args = params.get("args")

    try:
        if args is not None:
            result = page.evaluate(script, args)
        else:
            result = page.evaluate(script)

        return {"status": "ok", "result": result}
    except Exception as exc:
        logger.exception("JS execution failed")
        return {"status": "error", "error": str(exc)}


# ── Cleanup helper (for tear-down) ────────────────────────────────────────────

def atomic_browser_close(_ctx: Any, _params: JsonDict | None = None) -> dict:
    """Close the browser and release resources."""
    _close_browser()
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
