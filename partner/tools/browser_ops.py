"""Browser automation for Partner (Sprint 7). Page fetch, extract, screenshot via Playwright."""
import os, time, logging

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


def fetch_page_content(url: str, timeout: int = 30) -> dict:
    """Fetch a page and return text content using Playwright (handles JS-rendered pages)."""
    if not HAS_PLAYWRIGHT:
        return {"ok": False, "error": "Playwright not installed"}
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            text = page.inner_text("body")
            title = page.title()
            browser.close()
        
        return {
            "ok": True, "url": url, "title": title,
            "text": text[:50000], "text_length": len(text),
        }
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)[:200]}


def screenshot_page(url: str, output_path: str = None, timeout: int = 30) -> dict:
    """Take a screenshot of a web page."""
    if not HAS_PLAYWRIGHT:
        return {"ok": False, "error": "Playwright not installed"}
    if not output_path:
        output_path = f"/tmp/partner_browser_{int(time.time())}.png"
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            page.screenshot(path=output_path, full_page=False)
            browser.close()
        
        return {"ok": True, "url": url, "path": output_path, "size": os.path.getsize(output_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def extract_links(url: str, timeout: int = 30) -> dict:
    """Extract all links from a page."""
    if not HAS_PLAYWRIGHT:
        return {"ok": False, "error": "Playwright not installed"}
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            
            links = page.evaluate("""() => {
                const links = document.querySelectorAll('a[href]');
                return Array.from(links).map(a => ({
                    text: a.innerText.trim().substring(0, 100),
                    href: a.href
                })).filter(l => l.href.startsWith('http'));
            }""")
            browser.close()
        
        return {"ok": True, "url": url, "count": len(links), "links": links[:50]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
