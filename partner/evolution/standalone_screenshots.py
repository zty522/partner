"""Standalone screenshot capture — runs as a subprocess, outside the event loop.

Called by GUIManager when the inline async capture fails.
This isolates the Node.js/Python HTTP servers and Playwright from
Partner's event loop, preventing interference.
"""

import sys
import os
import json
import subprocess
import time
import socket
from pathlib import Path


def _get_screenshots_dir() -> str:
    """Resolve canonical screenshots directory."""
    try:
        from partner.utils.workspace import get_screenshots_dir
        return get_screenshots_dir()
    except Exception:
        d = os.path.join(
            os.environ.get("PARTNER_DATA_DIR", os.path.join(os.getcwd(), "partner_data")),
            "screenshots",
        )
        os.makedirs(d, exist_ok=True)
        return d


SCREENSHOTS_DIR = _get_screenshots_dir()
HERMES_DIR = "/mnt/e/work/partner_workspace/external_repos/hermes/out/renderer"
OPENCLAW_DIR = "/mnt/e/work/partner_workspace/external_repos/openclaw/dist"


def _find_free_port(start=5173) -> int:
    for port in range(start, start + 10):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if s.connect_ex(("127.0.0.1", port)) != 0:
            s.close()
            return port
        s.close()
    return start + 10


def _start_http_server(serve_dir: str, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=serve_dir,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=os.setpgrp,
    )


def _wait_for_port(port: int, timeout: int = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            s.close()
            return True
        s.close()
        time.sleep(0.5)
    return False


def _screenshot_playwright(url: str, output_path: str, timeout: int = 15) -> bool:
    """Capture a URL using Playwright (via subprocess to avoid event loop issues)."""
    script = rf"""
import asyncio, sys, os
async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={{"width": 1280, "height": 800}})
        try:
            await page.goto("{url}", timeout={timeout * 1000})
            await page.wait_for_load_state("networkidle", timeout=10000)
            await page.screenshot(path=r"{output_path}")
            sz = os.path.getsize(r"{output_path}") if os.path.exists(r"{output_path}") else 0
            print(f"OK:{{sz}}", file=sys.stderr)
        except Exception as e:
            print(f"FAIL:{{e}}", file=sys.stderr)
        finally:
            await browser.close()
asyncio.run(main())
"""
    script_path = Path("/tmp") / f"ss_{int(time.time())}_{os.urandom(4).hex()}.py"
    script_path.write_text(script)
    try:
        r = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=timeout + 10)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
            return True
        return False
    except Exception:
        return False
    finally:
        if script_path.exists():
            script_path.unlink()


def capture_all() -> dict:
    """Capture all 3 screenshots sequentially using standalone subprocesses."""
    results = {}
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    
    # 1. Partner GUI (Qt offscreen subprocess)
    print("[SS] Capturing Partner GUI...", flush=True)
    partner_out = os.path.join(SCREENSHOTS_DIR, f"partner_gui_{ts}.png")
    qt_script = f"""
import sys, os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.pop("DISPLAY", None)
sys.path.insert(0, "/mnt/e/work/partner")
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
try:
    from shells.frontend.desktop_gui.modern.main_window import ModernMainWindow
    win = ModernMainWindow()
    win.resize(1280, 900)
    win.show()
    from PySide6.QtCore import QTimer, QEventLoop
    loop = QEventLoop()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    pixmap = win.grab()
    pixmap.save(r"{partner_out}")
    sz = os.path.getsize(r"{partner_out}")
    print(f"OK:{{sz}}", file=sys.stderr)
except Exception as e:
    print(f"FAIL:{{e}}", file=sys.stderr)
    sys.exit(1)
"""
    script_path = Path("/tmp") / f"qt_{int(time.time())}.py"
    script_path.write_text(qt_script)
    r = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=20)
    if os.path.exists(partner_out) and os.path.getsize(partner_out) > 100 * 1024:
        results["partner_gui"] = partner_out
        print(f"  ✅ partner_gui: {partner_out} ({os.path.getsize(partner_out)//1024}KB)", flush=True)
    else:
        results["partner_gui"] = ""
        print(f"  ❌ partner_gui: failed", flush=True)
    if script_path.exists():
        script_path.unlink()

    # 2. Hermes (build → serve → Playwright)
    print("[SS] Capturing Hermes...", flush=True)
    hermes_out = os.path.join(SCREENSHOTS_DIR, f"hermes_ui_{ts}.png")
    if os.path.exists(os.path.join(HERMES_DIR, "index.html")):
        port = _find_free_port(5173)
        server = _start_http_server(HERMES_DIR, port)
        if _wait_for_port(port, 10):
            if _screenshot_playwright(f"http://127.0.0.1:{port}", hermes_out):
                results["hermes"] = hermes_out
                print(f"  ✅ hermes: {hermes_out} ({os.path.getsize(hermes_out)//1024}KB)", flush=True)
            else:
                results["hermes"] = ""
                print(f"  ❌ hermes: Playwright capture failed", flush=True)
        else:
            results["hermes"] = ""
            print(f"  ❌ hermes: HTTP server did not start", flush=True)
        server.terminate()
        try: server.wait(timeout=3)
        except: server.kill()
    else:
        results["hermes"] = ""
        print(f"  ❌ hermes: build not found at {HERMES_DIR}", flush=True)

    # 3. OpenClaw (build → serve → Playwright)
    print("[SS] Capturing OpenClaw...", flush=True)
    openclaw_out = os.path.join(SCREENSHOTS_DIR, f"openclaw_ui_{ts}.png")
    if os.path.exists(os.path.join(OPENCLAW_DIR, "index.html")):
        port = _find_free_port(5174)
        server = _start_http_server(OPENCLAW_DIR, port)
        if _wait_for_port(port, 10):
            if _screenshot_playwright(f"http://127.0.0.1:{port}", openclaw_out):
                results["openclaw"] = openclaw_out
                print(f"  ✅ openclaw: {openclaw_out} ({os.path.getsize(openclaw_out)//1024}KB)", flush=True)
            else:
                results["openclaw"] = ""
                print(f"  ❌ openclaw: Playwright capture failed", flush=True)
        else:
            results["openclaw"] = ""
            print(f"  ❌ openclaw: HTTP server did not start", flush=True)
        server.terminate()
        try: server.wait(timeout=3)
        except: server.kill()
    else:
        results["openclaw"] = ""
        print(f"  ❌ openclaw: build not found at {OPENCLAW_DIR}", flush=True)

    # Print JSON summary for the caller to parse
    print(json.dumps({"results": results}), flush=True)
    return results


if __name__ == "__main__":
    capture_all()
