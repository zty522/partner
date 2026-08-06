"""Screenshot capture module for Partner's self-evolution pipeline.
Uses only real GUI capture methods (Qt Offscreen). NEVER uses synthetic/fallback rendering.
"""
from __future__ import annotations
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR_NAME = "screenshots"
_TEMP_DIR = Path(tempfile.gettempdir()) / "partner_ss"
_TEMP_DIR.mkdir(parents=True, exist_ok=True)
_XVFB_DISPLAY = ":99"
_MAX_SS_RETRIES = 3
_SS_RETRY_DELAY_S = 2.0
_MIN_REAL_SCREENSHOT_BYTES = 100 * 1024  # 100KB — any smaller is likely synthetic


def _screenshot_path(screenshots_dir: str, prefix: str, label: str = "") -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    return os.path.join(screenshots_dir, f"{prefix}{suffix}_{ts}.png")


# ── Capture methods ───────────────────────────────────────────────────────────


def _find_project_root() -> str:
    """Find the partner project root (parent of partner/, shells/, etc.)."""
    candidates = [
        "/mnt/e/work/partner",
        os.path.expanduser("~/partner"),
        os.getcwd(),
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, "partner", "evolution")):
            return c
    return str(Path(__file__).resolve().parent.parent.parent)


def capture_qt_offscreen(
    screenshots_dir: str,
    label: str = "",
    *,
    title: str = "Partner GUI",
    width: int = 1280,
    height: int = 900,
    lines: list[str] | None = None,
) -> str:
    """Render the REAL Partner GUI offscreen and capture to PNG.

    Uses QT_QPA_PLATFORM=offscreen to instantiate the actual
    ModernMainWindow, renders it, and saves as PNG.

    Returns the path to the screenshot PNG on success, or empty string
    on failure. Does NOT fall back to synthetic rendering.

    Retries up to _MAX_SS_RETRIES times with _SS_RETRY_DELAY_S delay
    between attempts. Logs detailed error information on each failure.

    The subprocess tries to import shells.frontend.desktop_gui.modern.main_window.
    If the import fails (e.g., after auto-evolution damaged the file), the
    error message will contain the specific ImportError/SyntaxError.
    """
    output_path = _screenshot_path(screenshots_dir, "partner_gui", label)

    project_root = _find_project_root()
    script = '\n'.join([
        'import sys, os',
        'os.environ["QT_QPA_PLATFORM"] = "offscreen"',
        'os.environ.pop("DISPLAY", None)',
        'sys.path.insert(0, ' + repr(project_root) + ')',
        'from PySide6.QtWidgets import QApplication',
        'app = QApplication(sys.argv)',
        'try:',
        '    from shells.frontend.desktop_gui.modern.main_window import ModernMainWindow',
        '    win = ModernMainWindow()',
        '    win.resize(' + str(width) + ', ' + str(height) + ')',
        '    win.show()',
        '    from PySide6.QtCore import QTimer, QEventLoop',
        '    loop = QEventLoop()',
        '    QTimer.singleShot(3000, loop.quit)',
        '    loop.exec()',
        '    pixmap = win.grab()',
        '    pixmap.save(' + repr(output_path) + ')',
        '    size = os.path.getsize(' + repr(output_path) + ') if os.path.exists(' + repr(output_path) + ') else 0',
        '    print("OK:" + str(pixmap.width()) + "x" + str(pixmap.height()) + ":" + str(size), file=sys.stderr)',
        'except Exception as e:',
        '    print("FAIL:" + str(e), file=sys.stderr)',
        '    sys.exit(1)',
    ])

    for attempt in range(1, _MAX_SS_RETRIES + 1):
        script_path = _TEMP_DIR / ("qt_real_" + uuid.uuid4().hex[:8] + ".py")
        try:
            script_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True, text=True, timeout=30,
            )
            if os.path.exists(output_path) and os.path.getsize(output_path) > _MIN_REAL_SCREENSHOT_BYTES:
                logger.info(
                    "[SS] ✅ Real GUI offscreen: %s (%d bytes) attempt %d/%d",
                    output_path, os.path.getsize(output_path), attempt, _MAX_SS_RETRIES,
                )
                return output_path
            err_msg = result.stderr[:300] if result.stderr else "unknown error"
            if "FAIL:" in err_msg:
                cause = err_msg.split("FAIL:")[-1].strip()[:200]
            else:
                cause = err_msg
            logger.warning(
                "[SS] ❌ Real GUI offscreen attempt %d/%d failed: %s",
                attempt, _MAX_SS_RETRIES, cause,
            )
            # Log diagnostics about the output file
            if os.path.exists(output_path):
                logger.warning("[SS]    output file exists but only %d bytes (min %d)",
                               os.path.getsize(output_path), _MIN_REAL_SCREENSHOT_BYTES)
            else:
                logger.warning("[SS]    output file was not created")
        except Exception as e:
            logger.warning(
                "[SS] ❌ Real GUI offscreen attempt %d/%d exception: %s",
                attempt, _MAX_SS_RETRIES, e,
            )
        finally:
            if script_path.exists():
                script_path.unlink()

        if attempt < _MAX_SS_RETRIES:
            time.sleep(_SS_RETRY_DELAY_S)

    # All attempts failed — return empty, no synthetic fallback
    logger.error("[SS] All %d attempts failed for real GUI capture. NO synthetic fallback.", _MAX_SS_RETRIES)
    return ""


def capture_web_background(url: str, output_path: str, *, timeout: int = 30) -> str:
    """Headless Playwright capture of a web page."""
    pw_script = "\n".join([
        'import asyncio, sys',
        'async def main():',
        '    from playwright.async_api import async_playwright',
        '    async with async_playwright() as p:',
        '        browser = await p.chromium.launch(headless=True)',
        '        page = await browser.new_page(viewport={"width": 1280, "height": 800})',
        '        try:',
        '            await page.goto("' + url + '", timeout=' + str(timeout * 1000) + ', wait_until="networkidle")',
        '        except Exception:',
        '            pass',
        '        await page.screenshot(path="' + output_path + '")',
        '        await browser.close()',
        'asyncio.run(main())',
    ])
    script_path = _TEMP_DIR / ("pw_" + uuid.uuid4().hex[:8] + ".py")
    try:
        script_path.write_text(pw_script)
        subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=timeout + 10)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
            return output_path
        if os.path.exists(output_path):
            os.unlink(output_path)
        return ""
    except Exception as e:
        logger.debug("[SS] Playwright error: %s", e)
        return ""
    finally:
        if script_path.exists():
            script_path.unlink()


def capture_window_background(window_title: str, output_path: str) -> str:
    """PrintWindow capture via PowerShell (Windows from WSL)."""
    pw = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    if not os.path.exists(pw):
        return ""
    win_out = "C:\\temp\\ss_win_" + uuid.uuid4().hex[:8] + ".png"
    ps = '\n'.join([
        'Add-Type @"',
        'using System; using System.Runtime.InteropServices; using System.Drawing; using System.Drawing.Imaging;',
        'public class W {',
        '    [DllImport("user32.dll")] public static extern IntPtr FindWindow(string c, string w);',
        '    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);',
        '    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr d, int f);',
        '    public struct RECT { public int L; public int T; public int R; public int B; }',
        '    public static string Cap(string t, string o) {',
        '        IntPtr h = FindWindow(null, t);',
        '        if (h == IntPtr.Zero) return "NF:" + t;',
        '        RECT r; GetWindowRect(h, out r);',
        '        int w = r.R - r.L, hh = r.B - r.T;',
        '        if (w <= 0 || hh <= 0) return "BAD:" + w + "x" + hh;',
        '        using (Bitmap b = new Bitmap(w, hh)) {',
        '            using (Graphics g = Graphics.FromImage(b)) {',
        '                IntPtr dc = g.GetHdc();',
        '                PrintWindow(h, dc, 0);',
        '                g.ReleaseHdc(dc);',
        '            }',
        '            b.Save(o, ImageFormat.Png);',
        '        }',
        '        return "OK:" + o;',
        '    }',
        '}',
        '"@',
        '$r = [W]::Cap("' + window_title + '", "' + win_out + '")',
        'Write-Output $r',
    ])
    ps_file = _TEMP_DIR / ("ps_" + uuid.uuid4().hex[:8] + ".ps1")
    try:
        ps_file.write_text(ps, encoding="utf-8")
        result = subprocess.run([pw, "-NoProfile", "-ExecutionPolicy", "Bypass", str(ps_file)],
                                capture_output=True, text=True, timeout=15)
        win_path = win_out.replace("\\", "/").replace("C:", "/mnt/c")
        if os.path.exists(win_path):
            shutil.move(win_path, output_path)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 500:
                logger.info("[SS] ✅ PrintWindow: %s (%d bytes)", output_path, os.path.getsize(output_path))
                return output_path
        return ""
    except Exception as e:
        logger.debug("[SS] PrintWindow error: %s", e)
        return ""
    finally:
        if ps_file.exists():
            ps_file.unlink()


# ── High-level orchestrator ───────────────────────────────────────────────────


class EvolutionScreenshot:
    """Screenshot capture — ALL methods run in background (no visible windows).
    NEVER falls back to synthetic rendering.
    """

    def __init__(self, workspace: str | None = None):
        self._workspace = workspace or os.environ.get("PARTNER_DATA_DIR", "")
        self._screenshots_dir = self._resolve_screenshot_dir()

    def capture_partner_gui(self, label: str = "", lines: list[str] | None = None) -> str:
        """Capture Partner GUI in background. Qt Offscreen -> PrintWindow.
        Returns empty string on failure — never returns a synthetic screenshot.
        """
        path = capture_qt_offscreen(self._screenshots_dir, label=label, lines=lines)
        if path:
            return path
        output_path = _screenshot_path(self._screenshots_dir, "partner_gui", label)
        path = capture_window_background("Partner", output_path)
        if path:
            return path
        # All real capture methods failed
        logger.warning("[SS] All real capture methods failed for Partner GUI — returning empty")
        return ""

    def capture_external_app(self, app_name: str, label: str = "") -> str:
        """Capture external app UI via web or window capture.
        Never falls back to synthetic rendering.
        """
        app_lower = app_name.lower()
        web_urls = {
            "hermes": ["http://localhost:3000", "http://127.0.0.1:3000"],
            "openclaw": ["http://localhost:5173", "http://127.0.0.1:5173"],
        }
        for key, urls in web_urls.items():
            if key in app_lower:
                for url in urls:
                    out = _screenshot_path(self._screenshots_dir, "web_" + key, label)
                    path = capture_web_background(url, out)
                    if path:
                        return path
        out = _screenshot_path(self._screenshots_dir, app_name.lower(), label)
        path = capture_window_background(app_name, out)
        if path:
            return path
        # No synthetic fallback — just return empty
        logger.info("[SS] No real capture method succeeded for '%s' — returning empty", app_name)
        return ""

    def ensure_screenshots_dir(self) -> str:
        os.makedirs(self._screenshots_dir, exist_ok=True)
        return self._screenshots_dir

    def get_screenshots(self) -> list[dict[str, Any]]:
        shots = []
        if os.path.isdir(self._screenshots_dir):
            for fname in sorted(os.listdir(self._screenshots_dir)):
                if fname.endswith((".png", ".jpg", ".jpeg")):
                    fpath = os.path.join(self._screenshots_dir, fname)
                    shots.append({
                        "filename": fname, "path": fpath,
                        "size_kb": round(os.path.getsize(fpath) / 1024, 1),
                        "mtime": datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat(),
                    })
        return shots

    def generate_comparison_report(
        self, hermes_screenshot: str = "", openclaw_screenshot: str = "",
        partner_before: str = "", partner_after: str = "",
        gaps: list[dict] | None = None, plans: list[dict] | None = None,
    ) -> str:
        lines = [
            "# Frontend Comparison Report",
            "",
            "Generated: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "",
            "## Reference (background capture, no visible window)",
            "",
        ]
        if hermes_screenshot and os.path.exists(hermes_screenshot):
            lines.append("### Hermes UI")
            lines.append("!(" + os.path.basename(hermes_screenshot) + ")")
            lines.append("")
        if openclaw_screenshot and os.path.exists(openclaw_screenshot):
            lines.append("### OpenClaw UI")
            lines.append("!(" + os.path.basename(openclaw_screenshot) + ")")
            lines.append("")
        lines.append("## Before vs After")
        lines.append("")
        if partner_before and os.path.exists(partner_before):
            lines.append("### Before")
            lines.append("!(" + os.path.basename(partner_before) + ")")
            lines.append("")
        if partner_after and os.path.exists(partner_after):
            lines.append("### After")
            lines.append("!(" + os.path.basename(partner_after) + ")")
            lines.append("")
        if not partner_before and not partner_after:
            lines.append("**截图状态**：截图失败，无法提供对比图")
            lines.append("")
        lines.append("## Gaps Found")
        lines.append("")
        if gaps:
            for i, g in enumerate(gaps, 1):
                # Handle both PatternComparator schema (priority/category/external_pattern)
                # and GapDiscovery schema (severity/type/description)
                pri = g.get("priority") or g.get("severity", "?")
                cat = g.get("category") or g.get("type", "")
                pat = g.get("external_pattern") or g.get("description", "")
                lines.append(f"{i}. [{pri}] {cat}: {pat[:120]}")
                status = g.get("partner_status") or g.get("detail", "")
                if status:
                    lines.append(f"   Status: {status[:100]}")
                lines.append("")
        else:
            lines.append("None identified.")
        lines.append("## Applied Improvements")
        lines.append("")
        if plans:
            for i, p in enumerate(plans, 1):
                f = p.get("target_file", p.get("target_module", "?"))
                lines.append(f"{i}. **{os.path.basename(f)}**: {p.get('description','')[:80]}")
                lines.append("")
        else:
            lines.append("None applied.")
        lines.append("---")
        lines.append("*Auto-generated by Partner Self-Evolution System*")
        report = "\n".join(lines)
        report_path = os.path.join(self._screenshots_dir, "comparison.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        return report_path

    def _resolve_screenshot_dir(self) -> str:
        if self._workspace and os.path.isdir(self._workspace):
            base = os.path.join(self._workspace, "partner_data")
        else:
            base = os.environ.get("PARTNER_DATA_DIR", os.path.join(os.getcwd(), "partner_data"))
        snap_dir = os.path.join(base, SCREENSHOTS_DIR_NAME)
        os.makedirs(snap_dir, exist_ok=True)
        return snap_dir


# ── Async helpers for SelfEvolveEngine ────────────────────────────────────────


async def capture_evolution_screenshots(
    workspace: str, stage: str = "before", progress_callback=None,
    content_lines: list[str] | None = None,
) -> dict[str, str]:
    ss = EvolutionScreenshot(workspace=workspace)
    ss.ensure_screenshots_dir()
    results: dict[str, str] = {}
    if progress_callback:
        await progress_callback(f"[SS] Background capture ({stage})...")
    results["partner_gui"] = ss.capture_partner_gui(label=stage, lines=content_lines)
    results["hermes"] = ss.capture_external_app("Hermes", label=f"hermes_{stage}")
    results["openclaw"] = ss.capture_external_app("OpenClaw", label=f"openclaw_{stage}")
    if progress_callback:
        captured = sum(1 for v in results.values() if v)
        await progress_callback(f"[SS] Done: {captured}/3 captures (0 allowed = synthetic-free)" if captured == 0 else f"[SS] Done: {captured}/3 real captures")
    return results


async def generate_evolution_report(
    workspace: str, screenshots_before: dict[str, str], screenshots_after: dict[str, str],
    gaps: list[dict] | None = None, plans: list[dict] | None = None,
    progress_callback=None,
) -> str:
    ss = EvolutionScreenshot(workspace=workspace)
    if progress_callback:
        await progress_callback("[SS] Generating comparison report...")
    return ss.generate_comparison_report(
        hermes_screenshot=screenshots_before.get("hermes", ""),
        openclaw_screenshot=screenshots_before.get("openclaw", ""),
        partner_before=screenshots_before.get("partner_gui", ""),
        partner_after=screenshots_after.get("partner_gui", ""),
        gaps=gaps, plans=plans,
    )
